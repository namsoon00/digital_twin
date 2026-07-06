from dataclasses import asdict
from typing import Callable, Dict, List

from ..domain.accounts import AccountConfig, split_symbols
from ..domain.market_data import (
    known_stock,
    normalize_position,
    number,
    sector_from_symbol,
)
from ..domain.ontology import ONTOLOGY_PROMPT_VERSION, build_portfolio_ontology
from ..domain.portfolio import PortfolioSummary, Position, utc_now_iso
from ..domain.portfolio_calculations import (
    normalized_fx_rates,
    value_in_base,
)
from ..domain.strategy import StrategyModel, holding_decision_label, holding_pressure_scores


def clamp_score(value: float) -> int:
    return max(0, min(100, round(float(value or 0))))


def position_payload(position: Position) -> Dict[str, object]:
    return {
        "symbol": position.symbol,
        "name": position.name,
        "market": position.market,
        "currency": position.currency,
        "quantity": str(position.quantity).rstrip("0").rstrip(".") if position.quantity else "",
        "sellableQuantity": str(position.sellable_quantity).rstrip("0").rstrip(".") if position.sellable_quantity else "",
        "averagePrice": position.average_price,
        "currentPrice": position.current_price,
        "changeRate": position.change_rate,
        "quoteSource": position.quote_source,
        "quoteStatus": position.quote_status,
        "quoteMessage": position.quote_message,
        "dataQuality": position.data_quality,
        "updatedAt": position.updated_at,
        "marketValue": position.market_value,
        "profitLoss": position.profit_loss,
        "profitLossRate": position.profit_loss_rate,
        "tradeStrength": position.trade_strength,
        "tradingValue": position.trading_value,
        "volume": position.volume,
        "volumeRatio": position.volume_ratio,
        "buyVolume": position.buy_volume,
        "sellVolume": position.sell_volume,
        "orderbookBidVolume": position.orderbook_bid_volume,
        "orderbookAskVolume": position.orderbook_ask_volume,
        "bidAskImbalance": position.bid_ask_imbalance,
        "foreignBuyVolume": position.foreign_buy_volume,
        "foreignSellVolume": position.foreign_sell_volume,
        "foreignNetVolume": position.foreign_net_volume,
        "institutionBuyVolume": position.institution_buy_volume,
        "institutionSellVolume": position.institution_sell_volume,
        "institutionNetVolume": position.institution_net_volume,
        "individualBuyVolume": position.individual_buy_volume,
        "individualSellVolume": position.individual_sell_volume,
        "individualNetVolume": position.individual_net_volume,
        "ma5": position.ma5,
        "ma20": position.ma20,
        "ma60": position.ma60,
        "ma120": position.ma120,
        "ma200": position.ma200,
        "ma20Slope": position.ma20_slope,
        "ma60Slope": position.ma60_slope,
        "ma20Distance": position.ma20_distance,
        "ma60Distance": position.ma60_distance,
        "sector": position.sector,
    }


def portfolio_payload(portfolio) -> Dict[str, object]:
    if isinstance(portfolio, dict):
        return dict(portfolio)
    return asdict(portfolio)


def summary_payload(summary) -> Dict[str, object]:
    return {
        "total": summary.total,
        "invested": summary.invested,
        "cash": summary.cash,
        "markets": summary.markets,
        "sectors": summary.sectors,
        "concentration": summary.concentration,
    }


def demo_toss_portfolio(reason: str = "", demo_positions_provider: Callable = None) -> Dict[str, object]:
    positions = demo_positions_provider() if demo_positions_provider else []
    return {
        "mode": "demo",
        "configured": False,
        "status": reason or "토스 credentials 미설정",
        "account": {
            "displayNumber": "demo",
            "type": "BROKERAGE",
            "orderableAmount": 1250000,
            "currency": "KRW",
        },
        "positions": [position_payload(item) for item in positions],
        "watchlistQuotes": [],
    }


def mask_account(value: str) -> str:
    text = str(value or "")
    return "****" + text[-4:] if text else "연결 계좌"


