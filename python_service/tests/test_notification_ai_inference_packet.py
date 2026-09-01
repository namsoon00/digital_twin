import json
import unittest

from digital_twin.application.notification_ai_judgement_service import (
    NotificationAIJudgementService,
)
from digital_twin.application.notification.workflow import NotificationAIValidatedGateEnricher
from digital_twin.domain.notification_ai_gate_validation import validated_response_from_payload
from digital_twin.domain.notification_ai_inference_packet import (
    build_notification_ai_inference_packet,
)
from digital_twin.domain.notification_ai_context_router import (
    fit_notification_ai_decision_core,
)
from digital_twin.domain.notification_narrative import (
    narrative_claim_evidence_contract,
)
from digital_twin.domain.notifications import NotificationJob


def investment_context():
    rule = {
        "ruleId": "graph.holding.guard.v1",
        "label": "추가매수 보류",
        "evidenceRole": "support",
        "appliedFactFields": ["currentPrice", "ma20Distance"],
        "knowledgeBasis": {
            "ruleKind": "decision-rule",
            "decisionEligibility": "decision-eligible",
        },
        "evidenceState": {
            "evidenceUsableForJudgement": True,
            "inferenceEligibilityStatus": "eligible",
        },
    }
    return {
        "messageType": "investmentInsight",
        "displayTarget": "NAVER / 035420",
        "rawSymbol": "035420",
        "referenceDate": "2026-08-21 10:56 KST",
        "notificationAiDecisionContractVersion": "notification-ai-decision-contract-v7",
        "ontologyRelationContext": {
            "subject": {"symbol": "035420", "name": "NAVER", "market": "KR"},
            "facts": {"currentPrice": 218000, "ma20Distance": 1.2},
            "source": "typedbInferenceBox",
            "sourceAboxSnapshotId": "abox:1",
            "inferenceGenerationId": "generation:1",
            "activeRules": [rule],
            "matchedRules": [rule],
            "actionEnvelope": {
                "allowedActions": ["HOLD"],
                "blockedActions": ["ADD"],
            },
            "decision": {"selectedRuleId": rule["ruleId"]},
        },
    }


def response_payload(view_id, support_id, next_id):
    return {
        "action": "HOLD",
        "investmentView": "현재 확인된 위험 관계 때문에 추가매수는 보류합니다.",
        "executionDecision": "현재 보유 상태를 유지합니다.",
        "narrativeClaims": [
            {
                "claimId": "claim:view",
                "section": "view",
                "text": "현재 확인된 관계를 기준으로 추가매수는 보류합니다.",
                "evidenceIds": [view_id],
            },
            {
                "claimId": "claim:support",
                "section": "support",
                "text": "추가매수 보류 규칙이 현재 판단 근거로 성립했습니다.",
                "evidenceIds": [support_id],
            },
            {
                "claimId": "claim:next",
                "section": "next-condition",
                "text": "다음 관측에서 같은 관계가 유지되는지 다시 확인합니다.",
                "evidenceIds": [next_id],
            },
        ],
        "referenceDate": "2026-08-21 10:56 KST",
    }


