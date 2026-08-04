"""Asynchronous enrichment for stored news evidence.

Collection stays bounded by network and body-read budgets.  This use case
performs the slower structured Korean summary and English-title translation
after evidence is already visible to the operator.
"""

from datetime import datetime, timezone
from typing import Dict, Iterable, List

from ..domain.data_freshness import age_minutes, parse_datetime
from ..domain.events import ontology_reasoning_requested_event, research_evidence_collected_event
from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence
from ..domain.materiality import evidence_materiality
from ..domain.news_ai_analysis import (
    article_body_quality_needs_refresh,
    article_summary_quality_needs_refresh,
    article_text_parts,
    news_ai_analysis_is_current,
    news_ai_analysis_retryable,
    refreshed_article_summary_quality,
    source_language,
    summary_quality_payload,
)
from ..domain import news_analysis as news_domain


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        value = int(float(str(settings.get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class NewsAnalysisEnrichmentRunner:
    """Reprocess deferred news without delaying provider collection."""

    def __init__(
        self,
        evidence_store,
        analysis_service,
        settings: Dict[str, object],
        event_publisher=None,
        storage_guard=None,
    ):
        self.evidence_store = evidence_store
        self.analysis_service = analysis_service
        self.settings = dict(settings or {})
        self.event_publisher = event_publisher
        self.storage_guard = storage_guard

    def enabled(self) -> bool:
        return truthy(self.settings.get("newsAiAnalysisAsyncEnabled"), True) and bool(self.analysis_service)

    def interval_seconds(self) -> int:
        return int_setting(self.settings, "newsAiAnalysisWorkerIntervalSeconds", 60, 15, 3600)

    def batch_size(self) -> int:
        return int_setting(self.settings, "newsAiAnalysisWorkerBatchSize", 1, 1, 10)

    def local_repair_batch_size(self) -> int:
        return int_setting(self.settings, "newsAiAnalysisLocalRepairBatchSize", 25, 1, 100)

    def scan_limit(self) -> int:
        return int_setting(self.settings, "newsAiAnalysisWorkerScanLimit", 160, 10, 1000)

    def retry_minutes(self) -> int:
        return int_setting(self.settings, "newsAiAnalysisRetryMinutes", 30, 1, 1440)

    def timeout_seconds(self) -> int:
        return int_setting(self.settings, "newsAiAnalysisTimeoutSeconds", 90, 5, 300)

    def max_news_age_minutes(self) -> int:
        return int_setting(self.settings, "newsEvidenceMaxAgeMinutes", 1440 * 30, 5, 1440 * 30)

    def keep_undated_news(self) -> bool:
        return truthy(self.settings.get("newsEvidenceKeepUndated"), False)

    def item_is_fresh(self, item: ResearchEvidence) -> bool:
        parsed = parse_datetime(item.published_at or item.observed_at)
        if not parsed:
            return self.keep_undated_news()
        return age_minutes(parsed.isoformat(), now=datetime.now(timezone.utc)) <= self.max_news_age_minutes()

    def target_for(self, item: ResearchEvidence) -> NewsCollectionTarget:
        payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
        return NewsCollectionTarget(
            symbol=item.symbol,
            name=str(payload.get("name") or payload.get("companyName") or item.symbol),
            market=str(payload.get("market") or ("KOSPI" if str(item.symbol).isdigit() else "NASDAQ")),
            currency=str(payload.get("currency") or ("KRW" if str(item.symbol).isdigit() else "USD")),
            sector=str(payload.get("sector") or ""),
        )

    def should_retry(self, item: ResearchEvidence) -> bool:
        if not isinstance(item, ResearchEvidence) or item.kind != "news":
            return False
        if not self.item_is_fresh(item):
            return False
        payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
        if str(payload.get("relationScope") or "") == "editorial_context":
            return False
        quality_gate = payload.get("qualityGate") if isinstance(payload.get("qualityGate"), dict) else {}
        if str(quality_gate.get("decision") or "") == "exclude":
            return False
        analysis = payload.get("aiAnalysis") if isinstance(payload.get("aiAnalysis"), dict) else {}
        language = str(payload.get("sourceLanguage") or source_language(item.title)).lower()
        translation_status = str(payload.get("translationStatus") or "").lower()
        needs_translation = language == "en" and translation_status != "complete"
        title, body, feed_summary, _read_scope = article_text_parts(item)
        refreshed_quality = summary_quality_payload(
            payload.get("articleSummaryKo") or item.summary,
            " ".join(part for part in [title, body or feed_summary] if part),
            str(payload.get("name") or payload.get("companyName") or item.symbol),
        )
        needs_summary_review = str(refreshed_quality.get("state") or "") in {"blocked", "needs-review"}
        summary_quality_refresh = article_summary_quality_needs_refresh(item)
        analysis_status = str(analysis.get("status") or "").lower()
        retryable_analysis = (
            analysis_status in {"fallback", "error", ""}
            or (analysis_status == "local" and (needs_translation or needs_summary_review))
            or (analysis_status == "deferred" and (needs_translation or needs_summary_review))
            or news_ai_analysis_retryable(item) and (needs_translation or needs_summary_review)
        )
        analysis_outdated = not news_ai_analysis_is_current(item)
        body_quality_repair = article_body_quality_needs_refresh(item)
        if not (
            needs_translation
            or needs_summary_review
            or summary_quality_refresh
            or retryable_analysis
            or analysis_outdated
            or body_quality_repair
        ):
            return False
        last_attempt = parse_datetime(analysis.get("lastExternalAttemptAt"))
        if (
            not body_quality_repair
            and not summary_quality_refresh
            and last_attempt
            and age_minutes(last_attempt.isoformat(), now=datetime.now(timezone.utc)) < self.retry_minutes()
        ):
            return False
        return True

    def priority(self, item: ResearchEvidence) -> tuple:
        payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
        facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
        language = str(payload.get("sourceLanguage") or source_language(item.title)).lower()
        translation_pending = language == "en" and str(payload.get("translationStatus") or "").lower() != "complete"
        states = news_domain.news_state_rank(item.state_payload())
        published = parse_datetime(item.published_at or item.observed_at)
        return (
            *states,
            bool(facts.get("bodyAvailable")),
            translation_pending,
            not article_body_quality_needs_refresh(item),
            published.timestamp() if published else 0.0,
        )

    def deterministic_repair(self, item: ResearchEvidence) -> bool:
        if not news_ai_analysis_is_current(item) or news_ai_analysis_retryable(item):
            return False
        if article_body_quality_needs_refresh(item):
            return True
        return (
            article_summary_quality_needs_refresh(item)
            and str(refreshed_article_summary_quality(item).get("state") or "") == "ready"
        )

    def candidates(self) -> List[ResearchEvidence]:
        rows = list(self.evidence_store.latest(kind="news", limit=self.scan_limit()) or [])
        return sorted((item for item in rows if self.should_retry(item)), key=self.priority, reverse=True)

    def _status_for_candidates(self, candidates) -> Dict[str, object]:
        pending_translation = 0
        for item in candidates:
            payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
            if str(payload.get("sourceLanguage") or source_language(item.title)).lower() == "en" and str(payload.get("translationStatus") or "").lower() != "complete":
                pending_translation += 1
        return {
            "enabled": self.enabled(),
            "intervalSeconds": self.interval_seconds(),
            "batchSize": self.batch_size(),
            "localRepairBatchSize": self.local_repair_batch_size(),
            "retryMinutes": self.retry_minutes(),
            "pendingCount": len(candidates),
            "pendingTranslationCount": pending_translation,
        }

    def status(self) -> Dict[str, object]:
        candidates = self.candidates() if self.enabled() else []
        return self._status_for_candidates(candidates)

    def storage_state(self) -> Dict[str, object]:
        if not callable(self.storage_guard):
            return {"status": "not-configured", "nonEssentialWritesAllowed": True}
        try:
            return dict(self.storage_guard() or {})
        except Exception as error:  # noqa: BLE001 - a guard probe failure should defer optional analysis.
            return {
                "status": "unavailable",
                "nonEssentialWritesAllowed": False,
                "reason": str(error)[:180],
            }

    def _events_for_mutation(self, mutation, processed_count: int) -> List[object]:
        changed_items = list(getattr(mutation, "changed_items", []) or [])
        changed_symbols = list(getattr(mutation, "changed_symbols", []) or [])
        mutation_payload = mutation.to_dict() if hasattr(mutation, "to_dict") else {}
        materiality = [evidence_materiality(item, self.settings).to_dict() for item in changed_items]
        material_items = [item for item, state in zip(changed_items, materiality) if state.get("passed")]
        payload = {
            "source": "news-analysis-enrichment",
            "status": "ok",
            "targetCount": processed_count,
            "fetchedCount": processed_count,
            "savedCount": int(getattr(mutation, "written_count", 0) or 0),
            "changedCount": int(getattr(mutation, "written_count", 0) or 0),
            "symbols": changed_symbols,
            "changedSymbols": changed_symbols,
            "materialChangedCount": len(material_items),
            "materialChangedSymbols": sorted({item.symbol for item in material_items if item.symbol}),
            "changedItems": [item.to_dict() for item in changed_items[:50]],
            "materialChangedItems": [item.to_dict() for item in material_items[:50]],
            "materialityAssessments": materiality,
            "evidenceDeltas": list(mutation_payload.get("evidenceDeltas") or [
                delta.to_dict() if hasattr(delta, "to_dict") else delta
                for delta in (getattr(mutation, "deltas", []) or [])
            ]),
            "factRevisionsBySymbol": dict(
                mutation_payload.get("factRevisionsBySymbol")
                or getattr(mutation, "eligible_set_revisions", {})
                or {}
            ),
            "inferenceChangedSymbols": list(
                mutation_payload.get("inferenceChangedSymbols")
                or getattr(mutation, "inference_changed_symbols", [])
                or []
            ),
            "providers": ["news-ai-analysis"],
        }
        event = research_evidence_collected_event(payload)
        events = [event]
        inference_symbols = list(payload["inferenceChangedSymbols"])
        if inference_symbols:
            events.append(ontology_reasoning_requested_event(
                event,
                "news-analysis-enrichment",
                inference_symbols,
                changed_count=len(inference_symbols),
                observed_count=processed_count,
                fact_types=["ResearchEvidence", "NewsArticleAnalysis"],
                reason="본문 기반 뉴스 요약·번역이 보강되어 TypeDB ABox의 리서치 근거를 갱신합니다.",
                materiality_assessments=materiality,
                fact_revisions_by_symbol=payload["factRevisionsBySymbol"],
                evidence_deltas=payload["evidenceDeltas"],
            ))
        return events

    def run_once(self, limit: int = 0) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", **self.status(), "processedCount": 0, "savedCount": 0}
        storage = self.storage_state()
        if not bool(storage.get("nonEssentialWritesAllowed", True)):
            return {
                "status": "deferred-low-disk",
                "enabled": True,
                "intervalSeconds": self.interval_seconds(),
                "batchSize": self.batch_size(),
                "retryMinutes": self.retry_minutes(),
                "pendingCount": 0,
                "pendingTranslationCount": 0,
                "processedCount": 0,
                "savedCount": 0,
                "storage": storage,
            }
        candidates = self.candidates()
        repair_candidates = [item for item in candidates if self.deterministic_repair(item)]
        repair_selected = repair_candidates[: self.local_repair_batch_size()]
        repair_ids = {item.evidence_id for item in repair_selected}
        repair_candidate_ids = {item.evidence_id for item in repair_candidates}
        model_candidates = [item for item in candidates if item.evidence_id not in repair_candidate_ids]
        model_selected = model_candidates[: max(1, int(limit or self.batch_size()))]
        selected = [*repair_selected, *model_selected]
        updated: List[ResearchEvidence] = []
        failures: List[Dict[str, object]] = []
        translated_count = 0
        now = utc_now_iso()
        for item in selected:
            local_repair = item.evidence_id in repair_ids
            try:
                result = self.analysis_service.analyze_evidence(
                    self.target_for(item),
                    item,
                    external_timeout_seconds=self.timeout_seconds(),
                )
                payload = dict(result.raw_payload or {})
                analysis = dict(payload.get("aiAnalysis") or {})
                if local_repair:
                    analysis["lastLocalRepairAt"] = now
                else:
                    analysis["lastExternalAttemptAt"] = now
                    if str(analysis.get("status") or "").lower() in {"fallback", "error", "deferred"}:
                        analysis["nextRetryAfterMinutes"] = self.retry_minutes()
                    else:
                        analysis["externalCompletedAt"] = now
                payload["aiAnalysis"] = analysis
                result.raw_payload = payload
                if str(payload.get("translationStatus") or "").lower() == "complete":
                    translated_count += 1
                updated.append(result)
            except Exception as error:  # noqa: BLE001 - one article must not block the backlog.
                failures.append({"evidenceId": item.evidence_id, "symbol": item.symbol, "message": str(error)[:180]})

        event_state: Dict[str, object] = {}
        saved = 0
        if updated and self.event_publisher and hasattr(self.evidence_store, "upsert_many_with_events") and hasattr(self.event_publisher, "dispatch_recorded"):
            def event_builder(mutation):
                events = self._events_for_mutation(mutation, len(selected))
                event_state["events"] = events
                event_state["mutation"] = mutation
                return events

            saved, events = self.evidence_store.upsert_many_with_events(updated, event_builder)
            for event in events:
                self.event_publisher.dispatch_recorded(event)
        elif updated:
            saved = self.evidence_store.upsert_many(updated)
            if self.event_publisher:
                mutation = type("Mutation", (), {
                    "changed_items": list(getattr(self.evidence_store, "last_changed_items", []) or []),
                    "changed_symbols": list(getattr(self.evidence_store, "last_changed_symbols", []) or []),
                    "written_count": saved,
                    "deltas": list(getattr(self.evidence_store, "last_evidence_deltas", []) or []),
                    "eligible_set_revisions": dict(getattr(self.evidence_store, "last_eligible_evidence_revisions", {}) or {}),
                })()
                for event in self._events_for_mutation(mutation, len(selected)):
                    if hasattr(self.event_publisher, "publish"):
                        self.event_publisher.publish(event)
                    else:
                        self.event_publisher.handle(event)

        return {
            "status": "ok",
            **self._status_for_candidates(candidates),
            "processedCount": len(selected),
            "localRepairCount": len(repair_selected),
            "modelProcessedCount": len(model_selected),
            "savedCount": saved,
            "translatedCount": translated_count,
            "failedCount": len(failures),
            "failures": failures,
            "processedEvidenceIds": [item.evidence_id for item in selected],
        }
