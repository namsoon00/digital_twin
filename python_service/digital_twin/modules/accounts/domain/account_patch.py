"""Normalize explicit account edits without resetting fields omitted by a client."""

ACCOUNT_FIELDS = {
    "label": "label", "provider": "provider", "enabled": "enabled",
    "baseUrl": "base_url", "clientId": "client_id", "clientSecret": "client_secret",
    "accountSeq": "account_seq", "watchlistSymbols": "watchlist_symbols",
    "notifyProvider": "notify_provider", "telegramBotToken": "telegram_bot_token",
    "telegramChatId": "telegram_chat_id", "notifyLinkUrl": "notify_link_url",
    "quietHoursEnabled": "quiet_hours_enabled", "quietHoursStart": "quiet_hours_start",
    "quietHoursEnd": "quiet_hours_end", "quietHoursTimezone": "quiet_hours_timezone",
    "messageDeliveryLevel": "message_delivery_level", "notificationDetailLevel": "notification_detail_level",
    "investmentStrategyProfile": "investment_strategy_profile",
}


def explicit_account_patch(payload):
    patch = {}
    for canonical, alias in ACCOUNT_FIELDS.items():
        if canonical in payload:
            patch[canonical] = payload[canonical]
        elif alias in payload:
            patch[canonical] = payload[alias]
    return patch
