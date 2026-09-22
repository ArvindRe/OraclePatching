# Oracle DB Patching — Agentic POC

Full-stack scaffold for [DB_PATCHING_SCOPE.md](DB_PATCHING_SCOPE.md)'s Phase 1 POC:
an LLM-assisted layer sitting on top of the base [OraclePatching](..) repo's
`roles/oracle_cpu_patch` playbooks, with a hard rule everything here is built
around — **the LLM never has execution authority.** It only ever proposes a
`procedure_id` from an allow-listed registry; the Executor is what actually
runs anything, and only by looking that id up.

## Two decisions locked in during review, before any of this was built

1. **Rollback stays manual, always.** `DB_PATCHING_SCOPE.md`'s original
   Executor description said a validation failure "triggers rollback
   automatically" — that directly contradicted the base repo's
   [CLAUDE.md](../CLAUDE.md) design decision #4 ("rollback is never
   automated"). Resolved in favor of the existing rule: `executor/executor.py`
   creates a guaranteed restore point *before* applying (safe, additive,
   reversible-to-drop) but on any failure it stops, prints the manual rollback
   procedure via `cpu_patch_rollback_info.yml`, and hands the decision to a
   human. See `executor/rollback/snapshot.py`'s docstring for the full
   reasoning, and `tests/test_executor.py::test_apply_failure_surfaces_rollback_info_and_never_auto_rolls_back`
   for the test that locks this in.
2. **Build the full 7-component scaffold now**, not just the parts that don't
   need new infra — even though the base repo's own Phase 2
   (`../docs/ROADMAP.md`) isn't closed yet (no real patch applied to a live
   19c CDB). This scaffold is therefore **structurally wired and unit-tested,
   not proven against a live target** — same caveat `STATUS.md` already
   applies to the base repo, extended to cover this layer too.

## Layout

| Path | Component (DB_PATCHING_SCOPE.md #) | Status |
|---|---|---|
| `agents/scanner/` | 1. Environment Scanning Agent | Playbook written, Jinja verified via real `Templar`, **never run against a live host** |
| `rag/ingestion/`, `rag/retrieval/` | 2. Knowledge Base (RAG) | Real Qdrant ingestion/retrieval, smoke-tested against a live container (`tests/test_rag_smoke.py`); Postgres operational store smoke-tested too (`tests/test_operational_store_smoke.py`) |
| `agents/patch/` | 3. Patch Agent | Real OpenAI-compatible client + two-layer schema gate (JSON-schema `enum` + registry re-check), **never called against a real local LLM** (no model server running in this environment) |
| `registry/procedures/` | 4. Procedure Registry | Real, unit-tested (`tests/test_registry.py`) — allow-list enforced at load time (dangling playbook path fails the whole registry load, not just that entry) |
| `executor/` | 5. Executor | Real orchestration, fully unit-tested branch-by-branch (`tests/test_executor.py`), **never run against a live host** — dry-run is the real `opatchauto -analyze`/`opatch prereq` conflict analysis (via the existing precheck playbook), not `ansible-playbook --check` (see `executor/ansible_runner.py` docstring for why that distinction matters) |
| `approval/` | 6. Human Approval Gate | Real report builder + blocking CLI confirmation, reuses the base repo's "type the exact value" pattern (target_version here, `confirm_patch` downstream) |
| `audit/` | 7. Audit Log | Real hash-chained, append-only JSON-lines logger, tamper-detection unit-tested (`tests/test_audit_logger.py`) — **separate file** from the base repo's own per-invocation audit log (`~/.oracle_patching/audit.log`); this one is `~/.oracle_patching/agentic_audit.jsonl` and covers the agentic pipeline's own steps (scan/retrieval/recommendation/approval/dry-run/execution/validation), not every raw `ansible-playbook` invocation |

`run_patch.py` is the CLI that wires all seven together.

## Known gaps found and fixed during the build

- `scan_playbook.yml` originally used `default(omit)` inside a nested dict
  literal for `grid_home`/`crs_version_raw` — `omit` only works when a Jinja
  expression is the *entire* value of a module parameter, not embedded inside
  a larger structure. Caught by rendering through a real `ansible.template.Templar`
  (same validation standard as the base repo), fixed to `default('')`.
- `scanner.py`'s `subprocess.run(..., env={...})` originally replaced the
  whole child environment instead of extending it, which would have broken
  `PATH`/`HOME` for the ansible-playbook subprocess. Fixed to `{**os.environ, ...}`.
- `rag/ingestion/ingest.py` originally used `KnowledgeObject.id` (a
  human-readable string) directly as the Qdrant point id — Qdrant requires an
  unsigned int or UUID. Fixed with a deterministic `uuid5` mapping so
  re-ingestion of the same source id stays idempotent.
- A knowledge object (`rag/ingestion/sample_knowledge/data_guard_ru_patch_order.yml`)
  was added specifically to encode a gap flagged during review: the original
  scope doc checked Data Guard sync but never specified *patch ordering*
  (standby-first) — the base repo's `roles/oracle_cpu_patch` still has no
  DG-role-aware ordering logic; this is retrieved knowledge for now, not
  automated behavior.

## Quickstart (this dev machine)

```bash
cd Patching_Agentic
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Bring up Qdrant + Postgres
POSTGRES_PASSWORD=<choose one> docker compose up -d

# Unit tests (no infra needed) + smoke tests (need the containers above)
POSTGRES_PASSWORD=<same one> pytest tests/ -v
```

`run_patch.py` additionally needs a local OpenAI-compatible LLM server
(Ollama or llama.cpp, see `config/settings.yaml`'s `llm.base_url`) — not set
up in this environment. Until one exists, `agents/patch/patch_agent.py` is
exercised by unit test only for its schema-gating logic, not end-to-end.

## What this is not

Not a production system, not validated against a real Oracle instance at any
layer, and not a replacement for the base repo's own Phase 2 exit gate
(`../docs/ROADMAP.md`) — a real patch has still never been applied through
either the base playbooks or this scaffold. Treat every "real" claim in the
table above as "real code, exercised where it could be, against a target this
sandbox could actually reach" — not as a production readiness claim.

---

## Changelog

- 2026-09-22T15:53:48+05:30 — Initial version — full 7-component scaffold, quickstart, known gaps found during build — Arvind Regukumar
