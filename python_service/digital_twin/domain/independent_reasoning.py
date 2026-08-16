"""Version-neutral contracts for independently executable reasoning engines."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, Mapping, Tuple

from .events import DomainEvent, ONTOLOGY_REASONING_REQUESTED


INDEPENDENT_REASONING_REQUEST_VERSION = "independent-reasoning-request-v1"
INDEPENDENT_REASONING_RESULT_VERSION = "independent-reasoning-result-v1"


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
    source_abox_snapshot_ids: Tuple[str, ...] = ()
    inference_generation_ids: Tuple[str, ...] = ()
    projection_results: Dict[str, object] = field(default_factory=dict)
    candidate_events: Tuple[Dict[str, object], ...] = ()
    delivery_events: Tuple[Dict[str, object], ...] = ()
    delivery_authorized: bool = False
    ai_handoff_status: str = "not-requested"
    trace_complete: bool = False
    retryable: bool = False
    retry_after_seconds: int = 0
    reason: str = ""
    stage_durations_ms: Dict[str, int] = field(default_factory=dict)
    contract_version: str = INDEPENDENT_REASONING_RESULT_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "account_ids",
            "symbols",
            "source_abox_snapshot_ids",
            "inference_generation_ids",
            "candidate_events",
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
    context = {
        "sourceEventIds": [event.event_id for event in events],
        "accountIds": list(account_ids),
        "targetSymbols": list(symbols),
        "factTypes": list(fact_types),
        "sourceObservedAt": source_observed_at,
        "triggers": sorted({str(scope.get("trigger") or "") for scope in scopes}),
        "subjectKinds": sorted({str(scope.get("subjectKind") or "") for scope in scopes if scope.get("subjectKind")}),
        "subjectIds": sorted({str(scope.get("subjectId") or "") for scope in scopes if scope.get("subjectId")}),
        "verifiedSourceSnapshots": [
            dict((event.payload or {}).get("verifiedSourceSnapshot") or {})
            for event in events
            if isinstance((event.payload or {}).get("verifiedSourceSnapshot"), Mapping)
            and (event.payload or {}).get("verifiedSourceSnapshot")
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
