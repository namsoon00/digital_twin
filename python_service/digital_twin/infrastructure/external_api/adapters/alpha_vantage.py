from typing import Dict, Iterable, List

from ....application.external_data.contracts import CollectionJob, CollectionPartition, DatasetDescriptor, ExternalSubject
from .base import empty_signals, equity_partitions, legacy_provider, observation, position_for, require_payload, source_as_of


def is_us_equity(subject: ExternalSubject) -> bool:
    return subject.market in {"US", "NASDAQ", "NYSE", "AMEX"} or subject.currency == "USD"


class AlphaVantageQuoteAdapter:
    def __init__(self, daily_budget: int = 20, rate_limit_seconds: int = 15):
        self.descriptor = DatasetDescriptor(
            dataset_id="alpha.quote",
            provider_id="alpha-vantage",
            capability="polling-snapshot",
            cadence_seconds=21600,
            freshness_seconds=86400,
            priority=25,
            rate_limit_seconds=max(0, int(rate_limit_seconds or 15)),
            daily_request_budget=max(0, int(daily_budget or 20)),
            enabled_setting="externalAlphaEnabled",
            cadence_setting="externalDataAlphaQuoteCadenceSeconds",
            freshness_setting="externalDataAlphaQuoteFreshnessSeconds",
            max_partitions_setting="externalAlphaMaxSymbols",
            max_partitions=3,
            revision_mode="none",
            materiality_policy="market-movement",
        )

    def partitions(self, subjects: Iterable[ExternalSubject], settings: Dict[str, object]) -> List[CollectionPartition]:
        if not str(settings.get("alphaVantageApiKey") or "").strip():
            return []
        return equity_partitions(self.descriptor, subjects, is_us_equity)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        provider = legacy_provider(
            settings,
            externalAlphaEnabled="1",
            externalAlphaRelatedSymbolsEnabled="0",
            externalAlphaRelatedMaxSymbols="0",
            externalAlphaMaxSymbols="1",
        )
        signals = empty_signals()
        provider.add_alpha_vantage(signals, [position_for(job.subject)])
        quote = require_payload(signals, "equityQuotes", job.subject.symbol)
        fragment = {"equityQuotes": {job.subject.symbol: quote}}
        as_of = source_as_of(quote, signals.get("fetchedAt"))
        return observation(
            self.descriptor,
            job.subject.symbol,
            fragment,
            preferred_source_as_of=as_of,
            watermark={"latestTradingDay": str(quote.get("latestTradingDay") or "")},
        )
