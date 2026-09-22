# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial Executor orchestration tests (all branches, no live ansible/sqlplus needed) — Arvind Regukumar
#   2026-09-22T16:49:41+05:30 — Renamed BASE_REPO_PATH to ANSIBLE_DIR and RunContext's base_repo_path field to ansible_dir, now pointing at the new ansible/ subdirectory — Arvind Regukumar
#   2026-09-22T20:11:44+05:30 — Added test for a real crash found live (malformed target_version "19c" from the LLM crashed the whole process) — Arvind Regukumar

"""Exercises executor.run_patch_workflow's branches with ansible_runner/snapshot/
approval calls monkeypatched out — the orchestration logic (registry gate,
version-range gate, dry-run gate, snapshot gate, approval gate, apply outcome,
manual-only rollback surfacing) is what's under test here, not real Ansible or
SQL*Plus execution, which none of this sandbox can reach anyway.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from approval.report_builder import ApprovalDecision
from audit.logger import AuditLogger
from executor import executor as executor_module
from executor.ansible_runner import PlaybookResult
from executor.executor import ExecutorHaltedError, RunContext, run_patch_workflow
from executor.rollback.snapshot import SnapshotResult
from registry.procedures.registry import ProcedureRegistry

SCAFFOLD_ROOT = Path(__file__).parent.parent
ANSIBLE_DIR = (SCAFFOLD_ROOT / ".." / "ansible").resolve()
PROCEDURES_DIR = SCAFFOLD_ROOT / "registry" / "procedures"


@pytest.fixture
def registry():
    return ProcedureRegistry.load(PROCEDURES_DIR, ANSIBLE_DIR)


@pytest.fixture
def ctx():
    return RunContext(
        ansible_dir=ANSIBLE_DIR,
        inventory="inventories/vagrant_test/hosts.yml",
        target_host="dbhost01",
        oracle_os_owner="oracle",
    )


@pytest.fixture
def audit(tmp_path):
    return AuditLogger(tmp_path / "audit.jsonl")


def _valid_plan(procedure_id="oracle_19c_ru_patch", target_version="19.28.0.0.0"):
    return {
        "procedure_id": procedure_id,
        "target": "dbhost01",
        "current_version": "19.27.0.0.0",
        "target_version": target_version,
        "preconditions_checked": {
            "rman_backup_available": True,
            "archivelog_backup_available": True,
            "oracle_home_verified": True,
            "opatch_version_verified": True,
            "disk_space_verified": True,
            "data_guard_synchronized": True,
        },
    }


def test_unknown_procedure_id_halts_before_any_ansible_call(registry, ctx, audit, monkeypatch):
    called = []
    monkeypatch.setattr(executor_module.ansible_runner, "run_precheck", lambda *a, **k: called.append("precheck"))

    with pytest.raises(ExecutorHaltedError, match="not an allow-listed"):
        run_patch_workflow(_valid_plan(procedure_id="made_up"), ctx, registry, audit)

    assert called == []
    entries = audit.read_all()
    assert entries[-1].event_type == "validation_result"
    assert entries[-1].payload["ok"] is False


def test_version_out_of_range_halts_before_dry_run(registry, ctx, audit, monkeypatch):
    called = []
    monkeypatch.setattr(executor_module.ansible_runner, "run_precheck", lambda *a, **k: called.append("precheck"))

    with pytest.raises(ExecutorHaltedError, match="outside procedure"):
        run_patch_workflow(_valid_plan(target_version="20.1.0.0.0"), ctx, registry, audit)

    assert called == []


def test_malformed_version_halts_cleanly_instead_of_crashing(registry, ctx, audit, monkeypatch):
    """Real incident: the LLM returned target_version="19c" and _version_in_range's
    bare int() call crashed the whole process with an unhandled ValueError — no
    audit entry, no clean halt. This must fail the same way an out-of-range
    version does, not escape as a raw traceback."""
    called = []
    monkeypatch.setattr(executor_module.ansible_runner, "run_precheck", lambda *a, **k: called.append("precheck"))

    with pytest.raises(ExecutorHaltedError, match="could not be parsed"):
        run_patch_workflow(_valid_plan(target_version="19c"), ctx, registry, audit)

    assert called == []
    entries = audit.read_all()
    assert entries[-1].event_type == "validation_result"
    assert entries[-1].payload["ok"] is False
    assert entries[-1].payload["stage"] == "version_range"


def test_dry_run_failure_halts_before_snapshot(registry, ctx, audit, monkeypatch):
    monkeypatch.setattr(
        executor_module.ansible_runner,
        "run_precheck",
        lambda *a, **k: PlaybookResult("playbooks/cpu_patch_precheck.yml", 1, "", "boom"),
    )
    snapshot_called = []
    monkeypatch.setattr(
        executor_module, "create_guaranteed_restore_point", lambda *a, **k: snapshot_called.append(1)
    )

    with pytest.raises(ExecutorHaltedError, match="Precheck/dry-run failed"):
        run_patch_workflow(_valid_plan(), ctx, registry, audit)

    assert snapshot_called == []


def test_snapshot_failure_halts_before_approval(registry, ctx, audit, monkeypatch):
    monkeypatch.setattr(
        executor_module.ansible_runner,
        "run_precheck",
        lambda *a, **k: PlaybookResult("playbooks/cpu_patch_precheck.yml", 0, "ok", ""),
    )
    monkeypatch.setattr(
        executor_module,
        "create_guaranteed_restore_point",
        lambda *a, **k: SnapshotResult("PREPATCH_X", 1, "", "ORA-12345"),
    )
    approval_called = []
    monkeypatch.setattr(executor_module, "request_confirmation", lambda *a, **k: approval_called.append(1))

    with pytest.raises(ExecutorHaltedError, match="Could not create pre-patch restore point"):
        run_patch_workflow(_valid_plan(), ctx, registry, audit)

    assert approval_called == []


def test_rejected_approval_halts_before_apply(registry, ctx, audit, monkeypatch):
    monkeypatch.setattr(
        executor_module.ansible_runner,
        "run_precheck",
        lambda *a, **k: PlaybookResult("playbooks/cpu_patch_precheck.yml", 0, "ok", ""),
    )
    monkeypatch.setattr(
        executor_module,
        "create_guaranteed_restore_point",
        lambda *a, **k: SnapshotResult("PREPATCH_X", 0, "ok", ""),
    )
    monkeypatch.setattr(
        executor_module,
        "request_confirmation",
        lambda report: ApprovalDecision(approved=False, approver="tester", timestamp="t", typed_confirmation="nope"),
    )
    apply_called = []
    monkeypatch.setattr(executor_module.ansible_runner, "run_apply", lambda *a, **k: apply_called.append(1))

    with pytest.raises(ExecutorHaltedError, match="did not confirm"):
        run_patch_workflow(_valid_plan(), ctx, registry, audit)

    assert apply_called == []


def test_apply_failure_surfaces_rollback_info_and_never_auto_rolls_back(registry, ctx, audit, monkeypatch, capsys):
    monkeypatch.setattr(
        executor_module.ansible_runner,
        "run_precheck",
        lambda *a, **k: PlaybookResult("playbooks/cpu_patch_precheck.yml", 0, "ok", ""),
    )
    monkeypatch.setattr(
        executor_module,
        "create_guaranteed_restore_point",
        lambda *a, **k: SnapshotResult("PREPATCH_X", 0, "ok", ""),
    )
    monkeypatch.setattr(
        executor_module,
        "request_confirmation",
        lambda report: ApprovalDecision(approved=True, approver="tester", timestamp="t", typed_confirmation=report.target_version),
    )
    monkeypatch.setattr(
        executor_module.ansible_runner,
        "run_apply",
        lambda *a, **k: PlaybookResult("playbooks/cpu_patch_apply.yml", 1, "", "opatchauto failed"),
    )
    rollback_info_called = []
    monkeypatch.setattr(
        executor_module.ansible_runner,
        "run_rollback_info",
        lambda *a, **k: rollback_info_called.append(1) or PlaybookResult("playbooks/cpu_patch_rollback_info.yml", 0, "MANUAL STEPS: ...", ""),
    )

    with pytest.raises(ExecutorHaltedError, match="a human decides whether and when to run it"):
        run_patch_workflow(_valid_plan(), ctx, registry, audit)

    assert rollback_info_called == [1]
    captured = capsys.readouterr()
    assert "MANUAL STEPS" in captured.out

    event_types = [e.event_type for e in audit.read_all()]
    assert "snapshot" in event_types
    assert event_types[-1] == "validation_result"
    assert audit.read_all()[-1].payload["ok"] is False


def test_full_success_path_writes_final_validation_result(registry, ctx, audit, monkeypatch):
    monkeypatch.setattr(
        executor_module.ansible_runner,
        "run_precheck",
        lambda *a, **k: PlaybookResult("playbooks/cpu_patch_precheck.yml", 0, "ok", ""),
    )
    monkeypatch.setattr(
        executor_module,
        "create_guaranteed_restore_point",
        lambda *a, **k: SnapshotResult("PREPATCH_X", 0, "ok", ""),
    )
    monkeypatch.setattr(
        executor_module,
        "request_confirmation",
        lambda report: ApprovalDecision(approved=True, approver="tester", timestamp="t", typed_confirmation=report.target_version),
    )
    monkeypatch.setattr(
        executor_module.ansible_runner,
        "run_apply",
        lambda *a, **k: PlaybookResult("playbooks/cpu_patch_apply.yml", 0, "PLAY RECAP ok", ""),
    )

    run_patch_workflow(_valid_plan(), ctx, registry, audit)

    ok, reason = audit.verify_chain()
    assert ok, reason
    last = audit.read_all()[-1]
    assert last.event_type == "validation_result"
    assert last.payload["ok"] is True
