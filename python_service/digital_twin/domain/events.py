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
RESEARCH_EVIDENCE_COLLECTED = "research_evidence.collected"
ONTOLOGY_REASONING_REQUESTED = "ontology.reasoning_requested"
ONTOLOGY_REASONING_COMPLETED = "ontology.reasoning_completed"
INVESTMENT_CALENDAR_EVENT_SAVED = "investment_calendar.event_saved"
INVESTMENT_CALENDAR_EVENT_REMOVED = "investment_calendar.event_removed"
INVESTMENT_CALENDAR_REMINDER_DUE = "investment_calendar.reminder_due"
INVESTMENT_STRATEGY_PROPOSED = "investment_strategy.proposed"
INVESTMENT_STRATEGY_VALIDATED = "investment_strategy.validated"
INVESTMENT_STRATEGY_APPROVED = "investment_strategy.approved"
INVESTMENT_STRATEGY_DEPLOYED = "investment_strategy.deployed"
INVESTMENT_STRATEGY_PERFORMANCE_RECORDED = "investment_strategy.performance_recorded"


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
            "metadata": dict(getattr(snapshot, "metadata", {}) or {}),
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
                    "metadata": dict(getattr(item, "metadata", {}) or {}),
                    "generatedAt": getattr(item, "generated_at", ""),
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
    symbols = list(payload.get("changedSymbols") or payload.get("symbols") or [])
    material_symbols = list(payload.get("materialChangedSymbols") or [])
    return DomainEvent(
        name=MARKET_DATA_COLLECTED,
        aggregate_id=provider + ":" + markets,
        payload={
            "provider": provider,
            "markets": list(payload.get("markets") or []),
            "symbols": symbols[:200],
            "selectedCount": int(payload.get("selectedCount") or 0),
            "priceCount": int(payload.get("priceCount") or 0),
            "candleCount": int(payload.get("candleCount") or 0),
            "savedCount": int(payload.get("savedCount") or 0),
            "changedCount": int(payload.get("changedCount") or 0),
            "changedSymbols": symbols[:200],
            "materialChangedCount": int(payload.get("materialChangedCount") or len(material_symbols) or 0),
            "materialChangedSymbols": material_symbols[:200],
            "materialityAssessments": dict(payload.get("materialityAssessments") or {}),
            "status": str(payload.get("status") or ""),
            "dataQuality": str(payload.get("dataQuality") or "actual"),
        },
    )


def research_evidence_collected_event(payload: Dict[str, object]) -> DomainEvent:
    symbols = list(payload.get("symbols") or [])
    material_symbols = list(payload.get("materialChangedSymbols") or [])
    return DomainEvent(
        name=RESEARCH_EVIDENCE_COLLECTED,
        aggregate_id="news:" + (",".join(str(symbol) for symbol in symbols) or "all")[:180],
        payload={
            "source": "news-collection",
            "status": str(payload.get("status") or ""),
            "targetCount": int(payload.get("targetCount") or 0),
            "fetchedCount": int(payload.get("fetchedCount") or 0),
            "savedCount": int(payload.get("savedCount") or 0),
            "changedCount": int(payload.get("changedCount") or payload.get("savedCount") or 0),
            "symbols": symbols[:100],
            "changedSymbols": list(payload.get("changedSymbols") or symbols)[:100],
            "materialChangedCount": int(payload.get("materialChangedCount") or len(material_symbols) or 0),
            "materialChangedSymbols": material_symbols[:100],
            "changedItems": list(payload.get("changedItems") or [])[:100],
            "materialChangedItems": list(payload.get("materialChangedItems") or [])[:100],
            "materialityAssessments": list(payload.get("materialityAssessments") or [])[:100],
            "providers": list(payload.get("providers") or [])[:20],
        },
    )


def ontology_reasoning_requested_event(
    source_event: DomainEvent,
    trigger: str,
    symbols: Iterable[str] = None,
    changed_count: int = 0,
    observed_count: int = 0,
    fact_types: Iterable[str] = None,
    reason: str = "",
    materiality_assessments=None,
) -> DomainEvent:
    clean_symbols = sorted(set(str(symbol or "").upper().strip() for symbol in (symbols or []) if str(symbol or "").strip()))
    clean_fact_types = sorted(set(str(item or "").strip() for item in (fact_types or []) if str(item or "").strip()))
    return DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id="ontology:" + (",".join(clean_symbols) or str(trigger or "all"))[:180],
        correlation_id=source_event.correlation_id or source_event.event_id,
        payload={
            "trigger": str(trigger or "data-update"),
            "sourceEventId": source_event.event_id,
            "sourceEventName": source_event.name,
            "sourceAggregateId": source_event.aggregate_id,
            "symbols": clean_symbols[:200],
            "changedCount": int(changed_count or 0),
            "observedCount": int(observed_count or 0),
            "factTypes": clean_fact_types[:20],
            "reason": str(reason or ""),
            "dispatchMode": "data-update-driven",
            "importanceGate": "materiality-first",
            "materialityAssessments": materiality_assessments if materiality_assessments is not None else [],
        },
    )


