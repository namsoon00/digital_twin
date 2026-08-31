import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict

from ..domain.model_review import ModelReviewJob, build_model_review_prompt, local_model_review
from .local_ai_process_guard import run_ai_prompt_command
from .settings import ROOT_DIR, data_dir, runtime_settings


ENFORCED_CODEX_MODEL = "gpt-5.6-sol"
ENFORCED_CODEX_REASONING_EFFORT = "max"
VALID_CODEX_REASONING_EFFORTS = {"low", "medium", "high", "max"}
_CODEX_PREFLIGHT_CACHE = {}


class ModelReviewer:
    def review(self, job: ModelReviewJob) -> str:
        raise NotImplementedError


class LocalModelReviewer(ModelReviewer):
    def review(self, job: ModelReviewJob) -> str:
        return local_model_review(job)


class CommandModelReviewer(ModelReviewer):
    def __init__(self, command, timeout_seconds: int = 180, settings: Dict[str, object] = None):
        self.command = command
        self.timeout_seconds = max(30, int(timeout_seconds or 180))
        self.settings = dict(settings or {})

    def review(self, job: ModelReviewJob) -> str:
        prompt = build_model_review_prompt(job)
        completed = run_background_ai_prompt(
            self.command,
            prompt,
            self.timeout_seconds,
            self.settings,
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


def healthy_codex_executable() -> str:
    """Return Codex only when the installed binary can finish local startup."""

    executable = shutil.which("codex")
    if not executable:
        return ""
    try:
        stat = os.stat(executable)
        cache_key = (executable, int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        return ""
    if cache_key in _CODEX_PREFLIGHT_CACHE:
        return executable if _CODEX_PREFLIGHT_CACHE[cache_key] else ""
    try:
        completed = subprocess.run(
            [executable, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
        healthy = bool(completed.returncode == 0 and str(completed.stdout or "").strip())
    except (OSError, subprocess.TimeoutExpired):
        healthy = False
    _CODEX_PREFLIGHT_CACHE.clear()
    _CODEX_PREFLIGHT_CACHE[cache_key] = healthy
    return executable if healthy else ""


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


def background_ai_runtime_dir() -> Path:
    """Return an empty runtime for asynchronous extraction and review jobs."""

    configured = str(os.environ.get("ORBIT_BACKGROUND_AI_RUNTIME_DIR") or "").strip()
    path = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "orbit-alpha-background-ai"
    path.mkdir(parents=True, exist_ok=True)
    return path


def codex_process_arguments(reasoning_effort: str = "", working_directory: Path = None) -> list:
    """Build direct Codex argv without the cross-process guard wrapper."""

    executable = healthy_codex_executable()
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


def background_codex_process_arguments(reasoning_effort: str = "max") -> list:
    return codex_process_arguments(reasoning_effort, background_ai_runtime_dir())


def _bounded_int(value: object, fallback: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return fallback


def run_background_ai_prompt(
    command,
    prompt: str,
    timeout_seconds: int,
    settings: Dict[str, object] = None,
):
    configured = settings or runtime_settings()
    maximum = _bounded_int(
        configured.get("localAiMaxConcurrentProcesses") or os.environ.get("ORBIT_LOCAL_AI_MAX_CONCURRENT") or 2,
        2,
        1,
        8,
    )
    reserved = _bounded_int(
        configured.get("localAiInvestmentReservedProcesses") or os.environ.get("ORBIT_LOCAL_AI_INVESTMENT_RESERVED") or 1,
        1,
        0,
        max(0, maximum - 1),
    )
    wait_seconds = _bounded_int(
        configured.get("notificationAiCapacityWaitSeconds") or os.environ.get("ORBIT_LOCAL_AI_CAPACITY_WAIT_SECONDS") or 300,
        300,
        1,
        900,
    )
    return run_ai_prompt_command(
        command,
        prompt,
        lock_dir=data_dir() / "local-ai-capacity",
        max_concurrent=maximum,
        wait_seconds=wait_seconds,
        lane="background",
        reserved_priority_slots=reserved,
        timeout_seconds=timeout_seconds,
        cwd=background_ai_runtime_dir(),
        env=dict(os.environ),
    )


def codex_command(_requested_model: str = "", reasoning_effort: str = "") -> str:
    """Build a read-only command with the project-wide fixed model policy.

    The argument is retained for compatibility with older callers, but model
    selection is intentionally not caller-configurable. Workload-specific
    reasoning effort is explicit: realtime alert wording is latency-bounded,
    while asynchronous research and review keep the quality-first max policy.
    """

    executable = healthy_codex_executable()
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
        command = background_codex_process_arguments()
        if command:
            return FallbackModelReviewer(CommandModelReviewer(command, timeout, settings))
    return LocalModelReviewer()
