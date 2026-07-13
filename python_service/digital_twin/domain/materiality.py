from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from .market_data import number
from . import news_analysis as news_domain


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def float_setting(settings: Dict[str, object], key: str, fallback: float, lower: float = 0.0, upper: float = 100.0) -> float:
    raw = (settings or {}).get(key)
    if str(raw or "").strip() == "":
        parsed = fallback
    else:
        parsed = number(raw)
    return max(lower, min(upper, float(parsed if parsed is not None else fallback)))


def percent_delta(previous: object, current: object) -> float:
    before = number(previous)
    after = number(current)
    if not before or not after:
        return 0.0
    return (float(after) - float(before)) / abs(float(before)) * 100.0


def crossed_zero(previous: object, current: object) -> bool:
    before = number(previous)
    after = number(current)
    if before == 0 or after == 0:
        return False
    return (before < 0 <= after) or (before > 0 >= after)


@dataclass
class MaterialityAssessment:
    subject: str
    trigger: str
    score: float
    threshold: float
    passed: bool
    grade: str
    reason: str
    changed_fields: List[str] = field(default_factory=list)
    components: Dict[str, float] = field(default_factory=dict)
    facts: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "subject": self.subject,
            "trigger": self.trigger,
            "score": round(float(self.score or 0), 1),
            "threshold": round(float(self.threshold or 0), 1),
            "passed": bool(self.passed),
            "grade": self.grade,
            "reason": self.reason,
            "changedFields": list(self.changed_fields or []),
            "components": {key: round(float(value or 0), 1) for key, value in (self.components or {}).items()},
            "facts": dict(self.facts or {}),
        }


def materiality_grade(score: float, passed: bool) -> str:
    if not passed:
        return "record"
    if score >= 82:
        return "urgent"
    if score >= 65:
        return "watch"
    return "record"


def materiality_threshold(settings: Dict[str, object], specific_key: str, fallback: float = 65.0) -> float:
    gate_default = float_setting(settings, "materialityMinimumScore", fallback, 0.0, 100.0)
    return float_setting(settings, specific_key, gate_default, 0.0, 100.0)


def disabled_assessment(subject: str, trigger: str, changed_fields: Iterable[str]) -> MaterialityAssessment:
    return MaterialityAssessment(
        subject=subject,
        trigger=trigger,
        score=100.0,
        threshold=0.0,
        passed=True,
        grade="urgent",
        reason="중요 변경 게이트가 비활성화되어 모든 변경을 통과시킵니다.",
        changed_fields=list(changed_fields or []),
        components={"gateDisabled": 100.0},
    )


def market_change_materiality(
    symbol: str,
    previous: Dict[str, object],
    current: Dict[str, object],
    change: Dict[str, object],
    settings: Dict[str, object] = None,
) -> MaterialityAssessment:
    settings = settings or {}
    changed_fields = list((change or {}).get("fields") or [])
    threshold = materiality_threshold(settings, "marketMaterialityMinimumScore", 65.0)
    if not truthy(settings.get("materialityGateEnabled"), True):
        return disabled_assessment(symbol, "market-data-update", changed_fields)
    if not changed_fields:
        return MaterialityAssessment(symbol, "market-data-update", 0.0, threshold, False, "record", "투자 판단 필드 변화가 없습니다.")
    if not previous:
        score = 35.0
        passed = score >= threshold
        return MaterialityAssessment(
            symbol,
            "market-data-update",
            score,
            threshold,
            passed,
            materiality_grade(score, passed),
            "초기 시장 데이터 적재는 기록하되 단독 알림 추론은 억제합니다.",
            changed_fields,
            {"initialObservation": score},
        )

    price_change = percent_delta(previous.get("currentPrice"), current.get("currentPrice"))
    price_threshold = float_setting(settings, "marketMaterialityPriceChangePct", 0.6, 0.0, 20.0)
    trend_threshold = float_setting(settings, "marketMaterialityTrendDistancePct", 2.0, 0.0, 30.0)
    volume_threshold = float_setting(settings, "marketMaterialityVolumeRatio", 1.5, 0.0, 20.0)
    components: Dict[str, float] = {}

    if abs(price_change) >= price_threshold:
        components["priceMove"] = min(28.0, abs(price_change) / max(0.1, price_threshold) * 10.0)
    current_ma20 = number(current.get("ma20Distance"))
    previous_ma20 = number(previous.get("ma20Distance"))
    current_ma60 = number(current.get("ma60Distance"))
    previous_ma60 = number(previous.get("ma60Distance"))
    if crossed_zero(previous_ma20, current_ma20) or abs(current_ma20) >= trend_threshold:
        components["ma20Threshold"] = 24.0 if crossed_zero(previous_ma20, current_ma20) else min(18.0, abs(current_ma20) / max(0.1, trend_threshold) * 8.0)
    if crossed_zero(previous_ma60, current_ma60) or abs(current_ma60) >= trend_threshold:
        components["ma60Threshold"] = 18.0 if crossed_zero(previous_ma60, current_ma60) else min(14.0, abs(current_ma60) / max(0.1, trend_threshold) * 6.0)
    volume_ratio = number(current.get("volumeRatio"))
    if volume_ratio >= volume_threshold:
        components["volumeConfirmation"] = min(18.0, volume_ratio / max(0.1, volume_threshold) * 10.0)
    trade_strength = number(current.get("tradeStrength"))
    if trade_strength >= 120:
        components["tradeStrength"] = min(12.0, (trade_strength - 100.0) / 5.0)
    imbalance = abs(number(current.get("orderbookImbalance") or current.get("bidAskImbalance")))
    if imbalance >= 20:
        components["orderbookImbalance"] = min(10.0, imbalance / 4.0)
    if current.get("dataQuality") and previous.get("dataQuality") and current.get("dataQuality") != previous.get("dataQuality"):
        components["dataQualityChange"] = 12.0

    score = min(100.0, sum(components.values()))
    passed = score >= threshold
    reason = "중요 시장 변화 기준 통과" if passed else "변화는 기록하지만 중요도 기준에는 미달합니다."
    return MaterialityAssessment(
        symbol,
        "market-data-update",
        score,
        threshold,
        passed,
        materiality_grade(score, passed),
        reason,
        changed_fields,
        components,
        {
            "priceChangePct": round(price_change, 3),
            "volumeRatio": round(volume_ratio, 3),
            "ma20Distance": round(current_ma20, 3),
            "ma60Distance": round(current_ma60, 3),
        },
    )


