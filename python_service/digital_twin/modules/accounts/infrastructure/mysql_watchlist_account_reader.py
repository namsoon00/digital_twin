"""Account-owned projection that never selects brokerage or delivery credentials."""

from typing import List

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_core_stores import MySQLRuntimeSettingsStore
from digital_twin.modules.accounts.domain.configuration import split_symbols
from digital_twin.modules.accounts.domain.watchlist_account import WatchlistAccount


class MySQLWatchlistAccountReader(MySQLOperationalConnection):
    def __init__(self, settings=None):
        super().__init__(settings)
        configured = MySQLRuntimeSettingsStore(settings).load()
        configured.update(settings or {})
        self.default_symbols = split_symbols(
            configured.get("watchlistSymbols", "TSLA,AAPL,NVDA,000660")
        )
        # Saved rows historically default a NULL watchlist to the configured
        # value, but an unsaved default account also has bootstrap symbols.
        self.saved_default_symbols = split_symbols(configured.get("watchlistSymbols", ""))

    def load_saved(self) -> List[WatchlistAccount]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, label, provider, watchlist_symbols, enabled, created_at, updated_at
                FROM service_accounts
                ORDER BY updated_at DESC, id
            """
            ).fetchall()
        return [
            WatchlistAccount(
                account_id=row["id"],
                label=row["label"],
                provider=row["provider"],
                watchlist_symbols=(
                    split_symbols(row["watchlist_symbols"])
                    if row["watchlist_symbols"] is not None
                    else list(self.saved_default_symbols)
                ),
                enabled=bool(row["enabled"]),
                created_at=row["created_at"] or "",
                updated_at=row["updated_at"] or "",
            )
            for row in rows
        ]

    def load_all(self) -> List[WatchlistAccount]:
        return self.load_saved() or [
            WatchlistAccount(
                account_id="default",
                label="기본 계정",
                provider="toss",
                watchlist_symbols=list(self.default_symbols),
            )
        ]
