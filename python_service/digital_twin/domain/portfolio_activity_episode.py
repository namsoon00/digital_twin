"""Portfolio activity episodes and derived account state from live balances.

These objects record observable account arithmetic. They never assert that a
broker balance change was an executed order and never select an investment
action; TypeDB and the final AI judge own that meaning.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from typing import Dict, Iterable, List, Optional, Tuple

from .portfolio_ledger import (
    INFERRED_CORPORATE_ACTION,
    INFERRED_POSITION_DECREASE,
    INFERRED_POSITION_EXIT,
    INFERRED_POSITION_INCREASE,
    SNAPSHOT_CASH_ADJUSTMENT,
    PortfolioLedgerEntry,
    decimal_value,
)
from .snapshot_portfolio_activity import (
    activity_payload,
    cash_balance_components,
    observed_positions,
    snapshot_balance_fingerprint,
)


PORTFOLIO_ACTIVITY_EPISODE_VERSION = "portfolio-activity-episode-v2-native-cash-components"
PORTFOLIO_STATE_VERSION = "portfolio-state-snapshot-v1"
SNAPSHOT_CHECKPOINT_VERSION = "portfolio-snapshot-checkpoint-v2-native-cash-components"
DECISION_ACTION_OBSERVATION_VERSION = "decision-action-observation-v1"


def stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(item or "") for item in parts)
    return prefix + ":" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def parse_time(value: object) -> Optional[datetime]:
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


def account_source_fingerprint(snapshot) -> str:
    metadata = getattr(snapshot, "metadata", {})
    provider_fingerprint = str(
        metadata.get("accountSourceFingerprint")
        if isinstance(metadata, dict)
        else ""
    ).strip()
    if provider_fingerprint:
        return provider_fingerprint
    return stable_id(
        "account-source",
        str(getattr(snapshot, "provider", "") or "").strip().lower(),
        str(getattr(snapshot, "account_id", "") or "").strip(),
    )


@dataclass(frozen=True)
class PortfolioSnapshotCheckpoint:
    portfolio_id: str
    account_id: str
    account_fingerprint: str
    observed_at: str
    balance_fingerprint: str
    position_count: int
    total_quantity: Decimal
    cash_balance: Decimal
    portfolio_total: Decimal
    cash_balance_components: Dict[str, Decimal] = field(default_factory=dict)
    valuation_snapshot_id: str = ""
    valuation_basis: str = ""
    broker_comparable_total: Decimal = Decimal("0")
    broker_gross_total: Decimal = Decimal("0")
    broker_net_total: Decimal = Decimal("0")
    mark_to_market_total: Decimal = Decimal("0")
    version: int = 0
    status: str = "accepted"
    quarantine_reason: str = ""

    @classmethod
    def from_snapshot(cls, snapshot, portfolio_id: str, version: int = 0):
        positions = observed_positions(snapshot)
        return cls(
            portfolio_id=str(portfolio_id or ""),
            account_id=str(getattr(snapshot, "account_id", "") or ""),
            account_fingerprint=account_source_fingerprint(snapshot),
            observed_at=str(getattr(snapshot, "generated_at", "") or ""),
            balance_fingerprint=snapshot_balance_fingerprint(snapshot),
            position_count=len(positions),
            total_quantity=sum((decimal_value(item.get("quantity")) for item in positions.values()), Decimal("0")),
            cash_balance=decimal_value(getattr(getattr(snapshot, "portfolio", None), "cash", 0)),
            portfolio_total=decimal_value(getattr(getattr(snapshot, "portfolio", None), "total", 0)),
            cash_balance_components=cash_balance_components(snapshot),
            valuation_snapshot_id=str(getattr(getattr(snapshot, "portfolio", None), "valuation_snapshot_id", "") or ""),
            valuation_basis=str(getattr(getattr(snapshot, "portfolio", None), "valuation_basis", "") or ""),
            broker_comparable_total=decimal_value(getattr(getattr(snapshot, "portfolio", None), "broker_comparable_total", 0)),
            broker_gross_total=decimal_value(getattr(getattr(snapshot, "portfolio", None), "broker_gross_total", 0)),
            broker_net_total=decimal_value(getattr(getattr(snapshot, "portfolio", None), "broker_net_total", 0)),
            mark_to_market_total=decimal_value(getattr(getattr(snapshot, "portfolio", None), "mark_to_market_total", 0)),
            version=max(0, int(version or 0)),
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        row = dict(payload or {})
        return cls(
            portfolio_id=str(row.get("portfolioId") or row.get("portfolio_id") or ""),
            account_id=str(row.get("accountId") or row.get("account_id") or ""),
            account_fingerprint=str(row.get("accountFingerprint") or row.get("account_fingerprint") or ""),
            observed_at=str(row.get("observedAt") or row.get("observed_at") or ""),
            balance_fingerprint=str(row.get("balanceFingerprint") or row.get("balance_fingerprint") or ""),
            position_count=int(row.get("positionCount") or row.get("position_count") or 0),
            total_quantity=decimal_value(row.get("totalQuantity") or row.get("total_quantity")),
            cash_balance=decimal_value(row.get("cashBalance") or row.get("cash_balance")),
            portfolio_total=decimal_value(row.get("portfolioTotal") or row.get("portfolio_total")),
            cash_balance_components={
                str(currency or "").upper(): decimal_value(amount)
                for currency, amount in dict(
                    row.get("cashBalanceComponents") or row.get("cash_balance_components") or {}
                ).items()
                if str(currency or "").strip()
            },
            valuation_snapshot_id=str(row.get("valuationSnapshotId") or row.get("valuation_snapshot_id") or ""),
            valuation_basis=str(row.get("valuationBasis") or row.get("valuation_basis") or ""),
            broker_comparable_total=decimal_value(row.get("brokerComparableTotal") or row.get("broker_comparable_total")),
            broker_gross_total=decimal_value(row.get("brokerGrossTotal") or row.get("broker_gross_total")),
            broker_net_total=decimal_value(row.get("brokerNetTotal") or row.get("broker_net_total")),
            mark_to_market_total=decimal_value(row.get("markToMarketTotal") or row.get("mark_to_market_total")),
            version=int(row.get("checkpointVersion") or row.get("version") or 0),
            status=str(row.get("status") or "accepted"),
            quarantine_reason=str(row.get("quarantineReason") or row.get("quarantine_reason") or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "schemaVersion": SNAPSHOT_CHECKPOINT_VERSION,
            "portfolioId": self.portfolio_id,
            "accountId": self.account_id,
            "accountFingerprint": self.account_fingerprint,
            "observedAt": self.observed_at,
            "balanceFingerprint": self.balance_fingerprint,
            "positionCount": self.position_count,
            "totalQuantity": str(self.total_quantity),
            "cashBalance": str(self.cash_balance),
            "cashBalanceComponents": {
                currency: str(amount)
                for currency, amount in sorted(self.cash_balance_components.items())
            },
            "portfolioTotal": str(self.portfolio_total),
            "valuationSnapshotId": self.valuation_snapshot_id,
            "valuationBasis": self.valuation_basis,
            "brokerComparableTotal": str(self.broker_comparable_total),
            "brokerGrossTotal": str(self.broker_gross_total),
            "brokerNetTotal": str(self.broker_net_total),
            "markToMarketTotal": str(self.mark_to_market_total),
            "checkpointVersion": self.version,
            "status": self.status,
            "quarantineReason": self.quarantine_reason,
        }


def checkpoint_acceptance(
    previous: Optional[PortfolioSnapshotCheckpoint],
    current: PortfolioSnapshotCheckpoint,
) -> Tuple[str, str]:
    if not previous:
        return "accepted", "initial-checkpoint"
    if previous.account_fingerprint and previous.account_fingerprint != current.account_fingerprint:
        return "quarantined", "account-source-fingerprint-changed"
    previous_at = parse_time(previous.observed_at)
    current_at = parse_time(current.observed_at)
    if previous_at and current_at and current_at < previous_at:
        return "stale", "snapshot-older-than-checkpoint"
    if previous_at and current_at and current_at == previous_at:
        if previous.balance_fingerprint == current.balance_fingerprint:
            return "duplicate", "snapshot-already-checkpointed"
        return "quarantined", "same-timestamp-different-balance"
    if previous.balance_fingerprint == current.balance_fingerprint:
        if (
            previous.cash_balance_components
            and previous.cash_balance_components == current.cash_balance_components
            and previous.cash_balance != current.cash_balance
        ):
            return "unchanged", "cash-valuation-only-change"
        return "unchanged", "balance-fingerprint-unchanged"
    if (
        previous.position_count > 0
        and current.position_count == 0
        and current.cash_balance <= previous.cash_balance + Decimal("1")
    ):
        return "quarantined", "all-positions-disappeared-without-cash-offset"
    if (
        previous.portfolio_total > 0
        and current.portfolio_total < previous.portfolio_total * Decimal("0.1")
        and current.cash_balance <= previous.cash_balance + Decimal("1")
    ):
        return "quarantined", "portfolio-total-collapsed-without-cash-offset"
    return "accepted", "new-complete-balance"


@dataclass(frozen=True)
class PortfolioActivityEpisode:
    episode_id: str
    portfolio_id: str
    account_id: str
    observed_at: str
    previous_observed_at: str
    observation_fingerprint: str
    classification: str
    confidence: str
    ledger_entry_ids: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    cash_delta: Decimal = Decimal("0")
    estimated_notional: Decimal = Decimal("0")
    instrument_changes: List[Dict[str, object]] = field(default_factory=list)
    replaceable_by_actual_activity: bool = True

    @classmethod
    def from_entries(
        cls,
        entries: Iterable[PortfolioLedgerEntry],
        checkpoint: PortfolioSnapshotCheckpoint,
        previous: Optional[PortfolioSnapshotCheckpoint],
    ):
        rows = list(entries or [])
        if not rows:
            return None
        position_rows = [item for item in rows if item.symbol]
        cash_row = next((item for item in rows if item.entry_type == SNAPSHOT_CASH_ADJUSTMENT), None)
        types = {item.entry_type for item in position_rows}
        cash_delta = (
            decimal_value((cash_row.payload or {}).get("cashDelta"))
            if cash_row
            else Decimal("0")
        )
        estimated_notional = sum(
            (
                item.quantity
                * (
                    item.unit_price
                    or decimal_value((item.payload or {}).get("previousAverageCost"))
                    or decimal_value((item.payload or {}).get("providerAveragePrice"))
                )
                for item in position_rows
                if item.entry_type != INFERRED_CORPORATE_ACTION
            ),
            Decimal("0"),
        )
        if INFERRED_CORPORATE_ACTION in types:
            classification = "possible-corporate-action"
        elif types and types <= {INFERRED_POSITION_INCREASE} and cash_delta < 0:
            classification = "probable-buy"
        elif types and types <= {INFERRED_POSITION_DECREASE, INFERRED_POSITION_EXIT} and cash_delta > 0:
            classification = "probable-sell"
        elif not position_rows and cash_row:
            classification = "cash-balance-change"
        elif len(types) == 1 and position_rows:
            classification = "position-balance-change"
        else:
            classification = "mixed-portfolio-change"
        correspondence = Decimal("0")
        if estimated_notional > 0 and cash_delta:
            absolute_cash_delta = abs(cash_delta)
            correspondence = min(absolute_cash_delta, estimated_notional) / max(
                absolute_cash_delta,
                estimated_notional,
            )
        confidence = "medium" if classification in {"probable-buy", "probable-sell"} and correspondence >= Decimal("0.5") else "low"
        if classification == "cash-balance-change":
            confidence = "medium"
        activities = [activity_payload(item) for item in position_rows]
        return cls(
            episode_id=stable_id("portfolio-activity-episode", checkpoint.portfolio_id, checkpoint.observed_at, checkpoint.balance_fingerprint),
            portfolio_id=checkpoint.portfolio_id,
            account_id=checkpoint.account_id,
            observed_at=checkpoint.observed_at,
            previous_observed_at=previous.observed_at if previous else "",
            observation_fingerprint=checkpoint.balance_fingerprint,
            classification=classification,
            confidence=confidence,
            ledger_entry_ids=[item.entry_id for item in rows],
            symbols=sorted({item.symbol for item in position_rows if item.symbol}),
            cash_delta=cash_delta,
            estimated_notional=estimated_notional,
            instrument_changes=activities,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "schemaVersion": PORTFOLIO_ACTIVITY_EPISODE_VERSION,
            "episodeId": self.episode_id,
            "portfolioId": self.portfolio_id,
            "accountId": self.account_id,
            "observedAt": self.observed_at,
            "previousObservedAt": self.previous_observed_at,
            "observationFingerprint": self.observation_fingerprint,
            "classification": self.classification,
            "confidence": self.confidence,
            "ledgerEntryIds": list(self.ledger_entry_ids),
            "symbols": list(self.symbols),
            "cashDelta": str(self.cash_delta),
            "estimatedNotional": str(self.estimated_notional),
            "instrumentChanges": list(self.instrument_changes),
            "replaceableByActualActivity": self.replaceable_by_actual_activity,
            "executable": False,
        }


def recent_entry(entry: PortfolioLedgerEntry, observed_at: str, days: int) -> bool:
    end = parse_time(observed_at)
    occurred = parse_time(entry.occurred_at)
    return bool(end and occurred and 0 <= (end - occurred).total_seconds() <= days * 86400)


@dataclass(frozen=True)
class PortfolioStateSnapshot:
    state_id: str
    portfolio_id: str
    account_id: str
    observed_at: str
    source_checkpoint_version: int
    cash_weight_pct: float
    position_count: int
    positions: List[Dict[str, object]] = field(default_factory=list)

    @classmethod
    def from_snapshot(
        cls,
        snapshot,
        portfolio_id: str,
        checkpoint_version: int,
        ledger_entries: Iterable[PortfolioLedgerEntry],
    ):
        entries = list(ledger_entries or [])
        portfolio = getattr(snapshot, "portfolio", None)
        total = max(0.0, float(getattr(portfolio, "total", 0) or 0)) or 1.0
        valuation_snapshot_id = str(getattr(portfolio, "valuation_snapshot_id", "") or "")
        valuation_basis = str(getattr(portfolio, "valuation_basis", "") or "")
        observed_at = str(getattr(snapshot, "generated_at", "") or "")
        states: List[Dict[str, object]] = []
        for position in getattr(snapshot, "positions", []) or []:
            if position.is_cash() or decimal_value(position.quantity) <= 0:
                continue
            symbol = str(position.symbol or "").upper().strip()
            symbol_entries = [item for item in entries if item.symbol == symbol]
            increases = [item for item in symbol_entries if item.entry_type == INFERRED_POSITION_INCREASE]
            decreases = [item for item in symbol_entries if item.entry_type in {INFERRED_POSITION_DECREASE, INFERRED_POSITION_EXIT}]
            exits = [item for item in symbol_entries if item.entry_type == INFERRED_POSITION_EXIT]
            latest_exit = max((item.occurred_at for item in exits), default="")
            openings = [item for item in symbol_entries if item.entry_type in {"OPENING_POSITION", INFERRED_POSITION_INCREASE}]
            active_openings = [item for item in openings if not latest_exit or item.occurred_at > latest_exit]
            opened_at = min((item.occurred_at for item in active_openings), default=observed_at)
            opened_time = parse_time(opened_at)
            current_time = parse_time(observed_at)
            holding_days = max(0, int((current_time - opened_time).total_seconds() // 86400)) if current_time and opened_time else 0
            account_value = float(
                getattr(position, "account_value_krw", 0)
                or getattr(position, "market_value_krw", 0)
                or 0
            )
            states.append({
                "symbol": symbol,
                "name": str(position.name or symbol),
                "market": str(position.market or ""),
                "currency": str(position.currency or "KRW"),
                "quantity": str(decimal_value(position.quantity)),
                "averagePrice": float(position.average_price or 0),
                "currentPrice": float(position.current_price or 0),
                "marketValueKrw": account_value,
                "accountValueKrw": account_value,
                "accountValueBasis": str(getattr(position, "account_value_basis", "") or valuation_basis),
                "brokerGrossValueKrw": float(getattr(position, "broker_market_value_krw", 0) or 0),
                "brokerNetValueKrw": float(getattr(position, "broker_market_value_after_cost_krw", 0) or 0),
                "markToMarketValueKrw": float(getattr(position, "mark_to_market_value_krw", 0) or 0),
                "valuationSnapshotId": str(getattr(position, "valuation_snapshot_id", "") or valuation_snapshot_id),
                "valuationFxSource": str(getattr(position, "valuation_fx_source", "") or ""),
                "valuationFxState": str(getattr(position, "valuation_fx_state", "") or ""),
                "valuationFxAsOf": str(getattr(position, "valuation_fx_as_of", "") or ""),
                "currentWeightPct": account_value / total * 100,
                "profitLossRate": float(getattr(position, "profit_loss_rate", 0) or 0),
                "openedAt": opened_at,
                "holdingDays": holding_days,
                "lastIncreaseAt": max((item.occurred_at for item in increases), default=""),
                "lastDecreaseAt": max((item.occurred_at for item in decreases), default=""),
                "lastExitAt": latest_exit,
                "increaseCount5d": len([item for item in increases if recent_entry(item, observed_at, 5)]),
                "increaseCount20d": len([item for item in increases if recent_entry(item, observed_at, 20)]),
                "decreaseCount5d": len([item for item in decreases if recent_entry(item, observed_at, 5)]),
                "decreaseCount20d": len([item for item in decreases if recent_entry(item, observed_at, 20)]),
                "reentered": bool(latest_exit and any(item.occurred_at > latest_exit for item in increases)),
                "source": "complete-account-snapshot-and-ledger",
            })
        cash = max(0.0, float(getattr(getattr(snapshot, "portfolio", None), "cash", 0) or 0))
        fingerprint = snapshot_balance_fingerprint(snapshot)
        return cls(
            state_id=stable_id("portfolio-state", portfolio_id, observed_at, fingerprint),
            portfolio_id=portfolio_id,
            account_id=str(getattr(snapshot, "account_id", "") or ""),
            observed_at=observed_at,
            source_checkpoint_version=checkpoint_version,
            cash_weight_pct=cash / total * 100,
            position_count=len(states),
            positions=states,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "schemaVersion": PORTFOLIO_STATE_VERSION,
            "stateId": self.state_id,
            "portfolioId": self.portfolio_id,
            "accountId": self.account_id,
            "observedAt": self.observed_at,
            "sourceCheckpointVersion": self.source_checkpoint_version,
            "cashWeightPct": self.cash_weight_pct,
            "positionCount": self.position_count,
            "positions": list(self.positions),
        }


@dataclass(frozen=True)
class DecisionActionObservation:
    observation_id: str
    portfolio_id: str
    account_id: str
    symbol: str
    observed_at: str
    activity_episode_id: str
    prior_decision_episode_id: str
    prior_action: str
    observed_direction: str
    correspondence: str
    elapsed_minutes: int

    @classmethod
    def from_activity(
        cls,
        episode: PortfolioActivityEpisode,
        prior_decision: Dict[str, object],
        symbol: str = "",
    ):
        decision = dict(prior_decision or {})
        selected_symbol = str(symbol or (episode.symbols[0] if len(episode.symbols) == 1 else "")).upper().strip()
        if not selected_symbol or not decision:
            return None
        activity = next(
            (
                item
                for item in episode.instrument_changes
                if str(item.get("symbol") or "").upper().strip() == selected_symbol
            ),
            {},
        )
        activity_classification = str(activity.get("classification") or "")
        direction = (
            "increase"
            if activity_classification in {"new-position", "position-increase"}
            else "decrease"
            if activity_classification in {"position-decrease", "position-exit"}
            else "other"
        )
        action = str(decision.get("action") or "").upper()
        aligned = (direction == "increase" and action in {"BUY", "ADD"}) or (direction == "decrease" and action in {"SELL", "TRIM"})
        neutral = action in {"", "HOLD", "AVOID"} or direction == "other"
        prior_at = parse_time(decision.get("decidedAt") or decision.get("decided_at"))
        observed_at = parse_time(episode.observed_at)
        elapsed = max(0, int((observed_at - prior_at).total_seconds() // 60)) if observed_at and prior_at else 0
        prior_id = str(decision.get("episodeId") or decision.get("episode_id") or "")
        return cls(
            observation_id=stable_id("decision-action-observation", episode.episode_id, selected_symbol, prior_id),
            portfolio_id=episode.portfolio_id,
            account_id=episode.account_id,
            symbol=selected_symbol,
            observed_at=episode.observed_at,
            activity_episode_id=episode.episode_id,
            prior_decision_episode_id=prior_id,
            prior_action=action,
            observed_direction=direction,
            correspondence="unclassified" if neutral else "aligned" if aligned else "contrary",
            elapsed_minutes=elapsed,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "schemaVersion": DECISION_ACTION_OBSERVATION_VERSION,
            "observationId": self.observation_id,
            "portfolioId": self.portfolio_id,
            "accountId": self.account_id,
            "symbol": self.symbol,
            "observedAt": self.observed_at,
            "activityEpisodeId": self.activity_episode_id,
            "priorDecisionEpisodeId": self.prior_decision_episode_id,
            "priorAction": self.prior_action,
            "observedDirection": self.observed_direction,
            "correspondence": self.correspondence,
            "elapsedMinutes": self.elapsed_minutes,
            "causalityClaimed": False,
        }
