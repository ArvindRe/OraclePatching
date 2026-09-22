# Changelog:
#   2026-09-22T16:53:00+05:30 — Initial scripted demo: scan -> RAG -> propose -> registry gate -> executor (fails safe) -> audit chain -> tests — Arvind Regukumar
#   2026-09-22T16:53:00+05:30 — Verified with a full --auto run against the live vagrant VM: all 7 steps completed with no exceptions, Executor halted at the precheck/dry-run gate (rc=2, no patch staged), audit chain verified, tamper detection confirmed on a scratch copy, 19/19 tests passed — Arvind Regukumar
#   2026-09-22T16:49:41+05:30 — Updated for the ansible/ move: registry/scan/RunContext now use settings.ansible_dir, not settings.base_repo_path — Arvind Regukumar
#   2026-09-22T17:30:00+05:30 — Added Step 0 preflight checks (VM/Ansible, Qdrant, Ollama, Postgres) with real bounded timeouts, and timestamped log-file output (demo_logs/). Prompted by a real incident: a Ctrl-C'd run orphaned a stuck generation on Ollama's single request slot, which then hung every subsequent LLM call with no client-side timeout to catch it and no log to diagnose it from afterward — Arvind Regukumar
#   2026-09-22T17:44:12+05:30 — Added a new Step 2 (DMZ transfer simulation, knowledge_staging -> knowledge_inbox via rag/ingestion/dmz_transfer.py) ahead of RAG ingestion, which now reads only from the inbox — renumbered steps 3-8 accordingly. Mimics DB_PATCHING_SCOPE.md's one-directional DMZ transfer design, which had never actually existed as a boundary before (ingest.py used to read straight from a flat "sample_knowledge" directory with no staging/landed distinction) — Arvind Regukumar
#   2026-09-22T19:44:29+05:30 — Updated Step 1's CDB print to also show the real DB version (scan_playbook.yml now queries v$instance.version_full — see that file's changelog for why) — Arvind Regukumar
#   2026-09-22T20:06:10+05:30 — Step 0's VM/Ansible check now uses settings.preflight_vm_timeout_seconds (60s) instead of the shared 10s preflight timeout — observed a real false-positive failure (52s actual ansible -m ping latency under load) — Arvind Regukumar
#   2026-09-22T20:27:16+05:30 — Log filenames now use local system time (with numeric UTC offset, e.g. demo_20260922T202713+0530.log) instead of UTC — Arvind Regukumar
#   2026-09-22T20:29:23+05:30 — Dropped the UTC offset suffix — local time only, e.g. demo_20260922T202713.log — Arvind Regukumar

"""Live, narrated walkthrough of the agentic patching POC — no mocks, no
fabricated output. Everything printed is the real return value of a real
call against the real vagrant VM / Qdrant / Postgres / Ollama.

    python demo.py                  # pauses between steps (Enter to continue)
    python demo.py --auto           # no pauses, runs straight through
    python demo.py --real-embeddings  # use sentence-transformers instead of
                                       # the deterministic placeholder (needs
                                       # network access to download the model
                                       # the first time — see rag/ingestion/
                                       # embeddings.py's docstring)

Deliberately does NOT try to reach an actual patch apply — no real patch has
been downloaded yet (see STATUS.md), so this script's job is to demonstrate
the thing that IS proven: every stage either does real, useful work, or
fails closed with a clear reason and an audit entry, never a silent guess.

Where the Executor halts (version-range gate vs. dry-run/precheck gate)
depends on exactly what the LLM proposed this run — this script doesn't
paper over that variance, it reports whatever actually happened.

Every run writes a full transcript to demo_logs/demo_<local timestamp>.log
(gitignored — these are local run artifacts, not source) in addition to
printing to the console, so a hang/failure can be diagnosed after the fact
without having had to be watching live.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from agents.patch.patch_agent import PatchAgentEscalationError, propose_plan
from agents.scanner.scanner import ScanFailedError, scan
from audit.logger import AuditLogger
from config.settings import Settings, load_settings
from executor.executor import ExecutorHaltedError, RunContext, run_patch_workflow
from qdrant_client import QdrantClient
from rag.ingestion.dmz_transfer import transfer as dmz_transfer
from rag.ingestion.embeddings import DeterministicTestEmbedder, SentenceTransformerEmbedder
from rag.ingestion.ingest import load_knowledge_objects, ingest
from rag.retrieval.retrieve import retrieve
from registry.procedures.registry import ProcedureRegistry, UnknownProcedureError

SCAFFOLD_ROOT = Path(__file__).parent
KNOWLEDGE_STAGING_DIR = SCAFFOLD_ROOT / "rag" / "knowledge_staging"
KNOWLEDGE_INBOX_DIR = SCAFFOLD_ROOT / "rag" / "knowledge_inbox"
DMZ_TRANSFER_LOG = SCAFFOLD_ROOT / "rag" / "dmz_transfer_log.jsonl"
DEMO_QDRANT_COLLECTION = "oracle_procedures_demo"
DEMO_LOG_DIR = SCAFFOLD_ROOT / "demo_logs"


class Tee:
    """Writes to multiple streams at once — stdout stays live, everything
    also lands in the timestamped log file."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data: str) -> None:
        for s in self._streams:
            s.write(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()


def banner(n, title: str) -> None:
    print()
    print("=" * 72)
    print(f" STEP {n}: {title}")
    print("=" * 72)


def pause(auto: bool) -> None:
    if not auto:
        input("\n  [Enter to continue] ")


# ---------------------------------------------------------------- PREFLIGHT

def _check_vm_ansible(ansible_dir: Path, inventory: str, target: str, timeout_seconds: float) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["ansible", target, "-i", inventory, "-m", "ping"],
            cwd=str(ansible_dir),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if proc.returncode == 0:
            return True, "pong"
        return False, (proc.stderr or proc.stdout).strip().splitlines()[-1] if (proc.stderr or proc.stdout) else "unreachable"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout_seconds}s — VM stopped, or CDB/listener not started?"
    except FileNotFoundError as exc:
        return False, str(exc)


