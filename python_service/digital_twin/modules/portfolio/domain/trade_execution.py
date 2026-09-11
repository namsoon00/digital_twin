"""Action planning and immutable broker execution contracts."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Dict, Iterable, List, Optional


TRADE_EXECUTION_VERSION = "trade-execution-v2"
EXECUTABLE_ACTIONS = {"BUY", "ADD", "TRIM", "SELL"}


def stable_execution_id(prefix: str, *values: object) -> str:
    raw = "|".join(str(value or "") for value in values)
    return prefix + ":" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class ActionEnvelope:
    portfolio_id: str
    symbol: str
    allowed_actions: List[str]
    max_buy_notional: float = 0.0
    max_buy_quantity: float = 0.0
    max_sell_quantity: float = 0.0
    minimum_cash_after: float = 0.0
    policy_version: str = ""
    blocked_reasons: List[str] = field(default_factory=list)
    notional_currency: str = "KRW"
    base_currency: str = "KRW"

    def allows(self, action: str, quantity: float = 0.0, notional: float = 0.0) -> bool:
        value = str(action or "").upper()
        if self.blocked_reasons or value not in {item.upper() for item in self.allowed_actions}:
            return False
        if value in {"BUY", "ADD"}:
            return quantity <= self.max_buy_quantity and notional <= self.max_buy_notional
        if value in {"TRIM", "SELL"}:
            return quantity <= self.max_sell_quantity
        return True

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["version"] = TRADE_EXECUTION_VERSION
        return payload


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str = "LIMIT"
    limit_price: float = 0.0
    currency: str = "KRW"

    @property
    def notional(self) -> float:
        return round(max(0.0, self.quantity) * max(0.0, self.limit_price), 8)

    def __post_init__(self) -> None:
        if str(self.side or "").upper() not in {"BUY", "SELL"}:
            raise ValueError("OrderIntent side must be BUY or SELL.")
        if self.quantity <= 0:
            raise ValueError("OrderIntent quantity must be positive.")

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["notional"] = self.notional
        return payload


@dataclass(frozen=True)
class ActionPlanSlice:
    slice_id: str
    sequence: int
    quantity: float
    max_notional: float
    trigger_conditions: List[str] = field(default_factory=list)
    status: str = "planned"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ActionPlan:
    plan_id: str
    portfolio_id: str
    decision_episode_id: str
    action: str
    policy_version: str
    inference_generation_id: str
    order_intents: List[OrderIntent] = field(default_factory=list)
    status: str = "review-required"
    created_at: str = ""
    expires_at: str = ""
    envelope: Optional[ActionEnvelope] = None
    invalidation_conditions: List[str] = field(default_factory=list)
    sizing_basis: Dict[str, object] = field(default_factory=dict)
    slices: List[ActionPlanSlice] = field(default_factory=list)
    account_snapshot_fingerprint: str = ""
    supersedes_plan_id: str = ""
    execution_conditions: Dict[str, object] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        portfolio_id: str,
        decision_episode_id: str,
        action: str,
        policy_version: str,
        inference_generation_id: str,
        order_intents: Iterable[OrderIntent] = None,
        created_at: str = "",
        expires_at: str = "",
        envelope: ActionEnvelope = None,
        invalidation_conditions: Iterable[str] = None,
        sizing_basis: Dict[str, object] = None,
        slices: Iterable[ActionPlanSlice] = None,
        account_snapshot_fingerprint: str = "",
        supersedes_plan_id: str = "",
        execution_conditions: Dict[str, object] = None,
    ):
        intents = list(order_intents or [])
        action_value = str(action or "HOLD").upper()
        return cls(
            plan_id=stable_execution_id("action-plan", portfolio_id, decision_episode_id, action_value, policy_version),
            portfolio_id=str(portfolio_id or ""),
            decision_episode_id=str(decision_episode_id or ""),
            action=action_value,
            policy_version=str(policy_version or ""),
            inference_generation_id=str(inference_generation_id or ""),
            order_intents=intents,
            status="review-required" if action_value in EXECUTABLE_ACTIONS else "informational",
            created_at=str(created_at or ""),
            expires_at=str(expires_at or ""),
            envelope=envelope,
            invalidation_conditions=list(invalidation_conditions or []),
            sizing_basis=dict(sizing_basis or {}),
            slices=list(slices or []),
            account_snapshot_fingerprint=str(account_snapshot_fingerprint or ""),
            supersedes_plan_id=str(supersedes_plan_id or ""),
            execution_conditions=dict(execution_conditions or {}),
        )

    def validate(self, envelope: ActionEnvelope) -> List[str]:
        errors = []
        if self.portfolio_id != envelope.portfolio_id:
            errors.append("portfolio-mismatch")
        if self.policy_version != envelope.policy_version:
            errors.append("policy-version-mismatch")
        total_buy = sum(item.notional for item in self.order_intents if item.side.upper() == "BUY")
        total_buy_quantity = sum(item.quantity for item in self.order_intents if item.side.upper() == "BUY")
        total_sell_quantity = sum(item.quantity for item in self.order_intents if item.side.upper() == "SELL")
        if not envelope.allows(self.action, total_buy_quantity or total_sell_quantity, total_buy):
            errors.append("outside-action-envelope")
        return errors

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["order_intents"] = [item.to_dict() for item in self.order_intents]
        payload["slices"] = [item.to_dict() for item in self.slices]
        payload["envelope"] = self.envelope.to_dict() if self.envelope else {}
        payload["version"] = TRADE_EXECUTION_VERSION
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        values = dict(payload or {})
        envelope_values = values.get("envelope") if isinstance(values.get("envelope"), dict) else {}
        envelope = ActionEnvelope(**{
            "portfolio_id": str(envelope_values.get("portfolio_id") or envelope_values.get("portfolioId") or values.get("portfolio_id") or values.get("portfolioId") or ""),
            "symbol": str(envelope_values.get("symbol") or ""),
            "allowed_actions": list(envelope_values.get("allowed_actions") or envelope_values.get("allowedActions") or []),
            "max_buy_notional": float(envelope_values.get("max_buy_notional") or envelope_values.get("maxBuyNotional") or 0),
            "max_buy_quantity": float(envelope_values.get("max_buy_quantity") or envelope_values.get("maxBuyQuantity") or 0),
            "max_sell_quantity": float(envelope_values.get("max_sell_quantity") or envelope_values.get("maxSellQuantity") or 0),
            "minimum_cash_after": float(envelope_values.get("minimum_cash_after") or envelope_values.get("minimumCashAfter") or 0),
            "policy_version": str(envelope_values.get("policy_version") or envelope_values.get("policyVersion") or values.get("policy_version") or values.get("policyVersion") or ""),
            "blocked_reasons": list(envelope_values.get("blocked_reasons") or envelope_values.get("blockedReasons") or []),
            "notional_currency": str(envelope_values.get("notional_currency") or envelope_values.get("notionalCurrency") or "KRW"),
            "base_currency": str(envelope_values.get("base_currency") or envelope_values.get("baseCurrency") or "KRW"),
        }) if envelope_values else None
        intents = []
        for item in values.get("order_intents") or values.get("orderIntents") or []:
            if not isinstance(item, dict):
                continue
            intents.append(OrderIntent(
                intent_id=str(item.get("intent_id") or item.get("intentId") or ""),
                symbol=str(item.get("symbol") or ""),
                side=str(item.get("side") or ""),
                quantity=float(item.get("quantity") or 0),
                order_type=str(item.get("order_type") or item.get("orderType") or "LIMIT"),
                limit_price=float(item.get("limit_price") or item.get("limitPrice") or 0),
                currency=str(item.get("currency") or "KRW"),
            ))
        slices = []
        for item in values.get("slices") or []:
            if not isinstance(item, dict):
                continue
            slices.append(ActionPlanSlice(
                slice_id=str(item.get("slice_id") or item.get("sliceId") or ""),
                sequence=int(item.get("sequence") or 0),
                quantity=float(item.get("quantity") or 0),
                max_notional=float(item.get("max_notional") or item.get("maxNotional") or 0),
                trigger_conditions=list(item.get("trigger_conditions") or item.get("triggerConditions") or []),
                status=str(item.get("status") or "planned"),
            ))
        return cls(
            plan_id=str(values.get("plan_id") or values.get("planId") or ""),
            portfolio_id=str(values.get("portfolio_id") or values.get("portfolioId") or ""),
            decision_episode_id=str(values.get("decision_episode_id") or values.get("decisionEpisodeId") or ""),
            action=str(values.get("action") or "HOLD"),
            policy_version=str(values.get("policy_version") or values.get("policyVersion") or ""),
            inference_generation_id=str(values.get("inference_generation_id") or values.get("inferenceGenerationId") or ""),
            order_intents=intents,
            status=str(values.get("status") or "review-required"),
            created_at=str(values.get("created_at") or values.get("createdAt") or ""),
            expires_at=str(values.get("expires_at") or values.get("expiresAt") or ""),
            envelope=envelope,
            invalidation_conditions=list(values.get("invalidation_conditions") or values.get("invalidationConditions") or []),
            sizing_basis=dict(values.get("sizing_basis") or values.get("sizingBasis") or {}),
            slices=slices,
            account_snapshot_fingerprint=str(values.get("account_snapshot_fingerprint") or values.get("accountSnapshotFingerprint") or ""),
            supersedes_plan_id=str(values.get("supersedes_plan_id") or values.get("supersedesPlanId") or ""),
            execution_conditions=dict(values.get("execution_conditions") or values.get("executionConditions") or {}),
        )


@dataclass(frozen=True)
class ActionPlanReview:
    review_id: str
    plan_id: str
    decision: str
    reviewer: str
    reviewed_at: str
    reason: str = ""
    policy_version: str = ""
    validation_errors: List[str] = field(default_factory=list)

    @classmethod
    def create(cls, plan_id: str, decision: str, reviewer: str, reviewed_at: str, **values):
        decision_value = str(decision or "").lower()
        if decision_value not in {"approved", "rejected"}:
            raise ValueError("Action plan review decision must be approved or rejected.")
        return cls(
            review_id=stable_execution_id("action-plan-review", plan_id, decision_value, reviewed_at, reviewer),
            plan_id=str(plan_id or ""),
            decision=decision_value,
            reviewer=str(reviewer or "local-user"),
            reviewed_at=str(reviewed_at or ""),
            reason=str(values.get("reason") or ""),
            policy_version=str(values.get("policy_version") or values.get("policyVersion") or ""),
            validation_errors=list(values.get("validation_errors") or values.get("validationErrors") or []),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["version"] = TRADE_EXECUTION_VERSION
        return payload


@dataclass(frozen=True)
class TradeFill:
    fill_id: str
    provider_execution_id: str
    order_intent_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    currency: str
    executed_at: str

    def __post_init__(self) -> None:
        if not self.provider_execution_id:
            raise ValueError("TradeFill requires provider_execution_id for idempotency.")
        if str(self.side or "").upper() not in {"BUY", "SELL"}:
            raise ValueError("TradeFill side must be BUY or SELL.")
        if self.quantity <= 0 or self.price <= 0 or self.fee < 0:
            raise ValueError("TradeFill requires positive quantity and price and a non-negative fee.")
        if not self.executed_at:
            raise ValueError("TradeFill requires executed_at.")

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        values = dict(payload or {})
        provider_id = str(values.get("providerExecutionId") or values.get("provider_execution_id") or "")
        return cls(
            fill_id=str(values.get("fillId") or values.get("fill_id") or stable_execution_id("trade-fill", provider_id)),
            provider_execution_id=provider_id,
            order_intent_id=str(values.get("orderIntentId") or values.get("order_intent_id") or ""),
            symbol=str(values.get("symbol") or "").upper(),
            side=str(values.get("side") or "").upper(),
            quantity=float(values.get("quantity") or 0),
            price=float(values.get("price") or 0),
            fee=float(values.get("fee") or 0),
            currency=str(values.get("currency") or "KRW").upper(),
            executed_at=str(values.get("executedAt") or values.get("executed_at") or ""),
        )


@dataclass
class ExecutionEpisode:
    execution_episode_id: str
    action_plan_id: str
    portfolio_id: str
    status: str = "pending"
    fills: List[TradeFill] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""

    @classmethod
    def for_plan(cls, plan: ActionPlan, started_at: str = ""):
        return cls(
            execution_episode_id=stable_execution_id("execution-episode", plan.plan_id),
            action_plan_id=plan.plan_id,
            portfolio_id=plan.portfolio_id,
            started_at=str(started_at or ""),
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        values = dict(payload or {})
        return cls(
            execution_episode_id=str(values.get("executionEpisodeId") or values.get("execution_episode_id") or ""),
            action_plan_id=str(values.get("actionPlanId") or values.get("action_plan_id") or ""),
            portfolio_id=str(values.get("portfolioId") or values.get("portfolio_id") or ""),
            status=str(values.get("status") or "pending"),
            fills=[TradeFill.from_dict(item) for item in values.get("fills") or [] if isinstance(item, dict)],
            started_at=str(values.get("startedAt") or values.get("started_at") or ""),
            completed_at=str(values.get("completedAt") or values.get("completed_at") or ""),
        )

    def record_fill(self, fill: TradeFill) -> bool:
        if any(item.provider_execution_id == fill.provider_execution_id for item in self.fills):
            return False
        self.fills.append(fill)
        self.status = "partially-filled"
        return True

    def complete(self, completed_at: str) -> None:
        self.status = "filled" if self.fills else "completed-without-fill"
        self.completed_at = str(completed_at or "")

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": TRADE_EXECUTION_VERSION,
            "executionEpisodeId": self.execution_episode_id,
            "actionPlanId": self.action_plan_id,
            "portfolioId": self.portfolio_id,
            "status": self.status,
            "fills": [item.to_dict() for item in self.fills],
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
        }


def action_plan_fingerprint(plan: ActionPlan) -> str:
    raw = json.dumps(plan.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
