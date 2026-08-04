import os
import signal
import subprocess
from typing import Dict

from ..domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from ..domain.notification_ai_gate_validation import (
    build_notification_ai_gate_prompt,
    local_validated_ai_response,
    validated_response_from_text,
)
from .model_reviewer import codex_command, codex_model_label
from .settings import ROOT_DIR, runtime_settings


class NotificationAIReviewer:
    def review(self, context: Dict[str, object]) -> NotificationAIValidatedResponse:
        raise NotImplementedError


class LocalNotificationAIReviewer(NotificationAIReviewer):
    def review(self, context: Dict[str, object]) -> NotificationAIValidatedResponse:
        return local_validated_ai_response(context, source="local")


class CommandNotificationAIReviewer(NotificationAIReviewer):
    def __init__(
        self,
        command: str,
        timeout_seconds: int = 300,
        source: str = "AI",
        max_prompt_bytes: int = 48 * 1024,
    ):
        self.command = str(command or "").strip()
        self.timeout_seconds = max(5, int(timeout_seconds or 300))
        self.source = source
        self.max_prompt_bytes = max(24 * 1024, min(256 * 1024, int(max_prompt_bytes or 48 * 1024)))
        self.last_prompt_bytes = 0
        self.process = None

    def review(self, context: Dict[str, object]) -> NotificationAIValidatedResponse:
        if not self.command:
            raise RuntimeError("notification AI command is not configured")
        prompt = build_notification_ai_gate_prompt(context, max_prompt_bytes=self.max_prompt_bytes)
        self.last_prompt_bytes = len(prompt.encode("utf-8"))
        process_kwargs = {
            "stdin": subprocess.PIPE,
            "text": True,
            "shell": True,
            "cwd": str(ROOT_DIR),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": dict(os.environ),
        }
        if os.name != "nt":
            process_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            self.command,
            **process_kwargs,
        )
        self.process = process
        try:
            stdout, stderr = process.communicate(input=prompt, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as error:
            self.terminate_process(process, force=False)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.terminate_process(process, force=True)
                process.wait(timeout=2)
            raise TimeoutError("notification AI command exceeded " + str(self.timeout_seconds) + " seconds") from error
        finally:
            if self.process is process:
                self.process = None
        output = str(stdout or "").strip()
        if process.returncode != 0:
            raise RuntimeError((stderr or output or "notification AI command failed").strip())
        if not output:
            raise RuntimeError("notification AI command returned empty output")
        return validated_response_from_text(context, output, source=self.source)

    def stop(self) -> None:
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
        or "max"
    ).strip().lower()
    try:
        configured_timeout = int(
            settings.get("notificationAiTimeoutSeconds")
            or os.environ.get("NOTIFICATION_AI_TIMEOUT_SECONDS")
            or 300
        )
    except (TypeError, ValueError):
        configured_timeout = 300
    try:
        delivery_deadline = int(
            settings.get("notificationAiDeliveryDeadlineSeconds")
            or os.environ.get("NOTIFICATION_AI_DELIVERY_DEADLINE_SECONDS")
            or 300
        )
    except (TypeError, ValueError):
        delivery_deadline = 300
    timeout = max(5, min(max(5, configured_timeout), max(5, delivery_deadline)))
    try:
        max_prompt_bytes = int(
            settings.get("notificationAiQueueMaxPromptBytes")
            or os.environ.get("NOTIFICATION_AI_QUEUE_MAX_PROMPT_BYTES")
            or 48 * 1024
        )
    except (TypeError, ValueError):
        max_prompt_bytes = 48 * 1024
    if use_codex:
        command = codex_command(reasoning_effort=reasoning_effort)
        if command:
            primary = CommandNotificationAIReviewer(
                command,
                timeout,
                "Codex AI (" + codex_model_label(reasoning_effort) + ")",
                max_prompt_bytes=max_prompt_bytes,
            )
            return FallbackNotificationAIReviewer(primary) if allow_local_fallback else primary
    if allow_local_fallback:
        return LocalNotificationAIReviewer()
    return CommandNotificationAIReviewer("", timeout, "Codex AI unavailable", max_prompt_bytes=max_prompt_bytes)
