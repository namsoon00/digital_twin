import re
from typing import Dict, Iterable, List, Optional, Tuple

from .market_hours import evaluate_market_hours
from .message_types import INVESTMENT_INSIGHT, NEWS_DIGEST, SYSTEM_MESSAGE_TYPES
from .notification_ai_context import is_graph_backed_relation_context
from .ontology_relation_delivery import (
    relation_delivery_diff,
    relation_delivery_metadata,
)
from .ontology_decision_state import (
    CHANGE_STATES,
    CONFLICT_STATES,
    DATA_STATES,
    REVIEW_LEVEL_RANK,
    REVIEW_LEVELS,
    VALIDATION_STATES,
)
from .notification_rule_models import (
    DATA_QUALITY_REPEAT_BYPASS_IDS,
    DEFAULT_SIMILARITY_FIELDS,
    VOLATILE_VALUE_SUFFIX,
    NotificationRuleCondition,
    NotificationRuleConfig,
    NotificationRuleDecision,
    SimilarityBypassCondition,
    clamp_int,
    default_state_cooldown_minutes,
)
from .notifications import NotificationJob
from .notification_signal_classification import fallback_terms_for_delivery_condition


PROFIT_LOSS_DELIVERY_MESSAGE_TYPES = {"investmentInsight", "holdingTiming"}
PROFIT_LOSS_ACTION_GROUPS = {"lossControl", "profitTake"}
MATERIAL_SOURCE_EVENT_MARKERS = [
    ":news:",
    ":article:",
    ":rss:",
    ":disclosure:",
    ":dart:",
    ":filing:",
    ":sec:",
    ":externaldartdisclosure:",
]
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
INVESTMENT_STATE_GATED_MESSAGE_TYPES = {
    INVESTMENT_INSIGHT,
    "holdingTiming",
    "watchlistOntologySignal",
    "monitorDecisionChange",
    "monitorPositionChange",
    "monitorPnlChange",
    "monitorValueChange",
    "monitorTrendChange",
}
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


def nested_dict(value: object) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def notification_state_context(context: Dict[str, object]) -> Dict[str, str]:
    """Read the categorical decision contract without manufacturing a score."""

    context = context if isinstance(context, dict) else {}
    metadata = nested_dict(context.get("metadata"))
    relation_context = relation_context_from_notification_context(context)
    relation_state = nested_dict(relation_context.get("decisionState"))
    relation_decision = nested_dict(relation_context.get("decision"))
    insight = nested_dict(context.get("ontologyInsight")) or nested_dict(metadata.get("ontologyInsight"))
    validated = nested_dict(context.get("notificationAiValidatedResponse")) or nested_dict(
        metadata.get("notificationAiValidatedResponse")
    )
    opinion = nested_dict(context.get("activeInvestmentOpinion")) or nested_dict(
        metadata.get("activeInvestmentOpinion")
    )
    containers = [validated, insight, opinion, relation_state, relation_decision, relation_context, context, metadata]

    def first_known(key: str, allowed, fallback: str) -> str:
        for container in containers:
            value = str(container.get(key) or "").strip().lower()
            if value in allowed:
                return value
        return fallback

    review_level = first_known("reviewLevel", REVIEW_LEVELS, "observe")
    data_state = first_known("dataState", DATA_STATES, "partial")
    change_state = first_known("changeState", CHANGE_STATES, "unchanged")
    conflict_state = first_known("conflictState", CONFLICT_STATES, "context-only")
    validation_state = first_known("validationState", VALIDATION_STATES, "conditional")
    return {
        "reviewLevel": review_level,
        "dataState": data_state,
        "changeState": change_state,
        "conflictState": conflict_state,
        "validationState": validation_state,
    }


def graph_backed_notification(context: Dict[str, object]) -> bool:
    return is_graph_backed_relation_context(relation_context_from_notification_context(context or {}))


def material_evidence_present(context: Dict[str, object]) -> bool:
    context = context or {}
    insight = nested_dict(context.get("ontologyInsight"))
    change_state = str(insight.get("changeState") or context.get("changeState") or "").strip().lower()
    if change_state == "new-evidence":
        return True
    for field in (
        "sourceSignalTypes",
        "ontologyInsight.sourceEventKeys",
        "researchEvidence",
        "newsItems",
        "disclosures",
    ):
        if is_present(field_value(context, field)):
            return True
    return False


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


