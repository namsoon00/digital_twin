import csv
import html
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Dict, List

from ..domain.analytics import sector_from_symbol
from ..domain.symbol_universe import ListedSymbol, utc_now_iso


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
KRX_KIND_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"


def fetch_text(url: str, timeout: int = 15) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ExitLens/0.1 (+local symbol universe refresh)",
            "Accept": "text/html,text/plain,application/octet-stream,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
    lower = content_type.lower()
    if "charset=" in lower:
        charset = lower.split("charset=", 1)[1].split(";", 1)[0].strip()
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    for charset in ["utf-8", "cp949", "euc-kr", "latin-1"]:
        try:
            return raw.decode(charset)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_nasdaq_listed(text: str, fetched_at: str = "") -> List[ListedSymbol]:
    stamp = fetched_at or utc_now_iso()
    rows = []
    reader = csv.DictReader(str(text or "").splitlines(), delimiter="|")
    for row in reader:
        symbol = str(row.get("Symbol") or "").strip()
        if not symbol or symbol.startswith("File Creation Time"):
            continue
        if str(row.get("Test Issue") or "").upper() == "Y":
            continue
        name = str(row.get("Security Name") or symbol).strip()
        market_category = str(row.get("Market Category") or "NASDAQ").strip().upper()
        exchange = {
            "Q": "NASDAQ Global Select",
            "G": "NASDAQ Global Market",
            "S": "NASDAQ Capital Market",
        }.get(market_category, "NASDAQ")
        asset_type = "ETF" if str(row.get("ETF") or "").upper() == "Y" else "STOCK"
        rows.append(ListedSymbol.create(
            symbol=symbol,
            name=name,
            market="NASDAQ",
            exchange=exchange,
            currency="USD",
            sector=sector_from_symbol(symbol + " " + name),
            asset_type=asset_type,
            source="Nasdaq Trader Symbol Directory",
            source_url=NASDAQ_LISTED_URL,
            fetched_at=stamp,
        ))
    return rows


class SimpleTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_cell = False
        self.current_cell = ""
        self.current_row: List[str] = []
        self.rows: List[List[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"td", "th"}:
            self.in_cell = True
            self.current_cell = ""

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self.in_cell:
            self.current_row.append(html.unescape(" ".join(self.current_cell.split())))
            self.current_cell = ""
            self.in_cell = False
        if tag == "tr" and self.current_row:
            self.rows.append(self.current_row)
            self.current_row = []

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell += data


def krx_kind_url(market: str) -> str:
    market_type = "stockMkt" if str(market or "").upper() == "KOSPI" else "kosdaqMkt"
    query = urllib.parse.urlencode({
        "method": "download",
        "searchType": "13",
        "marketType": market_type,
    })
    return KRX_KIND_URL + "?" + query


def parse_krx_kind_table(text: str, market: str, source_url: str = "", fetched_at: str = "") -> List[ListedSymbol]:
    parser = SimpleTableParser()
    parser.feed(str(text or ""))
    rows = [row for row in parser.rows if len(row) >= 2]
    if not rows:
        return []
    headers = rows[0]
    data_rows = rows[1:]

    def index_for(*candidates):
        for candidate in candidates:
            for index, header in enumerate(headers):
                if candidate in header:
                    return index
        return -1

    name_index = index_for("회사명", "종목명", "기업명")
    code_index = index_for("종목코드", "코드")
    sector_index = index_for("업종", "산업")
    if code_index < 0:
        return []

    stamp = fetched_at or utc_now_iso()
    normalized_market = str(market or "").upper()
    result = []
    for row in data_rows:
        code = str(row[code_index] if code_index < len(row) else "").strip()
        code = "".join(ch for ch in code if ch.isdigit()).zfill(6)
        if len(code) != 6:
            continue
        name = str(row[name_index] if name_index >= 0 and name_index < len(row) else code).strip()
        sector = str(row[sector_index] if sector_index >= 0 and sector_index < len(row) else "").strip()
        result.append(ListedSymbol.create(
            symbol=code,
            name=name,
            market=normalized_market,
            exchange=normalized_market,
            currency="KRW",
            sector=sector or sector_from_symbol(code + " " + name),
            asset_type="STOCK",
            source="KRX KIND Listed Companies",
            source_url=source_url,
            fetched_at=stamp,
        ))
    return result


def fetch_market_symbols(market: str) -> List[ListedSymbol]:
    normalized = str(market or "").upper()
    if normalized == "NASDAQ":
        return parse_nasdaq_listed(fetch_text(NASDAQ_LISTED_URL), fetched_at=utc_now_iso())
    if normalized in {"KOSPI", "KOSDAQ"}:
        url = krx_kind_url(normalized)
        return parse_krx_kind_table(fetch_text(url), normalized, source_url=url, fetched_at=utc_now_iso())
    raise ValueError("지원하지 않는 시장입니다: " + str(market))


def source_descriptor(market: str) -> Dict[str, str]:
    normalized = str(market or "").upper()
    if normalized == "NASDAQ":
        return {"source": "Nasdaq Trader Symbol Directory", "sourceUrl": NASDAQ_LISTED_URL}
    if normalized in {"KOSPI", "KOSDAQ"}:
        return {"source": "KRX KIND Listed Companies", "sourceUrl": krx_kind_url(normalized)}
    return {"source": "", "sourceUrl": ""}
