import re
from dataclasses import asdict, dataclass
from typing import Dict, List

from .portfolio import AlertEvent


DEFAULT_TEMPLATE = "{title}\n{lines}"
BODY_TEMPLATE = "{body}"

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
    raw_lines = [str(line) for line in event.lines if str(line).strip()]
    lines = "\n".join(["- " + line for line in raw_lines])
    body = "\n".join([event.title] + ([lines] if lines else []))
    return {
        "messageType": event.rule,
        "accountId": event.account_id,
        "accountLabel": event.account_label,
        "severity": event.severity,
        "rule": event.rule,
        "key": event.key,
        "title": event.title,
        "symbol": event.symbol,
        "target": event.target(),
        "lines": lines,
        "rawLines": "\n".join(raw_lines),
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
        "rule",
        "key",
        "title",
        "symbol",
        "target",
        "lines",
        "rawLines",
        "body",
    ]
