from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


ACCOUNT_DATA_FAILURE_TERMS = ("실패", "오류", "unauthorized", "forbidden", "http 4", "http 5", "error", "timeout")
KR_MICROSTRUCTURE_MARKETS = {"KR", "KOR", "KOREA", "KOSPI", "KOSDAQ", "KONEX", "KRX", "XKRX"}
NON_KR_MICROSTRUCTURE_MARKETS = {
    "US",
    "USA",
    "NASDAQ",
    "NYSE",
    "AMEX",
    "ARCA",
    "BATS",
    "XNYS",
    "XNAS",
    "CRYPTO",
    "COIN",
}
NON_KR_MICROSTRUCTURE_CURRENCIES = {"USD", "USDT", "USDC", "BTC", "ETH"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def status_has_account_data_failure(status: object) -> bool:
    normalized = str(status or "").strip().lower()
    return any(term in normalized for term in ACCOUNT_DATA_FAILURE_TERMS)


def monitor_state_has_live_account_data(state: Dict[str, object]) -> bool:
    if not isinstance(state, dict):
        return False
    return str(state.get("mode") or "").strip().lower() == "live" and not status_has_account_data_failure(state.get("status"))


def expects_kr_microstructure_signals(market: object = "", currency: object = "", symbol: object = "") -> bool:
    market_code = str(market or "").strip().upper()
    currency_code = str(currency or "").strip().upper()
    compact_symbol = str(symbol or "").strip().upper().replace(".", "").replace("-", "")
    if market_code in KR_MICROSTRUCTURE_MARKETS or currency_code == "KRW":
        return True
    if compact_symbol.isdigit() and 4 <= len(compact_symbol) <= 8:
        return True
    if compact_symbol.isalpha() and 1 <= len(compact_symbol) <= 5:
        return False
    if market_code in NON_KR_MICROSTRUCTURE_MARKETS or currency_code in NON_KR_MICROSTRUCTURE_CURRENCIES:
        return False
    return True


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
    market_signal_coverage: Dict[str, object] = field(default_factory=dict)
    updated_at: str = ""
    market_value: float = 0.0
    market_value_krw: float = 0.0
    profit_loss: float = 0.0
    profit_loss_krw: float = 0.0
    profit_loss_rate: float = 0.0
    exchange_rate: float = 0.0
    trade_strength: float = 0.0
    trading_value: float = 0.0
    volume: float = 0.0
    volume_ratio: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    orderbook_bid_volume: float = 0.0
    orderbook_ask_volume: float = 0.0
    bid_ask_imbalance: float = 0.0
    foreign_buy_volume: float = 0.0
    foreign_sell_volume: float = 0.0
    foreign_net_volume: float = 0.0
    foreign_net_amount: float = 0.0
    institution_buy_volume: float = 0.0
    institution_sell_volume: float = 0.0
    institution_net_volume: float = 0.0
    institution_net_amount: float = 0.0
    individual_buy_volume: float = 0.0
    individual_sell_volume: float = 0.0
    individual_net_volume: float = 0.0
    individual_net_amount: float = 0.0
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
    source: str = "holding"

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
    profit_take_pressure: float = 0.0
    loss_cut_pressure: float = 0.0
    decision_basis: str = ""
    ontology_opinion: Dict[str, object] = field(default_factory=dict)
    ontology_worldview: Dict[str, object] = field(default_factory=dict)
    relation_rule_context: Dict[str, object] = field(default_factory=dict)
    ai_prompt_context: Dict[str, object] = field(default_factory=dict)
    active_investment_opinion: Dict[str, object] = field(default_factory=dict)
    ai_context: Dict[str, object] = field(default_factory=dict)

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
    metadata: Dict[str, object] = field(default_factory=dict)

    def has_live_account_data(self) -> bool:
        return monitor_state_has_live_account_data({"mode": self.mode, "status": self.status})

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
            "metadata": dict(self.metadata or {}),
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
    metadata: Dict[str, object] = field(default_factory=dict)
    generated_at: str = field(default_factory=utc_now_iso)

    def target(self) -> str:
        return self.symbol or "all"

    def cadence_key(self) -> str:
        if self.rule == "investmentInsight" and isinstance(self.metadata, dict):
            insight = self.metadata.get("ontologyInsight")
            if isinstance(insight, dict) and str(insight.get("cadenceKey") or "").strip():
                return str(insight.get("cadenceKey"))
        return ":".join(["cadence", "python", self.account_id, self.rule, self.target()])

    def message(self) -> str:
        title = self.title
        body = ["- " + line for line in self.lines if line]
        return "\n".join([title] + body)
