"""Reconcile source-bound company reports into the notification outbox."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Dict, Iterable, Mapping

from digital_twin.modules.news_intelligence.domain.company_change_report import company_report_notification_content, render_company_change_report
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
    reader = getattr(queue, "recent_for_symbol", None)
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
    """Prioritize uncovered companies, then rotate without a mutable cursor."""

    uncovered = [symbol for symbol in symbols if not states.get(symbol)]
    available = uncovered or [
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
    """Create a baseline once, then notify only on a material report change."""

    def __init__(
        self,
        *,
        account_repository,
        monitor_store,
        valuation_query_service,
        queue,
        settings: Mapping[str, object] = None,
        now_provider=None,
    ):
        self.account_repository = account_repository
        self.monitor_store = monitor_store
        self.valuation_query_service = valuation_query_service
        self.queue = queue
        self.settings = dict(settings or {})
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> Dict[str, object]:
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
                    report = payload.get("companyChangeReport") if isinstance(payload, Mapping) else {}
                    if not isinstance(report, Mapping) or not report.get("deliveryEligible"):
                        skipped += 1
                        continue
                    fingerprint = _text(report.get("materialFingerprint"))
                    if not fingerprint:
                        skipped += 1
                        continue
                    report = dict(report)
                    coverage_state = _text(
                        _mapping(_mapping(report.get("evidence")).get("coverage")).get("state")
                    ) or "preparing"
                    coverage_counts[coverage_state if coverage_state in coverage_counts else "preparing"] += 1
                    text = render_company_change_report(report)
                    content = company_report_notification_content(report)
                    readable = "\n".join([
                        _text(report.get("headline")), content["summary"],
                        *(line for section in content["sections"] for line in [section["title"], *section["rows"]]),
                    ])
                    job = NotificationJob.create(
                        text,
                        account_id=account_id,
                        account_label=_text(getattr(account, "label", "")),
                        message_type=INFORMATION_UPDATE,
                        source_event_id=_text(report.get("reportId")),
                        source_event_name="company_change_report.reconciled",
                        dedupe_key="company-change-report:" + account_id + ":" + symbol + ":" + fingerprint[:32],
                        context={
                            "companyChangeReport": report,
                            "messageType": INFORMATION_UPDATE,
                            "notificationSubject": "기업 변화 보고서",
                            "displayTarget": _text(report.get("name") or symbol),
                            "target": _text(report.get("name") or symbol),
                            "symbol": symbol,
                            "rawSymbol": symbol,
                            "market": _text(report.get("market")),
                            "referenceDate": _text(report.get("sourceCutoffDisplay") or report.get("sourceCutoffAt")),
                            "body": text,
                            "telegramMessage": text,
                            "readableMessage": readable,
                            "dataQuality": "actual",
                            "isMock": False,
                            "notificationContent": content,
                        },
                    )
                    queued += 1 if self.queue.enqueue(job) else 0
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


__all__ = ["CompanyChangeReportReconciler"]
