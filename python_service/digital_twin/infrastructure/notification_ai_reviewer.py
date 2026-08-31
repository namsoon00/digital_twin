import os
import signal
import subprocess
import threading
import time
from typing import Dict

from ..domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from ..domain.notification_ai_decision_brief import build_notification_ai_decision_prompt
from ..domain.notification_ai_gate_validation import (
    local_validated_ai_response,
    validated_response_from_text,
)
from .local_ai_process_guard import local_ai_capacity_lease
from .model_reviewer import (
    codex_model_label,
    codex_process_arguments,
    notification_ai_runtime_dir,
)
from .settings import ROOT_DIR, data_dir, runtime_settings


def optional_timeout_seconds(value: object, fallback: object = None):
    candidate = fallback if value in (None, "") else value
    if candidate in (None, ""):
        return None
    try:
        number = int(float(str(candidate)))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class NotificationAIReviewer:
    def review(self, context: Dict[str, object]) -> NotificationAIValidatedResponse:
        raise NotImplementedError


class LocalNotificationAIReviewer(NotificationAIReviewer):
    def review(self, context: Dict[str, object]) -> NotificationAIValidatedResponse:
        return local_validated_ai_response(context, source="local")


class CommandNotificationAIReviewer(NotificationAIReviewer):
    def __init__(
        self,
        command,
        timeout_seconds=None,
        source: str = "AI",
        max_prompt_bytes: int = 16 * 1024,
        command_factory=None,
        settings: Dict[str, object] = None,
        capacity_lock_dir=None,
        capacity_max_concurrent: int = 2,
        capacity_reserved_slots: int = 1,
        capacity_wait_seconds=None,
        runtime_directory=None,
    ):
        self.command = command
        self.timeout_seconds = optional_timeout_seconds(timeout_seconds)
        self.source = source
        self.max_prompt_bytes = max(12 * 1024, min(24 * 1024, int(max_prompt_bytes or 16 * 1024)))
        self.command_factory = command_factory
        self.settings = dict(settings or {})
        self.last_prompt_bytes = 0
        self.last_execution_profile = {}
        self.last_execution_spans = {}
        self.execution_history = []
        self.process = None
        self.cancel_event = threading.Event()
        self.capacity_lock_dir = capacity_lock_dir
        self.capacity_max_concurrent = max(1, min(8, int(capacity_max_concurrent or 2)))
        self.capacity_reserved_slots = max(
            0,
            min(self.capacity_max_concurrent - 1, int(capacity_reserved_slots or 0)),
        )
        self.capacity_wait_seconds = optional_timeout_seconds(capacity_wait_seconds)
        self.runtime_directory = runtime_directory

    def begin_trace(self, _request_id: str = "") -> None:
        self.cancel_event.clear()
        self.last_execution_spans = {}
        self.execution_history = []

    def review(self, context: Dict[str, object]) -> NotificationAIValidatedResponse:
        execution_profile = context.get("notificationAiExecutionProfile") if isinstance(context.get("notificationAiExecutionProfile"), dict) else {}
        reasoning_effort = str(execution_profile.get("reasoningEffort") or "").strip().lower()
        override_present = "_notificationAiTimeoutSecondsOverride" in context
        timeout_seconds = optional_timeout_seconds(
            context.get("_notificationAiTimeoutSecondsOverride") if override_present else None,
            self.timeout_seconds,
        )
        command = (
            self.command_factory(reasoning_effort=reasoning_effort)
            if self.command_factory and reasoning_effort
            else self.command
        )
        if not command:
            raise RuntimeError("notification AI command is not configured")
        prompt = str(context.get("_notificationAiPreparedPrompt") or "")
        if str(context.get("messageType") or "") == "investmentInsight" and not prompt:
            raise RuntimeError(
                "investment notification AI requires a prepared inference packet"
            )
        if not prompt:
            prompt = build_notification_ai_decision_prompt(
                context,
                self.settings,
                max_prompt_bytes=min(
                    self.max_prompt_bytes,
                    int(execution_profile.get("maxPromptBytes") or self.max_prompt_bytes),
                ),
                profile=execution_profile,
            )
        self.last_prompt_bytes = len(prompt.encode("utf-8"))
        self.last_execution_profile = dict(execution_profile or {})
        process_kwargs = {
            "stdin": subprocess.PIPE,
            "text": True,
            "shell": isinstance(command, str),
            "cwd": str(self.runtime_directory or ROOT_DIR),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": dict(os.environ),
        }
        if os.name != "nt":
            process_kwargs["start_new_session"] = True
        total_started = time.monotonic()
        capacity_started = total_started
        capacity_wait_ms = 0
        process_started = None
        process = None
        termination_reason = "completed"
        try:
            lease = local_ai_capacity_lease(
                self.capacity_lock_dir,
                max_concurrent=self.capacity_max_concurrent,
                wait_seconds=self.capacity_wait_seconds,
                lane="investment-judgement",
                reserved_priority_slots=self.capacity_reserved_slots,
                cancel_event=self.cancel_event,
            ) if self.capacity_lock_dir else None
            if lease is None:
                from contextlib import nullcontext
                lease = nullcontext()
            with lease:
                capacity_wait_ms = int((time.monotonic() - capacity_started) * 1000)
                if self.cancel_event.is_set():
                    raise RuntimeError("notification AI execution was cancelled before process start")
                launch_started = time.monotonic()
                process = subprocess.Popen(command, **process_kwargs)
                process_started = time.monotonic()
                launch_ms = int((process_started - launch_started) * 1000)
                self.process = process
                try:
                    if timeout_seconds is None:
                        stdout, stderr = process.communicate(input=prompt)
                    else:
                        stdout, stderr = process.communicate(input=prompt, timeout=timeout_seconds)
                except subprocess.TimeoutExpired as error:
                    termination_reason = "configured-timeout"
                    self.terminate_process(process, force=False)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.terminate_process(process, force=True)
                        process.wait(timeout=2)
                    raise TimeoutError("notification AI command exceeded " + str(timeout_seconds) + " seconds") from error
        except Exception:
            if self.cancel_event.is_set() and termination_reason == "completed":
                termination_reason = "cancelled"
            raise
        finally:
            finished = time.monotonic()
            spans = {
                "completionPolicy": "wait-until-complete" if timeout_seconds is None else "bounded",
                "configuredTimeoutSeconds": int(timeout_seconds or 0),
                "capacityWaitMs": capacity_wait_ms,
                "processLaunchMs": int(locals().get("launch_ms", 0) or 0),
                "modelProcessMs": int((finished - process_started) * 1000) if process_started else 0,
                "totalMs": int((finished - total_started) * 1000),
                "returnCode": int(process.returncode) if process is not None and process.returncode is not None else None,
                "terminationReason": termination_reason,
                "runtimeDirectory": str(self.runtime_directory or ROOT_DIR),
            }
            self.last_execution_spans = spans
            self.execution_history.append(dict(spans))
            if self.process is process:
                self.process = None
        output = str(stdout or "").strip()
        if process.returncode != 0:
            raise RuntimeError((stderr or output or "notification AI command failed").strip())
        if not output:
            raise RuntimeError("notification AI command returned empty output")
        source = self.source
        if reasoning_effort:
            source = "Codex AI (" + codex_model_label(reasoning_effort) + ")"
        return validated_response_from_text(context, output, source=source)

    def stop(self) -> None:
        self.cancel_event.set()
        process = self.process
        if process is not None and process.poll() is None:
            self.terminate_process(process, force=False)

    @staticmethod
    def terminate_process(process, force: bool = False) -> None:
        sig = signal.SIGKILL if force else signal.SIGTERM
        if os.name != "nt":
            try:
                os.killpg(process.pid, sig)
                return
            except (OSError, ProcessLookupError):
                pass
        try:
            process.kill() if force else process.terminate()
        except (OSError, ProcessLookupError):
            return


