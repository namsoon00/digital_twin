from typing import Dict, List


MODEL_BUY = "modelBuy"
MODEL_SELL = "modelSell"
WATCHLIST_BUY_CANDIDATE = "watchlistBuyCandidate"
WATCHLIST_QUOTE = "watchlistQuote"
WATCHLIST_QUOTE_PENDING = "watchlistQuotePending"
WATCHLIST_ONTOLOGY_SIGNAL = "watchlistOntologySignal"
CRYPTO_ONTOLOGY_SIGNAL = "cryptoOntologySignal"
PORTFOLIO_ONTOLOGY_SIGNAL = "portfolioOntologySignal"
PORTFOLIO_REBALANCE_REVIEW = "portfolioRebalanceReview"
HOLDING_TIMING = "holdingTiming"
INVESTMENT_INSIGHT = "investmentInsight"
MARKET_OBSERVATION = "marketObservation"
ONTOLOGY_OBSERVATION_FOLLOWUP = "ontologyObservationFollowup"
PORTFOLIO_HOLDINGS_SNAPSHOT = "portfolioHoldingsSnapshot"
PORTFOLIO_ACTIVITY_OBSERVATION = "portfolioActivityObservation"
INVESTMENT_CALENDAR_REMINDER = "investmentCalendarReminder"
NEWS_DIGEST = "newsDigest"
ONTOLOGY_INFERENCE_MISSING = "ontologyInferenceMissing"
ONTOLOGY_REASONING_QUEUE = "ontologyReasoningQueue"
OPERATIONAL_STORAGE_CAPACITY = "operationalStorageCapacity"
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
OPERATOR_REASONING_REPORT = "operatorReasoningReport"
GENERIC_NOTIFICATION = "notification"
DEFAULT_MESSAGE = "default"

MONITORING_MESSAGE_TYPES = [
    INVESTMENT_INSIGHT,
    MARKET_OBSERVATION,
    PORTFOLIO_HOLDINGS_SNAPSHOT,
    PORTFOLIO_ACTIVITY_OBSERVATION,
    NEWS_DIGEST,
    WATCHLIST_ONTOLOGY_SIGNAL,
    CRYPTO_ONTOLOGY_SIGNAL,
    PORTFOLIO_ONTOLOGY_SIGNAL,
    PORTFOLIO_REBALANCE_REVIEW,
    HOLDING_TIMING,
    ONTOLOGY_INFERENCE_MISSING,
    ONTOLOGY_REASONING_QUEUE,
    OPERATIONAL_STORAGE_CAPACITY,
    MONITOR_HEARTBEAT,
    MONITOR_CONNECTION,
    EXTERNAL_DATA_CONNECTION,
]

SYSTEM_MESSAGE_TYPES = {
    DEFAULT_MESSAGE,
    MODEL_REVIEW,
    WORK_HANDOFF,
    OPERATOR_REASONING_REPORT,
    ONTOLOGY_REASONING_QUEUE,
    OPERATIONAL_STORAGE_CAPACITY,
    GENERIC_NOTIFICATION,
}

USER_MANAGED_NOTIFICATION_TYPES = [
    INVESTMENT_INSIGHT,
    MARKET_OBSERVATION,
    PORTFOLIO_HOLDINGS_SNAPSHOT,
    PORTFOLIO_ACTIVITY_OBSERVATION,
    INVESTMENT_CALENDAR_REMINDER,
    NEWS_DIGEST,
    ONTOLOGY_INFERENCE_MISSING,
    MONITOR_CONNECTION,
    EXTERNAL_DATA_CONNECTION,
]

SYSTEM_MANAGED_NOTIFICATION_TYPES = [
    MODEL_REVIEW,
    WORK_HANDOFF,
    OPERATOR_REASONING_REPORT,
    GENERIC_NOTIFICATION,
    ONTOLOGY_REASONING_QUEUE,
    OPERATIONAL_STORAGE_CAPACITY,
]

