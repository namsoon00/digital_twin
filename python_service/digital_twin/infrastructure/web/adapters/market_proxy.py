"""Web market proxy boundary."""

from digital_twin.infrastructure.external_signal_utils import external_call_target
from digital_twin.infrastructure.external_signal_utils import guarded_external_call
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
from typing import Dict
from typing import List
import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request


WEB_PROXY_API_GUARD_STATE: Dict[str, object] = {}


def parse_number(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def fetch_text(target_url: str, timeout: int = 8, headers: Dict[str, str] = None) -> str:
    def fetch() -> str:
        request = urllib.request.Request(
            target_url,
            headers={"User-Agent": "OrbitAlpha/0.1", **(headers or {})},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")

    return guarded_external_call(
        runtime_settings(),
        web_proxy_source_for_url(target_url),
        external_call_target(target_url),
        fetch,
        state=WEB_PROXY_API_GUARD_STATE,
        rate_limit_seconds=0,
    )


def fetch_json_url(target_url: str, timeout: int = 8, headers: Dict[str, str] = None):
    return json.loads(fetch_text(target_url, timeout=timeout, headers={"Accept": "application/json", **(headers or {})}))


def web_proxy_source_for_url(target_url: str) -> str:
    host = urllib.parse.urlparse(str(target_url or "")).netloc.lower()
    if "stlouisfed.org" in host:
        return "FRED"
    if "opendart.fss.or.kr" in host:
        return "OpenDART"
    if "m.stock.naver.com" in host:
        return "Naver Finance"
    if "stooq.com" in host:
        return "Stooq"
    return "Web Proxy"


def normalize_fred_observations_url(query: Dict[str, List[str]]) -> str:
    series_id = configured(first_query(query, "series_id")).upper()
    api_key = configured(first_query(query, "api_key"))
    limit = configured(first_query(query, "limit") or "1")
    sort_order = configured(first_query(query, "sort_order") or "desc").lower()
    if not re.match(r"^[A-Z0-9_.-]{1,40}$", series_id):
        raise ValueError("FRED series_id 형식이 올바르지 않습니다.")
    if not re.match(r"^[A-Za-z0-9]{16,64}$", api_key):
        raise ValueError("FRED_API_KEY 형식이 올바르지 않습니다.")
    if not re.match(r"^\d{1,4}$", limit):
        raise ValueError("FRED limit 형식이 올바르지 않습니다.")
    if sort_order not in {"asc", "desc"}:
        raise ValueError("FRED sort_order는 asc 또는 desc만 가능합니다.")
    params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": limit,
        "sort_order": sort_order,
    })
    return "https://api.stlouisfed.org/fred/series/observations?" + params


def normalize_opendart_company_url(query: Dict[str, List[str]]) -> str:
    api_key = configured(first_query(query, "crtfc_key"))
    corp_code = configured(first_query(query, "corp_code") or "00126380")
    if not re.match(r"^[A-Za-z0-9]{32,64}$", api_key):
        raise ValueError("OpenDART API key 형식이 올바르지 않습니다.")
    if not re.match(r"^\d{8}$", corp_code):
        raise ValueError("OpenDART corp_code 형식이 올바르지 않습니다.")
    return "https://opendart.fss.or.kr/api/company.json?" + urllib.parse.urlencode({
        "crtfc_key": api_key,
        "corp_code": corp_code,
    })


def stock_input_to_naver_code(symbol: str) -> str:
    match = re.match(r"^(\d{6})(?:\.(KS|KQ|KR))?$", configured(symbol).upper())
    return match.group(1) if match else ""


def stock_input_to_stooq_symbol(symbol: str) -> str:
    cleaned = configured(symbol).upper()
    if not cleaned or stock_input_to_naver_code(cleaned):
        return ""
    return cleaned if "." in cleaned else cleaned + ".US"


def fetch_naver_quote(symbol: str) -> Dict[str, object]:
    code = stock_input_to_naver_code(symbol)
    payload = fetch_json_url("https://m.stock.naver.com/api/stock/" + code + "/basic")
    price = parse_number(payload.get("closePrice"))
    if price is None:
        raise ValueError("국내 종목 가격을 찾지 못했습니다.")
    return {
        "inputSymbol": symbol,
        "symbol": code,
        "displaySymbol": code,
        "name": payload.get("stockName") or code,
        "exchange": payload.get("stockExchangeName") or "KR",
        "currency": "KRW",
        "price": price,
        "previousClose": None,
        "change": parse_number(payload.get("compareToPreviousClosePrice")),
        "changePercent": parse_number(payload.get("fluctuationsRatio")),
        "open": None,
        "high": None,
        "low": None,
        "volume": parse_number(payload.get("accumulatedTradingVolume")),
        "marketStatus": payload.get("marketStatus") or "",
        "asOf": payload.get("localTradedAt") or "",
        "source": "Naver Finance",
    }


def fetch_stooq_quote(symbol: str) -> Dict[str, object]:
    stooq_symbol = stock_input_to_stooq_symbol(symbol)
    raw = fetch_text("https://stooq.com/q/l/?s=" + urllib.parse.quote(stooq_symbol.lower()) + "&f=sd2t2ohlcvpn&h&e=csv")
    rows = raw.strip().splitlines()
    if len(rows) < 2:
        raise ValueError("해외 종목 가격을 찾지 못했습니다.")
    header = next(csv.reader([rows[0]]))
    values = next(csv.reader([rows[1]]))
    row = dict(zip(header, values))
    close = parse_number(row.get("Close"))
    if close is None:
        raise ValueError("해외 종목 가격을 찾지 못했습니다. 미국 종목은 AAPL, TSLA처럼 입력하거나 거래소 접미사를 붙여 주세요.")
    previous_close = parse_number(row.get("Prev"))
    change = close - previous_close if previous_close is not None else None
    change_percent = (change / previous_close) * 100 if previous_close else None
    return {
        "inputSymbol": symbol,
        "symbol": row.get("Symbol") or stooq_symbol,
        "displaySymbol": re.sub(r"\.US$", "", row.get("Symbol") or stooq_symbol, flags=re.I),
        "name": row.get("Name") or configured(symbol).upper(),
        "exchange": (row.get("Symbol") or stooq_symbol).split(".")[1] if "." in (row.get("Symbol") or stooq_symbol) else "US",
        "currency": "USD",
        "price": close,
        "previousClose": previous_close,
        "change": change,
        "changePercent": change_percent,
        "open": parse_number(row.get("Open")),
        "high": parse_number(row.get("High")),
        "low": parse_number(row.get("Low")),
        "volume": parse_number(row.get("Volume")),
        "marketStatus": "DELAYED",
        "asOf": " ".join([row.get("Date") or "", row.get("Time") or ""]).strip(),
        "source": "Stooq",
    }


def fetch_quote(symbol: str) -> Dict[str, object]:
    return fetch_naver_quote(symbol) if stock_input_to_naver_code(symbol) else fetch_stooq_quote(symbol)


def stock_snapshot(symbol: str) -> Dict[str, object]:
    clean = configured(symbol)
    try:
        quote = fetch_quote(clean)
        return {"inputSymbol": clean, "quote": quote, "news": [], "error": ""}
    except Exception as error:
        return {"inputSymbol": clean, "quote": None, "news": [], "error": str(error) or "종목 정보를 가져오지 못했습니다."}
