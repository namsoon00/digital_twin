"""Scope policy implementation; facade-independent dependencies."""

from __future__ import annotations
from .scope_policy_ports import ScopePolicyPort
from digital_twin.domain.crypto_market_signals import crypto_markets_by_symbol
from digital_twin.domain.ontology_change_impact import (
    build_inference_impact_plan,
    compact_inference_impact_plan,
    scope_symbol,
)
from digital_twin.domain.ontology_fact_slots import build_fact_slot_projection_plan
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
)
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.world_partitioned_reasoning import (
    ACCOUNT_OVERLAY_PROJECTION_CONTRACT_VERSION,
)
from typing import Dict, List


def snapshot_symbols(_store: ScopePolicyPort, snapshot: AccountSnapshot) -> List[str]:
    symbols = []
    for item in list(snapshot.positions or []) + list(snapshot.watchlist or []):
        symbol = str(getattr(item, "symbol", "") or "").upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    for symbol in crypto_markets_by_symbol(getattr(snapshot, "external_signals", {})):
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


def inference_symbols(
    _store: ScopePolicyPort, snapshot: AccountSnapshot, target_symbols: List[str] = None
) -> List[str]:
    """Limit the expensive native TypeQL match to changed subjects when known.

    The ABox still includes the complete live portfolio so portfolio and
    exposure rules retain their full context. Only the TypeDB rule query and
    InferenceBox generation are narrowed to the material event subjects.
    """
    available = set(_store.snapshot_symbols(snapshot))
    selected = []
    for symbol in target_symbols or []:
        clean = str(symbol or "").upper().strip()
        if clean and clean in available and clean not in selected:
            selected.append(clean)
    if target_symbols:
        return selected
    return _store.snapshot_symbols(snapshot)


def scheduler_target_symbol_limit(reasoning_context: Dict[str, object] = None) -> int:
    """Read the runner's operational cap without changing investment meaning.

    The adaptive queue decides how many requested subjects may share one
    coherent generation. The projection layer must enforce that same cap;
    otherwise its configured native limit can refill a one-subject retry
    with unrelated impact-plan symbols.
    """
    context = reasoning_context if isinstance(reasoning_context, dict) else {}
    targets = [
        str(symbol or "").upper().strip()
        for symbol in context.get("targetSymbols") or []
        if str(symbol or "").strip()
    ]
    plan = context.get("batchPlan")
    plan = plan if isinstance(plan, dict) else {}
    if not targets or not plan:
        return 0
    try:
        return max(0, min(200, int(float(plan.get("targetSymbolLimit") or 0))))
    except (TypeError, ValueError):
        return 0


def reasoning_queue_pressure(
    reasoning_context: Dict[str, object] = None
) -> Dict[str, object]:
    context = reasoning_context if isinstance(reasoning_context, dict) else {}
    pressure = context.get("queuePressure")
    pressure = pressure if isinstance(pressure, dict) else {}

    def number(key: str) -> int:
        try:
            return max(0, int(float(pressure.get(key) or 0)))
        except (TypeError, ValueError):
            return 0

    effective_pending = number("effectivePendingCount")
    selected = number("selectedRequestCount")
    omitted_symbols = number("omittedSymbolCount")
    return {
        "effectivePendingCount": effective_pending,
        "selectedRequestCount": selected,
        "omittedSymbolCount": omitted_symbols,
        "hasDeferredWork": bool(
            pressure.get("hasDeferredWork")
            or omitted_symbols > 0
            or effective_pending > selected
        ),
    }


