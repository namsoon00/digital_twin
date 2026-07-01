import hashlib
import math
from datetime import date, timedelta
from typing import Dict, List

from ..domain.analytics import known_stock
from ..domain.portfolio import utc_now_iso


MOCK_MARKET_UNIVERSE = {
    "AAPL": {"name": "Apple", "market": "US", "currency": "USD", "sector": "AI/플랫폼", "anchorPrice": 210, "baseVolume": 56000000, "beta": 0.9},
    "MSFT": {"name": "Microsoft", "market": "US", "currency": "USD", "sector": "AI/플랫폼", "anchorPrice": 495, "baseVolume": 24000000, "beta": 0.8},
    "NVDA": {"name": "NVIDIA", "market": "US", "currency": "USD", "sector": "반도체", "anchorPrice": 158, "baseVolume": 225000000, "beta": 1.45},
    "AMD": {"name": "AMD", "market": "US", "currency": "USD", "sector": "반도체", "anchorPrice": 165, "baseVolume": 61000000, "beta": 1.35},
    "TSLA": {"name": "Tesla", "market": "US", "currency": "USD", "sector": "모빌리티", "anchorPrice": 330, "baseVolume": 95000000, "beta": 1.55},
    "GOOGL": {"name": "Alphabet", "market": "US", "currency": "USD", "sector": "AI/플랫폼", "anchorPrice": 185, "baseVolume": 31000000, "beta": 0.95},
    "META": {"name": "Meta", "market": "US", "currency": "USD", "sector": "AI/플랫폼", "anchorPrice": 680, "baseVolume": 18000000, "beta": 1.05},
    "005930": {"name": "삼성전자", "market": "KR", "currency": "KRW", "sector": "반도체", "anchorPrice": 72000, "baseVolume": 16000000, "beta": 1.05},
    "000660": {"name": "SK하이닉스", "market": "KR", "currency": "KRW", "sector": "반도체", "anchorPrice": 260000, "baseVolume": 4200000, "beta": 1.35},
}


MOCK_MARKET_SCENARIOS = {
    "recent-one-year": {
        "label": "최근 1년 기준",
        "description": "최근 1년 가격 레벨을 기준으로 완만한 상승, 순환 조정, 거래량 회복을 섞은 기본 학습 데이터",
        "targetPeriod": "rolling-one-year",
        "drift": 0.18,
        "volatility": 0.018,
        "volumeMultiplier": 1.0,
        "semiconductorTilt": 0.28,
        "events": [
            {"offset": 38, "label": "금리 경로 재가격", "impact": -0.06},
            {"offset": 116, "label": "실적 시즌 상향", "impact": 0.08},
            {"offset": 188, "label": "AI 설비 투자 확인", "impact": 0.1},
        ],
    },
    "covid-crash": {
        "label": "코로나 급락/회복",
        "description": "2020년 코로나 충격처럼 급락, 거래량 폭증, 정책 대응 후 V자 회복이 나타나는 국면",
        "start": "2020-02-03",
        "end": "2021-02-02",
        "drift": 0.1,
        "volatility": 0.03,
        "volumeMultiplier": 1.45,
        "shock": {"center": 36, "width": 16, "depth": -0.38},
        "rebound": {"center": 86, "width": 42, "strength": 0.5},
        "events": [
            {"offset": 28, "label": "팬데믹 공포 확산", "impact": -0.18},
            {"offset": 52, "label": "유동성 공급", "impact": 0.12},
            {"offset": 118, "label": "언택트/기술주 주도", "impact": 0.16},
        ],
    },
    "financial-crisis": {
        "label": "금융위기",
        "description": "2008년 금융위기처럼 신용 경색, 장기 하락, 높은 변동성, 느린 회복이 이어지는 국면",
        "start": "2008-09-02",
        "end": "2009-09-01",
        "drift": -0.12,
        "volatility": 0.026,
        "volumeMultiplier": 1.35,
        "shock": {"center": 62, "width": 46, "depth": -0.46},
        "rebound": {"center": 170, "width": 60, "strength": 0.24},
        "events": [
            {"offset": 18, "label": "신용 경색 심화", "impact": -0.12},
            {"offset": 66, "label": "강제 매도/마진콜", "impact": -0.18},
            {"offset": 152, "label": "정책 안정화 기대", "impact": 0.08},
        ],
    },
    "semiconductor-boom": {
        "label": "반도체 호황",
        "description": "AI/메모리 사이클 개선처럼 반도체가 시장을 주도하고 거래량과 상대강도가 커지는 국면",
        "start": "2023-10-02",
        "end": "2024-10-01",
        "drift": 0.22,
        "volatility": 0.021,
        "volumeMultiplier": 1.25,
        "semiconductorTilt": 0.72,
        "events": [
            {"offset": 44, "label": "AI 가속기 수요 상향", "impact": 0.12},
            {"offset": 104, "label": "HBM 공급 부족", "impact": 0.15},
            {"offset": 176, "label": "차익 실현 조정", "impact": -0.08},
        ],
    },
    "rate-shock": {
        "label": "금리 충격",
        "description": "금리 급등과 밸류에이션 압축으로 성장주가 흔들리는 고금리 스트레스 국면",
        "start": "2022-01-03",
        "end": "2023-01-03",
        "drift": -0.18,
        "volatility": 0.024,
        "volumeMultiplier": 1.18,
        "shock": {"center": 92, "width": 70, "depth": -0.28},
        "events": [
            {"offset": 32, "label": "인플레이션 서프라이즈", "impact": -0.08},
            {"offset": 96, "label": "긴축 가속", "impact": -0.12},
            {"offset": 188, "label": "금리 정점 기대", "impact": 0.09},
        ],
    },
}


