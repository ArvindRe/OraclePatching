# Roadmap — v1 to Production-Ready

Five phases. Each has a concrete deliverable and an acceptance criterion —
"done" means the criterion is met, not "the code exists." Phases are
ordered by risk: nothing moves to a real client site until phase 2 closes
the "never tested against a live database" gap from
[SCOPE.md](SCOPE.md).

---

## Phase 1 — v1: Core CPU/PSU patching (done)

**Deliverable:** `roles/oracle_cpu_patch` + three playbooks
(`cpu_patch_precheck.yml`, `cpu_patch_apply.yml`,
`cpu_patch_rollback_info.yml`) covering stage → conflict-check → confirm
gate → stop → apply → start → datapatch → postcheck for a single-instance
or Oracle Restart 19c CDB, plus a mandatory local audit log entry (JSON
Lines, one per invocation, success or failure) — see `SCOPE.md` "Audit
logging".

**Acceptance criteria (met):**
- All YAML parses; all three playbooks pass `ansible-playbook --syntax-check`.
- The `argv` list-construction and `regex_search`-based log parsing
  patterns are verified against real `ansible.template.Templar` (not just
  raw Jinja2), including the wallet/no-wallet and match/no-match branches.
- `cpu_patch_precheck.yml` cannot reach any state-changing task under any
  invocation (structural `run_full_patch: false`, not a tag convention that
  relies on the caller remembering `--tags`).
- `cpu_patch_apply.yml` refuses to proceed without `confirm_patch` exactly
  matching `patch_id`.
- Every invocation — success, failure, or precheck-only — writes exactly
  one audit log entry (`block`/`rescue`/`always` wired in `main.yml`,
  verified against real `ansible.template.Templar` across all three
  outcomes, not just read by eye).

**Known gap:** never run against a real Oracle instance. Phase 2 exists to
close that before anything here touches a client.

---

## Phase 2 — Real-environment validation + RAC/GI + backup gate

**Deliverable:**
- A Docker or Vagrant-based 19c CDB test environment (single-instance to
  start), checked into the repo (`test/` or similar), that
  `cpu_patch_precheck.yml`/`cpu_patch_apply.yml` can actually run against
  end to end — mirroring the pattern the sibling Datapump project already
  uses for its own script testing.
- A real (even if old/superseded) CPU patch applied successfully against
  that test environment, with `postcheck.yml` confirming
  `dba_registry_sqlpatch` shows `SUCCESS`.
- RAC-aware task path: verify (or fix) `opatchauto`'s multi-node behavior
  when invoked from one node of a cluster; add whatever `-rolling`/
  `-nonrolling` flag handling turns out to be needed.
- Grid Infrastructure path: exercise `grid_home` for a GI-integrated patch,
  not just a DB-only one.
- **Backup verification gate**: a precheck task that confirms a recent RMAN
  backup exists (e.g., queries `v$rman_backup_job_details` for a completion
  within N hours) and refuses to proceed without one, override-able only
  with an explicit `-e skip_backup_check=true`.
- Automated OPatch-updater remediation: if `precheck.yml` finds OPatch
  below the patch's minimum required version, offer (not force) an
  `opatch_updater_zip` var path to apply it as part of `stage_patch.yml`.

**Acceptance criterion:** a documented, repeatable test run — from a clean
snapshot, through precheck, apply, and postcheck — against a real (test)
19c CDB, with the transcript kept as `docs/TEST_REPORT.md` (same role this
file plays in the Datapump project).

**Exit gate for phase 2:** this is the point where the framework is safe
to run against a **non-production** client database. Not production yet —
see phase 5.

---

## Phase 3 — Linux OS patching role

**Deliverable:** `roles/linux_os_patch` — `yum`/`dnf` package updates,
kernel patch detection, reboot orchestration (`ansible.builtin.reboot`)
with pre/post health checks, serialized so a multi-node cluster never has
two nodes down for OS patching at once.

**Coordination requirement:** must check for (and refuse to run alongside)
an in-progress `oracle_cpu_patch` run on the same host — OS patching and
DB patching on the same box in the same window is asking for a confusing
failure to diagnose.

**Acceptance criterion:** same shape as phase 1/2 — syntax-validated, then
run end to end against the phase 2 test environment, with a documented
before/after `uname -r` and package-version diff.

---

## Phase 4 — Windows service management role

**Deliverable:** `roles/windows_services` — WinRM-based start/stop of
named services via `ansible.windows.win_service`, a new
`inventories/*/hosts.yml` `windows_hosts` group (`ansible_connection:
winrm`), and a bootstrap doc for enabling WinRM on a target Windows Server
that doesn't have it yet (this is usually the actual blocker in practice,
not the Ansible side).

**Prerequisite:** `pywinrm` on the control node; a decision on
Kerberos vs. NTLM vs. cert-based WinRM auth per client (this varies enough
by site that it's a phase-4 design question, not a v1 one).

**Acceptance criterion:** start/stop of a real Windows service (e.g., an
Oracle-related Windows service, or a generic test service) against a real
or test Windows Server host, both directions, idempotently reported
(`changed` only when the service actually changed state).

---

## Phase 5 — Production-ready, multi-client

This is the phase that actually earns "production ready" — everything
before it is necessary but not sufficient.

**Deliverables:**
- **Central/queryable audit logging**: v1 already writes a local, durable
  JSON-lines entry per run (see `SCOPE.md` "Audit logging") — this phase
  upgrades that to somewhere queryable across hosts/clients: a central log
  table (mirroring the Datapump project's `dp_log_entry` pattern) or
  shipped to the client's existing SIEM/logging pipeline.
- **Change-ticket gate**: `confirm_patch` alone is a good typo-guard, not
  an approval record — add a required `change_ticket` var, validated
  against a real ticketing system's API where the client has one, logged
  alongside the audit entry either way.
- **Secrets isolation, finalized**: for the (hopefully rare) client site
  with OS authentication disabled, a real vault-per-client pattern with
  documented rotation, not an afterthought.
- **CI**: `yamllint` + `ansible-lint` + `--syntax-check` running on every
  push (GitHub Actions or equivalent), so a broken playbook never reaches
  `main` in the first place.
- **Runbooks**: "onboard a new client," "add a new host," "schedule a
  patch window," "what to do when `cpu_patch_apply.yml` fails partway
  through" — written for whoever's on call, not just for the person who
  built this.
- **Versioning**: tagged releases + `CHANGELOG.md`, since multiple client
  sites will be running different versions of this at any given time.

**Acceptance criterion:** a second person — not whoever built phases 1–4 —
can onboard a new client and run a real patch window using only the
runbooks, with no undocumented tribal knowledge required.

---

## Summary table

| Phase | Focus | Exit gate |
|---|---|---|
| 1 (done) | Core CPU/PSU patching, single-instance | Structurally correct, validated by construction |
| 2 | Real-environment testing, RAC/GI, backup gate | Safe for **non-production** client use |
| 3 | Linux OS patching | Same validation bar as phase 2, coordinated with phase 1/2 |
| 4 | Windows service management | Real start/stop verified against a Windows host |
| 5 | Audit, change control, CI, runbooks, secrets | Safe for **production**, multi-client, handoff-ready |
