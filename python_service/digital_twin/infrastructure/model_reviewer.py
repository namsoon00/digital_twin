import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict

from ..domain.model_review import ModelReviewJob, build_model_review_prompt, local_model_review
from .settings import ROOT_DIR, data_dir, runtime_settings


ENFORCED_CODEX_MODEL = "gpt-5.6-sol"
ENFORCED_CODEX_REASONING_EFFORT = "max"
VALID_CODEX_REASONING_EFFORTS = {"low", "medium", "high", "max"}


class ModelReviewer:
    def review(self, job: ModelReviewJob) -> str:
        raise NotImplementedError


class LocalModelReviewer(ModelReviewer):
    def review(self, job: ModelReviewJob) -> str:
        return local_model_review(job)


class CommandModelReviewer(ModelReviewer):
    def __init__(self, command: str, timeout_seconds: int = 180):
        self.command = command
        self.timeout_seconds = max(30, int(timeout_seconds or 180))

    def review(self, job: ModelReviewJob) -> str:
        prompt = build_model_review_prompt(job)
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
            raise RuntimeError((completed.stderr or output or "model review command failed").strip())
        if not output:
            raise RuntimeError("model review command returned empty output")
        return output


class FallbackModelReviewer(ModelReviewer):
    def __init__(self, primary: ModelReviewer, fallback: ModelReviewer = None):
        self.primary = primary
        self.fallback = fallback or LocalModelReviewer()

    def review(self, job: ModelReviewJob) -> str:
        try:
            return self.primary.review(job)
        except Exception as error:  # noqa: BLE001 - fallback keeps async review service alive.
            return self.fallback.review(job) + "\n- LLM 상태: 외부 분석 실패로 로컬 진단 사용 (" + str(error)[:160] + ")"


def normalized_codex_reasoning_effort(value: object = "") -> str:
    effort = str(value or ENFORCED_CODEX_REASONING_EFFORT).strip().lower()
    return effort if effort in VALID_CODEX_REASONING_EFFORTS else ENFORCED_CODEX_REASONING_EFFORT


def codex_model_label(reasoning_effort: str = "") -> str:
    return "GPT-5.6 Sol · " + normalized_codex_reasoning_effort(reasoning_effort)


def codex_cli_arguments(reasoning_effort: str = "") -> list:
    """Return the fixed model and the bounded effort selected for a workload."""

    return [
        "--model",
        ENFORCED_CODEX_MODEL,
        "--config",
        'model_reasoning_effort="' + normalized_codex_reasoning_effort(reasoning_effort) + '"',
    ]


def notification_ai_runtime_dir() -> Path:
    """Return an empty runtime outside the repository for investment judgement."""

    configured = str(os.environ.get("ORBIT_NOTIFICATION_AI_RUNTIME_DIR") or "").strip()
    path = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "orbit-alpha-notification-ai"
    path.mkdir(parents=True, exist_ok=True)
    return path


def codex_process_arguments(reasoning_effort: str = "", working_directory: Path = None) -> list:
    """Build direct Codex argv without the cross-process guard wrapper."""

    executable = shutil.which("codex")
    if not executable:
        return []
    runtime_dir = Path(working_directory or ROOT_DIR)
    return [
        executable,
        *codex_cli_arguments(reasoning_effort),
        "-a",
        "never",
        "--sandbox",
        "read-only",
        "--cd",
        str(runtime_dir),
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "-",
    ]


def codex_command(_requested_model: str = "", reasoning_effort: str = "") -> str:
    """Build a read-only command with the project-wide fixed model policy.

    The argument is retained for compatibility with older callers, but model
    selection is intentionally not caller-configurable. Workload-specific
    reasoning effort is explicit: realtime alert wording is latency-bounded,
    while asynchronous research and review keep the quality-first max policy.
    """

    executable = shutil.which("codex")
    if not executable:
        return ""
    try:
        maximum = max(1, min(8, int(os.environ.get("ORBIT_LOCAL_AI_MAX_CONCURRENT") or 2)))
    except (TypeError, ValueError):
        maximum = 2
    try:
        wait_seconds = max(1, min(900, int(os.environ.get("ORBIT_LOCAL_AI_CAPACITY_WAIT_SECONDS") or 300)))
    except (TypeError, ValueError):
        wait_seconds = 300
    guard = Path(__file__).with_name("local_ai_process_guard.py")
    try:
        reserved = max(0, min(maximum - 1, int(os.environ.get("ORBIT_LOCAL_AI_INVESTMENT_RESERVED") or 1)))
    except (TypeError, ValueError):
        reserved = 1
    parts = [
        shlex.quote(sys.executable),
        shlex.quote(str(guard)),
        "--lock-dir",
        shlex.quote(str(data_dir() / "local-ai-capacity")),
        "--max-concurrent",
        str(maximum),
        "--wait-seconds",
        str(wait_seconds),
        "--lane",
        "background",
        "--reserved-priority-slots",
        str(reserved),
        "--",
    ]
    for argument in codex_process_arguments(reasoning_effort, ROOT_DIR):
        parts.append(shlex.quote(argument))
    return " ".join(parts)


def reviewer_from_settings(settings: Dict[str, str] = None) -> ModelReviewer:
    settings = settings or runtime_settings()
    use_codex = str(settings.get("modelReviewUseCodex") or os.environ.get("MODEL_REVIEW_USE_CODEX") or "1").strip() != "0"
    timeout = int(settings.get("modelReviewTimeoutSeconds") or os.environ.get("MODEL_REVIEW_TIMEOUT_SECONDS") or 180)
    if use_codex:
        command = codex_command()
        if command:
            return FallbackModelReviewer(CommandModelReviewer(command, timeout))
    return LocalModelReviewer()