def target_scoped_patch_targets(
    _store: ScopePolicyPort,
    snapshot: AccountSnapshot,
    active_metadata: Dict[str, object],
    scoped_identity: Dict[str, object],
    requested_symbols: List[str] = None,
    reasoning_context: Dict[str, object] = None,
) -> Dict[str, object]:
    """Choose a safe incremental write set before replacing a manifest.

    TypeDB still receives the full active world through the manifest. This
    only prevents a one-symbol observation from rewriting unchanged
    symbols. A global request without an explicit subject, a first
    projection keeps the full path. A timer never expands a local event.
    """
    preliminary = _store.inference_impact_plan(
        snapshot,
        active_metadata,
        scoped_identity,
        requested_symbols,
        reasoning_context=reasoning_context,
    )
    available = _store.snapshot_symbols(snapshot)
    explicit = (
        _store.inference_symbols(snapshot, requested_symbols)
        if requested_symbols
        else []
    )
    inferred = explicit or _store.inference_symbols(
        snapshot,
        preliminary.get("inferenceTargetSymbols") or requested_symbols,
    )
    inferred = _store.bounded_native_inference_symbols(
        snapshot,
        inferred,
        requested_symbols,
        scheduler_target_symbol_limit=_store.scheduler_target_symbol_limit(
            reasoning_context
        ),
    )
    # ``inference_symbols`` falls back to the full snapshot when its
    # argument is empty. An explicit scheduler request is authoritative:
    # impact analysis may select rule families and shared context, but it
    # must not append unrelated subjects to this execution turn.
    integrity_age_minutes = _store.scope_integrity_audit_age_minutes(active_metadata)
    integrity_due = (
        integrity_age_minutes is None
        or integrity_age_minutes >= _store.scope_integrity_audit_interval_minutes()
    )
    active_manifest_ready = bool(
        str((active_metadata or {}).get("status") or "").lower() == "ok"
        and (active_metadata or {}).get("scopePlan")
        and (active_metadata or {}).get("scopeGenerationIds")
        and str((active_metadata or {}).get("scopedAboxManifestVersion") or "")
        == SCOPED_ABOX_MANIFEST_VERSION
        and str((active_metadata or {}).get("scopeTopologyVersion") or "")
        == SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION
    )
    overlay_contract_migration_required = bool(
        _store.world_partitioned_reasoning_enabled()
        and str((active_metadata or {}).get("status") or "").lower() == "ok"
        and str(
            (active_metadata or {}).get("accountOverlayProjectionContractVersion") or ""
        )
        != ACCOUNT_OVERLAY_PROJECTION_CONTRACT_VERSION
    )
    base = {
        "preliminaryImpactPlan": compact_inference_impact_plan(preliminary),
        "targetSymbols": list(inferred),
        "explicitTargetSymbols": list(explicit),
        "availableSymbolCount": len(available),
        "scopeIntegrityAuditIntervalMinutes": _store.scope_integrity_audit_interval_minutes(),
        "scopeIntegrityAuditAgeMinutes": integrity_age_minutes,
        "scopeIntegrityAuditDue": integrity_due,
        "automaticFullProjectionBlocked": active_manifest_ready,
        "accountOverlayContractMigrationRequired": overlay_contract_migration_required,
        "queuePressure": _store.reasoning_queue_pressure(reasoning_context),
        "fallbackReason": "",
        "factSlotPlan": build_fact_slot_projection_plan(
            inferred,
            preliminary.get("requestedFactFamilies") or [],
            requested_fact_families_by_symbol=(reasoning_context or {}).get(
                "requestedScopeFamiliesBySymbol"
            )
            or {},
            changed_fields_by_symbol=(reasoning_context or {}).get(
                "changedFieldsBySymbol"
            )
            or {},
            event_boundary_authoritative=bool(
                (reasoning_context or {}).get("eventFactBoundaryAuthoritative")
            ),
            requested_dependency_keys=(reasoning_context or {}).get(
                "requestedDependencyKeys"
            )
            or [],
            requested_dependency_keys_by_symbol=(reasoning_context or {}).get(
                "requestedDependencyKeysBySymbol"
            )
            or {},
            dependency_boundary_authoritative=bool(
                (reasoning_context or {}).get("eventDependencyBoundaryAuthoritative")
            ),
            derived_fact_families_by_symbol={
                symbol: ["model-signal"]
                for symbol in inferred
                if any(
                    str(item.get("scopeFamily") or "").strip().lower() == "model-signal"
                    and scope_symbol(item.get("scopeId")) == symbol
                    for item in (scoped_identity or {}).get("scopePlan") or []
                    if isinstance(item, dict)
                )
            },
        ),
    }
    # A reasoning worker can intentionally schedule one subject even when
    # a shared macro or portfolio fact also changed. Persist that subject
    # and the shared scopes now; the untouched subjects retain their last
    # coherent context until their own queued turn or an explicit source
    # update. Without an explicit worker target, preserve the
    # conservative whole-portfolio path only for an explicitly global
    # change. Integrity checks are owned by the maintenance worker and
    # never promote a local event to this path.
    if (
        preliminary.get("impactScope") == "MARKET_CONTEXT"
        and not explicit
        and not inferred
    ):
        return {
            **base,
            "status": "market-context-awaiting-related-subjects",
            "eligible": True,
            "fallbackReason": "no-related-subject-for-market-context-revision",
        }
    if overlay_contract_migration_required:
        return {
            **base,
            "status": "account-overlay-contract-migration",
            "eligible": False,
            "fallbackReason": "legacy-portfolio-market-mirror-must-be-removed",
        }
    if not active_manifest_ready:
        return {
            **base,
            "status": "initial-scoped-manifest-bootstrap",
            "eligible": False,
            "fallbackReason": "active-scoped-manifest-unavailable",
        }
    if not inferred:
        return {
            **base,
            "status": "full-target-set",
            "eligible": False,
            "fallbackReason": "no-inference-target-symbol",
        }
    if len(inferred) >= len(available):
        return {
            **base,
            "status": "full-target-set",
            "eligible": False,
            "fallbackReason": "target-set-covers-active-portfolio",
        }
    return {
        **base,
        "status": (
            "target-scoped-integrity-audit-due"
            if integrity_due
            else (
                "target-scoped-explicit-global-context"
                if preliminary.get("globalImpact")
                else (
                    "target-scoped-quality-global-context"
                    if preliminary.get("qualityScopedGlobalContext")
                    else "target-scoped"
                )
            )
        ),
        "eligible": True,
        "fallbackReason": "",
    }


