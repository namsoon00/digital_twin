import html
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from .alert_formatting import signed_pct
from .message_types import MESSAGE_TYPE_EMOJIS, MESSAGE_TYPE_LABELS, TRIGGER_SUMMARIES
from .notification_ai import enrich_notification_ai_context
from .ontology_rules import relation_score_meaning
from .portfolio import AlertEvent
from .scoring import notification_signal_labels


LEGACY_DEFAULT_TEMPLATE = "{title}\n{lines}"
PREVIOUS_DEFAULT_TEMPLATE = "{readableMessage}"
DEFAULT_TEMPLATE = "{telegramMessage}"
BODY_TEMPLATE = "{body}"
KST = timezone(timedelta(hours=9))
SYMBOL_DISPLAY_NAMES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "035720": "카카오",
    "005380": "현대차",
    "000020": "동화약품",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "AMD": "AMD",
    "TSLA": "Tesla",
    "MSTR": "Strategy",
    "STRC": "Strategy Preferred",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "BTC": "비트코인",
    "ETH": "이더리움",
}
DATA_LABEL_PREFIXES = [
    "미장 가격 변동",
    "비트코인 변동",
    "크립토 변동",
    "크립토 가격",
    "크립토 거래액",
    "매수 판단",
    "매도 판단",
    "모델 매수 점수",
    "모델 매도 점수",
    "적정가 대비",
    "24h 거래액",
    "현재가",
    "평균매입가",
    "평단가",
    "수익률",
    "보유 수량",
    "매도가능 수량",
    "종목 평가금액",
    "계좌 평가금액",
    "기준일",
    "발송시각",
    "연속 실패",
    "실패 단계",
    "재시도",
    "투자자",
    "기울기",
    "확인 행동",
    "권장 액션",
    "거래량",
    "거래액",
    "가격",
    "수급",
    "추세",
    "출처",
    "이전",
    "현재",
    "변화",
    "상태",
    "손익",
    "평가",
    "보유",
    "신호",
]

DATA_LABEL_ORDER = {
    "상태": 10,
    "연속 실패": 11,
    "실패 단계": 12,
    "재시도": 13,
    "손익": 20,
    "미장 가격 변동": 20,
    "현재가": 21,
    "평균매입가": 22,
    "평단가": 22,
    "수익률": 23,
    "보유 수량": 24,
    "매도가능 수량": 25,
    "종목 평가금액": 26,
    "계좌 평가금액": 27,
    "매수 판단": 25,
    "매도 판단": 26,
    "수급": 30,
    "거래량": 31,
    "거래액": 32,
    "추세": 40,
    "확인 행동": 41,
    "권장 액션": 41,
    "기울기": 45,
    "투자자": 50,
    "신호": 60,
    "비트코인 변동": 70,
    "크립토 변동": 71,
    "크립토 가격": 72,
    "크립토 거래액": 73,
    "출처": 88,
    "기준일": 89,
    "발송시각": 90,
}

SEPARATE_DATA_LABELS = {
    "상태",
    "연속 실패",
    "실패 단계",
    "재시도",
    "손익",
    "미장 가격 변동",
    "현재가",
    "평균매입가",
    "평단가",
    "수익률",
    "보유 수량",
    "매도가능 수량",
    "종목 평가금액",
    "계좌 평가금액",
    "매수 판단",
    "매도 판단",
    "수급",
    "추세",
    "확인 행동",
    "권장 액션",
    "기울기",
    "투자자",
    "신호",
    "거래량",
    "거래액",
    "비트코인 변동",
    "크립토 변동",
    "크립토 가격",
    "크립토 거래액",
    "출처",
    "기준일",
    "발송시각",
    "평가",
    "보유",
}
ONTOLOGY_INTERNAL_DATA_PREFIXES = (
    "관계 신호",
    "성립 규칙",
    "AI 질문",
    "온톨로지:",
    "온톨로지 판단",
    "thesis:",
    "판단 근거:",
    "관계 충돌:",
    "주요 위험:",
    "부족 데이터 ",
)

SEVERITY_LABELS = {
    "INFO": "정보",
    "WATCH": "관찰",
    "ALERT": "주의",
}

SCORE_EXPLANATION_SKIP_TYPES = {
    "workHandoff",
}

SCORE_VALUE_PATTERN = re.compile(r"\d+(?:\.\d+)?점")

DEFAULT_NOTIFICATION_TEMPLATES = {
    "default": {
        "template": DEFAULT_TEMPLATE,
        "description": "기본 알림 템플릿. title, lines, body 변수를 사용할 수 있습니다.",
    },
    "investmentInsight": {
        "template": DEFAULT_TEMPLATE,
        "description": "온톨로지 관계 인사이트 기반 투자 알림",
    },
    "modelBuy": {
        "template": DEFAULT_TEMPLATE,
        "description": "모델 매수 조건 알림",
    },
    "modelSell": {
        "template": DEFAULT_TEMPLATE,
        "description": "모델 매도 조건 알림",
    },
    "watchlistBuyCandidate": {
        "template": DEFAULT_TEMPLATE,
        "description": "관심종목 매수 후보 알림",
    },
    "watchlistQuote": {
        "template": DEFAULT_TEMPLATE,
        "description": "관심종목 시세 수집 알림",
    },
    "watchlistQuotePending": {
        "template": DEFAULT_TEMPLATE,
        "description": "관심종목 시세 대기 알림",
    },
    "watchlistOntologySignal": {
        "template": DEFAULT_TEMPLATE,
        "description": "관심종목 온톨로지 관계 신호 알림",
    },
    "holdingTiming": {
        "template": DEFAULT_TEMPLATE,
        "description": "보유 종목 타이밍 점검 알림",
    },
    "monitorHeartbeat": {
        "template": DEFAULT_TEMPLATE,
        "description": "실시간 모니터링 상태 알림",
    },
    "monitorConnection": {
        "template": DEFAULT_TEMPLATE,
        "description": "토스 연결 상태 변화 알림",
    },
    "monitorPositionChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "보유 종목/수량 변화 알림",
    },
    "monitorPnlChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "손익률 변화 알림",
    },
    "monitorValueChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "평가액 변화 알림",
    },
    "monitorTrendChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "이동평균/추세 변화 알림",
    },
    "monitorCashChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "현금 비중 변화 알림",
    },
    "monitorDecisionChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "매수/매도 판단 변화 알림",
    },
    "externalEquityMove": {
        "template": DEFAULT_TEMPLATE,
        "description": "Alpha Vantage 기반 미장 가격/거래량 변동 알림",
    },
    "externalCryptoMove": {
        "template": DEFAULT_TEMPLATE,
        "description": "CoinGecko 기반 크립토 변동 알림",
    },
    "externalMacroShift": {
        "template": DEFAULT_TEMPLATE,
        "description": "FRED 기반 금리/스프레드 변화 알림",
    },
    "externalDartDisclosure": {
        "template": DEFAULT_TEMPLATE,
        "description": "OpenDART 기반 국내 종목 공시 변화 알림",
    },
    "externalDataConnection": {
        "template": DEFAULT_TEMPLATE,
        "description": "외부 데이터 API 연결 상태 알림",
    },
    "modelReview": {
        "template": BODY_TEMPLATE,
        "description": "비동기 모델 리뷰 결과 알림",
    },
    "workHandoff": {
        "template": BODY_TEMPLATE,
        "description": "작업 완료 핸드오프 알림",
    },
    "notification": {
        "template": BODY_TEMPLATE,
        "description": "일반 텍스트 알림",
    },
}

PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
BEGINNER_FRIENDLY_REPLACEMENTS = [
    ("opinion assertion", "의견 기록"),
    ("supporting-evidence", "보조 근거"),
    ("legacyModelRole", "기존 모델 역할"),
    ("온톨로지 판단", "관계 판단"),
    ("온톨로지 컨텍스트", "관계 분석 정보"),
    ("온톨로지 그래프", "관계 분석 데이터"),
    ("온톨로지", "관계 분석"),
    ("세계관 집중도", "관련 종목 비중"),
    ("세계관", "투자 관점"),
    ("손실 thesis 재검증", "손실 구간 보유 이유 재확인"),
    ("thesis 충돌", "보유 이유와 충돌"),
    ("thesis 훼손", "보유 이유 약화"),
    ("보유 thesis", "보유 이유"),
    ("종목 thesis", "종목 보유 이유"),
    ("기존 thesis", "기존 보유 이유"),
    ("thesis", "보유 이유"),
    ("evidence", "근거"),
    ("belief", "판단 근거"),
    ("assertion", "기록"),
    ("legacy score", "기존 점수"),
    ("legacy", "기존"),
    ("증거", "근거"),
    ("컨텍스트", "정보"),
    ("가설", "설명"),
]


def beginner_friendly_text(value: object) -> str:
    text = str(value or "")
    for before, after in BEGINNER_FRIENDLY_REPLACEMENTS:
        text = text.replace(before, after)
    return text


def plain_bullet(text: str) -> str:
    cleaned = beginner_friendly_text(text).strip()
    return "• " + cleaned if cleaned else ""


def html_bullet(text: str) -> str:
    cleaned = beginner_friendly_text(text).strip()
    return "• " + html.escape(cleaned, quote=False) if cleaned else ""


def split_label_value(text: str):
    cleaned = beginner_friendly_text(text).strip()
    if ": " not in cleaned:
        return "", cleaned
    label, value = cleaned.split(": ", 1)
    if 0 < len(label.strip()) <= 18 and value.strip():
        return label.strip(), value.strip()
    return "", cleaned


def criterion_row(text: str, rich: bool = False) -> str:
    label, value = split_label_value(text)
    if label and value:
        if rich:
            return "• <b>" + html.escape(label, quote=False) + "</b>: <code>" + html.escape(value, quote=False) + "</code>"
        return "• " + label + ": " + value
    return html_bullet(text) if rich else plain_bullet(text)


