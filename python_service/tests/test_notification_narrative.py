import unittest
from datetime import datetime, timezone

from digital_twin.application.notification.rendering import NotificationRenderingService
from digital_twin.application.ai_inference_queue_service import AIInferenceQueueRunner
from digital_twin.application.notification_ai_gate_message import _notification_selected_inference_rows
from digital_twin.domain.ai_inference_queue import AIInferenceRequest
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_narrative import (
    build_investment_narrative_brief,
    is_action_only_text,
    normalize_narrative_claims,
    response_writer_provenance,
)
from digital_twin.domain.notifications import NotificationJob


class NotificationNarrativeTests(unittest.TestCase):
    @staticmethod
    def context_observation_context():
        rule = {
            "ruleId": "graph.context.observation.v1",
            "label": "가격 관계 변화 관찰",
            "knowledgeBasis": {
                "ruleKind": "context-observation",
                "decisionEligibility": "reference-only",
                "requiresHypothesis": False,
            },
        }
        return {
            "messageType": "investmentInsight",
            "notificationDecisionMode": "typedb-context-observation",
            "displayTarget": "NAVER / 035420",
            "ontologyRelationContext": {
                "subject": {"symbol": "035420", "name": "NAVER", "market": "KR"},
                "facts": {"currentPrice": 218000, "market": "KR"},
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
                "generationAligned": True,
                "activeRules": [rule],
                "matchedRules": [rule],
                "decision": {"selectedRuleId": rule["ruleId"], "basis": "typedbInferenceBox"},
                "graphStoreInference": {
                    "graphStore": "typedb",
                    "sourceAboxSnapshotId": "abox:1",
                    "inferenceGenerationId": "generation:1",
                    "relations": [rule],
                    "traces": [{"id": "trace:1", **rule}],
                },
            },
        }

    def test_claim_validator_keeps_confirmed_support_and_rejects_missing_data_as_counter(self):
        context = {
            "_notificationAiPreparedDecisionCore": {
                "evidenceLedger": [
                    {
                        "evidenceId": "fact:currentPrice",
                        "role": "support",
                        "kind": "fact",
                        "label": "현재가",
                        "value": 218000,
                        "judgementEligible": True,
                    },
                    {
                        "evidenceId": "limit:peer-per",
                        "role": "limitation",
                        "kind": "data-limit",
                        "label": "피어 PER 표본",
                        "judgementEligible": False,
                    },
                ],
            },
        }
        payload = {
            "narrativeClaims": [
                {
                    "claimId": "claim:support",
                    "section": "support",
                    "text": "현재가는 218,000원입니다.",
                    "evidenceIds": ["fact:currentPrice"],
                },
                {
                    "claimId": "claim:bad-counter",
                    "section": "counter",
                    "text": "피어 PER 표본이 없어 반대 근거입니다.",
                    "evidenceIds": ["limit:peer-per"],
                },
            ],
        }

        claims, validation = normalize_narrative_claims(context, payload, writer_kind="ai")

        self.assertEqual(["claim:support"], [item["claimId"] for item in claims])
        self.assertEqual(1, validation["verifiedClaimCount"])
        self.assertEqual(1, validation["rejectedClaimCount"])
        rejected = next(item for item in validation["validations"] if item["status"] == "rejected")
        self.assertIn("limitation-used-as-counter", rejected["reasons"])

    def test_ai_without_evidence_linked_claims_downgrades_to_typedb_writer(self):
        context = {
            "messageType": "investmentInsight",
            "ontologyRelationContext": {
                "subject": {"symbol": "035420", "name": "NAVER", "market": "KR"},
                "facts": {"currentPrice": 218000},
                "decision": {"selectedRuleId": "graph.holding.guard.v1"},
                "matchedRules": [{
                    "ruleId": "graph.holding.guard.v1",
                    "label": "종목 성격 물타기 차단",
                    "referenceOnly": False,
                }],
            },
        }
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="보유",
            evidence=["추가매수 보류"],
            source="openai",
            raw_response='{"action":"HOLD"}',
        )

        brief = build_investment_narrative_brief(context, response)

        self.assertEqual("typedb", brief.writer_provenance["writerKind"])
        self.assertFalse(brief.writer_provenance["aiAuthored"])
        self.assertEqual("ai-narrative-claim-contract-missing", brief.writer_provenance["fallbackReason"])
        self.assertNotIn("추가매수 보류", [item["text"] for item in brief.claims])
        self.assertIn("종목 성격 물타기 차단", [item["text"] for item in brief.claims])

    def test_packet_bound_claim_validation_does_not_widen_its_evidence_ledger(self):
        ledger = [{
            "evidenceId": "fact:ma20Distance",
            "role": "support",
            "kind": "derived",
            "label": "20일 평균 괴리",
            "value": 4.3,
            "judgementEligible": True,
        }]
        claim = {
            "claimId": "claim:ma20",
            "section": "support",
            "text": "현재가는 20일 평균보다 4.3% 높습니다.",
            "evidenceIds": ["fact:ma20Distance"],
            "writerKind": "ai",
        }
        response = NotificationAIValidatedResponse(
            action="HOLD",
            narrative_claims=[claim],
            claim_validation={
                "status": "verified",
                "verifiedClaimCount": 1,
                "rejectedClaimCount": 0,
                "evidenceLedger": ledger,
                "inferencePacketId": "packet:1",
                "evidenceFingerprint": "evidence:1",
            },
            source="openai",
            raw_response='{"action":"HOLD"}',
        )
        context = {
            "messageType": "investmentInsight",
            "ontologyRelationContext": {
                "facts": {"currentPrice": 218000, "ma20Distance": 4.3},
                "matchedRules": [{"ruleId": "graph.extra.rule", "label": "추가 규칙"}],
            },
        }

        brief = build_investment_narrative_brief(context, response)

        self.assertEqual(["fact:ma20Distance"], [item["evidenceId"] for item in brief.evidence_ledger])
        self.assertEqual(1, brief.metrics["verifiedClaimCount"])

    def test_reference_only_evidence_cannot_become_action_support(self):
        context = {
            "_notificationAiPreparedDecisionCore": {
                "evidenceLedger": [{
                    "evidenceId": "external:news:1",
                    "role": "context",
                    "kind": "external-evidence",
                    "label": "추가 검증이 필요한 기사",
                    "judgementEligible": False,
                }],
            },
        }
        payload = {
            "narrativeClaims": [{
                "claimId": "claim:news-support",
                "section": "support",
                "text": "이 기사가 매수를 지지합니다.",
                "evidenceIds": ["external:news:1"],
            }],
        }

        claims, validation = normalize_narrative_claims(context, payload, writer_kind="ai")

        self.assertEqual([], claims)
        self.assertIn(
            "judgement-ineligible-evidence",
            validation["validations"][0]["reasons"],
        )

    def test_data_quality_and_guardrail_rules_do_not_become_action_support(self):
        context = {
            "messageType": "investmentInsight",
            "ontologyRelationContext": {
                "matchedRules": [
                    {
                        "sourceRuleId": "graph.temporal.coverage_gap.v1",
                        "label": "기간 히스토리 부족",
                        "evidenceRole": "risk",
                        "decisionStage": "DATA_CONFLICT",
                        "knowledgeBasis": {
                            "ruleKind": "data-quality-gate",
                            "decisionEligibility": "guardrail-only",
                        },
                    },
                    {
                        "sourceRuleId": "graph.instrument_profile.strategy_fit.support.v1",
                        "label": "종목 성격·투자 성향 적합",
                        "evidenceRole": "support",
                        "knowledgeBasis": {
                            "ruleKind": "policy-constraint",
                            "decisionEligibility": "guardrail-only",
                        },
                    },
                ],
            },
        }
        ledger = {
            item["evidenceId"]: item
            for item in build_investment_narrative_brief(
                context,
                NotificationAIValidatedResponse(source="TypeDB fallback"),
            ).evidence_ledger
        }

        self.assertEqual("limitation", ledger["rule:graph.temporal.coverage_gap.v1"]["role"])
        self.assertFalse(ledger["rule:graph.temporal.coverage_gap.v1"]["judgementEligible"])
        self.assertEqual("context", ledger["rule:graph.instrument_profile.strategy_fit.support.v1"]["role"])

        claims, validation = normalize_narrative_claims(
            {"_notificationAiPreparedDecisionCore": {"evidenceLedger": list(ledger.values())}},
            {
                "narrativeClaims": [{
                    "claimId": "claim:guardrail-support",
                    "section": "support",
                    "text": "종목 성격과 투자 성향이 매수를 지지합니다.",
                    "evidenceIds": ["rule:graph.instrument_profile.strategy_fit.support.v1"],
                }],
            },
            writer_kind="ai",
        )

        self.assertEqual([], claims)
        self.assertIn("support-role-mismatch", validation["validations"][0]["reasons"])

    def test_claim_cannot_introduce_a_number_absent_from_linked_evidence(self):
        context = {
            "_notificationAiPreparedDecisionCore": {
                "evidenceLedger": [{
                    "evidenceId": "fact:currentPrice",
                    "role": "support",
                    "kind": "fact",
                    "label": "현재가",
                    "value": 218000,
                    "judgementEligible": True,
                }],
            },
        }
        payload = {
            "narrativeClaims": [{
                "claimId": "claim:invented-target",
                "section": "support",
                "text": "현재가는 218,000원이고 목표가는 250,000원입니다.",
                "evidenceIds": ["fact:currentPrice"],
            }],
        }

        claims, validation = normalize_narrative_claims(context, payload, writer_kind="ai")

        self.assertEqual([], claims)
        self.assertIn("ungrounded-number", validation["validations"][0]["reasons"])

    def test_writer_provenance_separates_ai_from_typedb_fallback(self):
        ai = NotificationAIValidatedResponse(source="openai", raw_response="{}")
        fallback = NotificationAIValidatedResponse(source="TypeDB deterministic presentation")

        self.assertTrue(response_writer_provenance(ai)["aiAuthored"])
        self.assertEqual("typedb", response_writer_provenance(fallback)["writerKind"])
        self.assertFalse(response_writer_provenance(fallback)["aiAuthored"])

    def test_action_sentence_is_not_evidence(self):
        self.assertTrue(is_action_only_text("추가매수 보류"))
        self.assertTrue(is_action_only_text("보유 유지"))
        self.assertFalse(is_action_only_text("현재가는 20일 평균보다 4.3% 높습니다."))

    def test_rendered_ai_notification_uses_only_verified_claims_and_persists_audit(self):
        ledger = [{
            "evidenceId": "fact:ma20Distance",
            "role": "support",
            "kind": "derived",
            "label": "20일 평균 괴리",
            "value": 4.3,
            "judgementEligible": True,
        }]
        claim = {
            "claimId": "claim:ma20",
            "section": "support",
            "text": "현재가는 20일 평균보다 4.3% 높습니다.",
            "evidenceIds": ["fact:ma20Distance"],
            "writerKind": "ai",
        }
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="보유",
            summary="가격 회복 여부를 더 확인합니다.",
            investment_view="확인된 가격 관계를 기준으로 보유를 유지합니다.",
            evidence=[claim["text"]],
            narrative_claims=[claim],
            claim_validation={
                "status": "verified",
                "requestedClaimCount": 1,
                "verifiedClaimCount": 1,
                "rejectedClaimCount": 0,
                "validations": [{**claim, "status": "verified", "reasons": []}],
                "evidenceLedger": ledger,
            },
            source="openai",
            raw_response='{"action":"HOLD","narrativeClaims":[]}',
            reference_date="2026-08-21 11:00 KST",
        )
        job = NotificationJob.create(
            "legacy",
            account_id="main",
            message_type="investmentInsight",
            context={
                "messageType": "investmentInsight",
                "displayTarget": "NAVER / 035420",
                "notificationAiValidatedResponse": response.to_dict(),
                "ontologyRelationContext": {
                    "subject": {"symbol": "035420", "name": "NAVER", "market": "KR"},
                    "facts": {"currentPrice": 218000, "ma20Distance": 4.3},
                },
            },
        )
        renderer = NotificationRenderingService(
            template_renderer=lambda target: target.context["telegramMessage"],
            now_provider=lambda: datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc),
        )

        message = renderer.render(job)

        self.assertIn("[AI]", message)
        self.assertIn(claim["text"], message)
        self.assertNotIn("AI 의견", message)
        self.assertEqual("ai", job.context["notificationWriterProvenance"]["writerKind"])
        self.assertEqual(1, job.context["notificationClaimValidation"]["verifiedClaimCount"])
        self.assertTrue(job.context["notificationPresentationAudit"]["narrativeFingerprint"])

    def test_context_observation_ai_writes_narrative_without_reopening_action_decision(self):
        context = self.context_observation_context()
        job = NotificationJob.create(
            "observation",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )
        request = AIInferenceRequest.create(job, context)
        self.assertEqual("context-narrative", request.review_mode)

        class Queue:
            enriched = None

            def heartbeat(self, *_args, **_kwargs):
                return True

            def complete(self, _request, _worker_id, _result, enriched):
                self.enriched = enriched
                return True

        class Reviewer:
            last_prompt_bytes = 0

            def review(self, prepared):
                ledger = prepared["_notificationAiPreparedDecisionCore"]["evidenceLedger"]
                evidence_id = next(item["evidenceId"] for item in ledger if item["kind"] == "inference")
                claim = {
                    "claimId": "claim:observation",
                    "section": "view",
                    "text": "가격 관계 변화가 확인됐으며 매수·매도 행동은 바꾸지 않습니다.",
                    "evidenceIds": [evidence_id],
                    "writerKind": "ai",
                }
                return NotificationAIValidatedResponse(
                    action="HOLD",
                    action_label="관심 유지",
                    investment_view=claim["text"],
                    narrative_claims=[claim],
                    claim_validation={
                        "requestedClaimCount": 1,
                        "verifiedClaimCount": 1,
                        "rejectedClaimCount": 0,
                        "validations": [{**claim, "status": "verified", "reasons": []}],
                        "evidenceLedger": ledger,
                    },
                    source="openai",
                    raw_response='{"action":"HOLD"}',
                )

        class NoActionLifecycle:
            def validate_ai_result(self, *_args, **_kwargs):
                raise AssertionError("narrative-only AI must not validate a new action")

            def ai_completed(self, *_args, **_kwargs):
                raise AssertionError("narrative-only AI must not replace the TypeDB final decision")

        queue = Queue()
        runner = AIInferenceQueueRunner(
            queue,
            Reviewer(),
            settings={"notificationAiTypeDbFallbackEnabled": "1"},
            reasoning_orchestrator=NoActionLifecycle(),
            worker_id="test-narrative",
        )

        detail = runner.process_request(request)

        self.assertIn("completed", detail)
        self.assertIsNotNone(queue.enriched)
        writer = queue.enriched["notificationWriterProvenance"]
        self.assertTrue(writer["aiAuthored"])
        self.assertEqual("narrative-only", writer["writerRole"])
        self.assertEqual("typedb", writer["decisionOwner"])
        self.assertEqual(request.model, writer["model"])
        self.assertEqual(request.request_id, writer["requestId"])
        self.assertEqual("NO_ACTION", queue.enriched["notificationAiValidatedResponse"]["action"])
        publication = queue.enriched["notificationNarrativePublication"]
        self.assertEqual("context-narrative", publication["reviewMode"])
        self.assertEqual("typedb", publication["actionAuthority"])
        self.assertEqual(1, publication["aiClaimCount"])
        execution = queue.enriched["notificationAiExecutionAudit"]
        self.assertEqual(publication, execution["claimPublication"])
        self.assertEqual("narrative-adopted-action-not-applicable", execution["adoptionState"])

        persisted_publication = dict(publication)
        render_job = NotificationJob.create(
            "before-render",
            account_id="main",
            message_type="investmentInsight",
            context=queue.enriched,
        )
        renderer = NotificationRenderingService(
            template_renderer=lambda target: target.context["telegramMessage"],
        )
        renderer.render(render_job)
        self.assertEqual(persisted_publication, render_job.context["notificationNarrativePublication"])
        self.assertTrue(render_job.context["notificationWriterProvenance"]["aiAuthored"])

    def test_selected_inference_row_includes_observed_rule_values(self):
        context = {
            "ontologyRelationContext": {
                "decision": {"selectedRuleId": "graph.holding.loss.v1"},
                "actionEnvelope": {
                    "selectedRuleId": "graph.holding.loss.v1",
                    "dataReadiness": {"eligibleRuleIds": ["graph.holding.loss.v1"]},
                },
                "matchedRules": [{
                    "ruleId": "graph.holding.loss.v1",
                    "label": "손실 보유 추가매수 차단",
                }],
                "graphStoreInference": {
                    "traces": [{
                        "ruleId": "graph.holding.loss.v1",
                        "matchedConditions": [{
                            "conditionId": "holding-loss",
                            "field": "profitLossRate",
                            "operator": "<",
                            "observedValue": -8.86,
                            "ruleConditionShape": {"value": 0},
                        }],
                    }],
                },
            },
        }
        response = NotificationAIValidatedResponse(
            action="HOLD",
            precomputed_action="HOLD",
            source="TypeDB inference fallback",
        )

        rows = _notification_selected_inference_rows(context, response)

        self.assertIn("성립값: 보유 수익률 -8.86% < 0%", rows[0])


if __name__ == "__main__":
    unittest.main()
