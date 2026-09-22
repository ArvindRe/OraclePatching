# Open Questions

> Working list of unresolved design/operational questions, as of 2026-09-22.
> Not a decision record — see `CLAUDE.md` "Key Design Decisions" for things
> already settled. This file is for things that are genuinely still open.

---

## 1. Should we stick with Qdrant for the RAG store?

**Current state:** Qdrant is built and working — real ingestion/retrieval,
smoke-tested against a live container today (`Patching_Agentic/tests/test_rag_smoke.py`,
and `demo.py` step 2). Nothing here is broken; this is a "is it the right
long-term choice" question, not a bug report.

**The case for switching to `pgvector` on the existing Postgres instance
instead:**

- `DB_PATCHING_SCOPE.md`'s hardware section explicitly frames this as a
  **single-server POC**. The operational store already requires Postgres
  (`rag/ingestion/postgres_schema.sql`) — running Qdrant *as well* means two
  database services to install, patch, secure, and back up on a box that,
  by design, has no outside internet access to pull updates from. One fewer
  service is a real simplification for exactly the environment this is
  meant to run in.
- Data volume here is a knowledge base of structured procedure objects —
  realistically dozens to low hundreds of entries, not millions of
  documents. Qdrant's specialized ANN indexing exists to make similarity
  search fast at large scale; at this scale, `pgvector`'s `ivfflat`/`hnsw`
  indexes (or even a brute-force scan) are more than adequate. The
  performance argument for a dedicated vector DB doesn't apply here.
- The operational store (`database_state`, `execution_history`) and the
  knowledge store would live in the same database, transactionally
  consistent, one connection pool, one backup job.

**The case for keeping Qdrant:**

- It's already built, tested, and proven against a live container today —
  switching has a real (if small) migration cost for no functional gain
  right now.
- Qdrant's filtering/payload query API is more expressive out of the box
  than hand-rolling filtered vector search in SQL; `rag/retrieval/retrieve.py`
  already leans on this a little (though it does its version-range
  filtering client-side, not via Qdrant's filter DSL, specifically because
  version strings don't sort correctly as plain strings in a payload range
  filter — see that file's docstring).

**Recommendation:** keep Qdrant for now (it works, don't destabilize a
working POC over an architectural preference), but treat `pgvector`
migration as the likely right move **before** this goes to a real
single-server air-gapped deployment — the "fewer services on a box you
can't easily patch" argument is the strongest one here and gets stronger,
not weaker, once this is off a developer's laptop.

---

## 2. How do we handle failures?

Current behavior, by pipeline stage — what's real (tested or run live
today) vs. what's a gap:

