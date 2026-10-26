"""Delivered facts stay usable; only independently reviewed added meaning sends."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai_insight_fixtures import plan, ref, review
from test_business_insight_progress import source_packet, retrieve, business_result
from digital_twin.modules.notifications.domain.delivered_insight_context import context, validate, TYPES, BODY_BYTES
from digital_twin.modules.notifications.infrastructure.delivery_context_reader import DeliveredInsightContextReader
from digital_twin.modules.ai_orchestration.domain import notification_contribution as contribution, delivery_insight
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality, accept_review, quality_block
from digital_twin.modules.ai_orchestration.domain.publication import repeat_block
from digital_twin.modules.ai_orchestration.domain.execution_lanes import material_key
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.infrastructure.transactions.ai_control_publication import AIControlPublication


def receipt(packet, key="rule-1", kind="investmentInsight", **overrides):
    body = "<b>기존 규칙 알림</b>\n평가 손실과 비중 기준을 확인했습니다."
    return {"jobId": key, "accountId": packet["accountId"], "symbol": packet["symbol"],
            "messageType": kind, "status": "delivered", "deliveredAt": packet["capturedAt"],
            "body": body, "messageSha256": hashlib.sha256(body.encode()).hexdigest(), **overrides}


def contextual_packet():
    value = retrieve(source_packet())[0]
    value["notificationContributionPolicy"] = contribution.POLICY
    value["deliveredNotificationContext"] = context([receipt(value)], value["accountId"], value["symbol"], value["capturedAt"])
    return value


def added_value(packet, kind="new-interpretation", evidence=None):
    return {"kind": kind, "comparedReceiptIds": contribution.receipt_ids(packet), "section": "summary",
            "reason": "기존 규칙의 조건을 원천 근거로 다시 연결해 손실 회복 설명의 한계를 구분합니다.",
            "evidence": evidence or [ref("currentPrice")]}


def contextual_result(packet=None):
    value = packet or contextual_packet()
    raw = plan()
    raw["judgmentChange"] = {"kind": "market", "impact": "weakens",
        "reason": "이전 가격 회복 설명을 원천 근거에 맞춰 재검토합니다.", "evidence": [ref("currentPrice")]}
    raw["notificationContribution"] = added_value(value)
    return {**validate_plan(raw, value), "input": value, "inputFingerprint": "same-source",
            "observedAt": value["capturedAt"]}


def contextual_review(packet, kind="new-interpretation", supported=True):
    value = review()
    value["judgmentChange"] = {"supported": True, "reason": "원천 근거와 판단의 관계를 확인했습니다."}
    value["notificationContribution"] = {"supported": supported, "kind": kind,
        "comparedReceiptIds": contribution.receipt_ids(packet),
        "reason": "규칙 알림에서 전달하지 않은 추가 설명이 실제 전달 문단에 있습니다."}
    return value


class NotificationContributionTests(unittest.TestCase):
    def test_receipts_keep_all_routes_but_exclude_failed_foreign_future_and_duplicate_deliveries(self):
        packet = source_packet()
        valid = [receipt(packet, kind, kind) for kind in TYPES]
        rows = valid + [deepcopy(valid[0]), receipt(packet, "failed", status="failed"),
            receipt(packet, "foreign", accountId="other"), receipt(packet, "other", symbol="OTHER"),
            receipt(packet, "future", deliveredAt="2099-01-01T00:00:00Z"),
            receipt(packet, "expired", deliveredAt="2020-01-01T00:00:00Z"),
            receipt(packet, "draft", kind="aiObservationDiagnostic")]
        before = deepcopy(rows)
        value = context(rows, packet["accountId"], packet["symbol"], packet["capturedAt"])
        self.assertEqual(set(TYPES), {row["messageType"] for row in value["entries"]})
        self.assertEqual(4, len(value["entries"]))
        self.assertEqual("delivered-context-only", value["authority"])
        validate(value, packet["accountId"], packet["symbol"], packet["capturedAt"])
        self.assertEqual(before, rows)

    def test_context_is_bounded_redacted_and_does_not_invent_missing_receipt_text(self):
        packet = source_packet()
        body = '<a href="https://example.test/#share_token=SECRET">근거 보기</a> ' + "같은 원천 자료 " * 1000
        rows = [receipt(packet, str(i), body=body, messageSha256=hashlib.sha256(body.encode()).hexdigest()) for i in range(10)]
        rows += [receipt(packet, "no-body", "aiObservation", body=""),
                 receipt(packet, "changed-body", "marketObservation", body="실제 발송문이 아닌 값")]
        value = context(rows, packet["accountId"], packet["symbol"], packet["capturedAt"])
        self.assertTrue(value["coverage"]["bounded"])
        self.assertEqual(2, value["coverage"]["missingBodies"])
        self.assertEqual(4, len(value["entries"]))
        for row in value["entries"]:
            self.assertNotIn("SECRET", row["text"])
            self.assertLessEqual(len(row["text"].encode()), BODY_BYTES)
            if row["jobId"] in {"no-body", "changed-body"}:
                self.assertEqual(("", "unavailable"), (row["text"], row["textStatus"]))

    def test_new_author_review_and_repair_inputs_are_frozen_without_changing_legacy_contracts(self):
        legacy = freeze_execution_input(source_packet(), [], [])
        saved = deepcopy(legacy)
        for company in (False, True):
            packet = source_packet()
            packet["notificationContributionPolicy"] = contribution.POLICY
            packet["deliveredNotificationContext"] = context([receipt(packet)], packet["accountId"], packet["symbol"], packet["capturedAt"])
            if company: packet["companyResearchPolicy"] = "company-research-cycle-v1"
            author = freeze_execution_input(packet, [], [])
            self.assertEqual(delivery_insight.PROMPT_VERSION, author["promptVersion"])
            for envelope in (author, freeze_repair_input(author, {}, ["참조 확인 필요"], "parent"),
                             freeze_review_input(contextual_result(packet))):
                validate_execution_input(envelope)
                self.assertIn("deliveredNotificationContext", envelope["prompt"])
                self.assertIn("notificationContribution", envelope["outputSchema"]["required"])
                self.assertNotIn("kind=business는 새 원천이", envelope["prompt"])
                self.assertNotIn("summary와 comparison에 새 사실", envelope["prompt"])
                self.assertNotIn("첫 관찰/이전 발송 없음은 current.lastDeliveredNotification이 비어 있는지로", envelope["prompt"])
                forged = deepcopy(envelope)
                forged["current"]["deliveredNotificationContext"]["entries"][0]["text"] = "위조된 이전 설명"
                with self.assertRaises(ValueError): validate_execution_input(forged)
        validate_execution_input(legacy)
        self.assertEqual(saved, legacy)

    def test_retrieval_preserves_context_and_cannot_promote_notification_ids_to_evidence(self):
        packet = contextual_packet()
        selected, _, _ = retrieve(packet)
        self.assertEqual(packet["deliveredNotificationContext"], selected["deliveredNotificationContext"])
        self.assertEqual(packet["lastDeliveredNotification"], selected["lastDeliveredNotification"])
        for change in ({"comparedReceiptIds": []}, {"comparedReceiptIds": ["other-rule"]},
                       {"evidence": [ref("currentPrice", fact_id="rule-1")]},
                       {"evidence": [ref("currentPrice", period="baseline")]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                contribution.validate({**added_value(packet), **change}, packet)

    def test_same_facts_can_gain_new_interpretation_but_repetition_and_generic_reviews_cannot_send(self):
        result = contextual_result()
        result["quality"] = accept_review(result, contextual_review(result["input"]), "review")
        self.assertEqual("", quality_block(result))
        now = datetime.fromisoformat(result["observedAt"].replace("Z", "+00:00"))
        old = {"accountId": result["input"]["accountId"], "symbol": result["input"]["symbol"],
               "inputFingerprint": result["inputFingerprint"], "insightFingerprint": "old-explanation",
               "deliveredAt": (now-timedelta(days=1)).isoformat()}
        self.assertEqual("", repeat_block(result, [old], old["accountId"], old["symbol"], now))
        for kind in ("repetition", "no-added-value"):
            bad = deepcopy(result); bad["notificationContribution"]["kind"] = kind
            self.assertEqual("rejected", local_quality(bad)["status"])
        for review_change in ({"supported": False}, {"kind": "repetition"}, {"comparedReceiptIds": []}):
            reviewed = contextual_review(result["input"])
            reviewed["notificationContribution"].update(review_change)
            self.assertEqual("rejected", accept_review(result, reviewed, "review")["status"])
        old["insightFingerprint"] = result["quality"]["insightFingerprint"]
        self.assertTrue(repeat_block(result, [old], old["accountId"], old["symbol"], now))
        old.update(insightFingerprint="different", deliveredAt=(now-timedelta(minutes=10)).isoformat())
        self.assertIn("3시간", repeat_block(result, [old], old["accountId"], old["symbol"], now))
        risk = deepcopy(result)
        risk["notificationContribution"]["kind"] = "material-risk-change"
        next(row for row in risk["input"]["facts"] if row["id"] == "quote-1")["currentPrice"] = 99
        risk["quality"] = accept_review(risk, contextual_review(risk["input"], "material-risk-change"), "risk-review")
        earlier_risk = deepcopy(risk)
        next(row for row in earlier_risk["input"]["facts"] if row["id"] == "quote-1")["currentPrice"] = 100
        self.assertNotEqual(local_quality(earlier_risk)["insightFingerprint"], risk["quality"]["insightFingerprint"])

    def test_same_business_facts_and_unchanged_conclusion_require_a_new_reviewed_explanation(self):
        packet = contextual_packet()
        packet["lastDeliveredNotification"] = {"facts": deepcopy(packet["facts"])}
        original = deepcopy(packet)
        original.pop("notificationContributionPolicy")
        result = business_result(original)
        result["input"] = packet
        basis = result["judgmentChange"]["evidence"]
        result["notificationContribution"] = added_value(packet, evidence=basis)
        result["judgmentChange"]["impact"] = "unchanged"
        result["quality"] = accept_review(result, contextual_review(packet), "review")
        self.assertEqual("accepted", result["quality"]["status"], result["quality"].get("errors"))
        for kind in ("new-interpretation", "material-risk-change", "correction"):
            changed = deepcopy(result); changed["notificationContribution"]["kind"] = kind
            self.assertEqual("accepted", accept_review(changed, contextual_review(packet, kind), "review")["status"])
        result["judgmentChange"]["impact"] = "insufficient"
        self.assertEqual("rejected", local_quality(result)["status"])

    def test_new_receipt_renews_comparison_without_renewing_source_facts_or_ai_baseline(self):
        packet = contextual_packet(); original = deepcopy(packet)
        key = material_key(packet, [])
        later = deepcopy(packet)
        later["capturedAt"] = "2026-10-24T00:01:00Z"
        later["deliveredNotificationContext"] = context([receipt(packet)], packet["accountId"], packet["symbol"], later["capturedAt"])
        self.assertEqual(key, material_key(later, []))
        later["deliveredNotificationContext"] = context([receipt(packet), receipt(later, "new-rule")],
            packet["accountId"], packet["symbol"], later["capturedAt"])
        self.assertNotEqual(key, material_key(later, []))
        self.assertEqual(original["facts"], later["facts"])
        self.assertEqual(original["lastDeliveredNotification"], later["lastDeliveredNotification"])
        result = contextual_result(packet)
        result["quality"] = accept_review(result, contextual_review(packet), "review")
        result["notificationContribution"]["reason"] = "검토 이후 다른 의미로 몰래 바꾼 문장입니다."
        self.assertTrue(quality_block(result))

    def test_receipt_reader_failure_is_not_an_empty_first_observation(self):
        store = Mock(); store.connect.side_effect = RuntimeError("history unavailable")
        with self.assertRaisesRegex(RuntimeError, "history unavailable"):
            DeliveredInsightContextReader(store).read("test", "TEST", "2026-10-24T00:00:00Z")

    def test_rule_and_ai_routes_share_the_receipt_send_lock(self):
        queue = Mock(); order = []
        @contextmanager
        def locked(account, key):
            order.append(("enter", key)); yield True; order.append(("exit", key))
        @contextmanager
        def inner(job, message):
            order.append(("send", job.message_type)); yield
        queue.delivery_subject_lock = locked
        publication = AIControlPublication({"investmentNotificationRoute": "ai-control"}, Mock(), queue, lambda: [])
        publication._delivery_guard = inner
        for kind in TYPES:
            job = NotificationJob.create("synthetic", account_id="test", message_type=kind,
                                         context={"accountId": "test", "symbol": "TEST"})
            with publication.delivery_guard(job, "synthetic"): pass
        for index in range(0, len(order), 3):
            self.assertEqual(("enter", "delivered-context:TEST"), order[index])
            self.assertEqual(("exit", "delivered-context:TEST"), order[index+2])

    def test_a_rule_delivered_during_analysis_requires_new_comparison_without_rewriting_the_input(self):
        from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
        from digital_twin.modules.notifications.domain.notifications import notification_debug_number
        from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed
        result = contextual_result()
        packet = result["input"]
        result["quality"] = accept_review(result, contextual_review(packet), "review")
        result["publication"] = {"status": "queued", "jobId": "new-ai-job"}
        job = NotificationJob(job_id="new-ai-job", account_id=packet["accountId"], account_label="",
            message_type="aiObservation", text="", context={"symbol": packet["symbol"], "aiControlTaskId": "task"})
        @contextmanager
        def locked(*args): yield True
        @contextmanager
        def connected():
            connection = Mock()
            connection.execute.return_value.fetchone.return_value = {"status": "completed",
                "account_id": packet["accountId"], "symbol": packet["symbol"], "result_json": json.dumps(result)}
            yield connection
        queue, store = Mock(), Mock()
        queue.delivery_subject_lock = locked; store.connect = connected
        publication = AIControlPublication({"investmentNotificationRoute": "ai-control"}, store, queue, lambda: [packet])
        publication.receipts = Mock(return_value=[]); publication.memory = Mock(return_value={})
        publication.review_block = Mock(return_value="")
        body = render_ai_observation(result, debug_number=notification_debug_number(job.job_id))
        before = deepcopy(packet)
        path = "digital_twin.modules.notifications.infrastructure.delivery_context_reader.DeliveredInsightContextReader.read"
        with patch("digital_twin.infrastructure.transactions.ai_control_publication.publication_block", return_value=""), \
             patch("digital_twin.infrastructure.settings.runtime_settings", return_value={"aiControlEnabled": "true"}):
            with patch(path, return_value=packet["deliveredNotificationContext"]):
                with publication.delivery_guard(job, body): pass
            for delivered_at in (packet["capturedAt"], "2026-10-24T00:01:00Z"):
                current = context([receipt(packet), receipt(packet, "during-analysis", deliveredAt=delivered_at)],
                    packet["accountId"], packet["symbol"], "2026-10-24T00:02:00Z")
                with patch(path, return_value=current), self.assertRaisesRegex(NotificationDeliverySuppressed, "추가 가치"):
                    with publication.delivery_guard(job, body): self.fail("new receipt must be compared")
        self.assertEqual(before, packet)


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class DeliveredContextStorageTests(unittest.TestCase):
    def test_successful_receipts_survive_job_retention_and_bind_research_subject_without_mutable_text(self):
        from digital_twin.infrastructure.settings import load_local_env, runtime_settings
        from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
        from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
        from mysql_fixtures import mysql_test_settings
        # Acquire before runtime_settings also opens the shared schema.
        load_local_env()
        mysql_test_settings()
        queue = MySQLNotificationJobStore(runtime_settings())
        account, jobs = "contribution-storage-test", []
        notifier = Mock(supports_delivery_checkpoints=False, supports_research_replies=False)
        notifier.send.return_value = SimpleNamespace(delivered=True, label="test", reason="", metadata={})
        dispatch = NotificationDispatchService(queue, lambda _: notifier)
        try:
            for kind in TYPES:
                job = NotificationJob.create("원래 발송문을 비교할 합성 자료입니다.", account_id=account,
                    message_type=kind, context={"accountId": account, "symbol": "TEST"})
                jobs.append(job.job_id); queue.upsert_job(job)
                dispatch.deliver(job, {account: object()}, job.text)
            cutoff = datetime.now(timezone.utc).isoformat()
            reader = DeliveredInsightContextReader(queue)
            before = reader.read(account, "TEST", cutoff)
            self.assertEqual(4, len(before["entries"]))
            with queue.transaction() as connection:
                for key in jobs: connection.execute("DELETE FROM notification_jobs WHERE job_id=%s", (key,))
            self.assertEqual(before, reader.read(account, "TEST", cutoff))
            self.assertEqual([], reader.read(account, "OTHER", cutoff)["entries"])
            self.assertEqual([], reader.read("other-account", "TEST", cutoff)["entries"])
        finally:
            with queue.transaction() as connection:
                for key in jobs:
                    connection.execute("DELETE FROM notification_delivery_attempts WHERE job_id=%s", (key,))
                    connection.execute("DELETE FROM notification_jobs WHERE job_id=%s", (key,))