def criterion_rows(items: List[str], rich: bool = False) -> str:
    return "\n".join(criterion_row(item, rich) for item in items if str(item or "").strip())


def split_data_line(line: str):
    text = beginner_friendly_text(line).strip()
    for label in DATA_LABEL_PREFIXES:
        colon_prefix = label + ": "
        if text.startswith(colon_prefix):
            value = text[len(colon_prefix):].strip()
            if value:
                return label, value
        prefix = label + " "
        if text.startswith(prefix):
            value = text[len(prefix):].strip()
            if value:
                return label, value
    return "", text


def ordered_data_entries(raw_lines: List[str]) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    for index, line in enumerate(raw_lines):
        label, value = split_data_line(line)
        if label and value:
            entries.append({
                "kind": "pair",
                "label": label,
                "value": value,
                "index": index,
                "order": DATA_LABEL_ORDER.get(label, 100 + index),
            })
        else:
            entries.append({
                "kind": "text",
                "text": str(line or "").strip(),
                "index": index,
                "order": 100 + index,
            })
    return sorted(entries, key=lambda item: (int(item["order"]), int(item["index"])))


def is_ontology_internal_data_line(line: str) -> bool:
    text = str(line or "").strip()
    return any(text.startswith(prefix) for prefix in ONTOLOGY_INTERNAL_DATA_PREFIXES)


def notification_data_lines(raw_lines: List[str], metadata: Dict[str, object]) -> List[str]:
    if not ontology_relation_context(metadata):
        return list(raw_lines)
    return [line for line in raw_lines if not is_ontology_internal_data_line(line)]


def data_pair_text(label: str, value: str, rich: bool = False) -> str:
    label = beginner_friendly_text(label)
    value = beginner_friendly_text(value)
    if rich:
        return "<b>" + html.escape(label, quote=False) + "</b>: <code>" + html.escape(value, quote=False) + "</code>"
    return label + ": " + value


def formatted_data_rows(raw_lines: List[str], rich: bool = False) -> str:
    rows: List[str] = []
    pairs: List[str] = []

    def flush_pairs():
        nonlocal pairs
        if pairs:
            rows.extend(grouped_data_rows(pairs))
            pairs = []

    for entry in ordered_data_entries(raw_lines):
        if entry.get("kind") == "pair":
            label = str(entry.get("label") or "")
            value = str(entry.get("value") or "")
            pair_text = data_pair_text(label, value, rich)
            if label in SEPARATE_DATA_LABELS:
                flush_pairs()
                rows.append("• " + pair_text)
            else:
                pairs.append(pair_text)
            continue
        flush_pairs()
        text = str(entry.get("text") or "")
        rows.append(html_bullet(text) if rich else plain_bullet(text))
    flush_pairs()
    return "\n".join(row for row in rows if row)


def plain_data_rows(raw_lines: List[str]) -> str:
    return formatted_data_rows(raw_lines, False)


def telegram_data_rows(raw_lines: List[str]) -> str:
    return formatted_data_rows(raw_lines, True)


def grouped_data_rows(items: List[str], per_row: int = 2) -> List[str]:
    rows: List[str] = []
    for index in range(0, len(items), per_row):
        rows.append("• " + ", ".join(items[index:index + per_row]))
    return rows


def data_value(raw_lines: List[str], label: str) -> str:
    for line in raw_lines:
        parsed_label, value = split_data_line(line)
        if parsed_label == label and value:
            return value
    return ""


def signed_direction(value: str) -> int:
    match = re.search(r"([+-])\s*\d", str(value or ""))
    if not match:
        return 0
    return 1 if match.group(1) == "+" else -1


def dominant_signed_direction(value: str) -> int:
    signed_values: List[float] = []
    for match in re.finditer(r"([+-])\s*(\d+(?:\.\d+)?)", str(value or "")):
        sign = -1 if match.group(1) == "-" else 1
        signed_values.append(sign * float(match.group(2)))
    if not signed_values:
        return 0
    dominant = max(signed_values, key=lambda item: abs(item))
    if dominant > 0:
        return 1
    if dominant < 0:
        return -1
    return 0


def title_from_change(value: str, positive: str, negative: str, neutral: str) -> str:
    direction = dominant_signed_direction(value)
    if direction > 0:
        return positive
    if direction < 0:
        return negative
    return neutral


def first_data_text(raw_lines: List[str], pattern: str) -> str:
    compiled = re.compile(pattern)
    for line in raw_lines:
        text = str(line or "").strip()
        if compiled.search(text):
            return text
    return ""


def text_parts_from_value(value: object) -> List[str]:
    if isinstance(value, dict):
        parts: List[str] = []
        for nested in value.values():
            parts.extend(text_parts_from_value(nested))
        return parts
    if isinstance(value, list):
        parts = []
        for nested in value:
            parts.extend(text_parts_from_value(nested))
        return parts
    text = str(value or "").strip()
    return [text] if text else []


def investment_insight_signal_blob(raw_lines: List[str], event: AlertEvent) -> str:
    metadata = dict(getattr(event, "metadata", {}) or {})
    parts: List[str] = list(raw_lines) + [str(getattr(event, "title", "") or "")]
    for key in ["ontologyInsight", "insight", "notificationAiOpinion"]:
        value = metadata.get(key)
        if isinstance(value, dict):
            parts.extend(text_parts_from_value(value))
    for source_event in metadata.get("sourceAlertEvents") or []:
        if isinstance(source_event, dict):
            parts.extend(text_parts_from_value({
                "rule": source_event.get("rule"),
                "title": source_event.get("title"),
                "message": source_event.get("message"),
                "lines": source_event.get("lines"),
                "criteria": source_event.get("criteria"),
            }))
    relation_context = ontology_relation_context(metadata)
    if relation_context:
        parts.extend(text_parts_from_value({
            "decision": relation_context.get("decision"),
            "activeRules": relation_context.get("activeRules"),
        }))
    return " ".join(part for part in parts if part)


def investment_insight_decision_blob(raw_lines: List[str], event: AlertEvent) -> str:
    metadata = dict(getattr(event, "metadata", {}) or {})
    parts: List[str] = [
        data_value(raw_lines, "상태"),
        data_value(raw_lines, "손익") or data_value(raw_lines, "수익률"),
        data_value(raw_lines, "권장 액션"),
        data_value(raw_lines, "인사이트 유형"),
        data_value(raw_lines, "핵심 결론"),
        str(getattr(event, "title", "") or ""),
    ]
    relation_context = ontology_relation_context(metadata)
    if relation_context:
        parts.extend(text_parts_from_value({
            "decision": relation_context.get("decision"),
            "activeRules": relation_context.get("activeRules"),
        }))
    return " ".join(part for part in parts if part)


def percent_text(value: str) -> str:
    text = str(value or "").strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?%", text)
    return match.group(0) if match else text


def compact_action_title(value: str) -> str:
    text = str(value or "").strip()
    for separator in [",", "·", "/", ";"]:
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    if len(text) > 28:
        text = text[:28].rstrip() + "..."
    return text


def has_investment_loss_signal(value: str) -> bool:
    return any(term in str(value or "") for term in ["손절", "손실", "분할축소", "추가매수 보류", "신규 진입 보류", "하락 가속", "riskWatch", "loss_guard", "LOSS", "entry.add_buy.blocked", "trend.breakdown_acceleration"])


def has_investment_profit_signal(value: str) -> bool:
    return any(term in str(value or "") for term in ["분할매도", "익절", "수익", "리밸런싱", "profit_take", "PROFIT"])


def notification_title_icon(rule: str, raw_lines: List[str], event: AlertEvent) -> str:
    key = str(rule or "")
    status = data_value(raw_lines, "상태")
    profit = data_value(raw_lines, "손익") or data_value(raw_lines, "수익률")
    change = data_value(raw_lines, "변화")
    signal = data_value(raw_lines, "신호")
    title_text = str(getattr(event, "title", "") or "")

    if key in {"modelBuy", "watchlistBuyCandidate"}:
        return "🟢"
    if key == "watchlistOntologySignal":
        return "🧭"
    if key == "investmentInsight":
        blob = investment_insight_signal_blob(raw_lines, event)
        decision_blob = investment_insight_decision_blob(raw_lines, event)
        if any(term in blob for term in ["분할매수", "매수 후보", "기회 후보", "opportunityDetected", "watchlistBuyCandidate", "entry.pullback.supported"]):
            return "🟢"
        if signed_direction(profit) < 0 and has_investment_loss_signal(decision_blob):
            return "🛡️"
        if has_investment_profit_signal(decision_blob):
            return "💰"
        if has_investment_loss_signal(decision_blob):
            return "🛡️"
        if "외부" in blob:
            return "🌐"
        return "🧭"
    if key == "modelSell":
        return "🔴"
    if key == "holdingTiming":
        status_blob = " ".join([status, profit, title_text]).strip()
        if any(term in status_blob for term in ["손절", "손실"]) or signed_direction(profit) < 0:
            return "🛡️"
        if any(term in status_blob for term in ["분할", "익절", "수익"]):
            return "💰"
        return "⚖️"
    if key == "monitorPnlChange":
        return "📈" if dominant_signed_direction(change) > 0 else "📉" if dominant_signed_direction(change) < 0 else "📊"
    if key == "monitorValueChange":
        return "💵" if dominant_signed_direction(change) >= 0 else "💸"
    if key == "monitorTrendChange":
        if "하향" in signal or "이탈" in signal:
            return "📉"
        if "상향" in signal or "돌파" in signal:
            return "📈"
        return "📊"
    if key == "monitorDecisionChange":
        current = data_value(raw_lines, "현재")
        action = data_value(raw_lines, "권장 액션")
        decision_blob = " ".join([current, action])
        if any(term in decision_blob for term in ["손절", "손실", "축소"]):
            return "🛡️"
        if any(term in decision_blob for term in ["분할", "익절", "수익"]):
            return "💰"
        if "리밸런싱" in decision_blob:
            return "⚖️"
        return "🔁"
    if key == "externalCryptoMove":
        return "🪙"
    return MESSAGE_TYPE_EMOJIS.get(key, "🔔")


