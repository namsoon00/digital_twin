from typing import Callable, Dict, List

from digital_twin.domain.accounts import AccountConfig, split_symbols
from digital_twin.modules.instruments.application.ports import AccountWatchlistRepository


class AccountWatchlistService:
    """Manage one account's watchlist without replacing unrelated account fields."""

    def __init__(
        self,
        repository: AccountWatchlistRepository,
        event_publisher=None,
        refresh_requester: Callable[[str, str, str], Dict[str, object]] = None,
    ):
        self.repository = repository
        self.event_publisher = event_publisher
        self.refresh_requester = refresh_requester

    def account(self, account_id: str) -> AccountConfig:
        normalized_id = str(account_id or "").strip()
        if not normalized_id:
            raise ValueError("계정 ID가 필요합니다.")
        accounts = self.repository.load_saved()
        if not accounts and hasattr(self.repository, "load_all"):
            accounts = self.repository.load_all()
        for account in accounts:
            if account.account_id == normalized_id:
                return account
        raise ValueError("요청한 계정을 찾지 못했습니다.")

    def normalize_symbol(self, value: object) -> str:
        symbols = split_symbols(str(value or ""))
        if len(symbols) != 1:
            raise ValueError("한 번에 한 종목의 정확한 코드를 입력하세요.")
        return symbols[0]

    def list_payload(self, account_id: str) -> Dict[str, object]:
        account = self.account(account_id)
        return self.payload(account, changed=False, action="listed")

    def add(self, account_id: str, symbol: object) -> Dict[str, object]:
        normalized = self.normalize_symbol(symbol)
        return self.mutate(account_id, "added", [normalized], normalized)

    def remove(self, account_id: str, symbol: object) -> Dict[str, object]:
        normalized = self.normalize_symbol(symbol)
        return self.mutate(account_id, "removed", [normalized], normalized)

    def replace(self, account_id: str, symbols: List[object]) -> Dict[str, object]:
        normalized = self.unique_symbols(symbols)
        return self.mutate(account_id, "replaced", normalized)

    def mutate(self, account_id, action, symbols, symbol=""):
        account = self.account(account_id)
        updated, event = self.repository.mutate_watchlist(account.account_id, action, symbols)
        account.watchlist_symbols = updated
        if event:
            account.updated_at = event.occurred_at
            if self.event_publisher:
                dispatch = getattr(self.event_publisher, "dispatch_recorded", None)
                (dispatch or self.event_publisher.publish)(event)
        return self.payload(account, changed=event is not None, action=action, symbol=symbol)

    def unique_symbols(self, values) -> List[str]:
        seen = set()
        result = []
        for value in values or []:
            for symbol in split_symbols(str(value or "")):
                if symbol in seen:
                    continue
                seen.add(symbol)
                result.append(symbol)
        return result

    def stored_items(self, account: AccountConfig) -> List[Dict[str, object]]:
        if hasattr(self.repository, "watchlist_items"):
            items = self.repository.watchlist_items(account.account_id)
            if items:
                return list(items)
        return [
            {
                "symbol": symbol,
                "createdAt": account.updated_at or account.created_at,
                "updatedAt": account.updated_at or account.created_at,
            }
            for symbol in self.unique_symbols(account.watchlist_symbols)
        ]

    def payload(
        self,
        account: AccountConfig,
        *,
        changed: bool,
        action: str,
        symbol: str = "",
    ) -> Dict[str, object]:
        refresh = {"status": "not-requested"}
        if changed and self.refresh_requester:
            refresh = dict(self.refresh_requester(account.account_id, symbol, action) or refresh)
        return {
            "account": account.masked(),
            "accountId": account.account_id,
            "items": self.stored_items(account),
            "symbols": self.unique_symbols(account.watchlist_symbols),
            "symbol": symbol,
            "action": action,
            "changed": changed,
            "refresh": refresh,
        }
