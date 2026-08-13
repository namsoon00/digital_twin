"""Deterministic, review-only evaluation of frozen hypothesis criteria."""

from typing import Dict, Iterable, Mapping, Optional, Tuple

from .hypothesis_outcome_contract import HypothesisOutcomeContract, text


HYPOTHESIS_OUTCOME_EVALUATION_VERSION = "hypothesis-outcome-criterion-evaluation-v1"
SUPPORTING_ROLES = {"cause", "result"}


def optional_number(value: object) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sequence_count(value: object) -> Optional[float]:
    if isinstance(value, (list, tuple, set, dict)):
        return float(len(value))
    return optional_number(value)


def first_number(source: Mapping[str, object], keys: Iterable[str]) -> Tuple[Optional[float], str]:
    for key in keys:
        if key not in source:
            continue
        value = optional_number(source.get(key))
        if value is not None:
            return value, key
    return None, ""


def metric_value(
    metric: str,
    facts: Mapping[str, object],
    instrument_return_pct: float,
) -> Tuple[Optional[float], str]:
    source = dict(facts or {})
    if metric == "instrumentReturnPct":
        return float(instrument_return_pct), "decision-price-to-observed-price"
    if metric == "benchmarkReturnPct":
        return first_number(source, ["benchmarkReturnPct", "marketReturnPct"])
    if metric == "excessReturnPct":
        direct, basis = first_number(source, ["excessReturnPct", "relativeReturnPct"])
        if direct is not None:
            return direct, basis
        benchmark, benchmark_basis = first_number(source, ["benchmarkReturnPct", "marketReturnPct"])
        if benchmark is None:
            return None, "benchmark-return-missing"
        return round(float(instrument_return_pct) - benchmark, 6), "instrument-return-minus-" + benchmark_basis
    if metric == "verifiedEventCount":
        for key in ["verifiedEvents", "verifiedClaims", "researchEvidence", "disclosureIds"]:
            if key in source:
                return sequence_count(source.get(key)), key
        return None, "verified-event-evidence-missing"
    if metric == "counterEvidenceCount":
        for key in ["counterEvidenceIds", "contradictedEvidenceIds", "rejectedClaims"]:
            if key in source:
                return sequence_count(source.get(key)), key
        return None, "counter-evidence-missing"
    aliases = {
        "profitLossRate": ["profitLossRate"],
        "volumeRatio": ["volumeRatio", "timeAdjustedVolumeRatio"],
        "tradeStrength": ["tradeStrength"],
        "foreignNetVolume": ["foreignNetVolume"],
        "institutionNetVolume": ["institutionNetVolume"],
        "individualNetVolume": ["individualNetVolume"],
        "shareCountChangePct": ["shareCountChangePct", "dilutedShareCountChangePct"],
        "freeCashFlowChangePct": ["freeCashFlowChangePct", "fcfChangePct"],
    }
    return first_number(source, aliases.get(metric, [metric]))


def source_policy_status(criterion, facts: Mapping[str, object]) -> Tuple[bool, str]:
    required = {text(item).lower() for item in criterion.source_policy if text(item)}
    if not required:
        return True, ""
    observed = {
        text(value).lower()
        for value in [
            facts.get("provider"),
            facts.get("source"),
            facts.get("observationSource"),
            facts.get("evidenceSource"),
        ]
        if text(value)
    }
    raw_sources = facts.get("evidenceSources") or []
    if not isinstance(raw_sources, (list, tuple, set)):
        raw_sources = [raw_sources]
    for value in raw_sources:
        if text(value):
            observed.add(text(value).lower())
    if any(any(policy in source or source in policy for source in observed) for policy in required):
        return True, ""
    return False, "required-source-not-observed"