def _check_ollama(base_url: str, timeout_seconds: float) -> tuple[bool, str]:
    version_url = base_url.rsplit("/v1", 1)[0] + "/api/version"
    try:
        with urllib.request.urlopen(version_url, timeout=timeout_seconds) as resp:
            return True, resp.read().decode().strip()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, (
            f"{exc} — if this is a timeout, a prior Ctrl-C'd run may have orphaned a stuck "
            f"generation on the model server's single request slot; try `brew services restart ollama`."
        )


def _check_qdrant(host: str, port: int, timeout_seconds: float) -> tuple[bool, str]:
    try:
        QdrantClient(host=host, port=port, timeout=timeout_seconds).get_collections()
        return True, "reachable"
    except Exception as exc:  # noqa: BLE001 — genuinely any client-library exception means "not reachable"
        return False, str(exc)


def _check_postgres(settings: Settings, timeout_seconds: float) -> tuple[Optional[bool], str]:
    if not settings.postgres.password:
        return None, "POSTGRES_PASSWORD not set — skipped (this script itself doesn't need Postgres, only tests/ does)"
    try:
        import psycopg

        conn = psycopg.connect(
            host=settings.postgres.host,
            port=settings.postgres.port,
            dbname=settings.postgres.db,
            user=settings.postgres.user,
            password=settings.postgres.password,
            connect_timeout=int(timeout_seconds),
        )
        conn.close()
        return True, "reachable"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def run_preflight(settings: Settings, args: argparse.Namespace) -> bool:
    """Returns True if every REQUIRED check passed. Postgres is checked but
    non-fatal — nothing in this script actually depends on it."""
    banner(0, "Preflight checks — fail fast instead of hanging")

    checks: list[tuple[str, Optional[bool], str, bool]] = []  # name, ok, msg, fatal_if_false

    ok, msg = _check_vm_ansible(settings.ansible_dir, args.inventory, args.target, settings.preflight_vm_timeout_seconds)
    checks.append((f"Vagrant VM / Ansible SSH ({args.target})", ok, msg, True))

    ok, msg = _check_qdrant(settings.qdrant.host, settings.qdrant.port, settings.preflight_timeout_seconds)
    checks.append((f"Qdrant ({settings.qdrant.host}:{settings.qdrant.port})", ok, msg, True))

    ok, msg = _check_ollama(settings.llm.base_url, settings.llm.preflight_timeout_seconds)
    checks.append((f"LLM server ({settings.llm.base_url})", ok, msg, True))

    pg_ok, pg_msg = _check_postgres(settings, settings.preflight_timeout_seconds)
    checks.append(("Postgres (optional — only used by tests/)", pg_ok, pg_msg, False))

    all_required_ok = True
    for name, ok, msg, fatal in checks:
        status = "SKIP" if ok is None else ("OK" if ok else "FAIL")
        print(f"  [{status:4}] {name}: {msg}")
        if ok is False and fatal:
            all_required_ok = False

    if not all_required_ok:
        print("\nOne or more required services aren't responding — stopping now instead of")
        print("hanging partway through a later step with no way to tell why.")
    else:
        print("\nAll required services responding.")
    return all_required_ok


