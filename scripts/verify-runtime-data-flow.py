#!/usr/bin/env python3
"""Verify a deployed revision against frozen inputs, live TypeDB and executed AI.

Reads existing stores only. Does not enqueue reasoning, call an LLM, publish a
notification, create a database, or print account values or generated narratives.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python_service"))

import pymysql
from digital_twin.infrastructure.settings import load_local_env
from digital_twin.infrastructure.mysql_monitoring import mysql_settings
from digital_twin.infrastructure.typedb_ontology import TypeDBOntologyGraphRepository
from digital_twin.modules.market_data.domain.rates import interest_rate_facts
from digital_twin.modules.notifications.contracts import normalize_narrative_claims
from digital_twin.modules.portfolio.domain.portfolio import account_snapshot_from_monitor_state
from digital_twin.modules.portfolio.domain.valuation.service import evaluate_valuation_models
from digital_twin.modules.portfolio.domain.valuation.dcf_inputs import DRIVER_DCF_INPUT_VERSION
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_value_match
from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_external_signals_for_ontology


class ReadOnlyGraph(TypeDBOntologyGraphRepository):
    def ensure_database(self, driver):
        # The regular read adapter may create an absent database. An audit must
        # fail instead; all subsequent adapter transactions are READ transactions.
        assert driver.databases.contains(self.database), "Expected existing TypeDB database"


def read(cursor, sql, args=()):
    cursor.execute(sql, args)
    return cursor.fetchall()


def frozen_signals(cursor, run):
    rows = read(cursor, "SELECT payload_json,projection_payload_json FROM monitor_snapshot_history "
                "WHERE account_id=%s AND generated_at=%s", (run["account_id"], run["source_snapshot_at"]))
    if not rows:
        raise LookupError("Exact source snapshot no longer retained")
    source = json.loads(rows[0]["payload_json"])
    projection = json.loads(rows[0]["projection_payload_json"] or "{}")
    assert source["generatedAt"] == projection.get("generatedAt") == run["source_snapshot_at"], "Source clock mismatch"
    before, after = source.get("externalSignals") or {}, projection.get("externalSignals") or {}
    contracts = {row.get("contractVersion") for row in before.get("driverDcfReadiness", {}).values()}
    if contracts != {DRIVER_DCF_INPUT_VERSION}:
        # A restarted worker can finish a queue item frozen by the preceding
        # producer. Preserve that history, but do not call it a new-input test.
        return None
    for group in ("driverDcfInputs", "driverDcfReadiness", "valuationEvidenceFeeds"):
        assert before.get(group, {}) == after.get(group, {}), "Frozen valuation contract was truncated"
    for group in ("companyOverviews", "earningsReports"):
        for symbol, row in before.get(group, {}).items():
            for field in ("earningsEstimates", "multipleObservations", "growthData", "cycleData", "sourceReferences"):
                if field in row:
                    assert row[field] == after.get(group, {}).get(symbol, {}).get(field), "Frozen financial evidence was truncated"
    assert set(before.get("externalDataLineage") or {}) == set(after.get("externalDataLineage") or {}), "Frozen source references missing"
    assert interest_rate_facts(before) == interest_rate_facts(after), "Frozen macro inputs differ"
    # Replay the persisted worker input, after checking preservation against
    # the raw archive above. Capture assembles company knowledge once more.
    return projection


def verify_native(cursor, revision):
    rows = read(cursor, "SELECT run_id,account_id,source_snapshot_at,inference_generation_id,abox_snapshot_id,"
                "graph_database,world_id,engine_deployment_id,release_fingerprint,result_payload_json,created_at "
                "FROM ontology_projection_runs WHERE status='ok' ORDER BY created_at DESC LIMIT 100")
    runs, sources, versions, targets = {}, {}, Counter(), set()
    legacy_sources = 0
    expired_sources = 0
    for row in rows:
        result = json.loads(row["result_payload_json"])
        if not str((result.get("runtimeIdentity") or {}).get("revision") or "").startswith(revision):
            continue
        native = result.get("inferenceBox") or {}
        assert all(native.get(key) for key in ("nativeTypeDbReasoningUsed", "nativeTypeDbReasoningCompleted", "generationAligned")), "Native execution incomplete"
        assert native.get("generationId") == row["inference_generation_id"], "Native generation mismatch"
        assert native.get("sourceAboxSnapshotId") == row["abox_snapshot_id"], "Native ABox mismatch"
        try:
            source = frozen_signals(cursor, row)
        except LookupError:
            expired_sources += 1
            continue
        if source is None:
            legacy_sources += 1
            continue
        sources[row["run_id"]] = source
        runs[row["run_id"]] = row
        targets.update(native.get("targetSymbols") or [])
        versions.update(x.get("contractVersion") for x in source.get("externalSignals", {}).get("driverDcfInputs", {}).values())
    assert runs, "No completed native runs for requested revision in retained sample"
    return runs, sources, {"nativeRunsVerified": len(runs), "targetSubjectsVerified": len(targets),
                           "olderInputContractRunsExcluded": legacy_sources,
                           "expiredSourceRunsExcluded": expired_sources,
                           "frozenSourceClocksVerified": len({r['source_snapshot_at'] for r in runs.values()}),
                           "dcfInputVersions": dict(versions)}


def verify_ai(cursor, runs, sources):
    generations = {row["inference_generation_id"]: row for row in runs.values()}
    rows = read(cursor, "SELECT q.request_id,q.inference_generation_id,q.account_id,q.symbol,a.artifact_gzip,"
                "a.prompt_hash,r.response_json,r.ai_authored,r.publication_contract_passed "
                "FROM ai_inference_requests q JOIN ai_inference_execution_audits a ON a.request_id=q.request_id "
                "JOIN ai_inference_results r ON r.request_id=q.request_id ORDER BY a.created_at DESC LIMIT 100")
    counts = Counter()
    for row in rows:
        run = generations.get(row["inference_generation_id"])
        if not run:
            continue
        audit = json.loads(gzip.decompress(row["artifact_gzip"]))["executionAudit"]
        quality, core = audit.get("ontologyDecisionQuality") or {}, audit.get("decisionCore") or {}
        assert quality.get("inferenceGenerationId") == run["inference_generation_id"], "AI generation mismatch"
        assert quality.get("sourceAboxSnapshotId") == run["abox_snapshot_id"], "AI ABox mismatch"
        assert quality.get("deploymentId") == run["engine_deployment_id"], "AI deployment mismatch"
        assert quality.get("releaseFingerprint") == run["release_fingerprint"], "AI release mismatch"
        assert row["account_id"] == run["account_id"], "AI account mismatch"
        assert all(quality.get(key) for key in ("nativeTypeDbReasoningUsed", "graphTraceComplete", "generationAligned")), "AI native evidence incomplete"
        cases = read(cursor, "SELECT source_abox_snapshot_id,inference_generation_id,account_id,symbol "
                     "FROM investment_subject_decision_cases WHERE ai_request_id=%s", (row["request_id"],))
        assert len(cases) == 1, "AI must reference one frozen subject case"
        case = cases[0]
        assert (case["source_abox_snapshot_id"], case["inference_generation_id"], case["account_id"], case["symbol"]) == (
            run["abox_snapshot_id"], run["inference_generation_id"], run["account_id"], row["symbol"]), "Subject case mismatch"
        prompt = str(audit.get("prompt") or "")
        assert prompt and hashlib.sha256(prompt.encode()).hexdigest() == row["prompt_hash"], "Executed prompt mismatch"
        counts["linkedExecutionsChecked"] += 1
        source = sources[run["run_id"]]
        for key, value in interest_rate_facts(source["externalSignals"]).items():
            if key.startswith("macro") and value not in (None, ""):
                assert core.get("facts", {}).get(key) == value, "AI macro fact differs from frozen source"
                counts["macroFieldsCompared"] += 1
        response = json.loads(row["response_json"])
        if not (row["ai_authored"] and row["publication_contract_passed"]):
            counts["publicationContractFailures"] += 1
            continue
        _, validation = normalize_narrative_claims({"_notificationAiPreparedDecisionCore": core,
            "notificationAiReviewMode": audit.get("reviewMode"), "messageType": "investmentInsight"}, response, writer_kind="ai")
        assert not validation.get("rejectedClaimCount"), "AI claims failed frozen-evidence replay"
        counts["verifiedClaims"] += validation.get("verifiedClaimCount", 0)
        counts["aiExecutionsVerified"] += 1
        counts["companyEvidenceIncluded"] += bool(core.get("companyEvidence"))
    assert counts["linkedExecutionsChecked"], "No completed executions linked to this revision"
    return dict(counts)


def verify_live_graph(cursor, runs, sources):
    counts, kinds, model_cache = Counter(), Counter(), {}
    worlds = {(r["graph_database"], r["world_id"]) for r in runs.values()}
    for database, world in worlds:
        graph = ReadOnlyGraph(address=os.getenv("TYPEDB_ADDRESS", "127.0.0.1:1729"),
            user=os.getenv("TYPEDB_USER", "admin"), password=os.getenv("TYPEDB_PASSWORD", "password"),
            database=database, tls_enabled=os.getenv("TYPEDB_TLS_ENABLED", "0").lower() in {"1", "true", "yes"},
            query_timeout_seconds=20, retry_count=0)
        membership = graph.active_abox_members_clause([("$n", "audit")], world)
        selected = typedb_value_match("$n", "ontology-kind", ["valuation-assessment", "earnings-scenario-observation", "valuation-input-bundle"], "==", "auditKind")
        rows = graph.read_rows("match " + membership + " $n isa ontology-node, has ontology-kind $kind, "
            "has ontology-json $json; " + selected, ["kind", "json"], label="audit.valuation.readback")
        counts["activeRowsRead"] += len(rows)
        for row in rows:
            value = json.loads(row["json"])
            run_id = value.get("projectionRunId")
            if run_id not in runs:
                counts["outsideRequestedRevisionSample"] += 1
                continue
            key = (run_id, value.get("symbol"))
            if key not in model_cache:
                source = sources[run_id]
                snapshot = account_snapshot_from_monitor_state(source)
                position = next((p for p in [*snapshot.positions, *snapshot.watchlist] if p.symbol == key[1]), None)
                assert position, "Live valuation subject missing from exact source"
                prepared_signals = compact_external_signals_for_ontology(snapshot.external_signals)
                model_cache[key] = evaluate_valuation_models(position, prepared_signals, {})
            payload = value.get("payload") or {}
            field = {"earnings-scenario-observation": "epsScenario", "valuation-assessment": "valuationAssessment",
                     "valuation-input-bundle": "valuationBundle"}[row["kind"]]
            # Settings-authored historical models are separate contracts. This
            # replay checks normalized EPS and the repaired driver DCF inputs.
            if field == "valuationAssessment" and payload.get("modelId") != "driver-fcff-dcf":
                continue
            assert payload and any(model.get(field) == payload for model in model_cache[key]), "Live valuation differs from frozen-source recalculation"
            if field == "valuationAssessment":
                assert value.get("valuationDecisionEligible") == bool(payload.get("valuationDecisionEligible")), "Live valuation eligibility mismatch"
            if field == "valuationBundle":
                counts["sourceReferencesCompared"] += len(payload.get("sourceReferences") or [])
            kinds[row["kind"]] += 1
    assert kinds["valuation-assessment"], "No live DCF assessment verified for requested revision"
    assert kinds["earnings-scenario-observation"], "No live EPS scenario verified for requested revision"
    assert kinds["valuation-input-bundle"], "No live valuation input bundle verified for requested revision"
    return {**counts, "exactPayloadsVerified": dict(kinds)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="Deployed Git revision prefix (7-40 hex characters)")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{7,40}", args.revision):
        parser.error("revision must be a lowercase Git hash prefix")
    load_local_env()
    connection = pymysql.connect(**{k: v for k, v in mysql_settings().items() if v != ""},
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=5, read_timeout=20)
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            runs, sources, native = verify_native(cursor, args.revision)
            ai = verify_ai(cursor, runs, sources)
            live = verify_live_graph(cursor, runs, sources)
            passed = bool(ai.get("aiExecutionsVerified")) and not ai.get("publicationContractFailures")
            print(json.dumps({"status": "passed" if passed else "failed-ai-publication", "revision": args.revision, "mode": "read-only-no-send",
                "native": native, "ai": ai, "liveTypeDb": live,
                "limits": "Latest 100 retained successful projections and 100 AI executions; exact linked sources only. Live graph is a later read of active scopes. Unchanged older scopes are counted separately. Financial inputs need not appear in unrelated AI narratives; no new AI call or outcome validation."}, ensure_ascii=False))
            if not passed:
                raise SystemExit(1)
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