def typedb_action_groups(context: Dict[str, object]) -> List[str]:
    """Return action groups already materialized by TypeDB.

    Delivery may inspect these categorical groups to decide whether a real
    TypeDB change can bypass repetition.  It must not recreate the loss or
    profit thresholds from raw P&L values in Python.
    """

    relation_context = relation_context_from_notification_context(context or {})
    groups: List[str] = []
    queue: List[object] = [relation_context]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            value = str(current.get("actionGroup") or current.get("action_group") or "").strip()
            if value and value not in groups:
                groups.append(value)
            queue.extend(current.values())
        elif isinstance(current, (list, tuple)):
            queue.extend(current)
    return groups


def typedb_profit_loss_delivery_reason(
    job: NotificationJob = None,
    previous_context: Dict[str, object] = None,
    allow_without_previous: bool = True,
) -> str:
    if job is None:
        return ""
    if str(job.message_type or "") not in PROFIT_LOSS_DELIVERY_MESSAGE_TYPES:
        return ""
    context = job.context or {}
    if not graph_backed_notification(context):
        return ""
    state = notification_state_context(context)
    if state["reviewLevel"] == "blocked" or state["dataState"] in {"insufficient", "unavailable"}:
        return ""
    current_groups = set(typedb_action_groups(context))
    relevant_groups = current_groups.intersection(PROFIT_LOSS_ACTION_GROUPS)
    if not relevant_groups:
        return ""
    previous_context = previous_context or {}
    previous_groups = set(typedb_action_groups(previous_context))
    change_state = state["changeState"]
    if change_state == "new-condition" and (allow_without_previous or not previous_groups.intersection(PROFIT_LOSS_ACTION_GROUPS)):
        return "TypeDB 손익 관리 조건이 새로 성립"
    if change_state == "worsening" and "lossControl" in relevant_groups:
        return "TypeDB 손실 관리 조건과 손익 악화가 함께 확인"
    if change_state == "improving" and "profitTake" in relevant_groups:
        return "TypeDB 수익 보호 조건과 손익 개선이 함께 확인"
    return ""


def apply_typedb_profit_loss_delivery(
    decision: NotificationRuleDecision,
    job: NotificationJob = None,
    previous_context: Dict[str, object] = None,
    allow_without_previous: bool = True,
) -> bool:
    # 손익 구간은 반복·쿨다운만 우회할 수 있다. TypeDB, 데이터, AI 검증
    # 같은 필수 관문이 막힌 알림을 다시 살리면 ontology-first 계약이 깨진다.
    if not decision.should_send and decision.suppression_reason not in {"similar_repeat", "state_cooldown"}:
        return False
    reason = typedb_profit_loss_delivery_reason(
        job,
        previous_context=previous_context,
        allow_without_previous=allow_without_previous,
    )
    if not reason:
        return False
    decision.should_send = True
    decision.delivery_state = "send"
    decision.gate_state = "eligible"
    decision.gate_reason = reason
    decision.suppression_reason = ""
    decision.state_suppressed = False
    decision.state_decision = "typedb-profit-loss-change"
    decision.state_reason = reason
    decision.similarity_bypassed = True
    decision.similarity_bypass_reason = reason
    marker = "TypeDB 손익 조건 발송: " + reason
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


def notification_subject_group_key(job: NotificationJob) -> str:
    """Return the stable delivery subject before graph-state distinctions.

    Cooldown enforcement uses the richer state key below.  Graph-diff audit
    needs this coarser key so it can compare a new TypeDB rule or evidence
    path with the immediately preceding graph for the same account, subject,
    and notification intent.
    """

    if job is None:
        return ""
    message_type = str(job.message_type or "").strip()
    if message_type not in {"investmentInsight", "holdingTiming"}:
        return ""
    context = job.context or {}
    subject = first_normalized_field_value(context, [
        "ontologyInsight.subject",
        "activeInvestmentOpinion.subject",
        "ontologyRelationContext.subject.symbol",
        "symbol",
        "rawSymbol",
        "target",
    ])
    if not subject:
        return ""
    dispatch_type = first_normalized_field_value(context, [
        "ontologyInsight.dispatchInsightType",
        "dispatchInsightType",
        "ontologyInsight.insightType",
        "insightType",
    ])
    source_types = {
        normalized
        for item in flattened_strings(field_value(context, "sourceSignalTypes"))
        for normalized in normalize_similarity_list_items("sourceSignalTypes", item)
        if normalized
    }
    holding_group = (
        message_type == "holdingTiming"
        or dispatch_type == "holdingpositioncommon"
        or "holdingtiming" in source_types
        or normalize_fingerprint_part(field_value(context, "holdingPolicyGroup")) == "holdingpositioncommon"
    )
    state_group = "holdingPositionCommon" if holding_group else (dispatch_type or message_type)
    parts = [
        "state",
        message_type,
        normalize_fingerprint_part(job.account_id),
        subject,
        normalize_fingerprint_part(state_group),
    ]
    return "|".join(parts)


