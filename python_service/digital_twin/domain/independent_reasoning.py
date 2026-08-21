"""Version-neutral contracts for independently executable reasoning engines."""

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping, Tuple

from .events import DomainEvent, ONTOLOGY_REASONING_REQUESTED


INDEPENDENT_REASONING_REQUEST_VERSION = "independent-reasoning-request-v2"
INDEPENDENT_REASONING_RESULT_VERSION = "independent-reasoning-result-v4"


def _texts(values: object, uppercase: bool = False) -> Tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    if isinstance(values, Mapping) or values is None:
        return ()
    try:
        values = list(values)
    except TypeError:
        return ()
    cleaned = []
    for value in values:
        text = str(value or "").strip()
        if uppercase:
            text = text.upper()
        if text and text not in cleaned:
            cleaned.append(text)
    return tuple(sorted(cleaned))


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _event(value: object) -> DomainEvent:
    if isinstance(value, DomainEvent):
        return value
    if isinstance(value, Mapping):
        return DomainEvent.from_dict(dict(value))
    raise TypeError("Reasoning input must be a DomainEvent or mapping")


def reasoning_event_scope(event: object) -> Dict[str, object]:
    source = _event(event)
    payload = dict(source.payload or {})
    symbols = _texts(
        payload.get("affectedSymbols")
        or payload.get("symbols")
        or payload.get("targetSymbols")
        or [],
        uppercase=True,
    )
    account_ids = _texts(
        payload.get("accountIds")
        or ([payload.get("accountId")] if payload.get("accountId") else [])
    )
    fact_types = _texts(payload.get("factTypes") or [])
    trigger = str(payload.get("trigger") or source.aggregate_id or source.name).strip()
    subject_kind = str(payload.get("subjectKind") or "").upper().strip()
    subject_id = str(payload.get("subjectId") or "").strip()
    work_class = str(payload.get("workClass") or "").upper().strip()
    effective_symbols = symbols
    if not effective_symbols and subject_kind in {"SECURITY", "COMPANY"} and subject_id:
        effective_symbols = (subject_id.upper(),)
    supersedable = bool(
        source.name == ONTOLOGY_REASONING_REQUESTED
        and work_class not in {"DIRECT", "RESEARCH", "GOVERNANCE"}
        and str(payload.get("importanceGate") or "").lower() != "non-fungible"
    )
    material = {
        "accounts": account_ids or ("market",),
        "symbols": effective_symbols,
        "factTypes": fact_types,
        "trigger": trigger,
        "subjectKind": subject_kind,
        "subjectId": subject_id,
        "workClass": work_class,
    }
    return {
        "scopeId": "reasoning-scope:" + _hash(material)[:24],
        "accountIds": list(account_ids),
        "symbols": list(effective_symbols),
        "factTypes": list(fact_types),
        "trigger": trigger,
        "subjectKind": subject_kind,
        "subjectId": subject_id,
        "workClass": work_class,
        "supersedable": supersedable,
    }


def _filter_symbol_maps(value: object, symbols: Tuple[str, ...]) -> object:
    """Keep symbol-indexed facts aligned with a bounded reasoning shard."""

    if isinstance(value, list):
        return [_filter_symbol_maps(item, symbols) for item in value]
    if not isinstance(value, Mapping):
        return deepcopy(value)
    allowed = set(symbols)
    filtered = {}
    for key, item in value.items():
        text_key = str(key or "")
        if text_key.endswith("BySymbol") and isinstance(item, Mapping):
            filtered[text_key] = {
                str(symbol): _filter_symbol_maps(symbol_value, symbols)
                for symbol, symbol_value in item.items()
                if str(symbol or "").upper().strip() in allowed
            }
            continue
        filtered[text_key] = _filter_symbol_maps(item, symbols)
    return filtered


