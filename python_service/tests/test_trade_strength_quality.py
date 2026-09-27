import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.market_data.domain.trade_strength_quality import trade_strength_quality_snapshot
from digital_twin.modules.market_data.domain.data_freshness import aggregate_freshness
from digital_twin.modules.notifications.application.typedb_observation_message import _flow_rows
from digital_twin.modules.portfolio.domain.portfolio import Position
from digital_twin.modules.portfolio.domain.portfolio_calculations import portfolio_summary
from digital_twin.modules.reasoning.domain.ontology_inference_materializer import observation_metadata
from digital_twin.modules.reasoning.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.infrastructure.kis_market_signals import KISMarketSignalProvider, stage_source_as_of


def coverage(**overrides):
    item = {
        "stage": "ccnl",
        "status": "available",
        "fields": ["tradeStrength", "buyVolume", "sellVolume"],
        "marketSession": "regular",
        "marketSessionLabel": "정규장",
        "transport": "rest",
        "sourceTimestampState": "queried-at-fallback",
        "fetchedAt": "2026-09-25T01:00:00Z",
        "unchangedCount": 0,
    }
    item.update(overrides)
    return item


class TradeStrengthQualityTest(unittest.TestCase):
    def test_rest_stage_uses_query_clock_without_claiming_provider_timestamp(self):
        source_as_of, state = stage_source_as_of("ccnl", [{"tday_rltv": "105"}], "2026-09-25T01:00:00Z")

        self.assertEqual("2026-09-25T01:00:00Z", source_as_of)
        self.assertEqual("queried-at-fallback", state)

    def test_opening_sample_is_reference_even_for_websocket(self):
        result = trade_strength_quality_snapshot(
            trade_strength=121,
            coverage=coverage(transport="websocket", sourceTimestampState="websocket-received"),
            observed_at="2026-09-25T00:02:00Z",
            buy_volume=1200,
            sell_volume=900,
            cumulative_volume=2100,
            changed_since_previous=True,
        )

        self.assertEqual("opening-sample-pending", result["tradeStrengthQualityState"])
        self.assertFalse(result["judgementEvidenceUsable"])
        self.assertEqual(2.0, result["tradeStrengthSessionElapsedMinutes"])

    def test_websocket_sample_is_usable_after_opening_confirmation(self):
        result = trade_strength_quality_snapshot(
            trade_strength=121,
            coverage=coverage(transport="websocket", sourceTimestampState="websocket-received"),
            observed_at="2026-09-25T00:06:00Z",
            buy_volume=1200,
            sell_volume=900,
            cumulative_volume=2100,
            changed_since_previous=True,
        )

        self.assertEqual("confirmed-live", result["tradeStrengthQualityState"])
        self.assertTrue(result["judgementEvidenceUsable"])

    def test_rest_value_requires_observed_same_day_change(self):
        pending = trade_strength_quality_snapshot(
            trade_strength=105,
            coverage=coverage(),
            observed_at="2026-09-25T01:00:00Z",
            cumulative_volume=10000,
        )
        confirmed = trade_strength_quality_snapshot(
            trade_strength=105,
            coverage=coverage(),
            observed_at="2026-09-25T01:02:00Z",
            cumulative_volume=11000,
            changed_since_previous=True,
        )

        self.assertEqual("change-confirmation-pending", pending["tradeStrengthQualityState"])
        self.assertFalse(pending["judgementEvidenceUsable"])
        self.assertEqual("confirmed-polled-change", confirmed["tradeStrengthQualityState"])
        self.assertTrue(confirmed["judgementEvidenceUsable"])

    def test_repeated_frozen_value_is_not_usable(self):
        previous = coverage(
            judgementEvidenceUsable=True,
            aiUsableAsStrongEvidence=True,
            tradeStrengthQualityState="confirmed-polled-change",
            fetchedAt="2026-09-25T00:58:00Z",
        )
        result = trade_strength_quality_snapshot(
            trade_strength=95.75,
            coverage=coverage(status="stale", unchangedCount=3),
            observed_at="2026-09-25T01:00:00Z",
            cumulative_volume=2947714,
            changed_since_previous=False,
            previous_coverage=previous,
            stale_repeat_count=3,
        )

        self.assertEqual("stale-repeat", result["tradeStrengthQualityState"])
        self.assertFalse(result["judgementEvidenceUsable"])

    def test_post_close_value_is_reference_only(self):
        result = trade_strength_quality_snapshot(
            trade_strength=101,
            coverage=coverage(marketSession="post_close", marketSessionLabel="장 마감 후"),
            observed_at="2026-09-25T07:00:00Z",
            cumulative_volume=50000,
            changed_since_previous=True,
        )

        self.assertEqual("market-close-reference", result["tradeStrengthQualityState"])
        self.assertFalse(result["judgementEvidenceUsable"])

    def test_rest_ratio_remains_fresh_when_cumulative_volume_advances(self):
        provider = KISMarketSignalProvider(
            settings={"kisMarketSignalUnchangedStaleCount": "3"},
            quote_cache=object(),
            now_provider=lambda: datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc),
        )
        previous = {
            "tradeStrength": 105,
            "volume": 1000,
            "marketSignalCoverage": {
                "ccnl": {
                    **coverage(fetchedAt="2026-09-25T00:58:00Z"),
                    "judgementEvidenceUsable": True,
                    "aiUsableAsStrongEvidence": True,
                }
            },
        }
        current = {
            "tradeStrength": 105,
            "volume": 1100,
            "updatedAt": "2026-09-25T01:00:00Z",
            "marketSignalCoverage": {"ccnl": coverage()},
        }

        result = provider.mark_unchanged_stage_health(current, previous)
        ccnl = result["marketSignalCoverage"]["ccnl"]

        self.assertEqual("available", ccnl["status"])
        self.assertEqual(0, ccnl["unchangedCount"])
        self.assertEqual("confirmed-polled-change", ccnl["tradeStrengthQualityState"])
        self.assertTrue(ccnl["judgementEvidenceUsable"])

    def test_abox_and_inference_metadata_preserve_blocked_trade_strength(self):
        quality = trade_strength_quality_snapshot(
            trade_strength=118,
            coverage=coverage(),
            observed_at="2026-09-25T00:02:00Z",
            cumulative_volume=1000,
            changed_since_previous=True,
        )
        ccnl = {**coverage(fetchedAt="2026-09-25T00:02:00Z"), **quality}
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            current_price=1800000,
            market_value=1800000,
            quantity=1,
            sellable_quantity=1,
            ma5=1790000,
            ma20=1750000,
            ma60=1700000,
            trade_strength=118,
            volume=1000,
            source_as_of="2026-09-25T00:02:00Z",
            source_fetched_at="2026-09-25T00:02:00Z",
            updated_at="2026-09-25T00:02:00Z",
            data_quality="actual",
            market_signal_coverage={"ccnl": ccnl},
        )
        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([position], fx_rates={"KRW": 1}),
            portfolio_id="trade-strength-quality",
            runtime_context={"asOf": "2026-09-25T00:02:00Z"},
        )
        metric = next(
            item for item in graph.entities
            if item.kind == "flow-metric" and (item.properties or {}).get("field") == "tradeStrength"
        )
        metadata = observation_metadata(metric.properties, metric.properties.get("value"))

        self.assertEqual("opening-sample-pending", metric.properties["tradeStrengthQualityState"])
        self.assertFalse(metric.properties["judgementEvidenceUsable"])
        self.assertFalse(metadata["judgementEvidenceUsable"])

    def test_gated_trade_strength_does_not_block_other_flow_sources(self):
        result = aggregate_freshness([
            {
                "source": "KIS ccnl",
                "status": "fresh",
                "judgementEvidenceUsable": False,
                "aiUsableAsStrongEvidence": False,
                "ageMinutes": 0,
                "maxAgeMinutes": 5,
            },
            {
                "source": "KIS investor",
                "status": "fresh",
                "judgementEvidenceUsable": True,
                "aiUsableAsStrongEvidence": True,
                "ageMinutes": 0,
                "maxAgeMinutes": 5,
            },
        ], "investmentInsight", now=datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc))

        self.assertTrue(result["judgementEvidenceUsable"])
        self.assertEqual(1, result["judgementEvidenceUsableSourceCount"])
        self.assertEqual(1, result["judgementEvidenceGatedSourceCount"])

    def test_typedb_message_labels_confirmed_strength_as_unadjusted_raw_ratio(self):
        context = {
            "ontologyRelationContext": {
                "facts": {
                    "market": "KR",
                    "currentPrice": 1800000,
                    "tradeStrength": 111.2,
                    "marketSignalCoverage": {
                        "ccnl": {
                            "status": "available",
                            "judgementEvidenceUsable": True,
                            "tradeStrengthQualityState": "confirmed-polled-change",
                        }
                    },
                }
            }
        }

        self.assertIn("정규장 누적 원값", " ".join(_flow_rows(context, 10)))

    def test_typedb_message_explains_why_opening_strength_is_excluded(self):
        reason = "장 시작 후 5분 동안은 체결 표본이 작아 체결강도를 참고값으로만 봅니다."
        context = {
            "ontologyRelationContext": {
                "facts": {
                    "market": "KR",
                    "currentPrice": 1800000,
                    "tradeStrength": 130,
                    "marketSignalCoverage": {
                        "ccnl": {
                            "status": "available",
                            "judgementEvidenceUsable": False,
                            "tradeStrengthQualityState": "opening-sample-pending",
                            "tradeStrengthQualityReason": reason,
                        }
                    },
                }
            }
        }

        rows = " ".join(_flow_rows(context, 10))
        self.assertIn(reason, rows)
        self.assertNotIn("체결강도 130", rows)


if __name__ == "__main__":
    unittest.main()
