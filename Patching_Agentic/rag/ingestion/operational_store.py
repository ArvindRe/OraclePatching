# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial Postgres operational-store read/write helpers — Arvind Regukumar

"""Read/write helpers for the Postgres operational store (postgres_schema.sql).

Updated after every scan (agents/scanner) and every execution (executor.py) —
this is what lets the Patch Agent's prompt include "last successful patch
date" / "known issues" context instead of only the live scan.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import psycopg

from config.settings import PostgresSettings


def _connect(settings: PostgresSettings):
    return psycopg.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.db,
        user=settings.user,
        password=settings.password,
    )


def upsert_scan_result(settings: PostgresSettings, target: str, oracle_home: str, scan_result: dict[str, Any]) -> None:
    with _connect(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO database_state (target, oracle_home, last_scanned_at, last_scan_result, updated_at)
            VALUES (%s, %s, now(), %s, now())
            ON CONFLICT (target) DO UPDATE SET
                oracle_home = EXCLUDED.oracle_home,
                last_scanned_at = EXCLUDED.last_scanned_at,
                last_scan_result = EXCLUDED.last_scan_result,
                updated_at = now()
            """,
            (target, oracle_home, json.dumps(scan_result)),
        )
        conn.commit()


def record_execution(
    settings: PostgresSettings,
    target: str,
    procedure_id: str,
    started_at: datetime,
    finished_at: Optional[datetime],
    outcome: str,
    audit_log_seq_range: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    with _connect(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO execution_history
                (target, procedure_id, started_at, finished_at, outcome, audit_log_seq_range, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (target, procedure_id, started_at, finished_at, outcome, audit_log_seq_range, notes),
        )
        if outcome == "SUCCESS":
            cur.execute(
                """
                UPDATE database_state
                SET last_patch_level = %s, last_patch_date = %s, updated_at = now()
                WHERE target = %s
                """,
                (procedure_id, finished_at, target),
            )
        conn.commit()


def get_database_state(settings: PostgresSettings, target: str) -> Optional[dict[str, Any]]:
    with _connect(settings) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT * FROM database_state WHERE target = %s", (target,))
        return cur.fetchone()
