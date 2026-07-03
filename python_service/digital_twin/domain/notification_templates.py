import html
import re
from dataclasses import asdict, dataclass
from typing import Dict, List

from .message_types import MESSAGE_TYPE_LABELS, TRIGGER_SUMMARIES
from .portfolio import AlertEvent


LEGACY_DEFAULT_TEMPLATE = "{title}\n{lines}"
PREVIOUS_DEFAULT_TEMPLATE = "{readableMessage}"
DEFAULT_TEMPLATE = "{telegramMessage}"
BODY_TEMPLATE = "{body}"
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
    "기준일",
    "투자자",
    "기울기",
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
    "손익": 20,
    "매수 판단": 25,
    "매도 판단": 26,
    "수급": 30,
    "추세": 40,
    "기울기": 45,
    "투자자": 50,
    "신호": 60,
    "비트코인 변동": 70,
    "크립토 변동": 71,
    "크립토 가격": 72,
    "크립토 거래액": 73,
}

SEPARATE_DATA_LABELS = {
    "상태",
    "손익",
    "매수 판단",
    "매도 판단",
    "수급",
    "추세",
    "기울기",
    "투자자",
    "신호",
    "비트코인 변동",
    "크립토 변동",
    "크립토 가격",
    "크립토 거래액",
    "평가",
    "보유",
}

SEVERITY_LABELS = {
    "INFO": "정보",
    "WATCH": "관찰",
    "ALERT": "주의",
}

