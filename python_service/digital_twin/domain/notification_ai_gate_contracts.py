import re
from dataclasses import asdict, dataclass, field
from datetime import timedelta, timezone
from typing import Dict, List, Optional

from .message_types import (
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_DART_DISCLOSURE,
    EXTERNAL_EQUITY_MOVE,
    EXTERNAL_MACRO_SHIFT,
    HOLDING_TIMING,
    INVESTMENT_INSIGHT,
    MODEL_BUY,
    MODEL_SELL,
    MONITOR_DECISION_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_VALUE_CHANGE,
    WATCHLIST_BUY_CANDIDATE,
)


NOTIFICATION_AI_GATE_VERSION = "notification-ai-gate-v1"
AI_DECISION_MODE = "ai-first"
AI_DECISION_SOURCE_LABEL = "AI 투자 판단"
MESSAGE_START_BADGE = "🔔 새 알림"
KST = timezone(timedelta(hours=9))
VALID_ACTIONS = {"BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID"}
ACTION_LABELS = {
    "BUY": "매수",
    "ADD": "추가매수",
    "HOLD": "보유",
    "TRIM": "분할축소",
    "SELL": "매도",
    "AVOID": "회피",
}
ACTION_TEXT_REPLACEMENTS = {
    "BUY": "매수",
    "ADD": "추가매수",
    "HOLD": "보유",
    "TRIM": "분할축소",
    "SELL": "매도",
    "AVOID": "회피",
}

DEFAULT_AI_GATE_MESSAGE_TYPES = {
    INVESTMENT_INSIGHT,
    HOLDING_TIMING,
    MONITOR_DECISION_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_VALUE_CHANGE,
    MODEL_BUY,
    MODEL_SELL,
    WATCHLIST_BUY_CANDIDATE,
    EXTERNAL_EQUITY_MOVE,
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_MACRO_SHIFT,
    EXTERNAL_DART_DISCLOSURE,
}


@dataclass
class NotificationAIValidatedResponse:
    action: str = "HOLD"
    action_label: str = "보유"
    confidence: float = 50.0
    original_confidence: float = 0.0
    summary: str = ""
    opinion: str = ""
    evidence: List[str] = field(default_factory=list)
    counter_evidence: List[str] = field(default_factory=list)
    invalidation_condition: str = ""
    next_checks: List[str] = field(default_factory=list)
    missing_data_impact: List[str] = field(default_factory=list)
    source_urls: List[str] = field(default_factory=list)
    precomputed_action: str = ""
    disagreement_reason: str = ""
    confidence_cap: float = 100.0
    confidence_cap_reasons: List[str] = field(default_factory=list)
    reference_date: str = ""
    validation_warnings: List[str] = field(default_factory=list)
    strategy_guide: Dict[str, object] = field(default_factory=dict)
    source: str = "local"
    raw_response: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["engineVersion"] = NOTIFICATION_AI_GATE_VERSION
        payload["actionLabel"] = payload.pop("action_label")
        payload["originalConfidence"] = payload.pop("original_confidence")
        payload["counterEvidence"] = payload.pop("counter_evidence")
        payload["invalidationCondition"] = payload.pop("invalidation_condition")
        payload["nextChecks"] = payload.pop("next_checks")
        payload["missingDataImpact"] = payload.pop("missing_data_impact")
        payload["sourceUrls"] = payload.pop("source_urls")
        payload["precomputedAction"] = payload.pop("precomputed_action")
        payload["disagreementReason"] = payload.pop("disagreement_reason")
        payload["confidenceCap"] = payload.pop("confidence_cap")
        payload["confidenceCapReasons"] = payload.pop("confidence_cap_reasons")
        payload["referenceDate"] = payload.pop("reference_date")
        payload["validationWarnings"] = payload.pop("validation_warnings")
        payload["strategyGuide"] = payload.pop("strategy_guide")
        payload["rawResponse"] = payload.pop("raw_response")
        return payload


def ai_gate_message_type_set(raw: object = "") -> set:
    text = str(raw or "").strip()
    if not text:
        return set(DEFAULT_AI_GATE_MESSAGE_TYPES)
    return {part.strip() for part in text.replace("\n", ",").split(",") if part.strip()}


def ai_gate_enabled_for_message_type(message_type: str, settings: Optional[Dict[str, object]] = None) -> bool:
    settings = settings or {}
    enabled = str(settings.get("notificationAiGateEnabled") or "1").strip() != "0"
    if not enabled:
        return False
    return str(message_type or "").strip() in ai_gate_message_type_set(settings.get("notificationAiGateMessageTypes"))
