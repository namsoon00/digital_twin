import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
from digital_twin.modules.notifications.application.notification.intake import NotificationIngressService
from digital_twin.modules.notifications.application.notification.presentation import content_body, present_notification
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.notifications.application.notification.workflow import NotificationHoldingSnapshotEnricher, NotificationQueueRunner
from digital_twin.modules.notifications.application.typedb_observation_message import _flow_rows, _trigger_rows
from digital_twin.modules.read_models.domain.customer_investment_document import CustomerInvestmentDocument, CustomerInvestmentSection
from digital_twin.modules.notifications.domain.notification.presentation import LEGACY_KINDS, NOTIFICATION_KINDS, notification_kind
from digital_twin.modules.notifications.domain.notification.request import NotificationRequest
from digital_twin.modules.notifications.domain.notification_templates import NotificationTemplate, render_notification, text_context
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.infrastructure.notification.ingress import enqueue_request
from digital_twin.infrastructure.mysql_notification_jobs import (
    MySQLNotificationJobStore, notification_list_presentation_column, notification_list_presentation_join,
)


class NotificationPresentationBoundaryTests(unittest.TestCase):
    def test_every_legacy_kind_has_a_distinct_customer_identity(self):
        for legacy, expected in LEGACY_KINDS.items():
            with self.subTest(legacy=legacy):
                self.assertEqual(expected, notification_kind(legacy).key)
        self.assertEqual(11, len({kind.icon for kind in NOTIFICATION_KINDS.values()}))

    def test_reference_only_ai_is_not_an_investment_decision(self):
        for mode in ("typedb-context-observation", "typedb-review-observation", "context-narrative"):
            context = {
                "notificationDecisionMode": mode,
                "notificationWriterProvenance": {"aiAuthored": True, "writerRole": "narrative-only"},
                "notificationAiValidatedResponse": {"action": "HOLD"},
            }
            self.assertEqual("ai-interpretation", notification_kind("investmentInsight", context).key)

    def test_completed_upstream_decision_keeps_its_kind(self):
        self.assertEqual("investment-decision", notification_kind("investmentInsight", {
            "decisionPublication": {"outcomeKind": "FINAL_DECISION"},
            "notificationAiValidatedResponse": {"action": "ADD"},
        }).key)

    def test_body_alone_cannot_claim_validated_decision_or_ai_authorship(self):
        for requested in ("investment-decision", "ai-interpretation"):
            self.assertEqual("notice", notification_kind("notification", {
                "notificationContent": {"kind": requested, "body": "자료를 확인했습니다."},
            }).key)

    def test_unknown_optional_fields_survive_request_round_trip(self):
        request = NotificationRequest.from_dict({
            "accountId": "main", "kind": "future-kind", "body": "새 자료가 저장됐습니다.",
            "futureMetadata": {"a": [1, 2]}, "content": {"extra": {"b": 3}},
        })
        restored = NotificationRequest.from_dict(request.to_dict())
        job = NotificationIngressService.job_from_request(restored)
        self.assertEqual("notice", job.to_dict()["notificationKind"])
        self.assertEqual({"a": [1, 2]}, job.context["notificationRequestExtensions"]["futureMetadata"])
        self.assertEqual({"b": 3}, job.context["notificationContent"]["extra"])
        self.assertEqual(request.source_text, job.text)

    def test_body_sections_and_links_are_optional_and_long_urls_remain_whole(self):
        url = "https://example.com/news/" + "a" * 4500
        request = NotificationRequest.from_dict({
            "accountId": "main", "messageType": "newsDigest", "kind": "news",
            "subject": {"name": "삼성전자"},
            "content": {"body": "매출이 늘었습니다.", "links": [{"label": "원문", "url": url}]},
        })
        job = NotificationIngressService.job_from_request(request)
        rendered = NotificationRenderingService().render(job)
        self.assertIn("📰 뉴스·공시 · 삼성전자", rendered)
        self.assertIn(url, rendered)
        self.assertNotIn("AI 의견", rendered)

    def test_links_do_not_replace_top_level_free_text(self):
        request = NotificationRequest.from_dict({
            "accountId": "main", "kind": "news", "body": "기사의 실제 요약입니다.",
            "content": {"links": [{"url": "https://example.com/news"}]},
        })
        text = NotificationRenderingService().render(NotificationIngressService.job_from_request(request))
        self.assertIn(request.source_text, text)
        self.assertIn("https://example.com/news", text)

    def test_single_sentence_is_not_consumed_as_a_heading(self):
        text = "계좌 동기화가 복구됐습니다."
        job = NotificationIngressService().job_from_request(
            NotificationIngressService().request_from_text(text, account_id="main", message_type="monitorConnection")
        )
        rendered = NotificationRenderingService().render(job)
        self.assertIn(text, rendered)
        self.assertIn("⚙️ 운영 상태", rendered)

    def test_malformed_optional_sections_do_not_drop_free_body(self):
        for sections in (None, 3, {"a": 1}, [{"lines": 42}], [{"title": "빈 영역"}]):
            self.assertEqual("가격이 올랐습니다.", content_body({"body": "가격이 올랐습니다.", "sections": sections}))

    def test_template_error_uses_source_without_generating_a_judgement(self):
        job = NotificationJob.create("현재가가 3.8% 올랐습니다.", account_id="main", message_type="marketObservation")
        service = NotificationRenderingService(template_renderer=Mock(side_effect=ValueError("bad template")))
        message = service.render(job)
        self.assertIn("3.8%", message)
        self.assertEqual(["template-fallback:ValueError"], job.context["notificationPresentationWarnings"])
        self.assertNotIn("notificationAiValidatedResponse", job.context)

    def test_missing_upstream_decision_does_not_become_hold(self):
        job = NotificationJob.create("가격이 3.8% 올랐습니다.", message_type="investmentInsight")
        with patch("digital_twin.modules.decisions.domain.notification_ai_gate_validation.local_validated_ai_response", side_effect=AssertionError("must not infer")):
            message = NotificationRenderingService().render(job)
        self.assertIn("3.8%", message)
        self.assertNotIn("보유", message)
        self.assertNotIn("validatedDecisionResponse", job.context)
        self.assertNotIn("notificationInferenceResponse", job.context)

    def test_sparse_stored_document_is_delivered_without_required_sections(self):
        document = CustomerInvestmentDocument(
            role="ai-judgement", headline="old title", target="MSTR", lead="가격은 회복했지만 거래량 확인은 부족합니다.",
            sections=(),
        )
        context = {
            "displayTarget": "스트래티지 / MSTR",
            "customerInvestmentDocument": document.to_dict(),
            "decisionPublication": {"outcomeKind": "REVIEW_ONLY"},
            "notificationWriterProvenance": {"aiAuthored": True, "writerRole": "narrative-only"},
            "notificationAiValidatedResponse": {"action": "NO_ACTION"},
        }
        original_response = copy.deepcopy(context["notificationAiValidatedResponse"])
        job = NotificationJob.create("fallback", message_type="investmentInsight", context=context)
        rendered = NotificationRenderingService().render(job)
        self.assertIn("🧠 AI 해석 · 스트래티지", rendered)
        self.assertIn(document.lead, rendered)
        self.assertNotIn("종합 판단", rendered)
        self.assertEqual(original_response, job.context["notificationAiValidatedResponse"])

    def test_duplicate_bullets_removed_but_same_label_links_preserved(self):
        text = '• 같은 근거입니다.\n• 같은 근거입니다.\n• <a href="https://example.com/a">원문</a>\n• <a href="https://example.com/b">원문</a>'
        result = present_notification("newsDigest", {}, text)
        self.assertEqual(1, result.count("같은 근거입니다."))
        self.assertIn("https://example.com/a", result)
        self.assertIn("https://example.com/b", result)
        self.assertEqual(result, present_notification("newsDigest", {}, result))

    def test_replay_original_and_test_notice_are_preserved(self):
        body = "[재발송] 원본 알림 N-TEST\n원본 메시지"
        self.assertEqual(body, present_notification("investmentInsight", {"notificationReplayPreserveOriginal": True}, body))
        test = "🧪 테스트 알림 · 매매 신호 아님\n\n🔔 새 알림\n본문"
        self.assertTrue(present_notification("marketObservation", {}, test).startswith("🧪 테스트 알림"))

    def test_legacy_queue_identity_is_unchanged(self):
        job = NotificationJob.create("본문", account_id="main", message_type="investmentInsight",
                                     source_event_id="event1", dedupe_key="original:dedupe",
                                     context={"deliveryFingerprint": "original-fingerprint", "stateCooldownMinutes": 360})
        before = (job.job_id, job.account_id, job.message_type, job.source_event_id, job.dedupe_key)
        NotificationIngressService.prepare_job(job)
        self.assertEqual(before, (job.job_id, job.account_id, job.message_type, job.source_event_id, job.dedupe_key))
        self.assertEqual("original-fingerprint", job.context["deliveryFingerprint"])
        self.assertEqual(360, job.context["stateCooldownMinutes"])

    def test_queue_adapter_uses_the_request_port(self):
        queue = Mock()
        queue.enqueue_request.return_value = True
        request = NotificationRequest.from_dict({"body": "알림 본문", "accountId": "main"})
        self.assertTrue(enqueue_request(request, queue))
        queue.enqueue_request.assert_called_once_with(request)
        queue.enqueue.assert_not_called()

    def test_request_reaches_transport_and_done_without_an_ai_call(self):
        request = NotificationRequest.from_dict({
            "accountId": "main", "kind": "news", "subject": {"symbol": "MSTR", "name": "스트래티지"},
            "body": "공시 원문에서 신규 자금 조달 조건을 확인했습니다.",
        })
        self.assertEqual("newsDigest", request.message_type)
        job = NotificationIngressService.job_from_request(request)

        class Queue:
            def pending(self, limit=10):
                return [job] if job.status == "pending" else []

            def mark_processing(self, item):
                item.status = "processing"

            def mark_done(self, item):
                item.status = "done"

            def mark_failed(self, item, reason):
                item.status = "failed"
                item.last_error = reason

        account = SimpleNamespace(account_id="main", quiet_hours_active=lambda *args: False)
        transport = Mock()
        transport.send.return_value = SimpleNamespace(delivered=True, label="test", reason="", metadata={})
        runner = NotificationQueueRunner(Queue(), SimpleNamespace(load_all=lambda: [account]), lambda account: transport)
        self.assertEqual(1, runner.run_once())
        self.assertEqual("done", job.status)
        text = transport.send.call_args.args[0]
        self.assertIn(request.source_text, text)
        self.assertIn("📰 뉴스·공시 · 스트래티지", text)
        self.assertNotIn("AI 의견", text)
        self.assertEqual("MSTR", job.context["symbol"])
        self.assertEqual(0, runner.run_once())
        transport.send.assert_called_once()

    def test_price_only_typedb_trigger_is_not_a_relation_change(self):
        context = {
            "notificationDecisionMode": "typedb-context-observation",
            "reasoningDeliveryTrigger": {"facts": {"confirmedSignalTransitions": [{"signalId": "price", "observedValue": 3.8}]}},
            "ontologyRelationContext": {"decision": {"selectedRuleId": "graph.materiality.alert_candidate.v1"}},
        }
        self.assertEqual("price-change", notification_kind("investmentInsight", context).key)

    def test_lightweight_web_list_keeps_upstream_notification_kind(self):
        from digital_twin.infrastructure.web.adapters.notification_presentation import notification_job_list_payload

        cases = [
            ("ai-interpretation", {
                "notificationDecisionMode": "context-narrative",
                "notificationWriterProvenance.aiAuthored": True,
                "notificationAiValidatedResponse.action": "NO_ACTION",
            }),
            ("investment-decision", {"notificationAiValidatedResponse.action": "ADD"}),
            ("price-change", {
                "notificationDecisionMode": "typedb-context-observation",
                "ontologyRelationContext.decision.selectedRuleId": "graph.materiality.alert_candidate.v1",
                "reasoningDeliveryTrigger.facts.confirmedSignalTransitions": [{"signalId": "price"}],
            }),
            ("price-change", {
                "reasoningDeliveryTrigger.facts.cryptoTransitions": [{"symbol": "ETH"}],
            }),
            ("relation-change", {"ontologyRelationContext.engine": "typedb"}),
        ]
        for expected, fields in cases:
            with self.subTest(kind=expected):
                fields["symbolDisplayName"] = "종목 이름"
                job = MySQLNotificationJobStore.list_job_from_row({
                    "job_id": "list-job", "message_type": "investmentInsight",
                    "symbol": "MSTR", "text": "기존 본문은 유지합니다.",
                    "presentation_json": json.dumps(fields),
                })
                with patch.object(NotificationRenderingService, "apply_investment_presentation_contract",
                                  side_effect=AssertionError("list must not reconstruct a decision")):
                    payload = notification_job_list_payload(job, 2, {"_skipOperationalSchemaBootstrap": "1"})
                self.assertEqual(expected, payload["notificationKind"])
                self.assertIn("종목 이름", payload["title"])
                self.assertIn("기존 본문은 유지합니다.", payload["textPreview"])
        columns = notification_list_presentation_column()
        projection = notification_list_presentation_join()
        self.assertEqual(1, projection.count("JSON_TABLE("))
        self.assertNotIn("JSON_EXTRACT(", columns)
        self.assertIn("$.context.metadata.notificationDecisionMode", projection)
        self.assertNotIn("'$.context'", projection)
        self.assertNotIn("'$.context.ontologyRelationContext'", projection)

    def test_lightweight_list_exposes_authored_summary_without_rebuilding_decision(self):
        from digital_twin.infrastructure.web.adapters.notification_presentation import notification_job_list_payload

        fields = {
            "customerInvestmentDocument.headline": "수요 전망 변경",
            "customerInvestmentDocument.lead": "매출 가정의 재검토가 필요합니다.",
        }
        job = MySQLNotificationJobStore.list_job_from_row({
            "job_id": "summary-job", "message_type": "investmentInsight",
            "symbol": "TEST", "text": "원문은 상세에 유지합니다.",
            "presentation_json": json.dumps(fields),
        })
        job.context["deliveryReasons"] = ["22:00 quiet hours"]
        with patch.object(NotificationRenderingService, "apply_investment_presentation_contract",
                          side_effect=AssertionError("list must not reconstruct a decision")):
            payload = notification_job_list_payload(job, 2, {"_skipOperationalSchemaBootstrap": "1"})
        self.assertEqual({
            "headline": "수요 전망 변경", "reason": "매출 가정의 재검토가 필요합니다.",
        }, payload["investmentSummary"])
        self.assertNotIn("customerInvestmentDocument", payload)
        projection = notification_list_presentation_join()
        self.assertIn("$.context.customerInvestmentDocument.lead", projection)
        self.assertNotIn("'$.context.customerInvestmentDocument'", projection)

    def test_watchlist_zero_return_is_not_shown(self):
        context = {"ontologyRelationContext": {"facts": {"quantity": 0, "isHolding": False, "isWatchlist": True, "profitLossRate": 0, "currentPrice": 100}}}
        self.assertFalse(any("수익률" in line for line in _flow_rows(context, 5)))
        context["ontologyRelationContext"]["facts"]["quantity"] = 2
        self.assertTrue(any("수익률" in line for line in _flow_rows(context, 5)))

    def test_crypto_down_threshold_is_negative(self):
        context = {"reasoningDeliveryTrigger": {"facts": {"cryptoTransitions": [{
            "symbol": "ETH", "horizon": "24h", "changePct": -3.4, "thresholdPct": 3,
            "direction": "down", "transition": "threshold-crossed",
        }]}}}
        self.assertIn("하락 알림 기준 -3.0%", " ".join(_trigger_rows(context)))

    def test_missing_optional_reason_is_not_a_delivery_veto(self):
        queue = Mock()
        runner = NotificationQueueRunner(queue, Mock(), Mock())
        job = NotificationJob.create("확인된 가격 변화입니다.", message_type="investmentInsight")
        explanation = {"validation": {"state": "invalid", "errors": ["primary-cause-missing", "primary-cause-category-invalid"]}}
        with patch("digital_twin.modules.notifications.application.notification.workflow.build_customer_delivery_explanation", return_value=explanation):
            self.assertTrue(runner.apply_customer_delivery_explanation_gate(job))
        queue.mark_suppressed.assert_not_called()

    def test_false_action_transition_is_still_rejected(self):
        queue = Mock()
        runner = NotificationQueueRunner(queue, Mock(), Mock())
        job = NotificationJob.create("판단이 바뀌었습니다.", message_type="investmentInsight")
        explanation = {"validation": {"state": "invalid", "errors": ["action-transition-without-change"]}}
        with patch("digital_twin.modules.notifications.application.notification.workflow.build_customer_delivery_explanation", return_value=explanation):
            self.assertFalse(runner.apply_customer_delivery_explanation_gate(job))
        queue.mark_suppressed.assert_called_once()

    def test_unknown_recipient_cannot_fall_back_to_another_account(self):
        factory = Mock()
        job = NotificationJob.create("계좌 자료", account_id="missing", message_type="portfolioHoldingsSnapshot")
        with self.assertRaisesRegex(RuntimeError, "수신 계정"):
            NotificationDispatchService(Mock(), factory).deliver(job, {"other": object()}, job.text)
        factory.assert_not_called()

    def test_holding_enrichment_is_account_scoped(self):
        enricher = NotificationHoldingSnapshotEnricher(lambda: {"other": {"positions": {"MSTR": {"quantity": 20}}}})
        self.assertEqual(({}, {}), enricher.state_and_position_for_symbol("main", "MSTR"))
        self.assertEqual(({}, {}), enricher.state_and_position_for_symbol("", "MSTR"))

    def test_template_does_not_generate_a_canned_ai_opinion(self):
        values = text_context("연결이 복구됐습니다.", "monitorConnection", "main", "계정")
        with patch("digital_twin.modules.decisions.domain.notification_ai.enrich_notification_ai_context", side_effect=AssertionError("template cannot infer")):
            result = render_notification(NotificationTemplate("monitorConnection", "{body}"), values)
        self.assertNotIn("AI 의견", result)


if __name__ == "__main__":
    unittest.main()
