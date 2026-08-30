import hashlib
import json
from typing import Dict, Iterable, List

from ....application.external_data.contracts import (
    CollectionPartition,
    DatasetDescriptor,
    ExternalSubject,
    SourceObservation,
)
from ....domain.portfolio import Position, utc_now_iso
from ...external_signals import ExternalSignalProvider


class MemoryCache:
    def __init__(self):
        self.payload: Dict[str, object] = {}

    def load(self):
        return dict(self.payload)

    def replace(self, payload):
        self.payload = dict(payload or {})


class NoopEvidenceStore:
    @staticmethod
    def latest(**_kwargs):
        return []

    @staticmethod
    def upsert_many(_items):
        return 0


def empty_signals() -> Dict[str, object]:
    return {
        "fetchedAt": utc_now_iso(),
        "cryptoFetchedAt": "",
        "cryptoLastAttemptAt": "",
        "equityQuotes": {},
        "officialDailyPrices": {},
        "cryptoMarkets": {},
        "macro": {},
        "fxRates": {},
        "secFilings": {},
        "dartDisclosures": {},
        "newsHeadlines": {},
        "companyOverviews": {},
        "earningsReports": {},
        "yfinanceData": {},
        "researchEvidence": {},
        "statuses": [],
    }


def legacy_provider(settings: Dict[str, object], **enabled_overrides) -> ExternalSignalProvider:
    configured = {
        **dict(settings or {}),
        "_externalDataLegacyCollection": "1",
        "externalAlphaEnabled": "0",
        "externalAlphaFundamentalsEnabled": "0",
        "externalCoinGeckoEnabled": "0",
        "externalFredEnabled": "0",
        "externalDartEnabled": "0",
        "externalSecEnabled": "0",
        "externalNewsEnabled": "0",
        "externalFxRateEnabled": "0",
        "externalYFinanceEnabled": "0",
        "externalApiRateLimitSeconds": "0",
        "externalAlphaRateLimitSeconds": "0",
        "externalAlphaDailyRequestBudget": "0",
        **{key: value for key, value in enabled_overrides.items()},
    }
    aggregate = MemoryCache()
    return ExternalSignalProvider(
        settings=configured,
        cache=aggregate,
        company_cache=MemoryCache(),
        crypto_cache=MemoryCache(),
        evidence_store=NoopEvidenceStore(),
    )


def position_for(subject: ExternalSubject) -> Position:
    return Position(
        symbol=subject.symbol or subject.subject_key,
        name=subject.name or subject.symbol or subject.subject_key,
        market=subject.market,
        currency=subject.currency,
        sector=subject.sector or "기타",
        source=subject.source or "external-data",
    )


def global_partition(descriptor: DatasetDescriptor) -> List[CollectionPartition]:
    return [CollectionPartition(
        dataset_id=descriptor.dataset_id,
        partition_key="global",
        subject=ExternalSubject(subject_key="global", source="global"),
        priority=descriptor.priority,
    )]


def equity_partitions(
    descriptor: DatasetDescriptor,
    subjects: Iterable[ExternalSubject],
    predicate=None,
) -> List[CollectionPartition]:
    selected = []
    seen = set()
    for subject in sorted(list(subjects or []), key=lambda item: item.subject_key):
        symbol = str(subject.symbol or subject.subject_key or "").upper().strip()
        if not symbol or symbol in seen or (predicate and not predicate(subject)):
            continue
        seen.add(symbol)
        selected.append(CollectionPartition(
            dataset_id=descriptor.dataset_id,
            partition_key=symbol,
            subject=subject,
            priority=descriptor.priority,
        ))
    return selected


def stable_value(value: object):
    if isinstance(value, dict):
        return {
            str(key): stable_value(item)
            for key, item in value.items()
            if str(key) not in {"fetchedAt", "collectedAt", "checkedAt", "cryptoLastAttemptAt"}
        }
    if isinstance(value, list):
        return [stable_value(item) for item in value]
    return value


def source_revision(payload: Dict[str, object], preferred: object = "") -> str:
    if str(preferred or "").strip():
        return str(preferred or "").strip()[:191]
    raw = json.dumps(stable_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def source_as_of(payload: object, fallback: str = "") -> str:
    candidates: List[str] = []
    timestamp_keys = {
        "sourceAsOf",
        "lastUpdated",
        "observationDate",
        "receiptDate",
        "filingDate",
        "reportDate",
        "reportedDate",
        "publishedAt",
        "date",
    }

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key) in timestamp_keys and str(item or "").strip():
                    candidates.append(str(item).strip())
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value[:100]:
                visit(item)

    visit(payload)
    return max(candidates) if candidates else str(fallback or "")


def observation(
    descriptor: DatasetDescriptor,
    subject_key: str,
    fragment: Dict[str, object],
    preferred_revision: str = "",
    preferred_source_as_of: str = "",
    watermark: Dict[str, object] = None,
    quality: Dict[str, object] = None,
    empty_result: bool = False,
    retain_previous: bool = False,
) -> SourceObservation:
    fetched_at = utc_now_iso()
    return SourceObservation(
        dataset_id=descriptor.dataset_id,
        provider_id=descriptor.provider_id,
        subject_key=str(subject_key or "global"),
        source_revision=source_revision(fragment, preferred_revision),
        source_as_of=preferred_source_as_of or source_as_of(fragment, fetched_at),
        fetched_at=fetched_at,
        payload=fragment,
        quality=dict(quality or {"dataUsable": True, "provider": descriptor.provider_id}),
        watermark=dict(watermark or {}),
        empty_result=bool(empty_result),
        retain_previous=bool(retain_previous),
    )


def require_payload(signals: Dict[str, object], key: str, subject_key: str = "") -> object:
    value = signals.get(key)
    if subject_key and isinstance(value, dict):
        value = value.get(subject_key)
    if value:
        return value
    messages = [
        str(item.get("message") or "")
        for item in signals.get("statuses") or []
        if isinstance(item, dict) and str(item.get("message") or "")
    ]
    raise RuntimeError(" / ".join(messages[:3]) or (key + " returned no usable data"))
