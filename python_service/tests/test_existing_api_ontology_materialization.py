import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.infrastructure.sqlite_monitoring import SQLiteExternalSignalCache


class ExistingApiOntologyMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                "DIGITAL_TWIN_DATA_DIR": self.temp.name,
                "SETTINGS_PATH": str(Path(self.temp.name) / "settings.json"),
            },
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_alpha_vantage_fundamentals_can_use_existing_api_key(self):
        calls = []

        def fake_fetch(url, headers=None):
            calls.append(url)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            function = query.get("function", [""])[0]
            if function == "GLOBAL_QUOTE":
                return {"Global Quote": {
                    "05. price": "210.25",
                    "06. volume": "58000000",
                    "07. latest trading day": "2026-07-10",
                    "09. change": "2.25",
                    "10. change percent": "1.1%",
                }}
            if function == "OVERVIEW":
                return {
                    "Symbol": "AAPL",
                    "Name": "Apple Inc.",
                    "Sector": "Technology",
                    "Industry": "Consumer Electronics",
                    "LatestQuarter": "2026-03-28",
                    "MarketCapitalization": "3200000000000",
                    "RevenueTTM": "391000000000",
                    "PERatio": "29.5",
                    "PEGRatio": "2.1",
                    "ForwardPE": "27.2",
                    "AnalystTargetPrice": "245.0",
                    "AnalystRatingStrongBuy": "8",
                    "AnalystRatingBuy": "18",
                    "AnalystRatingHold": "12",
                    "AnalystRatingSell": "2",
                    "AnalystRatingStrongSell": "0",
                }
            if function == "EARNINGS":
                return {"quarterlyEarnings": [{
                    "fiscalDateEnding": "2026-03-28",
                    "reportedDate": "2026-05-01",
                    "reportedEPS": "1.65",
                    "estimatedEPS": "1.58",
                    "surprise": "0.07",
                    "surprisePercentage": "4.43",
                }]}
            return {}

        provider = ExternalSignalProvider(
            settings={
                "alphaVantageApiKey": "alpha-key",
                "externalAlphaFundamentalsEnabled": "1",
                "externalAlphaMaxSymbols": "1",
                "externalAlphaFundamentalsMaxSymbols": "1",
                "externalApiRateLimitSeconds": "0",
                "externalCoinGeckoEnabled": "0",
                "externalFredEnabled": "0",
                "externalDartEnabled": "0",
                "externalSecEnabled": "0",
                "externalNewsEnabled": "0",
                "externalFxRateEnabled": "0",
            },
            cache=SQLiteExternalSignalCache(Path(self.temp.name) / "service.db"),
            fetch_json=fake_fetch,
            sleep=lambda _: None,
        )
        position = normalize_position({"symbol": "AAPL", "name": "Apple", "market": "US", "currency": "USD"})

        signals = provider.fetch_signals([position])

        self.assertEqual({"AAPL"}, set(signals["companyOverviews"].keys()))
        self.assertEqual({"AAPL"}, set(signals["earningsReports"].keys()))
        self.assertEqual(245.0, signals["companyOverviews"]["AAPL"]["analystTargetPrice"])
        self.assertEqual(1.65, signals["earningsReports"]["AAPL"]["latestQuarter"]["reportedEPS"])
        self.assertEqual(["GLOBAL_QUOTE", "OVERVIEW", "EARNINGS"], [
            urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("function", [""])[0]
            for url in calls
        ])

    def test_existing_api_payloads_materialize_investment_world_objects(self):
        position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "marketValue": 1000,
            "currentPrice": 210,
        })
        kr_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000,
            "currentPrice": 90000,
        })
        external_signals = {
            "companyOverviews": {
                "AAPL": {
                    "provider": "Alpha Vantage",
                    "symbol": "AAPL",
                    "name": "Apple Inc.",
                    "industry": "Consumer Electronics",
                    "latestQuarter": "2026-03-28",
                    "marketCapitalization": 3200000000000,
                    "revenueTTM": 391000000000,
                    "peRatio": 29.5,
                    "pegRatio": 2.1,
                    "forwardPE": 27.2,
                    "analystTargetPrice": 245.0,
                    "analystRatingBuy": 18,
                    "analystRatingHold": 12,
                },
            },
            "earningsReports": {
                "AAPL": {
                    "provider": "Alpha Vantage",
                    "latestQuarter": {
                        "fiscalDateEnding": "2026-03-28",
                        "reportedDate": "2026-05-01",
                        "reportedEPS": 1.65,
                        "estimatedEPS": 1.58,
                        "surprisePercentage": 4.43,
                    },
                },
            },
            "secFilings": {
                "AAPL": {
                    "provider": "SEC EDGAR",
                    "latestFiling": {"form": "10-Q", "filingDate": "2026-05-01", "reportDate": "2026-03-28"},
                    "facts": {"revenue": {"value": 95359000000, "end": "2026-03-28", "filed": "2026-05-01", "form": "10-Q"}},
                },
            },
            "dartDisclosures": {
                "005930": {
                    "provider": "OpenDART",
                    "reportName": "주요사항보고서(자기주식처분결정 및 소송 등의 제기)",
                    "receiptNo": "20260701000001",
                    "receiptDate": "20260701",
                },
            },
        }

        graph = build_portfolio_ontology(
            [position, kr_position],
            portfolio_summary([position, kr_position], fx_rates={"USD": 1400, "KRW": 1}),
            external_signals=external_signals,
            portfolio_id="main",
        )
        kinds = {item.kind for item in graph.entities}
        tbox_classes = {(item.properties or {}).get("tboxClass") for item in graph.entities}
        relation_types = {item.relation_type for item in graph.relations}

        self.assertIn("AnalystRevision", tbox_classes)
        self.assertIn("CorporateAction", tbox_classes)
        self.assertIn("RegulatoryEvent", tbox_classes)
        self.assertIn("EarningsCalendarEvent", tbox_classes)
        self.assertIn("RevenueExposure", tbox_classes)
        self.assertIn("ValuationAssumption", tbox_classes)
        self.assertIn("analyst-revision", kinds)
        self.assertIn("corporate-action", kinds)
        self.assertIn("regulatory-event", kinds)
        self.assertIn("earnings-calendar-event", kinds)
        self.assertIn("revenue-exposure", kinds)
        self.assertIn("HAS_REVENUE_EXPOSURE", relation_types)
        self.assertIn("HAS_VALUATION", relation_types)


if __name__ == "__main__":
    unittest.main()
