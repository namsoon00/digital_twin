from typing import Dict

from ..domain.investment_calendar import clean_text
from ..domain.investment_calendar_candidates import (
    CANDIDATE_STATUS_REGISTERED,
    CANDIDATE_STATUS_REJECTED,
    InvestmentCalendarReviewCandidate,
)


class InvestmentCalendarCandidateService:
    def __init__(self, candidate_repository, calendar_service):
        self.candidate_repository = candidate_repository
        self.calendar_service = calendar_service

    def list_candidates(self, query: Dict[str, object] = None) -> Dict[str, object]:
        query = query if isinstance(query, dict) else {}
        status = clean_text(query.get("status") or "pending", 32)
        try:
            limit = int(float(str(query.get("limit") or 100)))
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(500, limit))
        candidates = self.candidate_repository.list(status=status, limit=limit)
        return {
            "candidates": [candidate.to_dict() for candidate in candidates],
            "summary": self.candidate_repository.summary(),
            "feedback": self.candidate_repository.feedback_summary(),
            "status": status,
            "limit": limit,
        }

    def approve_candidate(self, candidate_id: str, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload if isinstance(payload, dict) else {}
        candidate = self.candidate_repository.get(candidate_id)
        if not candidate:
            raise ValueError("검토 후보를 찾지 못했습니다.")
        starts_at = clean_text(payload.get("startsAt") or payload.get("starts_at") or candidate.starts_at, 80)
        if not starts_at:
            raise ValueError("날짜가 없는 후보는 startsAt을 지정해야 승인할 수 있습니다.")
        account_ids = payload.get("accountIds") if "accountIds" in payload else None
        event = self.calendar_service.save_event(candidate.to_calendar_payload(starts_at=starts_at, account_ids=account_ids))
        updated = self.candidate_repository.mark_status(
            candidate.candidate_id,
            CANDIDATE_STATUS_REGISTERED,
            clean_text(payload.get("reviewNote") or payload.get("review_note") or "approved", 1000),
        )
        return {"candidate": updated.to_dict() if updated else candidate.to_dict(), "event": event.get("event")}

    def reject_candidate(self, candidate_id: str, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload if isinstance(payload, dict) else {}
        candidate = self.candidate_repository.mark_status(
            candidate_id,
            CANDIDATE_STATUS_REJECTED,
            clean_text(payload.get("reviewNote") or payload.get("review_note") or "rejected", 1000),
        )
        if not candidate:
            raise ValueError("검토 후보를 찾지 못했습니다.")
        return {"candidate": candidate.to_dict()}
