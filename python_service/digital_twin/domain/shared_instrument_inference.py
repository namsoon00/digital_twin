"""Account-independent TypeDB inference and lightweight portfolio overlays.

The shared snapshot is a read model of already verified TypeDB output.  It
never creates an investment action.  Account-owned facts remain in a separate
overlay so one market explanation can be reused by many portfolios without
leaking holdings, cost basis, policy, or account identity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .hypothesis_scoping import MARKET_SHARED_SCOPE, inference_scope_assessment


SHARED_INSTRUMENT_INFERENCE_VERSION = "shared-instrument-inference-v1"
PORTFOLIO_INFERENCE_OVERLAY_VERSION = "portfolio-inference-overlay-v1"
VERIFIED_PROJECTION_STATUSES = frozenset({
    "ok",
    "partial",
    "unchanged-material-facts",
    "unchanged-material-facts-reasoning-retry",
})


def _text(value: object) -> str:
    return str(value or "").strip()


def _symbol(value: object) -> str:
    return _text(value).upper()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value: object = "") -> str:
    text = _text(value)
    if text:
        return text
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _strings(values: Iterable[object]) -> List[str]:
    return sorted({_text(value) for value in values or [] if _text(value)})


def _safe_condition(condition: Mapping[str, object]) -> Dict[str, object]:
    """Keep causal and observed market evidence while dropping arbitrary data."""

    source = dict(condition or {})
    allowed = [
        "conditionId", "kind", "role", "field", "operator", "value",
        "relationType", "direction", "targetKind", "targetPropertyFilters",
        "relationPropertyFilters", "hypothesisScope", "evidenceGroupKey",
        "observedValue", "observedAt", "sourceAsOf", "matched",
    ]
    return {key: source.get(key) for key in allowed if key in source}


def _safe_trace(trace: Mapping[str, object], scope: Mapping[str, object]) -> Dict[str, object]:
    source = dict(trace or {})
    return {
        "symbol": _symbol(source.get("symbol")),
        "ruleId": _text(source.get("ruleId")),
        "semanticRuleId": _text(source.get("semanticRuleId") or source.get("ruleId")),
        "hypothesisFamilyKey": _text(source.get("hypothesisFamilyKey")),
        "matchedConditionIds": _strings(source.get("matchedConditionIds") or []),
        "matchedConditions": [
            _safe_condition(item)
            for item in source.get("matchedConditions") or []
            if isinstance(item, Mapping)
        ],
        "ruleConditionShapes": [
            _safe_condition(item)
            for item in source.get("ruleConditionShapes") or []
            if isinstance(item, Mapping)
        ],
        "freshnessStatus": _text(source.get("freshnessStatus")),
        "validationState": _text(source.get("validationState")),
        "evidenceUsableForJudgement": source.get("evidenceUsableForJudgement"),
        "scopeState": _text(scope.get("scopeState")),
        "marketCausalSignature": _text(scope.get("marketCausalSignature")),
    }


def _safe_relation(relation: Mapping[str, object]) -> Dict[str, object]:
    source = dict(relation or {})
    allowed = [
        "type", "sourceLabel", "targetLabel", "ruleId",
        "semanticRuleId", "hypothesisFamilyKey", "polarity", "evidenceRole",
        "actionGroup", "actionLevel", "decisionStage", "decisionEffect",
        "reviewLevel", "dataState", "changeState", "validationState",
        "freshnessStatus", "evidenceUsableForJudgement",
        "strengthenConditions", "weakenConditions", "nextChecks",
    ]
    return {key: source.get(key) for key in allowed if key in source}


def projection_is_verified(projection: Mapping[str, object]) -> bool:
    values = dict(projection or {})
    inference = values.get("inferenceBox") if isinstance(values.get("inferenceBox"), Mapping) else {}
    return bool(
        _text(values.get("status")).lower() in VERIFIED_PROJECTION_STATUSES
        and (
            inference.get("nativeTypeDbReasoningCompleted")
            or inference.get("typedbNativeRuleEvaluationCompleted")
        )
        and inference.get("generationAligned")
        and _text(inference.get("sourceAboxSnapshotId"))
        and _text(inference.get("inferenceGenerationId"))
    )


def _row_symbols(row: Mapping[str, object], requested_symbols: Sequence[str]) -> List[str]:
    direct = _symbol(row.get("symbol"))
    if direct:
        return [direct]
    haystack = " ".join(
        _text(row.get(key)).upper()
        for key in ["source", "target", "sourceLabel", "targetLabel"]
    )
    return [symbol for symbol in requested_symbols if symbol and symbol in haystack]


def _rows_for_symbol(rows: Iterable[Mapping[str, object]], symbol: str) -> List[Dict[str, object]]:
    return [
        dict(row)
        for row in rows or []
        if isinstance(row, Mapping) and symbol in _row_symbols(row, [symbol])
    ]


@dataclass(frozen=True)
class SharedInstrumentInferenceSnapshot:
    snapshot_id: str
    deployment_id: str
    symbol: str
    market_id: str
    semantic_fingerprint: str
    source_fingerprint: str
    release_fingerprint: str
    rulebox_hash: str
    inference_generation_id: str
    source_abox_snapshot_id: str
    source_as_of: str
    consistency_status: str
    source_account_count: int
    payload: Dict[str, object]
    created_at: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioInferenceOverlay:
    overlay_id: str
    deployment_id: str
    account_id: str
    symbol: str
    shared_snapshot_ids: Tuple[str, ...]
    inference_generation_id: str
    source_abox_snapshot_id: str
    account_fingerprint: str
    status: str
    payload: Dict[str, object]
    created_at: str

    def to_dict(self) -> Dict[str, object]:
        values = asdict(self)
        values["shared_snapshot_ids"] = list(self.shared_snapshot_ids)
        return values


def build_shared_instrument_inference(
    projection_results: Mapping[str, object],
    requested_symbols: Iterable[str],
    *,
    deployment_id: str,
    release_fingerprint: str = "",
    observed_at: str = "",
) -> Dict[str, object]:
    """Split verified TypeDB output into reusable market facts and overlays.

    A conflicting market fingerprint across accounts is retained for audit but
    is not eligible to become the active shared head.  This fail-closed rule is
    the dual-run equivalence check used before execution can reuse the cache.
    """

    symbols = _strings(_symbol(value) for value in requested_symbols or [])
    created_at = _utc(observed_at)
    candidates: Dict[str, List[Dict[str, object]]] = {}
    account_rows: List[Dict[str, object]] = []
    for account_id, raw_projection in dict(projection_results or {}).items():
        projection = dict(raw_projection or {}) if isinstance(raw_projection, Mapping) else {}
        if not projection_is_verified(projection):
            continue
        inference = dict(projection.get("inferenceBox") or {})
        traces = [dict(item) for item in inference.get("traces") or [] if isinstance(item, Mapping)]
        relations = [dict(item) for item in inference.get("relations") or [] if isinstance(item, Mapping)]
        inferred_symbols = _strings(
            value
            for row in traces + relations
            for value in _row_symbols(row, symbols)
        )
        for symbol in inferred_symbols or symbols:
            symbol_traces = _rows_for_symbol(traces, symbol)
            symbol_relations = _rows_for_symbol(relations, symbol)
            market_traces: List[Dict[str, object]] = []
            market_rule_ids = set()
            local_rule_ids = set()
            for trace in symbol_traces:
                rule_id = _text(trace.get("ruleId") or trace.get("semanticRuleId"))
                related = [
                    relation for relation in symbol_relations
                    if not rule_id or _text(relation.get("ruleId") or relation.get("semanticRuleId")) == rule_id
                ]
                scope = inference_scope_assessment([trace], [], related, stance=trace.get("polarity"))
                if _text(scope.get("scopeState")) == MARKET_SHARED_SCOPE:
                    market_traces.append(_safe_trace(trace, scope))
                    if rule_id:
                        market_rule_ids.add(rule_id)
                elif rule_id:
                    local_rule_ids.add(rule_id)
            market_relations = [
                _safe_relation(relation)
                for relation in symbol_relations
                if _text(relation.get("ruleId") or relation.get("semanticRuleId")) in market_rule_ids
            ]
            market_payload = {
                "contractVersion": SHARED_INSTRUMENT_INFERENCE_VERSION,
                "symbol": symbol,
                "marketId": _text(
                    next((row.get("market") for row in symbol_traces if row.get("market")), "")
                ),
                "relations": market_relations,
                "traces": market_traces,
                "ruleIds": sorted(market_rule_ids),
                "scopeState": MARKET_SHARED_SCOPE,
                "decisionAuthority": "none",
            }
            semantic_fingerprint = _hash(market_payload) if market_traces else ""
            source_identity = {
                "deploymentId": _text(deployment_id),
                "accountId": _text(account_id),
                "inferenceGenerationId": _text(inference.get("inferenceGenerationId")),
                "sourceAboxSnapshotId": _text(inference.get("sourceAboxSnapshotId")),
                "semanticFingerprint": semantic_fingerprint,
            }
            candidate = {
                "accountId": _text(account_id),
                "symbol": symbol,
                "marketPayload": market_payload,
                "semanticFingerprint": semantic_fingerprint,
                "sourceFingerprint": _hash(source_identity),
                "inferenceGenerationId": _text(inference.get("inferenceGenerationId")),
                "sourceAboxSnapshotId": _text(inference.get("sourceAboxSnapshotId")),
                "sourceAsOf": _text(inference.get("inferenceGenerationAt")) or created_at,
                "ruleboxHash": _text(inference.get("ruleboxRulesHash")),
                "localRuleIds": sorted(local_rule_ids),
            }
            account_rows.append(candidate)
            if semantic_fingerprint:
                candidates.setdefault(symbol, []).append(candidate)

    snapshots: List[SharedInstrumentInferenceSnapshot] = []
    shared_ids_by_symbol: Dict[str, List[str]] = {}
    consistency_by_symbol: Dict[str, str] = {}
    for symbol, rows in candidates.items():
        fingerprints = sorted({row["semanticFingerprint"] for row in rows})
        consistency = "equivalent" if len(fingerprints) == 1 else "conflict"
        consistency_by_symbol[symbol] = consistency
        for fingerprint in fingerprints:
            equivalent_rows = [row for row in rows if row["semanticFingerprint"] == fingerprint]
            lead = max(equivalent_rows, key=lambda row: row["sourceAsOf"])
            source_fingerprint = _hash(sorted(row["sourceFingerprint"] for row in equivalent_rows))
            shared_generation_id = "shared-generation:" + source_fingerprint[:32]
            shared_source_id = "shared-source:" + _hash({
                "symbol": symbol,
                "sourceAsOf": lead["sourceAsOf"],
                "semanticFingerprint": fingerprint,
            })[:32]
            snapshot_id = "shared-inference:" + _hash({
                "deploymentId": deployment_id,
                "symbol": symbol,
                "sourceFingerprint": source_fingerprint,
            })[:32]
            snapshot = SharedInstrumentInferenceSnapshot(
                snapshot_id=snapshot_id,
                deployment_id=_text(deployment_id),
                symbol=symbol,
                market_id=_text(lead["marketPayload"].get("marketId")),
                semantic_fingerprint=fingerprint,
                source_fingerprint=source_fingerprint,
                release_fingerprint=_text(release_fingerprint),
                rulebox_hash=_text(lead.get("ruleboxHash")),
                inference_generation_id=shared_generation_id,
                source_abox_snapshot_id=shared_source_id,
                source_as_of=_text(lead.get("sourceAsOf")),
                consistency_status=consistency,
                source_account_count=len(equivalent_rows),
                payload=dict(lead["marketPayload"]),
                created_at=created_at,
            )
            snapshots.append(snapshot)
            if consistency == "equivalent":
                shared_ids_by_symbol.setdefault(symbol, []).append(snapshot_id)

    overlays: List[PortfolioInferenceOverlay] = []
    for row in account_rows:
        shared_ids = tuple(sorted(shared_ids_by_symbol.get(row["symbol"], [])))
        overlay_payload = {
            "contractVersion": PORTFOLIO_INFERENCE_OVERLAY_VERSION,
            "accountId": row["accountId"],
            "symbol": row["symbol"],
            "sharedSnapshotIds": list(shared_ids),
            "accountRuleIds": list(row["localRuleIds"]),
            "decisionAuthority": "typedb-plus-ai",
            "sharedConsistencyStatus": consistency_by_symbol.get(row["symbol"], "no-market-hypothesis"),
        }
        account_fingerprint = _hash({
            "accountId": row["accountId"],
            "symbol": row["symbol"],
            "sharedSnapshotIds": shared_ids,
            "accountRuleIds": row["localRuleIds"],
            "sourceAboxSnapshotId": row["sourceAboxSnapshotId"],
        })
        overlay_id = "portfolio-overlay:" + account_fingerprint[:32]
        overlays.append(PortfolioInferenceOverlay(
            overlay_id=overlay_id,
            deployment_id=_text(deployment_id),
            account_id=row["accountId"],
            symbol=row["symbol"],
            shared_snapshot_ids=shared_ids,
            inference_generation_id=row["inferenceGenerationId"],
            source_abox_snapshot_id=row["sourceAboxSnapshotId"],
            account_fingerprint=account_fingerprint,
            status=(
                "ready"
                if shared_ids
                else "conflict" if consistency_by_symbol.get(row["symbol"]) == "conflict"
                else "account-only"
            ),
            payload=overlay_payload,
            created_at=created_at,
        ))
    return {
        "contractVersion": SHARED_INSTRUMENT_INFERENCE_VERSION,
        "status": "conflict" if any(value == "conflict" for value in consistency_by_symbol.values()) else "ready",
        "snapshots": snapshots,
        "overlays": overlays,
        "consistencyBySymbol": consistency_by_symbol,
        "verifiedAccountCount": len({_text(row["accountId"]) for row in account_rows}),
        "sharedSymbolCount": len(shared_ids_by_symbol),
    }
