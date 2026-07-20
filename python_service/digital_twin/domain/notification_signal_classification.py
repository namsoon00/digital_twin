"""Classify notification evidence for delivery diagnostics.

This module only labels the kinds of data included in a notification. It does
not calculate a composite investment judgement or rank a buy/sell decision.
"""

from typing import Iterable, List


IMPORTANT_NOTIFICATION_RULES = {
    "investmentInsight",
    "newsDigest",
    "holdingTiming",
    "watchlistOntologySignal",
}

ACTIONABLE_NOTIFICATION_RULES = {
    "investmentInsight",
    "newsDigest",
    "holdingTiming",
    "watchlistOntologySignal",
    "ontologyInferenceMissing",
    "externalDataConnection",
}

LOW_SIGNAL_NOTIFICATION_RULES = {
    "monitorHeartbeat",
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
    "뉴스",
    "기사",
    "출처",
}

DEFAULT_SIGNAL_FALLBACK_TERMS = {
    "important_terms": [
        "온톨로지",
        "관계 신호",
        "InferenceBox",
        "RuleBox",
        "성립 규칙",
        "관계 강도",
        "신규 관계",
        "상충 신호",
        "신규 공시",
        "손절",
        "분할매도",
        "리스크",
        "위험",
    ],
    "confirming_data": ["수급", "거래량", "투자자", "추세", "20일선", "60일선", "외국인", "기관"],
    "actionable_terms": ["확인", "재확인", "점검", "기준", "후보", "검토"],
    "status_noise": ["정상 작동", "시세 대기", "현재가를 아직", "연결 확인 필요", "템플릿 테스트"],
}


def notification_signal_categories(rule: str, raw_lines: Iterable[str]) -> List[str]:
    labels = {signal_data_label(line) for line in raw_lines}
    categories: List[str] = []
    if str(rule or "").strip() in IMPORTANT_NOTIFICATION_RULES:
        categories.append("important")
    if labels.intersection(CONFIRMING_DATA_LABELS):
        categories.append("confirmingData")
    if str(rule or "").strip() in ACTIONABLE_NOTIFICATION_RULES:
        categories.append("actionable")
    if str(rule or "").strip() in LOW_SIGNAL_NOTIFICATION_RULES:
        categories.append("statusNoise")
    return categories


def signal_data_label(line: object) -> str:
    text = str(line or "").strip()
    for label in sorted(CONFIRMING_DATA_LABELS, key=len, reverse=True):
        if text.startswith(label + ": ") or text.startswith(label + " "):
            return label
    return ""


def fallback_terms_for_delivery_condition(condition_id: str) -> List[str]:
    return list(DEFAULT_SIGNAL_FALLBACK_TERMS.get(str(condition_id or ""), []))
