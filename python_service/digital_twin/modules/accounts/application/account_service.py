from typing import Dict, List

from digital_twin.modules.accounts.contracts import AccountConfig
from digital_twin.modules.accounts.domain.events import account_removed_event, account_saved_event
from digital_twin.modules.accounts.application.ports import AccountRepository
from digital_twin.modules.accounts.domain.account_patch import ACCOUNT_FIELDS, explicit_account_patch


class AccountApplicationService:
    def __init__(self, repository: AccountRepository, settings: Dict[str, str] = None, event_publisher=None):
        self.repository = repository
        self.settings = dict(settings or {})
        self.event_publisher = event_publisher

    def list_masked(self) -> List[Dict[str, object]]:
        return [account.masked() for account in self.repository.load_all()]

    def save(self, account: AccountConfig) -> AccountConfig:
        event = account_saved_event(account)
        self.repository.upsert_with_event(account, event)
        self.publish(event, recorded=True)
        return account

    def save_payload(self, payload: Dict[str, object]) -> AccountConfig:
        if isinstance(payload, dict):
            raw_account = payload.get("account") or payload
        else:
            raw_account = {}
        if not isinstance(raw_account, dict):
            raw_account = {}
        account = AccountConfig.from_dict(raw_account, self.settings)
        existing = next((item for item in self.repository.load_saved() if item.account_id == account.account_id), None)
        if existing is None:
            return self.save(account)
        patch = explicit_account_patch(raw_account)
        merged = existing.to_private_dict()
        merged.update(patch)
        account = AccountConfig.from_dict(merged, self.settings)
        changed = {key for key in patch if getattr(account, ACCOUNT_FIELDS[key]) != getattr(existing, ACCOUNT_FIELDS[key])}
        if not changed:
            return existing
        event = account_saved_event(account)
        event.payload["changedFields"] = sorted(changed)
        self.repository.patch_with_event(account, changed, event)
        self.publish(event, recorded=True)
        return account

    def remove(self, account_id: str) -> bool:
        event = account_removed_event(account_id)
        removed = self.repository.remove_with_event(account_id, event)
        if removed:
            self.publish(event, recorded=True)
        return removed

    def publish(self, event, recorded=False) -> None:
        if self.event_publisher:
            dispatch = getattr(self.event_publisher, "dispatch_recorded", None) if recorded else None
            (dispatch or self.event_publisher.publish)(event)

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
        if missing("quietHoursEnabled", "quiet_hours_enabled"):
            account.quiet_hours_enabled = existing.quiet_hours_enabled
        if missing("quietHoursStart", "quiet_hours_start"):
            account.quiet_hours_start = existing.quiet_hours_start
        if missing("quietHoursEnd", "quiet_hours_end"):
            account.quiet_hours_end = existing.quiet_hours_end
        if missing("quietHoursTimezone", "quiet_hours_timezone"):
            account.quiet_hours_timezone = existing.quiet_hours_timezone
        if missing("messageDeliveryLevel", "message_delivery_level"):
            account.message_delivery_level = existing.message_delivery_level
        if missing("notificationDetailLevel", "notification_detail_level"):
            account.notification_detail_level = existing.notification_detail_level
        if missing("investmentStrategyProfile", "investment_strategy_profile"):
            account.investment_strategy_profile = existing.investment_strategy_profile
        return account
