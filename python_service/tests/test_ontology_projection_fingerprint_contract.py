import unittest

from digital_twin.domain.ontology_contracts import OntologyEvidence, PortfolioOntology
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.ontology_projection_fingerprint import material_graph_fingerprint
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary
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


if __name__ == "__main__":
    unittest.main()
