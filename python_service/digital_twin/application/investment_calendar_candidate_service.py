from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from ..domain.investment_calendar import clean_text, has_explicit_event_time, parse_utc
from ..domain.investment_calendar_candidates import (
    bounded_int,
    CANDIDATE_STATUS_REGISTERED,
    CANDIDATE_STATUS_EXPIRED,
    CANDIDATE_STATUS_PENDING,
    CANDIDATE_STATUS_REJECTED,
    CANDIDATE_STATUS_SUPERSEDED,
    InvestmentCalendarReviewCandidate,
)
from ..domain.security_lines import security_lines_for_symbol


class InvestmentCalendarCandidateService:
    def __init__(self, candidate_repository, calendar_service, settings: Dict[str, object] = None, now=None):
        self.candidate_repository = candidate_repository
        self.calendar_service = calendar_service
        self.settings = dict(settings or {})
        self.now = now or (lambda: datetime.now(timezone.utc))

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
    def _structured_schedule(candidate: InvestmentCalendarReviewCandidate) -> bool:
        payload = candidate.payload if isinstance(candidate.payload, dict) else {}
        if not str(candidate.starts_at or "").strip():
            return False
        detector = str(payload.get("detector") or "").strip()
        parser = str(payload.get("sourceParser") or "").strip()
        date_source = str(payload.get("dateSource") or "").strip()
        structured_type = str(payload.get("structuredEventType") or "").strip()
        source_names = {
            str(candidate.source or "").strip().casefold(),
            str(payload.get("originalSource") or "").strip().casefold(),
        }
        return bool(
            date_source.startswith("calendar.")
            or detector == "structured-calendar-source-v1"
            or "yfinance" in source_names
            or (
                structured_type
                and (
                    parser in {"sec-edgar", "dart", "krx-kind", "exchange", "issuer-ir"}
                    or detector in {"calendar-scheduled-research-v1", "ai-research-calendar-recommender-v1"}
                )
            )
        )

    @staticmethod
    def _candidate_visibility(candidate: InvestmentCalendarReviewCandidate) -> Tuple[bool, str]:
        payload = candidate.payload if isinstance(candidate.payload, dict) else {}
        if not payload.get("autoDetected"):
            return True, ""
        if not str(candidate.starts_at or "").strip():
            return False, "날짜 없는 자동 후보"
        if not InvestmentCalendarCandidateService._structured_schedule(candidate):
            return False, "구조화 일정이 아닌 자동 후보"
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

    def reconcile_all_candidates(self, limit: int = 500) -> Dict[str, object]:
        """Repair legacy approvals using the current schedule-confirmation contract."""
        if not hasattr(self.candidate_repository, "list"):
            return {"reviewed": 0, "reopened": 0, "rejected": 0}
        rows = []
        for status in (CANDIDATE_STATUS_PENDING, CANDIDATE_STATUS_REGISTERED):
            try:
                rows.extend(self.candidate_repository.list(status=status, limit=limit, offset=0))
            except TypeError:
                rows.extend(self.candidate_repository.list(status=status, limit=limit))
        reopened = 0
        rejected = 0
        expired = 0
        unchanged = 0
        observed_now = self.now()
        if observed_now.tzinfo is None:
            observed_now = observed_now.replace(tzinfo=timezone.utc)
        expires_before = observed_now.astimezone(timezone.utc) - timedelta(days=2)
        for candidate in rows:
            if not (candidate.payload or {}).get("autoDetected"):
                unchanged += 1
                continue
            candidate_at = parse_utc(candidate.starts_at)
            if candidate_at and candidate_at < expires_before:
                updated = self.candidate_repository.mark_status(
                    candidate.candidate_id,
                    CANDIDATE_STATUS_EXPIRED,
                    "자동 정리: 예정일이 지나 후보 검토 대상에서 제외했습니다.",
                )
                expired += 1 if updated else 0
                continue
            if not self._structured_schedule(candidate):
                updated = self.candidate_repository.mark_status(
                    candidate.candidate_id,
                    CANDIDATE_STATUS_REJECTED,
                    "자동 정리: 구조화된 일정 근거가 없습니다.",
                )
                rejected += 1 if updated else 0
                continue
            event = None
            repository = getattr(self.calendar_service, "repository", None)
            if repository and hasattr(repository, "get"):
                event = repository.get(candidate.proposed_event_id)
            event_payload = getattr(event, "payload", {}) if event else {}
            confirmed = bool(
                event
                and not bool(event_payload.get("reviewRequired"))
                and str(event_payload.get("scheduleState") or "") in {"confirmed", "dateConfirmed"}
            )
            if candidate.status == CANDIDATE_STATUS_REGISTERED and not confirmed:
                updated = self.candidate_repository.mark_status(
                    candidate.candidate_id,
                    CANDIDATE_STATUS_PENDING,
                    "자동 정리: 발표 날짜와 시각을 다시 확인해야 합니다.",
                )
                reopened += 1 if updated else 0
            else:
                unchanged += 1
        return {
            "reviewed": len(rows),
            "reopened": reopened,
            "rejected": rejected,
            "expired": expired,
            "unchanged": unchanged,
        }

    def related_symbols(self, symbols) -> set:
        related = {str(symbol or "").upper() for symbol in symbols or [] if str(symbol or "").strip()}
        for symbol in list(related):
            for line in security_lines_for_symbol(symbol, self.settings):
                related.update({
                    str(line.local_symbol or "").upper(),
                    str(line.symbol or "").upper(),
                    str(line.underlying_symbol or "").upper(),
                })
        return {symbol for symbol in related if symbol}

    def reconcile_official_event(self, official_event, window_days: int = 14) -> Dict[str, object]:
        if not official_event or not getattr(official_event, "symbols", None):
            return {"superseded": 0, "candidateIds": []}
        official_at = parse_utc(getattr(official_event, "starts_at", ""))
        if not official_at:
            return {"superseded": 0, "candidateIds": []}
        symbols = self.related_symbols(official_event.symbols)
        rows = []
        for status in (CANDIDATE_STATUS_PENDING, CANDIDATE_STATUS_REGISTERED):
            try:
                rows.extend(self.candidate_repository.list(status=status, limit=500, offset=0))
            except TypeError:
                rows.extend(self.candidate_repository.list(status=status, limit=500))
        candidate_ids = []
        for candidate in rows:
            candidate_at = parse_utc(candidate.starts_at)
            if (
                candidate.event_type != official_event.event_type
                or not symbols.intersection(self.related_symbols(candidate.symbols))
                or not candidate_at
                or abs((candidate_at - official_at).total_seconds()) > max(1, int(window_days)) * 86400
            ):
                continue
            updated = self.candidate_repository.mark_status(
                candidate.candidate_id,
                CANDIDATE_STATUS_SUPERSEDED,
                "공식 일정으로 대체: " + str(official_event.event_id or ""),
            )
            if updated:
                candidate_ids.append(candidate.candidate_id)
        return {"superseded": len(candidate_ids), "candidateIds": candidate_ids}

    def approve_candidate(self, candidate_id: str, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload if isinstance(payload, dict) else {}
        candidate = self.candidate_repository.get(candidate_id)
        if not candidate:
            raise ValueError("검토 후보를 찾지 못했습니다.")
        starts_at = clean_text(payload.get("startsAt") or payload.get("starts_at") or candidate.starts_at, 80)
        if not starts_at:
            raise ValueError("날짜가 없는 후보는 startsAt을 지정해야 승인할 수 있습니다.")
        account_ids = payload.get("accountIds") if "accountIds" in payload else None
        timezone_name = clean_text(payload.get("timezone") or candidate.timezone or "Asia/Seoul", 80)
        event_payload = candidate.to_calendar_payload(
            starts_at=starts_at,
            account_ids=account_ids,
            timezone_name=timezone_name,
        )
        candidate_payload = candidate.payload if isinstance(candidate.payload, dict) else {}
        if candidate_payload.get("autoDetected"):
            self.apply_schedule_confirmation(event_payload, candidate, payload)
            refreshed_candidate = candidate.to_dict()
            refreshed_candidate.update({
                "startsAt": event_payload.get("startsAt"),
                "timezone": event_payload.get("timezone"),
                "allDay": False,
                "source": event_payload.get("source"),
                "sourceUrl": event_payload.get("sourceUrl"),
                "payload": dict(event_payload.get("payload") or {}),
            })
            self.candidate_repository.upsert(refreshed_candidate)
        event = self.calendar_service.save_event(event_payload)
        updated = self.candidate_repository.mark_status(
            candidate.candidate_id,
            CANDIDATE_STATUS_REGISTERED,
            clean_text(payload.get("reviewNote") or payload.get("review_note") or "approved", 1000),
        )
        return {"candidate": updated.to_dict() if updated else candidate.to_dict(), "event": event.get("event")}

    @staticmethod
    def apply_schedule_confirmation(event_payload: Dict[str, object], candidate, approval: Dict[str, object]) -> None:
        """Promote automatic candidates after the user confirms an explicit schedule."""
        source_url = clean_text(
            approval.get("officialSourceUrl")
            or approval.get("official_source_url")
            or "",
            1000,
        )
        if source_url:
            parsed = urlparse(source_url)
            if not (parsed.scheme in {"http", "https"} and parsed.netloc):
                raise ValueError("확인 URL을 입력하려면 http 또는 https 주소를 사용해야 합니다.")
        if not has_explicit_event_time(event_payload.get("startsAt")):
            raise ValueError("자동 감지 일정은 발표 날짜와 시각을 YYYY-MM-DDTHH:MM 형식으로 확인해야 합니다.")
        original = candidate.payload if isinstance(candidate.payload, dict) else {}
        body = event_payload.get("payload") if isinstance(event_payload.get("payload"), dict) else {}
        official_source = bool(original.get("officialSource"))
        body.update({
            "officialSource": official_source,
            "officialSourceUrl": source_url if official_source else "",
            "officialVerification": "source-provided" if official_source else "not-verified",
            "scheduleVerification": "user-confirmed",
            "scheduleVerifiedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "originalSource": candidate.source,
            "originalSourceUrl": candidate.source_url,
            "originalScheduleState": original.get("scheduleState") or "estimated",
            "scheduleState": "confirmed",
            "timeState": "userConfirmed",
            "timeSource": "calendar-candidate-review",
            "reviewRequired": False,
            "reviewReason": "",
            "reminderEnabled": True,
        })
        event_payload["payload"] = body
        event_payload["allDay"] = False
        if official_source:
            event_payload["source"] = candidate.source
            event_payload["sourceUrl"] = source_url or candidate.source_url
        else:
            event_payload["source"] = "사용자 확인 일정"
            event_payload["sourceUrl"] = source_url
        existing_notes = clean_text(event_payload.get("notes"), 1800)
        event_payload["notes"] = (existing_notes + " 발표 날짜와 시각을 사용자 확인으로 활성화했습니다.").strip()

    def reject_candidate(self, candidate_id: str, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload if isinstance(payload, dict) else {}
        candidate = self.candidate_repository.mark_status(
            candidate_id,
            CANDIDATE_STATUS_REJECTED,
            clean_text(payload.get("reviewNote") or payload.get("review_note") or "rejected", 1000),
        )
        if not candidate:
            raise ValueError("검토 후보를 찾지 못했습니다.")
        removed_tentative_event = False
        repository = getattr(self.calendar_service, "repository", None)
        if repository and hasattr(repository, "get"):
            event = repository.get(candidate.proposed_event_id)
            event_payload = getattr(event, "payload", {}) if event else {}
            if (
                event
                and getattr(event, "status", "") == "tentative"
                and isinstance(event_payload, dict)
                and event_payload.get("reviewCandidateId") == candidate.candidate_id
            ):
                removed_tentative_event = bool(self.calendar_service.delete_event(candidate.proposed_event_id).get("removed"))
        return {"candidate": candidate.to_dict(), "removedTentativeEvent": removed_tentative_event}
