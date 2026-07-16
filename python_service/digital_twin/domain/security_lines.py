from dataclasses import dataclass
from typing import Dict, Iterable, List

from .market_data import number
from .portfolio import Position


DEFAULT_SECURITY_LINES_TEXT = """# localSymbol|companyName|role|symbol|label|market|currency|exchange|adrRatio|conversionStartDate|leverageFactor|underlyingSymbol|sourceUrl|listingDate
000660|SK하이닉스|local|000660|SK하이닉스 보통주|KR|KRW|KRX|1|||000660|https://finance.yahoo.com/quote/000660.KS/|
000660|SK하이닉스|adr|SKHY|SK hynix ADR|US|USD|Nasdaq|0.1|2026-07-29|0|000660|https://www.nasdaq.com/market-activity/stocks/skhy|2026-07-13
000660|SK하이닉스|adr|SKHYV|SK hynix ADR temporary ticker|US|USD|Nasdaq|0.1|2026-07-29|0|000660|https://www.nasdaq.com/market-activity/stocks/skhy|2026-07-10
000660|SK하이닉스|leveraged_etf|SKHX|Leverage Shares 2x Long SK Hynix Daily ETF|US|USD|Cboe|0||2|SKHY|https://www.nasdaq.com/press-release/sk-hynix-etfs-2x-long-skhx-1x-short-skhz-leverage-shares-themes-arrive-adrs-begin|2026-07-14
000660|SK하이닉스|inverse_etf|SKHZ|Leverage Shares 1x Short SK Hynix Daily ETF|US|USD|Cboe|0||-1|SKHY|https://www.nasdaq.com/press-release/sk-hynix-etfs-2x-long-skhx-1x-short-skhz-leverage-shares-themes-arrive-adrs-begin|2026-07-14
000660|SK하이닉스|leveraged_etf|SKUU|GraniteShares 2x Long SK Hynix Daily ETF|US|USD|Nasdaq|0||2|SKHY|https://graniteshares.com/sk-hynix-leveraged-etfs/|2026-07-14
000660|SK하이닉스|inverse_leveraged_etf|SKDD|GraniteShares 2x Short SK Hynix Daily ETF|US|USD|Nasdaq|0||-2|SKHY|https://graniteshares.com/sk-hynix-leveraged-etfs/|2026-07-14
"""


@dataclass(frozen=True)
class SecurityLine:
    local_symbol: str
    company_name: str
    role: str
    symbol: str
    label: str
    market: str = ""
    currency: str = ""
    exchange: str = ""
    adr_ratio: float = 0.0
    conversion_start_date: str = ""
    leverage_factor: float = 0.0
    underlying_symbol: str = ""
    source_url: str = ""
    listing_date: str = ""
    source: str = "default"

    @property
    def is_adr(self) -> bool:
        return self.role == "adr"

    @property
    def is_local(self) -> bool:
        return self.role == "local"

    @property
    def is_leveraged(self) -> bool:
        return self.role in {"leveraged_etf", "inverse_etf", "inverse_leveraged_etf"} or abs(number(self.leverage_factor)) >= 2

    def to_dict(self) -> Dict[str, object]:
        return {
            "localSymbol": self.local_symbol,
            "companyName": self.company_name,
            "role": self.role,
            "symbol": self.symbol,
            "label": self.label,
            "market": self.market,
            "currency": self.currency,
            "exchange": self.exchange,
            "adrRatio": self.adr_ratio,
            "conversionStartDate": self.conversion_start_date,
            "leverageFactor": self.leverage_factor,
            "underlyingSymbol": self.underlying_symbol,
            "sourceUrl": self.source_url,
            "listingDate": self.listing_date,
            "source": self.source,
        }


def normalize_symbol(value: object) -> str:
    return str(value or "").upper().strip()


def parse_security_lines_text(text: str, source: str = "setting") -> List[SecurityLine]:
    rows: List[SecurityLine] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [item.strip() for item in line.split("|")]
        if len(parts) < 4:
            continue
        local_symbol = normalize_symbol(parts[0])
        role = str(parts[2] or "").strip().lower().replace("-", "_")
        symbol = normalize_symbol(parts[3])
        if not local_symbol or not role or not symbol:
            continue
        rows.append(SecurityLine(
            local_symbol=local_symbol,
            company_name=parts[1] if len(parts) > 1 and parts[1] else local_symbol,
            role=role,
            symbol=symbol,
            label=parts[4] if len(parts) > 4 and parts[4] else symbol,
            market=normalize_symbol(parts[5] if len(parts) > 5 else ""),
            currency=normalize_symbol(parts[6] if len(parts) > 6 else ""),
            exchange=parts[7] if len(parts) > 7 else "",
            adr_ratio=number(parts[8] if len(parts) > 8 else 0),
            conversion_start_date=parts[9] if len(parts) > 9 else "",
            leverage_factor=number(parts[10] if len(parts) > 10 else 0),
            underlying_symbol=normalize_symbol(parts[11] if len(parts) > 11 else ""),
            source_url=parts[12] if len(parts) > 12 else "",
            listing_date=parts[13] if len(parts) > 13 else "",
            source=source,
        ))
    return rows


def security_line_catalog(settings: Dict[str, object] = None) -> List[SecurityLine]:
    settings = settings if isinstance(settings, dict) else {}
    default_rows = parse_security_lines_text(DEFAULT_SECURITY_LINES_TEXT, source="default")
    custom_rows = parse_security_lines_text(str(settings.get("securityLineMappings") or ""), source="setting")
    by_key: Dict[tuple, SecurityLine] = {}
    for row in default_rows + custom_rows:
        by_key[(row.local_symbol, row.role, row.symbol)] = row
    return list(by_key.values())


def security_lines_for_symbol(symbol: object, settings: Dict[str, object] = None) -> List[SecurityLine]:
    normalized = normalize_symbol(symbol)
    rows = security_line_catalog(settings)
    local_symbols = {
        row.local_symbol
        for row in rows
        if row.local_symbol == normalized or row.symbol == normalized or row.underlying_symbol == normalized
    }
    if not local_symbols:
        return []
    return [row for row in rows if row.local_symbol in local_symbols]


def related_market_symbols_for_positions(positions: Iterable[Position], settings: Dict[str, object] = None) -> List[str]:
    settings = settings if isinstance(settings, dict) else {}
    enabled = str(settings.get("externalAlphaRelatedSymbolsEnabled") or "1").strip().lower() not in {"0", "false", "no", "off", "disabled"}
    if not enabled:
        return []
    observed = {normalize_symbol(position.symbol) for position in positions or [] if normalize_symbol(position.symbol)}
    symbols: List[str] = []
    seen = set()
    for row in security_line_catalog(settings):
        if row.local_symbol not in observed and row.symbol not in observed and row.underlying_symbol not in observed:
            continue
        if row.market not in {"US", "USA", "NASDAQ", "NYSE", "AMEX", "CBOE"} and row.currency != "USD":
            continue
        if row.symbol in observed or row.role == "local":
            continue
        if row.symbol not in seen:
            seen.add(row.symbol)
            symbols.append(row.symbol)
    max_symbols = int(number(settings.get("externalAlphaRelatedMaxSymbols")) or 8)
    return symbols[:max(0, max_symbols)]
