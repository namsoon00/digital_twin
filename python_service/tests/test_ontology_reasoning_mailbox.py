import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED, ontology_reasoning_requested_event


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
            for entry in rows:
                key = str(entry.get("mailboxKey") or "")
                current = self.slots.get(key)
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


class Reader:
    def __init__(self, events):
        self.events = list(events)

    def recent_events(self, **_kwargs):
        return list(self.events)


class Monitor:
    def __init__(self):
        self.accounts = []
        self.calls = []

    def run_once(self, force=False, symbol_filter=None):
        self.calls.append(list(symbol_filter or []))
        return []


def realtime_request(event_id, symbols, occurred_at, review_level="normal"):
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


class OntologyReasoningMailboxTests(unittest.TestCase):
    def build_runner(self, events, now=None, settings=None):
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


if __name__ == "__main__":
    unittest.main()
