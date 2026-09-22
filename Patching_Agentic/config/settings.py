# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial settings loader — Arvind Regukumar

"""Loads config/settings.yaml plus environment overrides for secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"


@dataclass(frozen=True)
class QdrantSettings:
    host: str
    port: int
    collection: str


@dataclass(frozen=True)
class PostgresSettings:
    host: str
    port: int
    db: str
    user: str
    password: str


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    model: str
    max_schema_retries: int
    temperature: float


@dataclass(frozen=True)
class Settings:
    base_repo_path: Path
    qdrant: QdrantSettings
    postgres: PostgresSettings
    llm: LLMSettings
    audit_log_path: Path
    procedures_dir: Path


def load_settings(path: Path = _SETTINGS_PATH) -> Settings:
    raw = yaml.safe_load(path.read_text())

    scaffold_root = path.parent.parent
    base_repo_path = (scaffold_root / raw["base_repo_path"]).resolve()

    return Settings(
        base_repo_path=base_repo_path,
        qdrant=QdrantSettings(**raw["qdrant"]),
        postgres=PostgresSettings(
            **raw["postgres"],
            password=os.environ.get("POSTGRES_PASSWORD", ""),
        ),
        llm=LLMSettings(**raw["llm"]),
        audit_log_path=Path(raw["audit"]["log_path"]).expanduser(),
        procedures_dir=(scaffold_root / raw["registry"]["procedures_dir"]).resolve(),
    )
