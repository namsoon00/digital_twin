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

from .hypothesis_scoping import (
    MARKET_SHARED_SCOPE,
    condition_scope_profile,
    inference_scope_assessment,
)
from .ontology_projection_input import compact_external_signals_for_ontology
from .ontology_projection_audit import projection_source_snapshot_fingerprint


SHARED_INSTRUMENT_INFERENCE_VERSION = "shared-instrument-inference-v2"
PORTFOLIO_INFERENCE_OVERLAY_VERSION = "portfolio-inference-overlay-v2"
SHARED_EXECUTION_REUSE_VERSION = "shared-instrument-execution-reuse-v2"
VERIFIED_PROJECTION_STATUSES = frozenset({
    "ok",
    "partial",
    "unchanged-material-facts",
    "unchanged-material-facts-reasoning-retry",
    "reused-shared-account-inference",
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


def market_source_revision_fingerprint(
    reasoning_context: Mapping[str, object],
    symbol: object,
) -> str:
    """Identify the exact market fact revision shared by account executions."""

    context = dict(reasoning_context or {}) if isinstance(reasoning_context, Mapping) else {}
    clean_symbol = _symbol(symbol)
    if not clean_symbol or not bool(context.get("eventFactBoundaryAuthoritative")):
        return ""
    vector = dict((context.get("revisionVectorsBySymbol") or {}).get(clean_symbol) or {})
    fact_revision = _text((context.get("factRevisionsBySymbol") or {}).get(clean_symbol))
    families = _strings(
        (context.get("requestedScopeFamiliesBySymbol") or {}).get(clean_symbol)
        or context.get("requestedScopeFamilies")
        or []
    )
    if not vector and not fact_revision:
        return ""
    return _hash({
        "symbol": clean_symbol,
        "factRevision": fact_revision,
        "revisionVector": {
            _text(key): _text(value)
            for key, value in sorted(vector.items(), key=lambda item: _text(item[0]))
            if _text(key) and _text(value)
        },
        "scopeFamilies": families,
    })


MARKET_POSITION_FIELDS = (
    "symbol", "name", "market", "currency", "current_price", "change_rate",
    "quote_source", "quote_status", "data_quality", "market_signal_coverage",
    "source_as_of", "source_fetched_at", "source_timestamp_state",
    "freshness_status", "freshness_age_minutes", "latency_status",
    "market_session", "source_transport", "real_time", "indicator_as_of",
    "trade_strength", "trading_value", "volume", "volume_ratio",
    "buy_volume", "sell_volume", "orderbook_bid_volume", "orderbook_ask_volume",
    "bid_ask_imbalance", "foreign_buy_volume", "foreign_sell_volume",
    "foreign_net_volume", "foreign_net_amount", "institution_buy_volume",
    "institution_sell_volume", "institution_net_volume", "institution_net_amount",
    "individual_buy_volume", "individual_sell_volume", "individual_net_volume",
    "individual_net_amount", "ma5", "ma20", "ma60", "ma120", "ma200",
    "ma20_slope", "ma60_slope", "ma5_distance", "ma20_distance", "ma60_distance",
    "sector",
)

ACCOUNT_POSITION_FIELDS = (
    "symbol", "market", "currency", "quantity", "sellable_quantity",
    "average_price", "source",
)


def account_overlay_input_fingerprint(snapshot: object, symbol: object) -> str:
    """Hash account-owned facts without copying current market observations.

    This fingerprint is the private overlay identity. Price, volume, moving
    averages and other market-owned values deliberately stay out of it; the
    exact shared market snapshot is a separate component of the overlay key.
    """

    clean_symbol = _symbol(symbol)
    if not clean_symbol or snapshot is None:
        return ""
    positions = []
    for position in list(getattr(snapshot, "positions", []) or []) + list(
        getattr(snapshot, "watchlist", []) or []
    ):
        value = (
            position.to_dict()
            if callable(getattr(position, "to_dict", None))
            else dict(position or {}) if isinstance(position, Mapping) else {}
        )
        if _symbol(value.get("symbol")) != clean_symbol:
            continue
        positions.append({key: value.get(key) for key in ACCOUNT_POSITION_FIELDS})
    portfolio = getattr(snapshot, "portfolio", None)
    account_state = {
        "accountId": _text(getattr(snapshot, "account_id", "")),
        "symbol": clean_symbol,
        "positions": positions,
        "cash": getattr(portfolio, "cash", None),
    }
    return _hash(account_state) if positions else ""


def decision_input_fingerprint(snapshot: object) -> str:
    """Identify the complete immutable ABox source consumed by TypeDB."""

    if snapshot is None:
        return ""
    try:
        return projection_source_snapshot_fingerprint(snapshot)
    except Exception:
        return ""


def _symbol_projection_replay(
    projection: Mapping[str, object],
    symbol: str,
) -> Dict[str, object]:
    """Keep the private TypeDB result required for an exact-input replay.

    Shared rows never receive this packet. It is stored only in the private
    account overlay and remains bounded to one symbol.
    """

    values = dict(projection or {}) if isinstance(projection, Mapping) else {}
    inference = dict(values.get("inferenceBox") or {}) if isinstance(
        values.get("inferenceBox"), Mapping
    ) else {}
    if not inference:
        return {}
    replay_inference_keys = (
        "configured", "status", "graphStore", "source", "reasoningMode",
        "nativeTypeDbReasoningUsed", "nativeTypeDbReasoningCompleted",
        "typedbNativeRuleEvaluationCompleted", "generationAligned",
        "inferenceGenerationId", "inferenceGenerationAt",
        "sourceAboxSnapshotId", "activeAboxSnapshotId", "worldId",
        "ruleboxRulesHash", "ruleboxShortHash", "ruleboxRuleCount",
        "ruleboxConditionCount", "ruleboxDerivationCount",
        "nativeRelationCount", "hypothesisCalibration",
    )
    relations = _rows_for_symbol(inference.get("relations") or [], symbol)
    traces = _rows_for_symbol(inference.get("traces") or [], symbol)
    replay_inference = {
        key: inference.get(key)
        for key in replay_inference_keys
        if key in inference
    }
    replay_inference.update({
        "relations": relations[:240],
        "traces": traces[:240],
        "relationCount": len(relations[:240]),
        "traceCount": len(traces[:240]),
    })
    return {
        "status": _text(values.get("status")) or "ok",
        "graphStore": _text(values.get("graphStore") or inference.get("graphStore")),
        "worldId": _text(values.get("worldId") or inference.get("worldId")),
        "accountId": _text(values.get("accountId")),
        "ontologyWorld": dict(values.get("ontologyWorld") or {}) if isinstance(
            values.get("ontologyWorld"), Mapping
        ) else {},
        "inferenceBox": replay_inference,
    }


def market_snapshot_input_fingerprint(snapshot: object, symbol: object) -> str:
    """Hash market-owned ABox inputs while excluding every account position fact."""

    clean_symbol = _symbol(symbol)
    if not clean_symbol or snapshot is None:
        return ""
    rows = []
    for position in list(getattr(snapshot, "positions", []) or []) + list(
        getattr(snapshot, "watchlist", []) or []
    ):
        value = (
            position.to_dict()
            if callable(getattr(position, "to_dict", None))
            else dict(position or {}) if isinstance(position, Mapping) else {}
        )
        if _symbol(value.get("symbol")) != clean_symbol:
            continue
        rows.append({key: value.get(key) for key in MARKET_POSITION_FIELDS if key in value})
    external = compact_external_signals_for_ontology(
        getattr(snapshot, "external_signals", {}) or {},
        target_symbols=[clean_symbol],
    )
    if not rows and not external:
        return ""
    return _hash({
        "symbol": clean_symbol,
        "marketRows": rows,
        "externalSignals": external,
    })


def market_shared_rule_ids(rules: Iterable[object]) -> List[str]:
    """Return rules whose every configured condition is market-owned."""

    result = []
    for rule in rules or []:
        if isinstance(rule, Mapping):
            rule_id = _text(rule.get("rule_id") or rule.get("ruleId"))
            conditions = list(rule.get("conditions") or [])
            enabled = rule.get("enabled", True) is not False
        else:
            rule_id = _text(getattr(rule, "rule_id", ""))
            conditions = list(getattr(rule, "conditions", []) or [])
            enabled = getattr(rule, "enabled", True) is not False
        if not rule_id or not enabled or not conditions:
            continue
        profiles = []
        for index, condition in enumerate(conditions):
            if isinstance(condition, Mapping):
                payload = dict(condition)
            elif callable(getattr(condition, "to_dict", None)):
                payload = dict(condition.to_dict() or {})
            else:
                payload = dict(vars(condition)) if hasattr(condition, "__dict__") else {}
            profiles.append(condition_scope_profile(payload, index))
        if profiles and all(profile.get("scope") == "market" for profile in profiles):
            result.append(rule_id)
    return sorted(set(result))


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
        "knowledgeBasis": dict(source.get("knowledgeBasis") or {}) if isinstance(source.get("knowledgeBasis"), Mapping) else {},
        "claimContract": dict(source.get("claimContract") or {}) if isinstance(source.get("claimContract"), Mapping) else {},
        "ruleKind": _text(source.get("ruleKind")),
        "theoryFamily": _text(source.get("theoryFamily")),
        "thesisFamily": _text(source.get("thesisFamily")),
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
        "knowledgeBasis", "claimContract", "ruleKind", "theoryFamily", "thesisFamily",
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
    market_rule_ids: Iterable[str] = (),
    market_input_fingerprints: Mapping[str, Mapping[str, str]] = None,
    account_input_fingerprints: Mapping[str, Mapping[str, str]] = None,
    decision_input_fingerprints: Mapping[str, str] = None,
) -> Dict[str, object]:
    """Split verified TypeDB output into reusable market facts and overlays.

    A conflicting market fingerprint across accounts is retained for audit but
    is not eligible to become the active shared head.  This fail-closed rule is
    the dual-run equivalence check used before execution can reuse the cache.
    """

    symbols = _strings(_symbol(value) for value in requested_symbols or [])
    created_at = _utc(observed_at)
    market_rule_catalog = _strings(market_rule_ids)
    candidates: Dict[str, List[Dict[str, object]]] = {}
    account_rows: List[Dict[str, object]] = []
    for account_id, raw_projection in dict(projection_results or {}).items():
        projection = dict(raw_projection or {}) if isinstance(raw_projection, Mapping) else {}
        if not projection_is_verified(projection):
            continue
        inference = dict(projection.get("inferenceBox") or {})
        reasoning_context = (
            projection.get("reasoningContext")
            if isinstance(projection.get("reasoningContext"), Mapping)
            else {}
        )
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
            source_revision_fingerprint = market_source_revision_fingerprint(
                reasoning_context,
                symbol,
            )
            market_input_fingerprint = _text(
                ((market_input_fingerprints or {}).get(_text(account_id)) or {}).get(symbol)
            )
            market_payload = {
                "contractVersion": SHARED_INSTRUMENT_INFERENCE_VERSION,
                "symbol": symbol,
                "marketId": _text(
                    next((row.get("market") for row in symbol_traces if row.get("market")), "")
                ),
                "relations": market_relations,
                "traces": market_traces,
                "ruleIds": sorted(market_rule_ids),
                "matchedMarketRuleIds": sorted(market_rule_ids),
                "marketRuleCatalogIds": list(market_rule_catalog),
                "sourceRevisionFingerprint": source_revision_fingerprint,
                "marketInputFingerprint": market_input_fingerprint,
                "scopeState": MARKET_SHARED_SCOPE,
                "decisionAuthority": "none",
            }
            semantic_payload = {
                key: value
                for key, value in market_payload.items()
                if key != "sourceRevisionFingerprint"
                and key != "marketInputFingerprint"
            }
            semantic_fingerprint = (
                _hash(semantic_payload)
                if market_traces or (market_rule_catalog and source_revision_fingerprint)
                else ""
            )
            # The shared identity must not depend on the account that happened
            # to execute the market rules first. Otherwise every account
            # advances the shared head even when it observed the same facts.
            source_identity = {
                "deploymentId": _text(deployment_id),
                "releaseFingerprint": _text(release_fingerprint),
                "symbol": symbol,
                "sourceRevisionFingerprint": source_revision_fingerprint,
                "marketInputFingerprint": market_input_fingerprint,
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
                "accountInputFingerprint": _text(
                    ((account_input_fingerprints or {}).get(_text(account_id)) or {}).get(symbol)
                ),
                "decisionInputFingerprint": _text(
                    (decision_input_fingerprints or {}).get(_text(account_id))
                ),
                "projectionReplay": _symbol_projection_replay(projection, symbol),
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
            source_fingerprint = _hash(sorted({
                row["sourceFingerprint"] for row in equivalent_rows
            }))
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
            "accountInputFingerprint": row["accountInputFingerprint"],
            "decisionInputFingerprint": row["decisionInputFingerprint"],
            "releaseFingerprint": _text(release_fingerprint),
        })
        overlay_payload.update({
            "releaseFingerprint": _text(release_fingerprint),
            "accountInputFingerprint": row["accountInputFingerprint"],
            "decisionInputFingerprint": row["decisionInputFingerprint"],
            "projectionReplay": row["projectionReplay"],
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
