import unittest
from dataclasses import asdict
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo


class AccountContractTests(unittest.TestCase):
    def test_account_contract_and_policies_have_one_owner(self):
        from digital_twin.modules.accounts.domain.configuration import AccountConfig as owned_config
        from digital_twin.modules.accounts.domain.ports import AccountRepository as owned_repository
        from digital_twin.modules.accounts.contracts import AccountConfig, AccountRepository
        from digital_twin.modules.notifications.contracts import is_quiet_time
        from digital_twin.modules.portfolio.contracts import investment_strategy_profile

        self.assertIs(owned_config, AccountConfig)
        self.assertIs(owned_repository, AccountRepository)
        self.assertEqual(
            "digital_twin.modules.accounts.domain.configuration", AccountConfig.__module__
        )
        self.assertTrue(
            is_quiet_time(
                datetime(2026, 9, 11, 7, tzinfo=ZoneInfo("Asia/Seoul")),
                "22:00",
                "08:00",
                "Asia/Seoul",
            )
        )
        self.assertFalse(
            is_quiet_time(
                datetime(2026, 9, 11, 8, tzinfo=ZoneInfo("Asia/Seoul")),
                "22:00",
                "08:00",
                "Asia/Seoul",
            )
        )

    def test_account_serialization_preserves_settings_without_putting_secrets_in_context(self):
        from digital_twin.modules.accounts.contracts import AccountConfig

        config = AccountConfig.from_dict(
            {
                "id": "fixture",
                "label": "Fixture",
                "watchlistSymbols": "AAA,BBB",
                "quietHoursEnd": "08:00",
                "investmentStrategyProfile": "growth",
                "clientSecret": "fixture-secret",
                "telegramBotToken": "fixture-bot",
                "notificationDetailLevel": "concise",
            },
            {},
        )
        self.assertEqual(config, AccountConfig.from_dict(config.to_private_dict(), {}))
        self.assertEqual("08:00", config.quiet_hours_end)
        self.assertEqual("growth", config.investment_strategy_profile)
        self.assertEqual(["AAA", "BBB"], config.watchlist_symbols)
        self.assertIs(True, config.masked()["clientSecret"])
        for projection in (
            config.masked(),
            config.ontology_account_context(),
            config.domain_profile().to_dict(),
        ):
            self.assertNotIn("fixture-secret", repr(projection))
            self.assertNotIn("fixture-bot", repr(projection))

    def test_watchlist_reader_selects_only_account_identity_and_preserves_empty_defaults(self):
        from digital_twin.modules.accounts.infrastructure.mysql_watchlist_account_reader import (
            MySQLWatchlistAccountReader,
        )

        reader = MySQLWatchlistAccountReader.__new__(MySQLWatchlistAccountReader)
        reader.saved_default_symbols = ["AAA"]
        reader.default_symbols = ["AAA", "BBB"]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        reader.connect = lambda: connection
        row = {
            "id": "fixture",
            "label": "Fixture",
            "provider": "toss",
            "enabled": 0,
            "watchlist_symbols": None,
            "created_at": "2026-09-10",
            "updated_at": "2026-09-11",
        }
        connection.execute.return_value.fetchall.return_value = [row]
        account = reader.load_saved()[0]
        self.assertEqual(["AAA"], account.watchlist_symbols)
        self.assertFalse(account.enabled)
        self.assertEqual(
            {
                "account_id",
                "label",
                "provider",
                "watchlist_symbols",
                "enabled",
                "created_at",
                "updated_at",
            },
            set(asdict(account)),
        )
        sql = connection.execute.call_args[0][0].lower()
        for forbidden in (
            "client_id",
            "client_secret",
            "toss_credentials",
            "telegram_configs",
            "select *",
            "a.*",
        ):
            self.assertNotIn(forbidden, sql)
        row["watchlist_symbols"] = ""
        self.assertEqual([], reader.load_saved()[0].watchlist_symbols)
        connection.execute.return_value.fetchall.return_value = []
        self.assertEqual([], reader.load_saved())
        default = reader.load_all()[0]
        self.assertEqual(["AAA", "BBB"], default.watchlist_symbols)
        default.watchlist_symbols.clear()
        self.assertEqual(["AAA", "BBB"], reader.load_all()[0].watchlist_symbols)


if __name__ == "__main__":
    unittest.main()
