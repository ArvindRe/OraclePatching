# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial registry allow-list tests (load, dangling playbook, unknown procedure) — Arvind Regukumar
#   2026-09-22T16:49:41+05:30 — Renamed BASE_REPO_PATH to ANSIBLE_DIR, now pointing at the new ansible/ subdirectory — Arvind Regukumar

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from registry.procedures.registry import ProcedureRegistry, UnknownProcedureError

SCAFFOLD_ROOT = Path(__file__).parent.parent
ANSIBLE_DIR = (SCAFFOLD_ROOT / ".." / "ansible").resolve()
PROCEDURES_DIR = SCAFFOLD_ROOT / "registry" / "procedures"


def test_loads_real_registry_against_real_playbooks():
    registry = ProcedureRegistry.load(PROCEDURES_DIR, ANSIBLE_DIR)
    assert "oracle_19c_ru_patch" in registry
    assert "oracle_19c_rac_ru_patch" in registry
    assert len(registry) == 2


def test_unknown_procedure_id_raises():
    registry = ProcedureRegistry.load(PROCEDURES_DIR, ANSIBLE_DIR)
    with pytest.raises(UnknownProcedureError):
        registry.get("procedure_the_llm_made_up")


def test_dangling_playbook_reference_fails_to_load(tmp_path):
    bad_dir = tmp_path / "procedures"
    bad_dir.mkdir()
    (bad_dir / "bogus_procedure.yml").write_text(
        yaml.safe_dump(
            {
                "procedure_id": "bogus_procedure",
                "description": "points at a playbook that doesn't exist",
                "target_type": "single_instance",
                "min_supported_version": "19.0.0.0.0",
                "max_supported_version": "19.99.99.99.99",
                "precheck_playbook": "playbooks/does_not_exist.yml",
                "apply_playbook": "playbooks/cpu_patch_apply.yml",
                "rollback_info_playbook": "playbooks/cpu_patch_rollback_info.yml",
                "required_preconditions": [],
            }
        )
    )
    with pytest.raises(FileNotFoundError):
        ProcedureRegistry.load(bad_dir, ANSIBLE_DIR)


def test_unknown_precondition_key_rejected(tmp_path):
    bad_dir = tmp_path / "procedures"
    bad_dir.mkdir()
    (bad_dir / "bad_precondition.yml").write_text(
        yaml.safe_dump(
            {
                "procedure_id": "bad_precondition",
                "description": "asserts a precondition key that isn't recognized",
                "target_type": "single_instance",
                "min_supported_version": "19.0.0.0.0",
                "max_supported_version": "19.99.99.99.99",
                "precheck_playbook": "playbooks/cpu_patch_precheck.yml",
                "apply_playbook": "playbooks/cpu_patch_apply.yml",
                "rollback_info_playbook": "playbooks/cpu_patch_rollback_info.yml",
                "required_preconditions": ["made_up_precondition"],
            }
        )
    )
    with pytest.raises(Exception):  # pydantic.ValidationError
        ProcedureRegistry.load(bad_dir, ANSIBLE_DIR)


def test_procedure_id_must_match_filename(tmp_path):
    bad_dir = tmp_path / "procedures"
    bad_dir.mkdir()
    (bad_dir / "one_name.yml").write_text(
        yaml.safe_dump(
            {
                "procedure_id": "a_different_name",
                "description": "filename/procedure_id mismatch",
                "target_type": "single_instance",
                "min_supported_version": "19.0.0.0.0",
                "max_supported_version": "19.99.99.99.99",
                "precheck_playbook": "playbooks/cpu_patch_precheck.yml",
                "apply_playbook": "playbooks/cpu_patch_apply.yml",
                "rollback_info_playbook": "playbooks/cpu_patch_rollback_info.yml",
                "required_preconditions": [],
            }
        )
    )
    with pytest.raises(ValueError):
        ProcedureRegistry.load(bad_dir, ANSIBLE_DIR)
