"""Action planning and immutable broker execution contracts."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Dict, Iterable, List


TRADE_EXECUTION_VERSION = "trade-execution-v1"
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

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


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
