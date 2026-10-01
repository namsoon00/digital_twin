#!/usr/bin/env python3
"""Read-only local source/graph/AI contract replay. Never calls vendors or LLMs."""
import json
import gzip
import hashlib
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python_service"))

import pymysql
from digital_twin.infrastructure.settings import load_local_env
from digital_twin.infrastructure.mysql_monitoring import mysql_settings
from digital_twin.modules.market_data.domain.rates import interest_rate_facts
from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_external_signals_for_ontology
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_external_abox import add_portfolio_macro_and_cross_asset_concepts
from digital_twin.modules.decisions.domain.notification_ai_context_router import _relation_facts
from digital_twin.modules.market_data.domain.external_dataset_catalog import external_dataset_catalog
from digital_twin.modules.portfolio.domain.portfolio import Position, account_snapshot_from_monitor_state
from digital_twin.modules.reasoning.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.modules.portfolio.domain.valuation.dcf import driver_dcf_valuation_row
from digital_twin.modules.portfolio.domain.valuation.dcf_inputs import _latest_verified_annual, FINANCIAL_INPUT_METRICS
from digital_twin.modules.portfolio.domain.valuation.projection import add_position_valuation_concepts
from digital_twin.modules.notifications.contracts import normalize_narrative_claims


# Routes reviewed against the actual consumers, not inferred from field names.
# Archive/discovery metadata is explicitly separate from admitted model evidence.
PAYLOAD_ROUTES = {
    "equityQuotes": "quote normalization / price ABox",
    "cryptoMarkets": "macro crypto ABox / exposure",
    "macro": "macro ABox / rate facts / AI core",
    "fxRates": "FX ABox / currency conversion",
    "marketIndices": "official index ABox / market context",
    "issuerIrDocuments": "verified document research / company report evidence",
    "officialRelease": "economic calendar release verification (no action authority)",
    "officialStatistics": "calendar latest statistics (not first-release vintage)",
    "dartDisclosures": "company financial normalization / disclosure ABox",
    "dartXbrlFacts": "company financial normalization / report contracts",
    "secFilings": "company financial normalization / filing ABox / research",
    "companyKnowledge": "canonical company ABox / valuation / AI evidence",
    "corporateActions": "corporate action ABox / lifecycle",
    "securityMaster": "security identity ABox",
    "officialDailyPrices": "completed-session price ABox (not realtime quote)",
    "companyOverviews": "consensus and multiple inputs / company ABox",
    "earningsReports": "earnings ABox / per-share valuation inputs",
    "yfinanceData": "normalized financials, valuation, option summaries; raw modules retained separately",
    "sourceArchive": "audit archive; intentionally excluded from inference",
}
ARCHIVE_ONLY_YFINANCE_MODULES = (
    "news", "recommendations", "recommendationsSummary", "upgradesDowngrades",
    "institutionalHolders", "mutualfundHolders", "majorHolders",
    "insiderTransactions", "insiderPurchases", "insiderRosterHolders",
)


def verify_inventory(cursor):
    cursor.execute("SELECT dataset_id,payload_json FROM external_fact_current")
    rows = cursor.fetchall()
    counts, groups, archive_modules = Counter(), {}, Counter()
    for row in rows:
        dataset = row["dataset_id"]
        counts[dataset] += 1
        payload = json.loads(row["payload_json"])
        groups.setdefault(dataset, set()).update(payload)
        for vendor in (payload.get("yfinanceData") or {}).values():
            archive_modules.update(key for key in ARCHIVE_ONLY_YFINANCE_MODULES if vendor.get(key))
    catalog = external_dataset_catalog()
    assert not set(counts) - set(catalog), "Unregistered retained dataset"
    assert not set().union(*groups.values()) - set(PAYLOAD_ROUTES), "Unreviewed payload group"
    inventory = [{"dataset": key, "facts": counts[key],
                  "routes": {group: PAYLOAD_ROUTES[group] for group in sorted(groups.get(key, []))}}
                 for key in sorted(catalog)]
    return {"registeredDatasets": len(catalog), "retainedDatasets": len(counts),
            "facts": len(rows), "datasets": inventory,
            "archiveOnlyYfinanceModuleFactCounts": dict(archive_modules),
            "archiveOnlyModules": ["yfinance.news.news: cached discovery metadata has no direct ABox consumer; independent news collector verifies articles",
                                   "raw vendor response fields beyond normalized contracts: retained for audit, not automatically admitted to AI"]}


