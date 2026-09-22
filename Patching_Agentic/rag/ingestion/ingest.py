# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial Qdrant ingestion pipeline for structured procedure objects — Arvind Regukumar
#   2026-09-22T15:53:48+05:30 — Fixed point IDs: Qdrant requires uint/UUID, not arbitrary strings — map KnowledgeObject.id through a deterministic UUID5 — Arvind Regukumar

"""Loads structured knowledge objects (YAML) and upserts them into Qdrant.

Per DB_PATCHING_SCOPE.md component 2: "Ingestion: monthly, via a one-directional
DMZ transfer from a connected staging machine. The production server has no
direct internet access." This module assumes the source YAML files have
already made that trip and are sitting on local disk — it does no network
fetch of its own.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import yaml
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from rag.ingestion.embeddings import Embedder
from rag.ingestion.schema import KnowledgeObject

# Qdrant point IDs must be an unsigned int or a UUID — arbitrary strings (our
# human-readable KnowledgeObject.id, e.g. "opatch_conflict_check") are not
# accepted directly. Deterministic UUID5 from that id keeps re-ingestion
# idempotent (same source id always maps to the same point) while still
# letting the human-readable id live in the payload for filtering/display.
_QDRANT_ID_NAMESPACE = uuid.UUID("f0b8a6f0-6b8b-4e6a-9b1a-6b1a6b1a6b1a")


def _point_id(knowledge_object_id: str) -> str:
    return str(uuid.uuid5(_QDRANT_ID_NAMESPACE, knowledge_object_id))


def load_knowledge_objects(source_dir: Path) -> list[KnowledgeObject]:
    objects: list[KnowledgeObject] = []
    for yml_path in sorted(source_dir.glob("*.yml")):
        raw = yaml.safe_load(yml_path.read_text())
        objects.append(KnowledgeObject.model_validate(raw))
    return objects


def ensure_collection(client: QdrantClient, collection: str, dimension: int) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
        )


def ingest(client: QdrantClient, collection: str, embedder: Embedder, objects: list[KnowledgeObject]) -> int:
    ensure_collection(client, collection, embedder.dimension)

    points = [
        PointStruct(
            id=_point_id(obj.id),
            vector=embedder.embed(obj.embedding_text()),
            payload=obj.model_dump(),
        )
        for obj in objects
    ]
    if points:
        client.upsert(collection_name=collection, points=points)
    return len(points)
