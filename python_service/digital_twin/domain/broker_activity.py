"""Broker activity capabilities and normalized append-only activity facts."""

from dataclasses import asdict, dataclass, field
from decimal import Decimal
import hashlib
from typing import Dict, Iterable, List

from .portfolio_ledger import (
    BUY,
    CASH_DEPOSIT,
    CASH_WITHDRAWAL,
    DIVIDEND,
    FEE,
    SELL,
    SPLIT,
    PortfolioLedgerEntry,
    decimal_value,
)


BROKER_ACTIVITY_VERSION = "broker-activity-v1"
SUPPORTED_ACTIVITY_TYPES = {BUY, SELL, CASH_DEPOSIT, CASH_WITHDRAWAL, DIVIDEND, FEE, SPLIT}


def stable_activity_id(prefix: str, *values: object) -> str:
    raw = "|".join(str(value or "") for value in values)
    return prefix + ":" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class BrokerActivityCapabilities:
    provider: str
    account_activity_api: bool = False
    orders_api: bool = False
    fills_api: bool = False
    cash_movements_api: bool = False
    dividends_api: bool = False
    csv_import: bool = True
    source: str = "provider-contract"
    checked_at: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def automatic_sync_supported(self) -> bool:
        return bool(self.account_activity_api or self.fills_api or self.cash_movements_api)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["automaticSyncSupported"] = self.automatic_sync_supported
        payload["version"] = BROKER_ACTIVITY_VERSION
        return payload


def provider_activity_capabilities(provider: str, checked_at: str = "") -> BrokerActivityCapabilities:
    key = str(provider or "").strip().lower()
    if key == "toss":
        return BrokerActivityCapabilities(
            provider="toss",
            checked_at=checked_at,
            notes=["현재 연결된 Toss 계좌 어댑터는 잔고·시세만 사용하며 거래 활동 API는 연결되지 않았습니다."],
        )
    if key == "kis":
        return BrokerActivityCapabilities(
            provider="kis",
            checked_at=checked_at,
            notes=["현재 KIS 연결은 시세·수급 전용이며 계좌 체결 어댑터는 구성되지 않았습니다."],
        )
    return BrokerActivityCapabilities(
        provider=key or "unknown",
        checked_at=checked_at,
        notes=["자동 거래 활동 기능을 확인할 공급자 어댑터가 없습니다."],
    )


@dataclass(frozen=True)
class BrokerActivity:
    activity_id: str
    account_id: str
    provider: str
    activity_type: str
    occurred_at: str
    source_reference: str
    symbol: str = ""
    currency: str = "KRW"
    quantity: Decimal = Decimal("0")
    unit_price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    payload: Dict[str, object] = field(default_factory=dict)

    @classmethod
    def create(cls, account_id: str, provider: str, activity_type: str, occurred_at: str, **values):
        activity_kind = str(activity_type or "").upper().strip()
        if activity_kind not in SUPPORTED_ACTIVITY_TYPES:
            raise ValueError("Unsupported broker activity type: " + activity_kind)
        source_reference = str(values.get("source_reference") or values.get("sourceReference") or "").strip()
        if not source_reference:
            source_reference = stable_activity_id(
                "broker-activity-source",
                provider,
                account_id,
                activity_kind,
                occurred_at,
                values.get("symbol"),
                values.get("quantity"),
                values.get("unit_price") or values.get("unitPrice"),
                values.get("amount"),
            )
        return cls(
            activity_id=str(values.get("activity_id") or values.get("activityId") or stable_activity_id("broker-activity", source_reference)),
            account_id=str(account_id or ""),
            provider=str(provider or "").lower(),
            activity_type=activity_kind,
            occurred_at=str(occurred_at or ""),
            source_reference=source_reference,
            symbol=str(values.get("symbol") or "").upper().strip(),
            currency=str(values.get("currency") or "KRW").upper().strip(),
            quantity=decimal_value(values.get("quantity")),
            unit_price=decimal_value(values.get("unit_price") or values.get("unitPrice")),
            amount=decimal_value(values.get("amount")),
            fee=decimal_value(values.get("fee")),
            payload=dict(values.get("payload") or {}),
        )

    def __post_init__(self) -> None:
        if not self.account_id or not self.provider or not self.occurred_at or not self.source_reference:
            raise ValueError("Broker activity requires account, provider, time, and source reference.")
        if self.quantity < 0 or self.unit_price < 0 or self.fee < 0:
            raise ValueError("Broker activity quantities, prices, and fees cannot be negative.")

    def to_ledger_entry(self, portfolio_id: str) -> PortfolioLedgerEntry:
        return PortfolioLedgerEntry.create(
            portfolio_id,
            self.account_id,
            self.activity_type,
            self.occurred_at,
            entry_id=stable_activity_id("ledger-activity", portfolio_id, self.source_reference),
            source_reference=self.provider + ":" + self.source_reference,
            symbol=self.symbol,
            currency=self.currency,
            quantity=self.quantity,
            unit_price=self.unit_price,
            amount=self.amount,
            fee=self.fee,
            payload={
                **self.payload,
                "brokerActivityId": self.activity_id,
                "provider": self.provider,
                "source": "broker-activity",
            },
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in ("quantity", "unit_price", "amount", "fee"):
            payload[key] = str(payload[key])
        payload["version"] = BROKER_ACTIVITY_VERSION
        return payload


@dataclass(frozen=True)
class BrokerActivitySyncState:
    sync_id: str
    portfolio_id: str
    account_id: str
    provider: str
    status: str
    capabilities: BrokerActivityCapabilities
    cursor: str = ""
    imported_count: int = 0
    rejected_count: int = 0
    last_activity_at: str = ""
    last_success_at: str = ""
    reason: str = ""
    missing_data: List[str] = field(default_factory=list)

    @classmethod
    def create(cls, portfolio_id: str, account_id: str, provider: str, status: str, capabilities, **values):
        return cls(
            sync_id=stable_activity_id("broker-activity-sync", portfolio_id, provider),
            portfolio_id=str(portfolio_id or ""),
            account_id=str(account_id or ""),
            provider=str(provider or "").lower(),
            status=str(status or "unavailable"),
            capabilities=capabilities,
            cursor=str(values.get("cursor") or ""),
            imported_count=max(0, int(values.get("imported_count") or values.get("importedCount") or 0)),
            rejected_count=max(0, int(values.get("rejected_count") or values.get("rejectedCount") or 0)),
            last_activity_at=str(values.get("last_activity_at") or values.get("lastActivityAt") or ""),
            last_success_at=str(values.get("last_success_at") or values.get("lastSuccessAt") or ""),
            reason=str(values.get("reason") or ""),
            missing_data=list(values.get("missing_data") or values.get("missingData") or []),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["capabilities"] = self.capabilities.to_dict()
        payload["version"] = BROKER_ACTIVITY_VERSION
        return payload


def activities_to_ledger_entries(portfolio_id: str, activities: Iterable[BrokerActivity]) -> List[PortfolioLedgerEntry]:
    return [activity.to_ledger_entry(portfolio_id) for activity in activities or []]
