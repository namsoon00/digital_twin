import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.external_signal_alerts import ExternalSignalAlertMixin
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.portfolio_calculations import portfolio_summary


class ExternalSignalAlertHarness(ExternalSignalAlertMixin):
    def criteria(self, setting: str, detected: str = ""):
        return [item for item in [setting, detected] if item]


class ExternalSignalAlertTests(unittest.TestCase):
    def snapshot(self, external_signals):
        return AccountSnapshot(
            "acct",
            "테스트 계좌",
            "test",
            "live",
            "ok",
            "2026-07-25T00:00:00Z",
            portfolio_summary([], account_cash=0),
            external_signals=external_signals,
        )

    def test_crypto_market_move_is_not_sent_outside_ontology_rulebox(self):
        events = ExternalSignalAlertHarness().external_signal_events(self.snapshot({
            "cryptoMarkets": {
                "bitcoin": {"symbol": "BTC", "price": 64000, "change24h": -8.0, "change7d": -15.0},
            },
        }), {})

        self.assertEqual([], events)

    def test_external_source_connection_failure_remains_operational_alert(self):
        events = ExternalSignalAlertHarness().external_signal_events(self.snapshot({
            "statuses": [{"source": "CoinGecko", "ok": False, "message": "rate limit"}],
        }), {})

        self.assertEqual(1, len(events))
        self.assertEqual("externalDataConnection", events[0].rule)
        self.assertEqual("WATCH", events[0].severity)


if __name__ == "__main__":
    unittest.main()
