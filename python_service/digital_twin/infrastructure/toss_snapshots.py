import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from typing import Dict, List, Tuple

from ..domain.accounts import AccountConfig
from ..domain.analytics import decisions_for_positions, known_stock, normalize_position, number, portfolio_summary, technical_indicators_from_candles
from ..domain.portfolio import AccountSnapshot, Position, utc_now_iso
from .external_signals import ExternalSignalProvider
from .settings import currency_rates


def http_json(method: str, url: str, headers: Dict[str, str], body: bytes = None, timeout: int = 12) -> Dict[str, object]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def form_body(payload: Dict[str, str]) -> bytes:
    return urllib.parse.urlencode(payload).encode("utf-8")


def normalize_accounts(payload: Dict[str, object]) -> List[Dict[str, object]]:
    data = payload.get("data") or payload.get("result") or payload
    accounts = data.get("accounts") if isinstance(data, dict) else data
    if isinstance(accounts, list):
        return [item for item in accounts if isinstance(item, dict)]
    return []


def account_identifiers(account: Dict[str, object]) -> List[str]:
    keys = [
        "accountSeq",
        "account_seq",
        "accountId",
        "account_id",
        "id",
        "accountNo",
        "accountNumber",
        "account_number",
    ]
    values = []
    for key in keys:
        value = str(account.get(key) or "").strip()
        if value:
            values.append(value)
    return values


def select_account(accounts: List[Dict[str, object]], configured_seq: str = "") -> Dict[str, object]:
    requested = str(configured_seq or "").strip()
    if requested:
        for account in accounts:
            if requested in account_identifiers(account):
                return account
    return accounts[0] if accounts else {}


def account_cash_amount(account: Dict[str, object]) -> float:
    keys = [
        "orderableAmount",
        "orderable_amount",
        "orderAvailableAmount",
        "availableOrderAmount",
        "availableAmount",
        "available_amount",
        "availableCash",
        "cashAvailable",
        "cashBalance",
        "cash_balance",
        "withdrawableAmount",
        "withdrawable_amount",
        "depositAmount",
        "deposit",
        "balance",
        "cash",
        "주문가능금액",
        "주문가능",
        "출금가능금액",
        "현금",
    ]
    for key in keys:
        if key in account and account.get(key) not in (None, ""):
            amount = number(account.get(key))
            if amount:
                return amount
    balances = account.get("balances") or account.get("cashBalances") or account.get("cash_balances")
    if isinstance(balances, list):
        for item in balances:
            if isinstance(item, dict):
                amount = account_cash_amount(item)
                if amount:
                    return amount
    return 0.0


def normalize_holdings(payload: Dict[str, object]) -> List[Dict[str, object]]:
    data = payload.get("data") or payload.get("result") or payload
    overview = data.get("overview") or data.get("holdings") or data if isinstance(data, dict) else data
    items = overview.get("items") or overview.get("holdings") or overview.get("positions") if isinstance(overview, dict) else overview
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def normalize_candles(payload: Dict[str, object]) -> List[Dict[str, object]]:
    data = payload.get("data") or payload.get("result") or payload
    candles = data.get("candles") if isinstance(data, dict) else data
    if isinstance(candles, list):
        return [item for item in candles if isinstance(item, dict)]
    return []


