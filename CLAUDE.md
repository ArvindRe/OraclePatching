# Oracle Patching Automation — Directory Briefing

> **New session? Read [STATUS.md](STATUS.md) first** — it has current project state, what's validated vs. not, and next actions. This file is the static project briefing; STATUS.md is the live handoff.

**Location:** `/Users/arvindregukumar/Documents/OraclePatching` — its own git repo, separate from the sibling `../Datapump` project.
**Owner:** Arvind Regukumar
**Purpose:** Ansible framework for Oracle Database maintenance automation, built for reuse across multiple client sites. v1 scope: Oracle CPU/PSU (quarterly Release Update) patching of 19c CDBs via OPatch/opatchauto. Full scope and phased plan: [docs/SCOPE.md](docs/SCOPE.md), [docs/ROADMAP.md](docs/ROADMAP.md).

---

## Project Inventory

### Playbooks

| Playbook | Role |
|----------|------|
| `playbooks/cpu_patch_precheck.yml` | Safe, read-only + stages the patch (copy/unzip/conflict-analyze). Structurally cannot reach a state-changing task — `run_full_patch: false` stops the role before the confirm gate. Run this first, always. |
| `playbooks/cpu_patch_apply.yml` | The real patch application — stop → apply → start → datapatch → postcheck. Refuses to proceed without `-e confirm_patch=<patch_id>` matching exactly. |
| `playbooks/cpu_patch_rollback_info.yml` | Prints (never executes) the manual rollback procedure for a given patch/host. |

### Role: `roles/oracle_cpu_patch`

| Task file | Role |
|-----------|------|
| `tasks/main.yml` | Dispatches the whole sequence; owns the `run_full_patch` / `confirm_patch` gates. |
| `tasks/precheck.yml` | OPatch version, existing-patch check, free space. |
| `tasks/stage_patch.yml` | Copy + unzip patch zip, conflict analysis (`opatchauto -analyze` / `opatch prereq`). |
| `tasks/stop_services.yml` / `start_services.yml` | Manual (`use_opatchauto: false`) path only — listener + instance stop/start. |
| `tasks/apply_patch.yml` | `opatchauto apply` (default) or manual `opatch apply` — `block`/`rescue` with real log content on failure. |
| `tasks/datapatch.yml` | SQL-level patch, once per CDB (covers all open PDBs automatically via `ORACLE_SID`). |
| `tasks/postcheck.yml` | `opatch lsinventory` + `dba_registry_sqlpatch` verification per CDB. |

### Inventory & patch definitions

- `inventories/<client>/` — one folder per client, copied from `inventories/example_client/`. `hosts.yml` defines an `oracle_db_hosts` group, one entry per DB host (`oracle_home`, `grid_home`); `group_vars/oracle_db_hosts.yml` carries `ansible_user`, `use_opatchauto`, `min_free_space_gb`.
- `vars/patches/<patch_id>.yml` — copied from `vars/patches/EXAMPLE_PATCH.yml` per quarterly patch. Shared across clients — the patch number/content is the same everywhere, only the inventory is client-specific.

---

## Key Design Decisions (do not silently change these)

1. **No database password anywhere.** Connections are OS-authenticated (`sqlplus / as sysdba`, local bequeath on the DB host) — the only credential this project needs is SSH + `become`. A client site with OS auth disabled needs a genuinely new code path added (see `docs/SCOPE.md` "Assumptions"), not a workaround bolted on quietly.
2. **`run_full_patch`, not `--tags`, gates precheck-only mode.** A role's own `tags:` key *adds* tags to its tasks — it does not filter which ones run (that's CLI-only, `--tags` on the command line). This was a real bug caught while building v1 (see STATUS.md); don't reintroduce a tags-based safety gate for `cpu_patch_precheck.yml`.
3. **`confirm_patch` must equal the exact `patch_id`,** not a generic "yes" — deliberate, so applying the wrong patch to the wrong host requires actively typing the wrong number.
4. **Rollback is never automated.** `cpu_patch_rollback_info.yml` only prints the procedure; a human decides whether and when to run it.
5. **`argv:` list construction relies on Ansible's native-type templating** — a module arg whose value is *exactly one* `{{ }}` expression (spanning the whole string) is passed through as a real Python object, not stringified, even though raw Jinja2's `Template.render()` always returns a string. Verified against real `ansible.template.Templar`, not assumed from memory. Don't "simplify" these into shell strings — that reintroduces shell-quoting risk this design avoids.
6. **`regex_search(...) | first` needs an `or ['']` guard.** `regex_search` returns `None` (not a list) on no match, and Jinja's `first` filter raises `TypeError` on `None` — it only catches `StopIteration` on an empty sequence, and `| default(...)` only catches Undefined, neither catches this. The working pattern, used everywhere a log path is parsed: `(expr | regex_search(...) or ['']) | first`.

---

## Notes for AI Agents

- **Never actually run `cpu_patch_apply.yml` against a real host** without the user explicitly asking and confirming — it stops production databases. This project has **never been run against a live database** (see `docs/SCOPE.md` risk notes); treat "it's built" as "it's structurally correct," not "it's proven against Oracle."
- Validate changes the way v1 was built: YAML parse-check, `ansible-playbook --syntax-check`, and for any non-trivial Jinja (list construction, regex filters), template it directly through `ansible.template.Templar` rather than trusting raw Jinja2 or reading by eye. This project's own build caught two real bugs exactly that way — see STATUS.md.
- `become_user` varies by task (`root` for `opatchauto`, `{{ oracle_os_owner }}` for `opatch`/`datapatch`/`sqlplus`/`lsnrctl`) — don't collapse these to one global `become_user`.
- Phase boundaries in `docs/ROADMAP.md` are exit gates, not suggestions — e.g., nothing should be described as safe for even non-production client use before Phase 2's real-environment testing is done.
