import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_lab_service import (  # noqa: E402
    OntologyLabService,
    ontology_lab_automation_payload,
    ontology_lab_notification_text,
)
from digital_twin.domain.message_types import ONTOLOGY_LAB_EXPERIMENT  # noqa: E402
from digital_twin.domain.notification_ai import build_notification_ai_opinion  # noqa: E402
from digital_twin.domain.notification_templates import modeling_lines  # noqa: E402
from digital_twin.domain.ontology_experiments import OntologyExperiment  # noqa: E402


class MemoryExperimentStore:
    def __init__(self, experiment):
        self.experiment = experiment

    def get(self, experiment_id):
        if experiment_id == self.experiment.experiment_id:
            return self.experiment
        return None


class RecordingNotificationQueue:
    def __init__(self):
        self.jobs = []

    def enqueue(self, job):
        self.jobs.append(job)
        return True


class OntologyLabNotificationTests(unittest.TestCase):
    def setUp(self):
        self.experiment = OntologyExperiment(
            experiment_id="ontology-exp-test",
            title="AI 제안: 검증 보류 관계",
            hypothesis="불완전한 근거가 판단 근거로 승격되지 않도록 검증합니다.",
        )
        self.queue = RecordingNotificationQueue()
        self.service = OntologyLabService(
            ontology_repository=None,
            experiment_store=MemoryExperimentStore(self.experiment),
            notification_queue=self.queue,
            settings={"ontologyLabNotifyEnabled": "1"},
        )

    @staticmethod
    def automation(action):
        return {
            "action": action,
            "runId": "ontology-lab-run-test",
            "readinessStatus": "needs-review",
            "validationState": "conditional",
            "dataState": "sufficient",
            "derivedRelationDelta": 1,
            "relationTypes": ["BLOCKS_VALIDATION_OF"],
            "decisionStages": ["prompt-admission"],
            "recommendations": ["TypeDB에서 후보 규칙 검증"],
            "reason": "readiness-needs-review",
        }

    def test_non_actionable_experiment_results_stay_in_the_lab(self):
        for action in ["notify-result", "notify-data", ""]:
            result = self.service.notify_automation(
                self.experiment.experiment_id,
                self.automation(action),
            )
            self.assertEqual("skipped", result["status"])
            self.assertEqual("non-actionable-experiment-result", result["reason"])
        self.assertEqual([], self.queue.jobs)

    def test_only_actionable_results_use_the_dedicated_message_type(self):
        for action in ["auto-applied", "notify-error", "notify-review"]:
            result = self.service.notify_automation(
                self.experiment.experiment_id,
                self.automation(action),
            )
            self.assertEqual("queued", result["status"])

        self.assertEqual(3, len(self.queue.jobs))
        for job in self.queue.jobs:
            self.assertEqual(ONTOLOGY_LAB_EXPERIMENT, job.message_type)
            self.assertEqual(ONTOLOGY_LAB_EXPERIMENT, job.context["messageType"])

    def test_ready_candidate_becomes_manual_review_when_auto_apply_is_disabled(self):
        self.experiment.last_result = {
            "promotionReadiness": {
                "status": "promote-candidate",
                "validationState": "ready",
                "dataState": "sufficient",
            },
            "inference": {"aggregateDelta": {"derivedRelationCount": 2}},
            "proposedOntologyChanges": {"ruleIds": ["graph.test.ready.v1"]},
        }

        automation = ontology_lab_automation_payload(
            self.experiment,
            self.experiment.last_result,
            "scheduled",
            {"eligible": False, "reason": "auto-apply-disabled"},
        )

        self.assertEqual("review-required", automation["status"])
        self.assertEqual("notify-review", automation["action"])

    def test_notification_uses_customer_labels_instead_of_internal_states(self):
        text = ontology_lab_notification_text(
            self.experiment,
            self.automation("notify-review"),
        )

        self.assertIn("수동 검토 필요", text)
        self.assertIn("추가 검증 필요", text)
        self.assertIn("검증용 데이터 충분", text)
        self.assertNotIn("needs-review", text)
        self.assertNotIn("conditional", text)
        self.assertNotIn("sufficient", text)

    def test_dedicated_experiment_type_has_no_generic_ai_opinion_or_model(self):
        context = {
            "messageType": ONTOLOGY_LAB_EXPERIMENT,
            "rawLines": "상태: 수동 검토 필요",
        }

        self.assertEqual({}, build_notification_ai_opinion(context))
        self.assertEqual([], modeling_lines(context))


if __name__ == "__main__":
    unittest.main()
