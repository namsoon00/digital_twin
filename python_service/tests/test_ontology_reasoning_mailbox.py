import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED, ontology_reasoning_requested_event
from digital_twin.domain.ontology_reasoning_queue import durable_mailbox_entries
from digital_twin.infrastructure.mysql_reasoning_mailbox import (
    MySQLOntologyReasoningMailboxStore,
    local_reasoning_watch_is_dead,
    local_reasoning_watch_pid,
)


class MemoryCursor:
    def __init__(self):
        self.ids = []
        self.superseded = []
        self.payload = {}

    def processed_event_ids(self):
        return list(self.ids)

    def mark_processed(self, event_ids):
        for event_id in event_ids or []:
            if event_id and event_id not in self.ids:
                self.ids.append(event_id)

    def mark_superseded(self, event_ids):
        for event_id in event_ids or []:
            if event_id and event_id not in self.superseded:
                self.superseded.append(event_id)
            if event_id and event_id not in self.ids:
                self.ids.append(event_id)

    def load(self):
        return dict(self.payload)

    def save(self, payload):
        self.payload = dict(payload or {})


class MemoryMailbox:
    """Small in-memory contract double for the durable MySQL mailbox."""

    terminal_states = {"completed", "superseded", "expired"}

    def __init__(self):
        self.events = {}
        self.slots = {}

    def known_event_ids(self, event_ids):
        return [event_id for event_id in event_ids or [] if event_id in self.events]

    def terminal_event_states(self, event_ids=None):
        ids = event_ids if event_ids is not None else list(self.events)
        return {
            event_id: self.events[event_id]["state"]
            for event_id in ids or []
            if event_id in self.events and self.events[event_id]["state"] in self.terminal_states
        }

    @staticmethod
    def newer(incoming, current):
        return (
            str(incoming.get("occurredAt") or ""),
            str(incoming.get("sourceEventId") or ""),
        ) > (
            str(current.get("occurredAt") or ""),
            str(current.get("sourceEventId") or ""),
        )

    @staticmethod
    def same_revision(incoming, current):
        incoming_revision = str(incoming.get("factRevision") or "").strip()
        current_revision = str(current.get("factRevision") or "").strip()
        return bool(incoming_revision and incoming_revision == current_revision)

    def decrement(self, event_id, state):
        event = self.events.get(event_id)
        if not event:
            return ""
        event["unresolved"] = max(0, int(event["unresolved"]) - 1)
        if event["unresolved"] == 0:
            event["state"] = state
            return state
        return ""

    def enqueue(self, entries):
        result = {
            "acceptedEntryKeys": [],
            "sameRevisionEntryKeys": [],
            "knownEventIds": [],
            "terminalEventStates": {},
            "enqueuedEventIds": [],
        }
        grouped = {}
        for entry in entries or []:
            grouped.setdefault(str(entry.get("sourceEventId") or ""), []).append(dict(entry))
        for event_id, rows in grouped.items():
            if event_id in self.events:
                result["knownEventIds"].append(event_id)
                state = self.events[event_id]["state"]
                if state in self.terminal_states:
                    result["terminalEventStates"][event_id] = state
                continue
            accepted = 0
            same_revision_skips = 0
            for entry in rows:
                key = str(entry.get("mailboxKey") or "")
                current = self.slots.get(key)
                if current and self.same_revision(entry, current):
                    same_revision_skips += 1
                    result["sameRevisionEntryKeys"].append(key)
                    continue
                if current and not self.newer(entry, current):
                    continue
                if current:
                    displaced = str(current.get("sourceEventId") or "")
                    if displaced and displaced != event_id:
                        terminal = self.decrement(displaced, "superseded")
                        if terminal:
                            result["terminalEventStates"][displaced] = terminal
                self.slots[key] = dict(entry)
                accepted += 1
                result["acceptedEntryKeys"].append(key)
            self.events[event_id] = {
                "state": "pending" if accepted else "superseded",
                "unresolved": accepted,
            }
            result["enqueuedEventIds"].append(event_id)
            if not accepted:
                result["terminalEventStates"][event_id] = "superseded"
                self.events[event_id]["reason"] = (
                    "same fact revision already owns every mailbox slot"
                    if same_revision_skips == len(rows)
                    else "newer observation already owns every mailbox slot"
                )
        return result

    def pending(self, limit):
        rows = sorted(
            self.slots.values(),
            key=lambda item: (-int(item.get("priorityHint") or 0), str(item.get("occurredAt") or "")),
        )
        return [dict(item) for item in rows[:int(limit or 100)]]

    def acknowledge(self, entries, state="completed", reason=""):
        terminal = {}
        for raw in entries or []:
            entry = dict(raw) if isinstance(raw, dict) else {"mailboxKey": raw}
            key = str(entry.get("mailboxKey") or "")
            expected = str(entry.get("sourceEventId") or "")
            current = self.slots.get(key)
            if not current or (expected and expected != str(current.get("sourceEventId") or "")):
                continue
            event_id = str(current.get("sourceEventId") or "")
            self.slots.pop(key, None)
            final_state = self.decrement(event_id, state)
            if final_state:
                terminal[event_id] = final_state
        return terminal

    def summary(self):
        pending = [item for item in self.slots.values()]
        return {
            "enabled": True,
            "pendingEntryCount": len(pending),
            "oldestPendingAt": min([str(item.get("occurredAt") or "") for item in pending] or [""]),
            "eventStateCounts": {
                state: len([item for item in self.events.values() if item["state"] == state])
                for state in {item["state"] for item in self.events.values()}
            },
        }

    def prune_terminal(self, *_args, **_kwargs):
        return 0


