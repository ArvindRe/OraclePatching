name: oracle-cpu-patching
description: >
  **PATCHING SKILL** — Oracle CPU/PSU (quarterly Release Update) patching via
  OPatch/opatchauto. Use for: staging a patch, running conflict analysis,
  applying it against a 19c CDB, running datapatch, verifying via
  dba_registry_sqlpatch, and printing (never executing) a manual rollback
  procedure.
  DO NOT USE FOR: Linux OS patching or Windows service management (not yet
  built — see docs/ROADMAP.md phases 3/4); general Oracle SQL help unrelated
  to patching; anything against a production host without the user's
  explicit, separate confirmation beyond just `confirm_patch`.
applies: "**/*.yml, vars/patches/**, inventories/**"

# Skill: Oracle CPU/PSU Patching Workflow

## Purpose

Guide one quarterly CPU/PSU patch run from precheck through verification
(and, if needed, rollback) using `roles/oracle_cpu_patch`. Each phase
produces specific, reviewable output; gate the next phase on that output
looking right — this is a production-database maintenance operation, not
a script to run start-to-finish unattended.

---

## Phase 0 — Pre-flight: Define the Patch Run

Before touching a host, both files below must exist and be reviewed.

```bash
# Per-client inventory (create once per client, reused across patches)
cp -r inventories/example_client inventories/<client_name>
vim inventories/<client_name>/hosts.yml       # oracle_home, grid_home per host

# Per-patch definition (create once per quarterly patch, reused across clients)
cp vars/patches/EXAMPLE_PATCH.yml vars/patches/<patch_id>.yml
vim vars/patches/<patch_id>.yml               # patch_id, cdbs, patch_zip_local_path
```

**Decision point:** confirm `patch_zip_local_path` actually points at the
downloaded patch zip on the control node — this project stages and applies
a patch already on disk, it does not download one from Oracle.

---

## Phase 1 — Precheck + Stage

Always run this first. It is structurally incapable of changing any
running state — `run_full_patch: false` stops the role before the confirm
gate, no matter what tags are or aren't passed.

```bash
ansible-playbook -i inventories/<client_name>/hosts.yml \
  playbooks/cpu_patch_precheck.yml -e @vars/patches/<patch_id>.yml
```

**What it checks:**
- OPatch version and current patch inventory (warns if this patch already appears applied).
- Free space under `oracle_home`'s filesystem (`min_free_space_gb`).
- Copies the patch zip to the host, unzips it, and runs conflict analysis
  (`opatchauto apply -analyze`, or `opatch prereq CheckConflictAgainstOHWithDetail`
  on the manual path).

**Pass criteria:** PLAY RECAP is clean (`failed=0`); conflict analysis
output shows no blocking conflicts against the target `ORACLE_HOME`.

---

## Phase 2 — Review

**Do not skip this.** Read the conflict-analysis output from Phase 1 in
full. Confirm:
- No other interim patches conflict with this one.
- The patch's documented minimum OPatch version is met (Phase 1 prints the
  current version — cross-check against the patch's README).
- A recent RMAN backup exists (not yet automated — see `docs/SCOPE.md`;
  this is a manual check until Phase 2 of `docs/ROADMAP.md` adds the gate).
- The maintenance window covers the expected downtime (listener + instance
  stop, patch apply, datapatch, restart, verification).

---

## Phase 3 — Apply

Requires the exact patch number as `confirm_patch` — this is deliberate,
not a formality: it means applying the wrong patch to the wrong host
requires actively typing the wrong number.

```bash
ansible-playbook -i inventories/<client_name>/hosts.yml \
  playbooks/cpu_patch_apply.yml -e @vars/patches/<patch_id>.yml \
  -e confirm_patch=<patch_id>
```

**What happens, in order:** precheck + stage again (cheap, re-verifies
nothing changed) → confirm gate → stop services (manual path only —
`opatchauto` handles this itself) → apply (`opatchauto apply` or `opatch
apply`) → start services (manual path only) → `datapatch -verbose` per CDB
→ postcheck.

**On failure:** every apply-side task uses `block`/`rescue` — the failure
message includes the actual OPatch/opatchauto log content, not just a
return code. Read it before deciding anything, including whether to go to
Phase 5.

---

## Phase 4 — Verify

Postcheck runs automatically as part of Phase 3, but re-confirm by hand
before closing the maintenance window:

```sql
-- Each CDB
SELECT patch_id, patch_type, status FROM dba_registry_sqlpatch
WHERE patch_id = <patch_id>;
```

**Pass criteria:** `STATUS = 'SUCCESS'` for every CDB in `cdbs:`. Then:
listener + every instance reachable, monitoring/EM alerting re-enabled, one
application-level smoke test beyond raw SQL*Plus connectivity.

---

## Phase 5 — If Something Goes Wrong: Rollback

Rollback is **never automated** by this project. If Phase 3 fails
partway, or Phase 4's verification doesn't pass, get the exact manual
procedure for this specific patch/host:

```bash
ansible-playbook -i inventories/<client_name>/hosts.yml \
  playbooks/cpu_patch_rollback_info.yml -e @vars/patches/<patch_id>.yml
```

This only prints commands — a human decides whether and when to run them.
Read the failure output from Phase 3 first; a failure during `datapatch`
(SQL-level) needs a different response than one during `opatchauto apply`
(binary-level).

---

## Quick Reference: Playbook → Phase Mapping

| Playbook | Phase |
|----------|-------|
| `playbooks/cpu_patch_precheck.yml` | 1 — Precheck + stage |
| `playbooks/cpu_patch_apply.yml` | 3 — Apply (includes 1 again, then 4) |
| `playbooks/cpu_patch_rollback_info.yml` | 5 — Rollback (informational only) |

---

## Key Constraints and Gotchas

- **`run_full_patch: false` is what makes precheck safe** — not `--tags`. A role's `tags:` key adds tags, it doesn't filter execution. Don't try to build a new "safe" entry point by tagging alone.
- **`confirm_patch` must equal `patch_id` exactly.** A generic `-e confirm_patch=yes` will not work and is not meant to.
- **`opatchauto` runs as `root`; `opatch`/`datapatch`/`sqlplus`/`lsnrctl` run as `{{ oracle_os_owner }}`** (default `oracle`) — this is per-task `become_user`, not a global setting.
- **No database password anywhere** — connections are OS-authenticated (`/ as sysdba`, local to the DB host). A client site with OS auth disabled needs a new code path, not a quiet workaround.
- **This has never been run against a live Oracle instance** (see `docs/SCOPE.md`). Treat successful `--syntax-check`/validation as "structurally correct," not "proven against Oracle."
- **`datapatch` needs only `ORACLE_SID` set to the CDB** — it walks CDB$ROOT and every open PDB automatically. Don't loop `pdbs:` into separate `datapatch` invocations; that list is for postcheck reporting only.