def compare(value: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    if operator == "!=":
        return value != threshold
    return value == threshold


def legacy_directional_status(stance: str, instrument_return_pct: float) -> str:
    if not instrument_return_pct or stance not in {"risk", "support"}:
        return "inconclusive"
    if stance == "risk":
        return "directionally-corroborated" if instrument_return_pct < 0 else "directionally-contradicted"
    return "directionally-corroborated" if instrument_return_pct > 0 else "directionally-contradicted"


def evaluate_hypothesis_outcome(
    contract_payload: Mapping[str, object],
    stance: str,
    facts: Mapping[str, object],
    instrument_return_pct: float,
    horizon_minutes: int,
) -> Dict[str, object]:
    contract = HypothesisOutcomeContract.from_dict(contract_payload).resolved()
    criteria = [
        item
        for item in contract.criteria
        if not item.horizon_minutes or int(item.horizon_minutes) == int(horizon_minutes or 0)
    ]
    if not criteria and contract.criteria:
        return {
            "version": HYPOTHESIS_OUTCOME_EVALUATION_VERSION,
            "mode": "contract-criteria-no-applicable",
            "selectedHypothesisStatus": "inconclusive",
            "criterionAssessments": [],
            "requiredCriterionCount": 0,
            "passedCriterionCount": 0,
            "failedCriterionCount": 0,
            "unknownCriterionCount": 0,
            "missingRequiredMetricIds": [],
        }
    if not criteria:
        return {
            "version": HYPOTHESIS_OUTCOME_EVALUATION_VERSION,
            "mode": "legacy-directional-fallback",
            "selectedHypothesisStatus": legacy_directional_status(text(stance).lower(), instrument_return_pct),
            "criterionAssessments": [],
            "requiredCriterionCount": 0,
            "passedCriterionCount": 0,
            "failedCriterionCount": 0,
            "unknownCriterionCount": 0,
            "missingRequiredMetricIds": [],
        }

    assessments = []
    for criterion in criteria:
        value, basis = metric_value(criterion.metric, facts, instrument_return_pct)
        source_usable, source_reason = source_policy_status(criterion, facts)
        if value is None or not source_usable:
            state = "unknown"
            passed = None
            reason = source_reason or basis or "required-metric-missing"
        else:
            passed = compare(value, criterion.operator, criterion.threshold)
            state = "passed" if passed else "failed"
            reason = "criterion-comparison"
        assessments.append({
            **criterion.to_dict(),
            "state": state,
            "observedValue": value,
            "observationBasis": basis,
            "reason": reason,
        })

    required_support = [
        item for item in assessments
        if item.get("required") and item.get("role") in SUPPORTING_ROLES
    ]
    required_invalidation = [
        item for item in assessments
        if item.get("required") and item.get("role") == "invalidation"
    ]
    supporting = [item for item in assessments if item.get("role") in SUPPORTING_ROLES]
    invalidation_passed = any(item.get("state") == "passed" for item in required_invalidation)
    missing_required = [
        text(item.get("criterionId"))
        for item in assessments
        if item.get("required") and item.get("state") == "unknown"
    ]
    if invalidation_passed:
        status = "directionally-contradicted"
    elif missing_required:
        status = "inconclusive"
    elif required_support and all(item.get("state") == "passed" for item in required_support):
        status = "directionally-corroborated"
    elif required_support and any(item.get("state") == "failed" for item in required_support):
        failed_required = [item for item in required_support if item.get("state") == "failed"]
        status = (
            "directionally-contradicted"
            if any(item.get("failureOutcome") != "inconclusive" for item in failed_required)
            else "inconclusive"
        )
    else:
        passed = sum(1 for item in supporting if item.get("state") == "passed")
        failed = sum(1 for item in supporting if item.get("state") == "failed")
        status = (
            "directionally-corroborated" if passed > failed
            else "directionally-contradicted" if failed > passed
            else "inconclusive"
        )
    return {
        "version": HYPOTHESIS_OUTCOME_EVALUATION_VERSION,
        "mode": "contract-criteria",
        "selectedHypothesisStatus": status,
        "criterionAssessments": assessments,
        "requiredCriterionCount": sum(1 for item in assessments if item.get("required")),
        "passedCriterionCount": sum(1 for item in assessments if item.get("state") == "passed"),
        "failedCriterionCount": sum(1 for item in assessments if item.get("state") == "failed"),
        "unknownCriterionCount": sum(1 for item in assessments if item.get("state") == "unknown"),
        "missingRequiredMetricIds": missing_required,
    }