def shard_reasoning_event(event: object, max_symbols: int) -> Tuple[DomainEvent, ...]:
    """Split one source event without changing its point-in-time boundary.

    The first shard retains the durable source event id so ingress-repair
    anti-joins still recognize the original event. Remaining shard ids are
    deterministic, making retries idempotent.
    """

    source = _event(event)
    symbols = tuple(reasoning_event_scope(source).get("symbols") or ())
    limit = max(1, int(max_symbols or 1))
    if len(symbols) <= limit:
        return (source,)
    chunks = tuple(
        tuple(symbols[index:index + limit])
        for index in range(0, len(symbols), limit)
    )
    shards: List[DomainEvent] = []
    for index, chunk in enumerate(chunks):
        payload = _filter_symbol_maps(source.payload, chunk)
        for key in ("affectedSymbols", "symbols", "targetSymbols"):
            if key in payload:
                payload[key] = list(chunk)
        payload["reasoningShard"] = {
            "contractVersion": "reasoning-event-shard-v1",
            "parentEventId": source.event_id,
            "shardIndex": index,
            "shardCount": len(chunks),
            "symbols": list(chunk),
        }
        shard_event_id = source.event_id
        if index:
            identity = _hash({"parentEventId": source.event_id, "symbols": chunk})
            shard_event_id = "reasoning-shard:" + identity[:48]
        shards.append(DomainEvent(
            name=source.name,
            aggregate_id=source.aggregate_id,
            schema_version=source.schema_version,
            payload=payload,
            occurred_at=source.occurred_at,
            event_id=shard_event_id,
            correlation_id=source.correlation_id,
        ))
    return tuple(shards)


def reasoning_queue_slot_key(event: object, reasoning_lane: str) -> str:
    """Return the stable latest-state queue slot for a supersedable event.

    A reasoning scope includes changed fact families, so it is intentionally
    different for every kind of change. The execution queue instead needs one
    stable slot per account, symbol and lane. Pending changes in that slot are
    merged before the newest verified source snapshot is evaluated.
    """

    scope = reasoning_event_scope(event)
    material = {
        "accounts": list(scope.get("accountIds") or []) or ["market"],
        "symbols": list(scope.get("symbols") or []) or ["global"],
        "lane": str(reasoning_lane or "CONTEXT").upper().strip() or "CONTEXT",
    }
    return "reasoning-slot:" + _hash(material)[:32]


def _merged_texts(events: Iterable[DomainEvent], key: str) -> List[str]:
    return list(_texts(
        value
        for event in events
        for value in (event.payload or {}).get(key) or []
    ))


def _merge_symbol_text_maps(events: Iterable[DomainEvent], key: str) -> Dict[str, List[str]]:
    merged: Dict[str, set] = {}
    for event in events:
        values = (event.payload or {}).get(key)
        if not isinstance(values, Mapping):
            continue
        for symbol, items in values.items():
            clean_symbol = str(symbol or "").upper().strip()
            if clean_symbol:
                merged.setdefault(clean_symbol, set()).update(_texts(items))
    return {
        symbol: sorted(items)
        for symbol, items in sorted(merged.items())
        if items
    }


def _event_recency(event: DomainEvent) -> Tuple[str, str, str]:
    payload = dict(event.payload or {})
    boundaries = [
        dict(value)
        for value in payload.get("verifiedSourceSnapshots") or []
        if isinstance(value, Mapping)
    ]
    singular = payload.get("verifiedSourceSnapshot")
    if isinstance(singular, Mapping):
        boundaries.append(dict(singular))
    boundary_at = max(
        [str(value.get("generatedAt") or "") for value in boundaries] or [""]
    )
    observed_at = str(payload.get("sourceObservedAt") or payload.get("sourceAsOf") or "")
    return max(boundary_at, observed_at, str(event.occurred_at or "")), str(event.occurred_at or ""), str(event.event_id or "")


