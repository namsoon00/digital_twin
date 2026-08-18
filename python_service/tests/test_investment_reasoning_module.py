import inspect
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from digital_twin.application.investment_reasoning import (
    InvestmentReasoningOrchestrator,
    V2GraphDecisionCandidateBuilder,
)
from digital_twin.application.investment_reasoning.decision_synthesis import (
    build_investment_insight_events_by_snapshot,
)
from digital_twin.domain.data_freshness import evaluate_notification_data_freshness
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from digital_twin.domain.independent_reasoning import independent_reasoning_request
from digital_twin.domain.investment_reasoning import (
    CASE_BLOCKED,
    CASE_DECISION_SYNTHESIZED,
    CASE_PUBLISHED,
    CASE_VALIDATED,
    FactDelta,
    GraphHypothesisManager,
    DecisionSynthesis,
    ActionAlternative,
    decision_synthesis_from_relation_context,
    reasoning_rule_inventory,
)
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position


class InMemoryReasoningCaseRepository:
    def __init__(self):
        self.cases = {}

    def save(self, reasoning_case):
        self.cases[reasoning_case.case_id] = reasoning_case
        return reasoning_case

    def get(self, case_id):
        return self.cases.get(case_id)

    def get_by_request(self, request_id):
        return next(
            (item for item in self.cases.values() if item.request_id == request_id),
            None,
        )


def reasoning_request(fact_types=None):
    event = DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id="market-observation:NVDA",
        event_id="event:investment-reasoning",
        occurred_at="2026-08-16T00:00:00Z",
        payload={
            "accountIds": ["account:1"],
            "affectedSymbols": ["NVDA"],
            "factTypes": list(fact_types or ["PRICE_OBSERVATION"]),
            "sourceObservedAt": "2026-08-16T00:00:00Z",
            "workClass": "MARKET",
        },
    )
    return independent_reasoning_request("ontology-v2-shadow", [event])


def hypothesis_candidate(hypothesis_id="hypothesis:recovery"):
    return {
        "metadata": {
            "ontologyRelationContext": {
                "investmentBrain": {
                    "hypothesisSet": {
                        "hypotheses": [{
                            "hypothesisId": hypothesis_id,
                            "familyId": "recovery",
                            "label": "Price recovery is supported",
                            "candidateAction": "BUY",
                            "supportingRuleIds": ["graph.price.recovery.v1"],
                            "supportingEvidenceIds": ["evidence:price-window"],
                            "counterEvidenceIds": ["evidence:weak-volume"],
                            "invalidationConditions": ["price recovery fails"],
                        }],
                    },
                },
            },
        },
    }