def notification_title_headline(rule: str, raw_lines: List[str], event: AlertEvent, fallback: str) -> str:
    key = str(rule or "")
    status = data_value(raw_lines, "상태")
    profit = data_value(raw_lines, "손익") or data_value(raw_lines, "수익률")
    change = data_value(raw_lines, "변화")
    signal = data_value(raw_lines, "신호")
    title_text = str(getattr(event, "title", "") or "")
    symbol = str(getattr(event, "symbol", "") or "").upper()

    if key in {"modelBuy", "watchlistBuyCandidate"}:
        return "매수 후보 감지"
    if key == "investmentInsight":
        insight_type = data_value(raw_lines, "인사이트 유형")
        blob = investment_insight_signal_blob(raw_lines, event)
        decision_blob = investment_insight_decision_blob(raw_lines, event)
        profit_text = percent_text(profit)
        if any(term in blob for term in ["분할매수", "매수 후보", "기회 후보", "opportunityDetected", "watchlistBuyCandidate", "entry.pullback.supported"]):
            return "분할매수 후보: 진입 조건 점검"
        if signed_direction(profit) < 0 and has_investment_loss_signal(decision_blob):
            return ("손실 " + profit_text + ": " if profit_text and signed_direction(profit) < 0 else "") + "손절·분할축소 점검"
        if has_investment_profit_signal(decision_blob):
            return ("수익 " + profit_text + ": " if profit_text and signed_direction(profit) > 0 else "") + "분할매도·리밸런싱 점검"
        if has_investment_loss_signal(decision_blob):
            return ("손실 " + profit_text + ": " if profit_text and signed_direction(profit) < 0 else "") + "손절·분할축소 점검"
        if "외부" in blob or "외부" in insight_type:
            return "외부 신호: 보유 영향 점검"
        if any(term in blob for term in ["매수", "기회"]):
            return "매수 후보: 진입 조건 점검"
        if insight_type:
            return insight_type + ": 대응 기준 점검"
        return "투자 인사이트: 대응 기준 점검"
    if key == "modelSell":
        return "매도 기준 점검"
    if key == "watchlistQuote":
        return "관심종목 시세 갱신"
    if key == "watchlistQuotePending":
        return "관심종목 시세 미수집"
    if key == "holdingTiming":
        status_blob = " ".join([status, profit, title_text]).strip()
        if any(term in status_blob for term in ["손절", "손실"]) or signed_direction(profit) < 0:
            profit_text = percent_text(profit)
            return ("손실 " + profit_text + ": " if profit_text else "") + "손절·분할축소 권장"
        if any(term in status_blob for term in ["분할", "익절", "수익"]):
            profit_text = percent_text(profit)
            return ("수익 " + profit_text + ": " if profit_text else "") + "분할매도 권장"
        if "조건부" in status_blob:
            return "조건부 보유: 추가매수 보류"
        return "보유 판단: 유지·대기"
    if key == "monitorHeartbeat":
        return "모니터링 상태 확인"
    if key == "monitorConnection":
        status_blob = " ".join([status, " ".join(raw_lines[:2])])
        if any(term in status_blob.lower() for term in ["실패", "오류", "unauthorized", "forbidden", "timeout", "error"]):
            return "토스 연결 오류"
        return "토스 연결 상태 변경"
    if key == "monitorPositionChange":
        body = " ".join(raw_lines)
        if "신규" in body:
            return "신규 보유 감지"
        if any(term in body for term in ["제외", "청산", "매도 완료"]):
            return "보유 제외 감지"
        return "보유 수량 변경"
    if key == "monitorPnlChange":
        return title_from_change(change, "손익률 개선", "손익률 악화", "손익률 변화")
    if key == "monitorValueChange":
        return title_from_change(change, "평가액 증가", "평가액 감소", "평가액 변화")
    if key == "monitorTrendChange":
        if "하향" in signal or "이탈" in signal:
            return "이동평균 하향 신호"
        if "상향" in signal or "돌파" in signal:
            return "이동평균 상향 신호"
        return "이동평균·추세 신호"
    if key == "monitorCashChange":
        return title_from_change(change, "현금 비중 증가", "현금 비중 감소", "현금 비중 변화")
    if key == "monitorDecisionChange":
        current = data_value(raw_lines, "현재")
        action = compact_action_title(data_value(raw_lines, "권장 액션"))
        if action:
            return "판단 변경: " + action
        if any(term in current for term in ["손절", "손실"]):
            return "판단 변경: 손절·분할축소 권장"
        if any(term in current for term in ["분할", "익절", "수익"]):
            return "판단 변경: 분할매도 권장"
        if "리밸런싱" in current:
            return "판단 변경: 리밸런싱 권장"
        if "보유" in current:
            return "판단 변경: 보유 유지"
        return "판단 변경: 대응 액션 변경"
    if key == "externalEquityMove":
        equity_change = data_value(raw_lines, "미장 가격 변동")
        return title_from_change(equity_change, "미장 가격 급등", "미장 가격 급락", "미장 가격·거래량 급변")
    if key == "externalCryptoMove":
        metadata = dict(getattr(event, "metadata", {}) or {})
        crypto_model = metadata.get("cryptoMoveModel") if isinstance(metadata.get("cryptoMoveModel"), dict) else {}
        model_title = str(crypto_model.get("titleLabel") or metadata.get("cryptoMoveTitle") or "").strip()
        if model_title:
            return model_title
        crypto_line = first_data_text(raw_lines, r"(비트코인|크립토).*?(24h|7d)")
        asset = "비트코인" if "비트코인" in crypto_line or symbol == "BTC" else "크립토"
        return title_from_change(crypto_line, asset + " 가격 급등", asset + " 가격 급락", asset + " 가격 급변")
    if key == "externalMacroShift":
        return "금리·거시 지표 변화"
    if key == "externalDartDisclosure":
        return "국내 공시 감지"
    if key == "externalDataConnection":
        provider = raw_lines[0] if raw_lines else ""
        return (provider + " 연결 점검").strip() if provider else "외부 API 연결 점검"
    return fallback or title_text or key


def event_generated_at(event: AlertEvent) -> str:
    metadata = dict(getattr(event, "metadata", {}) or {})
    return str(
        getattr(event, "generated_at", "")
        or getattr(event, "generatedAt", "")
        or metadata.get("generatedAt")
        or metadata.get("updatedAt")
        or metadata.get("asOf")
        or ""
    ).strip()


def current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def reference_date_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    except ValueError:
        return text


def raw_lines_with_reference_date(event: AlertEvent, raw_lines: List[str]) -> List[str]:
    if data_value(raw_lines, "기준일"):
        return list(raw_lines)
    reference_date = reference_date_text(event_generated_at(event) or current_utc_iso())
    if not reference_date:
        return list(raw_lines)
    return list(raw_lines) + ["기준일 " + reference_date]


def first_line_containing(raw_lines: List[str], terms: List[str]) -> str:
    for line in raw_lines:
        text = str(line or "").strip()
        if all(term in text for term in terms):
            return text
    return ""


def inferred_criterion_lines(event: AlertEvent, raw_lines: List[str], trigger_summary: str) -> List[str]:
    details: List[str] = []
    rule = str(event.rule or "")
    previous = data_value(raw_lines, "이전")
    current = data_value(raw_lines, "현재")
    change = data_value(raw_lines, "변화")
    signal = data_value(raw_lines, "신호")
    status = data_value(raw_lines, "상태")
    profit = data_value(raw_lines, "손익") or data_value(raw_lines, "수익률")

    if rule in {"modelBuy", "watchlistBuyCandidate"}:
        score = data_value(raw_lines, "매수 판단") or data_value(raw_lines, "모델 매수 점수")
        if score:
            details.append("감지: " + score)
    elif rule == "modelSell":
        score = data_value(raw_lines, "매도 판단") or data_value(raw_lines, "모델 매도 점수")
        if score:
            details.append("감지: " + score)
    elif rule == "holdingTiming":
        detected = ", ".join(part for part in ["상태 " + status if status else "", "수익률 " + profit if profit else ""] if part)
        if detected:
            details.append("감지: " + detected)
    elif rule in {"monitorPnlChange", "monitorValueChange", "monitorCashChange"}:
        detected = ", ".join(part for part in ["변화 " + change if change else "", "이전 " + previous if previous else "", "현재 " + current if current else ""] if part)
        if detected:
            details.append("감지: " + detected)
    elif rule == "monitorTrendChange":
        if signal:
            details.append("감지: " + signal)
        trend = data_value(raw_lines, "추세")
        if trend:
            details.append("확인 데이터: " + trend)
    elif rule == "monitorDecisionChange":
        detected = ", ".join(part for part in ["이전 " + previous if previous else "", "현재 " + current if current else ""] if part)
        if detected:
            details.append("감지: " + detected)
    elif rule == "externalEquityMove":
        change_value = data_value(raw_lines, "미장 가격 변동")
        price = data_value(raw_lines, "현재가") or data_value(raw_lines, "가격")
        detected = ", ".join(part for part in ["가격 변동 " + change_value if change_value else "", "현재가 " + price if price else ""] if part)
        if detected:
            details.append("감지: " + detected)
    elif rule == "externalCryptoMove":
        crypto_line = first_line_containing(raw_lines, ["24h"])
        if crypto_line:
            details.append("감지: " + crypto_line)
    elif rule == "externalMacroShift":
        macro_lines = [line for line in raw_lines if "bp" in str(line)]
        if macro_lines:
            details.append("감지: " + ", ".join(macro_lines[:3]))
    elif rule == "externalDartDisclosure":
        report = raw_lines[1] if len(raw_lines) > 1 else ""
        receipt_date = data_value(raw_lines, "접수일")
        detected = ", ".join(part for part in [report, "접수일 " + receipt_date if receipt_date else ""] if part)
        if detected:
            details.append("감지: " + detected)
    elif rule == "externalDataConnection":
        if raw_lines:
            details.append("감지: " + ", ".join(raw_lines[:2]))
    elif rule == "monitorPositionChange":
        if previous or current:
            details.append("감지: " + ", ".join(part for part in ["이전 " + previous if previous else "", "현재 " + current if current else ""] if part))
        elif raw_lines:
            details.append("감지: " + raw_lines[0])
    elif rule == "watchlistQuote":
        quote_change = first_line_containing(raw_lines, ["변화"])
        if quote_change:
            details.append("감지: " + quote_change)
        else:
            current_price = data_value(raw_lines, "현재가") or data_value(raw_lines, "현재")
            if current_price:
                details.append("감지: 현재가 " + current_price)
    elif rule == "watchlistQuotePending":
        if len(raw_lines) > 1:
            details.append("감지: " + raw_lines[1])
    elif rule == "monitorConnection":
        detected = ", ".join(part for part in ["이전 " + previous if previous else "", "현재 " + current if current else ""] if part)
        details.append("감지: " + (detected or ", ".join(raw_lines[:2])))
    elif rule == "monitorHeartbeat":
        status_value = status or (raw_lines[0] if raw_lines else "")
        if status_value:
            details.append("감지: " + status_value)

    if not details and raw_lines:
        details.append("감지: " + raw_lines[0])
    if details:
        return ["설정: " + trigger_summary] + details
    return [trigger_summary] if trigger_summary else []


