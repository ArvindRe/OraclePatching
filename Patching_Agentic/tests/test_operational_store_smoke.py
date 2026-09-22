# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial Postgres operational store smoke test — Arvind Regukumar
#   2026-09-22T15:53:48+05:30 — Fixed test not cleaning up its row between runs, causing a false failure on rerun — Arvind Regukumar

"""Requires a running Postgres (docker compose up -d postgres) with
POSTGRES_PASSWORD matching what the container was started with. Skipped if
unreachable.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from config.settings import PostgresSettings
from rag.ingestion.operational_store import get_database_state, record_execution, upsert_scan_result


@pytest.fixture
def settings():
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        pytest.skip("POSTGRES_PASSWORD not set")
    s = PostgresSettings(host="localhost", port=5432, db="oracle_patching_ops", user="patching_agent", password=password)
    try:
        import psycopg

        psycopg.connect(host=s.host, port=s.port, dbname=s.db, user=s.user, password=s.password).close()
    except Exception as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
    return s


def _cleanup(settings, target):
    import psycopg

    with psycopg.connect(host=settings.host, port=settings.port, dbname=settings.db, user=settings.user, password=settings.password) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM execution_history WHERE target = %s", (target,))
            cur.execute("DELETE FROM database_state WHERE target = %s", (target,))
        conn.commit()


def test_upsert_scan_then_record_execution_updates_last_patch_level(settings):
    target = "test_smoke_dbhost"
    _cleanup(settings, target)  # leftover row from a prior run would falsify the "starts unpatched" assertion below
    upsert_scan_result(settings, target, "/u01/app/oracle/product/19.0.0/dbhome_1", {"opatch_version": "12.2.0.1.42"})

    state = get_database_state(settings, target)
    assert state is not None
    assert state["oracle_home"] == "/u01/app/oracle/product/19.0.0/dbhome_1"
    assert state["last_patch_level"] is None

    now = datetime.now(timezone.utc)
    record_execution(settings, target, "oracle_19c_ru_patch", now, now, "SUCCESS")

    state_after = get_database_state(settings, target)
    assert state_after["last_patch_level"] == "oracle_19c_ru_patch"

    _cleanup(settings, target)
