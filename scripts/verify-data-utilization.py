#!/usr/bin/env python3
"""Read-only local source/graph/AI contract replay. Never calls vendors or LLMs."""
import json
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
    return {"macroSeriesVerified": count,
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
                "providerAttention": providers,
                "limits": "Input replay only; no native TypeDB execution, generated AI answer or delivery replay."},
                ensure_ascii=False, default=str))
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
