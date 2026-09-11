import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from digital_twin.domain.accounts import AccountConfig
from digital_twin.infrastructure.event_bus import EventBus
from digital_twin.modules.accounts.public import AccountApplicationService
from digital_twin.modules.instruments.public import AccountWatchlistService
from mysql_fixtures import TestAccountRegistry, reset_mysql_test_database, test_store_seed
from digital_twin.infrastructure import operational_store
from digital_twin.infrastructure.service_factory import build_account_watchlist_service
from digital_twin.modules.accounts.infrastructure.mysql_account_reader import MySQLAccountReader
from digital_twin.modules.accounts.infrastructure.mysql_watchlist_account_reader import MySQLWatchlistAccountReader
from digital_twin.modules.instruments.infrastructure.mysql_account_watchlist import MySQLAccountWatchlistRepository


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
        with patch("digital_twin.infrastructure.account_transactions.insert_domain_event_with_connection", side_effect=RuntimeError("event failed")):
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

    def test_module_watchlist_runtime_injects_owner_repository_and_read_only_account_access(self):
        account = self.account("module-owner-watchlist")
        bus = EventBus()
        refreshes = []
        service = build_account_watchlist_service(
            self.registry.settings, bus,
            refresh_requester=lambda *args: refreshes.append(args) or {"status": "queued"},
        )
        self.assertIsInstance(service.repository, MySQLAccountWatchlistRepository)
        self.assertIs(type(service.repository.account_reader), MySQLWatchlistAccountReader)
        self.assertFalse(hasattr(service.repository, "upsert"))
        self.assertFalse(hasattr(service.repository.account_reader, "patch_with_event"))
        self.assertEqual("queued", service.add(account.account_id, "MSFT")["refresh"]["status"])
        self.assertEqual([account.account_id, "MSFT", "added"], list(refreshes[0]))
        self.assertEqual(["AAPL", "MSFT"], self.stored(account.account_id).watchlist_symbols)
        self.assertEqual(1, len(bus.published))

        visible_account = service.account(account.account_id)
        for private_field in ("client_id", "client_secret", "telegram_bot_token", "telegram_chat_id", "notify_link_url"):
            self.assertFalse(hasattr(visible_account, private_field), private_field)
        self.assertEqual(account.label, visible_account.label)
        self.assertEqual("test-only-secret", self.stored(account.account_id).client_secret)

    def test_module_account_delete_failure_preserves_every_owner_and_its_event(self):
        account = self.account("module-delete-rollback")
        other = self.account("module-delete-other")
        bus = EventBus()
        service = AccountApplicationService(self.registry, self.registry.settings, bus)
        before = self.stored(account.account_id).to_private_dict()
        with patch("digital_twin.infrastructure.account_transactions.insert_domain_event_with_connection", side_effect=RuntimeError("delete event failed")):
            with self.assertRaisesRegex(RuntimeError, "delete event failed"):
                service.remove(account.account_id)
        self.assertEqual(before, self.stored(account.account_id).to_private_dict())
        self.assertEqual(["AAPL"], [row["symbol"] for row in self.registry.watchlist_items(account.account_id)])
        self.assertEqual([], bus.published)
        self.assertTrue(service.remove(account.account_id))
        self.assertFalse(service.remove(account.account_id))
        self.assertEqual(1, len(bus.published))
        self.assertEqual(["AAPL"], self.stored(other.account_id).watchlist_symbols)
        with self.registry.connect() as connection:
            for table in ["toss_credentials", "telegram_configs", "account_watchlist_symbols"]:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) AS count FROM " + table + " WHERE account_id = %s", (account.account_id,)).fetchone()["count"])

    def test_module_account_creation_failure_cannot_leave_partial_identity_or_preferences(self):
        account = AccountConfig.from_dict({
            "id": "module-create-rollback", "label": "Rollback test", "provider": "toss",
            "watchlistSymbols": "AAPL,TSLA", "quietHoursEnd": "08:00",
            "clientId": "test-client", "clientSecret": "test-only-secret",
        }, {})
        bus = EventBus()
        service = AccountApplicationService(self.registry, self.registry.settings, bus)
        with patch("digital_twin.infrastructure.account_transactions.write_account_mandate", side_effect=RuntimeError("mandate failed")):
            with self.assertRaisesRegex(RuntimeError, "mandate failed"):
                service.save(account)
        self.assertNotIn(account.account_id, [row.account_id for row in self.registry.load_saved()])
        self.assertEqual([], bus.published)
        with self.registry.connect() as connection:
            for table in ["toss_credentials", "telegram_configs", "account_watchlist_symbols"]:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) AS count FROM " + table + " WHERE account_id = %s", (account.account_id,)).fetchone()["count"])
        service.save(account)
        reader = operational_store.account_reader(self.registry.settings)
        saved = next(row for row in reader.load_saved() if row.account_id == account.account_id)
        self.assertEqual("08:00", saved.quiet_hours_end)
        self.assertEqual(["AAPL", "TSLA"], saved.watchlist_symbols)
        self.assertEqual("test-only-secret", saved.client_secret)


if __name__ == "__main__":
    unittest.main()
