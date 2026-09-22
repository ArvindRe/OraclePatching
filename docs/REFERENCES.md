# References

> External sources actually used or worth tracking for this project — not a
> link dump. Each entry says what it's for and whether/how it's been
> verified. Consolidated here from citations that were previously scattered
> across `STATUS.md`, `Patching_Agentic/DB_PATCHING_SCOPE.md`, and the
> `rag/knowledge_staging/` objects' own `source` fields — those files keep
> their own citations too where the connection is tight (e.g. a specific
> knowledge object's `source:`); this is the one place to look for
> everything at once, including things not yet turned into a knowledge
> object. See [REFERENCES_LEARNINGS.md](REFERENCES_LEARNINGS.md) for what
> this project should actually *do* differently because of these — this
> file only says what each source *is*.

---

## Practitioner write-ups — candidate sources, not yet ingested

- **[I automated Oracle 19.3 to 19.28 GI and DB patching with Ansible](https://dincosman.com/2026/04/18/ansible-oracle-patching/)**
  — Osman DİNÇ ("InsaneDBA"). Ansible playbooks for Oracle 19.28 Release
  Update patching covering RAC, Grid Infrastructure, and Database homes;
  out-of-place patching (new homes rather than patching in place), GIRU/DBRU
  application, datapatch on primaries, standby recovery automation, an
  "n-1" patching strategy (stay one RU behind, apply the latest MRP on
  top), and a linked GitHub repo. Directly relevant to this project's own
  flagged gaps — RAC/GI path is unverified here (`docs/NEXT_STEPS.md`), and
  out-of-place patching isn't covered by this project's design at all (v1
  is in-place only). Fetched and read directly 2026-09-22; not yet turned
  into a `rag/knowledge_staging/` object — a real candidate for one,
  particularly on RAC/GI ordering and out-of-place patching, once someone
  reviews it against the same content-selection criteria in
  `DB_PATCHING_SCOPE.md` as the existing 6 objects.
- **[Reddit r/SysAdminBlogs — "I automated Oracle 19.28 Database and Grid..."](https://www.reddit.com/r/SysAdminBlogs/comments/1soypwm/i_automated_oracle_1928_database_and_grid/)**
  — same general topic (Oracle 19.28 GI/DB patching automation) and very
  possibly the same author sharing/discussing the article above, but **not
  independently verified** — Reddit is unreachable through this session's
  fetch tooling (blocked), and a web search did not surface independent
  confirmation of the post's actual content. Treat as "found, topic
  matches, content unconfirmed" until someone with Reddit access actually
  reads it.
- **[Automating Oracle Grid Infrastructure Updates with Ansible](https://dincosman.com/2024/03/17/grid-update-ansible/)**
  — same author, earlier (2024) and considerably more detailed than the
  2026 article above: a real 19.20→19.22 GI out-of-place update walked
  through as **5 ordered playbooks** against a two-node cluster (gns01/gns02) —
  (1) create new grid home dirs on all nodes, (2) unzip/patch/`gridSetup.sh`
  on the first node only, (3) tag the new home in the inventory + run
  `root.sh` on both nodes to switch over, (4) start managed recovery on
  standby databases, (5) deinstall the old grid home. Prerequisites: SSH
  equivalency for both `oracle` and `root`, OPatch + GIRU + one-off patches
  + base grid home media staged ahead of time. This is the more actionable
  of the two dincosman.com articles for actually building the RAC/GI task
  path this project doesn't have — the 2026 article is the summary, this
  one is closer to a runbook. Fetched and read directly 2026-09-22.
- **[github.com/dincosman/Insane-DBAs-Backpack — "ansible playbooks latest"](https://github.com/dincosman/Insane-DBAs-Backpack/tree/main/Database/Oracle/ansible%20playbooks%20latest)**
  — the actual repo behind both articles above. Confirmed structure:
  separate `dbru/` and `giru/` subdirectories (concrete evidence for the
  "DBRU and GIRU are genuinely separate concerns" learning in
  `REFERENCES_LEARNINGS.md`), plus `start_recover_onremote.sh` (matching
  the 2024 article's step 4, standby managed recovery) and
  `expect_module_conf.txt` (Ansible's `expect` module — likely used for an
  interactive step in `gridSetup.sh` or similar; not yet confirmed which).
  The repo listing was truncated in what could be fetched ("View all
  files" not expanded) — worth a closer read before treating this as fully
  understood, not just skimmed.
- **[gist.github.com/dincosman](https://gist.github.com/dincosman)** — same
  author's public Gist profile. **Mostly not patching-specific** — general
  Oracle/Postgres DBA scripts (row-count utilities, lock-wait diagnostics,
  an audit-record spooler, a password-hash comparison script, a CPU stress
  test). Worth knowing exists as the same author's broader toolkit, but
  low direct relevance to this project's actual scope — don't oversell
  this one as a patching resource.

## Official Oracle sources

- **[oracle.com/security-alerts](https://www.oracle.com/security-alerts/)**
  — Oracle's public Critical Patch Update advisory index, no MOS login
  needed. CPUs (including the quarterly Database RU) publish here on the
  3rd Tuesday of Jan/Apr/Jul/Oct. This project's
  `rag/ingestion/patch_release_monitor.py` polls the real RSS feed behind
  this page — see `docs/INGESTION_PROCESS.md`.
- **RSS feed**: `https://www.oracle.com/ocom/groups/public/@otn/documents/webcontent/rss-otn-sec.xml`
  — found by inspecting the page source above, verified directly (real
  RSS 2.0, reachable with default `urllib`, no special headers needed).
  Mixes quarterly CPU advisories with newer monthly CSPU advisories — see
  `patch_release_monitor.py`'s docstring for why that distinction matters.
- **MOS Note 1454618.1** — "Quick Reference to Patch Numbers for Database
  PSU, SPU(CPU), Bundle Patches and Patchsets." The canonical DBA-facing
  patch-number lookup once MOS access exists. Requires MOS login.
- **MOS Note 2118136.2** — download assistant for selecting the right
  update/revision/PSU/CPU/Bundle Patch/Patchset. Requires MOS login.
- **MOS Note 730365.1** — upgrade reference list across most available
  Oracle Database releases. Requires MOS login.
- **[docs.oracle.com — V$RMAN_BACKUP_JOB_DETAILS (19c)](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/V-RMAN_BACKUP_JOB_DETAILS.html)**
  — exact column names/values (`STATUS`, `INPUT_TYPE`, etc.), verified
  directly before writing `ansible/roles/oracle_cpu_patch/tasks/backup_check.yml`'s
  SQL, not assumed from memory.
- **[docs.oracle.com — Backup and Recovery: Validating Database Files and Backups (19c)](https://docs.oracle.com/en/database/oracle/oracle-database/19/bradv/validating-database-files-backups.html)**
  — source for `rag/knowledge_staging/rman_backup_validation_before_patch.yml`.
- **[Oracle Enterprise Manager Cloud Control — OPatchAuto multi-node RAC patching](https://docs.oracle.com/en/enterprise-manager/cloud-control/enterprise-manager-cloud-control/13.4/optch/manual-multi-node-patching-grid-infrastructure-and-rac-db-environment-using-opatchauto.html)**
  — source for `rag/knowledge_staging/rac_rolling_patch_opatchauto.yml`.
- **[docs.oracle.com — Applying Patches to Oracle ASM (21c)](https://docs.oracle.com/en/database/oracle/oracle-database/21/cwhpx/applying-patches-to-oracle-asm.html)**
  — source for `rag/knowledge_staging/grid_infrastructure_asm_patching.yml`
  (thin on its own, cross-checked against the RAC source above since ASM
  patching uses the same `opatchauto` engine).

## Public practitioner references (no MOS needed)

- **[oracle-base.com — Patching: Find the Required Patches for Oracle Products](https://oracle-base.com/articles/misc/patching-find-the-required-patches-for-oracle-products)**
  — general public reference for locating patch numbers.
- Sources behind `rag/knowledge_staging/datapatch_cdb_pdb_behavior.yml`:
  Dbvisit Support ("Applying 19c Release Updates"), oracledbwr.com,
  geodatamaster.com ("Oracle PDB and when is datapatch required") —
  cross-checked across all three rather than trusted from one, per this
  project's own source-credibility criteria (`DB_PATCHING_SCOPE.md`).

---

## Changelog

- 2026-09-22T23:05:01+05:30 — Initial version — consolidated citations already scattered across STATUS.md/DB_PATCHING_SCOPE.md/knowledge_staging sources, plus two new candidate sources (dincosman.com's Ansible RAC/GI patching article, and a related Reddit post whose content could not be independently verified — Reddit is unreachable through this session's fetch tooling) — Arvind Regukumar
- 2026-09-22T23:07:11+05:30 — Added cross-link to the new REFERENCES_LEARNINGS.md — Arvind Regukumar
- 2026-09-23T00:42:39+05:30 — Added 3 more dincosman.com/related sources: the 2024 GI-update article (more detailed, 5-playbook GIRU walkthrough — the more actionable of the two dincosman.com articles), the actual GitHub repo behind both articles (confirmed dbru/giru as separate real subdirectories), and the author's Gist profile (mostly general DBA scripts, not patching-specific — noted honestly, not oversold) — Arvind Regukumar
