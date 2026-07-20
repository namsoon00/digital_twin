import re
from dataclasses import dataclass, field as dataclass_field
from typing import Dict, List

from .market_hours import default_market_hours_enabled, default_market_hours_markets
from .message_types import (
    INVESTMENT_CALENDAR_REMINDER,
    INVESTMENT_INSIGHT,
    NEWS_DIGEST,
    SYSTEM_MESSAGE_TYPES,
    notification_message_types,
)
from .notification_templates import DEFAULT_NOTIFICATION_TEMPLATES


DEFAULT_SIMILARITY_FIELDS = ["messageType", "accountId", "symbol", "severity", "title"]
STATE_COOLDOWN_MESSAGE_TYPES = {
    INVESTMENT_INSIGHT,
    "holdingTiming",
    "watchlistOntologySignal",
    "monitorDecisionChange",
    "monitorPositionChange",
    "monitorPnlChange",
    "monitorValueChange",
    "monitorTrendChange",
    "monitorCashChange",
    "externalEquityMove",
    "externalCryptoMove",
    "externalMacroShift",
    "externalDartDisclosure",
}
VOLATILE_VALUE_SUFFIX = re.compile(r":[+-]?\d+(?:\.\d+)?%?$")
DATA_QUALITY_REPEAT_BYPASS_IDS = {"new_source_signal", "new_relation_event"}

DELIVERY_STATES = ("send", "suppressed", "bypass")
DELIVERY_GATE_STATES = ("eligible", "conditional", "blocked", "bypass")

CONDITION_TYPE_LABELS = [
    {"type": "text_contains_any", "label": "메시지에 단어 포함", "description": "본문이나 알림 정보에 지정 단어 중 하나가 있는지 확인합니다."},
    {"type": "context_contains_any", "label": "정보 필드에 단어 포함", "description": "선택한 정보 필드에 지정 단어 중 하나가 있는지 확인합니다."},
    {"type": "context_equals", "label": "정보 값 일치", "description": "선택한 정보 필드가 지정 값과 같은지 확인합니다."},
    {"type": "context_present", "label": "정보 값 존재", "description": "선택한 정보 필드가 비어 있지 않은지 확인합니다."},
    {"type": "context_number_gte", "label": "정보 숫자 이상", "description": "선택한 실제 수치가 기준 이상인지 확인합니다."},
    {"type": "context_number_lte", "label": "정보 숫자 이하", "description": "선택한 실제 수치가 기준 이하인지 확인합니다."},
    {"type": "always", "label": "항상 확인", "description": "규칙이 켜져 있으면 항상 확인 근거로 남깁니다."},
]


