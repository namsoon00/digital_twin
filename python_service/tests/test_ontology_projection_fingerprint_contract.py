import unittest

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, PortfolioOntology
from digital_twin.domain.ontology_external_abox import add_external_signal_concepts, safe_signal_value
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.ontology_projection_fingerprint import material_graph_fingerprint
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.portfolio_ontology_state import position_market_state_payload
from digital_twin.domain.portfolio import AccountSnapshot, DecisionItem, PortfolioSummary
from digital_twin.domain.ontology_scopes import apply_scoped_abox_identity
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.portfolio_ontology_runtime_concepts import (
    add_operational_world_concepts,
    add_runtime_setting_concepts,
)
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder


class CurrentPipelineHealthStore:
    def load(self):
        return {
            "pipelines": {
                "marketSnapshot": {
                    "state": "failed",
                    "reason": "current worker failure",
                },
            },
        }


class OntologyProjectionFingerprintContractTests(unittest.TestCase):

    def test_projection_runtime_context_removes_derived_decision_history(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "currentPrice": 100,
            "quantity": 1,
        })
        decision = DecisionItem(
            symbol="005930",
            name="삼성전자",
            sector="반도체",
            market="KR",
            currency="KRW",
            market_value=100,
            profit_loss=0,
            profit_loss_rate=0,
            decision="매도",
            tone="caution",
            relation_rule_context={"activeRules": [{"ruleId": "graph.derived"}]},
        )
        snapshot = AccountSnapshot(
            "main", "Main", "toss", "live", "ok", "2026-07-23T10:00:00Z",
            PortfolioSummary(100, 100, 0, [], [], 1),
            positions=[position],
            decisions=[decision],
            metadata={
                "previousMonitorState": {
                    "positions": {"005930": {"currentPrice": 99}},
                    "decisions": {"005930": decision.to_dict()},
                    "metadata": {"ontology": {"derived": True}},
                },
                "monitorStateHistory": [{
                    "positions": {"005930": {"currentPrice": 98}},
                    "decisions": {"005930": decision.to_dict()},
                }],
            },
        )

        context = PortfolioOntologyProjectionRecorder(None).runtime_context(snapshot, active_tbox={})

        self.assertEqual([], context["decisionItems"])
        self.assertEqual({"005930": {"currentPrice": 99}}, context["metadata"]["previousMonitorState"]["positions"])
        self.assertNotIn("decisions", context["metadata"]["previousMonitorState"])
        self.assertNotIn("ontology", context["metadata"]["previousMonitorState"]["metadata"])
        self.assertNotIn("decisions", context["metadata"]["monitorStateHistory"][0])

    def test_typedb_projection_excludes_derived_decisions_from_abox_identity(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "currentPrice": 100,
            "quantity": 1,
            "sourceAsOf": "2026-07-23T10:00:00Z",
        })
        portfolio = PortfolioSummary(100, 100, 0, [], [], 1)
        first_context = {
            "settings": {},
            "decisionItems": [{"symbol": "005930", "decision": "매도", "reviewLevel": "act"}],
        }
        second_context = {
            "settings": {},
            "decisionItems": [{
                "symbol": "005930",
                "decision": "보유",
                "reviewLevel": "observe",
                "relationRuleContext": {"activeRules": [{"ruleId": "graph.derived"}]},
            }],
        }

        first = build_portfolio_ontology(
            [position], portfolio, portfolio_id="projection-derived-decision",
            runtime_context=first_context, include_tbox=False, include_presentation=False,
            include_derived_decision_items=False,
        )
        second = build_portfolio_ontology(
            [position], portfolio, portfolio_id="projection-derived-decision",
            runtime_context=second_context, include_tbox=False, include_presentation=False,
            include_derived_decision_items=False,
        )
        first_identity = apply_scoped_abox_identity(first)
        second_identity = apply_scoped_abox_identity(second)

        self.assertEqual(material_graph_fingerprint(first), material_graph_fingerprint(second))
        self.assertEqual(first_identity["scopeGenerationIds"], second_identity["scopeGenerationIds"])
        self.assertFalse(any(item.kind == "strategy-signal" for item in first.entities))

    def test_global_external_signal_omits_duplicated_raw_provider_payload(self):
        def graph_for(price: int) -> PortfolioOntology:
            graph = PortfolioOntology("external-signal-contract")
            add_external_signal_concepts(
                graph,
                "portfolio:external-signal-contract",
                {
                    "fetchedAt": "2026-07-23T10:00:00Z",
                    "equityQuotes": {"AAPL": {"currentPrice": price}},
                },
            )
            return graph

        first = graph_for(100)
        second = graph_for(101)
        signal = next(item for item in first.entities if item.entity_id == "external-signal:equityQuotes")

        self.assertNotIn("value", signal.properties)
        self.assertTrue(signal.properties["payloadPresent"])
        self.assertEqual("mapping", signal.properties["payloadKind"])
        self.assertFalse(any(item.entity_id == "external-signal:fetchedAt" for item in first.entities))
        self.assertEqual(material_graph_fingerprint(first), material_graph_fingerprint(second))

    def test_projection_uses_only_snapshot_captured_pipeline_health(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "currentPrice": 100,
            "quantity": 1,
            "sourceAsOf": "2026-07-23T10:00:00Z",
        })
        snapshot = AccountSnapshot(
            "main", "Main", "toss", "live", "ok", "2026-07-23T10:00:00Z",
            PortfolioSummary(100, 100, 0, [], [], 1), positions=[position], metadata={},
        )
        recorder = PortfolioOntologyProjectionRecorder(
            None,
            data_pipeline_health_store=CurrentPipelineHealthStore(),
        )

        self.assertEqual({}, recorder.runtime_context(snapshot)["dataPipelineHealth"])
        snapshot.metadata["dataPipelineHealth"] = {"pipelines": {"marketSnapshot": {"state": "failed"}}}
        self.assertEqual(
            "failed",
            recorder.runtime_context(snapshot)["dataPipelineHealth"]["pipelines"]["marketSnapshot"]["state"],
        )

    def test_fingerprint_uses_evidence_role_and_data_state(self):
        graph = PortfolioOntology("fingerprint-contract")
        evidence = OntologyEvidence(
            "evidence:contract",
            "stock:005930",
            "data-quality",
            "test",
            "시장 데이터 수집 상태",
            {"ontologyBox": "ABox"},
        )
        evidence.evidence_role = "risk"
        evidence.data_state = "partial"
        graph.evidence.append(evidence)

        fingerprint = material_graph_fingerprint(graph)

        self.assertEqual(64, len(fingerprint))

    def test_operational_runtime_settings_do_not_enter_material_ontology(self):
        graph = PortfolioOntology("runtime-setting-contract")
        portfolio_id = add_entity(
            graph,
            "portfolio",
            "runtime-setting-contract",
            "투자 포트폴리오",
            {"ontologyBox": "ABox"},
        )
        add_runtime_setting_concepts(graph, portfolio_id, {
            "settings": {
                "notificationCooldownMinutes": "360",
                "externalApiFetchIntervalMinutes": "30",
                "typedbNativeRuleQueryTimeoutSeconds": "6",
                "typedbNativeRuleExecutionBudgetSeconds": "30",
                "mysqlPassword": "must-not-be-projected",
            },
        })

        projected_keys = {
            str(item.properties.get("key") or "")
            for item in graph.entities
            if item.kind == "runtime-setting"
        }

        self.assertEqual(
            {"notificationCooldownMinutes", "externalApiFetchIntervalMinutes"},
            projected_keys,
        )

    def test_operational_runtime_setting_change_keeps_material_fingerprint_stable(self):
        def graph_for(query_timeout: str) -> PortfolioOntology:
            graph = PortfolioOntology("runtime-setting-fingerprint")
            portfolio_id = add_entity(
                graph,
                "portfolio",
                "runtime-setting-fingerprint",
                "투자 포트폴리오",
                {"ontologyBox": "ABox"},
            )
            add_runtime_setting_concepts(graph, portfolio_id, {
                "settings": {
                    "notificationCooldownMinutes": "360",
                    "typedbNativeRuleQueryTimeoutSeconds": query_timeout,
                },
            })
            return graph

        self.assertEqual(
            material_graph_fingerprint(graph_for("6")),
            material_graph_fingerprint(graph_for("10")),
        )

    def test_investment_policy_runtime_setting_change_updates_material_fingerprint(self):
        def graph_for(cooldown_minutes: str) -> PortfolioOntology:
            graph = PortfolioOntology("runtime-setting-policy")
            portfolio_id = add_entity(
                graph,
                "portfolio",
                "runtime-setting-policy",
                "투자 포트폴리오",
                {"ontologyBox": "ABox"},
            )
            add_runtime_setting_concepts(graph, portfolio_id, {
                "settings": {
                    "notificationCooldownMinutes": cooldown_minutes,
                    "typedbNativeRuleQueryTimeoutSeconds": "10",
                },
            })
            return graph

        self.assertNotEqual(
            material_graph_fingerprint(graph_for("360")),
            material_graph_fingerprint(graph_for("120")),
        )

    def test_healthy_pipeline_poll_timestamps_do_not_enter_material_ontology(self):
        def graph_for(checked_at: str, last_non_zero_at: str) -> PortfolioOntology:
            graph = PortfolioOntology("pipeline-health-contract")
            portfolio_id = add_entity(
                graph,
                "portfolio",
                "pipeline-health-contract",
                "투자 포트폴리오",
                {"ontologyBox": "ABox"},
            )
            add_operational_world_concepts(graph, portfolio_id, {
                "mode": "live",
                "settings": {},
                "dataPipelineHealth": {
                    "pipelines": {
                        "marketSnapshot": {
                            "state": "healthy",
                            "checkedAt": checked_at,
                            "lastNonZeroAt": last_non_zero_at,
                            "providerCandidateCount": 13,
                        },
                    },
                },
            }, [])
            return graph

        first = graph_for("2026-07-23T10:00:00Z", "2026-07-23T10:00:00Z")
        second = graph_for("2026-07-23T10:05:00Z", "2026-07-23T10:05:00Z")
        self.assertFalse(any(item.kind == "data-pipeline-health" for item in first.entities))
        self.assertEqual(material_graph_fingerprint(first), material_graph_fingerprint(second))

    def test_unhealthy_pipeline_timestamp_change_keeps_semantic_state_stable(self):
        def graph_for(checked_at: str, last_non_zero_at: str, state: str) -> PortfolioOntology:
            graph = PortfolioOntology("pipeline-health-risk-contract")
            portfolio_id = add_entity(
                graph,
                "portfolio",
                "pipeline-health-risk-contract",
                "투자 포트폴리오",
                {"ontologyBox": "ABox"},
            )
            add_operational_world_concepts(graph, portfolio_id, {
                "mode": "live",
                "settings": {},
                "dataPipelineHealth": {
                    "pipelines": {
                        "marketSnapshot": {
                            "state": state,
                            "reasonCode": "quote-coverage-empty",
                            "reason": "시세 수집 실패",
                            "checkedAt": checked_at,
                            "stateSince": "2026-07-23T09:00:00Z",
                            "lastNonZeroAt": last_non_zero_at,
                            "consecutiveZeroRuns": 2,
                            "providerFailureCount": 1,
                            "providerCandidateCount": 13,
                        },
                    },
                },
            }, [])
            return graph

        first = graph_for("2026-07-23T10:00:00Z", "2026-07-23T09:55:00Z", "failed")
        second = graph_for("2026-07-23T10:05:00Z", "2026-07-23T09:55:00Z", "failed")
        recovered = graph_for("2026-07-23T10:05:00Z", "2026-07-23T10:05:00Z", "healthy")
        self.assertEqual(material_graph_fingerprint(first), material_graph_fingerprint(second))
        self.assertNotEqual(material_graph_fingerprint(first), material_graph_fingerprint(recovered))

    def test_market_session_clock_and_provider_message_do_not_change_material_facts(self):
        def graph_for(session_time: str, message: str) -> PortfolioOntology:
            return PortfolioOntology(
                "market-clock-contract",
                entities=[OntologyEntity("stock:005930", "삼성전자", "stock", {
                    "ontologyBox": "ABox",
                    "symbol": "005930",
                    "currentPrice": 70000,
                    "marketSessionLocalTime": session_time,
                    "quoteMessage": message,
                    "freshnessStatus": "near-live",
                })],
            )

        first = graph_for("14:00:00", "provider poll 1")
        second = graph_for("14:05:00", "provider poll 2")
        stale = graph_for("14:05:00", "provider poll 2")
        stale.entities[0].properties["freshnessStatus"] = "stale"

        self.assertEqual(material_graph_fingerprint(first), material_graph_fingerprint(second))
        self.assertNotEqual(material_graph_fingerprint(first), material_graph_fingerprint(stale))

    def test_provider_poll_timestamps_are_removed_before_large_signal_values_are_compacted(self):
        def payload(fetched_at: str, updated_at: str, price: float):
            return {
                "fetchedAt": fetched_at,
                "company": {
                    "last_updated": updated_at,
                    "currentPrice": price,
                },
                "payload": "x" * 1600,
            }

        first = safe_signal_value(
            "companyOverviews",
            payload("2026-07-23T10:00:00Z", "2026-07-23T10:00:00Z", 100),
        )
        polled_again = safe_signal_value(
            "companyOverviews",
            payload("2026-07-23T10:05:00Z", "2026-07-23T10:05:00Z", 100),
        )
        changed_price = safe_signal_value(
            "companyOverviews",
            payload("2026-07-23T10:05:00Z", "2026-07-23T10:05:00Z", 101),
        )

        self.assertEqual(first, polled_again)
        self.assertNotEqual(first, changed_price)

    def test_position_poll_timestamp_does_not_enter_market_change_payload(self):
        first = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "currentPrice": 70000,
            "updatedAt": "2026-07-23T10:00:00Z",
        })
        polled_again = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "source": "holding",
            "currentPrice": 70000,
            "updatedAt": "2026-07-23T10:05:00Z",
        })

        first_payload = position_market_state_payload(first)
        second_payload = position_market_state_payload(polled_again)

        self.assertEqual(first_payload, second_payload)
        self.assertNotIn("updatedAt", first_payload)


if __name__ == "__main__":
    unittest.main()
