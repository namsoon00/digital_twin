import signal
import time


MIN_REALTIME_INTERVAL_SECONDS = 3 * 60


def install_stop_handlers(stop_callback) -> None:
    signal.signal(signal.SIGTERM, stop_callback)
    signal.signal(signal.SIGINT, stop_callback)


def wait_until_running(running, end_at: float, sleep_fn=time.sleep) -> None:
    while running() and time.monotonic() < end_at:
        sleep_fn(min(1.0, end_at - time.monotonic()))


class RealtimeScheduler:
    def __init__(self, runner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(MIN_REALTIME_INTERVAL_SECONDS, int(interval_seconds or MIN_REALTIME_INTERVAL_SECONDS))
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
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class ModelReviewScheduler:
    def __init__(self, runner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(60, int(interval_seconds or 300))
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
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class NotificationQueueScheduler:
    def __init__(self, runner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(5, int(interval_seconds or 30))
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
                    print("Processed notification jobs: " + str(processed))
            except Exception as error:  # noqa: BLE001 - worker must continue after a cycle failure.
                print("Python notification worker error: " + str(error))
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class OntologyReasoningScheduler:
    def __init__(self, runner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(5, int(interval_seconds or 10))
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
            except Exception as error:  # noqa: BLE001 - long-running reasoning worker must continue after a cycle failure.
                print("Python ontology reasoning worker error: " + str(error))
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class OntologyLabScheduler:
    def __init__(self, service, interval_seconds: int):
        self.service = service
        self.interval_seconds = max(5, int(interval_seconds or 300))
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
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)

    def auto_suggest_due(self, now: float) -> bool:
        if not self.service.auto_suggest_enabled() or not self.service.auto_suggest_configured():
            return False
        interval = self.service.auto_suggest_interval_seconds()
        return not self.last_auto_suggest_at or now - self.last_auto_suggest_at >= interval


class MarketDataCollectionScheduler:
    def __init__(self, runner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(3 * 60, int(interval_seconds or 180))
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
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class KISRealtimeWebSocketScheduler:
    def __init__(self, runner, reconnect_delay_seconds: int = 5):
        self.runner = runner
        self.reconnect_delay_seconds = max(1, int(reconnect_delay_seconds or 5))
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
            end_at = time.monotonic() + self.reconnect_delay_seconds
            wait_until_running(lambda: self.running, end_at, self.runner.sleep_fn)


class NewsCollectionScheduler:
    def __init__(self, runner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(60, int(interval_seconds or 60))
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
                print("News collection " + str(result.get("status")) + " saved=" + str(result.get("savedCount", 0)) + " fetched=" + str(result.get("fetchedCount", 0)))
            except Exception as error:  # noqa: BLE001 - long-running collector must continue after provider failures.
                print("Python news collector error: " + str(error))
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class InvestmentCalendarScheduler:
    def __init__(self, runner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(30, int(interval_seconds or 60))
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
                if result.get("dueCount") or result.get("queuedCount"):
                    print(
                        "Investment calendar "
                        + str(result.get("status"))
                        + " due="
                        + str(result.get("dueCount", 0))
                        + " queued="
                        + str(result.get("queuedCount", 0))
                    )
            except Exception as error:  # noqa: BLE001 - long-running calendar worker must continue after one cycle failure.
                print("Python investment calendar worker error: " + str(error))
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)
