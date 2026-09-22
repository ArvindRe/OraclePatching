# Changelog:
#   2026-09-22T15:53:48+05:30 — Initial end-to-end CLI: scan -> retrieve -> propose -> approve -> execute — Arvind Regukumar

"""CLI entry point wiring the full agentic pipeline together.

    python run_patch.py --target dbhost01 --inventory ../inventories/vagrant_test/hosts.yml \\
        --cdbs '[{"sid": "CDB1"}]' --oracle-os-owner oracle

This is the only place all seven DB_PATCHING_SCOPE.md components meet. Every
component it calls fails closed on its own (UnknownProcedureError,
PatchAgentEscalationError, ExecutorHaltedError) — this script's job is only to
sequence them and make sure a failure anywhere stops the whole run rather than
silently continuing to the next stage.

STATUS.md's own next actions still apply here as much as to the base repo:
this has not been run against a live target. Treat every component as
"structurally wired," not "proven," until it has.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from agents.patch.patch_agent import PatchAgentEscalationError, propose_plan
from agents.scanner.scanner import ScanFailedError, scan
from audit.logger import AuditLogger
from config.settings import load_settings
from executor.executor import ExecutorHaltedError, RunContext, run_patch_workflow
from rag.ingestion.embeddings import SentenceTransformerEmbedder
from rag.ingestion.operational_store import record_execution, upsert_scan_result
from rag.retrieval.retrieve import retrieve
from registry.procedures.registry import ProcedureRegistry
from qdrant_client import QdrantClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True, help="inventory hostname/alias to scan and (if approved) patch")
    parser.add_argument("--inventory", required=True, help="path to an Ansible inventory (hosts.yml)")
    parser.add_argument("--cdbs", required=True, help='JSON list, e.g. \'[{"sid": "CDB1"}]\'')
    parser.add_argument("--oracle-os-owner", default="oracle")
    parser.add_argument("--category", default=None, help="optional RAG category filter, e.g. data_guard")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()
    audit = AuditLogger(settings.audit_log_path)
    started_at = datetime.now(timezone.utc)

    registry = ProcedureRegistry.load(settings.procedures_dir, settings.base_repo_path)

    try:
        cdbs = json.loads(args.cdbs)
    except json.JSONDecodeError as exc:
        print(f"--cdbs must be valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        scan_result = scan(settings.base_repo_path, args.inventory, args.target, cdbs)
    except ScanFailedError as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 1
    audit.append("scan", scan_result)
    upsert_scan_result(settings.postgres, args.target, scan_result["oracle_home"], scan_result)

    qdrant = QdrantClient(host=settings.qdrant.host, port=settings.qdrant.port)
    embedder = SentenceTransformerEmbedder()
    query_text = f"patch {scan_result['oracle_home']} current opatch {scan_result.get('opatch_version', '')}"
    retrieval_hits = retrieve(qdrant, settings.qdrant.collection, embedder, query_text, category=args.category)
    audit.append("rag_retrieval", {"query": query_text, "hit_count": len(retrieval_hits)})

    try:
        plan = propose_plan(scan_result, retrieval_hits, registry, settings.llm)
    except PatchAgentEscalationError as exc:
        audit.append("validation_result", {"stage": "patch_agent", "ok": False, "reason": str(exc)})
        print(f"Patch Agent could not produce a valid plan — escalating to human review: {exc}", file=sys.stderr)
        return 1

    ctx = RunContext(
        base_repo_path=settings.base_repo_path,
        inventory=args.inventory,
        target_host=args.target,
        oracle_os_owner=args.oracle_os_owner,
    )

    outcome = "FAILED"
    try:
        run_patch_workflow(plan, ctx, registry, audit)
        outcome = "SUCCESS"
        return 0
    except ExecutorHaltedError as exc:
        outcome = "REJECTED_AT_APPROVAL" if "did not confirm" in str(exc) else "FAILED"
        print(f"Run halted: {exc}", file=sys.stderr)
        return 1
    finally:
        record_execution(
            settings.postgres,
            args.target,
            plan["procedure_id"],
            started_at,
            datetime.now(timezone.utc),
            outcome,
        )


if __name__ == "__main__":
    sys.exit(main())
