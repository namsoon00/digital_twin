import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.decision_continuity_service import DecisionContinuityService
from digital_twin.application.ai_inference_queue_service import NotificationAIRequestEnqueuer
from digital_twin.application.notification_ai_gate_message import decision_continuity_rows
from digital_twin.application.notification_decision_memory import context_with_previous_investment_decision
from digital_twin.domain.decision_continuity import build_decision_continuity_packet
from digital_twin.domain.notification_ai_decision_brief import (
    build_notification_ai_decision_prompt,
    notification_ai_decision_brief,
)
from digital_twin.domain.notifications import NotificationJob


class EpisodeStore:
    def __init__(self, episode):
        self.episode = episode
        self.list_calls = 0

    def list(self, account_id="", symbol="", limit=3):
        self.list_calls += 1
        return [self.episode]


class DomainStore:
    def __init__(self):
        self.continuity_calls = 0

    def decision_continuity_context(self, portfolio_id, account_id, symbol, decision_episode_id):
        self.continuity_calls += 1
        return {
            "actionObservations": [{
                "observationId": "observation:1",
                "observedAt": "2026-08-16T01:00:00Z",
                "activityEpisodeId": "activity:1",
                "priorDecisionEpisodeId": decision_episode_id,
                "priorAction": "ADD",
                "observedDirection": "increase",
                "correspondence": "aligned",
                "elapsedMinutes": 60,
                "previousQuantity": "8",
                "observedQuantity": "10",
                "quantityDelta": "2",
                "causalityClaimed": False,
            }],
            "currentPosition": {
                "symbol": symbol,
                "quantity": "10",
                "observedAt": "2026-08-16T01:00:00Z",
                "observationState": "observed",
            },
        }

    def execution_feedback_for_decisions(self, episode_ids):
        return {episode_ids[0]: {"actionPlans": [{"planId": "plan:1", "action": "ADD"}]}}

    def lifecycle_feedback_for_decisions(self, episode_ids):
        return {episode_ids[0]: {
            "decisionReviews": [{
                "reviewId": "review:1",
                "selectedHypothesisStatus": "supported",
                "evidenceStillValid": True,
            }],
        }}


def prior_episode():
    return {
        "episodeId": "decision:previous",
        "accountId": "main",
        "portfolioId": "portfolio:main",
        "symbol": "005930",
        "subjectName": "삼성전자",
        "action": "ADD",
        "reviewLevel": "check",
        "dataState": "sufficient",
        "validationState": "ready",
        "decisionReadiness": "ready",
        "selectedHypothesisId": "hypothesis:recovery",
        "decisionSummary": "가격과 수급 회복을 확인했습니다.",
        "decidedAt": "2026-08-16T00:00:00Z",
        "status": "active",
        "source": "notification-ai",
        "hypothesisSet": {"hypotheses": [{
            "hypothesisId": "hypothesis:recovery",
            "templateId": "template:recovery",
            "claim": "회복이 이어질 수 있다.",
            "stance": "support",
            "verificationStatus": "verified-by-current-evidence",
        }]},
        "followUpConditions": [{
            "conditionId": "follow-up:1",
            "field": "currentPrice",
            "operator": ">=",
            "threshold": 80000,
            "purpose": "strengthen",
            "label": "8만원 회복",
            "status": "satisfied",
            "currentValue": 81000,
        }],
        "outcomes": [{
            "outcomeId": "outcome:1",
            "episodeId": "decision:previous",
            "observedAt": "2026-08-16T01:00:00Z",
            "price": 81000,
            "priceChangeFromDecisionPct": 2.5,
            "selectedHypothesisStatus": "supported",
        }],
    }


