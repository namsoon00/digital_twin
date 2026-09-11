"""Scope implementation; facade-independent dependencies."""

from __future__ import annotations
from .scope_ports import ScopePort
from datetime import datetime, timezone
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Dict, List
import hashlib


def incremental_equivalence_audit_selected(
    _store: ScopePort,
    snapshot: AccountSnapshot,
    symbols: List[str],
    impact_plan: Dict[str, object],
    selection_context: Dict[str, object],
) -> bool:
    """Select a deterministic low-rate full pass for reuse validation."""

    sample_pct = _store.incremental_equivalence_audit_sample_pct()
    if (
        sample_pct <= 0
        or str(selection_context.get("proofSource") or "") != "typedb-rule-result-slots"
        or not isinstance(selection_context.get("ruleStatesBySymbol"), dict)
        or not impact_plan.get("deferredRuleIds")
    ):
        return False
    seed = "|".join(
        [
            str(snapshot.account_id or ""),
            str(snapshot.generated_at or ""),
            ",".join(
                sorted(str(symbol or "").upper().strip() for symbol in symbols or [])
            ),
            ",".join(
                str(rule_id or "")
                for rule_id in impact_plan.get("candidateRuleIds") or []
            ),
        ]
    )
    bucket = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % 100
    return bucket < sample_pct


def native_inference_symbol_limit(_store: ScopePort) -> int:
    """Return the configured TypeDB native-rule work bound, if enabled."""
    if _store.active_graph_store_key() != "typedb":
        return 0
    raw = _store.settings.get("typedbNativeRuleTargetSymbolLimit")
    if raw is None or not str(raw).strip():
        return 0
    try:
        return max(1, min(200, int(float(str(raw)))))
    except (TypeError, ValueError):
        return 0


def bounded_native_inference_symbols(
    _store: ScopePort,
    snapshot: AccountSnapshot,
    inferred_symbols: List[str],
    requested_symbols: List[str] = None,
    scheduler_target_symbol_limit: int = 0,
) -> List[str]:
    """Prioritize triggering subjects without dropping global ABox context.

    A portfolio or macro scope can affect many holdings, but evaluating all
    of them in one native TypeDB cycle defeats the worker's configured
    per-cycle symbol bound. The complete ABox remains active for each
    rule; only the current RuleBox subjects are sequenced across cycles.
    An explicit request is already a complete scheduler decision and must
    never be refilled with unrelated holdings merely because the configured
    upper bound has spare capacity.
    """
    requested = (
        _store.inference_symbols(snapshot, requested_symbols)
        if requested_symbols
        else []
    )
    limit = _store.native_inference_symbol_limit()
    if requested:
        return requested[:limit] if limit else requested
    if not limit:
        return list(inferred_symbols or [])
    try:
        scheduled_limit = max(
            0, min(200, int(float(scheduler_target_symbol_limit or 0)))
        )
    except (TypeError, ValueError):
        scheduled_limit = 0
    if scheduled_limit:
        limit = min(limit, scheduled_limit)
    available = _store.snapshot_symbols(snapshot)
    ordered = []
    for symbol in list(inferred_symbols or []) + available:
        clean = str(symbol or "").upper().strip()
        if clean and clean in available and clean not in ordered:
            ordered.append(clean)
    return ordered[:limit]


def scope_integrity_audit_interval_minutes(_store: ScopePort) -> float:
    """Return the read-only Manifest integrity audit cadence."""
    try:
        value = float(
            str(
                _store.settings.get("ontologyScopeIntegrityAuditIntervalMinutes")
                or "30"
            )
        )
    except (TypeError, ValueError):
        value = 30.0
    return max(5.0, min(24.0 * 60.0, value))


def scope_integrity_audit_age_minutes(
    _store: ScopePort, active_metadata: Dict[str, object]
):
    """Return the last read-only audit age without changing projection scope."""
    stamp = str(
        (active_metadata or {}).get("lastScopeIntegrityAuditAt")
        or (active_metadata or {}).get("lastFullScopeReconcileAt")
        or (active_metadata or {}).get("asOf")
        or ""
    ).strip()
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age_minutes = (
        datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    ).total_seconds() / 60.0
    return max(0.0, age_minutes)
