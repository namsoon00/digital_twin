from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

from digital_twin.modules.news_intelligence.domain.integration_events import hypothesis_research_completed_event
from digital_twin.modules.reasoning.contracts import ontology_reasoning_requested_event
from digital_twin.modules.decisions.contracts import InvestmentQuestion, stable_id, utc_now_iso
from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import HypothesisResearchBrief, ResearchReasoningHandoff, ResearchRun, claim_policy, claim_quality_summary, governed_evidence, hypothesis_research_brief_from_brain, normalized_source_trust_state, reasoning_handoff_from_context
from digital_twin.modules.news_intelligence.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.modules.market_data.contracts import parse_datetime
from digital_twin.modules.news_intelligence.domain.materiality import evidence_materiality
from digital_twin.modules.news_intelligence.domain.research_progress import all_tasks_addressed, assess_tasks, audit_retain_until, evidence_packets, fingerprint, progress_plan, task_contract


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        value = (settings or {}).get(key)
        parsed = int(float(str(fallback if value in (None, "") else value)))
    except (TypeError, ValueError, OverflowError):
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
        hypothesis_research_planner=None,
        settings: Dict[str, object] = None,
    ):
        self.evidence_repository = evidence_repository
        self.research_gateway = research_gateway
        self.research_store = research_store
        self.event_publisher = event_publisher
        self.article_analysis_service = article_analysis_service
        self.hypothesis_research_planner = hypothesis_research_planner
        self.settings = dict(settings or {})

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentBrainResearchEnabled"), True)

    def max_rounds(self) -> int:
        return int_setting(self.settings, "investmentBrainResearchMaxRounds", 2, 0, 3)

    def evidence_limit(self) -> int:
        return int_setting(self.settings, "investmentBrainResearchEvidenceLimit", 40, 5, 200)

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

    def research_claim_policy(self) -> Dict[str, object]:
        return claim_policy(self.settings)

    def source_types_with_verification(self, source_types: Iterable[str]) -> List[str]:
        """Request official evidence in the same bounded run as news claims."""
        normalized = unique_strings(source_types)
        policy = self.research_claim_policy()
        news_requested = any(item in {"news", "news-full-text", "article"} for item in normalized)
        if policy.get("officialVerificationEnabled") and news_requested:
            normalized = unique_strings([*normalized, "official-filing"])
        return normalized

    def run(
        self,
        question: InvestmentQuestion,
        target: NewsCollectionTarget,
        brain: Dict[str, object],
        account_id: str = "",
        force: bool = False,
        run_id: str = "",
        started_at: str = "",
        request_context: Dict[str, object] = None,
    ) -> ResearchRun:
        started_at = started_at or utc_now_iso()
        account_id = account_id or question.account_id
        plan = deepcopy(brain.get("researchPlan") or {})
        plan.setdefault("planId", stable_id("research-plan", question.question_id))
        plan.setdefault("questionId", question.question_id)
        run_id = run_id or stable_id("research-run", question.question_id, target.normalized_symbol(), started_at)
        handoff = reasoning_handoff_from_context(run_id, account_id, target.normalized_symbol(), brain)
        brief = hypothesis_research_brief_from_brain(brain)
        history, statuses, assessments = [], [], []
        cached_accepted, verified, rejected, corpus = [], [], [], []
        round_count, changed_count = 0, 0
        retain_until = audit_retain_until(question.horizon, utc_now_iso())
        round_budget = min(self.max_rounds(), int_setting(plan, "maxRounds", self.max_rounds(), 0, 3))

        def tasks():
            return [row for row in plan.get("tasks") or [] if isinstance(row, dict)]

        def sources(rows):
            return self.source_types_with_verification(unique_strings(
                source for row in rows for source in row.get("sourceTypes") or []
            ))

        def save(status, stop_reason=""):
            # Keep reviewed source receipts independently of provider-cache
            # retention and preserve the original queued request on checkpoints.
            executed = progress_plan(plan, assessments)
            executed.pop("taskReviews", None)
            executed["stopReason"] = stop_reason
            executed["status"] = "addressed" if all_tasks_addressed(assessments) else "unresolved"
            return self.persist_run(ResearchRun(
                run_id=run_id, question_id=question.question_id, account_id=account_id,
                symbol=target.normalized_symbol(), status=status,
                task_ids=[str(row.get("taskId") or "") for row in tasks()],
                source_types=sources(tasks()),
                reused_evidence_ids=[item.evidence_id for item in cached_accepted],
                verified_claims=verified, rejected_claims=rejected,
                claim_quality=claim_quality_summary(corpus), provider_statuses=statuses,
                round_count=round_count, changed_evidence_count=changed_count,
                reasoning_handoff=handoff, hypothesis_research_brief=brief,
                executed_plan=executed, task_assessments=assessments,
                round_history=list(history), stop_reason=stop_reason,
                request_context=deepcopy(request_context or {}),
                audit_retain_until=retain_until if history else "",
                started_at=started_at, completed_at="" if status == "processing" else utc_now_iso(),
            ))

        if not self.enabled() or round_budget <= 0:
            return save("disabled", "research-disabled")
        cached_items = self.latest_evidence(target.normalized_symbol())
        corpus_by_id = {item.evidence_id: item for item in cached_items if isinstance(item, ResearchEvidence)}

        def govern():
            # A short-lived task must not remove evidence valid for a longer
            # task. Task-specific freshness is checked by assess_tasks below.
            max_age = max([int(row.get("maxAgeMinutes") or 360) for row in tasks()] or [360])
            return governed_evidence(
                list(corpus_by_id.values()), target, max_age,
                self.minimum_source_trust_state(), policy=self.research_claim_policy(),
            )

        cached_accepted, verified, rejected = govern()
        corpus = list(corpus_by_id.values())
        packets = evidence_packets(cached_accepted, verified)
        assessments = assess_tasks(tasks(), packets, target.normalized_symbol(), utc_now_iso(), plan.get("taskReviews"))
        needs_research = force or question.source == "user" or self.plan_requires_research(brain, tasks())
        if not needs_research:
            return save("not-required", "not-required")
        if all_tasks_addressed(assessments) and not force:
            return save("cache-satisfied", "requirements-met")
        cooldown = self.cooldown_remaining_minutes(account_id, target.normalized_symbol())
        if cooldown > 0 and not force:
            statuses.append({"provider": "research-orchestrator", "status": "cooldown", "remainingMinutes": cooldown})
            return save("research-cooldown", "cooldown")

        attempted_requests = set()
        collection_ids = set()
        stop_reason = "round-budget-exhausted"
        # There is one review before collection and one after each bounded
        # collection round. A follow-up query must differ from attempted work.
        for review_round in range(round_budget + 1):
            assessed_at = utc_now_iso()
            accepted, verified, rejected = govern()
            packets = evidence_packets(accepted, verified)
            assessments = assess_tasks(tasks(), packets, target.normalized_symbol(), assessed_at, plan.get("taskReviews"))
            planning_brain = {**brain, "researchPlan": progress_plan(plan, assessments, packets)}
            plan, brief = self.plan_collection_work(planning_brain, question, target, account_id, brief)
            assessments = assess_tasks(tasks(), packets, target.normalized_symbol(), assessed_at, plan.get("taskReviews"))
            history.append({
                "round": review_round, "assessedAt": assessed_at,
                "planningAudit": dict(plan.get("planningAudit") or {}),
                "taskAssessments": assessments, "evidencePackets": packets,
            })
            save("processing")
            if all_tasks_addressed(assessments) and (not force or round_count):
                stop_reason = "requirements-met"
                break
            if review_round >= round_budget:
                break
            by_id = {row["taskId"]: row for row in assessments}
            pending = [row for row in tasks() if by_id.get(str(row.get("taskId") or ""), {}).get("status") != "addressed"]
            if not pending:
                if not (force and round_count == 0):
                    stop_reason = "no-executable-plan"
                    break
                pending = tasks()
            if not pending or not sources(pending):
                stop_reason = "no-executable-plan"
                break
            # Do not refetch already-attempted tasks just because a new task
            # was added. Completed tasks remain in the immutable plan/audit.
            pending = [row for row in pending if fingerprint(task_contract(row)) not in attempted_requests]
            if not pending:
                stop_reason = "no-new-research-path"
                break
            if not self.research_gateway or not hasattr(self.research_gateway, "collect_for_target"):
                stop_reason = "collector-unavailable"
                break
            attempted_requests.update(fingerprint(task_contract(row)) for row in pending)
            query_terms = unique_strings(term for row in pending for term in row.get("queryTerms") or [])[:6]
            research_target = replace(target, research_query_terms=query_terms)
            collected, provider_statuses = self.collect(research_target, sources(pending), pending)
            if self.article_analysis_service and hasattr(self.article_analysis_service, "analyze_many"):
                collected = self.article_analysis_service.analyze_many(target, collected)
            round_count += 1
            statuses.extend({**row, "round": round_count} for row in provider_statuses if isinstance(row, dict))
            for item in collected or []:
                if isinstance(item, ResearchEvidence) and item.evidence_id:
                    collection_ids.add(item.evidence_id)
                    # Freshly collected revisions supersede cached payloads
                    # with the same ID, never the reverse.
                    corpus_by_id[item.evidence_id] = item
            # Bound the entire run, not just each provider response. Record a
            # truncation explicitly; absence is never fabricated as coverage.
            if len(corpus_by_id) > self.evidence_limit():
                ordered = sorted(corpus_by_id.values(), key=lambda item: (item.observed_at, item.evidence_id), reverse=True)
                corpus_by_id = {item.evidence_id: item for item in ordered[:self.evidence_limit()]}
                statuses.append({"provider": "research-orchestrator", "status": "evidence-budget-truncated", "round": round_count})
            corpus = list(corpus_by_id.values())

        if round_count == 0:
            return save("cache-satisfied" if stop_reason == "requirements-met" else "verified-no-change" if verified else "evidence-unavailable", stop_reason)
        accepted, verified, rejected = govern()
        corpus = list(corpus_by_id.values())
        lifecycle_updates = [item for item in corpus if item.evidence_id not in collection_ids and any(
            str(claim.get("state") or "") in {"superseded", "conflicted"}
            for claim in (((item.raw_payload or {}).get("claimLedger") or {}).get("claims") or [])
            if isinstance(claim, dict)
        )]
        persistable = corpus if self.research_claim_policy().get("strictInvestmentEligibility") else accepted
        persistable_by_id = {item.evidence_id: item for item in persistable + lifecycle_updates}
        changed_count, handoff = self.persist_evidence(
            list(persistable_by_id.values()), question, target, run_id, handoff, brief,
        )
        status = "evidence-collected" if changed_count else "verified-no-change" if verified else "evidence-unavailable"
        return save(status, stop_reason)

    def collect(self, target, source_types, tasks):
        # Select the adapter's capability before invocation. Catching TypeError
        # from inside a provider and invoking it again duplicates side effects.
        import inspect
        method = self.research_gateway.collect_for_target
        parameters = inspect.signature(method).parameters
        accepts_kwargs = any(value.kind == inspect.Parameter.VAR_KEYWORD for value in parameters.values())
        kwargs = {}
        if "source_types" in parameters or accepts_kwargs:
            kwargs["source_types"] = source_types
        if "research_tasks" in parameters or accepts_kwargs:
            kwargs["research_tasks"] = tasks
        try:
            return method(target, **kwargs)
        except Exception as error:  # noqa: BLE001 - retain progress and an explicit collection failure.
            return [], [{"provider": "research-gateway", "status": "error", "reason": str(error)[:180]}]

    def plan_requires_research(self, brain: Dict[str, object], tasks: Iterable[Dict[str, object]]) -> bool:
        epistemic = brain.get("epistemicState") if isinstance(brain.get("epistemicState"), dict) else {}
        if str(epistemic.get("status") or "") == "contested":
            return True
        hypotheses = ((brain.get("hypothesisSet") or {}).get("hypotheses") if isinstance(brain.get("hypothesisSet"), dict) else []) or []
        if any(str(item.get("verificationStatus") or "") in {"requires-research", "counterfactual-challenge"} for item in hypotheses if isinstance(item, dict)):
            return True
        if not hypotheses and (brain.get("missingData") or (brain.get("researchPlan") or {}).get("unresolvedQuestions")):
            return True
        return any(str(item.get("status") or "") == "blocked-by-data" or bool(item.get("decisionChanging")) for item in tasks or [])

    def plan_collection_work(
        self,
        brain: Dict[str, object],
        question: InvestmentQuestion,
        target: NewsCollectionTarget,
        account_id: str,
        fallback_brief: HypothesisResearchBrief = None,
    ) -> Tuple[Dict[str, object], HypothesisResearchBrief]:
        baseline = brain.get("researchPlan") if isinstance(brain.get("researchPlan"), dict) else {}
        brief = fallback_brief or hypothesis_research_brief_from_brain(brain)
        if not self.hypothesis_research_planner or not hasattr(self.hypothesis_research_planner, "plan"):
            audit = {
                "status": "planner-unavailable",
                "reason": "AI 조사 계획 서비스가 구성되지 않아 TypeDB 기본 계획을 사용합니다.",
                "preservesBaselineTasks": True,
                "decisionEligibility": "research-only",
            }
            return {
                **dict(baseline or {}),
                "planningAudit": audit,
            }, brief.with_planning("planner-unavailable", "typedb-hypothesis-set", audit)
        try:
            result = self.hypothesis_research_planner.plan(
                brain,
                question.to_dict(),
                account_id or question.account_id,
                target.normalized_symbol(),
            )
        except Exception as error:  # noqa: BLE001 - a planning failure cannot widen collection beyond the base plan.
            audit = {
                "status": "planner-failed",
                "reason": str(error)[:180],
                "preservesBaselineTasks": True,
                "decisionEligibility": "research-only",
            }
            return {
                **dict(baseline or {}),
                "planningAudit": audit,
            }, brief.with_planning("planner-failed", "typedb-hypothesis-set", audit)
        result = dict(result or {})
        planned = result.get("researchPlan") if isinstance(result.get("researchPlan"), dict) else dict(baseline or {})
        planned_brief = result.get("hypothesisResearchBrief")
        if isinstance(planned_brief, HypothesisResearchBrief):
            brief = planned_brief
        elif isinstance(planned_brief, dict):
            brief = HypothesisResearchBrief.from_dict(planned_brief)
        return planned, brief

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
        hypothesis_research_brief: HypothesisResearchBrief = None,
    ) -> Tuple[int, ResearchReasoningHandoff]:
        handoff = reasoning_handoff or ResearchReasoningHandoff()
        if not items or not self.evidence_repository:
            return 0, handoff

        persisted_handoff = handoff

        def events(
            saved: int,
            changed_symbols: List[str],
            changed_items: List[ResearchEvidence],
            evidence_deltas=None,
            fact_revisions=None,
            tracks_eligible_set: bool = False,
        ):
            nonlocal persisted_handoff
            if not saved:
                return []
            changed_evidence_ids = [item.evidence_id for item in changed_items if item.evidence_id]
            # A lifecycle-aware repository supplies this exact transaction's
            # eligible fact-set revisions. Compatibility repositories retain
            # their established changed-symbol scheduling behavior.
            evidence_deltas = list(evidence_deltas or [])
            fact_revisions = dict(fact_revisions or {})
            inference_symbols = sorted({
                str(symbol or "").upper().strip()
                for symbol in fact_revisions
                if str(symbol or "").strip()
            }) if tracks_eligible_set else list(changed_symbols or [target.normalized_symbol()])
            persisted_handoff = handoff.requested(changed_evidence_ids) if inference_symbols else handoff
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
                "evidenceDeltas": evidence_deltas,
                "factRevisionsBySymbol": fact_revisions,
                "inferenceChangedSymbols": inference_symbols,
                "reasoningHandoff": persisted_handoff.to_dict(),
                "hypothesisResearchBrief": (hypothesis_research_brief or HypothesisResearchBrief()).to_dict(),
            })
            if not inference_symbols:
                return [completed]
            reasoning = ontology_reasoning_requested_event(
                completed,
                "hypothesis-research-update",
                symbols=inference_symbols,
                changed_count=len(inference_symbols),
                observed_count=len(items),
                fact_types=["ResearchEvidence", "VerifiedClaim", "VerificationRun"],
                fact_types_by_symbol={
                    symbol: ["ResearchEvidence", "VerifiedClaim", "VerificationRun"]
                    for symbol in inference_symbols
                },
                changed_fields_by_symbol={
                    symbol: ["external.researchEvidence", "external.verifiedClaims"]
                    for symbol in inference_symbols
                },
                reason="가설 검증에서 확보한 근거를 전체 ABox 스냅샷에 반영하고 TypeDB 네이티브 추론을 다시 실행합니다.",
                materiality_assessments=materiality,
                fact_revisions_by_symbol=fact_revisions,
                evidence_deltas=evidence_deltas,
            )
            return [completed, reasoning]

        def mutation_events(mutation):
            payload = mutation.to_dict() if hasattr(mutation, "to_dict") else {}
            return events(
                int(payload.get("writtenCount") or getattr(mutation, "written_count", 0) or 0),
                list(payload.get("changedSymbols") or getattr(mutation, "changed_symbols", []) or []),
                list(getattr(mutation, "changed_items", []) or []),
                evidence_deltas=list(payload.get("evidenceDeltas") or []),
                fact_revisions=dict(payload.get("factRevisionsBySymbol") or {}),
                tracks_eligible_set=True,
            )

        if (
            hasattr(self.evidence_repository, "upsert_many_with_events")
            and self.event_publisher
            and hasattr(self.event_publisher, "dispatch_recorded")
        ):
            saved, recorded = self.evidence_repository.upsert_many_with_events(items, mutation_events)
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
            hypothesis_research_brief=hypothesis_research_brief_from_brain(brain),
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
            request_context=request,
        )
        if queued.request_context and not completed.request_context:
            completed = replace(completed, request_context=dict(queued.request_context))
            self.persist_run(completed)
        return completed


