from typing import Dict, List

from ..domain.accounts import AccountConfig
from ..domain.repositories import AccountRepository


class AccountApplicationService:
    def __init__(self, repository: AccountRepository, settings: Dict[str, str] = None):
        self.repository = repository
        self.settings = dict(settings or {})

    def list_masked(self) -> List[Dict[str, object]]:
        return [account.masked() for account in self.repository.load_all()]

    def save(self, account: AccountConfig) -> AccountConfig:
        self.repository.upsert(account)
        return account

    def save_payload(self, payload: Dict[str, object]) -> AccountConfig:
        if isinstance(payload, dict):
            raw_account = payload.get("account") or payload
        else:
            raw_account = {}
        if not isinstance(raw_account, dict):
            raw_account = {}
        account = AccountConfig.from_dict(raw_account, self.settings)
        account = self.preserve_existing_secrets(raw_account, account)
        self.repository.upsert(account)
        return account

    def remove(self, account_id: str) -> bool:
        return self.repository.remove(account_id)

    def preserve_existing_secrets(self, payload, account: AccountConfig) -> AccountConfig:
        existing = {item.account_id: item for item in self.repository.load_saved()}.get(account.account_id)
        if not existing or not isinstance(payload, dict):
            return account

        def missing(*keys):
            return not any(key in payload for key in keys)

        if missing("clientId", "client_id"):
            account.client_id = existing.client_id
        if missing("clientSecret", "client_secret"):
            account.client_secret = existing.client_secret
        if missing("telegramBotToken", "telegram_bot_token"):
            account.telegram_bot_token = existing.telegram_bot_token
        if missing("telegramChatId", "telegram_chat_id"):
            account.telegram_chat_id = existing.telegram_chat_id
        return account
