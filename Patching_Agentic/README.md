# Oracle DB Patching — Agentic POC

Full-stack scaffold for [DB_PATCHING_SCOPE.md](DB_PATCHING_SCOPE.md)'s Phase 1 POC:
an LLM-assisted layer sitting on top of the base [OraclePatching](..) repo's
`ansible/roles/oracle_cpu_patch` playbooks, with a hard rule everything here is built
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
| `agents/scanner/` | 1. Environment Scanning Agent | **Run against the live vagrant VM (2026-09-22)** — correctly returned OPatch version, lsinventory, listener status, per-CDB state matching known VM state. Found/fixed 2 real bugs along the way (SSH key path, SQL `$`-expansion — see below) |
| `rag/ingestion/`, `rag/retrieval/` | 2. Knowledge Base (RAG) | Real Qdrant ingestion/retrieval, smoke-tested against a live container (`tests/test_rag_smoke.py`); Postgres operational store smoke-tested too (`tests/test_operational_store_smoke.py`) |
| `agents/patch/` | 3. Patch Agent | **Run against a real local LLM (2026-09-22)** — Ollama + `qwen2.5:3b-instruct`, real `response_format: json_schema` call. Correctly picked an allow-listed `procedure_id`; output quality otherwise limited as expected from a 3B model (see below) |
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
  (standby-first) — the base repo's `ansible/roles/oracle_cpu_patch` still has no
  DG-role-aware ordering logic; this is retrieved knowledge for now, not
  automated behavior.