class FastStateMailbox:
    def __init__(self):
        self.calls = 0

    def fast_state(self):
        self.calls += 1
        return {
            "enabled": True,
            "pendingEntryCount": 2,
            "runningEntryCount": 1,
            "retryingEntryCount": 1,
            "pendingSymbolCount": 2,
            "pendingSymbols": ["AAPL", "MSFT"],
            "oldestPendingAt": "2026-07-24T00:00:00Z",
            "lastStage": "typedb-projection-complete",
        }


class TimeoutRecoveringMailbox(MemoryMailbox):
    def __init__(self):
        super().__init__()
        self.recoveries = []
        self.timeouts = []

    def recover_worker_timeout(self, worker_id, retry_after_seconds, reason):
        self.recoveries.append((worker_id, retry_after_seconds, reason))
        return 1

    def record_timeout(self, details):
        self.timeouts.append(dict(details or {}))


class MySQLCursor:
    def __init__(self, row=None):
        self.row = dict(row or {}) if row else None

    def fetchone(self):
        return dict(self.row) if self.row else None


class MySQLTransaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


class MySQLMailboxConnection:
    """Minimal SQL contract double for the same-revision mailbox path."""

    def __init__(self):
        self.events = {}
        self.slots = {}

    def execute(self, sql, params=()):
        query = " ".join(str(sql).split())
        values = tuple(params or ())
        if query.startswith("SELECT state FROM ontology_reasoning_mailbox_events"):
            event = self.events.get(str(values[0]))
            return MySQLCursor({"state": event["state"]} if event else None)
        if "FROM ontology_reasoning_mailbox mailbox LEFT JOIN ontology_reasoning_mailbox_events" in query:
            slot = self.slots.get(str(values[0]))
            if not slot:
                return MySQLCursor()
            event = self.events.get(slot["source_event_id"], {})
            return MySQLCursor({**slot, "event_json": event.get("event_json", "")})
        if query.startswith("INSERT INTO ontology_reasoning_mailbox_events"):
            self.events[str(values[0])] = {
                "state": str(values[2]),
                "unresolved": int(values[3]),
                "reason": str(values[4]),
                "event_json": str(values[5]),
            }
            return MySQLCursor()
        if query.startswith("UPDATE ontology_reasoning_mailbox_events SET occurred_at"):
            event = self.events[str(values[6])]
            event.update({
                "state": str(values[1]),
                "unresolved": int(values[2]),
                "reason": str(values[3]),
                "event_json": str(values[4]),
            })
            return MySQLCursor()
        if query.startswith("INSERT IGNORE INTO ontology_reasoning_mailbox_events"):
            self.events.setdefault(str(values[0]), {
                "state": str(values[2]),
                "unresolved": 0,
                "reason": str(values[3]),
                "event_json": str(values[4]),
            })
            return MySQLCursor()
        if query.startswith("INSERT INTO ontology_reasoning_mailbox ("):
            self.slots[str(values[0])] = {
                "source_event_id": str(values[1]),
                "occurred_at": str(values[8]),
            }
            return MySQLCursor()
        raise AssertionError("unexpected SQL: " + query)


