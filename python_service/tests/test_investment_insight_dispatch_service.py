import unittest

from digital_twin.application.investment_insight_dispatch_service import (
    InvestmentInsightDispatchService,
)
from digital_twin.domain.investment_reasoning import (
    ARCHIVE,
    HANDOFF_AI,
    INVALID,
    PUBLISH_TYPEDB,
    CandidateSetSnapshot,
    DecisionPublication,
    DecisionSynthesis,
    SubjectDecisionCase,
    inference_dispatch_decision,
)
from digital_twin.domain.message_types import INVESTMENT_INSIGHT
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.portfolio import AlertEvent


def subject_case(case_id, *, action_authority="observe", eligible=(), outcome="OBSERVATION"):
    synthesis = DecisionSynthesis(
        synthesis_id="synthesis:" + case_id,
        account_id="main",
        symbol="MSTR",
        source_abox_snapshot_id="abox:mstr:1",
        inference_generation_id="generation:mstr:1",
        action_authority=action_authority,
        eligible_hypothesis_ids=tuple(eligible),
        execution_eligible_hypothesis_ids=tuple(eligible),
        allowed_actions=("HOLD", "TRIM") if eligible else (),
        disposition_code="ACTIONABLE_DECISION" if eligible else "CONTEXT_OBSERVATION",
    )
    candidate = CandidateSetSnapshot(
        candidate_set_id="candidate:" + case_id,
        fingerprint=(case_id[-1:] or "a") * 64,
        account_id="main",
        symbol="MSTR",
        source_abox_snapshot_id="abox:mstr:1",
        inference_generation_id="generation:mstr:1",
        synthesis_id=synthesis.synthesis_id,
        eligible_hypothesis_ids=tuple(eligible),
        execution_eligible_hypothesis_ids=tuple(eligible),
        allowed_actions=synthesis.allowed_actions,
        disposition_code=synthesis.disposition_code,
    )
    return SubjectDecisionCase(
        subject_case_id=case_id,
        batch_case_id="batch:mstr:1",
        request_id="request:mstr:1",
        deployment_id="deployment:test",
        release_fingerprint="release:test",
        account_id="main",
        symbol="MSTR",
        source_abox_snapshot_id="abox:mstr:1",
        inference_generation_id="generation:mstr:1",
        synthesis=synthesis,
        candidate_set=candidate,
        stage=outcome,
        publication=DecisionPublication(
            publication_id="publication:" + case_id,
            subject_case_id=case_id,
            outcome_kind=outcome,
            fingerprint=candidate.fingerprint,
        ),
    )


def context_observation(case):
    rule = {
        "ruleId": "graph.benchmark.beta.context.v1",
        "label": "벤치마크 민감도 변화",
        "matched": True,
        "knowledgeBasis": {
            "owner": "ontology-semantic",
            "ruleKind": "context-observation",
            "decisionEligibility": "reference-only",
            "requiresHypothesis": False,
        },
    }
    return {
        "investmentSubjectDecisionCaseId": case.subject_case_id,
        "investmentSubjectDecisionCase": case.to_dict(),
        "decisionPublication": case.publication.to_dict(),
        "ontologyInsight": {
            "semanticComponents": {
                "materialSourceEventKeys": ["market-observation:MSTR:beta:changed"],
            },
        },
        "ontologyRelationContext": {
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "sourceAboxSnapshotId": case.source_abox_snapshot_id,
            "inferenceGenerationId": case.inference_generation_id,
            "subject": {"symbol": "MSTR", "market": "US"},
            "facts": {"symbol": "MSTR", "currentPrice": 132.38},
            "activeRules": [rule],
            "matchedRules": [rule],
            "decision": {
                "selectedRuleId": rule["ruleId"],
                "basis": "typedbInferenceBox",
            },
            "graphStoreInference": {
                "graphStore": "typedb",
                "sourceAboxSnapshotId": case.source_abox_snapshot_id,
                "inferenceGenerationId": case.inference_generation_id,
                "relations": [rule],
                "traces": [{"id": "trace:mstr:beta", **rule}],
            },
        },
    }


def alert(case, context, suffix):
    return AlertEvent(
        account_id="main",
        account_label="기본",
        severity="WATCH",
        rule=INVESTMENT_INSIGHT,
        key="alert:" + suffix,
        title="MSTR TypeDB result",
        lines=["TypeDB 관계 변화"],
        symbol="MSTR",
        metadata=dict(context),
    )


class FakeOrchestrator:
    def __init__(self, cases):
        self.cases = {case.subject_case_id: case for case in cases}
        self.reconciled = []

    def required_subject(self, subject_case_id):
        return self.cases[subject_case_id]

    @staticmethod
    def compact_subject_context(case):
        return case.to_dict()

    def record_inference_dispatch(self, subject_case_id, decision, delivery_state=""):
        case = self.required_subject(subject_case_id)
        case.record_inference_dispatch(decision)
        if delivery_state:
            case.mark_delivery(delivery_state, decision.reason)
        return case

    def decision_delivery_reconciled(self, context, outcome):
        case_id = str(context.get("investmentSubjectDecisionCaseId") or "")
        case = self.required_subject(case_id)
        case.mark_delivery(
            "queued" if outcome.get("queued") else "suppressed",
            outcome.get("reason"),
        )
        self.reconciled.append(dict(outcome))
        return case


