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
    apply_narrative_brief_to_response,
    build_investment_narrative_brief,
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
    def _assert_research_compaction_preserves_each_rule_proof_path(self):
        rule_ids = ["graph.research.rule:" + str(index) for index in range(4)]
        required_evidence_ids = ["relation-evidence:" + str(index) for index in range(12)]
        hypotheses = [
            {
                "hypothesisId": "hypothesis:research:" + str(index),
                "familyId": "family:research:" + str(index),
                "candidateAction": "ADD" if index == 1 else "HOLD",
                "claim": "현재 관측을 설명하는 검증 대기 가설 " + str(index),
                "supportingRuleIds": rule_ids[index:index + 2],
                "supportingEvidenceIds": required_evidence_ids[index * 3:index * 3 + 3],
                "counterEvidenceIds": required_evidence_ids[9:12],
                "evidenceState": "blocked",
                "claimContract": {
                    "claimType": "market-hypothesis",
                    "decisionAuthority": "conditional-investment-evidence",
                },
                "qualification": {
                    "status": "shadow",
                    "reason": "독립된 사후 결과가 부족합니다.",
                },
            }
            for index in range(3)
        ]
        hypotheses[-1]["supportingRuleIds"] = [rule_ids[-1]]
        ledger = [
            {
                "evidenceId": evidence_id,
                "role": "counter" if index >= 9 else "support",
                "kind": "ontology-assertion",
                "label": "현재 세대 가설 근거 " + str(index),
                "judgementEligible": False,
            }
            for index, evidence_id in enumerate(required_evidence_ids)
        ] + [
            {
                "evidenceId": "context:" + str(index),
                "role": "context",
                "kind": "fact",
                "label": "현재 상태 " + str(index),
                "judgementEligible": True,
            }
            for index in range(8)
        ]
        core = {
            "schemaVersion": "investment-ai-decision-core-v4",
            "reviewMode": "context-narrative",
            "notificationIntent": "review-observation",
            "subject": {"symbol": "000660", "name": "SK하이닉스", "market": "KR"},
            "facts": {"currentPrice": 1775000, "volumeRatio": 0.84},
            "decision": {
                "actionEnvelope": {
                    "executionAction": "NO_ACTION",
                    "executionDisposition": "hypothesis-research-only",
                },
            },
            "hypothesisSet": {
                "subjectSymbol": "000660",
                "inferenceGenerationId": "generation:research",
                "hypotheses": hypotheses,
            },
            "reasoningLineage": {
                "version": "subject-reasoning-lineage-v1",
                "status": "ok",
                "identity": {
                    "symbol": "000660",
                    "tboxReleaseId": "tbox:research",
                    "ruleboxReleaseId": "rulebox:research",
                    "sourceAboxSnapshotId": "abox:research",
                    "inferenceGenerationId": "generation:research",
                },
                "integrity": {"state": "warning"},
                "proof": {
                    "sourceAboxSnapshotId": "abox:research",
                    "inferenceGenerationId": "generation:research",
                    "facts": [
                        {
                            "id": "fact:" + str(index),
                            "conditionId": "condition:" + str(index),
                            "kind": "model-signal",
                            "observedValue": {"signal": "confirmed", "detail": "x" * 800},
                            "source": "statistical-signal-pipeline",
                            "ruleIds": [rule_id],
                        }
                        for index, rule_id in enumerate(rule_ids)
                    ],
                    "relations": [
                        {
                            "id": "relation:" + str(index),
                            "type": "HAS_MODEL_SIGNAL",
                            "ruleId": rule_id,
                            "traceId": "trace:" + str(index),
                        }
                        for index, rule_id in enumerate(rule_ids)
                    ],
                    "rules": [
                        {"id": rule_id, "decisionEligible": False}
                        for rule_id in rule_ids
                    ],
                    "traces": [
                        {
                            "id": "trace:" + str(index),
                            "ruleId": rule_id,
                            "matched": True,
                            "evidenceUsable": False,
                        }
                        for index, rule_id in enumerate(rule_ids)
                    ],
                },
            },
            "evidenceLedger": ledger,
            "narrativeClaimContract": narrative_claim_evidence_contract(ledger),
            "background": {"auditOnly": "원본 감사 자료 " * 5000},
        }

        fitted = fit_notification_ai_decision_core(core, 15_220)

        proof = fitted["reasoningLineage"]["proof"]
        self.assertEqual("minimum-research-review-contract", fitted["routingAudit"]["status"])
        self.assertEqual("context-narrative", fitted["reviewMode"])
        self.assertEqual(rule_ids, [item["id"] for item in proof["rules"]])
        self.assertEqual(4, len(proof["facts"]))
        self.assertEqual(4, len(proof["relations"]))
        self.assertEqual(4, len(proof["traces"]))
        self.assertEqual(
            set(required_evidence_ids),
            {
                item["evidenceId"]
                for item in fitted["evidenceLedger"]
                if item["evidenceId"] in required_evidence_ids
            },
        )
        self.assertLessEqual(
            len(json.dumps(fitted, ensure_ascii=False, separators=(",", ":")).encode()),
            15_220,
        )

    def _assert_compaction_preserves_every_hypothesis_evidence_identifier(self):
        evidence_ids = ["evidence:" + str(index) for index in range(18)]
        hypotheses = [
            {
                "hypothesisId": "hypothesis:" + str(index),
                "candidateAction": "ADD" if index == 0 else "HOLD",
                "claim": "검증 가능한 투자 가설 " + str(index),
                "supportingRuleIds": ["rule:" + str(index)],
                "supportingEvidenceIds": evidence_ids[index:index + 2],
                "counterEvidenceIds": evidence_ids[6:12] if index == 0 else [evidence_ids[12 + index]],
            }
            for index in range(4)
        ]
        ledger = [
            {
                "evidenceId": evidence_id,
                "role": "counter" if evidence_id in evidence_ids[6:12] else "support",
                "kind": "relation",
                "label": "가설 비교 근거 " + evidence_id,
                "source": "typedb",
                "judgementEligible": True,
            }
            for evidence_id in evidence_ids
        ]
        core = {
            "schemaVersion": "investment-ai-decision-core-v1",
            "notificationIntent": "investment-judgement",
            "subject": {"symbol": "000660", "name": "SK하이닉스", "market": "KR"},
            "facts": {"currentPrice": 1775000, "market": "KR", "currency": "KRW"},
            "decision": {
                "actionEnvelope": {
                    "status": "HYPOTHESIS_COMPARISON_REQUIRED",
                    "executionAction": "NO_ACTION",
                    "targetRole": "holding",
                },
            },
            "hypothesisSet": {
                "hypothesisSetId": "hypothesis-set:sk",
                "comparisonRequired": True,
                "hypotheses": hypotheses,
            },
            "rules": [
                {"ruleId": "rule:" + str(index), "label": "규칙 " + str(index)}
                for index in range(8)
            ],
            "evidenceLedger": ledger,
            "narrativeClaimContract": narrative_claim_evidence_contract(ledger),
            "background": {"auditOnly": "참고 자료 " * 6000},
            "routingAudit": {"version": "notification-ai-context-route-v2", "status": "routed"},
        }

        fitted = fit_notification_ai_decision_core(core, 12 * 1024)

        fitted_hypotheses = fitted["hypothesisSet"]["hypotheses"]
        by_id = {item["hypothesisId"]: item for item in fitted_hypotheses}
        for hypothesis in hypotheses:
            fitted_hypothesis = by_id[hypothesis["hypothesisId"]]
            self.assertEqual(
                hypothesis["supportingEvidenceIds"],
                fitted_hypothesis["supportingEvidenceIds"],
            )
            self.assertEqual(
                hypothesis["counterEvidenceIds"],
                fitted_hypothesis["counterEvidenceIds"],
            )
        required_ids = {
            evidence_id
            for hypothesis in hypotheses
            for key in ("supportingEvidenceIds", "counterEvidenceIds")
            for evidence_id in hypothesis[key]
        }
        retained_ids = {item["evidenceId"] for item in fitted["evidenceLedger"]}
        self.assertTrue(required_ids.issubset(retained_ids))

    def test_retry_budget_preserves_minimum_contract_for_large_live_shape(self):
        self._assert_research_compaction_preserves_each_rule_proof_path()
        self._assert_compaction_preserves_every_hypothesis_evidence_identifier()
        self._assert_live_nested_audit_detail_cannot_block_ai_before_model_execution()
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

    def _assert_live_nested_audit_detail_cannot_block_ai_before_model_execution(self):
        evidence_ids = ["ontology-assertion:" + str(index) for index in range(4)]
        oversized_detail = {
            "field" + str(index): "감사 원본 상세 " * 80
            for index in range(180)
        }
        hypotheses = [
            {
                "hypothesisId": "hypothesis:" + str(index),
                "templateId": "hypothesis-template:rule:" + str(index),
                "familyId": "hypothesis-family:" + str(index),
                "candidateAction": "HOLD",
                "claim": "현재 가격 경로가 위험 사건을 흡수하고 있다는 검증 가능한 가설",
                "supportingRuleIds": ["rule:" + str(index)],
                "supportingEvidenceIds": [evidence_ids[index]],
                "counterEvidenceIds": [evidence_ids[index + 2]],
                "invalidationConditions": ["다음 관측에서 가격 방어가 사라지면 무효화"],
                "claimContract": {
                    "claimContractId": "rule-claim:rule:" + str(index),
                    "claimType": "market-hypothesis",
                    "decisionAuthority": "conditional-investment-evidence",
                },
                "qualification": {
                    "status": "observed" if index == 0 else "shadow",
                    "decisionAuthority": "conditional-investment-evidence",
                    "decisiveOutcomeCount": 4 if index == 0 else 0,
                    "directionalHitRate": 0.25 if index == 0 else 0,
                    "averageActionAdjustedReturnPct": -0.1031 if index == 0 else 0,
                    "reason": "사후 결과를 현재 판단과 분리해 검토합니다.",
                },
            }
            for index in range(2)
        ]
        ledger = [
            {
                "evidenceId": evidence_id,
                "role": "support" if index < 2 else "counter",
                "kind": "ontology-assertion",
                "label": "현재 세대 근거 " + str(index),
                "value": oversized_detail,
                "featureSummary": oversized_detail,
                "source": "TypeDB",
                "sourceFactIds": ["source-fact:" + str(index)],
                "modelEvidenceIds": ["model-evidence:" + str(index)],
                "judgementEligible": True,
            }
            for index, evidence_id in enumerate(evidence_ids)
        ]
        core = {
            "schemaVersion": "investment-ai-decision-core-v4",
            "notificationIntent": "context-observation",
            "subject": {"symbol": "000660", "name": "SK하이닉스", "market": "KR"},
            "facts": {"currentPrice": 1776000, "market": "KR", "currency": "KRW"},
            "decision": {
                "actionEnvelope": {
                    "status": "HYPOTHESIS_QUALIFICATION_PENDING",
                    "allowedActions": ["HOLD"],
                    "blockedActions": ["ADD"],
                },
            },
            "reasoningTrigger": {
                "status": "verified-material-transition",
                "materialRevisionKeys": list(oversized_detail),
                "audit": oversized_detail,
            },
            "relationLifecycle": {
                "changeKind": "strengthened",
                "evidenceDelta": oversized_detail,
            },
            "hypothesisSet": {
                "hypothesisSetId": "hypothesis-set:000660",
                "inferenceGenerationId": "inference-generation:current",
                "hypotheses": hypotheses,
            },
            "rules": [
                {"ruleId": "rule:" + str(index), "label": "가설 규칙 " + str(index)}
                for index in range(2)
            ],
            "reasoningLineage": {
                "status": "complete",
                "judgementEligible": True,
                "identity": {
                    "symbol": "000660",
                    "sourceAboxSnapshotId": "abox-manifest:current",
                    "inferenceGenerationId": "inference-generation:current",
                    "selectedRuleId": "rule:0",
                },
                "integrity": {"state": "pass"},
                "proof": {
                    "sourceAboxSnapshotId": "abox-manifest:current",
                    "inferenceGenerationId": "inference-generation:current",
                    "rules": [{"id": "rule:0", "selected": True}],
                    "traces": [{"id": "trace:0", "ruleId": "rule:0"}],
                    "relations": [],
                    "facts": [{
                        "id": "source-fact:0",
                        "observedValue": oversized_detail,
                        "targetProperties": oversized_detail,
                        "ruleIds": ["rule:0"],
                        "traceIds": ["trace:0"],
                    }],
                },
            },
            "evidenceLedger": ledger,
            "narrativeClaimContract": narrative_claim_evidence_contract(ledger),
            "routingAudit": {"version": "notification-ai-context-route-v5"},
        }

        fitted = fit_notification_ai_decision_core(core, 15_884)

        rendered_bytes = len(json.dumps(
            fitted,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode())
        self.assertLessEqual(rendered_bytes, 15_884)
        fitted_hypotheses = fitted["hypothesisSet"]["hypotheses"]
        self.assertEqual(
            evidence_ids,
            sorted({
                evidence_id
                for item in fitted_hypotheses
                for key in ("supportingEvidenceIds", "counterEvidenceIds")
                for evidence_id in item[key]
            }),
        )
        self.assertEqual("observed", fitted_hypotheses[0]["qualification"]["status"])
        self.assertEqual(4, fitted_hypotheses[0]["qualification"]["decisiveOutcomeCount"])
        self.assertEqual(0.25, fitted_hypotheses[0]["qualification"]["directionalHitRate"])
        self.assertEqual(
            set(evidence_ids),
            {item["evidenceId"] for item in fitted["evidenceLedger"]},
        )

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

        context["reasoningDeliveryTrigger"] = {
            "version": "reasoning-delivery-trigger-v1",
            "status": "verified-material-transition",
            "material": True,
            "userObservable": True,
            "kinds": ["verified-market-observation-followup"],
            "reasons": ["verified-observation-followup"],
            "materialRevisionKeys": ["revision:naver:price:2"],
            "changedFields": ["bidAskImbalance"],
            "matchedConditions": ["orderbook-imbalance"],
            "facts": {
                "previousBidAskImbalance": 8.5,
                "bidAskImbalance": 24.2,
                "bidAskImbalanceThreshold": 20,
                "orderbookBidVolume": 1500,
                "orderbookAskVolume": 900,
                "confirmedSignalTransitions": [{
                    "signalId": "orderbook",
                    "condition": "orderbook-imbalance",
                    "fromState": "neutral",
                    "toState": "positive",
                    "observedValue": 24.2,
                    "confirmationCount": 2,
                    "requiredConfirmations": 2,
                }],
            },
            "observedAt": "2026-09-09T00:00:00Z",
        }
        context["relationLifecycleTransition"] = {
            "version": "relation-lifecycle-transition-v1",
            "material": True,
            "changeKind": "strengthened",
            "previousState": "observed",
            "currentState": "strengthened",
            "occurredAt": "2026-09-09T00:00:01Z",
            "evidenceDelta": {
                "addedSupportingEvidenceKeys": ["price-above-ma20"],
            },
        }

        enriched_packet = build_notification_ai_inference_packet(context, {})

        self.assertEqual(
            "verified-material-transition",
            enriched_packet.decision_core["reasoningTrigger"]["status"],
        )
        self.assertEqual(
            ["orderbook-imbalance"],
            enriched_packet.decision_core["reasoningTrigger"]["matchedConditions"],
        )
        self.assertEqual(
            20,
            enriched_packet.decision_core["reasoningTrigger"]["facts"]["bidAskImbalanceThreshold"],
        )
        self.assertEqual(
            2,
            enriched_packet.decision_core["reasoningTrigger"]["facts"]["confirmedSignalTransitions"][0]["confirmationCount"],
        )
        self.assertEqual(
            "strengthened",
            enriched_packet.decision_core["relationLifecycle"]["changeKind"],
        )
        change_ids = enriched_packet.decision_core[
            "narrativeClaimContract"
        ]["allowedEvidenceIdsBySection"]["change"]
        self.assertIn("transition:reasoning-trigger", change_ids)
        self.assertIn("transition:relation-lifecycle", change_ids)

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
                payload["nextActionPlan"] = (
                    "현재가가 20일선 아래로 내려가면 현재 설명을 다시 검토합니다."
                )
                payload["nextChecks"] = [
                    "다음 가격 갱신에서 현재가와 20일선을 비교합니다."
                ]
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
        brief = build_investment_narrative_brief(
            investment_context(),
            outcome.response,
        )
        apply_narrative_brief_to_response(brief, outcome.response)
        self.assertIn("20일선 아래", outcome.response.next_action_plan)
        self.assertIn("현재가와 20일선", outcome.response.next_checks[0])

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

        class MissingNextReviewer:
            calls = 0

            def review(self, prepared):
                self.calls += 1
                core = prepared["_notificationAiPreparedDecisionCore"]
                support_id = core["narrativeClaimContract"]["allowedEvidenceIdsBySection"]["support"][0]
                payload = response_payload("fact:currentPrice", support_id, "fact:ma20Distance")
                payload["narrativeClaims"] = payload["narrativeClaims"][:2]
                payload["nextActionPlan"] = (
                    "다음 관측에서 가격 흐름과 거래 조건이 유지되는지 확인합니다."
                )
                return validated_response_from_payload(
                    prepared,
                    payload,
                    raw_response=json.dumps(payload, ensure_ascii=False),
                    source="test AI",
                )

        missing_next_reviewer = MissingNextReviewer()
        repaired_outcome = NotificationAIJudgementService(
            missing_next_reviewer,
            {},
        ).judge(investment_context())

        self.assertTrue(repaired_outcome.publishable)
        self.assertEqual(1, missing_next_reviewer.calls)
        self.assertFalse(repaired_outcome.repair_attempted)
        self.assertIn(
            "next-condition",
            repaired_outcome.response.verified_claim_sections,
        )
        structured = repaired_outcome.execution_spans["structuredNarrativeRepair"]
        self.assertEqual("repaired", structured["status"])
        self.assertEqual("nextActionPlan", structured["sourceField"])
        self.assertTrue(structured["evidenceIds"])

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
