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


class PriceContextAssertions:
    def assert_minute_extremes_survive_retracement_without_inventing_ticks(self):
        from digital_twin.modules.market_data.domain.price_observation_windows import observed_range
        state = {}
        for minute, price in ((0, 100), (.25, 110), (.5, 90), (.75, 101)):
            state, _ = cycle(state, minute, price)
        point = state['metadata'][PATHS]['TEST']['points'][-1]
        band = point['minuteRange']
        self.assertEqual((101, 110, 90, 4), (point['price'], band['high'], band['low'], band['observationCount']))
        self.assertEqual('2026-10-15T01:00:15Z', band['highAt'])
        self.assertEqual('2026-10-15T01:00:30Z', band['lowAt'])
        # The price at the anchor remains the denominator; earlier extremes are excluded.
        anchored = observed_range([point], point['at'], point['at'])
        self.assertEqual((101, 101), (anchored['high'], anchored['low']))
        repeated, events = cycle(state, .75, 101)
        self.assertFalse(events)
        self.assertEqual([point], repeated['metadata'][PATHS]['TEST']['points'])
        revision = advance_paths(snapshot(.75, 102).to_monitor_state(), state)['TEST']
        self.assertEqual('source-revision-without-new-clock', revision['status'])
        self.assertEqual([point], revision['points'])
        for minute, price in ((1, 101), (1.25, 110), (1.5, 90), (1.75, 101), (2.75, 101), (3.75, 103)):
            state, events = cycle(state, minute, price)
        window = price_windows(state['metadata'][PATHS]['TEST'])[0]
        self.assertAlmostEqual((103 / 101 - 1) * 100, window['changePct'])
        self.assertEqual((110, 90), (window['observedRange']['high'], window['observedRange']['low']))
        self.assertEqual('2026-10-15T01:01:30Z', window['observedRange']['lowAt'])
        self.assertIn('고가 110원', '\n'.join(events[0].lines))
        gap = advance_paths(snapshot(8, 102).to_monitor_state(), state)['TEST']
        self.assertEqual(1, len(gap['points']))
        self.assertEqual(102, gap['points'][0]['minuteRange']['low'])
        for minute in range(4, 16):
            state, events = cycle(state, minute + .75, 106 if minute == 15 else 101)
        prior = events[0].metadata['priceContext']['priorRange']
        self.assertEqual((110, 90, 'inside'), (prior['high'], prior['low'], prior['break']))
        self.assertEqual(840, prior['elapsedSeconds'])

    def assert_repeated_reference_merges_preserve_actual_price_proof(self):
        from digital_twin.infrastructure.kis_market_signals import KISMarketSignalProvider
        from digital_twin.modules.market_data.domain.price_observation_windows import source_point
        provider = object.__new__(KISMarketSignalProvider)
        selected = snapshot(0, 100).positions[0]
        reference = {"currentPrice": 90, "dataQuality": "mixed", "marketSignalCoverage": {
            "price": {"freshnessStatus": "last-close"}}}
        for _ in range(4):
            selected = provider.merge_position(selected, reference)
            self.assertEqual(100, selected.current_price)
            self.assertEqual("available", source_point(selected.to_dict(), snapshot(0, 100).generated_at)[1])
            self.assertEqual("actual", selected.market_signal_coverage["selectedPrice"]["dataQuality"])
        selected.market_signal_coverage["selectedPrice"]["values"]["currentPrice"] = 99
        selected = provider.merge_position(selected, reference)
        self.assertIsNone(source_point(selected.to_dict(), snapshot(0, 100).generated_at)[0])

    def assert_invalid_first_socket_tick_preserves_account_quote_and_proven_cache(self):
        from digital_twin.infrastructure.kis_market_signals import KISMarketSignalProvider
        from digital_twin.infrastructure.kis_realtime_ws import merge_realtime_signal, KIS_TR_CCN_PRICE
        provider = object.__new__(KISMarketSignalProvider)
        position = snapshot(0, 100).positions[0]
        position.symbol = "005930"
        for stamp in ("", "2026-10-15T02:00:00Z"):
            signal = merge_realtime_signal({}, {"symbol": position.symbol, "currentPrice": 105,
                "wireFieldCount": 46, "priceSourceAsOf": stamp}, "ccnl", KIS_TR_CCN_PRICE, "2026-10-15T01:00:05Z")
            merged = provider.merge_position(position, signal)
            self.assertEqual((100, "provider", position.source_as_of),
                             (merged.current_price, merged.source_timestamp_state, merged.source_as_of))
        verified = merge_realtime_signal({}, {"symbol": position.symbol, "currentPrice": 101,
            "wireFieldCount": 46, "priceSourceAsOf": "2026-10-15T01:00:04Z"}, "ccnl", KIS_TR_CCN_PRICE, "2026-10-15T01:00:05Z")
        self.assertEqual(101, provider.merge_position(position, verified).current_price)
        mixed = {**verified, "sourceTimestampState": "queried-at-fallback"}
        bad = merge_realtime_signal(mixed, {"symbol": position.symbol, "currentPrice": 110},
                                    "ccnl", KIS_TR_CCN_PRICE, "2026-10-15T01:00:06Z")
        self.assertEqual(mixed, bad)

    def assert_session_context_uses_matching_provider_facts_and_multiplicative_gaps(self):
        from digital_twin.modules.market_data.domain.price_observation_context import advance_price_context
        from digital_twin.modules.notifications.domain.price_movement_presentation import price_context_lines
        snap = snapshot(0, 105, previous_close=100)
        item = snap.positions[0]
        item.market_signal_coverage = {"ccnl": {"dataQuality": "actual", "sourceTimestampState": "provider",
            "sourceAsOf": snap.generated_at, "marketSession": "regular",
            "values": {"symbol": "TEST", "currentPrice": 105, "changeRate": 5}, "sessionPrices": {"open": 104, "high": 108, "low": 102}}}
        context_state, facts = advance_price_context(snap.to_monitor_state(), {}, {})
        daily = facts["TEST"]["daily"]
        self.assertEqual("provider-session", daily["basis"])
        self.assertAlmostEqual(4, daily["gapPct"])
        self.assertAlmostEqual(1.05, (1 + daily["gapPct"] / 100) * (1 + daily["sinceOpenPct"] / 100))
        self.assertIn("개장 갭 +4.00%", '\n'.join(price_context_lines(facts["TEST"], "KRW")))
        for rate in (None, 2):
            inconsistent = deepcopy(snap)
            inconsistent.positions[0].market_signal_coverage['ccnl']['values']['changeRate'] = rate
            _, limited = advance_price_context(inconsistent.to_monitor_state(), {}, {})
            self.assertIn('sinceOpenPct', limited['TEST']['daily'])
            self.assertNotIn('gapPct', limited['TEST']['daily'])
        prior = snap.to_monitor_state()
        prior['metadata']['marketPriceContext'] = context_state
        newer = deepcopy(snap)
        newer.generated_at = '2026-10-15T01:01:00Z'
        newer.positions[0].source_as_of = newer.generated_at
        newer.positions[0].market_signal_coverage['ccnl']['sourceAsOf'] = newer.generated_at
        newer.positions[0].market_signal_coverage['ccnl']['sessionPrices']['high'] = 109
        newer.positions[0].market_signal_coverage['ccnl']['sessionPrices']['low'] = 101
        _, later = advance_price_context(newer.to_monitor_state(), prior, {})
        self.assertTrue(later['TEST']['daily']['newHigh'])
        self.assertTrue(later['TEST']['daily']['newLow'])
        self.assertIn('고가·저가 갱신', '\n'.join(price_context_lines(later['TEST'], 'KRW')))
        self.assertIn('저가 대비', '\n'.join(price_context_lines(later['TEST'], 'KRW')))
        self.assertEqual(['정규장 고가·저가 갱신'], price_context_lines(later['TEST'], 'KRW', compact=True))
        self.assertEqual(105, later['TEST']['currentPrice'])
        for field, value in (("sourceAsOf", "2026-10-14T01:00:00Z"), ("values", {"symbol": "OTHER", "currentPrice": 105})):
            altered = deepcopy(snap)
            altered.positions[0].market_signal_coverage["ccnl"][field] = value
            _, facts = advance_price_context(altered.to_monitor_state(), {}, {})
            self.assertEqual("observed-session", facts["TEST"]["daily"]["basis"])
            self.assertNotIn("gapPct", facts["TEST"]["daily"])

    def assert_partial_day_range_and_new_day_never_invent_open_or_full_day_extrema(self):
        from digital_twin.modules.market_data.domain.price_observation_context import advance_price_context, STATE_KEY as CONTEXT
        first = snapshot(0, 100).to_monitor_state()
        state, _ = advance_price_context(first, {}, {})
        first["metadata"][CONTEXT] = state
        second = snapshot(1, 105).to_monitor_state()
        after, facts = advance_price_context(second, first, {})
        self.assertEqual(100, facts["TEST"]["daily"]["low"])
        self.assertTrue(facts["TEST"]["daily"]["newHigh"])
        self.assertNotIn("open", facts["TEST"]["daily"])
        second["metadata"][CONTEXT] = after
        _, facts = advance_price_context(snapshot(1440, 90).to_monitor_state(), second, {})
        self.assertEqual(90, facts["TEST"]["daily"]["high"])
        self.assertNotIn("newLow", facts["TEST"]["daily"])
        other = snapshot(2, 80).to_monitor_state()
        other["accountId"] = "different"
        _, facts = advance_price_context(other, second, {})
        self.assertEqual(80, facts["TEST"]["daily"]["high"])

    def assert_historical_comparison_excludes_overlap_future_wrong_length_and_warmup(self):
        from digital_twin.modules.market_data.domain.price_observation_context import window_context, local_time_band
        from zoneinfo import ZoneInfo
        state = {}
        for minute in range(30, 46):
            state, events = cycle(state, minute, 100 if minute < 45 else 104)
        path = state["metadata"][PATHS]["TEST"]
        current = snapshot(45, 104)
        history = []
        for index in range(24):
            clock = START + timedelta(minutes=45) - timedelta(days=1 + index)
            history.append([clock.isoformat(), 900.0, 1.0, clock.strftime('%Y-%m-%d') + ':10', 'regular'])
        history.sort()
        history.extend([[(START + timedelta(minutes=30)).isoformat(), 900, 500, '2026-10-15:10', 'regular'],
                        [(START + timedelta(days=1)).isoformat(), 900, 500, '2026-10-16:10', 'regular'],
                        [(START - timedelta(days=1)).isoformat(), 1000, 500, '2026-10-14:10', 'regular'],
                        [(START - timedelta(days=1, hours=4)).isoformat(), 900, 500, '2026-10-14:6', 'regular']])
        before = deepcopy(history)
        _, comparisons = window_context(path, {"15": history}, current.positions[0].to_dict())
        comparison = next(row for row in comparisons if row['windowMinutes'] == 15)
        # A sample ending at this interval's start is disjoint and may be used.
        self.assertEqual(25, comparison['sampleCount'])
        self.assertEqual("available", comparison['status'])
        self.assertEqual('10:00–12:00', comparison['localTimeBand'])
        self.assertEqual('Asia/Seoul', comparison['timeZone'])
        self.assertAlmostEqual(4, comparison['medianMultiple'])
        self.assertEqual(before, history)
        _, comparisons = window_context(path, {"15": history[:10]}, current.positions[0].to_dict())
        self.assertEqual('insufficient-history', next(row for row in comparisons if row['windowMinutes'] == 15)['status'])
        # Wall-clock bands stay stable across the US daylight-saving transition.
        for clock in ('2026-10-15T14:45:00Z', '2026-11-05T15:45:00Z'):
            self.assertEqual('10:00–12:00', local_time_band(clock, 900, ZoneInfo('America/New_York')))

    def assert_new_context_is_frozen_in_price_event_and_persisted_with_diagnostics(self):
        state = {}
        for minute in range(16):
            state, events = cycle(state, minute, 100 if minute < 15 else 104)
        event = events[0]
        context = alert_context(event)
        self.assertEqual('above', context['priceContext']['priorRange']['break'])
        self.assertIn('금일 관측 구간', '\n'.join(event.lines))
        self.assertIn('자료 축적 중', '\n'.join(event.lines))
        self.assertEqual(16, state['metadata']['marketPriceDiagnostics']['evaluationCounts']['available'])
        repeated = persist_price_state(snapshot(15, 104).to_monitor_state(), state, SETTINGS)
        self.assertEqual(state['metadata']['marketPriceDiagnostics'], repeated['metadata']['marketPriceDiagnostics'])
        missing = snapshot(16, 110, source_timestamp_state='websocket-received')
        rejected = persist_price_state(missing.to_monitor_state(), state, SETTINGS)
        self.assertEqual(1, rejected['metadata']['marketPriceDiagnostics']['evaluationCounts']['unverified-source-clock'])

    def assert_comparable_history_survives_session_changes_without_mixing_sessions(self):
        from digital_twin.modules.market_data.domain.price_observation_context import (
            advance_price_context, window_context, STATE_KEY as CONTEXT)
        previous = snapshot(0, 100).to_monitor_state()
        states, _ = advance_price_context(previous, {}, {})
        history = []
        for index in range(24):
            clock = START + timedelta(minutes=30) - timedelta(days=1 + index)
            history.append([clock.isoformat(), 900 + (index % 3 - 1) * 30, 1,
                            clock.date().isoformat() + ':' + str(clock.hour), 'regular'])
        history.append([(START - timedelta(days=4)).isoformat(), 900, 500, '2026-10-11:1', 'after-hours'])
        history.sort()
        states['TEST']['samples'] = {'15': history}
        previous['metadata'][CONTEXT] = states
        extended = snapshot(1, 200, market_session='after-hours').to_monitor_state()
        states, _ = advance_price_context(extended, previous, {})
        extended['metadata'][CONTEXT] = states
        regular = snapshot(2, 100).to_monitor_state()
        states, facts = advance_price_context(regular, extended, {})
        self.assertEqual(history, states['TEST']['samples']['15'])
        self.assertEqual(100, facts['TEST']['daily']['high'])
        path = {'status': 'available', 'points': [{'at': (START + timedelta(minutes=m)).isoformat(),
                 'price': 100 if m < 15 else 104} for m in range(16)]}
        _, comparisons = window_context(path, states['TEST']['samples'], snapshot(15, 104).positions[0].to_dict())
        result = next(row for row in comparisons if row['windowMinutes'] == 15)
        self.assertEqual((24, 'available'), (result['sampleCount'], result['status']))
        self.assertEqual((870, 930), (result['historicalMinSeconds'], result['historicalMaxSeconds']))
        self.assertAlmostEqual(4, result['medianMultiple'])
        from digital_twin.modules.notifications.domain.price_movement_presentation import price_context_lines
        presentation = '\n'.join(price_context_lines({**facts['TEST'], 'comparisons': [result]}, 'KRW'))
        self.assertIn('현재 15.0분 변동 비교 (과거 14.5~15.5분)', presentation)
        self.assertNotIn('자료 축적 중', presentation)
        regular['metadata'][CONTEXT] = states
        changed = snapshot(3, 100, quote_source='other-provider').to_monitor_state()
        states, _ = advance_price_context(changed, regular, {})
        self.assertEqual({}, states['TEST']['samples'])

    def assert_context_history_is_bounded_and_not_projected_into_ai_input(self):
        from digital_twin.modules.market_data.domain.price_observation_context import advance_price_context, STATE_KEY as CONTEXT, VERSION, MAX_SAMPLES
        from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_monitor_runtime_metadata_for_ontology
        snap = snapshot(0, 100)
        rows = [[(START - timedelta(minutes=index + 1)).isoformat(), 180, 1, '2026-10-15:0'] for index in reversed(range(320))]
        previous = snap.to_monitor_state()
        previous['metadata'][CONTEXT] = {'TEST': {'version': VERSION,
            'scope': ['KRW', 'test-source', 'regular', 'adjusted'], 'samples': {'3': rows},
            'lastAt': (START - timedelta(minutes=1)).isoformat()}}
        states, _ = advance_price_context(snap.to_monitor_state(), previous, {})
        self.assertEqual(MAX_SAMPLES, len(states['TEST']['samples']['3']))
        self.assertNotIn(CONTEXT, compact_monitor_runtime_metadata_for_ontology(previous['metadata']))
        future = snapshot(46 * 1440, 100).to_monitor_state()
        states, _ = advance_price_context(future, previous, {})
        self.assertEqual([], states['TEST']['samples']['3'])

    def assert_diagnostic_summary_keeps_observation_dates_and_excludes_account_identifiers(self):
        from digital_twin.modules.market_data.domain.price_observation_diagnostics import price_observation_diagnostics
        state, _ = cycle({}, 0, 100)
        result = price_observation_diagnostics([state])
        self.assertEqual({'available': 1}, result['days'][0]['evaluationCounts'])
        self.assertEqual('2026-10-15', result['days'][0]['dayKst'])
        self.assertNotIn('price-test', str(result))
        previous_day = snapshot(-1440, 100).to_monitor_state()
        stale = persist_price_state(previous_day, state, SETTINGS)
        self.assertEqual(state['metadata']['marketPriceDiagnostics'], stale['metadata']['marketPriceDiagnostics'])