def inference_impact_plan(
    _store: ScopePolicyPort,
    snapshot: AccountSnapshot,
    active_abox: Dict[str, object],
    scoped_identity: Dict[str, object],
    target_symbols: List[str] = None,
    reasoning_context: Dict[str, object] = None,
) -> Dict[str, object]:
    """Route native inference from immutable scope changes, not a timer."""
    previous_scope_plan = list((active_abox or {}).get("scopePlan") or [])
    next_scope_plan = list((scoped_identity or {}).get("scopePlan") or [])
    return build_inference_impact_plan(
        previous_scope_plan,
        next_scope_plan,
        _store.snapshot_symbols(snapshot),
        explicit_target_symbols=target_symbols,
        rules=_store.rulebox_rules_for_impact(),
        requested_fact_families=(reasoning_context or {}).get("requestedScopeFamilies")
        or [],
        requested_fact_families_by_symbol=(reasoning_context or {}).get(
            "requestedScopeFamiliesBySymbol"
        )
        or {},
        requested_dependency_keys=(reasoning_context or {}).get(
            "requestedDependencyKeys"
        )
        or [],
        requested_dependency_keys_by_symbol=(reasoning_context or {}).get(
            "requestedDependencyKeysBySymbol"
        )
        or {},
        dependency_boundary_authoritative=bool(
            (reasoning_context or {}).get("eventDependencyBoundaryAuthoritative")
        ),
    )


def rulebox_rules_for_impact(_store: ScopePolicyPort) -> List[Dict[str, object]]:
    cached = getattr(_store, "_rulebox_impact_rules", None)
    if isinstance(cached, list):
        return [dict(item) for item in cached if isinstance(item, dict)]
    if not hasattr(_store.repository, "rulebox_snapshot"):
        return []
    try:
        snapshot = _store.repository.rulebox_snapshot()
    except (
        Exception
    ):  # noqa: BLE001 - complete native evaluation remains safe without dependency metadata.
        return []
    rules = snapshot.get("rules") if isinstance(snapshot, dict) else []
    return [dict(item) for item in rules or [] if isinstance(item, dict)]


def inference_snapshot_limit(_store: ScopePolicyPort) -> int:
    try:
        value = int(
            float(str(_store.settings.get("investmentBrainInferenceBoxLimit") or 500))
        )
    except (TypeError, ValueError):
        value = 500
    return max(80, min(500, value))


def has_projectable_data(_store: ScopePolicyPort, snapshot: AccountSnapshot) -> bool:
    if not snapshot.has_live_account_data():
        return False
    if any(
        item
        for item in list(snapshot.positions or []) + list(snapshot.watchlist or [])
        if not item.is_cash()
    ):
        return True
    # BTC/ETH market subjects are not account holdings. They remain
    # projectable when CoinGecko supplies a current source fact.
    return bool(crypto_markets_by_symbol(snapshot.external_signals))


def typedb_projection_deferred(_store: ScopePolicyPort) -> bool:
    if _store.active_graph_store_key() != "typedb":
        return False
    if "typedbNativeRuleExecutionEnabled" not in _store.settings:
        return False
    return str(
        _store.settings.get("typedbNativeRuleExecutionEnabled") or ""
    ).strip().lower() in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def active_graph_store_key(
    _store: ScopePolicyPort, result: Dict[str, object] = None
) -> str:
    key = str(getattr(_store.repository, "store_key", "") or "").strip()
    return key or "graph-store"