OPERATIONS_DELIVERY_MESSAGE_TYPES = {
    ONTOLOGY_INFERENCE_MISSING,
    ONTOLOGY_REASONING_QUEUE,
    OPERATIONAL_STORAGE_CAPACITY,
    MONITOR_HEARTBEAT,
    MONITOR_CONNECTION,
    EXTERNAL_DATA_CONNECTION,
    WORK_HANDOFF,
    OPERATOR_REASONING_REPORT,
}


def is_operations_delivery_message_type(message_type: object) -> bool:
    return str(message_type or "").strip() in OPERATIONS_DELIVERY_MESSAGE_TYPES

VISIBLE_NOTIFICATION_TEMPLATE_TYPES = [
    DEFAULT_MESSAGE,
    *USER_MANAGED_NOTIFICATION_TYPES,
    *SYSTEM_MANAGED_NOTIFICATION_TYPES,
]

MIN_CADENCE_MINUTES = 10

DEFAULT_ALERT_RULES = {
    INVESTMENT_INSIGHT: 1,
    MARKET_OBSERVATION: 1,
    PORTFOLIO_HOLDINGS_SNAPSHOT: 1,
    PORTFOLIO_ACTIVITY_OBSERVATION: 1,
    INVESTMENT_CALENDAR_REMINDER: 1,
    NEWS_DIGEST: 1,
    WATCHLIST_ONTOLOGY_SIGNAL: 1,
    CRYPTO_ONTOLOGY_SIGNAL: 1,
    PORTFOLIO_ONTOLOGY_SIGNAL: 1,
    PORTFOLIO_REBALANCE_REVIEW: 1,
    HOLDING_TIMING: 1,
    ONTOLOGY_INFERENCE_MISSING: 1,
    ONTOLOGY_REASONING_QUEUE: 1,
    OPERATIONAL_STORAGE_CAPACITY: 1,
    MONITOR_HEARTBEAT: 1,
    MONITOR_CONNECTION: 1,
    EXTERNAL_DATA_CONNECTION: 1,
}

EVIDENCE_ONLY_MESSAGE_TYPES = [
    WATCHLIST_ONTOLOGY_SIGNAL,
    CRYPTO_ONTOLOGY_SIGNAL,
    PORTFOLIO_ONTOLOGY_SIGNAL,
    PORTFOLIO_REBALANCE_REVIEW,
    HOLDING_TIMING,
    ONTOLOGY_OBSERVATION_FOLLOWUP,
]

DEFAULT_ALERT_THRESHOLDS = {
    # Raw market-observation delivery only. This does not produce an
    # investment action or replace the TypeDB materiality/inference path.
    "marketObservationPriceChangePct": 2.0,
    "volumeRatioHigh": 2,
    "buyShareHigh": 65,
    "sellShareHigh": 65,
    "orderbookImbalance": 25,
    "momentumUp": 3,
    "momentumDown": -3,
    "marketCashLow": 10,
    "priceNearPercent": 1,
    "staleMinutes": 30,
    "pendingOrderMinutes": 30,
}

DEFAULT_RELATION_RULE_THRESHOLDS = {
    "lossRateLow": -8,
    "lossRateBufferPct": 1,
    "lossGuardVolumeConfirmRatio": 0.8,
    "lossGuardMa60SupportPct": 0,
    "profitRateHigh": 20,
    "sectorWeightHigh": 50,
    "positionWeightHigh": 30,
    "externalBitcoinChange24hPct": 3,
    "externalBitcoinChange7dPct": 4,
    "entryPullbackMa20BelowPct": -2,
    "entryPullbackMa20DeepPct": -8,
    "entryMa5TimingMinPct": -0.5,
    "entryMomentumMa20MinPct": -0.5,
    "entryMomentumMa60MinPct": 0,
    "entryMa60SupportPct": -1,
    "entryVolumeMinRatio": 0.8,
    "entryVolumeMaxRatio": 1.8,
    "entrySmartMoneyMin": 10,
    "entryTradeStrengthMin": 100,
    "entryOrderbookImbalanceMin": 5,
    "entryMaxPositionWeight": 20,
    "entryMaxSectorWeight": 45,
    "macroRateDeltaBp": 15,
    "macroRateHighPct": 4.5,
    "macroRateLowPct": 3.0,
    "macroCurveInversionPct": 0,
    "usdKrwDeltaKrw": 15,
    "usdKrwDeltaPct": 1,
    "usdKrw7dDeltaKrw": 30,
    "usdKrw7dDeltaPct": 2,
    "usdKrwHigh": 1450,
    "usdKrwLow": 1300,
    "fxExposureReview": 5,
    "fxExposureHigh": 10,
    "newsDirectFreshMaxAgeMinutes": 1440,
}

