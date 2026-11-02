"""Whole-universe analysis and no duplicate paid step after a persisted reply."""
from copy import deepcopy
from types import SimpleNamespace
import hashlib
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain import portfolio_analysis as c
from digital_twin.modules.ai_orchestration.application.portfolio_analysis import PortfolioAnalysisWorker
from digital_twin.modules.ai_orchestration.domain.portfolio_report_document import render_document
from digital_twin.modules.notifications.application.ai_report_message import report_summary
from digital_twin.modules.notifications.infrastructure.notification.transport import TelegramNotifier, NotificationResult
from digital_twin.infrastructure.web.routes.portfolio_reports import portfolio_report_response


def source(count=4):
    return {"snapshotAt": "2026-11-02T00:00:00Z", "subjects": [{"symbol": "S" + str(i), "name": "기업 " + str(i), "scope": "holding" if i < 2 else "watchlist",
        "worldId": "w", "position": {"currency": "KRW", "quantity": 10, "current_price": 100}, "coverage": {}, "answers": [], "openQuestions": []} for i in range(count)]}


def response(envelope, input_id):
    phase, data = envelope["phase"], envelope["data"]
    if phase == "read": return {"sourceIds": [], "reason": "제공된 계좌 사실 외에는 자료가 없습니다."}
    if phase == "review": return {"approved": [s + "." + d for s in data["draft"] for d in data["draft"][s]], "issues": []}
    if phase == "synthesis":
        return {"portfolio": {d: {"text": "보유 구조는 확인했지만 사업과 공통 외부 요인은 아직 확인할 수 없습니다.", "status": "unknown", "refs": [], "missing": ["실적 및 외부 요인 근거"]} for d in c.GLOBAL_DIMENSIONS}}
    result = {}
    for subject in data["subjects"]:
        key = next(k for k, r in data["selectedSources"].items() if r["symbol"] == subject["symbol"])
        result[subject["symbol"]] = {d: {"text": "사업 판단을 뒷받침할 공시가 아직 없습니다.", "status": "unknown", "refs": [], "missing": ["기업 공시"]} for d in c.DIMENSIONS}
        result[subject["symbol"]]["portfolio"] = {"text": "계좌 자료에서 보유 수량을 확인했습니다.", "status": "partial", "refs": [{"sourceId": key, "field": "quantity"}], "missing": ["최신 가치 근거"]}
    return result


def run_fixture(count=4):
    return {"version": c.VERSION, "analysisId": "a" * 64, "accountId": "analysis-test", "requestId": "analysis-test-request", "worldId": "w",
        "source": source(count), "createdAt": "2026-11-02T00:00:01Z", "status": "pending", "phase": "read", "batchIndex": 0,
        "readRound": 0, "step": 0, "completedStep": -1, "results": [], "execution": [], "sendWhenReady": False}


def fake_worker(count=4):
    state = run_fixture(count)
    first = c.task(state, 0); state["taskId"] = first["taskId"]
    runs = SimpleNamespace(state=state)
    runs.get = lambda *_: deepcopy(runs.state)
    runs.checkpoint = lambda job, run: setattr(runs, "state", deepcopy(run))
    control = SimpleNamespace(save_execution_input=lambda job, env: c.digest(env), execution_wait=lambda: None)
    model = Mock(side_effect=response)
    worker = PortfolioAnalysisWorker(runs, control, lambda scope: None,
        lambda: [{"accountId": "analysis-test", **s} for s in state["source"]["subjects"]], model, Mock(), lambda: [{"accountId": "analysis-test"}])
    return worker, runs, model, first


def completed_fixture():
    worker, runs, model, job = fake_worker()
    while True:
        result, children = worker(job)
        if not children: return runs.state["report"]
        job = children[0]; runs.state["taskId"] = job["taskId"]


