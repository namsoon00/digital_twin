from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from . import news_analysis as news_domain


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
NEWS_COLLECTION_ADMISSION_VERSION = "news-collection-admission-v1"
RELEVANCE_ORDER = ("unrelated", "context", "related", "direct")
SOURCE_TRUST_ORDER = ("unknown", "limited", "standard", "trusted")
MATERIALITY_ORDER = ("context", "notable", "material")
CORRECTION_MARKERS = (
    "correction",
    "corrected",
    "retraction",
    "withdraws",
    "정정",
    "오보",
    "기사 수정",
)


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def ordered_state(value: object, allowed: tuple, fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else fallback


def state_at_least(value: object, minimum: object, allowed: tuple) -> bool:
    current = ordered_state(value, allowed, allowed[0])
    required = ordered_state(minimum, allowed, allowed[-1])
    return allowed.index(current) >= allowed.index(required)


def evidence_payload(evidence) -> Dict[str, object]:
    payload = getattr(evidence, "raw_payload", {})
    return payload if isinstance(payload, dict) else {}


def evidence_is_correction(evidence) -> bool:
    payload = evidence_payload(evidence)
    if truthy(payload.get("isCorrection"), False):
        return True
    text = " ".join([
        str(getattr(evidence, "title", "") or ""),
        str(getattr(evidence, "summary", "") or ""),
    ]).casefold()
    return any(marker.casefold() in text for marker in CORRECTION_MARKERS)


@dataclass(frozen=True)
class NewsCollectionAdmission:
    evidence_id: str
    symbol: str
    passed: bool
    decision: str
    reason: str
    reason_codes: List[str] = field(default_factory=list)
    relevance_state: str = "unrelated"
    source_trust_state: str = "unknown"
    materiality_state: str = "context"
    validation_state: str = "conditional"
    body_available: bool = False
    body_quality_passed: bool = False
    correction: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": NEWS_COLLECTION_ADMISSION_VERSION,
            "evidenceId": self.evidence_id,
            "symbol": self.symbol,
            "passed": bool(self.passed),
            "decision": self.decision,
            "reason": self.reason,
            "reasonCodes": list(self.reason_codes),
            "relevanceState": self.relevance_state,
            "sourceTrustState": self.source_trust_state,
            "materialityState": self.materiality_state,
            "validationState": self.validation_state,
            "bodyAvailable": bool(self.body_available),
            "bodyQualityPassed": bool(self.body_quality_passed),
            "correction": bool(self.correction),
        }