class FakeIngress:
    @staticmethod
    def job_from_alert(event, source_event=None, account_context=None):
        return NotificationJob.create(
            event.title,
            account_id=event.account_id,
            account_label=event.account_label,
            message_type=event.rule,
            source_event_id=str(getattr(source_event, "event_id", "") or ""),
            source_event_name=str(getattr(source_event, "name", "") or ""),
            context=dict(event.metadata or {}),
        )


class FakeNotificationQueue:
    def __init__(self):
        self.jobs = {}

    def enqueue(self, job):
        if job.job_id in self.jobs:
            return False
        self.jobs[job.job_id] = job
        return True

    def get(self, job_id):
        return self.jobs.get(job_id)


class FakeAIHandoff:
    def __init__(self):
        self.events = []

    def enqueue(self, events):
        self.events = list(events)
        return {
            "status": "queued",
            "queuedCount": len(self.events),
            "queuedEvents": list(self.events),
            "outcomes": [
                {"status": "awaiting-ai-insight", "queued": True}
                for _event in self.events
            ],
        }


class InvestmentInsightDispatchServiceTests(unittest.TestCase):
    def test_domain_dispatch_routes_are_explicit_and_fail_closed(self):
        observation = subject_case("subject:observation")
        material_context = context_observation(observation)
        self.assertEqual(
            PUBLISH_TYPEDB,
            inference_dispatch_decision(material_context, observation).route,
        )
        first_dispatch = inference_dispatch_decision(
            material_context,
            observation,
            source_event_id="event:first",
        )
        retried_dispatch = inference_dispatch_decision(
            material_context,
            observation,
            source_event_id="event:retry",
        )
        self.assertEqual(first_dispatch.decision_id, retried_dispatch.decision_id)

        material_context["ontologyInsight"] = {"semanticComponents": {}}
        self.assertEqual(
            ARCHIVE,
            inference_dispatch_decision(material_context, observation).route,
        )

        actionable = subject_case(
            "subject:actionable",
            action_authority="originate",
            eligible=("hypothesis:mstr:trend",),
            outcome="READY",
        )
        self.assertEqual(
            HANDOFF_AI,
            inference_dispatch_decision({"requiresAiJudgement": True}, actionable).route,
        )

        actionable.candidate_set = CandidateSetSnapshot.from_dict({
            **actionable.candidate_set.to_dict(),
            "validationErrors": ["generation-mismatch"],
        })
        self.assertEqual(
            INVALID,
            inference_dispatch_decision({"requiresAiJudgement": True}, actionable).route,
        )

    def test_dispatch_keeps_typedb_publication_and_ai_handoff_independent(self):
        observation = subject_case("subject:observation")
        actionable = subject_case(
            "subject:actionable",
            action_authority="originate",
            eligible=("hypothesis:mstr:trend",),
            outcome="READY",
        )
        orchestrator = FakeOrchestrator([observation, actionable])
        notification_queue = FakeNotificationQueue()
        ai_handoff = FakeAIHandoff()
        service = InvestmentInsightDispatchService(
            FakeIngress(),
            notification_queue,
            ai_handoff,
            orchestrator,
        )
        actionable_context = {
            "investmentSubjectDecisionCaseId": actionable.subject_case_id,
            "investmentSubjectDecisionCase": actionable.to_dict(),
            "requiresAiJudgement": True,
        }

        result = service.dispatch([
            alert(observation, context_observation(observation), "typedb"),
            alert(actionable, actionable_context, "ai"),
        ])

        self.assertEqual("typedb-and-ai-queued", result["status"])
        self.assertEqual(1, result["typedbPublishedCount"])
        self.assertEqual(1, result["aiQueuedCount"])
        self.assertEqual(1, len(notification_queue.jobs))
        self.assertEqual(1, len(ai_handoff.events))
        self.assertEqual("alert:ai", ai_handoff.events[0].key)
        typedb_job = next(iter(notification_queue.jobs.values()))
        self.assertEqual("typedb", typedb_job.context["notificationDecisionOwner"])
        self.assertEqual(
            PUBLISH_TYPEDB,
            typedb_job.context["inferenceDispatchDecision"]["route"],
        )
        self.assertEqual(
            PUBLISH_TYPEDB,
            typedb_job.context["investmentSubjectDecisionCase"][
                "inferenceDispatchDecision"
            ]["route"],
        )
        self.assertNotIn("notificationAiValidatedResponse", typedb_job.context)
        self.assertEqual(PUBLISH_TYPEDB, observation.inference_dispatch_decision.route)
        self.assertEqual(HANDOFF_AI, actionable.inference_dispatch_decision.route)

        repeated = service.dispatch([
            alert(observation, context_observation(observation), "typedb-retry"),
        ])
        self.assertEqual("typedb-queued", repeated["status"])
        self.assertEqual(1, len(notification_queue.jobs))
        self.assertEqual(
            "typedb-notification-already-recorded",
            repeated["outcomes"][0]["status"],
        )


if __name__ == "__main__":
    unittest.main()
