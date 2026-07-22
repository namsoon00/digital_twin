import unittest

from digital_twin.application.flow_lens_service import FlowLensService


class FakeMarketQuoteCache:
    def __init__(self):
        self.rows = {
            ("toss", "__market_data__", "066570"): {
                "symbol": "066570",
                "name": "LG전자",
                "market": "KOSPI",
                "currency": "KRW",
                "currentPrice": 182600,
                "changeRate": 1.2,
                "ma20": 188590,
                "updatedAt": "2026-07-22T11:55:25Z",
                "dataQuality": "actual",
            },
            ("kis", "__market_signals__", "066570"): {
                "symbol": "066570",
                "tradeStrength": 111.39,
                "foreignNetVolume": 353264,
                "institutionNetVolume": 11185,
            },
        }

    def load_many(self, provider, account_id, symbols):
        return {
            symbol: self.rows[(provider, account_id, symbol)]
            for symbol in symbols
            if (provider, account_id, symbol) in self.rows
        }


class FlowLensMarketCacheTests(unittest.TestCase):
    def test_persisted_snapshot_uses_fresh_quote_and_signal_cache(self):
        service = FlowLensService(
            account_repository=None,
            snapshot_builder=None,
            settings_provider=lambda: {},
            symbol_enricher=lambda symbol: {
                "symbol": symbol,
                "name": "LG전자",
                "market": "KOSPI",
                "currency": "KRW",
                "sector": "전기전자",
            },
            market_quote_cache=FakeMarketQuoteCache(),
        )

        snapshot = service.snapshot_from_monitor_state({
            "accountId": "default",
            "mode": "live",
            "status": "토스 계좌 동기화",
            "portfolio": {},
            "positions": {},
            "watchlist": {"066570": {"symbol": "066570", "name": "066570", "source": "watchlist"}},
        }, watchlist_symbols="066570")
        item = snapshot["toss"]["watchlist"][0]

        self.assertEqual("LG전자", item["name"])
        self.assertEqual(182600, item["currentPrice"])
        self.assertEqual(111.39, item["tradeStrength"])
        self.assertEqual(353264, item["foreignNetVolume"])
        self.assertEqual(11185, item["institutionNetVolume"])


if __name__ == "__main__":
    unittest.main()
