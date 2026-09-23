"""Audit dependency-selected inference against a bounded full TypeDB pass."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping


INCREMENTAL_EQUIVALENCE_VERSION = "incremental-inference-equivalence-v2-safety-circuit"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _symbols(values: Iterable[object]) -> list:
    return sorted({_clean(item).upper() for item in values or [] if _clean(item)})


def _symbol_from_source_id(source_id: object, symbols: Iterable[object]) -> str:
    value = _clean(source_id).upper()
    for symbol in _symbols(symbols):
        if value == symbol or value.endswith(":" + symbol):
            return symbol
    return ""


def full_rule_states(
    execution: Mapping[str, object],
    symbols: Iterable[object],
    rule_ids: Iterable[object],
) -> Dict[str, object]:
    """Build per-subject states only from a complete full native evaluation."""

    values = dict(execution or {})
    native = values.get("nativeMatchResult")
    native = dict(native or {}) if isinstance(native, Mapping) else values
    targets = _symbols(symbols)
    rules = sorted({_clean(item) for item in rule_ids or [] if _clean(item)})
    if (
        str(values.get("status") or "").lower() != "ok"
        or bool(values.get("nativeRuleSelectionApplied"))
        or not bool(values.get("nativeInferenceEvaluationComplete", True))
        or not targets
        or not rules
    ):
        return {
            "complete": False,
            "reason": "current execution is not a complete full TypeDB evaluation",
            "statesBySymbol": {},
        }
    states = {
        symbol: {rule_id: "not-matched" for rule_id in rules}
        for symbol in targets
    }
    unresolved = []
    for item in native.get("matches") or []:
        if not isinstance(item, Mapping):
            continue
        rule_id = _clean(item.get("ruleId"))
        if rule_id not in rules:
            continue
        symbol = _symbol_from_source_id(item.get("sourceId"), targets)
        if not symbol:
            unresolved.append({"ruleId": rule_id, "sourceId": _clean(item.get("sourceId"))})
            continue
        states[symbol][rule_id] = "matched"
    return {
        "complete": not unresolved,
        "reason": "" if not unresolved else "one or more TypeDB matches lacked a subject identity",
        "statesBySymbol": states,
        "unresolvedMatches": unresolved[:20],
    }


def compare_incremental_rule_states(
    prior_states_by_symbol: Mapping[str, object],
    execution: Mapping[str, object],
    symbols: Iterable[object],
    deferred_rule_ids: Iterable[object],
) -> Dict[str, object]:
    """Compare only rules that an incremental pass would have reused."""

    targets = _symbols(symbols)
    deferred = sorted({_clean(item) for item in deferred_rule_ids or [] if _clean(item)})
    prior = {
        _clean(symbol).upper(): dict(states or {})
        for symbol, states in dict(prior_states_by_symbol or {}).items()
        if _clean(symbol) and isinstance(states, Mapping)
    }
    current = full_rule_states(execution, targets, deferred)
    if not prior or not deferred or not bool(current.get("complete")):
        return {
            "version": INCREMENTAL_EQUIVALENCE_VERSION,
            "status": "inconclusive",
            "verified": False,
            "reconciledByFullEvaluation": False,
            "reason": str(current.get("reason") or "prior slot states or deferred rules are unavailable"),
            "comparedRuleCount": 0,
            "mismatches": [],
        }
    current_states = dict(current.get("statesBySymbol") or {})
    mismatches = []
    compared = 0
    missing = []
    for symbol in targets:
        prior_symbol = prior.get(symbol) or {}
        current_symbol = current_states.get(symbol) or {}
        for rule_id in deferred:
            before = _clean(prior_symbol.get(rule_id))
            after = _clean(current_symbol.get(rule_id))
            if not before or not after:
                missing.append({"symbol": symbol, "ruleId": rule_id})
                continue
            compared += 1
            if before != after:
                mismatches.append({
                    "symbol": symbol,
                    "ruleId": rule_id,
                    "priorState": before,
                    "fullEvaluationState": after,
                })
    if missing:
        status = "inconclusive"
        verified = False
        reason = "slot coverage was incomplete for one or more deferred rules"
    elif mismatches:
        status = "mismatch-reconciled"
        verified = False
        reason = "the full TypeDB result replaced stale incremental slot states"
    else:
        status = "verified-equivalent"
        verified = True
        reason = "every reused deferred rule matched the full TypeDB result"
    return {
        "version": INCREMENTAL_EQUIVALENCE_VERSION,
        "status": status,
        "verified": verified,
        "reconciledByFullEvaluation": bool(mismatches),
        "reason": reason,
        "targetSymbolCount": len(targets),
        "deferredRuleCount": len(deferred),
        "comparedRuleCount": compared,
        "missingComparisonCount": len(missing),
        "mismatchCount": len(mismatches),
        "mismatches": mismatches[:20],
    }


def incremental_selection_safety_state(
    audit_rows: Iterable[Mapping[str, object]],
    required_full_recovery_runs: int = 3,
) -> Dict[str, object]:
    """Derive a fail-closed selection circuit from persisted run audits.

    Rows are expected newest first. A sampled mismatch is already reconciled
    by that run's full TypeDB result, but it also proves that the dependency
    selector cannot be trusted immediately. Selection remains suspended until
    a bounded number of subsequent complete full evaluations have succeeded.
    A RuleBox/TBox release boundary is applied by the caller before rows reach
    this function.
    """

    required = max(1, min(20, int(required_full_recovery_runs or 3)))
    clean_full_runs = 0
    inspected = 0
    for row in audit_rows or []:
        if not isinstance(row, Mapping):
            continue
        inspected += 1
        result = row.get("result")
        result = dict(result or {}) if isinstance(result, Mapping) else {}
        audit = result.get("incrementalEquivalenceAudit")
        audit = dict(audit or {}) if isinstance(audit, Mapping) else {}
        audit_status = _clean(audit.get("status")).lower()
        if audit_status == "mismatch-reconciled" or int(
            audit.get("mismatchCount") or 0
        ) > 0:
            recovered = clean_full_runs >= required
            return {
                "version": INCREMENTAL_EQUIVALENCE_VERSION,
                "status": "healthy" if recovered else "suspended",
                "selectionAllowed": recovered,
                "reason": (
                    "required full-evaluation recovery evidence is complete"
                    if recovered
                    else "a sampled incremental result differed from the full TypeDB result"
                ),
                "mismatchRunId": _clean(row.get("runId")),
                "fullRecoveryRunCount": clean_full_runs,
                "requiredFullRecoveryRunCount": required,
                "inspectedRunCount": inspected,
            }
        replay = result.get("nativeReplayValidation")
        replay = dict(replay or {}) if isinstance(replay, Mapping) else {}
        full_verified = bool(
            str(result.get("status") or "").lower() in {"ok", "completed"}
            and replay.get("verified")
            and not replay.get("selectionApplied")
            and replay.get("nativeEvaluationComplete")
            and replay.get("coverageComplete")
        )
        if full_verified:
            clean_full_runs += 1
    return {
        "version": INCREMENTAL_EQUIVALENCE_VERSION,
        "status": "healthy",
        "selectionAllowed": True,
        "reason": "no unresolved incremental equivalence mismatch was found",
        "mismatchRunId": "",
        "fullRecoveryRunCount": clean_full_runs,
        "requiredFullRecoveryRunCount": required,
        "inspectedRunCount": inspected,
    }