def assess_news_collection_admission(
    evidence,
    settings: Dict[str, object] = None,
) -> NewsCollectionAdmission:
    settings = dict(settings or {})
    payload = evidence_payload(evidence)
    facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
    states = news_domain.news_state_payload({**payload, **facts})
    correction = evidence_is_correction(evidence)
    body_available = bool(facts.get("bodyAvailable")) or str(
        payload.get("articleReadStatus") or facts.get("readStatus") or ""
    ).strip().lower() == "body"
    body_quality_passed = (
        facts.get("bodyQualityPassed") is True
        and payload.get("bodyQualityPassed") is not False
    )
    evidence_id = str(getattr(evidence, "evidence_id", "") or "")
    symbol = str(getattr(evidence, "symbol", "") or "").upper().strip()

    # Production runtime settings always provide this key. Keeping direct
    # library callers backward compatible avoids silently changing fixtures,
    # maintenance scripts, or one-off imports that intentionally pass no
    # runtime policy at all.
    quality_gate_enabled = (
        "newsCollectionQualityGateEnabled" in settings
        and truthy(settings.get("newsCollectionQualityGateEnabled"), True)
    )
    if not quality_gate_enabled:
        return NewsCollectionAdmission(
            evidence_id,
            symbol,
            True,
            "retain",
            "뉴스 저장 품질 게이트가 비활성화되어 기존 수집 정책을 사용합니다.",
            relevance_state=states["relevanceState"],
            source_trust_state=states["sourceTrustState"],
            materiality_state=states["materialityState"],
            validation_state=states["validationState"],
            body_available=body_available,
            body_quality_passed=body_quality_passed,
            correction=correction,
        )

    minimum_relevance = ordered_state(
        settings.get("newsCollectionMinimumRelevanceState"),
        RELEVANCE_ORDER,
        "direct",
    )
    minimum_trust = ordered_state(
        settings.get("newsCollectionMinimumSourceTrustState"),
        SOURCE_TRUST_ORDER,
        "standard",
    )
    minimum_materiality = ordered_state(
        settings.get("newsCollectionMinimumMaterialityState"),
        MATERIALITY_ORDER,
        "material",
    )
    require_body = truthy(settings.get("newsCollectionRequireArticleBody"), True)
    reasons: List[str] = []

    if payload.get("excludedReason"):
        reasons.append("entity-or-editorial-excluded")
    if not state_at_least(states["relevanceState"], minimum_relevance, RELEVANCE_ORDER):
        reasons.append("relevance-below-policy")
    if not state_at_least(states["sourceTrustState"], minimum_trust, SOURCE_TRUST_ORDER):
        reasons.append("source-trust-below-policy")
    if not correction and not state_at_least(
        states["materialityState"], minimum_materiality, MATERIALITY_ORDER
    ):
        reasons.append("materiality-below-policy")
    if require_body and not body_available:
        reasons.append("article-body-missing")
    if require_body and not body_quality_passed:
        reasons.append("article-body-quality-failed")
    quality_gate = payload.get("qualityGate") if isinstance(payload.get("qualityGate"), dict) else {}
    if quality_gate.get("passed") is False:
        reasons.append("article-quality-gate-failed")
    if states["validationState"] == "blocked":
        reasons.append("validation-blocked")

    labels = {
        "entity-or-editorial-excluded": "종목 직접 사건이 아닌 기사",
        "relevance-below-policy": "종목 관련성이 저장 기준보다 낮음",
        "source-trust-below-policy": "출처 신뢰도가 저장 기준보다 낮음",
        "materiality-below-policy": "투자 판단 중요도가 저장 기준보다 낮음",
        "article-body-missing": "원문 본문을 확보하지 못함",
        "article-body-quality-failed": "원문 본문 품질이 부족함",
        "article-quality-gate-failed": "기사 품질 검증을 통과하지 못함",
        "validation-blocked": "기사 검증 상태가 차단됨",
    }
    reasons = list(dict.fromkeys(reasons))
    passed = not reasons
    reason = (
        "종목 직접성·출처·원문·사건 중요도 기준을 통과했습니다."
        if passed
        else " · ".join(labels.get(code, code) for code in reasons)
    )
    return NewsCollectionAdmission(
        evidence_id,
        symbol,
        passed,
        "retain" if passed else "discard",
        reason,
        reasons,
        states["relevanceState"],
        states["sourceTrustState"],
        states["materialityState"],
        states["validationState"],
        body_available,
        body_quality_passed,
        correction,
    )


def annotate_news_collection_admission(evidence, assessment: NewsCollectionAdmission):
    payload = dict(evidence_payload(evidence))
    payload["collectionAdmission"] = assessment.to_dict()
    evidence.raw_payload = payload
    return evidence


def news_collection_admission_summary(
    assessments: Iterable[NewsCollectionAdmission],
) -> Dict[str, object]:
    rows = list(assessments or [])
    rejected = [row for row in rows if not row.passed]
    reason_counts = Counter(
        reason
        for row in rejected
        for reason in row.reason_codes
    )
    return {
        "version": NEWS_COLLECTION_ADMISSION_VERSION,
        "evaluatedCount": len(rows),
        "admittedCount": len(rows) - len(rejected),
        "rejectedCount": len(rejected),
        "rejectionReasonCounts": dict(sorted(reason_counts.items())),
        "rejectedItems": [row.to_dict() for row in rejected[:20]],
    }
