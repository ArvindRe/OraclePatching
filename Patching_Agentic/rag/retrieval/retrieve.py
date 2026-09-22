# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial version-aware Qdrant retrieval — Arvind Regukumar

"""Version-aware retrieval against the static Qdrant knowledge store.

DB_PATCHING_SCOPE.md component 2: "version-aware query against Qdrant." Since
version strings ("19.28.0.0.0") don't sort correctly as plain strings in a
Qdrant payload range filter, ingestion should also store a sortable integer
form — this module does that comparison client-side after an unfiltered
category search instead, which is correct for the POC's data volumes (a
handful of procedure objects, not millions) without requiring a schema change
to Qdrant's filter DSL. Revisit if the knowledge base grows enough that
client-side filtering becomes the bottleneck.
"""

from __future__ import annotations

from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from rag.ingestion.embeddings import Embedder


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def _applies_to_version(payload: dict, target_version: Optional[str]) -> bool:
    if target_version is None:
        return True
    min_v = payload.get("applicable_min_version")
    max_v = payload.get("applicable_max_version")
    target = _version_tuple(target_version)
    if min_v and target < _version_tuple(min_v):
        return False
    if max_v and target > _version_tuple(max_v):
        return False
    return True


def retrieve(
    client: QdrantClient,
    collection: str,
    embedder: Embedder,
    query_text: str,
    category: Optional[str] = None,
    target_version: Optional[str] = None,
    top_k: int = 5,
) -> list[dict]:
    query_filter = None
    if category is not None:
        query_filter = Filter(must=[FieldCondition(key="category", match=MatchValue(value=category))])

    # Over-fetch before client-side version filtering so top_k version-eligible
    # results are still likely to surface even if some top vector matches get
    # excluded by version.
    hits = client.query_points(
        collection_name=collection,
        query=embedder.embed(query_text),
        query_filter=query_filter,
        limit=top_k * 4,
    ).points

    results = [hit.payload for hit in hits if _applies_to_version(hit.payload, target_version)]
    return results[:top_k]
