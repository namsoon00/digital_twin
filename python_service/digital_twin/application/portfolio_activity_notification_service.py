"""Durable factual notification for complete live-account balance changes."""

from typing import Dict

from ..domain.message_types import PORTFOLIO_ACTIVITY_OBSERVATION
from ..domain.notifications import NotificationJob


ACTIVITY_LABELS = {
    "probable-buy": "매수와 일치할 수 있는 보유 증가",
    "probable-sell": "매도와 일치할 수 있는 보유 감소",
    "position-balance-change": "보유 수량 변화",
    "cash-balance-change": "현금 잔액 변화",
    "possible-corporate-action": "기업행동 가능성",
    "mixed-portfolio-change": "복합 계좌 변화",
}


def number_text(value: object) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except (TypeError, ValueError):
        return str(value or "0")


def portfolio_activity_notification_job(
    episode,
    source_event,
    account_label: str = "",
) -> NotificationJob:
    payload: Dict[str, object] = episode.to_dict()
    symbols = list(payload.get("symbols") or [])
    title = ACTIVITY_LABELS.get(str(payload.get("classification") or ""), "실계좌 잔고 변화")
    rows = [
        "[관찰] 실계좌 보유 변화",
        "• 변화: " + title,
        "• 대상: " + (", ".join(symbols) if symbols else "현금 잔액"),
    ]
    for item in payload.get("instrumentChanges") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            "• "
            + str(item.get("instrumentName") or item.get("symbol") or "종목")
            + ": "
            + str(item.get("previousQuantity") or "0")
            + " → "
            + str(item.get("observedQuantity") or "0")
        )
    if str(payload.get("cashDelta") or "0") not in {"", "0", "0.0"}:
        rows.append("• 현금 증감: " + number_text(payload.get("cashDelta")) + "원")
    rows.extend([
        "• 신뢰도: " + str(payload.get("confidence") or "low") + " · 실제 주문·수수료·세금은 확인되지 않음",
        "• 기준 시각: " + str(payload.get("observedAt") or ""),
        "• 후속 처리: 이 사실을 포함한 TypeDB 관계 추론과 AI 판단은 별도 알림으로 전달",
    ])
    text = "\n".join(rows)
    context = {
        "messageType": PORTFOLIO_ACTIVITY_OBSERVATION,
        "accountId": payload.get("accountId"),
        "accountLabel": str(account_label or ""),
        "displayTarget": ", ".join(symbols) if symbols else "계좌 현금",
        "title": title,
        "rawTitle": title,
        "readableMessage": text,
        "telegramMessage": text,
        "rawLines": text,
        "portfolioActivityEpisode": payload,
        "eventGeneratedAt": str(getattr(source_event, "occurred_at", "") or payload.get("observedAt") or ""),
        "notificationSignals": ["portfolioBalanceChanged", "factualObservation"],
        "criteria": ["완전한 실계좌 잔고", "직전 체크포인트와 다른 수량 또는 현금", "주문 원인 미확정"],
        "aiDecisionRequired": False,
    }
    return NotificationJob.create(
        text,
        account_id=str(payload.get("accountId") or ""),
        account_label=str(account_label or ""),
        message_type=PORTFOLIO_ACTIVITY_OBSERVATION,
        source_event_id=str(getattr(source_event, "event_id", "") or ""),
        source_event_name=str(getattr(source_event, "name", "") or ""),
        dedupe_key="portfolio-activity:" + str(payload.get("episodeId") or ""),
        context=context,
    )