class DecisionContinuityTests(unittest.TestCase):
    def test_packet_identity_ignores_capture_time_but_preserves_observation_semantics(self):
        inputs = {
            "account_id": "main",
            "symbol": "005930",
            "previous_decision": prior_episode(),
            "follow_up_conditions": prior_episode()["followUpConditions"],
            "action_observations": [{
                "observationId": "observation:1",
                "observedDirection": "increase",
                "causalityClaimed": False,
            }],
        }
        first = build_decision_continuity_packet(captured_at="2026-08-16T01:00:00Z", **inputs)
        second = build_decision_continuity_packet(captured_at="2026-08-16T01:05:00Z", **inputs)

        self.assertEqual(first["packetId"], second["packetId"])
        self.assertEqual("observed", first["observationState"]["userAction"])
        self.assertFalse(first["observationState"]["causalityClaimed"])
        self.assertFalse(first["observationState"]["noActionMeansHold"])

    def test_service_joins_prior_decision_followups_outcomes_and_account_activity(self):
        episodes = EpisodeStore(prior_episode())
        domain = DomainStore()
        packet = DecisionContinuityService(episodes, domain).build(
            account_id="main",
            symbol="005930",
            captured_at="2026-08-16T01:05:00Z",
        )

        self.assertEqual("available", packet["status"])
        self.assertEqual("ADD", packet["previousDecision"]["action"])
        self.assertEqual("hypothesis:recovery", packet["selectedHypothesis"]["hypothesisId"])
        self.assertEqual("satisfied", packet["followUpConditions"][0]["status"])
        self.assertEqual(2.5, packet["observedOutcomes"][0]["priceChangeFromDecisionPct"])
        self.assertEqual("2", packet["actionObservations"][0]["quantityDelta"])
        self.assertEqual("10", packet["currentPosition"]["quantity"])
        self.assertTrue(packet["summary"]["actionPlanRecorded"])
        self.assertFalse(packet["summary"]["executionRecorded"])
        self.assertTrue(packet["summary"]["lifecycleReviewRecorded"])

    def test_captured_packet_is_reused_without_second_database_read(self):
        episodes = EpisodeStore(prior_episode())
        domain = DomainStore()
        continuity = DecisionContinuityService(episodes, domain)
        context = {
            "accountId": "main",
            "rawSymbol": "005930",
            "referenceDate": "2026-08-16T01:05:00Z",
        }

        first = context_with_previous_investment_decision(
            context,
            episodes,
            continuity,
            account_id="main",
        )
        second = context_with_previous_investment_decision(
            first,
            episodes,
            continuity,
            account_id="main",
        )

        self.assertEqual(1, episodes.list_calls)
        self.assertEqual(1, domain.continuity_calls)
        self.assertEqual(
            first["decisionContinuityPacket"]["packetId"],
            second["decisionContinuityPacket"]["packetId"],
        )

    def test_ai_queue_captures_continuity_before_persisting_immutable_request(self):
        episodes = EpisodeStore(prior_episode())
        domain = DomainStore()
        continuity = DecisionContinuityService(episodes, domain)

        class Queue:
            request = None

            def enqueue(self, _job, request):
                self.request = request
                return {"status": "queued"}

        queue = Queue()
        job = NotificationJob.create(
            "continuity queue test",
            account_id="main",
            message_type="investmentInsight",
            context={
                "messageType": "investmentInsight",
                "rawSymbol": "005930",
                "referenceDate": "2026-08-16T01:05:00Z",
            },
        )
        outcome = NotificationAIRequestEnqueuer(
            queue,
            settings={},
            decision_episode_store=episodes,
            continuity_service=continuity,
        ).enqueue(job)

        self.assertEqual("queued", outcome["status"])
        self.assertEqual(
            "decision-continuity-packet-v2",
            queue.request.context["decisionContinuityPacket"]["contractVersion"],
        )
        self.assertEqual(
            "notification-ai-decision-contract-v4",
            queue.request.context["notificationAiDecisionContractVersion"],
        )

    def test_ai_brief_and_prompt_keep_continuity_contract(self):
        context = {
            "messageType": "investmentInsight",
            "accountId": "main",
            "rawSymbol": "005930",
            "displayTarget": "삼성전자 / 005930",
            "decisionContinuityPacket": build_decision_continuity_packet(
                account_id="main",
                symbol="005930",
                captured_at="2026-08-16T01:05:00Z",
                previous_decision=prior_episode(),
                follow_up_conditions=prior_episode()["followUpConditions"],
            ),
            "ontologyRelationContext": {
                "subject": {"symbol": "005930", "name": "삼성전자"},
                "facts": {"currentPrice": 81000},
            },
        }

        brief = notification_ai_decision_brief(context, {})
        prompt = build_notification_ai_decision_prompt(context, {}, decision_brief=brief)
        prompt_payload = json.loads(prompt.split("DecisionBrief:\n", 1)[1])

        self.assertEqual("decision-continuity-packet-v2", brief["decisionContinuity"]["contractVersion"])
        self.assertEqual("ADD", prompt_payload["decisionContinuity"]["previousDecision"]["action"])
        self.assertIn("actionObservations가 없다고", prompt)

    def test_notification_trace_states_quantity_change_without_claiming_causality(self):
        packet = DecisionContinuityService(EpisodeStore(prior_episode()), DomainStore()).build(
            account_id="main",
            symbol="005930",
            captured_at="2026-08-16T01:05:00Z",
        )

        rows = decision_continuity_rows({"decisionContinuityPacket": packet})

        self.assertTrue(any("8 → 10주" in item for item in rows))
        self.assertTrue(any("단정하지 않습니다" in item for item in rows))
        self.assertTrue(any("후속 조건 성립" in item for item in rows))


if __name__ == "__main__":
    unittest.main()
