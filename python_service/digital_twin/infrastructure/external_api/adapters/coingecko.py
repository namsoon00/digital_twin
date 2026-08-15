from typing import Dict, Iterable, List

from ....application.external_data.contracts import CollectionJob, CollectionPartition, DatasetDescriptor, ExternalSubject
from .base import empty_signals, global_partition, legacy_provider, observation, require_payload, source_as_of


class CoinGeckoMarketAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="coingecko.market",
        provider_id="coingecko",
        capability="bulk-snapshot",
        cadence_seconds=600,
        freshness_seconds=1500,
        priority=90,
        rate_limit_seconds=2,
        enabled_setting="externalCoinGeckoEnabled",
        cadence_setting="externalDataCoinGeckoCadenceSeconds",
        freshness_setting="externalDataCoinGeckoFreshnessSeconds",
        max_partitions=1,
        revision_mode="none",
        materiality_policy="market-movement",
    )

    def partitions(self, _subjects: Iterable[ExternalSubject], _settings: Dict[str, object]) -> List[CollectionPartition]:
        return global_partition(self.descriptor)

    def fetch(self, _job: CollectionJob, settings: Dict[str, object]):
        provider = legacy_provider(settings, externalCoinGeckoEnabled="1")
        signals = empty_signals()
        provider.add_coingecko(signals)
        markets = require_payload(signals, "cryptoMarkets")
        fragment = {"cryptoMarkets": markets}
        as_of = source_as_of(markets, signals.get("cryptoSourceAsOf") or signals.get("cryptoFetchedAt"))
        return observation(
            self.descriptor,
            "global",
            fragment,
            preferred_source_as_of=as_of,
            watermark={"sourceAsOf": as_of},
        )
