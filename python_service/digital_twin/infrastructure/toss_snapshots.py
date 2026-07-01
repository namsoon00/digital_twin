import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Tuple

from ..domain.accounts import AccountConfig
from ..domain.analytics import decisions_for_positions, normalize_position, number, portfolio_summary
from ..domain.portfolio import AccountSnapshot, Position, utc_now_iso
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


def normalize_holdings(payload: Dict[str, object]) -> List[Dict[str, object]]:
    data = payload.get("data") or payload.get("result") or payload
    overview = data.get("overview") or data.get("holdings") or data if isinstance(data, dict) else data
    items = overview.get("items") or overview.get("holdings") or overview.get("positions") if isinstance(overview, dict) else overview
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
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

    def fetch_positions(self) -> Tuple[str, str, List[Position], float, str]:
        if not self.account.client_id or not self.account.client_secret:
            return "demo", "토스 credentials 미설정", demo_positions(), 1250000.0, "KRW"
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
            selected = accounts[0] if accounts else {}
            account_seq = self.account.account_seq or str(selected.get("accountSeq") or selected.get("id") or "")
            account_cash = number(selected.get("orderableAmount") or selected.get("availableAmount") or selected.get("cashBalance"))
            account_currency = str(selected.get("currency") or "KRW")
            if not account_seq:
                return "live", "계좌 식별값 없음", [], account_cash, account_currency
            holdings_payload = http_json(
                "GET",
                self.base_url + "/api/v1/holdings",
                {"Authorization": "Bearer " + token, "X-Tossinvest-Account": account_seq},
            )
            positions = [normalize_position(item) for item in normalize_holdings(holdings_payload)]
            return "live", "토스 계좌 동기화", positions, account_cash, account_currency
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError) as error:
            return "demo", "토스 조회 실패 · " + str(error), demo_positions(), 1250000.0, "KRW"


def build_snapshot(account: AccountConfig) -> AccountSnapshot:
    provider = TossProvider(account)
    mode, status, positions, cash, currency = provider.fetch_positions()
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
    )
