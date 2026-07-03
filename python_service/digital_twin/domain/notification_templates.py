import html
import re
from dataclasses import asdict, dataclass
from typing import Dict, List

from .portfolio import AlertEvent


LEGACY_DEFAULT_TEMPLATE = "{title}\n{lines}"
PREVIOUS_DEFAULT_TEMPLATE = "{readableMessage}"
DEFAULT_TEMPLATE = "{telegramMessage}"
BODY_TEMPLATE = "{body}"
MESSAGE_DIVIDER = "━━━━━━━━━━━━━━━━━━━━"

MESSAGE_TYPE_LABELS = {
    "modelBuy": "모델 매수",
    "modelSell": "모델 매도",
    "watchlistQuote": "관심종목 시세",
    "watchlistQuotePending": "관심종목 시세 대기",
    "holdingTiming": "보유 타이밍",
    "monitorHeartbeat": "실시간 상태",
    "monitorConnection": "연결 상태",
    "monitorPositionChange": "보유 변화",
    "monitorPnlChange": "손익률 변화",
    "monitorValueChange": "평가액 변화",
    "monitorTrendChange": "이동평균 변화",
    "monitorCashChange": "현금비중 변화",
    "monitorDecisionChange": "판단 변화",
    "externalEquityMove": "미장 가격/거래량",
    "externalCryptoMove": "크립토 변동",
    "externalMacroShift": "거시 지표 변화",
    "externalDartDisclosure": "국내 공시",
    "externalDataConnection": "외부 데이터 연결",
}

TRIGGER_SUMMARIES = {
    "modelBuy": "내 매수 모델 점수가 기준을 넘을 때 보냅니다.",
    "modelSell": "내 매도 모델 점수가 기준을 넘을 때 보냅니다.",
    "watchlistQuote": "관심 종목의 시세와 추세 데이터가 갱신될 때 보냅니다.",
    "watchlistQuotePending": "관심 종목 시세를 아직 받지 못했을 때 보냅니다.",
    "holdingTiming": "보유 종목의 매수·매도 점검 데이터가 기준에 걸릴 때 보냅니다.",
    "monitorHeartbeat": "실시간 모니터링 워커가 정상 작동 중인지 확인할 때 보냅니다.",
    "monitorConnection": "Toss 연결 상태가 바뀔 때 보냅니다.",
    "monitorPositionChange": "새 보유, 제외, 수량 변경이 감지될 때 보냅니다.",
    "monitorPnlChange": "직전 스냅샷 대비 손익률 변화가 임계값을 넘을 때 보냅니다.",
    "monitorValueChange": "직전 스냅샷 대비 평가액 변화가 임계값을 넘을 때 보냅니다.",
    "monitorTrendChange": "이동평균 돌파, 크로스, 큰 괴리가 감지될 때 보냅니다.",
    "monitorCashChange": "시장별 현금 비중 변화가 임계값을 넘을 때 보냅니다.",
    "monitorDecisionChange": "종목 판단이나 리스크 점수가 바뀔 때 보냅니다.",
    "externalEquityMove": "Alpha Vantage 기준 미국 보유 종목의 가격 변화가 임계값을 넘을 때 보냅니다.",
    "externalCryptoMove": "CoinGecko 기준 크립토 가격 변화가 임계값을 넘을 때 보냅니다.",
    "externalMacroShift": "FRED 금리·스프레드 변화가 임계값을 넘을 때 보냅니다.",
    "externalDartDisclosure": "OpenDART에서 보유 국내 종목의 새 공시가 감지될 때 보냅니다.",
    "externalDataConnection": "외부 데이터 API 응답 오류나 호출 제한이 감지될 때 보냅니다.",
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
    bullet_lines = "\n".join(["• " + line for line in raw_lines])
    message_type_label = MESSAGE_TYPE_LABELS.get(event.rule, event.rule)
    severity_label = SEVERITY_LABELS.get(str(event.severity or "").upper(), event.severity or "")
    trigger_summary = TRIGGER_SUMMARIES.get(event.rule, "설정한 조건이 실제 데이터에서 충족될 때 보냅니다.")
    symbol_line = ("종목: " + event.symbol) if event.symbol else ""
    severity_line = ("상태: " + severity_label) if severity_label else ""
    type_line = ("유형: " + message_type_label) if message_type_label else ""
    trigger_line = ("발생 조건: " + trigger_summary) if trigger_summary else ""
    data_lines = lines
    if severity_label and message_type_label:
        headline = "[" + severity_label + "] " + message_type_label
    elif message_type_label:
        headline = message_type_label
    elif severity_label:
        headline = "[" + severity_label + "] " + event.title
    else:
        headline = event.title
    target_parts = [event.title]
    if event.symbol and event.symbol != event.title:
        target_parts.append(event.symbol)
    target_value = " / ".join(part for part in target_parts if part)
    target_line = "대상: " + target_value if target_value else ""
    trigger_block = ("조건\n• " + trigger_summary) if trigger_summary else ""
    data_block = ("데이터\n" + bullet_lines) if bullet_lines else ""
    readable_parts = [MESSAGE_DIVIDER, headline, target_line, MESSAGE_DIVIDER, "", trigger_block]
    if bullet_lines:
        readable_parts.extend(["", data_block])
    readable_message = "\n".join(part for part in readable_parts if str(part).strip() or part == "").strip()
    escaped_headline = html.escape(headline, quote=False)
    escaped_target = html.escape(target_value, quote=False)
    escaped_trigger = html.escape(trigger_summary, quote=False)
    telegram_data_lines = "\n".join(["• " + html.escape(line, quote=False) for line in raw_lines])
    telegram_parts = [
        MESSAGE_DIVIDER,
        "<b>" + escaped_headline + "</b>",
        ("<code>" + escaped_target + "</code>") if escaped_target else "",
        MESSAGE_DIVIDER,
        "",
        "<b>조건</b>",
        "• " + escaped_trigger if escaped_trigger else "",
    ]
    if telegram_data_lines:
        telegram_parts.extend(["", "<b>데이터</b>", telegram_data_lines])
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
        "targetLine": target_line,
        "triggerBlock": trigger_block,
        "dataBlock": data_block,
        "divider": MESSAGE_DIVIDER,
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
        "targetLine",
        "triggerBlock",
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
