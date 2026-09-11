"""Pure acceptance policy for subject-scoped ontology inference fanout.

The policy compares two read-only executions against one immutable ABox:
one query set containing every requested subject and independently evaluated
subject query sets.  It does not select investment rules or mutate graph state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Iterable, Mapping


ONTOLOGY_SUBJECT_FANOUT_PROOF_VERSION = "ontology-subject-fanout-proof-v1"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _symbols(values: Iterable[object]) -> list[str]:
    return sorted({
        str(value or "").upper().strip()
        for value in values or []
        if str(value or "").strip()
    })


def _match_identity(raw: Mapping[str, object]) -> Dict[str, object]:
    item = _mapping(raw)
    condition_ids = sorted({
        str(_mapping(condition).get("conditionId") or "").strip()
        for condition in item.get("matchedConditions") or []
        if str(_mapping(condition).get("conditionId") or "").strip()
    })
    return {
        "ruleId": str(item.get("ruleId") or "").strip(),
        "sourceId": str(item.get("sourceId") or "").strip(),
        "matchedConditionIds": condition_ids,
        "evidenceRelationIds": sorted({
            str(value or "").strip()
            for value in item.get("evidenceRelationIds") or []
            if str(value or "").strip()
        }),
    }


def native_match_signatures(matches: Iterable[Mapping[str, object]]) -> list[str]:
    """Return stable semantic signatures without execution-mode metadata."""
    signatures = set()
    for raw in matches or []:
        identity = _match_identity(raw)
        if not identity["ruleId"] or not identity["sourceId"]:
            continue
        encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        signatures.add(hashlib.sha256(encoded.encode("utf-8")).hexdigest())
    return sorted(signatures)


def native_rule_coverage(result: Mapping[str, object]) -> Dict[str, object]:
    """Normalize rule coverage independently of target-shard row ordering."""
    values = _mapping(result)
    executed: Dict[str, set[str]] = {}
    for raw in values.get("executedRules") or []:
        item = _mapping(raw)
        rule_id = str(item.get("ruleId") or "").strip()
        if not rule_id:
            continue
        executed.setdefault(rule_id, set()).update(_symbols(item.get("candidateSymbols") or []))
    failed: Dict[str, set[str]] = {}
    for raw in values.get("skippedRules") or []:
        item = _mapping(raw)
        status = str(item.get("status") or "").strip()
        if status in {"", "not-applicable", "not-applicable-preflight", "planned"}:
            continue
        rule_id = str(item.get("ruleId") or "").strip()
        if not rule_id:
            continue
        failed.setdefault(rule_id, set()).update(_symbols(item.get("candidateSymbols") or []))
    return {
        "executed": {
            rule_id: sorted(symbols)
            for rule_id, symbols in sorted(executed.items())
        },
        "failed": {
            rule_id: sorted(symbols)
            for rule_id, symbols in sorted(failed.items())
        },
        "coreEvaluationComplete": bool(
            values.get("coreNativeInferenceEvaluationComplete")
            if "coreNativeInferenceEvaluationComplete" in values
            else str(values.get("status") or "") == "ok"
        ),
        "fullEvaluationComplete": bool(
            values.get("nativeInferenceEvaluationComplete")
            if "nativeInferenceEvaluationComplete" in values
            else str(values.get("status") or "") == "ok"
        ),
        "nativeCoverageStatus": str(values.get("nativeCoverageStatus") or ""),
    }


def merge_subject_results(results: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    """Merge independent subject reads for comparison without materialization."""
    rows = [_mapping(item) for item in results or []]
    matches = []
    executed_rules = []
    skipped_rules = []
    for row in rows:
        matches.extend(_mapping(item) for item in row.get("matches") or [])
        executed_rules.extend(_mapping(item) for item in row.get("executedRules") or [])
        skipped_rules.extend(_mapping(item) for item in row.get("skippedRules") or [])
    signatures = native_match_signatures(matches)
    statuses = [str(row.get("status") or "error") for row in rows]
    core_complete = bool(rows) and all(bool(
        row.get("coreNativeInferenceEvaluationComplete")
        if "coreNativeInferenceEvaluationComplete" in row
        else str(row.get("status") or "") == "ok"
    ) for row in rows)
    full_complete = bool(rows) and all(bool(
        row.get("nativeInferenceEvaluationComplete")
        if "nativeInferenceEvaluationComplete" in row
        else str(row.get("status") or "") == "ok"
    ) for row in rows)
    return {
        "status": "ok" if statuses and set(statuses) == {"ok"} else "partial",
        "coreNativeInferenceEvaluationComplete": core_complete,
        "nativeInferenceEvaluationComplete": full_complete,
        "nativeCoverageStatus": "complete" if full_complete else "partial",
        "matches": matches,
        "matchSignatures": signatures,
        "matchedCount": len(signatures),
        "executedRules": executed_rules,
        "skippedRules": skipped_rules,
        "subjectStatuses": statuses,
    }


def evaluate_subject_fanout_comparison(
    combined_result: Mapping[str, object],
    subject_results: Iterable[Mapping[str, object]],
    *,
    combined_duration_ms: int,
    fanout_duration_ms: int,
    generation_unchanged: bool,
    minimum_reduction_pct: float = 40.0,
) -> Dict[str, object]:
    """Apply the fail-closed runtime activation gate to a read-only replay."""
    combined = _mapping(combined_result)
    merged = merge_subject_results(subject_results)
    combined_signatures = native_match_signatures(combined.get("matches") or [])
    fanout_signatures = list(merged.get("matchSignatures") or [])
    combined_coverage = native_rule_coverage(combined)
    fanout_coverage = native_rule_coverage(merged)
    semantic_match = combined_signatures == fanout_signatures
    coverage_match = combined_coverage == fanout_coverage
    combined_ms = max(0, int(combined_duration_ms or 0))
    fanout_ms = max(0, int(fanout_duration_ms or 0))
    reduction_pct = round(((combined_ms - fanout_ms) / combined_ms) * 100, 1) if combined_ms else 0.0
    complete = bool(
        str(combined.get("status") or "") == "ok"
        and bool(
            combined.get("coreNativeInferenceEvaluationComplete")
            if "coreNativeInferenceEvaluationComplete" in combined
            else True
        )
        and str(merged.get("status") or "") == "ok"
        and bool(merged.get("coreNativeInferenceEvaluationComplete"))
    )
    accepted = bool(
        generation_unchanged
        and complete
        and semantic_match
        and coverage_match
        and reduction_pct >= float(minimum_reduction_pct or 0)
    )
    reason_codes = []
    if not generation_unchanged:
        reason_codes.append("active-generation-changed")
    if not complete:
        reason_codes.append("incomplete-rule-evaluation")
    if not semantic_match:
        reason_codes.append("inference-match-mismatch")
    if not coverage_match:
        reason_codes.append("rule-coverage-mismatch")
    if reduction_pct < float(minimum_reduction_pct or 0):
        reason_codes.append("minimum-performance-gain-not-met")
    return {
        "contract": ONTOLOGY_SUBJECT_FANOUT_PROOF_VERSION,
        "status": "accepted" if accepted else "rejected",
        "acceptedForRuntime": accepted,
        "generationUnchanged": bool(generation_unchanged),
        "evaluationComplete": complete,
        "semanticMatch": semantic_match,
        "ruleCoverageMatch": coverage_match,
        "combinedMatchedCount": len(combined_signatures),
        "fanoutMatchedCount": len(fanout_signatures),
        "combinedDurationMs": combined_ms,
        "fanoutDurationMs": fanout_ms,
        "durationReductionPct": reduction_pct,
        "minimumDurationReductionPct": float(minimum_reduction_pct or 0),
        "reasonCodes": reason_codes,
        "combinedCoverage": combined_coverage,
        "fanoutCoverage": fanout_coverage,
    }
