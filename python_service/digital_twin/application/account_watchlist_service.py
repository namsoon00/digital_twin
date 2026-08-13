from typing import Callable, Dict, List

from ..domain.accounts import AccountConfig, split_symbols
from ..domain.events import account_saved_event
from ..domain.repositories import AccountRepository


class AccountWatchlistService:
    """Manage one account's watchlist without replacing unrelated account fields."""

    def __init__(
        self,
        repository: AccountRepository,
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
        account = self.account(account_id)
        normalized = self.normalize_symbol(symbol)
        symbols = self.unique_symbols(account.watchlist_symbols)
        changed = normalized not in symbols
        if changed:
            symbols.append(normalized)
            account.watchlist_symbols = symbols
            self.save(account)
        return self.payload(account, changed=changed, action="added", symbol=normalized)

    def remove(self, account_id: str, symbol: object) -> Dict[str, object]:
        account = self.account(account_id)
        normalized = self.normalize_symbol(symbol)
        symbols = self.unique_symbols(account.watchlist_symbols)
        changed = normalized in symbols
        if changed:
            account.watchlist_symbols = [item for item in symbols if item != normalized]
            self.save(account)
        return self.payload(account, changed=changed, action="removed", symbol=normalized)

    def replace(self, account_id: str, symbols: List[object]) -> Dict[str, object]:
        account = self.account(account_id)
        normalized = self.unique_symbols(symbols)
        changed = normalized != self.unique_symbols(account.watchlist_symbols)
        if changed:
            account.watchlist_symbols = normalized
            self.save(account)
        return self.payload(account, changed=changed, action="replaced")

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

    def save(self, account: AccountConfig) -> None:
        event = account_saved_event(account)
        if hasattr(self.repository, "upsert_with_event"):
            self.repository.upsert_with_event(account, event)
        else:
            self.repository.upsert(account)
        if self.event_publisher:
            self.event_publisher.publish(event)

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
