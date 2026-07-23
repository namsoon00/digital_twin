"""Categorical quality review for hypothesis outcome history.

This module creates review requests from already recorded lifecycle and outcome
facts.  It deliberately has no action selector, numeric quality score, or
automatic RuleBox mutation path.
"""

from hashlib import sha256
from typing import Dict, Iterable, List, Mapping


QUALITY_STATE_LABELS = {
    "stable": "관찰 유지",
    "observe": "표본 축적 중",
    "coverage-gap": "관측 데이터 보완 필요",
    "freshness-blocked": "신선도 복구 필요",
    "lifecycle-review": "수명주기 정책 점검",
    "revision-required": "가설 설명 재검토",
}


def text(value: object) -> str:
    return str(value or "").strip()


def values(value: object, limit: int = 80) -> List[str]:
    rows = value if isinstance(value, (list, tuple, set)) else [value]
    result: List[str] = []
    for raw in rows:
        item = text(raw)
        if item and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def review_key(item: Mapping[str, object], state: str, episode_ids: Iterable[object]) -> str:
    basis = "|".join([
        text(item.get("lifecycleKey")),
        state,
        ",".join(values(episode_ids, 100)),
        text((item.get("outcomeAssessment") or {}).get("latestObservedAt")) if isinstance(item.get("outcomeAssessment"), Mapping) else "",
    ])
    return "hypothesis-quality-review:" + sha256(basis.encode("utf-8")).hexdigest()[:20]


def required_freshness_problem(item: Mapping[str, object]) -> List[str]:
    domains = []
    for profile in item.get("freshness") or []:
        if not isinstance(profile, Mapping) or not bool(profile.get("required")):
            continue
        status = text(profile.get("status")).lower()
        usable = bool(profile.get("judgementEvidenceUsable", True))
        if status in {"stale", "unavailable"} or not usable:
            domain = text(profile.get("domain"))
            if domain and domain not in domains:
                domains.append(domain)
    return domains


def quality_review_for_item(item: Mapping[str, object]) -> Dict[str, object]:
    source = dict(item or {}) if isinstance(item, Mapping) else {}
    outcome = source.get("outcomeAssessment") if isinstance(source.get("outcomeAssessment"), Mapping) else {}
    outcome_state = text(outcome.get("outcomeState")) or "insufficient-sample"
    sample_count = int(outcome.get("sampleCount") or 0)
    minimum = int(outcome.get("minimumSampleCount") or 1)
    missing_domains = values(outcome.get("missingObservationDomains"))
    excluded_reasons = outcome.get("excludedOutcomeReasons") if isinstance(outcome.get("excludedOutcomeReasons"), Mapping) else {}
    legacy_outcome_count = int(excluded_reasons.get("legacy-eligibility-not-recorded") or 0)
    freshness_domains = required_freshness_problem(source)
    lifecycle_state = text(source.get("state")) or "observed"
    state = "stable"
    reason = "현재 사후 관측과 수명주기에서 별도 정책 변경 요청이 없습니다."
    next_check = "새 사후 관측과 다음 정상 TypeDB 세대를 기다립니다."
    change_type = "continue-observation"
    if outcome_state == "contradicted" and sample_count >= minimum:
        state = "revision-required"
        reason = "독립된 사후 관측에서 가설과 반대 방향의 결과가 더 많이 확인됐습니다."
        next_check = "원문 근거, 반대 근거, 관측 계약과 TypeDB 규칙 미리보기를 함께 검토합니다."
        change_type = "review-hypothesis-explanation-and-evidence-coverage"
    elif missing_domains:
        state = "coverage-gap"
        reason = "사후 관측에 필요한 데이터가 없어 일부 결과를 검토에서 제외했습니다: " + ", ".join(missing_domains) + "."
        next_check = "누락 데이터의 수집 가능 여부와 관측 계약의 필수 도메인을 검토합니다."
        change_type = "review-outcome-observation-coverage"
    elif legacy_outcome_count:
        state = "coverage-gap"
        reason = "이전 사후 관측 " + str(legacy_outcome_count) + "건에는 관측 계약 적합성 기록이 없어 검토 결과에서 제외했습니다."
        next_check = "새 판단부터 관측 시점과 필수 데이터 기록이 함께 저장되는지 확인합니다."
        change_type = "review-legacy-outcome-migration"
    elif freshness_domains:
        state = "freshness-blocked"
        reason = "현재 가설을 판단하는 데 필요한 데이터가 최신 상태가 아닙니다: " + ", ".join(freshness_domains) + "."
        next_check = "데이터 원천 복구 후 새 TypeDB 추론 세대에서 가설을 다시 확인합니다."
        change_type = "recover-required-observation-freshness"
    elif lifecycle_state in {"invalidated", "expired"}:
        state = "lifecycle-review"
        reason = "가설이 " + ("반증" if lifecycle_state == "invalidated" else "만료") + " 상태로 전이됐습니다."
        next_check = "성립 조건, 반증 조건, 유효 기간이 실제 관측 주기와 맞는지 검토합니다."
        change_type = "review-lifecycle-contract"
    elif outcome_state == "insufficient-sample":
        state = "observe"
        reason = "유효한 사후 관측 표본이 " + str(sample_count) + "건으로 아직 충분하지 않습니다."
        next_check = "계약에 정한 관찰 기간과 필수 데이터를 충족하는 새 관측을 쌓습니다."
        change_type = "continue-observation"
    episode_ids = values(outcome.get("matchedEpisodeIds"), 100)
    return {
        "reviewId": review_key(source, state, episode_ids),
        "lifecycleKey": text(source.get("lifecycleKey")),
        "lifecycleId": text(source.get("lifecycleId")),
        "scope": text(source.get("scope")) or "account",
        "scopeLabel": text(source.get("scopeLabel")),
        "symbol": text(source.get("symbol")).upper(),
        "familyId": text(source.get("familyId")),
        "qualityState": state,
        "qualityStateLabel": QUALITY_STATE_LABELS[state],
        "reason": reason,
        "nextCheck": next_check,
        "changeType": change_type,
        "sourceRuleIds": values(source.get("sourceRuleIds")),
        "sourceEpisodeIds": episode_ids,
        "outcomeState": outcome_state,
        "sampleCount": sample_count,
        "minimumSampleCount": minimum,
        "missingObservationDomains": missing_domains,
        "freshnessProblemDomains": freshness_domains,
        "automaticDeployment": False,
        "decisionEligibility": "quality-review-only",
    }


def quality_review_workspace(items: Iterable[object]) -> Dict[str, object]:
    reviews = [quality_review_for_item(item) for item in items or [] if isinstance(item, Mapping)]
    counts: Dict[str, int] = {}
    for review in reviews:
        state = text(review.get("qualityState")) or "observe"
        counts[state] = int(counts.get(state) or 0) + 1
    review_required = [
        item for item in reviews
        if item.get("qualityState") in {"revision-required", "coverage-gap", "freshness-blocked", "lifecycle-review"}
    ]
    return {
        "status": "ok",
        "source": "hypothesis-lifecycle+observed-outcome-quality-review",
        "decisionEligibility": "quality-review-only",
        "automaticDeployment": False,
        "count": len(reviews),
        "summary": {
            "stateCounts": counts,
            "reviewRequiredCount": len(review_required),
            "observationCount": int(counts.get("observe") or 0),
        },
        "items": reviews,
        "reviewRequired": review_required,
    }
