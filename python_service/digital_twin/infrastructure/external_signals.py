import time
from typing import Callable, Dict

from .external_signal_provider_alpha import ExternalSignalAlphaMixin
from .external_signal_provider_core import ExternalSignalCoreMixin
from .external_signal_provider_market import ExternalSignalMarketMixin
from .external_signal_provider_news import ExternalSignalNewsMixin
from .external_signal_provider_sec import ExternalSignalSecMixin
from .external_signal_provider_yfinance import ExternalSignalYFinanceMixin
from .external_signal_utils import (
    DISABLED_SETTING_VALUES,
    ExternalApiGuard,
    ExternalCircuitOpen,
    ExternalRateLimited,
    BytesFetcher,
    JsonFetcher,
    TextFetcher,
    api_error_text,
    default_json_fetcher,
    parse_iso,
    percent_text,
    retryable_api_error,
    symbol_assignments,
    symbol_list,
)
from .operational_store import crypto_market_signal_cache, external_signal_cache, research_evidence_store
from .settings import runtime_settings


class ExternalSignalProvider(
    ExternalSignalCoreMixin,
    ExternalSignalAlphaMixin,
    ExternalSignalSecMixin,
    ExternalSignalMarketMixin,
    ExternalSignalNewsMixin,
    ExternalSignalYFinanceMixin,
):
    def __init__(
        self,
        settings: Dict[str, str] = None,
        cache=None,
        evidence_store=None,
        fetch_json: JsonFetcher = None,
        fetch_text: TextFetcher = None,
        fetch_bytes: BytesFetcher = None,
        sleep: Callable[[float], None] = None,
        crypto_cache=None,
    ):
        self.settings = settings or runtime_settings()
        uses_default_cache = cache is None
        self.cache = cache or external_signal_cache(self.settings)
        # Injected aggregate caches are normally isolated unit-test doubles.
        # Production providers additionally share one compact global crypto
        # snapshot so portfolio/settings cache-key changes cannot hide it.
        self.crypto_cache = crypto_cache if crypto_cache is not None else (
            crypto_market_signal_cache(self.settings) if uses_default_cache else None
        )
        self.evidence_store = evidence_store or research_evidence_store(self.settings)
        # Provider-specific timeouts use the standard transport only. Test
        # and vendor adapters keep their existing two-argument contract.
        self._uses_default_json_fetcher = fetch_json is None
        self.fetch_json = fetch_json or self.default_fetch_json
        self.fetch_text = fetch_text or self.default_fetch_text
        self.fetch_bytes = fetch_bytes or self.default_fetch_bytes
        self.sleep = sleep or time.sleep
        self.provider_state: Dict[str, object] = {}
