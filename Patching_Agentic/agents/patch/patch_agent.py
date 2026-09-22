# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial Patch Agent (RAG retrieval + local LLM + schema-gated output) — Arvind Regukumar
#   2026-09-22T17:30:00+05:30 — Added a real client timeout + openai.APIError handling — a demo run got Ctrl-C'd while waiting on the LLM, and the orphaned request kept running server-side (llama.cpp's single -np 1 slot), blocking every subsequent call with no client-side signal anything was wrong. Without a timeout this call could hang indefinitely; now it fails closed into PatchAgentEscalationError like every other failure mode here — Arvind Regukumar
#   2026-09-22T19:44:29+05:30 — Added explicit system-prompt guidance after observing a real hallucination: the model returned current_version="19.99.99.99.99" — a procedure's max_supported_version range boundary, not a real Oracle release — apparently copied from available_procedures in-context rather than read from scan data (which had no clean version field until scan_playbook.yml's version_full fix). Now explicitly tells the model where to read the real version from and that an empty string beats a fabricated one — Arvind Regukumar
#   2026-09-22T20:02:32+05:30 — Found the fix above was aimed at the wrong source and didn't actually work — re-ran through demo.py itself (not just an isolated script) after the version_full fix and still got current_version="19.99.99.99.99". available_procedures never actually carried min/max_supported_version (checked the code); the real leak was retrieved_knowledge — every retrieved KnowledgeObject's applicable_min_version/applicable_max_version (same "19.0.0.0.0"/"19.99.99.99.99" on all 6 objects) was being serialized straight into the prompt. Fixed at the source: those fields are now stripped from the prompt payload in _build_user_prompt (they already did their job server-side in retrieve()'s filter; the model never needs to see them) — Arvind Regukumar
#   2026-09-22T20:39:12+05:30 — Stopped trusting the model for current_version at all, after a third variant of the same unreliability (correct "19.3.0.0.0" on one run, "1.0" on the next, same scan data both times). It's now unconditionally overwritten with agents.scanner.scanner.extract_db_version(scan_result) after the response passes schema validation — a live database fact that doesn't need an LLM's help to know, so stop asking it to guess. target_version stays model-generated since it's a genuine judgment call (which patch this is upgrading TO) nothing in scan_result can answer — Arvind Regukumar

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
import openai
from openai import OpenAI

from agents.scanner.scanner import extract_db_version
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
is the safe default, not a guess.

current_version is verified independently after you respond — whatever you put there \
gets overwritten with the real value read directly from scan_result, so don't spend \
effort on it beyond returning a syntactically valid placeholder; it is not a field you \
need to get right. target_version is different: it is not independently verified, \
because it's a judgment call (which patch/version this plan is upgrading TO) that \
nothing in this prompt can derive from a live reading alone. Never use a version-range \
boundary from anywhere in this prompt (a procedure's supported range, a knowledge \
object's applicable range, or any other min/max-shaped field) as a stand-in for \
target_version — "19.99.99.99.99" in particular is a range boundary, never a real \
Oracle release."""


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
    # applicable_min_version/applicable_max_version already did their job server-side
    # in retrieve()'s version-range filter (rag/retrieval/retrieve.py) — the model
    # doesn't need them and must never see them: they're filtering metadata, not a
    # live version reading, but they're syntactically indistinguishable from one once
    # they're sitting in the prompt. This was the actual, confirmed source of a real
    # observed hallucination (current_version copied straight from a knowledge
    # object's applicable_max_version, "19.99.99.99.99") — not available_procedures,
    # which was wrongly suspected first and never actually carried these fields.
    sanitized_hits = [
        {k: v for k, v in hit.items() if k not in ("applicable_min_version", "applicable_max_version")}
        for hit in retrieval_hits
    ]
    return json.dumps(
        {
            "scan_result": scan_result,
            "retrieved_knowledge": sanitized_hits,
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
    # timeout: a real ceiling on however long ONE call is allowed to hang, not just a
    # connect timeout — the openai SDK's default `timeout` covers the whole request
    # (connect + read), which is exactly what's needed against a local model server
    # that accepts the TCP connection immediately but can then sit silent mid-generation.
    # max_retries=0: the SDK's own transport-level retry is redundant with (and would
    # slow down) the schema-retry loop below; one clear timeout beats a hidden retry storm.
    client = OpenAI(
        base_url=llm.base_url,
        api_key="not-needed-local-server",
        timeout=llm.timeout_seconds,
        max_retries=0,
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(scan_result, retrieval_hits, registry)},
    ]

    last_error: Exception | None = None
    for attempt in range(llm.max_schema_retries + 1):
        try:
            response = client.chat.completions.create(
                model=llm.model,
                messages=messages,
                temperature=llm.temperature,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "patch_plan", "schema": schema, "strict": True},
                },
            )
        except openai.APITimeoutError as exc:
            raise PatchAgentEscalationError(
                f"LLM call to {llm.base_url} timed out after {llm.timeout_seconds}s — the model server may be "
                f"stuck on an orphaned request from a prior run (e.g. a Ctrl-C'd demo.py doesn't cancel the "
                f"server-side generation). Try restarting it (e.g. `brew services restart ollama`) before retrying."
            ) from exc
        except openai.APIConnectionError as exc:
            raise PatchAgentEscalationError(
                f"Could not reach LLM server at {llm.base_url}: {exc}. Is it running?"
            ) from exc

        raw_content = response.choices[0].message.content

        try:
            plan = json.loads(raw_content)
            jsonschema.validate(plan, schema)
            # Belt and braces: re-check against the registry directly, not just the enum.
            registry.get(plan["procedure_id"])
            # Never trust the model's self-reported current_version — proven unreliable
            # live (correct on one run, "19.99.99.99.99" or "1.0"/"19c" on others, even
            # with the real value present in scan_result to copy from). This is a fact
            # about the live database, independently knowable without the model's help,
            # so don't ask it to guess: overwrite with the real reading unconditionally.
            plan["current_version"] = extract_db_version(scan_result) or ""
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
