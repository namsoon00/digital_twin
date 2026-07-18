import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.investment_strategy_proposal_service import InvestmentStrategyProposalService
from digital_twin.application.ontology_lab_service import OntologyLabService
from digital_twin.domain.ontology_experiments import OntologyExperiment
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule


class MemoryExperimentStore:
    def __init__(self):
        self.rows = {}

    def list(self):
        return list(self.rows.values())

    def get(self, experiment_id):
        return self.rows.get(experiment_id)

    def save(self, experiment: OntologyExperiment):
        self.rows[experiment.experiment_id] = experiment


class MemoryStrategyProposalStore:
    def __init__(self):
        self.rows = {}

    def list(self):
        return list(self.rows.values())

    def get(self, proposal_id):
        return self.rows.get(proposal_id)

    def save(self, proposal):
        self.rows[proposal.proposal_id] = proposal


class FakeOntologyRepository:
    def __init__(self):
        self.rules = []
        self.saved_rulebox_payloads = []
        self.run_rulebox_payloads = []
        self.saved_tbox_graphs = []
        self.validation_payloads = []

    def active_tbox_metadata(self):
        return {"status": "ok", "source": "test"}

    def rulebox_snapshot(self):
        relation_types = []
        for rule in self.rules:
            for derivation in rule.get("derivations") or []:
                relation_type = str(derivation.get("relation_type") or derivation.get("relationType") or "").strip()
                if relation_type:
                    relation_types.append(relation_type)
        return {
            "configured": True,
            "status": "ok",
            "source": "test",
            "engineVersion": "test",
            "rules": [dict(item) for item in self.rules],
            "ruleCount": len(self.rules),
            "conditionCount": sum(len(item.get("conditions") or []) for item in self.rules),
            "derivationCount": sum(len(item.get("derivations") or []) for item in self.rules),
            "relationTypes": sorted(set(relation_types)),
            "changeCandidates": [],
        }

    def save_rulebox(self, payload):
        body = dict(payload or {})
        self.saved_rulebox_payloads.append(body)
        self.rules = [dict(item) for item in (body.get("rules") or []) if isinstance(item, dict)]
        snapshot = self.rulebox_snapshot()
        snapshot.update({"saved": True, "status": "ok", "versionCount": 1})
        return snapshot

    def run_rulebox(self, payload=None):
        self.run_rulebox_payloads.append(dict(payload or {}))
        return {"status": "ok", "statementCount": 3}

    def save_graph(self, graph):
        self.saved_tbox_graphs.append(graph)
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
        }

    def validate_rulebox_materialization(self, payload):
        self.validation_payloads.append(dict(payload or {}))
        return {
            "status": "ok",
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": len((payload or {}).get("rules") or []),
            "matchedCount": 1,
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


class FakeRuleCandidateService:
    def __init__(self, result=None):
        self.result = result or ai_candidate_result()
        self.calls = []

    def propose(self, symbols=None, trigger="manual"):
        self.calls.append({"symbols": list(symbols or []), "trigger": trigger})
        return self.result


class FakeNotificationQueue:
    def __init__(self):
        self.jobs = []

    def enqueue(self, job):
        self.jobs.append(job)
        return True


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

        self.assertFalse(lab_result["sandbox"]["mutatedTypeDB"])
        self.assertFalse(lab_result["sandbox"]["mutatedOperationalRuleBox"])
        self.assertEqual(1, lab_result["sandbox"]["graphRunCount"])
        self.assertEqual(0, delta["derivedRelationCount"])
        self.assertEqual(1, delta["requiresTypeDbMaterializationCount"])
        proposal = lab_result["proposedOntologyChanges"]
        self.assertIn("graph.lab.symbol-review.v1", proposal["ruleIds"])
        self.assertIn("REQUIRES_NEXT_CHECK", proposal["newRelationTypes"])
        self.assertIn("LAB_REVIEW", proposal["newDecisionStages"])
        recommendations = lab_result["recommendations"]
        self.assertTrue(recommendations)
        self.assertIn("run-typedb-materialization", {item["type"] for item in recommendations})
        self.assertIn("register-relation-types", {item["type"] for item in recommendations})
        self.assertIn("register-decision-stages", {item["type"] for item in recommendations})

    def test_apply_recommendations_promotes_rulebox_tbox_and_records_audit(self):
        store = MemoryExperimentStore()
        repository = FakeOntologyRepository()
        service = OntologyLabService(
            repository,
            store,
            monitor_store=FakeMonitorStore(),
        )
        experiment_id = service.create({
            "title": "AAPL apply lab",
            "symbols": ["AAPL"],
            "rules": [candidate_rule()],
        })["experiment"]["id"]
        service.run(experiment_id)

        result = service.apply_recommendations(experiment_id, {
            "reviewApproved": True,
            "reviewedBy": "unit-test",
            "reviewReason": "needs-review 실험 결과를 테스트에서 승인",
        })

        self.assertEqual("applied", result["status"])
        self.assertEqual(1, len(repository.saved_rulebox_payloads))
        self.assertEqual(1, len(repository.run_rulebox_payloads))
        self.assertEqual(1, len(repository.saved_tbox_graphs))
        saved_rules = repository.saved_rulebox_payloads[0]["rules"]
        self.assertIn("graph.lab.symbol-review.v1", {item["rule_id"] for item in saved_rules})
        self.assertTrue([item for item in saved_rules if item["rule_id"] == "graph.lab.symbol-review.v1"][0]["enabled"])
        application = result["application"]
        self.assertIn("graph.lab.symbol-review.v1", application["ruleIds"])
        self.assertIn("REQUIRES_NEXT_CHECK", application["relationTypes"])
        self.assertIn("LAB_REVIEW", application["decisionStages"])
        experiment = store.get(experiment_id)
        self.assertEqual("applied", experiment.run_history[0]["applyStatus"])
        self.assertEqual("applied", experiment.last_result["appliedOntologyChanges"]["status"])
        self.assertEqual("unit-test", experiment.last_result["appliedOntologyChanges"]["reviewApproval"]["reviewedBy"])
        self.assertIn("applied", {item.get("applyStatus") for item in experiment.last_result["recommendations"]})

    def test_apply_recommendations_normalizes_blank_condition_ids(self):
        store = MemoryExperimentStore()
        repository = FakeOntologyRepository()
        service = OntologyLabService(
            repository,
            store,
            monitor_store=FakeMonitorStore(),
        )
        rule = candidate_rule()
        rule["rule_id"] = "graph.lab.blank-condition-ids.v1"
        rule["conditions"] = [
            {
                "kind": "subject_property",
                "description": "AAPL 대상 실험입니다.",
                "field": "symbol",
                "operator": "==",
                "value": "AAPL",
            },
            {
                "condition_id": "",
                "kind": "subject_property",
                "description": "보유 출처 실험입니다.",
                "field": "source",
                "operator": "==",
                "value": "holding",
            },
        ]
        experiment_id = service.create({
            "title": "Blank condition id apply lab",
            "symbols": ["AAPL"],
            "rules": [rule],
        })["experiment"]["id"]
        service.run(experiment_id)

        result = service.apply_recommendations(experiment_id, {
            "reviewApproved": True,
            "reviewedBy": "unit-test",
            "reviewReason": "blank condition id normalization",
        })

        self.assertEqual("applied", result["status"])
        saved_rule = [
            item
            for item in repository.saved_rulebox_payloads[0]["rules"]
            if item["rule_id"] == "graph.lab.blank-condition-ids.v1"
        ][0]
        condition_ids = [item.get("condition_id") for item in saved_rule["conditions"]]
        self.assertEqual(["condition-1", "condition-2"], condition_ids)

    def test_apply_recommendations_rejects_needs_review_without_approval(self):
        store = MemoryExperimentStore()
        repository = FakeOntologyRepository()
        service = OntologyLabService(
            repository,
            store,
            monitor_store=FakeMonitorStore(),
        )
        experiment_id = service.create({
            "title": "AAPL apply lab",
            "symbols": ["AAPL"],
            "rules": [candidate_rule()],
        })["experiment"]["id"]
        service.run(experiment_id)

        result = service.apply_recommendations(experiment_id)

        self.assertEqual("not-ready", result["status"])
        self.assertEqual("experiment-needs-review-approval", result["reason"])
        self.assertEqual(0, len(repository.saved_rulebox_payloads))
        self.assertEqual(0, len(repository.run_rulebox_payloads))
        self.assertEqual(0, len(repository.saved_tbox_graphs))

    def test_status_exposes_server_promotion_gate_for_experiment_review(self):
        store = MemoryExperimentStore()
        repository = FakeOntologyRepository()
        service = OntologyLabService(
            repository,
            store,
            monitor_store=FakeMonitorStore(),
        )
        experiment_id = service.create({
            "title": "AAPL gate lab",
            "symbols": ["AAPL"],
            "rules": [candidate_rule()],
        })["experiment"]["id"]
        service.run(experiment_id)

        experiment = service.status()["experiments"][0]
        gate = experiment["promotionGate"]

        self.assertEqual("needs-review", gate["status"])
        self.assertEqual("experiment-needs-review-approval", gate["reason"])
        self.assertTrue(gate["canApply"])
        self.assertTrue(gate["requiresReviewApproval"])
        self.assertIn("promotionSummary", service.status())
        self.assertIn("review-approval", {item["id"] for item in gate["checks"]})
        review_check = [item for item in gate["checks"] if item["id"] == "review-approval"][0]
        self.assertTrue(review_check["required"])

        service.apply_recommendations(experiment_id, {
            "reviewApproved": True,
            "reviewedBy": "unit-test",
            "reviewReason": "gate promotion test",
        })
        applied_gate = service.status()["experiments"][0]["promotionGate"]

        self.assertEqual("applied", applied_gate["status"])
        self.assertFalse(applied_gate["canApply"])
        self.assertEqual("applied", applied_gate["applyStatus"])

    def test_suggest_from_ai_candidates_creates_draft_experiment_once(self):
        store = MemoryExperimentStore()
        service = OntologyLabService(
            FakeOntologyRepository(),
            store,
            monitor_store=FakeMonitorStore(),
        )
        candidate_result = ai_candidate_result()

        created = service.suggest_from_rule_candidates(candidate_result)
        duplicated = service.suggest_from_rule_candidates(candidate_result)

        self.assertEqual("created", created["status"])
        self.assertEqual(1, created["createdCount"])
        self.assertEqual("skipped", duplicated["status"])
        self.assertEqual(0, duplicated["createdCount"])
        self.assertEqual(1, duplicated["skippedCount"])
        experiment = store.get(created["experiments"][0]["id"])
        self.assertEqual("draft", experiment.status)
        self.assertTrue(experiment.title.startswith("AI 제안:"))
        self.assertEqual(["AAPL"], experiment.symbols)
        self.assertFalse(experiment.candidate_rules[0]["enabled"])
        self.assertEqual("suggested", experiment.last_result["status"])
        self.assertEqual("graph.lab.symbol-review.v1", experiment.last_result["sourceCandidate"]["ruleId"])

    def test_apply_recommendations_rejects_unrun_ai_suggestion(self):
        store = MemoryExperimentStore()
        repository = FakeOntologyRepository()
        service = OntologyLabService(
            repository,
            store,
            monitor_store=FakeMonitorStore(),
        )
        created = service.suggest_from_rule_candidates(ai_candidate_result())
        experiment_id = created["experiments"][0]["id"]

        result = service.apply_recommendations(experiment_id)

        self.assertEqual("not-ready", result["status"])
        self.assertEqual("experiment-result-not-completed", result["reason"])
        self.assertEqual(0, len(repository.saved_rulebox_payloads))
        self.assertEqual(0, len(repository.run_rulebox_payloads))
        self.assertEqual(0, len(repository.saved_tbox_graphs))
        self.assertEqual("draft", store.get(experiment_id).status)

    def test_suggest_from_ai_candidates_can_activate_and_run_immediately(self):
        store = MemoryExperimentStore()
        service = OntologyLabService(
            FakeOntologyRepository(),
            store,
            monitor_store=FakeMonitorStore(),
        )

        created = service.suggest_from_rule_candidates(
            ai_candidate_result(),
            {"activate": True, "run": True},
        )

        self.assertEqual("created", created["status"])
        experiment = store.get(created["experiments"][0]["id"])
        self.assertEqual("active", experiment.status)
        self.assertTrue(experiment.active_since)
        self.assertEqual("completed", experiment.last_result["status"])
        self.assertEqual("ai-suggested", experiment.last_result["runKind"])
        self.assertEqual("ai-suggested", experiment.run_history[0]["runKind"])

    def test_auto_suggest_uses_candidate_service_and_runs_active_experiment(self):
        store = MemoryExperimentStore()
        candidate_service = FakeRuleCandidateService()
        service = OntologyLabService(
            FakeOntologyRepository(),
            store,
            monitor_store=FakeMonitorStore(),
            rule_candidate_service=candidate_service,
            settings={"ontologyRuleCandidateAiMaxCandidates": "2"},
        )

        result = service.auto_suggest()

        self.assertEqual("created", result["status"])
        self.assertTrue(result["autoSuggest"])
        self.assertEqual(1, result["createdCount"])
        self.assertEqual("ontology-lab-auto-suggest", candidate_service.calls[0]["trigger"])
        self.assertEqual(["AAPL"], candidate_service.calls[0]["symbols"])
        experiment = store.get(result["experiments"][0]["id"])
        self.assertEqual("active", experiment.status)
        self.assertEqual("completed", experiment.last_result["status"])
        self.assertEqual("ai-suggested", experiment.run_history[0]["runKind"])

    def test_rule_candidate_creates_strategy_proposal_and_lab_validation_updates_it(self):
        proposal_store = MemoryStrategyProposalStore()
        strategy_service = InvestmentStrategyProposalService(
            proposal_store,
            ontology_repository=FakeOntologyRepository(),
        )
        proposal_result = strategy_service.propose_from_rule_candidates(
            ai_candidate_result(),
            {"symbols": ["AAPL"], "trigger": "unit-test"},
        )
        service = OntologyLabService(
            FakeOntologyRepository(),
            MemoryExperimentStore(),
            monitor_store=FakeMonitorStore(),
            strategy_proposal_service=strategy_service,
        )

        lab_result = service.suggest_from_rule_candidates(ai_candidate_result(), {"activate": True, "run": True})

        self.assertEqual("created", proposal_result["status"])
        self.assertEqual(1, proposal_result["createdCount"])
        self.assertEqual("created", lab_result["status"])
        proposal = proposal_store.list()[0]
        self.assertEqual("validated", proposal.status)
        self.assertTrue(proposal.source_experiment_id.startswith("ontology-exp-"))
        self.assertEqual("completed", proposal.validation["status"])
        self.assertIn("graph.lab.symbol-review.v1", proposal.rule_ids)

    def test_strategy_proposal_materialization_validation_does_not_write_operational_rulebox(self):
        repository = FakeOntologyRepository()
        proposal_store = MemoryStrategyProposalStore()
        strategy_service = InvestmentStrategyProposalService(
            proposal_store,
            ontology_repository=repository,
        )
        created = strategy_service.propose_from_rule_candidates(
            ai_candidate_result(),
            {"symbols": ["AAPL"], "trigger": "unit-test"},
        )
        proposal_id = created["proposals"][0]["id"]

        result = strategy_service.validate_materialization(proposal_id)

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, len(repository.validation_payloads))
        self.assertEqual(0, len(repository.saved_rulebox_payloads))
        self.assertEqual(0, len(repository.run_rulebox_payloads))
        self.assertFalse(result["validation"]["mutatedOperationalRuleBox"])
        self.assertFalse(result["validation"]["wroteInferenceBox"])
        self.assertEqual(1, result["validation"]["diff"]["candidateMatchedCount"])
        self.assertIn("materialization-validated", {
            item["action"]
            for item in proposal_store.get(proposal_id).lifecycle.get("reviewLog") or []
        })
        self.assertEqual("validated", proposal_store.get(proposal_id).status)

    def test_strategy_proposal_approval_and_performance_keep_audit_trail(self):
        repository = FakeOntologyRepository()
        proposal_store = MemoryStrategyProposalStore()
        strategy_service = InvestmentStrategyProposalService(
            proposal_store,
            ontology_repository=repository,
        )
        created = strategy_service.propose_from_rule_candidates(
            ai_candidate_result(),
            {"symbols": ["AAPL"], "trigger": "unit-test"},
        )
        proposal_id = created["proposals"][0]["id"]
        strategy_service.validate_materialization(proposal_id)

        approved = strategy_service.approve(proposal_id, {
            "reviewedBy": "unit-test",
            "reviewReason": "preview matched expected graph facts",
        })
        performance = strategy_service.record_performance_sample(proposal_id, {
            "portfolioReturnPct": "3.2",
            "benchmarkReturnPct": "1.1",
            "maxDrawdownPct": "-0.8",
            "signalCount": "2",
            "falsePositiveCount": "1",
        })

        proposal = proposal_store.get(proposal_id)
        actions = [item["action"] for item in proposal.lifecycle.get("reviewLog") or []]
        self.assertEqual("approved", approved["status"])
        self.assertEqual("approved", proposal.status)
        self.assertIn("approved", actions)
        self.assertIn("performance-recorded", actions)
        self.assertEqual(1, performance["performance"]["summary"]["sampleCount"])
        self.assertEqual(2.1, performance["performance"]["summary"]["avgExcessReturnPct"])
        self.assertEqual(0.5, performance["performance"]["summary"]["falsePositiveRate"])

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

    def test_auto_apply_promotable_experiment_and_queues_growth_notification(self):
        store = MemoryExperimentStore()
        repository = FakeOntologyRepository()
        queue = FakeNotificationQueue()
        service = OntologyLabService(
            repository,
            store,
            monitor_store=FakeMonitorStore(),
            notification_queue=queue,
            settings={"ontologyLabAutoApplyMinScore": "75"},
        )
        experiment_id = service.create({
            "title": "AAPL auto growth lab",
            "symbols": ["AAPL"],
            "rules": [candidate_rule()],
        })["experiment"]["id"]
        service.run(experiment_id)
        experiment = store.get(experiment_id)
        experiment.last_result["promotionReadiness"] = {
            "status": "promote-candidate",
            "score": 82,
            "reason": "unit-test promotable",
        }
        experiment.last_result["inference"]["aggregateDelta"]["derivedRelationCount"] = 8
        store.save(experiment)

        automation = service.automate_latest_result(experiment, "scheduled")

        self.assertEqual("applied", automation["status"])
        self.assertEqual("auto-applied", automation["action"])
        self.assertEqual(1, len(repository.saved_rulebox_payloads))
        self.assertEqual(1, len(repository.run_rulebox_payloads))
        self.assertEqual(1, len(repository.saved_tbox_graphs))
        self.assertEqual(1, len(queue.jobs))
        job = queue.jobs[0]
        self.assertEqual("notification", job.message_type)
        self.assertTrue(job.dedupe_key.startswith("ontology-lab:auto-applied:"))
        self.assertIn("자동 반영 완료", job.text)
        self.assertEqual("ontologyLabAutomation", job.context["source"])
        self.assertEqual("applied", job.context["ontologyLabAutomation"]["status"])
        experiment = store.get(experiment_id)
        self.assertEqual("completed", experiment.status)
        self.assertEqual("applied", experiment.last_result["automation"]["status"])
        self.assertEqual("applied", experiment.run_history[0]["automation"]["status"])

    def test_scheduled_needs_review_experiment_only_queues_review_notification(self):
        store = MemoryExperimentStore()
        repository = FakeOntologyRepository()
        queue = FakeNotificationQueue()
        service = OntologyLabService(
            repository,
            store,
            monitor_store=FakeMonitorStore(),
            notification_queue=queue,
            settings={"ontologyLabAutoApplyEnabled": "1", "ontologyLabNotifyEnabled": "1"},
        )
        experiment_id = service.create({
            "title": "AAPL review lab",
            "symbols": ["AAPL"],
            "rules": [candidate_rule()],
        })["experiment"]["id"]
        service.activate(experiment_id)

        result = service.run_once(force=True)

        self.assertEqual(1, result["runCount"])
        self.assertEqual("review-required", result["experiments"][0]["automation"]["status"])
        self.assertEqual(0, len(repository.saved_rulebox_payloads))
        self.assertEqual(0, len(repository.run_rulebox_payloads))
        self.assertEqual(0, len(repository.saved_tbox_graphs))
        self.assertEqual(1, len(queue.jobs))
        self.assertIn("검토 후 반영 필요", queue.jobs[0].text)
        self.assertEqual("active", store.get(experiment_id).status)

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

    def test_rulebox_contract_assigns_unique_condition_ids(self):
        rule = candidate_rule()
        rule["conditions"] = [
            {
                "kind": "subject_property",
                "description": "AAPL 대상 실험입니다.",
                "field": "symbol",
                "operator": "==",
                "value": "AAPL",
            },
            {
                "condition_id": "",
                "kind": "subject_property",
                "description": "보유 출처 실험입니다.",
                "field": "source",
                "operator": "==",
                "value": "holding",
            },
            {
                "condition_id": "condition-1",
                "kind": "subject_property",
                "description": "중복 ID 실험입니다.",
                "field": "sector",
                "operator": "==",
                "value": "Tech",
            },
        ]

        normalized = GraphInferenceRule.from_dict(rule).to_dict()

        self.assertEqual(
            ["condition-1", "condition-2", "condition-1-2"],
            [item["condition_id"] for item in normalized["conditions"]],
        )


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


def ai_candidate_result():
    return {
        "status": "ok",
        "symbols": ["AAPL"],
        "candidates": [
            {
                "id": "ai-candidate:test-lab",
                "title": "AI 다음 점검 실험",
                "status": "candidate",
                "priority": 77,
                "rationale": "반복 신호를 다음 점검 관계로 검증합니다.",
                "expectedEffect": "AI 판단 근거에 점검 컨텍스트를 추가합니다.",
                "risk": "과도한 점검 관계가 생길 수 있습니다.",
                "proposedRule": candidate_rule(),
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