| Stage | Current behavior | Status |
|---|---|---|
| Scan | `ScanFailedError`, clean message, pipeline stops | ✅ Handled, run live today |
| RAG retrieval (Qdrant unreachable) | None — raw `qdrant_client` exception propagates uncaught | ❌ **Gap** (`demo.py`'s Step 0 preflight now checks Qdrant reachability before the pipeline starts, but `propose_plan`/`retrieve` itself still has no try/except of its own) |
| LLM call (Ollama unreachable or stuck) | ~~Gap~~ **Fixed 2026-09-22, from a real incident**: a Ctrl-C'd `demo.py` run orphaned a stuck generation on Ollama's single request slot (`-np 1`), which then hung every subsequent call — the `OpenAI` client had no timeout, so nothing would have caught it. Now: `client = OpenAI(timeout=llm.timeout_seconds, max_retries=0)`, and `openai.APITimeoutError`/`APIConnectionError` are caught and turned into a clean `PatchAgentEscalationError` naming the likely cause. `demo.py`'s Step 0 also probes `/api/version` with a short timeout before anything else runs | ✅ Fixed and verified live (killed Ollama, confirmed preflight fails in <1s with a clear message instead of hanging) |
| Registry lookup (unknown `procedure_id`) | `UnknownProcedureError` → `ExecutorHaltedError`, audit-logged | ✅ Handled, run live today |
| Version-range check | ~~`ExecutorHaltedError`, audit-logged~~ **Was actually a crash bug until 2026-09-22**: a malformed `target_version` (real observed case: the LLM returned `"19c"`) made `_version_in_range`'s bare `int()` call raise an unhandled `ValueError` — no audit entry, no clean halt, just a traceback. This directly violated the "always fail closed" premise this whole table describes. Fixed: parsing failures are now caught and routed through the same `ExecutorHaltedError` + audit path as an out-of-range version | ✅ Fixed and unit-tested (`test_malformed_version_halts_cleanly_instead_of_crashing`) |
| Dry-run / precheck | Real Ansible subprocess failure → `ExecutorHaltedError`, audit-logged | ✅ Handled, run live today |
| Snapshot (restore point) creation | `SnapshotResult.ok` check → `ExecutorHaltedError`, audit-logged | ⚠️ Unit-tested only — **never actually reached against the live VM yet** (every live run so far has halted at the dry-run gate first, before snapshot). Real behavior of `GUARANTEE FLASHBACK DATABASE` against this VM's small disk (5GB `min_free_space_gb`) is unknown |
| Human approval rejected | `ExecutorHaltedError`, audit-logged | ✅ Handled, unit-tested |
| Apply failure | Prints rollback info, halts, **never** auto-rolls back | ✅ Handled, unit-tested — this is the core safety property |
| Postcheck / validation | ~~`dba_registry_sqlpatch` STATUS check in `postcheck.yml` is informational-only~~ **Fixed 2026-09-23**: the fix also caught a deeper bug — `DBA_REGISTRY_SQLPATCH` only ever reports the *connected container's* own status (`CDB$ROOT` here, since this project connects with plain `/ as sysdba`), so it was structurally blind to every PDB, not just non-fatal. Switched to `CDB_REGISTRY_SQLPATCH` joined to `v$pdbs`, replaced `debug` with a hard `assert` per container. Verified via Templar (3 cases) and live against the VM. The Executor now correctly sees `apply_result.ok = False` on a real per-container SQL-level failure | ✅ Fixed, see `postcheck.yml`'s own changelog and `docs/REFERENCES_LEARNINGS.md` |
| SSH / network hang | ~~No `timeout=` anywhere~~ **Fixed 2026-09-22**: `ansible_runner.py`, `scanner.py`, and `snapshot.py` now all pass `timeout=settings.subprocess_timeout_seconds` (default 600s) to `subprocess.run()`, catch `TimeoutExpired`, and turn it into an ordinary failed result/`ScanFailedError` — no new exception type needed, it flows through the same halt logic as any other failure | ✅ Fixed, verified via a real timed-out preflight check (VM/Ollama down) |
| Postgres operational store unreachable | No try/except around `upsert_scan_result`/`record_execution` in `run_patch.py` | ⚠️ Still a gap — arguably correct to hard-fail (it's meant to be a reliable record, not optional), but currently surfaces as a raw exception, not a clean message. `demo.py`'s Step 0 preflight checks Postgres too, but non-fatally, since `demo.py` itself never touches it |

**Design principle, stated explicitly rather than left implicit:** there is
**no retry/backoff logic anywhere** in this pipeline. Every failure is
immediately fatal. This is deliberate, not an oversight — a system that can
stop a production database should never blindly auto-retry a stop/apply
operation after a transient blip. Worth keeping this principle written down
so a future "let's add retries for robustness" suggestion gets evaluated
against it explicitly, not added by default.

**Suggested priority if these get worked on:** the LLM/Qdrant connection
gaps first (they'd currently crash ugly instead of escalating cleanly,
which undermines the "fails closed with a clear reason" story the whole
design is built around), then subprocess timeouts. (The postcheck STATUS
gate that used to be listed here is fixed — see the table above.)

**A limitation that's now half-closed, not fully:** `qwen2.5:3b-instruct` is
unreliable at the `current_version`/`target_version` fields, even with a
real, correctly-extracted version string available in `scan_result` to copy
from — one observed run correctly returned `"19.3.0.0.0"`; the very next run
returned `"1.0"` and `"19c"` for the same scan data.

**`current_version` — fixed, not just handled.** `agents/patch/patch_agent.py`
now unconditionally overwrites `current_version` with
`agents.scanner.scanner.extract_db_version(scan_result)` after the LLM
responds, regardless of what it said — the model's own guess for this field
is simply discarded. Verified live: a run where the model said `"1.0"`/`"19c"`
still ended up with the correct `"19.3.0.0.0"` in the plan. This closes the
gap for real, not just gracefully — `current_version` is now always
accurate when `scan_result` has a readable version, and an honest empty
string when it doesn't.

**`target_version` — still open, and can't be closed the same way.** Nothing
in `scan_result` can answer "which patch/version is this upgrading TO" — the
model still has to produce this as a genuine judgment call. What's fixed
(the crash-proofing in `executor.py`, see above) is that a bad
`target_version` now fails safely — verified in the same live run:
`target_version="19c"` hit the version-range gate and produced a clean
`ExecutorHaltedError`, not a crash. Closing this gap for real (not just
safely) needs either a larger/better model (see `config/settings.yaml`'s
note on this dev machine's 8GB RAM being the constraint) or sourcing
`target_version` from the specific patch's own metadata once a real patch
exists (`ansible/vars/patches/<patch_id>.yml` — not built yet either, see
question 4 above) rather than asking the LLM to infer it from context.

---

## 3. How do we download patches for testing without a commercial account?

**Short answer: there isn't a legitimate way around this — it's a
licensing constraint, not a technical one.** Oracle CPU/RU patches are
distributed exclusively through My Oracle Support, which requires an
account tied to an active support contract (CSI). This is different from
the plain Oracle account used to download the DB software itself (free,
OTN license) — patches specifically are support-contract-gated.

**Real options, in order of legitimacy:**

1. **An employer's or organization's existing support contract.** Worth
   checking whether Arvind's employer has one, even if his personal MOS
   login doesn't currently have access — this is the most common real path
   for someone in this situation.
2. **A colleague with MOS access downloads it on your behalf.** Legitimate
   only when that person is themselves covered by an active support
   contract doing a normal download for legitimate use — not redistribution
   to someone outside that agreement. Treat this as "ask a DBA colleague,"
   not "find a copy online."
3. **Do not use third-party/unofficial mirrors of Oracle patch zips.**
   Explicitly not recommended — likely a license violation, and the binary
   is unverifiable (no way to confirm it's what Oracle actually shipped).
4. **Not a real substitute:** Oracle occasionally publishes a full,
   RU-integrated installer image on the public OTN download page for a
   *new install* (not through MOS). Even if one exists for a version this
   project could use, applying a full reinstall exercises completely
   different mechanics than `opatchauto`/`opatch`/`datapatch` — it would
   not validate this project's actual patch-application code paths. Don't
   treat "we can get *a* newer Oracle install" as equivalent to "we proved
   the patching playbooks."

**In the meantime:** keep proving everything up to (but not including) the
actual apply step — which is what `Patching_Agentic/demo.py` and the base
repo's `cpu_patch_precheck.yml` already do. `cowork_prompt/download_cpu_patch.md`
has the concrete next step (a specific patch number to search for, 37960098,
once MOS access is sorted) — see that file rather than duplicating it here.

---

## 4. Where will patches actually be staged? Does the DMZ transfer apply to them too?

**This is currently an unaddressed gap, not a decided design.** `ansible/vars/patches/<patch_id>.yml`'s
`patch_zip_local_path` just assumes a patch zip already exists on the
**Ansible control node's** local filesystem — there's no mechanism anywhere
in this repo for how it got there. `cowork_prompt/download_cpu_patch.md`
papers over this by having the same machine (this dev Mac) play both roles
— internet-connected downloader *and* Ansible control node — which works
fine for a POC with direct internet access, but doesn't answer the real
question for the air-gapped production design `DB_PATCHING_SCOPE.md`
describes.

**Is this the same problem as the knowledge-base DMZ transfer
(`rag/ingestion/dmz_transfer.py`, `knowledge_staging/` → `knowledge_inbox/`)?**
Structurally yes — both need "internet-connected machine prepares content →
one-directional transfer → air-gapped side consumes it, never reaches the
network itself." But patch zips differ from knowledge YAML in ways that
matter enough to warrant a **separate transfer channel**, not reuse of the
same one:

- **Size.** Knowledge objects are a few KB each; a patch zip is commonly
  hundreds of MB to low GB. A one-directional transfer mechanism sized for
  small text files (a simple copy + JSON manifest) may not be the right
  shape for this — worth considering whether the real transfer medium here
  is physically different too (write-once media for large files vs. a
  network DMZ hop for small ones).
- **Licensing.** Oracle patch zips are licensed software, not freely
  redistributable reference material — unlike the knowledge base (curated
  from public docs and internal runbooks), a "patch staging" directory
  holding real patch zips should probably never be committed to git at all,
  even gitignored-and-regenerable the way `knowledge_inbox/` is. It should
  live purely as filesystem state on whichever machines need it.
- **Integrity matters more.** A corrupted or tampered patch zip gets
  applied to a production Oracle Home. The knowledge-base transfer's sha256
  manifest proves "this is what was in staging" — for patches, verification
  should go further: check the transferred file's checksum against Oracle's
  own published checksum for that patch (MOS lists one per patch), not just
  a self-consistency check against the staging copy.
- **Cadence** is related but not identical — quarterly RU releases roughly
  track the "monthly ingestion" cadence with room to spare, but a patch
  transfer is also naturally triggered per-patch-download, not purely
  time-scheduled the way knowledge content update might be.

**The bigger unanswered question underneath this:** where does the Ansible
**control node** itself sit? If it's on the air-gapped network (same
constraint as the production box), then `patch_zip_local_path` needs to
point at something that arrived via a one-way transfer, same shape as the
knowledge base. If the control node instead sits in a DMZ with controlled,
audited outbound access to MOS specifically (a narrower exception than
"downloader's laptop"), the design is simpler — no separate transfer
mechanism needed for patches at all, just an access-controlled download
step. This is a network-topology decision that hasn't been made yet, and it
changes the answer significantly — worth resolving before building anything
here, rather than guessing at a mechanism for a topology that might not
match reality.

**Recommendation:** don't build a patch-staging DMZ mechanism yet — figure
out where the control node lives first. If/when that's answered and it
turns out patches do need a one-way transfer, build it as its own
directory/script (not reusing `knowledge_staging/`/`knowledge_inbox/`),
with checksum verification against Oracle's published value as a hard
requirement, and never commit the staged zips themselves to git.

---

## Changelog

- 2026-09-22T17:00:17+05:30 — Initial version — Qdrant vs. pgvector, failure-handling status per pipeline stage, and the patch-download licensing constraint — Arvind Regukumar
- 2026-09-22T17:33:36+05:30 — Marked the LLM-hang and SSH/subprocess-timeout gaps as fixed — a real Ctrl-C'd demo run orphaned a stuck Ollama generation and hung the next call with no timeout to catch it; added real timeouts everywhere and a demo.py preflight step, verified live (killed Ollama, confirmed fail-fast) — Arvind Regukumar
- 2026-09-22T20:06:10+05:30 — Added question 4: where do patches actually get staged, and does the same DMZ transfer pattern built for the knowledge base apply to them too — flagged as a real, currently-unaddressed gap (patch_zip_local_path just assumes the file already exists locally, no mechanism for how) with a recommendation not to build anything until the control node's network topology is actually decided — Arvind Regukumar
- 2026-09-22T20:11:44+05:30 — Corrected the version-range row: it was actually a crash bug (malformed target_version "19c" from the LLM raised an unhandled ValueError), fixed and unit-tested. Added an explicit note that the underlying model-reliability problem (qwen2.5:3b-instruct is inconsistent at current_version/target_version even with real scan data available) is NOT fixed by any of today's error-handling work — only that wrong output now fails safely instead of crashing or being silently trusted — Arvind Regukumar
- 2026-09-22T20:42:49+05:30 — Updated the model-reliability note: current_version is now genuinely fixed (patch_agent.py overwrites it with scan_result's real reading unconditionally, verified live), not just handled-when-wrong. target_version remains open since nothing in scan_result can answer it — Arvind Regukumar
- 2026-09-23T00:54:21+05:30 — Marked the postcheck STATUS-gate row as fixed: switched to CDB_REGISTRY_SQLPATCH + a hard assert (the original DBA_REGISTRY_SQLPATCH query turned out to be blind to every PDB, not just non-fatal), verified via Templar and live against the VM — Arvind Regukumar
