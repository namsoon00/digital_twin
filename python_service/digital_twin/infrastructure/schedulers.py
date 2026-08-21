import errno
import inspect
import json
import os
import queue
import signal
import socket
import subprocess
import threading
import time

from .operational_error_reporting import operational_error_reporter, report_runtime_error


MIN_REALTIME_INTERVAL_SECONDS = 3 * 60


def install_stop_handlers(stop_callback) -> None:
    signal.signal(signal.SIGTERM, stop_callback)
    signal.signal(signal.SIGINT, stop_callback)


def wait_until_running(running, end_at: float, sleep_fn=time.sleep) -> None:
    while running() and time.monotonic() < end_at:
        remaining = end_at - time.monotonic()
        if remaining <= 0:
            return
        sleep_fn(min(1.0, remaining))


def _output_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _last_json_object(output: str):
    for line in reversed(str(output or "").splitlines()):
        text = line.strip()
        if not text.startswith("{") or not text.endswith("}"):
            continue
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


class IsolatedOntologyReasoningCycle:
    """Execute one TypeDB reasoning cycle in a killable child process.

    TypeDB client calls can remain inside native code after the Python worker
    receives a signal. The parent scheduler therefore owns only cadence and
    can terminate its dedicated child process group at a hard wall-clock
    limit. The child still owns normal cursor and mailbox transactions.
    """

    def __init__(self, command, working_directory="", process_factory=None, environment=None):
        self.command = list(command or [])
        self.working_directory = str(working_directory or "")
        self.process_factory = process_factory or subprocess.Popen
        self.environment = dict(environment or {})
        self.process = None
        self._stop_requested = False

    def command_for_limit(self, limit: int):
        command = list(self.command)
        if int(limit or 0) > 0:
            command.extend(["--limit", str(int(limit))])
        return command

    def stop(self, grace_seconds: int = 5) -> None:
        """Request child shutdown without re-entering ``Popen.communicate``.

        Python invokes signal handlers in the same main thread that is often
        blocked in ``communicate``.  Calling ``communicate`` again from this
        handler closes the same stdout pipe twice and can surface as
        ``[Errno 9] Bad file descriptor``.  The active ``run_once`` call owns
        waiting and pipe cleanup; shutdown only sends a process-group signal.
        """
        del grace_seconds  # Waiting belongs to the active run, never the signal handler.
        self._stop_requested = True
        process = self.process
        if process is None or process.poll() is not None:
            return
        self._signal_group(process, signal.SIGTERM)

    @staticmethod
    def _signal_group(process, sig) -> None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, sig)
                return
            except (AttributeError, OSError, ProcessLookupError):
                pass
        try:
            process.send_signal(sig)
        except (AttributeError, OSError, ProcessLookupError):
            return

    def _terminate(self, process, grace_seconds: int) -> str:
        self._signal_group(process, signal.SIGTERM)
        try:
            output, _ = process.communicate(timeout=max(1, int(grace_seconds or 1)))
            return _output_text(output)
        except subprocess.TimeoutExpired as error:
            partial = _output_text(getattr(error, "output", ""))
            self._signal_group(process, signal.SIGKILL)
            try:
                output, _ = process.communicate(timeout=max(1, int(grace_seconds or 1)))
                return partial + _output_text(output)
            except subprocess.TimeoutExpired as final_error:
                return partial + _output_text(getattr(final_error, "output", ""))
            except OSError as final_error:
                if getattr(final_error, "errno", None) == errno.EBADF:
                    return partial
                raise
        except OSError as error:
            # A child can close stdout while a terminating TypeDB client exits.
            # It is safe to preserve the timeout result because the process was
            # already signalled and this path never owns an investment decision.
            if getattr(error, "errno", None) == errno.EBADF:
                return ""
            raise

    @staticmethod
    def _stopped_result(process, started: float, output: str = "") -> dict:
        return {
            "status": "stopped",
            "processedCount": 0,
            "alertCount": 0,
            "stopRequested": True,
            "durationMs": int((time.monotonic() - started) * 1000),
            "childExitCode": int(getattr(process, "returncode", 0) or 0),
            "workerOutput": _output_text(output)[-1200:],
        }

    def run_once(
        self,
        limit: int,
        timeout_seconds: int,
        grace_seconds: int,
        cancel_requested=None,
        cancel_poll_seconds: float = 1.0,
    ) -> dict:
        if not self.command:
            return {
                "status": "error",
                "processedCount": 0,
                "alertCount": 0,
                "deferredReason": "추론 격리 실행 명령이 구성되지 않았습니다.",
            }
        started = time.monotonic()
        self._stop_requested = False
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
        }
        if self.working_directory:
            kwargs["cwd"] = self.working_directory
        if self.environment:
            kwargs["env"] = {**os.environ, **self.environment}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        process = self.process_factory(self.command_for_limit(limit), **kwargs)
        self.process = process
        if self._stop_requested:
            self._signal_group(process, signal.SIGTERM)
        deadline = time.monotonic() + max(1, int(timeout_seconds or 1))
        output = ""
        try:
            while True:
                remaining = deadline - time.monotonic()
                try:
                    poll_seconds = (
                        min(remaining, max(0.1, float(cancel_poll_seconds or 1.0)))
                        if callable(cancel_requested)
                        else remaining
                    )
                    output, _ = process.communicate(timeout=max(0.05, poll_seconds))
                    break
                except subprocess.TimeoutExpired as error:
                    if callable(cancel_requested) and bool(cancel_requested()):
                        output = _output_text(getattr(error, "output", "")) + self._terminate(
                            process,
                            grace_seconds,
                        )
                        return {
                            "status": "preempted-reasoning-queue",
                            "processedCount": 0,
                            "alertCount": 0,
                            "preempted": True,
                            "durationMs": int((time.monotonic() - started) * 1000),
                            "childExitCode": int(getattr(process, "returncode", 0) or 0),
                            "workerOutput": output[-1200:],
                        }
                    if remaining > 0 and callable(cancel_requested):
                        continue
                    if self._stop_requested:
                        output = _output_text(getattr(error, "output", "")) + self._terminate(
                            process,
                            grace_seconds,
                        )
                        return self._stopped_result(process, started, output)
                    output = _output_text(getattr(error, "output", "")) + self._terminate(
                        process,
                        grace_seconds,
                    )
                    return {
                        "status": "timeout",
                        "processedCount": 0,
                        "alertCount": 0,
                        "timeout": True,
                        "timeoutSeconds": int(timeout_seconds or 0),
                        "durationMs": int((time.monotonic() - started) * 1000),
                        "workerOutput": output[-1200:],
                    }
                except OSError as error:
                    if self._stop_requested and getattr(error, "errno", None) == errno.EBADF:
                        return self._stopped_result(process, started)
                    raise
        finally:
            if self.process is process:
                self.process = None

        output = _output_text(output)
        if self._stop_requested:
            return self._stopped_result(process, started, output)
        result = _last_json_object(output)
        if result is None:
            return {
                "status": "error",
                "processedCount": 0,
                "alertCount": 0,
                "durationMs": int((time.monotonic() - started) * 1000),
                "childExitCode": int(process.returncode or 0),
                "deferredReason": "격리된 추론 워커가 읽을 수 있는 결과를 반환하지 않았습니다.",
                "workerOutput": output[-1200:],
            }
        result = dict(result)
        result["isolatedExecution"] = True
        result["childExitCode"] = int(process.returncode or 0)
        result["isolatedDurationMs"] = int((time.monotonic() - started) * 1000)
        return result


