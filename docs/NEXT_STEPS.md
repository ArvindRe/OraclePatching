# Next Steps & Blockers

> Living tracker — update this file as items close or new ones surface,
> don't let it drift. For the detailed narrative/history behind any item,
> see `STATUS.md` (local-only) and `docs/OPEN_QUESTIONS.md`. This file is
> the short, forward-looking view; those are the long-form record.

**How to use this:** check an item off when it's actually done and
verified (not just "code written"), move its detail to `STATUS.md` if
worth keeping, and delete the line here. Add new items as they surface —
don't let real gaps go untracked just because this file wasn't updated.

---

## 🔴 Blockers (on the critical path — most other work depends on these)

- [ ] **No real CPU/RU patch downloaded.** Requires My Oracle Support
  access (active support contract/CSI) — this is on the user, not
  something an agent should do. See `cowork_prompt/download_cpu_patch.md`
  for the concrete next step (a specific patch number to search for once
  access exists). **Blocks:** `cpu_patch_apply.yml` validation, Phase 2's
  exit gate, the full `run_patch.py` pipeline end-to-end, restore-point
  creation being exercised live, and the `postcheck.yml` `cdb_registry_sqlpatch`
  fix (already built and Templar/live-verified, but not yet proven against
  a real patch run).
- [ ] **Control node network topology — undecided.** Where does the
  Ansible control node actually sit relative to the air-gapped production
  network? Answering this decides whether patch zips need the same
  one-directional DMZ transfer pattern built for the knowledge base, or a
  simpler access-controlled-download design. See
  `docs/OPEN_QUESTIONS.md` question 4. **Blocks:** any patch-staging DMZ
  mechanism — don't build one until this is answered.

---

## Next steps — not blocked, actionable now

### Base Ansible layer (`ansible/`)
- [ ] Fix `CDB1`/listener not auto-starting on VM boot (`/etc/oratab` has
  `CDB1:...:N`) — currently a manual `startup;` + `lsnrctl start` after
  every `vagrant up`. Worth a real fix if the VM keeps stopping/starting.
- [ ] Re-verify `ansible/inventories/vagrant_test/hosts.yml`'s `ansible_port`
  after any VM recreate — `vagrant-qemu` doesn't guarantee a stable port.

### Agentic layer (`Patching_Agentic/`)
- [ ] Harden the two remaining raw-exception gaps in `run_patch.py`:
  Qdrant unreachable during retrieval, Postgres unreachable during
  `record_execution` — both still crash instead of failing cleanly, unlike
  everything else in the pipeline. See `docs/OPEN_QUESTIONS.md` question 2.
- [ ] Improve RAG retrieval quality: query construction currently ignores
  scan signals (RAC/DG role, version) that would disambiguate results;
  embedding granularity dilutes section-specific relevance (one vector per
  whole object, not per section). See `docs/OPEN_QUESTIONS.md` question 1
  for the full prioritized list.
- [ ] Wire `rag/ingestion/patch_release_monitor.py` into a scheduled
  mechanism (currently manual `python -m rag.ingestion.patch_release_monitor`
  only) — cron, launchd, or similar, per `docs/INGESTION_PROCESS.md`.
- [ ] Expand `rag/knowledge_staging/` beyond the current 6 objects —
  ongoing, per the content-selection criteria in `DB_PATCHING_SCOPE.md`.
- [ ] Add a small retrieval eval set (query → expected top doc) so future
  embedding/query changes can be regression-tested instead of eyeballed.

### Cross-cutting / decisions needed
- [ ] Qdrant → `pgvector` migration — recommended before real deployment
  (fewer services on an air-gapped box), not urgent for local dev. See
  `docs/OPEN_QUESTIONS.md` question 1.
- [ ] Decide the real DMZ transfer *medium* for patch zips once the control
  node topology question above is answered (network hop vs. write-once
  physical media — size and licensing differ from the knowledge-base case).