DEFAULT_CADENCE = {
    INVESTMENT_INSIGHT: 10,
    MARKET_OBSERVATION: 60,
    PORTFOLIO_HOLDINGS_SNAPSHOT: 10,
    PORTFOLIO_ACTIVITY_OBSERVATION: 1,
    INVESTMENT_CALENDAR_REMINDER: 10,
    NEWS_DIGEST: 30,
    WATCHLIST_ONTOLOGY_SIGNAL: 10,
    CRYPTO_ONTOLOGY_SIGNAL: 10,
    PORTFOLIO_ONTOLOGY_SIGNAL: 10,
    PORTFOLIO_REBALANCE_REVIEW: 10080,
    HOLDING_TIMING: 10,
    ONTOLOGY_INFERENCE_MISSING: 60,
    ONTOLOGY_REASONING_QUEUE: 60,
    OPERATIONAL_STORAGE_CAPACITY: 60,
    MONITOR_HEARTBEAT: 10,
    MONITOR_CONNECTION: 10,
    EXTERNAL_DATA_CONNECTION: 60,
}

MESSAGE_TYPE_LABELS = {
    DEFAULT_MESSAGE: "기본 알림",
    INVESTMENT_INSIGHT: "투자 인사이트",
    MARKET_OBSERVATION: "시세 관측",
    PORTFOLIO_HOLDINGS_SNAPSHOT: "전체 보유 주식",
    PORTFOLIO_ACTIVITY_OBSERVATION: "실계좌 보유 변화",
    INVESTMENT_CALENDAR_REMINDER: "투자 캘린더",
    NEWS_DIGEST: "뉴스/피드 새 정보",
    MODEL_BUY: "모델 매수",
    MODEL_SELL: "모델 매도",
    WATCHLIST_BUY_CANDIDATE: "관심종목 매수 후보",
    WATCHLIST_QUOTE: "관심종목 시세",
    WATCHLIST_QUOTE_PENDING: "관심종목 시세 대기",
    WATCHLIST_ONTOLOGY_SIGNAL: "관심종목 관계 신호",
    CRYPTO_ONTOLOGY_SIGNAL: "크립토 관계 신호",
    PORTFOLIO_ONTOLOGY_SIGNAL: "포트폴리오 관계 신호",
    PORTFOLIO_REBALANCE_REVIEW: "포트폴리오 정기 리밸런싱",
    HOLDING_TIMING: "보유 타이밍",
    ONTOLOGY_OBSERVATION_FOLLOWUP: "시세 변화 관계 분석",
    ONTOLOGY_INFERENCE_MISSING: "온톨로지 추론 상태",
    ONTOLOGY_REASONING_QUEUE: "온톨로지 추론 대기열",
    OPERATIONAL_STORAGE_CAPACITY: "운영 저장공간",
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
    MODEL_REVIEW: "모델 리뷰",
    WORK_HANDOFF: "작업 완료",
    OPERATOR_REASONING_REPORT: "운영자 추론 보고서",
    GENERIC_NOTIFICATION: "일반 알림",
}