class PortfolioAnalysisTests(unittest.TestCase):
    def test_every_subject_and_dimension_survives_missing_graph_with_auditable_refs(self):
        report = completed_fixture()
        self.assertEqual(4, report["coverage"]["subjects"])
        self.assertEqual(4, report["coverage"]["withGaps"])
        self.assertEqual({"S0", "S1", "S2", "S3"}, {s["symbol"] for s in report["subjects"]})
        for subject in report["subjects"]:
            self.assertEqual(set(c.DIMENSIONS), set(subject["claims"]))
            ref = subject["claims"]["portfolio"]["refs"][0]
            self.assertEqual(10, c.field(subject["sources"][ref["sourceId"]]["data"], ref["field"]))
        self.assertIn("기업 3", render_document(report))
        artifact = report_summary(report)
        self.assertEqual(hashlib.sha256(artifact["document"]["html"].encode()).hexdigest(), artifact["document"]["sha256"])
        self.assertIn("전체 4종목", artifact["body"])

    def test_cached_response_and_completed_transition_are_not_called_again(self):
        worker, runs, model, job = fake_worker(1)
        worker(job)
        self.assertEqual(1, model.call_count)
        worker(job)  # Crash after progress commit, before central task completion.
        self.assertEqual(1, model.call_count)
        state = run_fixture(1); state["taskId"] = job["taskId"]
        batch = worker._prepare(state); state["batch"] = batch
        env = c.freeze(job, "read", {**worker._data(state), "availableSources": c.catalog(batch["records"])})
        state["work"] = {"envelope": env, "inputId": "cached-input", "response": {"sourceIds": [], "reason": "읽기 완료"}, "respondedAt": "2026-11-02T00:00:01Z"}
        runs.state = state
        worker(job)
        self.assertEqual(1, model.call_count)

    def test_bad_scope_unread_citation_and_frozen_prompt_mutation_are_rejected(self):
        worker, runs, model, job = fake_worker(1)
        state = runs.state; state["batch"] = worker._prepare(state)
        data = worker._data(state)
        env = c.freeze(job, "write", data)
        draft = response(env, "input")
        c.validate_draft(draft, data)
        draft["S0"]["portfolio"]["refs"][0]["sourceId"] = "not-read"
        with self.assertRaises(ValueError): c.validate_draft(draft, data)
        draft = response(env, "input"); draft["OTHER"] = draft.pop("S0")
        with self.assertRaises(ValueError): c.validate_draft(draft, data)
        numeric = response(env, "input"); numeric["S0"]["portfolio"]["text"] = "수익률이 99%입니다."
        with self.assertRaises(ValueError): c.validate_draft(numeric, data)
        admitted = c.admit_draft(numeric, data)
        self.assertEqual("unknown", admitted["S0"]["portfolio"]["status"])
        env["data"]["subjects"][0]["symbol"] = "FOREIGN"
        with self.assertRaises(Exception): c.validate_input(env)

    def test_review_rejection_is_visible_and_not_sent_as_approved_claim(self):
        worker, runs, model, job = fake_worker(1)
        state = runs.state; state["batch"] = worker._prepare(state)
        data = worker._data(state); draft = response(c.freeze(job, "write", data), "input")
        reviewed = c.reviewed(draft, {"approved": ["S0.portfolio"], "issues": [{"key": "S0.portfolio", "reason": "시각 미확인"}]}, c.DIMENSIONS)
        self.assertEqual("unknown", reviewed["S0"]["portfolio"]["status"])
        self.assertEqual([], reviewed["S0"]["portfolio"]["refs"])

    def test_summary_and_file_resume_independently_and_unknown_upload_is_not_repeated(self):
        document = report_summary(completed_fixture())["document"]
        notifier = TelegramNotifier("123:test", "100")
        notifier.post_message = Mock(return_value=NotificationResult(True, "Telegram", metadata={"messageIds": ["1"], "receiptVerified": True, "destinationVerified": True}))
        notifier.post_document = Mock(return_value=NotificationResult(True, "Telegram", metadata={"documentMessageId": "2"}))
        saved = []
        first = notifier.send_report_resumable("summary", document, on_checkpoint=lambda p: saved.append(deepcopy(p)))
        self.assertTrue(first.delivered)
        self.assertTrue(first.metadata["documentReceiptVerified"])
        second = notifier.send_report_resumable("summary", document, checkpoint=saved[-1], on_checkpoint=lambda p: saved.append(deepcopy(p)))
        self.assertTrue(second.delivered)
        self.assertEqual(1, notifier.post_message.call_count); self.assertEqual(1, notifier.post_document.call_count)
        uncertain = deepcopy(saved[-1]); uncertain["document"]["state"] = "sending"
        self.assertFalse(notifier.send_report_resumable("summary", document, checkpoint=uncertain, on_checkpoint=lambda p: None).delivered)
        self.assertEqual(1, notifier.post_document.call_count)
        foreign = deepcopy(saved[-1]); foreign["document"]["destinationFingerprint"] = "other"
        self.assertFalse(notifier.send_report_resumable("summary", document, checkpoint=foreign, on_checkpoint=lambda p: None).delivered)

    def test_analysis_endpoints_require_owner_and_read_never_starts_work(self):
        req = SimpleNamespace(command="GET", share_access=lambda: SimpleNamespace(role="viewer"), send_payload=Mock(), ensure_writable=Mock(return_value=False), read_json_body=Mock())
        factory = Mock()
        portfolio_report_response(req, "/api/ai-reports/analyses", {}, factory)
        self.assertEqual(403, req.send_payload.call_args.args[0]); factory.assert_not_called()
        req.share_access = lambda: SimpleNamespace(role="owner")
        service = SimpleNamespace(analysis=Mock(return_value=None), analyze=Mock()); factory.return_value = service
        portfolio_report_response(req, "/api/ai-reports/analyses", {"accountId": ["analysis-test"]}, factory)
        factory.assert_called_once_with(writable=False); service.analyze.assert_not_called()
        factory.reset_mock(); req.command = "POST"
        portfolio_report_response(req, "/api/ai-reports/analyses", {}, factory)
        factory.assert_not_called()
        path = "/api/ai-reports/analyses/" + "a" * 64 + "/resume"
        portfolio_report_response(req, path, {}, factory)
        factory.assert_not_called()
        req.ensure_writable.return_value = True
        req.read_json_body.return_value = {"accountId": "analysis-test"}
        service.resume_analysis = Mock(return_value={"status": "pending"})
        portfolio_report_response(req, path, {}, factory)
        service.resume_analysis.assert_called_once_with("analysis-test", "a" * 64)
        self.assertEqual(202, req.send_payload.call_args.args[0])
