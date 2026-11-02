"""Dated report scope, unchanged reuse, missing evidence and explicit delivery."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.domain.portfolio_report import build_report, reviewed_question, verify_report
from digital_twin.modules.ai_orchestration.application.portfolio_reports import PortfolioReportService
from digital_twin.modules.notifications.application.ai_report_message import report_summary, render_ai_report
from digital_twin.infrastructure.web.routes.portfolio_reports import portfolio_report_response

NOW = "2026-11-02T05:00:00Z"


def question():
    contract = {"period": "2026-06-30", "required": "매출과 비용의 비교"}
    return {"caseId": "case-report", "accountId": "report-test", "symbol": "EXAMPLE", "worldId": "world-test",
        "question": "매출과 비용의 변화를 확인할 수 있나요?", "questionContract": contract,
        "questionAssessment": {"version": "original-question-assessment-v1", "status": "partial",
            "questionContract": deepcopy(contract), "researchReviewInputId": "review-test", "executionInputId": "input-test",
            "taskId": "task-test", "at": "2026-11-01T05:00:00Z", "answer": "해당 보고기간의 매출은 증가했습니다. 비용 비교는 아직 확인하지 못했습니다.",
            "missingRequirements": ["같은 기간의 비용 원문"], "evidenceIds": ["fact-test"],
            "judgmentImpact": {"reason": "매출 확대 근거는 있지만 이익 개선 여부는 아직 확인할 수 없습니다.", "opinionChange": "unchanged"},
            "sources": [{"factId": "fact-test", "title": "공식 공시", "sourceUrl": "https://example.com/report", "periodEnd": "2026-06-30"}]}}


def fixture_report(account="report-test", request="request-test-0001"):
    answer = reviewed_question(question(), "report-test", "EXAMPLE", "world-test", NOW)
    return build_report(account, NOW, "2026-11-01T23:00:00Z", [{"symbol": "EXAMPLE", "name": "예시 기업", "scope": "holding",
        "opinion": None, "answers": [answer], "companyReport": {}, "openQuestions": [], "coverage": {}}], request_id=request)


class PortfolioReportTests(unittest.TestCase):
    def test_partial_research_preserves_scope_gaps_and_original_clock(self):
        case = question(); before = deepcopy(case)
        result = reviewed_question(case, "report-test", "EXAMPLE", "world-test", NOW)
        self.assertEqual(before, case)
        self.assertEqual("partial", result["answerStatus"])
        self.assertEqual(["같은 기간의 비용 원문"], result["missingRequirements"])
        self.assertEqual("2026-11-01T05:00:00Z", result["at"])
        self.assertEqual("not-reassessed", result["currentValidity"])
        self.assertTrue(result["researchReviewInputId"])

    def test_foreign_scope_future_changed_contract_or_missing_sources_are_excluded(self):
        for field, value in (("accountId", "other"), ("symbol", "OTHER"), ("worldId", "other")):
            case = question(); case[field] = value
            self.assertIsNone(reviewed_question(case, "report-test", "EXAMPLE", "world-test", NOW))
        for key, value in (("at", "2026-11-03T00:00:00Z"), ("researchReviewInputId", ""),
                           ("questionContract", {"period": "other"}), ("sources", [])):
            case = question(); case["questionAssessment"][key] = value
            self.assertIsNone(reviewed_question(case, "report-test", "EXAMPLE", "world-test", NOW))

    def test_report_without_current_quote_is_dated_and_tamper_evident(self):
        report = fixture_report()
        self.assertTrue(verify_report(report))
        self.assertEqual(0, report["newModelCalls"])
        self.assertEqual(0, report["coverage"]["withOpinion"])
        self.assertEqual(1, report["coverage"]["reviewedAnswers"])
        report["subjects"][0]["answers"][0]["answerStatus"] = "answered"
        self.assertFalse(verify_report(report))
        with self.assertRaises(ValueError):
            report_summary(report)

    def test_retried_request_reuses_snapshot_without_reading_sources_or_publishing(self):
        report = fixture_report()
        repo = SimpleNamespace(by_request=Mock(return_value=report))
        sources = SimpleNamespace(accounts=lambda: [{"accountId": "report-test"}], capture=Mock())
        publish = Mock()
        service = PortfolioReportService(repo, sources, publish)
        self.assertEqual(report, service.create("report-test", "request-test-0001"))
        sources.capture.assert_not_called(); publish.assert_not_called()
        with self.assertRaises(ValueError): service.create("other", "request-test-0001")

    def test_summary_keeps_partial_caveats_and_escapes_source_text(self):
        report = fixture_report(); artifact = report_summary(report)
        body = render_ai_report(artifact)
        self.assertIn("부분 답변", body)
        self.assertIn("같은 기간의 비용 원문", body)
        self.assertIn("11/01", body)
        artifact["body"] = "바뀐 주장"
        with self.assertRaises(ValueError): render_ai_report(artifact)

    def test_owner_only_gets_never_create_or_send_and_mutations_require_write_access(self):
        request = SimpleNamespace(command="GET", share_access=lambda: SimpleNamespace(role="viewer"),
            send_payload=Mock(), ensure_writable=Mock(return_value=False), read_json_body=Mock())
        factory = Mock()
        portfolio_report_response(request, "/api/ai-reports", {}, factory)
        self.assertEqual(403, request.send_payload.call_args.args[0]); factory.assert_not_called()
        request.share_access = lambda: SimpleNamespace(role="owner")
        service = SimpleNamespace(list=Mock(return_value=[]), create=Mock(), send=Mock())
        factory.return_value = service
        portfolio_report_response(request, "/api/ai-reports", {"accountId": ["report-test"]}, factory)
        factory.assert_called_once_with(writable=False)
        service.list.assert_called_once_with("report-test"); service.create.assert_not_called(); service.send.assert_not_called()
        factory.reset_mock(); request.command = "POST"
        portfolio_report_response(request, "/api/ai-reports", {}, factory)
        factory.assert_not_called(); request.read_json_body.assert_not_called()

    def test_customer_ai_messages_use_account_bot_even_with_legacy_research_selection(self):
        from digital_twin.modules.notifications.infrastructure.notification import transport
        from digital_twin.modules.notifications.domain.notification_rule_models import default_notification_rule
        from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
        from digital_twin.modules.notifications.contracts import NotificationJob
        settings = {"aiResearchTelegramBotToken": "fixture-token", "aiResearchTelegramMessageTypes": "aiObservation,aiReport"}
        account = SimpleNamespace(notify_provider="telegram", telegram_bot_token="123:customer-fixture", telegram_chat_id="fixture-recipient")
        with patch.object(transport, "runtime_settings", return_value=settings):
            for kind in ("aiObservation", "aiReport"):
                self.assertIsNone(transport.notifier_for_ai_research(kind, account))
            notifier = transport.notifier_for_account(account)
            self.assertEqual("fixture-recipient", notifier.chat_id)
        rule = default_notification_rule("aiReport")
        self.assertFalse(rule.similarity_enabled); self.assertFalse(rule.market_hours_enabled)
        customer = SimpleNamespace(send=Mock(return_value=transport.NotificationResult(True, "customer-fixture")))
        factory = Mock(return_value=customer)
        research = Mock(side_effect=AssertionError("customer content must not select research bot"))
        dispatcher = NotificationDispatchService(SimpleNamespace(), factory, ai_research_notifier_factory=research)
        for kind in ("aiObservation", "aiReport"):
            job = NotificationJob.create("report", account_id="report-test", message_type=kind,
                context={"aiPortfolioReport": report_summary(fixture_report())})
            dispatcher.deliver(job, {"report-test": account}, "report")
            self.assertEqual("account", job.context["deliveryRoute"])
            factory.assert_called_with(account)
            for missing in ("", "other-account"):
                job.account_id = missing
                with self.assertRaisesRegex(RuntimeError, "수신 계정"):
                    dispatcher.deliver(job, {"report-test": account}, "report")
        self.assertEqual(2, customer.send.call_count)
        research.assert_not_called()
