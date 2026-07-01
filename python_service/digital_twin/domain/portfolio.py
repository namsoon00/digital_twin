from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Position:
    symbol: str
    name: str
    market: str = ""
    currency: str = ""
    quantity: float = 0.0
    sellable_quantity: float = 0.0
    average_price: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    profit_loss: float = 0.0
    profit_loss_rate: float = 0.0
    sector: str = "기타"

    def key(self) -> str:
        return self.symbol.upper()

    def is_cash(self) -> bool:
        return self.symbol.upper() == "CASH" or self.sector == "현금"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class PortfolioSummary:
    total: float
    invested: float
    cash: float
    markets: List[Dict[str, object]]
    sectors: List[Dict[str, object]]
    concentration: float


@dataclass
class DecisionItem:
    symbol: str
    name: str
    sector: str
    market: str
    currency: str
    market_value: float
    profit_loss: float
    profit_loss_rate: float
    exit_pressure: float
    decision: str
    tone: str
    source: str = "holding"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class AccountSnapshot:
    account_id: str
    account_label: str
    provider: str
    mode: str
    status: str
    generated_at: str
    portfolio: PortfolioSummary
    positions: List[Position] = field(default_factory=list)
    decisions: List[DecisionItem] = field(default_factory=list)

    def to_monitor_state(self) -> Dict[str, object]:
        return {
            "accountId": self.account_id,
            "accountLabel": self.account_label,
            "provider": self.provider,
            "mode": self.mode,
            "status": self.status,
            "generatedAt": self.generated_at,
            "portfolio": asdict(self.portfolio),
            "positions": {
                item.symbol.upper(): item.to_dict()
                for item in self.positions
                if not item.is_cash()
            },
            "decisions": {
                item.symbol.upper(): item.to_dict()
                for item in self.decisions
                if item.source == "holding"
            },
        }


@dataclass
class AlertEvent:
    account_id: str
    account_label: str
    severity: str
    rule: str
    key: str
    title: str
    lines: List[str]
    symbol: str = ""

    def target(self) -> str:
        return self.symbol or "all"

    def cadence_key(self) -> str:
        return ":".join(["cadence", "python", self.account_id, self.rule, self.target()])

    def message(self) -> str:
        title = self.title
        if self.account_label and self.account_label not in title:
            title = self.account_label + " " + title
        body = ["- " + line for line in self.lines if line]
        return "\n".join([title] + body)

