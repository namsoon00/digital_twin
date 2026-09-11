import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from digital_twin.domain.accounts import AccountConfig
from digital_twin.infrastructure.event_bus import EventBus
from digital_twin.modules.accounts.public import AccountApplicationService
from digital_twin.modules.instruments.public import AccountWatchlistService
from mysql_fixtures import TestAccountRegistry, reset_mysql_test_database, test_store_seed


class ModuleAccountMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        reset_mysql_test_database(cls.temp.name)
        cls.registry = TestAccountRegistry(test_store_seed(cls.temp.name))

    def account(self, account_id):
        value = AccountConfig.from_dict({
            "id": account_id, "label": "Module test", "provider": "toss",
            "clientId": "test-client", "clientSecret": "test-only-secret",
            "watchlistSymbols": "AAPL", "quietHoursStart": "22:00", "quietHoursEnd": "05:00",
        }, {})
        self.registry.upsert(value)
        return value

    def stored(self, account_id):
        return next(item for item in self.registry.load_saved() if item.account_id == account_id)

    def test_module_partial_account_save_changes_only_requested_scope(self):
        account = self.account("module-preferences")
        bus = EventBus()
        service = AccountApplicationService(self.registry, self.registry.settings, bus)
        original_watchlist = self.registry.watchlist_items(account.account_id)
        with self.registry.connect() as connection:
            original_credentials = connection.execute("SELECT * FROM toss_credentials WHERE account_id = %s", (account.account_id,)).fetchone()
            original_channel = connection.execute("SELECT * FROM telegram_configs WHERE account_id = %s", (account.account_id,)).fetchone()
        saved = service.save_payload({"id": account.account_id, "quiet_hours_end": "08:00"})
        self.assertEqual("08:00", saved.quiet_hours_end)
        self.assertEqual("Module test", self.stored(account.account_id).label)
        self.assertEqual(original_watchlist, self.registry.watchlist_items(account.account_id))
        with self.registry.connect() as connection:
            self.assertEqual(original_credentials, connection.execute("SELECT * FROM toss_credentials WHERE account_id = %s", (account.account_id,)).fetchone())
            self.assertEqual(original_channel, connection.execute("SELECT * FROM telegram_configs WHERE account_id = %s", (account.account_id,)).fetchone())
        self.assertEqual(["quietHoursEnd"], bus.published[0].payload["changedFields"])
        service.save_payload({"id": account.account_id, "quietHoursEnd": "08:00"})
        self.assertEqual(1, len(bus.published))

    def test_module_account_event_failure_rolls_back_preferences(self):
        account = self.account("module-rollback")
        bus = EventBus()
        service = AccountApplicationService(self.registry, self.registry.settings, bus)
        with patch("digital_twin.infrastructure.mysql_operational_core_stores.insert_domain_event_with_connection", side_effect=RuntimeError("event failed")):
            with self.assertRaisesRegex(RuntimeError, "event failed"):
                service.save_payload({"id": account.account_id, "quietHoursEnd": "08:00"})
        self.assertEqual("05:00", self.stored(account.account_id).quiet_hours_end)
        self.assertEqual([], bus.published)

    def test_module_watchlist_concurrent_add_remove_is_scoped_and_idempotent(self):
        account = self.account("module-watchlist")
        other = self.account("module-watchlist-other")
        service = AccountWatchlistService(self.registry, EventBus())
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(lambda symbol: service.add(account.account_id, symbol), ["TSLA", "NVDA"]))
        self.assertEqual({"AAPL", "TSLA", "NVDA"}, set(self.stored(account.account_id).watchlist_symbols))
        self.assertFalse(service.add(account.account_id, "TSLA")["changed"])
        service.remove(account.account_id, "AAPL")
        self.assertEqual({"TSLA", "NVDA"}, set(self.stored(account.account_id).watchlist_symbols))
        self.assertEqual(["AAPL"], self.stored(other.account_id).watchlist_symbols)
        self.assertEqual("test-only-secret", self.stored(account.account_id).client_secret)
        self.assertEqual("05:00", self.stored(account.account_id).quiet_hours_end)
        before_failure = self.stored(account.account_id).watchlist_symbols
        with patch("digital_twin.modules.instruments.infrastructure.account_watchlist.insert_domain_event_with_connection", side_effect=RuntimeError("watchlist event failed")):
            with self.assertRaisesRegex(RuntimeError, "watchlist event failed"):
                service.add(account.account_id, "MSFT")
        self.assertEqual(before_failure, self.stored(account.account_id).watchlist_symbols)
        service.replace(account.account_id, [])
        self.assertEqual([], self.stored(account.account_id).watchlist_symbols)


if __name__ == "__main__":
    unittest.main()
