# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial ansible-playbook subprocess wrapper for the Executor — Arvind Regukumar

"""Thin, no-shell wrapper around `ansible-playbook` invocations.

Every call here targets one of the three existing, human-reviewed playbooks in
the base OraclePatching repo (playbooks/cpu_patch_precheck.yml,
cpu_patch_apply.yml, cpu_patch_rollback_info.yml) — paths that only ever come
from a ProcedureRegistry entry that has already been existence-checked at
registry load time (registry/procedures/registry.py). This module never
constructs a playbook path from LLM output.

Uses subprocess with an argument list (never shell=True / string
concatenation) for the same reason the base repo's argv: task construction
avoids shell strings — see CLAUDE.md design decision #5.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlaybookResult:
    playbook: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run(base_repo_path: Path, playbook_rel_path: str, inventory: str, extra_vars: dict[str, str]) -> PlaybookResult:
    playbook_path = base_repo_path / playbook_rel_path
    cmd = ["ansible-playbook", str(playbook_path), "-i", inventory]
    for key, value in extra_vars.items():
        cmd += ["-e", f"{key}={value}"]

    proc = subprocess.run(
        cmd,
        cwd=str(base_repo_path),
        capture_output=True,
        text=True,
        check=False,
    )
    return PlaybookResult(
        playbook=playbook_rel_path,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def run_precheck(base_repo_path: Path, precheck_playbook: str, inventory: str, extra_vars: dict[str, str]) -> PlaybookResult:
    """Runs the real precheck/stage playbook — this IS the dry-run.

    Not `ansible-playbook --check`: the base repo's tasks are mostly
    command/shell modules, which simply skip under Ansible's --check mode
    rather than validating anything (see the build review that flagged this).
    The actual dry-run mechanism is stage_patch.yml's real
    opatchauto -analyze / opatch prereq conflict analysis, run for real, with
    run_full_patch left at its default false so the play structurally cannot
    reach a state-changing task (CLAUDE.md design decision #2).
    """
    vars_with_precheck_mode = {**extra_vars, "run_full_patch": "false"}
    return _run(base_repo_path, precheck_playbook, inventory, vars_with_precheck_mode)


def run_apply(base_repo_path: Path, apply_playbook: str, inventory: str, extra_vars: dict[str, str], confirm_var_name: str, patch_id: str) -> PlaybookResult:
    """Runs the real apply playbook. extra_vars must not already contain confirm_var_name."""
    if confirm_var_name in extra_vars:
        raise ValueError(f"{confirm_var_name} must be supplied via the confirm_var_name mechanism, not extra_vars")
    vars_with_confirm = {**extra_vars, confirm_var_name: patch_id}
    return _run(base_repo_path, apply_playbook, inventory, vars_with_confirm)


def run_rollback_info(base_repo_path: Path, rollback_info_playbook: str, inventory: str, extra_vars: dict[str, str]) -> PlaybookResult:
    """Runs the playbook that PRINTS (never executes) the manual rollback procedure."""
    return _run(base_repo_path, rollback_info_playbook, inventory, extra_vars)
