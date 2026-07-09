import re
from dataclasses import dataclass, field as dataclass_field
from typing import Dict, Iterable, List, Tuple

from .message_types import (
    DEFAULT_ALERT_RULES,
    DEFAULT_CADENCE,
    INVESTMENT_INSIGHT,
    SYSTEM_MESSAGE_TYPES,
    notification_message_types,
)
from .market_hours import default_market_hours_enabled, default_market_hours_markets, evaluate_market_hours
from .notification_templates import DEFAULT_NOTIFICATION_TEMPLATES
from .notifications import NotificationJob
from .scoring import fallback_terms_for_condition
from .strategy import SafeFormula


DEFAULT_HONEY_THRESHOLD = 45
DEFAULT_LOW_SCORE_ACTION = "suppress"
DEFAULT_SIMILARITY_FIELDS = ["messageType", "accountId", "symbol", "severity", "title"]
STATE_COOLDOWN_MESSAGE_TYPES = {INVESTMENT_INSIGHT, "holdingTiming", "externalEquityMove", "externalCryptoMove"}
DEFAULT_NOTIFICATION_SCORE_FORMULA = "rawScore"
VOLATILE_SCORE_SUFFIX = re.compile(r":-?\d+(?:\.\d+)?$")
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
    "modelBuy",
    "modelSell",
    "watchlistBuyCandidate",
    "watchlistOntologySignal",
    "holdingTiming",
    "monitorPositionChange",
    "monitorPnlChange",
    "monitorValueChange",
    "monitorTrendChange",
    "monitorCashChange",
    "monitorDecisionChange",
    "externalEquityMove",
    "externalCryptoMove",
    "externalMacroShift",
    "externalDartDisclosure",
}
LOW_SIGNAL_MESSAGE_TYPES = {"monitorHeartbeat", "watchlistQuotePending", "externalDataConnection"}


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
    if key in {"externalEquityMove", "externalCryptoMove"}:
        return 60
    return DEFAULT_HONEY_THRESHOLD


def default_similarity_enabled(message_type: str) -> bool:
    return str(message_type or "") not in SYSTEM_MESSAGE_TYPES


def default_similarity_window_minutes(message_type: str) -> int:
    key = str(message_type or "")
    if key == INVESTMENT_INSIGHT:
        return 180
    if key in {"holdingTiming", "monitorHeartbeat", "externalEquityMove", "externalCryptoMove"}:
        return 360
    if key in LOW_SIGNAL_MESSAGE_TYPES:
        return 180
    if key in {"monitorPnlChange", "monitorValueChange", "monitorTrendChange", "monitorCashChange"}:
        return 60
    return 120


def default_similarity_penalty(message_type: str) -> int:
    key = str(message_type or "")
    if key == INVESTMENT_INSIGHT:
        return -35
    if key in {"externalEquityMove", "externalCryptoMove"}:
        return -55
    if key in {"holdingTiming", "monitorHeartbeat"}:
        return -40
    if key in LOW_SIGNAL_MESSAGE_TYPES:
        return -30
    return -20