MESSAGE_TYPE_EMOJIS = {
    DEFAULT_MESSAGE: "🔔",
    INVESTMENT_INSIGHT: "🧭",
    MARKET_OBSERVATION: "📈",
    PORTFOLIO_HOLDINGS_SNAPSHOT: "📋",
    PORTFOLIO_ACTIVITY_OBSERVATION: "↔️",
    INVESTMENT_CALENDAR_REMINDER: "🗓️",
    NEWS_DIGEST: "🗞️",
    MODEL_BUY: "🟢",
    MODEL_SELL: "🔴",
    WATCHLIST_BUY_CANDIDATE: "🎯",
    WATCHLIST_QUOTE: "👀",
    WATCHLIST_QUOTE_PENDING: "⏳",
    WATCHLIST_ONTOLOGY_SIGNAL: "🧭",
    CRYPTO_ONTOLOGY_SIGNAL: "🪙",
    PORTFOLIO_ONTOLOGY_SIGNAL: "⚖️",
    PORTFOLIO_REBALANCE_REVIEW: "🔄",
    HOLDING_TIMING: "⚖️",
    ONTOLOGY_INFERENCE_MISSING: "⚠️",
    ONTOLOGY_REASONING_QUEUE: "⏳",
    OPERATIONAL_STORAGE_CAPACITY: "💾",
    MONITOR_HEARTBEAT: "💓",
    MONITOR_CONNECTION: "🔌",
    MONITOR_POSITION_CHANGE: "↔️",
    MONITOR_PNL_CHANGE: "📊",
    MONITOR_VALUE_CHANGE: "💵",
    MONITOR_TREND_CHANGE: "📊",
    MONITOR_CASH_CHANGE: "💵",
    MONITOR_DECISION_CHANGE: "🔁",
    EXTERNAL_EQUITY_MOVE: "🇺🇸",
    EXTERNAL_CRYPTO_MOVE: "🪙",
    EXTERNAL_MACRO_SHIFT: "🏦",
    EXTERNAL_DART_DISCLOSURE: "📄",
    EXTERNAL_DATA_CONNECTION: "🛰️",
    MODEL_REVIEW: "🧠",
    WORK_HANDOFF: "📦",
    OPERATOR_REASONING_REPORT: "🛠️",
    GENERIC_NOTIFICATION: "🔔",
}

