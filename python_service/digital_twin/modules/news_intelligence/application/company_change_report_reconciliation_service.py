"""Reconcile source-bound company reports into the notification outbox."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import hashlib
from typing import Dict, Iterable, Mapping

from digital_twin.modules.news_intelligence.domain.company_change_report import build_company_change_report, company_report_notification_content, render_company_change_report
from digital_twin.modules.news_intelligence.domain.company_report_delivery import DELIVERY_VERSION, has_delivery_reference
from digital_twin.modules.notifications.contracts import INFORMATION_UPDATE, NotificationJob
from digital_twin.modules.portfolio.contracts import InstrumentValuationQuery


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _mapping(value: object) -> Dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _enabled(value: object, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _state_symbols(state: Mapping[str, object], configured: Iterable[str] = None) -> list[str]:
    symbols = []
    for key in ("positions", "watchlist"):
        raw = state.get(key)
        rows = raw.values() if isinstance(raw, Mapping) else raw if isinstance(raw, list) else []
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            symbol = _text(item.get("symbol")).upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    for raw in configured or []:
        symbol = _text(raw).upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _report_job_state(queue, account_id: str, symbol: str) -> Dict[str, object]:
    reader = getattr(queue, "recent_company_reports", None) or getattr(queue, "recent_for_symbol", None)
    if not callable(reader):
        return {}
    try:
        jobs = reader(symbol, account_id=account_id, limit=20)
    except (OSError, RuntimeError, TypeError, ValueError):
        return {}
    for job in jobs or []:
        context = getattr(job, "context", None)
        context = context if isinstance(context, Mapping) else {}
        report = context.get("companyChangeReport")
        if not isinstance(report, Mapping) or _text(report.get("symbol")).upper() != symbol:
            continue
        return {
            "status": _text(getattr(job, "status", "")).lower(),
            "updatedAt": _text(getattr(job, "updated_at", "") or getattr(job, "created_at", "")),
        }
    return {}


def _rotating_symbols(symbols, states, batch_size: int, now: datetime, rotation_seconds: int) -> list[str]:
    """Rotate quiet references fairly, including subjects with no outbox history."""

    available = [
        symbol for symbol in symbols
        if states.get(symbol, {}).get("status") not in {"pending", "processing", "awaiting_ai"}
    ]
    if not available:
        return []
    batch_count = max(1, math.ceil(len(available) / batch_size))
    slot = int(now.timestamp() // rotation_seconds) % batch_count
    start = slot * batch_size
    return available[start:start + batch_size]


class CompanyChangeReportReconciler:
    """Save a quiet reference, then coalesce comparable financial changes."""

    def __init__(
        self,
        *,
        account_repository,
        monitor_store,
        valuation_query_service,
        queue,
        settings: Mapping[str, object] = None,
        now_provider=None,
        state_store=None,
    ):
        self.account_repository = account_repository
        self.monitor_store = monitor_store
        self.valuation_query_service = valuation_query_service
        self.queue = queue
        self.state_store = state_store
        self.settings = dict(settings or {})
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> Dict[str, object]:
        if self.state_store is None:
            return {"status": "state-store-unavailable", "checked": 0, "queued": 0}
        if not _enabled(self.settings.get("companyChangeReportEnabled"), True):
            return {"status": "disabled", "checked": 0, "queued": 0}
        batch_size = max(1, min(20, int(
            self.settings.get("companyChangeReportBatchSize")
            or self.settings.get("companyChangeReportMaxSymbols")
            or 2
        )))
        rotation_seconds = max(30, min(24 * 60 * 60, int(
            self.settings.get("companyChangeReportRotationSeconds") or 60
        )))
        preferred = [
            item.strip().upper()
            for item in str(self.settings.get("companyChangeReportSymbols") or "").split(",")
            if item.strip()
        ]
        previous = getattr(self.monitor_store, "previous", {})
        previous = previous if isinstance(previous, Mapping) else {}
        accounts = list(self.account_repository.load() or [])
        checked = 0
        queued = 0
        skipped = 0
        errors = []
        universe_count = 0
        selected_symbols = []
        coverage_counts = {"sufficient": 0, "partial": 0, "preparing": 0}
        without_report = 0
        for account in accounts:
            if not bool(getattr(account, "enabled", True)):
                continue
            account_id = _text(getattr(account, "account_id", ""))
            state = previous.get(account_id)
            if not isinstance(state, Mapping):
                continue
            available = _state_symbols(state, getattr(account, "watchlist_symbols", []))
            symbols = [symbol for symbol in preferred if symbol in available] if preferred else available
            histories = {symbol: _report_job_state(self.queue, account_id, symbol) for symbol in symbols}
            universe_count += len(symbols)
            without_report += sum(not bool(histories[symbol]) for symbol in symbols)
            selected = _rotating_symbols(
                symbols, histories, batch_size, self.now_provider(), rotation_seconds,
            )
            selected_symbols.extend(selected)
            for symbol in selected:
                checked += 1
                try:
                    payload = self.valuation_query_service.query(
                        InstrumentValuationQuery(symbol=symbol, account_id=account_id)
                    )
                    coverage = _text(_mapping(_mapping(payload.get("companyReportEvidence")).get("coverage")).get("state")) or "preparing"
                    coverage_counts[coverage if coverage in coverage_counts else "preparing"] += 1
                    with self.state_store.subject(account_id, symbol) as state:
                        if state is None:
                            skipped += 1
                            continue
                        outcome = self._reconcile_subject(account, symbol, payload, state)
                    queued += int(outcome == "queued")
                    skipped += int(outcome != "queued")
                except Exception as error:  # noqa: BLE001 - one symbol must not block other reports.
                    errors.append({"accountId": account_id, "symbol": symbol, "reason": str(error)[:180]})
        return {
            "status": "ok" if not errors else "partial",
            "checked": checked,
            "queued": queued,
            "skipped": skipped,
            "universeCount": universe_count,
            "batchSize": batch_size,
            "selectedSymbols": selected_symbols,
            "withoutReportBeforeRun": without_report,
            "coverageCounts": coverage_counts,
            "errors": errors[:10],
        }

    def _reconcile_subject(self, account, symbol, payload, state_store):
        now = self.now_provider()
        state = state_store.load()
        reader = getattr(self.queue, "recent_company_reports", None) or getattr(self.queue, "recent_for_symbol", None)
        history = reader(symbol, account_id=account.account_id, limit=40) if callable(reader) else []
        jobs = [job for job in history if isinstance(getattr(job, "context", {}).get("companyChangeReport"), Mapping)]
        # Include the exact queued job even if general history has rotated away.
        if state.get("queuedJobId") and callable(getattr(self.queue, "get", None)):
            job = self.queue.get(state["queuedJobId"])
            if job:
                jobs.insert(0, job)
        for job in jobs:
            if job.status in {"pending", "processing", "awaiting_ai"}:
                if job.context.get("companyReportDelivery", {}).get("version") != DELIVERY_VERSION:
                    self.queue.mark_suppressed(job, "기업 보고서 발송 정책 변경: 이전 대기 보고서는 화면에서 확인합니다.")
                    continue
                return "pending"
        completed = max((job for job in jobs if job.status == "done"
                         and job.context.get("companyReportDelivery", {}).get("version") == DELIVERY_VERSION
                         and (job.updated_at or job.created_at) > state.get("lastSentAt", "")),
                        key=lambda job: job.updated_at or job.created_at, default=None)
        if completed and state.get("version") == DELIVERY_VERSION:
            state.update(baseline=completed.context["companyChangeReport"], lastSentAt=completed.updated_at or completed.created_at,
                         baselineJobId=completed.job_id, lastFingerprint=completed.context["companyReportDelivery"]["fingerprint"], pendingSince="", queuedJobId="")
            state_store.replace(state)
        if state.get("version") != DELIVERY_VERSION or not state.get("baseline"):
            delivered = [job for job in jobs if job.status == "done"]
            last_sent = max((job.updated_at or job.created_at for job in delivered), default="")
            state_store.replace({"version": DELIVERY_VERSION, "baseline": build_company_change_report(payload),
                                 "lastSentAt": last_sent, "pendingSince": ""})
            return "baseline-saved"
        if not has_delivery_reference(state["baseline"].get("evidence")):
            # First available financial data initializes an empty reference
            # quietly, so later changes can be compared after a cold start.
            if has_delivery_reference(payload.get("companyReportEvidence")):
                state["baseline"] = build_company_change_report(payload)
                state_store.replace(state)
            return "reference-only"
        report = build_company_change_report(payload, state["baseline"])
        policy = report["deliveryPolicy"]
        if not policy["eligible"]:
            if report["reportKind"] == "expanded":
                state.update(baseline=report, pendingSince="")
                state_store.replace(state)
            elif state.get("pendingSince"):
                state["pendingSince"] = ""
                state_store.replace(state)
            return "reference-only"
        if not state.get("pendingSince"):
            state["pendingSince"] = now.isoformat()
            state_store.replace(state)
        coalesce = max(1, int(self.settings.get("companyChangeReportCoalesceMinutes") or 30))
        cooldown = max(1, int(self.settings.get("companyChangeReportCooldownHours") or 24))
        def clock(value):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        ready_at = clock(state["pendingSince"]) + timedelta(minutes=coalesce)
        if state.get("lastSentAt"):
            ready_at = max(ready_at, clock(state["lastSentAt"]) + timedelta(hours=cooldown))
        if now < ready_at:
            return "coalescing"
        content = company_report_notification_content(report)
        fingerprint = policy["fingerprint"]
        # Include the baseline so a later A -> B -> A transition is not lost.
        dedupe = fingerprint + ":" + str(state.get("baselineJobId") or state["baseline"].get("reportId", ""))
        job = NotificationJob.create(
            render_company_change_report(report), account_id=account.account_id,
            account_label=_text(getattr(account, "label", "")), message_type=INFORMATION_UPDATE,
            source_event_id=report["reportId"], source_event_name="company_change_report.reconciled",
            dedupe_key="company-report-v1:" + account.account_id + ":" + symbol + ":" + hashlib.sha256(dedupe.encode()).hexdigest()[:32],
            context={"companyChangeReport": report, "companyReportDelivery": {**policy, "readyAt": ready_at.isoformat()},
                     "messageType": INFORMATION_UPDATE, "notificationSubject": "기업 변화 보고서",
                     "displayTarget": report["name"], "target": report["name"], "symbol": symbol, "rawSymbol": symbol,
                     "market": report["market"], "referenceDate": report["sourceCutoffDisplay"],
                     "notificationContent": content, "dataQuality": "actual", "isMock": False})
        if self.queue.enqueue(job):
            state["queuedJobId"] = job.job_id
            state_store.replace(state)
            return "queued"
        return "not-admitted"
