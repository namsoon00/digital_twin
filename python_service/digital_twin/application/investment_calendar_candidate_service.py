from typing import Dict, List, Tuple

from ..domain.investment_calendar import clean_text
from ..domain.investment_calendar_candidates import (
    bounded_int,
    CANDIDATE_STATUS_REGISTERED,
    CANDIDATE_STATUS_PENDING,
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
        page_size = bounded_int(query.get("pageSize") or query.get("page_size") or query.get("limit"), 20, lower=1, upper=100)
        page = bounded_int(query.get("page"), 0, lower=0, upper=100000)
        if query.get("offset") not in (None, ""):
            offset = bounded_int(query.get("offset"), 0, lower=0, upper=1000000)
            page = offset // page_size
        else:
            offset = page * page_size
        if hasattr(self.candidate_repository, "list"):
            try:
                # Review candidates are operationally small.  Read a bounded
                # window first so invalid automatic detections and duplicates
                # cannot distort pagination or the count shown to users.
                candidates = self.candidate_repository.list(status=status, limit=500, offset=0)
            except TypeError:
                candidates = self.candidate_repository.list(status=status, limit=500)
        else:
            candidates = []
        visible, hidden = self._visible_candidates(candidates)
        total = len(visible)
        candidates = visible[offset:offset + page_size]
        page_count = max(1, (total + page_size - 1) // page_size)
        summary = dict(self.candidate_repository.summary() or {})
        stored_total = int(summary.get(status) or len(visible) + len(hidden))
        if status == CANDIDATE_STATUS_PENDING:
            summary["storedPending"] = stored_total
            summary["pending"] = total
        summary["hiddenAutomaticCandidates"] = len(hidden)
        return {
            "candidates": [candidate.to_dict() for candidate in candidates],
            "summary": summary,
            "feedback": self.candidate_repository.feedback_summary(),
            "status": status,
            "limit": page_size,
            "page": page,
            "pageSize": page_size,
            "offset": offset,
            "total": total,
            "storedTotal": stored_total,
            "hidden": hidden,
            "pageInfo": {
                "page": page,
                "pageSize": page_size,
                "offset": offset,
                "total": total,
                "pageCount": page_count,
                "hasPrev": page > 0,
                "hasNext": page + 1 < page_count,
            },
        }

    @staticmethod
    def _candidate_visibility(candidate: InvestmentCalendarReviewCandidate) -> Tuple[bool, str]:
        payload = candidate.payload if isinstance(candidate.payload, dict) else {}
        if not payload.get("autoDetected"):
            return True, ""
        if not str(candidate.starts_at or "").strip():
            return False, "날짜 없는 자동 후보"
        structured_type = str(payload.get("structuredEventType") or "").strip()
        official = bool(payload.get("officialSource"))
        if not structured_type and not official:
            return False, "비공식 키워드 자동 후보"
        return True, ""

    @staticmethod
    def _candidate_identity(candidate: InvestmentCalendarReviewCandidate) -> str:
        source = str(candidate.source_url or "").strip().lower()
        title = " ".join(str(candidate.title or "").lower().split())
        symbols = ",".join(sorted(str(symbol or "").upper() for symbol in candidate.symbols or []))
        return "|".join([source or title, symbols, str(candidate.starts_at or "")])

    def _visible_candidates(self, candidates: List[InvestmentCalendarReviewCandidate]):
        visible = []
        hidden = []
        identities = set()
        for candidate in candidates or []:
            allowed, reason = self._candidate_visibility(candidate)
            if not allowed:
                hidden.append({"candidateId": candidate.candidate_id, "reason": reason})
                continue
            identity = self._candidate_identity(candidate)
            if identity in identities:
                hidden.append({"candidateId": candidate.candidate_id, "reason": "중복 자동 후보"})
                continue
            identities.add(identity)
            visible.append(candidate)
        return visible, hidden

    def reconcile_pending_candidates(self, limit: int = 500) -> Dict[str, object]:
        """Archive invalid automatic candidates without touching user-created rows."""
        if not hasattr(self.candidate_repository, "list"):
            return {"reviewed": 0, "rejected": 0}
        candidates = self.candidate_repository.list(
            status=CANDIDATE_STATUS_PENDING,
            limit=bounded_int(limit, 500, lower=1, upper=1000),
            offset=0,
        )
        _, hidden = self._visible_candidates(candidates)
        rejected = 0
        for item in hidden:
            updated = self.candidate_repository.mark_status(
                item["candidateId"],
                CANDIDATE_STATUS_REJECTED,
                "자동 정리: " + item["reason"],
            )
            rejected += 1 if updated else 0
        return {"reviewed": len(candidates), "rejected": rejected, "hidden": hidden}

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
