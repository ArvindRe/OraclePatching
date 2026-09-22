# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial knowledge-object schema for RAG ingestion — Arvind Regukumar

"""Schema for one ingested knowledge-base object.

DB_PATCHING_SCOPE.md component 2: "Ingested as structured procedure objects
(preconditions / steps / validation / rollback), not raw chunked prose." This
is deliberately NOT the same schema as registry/procedures/schema.py's
ProcedureDefinition — that one maps a procedure_id to an actually-executable
playbook and is the allow-list the Executor trusts. This one is reference
knowledge the Patch Agent retrieves to help it reason and to populate
risk_notes — retrieval hits are never executed, only ever read.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class KnowledgeObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    category: str  # e.g. "opatch", "rac", "asm", "data_guard", "rman"
    applicable_min_version: Optional[str] = None
    applicable_max_version: Optional[str] = None
    preconditions: list[str] = []
    steps: list[str] = []
    validation: list[str] = []
    rollback: list[str] = []
    source: str  # citation — oracle-base.com URL, internal runbook name, MOS doc ID, etc.

    def embedding_text(self) -> str:
        """Flattened text used to compute the retrieval vector."""
        parts = [self.title, self.category]
        parts += self.preconditions + self.steps + self.validation + self.rollback
        return "\n".join(parts)
