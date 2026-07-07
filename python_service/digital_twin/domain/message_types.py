from typing import Dict, List


MODEL_BUY = "modelBuy"
MODEL_SELL = "modelSell"
WATCHLIST_BUY_CANDIDATE = "watchlistBuyCandidate"
WATCHLIST_QUOTE = "watchlistQuote"
WATCHLIST_QUOTE_PENDING = "watchlistQuotePending"
HOLDING_TIMING = "holdingTiming"
MONITOR_HEARTBEAT = "monitorHeartbeat"
MONITOR_CONNECTION = "monitorConnection"
MONITOR_POSITION_CHANGE = "monitorPositionChange"
MONITOR_PNL_CHANGE = "monitorPnlChange"
MONITOR_VALUE_CHANGE = "monitorValueChange"
MONITOR_TREND_CHANGE = "monitorTrendChange"
MONITOR_CASH_CHANGE = "monitorCashChange"
MONITOR_DECISION_CHANGE = "monitorDecisionChange"
EXTERNAL_EQUITY_MOVE = "externalEquityMove"
EXTERNAL_CRYPTO_MOVE = "externalCryptoMove"
EXTERNAL_MACRO_SHIFT = "externalMacroShift"
EXTERNAL_DART_DISCLOSURE = "externalDartDisclosure"
EXTERNAL_DATA_CONNECTION = "externalDataConnection"
MODEL_REVIEW = "modelReview"
WORK_HANDOFF = "workHandoff"
GENERIC_NOTIFICATION = "notification"
DEFAULT_MESSAGE = "default"

MONITORING_MESSAGE_TYPES = [
    MODEL_BUY,
    MODEL_SELL,
    WATCHLIST_BUY_CANDIDATE,
    WATCHLIST_QUOTE,
    WATCHLIST_QUOTE_PENDING,
    HOLDING_TIMING,
    MONITOR_HEARTBEAT,
    MONITOR_CONNECTION,
    MONITOR_POSITION_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_VALUE_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_CASH_CHANGE,
    MONITOR_DECISION_CHANGE,
    EXTERNAL_EQUITY_MOVE,
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_MACRO_SHIFT,
    EXTERNAL_DART_DISCLOSURE,
    EXTERNAL_DATA_CONNECTION,
]

SYSTEM_MESSAGE_TYPES = {
    DEFAULT_MESSAGE,
    MODEL_REVIEW,
    WORK_HANDOFF,
    GENERIC_NOTIFICATION,
}

MIN_CADENCE_MINUTES = 10

DEFAULT_ALERT_RULES = {
    MODEL_BUY: 1,
    MODEL_SELL: 1,
    WATCHLIST_BUY_CANDIDATE: 1,
    WATCHLIST_QUOTE: 1,
    WATCHLIST_QUOTE_PENDING: 1,
    HOLDING_TIMING: 1,
    MONITOR_HEARTBEAT: 1,
    MONITOR_CONNECTION: 1,
    MONITOR_POSITION_CHANGE: 1,
    MONITOR_PNL_CHANGE: 1,
    MONITOR_VALUE_CHANGE: 1,
    MONITOR_TREND_CHANGE: 1,
    MONITOR_CASH_CHANGE: 1,
    MONITOR_DECISION_CHANGE: 1,
    EXTERNAL_EQUITY_MOVE: 1,
    EXTERNAL_CRYPTO_MOVE: 1,
    EXTERNAL_MACRO_SHIFT: 1,
    EXTERNAL_DART_DISCLOSURE: 1,
    EXTERNAL_DATA_CONNECTION: 1,
}

