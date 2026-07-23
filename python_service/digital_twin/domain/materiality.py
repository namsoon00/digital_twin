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
    parsed = fallback if str(raw or "").strip() == "" else number(raw)
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


def field_changed(changed_fields: Iterable[str], *fields: str) -> bool:
    changed = {str(field or "") for field in changed_fields or []}
    return any(field in changed for field in fields)


def threshold_transition(previous: object, current: object, threshold: float) -> str:
    """Describe a threshold entry or exit without treating a stable state as new."""
    before = abs(number(previous))
    after = abs(number(current))
    if before < threshold <= after:
        return "entered"
    if before >= threshold > after:
        return "cleared"
    return ""


def widening_distance(previous: object, current: object, minimum_change: float) -> bool:
    before = number(previous)
    after = number(current)
    if before == 0 or after == 0 or before * after < 0:
        return False
    return abs(after) - abs(before) >= minimum_change


def pressure_transition(previous: object, current: object, lower: float, upper: float) -> str:
    before = number(previous)
    after = number(current)
    before_active = bool(before) and (before <= lower or before >= upper)
    after_active = bool(after) and (after <= lower or after >= upper)
    if not before_active and after_active:
        return "entered"
    if before_active and not after_active:
        return "cleared"
    return ""


@dataclass
class MaterialityAssessment:
    subject: str
    trigger: str
    review_level: str
    passed: bool
    reason: str
    changed_fields: List[str] = field(default_factory=list)
    matched_conditions: List[str] = field(default_factory=list)
    facts: Dict[str, object] = field(default_factory=dict)
    data_state: str = "sufficient"
    change_state: str = "unchanged"
    evidence_role: str = "context"

    @property
    def grade(self) -> str:
        return self.review_level

    def to_dict(self) -> Dict[str, object]:
        return {
            "subject": self.subject,
            "trigger": self.trigger,
            "reviewLevel": self.review_level,
            "passed": bool(self.passed),
            "reason": self.reason,
            "changedFields": list(self.changed_fields or []),
            "matchedConditions": list(self.matched_conditions or []),
            "facts": dict(self.facts or {}),
            "dataState": self.data_state,
            "changeState": self.change_state,
            "evidenceRole": self.evidence_role,
        }