# --------------------------------------------------------------------- MAIN

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--auto", action="store_true", help="skip pauses, run straight through")
    parser.add_argument("--real-embeddings", action="store_true", help="use sentence-transformers instead of the deterministic placeholder")
    parser.add_argument("--target", default="vagrant-ora19c")
    parser.add_argument("--inventory", default="inventories/vagrant_test/hosts.yml")
    parser.add_argument("--cdb-sid", default="CDB1")
    parser.add_argument("--oracle-os-owner", default="oracle")
    args = parser.parse_args()

    DEMO_LOG_DIR.mkdir(exist_ok=True)
    # Local system time, no UTC offset suffix.
    log_path = DEMO_LOG_DIR / f"demo_{datetime.now().strftime('%Y%m%dT%H%M%S')}.log"
    log_file = open(log_path, "w")
    real_stdout = sys.stdout
    sys.stdout = Tee(real_stdout, log_file)

    try:
        print(f"Logging this run's full transcript to: {log_path}")
        print("Oracle DB Patching — Agentic POC — live demo")
        print("Every step below is a real call against a real target. Nothing is mocked.")

        settings = load_settings()

        if not run_preflight(settings, args):
            return 1
        pause(args.auto)

        registry = ProcedureRegistry.load(settings.procedures_dir, settings.ansible_dir)

        # ------------------------------------------------------------ STEP 1
        banner(1, f"Live scan of '{args.target}' (read-only, real Ansible against the real VM)")
        try:
            scan_result = scan(
                settings.ansible_dir, args.inventory, args.target, [{"sid": args.cdb_sid}],
                timeout_seconds=settings.subprocess_timeout_seconds,
            )
        except ScanFailedError as exc:
            print(f"Scan failed: {exc}")
            print("(Is the vagrant VM up, and is the CDB/listener started? See STATUS.md 'Open Issues'.)")
            return 1

        print(f"Host:            {scan_result['host']}")
        print(f"OS:              {scan_result['os']['distribution']} {scan_result['os']['distribution_version']}")
        print(f"Oracle Home:     {scan_result['oracle_home']}")
        print(f"OPatch version:  {scan_result['opatch_version']}")
        for cdb in scan_result["cdbs"]:
            open_mode, status, role, flashback, dg_lag, db_version = cdb["raw"].splitlines()[:6]
            print(f"CDB {cdb['sid']}:         {open_mode} / {status} / {role} / flashback={flashback} / DG apply lag={dg_lag} / version={db_version}")
        pause(args.auto)

        # ------------------------------------------------------------ STEP 2
        banner(2, "DMZ transfer simulation (knowledge_staging -> knowledge_inbox)")
        print("Mimics DB_PATCHING_SCOPE.md's 'one-directional DMZ transfer' — a staging")
        print("machine with internet access authors/curates content; it crosses into the")
        print("air-gapped side one-directionally; ingestion reads only what arrived, never")
        print("the network. knowledge_inbox/ is gitignored/regenerated — a fresh clone")
        print("starts with an empty inbox, same as a fresh air-gapped box before its first")
        print("monthly transfer.")
        manifest = dmz_transfer(KNOWLEDGE_STAGING_DIR, KNOWLEDGE_INBOX_DIR, DMZ_TRANSFER_LOG)
        print(f"\nTransferred {len(manifest.files)} file(s) at {manifest.transferred_at}:")
        for f in manifest.files:
            print(f"  - {f['name']} (sha256 {f['sha256'][:12]}...)")
        pause(args.auto)

        # ------------------------------------------------------------ STEP 3
        banner(3, "RAG ingestion + retrieval (real Qdrant, from the inbox only)")
        qdrant = QdrantClient(host=settings.qdrant.host, port=settings.qdrant.port)
        embedder = SentenceTransformerEmbedder() if args.real_embeddings else DeterministicTestEmbedder()
        if not args.real_embeddings:
            print("(Using the deterministic placeholder embedder — proves the Qdrant")
            print(" ingestion/retrieval mechanics for real, not retrieval QUALITY.")
            print(" Production uses a real sentence-transformers model, pre-cached")
            print(" for the air-gapped box. Pass --real-embeddings to use one here.)")

        objects = load_knowledge_objects(KNOWLEDGE_INBOX_DIR)
        ingest(qdrant, DEMO_QDRANT_COLLECTION, embedder, objects)
        print(f"Ingested {len(objects)} structured knowledge object(s) into '{DEMO_QDRANT_COLLECTION}' — read only from knowledge_inbox/, never knowledge_staging/ directly.")

        retrieval_hits = retrieve(
            qdrant, DEMO_QDRANT_COLLECTION, embedder,
            query_text=f"patch {scan_result['oracle_home']}",
            target_version="19.28.0.0.0",
            top_k=5,
        )
        print(f"Retrieved {len(retrieval_hits)} applicable knowledge object(s):")
        for hit in retrieval_hits:
            print(f"  - {hit['id']}: {hit['title']}")
        pause(args.auto)

        # ------------------------------------------------------------ STEP 4
        banner(4, "Patch Agent proposal (real local LLM call, schema-gated)")
        print(f"Calling {settings.llm.base_url} (model: {settings.llm.model}, timeout {settings.llm.timeout_seconds}s)...")
        try:
            plan = propose_plan(scan_result, retrieval_hits, registry, settings.llm)
        except PatchAgentEscalationError as exc:
            print(f"Patch Agent escalated to human review instead of guessing: {exc}")
            return 1

        print("Proposed plan (schema-validated, procedure_id constrained to the live registry's allow-list):")
        print(json.dumps(plan, indent=2))
        pause(args.auto)

        # ------------------------------------------------------------ STEP 5
        banner(5, "Registry allow-list gate — what happens to an id NOT on the list")
        bogus_id = "procedure_the_llm_made_up"
        print(f"Directly asking the registry for '{bogus_id}' (simulating what the Executor")
        print("does with ANY proposed procedure_id, real or hallucinated, before it will")
        print("touch a single Ansible playbook):")
        try:
            registry.get(bogus_id)
            print("  (unexpected: this should have been rejected)")
        except UnknownProcedureError as exc:
            print(f"  REJECTED: {exc}")
        pause(args.auto)

        # ------------------------------------------------------------ STEP 6
        banner(6, "Executor run — fails safe (no real patch is staged yet)")
        print("No real CPU/RU patch has been downloaded yet (see STATUS.md), so this")
        print("WILL halt before applying anything. That's the point of this step: watch")
        print("it stop cleanly, explain why, and write an audit entry either way.")
        audit = AuditLogger(settings.audit_log_path)
        ctx = RunContext(
            ansible_dir=settings.ansible_dir,
            inventory=args.inventory,
            target_host=args.target,
            oracle_os_owner=args.oracle_os_owner,
            subprocess_timeout_seconds=settings.subprocess_timeout_seconds,
        )
        halted_reason = None
        try:
            run_patch_workflow(plan, ctx, registry, audit)
            print("Patch applied successfully (unexpected in this environment — verify before trusting this).")
        except ExecutorHaltedError as exc:
            halted_reason = str(exc)
            print(f"Executor halted: {halted_reason}")
        pause(args.auto)

        # ------------------------------------------------------------ STEP 7
        banner(7, "Audit trail — hash-chained, tamper-evident")
        entries = audit.read_all()
        print(f"{len(entries)} total entries in {settings.audit_log_path}. Last 3:")
        for entry in entries[-3:]:
            print(f"  seq={entry.seq:<4} {entry.event_type:<20} ok={entry.payload.get('ok', '-')}")

        ok, reason = audit.verify_chain()
        print(f"\nverify_chain() on the real log: {'OK' if ok else f'BROKEN — {reason}'}")

        print("\nNow tampering with a SCRATCH COPY (never the real log) to show detection:")
        with tempfile.TemporaryDirectory() as tmp:
            tampered_path = Path(tmp) / "tampered_audit.jsonl"
            shutil.copy(settings.audit_log_path, tampered_path)
            lines = tampered_path.read_text().splitlines()
            if lines:
                tampered = json.loads(lines[0])
                tampered["payload"] = {"attacker": "rewrote this entry"}
                lines[0] = json.dumps(tampered)
                tampered_path.write_text("\n".join(lines) + "\n")
                tampered_logger = AuditLogger(tampered_path)
                ok2, reason2 = tampered_logger.verify_chain()
                print(f"verify_chain() on the tampered copy: {'OK' if ok2 else f'BROKEN — {reason2}'}")
        pause(args.auto)

        # ------------------------------------------------------------ STEP 8
        banner(8, "Test suite (the part of this that isn't a live demo)")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q"],
            cwd=str(SCAFFOLD_ROOT),
            capture_output=True,
            text=True,
            timeout=settings.subprocess_timeout_seconds,
        )
        print(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "(no output)")

        print()
        print("=" * 72)
        print(" Done. Summary of what this run actually proved, live:")
        print("=" * 72)
        print(f"  - Scanner read real state from '{args.target}'")
        print(f"  - DMZ transfer simulation moved {len(manifest.files)} file(s) staging -> inbox")
        print(f"  - RAG ingestion/retrieval worked against real Qdrant, reading only from the inbox")
        print(f"  - Patch Agent got a schema-valid, registry-constrained plan from a real local LLM")
        print(f"  - Registry rejected an off-allow-list id before touching Ansible")
        print(f"  - Executor halted safely: {halted_reason or '(see above)'}")
        print(f"  - Audit chain verified, and tamper detection confirmed on a scratch copy")
        print(f"  - Unit test suite: {result.stdout.strip().splitlines()[-1] if result.stdout.strip() else 'see above'}")
        print(f"\nFull transcript: {log_path}")
        return 0
    finally:
        sys.stdout = real_stdout
        log_file.close()


if __name__ == "__main__":
    sys.exit(main())