DEFAULT_ALERT_THRESHOLDS = {
    "modelBuyScore": 74,
    "modelSellScore": 72,
    "watchlistBuyScore": 74,
    "watchlistPriceDelta": 3,
    "lossRateLow": -8,
    "lossRateBufferPct": 1,
    "lossGuardVolumeConfirmRatio": 0.8,
    "lossGuardMa60SupportPct": 0,
    "lossGuardWeakEvidencePenalty": 30,
    "monitorPnlDelta": 2,
    "monitorValueDelta": 5,
    "monitorMaDistance": 8,
    "monitorCashDelta": 10,
    "monitorExitPressureDelta": 15,
    "monitorDecisionLabelBuffer": 5,
    "externalEquityChangePct": 3,
    "externalCryptoChange24hPct": 4,
    "externalCryptoChange7dPct": 10,
    "externalBitcoinChange24hPct": 3,
    "externalBitcoinChange7dPct": 4,
    "externalMacroRateDeltaBp": 15,
}

DEFAULT_CADENCE = {
    MODEL_BUY: 10,
    MODEL_SELL: 10,
    WATCHLIST_BUY_CANDIDATE: 10,
    WATCHLIST_QUOTE: 10,
    WATCHLIST_QUOTE_PENDING: 60,
    HOLDING_TIMING: 10,
    MONITOR_HEARTBEAT: 10,
    MONITOR_CONNECTION: 10,
    MONITOR_POSITION_CHANGE: 10,
    MONITOR_PNL_CHANGE: 10,
    MONITOR_VALUE_CHANGE: 10,
    MONITOR_TREND_CHANGE: 10,
    MONITOR_CASH_CHANGE: 10,
    MONITOR_DECISION_CHANGE: 10,
    EXTERNAL_EQUITY_MOVE: 60,
    EXTERNAL_CRYPTO_MOVE: 60,
    EXTERNAL_MACRO_SHIFT: 60,
    EXTERNAL_DART_DISCLOSURE: 60,
    EXTERNAL_DATA_CONNECTION: 60,
}

MESSAGE_TYPE_LABELS = {
    MODEL_BUY: "모델 매수",
    MODEL_SELL: "모델 매도",
    WATCHLIST_BUY_CANDIDATE: "관심종목 매수 후보",
    WATCHLIST_QUOTE: "관심종목 시세",
    WATCHLIST_QUOTE_PENDING: "관심종목 시세 대기",
    HOLDING_TIMING: "보유 타이밍",
    MONITOR_HEARTBEAT: "실시간 상태",
    MONITOR_CONNECTION: "연결 상태",
    MONITOR_POSITION_CHANGE: "보유 변화",
    MONITOR_PNL_CHANGE: "손익률 변화",
    MONITOR_VALUE_CHANGE: "평가액 변화",
    MONITOR_TREND_CHANGE: "이동평균 변화",
    MONITOR_CASH_CHANGE: "현금비중 변화",
    MONITOR_DECISION_CHANGE: "판단 변화",
    EXTERNAL_EQUITY_MOVE: "미장 가격/거래량",
    EXTERNAL_CRYPTO_MOVE: "크립토 변동",
    EXTERNAL_MACRO_SHIFT: "거시 지표 변화",
    EXTERNAL_DART_DISCLOSURE: "국내 공시",
    EXTERNAL_DATA_CONNECTION: "외부 데이터 연결",
}

MESSAGE_TYPE_EMOJIS = {
    DEFAULT_MESSAGE: "🔔",
    MODEL_BUY: "🟢",
    MODEL_SELL: "🔴",
    WATCHLIST_BUY_CANDIDATE: "👀",
    WATCHLIST_QUOTE: "👀",
    WATCHLIST_QUOTE_PENDING: "⏳",
    HOLDING_TIMING: "⚖️",
    MONITOR_HEARTBEAT: "💓",
    MONITOR_CONNECTION: "🔌",
    MONITOR_POSITION_CHANGE: "📦",
    MONITOR_PNL_CHANGE: "📊",
    MONITOR_VALUE_CHANGE: "💵",
    MONITOR_TREND_CHANGE: "📈",
    MONITOR_CASH_CHANGE: "💵",
    MONITOR_DECISION_CHANGE: "🔁",
    EXTERNAL_EQUITY_MOVE: "🇺🇸",
    EXTERNAL_CRYPTO_MOVE: "🪙",
    EXTERNAL_MACRO_SHIFT: "🏦",
    EXTERNAL_DART_DISCLOSURE: "📄",
    EXTERNAL_DATA_CONNECTION: "🛰️",
    MODEL_REVIEW: "🧠",
    WORK_HANDOFF: "✅",
    GENERIC_NOTIFICATION: "🔔",
}

