"""Clock, account scope, transition, and delivery rehearsals without live feeds."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from digital_twin.modules.market_data.domain.price_observation_windows import advance_paths, price_windows, STATE_KEY as PATHS
from digital_twin.modules.market_data.domain.price_observation_episodes import persist_price_state, acknowledge_transitions, STATE_KEY, admit_transition
from digital_twin.modules.market_data.domain.smart_price_observations import smart_market_observations, movement_cadence
from digital_twin.modules.market_data.domain.monitoring import RealtimeMonitor
from digital_twin.modules.portfolio.domain.portfolio import Position, AccountSnapshot, PortfolioSummary
from digital_twin.modules.notifications.domain.notification_templates import alert_context
from digital_twin.modules.notifications.application.notification.intake import NotificationIngressService
from digital_twin.modules.notifications.domain.investment_conversation import conversation_sources
from digital_twin.infrastructure.transactions.monitoring import MySQLMonitoringCycleRecorder, snapshot_state_for_persistence
from test_market_observation_delivery import SentRowConnection, market_event

SETTINGS = {"marketObservationSmartEnabled": "1", "marketObservationRawDeliveryMode": "critical-only",
            "marketObservationImmediateCadenceMinutes": "10"}
START = datetime(2026, 10, 15, 1, tzinfo=timezone.utc)


def snapshot(minute, price, role="holding", **changes):
    stamp = (START + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")
    item = Position("TEST", "테스트 종목", market="KR", currency="KRW", quantity=10 if role == "holding" else 0,
                    current_price=price, source_as_of=stamp, updated_at=stamp, quote_source="test-source",
                    source_timestamp_state="provider", data_quality="actual", freshness_status="fresh",
                    market_session="regular", price_history_adjustment="adjusted")
    for key, value in changes.items():
        setattr(item, key, value)
    return AccountSnapshot("price-test", "Fixture", "fixture", "live", "ok", stamp,
        PortfolioSummary(10000, 9000, 1000, [], [], 0),
        positions=[item] if role == "holding" else [], watchlist=[item] if role == "watchlist" else [])


def cycle(previous, minute, price, role="holding", ack=True, **changes):
    snap = snapshot(minute, price, role, **changes)
    events = smart_market_observations(snap, previous, [], SETTINGS)
    state = persist_price_state(snap.to_monitor_state(), previous, SETTINGS)
    return acknowledge_transitions(state, events) if ack else state, events


def falling():
    state = {}
    for minute, price in [(0, 100), (1, 100), (2, 99.5), (3, 98)]:
        state, events = cycle(state, minute, price)
    return state, events[0]


class SmartPriceObservationTests(unittest.TestCase):
    def test_source_clock_gap_and_reference_are_not_fast_moves(self):
        state, _ = cycle({}, 0, 100)
        for change in ({"source_as_of": ""}, {"source_timestamp_state": "queried-at-fallback"},
                       {"freshness_status": "last-close"}, {"data_quality": "reference"},
                       {"source_as_of": "2026-10-15T01:20:00Z"}):
            snap = snapshot(3, 95, **change)
            paths = advance_paths(snap.to_monitor_state(), state)
            self.assertEqual([], price_windows(paths["TEST"]))
        state, events = cycle(state, 10, 90)
        self.assertFalse(events)
        self.assertEqual(1, len(state["metadata"][PATHS]["TEST"]["points"]))

    def test_same_quote_is_not_new_observation_or_recovery(self):
        state, _ = falling()
        snap = snapshot(4, 98, source_as_of="2026-10-15T01:03:00Z")
        paths = advance_paths(snap.to_monitor_state(), state)
        self.assertEqual("unchanged-source", paths["TEST"]["status"])
        self.assertFalse(smart_market_observations(snap, state, [], SETTINGS))
        snap.positions[0].current_price = 99
        self.assertEqual("source-revision-without-new-clock", advance_paths(snap.to_monitor_state(), state)["TEST"]["status"])

    def test_holding_and_watchlist_have_different_fast_thresholds(self):
        for role, expected in (("holding", 1), ("watchlist", 0)):
            state = {}
            for minute, price in [(0, 100), (1, 100), (2, 99.5), (3, 98)]:
                state, events = cycle(state, minute, price, role)
            self.assertEqual(expected, len(events))
        state, first = falling()
        self.assertEqual("initial", first.metadata["priceMovement"]["stage"])
        self.assertAlmostEqual(-20, first.metadata["priceMovement"]["positionEffect"]["nativeAmount"])
        self.assertAlmostEqual(-.2, first.metadata["priceMovement"]["positionEffect"]["accountImpactPct"])

    def test_expansion_recovery_and_rearm_are_one_episode(self):
        state, first = falling()
        episode = first.metadata["priceMovement"]["episodeId"]
        state, repeated = cycle(state, 4, 98)
        self.assertFalse(repeated)
        state, expanded = cycle(state, 5, 96)
        self.assertEqual("expanded", expanded[0].metadata["priceMovement"]["stage"])
        self.assertEqual(1, movement_cadence(expanded[0], 10))
        state, initial_rebound = cycle(state, 6, 99)
        self.assertFalse(initial_rebound)
        state, recovery = cycle(state, 8, 99)
        self.assertEqual("recovery", recovery[0].metadata["priceMovement"]["stage"])
        self.assertEqual(episode, recovery[0].metadata["priceMovement"]["episodeId"])
        state, repeated = cycle(state, 9, 99)
        self.assertFalse(repeated)
        state, _ = cycle(state, 10, 99)
        state, _ = cycle(state, 11, 98)
        state, next_episode = cycle(state, 12, 96)
        self.assertTrue(next_episode)
        self.assertNotEqual(episode, next_episode[0].metadata["priceMovement"]["episodeId"])

    def test_outbox_ack_and_concurrent_worker_guard(self):
        previous = {}
        for m in range(3):
            previous, _ = cycle(previous, m, 100)
        state, events = cycle(previous, 3, 98, ack=False)
        event = events[0]
        self.assertTrue(admit_transition(event, previous)["accepted"])
        self.assertEqual(0, state["metadata"][STATE_KEY]["TEST"]["lastQueuedSequence"])
        acknowledged = acknowledge_transitions(state, events)
        self.assertFalse(admit_transition(event, acknowledged)["accepted"])
        newer_quiet, _ = cycle(previous, 4, 100)
        self.assertFalse(newer_quiet["metadata"][STATE_KEY]["TEST"])
        self.assertFalse(admit_transition(event, newer_quiet)["accepted"])
        _, retry = cycle(state, 4, 98)
        self.assertEqual(event.key, retry[0].key)

    def test_transaction_guard_allows_expansion_but_not_duplicate(self):
        state, first = falling()
        snap = snapshot(4, 96)
        event = smart_market_observations(snap, state, [], SETTINGS)[0]
        recorder = object.__new__(MySQLMonitoringCycleRecorder)
        recorder.runtime_settings = SETTINGS
        connection = SentRowConnection({"sent_key": event.cadence_key(), "sent_at": "2026-10-15T01:03:00Z"})
        accepted, _ = recorder.guard_market_observation_delivery_with_connection(connection, [event],
            {"price-test": state}, now=START + timedelta(minutes=4))
        self.assertEqual([event], accepted)
        accepted, _ = recorder.guard_market_observation_delivery_with_connection(connection, [event],
            {"price-test": state}, now=START + timedelta(minutes=3, seconds=30))
        self.assertFalse(accepted)

    def test_paths_are_scoped_bounded_and_preserve_real_duration(self):
        state = {}
        for m in range(0, 100, 2):
            state, _ = cycle(state, m, 100)
        rows = state["metadata"][PATHS]["TEST"]["points"]
        self.assertLessEqual(len(rows), 39)
        path = advance_paths(snapshot(100, 98).to_monitor_state(), state)["TEST"]
        windows = price_windows(path)
        self.assertEqual([3, 15, 60], [w["windowMinutes"] for w in windows])
        self.assertEqual(240, windows[0]["elapsedSeconds"])
        self.assertAlmostEqual(-2, windows[0]["changePct"])
        for change in ({"currency": "USD"}, {"quote_source": "other"}, {"market_session": "after-hours"}):
            reset = advance_paths(snapshot(100, 95, **change).to_monitor_state(), state)["TEST"]
            self.assertFalse(price_windows(reset))
        foreign = deepcopy(state)
        foreign["accountId"] = "another-account"
        self.assertFalse(price_windows(advance_paths(snapshot(100, 90).to_monitor_state(), foreign)["TEST"]))
        reset = advance_paths(snapshot(100, 50, previous_close=50).to_monitor_state(), state)["TEST"]
        self.assertFalse(price_windows(reset))

    def test_hour_window_and_incomplete_windows(self):
        state = {}
        for m in range(60):
            state, _ = cycle(state, m, 100 - 4.1 * m / 60)
        path = advance_paths(snapshot(60, 95.9).to_monitor_state(), state)["TEST"]
        rows = price_windows(path)
        self.assertAlmostEqual(-4.1, rows[-1]["changePct"])
        self.assertEqual(60, rows[-1]["windowMinutes"])
        _, events = cycle(state, 60, 95.9)
        self.assertEqual(60, events[0].metadata["priceMovement"]["trigger"]["windowMinutes"])

    def test_persistence_wiring_and_presentation(self):
        state, event = falling()
        snap = snapshot(4, 96)
        persisted = snapshot_state_for_persistence(snap, state, SETTINGS)
        self.assertEqual("2026-10-15T01:04:00Z", persisted["metadata"][PATHS]["TEST"]["points"][-1]["at"])
        context = alert_context(event)
        self.assertIn("보유", context["titleHeadline"])
        self.assertIn("KST", "\n".join(event.lines))
        self.assertNotIn("매도 권장", "\n".join(event.lines))
        job = NotificationIngressService().job_from_alert(event)
        sources = conversation_sources(job)
        self.assertIn(("price-movement", "", event.metadata["priceMovement"]["episodeId"]), sources["sources"])
        actual = RealtimeMonitor(SETTINGS).market_observation_events(snap, state)
        self.assertEqual(1, len(actual))
        self.assertEqual("expanded", actual[0].metadata["priceMovement"]["stage"])
        self.assertEqual(1, RealtimeMonitor(SETTINGS).dispatch_cadence_minutes(actual[0]))

    def test_cumulative_watchlist_threshold_and_modes_preserve_legacy(self):
        snap = snapshot(5, 104, "watchlist")
        legacy = market_event(100, 104)
        legacy.symbol = "TEST"
        events = smart_market_observations(snap, {}, [legacy], SETTINGS)
        self.assertTrue(events[0].metadata["deliveryDeferred"])
        self.assertEqual(4.5, events[0].metadata["marketObservation"]["deliveryThresholdPct"])
        state, _ = falling()
        fallback = market_event(100, 94)
        fallback.symbol = "TEST"
        unavailable = snapshot(4, 94, source_timestamp_state="queried-at-fallback")
        fallback_events = smart_market_observations(unavailable, state, [fallback], SETTINGS)
        self.assertEqual([fallback], fallback_events)
        self.assertFalse(fallback_events[0].metadata.get("deliveryDeferred"))
        for mode in ("disabled", "typedb-first"):
            self.assertFalse(smart_market_observations(snapshot(4, 96), state, [], {**SETTINGS, "marketObservationRawDeliveryMode": mode}))
        marker = market_event()
        self.assertEqual([marker], smart_market_observations(snap, {}, [marker], {}))

    def test_duplicate_watchlist_holding_wins_and_fx_unknown_is_not_guessed(self):
        state = {}
        for m in range(3):
            state, _ = cycle(state, m, 100)
        snap = snapshot(3, 98)
        snap.watchlist = deepcopy(snap.positions)
        events = smart_market_observations(snap, state, [], SETTINGS)
        self.assertEqual(1, len(events))
        self.assertEqual("holding", events[0].metadata["priceMovement"]["role"])
        from digital_twin.modules.market_data.domain.smart_price_observations import position_effect
        item = snapshot(3, 98, currency="USD", exchange_rate=1300).positions[0].to_dict()
        effect = position_effect(item, "holding", {"total": 10000}, 100)
        self.assertEqual(-20, effect["nativeAmount"])
        self.assertNotIn("accountImpactPct", effect)

    def test_rapid_opposite_move_is_not_hidden_by_an_open_episode(self):
        state = {}
        for minute, price in ((0, 100), (1, 100), (2, 100), (3, 101.5)):
            state, events = cycle(state, minute, price)
        self.assertEqual("up", events[0].metadata["priceMovement"]["direction"])
        original = events[0].metadata["priceMovement"]["episodeId"]
        _, reversal = cycle(state, 4, 98)
        movement = reversal[0].metadata["priceMovement"]
        self.assertEqual("reversed", movement["stage"])
        self.assertEqual("down", movement["direction"])
        self.assertEqual(original, movement["reversalOf"])
        self.assertEqual(1, movement_cadence(reversal[0], 10))
        self.assertTrue(admit_transition(reversal[0], state)["accepted"])


if __name__ == "__main__":
    unittest.main()
