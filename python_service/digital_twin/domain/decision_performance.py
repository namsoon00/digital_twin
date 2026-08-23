import math

from typing import Dict, Iterable, List

from .hypothesis_outcome_contract import outcome_contract_completeness


POSITIVE_ACTIONS = {"BUY", "ADD", "HOLD", "KEEP", "WATCH"}
NEGATIVE_ACTIONS = {"SELL", "TRIM", "REDUCE", "EXIT", "CUT"}
DECISIVE_STATUSES = {"directionally-corroborated", "directionally-contradicted"}
CORROBORATION_STATES = (
    "insufficient-history",
    "mixed",
    "more-contradicted",
    "more-corroborated",
)


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def episode_payload(value: object) -> Dict[str, object]:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return dict(value or {}) if isinstance(value, dict) else {}


def selected_hypothesis(episode: Dict[str, object]) -> Dict[str, object]:
    hypothesis_set = episode.get("hypothesisSet") if isinstance(episode.get("hypothesisSet"), dict) else {}
    selected_id = str(episode.get("selectedHypothesisId") or "")
    return next((
        item for item in hypothesis_set.get("hypotheses") or []
        if isinstance(item, dict) and str(item.get("hypothesisId") or "") == selected_id
    ), {})


def action_adjusted_return(action: str, raw_return: float):
    normalized = str(action or "").upper().strip()
    if normalized in POSITIVE_ACTIONS:
        return raw_return
    if normalized in NEGATIVE_ACTIONS:
        return -raw_return
    return None


