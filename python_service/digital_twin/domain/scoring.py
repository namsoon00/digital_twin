from typing import Iterable, List


IMPORTANT_NOTIFICATION_RULES = {
    "modelBuy",
    "modelSell",
    "watchlistBuyCandidate",
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

ACTIONABLE_NOTIFICATION_RULES = {
    "modelBuy",
    "modelSell",
    "watchlistBuyCandidate",
    "holdingTiming",
    "ontologyInferenceMissing",
    "monitorDecisionChange",
    "watchlistQuote",
    "externalDataConnection",
}

LOW_SIGNAL_NOTIFICATION_RULES = {
    "monitorHeartbeat",
    "watchlistQuotePending",
    "externalDataConnection",
}

CONFIRMING_DATA_LABELS = {
    "수급",
    "거래량",
    "거래액",
    "투자자",
    "추세",
    "기울기",
    "신호",
    "미장 가격 변동",
    "비트코인 변동",
    "크립토 변동",
    "크립토 가격",
    "크립토 거래액",
}

DEFAULT_SIGNAL_FALLBACK_TERMS = {
    "important_terms": [
        "판단 변화",
        "모델 매수",
        "관심종목 매수",
        "모델 매도",
        "내 매수 기준",
        "내 매도 기준",
        "손익률 급변",
        "평가액 급변",
        "보유 수량 변경",
        "새 보유",
        "이동평균",
        "신규 공시",
        "가격 변동",
        "크립토 변동",
        "거시 지표",
        "손절",
        "분할매도",
        "리스크",
        "위험",
    ],
    "confirming_data": ["수급", "거래량", "투자자", "추세", "20일선", "60일선", "외국인", "기관"],
    "actionable_terms": ["확인", "재확인", "점검", "기준", "후보", "검토"],
    "status_noise": ["정상 작동", "시세 대기", "현재가를 아직", "연결 확인 필요", "템플릿 테스트"],
}


def notification_signal_labels(rule: str, raw_lines: Iterable[str]) -> List[str]:
    labels = {scoring_data_label(line) for line in raw_lines}
    signals: List[str] = []
    if str(rule or "").strip() in IMPORTANT_NOTIFICATION_RULES:
        signals.append("important")
    if labels.intersection(CONFIRMING_DATA_LABELS):
        signals.append("confirmingData")
    if str(rule or "").strip() in ACTIONABLE_NOTIFICATION_RULES:
        signals.append("actionable")
    if str(rule or "").strip() in LOW_SIGNAL_NOTIFICATION_RULES:
        signals.append("statusNoise")
    return signals


def scoring_data_label(line: object) -> str:
    text = str(line or "").strip()
    for label in sorted(CONFIRMING_DATA_LABELS, key=len, reverse=True):
        if text.startswith(label + ": ") or text.startswith(label + " "):
            return label
    return ""


def fallback_terms_for_condition(condition_id: str) -> List[str]:
    return list(DEFAULT_SIGNAL_FALLBACK_TERMS.get(str(condition_id or ""), []))
