"""Notification-owned account preferences, without brokerage or watchlist writes."""

PREFERENCE_FIELDS = {
    "quietHoursEnabled": ("quiet_hours_enabled", "quiet_hours_enabled"),
    "quietHoursStart": ("quiet_hours_start", "quiet_hours_start"),
    "quietHoursEnd": ("quiet_hours_end", "quiet_hours_end"),
    "quietHoursTimezone": ("quiet_hours_timezone", "quiet_hours_timezone"),
    "messageDeliveryLevel": ("message_delivery_level", "message_delivery_level"),
    "notificationDetailLevel": ("notification_detail_level", "notification_detail_level"),
}
CHANNEL_FIELDS = {
    "notifyProvider": ("notify_provider", "notify_provider"),
    "telegramBotToken": ("bot_token", "telegram_bot_token"),
    "telegramChatId": ("chat_id", "telegram_chat_id"),
    "notifyLinkUrl": ("link_url", "notify_link_url"),
}


def write_preferences(connection, account, fields, stamp):
    selected = [PREFERENCE_FIELDS[key] for key in PREFERENCE_FIELDS if key in fields]
    if selected:
        values = [getattr(account, attr) for _, attr in selected]
        connection.execute(
            "UPDATE service_accounts SET " + ", ".join(column + " = %s" for column, _ in selected)
            + ", updated_at = %s WHERE id = %s",
            values + [stamp, account.account_id],
        )
    selected = [CHANNEL_FIELDS[key] for key in CHANNEL_FIELDS if key in fields]
    if selected:
        columns = [column for column, _ in CHANNEL_FIELDS.values()]
        values = [getattr(account, attr) for _, attr in CHANNEL_FIELDS.values()]
        connection.execute(
            "INSERT INTO telegram_configs (account_id, " + ", ".join(columns) + ", updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
            + ", ".join(column + " = VALUES(" + column + ")" for column, _ in selected)
            + ", updated_at = VALUES(updated_at)",
            [account.account_id] + values + [stamp],
        )
