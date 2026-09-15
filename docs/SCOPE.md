# Project Scope

## What this is

An Ansible framework for automating the repeatable parts of Oracle Database
maintenance, built to be reused across multiple client sites rather than
tied to one environment. v1 covers exactly one workflow end to end:
**quarterly CPU/PSU (Release Update) patching of Oracle 19c CDBs via
OPatch/opatchauto.**

## In scope — v1

- Single-instance and Oracle Restart (non-RAC) 19c CDB databases on Oracle
  Linux, patched via `opatchauto` (recommended path) or manual `opatch
  apply` + explicit stop/start (fallback path, `use_opatchauto: false`).
- Patch staging (copy zip to host, unzip, conflict analysis) as a
  read-only-safe step, separated from the state-changing apply step.
- `datapatch` execution per CDB (covers CDB$ROOT + all open PDBs
  automatically — no per-PDB looping needed for the SQL-level patch).
- Verification: `opatch lsinventory` + `dba_registry_sqlpatch` status per
  CDB.
- A hard, patch-number-specific confirmation gate before anything
  state-changing runs.
- A printed (not automated) manual rollback procedure.
- Multi-client inventory isolation (one `inventories/<client>/` folder per
  client — see "Multi-client model" below).
- **A mandatory local audit log entry for every invocation** — see
  "Audit logging" below. Not optional, not tag-gated: every run of
  `roles/oracle_cpu_patch`, success or failure, precheck-only or full
  apply, writes exactly one entry.

## Explicitly out of scope — v1

These are real gaps, not oversights — each is a candidate for a later
phase in [ROADMAP.md](ROADMAP.md), called out here so nobody mistakes v1
for a finished product:

| Gap | Why it matters | Planned phase |
|---|---|---|
| RAC / multi-node coordination | `opatchauto` *can* patch a RAC cluster from one node, but that path is unverified here — v1 assumes single-instance or Oracle Restart | v2 |
| Grid Infrastructure-integrated patches | `grid_home` exists as an inventory var but no GI-specific task path has been built or tested | v2 |
| Backup verification before patching | Nothing here checks an RMAN backup exists before `apply_patch.yml` runs | v2 |
| Automated OPatch version remediation | precheck *warns* if OPatch is stale; it doesn't apply the OPatch updater patch (p6880880) itself | v2 |
| Real-environment testing | Validated so far via YAML parsing, `ansible-playbook --syntax-check`, and `ansible.template.Templar` unit tests of the trickier Jinja expressions — **never run against a live 19c database** | v2 |
| Linux OS patching (yum/dnf, kernel, reboot orchestration) | Separate concern from DB-level patching, deliberately deferred out of v1 per scoping decision | v3 |
| Windows service start/stop | Different connection model entirely (WinRM, not SSH) | v4 |
| Central/queryable audit logging (SIEM, change-ticket integration) | v1's audit log (see below) is a local JSON-lines file, not a queryable central store or a change-ticket gate | v5 |
| CI (lint, syntax-check on every commit) | Currently run manually | v5 |
| Secrets manager integration | v1 needs no DB password (OS-authenticated `/ as sysdba`); a client with OS auth disabled needs a vault-based path not yet built | v5 |

## Audit logging

**Every invocation of `roles/oracle_cpu_patch` writes exactly one audit log
entry — this is not optional and does not depend on tags.** Wired into
`main.yml` via `block`/`rescue`/`always`, so it fires whether the run
succeeds, fails partway, or is refused at the confirm gate.

- **Where:** `audit_log_path` (default `~/.oracle_patching/audit.log` on
  the Ansible control node — override per client/CI environment if that
  default doesn't fit).
- **Format:** one JSON object per line (JSON Lines) — `timestamp`
  (target host's own clock, ISO 8601), `operator` (control-node `$USER`),
  `host`, `patch_id`, `patch_description`, `oracle_home`, `cdbs` (list of
  SIDs), `run_mode` (`full_apply` / `precheck_only`), `outcome`
  (`SUCCESS` / `FAILED`), `error` (populated only on failure).
- **What it is not:** a central/queryable store, a SIEM feed, or a
  change-ticket gate — those are v5 (see the scope table above and
  `ROADMAP.md`). It is the minimum "who ran what, when, against which
  host, with what outcome" record, kept locally, durable across runs.
- **Never assume Oracle's own logs are enough on their own** —
  `opatchauto`/`opatch`/`datapatch` all write their own detailed session
  logs under `cfgtoollogs/` on the *target* host regardless of this audit
  log; this entry exists specifically so a human doesn't have to SSH into
  every target host and grep timestamps to answer "did anyone patch this
  CDB, and did it work?"

## Assumptions and prerequisites

- Target hosts run Oracle Linux (or RHEL-compatible) with a 19c Oracle
  Database CDB already installed — this project patches an *existing*
  install, it does not provision one.
- OS authentication (`sqlplus / as sysdba` from the DB host, no password)
  is enabled. If a client site disables this, `postcheck.yml`/
  `datapatch.yml`/`stop_services.yml`/`start_services.yml` need a
  password-based connection path added first.
- The Ansible control node has SSH key-based access to every DB host, and
  the SSH user can `become` to both `oracle` (opatch/datapatch/sqlplus)
  and `root` (opatchauto).
- The patch zip is downloaded from Oracle support **by a human** ahead of
  time — this project stages and applies a patch already on disk
  (`patch_zip_local_path`), it does not download from Oracle.
- One `ORACLE_HOME` serves the CDB(s) listed in `cdbs:` for a given patch
  run — the binary patch applies once per home; `datapatch` then runs once
  per CDB against that home.

## Multi-client model

Each client gets its own `inventories/<client>/` directory (hosts +
group_vars) copied from `inventories/example_client/`, keeping host lists
and any client-specific overrides from bleeding across engagements. Patch
definitions (`vars/patches/<patch_id>.yml`) are shared across clients when
the patch number is the same — the host list, not the patch content, is
what's client-specific. See [ROADMAP.md](ROADMAP.md) phase 5 for where
this model needs to firm up further (secrets isolation, audit logging per
client) before it's genuinely multi-tenant-safe.

## Risk notes

- **This automates production database downtime.** `apply_patch.yml`
  stops a listener and/or shuts down a database instance. The confirmation
  gate and `block`/`rescue` diagnostics reduce operator error and improve
  failure visibility; they do not eliminate the inherent risk of patching
  a production system. Always run `cpu_patch_precheck.yml` and review its
  output — including the conflict analysis — before ever passing
  `confirm_patch`.
- **Never tested against a live database as of this writing.** Treat v1 as
  validated-by-construction (correct Ansible/Jinja mechanics, confirmed via
  syntax-check and direct Templar testing) but **not yet validated against
  real Oracle behavior**. Phase 2 of the roadmap exists specifically to
  close that gap before this touches a real client system.

---

## Changelog

- 2026-09-15T23:04:48+05:30 — Initial version — v1 in/out-of-scope table, assumptions, risk notes — Arvind Regukumar
- 2026-09-16T00:12:11+05:30 — Added "Audit logging" section; moved audit logging out of the out-of-scope table — Arvind Regukumar
- 2026-09-16T00:24:55+05:30 — Removed "(with Claude Code)" suffix from the author field per project convention — Arvind Regukumar