class Reader:
    def __init__(self, events):
        self.events = list(events)

    def recent_events(self, **_kwargs):
        return list(self.events)


class FailingReader:
    def recent_events(self, **_kwargs):
        raise AssertionError("durable queue probe must not scan domain events")


class Monitor:
    def __init__(self):
        self.accounts = []
        self.calls = []

    def run_once(self, force=False, symbol_filter=None):
        self.calls.append(list(symbol_filter or []))
        return []


def realtime_request(event_id, symbols, occurred_at, review_level="normal", fact_revision=""):
    source = DomainEvent(
        name="market_data.collected",
        aggregate_id="market:KR",
        occurred_at=occurred_at,
        payload={"sourceObservedAt": occurred_at, "symbols": list(symbols)},
    )
    request = ontology_reasoning_requested_event(
        source,
        "market-data-update",
        symbols,
        changed_count=len(symbols),
        fact_types=["MarketQuote"],
        fact_revisions_by_symbol={symbol: fact_revision for symbol in symbols} if fact_revision else None,
    )
    payload = dict(request.payload or {})
    if review_level != "normal":
        payload["materialityAssessments"] = [{"reviewLevel": review_level}]
    return DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id=request.aggregate_id,
        payload=payload,
        occurred_at=occurred_at,
        event_id=event_id,
    )


def research_evidence_request(
    event_id,
    symbols,
    occurred_at,
    trigger="research-evidence-update",
    research_run_id="",
    reasoning_handoff=None,
):
    source = DomainEvent(
        name="research.evidence.collected",
        aggregate_id="research:" + ",".join(symbols),
        occurred_at=occurred_at,
        payload={"sourceObservedAt": occurred_at, "symbols": list(symbols)},
    )
    request = ontology_reasoning_requested_event(
        source,
        trigger,
        symbols,
        changed_count=len(symbols),
        fact_types=["NewsEvent", "ResearchEvidence"],
    )
    payload = dict(request.payload or {})
    if research_run_id:
        payload["researchRunId"] = research_run_id
    if reasoning_handoff:
        payload["reasoningHandoff"] = dict(reasoning_handoff)
    return DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id=request.aggregate_id,
        payload=payload,
        occurred_at=occurred_at,
        event_id=event_id,
    )


