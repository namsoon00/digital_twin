"""Audit-only hypothesis review facts for AI context, ABox, and the web.

The functions here deliberately never rank an action or alter a TypeDB rule
result.  They only compare an already selected, TypeDB-derived hypothesis with
eligible observations recorded after a decision episode.  Market hypotheses
and account overlays are kept separate so account performance cannot be
mistaken for a shared market fact.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Dict, Iterable, List, Mapping, Sequence

from .hypothesis_lifecycle import HYPOTHESIS_LIFECYCLE_STATE_LABELS, parse_timestamp
from .ontology_rulebox_contracts import HypothesisLifecyclePolicy


HYPOTHESIS_OUTCOME_REVIEW_VERSION = "hypothesis-outcome-review-v1"
OUTCOME_STATES = (
    "supported",
    "contradicted",
    "inconclusive",
    "insufficient-sample",
)
OUTCOME_STATE_LABELS = {
    "supported": "지지됨",
    "contradicted": "반증됨",
    "inconclusive": "판단 불가",
    "insufficient-sample": "표본 부족",
}


def text(value: object) -> str:
    return str(value or "").strip()


def upper(value: object) -> str:
    return text(value).upper()


def integer(value: object, fallback: int = 0) -> int:
    try:
        return int(float(str(value or fallback)))
    except (TypeError, ValueError):
        return fallback


def as_dict(value: object) -> Dict[str, object]:
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        return dict(payload or {}) if isinstance(payload, dict) else {}
    return dict(value or {}) if isinstance(value, Mapping) else {}


def values(value: object, limit: int = 80) -> List[str]:
    source = value if isinstance(value, (list, tuple, set)) else [value]
    result: List[str] = []
    for item in source:
        normalized = text(item)
        if normalized and normalized not in result:
            result.append(normalized)
        if len(result) >= limit:
            break
    return result


def selected_hypothesis(episode: Mapping[str, object]) -> Dict[str, object]:
    hypothesis_set = episode.get("hypothesisSet") if isinstance(episode.get("hypothesisSet"), Mapping) else {}
    selected_id = text(episode.get("selectedHypothesisId"))
    for item in hypothesis_set.get("hypotheses") or []:
        if isinstance(item, Mapping) and text(item.get("hypothesisId")) == selected_id:
            return dict(item)
    return {}


def lifecycle_reference_for_hypothesis(
    hypothesis: Mapping[str, object],
    episode: Mapping[str, object],
) -> List[Dict[str, object]]:
    """Return exact lifecycle identities; never fall back to a broad family."""

    symbol = upper(episode.get("symbol"))
    account_id = text(episode.get("accountId"))
    family_id = text(hypothesis.get("familyId"))
    source_rule_ids = values(hypothesis.get("supportingRuleIds"))
    result: List[Dict[str, object]] = []
    market_id = text(hypothesis.get("marketHypothesisId"))
    if market_id:
        result.append({
            "scope": "market",
            "lifecycleId": market_id,
            "symbol": symbol,
            "familyId": family_id,
            "sourceRuleIds": source_rule_ids,
        })
    overlay_id = text(hypothesis.get("accountHypothesisOverlayId"))
    if overlay_id:
        result.append({
            "scope": "account",
            "lifecycleId": overlay_id,
            "accountId": account_id,
            "symbol": symbol,
            "familyId": family_id,
            "sourceRuleIds": source_rule_ids,
        })
    if not result:
        hypothesis_id = text(hypothesis.get("hypothesisId"))
        if hypothesis_id:
            result.append({
                "scope": "account",
                "lifecycleId": "hypothesis:" + hypothesis_id,
                "accountId": account_id,
                "symbol": symbol,
                "familyId": family_id,
                "sourceRuleIds": source_rule_ids,
            })
    return result


def lifecycle_reference_key(reference: Mapping[str, object]) -> str:
    scope = text(reference.get("scope")) or "account"
    account_id = text(reference.get("accountId")) if scope == "account" else ""
    return "|".join([scope, account_id, upper(reference.get("symbol")), text(reference.get("lifecycleId"))])


def lifecycle_references_from_episodes(episodes: Iterable[object]) -> List[Dict[str, object]]:
    references: List[Dict[str, object]] = []
    seen = set()
    for value in episodes or []:
        episode = as_dict(value)
        hypothesis = selected_hypothesis(episode)
        if not hypothesis:
            continue
        for reference in lifecycle_reference_for_hypothesis(hypothesis, episode):
            key = lifecycle_reference_key(reference)
            if key and key not in seen:
                seen.add(key)
                references.append(reference)
    return references


def episode_matches_lifecycle(episode: Mapping[str, object], lifecycle: Mapping[str, object]) -> bool:
    if upper(episode.get("symbol")) != upper(lifecycle.get("symbol")):
        return False
    scope = text(lifecycle.get("scope")) or "account"
    if scope == "account" and text(lifecycle.get("accountId")) and text(episode.get("accountId")) != text(lifecycle.get("accountId")):
        return False
    hypothesis = selected_hypothesis(episode)
    if not hypothesis:
        return False
    lifecycle_id = text(lifecycle.get("lifecycleId"))
    if scope == "market":
        return lifecycle_id and text(hypothesis.get("marketHypothesisId")) == lifecycle_id
    overlay_id = text(hypothesis.get("accountHypothesisOverlayId"))
    if overlay_id:
        return lifecycle_id == overlay_id
    hypothesis_id = text(hypothesis.get("hypothesisId"))
    return lifecycle_id == "hypothesis:" + hypothesis_id


def eligible_outcome_rows(
    lifecycle: Mapping[str, object],
    episodes: Iterable[object],
) -> Dict[str, object]:
    rows: List[Dict[str, object]] = []
    excluded_count = 0
    matched_episode_count = 0
    seen = set()
    for value in episodes or []:
        episode = as_dict(value)
        if not episode_matches_lifecycle(episode, lifecycle):
            continue
        matched_episode_count += 1
        episode_id = text(episode.get("episodeId"))
        for raw_outcome in episode.get("outcomes") or []:
            outcome = as_dict(raw_outcome)
            payload = outcome.get("payload") if isinstance(outcome.get("payload"), Mapping) else {}
            eligibility = text(payload.get("calibrationEligibility"))
            if eligibility != "eligible":
                excluded_count += 1
                continue
            horizon = max(0, integer(payload.get("horizonMinutes")))
            key = "|".join([episode_id, str(horizon), text(outcome.get("outcomeId"))])
            if not episode_id or key in seen:
                continue
            seen.add(key)
            rows.append({
                "episodeId": episode_id,
                "accountId": text(episode.get("accountId")),
                "symbol": upper(episode.get("symbol")),
                "horizonMinutes": horizon,
                "status": text(outcome.get("selectedHypothesisStatus")) or "inconclusive",
                "observedAt": text(outcome.get("observedAt")),
                "outcomeId": text(outcome.get("outcomeId")),
            })
    return {
        "rows": rows,
        "matchedEpisodeCount": matched_episode_count,
        "excludedOutcomeCount": excluded_count,
    }


def outcome_state(rows: Sequence[Mapping[str, object]], minimum_samples: int) -> Dict[str, object]:
    normalized = [dict(item) for item in rows or [] if isinstance(item, Mapping)]
    supported = sum(1 for item in normalized if text(item.get("status")) == "directionally-corroborated")
    contradicted = sum(1 for item in normalized if text(item.get("status")) == "directionally-contradicted")
    inconclusive = max(0, len(normalized) - supported - contradicted)
    if len(normalized) < minimum_samples:
        state = "insufficient-sample"
    elif supported > contradicted:
        state = "supported"
    elif contradicted > supported:
        state = "contradicted"
    else:
        state = "inconclusive"
    return {
        "outcomeState": state,
        "outcomeStateLabel": OUTCOME_STATE_LABELS[state],
        "sampleCount": len(normalized),
        "supportedCount": supported,
        "contradictedCount": contradicted,
        "inconclusiveCount": inconclusive,
        "latestObservedAt": max((text(item.get("observedAt")) for item in normalized), default=""),
    }


def select_latest_outcome_per_episode(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    latest: Dict[str, Dict[str, object]] = {}
    for raw in rows or []:
        item = dict(raw or {})
        episode_id = text(item.get("episodeId"))
        if not episode_id:
            continue
        current = latest.get(episode_id)
        candidate_key = (integer(item.get("horizonMinutes")), text(item.get("observedAt")), text(item.get("outcomeId")))
        current_key = (
            integer(current.get("horizonMinutes")),
            text(current.get("observedAt")),
            text(current.get("outcomeId")),
        ) if current else (-1, "", "")
        if candidate_key >= current_key:
            latest[episode_id] = item
    return [latest[key] for key in sorted(latest)]


def outcome_assessment_for_lifecycle(
    lifecycle: Mapping[str, object],
    episodes: Iterable[object],
    minimum_samples: int = 3,
) -> Dict[str, object]:
    """Summarise later observations without turning them into a decision score."""

    lifecycle = dict(lifecycle or {})
    minimum = max(1, min(100, integer(minimum_samples, 3)))
    gathered = eligible_outcome_rows(lifecycle, episodes)
    rows = list(gathered["rows"])
    overall_rows = select_latest_outcome_per_episode(rows)
    summary = outcome_state(overall_rows, minimum)
    by_horizon: Dict[int, List[Dict[str, object]]] = {}
    for row in rows:
        by_horizon.setdefault(integer(row.get("horizonMinutes")), []).append(row)
    horizon_assessments = []
    for horizon, grouped in sorted(by_horizon.items()):
        item = outcome_state(select_latest_outcome_per_episode(grouped), minimum)
        horizon_assessments.append({"horizonMinutes": horizon, **item})
    state = str(summary["outcomeState"])
    if state == "insufficient-sample":
        explanation = "유효한 사후 관측이 " + str(summary["sampleCount"]) + "건이라 아직 결론을 내리기엔 표본이 부족합니다."
    elif state == "supported":
        explanation = "유효한 사후 관측에서 이 가설과 같은 방향의 결과가 더 많이 확인됐습니다."
    elif state == "contradicted":
        explanation = "유효한 사후 관측에서 이 가설과 반대 방향의 결과가 더 많이 확인됐습니다."
    else:
        explanation = "유효한 사후 관측이 섞여 있어 이 가설의 결과를 한 방향으로 판단할 수 없습니다."
    scope = text(lifecycle.get("scope")) or "account"
    return {
        "version": HYPOTHESIS_OUTCOME_REVIEW_VERSION,
        "scope": scope,
        "scopeLabel": "시장 공통 가설" if scope == "market" else "계정 적용 가설",
        "lifecycleKey": text(lifecycle.get("lifecycleKey")) or lifecycle_reference_key(lifecycle),
        "lifecycleId": text(lifecycle.get("lifecycleId")),
        "accountId": text(lifecycle.get("accountId")) if scope == "account" else "",
        "symbol": upper(lifecycle.get("symbol")),
        "familyId": text(lifecycle.get("familyId")),
        "minimumSampleCount": minimum,
        "matchedEpisodeCount": gathered["matchedEpisodeCount"],
        "excludedOutcomeCount": gathered["excludedOutcomeCount"],
        "horizonAssessments": horizon_assessments,
        "decisionEligibility": "historical-review-only",
        "automaticDeployment": False,
        "summary": explanation,
        **summary,
    }


def outcome_assessments_for_lifecycles(
    lifecycles: Iterable[object],
    episodes: Iterable[object],
    minimum_samples: int = 3,
) -> List[Dict[str, object]]:
    items = [as_dict(item) for item in lifecycles or []]
    assessments = [
        outcome_assessment_for_lifecycle(item, episodes, minimum_samples)
        for item in items
        if text(item.get("lifecycleId")) and upper(item.get("symbol"))
    ]
    return sorted(assessments, key=lambda item: (
        text(item.get("scope")),
        upper(item.get("symbol")),
        text(item.get("lifecycleId")),
    ))


def outcome_assessments_from_episodes(
    episodes: Iterable[object],
    minimum_samples: int = 3,
) -> List[Dict[str, object]]:
    rows = [as_dict(item) for item in episodes or []]
    return outcome_assessments_for_lifecycles(
        lifecycle_references_from_episodes(rows),
        rows,
        minimum_samples=minimum_samples,
    )


def lifecycle_policy_payload(record: Mapping[str, object]) -> Dict[str, object]:
    snapshot = record.get("snapshot") if isinstance(record.get("snapshot"), Mapping) else {}
    raw = snapshot.get("policy") if isinstance(snapshot.get("policy"), Mapping) else {}
    return HypothesisLifecyclePolicy.from_dict(dict(raw or {})).to_dict()


def lifecycle_expiry(record: Mapping[str, object]) -> str:
    policy = HypothesisLifecyclePolicy.from_dict(lifecycle_policy_payload(record))
    if not policy.validity_minutes:
        return ""
    observed = parse_timestamp(record.get("lastObservedAt") or record.get("firstObservedAt"))
    if not observed:
        return ""
    return (observed + timedelta(minutes=policy.validity_minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def lifecycle_review_item(
    record: Mapping[str, object],
    assessment: Mapping[str, object] = None,
) -> Dict[str, object]:
    record = dict(record or {})
    snapshot = record.get("snapshot") if isinstance(record.get("snapshot"), Mapping) else {}
    policy = lifecycle_policy_payload(record)
    profiles = snapshot.get("observationProfiles") if isinstance(snapshot.get("observationProfiles"), Mapping) else {}
    required_domains = values(policy.get("requiredFreshnessDomains"))
    profile_domains = values(list(profiles.keys()))
    visible_domains = required_domains + [domain for domain in profile_domains if domain not in required_domains]
    freshness = []
    for domain in visible_domains:
        profile = profiles.get(domain)
        profile = profile if isinstance(profile, Mapping) else {}
        freshness.append({
            "domain": domain,
            "status": text(profile.get("freshnessStatus")) or "unavailable",
            "reason": text(profile.get("freshnessGateReason")),
            "required": domain in required_domains or bool(profile.get("freshnessRequired")),
            "judgementEvidenceUsable": bool(profile.get("judgementEvidenceUsable")) if "judgementEvidenceUsable" in profile else True,
        })
    return {
        "lifecycleKey": text(record.get("lifecycleKey")),
        "lifecycleId": text(record.get("lifecycleId")),
        "scope": text(record.get("scope")) or "account",
        "scopeLabel": "시장 공통 가설" if text(record.get("scope")) == "market" else "계정 적용 가설",
        "symbol": upper(record.get("symbol")),
        "familyId": text(record.get("familyId")),
        "state": text(record.get("state")) or "observed",
        "stateLabel": text(record.get("stateLabel")) or HYPOTHESIS_LIFECYCLE_STATE_LABELS.get(text(record.get("state")), text(record.get("state"))),
        "transitionReason": text(record.get("transitionReason")),
        "materialChange": bool(record.get("materialChange")),
        "firstObservedAt": text(record.get("firstObservedAt")),
        "lastObservedAt": text(record.get("lastObservedAt")),
        "lastTransitionAt": text(record.get("lastTransitionAt")),
        "inferenceGenerationId": text(record.get("inferenceGenerationId")),
        "previousGenerationId": text(record.get("previousGenerationId")),
        "evidenceDelta": dict(record.get("evidenceDelta") or {}),
        "supportingEvidenceIds": values(snapshot.get("supportingEvidenceIds")),
        "counterEvidenceIds": values(snapshot.get("counterEvidenceIds")),
        "causalPathIds": values(snapshot.get("causalPathIds")),
        "sourceRuleIds": values(snapshot.get("sourceRuleIds")),
        "policy": policy,
        "expiresAt": lifecycle_expiry(record),
        "freshness": freshness,
        "outcomeAssessment": dict(assessment or {}),
        "accountId": text(record.get("accountId")),
        "marketId": text(record.get("marketId")),
    }
