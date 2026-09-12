import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.read_models.application.instrument_valuation_query_service import InstrumentValuationQueryService
from digital_twin.modules.news_intelligence.domain.company_knowledge import (
    build_company_knowledge,
    company_valuation_context,
)
from digital_twin.modules.portfolio.domain.instrument_valuation import InstrumentValuationQuery
from digital_twin.modules.portfolio.domain.portfolio import (
    AccountSnapshot,
    PortfolioSummary,
    Position,
    utc_now_iso,
)


class StubMonitorStore:
    def __init__(self, previous):
        self.previous = previous


class InstrumentValuationQueryTests(unittest.TestCase):
    def external_signals(self):
        stamp = utc_now_iso()
        stored = build_company_knowledge(
            "035720",
            overview={
                "provider": "yfinance",
                "fetchedAt": "2026-01-01T00:00:00Z",
                "peRatio": 7.0,
                "forwardPE": 18.0,
                "trailingEPS": 900.0,
            },
        )
        return {
            "companyKnowledge": {"035720": stored},
            "companyOverviews": {
                "035720": {
                    "provider": "KIS Open API",
                    "source": "KIS inquire-price",
                    "fetchedAt": stamp,
                    "peRatio": 31.13,
                    "forwardPE": 0.0,
                    "pbr": 1.35,
                    "trailingEPS": 1110.0,
                    "epsPeriod": "ttm",
                    "historicalPERs": [20.0, 24.0, 28.0, 32.0],
                }
            },
            "yfinanceData": {
                "035720": {
                    "provider": "yfinance",
                    "collectedAt": stamp,
                    "info": {
                        "forwardPE": 19.25,
                        "dividendYield": 0.21,
                    },
                }
            },
        }

    def snapshot_state(self):
        snapshot = AccountSnapshot(
            account_id="default",
            account_label="테스트 계정",
            provider="test",
            mode="live",
            status="정상",
            generated_at=utc_now_iso(),
            portfolio=PortfolioSummary(
                total=10_000_000,
                invested=3_540_000,
                cash=6_460_000,
                markets=[],
                sectors=[],
                concentration=35.4,
            ),
            positions=[Position(
                symbol="035720",
                name="카카오",
                market="KR",
                currency="KRW",
                quantity=100,
                current_price=35_400,
                updated_at=utc_now_iso(),
                source="holding",
            )],
            external_signals=self.external_signals(),
        )
        return snapshot.to_monitor_state()

    def _assert_fresh_kis_per_wins_stored_value_and_zero_does_not_mask_forward_per(self):
        context = company_valuation_context(self.external_signals(), "035720")

        self.assertEqual("available", context["perStatus"])
        self.assertEqual(31.13, context["metrics"]["peRatio"])
        self.assertEqual(19.25, context["metrics"]["forwardPE"])
        self.assertEqual(1.35, context["metrics"]["pbr"])
        self.assertEqual(0.21, context["metrics"]["dividendYieldPct"])
        self.assertIn("KIS Open API", context["sourceProviders"])

    def _assert_query_exposes_market_multiples_and_auditable_fair_value_without_action(self):
        service = InstrumentValuationQueryService(
            monitor_store=StubMonitorStore({"default": self.snapshot_state()}),
            settings={"aiValuationAutoProposalEnabled": "1"},
        )

        payload = service.query(InstrumentValuationQuery("035720", "default"))

        self.assertEqual("ok", payload["status"])
        self.assertEqual("reference-only", payload["decisionRole"])
        self.assertEqual("holding", payload["instrument"]["scope"])
        self.assertEqual(31.13, payload["marketMetrics"]["currentPER"])
        self.assertEqual(19.25, payload["marketMetrics"]["forwardPER"])
        self.assertEqual(1110.0, payload["marketMetrics"]["trailingEPS"])
        self.assertEqual("ttm", payload["marketMetrics"]["trailingEPSPeriod"])
        self.assertGreater(payload["valuation"]["fairValue"]["base"], 0)
        self.assertTrue(payload["valuation"]["multipleBand"]["evidenceBacked"])
        self.assertEqual(4, payload["valuation"]["multipleBand"]["sampleCount"])
        self.assertFalse(payload["valuation"]["quality"]["decisionEligible"])
        for action_key in ("action", "decision", "recommendedAction"):
            self.assertNotIn(action_key, payload)
            self.assertNotIn(action_key, payload["valuation"])

    def _assert_negative_eps_is_explained_as_non_meaningful_per(self):
        signals = {
            "companyKnowledge": {
                "LOSS": build_company_knowledge(
                    "LOSS",
                    overview={
                        "provider": "yfinance",
                        "fetchedAt": utc_now_iso(),
                        "peRatio": 0,
                        "trailingEPS": -2.5,
                    },
                )
            }
        }

        context = company_valuation_context(signals, "LOSS")

        self.assertEqual("not-meaningful-loss", context["perStatus"])
        self.assertEqual(-2.5, context["metrics"]["trailingEPS"])

    def _assert_query_does_not_expose_a_stale_positive_per_for_a_loss_company(self):
        state = self.snapshot_state()
        position = state["positions"].pop("035720")
        position.update({"symbol": "LOSS", "name": "적자기업"})
        state["positions"]["LOSS"] = position
        state["externalSignals"] = {
            "companyKnowledge": {
                "LOSS": build_company_knowledge(
                    "LOSS",
                    overview={
                        "provider": "yfinance",
                        "fetchedAt": "2026-01-01T00:00:00Z",
                        "peRatio": 12.0,
                        "trailingEPS": 1.0,
                    },
                )
            },
            "companyOverviews": {
                "LOSS": {
                    "provider": "KIS Open API",
                    "fetchedAt": utc_now_iso(),
                    "peRatio": 0,
                    "trailingEPS": -2.5,
                }
            },
        }
        service = InstrumentValuationQueryService(
            monitor_store=StubMonitorStore({"default": state}),
            settings={},
        )

        payload = service.query(InstrumentValuationQuery("LOSS", "default"))

        self.assertEqual("not-meaningful-loss", payload["marketMetrics"]["perStatus"])
        self.assertIsNone(payload["marketMetrics"]["currentPER"])

    def test_instrument_valuation_read_model_contract(self):
        self._assert_fresh_kis_per_wins_stored_value_and_zero_does_not_mask_forward_per()
        self._assert_query_exposes_market_multiples_and_auditable_fair_value_without_action()
        self._assert_negative_eps_is_explained_as_non_meaningful_per()
        self._assert_query_does_not_expose_a_stale_positive_per_for_a_loss_company()

    def test_trailing_eps_never_inherits_forecast_period(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        original = InstrumentValuationQueryService(
            monitor_store=StubMonitorStore({"default": self.snapshot_state()}), settings={},
        )
        forecast = SimpleNamespace(
            rows=[{"epsScenario": {"period": "forward-12m"}}],
            status="reference-only", model_service_version="test",
        )
        with patch.object(original.valuation_service, "evaluate", return_value=forecast):
            payload = original.query(InstrumentValuationQuery("035720", "default"))
        self.assertEqual("ttm", payload["marketMetrics"]["trailingEPSPeriod"])


if __name__ == "__main__":
    unittest.main()
