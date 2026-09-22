# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial audit logger tests (chain integrity, tamper detection) — Arvind Regukumar

from __future__ import annotations

import json

from audit.logger import AuditLogger, GENESIS_HASH


def test_first_entry_chains_to_genesis(tmp_path):
    logger = AuditLogger(tmp_path / "audit.jsonl")
    entry = logger.append("scan", {"host": "dbhost01"})
    assert entry.seq == 1
    assert entry.prev_hash == GENESIS_HASH


def test_entries_chain_in_order(tmp_path):
    logger = AuditLogger(tmp_path / "audit.jsonl")
    e1 = logger.append("scan", {"n": 1})
    e2 = logger.append("recommendation", {"n": 2})
    e3 = logger.append("approval_decision", {"n": 3})

    assert e2.prev_hash == e1.entry_hash
    assert e3.prev_hash == e2.entry_hash

    ok, reason = logger.verify_chain()
    assert ok, reason


def test_tampering_with_a_past_entry_is_detected(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_path)
    logger.append("scan", {"n": 1})
    logger.append("recommendation", {"n": 2})

    lines = log_path.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"] = {"n": 999}  # attacker rewrites history
    lines[0] = json.dumps(tampered)
    log_path.write_text("\n".join(lines) + "\n")

    ok, reason = logger.verify_chain()
    assert not ok
    assert "tampered" in reason


def test_deleting_a_middle_entry_breaks_the_chain(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_path)
    logger.append("scan", {"n": 1})
    logger.append("recommendation", {"n": 2})
    logger.append("approval_decision", {"n": 3})

    lines = log_path.read_text().splitlines()
    del lines[1]  # attacker deletes the middle entry
    log_path.write_text("\n".join(lines) + "\n")

    ok, reason = logger.verify_chain()
    assert not ok


def test_append_survives_concurrent_style_sequential_calls_with_correct_seq(tmp_path):
    logger = AuditLogger(tmp_path / "audit.jsonl")
    entries = [logger.append("scan", {"i": i}) for i in range(20)]
    assert [e.seq for e in entries] == list(range(1, 21))
    ok, reason = logger.verify_chain()
    assert ok, reason
