import unittest

from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.portfolio import account_snapshot_from_monitor_state
from digital_twin.domain.portfolio_calculations import (
    apply_position_base_currency_values,
    portfolio_summary,
    runtime_fx_currencies_from_external_signals,
)
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.portfolio_ontology_exposure_concepts import position_weight
from digital_twin.domain.portfolio_valuation import BROKER_NET_BASIS
from digital_twin.infrastructure.toss_snapshots import TossProvider, currency_rates_from_external_signals


OBSERVED_AT = "2026-08-26T01:02:03Z"


class PortfolioValuationContractTests(unittest.TestCase):
    def sample_positions(self):
        return [
            normalize_position({
                "symbol": "028260",
                "market": "KR",
                "currency": "KRW",
                "quantity": 2,
                "currentPrice": 372500,
                "marketValue": {"amount": 745000, "amountAfterCost": 743294},
                "brokerSourceAsOf": OBSERVED_AT,
            }),
            normalize_position({
                "symbol": "000660",
                "market": "KR",
                "currency": "KRW",
                "quantity": 10,
                "currentPrice": 1715000,
                "marketValue": {"amount": 17150000, "amountAfterCost": 17111651},
                "brokerSourceAsOf": OBSERVED_AT,
            }),
            normalize_position({
                "symbol": "MSTR",
                "market": "US",
                "currency": "USD",
                "quantity": 224,
                "currentPrice": 124.81,
                "marketValue": {"amount": 27957.44, "amountAfterCost": 27909.17},
                "brokerSourceAsOf": OBSERVED_AT,
            }),
        ]

    def test_broker_net_total_matches_toss_cost_adjusted_values(self):
        positions = apply_position_base_currency_values(
            self.sample_positions(),
            {"KRW": 1, "USD": 1400},
            valuation_basis=BROKER_NET_BASIS,
        )
        cash = 17.21 * 1400
        summary = portfolio_summary(
            positions,
            account_cash=cash,
            account_currency="KRW",
            fx_rates={"KRW": 1, "USD": 1400},
            valuation_basis=BROKER_NET_BASIS,
            account_id="default",
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(56951877, summary.total)
        self.assertEqual(57059510, summary.broker_gross_total)
        self.assertEqual(56951877, summary.broker_net_total)
        self.assertEqual(BROKER_NET_BASIS, summary.valuation_basis)
        self.assertTrue(summary.valuation_snapshot_id.startswith("portfolio-valuation:"))
        self.assertEqual(summary.valuation_snapshot_id, summary.valuation["valuationSnapshotId"])
        self.assertEqual(
            {summary.valuation_snapshot_id},
            {position.valuation_snapshot_id for position in positions},
        )
        self.assertEqual(3, len(summary.valuation["positions"]))
        self.assertEqual(27909.17, summary.valuation["positions"][2]["brokerNetNative"])

    def test_quote_repricing_preserves_broker_values(self):
        position = normalize_position({
            "symbol": "000660",
            "market": "KR",
            "currency": "KRW",
            "quantity": 10,
            "currentPrice": 100,
            "marketValue": {"amount": 1000, "amountAfterCost": 990},
        })
        provider = TossProvider.__new__(TossProvider)

        merged = provider.merge_market_data(
            position,
            {"currentPrice": 120, "currency": "KRW", "market": "KR"},
            {},
            {},
            quote_live=True,
            indicators_live=False,
        )

        self.assertEqual(1200, merged.market_value)
        self.assertEqual(1200, merged.mark_to_market_value)
        self.assertEqual(1000, merged.broker_market_value)
        self.assertEqual(990, merged.broker_market_value_after_cost)

    def test_account_weight_uses_base_currency_account_value(self):
        position = normalize_position({
            "symbol": "MSTR",
            "market": "US",
            "currency": "USD",
            "quantity": 10,
            "currentPrice": 10,
            "marketValue": {"amount": 100, "amountAfterCost": 100},
        })
        positions = apply_position_base_currency_values(
            [position],
            {"KRW": 1, "USD": 1400},
            valuation_basis=BROKER_NET_BASIS,
        )
        summary = portfolio_summary(
            positions,
            fx_rates={"KRW": 1, "USD": 1400},
            valuation_basis=BROKER_NET_BASIS,
            account_id="default",
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(100, position_weight(positions[0], summary))

        ontology = build_portfolio_ontology(
            positions,
            summary,
            external_signals={},
            portfolio_id="portfolio:default",
        )
        portfolio_entity = next(item for item in ontology.entities if item.kind == "portfolio")
        stock_entity = next(item for item in ontology.entities if item.kind == "stock" and item.properties.get("symbol") == "MSTR")
        self.assertEqual("broker-net", portfolio_entity.properties["valuationBasis"])
        self.assertEqual(summary.valuation_snapshot_id, portfolio_entity.properties["valuationSnapshotId"])
        self.assertEqual(140000, stock_entity.properties["accountValueKrw"])
        self.assertEqual("broker-net", stock_entity.properties["accountValueBasis"])

    def test_buying_power_keeps_native_currency_rows(self):
        provider = TossProvider.__new__(TossProvider)
        provider.settings = {"fxRates": "KRW=1\nUSD=1400"}
        provider.base_url = "https://example.test"
        provider.cash_balances = {}
        provider.cash_balance_failures = []

        def token_request(_stage, _method, url, token, _headers):
            amount = 0 if "currency=KRW" in url else 17.21
            return {"data": {"cashBuyingPower": amount}}, token

        provider.token_request = token_request
        total, _ = provider.fetch_buying_power("token", "account")

        self.assertEqual(24094, total)
        self.assertTrue(provider.cash_balances_complete())
        self.assertEqual(17.21, provider.cash_balances["USD"]["amount"])

    def test_toss_exchange_rate_drives_broker_net_investment_value(self):
        provider = TossProvider.__new__(TossProvider)
        provider.base_url = "https://example.test"
        provider.exchange_rates = {}
        provider.exchange_rate_failures = []

        def token_request(stage, method, url, token):
            self.assertEqual("exchange-rate", stage)
            self.assertEqual("GET", method)
            self.assertIn("baseCurrency=USD", url)
            return {
                "result": {
                    "baseCurrency": "USD",
                    "quoteCurrency": "KRW",
                    "rate": "1380.4",
                    "midRate": "1379.9",
                    "validFrom": "2026-08-26T08:44:35.000+09:00",
                    "validUntil": "2026-08-26T08:49:32.000+09:00",
                }
            }, token

        provider.token_request = token_request
        provider.fetch_exchange_rates("token", ["USD"])
        signals = provider.attach_exchange_rates({
            "fxRates": {
                "USDKRW": {
                    "provider": "RuntimeSettings",
                    "base": "USD",
                    "quote": "KRW",
                    "rate": 1400,
                }
            }
        })
        rates = currency_rates_from_external_signals({"fxRates": "KRW=1\nUSD=1400"}, signals)
        positions = apply_position_base_currency_values(
            self.sample_positions(),
            rates,
            runtime_fx_currencies_from_external_signals(signals),
            external_signals=signals,
            valuation_basis=BROKER_NET_BASIS,
        )
        summary = portfolio_summary(
            positions,
            account_cash=17.21 * rates["USD"],
            account_currency="KRW",
            fx_rates=rates,
            runtime_fx_currencies=runtime_fx_currencies_from_external_signals(signals),
            valuation_basis=BROKER_NET_BASIS,
            external_signals=signals,
        )

        expected_invested = 743294 + 17111651 + (27909.17 * 1380.4)
        self.assertAlmostEqual(1380.4, rates["USD"])
        self.assertEqual({"USD"}, runtime_fx_currencies_from_external_signals(signals))
        self.assertAlmostEqual(expected_invested, summary.invested)
        self.assertAlmostEqual(expected_invested + (17.21 * 1380.4), summary.account_equity_total)
        self.assertEqual("Toss Securities", summary.valuation["fxContext"]["USD"]["source"])
        self.assertEqual("live", summary.valuation["fxContext"]["USD"]["state"])

    def test_legacy_snapshot_is_not_misrepresented_as_broker_net(self):
        snapshot = account_snapshot_from_monitor_state({
            "accountId": "default",
            "generatedAt": "2026-08-01T00:00:00Z",
            "portfolio": {
                "total": 140000,
                "invested": 140000,
                "cash": 0,
                "markets": [],
                "sectors": [],
                "concentration": 100,
            },
            "positions": {
                "MSTR": {
                    "symbol": "MSTR",
                    "name": "Strategy",
                    "currency": "USD",
                    "market_value": 100,
                    "market_value_krw": 140000,
                },
            },
        })

        self.assertEqual("legacy-unknown", snapshot.portfolio.valuation_basis)
        self.assertEqual(140000, snapshot.portfolio.mark_to_market_total)
        self.assertEqual("legacy-unknown", snapshot.positions[0].account_value_basis)
        self.assertEqual("legacy-unknown", snapshot.metadata["valuationCompatibility"]["state"])


if __name__ == "__main__":
    unittest.main()