def event_criterion_lines(event: AlertEvent, raw_lines: List[str], trigger_summary: str) -> List[str]:
    explicit = [str(item or "").strip() for item in getattr(event, "criteria", []) if str(item or "").strip()]
    if explicit:
        return explicit
    return inferred_criterion_lines(event, raw_lines, trigger_summary)


@dataclass
class NotificationTemplate:
    message_type: str
    template: str
    description: str = ""
    enabled: bool = True
    updated_at: str = ""

    @classmethod
    def default(cls, message_type: str = "default") -> "NotificationTemplate":
        configured = DEFAULT_NOTIFICATION_TEMPLATES.get(message_type) or DEFAULT_NOTIFICATION_TEMPLATES["default"]
        return cls(message_type, configured["template"], configured.get("description", ""), True)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "messageType": payload["message_type"],
            "template": payload["template"],
            "description": payload["description"],
            "enabled": payload["enabled"],
            "updatedAt": payload["updated_at"],
        }


def symbol_display_name(symbol: object, title: object = "") -> str:
    raw_symbol = str(symbol or "").strip().upper()
    known = SYMBOL_DISPLAY_NAMES.get(raw_symbol, "")
    if known:
        return known
    title_text = str(title or "").strip()
    if title_text and title_text.upper() != raw_symbol:
        return title_text
    return raw_symbol


def symbol_with_code(display_symbol: object, raw_symbol: object) -> str:
    raw_text = str(raw_symbol or "").strip().upper()
    display_text = str(display_symbol or "").strip()
    if display_text and raw_text and display_text.upper() != raw_text:
        return display_text + " / " + raw_text
    return display_text or raw_text


