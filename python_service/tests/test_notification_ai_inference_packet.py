import json
import unittest

from digital_twin.modules.decisions.application.notification_ai_judgement_service import NotificationAIJudgementService
from digital_twin.modules.decisions.public import NotificationAIValidatedGateEnricher
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
    normalize_narrative_claims,
    resolved_narrative_claim_evidence_contract,
)
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.narrative_numeric_grounding import ungrounded_narrative_numbers


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
                "text": "현재가가 20일선 아래로 내려가면 현재 관점을 다시 검토합니다.",
                "evidenceIds": [next_id],
            },
        ],
        "counterEvidenceStatus": "none-found",
        "invalidationCondition": "현재가가 20일선 아래로 내려가면 현재 관점을 다시 검토합니다.",
        "referenceDate": "2026-08-21 10:56 KST",
    }


class NotificationAIInferencePacketTests(unittest.TestCase):
    def test_display_rounding_and_korean_direction_preserve_numeric_grounding(self):
        rows = [
            {"evidenceId": "fact:ma20Distance", "label": "20일 평균 괴리", "value": -4.75781},
            {"evidenceId": "fact:priceChangeRate", "value": -0.4294},
            {"evidenceId": "fact:volumeRatio", "value": 0.8461},
        ]
        for text in [
            "20일 평균보다 4.76% 낮습니다.",
            "20일 평균을 4.76% 밑도는 가격입니다.",
            "가격 변화율은 -0.43%입니다.",
            "가격은 0.43% 하락했습니다.",
            "가격은 0.43% 내리고 있습니다.",
            "가격은 0.43% 내렸습니다.",
            "가격은 0.43% 떨어졌습니다.",
            "거래량은 평균의 0.85배입니다.",
        ]:
            with self.subTest(text=text):
                self.assertEqual([], ungrounded_narrative_numbers(text, rows))
        for text in [
            "20일 평균보다 4.76% 높습니다.",
            "가격은 -0.43% 상승했습니다.",
            "가격은 0.44% 하락했습니다.",
            "가격은 0.43% 올랐습니다.",
            "가격은 0.43% 오른 상태입니다.",
            "60일 평균보다 4.76% 낮습니다.",
            "새 진입 가격은 20원입니다.",
            "평균 대비 4.76%입니다.",
        ]:
            with self.subTest(text=text):
                self.assertTrue(ungrounded_narrative_numbers(text, rows))

    def test_numeric_grounding_uses_observed_values_not_provenance_digits(self):
        rows = [{
            "evidenceId": "fact:unrelated:999", "sourceAsOf": "2026-09-11",
            "value": {"ma20Distance": "2.761", "releaseId": "release:999", "priceChangeRate": -1.136456322557844},
        }]
        self.assertEqual([], ungrounded_narrative_numbers(
            "20일선보다 2.761% 높지만 일간 1.136% 하락했습니다.", rows,
        ))
        self.assertEqual([], ungrounded_narrative_numbers(
            "20일선보다 2.761% 높지만 일간 1.136% 내렸습니다.", rows,
        ))
        self.assertEqual([], ungrounded_narrative_numbers(
            "가격은 2.76% 올랐습니다.",
            [{"evidenceId": "fact:priceChangeRate", "value": 2.761}],
        ))
        for text in ("목표는 999원입니다.", "목표는 2026원입니다.", "2.7609% 높습니다."):
            with self.subTest(text=text):
                self.assertTrue(ungrounded_narrative_numbers(text, rows))
        self.assertEqual([], ungrounded_narrative_numbers(
            "기준금리가 3.5%로 내려가면 다시 확인합니다.",
            [{"evidenceId": "fact:krBaseRate", "value": 3.5}],
        ))

    def test_grounded_display_rounding_does_not_request_a_second_model_turn(self):
        context = investment_context()
        context["ontologyRelationContext"]["facts"]["ma20Distance"] = -4.75781

        class Reviewer:
            calls = 0

            def review(self, prepared):
                self.calls += 1
                core = prepared["_notificationAiPreparedDecisionCore"]
                support = core["narrativeClaimContract"]["allowedEvidenceIdsBySection"]["support"][0]
                payload = response_payload("fact:ma20Distance", support, "fact:ma20Distance")
                payload["narrativeClaims"][0]["text"] = "20일선보다 4.76% 낮아 추가매수는 보류합니다."
                return validated_response_from_payload(
                    prepared, payload, raw_response=json.dumps(payload, ensure_ascii=False), source="test AI",
                )

        reviewer = Reviewer()
        result = NotificationAIJudgementService(reviewer, {}).judge(context)
        self.assertTrue(result.publishable)
        self.assertFalse(result.repair_attempted)
        self.assertEqual(1, reviewer.calls)

    def _assert_research_compaction_preserves_each_rule_proof_path(self):
        rule_ids = [
            "graph.company.capital.research.rule:" + str(index) + ":" + ("r" * 36)
            for index in range(4)
        ]
        required_evidence_ids = [
            "relation-evidence:subject-generation:" + str(index) + ":" + ("e" * 36)
            for index in range(16)
        ]
        hypotheses = [
            {
                "hypothesisId": "hypothesis:research:" + str(index),
                "familyId": "family:research:" + str(index),
                "candidateAction": "ADD" if index == 1 else "HOLD",
                "claim": "현재 관측을 설명하는 검증 대기 가설 " + str(index),
                "supportingRuleIds": rule_ids[index:index + 2],
                "supportingEvidenceIds": required_evidence_ids[index * 3:index * 3 + 3],
                "counterEvidenceIds": required_evidence_ids[12:16],
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
            for index in range(4)
        ]
        hypotheses[-1]["supportingRuleIds"] = [rule_ids[-1]]
        ledger = [
            {
                "evidenceId": evidence_id,
                "role": "counter" if index >= 12 else "support",
                "kind": "ontology-assertion",
                "label": "현재 세대 가설 근거 " + str(index),
                "judgementEligible": False,
            }
            for index, evidence_id in enumerate(required_evidence_ids)
        ] + [
            {
                "evidenceId": "context:" + str(index),
                "role": "context",
                "kind": "decision-transition",
                "label": "현재 상태 " + str(index),
                "judgementEligible": True,
            }
            for index in range(8)
        ]
        ledger[0].update({
            "kind": "model-signal",
            "value": {
                "signalType": "price-trend-continuation-support",
                "strengthBand": "strong",
            },
            "source": "statistical-signal-pipeline",
            "relatedEvidenceIds": ["fact:currentPrice"],
        })
        ledger.append({
            "evidenceId": "fact:currentPrice",
            "role": "context",
            "kind": "fact",
            "label": "현재가",
            "value": 1775000,
            "judgementEligible": True,
        })
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
        self.assertEqual(4, len(fitted["hypothesisSet"]["hypotheses"]))
        self.assertEqual(rule_ids, proof["ruleIds"])
        self.assertEqual(4, proof["factCount"])
        self.assertEqual(4, proof["relationCount"])
        self.assertEqual(4, proof["traceCount"])
        self.assertTrue(proof["evidencePathAttested"])
        self.assertEqual(
            set(required_evidence_ids),
            {
                item["evidenceId"]
                for item in fitted["evidenceLedger"]
                if item["evidenceId"] in required_evidence_ids
            },
        )
        claim_contract = fitted["narrativeClaimContract"]
        self.assertEqual("role-indexed-v1", claim_contract["encoding"])
        self.assertEqual(
            ["fact:currentPrice", "fact:volumeRatio"],
            claim_contract["preferredObservedEvidenceIds"][:2],
        )
        expanded_contract = resolved_narrative_claim_evidence_contract(
            claim_contract,
            fitted["evidenceLedger"],
        )
        allowed_sections = expanded_contract["allowedEvidenceIdsBySection"]
        recommended_sections = expanded_contract["recommendedEvidenceIdsBySection"]
        for section in ("view", "mechanism", "implication", "catalyst"):
            self.assertTrue(allowed_sections[section])
            self.assertTrue(recommended_sections[section])
            self.assertLessEqual(len(recommended_sections[section]), 4)
        ledger_by_id = {
            item["evidenceId"]: item for item in fitted["evidenceLedger"]
        }
        self.assertEqual(1775000, ledger_by_id["fact:currentPrice"]["value"])
        self.assertEqual(0.84, ledger_by_id["fact:volumeRatio"]["value"])
        self.assertEqual(
            "strong",
            ledger_by_id[required_evidence_ids[0]]["value"]["strengthBand"],
        )
        self.assertEqual(
            ["fact:currentPrice"],
            ledger_by_id[required_evidence_ids[0]]["relatedEvidenceIds"],
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
            "schemaVersion": "investment-ai-decision-core-v5",
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
            "routingAudit": {"version": "notification-ai-context-route-v6"},
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
        self.assertTrue(
            set(evidence_ids).issubset({
                item["evidenceId"] for item in fitted["evidenceLedger"]
            })
        )

    def test_packet_is_stable_and_declares_section_evidence(self):
        first = build_notification_ai_inference_packet(investment_context(), {})
        second = build_notification_ai_inference_packet(investment_context(), {})

        self.assertEqual(first.packet_id, second.packet_id)
        self.assertEqual(first.prompt_hash, second.prompt_hash)
        self.assertEqual(first.evidence_fingerprint, second.evidence_fingerprint)
        self.assertNotIn("응답 스키마:", first.prompt)
        self.assertEqual(
            first.prompt_bytes,
            first.prompt_budget["renderedPromptBytes"],
        )
        self.assertLessEqual(first.prompt_bytes, first.prompt_budget["maxPromptBytes"])
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
            profiles = []
            timeouts = []

            def review(self, prepared):
                self.calls += 1
                self.profiles.append(dict(prepared.get("notificationAiExecutionProfile") or {}))
                self.timeouts.append(prepared.get("_notificationAiTimeoutSecondsOverride"))
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
        outcome = NotificationAIJudgementService(
            reviewer,
            {},
            repair_reasoning_effort="low",
        ).judge(
            investment_context(),
            timeout_seconds=180,
            profile={"name": "deepResearch", "reasoningEffort": "max"},
        )

        self.assertTrue(outcome.publishable)
        self.assertTrue(outcome.repair_attempted)
        self.assertTrue(outcome.repair_succeeded)
        self.assertEqual(2, reviewer.calls)
        self.assertEqual("max", reviewer.profiles[0]["reasoningEffort"])
        self.assertEqual("max", reviewer.profiles[1]["reasoningEffort"])
        self.assertEqual([180, 180], reviewer.timeouts)
        self.assertEqual("max", outcome.execution_spans["repairReasoningEffort"])
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
        self.assertEqual("repaired", structured["attempts"][0]["status"])

        class AlternateNextReviewer:
            calls = 0

            def review(self, prepared):
                self.calls += 1
                core = prepared["_notificationAiPreparedDecisionCore"]
                support_id = core["narrativeClaimContract"]["allowedEvidenceIdsBySection"]["support"][0]
                payload = response_payload("fact:currentPrice", support_id, "fact:ma20Distance")
                payload["narrativeClaims"] = payload["narrativeClaims"][:2]
                payload["nextActionPlan"] = (
                    "현재가가 999999원에 도달하는지 다음 관측에서 확인합니다."
                )
                payload["invalidationCondition"] = (
                    "가격 흐름이 약해지면 현재 관점을 다시 검토합니다."
                )
                return validated_response_from_payload(
                    prepared,
                    payload,
                    raw_response=json.dumps(payload, ensure_ascii=False),
                    source="test AI",
                )

        alternate_next_reviewer = AlternateNextReviewer()
        alternate_outcome = NotificationAIJudgementService(
            alternate_next_reviewer,
            {},
        ).judge(investment_context())

        self.assertTrue(alternate_outcome.publishable)
        self.assertEqual(1, alternate_next_reviewer.calls)
        self.assertFalse(alternate_outcome.repair_attempted)
        alternate_repair = alternate_outcome.execution_spans["structuredNarrativeRepair"]
        self.assertEqual("repaired", alternate_repair["status"])
        self.assertEqual("invalidationCondition", alternate_repair["sourceField"])
        self.assertEqual(2, len(alternate_repair["attempts"]))
        self.assertEqual("rejected", alternate_repair["attempts"][0]["status"])
        self.assertIn("ungrounded-number", alternate_repair["attempts"][0]["reasons"])
        self.assertEqual("repaired", alternate_repair["attempts"][1]["status"])

    def test_research_insight_survives_execution_block_and_structured_claim_omission(self):
        context = investment_context()
        rule_id = "graph.holding.guard.v1"
        hypothesis_id = "hypothesis:holding-guard"
        context["notificationAiReviewMode"] = "context-narrative"
        context["ontologyRelationContext"]["hypothesisSet"] = {
            "comparisonMode": "research-only",
            "referenceHypothesisIds": [hypothesis_id],
            "minimumComparisonCount": 1,
            "hypotheses": [{
                "hypothesisId": hypothesis_id,
                "familyId": "holding-guard",
                "templateId": "hypothesis-template:" + rule_id,
                "claim": "단기 회복이 아직 실행 기준을 충족하지 못했습니다.",
                "stance": "risk",
                "candidateAction": "HOLD",
                "horizon": "short-term",
                "supportingRuleIds": [rule_id],
                "qualification": {
                    "status": "research-reviewed",
                    "decisionUse": "research",
                },
            }],
        }

        class Reviewer:
            calls = 0

            def review(self, prepared):
                self.calls += 1
                payload = {
                    "action": "NO_ACTION",
                    "summary": "단기 하방 위험이 회복 가능성보다 우세합니다.",
                    "currentActionPlan": "기존 보유만 유지하고 추가 노출은 늘리지 않습니다.",
                    "nextActionPlan": "현재가와 20일선 관계를 다시 확인합니다.",
                    "hypotheses": [{
                        "hypothesisId": hypothesis_id,
                        "templateId": "hypothesis-template:" + rule_id,
                        "claim": "단기 회복이 아직 실행 기준을 충족하지 못했습니다.",
                        "stance": "risk",
                        "evidenceReviewStatus": "all-input-evidence-reviewed",
                        "verdict": "supported",
                        "reasoning": "현재 가격과 20일선 관계가 위험 가설을 지지합니다.",
                    }],
                    "selectedHypothesisId": hypothesis_id,
                    "decisionReadiness": "conditional",
                    "counterEvidenceStatus": "none-found",
                    "insightAssessment": {
                        "direction": "negative",
                        "horizon": "short-term",
                        "conviction": "moderate",
                        "dominantThesis": "단기 하방 위험이 회복 가능성보다 우세합니다.",
                        "causalMechanism": "가격 회복 제한이 단기 수급의 추세 전환을 늦춥니다.",
                        "investmentImplication": "보유자는 회복 확인 전 추가 노출을 늘리지 않는 편이 유리합니다.",
                        "catalysts": ["20일선 회복이 관점을 바꿀 촉매입니다."],
                        "risks": ["약한 흐름이 이어질 수 있습니다."],
                        "invalidationCondition": "현재가가 20일선 위에서 유지되면 하방 관점을 무효화합니다.",
                        "thesisKey": "holding-guard",
                    },
                    # Deliberately omit mechanism/implication claim rows. The
                    # service may only reuse the model's structured text.
                    "narrativeClaims": [{
                        "claimId": "claim:view",
                        "section": "view",
                        "text": "단기 하방 위험이 회복 가능성보다 우세합니다.",
                        "evidenceIds": ["fact:currentPrice", "fact:ma20Distance"],
                    }, {
                        "claimId": "claim:next",
                        "section": "next-condition",
                        "text": "현재가가 20일선 위에서 유지되면 하방 관점을 무효화합니다.",
                        "evidenceIds": ["fact:currentPrice", "fact:ma20Distance"],
                    }],
                    "invalidationCondition": "현재가가 20일선 위에서 유지되면 하방 관점을 무효화합니다.",
                }
                return validated_response_from_payload(
                    prepared,
                    payload,
                    raw_response=json.dumps(payload, ensure_ascii=False),
                    source="test AI",
                )

        reviewer = Reviewer()
        outcome = NotificationAIJudgementService(reviewer, {}).judge(context)

        self.assertTrue(outcome.publishable)
        self.assertEqual(1, reviewer.calls)
        self.assertFalse(outcome.repair_attempted)
        self.assertIn("mechanism", outcome.response.verified_claim_sections)
        self.assertIn("implication", outcome.response.verified_claim_sections)
        self.assertTrue(outcome.response.insight_assessment["publishable"])
        self.assertFalse(outcome.response.insight_assessment["executionEligible"])
        self.assertEqual(
            "repaired",
            outcome.execution_spans["structuredInsightRepair"]["status"],
        )

        class MissingViewClaimReviewer:
            calls = 0

            def review(self, prepared):
                self.calls += 1
                payload = outcome.response.to_dict()
                payload["narrativeClaims"] = [
                    item for item in payload.get("narrativeClaims") or []
                    if item.get("section") != "view"
                ]
                assessment = dict(payload.get("insightAssessment") or {})
                assessment["dominantThesis"] = ""
                payload["insightAssessment"] = assessment
                return validated_response_from_payload(
                    prepared,
                    payload,
                    raw_response=json.dumps(payload, ensure_ascii=False),
                    source="test AI",
                )

        missing_view_reviewer = MissingViewClaimReviewer()
        missing_view_outcome = NotificationAIJudgementService(
            missing_view_reviewer,
            {},
        ).judge(context)

        self.assertTrue(missing_view_outcome.publishable)
        self.assertEqual(1, missing_view_reviewer.calls)
        self.assertFalse(missing_view_outcome.repair_attempted)
        repaired_view = next(
            item for item in missing_view_outcome.response.narrative_claims
            if item["section"] == "view"
        )
        verified_implication = next(
            item for item in missing_view_outcome.response.narrative_claims
            if item["section"] == "implication"
        )
        self.assertEqual(verified_implication["text"], repaired_view["text"])
        self.assertEqual(
            "verified-implication-claim",
            missing_view_outcome.execution_spans[
                "structuredInsightRepair"
            ]["sourceSections"]["view"],
        )
        counter_ledger = [{
            "evidenceId": "assertion:risk",
            "role": "counter",
            "kind": "model-signal",
            "label": "검증된 반대 모델 신호",
            "judgementEligible": True,
        }]
        counter_context = {
            "messageType": "investmentInsight",
            "contextObservationDecision": {
                "decisionMode": "typedb-context-observation",
                "requiresAiNarrative": True,
            },
            "_notificationAiPreparedDecisionCore": {
                "evidenceLedger": counter_ledger,
                "narrativeClaimContract": narrative_claim_evidence_contract(counter_ledger),
            },
        }
        counter_claims, counter_validation = normalize_narrative_claims(
            counter_context,
            {"narrativeClaims": [{
                "claimId": "claim:research-risk",
                "section": "counter",
                "text": "반대 모델 신호가 이어지면 현재 관점이 약해집니다.",
                "evidenceIds": ["assertion:risk"],
            }]},
            writer_kind="ai",
        )
        self.assertEqual(1, counter_validation["verifiedClaimCount"])
        self.assertEqual("counter", counter_claims[0]["section"])

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
