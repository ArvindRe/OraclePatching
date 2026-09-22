# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial procedure registry loader — Arvind Regukumar
#   2026-09-22T16:49:41+05:30 — Renamed base_repo_path param to ansible_dir — playbook paths (e.g. "playbooks/cpu_patch_precheck.yml") now resolve against the new ansible/ subdirectory, not the repo root — Arvind Regukumar

"""Loads and validates the allow-listed procedure registry.

Every entry's playbook paths are verified to exist on disk at load time. If any
registered procedure points at a missing playbook, the *entire registry* fails to
load — a broken entry never silently becomes a "procedure_id that exists but does
nothing." The Executor should only ever import ProcedureRegistry, never read the
YAML files directly.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from registry.procedures.schema import ProcedureDefinition


class UnknownProcedureError(KeyError):
    """Raised when a procedure_id (e.g. one an LLM proposed) is not in the registry.

    The Executor must treat this as a hard stop, never a fallback to some inferred
    or ad hoc action.
    """


class ProcedureRegistry:
    def __init__(self, procedures: dict[str, ProcedureDefinition]):
        self._procedures = procedures

    @classmethod
    def load(cls, procedures_dir: Path, ansible_dir: Path) -> "ProcedureRegistry":
        procedures: dict[str, ProcedureDefinition] = {}

        for yml_path in sorted(procedures_dir.glob("*.yml")):
            raw = yaml.safe_load(yml_path.read_text())
            definition = ProcedureDefinition.model_validate(raw)

            if definition.procedure_id != yml_path.stem:
                raise ValueError(
                    f"{yml_path}: procedure_id '{definition.procedure_id}' must "
                    f"match filename '{yml_path.stem}.yml'"
                )

            for field in ("precheck_playbook", "apply_playbook", "rollback_info_playbook"):
                rel_path = getattr(definition, field)
                full_path = ansible_dir / rel_path
                if not full_path.is_file():
                    raise FileNotFoundError(
                        f"{yml_path}: {field} '{rel_path}' does not exist under "
                        f"{ansible_dir} — refusing to load a registry with a "
                        f"dangling procedure reference"
                    )

            if definition.procedure_id in procedures:
                raise ValueError(f"duplicate procedure_id '{definition.procedure_id}'")

            procedures[definition.procedure_id] = definition

        return cls(procedures)

    def get(self, procedure_id: str) -> ProcedureDefinition:
        try:
            return self._procedures[procedure_id]
        except KeyError:
            raise UnknownProcedureError(
                f"'{procedure_id}' is not an allow-listed procedure_id. Known: "
                f"{sorted(self._procedures)}"
            ) from None

    def __contains__(self, procedure_id: str) -> bool:
        return procedure_id in self._procedures

    def __iter__(self):
        return iter(self._procedures.values())

    def __len__(self) -> int:
        return len(self._procedures)
