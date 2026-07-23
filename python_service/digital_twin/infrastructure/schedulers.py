import signal
import time

from .operational_error_reporting import operational_error_reporter, report_runtime_error


MIN_REALTIME_INTERVAL_SECONDS = 3 * 60


def install_stop_handlers(stop_callback) -> None:
    signal.signal(signal.SIGTERM, stop_callback)
    signal.signal(signal.SIGINT, stop_callback)


def wait_until_running(running, end_at: float, sleep_fn=time.sleep) -> None:
    while running() and time.monotonic() < end_at:
        sleep_fn(min(1.0, end_at - time.monotonic()))


class RealtimeScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(MIN_REALTIME_INTERVAL_SECONDS, int(interval_seconds or MIN_REALTIME_INTERVAL_SECONDS))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        print("Python realtime monitor started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                self.runner.run_once()
            except Exception as error:  # noqa: BLE001 - long-running scheduler must continue after a cycle failure.
                print("Python realtime monitor error: " + str(error))
                report_runtime_error(self.error_reporter, "Python realtime monitor", error, "monitor cycle")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class ModelReviewScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(60, int(interval_seconds or 300))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self, limit: int = 1) -> None:
        install_stop_handlers(self.stop)
        print("Python model review worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                self.runner.run_once(limit=limit)
            except Exception as error:  # noqa: BLE001 - worker must continue after a cycle failure.
                print("Python model review worker error: " + str(error))
                report_runtime_error(self.error_reporter, "Python model review worker", error, "review cycle")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class NotificationQueueScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(5, int(interval_seconds or 30))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self, limit: int = 10) -> None:
        install_stop_handlers(self.stop)
        print("Python notification worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                processed = self.runner.run_once(limit=limit)
                if processed:
                    details = list(getattr(self.runner, "last_run_details", []) or [])
                    suffix = (" · " + "; ".join(details[:8])) if details else ""
                    if len(details) > 8:
                        suffix += "; +" + str(len(details) - 8) + " more"
                    print("Processed notification jobs: " + str(processed) + suffix)
            except Exception as error:  # noqa: BLE001 - worker must continue after a cycle failure.
                print("Python notification worker error: " + str(error))
                report_runtime_error(self.error_reporter, "Python notification worker", error, "notification delivery")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class OntologyReasoningScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(5, int(interval_seconds or 10))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.last_deferred_signature = ""
        self.last_deferred_report_at = 0.0
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self, limit: int = 0) -> None:
        install_stop_handlers(self.stop)
        print("Python ontology reasoning worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once(limit=limit)
                if result.get("processedCount"):
                    print(
                        "Ontology reasoning "
                        + str(result.get("status"))
                        + " processed="
                        + str(result.get("processedCount", 0))
                        + " alerts="
                        + str(result.get("alertCount", 0))
                    )
                    self.last_deferred_signature = ""
                    self.last_deferred_report_at = 0.0
                elif str(result.get("status") or "") in {"deferred", "circuit-open"}:
                    reason = str(result.get("deferredReason") or "TypeDB projection is not ready.")
                    signature = str(result.get("status") or "") + "|" + reason
                    # A worker can retry every few seconds. Log the first
                    # distinct block immediately, then retain one heartbeat
                    # per minute so a persistent block stays observable.
                    if (
                        signature != self.last_deferred_signature
                        or started - self.last_deferred_report_at >= 60.0
                    ):
                        print(
                            "Ontology reasoning "
                            + str(result.get("status"))
                            + " retryAfter="
                            + str(result.get("retryAfterSeconds", 0))
                            + "s reason="
                            + reason[:280]
                        )
                        self.last_deferred_signature = signature
                        self.last_deferred_report_at = started
            except Exception as error:  # noqa: BLE001 - long-running reasoning worker must continue after a cycle failure.
                print("Python ontology reasoning worker error: " + str(error))
                report_runtime_error(self.error_reporter, "Python ontology reasoning worker", error, "inference cycle")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class OntologyLabScheduler:
    def __init__(self, service, interval_seconds: int, error_reporter=None):
        self.service = service
        self.interval_seconds = max(5, int(interval_seconds or 300))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.last_auto_suggest_at = 0.0
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self, limit: int = 0, force: bool = False) -> None:
        install_stop_handlers(self.stop)
        print(
            "Python ontology lab worker started. interval="
            + str(self.interval_seconds)
            + "s autoSuggestInterval="
            + str(self.service.auto_suggest_interval_seconds())
            + "s"
        )
        while self.running:
            started = time.monotonic()
            try:
                result = self.service.run_once(limit=limit, force=force)
                if result.get("processedCount"):
                    print(
                        "Ontology lab "
                        + str(result.get("status"))
                        + " processed="
                        + str(result.get("processedCount", 0))
                        + " runs="
                        + str(result.get("runCount", 0))
                        + " skipped="
                        + str(result.get("skippedCount", 0))
                    )
                if self.auto_suggest_due(started):
                    auto_result = self.service.auto_suggest()
                    self.last_auto_suggest_at = time.monotonic()
                    print(
                        "Ontology lab auto-suggest "
                        + str(auto_result.get("status"))
                        + " created="
                        + str(auto_result.get("createdCount", 0))
                        + " skipped="
                        + str(auto_result.get("skippedCount", 0))
                    )
            except Exception as error:  # noqa: BLE001 - long-running lab worker must continue after a cycle failure.
                print("Python ontology lab worker error: " + str(error))
                report_runtime_error(self.error_reporter, "Python ontology lab worker", error, "lab cycle")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)

    def auto_suggest_due(self, now: float) -> bool:
        if not self.service.auto_suggest_enabled() or not self.service.auto_suggest_configured():
            return False
        interval = self.service.auto_suggest_interval_seconds()
        return not self.last_auto_suggest_at or now - self.last_auto_suggest_at >= interval


class MarketDataCollectionScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(3 * 60, int(interval_seconds or 180))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        print("Python market data collector started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once()
                print("Market data collection " + str(result.get("status")) + " saved=" + str(result.get("savedCount", 0)))
            except Exception as error:  # noqa: BLE001 - long-running collector must continue after provider failures.
                print("Python market data collector error: " + str(error))
                report_runtime_error(self.error_reporter, "Python market data collector", error, "collection cycle")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class KISRealtimeWebSocketScheduler:
    def __init__(self, runner, reconnect_delay_seconds: int = 5, error_reporter=None):
        self.runner = runner
        self.reconnect_delay_seconds = max(1, int(reconnect_delay_seconds or 5))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        print("Python KIS realtime WebSocket worker started. reconnect=" + str(self.reconnect_delay_seconds) + "s")
        while self.running:
            try:
                result = self.runner.run_once()
                print(
                    "KIS realtime websocket "
                    + str(result.get("status"))
                    + " saved="
                    + str(result.get("savedCount", 0))
                    + " symbols="
                    + str(len(result.get("symbols") or [])),
                    flush=True,
                )
            except Exception as error:  # noqa: BLE001 - realtime feed should reconnect after vendor/network errors.
                print("Python KIS realtime WebSocket error: " + str(error), flush=True)
                report_runtime_error(self.error_reporter, "Python KIS realtime WebSocket worker", error, "websocket cycle")
            end_at = time.monotonic() + self.reconnect_delay_seconds
            wait_until_running(lambda: self.running, end_at, self.runner.sleep_fn)


class NewsCollectionScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(60, int(interval_seconds or 60))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        print("Python news collector started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once()
                health = result.get("pipelineHealth") if isinstance(result.get("pipelineHealth"), dict) else {}
                print(
                    "News collection "
                    + str(result.get("status"))
                    + " saved="
                    + str(result.get("savedCount", 0))
                    + " fetched="
                    + str(result.get("fetchedCount", 0))
                    + " health="
                    + str(health.get("state") or "unknown")
                    + " zeroStreak="
                    + str(health.get("consecutiveZeroRuns") or 0)
                    + " providerFailures="
                    + str(health.get("providerFailureCount") or 0),
                    flush=True,
                )
            except Exception as error:  # noqa: BLE001 - long-running collector must continue after provider failures.
                print("Python news collector error: " + str(error))
                report_runtime_error(self.error_reporter, "Python news collector", error, "collection cycle")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class InvestmentResearchScheduler:
    def __init__(self, runner, interval_seconds: int, batch_size: int = 3, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(5, int(interval_seconds or 15))
        self.batch_size = max(1, min(20, int(batch_size or 3)))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        print("Python investment research worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once(limit=self.batch_size)
                if result.get("processedCount"):
                    print(
                        "Investment research processed="
                        + str(result.get("processedCount") or 0)
                        + " queued="
                        + str(result.get("queuedCount") or 0),
                        flush=True,
                    )
            except Exception as error:  # noqa: BLE001 - research queue must continue after one failed task.
                print("Python investment research worker error: " + str(error), flush=True)
                report_runtime_error(self.error_reporter, "Python investment research worker", error, "research cycle")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class InvestmentCalendarScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(30, int(interval_seconds or 60))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        print("Python investment calendar worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once()
                discovery = result.get("calendarDiscovery") if isinstance(result.get("calendarDiscovery"), dict) else {}
                if result.get("dueCount") or result.get("queuedCount") or discovery:
                    print(
                        "Investment calendar "
                        + str(result.get("status"))
                        + " due="
                        + str(result.get("dueCount", 0))
                        + " queued="
                        + str(result.get("queuedCount", 0))
                        + (" discovery=" + str(discovery.get("status")) + " tentative=" + str(discovery.get("tentativeCount", 0)) if discovery else "")
                    )
            except Exception as error:  # noqa: BLE001 - long-running calendar worker must continue after one cycle failure.
                print("Python investment calendar worker error: " + str(error))
                report_runtime_error(self.error_reporter, "Python investment calendar worker", error, "calendar cycle")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)
