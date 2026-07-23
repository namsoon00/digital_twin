import json
import os
import subprocess
from typing import Dict

from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence
from ..domain.news_ai_analysis import build_news_ai_analysis_prompt, local_news_ai_analysis, normalize_ai_analysis
from .model_reviewer import codex_command
from .settings import ROOT_DIR, runtime_settings


class NewsAiAnalyzer:
    def analyze(self, target: NewsCollectionTarget, evidence: ResearchEvidence) -> Dict[str, object]:
        raise NotImplementedError


class LocalNewsAiAnalyzer(NewsAiAnalyzer):
    def analyze(self, target: NewsCollectionTarget, evidence: ResearchEvidence) -> Dict[str, object]:
        return local_news_ai_analysis(target, evidence).to_dict()

    def analyze_with_timeout(self, target: NewsCollectionTarget, evidence: ResearchEvidence, _timeout_seconds: int) -> Dict[str, object]:
        return self.analyze(target, evidence)


def first_json_object(text: object) -> Dict[str, object]:
    source = str(text or "").strip()
    if not source:
        return {}
    try:
        parsed = json.loads(source)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    start = source.find("{")
    end = source.rfind("}")
    if start < 0 or end <= start:
        return {}
    parsed = json.loads(source[start : end + 1])
    return parsed if isinstance(parsed, dict) else {}


class CommandNewsAiAnalyzer(NewsAiAnalyzer):
    def __init__(self, command: str, timeout_seconds: int = 90, model_name: str = "External article AI"):
        self.command = str(command or "").strip()
        self.timeout_seconds = max(1, int(timeout_seconds or 90))
        self.model_name = str(model_name or "External article AI").strip()

    def analyze(self, target: NewsCollectionTarget, evidence: ResearchEvidence) -> Dict[str, object]:
        return self.analyze_with_timeout(target, evidence, self.timeout_seconds)

    def analyze_with_timeout(self, target: NewsCollectionTarget, evidence: ResearchEvidence, timeout_seconds: int) -> Dict[str, object]:
        prompt = build_news_ai_analysis_prompt(target, evidence)
        completed = subprocess.run(
            self.command,
            input=prompt,
            text=True,
            shell=True,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(1, min(self.timeout_seconds, int(timeout_seconds or self.timeout_seconds))),
            env=dict(os.environ),
        )
        output = completed.stdout.strip()
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or output or "news AI analysis command failed").strip())
        parsed = first_json_object(output)
        if not parsed:
            raise RuntimeError("news AI analysis command returned no JSON object")
        fallback = local_news_ai_analysis(target, evidence)
        parsed.setdefault("model", self.model_name)
        return normalize_ai_analysis(parsed, fallback).to_dict()


class FallbackNewsAiAnalyzer(NewsAiAnalyzer):
    def __init__(self, primary: NewsAiAnalyzer, fallback: NewsAiAnalyzer = None):
        self.primary = primary
        self.fallback = fallback or LocalNewsAiAnalyzer()

    def analyze(self, target: NewsCollectionTarget, evidence: ResearchEvidence) -> Dict[str, object]:
        return self.analyze_with_timeout(target, evidence, 0)

    def analyze_with_timeout(self, target: NewsCollectionTarget, evidence: ResearchEvidence, timeout_seconds: int) -> Dict[str, object]:
        try:
            if timeout_seconds and hasattr(self.primary, "analyze_with_timeout"):
                return self.primary.analyze_with_timeout(target, evidence, timeout_seconds)
            return self.primary.analyze(target, evidence)
        except Exception as error:  # noqa: BLE001 - collection must continue with local analysis.
            result = self.fallback.analyze(target, evidence)
            limitations = list(result.get("reasoningLimitations") or [])
            limitations.append("외부 AI 분석 실패로 로컬 기사 분석 사용: " + str(error)[:160])
            result["status"] = "fallback"
            result["reasoningLimitations"] = limitations
            return result


def news_ai_analyzer_from_settings(settings: Dict[str, str] = None) -> NewsAiAnalyzer:
    configured = settings or runtime_settings()
    command = str(configured.get("newsAiAnalysisCommand") or os.environ.get("NEWS_AI_ANALYSIS_COMMAND") or "").strip()
    use_codex = str(configured.get("newsAiAnalysisUseCodex") or os.environ.get("NEWS_AI_ANALYSIS_USE_CODEX") or "1").strip() not in {"0", "false", "no", "off"}
    timeout = int(configured.get("newsAiAnalysisTimeoutSeconds") or os.environ.get("NEWS_AI_ANALYSIS_TIMEOUT_SECONDS") or 90)
    if command:
        return FallbackNewsAiAnalyzer(CommandNewsAiAnalyzer(command, timeout, "External article AI"))
    if use_codex:
        model = str(
            configured.get("newsAiAnalysisModel")
            or os.environ.get("NEWS_AI_ANALYSIS_MODEL")
            or configured.get("notificationAiModel")
            or "gpt-5.5"
        ).strip()
        command = codex_command(model)
        if command:
            return FallbackNewsAiAnalyzer(CommandNewsAiAnalyzer(command, timeout, "Codex AI (" + model + ")"))
    return LocalNewsAiAnalyzer()
