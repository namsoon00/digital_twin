"""Infer bounded portfolio ledger facts from complete account snapshots."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Dict, Iterable, List, Tuple

from .portfolio_ledger import (
    INFERRED_CORPORATE_ACTION,
    INFERRED_POSITION_DECREASE,
    INFERRED_POSITION_EXIT,
    INFERRED_POSITION_INCREASE,
    SNAPSHOT_CASH_ADJUSTMENT,
    PortfolioLedgerEntry,
    PortfolioLedgerState,
    decimal_value,
)


SNAPSHOT_ACTIVITY_VERSION = "snapshot-portfolio-activity-v1"
QUANTITY_TOLERANCE = Decimal("0.000001")
CASH_TOLERANCE = Decimal("1")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(prefix: str, *parts: object) -> str:
    value = "|".join(str(item or "") for item in parts)
    return prefix + ":" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def trusted_account_snapshot(snapshot) -> Tuple[bool, Dict[str, object]]:
    """Accept only a provider-declared complete live balance snapshot.

    The exact Toss success status remains a compatibility fallback while all
    newly collected snapshots carry explicit holdings/cash completeness.
    """
    if not snapshot or not snapshot.has_live_account_data():
        return False, {"trusted": False, "reason": "snapshot-is-not-live"}
    metadata = dict(getattr(snapshot, "metadata", {}) or {})
    coverage = metadata.get("accountSnapshotCompleteness")
    if isinstance(coverage, dict):
        holdings = str(coverage.get("holdings") or "").lower()
        cash = str(coverage.get("cash") or "").lower()
        trusted = holdings == "complete" and cash == "complete"
        return trusted, {
            "trusted": trusted,
            "reason": "provider-declared-complete" if trusted else "provider-declared-incomplete",
            "holdings": holdings or "unknown",
            "cash": cash or "unknown",
            "source": str(coverage.get("source") or getattr(snapshot, "provider", "")),
        }
    trusted = str(getattr(snapshot, "status", "") or "").strip() == "토스 계좌 동기화"
    return trusted, {
        "trusted": trusted,
        "reason": "compatible-toss-success-status" if trusted else "snapshot-completeness-not-declared",
        "holdings": "complete" if trusted else "unknown",
        "cash": "complete" if trusted else "unknown",
        "source": str(getattr(snapshot, "provider", "") or ""),
    }


def observed_positions(snapshot) -> Dict[str, Dict[str, object]]:
    rows: Dict[str, Dict[str, object]] = {}
    for position in getattr(snapshot, "positions", []) or []:
        if position.is_cash():
            continue
        symbol = str(position.symbol or "").upper().strip()
        quantity = decimal_value(position.quantity)
        if not symbol or quantity <= 0:
            continue
        rows[symbol] = {
            "quantity": quantity,
            "averagePrice": decimal_value(position.average_price),
            "currency": str(position.currency or "KRW").upper(),
            "name": str(position.name or symbol),
        }
    return rows


def snapshot_balance_fingerprint(snapshot) -> str:
    positions = observed_positions(snapshot)
    canonical = {
        "accountId": str(getattr(snapshot, "account_id", "") or ""),
        "provider": str(getattr(snapshot, "provider", "") or "").lower(),
        "positions": {
            symbol: {
                "quantity": str(row["quantity"]),
                "averagePrice": str(row["averagePrice"]),
                "currency": row["currency"],
            }
            for symbol, row in sorted(positions.items())
        },
        "cash": str(decimal_value(getattr(getattr(snapshot, "portfolio", None), "cash", 0))),
    }
    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def previous_snapshot_at(entries: Iterable[PortfolioLedgerEntry]) -> str:
    candidates = []
    for entry in entries or []:
        payload = dict(entry.payload or {})
        candidates.extend([
            str(payload.get("currentSnapshotAt") or ""),
            str(payload.get("snapshotGeneratedAt") or ""),
            str(payload.get("sourceSnapshotAt") or ""),
        ])
    return max((item for item in candidates if item), default="")


def possible_split(old_quantity: Decimal, new_quantity: Decimal, old_average: Decimal, new_average: Decimal) -> bool:
    if old_quantity <= 0 or new_quantity <= 0 or old_average <= 0 or new_average <= 0:
        return False
    quantity_ratio = new_quantity / old_quantity
    price_ratio = old_average / new_average
    for ratio in (Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5"), Decimal("10")):
        if abs(quantity_ratio - ratio) / ratio <= Decimal("0.02") and abs(price_ratio - ratio) / ratio <= Decimal("0.05"):
            return True
        inverse = Decimal("1") / ratio
        if abs(quantity_ratio - inverse) / inverse <= Decimal("0.02") and abs(price_ratio - inverse) / inverse <= Decimal("0.05"):
            return True
    return False


def inferred_unit_cost(
    old_quantity: Decimal,
    old_average: Decimal,
    new_quantity: Decimal,
    new_average: Decimal,
) -> Tuple[Decimal, str]:
    delta = new_quantity - old_quantity
    if delta <= 0:
        return Decimal("0"), "not-applicable"
    implied = ((new_quantity * new_average) - (old_quantity * old_average)) / delta
    if implied > 0:
        return implied, "implied-from-provider-average-cost"
    return max(Decimal("0"), new_average), "provider-average-cost-fallback"


def infer_snapshot_ledger_entries(
    snapshot,
    portfolio_id: str,
    ledger_state: PortfolioLedgerState,
    prior_entries: Iterable[PortfolioLedgerEntry],
) -> List[PortfolioLedgerEntry]:
    """Return idempotent correction facts without claiming unknown trades."""
    stamp = str(getattr(snapshot, "generated_at", "") or utc_now_iso())
    fingerprint = snapshot_balance_fingerprint(snapshot)
    observation_id = stable_id(
        "account-balance-observation",
        str(getattr(snapshot, "account_id", "") or ""),
        stamp,
        fingerprint,
    )
    previous_at = previous_snapshot_at(prior_entries)
    observed = observed_positions(snapshot)
    ledger_symbols = {
        str(item.symbol or "").upper().strip()
        for item in ledger_state.lots
        if item.remaining_quantity > 0
    }
    entries: List[PortfolioLedgerEntry] = []
    for symbol in sorted(set(observed) | ledger_symbols):
        row = observed.get(symbol) or {
            "quantity": Decimal("0"),
            "averagePrice": Decimal("0"),
            "currency": next((item.currency for item in ledger_state.lots if item.symbol == symbol), "KRW"),
            "name": symbol,
        }
        old_quantity = ledger_state.quantity(symbol)
        new_quantity = decimal_value(row["quantity"])
        delta = new_quantity - old_quantity
        if abs(delta) <= QUANTITY_TOLERANCE:
            continue
        old_average = ledger_state.average_cost(symbol)
        new_average = decimal_value(row["averagePrice"])
        split_candidate = possible_split(old_quantity, new_quantity, old_average, new_average)
        if delta > 0:
            entry_type = INFERRED_POSITION_INCREASE
            classification = "new-position" if old_quantity <= 0 else "position-increase"
            quantity = delta
            unit_cost, cost_basis_source = inferred_unit_cost(old_quantity, old_average, new_quantity, new_average)
        else:
            entry_type = INFERRED_POSITION_EXIT if new_quantity <= QUANTITY_TOLERANCE else INFERRED_POSITION_DECREASE
            classification = "position-exit" if entry_type == INFERRED_POSITION_EXIT else "position-decrease"
            quantity = abs(delta)
            unit_cost = Decimal("0")
            cost_basis_source = "not-applicable"
        if split_candidate:
            entry_type = INFERRED_CORPORATE_ACTION
            classification = "possible-corporate-action"
            quantity = Decimal("0")
            unit_cost = Decimal("0")
            cost_basis_source = "inferred-split-ratio"
        confidence = "low" if split_candidate or cost_basis_source.endswith("fallback") else "medium"
        source_reference = observation_id + ":position:" + symbol
        payload = {
            "version": SNAPSHOT_ACTIVITY_VERSION,
            "source": "complete-account-snapshot-difference",
            "inferenceType": classification,
            "confidence": confidence,
            "previousQuantity": str(old_quantity),
            "observedQuantity": str(new_quantity),
            "quantityDelta": str(delta),
            "previousAverageCost": str(old_average),
            "providerAveragePrice": str(new_average),
            "unitCostBasis": cost_basis_source,
            "ratio": str(new_quantity / old_quantity) if split_candidate and old_quantity else "",
            "previousSnapshotAt": previous_at,
            "currentSnapshotAt": stamp,
            "observationFingerprint": fingerprint,
            "observationId": observation_id,
            "provider": str(getattr(snapshot, "provider", "") or ""),
            "instrumentName": str(row["name"] or symbol),
            "replaceableByActualActivity": True,
            "realizedProfitLossKnown": False,
            "feesKnown": False,
            "taxesKnown": False,
        }
        entries.append(PortfolioLedgerEntry.create(
            portfolio_id,
            str(getattr(snapshot, "account_id", "") or ""),
            entry_type,
            stamp,
            entry_id=stable_id("snapshot-ledger-entry", portfolio_id, source_reference),
            source_reference=source_reference,
            symbol=symbol,
            currency=str(row["currency"] or "KRW"),
            quantity=quantity,
            unit_price=unit_cost,
            payload=payload,
        ))

    observed_cash = decimal_value(getattr(getattr(snapshot, "portfolio", None), "cash", 0))
    ledger_cash = ledger_state.cash.get("KRW", Decimal("0"))
    cash_delta = observed_cash - ledger_cash
    if abs(cash_delta) > CASH_TOLERANCE:
        source_reference = observation_id + ":cash:KRW"
        entries.append(PortfolioLedgerEntry.create(
            portfolio_id,
            str(getattr(snapshot, "account_id", "") or ""),
            SNAPSHOT_CASH_ADJUSTMENT,
            stamp,
            entry_id=stable_id("snapshot-ledger-entry", portfolio_id, source_reference),
            source_reference=source_reference,
            currency="KRW",
            amount=cash_delta,
            payload={
                "version": SNAPSHOT_ACTIVITY_VERSION,
                "source": "complete-account-snapshot-difference",
                "inferenceType": "unclassified-cash-balance-change",
                "confidence": "medium",
                "previousCash": str(ledger_cash),
                "observedCash": str(observed_cash),
                "cashDelta": str(cash_delta),
                "previousSnapshotAt": previous_at,
                "currentSnapshotAt": stamp,
                "observationFingerprint": fingerprint,
                "observationId": observation_id,
                "provider": str(getattr(snapshot, "provider", "") or ""),
                "replaceableByActualActivity": True,
                "depositOrWithdrawalKnown": False,
                "feesKnown": False,
                "taxesKnown": False,
            },
        ))
    return entries


def activity_payload(entry: PortfolioLedgerEntry) -> Dict[str, object]:
    payload = dict(entry.payload or {})
    return {
        "entryId": entry.entry_id,
        "entryType": entry.entry_type,
        "occurredAt": entry.occurred_at,
        "sourceReference": entry.source_reference,
        "symbol": entry.symbol,
        "currency": entry.currency,
        "quantity": str(entry.quantity),
        "unitPrice": str(entry.unit_price),
        "amount": str(entry.amount),
        "classification": str(payload.get("inferenceType") or ""),
        "confidence": str(payload.get("confidence") or ""),
        "previousQuantity": str(payload.get("previousQuantity") or ""),
        "observedQuantity": str(payload.get("observedQuantity") or ""),
        "quantityDelta": str(payload.get("quantityDelta") or ""),
        "cashDelta": str(payload.get("cashDelta") or ""),
        "previousSnapshotAt": str(payload.get("previousSnapshotAt") or ""),
        "currentSnapshotAt": str(payload.get("currentSnapshotAt") or ""),
        "provider": str(payload.get("provider") or ""),
        "instrumentName": str(payload.get("instrumentName") or entry.symbol),
        "realizedProfitLossKnown": bool(payload.get("realizedProfitLossKnown", False)),
        "replaceableByActualActivity": bool(payload.get("replaceableByActualActivity", False)),
    }
