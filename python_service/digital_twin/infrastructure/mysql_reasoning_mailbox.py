"""Durable latest-state mailbox for TypeDB reasoning requests.

The event log remains the audit source of truth. This store only keeps the
newest pending fungible observation for each account/symbol/fact family so a
slow native TypeDB cycle does not replay stale source updates before current
data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import socket
from typing import Dict, Iterable, List, Mapping

from ..domain.ontology_reasoning_queue import (
    durable_mailbox_entries,
    event_as_dict,
    event_has_reasoning_work,
)
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


TERMINAL_STATES = {"completed", "superseded", "expired"}
DIRECT_WORK_PREFIX = "direct:"


def _text(value: object) -> str:
    return str(value or "").strip()


def direct_work_key(event_id: object) -> str:
    clean = _text(event_id)
    return DIRECT_WORK_PREFIX + clean if clean else ""


def local_reasoning_watch_pid(owner: object, hostname: str = "") -> int:
    """Return a local scheduler or one-shot PID encoded in a durable lease owner.

    A PID alone is only safe to inspect on the machine that created it. New
    owners include a hostname; the legacy ``reasoning-watch:<pid>`` and
    ``reasoning:<pid>`` shapes are retained as local-only compatibility cases
    for leases created before the host component existed.
    """
    value = _text(owner)
    prefixes = ("reasoning-watch:", "reasoning:")
    prefix = next((candidate for candidate in prefixes if value.startswith(candidate)), "")
    if not prefix:
        return 0
    remainder = value[len(prefix):]
    parts = remainder.rsplit(":", 1)
    local_hostname = _text(hostname) or socket.gethostname()
    if len(parts) == 2:
        owner_hostname, raw_pid = parts
        if _text(owner_hostname) != local_hostname:
            return 0
    else:
        raw_pid = remainder
    try:
        return max(0, int(raw_pid))
    except (TypeError, ValueError):
        return 0


def local_reasoning_watch_is_dead(owner: object, hostname: str = "", process_alive=None) -> bool:
    pid = local_reasoning_watch_pid(owner, hostname)
    if pid <= 0:
        return False
    alive = process_alive
    if not callable(alive):
        def alive(candidate: int) -> bool:
            try:
                os.kill(candidate, 0)
                return True
            except PermissionError:
                return True
            except OSError:
                return False
    try:
        return not bool(alive(pid))
    except Exception:
        # An uncertain process check must retain the lease until its normal
        # expiry; never recover work based on an inspection failure.
        return False


def _older_than(value: object, now: datetime, seconds: int) -> bool:
    stamp = _text(value)
    if not stamp:
        return True
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed.astimezone(timezone.utc)).total_seconds() >= max(0, int(seconds or 0))


def _age_grace_remaining_seconds(value: object, now: datetime, seconds: int) -> int:
    stamp = _text(value)
    if not stamp:
        return 0
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    remaining = max(0.0, float(max(0, int(seconds or 0))) - (now - parsed.astimezone(timezone.utc)).total_seconds())
    return int(remaining + 0.999999) if remaining else 0


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


def _priority_hint(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _checkpoint_metadata(entry: Mapping[str, object]) -> Dict[str, object]:
    return {
        "sourceEventId": _text(entry.get("sourceEventId") or entry.get("source_event_id")),
        "workClass": _text(entry.get("workClass") or entry.get("work_class")),
        "impactScope": _text(entry.get("impactScope") or entry.get("impact_scope")),
        "reasoningLane": _text(entry.get("reasoningLane") or entry.get("reasoning_lane")),
        "marketScope": _text(entry.get("marketScope") or entry.get("market_scope")),
        "ruleFamilies": list(entry.get("ruleFamilies") or entry.get("rule_families") or []),
        "revisionVector": dict(entry.get("revisionVector") or entry.get("revision_vector") or {}),
    }


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
                    "SELECT event_id FROM ontology_reasoning_mailbox_events "
                    "WHERE state <> 'direct-pending' AND event_id IN (" + placeholders + ")",
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
        with self.transaction() as connection:
            return self.enqueue_with_connection(connection, entries)

    @classmethod
    def enqueue_event_with_connection(cls, connection, event) -> Dict[str, object]:
        """Persist a coalescible reasoning request with its domain event.

        ``MySQLEventLog`` invokes this inside the same transaction that writes
        the audit event.  A successful event therefore cannot be invisible to
        the live queue, while an unsupported non-fungible event remains on the
        normal audited event path.
        """
        entries = durable_mailbox_entries(event)
        if not entries:
            return {
                "acceptedEntryKeys": [],
                "sameRevisionEntryKeys": [],
                "knownEventIds": [],
                "terminalEventStates": {},
                "enqueuedEventIds": [],
            }
        store = cls.__new__(cls)
        return store.enqueue_with_connection(connection, entries)

    @classmethod
    def ingress_event_with_connection(cls, connection, event) -> Dict[str, object]:
        """Register every actionable reasoning event in the durable ingress.

        Fungible realtime observations become latest-state mailbox slots.  A
        non-fungible request intentionally keeps its own ``direct-pending``
        marker so the repair reader never needs to rediscover completed events
        by scanning the entire domain-event history.
        """
        store = cls.__new__(cls)
        entries = durable_mailbox_entries(event)
        if entries:
            result = store.enqueue_with_connection(connection, entries)
            result["ingressKind"] = "mailbox"
            return result

        actionable = event_has_reasoning_work(event)
        state = "direct-pending" if actionable else "expired"
        event_payload = getattr(event, "payload", {})
        event_payload = dict(event_payload or {}) if isinstance(event_payload, Mapping) else {}
        contract = event_payload.get("factChangeContract")
        contract = dict(contract or {}) if isinstance(contract, Mapping) else {}
        if str(contract.get("status") or "").strip() == "blocked-unclassified":
            unknown = [
                _text(value)
                for value in contract.get("unclassifiedFactTypes") or []
                if _text(value)
            ]
            terminal_reason = "blocked unclassified fact types: " + ", ".join(unknown[:10])
        elif not actionable:
            terminal_reason = "no actionable changed facts"
        else:
            terminal_reason = "non-fungible reasoning request"
        store._record_direct_event_with_connection(
            connection,
            event,
            state=state,
            reason=terminal_reason,
        )
        return {
            "acceptedEntryKeys": [],
            "sameRevisionEntryKeys": [],
            "knownEventIds": [],
            "terminalEventStates": ({str(getattr(event, "event_id", "") or ""): state} if state in TERMINAL_STATES else {}),
            "enqueuedEventIds": [str(getattr(event, "event_id", "") or "")],
            "ingressKind": "direct",
        }

    @staticmethod
    def _record_direct_event_with_connection(connection, event, state: str, reason: str = "") -> None:
        event_id = _text(getattr(event, "event_id", ""))
        if not event_id:
            return
        stamp = utc_now()
        source_event = event_as_dict(event)
        connection.execute(
            """
            INSERT IGNORE INTO ontology_reasoning_mailbox_events (
                event_id, occurred_at, state, unresolved_entry_count, terminal_reason,
                event_json, created_at, updated_at
            ) VALUES (%s, %s, %s, 0, %s, %s, %s, %s)
            """,
            (
                event_id,
                _text(getattr(event, "occurred_at", "")),
                _text(state) or "direct-pending",
                _text(reason)[:255],
                json_dumps(source_event),
                stamp,
                stamp,
            ),
        )
        if _text(state) == "direct-pending":
            payload = source_event.get("payload") if isinstance(source_event.get("payload"), Mapping) else {}
            symbols = [
                _text(symbol).upper()
                for symbol in payload.get("symbols") or []
                if _text(symbol)
            ]
            MySQLOntologyReasoningMailboxStore._replace_work_item_with_connection(
                connection,
                {
                    "mailboxKey": direct_work_key(event_id),
                    "sourceEventId": event_id,
                    "workClass": "RESEARCH" if "research" in _text(payload.get("trigger")).lower() else "DIRECT",
                    "impactScope": "SUBJECT",
                    "reasoningLane": "RESEARCH_REASONING" if "research" in _text(payload.get("trigger")).lower() else "REALTIME_REASONING",
                    "marketScope": _text(payload.get("marketScope")) or "market",
                    "ruleFamilies": [],
                    "revisionVector": {},
                    "symbol": symbols[0] if len(symbols) == 1 else "",
                },
                stamp,
            )
            MySQLOntologyReasoningMailboxStore._refresh_queue_state_with_connection(connection)

    def enqueue_with_connection(self, connection, entries: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        """Transaction-aware form used by durable event ingress."""
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
        for event_id, event_entries in grouped.items():
            existing_event = connection.execute(
                "SELECT state FROM ontology_reasoning_mailbox_events WHERE event_id = %s FOR UPDATE",
                (event_id,),
            ).fetchone()
            existing_direct_marker = bool(existing_event and _text(existing_event.get("state")) == "direct-pending")
            if existing_event and not existing_direct_marker:
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
                    "SELECT mailbox.source_event_id, mailbox.occurred_at, mailbox.priority_hint, events.event_json "
                    "FROM ontology_reasoning_mailbox mailbox "
                    "LEFT JOIN ontology_reasoning_mailbox_events events ON events.event_id = mailbox.source_event_id "
                    "WHERE mailbox.mailbox_key = %s FOR UPDATE",
                    (mailbox_key,),
                ).fetchone()
                incoming_revision = _entry_fact_revision(entry)
                current_event = _json_loads(current.get("event_json"), {}) if current else {}
                current_revision = _event_fact_revision(current_event, entry.get("symbol"))
                incoming_priority = _priority_hint(entry.get("priorityHint"))
                current_priority = _priority_hint(current.get("priority_hint")) if current else 0
                if current and incoming_revision and incoming_revision == current_revision:
                    if incoming_priority > current_priority:
                        connection.execute(
                            "UPDATE ontology_reasoning_mailbox SET priority_hint = %s, updated_at = %s "
                            "WHERE mailbox_key = %s",
                            (incoming_priority, stamp, mailbox_key),
                        )
                    same_revision_skips += 1
                    result["sameRevisionEntryKeys"].append(mailbox_key)
                    continue
                if current and not _newer(
                    entry.get("occurredAt"), event_id, current.get("occurred_at"), current.get("source_event_id"),
                ):
                    continue
                if current:
                    displaced = _text(current.get("source_event_id"))
                    effective_entry = dict(entry)
                    # A material price observation may be superseded by a newer
                    # snapshot before the worker claims the slot. Retain its
                    # scheduling urgency while replacing the source facts.
                    effective_entry["priorityHint"] = max(incoming_priority, current_priority)
                    connection.execute(
                        """
                        UPDATE ontology_reasoning_mailbox
                        SET source_event_id = %s, account_scope = %s, symbol = %s, fact_family = %s,
                            work_class = %s, impact_scope = %s, reasoning_lane = %s, market_scope = %s,
                            rule_families_json = %s, revision_vector_json = %s,
                            trigger_name = %s, review_level = %s, priority_hint = %s, occurred_at = %s, updated_at = %s
                        WHERE mailbox_key = %s
                        """,
                        self._entry_values(effective_entry, stamp) + (mailbox_key,),
                    )
                    if displaced and displaced != event_id:
                        terminal = self._decrement_source_event(connection, displaced, "superseded", "newer fungible observation")
                        if terminal:
                            result["terminalEventStates"][displaced] = terminal
                else:
                    connection.execute(
                        """
                        INSERT INTO ontology_reasoning_mailbox (
                            mailbox_key, source_event_id, account_scope, symbol, fact_family,
                            work_class, impact_scope, reasoning_lane, market_scope,
                            rule_families_json, revision_vector_json, trigger_name,
                            review_level, priority_hint, occurred_at, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (mailbox_key,) + self._entry_values(entry, stamp) + (stamp,),
                    )
                self._replace_work_item_with_connection(
                    connection,
                    effective_entry if current else entry,
                    stamp,
                )
                accepted += 1
                result["acceptedEntryKeys"].append(mailbox_key)

            state = "pending" if accepted else "superseded"
            terminal_reason = "" if accepted else (
                "same fact revision already owns every mailbox slot"
                if same_revision_skips == len(event_entries)
                else "newer observation already owns every mailbox slot"
            )
            if existing_direct_marker:
                connection.execute(
                    """
                    UPDATE ontology_reasoning_mailbox_events
                    SET occurred_at = %s, state = %s, unresolved_entry_count = %s,
                        terminal_reason = %s, event_json = %s, updated_at = %s
                    WHERE event_id = %s AND state = 'direct-pending'
                    """,
                    (
                        _text(first.get("occurredAt")), state, accepted, terminal_reason,
                        json_dumps(first.get("sourceEvent") or {}), stamp, event_id,
                    ),
                )
            else:
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
        self._refresh_queue_state_with_connection(connection)
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
            _text(entry.get("workClass")) or "MARKET",
            _text(entry.get("impactScope")) or "SUBJECT",
            _text(entry.get("reasoningLane")) or "REALTIME_REASONING",
            _text(entry.get("marketScope")) or "market",
            json_dumps(list(entry.get("ruleFamilies") or [])),
            json_dumps(dict(entry.get("revisionVector") or {})),
            _text(entry.get("trigger")),
            _text(entry.get("reviewLevel")) or "normal",
            int(entry.get("priorityHint") or 0),
            _text(entry.get("occurredAt")),
            stamp,
        )

    @staticmethod
    def _replace_work_item_with_connection(connection, entry: Mapping[str, object], stamp: str) -> None:
        """Reset only the durable work record for a newer mailbox revision.

        The mailbox row remains the source of truth.  This checkpoint record
        is intentionally best-effort so an operational telemetry migration
        can never reject a valid market event.
        """
        try:
            connection.execute(
                """
                INSERT INTO ontology_reasoning_work_items (
                    mailbox_key, source_event_id, work_state, lease_owner, lease_until, not_before_at,
                    attempt_count, last_stage, stage_started_at, heartbeat_at, checkpoint_json,
                    last_error, created_at, updated_at
                ) VALUES (%s, %s, 'queued', '', '', '', 0, 'queued', '', %s, %s, '', %s, %s)
                ON DUPLICATE KEY UPDATE
                    source_event_id = VALUES(source_event_id), work_state = 'queued', lease_owner = '',
                    lease_until = '', not_before_at = '', attempt_count = 0, last_stage = 'queued',
                    stage_started_at = '', heartbeat_at = VALUES(heartbeat_at),
                    checkpoint_json = VALUES(checkpoint_json), last_error = '', updated_at = VALUES(updated_at)
                """,
                (
                    _text(entry.get("mailboxKey")),
                    _text(entry.get("sourceEventId")),
                    stamp,
                    json_dumps({
                        "version": "reasoning-work-checkpoint-v2",
                        **_checkpoint_metadata(entry),
                        "stage": "queued",
                        "updatedAt": stamp,
                    }),
                    stamp,
                    stamp,
                ),
            )
        except Exception:
            return

    @staticmethod
    def _remove_work_item_with_connection(connection, mailbox_key: str, source_event_id: str) -> None:
        try:
            connection.execute(
                "DELETE FROM ontology_reasoning_work_items "
                "WHERE mailbox_key = %s AND source_event_id = %s",
                (mailbox_key, source_event_id),
            )
        except Exception:
            return

    @staticmethod
    def _refresh_queue_state_with_connection(connection) -> Dict[str, object]:
        """Materialize an O(1) queue summary outside the hot probe path."""
        try:
            stamp = utc_now()
            row = connection.execute(
                """
                SELECT COUNT(*) AS pending_count,
                       MIN(mailbox.occurred_at) AS oldest_pending_at,
                       COUNT(DISTINCT mailbox.symbol) AS pending_symbol_count,
                       SUM(CASE WHEN work.work_state = 'running' AND work.lease_until > %s THEN 1 ELSE 0 END) AS running_count,
                       SUM(CASE WHEN work.work_state = 'retrying' THEN 1 ELSE 0 END) AS retrying_count
                FROM ontology_reasoning_mailbox mailbox
                LEFT JOIN ontology_reasoning_work_items work
                  ON work.mailbox_key = mailbox.mailbox_key
                 AND work.source_event_id = mailbox.source_event_id
                """,
                (stamp,),
            ).fetchone() or {}
            direct = connection.execute(
                """
                SELECT COUNT(*) AS pending_count,
                       MIN(events.occurred_at) AS oldest_pending_at,
                       SUM(CASE WHEN work.work_state = 'running' AND work.lease_until > %s THEN 1 ELSE 0 END) AS running_count,
                       SUM(CASE WHEN work.work_state = 'retrying' THEN 1 ELSE 0 END) AS retrying_count
                FROM ontology_reasoning_mailbox_events events
                LEFT JOIN ontology_reasoning_work_items work
                  ON work.mailbox_key = CONCAT('direct:', events.event_id)
                 AND work.source_event_id = events.event_id
                WHERE events.state = 'direct-pending'
                """,
                (stamp,),
            ).fetchone() or {}
            symbols = connection.execute(
                """
                SELECT symbol, MIN(occurred_at) AS oldest_at FROM ontology_reasoning_mailbox
                WHERE symbol <> '' GROUP BY symbol ORDER BY oldest_at, symbol LIMIT 80
                """
            ).fetchall()
            direct_events = connection.execute(
                """
                SELECT event_json FROM ontology_reasoning_mailbox_events
                WHERE state = 'direct-pending'
                ORDER BY occurred_at ASC, event_id ASC LIMIT 80
                """
            ).fetchall()
            pending_symbols = [
                _text(item.get("symbol")).upper()
                for item in symbols or []
                if _text(item.get("symbol"))
            ]
            for direct_event in direct_events or []:
                event = _json_loads(direct_event.get("event_json"), {})
                payload = event.get("payload") if isinstance(event, Mapping) and isinstance(event.get("payload"), Mapping) else {}
                for symbol in payload.get("symbols") or []:
                    clean_symbol = _text(symbol).upper()
                    if clean_symbol and clean_symbol not in pending_symbols:
                        pending_symbols.append(clean_symbol)
            active = connection.execute(
                """
                SELECT lease_owner, lease_until, last_stage, heartbeat_at
                FROM ontology_reasoning_work_items
                WHERE work_state = 'running' AND lease_until > %s
                ORDER BY heartbeat_at DESC, updated_at DESC LIMIT 1
                """,
                (stamp,),
            ).fetchone() or {}
            previous = connection.execute(
                "SELECT last_completed_at, last_timeout_at, version FROM ontology_reasoning_queue_state "
                "WHERE state_id = 'global' FOR UPDATE"
            ).fetchone() or {}
            values = {
                "pending": max(0, int(row.get("pending_count") or 0)) + max(0, int(direct.get("pending_count") or 0)),
                "running": max(0, int(row.get("running_count") or 0)) + max(0, int(direct.get("running_count") or 0)),
                "retrying": max(0, int(row.get("retrying_count") or 0)) + max(0, int(direct.get("retrying_count") or 0)),
                "symbols": pending_symbols[:80],
                "oldest": min(
                    [value for value in [_text(row.get("oldest_pending_at")), _text(direct.get("oldest_pending_at"))] if value]
                    or [""]
                ),
                "activeWorker": _text(active.get("lease_owner")),
                "activeLease": _text(active.get("lease_until")),
                "lastStage": _text(active.get("last_stage")),
                "lastStageAt": _text(active.get("heartbeat_at")),
                "lastCompleted": _text(previous.get("last_completed_at")),
                "lastTimeout": _text(previous.get("last_timeout_at")),
                "version": int(previous.get("version") or 0) + 1,
                "updatedAt": stamp,
            }
            connection.execute(
                """
                INSERT INTO ontology_reasoning_queue_state (
                    state_id, pending_entry_count, running_entry_count, retrying_entry_count,
                    pending_symbol_count, oldest_pending_at, pending_symbols_json,
                    active_worker_id, active_lease_until, last_stage, last_stage_at,
                    last_completed_at, last_timeout_at, version, updated_at
                ) VALUES ('global', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    pending_entry_count = VALUES(pending_entry_count),
                    running_entry_count = VALUES(running_entry_count),
                    retrying_entry_count = VALUES(retrying_entry_count),
                    pending_symbol_count = VALUES(pending_symbol_count),
                    oldest_pending_at = VALUES(oldest_pending_at),
                    pending_symbols_json = VALUES(pending_symbols_json),
                    active_worker_id = VALUES(active_worker_id),
                    active_lease_until = VALUES(active_lease_until),
                    last_stage = VALUES(last_stage), last_stage_at = VALUES(last_stage_at),
                    last_completed_at = VALUES(last_completed_at), last_timeout_at = VALUES(last_timeout_at),
                    version = VALUES(version), updated_at = VALUES(updated_at)
                """,
                (
                    values["pending"], values["running"], values["retrying"], len(values["symbols"]),
                    values["oldest"], json_dumps(values["symbols"]), values["activeWorker"],
                    values["activeLease"], values["lastStage"], values["lastStageAt"],
                    values["lastCompleted"], values["lastTimeout"], values["version"], values["updatedAt"],
                ),
            )
            return values
        except Exception:
            return {}

    def fast_state(self) -> Dict[str, object]:
        """Read the compact durable queue summary in one indexed lookup."""
        columns = (
            "pending_entry_count, running_entry_count, retrying_entry_count, pending_symbol_count, "
            "oldest_pending_at, pending_symbols_json, active_worker_id, active_lease_until, "
            "last_stage, last_stage_at, last_completed_at, last_timeout_at, version, updated_at"
        )
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT " + columns + " FROM ontology_reasoning_queue_state WHERE state_id = 'global'"
                ).fetchone() or {}
            if not _text(row.get("updated_at")):
                with self.transaction() as connection:
                    self._refresh_queue_state_with_connection(connection)
                with self.connect() as connection:
                    row = connection.execute(
                        "SELECT " + columns + " FROM ontology_reasoning_queue_state WHERE state_id = 'global'"
                    ).fetchone() or {}
            symbols = _json_loads(row.get("pending_symbols_json"), [])
            if not isinstance(symbols, list):
                symbols = []
            return {
                "enabled": True,
                "stateVersion": "durable-queue-state-v2",
                "pendingEntryCount": max(0, int(row.get("pending_entry_count") or 0)),
                "runningEntryCount": max(0, int(row.get("running_entry_count") or 0)),
                "retryingEntryCount": max(0, int(row.get("retrying_entry_count") or 0)),
                "pendingSymbolCount": max(0, int(row.get("pending_symbol_count") or 0)),
                "pendingSymbols": [str(item or "").upper().strip() for item in symbols if str(item or "").strip()],
                "oldestPendingAt": _text(row.get("oldest_pending_at")),
                "activeWorkerId": _text(row.get("active_worker_id")),
                "activeLeaseUntil": _text(row.get("active_lease_until")),
                "lastStage": _text(row.get("last_stage")),
                "lastStageAt": _text(row.get("last_stage_at")),
                "lastCompletedAt": _text(row.get("last_completed_at")),
                "lastTimeoutAt": _text(row.get("last_timeout_at")),
                "version": int(row.get("version") or 0),
                "updatedAt": _text(row.get("updated_at")),
            }
        except Exception as error:
            return {
                "enabled": True,
                "status": "error",
                "reason": str(error)[:180],
                "pendingEntryCount": 0,
                "pendingSymbolCount": 0,
                "pendingSymbols": [],
                "oldestPendingAt": "",
            }

    def claim(self, entries: Iterable[Mapping[str, object]], worker_id: str, lease_seconds: int = 300) -> Dict[str, object]:
        """Lease selected latest-state rows without ever deleting newer work."""
        clean = []
        metadata_by_revision = {}
        for item in entries or []:
            key = _text(item.get("mailboxKey") or item.get("mailbox_key"))
            event_id = _text(item.get("sourceEventId") or item.get("source_event_id"))
            if key and event_id and (key, event_id) not in clean:
                clean.append((key, event_id))
                metadata_by_revision[(key, event_id)] = _checkpoint_metadata(item)
        result = {
            "enabled": True,
            "claimed": [],
            "blocked": [],
            "resumed": [],
            "leaseOwner": _text(worker_id),
        }
        if not clean:
            return result
        try:
            seconds = max(30, min(3600, int(lease_seconds or 300)))
            now = datetime.now(timezone.utc)
            stamp = now.isoformat().replace("+00:00", "Z")
            lease_until = (now + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
            with self.transaction() as connection:
                for key, event_id in clean:
                    is_direct = key == direct_work_key(event_id)
                    if is_direct:
                        current = connection.execute(
                            "SELECT event_id AS source_event_id, state FROM ontology_reasoning_mailbox_events "
                            "WHERE event_id = %s FOR UPDATE",
                            (event_id,),
                        ).fetchone()
                        current_valid = bool(current and _text(current.get("state")) == "direct-pending")
                    else:
                        current = connection.execute(
                            "SELECT source_event_id FROM ontology_reasoning_mailbox WHERE mailbox_key = %s FOR UPDATE",
                            (key,),
                        ).fetchone()
                        current_valid = bool(current and _text(current.get("source_event_id")) == event_id)
                    if not current_valid:
                        result["blocked"].append({"mailboxKey": key, "sourceEventId": event_id, "reason": "newer-source"})
                        continue
                    work = connection.execute(
                        "SELECT source_event_id, work_state, lease_owner, lease_until, not_before_at, attempt_count, last_stage, checkpoint_json FROM ontology_reasoning_work_items "
                        "WHERE mailbox_key = %s FOR UPDATE",
                        (key,),
                    ).fetchone()
                    if work and _text(work.get("source_event_id")) != event_id:
                        result["blocked"].append({"mailboxKey": key, "sourceEventId": event_id, "reason": "work-revision"})
                        continue
                    if (
                        work
                        and _text(work.get("work_state")) == "retrying"
                        and _text(work.get("not_before_at")) > stamp
                    ):
                        result["blocked"].append({"mailboxKey": key, "sourceEventId": event_id, "reason": "retry-not-before"})
                        continue
                    if work and _text(work.get("work_state")) == "running" and _text(work.get("lease_until")) > stamp and _text(work.get("lease_owner")) not in {"", _text(worker_id)}:
                        result["blocked"].append({"mailboxKey": key, "sourceEventId": event_id, "reason": "leased"})
                        continue
                    previous_stage = _text(work.get("last_stage")) if work else ""
                    previous_checkpoint = _json_loads(work.get("checkpoint_json"), {}) if work else {}
                    # Only a checkpoint written after the monitor cycle has
                    # committed its snapshot and notification outbox may skip
                    # TypeDB work on retry. Earlier native-inference
                    # checkpoints are intentionally replayed because alert
                    # construction or durable delivery may not have run yet.
                    resumed_from_verified_inference = previous_stage == "post-monitor-inference-verified"
                    claim_stage = (
                        "resume-after-monitor-commit"
                        if resumed_from_verified_inference
                        else "snapshot-preparation"
                    )
                    checkpoint = {
                        "version": "reasoning-work-checkpoint-v2",
                        "stage": claim_stage,
                        "startedAt": stamp,
                        **metadata_by_revision.get((key, event_id), {}),
                    }
                    if resumed_from_verified_inference:
                        checkpoint["resumedFrom"] = "post-monitor-inference-verified"
                        checkpoint["previousCheckpoint"] = previous_checkpoint
                    connection.execute(
                        """
                        INSERT INTO ontology_reasoning_work_items (
                            mailbox_key, source_event_id, work_state, lease_owner, lease_until, not_before_at,
                            attempt_count, last_stage, stage_started_at, heartbeat_at, checkpoint_json,
                            last_error, created_at, updated_at
                        ) VALUES (%s, %s, 'running', %s, %s, '', 1, %s, %s, %s, %s, '', %s, %s)
                        ON DUPLICATE KEY UPDATE
                            source_event_id = VALUES(source_event_id), work_state = 'running',
                            lease_owner = VALUES(lease_owner), lease_until = VALUES(lease_until), not_before_at = '',
                            attempt_count = attempt_count + 1, last_stage = VALUES(last_stage),
                            stage_started_at = VALUES(stage_started_at), heartbeat_at = VALUES(heartbeat_at),
                            checkpoint_json = VALUES(checkpoint_json), last_error = '', updated_at = VALUES(updated_at)
                        """,
                        (
                            key, event_id, _text(worker_id), lease_until, claim_stage, stamp, stamp,
                            json_dumps(checkpoint),
                            stamp, stamp,
                        ),
                    )
                    result["claimed"].append({
                        "mailboxKey": key,
                        "sourceEventId": event_id,
                        **metadata_by_revision.get((key, event_id), {}),
                        # Expose the post-claim count to the application
                        # coordinator. It uses this only for bounded retry
                        # scheduling; latest-state ownership remains the
                        # mailbox key/source revision pair above.
                        "attemptCount": max(1, int((work or {}).get("attempt_count") or 0) + 1),
                    })
                    if resumed_from_verified_inference:
                        result["resumed"].append({
                            "mailboxKey": key,
                            "sourceEventId": event_id,
                            "resumedFrom": "post-monitor-inference-verified",
                            "checkpoint": previous_checkpoint,
                        })
                self._refresh_queue_state_with_connection(connection)
            return result
        except Exception as error:
            return {**result, "enabled": False, "reason": str(error)[:180]}

    @staticmethod
    def current_entries_with_connection(
        connection,
        entries: Iterable[Mapping[str, object]],
        lock_rows: bool = True,
    ) -> Dict[str, object]:
        """Prove that a claimed latest-state revision is still current.

        This is an operational delivery guard, not an investment decision. It
        runs inside the same MySQL transaction that creates notification jobs,
        so a newer mailbox ingress either wins before the guard or waits until
        the obsolete result has been explicitly classified. A stale inference
        is never allowed to enqueue an alert for the revision it no longer
        represents.
        """
        clean = []
        for item in entries or []:
            if not isinstance(item, Mapping):
                continue
            key = _text(item.get("mailboxKey") or item.get("mailbox_key"))
            event_id = _text(item.get("sourceEventId") or item.get("source_event_id"))
            symbol = _text(item.get("symbol")).upper()
            if key and event_id and (key, event_id, symbol) not in clean:
                clean.append((key, event_id, symbol))
        result = {"status": "ok", "current": [], "superseded": [], "missing": []}
        for key, expected_event_id, symbol in clean:
            if key == direct_work_key(expected_event_id):
                query = "SELECT event_id AS source_event_id, state FROM ontology_reasoning_mailbox_events WHERE event_id = %s"
                if lock_rows:
                    query += " FOR UPDATE"
                row = connection.execute(query, (expected_event_id,)).fetchone()
                if row and _text(row.get("state")) != "direct-pending":
                    row = None
            else:
                query = "SELECT source_event_id, symbol FROM ontology_reasoning_mailbox WHERE mailbox_key = %s"
                if lock_rows:
                    query += " FOR UPDATE"
                row = connection.execute(query, (key,)).fetchone()
            if not row:
                result["missing"].append({
                    "mailboxKey": key,
                    "sourceEventId": expected_event_id,
                    "symbol": symbol,
                    "reason": "mailbox-row-missing",
                })
                continue
            actual_event_id = _text(row.get("source_event_id"))
            actual_symbol = _text(row.get("symbol")).upper() or symbol
            item = {
                "mailboxKey": key,
                "sourceEventId": expected_event_id,
                "currentSourceEventId": actual_event_id,
                "symbol": actual_symbol,
            }
            if actual_event_id == expected_event_id:
                result["current"].append(item)
            else:
                result["superseded"].append({**item, "reason": "newer-source-event"})
        result["currentCount"] = len(result["current"])
        result["supersededCount"] = len(result["superseded"])
        result["missingCount"] = len(result["missing"])
        return result

    def current_entries(self, entries: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        """Read currentness outside a delivery transaction for diagnostics."""
        try:
            with self.connect() as connection:
                return self.current_entries_with_connection(connection, entries, lock_rows=False)
        except Exception as error:
            return {
                "status": "error",
                "reason": str(error)[:180],
                "current": [],
                "superseded": [],
                "missing": [],
                "currentCount": 0,
                "supersededCount": 0,
                "missingCount": 0,
            }

    def checkpoint(
        self,
        entries: Iterable[Mapping[str, object]],
        stage: str,
        details: Mapping[str, object] = None,
        worker_id: str = "",
    ) -> None:
        clean = []
        for item in entries or []:
            if not isinstance(item, Mapping):
                continue
            key = _text(item.get("mailboxKey") or item.get("mailbox_key"))
            event_id = _text(item.get("sourceEventId") or item.get("source_event_id"))
            if key and event_id:
                clean.append((key, event_id, _checkpoint_metadata(item)))
        if not clean:
            return
        stamp = utc_now()
        try:
            with self.transaction() as connection:
                for key, event_id, metadata in clean:
                    owner_clause = " AND lease_owner = %s AND work_state = 'running'" if _text(worker_id) else ""
                    parameters = [
                        _text(stage)[:64], stamp, _text(stage)[:64], stamp,
                        json_dumps({
                            "version": "reasoning-work-checkpoint-v2",
                            **metadata,
                            "stage": _text(stage)[:64],
                            "updatedAt": stamp,
                            "details": dict(details or {}),
                        }),
                        stamp, key, event_id,
                    ]
                    if _text(worker_id):
                        parameters.append(_text(worker_id))
                    connection.execute(
                        """
                        UPDATE ontology_reasoning_work_items
                        SET stage_started_at = CASE
                                WHEN last_stage <> %s OR stage_started_at = '' THEN %s
                                ELSE stage_started_at
                            END,
                            last_stage = %s, heartbeat_at = %s, checkpoint_json = %s, updated_at = %s
                        WHERE mailbox_key = %s AND source_event_id = %s
                        """ + owner_clause,
                        parameters,
                    )
                self._refresh_queue_state_with_connection(connection)
        except Exception:
            return

    def release(
        self,
        entries: Iterable[Mapping[str, object]],
        reason: str,
        retry_after_seconds: int = 30,
        worker_id: str = "",
    ) -> None:
        clean = [
            (_text(item.get("mailboxKey") or item.get("mailbox_key")), _text(item.get("sourceEventId") or item.get("source_event_id")))
            for item in entries or [] if isinstance(item, Mapping)
        ]
        clean = [(key, event_id) for key, event_id in clean if key and event_id]
        if not clean:
            return
        now = datetime.now(timezone.utc)
        stamp = now.isoformat().replace("+00:00", "Z")
        retry_at = (now + timedelta(seconds=max(1, min(3600, int(retry_after_seconds or 30))))).isoformat().replace("+00:00", "Z")
        try:
            with self.transaction() as connection:
                for key, event_id in clean:
                    owner_clause = " AND lease_owner = %s AND work_state = 'running'" if _text(worker_id) else ""
                    parameters = [retry_at, stamp, _text(reason)[:255], stamp, key, event_id]
                    if _text(worker_id):
                        parameters.append(_text(worker_id))
                    connection.execute(
                        """
                        UPDATE ontology_reasoning_work_items
                        SET work_state = 'retrying', lease_owner = '', lease_until = '', not_before_at = %s,
                            last_stage = 'retry-scheduled', heartbeat_at = %s, last_error = %s, updated_at = %s
                        WHERE mailbox_key = %s AND source_event_id = %s
                        """ + owner_clause,
                        parameters,
                    )
                self._refresh_queue_state_with_connection(connection)
        except Exception:
            return

    def recover_worker_timeout(
        self,
        worker_id: str,
        retry_after_seconds: int,
        reason: str = "isolated reasoning worker exceeded its execution deadline",
    ) -> int:
        """Release only the killed isolated worker's leases after its parent confirms timeout."""
        owner = _text(worker_id)
        if not owner:
            return 0
        now = datetime.now(timezone.utc)
        stamp = now.isoformat().replace("+00:00", "Z")
        retry_at = (now + timedelta(seconds=max(5, min(3600, int(retry_after_seconds or 30))))).isoformat().replace("+00:00", "Z")
        try:
            with self.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE ontology_reasoning_work_items
                    SET work_state = 'retrying', lease_owner = '', lease_until = '', not_before_at = %s,
                        last_stage = 'timeout-retry-scheduled', heartbeat_at = %s,
                        last_error = %s, updated_at = %s
                    WHERE work_state = 'running' AND lease_owner = %s
                    """,
                    (retry_at, stamp, _text(reason)[:255], stamp, owner),
                )
                self._refresh_queue_state_with_connection(connection)
                return int(cursor.rowcount or 0)
        except Exception:
            return 0

    def recover_dead_local_worker_leases(
        self,
        retry_after_seconds: int = 30,
        minimum_age_seconds: int = 30,
    ) -> Dict[str, object]:
        """Recover only leases whose local scheduler parent is confirmed dead.

        A controlled service restart terminates the isolated child through its
        parent process group. Its durable work lease should not block the new
        parent for the full lease period. The age guard gives that child its
        signal grace window; remote or unrecognised owners are left untouched
        for normal lease-expiry recovery.
        """
        now = datetime.now(timezone.utc)
        stamp = now.isoformat().replace("+00:00", "Z")
        retry_at = (now + timedelta(seconds=max(5, min(900, int(retry_after_seconds or 30))))).isoformat().replace("+00:00", "Z")
        result = {
            "enabled": True,
            "recovered": [],
            "protected": 0,
            "waitingForGraceCount": 0,
            "retryAfterSeconds": 0,
        }
        try:
            with self.transaction() as connection:
                rows = connection.execute(
                    """
                    SELECT mailbox_key, source_event_id, lease_owner, heartbeat_at, updated_at
                    FROM ontology_reasoning_work_items
                    WHERE work_state = 'running'
                      AND (lease_owner LIKE %s OR lease_owner LIKE %s)
                    FOR UPDATE
                    """,
                    ("reasoning-watch:%", "reasoning:%"),
                ).fetchall()
                for row in rows or []:
                    owner = _text(row.get("lease_owner"))
                    observed_at = _text(row.get("heartbeat_at")) or _text(row.get("updated_at"))
                    if not local_reasoning_watch_is_dead(owner):
                        result["protected"] += 1
                        continue
                    if not _older_than(observed_at, now, minimum_age_seconds):
                        remaining = max(1, _age_grace_remaining_seconds(observed_at, now, minimum_age_seconds))
                        result["waitingForGraceCount"] += 1
                        current = int(result.get("retryAfterSeconds") or 0)
                        result["retryAfterSeconds"] = remaining if not current else min(current, remaining)
                        continue
                    key = _text(row.get("mailbox_key"))
                    event_id = _text(row.get("source_event_id"))
                    cursor = connection.execute(
                        """
                        UPDATE ontology_reasoning_work_items
                        SET work_state = 'retrying', lease_owner = '', lease_until = '', not_before_at = %s,
                            last_stage = 'orphaned-worker-retry-scheduled', heartbeat_at = %s,
                            last_error = %s, updated_at = %s
                        WHERE mailbox_key = %s AND source_event_id = %s
                          AND work_state = 'running' AND lease_owner = %s
                        """,
                        (
                            retry_at, stamp, "previous local reasoning scheduler is no longer running",
                            stamp, key, event_id, owner,
                        ),
                    )
                    if int(cursor.rowcount or 0) > 0:
                        result["recovered"].append({"mailboxKey": key, "sourceEventId": event_id, "leaseOwner": owner})
                if result["recovered"]:
                    self._refresh_queue_state_with_connection(connection)
            return result
        except Exception as error:
            return {**result, "enabled": False, "reason": str(error)[:180]}

    def record_timeout(self, details: Mapping[str, object] = None) -> None:
        stamp = utc_now()
        try:
            with self.transaction() as connection:
                connection.execute(
                    "UPDATE ontology_reasoning_queue_state SET last_timeout_at = %s, last_stage = %s, "
                    "last_stage_at = %s, updated_at = %s, version = version + 1 WHERE state_id = 'global'",
                    (stamp, _text((details or {}).get("stage") or "timeout")[:64], stamp, stamp),
                )
        except Exception:
            return

    def record_completion(self, details: Mapping[str, object] = None) -> None:
        stamp = utc_now()
        try:
            with self.transaction() as connection:
                connection.execute(
                    "UPDATE ontology_reasoning_queue_state SET last_completed_at = %s, last_stage = %s, "
                    "last_stage_at = %s, updated_at = %s, version = version + 1 WHERE state_id = 'global'",
                    (stamp, _text((details or {}).get("stage") or "completed")[:64], stamp, stamp),
                )
        except Exception:
            return

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
        stamp = utc_now()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT mailbox.mailbox_key, mailbox.source_event_id, mailbox.account_scope, mailbox.symbol,
                       mailbox.fact_family, mailbox.work_class, mailbox.impact_scope, mailbox.reasoning_lane,
                       mailbox.market_scope, mailbox.rule_families_json, mailbox.revision_vector_json,
                       mailbox.trigger_name, mailbox.review_level, mailbox.priority_hint,
                       mailbox.occurred_at, events.event_json
                FROM ontology_reasoning_mailbox mailbox
                INNER JOIN ontology_reasoning_mailbox_events events ON events.event_id = mailbox.source_event_id
                LEFT JOIN ontology_reasoning_work_items work
                  ON work.mailbox_key = mailbox.mailbox_key
                 AND work.source_event_id = mailbox.source_event_id
                WHERE events.state = 'pending'
                  AND (
                    work.mailbox_key IS NULL
                    OR work.work_state = 'queued'
                    OR (work.work_state = 'retrying' AND (work.not_before_at = '' OR work.not_before_at <= %s))
                    OR (work.work_state = 'running' AND (work.lease_until = '' OR work.lease_until <= %s))
                  )
                ORDER BY mailbox.priority_hint DESC, mailbox.occurred_at ASC, mailbox.mailbox_key ASC
                LIMIT %s
                """,
                (stamp, stamp, bounded),
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
                "workClass": _text(row.get("work_class")) or "MARKET",
                "impactScope": _text(row.get("impact_scope")) or "SUBJECT",
                "reasoningLane": _text(row.get("reasoning_lane")) or "REALTIME_REASONING",
                "marketScope": _text(row.get("market_scope")) or "market",
                "ruleFamilies": list(_json_loads(row.get("rule_families_json"), []) or []),
                "revisionVector": dict(_json_loads(row.get("revision_vector_json"), {}) or {}),
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
                expected = expected_by_key.get(mailbox_key) or ""
                if expected and mailbox_key == direct_work_key(expected):
                    row = connection.execute(
                        "SELECT state FROM ontology_reasoning_mailbox_events WHERE event_id = %s FOR UPDATE",
                        (expected,),
                    ).fetchone()
                    if not row or _text(row.get("state")) != "direct-pending":
                        continue
                    connection.execute(
                        "UPDATE ontology_reasoning_mailbox_events SET state = %s, terminal_reason = %s, updated_at = %s "
                        "WHERE event_id = %s AND state = 'direct-pending'",
                        (terminal_state, _text(reason or terminal_state)[:255], utc_now(), expected),
                    )
                    self._remove_work_item_with_connection(connection, mailbox_key, expected)
                    terminal[expected] = terminal_state
                    continue
                row = connection.execute(
                    "SELECT source_event_id FROM ontology_reasoning_mailbox WHERE mailbox_key = %s FOR UPDATE",
                    (mailbox_key,),
                ).fetchone()
                if not row:
                    continue
                event_id = _text(row.get("source_event_id"))
                if expected and event_id != expected:
                    continue
                connection.execute("DELETE FROM ontology_reasoning_mailbox WHERE mailbox_key = %s", (mailbox_key,))
                self._remove_work_item_with_connection(connection, mailbox_key, event_id)
                final_state = self._decrement_source_event(connection, event_id, terminal_state, reason or terminal_state)
                if final_state:
                    terminal[event_id] = final_state
            self._refresh_queue_state_with_connection(connection)
        return terminal

    def terminalize_direct_events(
        self,
        events: Iterable[object],
        state: str = "completed",
        reason: str = "",
    ) -> List[str]:
        """Seal direct-ingress rows so they never re-enter repair scans.

        Existing mailbox-backed events are intentionally untouched.  The same
        helper also seals already-cursor-processed legacy rows that predate
        atomic ingress, which makes rollout self-healing without a bulk scan.
        """
        terminal_state = state if state in TERMINAL_STATES else "completed"
        candidates = []
        for event in events or []:
            event_id = _text(getattr(event, "event_id", event if isinstance(event, str) else ""))
            if not event_id or event_id.startswith("mailbox:"):
                continue
            occurred_at = _text(getattr(event, "occurred_at", ""))
            source_event = event_as_dict(event) if not isinstance(event, str) else {}
            candidates.append((event_id, occurred_at, source_event))
        if not candidates:
            return []
        stamp = utc_now()
        sealed = []
        try:
            with self.transaction() as connection:
                for event_id, occurred_at, source_event in candidates:
                    connection.execute(
                        """
                        INSERT IGNORE INTO ontology_reasoning_mailbox_events (
                            event_id, occurred_at, state, unresolved_entry_count, terminal_reason,
                            event_json, created_at, updated_at
                        ) VALUES (%s, %s, %s, 0, %s, %s, %s, %s)
                        """,
                        (
                            event_id, occurred_at, terminal_state, _text(reason or terminal_state)[:255],
                            json_dumps(source_event), stamp, stamp,
                        ),
                    )
                    cursor = connection.execute(
                        """
                        UPDATE ontology_reasoning_mailbox_events
                        SET state = %s, terminal_reason = %s, updated_at = %s
                        WHERE event_id = %s AND state = 'direct-pending'
                        """,
                        (terminal_state, _text(reason or terminal_state)[:255], stamp, event_id),
                    )
                    if int(cursor.rowcount or 0) > 0:
                        sealed.append(event_id)
                    self._remove_work_item_with_connection(
                        connection,
                        direct_work_key(event_id),
                        event_id,
                    )
                self._refresh_queue_state_with_connection(connection)
        except Exception:
            return sealed
        return sealed

    def summary(self) -> Dict[str, object]:
        state = self.fast_state()
        active_checkpoint = self.active_checkpoint() if int(state.get("runningEntryCount") or 0) else {}
        return {
            "enabled": True,
            "pendingEntryCount": int(state.get("pendingEntryCount") or 0),
            "runningEntryCount": int(state.get("runningEntryCount") or 0),
            "retryingEntryCount": int(state.get("retryingEntryCount") or 0),
            "pendingSymbolCount": int(state.get("pendingSymbolCount") or 0),
            "pendingSymbols": list(state.get("pendingSymbols") or []),
            "oldestPendingAt": _text(state.get("oldestPendingAt")),
            "activeWorkerId": _text(state.get("activeWorkerId")),
            "activeLeaseUntil": _text(state.get("activeLeaseUntil")),
            "lastStage": _text(state.get("lastStage")),
            "lastStageAt": _text(state.get("lastStageAt")),
            "lastCompletedAt": _text(state.get("lastCompletedAt")),
            "lastTimeoutAt": _text(state.get("lastTimeoutAt")),
            "stateVersion": _text(state.get("stateVersion")),
            "updatedAt": _text(state.get("updatedAt")),
            **({"activeCheckpoint": active_checkpoint} if active_checkpoint else {}),
            "eventStateCounts": {},
            **({"status": "error", "reason": _text(state.get("reason"))} if _text(state.get("status")) == "error" else {}),
        }

    def active_checkpoint(self) -> Dict[str, object]:
        """Read the active durable stage without widening the hot queue probe.

        The scheduler invokes this only for a timeout or operator summary, so
        the normal queue probe remains a single indexed queue-state lookup.
        """
        stamp = utc_now()
        try:
            with self.connect() as connection:
                row = connection.execute(
                    """
                    SELECT mailbox_key, source_event_id, lease_owner, lease_until, last_stage,
                           stage_started_at, heartbeat_at, checkpoint_json
                    FROM ontology_reasoning_work_items
                    WHERE work_state = 'running' AND lease_until > %s
                    ORDER BY heartbeat_at DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (stamp,),
                ).fetchone() or {}
            if not row:
                return {}
            checkpoint = _json_loads(row.get("checkpoint_json"), {})
            if not isinstance(checkpoint, dict):
                checkpoint = {}
            started = _text(row.get("stage_started_at"))
            age_seconds = 0
            if started:
                try:
                    parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    age_seconds = max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
                except ValueError:
                    age_seconds = 0
            return {
                "mailboxKey": _text(row.get("mailbox_key")),
                "sourceEventId": _text(row.get("source_event_id")),
                "workerId": _text(row.get("lease_owner")),
                "leaseUntil": _text(row.get("lease_until")),
                "stage": _text(row.get("last_stage")),
                "stageStartedAt": started,
                "heartbeatAt": _text(row.get("heartbeat_at")),
                "stageElapsedSeconds": age_seconds,
                "details": dict(checkpoint.get("details") or {}),
            }
        except Exception:
            return {}

    def prune_terminal(self, retention_hours: int = 24, limit: int = 50) -> int:
        # This is operational history, not the source event log.  Keep its
        # transaction short even when a legacy worker passes wider limits.
        hours = max(1, min(24, int(retention_hours or 24)))
        bounded = max(1, min(50, int(limit or 50)))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
        def delete(connection):
            cursor = connection.execute(
                """
                DELETE FROM ontology_reasoning_mailbox_events
                WHERE state IN ('completed', 'superseded', 'expired') AND updated_at < %s
                ORDER BY updated_at ASC, event_id ASC LIMIT %s
                """,
                (cutoff, bounded),
            )
            return int(getattr(cursor, "rowcount", 0) or 0)

        return int(self.transaction_with_deadlock_retry("reasoning-mailbox-prune-terminal", delete) or 0)
