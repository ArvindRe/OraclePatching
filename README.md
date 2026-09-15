# Oracle Patching Automation

Ansible framework for Oracle Database maintenance automation, built for use
across multiple client sites. **v1 scope: Oracle CPU/PSU (quarterly
Release Update) patching via OPatch.** Linux OS patching and Windows
service start/stop are planned but not yet built — see
[docs/ROADMAP.md](docs/ROADMAP.md) for the phased plan and
[docs/SCOPE.md](docs/SCOPE.md) for what v1 does and doesn't cover.

## Why this exists

Oracle ships a CPU (Critical Patch Update) / RU (Release Update) every
quarter. Applying one by hand — stage, conflict-check, stop services,
`opatchauto apply`, `datapatch`, verify, restart — is the same ~10-step
procedure every time, against every CDB, at every client site. This
automates the repeatable parts and puts a hard stop in front of the
parts that shouldn't be automated (see "Safety model" below).

## Architecture

```
Ansible controller (your laptop, or a jump host per client)
    |
    +-- SSH -> Oracle DB host (inventory: oracle_db_hosts)
                |
                +-- opatchauto / opatch    (become_user: root / oracle)
                +-- datapatch, sqlplus     (become_user: oracle, OS-authenticated "/ as sysdba")
                +-- lsnrctl                (become_user: oracle)
```

No database password anywhere in this project — `/ as sysdba` is a local,
OS-authenticated connection on the DB host itself (the standard DBA
pattern), so the only credential Ansible needs is SSH access + `become`.

## Project structure

```
OraclePatching/
├── ansible.cfg
├── inventories/
│   └── example_client/        # copy per client — see docs/SCOPE.md "Multi-client model"
│       ├── hosts.yml           # oracle_db_hosts group, one entry per DB host
│       └── group_vars/oracle_db_hosts.yml
├── playbooks/
│   ├── cpu_patch_precheck.yml  # safe, read-only + stages the patch — run this first
│   ├── cpu_patch_apply.yml     # the real thing — gated, see "Safety model"
│   └── cpu_patch_rollback_info.yml  # prints the manual rollback procedure, executes nothing
├── roles/oracle_cpu_patch/
│   ├── defaults/main.yml       # full per-patch variable schema
│   └── tasks/
│       ├── main.yml            # dispatches: precheck -> stage -> [confirm gate] -> stop
│       │                       #   -> apply -> start -> datapatch -> postcheck
│       ├── precheck.yml        # OPatch version, existing inventory, free space
│       ├── stage_patch.yml     # copy + unzip patch, conflict analysis (opatchauto -analyze)
│       ├── apply_patch.yml     # opatchauto apply (or manual opatch apply), block/rescue
│       ├── stop_services.yml / start_services.yml   # manual (non-opatchauto) path only
│       ├── datapatch.yml       # SQL-level patch, per CDB
│       ├── postcheck.yml       # opatch lsinventory + dba_registry_sqlpatch verification
│       └── audit_log.yml       # writes one JSON-lines entry per run — see "Safety model"
└── vars/patches/EXAMPLE_PATCH.yml   # copy per quarterly patch — patch_id, CDBs, zip path
```

A local Vagrant+QEMU Phase 2 test environment (a real 19c CDB to run the
playbooks against) exists but is kept out of this repo — see
`docs/ROADMAP.md` Phase 2.

## Usage

```bash
# 1. Point at the right client's inventory (copy example_client/ first — see docs/SCOPE.md)
cp -r inventories/example_client inventories/acme_corp
vim inventories/acme_corp/hosts.yml

# 2. Describe the patch run
cp vars/patches/EXAMPLE_PATCH.yml vars/patches/34765931.yml
vim vars/patches/34765931.yml

# 3. Precheck — safe, review the output, always ends in a clean PLAY RECAP
ansible-playbook -i inventories/acme_corp/hosts.yml \
  playbooks/cpu_patch_precheck.yml -e @vars/patches/34765931.yml

# 4. Apply — requires the exact patch_id as explicit confirmation
ansible-playbook -i inventories/acme_corp/hosts.yml \
  playbooks/cpu_patch_apply.yml -e @vars/patches/34765931.yml \
  -e confirm_patch=34765931
```

## Safety model

- **Precheck/stage vs. apply are separate playbooks.** `cpu_patch_precheck.yml`
  never touches a running service — it can't, structurally (`run_full_patch:
  false` stops the role before it reaches anything state-changing).
- **The apply path has a hard confirmation gate.** Nothing that stops a
  listener, shuts down an instance, applies a binary patch, or runs
  `datapatch` executes without `-e confirm_patch=<patch_id>` matching the
  patch being applied *exactly* — not a generic "yes", the actual patch
  number, so applying the wrong patch to the wrong host requires actively
  typing the wrong number.
- **Every apply-side command uses `block`/`rescue`.** On failure you get
  the actual OPatch/opatchauto session log content, not just "non-zero
  return code."
- **`serial: 1` + `any_errors_fatal: true`** on every play — never fans a
  patch out across a fleet at once.
- **Rollback is never automated.** `cpu_patch_rollback_info.yml` prints the
  exact manual rollback command sequence for the specific patch/host, but a
  human decides whether to run it and when.
- **Every invocation is audit-logged — mandatory, not optional.** One JSON
  Lines entry per run (`audit_log_path`, default
  `~/.oracle_patching/audit.log` on the control node) recording who,
  when, which host/patch/CDBs, precheck-only vs. full apply, and
  success/failure — written via `block`/`rescue`/`always` so it fires no
  matter where in the sequence a run stops, including a rejected confirm
  gate. See `docs/SCOPE.md` "Audit logging" for the exact fields and what
  this is (and isn't) a substitute for.
