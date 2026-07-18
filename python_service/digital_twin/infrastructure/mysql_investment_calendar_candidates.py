from typing import Dict, List

from ..domain.investment_calendar_candidates import InvestmentCalendarReviewCandidate
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


def candidate_from_row(row) -> InvestmentCalendarReviewCandidate:
    payload = _json_loads(row["payload_json"], {})
    if not isinstance(payload, dict):
        payload = {}
    payload.update({
        "candidateId": row["candidate_id"],
        "proposedEventId": row["proposed_event_id"],
        "title": row["title"],
        "eventType": row["event_type"],
        "startsAt": row["starts_at"],
        "timezone": row["timezone_name"],
        "allDay": bool(row["all_day"]),
        "status": row["status"],
        "reviewReason": row["review_reason"],
        "importance": row["importance"],
        "confidence": row["confidence"],
        "symbols": _json_loads(row["symbols_json"], []),
        "markets": _json_loads(row["markets_json"], []),
        "accountIds": _json_loads(row["account_ids_json"], []),
        "source": row["source"],
        "sourceUrl": row["source_url"],
        "notes": row["notes"],
        "reminderOffsetsMinutes": _json_loads(row["reminder_offsets_json"], []),
        "sourceEvidenceId": row["source_evidence_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "reviewedAt": row["reviewed_at"],
        "reviewNote": row["review_note"],
    })
    return InvestmentCalendarReviewCandidate.from_payload(payload)


class MySQLInvestmentCalendarCandidateStore(MySQLOperationalConnection):
    def upsert(self, payload: Dict[str, object]) -> bool:
        candidate = InvestmentCalendarReviewCandidate.from_payload(payload)
        if not candidate.candidate_id:
            raise ValueError("candidateId는 필요합니다.")
        stamp = utc_now()
        candidate.updated_at = stamp
        if not candidate.created_at:
            candidate.created_at = stamp
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO investment_calendar_candidates (
                    candidate_id, proposed_event_id, title, event_type, starts_at, timezone_name, all_day,
                    status, review_reason, importance, confidence, symbols_json, markets_json, account_ids_json,
                    source, source_url, notes, reminder_offsets_json, source_evidence_id, payload_json,
                    created_at, updated_at, reviewed_at, review_note
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    title = IF(status = 'pending', VALUES(title), title),
                    event_type = IF(status = 'pending', VALUES(event_type), event_type),
                    starts_at = IF(status = 'pending', VALUES(starts_at), starts_at),
                    timezone_name = IF(status = 'pending', VALUES(timezone_name), timezone_name),
                    all_day = IF(status = 'pending', VALUES(all_day), all_day),
                    review_reason = IF(status = 'pending', VALUES(review_reason), review_reason),
                    importance = IF(status = 'pending', VALUES(importance), importance),
                    confidence = IF(status = 'pending', VALUES(confidence), confidence),
                    symbols_json = IF(status = 'pending', VALUES(symbols_json), symbols_json),
                    markets_json = IF(status = 'pending', VALUES(markets_json), markets_json),
                    account_ids_json = IF(status = 'pending', VALUES(account_ids_json), account_ids_json),
                    source = IF(status = 'pending', VALUES(source), source),
                    source_url = IF(status = 'pending', VALUES(source_url), source_url),
                    notes = IF(status = 'pending', VALUES(notes), notes),
                    reminder_offsets_json = IF(status = 'pending', VALUES(reminder_offsets_json), reminder_offsets_json),
                    source_evidence_id = IF(status = 'pending', VALUES(source_evidence_id), source_evidence_id),
                    payload_json = IF(status = 'pending', VALUES(payload_json), payload_json),
                    updated_at = VALUES(updated_at)
                """,
                (
                    candidate.candidate_id,
                    candidate.proposed_event_id,
                    candidate.title,
                    candidate.event_type,
                    candidate.starts_at,
                    candidate.timezone,
                    1 if candidate.all_day else 0,
                    candidate.status,
                    candidate.review_reason,
                    int(candidate.importance or 0),
                    float(candidate.confidence or 0),
                    json_dumps(candidate.symbols),
                    json_dumps(candidate.markets),
                    json_dumps(candidate.account_ids),
                    candidate.source,
                    candidate.source_url,
                    candidate.notes,
                    json_dumps(candidate.reminder_offsets_minutes),
                    candidate.source_evidence_id,
                    json_dumps(candidate.to_dict()),
                    candidate.created_at,
                    candidate.updated_at,
                    candidate.reviewed_at,
                    candidate.review_note,
                ),
            )
        return bool(cursor.rowcount)

    def get(self, candidate_id: str):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM investment_calendar_candidates WHERE candidate_id = %s",
                (str(candidate_id or ""),),
            ).fetchone()
        return candidate_from_row(row) if row else None

    def list(self, status: str = "pending", limit: int = 100, offset: int = 0) -> List[InvestmentCalendarReviewCandidate]:
        clauses = []
        params = []
        if status:
            clauses.append("status = %s")
            params.append(str(status))
        params.append(max(1, min(500, int(limit or 100))))
        params.append(max(0, int(offset or 0)))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM investment_calendar_candidates" + where + " ORDER BY created_at DESC, candidate_id LIMIT %s OFFSET %s",
                params,
            ).fetchall()
        return [candidate_from_row(row) for row in rows]

    def count(self, status: str = "pending") -> int:
        clauses = []
        params = []
        if status:
            clauses.append("status = %s")
            params.append(str(status))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM investment_calendar_candidates" + where,
                params,
            ).fetchone()
        return int((row or {}).get("count") or 0)

    def mark_status(self, candidate_id: str, status: str, review_note: str = ""):
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE investment_calendar_candidates
                SET status = %s, reviewed_at = %s, review_note = %s, updated_at = %s
                WHERE candidate_id = %s
                """,
                (str(status or ""), stamp, str(review_note or ""), stamp, str(candidate_id or "")),
            )
        return self.get(candidate_id)

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM investment_calendar_candidates
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
        return {row["status"]: int(row["count"] or 0) for row in rows}

    def feedback_summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type, status, COUNT(*) AS count
                FROM investment_calendar_candidates
                WHERE status IN ('registered', 'rejected')
                GROUP BY event_type, status
                """
            ).fetchall()
        summary: Dict[str, Dict[str, int]] = {}
        for row in rows:
            event_type = str(row["event_type"] or "custom")
            bucket = summary.setdefault(event_type, {"accepted": 0, "rejected": 0})
            if row["status"] == "registered":
                bucket["accepted"] += int(row["count"] or 0)
            elif row["status"] == "rejected":
                bucket["rejected"] += int(row["count"] or 0)
        return summary
