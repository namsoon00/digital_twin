import unittest
from types import SimpleNamespace

from digital_twin.application.investment_reasoning import InvestmentReasoningOrchestrator
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from digital_twin.domain.independent_reasoning import independent_reasoning_request
from digital_twin.domain.investment_reasoning import (
    CASE_BLOCKED,
    CASE_PUBLISHED,
    CASE_VALIDATED,
    FactDelta,
    GraphHypothesisManager,
)


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