def merge_reasoning_events(events: Iterable[object]) -> DomainEvent:
    """Losslessly coalesce pending deltas onto the newest source boundary.

    The verified snapshot is a complete point-in-time world. Older pending
    events therefore do not need their old snapshots, but their changed fact
    families and dependency keys must survive so impact routing cannot miss a
    rule. Non-fungible events are never passed to this function by the queue.
    """

    source_events = tuple(_event(value) for value in events or [])
    if not source_events:
        raise ValueError("At least one reasoning event is required for coalescing")
    ordered = tuple(sorted(source_events, key=_event_recency))
    newest = ordered[-1]
    payload = deepcopy(newest.payload or {})

    for key in ("factTypes", "affectedSymbols", "symbols", "targetSymbols"):
        values = _merged_texts(ordered, key)
        if values:
            payload[key] = [value.upper() for value in values] if key != "factTypes" else values
    for key in ("factTypesBySymbol", "changedFieldsBySymbol"):
        values = _merge_symbol_text_maps(ordered, key)
        if values:
            payload[key] = values

    contracts = [
        dict((event.payload or {}).get("factChangeContract") or {})
        for event in ordered
        if isinstance((event.payload or {}).get("factChangeContract"), Mapping)
    ]
    if contracts:
        merged_contract = deepcopy(contracts[-1])
        merged_contract["version"] = str(merged_contract.get("version") or "fact-change-contract-v3")
        merged_contract["status"] = (
            "ready" if len(contracts) == len(ordered)
            and all(str(value.get("status") or "") == "ready" for value in contracts)
            else "incomplete"
        )
        for key in ("factTypes", "scopeFamilies", "dependencyKeys", "unclassifiedFactTypes"):
            merged_contract[key] = sorted({
                item
                for contract in contracts
                for item in _texts(contract.get(key) or [])
            })
        for key in (
            "scopeFamiliesBySymbol",
            "dependencyKeysBySymbol",
            "unclassifiedFactTypesBySymbol",
        ):
            merged: Dict[str, set] = {}
            for contract in contracts:
                for symbol, items in dict(contract.get(key) or {}).items():
                    clean_symbol = str(symbol or "").upper().strip()
                    if clean_symbol:
                        merged.setdefault(clean_symbol, set()).update(_texts(items))
            merged_contract[key] = {
                symbol: sorted(items) for symbol, items in sorted(merged.items()) if items
            }
        merged_contract["dependencyKeysComplete"] = bool(contracts) and all(
            bool(value.get("dependencyKeysComplete")) for value in contracts
        )
        completeness: Dict[str, List[bool]] = {}
        for contract in contracts:
            for symbol, value in dict(contract.get("dependencyKeysCompleteBySymbol") or {}).items():
                completeness.setdefault(str(symbol or "").upper().strip(), []).append(bool(value))
        merged_contract["dependencyKeysCompleteBySymbol"] = {
            symbol: all(values)
            for symbol, values in sorted(completeness.items())
            if symbol
        }
        payload["factChangeContract"] = merged_contract

    # Revision maps represent the latest known revision for each symbol/key.
    for key in ("factRevisionsBySymbol", "revisionVectorsBySymbol"):
        merged = {}
        for event in ordered:
            values = (event.payload or {}).get(key)
            if isinstance(values, Mapping):
                merged.update(deepcopy(dict(values)))
        if merged:
            payload[key] = merged

    assessments = []
    seen_assessments = set()
    for event in reversed(ordered):
        for item in (event.payload or {}).get("materialityAssessments") or []:
            identity = _canonical_json(item)
            if identity not in seen_assessments:
                assessments.append(deepcopy(item))
                seen_assessments.add(identity)
    if assessments:
        payload["materialityAssessments"] = assessments[:100]

    payload["coalescedReasoningChanges"] = {
        "contractVersion": "reasoning-change-coalescing-v1",
        "eventCount": len(ordered),
        "sourceEventIds": [str(event.event_id or "") for event in ordered],
        "oldestObservedAt": min(
            str((event.payload or {}).get("sourceObservedAt") or event.occurred_at or "")
            for event in ordered
        ),
        "newestObservedAt": max(
            str((event.payload or {}).get("sourceObservedAt") or event.occurred_at or "")
            for event in ordered
        ),
    }
    return DomainEvent(
        name=newest.name,
        aggregate_id=newest.aggregate_id,
        schema_version=newest.schema_version,
        payload=payload,
        occurred_at=newest.occurred_at,
        event_id=newest.event_id,
        correlation_id=newest.correlation_id,
    )


