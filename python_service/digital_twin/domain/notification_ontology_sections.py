import html
from typing import Dict, List

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
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
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
