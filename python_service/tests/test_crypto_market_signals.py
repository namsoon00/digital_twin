import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.crypto_market_signals import (
    combine_crypto_market_snapshots,
    crypto_market_observation_events,
    crypto_market_snapshot,
    crypto_market_transitions,
    crypto_transition_baseline,
    crypto_transition_targets,
)
from digital_twin.domain.ontology_rulebox_catalog import crypto_market_inference_rules
from digital_twin.domain.portfolio import Position


def signals(*, btc_24h=0.0, btc_7d=0.0, eth_24h=0.0, eth_7d=0.0, freshness="fresh"):
    return {
        "cryptoFreshness": {
            "status": freshness,
            "fetchedAt": "2026-08-01T00:00:00Z",
            "maxAgeMinutes": 25,
        },
        "cryptoMarkets": {
            "bitcoin": {
                "symbol": "BTC",
                "name": "Bitcoin",
                "change24h": btc_24h,
                "change7d": btc_7d,
                "fetchedAt": "2026-08-01T00:00:00Z",
            },
            "ethereum": {
                "symbol": "ETH",
                "name": "Ethereum",
                "change24h": eth_24h,
                "change7d": eth_7d,
                "fetchedAt": "2026-08-01T00:00:00Z",
            },
        },
    }


class CryptoMarketSignalTests(unittest.TestCase):
    def test_snapshot_rebuilds_stale_cache_timestamps_from_market_rows(self):
        snapshot = crypto_market_snapshot({
            "cryptoFetchedAt": "2026-08-01T00:00:00Z",
            "cryptoSourceAsOf": "2026-07-01T00:00:00Z",
            "cryptoMarkets": {
                "bitcoin": {
                    "symbol": "BTC",
                    "price": 65000,
                    "lastUpdated": "2026-08-01T00:09:00Z",
                    "fetchedAt": "2026-08-01T00:10:00Z",
                },
            },
        })

        self.assertEqual("2026-08-01T00:10:00Z", snapshot["fetchedAt"])
        self.assertEqual("2026-08-01T00:09:00Z", snapshot["sourceAsOf"])
        self.assertEqual("2026-08-01T00:10:00Z", snapshot["lastAttemptAt"])

    def test_combined_snapshot_does_not_retain_legacy_source_time(self):
        combined = combine_crypto_market_snapshots(
            {
                "markets": {
                    "bitcoin": {
                        "symbol": "BTC",
                        "price": 64000,
                        "lastUpdated": "2026-08-01T00:00:00Z",
                        "fetchedAt": "2026-08-01T00:01:00Z",
                    },
                },
                "fetchedAt": "2026-08-01T00:01:00Z",
                "sourceAsOf": "2026-07-01T00:00:00Z",
            },
            {
                "markets": {
                    "bitcoin": {
                        "symbol": "BTC",
                        "price": 65000,
                        "lastUpdated": "2026-08-01T00:09:00Z",
                        "fetchedAt": "2026-08-01T00:10:00Z",
                    },
                },
                "fetchedAt": "2026-08-01T00:10:00Z",
                "sourceAsOf": "2026-07-01T00:00:00Z",
            },
        )

        self.assertEqual(65000, combined["markets"]["bitcoin"]["price"])
        self.assertEqual("2026-08-01T00:09:00Z", combined["sourceAsOf"])

    def test_transitions_require_entry_reversal_or_escalation(self):
        baseline = signals(btc_24h=1.0)
        watch = signals(btc_24h=3.2)
        entry = crypto_market_transitions(baseline, watch)

        self.assertEqual(1, len(entry))
        self.assertEqual("BTC", entry[0]["symbol"])
        self.assertEqual("24h", entry[0]["horizon"])
        self.assertEqual("up", entry[0]["direction"])
        self.assertEqual("watch", entry[0]["severity"])
        self.assertEqual("threshold-crossed", entry[0]["transition"])
        self.assertEqual([], crypto_market_transitions(watch, signals(btc_24h=4.8)))

        escalation = crypto_market_transitions(watch, signals(btc_24h=6.1))
        self.assertEqual("severity-escalated", escalation[0]["transition"])
        self.assertEqual("major", escalation[0]["severity"])

        reversal = crypto_market_transitions(signals(btc_24h=3.2), signals(btc_24h=-3.1))
        self.assertEqual("direction-changed", reversal[0]["transition"])
        self.assertEqual("down", reversal[0]["direction"])

    def test_stale_crypto_data_never_schedules_a_transition(self):
        self.assertEqual(
            [],
            crypto_market_transitions(signals(btc_24h=0.0), signals(btc_24h=-8.0, freshness="stale")),
        )

    def test_market_observation_owns_thresholds_and_rulebox_consumes_classified_event(self):
        events = crypto_market_observation_events(signals(btc_24h=6.2, eth_7d=-4.2))
        by_state = {item["symbol"] + ":" + item["horizon"]: item for item in events}

        self.assertEqual("major", by_state["BTC:24h"]["severity"])
        self.assertEqual("down", by_state["ETH:7d"]["direction"])
        rules = crypto_market_inference_rules()
        self.assertEqual(8, len(rules))
        self.assertTrue(all(len(rule.conditions) == 1 for rule in rules))
        self.assertTrue(all(rule.conditions[0].relation_type == "HAS_OBSERVATION" for rule in rules))
        self.assertTrue(all(rule.conditions[0].target_kind == "market-event" for rule in rules))
        self.assertTrue(all(
            "eventType" in rule.conditions[0].target_property_filters
            for rule in rules
        ))

    def test_transition_baseline_only_records_fresh_supported_assets(self):
        source = signals(btc_24h=3.2, eth_7d=-4.1)
        source["cryptoMarkets"]["dogecoin"] = {
            "symbol": "DOGE",
            "change24h": 20.0,
        }

        baseline = crypto_transition_baseline(source)

        self.assertEqual({"bitcoin", "ethereum"}, set(baseline["cryptoMarkets"]))
        self.assertEqual(3.2, baseline["cryptoMarkets"]["bitcoin"]["change24h"])
        self.assertEqual("fresh", baseline["cryptoFreshness"]["status"])
        self.assertEqual({}, crypto_transition_baseline(signals(btc_24h=8.0, freshness="stale")))

    def test_transition_targets_are_direct_assets_and_sensitive_positions_only(self):
        transitions = crypto_market_transitions(signals(btc_24h=0.0), signals(btc_24h=-3.2))
        targets = crypto_transition_targets(
            transitions,
            [
                Position(symbol="AAPL", name="Apple", market="US", currency="USD", source="holding"),
                Position(symbol="MSTR", name="Strategy", market="US", currency="USD", source="holding"),
                Position(symbol="COIN", name="Coinbase", market="US", currency="USD", source="watchlist"),
            ],
        )

        self.assertEqual(["BTC", "MSTR", "COIN"], targets)


if __name__ == "__main__":
    unittest.main()
