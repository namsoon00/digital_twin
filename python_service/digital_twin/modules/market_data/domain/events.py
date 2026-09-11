from digital_twin.modules.market_data.domain.event_types import MONITORING_SNAPSHOT_COLLECTED, MONITORING_ALERTS_DETECTED, MONITORING_CYCLE_COMPLETED, MARKET_DATA_COLLECTED, EXTERNAL_FACT_CHANGED, EXTERNAL_PROVIDER_HEALTH_CHANGED
from dataclasses import asdict, dataclass, field
from digital_twin.modules.portfolio.contracts import AccountSnapshot, AlertEvent, utc_now_iso
from digital_twin.modules.reasoning.contracts import compact_snapshot_event_metadata
from digital_twin.shared_kernel.events import DomainEvent
from typing import Dict, Iterable, List, Mapping














def external_fact_changed_event(
    dataset_id: str,
    subject_key: str,
    provider_id: str,
    source_revision: str,
    source_as_of: str,
    change_type: str,
    changed_fields: Iterable[str] = None,
    reason: str = "",
) -> DomainEvent:
    dataset = str(dataset_id or "external").strip()
    subject = str(subject_key or "global").strip()
    return DomainEvent(
        name=EXTERNAL_FACT_CHANGED,
        aggregate_id=(dataset + ":" + subject)[:191],
        payload={
            "datasetId": dataset[:191],
            "subjectKey": subject[:191],
            "providerId": str(provider_id or "")[:96],
            "sourceRevision": str(source_revision or "")[:191],
            "sourceAsOf": str(source_as_of or "")[:80],
            "changeType": str(change_type or "revision")[:64],
            "changedFields": [str(item or "")[:120] for item in list(changed_fields or [])[:40] if str(item or "")],
            "reason": str(reason or "")[:500],
        },
        correlation_id=("external-fact:" + dataset + ":" + subject)[:191],
    )


def external_provider_health_changed_event(
    provider_id: str,
    bucket_id: str,
    previous_state: str,
    current_state: str,
    message: str = "",
) -> DomainEvent:
    provider = str(provider_id or "external").strip()
    bucket = str(bucket_id or "default").strip()
    return DomainEvent(
        name=EXTERNAL_PROVIDER_HEALTH_CHANGED,
        aggregate_id=("external-provider:" + provider + ":" + bucket)[:191],
        payload={
            "providerId": provider[:96],
            "bucketId": bucket[:191],
            "previousState": str(previous_state or "unknown")[:32],
            "currentState": str(current_state or "unknown")[:32],
            "message": str(message or "")[:500],
        },
        correlation_id=("external-provider-health:" + provider + ":" + bucket)[:191],
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
            "valuationSnapshotId": snapshot.portfolio.valuation_snapshot_id,
            "valuationBasis": snapshot.portfolio.valuation_basis or "legacy-unknown",
            "brokerGrossTotal": snapshot.portfolio.broker_gross_total,
            "brokerNetTotal": snapshot.portfolio.broker_net_total,
            "markToMarketTotal": snapshot.portfolio.mark_to_market_total,
            "metadata": compact_snapshot_event_metadata(getattr(snapshot, "metadata", {}) or {}),
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
    symbols = [str(symbol or "").upper().strip() for symbol in (payload.get("changedSymbols") or payload.get("symbols") or []) if str(symbol or "").strip()]
    material_symbols = [str(symbol or "").upper().strip() for symbol in (payload.get("materialChangedSymbols") or []) if str(symbol or "").strip()]
    raw_changed_fields = payload.get("changedFieldsBySymbol") if isinstance(payload.get("changedFieldsBySymbol"), dict) else {}
    raw_revisions = payload.get("factRevisionsBySymbol") if isinstance(payload.get("factRevisionsBySymbol"), dict) else {}
    changed_fields = {}
    revisions = {}
    for symbol in symbols[:200]:
        fields = raw_changed_fields.get(symbol)
        if fields is None:
            fields = raw_changed_fields.get(symbol.upper())
        if isinstance(fields, (list, tuple, set)):
            changed_fields[symbol] = [str(field or "").strip() for field in fields if str(field or "").strip()][:80]
        revision = str(raw_revisions.get(symbol) or raw_revisions.get(symbol.upper()) or "").strip()
        if revision:
            revisions[symbol] = revision[:160]
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
            "changedFieldsBySymbol": changed_fields,
            "factRevisionsBySymbol": revisions,
            "materialChangedCount": int(payload.get("materialChangedCount") or len(material_symbols) or 0),
            "materialChangedSymbols": material_symbols[:200],
            "materialityAssessments": dict(payload.get("materialityAssessments") or {}),
            "status": str(payload.get("status") or ""),
            "dataQuality": str(payload.get("dataQuality") or "actual"),
            "quoteQualityCounts": dict(payload.get("quoteQualityCounts") or {}),
            "marketSessionCounts": dict(payload.get("marketSessionCounts") or {}),
        },
    )
