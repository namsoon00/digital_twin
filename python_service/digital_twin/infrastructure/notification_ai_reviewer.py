import os
import subprocess
from typing import Dict

from ..domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from ..domain.notification_ai_gate_validation import (
    build_notification_ai_gate_prompt,
    local_validated_ai_response,
    validated_response_from_text,
)
from .model_reviewer import codex_command
from .settings import ROOT_DIR, runtime_settings


class NotificationAIReviewer:
    def review(self, context: Dict[str, object]) -> NotificationAIValidatedResponse:
        raise NotImplementedError


class LocalNotificationAIReviewer(NotificationAIReviewer):
    def review(self, context: Dict[str, object]) -> NotificationAIValidatedResponse:
        return local_validated_ai_response(context, source="local")


class CommandNotificationAIReviewer(NotificationAIReviewer):
    def __init__(self, command: str, timeout_seconds: int = 120, source: str = "AI"):
        self.command = str(command or "").strip()
        self.timeout_seconds = max(30, int(timeout_seconds or 120))
        self.source = source

    def review(self, context: Dict[str, object]) -> NotificationAIValidatedResponse:
        if not self.command:
            raise RuntimeError("notification AI command is not configured")
        prompt = build_notification_ai_gate_prompt(context)
        completed = subprocess.run(
            self.command,
            input=prompt,
            text=True,
            shell=True,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout_seconds,
            env=dict(os.environ),
        )
        output = completed.stdout.strip()
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or output or "notification AI command failed").strip())
        if not output:
            raise RuntimeError("notification AI command returned empty output")
        return validated_response_from_text(context, output, source=self.source)


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
            fallback.validation_warnings.append("AI 응답 실패로 로컬 검증 의견을 사용했습니다: " + str(error)[:140])
            return fallback


def notification_ai_reviewer_from_settings(settings: Dict[str, str] = None) -> NotificationAIReviewer:
    settings = settings or runtime_settings()
    command = str(settings.get("notificationAiCommand") or os.environ.get("NOTIFICATION_AI_COMMAND") or "").strip()
    use_codex = str(settings.get("notificationAiUseCodex") or os.environ.get("NOTIFICATION_AI_USE_CODEX") or "1").strip() != "0"
    timeout = int(settings.get("notificationAiTimeoutSeconds") or os.environ.get("NOTIFICATION_AI_TIMEOUT_SECONDS") or 120)
    if command:
        return FallbackNotificationAIReviewer(CommandNotificationAIReviewer(command, timeout, "AI 명령"))
    if use_codex:
        command = codex_command()
        if command:
            return FallbackNotificationAIReviewer(CommandNotificationAIReviewer(command, timeout, "Codex AI"))
    return LocalNotificationAIReviewer()
