import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED, ontology_reasoning_requested_event
from digital_twin.domain.ontology_reasoning_queue import durable_mailbox_entries
from digital_twin.domain.portfolio import AlertEvent
from digital_twin.infrastructure.mysql_reasoning_mailbox import (
    MySQLOntologyReasoningMailboxStore,
    local_reasoning_watch_is_dead,
    local_reasoning_watch_pid,
)
from digital_twin.infrastructure.mysql_schema_tuning import MYSQL_OPERATIONAL_COLUMNS


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
        self.direct_terminalizations = []

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
                    current["priorityHint"] = max(
                        int(current.get("priorityHint") or 0),
                        int(entry.get("priorityHint") or 0),
                    )
                    current["observationFollowup"] = bool(
                        current.get("observationFollowup")
                        or entry.get("observationFollowup")
                    )
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
                    entry = {
                        **entry,
                        "priorityHint": max(
                            int(current.get("priorityHint") or 0),
                            int(entry.get("priorityHint") or 0),
                        ),
                        "observationFollowup": bool(
                            current.get("observationFollowup")
                            or entry.get("observationFollowup")
                        ),
                    }
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

    def current_entries_with_connection(self, _connection, entries, lock_rows=True):
        del lock_rows
        result = {"status": "ok", "current": [], "superseded": [], "missing": []}
        for entry in entries or []:
            key = str(entry.get("mailboxKey") or "")
            expected = str(entry.get("sourceEventId") or "")
            current = self.slots.get(key)
            symbol = str((current or entry).get("symbol") or "").upper()
            item = {
                "mailboxKey": key,
                "sourceEventId": expected,
                "currentSourceEventId": str((current or {}).get("sourceEventId") or ""),
                "symbol": symbol,
            }
            if not current:
                result["missing"].append({**item, "reason": "mailbox-row-missing"})
            elif item["currentSourceEventId"] == expected:
                result["current"].append(item)
            else:
                result["superseded"].append({**item, "reason": "newer-source-event"})
        result["currentCount"] = len(result["current"])
        result["supersededCount"] = len(result["superseded"])
        result["missingCount"] = len(result["missing"])
        return result

    def prune_terminal(self, *_args, **_kwargs):
        return 0

    def terminalize_direct_events(self, events, state="completed", reason=""):
        event_ids = [
            str(getattr(event, "event_id", event) or "").strip()
            for event in events or []
        ]
        event_ids = [event_id for event_id in event_ids if event_id]
        self.direct_terminalizations.append({
            "eventIds": event_ids,
            "state": state,
            "reason": reason,
        })
        return event_ids


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


class CheckpointTimeoutRecoveringMailbox(TimeoutRecoveringMailbox):
    """Timeout mailbox double with an active durable reasoning checkpoint."""

    def __init__(self, stage):
        super().__init__()
        self.stage = str(stage or "")

    def summary(self):
        return {
            **super().summary(),
            "runningEntryCount": 1,
            "activeCheckpoint": {"stage": self.stage},
        }


class PostMonitorCheckpointMailbox(MemoryMailbox):
    """Mailbox double that models a retry after monitor persistence committed."""

    def claim(self, entries, _worker_id, _lease_seconds):
        claimed = [dict(item) for item in entries or []]
        return {
            "enabled": True,
            "claimed": claimed,
            "blocked": [],
            "resumed": [{
                **item,
                "resumedFrom": "post-monitor-inference-verified",
                "checkpoint": {"stage": "post-monitor-inference-verified"},
            } for item in claimed],
        }

    def checkpoint(self, *_args, **_kwargs):
        return None


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
        self.work_items = {}

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
        if query.startswith("INSERT INTO ontology_reasoning_work_items"):
            self.work_items[str(values[0])] = {
                "source_event_id": str(values[1]),
                "work_state": "queued",
                "checkpoint_json": str(values[3]),
            }
            return MySQLCursor()
        if query.startswith("INSERT INTO ontology_reasoning_mailbox ("):
            self.slots[str(values[0])] = {
                "source_event_id": str(values[1]),
                "occurred_at": str(values[14]),
                "priority_hint": int(values[13]),
            }
            return MySQLCursor()
        if query.startswith("UPDATE ontology_reasoning_mailbox SET priority_hint"):
            self.slots[str(values[2])]["priority_hint"] = int(values[0])
            return MySQLCursor()
        if query.startswith("UPDATE ontology_reasoning_mailbox SET source_event_id"):
            self.slots[str(values[15])].update({
                "source_event_id": str(values[0]),
                "occurred_at": str(values[13]),
                "priority_hint": int(values[12]),
            })
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
        self.reasoning_contexts = []

    def run_once(self, force=False, symbol_filter=None, reasoning_context=None):
        self.calls.append(list(symbol_filter or []))
        self.reasoning_contexts.append(dict(reasoning_context or {}))
        return []


def realtime_request(
    event_id,
    symbols,
    occurred_at,
    review_level="normal",
    fact_revision="",
    observation_followup_symbols=None,
):
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
        observation_followup_symbols=observation_followup_symbols,
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