@dataclass(frozen=True)
class IndependentReasoningRequest:
    request_id: str
    deployment_id: str
    source_event_ids: Tuple[str, ...]
    source_events: Tuple[Dict[str, object], ...]
    account_ids: Tuple[str, ...]
    symbols: Tuple[str, ...]
    fact_types: Tuple[str, ...]
    trigger: str
    source_observed_at: str
    requested_at: str
    scope_id: str
    input_fingerprint: str
    release_manifest: Dict[str, object] = field(default_factory=dict)
    context: Dict[str, object] = field(default_factory=dict)
    supersedable: bool = False
    contract_version: str = INDEPENDENT_REASONING_REQUEST_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in ["source_event_ids", "source_events", "account_ids", "symbols", "fact_types"]:
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True)
class IndependentReasoningResult:
    request_id: str
    deployment_id: str
    status: str
    started_at: str
    completed_at: str
    duration_ms: int
    account_ids: Tuple[str, ...] = ()
    symbols: Tuple[str, ...] = ()
    evaluated_symbols: Tuple[str, ...] = ()
    not_evaluated_symbols: Tuple[str, ...] = ()
    source_abox_snapshot_ids: Tuple[str, ...] = ()
    inference_generation_ids: Tuple[str, ...] = ()
    projection_results: Dict[str, object] = field(default_factory=dict)
    candidate_events: Tuple[Dict[str, object], ...] = ()
    decision_syntheses: Tuple[Dict[str, object], ...] = ()
    delivery_events: Tuple[Dict[str, object], ...] = ()
    delivery_authorized: bool = False
    ai_handoff_status: str = "not-requested"
    trace_complete: bool = False
    retryable: bool = False
    retry_after_seconds: int = 0
    reason: str = ""
    reason_code: str = ""
    stage_durations_ms: Dict[str, int] = field(default_factory=dict)
    reasoning_case_id: str = ""
    reasoning_case_stage: str = ""
    reasoning_lane: str = ""
    release_fingerprint: str = ""
    validation_cohort_id: str = ""
    shared_inference: Dict[str, object] = field(default_factory=dict)
    contract_version: str = INDEPENDENT_REASONING_RESULT_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "account_ids",
            "symbols",
            "evaluated_symbols",
            "not_evaluated_symbols",
            "source_abox_snapshot_ids",
            "inference_generation_ids",
            "candidate_events",
            "decision_syntheses",
            "delivery_events",
        ]:
            payload[key] = list(payload[key])
        return payload


