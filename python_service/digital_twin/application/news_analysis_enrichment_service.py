"""Asynchronous enrichment for stored news evidence.

Collection stays bounded by network and body-read budgets.  This use case
performs the slower structured Korean summary and English-title translation
after evidence is already visible to the operator.
"""

import copy
from datetime import datetime, timezone
import hashlib
import os
import socket
from typing import Dict, Iterable, List
import uuid

from ..domain.data_freshness import age_minutes, parse_datetime
from ..domain.events import news_article_analyzed_event, ontology_reasoning_requested_event, research_evidence_collected_event
from ..domain.evidence_delta import evidence_story_key
from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence
from ..domain.materiality import evidence_materiality
from ..domain.news_ai_analysis import (
    NEWS_AI_ANALYSIS_VERSION,
    article_body_quality_needs_refresh,
    article_summary_quality_needs_refresh,
    article_text_parts,
    news_ai_analysis_is_current,
    news_ai_analysis_retryable,
    refreshed_article_summary_quality,
    source_language,
    summary_quality_payload,
)
from ..news_intelligence.domain.article import article_source_revision
from ..domain.prompt_evidence_admission import assess_prompt_evidence
from ..domain import news_analysis as news_domain
from ..news_intelligence.application.analyze_article import annotate_evidence_eligibility


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
        self.worker_id = (
            "news-analysis:"
            + socket.gethostname()
            + ":"
            + str(os.getpid())
            + ":"
            + uuid.uuid4().hex[:8]
        )[:191]

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
        completed_analysis = bool(
            analysis_status in {"complete", "ok", "success", "verified"}
            and news_ai_analysis_is_current(item)
            and not needs_translation
        )
        retryable_analysis = (
            analysis_status in {"fallback", "error", ""}
            or analysis_status in {"local", "deferred"}
            or news_ai_analysis_retryable(item) and (needs_translation or needs_summary_review)
        )
        analysis_outdated = not news_ai_analysis_is_current(item)
        body_quality_repair = article_body_quality_needs_refresh(item)
        if not (
            needs_translation
            or (needs_summary_review and not completed_analysis)
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
        governance = payload.get("evidenceGovernance") if isinstance(payload.get("evidenceGovernance"), dict) else {}
        audience = str(payload.get("collectionAudience") or "").strip().lower()
        audience_rank = 2 if audience == "holding" else 1 if audience == "watchlist" else 0
        published = parse_datetime(item.published_at or item.observed_at)
        return (
            audience_rank,
            bool(governance.get("investmentJudgmentEligible")),
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
        ordered = sorted((item for item in rows if self.should_retry(item)), key=self.priority, reverse=True)
        selected = []
        seen = set()
        for item in ordered:
            story_key = evidence_story_key(item) or item.evidence_id
            if story_key in seen:
                continue
            seen.add(story_key)
            selected.append(item)
        self._last_candidate_scan = {
            "scannedCount": len(rows),
            "retryableCount": len(ordered),
            "deduplicatedCount": max(0, len(ordered) - len(selected)),
        }
        return selected

    def durable_queue_enabled(self) -> bool:
        return all(callable(getattr(self.evidence_store, name, None)) for name in (
            "enqueue_news_analysis_work",
            "claim_news_analysis_work",
            "finish_news_analysis_work",
        ))

    def queue_priority(self, item: ResearchEvidence) -> int:
        values = self.priority(item)
        ranks = []
        for value in values[2:6]:
            try:
                ranks.append(max(0, int(value)))
            except (TypeError, ValueError):
                ranks.append(0)
        while len(ranks) < 4:
            ranks.append(0)
        published_at = parse_datetime(item.published_at or item.observed_at)
        age_hours = max(
            0,
            int((datetime.now(timezone.utc) - published_at).total_seconds() // 3600),
        ) if published_at else 1999
        score = (
            max(0, min(2, int(values[0] or 0))) * 180000
            + (300000 if bool(values[1]) else 0)
            + ranks[0] * 80000
            + ranks[1] * 40000
            + ranks[2] * 20000
            + ranks[3] * 10000
            + (8000 if len(values) > 6 and bool(values[6]) else 0)
            + (4000 if len(values) > 7 and bool(values[7]) else 0)
            + (2000 if len(values) > 8 and bool(values[8]) else 0)
            + max(0, 1999 - min(1999, age_hours))
        )
        return min(1000000, score)

    def enqueue_candidates(self, candidates: Iterable[ResearchEvidence]) -> int:
        if not self.durable_queue_enabled():
            return 0
        jobs = []
        for item in candidates or []:
            work_class = "local" if self.deterministic_repair(item) else "model"
            jobs.append({
                "evidenceId": item.evidence_id,
                "subjectRevision": self.work_revision(item, work_class),
                "workClass": work_class,
                "priority": self.queue_priority(item),
            })
        return int(self.evidence_store.enqueue_news_analysis_work(jobs) or 0)

    @staticmethod
    def work_revision(item: ResearchEvidence, work_class: str) -> str:
        payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
        source_revision = str(payload.get("articleSourceRevision") or article_source_revision(item)).strip()
        revision_source = ":".join([
            source_revision,
            NEWS_AI_ANALYSIS_VERSION,
            str(work_class or "model"),
        ])
        return "news-analysis:" + hashlib.sha256(
            revision_source.encode("utf-8")
        ).hexdigest()[:32]

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
            "candidateScan": dict(getattr(self, "_last_candidate_scan", {}) or {}),
        }

    def status(self) -> Dict[str, object]:
        candidates = self.candidates() if self.enabled() else []
        result = self._status_for_candidates(candidates)
        status_loader = getattr(self.evidence_store, "news_analysis_work_status", None)
        if callable(status_loader):
            try:
                result["durableQueue"] = dict(status_loader() or {})
            except Exception as error:  # noqa: BLE001 - status remains available from evidence scan.
                result["durableQueue"] = {"durable": True, "status": "error", "reason": str(error)[:180]}
        work_loader = getattr(self.evidence_store, "news_analysis_work_items", None)
        if callable(work_loader):
            try:
                work_items = dict(work_loader([item.evidence_id for item in candidates]) or {})
                missing = 0
                completed_retryable = 0
                revision_mismatch = 0
                represented = 0
                for item in candidates:
                    job = work_items.get(item.evidence_id) or {}
                    if not job:
                        missing += 1
                        continue
                    if str(job.get("subjectRevision") or "") != self.work_revision(
                        item,
                        str(job.get("workClass") or "model"),
                    ):
                        revision_mismatch += 1
                        continue
                    if str(job.get("workState") or "") == "completed":
                        completed_retryable += 1
                        continue
                    represented += 1
                inconsistent = missing + completed_retryable + revision_mismatch
                result["queueIntegrity"] = {
                    "status": "degraded" if inconsistent else "healthy",
                    "candidateCount": len(candidates),
                    "representedCandidateCount": represented,
                    "missingWorkCount": missing,
                    "completedButRetryableCount": completed_retryable,
                    "revisionMismatchCount": revision_mismatch,
                    "inconsistentCount": inconsistent,
                }
            except Exception as error:  # noqa: BLE001 - diagnostics must not block status.
                result["queueIntegrity"] = {"status": "error", "reason": str(error)[:180]}
        enrichment_loader = getattr(self.evidence_store, "news_enrichment_status", None)
        if callable(enrichment_loader):
            try:
                result["enrichmentRevisions"] = dict(enrichment_loader() or {})
            except Exception as error:  # noqa: BLE001 - diagnostics must not block status.
                result["enrichmentRevisions"] = {"status": "error", "reason": str(error)[:180]}
        return result

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
        alert_items = [
            item for item in changed_items
            if assess_prompt_evidence(
                item.raw_payload,
                kind=item.kind,
                published_at=item.published_at,
                observed_at=item.observed_at,
            ).alert_eligible
        ]
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
            "alertEligibleCount": len(alert_items),
            "alertEligibleSymbols": sorted({item.symbol for item in alert_items if item.symbol}),
            "changedItems": [item.to_dict() for item in changed_items[:50]],
            "materialChangedItems": [item.to_dict() for item in material_items[:50]],
            "alertEligibleItems": [item.to_dict() for item in alert_items[:50]],
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
        news_event = news_article_analyzed_event(payload)
        if int(news_event.payload.get("materialChangedCount") or 0):
            events.append(news_event)
        inference_symbols = list(payload["inferenceChangedSymbols"])
        if inference_symbols:
            events.append(ontology_reasoning_requested_event(
                event,
                "news-analysis-enrichment",
                inference_symbols,
                changed_count=len(inference_symbols),
                observed_count=processed_count,
                fact_types=["ResearchEvidence", "NewsArticleAnalysis"],
                fact_types_by_symbol={
                    symbol: ["ResearchEvidence", "NewsArticleAnalysis"]
                    for symbol in inference_symbols
                },
                changed_fields_by_symbol={
                    symbol: ["external.researchEvidence"]
                    for symbol in inference_symbols
                },
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
        durable_queue = self.durable_queue_enabled()
        enqueued_count = self.enqueue_candidates(candidates) if durable_queue else 0
        selected_jobs: Dict[str, Dict[str, object]] = {}
        stale_jobs: List[Dict[str, object]] = []
        if durable_queue:
            lease_seconds = max(120, min(1800, self.timeout_seconds() * 3))
            claimed = [
                *self.evidence_store.claim_news_analysis_work(
                    self.worker_id,
                    "local",
                    self.local_repair_batch_size(),
                    lease_seconds,
                ),
                *self.evidence_store.claim_news_analysis_work(
                    self.worker_id,
                    "model",
                    max(1, int(limit or self.batch_size())),
                    lease_seconds,
                ),
            ]
            selected = []
            for job in claimed:
                item = self.evidence_store.get(job.get("evidenceId"))
                if not item or not self.should_retry(item):
                    stale_jobs.append(job)
                    continue
                selected.append(item)
                selected_jobs[item.evidence_id] = dict(job)
            if stale_jobs:
                self.evidence_store.finish_news_analysis_work(
                    stale_jobs,
                    self.worker_id,
                )
            repair_ids = {
                evidence_id
                for evidence_id, job in selected_jobs.items()
                if str(job.get("workClass") or "") == "local"
            }
            repair_selected = [item for item in selected if item.evidence_id in repair_ids]
            model_selected = [item for item in selected if item.evidence_id not in repair_ids]
        else:
            repair_candidates = [item for item in candidates if self.deterministic_repair(item)]
            repair_selected = repair_candidates[: self.local_repair_batch_size()]
            repair_ids = {item.evidence_id for item in repair_selected}
            repair_candidate_ids = {item.evidence_id for item in repair_candidates}
            model_candidates = [item for item in candidates if item.evidence_id not in repair_candidate_ids]
            model_selected = model_candidates[: max(1, int(limit or self.batch_size()))]
            selected = [*repair_selected, *model_selected]
        updated: List[ResearchEvidence] = []
        failures: List[Dict[str, object]] = []
        retry_job_ids = set()
        stale_result_ids = set()
        translated_count = 0
        now = utc_now_iso()
        for item in selected:
            local_repair = item.evidence_id in repair_ids
            try:
                result = self.analysis_service.analyze_evidence(
                    self.target_for(item),
                    copy.deepcopy(item),
                    external_timeout_seconds=self.timeout_seconds(),
                )
                result = annotate_evidence_eligibility(
                    result,
                    self.settings.get("researchClaimSourceRegistry") or "",
                )
                payload = dict(result.raw_payload or {})
                analysis = dict(payload.get("aiAnalysis") or {})
                if local_repair:
                    analysis["lastLocalRepairAt"] = now
                else:
                    analysis["lastExternalAttemptAt"] = now
                    if str(analysis.get("status") or "").lower() in {"fallback", "error", "deferred"}:
                        analysis["nextRetryAfterMinutes"] = self.retry_minutes()
                        retry_job_ids.add(item.evidence_id)
                    else:
                        analysis["externalCompletedAt"] = now
                payload["aiAnalysis"] = analysis
                result.raw_payload = payload
                if durable_queue:
                    job = selected_jobs.get(item.evidence_id) or {}
                    current = self.evidence_store.get(item.evidence_id)
                    expected_revision = str(job.get("subjectRevision") or "")
                    current_revision = self.work_revision(
                        current,
                        str(job.get("workClass") or "model"),
                    ) if current else ""
                    if not expected_revision or current_revision != expected_revision:
                        stale_result_ids.add(item.evidence_id)
                        continue
                if str(payload.get("translationStatus") or "").lower() == "complete":
                    translated_count += 1
                updated.append(result)
            except Exception as error:  # noqa: BLE001 - one article must not block the backlog.
                failures.append({"evidenceId": item.evidence_id, "symbol": item.symbol, "message": str(error)[:180]})
                retry_job_ids.add(item.evidence_id)

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

        queue_completed_count = 0
        queue_retry_count = 0
        queue_stale_completed_count = 0
        if durable_queue and selected_jobs:
            retry_jobs = [
                selected_jobs[evidence_id]
                for evidence_id in sorted(retry_job_ids)
                if evidence_id in selected_jobs
            ]
            completed_jobs = [
                job
                for evidence_id, job in selected_jobs.items()
                if evidence_id not in retry_job_ids and evidence_id not in stale_result_ids
            ]
            stale_result_jobs = [
                selected_jobs[evidence_id]
                for evidence_id in sorted(stale_result_ids)
                if evidence_id in selected_jobs
            ]
            if completed_jobs:
                queue_completed_count = self.evidence_store.finish_news_analysis_work(
                    completed_jobs,
                    self.worker_id,
                )
            if stale_result_jobs:
                queue_stale_completed_count += self.evidence_store.finish_news_analysis_work(
                    stale_result_jobs,
                    self.worker_id,
                )
            if retry_jobs:
                queue_retry_count = self.evidence_store.finish_news_analysis_work(
                    retry_jobs,
                    self.worker_id,
                    retry_minutes=self.retry_minutes(),
                    error="; ".join(item.get("message") or "analysis-retry-required" for item in failures)[:1000],
                )

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
            "durableQueueEnabled": durable_queue,
            "queueEnqueuedCount": enqueued_count,
            "queueCompletedCount": queue_completed_count,
            "queueRetryCount": queue_retry_count,
            "queueStaleCompletedCount": len(stale_jobs) + queue_stale_completed_count,
        }
