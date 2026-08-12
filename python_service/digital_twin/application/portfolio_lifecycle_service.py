"""Portfolio accounting, policy sizing, and governed execution use cases."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import math
from typing import Dict, Iterable, List, Optional, Protocol

from ..domain.investment_mandate import InvestmentMandate
from ..domain.portfolio import AccountSnapshot
from ..domain.portfolio_ledger import (
    OPENING_CASH,
    OPENING_POSITION,
    PortfolioLedger,
    PortfolioLedgerEntry,
    PortfolioReconciliation,
    ReconciliationDifference,
    decimal_value,
)
from ..domain.portfolio_rebalancing import (
    AllocationBand,
    RebalanceLeg,
    RebalanceProposal,
    allocation_drifts,
)
from ..domain.risk_exposure import ExposureMetric, ExposureSnapshot
from ..domain.trade_execution import (
    EXECUTABLE_ACTIONS,
    ActionEnvelope,
    ActionPlan,
    ActionPlanReview,
    ExecutionEpisode,
    OrderIntent,
    stable_execution_id,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: object) -> Optional[datetime]:
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


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def monitor_account_state(monitor_store, account_id: str) -> Dict[str, object]:
    if not monitor_store:
        return {}
    account_key = str(account_id or "")
    previous = getattr(monitor_store, "previous", None)
    if isinstance(previous, dict) and isinstance(previous.get(account_key), dict):
        return dict(previous[account_key])
    loader = getattr(monitor_store, "load_previous", None)
    if not callable(loader):
        return {}
    try:
        loaded = loader(account_key)
    except TypeError:
        loaded = loader()
    if not isinstance(loaded, dict):
        return {}
    if isinstance(loaded.get(account_key), dict):
        return dict(loaded[account_key])
    if "portfolio" in loaded or "accountId" in loaded or "account_id" in loaded:
        return dict(loaded)
    return {}


def position_value(position: object) -> float:
    return max(0.0, number(getattr(position, "market_value_krw", 0) or getattr(position, "market_value", 0)))


def stable_exposure_snapshot_id(portfolio_id: str, metrics: Iterable[ExposureMetric]) -> str:
    values = "|".join(sorted(
        ":".join([
            item.exposure_type,
            item.key,
            f"{item.ratio_pct:.1f}",
            f"{item.policy_limit_pct:.1f}",
            item.policy_direction,
        ])
        for item in metrics or []
    ))
    return "exposure-snapshot:" + hashlib.sha256((portfolio_id + "|" + values).encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class PortfolioLifecycleObservation:
    portfolio_id: str
    reconciliation: PortfolioReconciliation
    exposure_snapshot: ExposureSnapshot
    rebalance_proposal: Optional[RebalanceProposal]
    opening_entry_count: int = 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": "ready",
            "portfolioId": self.portfolio_id,
            "openingEntryCount": self.opening_entry_count,
            "reconciliation": self.reconciliation.to_dict(),
            "exposureSnapshot": self.exposure_snapshot.to_dict(),
            "rebalanceProposal": self.rebalance_proposal.to_dict() if self.rebalance_proposal else {},
        }


class PortfolioAccountingService:
    """Reconcile one live broker snapshot without inventing missing trades."""

    def __init__(self, repository, account_repository=None):
        self.repository = repository
        self.account_repository = account_repository

    def observe_snapshot(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        if not snapshot or not snapshot.has_live_account_data():
            return {"status": "skipped-non-live-snapshot"}
        portfolio_id = "portfolio:" + str(snapshot.account_id or "default")
        mandate = self.active_mandate(snapshot, portfolio_id)
        entries = list(self.repository.ledger_entries(portfolio_id, limit=100000) or [])
        opening_entries = self.opening_entries(snapshot, portfolio_id) if not entries else []
        if opening_entries:
            self.repository.append_ledger_entries(opening_entries)
            entries.extend(opening_entries)
        ledger_state = PortfolioLedger(portfolio_id, snapshot.account_id).replay(entries)
        reconciliation = self.reconciliation(snapshot, portfolio_id, ledger_state)
        self.repository.save_reconciliation(reconciliation)
        exposure = self.exposure_snapshot(snapshot, portfolio_id, mandate)
        self.repository.save_exposure_snapshot(exposure)
        proposal = self.rebalance_proposal(snapshot, mandate, exposure)
        if proposal:
            self.repository.save_rebalance_proposal(proposal)
        return PortfolioLifecycleObservation(
            portfolio_id=portfolio_id,
            reconciliation=reconciliation,
            exposure_snapshot=exposure,
            rebalance_proposal=proposal,
            opening_entry_count=len(opening_entries),
        ).to_dict()

    def active_mandate(self, snapshot: AccountSnapshot, portfolio_id: str) -> InvestmentMandate:
        payload = self.repository.active_mandate(portfolio_id)
        if payload:
            return InvestmentMandate.from_dict(payload)
        account = None
        if self.account_repository:
            loader = getattr(self.account_repository, "load_all", None) or getattr(self.account_repository, "load", None)
            for candidate in loader() if callable(loader) else []:
                if str(getattr(candidate, "account_id", "")) == snapshot.account_id:
                    account = candidate
                    break
        if account and callable(getattr(account, "investment_mandate", None)):
            mandate = account.investment_mandate(snapshot.generated_at)
        else:
            mandate = InvestmentMandate.from_profile(
                snapshot.account_id,
                portfolio_id,
                {},
                snapshot.generated_at,
            )
        return self.repository.save_mandate(mandate)

    def opening_entries(self, snapshot: AccountSnapshot, portfolio_id: str) -> List[PortfolioLedgerEntry]:
        stamp = str(snapshot.generated_at or utc_now_iso())
        entries: List[PortfolioLedgerEntry] = []
        for position in snapshot.positions or []:
            if position.is_cash() or number(position.quantity) <= 0:
                continue
            symbol = str(position.symbol or "").upper().strip()
            if not symbol:
                continue
            source_reference = "opening-balance:" + snapshot.account_id + ":position:" + symbol
            entries.append(PortfolioLedgerEntry.create(
                portfolio_id,
                snapshot.account_id,
                OPENING_POSITION,
                stamp,
                entry_id=stable_execution_id("ledger-opening-position", portfolio_id, symbol),
                source_reference=source_reference,
                symbol=symbol,
                currency=str(position.currency or "KRW"),
                quantity=position.quantity,
                unit_price=position.average_price,
                payload={
                    "source": "broker-snapshot-opening-balance",
                    "snapshotGeneratedAt": stamp,
                    "syntheticOpeningBalance": True,
                },
            ))
        cash = max(0.0, number(snapshot.portfolio.cash))
        if cash:
            entries.append(PortfolioLedgerEntry.create(
                portfolio_id,
                snapshot.account_id,
                OPENING_CASH,
                stamp,
                entry_id=stable_execution_id("ledger-opening-cash", portfolio_id, "KRW"),
                source_reference="opening-balance:" + snapshot.account_id + ":cash:KRW",
                currency="KRW",
                amount=cash,
                payload={
                    "source": "broker-snapshot-opening-balance",
                    "snapshotGeneratedAt": stamp,
                    "syntheticOpeningBalance": True,
                },
            ))
        return entries

    def reconciliation(self, snapshot: AccountSnapshot, portfolio_id: str, ledger_state) -> PortfolioReconciliation:
        observed_positions = {
            str(item.symbol or "").upper().strip(): decimal_value(item.quantity)
            for item in snapshot.positions or []
            if not item.is_cash() and str(item.symbol or "").strip()
        }
        symbols = sorted(set(observed_positions) | {
            str(item.symbol or "").upper().strip()
            for item in ledger_state.lots
            if item.remaining_quantity > 0
        })
        differences = [
            ReconciliationDifference(
                difference_type="position-quantity",
                key=symbol,
                expected=ledger_state.quantity(symbol),
                observed=observed_positions.get(symbol, Decimal("0")),
                tolerance=Decimal("0.000001"),
                reason="공급자 거래내역 없이 잔고 스냅샷과 불변 원장을 비교한 값입니다.",
            )
            for symbol in symbols
        ]
        observed_cash = decimal_value(snapshot.portfolio.cash)
        differences.append(ReconciliationDifference(
            difference_type="cash-balance",
            key="KRW",
            expected=ledger_state.cash.get("KRW", Decimal("0")),
            observed=observed_cash,
            tolerance=Decimal("1"),
            currency="KRW",
            reason="현금 입출금·체결 내역이 없으면 차이를 자동 보정하지 않습니다.",
        ))
        balances = {
            "positions": {key: str(observed_positions[key]) for key in sorted(observed_positions)},
            "cash": {"KRW": str(observed_cash)},
            "ledgerPositions": {key: str(ledger_state.quantity(key)) for key in symbols},
            "ledgerCash": {"KRW": str(ledger_state.cash.get("KRW", Decimal("0")))},
        }
        return PortfolioReconciliation.create(
            portfolio_id,
            snapshot.account_id,
            snapshot.generated_at,
            differences,
            balances,
            created_at=utc_now_iso(),
        )

    def exposure_snapshot(
        self,
        snapshot: AccountSnapshot,
        portfolio_id: str,
        mandate: InvestmentMandate,
    ) -> ExposureSnapshot:
        observed_at = str(snapshot.generated_at or utc_now_iso())
        total = max(0.0, number(snapshot.portfolio.total))
        if not total:
            total = sum(position_value(item) for item in snapshot.positions or []) + max(0.0, number(snapshot.portfolio.cash))
        total = total or 1.0
        metrics: List[ExposureMetric] = []
        sector_values: Dict[str, float] = {}
        fx_value = 0.0
        for position in snapshot.positions or []:
            if position.is_cash() or number(position.quantity) <= 0:
                continue
            value = position_value(position)
            symbol = str(position.symbol or "").upper().strip()
            ratio = value / total * 100
            metrics.append(ExposureMetric(
                exposure_id="position-exposure:" + portfolio_id + ":" + symbol,
                portfolio_id=portfolio_id,
                exposure_type="position",
                key=symbol,
                value=value,
                ratio_pct=ratio,
                policy_limit_pct=mandate.max_position_weight_pct,
                observed_at=observed_at,
                source="live-account-snapshot",
            ))
            sector = str(position.sector or "기타")
            sector_values[sector] = sector_values.get(sector, 0.0) + value
            if str(position.currency or "KRW").upper() != "KRW":
                fx_value += value
        for sector, value in sorted(sector_values.items()):
            metrics.append(ExposureMetric(
                exposure_id="sector-exposure:" + portfolio_id + ":" + sector,
                portfolio_id=portfolio_id,
                exposure_type="sector",
                key=sector,
                value=value,
                ratio_pct=value / total * 100,
                policy_limit_pct=mandate.max_sector_weight_pct,
                observed_at=observed_at,
                source="live-account-snapshot",
            ))
        metrics.append(ExposureMetric(
            exposure_id="fx-exposure:" + portfolio_id,
            portfolio_id=portfolio_id,
            exposure_type="currency",
            key="non-KRW",
            value=fx_value,
            ratio_pct=fx_value / total * 100,
            policy_limit_pct=mandate.fx_exposure_review_pct,
            observed_at=observed_at,
            source="live-account-snapshot",
        ))
        cash = max(0.0, number(snapshot.portfolio.cash))
        metrics.append(ExposureMetric(
            exposure_id="cash-exposure:" + portfolio_id,
            portfolio_id=portfolio_id,
            exposure_type="cash",
            key="KRW",
            value=cash,
            ratio_pct=cash / total * 100,
            policy_limit_pct=mandate.min_cash_weight_pct,
            observed_at=observed_at,
            source="live-account-snapshot",
            policy_direction="minimum",
        ))
        return ExposureSnapshot(
            snapshot_id=stable_exposure_snapshot_id(portfolio_id, metrics),
            portfolio_id=portfolio_id,
            metrics=metrics,
            observed_at=observed_at,
        )

    def rebalance_proposal(
        self,
        snapshot: AccountSnapshot,
        mandate: InvestmentMandate,
        exposure: ExposureSnapshot,
    ) -> Optional[RebalanceProposal]:
        breached = exposure.over_policy_metrics()
        if not breached:
            return None
        total = max(0.0, number(snapshot.portfolio.total))
        bands = []
        current = {}
        metric_by_key = {}
        for metric in breached:
            allocation_key = metric.exposure_type + ":" + metric.key
            current[allocation_key] = metric.ratio_pct
            metric_by_key[allocation_key] = metric
            if metric.policy_direction == "minimum":
                bands.append(AllocationBand(allocation_key, metric.policy_limit_pct, metric.policy_limit_pct, 100))
            else:
                bands.append(AllocationBand(allocation_key, metric.policy_limit_pct, 0, metric.policy_limit_pct))
        drifts = allocation_drifts(current, bands)
        legs = []
        for drift in drifts:
            if not drift.band_delta_pct:
                continue
            metric = metric_by_key[drift.allocation_key]
            if metric.exposure_type != "position":
                continue
            legs.append(RebalanceLeg(
                allocation_key=drift.allocation_key,
                side="DECREASE" if drift.band_delta_pct > 0 else "INCREASE",
                target_delta_pct=-drift.band_delta_pct,
                maximum_notional=abs(drift.band_delta_pct) * total / 100,
                symbol=metric.key,
            ))
        return RebalanceProposal.create(
            exposure.portfolio_id,
            mandate.policy_version,
            exposure.snapshot_id,
            drifts,
            legs,
            created_at=exposure.observed_at,
        )


class DecisionActionPlanningService:
    """Compile TypeDB/AI categorical action into policy-bounded arithmetic."""

    def __init__(self, repository, monitor_store=None, settings: Dict[str, object] = None):
        self.repository = repository
        self.monitor_store = monitor_store
        self.settings = dict(settings or {})

    def plan_expiry_minutes(self) -> int:
        return max(5, min(1440, int(number(self.settings.get("investmentActionPlanExpiryMinutes")) or 30)))

    def slice_pct(self) -> float:
        return max(1.0, min(100.0, number(self.settings.get("investmentActionPlanSlicePct")) or 25.0))

    def monitor_state(self, account_id: str) -> Dict[str, object]:
        return monitor_account_state(self.monitor_store, account_id)

    def prepare(self, episode, context: Dict[str, object]) -> ActionPlan:
        portfolio_id = str(episode.portfolio_id or "portfolio:" + str(episode.account_id or "default"))
        mandate_payload = self.repository.active_mandate(portfolio_id)
        mandate = InvestmentMandate.from_dict(mandate_payload) if mandate_payload else None
        relation = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
        graph_envelope = relation.get("actionEnvelope") if isinstance(relation.get("actionEnvelope"), dict) else {}
        graph_allowed = {
            str(item or "").upper().strip()
            for item in graph_envelope.get("allowedActions") or relation.get("allowedActions") or []
            if str(item or "").strip()
        }
        mandate_allowed = set(mandate.allowed_actions if mandate else [])
        allowed = sorted(graph_allowed & mandate_allowed) if graph_allowed else sorted(mandate_allowed)
        state = self.monitor_state(episode.account_id)
        positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
        position = dict(positions.get(str(episode.symbol or "").upper()) or {})
        portfolio = state.get("portfolio") if isinstance(state.get("portfolio"), dict) else {}
        price = max(0.0, number(position.get("current_price") or position.get("currentPrice")))
        currency = str(position.get("currency") or "KRW").upper()
        exchange_rate = max(0.0, number(position.get("exchange_rate") or position.get("exchangeRate")))
        quantity = max(0.0, number(position.get("quantity")))
        sellable = max(0.0, number(position.get("sellable_quantity") or position.get("sellableQuantity") or quantity))
        value = max(0.0, number(position.get("market_value_krw") or position.get("marketValueKrw") or position.get("market_value") or position.get("marketValue")))
        total = max(0.0, number(portfolio.get("total")))
        cash = max(0.0, number(portfolio.get("cash")))
        blocked = []
        policy_version = mandate.policy_version if mandate else str(episode.mandate_version or "")
        if not mandate:
            blocked.append("active-mandate-missing")
        if episode.mandate_version and policy_version and episode.mandate_version != policy_version:
            blocked.append("decision-policy-version-stale")
        action = str(episode.action or "HOLD").upper()
        if action in EXECUTABLE_ACTIONS and price <= 0:
            blocked.append("current-price-missing")
        if action in {"BUY", "ADD"} and currency != "KRW" and exchange_rate <= 0:
            blocked.append("base-currency-conversion-required")
        minimum_cash_after = total * (mandate.min_cash_weight_pct / 100) if mandate else cash
        cash_headroom = max(0.0, cash - minimum_cash_after)
        position_headroom = max(0.0, total * (mandate.max_position_weight_pct / 100) - value) if mandate else 0.0
        max_buy_notional_base = min(cash_headroom, position_headroom)
        max_buy_notional = (
            max_buy_notional_base / exchange_rate
            if currency != "KRW" and exchange_rate > 0
            else max_buy_notional_base if currency == "KRW" else 0.0
        )
        max_buy_quantity = math.floor(max_buy_notional / price) if price > 0 else 0
        envelope = ActionEnvelope(
            portfolio_id=portfolio_id,
            symbol=str(episode.symbol or "").upper(),
            allowed_actions=allowed,
            max_buy_notional=max_buy_notional,
            max_buy_quantity=max_buy_quantity,
            max_sell_quantity=sellable,
            minimum_cash_after=minimum_cash_after,
            policy_version=policy_version,
            blocked_reasons=blocked,
            notional_currency=currency,
            base_currency="KRW",
        )
        slice_ratio = self.slice_pct() / 100
        intents: List[OrderIntent] = []
        if action in {"BUY", "ADD"} and max_buy_quantity >= 1:
            intent_quantity = max(1, math.floor(max_buy_quantity * slice_ratio))
            intents.append(OrderIntent(
                intent_id=stable_execution_id("order-intent", episode.episode_id, action, episode.symbol),
                symbol=str(episode.symbol or "").upper(),
                side="BUY",
                quantity=min(intent_quantity, max_buy_quantity),
                order_type="LIMIT",
                limit_price=price,
                currency=currency,
            ))
        elif action in {"TRIM", "SELL"} and sellable > 0:
            intent_quantity = sellable if action == "SELL" else max(1, math.floor(sellable * slice_ratio))
            intents.append(OrderIntent(
                intent_id=stable_execution_id("order-intent", episode.episode_id, action, episode.symbol),
                symbol=str(episode.symbol or "").upper(),
                side="SELL",
                quantity=min(intent_quantity, sellable),
                order_type="LIMIT",
                limit_price=price,
                currency=currency,
            ))
        created = parse_timestamp(episode.decided_at) or datetime.now(timezone.utc)
        selected = next((item for item in episode.hypothesis_set.hypotheses if item.hypothesis_id == episode.selected_hypothesis_id), None)
        plan = ActionPlan.create(
            portfolio_id=portfolio_id,
            decision_episode_id=episode.episode_id,
            action=action,
            policy_version=policy_version,
            inference_generation_id=episode.inference_generation_id,
            order_intents=intents,
            created_at=created.isoformat().replace("+00:00", "Z"),
            expires_at=(created + timedelta(minutes=self.plan_expiry_minutes())).isoformat().replace("+00:00", "Z"),
            envelope=envelope,
            invalidation_conditions=list(getattr(selected, "invalidation_conditions", []) or []),
            sizing_basis={
                "source": "policy-bounded-arithmetic",
                "slicePct": self.slice_pct(),
                "currentPrice": price,
                "portfolioTotal": total,
                "cash": cash,
                "currentPositionValue": value,
                "maxBuyNotionalBase": max_buy_notional_base,
                "exchangeRate": exchange_rate,
                "notionalCurrency": currency,
                "graphAllowedActions": sorted(graph_allowed),
                "mandateAllowedActions": sorted(mandate_allowed),
            },
        )
        if plan.validate(envelope) or (action in EXECUTABLE_ACTIONS and not intents):
            plan = replace(plan, status="blocked")
        return plan

    def save(self, plan: ActionPlan) -> ActionPlan:
        return self.repository.save_action_plan(plan)


class BrokerOrderGateway(Protocol):
    def configured(self) -> bool:
        ...

    def submit(self, plan: ActionPlan) -> ExecutionEpisode:
        ...


class DisabledBrokerOrderGateway:
    def configured(self) -> bool:
        return False

    def submit(self, plan: ActionPlan) -> ExecutionEpisode:
        episode = ExecutionEpisode.for_plan(plan, utc_now_iso())
        episode.status = "blocked-provider-not-configured"
        episode.completed_at = utc_now_iso()
        return episode


class TradeExecutionService:
    """Approve plans separately from broker submission and revalidate on both."""

    def __init__(
        self,
        repository,
        gateway: BrokerOrderGateway = None,
        monitor_store=None,
        settings: Dict[str, object] = None,
    ):
        self.repository = repository
        self.gateway = gateway or DisabledBrokerOrderGateway()
        self.monitor_store = monitor_store
        self.settings = dict(settings or {})

    def review_plan(self, plan_id: str, decision: str, reviewer: str, reason: str = "") -> Dict[str, object]:
        plan = self.repository.action_plan(plan_id)
        if not plan:
            raise ValueError("Action plan not found.")
        errors = self.validation_errors(plan)
        decision_value = str(decision or "").lower()
        if decision_value == "approved" and errors:
            decision_value = "rejected"
        review = ActionPlanReview.create(
            plan.plan_id,
            decision_value,
            reviewer or "local-user",
            utc_now_iso(),
            reason=reason,
            policy_version=plan.policy_version,
            validation_errors=errors,
        )
        plan = replace(plan, status=decision_value)
        self.repository.save_action_plan_review(review)
        self.repository.save_action_plan(plan)
        return {"plan": plan.to_dict(), "review": review.to_dict(), "validationErrors": errors}

    def submit_plan(self, plan_id: str) -> Dict[str, object]:
        plan = self.repository.action_plan(plan_id)
        if not plan:
            raise ValueError("Action plan not found.")
        errors = self.validation_errors(plan)
        if plan.status != "approved":
            errors.append("plan-not-approved")
        if not self.gateway.configured():
            errors.append("broker-order-provider-not-configured")
        if errors:
            return {"status": "blocked", "planId": plan.plan_id, "validationErrors": list(dict.fromkeys(errors))}
        episode = self.gateway.submit(plan)
        self.repository.save_execution_episode(episode)
        return {"status": episode.status, "executionEpisode": episode.to_dict()}

    def validation_errors(self, plan: ActionPlan) -> List[str]:
        errors = []
        mandate = self.repository.active_mandate(plan.portfolio_id)
        active_version = str(mandate.get("policyVersion") or mandate.get("policy_version") or "") if isinstance(mandate, dict) else ""
        if not active_version or active_version != plan.policy_version:
            errors.append("policy-version-mismatch")
        expires = parse_timestamp(plan.expires_at)
        if expires and expires <= datetime.now(timezone.utc):
            errors.append("plan-expired")
        if not plan.envelope:
            errors.append("action-envelope-missing")
        else:
            errors.extend(plan.validate(plan.envelope))
        errors.extend(self.current_account_errors(plan))
        return list(dict.fromkeys(errors))

    def current_account_errors(self, plan: ActionPlan) -> List[str]:
        if not self.monitor_store or plan.action not in EXECUTABLE_ACTIONS:
            return []
        account_id = plan.portfolio_id[len("portfolio:"):] if plan.portfolio_id.startswith("portfolio:") else plan.portfolio_id
        state = monitor_account_state(self.monitor_store, account_id)
        if not state or str(state.get("mode") or "").lower() != "live":
            return ["current-live-account-snapshot-missing"]
        errors = []
        generated = parse_timestamp(state.get("generatedAt"))
        maximum_age = max(1, min(120, int(number(self.settings.get("investmentExecutionSnapshotMaxAgeMinutes")) or 10)))
        if not generated or datetime.now(timezone.utc) - generated > timedelta(minutes=maximum_age):
            errors.append("current-account-snapshot-stale")
        positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
        symbol = plan.envelope.symbol if plan.envelope else ""
        position = dict(positions.get(symbol) or {})
        current_price = number(position.get("current_price") or position.get("currentPrice"))
        if current_price <= 0:
            errors.append("current-price-missing")
        sell_quantity = sum(item.quantity for item in plan.order_intents if item.side.upper() == "SELL")
        sellable = number(position.get("sellable_quantity") or position.get("sellableQuantity") or position.get("quantity"))
        if sell_quantity > sellable:
            errors.append("current-sellable-quantity-insufficient")
        if plan.envelope and any(item.side.upper() == "BUY" for item in plan.order_intents):
            portfolio = state.get("portfolio") if isinstance(state.get("portfolio"), dict) else {}
            cash = number(portfolio.get("cash"))
            local_notional = sum(item.notional for item in plan.order_intents if item.side.upper() == "BUY")
            exchange_rate = number(plan.sizing_basis.get("exchangeRate")) or 1.0
            base_notional = local_notional * exchange_rate
            if cash - base_notional < plan.envelope.minimum_cash_after:
                errors.append("current-cash-floor-breach")
        return errors
