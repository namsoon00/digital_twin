import html
from typing import Dict, List

from .market_data import number
from .notification_text_formatting import (
    FOOTER_DATA_LABELS,
    format_score_value,
    html_bullet,
    is_ontology_internal_data_line,
    plain_bullet,
    split_data_line,
)
from .ontology_relation_reasoning import relation_score_meaning


def notification_data_lines(raw_lines: List[str], metadata: Dict[str, object]) -> List[str]:
    lines = []
    for line in raw_lines:
        label, _value = split_data_line(line)
        if label in FOOTER_DATA_LABELS:
            continue
        if ontology_relation_context(metadata) and is_ontology_internal_data_line(line):
            continue
        lines.append(line)
    return lines

def _relation_context_candidates(value: object) -> List[Dict[str, object]]:
    if isinstance(value, dict) and value:
        return [dict(value)]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict) and item]
    return []


def ontology_relation_contexts(context_or_metadata: Dict[str, object]) -> List[Dict[str, object]]:
    if not isinstance(context_or_metadata, dict):
        return []
    contexts: List[Dict[str, object]] = []
    contexts.extend(_relation_context_candidates(context_or_metadata.get("ontologyRelationContext")))
    metadata = context_or_metadata.get("metadata")
    if isinstance(metadata, dict):
        contexts.extend(_relation_context_candidates(metadata.get("ontologyRelationContext")))
    review = context_or_metadata.get("ontologyReviewContext")
    if isinstance(review, dict):
        nested = review.get("relationRuleContext")
        contexts.extend(_relation_context_candidates(nested))
    return contexts


def ontology_relation_context(context_or_metadata: Dict[str, object]) -> Dict[str, object]:
    contexts = ontology_relation_contexts(context_or_metadata)
    return contexts[0] if contexts else {}


def fact_number(value: object) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def has_fact_value(value: object) -> bool:
    if value in (None, ""):
        return False
    try:
        float(str(value).replace(",", "").strip())
        return True
    except (TypeError, ValueError):
        return False


def fact_number_text(value: object, decimals: int = 2, signed: bool = False) -> str:
    amount = fact_number(value)
    text = (("%." + str(decimals) + "f") % amount).rstrip("0").rstrip(".")
    if "." not in text and abs(amount) >= 1000:
        text = format(int(round(amount)), ",")
    if signed and amount > 0:
        return "+" + text
    return text


FX_REGIME_LABELS = {
    "krw_weakening": "원화 약세",
    "krw_strengthening": "원화 강세",
    "fx_observed": "환율 관찰",
    "base_currency_or_unknown": "기준통화/미확인",
}

RATE_REGIME_LABELS = {
    "high_rate": "고금리",
    "low_rate": "저금리",
    "neutral_rate": "중립 금리",
}

CURVE_REGIME_LABELS = {
    "inverted_curve": "역전",
    "positive_curve": "정상",
    "flat_or_unknown_curve": "평탄/미확인",
}

RELATION_AXIS_ORDER = [
    "손익·보유비중",
    "종목 타입",
    "투자 성향·정책",
    "밸류에이션",
    "가격 회복·약화",
    "수급 심리",
    "뉴스·공시",
    "외부 환경",
    "데이터 신뢰도",
]

RELATION_AXIS_CATEGORY_LABELS = {
    "position": "손익·보유비중",
    "instrumentprofile": "종목 타입",
    "crossasset": "종목 타입",
    "trend": "가격 회복·약화",
    "liquidity": "수급 심리",
    "investorflow": "수급 심리",
    "addbuy": "투자 성향·정책",
    "valuation": "밸류에이션",
    "valuationrisk": "밸류에이션",
    "valuationopportunity": "밸류에이션",
    "undervaluationopportunity": "밸류에이션",
    "marginofsafety": "밸류에이션",
    "fairvalueestimate": "밸류에이션",
    "research": "뉴스·공시",
    "news": "뉴스·공시",
    "disclosure": "뉴스·공시",
    "macro": "외부 환경",
    "rateregime": "외부 환경",
    "fxregime": "외부 환경",
    "dataquality": "데이터 신뢰도",
    "dataqualitywarning": "데이터 신뢰도",
    "relationrule": "관계 규칙",
}