def toss_portfolio_for_account(
    account: AccountConfig,
    snapshot_builder: Callable[[AccountConfig], object],
    demo_positions_provider: Callable = None,
) -> Dict[str, object]:
    snapshot = snapshot_builder(account)
    if snapshot.mode != "live":
        payload = demo_toss_portfolio(snapshot.status, demo_positions_provider)
        if snapshot.positions:
            payload["positions"] = [position_payload(item) for item in snapshot.positions]
        if snapshot.watchlist:
            payload["watchlistQuotes"] = [position_payload(item) for item in snapshot.watchlist]
        payload["portfolio"] = portfolio_payload(snapshot.portfolio)
        return payload
    return {
        "mode": snapshot.mode,
        "configured": bool(account.client_id and account.client_secret),
        "status": snapshot.status,
        "account": {
            "displayNumber": mask_account(account.account_seq),
            "type": "BROKERAGE",
            "orderableAmount": snapshot.portfolio.cash,
            "currency": "KRW",
        },
        "portfolio": portfolio_payload(snapshot.portfolio),
        "positions": [position_payload(item) for item in snapshot.positions],
        "watchlistQuotes": [position_payload(item) for item in snapshot.watchlist],
    }


def is_cash_position(item: Dict[str, object]) -> bool:
    sector = str(item.get("sector") or sector_from_symbol(str(item.get("symbol") or item.get("name") or "")))
    return sector == "현금" or str(item.get("symbol") or "").upper() == "CASH"


def market_bucket_for_cash(currency: str) -> str:
    code = str(currency or "").upper()
    if code == "USD":
        return "US"
    if code == "KRW" or not code:
        return "KR"
    return "OTHER"


def market_bucket_for_position(item: Dict[str, object]) -> str:
    market = str(item.get("market") or "").upper()
    currency = str(item.get("currency") or "").upper()
    symbol = str(item.get("symbol") or "")
    if is_cash_position(item):
        return market_bucket_for_cash(currency)
    if market in {"KR", "KOSPI", "KOSDAQ"} or currency == "KRW" or (len(symbol) == 6 and symbol.isdigit()):
        return "KR"
    if market == "US" or currency == "USD":
        return "US"
    return "OTHER"


def empty_market_exposure(key: str) -> Dict[str, object]:
    labels = {"KR": "한국장", "US": "미국장", "OTHER": "기타"}
    return {"key": key, "label": labels.get(key, key), "invested": 0, "cash": 0, "total": 0, "cashRatio": 0}


def currency_for_item(item: Dict[str, object]) -> str:
    explicit = str(item.get("currency") or "").upper()
    if explicit:
        return explicit
    market = str(item.get("market") or "").upper()
    symbol = str(item.get("symbol") or "")
    if market == "US":
        return "USD"
    if market in {"KR", "KOSPI", "KOSDAQ"} or (len(symbol) == 6 and symbol.isdigit()):
        return "KRW"
    return str(known_stock(symbol).get("currency") or "KRW").upper()


def base_market_value(item: Dict[str, object], rates: Dict[str, float]) -> float:
    return max(0.0, value_in_base(number(item.get("marketValue")), currency_for_item(item), rates))


def build_toss_portfolio(
    positions: List[Dict[str, object]],
    account: Dict[str, object],
    fx_rates: Dict[str, float] = None,
) -> Dict[str, object]:
    rates = normalized_fx_rates(fx_rates)
    market_map: Dict[str, Dict[str, object]] = {}

    def exposure(key: str) -> Dict[str, object]:
        if key not in market_map:
            market_map[key] = empty_market_exposure(key)
        return market_map[key]

    cash_positions = [item for item in positions if is_cash_position(item)]
    cash_from_positions = 0.0
    for item in cash_positions:
        value = base_market_value(item, rates)
        exposure(market_bucket_for_position(item))["cash"] += value
        cash_from_positions += value

    account_cash = max(0.0, value_in_base(number(account.get("orderableAmount")), str(account.get("currency") or "KRW"), rates))
    if not cash_from_positions and account_cash:
        exposure(market_bucket_for_cash(str(account.get("currency") or "KRW")))["cash"] += account_cash
    cash = cash_from_positions or account_cash

    invested = 0.0
    sector_map: Dict[str, float] = {}
    if cash:
        sector_map["현금"] = cash
    for item in [position for position in positions if not is_cash_position(position)]:
        value = base_market_value(item, rates)
        invested += value
        exposure(market_bucket_for_position(item))["invested"] += value
        sector = str(item.get("sector") or sector_from_symbol(str(item.get("symbol") or item.get("name") or "")))
        sector_map[sector] = sector_map.get(sector, 0.0) + value

    total = invested + cash
    sectors = sorted(
        [
            {"sector": sector, "value": value, "ratio": round((value / total) * 100) if total else 0}
            for sector, value in sector_map.items()
        ],
        key=lambda item: float(item["value"]),
        reverse=True,
    )
    markets = []
    for key in ["KR", "US", "OTHER"]:
        item = exposure(key)
        item["total"] = item["invested"] + item["cash"]
        item["cashRatio"] = round((item["cash"] / item["total"]) * 100) if item["total"] else 0
        if item["total"]:
            markets.append(item)
    concentration = next((item["ratio"] for item in sectors if item["sector"] != "현금"), 0)
    return {
        "total": total,
        "invested": invested,
        "cash": cash,
        "markets": markets,
        "sectors": sectors,
        "concentration": concentration,
    }