def ontology_relation_context(context_or_metadata: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(context_or_metadata, dict):
        return {}
    context = context_or_metadata.get("ontologyRelationContext")
    if isinstance(context, dict) and context:
        return dict(context)
    metadata = context_or_metadata.get("metadata")
    if isinstance(metadata, dict):
        context = metadata.get("ontologyRelationContext")
        if isinstance(context, dict) and context:
            return dict(context)
    review = context_or_metadata.get("ontologyReviewContext")
    if isinstance(review, dict):
        nested = review.get("relationRuleContext")
        if isinstance(nested, dict) and nested:
            return dict(nested)
    return {}


def ontology_prompt_context(context_or_metadata: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(context_or_metadata, dict):
        return {}
    context = context_or_metadata.get("ontologyPromptContext")
    ontology_context = dict(context) if isinstance(context, dict) and context else {}
    context = context_or_metadata.get("notificationAiPromptContext")
    notification_context = dict(context) if isinstance(context, dict) and context else {}
    opinion = context_or_metadata.get("notificationAiOpinion")
    if isinstance(opinion, dict):
        context = opinion.get("promptContext")
        if isinstance(context, dict) and context and not notification_context:
            notification_context = dict(context)
    if notification_context and not ontology_context.get("promptTemplate"):
        return notification_context
    if ontology_context:
        return ontology_context
    if notification_context:
        return notification_context
    metadata = context_or_metadata.get("metadata")
    if isinstance(metadata, dict):
        context = metadata.get("ontologyPromptContext")
        ontology_context = dict(context) if isinstance(context, dict) and context else {}
        context = metadata.get("notificationAiPromptContext")
        notification_context = dict(context) if isinstance(context, dict) and context else {}
        opinion = metadata.get("notificationAiOpinion")
        if isinstance(opinion, dict):
            context = opinion.get("promptContext")
            if isinstance(context, dict) and context and not notification_context:
                notification_context = dict(context)
        if notification_context and not ontology_context.get("promptTemplate"):
            return notification_context
        if ontology_context:
            return ontology_context
        if notification_context:
            return notification_context
    relation_context = ontology_relation_context(context_or_metadata)
    nested = relation_context.get("promptContext") if isinstance(relation_context, dict) else {}
    return dict(nested or {}) if isinstance(nested, dict) else {}


def ontology_missing_data(context_or_metadata: Dict[str, object]) -> List[Dict[str, object]]:
    relation_context = ontology_relation_context(context_or_metadata)
    missing = relation_context.get("missingData") if isinstance(relation_context, dict) else []
    if not isinstance(missing, list):
        return []
    rows = []
    for item in missing:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("key") or "").strip()
            effect = str(item.get("effect") or "").strip()
            row = {"label": label, "effect": effect}
            status = str(item.get("status") or "").strip()
            source = str(item.get("source") or "").strip()
            if status:
                row["status"] = status
            if source:
                row["source"] = source
            rows.append(row)
        elif str(item or "").strip():
            rows.append({"label": str(item).strip(), "effect": ""})
    return [item for item in rows if item.get("label")]


def rule_value(item: Dict[str, object], *keys):
    for key in keys:
        if isinstance(item, dict) and item.get(key) not in (None, ""):
            return item.get(key)
    return ""


def ontology_rule_lines(context_or_metadata: Dict[str, object]) -> List[str]:
    relation_context = ontology_relation_context(context_or_metadata)
    if not relation_context:
        return []
    lines: List[str] = []
    strength = relation_context.get("signalStrength")
    label = str(relation_context.get("signalStrengthLabel") or "").strip()
    confidence = relation_context.get("confidence")
    if strength not in (None, ""):
        suffix = "신뢰도 " + format_score_value(confidence) if confidence not in (None, "") else ""
        lines.append("관계 신호: " + " ".join(part for part in [label + " (" + format_score_value(strength) + "점)", suffix] if part))
        lines.append("점수 해석: " + relation_score_meaning(float(strength)) + "입니다. 점수 상승은 대응 필요 강도 강화, 하락은 완화를 뜻하며 가격 방향 예측 점수가 아닙니다.")
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
    for item in rules:
        if not isinstance(item, dict):
            continue
        if item.get("referenceOnly") or item.get("reference_only"):
            continue
        rule_label = str(rule_value(item, "label", "rule_id", "ruleId")).strip()
        score = rule_value(item, "strengthScore", "strength_score")
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence_text = ", ".join(str(value) for value in evidence[:3] if str(value or "").strip())
        value = rule_label
        if score not in (None, ""):
            value += " (" + format_score_value(score) + "점)"
        if evidence_text:
            value += " - " + evidence_text
        if value.strip():
            lines.append("성립 규칙: " + value)
    prompt_context = ontology_prompt_context(context_or_metadata)
    prompt_id = str(prompt_context.get("promptId") or "").strip()
    if prompt_id:
        lines.append("AI 질문: " + prompt_id)
    return lines


def ai_prompt_lines(context_or_metadata: Dict[str, object]) -> List[str]:
    prompt_context = ontology_prompt_context(context_or_metadata)
    if not prompt_context:
        return []
    template = prompt_context.get("promptTemplate") if isinstance(prompt_context.get("promptTemplate"), dict) else {}
    label = str(template.get("label") or prompt_context.get("promptId") or "").strip()
    version = str(prompt_context.get("promptVersion") or template.get("version") or "").strip()
    lines = []
    if label:
        lines.append("프롬프트: " + label + ((" / " + version) if version else ""))
    guardrails = prompt_context.get("guardrails") if isinstance(prompt_context.get("guardrails"), list) else []
    if guardrails:
        lines.append("가드레일: " + " · ".join(str(item) for item in guardrails[:2] if str(item or "").strip()))
    return lines


def notification_ai_opinion_payload(context_or_metadata: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(context_or_metadata, dict):
        return {}
    opinion = context_or_metadata.get("notificationAiOpinion")
    if isinstance(opinion, dict) and opinion:
        return dict(opinion)
    metadata = context_or_metadata.get("metadata")
    if isinstance(metadata, dict):
        opinion = metadata.get("notificationAiOpinion")
        if isinstance(opinion, dict) and opinion:
            return dict(opinion)
    return {}


def notification_ai_opinion_lines(context_or_metadata: Dict[str, object]) -> List[str]:
    opinion = notification_ai_opinion_payload(context_or_metadata)
    lines = opinion.get("lines") if isinstance(opinion.get("lines"), list) else []
    return [str(line).strip() for line in lines if str(line or "").strip()]


def missing_data_lines(context_or_metadata: Dict[str, object]) -> List[str]:
    rows = ontology_missing_data(context_or_metadata)
    status_labels = {
        "missing": "수집 안 됨",
        "empty": "응답 비어 있음",
        "zero": "0값 수신",
        "proxy": "대체 근거 사용",
        "stale": "오래된 값",
    }
    lines = []
    for item in rows:
        text = str(item.get("label") or "").strip()
        status = str(item.get("status") or "").strip()
        status_label = status_labels.get(status, "")
        if status_label:
            text += " (" + status_label + ")"
        effect = str(item.get("effect") or "").strip()
        if effect:
            text += ": " + effect
        lines.append(text)
    return lines


def block_from_lines(title: str, lines: List[str]) -> str:
    if not lines:
        return ""
    return title + "\n" + "\n".join(plain_bullet(line) for line in lines)


def telegram_block_from_lines(title: str, lines: List[str]) -> str:
    if not lines:
        return ""
    return "<b>" + html.escape(title, quote=False) + "</b>\n" + "\n".join(html_bullet(line) for line in lines)


def target_display_value(title: object, raw_symbol: object, display_symbol: object) -> str:
    title_text = str(title or "").strip()
    raw_text = str(raw_symbol or "").strip().upper()
    display_text = str(display_symbol or "").strip()
    symbol_text = symbol_with_code(display_text, raw_text)
    if title_text and raw_text and title_text.upper() == raw_text and display_text:
        return symbol_text
    if title_text and symbol_text:
        if title_text == display_text:
            return symbol_text
        if raw_text and raw_text in title_text:
            return title_text
        if display_text and display_text in title_text:
            return title_text + (" / " + raw_text if raw_text else "")
        return title_text + " / " + symbol_text
    return title_text or symbol_text


def alert_context(event: AlertEvent) -> Dict[str, object]:
    raw_lines = raw_lines_with_reference_date(event, [str(line).strip() for line in event.lines if str(line).strip()])
    metadata = dict(event.metadata or {})
    lines = "\n".join(["- " + line for line in raw_lines])
    bullet_lines = "\n".join([plain_bullet(line) for line in raw_lines])
    message_type_label = MESSAGE_TYPE_LABELS.get(event.rule, event.rule)
    severity_label = SEVERITY_LABELS.get(str(event.severity or "").upper(), event.severity or "")
    trigger_summary = TRIGGER_SUMMARIES.get(event.rule, "설정한 조건이 실제 데이터에서 충족될 때 보냅니다.")
    raw_symbol = str(event.symbol or "").strip()
    display_symbol = symbol_display_name(raw_symbol, event.title) if raw_symbol else ""
    symbol_text = symbol_with_code(display_symbol, raw_symbol)
    symbol_line = ("종목: " + symbol_text) if symbol_text else ""
    severity_line = ("상태: " + severity_label) if severity_label else ""
    type_line = ("유형: " + message_type_label) if message_type_label else ""
    trigger_line = ("발생 조건: " + trigger_summary) if trigger_summary else ""
    data_lines = lines
    status_headline = ("[" + severity_label + "]") if severity_label else ""
    title_icon = notification_title_icon(event.rule, raw_lines, event)
    title_headline = notification_title_headline(event.rule, raw_lines, event, message_type_label or event.title)
    headline = " ".join(part for part in [status_headline, title_icon, title_headline] if part)
    target_value = target_display_value(event.title, raw_symbol, display_symbol)
    target_line = "대상: " + target_value if target_value else ""
    criteria = event_criterion_lines(event, raw_lines, trigger_summary)
    signals = notification_signal_labels(event.rule, raw_lines)
    trigger_block_rows = criterion_rows(criteria, False)
    trigger_block = ("발송 기준\n" + trigger_block_rows) if trigger_block_rows else ""
    display_raw_lines = notification_data_lines(raw_lines, metadata)
    data_rows = plain_data_rows(display_raw_lines)
    data_block = ("데이터\n" + data_rows) if data_rows else ""
    ontology_lines = ontology_rule_lines(metadata)
    ai_lines = ai_prompt_lines(metadata)
    ai_opinion_lines = notification_ai_opinion_lines(metadata)
    missing_lines = missing_data_lines(metadata)
    ontology_block = block_from_lines("관계 규칙", ontology_lines)
    ai_opinion_block = block_from_lines("AI 의견", ai_opinion_lines)
    ai_prompt_block = block_from_lines("AI 분석 기준", ai_lines)
    missing_data_block = block_from_lines("부족 데이터", missing_lines)
    readable_parts = [
        headline,
        target_value,
    ]
    if data_rows:
        readable_parts.extend(["", data_block])
    if ai_opinion_block:
        readable_parts.extend(["", ai_opinion_block])
    if ontology_block:
        readable_parts.extend(["", ontology_block])
    if ai_prompt_block:
        readable_parts.extend(["", ai_prompt_block])
    if missing_data_block:
        readable_parts.extend(["", missing_data_block])
    if trigger_block:
        readable_parts.extend(["", trigger_block])
    readable_message = "\n".join(part for part in readable_parts if str(part).strip() or part == "").strip()
    escaped_target = html.escape(target_value, quote=False)
    telegram_trigger_rows = criterion_rows(criteria, True)
    telegram_data_lines = telegram_data_rows(display_raw_lines)
    telegram_ontology_block = telegram_block_from_lines("관계 규칙", ontology_lines)
    telegram_ai_opinion_block = telegram_block_from_lines("AI 의견", ai_opinion_lines)
    telegram_ai_prompt_block = telegram_block_from_lines("AI 분석 기준", ai_lines)
    telegram_missing_data_block = telegram_block_from_lines("부족 데이터", missing_lines)
    telegram_parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + escaped_target + "</code>") if escaped_target else "",
    ]
    if telegram_data_lines:
        telegram_parts.extend(["", "<b>데이터</b>", telegram_data_lines])
    if telegram_ai_opinion_block:
        telegram_parts.extend(["", telegram_ai_opinion_block])
    if telegram_ontology_block:
        telegram_parts.extend(["", telegram_ontology_block])
    if telegram_ai_prompt_block:
        telegram_parts.extend(["", telegram_ai_prompt_block])
    if telegram_missing_data_block:
        telegram_parts.extend(["", telegram_missing_data_block])
    if telegram_trigger_rows:
        telegram_parts.extend(["", "<b>발송 기준</b>", telegram_trigger_rows])
    telegram_message = "\n".join(part for part in telegram_parts if str(part).strip() or part == "").strip()
    body = telegram_message or readable_message or "\n".join([event.title] + ([lines] if lines else []))
    generated_at = event_generated_at(event)
    reference_date = data_value(raw_lines, "기준일") or reference_date_text(generated_at)
    context = {
        "messageType": event.rule,
        "accountId": event.account_id,
        "accountLabel": event.account_label,
        "severity": event.severity,
        "severityLabel": severity_label,
        "rule": event.rule,
        "key": event.key,
        "title": event.title,
        "symbol": event.symbol,
        "rawSymbol": raw_symbol,
        "symbolDisplayName": display_symbol,
        "displaySymbolName": display_symbol,
        "symbolWithCode": symbol_text,
        "target": event.target(),
        "rawTarget": event.target(),
        "displayTarget": target_value,
        "messageTypeLabel": message_type_label,
        "triggerSummary": trigger_summary,
        "headline": headline,
        "statusHeadline": status_headline,
        "titleIcon": title_icon,
        "titleHeadline": title_headline,
        "targetLine": target_line,
        "triggerBlock": trigger_block,
        "criterionBlock": trigger_block,
        "criterionLines": "\n".join(criteria),
        "dataBlock": data_block,
        "aiOpinionBlock": ai_opinion_block,
        "ontologyRelationBlock": ontology_block,
        "aiPromptBlock": ai_prompt_block,
        "missingDataBlock": missing_data_block,
        "divider": "",
        "telegramMessage": telegram_message,
        "telegramDataLines": telegram_data_lines,
        "telegramAiOpinionBlock": telegram_ai_opinion_block,
        "telegramOntologyRelationBlock": telegram_ontology_block,
        "telegramAiPromptBlock": telegram_ai_prompt_block,
        "telegramMissingDataBlock": telegram_missing_data_block,
        "symbolLine": symbol_line,
        "severityLine": severity_line,
        "typeLine": type_line,
        "triggerLine": trigger_line,
        "dataLines": data_lines,
        "bulletLines": bullet_lines,
        "lines": lines,
        "rawLines": "\n".join(raw_lines),
        "notificationSignals": list(signals),
        "notificationSignalText": " ".join(signals),
        "referenceDate": reference_date,
        "eventGeneratedAt": generated_at,
        "readableMessage": readable_message,
        "body": body,
    }
    context["metadata"] = metadata
    for key, value in metadata.items():
        context.setdefault(str(key), value)
    return context


def text_context(
    text: str,
    message_type: str = "notification",
    account_id: str = "",
    account_label: str = "",
) -> Dict[str, object]:
    body = str(text or "").strip()
    title = body.splitlines()[0] if body else ""
    return {
        "messageType": str(message_type or "notification"),
        "accountId": str(account_id or ""),
        "accountLabel": str(account_label or ""),
        "title": title,
        "body": body,
        "lines": "\n".join(body.splitlines()[1:]),
    }