RELATION_AXIS_RULE_PREFIXES = [
    ("graph.instrument_profile.", "종목 타입"),
    ("graph.crypto.", "종목 타입"),
    ("graph.strategy_profile.", "투자 성향·정책"),
    ("graph.averaging_down.", "투자 성향·정책"),
    ("graph.valuation.", "밸류에이션"),
    ("graph.price.", "가격 회복·약화"),
    ("graph.holding.trend_transition.", "가격 회복·약화"),
    ("graph.watchlist.trend_transition.", "가격 회복·약화"),
    ("graph.flow.", "수급 심리"),
    ("graph.loss_smart_money.", "수급 심리"),
    ("graph.winner_momentum.", "수급 심리"),
    ("graph.news.", "뉴스·공시"),
    ("graph.disclosure.", "뉴스·공시"),
    ("graph.macro.", "외부 환경"),
    ("graph.benchmark.", "외부 환경"),
    ("graph.factor.", "외부 환경"),
    ("graph.data_quality.", "데이터 신뢰도"),
    ("graph.coverage.", "데이터 신뢰도"),
    ("graph.execution.", "손익·보유비중"),
    ("graph.loss_guard.", "손익·보유비중"),
]

RELATION_DIRECTION_LABELS = {
    "risk": "위험 쪽 근거",
    "support": "버티는 근거",
    "counter": "반대 근거",
    "neutral": "참고 근거",
}


