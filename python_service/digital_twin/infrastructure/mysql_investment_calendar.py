from datetime import timedelta
from typing import Dict, List

from ..domain.investment_calendar import InvestmentCalendarEvent, parse_utc, utc_iso
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


def calendar_event_from_row(row) -> InvestmentCalendarEvent:
    payload = _json_loads(row["payload_json"], {})
    if not isinstance(payload, dict):
        payload = {}
    payload.update({
        "eventId": row["event_id"],
        "title": row["title"],
        "eventType": row["event_type"],
        "startsAt": row["starts_at"],
        "endsAt": row["ends_at"],
        "timezone": row["timezone_name"],
        "allDay": bool(row["all_day"]),
        "status": row["status"],
        "importance": row["importance"],
        "symbols": _json_loads(row["symbols_json"], []),
        "markets": _json_loads(row["markets_json"], []),
        "accountIds": _json_loads(row["account_ids_json"], []),
        "source": row["source"],
        "sourceUrl": row["source_url"],
        "notes": row["notes"],
        "reminderOffsetsMinutes": _json_loads(row["reminder_offsets_json"], []),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    })
    return InvestmentCalendarEvent.from_dict(payload)


class MySQLInvestmentCalendarStore(MySQLOperationalConnection):
    def upsert(self, event: InvestmentCalendarEvent) -> InvestmentCalendarEvent:
        stamp = utc_now()
        event.updated_at = stamp
        if not event.created_at:
            event.created_at = stamp
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO investment_calendar_events (
                    event_id, title, event_type, starts_at, ends_at, timezone_name, all_day, status,
                    importance, symbols_json, markets_json, account_ids_json, source, source_url, notes,
                    reminder_offsets_json, payload_json, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    event_type = VALUES(event_type),
                    starts_at = VALUES(starts_at),
                    ends_at = VALUES(ends_at),
                    timezone_name = VALUES(timezone_name),
                    all_day = VALUES(all_day),
                    status = VALUES(status),
                    importance = VALUES(importance),
                    symbols_json = VALUES(symbols_json),
                    markets_json = VALUES(markets_json),
                    account_ids_json = VALUES(account_ids_json),
                    source = VALUES(source),
                    source_url = VALUES(source_url),
                    notes = VALUES(notes),
                    reminder_offsets_json = VALUES(reminder_offsets_json),
                    payload_json = VALUES(payload_json),
                    updated_at = VALUES(updated_at)
                """,
                (
                    event.event_id,
                    event.title,
                    event.event_type,
                    event.starts_at,
                    event.ends_at,
                    event.timezone,
                    1 if event.all_day else 0,
                    event.status,
                    int(event.importance or 0),
                    json_dumps(event.symbols),
                    json_dumps(event.markets),
                    json_dumps(event.account_ids),
                    event.source,
                    event.source_url,
                    event.notes,
                    json_dumps(event.reminder_offsets_minutes),
                    json_dumps(event.to_dict()),
                    event.created_at,
                    event.updated_at,
                ),
            )
        return self.get(event.event_id) or event

    def get(self, event_id: str):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM investment_calendar_events WHERE event_id = %s",
                (str(event_id or ""),),
            ).fetchone()
        return calendar_event_from_row(row) if row else None

    def delete(self, event_id: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM investment_calendar_events WHERE event_id = %s",
                (str(event_id or ""),),
            )
        return bool(cursor.rowcount)

    def list(
        self,
        from_at: str = "",
        to_at: str = "",
        status: str = "",
        symbol: str = "",
        event_type: str = "",
        limit: int = 200,
    ) -> List[InvestmentCalendarEvent]:
        clauses = []
        params = []
        if from_at:
            clauses.append("starts_at >= %s")
            params.append(str(from_at))
        if to_at:
            clauses.append("starts_at <= %s")
            params.append(str(to_at))
        if status:
            clauses.append("status = %s")
            params.append(str(status))
        else:
            clauses.append("status <> 'deleted'")
        if event_type:
            clauses.append("event_type = %s")
            params.append(str(event_type))
        params.append(max(1, min(500, int(limit or 200))) * 3)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM investment_calendar_events" + where + " ORDER BY starts_at, importance DESC, event_id LIMIT %s",
                params,
            ).fetchall()
        events = [calendar_event_from_row(row) for row in rows]
        clean_symbol = str(symbol or "").upper().strip()
        if clean_symbol:
            events = [event for event in events if clean_symbol in event.symbols]
        return events[: max(1, min(500, int(limit or 200)))]

    def reminder_candidates(self, now_at: str = "", lookback_minutes: int = 180) -> List[InvestmentCalendarEvent]:
        now = parse_utc(now_at) or parse_utc(utc_now())
        start_at = utc_iso(now - timedelta(minutes=max(1, int(lookback_minutes or 180))))
        end_at = utc_iso(now + timedelta(days=31))
        return self.list(from_at=start_at, to_at=end_at, status="active", limit=500)

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) AS count FROM investment_calendar_events WHERE status <> 'deleted'"
            ).fetchone()
            upcoming = connection.execute(
                """
                SELECT COUNT(*) AS count, MIN(starts_at) AS next_starts_at
                FROM investment_calendar_events
                WHERE status IN ('active', 'tentative') AND starts_at >= %s
                """,
                (utc_now(),),
            ).fetchone()
            by_type_rows = connection.execute(
                """
                SELECT event_type, COUNT(*) AS count
                FROM investment_calendar_events
                WHERE status <> 'deleted'
                GROUP BY event_type
                ORDER BY count DESC, event_type
                """
            ).fetchall()
        return {
            "total": int(total["count"] if total else 0),
            "upcoming": int(upcoming["count"] if upcoming else 0),
            "nextStartsAt": str((upcoming or {}).get("next_starts_at") or ""),
            "byType": [
                {"eventType": row["event_type"], "count": int(row["count"] or 0)}
                for row in by_type_rows
            ],
        }
