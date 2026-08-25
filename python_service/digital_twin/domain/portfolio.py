import copy
from dataclasses import asdict, dataclass, field, fields
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
    previous_close: float = 0.0
    return_1d: Optional[float] = None
    return_3d: Optional[float] = None
    return_5d: Optional[float] = None
    price_change_source: str = ""
    price_change_basis: str = ""
    price_history_adjustment: str = ""
    price_change_usable: bool = False
    quote_source: str = ""
    quote_status: str = ""
    quote_message: str = ""
    data_quality: str = ""
    market_signal_coverage: Dict[str, object] = field(default_factory=dict)
    updated_at: str = ""
    source_as_of: str = ""
    source_fetched_at: str = ""
    source_timestamp_state: str = ""
    freshness_status: str = ""
    freshness_reason: str = ""
    freshness_age_minutes: Optional[float] = None
    freshness_max_age_minutes: Optional[float] = None
    latency_status: str = ""
    latency_reason: str = ""
    market_session: str = ""
    market_session_label: str = ""
    source_transport: str = ""
    real_time: bool = False
    indicator_as_of: str = ""
    indicator_fetched_at: str = ""
    market_value: float = 0.0
    market_value_krw: float = 0.0
    broker_market_value: float = 0.0
    broker_market_value_after_cost: float = 0.0
    broker_purchase_amount: float = 0.0
    broker_profit_loss: float = 0.0
    broker_profit_loss_after_cost: float = 0.0
    broker_market_value_krw: float = 0.0
    broker_market_value_after_cost_krw: float = 0.0
    broker_source_as_of: str = ""
    mark_to_market_value: float = 0.0
    mark_to_market_value_krw: float = 0.0
    account_value_krw: float = 0.0
    account_value_basis: str = ""
    valuation_fx_source: str = ""
    valuation_fx_state: str = ""
    valuation_fx_as_of: str = ""
    valuation_snapshot_id: str = ""
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
    ma5_distance: float = 0.0
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
    valuation_snapshot_id: str = ""
    valuation_basis: str = ""
    broker_comparable_total: float = 0.0
    broker_gross_total: float = 0.0
    broker_net_total: float = 0.0
    mark_to_market_total: float = 0.0
    account_equity_total: float = 0.0
    valuation: Dict[str, object] = field(default_factory=dict)


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
    decision: str
    tone: str
    source: str = "holding"
    review_level: str = "check"
    data_state: str = "partial"
    change_state: str = "unchanged"
    conflict_state: str = "context-only"
    validation_state: str = "conditional"
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

    def projection_observation_input(self, target_symbols=None) -> Dict[str, object]:
        """Return a bounded observation set for an incremental ABox update.

        ``referencePositions`` always retains the complete account view for
        portfolio-wide facts such as cash, sector exposure, and market
        sessions.  ``positions`` only contains the requested subjects when a
        reasoning worker has already selected a bounded target set.  This is
        an input-shaping concern, not a materiality or investment decision.
        """
        reference_positions = [
            item
            for item in list(self.positions or []) + list(self.watchlist or [])
            if not item.is_cash()
        ]
        requested = {
            str(symbol or "").upper().strip()
            for symbol in target_symbols or []
            if str(symbol or "").strip()
        }
        position_symbols = {
            item.key()
            for item in reference_positions
            if item.key()
        }
        crypto_markets = self.external_signals.get("cryptoMarkets") if isinstance(self.external_signals, dict) else {}
        crypto_symbols = {
            str(item.get("symbol") or "").upper().strip()
            for item in crypto_markets.values()
            if isinstance(item, dict) and str(item.get("symbol") or "").strip().upper() in {"BTC", "ETH"}
        } if isinstance(crypto_markets, dict) else set()
        available = position_symbols | crypto_symbols
        if not requested:
            return {
                "mode": "full",
                "reason": "no-target-symbols",
                "positions": reference_positions,
                "referencePositions": reference_positions,
                "targetSymbols": [],
                "availableSymbols": sorted(available),
            }
        selected = [item for item in reference_positions if item.key() in requested]
        selected_symbols = {item.key() for item in selected if item.key()}
        selected_symbols.update(requested & crypto_symbols)
        if not selected_symbols:
            return {
                "mode": "empty",
                "reason": "target-symbols-not-in-snapshot",
                "positions": [],
                "referencePositions": reference_positions,
                "targetSymbols": sorted(requested),
                "availableSymbols": sorted(available),
            }
        if available and available.issubset(requested):
            return {
                "mode": "full",
                "reason": "target-set-covers-snapshot",
                "positions": reference_positions,
                "referencePositions": reference_positions,
                "targetSymbols": sorted(selected_symbols),
                "availableSymbols": sorted(available),
            }
        return {
            "mode": "target-scoped",
            "reason": "bounded-target-symbols",
            "positions": selected,
            "referencePositions": reference_positions,
            "targetSymbols": sorted(selected_symbols),
            "availableSymbols": sorted(available),
        }

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