class InvestmentResearchQueueRunner:
    def __init__(
        self,
        store,
        orchestrator: InvestmentResearchOrchestrationService,
        hypothesis_proposal_runner=None,
    ):
        self.store = store
        self.orchestrator = orchestrator
        self.hypothesis_proposal_runner = hypothesis_proposal_runner
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
                failed_source = queued
                if hasattr(self.store, "get_run"):
                    try:
                        persisted = self.store.get_run(queued.run_id)
                        if isinstance(persisted, dict) and persisted.get("runId") == queued.run_id:
                            failed_source = ResearchRun.from_dict(persisted)
                    except Exception:  # noqa: BLE001 - report the original failure even if audit reads fail.
                        pass
                failed = replace(
                    failed_source,
                    status="error",
                    stop_reason="execution-failed",
                    completed_at=utc_now_iso(),
                    provider_statuses=[{"provider": "research-worker", "status": "error", "reason": str(error)[:180]}],
                )
                self.orchestrator.persist_run(failed)
                self.last_results.append(failed.to_dict())
        proposal_result = (
            self.hypothesis_proposal_runner.run_once(limit=1)
            if self.hypothesis_proposal_runner is not None
            else {"status": "not-configured", "processedCount": 0}
        )
        return {
            "status": "ok",
            "processedCount": len(runs),
            "queuedCount": self.store.queued_count() if self.store and hasattr(self.store, "queued_count") else 0,
            "results": self.last_results,
            "hypothesisProposalAutomation": proposal_result,
        }

    def status(self) -> Dict[str, object]:
        return {
            "status": "ready",
            "queuedCount": self.store.queued_count() if self.store and hasattr(self.store, "queued_count") else 0,
            "lastResults": self.last_results[-20:],
            "hypothesisProposalAutomation": (
                self.hypothesis_proposal_runner.status()
                if self.hypothesis_proposal_runner is not None
                else {"status": "not-configured"}
            ),
        }


def unique_strings(values: Iterable[object]) -> List[str]:
    result = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
