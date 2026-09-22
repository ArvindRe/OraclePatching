# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial RMAN guaranteed restore point capture (creation only, never auto-restore) — Arvind Regukumar

"""Creates an RMAN guaranteed restore point before a patch apply.

IMPORTANT — what this module does and does not do:

- It only CREATEs a restore point. It never restores/flashes back to one.
  Creating a named SCN marker is a safe, additive, fully reversible action
  (drop it later if unused) — it is preparation, not rollback. Per the
  reviewed design decision, actually rolling back stays entirely manual and
  human-initiated, same as CLAUDE.md design decision #4 and
  playbooks/cpu_patch_rollback_info.yml. This module's output is handed to a
  human as one more option alongside the RMAN backup / rollback procedure
  that playbook prints — it does not replace them.

- `GUARANTEE FLASHBACK DATABASE` forces Oracle to retain the flashback logs
  needed to honor this specific restore point even if database-level
  Flashback Database is otherwise off. That means it consumes Fast Recovery
  Area space for as long as the restore point exists — this module does NOT
  estimate FRA headroom (that's `disk_space_verified`'s job in the existing
  precheck, and it doesn't currently check FRA specifically — a real gap,
  not silently assumed fine). The caller is responsible for dropping the
  restore point (DROP RESTORE POINT) once the patch is confirmed good, or
  it will sit there consuming space indefinitely.

- Uses `ansible <host> -m ansible.builtin.shell` as a one-off ad hoc command
  (not a checked-in playbook) specifically because this is new, unreviewed
  functionality for this POC — it should graduate into a real
  roles/oracle_cpu_patch task once proven, not be treated as equivalent to
  the human-reviewed playbooks it currently sits alongside.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_]")


def restore_point_name(patch_id: str) -> str:
    """Deterministic, SQL-identifier-safe restore point name.

    Oracle restore point names are limited to 32 bytes — patch_id is already
    registry/schema-constrained (it's validated against the ProcedureRegistry
    before this is ever called), but this still sanitizes defensively rather
    than trusting that upstream constraint alone.
    """
    safe_patch_id = _SAFE_NAME_RE.sub("_", patch_id)[:20]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"PREPATCH_{safe_patch_id}_{stamp}"[:32]


@dataclass(frozen=True)
class SnapshotResult:
    restore_point: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def create_guaranteed_restore_point(
    base_repo_path: Path,
    inventory: str,
    target_host: str,
    oracle_os_owner: str,
    patch_id: str,
) -> SnapshotResult:
    name = restore_point_name(patch_id)

    sql = (
        f"CREATE RESTORE POINT {name} GUARANTEE FLASHBACK DATABASE;\n"
        f"EXIT;\n"
    )
    remote_command = f"echo \"{sql}\" | sqlplus -s / as sysdba"

    cmd = [
        "ansible",
        target_host,
        "-i",
        inventory,
        "-m",
        "ansible.builtin.shell",
        "-a",
        remote_command,
        "--become",
        "--become-user",
        oracle_os_owner,
    ]

    proc = subprocess.run(
        cmd,
        cwd=str(base_repo_path),
        capture_output=True,
        text=True,
        check=False,
    )
    return SnapshotResult(
        restore_point=name,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