def account_snapshot_from_monitor_state(state: Dict[str, object]) -> Optional[AccountSnapshot]:
    """Rehydrate one immutable monitoring read-model snapshot.

    The realtime monitor persists source facts before TypeDB projection.  A
    reasoning worker can safely rebuild its ABox from that verified state
    without issuing another provider collection cycle.  Keep this conversion
    in the domain layer so every reader preserves the same portfolio contract.
    """
    if not isinstance(state, dict) or not isinstance(state.get("portfolio"), dict):
        return None

    def from_mapping(cls, value: object):
        payload = copy.deepcopy(value) if isinstance(value, dict) else {}
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in payload.items() if key in allowed})

    def values_from_map(value: object):
        if isinstance(value, dict):
            return list(value.values())
        if isinstance(value, list):
            return list(value)
        return []

    portfolio = from_mapping(PortfolioSummary, state.get("portfolio"))
    positions = [
        from_mapping(Position, item)
        for item in values_from_map(state.get("positions"))
        if isinstance(item, dict)
    ]
    watchlist = [
        from_mapping(Position, item)
        for item in values_from_map(state.get("watchlist"))
        if isinstance(item, dict)
    ]
    metadata = copy.deepcopy(state.get("metadata") or {})
    if not portfolio.valuation_basis:
        portfolio.valuation_basis = "legacy-unknown"
        portfolio.account_equity_total = portfolio.total
        portfolio.mark_to_market_total = portfolio.total
        portfolio.valuation = {
            "version": "legacy-portfolio-valuation-v1",
            "displayBasis": "legacy-unknown",
            "migrationState": "source-fields-unavailable",
            "observedAt": str(state.get("generatedAt") or state.get("generated_at") or ""),
        }
        for position in positions:
            position.mark_to_market_value = position.mark_to_market_value or position.market_value
            position.mark_to_market_value_krw = position.mark_to_market_value_krw or position.market_value_krw
            position.account_value_krw = position.account_value_krw or position.market_value_krw
            position.account_value_basis = position.account_value_basis or "legacy-unknown"
        for position in watchlist:
            position.mark_to_market_value = position.mark_to_market_value or position.market_value
            position.mark_to_market_value_krw = position.mark_to_market_value_krw or position.market_value_krw
        metadata["valuationCompatibility"] = {
            "state": "legacy-unknown",
            "reason": "historical snapshot did not preserve broker gross/net fields",
            "recollectRequired": True,
        }
    return AccountSnapshot(
        account_id=str(state.get("accountId") or state.get("account_id") or "portfolio"),
        account_label=str(state.get("accountLabel") or state.get("account_label") or "투자 계좌"),
        provider=str(state.get("provider") or ""),
        mode=str(state.get("mode") or ""),
        status=str(state.get("status") or ""),
        generated_at=str(state.get("generatedAt") or state.get("generated_at") or ""),
        portfolio=portfolio,
        positions=positions,
        decisions=[
            from_mapping(DecisionItem, item)
            for item in values_from_map(state.get("decisions"))
            if isinstance(item, dict)
        ],
        external_signals=copy.deepcopy(state.get("externalSignals") or {}),
        watchlist=watchlist,
        metadata=metadata,
    )


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
