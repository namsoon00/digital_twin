import unittest

from digital_twin.application.ai_inference_queue_service import NotificationAIRequestEnqueuer
from digital_twin.application.notification.quality import ontology_quality_gate_context
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.ontology_decision_quality import (
    QUALITY_CONTRACT_VERSION,
    build_ontology_decision_quality_snapshot,
)


def v2_context():
    return {
        "messageType": "investmentInsight",
        "investmentReasoningCaseId": "case:1",
        "investmentReasoningCase": {
            "caseId": "case:1",
            "requestId": "reasoning:1",
            "deploymentId": "ontology-v2-active",
            "releaseFingerprint": "release:1",
            "sourceAboxSnapshotIds": ["abox:1"],
            "inferenceGenerationIds": ["generation:1"],
        },
        "v2DecisionSynthesis": {
            "synthesis_id": "synthesis:1",
            "source_abox_snapshot_id": "abox:1",
            "inference_generation_id": "generation:1",
            "graph_candidate_action": "HOLD",
            "allowed_actions": ["HOLD"],
            "eligible_hypothesis_ids": ["hypothesis:1"],
            "data_state": "sufficient",
            "graph_trace_complete": True,
            "judgement_blocked": False,
        },
        "ontologyRelationContext": {
            "sourceAboxSnapshotId": "abox:1",
            "inferenceGenerationId": "generation:1",
            "inferenceGenerationAt": "2026-08-22T00:00:00Z",
            "generationAligned": True,
            "nativeTypeDbReasoningUsed": True,
            "graphStoreInference": {"traces": [{"id": "trace:1"}]},
        },
        "dataFreshness": {"status": "fresh"},
    }


class NotificationDecisionQualityTests(unittest.TestCase):
    def test_v2_quality_snapshot_is_ready_and_reproducible(self):
        context = v2_context()

        first = build_ontology_decision_quality_snapshot(context)
        second = build_ontology_decision_quality_snapshot(context)

        self.assertEqual(QUALITY_CONTRACT_VERSION, first["contractVersion"])
        self.assertEqual("ready", first["validationState"])
        self.assertEqual("passed", first["pipelineValidation"])
        self.assertEqual("sufficient", first["evidenceQuality"])
        self.assertEqual("ready", first["decisionConfidence"])
        self.assertEqual("eligible", first["executionEligibility"])
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual("ready", ontology_quality_gate_context(context)["validationState"])

    def test_generation_mismatch_blocks_the_pipeline_axis(self):
        context = v2_context()
        context["ontologyRelationContext"]["inferenceGenerationId"] = "generation:other"

        quality = build_ontology_decision_quality_snapshot(context)

        self.assertEqual("blocked", quality["validationState"])
        self.assertEqual("failed", quality["pipelineValidation"])
        self.assertTrue(any("추론 세대" in item for item in quality["errors"]))

    def test_enqueue_calculates_quality_after_reasoning_context_capture(self):
        captured = {}

        class Queue:
            def enqueue(self, _job, request):
                captured.update(request.context)
                return {"status": "awaiting-ai", "requestId": request.request_id}

        class Orchestrator:
            def capture_ai_context(self, _case_id, context):
                return {**context, **v2_context()}

            def ai_queued(self, *_args):
                return None

            def case_superseded(self, *_args):
                return None

        job = NotificationJob.create(
            "notification",
            account_id="main",
            message_type="investmentInsight",
            context={
                "investmentReasoningCaseId": "case:1",
                "investmentReasoningCase": {"caseId": "case:1"},
            },
        )

        NotificationAIRequestEnqueuer(
            Queue(),
            reasoning_orchestrator=Orchestrator(),
        ).enqueue(job)

        self.assertEqual("ready", captured["ontologyDecisionQuality"]["validationState"])
        self.assertEqual("ready", captured["ontologyQualityGate"]["validationState"])


if __name__ == "__main__":
    unittest.main()