def notification_state_group_key(
    job: NotificationJob,
    include_relation_delivery: bool = True,
) -> str:
    subject_key = notification_subject_group_key(job)
    if not subject_key:
        return ""
    context = job.context or {}
    parts = [subject_key]
    relation_signature = ontology_relation_state_signature(context)
    if relation_signature:
        parts.append(relation_signature)
    relation_delivery = ontology_relation_delivery_metadata(context)
    if include_relation_delivery and relation_delivery.get("fingerprint"):
        # Price, P&L, timestamps, and generation ids are deliberately absent
        # from this key. A new group exists only when the graph's decision,
        # active rule/evidence set, or relation topology materially changes.
        parts.append("graph=" + str(relation_delivery["fingerprint"]))
    return "|".join(parts)


def ontology_relation_state_signature(context: Dict[str, object]) -> str:
    relation_context = relation_context_from_notification_context(context)
    if not relation_context:
        return ""
    timeline = relation_context.get("inferenceTimeline") if isinstance(relation_context.get("inferenceTimeline"), dict) else {}
    conflict = relation_context.get("signalConflicts") if isinstance(relation_context.get("signalConflicts"), dict) else {}
    why_now = relation_context.get("whyNow") if isinstance(relation_context.get("whyNow"), dict) else {}
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    parts: List[str] = []
    state_key = normalize_fingerprint_part(timeline.get("currentStateKey"))
    if state_key:
        parts.append("timeline=" + state_key)
    else:
        selected_rule = normalize_fingerprint_part(decision.get("selectedRuleId"))
        if selected_rule:
            parts.append("rule=" + selected_rule)
    conflict_type = normalize_fingerprint_part(conflict.get("conflictType"))
    if conflict_type and conflict_type != "none":
        parts.append("conflict=" + conflict_type)
    if why_now:
        parts.append("why=" + ("escalate" if bool(why_now.get("shouldEscalate")) else "repeat"))
    return "|".join(parts)


def ontology_relation_delivery_metadata(context: Dict[str, object]) -> Dict[str, object]:
    relation_context = relation_context_from_notification_context(context)
    return relation_delivery_metadata_for_context(relation_context, context)


def relation_delivery_metadata_for_context(
    relation_context: Dict[str, object],
    notification_context: Dict[str, object],
) -> Dict[str, object]:
    # A partial or legacy relation-shaped payload must retain the existing
    # cooldown behavior. Only a verified graph-backed inference can introduce
    # graph semantic novelty into delivery policy.
    if not is_graph_backed_relation_context(relation_context):
        return {}
    return relation_delivery_metadata(relation_context, notification_context)


def ontology_relation_delivery_diff(
    current_context: Dict[str, object],
    previous_context: Dict[str, object],
) -> Dict[str, object]:
    current_relation = relation_context_from_notification_context(current_context)
    previous_relation = relation_context_from_notification_context(previous_context)
    return relation_delivery_diff(
        current_relation,
        previous_relation,
        current_context,
        previous_context,
    )


