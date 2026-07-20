from typing import Dict, Iterable, List

from ..domain.events import DomainEvent, RESEARCH_EVIDENCE_COLLECTED
from ..domain.investment_calendar_extraction import calendar_candidate_sets_from_research_items


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def item_identity(item: Dict[str, object]) -> str:
    if not isinstance(item, dict):
        return ""
    return "|".join(str(item.get(key) or "") for key in ["evidenceId", "url", "title", "symbol"])


def unique_items(items: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    result = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = item_identity(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


class InvestmentCalendarExtractionService:
    def __init__(
        self,
        calendar_service,
        account_repository=None,
        candidate_repository=None,
        settings: Dict[str, object] = None,
    ):
        self.calendar_service = calendar_service
        self.account_repository = account_repository
        self.candidate_repository = candidate_repository
        self.settings = dict(settings or {})

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarAutoExtractEnabled"), True)

    def register_undated(self) -> bool:
        return truthy(self.settings.get("investmentCalendarAutoExtractRegisterUndated"), False)

    def review_enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarAutoExtractReviewEnabled"), True)

    def feedback(self) -> Dict[str, object]:
        if not self.candidate_repository or not hasattr(self.candidate_repository, "feedback_summary"):
            return {}
        try:
            return self.candidate_repository.feedback_summary()
        except Exception:  # noqa: BLE001 - feedback must not break news collection.
            return {}

    def accounts(self) -> List[object]:
        if not self.account_repository:
            return []
        try:
            accounts = self.account_repository.load_all() if hasattr(self.account_repository, "load_all") else self.account_repository.load()
        except Exception:  # noqa: BLE001 - extraction should not break news collection if accounts cannot be read.
            accounts = []
        return [account for account in accounts or [] if getattr(account, "enabled", True)]

    def account_ids_for_candidate(self, candidate) -> List[str]:
        accounts = self.accounts()
        if not accounts:
            return []
        symbols = {str(symbol or "").upper() for symbol in getattr(candidate, "symbols", []) or [] if str(symbol or "").strip()}
        if symbols:
            selected = [
                account
                for account in accounts
                if symbols.intersection({str(symbol or "").upper() for symbol in getattr(account, "watchlist_symbols", []) or []})
            ]
            if selected:
                return [str(getattr(account, "account_id", "") or "") for account in selected if getattr(account, "account_id", "")]
        return [str(getattr(account, "account_id", "") or "") for account in accounts if getattr(account, "account_id", "")]

    def event_items(self, event: DomainEvent) -> List[Dict[str, object]]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        return unique_items(list(payload.get("materialChangedItems") or []) + list(payload.get("changedItems") or []))

    def handle(self, event: DomainEvent) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "candidateCount": 0, "savedCount": 0}
        if not isinstance(event, DomainEvent) or event.name != RESEARCH_EVIDENCE_COLLECTED:
            return {"status": "ignored", "candidateCount": 0, "savedCount": 0}
        candidate_sets = calendar_candidate_sets_from_research_items(
            self.event_items(event),
            register_undated=self.register_undated(),
            feedback=self.feedback(),
        )
        candidates = candidate_sets["ready"]
        review_candidates = candidate_sets["review"] if self.review_enabled() else []
        saved = 0
        stored_review = 0
        errors = []
        for candidate in candidates:
            payload = candidate.to_calendar_payload(self.account_ids_for_candidate(candidate))
            body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
            body.update({
                "sourceDomainEventId": event.event_id,
                "sourceDomainEventName": event.name,
                "sourceAggregateId": event.aggregate_id,
            })
            payload["payload"] = body
            try:
                self.calendar_service.save_event(payload)
                saved += 1
            except Exception as error:  # noqa: BLE001 - one bad candidate should not block the event bus.
                errors.append(str(error))
        if self.candidate_repository:
            for candidate in review_candidates:
                try:
                    payload = candidate.to_review_payload(self.account_ids_for_candidate(candidate))
                    body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                    body.update({
                        "sourceDomainEventId": event.event_id,
                        "sourceDomainEventName": event.name,
                        "sourceAggregateId": event.aggregate_id,
                    })
                    payload["payload"] = body
                    if self.candidate_repository.upsert(payload):
                        stored_review += 1
                except Exception as error:  # noqa: BLE001 - review storage should not block collection.
                    errors.append(str(error))
        return {
            "status": "ok",
            "candidateCount": len(candidates),
            "savedCount": saved,
            "reviewCandidateCount": len(review_candidates),
            "storedReviewCandidateCount": stored_review,
            "errors": errors[:5],
        }
