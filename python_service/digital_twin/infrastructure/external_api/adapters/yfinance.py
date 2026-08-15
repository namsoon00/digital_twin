from typing import Dict, Iterable, List

from ....application.external_data.contracts import CollectionJob, CollectionPartition, DatasetDescriptor, ExternalSubject
from .base import empty_signals, equity_partitions, legacy_provider, observation, position_for, source_as_of


PROFILE_POLICIES = {
    "price": {
        "cadence": 1800,
        "freshness": 3600,
        "priority": 60,
        "revisionMode": "none",
        "materiality": "market-movement",
    },
    "options": {
        "cadence": 3600,
        "freshness": 7200,
        "priority": 25,
        "revisionMode": "none",
        "materiality": "revision",
    },
    "news": {
        "cadence": 86400,
        "freshness": 172800,
        "priority": 20,
        "revisionMode": "changes",
        "materiality": "source-revision",
    },
    "analyst": {
        "cadence": 604800,
        "freshness": 1209600,
        "priority": 20,
        "revisionMode": "changes",
        "materiality": "source-revision",
    },
    "fundamental": {
        "cadence": 86400,
        "freshness": 172800,
        "priority": 35,
        "revisionMode": "changes",
        "materiality": "source-revision",
    },
}


class YFinanceProfileAdapter:
    def __init__(self, profile: str):
        normalized = str(profile or "").strip()
        if normalized not in PROFILE_POLICIES:
            raise ValueError("Unsupported yfinance profile: " + normalized)
        policy = PROFILE_POLICIES[normalized]
        self.profile = normalized
        title = normalized[:1].upper() + normalized[1:]
        self.descriptor = DatasetDescriptor(
            dataset_id="yfinance." + normalized,
            provider_id="yfinance",
            capability="polling-snapshot",
            cadence_seconds=policy["cadence"],
            freshness_seconds=policy["freshness"],
            priority=policy["priority"],
            rate_limit_seconds=1,
            enabled_setting="externalYFinanceEnabled",
            cadence_setting="externalDataYFinance" + title + "CadenceSeconds",
            freshness_setting="externalDataYFinance" + title + "FreshnessSeconds",
            max_partitions_setting="externalDataYFinanceMaxSymbols",
            max_partitions=100,
            revision_mode=policy["revisionMode"],
            materiality_policy=policy["materiality"],
        )

    def partitions(self, subjects: Iterable[ExternalSubject], _settings: Dict[str, object]) -> List[CollectionPartition]:
        return equity_partitions(self.descriptor, subjects)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        try:
            import yfinance as yf  # noqa: PLC0415 - optional external provider dependency.
        except Exception as error:  # noqa: BLE001
            raise RuntimeError("yfinance package unavailable: " + str(error)) from error
        provider = legacy_provider(settings, externalYFinanceEnabled="1", externalYFinanceMaxSymbols="1")
        position = position_for(job.subject)
        query_symbol = provider.yfinance_query_symbol(position)
        payload = provider.fetch_yfinance_symbol(
            yf,
            job.subject.symbol,
            query_symbol,
            profiles=[self.profile],
        )
        if not payload.get("modulesCollected"):
            raise RuntimeError("yfinance " + self.profile + " returned no usable modules")
        signals = empty_signals()
        signals["yfinanceData"][job.subject.symbol] = payload
        provider.merge_yfinance_summaries(signals, job.subject.symbol, payload)
        fragment = {
            key: value
            for key, value in signals.items()
            if key in {"yfinanceData", "equityQuotes", "companyOverviews", "earningsReports"} and value
        }
        as_of = source_as_of(payload, payload.get("collectedAt"))
        return observation(
            self.descriptor,
            job.subject.symbol,
            fragment,
            preferred_source_as_of=as_of,
            watermark={"profile": self.profile, "sourceAsOf": as_of},
        )