class FallbackNotificationAIReviewer(NotificationAIReviewer):
    def __init__(self, primary: NotificationAIReviewer, fallback: NotificationAIReviewer = None):
        self.primary = primary
        self.fallback = fallback or LocalNotificationAIReviewer()

    def review(self, context: Dict[str, object]) -> NotificationAIValidatedResponse:
        try:
            return self.primary.review(context)
        except Exception as error:  # noqa: BLE001 - alert delivery falls back to deterministic validation.
            fallback = self.fallback.review(context)
            fallback.source = "local fallback"
            error_lines = [line.strip() for line in str(error).splitlines() if line.strip()]
            error_detail = next((line for line in reversed(error_lines) if "ERROR" in line.upper()), error_lines[-1] if error_lines else str(error))
            fallback.validation_warnings.append("AI 응답 실패로 로컬 검증 의견을 사용했습니다: " + error_detail[:320])
            return fallback

    def begin_trace(self, request_id: str = "") -> None:
        starter = getattr(self.primary, "begin_trace", None)
        if callable(starter):
            starter(request_id)

    @property
    def execution_history(self):
        return list(getattr(self.primary, "execution_history", []) or [])

    @property
    def last_prompt_bytes(self) -> int:
        return int(getattr(self.primary, "last_prompt_bytes", 0) or 0)

    def stop(self) -> None:
        stopper = getattr(self.primary, "stop", None)
        if callable(stopper):
            stopper()


