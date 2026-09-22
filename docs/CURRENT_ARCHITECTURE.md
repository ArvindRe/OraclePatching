# Current Architecture

> Snapshot as of 2026-09-23. This is a point-in-time architecture reference —
> for the live, evolving handoff (what's next, open issues), see
> [STATUS.md](../STATUS.md). For phase gates, see [ROADMAP.md](ROADMAP.md).

This project is two layers, built in two stages, with a hard boundary
between them:

1. **The base Ansible framework** (`ansible/roles/`, `ansible/playbooks/`,
   `ansible/inventories/`, `ansible/vars/`) — deterministic, human-reviewed
   automation. This is what actually touches a database.
2. **The agentic layer** (`Patching_Agentic/`) — an LLM-assisted proposal
   system sitting on top of layer 1. It never executes anything directly;
   it only ever proposes a `procedure_id` that layer 1's Executor looks up
   in an allow-list.

Neither layer has ever applied a real patch to a live Oracle instance. Both
have been exercised against a real (test) 19c CDB for their read-only and
structural paths. Treat every claim below as scoped exactly to what's
stated — "syntax-validated" is not "run," and "run" is not "proven safe for
production."

---

## Layer 1 — Base Ansible framework

```
ansible/playbooks/cpu_patch_precheck.yml   ─┐
ansible/playbooks/cpu_patch_apply.yml       ├─►  ansible/roles/oracle_cpu_patch
ansible/playbooks/cpu_patch_rollback_info.yml ┘
```

### Task flow (`ansible/roles/oracle_cpu_patch/tasks/main.yml`)

```
block:
  precheck.yml        # OPatch version, existing-patch check, free space
  backup_check.yml     # RMAN whole-database backup verification gate,
                        # opt-in RESTORE VALIDATE (verify_backup_restorable)
  stage_patch.yml      # copy/unzip patch zip, real conflict analysis
                        # (opatchauto -analyze / opatch prereq)
  [run_full_patch gate — structurally cannot proceed past here unless true]
  [confirm_patch gate — must exactly equal patch_id]
  stop_services.yml    # manual path only (use_opatchauto: false)
  apply_patch.yml       # opatchauto apply (default) or manual opatch apply
  start_services.yml   # manual path only
  datapatch.yml         # SQL-level patch, once per CDB
  postcheck.yml          # opatch lsinventory + cdb_registry_sqlpatch check,
                          # hard-fails (assert) on any non-SUCCESS status
rescue:
  # capture failing task's message/stderr
always:
  audit_log.yml          # exactly one JSON-lines entry per invocation,
                          # success or failure — delegate_to: localhost
```

### Design principles that don't change silently

See [CLAUDE.md](../CLAUDE.md) "Key Design Decisions" for the full list with
rationale. Load-bearing ones:

- No database password anywhere — OS-authenticated `/ as sysdba` only.
- `run_full_patch` (a structural var), not `--tags`, gates precheck-only mode.
- `confirm_patch` must equal the exact `patch_id`, not a generic "yes."
- **Rollback is never automated** — `cpu_patch_rollback_info.yml` only prints
  the procedure; a human runs it. This is the rule the agentic layer's
  Executor was explicitly built to respect, not work around.
- Audit logging is mandatory (`block`/`rescue`/`always`), not tag-gated.

### Validation status

| Path | Status |
|---|---|
| YAML parse / `--syntax-check` (all 3 playbooks) | ✅ Pass |
| `argv:`/`regex_search` Jinja patterns | ✅ Verified via real `ansible.template.Templar` |
| `cpu_patch_precheck.yml` against a live 19c CDB | ✅ Ran successfully through OPatch check → free-space assert → staging (2026-09-16) |
| `cpu_patch_apply.yml` (the actual patch) against a live CDB | ❌ **Never run** — no real patch downloaded yet (see STATUS.md "Open Issues") |
| RAC / Grid Infrastructure path | ❌ Accepted as a var (`grid_home`), never exercised |
| Backup-verification gate (`backup_check.yml`) | ✅ Built, verified live against the VM (2026-09-22); opt-in RESTORE VALIDATE step added and live-verified 2026-09-23 |
| Postcheck STATUS gate hard-fails on real failure | ✅ Fixed 2026-09-23 — see "Real bugs found" below |

---

## Layer 2 — Agentic scaffold (`Patching_Agentic/`)

Built from [DB_PATCHING_SCOPE.md](../Patching_Agentic/DB_PATCHING_SCOPE.md)'s
7-component POC design. Full component-by-component status lives in
[Patching_Agentic/README.md](../Patching_Agentic/README.md) — this section
is the architecture, that one is the build log.

### Data flow

```
┌──────────────┐     ┌───────────────────┐     ┌──────────────────┐
│   Scanner    │────►│  Operational Store │     │  Static Knowledge │
│ (read-only   │     │     (Postgres)     │     │  Store (Qdrant)   │
│  ansible)    │     │  per-DB state      │     │  procedure objects│
└──────┬───────┘     └─────────┬─────────┘     └─────────┬─────────┘
       │                       │                          │
       │              ┌────────▼──────────────────────────▼───────┐
       │              │            Patch Agent (LLM)                │
       │              │  schema-gated: procedure_id constrained to  │
       │              │  registry enum + re-checked after parsing   │
       │              └────────────────────┬─────────────────────┘
       │                                    │ PatchPlan (JSON, schema-valid)
       │                                    ▼
       │                        ┌───────────────────────┐
       │                        │  Procedure Registry     │
       │                        │  allow-list, playbook   │
       │                        │  paths verified at load │
       │                        └───────────┬────────────┘
       │                                    │ resolved procedure
       │                                    ▼
       │                        ┌───────────────────────┐
       └───────────────────────►│        Executor          │
                                 │ registry lookup          │
                                 │ → real dry-run            │
                                 │   (opatchauto -analyze,   │
                                 │    not ansible --check)   │
                                 │ → guaranteed restore point│
                                 │   (create only, never     │
                                 │    auto-restore)           │
                                 │ → Human Approval Gate      │
                                 │   (blocking, typed confirm)│
                                 │ → apply_playbook (layer 1) │
                                 │ → postcheck / validate      │
                                 │ → on failure: print manual  │
                                 │   rollback info, STOP        │
                                 └───────────┬───────────────┘
                                             │ every step
                                             ▼
                                 ┌───────────────────────┐
                                 │   Audit Log (hash-       │
                                 │   chained, append-only)  │
                                 └───────────────────────┘
```

### Component map

| Component | Files | What it actually does |
|---|---|---|
| Scanner | `agents/scanner/scan_playbook.yml`, `scanner.py` | Read-only Ansible playbook + Python wrapper; parses `ansible-playbook`'s JSON callback output into a structured dict (OS, OPatch/lsinventory, listener, per-CDB open mode/role/flashback/DG lag, CRS version if GI) |
| RAG — ingestion | `rag/ingestion/` | Structured knowledge objects (preconditions/steps/validation/rollback, not raw prose) → Qdrant, via a pluggable embedder |
| RAG — retrieval | `rag/retrieval/retrieve.py` | Vector search + category filter + **client-side version-range filter** (target version compared against each object's applicable min/max) |
| Operational store | `rag/ingestion/operational_store.py`, `postgres_schema.sql` | Per-target `database_state` (last scan, last patch level/date) + `execution_history` |
| Patch Agent | `agents/patch/patch_agent.py` | OpenAI-compatible client against a local LLM. Two independent enforcement layers: (1) JSON-schema `enum` built from the live registry, so the model can't even select an invalid `procedure_id`; (2) the parsed response is re-checked against the registry object directly. Fails closed (`PatchAgentEscalationError`) after N invalid attempts — never relaxes validation to force a parse |
| Procedure Registry | `registry/procedures/` | Pydantic schema + loader. **Every playbook path is existence-checked at load time** — one dangling reference fails the entire registry load, not just that entry |
| Executor | `executor/` | Orchestrates registry lookup → dry-run → snapshot → approval → apply → validate. Every branch unit-tested (`tests/test_executor.py`) with ansible/snapshot/approval calls monkeypatched out |
| Snapshot | `executor/rollback/snapshot.py` | Creates an RMAN **guaranteed restore point** before apply — creation only, never restore. New, unreviewed automation (ad hoc `ansible` command, not a checked-in playbook) — deliberately not treated as equivalent to the human-reviewed layer-1 playbooks yet |
| Approval Gate | `approval/report_builder.py` | Builds a text report (risk level, missing preconditions, warnings) and blocks on the approver typing the exact `target_version` |
| Audit Log | `audit/logger.py` | Hash-chained JSON-lines, `~/.oracle_patching/agentic_audit.jsonl` — **separate file** from layer 1's own per-invocation audit log (`~/.oracle_patching/audit.log`); this one logs the agentic pipeline's own steps (scan/retrieval/recommendation/approval/dry-run/snapshot/execution/validation) |
| CLI | `run_patch.py` | Wires all of the above together |

### The one design decision that was changed before building

`DB_PATCHING_SCOPE.md`'s original Executor spec said a validation failure
"triggers rollback automatically." This directly contradicted layer 1's
design decision #4 ("rollback is never automated"). **Resolved in favor of
the existing rule**: the Executor creates a restore point automatically
(safe, additive, drop-if-unused) but on any failure it stops and hands a
human the printed rollback procedure — it never restores or rolls back
anything itself. Locked in by
`tests/test_executor.py::test_apply_failure_surfaces_rollback_info_and_never_auto_rolls_back`.

### Validation status

| Path | Status |
|---|---|
| Registry allow-list enforcement (dangling playbook path, unknown precondition, id/filename mismatch) | ✅ Unit-tested, real assertions |
| Audit hash-chain integrity + tamper/deletion detection | ✅ Unit-tested |
| Executor orchestration (all 7 branches: unknown procedure, version out of range, dry-run failure, snapshot failure, rejected approval, apply failure→rollback surfaced, full success) | ✅ Unit-tested with real registry, mocked ansible/snapshot/approval |
| RAG ingestion + version-aware retrieval | ✅ Smoke-tested against a real local Qdrant container. **Production `oracle_procedures` collection now populated (2026-09-22)** with 6 real, sourced knowledge objects covering OPatch/RAC/ASM/Data Guard/RMAN (`rag/ingestion/sample_knowledge/`, each citing a real Oracle doc or practitioner source) — real semantic search verified with `SentenceTransformerEmbedder` (not the placeholder): a RAC question correctly surfaced the RAC object first, an RMAN question the RMAN object first. Still a small, hand-curated set, not the full knowledge base `DB_PATCHING_SCOPE.md` describes (see docs/OPEN_QUESTIONS.md for update cadence) |
| Postgres operational store | ✅ Smoke-tested against a real local Postgres container |
| Scanner (`scan_playbook.yml`) | ✅ **Run against the live vagrant VM (2026-09-22)** — correctly returned OPatch version, lsinventory, listener status, and per-CDB state (`READ WRITE`/`OPEN`/`PRIMARY`/flashback off/no DG lag) matching known VM state. Found and fixed a real bug in the process (see below) |
| Patch Agent's actual LLM call | ✅ **Ran against a real local model (2026-09-22)** — Ollama + `qwen2.5:3b-instruct`, real `/v1/chat/completions` call with `response_format: json_schema`. The model correctly picked `oracle_19c_ru_patch` from the registry-derived `enum` (not invented). Semantic quality is weak as expected from a 3B model with no retrieval context (`target` field came back as the target_type value instead of the hostname, version numbers were guessed) — this does not compromise safety: `run_patch.py` routes execution through the operator-supplied `RunContext`, never `plan["target"]`, and missing/false preconditions correctly drove the approval report to HIGH risk. Worth re-running on real POC hardware with a larger model once available |
| Executor against a live host | ❌ Never run — blocked on layer 1 having a real patch downloaded (same blocker both layers share) |

### Real bugs found and fixed so far (chronological)

1. `scan_playbook.yml`: `default(omit)` inside a nested `set_fact` dict
   literal doesn't actually omit the key — `omit` only works as a module
   arg's *entire* value. Caught by rendering through a real `Templar` before
   ever touching a live host. Fixed to `default('')`.
2. `scanner.py`: `subprocess.run(env={...})` replaced the whole child
   environment instead of extending it, which would have broken
   `PATH`/`HOME`. Fixed to `{**os.environ, ...}`.
3. `rag/ingestion/ingest.py`: Qdrant point IDs must be uint/UUID, not the
   knowledge object's human-readable string id. Fixed with a deterministic
   `uuid5` mapping.
4. `ansible/inventories/vagrant_test/group_vars/oracle_db_hosts.yml`:
   `ansible_ssh_private_key_file` was `{{ playbook_dir }}/../vagrant/...` —
   correct for `ansible/playbooks/*.yml` (one level deep from repo root) but wrong
   for `Patching_Agentic/agents/scanner/scan_playbook.yml` (three levels
   deep), so `playbook_dir` resolved to the wrong base and SSH failed with
   "no such identity." Only surfaced when actually connecting to the live
   VM through the new playbook's location — fixed to `{{ inventory_dir }}`,
   which is stable regardless of which playbook is running.
5. `scan_playbook.yml`'s per-CDB SQL task: a `printf "...v\\$database..."`
   one-liner let the shell expand `$database`/`$instance`/etc. to empty
   *before* `printf` or `sqlplus` ever saw them, silently truncating every
   query (`ORA-00911: invalid character`). Only caught by running against a
   live CDB — no static check would have found this, since it's shell-level
   expansion, not Jinja or YAML. Fixed with a quoted heredoc (`<<'SQL'`),
   which disables shell expansion inside the body entirely.
6. `postcheck.yml` queried `DBA_REGISTRY_SQLPATCH`, which only ever reports
   the status of the *currently connected container* — with this project's
   plain `/ as sysdba` connection (no `ALTER SESSION SET CONTAINER`), that's
   `CDB$ROOT` only. It was structurally blind to every PDB, not merely the
   already-known "informational-only, `debug`-not-`failed_when`" gap it was
   found while fixing. Switched to `CDB_REGISTRY_SQLPATCH` (has a `CON_ID`
   spanning every container) joined to `v$pdbs`, and replaced the `debug`
   with a hard `assert` per container. Verified via real `Templar` across 3
   cases and live against the VM before being considered fixed — same
   discipline as every other bug in this list.

Pattern worth naming: **every bug found so far was invisible to
`--syntax-check` and `Templar` rendering** — they only surfaced by actually
connecting to and querying a live target. This is why STATUS.md and this
document both insist on the "structurally wired ≠ proven" distinction
rather than treating a clean syntax-check as validation.

---

## Local dev environment (this machine, snapshot)

- **Vagrant/QEMU VM** (`vagrant/`): `CDB1`/`PDB1`, Oracle Linux 8.10, 19c —
  arm64 host running the x86_64 guest under QEMU TCG emulation (no hardware
  acceleration). SSH port is **not stable** across `vagrant up` runs —
  always re-check with `vagrant ssh-config` before assuming
  `ansible/inventories/vagrant_test/hosts.yml`'s `ansible_port` is current. The
  Oracle instance and listener do **not** auto-start on VM boot
  (`/etc/oratab` has `CDB1:...:N`) — must be started manually each time the
  VM comes up (`startup;` via `sqlplus / as sysdba`, then
  `lsnrctl start LISTENER`).
- **Qdrant + Postgres** (`Patching_Agentic/docker-compose.yml`): local
  containers, `POSTGRES_PASSWORD` via env var, never committed.
- **Ollama**: installed via Homebrew, running as a background service
  (`brew services start ollama`) on `localhost:11434`. Model pinned to
  `qwen2.5:3b-instruct` (1.9GB) rather than the originally-configured 7B
  model — **this machine has 8GB total RAM** with 4GB reserved for the
  vagrant guest, nowhere near `DB_PATCHING_SCOPE.md`'s assumed 64GB POC
  server. Revisit model size on real POC hardware.

---

## What would make this production-relevant (not there yet)

Both layers converge on the same real blocker: **no real CPU/RU patch has
been downloaded**, so `cpu_patch_apply.yml` — and therefore the agentic
Executor's apply step — has never run against anything. Everything else
documented as "✅" above is real, exercised code, not a mock — but none of
it has touched the one step (`opatchauto apply`) that actually changes a
database. See STATUS.md "Next Actions" for the current priority order.

---

## Changelog

- 2026-09-22T16:37:52+05:30 — Initial version — full architecture snapshot covering both layers, data flow, component map, validation status, and the 5 real bugs found so far — Arvind Regukumar
- 2026-09-22T16:49:41+05:30 — Updated path references for the new ansible/ subdirectory (playbooks/roles/inventories/vars moved out of the repo root, grouped alongside Patching_Agentic/, docs/, vagrant/) — Arvind Regukumar
- 2026-09-22T17:39:21+05:30 — Documented the production Qdrant collection now being populated with real, sourced knowledge objects (real semantic retrieval verified) — Arvind Regukumar
- 2026-09-23T00:59:18+05:30 — Refreshed against 2026-09-23's real code changes, found stale via a full-repo markdown audit: added backup_check.yml to the task-flow diagram (it existed in code since 2026-09-22 but was never added here), corrected postcheck.yml's description from dba_registry_sqlpatch to cdb_registry_sqlpatch + noted it now hard-fails, flipped the Backup-verification-gate validation-status row from "does not exist yet" (false — it existed since 2026-09-22) to built/verified, added a Postcheck-hard-fail row, and added bug #6 (the DBA_REGISTRY_SQLPATCH→CDB_REGISTRY_SQLPATCH fix) to the bug list — Arvind Regukumar
