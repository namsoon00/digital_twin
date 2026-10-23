"""Customer-use boundaries and receipt-clock outcome regressions."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock

from digital_twin.modules.outcomes.domain.hypothesis_outcome_contract import observation_domain_status
from digital_twin.modules.outcomes.domain.notification_timing import VERSION, timing_contract, advance_timing
from digital_twin.modules.outcomes.application.notification_timing_service import NotificationTimingService
from digital_twin.modules.notifications.domain.rule_event_presentation import event_conclusion
from digital_twin.modules.notifications.domain.rulebox_notification_summary import compact_rulebox_summary
from test_rule_notification_intent import registered_context, packet_for


class RuleTimingValidationTests(TestCase):
    def test_frozen_required_trend_metric_repairs_gap_without_inventing_a_measurement(self):
        contract = {"requiredObservationDomains": ["quote", "trend"], "criteria": [{
            "criterionId": "premise", "metric": "ma20DistanceChangePp", "operator": "<", "threshold": 0,
            "role": "cause", "required": True, "requiredObservationDomains": ["trend"]}]}
        facts = {"currentPrice": 100, "ma20DistanceChangePp": -0.12}
        self.assertEqual([], observation_domain_status(facts, contract)["missingObservationDomains"])
        for value in [None, "", float("nan"), float("inf")]:
            self.assertIn("trend", observation_domain_status({**facts, "ma20DistanceChangePp": value}, contract)["missingObservationDomains"])
        generic = {"requiredObservationDomains": ["trend"]}
        self.assertIn("trend", observation_domain_status({"ma20Distance": 0, "ma20": 0}, generic)["missingObservationDomains"])

    def test_quarantine_and_observation_are_visible_in_real_customer_event(self):
        fingerprints = set()
        for status, expected in [("quarantined", "성과 재검증 중"), ("shadow", "검증 중인 관찰"),
                                 ("observed", "검증 중인 관찰"), ("active", "별도 검증")]:
            context = registered_context()
            h = context["ontologyRelationContext"]["hypothesisSet"]["hypotheses"][0]
            h["qualification"] = {"status": status}
            packet = packet_for(context)
            events = [e for e in packet["customerEvents"] if e["purpose"] == "hypothesis-evidence"]
            self.assertTrue(events)
            fingerprints.add(events[0]["fingerprint"])
            self.assertIn(expected, event_conclusion(events[0]))
            self.assertFalse(events[0]["evidenceUse"]["tradeTimingValidated"])
            summary = compact_rulebox_summary(packet)
            self.assertIn(expected, summary["lead"] + str(summary["sections"]))
            packet["customerEvents"] = events * 2
            duplicate_summary = compact_rulebox_summary(packet)
            self.assertEqual(1, duplicate_summary["lead"].count(expected))
        # Explaining qualification must not create a new evidence-change push.
        self.assertEqual(1, len(fingerprints))

    def test_timing_contract_requires_proven_hypothesis_event(self):
        packet = packet_for(registered_context())
        contract = timing_contract(packet)
        self.assertEqual(VERSION, contract["version"])
        self.assertFalse(contract["tradeTimingQualified"])
        self.assertTrue(contract["horizons"])
        packet["current"]["proofIdentityVerified"] = False
        self.assertEqual({}, timing_contract(packet))

    def test_receipt_clock_rejects_old_future_and_synthetic_quotes(self):
        row = {"deliveredAt": "2026-10-22T01:05:00Z", "contract": {"horizons": [60], "signalPrice": 100}, "state": "awaiting-entry"}
        for source, quality in [("2026-10-22T01:04:59Z", "actual"), ("2026-10-22T01:08:00Z", "actual"),
                                ("2026-10-22T01:05:30Z", "mock")]:
            result = advance_timing(row, {"entry": {"currentPrice": 101, "sourceAsOf": source, "dataQuality": quality}}, "2026-10-22T01:06:00Z")
            self.assertNotIn("entry", result)
        expired = advance_timing(row, {}, "2026-10-22T01:11:00Z")
        self.assertEqual("unmeasurable", expired["state"])
        self.assertEqual("post-delivery-quote-unavailable", expired["reason"])

    def test_observation_freezes_delivery_baseline_and_does_not_claim_net_profit(self):
        row = {"deliveredAt": "2026-10-22T01:05:00Z", "contract": {"horizons": [60], "signalPrice": 100}}
        quote = lambda price, time: {"currentPrice": price, "sourceAsOf": time, "dataQuality": "actual", "provider": "fixture"}
        first = advance_timing(row, {"entry": quote(102, "2026-10-22T01:05:30Z")}, "2026-10-22T01:06:00Z")
        result = advance_timing(first, {"entry": quote(999, "2026-10-22T01:06:00Z"), "60": quote(103.02, "2026-10-22T02:05:01Z")}, "2026-10-22T02:06:00Z")
        self.assertEqual(102, result["entry"]["price"])
        self.assertEqual(2, result["signalToEntryChangePct"])
        self.assertEqual(1, result["outcomes"]["60"]["grossPriceChangePct"])
        self.assertIsNone(result["outcomes"]["60"]["netReturnPct"])
        self.assertFalse(result["tradeTimingQualified"])
        self.assertEqual("complete", result["state"])

    def test_observer_registers_only_matching_receipts_and_uses_minute_quotes(self):
        packet = packet_for(registered_context())
        contract = timing_contract(packet)
        contract.update(accountId="main", symbol="MSTR", horizons=[60])
        row = {"receiptId": "receipt", "jobId": "job", "deliveredAt": "2026-10-22T01:05:00Z", "contract": contract}
        store, receipts, series = Mock(), Mock(), Mock()
        store.cursor.return_value = ("", "")
        store.pending.return_value = [deepcopy(row)]
        receipts.after.return_value = [deepcopy(row), {**row, "contract": {**contract, "accountId": "other"}}]
        series.load_outcome_observations.return_value = {"receipt/entry": {
            "currentPrice": 100, "sourceAsOf": "2026-10-22T01:05:30Z", "dataQuality": "actual"}}
        result = NotificationTimingService(store, receipts, series).observe("main", "2026-10-22T01:06:00Z")
        self.assertEqual(1, result["registeredReceiptCount"])
        self.assertEqual(["1m", "3m"], series.load_outcome_observations.call_args.args[1][0]["allowedGranularities"])
        self.assertEqual(100, store.save.call_args.args[0]["entry"]["price"])
