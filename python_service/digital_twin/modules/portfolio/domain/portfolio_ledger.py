"""Append-only portfolio ledger with deterministic position reconstruction."""

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, InvalidOperation
import uuid
import hashlib
import json
from typing import Dict, Iterable, List


PORTFOLIO_LEDGER_VERSION = "portfolio-ledger-v1"
BUY = "BUY"
SELL = "SELL"
CASH_DEPOSIT = "CASH_DEPOSIT"
CASH_WITHDRAWAL = "CASH_WITHDRAWAL"
DIVIDEND = "DIVIDEND"
FEE = "FEE"
SPLIT = "SPLIT"
SNAPSHOT_RECONCILIATION = "SNAPSHOT_RECONCILIATION"
OPENING_POSITION = "OPENING_POSITION"
OPENING_CASH = "OPENING_CASH"
INFERRED_POSITION_INCREASE = "INFERRED_POSITION_INCREASE"
INFERRED_POSITION_DECREASE = "INFERRED_POSITION_DECREASE"
INFERRED_POSITION_EXIT = "INFERRED_POSITION_EXIT"
INFERRED_CORPORATE_ACTION = "INFERRED_CORPORATE_ACTION"
SNAPSHOT_CASH_ADJUSTMENT = "SNAPSHOT_CASH_ADJUSTMENT"

INFERRED_SNAPSHOT_ENTRY_TYPES = {
    INFERRED_POSITION_INCREASE,
    INFERRED_POSITION_DECREASE,
    INFERRED_POSITION_EXIT,
    INFERRED_CORPORATE_ACTION,
    SNAPSHOT_CASH_ADJUSTMENT,
}


