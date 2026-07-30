import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.monitoring_service import MonitorRunner
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.market_observations import apply_market_observation_outbox_baselines
from digital_twin.domain.message_types import (
    INVESTMENT_INSIGHT,
    MARKET_OBSERVATION,
    ONTOLOGY_OBSERVATION_FOLLOWUP,
    PORTFOLIO_HOLDINGS_SNAPSHOT,
)
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.domain.portfolio import AccountSnapshot, AlertEvent, utc_now_iso
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.strategy import decisions_for_positions
from digital_twin.infrastructure.mysql_monitoring_stores import (
    market_observation_followup_symbols,
    snapshot_state_for_persistence,
)


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

        events = RealtimeMonitor({"alertThresholds": "marketObservationPriceChangePct=0.6"}).events_for_snapshot(
            current,
            previous.to_monitor_state(),
        )
        observations = [event for event in events if event.rule == MARKET_OBSERVATION]

        self.assertEqual(1, len(observations))
        self.assertEqual("000660", observations[0].symbol)
        self.assertTrue(observations[0].metadata["observationOnly"])
        self.assertFalse(observations[0].metadata["investmentJudgement"])
        self.assertIn("매수·매도 판단 없음", "\n".join(observations[0].lines))
        self.assertIn("관계 분석 결과를 별도 인사이트로 발송", "\n".join(observations[0].lines))
        self.assertTrue(current.metadata["ontology"]["inferenceMissingState"]["pending"])

    def test_only_outboxed_raw_observations_enter_the_prompt_followup_lane(self):
        events = [
            AlertEvent(
                "main", "메인", "WATCH", MARKET_OBSERVATION, "main:aapl", "AAPL", [],
                symbol="AAPL", metadata={"observationOnly": True},
            ),
            AlertEvent(
                "main", "메인", "WATCH", MARKET_OBSERVATION, "main:msft", "MSFT", [],
                symbol="MSFT", metadata={"observationOnly": False},
            ),
            AlertEvent(
                "other", "기타", "WATCH", MARKET_OBSERVATION, "other:nvda", "NVDA", [],
                symbol="NVDA", metadata={"observationOnly": True},
            ),
        ]

        self.assertEqual(["AAPL"], market_observation_followup_symbols(events, "main"))

    def test_market_observation_accumulates_from_last_outbox_baseline(self):
        previous_position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "quantity": 1,
            "currentPrice": 100.4,
            "updatedAt": utc_now_iso(),
        })
        current_position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "quantity": 1,
            "currentPrice": 100.7,
            "updatedAt": utc_now_iso(),
        })
        previous = AccountSnapshot(
            "main", "메인", "toss", "live", "ok", utc_now_iso(),
            portfolio_summary([previous_position]), [previous_position], [],
            metadata={
                "marketObservationBaselines": {
                    "AAPL": {"price": 100.0, "currency": "USD"},
                },
            },
        )
        current = AccountSnapshot(
            "main", "메인", "toss", "live", "ok", utc_now_iso(),
            portfolio_summary([current_position]), [current_position], [], metadata={},
        )

        observations = [
            event for event in RealtimeMonitor({"alertThresholds": "marketObservationPriceChangePct=0.6"}).events_for_snapshot(
                current,
                previous.to_monitor_state(),
            )
            if event.rule == MARKET_OBSERVATION
        ]

        self.assertEqual(1, len(observations))
        observation = observations[0]
        self.assertEqual("last-outbox-alert", observation.metadata["marketObservation"]["baselineKind"])
        self.assertEqual(100.0, observation.metadata["marketObservation"]["baselinePrice"])
        self.assertAlmostEqual(0.7, observation.metadata["marketObservation"]["changePct"])
        self.assertIn("마지막 알림 기준값", "\n".join(observation.lines))

        updated_state = apply_market_observation_outbox_baselines(current.to_monitor_state(), observations)
        baseline = updated_state["metadata"]["marketObservationBaselines"]["AAPL"]
        self.assertEqual(100.7, baseline["price"])
        self.assertEqual("USD", baseline["currency"])

    def test_market_observation_bootstraps_a_cumulative_baseline_without_an_alert(self):
        first_position = normalize_position({
            "symbol": "AAPL", "name": "Apple", "market": "US", "currency": "USD",
            "quantity": 1, "currentPrice": 100.0, "updatedAt": utc_now_iso(),
        })
        intermediate_position = normalize_position({
            "symbol": "AAPL", "name": "Apple", "market": "US", "currency": "USD",
            "quantity": 1, "currentPrice": 100.4, "updatedAt": utc_now_iso(),
        })
        current_position = normalize_position({
            "symbol": "AAPL", "name": "Apple", "market": "US", "currency": "USD",
            "quantity": 1, "currentPrice": 100.7, "updatedAt": utc_now_iso(),
        })
        first = AccountSnapshot(
            "main", "메인", "toss", "live", "ok", utc_now_iso(),
            portfolio_summary([first_position]), [first_position], [], metadata={},
        )
        intermediate = AccountSnapshot(
            "main", "메인", "toss", "live", "ok", utc_now_iso(),
            portfolio_summary([intermediate_position]), [intermediate_position], [], metadata={},
        )
        current = AccountSnapshot(
            "main", "메인", "toss", "live", "ok", utc_now_iso(),
            portfolio_summary([current_position]), [current_position], [], metadata={},
        )

        intermediate_state = snapshot_state_for_persistence(intermediate, first.to_monitor_state())
        self.assertEqual(100.0, intermediate_state["metadata"]["marketObservationBaselines"]["AAPL"]["price"])

        observations = [
            event for event in RealtimeMonitor({"alertThresholds": "marketObservationPriceChangePct=0.6"}).events_for_snapshot(
                current,
                intermediate_state,
            )
            if event.rule == MARKET_OBSERVATION
        ]
        self.assertEqual(1, len(observations))
        self.assertAlmostEqual(0.7, observations[0].metadata["marketObservation"]["changePct"])

    def test_market_observation_defaults_require_a_material_move_and_longer_cadence(self):
        monitor = RealtimeMonitor()

        self.assertEqual(2.0, monitor.market_observation_price_change_threshold())
        self.assertEqual(60, monitor.rule_cadence_minutes(MARKET_OBSERVATION))

    def test_raw_observation_followup_builds_an_insight_after_graph_analysis(self):
        watchlist = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "currentPrice": 210,
            "updatedAt": utc_now_iso(),
            "source": "watchlist",
        })
        snapshot = AccountSnapshot(
            "main", "메인", "toss", "live", "ok", utc_now_iso(),
            portfolio_summary([]), [], [], watchlist=[watchlist], metadata={},
        )
        relation_context = {
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "reviewLevel": "normal",
            "dataState": "sufficient",
            "changeState": "unchanged",
            "conflictState": "context-only",
            "decision": {
                "basis": "typedbInferenceBox",
                "label": "관심 유지",
                "reviewLevel": "normal",
                "dataState": "sufficient",
                "changeState": "unchanged",
            },
            "activeRules": [{"ruleId": "graph.watchlist.trend.observe.v1", "label": "추세 관찰"}],
        }

        with patch("digital_twin.domain.monitoring.relation_contexts_from_snapshot", return_value={"AAPL": relation_context}):
            events = RealtimeMonitor().events_for_snapshot(
                snapshot,
                {},
                reasoning_context={
                    "observationFollowupSymbols": ["AAPL"],
                    "sourceEventIds": ["market-observation-source"],
                },
            )

        insights = [event for event in events if event.rule == INVESTMENT_INSIGHT]
        self.assertEqual(1, len(insights))
        insight = insights[0]
        self.assertEqual([ONTOLOGY_OBSERVATION_FOLLOWUP], insight.metadata["sourceSignalTypes"])
        self.assertTrue(insight.metadata["ontologyInsight"]["observationFollowup"])
        self.assertIn("TypeDB 관계 분석이 완료", "\n".join(insight.lines))

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
