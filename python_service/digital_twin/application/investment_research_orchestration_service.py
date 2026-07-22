from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

from ..domain.events import (
    hypothesis_research_completed_event,
    ontology_reasoning_requested_event,
)
from ..domain.investment_brain import InvestmentQuestion, stable_id, utc_now_iso
from ..domain.investment_evidence_governance import (
    ResearchReasoningHandoff,
    ResearchRun,
    governed_evidence,
    normalized_source_trust_state,
    reasoning_handoff_from_context,
)
from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence
from ..domain.data_freshness import parse_datetime
from ..domain.materiality import evidence_materiality


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


def float_setting(settings: Dict[str, object], key: str, fallback: float, lower: float, upper: float) -> float:
    try:
        parsed = float(str((settings or {}).get(key) or fallback))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


class InvestmentResearchOrchestrationService:
    def __init__(
        self,
        evidence_repository,
        research_gateway,
        research_store=None,
        event_publisher=None,
        article_analysis_service=None,
        settings: Dict[str, object] = None,
    ):
        self.evidence_repository = evidence_repository
        self.research_gateway = research_gateway
        self.research_store = research_store
        self.event_publisher = event_publisher
        self.article_analysis_service = article_analysis_service
        self.settings = dict(settings or {})

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentBrainResearchEnabled"), True)

    def max_rounds(self) -> int:
        return int_setting(self.settings, "investmentBrainResearchMaxRounds", 2, 0, 3)

    def evidence_limit(self) -> int:
        return int_setting(self.settings, "investmentBrainResearchEvidenceLimit", 40, 5, 200)

    def minimum_verified_count(self) -> int:
        return int_setting(self.settings, "investmentBrainResearchMinimumVerifiedCount", 2, 1, 10)

    def minimum_source_trust_state(self) -> str:
        configured = str(self.settings.get("investmentBrainResearchMinimumSourceTrustState") or "").strip().lower()
        if configured:
            return normalized_source_trust_state(configured)
        # One-way compatibility for existing local settings.  New settings use
        # the named state and never expose a numeric reliability threshold.
        legacy = self.settings.get("investmentBrainResearchMinimumSourceReliability")
        return normalized_source_trust_state(legacy, "standard")

    def cooldown_minutes(self) -> int:
        return int_setting(self.settings, "investmentBrainResearchCooldownMinutes", 30, 0, 1440)

    def run(
        self,
        question: InvestmentQuestion,
        target: NewsCollectionTarget,
        brain: Dict[str, object],
        account_id: str = "",
        force: bool = False,
        run_id: str = "",
        started_at: str = "",
    ) -> ResearchRun:
        started_at = started_at or utc_now_iso()
        plan = brain.get("researchPlan") if isinstance(brain.get("researchPlan"), dict) else {}
        tasks = [item for item in plan.get("tasks") or [] if isinstance(item, dict)]
        task_ids = [str(item.get("taskId") or "") for item in tasks if str(item.get("taskId") or "")]
        source_types = unique_strings(
            source
            for item in tasks
            for source in (item.get("sourceTypes") or [])
        )
        run_id = run_id or stable_id("research-run", question.question_id, target.normalized_symbol(), started_at)
        reasoning_handoff = reasoning_handoff_from_context(
            run_id,
            account_id or question.account_id,
            target.normalized_symbol(),
            brain,
        )
        if not self.enabled() or self.max_rounds() <= 0:
            return self.persist_run(ResearchRun(
                run_id=run_id,
                question_id=question.question_id,
                account_id=account_id,
                symbol=target.normalized_symbol(),
                status="disabled",
                task_ids=task_ids,
                source_types=source_types,
                reasoning_handoff=reasoning_handoff,
                started_at=started_at,
                completed_at=utc_now_iso(),
            ))

        max_age = min(
            [int(item.get("maxAgeMinutes") or 360) for item in tasks] or [360]
        )
        cached_items = self.latest_evidence(target.normalized_symbol())
        cached_accepted, cached_claims, _cached_rejected = governed_evidence(
            cached_items,
            target,
            max_age,
            self.minimum_source_trust_state(),
        )
        needs_research = force or self.plan_requires_research(brain, tasks)
        if not needs_research:
            return self.persist_run(ResearchRun(
                run_id=run_id,
                question_id=question.question_id,
                account_id=account_id,
                symbol=target.normalized_symbol(),
                status="not-required",
                task_ids=task_ids,
                source_types=source_types,
                reused_evidence_ids=[item.evidence_id for item in cached_accepted],
                verified_claims=cached_claims,
                reasoning_handoff=reasoning_handoff,
                started_at=started_at,
                completed_at=utc_now_iso(),
            ))
        if len(cached_claims) >= self.minimum_verified_count() and not force:
            return self.persist_run(ResearchRun(
                run_id=run_id,
                question_id=question.question_id,
                account_id=account_id,
                symbol=target.normalized_symbol(),
                status="cache-satisfied",
                task_ids=task_ids,
                source_types=source_types,
                reused_evidence_ids=[item.evidence_id for item in cached_accepted],
                verified_claims=cached_claims,
                round_count=0,
                reasoning_handoff=reasoning_handoff,
                started_at=started_at,
                completed_at=utc_now_iso(),
            ))
        cooldown_remaining = self.cooldown_remaining_minutes(account_id, target.normalized_symbol())
        if cooldown_remaining > 0 and not force:
            return self.persist_run(ResearchRun(
                run_id=run_id,
                question_id=question.question_id,
                account_id=account_id,
                symbol=target.normalized_symbol(),
                status="research-cooldown",
                task_ids=task_ids,
                source_types=source_types,
                reused_evidence_ids=[item.evidence_id for item in cached_accepted],
                verified_claims=cached_claims,
                provider_statuses=[{
                    "provider": "research-orchestrator",
                    "status": "cooldown",
                    "remainingMinutes": cooldown_remaining,
                }],
                reasoning_handoff=reasoning_handoff,
                started_at=started_at,
                completed_at=utc_now_iso(),
            ))

        collected: List[ResearchEvidence] = []
        provider_statuses: List[Dict[str, object]] = []
        if self.research_gateway and hasattr(self.research_gateway, "collect_for_target"):
            try:
                collected, provider_statuses = self.research_gateway.collect_for_target(
                    target,
                    source_types=source_types,
                )
            except TypeError:
                collected, provider_statuses = self.research_gateway.collect_for_target(target)
        if self.article_analysis_service and hasattr(self.article_analysis_service, "analyze_many"):
            collected = self.article_analysis_service.analyze_many(target, collected)
        accepted, verified, rejected = governed_evidence(
            collected,
            target,
            max_age,
            self.minimum_source_trust_state(),
        )
        changed_count, reasoning_handoff = self.persist_evidence(
            accepted,
            question,
            target,
            run_id,
            reasoning_handoff,
        )
        status = "evidence-collected" if changed_count else ("verified-no-change" if verified else "evidence-unavailable")
        return self.persist_run(ResearchRun(
            run_id=run_id,
            question_id=question.question_id,
            account_id=account_id,
            symbol=target.normalized_symbol(),
            status=status,
            task_ids=task_ids,
            source_types=source_types,
            reused_evidence_ids=[item.evidence_id for item in cached_accepted],
            verified_claims=verified,
            rejected_claims=rejected,
            provider_statuses=provider_statuses,
            round_count=1,
            changed_evidence_count=changed_count,
            reasoning_handoff=reasoning_handoff,
            started_at=started_at,
            completed_at=utc_now_iso(),
        ))

    def plan_requires_research(self, brain: Dict[str, object], tasks: Iterable[Dict[str, object]]) -> bool:
        epistemic = brain.get("epistemicState") if isinstance(brain.get("epistemicState"), dict) else {}
        if str(epistemic.get("status") or "") == "contested":
            return True
        hypotheses = ((brain.get("hypothesisSet") or {}).get("hypotheses") if isinstance(brain.get("hypothesisSet"), dict) else []) or []
        if any(str(item.get("verificationStatus") or "") in {"requires-research", "counterfactual-challenge"} for item in hypotheses if isinstance(item, dict)):
            return True
        return any(str(item.get("status") or "") == "blocked-by-data" for item in tasks or [])

    def latest_evidence(self, symbol: str) -> List[ResearchEvidence]:
        if not self.evidence_repository or not hasattr(self.evidence_repository, "latest"):
            return []
        try:
            return list(self.evidence_repository.latest(symbol=symbol, limit=self.evidence_limit()) or [])
        except Exception:  # noqa: BLE001 - a new bounded query may still proceed.
            return []

    def cooldown_remaining_minutes(self, account_id: str, symbol: str) -> int:
        cooldown = self.cooldown_minutes()
        if cooldown <= 0 or not self.research_store or not hasattr(self.research_store, "list_runs"):
            return 0
        try:
            rows = self.research_store.list_runs(account_id, symbol, 100)
        except Exception:  # noqa: BLE001 - unavailable audit history must not disable research.
            return 0
        now = datetime.now(timezone.utc)
        for item in rows or []:
            if not isinstance(item, dict) or str(item.get("status") or "") in {"queued", "processing", "not-required", "cache-satisfied", "research-cooldown"}:
                continue
            stamp = parse_datetime(str(item.get("startedAt") or item.get("completedAt") or ""))
            if not stamp:
                continue
            elapsed = max(0.0, (now - stamp.astimezone(timezone.utc)).total_seconds() / 60.0)
            return max(0, int(round(cooldown - elapsed)))
        return 0

    def persist_evidence(
        self,
        items: List[ResearchEvidence],
        question: InvestmentQuestion,
        target: NewsCollectionTarget,
        run_id: str,
        reasoning_handoff: ResearchReasoningHandoff = None,
    ) -> Tuple[int, ResearchReasoningHandoff]:
        handoff = reasoning_handoff or ResearchReasoningHandoff()
        if not items or not self.evidence_repository:
            return 0, handoff

        persisted_handoff = handoff

        def events(saved: int, changed_symbols: List[str], changed_items: List[ResearchEvidence]):
            nonlocal persisted_handoff
            if not saved:
                return []
            changed_evidence_ids = [item.evidence_id for item in changed_items if item.evidence_id]
            persisted_handoff = handoff.requested(changed_evidence_ids)
            materiality = [evidence_materiality(item, self.settings).to_dict() for item in changed_items]
            completed = hypothesis_research_completed_event({
                "runId": run_id,
                "questionId": question.question_id,
                "accountId": question.account_id,
                "symbol": target.normalized_symbol(),
                "status": "evidence-collected",
                "changedEvidenceCount": saved,
                "verifiedClaims": changed_evidence_ids,
                "changedEvidenceIds": changed_evidence_ids,
                "reasoningHandoff": persisted_handoff.to_dict(),
            })
            reasoning = ontology_reasoning_requested_event(
                completed,
                "hypothesis-research-update",
                symbols=changed_symbols or [target.normalized_symbol()],
                changed_count=saved,
                observed_count=len(items),
                fact_types=["ResearchEvidence", "VerifiedClaim", "VerificationRun"],
                reason="가설 검증에서 확보한 근거를 전체 ABox 스냅샷에 반영하고 TypeDB 네이티브 추론을 다시 실행합니다.",
                materiality_assessments=materiality,
            )
            return [completed, reasoning]

        if (
            hasattr(self.evidence_repository, "upsert_many_with_events")
            and self.event_publisher
            and hasattr(self.event_publisher, "dispatch_recorded")
        ):
            saved, recorded = self.evidence_repository.upsert_many_with_events(items, events)
            for event in recorded:
                self.event_publisher.dispatch_recorded(event)
            return int(saved or 0), persisted_handoff
        saved = int(self.evidence_repository.upsert_many(items) or 0)
        changed_items = list(getattr(self.evidence_repository, "last_changed_items", []) or items)
        if self.event_publisher and saved:
            for event in events(saved, [target.normalized_symbol()], changed_items):
                if hasattr(self.event_publisher, "publish"):
                    self.event_publisher.publish(event)
        if saved and persisted_handoff is handoff:
            persisted_handoff = handoff.requested([item.evidence_id for item in changed_items if item.evidence_id])
        return saved, persisted_handoff

    def persist_run(self, run: ResearchRun) -> ResearchRun:
        if self.research_store and hasattr(self.research_store, "save_run"):
            return self.research_store.save_run(run)
        return run

    def mark_reasoning_refreshed(
        self,
        run: ResearchRun,
        refreshed: bool,
        reasoning_handoff: ResearchReasoningHandoff = None,
    ) -> ResearchRun:
        handoff = reasoning_handoff or run.reasoning_handoff
        confirmed = bool(refreshed) and (not handoff.request_id or handoff.applied())
        status = run.status
        if confirmed and status not in {"disabled", "not-required"}:
            status = "reasoning-refreshed"
        elif not confirmed and run.changed_evidence_count > 0:
            status = "reasoning-refresh-failed"
        updated = replace(
            run,
            status=status,
            reasoning_refreshed=confirmed,
            reasoning_handoff=handoff,
            completed_at=utc_now_iso(),
        )
        return self.persist_run(updated)

    def enqueue(
        self,
        question: InvestmentQuestion,
        target: NewsCollectionTarget,
        brain: Dict[str, object],
        account_id: str = "",
        notification_event_id: str = "",
    ) -> ResearchRun:
        plan = brain.get("researchPlan") if isinstance(brain.get("researchPlan"), dict) else {}
        tasks = [item for item in plan.get("tasks") or [] if isinstance(item, dict)]
        task_ids = [str(item.get("taskId") or "") for item in tasks if str(item.get("taskId") or "")]
        source_types = unique_strings(source for item in tasks for source in (item.get("sourceTypes") or []))
        status = "queued" if self.enabled() and self.plan_requires_research(brain, tasks) else "not-required"
        run_id = stable_id("research-run-queued", question.question_id, target.normalized_symbol())
        run = ResearchRun(
            run_id=run_id,
            question_id=question.question_id,
            account_id=account_id,
            symbol=target.normalized_symbol(),
            status=status,
            task_ids=task_ids,
            source_types=source_types,
            reasoning_handoff=reasoning_handoff_from_context(
                run_id,
                account_id or question.account_id,
                target.normalized_symbol(),
                brain,
            ),
            request_context={
                "question": question.to_dict(),
                "target": {
                    "symbol": target.normalized_symbol(),
                    "name": target.name,
                    "market": target.market,
                    "currency": target.currency,
                    "sector": target.sector,
                },
                "brain": dict(brain or {}),
                "notificationEventId": str(notification_event_id or ""),
            },
            completed_at=utc_now_iso() if status == "not-required" else "",
        )
        return self.persist_run(run)

    def execute_queued(self, queued: ResearchRun) -> ResearchRun:
        request = dict(queued.request_context or {})
        question_payload = request.get("question") if isinstance(request.get("question"), dict) else {}
        target_payload = request.get("target") if isinstance(request.get("target"), dict) else {}
        brain = request.get("brain") if isinstance(request.get("brain"), dict) else {}
        question = InvestmentQuestion(
            question_id=str(question_payload.get("questionId") or queued.question_id),
            text=str(question_payload.get("text") or "투자 판단 근거를 비동기로 검증한다."),
            intent=str(question_payload.get("intent") or "investment-decision"),
            subject_symbol=str(question_payload.get("subjectSymbol") or queued.symbol),
            subject_name=str(question_payload.get("subjectName") or target_payload.get("name") or queued.symbol),
            horizon=str(question_payload.get("horizon") or "multi-horizon"),
            account_id=str(question_payload.get("accountId") or queued.account_id),
            asked_at=str(question_payload.get("askedAt") or queued.started_at),
            source=str(question_payload.get("source") or "notification"),
        )
        target = NewsCollectionTarget(
            symbol=str(target_payload.get("symbol") or queued.symbol),
            name=str(target_payload.get("name") or queued.symbol),
            market=str(target_payload.get("market") or ""),
            currency=str(target_payload.get("currency") or ""),
            sector=str(target_payload.get("sector") or ""),
        )
        completed = self.run(
            question,
            target,
            brain,
            account_id=queued.account_id,
            run_id=queued.run_id,
            started_at=queued.started_at,
        )
        if queued.request_context and not completed.request_context:
            completed = replace(completed, request_context=dict(queued.request_context))
            self.persist_run(completed)
        return completed


