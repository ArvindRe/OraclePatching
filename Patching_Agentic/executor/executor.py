# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial Executor orchestration (registry -> dry-run -> snapshot -> approval -> apply -> validate), manual-only rollback — Arvind Regukumar

"""Orchestrates one patch run end to end.

This is the component that enforces DB_PATCHING_SCOPE.md's core design
principle: "The LLM never has execution authority." A PatchPlan (from
agents/patch/patch_agent.py, or supplied directly for testing) is only ever
trusted for its procedure_id, which must resolve against ProcedureRegistry —
every real action taken from there on is a lookup into that registry, never
anything derived from free-form LLM output.

Sequence: recommendation -> dry-run (real precheck/conflict-analysis) ->
guaranteed restore point -> human approval gate -> apply -> postcheck /
validation. On ANY failure, this stops and surfaces the rollback procedure —
it does not attempt to fix or restore anything itself. See
executor/rollback/snapshot.py and approval/report_builder.py docstrings for
why automated rollback was deliberately rejected (CLAUDE.md design decision #4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from approval.report_builder import ApprovalDecision, build_report, request_confirmation
from audit.logger import AuditLogger
from executor import ansible_runner
from executor.rollback.snapshot import create_guaranteed_restore_point
from registry.procedures.registry import ProcedureRegistry, UnknownProcedureError
from registry.procedures.schema import ProcedureDefinition


class ExecutorHaltedError(RuntimeError):
    """Raised whenever the workflow stops short of a completed, validated apply.

    Every raise site here has already written its own audit entry before
    raising — callers should treat this as "already logged, now surface to
    the human," not as an unexpected crash.
    """


@dataclass(frozen=True)
class RunContext:
    base_repo_path: Path
    inventory: str
    target_host: str
    oracle_os_owner: str


def _version_in_range(version: str, min_v: str, max_v: str) -> bool:
    def as_tuple(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in v.split("."))

    return as_tuple(min_v) <= as_tuple(version) <= as_tuple(max_v)


def run_patch_workflow(
    plan: dict[str, Any],
    ctx: RunContext,
    registry: ProcedureRegistry,
    audit: AuditLogger,
) -> None:
    audit.append("recommendation", plan)

    try:
        procedure: ProcedureDefinition = registry.get(plan["procedure_id"])
    except UnknownProcedureError as exc:
        audit.append("validation_result", {"stage": "registry_lookup", "ok": False, "reason": str(exc)})
        raise ExecutorHaltedError(str(exc)) from exc

    if not _version_in_range(plan["target_version"], procedure.min_supported_version, procedure.max_supported_version):
        reason = (
            f"target_version {plan['target_version']} outside procedure "
            f"{procedure.procedure_id}'s supported range "
            f"[{procedure.min_supported_version}, {procedure.max_supported_version}]"
        )
        audit.append("validation_result", {"stage": "version_range", "ok": False, "reason": reason})
        raise ExecutorHaltedError(reason)

    # --- Dry-run: the real precheck/stage playbook, real conflict analysis ---
    dry_run = ansible_runner.run_precheck(
        ctx.base_repo_path, procedure.precheck_playbook, ctx.inventory, extra_vars={}
    )
    audit.append(
        "dry_run_result",
        {"playbook": dry_run.playbook, "ok": dry_run.ok, "returncode": dry_run.returncode, "stdout_tail": dry_run.stdout[-4000:]},
    )
    if not dry_run.ok:
        raise ExecutorHaltedError(
            f"Precheck/dry-run failed (rc={dry_run.returncode}); see audit log for output. Not proceeding to apply."
        )

    # --- Guaranteed restore point: creation only, never used to auto-restore ---
    snapshot = create_guaranteed_restore_point(
        ctx.base_repo_path, ctx.inventory, ctx.target_host, ctx.oracle_os_owner, plan["procedure_id"]
    )
    audit.append(
        "snapshot",
        {
            "restore_point": snapshot.restore_point,
            "ok": snapshot.ok,
            "returncode": snapshot.returncode,
            "stdout_tail": snapshot.stdout[-2000:],
            "stderr_tail": snapshot.stderr[-2000:],
        },
    )
    if not snapshot.ok:
        raise ExecutorHaltedError(
            f"Could not create pre-patch restore point (rc={snapshot.returncode}); see audit log. Not proceeding to apply."
        )

    # --- Human approval gate ---
    dry_run_summary = f"{procedure.precheck_playbook} exited 0; restore point {snapshot.restore_point} created."
    report = build_report(plan, procedure, dry_run_summary)
    decision: ApprovalDecision = request_confirmation(report)
    audit.append(
        "approval_decision",
        {
            "approved": decision.approved,
            "approver": decision.approver,
            "timestamp": decision.timestamp,
            "typed_confirmation": decision.typed_confirmation,
        },
    )
    if not decision.approved:
        raise ExecutorHaltedError("Human approver did not confirm — stopping before apply. No changes were made.")

    # --- Apply ---
    apply_result = ansible_runner.run_apply(
        ctx.base_repo_path,
        procedure.apply_playbook,
        ctx.inventory,
        extra_vars={},
        confirm_var_name=procedure.confirm_var_name,
        patch_id=plan["procedure_id"],
    )
    audit.append(
        "execution_output",
        {"playbook": apply_result.playbook, "ok": apply_result.ok, "returncode": apply_result.returncode, "stdout_tail": apply_result.stdout[-8000:]},
    )

    if not apply_result.ok:
        rollback_info = ansible_runner.run_rollback_info(
            ctx.base_repo_path, procedure.rollback_info_playbook, ctx.inventory, extra_vars={}
        )
        audit.append(
            "validation_result",
            {
                "stage": "apply",
                "ok": False,
                "reason": f"apply playbook exited {apply_result.returncode}",
                "rollback_procedure_printed": rollback_info.ok,
            },
        )
        # Per CLAUDE.md design decision #4: never automated. Surface, don't act.
        print(rollback_info.stdout)
        raise ExecutorHaltedError(
            f"Apply failed (rc={apply_result.returncode}). Restore point '{snapshot.restore_point}' "
            f"is available. Rollback procedure printed above — a human decides whether and when to run it."
        )

    # apply_playbook's own postcheck.yml already ran as part of the sequence; its
    # result is embedded in apply_result.stdout. This records the overall outcome.
    audit.append("validation_result", {"stage": "apply", "ok": True, "restore_point": snapshot.restore_point})
    print(
        f"Patch applied successfully. Restore point '{snapshot.restore_point}' is still present — "
        f"drop it once you're satisfied the patch is stable (it consumes Fast Recovery Area space)."
    )
