"""Volume provenance, intraday arithmetic and receipt-backed notification history."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from digital_twin.modules.market_data.contracts import position_volume_pace_snapshot, volume_pace_snapshot
from digital_twin.modules.market_data.domain.market_data import normalize_position, technical_indicators_from_candles
from digital_twin.modules.notifications.domain.observation_market_snapshot import execution_rows, volume_pace_rows
from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
from digital_twin.modules.notifications.application.typedb_observation_message import _follow_up_rows, typedb_observation_telegram_message
from digital_twin.modules.outcomes.domain.follow_up_tracking import registered_follow_up, follow_up_source_time
from digital_twin.infrastructure.toss_snapshots import TossProvider
from digital_twin.infrastructure.kis_realtime_ws import normalize_ws_ccnl, merge_realtime_signal, KIS_TR_CCN_PRICE
from test_typedb_independent_observation import readable_fixture
from test_ai_follow_up_tracking import condition, START


AT = "2026-09-14T12:15:00+09:00"


def position_fixture():
    return normalize_position({"symbol": "000680", "market": "KR", "currency": "KRW", "currentPrice": 3000,
        "volume": 5500, "volumeRatio": .55, "updatedAt": "2026-09-14T06:00:00Z",
        "marketSignalCoverage": {"volume": {
            "values": {"volumeRatio": .55}, "observedFields": ["volumeRatio"], "status": "available",
            "sourceAsOf": AT, "fetchedAt": "2026-09-14T03:15:01Z", "sourceTimestampState": "provider-execution",
            "provider": "KIS", "marketSession": "regular", "ratioBasis": "previous-session-total",
            "measurementScope": "session-cumulative", "numeratorVolume": 5500,
        }}})


def snapshot(facts, ccnl=None):
    return {"market": "KR", "facts": [{"id": key, "value": value} for key, value in facts.items()],
            "marketSignalCoverage": {"ccnl": ccnl or {}}}


class RelationMarketTrackingTests(unittest.TestCase):
    def test_intraday_arithmetic_is_source_clocked_with_korean_and_us_dst_sessions(self):
        common = dict(measurement_scope="session-cumulative", ratio_basis="previous-session-total", source_session="regular")
        for market, at in (("KR", AT), ("US", "2026-07-06T16:45:00Z"), ("US", "2026-01-05T17:45:00Z")):
            with self.subTest(market=market, at=at):
                pace = volume_pace_snapshot(market, .55, observed_at=at, **common)
                self.assertEqual(.55, pace["expectedVolumeRatioNow"])
                self.assertEqual(50, pace["volumePaceElapsedPct"])
                self.assertEqual(1, pace["timeAdjustedVolumeRatio"])
                self.assertEqual(pace, volume_pace_snapshot(market, .55, observed_at=at, now=datetime(2030, 1, 1, tzinfo=timezone.utc), **common))
        for clock in ("", "bad", "2026-09-14T12:15:00", datetime(2026, 9, 14, 12, 15)):
            self.assertNotIn("timeAdjustedVolumeRatio", volume_pace_snapshot("KR", .55, observed_at=clock,
                now=datetime(2026, 9, 14, 3, 15, tzinfo=timezone.utc), **common))

    def test_volume_correction_rejects_extended_closed_unknown_scope_and_invalid_inputs(self):
        common = dict(market="KR", raw_volume_ratio=.55, observed_at=AT, measurement_scope="session-cumulative",
                      ratio_basis="previous-session-total", source_session="regular")
        changes = [{"observed_at": at} for at in ("2026-09-14T09:00:00+09:00", "2026-09-14T08:20:00+09:00",
                    "2026-09-14T19:55:00+09:00", "2026-09-13T12:15:00+09:00")]
        changes += [{"measurement_scope": "daily-candle"}, {"ratio_basis": ""}, {"source_session": "closed"}]
        changes += [{"raw_volume_ratio": value} for value in (-1, float("inf"), float("nan"), "bad", True, None)]
        for change in changes:
            with self.subTest(change=change):
                pace = volume_pace_snapshot(**{**common, **change})
                self.assertNotIn("timeAdjustedVolumeRatio", pace)
        self.assertEqual(0, volume_pace_snapshot(**{**common, "raw_volume_ratio": 0})["timeAdjustedVolumeRatio"])

    def test_selected_ratio_provenance_prevents_quote_clock_and_other_feed_substitution(self):
        position = position_fixture()
        pace = position_volume_pace_snapshot(position)
        self.assertEqual(1, pace["timeAdjustedVolumeRatio"])
        self.assertEqual(AT, pace["volumePaceSourceAsOf"])
        from digital_twin.modules.reasoning.domain.portfolio_ontology_market_concepts import volume_profile
        from digital_twin.modules.reasoning.domain.ontology_relation_facts import position_signal_facts
        from digital_twin.modules.portfolio.domain.portfolio_calculations import portfolio_summary
        from digital_twin.modules.outcomes.application.investment_outcome_observation_service import InvestmentOutcomeObservationService
        facts = position_signal_facts(position, portfolio_summary([position]))
        for shaped in (facts, volume_profile(position), InvestmentOutcomeObservationService.snapshot_observations(
                SimpleNamespace(settings={}, optional_number=InvestmentOutcomeObservationService.optional_number), [position], AT)[position.symbol]):
            self.assertEqual(1, shaped["timeAdjustedVolumeRatio"])
            self.assertEqual(AT, shaped["volumePaceSourceAsOf"])
        for change in ({"values": {"volumeRatio": .8}}, {"sourceTimestampState": "websocket-received"},
                       {"sourceTimestampState": "queried-at-fallback"}, {"freshnessStatus": "stale"},
                       {"sourceAsOf": "2026-09-14T14:15:00+09:00"}):
            bad = deepcopy(position)
            bad.market_signal_coverage["volume"].update(change)
            self.assertNotIn("timeAdjustedVolumeRatio", position_volume_pace_snapshot(bad))
        queried = deepcopy(position)
        queried.market_signal_coverage["volume"]["sourceTimestampState"] = "queried-at-fallback"
        rows = " ".join(volume_pace_rows(snapshot(position_volume_pace_snapshot(queried))))
        self.assertIn("거래량 조회 시각", rows)
        self.assertIn("실제 집계 시각 미확인", rows)

    def test_toss_daily_ratio_retains_its_own_candle_volume_denominator_and_date(self):
        indicators = technical_indicators_from_candles([
            {"date": "2026-09-10", "close": 3000, "volume": 1000},
            {"date": "2026-09-11", "close": 3100, "volume": 2000},
            {"date": "2026-09-14", "close": 3200, "volume": 0},
        ])
        provider = TossProvider.__new__(TossProvider)
        merged = provider.merge_market_data(position_fixture(),
            {"currentPrice": 3300, "volume": 7000, "sourceAsOf": AT, "updatedAt": AT}, indicators, {}, True, True)
        pace = position_volume_pace_snapshot(merged)
        self.assertEqual(7000, merged.volume)
        self.assertAlmostEqual(2000 / 1500, merged.volume_ratio)
        self.assertEqual("2026-09-11", pace["volumePaceSourceAsOf"])
        self.assertEqual(2000, pace["volumeRatioNumerator"])
        self.assertEqual(1500, pace["volumeRatioDenominator"])
        self.assertNotIn("timeAdjustedVolumeRatio", pace)
        text = " ".join(volume_pace_rows(snapshot({**pace, "volumeRatio": merged.volume_ratio})))
        self.assertIn("마지막 봉 포함", text)
        self.assertIn("2026-09-11 (일봉 날짜)", text)
        self.assertIn("일봉 거래량을 장중 누적 거래량으로 보정하지 않습니다", text)

    def test_kis_volume_uses_exchange_date_time_and_preserves_it_through_merge(self):
        from digital_twin.infrastructure.kis_market_signals import merge_fresh_websocket_stages, KISMarketSignalProvider
        from unittest.mock import patch
        update = normalize_ws_ccnl({"mksc_shrn_iscd": "000680", "bsop_date": "20260914", "stck_cntg_hour": "121500",
            "stck_prpr": "3000", "acml_vol": "5500", "prdy_vol_vrss_acml_vol_rate": "55", "cttr": "120",
            "shnu_cntg_smtn": "3000", "seln_cntg_smtn": "2500", "_wire_field_count": 46})
        signal = merge_realtime_signal({}, update, "ccnl", KIS_TR_CCN_PRICE, "2026-09-14T03:15:10Z")
        self.assertEqual(AT, signal["marketSignalCoverage"]["volume"]["sourceAsOf"])
        with patch("digital_twin.infrastructure.kis_market_signals.fresh_websocket_stage", side_effect=lambda cached, stage, age: stage == "ccnl"):
            signal = merge_fresh_websocket_stages({"symbol": "000680"}, signal, 30)
        position = KISMarketSignalProvider.__new__(KISMarketSignalProvider).merge_position(
            normalize_position({"symbol": "000680", "market": "KR"}), signal)
        self.assertEqual(1, position_volume_pace_snapshot(position)["timeAdjustedVolumeRatio"])
        update["volumeSourceAsOf"] = ""
        missing = merge_realtime_signal({}, update, "ccnl", KIS_TR_CCN_PRICE, "2026-09-14T03:15:10Z")
        self.assertEqual("", missing["marketSignalCoverage"]["volume"]["sourceAsOf"])
        from digital_twin.infrastructure.kis_realtime_ws import volume_source_time
        for date, clock in (("202609", "121500"), ("20260914", "12150"), ("20260230", "121500"), ("20260914", "250000")):
            self.assertEqual("", volume_source_time({"bsop_date": date, "stck_cntg_hour": clock}))

    def test_trade_strength_is_visible_with_history_quality_samples_and_observed_zero(self):
        source = {"observedFields": ["tradeStrength"], "sourceAsOf": AT, "tradeStrengthSampleCount": 5500,
                  "tradeStrengthSampleState": "execution-volume", "judgementEvidenceUsable": True}
        before = snapshot({"tradeStrength": 90}, {**source, "sourceAsOf": "2026-09-14T12:00:00+09:00"})
        text = " ".join(execution_rows(snapshot({"tradeStrength": 120}, source), before))
        for expected in ("체결강도 120", "90 → 이번 120 (+30포인트)", "12:00 KST", "12:15 KST", "체결 표본 5,500주"):
            self.assertIn(expected, text)
        self.assertIn("체결강도 0", " ".join(execution_rows(snapshot({"tradeStrength": 0}, source))))
        self.assertIn("미확인", " ".join(execution_rows(snapshot({"tradeStrength": 0}))))
        text = " ".join(execution_rows(snapshot({"tradeStrength": 69.68}, {**source, "judgementEvidenceUsable": False,
            "tradeStrengthQualityReason": "장 마감 후 값입니다."})))
        self.assertIn("69.68 · 참고값", text)
        self.assertIn("장 마감 후", text)
        self.assertNotIn("매도 체결 강함", text)
        self.assertIn("체결강도 미제공", " ".join(execution_rows(snapshot({"market": "US", "tradeStrength": 0}))))

    def test_final_relation_message_keeps_formula_tracking_and_legacy_estimate_warning(self):
        context = readable_fixture()
        facts = context["ontologyRelationContext"]["facts"]
        facts.update(position_volume_pace_snapshot(position_fixture()))
        previous = deepcopy(context["relationChangeEvidence"]["previous"])
        context["relationChangeEvidence"] = relation_change_evidence(context, previous)
        text = typedb_observation_telegram_message(context)
        for expected in ("체결강도와 대기 주문", "거래량과 시간 보정", "체결강도 추적", "기대 누적 비중 55%", "정규장 50% 경과", "실제 과거 동시간"):
            self.assertIn(expected, text)
        sections = {row["key"]: row for row in context["customerInvestmentDocument"]["sections"]}
        self.assertIn("volume", sections)
        legacy = " ".join(volume_pace_rows(snapshot({"volumeRatio": 1.64068, "timeAdjustedVolumeRatio": 1.549})))
        self.assertIn("기존 시간 보정 기록 1.55배", legacy)
        self.assertIn("검증되지 않은 참고값", legacy)
        self.assertNotIn("장중 시간 보정 추정", legacy)
        from test_relation_observation_narrative import temporal_fixture, freeze_and_render
        context, proof = temporal_fixture()
        proof["sourceTemporalWindows"][0].update(tradeStrengthEnd=97.35, volumeRatioEnd=.72)
        context["ontologyRelationContext"]["facts"]["profitLoss"] = -7.13
        text = freeze_and_render(context)
        self.assertIn("이 기간 데이터에 저장된 마지막 값은 체결강도 97.35, 거래량 비율 0.72배", text)
        self.assertIn("평가손익 -$7.13", text)

    def test_followup_display_requires_registration_and_verified_transition_with_progress(self):
        row = registered_follow_up(condition(), episode_id="ai:1", account_id="main", symbol="005930", registered_at=START, owner_kind="ai-insight")
        row.update(trackingBaselineValue=-.1, previousValue=.2, currentValue=.25, lastSourceAsOf="2026-09-15T00:03:00Z",
                   confirmationCount=1, status="satisfied", transitionVerified=False)
        context = {"accountId": "main", "symbol": "005930", "followUpRegistration": {"conditions": [row]}}
        text = " ".join(_follow_up_rows(context))
        for value in ("시작 -0.1% → 직전 관측 0.2% → 최근 관측 0.25%", "연속 확인 1/2회", "09/15 09:03 KST"):
            self.assertIn(value, text)
        self.assertNotIn("조건 도달", text)
        row["transitionVerified"] = True
        self.assertIn("조건 도달 확인", " ".join(_follow_up_rows(context)))
        context["accountId"] = "another"
        self.assertEqual([], _follow_up_rows(context))
        self.assertEqual([], _follow_up_rows({"decisionContinuityPacket": {"followUpConditions": [condition()]}}))

    def test_followup_volume_clock_never_falls_back_to_price_when_pace_provenance_exists(self):
        facts = {"marketEvidenceProfile": {"capabilities": {"volume": {"state": "fresh", "sourceAsOf": "2026-09-14T06:00:00Z"}}},
                 "sourceAsOf": "2026-09-14T06:00:00Z", "volumePaceSourceAsOf": AT, "volumePaceStatus": "open", "volumePaceSourceTimestampState": "provider-execution"}
        condition = {"field": "timeAdjustedVolumeRatio"}
        self.assertEqual(AT, follow_up_source_time(condition, facts))
        facts["volumePaceStatus"] = "unavailable"
        self.assertEqual("", follow_up_source_time(condition, facts))
        facts["volumePaceSourceAsOf"] = ""
        self.assertEqual("", follow_up_source_time({"field": "volumeRatio"}, facts))
        for missing_zone in ("2026-09-14", "2026-09-14T12:15:00"):
            facts["volumePaceSourceAsOf"] = missing_zone
            facts["volumePaceSourceTimestampState"] = "provider-candle"
            self.assertEqual("", follow_up_source_time({"field": "volumeRatio"}, facts))