def demo_positions() -> List[Position]:
    return [
        normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "quantity": "12",
            "sellableQuantity": "12",
            "averagePrice": 65000,
            "currentPrice": 72000,
            "marketValue": 864000,
            "profitLoss": 84000,
            "profitLossRate": 10.8,
            "tradeStrength": 118,
            "tradingValue": 912000000,
            "volume": 12666,
            "volumeRatio": 1.8,
            "buyVolume": 620000,
            "sellVolume": 480000,
            "foreignBuyVolume": 420000,
            "foreignSellVolume": 275000,
            "institutionBuyVolume": 310000,
            "institutionSellVolume": 228000,
            "ma5": 70500,
            "ma20": 69000,
            "ma60": 66000,
            "ma120": 64000,
            "ma200": 62000,
            "ma20Slope": 0.4,
            "ma60Slope": 0.2,
            "ma20Distance": 4.3,
            "ma60Distance": 9.1,
            "sector": "반도체",
        }),
        normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "quantity": "2",
            "sellableQuantity": "2",
            "averagePrice": 210,
            "currentPrice": 243.1,
            "marketValue": 486.2,
            "profitLoss": 66.2,
            "profitLossRate": 15.8,
            "tradeStrength": 106,
            "tradingValue": 142000000,
            "volume": 584000,
            "volumeRatio": 1.4,
            "buyVolume": 320000,
            "sellVolume": 410000,
            "foreignBuyVolume": 840000,
            "foreignSellVolume": 910000,
            "institutionBuyVolume": 420000,
            "institutionSellVolume": 455000,
            "ma5": 239,
            "ma20": 232,
            "ma60": 218,
            "ma120": 205,
            "ma200": 198,
            "ma20Slope": 0.3,
            "ma60Slope": 0.2,
            "ma20Distance": 4.8,
            "ma60Distance": 11.5,
            "sector": "AI/플랫폼",
        }),
        normalize_position({
            "symbol": "CASH",
            "name": "대기 현금",
            "market": "CASH",
            "currency": "KRW",
            "marketValue": 1250000,
            "sector": "현금",
        }),
    ]


