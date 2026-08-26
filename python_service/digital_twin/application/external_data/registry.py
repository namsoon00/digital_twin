from typing import Dict, Iterable, List

from .contracts import (
    CollectionPartition,
    ExternalDatasetAdapter,
    ExternalSubject,
    FollowupCollectionRequest,
    SourceObservation,
)


class ExternalDatasetRegistry:
    """Typed registry that keeps provider branching out of the scheduler."""

    def __init__(self, adapters: Iterable[ExternalDatasetAdapter] = None):
        self._adapters: Dict[str, ExternalDatasetAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: ExternalDatasetAdapter) -> None:
        dataset_id = str(adapter.descriptor.dataset_id or "").strip()
        if not dataset_id:
            raise ValueError("External dataset id is required")
        if dataset_id in self._adapters:
            raise ValueError("Duplicate external dataset: " + dataset_id)
        self._adapters[dataset_id] = adapter

    def adapter(self, dataset_id: str) -> ExternalDatasetAdapter:
        adapter = self._adapters.get(str(dataset_id or ""))
        if not adapter:
            raise KeyError("External dataset adapter not registered: " + str(dataset_id or ""))
        return adapter

    def adapters(self) -> List[ExternalDatasetAdapter]:
        return [self._adapters[key] for key in sorted(self._adapters)]

    def desired_partitions(
        self,
        subjects: Iterable[ExternalSubject],
        settings: Dict[str, object],
    ) -> List[CollectionPartition]:
        rows: List[CollectionPartition] = []
        subject_rows = list(subjects or [])
        for adapter in self.adapters():
            descriptor = adapter.descriptor
            if not descriptor.enabled(settings):
                continue
            if descriptor.partition_strategy == "followup":
                continue
            partitions = adapter.partitions(subject_rows, settings)
            rows.extend(partitions[:descriptor.resolved_max_partitions(settings)])
        return rows

    def static_dataset_ids(self, settings: Dict[str, object]) -> List[str]:
        return [
            adapter.descriptor.dataset_id
            for adapter in self.adapters()
            if adapter.descriptor.enabled(settings)
            and adapter.descriptor.partition_strategy != "followup"
        ]

    def followups(
        self,
        source_dataset_id: str,
        observation: SourceObservation,
        settings: Dict[str, object],
    ) -> List[tuple]:
        source = self.adapter(source_dataset_id)
        planner = getattr(source, "followup_requests", None)
        if not callable(planner):
            return []
        plans = []
        for request in planner(observation, settings) or []:
            if not isinstance(request, FollowupCollectionRequest):
                continue
            target = self.adapter(request.dataset_id)
            descriptor = target.descriptor
            if not descriptor.enabled(settings) or descriptor.partition_strategy != "followup":
                continue
            plans.append((descriptor, request))
        return plans

    def descriptors(self, settings: Dict[str, object] = None) -> List[Dict[str, object]]:
        configured = dict(settings or {})
        return [
            {
                "datasetId": adapter.descriptor.dataset_id,
                "providerId": adapter.descriptor.provider_id,
                "capability": adapter.descriptor.capability,
                "priority": adapter.descriptor.priority,
                "materialityPolicy": adapter.descriptor.materiality_policy,
                "partitionStrategy": adapter.descriptor.partition_strategy,
                "completionMode": adapter.descriptor.completion_mode,
                "enabled": adapter.descriptor.enabled(configured),
                "cadenceSeconds": adapter.descriptor.resolved_cadence_seconds(configured),
                "freshnessSeconds": adapter.descriptor.resolved_freshness_seconds(configured),
                "maxPartitions": adapter.descriptor.resolved_max_partitions(configured),
            }
            for adapter in self.adapters()
        ]
