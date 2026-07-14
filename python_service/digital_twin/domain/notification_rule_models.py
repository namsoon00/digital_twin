import re
from dataclasses import dataclass, field as dataclass_field
from typing import Dict, List

from .message_types import (
    DEFAULT_ALERT_RULES,
    DEFAULT_CADENCE,
    INVESTMENT_CALENDAR_REMINDER,
    INVESTMENT_INSIGHT,
    NEWS_DIGEST,
    SYSTEM_MESSAGE_TYPES,
    notification_message_types,
)
from .market_hours import default_market_hours_enabled, default_market_hours_markets
from .notification_templates import DEFAULT_NOTIFICATION_TEMPLATES


DEFAULT_HONEY_THRESHOLD = 45
DEFAULT_LOW_SCORE_ACTION = "suppress"
DEFAULT_SIMILARITY_FIELDS = ["messageType", "accountId", "symbol", "severity", "title"]
STATE_COOLDOWN_MESSAGE_TYPES = {INVESTMENT_INSIGHT, "holdingTiming", "watchlistOntologySignal"}
DEFAULT_NOTIFICATION_SCORE_FORMULA = "rawScore"
VOLATILE_SCORE_SUFFIX = re.compile(r":[+-]?\d+(?:\.\d+)?%?$")
FORMULA_VARIABLE_BY_CONDITION_ID = {
    "severity_alert": "severityScore",
    "severity_watch": "severityScore",
    "has_symbol": "symbolScore",
    "important_terms": "importantScore",
    "confirming_data": "confirmingDataScore",
    "actionable_terms": "actionableScore",
    "body_present": "bodyScore",
    "status_noise": "noisePenalty",
}
DATA_QUALITY_REPEAT_BYPASS_IDS = {"novelty_score_delta", "new_source_signal", "new_relation_event"}

CONDITION_TYPE_LABELS = [
    {"type": "text_contains_any", "label": "메시지에 단어 포함", "description": "본문이나 알림 정보에 지정 단어 중 하나가 있으면 점수를 더합니다."},
    {"type": "context_contains_any", "label": "정보 필드에 단어 포함", "description": "선택한 정보 필드에 지정 단어 중 하나가 있으면 점수를 더합니다."},
    {"type": "context_equals", "label": "정보 값 일치", "description": "선택한 정보 필드가 지정 값과 같으면 점수를 더합니다."},
    {"type": "context_present", "label": "정보 값 존재", "description": "선택한 정보 필드가 비어 있지 않으면 점수를 더합니다."},
    {"type": "context_number_gte", "label": "정보 숫자 이상", "description": "선택한 정보 필드의 숫자가 기준 이상이면 점수를 더합니다."},
    {"type": "context_number_lte", "label": "정보 숫자 이하", "description": "선택한 정보 필드의 숫자가 기준 이하이면 점수를 더합니다."},
    {"type": "always", "label": "항상 적용", "description": "룰이 켜져 있으면 항상 점수를 더하거나 뺍니다."},
]

HIGH_SIGNAL_MESSAGE_TYPES = {
    INVESTMENT_INSIGHT,
    INVESTMENT_CALENDAR_REMINDER,
    NEWS_DIGEST,
    "watchlistOntologySignal",
    "holdingTiming",
}
LOW_SIGNAL_MESSAGE_TYPES = {"monitorHeartbeat", "externalDataConnection"}


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


def default_base_score(message_type: str) -> int:
    key = str(message_type or "")
    if key in SYSTEM_MESSAGE_TYPES:
        return 85
    if key in HIGH_SIGNAL_MESSAGE_TYPES:
        return 35
    if key in LOW_SIGNAL_MESSAGE_TYPES:
        return 15
    return 25


def default_threshold(message_type: str) -> int:
    key = str(message_type or "")
    if key in SYSTEM_MESSAGE_TYPES:
        return 20
    if key == INVESTMENT_INSIGHT:
        return 50
    if key == NEWS_DIGEST:
        return 45
    return DEFAULT_HONEY_THRESHOLD


