import re
from typing import Dict, Iterable, List, Optional, Tuple

from .market_hours import evaluate_market_hours
from .notification_rule_models import (
    DATA_QUALITY_REPEAT_BYPASS_IDS,
    DEFAULT_LOW_SCORE_ACTION,
    DEFAULT_NOTIFICATION_SCORE_FORMULA,
    DEFAULT_SIMILARITY_FIELDS,
    FORMULA_VARIABLE_BY_CONDITION_ID,
    VOLATILE_SCORE_SUFFIX,
    NotificationRuleCondition,
    NotificationRuleConfig,
    NotificationRuleDecision,
    SimilarityBypassCondition,
    clamp_int,
    default_base_score,
    default_similarity_penalty,
    default_state_cooldown_minutes,
    default_threshold,
)
from .notifications import NotificationJob
from .scoring import fallback_terms_for_condition
from .strategy import SafeFormula


MANDATORY_PROFIT_LOSS_MESSAGE_TYPES = {"investmentInsight", "holdingTiming"}
MANDATORY_LOSS_RATE_THRESHOLD = -15.0
MANDATORY_PROFIT_RATE_THRESHOLD = 20.0
MANDATORY_PROFIT_LOSS_REPEAT_DELTA_PCT = 1.0
MANDATORY_LOSS_BANDS = [-15.0, -20.0, -30.0]
MANDATORY_PROFIT_BANDS = [20.0, 30.0, 50.0]
PROFIT_LOSS_FIELD_CANDIDATES = [
    "profitLossRate",
    "profit_loss_rate",
    "pnlRate",
    "pnl_rate",
    "position.profitLossRate",
    "position.profit_loss_rate",
    "portfolioPosition.profitLossRate",
    "holding.profitLossRate",
    "facts.profitLossRate",
    "ontologyInsight.facts.profitLossRate",
    "ontologyInsight.legacyModel.profitLossRate",
    "ontologyInsight.legacy_model.profitLossRate",
    "activeInvestmentOpinion.facts.profitLossRate",
    "activeInvestmentOpinion.legacyModel.profitLossRate",
    "activeInvestmentOpinion.legacy_model.profitLossRate",
    "ontologyRelationContext.facts.profitLossRate",
    "relationContext.facts.profitLossRate",
]
PROFIT_LOSS_TEXT_FIELDS = ["rawLines", "body", "summary", "currentStatus", "currentSituation"]
PROFIT_LOSS_TEXT_PATTERN = re.compile(
    r"(?:수익률|손익률|손익)\s*(?:[:：]|은|이|약)?\s*([+-]?\d+(?:\.\d+)?)\s*%"
)
MA60_DISTANCE_FIELD_CANDIDATES = [
    "ma60Distance",
    "ma60_distance",
    "facts.ma60Distance",
    "ontologyInsight.facts.ma60Distance",
    "activeInvestmentOpinion.facts.ma60Distance",
    "ontologyRelationContext.facts.ma60Distance",
    "relationContext.facts.ma60Distance",
]
MA60_PAREN_PATTERN = re.compile(r"60일[^\n]*?\(([+-]?\d+(?:\.\d+)?)\s*%\)")
MA60_TEXT_PATTERN = re.compile(
    r"60일[^\n]*?([+-]?\d+(?:\.\d+)?)\s*%\s*(낮음|아래|하회|높음|위|상회)"
)
ACTION_FIELD_CANDIDATES = [
    "actionLabel",
    "action",
    "activeInvestmentOpinion.actionLabel",
    "activeInvestmentOpinion.action",
    "aiOpinion.actionLabel",
    "aiOpinion.action",
    "decision.actionLabel",
    "decision.action",
    "ontologyInsight.actionLabel",
    "ontologyInsight.action",
    "holdingDecision",
    "holdingAction",
]


