import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from digital_twin.modules.investment_calendar.application.investment_calendar_discovery_service import InvestmentCalendarDiscoveryService
from digital_twin.modules.investment_calendar.application.official_calendar_sync_service import OfficialCalendarSyncService
from digital_twin.modules.investment_calendar.domain.official_calendar import OfficialCalendarEvent


class Checkpoints:
    def __init__(self):
        self.rows = {}

    def load(self, key):
        return copy.deepcopy(self.rows.get(key, {}))

    def save(self, key, value):
        self.rows[key] = copy.deepcopy(value)


class Calendar:
    def __init__(self):
        self.events = {}

    def save_event(self, row):
        self.events[row["eventId"]] = row


def event(symbol="AAPL", date="2026-11-02T22:00:00Z"):
    return OfficialCalendarEvent(event_id=symbol + "-2026-q4", title=symbol + " earnings",
                                 event_type="earnings", starts_at=date, symbols=[symbol])


def source(label, events=None):
    return SimpleNamespace(label=label, events=Mock(return_value=events if events is not None else [event(label)]))


class CalendarAutomaticCollectionTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 30, tzinfo=timezone.utc)
        self.clock = lambda: self.now
        self.checkpoints = Checkpoints()
        self.calendar = Calendar()

    def test_new_official_target_is_collected_without_restart_or_full_resync(self):
        apple, nvidia = source("AAPL"), source("NVDA")
        sources = [apple]
        service = OfficialCalendarSyncService(self.calendar, now=self.clock, checkpoint_store=self.checkpoints,
            source_reader=lambda: {"sources": list(sources), "targetSymbols": [item.label for item in sources]})
        self.assertEqual(service.run_due()["savedCount"], 1)
        sources.append(nvidia)
        self.now += timedelta(seconds=61)
        result = service.run_due()
        self.assertEqual(result["targetSymbols"], ["AAPL", "NVDA"])
        self.assertEqual(result["savedCount"], 1)
        self.assertEqual(apple.events.call_count, 1)
        self.assertEqual(nvidia.events.call_count, 1)
        self.assertFalse(service.due())

    def test_bulk_source_tracks_target_changes_and_discards_removed_scope(self):
        dart = source("DART")
        dart.target_symbols = ["000660"]
        service = OfficialCalendarSyncService(self.calendar, [dart], now=self.clock, checkpoint_store=self.checkpoints)
        service.run_due()
        dart.target_symbols.append("035420")
        service.run_due()
        self.assertEqual(dart.events.call_count, 2)
        self.assertEqual(len(service.source_states), 1)
        service.sources = []
        service.run_once(force=True)
        self.assertEqual(service.source_states, {})

    def test_failed_source_retries_after_restart_without_refetching_successes(self):
        apple, tesla = source("AAPL"), source("TSLA")
        self.calendar.quarantine_unverified_automatic_events = Mock(return_value={"quarantinedCount": 0})
        tesla.events.side_effect = [TimeoutError("unavailable"), [event("TSLA")]]
        arguments = dict(calendar_service=self.calendar, sources=[apple, tesla], now=self.clock,
                         checkpoint_store=self.checkpoints)
        first = OfficialCalendarSyncService(**arguments).run_due()
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["sources"][1]["nextAttemptAt"], "2026-10-30T00:05:00Z")
        restarted = OfficialCalendarSyncService(**arguments)
        self.now += timedelta(minutes=4)
        self.assertFalse(restarted.due())
        self.now += timedelta(minutes=1)
        self.assertEqual(restarted.run_due()["status"], "ok")
        self.assertEqual(apple.events.call_count, 1)
        self.assertEqual(tesla.events.call_count, 2)
        self.assertEqual(self.calendar.quarantine_unverified_automatic_events.call_count, 1)
        self.assertEqual(restarted.source_states["TSLA"]["failureCount"], 0)

    def test_save_failure_has_backoff_and_retries_same_event_identity(self):
        item = source("AAPL")
        saver = Mock(side_effect=[RuntimeError("write failed"), None])
        service = OfficialCalendarSyncService(SimpleNamespace(save_event=saver), [item], now=self.clock)
        result = service.run_due()
        self.assertEqual(result["sources"][0]["coverageState"], "save-error")
        self.assertFalse(service.due())
        self.now += timedelta(minutes=5)
        self.assertEqual(service.run_due()["savedCount"], 1)
        self.assertEqual(saver.call_args_list[0], saver.call_args_list[1])

    def test_source_failures_back_off_without_becoming_absent_schedules(self):
        item = source("TSLA")
        item.events.side_effect = TimeoutError("unavailable")
        service = OfficialCalendarSyncService(self.calendar, [item], now=self.clock)
        for expected_minutes in [5, 10, 20, 40, 60, 60]:
            row = service.run_due()["sources"][0]
            self.assertEqual(row["coverageState"], "collection-error")
            self.assertEqual(datetime.fromisoformat(row["nextAttemptAt"].replace("Z", "+00:00")),
                             self.now + timedelta(minutes=expected_minutes))
            self.now += timedelta(minutes=expected_minutes)

    def test_no_upcoming_and_disabled_are_distinct_from_collection_failure(self):
        historical = source("AAPL", [event(date="2026-07-30T21:00:00Z")])
        disabled = source("DART", [])
        disabled.enabled = lambda: False
        rows = OfficialCalendarSyncService(self.calendar, [historical, disabled], now=self.clock).run_due()["sources"]
        self.assertEqual(rows[0]["coverageState"], "no-upcoming-confirmed")
        self.assertEqual(rows[1]["coverageState"], "disabled")
        self.assertEqual(len(self.calendar.events), 1)

    def test_failed_target_refresh_preserves_previous_plan(self):
        item = source("AAPL")
        reader = Mock(side_effect=[{"sources": [item]}, RuntimeError("private request details")])
        service = OfficialCalendarSyncService(self.calendar, source_reader=reader, now=self.clock)
        service.run_due()
        self.now += timedelta(minutes=1)
        result = service.run_due()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(service.sources, [item])
        self.assertEqual(item.events.call_count, 1)
        self.assertNotIn("private request details", str(result))

    def discovery(self, symbols, gateway):
        return InvestmentCalendarDiscoveryService(self.calendar, None, research_gateway=gateway,
            target_reader=lambda: {"holdingSymbols": symbols}, now=self.clock, checkpoint_store=self.checkpoints,
            settings={"investmentCalendarDiscoveryMaxSymbols": 2})

    def test_discovery_drains_unvisited_batches_and_new_targets_before_next_cycle(self):
        symbols = ["AAPL", "NVDA", "PLTR"]
        gateway = SimpleNamespace(collect_for_target=Mock(return_value=([], [])))
        service = self.discovery(symbols, gateway)
        first = service.run_due()
        self.assertEqual(first["symbols"], ["AAPL", "NVDA"])
        self.assertEqual(first["dueTargetCount"], 1)
        restarted = self.discovery(symbols, gateway)
        self.assertEqual(restarted.run_due()["symbols"], ["PLTR"])
        self.assertEqual(restarted.run_due()["status"], "not-due")
        symbols.append("TSLA")
        self.assertEqual(restarted.run_due()["symbols"], ["TSLA"])
        self.assertEqual(gateway.collect_for_target.call_count, 4)

    def test_discovery_retries_only_failed_symbols_and_prunes_removed_targets(self):
        symbols = ["AAPL", "NVDA"]
        gateway = SimpleNamespace(collect_for_target=Mock(side_effect=[TimeoutError(), ([], []), ([], [])]))
        service = self.discovery(symbols, gateway)
        self.assertEqual(service.run_due()["retrySymbols"], ["AAPL"])
        self.now += timedelta(minutes=5)
        restarted = self.discovery(symbols, gateway)
        self.assertEqual(restarted.run_due()["symbols"], ["AAPL"])
        symbols.remove("NVDA")
        restarted.run_due()
        self.assertNotIn("NVDA", restarted.target_states)

    def test_collection_disable_preserves_due_work_without_network_calls(self):
        item = source("AAPL")
        official = OfficialCalendarSyncService(self.calendar, [item], now=self.clock,
            settings={"investmentCalendarOfficialMacroSyncEnabled": "0"})
        self.assertFalse(official.due())
        self.assertEqual(official.run_once(force=True)["status"], "disabled")
        item.events.assert_not_called()
        gateway = SimpleNamespace(collect_for_target=Mock())
        discovery = self.discovery(["AAPL"], gateway)
        discovery.settings["investmentCalendarDiscoveryEnabled"] = "0"
        self.assertEqual(discovery.run_due()["status"], "disabled")
        gateway.collect_for_target.assert_not_called()

    def test_partition_status_does_not_spread_a_global_issuer_error(self):
        from digital_twin.modules.market_data.application.external_data.read_model_service import ExternalSignalsReadModelService
        from digital_twin.infrastructure.investment_research_gateway import ExistingApiResearchGateway
        from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget

        states = [
            {"datasetId": "issuer.ir_documents", "subjectKey": "AAPL", "active": True,
             "jobStatus": "failed", "lastError": "private diagnostic"},
            {"datasetId": "issuer.ir_documents", "subjectKey": "NVDA", "active": True,
             "jobStatus": "completed", "lastError": ""},
            {"datasetId": "issuer.ir_documents", "subjectKey": "NVDA", "active": False,
             "jobStatus": "failed", "lastError": "historical partition failure"},
            {"datasetId": "public-data.kr-security-master", "subjectKey": "NVDA", "active": True,
             "jobStatus": "failed", "lastError": "unrelated dataset"},
        ]
        store = SimpleNamespace(list_current=lambda _: [], collection_coverage_rows=lambda _: states,
            provider_statuses=lambda: [{"datasetId": "issuer.ir_documents", "providerId": "issuer-ir",
                                       "state": "failed", "lastError": "AAPL failed"}])
        read_model = ExternalSignalsReadModelService(store)
        provider = SimpleNamespace(signals_for_positions=lambda positions: read_model.signals_for_subjects([positions[0].symbol]))
        gateway = ExistingApiResearchGateway(provider=provider, collection_purposes={"disclosure", "investor-relations", "valuation"})
        for symbol, expected_ok in [("NVDA", True), ("AAPL", False)]:
            signals = read_model.signals_for_subjects([symbol])
            self.assertNotIn("private diagnostic", str(signals["externalDataPlatform"]["collectionStates"]))
            _, statuses = gateway.collect_for_target(NewsCollectionTarget(symbol=symbol, name=symbol, market="US"),
                                                     source_types=["official", "financial-data"])
            collection = [row for row in statuses if row.get("datasetId")]
            self.assertEqual(len(collection), 1)
            self.assertEqual(collection[0]["symbol"], symbol)
            self.assertEqual(collection[0]["ok"], expected_ok)

    def test_scoped_calendar_gateway_preserves_legacy_research_status_behavior(self):
        from digital_twin.infrastructure.investment_research_gateway import ExistingApiResearchGateway
        from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget

        global_error = {"source": "issuer-ir", "ok": False, "message": "provider-wide error"}
        provider = SimpleNamespace(signals_for_positions=lambda _: {
            "statuses": [global_error], "externalDataPlatform": {"enabled": True},
        })
        target = NewsCollectionTarget(symbol="NVDA", name="NVIDIA", market="US")
        _, legacy = ExistingApiResearchGateway(provider=provider).collect_for_target(target)
        self.assertIn(global_error, legacy)
        _, scoped = ExistingApiResearchGateway(provider=provider, collection_purposes={"investor-relations"}).collect_for_target(target)
        self.assertNotIn(global_error, scoped)
        self.assertEqual(scoped[0]["state"], "scope-unavailable")
        # Upgrade old retry state without waiting for its provider-wide backoff.
        symbols = ["AAPL", "NVDA"]
        collecting = SimpleNamespace(collect_for_target=Mock(side_effect=[TimeoutError(), ([], []), ([], [])]))
        previous = self.discovery(symbols, collecting)
        previous.run_due()
        upgraded = self.discovery(symbols, collecting)
        upgraded.status_scope_version = "target-purpose-v1"
        self.assertEqual(upgraded.run_due()["symbols"], ["AAPL"])
        self.assertEqual(upgraded.run_due()["status"], "not-due")
        checkpoint = next(iter(self.checkpoints.rows.values()))
        self.assertEqual(checkpoint["statusScopeVersion"], "target-purpose-v1")


if __name__ == "__main__":
    unittest.main()