def default_similarity_enabled(message_type: str) -> bool:
    return str(message_type or "") not in SYSTEM_MESSAGE_TYPES


def default_similarity_window_minutes(message_type: str) -> int:
    key = str(message_type or "")
    if key == NEWS_DIGEST:
        return 1440
    if key == INVESTMENT_CALENDAR_REMINDER:
        return 1440
    if key == INVESTMENT_INSIGHT:
        return 180
    if key in {"holdingTiming", "watchlistOntologySignal", "monitorHeartbeat"}:
        return 360
    if key in LOW_SIGNAL_MESSAGE_TYPES:
        return 180
    return 120


def default_similarity_penalty(message_type: str) -> int:
    key = str(message_type or "")
    if key == NEWS_DIGEST:
        return -60
    if key == INVESTMENT_CALENDAR_REMINDER:
        return -80
    if key == INVESTMENT_INSIGHT:
        return -35
    if key in {"holdingTiming", "watchlistOntologySignal", "monitorHeartbeat"}:
        return -40
    if key in LOW_SIGNAL_MESSAGE_TYPES:
        return -30
    return -20


def default_similarity_bypass_score_delta(message_type: str) -> int:
    if str(message_type or "") == NEWS_DIGEST:
        return 30
    if str(message_type or "") == INVESTMENT_CALENDAR_REMINDER:
        return 60
    return 15 if str(message_type or "") in {INVESTMENT_INSIGHT, "holdingTiming", "watchlistOntologySignal"} else 20


def default_state_cooldown_enabled(message_type: str) -> bool:
    return str(message_type or "") in STATE_COOLDOWN_MESSAGE_TYPES


def default_state_cooldown_minutes(message_type: str) -> int:
    return 360 if default_state_cooldown_enabled(message_type) else 0


