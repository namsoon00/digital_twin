from typing import Dict, Iterable, List

from ....application.external_data.contracts import CollectionJob, CollectionPartition, DatasetDescriptor, ExternalSubject
from .base import empty_signals, global_partition, legacy_provider, observation, require_payload, source_as_of


class FredMacroAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="fred.macro",
        provider_id="fred",
        capability="bulk-snapshot",
        cadence_seconds=21600,
        freshness_seconds=172800,
        priority=40,
        rate_limit_seconds=1,
        enabled_setting="externalFredEnabled",
        cadence_setting="externalDataFredCadenceSeconds",
        freshness_setting="externalDataFredFreshnessSeconds",
        max_partitions=1,
        revision_mode="changes",
        materiality_policy="published-observation",
    )

    def partitions(self, _subjects: Iterable[ExternalSubject], settings: Dict[str, object]) -> List[CollectionPartition]:
        if not str(settings.get("fredApiKey") or "").strip():
            return []
        return global_partition(self.descriptor)

    def fetch(self, _job: CollectionJob, settings: Dict[str, object]):
        provider = legacy_provider(settings, externalFredEnabled="1")
        signals = empty_signals()
        provider.add_fred(signals)
        macro = require_payload(signals, "macro")
        fragment = {"macro": macro}
        as_of = source_as_of(macro, signals.get("fetchedAt"))
        return observation(
            self.descriptor,
            "global",
            fragment,
            preferred_source_as_of=as_of,
            watermark={"observationDate": as_of},
        )