class OntologyReasoningMailboxTests(unittest.TestCase):
    def build_runner(self, events, now=None, settings=None, event_publisher=None):
        self.cursor = MemoryCursor()
        self.mailbox = MemoryMailbox()
        self.monitor = Monitor()
        runtime_settings = {
            "ontologyReasoningEnabled": "1",
            "ontologyReasoningMailboxEnabled": "1",
            "ontologyReasoningSourceFreshnessEnabled": "0",
            "ontologyReasoningMaxSymbolsPerRun": "1",
            "ontologyReasoningTypeDbNativeRuleExecutionEnabled": "1",
            "typedbNativeRuleTargetSymbolLimit": "1",
            "ontologyRuleCandidateAiEnabled": "0",
        }
        runtime_settings.update(settings or {})
        return OntologyReasoningRunner(
            event_reader=Reader(events),
            cursor_store=self.cursor,
            monitor_runner_factory=lambda: self.monitor,
            event_publisher=event_publisher,
            settings=runtime_settings,
            mailbox_store=self.mailbox,
            now_provider=now or (lambda: datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc)),
        )

    def test_newer_realtime_observation_replaces_older_source_before_typedb(self):
        old = realtime_request("old", ["AAPL"], "2026-07-24T00:00:00Z")
        newest = realtime_request("new", ["AAPL"], "2026-07-24T00:01:00Z")
        runner = self.build_runner([old, newest])

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertIn("old", self.cursor.superseded)
        self.assertIn("new", self.cursor.ids)
        self.assertEqual("superseded", self.mailbox.events["old"]["state"])
        self.assertEqual(0, result["mailbox"]["pendingEntryCount"])
        self.assertEqual("ok", result["executionTelemetry"]["status"])

    def test_generic_research_evidence_updates_coalesce_per_symbol_before_typedb(self):
        old = research_evidence_request("research-old", ["AAPL", "MSFT"], "2026-07-24T00:00:00Z")
        newest_aapl = research_evidence_request("research-aapl", ["AAPL"], "2026-07-24T00:01:00Z")
        newest_msft = research_evidence_request("research-msft", ["MSFT"], "2026-07-24T00:02:00Z")
        runner = self.build_runner([old, newest_aapl, newest_msft])

        first = runner.run_once(force=True)

        self.assertIn("research-old", self.cursor.superseded)
        self.assertEqual("superseded", self.mailbox.events["research-old"]["state"])
        self.assertEqual(1, first["mailbox"]["pendingEntryCount"])
        self.assertEqual(1, len(self.monitor.calls))

        second = runner.run_once(force=True)

        self.assertEqual(2, len(self.monitor.calls))
        self.assertEqual(0, second["mailbox"]["pendingEntryCount"])
        self.assertIn("research-aapl", self.cursor.ids)
        self.assertIn("research-msft", self.cursor.ids)

    def test_hypothesis_research_handoff_does_not_enter_latest_state_mailbox(self):
        event = research_evidence_request(
            "hypothesis-research",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            trigger="hypothesis-research-update",
            research_run_id="research-run-1",
            reasoning_handoff={"status": "requested", "requestId": "handoff-1"},
        )
        runner = self.build_runner([event])

        self.assertEqual([], runner.mailbox_entries_for_event(event))

    def test_news_analysis_enrichment_coalesces_to_the_latest_evidence_state(self):
        event = research_evidence_request(
            "news-analysis",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            trigger="news-analysis-enrichment",
        )
        runner = self.build_runner([])

        application_entry = runner.mailbox_entries_for_event(event)[0]
        ingress_entry = durable_mailbox_entries(event)[0]

        self.assertEqual(application_entry["mailboxKey"], ingress_entry["mailboxKey"])
        self.assertEqual("AAPL", ingress_entry["symbol"])
        self.assertEqual("news-analysis-enrichment", ingress_entry["trigger"])

    def test_same_fact_revision_keeps_existing_pending_mailbox_slot(self):
        old = realtime_request("old-revision", ["AAPL"], "2026-07-24T00:00:00Z", fact_revision="fact-revision:aapl-v1")
        duplicate = realtime_request("duplicate-revision", ["AAPL"], "2026-07-24T00:01:00Z", fact_revision="fact-revision:aapl-v1")
        runner = self.build_runner([old, duplicate])

        result = runner.run_once(force=True)

        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertIn("old-revision", self.cursor.ids)
        self.assertIn("duplicate-revision", self.cursor.superseded)
        self.assertEqual("completed", self.mailbox.events["old-revision"]["state"])
        self.assertEqual("superseded", self.mailbox.events["duplicate-revision"]["state"])
        self.assertEqual(
            "same fact revision already owns every mailbox slot",
            self.mailbox.events["duplicate-revision"]["reason"],
        )
        self.assertEqual(1, result["sameRevisionEntryCount"])
        self.assertEqual(0, result["mailbox"]["pendingEntryCount"])

    def test_mysql_mailbox_does_not_replace_a_pending_slot_with_same_revision(self):
        old = realtime_request("mysql-old", ["AAPL"], "2026-07-24T00:00:00Z", fact_revision="fact-revision:aapl-v1")
        duplicate = realtime_request("mysql-duplicate", ["AAPL"], "2026-07-24T00:01:00Z", fact_revision="fact-revision:aapl-v1")
        runner = self.build_runner([])
        connection = MySQLMailboxConnection()
        store = MySQLOntologyReasoningMailboxStore.__new__(MySQLOntologyReasoningMailboxStore)
        store.transaction = lambda: MySQLTransaction(connection)

        old_entry = runner.mailbox_entries_for_event(old)[0]
        duplicate_entry = runner.mailbox_entries_for_event(duplicate)[0]
        first = store.enqueue([old_entry])
        second = store.enqueue([duplicate_entry])

        self.assertEqual([old_entry["mailboxKey"]], first["acceptedEntryKeys"])
        self.assertEqual([duplicate_entry["mailboxKey"]], second["sameRevisionEntryKeys"])
        self.assertEqual({"mysql-duplicate": "superseded"}, second["terminalEventStates"])
        self.assertEqual("mysql-old", connection.slots[old_entry["mailboxKey"]]["source_event_id"])
        self.assertEqual("same fact revision already owns every mailbox slot", connection.events["mysql-duplicate"]["reason"])

    def test_atomic_ingress_marks_non_fungible_request_as_direct_pending(self):
        event = research_evidence_request(
            "handoff-direct",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            trigger="hypothesis-research-update",
            research_run_id="research-run-1",
            reasoning_handoff={"status": "requested", "requestId": "handoff-1"},
        )
        connection = MySQLMailboxConnection()

        result = MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(connection, event)

        self.assertEqual("direct", result["ingressKind"])
        self.assertEqual("direct-pending", connection.events["handoff-direct"]["state"])

    def test_orphan_recovery_only_accepts_confirmed_dead_local_scheduler_owners(self):
        self.assertEqual(451, local_reasoning_watch_pid("reasoning-watch:local:451", hostname="local"))
        self.assertEqual(452, local_reasoning_watch_pid("reasoning-watch:452", hostname="local"))
        self.assertEqual(0, local_reasoning_watch_pid("reasoning-watch:remote:453", hostname="local"))
        self.assertTrue(
            local_reasoning_watch_is_dead(
                "reasoning-watch:local:451",
                hostname="local",
                process_alive=lambda _pid: False,
            )
        )
        self.assertFalse(
            local_reasoning_watch_is_dead(
                "reasoning-watch:local:451",
                hostname="local",
                process_alive=lambda _pid: True,
            )
        )
        self.assertFalse(
            local_reasoning_watch_is_dead(
                "reasoning-watch:remote:453",
                hostname="local",
                process_alive=lambda _pid: False,
            )
        )

    def test_legacy_direct_news_marker_migrates_into_a_latest_state_mailbox_slot(self):
        event = research_evidence_request(
            "legacy-news-direct",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            trigger="news-analysis-enrichment",
        )
        connection = MySQLMailboxConnection()
        store = MySQLOntologyReasoningMailboxStore.__new__(MySQLOntologyReasoningMailboxStore)
        store.transaction = lambda: MySQLTransaction(connection)

        # Simulate a row written by the pre-coalescing ingress version.
        store._record_direct_event_with_connection(
            connection,
            event,
            state="direct-pending",
            reason="legacy generic research event",
        )
        migrated = store.enqueue(durable_mailbox_entries(event))
        entry = durable_mailbox_entries(event)[0]

        self.assertEqual([entry["mailboxKey"]], migrated["acceptedEntryKeys"])
        self.assertEqual("pending", connection.events["legacy-news-direct"]["state"])
        self.assertEqual("legacy-news-direct", connection.slots[entry["mailboxKey"]]["source_event_id"])

    def test_claim_store_error_defers_instead_of_running_unleased_typedb_work(self):
        class FailingClaimMailbox(MemoryMailbox):
            def claim(self, *_args, **_kwargs):
                return {"enabled": False, "claimed": [], "blocked": [], "reason": "MySQL unavailable"}

        event = realtime_request("lease-error", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner([event])
        runner.mailbox_store = FailingClaimMailbox()

        result = runner.run_once(force=True)

        self.assertEqual("deferred", result["status"])
        self.assertEqual([], self.monitor.calls)
        self.assertIn("lease", result["deferredReason"])

    def test_durable_fast_state_avoids_any_event_log_scan(self):
        mailbox = FastStateMailbox()
        runner = OntologyReasoningRunner(
            event_reader=FailingReader(),
            cursor_store=MemoryCursor(),
            monitor_runner_factory=lambda: Monitor(),
            settings={"ontologyReasoningEnabled": "1", "ontologyReasoningMailboxEnabled": "1"},
            mailbox_store=mailbox,
        )

        state = runner.lightweight_queue_state()

        self.assertEqual("durable-mailbox-state-v2", state["probeMode"])
        self.assertEqual(2, state["effectivePendingCount"])
        self.assertEqual(1, state["runningEntryCount"])
        self.assertEqual(["AAPL", "MSFT"], state["pendingSymbols"])
        self.assertEqual(1, mailbox.calls)

    def test_timeout_recovers_only_the_isolated_worker_lease(self):
        runner = self.build_runner([])
        mailbox = TimeoutRecoveringMailbox()
        runner.mailbox_store = mailbox

        result = runner.record_execution_timeout(240, worker_id="reasoning-watch:test")

        self.assertEqual("timeout", result["status"])
        self.assertEqual(1, result["executionTelemetry"]["mailboxTimeoutRecoveryCount"])
        self.assertEqual("reasoning-watch:test", mailbox.recoveries[0][0])
        self.assertEqual(300, mailbox.recoveries[0][1])
        self.assertEqual(1, len(mailbox.timeouts))

    def test_actionable_quote_snapshots_still_keep_only_the_latest_state(self):
        old = realtime_request("old-act", ["AAPL"], "2026-07-24T00:00:00Z", review_level="act")
        newest = realtime_request("new-act", ["AAPL"], "2026-07-24T00:01:00Z", review_level="act")
        runner = self.build_runner([old, newest])

        result = runner.run_once(force=True)

        self.assertEqual([['AAPL']], self.monitor.calls)
        self.assertIn("old-act", self.cursor.superseded)
        self.assertIn("new-act", self.cursor.ids)
        self.assertEqual("ok", result["status"])

    def test_source_event_is_completed_only_after_each_symbol_mailbox_row_finishes(self):
        event = realtime_request("two-symbols", ["AAPL", "MSFT"], "2026-07-24T00:00:00Z")
        runner = self.build_runner([event])

        first = runner.run_once(force=True)

        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertNotIn("two-symbols", self.cursor.ids)
        self.assertEqual(1, first["mailbox"]["pendingEntryCount"])

        second = runner.run_once(force=True)

        self.assertEqual([["AAPL"], ["MSFT"]], self.monitor.calls)
        self.assertIn("two-symbols", self.cursor.ids)
        self.assertEqual(0, second["mailbox"]["pendingEntryCount"])

    def test_completion_telemetry_includes_only_the_request_scheduled_this_cycle(self):
        class Publisher:
            def __init__(self):
                self.events = []

            def publish(self, event):
                self.events.append(event)

        publisher = Publisher()
        first = realtime_request("first", ["AAPL"], "2026-07-24T00:00:00Z")
        second = realtime_request("second", ["MSFT"], "2026-07-24T00:01:00Z")
        runner = self.build_runner([first, second], event_publisher=publisher)

        result = runner.run_once(force=True)

        self.assertEqual(1, result["scheduledRequestCount"])
        self.assertEqual(1, result["processedCount"])
        self.assertEqual(1, len(self.monitor.calls))
        self.assertEqual(1, len(publisher.events))
        self.assertEqual(1, len(publisher.events[0].payload["triggerEventIds"]))
        self.assertEqual(1, len(self.cursor.ids))

    def test_coherent_snapshot_fills_the_native_target_cap_across_source_events(self):
        first = realtime_request("first", ["AAPL"], "2026-07-24T00:00:00Z")
        second = realtime_request("second", ["MSFT"], "2026-07-24T00:01:00Z")
        third = realtime_request("third", ["NVDA"], "2026-07-24T00:02:00Z")
        runner = self.build_runner(
            [first, second, third],
            settings={
                "ontologyReasoningMaxSymbolsPerRun": "2",
                "typedbNativeRuleTargetSymbolLimit": "2",
            },
        )

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual(2, result["scheduledRequestCount"])
        self.assertEqual(2, len(result["symbols"]))
        self.assertEqual(1, result["omittedSymbolCount"])
        self.assertEqual(1, len(self.monitor.calls))
        self.assertEqual(set(result["symbols"]), set(self.monitor.calls[0]))
        self.assertEqual(2, len(self.cursor.ids))
        self.assertEqual(1, result["mailbox"]["pendingEntryCount"])

    def test_pending_mailbox_slot_without_a_due_symbol_does_not_run_the_whole_portfolio(self):
        event = realtime_request("waiting", ["AAPL"], "2026-07-24T00:04:00Z")
        runner = self.build_runner(
            [event],
            settings={"ontologyReasoningMinIntervalSeconds": "180"},
        )
        self.cursor.payload["lastReasonedAtBySymbol"] = {"AAPL": "2026-07-24T00:04:00Z"}

        result = runner.run_once(force=True)

        self.assertEqual("cooldown", result["status"])
        self.assertEqual(0, result["scheduledRequestCount"])
        self.assertEqual([], self.monitor.calls)
        self.assertEqual(1, result["mailbox"]["pendingEntryCount"])

    def test_stale_realtime_input_is_expired_before_projection(self):
        stale = realtime_request("stale", ["AAPL"], "2026-07-23T00:00:00Z")
        runner = self.build_runner(
            [stale],
            settings={
                "ontologyReasoningSourceFreshnessEnabled": "1",
                "ontologyReasoningRealtimeEventMaxAgeMinutes": "15",
            },
        )

        result = runner.run_once(force=True)

        self.assertEqual("idle", result["status"])
        self.assertEqual([], self.monitor.calls)
        self.assertIn("stale", self.cursor.superseded)
        self.assertEqual(1, result["staleRequestCount"])
        self.assertEqual(0, result["mailbox"]["pendingEntryCount"])

    def test_stale_news_enrichment_is_expired_before_projection(self):
        stale = research_evidence_request(
            "stale-news",
            ["AAPL"],
            "2026-07-23T00:00:00Z",
            trigger="news-analysis-enrichment",
        )
        runner = self.build_runner(
            [stale],
            settings={
                "ontologyReasoningSourceFreshnessEnabled": "1",
                "ontologyReasoningResearchEventMaxAgeMinutes": "60",
            },
        )

        result = runner.run_once(force=True)

        self.assertEqual("idle", result["status"])
        self.assertEqual([], self.monitor.calls)
        self.assertIn("stale-news", self.cursor.superseded)
        self.assertEqual("expired", self.mailbox.events["stale-news"]["state"])
        self.assertEqual(1, result["staleRequestCount"])
        self.assertEqual(0, result["mailbox"]["pendingEntryCount"])

    def test_old_projection_acknowledgement_cannot_delete_a_newer_mailbox_observation(self):
        old = realtime_request("old", ["AAPL"], "2026-07-24T00:00:00Z")
        newest = realtime_request("new", ["AAPL"], "2026-07-24T00:01:00Z")
        runner = self.build_runner([old])

        runner.synchronize_mailbox([old])
        old_entry = self.mailbox.pending(1)[0]
        runner.synchronize_mailbox([newest])
        terminal = self.mailbox.acknowledge([{
            "mailboxKey": old_entry["mailboxKey"],
            "sourceEventId": old_entry["sourceEventId"],
        }])

        current = self.mailbox.pending(1)[0]
        self.assertEqual({}, terminal)
        self.assertEqual("new", current["sourceEventId"])

    def test_status_exposes_mailbox_freshness_and_execution_history(self):
        event = realtime_request("status", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner([event])

        runner.run_once(force=True)
        status = runner.status()

        self.assertTrue(status["mailbox"]["enabled"])
        self.assertIn("sourceFreshness", status)
        self.assertTrue(status["executionTelemetry"]["last"])

    def test_lightweight_queue_state_avoids_account_priority_and_reports_pending_work(self):
        event = realtime_request("lightweight", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner([event])
        runner.priority_symbols_provider = lambda: (_ for _ in ()).throw(
            AssertionError("lightweight queue probe must not load account priority")
        )

        state = runner.lightweight_queue_state()

        self.assertEqual("pending", state["status"])
        self.assertEqual("lightweight-event-mailbox", state["probeMode"])
        self.assertEqual(1, state["effectivePendingCount"])
        self.assertEqual(["AAPL"], state["pendingSymbols"])
        self.assertEqual("2026-07-24T00:00:00Z", state["oldestRequestAt"])

    def test_queue_dispatch_explains_selected_work_without_changing_priority(self):
        market = realtime_request("market", ["AAPL"], "2026-07-24T00:00:00Z")
        source = DomainEvent(
            name="research.evidence.collected",
            aggregate_id="research:MSFT",
            occurred_at="2026-07-24T00:01:00Z",
            payload={"sourceObservedAt": "2026-07-24T00:01:00Z", "symbols": ["MSFT"]},
        )
        requested = ontology_reasoning_requested_event(
            source,
            "research-evidence-update",
            ["MSFT"],
            changed_count=1,
            fact_types=["ResearchEvidence"],
        )
        research = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id=requested.aggregate_id,
            payload=dict(requested.payload or {}),
            occurred_at="2026-07-24T00:01:00Z",
            event_id="research",
        )
        runner = self.build_runner([market, research])

        status = runner.status()
        dispatch = status["queueDispatch"]

        self.assertEqual(1, dispatch["pendingByClass"]["realtime-market"])
        self.assertEqual(1, dispatch["pendingByClass"]["research"])
        self.assertEqual(["research"], dispatch["selectedWorkClasses"])
        self.assertEqual(["MSFT"], dispatch["selectedSymbols"])


if __name__ == "__main__":
    unittest.main()
