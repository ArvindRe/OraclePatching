# Changelog:
#   2026-09-22T17:44:12+05:30 — Initial DMZ transfer simulation tests (copy, manifest, mirror-deletes, append-only log) — Arvind Regukumar

from __future__ import annotations

import json

from rag.ingestion.dmz_transfer import read_manifest, transfer


def test_transfer_copies_files_and_writes_manifest(tmp_path):
    staging = tmp_path / "staging"
    inbox = tmp_path / "inbox"
    log = tmp_path / "log.jsonl"
    staging.mkdir()
    (staging / "a.yml").write_text("id: a\n")
    (staging / "b.yml").write_text("id: b\n")

    manifest = transfer(staging, inbox, log)

    assert {f.name for f in inbox.glob("*.yml")} == {"a.yml", "b.yml"}
    assert {f["name"] for f in manifest.files} == {"a.yml", "b.yml"}
    for f in manifest.files:
        assert len(f["sha256"]) == 64  # real sha256 hex digest, not a placeholder


def test_transfer_mirrors_staging_removed_files_get_removed_from_inbox(tmp_path):
    staging = tmp_path / "staging"
    inbox = tmp_path / "inbox"
    log = tmp_path / "log.jsonl"
    staging.mkdir()
    (staging / "a.yml").write_text("id: a\n")
    (staging / "b.yml").write_text("id: b\n")
    transfer(staging, inbox, log)

    (staging / "b.yml").unlink()
    manifest = transfer(staging, inbox, log)

    assert {f.name for f in inbox.glob("*.yml")} == {"a.yml"}
    assert {f["name"] for f in manifest.files} == {"a.yml"}


def test_read_manifest_returns_none_before_any_transfer(tmp_path):
    # Same state as a fresh clone / fresh air-gapped box before its first transfer.
    assert read_manifest(tmp_path / "never_transferred") is None


def test_read_manifest_matches_what_transfer_wrote(tmp_path):
    staging = tmp_path / "staging"
    inbox = tmp_path / "inbox"
    log = tmp_path / "log.jsonl"
    staging.mkdir()
    (staging / "a.yml").write_text("id: a\n")

    written = transfer(staging, inbox, log)
    read_back = read_manifest(inbox)

    assert read_back.transferred_at == written.transferred_at
    assert read_back.files == written.files


def test_transfer_appends_to_log_across_multiple_runs(tmp_path):
    staging = tmp_path / "staging"
    inbox = tmp_path / "inbox"
    log = tmp_path / "log.jsonl"
    staging.mkdir()
    (staging / "a.yml").write_text("id: a\n")

    transfer(staging, inbox, log)
    transfer(staging, inbox, log)  # second run — log should have 2 entries, not overwrite

    lines = log.read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        entry = json.loads(line)
        assert entry["file_count"] == 1
        assert entry["file_names"] == ["a.yml"]
