from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Protocol


def bounded_int(value: object, fallback: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def setting_enabled(settings: Dict[str, object], key: str, fallback: bool = True) -> bool:
    raw = settings.get(key)
    if raw in (None, ""):
        return fallback
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}


@dataclass(frozen=True)
class ExternalSubject:
    subject_key: str
    symbol: str = ""
    name: str = ""
    market: str = ""
    currency: str = ""
    sector: str = ""
    source: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "subjectKey": self.subject_key,
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "currency": self.currency,
            "sector": self.sector,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        values = dict(payload or {})
        symbol = str(values.get("symbol") or values.get("subjectKey") or "").upper().strip()
        return cls(
            subject_key=str(values.get("subjectKey") or symbol or "global").strip(),
            symbol=symbol,
            name=str(values.get("name") or symbol),
            market=str(values.get("market") or "").upper().strip(),
            currency=str(values.get("currency") or "").upper().strip(),
            sector=str(values.get("sector") or "").strip(),
            source=str(values.get("source") or "").strip(),
        )


@dataclass(frozen=True)
class DatasetDescriptor:
    dataset_id: str
    provider_id: str
    capability: str
    cadence_seconds: int
    freshness_seconds: int
    priority: int = 50
    rate_limit_seconds: int = 0
    daily_request_budget: int = 0
    failure_threshold: int = 2
    circuit_cooldown_seconds: int = 1800
    enabled_setting: str = ""
    cadence_setting: str = ""
    freshness_setting: str = ""
    max_partitions_setting: str = ""
    max_partitions: int = 100
    revision_mode: str = "current"
    materiality_policy: str = "revision"
    partition_strategy: str = "subjects"
    completion_mode: str = "recurring"

    def enabled(self, settings: Dict[str, object]) -> bool:
        return setting_enabled(settings, self.enabled_setting, True) if self.enabled_setting else True

    def resolved_cadence_seconds(self, settings: Dict[str, object]) -> int:
        if not self.cadence_setting:
            return max(10, int(self.cadence_seconds or 60))
        return bounded_int(
            settings.get(self.cadence_setting),
            self.cadence_seconds,
            10,
            30 * 86400,
        )

    def resolved_freshness_seconds(self, settings: Dict[str, object]) -> int:
        if not self.freshness_setting:
            return max(10, int(self.freshness_seconds or self.cadence_seconds or 60))
        return bounded_int(
            settings.get(self.freshness_setting),
            self.freshness_seconds,
            10,
            90 * 86400,
        )

    def resolved_max_partitions(self, settings: Dict[str, object]) -> int:
        if not self.max_partitions_setting:
            return max(1, int(self.max_partitions or 1))
        return bounded_int(
            settings.get(self.max_partitions_setting),
            self.max_partitions,
            1,
            5000,
        )


@dataclass(frozen=True)
class CollectionPartition:
    dataset_id: str
    partition_key: str
    subject: ExternalSubject
    priority: int = 50

    def to_dict(self) -> Dict[str, object]:
        return {
            "datasetId": self.dataset_id,
            "partitionKey": self.partition_key,
            "priority": self.priority,
            "subject": self.subject.to_dict(),
        }


@dataclass(frozen=True)
class CollectionJob:
    dataset_id: str
    partition_key: str
    provider_id: str
    priority: int
    subject: ExternalSubject
    attempt_count: int = 0
    lease_owner: str = ""
    lease_until: str = ""
    watermark: Dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        values = dict(payload or {})
        return cls(
            dataset_id=str(values.get("datasetId") or ""),
            partition_key=str(values.get("partitionKey") or ""),
            provider_id=str(values.get("providerId") or ""),
            priority=int(values.get("priority") or 0),
            subject=ExternalSubject.from_dict(values.get("subject") or {}),
            attempt_count=int(values.get("attemptCount") or 0),
            lease_owner=str(values.get("leaseOwner") or ""),
            lease_until=str(values.get("leaseUntil") or ""),
            watermark=dict(values.get("watermark") or {}),
        )


@dataclass(frozen=True)
class FollowupCollectionRequest:
    dataset_id: str
    partition_key: str
    subject: ExternalSubject
    watermark: Dict[str, object] = field(default_factory=dict)
    priority: int = 50


@dataclass(frozen=True)
class SourceObservation:
    dataset_id: str
    provider_id: str
    subject_key: str
    source_revision: str
    source_as_of: str
    fetched_at: str
    payload: Dict[str, object]
    quality: Dict[str, object] = field(default_factory=dict)
    watermark: Dict[str, object] = field(default_factory=dict)
    empty_result: bool = False
    retain_previous: bool = False


class ExternalDatasetAdapter(Protocol):
    descriptor: DatasetDescriptor

    def partitions(
        self,
        subjects: Iterable[ExternalSubject],
        settings: Dict[str, object],
    ) -> List[CollectionPartition]:
        ...

    def fetch(
        self,
        job: CollectionJob,
        settings: Dict[str, object],
    ) -> SourceObservation:
        ...
