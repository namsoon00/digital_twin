from typing import Dict, Iterable, List

from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence
from ..domain.news_ai_analysis import (
    apply_news_ai_analysis,
    local_news_ai_analysis,
    news_ai_analysis_is_current,
)


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


class NewsAiAnalysisService:
    def __init__(self, analyzer=None, settings: Dict[str, object] = None):
        self.analyzer = analyzer
        self.settings = dict(settings or {})

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

    def analyze_many(self, target: NewsCollectionTarget, items: Iterable[ResearchEvidence]) -> List[ResearchEvidence]:
        return [self.analyze_evidence(target, item) for item in items or []]