def ontology_reasoning_completed_event(
    trigger_event_ids: Iterable[str],
    account_ids: Iterable[str],
    symbols: Iterable[str],
    alert_count: int,
    status: str = "ok",
    reason: str = "",
) -> DomainEvent:
    clean_trigger_ids = [str(item or "").strip() for item in (trigger_event_ids or []) if str(item or "").strip()]
    clean_accounts = sorted(set(str(item or "").strip() for item in (account_ids or []) if str(item or "").strip()))
    clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
    return DomainEvent(
        name=ONTOLOGY_REASONING_COMPLETED,
        aggregate_id="ontology:" + (",".join(clean_accounts) or "all")[:180],
        payload={
            "triggerEventIds": clean_trigger_ids[:200],
            "accountIds": clean_accounts[:100],
            "symbols": clean_symbols[:200],
            "alertCount": int(alert_count or 0),
            "status": str(status or "ok"),
            "reason": str(reason or ""),
            "dispatchMode": "data-update-driven",
        },
    )


def investment_strategy_proposed_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_PROPOSED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={"proposal": payload},
    )


def investment_strategy_validated_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_VALIDATED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "validation": dict(payload.get("validation") or {}),
        },
    )


def investment_strategy_approved_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    lifecycle = dict(payload.get("lifecycle") or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_APPROVED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "approvedAt": str(payload.get("approvedAt") or ""),
            "approvedBy": str(lifecycle.get("approvedBy") or ""),
            "approvalReason": str(lifecycle.get("approvalReason") or ""),
        },
    )


def investment_strategy_deployed_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_DEPLOYED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "deployedAt": str(payload.get("deployedAt") or ""),
            "ruleIds": list(payload.get("ruleIds") or []),
        },
    )


def investment_strategy_performance_recorded_event(proposal, sample: Dict[str, object]) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    performance = dict(payload.get("performance") or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_PERFORMANCE_RECORDED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "sample": dict(sample or {}),
            "summary": dict(performance.get("summary") or {}),
        },
    )


def investment_calendar_event_saved_event(calendar_event) -> DomainEvent:
    payload = calendar_event.to_dict() if hasattr(calendar_event, "to_dict") else dict(calendar_event or {})
    symbols = list(payload.get("symbols") or [])
    markets = list(payload.get("markets") or [])
    return DomainEvent(
        name=INVESTMENT_CALENDAR_EVENT_SAVED,
        aggregate_id=str(payload.get("eventId") or ""),
        payload={
            "event": payload,
            "eventId": str(payload.get("eventId") or ""),
            "title": str(payload.get("title") or ""),
            "eventType": str(payload.get("eventType") or ""),
            "startsAt": str(payload.get("startsAt") or ""),
            "importance": int(payload.get("importance") or 0),
            "symbols": symbols[:100],
            "markets": markets[:50],
            "changedSymbols": symbols[:100],
            "changedCount": len(symbols),
        },
    )


def investment_calendar_event_removed_event(event_id: str) -> DomainEvent:
    return DomainEvent(
        name=INVESTMENT_CALENDAR_EVENT_REMOVED,
        aggregate_id=str(event_id or ""),
        payload={"eventId": str(event_id or "")},
    )


def investment_calendar_reminder_due_event(reminders: Iterable[object]) -> DomainEvent:
    items = [item.to_dict() if hasattr(item, "to_dict") else dict(item or {}) for item in reminders or []]
    event_ids = sorted(set(str(item.get("eventId") or "") for item in items if str(item.get("eventId") or "")))
    symbols = sorted(set(str(symbol or "").upper().strip() for item in items for symbol in (item.get("symbols") or []) if str(symbol or "").strip()))
    return DomainEvent(
        name=INVESTMENT_CALENDAR_REMINDER_DUE,
        aggregate_id="calendar:" + (",".join(event_ids) or "none")[:180],
        payload={
            "count": len(items),
            "eventIds": event_ids[:100],
            "symbols": symbols[:100],
            "reminders": items[:100],
        },
    )