def context_value(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        return " ".join(str(value.get(key) or "") for key in sorted(value.keys()) if str(value.get(key) or "").strip())
    return str(value or "")


def render_template(template: str, context: Dict[str, object]) -> str:
    values = dict(context or {})

    def replace(match) -> str:
        return context_value(values.get(match.group(1), ""))

    rendered = PLACEHOLDER_PATTERN.sub(replace, str(template or BODY_TEMPLATE)).strip()
    compacted = []
    previous_blank = False
    for line in rendered.splitlines():
        cleaned = line.rstrip()
        if cleaned.strip():
            compacted.append(cleaned)
            previous_blank = False
        elif compacted and not previous_blank:
            compacted.append("")
            previous_blank = True
    while compacted and not compacted[-1].strip():
        compacted.pop()
    rendered = "\n".join(compacted)
    return beginner_friendly_text(rendered or context_value(values.get("body") or values.get("title") or ""))


def context_raw_lines(context: Dict[str, object]) -> List[str]:
    raw = context.get("rawLines") if isinstance(context, dict) else ""
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


def has_score_value(value: str) -> bool:
    return bool(SCORE_VALUE_PATTERN.search(str(value or "")))


def format_score_value(value: object) -> str:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return str(value or "").strip()
    if number.is_integer():
        return str(int(number))
    return ("%.1f" % number).rstrip("0").rstrip(".")


def context_message_type(context: Dict[str, object]) -> str:
    return str((context or {}).get("messageType") or (context or {}).get("rule") or "").strip()


def score_reason_items(value: object) -> List[str]:
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").splitlines()
    return [friendly_score_reason(item) for item in items if str(item or "").strip()]


def friendly_score_reason(value: object) -> str:
    text = str(value or "").strip()
    replacements = [
        ("기본 ", "기본 우선도 "),
        ("주의 등급", "주의 알림"),
        ("관찰 등급", "관찰 알림"),
        ("종목 지정", "종목 코드/이름 포함"),
        ("핵심 투자 단어", "중요 투자 표현 포함"),
        ("확인 데이터 포함", "수급·추세 같은 확인 데이터 포함"),
        ("행동 필요 표현", "확인·점검 표현 포함"),
        ("본문 있음", "알림 내용 있음"),
        ("상태성 노이즈", "단순 상태 알림"),
        ("유사 메시지", "비슷한 알림 반복"),
        ("상태 정책", "반복 알림 조절"),
        ("장 시간", "장 운영 시간"),
        ("신규 임계값 상태", "새 기준값 상태"),
        ("같은 임계값 상태 지속", "같은 기준값 상태 지속"),
        ("임계값", "기준값"),
        ("리스크", "위험"),
        ("모델 매수", "내 매수 기준"),
        ("모델 매도", "내 매도 기준"),
        ("danger", "위험"),
        ("caution", "주의"),
        ("ALERT", "주의"),
        ("WATCH", "관찰"),
    ]
    for before, after in replacements:
        text = text.replace(before, after)
    text = re.sub(r"([+-]\d+(?:\.\d+)?)$", r"\1점", text)
    return text


def formula_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if number.is_integer():
        return str(int(number))
    return ("%.4f" % number).rstrip("0").rstrip(".")


def formula_audit_items(context: Dict[str, object], domain: str = "all") -> List[Dict[str, object]]:
    audits: List[Dict[str, object]] = []
    if domain in {"all", "model"}:
        raw_audits = (context or {}).get("formulaAudits")
    else:
        raw_audits = []
    if isinstance(raw_audits, list):
        audits.extend([item for item in raw_audits if isinstance(item, dict)])
    if domain in {"all", "delivery"}:
        notification_audit = (context or {}).get("notificationFormulaAudit")
        if isinstance(notification_audit, dict) and notification_audit:
            audits.append(notification_audit)
        elif (context or {}).get("honeyScore") not in (None, ""):
            expression = str((context or {}).get("notificationScoreFormula") or "rawScore").strip() or "rawScore"
            audits.append({
                "key": "notificationScoreFormula",
                "label": "알림 발송 공식",
                "expression": expression,
                "result": context.get("honeyScore"),
                "variables": {"rawScore": context.get("honeyScore")},
                "missing": [],
                "note": "상세 발송 변수표가 없는 이전 알림이어서 최종 발송 우선도를 rawScore로 표시합니다.",
            })
    seen = set()
    unique: List[Dict[str, object]] = []
    for audit in audits:
        key = str(audit.get("key") or audit.get("label") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(audit)
    return unique


def formula_variables_text(audit: Dict[str, object]) -> str:
    variables = audit.get("variables") if isinstance(audit, dict) else {}
    if not isinstance(variables, dict) or not variables:
        return "없음"
    return ", ".join(
        str(key) + "=" + formula_number(variables.get(key))
        for key in sorted(variables.keys())
    )


def formula_missing_text(audit: Dict[str, object]) -> str:
    missing = audit.get("missing") if isinstance(audit, dict) else []
    if isinstance(missing, str):
        missing = [missing]
    if not isinstance(missing, list) or not missing:
        return "없음"
    return ", ".join(str(item) for item in missing if str(item or "").strip()) or "없음"


def formula_detail_text(label: str, detail: str) -> str:
    return "없음" if detail == "없음" else label + " " + detail


def formula_audit_lines(context: Dict[str, object], domain: str = "model") -> List[str]:
    lines: List[str] = []
    is_delivery = domain == "delivery"
    for audit in formula_audit_items(context, domain):
        key = str(audit.get("key") or "").strip()
        label = str(audit.get("label") or key or "공식").strip()
        expression = str(audit.get("expression") or "").strip()
        result = formula_number(audit.get("result"))
        if is_delivery:
            result_text = "우선도 " + result
            formula_label = "발송 공식"
            variable_label = "발송 대입값"
            missing_label = "발송 부족 데이터"
            note_label = "발송 참고"
        else:
            result_text = ("선택됨, " if audit.get("selected") else "") + result + "점"
            formula_label = "모델 공식"
            variable_label = "모델 대입값"
            missing_label = "모델 부족 데이터"
            note_label = "모델 참고"
        if expression:
            lines.append(formula_label + ": " + label + "(" + key + ") = " + expression + " -> " + result_text)
        else:
            lines.append(formula_label + ": " + label + "(" + key + ") -> " + result_text)
        lines.append(variable_label + ": " + formula_detail_text(label, formula_variables_text(audit)))
        lines.append(missing_label + ": " + formula_detail_text(label, formula_missing_text(audit)))
        if is_delivery and key == "notificationScoreFormula" and (context or {}).get("honeyScore") not in (None, ""):
            final_score = formula_number((context or {}).get("honeyScore"))
            if result and final_score and result != final_score:
                lines.append("최종 발송 우선도: 공식 결과 " + result + "에서 반복·장 시간 정책 반영 후 " + final_score)
        note = str(audit.get("note") or "").strip()
        if note:
            lines.append(note_label + ": " + note)
    return lines


MODELING_LABELS = {
    "investmentInsight": "온톨로지 투자 인사이트 모델",
    "modelBuy": "매수 판단 모델",
    "modelSell": "매도 판단 모델",
    "watchlistBuyCandidate": "관심종목 매수 후보 모델",
    "watchlistQuote": "관심종목 시세 변화 감지 모델",
    "watchlistQuotePending": "관심종목 시세 수집 대기 모델",
    "watchlistOntologySignal": "관심종목 온톨로지 관계 신호 모델",
    "holdingTiming": "보유 타이밍 모델",
    "monitorHeartbeat": "실시간 모니터링 상태 모델",
    "monitorConnection": "토스 연결 상태 모델",
    "monitorPositionChange": "보유 수량 변화 감지 모델",
    "monitorPnlChange": "손익률 변화 감지 모델",
    "monitorValueChange": "평가액 변화 감지 모델",
    "monitorTrendChange": "이동평균/추세 변화 감지 모델",
    "monitorCashChange": "현금 비중 변화 감지 모델",
    "monitorDecisionChange": "보유 판단 변화 감지 모델",
    "externalEquityMove": "미장 가격/거래량 변동 모델",
    "externalCryptoMove": "크립토 변동 모델",
    "externalMacroShift": "거시 지표 변화 모델",
    "externalDartDisclosure": "공시 변화 감지 모델",
    "externalDataConnection": "외부 API 연결 상태 모델",
    "modelReview": "비동기 모델 리뷰 모델",
    "notification": "일반 알림 발송 모델",
    "default": "기본 알림 발송 모델",
}

MODEL_DATA_HINTS = {
    "investmentInsight": "보유·관심종목·외부 데이터·관계 규칙·기존 모델 신호를 연결한 온톨로지 인사이트",
    "modelBuy": "토스 시세·수급·추세·가치평가 데이터",
    "modelSell": "토스 시세·수급·추세·손절/가치평가 데이터",
    "watchlistBuyCandidate": "관심종목 시세·수급·추세·가치평가 데이터",
    "watchlistOntologySignal": "관심종목 ABox 관측값, 관계 규칙, 추세 동역학, 데이터 품질",
    "holdingTiming": "보유 스냅샷, 손익률, 수급, 추세, 매도 가능 수량",
    "monitorDecisionChange": "이전/현재 보유 모델 점수와 보유 스냅샷",
    "monitorTrendChange": "현재가와 20일/60일 이동평균 비교",
    "monitorPnlChange": "이전/현재 손익률",
    "monitorValueChange": "이전/현재 평가액",
    "monitorCashChange": "이전/현재 현금 비중",
    "externalEquityMove": "Alpha Vantage 가격·거래량",
    "externalCryptoMove": "CoinGecko 24시간/7일 변동률·가격·거래액",
    "externalMacroShift": "FRED 금리·거시 시계열",
    "externalDartDisclosure": "OpenDART 공시",
    "externalDataConnection": "외부 API 호출 결과",
}


def context_number(context: Dict[str, object], key: str):
    value = (context or {}).get(key)
    if value in (None, ""):
        raw_value = data_value(context_raw_lines(context), key)
        value = raw_value
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def selected_holding_formula_key(context: Dict[str, object]) -> str:
    basis = str((context or {}).get("holdingDecisionBasis") or "").strip()
    if basis == "profitTake":
        return "profitTakeScoreFormula"
    if basis == "lossCut":
        return "lossCutScoreFormula"
    pnl = context_number(context, "profitLossRate")
    if pnl is None:
        pnl = context_number(context, "손익")
    if pnl is None:
        pnl = context_number(context, "수익률")
    if pnl is not None:
        return "lossCutScoreFormula" if pnl < 0 else "profitTakeScoreFormula"
    return ""


def strategy_formula_line(context: Dict[str, object]) -> str:
    message_type = context_message_type(context)
    if message_type == "investmentInsight":
        return "판단 공식: 온톨로지 관계 인사이트(sourceSignalTypes + ontologyInsight)"
    if message_type in {"modelBuy", "watchlistBuyCandidate"}:
        return "판단 공식: 매수 공식(buyScoreFormula)"
    if message_type == "modelSell":
        return "판단 공식: 매도 공식(sellScoreFormula)"
    if message_type in {"holdingTiming", "monitorDecisionChange"}:
        formula_key = selected_holding_formula_key(context)
        if formula_key:
            label = "익절 점검 공식" if formula_key == "profitTakeScoreFormula" else "손실 관리 공식"
            return "판단 공식: " + label + "(" + formula_key + ")"
        return "판단 공식: 익절 점검 공식(profitTakeScoreFormula) / 손실 관리 공식(lossCutScoreFormula)"
    if message_type == "externalCryptoMove":
        return "판단 공식: 크립토 변동 공식(cryptoMoveScoreFormula)"
    return ""


def crypto_model_summary_lines(context: Dict[str, object]) -> List[str]:
    if context_message_type(context) != "externalCryptoMove":
        return []
    model = context.get("cryptoMoveModel") if isinstance(context, dict) else {}
    if not isinstance(model, dict):
        model = {}
    title = str(model.get("titleLabel") or context.get("cryptoMoveTitle") or "").strip()
    score = model.get("score", context.get("cryptoMoveScore"))
    period = str(model.get("dominantPeriodLabel") or context.get("cryptoMoveDominantPeriod") or "").strip()
    change = model.get("dominantChange", context.get("cryptoMoveDominantChange"))
    reason = str(model.get("reason") or context.get("cryptoMoveReason") or "").strip()
    lines: List[str] = []
    if title or score not in (None, ""):
        result = title or "크립토 가격 변동"
        if score not in (None, ""):
            result += " (모델 점수 " + format_score_value(score) + "점)"
        lines.append("판단 결과: " + result)
    if period or change not in (None, ""):
        change_text = signed_pct(change) if isinstance(change, (int, float)) else str(change or "").strip()
        lines.append("대표 변화: " + " ".join(part for part in [period, change_text] if part))
    if reason:
        lines.append("핵심 이유: " + reason)
    return lines


def modeling_lines(context: Dict[str, object]) -> List[str]:
    message_type = context_message_type(context)
    if message_type in SCORE_EXPLANATION_SKIP_TYPES:
        return []
    label = MODELING_LABELS.get(message_type, "알림 발송 모델")
    lines = ["모델: " + label]
    formula_line = strategy_formula_line(context)
    if formula_line:
        lines.append(formula_line)
    data_hint = MODEL_DATA_HINTS.get(message_type)
    if data_hint:
        lines.append("사용 데이터: " + data_hint)
    lines.extend(crypto_model_summary_lines(context))
    return lines


def delivery_score_lines(context: Dict[str, object]) -> List[str]:
    if not isinstance(context, dict) or context.get("honeyScore") in (None, ""):
        return []
    score = format_score_value(context.get("honeyScore"))
    threshold = format_score_value(context.get("honeyThreshold"))
    lines = [
        "발송 우선도: " + score + "/" + threshold,
        "발송 우선도는 투자 모델 점수가 아니라 이 알림을 실제로 보낼지 판단하는 별도 값입니다. 발송 공식으로 알림 중요도를 계산한 뒤 반복 여부와 장 시간 정책을 반영합니다.",
    ]
    formula = str(context.get("notificationScoreFormula") or "").strip()
    if formula and formula != "rawScore":
        lines.append("발송 공식: " + formula)
    reasons = score_reason_items(context.get("honeyReasons"))
    if reasons:
        lines.append("발송 판정 내역: " + ", ".join(reasons))
    return lines


def investment_score_lines(context: Dict[str, object]) -> List[str]:
    message_type = context_message_type(context)
    raw_lines = context_raw_lines(context)
    lines: List[str] = []
    buy_value = data_value(raw_lines, "매수 판단") or data_value(raw_lines, "모델 매수 점수")
    sell_value = data_value(raw_lines, "매도 판단") or data_value(raw_lines, "모델 매도 점수")
    status_value = data_value(raw_lines, "상태")
    previous_value = data_value(raw_lines, "이전")
    current_value = data_value(raw_lines, "현재")

    if message_type == "investmentInsight":
        insight = context.get("ontologyInsight") if isinstance(context, dict) else {}
        score = insight.get("score") if isinstance(insight, dict) else context.get("score")
        confidence = insight.get("confidence") if isinstance(insight, dict) else context.get("confidence")
        source_types = insight.get("sourceSignalTypes") if isinstance(insight, dict) else context.get("sourceSignalTypes")
        if isinstance(source_types, list):
            source_text = ", ".join(str(item) for item in source_types[:5])
        else:
            source_text = str(source_types or "").strip()
        parts = []
        if score not in (None, ""):
            parts.append("관계 강도 " + formula_number(score) + "점")
        if confidence not in (None, ""):
            parts.append("신뢰도 " + formula_number(confidence) + "%")
        if source_text:
            parts.append("근거 신호 " + source_text)
        lines.append(
            "온톨로지 인사이트 점수: 개별 알림 타입을 직접 발송하지 않고, 보유·관심종목·외부 신호가 만든 관계 조합을 하나의 투자 인사이트로 합성합니다."
            + (" " + ", ".join(parts) + "." if parts else "")
        )
    if message_type in {"modelBuy", "watchlistBuyCandidate"} and has_score_value(buy_value):
        lines.append(
            "매수 모델 점수: 체결 흐름, 방향 있는 거래량, 매수 비중, 호가 균형, 가격 움직임, 이동평균 흐름, 투자자 수급, 적정가 대비를 합쳐 0~100점으로 계산합니다. 점수가 높을수록 매수 후보에 가깝습니다."
        )
    if message_type == "modelSell" and has_score_value(sell_value):
        lines.append(
            "매도 모델 점수: 매도 압력, 거래량 변화, 가격 움직임, 이동평균 흐름, 투자자 수급, 적정가 대비, 손절 기준을 합쳐 0~100점으로 계산합니다. 점수가 높을수록 분할매도나 손절 기준을 다시 봐야 합니다."
        )
    if message_type in {"holdingTiming", "monitorDecisionChange"} and any(has_score_value(item) for item in [status_value, previous_value, current_value]):
        lines.append(
            "보유 모델 점수: 사용자가 설정한 익절 공식과 손절/손실 관리 공식을 따로 계산한 뒤, 수익 중이면 익절 점수, 손실 중이면 손실 관리 점수를 선택합니다. 공식에는 기본 점수, 손익률 구간, 한 업종에 몰린 정도, 팔 수 있는 수량, 수급·추세 흐름, 손실 기준 근처의 확인 신호와 약한 근거 감점이 들어갑니다."
        )
    if message_type == "externalCryptoMove" and (context or {}).get("cryptoMoveScore") not in (None, ""):
        lines.append(
            "크립토 변동 모델 점수: 24시간 변화율과 7일 변화율이 각각의 기준값을 얼마나 넘었는지 비교해 0~100점으로 계산합니다. 모델은 더 강하게 기준을 넘은 기간을 대표 변화로 고르고, 그 방향으로 제목과 관찰/주의 등급을 정합니다."
        )
    return lines


def ontology_modeling_lines(context: Dict[str, object]) -> List[str]:
    relation_context = ontology_relation_context(context)
    if not relation_context:
        return []
    subject = relation_context.get("subject") if isinstance(relation_context.get("subject"), dict) else {}
    subject_text = " / ".join(
        str(value)
        for value in [subject.get("name"), subject.get("symbol"), subject.get("market")]
        if str(value or "").strip()
    )
    lines = [
        "모델: 관계 규칙 모델",
        "엔진: " + str(relation_context.get("engineVersion") or "-"),
    ]
    if subject_text:
        lines.append("대상: " + subject_text)
    strength = relation_context.get("signalStrength")
    if strength not in (None, ""):
        lines.append(
            "관계 신호: "
            + str(relation_context.get("signalStrengthLabel") or "")
            + " ("
            + format_score_value(strength)
            + "점)"
        )
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    if decision:
        selected = str(decision.get("selectedRuleId") or "").strip()
        lines.append("판단: " + str(decision.get("label") or "-") + ((" / 선택 규칙 " + selected) if selected else ""))
    if context_message_type(context) == "externalCryptoMove":
        model = context.get("cryptoMoveModel") if isinstance(context.get("cryptoMoveModel"), dict) else {}
        period = str(model.get("dominantPeriodLabel") or context.get("cryptoMoveDominantPeriod") or "").strip()
        change = model.get("dominantChange", context.get("cryptoMoveDominantChange"))
        if period or change not in (None, ""):
            change_text = signed_pct(change) if isinstance(change, (int, float)) else str(change or "").strip()
            lines.append("대표 변화: " + " ".join(part for part in [period, change_text] if part))
    for line in ontology_rule_lines(context)[:4]:
        lines.append(line)
    return lines


def ontology_missing_lines(context: Dict[str, object]) -> List[str]:
    lines = missing_data_lines(context)
    return ["부족 데이터 없음"] if ontology_relation_context(context) and not lines else lines


def ontology_prompt_section_lines(context: Dict[str, object]) -> List[str]:
    lines = ai_prompt_lines(context)
    prompt_context = ontology_prompt_context(context)
    policy = str(prompt_context.get("promptPolicy") or "").strip()
    if policy:
        compact_policy = ", ".join(line.strip() for line in policy.splitlines() if line.strip())
        if compact_policy:
            lines.append("정책: " + compact_policy)
    return lines


def score_explanation_sections(context: Dict[str, object]) -> List[tuple]:
    if context_message_type(context) in SCORE_EXPLANATION_SKIP_TYPES:
        return []
    message_type = context_message_type(context)
    formula_first_types = {"modelBuy", "modelSell", "watchlistBuyCandidate"}
    has_relation_context = bool(ontology_relation_context(context))
    if has_relation_context and message_type not in formula_first_types:
        model_lines = ontology_modeling_lines(context)
        missing_lines = ontology_missing_lines(context)
        prompt_lines = ontology_prompt_section_lines(context)
    else:
        model_lines = modeling_lines(context)
        model_lines.extend(formula_audit_lines(context, "model"))
        model_lines.extend(investment_score_lines(context))
        if has_relation_context:
            model_lines.extend(ontology_rule_lines(context)[:3])
        missing_lines = []
        prompt_lines = ontology_prompt_section_lines(context)
    delivery_lines = delivery_score_lines(context)
    delivery_lines.extend(formula_audit_lines(context, "delivery"))
    sections = []
    if model_lines:
        sections.append(("관계 판단" if has_relation_context and message_type not in formula_first_types else "모델 판단", model_lines))
    if missing_lines:
        sections.append(("부족 데이터", missing_lines))
    if prompt_lines:
        sections.append(("AI 프롬프트", prompt_lines))
    if delivery_lines:
        sections.append(("알림 발송", delivery_lines))
    return sections


def score_explanation_lines(context: Dict[str, object]) -> List[str]:
    lines: List[str] = []
    for _title, section_lines in score_explanation_sections(context):
        lines.extend(section_lines)
    return lines


def score_explanation_block(context: Dict[str, object], rich: bool = False) -> str:
    sections = score_explanation_sections(context)
    if not sections:
        return ""
    blocks = []
    for title, lines in sections:
        if not lines:
            continue
        if rich:
            rows = []
            for line in lines:
                label, value = split_label_value(line)
                if label and value:
                    rows.append("• <b>" + html.escape(label, quote=False) + "</b>: " + html.escape(value, quote=False))
                else:
                    rows.append(html_bullet(line))
            blocks.append("<b>" + html.escape(title, quote=False) + "</b>\n" + "\n".join(rows))
        else:
            blocks.append(title + "\n" + "\n".join(plain_bullet(line) for line in lines))
    return "\n\n".join(blocks)


def ai_opinion_block(context: Dict[str, object], rich: bool = False) -> str:
    lines = notification_ai_opinion_lines(context)
    if not lines:
        return ""
    if rich:
        rows = []
        for line in lines:
            label, value = split_label_value(line)
            if label and value:
                rows.append("• <b>" + html.escape(label, quote=False) + "</b>: " + html.escape(value, quote=False))
            else:
                rows.append(html_bullet(line))
        return "<b>AI 의견</b>\n" + "\n".join(rows)
    return "AI 의견\n" + "\n".join(plain_bullet(line) for line in lines)


def context_with_score_explanation(context: Dict[str, object]) -> Dict[str, object]:
    values = enrich_notification_ai_context(dict(context or {}))
    raw_symbol = str(values.get("rawSymbol") or values.get("symbol") or "").strip().upper()
    display_symbol = str(
        values.get("symbolDisplayName")
        or values.get("displaySymbolName")
        or SYMBOL_DISPLAY_NAMES.get(raw_symbol, "")
        or ""
    ).strip()
    symbol_text = str(values.get("symbolWithCode") or "").strip() or symbol_with_code(display_symbol, raw_symbol)
    if symbol_text:
        values["symbol"] = symbol_text
    display_target = str(values.get("displayTarget") or "").strip()
    if not display_target and symbol_text and str(values.get("target") or "").strip().upper() == raw_symbol:
        display_target = symbol_text
    if display_target:
        values["target"] = display_target
    values["aiOpinionBlock"] = ai_opinion_block(values, False)
    values["telegramAiOpinionBlock"] = ai_opinion_block(values, True)
    values["scoreExplanation"] = score_explanation_block(values, False)
    values["telegramScoreExplanation"] = score_explanation_block(values, True)
    return values


def template_prefers_rich_score(template: str, rendered: str) -> bool:
    text = str(template or "")
    return "{telegramMessage}" in text or "<b>" in rendered or "<code>" in rendered


def append_score_explanation(rendered: str, context: Dict[str, object], rich: bool = False) -> str:
    rendered_text = str(rendered or "")
    if (
        context_message_type(context) in {"holdingTiming", "monitorDecisionChange", "investmentInsight"}
        and ontology_relation_context(context)
        and any(marker in rendered_text for marker in ["관계 규칙", "AI 분석 기준", "부족 데이터", "발송 기준"])
    ):
        return rendered
    if not rendered_text.strip() or "점수 계산" in rendered_text or "모델 판단" in rendered_text or "관계 판단" in rendered_text or "온톨로지 판단" in rendered_text or "알림 발송" in rendered_text:
        return rendered
    block = score_explanation_block(context, rich)
    if not block:
        return rendered
    return str(rendered).rstrip() + "\n\n" + block


def append_ai_opinion(rendered: str, context: Dict[str, object], rich: bool = False) -> str:
    rendered_text = str(rendered or "")
    if isinstance(context, dict) and context.get("notificationAiValidatedResponse"):
        return rendered
    if not rendered_text.strip() or "AI 의견" in rendered_text:
        return rendered
    block = ai_opinion_block(context, rich)
    if not block:
        return rendered
    insertion_markers = [
        "\n\n<b>관계 규칙</b>",
        "\n\n관계 규칙",
        "\n\n<b>발송 기준</b>",
        "\n\n발송 기준",
        "\n\n<b>모델 판단</b>",
        "\n\n모델 판단",
        "\n\n<b>관계 판단</b>",
        "\n\n관계 판단",
        "\n\n<b>온톨로지 판단</b>",
        "\n\n온톨로지 판단",
    ]
    for marker in insertion_markers:
        if marker in rendered_text:
            return rendered_text.replace(marker, "\n\n" + block + marker, 1)
    return rendered_text.rstrip() + "\n\n" + block


def render_notification(template: NotificationTemplate, context: Dict[str, object]) -> str:
    values = context_with_score_explanation(context)
    if template and template.enabled:
        rendered = render_template(template.template, values)
        rich = template_prefers_rich_score(template.template, rendered)
        rendered = append_ai_opinion(rendered, values, rich)
        return beginner_friendly_text(append_score_explanation(rendered, values, rich))
    rendered = render_template(BODY_TEMPLATE, values)
    rich = template_prefers_rich_score(BODY_TEMPLATE, rendered)
    rendered = append_ai_opinion(rendered, values, rich)
    return beginner_friendly_text(append_score_explanation(rendered, values, rich))



def template_variables() -> List[str]:
    return [
        "messageType",
        "accountId",
        "accountLabel",
        "severity",
        "severityLabel",
        "rule",
        "key",
        "title",
        "symbol",
        "rawSymbol",
        "symbolDisplayName",
        "displaySymbolName",
        "target",
        "rawTarget",
        "displayTarget",
        "messageTypeLabel",
        "triggerSummary",
        "headline",
        "statusHeadline",
        "titleIcon",
        "titleHeadline",
        "targetLine",
        "triggerBlock",
        "criterionBlock",
        "criterionLines",
        "dataBlock",
        "aiOpinionBlock",
        "ontologyRelationBlock",
        "aiPromptBlock",
        "missingDataBlock",
        "divider",
        "telegramMessage",
        "telegramDataLines",
        "telegramAiOpinionBlock",
        "telegramOntologyRelationBlock",
        "telegramAiPromptBlock",
        "telegramMissingDataBlock",
        "symbolLine",
        "severityLine",
        "typeLine",
        "triggerLine",
        "dataLines",
        "bulletLines",
        "lines",
        "rawLines",
        "notificationSignals",
        "notificationSignalText",
        "referenceDate",
        "eventGeneratedAt",
        "sentAt",
        "sentTime",
        "sentLine",
        "readableMessage",
        "body",
        "disclosureAnalysis",
        "telegramDisclosureAnalysis",
        "disclosureAnalysisSource",
        "disclosureAnalysisVersion",
        "scoreExplanation",
        "telegramScoreExplanation",
        "honeyScore",
        "honeyThreshold",
        "honeyScoreText",
        "honeyDecision",
        "honeyReasons",
        "metadata",
        "formulaAudits",
        "legacyFormulaAudits",
        "notificationAiOpinion",
        "notificationAiPromptContext",
        "ontologyRelationContext",
        "ontologyPromptContext",
        "notificationFormulaAudit",
        "buyScoreFormula",
        "sellScoreFormula",
        "profitTakeScoreFormula",
        "lossCutScoreFormula",
        "notificationScoreFormula",
        "holdingDecision",
        "holdingDecisionBasis",
        "holdingDecisionScore",
        "profitLossRate",
        "market",
        "changePercent",
        "change24h",
        "change7d",
        "price",
        "volume",
        "volume24h",
        "provider",
        "cryptoMoveModel",
        "cryptoMoveScore",
        "cryptoMoveDirection",
        "cryptoMoveDominantPeriod",
        "cryptoMoveDominantChange",
        "cryptoMoveTitle",
        "cryptoMoveReason",
    ]
