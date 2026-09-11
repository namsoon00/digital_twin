"""Account-scoped watchlist changes and their secret-free event contract."""

from digital_twin.modules.accounts.contracts import split_symbols
from digital_twin.domain.events import DomainEvent


ACCOUNT_WATCHLIST_CHANGED = "account.watchlist_changed"


def normalized_symbols(values):
    return list(dict.fromkeys(
        symbol for value in values for symbol in split_symbols(str(value or ""))
    ))


def changed_watchlist(previous, action, requested):
    requested = normalized_symbols(requested)
    if action == "added":
        return normalized_symbols(previous + requested)
    if action == "removed":
        removed = set(requested)
        return [symbol for symbol in previous if symbol not in removed]
    if action == "replaced":
        return requested
    raise ValueError("Unknown watchlist action")


def watchlist_changed_event(account_id, previous, updated, action, stamp):
    return DomainEvent(
        name=ACCOUNT_WATCHLIST_CHANGED,
        aggregate_id=account_id,
        occurred_at=stamp,
        payload={
            "accountId": account_id,
            "symbols": list(updated),
            "previousSymbols": list(previous),
            "action": action,
        },
    )