class SmartPriceObservationTests(PriceContextAssertions, unittest.TestCase):
    def test_source_clock_gap_and_reference_are_not_fast_moves(self):
        with self.subTest(price_context="repeated_reference_merges_preserve_actual_price_proof"):
            self.assert_repeated_reference_merges_preserve_actual_price_proof()
        with self.subTest(price_context="invalid_first_socket_tick_preserves_account_quote_and_proven_cache"):
            self.assert_invalid_first_socket_tick_preserves_account_quote_and_proven_cache()
        state, _ = cycle({}, 0, 100)
        for change in ({"source_as_of": ""}, {"source_timestamp_state": "queried-at-fallback"},
                       {"freshness_status": "last-close"}, {"data_quality": "reference"},
                       {"source_as_of": "2026-10-15T01:20:00Z"}):
            snap = snapshot(3, 95, **change)
            paths = advance_paths(snap.to_monitor_state(), state)
            self.assertEqual([], price_windows(paths["TEST"]))
        from digital_twin.modules.market_data.domain.market_observations import (
            apply_market_observation_outbox_baselines, hydrate_market_observation_baselines,
            market_observation_delivery_admission, QUOTE_BASELINE_VERSION)
        monitor = RealtimeMonitor({**SETTINGS, "marketObservationSmartEnabled": "0"})
        for role, start, reference, final in (("holding", 16430, 18020, 16400),
                                              ("watchlist", 152800, 160000, 152700)):
            with self.subTest(role=role):
                original = snapshot_state_for_persistence(snapshot(0, start, role), {}, SETTINGS)
                bad = snapshot(12, reference, role, source_timestamp_state="queried-at-fallback",
                               freshness_status="last-close", data_quality="mixed", quote_source="KIS")
                self.assertEqual([], monitor.market_observation_events(bad, original))
                saved = snapshot_state_for_persistence(bad, original, SETTINGS)
                self.assertEqual(start, saved["metadata"]["marketObservationBaselines"]["TEST"]["initialPrice"])
                live = snapshot(22, final, role, source_timestamp_state="provider-execution",
                                freshness_status="realtime", quote_source="KIS WebSocket")
                self.assertEqual([], monitor.market_observation_events(live, saved))
                # Real cumulative movement still works without a short path.
                moved = snapshot(25, start * 1.06, role)
                events = monitor.market_observation_events(moved, saved)
                self.assertEqual(1, len(events))
                self.assertTrue(market_observation_delivery_admission(events[0], saved)["accepted"])
                advanced = apply_market_observation_outbox_baselines(saved, events)
                self.assertEqual(start * 1.06, advanced["metadata"]["marketObservationBaselines"]["TEST"]["outboxPrice"])
                events[0].metadata["marketObservation"]["quoteWitness"]["source_timestamp_state"] = "queried-at-fallback"
                self.assertFalse(market_observation_delivery_admission(events[0], saved)["accepted"])
                self.assertEqual(saved, apply_market_observation_outbox_baselines(saved, events))
                legacy = deepcopy(saved)
                legacy["metadata"]["marketObservationBaselines"]["TEST"] = {"price": reference, "outboxPrice": reference}
                self.assertEqual([], monitor.market_observation_events(live, legacy))
                migrated = hydrate_market_observation_baselines(live.to_monitor_state(), legacy)
                anchor = migrated["metadata"]["marketObservationBaselines"]["TEST"]
                self.assertEqual(final, anchor["initialPrice"])
                self.assertNotIn("outboxPrice", anchor)
                self.assertEqual(QUOTE_BASELINE_VERSION, anchor["quoteBaselineVersion"])
                self.assertEqual("legacy-quote-unverified", anchor["deliveryAnchorResetReason"])
                # A stale snapshot cannot reset a concurrently committed anchor.
                slower = live.to_monitor_state()
                slower["metadata"] = deepcopy(legacy["metadata"])
                concurrent = hydrate_market_observation_baselines(slower, advanced)
                self.assertEqual(start * 1.06, concurrent["metadata"]["marketObservationBaselines"]["TEST"]["outboxPrice"])
                changed_account = live.to_monitor_state()
                changed_account["accountId"] = "other-fixture"
                separate = hydrate_market_observation_baselines(changed_account, advanced)
                self.assertEqual(final, separate["metadata"]["marketObservationBaselines"]["TEST"]["initialPrice"])
                self.assertNotIn("outboxPrice", separate["metadata"]["marketObservationBaselines"]["TEST"])

        execution_state = {}
        for minute, price in [(0, 100), (1, 100), (2, 99.5), (3, 98)]:
            execution_state, execution_events = cycle(execution_state, minute, price,
                source_timestamp_state="provider-execution", freshness_status="realtime")
        self.assertEqual("initial", execution_events[0].metadata["priceMovement"]["stage"])
        state, events = cycle(state, 10, 90)
        self.assertFalse(events)
        self.assertEqual(1, len(state["metadata"][PATHS]["TEST"]["points"]))

    def test_same_quote_is_not_new_observation_or_recovery(self):
        with self.subTest(price_context="diagnostic_summary_keeps_observation_dates_and_excludes_account_identifiers"):
            self.assert_diagnostic_summary_keeps_observation_dates_and_excludes_account_identifiers()
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
        with self.subTest(price_context='minute_extremes_survive_retracement_without_inventing_ticks'):
            self.assert_minute_extremes_survive_retracement_without_inventing_ticks()
        with self.subTest(price_context="partial_day_range_and_new_day_never_invent_open_or_full_day_extrema"):
            self.assert_partial_day_range_and_new_day_never_invent_open_or_full_day_extrema()
        with self.subTest(price_context="comparable_history_survives_session_changes_without_mixing_sessions"):
            self.assert_comparable_history_survives_session_changes_without_mixing_sessions()
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
        with self.subTest(price_context="historical_comparison_excludes_overlap_future_wrong_length_and_warmup"):
            self.assert_historical_comparison_excludes_overlap_future_wrong_length_and_warmup()
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
        with self.subTest(price_context="session_context_uses_matching_provider_facts_and_multiplicative_gaps"):
            self.assert_session_context_uses_matching_provider_facts_and_multiplicative_gaps()
        with self.subTest(price_context="new_context_is_frozen_in_price_event_and_persisted_with_diagnostics"):
            self.assert_new_context_is_frozen_in_price_event_and_persisted_with_diagnostics()
        with self.subTest(price_context="context_history_is_bounded_and_not_projected_into_ai_input"):
            self.assert_context_history_is_bounded_and_not_projected_into_ai_input()
        state, event = falling()
        snap = snapshot(4, 96)
        persisted = snapshot_state_for_persistence(snap, state, SETTINGS)
        self.assertEqual("2026-10-15T01:04:00Z", persisted["metadata"][PATHS]["TEST"]["points"][-1]["at"])
        context = alert_context(event)
        self.assertIn("보유", context["titleHeadline"])
        for summary in ('최근 3분', '현재가', '이번 가격 변동분'):
            self.assertLess(context['telegramMessage'].index(summary), context['telegramMessage'].index('시세 시각'))
        self.assertIn("KST", "\n".join(event.lines))
        self.assertNotIn("매도 권장", "\n".join(event.lines))
        job = NotificationIngressService().job_from_alert(event)
        sources = conversation_sources(job)
        self.assertIn(("price-movement", "", event.metadata["priceMovement"]["episodeId"]), sources["sources"])
        actual = RealtimeMonitor(SETTINGS).market_observation_events(snap, state)
        self.assertEqual(1, len(actual))
        self.assertEqual("expanded", actual[0].metadata["priceMovement"]["stage"])
        self.assertTrue(event.lines[0].startswith('최근 3분:'))
        self.assertTrue(event.lines[1].startswith('현재가:'))
        self.assertTrue(event.lines[2].startswith('이번 가격 변동분:'))
        self.assertTrue(actual[0].lines[0].startswith('이번 변동 시작 대비:'))
        self.assertNotIn('자료 축적 중', '\n'.join(actual[0].lines))
        self.assertNotIn('평균매입가 미확인', '\n'.join(actual[0].lines))
        self.assertLess(len(actual[0].lines), len(event.lines))
        self.assertIn('comparisons', actual[0].metadata['priceContext'])
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
        self.assertEqual([], fallback_events)
        for mode in ("disabled", "typedb-first"):
            self.assertFalse(smart_market_observations(snapshot(4, 96), state, [], {**SETTINGS, "marketObservationRawDeliveryMode": mode}))
        marker = market_event(100, 104)
        marker.symbol = "TEST"
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

    def test_total_unrealized_loss_is_not_the_positive_quote_effect(self):
        from digital_twin.modules.market_data.domain.smart_price_observations import position_effect
        from digital_twin.modules.notifications.contracts import position_price_lines
        item = snapshot(5, 22650, quantity=17, average_price=30000).positions[0].to_dict()
        effect = position_effect(item, "holding", {}, 21950)
        self.assertEqual(11900, effect["nativeAmount"])
        self.assertEqual(-124950, effect["unrealizedNativeAmount"])
        text = "\n".join(position_price_lines(effect, "KRW", "알림 기준가 대비"))
        self.assertIn("매입가 대비 평가손익: -124,950원", text)
        self.assertIn("이번 가격 변동분: +11,900원", text)
        self.assertIn("수수료·세금 제외", text)
        self.assertNotIn(".00", text)
        self.assertNotIn("보유분 가격 효과", text)
        legacy = market_event(21950, 22650)
        legacy.symbol = "TEST"
        legacy.lines = ['현재가: 22,650원', '기준 대비: +3.19%']
        event = smart_market_observations(snapshot(5, 22650, quantity=17, average_price=30000), {}, [legacy], SETTINGS)[0]
        self.assertEqual(['기준 대비:', '현재가:', '이번 가격 변동분:'],
                         [line.split(':')[0] + ':' for line in event.lines[:3]])
        self.assertIn("매입가 대비 평가손익: -124,950원", "\n".join(event.lines))
        for average in (None, 0, -1, float("nan")):
            with self.subTest(average=average):
                missing = position_effect({**item, "average_price": average}, "holding", {}, 21950)
                self.assertNotIn("unrealizedNativeAmount", missing)
                self.assertIn("평균매입가 미확인", "\n".join(position_price_lines(missing, "KRW", "기준가 대비")))
        flat = position_effect({**item, "average_price": 22650}, "holding", {}, 22650)
        self.assertIn("평가손익: 0원", "\n".join(position_price_lines(flat, "KRW", "기준가 대비")))
        foreign = position_effect({**item, "quantity": .5, "currency": "USD", "current_price": 101.5,
                                   "average_price": 110}, "holding", {}, 100)
        usd = "\n".join(position_price_lines(foreign, "USD", "기준가 대비"))
        self.assertIn("-4.25 USD", usd)
        self.assertIn("+0.75 USD", usd)
        self.assertEqual({}, position_effect(item, "watchlist", {}, 21950))
        previous = {}
        for minute, price in [(0, 100), (1, 100), (2, 99.5), (3, 98)]:
            previous, events = cycle(previous, minute, price, average_price=110)
        text = "\n".join(events[0].lines)
        self.assertIn("평가손익: -120원", text)
        self.assertIn("이번 가격 변동분: -20원", text)
        self.assertIn("현재가: 98원", text)
        self.assertTrue(events[0].metadata["reasoningFollowupRequired"])

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
