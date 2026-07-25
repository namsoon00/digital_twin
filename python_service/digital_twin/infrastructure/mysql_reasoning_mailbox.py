"""Durable latest-state mailbox for TypeDB reasoning requests.

The event log remains the audit source of truth. This store only keeps the
newest pending realtime observation for each account/symbol/fact family so a
slow native TypeDB cycle does not replay stale ticks before current data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Mapping

from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


TERMINAL_STATES = {"completed", "superseded", "expired"}


def _text(value: object) -> str:
    return str(value or "").strip()


def _entries_by_event(entries: Iterable[Mapping[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for item in entries or []:
        row = dict(item or {}) if isinstance(item, Mapping) else {}
        event = dict(row.get("sourceEvent") or {}) if isinstance(row.get("sourceEvent"), Mapping) else {}
        event_id = _text(row.get("sourceEventId") or event.get("event_id") or event.get("eventId"))
        key = _text(row.get("mailboxKey"))
        if not event_id or not key or not event:
            continue
        row["sourceEventId"] = event_id
        row["sourceEvent"] = event
        grouped.setdefault(event_id, []).append(row)
    return grouped


def _newer(incoming_at: object, incoming_id: object, current_at: object, current_id: object) -> bool:
    """Compare normalized ISO event timestamps with a stable event-id tie break."""
    incoming = (_text(incoming_at), _text(incoming_id))
    current = (_text(current_at), _text(current_id))
    return incoming > current


def _event_fact_revision(event: Mapping[str, object], symbol: object) -> str:
    payload = dict(event.get("payload") or {}) if isinstance(event, Mapping) else {}
    revisions = payload.get("factRevisionsBySymbol")
    clean_symbol = _text(symbol).upper()
    if not clean_symbol or not isinstance(revisions, Mapping):
        return ""
    value = revisions.get(clean_symbol)
    if value is None:
        for key, candidate in revisions.items():
            if _text(key).upper() == clean_symbol:
                value = candidate
                break
    return _text(value)[:160]


def _entry_fact_revision(entry: Mapping[str, object]) -> str:
    explicit = _text(entry.get("factRevision"))
    if explicit:
        return explicit[:160]
    event = entry.get("sourceEvent")
    return _event_fact_revision(event if isinstance(event, Mapping) else {}, entry.get("symbol"))


class MySQLOntologyReasoningMailboxStore(MySQLOperationalConnection):
    """A leased-worker-ready mailbox with source-event completion accounting."""

    def known_event_ids(self, event_ids: Iterable[str]) -> List[str]:
        clean = list(dict.fromkeys(_text(item) for item in event_ids or [] if _text(item)))
        if not clean:
            return []
        found = []
        with self.connect() as connection:
            for start in range(0, len(clean), 400):
                batch = clean[start:start + 400]
                placeholders = ", ".join(["%s"] * len(batch))
                rows = connection.execute(
                    "SELECT event_id FROM ontology_reasoning_mailbox_events WHERE event_id IN (" + placeholders + ")",
                    batch,
                ).fetchall()
                found.extend(_text(row.get("event_id")) for row in rows or [])
        return list(dict.fromkeys(item for item in found if item))

    def terminal_event_states(self, event_ids: Iterable[str] = None) -> Dict[str, str]:
        clean = list(dict.fromkeys(_text(item) for item in event_ids or [] if _text(item)))
        states: Dict[str, str] = {}
        with self.connect() as connection:
            if clean:
                for start in range(0, len(clean), 400):
                    batch = clean[start:start + 400]
                    placeholders = ", ".join(["%s"] * len(batch))
                    rows = connection.execute(
                        "SELECT event_id, state FROM ontology_reasoning_mailbox_events "
                        "WHERE state IN ('completed', 'superseded', 'expired') AND event_id IN (" + placeholders + ")",
                        batch,
                    ).fetchall()
                    for row in rows or []:
                        states[_text(row.get("event_id"))] = _text(row.get("state"))
            else:
                rows = connection.execute(
                    "SELECT event_id, state FROM ontology_reasoning_mailbox_events "
                    "WHERE state IN ('completed', 'superseded', 'expired') ORDER BY updated_at DESC LIMIT 500"
                ).fetchall()
                for row in rows or []:
                    states[_text(row.get("event_id"))] = _text(row.get("state"))
        return {event_id: state for event_id, state in states.items() if event_id and state}

    def enqueue(self, entries: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        """Insert only newer rows and terminally resolve displaced source events."""
        grouped = _entries_by_event(entries)
        result = {
            "acceptedEntryKeys": [],
            "sameRevisionEntryKeys": [],
            "knownEventIds": [],
            "terminalEventStates": {},
            "enqueuedEventIds": [],
        }
        if not grouped:
            return result
        stamp = utc_now()
        with self.transaction() as connection:
            for event_id, event_entries in grouped.items():
                existing_event = connection.execute(
                    "SELECT state FROM ontology_reasoning_mailbox_events WHERE event_id = %s FOR UPDATE",
                    (event_id,),
                ).fetchone()
                if existing_event:
                    state = _text(existing_event.get("state"))
                    result["knownEventIds"].append(event_id)
                    if state in TERMINAL_STATES:
                        result["terminalEventStates"][event_id] = state
                    continue

                accepted = 0
                same_revision_skips = 0
                first = event_entries[0]
                for entry in event_entries:
                    mailbox_key = _text(entry.get("mailboxKey"))
                    current = connection.execute(
                        "SELECT mailbox.source_event_id, mailbox.occurred_at, events.event_json "
                        "FROM ontology_reasoning_mailbox mailbox "
                        "LEFT JOIN ontology_reasoning_mailbox_events events ON events.event_id = mailbox.source_event_id "
                        "WHERE mailbox.mailbox_key = %s FOR UPDATE",
                        (mailbox_key,),
                    ).fetchone()
                    incoming_revision = _entry_fact_revision(entry)
                    current_event = _json_loads(current.get("event_json"), {}) if current else {}
                    current_revision = _event_fact_revision(current_event, entry.get("symbol"))
                    if current and incoming_revision and incoming_revision == current_revision:
                        same_revision_skips += 1
                        result["sameRevisionEntryKeys"].append(mailbox_key)
                        continue
                    if current and not _newer(
                        entry.get("occurredAt"), event_id, current.get("occurred_at"), current.get("source_event_id"),
                    ):
                        continue
                    if current:
                        displaced = _text(current.get("source_event_id"))
                        connection.execute(
                            """
                            UPDATE ontology_reasoning_mailbox
                            SET source_event_id = %s, account_scope = %s, symbol = %s, fact_family = %s,
                                trigger_name = %s, review_level = %s, priority_hint = %s, occurred_at = %s, updated_at = %s
                            WHERE mailbox_key = %s
                            """,
                            self._entry_values(entry, stamp) + (mailbox_key,),
                        )
                        if displaced and displaced != event_id:
                            terminal = self._decrement_source_event(connection, displaced, "superseded", "newer realtime observation")
                            if terminal:
                                result["terminalEventStates"][displaced] = terminal
                    else:
                        connection.execute(
                            """
                            INSERT INTO ontology_reasoning_mailbox (
                                mailbox_key, source_event_id, account_scope, symbol, fact_family, trigger_name,
                                review_level, priority_hint, occurred_at, created_at, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (mailbox_key,) + self._entry_values(entry, stamp) + (stamp,),
                        )
                    accepted += 1
                    result["acceptedEntryKeys"].append(mailbox_key)

                state = "pending" if accepted else "superseded"
                terminal_reason = "" if accepted else (
                    "same fact revision already owns every mailbox slot"
                    if same_revision_skips == len(event_entries)
                    else "newer observation already owns every mailbox slot"
                )
                connection.execute(
                    """
                    INSERT INTO ontology_reasoning_mailbox_events (
                        event_id, occurred_at, state, unresolved_entry_count, terminal_reason,
                        event_json, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event_id,
                        _text(first.get("occurredAt")),
                        state,
                        accepted,
                        terminal_reason,
                        json_dumps(first.get("sourceEvent") or {}),
                        stamp,
                        stamp,
                    ),
                )
                result["enqueuedEventIds"].append(event_id)
                if state in TERMINAL_STATES:
                    result["terminalEventStates"][event_id] = state
        result["acceptedEntryKeys"] = list(dict.fromkeys(result["acceptedEntryKeys"]))
        result["sameRevisionEntryKeys"] = list(dict.fromkeys(result["sameRevisionEntryKeys"]))
        result["knownEventIds"] = list(dict.fromkeys(result["knownEventIds"]))
        result["enqueuedEventIds"] = list(dict.fromkeys(result["enqueuedEventIds"]))
        return result

    @staticmethod
    def _entry_values(entry: Mapping[str, object], stamp: str):
        return (
            _text(entry.get("sourceEventId")),
            _text(entry.get("accountScope")) or "market",
            _text(entry.get("symbol")).upper(),
            _text(entry.get("factFamily")),
            _text(entry.get("trigger")),
            _text(entry.get("reviewLevel")) or "normal",
            int(entry.get("priorityHint") or 0),
            _text(entry.get("occurredAt")),
            stamp,
        )

    def _decrement_source_event(self, connection, event_id: str, terminal_state: str, reason: str) -> str:
        row = connection.execute(
            "SELECT unresolved_entry_count, state FROM ontology_reasoning_mailbox_events "
            "WHERE event_id = %s FOR UPDATE",
            (event_id,),
        ).fetchone()
        if not row:
            return ""
        remaining = max(0, int(row.get("unresolved_entry_count") or 0) - 1)
        current_state = _text(row.get("state")) or "pending"
        next_state = terminal_state if remaining == 0 else current_state
        connection.execute(
            """
            UPDATE ontology_reasoning_mailbox_events
            SET unresolved_entry_count = %s, state = %s,
                terminal_reason = %s, updated_at = %s
            WHERE event_id = %s
            """,
            (remaining, next_state, reason[:255] if remaining == 0 else "", utc_now(), event_id),
        )
        return next_state if remaining == 0 else ""

    def pending(self, limit: int = 100) -> List[Dict[str, object]]:
        bounded = max(1, min(1000, int(limit or 100)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT mailbox.mailbox_key, mailbox.source_event_id, mailbox.account_scope, mailbox.symbol,
                       mailbox.fact_family, mailbox.trigger_name, mailbox.review_level, mailbox.priority_hint,
                       mailbox.occurred_at, events.event_json
                FROM ontology_reasoning_mailbox mailbox
                INNER JOIN ontology_reasoning_mailbox_events events ON events.event_id = mailbox.source_event_id
                WHERE events.state = 'pending'
                ORDER BY mailbox.priority_hint DESC, mailbox.occurred_at ASC, mailbox.mailbox_key ASC
                LIMIT %s
                """,
                (bounded,),
            ).fetchall()
        result = []
        for row in rows or []:
            event = _json_loads(row.get("event_json"), {})
            if not event:
                continue
            result.append({
                "mailboxKey": _text(row.get("mailbox_key")),
                "sourceEventId": _text(row.get("source_event_id")),
                "accountScope": _text(row.get("account_scope")),
                "symbol": _text(row.get("symbol")).upper(),
                "factFamily": _text(row.get("fact_family")),
                "trigger": _text(row.get("trigger_name")),
                "reviewLevel": _text(row.get("review_level")),
                "priorityHint": int(row.get("priority_hint") or 0),
                "occurredAt": _text(row.get("occurred_at")),
                "sourceEvent": event,
            })
        return result

    def acknowledge(self, mailbox_keys: Iterable[object], state: str = "completed", reason: str = "") -> Dict[str, str]:
        """Remove rows only when they still point at the reasoned source event.

        TypeDB inference can take longer than the market-data interval.  A
        newer observation may replace a mailbox row while the previous cycle
        is running; acknowledging that old cycle must never delete the newer
        observation.
        """
        terminal: Dict[str, str] = {}
        expected_by_key: Dict[str, str] = {}
        clean = []
        for item in mailbox_keys or []:
            if isinstance(item, Mapping):
                key = _text(item.get("mailboxKey") or item.get("mailbox_key"))
                expected = _text(item.get("sourceEventId") or item.get("source_event_id"))
            else:
                key = _text(item)
                expected = ""
            if key and key not in clean:
                clean.append(key)
                expected_by_key[key] = expected
        if not clean:
            return terminal
        terminal_state = state if state in TERMINAL_STATES else "completed"
        with self.transaction() as connection:
            for mailbox_key in clean:
                row = connection.execute(
                    "SELECT source_event_id FROM ontology_reasoning_mailbox WHERE mailbox_key = %s FOR UPDATE",
                    (mailbox_key,),
                ).fetchone()
                if not row:
                    continue
                event_id = _text(row.get("source_event_id"))
                expected = expected_by_key.get(mailbox_key) or ""
                if expected and event_id != expected:
                    continue
                connection.execute("DELETE FROM ontology_reasoning_mailbox WHERE mailbox_key = %s", (mailbox_key,))
                final_state = self._decrement_source_event(connection, event_id, terminal_state, reason or terminal_state)
                if final_state:
                    terminal[event_id] = final_state
        return terminal

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            mailbox_row = connection.execute(
                "SELECT COUNT(*) AS count, MIN(occurred_at) AS oldest_at FROM ontology_reasoning_mailbox"
            ).fetchone() or {}
            state_rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM ontology_reasoning_mailbox_events GROUP BY state"
            ).fetchall()
        states = {_text(row.get("state")): int(row.get("count") or 0) for row in state_rows or []}
        return {
            "enabled": True,
            "pendingEntryCount": int(mailbox_row.get("count") or 0),
            "oldestPendingAt": _text(mailbox_row.get("oldest_at")),
            "eventStateCounts": states,
        }

    def prune_terminal(self, retention_hours: int = 72, limit: int = 1000) -> int:
        hours = max(1, min(24 * 90, int(retention_hours or 72)))
        bounded = max(1, min(10000, int(limit or 1000)))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM ontology_reasoning_mailbox_events
                WHERE state IN ('completed', 'superseded', 'expired') AND updated_at < %s
                LIMIT %s
                """,
                (cutoff, bounded),
            )
        return int(cursor.rowcount or 0)
