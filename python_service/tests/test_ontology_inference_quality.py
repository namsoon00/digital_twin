import unittest
from datetime import datetime, timezone

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_inference_context import inference_evidence_state, matches_from_inference
from digital_twin.domain.ontology_inference_materializer import materialize_rule_inference
from digital_twin.domain.ontology_observation_quality import position_observation_profiles
from digital_twin.domain.ontology_projection_fingerprint import material_graph_fingerprint
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.portfolio import AccountSnapshot, Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.market_data import normalize_position
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder


class OntologyInferenceQualityTests(unittest.TestCase):
    def test_meta_relations_are_not_counted_as_active_rule_matches(self):
        rule_id = "graph.loss_guard.breakdown.v1"
        relations = [
            {
                "type": "HAS_INFERRED_RISK",
                "source": "stock:000660",
                "target": "risk:000660:loss",
                "ruleId": rule_id,
                "derivationIndex": 0,
                "decisionStage": "LOSS_REDUCE",
                "actionGroup": "lossControl",
                "polarity": "risk",
                "riskImpact": 13,
                "weight": 0.86,
            },
            {"type": "HAS_INFERENCE_TRACE", "source": "stock:000660", "target": "trace:1", "ruleId": rule_id},
            {"type": "HAS_WHY_NOW", "source": "stock:000660", "target": "why:1", "ruleId": rule_id},
            {"type": "HAS_SIGNAL_CONFLICT", "source": "stock:000660", "target": "conflict:1", "ruleId": rule_id},
            {"type": "HAS_INFERENCE_TIMELINE", "source": "stock:000660", "target": "timeline:1", "ruleId": rule_id},
            {"type": "EXPLAINED_BY_TRACE", "source": "risk:000660:loss", "target": "trace:1", "ruleId": rule_id},
        ]

        matches = matches_from_inference(relations, [{"ruleId": rule_id, "confidence": 0.86}], facts={})

        self.assertEqual(1, len(matches))
        self.assertEqual("HAS_INFERRED_RISK", matches[0].relation_type)

    def test_materialized_trace_contains_observed_values_and_relation_ids(self):
        rule = default_graph_inference_rules()[0]
        graph = PortfolioOntology("quality-test")
        stock = OntologyEntity("stock:005930", "삼성전자", "stock", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "source": "holding",
            "profitLossRate": -12.5,
            "updatedAt": "2026-07-20T00:00:00Z",
            "sourceAsOf": "2026-07-20T00:00:00Z",
            "freshnessRequired": True,
            "freshnessStatus": "fresh",
            "judgementEvidenceUsable": True,
            "quoteStatus": "ok",
            "quoteSource": "Toss",
        })
        risk_budget = OntologyEntity("risk-budget:main", "손실 한도", "risk-budget", {
            "ontologyBox": "ABox",
            "source": "account-policy",
            "freshnessRequired": False,
            "freshnessStatus": "not-applicable",
            "judgementEvidenceUsable": True,
        })
        level = OntologyEntity("key-level:005930:ma20", "20일선", "key-level", {
            "ontologyBox": "ABox",
            "levelType": "ma20",
            "value": 300000,
            "observedAt": "2026-07-20T00:00:00Z",
            "freshnessStatus": "fresh",
            "source": "Toss",
        })
        wrong_level = OntologyEntity("key-level:005930:ma5", "5일선", "key-level", {
            "ontologyBox": "ABox",
            "levelType": "ma5",
            "value": 320000,
            "observedAt": "2026-07-20T00:00:00Z",
            "freshnessStatus": "fresh",
            "source": "Toss",
        })
        graph.entities.extend([stock, risk_budget, level, wrong_level])
        graph.relations.extend([
            OntologyRelation("stock:005930", "risk-budget:main", "HAS_RISK_BUDGET", 1.0, properties={"_relationId": "relation:risk-budget"}),
            OntologyRelation("stock:005930", "key-level:005930:ma20", "BREAKS_LEVEL", 0.8, properties={"_relationId": "relation:ma20"}),
            OntologyRelation("stock:005930", "key-level:005930:ma5", "BREAKS_LEVEL", 0.99, properties={"_relationId": "relation:wrong-ma5"}),
        ])

        materialize_rule_inference(graph, rule, stock, {
            "matchedConditions": [
                {"conditionId": "holding-source", "kind": "subject_property"},
                {"conditionId": "strategy-risk-budget", "kind": "relation"},
                {"conditionId": "holding-loss", "kind": "subject_property"},
                {"conditionId": "ma-break", "kind": "relation"},
            ],
            "evidenceRelationIds": [],
            "confidence": 0.86,
        })

        trace = next(item for item in graph.entities if item.kind == "inference-trace")
        conditions = {item["conditionId"]: item for item in trace.properties["matchedConditions"]}
        self.assertEqual(-12.5, conditions["holding-loss"]["observedValue"])
        self.assertEqual("relation:ma20", conditions["ma-break"]["relationId"])
        self.assertEqual("fresh", conditions["ma-break"]["freshnessStatus"])
        self.assertEqual("sufficient", trace.properties["dataState"])
        self.assertEqual("typedb-match+abox-grounding", trace.properties["conditionDetailSource"])
        self.assertTrue(trace.properties["evidenceUsableForJudgement"])

    def test_position_observation_profile_requires_provider_clock_and_open_session(self):
        open_at = datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 80000,
            "quoteSource": "KIS Open API",
            "dataQuality": "actual",
            "sourceAsOf": "2026-07-20T00:58:00Z",
            "sourceFetchedAt": "2026-07-20T00:59:00Z",
            "indicatorAsOf": "2026-07-17T06:30:00Z",
            "indicatorFetchedAt": "2026-07-20T00:59:00Z",
        })

        profiles = position_observation_profiles(position, {"asOf": open_at.isoformat()})

        self.assertEqual("fresh", profiles["quote"]["freshnessStatus"])
        self.assertEqual("open", profiles["quote"]["marketSessionStatus"])
        self.assertTrue(profiles["quote"]["sourceTimestampPresent"])
        self.assertTrue(profiles["quote"]["judgementEvidenceUsable"])
        self.assertEqual("not-applicable", profiles["static"]["freshnessStatus"])

    def test_position_observation_profile_rejects_fetch_clock_without_source_clock(self):
        position = Position(
            "005930",
            "삼성전자",
            market="KR",
            currency="KRW",
            current_price=80000,
            quote_source="KIS Open API",
            source_fetched_at="2026-07-20T00:59:00Z",
        )

        profile = position_observation_profiles(position, {"asOf": "2026-07-20T01:00:00Z"})["quote"]

        self.assertEqual("unknown", profile["freshnessStatus"])
        self.assertFalse(profile["sourceTimestampPresent"])
        self.assertFalse(profile["judgementEvidenceUsable"])
        self.assertIn("원천 기준시각", profile["freshnessGateReason"])

    def test_position_observation_profile_rejects_closed_market_for_judgement(self):
        position = Position(
            "005930",
            "삼성전자",
            market="KR",
            currency="KRW",
            current_price=80000,
            quote_source="KIS Open API",
            source_as_of="2026-07-20T12:59:00Z",
            source_fetched_at="2026-07-20T12:59:30Z",
        )

        profile = position_observation_profiles(position, {"asOf": "2026-07-20T13:00:00Z"})["quote"]

        self.assertEqual("fresh", profile["freshnessStatus"])
        self.assertEqual("closed", profile["marketSessionStatus"])
        self.assertFalse(profile["judgementEvidenceUsable"])

    def test_inference_is_blocked_when_temporal_evidence_is_stale(self):
        state = inference_evidence_state(
            {
                "type": "HAS_INFERRED_RISK",
                "ruleId": "graph.loss_guard.breakdown.v1",
                "actionGroup": "lossControl",
                "polarity": "risk",
                "evidenceRole": "risk",
            },
            {
                "isHolding": True,
                "profitLossRate": -25,
                "ma20Distance": -20,
                "ma60Distance": -12,
            },
            {
                "evidenceUsableForJudgement": False,
                "freshnessStatus": "stale",
                "freshnessGateReason": "원천 가격 기준시각이 오래되었습니다.",
                "matchedConditions": [{
                    "conditionId": "holding-loss",
                    "observedValue": -25,
                    "freshnessRequired": True,
                    "freshnessStatus": "stale",
                }],
            },
        )

        self.assertTrue(state["judgementBlocked"])
        self.assertEqual("unavailable", state["dataState"])
        self.assertEqual("risk", state["evidenceRole"])
        self.assertFalse(state["evidenceUsableForJudgement"])
        self.assertIn("원천 가격 기준시각", state["freshnessGateReason"])

    def test_material_fingerprint_ignores_poll_time_but_changes_with_price(self):
        position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "source": "holding",
            "quantity": 1,
            "currentPrice": 210,
            "marketValue": 210,
            "updatedAt": "2026-07-20T00:00:00Z",
        })
        snapshot_one = self.snapshot(position, "2026-07-20T00:01:00Z")
        snapshot_two = self.snapshot(position, "2026-07-20T00:04:00Z")
        repository = MemoryProjectionRepository()
        recorder = PortfolioOntologyProjectionRecorder(repository)

        first = recorder.record_snapshot(snapshot_one)
        second = recorder.record_snapshot(snapshot_two)

        self.assertTrue(first["saved"])
        self.assertEqual("unchanged-material-facts", second["status"])
        self.assertEqual(1, repository.save_count)
        self.assertEqual(1, repository.rulebox_count)
        self.assertTrue(second["inferenceBox"]["reusedForUnchangedMaterialFacts"])

        changed = normalize_position({**position.to_dict(), "currentPrice": 211, "marketValue": 211})
        third = recorder.record_snapshot(self.snapshot(changed, "2026-07-20T00:07:00Z"))
        self.assertTrue(third["saved"])
        self.assertNotEqual(first["materialFingerprint"], third["materialFingerprint"])

    def test_material_abox_generation_is_audited_before_graph_activation(self):
        events = []

        class AuditStore:
            def __init__(self):
                self.runs = []

            def begin(self, run):
                events.append(("begin", run.run_id))
                self.runs.append(run)
                return run

            def complete(self, run):
                events.append(("complete", run.run_id))
                self.runs[-1] = run
                return run

        class OrderedRepository(MemoryProjectionRepository):
            def save_graph(self, graph):
                events.append(("save", str((graph.worldview or {}).get("projectionRunId") or "")))
                return super().save_graph(graph)

        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "quantity": 10,
            "currentPrice": 70000,
            "marketValue": 700000,
        })
        snapshot = self.snapshot(position, "2026-07-20T00:01:00Z")
        snapshot.metadata["ontology"] = {"projection": {"old": "derived"}}
        repository = OrderedRepository()
        audit_store = AuditStore()
        recorder = PortfolioOntologyProjectionRecorder(
            repository,
            projection_run_store=audit_store,
        )

        first = recorder.record_snapshot(snapshot)
        second = recorder.record_snapshot(self.snapshot(position, "2026-07-20T00:04:00Z"))

        self.assertTrue(first["saved"])
        self.assertEqual("recorded", first["projectionAudit"]["status"])
        self.assertEqual(["begin", "save", "complete"], [event[0] for event in events])
        self.assertEqual(events[0][1], events[1][1])
        self.assertEqual(events[0][1], events[2][1])
        self.assertNotIn("sourceSnapshot", audit_store.runs[0].context_payload)
        self.assertEqual(
            "monitor_snapshot_history",
            audit_store.runs[0].context_payload["sourceSnapshotReference"]["store"],
        )
        self.assertEqual("unchanged-material-facts", second["status"])
        self.assertEqual(1, len(audit_store.runs))

    def test_source_audit_failure_preserves_existing_active_abox(self):
        class FailingAuditStore:
            def begin(self, _run):
                raise RuntimeError("mysql unavailable")

        position = normalize_position({
            "symbol": "000660",
            "name": "SK하이닉스",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "quantity": 7,
            "currentPrice": 1800000,
            "marketValue": 12600000,
        })
        repository = MemoryProjectionRepository()

        result = PortfolioOntologyProjectionRecorder(
            repository,
            projection_run_store=FailingAuditStore(),
        ).record_snapshot(self.snapshot(position, "2026-07-20T00:01:00Z"))

        self.assertEqual("source-audit-failed", result["status"])
        self.assertTrue(result["preservedActiveGeneration"])
        self.assertEqual(0, repository.save_count)

    def test_runtime_projection_can_skip_static_tbox_and_presentation_payload(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "quantity": 1,
            "currentPrice": 70000,
            "marketValue": 70000,
        })

        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([position]),
            portfolio_id="runtime-abox-only",
            include_tbox=False,
            include_presentation=False,
        )

        self.assertFalse(any((item.properties or {}).get("ontologyBox") == "TBox" for item in graph.entities))
        self.assertFalse(graph.reasoning_cards)
        self.assertFalse(graph.prompt)
        self.assertTrue(graph.worldview["presentationDeferred"])

    def test_unchanged_abox_retries_when_inference_generation_is_stale(self):
        position = normalize_position({
            "symbol": "000660",
            "name": "SK하이닉스",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "quantity": 7,
            "currentPrice": 1830000,
            "marketValue": 12810000,
        })
        repository = MemoryProjectionRepository()
        recorder = PortfolioOntologyProjectionRecorder(repository)
        snapshot = self.snapshot(position, "2026-07-20T00:01:00Z")
        recorder.record_snapshot(snapshot)
        repository.inference_status = "stale-generation"

        result = recorder.record_snapshot(self.snapshot(position, "2026-07-20T00:04:00Z"))

        self.assertEqual("unchanged-material-facts-reasoning-retry", result["status"])
        self.assertTrue(result["reasoningRetryRequired"])
        self.assertEqual(2, repository.rulebox_count)

    def test_incomplete_abox_with_matching_fingerprint_is_reprojected(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "quantity": 10,
            "currentPrice": 250000,
            "marketValue": 2500000,
        })
        repository = MemoryProjectionRepository()
        recorder = PortfolioOntologyProjectionRecorder(repository)
        snapshot = self.snapshot(position, "2026-07-20T00:01:00Z")

        first = recorder.record_snapshot(snapshot)
        repository.active["status"] = "incomplete"
        second = recorder.record_snapshot(self.snapshot(position, "2026-07-20T00:04:00Z"))

        self.assertTrue(first["saved"])
        self.assertTrue(second["saved"])
        self.assertEqual(2, repository.save_count)

    def test_unchanged_abox_retries_when_inference_scope_is_partial(self):
        first_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "quantity": 10,
            "currentPrice": 250000,
            "marketValue": 2500000,
        })
        second_position = normalize_position({
            "symbol": "000660",
            "name": "SK하이닉스",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "quantity": 7,
            "currentPrice": 1800000,
            "marketValue": 12600000,
        })
        repository = MemoryProjectionRepository()
        recorder = PortfolioOntologyProjectionRecorder(repository)
        snapshot = self.snapshot_with_positions(
            [first_position, second_position],
            "2026-07-20T00:01:00Z",
        )
        recorder.record_snapshot(snapshot)
        repository.target_symbols = ["005930"]

        result = recorder.record_snapshot(self.snapshot_with_positions(
            [first_position, second_position],
            "2026-07-20T00:04:00Z",
        ))

        self.assertEqual("unchanged-material-facts-reasoning-retry", result["status"])
        self.assertEqual(2, repository.rulebox_count)

    @staticmethod
    def snapshot(position, generated_at):
        return OntologyInferenceQualityTests.snapshot_with_positions([position], generated_at)

    @staticmethod
    def snapshot_with_positions(positions, generated_at):
        portfolio = portfolio_summary(positions, fx_rates={"USD": 1400})
        return AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            generated_at,
            portfolio,
            positions,
            [],
        )


