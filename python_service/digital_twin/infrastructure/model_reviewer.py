import os
import shlex
import shutil
import subprocess
from typing import Dict

from ..domain.model_review import ModelReviewJob, build_model_review_prompt, local_model_review
from .settings import ROOT_DIR, runtime_settings


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


def codex_command() -> str:
    executable = shutil.which("codex")
    if not executable:
        return ""
    return " ".join([
        shlex.quote(executable),
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


def reviewer_from_settings(settings: Dict[str, str] = None) -> ModelReviewer:
    settings = settings or runtime_settings()
    command = str(settings.get("modelReviewCommand") or os.environ.get("MODEL_REVIEW_COMMAND") or "").strip()
    use_codex = str(settings.get("modelReviewUseCodex") or os.environ.get("MODEL_REVIEW_USE_CODEX") or "1").strip() != "0"
    timeout = int(settings.get("modelReviewTimeoutSeconds") or os.environ.get("MODEL_REVIEW_TIMEOUT_SECONDS") or 180)
    if command:
        return FallbackModelReviewer(CommandModelReviewer(command, timeout))
    if use_codex:
        command = codex_command()
        if command:
            return FallbackModelReviewer(CommandModelReviewer(command, timeout))
    return LocalModelReviewer()