class InvestmentReasoningModuleTests(unittest.TestCase):
    def test_v2_candidate_builder_has_no_v1_monitor_dependency(self):
        source = inspect.getsource(V2GraphDecisionCandidateBuilder)

        self.assertNotIn("RealtimeMonitor", source)
        self.assertNotIn("events_for_snapshot", source)

    def test_v2_candidate_preserves_source_freshness_for_notification_delivery(self):
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        snapshot = AccountSnapshot(
            "account:1",
            "Test",
            "toss",
            "live",
            "ok",
            observed_at,
            PortfolioSummary(1000.0, 1000.0, 0.0, [], [], 100.0),
            positions=[Position(
                symbol="NVDA",
                name="NVIDIA",
                current_price=200.0,
                quote_source="market-feed",
                data_quality="actual",
                source_as_of=observed_at,
                source_fetched_at=observed_at,
            )],
        )
        relation = hypothesis_candidate()["metadata"]["ontologyRelationContext"]
        relation.update({
            "subject": {"symbol": "NVDA", "name": "NVIDIA"},
            "facts": {"source": "watchlist", "currentPrice": 200.0},
            "source": "typedbInferenceBox",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "sourceAboxSnapshotId": "abox:1",
            "inferenceGenerationId": "generation:1",
            "generationAligned": True,
            "allowedActions": ["WATCH"],
            "decision": {
                "candidateAction": "WATCH",
                "selectedRuleId": "graph.price.recovery.v1",
                "notificationSeverity": "WATCH",
                "basis": "typedbInferenceBox",
            },
            "graphStoreInference": {
                "traces": [{"id": "trace:1"}],
                "relations": [{"ruleId": "graph.price.recovery.v1"}],
            },
        })
        synthesis = decision_synthesis_from_relation_context("account:1", relation)
        builder = V2GraphDecisionCandidateBuilder({}, SimpleNamespace(sent={}))

        event = builder._base_event(snapshot, relation, synthesis)

        self.assertEqual("fresh", event.metadata["dataFreshness"]["status"])
        source = event.metadata["dataFreshness"]["sources"][0]
        self.assertEqual(observed_at, source["sourceAsOf"])
        self.assertEqual(observed_at, event.metadata["reasoningSourceObservedAt"])
        insight = build_investment_insight_events_by_snapshot([snapshot], [event])[0]
        freshness = evaluate_notification_data_freshness(
            {"messageType": insight.rule, **dict(insight.metadata or {})},
            now=datetime.now(timezone.utc),
        )
        self.assertEqual(observed_at, insight.generated_at)
        self.assertTrue(freshness.should_send)

    def test_typedb_relation_context_becomes_auditable_action_alternatives(self):
        relation = hypothesis_candidate()["metadata"]["ontologyRelationContext"]
        relation.update({
            "subject": {"symbol": "NVDA", "name": "NVIDIA"},
            "sourceAboxSnapshotId": "abox:1",
            "inferenceGenerationId": "generation:1",
            "generationAligned": True,
            "allowedActions": ["BUY", "WATCH"],
            "blockedActions": ["SELL"],
            "decision": {
                "candidateAction": "BUY",
                "selectedRuleId": "graph.price.recovery.v1",
                "nextChecks": ["volume confirmation"],
            },
            "graphStoreInference": {
                "traces": [{"id": "trace:1"}],
                "relations": [{
                    "ruleId": "graph.price.recovery.v1",
                    "candidateAction": "BUY",
                }],
            },
        })

        synthesis = decision_synthesis_from_relation_context("account:1", relation)

        self.assertEqual("BUY", synthesis.graph_candidate_action)
        self.assertEqual(("hypothesis:recovery",), synthesis.eligible_hypothesis_ids)
        self.assertEqual(["BUY"], [item.action for item in synthesis.alternatives])
        self.assertTrue(synthesis.graph_trace_complete)
        self.assertIn("SELL", synthesis.blocked_actions)

    def test_reasoning_case_persists_decision_synthesis_before_ai(self):
        repository = InMemoryReasoningCaseRepository()
        orchestrator = InvestmentReasoningOrchestrator(repository)
        reasoning_case = orchestrator.start(reasoning_request())
        orchestrator.input_ready(reasoning_case.case_id)
        orchestrator.inference_completed(
            reasoning_case.case_id,
            {"account:1": {
                "verified": True,
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
            }},
            {},
            10,
        )
        orchestrator.hypotheses_ready(reasoning_case.case_id, [hypothesis_candidate()])
        synthesis = DecisionSynthesis(
            synthesis_id="synthesis:1",
            account_id="account:1",
            symbol="NVDA",
            source_abox_snapshot_id="abox:1",
            inference_generation_id="generation:1",
            graph_candidate_action="BUY",
            allowed_actions=("BUY", "WATCH"),
            alternatives=(ActionAlternative(
                action="BUY",
                hypothesis_ids=("hypothesis:recovery",),
                decision_eligible=True,
            ),),
            eligible_hypothesis_ids=("hypothesis:recovery",),
        )

        synthesized = orchestrator.decisions_synthesized(reasoning_case.case_id, [synthesis])

        self.assertEqual(CASE_DECISION_SYNTHESIZED, synthesized.stage)
        self.assertEqual("synthesis:1", synthesized.to_dict()["decisionSyntheses"][0]["synthesis_id"])
        self.assertEqual(
            "BUY",
            orchestrator.compact_context(synthesized)["decisionSyntheses"][0]["graphCandidateAction"],
        )

    def test_rule_inventory_exposes_unroutable_rules_before_release(self):
        inventory = reasoning_rule_inventory([{
            "rule_id": "graph.complete.v1",
            "enabled": True,
            "domain_manifest": {
                "module": "decision-intelligence",
                "dependencyContractVersion": "v2",
                "triggerDependencies": [{"conditionId": "price"}],
                "derivedOutputs": [{"relationType": "SUPPORTS"}],
                "invalidationContract": {"mode": "not-materialized"},
                "executionStage": "critical",
                "lifecycleClass": "hot",
                "decisionEffects": ["support"],
            },
        }, {
            "rule_id": "graph.incomplete.v1",
            "enabled": True,
        }])

        self.assertEqual(2, inventory["ruleCount"])
        self.assertEqual(1, inventory["invalidRuleCount"])
        self.assertFalse(inventory["releaseReady"])

    def test_fact_delta_routes_market_price_to_realtime_lane(self):
        delta = FactDelta.from_request(reasoning_request())

        self.assertEqual("REALTIME", delta.lane)
        self.assertEqual(("NVDA",), delta.symbols)
        self.assertIn("PRICE_OBSERVATION", delta.fact_types)

    def test_hypothesis_manager_accepts_only_graph_context(self):
        manager = GraphHypothesisManager()

        graph_hypotheses = manager.from_candidates([hypothesis_candidate()])
        invented_hypotheses = manager.from_candidates([{
            "hypotheses": [{"hypothesisId": "python:invented"}],
        }])

        self.assertEqual(["hypothesis:recovery"], [item.hypothesis_id for item in graph_hypotheses])
        self.assertEqual((), invented_hypotheses)

    def test_case_is_published_only_after_inference_ai_validation_and_delivery(self):
        repository = InMemoryReasoningCaseRepository()
        orchestrator = InvestmentReasoningOrchestrator(repository)
        request = reasoning_request()
        reasoning_case = orchestrator.start(request, {
            "releaseFingerprint": "release:1",
            "validationCohortId": "cohort:1",
        })
        orchestrator.input_ready(reasoning_case.case_id)
        orchestrator.inference_completed(
            reasoning_case.case_id,
            {"account:1": {
                "verified": True,
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
                "relationCount": 3,
                "traceCount": 2,
            }},
            {"account:1": {"status": "ok"}},
            125,
        )
        orchestrator.hypotheses_ready(reasoning_case.case_id, [hypothesis_candidate()])
        orchestrator.ai_queued(reasoning_case.case_id, "ai-request:1", "notification:1")
        result = SimpleNamespace(
            request_id="ai-request:1",
            result_id="ai-result:1",
            model="gpt-test",
            reasoning_effort="max",
            latency_ms=80,
            validation_state="verified",
            response={
                "action": "BUY",
                "confidence": 0.81,
                "selectedHypothesisId": "hypothesis:recovery",
                "validationState": "verified",
                "summary": "Recovery hypothesis is supported.",
            },
        )
        request_stub = SimpleNamespace(notification_job_id="notification:1")
        validated = orchestrator.ai_completed(
            request_stub,
            {"investmentReasoningCaseId": reasoning_case.case_id},
            result,
        )

        self.assertEqual(CASE_VALIDATED, validated.stage)
        self.assertFalse(validated.final_decision.published)
        published = orchestrator.notification_published({
            "investmentReasoningCaseId": reasoning_case.case_id,
        })
        self.assertEqual(CASE_PUBLISHED, published.stage)
        self.assertTrue(published.final_decision.published)
        self.assertTrue(published.inference_result.trace_complete)

    def test_ai_cannot_publish_without_a_typedb_hypothesis(self):
        repository = InMemoryReasoningCaseRepository()
        orchestrator = InvestmentReasoningOrchestrator(repository)
        reasoning_case = orchestrator.start(reasoning_request())
        orchestrator.input_ready(reasoning_case.case_id)
        orchestrator.inference_completed(
            reasoning_case.case_id,
            {"account:1": {
                "verified": True,
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
            }},
            {},
            10,
        )
        orchestrator.hypotheses_ready(reasoning_case.case_id, [])
        orchestrator.ai_queued(reasoning_case.case_id, "ai-request:2", "notification:2")
        blocked = orchestrator.ai_completed(
            SimpleNamespace(notification_job_id="notification:2"),
            {"investmentReasoningCaseId": reasoning_case.case_id},
            SimpleNamespace(
                request_id="ai-request:2",
                result_id="ai-result:2",
                model="gpt-test",
                reasoning_effort="max",
                latency_ms=20,
                validation_state="verified",
                response={
                    "action": "BUY",
                    "selectedHypothesisId": "python:invented",
                    "validationState": "verified",
                },
            ),
        )

        self.assertEqual(CASE_BLOCKED, blocked.stage)
        self.assertIn("hypothesis set is empty", blocked.errors[-1]["reason"])


if __name__ == "__main__":
    unittest.main()
