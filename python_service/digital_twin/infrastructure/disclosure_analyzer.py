import os
import subprocess
from typing import Dict

from ..domain.disclosure_analysis import (
    DisclosureAnalysisResult,
    build_disclosure_analysis_prompt,
    local_disclosure_analysis,
    normalize_disclosure_analysis_output,
)
from .model_reviewer import codex_command
from .settings import ROOT_DIR, runtime_settings


class DisclosureAnalyzer:
    def analyze(self, context: Dict[str, object]) -> DisclosureAnalysisResult:
        raise NotImplementedError


class LocalDisclosureAnalyzer(DisclosureAnalyzer):
    def analyze(self, context: Dict[str, object]) -> DisclosureAnalysisResult:
        return local_disclosure_analysis(context)


class CommandDisclosureAnalyzer(DisclosureAnalyzer):
    def __init__(self, command: str, timeout_seconds: int = 90, source: str = "AI 분석"):
        self.command = command
        self.timeout_seconds = max(15, int(timeout_seconds or 90))
        self.source = source

    def analyze(self, context: Dict[str, object]) -> DisclosureAnalysisResult:
        completed = subprocess.run(
            self.command,
            input=build_disclosure_analysis_prompt(context),
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
            raise RuntimeError((completed.stderr or output or "disclosure analysis command failed").strip())
        if not output:
            raise RuntimeError("disclosure analysis command returned empty output")
        return normalize_disclosure_analysis_output(output, local_disclosure_analysis(context), self.source)


class FallbackDisclosureAnalyzer(DisclosureAnalyzer):
    def __init__(self, primary: DisclosureAnalyzer, fallback: DisclosureAnalyzer = None):
        self.primary = primary
        self.fallback = fallback or LocalDisclosureAnalyzer()

    def analyze(self, context: Dict[str, object]) -> DisclosureAnalysisResult:
        try:
            return self.primary.analyze(context)
        except Exception:  # noqa: BLE001 - fallback keeps notification delivery alive.
            return local_disclosure_analysis(context, "로컬 fallback")


def int_setting(settings: Dict[str, str], key: str, default: int) -> int:
    try:
        return int(str(settings.get(key) or default).strip())
    except (TypeError, ValueError):
        return default


def enabled_setting(settings: Dict[str, str], key: str, default: str = "1") -> bool:
    return str(settings.get(key) or default).strip() != "0"


def disclosure_analyzer_from_settings(settings: Dict[str, str] = None) -> DisclosureAnalyzer:
    settings = settings or runtime_settings()
    timeout = int_setting(settings, "dartDisclosureAiTimeoutSeconds", int_setting(settings, "modelReviewTimeoutSeconds", 90))
    command = str(settings.get("dartDisclosureAiCommand") or settings.get("modelReviewCommand") or "").strip()
    if command:
        return FallbackDisclosureAnalyzer(CommandDisclosureAnalyzer(command, timeout, "공시 AI 명령"))
    if enabled_setting(settings, "dartDisclosureAiUseCodex", str(settings.get("modelReviewUseCodex") or "1")):
        command = codex_command()
        if command:
            return FallbackDisclosureAnalyzer(CommandDisclosureAnalyzer(command, timeout, "Codex AI"))
    return LocalDisclosureAnalyzer()
