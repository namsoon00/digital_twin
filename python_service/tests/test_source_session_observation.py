"""Real ingestion/serialization must supply the strategy's session facts."""
import json
import unittest
from copy import deepcopy
from dataclasses import replace

from digital_twin.modules.portfolio.contracts import Position
from digital_twin.modules.market_data.domain.market_time_series import (
    MarketTimeSeriesObservation, temporal_observation_payload,
)
from digital_twin.modules.market_data.domain.time_series_storage import TemporalFeatureSnapshot, TimeSeriesWatermark
from digital_twin.modules.model_registry.domain.statistical_signals import score_temporal_feature_snapshot
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_schema import add_entity
from digital_twin.modules.reasoning.domain.portfolio_ontology_statistical_concepts import add_position_statistical_signal_concepts


def source_row(source="2026-10-23T05:00:00Z", captured="2026-10-23T05:00:24Z", **overrides):
    position = Position(symbol="STRATEGY_TEST", name="Fixture", market="KR", currency="KRW",
        current_price=105, ma20=100, ma20_distance=5, data_quality="actual", source_as_of=source,
        source_fetched_at=captured, updated_at=captured, freshness_status="fresh")
    position = replace(position, **overrides)
    return MarketTimeSeriesObservation.from_position("fixture", position, captured).to_row()


def source_signal(row=None):
    live = temporal_observation_payload(row or source_row(), "time-series-store")
    daily = [{"sourceAsOf": "2026-10-%02dT06:30:00Z" % day,
              "generatedAt": "2026-10-%02dT06:31:00Z" % day,
              "currentPrice": price, "market": "KR", "currency": "KRW", "dataQuality": "actual"}
             for day, price in [(16, 100), (19, 101), (20, 102), (21, 103), (22, 104)]]
    packet = TemporalFeatureSnapshot.create(backend_id="fixture", account_id="fixture",
        as_of=live["generatedAt"], windows={"STRATEGY_TEST": {"5D": daily, "20D": daily, "SESSION": [live]}},
        watermark=TimeSeriesWatermark("fixture", live["generatedAt"]))
    return score_temporal_feature_snapshot(packet)


class SourceSessionObservationTests(unittest.TestCase):
    def test_persisted_source_reaches_scoring_and_abox_without_injected_session(self):
        row = source_row()
        # Simulate JSON/SQL round trip: metadata must travel in an existing
        # persisted column, rather than a transient Position-only attribute.
        row = json.loads(json.dumps(row))
        live = temporal_observation_payload(row, "time-series-store")
        self.assertEqual("regular", live["marketSession"])
        self.assertEqual(row["source_as_of"], live["sourceSessionObservation"]["sourceAsOf"])
        signal = source_signal(row)
        self.assertTrue(signal.signals)
        self.assertTrue(all(s.market_session == "regular" for s in signal.signals))
        self.assertTrue(all(s.source_age_seconds == 24 for s in signal.signals))
        graph = PortfolioOntology("portfolio:fixture")
        stock = add_entity(graph, "stock", "STRATEGY_TEST", "Fixture", {"symbol": "STRATEGY_TEST"})
        add_position_statistical_signal_concepts(graph, stock, "STRATEGY_TEST",
            {"statisticalSignalSnapshot": signal.to_dict()})
        models = [e for e in graph.entities if e.kind == "statistical-model-signal"]
        self.assertTrue(models)
        self.assertTrue(all(e.properties.get("marketSession") == "regular" for e in models))
        self.assertTrue(all(e.properties.get("sourceAgeSeconds") == 24 for e in models))

    def test_regular_boundaries_weekends_and_us_dst(self):
        for source, captured, market, expected in [
            ("2026-10-23T00:00:00Z", "2026-10-23T00:00:24Z", "KR", "regular"),
            ("2026-10-23T06:29:59Z", "2026-10-23T06:30:01Z", "KR", "after"),
            ("2026-10-24T05:00:00Z", "2026-10-24T05:00:24Z", "KR", "closed"),
            ("2026-10-23T13:29:00Z", "2026-10-23T13:29:24Z", "US", "pre"),
            ("2026-10-23T13:30:00Z", "2026-10-23T13:30:24Z", "US", "regular"),
            ("2026-11-06T13:30:00Z", "2026-11-06T13:30:24Z", "US", "pre"),
            ("2026-11-06T14:30:00Z", "2026-11-06T14:30:24Z", "US", "regular"),
        ]:
            with self.subTest(source=source, market=market):
                self.assertEqual(expected, temporal_observation_payload(
                    source_row(source, captured, market=market), "fixture")["marketSession"])

    def test_explicit_closure_and_reference_override_standard_hours(self):
        for kwargs in ({"market_session": "closed_exception"}, {"freshness_status": "last-close"},
                       {"freshness_status": "last-close", "market_session": "pre"},
                       {"market_signal_coverage": {"price": {"sourceAsOf": "2026-10-23T05:00:00Z", "marketSession": "closed"}}}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual("closed", temporal_observation_payload(source_row(**kwargs), "fixture")["marketSession"])

    def test_different_feed_closure_does_not_replace_selected_quote_session(self):
        row = source_row(market_signal_coverage={"price": {"sourceAsOf": "2026-10-22T06:30:00Z", "marketSession": "closed"}})
        self.assertEqual("regular", temporal_observation_payload(row, "fixture")["marketSession"])

    def test_invalid_missing_and_future_source_cannot_borrow_poll_clock(self):
        for source in ("", "invalid", "2026-10-23T05:00:00", "2026-10-23T05:01:00Z"):
            with self.subTest(source=source):
                self.assertNotIn("marketSession", temporal_observation_payload(source_row(source), "fixture"))

    def test_legacy_daily_and_mismatched_metadata_remain_unknown(self):
        for field, value in (("investor_coverage_json", "{}"), ("source_as_of", "2026-10-23T04:59:59Z"),
                             ("observed_at", "2026-10-23T05:01:00Z"), ("market", "US"), ("granularity", "1d")):
            with self.subTest(field=field):
                row = {**source_row(), field: value}
                frozen = deepcopy(row)
                self.assertNotIn("marketSession", temporal_observation_payload(row, "fixture"))
                self.assertEqual(frozen, row)

    def test_stale_source_keeps_age_instead_of_becoming_fresh_on_capture(self):
        packet = source_signal(source_row(source="2026-10-23T04:40:00Z"))
        self.assertTrue(all(s.source_age_seconds == 1224 for s in packet.signals))


if __name__ == "__main__":
    unittest.main()
