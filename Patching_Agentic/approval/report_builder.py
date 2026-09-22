# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial approval report builder + human confirmation gate — Arvind Regukumar

"""Builds the human-readable observability report and blocks on explicit approval.

DB_PATCHING_SCOPE.md component 6: "Presents an observability report (pre-check
results, actions, estimated downtime, risk level) and requires explicit
confirmation. No automated bypass. Approver identity and timestamp are recorded
in the audit log."

Per the reviewed design decision: this module NEVER offers or performs an
automated rollback. On a validation failure the caller (executor.py) is
responsible for stopping and handing the human the rollback procedure printed by
cpu_patch_rollback_info.yml — consistent with CLAUDE.md design decision #4.

Confirmation reuses the existing safety pattern from the base repo (CLAUDE.md
design decision #3): the approver must type the exact target_version, not a
generic "yes" — so approving the wrong plan for the wrong host requires actively
typing the wrong thing.
"""

from __future__ import annotations

import getpass
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from registry.procedures.schema import KNOWN_PRECONDITIONS, ProcedureDefinition


@dataclass(frozen=True)
class ApprovalReport:
    target: str
    procedure_id: str
    current_version: str
    target_version: str
    preconditions_checked: dict[str, bool]
    missing_preconditions: list[str]
    warnings: list[str]
    risk_level: str
    dry_run_summary: str
    risk_notes: Optional[str] = None


def _risk_level(missing_preconditions: list[str], warnings: list[str]) -> str:
    if missing_preconditions:
        return "HIGH — missing required preconditions, review before approving"
    if warnings:
        return "MEDIUM — proceeds with caveats, see warnings"
    return "STANDARD — all required preconditions satisfied"


def build_report(
    plan: dict[str, Any],
    procedure: ProcedureDefinition,
    dry_run_summary: str,
) -> ApprovalReport:
    checked = plan.get("preconditions_checked", {})
    missing = [
        name
        for name in procedure.required_preconditions
        if not checked.get(name, False)
    ]

    warnings: list[str] = []
    if procedure.requires_grid_infra:
        warnings.append(
            "This procedure targets a RAC/Grid-Infrastructure path that is "
            "UNVERIFIED against a real cluster (see registry notes and "
            "STATUS.md Open Issues). Treat as first-of-its-kind, not routine."
        )
    unknown_keys = set(checked) - KNOWN_PRECONDITIONS
    if unknown_keys:
        warnings.append(
            f"Patch Agent asserted unrecognized precondition key(s) {sorted(unknown_keys)} "
            f"— ignored, not evaluated."
        )

    return ApprovalReport(
        target=plan["target"],
        procedure_id=procedure.procedure_id,
        current_version=plan["current_version"],
        target_version=plan["target_version"],
        preconditions_checked=checked,
        missing_preconditions=missing,
        warnings=warnings,
        risk_level=_risk_level(missing, warnings),
        dry_run_summary=dry_run_summary,
        risk_notes=plan.get("risk_notes"),
    )


def render_text(report: ApprovalReport) -> str:
    lines = [
        "=" * 72,
        "ORACLE PATCH — APPROVAL REQUIRED",
        "=" * 72,
        f"Target:            {report.target}",
        f"Procedure:         {report.procedure_id}",
        f"Version:           {report.current_version} -> {report.target_version}",
        f"Risk level:        {report.risk_level}",
        "",
        "Estimated downtime: not modeled in this POC — no execution-history data "
        "exists yet (see DB_PATCHING_SCOPE.md 'Out of Scope: Learning-loop / "
        "execution-history recommendations'). Do not treat silence here as zero.",
        "",
        "Preconditions:",
    ]
    for name in sorted(report.preconditions_checked):
        status = "OK" if report.preconditions_checked[name] else "NOT CONFIRMED"
        lines.append(f"  [{status:14}] {name}")
    if report.missing_preconditions:
        lines.append(f"  MISSING: {', '.join(report.missing_preconditions)}")

    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in report.warnings:
            lines.append(f"  - {w}")

    if report.risk_notes:
        lines.append("")
        lines.append(f"Agent risk notes: {report.risk_notes}")

    lines.append("")
    lines.append("Dry-run (conflict analysis) summary:")
    lines.append(f"  {report.dry_run_summary}")
    lines.append("=" * 72)
    return "\n".join(lines)


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    approver: str
    timestamp: str
    typed_confirmation: str


def request_confirmation(report: ApprovalReport) -> ApprovalDecision:
    """Blocks on an interactive, exact-match confirmation. No automated bypass.

    The caller is responsible for logging this decision to the audit trail
    (audit/logger.py, event_type="approval_decision") — this function only
    collects it.
    """
    print(render_text(report))
    print()
    if report.missing_preconditions:
        print(
            "One or more required preconditions are NOT confirmed. Approving "
            "anyway is a deliberate override, not a default."
        )
    typed = input(
        f"Type the target version exactly ('{report.target_version}') to approve, "
        f"or anything else to reject: "
    ).strip()

    return ApprovalDecision(
        approved=(typed == report.target_version),
        approver=getpass.getuser(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        typed_confirmation=typed,
    )
