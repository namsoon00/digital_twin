import unittest

from digital_twin.application.market_data_collection_service import MarketDataCollectionRunner
from digital_twin.domain.portfolio import Position
from digital_twin.domain.position_identity import position_with_symbol_identity


class PositionIdentityTests(unittest.TestCase):
    def test_replaces_code_only_provider_name_with_symbol_universe_name(self):
        position = Position(symbol="066570", name="066570", market="", currency="", sector="기타")

        actual = position_with_symbol_identity(position, {
            "name": "LG전자",
            "market": "KOSPI",
            "currency": "KRW",
            "sector": "전기전자",
        })

        self.assertEqual("LG전자", actual.name)
        self.assertEqual("KOSPI", actual.market)
        self.assertEqual("KRW", actual.currency)
        self.assertEqual("전기전자", actual.sector)

    def test_preserves_provider_name_when_it_is_not_a_symbol_placeholder(self):
        position = Position(symbol="066570", name="LG Electronics", market="KR", currency="KRW", sector="기타")

        actual = position_with_symbol_identity(position, {"name": "LG전자", "sector": "전기전자"})

        self.assertEqual("LG Electronics", actual.name)
        self.assertEqual("전기전자", actual.sector)

    def test_market_collection_enriches_code_only_focus_position(self):
        class SymbolService:
            def enrich(self, symbol):
                return {"name": "LG전자", "market": "KOSPI", "currency": "KRW", "sector": "전기전자"}

        class Provider:
            def fetch_focus_targets(self):
                position = Position(symbol="066570", name="066570", source="watchlist")
                return "live", "토스 계좌 동기화", "token", [], [position]

        runner = MarketDataCollectionRunner(None, SymbolService(), None, {}, None)

        mode, _status, _token, positions = runner.focus_positions(Provider())

        self.assertEqual("live", mode)
        self.assertEqual(1, len(positions))
        self.assertEqual("LG전자", positions[0].name)


if __name__ == "__main__":
    unittest.main()
