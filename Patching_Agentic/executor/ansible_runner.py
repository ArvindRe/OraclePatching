# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial ansible-playbook subprocess wrapper for the Executor — Arvind Regukumar
#   2026-09-22T16:49:41+05:30 — Renamed base_repo_path to ansible_dir and set it as the subprocess cwd — playbooks/roles/inventories/ansible.cfg moved under ansible/, and ansible.cfg is only auto-discovered (for roles_path etc.) when cwd is the directory containing it — Arvind Regukumar
#   2026-09-22T17:30:00+05:30 — Added a real subprocess timeout — none of these calls had one, so a hung SSH session (VM stopped mid-run, network blip) would block the whole pipeline indefinitely with no visibility. A timeout now surfaces as an ordinary failed PlaybookResult, same as any other non-zero exit — Arvind Regukumar

"""Thin, no-shell wrapper around `ansible-playbook` invocations.

Every call here targets one of the three existing, human-reviewed playbooks in
the base OraclePatching repo (ansible/playbooks/cpu_patch_precheck.yml,
cpu_patch_apply.yml, cpu_patch_rollback_info.yml) — paths that only ever come
from a ProcedureRegistry entry that has already been existence-checked at
registry load time (registry/procedures/registry.py). This module never
constructs a playbook path from LLM output.

cwd is always ansible_dir (the ansible/ subdirectory), not the repo root —
that's what makes ansible.cfg auto-discovery (roles_path, inventory default)
work; running from the repo root would silently miss it since Ansible only
auto-discovers ./ansible.cfg relative to the current working directory.

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


def _run(ansible_dir: Path, playbook_rel_path: str, inventory: str, extra_vars: dict[str, str], timeout_seconds: float) -> PlaybookResult:
    playbook_path = ansible_dir / playbook_rel_path
    cmd = ["ansible-playbook", str(playbook_path), "-i", inventory]
    for key, value in extra_vars.items():
        cmd += ["-e", f"{key}={value}"]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ansible_dir),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        # Surfaced as an ordinary failed result, not a raised exception — the
        # Executor already knows how to handle "dry-run/apply didn't succeed";
        # it doesn't need a second, timeout-specific code path.
        return PlaybookResult(
            playbook=playbook_rel_path,
            returncode=-1,
            stdout=(exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=f"ansible-playbook timed out after {timeout_seconds}s (killed). "
            f"Check whether the target host is reachable (SSH hang, VM stopped mid-run, network blip).",
        )
    return PlaybookResult(
        playbook=playbook_rel_path,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def run_precheck(ansible_dir: Path, precheck_playbook: str, inventory: str, extra_vars: dict[str, str], timeout_seconds: float) -> PlaybookResult:
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
    return _run(ansible_dir, precheck_playbook, inventory, vars_with_precheck_mode, timeout_seconds)


def run_apply(ansible_dir: Path, apply_playbook: str, inventory: str, extra_vars: dict[str, str], confirm_var_name: str, patch_id: str, timeout_seconds: float) -> PlaybookResult:
    """Runs the real apply playbook. extra_vars must not already contain confirm_var_name."""
    if confirm_var_name in extra_vars:
        raise ValueError(f"{confirm_var_name} must be supplied via the confirm_var_name mechanism, not extra_vars")
    vars_with_confirm = {**extra_vars, confirm_var_name: patch_id}
    return _run(ansible_dir, apply_playbook, inventory, vars_with_confirm, timeout_seconds)


def run_rollback_info(ansible_dir: Path, rollback_info_playbook: str, inventory: str, extra_vars: dict[str, str], timeout_seconds: float) -> PlaybookResult:
    """Runs the playbook that PRINTS (never executes) the manual rollback procedure."""
    return _run(ansible_dir, rollback_info_playbook, inventory, extra_vars, timeout_seconds)
