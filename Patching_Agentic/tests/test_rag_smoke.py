# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial RAG smoke test against real Qdrant (ingest -> version-aware retrieve) — Arvind Regukumar
#   2026-09-22T17:33:36+05:30 — De-hardcoded the object/point count (was == 2, broke the moment RMAN/datapatch/RAC/ASM knowledge objects were added) — Arvind Regukumar
#   2026-09-22T17:44:12+05:30 — Updated SAMPLE_DIR: sample_knowledge/ renamed to knowledge_staging/ (part of the new staging -> inbox DMZ-transfer-simulation boundary, see rag/ingestion/dmz_transfer.py) — Arvind Regukumar
#   2026-09-22T20:46:44+05:30 — Added an explicit 30s timeout to the QdrantClient fixture — the default was too tight for this dev machine under real load (VM + Ollama + Postgres all running at once), causing a real ingest() upsert to time out, not just a connectivity blip — Arvind Regukumar

"""Requires a running Qdrant (docker compose up -d qdrant). Uses
DeterministicTestEmbedder, not sentence-transformers — this test verifies the
ingestion/retrieval PLUMBING (collection creation, upsert, payload filter,
client-side version filtering) against a real Qdrant instance, not retrieval
quality, which needs a real model. Skipped automatically if Qdrant isn't
reachable, so it doesn't block a plain `pytest` run in an environment without
docker compose up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag.ingestion.embeddings import DeterministicTestEmbedder
from rag.ingestion.ingest import ingest, load_knowledge_objects
from rag.retrieval.retrieve import retrieve

SAMPLE_DIR = Path(__file__).parent.parent / "rag" / "knowledge_staging"


@pytest.fixture
def qdrant_client():
    qdrant_client_mod = pytest.importorskip("qdrant_client")
    # Explicit generous timeout — the client's default is too tight for a dev
    # machine under real load (observed: QEMU + Ollama + Postgres all running
    # at once caused real upsert() calls, not just connectivity, to time out).
    client = qdrant_client_mod.QdrantClient(host="localhost", port=6333, timeout=30)
    try:
        client.get_collections()
    except Exception as exc:
        pytest.skip(f"Qdrant not reachable on localhost:6333: {exc}")
    return client


def test_ingest_and_version_aware_retrieve(qdrant_client):
    collection = "test_oracle_procedures_smoke"
    embedder = DeterministicTestEmbedder()

    objects = load_knowledge_objects(SAMPLE_DIR)
    # Not a fixed count — knowledge_staging/ grows as more reference knowledge
    # objects get added; this just confirms loading found *something* real.
    assert len(objects) >= 2

    count = ingest(qdrant_client, collection, embedder, objects)
    assert count == len(objects)

    # In-range version should surface the data_guard object when filtered by category.
    hits = retrieve(
        qdrant_client,
        collection,
        embedder,
        query_text="standby first patch order",
        category="data_guard",
        target_version="19.28.0.0.0",
        top_k=5,
    )
    assert len(hits) == 1
    assert hits[0]["id"] == "data_guard_ru_patch_order"

    # Out-of-range version should exclude it even though the category matches.
    hits_out_of_range = retrieve(
        qdrant_client,
        collection,
        embedder,
        query_text="standby first patch order",
        category="data_guard",
        target_version="21.1.0.0.0",
        top_k=5,
    )
    assert hits_out_of_range == []

    qdrant_client.delete_collection(collection)
