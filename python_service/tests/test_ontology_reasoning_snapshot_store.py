import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from digital_twin.domain.accounts import AccountConfig
from digital_twin.infrastructure.mysql_monitoring_stores import (
    MySQLMonitorStore,
    MySQLOntologyReasoningMonitorStore,
)
from digital_twin.infrastructure.reasoning_snapshot_source import LatestMonitorSnapshotReasoningSource
from mysql_fixtures import mysql_test_settings, reset_mysql_test_database, test_store_seed


class OntologyReasoningSnapshotStoreTests(unittest.TestCase):
    def test_reasoning_input_keeps_the_exact_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            settings = mysql_test_settings(test_store_seed(temp))
            source_store = MySQLMonitorStore(settings)
            previous = {
                "accountId": "main",
                "accountLabel": "메인",
                "provider": "toss",
                "mode": "live",
                "status": "ok",
                "generatedAt": "2026-07-29T01:00:00Z",
                "portfolio": {
                    "total": 1000.0,
                    "invested": 900.0,
                    "cash": 100.0,
                    "markets": [],
                    "sectors": [],
                    "concentration": 0.9,
                },
                "positions": {
                    "AAPL": {
                        "symbol": "AAPL",
                        "name": "Apple",
                        "current_price": 200.0,
                        "profit_loss_rate": 5.0,
                        "quantity": 1.0,
                    },
                },
                "watchlist": {},
                "metadata": {},
                "externalSignals": {},
            }
            current = copy.deepcopy(previous)
            current["generatedAt"] = "2026-07-29T01:03:00Z"
            current["positions"]["AAPL"]["current_price"] = 210.0
            current["positions"]["AAPL"]["profit_loss_rate"] = 10.0

            source_store.upsert_snapshot_state("main", previous)
            source_store.upsert_snapshot_state("main", current, previous_state=previous)

            reasoning_store = MySQLOntologyReasoningMonitorStore(settings)
            target = reasoning_store.reasoning_snapshot_state("main", target_symbols=["AAPL"])
            persisted_previous = target["metadata"]["previousMonitorState"]

            self.assertEqual("2026-07-29T01:00:00Z", persisted_previous["generatedAt"])
            self.assertEqual(200.0, persisted_previous["positions"]["AAPL"]["current_price"])
            self.assertEqual(210.0, target["positions"]["AAPL"]["current_price"])

    def test_target_scoped_cache_avoids_replaying_the_full_provider_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            settings = mysql_test_settings(test_store_seed(temp))
            source_store = MySQLMonitorStore(settings)
            state = {
                "accountId": "main",
                "accountLabel": "메인",
                "provider": "toss",
                "mode": "live",
                "status": "ok",
                "generatedAt": "2026-07-29T01:00:00Z",
                "portfolio": {
                    "total": 1000.0,
                    "invested": 900.0,
                    "cash": 100.0,
                    "markets": [],
                    "sectors": [],
                    "concentration": 0.9,
                },
                "positions": {
                    "AAPL": {"symbol": "AAPL", "name": "Apple", "current_price": 200.0, "quantity": 1.0},
                },
                "watchlist": {
                    "NVDA": {"symbol": "NVDA", "name": "NVIDIA", "current_price": 180.0},
                },
                "metadata": {
                    "marketProxyQuotes": {"SPY": {"currentPrice": 600.0}},
                },
                "externalSignals": {
                    "macro": {"series": {"DGS10": {"value": 4.2, "date": "2026-07-29"}}},
                    "equityQuotes": {
                        "AAPL": {"price": 200.0, "changePercent": 1.0},
                        "NVDA": {"price": 180.0, "changePercent": -1.0},
                    },
                    "researchEvidence": {
                        "AAPL": [{
                            "evidenceId": "research:aapl:1",
                            "symbol": "AAPL",
                            "title": "Apple update",
                            "payload": {
                                "relationScope": "direct",
                                "articleText": "archive " * 80000,
                            },
                        }],
                    },
                },
            }
            source_store.upsert_snapshot_state("main", state)
            with source_store.transaction() as connection:
                connection.execute(
                    "DELETE FROM monitor_snapshot_reasoning_inputs WHERE account_id = %s",
                    ("main",),
                )
            self.assertEqual(1, source_store.backfill_reasoning_snapshot_inputs(["main"]))

            reasoning_store = MySQLOntologyReasoningMonitorStore(settings)
            target = reasoning_store.reasoning_snapshot_state("main", target_symbols=["AAPL"])

            self.assertEqual("main", target["accountId"])
            self.assertEqual({"AAPL"}, set(target["externalSignals"]["equityQuotes"]))
            evidence = target["externalSignals"]["researchEvidence"]["AAPL"][0]
            self.assertNotIn("articleText", evidence["payload"])
            self.assertEqual(600.0, target["metadata"]["marketProxyQuotes"]["SPY"]["currentPrice"])
            self.assertLess(
                len(json.dumps(target, ensure_ascii=False)),
                len(json.dumps(state, ensure_ascii=False)) // 50,
            )

            snapshot_source = LatestMonitorSnapshotReasoningSource(
                reasoning_store,
                settings={"monitorAccountIntervalSeconds": "180"},
                now_provider=lambda: datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc),
            )
            snapshot = snapshot_source(
                AccountConfig("main", "메인", "toss", "", "", "", "", ["AAPL"]),
                reasoning_context={"targetSymbols": ["AAPL"]},
            )

            self.assertTrue(snapshot.has_live_account_data())
            self.assertEqual({"AAPL"}, set(snapshot.external_signals["equityQuotes"]))


if __name__ == "__main__":
    unittest.main()
