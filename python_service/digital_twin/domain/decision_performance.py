from typing import Dict, Iterable, List


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


def performance_observations(episodes: Iterable[object]) -> List[Dict[str, object]]:
    observations: List[Dict[str, object]] = []
    for value in episodes or []:
        episode = episode_payload(value)
        hypothesis = selected_hypothesis(episode)
        for outcome in episode.get("outcomes") or []:
            if not isinstance(outcome, dict):
                continue
            payload = outcome.get("payload") if isinstance(outcome.get("payload"), dict) else {}
            status = str(outcome.get("selectedHypothesisStatus") or "inconclusive")
            raw_return = number(outcome.get("priceChangeFromDecisionPct"))
            calibration_eligibility = str(payload.get("calibrationEligibility") or "legacy-unverified")
            observations.append({
                "episodeId": str(episode.get("episodeId") or ""),
                "accountId": str(episode.get("accountId") or ""),
                "symbol": str(episode.get("symbol") or "").upper(),
                "action": str(episode.get("action") or "HOLD").upper(),
                "hypothesisId": str(hypothesis.get("hypothesisId") or episode.get("selectedHypothesisId") or ""),
                "hypothesisTemplateId": str(hypothesis.get("templateId") or ""),
                "hypothesisTemplateLabel": str(hypothesis.get("templateLabel") or hypothesis.get("claim") or ""),
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


def metric_slice(
    observations: Iterable[Dict[str, object]],
    key: str = "",
    label: str = "",
    minimum_sample_count: int = 5,
) -> Dict[str, object]:
    rows = list(observations or [])
    eligible_rows = [item for item in rows if item.get("calibrationEligible")]
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
    return {
        "key": str(key or "all"),
        "label": str(label or key or "전체"),
        "outcomeCount": len(rows),
        "independentEpisodeCount": len({str(item.get("episodeId") or "") for item in rows if str(item.get("episodeId") or "")}),
        "calibrationEligibleOutcomeCount": len(eligible_rows),
        "calibrationEligibleEpisodeCount": len({str(item.get("episodeId") or "") for item in eligible_rows if str(item.get("episodeId") or "")}),
        "excludedOutcomeCount": len(rows) - len(eligible_rows),
        "delayedOutcomeCount": len([item for item in rows if item.get("observationTiming") == "delayed"]),
        "legacyUnverifiedOutcomeCount": len([item for item in rows if item.get("calibrationEligibility") == "legacy-unverified"]),
        "decisiveOutcomeCount": len(decisive),
        "corroboratedCount": len(corroborated),
        "contradictedCount": len(contradicted),
        "inconclusiveCount": len([item for item in rows if not item.get("decisive")]),
        "averageRawReturnPct": round(sum(number(item.get("rawReturnPct")) for item in eligible_rows) / len(eligible_rows), 4) if eligible_rows else 0.0,
        "observedAverageRawReturnPct": round(sum(number(item.get("rawReturnPct")) for item in rows) / len(rows), 4) if rows else 0.0,
        "averageActionAdjustedReturnPct": round(avg_adjusted, 4),
        "averageDownsidePct": round(sum(negative_adjusted) / len(negative_adjusted), 4) if negative_adjusted else 0.0,
        "worstActionAdjustedReturnPct": round(min(adjusted), 4) if adjusted else 0.0,
        "minimumSampleCount": int(minimum_sample_count or 0),
        "sampleStatus": "usable" if enough_samples else ("awaiting-eligible-outcomes" if rows and not eligible_rows else "insufficient-history"),
        "corroborationState": corroboration,
        "actionReturnState": return_state,
        "promotionEligible": promotion_eligible,
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


def evaluate_decision_performance(
    episodes: Iterable[object],
    minimum_sample_count: int = 5,
) -> Dict[str, object]:
    episode_rows = [episode_payload(item) for item in episodes or []]
    observations = performance_observations(episode_rows)
    episodes_with_outcomes = {str(item.get("episodeId") or "") for item in observations if str(item.get("episodeId") or "")}
    calibration_episodes = {str(item.get("episodeId") or "") for item in observations if item.get("calibrationEligible") and str(item.get("episodeId") or "")}
    calibration_observations = [item for item in observations if item.get("calibrationEligible")]
    coverage = (len(episodes_with_outcomes) / len(episode_rows) * 100.0) if episode_rows else 0.0
    return {
        "status": "ok" if observations else "insufficient-data",
        "episodeCount": len(episode_rows),
        "episodeWithOutcomeCount": len(episodes_with_outcomes),
        "outcomeCoveragePct": round(coverage, 2),
        "outcomeCount": len(observations),
        "calibrationEligibleEpisodeCount": len(calibration_episodes),
        "calibrationEligibleOutcomeCount": len(calibration_observations),
        "calibrationCoveragePct": round((len(calibration_episodes) / len(episode_rows) * 100.0), 2) if episode_rows else 0.0,
        "minimumSampleCount": int(minimum_sample_count or 0),
        "summary": metric_slice(observations, "all", "전체 판단", minimum_sample_count),
        "byHorizon": grouped_metrics(observations, "horizonMinutes", minimum_sample_count),
        "byAction": grouped_metrics(observations, "action", minimum_sample_count),
        "byRule": grouped_metrics(observations, "ruleIds", minimum_sample_count, multi_value=True),
        "byHypothesis": grouped_metrics(observations, "hypothesisTemplateId", minimum_sample_count),
        "governance": {
            "automaticDeployment": False,
            "promotionRequires": ["minimum-history", "more-corroborated-outcomes", "non-negative-action-adjusted-return", "human-review"],
        },
    }