class InvestmentResearchQueueRunner:
    def __init__(self, store, orchestrator: InvestmentResearchOrchestrationService):
        self.store = store
        self.orchestrator = orchestrator
        self.last_results: List[Dict[str, object]] = []

    def run_once(self, limit: int = 5) -> Dict[str, object]:
        self.last_results = []
        runs = self.store.claim_queued_runs(limit) if self.store and hasattr(self.store, "claim_queued_runs") else []
        for queued in runs:
            try:
                completed = self.orchestrator.execute_queued(queued)
                if completed.changed_evidence_count > 0:
                    completed = replace(completed, status="reasoning-queued", reasoning_refreshed=False)
                    self.orchestrator.persist_run(completed)
                self.last_results.append(completed.to_dict())
            except Exception as error:  # noqa: BLE001 - one research task must not stop the queue.
                failed = replace(
                    queued,
                    status="error",
                    completed_at=utc_now_iso(),
                    provider_statuses=[{"provider": "research-worker", "status": "error", "reason": str(error)[:180]}],
                )
                self.orchestrator.persist_run(failed)
                self.last_results.append(failed.to_dict())
        return {
            "status": "ok",
            "processedCount": len(runs),
            "queuedCount": self.store.queued_count() if self.store and hasattr(self.store, "queued_count") else 0,
            "results": self.last_results,
        }

    def status(self) -> Dict[str, object]:
        return {
            "status": "ready",
            "queuedCount": self.store.queued_count() if self.store and hasattr(self.store, "queued_count") else 0,
            "lastResults": self.last_results[-20:],
        }


def unique_strings(values: Iterable[object]) -> List[str]:
    result = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