TRIGGER_SUMMARIES = {
    INVESTMENT_INSIGHT: "온톨로지 관계 그래프에서 의미 있는 투자 인사이트가 생성될 때 보냅니다.",
    MARKET_OBSERVATION: "마지막 알림 기준 시세와 비교해 설정한 폭 이상 누적 변동한 원시 시세를 즉시 관측 알림으로 보냅니다. 매수·매도 판단은 TypeDB 추론 완료 후 별도로 보냅니다.",
    PORTFOLIO_HOLDINGS_SNAPSHOT: "강제 점검이나 수동 확인 요청에서 모든 보유 종목의 현재 상태를 한 번에 확인할 때 보냅니다.",
    PORTFOLIO_ACTIVITY_OBSERVATION: "완전한 실계좌 잔고 두 시점 사이에서 수량 또는 현금 변화가 확인되면 사실 알림으로 보냅니다.",
    INVESTMENT_CALENDAR_REMINDER: "등록한 투자 이벤트가 설정한 리마인더 시점에 도달하면 보냅니다. 투자 판단은 별도 온톨로지 인사이트로만 보냅니다.",
    NEWS_DIGEST: "보유/관심 종목에 신선도·관련성·중요도 기준을 통과한 새 뉴스나 피드 근거가 들어올 때 보냅니다.",
    MODEL_BUY: "관심 종목의 진입 조건이 새로 성립하고 자료 검증을 통과할 때 보냅니다.",
    MODEL_SELL: "보유 종목의 손실 관리 또는 비중 축소 조건이 새로 성립할 때 보냅니다.",
    WATCHLIST_BUY_CANDIDATE: "관심 종목의 가격·수급·가치 조건이 함께 확인되어 진입 검토 상태가 될 때 보냅니다.",
    WATCHLIST_QUOTE: "관심 종목의 시세와 추세 데이터가 갱신될 때 보냅니다.",
    WATCHLIST_QUOTE_PENDING: "관심 종목 시세를 아직 받지 못했을 때 보냅니다.",
    WATCHLIST_ONTOLOGY_SIGNAL: "관심 종목의 온톨로지 관계 규칙에서 진입, 회복, 리스크 신호가 생성될 때 보냅니다.",
    PORTFOLIO_ONTOLOGY_SIGNAL: "포트폴리오 위험·집중도 관계 규칙에서 리밸런싱 점검 신호가 생성될 때 보냅니다.",
    PORTFOLIO_REBALANCE_REVIEW: "주 1회 포트폴리오 전체 비중·현금·통화·위험을 함께 점검한 리밸런싱 보고서를 보냅니다. 중대한 정책 위반은 정기 주기를 기다리지 않고 별도 포트폴리오 관계 신호로 보냅니다.",
    HOLDING_TIMING: "보유 종목의 매수·매도 점검 데이터가 기준에 걸릴 때 보냅니다.",
    ONTOLOGY_INFERENCE_MISSING: "실계좌 데이터는 있지만 그래프 저장소 InferenceBox 추론 결과가 없어 매수·매도 판단을 만들지 못할 때 보냅니다.",
    ONTOLOGY_REASONING_QUEUE: "온톨로지 추론 요청이 지연·차단되거나 정상 처리 상태로 복구될 때 운영 채널에 보냅니다.",
    OPERATIONAL_STORAGE_CAPACITY: "디스크 여유 또는 TypeDB·MySQL·로그 저장공간 한도에 도달하거나 정상 범위로 복구될 때 운영 채널에 보냅니다.",
    MONITOR_HEARTBEAT: "실시간 모니터링 워커가 정상 작동 중인지 확인할 때 보냅니다.",
    MONITOR_CONNECTION: "Toss 연결 상태가 바뀔 때 보냅니다.",
    MONITOR_POSITION_CHANGE: "새 보유, 제외, 수량 변경이 감지될 때 보냅니다.",
    MONITOR_PNL_CHANGE: "직전 기록과 비교해 손익률 변화가 기준값을 넘을 때 보냅니다.",
    MONITOR_VALUE_CHANGE: "직전 기록과 비교해 평가액 변화가 기준값을 넘을 때 보냅니다.",
    MONITOR_TREND_CHANGE: "이동평균 돌파, 크로스, 현재가와 이동평균 차이가 커질 때 보냅니다.",
    MONITOR_CASH_CHANGE: "시장별 현금 비중 변화가 기준값을 넘을 때 보냅니다.",
    MONITOR_DECISION_CHANGE: "종목의 권장 행동, 확인 단계 또는 근거 방향이 바뀔 때 보냅니다.",
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


def user_managed_notification_types(include_system: bool = False) -> List[str]:
    keys = list(USER_MANAGED_NOTIFICATION_TYPES)
    if include_system:
        keys.extend(SYSTEM_MANAGED_NOTIFICATION_TYPES)
    return list(dict.fromkeys(keys))


def visible_notification_template_types() -> List[str]:
    return list(dict.fromkeys(VISIBLE_NOTIFICATION_TEMPLATE_TYPES))


def notification_type_role(key: str) -> str:
    normalized = str(key or "").strip()
    if normalized in USER_MANAGED_NOTIFICATION_TYPES:
        return "user"
    if normalized in SYSTEM_MANAGED_NOTIFICATION_TYPES or normalized == DEFAULT_MESSAGE:
        return "system"
    if normalized in EVIDENCE_ONLY_MESSAGE_TYPES:
        return "evidence"
    return "internal"


def public_message_catalog() -> Dict[str, Dict[str, object]]:
    return {
        key: {
            "label": MESSAGE_TYPE_LABELS.get(key, key),
            "icon": MESSAGE_TYPE_EMOJIS.get(key, "🔔"),
            "triggerSummary": TRIGGER_SUMMARIES.get(key, ""),
            "monitoring": key in MONITORING_MESSAGE_TYPES,
            "system": key in SYSTEM_MESSAGE_TYPES,
            "role": notification_type_role(key),
            "userManaged": key in USER_MANAGED_NOTIFICATION_TYPES,
            "templateManaged": key in VISIBLE_NOTIFICATION_TEMPLATE_TYPES,
            "evidenceOnly": key in EVIDENCE_ONLY_MESSAGE_TYPES,
            "cadenceMinutes": DEFAULT_CADENCE.get(key, 0),
            "enabledByDefault": DEFAULT_ALERT_RULES.get(key, 1),
        }
        for key in notification_message_types()
    }
