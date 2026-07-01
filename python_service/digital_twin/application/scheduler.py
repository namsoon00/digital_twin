import signal
import time


MIN_REALTIME_INTERVAL_SECONDS = 10 * 60


class RealtimeScheduler:
    def __init__(self, runner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(MIN_REALTIME_INTERVAL_SECONDS, int(interval_seconds or MIN_REALTIME_INTERVAL_SECONDS))
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        print("Python realtime monitor started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                self.runner.run_once()
            except Exception as error:  # noqa: BLE001 - long-running scheduler must continue after a cycle failure.
                print("Python realtime monitor error: " + str(error))
            elapsed = time.monotonic() - started
            sleep_seconds = max(1.0, self.interval_seconds - elapsed)
            end_at = time.monotonic() + sleep_seconds
            while self.running and time.monotonic() < end_at:
                time.sleep(min(1.0, end_at - time.monotonic()))