def relation_context_from_notification_context(context: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(context, dict):
        return {}
    candidates = [
        context.get("ontologyRelationContext"),
        context.get("relationContext"),
        context.get("relationRuleContext"),
    ]
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    candidates.extend([
        metadata.get("ontologyRelationContext"),
        metadata.get("relationContext"),
        metadata.get("relationRuleContext"),
    ])
    ai_context = context.get("aiContext") if isinstance(context.get("aiContext"), dict) else {}
    candidates.append(ai_context.get("relationRuleContext"))
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


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
    fallback_terms = [normalized_text(term) for term in fallback_terms_for_delivery_condition(condition.condition_id)]
    return any(term in blob for term in fallback_terms if term)


def format_rule_number(value: float) -> str:
    text = ("%.4f" % value).rstrip("0").rstrip(".")
    return text or "0"


def normalize_similarity_list_item(field: str, value: object) -> str:
    normalized = normalize_fingerprint_part(value)
    if str(field or "").endswith("sourceEventKeys"):
        return VOLATILE_VALUE_SUFFIX.sub("", normalized)
    return normalized


def material_source_event_key(value: str) -> bool:
    normalized = normalized_text(value)
    if not normalized:
        return False
    return any(marker in normalized for marker in MATERIAL_SOURCE_EVENT_MARKERS)


def normalize_similarity_list_items(field: str, value: object) -> List[str]:
    normalized = normalize_similarity_list_item(field, value)
    if not normalized:
        return []
    if not str(field or "").endswith("sourceEventKeys"):
        return [normalized]
    parts = []
    for item in normalized.split("+"):
        key = VOLATILE_VALUE_SUFFIX.sub("", normalize_fingerprint_part(item))
        if key and material_source_event_key(key):
            parts.append(key)
    return parts


def similarity_bypass_match(
    condition: SimilarityBypassCondition,
    job: NotificationJob,
    previous_context: Dict[str, object],
    decision: NotificationRuleDecision,
) -> Tuple[bool, str]:
    context = job.context or {}
    condition_type = condition.condition_type
    label = condition.label or condition.condition_id or "반복 예외"
    field = condition.field or ""
    if condition.condition_id in DATA_QUALITY_REPEAT_BYPASS_IDS and is_data_quality_insight_context(context):
        return False, ""
    if condition_type == "severity_upgrade":
        target_field = field or "severity"
        current_rank = severity_rank(field_value(context, target_field))
        previous_rank = severity_rank(field_value(previous_context, target_field))
        if current_rank > previous_rank >= 0:
            return True, label + " " + str(field_value(previous_context, target_field) or "-") + " -> " + str(field_value(context, target_field) or "-")
        return False, ""
    if condition_type == "review_level_upgrade":
        target_field = field or "ontologyInsight.reviewLevel"
        current = str(field_value(context, target_field) or "").strip().lower()
        previous = str(field_value(previous_context, target_field) or "").strip().lower()
        # A blocked judgement means the graph or evidence is unusable.  It is
        # never an investment-severity escalation and must not bypass an
        # otherwise active cooldown.
        if current == "blocked" or previous == "blocked":
            return False, ""
        if current in REVIEW_LEVEL_RANK and previous in REVIEW_LEVEL_RANK:
            if REVIEW_LEVEL_RANK[current] > REVIEW_LEVEL_RANK[previous]:
                return True, label + " " + previous + " -> " + current
        return False, ""
    if condition_type == "field_changed":
        current = normalize_fingerprint_part(field_value(context, field))
        previous = normalize_fingerprint_part(field_value(previous_context, field))
        if current and previous and current != previous:
            return True, label + " " + previous + " -> " + current
        if current and not previous:
            return True, label + " 신규 " + current
        return False, ""
    if condition_type == "field_changed_existing":
        current = normalize_fingerprint_part(field_value(context, field))
        previous = normalize_fingerprint_part(field_value(previous_context, field))
        if current and previous and current != previous:
            return True, label + " " + previous + " -> " + current
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
        current_items = set()
        previous_items = set()
        for item in flattened_strings(field_value(context, field)):
            current_items.update(normalize_similarity_list_items(field, item))
        for item in flattened_strings(field_value(previous_context, field)):
            previous_items.update(normalize_similarity_list_items(field, item))
        new_items = sorted(current_items - previous_items)
        minimum = numeric_value(condition.value)
        if new_items and len(new_items) >= int(minimum or 1):
            return True, label + " " + ", ".join(new_items[:4])
        return False, ""
    if condition_type in {
        "abs_number_delta_gte",
        "number_delta_gte",
        "number_delta_lte",
        "number_multiplier_gte",
    }:
        # 가격·거래량 같은 실제 관측값만 반복 해제에 사용할 수 있다.
        # 이름에 score가 들어간 예전 합산 점수 필드는 명시적으로 거부한다.
        if "score" in str(field or "").strip().lower():
            return False, ""
        current = numeric_value(field_value(context, field))
        previous = numeric_value(field_value(previous_context, field))
        threshold = numeric_value(condition.value)
        if current is None or previous is None or threshold is None:
            return False, ""
        delta = current - previous
        if condition_type == "abs_number_delta_gte" and abs(delta) >= threshold:
            return True, label + " " + format_rule_number(previous) + " -> " + format_rule_number(current)
        if condition_type == "number_delta_gte" and delta >= threshold:
            return True, label + " " + format_rule_number(previous) + " -> " + format_rule_number(current)
        if condition_type == "number_delta_lte" and delta <= threshold:
            return True, label + " " + format_rule_number(previous) + " -> " + format_rule_number(current)
        if condition_type == "number_multiplier_gte" and previous != 0 and current / previous >= threshold:
            return True, label + " " + format_rule_number(previous) + " -> " + format_rule_number(current)
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
    if condition_type == "profit_loss_improved_gte":
        current = profit_loss_rate_from_context(context, job.text if job else "")
        previous = profit_loss_rate_from_context(previous_context)
        minimum = numeric_value(condition.value)
        if current is None or previous is None or minimum is None:
            return False, ""
        delta = current - previous
        if delta >= minimum:
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
    if condition_type == "ma60_crossed_above":
        current = ma60_distance_from_context(context, job.text if job else "")
        previous = ma60_distance_from_context(previous_context)
        threshold = numeric_value(condition.value)
        if threshold is None:
            threshold = 0.0
        if current is None or previous is None:
            return False, ""
        if previous < threshold and current >= threshold:
            return True, label + " " + format_rule_number(previous) + "% -> " + format_rule_number(current) + "%"
        return False, ""
    return False, ""


def evaluate_notification_rule(job: NotificationJob, config: NotificationRuleConfig) -> NotificationRuleDecision:
    state = notification_state_context(job.context or {})
    reasons: List[str] = []
    matched_conditions: List[str] = []
    blob = job_search_blob(job)
    for condition in config.conditions:
        if not condition.enabled:
            continue
        if condition_matches(condition, job, blob):
            matched_conditions.append(condition.condition_id)
            reasons.append(condition.label)

    message_type = str(job.message_type or config.message_type or "")
    decision = NotificationRuleDecision(
        message_type=job.message_type or config.message_type,
        enabled=bool(config.enabled),
        should_send=True,
        delivery_state="send",
        gate_state="eligible",
        gate_reason="발송 가능한 이벤트입니다.",
        review_level=state["reviewLevel"],
        data_state=state["dataState"],
        change_state=state["changeState"],
        conflict_state=state["conflictState"],
        validation_state=state["validationState"],
        reasons=reasons,
        matched_conditions=matched_conditions,
        fingerprint=notification_fingerprint(job, config),
        similarity_enabled=bool(config.similarity_enabled),
        similarity_window_minutes=int(config.similarity_window_minutes or 0),
    )
    if not config.enabled:
        decision.mark_bypass("발송 필터가 꺼져 있어 이벤트를 그대로 보냅니다.")
        return decision
    if not str(job.text or "").strip():
        decision.mark_suppressed("empty_body", "알림 본문이 비어 있어 보내지 않습니다.")
        return decision
    if message_type in SYSTEM_MESSAGE_TYPES:
        decision.gate_reason = "연결·운영 상태 알림은 투자 판단과 분리해 보냅니다."
        return decision
    if message_type == NEWS_DIGEST:
        decision.gate_reason = "새 기사 근거가 선별되어 뉴스 알림을 보냅니다."
        return decision
    if is_data_quality_insight_context(job.context or {}):
        decision.gate_state = "conditional"
        decision.gate_reason = "투자 의견이 아니라 자료 상태 경고이므로 별도로 보냅니다."
        return decision
    if message_type in INVESTMENT_STATE_GATED_MESSAGE_TYPES:
        if not graph_backed_notification(job.context or {}):
            decision.mark_suppressed("missing_graph_inference", "TypeDB 추론 근거가 없어 투자 판단 알림을 보내지 않습니다.")
            return decision
        if state["dataState"] in {"insufficient", "unavailable"}:
            decision.mark_suppressed("insufficient_data", "핵심 자료가 부족하거나 사용할 수 없어 투자 판단 알림을 보내지 않습니다.")
            return decision
        if state["validationState"] == "blocked" or state["reviewLevel"] == "blocked":
            decision.mark_suppressed("validation_blocked", "AI 검증 또는 관계 판단이 보류 상태라 투자 판단 알림을 보내지 않습니다.")
            return decision
        if (
            state["reviewLevel"] == "normal"
            and state["changeState"] == "unchanged"
            and not material_evidence_present(job.context or {})
        ):
            decision.mark_suppressed("unchanged_normal_state", "평소 상태가 그대로이고 새 근거가 없어 반복 알림을 보내지 않습니다.")
            return decision
        if state["dataState"] == "partial" or state["validationState"] == "conditional":
            decision.gate_state = "conditional"
            decision.gate_reason = "일부 자료가 비어 있어 한계를 함께 표시한 조건부 알림입니다."
        else:
            decision.gate_reason = "TypeDB 관계 판단과 AI 검증을 통과한 변화입니다."
        return decision
    if "status_noise" in matched_conditions and not set(matched_conditions).intersection(
        {"severity_alert", "severity_watch", "important_terms", "confirming_data", "actionable_terms"}
    ):
        decision.mark_suppressed("status_noise", "새 변화 없이 상태 설명만 반복되어 보내지 않습니다.")
        return decision
    decision.gate_reason = "해당 이벤트의 직접 발송 조건이 성립했습니다."
    return decision


def apply_similarity_rule(
    decision: NotificationRuleDecision,
    config: NotificationRuleConfig,
    recent_count: int,
    previous_context: Dict[str, object] = None,
    job: NotificationJob = None,
) -> NotificationRuleDecision:
    decision.similarity_recent_count = max(0, int(recent_count or 0))
    previous_context = previous_context or {}
    if config.enabled and apply_typedb_profit_loss_delivery(
        decision,
        job,
        previous_context=previous_context,
        allow_without_previous=decision.similarity_recent_count <= 0,
    ):
        return decision
    if not decision.should_send or decision.suppression_reason == "state_cooldown" or decision.similarity_bypassed:
        return decision
    if not config.enabled or not config.similarity_enabled or decision.similarity_recent_count <= 0:
        return decision
    if job is not None:
        for condition in config.similarity_bypass_conditions or []:
            if not condition.enabled:
                continue
            matched, reason = similarity_bypass_match(
                condition,
                job=job,
                previous_context=previous_context,
                decision=decision,
            )
            if matched:
                decision.similarity_bypassed = True
                decision.similarity_bypass_reason = reason
                decision.reasons.append("반복 보류 해제: " + reason)
                return decision
    reason = "같은 내용이 " + str(config.similarity_window_minutes) + "분 안에 이미 발송되어 다시 보내지 않습니다."
    decision.mark_suppressed("similar_repeat", reason)
    decision.reasons.append("반복 정책: " + reason)
    return decision


def apply_state_cooldown_rule(
    decision: NotificationRuleDecision,
    config: NotificationRuleConfig,
    sent_count: int,
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
    if apply_typedb_profit_loss_delivery(
        decision,
        job,
        previous_context=previous_context,
        allow_without_previous=decision.state_recent_sent_count <= 0,
    ):
        return decision
    if decision.state_recent_sent_count <= 0:
        decision.state_decision = "new-condition"
        decision.state_reason = "처음 확인된 상태"
        decision.reasons.append("상태 정책: " + decision.state_reason)
        return decision
    if job is not None:
        for condition in config.similarity_bypass_conditions or []:
            if not condition.enabled:
                continue
            matched, reason = similarity_bypass_match(
                condition,
                job=job,
                previous_context=previous_context,
                decision=decision,
            )
            if matched:
                decision.state_decision = "meaningful-change"
                if condition.condition_type in {"profit_loss_improved_gte", "ma60_crossed_above"}:
                    change_label = "의미 있는 회복"
                else:
                    change_label = "의미 있는 변화"
                decision.state_reason = change_label + ": " + reason
                decision.similarity_bypassed = True
                decision.similarity_bypass_reason = reason
                decision.reasons.append("상태 정책: " + decision.state_reason)
                return decision
    if decision.state_cooldown_minutes and decision.state_last_sent_age_minutes >= decision.state_cooldown_minutes:
        decision.state_decision = "scheduled-summary"
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
    decision.delivery_state = "suppressed"
    decision.gate_state = "blocked"
    decision.gate_reason = decision.state_reason
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
        reason_code = "market_closed" if market_decision.status == "closed" else "market_hours"
        decision.mark_suppressed(reason_code, market_decision.reason or "장 운영 시간 정책으로 발송을 보류합니다.")
    return decision
