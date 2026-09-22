# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial embedder interface + sentence-transformers implementation — Arvind Regukumar

"""Embedding interface for RAG ingestion/retrieval.

Kept as a small interface (not just a bare function) because the production
path and the test path are genuinely different: production uses a real
sentence-transformers model, pre-downloaded onto the air-gapped box during
the monthly DMZ transfer (DB_PATCHING_SCOPE.md component 2, "Ingestion") —
there is no internet access at query time to fetch it. Tests use a
deterministic hash-based stand-in so Qdrant plumbing (collection creation,
upsert, filtered search) can be verified without a multi-hundred-MB model
download in CI or this sandbox.
"""

from __future__ import annotations

import hashlib
from typing import Protocol


class Embedder(Protocol):
    dimension: int

    def embed(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    """Real embedder. Requires the model already cached locally — see module docstring."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # deferred: heavy import

        self._model = SentenceTransformer(model_name)
        self.dimension = self._model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()


class DeterministicTestEmbedder:
    """Not for production retrieval quality — only for exercising Qdrant plumbing
    offline (no model download, no network). Same text always maps to the same
    vector; unrelated texts are not meaningfully close to each other."""

    def __init__(self, dimension: int = 32):
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Expand the 32-byte digest into `dimension` floats in [-1, 1].
        values = [(digest[i % len(digest)] / 127.5) - 1.0 for i in range(self.dimension)]
        return values
