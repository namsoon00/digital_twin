"""Cross-process HTTP admission using the existing external provider ledger."""

from digital_twin.modules.market_data.public import DatasetDescriptor, bounded_int
from .external_signal_utils import ExternalRateLimited, api_error_text, guarded_external_call


class ProviderHttpBudget:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings

    def call(self, fetch):
        descriptor = DatasetDescriptor(
            dataset_id="http.gdelt", provider_id="http.gdelt", capability="http-admission",
            cadence_seconds=60, freshness_seconds=60,
            rate_limit_seconds=bounded_int(self.settings.get("newsCollectionGdeltRateLimitSeconds"), 6, 6, 3600),
            daily_request_budget=bounded_int(self.settings.get("newsCollectionGdeltDailyRequestBudget"), 240, 1, 14400),
            circuit_cooldown_seconds=300,
        )
        permit = self.store.reserve_provider_call(descriptor)
        if not permit.get("allowed"):
            raise ExternalRateLimited("GDELT " + str(permit.get("reason")) + " until " + str(permit.get("nextAllowedAt")),
                str(permit.get("nextAllowedAt") or ""), str(permit.get("reason") or "rate-limited"))
        try:
            # The durable ledger owns the circuit. Use a fresh transport guard only
            # to normalize HTTP errors/Retry-After, never a second local cooldown.
            result = guarded_external_call(self.settings, "GDELT News", "provider", fetch, state={}, attempts=1)
        except Exception as error:
            self.store.record_http_failure(descriptor, api_error_text(error), getattr(error, "retry_at", ""))
            raise
        self.store.mark_provider_success(descriptor)
        return result


def news_http_budget(settings):
    from . import operational_store as stores
    return ProviderHttpBudget(stores.external_data_store(settings), settings)
