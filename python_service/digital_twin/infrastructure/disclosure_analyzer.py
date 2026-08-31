from typing import Dict

from ..domain.disclosure_analysis import (
    DisclosureAnalysisResult,
    build_disclosure_analysis_prompt,
    disclosure_analysis_ready,
    local_disclosure_analysis,
    normalize_disclosure_analysis_output,
)
from .model_reviewer import (
    background_codex_process_arguments,
    codex_model_label,
    run_background_ai_prompt,
)
from .settings import runtime_settings


class DisclosureAnalyzer:
    def analyze(self, context: Dict[str, object]) -> DisclosureAnalysisResult:
        raise NotImplementedError


class LocalDisclosureAnalyzer(DisclosureAnalyzer):
    def analyze(self, context: Dict[str, object]) -> DisclosureAnalysisResult:
        return local_disclosure_analysis(context)


class CommandDisclosureAnalyzer(DisclosureAnalyzer):
    def __init__(self, command, timeout_seconds: int = 90, source: str = "AI 분석", settings=None):
        self.command = command
        self.timeout_seconds = max(15, int(timeout_seconds or 90))
        self.source = source
        self.settings = dict(settings or {})

    def analyze(self, context: Dict[str, object]) -> DisclosureAnalysisResult:
        if not disclosure_analysis_ready(context):
            return local_disclosure_analysis(context, "메타데이터 전용")
        completed = run_background_ai_prompt(
            self.command,
            build_disclosure_analysis_prompt(context),
            self.timeout_seconds,
            self.settings,
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
    if enabled_setting(settings, "dartDisclosureAiUseCodex", str(settings.get("modelReviewUseCodex") or "1")):
        command = background_codex_process_arguments("medium")
        if command:
            return FallbackDisclosureAnalyzer(CommandDisclosureAnalyzer(command, timeout, "Codex AI (" + codex_model_label() + ")", settings))
    return LocalDisclosureAnalyzer()
