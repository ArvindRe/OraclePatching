# Oracle DB Patching Automation — Phase 1 Scope

> Context file for Claude Code. This defines what to build for the Phase 1 POC.
> EBS is explicitly out of scope for Phase 1 — see "Out of Scope" below.

## Goal

Build an on-premises, air-gapped agent system that automates Oracle Database
patching (CPU/RU) on Linux, using RAG-grounded knowledge and a strict
human-approval gate before any destructive action. The LLM proposes; it
never executes directly.

## Phase 1 Scope (this build)

- **Platform:** Linux only (Oracle Linux 8/9). No Windows.
- **Target:** Oracle Database 19c, single-instance and RAC, on Exadata/ASM.
  Data Guard-aware (must check primary/standby sync before proposing a patch).
- **Application tier:** none. Database only — **EBS 12.2 is explicitly
  excluded from Phase 1.** No ADOP, no Online Patching, no WebLogic/Concurrent
  Manager awareness, no application-tier stop/start. Add this in a later
  phase as its own knowledge collection and its own agent — do not fold it
  into the DB Patch Agent now.
- **Test scope:** 3-5 representative non-prod/sandbox databases, not the
  full fleet and not the 430TB database.

## Core Design Principle

The LLM never has execution authority. Every agent output is a structured,
schema-validated object referencing a pre-vetted procedure ID. The Executor
looks up the real script/playbook by that ID — it never runs LLM-generated
shell, SQL, or PowerShell text directly.

## Components to Build (Phase 1)

### 1. Environment Scanning Agent (read-only)
Collects, as structured JSON, the ground truth for a target:
- OS version, kernel version
- Oracle Home path(s), installed patch inventory (`opatch lsinventory`)
- Current PSU/CPU/RU level
- CRS/ASM version (if RAC)
- Data Guard role and sync status (if applicable)
- DB open mode, instance status, listener status

This output is the single source of truth every other component reads from —
no other agent re-derives it independently.

### 2. Knowledge Base (RAG)
- **Static store (Qdrant):** Oracle 19c / RAC / ASM / Data Guard / RMAN /
  OPatch documentation, oracle-base.com content, internal runbooks. Ingested
  as **structured procedure objects** (preconditions / steps / validation /
  rollback), not raw chunked prose. EBS documentation is not ingested in
  Phase 1.
- **Operational store (Postgres):** per-database state — last patch level,
  last successful patch date, known issues, Data Guard status. Updated after
  every scan and every execution.
- **Ingestion:** monthly, via a one-directional DMZ transfer from a
  connected staging machine. The production server has no direct internet
  access.
- **Content selection criteria** — what actually gets curated onto the
  staging machine and transferred, not just "relevant Oracle docs":
  1. **In-scope domain only.** Must map to one of the six named domains
     above (19c / RAC / ASM / Data Guard / RMAN / OPatch) and to a
     version this project targets (19c). Never EBS, never a different DB
     version, regardless of how good the content is.
  2. **Tied to something this codebase actually does or has flagged as a
     gap**, not generic background reading. Preference order: (a) explains
     a mechanism the automation directly relies on (e.g. `opatchauto -analyze`
     as the real dry-run — `executor/ansible_runner.py` depends on this
     being accurate), (b) closes a gap already named in `STATUS.md`/
     `docs/OPEN_QUESTIONS.md` (e.g. RMAN backup validation, RAC rolling
     order — both existed as flagged gaps before the matching knowledge
     object did), (c) general reference otherwise. A knowledge base that's
     encyclopedic but not actionable during a real patch run isn't the goal.
  3. **Source credibility, tiered.** Prefer an official `docs.oracle.com`
     page with a real citation. Where official docs are thin (common for
     patching mechanics — Oracle's own docs describe *what* commands do,
     rarely *why* an ordering or precondition matters), cross-check 2+
     independent practitioner sources rather than trusting one blog post.
     Never ingest a claim with no traceable source — every knowledge
     object's `source` field must name where it actually came from, not
     "general knowledge."
  4. **Decomposes honestly into preconditions/steps/validation/rollback.**
     If a source is pure narrative that can't be split that way without
     distorting it, either restructure carefully by hand (verifying nothing
     was lost) or don't ingest it as-is — don't force-fit prose into the
     schema just to have more objects.
  5. **Never reproduce licensed Oracle content verbatim** — patch READMEs,
     MOS note text, etc. Summarize and cite; don't copy.