def parse_watchlist(
    raw_value: str = "",
    fallback_symbols: str = "",
    enrich_symbol: Callable[[str], Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    raw = str(raw_value if raw_value is not None and raw_value != "" else fallback_symbols or "").strip()
    symbols = split_symbols(raw) if raw else ["TSLA", "AAPL", "NVDA", "000660"]
    unique = []
    for symbol in symbols:
        if symbol not in unique:
            unique.append(symbol)
    items = []
    for symbol in unique[:30]:
        try:
            info = enrich_symbol(symbol) if enrich_symbol else known_stock(symbol)
        except Exception:
            info = known_stock(symbol)
        items.append({
            "symbol": info["symbol"],
            "name": info["name"],
            "market": info["market"],
            "currency": info["currency"],
            "sector": info["sector"],
            "source": "watchlist",
            "configured": bool(raw),
        })
    return items


def build_toss_watchlist(
    positions: List[Dict[str, object]],
    watchlist_symbols: str = "",
    fallback_symbols: str = "",
    enrich_symbol: Callable[[str], Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    holding_symbols = {str(item.get("symbol") or "").upper() for item in positions}
    result = []
    for item in parse_watchlist(watchlist_symbols, fallback_symbols, enrich_symbol):
        if str(item.get("symbol") or "").upper() in holding_symbols:
            continue
        next_item = dict(item)
        result.append(next_item)
    return result


def merge_watchlist_quotes(watchlist: List[Dict[str, object]], quotes: List[Dict[str, object]]) -> List[Dict[str, object]]:
    quote_map = {
        str(item.get("symbol") or "").upper(): item
        for item in quotes
        if isinstance(item, dict) and item.get("symbol")
    }
    merged = []
    for item in watchlist:
        symbol = str(item.get("symbol") or "").upper()
        quote = quote_map.get(symbol) or {}
        next_item = dict(item)
        if quote:
            next_item.update({
                key: value
                for key, value in quote.items()
                if value not in (None, "")
            })
        next_item["quoteStatus"] = str(next_item.get("quoteStatus") or ("토스 prices 반영" if number(next_item.get("currentPrice")) else "시세 조회 대기"))
        if not number(next_item.get("currentPrice")):
            next_item["quoteMessage"] = next_item.get("quoteMessage") or "토스 prices 응답이 없거나 아직 해당 종목 시세를 받지 못했습니다."
        else:
            next_item["quoteMessage"] = next_item.get("quoteMessage") or "현재가는 토스 prices, 이동평균은 토스 candles 기준입니다."
        merged.append(next_item)
    return merged


def profit_loss_rate(item: Dict[str, object]) -> float:
    market_value = number(item.get("marketValue"))
    profit_loss = number(item.get("profitLoss"))
    cost_basis = market_value - profit_loss
    if not cost_basis:
        return 0.0
    return round((profit_loss / cost_basis) * 1000) / 10


def toss_decision_for_holding(item: Dict[str, object], portfolio: Dict[str, object], strategy_model: StrategyModel = None) -> Dict[str, object]:
    pnl_rate = number(item.get("profitLossRate")) if item.get("profitLossRate") is not None else profit_loss_rate(item)
    sector = str(item.get("sector") or sector_from_symbol(str(item.get("symbol") or item.get("name") or "")))
    sector_entry = next((entry for entry in portfolio.get("sectors", []) if entry.get("sector") == sector), {"ratio": 0})
    sellable = number(item.get("sellableQuantity") or item.get("quantity"))
    decision_position = Position(
        symbol=str(item.get("symbol") or ""),
        name=str(item.get("name") or ""),
        market=str(item.get("market") or ""),
        currency=str(item.get("currency") or ""),
        quantity=number(item.get("quantity")),
        sellable_quantity=sellable,
        current_price=number(item.get("currentPrice")),
        market_value=number(item.get("marketValue")),
        profit_loss=number(item.get("profitLoss")),
        profit_loss_rate=pnl_rate,
        trade_strength=number(item.get("tradeStrength") or item.get("executionStrength")),
        trading_value=number(item.get("tradingValue")),
        volume=number(item.get("volume")),
        volume_ratio=number(item.get("volumeRatio")),
        buy_volume=number(item.get("buyVolume")),
        sell_volume=number(item.get("sellVolume")),
        orderbook_bid_volume=number(item.get("orderbookBidVolume")),
        orderbook_ask_volume=number(item.get("orderbookAskVolume")),
        bid_ask_imbalance=number(item.get("bidAskImbalance")),
        foreign_buy_volume=number(item.get("foreignBuyVolume")),
        foreign_sell_volume=number(item.get("foreignSellVolume")),
        foreign_net_volume=number(item.get("foreignNetVolume")),
        institution_buy_volume=number(item.get("institutionBuyVolume")),
        institution_sell_volume=number(item.get("institutionSellVolume")),
        institution_net_volume=number(item.get("institutionNetVolume")),
        individual_buy_volume=number(item.get("individualBuyVolume")),
        individual_sell_volume=number(item.get("individualSellVolume")),
        individual_net_volume=number(item.get("individualNetVolume")),
        ma20=number(item.get("ma20")),
        ma60=number(item.get("ma60")),
        ma20_slope=number(item.get("ma20Slope")),
        ma60_slope=number(item.get("ma60Slope")),
        ma20_distance=number(item.get("ma20Distance")),
        ma60_distance=number(item.get("ma60Distance")),
        sector=sector,
    )
    pressure_scores = holding_pressure_scores(decision_position, number(sector_entry.get("ratio")), strategy_model)
    exit_pressure = clamp_score(pressure_scores.get("exitPressure"))
    label, tone = holding_decision_label(exit_pressure, pnl_rate)
    if exit_pressure >= 72:
        priority = 1
    elif exit_pressure >= 55:
        priority = 2
    elif exit_pressure >= 38:
        priority = 3
    else:
        priority = 4
    reasons = [
        "토스 잔고 기준 수익률이 " + ("+" if pnl_rate > 0 else "") + str(pnl_rate) + "%입니다.",
        "평가손익은 " + format(round(number(item.get("profitLoss"))), ",") + " " + str(item.get("currency") or "") + "입니다.",
    ]
    if number(sector_entry.get("ratio")) >= 35:
        reasons.append(sector + " 노출이 계좌의 " + str(sector_entry.get("ratio")) + "%입니다.")
    if sellable > 0:
        reasons.append("매도 가능 수량은 " + str(sellable).rstrip("0").rstrip(".") + "입니다.")
    return {
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "source": "holding",
        "sector": sector,
        "market": item.get("market") or "",
        "currency": item.get("currency") or "",
        "marketValue": number(item.get("marketValue")),
        "profitLoss": number(item.get("profitLoss")),
        "profitLossRate": pnl_rate,
        "exitPressure": exit_pressure,
        "profitTakePressure": clamp_score(pressure_scores.get("profitTakePressure")),
        "lossCutPressure": clamp_score(pressure_scores.get("lossCutPressure")),
        "decisionBasis": pressure_scores.get("decisionBasis"),
        "decision": label,
        "tone": tone,
        "priority": priority,
        "reasons": reasons[:3],
        "triggers": ["수익률", "평가손익", "매도 가능 수량"],
    }


def toss_decision_for_watch(item: Dict[str, object]) -> Dict[str, object]:
    return {
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "source": "watchlist",
        "sector": item.get("sector"),
        "market": item.get("market") or "",
        "currency": item.get("currency") or "",
        "marketValue": 0,
        "profitLoss": 0,
        "profitLossRate": 0,
        "exitPressure": 32,
        "decision": "시세 기준 대기",
        "tone": "hold",
        "priority": 5,
        "reasons": ["보유 종목이 아니므로 매도 판단 대신 토스 시세 기준을 기다립니다."],
        "triggers": ["관심 종목", "현재가", "기준가"],
    }


def build_toss_decision(
    toss: Dict[str, object],
    portfolio: Dict[str, object],
    watchlist: List[Dict[str, object]],
    strategy_model: StrategyModel = None,
) -> Dict[str, object]:
    positions = [
        item
        for item in toss.get("positions", [])
        if not is_cash_position(item) and number(item.get("marketValue")) > 0
    ]
    holding_items = [toss_decision_for_holding(item, portfolio, strategy_model) for item in positions]
    ontology = build_toss_ontology(positions, portfolio, holding_items)
    for item in holding_items:
        opinion = ontology.opinion_for_symbol(str(item.get("symbol") or ""))
        if not opinion:
            continue
        opinion_payload = opinion.to_dict()
        item["ontologyOpinion"] = opinion_payload
        item["ontologyWorldview"] = dict(ontology.worldview or {})
        item["aiContext"] = {
            "promptVersion": ONTOLOGY_PROMPT_VERSION,
            "role": "ontology-first-investment-opinion",
            "legacyModelRole": "supporting-evidence",
            "worldview": dict(ontology.worldview or {}),
            "opinion": opinion_payload,
            "prompt": ontology.prompt,
        }
    watch_items = [toss_decision_for_watch(item) for item in watchlist]
    items = sorted(holding_items + watch_items, key=lambda item: (item.get("priority", 9), -number(item.get("exitPressure"))))
    urgent_count = len([item for item in holding_items if item.get("tone") in {"danger", "caution"}])
    top_items = items[:3]
    overall_pressure = round(sum(number(item.get("exitPressure")) for item in top_items) / len(top_items)) if top_items else 0
    return {
        "headline": (
            "내 토스 계좌 기준으로 " + str(items[0].get("name")) + " " + str(items[0].get("decision")) + "이 우선입니다."
            if items
            else "토스 잔고에서 점검할 종목이 아직 없습니다."
        ),
        "overallPressure": overall_pressure,
        "urgentCount": urgent_count,
        "holdingCount": len(holding_items),
        "watchCount": len(watch_items),
        "items": items,
        "rules": [
            "투자전략은 온톨로지 관계 그래프를 우선하고 기존 공식 점수는 보조 evidence로 유지합니다.",
            "수익률, 평가손익, 수급, 추세, 섹터 집중도를 AI 의견 컨텍스트에 함께 넣습니다.",
            "관심 종목은 보유가 아니므로 매도 판단 대신 시세 기준 대기 상태로 둡니다.",
            "Neo4j 설정이 있으면 같은 관계 그래프를 저장합니다.",
        ],
        "ontologyStrategy": {
            "promptVersion": ONTOLOGY_PROMPT_VERSION,
            "tbox": ontology.to_dict().get("tbox", {}),
            "abox": ontology.to_dict().get("abox", {}),
            "worldview": dict(ontology.worldview or {}),
            "entityCount": len(ontology.entities),
            "relationCount": len(ontology.relations),
            "evidenceCount": len(ontology.evidence),
            "entities": [item.to_dict() for item in ontology.entities[:80]],
            "relations": [item.to_dict() for item in ontology.relations[:120]],
            "evidence": [item.to_dict() for item in ontology.evidence[:80]],
            "beliefs": [item.to_dict() for item in ontology.beliefs[:80]],
            "opinions": [item.to_dict() for item in ontology.opinions[:40]],
            "prompt": ontology.prompt,
        },
    }


def build_toss_ontology(
    positions: List[Dict[str, object]],
    portfolio: Dict[str, object],
    holding_items: List[Dict[str, object]],
):
    normalized_positions = [normalize_position(item) for item in positions]
    summary = PortfolioSummary(
        total=number(portfolio.get("total")),
        invested=number(portfolio.get("invested")),
        cash=number(portfolio.get("cash")),
        markets=list(portfolio.get("markets") or []),
        sectors=list(portfolio.get("sectors") or []),
        concentration=number(portfolio.get("concentration")),
    )
    legacy_by_symbol = {
        str(item.get("symbol") or "").upper(): {
            "exitPressure": number(item.get("exitPressure")),
            "profitTakePressure": number(item.get("profitTakePressure")),
            "lossCutPressure": number(item.get("lossCutPressure")),
            "decisionBasis": str(item.get("decisionBasis") or ""),
        }
        for item in holding_items
    }
    return build_portfolio_ontology(
        normalized_positions,
        summary,
        legacy_by_symbol=legacy_by_symbol,
        portfolio_id="flow-lens",
    )


def build_toss_lens_snapshot(
    toss: Dict[str, object],
    mock: bool = False,
    watchlist_symbols: str = "",
    fallback_watchlist_symbols: str = "",
    fx_rates: Dict[str, float] = None,
    enrich_symbol: Callable[[str], Dict[str, object]] = None,
    strategy_model: StrategyModel = None,
) -> Dict[str, object]:
    positions = list(toss.get("positions") or [])
    portfolio = dict(toss.get("portfolio") or {}) or build_toss_portfolio(positions, dict(toss.get("account") or {}), fx_rates)
    watchlist = merge_watchlist_quotes(
        build_toss_watchlist(positions, watchlist_symbols, fallback_watchlist_symbols, enrich_symbol),
        list(toss.get("watchlistQuotes") or []),
    )
    toss["watchlist"] = watchlist
    toss_decision = build_toss_decision(toss, portfolio, watchlist, strategy_model)
    return {
        "generatedAt": utc_now_iso(),
        "dataMode": "mock" if mock else "live",
        "mock": bool(mock),
        "headline": toss_decision["headline"],
        "exitScore": toss_decision["overallPressure"],
        "regime": "토스 조회 전용",
        "summary": [
            "보유 종목 " + str(toss_decision["holdingCount"]) + "개와 관심 종목 " + str(toss_decision["watchCount"]) + "개를 분리했습니다.",
            "외부 텍스트 신호는 판단에서 제외했습니다.",
            (
                "가장 큰 계좌 노출은 "
                + str(portfolio["sectors"][0]["sector"])
                + " "
                + str(portfolio["sectors"][0]["ratio"])
                + "%입니다."
                if portfolio["sectors"]
                else "계좌 보유 종목은 아직 비어 있습니다."
            ),
        ],
        "toss": toss,
        "portfolio": portfolio,
        "tossDecision": toss_decision,
        "checklist": [
            {"label": "토스 잔고의 수익률, 평가손익, 매도 가능 수량 확인", "status": "주의" if toss_decision["urgentCount"] else "정상"},
            {"label": "관심 종목은 토스 시세 연결 후 현재가 기준만 비교", "status": "대기" if watchlist else "정상"},
            {"label": "주문 실행은 읽기 전용 검증 이후 별도 단계에서만 열기", "status": "잠금"},
        ],
    }


class FlowLensService:
    def __init__(
        self,
        account_repository,
        snapshot_builder: Callable[[AccountConfig], object],
        demo_positions_provider: Callable = None,
        settings_provider: Callable[[], Dict[str, str]] = None,
        fx_rates_provider: Callable[[Dict[str, str]], Dict[str, float]] = None,
        symbol_enricher: Callable[[str], Dict[str, object]] = None,
    ):
        self.account_repository = account_repository
        self.snapshot_builder = snapshot_builder
        self.demo_positions_provider = demo_positions_provider
        self.settings_provider = settings_provider or (lambda: {})
        self.fx_rates_provider = fx_rates_provider or (lambda settings: {})
        self.symbol_enricher = symbol_enricher

    def snapshot(self, mock: bool = False, watchlist_symbols: str = "") -> Dict[str, object]:
        settings = dict(self.settings_provider() or {})
        rates = self.fx_rates_provider(settings)
        strategy_model = StrategyModel(settings)
        if mock:
            toss = demo_toss_portfolio("웹 mock 데이터", self.demo_positions_provider)
            toss["mode"] = "mock"
            toss["status"] = "웹 mock 데이터"
            return build_toss_lens_snapshot(
                toss,
                mock=True,
                watchlist_symbols=watchlist_symbols,
                fallback_watchlist_symbols=settings.get("watchlistSymbols", ""),
                fx_rates=rates,
                enrich_symbol=self.symbol_enricher,
                strategy_model=strategy_model,
            )
        account = self.account_repository.load()[0]
        toss = toss_portfolio_for_account(account, self.snapshot_builder, self.demo_positions_provider)
        selected_watchlist = watchlist_symbols or ",".join(account.watchlist_symbols)
        return build_toss_lens_snapshot(
            toss,
            mock=False,
            watchlist_symbols=selected_watchlist,
            fallback_watchlist_symbols=settings.get("watchlistSymbols", ""),
            fx_rates=rates,
            enrich_symbol=self.symbol_enricher,
            strategy_model=strategy_model,
        )


def flow_lens_snapshot(service: FlowLensService, mock: bool = False, watchlist_symbols: str = "") -> Dict[str, object]:
    return service.snapshot(mock=mock, watchlist_symbols=watchlist_symbols)
