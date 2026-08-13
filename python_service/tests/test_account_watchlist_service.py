import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.account_watchlist_service import AccountWatchlistService
from digital_twin.domain.accounts import AccountConfig
from digital_twin.infrastructure.event_bus import EventBus


def account(symbols=None):
    return AccountConfig(
        account_id="main",
        label="메인 계정",
        provider="toss",
        base_url="https://example.test",
        client_id="client-id",
        client_secret="secret",
        account_seq="01",
        watchlist_symbols=list(symbols or []),
        telegram_bot_token="telegram-secret",
        updated_at="2026-08-13T00:00:00Z",
    )


class AccountRepositoryStub:
    def __init__(self, value):
        self.value = value
        self.saved = []

    def load_saved(self):
        return [self.value]

    def upsert(self, value):
        self.value = value
        self.saved.append(value)


class AccountWatchlistServiceTests(unittest.TestCase):
    def test_add_is_account_scoped_idempotent_and_requests_refresh_once(self):
        repository = AccountRepositoryStub(account(["AAPL"]))
        events = EventBus()
        refreshes = []
        service = AccountWatchlistService(
            repository,
            event_publisher=events,
            refresh_requester=lambda account_id, symbol, action: refreshes.append((account_id, symbol, action)) or {"status": "queued"},
        )

        added = service.add("main", "tsla")
        duplicate = service.add("main", "TSLA")

        self.assertEqual(["AAPL", "TSLA"], added["symbols"])
        self.assertTrue(added["changed"])
        self.assertFalse(duplicate["changed"])
        self.assertEqual([("main", "TSLA", "added")], refreshes)
        self.assertEqual(1, len(repository.saved))
        self.assertEqual(1, len(events.published))
        self.assertEqual("secret", repository.value.client_secret)
        self.assertEqual("telegram-secret", repository.value.telegram_bot_token)

    def test_remove_and_replace_keep_unique_normalized_symbols(self):
        repository = AccountRepositoryStub(account(["AAPL", "TSLA"]))
        service = AccountWatchlistService(repository)

        removed = service.remove("main", "aapl")
        replaced = service.replace("main", [" nvda ", "NVDA", "000660"])

        self.assertEqual(["TSLA"], removed["symbols"])
        self.assertEqual(["NVDA", "000660"], replaced["symbols"])
        self.assertEqual(["NVDA", "000660"], repository.value.watchlist_symbols)

    def test_requires_existing_account_and_single_symbol(self):
        service = AccountWatchlistService(AccountRepositoryStub(account()))

        with self.assertRaisesRegex(ValueError, "한 번에 한 종목"):
            service.add("main", "AAPL,TSLA")
        with self.assertRaisesRegex(ValueError, "계정을 찾지 못했습니다"):
            service.add("missing", "AAPL")


if __name__ == "__main__":
    unittest.main()