class PersistentIsolatedOntologyReasoningCycle:
    """Keep one killable TypeDB child warm across bounded reasoning turns.

    One-shot isolation protects the scheduler from a native TypeDB call that
    ignores Python cancellation, but starting a fresh interpreter and driver
    for every mailbox turn makes the handshake the dominant queue cost.  This
    sidecar keeps its repository and driver process-local, while the parent
    still enforces the same hard timeout and replaces the whole process when a
    request stops making progress.
    """

    protocol = "ontology-reasoning-worker-v1"
    persistent_worker = True

    def __init__(self, command, working_directory="", process_factory=None, environment=None):
        self.command = list(command or [])
        self.working_directory = str(working_directory or "")
        self.process_factory = process_factory or subprocess.Popen
        self.environment = dict(environment or {})
        self.process = None
        self._output_queue = queue.Queue()
        self._reader_thread = None
        self._stop_requested = False
        self._request_sequence = 0

    @staticmethod
    def _signal_group(process, sig) -> None:
        IsolatedOntologyReasoningCycle._signal_group(process, sig)

    def _launch(self):
        if not self.command:
            raise RuntimeError("persistent ontology reasoning worker command is not configured")
        kwargs = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
        }
        if self.working_directory:
            kwargs["cwd"] = self.working_directory
        if self.environment:
            kwargs["env"] = {**os.environ, **self.environment}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        process = self.process_factory(self.command, **kwargs)
        self.process = process
        self._output_queue = queue.Queue()
        output = getattr(process, "stdout", None)
        if output is not None:
            self._reader_thread = threading.Thread(
                target=self._read_output,
                args=(output,),
                name="ontology-reasoning-sidecar-output",
                daemon=True,
            )
            self._reader_thread.start()
        else:
            self._reader_thread = None
        return process

    def _read_output(self, output) -> None:
        try:
            for line in iter(output.readline, ""):
                self._output_queue.put(_output_text(line))
        except Exception as error:  # noqa: BLE001 - parent reports a missing response below.
            self._output_queue.put("sidecar-output-error: " + str(error))
        finally:
            self._output_queue.put(None)

    @staticmethod
    def _process_exit_code(process) -> int:
        code = getattr(process, "returncode", None)
        if code is None:
            try:
                code = process.poll()
            except Exception:
                code = None
        try:
            return int(code) if code is not None else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _wait_for_exit(process, timeout_seconds: float) -> bool:
        end_at = time.monotonic() + max(0.05, float(timeout_seconds or 0.05))
        while time.monotonic() < end_at:
            try:
                if process.poll() is not None:
                    return True
            except Exception:
                return True
            time.sleep(0.02)
        try:
            return process.poll() is not None
        except Exception:
            return True

    @staticmethod
    def _close_stdin(process) -> None:
        stream = getattr(process, "stdin", None)
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    @staticmethod
    def _close_stdout(process) -> None:
        stream = getattr(process, "stdout", None)
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def _discard_process(self, process, grace_seconds: int) -> None:
        self._close_stdin(process)
        self._signal_group(process, signal.SIGTERM)
        if not self._wait_for_exit(process, max(1, int(grace_seconds or 1))):
            self._signal_group(process, signal.SIGKILL)
            self._wait_for_exit(process, max(1, int(grace_seconds or 1)))
        reader = self._reader_thread
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=0.2)
        self._close_stdout(process)
        if self.process is process:
            self.process = None
            self._reader_thread = None

    def _drain_output(self) -> str:
        values = []
        while True:
            try:
                line = self._output_queue.get_nowait()
            except queue.Empty:
                break
            if line is not None:
                values.append(_output_text(line))
        return "".join(values)[-1200:]

    def stop(self, grace_seconds: int = 5) -> None:
        self._stop_requested = True
        process = self.process
        if process is None:
            return
        try:
            alive = process.poll() is None
        except Exception:
            alive = False
        if alive:
            self._signal_group(process, signal.SIGTERM)
        # The active request owns pipe draining.  It will observe the signal
        # and clear the sidecar; calling ``communicate`` here races its reader
        # thread in exactly the same way as the original one-shot worker.
        del grace_seconds

    def close(self, grace_seconds: int = 5) -> None:
        """Terminate and release the sidecar when its scheduler is retired."""
        process = self.process
        if process is not None:
            self._discard_process(process, grace_seconds)

    def run_once(
        self,
        limit: int,
        timeout_seconds: int,
        grace_seconds: int,
        action: str = "run",
    ) -> dict:
        started = time.monotonic()
        self._stop_requested = False
        process = self.process
        try:
            alive = process is not None and process.poll() is None
        except Exception:
            alive = False
        if not alive:
            if process is not None:
                self._discard_process(process, grace_seconds)
            try:
                process = self._launch()
            except Exception as error:  # noqa: BLE001 - preserve the parent scheduler boundary.
                return {
                    "status": "error",
                    "processedCount": 0,
                    "alertCount": 0,
                    "durationMs": int((time.monotonic() - started) * 1000),
                    "deferredReason": "영구 격리 추론 워커를 시작하지 못했습니다: " + str(error)[:180],
                }
        if self._stop_requested:
            self._discard_process(process, grace_seconds)
            return {
                "status": "stopped",
                "processedCount": 0,
                "alertCount": 0,
                "stopRequested": True,
                "durationMs": int((time.monotonic() - started) * 1000),
                "childExitCode": self._process_exit_code(process),
            }
        stream = getattr(process, "stdin", None)
        if stream is None:
            self._discard_process(process, grace_seconds)
            return {
                "status": "error",
                "processedCount": 0,
                "alertCount": 0,
                "durationMs": int((time.monotonic() - started) * 1000),
                "deferredReason": "영구 격리 추론 워커의 요청 채널을 열 수 없습니다.",
            }
        self._request_sequence += 1
        request_id = str(self._request_sequence)
        prior_output = self._drain_output()
        try:
            stream.write(json.dumps({
                "protocol": self.protocol,
                "requestId": request_id,
                "action": str(action or "run"),
                "limit": max(0, int(limit or 0)),
            }, ensure_ascii=False) + "\n")
            stream.flush()
        except Exception as error:  # noqa: BLE001 - a dead sidecar is replaced on the next scheduler turn.
            self._discard_process(process, grace_seconds)
            return {
                "status": "error",
                "processedCount": 0,
                "alertCount": 0,
                "durationMs": int((time.monotonic() - started) * 1000),
                "deferredReason": "영구 격리 추론 워커에 요청을 전달하지 못했습니다: " + str(error)[:180],
                "workerOutput": prior_output[-1200:],
            }
        output = prior_output
        deadline = started + max(1, int(timeout_seconds or 1))
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                line = self._output_queue.get(timeout=remaining)
            except queue.Empty:
                break
            if line is None:
                output = (output + "\nsidecar-output-closed")[-1200:]
                break
            text = _output_text(line)
            output = (output + text)[-1200:]
            try:
                envelope = json.loads(text)
            except (TypeError, ValueError):
                continue
            if not isinstance(envelope, dict):
                continue
            if str(envelope.get("protocol") or "") != self.protocol:
                continue
            if str(envelope.get("requestId") or "") != request_id:
                continue
            result = envelope.get("result")
            if not isinstance(result, dict):
                result = {
                    "status": "error",
                    "processedCount": 0,
                    "alertCount": 0,
                    "deferredReason": "영구 격리 추론 워커가 유효한 결과를 반환하지 않았습니다.",
                }
            response = dict(result)
            response["isolatedExecution"] = True
            response["persistentIsolatedWorker"] = True
            response["childExitCode"] = self._process_exit_code(process)
            response["isolatedDurationMs"] = int((time.monotonic() - started) * 1000)
            return response
        if self._stop_requested:
            self._discard_process(process, grace_seconds)
            return {
                "status": "stopped",
                "processedCount": 0,
                "alertCount": 0,
                "stopRequested": True,
                "durationMs": int((time.monotonic() - started) * 1000),
                "childExitCode": self._process_exit_code(process),
                "workerOutput": output[-1200:],
            }
        self._discard_process(process, grace_seconds)
        return {
            "status": "timeout",
            "processedCount": 0,
            "alertCount": 0,
            "timeout": True,
            "timeoutSeconds": int(timeout_seconds or 0),
            "durationMs": int((time.monotonic() - started) * 1000),
            "workerOutput": output[-1200:],
            "persistentIsolatedWorker": True,
        }

    def recover_dead_leases(self, timeout_seconds: int, grace_seconds: int) -> dict:
        """Recover a killed child’s TypeDB leases inside a fresh sidecar.

        The normal timeout path intentionally never calls TypeDB from the
        scheduler parent.  This request starts a replacement child when
        needed, gives the recovery its own small hard boundary, and leaves
        the queue retryable if the TypeDB control plane is unavailable.
        """
        return self.run_once(
            0,
            timeout_seconds,
            grace_seconds,
            action="recover-dead-leases",
        )


class RealtimeScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None, minimum_interval_seconds: int = None):
        self.runner = runner
        minimum = max(5, int(minimum_interval_seconds or MIN_REALTIME_INTERVAL_SECONDS))
        self.interval_seconds = max(minimum, int(interval_seconds or minimum))
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


class AIInferenceQueueScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(2, int(interval_seconds or 5))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False
        stopper = getattr(self.runner, "stop", None)
        if callable(stopper):
            stopper()

    def run_forever(self, limit: int = 1) -> None:
        install_stop_handlers(self.stop)
        print(
            "Python notification AI inference worker started. worker="
            + str(getattr(self.runner, "worker_id", "unknown"))
            + " interval="
            + str(self.interval_seconds)
            + "s"
        )
        while self.running:
            started = time.monotonic()
            try:
                processed = self.runner.run_once(limit=limit)
                if processed:
                    details = list(getattr(self.runner, "last_run_details", []) or [])
                    print("Processed AI inference requests: " + str(processed) + " · " + "; ".join(details[:6]))
            except Exception as error:  # noqa: BLE001 - worker must continue after a failed request.
                print("Python notification AI inference worker error: " + str(error))
                report_runtime_error(
                    self.error_reporter,
                    "Python notification AI inference worker",
                    error,
                    "AI inference cycle",
                )
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class OperationalHistoryRetentionScheduler:
    """Run bounded MySQL retention only when no realtime code creates stores.

    Cleanup may scan and delete historical rows. Keeping it in a separate
    low-priority worker prevents a transient lock or slow query from becoming
    part of the TypeDB inference deadline.
    """

    def __init__(self, cleanup_once, interval_seconds: int, error_reporter=None):
        self.cleanup_once = cleanup_once
        self.interval_seconds = max(60, int(interval_seconds or 300))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_once(self) -> dict:
        return dict(self.cleanup_once() or {})

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        print(
            "Python operational history maintenance worker started. interval="
            + str(self.interval_seconds)
            + "s"
        )
        while self.running:
            started = time.monotonic()
            next_interval = self.interval_seconds
            try:
                result = self.run_once()
                try:
                    next_interval = max(
                        60,
                        min(self.interval_seconds, int(result.get("nextIntervalSeconds") or self.interval_seconds)),
                    )
                except (TypeError, ValueError):
                    next_interval = self.interval_seconds
                deleted = int(result.get("deleted") or 0)
                skipped = str(result.get("skipped") or "")
                if deleted or skipped:
                    print(
                        "Operational history maintenance deleted="
                        + str(deleted)
                        + (" skipped=" + skipped if skipped else ""),
                        flush=True,
                    )
            except Exception as error:  # noqa: BLE001 - retention must never stop runtime processing.
                print("Python operational history maintenance error: " + str(error), flush=True)
                report_runtime_error(
                    self.error_reporter,
                    "Python operational history maintenance worker",
                    error,
                    "MySQL history retention",
                )
            end_at = time.monotonic() + max(1.0, next_interval - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class OntologyReasoningScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None, isolated_cycle=None):
        self.runner = runner
        self.interval_seconds = max(5, int(interval_seconds or 10))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.isolated_cycle = isolated_cycle
        # The parent passes this stable id to its killable child.  If the
        # child times out, only this invocation's mailbox lease is released;
        # a different scheduler instance cannot be accidentally reclaimed.
        self.worker_id = "reasoning-watch:" + socket.gethostname() + ":" + str(os.getpid())
        self.last_deferred_signature = ""
        self.last_deferred_report_at = 0.0
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False
        if self.isolated_cycle:
            grace = self.execution_timeout_grace_seconds()
            self.isolated_cycle.stop(grace)

    def process_isolation_enabled(self) -> bool:
        configured = getattr(self.runner, "process_isolation_enabled", None)
        return bool(self.isolated_cycle) and (not callable(configured) or bool(configured()))

    def execution_timeout_seconds(self) -> int:
        configured = getattr(self.runner, "execution_timeout_seconds", None)
        return max(5, int(configured() if callable(configured) else 240))

    def execution_timeout_grace_seconds(self) -> int:
        configured = getattr(self.runner, "execution_timeout_grace_seconds", None)
        return max(1, int(configured() if callable(configured) else 10))

    def timeout_lease_recovery_seconds(self) -> int:
        """Bound post-timeout TypeDB cleanup independently of inference.

        A native inference can receive a larger budget, but recovering a
        dead local lease is a control operation.  It gets at most 30 seconds
        (20 seconds with the normal ten-second shutdown grace) and always
        runs in the replacement sidecar.
        """
        return max(5, min(30, self.execution_timeout_grace_seconds() * 2))

    def run_once(self, limit: int = 0):
        if not self.process_isolation_enabled():
            return self.runner.run_once(limit=limit)
        # This guard reads only the durable cursor. A terminated client does
        # not prove that TypeDB stopped its server-side transaction, so do not
        # even wake the persistent sidecar while its timeout backoff is open.
        # It is intentionally separate from the TypeDB coordinator preflight,
        # which remains inside a killable persistent child.
        timeout_guard_preflight = {}
        timeout_guard_check = getattr(self.runner, "isolated_timeout_guard_preflight", None)
        if callable(timeout_guard_check):
            try:
                timeout_guard_preflight = dict(timeout_guard_check() or {})
            except Exception:
                # The child remains authoritative if the lightweight cursor
                # read itself is temporarily unavailable.
                timeout_guard_preflight = {"ready": True, "status": "timeout-guard-probe-error"}
        if timeout_guard_preflight and not bool(timeout_guard_preflight.get("ready", True)):
            return {
                "status": "deferred",
                "processedCount": 0,
                "alertCount": 0,
                "retryAfterSeconds": max(
                    1,
                    int(timeout_guard_preflight.get("retryAfterSeconds") or self.interval_seconds),
                ),
                "deferredReason": str(
                    timeout_guard_preflight.get("reason")
                    or "이전 TypeDB 추론의 안전 재시도 대기가 끝날 때까지 새 워커를 시작하지 않습니다."
                )[:220],
                "executionTimeoutGuard": dict(timeout_guard_preflight.get("executionTimeoutGuard") or {}),
                "isolatedTimeoutGuardPreflight": timeout_guard_preflight,
            }
        recovery = {}
        recover_orphaned = getattr(self.runner, "recover_orphaned_mailbox_work", None)
        if callable(recover_orphaned):
            try:
                recovery = dict(recover_orphaned() or {})
            except Exception:
                recovery = {"enabled": False, "recovered": []}
        grace_wait = max(0, int(recovery.get("waitingForGraceCount") or 0))
        if grace_wait:
            return {
                "status": "deferred",
                "processedCount": 0,
                "alertCount": 0,
                "retryAfterSeconds": max(1, int(recovery.get("retryAfterSeconds") or self.interval_seconds)),
                "deferredReason": "이전 로컬 추론 워커 종료 유예를 확인한 뒤 영속 작업 lease를 회수합니다.",
                "mailboxOrphanLeaseRecovery": recovery,
            }
        # A one-shot child is expensive to launch, so it keeps the historic
        # TypeDB coordinator probe in the parent.  A persistent child has
        # already removed that launch cost.  More importantly, keeping a
        # native-driver control read in the parent would put it outside the
        # hard timeout boundary: a stalled driver handshake could freeze the
        # queue before the killable sidecar ever receives a request.  Let the
        # warm sidecar perform its ordinary coordinator/lease acquisition and
        # contain *all* TypeDB work in the replaceable process instead.
        persistent_worker = bool(getattr(self.isolated_cycle, "persistent_worker", False))
        if not persistent_worker:
            preflight = {}
            preflight_check = getattr(self.runner, "isolated_execution_preflight", None)
            if callable(preflight_check):
                try:
                    preflight = dict(preflight_check() or {})
                except Exception as error:  # noqa: BLE001 - the child remains the authoritative scheduler path.
                    preflight = {"ready": True, "status": "preflight-error", "reason": str(error)[:180]}
            if preflight and not bool(preflight.get("ready", True)):
                return {
                    "status": str(preflight.get("status") or "deferred"),
                    "processedCount": 0,
                    "alertCount": 0,
                    "retryAfterSeconds": max(
                        1,
                        int(preflight.get("retryAfterSeconds") or self.interval_seconds),
                    ),
                    "deferredReason": str(
                        preflight.get("reason")
                        or "TypeDB 투영 경계를 확인한 뒤 추론 워커를 다시 시작합니다."
                    )[:220],
                    "isolatedExecutionPreflight": preflight,
                    **({"mailboxOrphanLeaseRecovery": recovery} if recovery else {}),
                }
        current_environment = dict(getattr(self.isolated_cycle, "environment", {}) or {})
        current_environment["ONTOLOGY_REASONING_WORKER_ID"] = self.worker_id
        self.isolated_cycle.environment = current_environment
        result = self.isolated_cycle.run_once(
            limit,
            self.execution_timeout_seconds(),
            self.execution_timeout_grace_seconds(),
        )
        if recovery:
            result = {**dict(result or {}), "mailboxOrphanLeaseRecovery": recovery}
        if not result.get("timeout"):
            return result
        recorder = getattr(self.runner, "record_execution_timeout", None)
        if not callable(recorder):
            return result
        dead_lease_recovery = None
        if bool(getattr(self.isolated_cycle, "persistent_worker", False)):
            recover_dead_leases = getattr(self.isolated_cycle, "recover_dead_leases", None)
            if callable(recover_dead_leases):
                recovery_result = recover_dead_leases(
                    self.timeout_lease_recovery_seconds(),
                    self.execution_timeout_grace_seconds(),
                )
                if recovery_result.get("timeout"):
                    dead_lease_recovery = {
                        "status": "timeout",
                        "clearedCount": 0,
                        "reason": "격리된 TypeDB lease 복구가 제한 시간 안에 끝나지 않았습니다.",
                        "isolatedDurationMs": int(recovery_result.get("durationMs") or 0),
                    }
                else:
                    raw_recovery = recovery_result.get("typedbDeadLeaseRecovery")
                    if isinstance(raw_recovery, dict):
                        dead_lease_recovery = dict(raw_recovery)
                        dead_lease_recovery["isolatedDurationMs"] = int(
                            recovery_result.get("isolatedDurationMs")
                            or recovery_result.get("durationMs")
                            or 0
                        )
                    else:
                        dead_lease_recovery = {
                            "status": "invalid-sidecar-response",
                            "clearedCount": 0,
                        }
        timeout_seconds = int(result.get("timeoutSeconds") or self.execution_timeout_seconds())
        timeout_output = str(result.get("workerOutput") or "")
        try:
            if dead_lease_recovery is None:
                recorded = recorder(
                    timeout_seconds,
                    output=timeout_output,
                    worker_id=self.worker_id,
                )
            else:
                recorded = recorder(
                    timeout_seconds,
                    output=timeout_output,
                    worker_id=self.worker_id,
                    dead_lease_recovery=dead_lease_recovery,
                )
        except TypeError:
            # Compatibility runners may predate the isolated-recovery
            # argument, or (in older tests) the durable worker-id argument.
            try:
                recorded = recorder(
                    timeout_seconds,
                    output=timeout_output,
                    worker_id=self.worker_id,
                )
            except TypeError:
                recorded = recorder(timeout_seconds, output=timeout_output)
        return {
            **recorded,
            "isolatedExecution": True,
            "isolatedDurationMs": int(result.get("durationMs") or 0),
            "workerOutput": str(result.get("workerOutput") or "")[-1200:],
            **({"timeoutLeaseRecovery": dead_lease_recovery} if dead_lease_recovery else {}),
            **({"mailboxOrphanLeaseRecovery": recovery} if recovery else {}),
        }

    def run_forever(self, limit: int = 0) -> None:
        install_stop_handlers(self.stop)
        persistent = bool(getattr(self.isolated_cycle, "persistent_worker", False))
        mode = (
            "isolated-persistent" if self.process_isolation_enabled() and persistent
            else "isolated" if self.process_isolation_enabled()
            else "in-process"
        )
        print(
            "Python ontology reasoning worker started. interval="
            + str(self.interval_seconds)
            + "s mode="
            + mode
            + (" timeout=" + str(self.execution_timeout_seconds()) + "s" if mode.startswith("isolated") else "")
        )
        try:
            while self.running:
                started = time.monotonic()
                try:
                    result = self.run_once(limit=limit)
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
                    result = {}
                retry_after = 0
                try:
                    retry_after = max(0, int(float((result or {}).get("retryAfterSeconds") or 0)))
                except (TypeError, ValueError):
                    retry_after = 0
                wait_seconds = max(
                    1.0,
                    self.interval_seconds - (time.monotonic() - started),
                    float(retry_after),
                )
                end_at = time.monotonic() + wait_seconds
                wait_until_running(lambda: self.running, end_at)
        finally:
            close = getattr(self.isolated_cycle, "close", None)
            if callable(close):
                close(self.execution_timeout_grace_seconds())


