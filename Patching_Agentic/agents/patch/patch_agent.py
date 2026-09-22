# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial Patch Agent (RAG retrieval + local LLM + schema-gated output) — Arvind Regukumar

"""Proposes a PatchPlan. Never executes anything itself.

Enforces DB_PATCHING_SCOPE.md's core design principle in two independent
layers, not one:

1. The JSON schema sent to the LLM has `procedure_id` constrained to an
   `enum` built from the live ProcedureRegistry — the model is not even
   offered the option of inventing an id.
2. Even so, the parsed response's procedure_id is re-checked against the
   registry after parsing (registry.get() raises UnknownProcedureError if
   not found) — belt and braces, because "the model was told to only pick
   from a list" is a prompting technique, not a security boundary; the real
   boundary is executor.py's registry lookup down the line. This module's
   job is to fail closed and escalate to a human on repeated invalid output,
   never to guess or relax validation to get something that parses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from openai import OpenAI

from config.settings import LLMSettings
from registry.procedures.registry import ProcedureRegistry

_SCHEMA_PATH = Path(__file__).parent.parent.parent / "schemas" / "patch_plan.schema.json"

_SYSTEM_PROMPT = """You are the Oracle Database Patch Agent for an on-premises, \
air-gapped patching system. You propose a single patch plan as JSON matching the \
provided schema. You do not have execution authority — your output is only ever a \
reference to a pre-vetted procedure_id and a summary of preconditions you evaluated \
from the scan data and retrieved knowledge you were given. Never include shell \
commands, SQL, or file paths in any field. If the scan data or retrieved knowledge \
is insufficient to confidently set a precondition to true, leave it false — false \
is the safe default, not a guess."""


class PatchAgentEscalationError(RuntimeError):
    """Raised when the LLM cannot produce schema-valid output after retries.

    The caller must treat this as "route to a human," never as license to
    fabricate a plan or relax the schema to force a parse.
    """


def _build_schema_with_enum(registry: ProcedureRegistry) -> dict[str, Any]:
    schema = json.loads(_SCHEMA_PATH.read_text())
    schema["properties"]["procedure_id"]["enum"] = sorted(p.procedure_id for p in registry)
    return schema


def _build_user_prompt(scan_result: dict[str, Any], retrieval_hits: list[dict[str, Any]], registry: ProcedureRegistry) -> str:
    procedure_summaries = [
        {"procedure_id": p.procedure_id, "description": p.description, "target_type": p.target_type}
        for p in registry
    ]
    return json.dumps(
        {
            "scan_result": scan_result,
            "retrieved_knowledge": retrieval_hits,
            "available_procedures": procedure_summaries,
        },
        indent=2,
    )


def propose_plan(
    scan_result: dict[str, Any],
    retrieval_hits: list[dict[str, Any]],
    registry: ProcedureRegistry,
    llm: LLMSettings,
) -> dict[str, Any]:
    schema = _build_schema_with_enum(registry)
    client = OpenAI(base_url=llm.base_url, api_key="not-needed-local-server")

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(scan_result, retrieval_hits, registry)},
    ]

    last_error: Exception | None = None
    for attempt in range(llm.max_schema_retries + 1):
        response = client.chat.completions.create(
            model=llm.model,
            messages=messages,
            temperature=llm.temperature,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "patch_plan", "schema": schema, "strict": True},
            },
        )
        raw_content = response.choices[0].message.content

        try:
            plan = json.loads(raw_content)
            jsonschema.validate(plan, schema)
            # Belt and braces: re-check against the registry directly, not just the enum.
            registry.get(plan["procedure_id"])
            return plan
        except (json.JSONDecodeError, jsonschema.ValidationError, KeyError) as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": raw_content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": f"That output was invalid: {exc}. Return ONLY corrected JSON matching the schema.",
                }
            )

    raise PatchAgentEscalationError(
        f"LLM failed to produce a schema-valid patch plan after {llm.max_schema_retries + 1} attempt(s): {last_error}"
    )
