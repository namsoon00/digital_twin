import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.monitoring_service import MonitorRunner
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.message_types import MARKET_OBSERVATION, PORTFOLIO_HOLDINGS_SNAPSHOT
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.domain.portfolio import AccountSnapshot, utc_now_iso
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.strategy import decisions_for_positions


class MemoryMonitorStore:
    def __init__(self):
        self._previous = {}
        self._sent = {}

    @property
    def previous(self):
        return self._previous

    @property
    def sent(self):
        return self._sent

    def save_snapshot(self, snapshot):
        self._previous[snapshot.account_id] = snapshot.to_monitor_state()

    def mark_sent(self, events):
        stamp = utc_now_iso()
        for event in events:
            self._sent[event.key] = stamp
            self._sent[event.cadence_key()] = stamp

    def write(self):
        return None


class MonitoringForceSnapshotTests(unittest.TestCase):
    def test_market_observation_is_emitted_while_typedb_reasoning_is_deferred(self):
        previous_position = normalize_position({
            "symbol": "000660",
            "name": "SK하이닉스",
            "market": "KR",
            "currency": "KRW",
            "quantity": 1,
            "currentPrice": 200000,
            "updatedAt": utc_now_iso(),
        })
        current_position = normalize_position({
            "symbol": "000660",
            "name": "SK하이닉스",
            "market": "KR",
            "currency": "KRW",
            "quantity": 1,
            "currentPrice": 202000,
            "updatedAt": utc_now_iso(),
        })
        previous = AccountSnapshot(
            "main", "메인", "toss", "live", "ok", utc_now_iso(),
            portfolio_summary([previous_position]), [previous_position], [], metadata={},
        )
        current = AccountSnapshot(
            "main", "메인", "toss", "live", "ok", utc_now_iso(),
            portfolio_summary([current_position]), [current_position], [],
            metadata={
                "ontology": {
                    "projection": {
                        "status": "deferred-to-reasoning-worker",
                        "reason": "전용 온톨로지 추론 워커 처리 대기",
                    },
                },
            },
        )

        events = RealtimeMonitor().events_for_snapshot(current, previous.to_monitor_state())
        observations = [event for event in events if event.rule == MARKET_OBSERVATION]

        self.assertEqual(1, len(observations))
        self.assertEqual("000660", observations[0].symbol)
        self.assertTrue(observations[0].metadata["observationOnly"])
        self.assertFalse(observations[0].metadata["investmentJudgement"])
        self.assertIn("매수·매도 판단 없음", "\n".join(observations[0].lines))
        self.assertTrue(current.metadata["ontology"]["inferenceMissingState"]["pending"])

    def test_verified_native_no_match_is_not_reported_as_an_inference_failure(self):
        reason_code, reason, detail = RealtimeMonitor().ontology_inference_missing_reason_from_metadata({
            "ontology": {
                "projection": {
                    "status": "ok",
                    "graphStore": "typedb",
                    "ruleboxExecution": {"status": "empty"},
                    "inferenceBox": {
                        "status": "empty",
                        "graphStore": "typedb",
                        "nativeTypeDbReasoningCompleted": True,
                        "nativeInferenceOutcome": "no-match",
                        "generationAligned": True,
                        "sourceAboxSnapshotId": "abox-manifest:current",
                    },
                },
            },
        })

        self.assertEqual("", reason_code)
        self.assertEqual("", reason)
        self.assertTrue(detail["noMatch"])

    def test_force_run_adds_all_holdings_snapshot_event_with_freshness(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])
        sent = []

        def snapshot_builder(_account):
            position = normalize_position({
                "symbol": "AAPL",
                "name": "Apple",
                "currency": "USD",
                "currentPrice": 327.5,
                "averagePrice": 313.5,
                "marketValue": 327.5,
                "marketValueKrw": 450000,
                "profitLossRate": 4.48,
                "quantity": 1,
                "sellableQuantity": 1,
                "updatedAt": utc_now_iso(),
            })
            portfolio = portfolio_summary([position])
            return AccountSnapshot(
                "main",
                "메인",
                "toss",
                "live",
                "ok",
                utc_now_iso(),
                portfolio,
                [position],
                decisions_for_positions([position], portfolio),
            )

        def sender(events, dry_run=False, accounts=None, source_event=None):
            sent.extend(events)
            return SimpleNamespace(delivered=True)

        events = MonitorRunner(
            [account],
            store=MemoryMonitorStore(),
            monitor=RealtimeMonitor(),
            snapshot_builder=snapshot_builder,
            event_sender=sender,
        ).run_once(dry_run=True, force=True)

        holdings_events = [event for event in events if event.rule == PORTFOLIO_HOLDINGS_SNAPSHOT]
        self.assertEqual(1, len(holdings_events))
        self.assertEqual(events, sent)
        self.assertIn("Apple / AAPL", "\n".join(holdings_events[0].lines))
        self.assertTrue(holdings_events[0].metadata["dataFreshnessRequired"])
        self.assertEqual("fresh", holdings_events[0].metadata["dataFreshness"]["status"])


if __name__ == "__main__":
    unittest.main()
