import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List

from .accounts import AccountConfig
from .portfolio import AccountSnapshot, AlertEvent, utc_now_iso


ACCOUNT_SAVED = "account.saved"
ACCOUNT_REMOVED = "account.removed"
MONITORING_SNAPSHOT_COLLECTED = "monitoring.snapshot_collected"
MONITORING_ALERTS_DETECTED = "monitoring.alerts_detected"
MONITORING_CYCLE_COMPLETED = "monitoring.cycle_completed"
SETTINGS_UPDATED = "settings.updated"
NOTIFICATION_TEMPLATE_UPDATED = "notification_template.updated"
NOTIFICATION_RULE_UPDATED = "notification_rule.updated"
NOTIFICATION_TEST_REQUESTED = "notification.test_requested"
NOTIFICATION_JOB_QUEUED = "notification.job_queued"
APP_PROFILE_UPDATED = "app.profile_updated"
APP_MEMORY_RECORDED = "app.memory_recorded"
APP_MEMORY_UPDATED = "app.memory_updated"
APP_MEMORY_REMOVED = "app.memory_removed"
APP_ITEM_UPDATED = "app.item_updated"
APP_ITEM_REMOVED = "app.item_removed"
CHAT_MESSAGE_APPENDED = "chat.message_appended"
SYMBOL_UNIVERSE_REFRESHED = "symbol_universe.refreshed"
MARKET_DATA_COLLECTED = "market_data.collected"


@dataclass(frozen=True)
class DomainEvent:
    name: str
    aggregate_id: str
    payload: Dict[str, object] = field(default_factory=dict)
    occurred_at: str = field(default_factory=utc_now_iso)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        return cls(
            name=str(payload.get("name") or ""),
            aggregate_id=str(payload.get("aggregate_id") or payload.get("aggregateId") or ""),
            payload=dict(payload.get("payload") or {}),
            occurred_at=str(payload.get("occurred_at") or payload.get("occurredAt") or utc_now_iso()),
            event_id=str(payload.get("event_id") or payload.get("eventId") or uuid.uuid4().hex),
            correlation_id=str(payload.get("correlation_id") or payload.get("correlationId") or ""),
        )


def account_saved_event(account: AccountConfig) -> DomainEvent:
    return DomainEvent(
        name=ACCOUNT_SAVED,
        aggregate_id=account.account_id,
        payload={"account": account.masked()},
    )


def account_removed_event(account_id: str) -> DomainEvent:
    return DomainEvent(
        name=ACCOUNT_REMOVED,
        aggregate_id=account_id,
        payload={"accountId": account_id},
    )


def snapshot_collected_event(snapshot: AccountSnapshot) -> DomainEvent:
    return DomainEvent(
        name=MONITORING_SNAPSHOT_COLLECTED,
        aggregate_id=snapshot.account_id,
        payload={
            "accountId": snapshot.account_id,
            "accountLabel": snapshot.account_label,
            "provider": snapshot.provider,
            "mode": snapshot.mode,
            "status": snapshot.status,
            "generatedAt": snapshot.generated_at,
            "positionCount": len([item for item in snapshot.positions if not item.is_cash()]),
            "decisionCount": len(snapshot.decisions),
            "portfolioTotal": snapshot.portfolio.total,
            "portfolioInvested": snapshot.portfolio.invested,
            "portfolioCash": snapshot.portfolio.cash,
        },
    )


def alerts_detected_event(events: Iterable[AlertEvent]) -> DomainEvent:
    items = list(events)
    account_ids = sorted(set(item.account_id for item in items))
    rules = sorted(set(item.rule for item in items))
    symbols = sorted(set(item.symbol for item in items if item.symbol))
    return DomainEvent(
        name=MONITORING_ALERTS_DETECTED,
        aggregate_id=",".join(account_ids) or "all",
        payload={
            "accountIds": account_ids,
            "count": len(items),
            "rules": rules,
            "symbols": symbols,
            "events": [
                {
                    "accountId": item.account_id,
                    "accountLabel": item.account_label,
                    "severity": item.severity,
                    "rule": item.rule,
                    "key": item.key,
                    "title": item.title,
                    "symbol": item.symbol,
                    "lines": item.lines,
                    "criteria": list(getattr(item, "criteria", []) or []),
                }
                for item in items
            ],
        },
    )


def monitoring_cycle_completed_event(
    account_ids: List[str],
    snapshot_count: int,
    alert_count: int,
    dry_run: bool,
    delivered: bool,
) -> DomainEvent:
    return DomainEvent(
        name=MONITORING_CYCLE_COMPLETED,
        aggregate_id=",".join(account_ids) or "all",
        payload={
            "accountIds": account_ids,
            "snapshotCount": snapshot_count,
            "alertCount": alert_count,
            "dryRun": dry_run,
            "delivered": delivered,
        },
    )


def market_data_collected_event(payload: Dict[str, object]) -> DomainEvent:
    provider = str(payload.get("provider") or "market-data")
    markets = ",".join(str(market) for market in payload.get("markets") or []) or "all"
    return DomainEvent(
        name=MARKET_DATA_COLLECTED,
        aggregate_id=provider + ":" + markets,
        payload={
            "provider": provider,
            "markets": list(payload.get("markets") or []),
            "selectedCount": int(payload.get("selectedCount") or 0),
            "priceCount": int(payload.get("priceCount") or 0),
            "candleCount": int(payload.get("candleCount") or 0),
            "savedCount": int(payload.get("savedCount") or 0),
            "status": str(payload.get("status") or ""),
            "dataQuality": str(payload.get("dataQuality") or "actual"),
        },
    )
