import unittest
from datetime import datetime, timezone

from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.application.notification_ai_gate_message import execution_telegram_message
from digital_twin.application.notification.rendering import NotificationRenderingService
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_explanation import build_notification_explanation_packet
from digital_twin.domain.notifications import NotificationJob


class NotificationExplanationTests(unittest.TestCase):
    def response(self):
        return NotificationAIValidatedResponse(
            action="HOLD",
            action_label="보유",
            summary="가격 회복은 확인됐지만 거래 확인이 부족해 보유합니다.",
            current_action_plan="지금은 주문하지 않고 보유합니다.",
            change_analysis="이전 매수 검토에서 보유로 바뀌었습니다.",
            evidence=[
                "현재가가 20일 평균 위에 있습니다.",
                "현재가가 20일 평균 위에 있습니다.",
                "20거래일 수익률이 양수입니다.",
                "가격 회복 신호가 다음 조회에도 유지됐습니다.",
                "네 번째 이후 근거는 간결 알림에서 제외됩니다.",
            ],
            counter_evidence=["거래량은 평균보다 적습니다.", "투자자 수급은 확인되지 않았습니다."],
            invalidation_condition="현재가가 20일 평균 아래로 내려가면 다시 판단합니다.",
            next_checks=["다음 정규장에서 거래량을 확인합니다."],
            missing_data_impact=["투자자 수급이 없어 가격 회복의 수요를 확인하지 못했습니다."],
            source="ai",
            reference_date="2026-08-14 10:00 KST",
        )

    def test_packet_deduplicates_and_bounds_customer_explanation(self):
        packet = build_notification_explanation_packet(
            detail_level="concise",
            action="보유",
            evidence=["같은 근거", "같은 근거", "다른 근거", "세 번째 근거", "네 번째 근거"],
            counter_evidence=["반대 1", "반대 2"],
            next_checks=["조건 1", "조건 2", "조건 3"],
            data_warnings=["한계 1", "한계 2"],
        )

        self.assertEqual(("같은 근거", "다른 근거", "세 번째 근거"), packet.evidence)
        self.assertEqual(("반대 1",), packet.counter_evidence)
        self.assertEqual(("조건 1", "조건 2"), packet.next_checks)
        self.assertEqual(("한계 1",), packet.data_warnings)

    def test_new_account_defaults_to_concise_notifications(self):
        account = AccountConfig("main", "메인", "toss", "", "", "", "", [])
        context = account.message_delivery_context()
        context.update({
            "title": "카카오 알림",
            "displayTarget": "카카오 / 035720",
            "rawLines": ["현재가: 39,400원", "수익률: +2.1%", "추세: 20일선보다 5.4% 높음"],
            "notifyLinkUrl": "https://example.test/?tab=notifications",
            "notificationDetailUrl": "https://example.test/?tab=notifications&detail=notification-job&detailKey=test-job",
            "ontologyRelationContext": {
                "matchedRules": [{
                    "ruleId": "graph.temporal.support.v1",
                    "label": "기간 회복이 다음 조회에도 유지됨",
                    "matched": True,
                    "referenceOnly": False,
                }],
                "actionEnvelope": {
                    "selectedRuleId": "graph.temporal.support.v1",
                    "dataReadiness": {"eligibleRuleIds": ["graph.temporal.support.v1"]},
                },
            },
        })

        message = execution_telegram_message(context, self.response())

        self.assertIn("<b>지금 행동</b>", message)
        self.assertIn("<b>핵심 근거</b>", message)
        self.assertIn("<b>TypeDB 핵심 추론</b>", message)
        self.assertIn("<b>다음 판단 조건</b>", message)
        self.assertIn("웹에서 전체 근거 보기", message)
        self.assertNotIn("<b>TypeDB 경쟁 추론</b>", message)
        self.assertNotIn("<b>회사 가치</b>", message)
        self.assertNotIn("<b>자료 상태</b>", message)
        self.assertNotIn("네 번째 이후 근거", message)
        self.assertEqual(1, message.count("현재가가 20일 평균 위에 있습니다."))

    def test_dispatch_rebuilds_legacy_investment_message_with_stable_contract(self):
        job = NotificationJob.create(
            "legacy message that must not be sent",
            account_id="main",
            message_type="investmentInsight",
            context={
                "messageType": "investmentInsight",
                "title": "NVIDIA 알림",
                "displayTarget": "NVIDIA / NVDA",
                "telegramMessage": "<b>관계 규칙</b>\nlegacy duplicate",
                "rawLines": ["현재가: 226.17", "수익률: 0%"],
                "ontologyRelationContext": {
                    "targetRole": "watchlist",
                    "subject": {"symbol": "NVDA", "name": "NVIDIA"},
                    "facts": {"currentPrice": 226.17, "currency": "USD", "market": "US"},
                    "matchedRules": [{"label": "기간 회복이 유지됨"}],
                },
            },
        )
        renderer = NotificationRenderingService(
            template_renderer=lambda target: target.context["telegramMessage"],
            now_provider=lambda: datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc),
        )

        message = renderer.render(job)

        self.assertIn("<b>지금 행동</b>", message)
        self.assertIn("<b>TypeDB 핵심 추론</b>", message)
        self.assertIn("$226.17", message)
        self.assertNotIn("수익률 0%", message)
        self.assertNotIn("<b>관계 규칙</b>", message)
        self.assertEqual(
            "investment-notification-presentation-v1",
            job.context["notificationPresentationAudit"]["version"],
        )
        self.assertEqual("typedb-evidence-narrative", job.context["notificationPresentationMode"])
        self.assertEqual("typedb", job.context["notificationWriterProvenance"]["writerKind"])
        self.assertNotIn("notificationAiValidatedResponse", job.context)

    def test_operational_message_keeps_operational_presentation_audit(self):
        job = NotificationJob.create(
            "운영 알림",
            account_id="main",
            message_type="workHandoff",
            context={},
        )
        renderer = NotificationRenderingService(
            now_provider=lambda: datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc),
        )

        renderer.render(job)

        self.assertEqual("notification-presentation-v2", job.context["notificationPresentationAudit"]["version"])
        self.assertEqual("full", job.context["notificationPresentationAudit"]["detailLevel"])

    def test_context_observation_is_presented_as_typedb_reference_not_ai_failure(self):
        rule = {
            "ruleId": "graph.context.observation.v1",
            "label": "가격 관계 변화 관찰",
            "knowledgeBasis": {
                "ruleKind": "context-observation",
                "decisionEligibility": "reference-only",
                "requiresHypothesis": False,
            },
        }
        job = NotificationJob.create(
            "legacy observation",
            account_id="main",
            message_type="investmentInsight",
            context={
                "messageType": "investmentInsight",
                "title": "NAVER",
                "displayTarget": "NAVER / 035420",
                "rawLines": ["현재가: 219500"],
                "ontologyRelationContext": {
                    "subject": {"symbol": "035420", "name": "NAVER", "market": "KR"},
                    "facts": {"currentPrice": 219500, "currency": "KRW", "market": "KR"},
                    "source": "typedbInferenceBox",
                    "graphStore": "typedb",
                    "graphStoreUsed": True,
                    "sourceAboxSnapshotId": "abox:1",
                    "inferenceGenerationId": "generation:1",
                    "generationAligned": True,
                    "activeRules": [rule],
                    "matchedRules": [rule],
                    "decision": {
                        "selectedRuleId": rule["ruleId"],
                        "basis": "typedbInferenceBox",
                    },
                    "graphStoreInference": {
                        "graphStore": "typedb",
                        "sourceAboxSnapshotId": "abox:1",
                        "inferenceGenerationId": "generation:1",
                        "relations": [rule],
                        "traces": [{"id": "trace:1", **rule}],
                    },
                },
            },
        )
        renderer = NotificationRenderingService(
            template_renderer=lambda target: target.context["telegramMessage"],
            now_provider=lambda: datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc),
        )

        message = renderer.render(job)

        self.assertIn("관계 변화 관찰", message)
        self.assertIn("[TypeDB 관찰]", message)
        self.assertIn("매수·매도 판단이 아닙니다", message)
        self.assertIn("TypeDB 참고 규칙: 가격 관계 변화 관찰", message)
        self.assertNotIn("AI 안전 보류", message)
        self.assertNotIn("AI 설명 비교", message)

    def test_concise_notification_keeps_foreign_and_institution_flow(self):
        context = {
            "messageDeliveryLevel": "absoluteBeginner",
            "notificationDetailLevel": "concise",
            "title": "LG전자 알림",
            "displayTarget": "LG전자 / 066570",
            "rawLines": [
                "현재가: 202,000원",
                "수익률: 0%",
                "추세: 5일선보다 2.0% 낮음, 20일선보다 11.9% 높음, 60일선보다 3.2% 낮음",
            ],
            "notificationDetailUrl": "https://evidence.test/?detail=notification-job&detailKey=test-job",
            "ontologyRelationContext": {
                "facts": {
                    "volume": 1170383,
                    "volumeRatio": 0.49,
                    "foreignBuyVolume": 202082,
                    "foreignSellVolume": 137364,
                    "foreignNetVolume": 64718,
                    "institutionBuyVolume": 117215,
                    "institutionSellVolume": 187359,
                    "institutionNetVolume": -70144,
                    "marketSignalCoverage": {
                        "investor": {
                            "status": "available",
                            "judgementEvidenceUsable": True,
                            "measurementType": "daily-final",
                            "observedFields": [
                                "foreignBuyVolume",
                                "foreignSellVolume",
                                "foreignNetVolume",
                                "institutionBuyVolume",
                                "institutionSellVolume",
                                "institutionNetVolume",
                            ],
                        },
                    },
                },
                "matchedRules": [{"label": "기간 회복 시도 에피소드"}],
            },
        }

        message = execution_telegram_message(context, self.response())

        self.assertIn("투자자 수급", message)
        self.assertIn("외국인 순매수 64,718주", message)
        self.assertIn("기관 순매도 70,144주", message)
        self.assertIn("장 마감 확정", message)

    def test_evidence_link_is_hidden_without_exact_notification_detail_url(self):
        context = {
            "messageDeliveryLevel": "absoluteBeginner",
            "notificationDetailLevel": "concise",
            "displayTarget": "LG전자 / 066570",
            "notifyLinkUrl": "https://example.test/?tab=notifications",
        }

        message = execution_telegram_message(context, self.response())

        self.assertNotIn("웹에서 전체 근거 보기", message)

    def test_standard_adds_summary_layers_without_full_diagnostics(self):
        context = {
            "messageDeliveryLevel": "intermediate",
            "notificationDetailLevel": "standard",
            "title": "카카오 알림",
            "displayTarget": "카카오 / 035720",
            "rawLines": ["현재가: 39,400원", "수익률: +2.1%", "추세: 20일선보다 5.4% 높음"],
            "ontologyRelationContext": {
                "matchedRules": [{"label": "기간 회복 + 거래 확인 -> 보유 관찰"}],
            },
        }

        message = execution_telegram_message(context, self.response())

        self.assertIn("<b>현재 흐름</b>", message)
        self.assertIn("<b>TypeDB 핵심 추론</b>", message)
        self.assertNotIn("<b>온톨로지 판단 영역</b>", message)
        self.assertNotIn("<b>판단에서 제외한 정보</b>", message)

    def test_legacy_context_without_detail_setting_keeps_full_renderer(self):
        message = execution_telegram_message(
            {"messageDeliveryLevel": "beginner", "title": "카카오 알림"},
            self.response(),
        )

        self.assertIn("<b>TypeDB 경쟁 추론</b>", message)
        self.assertIn("<b>판단에서 제외한 정보</b>", message)

    def test_send_context_links_directly_to_exact_notification_detail(self):
        runner = NotificationQueueRunner(
            queue=None,
            account_repository=None,
            notifier_factory=lambda account: None,
            now_provider=lambda: datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc),
            link_base_resolver=lambda _configured: "https://evidence.trycloudflare.com/?share_token=test-viewer",
        )
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context={"notifyLinkUrl": "http://127.0.0.1:3000"},
        )

        runner.apply_send_time_context(job)

        detail_url = job.context["notificationDetailUrl"]
        self.assertTrue(detail_url.startswith("https://evidence.trycloudflare.com/"))
        self.assertIn("share_token=test-viewer", detail_url)
        self.assertIn("tab=notifications", detail_url)
        self.assertIn("detail=notification-job", detail_url)
        self.assertIn("detailKey=" + job.job_id, detail_url)


if __name__ == "__main__":
    unittest.main()