def default_similarity_bypass_conditions(message_type: str) -> List["SimilarityBypassCondition"]:
    key = str(message_type or "")
    if key == INVESTMENT_INSIGHT:
        return [
            SimilarityBypassCondition(
                "insight_severity_upgrade",
                "인사이트 등급 상승",
                "severity_upgrade",
                description="온톨로지 인사이트 중요도가 올라가면 반복이어도 보냅니다.",
            ),
            SimilarityBypassCondition(
                "insight_type_changed",
                "인사이트 유형 변경",
                "field_changed",
                field="ontologyInsight.dispatchInsightType",
                description="위험, 기회, 데이터 품질처럼 발송 정책 분류가 바뀌면 보냅니다.",
            ),
            SimilarityBypassCondition(
                "relation_score_delta",
                "관계 강도 변화",
                "abs_number_delta_gte",
                field="ontologyInsight.score",
                value=8,
                description="이전 유사 인사이트보다 관계 강도가 기준점 이상 달라지면 보냅니다.",
            ),
            SimilarityBypassCondition(
                "novelty_score_delta",
                "신규성 변화",
                "abs_number_delta_gte",
                field="ontologyInsight.noveltyScore",
                value=12,
                description="관계 신규성이 기준점 이상 달라지면 보냅니다.",
            ),
            SimilarityBypassCondition(
                "new_source_signal",
                "새 근거 신호 추가",
                "list_new_items_gte",
                field="sourceSignalTypes",
                value=1,
                description="이전 유사 인사이트에 없던 근거 신호 타입이 추가되면 보냅니다.",
            ),
            SimilarityBypassCondition(
                "new_relation_event",
                "새 뉴스/공시 원천 근거 추가",
                "list_new_items_gte",
                field="ontologyInsight.sourceEventKeys",
                value=1,
                description="같은 신호 타입이어도 새 뉴스·공시·원문 기사 같은 실제 원천 근거가 추가되면 보냅니다. 반복 계산용 관계 규칙 키는 제외합니다.",
            ),
            SimilarityBypassCondition(
                "insight_profit_loss_worsened",
                "손익률 추가 악화",
                "profit_loss_worsened_lte",
                value=1,
                description="이전 투자 인사이트보다 손익률이 1%p 이상 나빠지면 반복이어도 보냅니다. 이미 큰 손실·큰 수익 구간이면 새 구간에 들어갈 때만 예외로 보냅니다.",
            ),
            SimilarityBypassCondition(
                "insight_profit_loss_improved",
                "손익률 큰 개선",
                "profit_loss_improved_gte",
                value=5,
                description="이전 투자 인사이트보다 손익률이 5%p 이상 좋아지면 회복 신호로 보고 반복이어도 보냅니다.",
            ),
            SimilarityBypassCondition(
                "insight_ma60_crossed_below",
                "60일 평균 아래 전환",
                "ma60_crossed_below",
                value=0,
                description="이전에는 60일 평균 이상이었지만 현재 60일 평균 아래로 내려가면 보냅니다.",
            ),
            SimilarityBypassCondition(
                "insight_action_changed",
                "판단 액션 변경",
                "field_changed_any",
                field="activeInvestmentOpinion.actionLabel,activeInvestmentOpinion.action,actionLabel,action,ontologyInsight.actionLabel,ontologyInsight.action",
                description="AI 또는 모델의 우선 행동이 바뀌면 반복이어도 보냅니다.",
            ),
        ]
    if key == "holdingTiming":
        return [
            SimilarityBypassCondition(
                "severity_upgrade",
                "등급 상승",
                "severity_upgrade",
                description="관찰에서 주의처럼 중요도가 올라가면 반복이어도 보냅니다.",
            ),
            SimilarityBypassCondition(
                "holding_score_delta",
                "보유 모델 점수 변화",
                "abs_number_delta_gte",
                field="holdingDecisionScore",
                value=8,
                description="이전 보유 타이밍 알림보다 판단 점수가 기준점 이상 달라지면 보냅니다.",
            ),
            SimilarityBypassCondition(
                "loss_rate_worsened",
                "손익률 추가 악화",
                "profit_loss_worsened_lte",
                value=1,
                description="이전 보유 타이밍 알림보다 손익률이 1%p 이상 나빠지면 보냅니다. 이미 큰 손실·큰 수익 구간이면 새 구간에 들어갈 때만 예외로 보냅니다.",
            ),
            SimilarityBypassCondition(
                "loss_rate_improved",
                "손익률 큰 개선",
                "profit_loss_improved_gte",
                value=5,
                description="이전 보유 타이밍 알림보다 손익률이 5%p 이상 좋아지면 회복 신호로 보고 반복이어도 보냅니다.",
            ),
            SimilarityBypassCondition(
                "holding_ma60_crossed_below",
                "60일 평균 아래 전환",
                "ma60_crossed_below",
                value=0,
                description="이전에는 60일 평균 이상이었지만 현재 60일 평균 아래로 내려가면 보냅니다.",
            ),
            SimilarityBypassCondition(
                "holding_action_changed",
                "판단 액션 변경",
                "field_changed_any",
                field="holdingDecision,holdingAction,actionLabel,activeInvestmentOpinion.actionLabel,activeInvestmentOpinion.action",
                description="보유 판단 또는 우선 행동이 바뀌면 반복이어도 보냅니다.",
            ),
        ]
    return []


@dataclass
class NotificationRuleCondition:
    condition_id: str
    label: str
    condition_type: str
    score: int
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
            score=clamp_int(payload.get("score"), -100, 100, 0),
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
            "score": int(self.score or 0),
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


