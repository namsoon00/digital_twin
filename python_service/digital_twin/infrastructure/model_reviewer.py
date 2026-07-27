import os
import shlex
import shutil
import subprocess
from typing import Dict

from ..domain.model_review import ModelReviewJob, build_model_review_prompt, local_model_review
from .settings import ROOT_DIR, runtime_settings


ENFORCED_CODEX_MODEL = "gpt-5.6-sol"
ENFORCED_CODEX_REASONING_EFFORT = "max"


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


def codex_model_label() -> str:
    return "GPT-5.6 Sol · max"


def codex_cli_arguments() -> list:
    """Return the non-negotiable model policy for every app-owned Codex call."""

    return [
        "--model",
        ENFORCED_CODEX_MODEL,
        "--config",
        'model_reasoning_effort="' + ENFORCED_CODEX_REASONING_EFFORT + '"',
    ]


def codex_command(_requested_model: str = "") -> str:
    """Build a read-only command with the project-wide quality-first model policy.

    The argument is retained for compatibility with older callers, but model
    selection is intentionally not caller-configurable.  A mixed model policy
    made alert, article, disclosure, and review output difficult to audit.
    """

    executable = shutil.which("codex")
    if not executable:
        return ""
    parts = [
        shlex.quote(executable),
    ]
    for argument in codex_cli_arguments():
        parts.append(shlex.quote(argument))
    parts.extend([
        "-a",
        "never",
        "--sandbox",
        "read-only",
        "--cd",
        shlex.quote(str(ROOT_DIR)),
        "exec",
        "--skip-git-repo-check",
        "-",
    ])
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