def default_similarity_bypass_score_delta(message_type: str) -> int:
    return 15 if str(message_type or "") in {INVESTMENT_INSIGHT, "modelBuy", "modelSell", "watchlistBuyCandidate", "monitorDecisionChange"} else 20


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
                field="ontologyInsight.insightType",
                description="위험, 기회, 데이터 품질처럼 의미 분류가 바뀌면 보냅니다.",
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
                "새 관계 이벤트 추가",
                "list_new_items_gte",
                field="ontologyInsight.sourceEventKeys",
                value=1,
                description="같은 신호 타입이어도 활성 관계 규칙 조합이 달라지면 보냅니다.",
            ),
        ]
    if key == "externalEquityMove":
        return [
            SimilarityBypassCondition(
                "severity_upgrade",
                "등급 상승",
                "severity_upgrade",
                description="관찰에서 주의처럼 중요도가 올라가면 반복이어도 보냅니다.",
            ),
            SimilarityBypassCondition(
                "change_abs_delta",
                "변동률 추가 확대",
                "abs_number_delta_gte",
                field="changePercent",
                value=2,
                description="이전 유사 알림보다 변동률 절대값이 기준 %p 이상 커지면 보냅니다.",
            ),
            SimilarityBypassCondition(
                "volume_multiplier",
                "거래량 급증",
                "number_multiplier_gte",
                field="volume",
                value=1.5,
                description="이전 유사 알림보다 거래량이 기준 배수 이상 커지면 보냅니다.",
            ),
        ]
    if key == "externalCryptoMove":
        return [
            SimilarityBypassCondition(
                "severity_upgrade",
                "등급 상승",
                "severity_upgrade",
                description="관찰에서 주의처럼 중요도가 올라가면 반복이어도 보냅니다.",
            ),
            SimilarityBypassCondition(
                "change_24h_abs_delta",
                "24시간 변동 확대",
                "abs_number_delta_gte",
                field="change24h",
                value=2,
                description="이전 유사 알림보다 24시간 변동률 절대값이 기준 %p 이상 커지면 보냅니다.",
            ),
            SimilarityBypassCondition(
                "change_7d_abs_delta",
                "7일 변동 확대",
                "abs_number_delta_gte",
                field="change7d",
                value=3,
                description="이전 유사 알림보다 7일 변동률 절대값이 기준 %p 이상 커지면 보냅니다.",
            ),
            SimilarityBypassCondition(
                "volume_multiplier",
                "거래액 급증",
                "number_multiplier_gte",
                field="volume24h",
                value=1.5,
                description="이전 유사 알림보다 거래액이 기준 배수 이상 커지면 보냅니다.",
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
                "number_delta_lte",
                field="profitLossRate",
                value=2,
                description="이전 보유 타이밍 알림보다 손익률이 기준 %p 이상 나빠지면 보냅니다.",
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

    def to_context(self) -> Dict[str, object]:
        decision = "send" if self.should_send else "suppressed"
        if not self.enabled:
            decision = "bypass"
        return {
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
        similarity_fields = ["messageType", "accountId", "ontologyInsight.subject", "ontologyInsight.insightType"]
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
    current = context
    for part in [item for item in str(field or "").split(".") if item]:
        if isinstance(current, dict) and part in current:
            current = current.get(part)
        else:
            return ""
    return current


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
    if condition_type == "list_new_items_gte":
        current_items = set(normalize_similarity_list_item(field, item) for item in flattened_strings(field_value(context, field)) if normalize_similarity_list_item(field, item))
        previous_items = set(normalize_similarity_list_item(field, item) for item in flattened_strings(field_value(previous_context, field)) if normalize_similarity_list_item(field, item))
        new_items = sorted(current_items - previous_items)
        minimum = numeric_value(condition.value)
        if new_items and len(new_items) >= int(minimum or 1):
            return True, label + " " + ", ".join(new_items[:4])
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
    if decision.suppression_reason == "state_cooldown" or decision.similarity_bypassed:
        return decision
    if not config.enabled or not config.similarity_enabled or decision.similarity_recent_count <= 0:
        return decision
    previous_context = previous_context or {}
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
    if decision.state_recent_sent_count <= 0:
        decision.state_decision = "new_threshold"
        decision.state_reason = "신규 임계값 상태"
        decision.reasons.append("상태 정책: " + decision.state_reason)
        return decision
    previous_context = previous_context or {}
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
    if config.enabled and config.market_hours_enabled and market_decision.status in {"open", "closed"}:
        decision.reasons.append("장 시간 " + market_decision.reason)
    if config.enabled and config.market_hours_enabled and not market_decision.should_send:
        decision.should_send = False
        decision.suppression_reason = "market_closed" if market_decision.status == "closed" else "market_hours"
    return decision