class TossProvider:
    def __init__(self, account: AccountConfig):
        self.account = account
        self.base_url = account.base_url.rstrip("/")

    def fetch_positions(self) -> Tuple[str, str, List[Position], float, str, List[Position]]:
        if not self.account.client_id or not self.account.client_secret:
            return "demo", "토스 credentials 미설정", demo_positions(), 1250000.0, "KRW", []
        try:
            token_payload = http_json(
                "POST",
                self.base_url + "/oauth2/token",
                {"Content-Type": "application/x-www-form-urlencoded"},
                form_body({
                    "grant_type": "client_credentials",
                    "client_id": self.account.client_id,
                    "client_secret": self.account.client_secret,
                }),
            )
            token = str(token_payload.get("access_token") or "")
            if not token:
                raise RuntimeError("토스 access_token이 없습니다.")
            accounts_payload = http_json("GET", self.base_url + "/api/v1/accounts", {"Authorization": "Bearer " + token})
            accounts = normalize_accounts(accounts_payload)
            selected = select_account(accounts, self.account.account_seq)
            account_seq = self.account.account_seq or str(selected.get("accountSeq") or selected.get("id") or "")
            account_cash = account_cash_amount(selected)
            account_currency = str(selected.get("currency") or "KRW")
            if not account_seq:
                return "live", "계좌 식별값 없음", [], account_cash, account_currency, []
            buying_power = self.fetch_buying_power(token, account_seq)
            if buying_power:
                account_cash = buying_power
                account_currency = "KRW"
            holdings_payload = http_json(
                "GET",
                self.base_url + "/api/v1/holdings",
                {"Authorization": "Bearer " + token, "X-Tossinvest-Account": account_seq},
            )
            positions = [normalize_position(item) for item in normalize_holdings(holdings_payload)]
            positions = self.enrich_positions_with_candles(token, positions)
            watchlist = self.fetch_watchlist_quotes(token, positions)
            return "live", "토스 계좌 동기화", positions, account_cash, account_currency, watchlist
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError) as error:
            return "demo", "토스 조회 실패 · " + str(error), demo_positions(), 1250000.0, "KRW", []

    def fetch_buying_power(self, token: str, account_seq: str) -> float:
        total = 0.0
        rates = currency_rates()
        for currency in ["KRW", "USD"]:
            try:
                query = urllib.parse.urlencode({"currency": currency})
                payload = http_json(
                    "GET",
                    self.base_url + "/api/v1/buying-power?" + query,
                    {"Authorization": "Bearer " + token, "X-Tossinvest-Account": account_seq},
                )
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError):
                continue
            data = payload.get("data") or payload.get("result") or payload
            amount = number(data.get("cashBuyingPower") if isinstance(data, dict) else 0)
            total += amount * rates.get(currency, 1.0)
        return total

    def fetch_daily_candles(self, token: str, symbol: str) -> List[Dict[str, object]]:
        query = urllib.parse.urlencode({
            "symbol": symbol,
            "interval": "1d",
            "count": "200",
            "adjusted": "true",
        })
        payload = http_json(
            "GET",
            self.base_url + "/api/v1/candles?" + query,
            {"Authorization": "Bearer " + token},
        )
        return normalize_candles(payload)

    def enrich_positions_with_candles(self, token: str, positions: List[Position]) -> List[Position]:
        enriched: List[Position] = []
        chart_calls = 0
        for position in positions:
            if position.is_cash() or not position.symbol:
                enriched.append(position)
                continue
            try:
                if chart_calls:
                    time.sleep(0.22)
                candles = self.fetch_daily_candles(token, position.symbol)
                chart_calls += 1
                indicators = technical_indicators_from_candles(candles)
                if not indicators:
                    enriched.append(position)
                    continue
                enriched.append(replace(
                    position,
                    current_price=position.current_price or indicators.get("currentPrice", 0.0),
                    volume=position.volume or number(indicators.get("volume")),
                    volume_ratio=position.volume_ratio or number(indicators.get("volumeRatio")),
                    ma5=number(indicators.get("ma5")),
                    ma20=number(indicators.get("ma20")),
                    ma60=number(indicators.get("ma60")),
                    ma120=number(indicators.get("ma120")),
                    ma200=number(indicators.get("ma200")),
                    ma20_slope=number(indicators.get("ma20Slope")),
                    ma60_slope=number(indicators.get("ma60Slope")),
                    ma20_distance=number(indicators.get("ma20Distance")),
                    ma60_distance=number(indicators.get("ma60Distance")),
                ))
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError, OSError):
                enriched.append(position)
        return enriched

    def fetch_watchlist_quotes(self, token: str, positions: List[Position]) -> List[Position]:
        holding_symbols = {position.symbol.upper() for position in positions if position.symbol}
        watchlist: List[Position] = []
        chart_calls = 0
        for symbol in self.account.watchlist_symbols[:30]:
            normalized = str(symbol or "").upper()
            if not normalized or normalized in holding_symbols:
                continue
            info = known_stock(normalized)
            base = normalize_position({
                "symbol": info.get("symbol") or normalized,
                "name": info.get("name") or normalized,
                "market": info.get("market") or "",
                "currency": info.get("currency") or "",
                "sector": info.get("sector") or "",
            })
            try:
                if chart_calls:
                    time.sleep(0.22)
                candles = self.fetch_daily_candles(token, normalized)
                chart_calls += 1
                indicators = technical_indicators_from_candles(candles)
                if not indicators:
                    watchlist.append(base)
                    continue
                watchlist.append(replace(
                    base,
                    current_price=number(indicators.get("currentPrice")),
                    volume=number(indicators.get("volume")),
                    volume_ratio=number(indicators.get("volumeRatio")),
                    ma5=number(indicators.get("ma5")),
                    ma20=number(indicators.get("ma20")),
                    ma60=number(indicators.get("ma60")),
                    ma120=number(indicators.get("ma120")),
                    ma200=number(indicators.get("ma200")),
                    ma20_slope=number(indicators.get("ma20Slope")),
                    ma60_slope=number(indicators.get("ma60Slope")),
                    ma20_distance=number(indicators.get("ma20Distance")),
                    ma60_distance=number(indicators.get("ma60Distance")),
                ))
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError, OSError):
                watchlist.append(base)
        return watchlist


def build_snapshot(account: AccountConfig) -> AccountSnapshot:
    provider = TossProvider(account)
    mode, status, positions, cash, currency, watchlist = provider.fetch_positions()
    external_signals = ExternalSignalProvider().signals_for_positions(positions + watchlist)
    portfolio = portfolio_summary(positions, cash, currency, currency_rates())
    decisions = decisions_for_positions(positions, portfolio)
    return AccountSnapshot(
        account_id=account.account_id,
        account_label=account.label,
        provider=account.provider,
        mode=mode,
        status=status,
        generated_at=utc_now_iso(),
        portfolio=portfolio,
        positions=positions,
        decisions=decisions,
        external_signals=external_signals,
        watchlist=watchlist,
    )
