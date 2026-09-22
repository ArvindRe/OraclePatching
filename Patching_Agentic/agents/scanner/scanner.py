# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial scanner wrapper (runs scan_playbook.yml, parses JSON callback output) — Arvind Regukumar
#   2026-09-22T15:53:48+05:30 — Fixed env= replacing the whole subprocess environment instead of extending it (would have broken PATH/HOME); removed leftover dead loop — Arvind Regukumar

"""Runs scan_playbook.yml and returns its structured scan_result as a dict.

This is the "single source of truth every other component reads from" per
DB_PATCHING_SCOPE.md component 1 — the Patch Agent and the approval report
both consume this output rather than re-deriving OS/patch/DG state
independently. Read-only: this module never touches a state-changing task.

Uses ANSIBLE_STDOUT_CALLBACK=json so the final scan_result fact can be parsed
out of ansible-playbook's own structured output instead of scraping debug
text — the base repo's audit_log.yml deliberately avoids parsing debug
output for the same reliability reason.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

_PLAYBOOK = Path(__file__).parent / "scan_playbook.yml"


class ScanFailedError(RuntimeError):
    pass


def scan(base_repo_path: Path, inventory: str, scan_target: str, cdbs: list[dict[str, str]]) -> dict[str, Any]:
    cmd = [
        "ansible-playbook",
        str(_PLAYBOOK),
        "-i",
        inventory,
        "-e",
        f"scan_target={scan_target}",
        "-e",
        json.dumps({"cdbs": cdbs}),
    ]

    proc = subprocess.run(
        cmd,
        cwd=str(base_repo_path),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "ANSIBLE_STDOUT_CALLBACK": "json"},
    )

    if proc.returncode != 0:
        raise ScanFailedError(
            f"scan_playbook.yml exited {proc.returncode} for target '{scan_target}':\n{proc.stderr or proc.stdout}"
        )

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ScanFailedError(f"could not parse ansible-playbook JSON callback output: {exc}") from exc

    # ansible's json callback nests host results as plays[].tasks[].hosts[hostname]
    for play in parsed.get("plays", []):
        for task in play.get("tasks", []):
            hosts = task.get("hosts", {})
            host_result = hosts.get(scan_target)
            if host_result and "scan_result" in host_result:
                return host_result["scan_result"]

    raise ScanFailedError(
        f"scan_playbook.yml ran successfully but no 'scan_result' fact was found for '{scan_target}' "
        f"in its JSON output — playbook structure may have changed without updating this parser."
    )
