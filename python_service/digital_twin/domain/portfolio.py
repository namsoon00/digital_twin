from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


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
    change_rate: Optional[float] = None
    quote_source: str = ""
    quote_status: str = ""
    quote_message: str = ""
    data_quality: str = ""
    updated_at: str = ""
    market_value: float = 0.0
    profit_loss: float = 0.0
    profit_loss_rate: float = 0.0
    trade_strength: float = 0.0
    trading_value: float = 0.0
    volume: float = 0.0
    volume_ratio: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    foreign_buy_volume: float = 0.0
    foreign_sell_volume: float = 0.0
    foreign_net_volume: float = 0.0
    institution_buy_volume: float = 0.0
    institution_sell_volume: float = 0.0
    institution_net_volume: float = 0.0
    ma5: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    ma120: float = 0.0
    ma200: float = 0.0
    ma20_slope: float = 0.0
    ma60_slope: float = 0.0
    ma20_distance: float = 0.0
    ma60_distance: float = 0.0
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
    external_signals: Dict[str, object] = field(default_factory=dict)
    watchlist: List[Position] = field(default_factory=list)

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
            "externalSignals": dict(self.external_signals or {}),
            "watchlist": {
                item.symbol.upper(): item.to_dict()
                for item in self.watchlist
                if not item.is_cash()
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
    criteria: List[str] = field(default_factory=list)

    def target(self) -> str:
        return self.symbol or "all"

    def cadence_key(self) -> str:
        return ":".join(["cadence", "python", self.account_id, self.rule, self.target()])

    def message(self) -> str:
        title = self.title
        body = ["- " + line for line in self.lines if line]
        return "\n".join([title] + body)
