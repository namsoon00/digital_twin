from typing import Dict, Iterable, List

from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence
from ..domain.news_ai_analysis import (
    apply_news_ai_analysis,
    local_news_ai_analysis,
    news_ai_analysis_is_current,
)
from ..domain.news_analysis import news_state_rank, news_state_payload


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 0, upper: int = 1000) -> int:
    raw = settings.get(key)
    if raw in (None, ""):
        parsed = fallback
    else:
        parsed = number(raw)
    return max(lower, min(upper, int(parsed)))


def analysis_priority(item: ResearchEvidence):
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
    states = news_state_payload({**payload, **facts})
    body_available = bool(facts.get("bodyAvailable"))
    actionable_state = {
        "blocked": 0,
        "conditional": 1,
        "ready": 2,
    }.get(states["validationState"], 0)
    return (
        *news_state_rank(states),
        actionable_state,
        body_available,
        str(item.published_at or item.observed_at or ""),
    )


class NewsAiAnalysisService:
    def __init__(self, analyzer=None, settings: Dict[str, object] = None):
        self.analyzer = analyzer
        self.settings = dict(settings or {})
        self._external_analysis_used = 0

    def enabled(self) -> bool:
        return truthy(self.settings.get("newsAiAnalysisEnabled"), True)

    def analyze_evidence(self, target: NewsCollectionTarget, evidence: ResearchEvidence) -> ResearchEvidence:
        if not self.enabled() or evidence.kind != "news":
            return evidence
        if news_ai_analysis_is_current(evidence):
            return evidence
        try:
            if self.analyzer and hasattr(self.analyzer, "analyze"):
                analysis_payload = self.analyzer.analyze(target, evidence)
            elif callable(self.analyzer):
                analysis_payload = self.analyzer(target, evidence)
            else:
                analysis_payload = local_news_ai_analysis(target, evidence).to_dict()
        except Exception as error:  # noqa: BLE001 - article analysis must not block collection.
            analysis_payload = local_news_ai_analysis(target, evidence).to_dict()
            analysis_payload["status"] = "fallback"
            analysis_payload["reasoningLimitations"] = list(analysis_payload.get("reasoningLimitations") or []) + [
                "외부 AI 분석 실패로 로컬 기사 분석 사용: " + str(error)[:160]
            ]
        return apply_news_ai_analysis(evidence, analysis_payload)

    def local_analyze_evidence(self, target: NewsCollectionTarget, evidence: ResearchEvidence, reason: str = "") -> ResearchEvidence:
        if not self.enabled() or evidence.kind != "news" or news_ai_analysis_is_current(evidence):
            return evidence
        analysis_payload = local_news_ai_analysis(target, evidence).to_dict()
        if reason:
            analysis_payload["status"] = "fallback"
            analysis_payload["reasoningLimitations"] = list(analysis_payload.get("reasoningLimitations") or []) + [reason]
        return apply_news_ai_analysis(evidence, analysis_payload)

    def analyze_many(self, target: NewsCollectionTarget, items: Iterable[ResearchEvidence]) -> List[ResearchEvidence]:
        rows = list(items or [])
        if not rows or not self.enabled():
            return rows
        max_per_target = int_setting(self.settings, "newsAiAnalysisMaxPerTarget", len(rows), 0, 1000)
        max_per_run = int_setting(self.settings, "newsAiAnalysisMaxPerRun", 10000, 0, 10000)
        remaining_run_budget = max(0, max_per_run - self._external_analysis_used)
        max_external = min(max_per_target, remaining_run_budget)
        if max_external >= len(rows):
            analyzed = []
            for item in rows:
                analyzed.append(self.analyze_evidence(target, item))
                if getattr(item, "kind", "") == "news":
                    self._external_analysis_used += 1
            return analyzed
        selected_ids = {
            id(item)
            for item in sorted(rows, key=analysis_priority, reverse=True)[:max_external]
            if getattr(item, "kind", "") == "news"
        }
        skipped_reason = "외부 AI 기사 분석 우선순위 제한으로 로컬 기사 분석 사용"
        analyzed: List[ResearchEvidence] = []
        for item in rows:
            if id(item) in selected_ids:
                analyzed.append(self.analyze_evidence(target, item))
                self._external_analysis_used += 1
            else:
                analyzed.append(self.local_analyze_evidence(target, item, skipped_reason))
        return analyzed
