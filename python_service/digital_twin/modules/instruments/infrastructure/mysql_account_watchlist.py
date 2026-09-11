"""Watchlist-only persistence capability with an injected account reader."""

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.settings import utc_now
from digital_twin.modules.accounts.contracts import WatchlistAccountReader
from digital_twin.modules.instruments.infrastructure.account_watchlist import mutate_watchlist, read_watchlist


class MySQLAccountWatchlistRepository(MySQLOperationalConnection):
    def __init__(self, settings, account_reader: WatchlistAccountReader):
        super().__init__(settings)
        self.account_reader = account_reader

    def load_saved(self):
        return self.account_reader.load_saved()

    def load_all(self):
        return self.account_reader.load_all()

    def watchlist_items(self, account_id):
        with self.connect() as connection:
            return read_watchlist(connection, account_id)

    def mutate_watchlist(self, account_id, action, symbols):
        with self.transaction() as connection:
            return mutate_watchlist(connection, account_id, action, symbols, utc_now())