DEFAULT_NOTIFICATION_TEMPLATES = {
    "default": {
        "template": DEFAULT_TEMPLATE,
        "description": "기본 알림 템플릿. title, lines, body 변수를 사용할 수 있습니다.",
    },
    "modelBuy": {
        "template": DEFAULT_TEMPLATE,
        "description": "모델 매수 조건 알림",
    },
    "modelSell": {
        "template": DEFAULT_TEMPLATE,
        "description": "모델 매도 조건 알림",
    },
    "watchlistQuote": {
        "template": DEFAULT_TEMPLATE,
        "description": "관심종목 시세 수집 알림",
    },
    "watchlistQuotePending": {
        "template": DEFAULT_TEMPLATE,
        "description": "관심종목 시세 대기 알림",
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


def plain_bullet(text: str) -> str:
    cleaned = str(text or "").strip()
    return "• " + cleaned if cleaned else ""


def html_bullet(text: str) -> str:
    cleaned = str(text or "").strip()
    return "• " + html.escape(cleaned, quote=False) if cleaned else ""


def split_label_value(text: str):
    cleaned = str(text or "").strip()
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
    text = str(line or "").strip()
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


def data_pair_text(label: str, value: str, rich: bool = False) -> str:
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
    profit = data_value(raw_lines, "손익")

    if rule == "modelBuy":
        score = data_value(raw_lines, "매수 판단") or data_value(raw_lines, "모델 매수 점수")
        if score:
            details.append("감지: " + score)
    elif rule == "modelSell":
        score = data_value(raw_lines, "매도 판단") or data_value(raw_lines, "모델 매도 점수")
        if score:
            details.append("감지: " + score)
    elif rule == "holdingTiming":
        detected = ", ".join(part for part in ["상태 " + status if status else "", "손익 " + profit if profit else ""] if part)
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
        price = data_value(raw_lines, "가격")
        detected = ", ".join(part for part in ["가격 변동 " + change_value if change_value else "", "가격 " + price if price else ""] if part)
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
            current_price = data_value(raw_lines, "현재")
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


def alert_context(event: AlertEvent) -> Dict[str, object]:
    raw_lines = [str(line).strip() for line in event.lines if str(line).strip()]
    lines = "\n".join(["- " + line for line in raw_lines])
    bullet_lines = "\n".join([plain_bullet(line) for line in raw_lines])
    message_type_label = MESSAGE_TYPE_LABELS.get(event.rule, event.rule)
    severity_label = SEVERITY_LABELS.get(str(event.severity or "").upper(), event.severity or "")
    trigger_summary = TRIGGER_SUMMARIES.get(event.rule, "설정한 조건이 실제 데이터에서 충족될 때 보냅니다.")
    symbol_line = ("종목: " + event.symbol) if event.symbol else ""
    severity_line = ("상태: " + severity_label) if severity_label else ""
    type_line = ("유형: " + message_type_label) if message_type_label else ""
    trigger_line = ("발생 조건: " + trigger_summary) if trigger_summary else ""
    data_lines = lines
    status_headline = ("[" + severity_label + "]") if severity_label else ""
    title_headline = message_type_label or event.title
    headline = " ".join(part for part in [status_headline, title_headline] if part)
    target_parts = [event.title]
    if event.symbol and event.symbol != event.title:
        target_parts.append(event.symbol)
    target_value = " / ".join(part for part in target_parts if part)
    target_line = "대상: " + target_value if target_value else ""
    criteria = event_criterion_lines(event, raw_lines, trigger_summary)
    trigger_block_rows = criterion_rows(criteria, False)
    trigger_block = ("발송 기준\n" + trigger_block_rows) if trigger_block_rows else ""
    data_rows = plain_data_rows(raw_lines)
    data_block = ("데이터\n" + data_rows) if data_rows else ""
    readable_parts = [
        headline,
        target_value,
    ]
    if data_rows:
        readable_parts.extend(["", data_block])
    if trigger_block:
        readable_parts.extend(["", trigger_block])
    readable_message = "\n".join(part for part in readable_parts if str(part).strip() or part == "").strip()
    escaped_target = html.escape(target_value, quote=False)
    telegram_trigger_rows = criterion_rows(criteria, True)
    telegram_data_lines = telegram_data_rows(raw_lines)
    telegram_parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + escaped_target + "</code>") if escaped_target else "",
    ]
    if telegram_data_lines:
        telegram_parts.extend(["", "<b>데이터</b>", telegram_data_lines])
    if telegram_trigger_rows:
        telegram_parts.extend(["", "<b>발송 기준</b>", telegram_trigger_rows])
    telegram_message = "\n".join(part for part in telegram_parts if str(part).strip() or part == "").strip()
    body = telegram_message or readable_message or "\n".join([event.title] + ([lines] if lines else []))
    return {
        "messageType": event.rule,
        "accountId": event.account_id,
        "accountLabel": event.account_label,
        "severity": event.severity,
        "severityLabel": severity_label,
        "rule": event.rule,
        "key": event.key,
        "title": event.title,
        "symbol": event.symbol,
        "target": event.target(),
        "messageTypeLabel": message_type_label,
        "triggerSummary": trigger_summary,
        "headline": headline,
        "statusHeadline": status_headline,
        "titleHeadline": title_headline,
        "targetLine": target_line,
        "triggerBlock": trigger_block,
        "criterionBlock": trigger_block,
        "criterionLines": "\n".join(criteria),
        "dataBlock": data_block,
        "divider": "",
        "telegramMessage": telegram_message,
        "telegramDataLines": telegram_data_lines,
        "symbolLine": symbol_line,
        "severityLine": severity_line,
        "typeLine": type_line,
        "triggerLine": trigger_line,
        "dataLines": data_lines,
        "bulletLines": bullet_lines,
        "lines": lines,
        "rawLines": "\n".join(raw_lines),
        "readableMessage": readable_message,
        "body": body,
    }


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
    return rendered or context_value(values.get("body") or values.get("title") or "")


def render_notification(template: NotificationTemplate, context: Dict[str, object]) -> str:
    if template and template.enabled:
        return render_template(template.template, context)
    return render_template(BODY_TEMPLATE, context)


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
        "target",
        "messageTypeLabel",
        "triggerSummary",
        "headline",
        "statusHeadline",
        "titleHeadline",
        "targetLine",
        "triggerBlock",
        "criterionBlock",
        "criterionLines",
        "dataBlock",
        "divider",
        "telegramMessage",
        "telegramDataLines",
        "symbolLine",
        "severityLine",
        "typeLine",
        "triggerLine",
        "dataLines",
        "bulletLines",
        "lines",
        "rawLines",
        "readableMessage",
        "body",
    ]