TRIGGER_SUMMARIES = {
    MODEL_BUY: "내가 정한 매수 점수가 기준값을 넘을 때 보냅니다.",
    MODEL_SELL: "내가 정한 매도 점수가 기준값을 넘을 때 보냅니다.",
    WATCHLIST_BUY_CANDIDATE: "관심 종목의 매수 점수가 기준값을 넘을 때 보냅니다.",
    WATCHLIST_QUOTE: "관심 종목의 시세와 추세 데이터가 갱신될 때 보냅니다.",
    WATCHLIST_QUOTE_PENDING: "관심 종목 시세를 아직 받지 못했을 때 보냅니다.",
    HOLDING_TIMING: "보유 종목의 매수·매도 점검 데이터가 기준에 걸릴 때 보냅니다.",
    MONITOR_HEARTBEAT: "실시간 모니터링 워커가 정상 작동 중인지 확인할 때 보냅니다.",
    MONITOR_CONNECTION: "Toss 연결 상태가 바뀔 때 보냅니다.",
    MONITOR_POSITION_CHANGE: "새 보유, 제외, 수량 변경이 감지될 때 보냅니다.",
    MONITOR_PNL_CHANGE: "직전 기록과 비교해 손익률 변화가 기준값을 넘을 때 보냅니다.",
    MONITOR_VALUE_CHANGE: "직전 기록과 비교해 평가액 변화가 기준값을 넘을 때 보냅니다.",
    MONITOR_TREND_CHANGE: "이동평균 돌파, 크로스, 현재가와 이동평균 차이가 커질 때 보냅니다.",
    MONITOR_CASH_CHANGE: "시장별 현금 비중 변화가 기준값을 넘을 때 보냅니다.",
    MONITOR_DECISION_CHANGE: "종목 판단이나 위험 점수가 바뀔 때 보냅니다.",
    EXTERNAL_EQUITY_MOVE: "Alpha Vantage 기준 미국 보유 종목의 가격 변화가 기준값을 넘을 때 보냅니다.",
    EXTERNAL_CRYPTO_MOVE: "CoinGecko 기준 크립토 가격 변화가 기준값을 넘을 때 보냅니다.",
    EXTERNAL_MACRO_SHIFT: "FRED 금리·스프레드 변화가 기준값을 넘을 때 보냅니다.",
    EXTERNAL_DART_DISCLOSURE: "OpenDART에서 보유 국내 종목의 새 공시가 감지될 때 보냅니다.",
    EXTERNAL_DATA_CONNECTION: "외부 데이터 API 응답 오류나 호출 제한이 감지될 때 보냅니다.",
}


def notification_message_types(extra_types: List[str] = None) -> List[str]:
    keys = [
        DEFAULT_MESSAGE,
        *MONITORING_MESSAGE_TYPES,
        MODEL_REVIEW,
        WORK_HANDOFF,
        GENERIC_NOTIFICATION,
        *(extra_types or []),
    ]
    seen = set()
    ordered = []
    for key in keys:
        normalized = str(key or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def public_message_catalog() -> Dict[str, Dict[str, object]]:
    return {
        key: {
            "label": MESSAGE_TYPE_LABELS.get(key, key),
            "icon": MESSAGE_TYPE_EMOJIS.get(key, "🔔"),
            "triggerSummary": TRIGGER_SUMMARIES.get(key, ""),
            "monitoring": key in MONITORING_MESSAGE_TYPES,
            "system": key in SYSTEM_MESSAGE_TYPES,
            "cadenceMinutes": DEFAULT_CADENCE.get(key, 0),
            "enabledByDefault": DEFAULT_ALERT_RULES.get(key, 1),
        }
        for key in notification_message_types()
    }
