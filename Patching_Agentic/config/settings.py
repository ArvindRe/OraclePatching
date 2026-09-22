# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial settings loader — Arvind Regukumar
#   2026-09-22T16:49:41+05:30 — Added ansible_dir (playbooks/roles/inventories/vars/ansible.cfg moved under a new ansible/ subdirectory at the repo root) — Arvind Regukumar
#   2026-09-22T17:30:00+05:30 — Added LLMSettings.timeout_seconds/preflight_timeout_seconds and Settings.subprocess_timeout_seconds — Arvind Regukumar
#   2026-09-22T20:06:10+05:30 — Added preflight_vm_timeout_seconds (the VM/SSH check needs a longer ceiling than the fast HTTP-style Qdrant/Ollama/Postgres checks) — Arvind Regukumar

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
    timeout_seconds: float
    preflight_timeout_seconds: float


@dataclass(frozen=True)
class Settings:
    base_repo_path: Path
    ansible_dir: Path
    qdrant: QdrantSettings
    postgres: PostgresSettings
    llm: LLMSettings
    audit_log_path: Path
    procedures_dir: Path
    subprocess_timeout_seconds: float
    preflight_timeout_seconds: float
    preflight_vm_timeout_seconds: float


def load_settings(path: Path = _SETTINGS_PATH) -> Settings:
    raw = yaml.safe_load(path.read_text())

    scaffold_root = path.parent.parent
    base_repo_path = (scaffold_root / raw["base_repo_path"]).resolve()

    return Settings(
        base_repo_path=base_repo_path,
        # All Ansible content (playbooks/, roles/, inventories/, vars/,
        # ansible.cfg) lives under ansible/ — kept separate from base_repo_path
        # since ansible.cfg's own roles_path/inventory defaults only resolve
        # correctly when a tool's cwd is this directory (see
        # executor/ansible_runner.py, agents/scanner/scanner.py).
        ansible_dir=base_repo_path / "ansible",
        qdrant=QdrantSettings(**raw["qdrant"]),
        postgres=PostgresSettings(
            **raw["postgres"],
            password=os.environ.get("POSTGRES_PASSWORD", ""),
        ),
        llm=LLMSettings(**raw["llm"]),
        audit_log_path=Path(raw["audit"]["log_path"]).expanduser(),
        procedures_dir=(scaffold_root / raw["registry"]["procedures_dir"]).resolve(),
        subprocess_timeout_seconds=raw["subprocess_timeout_seconds"],
        preflight_timeout_seconds=raw["preflight_timeout_seconds"],
        preflight_vm_timeout_seconds=raw["preflight_vm_timeout_seconds"],
    )
