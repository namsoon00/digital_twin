"""Versioned account-valuation contracts shared by portfolio consumers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List


POSITION_VALUATION_VERSION = "broker-position-valuation-v2"
PORTFOLIO_VALUATION_VERSION = "portfolio-valuation-snapshot-v2"
BROKER_NET_BASIS = "broker-net"
BROKER_GROSS_BASIS = "broker-gross"
MARK_TO_MARKET_BASIS = "mark-to-market"
VALUATION_BASES = {BROKER_NET_BASIS, BROKER_GROSS_BASIS, MARK_TO_MARKET_BASIS}


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalized_valuation_basis(value: object, fallback: str = MARK_TO_MARKET_BASIS) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALUATION_BASES else fallback


@dataclass(frozen=True)
class PositionValuation:
    symbol: str
    currency: str
    quantity: float
    broker_price: float
    broker_gross_native: float
    broker_net_native: float
    broker_purchase_native: float
    broker_profit_loss_native: float
    broker_profit_loss_net_native: float
    broker_gross_base: float
    broker_net_base: float
    mark_to_market_native: float
    mark_to_market_base: float
    account_value_base: float
    account_value_basis: str
    fx_rate: float
    fx_source: str
    fx_state: str
    fx_as_of: str
    holdings_as_of: str
    price_as_of: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": POSITION_VALUATION_VERSION,
            "symbol": self.symbol,
            "currency": self.currency,
            "quantity": self.quantity,
            "brokerPrice": self.broker_price,
            "brokerGrossNative": self.broker_gross_native,
            "brokerNetNative": self.broker_net_native,
            "brokerPurchaseNative": self.broker_purchase_native,
            "brokerProfitLossNative": self.broker_profit_loss_native,
            "brokerProfitLossNetNative": self.broker_profit_loss_net_native,
            "brokerGrossBase": self.broker_gross_base,
            "brokerNetBase": self.broker_net_base,
            "markToMarketNative": self.mark_to_market_native,
            "markToMarketBase": self.mark_to_market_base,
            "accountValueBase": self.account_value_base,
            "accountValueBasis": self.account_value_basis,
            "fxRate": self.fx_rate,
            "fxSource": self.fx_source,
            "fxState": self.fx_state,
            "fxAsOf": self.fx_as_of,
            "holdingsAsOf": self.holdings_as_of,
            "priceAsOf": self.price_as_of,
        }


@dataclass(frozen=True)
class PortfolioValuationSnapshot:
    valuation_snapshot_id: str
    account_id: str
    observed_at: str
    display_basis: str
    base_currency: str
    broker_comparable_total: float
    broker_gross_total: float
    broker_net_total: float
    mark_to_market_total: float
    invested_total: float
    cash_total: float
    account_equity_total: float
    position_count: int
    fx_context: Dict[str, object] = field(default_factory=dict)
    component_as_of: Dict[str, str] = field(default_factory=dict)
    reconciliation: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": PORTFOLIO_VALUATION_VERSION,
            "valuationSnapshotId": self.valuation_snapshot_id,
            "accountId": self.account_id,
            "observedAt": self.observed_at,
            "displayBasis": self.display_basis,
            "baseCurrency": self.base_currency,
            "brokerComparableTotal": self.broker_comparable_total,
            "brokerGrossTotal": self.broker_gross_total,
            "brokerNetTotal": self.broker_net_total,
            "markToMarketTotal": self.mark_to_market_total,
            "investedTotal": self.invested_total,
            "cashTotal": self.cash_total,
            "accountEquityTotal": self.account_equity_total,
            "positionCount": self.position_count,
            "fxContext": self.fx_context,
            "componentAsOf": self.component_as_of,
            "reconciliation": self.reconciliation,
        }


def stable_valuation_snapshot_id(
    account_id: object,
    observed_at: object,
    display_basis: object,
    positions: Iterable[Dict[str, object]],
    cash_total: object,
) -> str:
    rows: List[Dict[str, object]] = []
    for item in positions or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "symbol": str(item.get("symbol") or "").upper().strip(),
            "quantity": _number(item.get("quantity")),
            "accountValueBase": round(_number(item.get("accountValueBase")), 6),
            "markToMarketBase": round(_number(item.get("markToMarketBase")), 6),
            "brokerNetBase": round(_number(item.get("brokerNetBase")), 6),
        })
    rows.sort(key=lambda item: item["symbol"])
    payload = {
        "accountId": str(account_id or ""),
        "observedAt": str(observed_at or ""),
        "displayBasis": normalized_valuation_basis(display_basis),
        "positions": rows,
        "cashTotal": round(_number(cash_total), 6),
        "version": PORTFOLIO_VALUATION_VERSION,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return "portfolio-valuation:" + digest
