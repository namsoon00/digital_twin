from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from ..domain.investment_calendar import clean_text, parse_utc
from ..domain.investment_calendar_candidates import CANDIDATE_FINAL_STATUSES
from ..domain.investment_calendar_extraction import calendar_candidate_sets_from_research_items
from ..domain.investment_research import NewsCollectionTarget
from ..domain.investment_strategy_guidance import event_type_guidance, target_text
from ..domain.market_data import number
from ..domain.portfolio import utc_now_iso


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
DISCOVERY_DETECTOR = "calendar-scheduled-research-v1"


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 1, upper: int = 500) -> int:
    raw = (settings or {}).get(key)
    parsed = number(raw)
    if parsed == 0 and raw in (None, ""):
        parsed = fallback
    return max(lower, min(upper, int(parsed or fallback)))


def evidence_to_dict(item) -> Dict[str, object]:
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "to_dict"):
        return item.to_dict()
    return {}


class InvestmentCalendarDiscoveryService:
    """Periodically discover dated, review-first investment calendar events.

    The service intentionally separates discovery from activation. Structured
    calendar dates from financial providers create visible tentative events and
    review candidates; only official/approved events become active reminders.
    """

    def __init__(
        self,
        calendar_service,
        candidate_repository,
        evidence_repository=None,
        account_repository=None,
        research_gateway=None,
        settings: Dict[str, object] = None,
        now=None,
    ):
        self.calendar_service = calendar_service
        self.candidate_repository = candidate_repository
        self.evidence_repository = evidence_repository
        self.account_repository = account_repository
        self.research_gateway = research_gateway
        self.settings = dict(settings or {})
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_run_at = None
        self.last_result: Dict[str, object] = {}

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarDiscoveryEnabled"), True)

    def interval_seconds(self) -> int:
        hours = int_setting(self.settings, "investmentCalendarDiscoveryIntervalHours", 12, 1, 168)
        return hours * 3600

    def max_symbols(self, payload: Dict[str, object] = None) -> int:
        value = dict(self.settings)
        if isinstance(payload, dict) and payload.get("limit") not in (None, ""):
            value["investmentCalendarDiscoveryMaxSymbols"] = payload.get("limit")
        return int_setting(value, "investmentCalendarDiscoveryMaxSymbols", 12, 1, 50)

    def horizon_days(self) -> int:
        return int_setting(self.settings, "investmentCalendarDiscoveryHorizonDays", 180, 14, 730)

    def due(self) -> bool:
        if not self.enabled() or not self.research_gateway:
            return False
        if not self.last_run_at:
            return True
        return self.now() - self.last_run_at >= timedelta(seconds=self.interval_seconds())

    def accounts(self) -> List[object]:
        if not self.account_repository:
            return []
        try:
            rows = self.account_repository.load_all() if hasattr(self.account_repository, "load_all") else self.account_repository.load()
        except Exception:  # noqa: BLE001 - discovery can fall back to configured symbols.
            rows = []
        return [account for account in rows or [] if getattr(account, "enabled", True)]

    def targets(self, payload: Dict[str, object] = None) -> List[NewsCollectionTarget]:
        payload = payload if isinstance(payload, dict) else {}
        requested = clean_text(payload.get("symbol"), 24).upper()
        symbols = []
        if requested:
            symbols.append(requested)
        else:
            for account in self.accounts():
                symbols.extend(getattr(account, "watchlist_symbols", []) or [])
            symbols.extend(str(self.settings.get("watchlistSymbols") or "").replace("\n", ",").split(","))
        targets = []
        seen = set()
        for raw in symbols:
            symbol = clean_text(raw, 24).upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            market = "KOSPI" if symbol.isdigit() else ""
            currency = "KRW" if symbol.isdigit() else "USD"
            targets.append(NewsCollectionTarget(symbol=symbol, name=symbol, market=market, currency=currency))
            if len(targets) >= self.max_symbols(payload):
                break
        return targets

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

    def feedback(self) -> Dict[str, object]:
        if not self.candidate_repository or not hasattr(self.candidate_repository, "feedback_summary"):
            return {}
        try:
            return self.candidate_repository.feedback_summary()
        except Exception:  # noqa: BLE001 - review feedback must not block data refresh.
            return {}

    def source_types(self) -> List[str]:
        return ["official", "official-filing", "financial-data"]

    def collect(self, targets: Iterable[NewsCollectionTarget]) -> tuple:
        items = []
        statuses = []
        errors = []
        for target in targets or []:
            try:
                rows, source_statuses = self.research_gateway.collect_for_target(target, source_types=self.source_types())
            except Exception as error:  # noqa: BLE001 - one symbol cannot stop calendar discovery.
                errors.append(str(getattr(target, "symbol", "")) + ": " + str(error)[:180])
                continue
            items.extend(item for item in rows or [] if item)
            for source_status in source_statuses or []:
                if not isinstance(source_status, dict):
                    continue
                status = dict(source_status)
                statuses.append(status)
                if status.get("ok") is False:
                    source = clean_text(status.get("source"), 80) or str(getattr(target, "symbol", ""))
                    message = clean_text(status.get("message") or status.get("status") or "source request failed", 180)
                    errors.append(source + ": " + message)
        return items, statuses, errors

    def persist_evidence(self, items: Iterable[object]) -> int:
        if not self.evidence_repository or not hasattr(self.evidence_repository, "upsert_many"):
            return 0
        try:
            return int(self.evidence_repository.upsert_many(items) or 0)
        except Exception:  # noqa: BLE001 - calendar entries remain independently usable.
            return 0

    def existing_event(self, event_id: str):
        repository = getattr(self.calendar_service, "repository", None)
        if not repository or not hasattr(repository, "get"):
            return None
        try:
            return repository.get(event_id)
        except Exception:  # noqa: BLE001 - a missing lookup must not stop discovery.
            return None

    def candidate_payload(self, candidate, account_ids: Iterable[str]) -> Dict[str, object]:
        payload = candidate.to_review_payload(account_ids)
        guidance = event_type_guidance(candidate.event_type, target_text(candidate.symbols, candidate.markets), candidate.symbols)
        body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        body.update({
            "calendarDiscovery": True,
            "detector": DISCOVERY_DETECTOR,
            "recommendationMode": "tentative-calendar",
            "discoveredAt": utc_now_iso(),
            "investmentImpact": guidance["impact"],
            "watchItems": list(guidance["watchItems"]),
        })
        payload["payload"] = body
        return payload

    def tentative_event_payload(self, candidate, account_ids: Iterable[str]) -> Dict[str, object]:
        payload = candidate.to_calendar_payload(account_ids)
        payload["status"] = "tentative"
        payload["reminderOffsetsMinutes"] = []
        body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        body.update({
            "calendarDiscovery": True,
            "detector": DISCOVERY_DETECTOR,
            "reminderEnabled": False,
            "scheduleState": body.get("scheduleState") or "estimated",
            "reviewCandidateId": candidate.review_candidate_id,
        })
        payload["payload"] = body
        return payload

    def save_discovered_candidates(self, items: Iterable[object]) -> Dict[str, object]:
        rows = [evidence_to_dict(item) for item in items or []]
        sets = calendar_candidate_sets_from_research_items(rows, feedback=self.feedback())
        active_count = 0
        tentative_count = 0
        review_count = 0
        stored_review_count = 0
        skipped_final_count = 0
        outside_horizon_count = 0
        errors = []
        now_at = self.now()
        latest_at = now_at + timedelta(days=self.horizon_days())
        earliest_at = now_at - timedelta(days=2)
        candidates = list(sets.get("ready") or []) + list(sets.get("review") or [])
        ready_ids = {candidate.event_id for candidate in sets.get("ready") or []}
        for candidate in candidates:
            starts_at = parse_utc(candidate.starts_at)
            if not starts_at or starts_at < earliest_at or starts_at > latest_at:
                outside_horizon_count += 1
                continue
            if candidate.event_id in ready_ids:
                try:
                    self.calendar_service.save_event(candidate.to_calendar_payload(self.account_ids_for_candidate(candidate)))
                    active_count += 1
                except Exception as error:  # noqa: BLE001 - continue with the remaining dates.
                    errors.append(candidate.event_id + ": " + str(error)[:180])
                continue
            review_count += 1
            account_ids = self.account_ids_for_candidate(candidate)
            existing_candidate = self.candidate_repository.get(candidate.review_candidate_id) if self.candidate_repository and hasattr(self.candidate_repository, "get") else None
            if existing_candidate and getattr(existing_candidate, "status", "") in CANDIDATE_FINAL_STATUSES:
                skipped_final_count += 1
                continue
            existing_event = self.existing_event(candidate.event_id)
            if not existing_event or str(getattr(existing_event, "status", "")) != "active":
                try:
                    self.calendar_service.save_event(self.tentative_event_payload(candidate, account_ids))
                    tentative_count += 1
                except Exception as error:  # noqa: BLE001 - a review candidate remains available.
                    errors.append(candidate.event_id + ": " + str(error)[:180])
            if self.candidate_repository:
                try:
                    if self.candidate_repository.upsert(self.candidate_payload(candidate, account_ids)):
                        stored_review_count += 1
                except Exception as error:  # noqa: BLE001 - retain other candidates.
                    errors.append(candidate.review_candidate_id + ": " + str(error)[:180])
        return {
            "activeCount": active_count,
            "tentativeCount": tentative_count,
            "reviewCandidateCount": review_count,
            "storedReviewCandidateCount": stored_review_count,
            "skippedFinalCandidateCount": skipped_final_count,
            "outsideHorizonCount": outside_horizon_count,
            "errors": errors[:10],
        }

    def run_due(self) -> Dict[str, object]:
        if not self.due():
            return {"status": "not-due", "targetCount": 0, "evidenceCount": 0, "tentativeCount": 0}
        return self.run_once()

    def run_once(self, payload: Dict[str, object] = None, force: bool = False) -> Dict[str, object]:
        payload = payload if isinstance(payload, dict) else {}
        force = bool(force or payload.get("force"))
        if not self.enabled() and not force:
            return {"status": "disabled", "targetCount": 0, "evidenceCount": 0, "tentativeCount": 0}
        if not self.research_gateway:
            return {"status": "unavailable", "targetCount": 0, "evidenceCount": 0, "tentativeCount": 0}
        targets = self.targets(payload)
        if not targets:
            result = {"status": "noTargets", "targetCount": 0, "evidenceCount": 0, "tentativeCount": 0, "sources": []}
            self.last_run_at = self.now()
            self.last_result = result
            return result
        items, statuses, collect_errors = self.collect(targets)
        result = self.save_discovered_candidates(items)
        result.update({
            "status": "partial" if collect_errors or result.get("errors") else "ok",
            "generatedAt": utc_now_iso(),
            "detector": DISCOVERY_DETECTOR,
            "targetCount": len(targets),
            "symbols": [target.normalized_symbol() for target in targets],
            "evidenceCount": len(items),
            "savedEvidenceCount": self.persist_evidence(items),
            "sources": statuses[:80],
            "errors": (collect_errors + list(result.get("errors") or []))[:10],
        })
        self.last_run_at = self.now()
        self.last_result = dict(result)
        return result

    def status(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled(),
            "due": self.due(),
            "intervalSeconds": self.interval_seconds(),
            "lastRunAt": self.last_run_at.isoformat().replace("+00:00", "Z") if self.last_run_at else "",
            "lastResult": dict(self.last_result or {}),
        }
