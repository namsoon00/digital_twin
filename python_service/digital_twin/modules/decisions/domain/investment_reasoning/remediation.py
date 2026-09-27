"""Deterministic follow-up ownership for non-actionable reasoning outcomes."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping


REASONING_REMEDIATION_VERSION = "reasoning-remediation-plan-v1"


def _text(value: object) -> str:
    return str(value or "").strip()


def _gap_rows(values: Iterable[object]) -> list:
    rows = []
    for value in values or []:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        if isinstance(value, Mapping):
            rows.append(dict(value))
    return rows


def reasoning_remediation_plan(
    *,
    disposition_code: object,
    data_gaps: Iterable[object] = (),
    qualification_reasons: Iterable[object] = (),
    graph_trace_complete: bool = True,
) -> Dict[str, object]:
    code = _text(disposition_code).upper() or "UNKNOWN"
    gaps = _gap_rows(data_gaps)
    blocking = [row for row in gaps if bool(row.get("blocking"))]
    failed = [row for row in blocking if _text(row.get("state")).lower() == "failed"]
    scheduled = [row for row in gaps if _text(row.get("state")).lower() == "not-yet-published"]
    stale_or_missing = [
        row for row in blocking
        if _text(row.get("state")).lower() in {"missing", "stale", "partial", "unavailable"}
    ]
    qualification = sorted({_text(value) for value in qualification_reasons or [] if _text(value)})

    if code == "DATA_SOURCE_FAILURE" or failed:
        reason_class, owner, action, trigger = (
            "source-failure", "external-data", "retry-provider-with-circuit-policy", "provider-recovered"
        )
    elif code == "WAITING_FOR_SCHEDULED_SOURCE" or scheduled:
        reason_class, owner, action, trigger = (
            "scheduled-source-wait", "calendar", "collect-at-scheduled-release", "scheduled-source-published"
        )
    elif code == "HYPOTHESIS_QUALIFICATION_PENDING":
        reason_class, owner, action, trigger = (
            "hypothesis-observation", "outcomes", "observe-authored-outcome-contract", "qualification-state-changed"
        )
    elif code == "RULE_COVERAGE_GAP_CANDIDATE":
        reason_class, owner, action, trigger = (
            "rule-hypothesis-coverage", "model-registry", "queue-governed-rule-coverage-review", "coverage-contract-materialized"
        )
    elif stale_or_missing:
        reason_class, owner, action, trigger = (
            "required-data-gap", "market-data", "refresh-required-fact-slices", "required-facts-refreshed"
        )
    elif not graph_trace_complete:
        reason_class, owner, action, trigger = (
            "reasoning-integrity", "reasoning", "retry-from-frozen-source-snapshot", "complete-graph-trace-produced"
        )
    elif code == "NO_MATERIAL_PREDICTIVE_RULE_MATCH":
        reason_class, owner, action, trigger = (
            "no-material-transition", "reasoning", "wait-for-new-semantic-transition", "material-fact-transition"
        )
    elif code == "HYPOTHESIS_RESEARCH_ONLY":
        reason_class, owner, action, trigger = (
            "research-only-hypothesis", "outcomes", "continue-shadow-observation", "hypothesis-qualified-or-retired"
        )
    elif code == "JUDGEMENT_BLOCKED":
        reason_class, owner, action, trigger = (
            "judgement-contract-blocked", "reasoning", "re-evaluate-after-input-contract-change", "blocking-contract-resolved"
        )
    else:
        reason_class, owner, action, trigger = (
            "none", "decisions", "none", "new-candidate-set"
        )

    return {
        "version": REASONING_REMEDIATION_VERSION,
        "dispositionCode": code,
        "reasonClass": reason_class,
        "owner": owner,
        "automaticAction": action,
        "retryTrigger": trigger,
        "automatic": action != "none",
        "blockingGapCodes": sorted({_text(row.get("code")) for row in blocking if _text(row.get("code"))}),
        "scheduledAt": min(
            (_text(row.get("expectedAt")) for row in scheduled if _text(row.get("expectedAt"))),
            default="",
        ),
        "qualificationReasons": qualification,
    }