- First live run found two more real bugs, neither catchable statically:
  `ansible/inventories/vagrant_test/group_vars/oracle_db_hosts.yml`'s SSH key path
  was `{{ playbook_dir }}`-relative, which broke specifically for
  `scan_playbook.yml` (three directories deep vs. `ansible/playbooks/*.yml`'s one) —
  fixed to `{{ inventory_dir }}`-relative. And `scan_playbook.yml`'s per-CDB
  SQL task's `printf "...v\\$database..."` one-liner let the shell expand
  `$database` etc. to empty before `sqlplus` ever saw it — fixed with a
  quoted heredoc (`<<'SQL'`). Full writeup: `../docs/CURRENT_ARCHITECTURE.md`.

## Quickstart (this dev machine)

```bash
cd Patching_Agentic
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# One-time: create .env (gitignored, never committed) with a real generated
# password — deliberately not a "<placeholder>" to edit by hand, since this is
# exactly the kind of line that gets copy-pasted verbatim and silently breaks
# auth later. docker-compose.yml auto-loads it; nothing else does, see below.
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" > .env

# Bring up Qdrant + Postgres (reads .env automatically)
docker compose up -d

# Load .env into this shell too — pytest/demo.py/run_patch.py read
# POSTGRES_PASSWORD from the environment directly, not from .env
set -a && source .env && set +a

# Unit tests (no infra needed) + smoke tests (need the containers above)
pytest tests/ -v
```

If you ever forget the password: it's whatever's in `.env` (gitignored,
local to this machine) — but note that changing `.env` does **not** change
an already-running container's actual Postgres password, since that's only
set once, at first `initdb`. To pick up a new value you'd need
`docker compose down -v && docker compose up -d` (the `-v` drops the data
volume too).

`run_patch.py` additionally needs a local OpenAI-compatible LLM server —
this dev machine has Ollama running (`brew services start ollama`,
`qwen2.5:3b-instruct` pulled; sized down from the originally-configured 7B
model because this machine has 8GB total RAM, not the 64GB
`DB_PATCHING_SCOPE.md` assumes — see `config/settings.yaml`'s changelog).

## Run the live demo

`demo.py` opens with a **Step 0 preflight** (VM/Ansible SSH, Qdrant, Ollama,
Postgres — each with a short bounded timeout) that fails fast with a clear
message if anything required isn't responding, then runs an 8-step narrated
walkthrough (live scan → DMZ transfer simulation → RAG ingestion/retrieval →
LLM proposal → registry rejection → Executor fails-safe → audit tamper
detection → test suite) — real calls against the real vagrant
VM/Qdrant/Postgres/Ollama, no mocks. Requires the vagrant VM up
(`cd ../vagrant && vagrant up`, then start `CDB1`/the listener manually —
see `STATUS.md` "Open Issues"), the Quickstart setup above, and Ollama
running. Must be run from a real terminal (not piped/non-interactive) unless
you pass `--auto`.

**Every run writes a full transcript** to
`demo_logs/demo_<local timestamp>.log` (e.g. `demo_20260922T202713.log`)
(gitignored, local only) in addition to printing live — the path is printed
first thing, and again in the closing summary. Added after a real incident:
a Ctrl-C'd run orphaned a stuck generation on Ollama's single request slot
(`llama-server -np 1`), which then hung every subsequent LLM call with no
client-side timeout to catch it and nothing to diagnose it from afterward.
Fixed two ways: (1) the LLM client, and every `ansible-playbook`/`ansible`
subprocess call, now has a real timeout (`config/settings.yaml`'s
`llm.timeout_seconds` / `subprocess_timeout_seconds`) and fails into a clean
error instead of hanging; (2) Step 0 catches exactly this case up front —
verified live by stopping Ollama and confirming the preflight fails in
under a second with a message pointing at `brew services restart ollama`.

```bash
cd Patching_Agentic
source .venv/bin/activate
set -a && source .env && set +a

# Paced — pauses after each step, press Enter to continue
python demo.py

# Or runs straight through, no pauses
python demo.py --auto
```

(**The durable fix, do this once:** add `setopt interactivecomments` to your
`~/.zshrc`. In an interactive `zsh` session — the macOS default, prompt
ending in `%` — `#` does not start a comment at all without it, trailing
*or* on its own line, and every code block in every doc that uses `#` is
affected, not just this repo's. Confirmed two distinct failure modes
directly (not assumed): without the option, a *harmless* `#` comment line
still throws a visible `zsh: command not found: #` for each one (annoying,
non-fatal, the real commands after it still run); but if the comment text
happens to contain an apostrophe, it is far worse — that apostrophe opens
an unterminated quote zsh will wait on forever (`quote>` prompt), silently
swallowing every subsequent line until you Ctrl-C. A real comment reading
"...since that's exactly..." hung a paste exactly this way, reproduced and
fixed here by removing the apostrophe — but that is a per-comment patch,
not a real fix; `setopt interactivecomments` is the one that actually
closes the whole class, everywhere, permanently.)

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
- 2026-09-22T16:40:35+05:30 — First live validation: scanner run against the vagrant VM, Patch Agent run against a real local Ollama model — both successful, 2 more real bugs found/fixed along the way — Arvind Regukumar
- 2026-09-22T16:49:41+05:30 — Updated path references for the new ansible/ subdirectory (playbooks/roles/inventories/vars moved out of the repo root) — Arvind Regukumar
- 2026-09-22T17:05:16+05:30 — Added "Run the live demo" section — demo.py existed but was never documented here — Arvind Regukumar
- 2026-09-22T17:08:37+05:30 — Added a local .env (gitignored) for POSTGRES_PASSWORD, auto-loaded by docker-compose.yml — previously the password only existed in ad hoc shell history, nowhere durable. Updated Quickstart and Run-the-demo commands to `source .env` instead of retyping it — Arvind Regukumar
- 2026-09-22T17:33:36+05:30 — Documented demo.py's new Step 0 preflight and timestamped log-file output, added after a real hang incident (orphaned Ollama generation, no timeout anywhere to catch it) — Arvind Regukumar
- 2026-09-22T17:43:59+05:30 — Fixed a real footgun: `echo "POSTGRES_PASSWORD=<choose one>" > .env` got copy-pasted literally, overwriting a working .env with the placeholder text and breaking Postgres auth. Replaced with `$(openssl rand -hex 16)` so the command produces a real usable password with no manual substitution step to get wrong — Arvind Regukumar
- 2026-09-22T20:27:16+05:30 — Corrected the demo log filename format doc: local system time with a numeric UTC offset, not UTC — Arvind Regukumar
- 2026-09-22T20:29:23+05:30 — Dropped the UTC offset suffix from the demo log filename doc — local time only — Arvind Regukumar
- 2026-09-22T22:59:02+05:30 — Found and fixed the actual bug behind the zsh hang the earlier trailing-comment note only partially explained: the Quickstart's own .env-creation comment contained an apostrophe ("...since that's..."), which opens an unterminated quote in interactive zsh and hangs indefinitely — confirmed live via a direct before/after zsh repro (quote>/unmatched error before, clean parse after). Also found the earlier note's "comments on their own line work in both" claim was wrong even for apostrophe-free comments — raw interactive zsh without `setopt interactivecomments` throws `command not found: #` for every comment line regardless of position, just non-fatally. Reworded the offending comment and rewrote this note to lead with `setopt interactivecomments` as the actual durable fix rather than per-comment wording patches. Swept the whole repo for other apostrophes inside # lines within real bash/sh/zsh fences — found and fixed 2 more (README.md, docs/PHASE2_SETUP_RUNBOOK.md) — Arvind Regukumar