def disabled_assessment(subject: str, trigger: str, changed_fields: Iterable[str]) -> MaterialityAssessment:
    return MaterialityAssessment(
        subject=subject,
        trigger=trigger,
        review_level="check",
        passed=True,
        reason="중요 변화 필터가 꺼져 있어 변경 사실을 그대로 전달합니다.",
        changed_fields=list(changed_fields or []),
        matched_conditions=["filter-disabled"],
        change_state="new-condition",
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
    if not truthy(settings.get("materialityGateEnabled"), True):
        return disabled_assessment(symbol, "market-data-update", changed_fields)
    if not changed_fields:
        return MaterialityAssessment(
            symbol,
            "market-data-update",
            "normal",
            False,
            "투자 판단에 쓰는 값이 바뀌지 않았습니다.",
        )
    if not previous:
        return MaterialityAssessment(
            symbol,
            "market-data-update",
            "normal",
            False,
            "첫 관측값은 비교 대상이 없어 저장만 합니다.",
            changed_fields,
            ["initial-observation"],
            data_state="partial",
            change_state="new-condition",
        )

    price_change = percent_delta(previous.get("currentPrice"), current.get("currentPrice"))
    price_threshold = float_setting(settings, "marketMaterialityPriceChangePct", 0.6, 0.0, 20.0)
    trend_threshold = float_setting(settings, "marketMaterialityTrendDistancePct", 2.0, 0.0, 30.0)
    trend_change_threshold = float_setting(settings, "marketMaterialityTrendDistanceChangePct", 1.0, 0.0, 30.0)
    volume_threshold = float_setting(settings, "marketMaterialityVolumeRatio", 1.5, 0.0, 20.0)
    current_ma20 = number(current.get("ma20Distance"))
    previous_ma20 = number(previous.get("ma20Distance"))
    current_ma60 = number(current.get("ma60Distance"))
    previous_ma60 = number(previous.get("ma60Distance"))
    previous_volume_ratio = number(previous.get("volumeRatio"))
    volume_ratio = number(current.get("volumeRatio"))
    previous_trade_strength = number(previous.get("tradeStrength"))
    trade_strength = number(current.get("tradeStrength"))
    previous_imbalance = abs(number(previous.get("orderbookImbalance") or previous.get("bidAskImbalance")))
    imbalance = abs(number(current.get("orderbookImbalance") or current.get("bidAskImbalance")))
    matched: List[str] = []

    if field_changed(changed_fields, "currentPrice", "changeRate") and abs(price_change) >= price_threshold:
        matched.append("price-move")
    if field_changed(changed_fields, "ma20Distance"):
        ma20_transition = threshold_transition(previous_ma20, current_ma20, trend_threshold)
        if crossed_zero(previous_ma20, current_ma20):
            matched.append("ma20-cross")
        elif ma20_transition == "entered":
            matched.append("ma20-distance")
        elif ma20_transition == "cleared":
            matched.append("ma20-distance-cleared")
        elif widening_distance(previous_ma20, current_ma20, trend_change_threshold):
            matched.append("ma20-distance-widened")
    if field_changed(changed_fields, "ma60Distance"):
        ma60_transition = threshold_transition(previous_ma60, current_ma60, trend_threshold)
        if crossed_zero(previous_ma60, current_ma60):
            matched.append("ma60-cross")
        elif ma60_transition == "entered":
            matched.append("ma60-distance")
        elif ma60_transition == "cleared":
            matched.append("ma60-distance-cleared")
        elif widening_distance(previous_ma60, current_ma60, trend_change_threshold):
            matched.append("ma60-distance-widened")
    if field_changed(changed_fields, "volumeRatio"):
        volume_transition = threshold_transition(previous_volume_ratio, volume_ratio, volume_threshold)
        if volume_transition == "entered":
            matched.append("volume-confirmation")
        elif volume_transition == "cleared":
            matched.append("volume-confirmation-cleared")
    if field_changed(changed_fields, "tradeStrength"):
        trade_transition = pressure_transition(previous_trade_strength, trade_strength, 80, 120)
        if trade_transition == "entered":
            matched.append("trade-pressure")
        elif trade_transition == "cleared":
            matched.append("trade-pressure-cleared")
    if field_changed(changed_fields, "orderbookImbalance", "bidAskImbalance"):
        imbalance_transition = threshold_transition(previous_imbalance, imbalance, 20)
        if imbalance_transition == "entered":
            matched.append("orderbook-imbalance")
        elif imbalance_transition == "cleared":
            matched.append("orderbook-imbalance-cleared")
    if current.get("dataQuality") and previous.get("dataQuality") and current.get("dataQuality") != previous.get("dataQuality"):
        matched.append("data-state-change")

    directional = {
        "price-move",
        "ma20-cross",
        "ma60-cross",
        "ma20-distance",
        "ma60-distance",
        "ma20-distance-cleared",
        "ma60-distance-cleared",
        "ma20-distance-widened",
        "ma60-distance-widened",
    } & set(matched)
    confirmation = {
        "volume-confirmation",
        "trade-pressure",
        "orderbook-imbalance",
        "volume-confirmation-cleared",
        "trade-pressure-cleared",
        "orderbook-imbalance-cleared",
    } & set(matched)
    if "ma60-cross" in matched or (directional and confirmation):
        review_level = "act"
    elif directional or "data-state-change" in matched:
        review_level = "check"
    else:
        review_level = "normal"
    passed = review_level in {"check", "act", "immediate"}
    if price_change > 0:
        change_state = "improving"
        evidence_role = "support"
    elif price_change < 0:
        change_state = "worsening"
        evidence_role = "risk"
    else:
        change_state = "new-condition" if matched else "unchanged"
        evidence_role = "context"
    data_state = "partial" if str(current.get("dataQuality") or "").lower() in {"poor", "stale", "partial"} else "sufficient"
    reason = (
        "가격·추세 또는 수급 상태가 새 기준을 넘거나 해제되어 다시 확인합니다."
        if passed
        else "값은 갱신됐지만 기존 상태가 유지되어 TypeDB 재추론 요청은 만들지 않습니다."
    )
    return MaterialityAssessment(
        symbol,
        "market-data-update",
        review_level,
        passed,
        reason,
        changed_fields,
        matched,
        {
            "priceChangePct": round(price_change, 3),
            "volumeRatio": round(volume_ratio, 3),
            "ma20Distance": round(current_ma20, 3),
            "ma60Distance": round(current_ma60, 3),
            "ma20DistanceChange": round(current_ma20 - previous_ma20, 3),
            "ma60DistanceChange": round(current_ma60 - previous_ma60, 3),
        },
        data_state=data_state,
        change_state=change_state,
        evidence_role=evidence_role,
    )


def evidence_materiality(evidence, settings: Dict[str, object] = None) -> MaterialityAssessment:
    settings = settings or {}
    symbol = str(getattr(evidence, "symbol", "") or "").upper().strip()
    payload = getattr(evidence, "raw_payload", {}) if isinstance(getattr(evidence, "raw_payload", {}), dict) else {}
    if not truthy(settings.get("materialityGateEnabled"), True):
        return disabled_assessment(symbol, "research-evidence-update", ["researchEvidence"])
    scope = str(payload.get("relationScope") or "context").strip()
    if scope != "context" and not news_domain.relation_scope_is_investable(scope):
        return MaterialityAssessment(
            symbol,
            "research-evidence-update",
            "normal",
            False,
            "종목과 직접 연결되지 않은 자료라 저장만 합니다.",
            ["researchEvidence"],
            ["indirect-or-noisy-scope"],
            {"relationScope": scope},
            data_state="partial",
        )

    governance = payload.get("evidenceGovernance") if isinstance(payload.get("evidenceGovernance"), dict) else {}
    quality_gate = payload.get("qualityGate") if isinstance(payload.get("qualityGate"), dict) else {}
    eligible = bool(governance.get("investmentJudgmentEligible"))
    if not governance and payload.get("materialityPassed") is not None:
        eligible = bool(payload.get("materialityPassed"))
    polarity = str(getattr(evidence, "polarity", "") or "context").lower()
    read_scope = str(payload.get("readScope") or (payload.get("aiAnalysis") or {}).get("readScope") or "").lower()
    body_read = read_scope in {"full", "full-body", "article-body", "body"} or bool(payload.get("articleSummaryKo"))
    blocked = quality_gate.get("passed") is False or governance.get("dataState") in {"insufficient", "unavailable"}
    matched = ["direct-scope" if scope == "direct" else "investable-context"]
    if body_read:
        matched.append("article-body-read")
    if polarity in {"risk", "support", "negative", "positive", "bearish", "bullish"}:
        matched.append("direction-identified")
    if eligible:
        matched.append("governance-approved")

    if blocked:
        review_level = "blocked"
        passed = False
        data_state = "insufficient"
        reason = "본문 또는 출처 검증 조건을 통과하지 못했습니다."
        evidence_role = "blocking"
    elif eligible and scope == "direct" and body_read:
        review_level = "act" if polarity in {"risk", "negative", "bearish"} else "check"
        passed = True
        data_state = "sufficient"
        reason = "종목 직접 기사이며 본문과 출처 검증을 통과했습니다."
        evidence_role = "risk" if polarity in {"risk", "negative", "bearish"} else "support" if polarity in {"support", "positive", "bullish"} else "context"
    elif eligible:
        review_level = "check"
        passed = True
        data_state = "partial" if not body_read else "sufficient"
        reason = "투자 판단에 참고할 새 근거가 확인됐습니다."
        evidence_role = "risk" if polarity in {"risk", "negative", "bearish"} else "support" if polarity in {"support", "positive", "bullish"} else "context"
    else:
        review_level = "observe"
        passed = False
        data_state = "partial"
        reason = "새 자료는 저장하지만 투자 행동을 바꿀 근거로는 사용하지 않습니다."
        evidence_role = "context"

    return MaterialityAssessment(
        symbol,
        "research-evidence-update",
        review_level,
        passed,
        reason,
        ["researchEvidence"],
        matched,
        {
            "relationScope": scope,
            "polarity": polarity,
            "eventType": str(payload.get("eventType") or ""),
            "readScope": read_scope,
        },
        data_state=data_state,
        change_state="new-evidence",
        evidence_role=evidence_role,
    )
