import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_lab_service import OntologyLabService
from digital_twin.domain.ontology_experiments import OntologyExperiment


class MemoryExperimentStore:
    def __init__(self):
        self.rows = {}

    def list(self):
        return list(self.rows.values())

    def get(self, experiment_id):
        return self.rows.get(experiment_id)

    def save(self, experiment: OntologyExperiment):
        self.rows[experiment.experiment_id] = experiment


class FakeOntologyRepository:
    def active_tbox_metadata(self):
        return {"status": "ok", "source": "test"}

    def rulebox_snapshot(self):
        return {
            "configured": False,
            "status": "disabled",
            "source": "defaults",
            "engineVersion": "test",
            "rules": [],
            "ruleCount": 0,
            "conditionCount": 0,
            "derivationCount": 0,
            "relationTypes": [],
            "changeCandidates": [],
        }


class FakeMonitorStore:
    @property
    def previous(self):
        return {
            "acct-1": {
                "accountId": "acct-1",
                "accountLabel": "테스트 계좌",
                "provider": "demo",
                "mode": "demo",
                "status": "ok",
                "generatedAt": "2026-07-11T00:00:00Z",
                "portfolio": {
                    "total": 1000000,
                    "invested": 500000,
                    "cash": 500000,
                    "markets": [{"market": "US", "ratio": 50}],
                    "sectors": [{"sector": "Tech", "ratio": 50}],
                    "concentration": 50,
                },
                "positions": {
                    "AAPL": {
                        "symbol": "AAPL",
                        "name": "Apple",
                        "market": "US",
                        "currency": "USD",
                        "quantity": 1,
                        "sellable_quantity": 1,
                        "average_price": 100,
                        "current_price": 110,
                        "market_value": 110,
                        "market_value_krw": 150000,
                        "profit_loss": 10,
                        "profit_loss_krw": 13000,
                        "profit_loss_rate": 10,
                        "sector": "Tech",
                        "source": "holding",
                    }
                },
                "watchlist": {},
                "decisions": {},
                "externalSignals": {},
                "metadata": {},
            }
        }


