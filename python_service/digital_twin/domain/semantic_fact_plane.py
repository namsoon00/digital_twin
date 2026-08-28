"""Bitemporal facts and bounded semantic changes for reasoning execution.

This module describes *what changed* and the exact source-time boundary used
by a reasoning turn.  It never interprets a value or chooses an investment
action.  The immutable packet is safe to persist in an event, reuse in a
projection audit, and replay at the same point in time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Dict, Iterable, Mapping, Tuple


SEMANTIC_FACT_ENVELOPE_VERSION = "semantic-fact-envelope-v1"
SEMANTIC_FACT_SLICE_VERSION = "semantic-fact-slice-v1"
SEMANTIC_CHANGE_SET_VERSION = "semantic-change-set-v1"


def _texts(values: object, uppercase: bool = False) -> Tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    if values is None or isinstance(values, Mapping):
        return ()
    try:
        candidates = list(values)
    except TypeError:
        return ()
    result = []
    for value in candidates:
        text = str(value or "").strip()
        if uppercase:
            text = text.upper()
        if text and text not in result:
            result.append(text)
    return tuple(sorted(result))


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def semantic_fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FactEnvelope:
    fact_id: str
    subject_id: str
    fact_type: str
    source_event_id: str
    observed_at: str
    valid_from: str
    ingested_at: str
    source: str = "domain-event"
    revision: str = ""
    valid_to: str = ""
    quality_state: str = "verified-source-boundary"
    version: str = SEMANTIC_FACT_ENVELOPE_VERSION

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FactRevisionVector:
    subject_id: str
    revisions: Tuple[Tuple[str, str], ...] = ()

    @classmethod
    def from_mapping(cls, subject_id: str, values: Mapping[str, object] = None):
        return cls(
            subject_id=str(subject_id or "").upper().strip(),
            revisions=tuple(sorted(
                (str(key or "").strip(), str(value or "").strip())
                for key, value in dict(values or {}).items()
                if str(key or "").strip() and str(value or "").strip()
            )),
        )

    @property
    def fingerprint(self) -> str:
        return semantic_fingerprint({
            "subjectId": self.subject_id,
            "revisions": list(self.revisions),
        })

    def to_dict(self) -> Dict[str, object]:
        return {
            "subjectId": self.subject_id,
            "revisions": {key: value for key, value in self.revisions},
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class FactSlice:
    subject_id: str
    scope_families: Tuple[str, ...]
    dependency_keys: Tuple[str, ...]
    changed_fields: Tuple[str, ...]
    fact_types: Tuple[str, ...]
    revision_vector: FactRevisionVector
    observed_at: str
    version: str = SEMANTIC_FACT_SLICE_VERSION

    @property
    def fingerprint(self) -> str:
        return semantic_fingerprint({
            "subjectId": self.subject_id,
            "scopeFamilies": list(self.scope_families),
            "dependencyKeys": list(self.dependency_keys),
            "changedFields": list(self.changed_fields),
            "factTypes": list(self.fact_types),
            "revisionVector": self.revision_vector.to_dict(),
            "observedAt": self.observed_at,
        })

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "subjectId": self.subject_id,
            "scopeFamilies": list(self.scope_families),
            "dependencyKeys": list(self.dependency_keys),
            "changedFields": list(self.changed_fields),
            "factTypes": list(self.fact_types),
            "revisionVector": self.revision_vector.to_dict(),
            "observedAt": self.observed_at,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class SemanticChangeSet:
    change_set_id: str
    source_event_ids: Tuple[str, ...]
    account_ids: Tuple[str, ...]
    observed_at: str
    requested_at: str
    fact_envelopes: Tuple[FactEnvelope, ...] = ()
    fact_slices: Tuple[FactSlice, ...] = ()
    authoritative_fact_boundary: bool = False
    authoritative_dependency_boundary: bool = False
    version: str = SEMANTIC_CHANGE_SET_VERSION

    @property
    def fingerprint(self) -> str:
        return semantic_fingerprint({
            "sourceEventIds": list(self.source_event_ids),
            "accountIds": list(self.account_ids),
            "observedAt": self.observed_at,
            "requestedAt": self.requested_at,
            "factEnvelopes": [item.to_dict() for item in self.fact_envelopes],
            "factSlices": [item.to_dict() for item in self.fact_slices],
            "authoritativeFactBoundary": self.authoritative_fact_boundary,
            "authoritativeDependencyBoundary": self.authoritative_dependency_boundary,
        })

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "changeSetId": self.change_set_id,
            "sourceEventIds": list(self.source_event_ids),
            "accountIds": list(self.account_ids),
            "observedAt": self.observed_at,
            "requestedAt": self.requested_at,
            "factEnvelopes": [item.to_dict() for item in self.fact_envelopes],
            "factSlices": [item.to_dict() for item in self.fact_slices],
            "authoritativeFactBoundary": self.authoritative_fact_boundary,
            "authoritativeDependencyBoundary": self.authoritative_dependency_boundary,
            "fingerprint": self.fingerprint,
        }


def semantic_change_set(
    *,
    source_events: Iterable[Mapping[str, object]],
    source_event_ids: Iterable[object],
    account_ids: Iterable[object],
    symbols: Iterable[object],
    fact_types: Iterable[object],
    observed_at: str,
    requested_at: str,
    scope_families_by_symbol: Mapping[str, object] = None,
    dependency_keys_by_symbol: Mapping[str, object] = None,
    changed_fields_by_symbol: Mapping[str, object] = None,
    revision_vectors_by_symbol: Mapping[str, object] = None,
    authoritative_fact_boundary: bool = False,
    authoritative_dependency_boundary: bool = False,
) -> SemanticChangeSet:
    """Compile one deterministic change packet from already-governed events."""

    clean_symbols = _texts(symbols, uppercase=True)
    clean_fact_types = _texts(fact_types, uppercase=True)
    clean_event_ids = _texts(source_event_ids)
    clean_accounts = _texts(account_ids)
    events = [dict(item or {}) for item in source_events or [] if isinstance(item, Mapping)]
    envelopes = []
    for event in events:
        payload = dict(event.get("payload") or {}) if isinstance(event.get("payload"), Mapping) else {}
        event_id = str(event.get("event_id") or event.get("eventId") or "").strip()
        event_observed_at = str(
            payload.get("sourceObservedAt")
            or payload.get("sourceAsOf")
            or event.get("occurred_at")
            or event.get("occurredAt")
            or observed_at
        )
        ingested_at = str(event.get("occurred_at") or event.get("occurredAt") or requested_at)
        event_symbols = _texts(
            payload.get("affectedSymbols") or payload.get("symbols") or clean_symbols,
            uppercase=True,
        ) or clean_symbols or ("GLOBAL",)
        event_fact_types = _texts(payload.get("factTypes") or clean_fact_types, uppercase=True) or ("UNKNOWN",)
        event_revisions = dict(payload.get("factRevisionsBySymbol") or {})
        for subject_id in event_symbols:
            for fact_type in event_fact_types:
                identity = {
                    "eventId": event_id,
                    "subjectId": subject_id,
                    "factType": fact_type,
                    "observedAt": event_observed_at,
                }
                envelopes.append(FactEnvelope(
                    fact_id="fact-envelope:" + semantic_fingerprint(identity)[:32],
                    subject_id=subject_id,
                    fact_type=fact_type,
                    source_event_id=event_id,
                    observed_at=event_observed_at,
                    valid_from=event_observed_at,
                    ingested_at=ingested_at,
                    revision=str(event_revisions.get(subject_id) or ""),
                ))
    slices = []
    for subject_id in clean_symbols:
        vector = FactRevisionVector.from_mapping(
            subject_id,
            dict(revision_vectors_by_symbol or {}).get(subject_id) or {},
        )
        slices.append(FactSlice(
            subject_id=subject_id,
            scope_families=_texts(dict(scope_families_by_symbol or {}).get(subject_id) or []),
            dependency_keys=_texts(dict(dependency_keys_by_symbol or {}).get(subject_id) or []),
            changed_fields=_texts(dict(changed_fields_by_symbol or {}).get(subject_id) or []),
            fact_types=clean_fact_types,
            revision_vector=vector,
            observed_at=str(observed_at or ""),
        ))
    identity = {
        "sourceEventIds": list(clean_event_ids),
        "accounts": list(clean_accounts),
        "symbols": list(clean_symbols),
        "observedAt": str(observed_at or ""),
    }
    return SemanticChangeSet(
        change_set_id="semantic-change:" + semantic_fingerprint(identity)[:32],
        source_event_ids=clean_event_ids,
        account_ids=clean_accounts,
        observed_at=str(observed_at or ""),
        requested_at=str(requested_at or ""),
        fact_envelopes=tuple(envelopes),
        fact_slices=tuple(slices),
        authoritative_fact_boundary=bool(authoritative_fact_boundary),
        authoritative_dependency_boundary=bool(authoritative_dependency_boundary),
    )