@dataclass
class NotificationRuleConfig:
    message_type: str
    enabled: bool = True
    threshold: int = DEFAULT_HONEY_THRESHOLD
    base_score: int = 25
    low_score_action: str = DEFAULT_LOW_SCORE_ACTION
    conditions: List[NotificationRuleCondition] = dataclass_field(default_factory=list)
    similarity_enabled: bool = True
    similarity_window_minutes: int = 120
    similarity_penalty: int = -20
    similarity_bypass_score_delta: int = 20
    similarity_bypass_conditions: List[SimilarityBypassCondition] = dataclass_field(default_factory=list)
    similarity_fields: List[str] = dataclass_field(default_factory=lambda: list(DEFAULT_SIMILARITY_FIELDS))
    state_cooldown_enabled: bool = False
    state_cooldown_minutes: int = 0
    market_hours_enabled: bool = False
    market_hours_markets: List[str] = dataclass_field(default_factory=list)
    updated_at: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "NotificationRuleConfig":
        raw_conditions = payload.get("conditions") if isinstance(payload, dict) else []
        conditions = []
        if isinstance(raw_conditions, list):
            for item in raw_conditions:
                if isinstance(item, dict):
                    condition = NotificationRuleCondition.from_dict(item)
                    if condition.condition_id and condition.condition_type:
                        conditions.append(condition)
        message_type = str(payload.get("messageType") or payload.get("message_type") or "").strip()
        raw_bypass_conditions = (
            payload.get("similarityBypassConditions")
            if "similarityBypassConditions" in payload
            else payload.get("similarity_bypass_conditions")
        )
        bypass_conditions = []
        if isinstance(raw_bypass_conditions, list):
            for item in raw_bypass_conditions:
                if isinstance(item, dict):
                    condition = SimilarityBypassCondition.from_dict(item)
                    if condition.condition_id and condition.condition_type:
                        bypass_conditions.append(condition)
        else:
            bypass_conditions = default_similarity_bypass_conditions(message_type)
        base_score_value = payload["baseScore"] if "baseScore" in payload else payload.get("base_score")
        raw_similarity_fields = payload.get("similarityFields") if "similarityFields" in payload else payload.get("similarity_fields")
        if isinstance(raw_similarity_fields, str):
            similarity_fields = [item.strip() for item in raw_similarity_fields.split(",") if item.strip()]
        elif isinstance(raw_similarity_fields, list):
            similarity_fields = [str(item or "").strip() for item in raw_similarity_fields if str(item or "").strip()]
        else:
            similarity_fields = list(DEFAULT_SIMILARITY_FIELDS)
        raw_market_hours_markets = payload.get("marketHoursMarkets") if "marketHoursMarkets" in payload else payload.get("market_hours_markets")
        if isinstance(raw_market_hours_markets, str):
            market_hours_markets = [item.strip().upper() for item in raw_market_hours_markets.split(",") if item.strip()]
        elif isinstance(raw_market_hours_markets, list):
            market_hours_markets = [str(item or "").strip().upper() for item in raw_market_hours_markets if str(item or "").strip()]
        else:
            market_hours_markets = default_market_hours_markets(message_type)
        similarity_enabled_value = payload["similarityEnabled"] if "similarityEnabled" in payload else payload.get("similarity_enabled")
        similarity_window_value = payload["similarityWindowMinutes"] if "similarityWindowMinutes" in payload else payload.get("similarity_window_minutes")
        similarity_penalty_value = payload["similarityPenalty"] if "similarityPenalty" in payload else payload.get("similarity_penalty")
        similarity_bypass_value = (
            payload["similarityBypassScoreDelta"]
            if "similarityBypassScoreDelta" in payload
            else payload.get("similarity_bypass_score_delta")
        )
        state_cooldown_enabled_value = payload["stateCooldownEnabled"] if "stateCooldownEnabled" in payload else payload.get("state_cooldown_enabled")
        state_cooldown_minutes_value = payload["stateCooldownMinutes"] if "stateCooldownMinutes" in payload else payload.get("state_cooldown_minutes")
        market_hours_enabled_value = payload["marketHoursEnabled"] if "marketHoursEnabled" in payload else payload.get("market_hours_enabled")
        return cls(
            message_type=message_type,
            enabled=bool_value(payload.get("enabled"), True),
            threshold=clamp_int(payload.get("threshold"), 0, 100, default_threshold(message_type)),
            base_score=clamp_int(base_score_value, 0, 100, default_base_score(message_type)),
            low_score_action=str(payload.get("lowScoreAction") or payload.get("low_score_action") or DEFAULT_LOW_SCORE_ACTION).strip() or DEFAULT_LOW_SCORE_ACTION,
            conditions=conditions,
            similarity_enabled=bool_value(similarity_enabled_value, default_similarity_enabled(message_type)),
            similarity_window_minutes=clamp_int(similarity_window_value, 0, 10080, default_similarity_window_minutes(message_type)),
            similarity_penalty=clamp_int(similarity_penalty_value, -100, 0, default_similarity_penalty(message_type)),
            similarity_bypass_score_delta=clamp_int(similarity_bypass_value, 0, 100, default_similarity_bypass_score_delta(message_type)),
            similarity_bypass_conditions=bypass_conditions,
            similarity_fields=similarity_fields,
            state_cooldown_enabled=bool_value(state_cooldown_enabled_value, default_state_cooldown_enabled(message_type)),
            state_cooldown_minutes=clamp_int(state_cooldown_minutes_value, 0, 10080, default_state_cooldown_minutes(message_type)),
            market_hours_enabled=bool_value(market_hours_enabled_value, default_market_hours_enabled(message_type)),
            market_hours_markets=market_hours_markets,
            updated_at=str(payload.get("updatedAt") or payload.get("updated_at") or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "messageType": self.message_type,
            "enabled": bool(self.enabled),
            "threshold": int(self.threshold or 0),
            "baseScore": int(self.base_score or 0),
            "lowScoreAction": self.low_score_action or DEFAULT_LOW_SCORE_ACTION,
            "conditions": [condition.to_dict() for condition in self.conditions],
            "similarityEnabled": bool(self.similarity_enabled),
            "similarityWindowMinutes": int(self.similarity_window_minutes or 0),
            "similarityPenalty": int(self.similarity_penalty or 0),
            "similarityBypassScoreDelta": int(self.similarity_bypass_score_delta or 0),
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
    score: int
    threshold: int
    should_send: bool
    low_score_action: str
    reasons: List[str] = dataclass_field(default_factory=list)
    fingerprint: str = ""
    similarity_enabled: bool = False
    similarity_window_minutes: int = 0
    similarity_penalty: int = 0
    similarity_recent_count: int = 0
    similarity_previous_score: int = 0
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
    notification_formula_audit: Dict[str, object] = dataclass_field(default_factory=dict)
    previous_profit_loss_rate: object = None
    profit_loss_rate_delta_pct: object = None

    def to_context(self) -> Dict[str, object]:
        decision = "send" if self.should_send else "suppressed"
        if not self.enabled:
            decision = "bypass"
        payload = {
            "honeyScore": self.score,
            "honeyThreshold": self.threshold,
            "honeyScoreText": str(self.score) + "/" + str(self.threshold),
            "honeyDecision": decision,
            "honeyReasons": list(self.reasons or []),
            "honeyRuleEnabled": bool(self.enabled),
            "honeyLowScoreAction": self.low_score_action,
            "honeyFingerprint": self.fingerprint,
            "honeySimilarityEnabled": bool(self.similarity_enabled),
            "honeySimilarityWindowMinutes": self.similarity_window_minutes,
            "honeySimilarityPenalty": self.similarity_penalty,
            "honeySimilarityRecentCount": self.similarity_recent_count,
            "honeySimilarityPreviousScore": self.similarity_previous_score,
            "honeySimilarityBypassed": bool(self.similarity_bypassed),
            "honeySimilarityBypassReason": self.similarity_bypass_reason,
            "honeySuppressionReason": self.suppression_reason,
            "honeyStateCooldownEnabled": bool(self.state_cooldown_enabled),
            "honeyStateCooldownMinutes": self.state_cooldown_minutes,
            "honeyStateRecentSentCount": self.state_recent_sent_count,
            "honeyStateLastSentAt": self.state_last_sent_at,
            "honeyStateLastSentAgeMinutes": self.state_last_sent_age_minutes,
            "honeyStateDecision": self.state_decision,
            "honeyStateReason": self.state_reason,
            "honeyStateSuppressed": bool(self.state_suppressed),
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
            "notificationFormulaAudit": dict(self.notification_formula_audit or {}),
        }
        if self.previous_profit_loss_rate is not None:
            payload["previousProfitLossRate"] = self.previous_profit_loss_rate
        if self.profit_loss_rate_delta_pct is not None:
            payload["profitLossRateDeltaPct"] = self.profit_loss_rate_delta_pct
        return payload


def default_conditions() -> List[NotificationRuleCondition]:
    return [
        NotificationRuleCondition("severity_alert", "주의 등급", "context_equals", 25, field="severity", value="ALERT"),
        NotificationRuleCondition("severity_watch", "관찰 등급", "context_equals", 10, field="severity", value="WATCH"),
        NotificationRuleCondition("has_symbol", "종목 지정", "context_present", 10, field="symbol"),
        NotificationRuleCondition(
            "important_terms",
            "핵심 투자 단어",
            "context_contains_any",
            15,
            field="notificationSignals",
            terms=["important"],
        ),
        NotificationRuleCondition(
            "confirming_data",
            "확인 데이터 포함",
            "context_contains_any",
            10,
            field="notificationSignals",
            terms=["confirmingData"],
        ),
        NotificationRuleCondition(
            "actionable_terms",
            "행동 필요 표현",
            "context_contains_any",
            10,
            field="notificationSignals",
            terms=["actionable"],
        ),
        NotificationRuleCondition(
            "body_present",
            "본문 있음",
            "context_present",
            5,
            field="body",
        ),
        NotificationRuleCondition(
            "status_noise",
            "상태성 노이즈",
            "context_contains_any",
            -25,
            field="notificationSignals",
            terms=["statusNoise"],
        ),
    ]


def ontology_insight_conditions() -> List[NotificationRuleCondition]:
    return [
        NotificationRuleCondition("ontology_relation_score", "관계 강도", "context_number_gte", 15, field="ontologyInsight.score", value=55),
        NotificationRuleCondition("ontology_novelty_score", "관계 신규성", "context_number_gte", 15, field="ontologyInsight.noveltyScore", value=65),
        NotificationRuleCondition("ontology_confidence", "인사이트 신뢰도", "context_number_gte", 10, field="ontologyInsight.confidence", value=70),
        NotificationRuleCondition("ontology_source_signals", "근거 신호 묶음", "context_present", 5, field="sourceSignalTypes"),
    ]


def default_notification_rule(message_type: str) -> NotificationRuleConfig:
    key = str(message_type or "notification").strip() or "notification"
    conditions = [NotificationRuleCondition.from_dict(condition.to_dict()) for condition in default_conditions()]
    similarity_fields = list(DEFAULT_SIMILARITY_FIELDS)
    if key == INVESTMENT_INSIGHT:
        conditions.extend(NotificationRuleCondition.from_dict(condition.to_dict()) for condition in ontology_insight_conditions())
        similarity_fields = ["messageType", "accountId", "ontologyInsight.subject", "ontologyInsight.dispatchInsightType"]
    if key == NEWS_DIGEST:
        similarity_fields = ["messageType", "accountId", "newsDigest.primaryEvidenceId"]
    if key == INVESTMENT_CALENDAR_REMINDER:
        similarity_fields = ["messageType", "accountId", "investmentCalendar.eventId", "investmentCalendar.offsetMinutes"]
    return NotificationRuleConfig(
        message_type=key,
        enabled=True,
        threshold=default_threshold(key),
        base_score=default_base_score(key),
        low_score_action=DEFAULT_LOW_SCORE_ACTION,
        conditions=conditions,
        similarity_enabled=default_similarity_enabled(key),
        similarity_window_minutes=default_similarity_window_minutes(key),
        similarity_penalty=default_similarity_penalty(key),
        similarity_bypass_score_delta=default_similarity_bypass_score_delta(key),
        similarity_bypass_conditions=[SimilarityBypassCondition.from_dict(condition.to_dict()) for condition in default_similarity_bypass_conditions(key)],
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
