# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial procedure registry schema — Arvind Regukumar

"""Schema for a single allow-listed procedure registry entry.

This is the hard boundary described in DB_PATCHING_SCOPE.md component 4: the Patch
Agent (LLM-backed) can only ever reference a procedure_id. Every field that turns
into an actual executable action here is a path into the existing, human-reviewed
OraclePatching playbooks — never inline shell/SQL, never an LLM-supplied path.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

TargetType = Literal["single_instance", "rac"]

# The only preconditions the Patch Agent is allowed to assert. Matches the
# `preconditions_checked` keys in DB_PATCHING_SCOPE.md's example plan object.
KNOWN_PRECONDITIONS = frozenset(
    {
        "rman_backup_available",
        "archivelog_backup_available",
        "oracle_home_verified",
        "opatch_version_verified",
        "disk_space_verified",
        "data_guard_synchronized",
    }
)


class ProcedureDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    procedure_id: str
    description: str
    target_type: TargetType

    # Inclusive min/max, e.g. "19.0.0.0.0" / "19.99.99.99.99" — compared as Oracle
    # version tuples by registry.py, not string comparison.
    min_supported_version: str
    max_supported_version: str

    # Paths relative to base_repo_path (the OraclePatching repo root), e.g.
    # "playbooks/cpu_patch_precheck.yml". Existence is verified at load time by
    # registry.py — a procedure entry pointing at a playbook that doesn't exist
    # fails registry load, it does not become a runnable-but-broken procedure.
    precheck_playbook: str
    apply_playbook: str
    rollback_info_playbook: str

    # Name of the Ansible extra-var the apply playbook gates on (see CLAUDE.md
    # design decision #3 — must equal procedure's patch_id exactly, not a generic yes).
    confirm_var_name: str = "confirm_patch"

    required_preconditions: list[str]

    # RAC/GI paths are unverified against a real cluster as of STATUS.md's Open
    # Issues — surfaced so the approval report can warn instead of silently
    # treating an unverified path as equivalent to the validated single-instance one.
    requires_grid_infra: bool = False

    notes: Optional[str] = None

    @field_validator("required_preconditions")
    @classmethod
    def _preconditions_are_known(cls, value: list[str]) -> list[str]:
        unknown = set(value) - KNOWN_PRECONDITIONS
        if unknown:
            raise ValueError(
                f"unknown precondition(s) {sorted(unknown)}; must be a subset of "
                f"{sorted(KNOWN_PRECONDITIONS)}"
            )
        return value