- [ ] **Decide whether v1's in-place-only patching scope still holds.**
  `docs/SCOPE.md` scopes v1 to in-place patching (`opatchauto` against the
  existing Oracle Home) only. The
  [2026 dincosman.com article](REFERENCES.md) builds *out-of-place*
  patching as its default (new home, patch it, switch over) — many
  enterprise DBAs prefer it specifically because rollback is "point back
  at the old home." This is a genuine scope decision, not something to
  silently build — see `docs/REFERENCES_LEARNINGS.md` "Real scope gaps"
  for the full comparison. Flagging here rather than implementing
  speculatively.
- [ ] **Decide when/whether to add a RAC rolling-patch GI precondition
  check.** `rag/knowledge_staging/rac_rolling_patch_opatchauto.yml`
  (sourced from Oracle's own OPatchAuto multi-node doc) notes rolling mode
  can't even start unless Grid Infrastructure is active on at least one
  remote node — nothing in `precheck.yml` or `backup_check.yml` checks
  this today, and there's no RAC-specific task path to hang it off yet
  (`registry/procedures/oracle_19c_rac_ru_patch.yml` is still a
  placeholder). Worth deciding whether this waits for real RAC/GI work to
  start, or is worth a standalone precheck sooner. See
  `docs/REFERENCES_LEARNINGS.md` "Real scope gaps."

---

## Blocked on the 🔴 blockers above — actionable only once those clear

- [ ] Run `cpu_patch_precheck.yml` → `cpu_patch_apply.yml` end to end
  against the VM; capture the transcript as `docs/TEST_REPORT.md`. Closes
  Phase 2's exit gate. (Would also be the first live proof of the
  `postcheck.yml` fix below against a real patch, not just simulated rows.)
- [ ] Run the full `run_patch.py` pipeline (scan → retrieve → propose →
  approve → execute) end to end against the VM — the one thing neither
  layer has proven yet.
- [ ] Exercise `executor/rollback/snapshot.py`'s restore-point creation
  against the live VM — every run so far has halted at the dry-run gate
  first, before reaching this step.

---

## Deferred, explicitly out of scope for now

- RAC + Grid Infrastructure verification — needs a multi-node/GI test
  setup this project doesn't have; `grid_home` is accepted as a var but
  the path is unverified. Its own blocker, separate from the two above.
- Linux OS patching (`roles/linux_os_patch`) and Windows service management
  (`roles/windows_services`) — Phase 3/4, deliberately not started.
- Central/queryable audit logging, change-ticket gate — Phase 5.

---

## Changelog

- 2026-09-22T21:11:55+05:30 — Initial version — consolidated from STATUS.md's Open Issues/Next Actions and docs/OPEN_QUESTIONS.md into one forward-looking tracker — Arvind Regukumar
- 2026-09-22T21:20:35+05:30 — Removed the backup-verification precheck gate item — built, verified live against the vagrant VM (both the refuse-without-backup and override paths), see STATUS.md and docs/SCOPE.md — Arvind Regukumar
- 2026-09-23T00:54:21+05:30 — Removed the postcheck.yml STATUS-gate item (fixed: CDB_REGISTRY_SQLPATCH + assert, see STATUS.md/REFERENCES_LEARNINGS.md) and the RESTORE VALIDATE backup item (addressed: verify_backup_restorable opt-in added to backup_check.yml). Added two explicit decision-needed items surfaced by the new dincosman.com references: out-of-place patching scope, and a RAC rolling-patch GI-active-on-remote-node precondition — flagged for a human decision, not implemented — Arvind Regukumar
- 2026-09-23T01:01:06+05:30 — Found stale via a full-repo markdown audit: the patch-download blocker's own text still described the postcheck.yml STATUS gate as an unfixed "fix being provable" item — corrected to reflect it's already built and verified, just not yet proven against a real patch — Arvind Regukumar