class OntologyWorldProjectionScheduler:
    """Drive durable MarketWorld/KnowledgeWorld projection independently."""

    def __init__(self, runner, interval_seconds: int, error_reporter=None, isolated_cycle=None):
        self.runner = runner
        self.interval_seconds = max(5, int(interval_seconds or 10))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.isolated_cycle = isolated_cycle
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False
        if self.isolated_cycle:
            grace = self.execution_timeout_grace_seconds()
            self.isolated_cycle.stop(grace)

    def process_isolation_enabled(self) -> bool:
        return bool(self.isolated_cycle)

    def execution_timeout_seconds(self) -> int:
        configured = getattr(self.runner, "execution_timeout_seconds", None)
        return max(15, int(configured() if callable(configured) else 150))

    def execution_timeout_grace_seconds(self) -> int:
        configured = getattr(self.runner, "execution_timeout_grace_seconds", None)
        return max(1, int(configured() if callable(configured) else 10))

    def run_once(self, limit: int = 0):
        preflight = getattr(self.runner, "reasoning_queue_deferral", None)
        if callable(preflight):
            result = dict(preflight() or {})
            if result:
                result.setdefault("durationMs", 0)
                return result
        if not self.process_isolation_enabled():
            return self.runner.run_once(limit=limit)
        return self.isolated_cycle.run_once(
            limit,
            self.execution_timeout_seconds(),
            self.execution_timeout_grace_seconds(),
        )

    def run_forever(self, limit: int = 0) -> None:
        install_stop_handlers(self.stop)
        print(
            "Python ontology world projection worker started. interval="
            + str(self.interval_seconds)
            + "s mode="
            + ("isolated" if self.process_isolation_enabled() else "in-process")
        )
        while self.running:
            started = time.monotonic()
            try:
                result = self.run_once(limit=limit)
                if result.get("claimedCount"):
                    print(
                        "Ontology world projection completed="
                        + str(result.get("completedCount", 0))
                        + " retried="
                        + str(result.get("retryCount", 0)),
                        flush=True,
                    )
            except Exception as error:  # noqa: BLE001 - a shared-world failure must not stop the worker.
                print("Python ontology world projection worker error: " + str(error), flush=True)
                report_runtime_error(self.error_reporter, "Python ontology world projection worker", error, "shared world projection")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class OntologyInferenceDetailScheduler(OntologyWorldProjectionScheduler):
    """Drive low-priority durable InferenceBox detail readback.

    It deliberately shares the isolated-cycle and live-queue preflight
    semantics with shared-world projection, while keeping its operational
    output distinct so an expensive detailed TypeDB traversal is visible.
    """

    def run_forever(self, limit: int = 0) -> None:
        install_stop_handlers(self.stop)
        print(
            "Python ontology inference detail worker started. interval="
            + str(self.interval_seconds)
            + "s mode="
            + ("isolated" if self.process_isolation_enabled() else "in-process")
        )
        while self.running:
            started = time.monotonic()
            try:
                result = self.run_once(limit=limit)
                if result.get("claimedCount"):
                    print(
                        "Ontology inference detail completed="
                        + str(result.get("completedCount", 0))
                        + " superseded="
                        + str(result.get("supersededCount", 0))
                        + " retried="
                        + str(result.get("retryCount", 0)),
                        flush=True,
                    )
            except Exception as error:  # noqa: BLE001 - diagnostic detail must never stop live reasoning.
                print("Python ontology inference detail worker error: " + str(error), flush=True)
                report_runtime_error(
                    self.error_reporter,
                    "Python ontology inference detail worker",
                    error,
                    "InferenceBox detailed readback",
                )
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class OntologyRuleboxPrewarmScheduler:
    """Run bounded RuleBox compilation outside the live inference worker."""

    def __init__(self, runner, interval_seconds: int, error_reporter=None, isolated_cycle=None):
        self.runner = runner
        # Schema commits normally wait for an empty durable reasoning queue.
        # An aged queue without an active inference lease is the controlled
        # recovery exception: direct TypeQL has already failed to drain it, so
        # the coordinator-protected compiler gets a bounded turn.
        self.interval_seconds = max(5, int(interval_seconds or 15))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.isolated_cycle = isolated_cycle
        self.last_signature = ""
        self.last_report_at = 0.0
        # Do not assume an empty queue immediately after a worker restart is
        # a maintenance window. The parent scheduler survives isolated child
        # exits and is the one place that can measure a continuous quiet
        # interval without persisting compiler state in TypeDB.
        self.last_reasoning_activity_at = time.monotonic()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False
        if self.isolated_cycle:
            self.isolated_cycle.stop(self.execution_timeout_grace_seconds())

    def process_isolation_enabled(self) -> bool:
        configured = getattr(self.runner, "process_isolation_enabled", None)
        return bool(self.isolated_cycle) and (not callable(configured) or bool(configured()))

    def execution_timeout_seconds(self) -> int:
        configured = getattr(self.runner, "execution_timeout_seconds", None)
        return max(30, int(configured() if callable(configured) else 180))

    def execution_timeout_grace_seconds(self) -> int:
        configured = getattr(self.runner, "execution_timeout_grace_seconds", None)
        return max(1, int(configured() if callable(configured) else 10))

    def idle_quiet_seconds(self) -> int:
        configured = getattr(self.runner, "idle_quiet_seconds", None)
        return max(30, int(configured() if callable(configured) else 300))

    def idle_compile_guard(self) -> dict:
        """Return a no-TypeDB compiler deferral while live work is recent.

        The runner's queue probe reads MySQL mailbox state only. Keeping this
        check in the parent prevents a just-created isolated child from
        starting a schema transaction in the brief gap between reasoning
        batches.
        """
        activity_reader = getattr(self.runner, "prewarm_activity_state", None)
        if callable(activity_reader):
            try:
                candidate = activity_reader()
                activity = dict(candidate or {}) if isinstance(candidate, dict) else {}
            except Exception:
                activity = {}
            if bool(activity.get("active")):
                status = str(activity.get("status") or "running")
                return {
                    "status": "deferred-compiler-activity",
                    "configured": True,
                    "functionsReady": False,
                    "pendingRuleCount": None,
                    "prewarmActivity": activity,
                    "reason": (
                        "TypeDB RuleBox compiler is " + status
                        + "; the scheduler keeps the next schema writer idle."
                    ),
                    "recommendedRetryAfterSeconds": max(
                        1,
                        int(activity.get("retryAfterSeconds") or self.interval_seconds),
                    ),
                    "durationMs": 0,
                }
        queue_reader = getattr(self.runner, "reasoning_queue_state", None)
        pending_reader = getattr(self.runner, "pending_reasoning_count", None)
        if not callable(queue_reader) or not callable(pending_reader):
            return {}
        try:
            queue = dict(queue_reader() or {})
            pending = max(0, int(pending_reader(queue) or 0))
        except Exception:
            # A compiler pass is lower priority than a queue visibility
            # failure. Treat an unavailable queue probe as recent live work.
            self.last_reasoning_activity_at = time.monotonic()
            return {
                "status": "deferred-reasoning-queue-probe",
                "configured": True,
                "functionsReady": None,
                "pendingRuleCount": None,
                "reasoningPendingCount": 0,
                "reason": "Reasoning queue state could not be confirmed; RuleBox compilation remains idle.",
                "recommendedRetryAfterSeconds": self.interval_seconds,
                "durationMs": 0,
            }
        now = time.monotonic()
        if pending:
            self.last_reasoning_activity_at = now
            bootstrap_reader = getattr(self.runner, "cold_bootstrap_state", None)
            bootstrap = {}
            if callable(bootstrap_reader):
                try:
                    candidate = bootstrap_reader(queue)
                    bootstrap = dict(candidate or {}) if isinstance(candidate, dict) else {}
                except Exception:
                    bootstrap = {}
            if bool(bootstrap.get("canBootstrap")):
                # A strict live worker has intentionally deferred before
                # taking a TypeDB inference lease. Let the compiler child
                # inspect and advance its staged receipt now; otherwise the
                # queue and the empty-queue prewarm policy would wait for one
                # another indefinitely after a TypeDB restart.
                return {}
            recovery_reader = getattr(self.runner, "backlog_recovery_state", None)
            recovery = {}
            if callable(recovery_reader):
                try:
                    candidate = recovery_reader(queue)
                    recovery = dict(candidate or {}) if isinstance(candidate, dict) else {}
                except Exception:
                    recovery = {}
            if bool(recovery.get("canRecover")) and not bool(
                recovery.get("directTypeqlFallbackEnabled")
            ):
                # The runner will take the TypeDB projection coordinator before
                # compiling. A racing reasoning worker therefore receives a
                # cheap coordinator deferral rather than concurrent native
                # inference while the schema compiler is active.
                return {}
            return {
                "status": (
                    "deferred-aged-reasoning-backlog-active"
                    if recovery.get("eligible")
                    else "deferred-reasoning-pending"
                ),
                "configured": True,
                "functionsReady": None,
                "pendingRuleCount": None,
                "reasoningPendingCount": pending,
                "reasoningQueue": queue,
                "coldBootstrap": bootstrap,
                "backlogRecovery": recovery,
                "reason": (
                    "Live ontology reasoning has an active lease; RuleBox compilation remains idle."
                    if recovery.get("eligible")
                    else "Live ontology reasoning is pending; RuleBox compilation remains idle."
                ),
                "recommendedRetryAfterSeconds": self.interval_seconds,
                "durationMs": 0,
            }
        remaining = self.idle_quiet_seconds() - (now - self.last_reasoning_activity_at)
        if remaining > 0:
            return {
                "status": "deferred-idle-quiet-period",
                "configured": True,
                "functionsReady": None,
                "pendingRuleCount": None,
                "reasoningPendingCount": 0,
                "reasoningQueue": queue,
                "reason": "RuleBox schema compilation waits for a sustained empty reasoning queue.",
                "recommendedRetryAfterSeconds": max(1, int(remaining + 0.999)),
                "durationMs": 0,
            }
        return {}

    def run_once(self):
        guard = self.idle_compile_guard()
        if guard:
            return guard
        if not self.process_isolation_enabled():
            return self.runner.run_once()
        result = self.isolated_cycle.run_once(
            0,
            self.execution_timeout_seconds(),
            self.execution_timeout_grace_seconds(),
        )
        return self.record_isolated_compiler_handoff(result)

    def record_isolated_compiler_handoff(self, result: dict) -> dict:
        """Persist a cooldown when the child cannot publish its own result.

        The isolated child normally writes ``running`` then its final state.
        An outer hard timeout is the exception: the scheduler owns the result
        while the child is being stopped, and TypeDB may still be completing
        the schema commit after the client disappears.
        """
        payload = dict(result or {})
        status = str(payload.get("status") or "").strip().lower()
        if status != "timeout" and not (
            status == "error" and self.interrupted_compiler_error(payload)
        ):
            return payload
        cooldown_reader = getattr(self.runner, "activity_cooldown_seconds", None)
        publisher = getattr(self.runner, "publish_activity", None)
        if not callable(cooldown_reader) or not callable(publisher):
            return payload
        try:
            cooldown_seconds = max(0, int(cooldown_reader(payload) or 0))
        except Exception:
            cooldown_seconds = 0
        if not cooldown_seconds:
            return payload
        try:
            activity = publisher(
                "cooldown",
                active_seconds=cooldown_seconds,
                result=payload,
            )
        except Exception:
            activity = {}
        if activity:
            payload["prewarmActivity"] = activity
        return payload

    def should_report(self, result: dict, started: float) -> bool:
        status = str(result.get("status") or "unknown")
        try:
            pending = int(result.get("pendingRuleCount") or 0)
        except (TypeError, ValueError):
            pending = 0
        try:
            reasoning_pending = int(result.get("reasoningPendingCount") or 0)
        except (TypeError, ValueError):
            reasoning_pending = 0
        signature = (
            status + "|" + str(pending) + "|" + str(reasoning_pending)
            + "|" + str(result.get("reason") or "")[:120]
        )
        if signature != self.last_signature or started - self.last_report_at >= 300.0:
            self.last_signature = signature
            self.last_report_at = started
            return status not in {"disabled"}
        return False

    @staticmethod
    def interrupted_compiler_error(payload: dict) -> bool:
        """Recognize a TypeDB call that may still be compiling server-side."""
        text = " ".join(str(dict(payload or {}).get(key) or "") for key in [
            "reason", "deferredReason", "workerOutput",
        ]).lower()
        return any(token in text for token in [
            "keep-alive timed out",
            "operation timed out",
            "deadline exceeded",
            "transport error",
            "connection reset",
            "connection closed",
        ])

    def retry_interval_seconds(self, result: dict) -> int:
        """Leave TypeDB recovery time after an expensive schema attempt.

        A TypeDB schema commit may keep compiling after an isolated child has
        timed out. Retrying that same commit every few seconds only adds more
        clients behind the compiler and delays live read transactions. A
        successful partial pass can resume at a measured pace; errors receive
        a longer cooldown.
        """
        payload = dict(result or {})
        status = str(payload.get("status") or "").strip()
        try:
            recommended = int(payload.get("recommendedRetryAfterSeconds") or 0)
        except (TypeError, ValueError):
            recommended = 0
        if status == "timeout":
            # Killing the isolated Python client does not guarantee that a
            # TypeDB schema compiler has stopped. Give the server a real
            # recovery window instead of immediately queueing another compile.
            return max(self.interval_seconds, recommended, 300)
        if status == "error":
            if self.interrupted_compiler_error(payload):
                # Driver-side keep-alive loss has the same safety property as
                # an outer isolated-process timeout: TypeDB can still be
                # completing the schema transaction after the client exits.
                return max(self.interval_seconds, recommended, 300)
            return max(self.interval_seconds, recommended, 60)
        if (
            (
                bool(payload.get("backlogRecoveryGranted"))
                or bool(payload.get("bootstrapPriorityGranted"))
            )
            and status in {"provisioning", "deferred-projection-coordinator"}
        ):
            # A recovery pass has no active inference lease and commits only a
            # bounded RuleBox batch. Respect its short hand-off cadence so the
            # compiler can finish before the retrying queue starts another
            # serial direct-TypeQL cycle.
            return max(3, recommended or 5)
        if status in {
            "provisioning",
            "deferred-projection-coordinator",
            "deferred-reasoning-pending",
            "deferred-aged-reasoning-backlog",
            "deferred-aged-reasoning-backlog-active",
            "deferred-compiler-activity",
            "deferred-idle-quiet-period",
            "deferred-reasoning-queue-probe",
            "deferred-direct-typeql-fallback",
        }:
            return max(self.interval_seconds, recommended, 30)
        return max(self.interval_seconds, recommended)

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        print(
            "Python ontology RuleBox prewarm worker started. interval="
            + str(self.interval_seconds)
            + "s mode="
            + ("isolated" if self.process_isolation_enabled() else "in-process")
        )
        while self.running:
            started = time.monotonic()
            retry_interval = self.interval_seconds
            try:
                result = self.run_once()
                retry_interval = self.retry_interval_seconds(result)
                if self.should_report(result, started):
                    reason = str(result.get("reason") or "").strip()
                    failed_stage = str(result.get("failedStage") or "").strip()
                    print(
                        "Ontology RuleBox prewarm "
                        + str(result.get("status") or "unknown")
                        + " functionsPending="
                        + str(result.get("pendingRuleCount") or 0)
                        + " reasoningPending="
                        + str(result.get("reasoningPendingCount") or 0)
                        + " ready="
                        + str(bool(result.get("functionsReady")))
                        + (" failedStage=" + failed_stage if failed_stage else "")
                        + (" reason=" + reason[:220] if reason else ""),
                        flush=True,
                    )
            except Exception as error:  # noqa: BLE001 - live inference keeps its own safe prior generation.
                retry_interval = max(self.interval_seconds, 60)
                print("Python ontology RuleBox prewarm worker error: " + str(error), flush=True)
                report_runtime_error(
                    self.error_reporter,
                    "Python ontology RuleBox prewarm worker",
                    error,
                    "TypeDB RuleBox schema function prewarm",
                )
            end_at = time.monotonic() + max(1.0, retry_interval - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class OntologyMaintenanceScheduler:
    """Run low-priority scoped ABox retention outside the reasoning worker.

    Each cycle handles one world only. The repository still owns the TypeDB
    writer lease, so a busy live projection wins and retention retries later.
    """

    def __init__(self, runner, interval_seconds: int, error_reporter=None, isolated_cycle=None):
        self.runner = runner
        self.interval_seconds = max(15, int(interval_seconds or 60))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.isolated_cycle = isolated_cycle
        self.last_signature = ""
        self.last_report_at = 0.0
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False
        if self.isolated_cycle:
            self.isolated_cycle.stop(self.execution_timeout_grace_seconds())

    def process_isolation_enabled(self) -> bool:
        configured = getattr(self.runner, "process_isolation_enabled", None)
        return bool(self.isolated_cycle) and (not callable(configured) or bool(configured()))

    def execution_timeout_seconds(self) -> int:
        configured = getattr(self.runner, "execution_timeout_seconds", None)
        return max(30, int(configured() if callable(configured) else 180))

    def execution_timeout_grace_seconds(self) -> int:
        configured = getattr(self.runner, "execution_timeout_grace_seconds", None)
        return max(1, int(configured() if callable(configured) else 10))

    def live_reasoning_pending(self) -> bool:
        queue_reader = getattr(self.runner, "reasoning_queue_state", None)
        counter = getattr(self.runner, "queue_pending_count", None)
        if not callable(queue_reader) or not callable(counter):
            return False
        try:
            return int(counter(dict(queue_reader() or {}))) > 0
        except Exception:
            # The shared TypeDB coordinator remains the final boundary when a
            # transient MySQL probe cannot determine queue state.
            return False

    def run_once(self):
        preflight = getattr(self.runner, "reasoning_queue_deferral", None)
        if callable(preflight):
            result = dict(preflight() or {})
            if result:
                result.setdefault("durationMs", 0)
                return result
        if not self.process_isolation_enabled():
            return self.runner.run_once()
        isolated_run = self.isolated_cycle.run_once
        parameters = inspect.signature(isolated_run).parameters
        kwargs = {}
        if "cancel_requested" in parameters:
            kwargs["cancel_requested"] = self.live_reasoning_pending
            kwargs["cancel_poll_seconds"] = 1.0
        result = isolated_run(
            0,
            self.execution_timeout_seconds(),
            self.execution_timeout_grace_seconds(),
            **kwargs,
        )
        if not bool(result.get("timeout") or result.get("preempted")):
            return result
        recover = getattr(self.runner, "recover_dead_projection_leases", None)
        if not callable(recover):
            return result
        try:
            recovery = dict(recover() or {})
        except Exception as error:  # noqa: BLE001 - lease expiry remains the final fallback.
            recovery = {
                "status": "error",
                "clearedCount": 0,
                "reason": str(error)[:180],
            }
        return {
            **dict(result or {}),
            (
                "preemptionLeaseRecovery"
                if bool(result.get("preempted"))
                else "timeoutLeaseRecovery"
            ): recovery,
        }

    def next_wait_seconds(self, result: dict) -> float:
        """Retry lease-only deferrals inside the normal maintenance interval.

        The retry is a MySQL/lease probe, not an additional TypeDB write. It
        lets retention catch a short idle gap between continuous reasoning
        batches instead of repeatedly waking on the same one-minute phase.
        """
        values = dict(result or {}) if isinstance(result, dict) else {}
        status = str(values.get("status") or "")
        if status not in {
            "deferred-reasoning-queue",
            "deferred-projection-coordinator",
            "deferred-write-lease",
            "deferred-pending-abox-activation",
            "deferred-scope-integrity-repair",
        }:
            return float(self.interval_seconds)
        try:
            retry_after = int(float(values.get("retryAfterSeconds") or self.interval_seconds))
        except (TypeError, ValueError):
            retry_after = self.interval_seconds
        return float(max(5, min(self.interval_seconds, retry_after)))

    def should_report(self, result: dict, started: float) -> bool:
        maintenance = result.get("maintenance") if isinstance(result.get("maintenance"), dict) else {}
        removed = int(maintenance.get("removedManifestCount") or 0)
        deleted_batches = int(maintenance.get("deletedBatchCount") or 0)
        status = str(result.get("status") or "unknown")
        if removed or deleted_batches:
            self.last_signature = ""
            self.last_report_at = 0.0
            return True
        if status in {
            "error",
            "partial",
            "deferred-reasoning-queue",
            "deferred-write-lease",
            "deferred-pending-abox-activation",
            "deferred-scope-integrity-repair",
            "timeout",
        }:
            signature = status + "|" + str(maintenance.get("reason") or result.get("reason") or "")[:180]
            if signature != self.last_signature or started - self.last_report_at >= 300.0:
                self.last_signature = signature
                self.last_report_at = started
                return True
        return False

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        mode = "isolated" if self.process_isolation_enabled() else "in-process"
        print(
            "Python ontology ABox maintenance worker started. interval="
            + str(self.interval_seconds)
            + "s mode="
            + mode
        )
        while self.running:
            started = time.monotonic()
            result = {}
            try:
                result = self.run_once()
                if self.should_report(result, started):
                    maintenance = result.get("maintenance") if isinstance(result.get("maintenance"), dict) else {}
                    print(
                        "Ontology ABox maintenance "
                        + str(result.get("status") or "unknown")
                        + " world="
                        + str(result.get("worldId") or maintenance.get("worldId") or "")
                        + " removed="
                        + str(maintenance.get("removedManifestCount") or 0)
                        + " deleteBatches="
                        + str(maintenance.get("deletedBatchCount") or 0)
                        + " remaining="
                        + str(maintenance.get("inactiveManifestCountRemaining") or 0)
                        + " durationMs="
                        + str(maintenance.get("durationMs") or result.get("durationMs") or 0)
                        + " resume="
                        + str(bool(maintenance.get("resumeRequired"))),
                        flush=True,
                    )
            except Exception as error:  # noqa: BLE001 - retention never stops live inference.
                print("Python ontology ABox maintenance worker error: " + str(error), flush=True)
                report_runtime_error(self.error_reporter, "Python ontology ABox maintenance worker", error, "scoped ABox retention")
            next_interval = self.next_wait_seconds(result)
            end_at = time.monotonic() + max(1.0, next_interval - (time.monotonic() - started))
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


class ExternalDataCollectionScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(5, int(interval_seconds or 15))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        print("Python external data collector started. interval=" + str(self.interval_seconds) + "s", flush=True)
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once()
                print(
                    "External data collection "
                    + str(result.get("status"))
                    + " processed="
                    + str(result.get("processedCount", 0))
                    + " failed="
                    + str(result.get("failureCount", 0)),
                    flush=True,
                )
                for item in result.get("results") or []:
                    if str(item.get("status") or "") != "error":
                        continue
                    error = RuntimeError(str(item.get("error") or "external provider collection failed"))
                    report_runtime_error(
                        self.error_reporter,
                        "Python external data collector",
                        error,
                        str(item.get("datasetId") or "external dataset")
                        + "/"
                        + str(item.get("partitionKey") or "global"),
                    )
            except Exception as error:  # noqa: BLE001 - durable jobs are retried on the next cycle.
                print("Python external data collector error: " + str(error), flush=True)
                report_runtime_error(self.error_reporter, "Python external data collector", error, "collection cycle")
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
                    + str(health.get("providerFailureCount") or 0)
                    + " suppressedProviders="
                    + str(health.get("providerSuppressedCount") or 0),
                    flush=True,
                )
            except Exception as error:  # noqa: BLE001 - long-running collector must continue after provider failures.
                print("Python news collector error: " + str(error))
                report_runtime_error(self.error_reporter, "Python news collector", error, "collection cycle")
            end_at = time.monotonic() + max(1.0, self.interval_seconds - (time.monotonic() - started))
            wait_until_running(lambda: self.running, end_at)


class NewsAnalysisEnrichmentScheduler:
    def __init__(self, runner, interval_seconds: int, error_reporter=None):
        self.runner = runner
        self.interval_seconds = max(15, int(interval_seconds or 60))
        self.error_reporter = error_reporter or operational_error_reporter()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        install_stop_handlers(self.stop)
        print("Python news analysis worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once()
                if result.get("processedCount") or result.get("pendingCount"):
                    print(
                        "News analysis "
                        + str(result.get("status"))
                        + " processed="
                        + str(result.get("processedCount") or 0)
                        + " saved="
                        + str(result.get("savedCount") or 0)
                        + " translated="
                        + str(result.get("translatedCount") or 0)
                        + " pending="
                        + str(result.get("pendingCount") or 0),
                        flush=True,
                    )
            except Exception as error:  # noqa: BLE001 - analysis must not stop collection or later retries.
                print("Python news analysis worker error: " + str(error), flush=True)
                report_runtime_error(self.error_reporter, "Python news analysis worker", error, "analysis cycle")
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
