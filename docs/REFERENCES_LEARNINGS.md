# References — Learnings

> Not a restatement of [REFERENCES.md](REFERENCES.md) — that file says what
> each source *is*; this one says what this project should actually *do*
> differently because of it. Each item ties back to a concrete gap, file,
> or decision, not a generic summary of the source.

---

## Real scope gaps this surfaces

- **Out-of-place patching isn't supported at all, and that may matter more
  than assumed.** `docs/SCOPE.md` scopes v1 to in-place patching only
  (`opatchauto` against the existing Oracle Home). The
  [dincosman.com article](REFERENCES.md) builds *out-of-place* patching as
  its default approach (new home, patch it, switch over) — a pattern many
  enterprise DBAs prefer specifically because rollback is "point back at
  the old home" rather than this project's own manual-procedure rollback.
  Worth an explicit decision (not a silent gap): does v1's in-place-only
  scope hold, or is out-of-place worth a v2 line in `docs/ROADMAP.md`?
- **The RAC/GI gap now has a concrete, detailed reference implementation to
  learn from, not just a known unknown.**
  `registry/procedures/oracle_19c_rac_ru_patch.yml` is explicitly a
  placeholder ("reuses the same playbooks with grid_home set... NOT a
  separate RAC-specific task path"). The
  [2024 dincosman.com article](REFERENCES.md) is a real runbook, not just a
  summary: a 19.20→19.22 GI out-of-place update as **5 ordered playbooks**
  against a two-node cluster — new grid home dirs on all nodes, unzip/patch/
  `gridSetup.sh` on node 1 only, tag-and-`root.sh` on both nodes to switch
  over, managed recovery on standby databases, then deinstall the old home.
  The linked repo (`Insane-DBAs-Backpack`) confirms this isn't just prose —
  it has real `dbru/` and `giru/` subdirectories, i.e. **GIRU and DBRU are
  built as genuinely separate playbook trees**, not one shared role with a
  var flipped. When RAC/GI work actually starts (`docs/NEXT_STEPS.md`),
  this is a real second opinion on the task structure, with an actual
  ordered sequence to compare against — not necessarily to copy, but
  "5 separate playbooks, DBRU and GIRU never sharing task files" is a
  concrete, opinionated answer to a structural question this project
  hasn't actually answered yet.
- **`start_recover_onremote.sh` in that repo is independent confirmation
  this project's Data Guard patch-ordering knowledge object has the right
  idea.** `rag/knowledge_staging/data_guard_ru_patch_order.yml` (authored
  from Oracle DG documentation, standby-first ordering) and this repo's
  step 4 (managed recovery on standby databases, as its own dedicated
  script/step) are independently converging on the same shape: standby
  handling is its own explicit step, not folded into the primary's own
  patch sequence. Two independent sources agreeing is a stronger signal
  than either alone.
- **Rolling RAC patching has a precondition this project doesn't check
  anywhere.** `rag/knowledge_staging/rac_rolling_patch_opatchauto.yml`
  (sourced from Oracle's own OPatchAuto multi-node doc) notes that rolling
  mode can't even *start* unless Grid Infrastructure is active on at least
  one remote node. If/when a real GI-specific task path gets built, this
  needs its own precheck — it isn't implied by anything currently in
  `precheck.yml` or `backup_check.yml`.
- **`expect_module_conf.txt` in the repo hints at an interactive step
  neither this project's design nor its knowledge objects account for.**
  Ansible's `expect` module exists specifically for CLI tools that prompt
  interactively (a real candidate: `gridSetup.sh` itself sometimes does).
  Not yet confirmed which step needs it (the repo listing was truncated in
  what could be fetched) — flagged here rather than guessed at, since
  claiming certainty about *why* it's there without reading the actual file
  would be exactly the kind of unverified assumption this project has
  already been burned by twice this session.

## A concrete hardening gap in code — now addressed

- **`backup_check.yml` proved a backup *completed*, not that it's
  *restorable*. Fixed 2026-09-23.** The gate queries `v$rman_backup_job_details`
  for `STATUS='COMPLETED'` — but `rag/knowledge_staging/rman_backup_validation_before_patch.yml`
  (sourced from Oracle's own Backup and Recovery guide) is specific that
  `RESTORE ... VALIDATE` is the only way to actually confirm a backup can
  be restored — a completed backup job and a restorable backup are not
  the same guarantee. Added an opt-in `RESTORE DATABASE/ARCHIVELOG ALL
  VALIDATE` step (`verify_backup_restorable`, default off — real time/I/O
  cost on every run, a deliberate cost/benefit tradeoff, not silently
  skipped). `failed_when` checked both `rc != 0` and real `RMAN-`/`ORA-`
  markers in stdout, live-verified against the vagrant VM's genuine
  no-backup state (both conditions independently triggered on a real
  failure). See `backup_check.yml`'s own changelog.
- **`postcheck.yml`'s informational-only STATUS gate had a concrete,
  non-theoretical failure mode. Fixed 2026-09-23 — and the fix uncovered a
  deeper bug than originally scoped.** The datapatch sources behind
  `rag/knowledge_staging/datapatch_cdb_pdb_behavior.yml` confirm datapatch
  silently skips any PDB that's closed at the moment it runs — no error,
  no warning. The check meant to catch that (`dba_registry_sqlpatch`
  status, `debug`-only not `failed_when`) turned out to be reading the
  wrong view entirely: `DBA_REGISTRY_SQLPATCH` only ever reports the
  *currently connected container's* own status — with this project's
  plain `/ as sysdba` connection (no `ALTER SESSION SET CONTAINER`),
  that's `CDB$ROOT` only. It was structurally blind to every PDB, not
  merely non-fatal. Fixed by switching to `CDB_REGISTRY_SQLPATCH` (has a
  `CON_ID` spanning every container, verified against real Oracle docs)
  joined to `v$pdbs`, and replacing the `debug` with a hard `assert` per
  container. Verified via real `Templar` across 3 cases (all-success,
  zero-rows, mixed success+failure) and live against the VM (real query
  execution, real historical row returned). Known residual limitation,
  documented in the task file itself: CDB views only surface *open*
  containers, so a closed PDB still won't show a row — same underlying
  cause as datapatch's own skip behavior, not a new gap. See
  `postcheck.yml`'s own changelog for full detail.

## Design patterns this project already applied, now with real grounding

- **Splitting "awareness" from "acquisition" was the right call.**
  `rag/ingestion/patch_release_monitor.py` exists because
  oracle.com/security-alerts proved a new-release *signal* doesn't need MOS
  access, even though the *download* still does. That's a real,
  Oracle-confirmed asymmetry, not an assumption — worth remembering as a
  template if other MOS-gated problems come up: check whether the
  "does something new exist" half is actually public before assuming the
  whole workflow needs credentials.
- **Similar-sounding official content types can mean different things.**
  The CPU-vs-CSPU distinction in Oracle's own RSS feed (near-identical
  titles, one carries a DB RU and one doesn't) is a general lesson, not a
  one-off: don't assume a naming pattern is unambiguous just because it's
  from an official source — verify the actual taxonomy before automating
  around it, the way `patch_release_monitor.py`'s regex had to.
- **Oracle already maintains the patch-number master index — don't
  duplicate it.** MOS Notes 1454618.1 / 2118136.2 / 730365.1 exist
  specifically so nobody has to re-derive "which patch number is the RU for
  version X." This project's knowledge base should keep pointing at these
  notes as the source of truth (as `DB_PATCHING_SCOPE.md` already does) —
  never cache or re-derive patch-number lookups locally, that's a staleness
  bug waiting to happen.

## Process / meta learnings

- **The verify-before-trusting discipline this project already practices
  keeps paying off, concretely.** The exact SQL in `backup_check.yml` (column
  names, `STATUS`/`INPUT_TYPE` values) and the RMAN validation guidance both
  came from checking real `docs.oracle.com` pages directly rather than
  memory — consistent with this project's own established pattern (the
  `Templar`-verified Jinja, the `$`-escaping bug caught by live testing).
  Nothing here is a new lesson so much as another data point that this
  discipline is load-bearing, not optional.
- **Not every source is checkable, and that's worth saying plainly.** The
  Reddit post in `REFERENCES.md` is flagged unverified specifically because
  this session's fetch tooling can't reach reddit.com at all — the honest
  move was recording "topic matches, content unconfirmed" rather than
  inferring content from the title or search snippets. Worth keeping that
  standard for any future source this can't actually be read.

---

## Changelog

- 2026-09-22T23:06:35+05:30 — Initial version — concrete, project-specific takeaways distilled from REFERENCES.md, not a restatement of it — Arvind Regukumar
- 2026-09-23T00:42:39+05:30 — Updated RAC/GI section with real detail from the 2024 dincosman.com article and the actual GitHub repo behind it (confirmed dbru/giru split, standby-recovery-as-its-own-step corroborating this project's own DG knowledge object, and an honestly-flagged-not-guessed-at expect_module_conf.txt question) — Arvind Regukumar
- 2026-09-23T00:54:21+05:30 — Marked both items in "A concrete hardening gap in code" as addressed: backup_check.yml's RESTORE VALIDATE opt-in, and postcheck.yml's CDB_REGISTRY_SQLPATCH + assert fix (which turned out to be a deeper bug — DBA_REGISTRY_SQLPATCH was blind to every PDB, not just informational) — Arvind Regukumar
