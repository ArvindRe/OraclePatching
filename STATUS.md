# Project Status

**For new sessions:** Read this file first, then `CLAUDE.md` for the project briefing.
**After each session:** Update the sections below before closing. Keep it current — this is the handoff.

---

## Where We Are

Phase 1 (v1 core, per `docs/ROADMAP.md`) is built and structurally validated: all YAML parses, all three playbooks pass `ansible-playbook --syntax-check`, and the trickier Jinja expressions (argv list construction, regex_search-based log-path parsing) are confirmed against real `ansible.template.Templar` — not just read by eye. **Never run against a real Oracle instance.** Phase 2 exists specifically to close that gap before this touches any client site, even non-production.

---

## What Has Been Done

### Code
- `roles/oracle_cpu_patch` — full precheck → stage → confirm-gate → stop → apply → start → datapatch → postcheck sequence, covering both the `opatchauto` (default) and manual `opatch apply` paths.
- Three playbooks: `cpu_patch_precheck.yml`, `cpu_patch_apply.yml`, `cpu_patch_rollback_info.yml`.
- `inventories/example_client/` template + `vars/patches/EXAMPLE_PATCH.yml` sample.

### Bugs caught and fixed during the build (see CLAUDE.md "Key Design Decisions" for the lasting rule each one left behind)
- `executable: /bin/bash` was placed as a sibling of `ansible.builtin.shell:` instead of nested inside its module args — Ansible read it as a second module call ("conflicting action statements"). Caught by `--syntax-check`.
- `regex_search(...) | first | default('')` crashes rather than gracefully defaulting on no match — fixed with `(... or ['']) | first`.
- A role-level `tags:` key was mistakenly used to try to restrict `cpu_patch_precheck.yml` to safe tasks only — it adds tags to tasks, it does not filter which run. Replaced with a structural `run_full_patch` var so the precheck playbook always ends in a clean PLAY RECAP.

### Documentation
- `README.md`, `docs/SCOPE.md`, `docs/ROADMAP.md` (5-phase plan to production-ready).
- `CLAUDE.md`, `SKILL.md`, this file — added 2026-09-15.

---

## Open Issues

- No test environment exists yet — Phase 2's Docker/Vagrant 19c CDB test setup has not been started.
- RAC and Grid Infrastructure paths are unverified — `grid_home` is accepted as an inventory var but no GI-specific task path has been built or exercised.
- No backup-verification gate exists before `apply_patch.yml` runs.
- Linux OS patching (`roles/linux_os_patch`) and Windows service management (`roles/windows_services`) are Phase 3/4 — not started, deliberately out of v1 scope.
- No audit logging / change-ticket gate yet (Phase 5).

---

## Next Actions (Priority Order)

1. **Build the Phase 2 test environment** (Docker or Vagrant, 19c CDB) — nothing here should reach a real client site before this exists.
2. **Run a real (even superseded) CPU patch end to end** against that test environment; capture the transcript as `docs/TEST_REPORT.md`, same role that file plays in the sibling Datapump project.
3. **Add the backup-verification precheck gate** (query `v$rman_backup_job_details`, refuse without a recent backup unless `-e skip_backup_check=true`).
4. **Verify/fix RAC + Grid Infrastructure behavior** against a multi-node or GI-enabled test setup.

---

## Key Reference Files

| Purpose | File |
|---------|------|
| Project briefing | [CLAUDE.md](CLAUDE.md) |
| What v1 covers / doesn't, assumptions, risks | [docs/SCOPE.md](docs/SCOPE.md) |
| Phased plan to production-ready | [docs/ROADMAP.md](docs/ROADMAP.md) |
| Workflow runbook (phase-by-phase usage) | [SKILL.md](SKILL.md) |
| Usage / quickstart / safety model | [README.md](README.md) |

---

## How to Update This File

At the end of each session, update: **What Has Been Done** (code/docs/bugs fixed, with file names), **Open Issues** (mark resolved items done, add anything newly discovered), **Next Actions** (reorder or remove completed items, add what came up). Keep entries concrete — file names and specifics, not vague summaries.