class MemoryProjectionRepository:
    store_key = "typedb"

    def __init__(self):
        self.active = {}
        self.save_count = 0
        self.rulebox_count = 0
        self.inference_status = "ok"
        self.target_symbols = []

    def active_tbox_metadata(self):
        return {"status": "ok", "source": "test", "version": "test", "fingerprint": "test"}

    def rulebox_snapshot(self):
        rules = [item.to_dict() for item in default_graph_inference_rules()]
        return {
            "configured": True,
            "status": "ok",
            "rules": rules,
            "ruleCount": len(rules),
        }

    def active_abox_metadata(self):
        return dict(self.active)

    def save_graph(self, graph):
        self.save_count += 1
        fingerprint = material_graph_fingerprint(graph)
        snapshot_id = str((graph.worldview or {}).get("aboxSnapshotId") or "")
        self.active = {"materialFingerprint": fingerprint, "aboxSnapshotId": snapshot_id}
        return {"saved": True, "status": "ok", "graphStore": "typedb"}

    def run_rulebox(self, payload=None):
        self.rulebox_count += 1
        self.target_symbols = list((payload or {}).get("symbols") or [])
        return {
            "status": "ok",
            "inferenceBox": {
                "status": "ok",
                "nativeTypeDbReasoningUsed": True,
                "inferenceGenerationId": "generation:" + str(self.rulebox_count),
                "targetSymbols": list(self.target_symbols),
                "relations": [],
                "traces": [],
            },
        }

    def inferencebox_snapshot(self, symbols=None, limit=80):
        return {
            "status": self.inference_status,
            "nativeTypeDbReasoningUsed": self.inference_status == "ok",
            "inferenceGenerationId": "generation:" + str(self.rulebox_count),
            "sourceAboxSnapshotId": str(self.active.get("aboxSnapshotId") or ""),
            "generationAligned": self.inference_status == "ok",
            "targetSymbols": list(self.target_symbols),
            "relations": [],
            "traces": [],
        }


if __name__ == "__main__":
    unittest.main()
