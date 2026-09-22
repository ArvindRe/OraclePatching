-- Changelog:
--   2026-09-22T15:53:48+05:30 — Initial operational store schema (per-database state) — Arvind Regukumar

-- DB_PATCHING_SCOPE.md component 2, "Operational store (Postgres): per-database
-- state — last patch level, last successful patch date, known issues, Data
-- Guard status. Updated after every scan and every execution."

CREATE TABLE IF NOT EXISTS database_state (
    target              TEXT PRIMARY KEY,       -- inventory hostname/alias
    oracle_home         TEXT NOT NULL,
    last_scanned_at     TIMESTAMPTZ,
    last_scan_result    JSONB,                  -- full scanner output, see agents/scanner/scanner.py
    last_patch_level    TEXT,
    last_patch_date     TIMESTAMPTZ,
    data_guard_role     TEXT,                   -- PRIMARY / PHYSICAL STANDBY / null if not DG
    data_guard_apply_lag TEXT,
    known_issues        TEXT[],
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution_history (
    id                  BIGSERIAL PRIMARY KEY,
    target              TEXT NOT NULL REFERENCES database_state(target),
    procedure_id        TEXT NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ,
    outcome             TEXT NOT NULL,          -- SUCCESS / FAILED / REJECTED_AT_APPROVAL
    audit_log_seq_range TEXT,                   -- e.g. '14-21' — first/last audit/logger.py seq for this run, for cross-reference
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_execution_history_target ON execution_history(target);