def verify_ai_execution(cursor):
    cursor.execute("SELECT a.artifact_gzip,a.prompt_hash,r.response_json,a.created_at "
                   "FROM ai_inference_execution_audits a JOIN ai_inference_results r "
                   "ON a.request_id=r.request_id ORDER BY a.created_at DESC LIMIT 100")
    counts, routes = Counter(), Counter()
    latest = {}
    for row in cursor.fetchall():
        audit = json.loads(gzip.decompress(row["artifact_gzip"]))["executionAudit"]
        response = json.loads(row["response_json"])
        core = audit.get("decisionCore") or {}
        counts["retainedAuditResultPairs"] += 1
        prompt = str(audit.get("prompt") or "")
        if prompt:
            assert hashlib.sha256(prompt.encode()).hexdigest() == row["prompt_hash"], "Executed prompt hash mismatch"
            counts["verifiedPromptHashes"] += 1
        else:
            assert not row["prompt_hash"] and (audit.get("fallback") or {}).get("used"), "Unexplained missing prompt"
            counts["fallbackWithoutPrompt"] += 1
        routes[str((audit.get("contextRouting") or {}).get("version") or "missing")] += 1
        if response.get("narrativeClaims"):
            _, validation = normalize_narrative_claims({
                "_notificationAiPreparedDecisionCore": core,
                "notificationAiReviewMode": audit.get("reviewMode"), "messageType": "investmentInsight",
            }, response, writer_kind="ai")
            assert not validation.get("rejectedClaimCount"), "Retained narrative failed evidence replay"
            counts["verifiedNarrativeClaims"] += validation.get("verifiedClaimCount", 0)
        if not latest:
            quality = audit.get("ontologyDecisionQuality") or {}
            latest = {"createdAt": row["created_at"], "routingVersion": (audit.get("contextRouting") or {}).get("version"),
                      "nativeTypeDbReasoningUsed": quality.get("nativeTypeDbReasoningUsed"),
                      "graphTraceComplete": quality.get("graphTraceComplete"), "generationAligned": quality.get("generationAligned"),
                      "macroFactCount": sum(key.startswith("macro") for key in core.get("facts") or {}),
                      "publicationStatus": (audit.get("claimPublication") or {}).get("status")}
    cursor.execute("SELECT result_payload_json,created_at FROM ontology_projection_runs WHERE status='ok' ORDER BY created_at DESC LIMIT 5")
    native = []
    for row in cursor.fetchall():
        result = json.loads(row["result_payload_json"])
        inference = result.get("inferenceBox") or {}
        assert inference.get("generationAligned") and inference.get("nativeTypeDbReasoningCompleted"), "Successful projection lacks aligned native execution"
        native.append({"createdAt": row["created_at"], "runtimeRevision": (result.get("runtimeIdentity") or {}).get("revision"),
                       **{key: inference.get(key) for key in ("nativeTypeDbReasoningUsed", "nativeTypeDbReasoningCompleted", "generationAligned", "traceCount")}})
    return {**counts, "routingVersions": dict(routes), "latest": latest, "retainedNativeRuns": native,
            "scope": "Retained executions and claim evidence replay; no new model call or trade-outcome validation"}


