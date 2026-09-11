"""Account-owned identity and brokerage edits within a supplied transaction."""

IDENTITY_FIELDS = {"label": "label", "provider": "provider", "enabled": "enabled"}
BROKER_FIELDS = {
    "baseUrl": ("base_url", "base_url"), "clientId": ("client_id", "client_id"),
    "clientSecret": ("client_secret", "client_secret"), "accountSeq": ("account_seq", "account_seq"),
}


def ensure_identity(connection, account, stamp):
    connection.execute(
        "INSERT INTO service_accounts "
        "(id, label, provider, enabled, watchlist_symbols, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, '', %s, %s) "
        "ON DUPLICATE KEY UPDATE label = VALUES(label), provider = VALUES(provider), "
        "enabled = VALUES(enabled), updated_at = VALUES(updated_at)",
        (account.account_id, account.label, account.provider, account.enabled, stamp, stamp),
    )


def remove_identity(connection, account_id):
    connection.execute("DELETE FROM toss_credentials WHERE account_id = %s", (account_id,))
    return connection.execute("DELETE FROM service_accounts WHERE id = %s", (account_id,))


def write_identity(connection, account, fields, stamp):
    columns = [column for key, column in IDENTITY_FIELDS.items() if key in fields]
    if columns:
        connection.execute(
            "UPDATE service_accounts SET " + ", ".join(column + " = %s" for column in columns)
            + ", updated_at = %s WHERE id = %s",
            [getattr(account, column) for column in columns] + [stamp, account.account_id],
        )
    selected = [BROKER_FIELDS[key] for key in BROKER_FIELDS if key in fields]
    if selected:
        columns = [column for column, _ in BROKER_FIELDS.values()]
        values = [getattr(account, attr) for _, attr in BROKER_FIELDS.values()]
        connection.execute(
            "INSERT INTO toss_credentials (account_id, " + ", ".join(columns) + ", updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
            + ", ".join(column + " = VALUES(" + column + ")" for column, _ in selected)
            + ", updated_at = VALUES(updated_at)",
            [account.account_id] + values + [stamp],
        )