def notification_ai_reviewer_from_settings(
    settings: Dict[str, str] = None,
    *,
    allow_local_fallback: bool = True,
) -> NotificationAIReviewer:
    settings = settings or runtime_settings()
    use_codex = str(settings.get("notificationAiUseCodex") or os.environ.get("NOTIFICATION_AI_USE_CODEX") or "1").strip() != "0"
    reasoning_effort = str(
        settings.get("notificationAiReasoningEffort")
        or os.environ.get("NOTIFICATION_AI_REASONING_EFFORT")
        or settings.get("notificationAiStandardReasoningEffort")
        or os.environ.get("NOTIFICATION_AI_STANDARD_REASONING_EFFORT")
        or "high"
    ).strip().lower()
    configured_timeout = optional_timeout_seconds(
        settings.get("notificationAiTimeoutSeconds"),
        os.environ.get("NOTIFICATION_AI_TIMEOUT_SECONDS"),
    )
    delivery_deadline = optional_timeout_seconds(
        settings.get("notificationAiDeliveryDeadlineSeconds"),
        os.environ.get("NOTIFICATION_AI_DELIVERY_DEADLINE_SECONDS"),
    )
    configured = [value for value in (configured_timeout, delivery_deadline) if value is not None]
    timeout = min(configured) if configured else None
    try:
        max_prompt_bytes = int(
            settings.get("notificationAiDeepPromptMaxBytes")
            or os.environ.get("NOTIFICATION_AI_DEEP_PROMPT_MAX_BYTES")
            or settings.get("notificationAiQueueMaxPromptBytes")
            or 20 * 1024
        )
    except (TypeError, ValueError):
        max_prompt_bytes = 20 * 1024
    if use_codex:
        runtime_dir = notification_ai_runtime_dir()
        command = codex_process_arguments(reasoning_effort=reasoning_effort, working_directory=runtime_dir)
        if command:
            try:
                maximum = max(1, min(8, int(settings.get("localAiMaxConcurrentProcesses") or os.environ.get("ORBIT_LOCAL_AI_MAX_CONCURRENT") or 2)))
            except (TypeError, ValueError):
                maximum = 2
            try:
                reserved = max(0, min(maximum - 1, int(settings.get("localAiInvestmentReservedProcesses") or os.environ.get("ORBIT_LOCAL_AI_INVESTMENT_RESERVED") or 1)))
            except (TypeError, ValueError):
                reserved = 1
            capacity_wait = optional_timeout_seconds(
                settings.get("notificationAiCapacityWaitSeconds"),
                os.environ.get("NOTIFICATION_AI_CAPACITY_WAIT_SECONDS"),
            )
            primary = CommandNotificationAIReviewer(
                command,
                timeout,
                "Codex AI (" + codex_model_label(reasoning_effort) + ")",
                max_prompt_bytes=max_prompt_bytes,
                command_factory=lambda reasoning_effort="": codex_process_arguments(
                    reasoning_effort=reasoning_effort,
                    working_directory=runtime_dir,
                ),
                settings=settings,
                capacity_lock_dir=data_dir() / "local-ai-capacity",
                capacity_max_concurrent=maximum,
                capacity_reserved_slots=reserved,
                capacity_wait_seconds=capacity_wait,
                runtime_directory=runtime_dir,
            )
            return FallbackNotificationAIReviewer(primary) if allow_local_fallback else primary
    if allow_local_fallback:
        return LocalNotificationAIReviewer()
    return CommandNotificationAIReviewer("", timeout, "Codex AI unavailable", max_prompt_bytes=max_prompt_bytes, settings=settings)
