"""Portfolio accounting, policy sizing, and governed execution use cases."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import math
from typing import Dict, Iterable, List, Optional, Protocol

from ..domain.investment_mandate import InvestmentMandate
from ..domain.events import ontology_reasoning_requested_event
from ..domain.portfolio_activity_episode import (
    DecisionActionObservation,
    PortfolioActivityEpisode,
    PortfolioSnapshotCheckpoint,
    PortfolioStateSnapshot,
    checkpoint_acceptance,
)
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
from ..domain.snapshot_portfolio_activity import (
    activity_payload,
    infer_snapshot_ledger_entries,
    trusted_account_snapshot,
)
from ..domain.portfolio_rebalancing import (
    AllocationBand,
    RebalanceLeg,
    RebalanceProposal,
    RebalanceScenario,
    RebalanceState,
    allocation_drifts,
    rebalance_transition,
)
from ..domain.portfolio_analytics import (
    PortfolioRiskSnapshot,
    portfolio_risk_snapshot,
    with_policy_limits,
)
from ..domain.portfolio_decision_cycle import PortfolioActionCandidate, PortfolioDecisionCycle
from ..domain.risk_exposure import ExposureMetric, ExposureSnapshot
from ..domain.trade_execution import (
    EXECUTABLE_ACTIONS,
    ActionEnvelope,
    ActionPlan,
    ActionPlanSlice,
    ActionPlanReview,
    ExecutionEpisode,
    OrderIntent,
    TradeFill,
    stable_execution_id,
)
from .portfolio_activity_notification_service import portfolio_activity_notification_job


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
    decision_cycle: PortfolioDecisionCycle
    risk_snapshot: Optional[PortfolioRiskSnapshot] = None
    snapshot_trust: Dict[str, object] = None
    inferred_activities: List[Dict[str, object]] = None
    inferred_entry_count: int = 0
    opening_entry_count: int = 0
    snapshot_checkpoint: Dict[str, object] = None
    activity_episode: Dict[str, object] = None
    portfolio_state: Dict[str, object] = None
    decision_action_observations: List[Dict[str, object]] = None
    factual_notification_queued: bool = False
    rebalance_state: Dict[str, object] = None
    rebalance_transition: Dict[str, object] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": "ready",
            "portfolioId": self.portfolio_id,
            "openingEntryCount": self.opening_entry_count,
            "reconciliation": self.reconciliation.to_dict(),
            "exposureSnapshot": self.exposure_snapshot.to_dict(),
            "rebalanceProposal": self.rebalance_proposal.to_dict() if self.rebalance_proposal else {},
            "portfolioDecisionCycle": self.decision_cycle.to_dict(),
            "portfolioRiskSnapshot": self.risk_snapshot.to_dict() if self.risk_snapshot else {},
            "snapshotTrust": dict(self.snapshot_trust or {}),
            "inferredEntryCount": self.inferred_entry_count,
            "inferredActivities": list(self.inferred_activities or []),
            "snapshotCheckpoint": dict(self.snapshot_checkpoint or {}),
            "activityEpisode": dict(self.activity_episode or {}),
            "portfolioState": dict(self.portfolio_state or {}),
            "decisionActionObservations": list(self.decision_action_observations or []),
            "factualNotificationQueued": self.factual_notification_queued,
            "rebalanceState": dict(self.rebalance_state or {}),
            "rebalanceTransition": dict(self.rebalance_transition or {}),
        }


class PortfolioAccountingService:
    """Reconcile complete broker balances and record bounded inferred changes."""

    def __init__(
        self,
        repository,
        account_repository=None,
        investment_domain_service=None,
        market_time_series_store=None,
        settings=None,
    ):
        self.repository = repository
        self.account_repository = account_repository
        self.investment_domain_service = investment_domain_service
        self.market_time_series_store = market_time_series_store
        self.settings = dict(settings or {})

    def append_ledger_entries(self, entries: Iterable[PortfolioLedgerEntry]) -> int:
        rows = list(entries or [])
        if not rows:
            return 0
        if self.investment_domain_service:
            return self.investment_domain_service.append_ledger_entries(rows)
        return self.repository.append_ledger_entries(rows)

    def observe_snapshot(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        trusted, trust = trusted_account_snapshot(snapshot)
        if not trusted:
            return {"status": "skipped-untrusted-snapshot", "snapshotTrust": trust}
        if not callable(getattr(self.repository, "commit_snapshot_observation", None)):
            return self.observe_snapshot_compatibility(snapshot, trust)
        portfolio_id = "portfolio:" + str(snapshot.account_id or "default")
        mandate = self.active_mandate(snapshot, portfolio_id)
        for _attempt in range(3):
            previous = self.repository.snapshot_checkpoint(portfolio_id)
            expected_version = previous.version if previous else 0
            checkpoint = PortfolioSnapshotCheckpoint.from_snapshot(
                snapshot,
                portfolio_id,
                version=expected_version + 1,
            )
            acceptance, reason = checkpoint_acceptance(previous, checkpoint)
            if acceptance == "unchanged":
                advanced = self.repository.advance_snapshot_checkpoint(expected_version, checkpoint)
                if advanced.get("status") == "checkpoint-conflict":
                    continue
                analysis = (
                    self.refresh_analysis(snapshot, portfolio_id, mandate)
                    if self.portfolio_analysis_due(portfolio_id, snapshot.generated_at)
                    else {
                        "analysisStatus": "deferred-unchanged-snapshot",
                        "analysisIntervalSeconds": self.portfolio_analysis_interval_seconds(),
                    }
                )
                return {
                    **advanced,
                    "reason": reason,
                    "portfolioId": portfolio_id,
                    "openingEntryCount": 0,
                    "inferredEntryCount": 0,
                    "inferredActivities": [],
                    "snapshotTrust": trust,
                    "snapshotCheckpoint": {
                        **checkpoint.to_dict(),
                        "checkpointVersion": advanced.get("actualCheckpointVersion"),
                    },
                    **analysis,
                }
            if acceptance in {"duplicate", "stale", "quarantined"}:
                visible_checkpoint = checkpoint
                if acceptance == "quarantined":
                    visible_checkpoint = replace(
                        checkpoint,
                        status="quarantined",
                        quarantine_reason=reason,
                    )
                    recorder = getattr(self.repository, "record_snapshot_quarantine", None)
                    if callable(recorder):
                        recorder(visible_checkpoint, reason, previous)
                return {
                    "status": acceptance,
                    "reason": reason,
                    "portfolioId": portfolio_id,
                    "openingEntryCount": 0,
                    "inferredEntryCount": 0,
                    "inferredActivities": [],
                    "snapshotTrust": trust,
                    "snapshotCheckpoint": visible_checkpoint.to_dict(),
                }
            entries = list(self.repository.ledger_entries(portfolio_id, limit=100000) or [])
            had_prior_ledger = bool(entries)
            opening_entries = self.opening_entries(snapshot, portfolio_id) if not entries else []
            current_state = PortfolioLedger(portfolio_id, snapshot.account_id).replay(entries)
            # A pre-existing ledger without a checkpoint becomes the first
            # trusted comparison baseline; it is never retroactively rewritten.
            inferred_entries = (
                infer_snapshot_ledger_entries(snapshot, portfolio_id, current_state, entries)
                if previous and had_prior_ledger
                else []
            )
            rows_to_commit = opening_entries + inferred_entries
            prospective_entries = entries + rows_to_commit
            ledger_state = PortfolioLedger(portfolio_id, snapshot.account_id).replay(prospective_entries)
            reconciliation = self.reconciliation(snapshot, portfolio_id, ledger_state)
            exposure = self.exposure_snapshot(snapshot, portfolio_id, mandate)
            risk = self.risk_snapshot(snapshot, portfolio_id, mandate, exposure)
            proposal = self.rebalance_proposal(snapshot, mandate, exposure, risk)
            decision_cycle = self.portfolio_decision_cycle(snapshot, mandate, exposure, reconciliation, risk, proposal)
            rebalance_state, transition, rebalance_event, rebalance_reasoning_event = self.rebalance_transition_context(
                snapshot, mandate, exposure, risk, proposal,
            )
            episode = PortfolioActivityEpisode.from_entries(inferred_entries, checkpoint, previous)
            state_snapshot = PortfolioStateSnapshot.from_snapshot(
                snapshot,
                portfolio_id,
                expected_version + 1,
                prospective_entries,
            )
            action_observations = self.decision_action_observations(episode)
            event = None
            job = None
            if rows_to_commit and self.investment_domain_service:
                event = self.investment_domain_service.ledger_recorded_event(
                    rows_to_commit,
                    len(rows_to_commit),
                    episode,
                )
            if episode and event:
                job = portfolio_activity_notification_job(episode, event, snapshot.account_label)
            fact_types = ["PortfolioActivityEpisode", "PortfolioStateSnapshot"]
            if action_observations:
                fact_types.append("DecisionActionObservation")
            reasoning_event = ontology_reasoning_requested_event(
                event,
                "portfolio-activity",
                symbols=episode.symbols,
                changed_count=len(inferred_entries),
                observed_count=len(inferred_entries),
                fact_types=fact_types,
                fact_types_by_symbol={symbol: fact_types for symbol in episode.symbols},
                changed_fields_by_symbol={symbol: ["portfolioActivity", "portfolioState"] for symbol in episode.symbols},
                reason="완전한 실계좌 잔고에서 보유 또는 현금 변화가 확인됐습니다.",
                importance_gate="portfolio-activity-change",
            ) if episode and event and episode.symbols else None
            committed = self.repository.commit_snapshot_observation(
                expected_version,
                checkpoint,
                rows_to_commit,
                episode,
                state_snapshot,
                reconciliation,
                exposure,
                proposal,
                decision_cycle,
                action_observations,
                event,
                job,
                reasoning_event,
                risk_snapshot=risk,
                rebalance_state=rebalance_state,
                rebalance_transition=transition,
                rebalance_event=rebalance_event,
                rebalance_reasoning_event=rebalance_reasoning_event,
            )
            if committed.get("status") == "checkpoint-conflict":
                continue
            if committed.get("status") != "committed":
                return {
                    **committed,
                    "portfolioId": portfolio_id,
                    "snapshotTrust": trust,
                }
            inserted_count = int(committed.get("insertedCount") or 0)
            if event and inserted_count and self.investment_domain_service:
                self.investment_domain_service.dispatch_recorded(event)
            if committed.get("rebalanceTransitionRecorded") and self.investment_domain_service:
                self.investment_domain_service.dispatch_recorded(rebalance_event)
                self.investment_domain_service.dispatch_recorded(rebalance_reasoning_event)
            return PortfolioLifecycleObservation(
                portfolio_id=portfolio_id,
                reconciliation=reconciliation,
                exposure_snapshot=exposure,
                rebalance_proposal=proposal,
                decision_cycle=decision_cycle,
                risk_snapshot=risk,
                snapshot_trust=trust,
                inferred_activities=[activity_payload(item) for item in inferred_entries] if inserted_count else [],
                inferred_entry_count=len(inferred_entries) if inserted_count else 0,
                opening_entry_count=len(opening_entries) if inserted_count else 0,
                snapshot_checkpoint={**checkpoint.to_dict(), "checkpointVersion": committed.get("actualCheckpointVersion")},
                activity_episode=episode.to_dict() if episode and inserted_count else {},
                portfolio_state=state_snapshot.to_dict(),
                decision_action_observations=[item.to_dict() for item in action_observations],
                factual_notification_queued=bool(committed.get("notificationQueued")),
                rebalance_state=rebalance_state.to_dict(),
                rebalance_transition=transition.to_dict() if transition and committed.get("rebalanceTransitionRecorded") else {},
            ).to_dict()
        return {
            "status": "checkpoint-conflict",
            "reason": "concurrent-snapshot-observation-retry-exhausted",
            "portfolioId": portfolio_id,
            "snapshotTrust": trust,
        }

    def portfolio_analysis_interval_seconds(self) -> int:
        return max(60, min(3600, int(number(
            self.settings.get("portfolioAnalysisIntervalSeconds")
        ) or 300)))

    def portfolio_analysis_due(self, portfolio_id: str, observed_at: str) -> bool:
        loader = getattr(self.repository, "latest_rebalance_current_state", None)
        if not callable(loader):
            return True
        current = loader(portfolio_id) or {}
        previous_at = parse_timestamp(current.get("observedAt") or current.get("observed_at"))
        candidate_at = parse_timestamp(observed_at)
        if not previous_at or not candidate_at:
            return True
        return (candidate_at - previous_at).total_seconds() >= self.portfolio_analysis_interval_seconds()

    def decision_action_observations(self, episode) -> List[DecisionActionObservation]:
        if not episode or not callable(getattr(self.repository, "latest_decision_before", None)):
            return []
        rows = []
        for symbol in episode.symbols:
            prior = self.repository.latest_decision_before(episode.account_id, symbol, episode.observed_at)
            observation = DecisionActionObservation.from_activity(episode, prior, symbol)
            if observation:
                rows.append(observation)
        return rows

    def observe_snapshot_compatibility(self, snapshot: AccountSnapshot, trust: Dict[str, object]) -> Dict[str, object]:
        """Compatibility path for lightweight adapters that predate checkpoints."""
        portfolio_id = "portfolio:" + str(snapshot.account_id or "default")
        mandate = self.active_mandate(snapshot, portfolio_id)
        entries = list(self.repository.ledger_entries(portfolio_id, limit=100000) or [])
        had_prior_ledger = bool(entries)
        opening_entries = self.opening_entries(snapshot, portfolio_id) if not entries else []
        if opening_entries:
            self.append_ledger_entries(opening_entries)
            entries = list(self.repository.ledger_entries(portfolio_id, limit=100000) or [])
        ledger_state = PortfolioLedger(portfolio_id, snapshot.account_id).replay(entries)
        inferred_entries = infer_snapshot_ledger_entries(snapshot, portfolio_id, ledger_state, entries) if had_prior_ledger else []
        inferred_entry_count = self.append_ledger_entries(inferred_entries)
        if inferred_entries:
            entries = list(self.repository.ledger_entries(portfolio_id, limit=100000) or [])
            ledger_state = PortfolioLedger(portfolio_id, snapshot.account_id).replay(entries)
        reconciliation = self.reconciliation(snapshot, portfolio_id, ledger_state)
        self.repository.save_reconciliation(reconciliation)
        exposure = self.exposure_snapshot(snapshot, portfolio_id, mandate)
        self.repository.save_exposure_snapshot(exposure)
        risk = self.risk_snapshot(snapshot, portfolio_id, mandate, exposure)
        saver = getattr(self.repository, "save_risk_snapshot", None)
        if callable(saver):
            saver(risk)
        proposal = self.rebalance_proposal(snapshot, mandate, exposure, risk)
        if proposal:
            self.repository.save_rebalance_proposal(proposal)
        decision_cycle = self.portfolio_decision_cycle(snapshot, mandate, exposure, reconciliation, risk, proposal)
        self.repository.save_portfolio_decision_cycle(decision_cycle)
        return PortfolioLifecycleObservation(
            portfolio_id=portfolio_id,
            reconciliation=reconciliation,
            exposure_snapshot=exposure,
            rebalance_proposal=proposal,
            decision_cycle=decision_cycle,
            risk_snapshot=risk,
            snapshot_trust=trust,
            inferred_activities=[activity_payload(item) for item in inferred_entries] if inferred_entry_count else [],
            inferred_entry_count=inferred_entry_count,
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
                reason="완전한 실계좌 잔고 변화로 보정한 불변 원장과 현재 스냅샷을 비교한 값입니다.",
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
            reason="입출금·체결 원인이 알려지지 않은 현금 변화는 별도 잔액 조정 사실로 기록합니다.",
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

    def risk_snapshot(
        self,
        snapshot: AccountSnapshot,
        portfolio_id: str,
        mandate: InvestmentMandate,
        exposure: ExposureSnapshot,
    ) -> PortfolioRiskSnapshot:
        weights = {
            metric.key.upper(): metric.ratio_pct
            for metric in exposure.metrics
            if metric.exposure_type == "position" and metric.ratio_pct > 0
        }
        positions = {
            str(item.symbol or "").upper(): item
            for item in snapshot.positions or []
            if not item.is_cash() and number(item.quantity) > 0
        }
        benchmark_by_symbol = {}
        for symbol in weights:
            position = positions.get(symbol)
            market = str(getattr(position, "market", "") or "").upper()
            currency = str(getattr(position, "currency", "") or "").upper()
            benchmark_key = "CRYPTO" if market in {"CRYPTO", "COIN"} or currency in {"BTC", "ETH"} else (
                "US" if market in {"US", "USA", "NASDAQ", "NYSE", "AMEX"} or currency == "USD" else "KR"
            )
            benchmark = mandate.benchmark_symbols.get(benchmark_key, "")
            if benchmark:
                benchmark_by_symbol[symbol] = benchmark
        symbols = list(dict.fromkeys([*weights, *benchmark_by_symbol.values()]))
        series = {}
        loader = getattr(self.market_time_series_store, "load_portfolio_analysis_series", None)
        if callable(loader) and symbols:
            series = loader(
                snapshot.account_id,
                symbols,
                as_of=str(snapshot.generated_at or utc_now_iso()),
                limit_per_symbol=max(30, min(1000, int(number(self.settings.get("portfolioRiskHistoryDays")) or 260))),
            )
        measured = portfolio_risk_snapshot(
            portfolio_id,
            str(snapshot.generated_at or utc_now_iso()),
            series,
            weights,
            benchmark_by_symbol,
        )
        return with_policy_limits(
            measured,
            max_volatility_pct=mandate.max_portfolio_volatility_pct,
            max_drawdown_pct=mandate.max_portfolio_drawdown_pct,
            max_correlation=mandate.max_pairwise_correlation,
            policy_version=mandate.policy_version,
        )

    def refresh_analysis(
        self,
        snapshot: AccountSnapshot,
        portfolio_id: str,
        mandate: InvestmentMandate,
    ) -> Dict[str, object]:
        entries = list(self.repository.ledger_entries(portfolio_id, limit=100000) or [])
        ledger_state = PortfolioLedger(portfolio_id, snapshot.account_id).replay(entries)
        reconciliation = self.reconciliation(snapshot, portfolio_id, ledger_state)
        exposure = self.exposure_snapshot(snapshot, portfolio_id, mandate)
        risk = self.risk_snapshot(snapshot, portfolio_id, mandate, exposure)
        proposal = self.rebalance_proposal(snapshot, mandate, exposure, risk)
        cycle = self.portfolio_decision_cycle(snapshot, mandate, exposure, reconciliation, risk, proposal)
        rebalance_state, transition, source_event, reasoning_event = self.rebalance_transition_context(
            snapshot, mandate, exposure, risk, proposal,
        )
        bundle_saver = getattr(self.repository, "save_portfolio_analysis_bundle", None)
        transition_recorded = False
        if callable(bundle_saver):
            saved = bundle_saver(
                risk,
                exposure,
                proposal,
                cycle,
                None,
                None,
                rebalance_state=rebalance_state,
                rebalance_transition=transition,
                rebalance_event=source_event,
                rebalance_reasoning_event=reasoning_event,
            )
            if saved.get("rebalanceTransitionRecorded") and self.investment_domain_service and source_event and reasoning_event:
                transition_recorded = True
                self.investment_domain_service.dispatch_recorded(source_event)
                self.investment_domain_service.dispatch_recorded(reasoning_event)
        else:
            self.repository.save_exposure_snapshot(exposure)
            risk_saver = getattr(self.repository, "save_risk_snapshot", None)
            if callable(risk_saver):
                risk_saver(risk)
            if proposal:
                self.repository.save_rebalance_proposal(proposal)
            self.repository.save_portfolio_decision_cycle(cycle)
            if self.investment_domain_service and source_event and reasoning_event:
                self.investment_domain_service.publish(source_event)
                self.investment_domain_service.publish(reasoning_event)
                transition_recorded = True
        return {
            "reconciliation": reconciliation.to_dict(),
            "exposureSnapshot": exposure.to_dict(),
            "portfolioRiskSnapshot": risk.to_dict(),
            "rebalanceProposal": proposal.to_dict() if proposal else {},
            "rebalanceState": rebalance_state.to_dict(),
            "rebalanceTransition": transition.to_dict() if transition and transition_recorded else {},
            "portfolioDecisionCycle": cycle.to_dict(),
        }

    def rebalance_transition_context(
        self,
        snapshot: AccountSnapshot,
        mandate: InvestmentMandate,
        exposure: ExposureSnapshot,
        risk: Optional[PortfolioRiskSnapshot],
        proposal: Optional[RebalanceProposal],
    ):
        current = RebalanceState.from_analysis(
            exposure.portfolio_id,
            mandate.policy_version,
            exposure,
            risk,
            proposal,
        )
        baseline_loader = getattr(self.repository, "latest_rebalance_state", None)
        baseline = baseline_loader(exposure.portfolio_id) if callable(baseline_loader) else {}
        previous = RebalanceState.from_dict(baseline) if baseline else None
        transition = rebalance_transition(previous, current)
        if not transition or not self.investment_domain_service:
            return current, transition, None, None
        symbols = sorted({
            *[str(item.symbol or "").upper().strip() for item in risk.positions if str(item.symbol or "").strip()],
            *[str(item.symbol or "").upper().strip() for item in (proposal.legs if proposal else []) if str(item.symbol or "").strip()],
        })
        source_event = self.investment_domain_service.rebalance_transition_event(transition, symbols)
        fact_types = [
            "Portfolio",
            "ExposureSnapshot",
            "PortfolioRiskSnapshot",
            "RebalanceProposal",
            "RebalanceState",
        ]
        reasoning_event = ontology_reasoning_requested_event(
            source_event,
            "portfolio-rebalance-transition",
            changed_count=1,
            observed_count=max(1, len(symbols)),
            fact_types=fact_types,
            subject_kind="PORTFOLIO",
            subject_id=exposure.portfolio_id,
            affected_symbols=symbols,
            subject_revision=transition.revision,
            subject_changed_fields=["portfolioExposure", "portfolioRisk", "rebalanceState"],
            account_id=snapshot.account_id,
            reason="포트폴리오 정책 위반 상태가 열리거나 의미 있게 변경되거나 해소됐습니다.",
            importance_gate="portfolio-rebalance-state-transition",
        )
        return current, transition, source_event, reasoning_event

    def rebalance_proposal(
        self,
        snapshot: AccountSnapshot,
        mandate: InvestmentMandate,
        exposure: ExposureSnapshot,
        risk: Optional[PortfolioRiskSnapshot] = None,
    ) -> Optional[RebalanceProposal]:
        breached = exposure.over_policy_metrics()
        total = max(0.0, number(snapshot.portfolio.total))
        bands_by_key = {}
        current = {}
        metric_by_key = {}
        for metric in breached:
            allocation_key = metric.exposure_type + ":" + metric.key
            current[allocation_key] = metric.ratio_pct
            metric_by_key[allocation_key] = metric
            if metric.policy_direction == "minimum":
                bands_by_key[allocation_key] = AllocationBand(
                    allocation_key, metric.policy_limit_pct, metric.policy_limit_pct, 100
                )
            else:
                bands_by_key[allocation_key] = AllocationBand(
                    allocation_key, metric.policy_limit_pct, 0, metric.policy_limit_pct
                )
        exposure_by_key = {
            metric.exposure_type + ":" + metric.key: metric
            for metric in exposure.metrics
        }
        band_width = max(0.0, mandate.allocation_band_pct)
        for allocation_key, target in sorted(mandate.target_allocations.items()):
            metric = exposure_by_key.get(allocation_key)
            current[allocation_key] = metric.ratio_pct if metric else 0.0
            if metric:
                metric_by_key[allocation_key] = metric
            bands_by_key[allocation_key] = AllocationBand(
                allocation_key,
                target,
                max(0.0, target - band_width),
                min(100.0, target + band_width),
            )
        drifts = allocation_drifts(current, bands_by_key.values())
        legs = []
        for drift in drifts:
            if not drift.band_delta_pct:
                continue
            metric = metric_by_key.get(drift.allocation_key)
            exposure_type, _, exposure_key = drift.allocation_key.partition(":")
            if exposure_type != "position":
                continue
            symbol = metric.key if metric else exposure_key
            raw_notional = abs(drift.target_delta_pct) * total / 100
            turnover_cap = mandate.max_rebalance_turnover_pct * total / 100
            maximum_notional = min(raw_notional, turnover_cap) if turnover_cap > 0 else 0.0
            legs.append(RebalanceLeg(
                allocation_key=drift.allocation_key,
                side="DECREASE" if drift.band_delta_pct > 0 else "INCREASE",
                target_delta_pct=-drift.target_delta_pct,
                maximum_notional=maximum_notional,
                symbol=symbol,
                estimated_cost=maximum_notional * mandate.estimated_transaction_cost_bps / 10000,
                before_weight_pct=drift.current_weight_pct,
                after_weight_pct=drift.band.target_weight_pct,
                rationale="정책 배분 허용 범위 복원 후보",
            ))
        risk_breached = bool(risk and any([
            risk.volatility_policy_delta_pct > 0,
            risk.drawdown_policy_delta_pct > 0,
            risk.correlation_policy_delta > 0,
        ]))
        if not any(item.band_delta_pct for item in drifts) and not risk_breached:
            return None
        before_metrics = {
            "portfolioTotal": total,
            "annualizedVolatilityPct": risk.annualized_volatility_pct if risk else None,
            "maximumDrawdownPct": risk.maximum_drawdown_pct if risk else None,
            "maximumPairwiseCorrelation": risk.maximum_pairwise_correlation if risk else None,
        }
        scenarios = [RebalanceScenario(
            scenario_id="rebalance-scenario:no-action:" + exposure.snapshot_id.split(":")[-1],
            scenario_type="NO_ACTION",
            label="현재 구성 유지",
            before_metrics=before_metrics,
            after_metrics=before_metrics,
            policy_effects=["현재 배분과 위험 상태를 유지합니다."],
            data_state=risk.data_state if risk else "partial",
            missing_data=list(risk.missing_data if risk else ["portfolioRiskSnapshot"]),
        )]
        if legs:
            scenarios.append(RebalanceScenario(
                scenario_id="rebalance-scenario:restore-band:" + exposure.snapshot_id.split(":")[-1],
                scenario_type="RESTORE_POLICY_BANDS",
                label="정책 배분 범위 복원",
                legs=legs,
                before_metrics=before_metrics,
                after_metrics={**before_metrics, "allocationBandsRestored": True},
                estimated_cost=round(sum(item.estimated_cost for item in legs), 4),
                turnover_pct=round(sum(item.maximum_notional for item in legs) / (total or 1.0) * 100, 6),
                policy_effects=["정책 배분 이탈을 허용 범위 안으로 되돌리는 최대 주문 후보입니다."],
                invalidation_conditions=["시세 또는 계좌 잔고가 바뀌면 다시 계산합니다."],
                data_state=risk.data_state if risk else "partial",
                missing_data=list(risk.missing_data if risk else ["portfolioRiskSnapshot"]),
            ))
        if risk_breached:
            scenarios.append(RebalanceScenario(
                scenario_id="rebalance-scenario:risk-review:" + risk.risk_snapshot_id.split(":")[-1],
                scenario_type="REDUCE_PORTFOLIO_RISK",
                label="포트폴리오 위험 축소 검토",
                legs=[item for item in legs if item.side == "DECREASE"],
                before_metrics=before_metrics,
                after_metrics={**before_metrics, "requiresGraphValidation": True},
                estimated_cost=round(sum(item.estimated_cost for item in legs if item.side == "DECREASE"), 4),
                turnover_pct=round(sum(item.maximum_notional for item in legs if item.side == "DECREASE") / (total or 1.0) * 100, 6),
                policy_effects=["위험 한도 초과 원인과 축소 대상을 관계 추론에서 검증합니다."],
                invalidation_conditions=["위험 한도 초과가 해소되면 이 시나리오는 무효입니다."],
                data_state=risk.data_state,
                missing_data=list(risk.missing_data),
            ))
        return RebalanceProposal.create(
            exposure.portfolio_id,
            mandate.policy_version,
            exposure.snapshot_id,
            drifts,
            legs,
            scenarios,
            created_at=exposure.observed_at,
        )

    def portfolio_decision_cycle(
        self,
        snapshot: AccountSnapshot,
        mandate: InvestmentMandate,
        exposure: ExposureSnapshot,
        reconciliation: PortfolioReconciliation,
        risk: Optional[PortfolioRiskSnapshot] = None,
        proposal: Optional[RebalanceProposal] = None,
    ) -> PortfolioDecisionCycle:
        total = max(0.0, number(snapshot.portfolio.total)) or 1.0
        cash = max(0.0, number(snapshot.portfolio.cash))
        base_metrics = {
            "portfolioTotal": total,
            "cash": cash,
            "cashWeightPct": cash / total * 100,
            "overPolicyCount": len(exposure.over_policy_metrics()),
            "annualizedVolatilityPct": risk.annualized_volatility_pct if risk else None,
            "maximumDrawdownPct": risk.maximum_drawdown_pct if risk else None,
            "maximumPairwiseCorrelation": risk.maximum_pairwise_correlation if risk else None,
        }
        source_snapshot_id = reconciliation.balance_fingerprint + ":" + exposure.snapshot_id + ":" + (
            risk.risk_snapshot_id if risk else "risk-unavailable"
        )
        candidates = [PortfolioActionCandidate.create(
            source_snapshot_id,
            "NO_ACTION",
            "현재 구성 유지",
            before_metrics=base_metrics,
            after_metrics=base_metrics,
            policy_effects=["현재 정책 위반과 데이터 공백을 그대로 유지합니다."],
            required_relation_types=["HAS_EXPOSURE", "GOVERNED_BY_MANDATE"],
            data_state="complete" if reconciliation.status == "matched" else "partial",
        )]
        for metric in exposure.over_policy_metrics():
            if metric.exposure_type == "position":
                maximum_notional = max(0.0, metric.policy_delta_pct * total / 100)
                after_cash = cash + maximum_notional
                candidates.append(PortfolioActionCandidate.create(
                    source_snapshot_id,
                    "REDUCE_POSITION_EXPOSURE",
                    metric.key + " 정책 초과분 검토",
                    affected_symbol=metric.key,
                    maximum_notional=maximum_notional,
                    before_metrics={**base_metrics, "positionWeightPct": metric.ratio_pct},
                    after_metrics={
                        **base_metrics,
                        "cash": after_cash,
                        "cashWeightPct": after_cash / total * 100,
                        "positionWeightPct": metric.policy_limit_pct,
                    },
                    policy_effects=["종목 비중을 정책 상한 안으로 낮추는 최대 범위입니다."],
                    required_relation_types=["EXCEEDS_POLICY", "HAS_EXPOSURE", "HAS_LIQUIDITY_CONSTRAINT"],
                    data_state="partial",
                ))
            elif metric.exposure_type == "cash" and metric.policy_direction == "minimum":
                shortfall = max(0.0, metric.policy_delta_pct * total / 100)
                candidates.append(PortfolioActionCandidate.create(
                    source_snapshot_id,
                    "RESTORE_CASH_FLOOR",
                    "현금 하한 회복 검토",
                    maximum_notional=shortfall,
                    before_metrics=base_metrics,
                    after_metrics={**base_metrics, "cash": cash + shortfall, "cashWeightPct": metric.policy_limit_pct},
                    policy_effects=["현금 비중을 계좌 정책 하한까지 회복하는 부족분입니다."],
                    required_relation_types=["EXCEEDS_POLICY", "HAS_RISK_BUDGET"],
                    data_state="partial",
                ))
        for drift in (proposal.drifts if proposal else []):
            if drift.band_delta_pct >= 0 or not drift.allocation_key.startswith("position:"):
                continue
            symbol = drift.allocation_key.split(":", 1)[1]
            candidates.append(PortfolioActionCandidate.create(
                source_snapshot_id,
                "INCREASE_UNDERWEIGHT_ALLOCATION",
                symbol + " 목표 배분 하단 복원 검토",
                affected_symbol=symbol,
                maximum_notional=min(
                    abs(drift.target_delta_pct) * total / 100,
                    mandate.max_rebalance_turnover_pct * total / 100,
                ),
                before_metrics={**base_metrics, "positionWeightPct": drift.current_weight_pct},
                after_metrics={**base_metrics, "positionWeightPct": drift.band.target_weight_pct},
                policy_effects=["사용자가 정한 목표 배분 범위의 하단을 복원하는 후보입니다."],
                required_relation_types=["HAS_TARGET_ALLOCATION", "HAS_RISK_BUDGET"],
                data_state=risk.data_state if risk else "partial",
            ))
        if risk and any([
            risk.volatility_policy_delta_pct > 0,
            risk.drawdown_policy_delta_pct > 0,
            risk.correlation_policy_delta > 0,
        ]):
            candidates.append(PortfolioActionCandidate.create(
                source_snapshot_id,
                "REDUCE_PORTFOLIO_RISK",
                "포트폴리오 위험 한도 초과 검토",
                before_metrics=base_metrics,
                after_metrics={**base_metrics, "requiresGraphValidation": True},
                policy_effects=["변동성·낙폭·상관 위험의 원인 종목을 관계 추론으로 검증합니다."],
                required_relation_types=["HAS_RISK_SNAPSHOT", "EXCEEDS_RISK_POLICY"],
                data_state=risk.data_state,
            ))
        missing = list(risk.missing_data if risk else [
            "positionReturnVolatility", "positionCorrelationMatrix", "portfolioBenchmarkReturn"
        ])
        if reconciliation.status != "matched":
            missing.append("matchedPortfolioLedger")
        return PortfolioDecisionCycle.create(
            exposure.portfolio_id,
            snapshot.account_id,
            mandate.policy_version,
            source_snapshot_id,
            candidates,
            data_state="partial" if missing else "complete",
            missing_data=missing,
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

    def slice_count(self) -> int:
        return max(1, min(10, int(number(self.settings.get("investmentActionPlanSliceCount")) or 3)))

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
        lifecycle = {}
        lifecycle_loader = getattr(self.repository, "latest_portfolio_lifecycle", None)
        if callable(lifecycle_loader):
            lifecycle = lifecycle_loader(portfolio_id) or {}
        cycle = lifecycle.get("portfolioDecisionCycle") if isinstance(lifecycle.get("portfolioDecisionCycle"), dict) else {}
        compatible_candidate_types = {
            "BUY": {"INCREASE_UNDERWEIGHT_ALLOCATION"},
            "ADD": {"INCREASE_UNDERWEIGHT_ALLOCATION"},
            "TRIM": {"REDUCE_POSITION_EXPOSURE", "REDUCE_PORTFOLIO_RISK"},
            "SELL": {"REDUCE_POSITION_EXPOSURE", "REDUCE_PORTFOLIO_RISK"},
        }.get(action, set())
        policy_candidates = [
            item for item in cycle.get("candidates") or []
            if isinstance(item, dict)
            and str(item.get("candidate_type") or item.get("candidateType") or "").upper() in compatible_candidate_types
            and str(item.get("affected_symbol") or item.get("affectedSymbol") or episode.symbol or "").upper()
            in {"", str(episode.symbol or "").upper()}
        ]
        policy_candidate_cap_base = max([
            number(item.get("maximum_notional") or item.get("maximumNotional"))
            for item in policy_candidates
        ] or [0.0])
        if action in EXECUTABLE_ACTIONS and price <= 0:
            blocked.append("current-price-missing")
        if action in {"BUY", "ADD"} and currency != "KRW" and exchange_rate <= 0:
            blocked.append("base-currency-conversion-required")
        minimum_cash_after = total * (mandate.min_cash_weight_pct / 100) if mandate else cash
        cash_headroom = max(0.0, cash - minimum_cash_after)
        position_headroom = max(0.0, total * (mandate.max_position_weight_pct / 100) - value) if mandate else 0.0
        max_buy_notional_base = min(cash_headroom, position_headroom)
        if policy_candidate_cap_base > 0:
            max_buy_notional_base = min(max_buy_notional_base, policy_candidate_cap_base)
        max_buy_notional = (
            max_buy_notional_base / exchange_rate
            if currency != "KRW" and exchange_rate > 0
            else max_buy_notional_base if currency == "KRW" else 0.0
        )
        max_buy_quantity = math.floor(max_buy_notional / price) if price > 0 else 0
        max_sell_quantity = sellable
        if policy_candidate_cap_base > 0 and price > 0:
            local_cap = policy_candidate_cap_base / exchange_rate if currency != "KRW" and exchange_rate > 0 else policy_candidate_cap_base
            max_sell_quantity = min(sellable, math.floor(local_cap / price))
        envelope = ActionEnvelope(
            portfolio_id=portfolio_id,
            symbol=str(episode.symbol or "").upper(),
            allowed_actions=allowed,
            max_buy_notional=max_buy_notional,
            max_buy_quantity=max_buy_quantity,
            max_sell_quantity=max_sell_quantity,
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
        elif action in {"TRIM", "SELL"} and max_sell_quantity > 0:
            intent_quantity = max_sell_quantity if action == "SELL" else max(1, math.floor(max_sell_quantity * slice_ratio))
            intents.append(OrderIntent(
                intent_id=stable_execution_id("order-intent", episode.episode_id, action, episode.symbol),
                symbol=str(episode.symbol or "").upper(),
                side="SELL",
                quantity=min(intent_quantity, max_sell_quantity),
                order_type="LIMIT",
                limit_price=price,
                currency=currency,
            ))
        created = parse_timestamp(episode.decided_at) or datetime.now(timezone.utc)
        selected = next((item for item in episode.hypothesis_set.hypotheses if item.hypothesis_id == episode.selected_hypothesis_id), None)
        snapshot_fingerprint = stable_execution_id(
            "account-snapshot",
            state.get("generatedAt"),
            episode.symbol,
            price,
            quantity,
            sellable,
            total,
            cash,
            policy_version,
        )
        slices: List[ActionPlanSlice] = []
        if intents:
            intent = intents[0]
            count = min(self.slice_count(), max(1, int(intent.quantity)))
            remaining = intent.quantity
            for sequence in range(1, count + 1):
                slice_quantity = math.floor(remaining / (count - sequence + 1))
                slice_quantity = max(1, slice_quantity) if remaining >= 1 else remaining
                slice_quantity = min(remaining, slice_quantity)
                remaining -= slice_quantity
                slices.append(ActionPlanSlice(
                    slice_id=stable_execution_id("action-plan-slice", episode.episode_id, sequence),
                    sequence=sequence,
                    quantity=slice_quantity,
                    max_notional=round(slice_quantity * price, 8),
                    trigger_conditions=["승인 시 현재 계좌·시세 재검증", "앞선 분할 실행 결과 확인" if sequence > 1 else "첫 분할 실행"],
                ))
        latest_plan = None
        latest_loader = getattr(self.repository, "latest_active_action_plan", None)
        if callable(latest_loader):
            latest_plan = latest_loader(portfolio_id, str(episode.symbol or "").upper(), action)
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
                "policyCandidateMaximumNotionalBase": policy_candidate_cap_base,
                "policyCandidateIds": [
                    str(item.get("candidate_id") or item.get("candidateId") or "")
                    for item in policy_candidates
                ],
                "portfolioDecisionCycleId": str(cycle.get("cycleId") or ""),
                "portfolioRiskSnapshotId": str(
                    (lifecycle.get("portfolioRiskSnapshot") or {}).get("riskSnapshotId") or ""
                ) if isinstance(lifecycle.get("portfolioRiskSnapshot"), dict) else "",
                "exchangeRate": exchange_rate,
                "notionalCurrency": currency,
                "graphAllowedActions": sorted(graph_allowed),
                "mandateAllowedActions": sorted(mandate_allowed),
            },
            slices=slices,
            account_snapshot_fingerprint=snapshot_fingerprint,
            supersedes_plan_id=latest_plan.plan_id if latest_plan else "",
            execution_conditions={
                "approvalRequired": action in EXECUTABLE_ACTIONS,
                "revalidateAccountSnapshot": True,
                "revalidatePolicyVersion": True,
                "maximumQuoteDriftPct": max(0.1, min(20.0, number(self.settings.get("investmentExecutionQuoteDriftPct")) or 2.0)),
                "brokerSubmissionEnabled": False,
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
        investment_domain_service=None,
    ):
        self.repository = repository
        self.gateway = gateway or DisabledBrokerOrderGateway()
        self.monitor_store = monitor_store
        self.settings = dict(settings or {})
        self.investment_domain_service = investment_domain_service

    def persist_execution(self, episode: ExecutionEpisode, plan: ActionPlan) -> Dict[str, object]:
        event = (
            self.investment_domain_service.execution_recorded_event(episode)
            if self.investment_domain_service else None
        )
        saver = getattr(self.repository, "save_execution_with_ledger", None)
        if callable(saver):
            result = saver(episode, plan, event)
            if event:
                self.investment_domain_service.dispatch_recorded(event)
            return result
        if self.investment_domain_service:
            self.investment_domain_service.save_execution(episode)
        else:
            self.repository.save_execution_episode(episode)
        return {"status": episode.status, "executionEpisode": episode.to_dict(), "actualLedgerEntryCount": 0}

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
        return self.persist_execution(episode, plan)

    def record_fills(
        self,
        plan_id: str,
        fills: Iterable[Dict[str, object]],
        completed_at: str = "",
    ) -> Dict[str, object]:
        """Import confirmed provider fills without enabling order submission."""
        plan = self.repository.action_plan(plan_id)
        if not plan:
            raise ValueError("Action plan not found.")
        if plan.status != "approved":
            return {"status": "blocked", "planId": plan.plan_id, "validationErrors": ["plan-not-approved"]}
        intents = {item.intent_id: item for item in plan.order_intents}
        episode_loader = getattr(self.repository, "execution_episode_for_plan", None)
        episode = episode_loader(plan.plan_id) if callable(episode_loader) else None
        episode = episode or ExecutionEpisode.for_plan(plan, utc_now_iso())
        rows = [TradeFill.from_dict(item) for item in fills or [] if isinstance(item, dict)]
        validation_errors = []
        known_provider_ids = {item.provider_execution_id for item in episode.fills}
        accepted_rows = []
        for fill in rows:
            if fill.provider_execution_id in known_provider_ids:
                continue
            intent = intents.get(fill.order_intent_id)
            if not intent:
                validation_errors.append("fill-order-intent-missing")
                continue
            if fill.symbol != intent.symbol or fill.side != intent.side or fill.currency != intent.currency:
                validation_errors.append("fill-intent-mismatch")
                continue
            known_provider_ids.add(fill.provider_execution_id)
            accepted_rows.append(fill)
        if not rows:
            validation_errors.append("confirmed-fill-missing")
        cumulative_by_intent = {}
        for fill in [*episode.fills, *accepted_rows]:
            cumulative_by_intent[fill.order_intent_id] = (
                cumulative_by_intent.get(fill.order_intent_id, 0.0) + fill.quantity
            )
        if any(
            quantity > intents[intent_id].quantity + 1e-9
            for intent_id, quantity in cumulative_by_intent.items()
            if intent_id in intents
        ):
            validation_errors.append("fill-quantity-exceeds-intent")
        if validation_errors:
            return {
                "status": "blocked",
                "planId": plan.plan_id,
                "validationErrors": list(dict.fromkeys(validation_errors)),
            }
        if not accepted_rows:
            return {
                "status": episode.status,
                "planId": plan.plan_id,
                "executionEpisode": episode.to_dict(),
                "actualLedgerEntryCount": 0,
                "duplicateFillCount": len(rows),
            }
        if not episode.started_at:
            episode.started_at = min(item.executed_at for item in accepted_rows)
        for fill in accepted_rows:
            episode.record_fill(fill)
        episode.complete(completed_at or max(item.executed_at for item in accepted_rows) or utc_now_iso())
        return self.persist_execution(episode, plan)

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
        planned_price = number(plan.sizing_basis.get("currentPrice"))
        maximum_quote_drift = number(plan.execution_conditions.get("maximumQuoteDriftPct")) or max(
            0.1,
            min(20.0, number(self.settings.get("investmentExecutionQuoteDriftPct")) or 2.0),
        )
        if planned_price > 0 and current_price > 0:
            quote_drift_pct = abs(current_price - planned_price) / planned_price * 100
            if quote_drift_pct > maximum_quote_drift:
                errors.append("quote-drift-exceeded")
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