def clamp_int(value, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def bool_value(value, fallback: bool = True) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    return fallback


def default_rule_message_types() -> List[str]:
    return notification_message_types(list(DEFAULT_NOTIFICATION_TEMPLATES.keys()))


def default_similarity_enabled(message_type: str) -> bool:
    return str(message_type or "") not in SYSTEM_MESSAGE_TYPES


def default_similarity_window_minutes(message_type: str) -> int:
    key = str(message_type or "")
    if key in {NEWS_DIGEST, INVESTMENT_CALENDAR_REMINDER}:
        return 1440
    if key == INVESTMENT_INSIGHT or key in STATE_COOLDOWN_MESSAGE_TYPES or key == "monitorHeartbeat":
        return 360
    if key in {"monitorHeartbeat", "externalDataConnection"}:
        return 180
    return 120


def default_state_cooldown_enabled(message_type: str) -> bool:
    return str(message_type or "") in STATE_COOLDOWN_MESSAGE_TYPES


def default_state_cooldown_minutes(message_type: str) -> int:
    return 360 if default_state_cooldown_enabled(message_type) else 0


@dataclass
class NotificationRuleCondition:
    """A named observation used in delivery diagnostics, never a score input."""

    condition_id: str
    label: str
    condition_type: str
    field: str = ""
    value: object = ""
    terms: List[str] = dataclass_field(default_factory=list)
    enabled: bool = True

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "NotificationRuleCondition":
        raw_terms = payload.get("terms") if isinstance(payload, dict) else []
        if isinstance(raw_terms, str):
            terms = [item.strip() for item in raw_terms.split(",") if item.strip()]
        elif isinstance(raw_terms, list):
            terms = [str(item or "").strip() for item in raw_terms if str(item or "").strip()]
        else:
            terms = []
        return cls(
            condition_id=str(payload.get("id") or payload.get("conditionId") or payload.get("condition_id") or "").strip(),
            label=str(payload.get("label") or "").strip(),
            condition_type=str(payload.get("type") or payload.get("conditionType") or payload.get("condition_type") or "").strip(),
            field=str(payload.get("field") or "").strip(),
            value=payload.get("value", ""),
            terms=terms,
            enabled=payload.get("enabled") is not False,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.condition_id,
            "label": self.label,
            "type": self.condition_type,
            "field": self.field,
            "value": self.value,
            "terms": list(self.terms or []),
            "enabled": bool(self.enabled),
        }


@dataclass
class SimilarityBypassCondition:
    condition_id: str
    label: str
    condition_type: str
    field: str = ""
    value: object = ""
    enabled: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "SimilarityBypassCondition":
        return cls(
            condition_id=str(payload.get("id") or payload.get("conditionId") or payload.get("condition_id") or "").strip(),
            label=str(payload.get("label") or "").strip(),
            condition_type=str(payload.get("type") or payload.get("conditionType") or payload.get("condition_type") or "").strip(),
            field=str(payload.get("field") or "").strip(),
            value=payload.get("value", ""),
            enabled=payload.get("enabled") is not False,
            description=str(payload.get("description") or "").strip(),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.condition_id,
            "label": self.label,
            "type": self.condition_type,
            "field": self.field,
            "value": self.value,
            "enabled": bool(self.enabled),
            "description": self.description,
        }


def default_similarity_bypass_conditions(message_type: str) -> List[SimilarityBypassCondition]:
    key = str(message_type or "")
    if key == INVESTMENT_INSIGHT:
        return [
            SimilarityBypassCondition(
                "insight_severity_upgrade",
                "알림 단계 상승",
                "severity_upgrade",
                description="관찰에서 주의처럼 알림 단계가 올라가면 다시 보냅니다.",
            ),
            SimilarityBypassCondition(
                "review_level_upgrade",
                "확인 단계 상승",
                "review_level_upgrade",
                field="ontologyInsight.reviewLevel",
                description="관찰에서 대응 준비처럼 확인 단계가 올라가면 다시 보냅니다.",
            ),
            SimilarityBypassCondition(
                "new_source_signal",
                "새 근거 종류 추가",
                "list_new_items_gte",
                field="sourceSignalTypes",
                value=1,
                description="이전에 없던 근거 종류가 추가되면 다시 보냅니다.",
            ),
            SimilarityBypassCondition(
                "new_relation_event",
                "새 뉴스·공시 추가",
                "list_new_items_gte",
                field="ontologyInsight.sourceEventKeys",
                value=1,
                description="같은 판단이라도 새 뉴스·공시 원문이 추가되면 다시 보냅니다.",
            ),
            SimilarityBypassCondition(
                "insight_profit_loss_worsened",
                "손익률 추가 악화",
                "profit_loss_worsened_lte",
                value=1,
                description="이전 알림보다 손익률이 1%p 이상 나빠지면 다시 보냅니다.",
            ),
            SimilarityBypassCondition(
                "insight_profit_loss_improved",
                "손익률 개선",
                "profit_loss_improved_gte",
                value=1,
                description="이전 알림보다 손익률이 1%p 이상 좋아지면 다시 보냅니다.",
            ),
            SimilarityBypassCondition(
                "insight_ma60_crossed_below",
                "60일 평균 아래로 전환",
                "ma60_crossed_below",
                value=0,
                description="가격이 60일 평균 위에서 아래로 내려가면 다시 보냅니다.",
            ),
            SimilarityBypassCondition(
                "insight_ma60_crossed_above",
                "60일 평균 위로 회복",
                "ma60_crossed_above",
                value=0,
                description="가격이 60일 평균 아래에서 위로 회복하면 다시 보냅니다.",
            ),
            SimilarityBypassCondition(
                "insight_action_changed",
                "권장 대응 변경",
                "field_changed_any",
                field="notificationAiValidatedResponse.actionLabel,notificationAiValidatedResponse.action,aiOpinion.actionLabel,aiOpinion.action",
                description="검증된 최종 대응이 바뀌면 다시 보냅니다.",
            ),
        ]
    if key == "holdingTiming":
        return [
            SimilarityBypassCondition("severity_upgrade", "알림 단계 상승", "severity_upgrade"),
            SimilarityBypassCondition("loss_rate_worsened", "손익률 추가 악화", "profit_loss_worsened_lte", value=1),
            SimilarityBypassCondition("loss_rate_improved", "손익률 개선", "profit_loss_improved_gte", value=1),
            SimilarityBypassCondition("holding_ma60_crossed_below", "60일 평균 아래로 전환", "ma60_crossed_below", value=0),
            SimilarityBypassCondition("holding_ma60_crossed_above", "60일 평균 위로 회복", "ma60_crossed_above", value=0),
            SimilarityBypassCondition(
                "holding_action_changed",
                "권장 대응 변경",
                "field_changed_any",
                field="holdingDecision,holdingAction,actionLabel,activeInvestmentOpinion.actionLabel,activeInvestmentOpinion.action",
            ),
        ]
    return []


@dataclass
class NotificationRuleConfig:
    message_type: str
    enabled: bool = True
    conditions: List[NotificationRuleCondition] = dataclass_field(default_factory=list)
    similarity_enabled: bool = True
    similarity_window_minutes: int = 120
    similarity_bypass_conditions: List[SimilarityBypassCondition] = dataclass_field(default_factory=list)
    similarity_fields: List[str] = dataclass_field(default_factory=lambda: list(DEFAULT_SIMILARITY_FIELDS))
    state_cooldown_enabled: bool = False
    state_cooldown_minutes: int = 0
    market_hours_enabled: bool = False
    market_hours_markets: List[str] = dataclass_field(default_factory=list)
    updated_at: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "NotificationRuleConfig":
        payload = payload if isinstance(payload, dict) else {}
        message_type = str(payload.get("messageType") or payload.get("message_type") or "").strip()
        raw_conditions = payload.get("conditions") or []
        conditions = []
        if isinstance(raw_conditions, list):
            for item in raw_conditions:
                if isinstance(item, dict):
                    condition = NotificationRuleCondition.from_dict(item)
                    if condition.condition_id and condition.condition_type:
                        conditions.append(condition)

        bypass_key_present = "similarityBypassConditions" in payload or "similarity_bypass_conditions" in payload
        raw_bypass = payload.get("similarityBypassConditions", payload.get("similarity_bypass_conditions", []))
        bypass_conditions = []
        if isinstance(raw_bypass, list):
            for item in raw_bypass:
                if isinstance(item, dict):
                    condition = SimilarityBypassCondition.from_dict(item)
                    if condition.condition_id and condition.condition_type:
                        bypass_conditions.append(condition)
        if not bypass_key_present:
            bypass_conditions = default_similarity_bypass_conditions(message_type)

        raw_fields = payload.get("similarityFields", payload.get("similarity_fields"))
        if isinstance(raw_fields, str):
            similarity_fields = [item.strip() for item in raw_fields.split(",") if item.strip()]
        elif isinstance(raw_fields, list):
            similarity_fields = [str(item or "").strip() for item in raw_fields if str(item or "").strip()]
        else:
            similarity_fields = list(DEFAULT_SIMILARITY_FIELDS)

        raw_markets = payload.get("marketHoursMarkets", payload.get("market_hours_markets"))
        if isinstance(raw_markets, str):
            markets = [item.strip().upper() for item in raw_markets.split(",") if item.strip()]
        elif isinstance(raw_markets, list):
            markets = [str(item or "").strip().upper() for item in raw_markets if str(item or "").strip()]
        else:
            markets = default_market_hours_markets(message_type)

        return cls(
            message_type=message_type,
            enabled=bool_value(payload.get("enabled"), True),
            conditions=conditions,
            similarity_enabled=bool_value(
                payload.get("similarityEnabled", payload.get("similarity_enabled")),
                default_similarity_enabled(message_type),
            ),
            similarity_window_minutes=clamp_int(
                payload.get("similarityWindowMinutes", payload.get("similarity_window_minutes")),
                0,
                10080,
                default_similarity_window_minutes(message_type),
            ),
            similarity_bypass_conditions=bypass_conditions,
            similarity_fields=similarity_fields,
            state_cooldown_enabled=bool_value(
                payload.get("stateCooldownEnabled", payload.get("state_cooldown_enabled")),
                default_state_cooldown_enabled(message_type),
            ),
            state_cooldown_minutes=clamp_int(
                payload.get("stateCooldownMinutes", payload.get("state_cooldown_minutes")),
                0,
                10080,
                default_state_cooldown_minutes(message_type),
            ),
            market_hours_enabled=bool_value(
                payload.get("marketHoursEnabled", payload.get("market_hours_enabled")),
                default_market_hours_enabled(message_type),
            ),
            market_hours_markets=markets,
            updated_at=str(payload.get("updatedAt") or payload.get("updated_at") or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "messageType": self.message_type,
            "enabled": bool(self.enabled),
            "conditions": [condition.to_dict() for condition in self.conditions],
            "similarityEnabled": bool(self.similarity_enabled),
            "similarityWindowMinutes": int(self.similarity_window_minutes or 0),
            "similarityBypassConditions": [condition.to_dict() for condition in self.similarity_bypass_conditions],
            "similarityFields": list(self.similarity_fields or []),
            "stateCooldownEnabled": bool(self.state_cooldown_enabled),
            "stateCooldownMinutes": int(self.state_cooldown_minutes or 0),
            "marketHoursEnabled": bool(self.market_hours_enabled),
            "marketHoursMarkets": list(self.market_hours_markets or []),
            "updatedAt": self.updated_at,
        }


@dataclass
class NotificationRuleDecision:
    message_type: str
    enabled: bool
    should_send: bool
    delivery_state: str
    gate_state: str
    gate_reason: str
    review_level: str = "observe"
    data_state: str = "partial"
    change_state: str = "unchanged"
    conflict_state: str = "context-only"
    validation_state: str = "conditional"
    reasons: List[str] = dataclass_field(default_factory=list)
    matched_conditions: List[str] = dataclass_field(default_factory=list)
    fingerprint: str = ""
    similarity_enabled: bool = False
    similarity_window_minutes: int = 0
    similarity_recent_count: int = 0
    similarity_bypassed: bool = False
    similarity_bypass_reason: str = ""
    suppression_reason: str = ""
    state_cooldown_enabled: bool = False
    state_cooldown_minutes: int = 0
    state_recent_sent_count: int = 0
    state_last_sent_at: str = ""
    state_last_sent_age_minutes: int = 0
    state_decision: str = ""
    state_reason: str = ""
    state_suppressed: bool = False
    market_hours_enabled: bool = False
    market_hours_market: str = ""
    market_hours_label: str = ""
    market_hours_status: str = ""
    market_hours_reason: str = ""
    market_hours_local_time: str = ""
    market_hours_open_time: str = ""
    market_hours_close_time: str = ""
    market_hours_timezone: str = ""
    market_hours_markets: List[str] = dataclass_field(default_factory=list)
    previous_profit_loss_rate: object = None
    profit_loss_rate_delta_pct: object = None

    def mark_suppressed(self, reason_code: str, reason: str) -> None:
        self.should_send = False
        self.delivery_state = "suppressed"
        self.gate_state = "blocked"
        self.suppression_reason = reason_code
        self.gate_reason = reason

    def mark_bypass(self, reason: str) -> None:
        self.should_send = True
        self.delivery_state = "bypass"
        self.gate_state = "bypass"
        self.gate_reason = reason

    def to_context(self) -> Dict[str, object]:
        payload = {
            "deliveryDecision": self.delivery_state,
            "deliveryGateState": self.gate_state,
            "deliveryGateReason": self.gate_reason,
            "deliveryReasons": list(self.reasons or []),
            "deliveryMatchedConditions": list(self.matched_conditions or []),
            "deliveryRuleEnabled": bool(self.enabled),
            "deliveryFingerprint": self.fingerprint,
            "deliveryReviewLevel": self.review_level,
            "deliveryDataState": self.data_state,
            "deliveryChangeState": self.change_state,
            "deliveryConflictState": self.conflict_state,
            "deliveryValidationState": self.validation_state,
            "repeatFilterEnabled": bool(self.similarity_enabled),
            "repeatWindowMinutes": self.similarity_window_minutes,
            "repeatRecentCount": self.similarity_recent_count,
            "repeatBypassed": bool(self.similarity_bypassed),
            "repeatBypassReason": self.similarity_bypass_reason,
            "deliverySuppressionReason": self.suppression_reason,
            "cooldownEnabled": bool(self.state_cooldown_enabled),
            "cooldownMinutes": self.state_cooldown_minutes,
            "cooldownRecentSentCount": self.state_recent_sent_count,
            "cooldownLastSentAt": self.state_last_sent_at,
            "cooldownLastSentAgeMinutes": self.state_last_sent_age_minutes,
            "cooldownDecision": self.state_decision,
            "cooldownReason": self.state_reason,
            "cooldownSuppressed": bool(self.state_suppressed),
            "marketHoursEnabled": bool(self.market_hours_enabled),
            "marketHoursMarket": self.market_hours_market,
            "marketHoursLabel": self.market_hours_label,
            "marketHoursStatus": self.market_hours_status,
            "marketHoursDecision": "send" if self.market_hours_status != "closed" else "suppressed",
            "marketHoursReason": self.market_hours_reason,
            "marketHoursLocalTime": self.market_hours_local_time,
            "marketHoursOpenTime": self.market_hours_open_time,
            "marketHoursCloseTime": self.market_hours_close_time,
            "marketHoursTimezone": self.market_hours_timezone,
            "marketHoursMarkets": list(self.market_hours_markets or []),
        }
        if self.previous_profit_loss_rate is not None:
            payload["previousProfitLossRate"] = self.previous_profit_loss_rate
        if self.profit_loss_rate_delta_pct is not None:
            payload["profitLossRateDeltaPct"] = self.profit_loss_rate_delta_pct
        return payload


def default_conditions() -> List[NotificationRuleCondition]:
    return [
        NotificationRuleCondition("severity_alert", "주의 단계", "context_equals", field="severity", value="ALERT"),
        NotificationRuleCondition("severity_watch", "관찰 단계", "context_equals", field="severity", value="WATCH"),
        NotificationRuleCondition("has_symbol", "종목 지정", "context_present", field="symbol"),
        NotificationRuleCondition("important_terms", "중요 투자 내용", "context_contains_any", field="notificationSignals", terms=["important"]),
        NotificationRuleCondition("confirming_data", "확인 자료 포함", "context_contains_any", field="notificationSignals", terms=["confirmingData"]),
        NotificationRuleCondition("actionable_terms", "대응 확인 필요", "context_contains_any", field="notificationSignals", terms=["actionable"]),
        NotificationRuleCondition("body_present", "본문 있음", "context_present", field="body"),
        NotificationRuleCondition("status_noise", "상태성 반복 내용", "context_contains_any", field="notificationSignals", terms=["statusNoise"]),
    ]


def ontology_insight_conditions() -> List[NotificationRuleCondition]:
    return [
        NotificationRuleCondition("ontology_review_act", "대응 준비 단계", "context_equals", field="ontologyInsight.reviewLevel", value="act"),
        NotificationRuleCondition("ontology_review_immediate", "즉시 재확인 단계", "context_equals", field="ontologyInsight.reviewLevel", value="immediate"),
        NotificationRuleCondition("ontology_change", "관계 변화 있음", "context_present", field="ontologyInsight.changeState"),
        NotificationRuleCondition("ontology_source_signals", "근거 종류 있음", "context_present", field="sourceSignalTypes"),
    ]


def default_notification_rule(message_type: str) -> NotificationRuleConfig:
    key = str(message_type or "notification").strip() or "notification"
    conditions = [NotificationRuleCondition.from_dict(condition.to_dict()) for condition in default_conditions()]
    similarity_fields = list(DEFAULT_SIMILARITY_FIELDS)
    if key == INVESTMENT_INSIGHT:
        conditions.extend(NotificationRuleCondition.from_dict(condition.to_dict()) for condition in ontology_insight_conditions())
        similarity_fields = ["messageType", "accountId", "ontologyInsight.subject"]
    if key == NEWS_DIGEST:
        similarity_fields = ["messageType", "accountId", "newsDigest.primaryEvidenceId"]
    if key == INVESTMENT_CALENDAR_REMINDER:
        similarity_fields = ["messageType", "accountId", "investmentCalendar.eventId", "investmentCalendar.offsetMinutes"]
    return NotificationRuleConfig(
        message_type=key,
        enabled=True,
        conditions=conditions,
        similarity_enabled=default_similarity_enabled(key),
        similarity_window_minutes=default_similarity_window_minutes(key),
        similarity_bypass_conditions=[
            SimilarityBypassCondition.from_dict(condition.to_dict())
            for condition in default_similarity_bypass_conditions(key)
        ],
        similarity_fields=similarity_fields,
        state_cooldown_enabled=default_state_cooldown_enabled(key),
        state_cooldown_minutes=default_state_cooldown_minutes(key),
        market_hours_enabled=default_market_hours_enabled(key),
        market_hours_markets=default_market_hours_markets(key),
    )


DEFAULT_NOTIFICATION_RULES = {
    message_type: default_notification_rule(message_type)
    for message_type in default_rule_message_types()
}