- **What to monitor for new releases** — verified against Oracle's own
  sources, not assumed: the **public, no-MOS-login** page
  [oracle.com/security-alerts](https://www.oracle.com/security-alerts/) is
  Oracle's official Critical Patch Update advisory index — CPUs (which
  include the quarterly Database RU) are published there on the third
  Tuesday of January, April, July, and October, with an email subscription
  option. This is the right thing for a staging machine to poll/subscribe
  to for "has a new quarterly RU shipped" *without* needing MOS access at
  all — only the actual patch *download* is MOS-gated, not knowing that one
  exists. Once MOS access exists, the DBA-facing patch-number lookup is MOS
  Note 1454618.1 ("Quick Reference to Patch Numbers for Database PSU,
  SPU(CPU), Bundle Patches and Patchsets") — the master index this
  project's own knowledge objects should track, not something to duplicate.
  `oracle-base.com`'s "Patching: Find the Required Patches for Oracle
  Products" is a solid public supplementary reference (no MOS needed)
  already used informally to source this repo's own knowledge objects.

### 3. Patch Agent
Retrieves the applicable procedure from RAG, compares against the scan
output, and proposes a plan. Output is a schema-validated object, e.g.:

```json
{
  "procedure_id": "oracle_19c_ru_patch",
  "target": "PROD01",
  "current_version": "19.28",
  "target_version": "19.29",
  "preconditions_checked": {
    "rman_backup_available": true,
    "archivelog_backup_available": true,
    "oracle_home_verified": true,
    "opatch_version_verified": true,
    "disk_space_verified": true,
    "data_guard_synchronized": true
  }
}
```

Never emits raw shell/SQL — only references to procedure IDs in the
registry below.

### 4. Procedure Registry
An allow-list of vetted procedures (e.g. `oracle_19c_ru_patch`,
`oracle_19c_rac_ru_patch`). Each entry maps to a real, human-reviewed
Ansible playbook or script. The Patch Agent can only reference IDs that
exist here — it cannot introduce a new procedure at runtime.

Build this on top of the existing `OraclePatching` repo
(precheck / apply / rollback-info / confirm-gate playbooks) — extend it,
don't replace it.

### 5. Executor
- Looks up the playbook by procedure ID from the registry.
- Runs Ansible `--check` (dry-run) first — mandatory, not optional.
- On dry-run success, takes an automatic RMAN restore point / snapshot
  before the real run.
- Executes the real playbook.
- Runs the post-patch validation checks.
- On validation failure, triggers rollback automatically.

### 6. Human Approval Gate
Blocking step between plan generation and execution. Presents an
observability report (pre-check results, actions, estimated downtime, risk
level) and requires explicit confirmation. No automated bypass. Approver
identity and timestamp are recorded in the audit log.

### 7. Audit Log
Append-only / hash-chained. Every scan, RAG retrieval, recommendation,
approval, dry-run result, execution output, and validation result is logged.

## Out of Scope (Phase 1)

- **EBS 12.2** (application tier, ADOP, Online Patching, WebLogic,
  Concurrent Manager, ISG, REST/SOAP) — defer entirely to a later phase.
- Windows platform / PowerShell / WinRM agent.
- Full fleet rollout (300+ databases) — Phase 1 targets 3-5 sandbox DBs only.
- Learning-loop / execution-history recommendations — can be added once the
  core loop (scan → plan → approve → execute → validate → audit) is proven.

## Suggested Repo Structure

```
oracle-db-patching-poc/
├── agents/
│   ├── scanner/          # environment scanning agent
│   └── patch/            # patch agent (proposes only)
├── rag/
│   ├── ingestion/        # DMZ import + structured object parser
│   └── retrieval/        # version-aware query against Qdrant
├── registry/
│   └── procedures/       # allow-listed procedure definitions
├── executor/
│   ├── ansible/          # extends existing OraclePatching playbooks
│   └── rollback/         # snapshot/restore-point capture
├── approval/
│   └── report_builder.py # observability report generation
└── audit/
    └── logger.py          # append-only audit trail
```

## Hardware (single-server POC)

12+ core CPU, 64GB RAM, 12GB GPU, 2TB NVMe, Linux host, Docker/Podman.
Local LLM: Llama 3.1 8B or Qwen2.5-7B, quantized (Q4/AWQ) — sufficient
given the schema-gated design keeps the LLM's job narrow.

---

## Changelog

- 2026-09-22T15:28:39+05:30 — Initial version — Phase 1 POC scope for the agentic patching layer — Arvind Regukumar (timestamp from filesystem mtime; file predates git tracking and this project's per-file changelog convention)
- 2026-09-22T15:53:48+05:30 — Brought under the per-file changelog convention; see [README.md](README.md) for the review findings (auto-rollback conflict resolved in favor of CLAUDE.md design decision #4) and build status against this scope — Arvind Regukumar
- 2026-09-22T21:01:02+05:30 — Added explicit content-selection criteria for the Knowledge Base (formalizing what the 6 existing knowledge objects in rag/knowledge_staging/ actually followed) and verified, cited sources for tracking new patch releases — the public oracle.com/security-alerts CPU advisory page (no MOS login needed, quarterly on the third Tuesday of Jan/Apr/Jul/Oct) for knowing a release exists, MOS Note 1454618.1 for the DBA-facing patch-number lookup once MOS access exists — Arvind Regukumar

