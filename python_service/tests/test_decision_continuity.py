import json
from copy import deepcopy
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.decisions.application.decision_continuity_service import DecisionContinuityService
from digital_twin.modules.decisions.application.ai_inference_queue_service import NotificationAIRequestEnqueuer
from digital_twin.modules.notifications.application.notification_ai_gate_message import decision_continuity_rows
from digital_twin.modules.decisions.application.notification_decision_memory import context_with_previous_investment_decision, context_with_previous_investment_insight, context_with_previous_delivered_investment_insight, frozen_current_position
from digital_twin.modules.decisions.domain.decision_continuity import build_decision_continuity_packet
from digital_twin.modules.decisions.domain.investment_decision_actionability import (
    investment_decision_actionability,
)
from digital_twin.modules.outcomes.domain.decision_follow_up import (
    evaluate_follow_up_conditions,
    normalize_follow_up_conditions,
)
from digital_twin.modules.decisions.domain.notification_ai_decision_brief import (
    AI_DECISION_CONTRACT_VERSION,
    _compact_portfolio_lifecycle,
    build_notification_ai_decision_prompt,
    notification_ai_decision_brief,
    notification_ai_execution_profile,
)
from digital_twin.modules.decisions.domain.notification_ai_prompt_release import AI_DECISION_PROMPT_VERSION
from digital_twin.modules.notifications.domain.notifications import NotificationJob


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
    row = {
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
        "decisionAssurance": {"executionEligibility": "eligible"},
        "selectedHypothesisId": "hypothesis:recovery",
        "evidenceIds": ["evidence:price", "evidence:flow"],
        "decisionSummary": "가격과 수급 회복을 확인했습니다.",
        "currentActionPlan": "허용 범위 안에서 추가매수를 분할로 검토합니다.",
        "changeAnalysis": "가격 회복과 외국인 순매수가 함께 확인됐습니다.",
        "nextActionPlan": "다음 정규장에서 거래량과 외국인 수급을 다시 확인합니다.",
        "invalidationCondition": "현재가가 20일선 아래로 내려가면 추가매수 판단을 취소합니다.",
        "causalChain": [{
            "status": "supported",
            "evidenceIds": ["evidence:price", "evidence:flow"],
        }],
        "decidedAt": "2026-08-16T00:00:00Z",
        "status": "active",
        "source": "notification-ai",
        "hypothesisSet": {"hypotheses": [{
            "hypothesisId": "hypothesis:recovery",
            "templateId": "template:recovery",
            "claim": "회복이 이어질 수 있다.",
            "stance": "support",
            "candidateAction": "ADD",
            "evidenceState": "supported",
            "supportingRuleIds": ["graph.recovery.v1"],
            "supportingEvidenceIds": ["evidence:price", "evidence:flow"],
            "causalPathIds": ["trace:recovery"],
            "invalidationConditions": ["20일선 아래로 하락"],
            "verificationStatus": "verified-current-generation",
            "approvalStatus": "approved-active",
            "status": "active",
            "scopeState": "market-shared",
            "marketHypothesisId": "market-hypothesis:recovery",
            "inferenceGenerationId": "generation:1",
            "knowledgeBasis": {
                "requiresHypothesis": True,
                "decisionEligibility": "investment-evidence",
            },
            "claimContract": {
                "claimContractId": "claim:recovery",
                "claimType": "market-hypothesis",
                "ruleId": "graph.recovery.v1",
            },
            "qualification": {"status": "active"},
        }]},
        "followUpConditions": [{
            "conditionId": "follow-up:1",
            "field": "currentPrice",
            "operator": ">=",
            "threshold": 80000,
            "purpose": "strengthen",
            "label": "8만원 회복",
            "status": "satisfied",
            "previousMatched": False,
            "currentMatched": True,
            "transitionVerified": True,
            "transitionKind": "false-to-true",
            "transitionAt": "2026-08-16T00:55:00Z",
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
    row["decisionActionability"] = investment_decision_actionability(row, row)
    return row


class DecisionContinuityTests(unittest.TestCase):
    def test_excluded_observation_targets_are_not_presented_as_waiting(self):
        previous = prior_episode()
        previous["outcomes"] = []
        episodes = EpisodeStore(previous)
        episodes.decision_outcome_schedule = lambda **kwargs: {
            "readStatus": "available", "targetCount": 2,
            "states": {"excluded": 2}, "nextTargetAt": "",
        }
        packet = DecisionContinuityService(episodes).build(
            account_id="main", symbol="005930", captured_at="2026-09-14T00:00:00Z",
        )
        self.assertEqual("excluded", packet["reviewSummary"]["state"])
        self.assertIn("제외", packet["reviewSummary"]["scheduleExplanation"])
        self.assertEqual(2, packet["outcomeSchedule"]["targetCount"])

    def test_missing_schedule_and_unavailable_reader_do_not_claim_pending(self):
        for schedule, expected in ((
            {"readStatus": "available", "targetCount": 0, "states": {}}, "not-scheduled",
        ), ({"readStatus": "error"}, "unavailable"), (
            {"readStatus": "unavailable"}, "unavailable",
        ), ({"readStatus": "available", "targetCount": 1, "states": {"needs-data": 1}}, "data-gap"), (
            {"readStatus": "available", "targetCount": 2, "states": {"excluded": 1, "pending": 1}}, "partial",
        ), ({"readStatus": "available", "targetCount": 1, "states": {"observed": 1}}, "unavailable"), (
            {"readStatus": "available", "targetCount": 1, "states": {"pending": 1},
             "nextTargetAt": "2026-09-15T00:00:00Z"}, "pending",
        )):
            with self.subTest(expected=expected):
                previous = prior_episode()
                previous["outcomes"] = []
                episodes = EpisodeStore(previous)
                episodes.decision_outcome_schedule = lambda **kwargs: schedule
                packet = DecisionContinuityService(episodes).build(account_id="main", symbol="005930")
                self.assertEqual(expected, packet["reviewSummary"]["state"])
        for eligibility, expected in (("eligible", "evaluated"), ("excluded-contract-data-gap", "data-gap")):
            with self.subTest(eligibility=eligibility):
                previous = prior_episode()
                previous["outcomes"][0]["calibrationEligibility"] = eligibility
                episodes = EpisodeStore(previous)
                episodes.decision_outcome_schedule = lambda **kwargs: {
                    "readStatus": "available", "targetCount": 1, "states": {"observed": 1},
                }
                packet = DecisionContinuityService(episodes).build(account_id="main", symbol="005930")
                self.assertEqual(expected, packet["reviewSummary"]["state"])
                self.assertIn("관측은 완료", packet["reviewSummary"]["scheduleExplanation"])
                self.assertNotIn("확인하지 못", packet["reviewSummary"]["scheduleExplanation"])

    def test_foreign_previous_decision_cannot_enter_continuity_or_ai_context(self):
        for field, value in (("accountId", "other"), ("symbol", "MSTR")):
            with self.subTest(field=field):
                foreign = prior_episode()
                foreign[field] = value
                store = EpisodeStore(foreign)
                domain = DomainStore()
                packet = DecisionContinuityService(store, domain).build(
                    account_id="main", symbol="005930", existing_previous=foreign,
                )
                self.assertFalse(packet["previousDecision"])
                self.assertFalse(packet["observedOutcomes"])
                self.assertEqual(0, domain.continuity_calls)
                context = context_with_previous_investment_decision({
                    "accountId": "main", "symbol": "005930",
                    "previousInvestmentDecisionEpisode": foreign,
                }, store)
                self.assertFalse(context.get("previousInvestmentDecisionEpisode"))

    def test_empty_captured_packet_removes_detached_previous_decision(self):
        packet = build_decision_continuity_packet(
            account_id="main", symbol="005930", captured_at="2026-09-14T00:00:00Z",
        )
        context = context_with_previous_investment_decision({
            "accountId": "main", "symbol": "005930", "decisionContinuityPacket": packet,
            "previousInvestmentDecisionEpisode": prior_episode(),
        })
        self.assertNotIn("previousInvestmentDecisionEpisode", context)

    def test_captured_action_and_packet_survive_without_reloading_or_reauthorizing_history(self):
        packet = DecisionContinuityService(EpisodeStore(prior_episode())).build(account_id="main", symbol="005930")
        original = deepcopy(packet)
        context = context_with_previous_investment_decision({
            "accountId": "main", "symbol": "005930", "decisionContinuityPacket": packet,
        })
        self.assertEqual("ADD", context["previousInvestmentDecisionEpisode"]["action"])
        self.assertEqual(original, packet)
        self.assertEqual(original["packetId"], context["decisionContinuityPacket"]["packetId"])
        self.assertEqual(original["previousDecision"], context["decisionContinuityPacket"]["previousDecision"])
        self.assertEqual(context, context_with_previous_investment_decision(context))

    def test_scope_and_current_episode_are_checked_on_index_and_hydration_reads(self):
        class IndexedStore:
            def latest_decision_memory(self, *_args, **_kwargs):
                return {**prior_episode(), "accountId": "other"}

        packet = DecisionContinuityService(IndexedStore()).build(account_id="main", symbol="005930")
        self.assertFalse(packet["previousDecision"])
        class HydrationStore:
            def get(self, _episode_id):
                return {**prior_episode(), "symbol": "MSTR"}
        packet = DecisionContinuityService(HydrationStore()).build(
            account_id="main", symbol="005930", existing_previous=prior_episode(),
        )
        self.assertFalse(packet["observedOutcomes"])
        self.assertFalse(packet["selectedHypothesis"])
        packet = DecisionContinuityService(EpisodeStore(prior_episode())).build(
            account_id="main", symbol="005930", exclude_episode_id="decision:previous",
        )
        self.assertFalse(packet["previousDecision"])

    def test_schedule_reader_failure_is_visible_without_losing_valid_previous_decision(self):
        class FailingStore(EpisodeStore):
            def decision_outcome_schedule(self, **_kwargs):
                raise TimeoutError("unavailable")
        previous = prior_episode()
        previous["outcomes"] = []
        packet = DecisionContinuityService(FailingStore(previous)).build(account_id="main", symbol="005930")
        self.assertTrue(packet["previousDecision"])
        self.assertEqual("unavailable", packet["reviewSummary"]["state"])
        self.assertIn("outcomeSchedule", packet["sourceErrors"])

    def test_foreign_insights_are_removed_even_when_readers_are_unavailable(self):
        for field, value in (("accountId", "other"), ("symbol", "MSTR"), ("accountId", "")):
            with self.subTest(field=field, value=value):
                previous = {"episodeId": "insight:previous", "accountId": "main", "symbol": "005930",
                            "insightAssessment": {"publishable": True, "direction": "positive"}, field: value}
                context = context_with_previous_investment_insight({
                    "accountId": "main", "symbol": "005930",
                    "previousInvestmentAIInsightEpisode": previous,
                    "previousDeliveredInvestmentAIInsightEpisode": previous,
                    "investmentInsightDeliveryHistory": {"status": "found", "accountId": "main", "symbol": "005930",
                                                         "previousEpisodeId": "insight:previous"},
                })
                self.assertNotIn("previousInvestmentAIInsightEpisode", context)
                self.assertNotIn("previousDeliveredInvestmentAIInsightEpisode", context)
                self.assertEqual("unavailable", context["investmentInsightDeliveryHistory"]["status"])

    def test_empty_captured_delivery_history_removes_loose_prior_receipt(self):
        context = context_with_previous_delivered_investment_insight({
            "accountId": "main", "symbol": "005930",
            "investmentInsightDeliveryHistory": {"status": "not-found", "accountId": "main", "symbol": "005930"},
            "previousDeliveredInvestmentAIInsightEpisode": {"episodeId": "old", "accountId": "main", "symbol": "005930",
                                                            "insightAssessment": {"publishable": True}},
        })
        self.assertNotIn("previousDeliveredInvestmentAIInsightEpisode", context)

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
        self.assertEqual("10", packet["historicalPosition"]["quantity"])
        self.assertEqual({}, packet["currentPosition"])
        self.assertTrue(packet["summary"]["actionPlanRecorded"])
        self.assertFalse(packet["summary"]["executionRecorded"])
        self.assertTrue(packet["summary"]["lifecycleReviewRecorded"])

    def test_frozen_current_values_are_not_replaced_by_ledger_valuation(self):
        previous = prior_episode()
        previous["hypothesisSet"]["hypotheses"][0]["claim"] = "다른 회사의 과거 설명"
        context = {"accountId": "main", "rawSymbol": "005930", "referenceDate": "2026-09-16T00:00:00Z",
                   "ontologyRelationContext": {"subject": {"symbol": "005930"}, "sourceAboxSnapshotId": "frozen:1",
                                               "facts": {"currentPrice": 127.92, "profitLossRate": -6.48, "quantity": 10}}}
        domain = DomainStore()
        packet = context_with_previous_investment_decision(
            context, EpisodeStore(previous), DecisionContinuityService(EpisodeStore(previous), domain),
        )["decisionContinuityPacket"]
        self.assertEqual(127.92, packet["currentPosition"]["currentPrice"])
        self.assertEqual("frozen:1", packet["currentPosition"]["sourceAboxSnapshotId"])
        self.assertEqual("2026-08-16T01:00:00Z", packet["historicalPosition"]["observedAt"])
        self.assertNotIn("claim", packet["selectedHypothesis"])
        self.assertEqual("withheld-unverified-scope", packet["sourceStatus"]["hypothesisDescription"])

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

    def test_legacy_packet_cannot_reintroduce_old_values_as_current(self):
        legacy = {"contractVersion": "decision-continuity-packet-v2", "accountId": "main", "symbol": "005930",
                  "packetId": "legacy:1", "currentPosition": {"currentPrice": 999}}
        context = context_with_previous_investment_decision({"accountId": "main", "rawSymbol": "005930",
                                                            "decisionContinuityPacket": legacy})
        self.assertNotIn("decisionContinuityPacket", context)
        self.assertEqual(999, legacy["currentPosition"]["currentPrice"])

    def test_after_source_observations_do_not_leak_into_frozen_memory(self):
        packet = DecisionContinuityService(EpisodeStore(prior_episode()), DomainStore()).build(
            account_id="main", symbol="005930", captured_at="2026-08-16T00:30:00Z",
        )
        self.assertEqual({}, packet["historicalPosition"])
        self.assertEqual([], packet["observedOutcomes"])
        self.assertEqual([], packet["actionObservations"])
        self.assertEqual([], packet["followUpConditions"])
        self.assertEqual("withheld-after-source-cutoff", packet["sourceStatus"]["accountObservation"])

    def test_invalid_reference_clock_cannot_admit_historical_facts(self):
        packet = DecisionContinuityService(EpisodeStore(prior_episode()), DomainStore()).build(
            account_id="main", symbol="005930", captured_at="unknown-clock",
        )
        self.assertEqual({}, packet["previousDecision"])
        self.assertEqual({}, packet["historicalPosition"])

    def test_unbound_current_facts_are_not_presented_as_frozen(self):
        context = {"rawSymbol": "005930", "referenceDate": "2026-09-16T00:00:00Z",
                   "ontologyRelationContext": {"facts": {"currentPrice": 100}}}
        self.assertEqual({}, frozen_current_position(context, "005930"))

    def test_compacted_account_activity_cannot_supply_a_current_valuation(self):
        value = {"portfolioState": {"positions": [{"symbol": "005930", "quantity": 10,
                    "observedAt": "2026-09-15T00:00:00Z", "profitLossRate": 99, "marketValueKrw": 999999}]}}
        for include_rebalance in (False, True):
            compact = _compact_portfolio_lifecycle(value, "005930", include_rebalance)
            row = compact["portfolioState"]["subjectPositions"][0]
            self.assertEqual(10, row["quantity"])
            self.assertEqual("2026-09-15T00:00:00Z", row["observedAt"])
            self.assertNotIn("profitLossRate", row)
            self.assertNotIn("marketValueKrw", row)

    def test_previous_insight_memory_skips_current_and_unpublishable_episodes(self):
        class InsightStore:
            calls = []

            def latest_insight_episodes(self, account_id="", symbol="", limit=0):
                self.calls.append((account_id, symbol, limit))
                return [
                    {
                        "episodeId": "insight:current",
                        "accountId": account_id, "symbol": symbol,
                        "subjectCaseId": "subject:current",
                        "insight": {"insightAssessment": {
                            "publishable": True,
                            "direction": "positive",
                        }},
                    },
                    {
                        "episodeId": "insight:rejected",
                        "accountId": account_id, "symbol": symbol,
                        "subjectCaseId": "subject:rejected",
                        "insight": {"insightAssessment": {
                            "publishable": False,
                            "direction": "negative",
                        }},
                    },
                    {
                        "episodeId": "insight:previous",
                        "accountId": account_id, "symbol": symbol,
                        "subjectCaseId": "subject:previous",
                        "inferenceGenerationId": "generation:previous",
                        "createdAt": "2026-08-16T00:30:00Z",
                        "insight": {"insightAssessment": {
                            "publishable": True,
                            "status": "conditional",
                            "direction": "negative",
                            "directionLabel": "하락 위험 우세",
                            "horizon": "medium-term",
                            "conviction": "moderate",
                            "dominantThesis": "수익성 둔화 위험이 중기 관점을 지배합니다.",
                            "causalMechanism": "매출 둔화가 이익 추정치를 낮춥니다.",
                            "investmentImplication": "신규 노출 확대보다 위험 관찰이 우선입니다.",
                            "thesisKey": "earnings-deceleration",
                            "materialFingerprint": "fingerprint:previous",
                        }},
                    },
                ]

        store = InsightStore()
        enriched = context_with_previous_investment_insight(
            {
                "messageType": "investmentInsight",
                "accountId": "main",
                "rawSymbol": "005930",
                "investmentSubjectDecisionCaseId": "subject:current",
            },
            store,
        )

        self.assertEqual([("main", "005930", 8)], store.calls)
        self.assertEqual(
            "insight:previous",
            enriched["previousInvestmentAIInsightEpisode"]["episodeId"],
        )
        self.assertEqual(
            "negative",
            enriched["previousInvestmentAIInsightEpisode"]["insightAssessment"]["direction"],
        )
        self.assertEqual("found", enriched["investmentInsightHistory"]["status"])
        self.assertEqual(
            "fingerprint:previous",
            enriched["investmentInsightHistory"]["previousMaterialFingerprint"],
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
            "decision-continuity-packet-v3",
            queue.request.context["decisionContinuityPacket"]["contractVersion"],
        )
        self.assertEqual(
            AI_DECISION_CONTRACT_VERSION,
            queue.request.context["notificationAiDecisionContractVersion"],
        )
        self.assertEqual(
            AI_DECISION_PROMPT_VERSION,
            queue.request.context["notificationAiReplayManifest"]["promptVersion"],
        )
        self.assertTrue(queue.request.context["notificationAiReplayManifest"]["modelVersion"])

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
                outcome_schedule={"readStatus": "available", "targetCount": 2, "states": {"excluded": 2}},
            ),
            "ontologyRelationContext": {
                "subject": {"symbol": "005930", "name": "삼성전자"},
                "facts": {"currentPrice": 81000},
            },
        }

        brief = notification_ai_decision_brief(context, {})
        prompt = build_notification_ai_decision_prompt(context, {}, decision_brief=brief)
        prompt_payload = json.loads(prompt.split("DecisionCore:\n", 1)[1])

        self.assertEqual("decision-continuity-packet-v3", brief["decisionContinuity"]["contractVersion"])
        self.assertEqual("ADD", prompt_payload["continuityDelta"]["previousDecision"]["action"])
        self.assertEqual("excluded", prompt_payload["continuityDelta"]["reviewSummary"]["state"])
        self.assertIn("평가 대상에서 제외", prompt_payload["continuityDelta"]["reviewSummary"]["scheduleExplanation"])
        self.assertIn("continuityDelta", prompt)

        settings = {
            "notificationAiReasoningEffort": "auto",
            "notificationAiStandardReasoningEffort": "high",
            "notificationAiDeepReasoningEffort": "max",
        }
        standard = notification_ai_execution_profile(
            {"ontologyRelationContext": {"reviewLevel": "check"}},
            settings,
        )
        deep = notification_ai_execution_profile(
            {
                "ontologyRelationContext": {"reviewLevel": "act"},
                "decisionTransition": {"kind": "action-changed"},
            },
            settings,
        )
        self.assertEqual("high", standard["reasoningEffort"])
        self.assertEqual("max", deep["reasoningEffort"])
        self.assertEqual(24 * 1024, standard["maxPromptBytes"])
        self.assertEqual(48 * 1024, deep["maxPromptBytes"])

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

        baseline, unsupported = normalize_follow_up_conditions(
            [{
                "field": "ma20Distance",
                "operator": "<=",
                "threshold": 0,
                "purpose": "weaken",
                "label": "20일선 아래로 하락",
            }],
            {
                "symbol": "000660",
                "ma20Distance": -1.0,
                "marketEvidenceProfile": {
                    "profileKey": "kr-equity",
                    "capabilities": {"pricePath": {"state": "fresh"}},
                },
                "updatedAt": "2026-08-31T05:00:00Z",
            },
            "000660",
        )
        self.assertFalse(unsupported)
        self.assertEqual("pending", baseline[0]["status"])
        self.assertFalse(baseline[0]["armed"])
        self.assertFalse(baseline[0]["transitionVerified"])

        unobserved, _ = normalize_follow_up_conditions(
            [{"field": "ma20Distance", "operator": "<=", "threshold": 0}],
            {
                "symbol": "000660",
                "marketEvidenceProfile": {
                    "capabilities": {"pricePath": {"state": "fresh"}},
                },
            },
            "000660",
        )
        first_true, unobserved_material = evaluate_follow_up_conditions(
            unobserved,
            {"ma20Distance": -0.1},
            "2026-08-31T05:01:00Z",
        )
        self.assertFalse(unobserved_material)
        self.assertEqual("pending", first_true[0]["status"])
        self.assertFalse(first_true[0]["armed"])

        tracked, _ = normalize_follow_up_conditions(
            [{
                "field": "ma20Distance",
                "operator": "<=",
                "threshold": 0,
                "purpose": "weaken",
                "label": "20일선 아래로 하락",
            }],
            {
                "symbol": "000660",
                "ma20Distance": 2.9,
                "marketEvidenceProfile": {
                    "profileKey": "kr-equity",
                    "capabilities": {"pricePath": {"state": "fresh"}},
                },
            },
            "000660",
        )

        updated, material = evaluate_follow_up_conditions(
            tracked,
            {"ma20Distance": 2.9},
            "2026-08-31T06:00:00Z",
        )

        self.assertFalse(material)
        self.assertEqual("pending", updated[0]["status"])
        self.assertTrue(updated[0]["armed"])
        self.assertFalse(updated[0]["currentMatched"])

        still_false, first_material = evaluate_follow_up_conditions(
            tracked,
            {"ma20Distance": 1.2},
            "2026-08-31T06:01:00Z",
        )
        transitioned, second_material = evaluate_follow_up_conditions(
            still_false,
            {"ma20Distance": -0.1},
            "2026-08-31T06:02:00Z",
        )

        self.assertFalse(first_material)
        self.assertTrue(second_material)
        self.assertEqual("satisfied", transitioned[0]["status"])
        self.assertTrue(transitioned[0]["transitionVerified"])
        self.assertEqual("false-to-true", transitioned[0]["transitionKind"])

        legacy_packet = build_decision_continuity_packet(
            account_id="main",
            symbol="000660",
            captured_at="2026-08-31T06:03:00Z",
            previous_decision=prior_episode(),
            follow_up_conditions=[{
                "conditionId": "follow-up:legacy",
                "field": "ma20Distance",
                "operator": "<=",
                "threshold": 0,
                "label": "20일선 아래로 하락",
                "status": "satisfied",
            }],
        )
        legacy_rows = decision_continuity_rows({"decisionContinuityPacket": legacy_packet})
        self.assertFalse(any("후속 조건" in item for item in legacy_rows))


if __name__ == "__main__":
    unittest.main()
