# Changelog:
#   2026-09-22T17:44:12+05:30 — Initial DMZ transfer simulation (staging -> inbox, manifest + append-only log) — Arvind Regukumar

"""Simulates DB_PATCHING_SCOPE.md's "one-directional DMZ transfer" locally.

The real production design (component 2, "Ingestion"): a staging machine
with internet access authors/curates knowledge content; it crosses into the
air-gapped production network one-directionally; the production ingestion
job (ingest.py) reads only from what arrived, never reaches the network
itself. That boundary has never actually existed in this repo before —
`rag/ingestion/ingest.py` and everything upstream of it (curation, transfer)
lived in one flat directory with no separation between "authored" and
"what's actually landed for ingestion."

This module makes that boundary real, locally, without needing a second
machine:

    knowledge_staging/   the authoring area (tracked in git — this is real,
                          curated source content, analogous to what would
                          live on the staging machine)
              |
              | transfer() — one-directional: reads staging, writes inbox.
              | Never the other way. A real DMZ transfer is a copy, not a
              | sync — this mirrors that by making inbox always match
              | staging's current state after a transfer, not accumulate
              | independently.
              v
    knowledge_inbox/      what ingest.py actually reads. Gitignored — it's a
                          derived snapshot of the last transfer, regenerated
                          by running this script, not hand-edited. A fresh
                          clone has an empty inbox until transfer() runs once,
                          same as a fresh air-gapped box before its first
                          monthly DMZ transfer.

MANIFEST.json in the inbox records what/when the last transfer contained
(name, sha256, size per file) — the provenance record for "what's actually
in the knowledge base right now." dmz_transfer_log.jsonl (tracked in git,
append-only) keeps a running history across every transfer ever run, the
same spirit as this project's other append-only logs.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MANIFEST_FILENAME = "MANIFEST.json"


@dataclass(frozen=True)
class TransferManifest:
    transferred_at: str
    source: str
    files: list[dict]


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transfer(staging_dir: Path, inbox_dir: Path, log_path: Path) -> TransferManifest:
    """One-directional: staging -> inbox. Inbox ends up exactly matching
    staging's current *.yml files — files removed from staging are removed
    from inbox too, since a real DMZ transfer is a fresh copy each cycle,
    not an ever-growing accumulation of everything ever staged.
    """
    inbox_dir.mkdir(parents=True, exist_ok=True)

    staging_files = sorted(staging_dir.glob("*.yml"))
    staging_names = {f.name for f in staging_files}

    # Remove anything in inbox that's no longer in staging (except the manifest itself).
    for existing in inbox_dir.glob("*.yml"):
        if existing.name not in staging_names:
            existing.unlink()

    file_records = []
    for src in staging_files:
        dest = inbox_dir / src.name
        shutil.copy2(src, dest)
        file_records.append(
            {
                "name": src.name,
                "sha256": _sha256_of(src),
                "size_bytes": src.stat().st_size,
            }
        )

    manifest = TransferManifest(
        transferred_at=datetime.now(timezone.utc).isoformat(),
        source=f"{staging_dir} (simulated one-directional DMZ transfer)",
        files=file_records,
    )

    manifest_path = inbox_dir / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(
            {"transferred_at": manifest.transferred_at, "source": manifest.source, "files": manifest.files},
            indent=2,
            sort_keys=True,
        )
    )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as fh:
        fh.write(
            json.dumps(
                {
                    "transferred_at": manifest.transferred_at,
                    "file_count": len(file_records),
                    "file_names": [f["name"] for f in file_records],
                }
            )
            + "\n"
        )

    return manifest


def read_manifest(inbox_dir: Path) -> Optional[TransferManifest]:
    """Returns None if no transfer has ever run — same state as a fresh
    air-gapped box before its first monthly DMZ transfer."""
    manifest_path = inbox_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    raw = json.loads(manifest_path.read_text())
    return TransferManifest(**raw)


if __name__ == "__main__":
    _scaffold_root = Path(__file__).parent.parent.parent
    result = transfer(
        staging_dir=_scaffold_root / "rag" / "knowledge_staging",
        inbox_dir=_scaffold_root / "rag" / "knowledge_inbox",
        log_path=_scaffold_root / "rag" / "dmz_transfer_log.jsonl",
    )
    print(f"Transferred {len(result.files)} file(s) at {result.transferred_at}")
    for f in result.files:
        print(f"  - {f['name']} ({f['size_bytes']} bytes, sha256 {f['sha256'][:12]}...)")