def decimal_value(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


@dataclass(frozen=True)
class PortfolioLedgerEntry:
    entry_id: str
    portfolio_id: str
    account_id: str
    entry_type: str
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
    def create(cls, portfolio_id: str, account_id: str, entry_type: str, occurred_at: str, **values):
        source_reference = str(values.get("source_reference") or values.get("sourceReference") or "")
        return cls(
            entry_id=str(values.get("entry_id") or values.get("entryId") or uuid.uuid4().hex),
            portfolio_id=str(portfolio_id or ""),
            account_id=str(account_id or ""),
            entry_type=str(entry_type or "").upper(),
            occurred_at=str(occurred_at or ""),
            source_reference=source_reference,
            symbol=str(values.get("symbol") or "").upper(),
            currency=str(values.get("currency") or "KRW").upper(),
            quantity=decimal_value(values.get("quantity")),
            unit_price=decimal_value(values.get("unit_price") or values.get("unitPrice")),
            amount=decimal_value(values.get("amount")),
            fee=decimal_value(values.get("fee")),
            payload=dict(values.get("payload") or {}),
        )

    def __post_init__(self) -> None:
        if not self.portfolio_id or not self.account_id or not self.entry_type:
            raise ValueError("Portfolio ledger entry requires portfolio, account, and entry type.")
        if self.quantity < 0 or self.unit_price < 0 or self.fee < 0:
            raise ValueError("Ledger quantities, prices, and fees cannot be negative.")

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in ("quantity", "unit_price", "amount", "fee"):
            payload[key] = str(payload[key])
        payload["version"] = PORTFOLIO_LEDGER_VERSION
        return payload


@dataclass(frozen=True)
class PositionLot:
    lot_id: str
    portfolio_id: str
    symbol: str
    currency: str
    opened_at: str
    quantity: Decimal
    remaining_quantity: Decimal
    unit_cost: Decimal
    source_reference: str = ""

    @property
    def cost_basis(self) -> Decimal:
        return self.remaining_quantity * self.unit_cost

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["quantity"] = str(self.quantity)
        payload["remaining_quantity"] = str(self.remaining_quantity)
        payload["unit_cost"] = str(self.unit_cost)
        payload["costBasis"] = str(self.cost_basis)
        return payload


@dataclass
class PortfolioLedgerState:
    portfolio_id: str
    account_id: str
    lots: List[PositionLot] = field(default_factory=list)
    cash: Dict[str, Decimal] = field(default_factory=dict)
    realized_profit_loss: Dict[str, Decimal] = field(default_factory=dict)
    applied_entry_ids: List[str] = field(default_factory=list)
    applied_source_references: List[str] = field(default_factory=list)

    def quantity(self, symbol: str) -> Decimal:
        key = str(symbol or "").upper()
        return sum((lot.remaining_quantity for lot in self.lots if lot.symbol == key), Decimal("0"))

    def average_cost(self, symbol: str) -> Decimal:
        key = str(symbol or "").upper()
        lots = [lot for lot in self.lots if lot.symbol == key and lot.remaining_quantity > 0]
        quantity = sum((lot.remaining_quantity for lot in lots), Decimal("0"))
        return (sum((lot.cost_basis for lot in lots), Decimal("0")) / quantity) if quantity else Decimal("0")

    def to_dict(self) -> Dict[str, object]:
        symbols = sorted({item.symbol for item in self.lots if item.remaining_quantity > 0})
        return {
            "version": PORTFOLIO_LEDGER_VERSION,
            "portfolioId": self.portfolio_id,
            "accountId": self.account_id,
            "positions": [
                {
                    "symbol": symbol,
                    "quantity": str(self.quantity(symbol)),
                    "averageCost": str(self.average_cost(symbol)),
                }
                for symbol in symbols
            ],
            "lots": [item.to_dict() for item in self.lots if item.remaining_quantity > 0],
            "cash": {key: str(value) for key, value in self.cash.items()},
            "realizedProfitLoss": {key: str(value) for key, value in self.realized_profit_loss.items()},
        }


class PortfolioLedger:
    def __init__(self, portfolio_id: str, account_id: str):
        self.state = PortfolioLedgerState(str(portfolio_id or ""), str(account_id or ""))

    def replay(self, entries: Iterable[PortfolioLedgerEntry]) -> PortfolioLedgerState:
        rows = list(entries or [])
        superseded_entry_ids = {
            str(superseded_id)
            for entry in rows
            for superseded_id in list((entry.payload or {}).get("supersedesEntryIds") or [])
            if str(superseded_id or "")
        }
        type_priority = {
            OPENING_POSITION: 0,
            OPENING_CASH: 0,
            INFERRED_POSITION_INCREASE: 2,
            INFERRED_POSITION_DECREASE: 2,
            INFERRED_POSITION_EXIT: 2,
            INFERRED_CORPORATE_ACTION: 2,
            SNAPSHOT_CASH_ADJUSTMENT: 2,
        }
        for entry in sorted(
            rows,
            key=lambda item: (item.occurred_at, type_priority.get(item.entry_type, 1), item.entry_id),
        ):
            if entry.entry_id in superseded_entry_ids:
                continue
            self.apply(entry)
        return self.state

    def apply(self, entry: PortfolioLedgerEntry) -> bool:
        if entry.portfolio_id != self.state.portfolio_id or entry.account_id != self.state.account_id:
            raise ValueError("Ledger entry aggregate does not match the ledger state.")
        if entry.entry_id in self.state.applied_entry_ids:
            return False
        if entry.source_reference and entry.source_reference in self.state.applied_source_references:
            return False
        handler = getattr(self, "_apply_" + entry.entry_type.lower(), None)
        if not callable(handler):
            raise ValueError("Unsupported portfolio ledger entry type: " + entry.entry_type)
        handler(entry)
        self.state.applied_entry_ids.append(entry.entry_id)
        if entry.source_reference:
            self.state.applied_source_references.append(entry.source_reference)
        return True

    def _apply_buy(self, entry: PortfolioLedgerEntry) -> None:
        if not entry.symbol or entry.quantity <= 0 or entry.unit_price <= 0:
            raise ValueError("BUY requires symbol, quantity, and unit price.")
        lot = PositionLot(
            lot_id="lot:" + entry.entry_id,
            portfolio_id=entry.portfolio_id,
            symbol=entry.symbol,
            currency=entry.currency,
            opened_at=entry.occurred_at,
            quantity=entry.quantity,
            remaining_quantity=entry.quantity,
            unit_cost=entry.unit_price + (entry.fee / entry.quantity if entry.quantity else Decimal("0")),
            source_reference=entry.source_reference,
        )
        self.state.lots.append(lot)
        cost = entry.amount or (entry.quantity * entry.unit_price)
        self.state.cash[entry.currency] = self.state.cash.get(entry.currency, Decimal("0")) - cost - entry.fee

    def _apply_opening_position(self, entry: PortfolioLedgerEntry) -> None:
        if not entry.symbol or entry.quantity <= 0 or entry.unit_price < 0:
            raise ValueError("OPENING_POSITION requires symbol and positive quantity.")
        self.state.lots.append(PositionLot(
            lot_id="opening-lot:" + entry.entry_id,
            portfolio_id=entry.portfolio_id,
            symbol=entry.symbol,
            currency=entry.currency,
            opened_at=entry.occurred_at,
            quantity=entry.quantity,
            remaining_quantity=entry.quantity,
            unit_cost=entry.unit_price,
            source_reference=entry.source_reference,
        ))

    def _apply_opening_cash(self, entry: PortfolioLedgerEntry) -> None:
        self.state.cash[entry.currency] = self.state.cash.get(entry.currency, Decimal("0")) + entry.amount

    def _apply_inferred_position_increase(self, entry: PortfolioLedgerEntry) -> None:
        if not entry.symbol or entry.quantity <= 0 or entry.unit_price < 0:
            raise ValueError("INFERRED_POSITION_INCREASE requires symbol and positive quantity.")
        self.state.lots.append(PositionLot(
            lot_id="inferred-lot:" + entry.entry_id,
            portfolio_id=entry.portfolio_id,
            symbol=entry.symbol,
            currency=entry.currency,
            opened_at=entry.occurred_at,
            quantity=entry.quantity,
            remaining_quantity=entry.quantity,
            unit_cost=entry.unit_price,
            source_reference=entry.source_reference,
        ))

    def _consume_inferred_position(self, entry: PortfolioLedgerEntry) -> None:
        if not entry.symbol or entry.quantity <= 0:
            raise ValueError(entry.entry_type + " requires symbol and positive quantity.")
        remaining = entry.quantity
        next_lots = []
        for lot in self.state.lots:
            if lot.symbol != entry.symbol or remaining <= 0 or lot.remaining_quantity <= 0:
                next_lots.append(lot)
                continue
            consumed = min(remaining, lot.remaining_quantity)
            remaining -= consumed
            next_lots.append(replace(lot, remaining_quantity=lot.remaining_quantity - consumed))
        if remaining > Decimal("0.000001"):
            raise ValueError(entry.entry_type + " quantity exceeds reconstructed position quantity.")
        self.state.lots = next_lots

    def _apply_inferred_position_decrease(self, entry: PortfolioLedgerEntry) -> None:
        self._consume_inferred_position(entry)

    def _apply_inferred_position_exit(self, entry: PortfolioLedgerEntry) -> None:
        self._consume_inferred_position(entry)

    def _apply_inferred_corporate_action(self, entry: PortfolioLedgerEntry) -> None:
        self._apply_split(entry)

    def _apply_snapshot_cash_adjustment(self, entry: PortfolioLedgerEntry) -> None:
        self.state.cash[entry.currency] = self.state.cash.get(entry.currency, Decimal("0")) + entry.amount

    def _apply_sell(self, entry: PortfolioLedgerEntry) -> None:
        if not entry.symbol or entry.quantity <= 0 or entry.unit_price <= 0:
            raise ValueError("SELL requires symbol, quantity, and unit price.")
        remaining = entry.quantity
        consumed_cost = Decimal("0")
        next_lots = []
        for lot in self.state.lots:
            if lot.symbol != entry.symbol or remaining <= 0 or lot.remaining_quantity <= 0:
                next_lots.append(lot)
                continue
            consumed = min(remaining, lot.remaining_quantity)
            remaining -= consumed
            consumed_cost += consumed * lot.unit_cost
            next_lots.append(replace(lot, remaining_quantity=lot.remaining_quantity - consumed))
        if remaining > 0:
            raise ValueError("SELL quantity exceeds reconstructed position quantity.")
        self.state.lots = next_lots
        proceeds = entry.amount or (entry.quantity * entry.unit_price)
        realized = proceeds - entry.fee - consumed_cost
        self.state.cash[entry.currency] = self.state.cash.get(entry.currency, Decimal("0")) + proceeds - entry.fee
        self.state.realized_profit_loss[entry.currency] = self.state.realized_profit_loss.get(entry.currency, Decimal("0")) + realized

    def _apply_cash_deposit(self, entry: PortfolioLedgerEntry) -> None:
        self.state.cash[entry.currency] = self.state.cash.get(entry.currency, Decimal("0")) + entry.amount

    def _apply_cash_withdrawal(self, entry: PortfolioLedgerEntry) -> None:
        self.state.cash[entry.currency] = self.state.cash.get(entry.currency, Decimal("0")) - entry.amount

    def _apply_dividend(self, entry: PortfolioLedgerEntry) -> None:
        self._apply_cash_deposit(entry)

    def _apply_fee(self, entry: PortfolioLedgerEntry) -> None:
        charge = entry.amount or entry.fee
        self.state.cash[entry.currency] = self.state.cash.get(entry.currency, Decimal("0")) - charge

    def _apply_split(self, entry: PortfolioLedgerEntry) -> None:
        ratio = decimal_value(entry.payload.get("ratio"))
        if not entry.symbol or ratio <= 0:
            raise ValueError("SPLIT requires symbol and positive ratio.")
        self.state.lots = [
            replace(
                lot,
                quantity=lot.quantity * ratio,
                remaining_quantity=lot.remaining_quantity * ratio,
                unit_cost=lot.unit_cost / ratio,
            ) if lot.symbol == entry.symbol else lot
            for lot in self.state.lots
        ]

    def _apply_snapshot_reconciliation(self, entry: PortfolioLedgerEntry) -> None:
        # Reconciliation remains an immutable audit fact. It does not silently
        # rewrite trade-derived lots; discrepancies are handled explicitly.
        return None


def execution_ledger_entries(
    episode: object,
    plan: object,
    existing_entries: Iterable[PortfolioLedgerEntry] = None,
) -> List[PortfolioLedgerEntry]:
    """Translate provider fills into idempotent actual ledger facts.

    A later provider fill can replace one matching snapshot-inferred position
    observation and its cash adjustment. The inferred rows stay immutable for
    audit, while replay ignores IDs listed by the actual fill.
    """
    portfolio_id = str(getattr(episode, "portfolio_id", "") or getattr(plan, "portfolio_id", ""))
    account_id = portfolio_id[len("portfolio:"):] if portfolio_id.startswith("portfolio:") else portfolio_id
    already_superseded = {
        str(superseded_id)
        for item in existing_entries or []
        for superseded_id in list((item.payload or {}).get("supersedesEntryIds") or [])
        if str(superseded_id or "")
    }
    inferred = [
        item for item in existing_entries or []
        if item.entry_type in INFERRED_SNAPSHOT_ENTRY_TYPES
        and bool((item.payload or {}).get("replaceableByActualActivity", False))
        and item.entry_id not in already_superseded
    ]
    claimed = set()
    fills = list(getattr(episode, "fills", None) or [])
    fill_groups = {}
    for fill in fills:
        key = (
            str(getattr(fill, "symbol", "") or "").upper(),
            str(getattr(fill, "side", "") or "").upper(),
        )
        fill_groups.setdefault(key, []).append(fill)
    superseded_by_provider_id = {}

    def claim_inferred(matched: PortfolioLedgerEntry) -> List[str]:
        superseded = [matched.entry_id]
        claimed.add(matched.entry_id)
        observation_id = str((matched.payload or {}).get("observationId") or "")
        for item in inferred:
            if (
                item.entry_id not in claimed
                and item.entry_type == SNAPSHOT_CASH_ADJUSTMENT
                and str((item.payload or {}).get("observationId") or "") == observation_id
            ):
                superseded.append(item.entry_id)
                claimed.add(item.entry_id)
        return superseded

    for (symbol, side), group in fill_groups.items():
        expected_types = (
            {INFERRED_POSITION_INCREASE}
            if side == BUY
            else {INFERRED_POSITION_DECREASE, INFERRED_POSITION_EXIT}
        )
        candidates = sorted(
            (
                item for item in inferred
                if item.entry_id not in claimed
                and item.entry_type in expected_types
                and item.symbol == symbol
            ),
            key=lambda item: (item.occurred_at, item.entry_id),
            reverse=True,
        )
        group_total = sum((decimal_value(getattr(fill, "quantity", 0)) for fill in group), Decimal("0"))
        aggregate_match = next(
            (item for item in candidates if abs(item.quantity - group_total) <= Decimal("0.000001")),
            None,
        )
        if aggregate_match:
            latest_fill = max(
                group,
                key=lambda item: (
                    str(getattr(item, "executed_at", "") or ""),
                    str(getattr(item, "provider_execution_id", "") or ""),
                ),
            )
            superseded_by_provider_id[str(getattr(latest_fill, "provider_execution_id", "") or "")] = claim_inferred(
                aggregate_match
            )
            continue
        for fill in sorted(group, key=lambda item: str(getattr(item, "executed_at", "") or "")):
            quantity = decimal_value(getattr(fill, "quantity", 0))
            matched = next(
                (item for item in candidates if item.entry_id not in claimed and abs(item.quantity - quantity) <= Decimal("0.000001")),
                None,
            )
            if matched:
                superseded_by_provider_id[str(getattr(fill, "provider_execution_id", "") or "")] = claim_inferred(matched)

    result = []
    for fill in fills:
        symbol = str(getattr(fill, "symbol", "") or "").upper()
        side = str(getattr(fill, "side", "") or "").upper()
        quantity = decimal_value(getattr(fill, "quantity", 0))
        provider_execution_id = str(getattr(fill, "provider_execution_id", "") or "")
        result.append(PortfolioLedgerEntry.create(
            portfolio_id,
            account_id,
            BUY if side == BUY else SELL,
            str(getattr(fill, "executed_at", "") or ""),
            entry_id="ledger-fill:" + hashlib.sha256(provider_execution_id.encode("utf-8")).hexdigest()[:24],
            source_reference="provider-fill:" + provider_execution_id,
            symbol=symbol,
            currency=str(getattr(fill, "currency", "KRW") or "KRW"),
            quantity=quantity,
            unit_price=getattr(fill, "price", 0),
            amount=quantity * decimal_value(getattr(fill, "price", 0)),
            fee=getattr(fill, "fee", 0),
            payload={
                "source": "provider-execution-fill",
                "providerExecutionId": provider_execution_id,
                "executionEpisodeId": str(getattr(episode, "execution_episode_id", "") or ""),
                "actionPlanId": str(getattr(plan, "plan_id", "") or ""),
                "supersedesEntryIds": list(superseded_by_provider_id.get(provider_execution_id) or []),
                "actualActivity": True,
            },
        ))
    return result


@dataclass(frozen=True)
class ReconciliationDifference:
    difference_type: str
    key: str
    expected: Decimal
    observed: Decimal
    tolerance: Decimal = Decimal("0")
    currency: str = ""
    reason: str = ""

    @property
    def delta(self) -> Decimal:
        return self.observed - self.expected

    @property
    def matched(self) -> bool:
        return abs(self.delta) <= self.tolerance

    def to_dict(self) -> Dict[str, object]:
        return {
            "differenceType": self.difference_type,
            "key": self.key,
            "expected": str(self.expected),
            "observed": str(self.observed),
            "delta": str(self.delta),
            "tolerance": str(self.tolerance),
            "currency": self.currency,
            "reason": self.reason,
            "matched": self.matched,
        }


@dataclass(frozen=True)
class PortfolioReconciliation:
    reconciliation_id: str
    portfolio_id: str
    account_id: str
    source_snapshot_at: str
    balance_fingerprint: str
    differences: List[ReconciliationDifference] = field(default_factory=list)
    status: str = "matched"
    source: str = "broker-snapshot"
    created_at: str = ""

    @classmethod
    def create(
        cls,
        portfolio_id: str,
        account_id: str,
        source_snapshot_at: str,
        differences: Iterable[ReconciliationDifference],
        balance_values: Dict[str, object],
        created_at: str = "",
    ):
        canonical = json.dumps(balance_values or {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        rows = list(differences or [])
        unmatched = [item for item in rows if not item.matched]
        return cls(
            reconciliation_id="portfolio-reconciliation:" + hashlib.sha256(
                (str(portfolio_id) + "|" + fingerprint).encode("utf-8")
            ).hexdigest()[:24],
            portfolio_id=str(portfolio_id or ""),
            account_id=str(account_id or ""),
            source_snapshot_at=str(source_snapshot_at or ""),
            balance_fingerprint=fingerprint,
            differences=rows,
            status="discrepancy" if unmatched else "matched",
            created_at=str(created_at or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": PORTFOLIO_LEDGER_VERSION,
            "reconciliationId": self.reconciliation_id,
            "portfolioId": self.portfolio_id,
            "accountId": self.account_id,
            "sourceSnapshotAt": self.source_snapshot_at,
            "balanceFingerprint": self.balance_fingerprint,
            "status": self.status,
            "source": self.source,
            "differenceCount": len([item for item in self.differences if not item.matched]),
            "differences": [item.to_dict() for item in self.differences],
            "createdAt": self.created_at,
        }
