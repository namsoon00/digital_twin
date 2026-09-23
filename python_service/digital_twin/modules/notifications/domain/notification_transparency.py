"""Customer-safe explanations for AI fallback and evidence exclusion."""

from __future__ import annotations

from typing import Dict, Mapping


AI_FALLBACK_MESSAGES = {
    "prompt-contract-budget": "필수 판단 자료를 AI 입력 한도 안에 안전하게 담지 못했습니다.",
    "contract-invalid": "AI 응답이 행동·근거 검증 계약을 통과하지 못했습니다.",
    "timeout": "AI 모델 응답이 완료 대기 시간 안에 끝나지 않았습니다.",
    "capacity": "AI 실행 슬롯을 확보하지 못했습니다.",
    "model-process": "AI 모델 실행 프로세스가 유효한 응답을 반환하지 못했습니다.",
    "execution": "AI가 검증 가능한 투자 판단을 반환하지 못했습니다.",
    "ai-narrative-claim-contract-missing": "AI 문장에 확인 가능한 근거 연결이 없었습니다.",
    "ai-narrative-claims-rejected": "AI 문장이 저장된 사실과의 검증을 통과하지 못했습니다.",
    "no-verified-ai-claims": "검증을 통과한 AI 문장이 없었습니다.",
}

NEWS_EXCLUSION_MESSAGES = {
    "different-subject": "다른 종목을 주로 다룬 자료",
    "source-time-missing": "발행 시각을 확인할 수 없는 자료",
    "source-time-in-future": "발행 시각이 기준 시각보다 미래인 자료",
    "evidence-stale": "허용 신선도를 지난 자료",
    "lifecycle-inactive": "이미 비활성화된 자료",
    "news-reasoning-not-eligible": "추론 사용 기준을 통과하지 못한 뉴스",
    "news-decision-inline-not-eligible": "매수·매도 판단에 직접 사용할 수 없는 뉴스",
    "news-analysis-not-current": "최신 본문 분석이 완료되지 않은 뉴스",
    "claim-governance-not-eligible": "출처·주장 검증을 통과하지 못한 자료",
    "news-reference-only-unverified": "참고는 가능하지만 행동 근거로 검증되지 않은 뉴스",
    "official-document-not-verified": "공식 문서 원문 또는 분석이 확인되지 않은 공시",
}


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def ai_fallback_disclosure(context: Mapping[str, object]) -> Dict[str, object]:
    """Describe why deterministic TypeDB output replaced an AI result."""

    values = _mapping(context)
    execution = _mapping(values.get("notificationAiExecutionAudit"))
    fallback = _mapping(execution.get("fallback"))
    failure = _mapping(execution.get("failure")) or _mapping(
        values.get("notificationAiFailure")
    )
    publication = _mapping(
        _mapping(values.get("notificationNarrativeBrief")).get("publication")
    )
    used = bool(
        fallback.get("used")
        or publication.get("fallbackUsed")
        or _text(execution.get("status")).lower() == "typedb-fallback"
    )
    if not used:
        return {"used": False}
    code = _text(
        failure.get("category")
        or fallback.get("reasonCode")
        or publication.get("fallbackReason")
        or "execution"
    ).lower()
    message = AI_FALLBACK_MESSAGES.get(code)
    if not message:
        raw_reason = _text(failure.get("summary") or fallback.get("reason"))
        message = raw_reason or AI_FALLBACK_MESSAGES["execution"]
    ai_attempted = bool(execution.get("aiAttempted"))
    result_owner = "TypeDB 관계 추론과 시스템 근거 요약"
    return {
        "version": "notification-analysis-transparency-v1",
        "used": True,
        "reasonCode": code,
        "reason": message,
        "aiAttempted": ai_attempted,
        "stage": _text(failure.get("stage") or fallback.get("stage") or "model-execution"),
        "retryable": bool(failure.get("retryable")),
        "resultOwner": result_owner,
        "userMessage": (
            message
            + " 따라서 AI 의견은 사용하지 않았고, 이번 내용은 "
            + result_owner
            + "으로 작성했습니다."
        ),
    }


def _evidence_admission(context: Mapping[str, object]) -> Dict[str, object]:
    values = _mapping(context)
    execution = _mapping(values.get("notificationAiExecutionAudit"))
    candidates = [
        _mapping(execution.get("contextRouting")),
        _mapping(_mapping(execution.get("decisionCore")).get("routingAudit")),
        _mapping(_mapping(execution.get("inferencePacket")).get("contextRouting")),
        _mapping(values.get("notificationAiContextRouting")),
    ]
    for candidate in candidates:
        admission = _mapping(candidate.get("evidenceAdmission"))
        if admission:
            return admission
    return {}


def news_exclusion_disclosure(context: Mapping[str, object]) -> Dict[str, object]:
    """Summarize why collected news or filings did not enter AI judgement."""

    admission = _evidence_admission(context)
    if not admission:
        return {"available": False}
    evaluated = int(admission.get("evaluatedCount") or 0)
    eligible = int(admission.get("eligibleCount") or 0)
    excluded = int(admission.get("excludedCount") or max(0, evaluated - eligible))
    omitted = int(admission.get("omittedForBudgetCount") or 0)
    reason_counts = _mapping(admission.get("reasonCounts"))
    reasons = [
        {
            "reasonCode": str(code),
            "label": NEWS_EXCLUSION_MESSAGES.get(str(code), str(code)),
            "count": int(count or 0),
        }
        for code, count in sorted(
            reason_counts.items(),
            key=lambda item: (-int(item[1] or 0), str(item[0])),
        )
        if int(count or 0) > 0
    ]
    rows = [item["label"] + " " + str(item["count"]) + "건" for item in reasons[:4]]
    if omitted:
        rows.append("입력 크기 제한으로 우선순위에서 밀린 자료 " + str(omitted) + "건")
    return {
        "version": "notification-evidence-exclusion-v1",
        "available": True,
        "evaluatedCount": evaluated,
        "eligibleCount": eligible,
        "excludedCount": excluded,
        "omittedForBudgetCount": omitted,
        "reasons": reasons,
        "summary": (
            "후보 " + str(evaluated) + "건 중 " + str(eligible)
            + "건을 판단 자료로 사용하고 " + str(excluded) + "건을 제외했습니다."
        ),
        "rows": rows,
    }