def independent_reasoning_request(
    deployment_id: str,
    source_events: Iterable[object],
    release_manifest: Mapping[str, object] = None,
) -> IndependentReasoningRequest:
    events = tuple(_event(item) for item in source_events or [])
    if not events:
        raise ValueError("An independent reasoning request requires at least one source event")
    scopes = [reasoning_event_scope(event) for event in events]
    account_ids = _texts(
        account_id
        for scope in scopes
        for account_id in scope.get("accountIds") or []
    )
    symbols = _texts(
        (
            symbol
            for scope in scopes
            for symbol in scope.get("symbols") or []
        ),
        uppercase=True,
    )
    fact_types = _texts(
        fact_type
        for scope in scopes
        for fact_type in scope.get("factTypes") or []
    )
    event_payloads = tuple(event.to_dict() for event in events)
    release = dict(release_manifest or {})
    source_observed_at = max(
        str(
            (event.payload or {}).get("sourceObservedAt")
            or (event.payload or {}).get("sourceAsOf")
            or event.occurred_at
            or ""
        )
        for event in events
    )
    requested_at = max(str(event.occurred_at or "") for event in events)
    scope_material = {
        "accounts": account_ids or ("market",),
        "symbols": symbols,
        "factTypes": fact_types,
        "sourceScopes": sorted(str(scope.get("scopeId") or "") for scope in scopes),
    }
    scope_id = "reasoning-scope:" + _hash(scope_material)[:24]
    input_material = {
        "contractVersion": INDEPENDENT_REASONING_REQUEST_VERSION,
        "deploymentId": str(deployment_id or ""),
        "events": event_payloads,
        "release": release,
        "scopeId": scope_id,
    }
    fingerprint = _hash(input_material)
    requested_scope_families = set()
    requested_scope_families_by_symbol = {}
    requested_dependency_keys = set()
    requested_dependency_keys_by_symbol = {}
    changed_fields_by_symbol = {}
    fact_revisions_by_symbol = {}
    revision_vectors_by_symbol = {}
    fact_change_contracts = []
    authoritative_fact_boundary = True
    authoritative_dependency_boundary = True
    for event, scope in zip(events, scopes):
        payload = dict(event.payload or {})
        contract = payload.get("factChangeContract")
        if not isinstance(contract, Mapping):
            authoritative_fact_boundary = False
            authoritative_dependency_boundary = False
            continue
        status = str(contract.get("status") or "").strip()
        unclassified = _texts(contract.get("unclassifiedFactTypes") or [])
        unclassified_by_symbol = {
            str(symbol or "").upper().strip(): list(_texts(values))
            for symbol, values in dict(
                contract.get("unclassifiedFactTypesBySymbol") or {}
            ).items()
            if str(symbol or "").strip() and _texts(values)
        }
        if status != "ready" or unclassified or unclassified_by_symbol:
            authoritative_fact_boundary = False
        families = set(_texts(contract.get("scopeFamilies") or []))
        requested_scope_families.update(families)
        dependency_keys = set(_texts(contract.get("dependencyKeys") or []))
        requested_dependency_keys.update(dependency_keys)
        dependency_keys_complete = bool(contract.get("dependencyKeysComplete"))
        if not dependency_keys_complete:
            authoritative_dependency_boundary = False
        contract_by_symbol = {
            str(symbol or "").upper().strip(): set(_texts(values))
            for symbol, values in dict(
                contract.get("scopeFamiliesBySymbol") or {}
            ).items()
            if str(symbol or "").strip() and _texts(values)
        }
        dependency_keys_by_symbol = {
            str(symbol or "").upper().strip(): set(_texts(values))
            for symbol, values in dict(
                contract.get("dependencyKeysBySymbol") or {}
            ).items()
            if str(symbol or "").strip() and _texts(values)
        }
        dependency_completeness_by_symbol = {
            str(symbol or "").upper().strip(): bool(value)
            for symbol, value in dict(
                contract.get("dependencyKeysCompleteBySymbol") or {}
            ).items()
            if str(symbol or "").strip()
        }
        event_symbols = [
            str(symbol or "").upper().strip()
            for symbol in scope.get("symbols") or []
            if str(symbol or "").strip()
        ]
        for symbol in event_symbols:
            requested_scope_families_by_symbol.setdefault(symbol, set()).update(
                contract_by_symbol.get(symbol) or families
            )
            requested_dependency_keys_by_symbol.setdefault(symbol, set()).update(
                dependency_keys_by_symbol.get(symbol) or dependency_keys
            )
            if not dependency_completeness_by_symbol.get(
                symbol,
                dependency_keys_complete,
            ):
                authoritative_dependency_boundary = False
        for symbol, values in contract_by_symbol.items():
            requested_scope_families_by_symbol.setdefault(symbol, set()).update(values)
        for symbol, values in dependency_keys_by_symbol.items():
            requested_dependency_keys_by_symbol.setdefault(symbol, set()).update(values)
        for symbol, values in dict(payload.get("changedFieldsBySymbol") or {}).items():
            clean_symbol = str(symbol or "").upper().strip()
            if clean_symbol:
                changed_fields_by_symbol.setdefault(clean_symbol, set()).update(
                    _texts(values)
                )
        subject_fields = _texts(payload.get("subjectChangedFields") or [])
        if subject_fields and len(event_symbols) == 1:
            changed_fields_by_symbol.setdefault(event_symbols[0], set()).update(
                subject_fields
            )
        for symbol, revision in dict(payload.get("factRevisionsBySymbol") or {}).items():
            clean_symbol = str(symbol or "").upper().strip()
            clean_revision = str(revision or "").strip()
            if clean_symbol and clean_revision:
                fact_revisions_by_symbol[clean_symbol] = clean_revision
        for symbol, vector in dict(payload.get("revisionVectorsBySymbol") or {}).items():
            clean_symbol = str(symbol or "").upper().strip()
            if clean_symbol and isinstance(vector, Mapping):
                revision_vectors_by_symbol[clean_symbol] = {
                    str(key or "").strip(): str(value or "").strip()
                    for key, value in vector.items()
                    if str(key or "").strip() and str(value or "").strip()
                }
        fact_change_contracts.append({
            "requestEventId": str(event.event_id or ""),
            "version": str(contract.get("version") or ""),
            "status": status,
            "scopeFamilies": sorted(families),
            "scopeFamiliesBySymbol": {
                symbol: sorted(values)
                for symbol, values in sorted(contract_by_symbol.items())
            },
            "dependencyKeys": sorted(dependency_keys),
            "dependencyKeysBySymbol": {
                symbol: sorted(values)
                for symbol, values in sorted(dependency_keys_by_symbol.items())
            },
            "dependencyKeysComplete": dependency_keys_complete,
            "dependencyKeysCompleteBySymbol": dependency_completeness_by_symbol,
            "unclassifiedFactTypes": list(unclassified),
            "unclassifiedFactTypesBySymbol": unclassified_by_symbol,
        })
    if len(fact_change_contracts) != len(events):
        authoritative_fact_boundary = False
        authoritative_dependency_boundary = False
    context = {
        "sourceEventIds": [event.event_id for event in events],
        "accountIds": list(account_ids),
        "targetSymbols": list(symbols),
        "factTypes": list(fact_types),
        "sourceObservedAt": source_observed_at,
        "triggers": sorted({str(scope.get("trigger") or "") for scope in scopes}),
        "subjectKinds": sorted({str(scope.get("subjectKind") or "") for scope in scopes if scope.get("subjectKind")}),
        "subjectIds": sorted({str(scope.get("subjectId") or "") for scope in scopes if scope.get("subjectId")}),
        "workClasses": sorted({str(scope.get("workClass") or "") for scope in scopes if scope.get("workClass")}),
        "requestedScopeFamilies": sorted(requested_scope_families),
        "requestedScopeFamiliesBySymbol": {
            symbol: sorted(values)
            for symbol, values in sorted(requested_scope_families_by_symbol.items())
        },
        "requestedDependencyKeys": sorted(requested_dependency_keys),
        "requestedDependencyKeysBySymbol": {
            symbol: sorted(values)
            for symbol, values in sorted(requested_dependency_keys_by_symbol.items())
        },
        "changedFieldsBySymbol": {
            symbol: sorted(values)
            for symbol, values in sorted(changed_fields_by_symbol.items())
        },
        "factRevisionsBySymbol": fact_revisions_by_symbol,
        "revisionVectorsBySymbol": revision_vectors_by_symbol,
        "factChangeContracts": fact_change_contracts,
        "eventFactBoundaryAuthoritative": authoritative_fact_boundary,
        "eventDependencyBoundaryAuthoritative": authoritative_dependency_boundary,
        "verifiedSourceSnapshots": [
            boundary
            for event in events
            for boundary in (
                [dict((event.payload or {}).get("verifiedSourceSnapshot") or {})]
                + [
                    dict(value)
                    for value in (event.payload or {}).get("verifiedSourceSnapshots") or []
                    if isinstance(value, Mapping)
                ]
            )
            if boundary and str(boundary.get("snapshotId") or boundary.get("generatedAt") or "").strip()
        ],
        "reasoningEngineDeploymentId": str(deployment_id or ""),
        "reasoningEngineReleaseManifest": release,
        "reasoningRequestFingerprint": fingerprint,
        "reasoningRequestContractVersion": INDEPENDENT_REASONING_REQUEST_VERSION,
    }
    return IndependentReasoningRequest(
        request_id="reasoning-request:" + fingerprint[:32],
        deployment_id=str(deployment_id or ""),
        source_event_ids=tuple(event.event_id for event in events),
        source_events=event_payloads,
        account_ids=account_ids,
        symbols=symbols,
        fact_types=fact_types,
        trigger=",".join(context["triggers"]),
        source_observed_at=source_observed_at,
        requested_at=requested_at,
        scope_id=scope_id,
        input_fingerprint=fingerprint,
        release_manifest=release,
        context=context,
        supersedable=all(bool(scope.get("supersedable")) for scope in scopes),
    )