def verify_snapshot(snapshot):
    source = snapshot.get("externalSignals") or {}
    projected = compact_external_signals_for_ontology(source)
    before, after = interest_rate_facts(source), interest_rate_facts(projected)
    assert before == after, "Rate facts changed during projection"
    ai = _relation_facts({"relationFacts": after}, [], [])
    for key, value in before.items():
        if key.startswith("macro") and value not in (None, ""):
            assert ai.get(key) == value, "Rate value or source clock missing from AI core"
    graph = PortfolioOntology("read-only-verification")
    add_portfolio_macro_and_cross_asset_concepts(graph, graph.portfolio_id, projected)
    macro = source.get("macro") or {}
    series = macro.get("series") or {}
    count = 0
    for node in graph.entities:
        original = series.get(node.properties.get("seriesId"))
        if not isinstance(original, dict):
            continue
        count += 1
        for field in ("deltaBp", "delta5dBp", "delta20dBp", "deltaPct", "yearOverYearPct", "unit", "yearAgoPeriod"):
            value = original.get(field)
            if value is None or value == "":
                continue
            expected = round(value, 4) if isinstance(value, (int, float)) else value
            assert node.properties.get(field) == expected, "Source measurement missing from ABox"
    assert count == sum(isinstance(row, dict) for row in series.values()), "Source macro series missing from ABox"
    expected_clock = macro.get("yieldSpreadObservationDate") or ""
    for node in graph.entities:
        if node.kind == "yield-curve":
            assert node.properties.get("sourceAsOf") == expected_clock, "Spread inherited another source clock"
    readiness = source.get("driverDcfReadiness") or {}
    annual_missing = Counter()
    annual_selected = 0
    for company in (source.get("companyKnowledge") or {}).values():
        annual, assessment = _latest_verified_annual(company)
        if assessment.get("eligible"):
            annual_selected += 1
            annual_missing.update(key for key in FINANCIAL_INPUT_METRICS if annual.get(key) is None)
    lineage = source.get("externalDataLineage") or {}
    assert set(lineage) == set(projected.get("externalDataLineage") or {}), "Source revision lineage truncated"
    eps_count = 0
    for group in ("companyOverviews", "earningsReports"):
        for symbol, original in (source.get(group) or {}).items():
            compact = (projected.get(group) or {}).get(symbol) or {}
            for field in ("earningsEstimates", "multipleObservations", "growthData", "cycleData", "latestAnnual"):
                if field in original:
                    assert original[field] == compact.get(field), "Normalized valuation evidence changed during projection"
            eps_count += len(original.get("earningsEstimates") or [])
    dcf_count = 0
    for symbol, bundle in (source.get("driverDcfInputs") or {}).items():
        assert bundle == projected.get("driverDcfInputs", {}).get(symbol), "DCF contract changed during projection"
        position = Position(symbol, "audit-subject", currency=bundle.get("currency") or "")
        original = driver_dcf_valuation_row(position, source, {})
        compact = driver_dcf_valuation_row(position, projected, {})
        assert original == compact, "DCF result changed during projection"
        graph = PortfolioOntology("read-only-valuation")
        add_position_valuation_concepts(graph, "stock:" + symbol, position,
                                        {"driverDcfInputs": {symbol: bundle}}, {})
        assessments = [node for node in graph.entities if node.kind == "valuation-assessment"]
        assert assessments, "Retained DCF bundle missing from ABox"
        if not original.get("valuationDecisionEligible"):
            assert all(not node.properties.get("valuationDecisionEligible") for node in assessments), "Reference DCF gained action eligibility"
        dcf_count += 1
    hydrated = account_snapshot_from_monitor_state(snapshot)
    assert hydrated, "Account snapshot cannot be reconstructed"
    positions = [*hydrated.positions, *hydrated.watchlist]
    graph = build_portfolio_ontology(positions, hydrated.portfolio, external_signals=projected,
        portfolio_id="read-only-verification", runtime_context={"asOf": hydrated.generated_at},
        include_tbox=False, include_presentation=False, include_derived_decision_items=False)
    stock_facts = {node.properties.get("symbol"): node.properties for node in graph.entities if node.kind == "stock"}
    position_count = 0
    for position in positions:
        if position.is_cash():
            continue
        values = stock_facts.get(position.symbol)
        assert values, "Position or watchlist subject missing from ABox"
        for source_field, target_field in (
            ("current_price", "currentPrice"), ("quantity", "quantity"),
            ("market_value", "marketValue"), ("profit_loss_rate", "profitLossRate"),
            ("currency", "currency"), ("ma20", "ma20"), ("ma60", "ma60"),
        ):
            assert getattr(position, source_field) == values.get(target_field), "Position value changed during ABox assembly"
        position_count += 1
    return {"macroSeriesVerified": count,
            "positionAndWatchlistRowsVerified": position_count,
            "aboxEntities": len(graph.entities), "aboxRelations": len(graph.relations),
            "sourceReferencesPreserved": len(lineage), "epsRowsPreserved": eps_count,
            "dcfBundlesAndAboxAssessmentsVerified": dcf_count,
            "selectedAnnualReports": annual_selected, "selectedAnnualMissingMetrics": dict(annual_missing),
            "dcfStates": dict(Counter(str(row.get("status")) for row in readiness.values())),
            "dcfMissingInputs": dict(Counter(str(field) for row in readiness.values() for field in row.get("missingInputs") or []))}


def main():
    load_local_env()
    config = {key: value for key, value in mysql_settings().items() if value != ""}
    connection = pymysql.connect(**config, cursorclass=pymysql.cursors.DictCursor,
                                 connect_timeout=5, read_timeout=20)
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            inventory = verify_inventory(cursor)
            execution = verify_ai_execution(cursor)
            cursor.execute("SELECT payload_json FROM monitor_snapshots")
            snapshots = [verify_snapshot(json.loads(row["payload_json"])) for row in cursor.fetchall()]
            assert snapshots, "No retained monitor snapshots to verify"
            cursor.execute("SELECT COUNT(*) AS currentFacts, SUM(revision.revision_id IS NULL) AS currentOnly, "
                "SUM(fact.payload_hash <> revision.payload_hash) AS retainedHashMismatches "
                "FROM external_fact_current fact LEFT JOIN external_fact_revision revision ON fact.revision_id=revision.revision_id")
            lineage = cursor.fetchone()
            assert not lineage["retainedHashMismatches"], "Retained revision hash differs from current source"
            cursor.execute("SELECT dataset_id,health_state,last_success_at,consecutive_failures FROM "
                "(SELECT bucket_id AS dataset_id,health_state,last_success_at,consecutive_failures FROM external_provider_state "
                "WHERE bucket_id <> '__provider__' AND health_state IN ('failed','circuit_open')) providers")
            providers = cursor.fetchall()
            print(json.dumps({"status": "passed", "mode": "read-only-no-send",
                "snapshotCount": len(snapshots), "snapshots": snapshots, "lineage": lineage,
                "datasetInventory": inventory, "retainedExecutionAudit": execution,
                "providerAttention": providers,
                "limits": "Current input/ABox replay and retained native/AI execution audit. No new TypeDB write, LLM call or notification; archive retention bounds historical coverage."},
                ensure_ascii=False, default=str))
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
