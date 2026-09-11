"""Instrument-owned writes; callers provide the transaction boundary."""

from digital_twin.domain.accounts import split_symbols
from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
from digital_twin.modules.instruments.domain.watchlist import (
    changed_watchlist,
    normalized_symbols,
    watchlist_changed_event,
)


def write_watchlist(connection, account_id, symbols, stamp):
    normalized = normalized_symbols(symbols)
    connection.execute(
        "UPDATE service_accounts SET watchlist_symbols = %s, updated_at = %s WHERE id = %s",
        (",".join(normalized), stamp, account_id),
    )
    if normalized:
        placeholders = ",".join(["%s"] * len(normalized))
        connection.execute(
            "DELETE FROM account_watchlist_symbols WHERE account_id = %s AND symbol NOT IN (" + placeholders + ")",
            [account_id] + normalized,
        )
    else:
        connection.execute("DELETE FROM account_watchlist_symbols WHERE account_id = %s", (account_id,))
    for symbol in normalized:
        connection.execute(
            "INSERT INTO account_watchlist_symbols (account_id, symbol, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE symbol = VALUES(symbol)",
            (account_id, symbol, stamp, stamp),
        )
    return normalized


def mutate_watchlist(connection, account_id, action, symbols, stamp):
    if action not in {"added", "removed", "replaced"}:
        raise ValueError("Unknown watchlist action")
    row = connection.execute(
        "SELECT watchlist_symbols FROM service_accounts WHERE id = %s FOR UPDATE", (account_id,),
    ).fetchone()
    if not row:
        raise ValueError("계정 설정을 먼저 저장한 뒤 관심 종목을 변경해 주세요.")
    previous = list(dict.fromkeys(split_symbols(row.get("watchlist_symbols") or "")))
    updated = changed_watchlist(previous, action, symbols)
    if updated == previous:
        return updated, None
    write_watchlist(connection, account_id, updated, stamp)
    event = watchlist_changed_event(account_id, previous, updated, action, stamp)
    insert_domain_event_with_connection(connection, event)
    return updated, event
