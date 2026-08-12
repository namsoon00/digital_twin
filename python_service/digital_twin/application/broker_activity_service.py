"""Broker activity import and append-only ledger synchronization use cases."""

from datetime import datetime, timezone
from typing import Dict, Iterable, List

from ..domain.broker_activity import (
    BrokerActivity,
    BrokerActivitySyncState,
    activities_to_ledger_entries,
    provider_activity_capabilities,
)
from ..domain.portfolio_ledger import OPENING_CASH, OPENING_POSITION
from ..infrastructure.broker_activity_csv import parse_broker_activity_csv


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class BrokerActivitySyncService:
    """Persist normalized incremental activities without rewriting opening balances."""

    def __init__(self, repository):
        self.repository = repository

    def record_capabilities(self, account_id: str, provider: str) -> BrokerActivitySyncState:
        portfolio_id = "portfolio:" + str(account_id or "default")
        capabilities = provider_activity_capabilities(provider, utc_now_iso())
        existing = self.repository.broker_activity_sync_state(portfolio_id) or {}
        status = "ready" if capabilities.automatic_sync_supported else "manual-import-required"
        state = BrokerActivitySyncState.create(
            portfolio_id,
            account_id,
            provider,
            status,
            capabilities,
            cursor=existing.get("cursor"),
            imported_count=existing.get("imported_count") or existing.get("importedCount"),
            rejected_count=existing.get("rejected_count") or existing.get("rejectedCount"),
            last_activity_at=existing.get("last_activity_at") or existing.get("lastActivityAt"),
            last_success_at=existing.get("last_success_at") or existing.get("lastSuccessAt"),
            reason="자동 거래 활동 API가 연결되지 않아 CSV 증분 수입을 사용합니다." if not capabilities.automatic_sync_supported else "",
            missing_data=[] if capabilities.automatic_sync_supported else ["brokerTradeHistory", "brokerCashMovements"],
        )
        if existing and str(existing.get("provider") or "").lower() == str(provider or "").lower():
            return state
        return self.repository.save_broker_activity_sync_state(state)

    def import_csv(self, account_id: str, provider: str, content: str) -> Dict[str, object]:
        parsed = parse_broker_activity_csv(account_id, provider, content)
        return self.import_activities(account_id, provider, parsed.get("activities") or [], parsed.get("rejected") or [])

    def import_activities(
        self,
        account_id: str,
        provider: str,
        activities: Iterable[BrokerActivity],
        rejected: List[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        portfolio_id = "portfolio:" + str(account_id or "default")
        existing_entries = list(self.repository.ledger_entries(portfolio_id, limit=100000) or [])
        opening_times = [
            str(item.occurred_at or "")
            for item in existing_entries
            if item.entry_type in {OPENING_POSITION, OPENING_CASH} and str(item.occurred_at or "")
        ]
        opening_boundary = max(opening_times) if opening_times else ""
        opening_boundary_at = parse_timestamp(opening_boundary)
        activity_rows = list(activities or [])
        valid = []
        rejected_rows = list(rejected or [])
        seen = {str(item.source_reference or "") for item in existing_entries}
        for activity in sorted(activity_rows, key=lambda item: (item.occurred_at, item.activity_id)):
            source_reference = activity.provider + ":" + activity.source_reference
            occurred_at = parse_timestamp(activity.occurred_at)
            if not occurred_at:
                rejected_rows.append({
                    "activityId": activity.activity_id,
                    "reason": "invalid-activity-timestamp",
                    "occurredAt": activity.occurred_at,
                })
                continue
            if opening_boundary_at and occurred_at <= opening_boundary_at:
                rejected_rows.append({
                    "activityId": activity.activity_id,
                    "reason": "activity-not-after-opening-balance",
                    "occurredAt": activity.occurred_at,
                    "openingBalanceAt": opening_boundary,
                })
                continue
            if source_reference in seen:
                continue
            seen.add(source_reference)
            valid.append(activity)
        entries = activities_to_ledger_entries(portfolio_id, valid)
        inserted = self.repository.append_ledger_entries(entries)
        capabilities = provider_activity_capabilities(provider, utc_now_iso())
        previous = self.repository.broker_activity_sync_state(portfolio_id) or {}
        last_activity_at = max([item.occurred_at for item in valid], default=str(previous.get("lastActivityAt") or ""))
        state = BrokerActivitySyncState.create(
            portfolio_id,
            account_id,
            provider,
            "imported" if inserted else ("rejected" if rejected_rows else "unchanged"),
            capabilities,
            imported_count=int(previous.get("imported_count") or previous.get("importedCount") or 0) + inserted,
            rejected_count=int(previous.get("rejected_count") or previous.get("rejectedCount") or 0) + len(rejected_rows),
            last_activity_at=last_activity_at,
            last_success_at=utc_now_iso() if inserted else str(previous.get("lastSuccessAt") or ""),
            reason="증분 거래 활동만 반영했습니다." if inserted else "새로 반영할 거래 활동이 없습니다.",
            missing_data=[] if capabilities.automatic_sync_supported else ["automaticBrokerActivityApi"],
        )
        self.repository.save_broker_activity_sync_state(state)
        return {
            "status": state.status,
            "portfolioId": portfolio_id,
            "parsedCount": len(activity_rows),
            "acceptedCount": len(valid),
            "insertedCount": inserted,
            "rejectedCount": len(rejected_rows),
            "rejected": rejected_rows,
            "openingBalanceAt": opening_boundary,
            "syncState": state.to_dict(),
        }
