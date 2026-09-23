"""Provider-neutral contracts for durable external observations.

The collector owns transport scheduling.  These contracts describe what was
observed and how a later consumer can retrieve that exact observation without
depending on a vendor payload shape or the mutable current-fact row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, Mapping, Tuple


SOURCE_REFERENCE_VERSION = "external-source-reference-v1"
SOURCE_SCHEMA_LEGACY_VERSION = "external-source-legacy-v1"
OFFICIAL_EVIDENCE_CONSUMER_ID = "external-official-evidence"
OFFICIAL_EVIDENCE_PROJECTOR_VERSION = "official-evidence-projection-v5-company-events"
OFFICIAL_EVIDENCE_DATASET_IDS = frozenset({
    "opendart.disclosures",
    "opendart.document",
    "sec.submissions",
    "sec.document",
    "public-data.kr-dividends",
    "public-data.kr-capital-events",
    "public-data.kr-shareholder-rights",
})

DATA_CATEGORIES = frozenset({
    "identity",
    "price_trade",
    "investor_flow",
    "financial",
    "expectation_valuation",
    "company_event",
    "calendar",
    "market_context",
    "account_portfolio",
})

AVAILABILITY_STATES = frozenset({
    "observed",
    "missing",
    "unsupported",
    "not-applicable",
    "unknown",
})

EVIDENCE_BASES = frozenset({"reported", "estimated", "inferred", "derived"})


def _text(value: object) -> str:
    return str(value or "").strip()


def _stable(value: object):
    if isinstance(value, Mapping):
        return {
            str(key): _stable(item)
            for key, item in value.items()
            if str(key) not in {
                "fetchedAt",
                "collectedAt",
                "checkedAt",
                "cryptoLastAttemptAt",
                "lastAttemptAt",
            }
        }
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    return value


def canonical_payload_hash(payload: Mapping[str, object]) -> str:
    raw = json.dumps(
        _stable(dict(payload or {})),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalized_availability(
    value: object,
    *,
    empty_result: bool = False,
    quality: Mapping[str, object] = None,
) -> str:
    explicit = _text(value).lower()
    aliases = {
        "available": "observed",
        "fresh": "observed",
        "empty": "missing",
        "no-data": "missing",
        "not_available": "unsupported",
        "not-supported": "unsupported",
        "not_applicable": "not-applicable",
        "n/a": "not-applicable",
    }
    explicit = aliases.get(explicit, explicit)
    if explicit in AVAILABILITY_STATES:
        return explicit
    details = dict(quality or {})
    quality_state = _text(details.get("availability")).lower()
    quality_state = aliases.get(quality_state, quality_state)
    if quality_state in AVAILABILITY_STATES:
        return quality_state
    if details.get("dataUsable") is False:
        return "missing" if empty_result or details.get("emptyResult") else "unknown"
    if empty_result or details.get("emptyResult"):
        return "missing"
    return "observed"


def _semantic_quality(quality: Mapping[str, object]) -> Dict[str, object]:
    return _stable(dict(quality or {}))


def source_revision_id(
    *,
    dataset_id: str,
    provider_id: str,
    subject_key: str,
    provider_revision: str,
    source_as_of: str,
    payload_hash: str,
    source_schema_version: str,
    availability: str,
    quality: Mapping[str, object] = None,
) -> str:
    identity = {
        "contractVersion": SOURCE_REFERENCE_VERSION,
        "datasetId": _text(dataset_id),
        "providerId": _text(provider_id),
        "subjectKey": _text(subject_key),
        "providerRevision": _text(provider_revision),
        "sourceAsOf": _text(source_as_of),
        "payloadHash": _text(payload_hash),
        "sourceSchemaVersion": _text(source_schema_version) or SOURCE_SCHEMA_LEGACY_VERSION,
        "availability": normalized_availability(availability, quality=quality),
        "quality": _semantic_quality(quality or {}),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceReference:
    dataset_id: str
    provider_id: str
    subject_key: str
    revision_id: str
    provider_revision: str
    payload_hash: str
    source_schema_version: str
    source_as_of: str
    availability: str
    contract_version: str = SOURCE_REFERENCE_VERSION

    def to_dict(self) -> Dict[str, object]:
        return {
            "contractVersion": self.contract_version,
            "datasetId": self.dataset_id,
            "providerId": self.provider_id,
            "subjectKey": self.subject_key,
            "revisionId": self.revision_id,
            "providerRevision": self.provider_revision,
            "payloadHash": self.payload_hash,
            "sourceSchemaVersion": self.source_schema_version,
            "sourceAsOf": self.source_as_of,
            "availability": self.availability,
        }


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
    source_schema_version: str = SOURCE_SCHEMA_LEGACY_VERSION
    availability: str = ""

    def source_reference(self, source_schema_version: str = "") -> SourceReference:
        schema_version = _text(source_schema_version or self.source_schema_version) or SOURCE_SCHEMA_LEGACY_VERSION
        payload_fingerprint = canonical_payload_hash(self.payload)
        availability = normalized_availability(
            self.availability,
            empty_result=self.empty_result,
            quality=self.quality,
        )
        revision_id = source_revision_id(
            dataset_id=self.dataset_id,
            provider_id=self.provider_id,
            subject_key=self.subject_key,
            provider_revision=self.source_revision,
            source_as_of=self.source_as_of,
            payload_hash=payload_fingerprint,
            source_schema_version=schema_version,
            availability=availability,
            quality=self.quality,
        )
        return SourceReference(
            dataset_id=_text(self.dataset_id),
            provider_id=_text(self.provider_id),
            subject_key=_text(self.subject_key),
            revision_id=revision_id,
            provider_revision=_text(self.source_revision),
            payload_hash=payload_fingerprint,
            source_schema_version=schema_version,
            source_as_of=_text(self.source_as_of),
            availability=availability,
        )


@dataclass(frozen=True)
class DatasetSemantics:
    dataset_id: str
    category_ids: Tuple[str, ...]
    output_contract: str
    purpose_ids: Tuple[str, ...] = ()
    supported_markets: Tuple[str, ...] = ()
    asset_kinds: Tuple[str, ...] = ()
    evidence_basis: str = "reported"
    source_schema_version: str = SOURCE_SCHEMA_LEGACY_VERSION
    normalizer_id: str = "external-signals-compat"
    normalizer_version: str = "v1"
    empty_result_semantics: str = "missing"

    def __post_init__(self) -> None:
        unknown = set(self.category_ids) - DATA_CATEGORIES
        if unknown:
            raise ValueError("Unknown external-data categories: " + ", ".join(sorted(unknown)))
        if self.evidence_basis not in EVIDENCE_BASES:
            raise ValueError("Unknown external-data evidence basis: " + self.evidence_basis)

    def to_dict(self) -> Dict[str, object]:
        return {
            "datasetId": self.dataset_id,
            "categoryIds": list(self.category_ids),
            "outputContract": self.output_contract,
            "purposeIds": list(self.purpose_ids),
            "supportedMarkets": list(self.supported_markets),
            "assetKinds": list(self.asset_kinds),
            "evidenceBasis": self.evidence_basis,
            "sourceSchemaVersion": self.source_schema_version,
            "normalizerId": self.normalizer_id,
            "normalizerVersion": self.normalizer_version,
            "emptyResultSemantics": self.empty_result_semantics,
        }
