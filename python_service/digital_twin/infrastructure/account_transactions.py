"""Explicit transaction coordinator for account edits spanning several owners."""

from digital_twin.domain.events import DomainEvent
from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
from digital_twin.infrastructure.settings import utc_now
from digital_twin.modules.accounts.domain.account_patch import ACCOUNT_FIELDS
from digital_twin.modules.accounts.infrastructure.account_identity import ensure_identity, remove_identity, write_identity
from digital_twin.modules.accounts.infrastructure.mysql_account_reader import MySQLAccountReader
from digital_twin.modules.instruments.infrastructure.account_watchlist import (
    mutate_watchlist,
    read_watchlist,
    remove_watchlist,
    write_watchlist,
)
from digital_twin.modules.notifications.infrastructure.account_preferences import remove_preferences, write_preferences
from digital_twin.modules.portfolio.infrastructure.account_mandate import write_account_mandate


class MySQLAccountRegistry(MySQLAccountReader):
    """Account command capability. Read paths should receive MySQLAccountReader."""

    def upsert_with_connection(self, connection, account):
        stamp = utc_now()
        ensure_identity(connection, account, stamp)
        write_identity(connection, account, ACCOUNT_FIELDS, stamp)
        write_preferences(connection, account, ACCOUNT_FIELDS, stamp)
        write_watchlist(connection, account.account_id, account.watchlist_symbols, stamp)
        write_account_mandate(connection, account, stamp)

    def upsert(self, account):
        with self.transaction() as connection:
            self.upsert_with_connection(connection, account)

    def upsert_with_event(self, account, event: DomainEvent):
        with self.transaction() as connection:
            self.upsert_with_connection(connection, account)
            insert_domain_event_with_connection(connection, event)

    def patch_with_event(self, account, fields, event: DomainEvent):
        fields = set(fields)
        if not fields.issubset(ACCOUNT_FIELDS):
            raise ValueError("Unknown account patch fields")
        stamp = event.occurred_at
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM service_accounts WHERE id = %s FOR UPDATE", (account.account_id,),
            ).fetchone()
            if not row:
                raise ValueError("Account was removed before the update")
            write_identity(connection, account, fields, stamp)
            write_preferences(connection, account, fields, stamp)
            if "watchlistSymbols" in fields:
                write_watchlist(connection, account.account_id, account.watchlist_symbols, stamp)
            if "investmentStrategyProfile" in fields:
                write_account_mandate(connection, account, stamp)
            connection.execute("UPDATE service_accounts SET updated_at = %s WHERE id = %s", (stamp, account.account_id))
            insert_domain_event_with_connection(connection, event)
        account.updated_at = stamp

    def watchlist_items(self, account_id):
        with self.connect() as connection:
            return read_watchlist(connection, account_id)

    def mutate_watchlist(self, account_id, action, symbols):
        with self.transaction() as connection:
            return mutate_watchlist(connection, account_id, action, symbols, utc_now())

    def remove(self, account_id):
        return self._remove(account_id)

    def remove_with_event(self, account_id, event: DomainEvent):
        return self._remove(account_id, event)

    def _remove(self, account_id, event=None):
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM service_accounts WHERE id = %s FOR UPDATE", (account_id,),
            ).fetchone()
            if not row:
                return False
            remove_preferences(connection, account_id)
            remove_watchlist(connection, account_id)
            remove_identity(connection, account_id)
            if event:
                insert_domain_event_with_connection(connection, event)
        return True