def portfolio_risk_request(
    event_id,
    symbols,
    occurred_at,
    fact_revision="",
    trigger="portfolio-risk-change",
):
    source = DomainEvent(
        name="portfolio.risk_observed",
        aggregate_id="portfolio:local:default",
        occurred_at=occurred_at,
        payload={"sourceObservedAt": occurred_at, "symbols": list(symbols)},
    )
    request = ontology_reasoning_requested_event(
        source,
        trigger,
        changed_count=1,
        fact_types=["PortfolioRiskSnapshot", "PositionRiskMetric", "RebalanceScenario"],
        subject_kind="PORTFOLIO",
        subject_id="portfolio:local:default",
        affected_symbols=symbols,
        subject_revision=fact_revision,
        subject_changed_fields=["portfolioRisk", "positionRisk", "rebalanceScenario"],
        account_id="local:default",
    )
    return DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id=request.aggregate_id,
        payload=request.payload,
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
    fact_types=None,
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
        fact_types=list(fact_types or ["NewsEvent", "ResearchEvidence"]),
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
    def build_runner(
        self,
        events,
        now=None,
        settings=None,
        event_publisher=None,
        activity_probe=None,
        prewarm_state_writer=None,
        maintenance_yield_probe=None,
    ):
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
            rulebox_prewarm_activity_probe=activity_probe,
            rulebox_prewarm_state_writer=prewarm_state_writer,
            maintenance_yield_state_probe=maintenance_yield_probe,
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

    def test_newer_portfolio_risk_snapshot_replaces_older_source_before_typedb(self):
        old = portfolio_risk_request(
            "old-risk", ["000660", "MSTR"], "2026-07-24T00:00:00Z", "risk:1",
        )
        newest = portfolio_risk_request(
            "new-risk", ["000660", "MSTR"], "2026-07-24T00:01:00Z", "risk:2",
        )
        entries = durable_mailbox_entries(newest)
        runner = self.build_runner([old, newest])

        first = runner.run_once(force=True)
        second = runner.run_once(force=True)

        self.assertEqual(1, len(entries))
        self.assertEqual({"PORTFOLIO"}, {entry["workClass"] for entry in entries})
        self.assertEqual({"PORTFOLIO"}, {entry["subjectKind"] for entry in entries})
        self.assertEqual({"portfolio:local:default"}, {entry["subjectId"] for entry in entries})
        self.assertEqual({"local:default"}, {entry["accountScope"] for entry in entries})
        self.assertEqual(["000660", "MSTR"], entries[0]["affectedSymbols"])
        self.assertEqual(
            {("PortfolioRiskSnapshot", "PositionRiskMetric", "RebalanceScenario")},
            {tuple(entry["ruleFamilies"]) for entry in entries},
        )
        self.assertEqual([["000660", "MSTR"]], self.monitor.calls)
        self.assertIn("old-risk", self.cursor.superseded)
        self.assertIn("new-risk", self.cursor.ids)
        self.assertEqual("superseded", self.mailbox.events["old-risk"]["state"])
        self.assertEqual("ok", first["status"])
        self.assertEqual("idle", second["status"])

    def test_overdue_portfolio_aggregate_runs_before_newer_market_revision(self):
        portfolio = portfolio_risk_request(
            "portfolio-overdue", ["000660", "MSTR"], "2026-07-24T00:00:00Z", "risk:1",
        )
        market = realtime_request("market-newer", ["000660"], "2026-07-24T00:15:00Z")
        runner = self.build_runner(
            [portfolio, market],
            now=lambda: datetime(2026, 7, 24, 0, 16, tzinfo=timezone.utc),
        )

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual(["PORTFOLIO"], result["queueDispatch"]["selectedWorkClasses"])
        self.assertTrue(result["queueDispatch"]["eventFairnessReservationActive"])
        self.assertEqual([["000660", "MSTR"]], self.monitor.calls)
        self.assertEqual("completed", self.mailbox.events["portfolio-overdue"]["state"])
        self.assertEqual("pending", self.mailbox.events["market-newer"]["state"])

    def test_urgent_portfolio_transition_has_holding_priority_without_a_ticker(self):
        portfolio = portfolio_risk_request(
            "portfolio-transition",
            ["000660", "MSTR"],
            "2026-07-24T00:00:00Z",
            "rebalance:1",
            trigger="portfolio-rebalance-transition",
        )
        market = realtime_request("market-newer", ["000660"], "2026-07-24T00:01:00Z")
        runner = self.build_runner(
            [portfolio, market],
            now=lambda: datetime(2026, 7, 24, 0, 2, tzinfo=timezone.utc),
        )

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual(["PORTFOLIO"], result["queueDispatch"]["selectedWorkClasses"])
        self.assertFalse(result["queueDispatch"]["eventFairnessReservationActive"])
        self.assertEqual([["000660", "MSTR"]], self.monitor.calls)

    def test_market_observation_anchor_completes_only_after_verified_projection(self):
        event = realtime_request("anchor-event", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner([event])
        completions = []
        runner.market_observation_completion_recorder = lambda event_ids, account_ids, symbols: (
            completions.append({
                "eventIds": list(event_ids or []),
                "accountIds": list(account_ids or []),
                "symbols": list(symbols or []),
            })
            or {"status": "completed", "completedCount": 1}
        )

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual([{
            "eventIds": ["anchor-event"],
            "accountIds": [],
            "symbols": ["AAPL"],
        }], completions)
        self.assertEqual(1, result["marketObservationAnchorCompletion"]["completedCount"])

    def test_verified_abox_yield_defers_before_starting_a_new_monitor_projection(self):
        request = realtime_request("yield", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner(
            [request],
            settings={"ontologyAboxMaintenanceYieldEnabled": "1"},
            maintenance_yield_probe=lambda: {
                "maintenanceYieldRequest": {
                    "requestedAt": "2026-07-24T00:05:00Z",
                    "expiresAt": "2026-07-24T00:05:30Z",
                    "worldId": "portfolio:local:main",
                    "inactiveManifestCount": 20,
                },
                "maintenanceYieldLastRequestedAt": "2026-07-24T00:05:00Z",
            },
        )

        result = runner.run_once(force=True)

        self.assertEqual("deferred", result["status"])
        self.assertEqual("maintenance-window", result["deferredKind"])
        self.assertEqual(30, result["retryAfterSeconds"])
        self.assertTrue(result["maintenanceYield"]["active"])
        self.assertEqual([], self.monitor.calls)

    def test_abox_yield_does_not_block_reasoning_after_bounded_window(self):
        request = realtime_request("yield-expired", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner(
            [request],
            now=lambda: datetime(2026, 7, 24, 0, 7, tzinfo=timezone.utc),
            settings={"ontologyAboxMaintenanceYieldEnabled": "1"},
            maintenance_yield_probe=lambda: {
                "maintenanceYieldRequest": {
                    "requestedAt": "2026-07-24T00:05:00Z",
                    "expiresAt": "2026-07-24T00:12:00Z",
                    "worldId": "portfolio:local:main",
                    "inactiveManifestCount": 20,
                },
                "maintenanceYieldLastRequestedAt": "2026-07-24T00:05:00Z",
            },
        )

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual([["AAPL"]], self.monitor.calls)

    def test_notified_observation_uses_the_single_subject_priority_lane(self):
        observation = realtime_request(
            "observation",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            observation_followup_symbols=["AAPL"],
        )
        background = realtime_request("background", ["MSFT"], "2026-07-24T00:01:00Z")
        runner = self.build_runner(
            [observation, background],
            settings={
                "ontologyReasoningMaxSymbolsPerRun": "2",
                "typedbNativeRuleTargetSymbolLimit": "2",
            },
        )

        result = runner.run_once(force=True)

        self.assertEqual(["AAPL"], self.monitor.calls[0])
        self.assertEqual(1, result["batchPlan"]["targetSymbolLimit"])
        self.assertTrue(result["batchPlan"]["singleSubjectInference"])
        self.assertEqual(2, result["batchPlan"]["proposedMultiSubjectLimit"])
        self.assertEqual(["AAPL"], result["batchPlan"]["observationFollowupSymbols"])
        self.assertEqual(["AAPL"], self.monitor.reasoning_contexts[0]["observationFollowupSymbols"])
        self.assertEqual("MARKET", result["queueDispatch"]["selectedWorkClasses"][0])
        self.assertEqual(1, result["mailbox"]["pendingEntryCount"])

    def test_observation_followup_priority_survives_a_newer_latest_state_snapshot(self):
        notified = realtime_request(
            "notified",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            observation_followup_symbols=["AAPL"],
        )
        newer = realtime_request("newer", ["AAPL"], "2026-07-24T00:01:00Z")
        runner = self.build_runner([])

        runner.synchronize_mailbox([notified])
        runner.synchronize_mailbox([newer])
        entry = self.mailbox.pending(1)[0]
        virtual = runner.mailbox_virtual_event(entry)

        self.assertEqual("newer", entry["sourceEventId"])
        self.assertGreaterEqual(entry["priorityHint"], 100000)
        self.assertTrue(virtual.payload["_reasoningMailbox"]["observationFollowup"])

    def test_projection_alert_outcome_marks_an_evaluated_no_match_as_no_material_change(self):
        runner = self.build_runner([])
        monitor = SimpleNamespace(last_ontology_projection_results={
            "main": {
                "status": "ok",
                "inferenceBox": {
                    "status": "empty",
                    "nativeTypeDbReasoningCompleted": True,
                    "generationAligned": True,
                },
                "alertPipeline": {
                    "status": "no-signal",
                    "requestedSymbols": ["AAPL"],
                },
            },
        })

        outcomes = runner.projection_alert_outcomes(monitor)

        self.assertEqual("no-material-change", outcomes[0]["terminalOutcome"])
        self.assertEqual(["AAPL"], outcomes[0]["alertPipeline"]["requestedSymbols"])

    def test_cold_rulebox_uses_direct_fallback_by_default(self):
        event = realtime_request("prewarm-gate", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner(
            [event],
            settings={"ontologyRuleboxPrewarmEnabled": "1"},
        )
        runner.rulebox_prewarm_probe = lambda: {
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 3,
            "reason": "RuleBox schema functions are being prepared.",
        }

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, len(self.monitor.calls))
        self.assertNotIn("ruleboxPrewarmRequest", result)

    def test_cold_receipt_publishes_a_compiler_handoff_without_starting_inference(self):
        event = realtime_request("prewarm-handoff", ["AAPL"], "2026-07-24T00:00:00Z")
        published = []
        runner = self.build_runner(
            [event],
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmRequireReadyForInference": "1",
            },
            prewarm_state_writer=lambda payload: published.append(dict(payload or {})),
        )
        runner.rulebox_prewarm_probe = lambda: {
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 3,
            "reason": "RuleBox schema functions are being prepared.",
        }

        result = runner.run_once()

        self.assertEqual("deferred-rulebox-prewarm", result["status"])
        self.assertEqual([], self.monitor.calls)
        self.assertTrue(result["ruleboxPrewarmRequest"]["published"])
        self.assertEqual(1, len(published))
        self.assertEqual("bootstrap-required", published[0]["status"])
        self.assertFalse(published[0]["lastResult"]["functionsReady"])

    def test_aged_multi_entry_queue_keeps_direct_typeql_delivery_available(self):
        events = [
            realtime_request("prewarm-recovery-a", ["AAPL"], "2026-07-24T00:00:00Z"),
            realtime_request("prewarm-recovery-b", ["MSFT"], "2026-07-24T00:00:00Z"),
        ]
        runner = self.build_runner(
            events,
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryAgeSeconds": "90",
                "ontologyRuleboxPrewarmBacklogRecoveryMinPendingEntries": "2",
                "ontologyRuleboxPrewarmBacklogRecoveryRetrySeconds": "5",
                "ontologyRuleboxPrewarmRequireReadyForInference": "0",
                "typedbNativeRuleDirectQueryFallbackEnabled": "1",
            },
        )
        runner.rulebox_prewarm_probe = lambda: {
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 3,
            "reason": "RuleBox schema functions are being prepared.",
        }

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertTrue(result["ruleboxPrewarmRecovery"]["eligible"])

    def test_aged_queue_keeps_direct_typeql_delivery_available_during_compiler_activity(self):
        now = datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc)
        events = [
            realtime_request("prewarm-activity-a", ["AAPL"], "2026-07-24T00:00:00Z"),
            realtime_request("prewarm-activity-b", ["MSFT"], "2026-07-24T00:00:00Z"),
        ]
        runner = self.build_runner(
            events,
            now=lambda: now,
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryAgeSeconds": "90",
                "ontologyRuleboxPrewarmBacklogRecoveryMinPendingEntries": "2",
                "ontologyRuleboxPrewarmRequireReadyForInference": "0",
                "typedbNativeRuleDirectQueryFallbackEnabled": "1",
            },
            activity_probe=lambda: {
                "status": "running",
                "active": True,
                "expiresAtEpoch": now.timestamp() + 120,
            },
        )
        readiness_calls = []
        runner.rulebox_prewarm_probe = lambda: readiness_calls.append(True) or {
            "status": "provisioning",
            "functionsReady": False,
        }

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual([], readiness_calls)
        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertTrue(result["ruleboxPrewarmActivity"]["active"])
        self.assertTrue(result["ruleboxPrewarmFallbackDuringCompilerActivity"])

    def test_direct_typeql_fallback_runs_during_compiler_activity_when_strict_gate_is_disabled(self):
        now = datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc)
        event = realtime_request("prewarm-active-fallback", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner(
            [event],
            now=lambda: now,
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmRequireReadyForInference": "0",
                "typedbNativeRuleDirectQueryFallbackEnabled": "1",
            },
            activity_probe=lambda: {
                "status": "cooldown",
                "active": True,
                "expiresAtEpoch": now.timestamp() + 120,
            },
        )
        readiness_calls = []
        runner.rulebox_prewarm_probe = lambda: readiness_calls.append(True) or {
            "status": "provisioning",
            "functionsReady": False,
        }

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertEqual([], readiness_calls)
        self.assertTrue(result["ruleboxPrewarmFallbackDuringCompilerActivity"])
        self.assertEqual("cooldown", result["ruleboxPrewarmActivity"]["status"])

    def test_aged_queue_uses_direct_typeql_during_failed_compiler_cooldown(self):
        now = datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc)
        events = [
            realtime_request("prewarm-cooldown-a", ["AAPL"], "2026-07-24T00:00:00Z"),
            realtime_request("prewarm-cooldown-b", ["MSFT"], "2026-07-24T00:00:00Z"),
        ]
        runner = self.build_runner(
            events,
            now=lambda: now,
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryAgeSeconds": "90",
                "ontologyRuleboxPrewarmBacklogRecoveryMinPendingEntries": "2",
                "ontologyRuleboxPrewarmRequireReadyForInference": "0",
                "typedbNativeRuleDirectQueryFallbackEnabled": "1",
            },
            activity_probe=lambda: {
                "status": "cooldown",
                "active": True,
                "expiresAtEpoch": now.timestamp() + 120,
            },
        )

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertTrue(result["ruleboxPrewarmRecovery"]["eligible"])
        self.assertTrue(result["ruleboxPrewarmRecoveryCooldownFallback"])

    def test_aged_queue_avoids_compiler_receipt_probe_when_direct_typeql_is_available(self):
        events = [
            realtime_request("prewarm-recovery-error-a", ["AAPL"], "2026-07-24T00:00:00Z"),
            realtime_request("prewarm-recovery-error-b", ["MSFT"], "2026-07-24T00:00:00Z"),
        ]
        runner = self.build_runner(
            events,
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryAgeSeconds": "90",
                "ontologyRuleboxPrewarmBacklogRecoveryMinPendingEntries": "2",
                "ontologyRuleboxPrewarmBacklogRecoveryRetrySeconds": "5",
                "ontologyRuleboxPrewarmRequireReadyForInference": "0",
                "typedbNativeRuleDirectQueryFallbackEnabled": "1",
            },
        )
        runner.rulebox_prewarm_probe = lambda: (_ for _ in ()).throw(
            RuntimeError("temporary RuleBox receipt read failure")
        )

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertTrue(result["ruleboxPrewarmRecovery"]["eligible"])

    def test_repeated_native_generation_failure_yields_the_same_mailbox_revision(self):
        class LeasedMailbox(MemoryMailbox):
            def __init__(self):
                super().__init__()
                self.releases = []

            def claim(self, entries, _worker_id, _lease_seconds):
                return {
                    "enabled": True,
                    "claimed": [{**dict(item), "attemptCount": 3} for item in entries or []],
                    "blocked": [],
                    "resumed": [],
                }

            def release(self, entries, reason, retry_after_seconds, worker_id=""):
                self.releases.append({
                    "entries": [dict(item) for item in entries or []],
                    "reason": reason,
                    "retryAfterSeconds": retry_after_seconds,
                    "workerId": worker_id,
                })

        class NativeFailureMonitor(Monitor):
            def __init__(self):
                super().__init__()
                self.last_ontology_projection_results = {
                    "default": {
                        "status": "inference-failed-rolled-back",
                        "reason": "TypeDB native rule execution timed out before an aligned InferenceBox was stored.",
                    },
                }

        event = realtime_request("native-failure-yield", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner([event])
        mailbox = LeasedMailbox()
        runner.mailbox_store = mailbox
        self.mailbox = mailbox
        self.monitor = NativeFailureMonitor()
        runner.monitor_runner_factory = lambda: self.monitor

        result = runner.run_once(force=True)

        self.assertEqual("deferred", result["status"])
        self.assertTrue(result["mailboxFailureYield"]["applied"])
        self.assertEqual(3, result["mailboxFailureYield"]["attemptCount"])
        self.assertEqual(120, result["retryAfterSeconds"])
        self.assertEqual(120, mailbox.releases[-1]["retryAfterSeconds"])

    def test_status_reports_native_inference_failure_separately_from_queue_probe_health(self):
        event = realtime_request("native-health", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner([event])
        self.cursor.payload["lastReasoningExecution"] = {
            "status": "deferred",
            "deferredReason": "TypeDB native-rule 대기: direct rule query timed out",
            "projectionFailures": [{
                "stage": "projection",
                "status": "inference-failed-rolled-back",
            }],
            "mailboxFailureYield": {"applied": True, "retryAfterSeconds": 120},
        }

        status = runner.status()

        self.assertEqual("healthy", status["probeHealth"]["status"])
        self.assertEqual("critical", status["inferenceHealth"]["status"])
        self.assertEqual("typedb-inference-execution", status["inferenceHealth"]["scope"])
        self.assertTrue(status["inferenceHealth"]["failureYield"]["applied"])

    def test_waits_for_rulebox_prewarm_when_safety_gate_is_enabled(self):
        event = realtime_request("prewarm-strict-gate", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner(
            [event],
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "typedbNativeRuleDirectQueryFallbackEnabled": "1",
                "ontologyRuleboxPrewarmRequireReadyForInference": "1",
            },
        )
        runner.rulebox_prewarm_probe = lambda: {
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 3,
            "reason": "RuleBox schema functions are being prepared.",
        }

        result = runner.run_once()

        self.assertEqual("deferred-rulebox-prewarm", result["status"])
        self.assertEqual([], self.monitor.calls)
        self.assertEqual(15, result["retryAfterSeconds"])
        self.assertFalse(result["ruleboxPrewarm"]["ready"])

    def test_source_snapshot_preflight_defers_before_creating_a_typedb_runner(self):
        event = realtime_request("snapshot-preflight", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner([event])
        runner.snapshot_readiness_probe = lambda context: {
            "ready": False,
            "status": "deferred-source-snapshot",
            "reason": "The latest monitor snapshot predates the requested fact revision.",
            "retryAfterSeconds": 30,
            "accounts": [{"accountId": "acct", "status": "deferred"}],
        }

        result = runner.run_once(force=True)

        self.assertEqual("deferred", result["status"])
        self.assertEqual([], self.monitor.calls)
        self.assertEqual(30, result["retryAfterSeconds"])
        self.assertIn("확정 모니터 스냅샷 대기", result["deferredReason"])
        self.assertEqual("deferred-source-snapshot", result["sourceSnapshotPreflight"]["status"])

    def test_permanent_invalid_source_is_isolated_and_queue_continues(self):
        invalid = realtime_request(
            "invalid-source",
            ["AAPL"],
            "2026-07-24T00:02:00Z",
            review_level="critical",
        )
        invalid.payload["accountId"] = "market:shared:global"
        valid = realtime_request("valid-source", ["MSFT"], "2026-07-24T00:01:00Z")
        valid.payload["accountId"] = "default"
        runner = self.build_runner([invalid, valid])
        readiness_calls = []

        def readiness(context):
            readiness_calls.append(dict(context or {}))
            if "market:shared:global" in set(context.get("accountIds") or []):
                return {
                    "ready": False,
                    "status": "rejected-source-account",
                    "reason": "unregistered source",
                    "permanent": True,
                    "invalidAccountIds": ["market:shared:global"],
                }
            return {"ready": True, "status": "ready"}

        runner.snapshot_readiness_probe = readiness

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual([["MSFT"]], self.monitor.calls)
        self.assertEqual(2, len(readiness_calls))
        self.assertIn("invalid-source", self.cursor.superseded)
        self.assertEqual("expired", self.mailbox.events["invalid-source"]["state"])
        self.assertEqual("completed", self.mailbox.events["valid-source"]["state"])
        self.assertEqual(1, len(result["isolatedRequestFailures"][0]["rejectedRequestEventIds"]))

    def test_post_monitor_checkpoint_finishes_without_rebuilding_typedb(self):
        event = realtime_request("resume-current", ["AAPL"], "2026-07-24T00:00:00Z")
        runner = self.build_runner([event])
        mailbox = PostMonitorCheckpointMailbox()
        runner.mailbox_store = mailbox
        self.mailbox = mailbox

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual([], self.monitor.calls)
        self.assertEqual("post-monitor-inference-verified", result["resumedFromCheckpoint"]["status"])
        self.assertIn("resume-current", self.cursor.ids)
        self.assertEqual("completed", mailbox.events["resume-current"]["state"])

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

    def test_generic_research_fact_families_share_one_latest_state_slot(self):
        article = research_evidence_request(
            "article-analysis",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            trigger="news-analysis-enrichment",
            fact_types=["NewsArticleAnalysis", "ResearchEvidence"],
        )
        evidence = research_evidence_request(
            "evidence-refresh",
            ["AAPL"],
            "2026-07-24T00:01:00Z",
            trigger="research-evidence-update",
            fact_types=["NewsEvent", "ResearchEvidence"],
        )
        runner = self.build_runner([article, evidence])

        article_entry = runner.mailbox_entries_for_event(article)[0]
        evidence_entry = runner.mailbox_entries_for_event(evidence)[0]
        ingress_article_entry = durable_mailbox_entries(article)[0]
        ingress_evidence_entry = durable_mailbox_entries(evidence)[0]
        result = runner.run_once(force=True)

        self.assertEqual(article_entry["mailboxKey"], evidence_entry["mailboxKey"])
        self.assertEqual(ingress_article_entry["mailboxKey"], ingress_evidence_entry["mailboxKey"])
        self.assertEqual(article_entry["mailboxKey"], ingress_article_entry["mailboxKey"])
        self.assertNotEqual(article_entry["factFamily"], evidence_entry["factFamily"])
        self.assertEqual("ResearchEvidenceLatestState", article_entry["mailboxSlotFamily"])
        self.assertEqual("ResearchEvidenceLatestState", evidence_entry["mailboxSlotFamily"])
        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertEqual("superseded", self.mailbox.events["article-analysis"]["state"])
        self.assertEqual("completed", self.mailbox.events["evidence-refresh"]["state"])
        self.assertEqual("ok", result["status"])

    def test_mixed_snapshot_routes_market_evidence_and_macro_to_separate_lanes(self):
        source = DomainEvent(
            name="monitor.snapshot.verified",
            aggregate_id="account:main",
            occurred_at="2026-07-24T00:00:00Z",
            payload={"symbols": ["AAPL"]},
        )
        requested = ontology_reasoning_requested_event(
            source,
            "market-data-update",
            ["AAPL"],
            changed_count=3,
            fact_types=["MarketQuote", "ResearchEvidence", "InterestRate"],
            fact_revisions_by_symbol={"AAPL": "revision:mixed:1"},
            observation_followup_symbols=["AAPL"],
        )
        event = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id=requested.aggregate_id,
            payload=requested.payload,
            occurred_at=requested.occurred_at,
            event_id="mixed-routing",
        )

        entries = durable_mailbox_entries(event)
        by_class = {entry["workClass"]: entry for entry in entries}

        self.assertEqual({"MARKET", "EVIDENCE", "MACRO"}, set(by_class))
        self.assertEqual("REALTIME_REASONING", by_class["MARKET"]["reasoningLane"])
        self.assertEqual("CONTEXT_REASONING", by_class["EVIDENCE"]["reasoningLane"])
        self.assertEqual("CONTEXT_REASONING", by_class["MACRO"]["reasoningLane"])
        self.assertEqual("MARKET_CONTEXT", by_class["MACRO"]["impactScope"])
        self.assertTrue(by_class["MARKET"]["observationFollowup"])
        self.assertFalse(by_class["MACRO"]["observationFollowup"])
        self.assertEqual(["InterestRate"], by_class["MACRO"]["ruleFamilies"])
        self.assertEqual(3, len({entry["mailboxKey"] for entry in entries}))

    def test_legacy_generic_research_slot_is_superseded_by_newer_evidence(self):
        article = research_evidence_request(
            "legacy-article",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            trigger="news-analysis-enrichment",
            fact_types=["NewsArticleAnalysis", "ResearchEvidence"],
        )
        evidence = research_evidence_request(
            "current-evidence",
            ["AAPL"],
            "2026-07-24T00:01:00Z",
            trigger="research-evidence-update",
            fact_types=["NewsEvent", "ResearchEvidence"],
        )
        runner = self.build_runner([evidence])
        legacy_entry = runner.mailbox_entries_for_event(article)[0]
        # Reproduce a row created before the generic-research slot identity
        # was introduced: it used the visible fact family as its key.
        legacy_entry["mailboxKey"] = "legacy-research:" + legacy_entry["mailboxKey"]
        self.mailbox.enqueue([legacy_entry])

        result = runner.run_once(force=True)

        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertEqual("superseded", self.mailbox.events["legacy-article"]["state"])
        self.assertEqual("completed", self.mailbox.events["current-evidence"]["state"])
        self.assertEqual(1, result["semanticSupersededMailboxEntryCount"])
        self.assertEqual(0, result["mailbox"]["pendingEntryCount"])

    def test_legacy_realtime_fact_family_slot_is_superseded_by_newest_subject_state(self):
        old = realtime_request("legacy-market", ["AAPL"], "2026-07-24T00:00:00Z")
        source = DomainEvent(
            name="monitoring.snapshot_collected",
            aggregate_id="account:market",
            occurred_at="2026-07-24T00:01:00Z",
            payload={"sourceObservedAt": "2026-07-24T00:01:00Z", "symbols": ["AAPL"]},
        )
        requested = ontology_reasoning_requested_event(
            source,
            "portfolio-snapshot-update",
            ["AAPL"],
            changed_count=1,
            fact_types=["PortfolioSnapshot"],
        )
        newest = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id=requested.aggregate_id,
            payload=dict(requested.payload or {}),
            occurred_at="2026-07-24T00:01:00Z",
            event_id="current-snapshot",
        )
        runner = self.build_runner([newest])
        legacy_entry = runner.mailbox_entries_for_event(old)[0]
        # Reproduce the separate fact-family row written before realtime
        # observations shared one account/symbol latest-state slot.
        legacy_entry["mailboxKey"] = "legacy-realtime:" + legacy_entry["mailboxKey"]
        self.mailbox.enqueue([legacy_entry])

        result = runner.run_once(force=True)

        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertEqual("superseded", self.mailbox.events["legacy-market"]["state"])
        self.assertEqual("completed", self.mailbox.events["current-snapshot"]["state"])
        self.assertEqual(1, result["semanticSupersededMailboxEntryCount"])
        self.assertEqual("compacted", result["mailboxRealtimeLatestStateCompaction"]["status"])
        self.assertEqual(0, result["mailbox"]["pendingEntryCount"])

    def test_orphaned_research_retry_is_retired_after_a_later_successful_generation(self):
        article = research_evidence_request(
            "orphaned-research",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            trigger="news-analysis-enrichment",
            fact_types=["NewsArticleAnalysis", "ResearchEvidence"],
        )
        runner = self.build_runner([])
        legacy_entry = runner.mailbox_entries_for_event(article)[0]
        legacy_entry["mailboxKey"] = "orphaned-research:" + legacy_entry["mailboxKey"]
        self.mailbox.enqueue([legacy_entry])
        self.cursor.payload["lastReasonedAtBySymbol"] = {"AAPL": "2026-07-24T00:01:00Z"}

        result = runner.run_once(force=True)

        self.assertEqual("idle", result["status"])
        self.assertEqual([], self.monitor.calls)
        self.assertEqual("superseded", self.mailbox.events["orphaned-research"]["state"])
        self.assertIn("orphaned-research", self.cursor.superseded)
        self.assertEqual(1, result["semanticSupersededMailboxEntryCount"])

    def test_status_excludes_legacy_research_slot_from_actionable_queue_delay(self):
        article = research_evidence_request(
            "legacy-status-article",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            trigger="news-analysis-enrichment",
            fact_types=["NewsArticleAnalysis", "ResearchEvidence"],
        )
        evidence = research_evidence_request(
            "current-status-evidence",
            ["AAPL"],
            "2026-07-24T00:01:00Z",
            trigger="research-evidence-update",
            fact_types=["NewsEvent", "ResearchEvidence"],
        )
        runner = self.build_runner([])
        legacy_entry = runner.mailbox_entries_for_event(article)[0]
        legacy_entry["mailboxKey"] = "legacy-status:" + legacy_entry["mailboxKey"]
        self.mailbox.enqueue([legacy_entry])
        self.mailbox.enqueue(runner.mailbox_entries_for_event(evidence))

        status = runner.status()

        self.assertEqual(1, status["effectivePendingCount"])
        self.assertEqual(1, status["mailboxPendingEntryCount"])
        self.assertEqual(2, status["mailboxStoredEntryCount"])
        self.assertEqual(1, status["semanticSupersededMailboxEntryCount"])
        self.assertEqual("preview", status["mailboxSemanticCompaction"]["status"])
        self.assertEqual("2026-07-24T00:01:00Z", status["queueDispatch"]["oldestRequestAt"])
        self.assertEqual("pending", self.mailbox.events["legacy-status-article"]["state"])

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
        self.assertEqual(1, runner.status()["pendingResearchHandoffCount"])

    def test_status_reads_direct_handoffs_without_running_event_log_repair(self):
        class CountingIngressReader(Reader):
            def __init__(self, events):
                super().__init__(events)
                self.direct_reads = 0
                self.repair_reads = 0

            def direct_pending_reasoning_events(self, limit=0):
                del limit
                self.direct_reads += 1
                return list(self.events)

            def unmaterialized_reasoning_events(self, limit=0):
                del limit
                self.repair_reads += 1
                raise AssertionError("status must not scan the append-only event log")

        event = research_evidence_request(
            "counted-hypothesis-research",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
            trigger="hypothesis-research-update",
            research_run_id="research-run-counted",
            reasoning_handoff={"status": "requested", "requestId": "handoff-counted"},
        )
        runner = self.build_runner([])
        reader = CountingIngressReader([event])
        runner.event_reader = reader

        status = runner.status()

        self.assertEqual(1, reader.direct_reads)
        self.assertEqual(0, reader.repair_reads)
        self.assertEqual(1, status["pendingResearchHandoffCount"])

    def test_scheduler_runs_bounded_ingress_repair_once_per_interval(self):
        class CountingIngressReader(Reader):
            def __init__(self, events):
                super().__init__(events)
                self.direct_reads = 0
                self.repair_pages = []

            def direct_pending_reasoning_events(self, limit=0):
                del limit
                self.direct_reads += 1
                return []

            def reasoning_ingress_repair_page(self, after_occurred_at="", after_event_id="", limit=0):
                self.repair_pages.append((after_occurred_at, after_event_id, limit))
                return {
                    "events": list(self.events),
                    "cursor": {"occurredAt": "2026-07-24T00:00:00Z", "eventId": "legacy-ingress-repair"},
                    "scannedCount": 20,
                    "recoveredEventCount": len(self.events),
                    "exhausted": True,
                }

            def unmaterialized_reasoning_events(self, limit=0):
                raise AssertionError("indexed repair page should replace the legacy anti-join")

        event = research_evidence_request(
            "legacy-ingress-repair",
            ["AAPL"],
            "2026-07-24T00:00:00Z",
        )
        runner = self.build_runner([])
        reader = CountingIngressReader([event])
        runner.event_reader = reader

        first = runner.source_reasoning_events()
        second = runner.source_reasoning_events()

        self.assertEqual([event.event_id], [item.event_id for item in first])
        self.assertEqual([], second)
        self.assertEqual(2, reader.direct_reads)
        self.assertEqual([("", "", 100)], reader.repair_pages)
        repair = runner.status()["ingressRepair"]
        self.assertEqual("paged-index-scan", repair["mode"])
        self.assertEqual(20, repair["lastScannedCount"])
        self.assertEqual("legacy-ingress-repair", repair["cursor"]["eventId"])

    def test_failed_ingress_repair_waits_for_the_next_repair_interval(self):
        class FailingRepairReader(Reader):
            def __init__(self):
                super().__init__([])
                self.repair_reads = 0

            def direct_pending_reasoning_events(self, limit=0):
                del limit
                return []

            def unmaterialized_reasoning_events(self, limit=0):
                del limit
                self.repair_reads += 1
                raise RuntimeError("temporary event-log repair failure")

        runner = self.build_runner([])
        reader = FailingRepairReader()
        runner.event_reader = reader

        self.assertEqual([], runner.source_reasoning_events())
        self.assertEqual([], runner.source_reasoning_events())
        status = runner.status()

        self.assertEqual(1, reader.repair_reads)
        self.assertEqual("error", status["ingressRepair"]["lastStatus"])
        self.assertFalse(status["ingressRepair"]["due"])
        self.assertIn("temporary event-log repair failure", status["ingressRepair"]["lastError"])

    def test_shared_world_projection_completes_an_older_direct_research_handoff(self):
        class IngressReader(Reader):
            def unmaterialized_reasoning_events(self, limit=0):
                del limit
                return list(self.events)

        class ResearchStore:
            def __init__(self):
                self.refreshes = []

            def mark_reasoning_refreshed(self, run_id, refreshed=True, reasoning_handoff=None):
                self.refreshes.append((run_id, bool(refreshed), dict(reasoning_handoff or {})))
                return {"runId": run_id, "reasoningRefreshed": bool(refreshed)}

        class ProjectionMonitor:
            def __init__(self):
                self.accounts = [SimpleNamespace(account_id="default")]
                self.calls = []
                self.last_ontology_projection_results = {
                    "default": {
                        "status": "ok",
                        "aboxSnapshotId": "abox:new",
                        "ontologyWorld": {
                            "accountId": "default",
                            "worldId": "portfolio:local:default",
                        },
                        "inferenceBox": {
                            "status": "ok",
                            "generationAligned": True,
                            "sourceAboxSnapshotId": "abox:new",
                            "inferenceGenerationId": "inference:new",
                            "worldId": "portfolio:local:default",
                            "inferenceGenerationAt": "2026-07-24T00:05:00Z",
                        },
                    },
                }

            def run_once(self, force=False, symbol_filter=None, **_kwargs):
                del force
                self.calls.append(list(symbol_filter or []))
                return []

        generic = research_evidence_request(
            "current-generic",
            ["AAPL"],
            "2026-07-24T00:04:00Z",
        )
        old_handoff = research_evidence_request(
            "old-handoff",
            ["005930"],
            "2026-07-24T00:00:00Z",
            trigger="hypothesis-research-update",
            research_run_id="research-run-old",
            reasoning_handoff={
                "requestId": "handoff-old",
                "status": "pending",
                "changedEvidenceIds": ["evidence-old"],
                "sourceGeneration": {
                    "inferenceGenerationId": "inference:old",
                    "sourceAboxSnapshotId": "abox:old",
                    "worldId": "portfolio:local:default",
                    "generationAligned": True,
                    "observedAt": "2026-07-24T00:00:00Z",
                },
            },
        )
        old_handoff = DomainEvent(
            name=old_handoff.name,
            aggregate_id=old_handoff.aggregate_id,
            event_id=old_handoff.event_id,
            occurred_at=old_handoff.occurred_at,
            payload={**old_handoff.payload, "accountId": "default"},
        )
        runner = self.build_runner([])
        self.monitor = ProjectionMonitor()
        runner.event_reader = IngressReader([old_handoff, generic])
        runner.monitor_runner_factory = lambda: self.monitor
        store = ResearchStore()
        runner.research_store = store
        # The older direct handoff is not due as a scheduler trigger, but a
        # successful shared-world projection must still reconcile it.
        self.cursor.payload["lastReasonedAtBySymbol"] = {"005930": "2026-07-24T00:04:00Z"}

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual([["AAPL"]], self.monitor.calls)
        self.assertIn("old-handoff", self.cursor.ids)
        self.assertEqual(["research-run-old"], result["refreshedResearchRunIds"])
        self.assertEqual(["old-handoff"], result["reconciledResearchRequestEventIds"])
        self.assertTrue(store.refreshes[-1][1])
        self.assertEqual("applied", store.refreshes[-1][2]["status"])
        self.assertTrue(any(
            item["eventIds"] == ["old-handoff"] and item["state"] == "completed"
            for item in self.mailbox.direct_terminalizations
        ))

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
        work_item = connection.work_items["direct:handoff-direct"]
        self.assertEqual("handoff-direct", work_item["source_event_id"])
        self.assertEqual("queued", work_item["work_state"])
        self.assertIn("reasoning-work-checkpoint-v2", work_item["checkpoint_json"])

    def test_atomic_ingress_terminalizes_an_unclassified_fact_contract(self):
        source = DomainEvent(
            name="future.provider.updated",
            aggregate_id="future:AAPL",
            occurred_at="2026-07-24T00:00:00Z",
            payload={"sourceObservedAt": "2026-07-24T00:00:00Z"},
        )
        event = ontology_reasoning_requested_event(
            source,
            "future-provider-update",
            symbols=["AAPL"],
            changed_count=1,
            fact_types=["FutureProviderPayload"],
        )
        event = DomainEvent.from_dict({**event.to_dict(), "event_id": "blocked-unclassified"})
        connection = MySQLMailboxConnection()

        result = MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(connection, event)

        self.assertEqual("direct", result["ingressKind"])
        self.assertEqual("expired", connection.events[event.event_id]["state"])
        self.assertIn("FutureProviderPayload", connection.events[event.event_id]["reason"])
        self.assertNotIn("direct:" + event.event_id, connection.work_items)
        self.assertEqual({event.event_id: "expired"}, result["terminalEventStates"])

    def test_orphan_recovery_only_accepts_confirmed_dead_local_scheduler_owners(self):
        self.assertEqual(451, local_reasoning_watch_pid("reasoning-watch:local:451", hostname="local"))
        self.assertEqual(452, local_reasoning_watch_pid("reasoning-watch:452", hostname="local"))
        self.assertEqual(453, local_reasoning_watch_pid("reasoning:local:453", hostname="local"))
        self.assertEqual(454, local_reasoning_watch_pid("reasoning:454", hostname="local"))
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

    def test_timeout_retries_pre_native_graph_assembly_without_long_global_cooldown(self):
        runner = self.build_runner([], settings={
            "ontologyReasoningExecutionTimeoutBackoffSeconds": "300",
            "ontologyReasoningPreNativeTimeoutBackoffSeconds": "45",
        })
        mailbox = CheckpointTimeoutRecoveringMailbox("typedb-graph-assembly-start")
        runner.mailbox_store = mailbox

        result = runner.record_execution_timeout(240, worker_id="reasoning-watch:pre-native")

        self.assertEqual(45, result["retryAfterSeconds"])
        self.assertEqual(45, mailbox.recoveries[0][1])
        self.assertEqual("pre-native-fast-retry", result["executionTelemetry"]["timeoutRetryPolicy"]["mode"])
        self.assertEqual("typedb-graph-assembly-start", mailbox.timeouts[0]["stage"])

    def test_timeout_keeps_native_boundary_protection_after_persistence_starts(self):
        runner = self.build_runner([], settings={
            "ontologyReasoningExecutionTimeoutBackoffSeconds": "300",
            "ontologyReasoningPreNativeTimeoutBackoffSeconds": "45",
        })
        mailbox = CheckpointTimeoutRecoveringMailbox("typedb-native-inference-start")
        runner.mailbox_store = mailbox

        result = runner.record_execution_timeout(240, worker_id="reasoning-watch:native")

        self.assertEqual(300, result["retryAfterSeconds"])
        self.assertEqual(300, mailbox.recoveries[0][1])
        self.assertEqual("native-boundary-protection", result["executionTelemetry"]["timeoutRetryPolicy"]["mode"])

    def test_timeout_checkpoint_column_migrates_existing_mailbox_table(self):
        columns = {
            definition.name: definition.definition_sql
            for definition in MYSQL_OPERATIONAL_COLUMNS["ontology_reasoning_work_items"]
        }

        self.assertEqual("VARCHAR(40) NOT NULL DEFAULT ''", columns["stage_started_at"])

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

    def test_coherent_snapshot_keeps_source_events_queued_behind_one_native_subject(self):
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
        self.assertEqual(1, result["scheduledRequestCount"])
        self.assertEqual(1, len(result["symbols"]))
        self.assertEqual(2, result["omittedSymbolCount"])
        self.assertEqual(1, len(self.monitor.calls))
        self.assertEqual(set(result["symbols"]), set(self.monitor.calls[0]))
        self.assertEqual(1, len(self.cursor.ids))
        self.assertEqual(2, result["mailbox"]["pendingEntryCount"])
        self.assertEqual(2, result["batchPlan"]["proposedMultiSubjectLimit"])

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

    def test_delivery_guard_suppresses_an_alert_from_a_superseded_revision(self):
        old = realtime_request("old", ["AAPL"], "2026-07-24T00:00:00Z")
        newest = realtime_request("new", ["AAPL"], "2026-07-24T00:01:00Z")
        runner = self.build_runner([old])

        runner.synchronize_mailbox([old])
        old_entry = self.mailbox.pending(1)[0]
        runner.synchronize_mailbox([newest])
        guarded_events, details = runner.mailbox_delivery_guard([old_entry])(
            None,
            [
                AlertEvent("main", "메인", "관찰", "investmentInsight", "aapl", "AAPL", [], symbol="AAPL"),
                AlertEvent("main", "메인", "관찰", "investmentInsight", "msft", "MSFT", [], symbol="MSFT"),
            ],
        )

        self.assertEqual("applied", details["status"])
        self.assertEqual(["AAPL"], details["staleSymbols"])
        self.assertEqual(1, details["suppressedEventCount"])
        self.assertEqual(["MSFT"], [event.symbol for event in guarded_events])

    def test_superseded_delivery_revision_advances_fairness_without_acknowledging_newer_work(self):
        old = realtime_request("old", ["AAPL"], "2026-07-24T00:00:00Z")
        other = realtime_request("other", ["MSFT"], "2026-07-24T00:00:00Z")
        newest = realtime_request("new", ["AAPL"], "2026-07-24T00:01:00Z")
        runner = self.build_runner([old, other])

        class SupersedingMonitor(Monitor):
            def __init__(self, inject_new_revision):
                super().__init__()
                self.inject_new_revision = inject_new_revision

            def run_once(self, force=False, symbol_filter=None, delivery_guard=None, **_kwargs):
                del force
                self.calls.append(list(symbol_filter or []))
                if self.inject_new_revision:
                    self.inject_new_revision = False
                    runner.synchronize_mailbox([newest])
                    _events, details = delivery_guard(None, [])
                    self.last_delivery_guard_result = details
                else:
                    self.last_delivery_guard_result = {}
                return []

        self.monitor = SupersedingMonitor(inject_new_revision=True)
        runner.monitor_runner_factory = lambda: self.monitor

        first = runner.run_once(force=True)

        # The old revision is unsafe to deliver and the newer AAPL revision
        # remains queued, but a verified TypeDB generation did serve AAPL.
        # Fairness must therefore move on to MSFT instead of repeatedly
        # re-running AAPL while the mailbox keeps receiving fresh snapshots.
        self.assertEqual([['AAPL']], self.monitor.calls)
        self.assertEqual(0, first["processedCount"])
        self.assertEqual(1, first["servedSymbolCount"])
        self.assertEqual(["AAPL"], first["servedSymbols"])
        self.assertEqual(1, first["staleDeliverySymbolCount"])
        self.assertEqual(1, first["executionTelemetry"]["servedSymbolCount"])
        self.assertEqual(0, first["executionTelemetry"]["processedCount"])
        self.assertEqual("2026-07-24T00:05:00Z", self.cursor.payload["lastReasonedAtBySymbol"]["AAPL"])
        pending_by_symbol = {
            item["symbol"]: item["sourceEventId"]
            for item in self.mailbox.pending(10)
        }
        self.assertEqual("new", pending_by_symbol["AAPL"])

        second = runner.run_once(force=True)

        self.assertEqual([["AAPL"], ["MSFT"]], self.monitor.calls)
        self.assertEqual(1, second["processedCount"])
        self.assertEqual(["MSFT"], second["servedSymbols"])

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

    def test_queue_dispatch_prioritizes_market_work_over_general_research(self):
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

        self.assertEqual(1, dispatch["pendingByClass"]["MARKET"])
        self.assertEqual(1, dispatch["pendingByClass"]["EVIDENCE"])
        self.assertEqual(1, dispatch["pendingByPriority"]["market"])
        self.assertEqual(1, dispatch["pendingByPriority"]["research"])
        self.assertEqual(1, dispatch["selectedByPriority"]["market"])
        self.assertEqual(["MARKET"], dispatch["selectedWorkClasses"])
        self.assertEqual(["AAPL"], dispatch["selectedSymbols"])


if __name__ == "__main__":
    unittest.main()
