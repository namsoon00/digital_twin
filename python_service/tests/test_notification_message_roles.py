"""The final transport distinguishes current quotes from causal rule changes."""

from copy import deepcopy
import json
import unittest

from test_smart_price_observations import cycle, snapshot, SETTINGS
from test_rulebox_decision_brief import fixture
from digital_twin.modules.market_data.domain.monitoring import RealtimeMonitor
from digital_twin.modules.notifications.application.notification.intake import NotificationIngressService
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.notifications.application.typedb_observation_message import typedb_observation_telegram_message
from digital_twin.modules.notifications.domain.rulebox_notification_summary import compact_rulebox_summary


def price_event(role="holding", **changes):
    state = {}
    for minute, price in ((0, 100), (1, 100), (2, 99.5), (3, 96)):
        state, events = cycle(state, minute, price, role, **changes)
    return events[0]


def price_job(event):
    return NotificationIngressService().job_from_alert(event)


class NotificationMessageRoleTests(unittest.TestCase):
    def test_current_data_survives_producer_queue_and_final_template_without_live_reads(self):
        coverage = {
            "investor": {"observedFields": ["foreignNetVolume", "institutionNetVolume", "individualNetVolume"],
                         "sourceAsOf": "2026-10-15T01:02:00Z", "measurementType": "intraday-estimate"},
            "ccnl": {"observedFields": ["tradeStrength", "buyVolume", "sellVolume", "volume", "tradingValue"],
                     "sourceAsOf": "2026-10-15T01:03:00Z", "sourceTimestampState": "provider-execution"},
            "orderbook": {"observedFields": ["orderbookBidVolume", "orderbookAskVolume"], "sourceAsOf": "2026-10-15T01:01:00Z"},
            "volume": {"values": {"volumeRatio": 1.2}, "sourceAsOf": "2026-10-15T01:03:00Z",
                       "sourceTimestampState": "provider-execution", "measurementScope": "session-cumulative",
                       "ratioBasis": "previous-session-total", "marketSession": "regular", "provider": "fixture",
                       "numeratorVolume": 1100, "denominatorVolume": 1100 / 1.2},
        }
        event = price_event(average_price=90, change_rate=-4, price_change_usable=True, ma5=100, ma20=110, ma60=120,
            foreign_net_volume=-500, institution_net_volume=100, individual_net_volume=0, trade_strength=120,
            buy_volume=600, sell_volume=500, volume=1100, trading_value=105600, volume_ratio=1.2,
            orderbook_bid_volume=400, orderbook_ask_volume=600, market_signal_coverage=coverage)
        frozen = deepcopy(event.metadata)
        job = price_job(event)
        from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
        stored = {"text": job.text, "payload_json": json.dumps(MySQLNotificationJobStore.compact_job_payload(job))}
        job = MySQLNotificationJobStore.job_from_row(stored)
        job.context["notificationDetailLevel"] = "concise"
        renderer = NotificationRenderingService(template_renderer=lambda _: "old short template",
            context_enricher=lambda j: j.context.update(currentPrice=99999, tradeStrength=999))
        text = renderer.render(job)
        for value in ("시세 변화", "전일 대비 -4%", "외국인: 순매도 500주", "기관: 순매수 100주",
                      "개인: 순매수·매도 차이 0주", "체결강도 120", "매수 대기 400주", "매도 대기 600주",
                      "누적 거래량 1,100주", "누적 거래대금 105,600원", "시간 보정 추정", "20일 평균 가격",
                      "평균 매입가 90원", "10:02 KST", "10:01 KST", "이번 가격 변동분"):
            self.assertIn(value, text)
        self.assertNotIn("99999", text)
        self.assertNotIn("old short template", text)
        self.assertNotIn("평소 변동 비교", text)
        self.assertEqual(frozen, event.metadata)
        self.assertEqual(frozen["priceCurrentSnapshot"], job.context["priceCurrentSnapshot"])
        self.assertEqual("price-change", job.context["notificationPresentation"]["kind"])
        self.assertEqual(text, job.context["telegramMessage"])
        self.assertNotIn("계산 근거:", text)
        self.assertIn("계산 근거:", job.context["priceCurrentDetailText"])
        from digital_twin.infrastructure.web.adapters.notification_presentation import notification_job_public_payload
        payload = notification_job_public_payload(job, stale_minutes=2,
            include_customer_document=True, include_detail_document=True)
        self.assertIn("계산 근거:", payload["fullText"])
        self.assertIn("외국인: 순매도 500주", payload["fullText"])

    def test_missing_defaults_unsupported_markets_and_stale_flow_keep_distinct_clocks(self):
        event = price_event("watchlist", market="US", currency="USD")
        text = NotificationRenderingService().render(price_job(event))
        for value in ("전일 대비 미확인", "이 시장의 자료 미지원", "체결강도 미제공", "평균 가격 자료 미확인", "보유 없음"):
            self.assertIn(value, text)
        self.assertNotIn("평가 수익률 0%", text)
        self.assertNotIn("평균 가격보다 같음", text)
        missing_holding = NotificationRenderingService().render(price_job(price_event(quantity=0)))
        self.assertIn("보유 수량·매입가 미확인", missing_holding)
        self.assertNotIn("관심 종목", missing_holding)
        stale = price_event(foreign_net_volume=500, market_signal_coverage={"investor": {
            "observedFields": ["foreignNetVolume"], "sourceAsOf": "2026-10-14T05:30:00Z", "freshnessStatus": "stale",
            "measurementType": "daily-final", "rawResponse": {"secret": "never-copy"}}})
        text = NotificationRenderingService().render(price_job(stale))
        self.assertIn("과거 값 참고", text)
        self.assertIn("10/14 14:30 KST", text)
        self.assertNotIn("never-copy", str(stale.metadata["priceCurrentSnapshot"]))

    def test_scope_mismatch_original_replay_and_partial_delivery_preserve_original(self):
        job = price_job(price_event(trade_strength=123))
        renderer = NotificationRenderingService()
        text = renderer.render(job)
        for preserve in ({"notificationReplayPreserveOriginal": True}, {"transportDelivery": {"message": text}}):
            original = deepcopy(job)
            original.context.update(preserve)
            original.context["priceCurrentSnapshot"]["accountId"] = "other"
            self.assertEqual(text, renderer.render(original))
        from digital_twin.modules.notifications.domain.price_current_snapshot import price_current_content
        for field in ("accountId", "symbol", "observedAt"):
            wrong = deepcopy(job.context)
            wrong["priceCurrentSnapshot"][field] = "other"
            self.assertIsNone(price_current_content(wrong, job.account_id, job.context["symbol"]))
        for field in ("symbol", "currency", "source_as_of"):
            wrong = deepcopy(job.context)
            wrong["marketObservation"]["quoteWitness"][field] = "other"
            self.assertIsNone(price_current_content(wrong, job.account_id, job.context["symbol"]))

    def test_legacy_cumulative_path_also_captures_current_data_without_changing_trigger(self):
        old = snapshot(0, 100).to_monitor_state()
        snap = snapshot(1, 104, trade_strength=120)
        monitor = RealtimeMonitor(settings={**SETTINGS, "marketObservationSmartEnabled": "0"})
        events = monitor.market_observation_events(snap, old)
        self.assertEqual(1, len(events))
        event = events[0]
        self.assertFalse(event.metadata["deliveryDeferred"])
        self.assertAlmostEqual(4, event.metadata["marketObservation"]["changePct"])
        self.assertIn("체결강도 120", NotificationRenderingService().render(price_job(event)))

    def test_relation_uses_only_selected_rule_values_and_preserves_full_market_detail(self):
        context = fixture()
        packet = context["relationChangeEvidence"]
        packet["customerEvents"] = [{"sourceRuleIds": ["rule:linked"], "purpose": "hypothesis-evidence", "changeKind": "support-added"}]
        rule = next(r for r in packet["current"]["rules"] if r["id"] == "rule:linked")
        rule["conditions"] = [{"conditionId": "strength", "field": "tradeStrength", "observedValue": 120,
                               "resolvedThreshold": {"value": 110}, "operator": ">=", "matchedByTypeDB": True}]
        next(r for r in packet["previous"]["rules"] if r["id"] == "rule:linked")["conditions"] = [
            {**rule["conditions"][0], "observedValue": 100}]
        frozen = deepcopy(packet)
        summary = compact_rulebox_summary(packet)
        sections = {key: rows for key, _, rows in summary["sections"]}
        self.assertIn("체결강도 100 → 120 / 기준 110 이상", " ".join(sections["decision-impact"]))
        self.assertNotIn("-15%", " ".join(sections["decision-impact"]))
        self.assertIn("지지 근거 추가", " ".join(sections["changed-rules"]))
        self.assertEqual(frozen, packet)
        text = typedb_observation_telegram_message(context)
        self.assertNotIn("외국인: 순매도", text)
        self.assertIn("외국인: 순매도", context["customerInvestmentDetailText"])

    def test_multiple_trigger_rules_keep_separate_thresholds_for_the_same_measurement(self):
        packet = fixture()["relationChangeEvidence"]
        packet["customerEvents"] = [{"sourceRuleIds": ["a", "b"], "purpose": "hypothesis-evidence", "changeKind": "support-added"}]
        rules = [{"id": rid, "label": label, "conditions": [{"conditionId": "strength", "field": "tradeStrength",
                  "observedValue": 120, "resolvedThreshold": {"value": threshold}, "operator": ">=", "matchedByTypeDB": True}]}
                 for rid, label, threshold in (("a", "체결 회복 조건", 100), ("b", "체결 확대 조건", 115))]
        packet["current"]["rules"] = rules
        packet["previous"]["rules"] = [{**r, "conditions": [{**r["conditions"][0], "observedValue": 95}]} for r in rules]
        summary = compact_rulebox_summary(packet)
        text = "\n".join(row for _, _, rows in summary["sections"] for row in rows)
        self.assertIn("체결 회복 조건: 체결강도 95 → 120 / 기준 100 이상", text)
        self.assertIn("체결 확대 조건: 체결강도 95 → 120 / 기준 115 이상", text)
        # A lifecycle may still reference both rules while only one changed.
        packet["customerEvents"][0]["ruleComparisons"] = [{"ruleId": "b"}]
        summary = compact_rulebox_summary(packet)
        sections = {key: rows for key, _, rows in summary["sections"]}
        self.assertNotIn("체결 회복 조건", " ".join(sections["decision-impact"]))
        self.assertNotIn("체결 회복 조건", " ".join(sections["changed-rules"]))
        self.assertIn("체결 확대 조건", " ".join(sections["changed-rules"]))
        # Removed links retain their original name from the previous receipt.
        packet["current"]["rules"] = [rules[0]]
        packet["customerEvents"][0]["changeKind"] = "support-removed"
        summary = compact_rulebox_summary(packet)
        self.assertIn("체결 확대 조건 · 지지 근거 해제", str(summary["focusContract"]["changedRules"]))


if __name__ == "__main__":
    unittest.main()
