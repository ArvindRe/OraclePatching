# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial hash-chained append-only audit logger — Arvind Regukumar
#   2026-09-22T15:53:48+05:30 — Added "snapshot" event type for executor/rollback/snapshot.py — Arvind Regukumar
#   2026-09-22T16:49:41+05:30 — Updated roles/oracle_cpu_patch prose reference to ansible/roles/oracle_cpu_patch (Ansible content moved under a new ansible/ subdirectory) — Arvind Regukumar

"""Append-only, hash-chained audit log for the agentic patching POC.

DB_PATCHING_SCOPE.md component 7 asks for "append-only / hash-chained," which is a
step beyond the base OraclePatching repo's existing local JSON-lines audit log
(ansible/roles/oracle_cpu_patch/tasks/audit_log.yml — see CLAUDE.md design decision #7).
This logger is deliberately a *separate* file (settings.audit_log_path, default
~/.oracle_patching/agentic_audit.jsonl) rather than a rewrite of that one — the
Ansible-native log covers every playbook invocation regardless of how it was
triggered (agentic or a human running ansible-playbook directly); this one covers
the agentic pipeline's own steps (scan, retrieval, recommendation, approval,
dry-run, execution, validation), one entry per step, chained together so a step
can't be silently deleted or reordered after the fact without breaking the chain.

Every event a caller logs is untrusted content only in the sense that it's
data, not instructions — this module makes no execution decisions, it only
records what already happened elsewhere.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

GENESIS_HASH = "0" * 64

# The event types the agentic pipeline is expected to log, per
# DB_PATCHING_SCOPE.md component 7. Not enforced as a hard allow-list (new event
# types are cheap to add) but kept here so callers don't invent ad hoc strings.
EVENT_TYPES = (
    "scan",
    "rag_retrieval",
    "recommendation",
    "approval_decision",
    "dry_run_result",
    "snapshot",
    "execution_output",
    "validation_result",
)


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    timestamp: str
    event_type: str
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str

    @staticmethod
    def compute_hash(seq: int, timestamp: str, event_type: str, payload: dict[str, Any], prev_hash: str) -> str:
        canonical = json.dumps(
            {"seq": seq, "timestamp": timestamp, "event_type": event_type, "payload": payload, "prev_hash": prev_hash},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "timestamp": self.timestamp,
                "event_type": self.event_type,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
                "entry_hash": self.entry_hash,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json_line(cls, line: str) -> "AuditEntry":
        raw = json.loads(line)
        return cls(
            seq=raw["seq"],
            timestamp=raw["timestamp"],
            event_type=raw["event_type"],
            payload=raw["payload"],
            prev_hash=raw["prev_hash"],
            entry_hash=raw["entry_hash"],
        )


class ChainIntegrityError(RuntimeError):
    """Raised by verify_chain() findings — never raised silently, callers must check."""


class AuditLogger:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.touch(exist_ok=True)

    def append(self, event_type: str, payload: dict[str, Any]) -> AuditEntry:
        """Appends one entry under an exclusive file lock, chained to the last entry."""
        with open(self.log_path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                lines = [line for line in fh.read().splitlines() if line.strip()]
                if lines:
                    prev = AuditEntry.from_json_line(lines[-1])
                    seq = prev.seq + 1
                    prev_hash = prev.entry_hash
                else:
                    seq = 1
                    prev_hash = GENESIS_HASH

                timestamp = datetime.now(timezone.utc).isoformat()
                entry_hash = AuditEntry.compute_hash(seq, timestamp, event_type, payload, prev_hash)
                entry = AuditEntry(seq, timestamp, event_type, payload, prev_hash, entry_hash)

                fh.write(entry.to_json_line() + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                return entry
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def read_all(self) -> list[AuditEntry]:
        with open(self.log_path, "r", encoding="utf-8") as fh:
            return [AuditEntry.from_json_line(line) for line in fh if line.strip()]

    def verify_chain(self) -> tuple[bool, Optional[str]]:
        """Recomputes every hash and checks linkage. Returns (ok, reason_if_not)."""
        entries = self.read_all()
        prev_hash = GENESIS_HASH
        expected_seq = 1

        for entry in entries:
            if entry.seq != expected_seq:
                return False, f"entry at position {expected_seq} has seq={entry.seq} (gap or reorder)"
            if entry.prev_hash != prev_hash:
                return False, f"entry seq={entry.seq} prev_hash mismatch (chain broken or tampered)"
            recomputed = AuditEntry.compute_hash(entry.seq, entry.timestamp, entry.event_type, entry.payload, entry.prev_hash)
            if recomputed != entry.entry_hash:
                return False, f"entry seq={entry.seq} entry_hash does not match its own content (tampered)"
            prev_hash = entry.entry_hash
            expected_seq += 1

        return True, None
