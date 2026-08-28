from typing import Dict

from ....application.external_data.registry import ExternalDatasetRegistry
from .coingecko import CoinGeckoMarketAdapter
from .fred import FredMacroAdapter
from .opendart import OpenDartCompanyFactsAdapter, OpenDartDisclosureAdapter, OpenDartDocumentAdapter
from .sec import SecCompanyFactsAdapter, SecDocumentAdapter, SecSubmissionsAdapter
from .yfinance import YFinanceProfileAdapter
from .alpha_vantage import AlphaVantageQuoteAdapter


def default_external_dataset_registry(
    settings: Dict[str, object],
    opendart_corp_code_lookup=None,
) -> ExternalDatasetRegistry:
    configured = dict(settings or {})
    adapters = [
        CoinGeckoMarketAdapter(),
        FredMacroAdapter(),
        OpenDartDisclosureAdapter(opendart_corp_code_lookup),
        OpenDartDocumentAdapter(),
        OpenDartCompanyFactsAdapter(opendart_corp_code_lookup),
        SecSubmissionsAdapter(),
        SecDocumentAdapter(),
        SecCompanyFactsAdapter(),
        YFinanceProfileAdapter("price"),
        YFinanceProfileAdapter("options"),
        YFinanceProfileAdapter("news"),
        YFinanceProfileAdapter("analyst"),
        YFinanceProfileAdapter("fundamental"),
        AlphaVantageQuoteAdapter(
            daily_budget=int(float(str(configured.get("externalAlphaDailyRequestBudget") or 20))),
            rate_limit_seconds=int(float(str(configured.get("externalAlphaRateLimitSeconds") or 15))),
        ),
    ]
    return ExternalDatasetRegistry(adapters)


__all__ = ["default_external_dataset_registry"]