def source_fact_rows(context_or_metadata: Dict[str, object]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for context in ontology_relation_contexts(context_or_metadata):
        candidates = []
        if isinstance(context, dict):
            candidates.append(context.get("sourceFacts"))
            execution_plan = context.get("executionPlan")
            if isinstance(execution_plan, dict):
                candidates.append(execution_plan.get("sourceFacts"))
            prompt_context = context.get("promptContext")
            if isinstance(prompt_context, dict):
                candidates.append(prompt_context.get("facts"))
                prompt_execution_plan = prompt_context.get("executionPlan")
                if isinstance(prompt_execution_plan, dict):
                    candidates.append(prompt_execution_plan.get("sourceFacts"))
        for facts in candidates:
            if isinstance(facts, dict) and facts:
                rows.append(dict(facts))
    return rows


def fx_fact_line(facts: Dict[str, object]) -> str:
    pair = str(facts.get("fxRatePair") or "").upper().replace("/", "").strip()
    rate_value = facts.get("fxRateToKrw")
    if not has_fact_value(rate_value):
        rate_value = facts.get("usdKrwRate")
    rate_amount = fact_number(rate_value)
    if not pair and has_fact_value(facts.get("usdKrwRate")) and fact_number(facts.get("usdKrwRate")) > 0:
        pair = "USDKRW"
        rate_value = facts.get("usdKrwRate")
        rate_amount = fact_number(rate_value)
    if not pair or len(pair) < 6 or rate_amount <= 0:
        return ""
    base = pair[:3] if len(pair) >= 6 else str(facts.get("fxBaseCurrency") or "USD").upper()
    quote = pair[3:6] if len(pair) >= 6 else str(facts.get("fxQuoteCurrency") or "KRW").upper()
    if base == quote:
        return ""
    parts = []
    if base and quote:
        parts.append(base + "/" + quote)
    parts.append("1 " + base + " = " + fact_number_text(rate_value, 2) + " " + quote)
    source_type = str(facts.get("fxSourceType") or "").strip()
    provider = str(facts.get("fxProvider") or "").strip()
    source_labels = {
        "market_realtime": "실시간 API",
        "market_daily": "일일 API 갱신",
        "broker_applied_valuation": "계좌 적용 환율",
        "fallback_setting": "설정값 기준",
    }
    source_label = source_labels.get(source_type, "")
    if source_label:
        parts.append(source_label)
    elif provider:
        parts.append("출처 " + provider)
    exposure = facts.get("fxExposureRatio")
    if has_fact_value(exposure) and fact_number(exposure):
        parts.append("노출 " + fact_number_text(exposure, 1) + "%")
    regime = FX_REGIME_LABELS.get(str(facts.get("fxRegime") or ""), "")
    if regime:
        parts.append(regime)
    return "환율: " + " · ".join(parts) if parts else ""


def rate_fact_line(facts: Dict[str, object]) -> str:
    parts = []
    if has_fact_value(facts.get("macroDgs10")):
        parts.append("미국10년 " + fact_number_text(facts.get("macroDgs10"), 2) + "%")
    if has_fact_value(facts.get("macroDgs2")):
        parts.append("미국2년 " + fact_number_text(facts.get("macroDgs2"), 2) + "%")
    if has_fact_value(facts.get("macroDff")) and fact_number(facts.get("macroDff")):
        parts.append("연방기금 " + fact_number_text(facts.get("macroDff"), 2) + "%")
    if has_fact_value(facts.get("macroYieldSpread10y2y")):
        parts.append("10Y-2Y " + fact_number_text(facts.get("macroYieldSpread10y2y"), 2, signed=True) + "%p")
    if not parts:
        return ""
    regimes = []
    rate_regime = RATE_REGIME_LABELS.get(str(facts.get("rateRegime") or ""), "")
    curve_regime = CURVE_REGIME_LABELS.get(str(facts.get("yieldCurveRegime") or ""), "")
    if rate_regime:
        regimes.append(rate_regime)
    if curve_regime:
        regimes.append("수익률곡선 " + curve_regime)
    if regimes:
        parts.append("레짐 " + " / ".join(regimes))
    return "금리: " + " · ".join(parts)


def macro_context_lines(context_or_metadata: Dict[str, object]) -> List[str]:
    lines: List[str] = []
    for facts in source_fact_rows(context_or_metadata):
        for line in [fx_fact_line(facts), rate_fact_line(facts)]:
            if line and line not in lines:
                lines.append(line)
    return lines


def append_unique_lines(lines: List[str], additions: List[str]) -> List[str]:
    result = list(lines or [])
    existing = {str(line or "").strip() for line in result}
    for line in additions or []:
        text = str(line or "").strip()
        if text and text not in existing:
            result.append(text)
            existing.add(text)
    return result


def ontology_prompt_context(context_or_metadata: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(context_or_metadata, dict):
        return {}
    context = context_or_metadata.get("ontologyPromptContext")
    ontology_context = dict(context) if isinstance(context, dict) and context else {}
    context = context_or_metadata.get("notificationAiPromptContext")
    notification_context = dict(context) if isinstance(context, dict) and context else {}
    opinion = context_or_metadata.get("notificationAiOpinion")
    if isinstance(opinion, dict):
        context = opinion.get("promptContext")
        if isinstance(context, dict) and context and not notification_context:
            notification_context = dict(context)
    if notification_context and not ontology_context.get("promptTemplate"):
        return notification_context
    if ontology_context:
        return ontology_context
    if notification_context:
        return notification_context
    metadata = context_or_metadata.get("metadata")
    if isinstance(metadata, dict):
        context = metadata.get("ontologyPromptContext")
        ontology_context = dict(context) if isinstance(context, dict) and context else {}
        context = metadata.get("notificationAiPromptContext")
        notification_context = dict(context) if isinstance(context, dict) and context else {}
        opinion = metadata.get("notificationAiOpinion")
        if isinstance(opinion, dict):
            context = opinion.get("promptContext")
            if isinstance(context, dict) and context and not notification_context:
                notification_context = dict(context)
        if notification_context and not ontology_context.get("promptTemplate"):
            return notification_context
        if ontology_context:
            return ontology_context
        if notification_context:
            return notification_context
    relation_context = ontology_relation_context(context_or_metadata)
    nested = relation_context.get("promptContext") if isinstance(relation_context, dict) else {}
    return dict(nested or {}) if isinstance(nested, dict) else {}


def ontology_missing_data(context_or_metadata: Dict[str, object]) -> List[Dict[str, object]]:
    relation_context = ontology_relation_context(context_or_metadata)
    missing = relation_context.get("missingData") if isinstance(relation_context, dict) else []
    if not isinstance(missing, list):
        return []
    rows = []
    for item in missing:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("key") or "").strip()
            effect = str(item.get("effect") or "").strip()
            row = {"label": label, "effect": effect}
            status = str(item.get("status") or "").strip()
            source = str(item.get("source") or "").strip()
            if status:
                row["status"] = status
            if source:
                row["source"] = source
            rows.append(row)
        elif str(item or "").strip():
            rows.append({"label": str(item).strip(), "effect": ""})
    return [item for item in rows if item.get("label")]


def rule_value(item: Dict[str, object], *keys):
    for key in keys:
        if isinstance(item, dict) and item.get(key) not in (None, ""):
            return item.get(key)
    return ""


def relation_axis_from_rule(rule_id: object, label: object = "") -> str:
    text = (str(rule_id or "") + " " + str(label or "")).strip()
    lowered = text.casefold()
    for prefix, axis in RELATION_AXIS_RULE_PREFIXES:
        if lowered.startswith(prefix):
            return axis
    if any(term in lowered for term in ["instrument", "profile", "종목 타입", "디지털자산", "우선주"]):
        return "종목 타입"
    if any(term in lowered for term in ["strategy_profile", "성향", "비중 한도", "물타기", "추가매수"]):
        return "투자 성향·정책"
    if any(term in lowered for term in ["valuation", "밸류", "저평가", "고평가", "안전마진", "적정가", "per", "eps", "fair value"]):
        return "밸류에이션"
    if any(term in lowered for term in ["trend", "recovery", "breakdown", "5일", "20일", "60일", "평균"]):
        return "가격 회복·약화"
    if any(term in lowered for term in ["flow", "liquidity", "smart_money", "수급", "기관", "외국인", "체결", "호가"]):
        return "수급 심리"
    if any(term in lowered for term in ["news", "disclosure", "공시", "뉴스", "기사"]):
        return "뉴스·공시"
    if any(term in lowered for term in ["macro", "benchmark", "factor", "rate", "fx", "금리", "환율", "시장"]):
        return "외부 환경"
    if any(term in lowered for term in ["quality", "coverage", "신뢰도", "누락", "품질"]):
        return "데이터 신뢰도"
    if any(term in lowered for term in ["loss", "execution", "손실", "손익", "실행", "보유"]):
        return "손익·보유비중"
    return ""


def relation_axis_from_driver(driver: Dict[str, object]) -> str:
    category = str((driver or {}).get("category") or "").replace("_", "").replace("-", "").casefold()
    axis = RELATION_AXIS_CATEGORY_LABELS.get(category, "")
    if axis:
        return axis
    return relation_axis_from_rule(driver.get("ruleId") or driver.get("rule_id"), driver.get("label") or driver.get("summary"))


def relation_rule_score(item: Dict[str, object]) -> float:
    if not isinstance(item, dict):
        return 0.0
    return number(item.get("strengthScore") or item.get("strength_score") or item.get("score") or item.get("relationScore"))


def relation_axis_sort_key(axis: str, importance: float) -> tuple:
    try:
        order = RELATION_AXIS_ORDER.index(axis)
    except ValueError:
        order = len(RELATION_AXIS_ORDER)
    return (order, -float(importance or 0))


def relation_axis_line(axis: str, direction: object, summary: object) -> str:
    text = " ".join(str(summary or "").split())
    if not text:
        return ""
    text = text.replace(" -> ", " → ")
    if len(text) > 170:
        text = text[:167].rstrip() + "..."
    direction_label = RELATION_DIRECTION_LABELS.get(str(direction or "").strip(), "")
    prefix = axis + ((" · " + direction_label) if direction_label else "")
    return prefix + ": " + text


def relation_axis_summary_lines(context_or_metadata: Dict[str, object], limit: int = 5) -> List[str]:
    relation_context = ontology_relation_context(context_or_metadata)
    if not relation_context:
        return []
    execution_plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
    candidates: List[Dict[str, object]] = []
    drivers = execution_plan.get("decisionDrivers") if isinstance(execution_plan.get("decisionDrivers"), list) else []
    for item in drivers:
        if not isinstance(item, dict):
            continue
        axis = relation_axis_from_driver(item)
        summary = item.get("summary") or item.get("label")
        if axis and summary:
            candidates.append({
                "axis": axis,
                "direction": item.get("direction"),
                "summary": summary,
                "importance": number(item.get("importance")),
            })
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
    for item in rules:
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        axis = relation_axis_from_rule(rule_value(item, "ruleId", "rule_id"), rule_value(item, "label", "name"))
        label = rule_value(item, "label", "name", "ruleId", "rule_id")
        if axis and label:
            candidates.append({
                "axis": axis,
                "direction": rule_value(item, "direction", "polarity") or "neutral",
                "summary": label,
                "importance": relation_rule_score(item),
            })
    selected: Dict[str, Dict[str, object]] = {}
    for item in candidates:
        axis = str(item.get("axis") or "")
        current = selected.get(axis)
        if current is None or float(item.get("importance") or 0) > float(current.get("importance") or 0):
            selected[axis] = item
    rows: List[str] = []
    for item in sorted(selected.values(), key=lambda value: relation_axis_sort_key(str(value.get("axis") or ""), float(value.get("importance") or 0))):
        line = relation_axis_line(str(item.get("axis") or ""), item.get("direction"), item.get("summary"))
        if line:
            rows.append(line)
        if len(rows) >= limit:
            break
    return rows


def beginner_relation_decision_line(relation_context: Dict[str, object]) -> str:
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    action_group = str(decision.get("actionGroup") or "").strip()
    label = str(decision.get("label") or "").strip()
    if action_group == "executionRisk":
        return "쉽게 말하면: 이 점수는 팔아야 한다는 뜻이 아니라, 팔기로 결정했을 때 주문이 무리 없이 가능한지 보는 보조 확인입니다."
    if action_group in {"eventRisk", "disclosure"}:
        return "쉽게 말하면: 뉴스나 공시 때문에 보유 이유를 다시 확인하라는 뜻입니다. 이것만으로 매도 확정은 아닙니다."
    if action_group == "factorRisk":
        return "쉽게 말하면: 종목 자체 문제라기보다 시장 전체나 같은 테마가 흔들릴 때 같이 움직일 수 있는지 보라는 뜻입니다."
    if action_group == "lossControl":
        return "쉽게 말하면: 손실이나 가격 흐름 약화가 실제로 커져 비중을 줄일 기준을 확인하라는 뜻입니다."
    if action_group == "profitTake":
        return "쉽게 말하면: 수익을 지키기 위해 일부만 줄일지 확인하라는 뜻이지, 전량 매도 확정은 아닙니다."
    if action_group == "valuation":
        return "쉽게 말하면: 사용자가 정한 적정가와 현재가를 비교해 싼지 비싼지 확인하는 단계입니다. 이것만으로 매수나 매도 확정은 아닙니다."
    if "보유" in label or "관찰" in label:
        return "쉽게 말하면: 바로 행동하기보다 다음 데이터에서도 같은 신호가 유지되는지 보는 단계입니다."
    return ""


def relation_score_breakdown(context_or_metadata: Dict[str, object]) -> Dict[str, object]:
    relation_context = ontology_relation_context(context_or_metadata)
    if not relation_context:
        return {}
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    for candidate in [
        decision.get("scoreBreakdown") if isinstance(decision, dict) else {},
        relation_context.get("scoreBreakdown"),
    ]:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def relation_score_breakdown_line(context_or_metadata: Dict[str, object]) -> str:
    breakdown = relation_score_breakdown(context_or_metadata)
    if not breakdown:
        return ""
    parts = []
    labels = [
        ("위험 압력", "riskPressure", "점"),
        ("버티는 근거", "supportEvidence", "점"),
        ("데이터 확신", "dataConfidence", "%"),
        ("실행 필요도", "actionability", "점"),
        ("새 변화", "novelty", "점"),
    ]
    for label, key, unit in labels:
        value = breakdown.get(key)
        if value in (None, ""):
            continue
        parts.append(label + " " + format_score_value(value) + unit)
    if not parts:
        return ""
    return "점수 구성: " + ", ".join(parts)


def beginner_score_breakdown_line(context_or_metadata: Dict[str, object]) -> str:
    breakdown = relation_score_breakdown(context_or_metadata)
    if not breakdown:
        return ""
    risk = number(breakdown.get("riskPressure"))
    support = number(breakdown.get("supportEvidence"))
    confidence = number(breakdown.get("dataConfidence"))
    drivers = [str(item or "").strip() for item in breakdown.get("drivers") or [] if str(item or "").strip()]
    if risk and support:
        base = (
            "위험 쪽 근거는 "
            + format_score_value(risk)
            + "점이고, 버티는 근거는 "
            + format_score_value(support)
            + "점입니다."
        )
    elif risk:
        base = "위험 쪽 근거가 " + format_score_value(risk) + "점으로 더 크게 보입니다."
    elif support:
        base = "버티는 근거가 " + format_score_value(support) + "점으로 더 크게 보입니다."
    else:
        base = "아직 한쪽으로 강하게 기운 근거는 약합니다."
    if confidence:
        base += " 데이터 확신도는 " + format_score_value(confidence) + "%입니다."
    if drivers:
        base += " 특히 " + ", ".join(drivers[:3]) + "을 중요하게 봤습니다."
    return base


def beginner_rule_explanation(item: Dict[str, object]) -> str:
    text = " ".join(
        str(rule_value(item, "ruleId", "rule_id", "label", "relationType", "relation_type") or "").split()
    )
    lowered = text.casefold()
    if "execution.capacity" in lowered or "작은 실행 노출" in text or "실행 가능 용량" in text:
        return "쉬운 해석: 보유 수량이 작아, 나중에 팔기로 정해도 주문 자체는 어렵지 않다는 뜻입니다. 매도해야 한다는 뜻은 아닙니다."
    if "benchmark.beta" in lowered or "벤치마크 베타" in text:
        return "쉬운 해석: 애플 같은 대형주는 미국 증시와 같이 움직일 수 있으니 지수·금리도 같이 보라는 뜻입니다."
    if "disclosure.event_risk" in lowered or "공시" in text:
        return "쉬운 해석: 새 공시나 신고가 있어 내용을 확인하라는 뜻입니다. 가격 반응이 나쁘게 확인될 때만 경계 강도가 커집니다."
    if "news.direct" in lowered or "리스크 뉴스" in text or "위험 뉴스" in text:
        return "쉬운 해석: 직접 악재 뉴스가 있어 원문과 다음 가격 반응을 보라는 뜻입니다. 뉴스 하나만으로 매도 확정은 아닙니다."
    if "valuation.margin_of_safety" in lowered or "안전마진" in text or "저평가" in text:
        return "쉬운 해석: 현재가가 적정가보다 충분히 낮은지 보는 기준입니다. 싸 보이더라도 가격 흐름, 거래량, 매수/매도 압력을 확인하기 전에는 매수 확정이 아닙니다."
    if "valuation.negative_margin" in lowered or "고평가" in text or "적정가 대비 현재가 부담" in text:
        return "쉬운 해석: 현재가가 적정가보다 비싸 보이는지 확인하는 기준입니다. 이것만으로 바로 팔라는 뜻은 아닙니다."
    return ""


def ontology_rule_lines(context_or_metadata: Dict[str, object]) -> List[str]:
    relation_context = ontology_relation_context(context_or_metadata)
    if not relation_context:
        return []
    lines: List[str] = []
    strength = relation_context.get("signalStrength")
    label = str(relation_context.get("signalStrengthLabel") or "").strip()
    confidence = relation_context.get("confidence")
    if strength not in (None, ""):
        suffix = "신뢰도 " + format_score_value(confidence) if confidence not in (None, "") else ""
        lines.append("관계 신호: " + " ".join(part for part in [label + " (" + format_score_value(strength) + "점)", suffix] if part))
        lines.append("점수 해석: " + relation_score_meaning(float(strength)) + "입니다. 점수 상승은 대응 필요 강도 강화, 하락은 완화를 뜻하며 가격 방향 예측 점수가 아닙니다.")
    breakdown_line = relation_score_breakdown_line(context_or_metadata)
    if breakdown_line:
        lines.append(breakdown_line)
    easy_decision = beginner_relation_decision_line(relation_context)
    if easy_decision:
        lines.append(easy_decision)
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
    easy_rule_lines: List[str] = []
    for item in rules:
        if not isinstance(item, dict):
            continue
        if item.get("referenceOnly") or item.get("reference_only"):
            continue
        rule_label = str(rule_value(item, "label", "rule_id", "ruleId")).strip()
        score = rule_value(item, "strengthScore", "strength_score")
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence_text = ", ".join(str(value) for value in evidence[:3] if str(value or "").strip())
        value = rule_label
        if score not in (None, ""):
            value += " (" + format_score_value(score) + "점)"
        if evidence_text:
            value += " - " + evidence_text
        if value.strip():
            lines.append("성립 규칙: " + value)
        easy_rule = beginner_rule_explanation(item)
        if easy_rule and easy_rule not in easy_rule_lines:
            easy_rule_lines.append(easy_rule)
    lines.extend(easy_rule_lines[:3])
    prompt_context = ontology_prompt_context(context_or_metadata)
    prompt_id = str(prompt_context.get("promptId") or "").strip()
    if prompt_id:
        lines.append("AI 질문: " + prompt_id)
    return lines


def ai_prompt_lines(context_or_metadata: Dict[str, object]) -> List[str]:
    prompt_context = ontology_prompt_context(context_or_metadata)
    if not prompt_context:
        return []
    template = prompt_context.get("promptTemplate") if isinstance(prompt_context.get("promptTemplate"), dict) else {}
    label = str(template.get("label") or prompt_context.get("promptId") or "").strip()
    version = str(prompt_context.get("promptVersion") or template.get("version") or "").strip()
    lines = []
    if label:
        lines.append("프롬프트: " + label + ((" / " + version) if version else ""))
    guardrails = prompt_context.get("guardrails") if isinstance(prompt_context.get("guardrails"), list) else []
    if guardrails:
        lines.append("가드레일: " + " · ".join(str(item) for item in guardrails[:2] if str(item or "").strip()))
    return lines


def notification_ai_opinion_payload(context_or_metadata: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(context_or_metadata, dict):
        return {}
    opinion = context_or_metadata.get("notificationAiOpinion")
    if isinstance(opinion, dict) and opinion:
        return dict(opinion)
    metadata = context_or_metadata.get("metadata")
    if isinstance(metadata, dict):
        opinion = metadata.get("notificationAiOpinion")
        if isinstance(opinion, dict) and opinion:
            return dict(opinion)
    return {}


def notification_ai_opinion_lines(context_or_metadata: Dict[str, object]) -> List[str]:
    opinion = notification_ai_opinion_payload(context_or_metadata)
    lines = opinion.get("lines") if isinstance(opinion.get("lines"), list) else []
    return [str(line).strip() for line in lines if str(line or "").strip()]


def missing_data_lines(context_or_metadata: Dict[str, object]) -> List[str]:
    rows = ontology_missing_data(context_or_metadata)
    status_labels = {
        "missing": "수집 안 됨",
        "empty": "응답 비어 있음",
        "zero": "0값 수신",
        "proxy": "대체 근거 사용",
        "stale": "오래된 값",
        "latency": "지연/반복값",
        "stale-repeat": "반복 지연",
        "reference-only": "참고용",
        "unknown": "상태 미확인",
    }
    lines = []
    for item in rows:
        text = str(item.get("label") or "").strip()
        status = str(item.get("status") or "").strip()
        status_label = status_labels.get(status, "")
        if status_label:
            text += " (" + status_label + ")"
        effect = str(item.get("effect") or "").strip()
        if effect:
            text += ": " + effect
        lines.append(text)
    return lines


def block_from_lines(title: str, lines: List[str]) -> str:
    if not lines:
        return ""
    return title + "\n" + "\n".join(plain_bullet(line) for line in lines)


def telegram_block_from_lines(title: str, lines: List[str]) -> str:
    if not lines:
        return ""
    return "<b>" + html.escape(title, quote=False) + "</b>\n" + "\n".join(html_bullet(line) for line in lines)
