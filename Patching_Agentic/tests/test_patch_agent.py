# Changelog:
#   2026-09-22T20:39:12+05:30 — Initial Patch Agent tests — current_version override, the safety property added after three separate live hallucination incidents — Arvind Regukumar

"""No test file existed for patch_agent.py before this — it had only ever been
exercised live/manually. This covers the one behavior that actually matters for
safety: current_version gets overwritten with the real scan_result reading
regardless of what the (mocked, in these tests) LLM said, since the model has
proven unreliable at this field three separate times in live testing even with
correct data available to copy — see this file's own changelog and
agents/patch/patch_agent.py's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.patch.patch_agent import PatchAgentEscalationError, propose_plan
from config.settings import LLMSettings
from registry.procedures.registry import ProcedureRegistry

SCAFFOLD_ROOT = Path(__file__).parent.parent
ANSIBLE_DIR = (SCAFFOLD_ROOT / ".." / "ansible").resolve()
PROCEDURES_DIR = SCAFFOLD_ROOT / "registry" / "procedures"


@pytest.fixture
def registry():
    return ProcedureRegistry.load(PROCEDURES_DIR, ANSIBLE_DIR)


@pytest.fixture
def llm_settings():
    return LLMSettings(
        base_url="http://fake-llm.invalid/v1",
        model="fake-model",
        max_schema_retries=1,
        temperature=0.1,
        timeout_seconds=5,
        preflight_timeout_seconds=5,
    )


def _scan_result_with_version(version: str | None) -> dict:
    if version is None:
        return {"cdbs": []}
    return {"cdbs": [{"sid": "CDB1", "raw": f"READ WRITE\nOPEN\nPRIMARY\nNO\nN/A\n{version}"}]}


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


def _fake_openai_returning(*plans: dict):
    """Builds a fake OpenAI class whose chat.completions.create() returns each
    plan in `plans` in sequence (one per call) — lets a test simulate a retry."""
    remaining = list(plans)

    class _FakeCompletions:
        def create(self, **kwargs):
            plan = remaining.pop(0) if remaining else plans[-1]
            return _FakeResponse(json.dumps(plan))

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = _FakeChat()

    return _FakeOpenAI


def _hallucinated_plan(procedure_id: str, bogus_version: str) -> dict:
    return {
        "procedure_id": procedure_id,
        "target": "single_instance",
        "current_version": bogus_version,
        "target_version": "19.28.0.0.0",
        "preconditions_checked": {},
    }


def test_current_version_is_overwritten_with_real_scan_data(registry, llm_settings, monkeypatch):
    import agents.patch.patch_agent as patch_agent_module

    monkeypatch.setattr(
        patch_agent_module,
        "OpenAI",
        _fake_openai_returning(_hallucinated_plan("oracle_19c_ru_patch", "19.99.99.99.99")),
    )

    scan_result = _scan_result_with_version("19.28.0.0.0")
    plan = propose_plan(scan_result, [], registry, llm_settings)

    # The LLM said "19.99.99.99.99" — real scan data says "19.28.0.0.0" — the
    # real value must win, unconditionally.
    assert plan["current_version"] == "19.28.0.0.0"


def test_current_version_is_empty_string_when_scan_has_no_version(registry, llm_settings, monkeypatch):
    import agents.patch.patch_agent as patch_agent_module

    monkeypatch.setattr(
        patch_agent_module,
        "OpenAI",
        _fake_openai_returning(_hallucinated_plan("oracle_19c_ru_patch", "1.0")),
    )

    scan_result = _scan_result_with_version(None)  # no cdbs at all
    plan = propose_plan(scan_result, [], registry, llm_settings)

    # No real data available — must be an honest empty string, not the model's guess.
    assert plan["current_version"] == ""


def test_escalates_after_repeated_unknown_procedure_id(registry, llm_settings, monkeypatch):
    import agents.patch.patch_agent as patch_agent_module

    # Every attempt proposes an id that isn't on the allow-list — should exhaust
    # retries and escalate, never fabricate or relax validation to force a parse.
    monkeypatch.setattr(
        patch_agent_module,
        "OpenAI",
        _fake_openai_returning(_hallucinated_plan("made_up_procedure", "19.0.0.0.0")),
    )

    scan_result = _scan_result_with_version("19.28.0.0.0")
    with pytest.raises(PatchAgentEscalationError):
        propose_plan(scan_result, [], registry, llm_settings)
