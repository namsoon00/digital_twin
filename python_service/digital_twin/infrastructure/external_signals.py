import time
from typing import Callable, Dict

from .external_signal_provider_alpha import ExternalSignalAlphaMixin
from .external_signal_provider_core import ExternalSignalCoreMixin
from .external_signal_provider_market import ExternalSignalMarketMixin
from .external_signal_provider_news import ExternalSignalNewsMixin
from .external_signal_provider_sec import ExternalSignalSecMixin
from .external_signal_utils import (
    DISABLED_SETTING_VALUES,
    ExternalApiGuard,
    ExternalCircuitOpen,
    ExternalRateLimited,
    JsonFetcher,
    api_error_text,
    default_json_fetcher,
    parse_iso,
    percent_text,
    retryable_api_error,
    symbol_assignments,
    symbol_list,
)
from .operational_store import external_signal_cache, research_evidence_store
from .settings import runtime_settings


class ExternalSignalProvider(
    ExternalSignalCoreMixin,
    ExternalSignalAlphaMixin,
    ExternalSignalSecMixin,
    ExternalSignalMarketMixin,
    ExternalSignalNewsMixin,
):
    def __init__(
        self,
        settings: Dict[str, str] = None,
        cache=None,
        evidence_store=None,
        fetch_json: JsonFetcher = None,
        sleep: Callable[[float], None] = None,
    ):
        self.settings = settings or runtime_settings()
        self.cache = cache or external_signal_cache(self.settings)
        self.evidence_store = evidence_store or research_evidence_store(self.settings)
        self.fetch_json = fetch_json or self.default_fetch_json
        self.sleep = sleep or time.sleep
        self.provider_state: Dict[str, object] = {}