def evidence_materiality(
    evidence,
    settings: Dict[str, object] = None,
) -> MaterialityAssessment:
    settings = settings or {}
    symbol = str(getattr(evidence, "symbol", "") or "").upper().strip()
    threshold = materiality_threshold(settings, "newsMaterialityMinimumScore", 65.0)
    payload = getattr(evidence, "raw_payload", {}) if isinstance(getattr(evidence, "raw_payload", {}), dict) else {}
    if not truthy(settings.get("materialityGateEnabled"), True):
        return disabled_assessment(symbol, "research-evidence-update", ["researchEvidence"])
    scope = str(payload.get("relationScope") or "").strip() or "context"
    if scope != "context" and not news_domain.relation_scope_is_investable(scope):
        return MaterialityAssessment(symbol, "research-evidence-update", 0.0, threshold, False, "record", "뉴스 관련성 분류가 " + scope + "입니다.")
    relevance = number(payload.get("relevanceScore"))
    reliability = number(payload.get("sourceReliability"))
    materiality = number(payload.get("materialityScore"))
    impact = abs(number(getattr(evidence, "impact_score", 0.0)))
    confidence = number(getattr(evidence, "confidence", 0.0)) * 100.0
    polarity = str(getattr(evidence, "polarity", "") or "context")

    components = {
        "relevance": min(28.0, relevance * 0.28),
        "sourceReliability": min(16.0, reliability * 0.16),
        "eventMateriality": min(30.0, materiality * 0.30),
        "impact": min(16.0, impact * 2.0),
        "confidence": min(10.0, confidence * 0.10),
    }
    if scope == "direct":
        components["directScope"] = 12.0
    elif scope in {"peer", "sector"}:
        components["indirectScope"] = 4.0
    if polarity in {"risk", "support"} and scope == "direct":
        components["actionablePolarity"] = 8.0

    score = min(100.0, sum(components.values()))
    if scope in {"peer", "sector", "market"} and materiality < 75 and score > threshold - 1:
        score = threshold - 1
    passed = score >= threshold
    reason = "중요 뉴스/리서치 근거 기준 통과" if passed else "뉴스는 저장하지만 알림 추론 기준에는 미달합니다."
    return MaterialityAssessment(
        symbol,
        "research-evidence-update",
        score,
        threshold,
        passed,
        materiality_grade(score, passed),
        reason,
        ["researchEvidence"],
        components,
        {
            "relationScope": scope,
            "relevanceScore": round(relevance, 1),
            "sourceReliability": round(reliability, 1),
            "materialityScore": round(materiality, 1),
            "polarity": polarity,
            "eventType": str(payload.get("eventType") or ""),
        },
    )
