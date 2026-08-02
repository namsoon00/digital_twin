import time
from typing import Callable, Dict, Iterable, List

from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence
from ..domain.market_data import number
from ..domain.news_ai_analysis import (
    apply_news_ai_analysis,
    article_body_quality_needs_refresh,
    local_news_ai_analysis,
    news_ai_analysis_is_current,
    news_ai_analysis_retryable,
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

    def begin_run(self) -> Dict[str, int]:
        """Reset the budget named ``MaxPerRun`` before each collection cycle."""
        self._external_analysis_used = 0
        return {"externalAnalysisUsed": self._external_analysis_used}

    def enabled(self) -> bool:
        return truthy(self.settings.get("newsAiAnalysisEnabled"), True)

    def inline_timeout_seconds(self) -> int:
        return int_setting(self.settings, "newsAiAnalysisInlineTimeoutSeconds", 8, 1, 120)

    def inline_enabled(self) -> bool:
        # Collection must remain a bounded I/O operation.  The dedicated
        # enrichment worker performs the slower model call by default.
        # Keep direct service callers that have not adopted the runtime
        # setting backward compatible; runtime_settings always supplies the
        # new key with the production default of false.
        if "newsAiAnalysisInlineEnabled" not in self.settings:
            return True
        return truthy(self.settings.get("newsAiAnalysisInlineEnabled"), False)

    def analysis_needs_work(self, evidence: ResearchEvidence) -> bool:
        if evidence.kind != "news":
            return False
        return (
            not news_ai_analysis_is_current(evidence)
            or news_ai_analysis_retryable(evidence)
            or article_body_quality_needs_refresh(evidence)
        )

    def analyze_evidence(
        self,
        target: NewsCollectionTarget,
        evidence: ResearchEvidence,
        external_timeout_seconds: int = 0,
    ) -> ResearchEvidence:
        if not self.enabled() or not self.analysis_needs_work(evidence):
            return evidence
        # This is a deterministic migration of stale extractor output, not a
        # reason to consume an external AI-analysis call.
        if (
            article_body_quality_needs_refresh(evidence)
            and news_ai_analysis_is_current(evidence)
            and not news_ai_analysis_retryable(evidence)
        ):
            return self.local_analyze_evidence(
                target,
                evidence,
                "본문 경계 정제 기준이 변경되어 기사 본문 품질을 다시 검증했습니다.",
                status="local",
            )
        try:
            if self.analyzer and hasattr(self.analyzer, "analyze"):
                if external_timeout_seconds and hasattr(self.analyzer, "analyze_with_timeout"):
                    analysis_payload = self.analyzer.analyze_with_timeout(target, evidence, external_timeout_seconds)
                elif external_timeout_seconds:
                    raise TimeoutError("외부 AI 분석기가 수집 실행 시간 제한 계약을 지원하지 않습니다.")
                else:
                    analysis_payload = self.analyzer.analyze(target, evidence)
            elif callable(self.analyzer):
                if external_timeout_seconds:
                    raise TimeoutError("외부 AI 분석기가 수집 실행 시간 제한 계약을 지원하지 않습니다.")
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

    def local_analyze_evidence(
        self,
        target: NewsCollectionTarget,
        evidence: ResearchEvidence,
        reason: str = "",
        status: str = "",
    ) -> ResearchEvidence:
        if not self.enabled() or not self.analysis_needs_work(evidence):
            return evidence
        analysis_payload = local_news_ai_analysis(target, evidence).to_dict()
        if reason:
            analysis_payload["status"] = status or "fallback"
            analysis_payload["reasoningLimitations"] = list(analysis_payload.get("reasoningLimitations") or []) + [reason]
        return apply_news_ai_analysis(evidence, analysis_payload)

    def analyze_many(
        self,
        target: NewsCollectionTarget,
        items: Iterable[ResearchEvidence],
        deadline_monotonic: float = 0.0,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> List[ResearchEvidence]:
        rows = list(items or [])
        if not rows or not self.enabled():
            return rows
        if not self.inline_enabled():
            return [
                self.local_analyze_evidence(
                    target,
                    item,
                    "뉴스 수집과 분리된 AI 분석 대기열에서 본문 기반 요약·번역을 보강합니다.",
                    status="deferred",
                ) if self.analysis_needs_work(item) else item
                for item in rows
            ]
        max_per_target = int_setting(self.settings, "newsAiAnalysisMaxPerTarget", len(rows), 0, 1000)
        max_per_run = int_setting(self.settings, "newsAiAnalysisMaxPerRun", 10000, 0, 10000)
        remaining_run_budget = max(0, max_per_run - self._external_analysis_used)
        max_external = min(max_per_target, remaining_run_budget)
        candidates = [item for item in rows if self.analysis_needs_work(item)]
        selected_ids = {
            id(item)
            for item in sorted(candidates, key=analysis_priority, reverse=True)[:max_external]
        }
        skipped_reason = "외부 AI 기사 분석 우선순위 제한으로 로컬 기사 분석 사용"
        analyzed: List[ResearchEvidence] = []
        for item in rows:
            if not self.analysis_needs_work(item):
                analyzed.append(item)
                continue
            if id(item) in selected_ids:
                remaining_seconds = self.inline_timeout_seconds()
                if deadline_monotonic:
                    remaining_seconds = min(remaining_seconds, int(deadline_monotonic - monotonic_fn()))
                if remaining_seconds <= 0:
                    analyzed.append(self.local_analyze_evidence(
                        target,
                        item,
                        "뉴스 수집 실행 예산을 넘어 외부 AI 분석을 다음 주기로 미뤘습니다.",
                    ))
                    continue
                analyzed.append(self.analyze_evidence(target, item, external_timeout_seconds=max(1, remaining_seconds)))
                self._external_analysis_used += 1
            else:
                analyzed.append(self.local_analyze_evidence(target, item, skipped_reason, status="deferred"))
        return analyzed