class NotificationAIInferencePacketTests(unittest.TestCase):
    def test_retry_budget_preserves_minimum_contract_for_large_live_shape(self):
        ledger = [
            {
                "evidenceId": "evidence:" + str(index),
                "role": "risk" if index % 2 else "support",
                "kind": "relation",
                "label": "실제 관계 근거 " + str(index),
                "source": "typedb",
                "ruleIds": ["rule:" + str(index % 4)],
                "hypothesisIds": [],
                "judgementEligible": True,
            }
            for index in range(32)
        ]
        core = {
            "schemaVersion": "investment-ai-decision-core-v1",
            "notificationIntent": "investment-judgement",
            "subject": {"symbol": "NVDA", "name": "NVIDIA", "market": "US"},
            "question": {
                "questionId": "question:1",
                "intent": "investment-decision",
                "horizon": "multi-horizon",
                "text": "현재 행동을 바꿀 만큼 중요한 변화가 있는가?",
            },
            "facts": {
                **{key: float(index) for index, key in enumerate((
                    "currentPrice", "averagePrice", "profitLossRate", "quantity",
                    "marketValue", "volume", "volumeRatio", "timeAdjustedVolumeRatio",
                    "ma5", "ma20", "ma60", "ma5Distance", "ma20Distance",
                    "ma60Distance", "priceChangeRate",
                ), 1)},
                "currency": "USD",
                "market": "US",
                "marketEvidenceProfile": {"capabilities": {str(index): "fresh" for index in range(20)}},
            },
            "decision": {
                "previousAction": "HOLD",
                "typeDbDecision": {"primaryAction": "HOLD", "judgementBlocked": False},
                "actionEnvelope": {
                    "status": "ACTIONABLE",
                    "executionAction": "HOLD",
                    "drivingRuleIds": ["rule:1", "rule:2"],
                    "targetRole": "holding",
                },
                "transition": {"kind": "initial", "currentAction": "HOLD", "summary": "기준 상태"},
                "readiness": {"status": "evaluated", "state": "sufficient", "evaluated": True},
            },
            "continuityDelta": {
                "status": "available",
                "previousDecision": {
                    "action": "HOLD",
                    "decisionReadiness": "conditional",
                    "summary": "이전 판단의 상세 설명 " * 300,
                },
                "followUpConditions": [
                    {"field": "ma20Distance", "operator": ">", "threshold": 0, "onSatisfied": "재검토 " * 20},
                    {"field": "volumeRatio", "operator": ">", "threshold": 1, "onSatisfied": "확인 " * 20},
                ],
            },
            "companyEvidence": {
                "symbol": "NVDA",
                "companyName": "NVIDIA",
                "profile": {"sector": "Technology", "industry": "Semiconductors"},
                "valuation": {"peRatio": 28, "forwardPE": 15, "trailingEPS": 8},
                "latestFinancials": {
                    "annual": [{"period": "2026", "revenue": 100, "netIncome": 50}],
                    "quarterly": [{"period": "2026Q1", "revenue": 30, "netIncome": 15}],
                },
                "coverage": {"dataState": "sufficient", "officialSource": True},
            },
            "hypothesisSet": {
                "hypothesisSetId": "hypothesis-set:1",
                "questionId": "question:1",
                "subjectSymbol": "NVDA",
                "comparisonRequired": False,
                "hypotheses": [],
            },
            "rules": [{"ruleId": "rule:" + str(index), "label": "규칙 " + str(index)} for index in range(8)],
            "evidenceLedger": ledger,
            "narrativeClaimContract": narrative_claim_evidence_contract(ledger),
            "dataLimits": ["자료 제한 " * 20],
            "routingAudit": {"version": "notification-ai-context-route-v2", "status": "routed"},
        }

        fitted = fit_notification_ai_decision_core(core, 6 * 1024 + 1)

        rendered_bytes = len(json.dumps(fitted, ensure_ascii=False, separators=(",", ":")).encode())
        self.assertLessEqual(rendered_bytes, 6 * 1024 + 1)
        self.assertEqual("minimum-decision-contract", fitted["routingAudit"]["status"])
        self.assertEqual("HOLD", fitted["decision"]["actionEnvelope"]["executionAction"])
        self.assertEqual(1.0, fitted["facts"]["currentPrice"])
        self.assertLessEqual(len(fitted["evidenceLedger"]), 3)

    def test_packet_is_stable_and_declares_section_evidence(self):
        first = build_notification_ai_inference_packet(investment_context(), {})
        second = build_notification_ai_inference_packet(investment_context(), {})

        self.assertEqual(first.packet_id, second.packet_id)
        self.assertEqual(first.prompt_hash, second.prompt_hash)
        self.assertEqual(first.evidence_fingerprint, second.evidence_fingerprint)
        contract = first.decision_core["narrativeClaimContract"]["allowedEvidenceIdsBySection"]
        self.assertIn("rule:graph.holding.guard.v1", contract["support"])
        self.assertNotIn("fact:currentPrice", contract["support"])
        self.assertIn("fact:currentPrice", contract["view"])
        self.assertEqual([], contract["change"])
        full_contract = first.decision_core["narrativeClaimContract"]
        self.assertIn("fact:currentPrice", full_contract["recommendedEvidenceIdsBySection"]["view"])
        self.assertEqual(
            ["rule:graph.holding.guard.v1", "fact:currentPrice", "fact:ma20Distance"],
            full_contract["evidenceBundlesByInference"]["rule:graph.holding.guard.v1"],
        )

    def test_change_claim_requires_snapshot_bound_decision_transition(self):
        context = investment_context()
        transition = {
            "kind": "action-changed",
            "changed": True,
            "material": True,
            "previousAction": "ADD",
            "currentAction": "HOLD",
        }
        context["ontologyRelationContext"]["decisionTransition"] = transition
        context["decisionTransition"] = transition

        packet = build_notification_ai_inference_packet(context, {})

        contract = packet.decision_core["narrativeClaimContract"]["allowedEvidenceIdsBySection"]
        self.assertEqual(["transition:decision"], contract["change"])
        transition = next(
            item for item in packet.decision_core["evidenceLedger"]
            if item["evidenceId"] == "transition:decision"
        )
        self.assertEqual("decision-history", transition["source"])

    def test_shared_service_validates_against_the_same_packet_ledger(self):
        class Reviewer:
            calls = 0

            def review(self, prepared):
                self.calls += 1
                core = prepared["_notificationAiPreparedDecisionCore"]
                support_id = core["narrativeClaimContract"]["allowedEvidenceIdsBySection"]["support"][0]
                payload = response_payload("fact:currentPrice", support_id, "fact:ma20Distance")
                return validated_response_from_payload(
                    prepared,
                    payload,
                    raw_response=json.dumps(payload, ensure_ascii=False),
                    source="test AI",
                )

        reviewer = Reviewer()
        outcome = NotificationAIJudgementService(reviewer, {}).judge(investment_context())

        self.assertTrue(outcome.publishable)
        self.assertEqual(1, reviewer.calls)
        self.assertEqual(3, outcome.response.verified_claim_count)
        self.assertEqual(0, outcome.response.rejected_claim_count)
        self.assertEqual(
            outcome.packet.evidence_fingerprint,
            outcome.response.claim_validation["evidenceFingerprint"],
        )
        self.assertEqual(
            set(outcome.packet.evidence_ids),
            {
                item["evidenceId"]
                for item in outcome.response.claim_validation["evidenceLedger"]
            },
        )

    def test_unknown_evidence_is_repaired_once_before_publication(self):
        class Reviewer:
            calls = 0

            def review(self, prepared):
                self.calls += 1
                core = prepared["_notificationAiPreparedDecisionCore"]
                support_id = core["narrativeClaimContract"]["allowedEvidenceIdsBySection"]["support"][0]
                view_id = "relation-evidence:not-in-packet" if self.calls == 1 else "fact:currentPrice"
                payload = response_payload(view_id, support_id, "fact:ma20Distance")
                return validated_response_from_payload(
                    prepared,
                    payload,
                    raw_response=json.dumps(payload, ensure_ascii=False),
                    source="test AI",
                )

        reviewer = Reviewer()
        outcome = NotificationAIJudgementService(reviewer, {}).judge(investment_context())

        self.assertTrue(outcome.publishable)
        self.assertTrue(outcome.repair_attempted)
        self.assertTrue(outcome.repair_succeeded)
        self.assertEqual(2, reviewer.calls)
        self.assertEqual(0, outcome.response.rejected_claim_count)
        self.assertIn("unknown-evidence-id", outcome.executed_prompt)

    def test_rule_only_view_uses_exact_observed_evidence_closure_without_second_ai_call(self):
        class Reviewer:
            calls = 0

            def review(self, prepared):
                self.calls += 1
                core = prepared["_notificationAiPreparedDecisionCore"]
                support_id = core["narrativeClaimContract"]["allowedEvidenceIdsBySection"]["support"][0]
                view_id = support_id
                payload = response_payload(view_id, support_id, "fact:ma20Distance")
                return validated_response_from_payload(
                    prepared,
                    payload,
                    raw_response=json.dumps(payload, ensure_ascii=False),
                    source="test AI",
                )

        reviewer = Reviewer()
        outcome = NotificationAIJudgementService(reviewer, {}).judge(investment_context())

        self.assertTrue(outcome.publishable)
        self.assertEqual(1, reviewer.calls)
        self.assertFalse(outcome.repair_attempted)
        view_claim = next(
            item for item in outcome.response.narrative_claims
            if item["section"] == "view"
        )
        self.assertIn("fact:currentPrice", view_claim["evidenceClosureAddedIds"])

    def test_unrepairable_ai_claims_fall_back_without_ai_writer_label(self):
        class Reviewer:
            calls = 0

            def review(self, prepared):
                self.calls += 1
                core = prepared["_notificationAiPreparedDecisionCore"]
                support_id = core["narrativeClaimContract"]["allowedEvidenceIdsBySection"]["support"][0]
                payload = response_payload(
                    "relation-evidence:not-in-packet",
                    support_id,
                    "fact:ma20Distance",
                )
                return validated_response_from_payload(
                    prepared,
                    payload,
                    raw_response=json.dumps(payload, ensure_ascii=False),
                    source="test AI",
                )

        reviewer = Reviewer()
        context = investment_context()
        job = NotificationJob.create(
            "packet fallback",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )

        NotificationAIValidatedGateEnricher(
            reviewer,
            {
                "notificationAiGateEnabled": "1",
                "notificationAiGateMessageTypes": "investmentInsight",
            },
        )(job)

        self.assertEqual(2, reviewer.calls)
        self.assertEqual(
            "TypeDB inference fallback",
            job.context["notificationAiValidatedResponse"]["source"],
        )
        self.assertEqual("typedb", job.context["notificationWriterProvenance"]["writerKind"])
        self.assertFalse(job.context["notificationWriterProvenance"]["aiAuthored"])


if __name__ == "__main__":
    unittest.main()
