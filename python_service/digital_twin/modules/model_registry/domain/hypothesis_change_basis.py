"""Explain why proven paths changed; never infer a new market hypothesis."""

from copy import deepcopy
from datetime import datetime
from digital_twin.modules.model_registry.domain.statistical_signals.contracts import signal_strength_floor


VERSION = "hypothesis-change-basis-v1"
MODEL_FIELDS = (
    "hypothesisContractId", "signalType", "releaseId", "score", "strengthBand",
    "contractMatched", "freshnessCompatible", "eligibilityStatus", "observedAt",
    "sourceFeatureSnapshotId", "sourceObservation", "modelInputWindows", "status",
    "requiredStrengthBand", "minimumScore", "failedConditionIds", "unknownConditionIds",
)


def mapping(value):
    return value if isinstance(value, dict) else {}


def clock(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.timestamp() if stamp.tzinfo else None
    except (TypeError, ValueError):
        return None


def lifecycle_observation_basis(context):
    """Copy current subject input proof, including models below the rule band."""
    facts = mapping(context.get("facts"))
    models = deepcopy(mapping(context.get("modelEvidenceObservations")))
    traces = mapping(context.get("graphStoreInference")).get("traces") or []
    for trace in traces:
        for condition in trace.get("matchedConditions") or []:
            props = mapping(condition.get("matchedTargetProperties"))
            rule = props.get("hypothesisContractId")
            if not rule:
                continue
            row = models.setdefault(rule, {k: deepcopy(props[k]) for k in MODEL_FIELDS if k in props})
            row["label"] = trace.get("label") or trace.get("ruleLabel") or rule
            filters = mapping(mapping(condition.get("ruleConditionShape")).get("targetPropertyFilters"))
            if filters.get("strengthBand"):
                row["requiredStrengthBand"] = filters["strengthBand"]
                row["minimumScore"] = signal_strength_floor(filters["strengthBand"])
    return {"version": VERSION, "models": models,
            "quote": {k: facts[k] for k in ("sourceAsOf", "currentPrice", "quoteSource") if k in facts}}


def legacy_observation_basis(payload):
    models = {}
    for evidence in mapping(payload.get("evidenceDetails")).values():
        if evidence.get("relationType") == "HAS_MODEL_SIGNAL" and evidence.get("ruleId"):
            models[evidence["ruleId"]] = {**mapping(evidence.get("observedValue")), "label": evidence.get("label")}
    return {"models": models, "quote": {
        "sourceAsOf": mapping(mapping(payload.get("observationProfiles")).get("quote")).get("sourceAsOf")}}


def compare_change_basis(before, current, rule_ids, *, removed=False, require_model_proof=False):
    """A negative match requires current input proof, not absence from a graph."""
    before, current = mapping(before), mapping(current)
    previous_models, models = mapping(before.get("models")), mapping(current.get("models"))
    comparisons, missing, revaluations = [], [], []
    for rule_id in sorted(set(rule_ids)):
        old, new = mapping(previous_models.get(rule_id)), mapping(models.get(rule_id))
        if not old and not new and not require_model_proof:
            continue  # Non-model rules keep their native lifecycle contract.
        unavailable = (not new or new.get("status") == "untested"
                       or new.get("freshnessCompatible") is False
                       or new.get("eligibilityStatus") in {"ineligible", "reference-only"}
                       or bool(new.get("unknownConditionIds")))
        if unavailable:
            missing.append(rule_id)
        new = {**new, **{k: old[k] for k in ("requiredStrengthBand", "minimumScore") if k in old and k not in new}}
        comparisons.append({"ruleId": rule_id, "label": old.get("label") or new.get("label") or rule_id,
                            "previous": deepcopy(old), "current": deepcopy(new),
                            "comparison": "unavailable" if unavailable else "verified"})
        old_source, new_source = mapping(old.get("sourceObservation")), mapping(new.get("sourceObservation"))
        same_source = (old_source.get("verified") is True and new_source.get("verified") is True
                       and clock(old_source.get("observedAt")) is not None
                       and clock(old_source.get("observedAt")) == clock(new_source.get("observedAt"))
                       and old_source.get("currentPrice") == new_source.get("currentPrice"))
        price_signal = str(new.get("signalType") or old.get("signalType") or "").startswith("price-")
        changed = old.get("score") != new.get("score") or old.get("strengthBand") != new.get("strengthBand")
        old_quote, new_quote = mapping(before.get("quote")), mapping(current.get("quote"))
        same_quote_clock = (clock(old_quote.get("sourceAsOf")) is not None
                            and clock(old_quote.get("sourceAsOf")) == clock(new_quote.get("sourceAsOf")))
        # Migrated snapshots have no model source clock. Keep their comparison
        # explicit and tentative rather than announcing a new market movement.
        if not same_source and same_quote_clock and price_signal and changed:
            comparisons[-1]["comparison"] = "source-comparison-partial"
            revaluations.append(rule_id)
        if not unavailable and old and price_signal and same_source and changed:
            revaluations.append(rule_id)
    if missing and removed:
        category, reason = "data-unavailable", "이전 근거를 현재 자료로 다시 확인하지 못했습니다. 조건 해제나 가설 강화·약화로 판단하지 않습니다."
    elif comparisons and revaluations and len(revaluations) == len(comparisons):
        category = "reassessment"
        reason = ("시세의 원천 시각은 같지만 모델 입력의 전후 비교가 불완전해 변화 원인을 추가 확인해야 합니다."
                  if any(row["comparison"] == "source-comparison-partial" for row in comparisons)
                  else "새 시세 변동 없이 분석에 사용한 기간·입력 구성이 바뀌어 조건을 재평가했습니다.")
    else:
        category, reason = "market-change", "규칙의 근거 변화를 확인했습니다."
    return {"version": VERSION, "category": category, "reason": reason,
            "ruleComparisons": comparisons, "unavailableRuleIds": missing}