def clamp_number(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def business_days_between(start: str, end: str) -> List[str]:
    days = []
    cursor = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    while cursor <= end_date:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def rolling_one_year_range(reference_date: str = "") -> Dict[str, str]:
    try:
        end_date = date.fromisoformat(str(reference_date or ""))
    except ValueError:
        end_date = date.today()
    return {"start": (end_date - timedelta(days=365)).isoformat(), "end": end_date.isoformat()}


def hash_seed(value: str) -> int:
    digest = hashlib.sha256(str(value or "mock").encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") or 1


def seeded_random(seed_input: str):
    seed = hash_seed(seed_input)

    def random() -> float:
        nonlocal seed
        seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
        return seed / 4294967296

    return random


def random_normal(random) -> float:
    u1 = max(random(), 1e-9)
    u2 = max(random(), 1e-9)
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)


def scenario_pulse(index: int, center: float, width: float, strength: float) -> float:
    distance = (index - center) / max(width, 1)
    return strength * math.exp(-0.5 * distance * distance)


def moving_average(candles: List[Dict[str, object]], field: str, index: int, window_size: int) -> float:
    window = candles[max(0, index - window_size + 1): index + 1]
    return sum(float(item.get(field) or 0) for item in window) / len(window) if window else 0


def round_market(value: float, currency: str):
    return round(value) if currency == "KRW" else round(value * 100) / 100


def scenario_by_id(scenario_id: str) -> Dict[str, object]:
    return MOCK_MARKET_SCENARIOS.get(str(scenario_id or ""), MOCK_MARKET_SCENARIOS["recent-one-year"])


def scenario_range(scenario: Dict[str, object], query: Dict[str, object]) -> Dict[str, str]:
    if scenario.get("targetPeriod") == "rolling-one-year":
        return rolling_one_year_range(str(query.get("asOf") or ""))
    return {"start": str(scenario.get("start")), "end": str(scenario.get("end"))}


def stock_info(symbol: str) -> Dict[str, object]:
    normalized = str(symbol or "").strip().upper()
    info = dict(known_stock(normalized))
    info.update(MOCK_MARKET_UNIVERSE.get(normalized, {"anchorPrice": 100, "baseVolume": 10000000, "beta": 1}))
    return info


def generate_mock_candles(symbol: str, scenario_id: str, query: Dict[str, object]) -> Dict[str, object]:
    scenario = scenario_by_id(scenario_id)
    info = stock_info(symbol)
    date_range = scenario_range(scenario, query or {})
    dates = business_days_between(date_range["start"], date_range["end"])[-252:]
    random = seeded_random(":".join([str(scenario_id), str(info.get("symbol")), str((query or {}).get("seed") or "")]))
    semiconductor_boost = float(scenario.get("semiconductorTilt") or 0) if info.get("sector") == "반도체" else 0
    beta = float(info.get("beta") or 1)
    daily_drift = (float(scenario.get("drift") or 0) + semiconductor_boost) / max(len(dates), 1)
    volatility = float(scenario.get("volatility") or 0.018) * beta
    close = float(info.get("anchorPrice") or 100)
    candles = []

    for index, day in enumerate(dates):
        event_impact = sum(
            scenario_pulse(index, float(event.get("offset") or 0), 4, float(event.get("impact") or 0))
            for event in scenario.get("events", [])
        )
        shock = scenario_pulse(index, scenario["shock"]["center"], scenario["shock"]["width"], scenario["shock"]["depth"]) if scenario.get("shock") else 0
        rebound = scenario_pulse(index, scenario["rebound"]["center"], scenario["rebound"]["width"], scenario["rebound"]["strength"]) if scenario.get("rebound") else 0
        noise = random_normal(random) * volatility
        daily_return = clamp_number(daily_drift + noise + event_impact / 18 + shock / 28 + rebound / 36, -0.18, 0.18)
        open_price = close * (1 + random_normal(random) * volatility * 0.28)
        close = max(1, close * (1 + daily_return))
        intraday = abs(random_normal(random)) * volatility * 1.8 + abs(daily_return) * 0.35
        high = max(open_price, close) * (1 + intraday)
        low = max(0.01, min(open_price, close) * (1 - intraday))
        stress = abs(shock) + abs(event_impact) + max(0, rebound)
        relative_volume = clamp_number(float(scenario.get("volumeMultiplier") or 1) + abs(daily_return) * 12 + stress * 3 + random() * 0.35, 0.35, 6)
        volume = round(float(info.get("baseVolume") or 10000000) * relative_volume)
        buy_share = clamp_number(50 + daily_return * 180 + rebound * 20 - abs(shock) * 18 + random_normal(random) * 6, 12, 88)
        trade_strength = clamp_number(100 + (buy_share - 50) * 1.9 + daily_return * 90, 35, 185)
        buy_volume = round(volume * (buy_share / 100))
        sell_volume = max(0, volume - buy_volume)
        bid_ask_imbalance = clamp_number((buy_share - 50) * 1.4 + random_normal(random) * 3, -45, 45)
        active_events = [
            str(event.get("label"))
            for event in scenario.get("events", [])
            if abs(index - int(event.get("offset") or 0)) <= 4
        ]
        candles.append({
            "date": day,
            "open": round_market(open_price, str(info.get("currency") or "")),
            "high": round_market(high, str(info.get("currency") or "")),
            "low": round_market(low, str(info.get("currency") or "")),
            "close": round_market(close, str(info.get("currency") or "")),
            "volume": volume,
            "changePercent": round(daily_return * 10000) / 100,
            "relativeVolume": round(relative_volume * 100) / 100,
            "tradeStrength": round(trade_strength),
            "buyVolume": buy_volume,
            "sellVolume": sell_volume,
            "bidAskImbalance": round(bid_ask_imbalance * 10) / 10,
            "eventTags": active_events,
        })

    for index, candle in enumerate(candles):
        candle["ma20"] = round_market(moving_average(candles, "close", index, 20), str(info.get("currency") or ""))
        candle["ma60"] = round_market(moving_average(candles, "close", index, 60), str(info.get("currency") or ""))
        candle["volumeMa20"] = round(moving_average(candles, "volume", index, 20))

    return {"info": info, "range": date_range, "candles": candles}


def latest_mock_signal(symbol: str, generated: Dict[str, object]) -> Dict[str, object]:
    candles = generated.get("candles") or []
    last = candles[-1] if candles else {}
    previous = candles[-2] if len(candles) > 1 else last
    previous_close = float(previous.get("close") or 0)
    price_change_rate = ((float(last.get("close") or 0) / previous_close) - 1) * 100 if previous_close else 0
    return {
        "symbol": symbol,
        "tradeStrength": last.get("tradeStrength") or 0,
        "volumeRatio": last.get("relativeVolume") or 0,
        "buyVolume": last.get("buyVolume") or 0,
        "sellVolume": last.get("sellVolume") or 0,
        "bidAskImbalance": last.get("bidAskImbalance") or 0,
        "priceChangeRate": round(price_change_rate * 10) / 10,
        "close": last.get("close") or 0,
        "ma20": last.get("ma20") or 0,
        "ma60": last.get("ma60") or 0,
        "asOf": last.get("date") or "",
    }


def mock_market_payload(query: Dict[str, object]) -> Dict[str, object]:
    scenario_id = str(query.get("scenario") or query.get("regime") or "recent-one-year").strip()
    scenario = scenario_by_id(scenario_id)
    symbols = []
    for symbol in str(query.get("symbols") or "NVDA,AAPL,005930,000660,TSLA").split(","):
        normalized = symbol.strip().upper()
        if normalized and normalized not in symbols:
            symbols.append(normalized)
    symbols = symbols[:12]
    series = {}
    signals = []
    for symbol in symbols:
        generated = generate_mock_candles(symbol, scenario_id, query)
        info = generated["info"]
        series[symbol] = {
            "symbol": symbol,
            "name": info.get("name"),
            "market": info.get("market"),
            "currency": info.get("currency"),
            "sector": info.get("sector"),
            "range": generated["range"],
            "candles": generated["candles"],
        }
        signals.append(latest_mock_signal(symbol, generated))
    selected_id = scenario_id if scenario_id in MOCK_MARKET_SCENARIOS else "recent-one-year"
    return {
        "schemaVersion": 1,
        "dataQuality": "mock-synthetic",
        "provider": "Digiter Twin mock market API",
        "generatedAt": utc_now_iso(),
        "scenario": {
            "id": selected_id,
            "label": scenario.get("label"),
            "description": scenario.get("description"),
            "targetPeriod": scenario.get("targetPeriod") or "/".join([str(scenario.get("start")), str(scenario.get("end"))]),
            "events": scenario.get("events") or [],
        },
        "request": {
            "symbols": symbols,
            "seed": query.get("seed") or "",
            "asOf": query.get("asOf") or "",
        },
        "series": series,
        "signals": signals,
    }


def mock_market_scenario_list() -> Dict[str, object]:
    return {
        "schemaVersion": 1,
        "scenarios": [
            {
                "id": scenario_id,
                "label": scenario.get("label"),
                "description": scenario.get("description"),
                "targetPeriod": scenario.get("targetPeriod") or "/".join([str(scenario.get("start")), str(scenario.get("end"))]),
                "events": scenario.get("events") or [],
            }
            for scenario_id, scenario in MOCK_MARKET_SCENARIOS.items()
        ],
        "defaultSymbols": list(MOCK_MARKET_UNIVERSE.keys()),
    }