class OntologyLabTests(unittest.TestCase):
    def test_create_normalizes_candidate_rule_as_disabled(self):
        service = OntologyLabService(
            FakeOntologyRepository(),
            MemoryExperimentStore(),
            monitor_store=FakeMonitorStore(),
        )

        result = service.create({"title": "Lab", "rules": [candidate_rule()]})
        experiment = result["experiment"]

        self.assertEqual("Lab", experiment["title"])
        self.assertEqual(1, len(experiment["candidateRules"]))
        self.assertFalse(experiment["candidateRules"][0]["enabled"])

    def test_run_replays_candidate_rule_without_mutating_operational_rulebox(self):
        store = MemoryExperimentStore()
        service = OntologyLabService(
            FakeOntologyRepository(),
            store,
            monitor_store=FakeMonitorStore(),
        )
        experiment_id = service.create({
            "title": "AAPL next-check lab",
            "symbols": ["AAPL"],
            "rules": [candidate_rule()],
        })["experiment"]["id"]

        result = service.run(experiment_id)
        lab_result = result["result"]
        delta = lab_result["inference"]["aggregateDelta"]

        self.assertFalse(lab_result["sandbox"]["mutatedNeo4j"])
        self.assertFalse(lab_result["sandbox"]["mutatedOperationalRuleBox"])
        self.assertEqual(1, lab_result["sandbox"]["graphRunCount"])
        self.assertGreaterEqual(delta["derivedRelationCount"], 1)
        self.assertIn("graph.lab.symbol-review.v1", delta["newRuleIds"])
        self.assertIn("REQUIRES_NEXT_CHECK", delta["newRelationTypes"])
        proposal = lab_result["proposedOntologyChanges"]
        self.assertIn("graph.lab.symbol-review.v1", proposal["ruleIds"])
        self.assertIn("REQUIRES_NEXT_CHECK", proposal["newRelationTypes"])
        self.assertIn("LAB_REVIEW", proposal["newDecisionStages"])
        recommendations = lab_result["recommendations"]
        self.assertTrue(recommendations)
        self.assertIn("review-rule-promotion", {item["type"] for item in recommendations})
        self.assertIn("register-relation-types", {item["type"] for item in recommendations})
        self.assertIn("register-decision-stages", {item["type"] for item in recommendations})

    def test_run_without_snapshots_marks_result_as_needing_data(self):
        service = OntologyLabService(
            FakeOntologyRepository(),
            MemoryExperimentStore(),
            monitor_store=None,
        )
        experiment_id = service.create({"rules": [candidate_rule()]})["experiment"]["id"]

        result = service.run(experiment_id)

        self.assertEqual("needs-data", result["result"]["promotionReadiness"]["status"])
        self.assertIn("collect-abox-data", {item["type"] for item in result["result"]["recommendations"]})

    def test_active_experiment_runs_once_per_monitor_snapshot(self):
        store = MemoryExperimentStore()
        service = OntologyLabService(
            FakeOntologyRepository(),
            store,
            monitor_store=FakeMonitorStore(),
            settings={"ontologyLabBatchSize": "5", "ontologyLabRunHistoryLimit": "3"},
        )
        experiment_id = service.create({
            "title": "AAPL continuous lab",
            "symbols": ["AAPL"],
            "rules": [candidate_rule()],
        })["experiment"]["id"]

        activated = service.activate(experiment_id)
        first_run = service.run_once()
        second_run = service.run_once()

        self.assertEqual("active", activated["status"])
        self.assertEqual(1, first_run["runCount"])
        self.assertEqual(0, first_run["skippedCount"])
        self.assertEqual(0, second_run["runCount"])
        self.assertEqual(1, second_run["skippedCount"])
        experiment = store.get(experiment_id)
        self.assertEqual("active", experiment.status)
        self.assertEqual(1, len(experiment.run_history))
        self.assertTrue(experiment.last_snapshot_key.startswith("monitor:"))
        self.assertEqual("scheduled", experiment.run_history[0]["runKind"])
        self.assertGreaterEqual(experiment.run_history[0]["recommendationCount"], 1)

    def test_pause_experiment_removes_it_from_active_batch(self):
        store = MemoryExperimentStore()
        service = OntologyLabService(
            FakeOntologyRepository(),
            store,
            monitor_store=FakeMonitorStore(),
        )
        experiment_id = service.create({"symbols": ["AAPL"], "rules": [candidate_rule()]})["experiment"]["id"]

        service.activate(experiment_id)
        paused = service.pause(experiment_id)
        result = service.run_once(force=True)

        self.assertEqual("paused", paused["status"])
        self.assertEqual("idle", result["status"])
        self.assertEqual(0, result["processedCount"])


def candidate_rule():
    return {
        "rule_id": "graph.lab.symbol-review.v1",
        "label": "실험 심볼 점검",
        "version": "lab-test-v1",
        "source_kind": "stock",
        "enabled": True,
        "action_group": "alertReview",
        "action_level": "watch",
        "prompt_hint": "실험 후보가 현재 종목을 다음 점검 대상으로 올립니다.",
        "conditions": [
            {
                "condition_id": "symbol-aapl",
                "kind": "subject_property",
                "description": "AAPL 대상 실험입니다.",
                "field": "symbol",
                "operator": "==",
                "value": "AAPL",
            }
        ],
        "derivations": [
            {
                "relation_type": "REQUIRES_NEXT_CHECK",
                "target_kind": "next-check",
                "target_key": "{symbol}:lab-review",
                "target_label": "{displayName} 실험 점검",
                "tbox_class": "NextCheck",
                "tbox_classes": ["NextCheck"],
                "polarity": "context",
                "weight": 0.72,
                "decision_stage": "LAB_REVIEW",
                "stage_priority": 40,
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