def latest_independent_observations(observations: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    latest: Dict[str, Dict[str, object]] = {}
    for item in observations or []:
        independence_key = str(item.get("independentEpisodeKey") or item.get("episodeId") or "").strip()
        if not independence_key:
            continue
        previous = latest.get(independence_key)
        if previous is None or str(item.get("observedAt") or "") >= str(previous.get("observedAt") or ""):
            latest[independence_key] = item
    return list(latest.values())


def performance_observations(episodes: Iterable[object]) -> List[Dict[str, object]]:
    observations: List[Dict[str, object]] = []
    for value in episodes or []:
        episode = episode_payload(value)
        hypothesis = selected_hypothesis(episode)
        facts = episode.get("factsAtDecision") if isinstance(episode.get("factsAtDecision"), dict) else {}
        raw_contract = facts.get("hypothesisOutcomeContract") if isinstance(facts.get("hypothesisOutcomeContract"), dict) else {}
        contract_complete = bool(outcome_contract_completeness(raw_contract).get("complete"))
        for outcome in episode.get("outcomes") or []:
            if not isinstance(outcome, dict):
                continue
            payload = outcome.get("payload") if isinstance(outcome.get("payload"), dict) else {}
            status = str(outcome.get("selectedHypothesisStatus") or "inconclusive")
            raw_return = number(outcome.get("priceChangeFromDecisionPct"))
            stored_eligibility = str(payload.get("calibrationEligibility") or "legacy-unverified")
            calibration_eligibility = (
                stored_eligibility
                if contract_complete
                else "excluded-incomplete-prediction-contract"
            )
            observations.append({
                "episodeId": str(episode.get("episodeId") or ""),
                "independentEpisodeKey": str(payload.get("accountIndependenceKey") or episode.get("episodeId") or ""),
                "accountId": str(episode.get("accountId") or ""),
                "symbol": str(episode.get("symbol") or "").upper(),
                "action": str(episode.get("action") or "HOLD").upper(),
                "hypothesisId": str(hypothesis.get("hypothesisId") or episode.get("selectedHypothesisId") or ""),
                "hypothesisTemplateId": str(hypothesis.get("templateId") or ""),
                "hypothesisTemplateLabel": str(hypothesis.get("templateLabel") or hypothesis.get("claim") or ""),
                "hypothesisFamilyId": str(hypothesis.get("familyId") or ""),
                "predictionTarget": str(hypothesis.get("predictionTarget") or ""),
                "expectedDirection": str(hypothesis.get("expectedDirection") or ""),
                "expectedOutcome": str(hypothesis.get("expectedOutcome") or ""),
                "outcomeMetric": str(hypothesis.get("outcomeMetric") or ""),
                "falsificationContract": str(hypothesis.get("falsificationContract") or ""),
                "ruleIds": list(hypothesis.get("supportingRuleIds") or []),
                "horizonMinutes": int(number(payload.get("horizonMinutes"))),
                "status": status,
                "corroborated": status == "directionally-corroborated",
                "decisive": status in DECISIVE_STATUSES,
                "calibrationEligibility": calibration_eligibility,
                "calibrationEligible": calibration_eligibility == "eligible",
                "observationTiming": str(payload.get("observationTiming") or "legacy-unknown"),
                "observationDelayMinutes": number(payload.get("observationDelayMinutes")),
                "rawReturnPct": raw_return,
                "actionAdjustedReturnPct": action_adjusted_return(str(episode.get("action") or ""), raw_return),
                "observedAt": str(outcome.get("observedAt") or ""),
            })
    return observations


def corroboration_state(
    corroborated_count: int,
    contradicted_count: int,
    enough_samples: bool,
) -> str:
    if not enough_samples:
        return "insufficient-history"
    if corroborated_count > contradicted_count:
        return "more-corroborated"
    if contradicted_count > corroborated_count:
        return "more-contradicted"
    return "mixed"


def action_return_state(values: Iterable[object]) -> str:
    usable = [number(value) for value in values if value is not None]
    if not usable:
        return "unavailable"
    average = sum(usable) / len(usable)
    if average > 0:
        return "non-negative"
    if average < 0:
        return "negative"
    return "flat"


def binomial_confidence_interval(success_count: int, sample_count: int) -> Dict[str, float]:
    """Return a 95% Wilson interval without claiming a calibrated probability."""

    total = max(0, int(sample_count or 0))
    successes = max(0, min(total, int(success_count or 0)))
    if not total:
        return {"rate": 0.0, "lower": 0.0, "upper": 0.0}
    z = 1.959963984540054
    rate = successes / total
    denominator = 1 + (z * z / total)
    centre = (rate + (z * z / (2 * total))) / denominator
    margin = (
        z
        * math.sqrt((rate * (1 - rate) / total) + (z * z / (4 * total * total)))
        / denominator
    )
    return {
        "rate": round(rate, 6),
        "lower": round(max(0.0, centre - margin), 6),
        "upper": round(min(1.0, centre + margin), 6),
    }


def performance_qualification_state(
    enough_samples: bool,
    corroborated_count: int,
    contradicted_count: int,
    average_action_adjusted_return: float,
    promotion_eligible: bool,
    directional_hit_rate_upper95: float,
) -> str:
    """Classify review urgency while retaining human RuleBox governance."""

    if not enough_samples:
        return "insufficient-history"
    if (
        int(contradicted_count or 0) > int(corroborated_count or 0)
        and float(average_action_adjusted_return or 0) < 0
        and float(directional_hit_rate_upper95 or 0) < 0.5
    ):
        return "quarantine-recommended"
    if promotion_eligible:
        return "qualified-for-review"
    return "review-required"


def metric_slice(
    observations: Iterable[Dict[str, object]],
    key: str = "",
    label: str = "",
    minimum_sample_count: int = 5,
) -> Dict[str, object]:
    rows = list(observations or [])
    independent_rows = latest_independent_observations(rows)
    eligible_rows = latest_independent_observations(item for item in rows if item.get("calibrationEligible"))
    decisive = [item for item in eligible_rows if item.get("decisive")]
    corroborated = [item for item in decisive if item.get("corroborated")]
    contradicted = [item for item in decisive if not item.get("corroborated")]
    adjusted = [number(item.get("actionAdjustedReturnPct")) for item in eligible_rows if item.get("actionAdjustedReturnPct") is not None]
    negative_adjusted = [value for value in adjusted if value < 0]
    avg_adjusted = sum(adjusted) / len(adjusted) if adjusted else 0.0
    enough_samples = len(decisive) >= max(1, int(minimum_sample_count or 1))
    corroboration = corroboration_state(len(corroborated), len(contradicted), enough_samples)
    return_state = action_return_state(adjusted)
    promotion_eligible = corroboration == "more-corroborated" and return_state == "non-negative"
    confidence = binomial_confidence_interval(len(corroborated), len(decisive))
    qualification_state = performance_qualification_state(
        enough_samples,
        len(corroborated),
        len(contradicted),
        avg_adjusted,
        promotion_eligible,
        confidence["upper"],
    )
    return {
        "key": str(key or "all"),
        "label": str(label or key or "전체"),
        "outcomeCount": len(rows),
        "independentEpisodeCount": len(independent_rows),
        "calibrationEligibleOutcomeCount": len(eligible_rows),
        "calibrationEligibleEpisodeCount": len(eligible_rows),
        "excludedOutcomeCount": len(rows) - len(eligible_rows),
        "delayedOutcomeCount": len([item for item in rows if item.get("observationTiming") == "delayed"]),
        "legacyUnverifiedOutcomeCount": len([item for item in rows if item.get("calibrationEligibility") == "legacy-unverified"]),
        "decisiveOutcomeCount": len(decisive),
        "corroboratedCount": len(corroborated),
        "contradictedCount": len(contradicted),
        "inconclusiveCount": len([item for item in independent_rows if not item.get("decisive")]),
        "averageRawReturnPct": round(sum(number(item.get("rawReturnPct")) for item in eligible_rows) / len(eligible_rows), 4) if eligible_rows else 0.0,
        "observedAverageRawReturnPct": round(sum(number(item.get("rawReturnPct")) for item in rows) / len(rows), 4) if rows else 0.0,
        "averageActionAdjustedReturnPct": round(avg_adjusted, 4),
        "averageDownsidePct": round(sum(negative_adjusted) / len(negative_adjusted), 4) if negative_adjusted else 0.0,
        "worstActionAdjustedReturnPct": round(min(adjusted), 4) if adjusted else 0.0,
        "minimumSampleCount": int(minimum_sample_count or 0),
        "sampleStatus": "usable" if enough_samples else ("awaiting-eligible-outcomes" if rows and not eligible_rows else "insufficient-history"),
        "corroborationState": corroboration,
        "directionalHitRate": confidence["rate"],
        "directionalHitRateConfidence95": {
            "lower": confidence["lower"],
            "upper": confidence["upper"],
        },
        "actionReturnState": return_state,
        "promotionEligible": promotion_eligible,
        "qualificationState": qualification_state,
        "quarantineRecommended": qualification_state == "quarantine-recommended",
        "automaticRuleChange": False,
        "governance": "human-review-required" if promotion_eligible else "not-eligible",
    }


def grouped_metrics(
    observations: List[Dict[str, object]],
    value_key: str,
    minimum_sample_count: int,
    multi_value: bool = False,
) -> List[Dict[str, object]]:
    grouped: Dict[str, List[Dict[str, object]]] = {}
    labels: Dict[str, str] = {}
    for item in observations:
        values = item.get(value_key)
        values = values if multi_value and isinstance(values, list) else [values]
        for value in values:
            key = str(value or "").strip()
            if not key:
                continue
            grouped.setdefault(key, []).append(item)
            labels[key] = str(item.get("hypothesisTemplateLabel") or key) if value_key == "hypothesisTemplateId" else key
    return sorted(
        [metric_slice(rows, key, labels.get(key, key), minimum_sample_count) for key, rows in grouped.items()],
        key=lambda item: (
            {"more-corroborated": 3, "mixed": 2, "more-contradicted": 1, "insufficient-history": 0}.get(
                str(item.get("corroborationState") or ""),
                0,
            ),
            int(item.get("decisiveOutcomeCount") or 0),
        ),
        reverse=True,
    )


def grouped_family_horizon_metrics(
    observations: List[Dict[str, object]],
    minimum_sample_count: int,
) -> List[Dict[str, object]]:
    grouped: Dict[str, List[Dict[str, object]]] = {}
    labels: Dict[str, str] = {}
    for item in observations:
        family_id = str(item.get("hypothesisFamilyId") or item.get("hypothesisTemplateId") or "").strip()
        if not family_id:
            continue
        horizon = int(number(item.get("horizonMinutes")))
        metric = str(item.get("outcomeMetric") or "instrumentReturnPct").strip()
        key = "|".join([family_id, str(horizon), metric])
        grouped.setdefault(key, []).append(item)
        labels[key] = " · ".join(filter(None, [
            str(item.get("hypothesisTemplateLabel") or family_id),
            str(horizon) + "분",
            metric,
        ]))
    return sorted(
        [metric_slice(rows, key, labels[key], minimum_sample_count) for key, rows in grouped.items()],
        key=lambda item: (int(item.get("decisiveOutcomeCount") or 0), str(item.get("key") or "")),
        reverse=True,
    )


def contradiction_learning_candidates(
    episodes: Iterable[object],
    minimum_sample_count: int = 3,
) -> List[Dict[str, object]]:
    """Return review candidates only for one repeated causal contract."""

    minimum = max(2, int(minimum_sample_count or 3))
    observations = performance_observations(episodes)
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for item in observations:
        if not item.get("calibrationEligible") or item.get("status") != "directionally-contradicted":
            continue
        family_id = str(item.get("hypothesisFamilyId") or item.get("hypothesisTemplateId") or "").strip()
        if not family_id:
            continue
        horizon = int(number(item.get("horizonMinutes")))
        metric = str(item.get("outcomeMetric") or "instrumentReturnPct").strip()
        key = "|".join([family_id, str(horizon), metric])
        grouped.setdefault(key, []).append(item)
    candidates = []
    for key, rows in grouped.items():
        independent = latest_independent_observations(rows)
        if len(independent) < minimum:
            continue
        first = independent[0]
        candidates.append({
            "groupKey": key,
            "familyId": str(first.get("hypothesisFamilyId") or ""),
            "templateId": str(first.get("hypothesisTemplateId") or ""),
            "templateLabel": str(first.get("hypothesisTemplateLabel") or ""),
            "predictionTarget": str(first.get("predictionTarget") or ""),
            "expectedDirection": str(first.get("expectedDirection") or ""),
            "expectedOutcome": str(first.get("expectedOutcome") or ""),
            "outcomeMetric": str(first.get("outcomeMetric") or "instrumentReturnPct"),
            "falsificationContract": str(first.get("falsificationContract") or ""),
            "horizonMinutes": int(number(first.get("horizonMinutes"))),
            "contradictedCount": len(independent),
            "sourceEpisodeIds": sorted({str(item.get("episodeId") or "") for item in independent if str(item.get("episodeId") or "")}),
            "affectedRuleIds": sorted({str(rule_id) for item in independent for rule_id in item.get("ruleIds") or [] if str(rule_id or "")}),
            "latestObservedAt": max((str(item.get("observedAt") or "") for item in independent), default=""),
            "automaticDeployment": False,
            "decisionEligibility": "learning-review-only",
        })
    return sorted(candidates, key=lambda item: (int(item["contradictedCount"]), item["latestObservedAt"]), reverse=True)


def evaluate_decision_performance(
    episodes: Iterable[object],
    minimum_sample_count: int = 5,
) -> Dict[str, object]:
    episode_rows = [episode_payload(item) for item in episodes or []]
    observations = performance_observations(episode_rows)
    episodes_with_outcomes = {str(item.get("episodeId") or "") for item in observations if str(item.get("episodeId") or "")}
    independent_observations = latest_independent_observations(observations)
    calibration_observations = latest_independent_observations(item for item in observations if item.get("calibrationEligible"))
    coverage = (len(episodes_with_outcomes) / len(episode_rows) * 100.0) if episode_rows else 0.0
    by_rule = grouped_metrics(observations, "ruleIds", minimum_sample_count, multi_value=True)
    return {
        "status": "ok" if observations else "insufficient-data",
        "episodeCount": len(episode_rows),
        "episodeWithOutcomeCount": len(episodes_with_outcomes),
        "independentEpisodeCount": len(independent_observations),
        "outcomeCoveragePct": round(coverage, 2),
        "outcomeCount": len(observations),
        "calibrationEligibleEpisodeCount": len(calibration_observations),
        "calibrationEligibleOutcomeCount": len(calibration_observations),
        "calibrationCoveragePct": round((len(calibration_observations) / len(independent_observations) * 100.0), 2) if independent_observations else 0.0,
        "minimumSampleCount": int(minimum_sample_count or 0),
        "summary": metric_slice(observations, "all", "전체 판단", minimum_sample_count),
        "byHorizon": grouped_metrics(observations, "horizonMinutes", minimum_sample_count),
        "byAction": grouped_metrics(observations, "action", minimum_sample_count),
        "byRule": by_rule,
        "byHypothesis": grouped_metrics(observations, "hypothesisTemplateId", minimum_sample_count),
        "byHypothesisFamily": grouped_metrics(observations, "hypothesisFamilyId", minimum_sample_count),
        "byHypothesisFamilyAndHorizon": grouped_family_horizon_metrics(observations, minimum_sample_count),
        "byPredictionTarget": grouped_metrics(observations, "predictionTarget", minimum_sample_count),
        "byOutcomeMetric": grouped_metrics(observations, "outcomeMetric", minimum_sample_count),
        "governance": {
            "automaticDeployment": False,
            "promotionRequires": ["minimum-history", "more-corroborated-outcomes", "non-negative-action-adjusted-return", "human-review"],
            "quarantineRecommendedRuleIds": [
                str(item.get("key") or "")
                for item in by_rule
                if item.get("quarantineRecommended") and str(item.get("key") or "")
            ],
        },
    }
