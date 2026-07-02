from typing import Dict, List

from ..domain.accounts import AccountConfig, split_symbols
from ..domain.analytics import (
    known_stock,
    number,
    sector_from_symbol,
    value_in_base,
)
from ..domain.portfolio import Position, utc_now_iso
from ..infrastructure.settings import currency_rates, runtime_settings
from ..infrastructure.sqlite_accounts import AccountRegistry
from ..infrastructure.toss_snapshots import build_snapshot, demo_positions
from .symbol_universe_service import SymbolUniverseService


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
        "marketValue": position.market_value,
        "profitLoss": position.profit_loss,
        "profitLossRate": position.profit_loss_rate,
        "tradeStrength": position.trade_strength,
        "tradingValue": position.trading_value,
        "volume": position.volume,
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


def summary_payload(summary) -> Dict[str, object]:
    return {
        "total": summary.total,
        "invested": summary.invested,
        "cash": summary.cash,
        "markets": summary.markets,
        "sectors": summary.sectors,
        "concentration": summary.concentration,
    }


def demo_toss_portfolio(reason: str = "") -> Dict[str, object]:
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
        "positions": [position_payload(item) for item in demo_positions()],
    }


def mask_account(value: str) -> str:
    text = str(value or "")
    return "****" + text[-4:] if text else "연결 계좌"


def toss_portfolio_for_account(account: AccountConfig) -> Dict[str, object]:
    snapshot = build_snapshot(account)
    if snapshot.mode != "live":
        return demo_toss_portfolio(snapshot.status)
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
        "positions": [position_payload(item) for item in snapshot.positions],
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


def build_toss_portfolio(positions: List[Dict[str, object]], account: Dict[str, object]) -> Dict[str, object]:
    rates = currency_rates()
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


def parse_watchlist(raw_value: str = "") -> List[Dict[str, object]]:
    settings = runtime_settings()
    raw = str(raw_value if raw_value is not None and raw_value != "" else settings.get("watchlistSymbols") or "").strip()
    symbols = split_symbols(raw) if raw else ["TSLA", "AAPL", "NVDA", "000660"]
    unique = []
    for symbol in symbols:
        if symbol not in unique:
            unique.append(symbol)
    items = []
    universe = SymbolUniverseService()
    for symbol in unique[:30]:
        try:
            info = universe.enrich(symbol)
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


def build_toss_watchlist(positions: List[Dict[str, object]], watchlist_symbols: str = "") -> List[Dict[str, object]]:
    holding_symbols = {str(item.get("symbol") or "").upper() for item in positions}
    result = []
    for item in parse_watchlist(watchlist_symbols):
        if str(item.get("symbol") or "").upper() in holding_symbols:
            continue
        next_item = dict(item)
        next_item["quoteStatus"] = "시세 조회 대기"
        result.append(next_item)
    return result


def profit_loss_rate(item: Dict[str, object]) -> float:
    market_value = number(item.get("marketValue"))
    profit_loss = number(item.get("profitLoss"))
    cost_basis = market_value - profit_loss
    if not cost_basis:
        return 0.0
    return round((profit_loss / cost_basis) * 1000) / 10


def toss_decision_for_holding(item: Dict[str, object], portfolio: Dict[str, object]) -> Dict[str, object]:
    pnl_rate = number(item.get("profitLossRate")) if item.get("profitLossRate") is not None else profit_loss_rate(item)
    sector = str(item.get("sector") or sector_from_symbol(str(item.get("symbol") or item.get("name") or "")))
    sector_entry = next((entry for entry in portfolio.get("sectors", []) if entry.get("sector") == sector), {"ratio": 0})
    sellable = number(item.get("sellableQuantity") or item.get("quantity"))
    score = 24.0
    if pnl_rate >= 20:
        score += 40
    elif pnl_rate >= 10:
        score += 28
    elif pnl_rate >= 5:
        score += 15
    elif pnl_rate <= -15:
        score += 38
    elif pnl_rate <= -8:
        score += 24
    if number(sector_entry.get("ratio")) >= 50:
        score += 12
    elif number(sector_entry.get("ratio")) >= 35:
        score += 6
    if sellable > 0:
        score += 4
    exit_pressure = clamp_score(score)
    if exit_pressure >= 72:
        label, tone, priority = ("손절 기준 확인", "danger", 1) if pnl_rate <= -8 else ("분할 매도 기준 확인", "danger", 1)
    elif exit_pressure >= 55:
        label, tone, priority = "일부 익절 기준 확인", "caution", 2
    elif exit_pressure >= 38:
        label, tone, priority = "조건부 보유", "hold", 3
    else:
        label, tone, priority = "보유 유지", "watch", 4
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


def build_toss_decision(toss: Dict[str, object], portfolio: Dict[str, object], watchlist: List[Dict[str, object]]) -> Dict[str, object]:
    positions = [
        item
        for item in toss.get("positions", [])
        if not is_cash_position(item) and number(item.get("marketValue")) > 0
    ]
    holding_items = [toss_decision_for_holding(item, portfolio) for item in positions]
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
            "수익률과 평가손익은 토스 잔고에서 확인 가능한 값만 사용합니다.",
            "관심 종목은 보유가 아니므로 매도 판단 대신 시세 기준 대기 상태로 둡니다.",
            "외부 텍스트 신호는 토스 전용 판단 점수에 반영하지 않습니다.",
        ],
    }


def build_toss_lens_snapshot(toss: Dict[str, object], mock: bool = False, watchlist_symbols: str = "") -> Dict[str, object]:
    positions = list(toss.get("positions") or [])
    portfolio = build_toss_portfolio(positions, dict(toss.get("account") or {}))
    watchlist = build_toss_watchlist(positions, watchlist_symbols)
    toss["watchlist"] = watchlist
    toss_decision = build_toss_decision(toss, portfolio, watchlist)
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


def flow_lens_snapshot(mock: bool = False, watchlist_symbols: str = "") -> Dict[str, object]:
    if mock:
        toss = demo_toss_portfolio("웹 mock 데이터")
        toss["mode"] = "mock"
        toss["status"] = "웹 mock 데이터"
        return build_toss_lens_snapshot(toss, mock=True, watchlist_symbols=watchlist_symbols)
    account = AccountRegistry().load()[0]
    toss = toss_portfolio_for_account(account)
    selected_watchlist = watchlist_symbols or ",".join(account.watchlist_symbols)
    return build_toss_lens_snapshot(toss, mock=False, watchlist_symbols=selected_watchlist)