def flattened_strings(value) -> Iterable[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(str(key))
            parts.extend(flattened_strings(item))
        return parts
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            parts.extend(flattened_strings(item))
        return parts
    return [str(value)]


def normalized_text(value) -> str:
    return " ".join(str(value or "").lower().split())


def field_value(context: Dict[str, object], field: str):
    if str(field or "") == "ontologyInsight.dispatchInsightType":
        insight = context.get("ontologyInsight") if isinstance(context, dict) else {}
        if isinstance(insight, dict):
            value = insight.get("dispatchInsightType")
            if value not in (None, ""):
                return value
            return insight.get("insightType", "")
    current = context
    for part in [item for item in str(field or "").split(".") if item]:
        if isinstance(current, dict) and part in current:
            current = current.get(part)
        else:
            return ""
    return current


def is_data_quality_insight_context(context: Dict[str, object]) -> bool:
    insight = context.get("ontologyInsight") if isinstance(context, dict) else {}
    if not isinstance(insight, dict):
        return False
    insight_type = str(insight.get("dispatchInsightType") or insight.get("insightType") or "").strip()
    return insight_type == "dataQualityWarning"


def is_present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def numeric_value(value):
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def profit_loss_rate_from_text(value: object) -> Optional[float]:
    text = str(value or "")
    if not text.strip():
        return None
    match = PROFIT_LOSS_TEXT_PATTERN.search(text)
    if not match:
        return None
    return numeric_value(match.group(1))


def profit_loss_rate_from_context(context: Dict[str, object], text: str = "") -> Optional[float]:
    context = context or {}
    for field in PROFIT_LOSS_FIELD_CANDIDATES:
        value = numeric_value(field_value(context, field))
        if value is not None:
            return value
    for field in PROFIT_LOSS_TEXT_FIELDS:
        field_text = " ".join(flattened_strings(field_value(context, field)))
        value = profit_loss_rate_from_text(field_text)
        if value is not None:
            return value
    value = profit_loss_rate_from_text(text)
    if value is not None:
        return value
    return None

def attach_previous_profit_loss_context(
    decision: NotificationRuleDecision,
    job: NotificationJob,
    previous_context: Dict[str, object] = None,
) -> NotificationRuleDecision:
    previous_context = previous_context or {}
    if job is None or not previous_context:
        return decision
    current = profit_loss_rate_from_context(job.context or {}, job.text if job else "")
    previous = profit_loss_rate_from_context(previous_context)
    if previous is None:
        return decision
    decision.previous_profit_loss_rate = round(float(previous), 2)
    if current is not None:
        decision.profit_loss_rate_delta_pct = round(float(current) - float(previous), 2)
    return decision


def ma60_distance_from_text(value: object) -> Optional[float]:
    text = str(value or "")
    if not text.strip():
        return None
    paren_match = MA60_PAREN_PATTERN.search(text)
    if paren_match:
        return numeric_value(paren_match.group(1))
    match = MA60_TEXT_PATTERN.search(text)
    if not match:
        return None
    value = numeric_value(match.group(1))
    if value is None:
        return None
    direction = str(match.group(2) or "")
    if any(term in direction for term in ["낮", "아래", "하회"]):
        return -abs(value)
    return abs(value)


def ma60_distance_from_context(context: Dict[str, object], text: str = "") -> Optional[float]:
    context = context or {}
    for field in MA60_DISTANCE_FIELD_CANDIDATES:
        value = numeric_value(field_value(context, field))
        if value is not None:
            return value
    for field in PROFIT_LOSS_TEXT_FIELDS:
        field_text = " ".join(flattened_strings(field_value(context, field)))
        value = ma60_distance_from_text(field_text)
        if value is not None:
            return value
    value = ma60_distance_from_text(text)
    if value is not None:
        return value
    return None


def candidate_fields(value: str, fallback: List[str]) -> List[str]:
    fields = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return fields or list(fallback)


def first_normalized_field_value(context: Dict[str, object], fields: List[str]) -> str:
    for field in fields:
        value = normalize_fingerprint_part(field_value(context or {}, field))
        if value:
            return value
    return ""


def format_profit_loss_percent(value: float) -> str:
    prefix = "+" if float(value or 0) > 0 else ""
    return prefix + format_rule_number(value) + "%"


def mandatory_loss_band_rank(value: float) -> int:
    return len([threshold for threshold in MANDATORY_LOSS_BANDS if float(value or 0) <= threshold])


def mandatory_profit_band_rank(value: float) -> int:
    return len([threshold for threshold in MANDATORY_PROFIT_BANDS if float(value or 0) >= threshold])


def mandatory_profit_loss_delivery_reason(job: NotificationJob = None, previous_context: Dict[str, object] = None) -> str:
    if job is None:
        return ""
    if str(job.message_type or "") not in MANDATORY_PROFIT_LOSS_MESSAGE_TYPES:
        return ""
    profit_loss_rate = profit_loss_rate_from_context(job.context or {}, job.text or "")
    if profit_loss_rate is None:
        return ""
    previous_rate = profit_loss_rate_from_context(previous_context or {})
    if profit_loss_rate <= MANDATORY_LOSS_RATE_THRESHOLD:
        current_text = format_profit_loss_percent(profit_loss_rate)
        if previous_rate is not None:
            previous_text = format_profit_loss_percent(previous_rate)
            if previous_rate > MANDATORY_LOSS_RATE_THRESHOLD:
                return "손실률 " + previous_text + " -> " + current_text + "로 필수 발송 구간에 신규 진입"
            if mandatory_loss_band_rank(profit_loss_rate) > mandatory_loss_band_rank(previous_rate):
                return "손실률 " + previous_text + " -> " + current_text + "로 더 깊은 손실 구간 진입"
            delta = float(profit_loss_rate) - float(previous_rate)
            if delta <= -MANDATORY_PROFIT_LOSS_REPEAT_DELTA_PCT:
                return "손실률 추가 악화 " + previous_text + " -> " + current_text
            return ""
        return (
            "손실률 "
            + current_text
            + "가 필수 발송 구간("
            + format_rule_number(MANDATORY_LOSS_RATE_THRESHOLD)
            + "% 이하)에 있음"
        )
    if profit_loss_rate >= MANDATORY_PROFIT_RATE_THRESHOLD:
        current_text = format_profit_loss_percent(profit_loss_rate)
        if previous_rate is not None:
            previous_text = format_profit_loss_percent(previous_rate)
            if previous_rate < MANDATORY_PROFIT_RATE_THRESHOLD:
                return "수익률 " + previous_text + " -> " + current_text + "로 필수 발송 구간에 신규 진입"
            if mandatory_profit_band_rank(profit_loss_rate) > mandatory_profit_band_rank(previous_rate):
                return "수익률 " + previous_text + " -> " + current_text + "로 더 높은 수익 구간 진입"
            delta = float(profit_loss_rate) - float(previous_rate)
            if delta >= MANDATORY_PROFIT_LOSS_REPEAT_DELTA_PCT:
                return "수익률 추가 개선 " + previous_text + " -> " + current_text
            return ""
        return (
            "수익률 "
            + current_text
            + "가 필수 발송 구간(+"
            + format_rule_number(MANDATORY_PROFIT_RATE_THRESHOLD)
            + "% 이상)에 있음"
        )
    return ""


def apply_mandatory_profit_loss_delivery(
    decision: NotificationRuleDecision,
    job: NotificationJob = None,
    previous_context: Dict[str, object] = None,
) -> bool:
    reason = mandatory_profit_loss_delivery_reason(job, previous_context=previous_context)
    if not reason:
        return False
    decision.should_send = True
    decision.suppression_reason = ""
    decision.state_suppressed = False
    decision.state_decision = "mandatory_profit_loss_band"
    decision.state_reason = reason
    decision.similarity_bypassed = True
    decision.similarity_bypass_reason = reason
    marker = "손익 필수 발송: " + reason
    if marker not in decision.reasons:
        decision.reasons.append(marker)
    return True


def severity_rank(value) -> int:
    normalized = normalized_text(value).upper()
    ranks = {
        "INFO": 0,
        "정보": 0,
        "WATCH": 1,
        "관찰": 1,
        "ALERT": 2,
        "주의": 2,
        "WARNING": 2,
        "CRITICAL": 3,
        "위험": 3,
    }
    return ranks.get(normalized, -1)


def fingerprint_field_value(job: NotificationJob, field: str):
    if field == "messageType":
        return job.message_type
    if field == "accountId":
        return job.account_id
    if field == "accountLabel":
        return job.account_label
    if field == "body" or field == "text":
        return job.text
    return field_value(job.context or {}, field)


def normalize_fingerprint_part(value) -> str:
    if isinstance(value, (list, tuple, set)):
        return normalized_text(" ".join(str(item or "") for item in value))
    if isinstance(value, dict):
        return normalized_text(" ".join(str(item or "") for item in flattened_strings(value)))
    return normalized_text(value)


def notification_fingerprint(job: NotificationJob, config: NotificationRuleConfig) -> str:
    parts = []
    for field in config.similarity_fields or DEFAULT_SIMILARITY_FIELDS:
        normalized = normalize_fingerprint_part(fingerprint_field_value(job, field))
        parts.append(str(field) + "=" + normalized)
    return "|".join(parts)


def job_search_blob(job: NotificationJob) -> str:
    parts = [
        job.text,
        job.message_type,
        job.account_id,
        job.account_label,
    ]
    parts.extend(flattened_strings(job.context or {}))
    return normalized_text(" ".join(str(part or "") for part in parts))


def condition_matches(condition: NotificationRuleCondition, job: NotificationJob, blob: str) -> bool:
    context = job.context or {}
    condition_type = condition.condition_type
    terms = [normalized_text(term) for term in condition.terms if normalized_text(term)]
    if condition_type == "always":
        return True
    if condition_type == "text_contains_any":
        return any(term in blob for term in terms)
    if condition_type == "context_contains_any":
        value = normalized_text(" ".join(flattened_strings(field_value(context, condition.field))))
        if any(term in value for term in terms):
            return True
        return default_signal_fallback_matches(condition, blob, context)
    if condition_type == "context_equals":
        return normalized_text(field_value(context, condition.field)) == normalized_text(condition.value)
    if condition_type == "context_present":
        return is_present(field_value(context, condition.field))
    if condition_type == "context_number_gte":
        left = numeric_value(field_value(context, condition.field))
        right = numeric_value(condition.value)
        return left is not None and right is not None and left >= right
    if condition_type == "context_number_lte":
        left = numeric_value(field_value(context, condition.field))
        right = numeric_value(condition.value)
        return left is not None and right is not None and left <= right
    return False


def default_signal_fallback_matches(condition: NotificationRuleCondition, blob: str, context: Dict[str, object]) -> bool:
    if is_present(field_value(context, condition.field)):
        return False
    fallback_terms = [normalized_text(term) for term in fallback_terms_for_condition(condition.condition_id)]
    return any(term in blob for term in fallback_terms if term)


def format_rule_number(value: float) -> str:
    text = ("%.4f" % value).rstrip("0").rstrip(".")
    return text or "0"


def normalize_similarity_list_item(field: str, value: object) -> str:
    normalized = normalize_fingerprint_part(value)
    if str(field or "").endswith("sourceEventKeys"):
        return VOLATILE_SCORE_SUFFIX.sub("", normalized)
    return normalized


def similarity_bypass_match(
    condition: SimilarityBypassCondition,
    job: NotificationJob,
    previous_context: Dict[str, object],
    decision: NotificationRuleDecision,
    previous_score: int,
) -> Tuple[bool, str]:
    context = job.context or {}
    condition_type = condition.condition_type
    label = condition.label or condition.condition_id or "반복 예외"
    field = condition.field or ""
    if condition.condition_id in DATA_QUALITY_REPEAT_BYPASS_IDS and is_data_quality_insight_context(context):
        return False, ""
    if condition_type == "score_delta_gte":
        current = int(decision.score or 0)
        previous = int(previous_score or 0)
        minimum = numeric_value(condition.value)
        if minimum is not None and previous and current - previous >= minimum:
            return True, label + " +" + str(current - previous) + "점"
        return False, ""
    if condition_type == "severity_upgrade":
        target_field = field or "severity"
        current_rank = severity_rank(field_value(context, target_field))
        previous_rank = severity_rank(field_value(previous_context, target_field))
        if current_rank > previous_rank >= 0:
            return True, label + " " + str(field_value(previous_context, target_field) or "-") + " -> " + str(field_value(context, target_field) or "-")
        return False, ""
    if condition_type == "field_changed":
        current = normalize_fingerprint_part(field_value(context, field))
        previous = normalize_fingerprint_part(field_value(previous_context, field))
        if current and previous and current != previous:
            return True, label + " " + previous + " -> " + current
        if current and not previous:
            return True, label + " 신규 " + current
        return False, ""
    if condition_type == "field_changed_any":
        fields = candidate_fields(field, ACTION_FIELD_CANDIDATES)
        current = first_normalized_field_value(context, fields)
        previous = first_normalized_field_value(previous_context, fields)
        if current and previous and current != previous:
            return True, label + " " + previous + " -> " + current
        if current and not previous:
            return True, label + " 신규 " + current
        return False, ""
    if condition_type == "list_new_items_gte":
        current_items = set(normalize_similarity_list_item(field, item) for item in flattened_strings(field_value(context, field)) if normalize_similarity_list_item(field, item))
        previous_items = set(normalize_similarity_list_item(field, item) for item in flattened_strings(field_value(previous_context, field)) if normalize_similarity_list_item(field, item))
        new_items = sorted(current_items - previous_items)
        minimum = numeric_value(condition.value)
        if new_items and len(new_items) >= int(minimum or 1):
            return True, label + " " + ", ".join(new_items[:4])
        return False, ""
    if condition_type == "profit_loss_worsened_lte":
        current = profit_loss_rate_from_context(context, job.text if job else "")
        previous = profit_loss_rate_from_context(previous_context)
        minimum = numeric_value(condition.value)
        if current is None or previous is None or minimum is None:
            return False, ""
        delta = current - previous
        if delta <= -minimum:
            return True, label + " " + format_rule_number(previous) + "% -> " + format_rule_number(current) + "%"
        return False, ""
    if condition_type == "ma60_crossed_below":
        current = ma60_distance_from_context(context, job.text if job else "")
        previous = ma60_distance_from_context(previous_context)
        threshold = numeric_value(condition.value)
        if threshold is None:
            threshold = 0.0
        if current is None or previous is None:
            return False, ""
        if previous >= threshold and current < threshold:
            return True, label + " " + format_rule_number(previous) + "% -> " + format_rule_number(current) + "%"
        return False, ""
    if condition_type in {"abs_number_delta_gte", "number_delta_gte", "number_delta_lte", "number_multiplier_gte"}:
        current = numeric_value(field_value(context, field))
        previous = numeric_value(field_value(previous_context, field))
        minimum = numeric_value(condition.value)
        if current is None or previous is None or minimum is None:
            return False, ""
        if condition_type == "abs_number_delta_gte":
            delta = abs(current) - abs(previous)
            if delta >= minimum:
                return True, label + " " + format_rule_number(previous) + " -> " + format_rule_number(current)
        if condition_type == "number_delta_gte":
            delta = current - previous
            if delta >= minimum:
                return True, label + " +" + format_rule_number(delta)
        if condition_type == "number_delta_lte":
            delta = current - previous
            if delta <= -minimum:
                return True, label + " " + format_rule_number(delta)
        if condition_type == "number_multiplier_gte" and previous > 0:
            multiplier = current / previous
            if multiplier >= minimum:
                return True, label + " x" + format_rule_number(multiplier)
    return False, ""


def evaluate_notification_rule(job: NotificationJob, config: NotificationRuleConfig) -> NotificationRuleDecision:
    base_score = clamp_int(config.base_score, 0, 100, default_base_score(config.message_type))
    score = base_score
    reasons = ["기본 " + str(score) + "점"]
    formula_variables = {
        "baseScore": float(base_score),
        "severityScore": 0.0,
        "symbolScore": 0.0,
        "importantScore": 0.0,
        "confirmingDataScore": 0.0,
        "actionableScore": 0.0,
        "bodyScore": 0.0,
        "noisePenalty": 0.0,
        "conditionScore": 0.0,
        "threshold": float(clamp_int(config.threshold, 0, 100, default_threshold(config.message_type))),
        "severityRank": float(severity_rank((job.context or {}).get("severity"))),
        "hasSymbol": 1.0 if is_present((job.context or {}).get("symbol")) else 0.0,
        "hasBody": 1.0 if is_present((job.context or {}).get("body")) else 0.0,
        "signalCount": float(len((job.context or {}).get("notificationSignals") or [])),
    }
    blob = job_search_blob(job)
    for condition in config.conditions:
        if not condition.enabled:
            continue
        if condition_matches(condition, job, blob):
            score += int(condition.score or 0)
            formula_variables["conditionScore"] += float(condition.score or 0)
            variable_name = FORMULA_VARIABLE_BY_CONDITION_ID.get(condition.condition_id)
            if variable_name:
                formula_variables[variable_name] += float(condition.score or 0)
            reasons.append(condition.label + " " + ("+" if condition.score >= 0 else "") + str(condition.score))
    threshold = clamp_int(config.threshold, 0, 100, default_threshold(config.message_type))
    raw_score = clamp_int(score, 0, 100, 0)
    formula_variables["rawScore"] = float(raw_score)
    score, formula_audit = notification_formula_score(job.context or {}, formula_variables, raw_score, reasons)
    action = config.low_score_action or DEFAULT_LOW_SCORE_ACTION
    should_send = True
    if config.enabled and action == "suppress":
        should_send = score >= threshold
    return NotificationRuleDecision(
        message_type=job.message_type or config.message_type,
        enabled=bool(config.enabled),
        score=score,
        threshold=threshold,
        should_send=bool(should_send),
        low_score_action=action,
        reasons=reasons,
        fingerprint=notification_fingerprint(job, config),
        similarity_enabled=bool(config.similarity_enabled),
        similarity_window_minutes=int(config.similarity_window_minutes or 0),
        similarity_penalty=int(config.similarity_penalty or 0),
        notification_formula_audit=formula_audit,
    )


def notification_formula_score(
    context: Dict[str, object],
    variables: Dict[str, float],
    raw_score: int,
    reasons: List[str],
) -> Tuple[int, Dict[str, object]]:
    formula_text = str(context.get("notificationScoreFormula") or "").strip()
    if not formula_text:
        formula_text = DEFAULT_NOTIFICATION_SCORE_FORMULA
    formula = None
    missing = []
    used_variables = {"rawScore": float(raw_score)}
    try:
        formula = SafeFormula(formula_text)
        used_names = formula.variable_names()
        missing = [name for name in used_names if name not in variables]
        used_variables = {
            name: round(float(variables.get(name, 0.0)), 4)
            for name in used_names
            if name in variables
        }
        score = clamp_int(formula.evaluate(variables), 0, 100, raw_score)
    except (SyntaxError, ValueError, NameError, ArithmeticError) as error:
        reasons.append("사용자 발송 공식 오류: " + str(error))
        score = raw_score
    else:
        if formula_text != DEFAULT_NOTIFICATION_SCORE_FORMULA:
            reasons.append("사용자 발송 공식 적용 " + str(score) + "점")
    audit = {
        "key": "notificationScoreFormula",
        "label": "알림 발송 공식",
        "expression": formula_text,
        "result": score,
        "variables": used_variables,
        "missing": missing,
        "note": "반복 알림 패널티나 장 시간 정책 적용 전의 공식 결과입니다.",
    }
    return score, audit


def apply_similarity_rule(
    decision: NotificationRuleDecision,
    config: NotificationRuleConfig,
    recent_count: int,
    previous_score: int = 0,
    previous_context: Dict[str, object] = None,
    job: NotificationJob = None,
) -> NotificationRuleDecision:
    decision.similarity_recent_count = max(0, int(recent_count or 0))
    decision.similarity_previous_score = max(0, int(previous_score or 0))
    previous_context = previous_context or {}
    if config.enabled and apply_mandatory_profit_loss_delivery(decision, job, previous_context=previous_context):
        return decision
    if decision.suppression_reason == "state_cooldown" or decision.similarity_bypassed:
        return decision
    if not config.enabled or not config.similarity_enabled or decision.similarity_recent_count <= 0:
        return decision
    if job is not None:
        for condition in config.similarity_bypass_conditions or []:
            if not condition.enabled:
                continue
            matched, reason = similarity_bypass_match(condition, job=job, previous_context=previous_context, decision=decision, previous_score=decision.similarity_previous_score)
            if matched:
                decision.similarity_bypassed = True
                decision.similarity_bypass_reason = reason
                decision.reasons.append("유사 메시지 예외: " + reason)
                return decision
    score_delta = decision.score - decision.similarity_previous_score if decision.similarity_previous_score else 0
    bypass_delta = int(config.similarity_bypass_score_delta or 0)
    if bypass_delta and decision.similarity_previous_score and score_delta >= bypass_delta:
        decision.similarity_bypassed = True
        decision.similarity_bypass_reason = "발송 우선도 +" + str(score_delta) + " 상승"
        decision.reasons.append("유사 메시지 예외: " + decision.similarity_bypass_reason)
        return decision
    penalty = clamp_int(config.similarity_penalty, -100, 0, default_similarity_penalty(config.message_type))
    if penalty:
        decision.score = clamp_int(decision.score + penalty, 0, 100, 0)
        decision.reasons.append("유사 메시지 " + str(config.similarity_window_minutes) + "분 내 반복 " + str(penalty))
    if config.low_score_action == "suppress":
        decision.should_send = decision.score >= decision.threshold
    return decision


def apply_state_cooldown_rule(
    decision: NotificationRuleDecision,
    config: NotificationRuleConfig,
    sent_count: int,
    previous_score: int = 0,
    previous_context: Dict[str, object] = None,
    last_sent_at: str = "",
    last_sent_age_minutes: int = 0,
    job: NotificationJob = None,
) -> NotificationRuleDecision:
    decision.state_cooldown_enabled = bool(config.state_cooldown_enabled)
    decision.state_cooldown_minutes = clamp_int(config.state_cooldown_minutes, 0, 10080, default_state_cooldown_minutes(config.message_type))
    decision.state_recent_sent_count = max(0, int(sent_count or 0))
    decision.state_last_sent_at = str(last_sent_at or "")
    decision.state_last_sent_age_minutes = max(0, int(last_sent_age_minutes or 0))
    if not config.enabled or not config.state_cooldown_enabled:
        decision.state_decision = "bypass"
        decision.state_reason = "상태 지속 억제 꺼짐"
        return decision
    if not decision.fingerprint:
        decision.state_decision = "unknown"
        decision.state_reason = "상태 fingerprint 없음"
        return decision
    previous_context = previous_context or {}
    if apply_mandatory_profit_loss_delivery(decision, job, previous_context=previous_context):
        return decision
    if decision.state_recent_sent_count <= 0:
        decision.state_decision = "new_threshold"
        decision.state_reason = "신규 임계값 상태"
        decision.reasons.append("상태 정책: " + decision.state_reason)
        return decision
    if job is not None:
        for condition in config.similarity_bypass_conditions or []:
            if not condition.enabled:
                continue
            matched, reason = similarity_bypass_match(condition, job=job, previous_context=previous_context, decision=decision, previous_score=previous_score)
            if matched:
                decision.state_decision = "material_change"
                decision.state_reason = "의미 있는 추가 확대: " + reason
                decision.similarity_bypassed = True
                decision.similarity_bypass_reason = reason
                decision.reasons.append("상태 정책: " + decision.state_reason)
                return decision
    if decision.state_cooldown_minutes and decision.state_last_sent_age_minutes >= decision.state_cooldown_minutes:
        decision.state_decision = "sustained_summary"
        decision.state_reason = "지속 상태 요약 " + str(decision.state_cooldown_minutes) + "분 경과"
        decision.similarity_bypassed = True
        decision.similarity_bypass_reason = decision.state_reason
        decision.reasons.append("상태 정책: " + decision.state_reason)
        return decision
    decision.state_decision = "cooldown"
    decision.state_suppressed = True
    decision.state_reason = (
        "같은 임계값 상태 지속: 마지막 발송 후 "
        + str(decision.state_last_sent_age_minutes)
        + "분, 쿨다운 "
        + str(decision.state_cooldown_minutes)
        + "분"
    )
    decision.should_send = False
    decision.suppression_reason = "state_cooldown"
    decision.reasons.append("상태 정책: " + decision.state_reason)
    return decision


def apply_market_hours_rule(
    decision: NotificationRuleDecision,
    config: NotificationRuleConfig,
    job: NotificationJob,
    now=None,
) -> NotificationRuleDecision:
    market_decision = evaluate_market_hours(
        job.message_type or config.message_type,
        job.context or {},
        bool(config.enabled and config.market_hours_enabled),
        list(config.market_hours_markets or []),
        now=now,
    )
    decision.market_hours_enabled = bool(config.market_hours_enabled)
    decision.market_hours_market = market_decision.market
    decision.market_hours_label = market_decision.label
    decision.market_hours_status = market_decision.status
    decision.market_hours_reason = market_decision.reason
    decision.market_hours_local_time = market_decision.local_time
    decision.market_hours_open_time = market_decision.open_time
    decision.market_hours_close_time = market_decision.close_time
    decision.market_hours_timezone = market_decision.timezone
    decision.market_hours_markets = list(config.market_hours_markets or [])
    if config.enabled and config.market_hours_enabled and market_decision.status in {"open", "closed", "closed_exception"}:
        decision.reasons.append("장 시간 " + market_decision.reason)
    if config.enabled and config.market_hours_enabled and not market_decision.should_send:
        decision.should_send = False
        decision.suppression_reason = "market_closed" if market_decision.status == "closed" else "market_hours"
    return decision
