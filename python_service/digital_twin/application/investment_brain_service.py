from dataclasses import fields
from typing import Dict, List, Optional, Tuple

from ..domain.investment_brain import (
    InvestmentQuestion,
    decision_episode_from_context,
    hypothesis_set_from_relation_context,
    hypothesis_templates_from_rulebox_snapshot,
)
from ..domain.investment_research import NewsCollectionTarget
from ..domain.investment_evidence_governance import (
    ReasoningGeneration,
    ResearchRun,
    complete_reasoning_handoff,
)
from ..domain.message_types import INVESTMENT_INSIGHT
from ..domain.ontology_inference_context import relation_context_from_inferencebox
from ..domain.ontology_worlds import portfolio_world_id
from ..domain.portfolio import PortfolioSummary, Position


class InvestmentBrainService:
    def __init__(
        self,
        monitor_store,
        ontology_repository,
        reviewer,
        decision_episode_store,
        research_orchestrator=None,
        reasoning_refresher=None,
        hypothesis_proposal_service=None,
        research_store=None,
        settings: Dict[str, object] = None,
    ):
        self.monitor_store = monitor_store
        self.ontology_repository = ontology_repository
        self.reviewer = reviewer
        self.decision_episode_store = decision_episode_store
        self.research_orchestrator = research_orchestrator
        self.reasoning_refresher = reasoning_refresher
        self.hypothesis_proposal_service = hypothesis_proposal_service
        self.research_store = research_store
        self.settings = dict(settings or {})

    def ask(self, message: str, account_id: str = "", symbol: str = "") -> Dict[str, object]:
        message = " ".join(str(message or "").split())
        if not message:
            raise ValueError("투자 질문을 입력하세요.")
        state, position, source = self.resolve_subject(message, account_id, symbol)
        if not state or not position:
            return {
                "status": "blocked",
                "engine": "ontology-investment-brain",
                "reply": "최신 계좌 스냅샷에서 질문 대상을 찾지 못했습니다. 회사명 또는 종목을 질문에 포함해 주세요.",
                "missing": ["account-snapshot", "investment-subject"],
            }
        resolved_account_id = str(state.get("accountId") or account_id or "")
        question = InvestmentQuestion.create(
            message,
            subject_symbol=position.symbol,
            subject_name=position.name,
            account_id=resolved_account_id,
        )
        relation_context = self.load_relation_context(state, position, source)
        if not relation_context:
            return {
                "status": "blocked",
                "engine": "ontology-investment-brain",
                "question": question.to_dict(),
                "reply": "TypeDB InferenceBox에서 이 종목과 연결된 추론 관계를 찾지 못해 투자 답변을 만들지 않았습니다.",
                "missing": ["typedb-inference-relations"],
            }
        brain = self.brain_with_reasoning_generation(
            hypothesis_set_from_relation_context(relation_context, question),
            relation_context,
        )
        research_run = self.run_research(question, position, brain, resolved_account_id)
        refresh_result = {}
        if research_run and self.research_requires_reasoning_refresh(research_run):
            refresh_result = self.refresh_reasoning(resolved_account_id, position.symbol)
            refreshed_handoff = self.completed_reasoning_handoff(research_run, refresh_result)
            refreshed = bool(refreshed_handoff and refreshed_handoff.applied())
            if str(refresh_result.get("status") or "") != "queued" and self.research_orchestrator and hasattr(self.research_orchestrator, "mark_reasoning_refreshed"):
                research_run = self.mark_research_reasoning_refreshed(
                    research_run,
                    refreshed,
                    refreshed_handoff,
                )
            if refreshed:
                refreshed_position = refresh_result.get("position") if isinstance(refresh_result.get("position"), dict) else {}
                if refreshed_position:
                    position = position_from_payload(refreshed_position, position.symbol, source)
                refreshed_state = refresh_result.get("state") if isinstance(refresh_result.get("state"), dict) else {}
                if refreshed_state:
                    state = {**state, **refreshed_state}
                refreshed_context = self.load_relation_context(state, position, source)
                if refreshed_context:
                    relation_context = refreshed_context
                    brain = self.brain_with_reasoning_generation(
                        hypothesis_set_from_relation_context(relation_context, question),
                        relation_context,
                    )
        research_payload = research_run.to_dict() if research_run and hasattr(research_run, "to_dict") else {}
        if research_run and self.research_requires_reasoning_refresh(research_run) and not bool(research_payload.get("reasoningRefreshed")):
            return {
                "status": "blocked",
                "engine": "ontology-investment-brain",
                "reply": "새 검증 근거는 저장됐지만 TypeDB 재추론이 완료되지 않아 투자 의견을 만들지 않았습니다. 마지막 정상 추론 세대는 유지됩니다.",
                "question": question.to_dict(),
                "hypothesisSet": brain.get("hypothesisSet") or {},
                "researchPlan": brain.get("researchPlan") or {},
                "researchRun": research_payload,
                "reasoningRefresh": refresh_result,
                "missing": ["typedb-reasoning-refresh"],
            }
        relation_context.update({
            "investmentBrain": brain,
            "hypothesisTemplates": brain.get("hypothesisTemplates") or [],
            "hypothesisSet": brain.get("hypothesisSet") or {},
            "researchPlan": brain.get("researchPlan") or {},
            "selfQuestions": brain.get("selfQuestions") or [],
            "epistemicState": brain.get("epistemicState") or {},
            "researchCycle": {
                **research_payload,
                "reasoningRefresh": refresh_result,
            } if research_payload else {},
        })
        context = {
            "messageType": INVESTMENT_INSIGHT,
            "accountId": resolved_account_id,
            "accountLabel": state.get("accountLabel") or "",
            "displayTarget": position.name or position.symbol,
            "title": position.name or position.symbol,
            "referenceDate": relation_context.get("inferenceGenerationAt") or state.get("generatedAt") or "",
            "rawLines": ["사용자 투자 질문: " + message],
            "criteria": ["TypeDB 동적 인과 가설 비교", "반대 근거와 데이터 공백 조사", "검증 근거 반영 후 공통 AI 심판"],
            "ontologyRelationContext": relation_context,
            "investmentBrainQuestion": question.to_dict(),
        }
        response = self.reviewer.review(context)
        response_payload = response.to_dict()
        episode = decision_episode_from_context(context, response_payload, job_id=question.question_id)
        if episode and self.decision_episode_store:
            facts = dict(relation_context.get("facts") or {})
            facts["inferenceGenerationId"] = relation_context.get("inferenceGenerationId") or ""
            self.decision_episode_store.record_observation(
                resolved_account_id,
                position.symbol,
                facts,
                str(relation_context.get("inferenceGenerationAt") or ""),
            )
            self.decision_episode_store.save(episode)
        proposal_result = self.propose_novel_hypotheses(
            resolved_account_id,
            position.symbol,
            question,
            brain,
            research_payload,
            relation_context,
        )
        return {
            "status": "answered",
            "engine": "ontology-investment-brain",
            "reply": answer_text(response_payload),
            "question": question.to_dict(),
            "answer": response_payload,
            "hypothesisSet": brain.get("hypothesisSet") or {},
            "hypothesisTemplates": brain.get("hypothesisTemplates") or [],
            "researchPlan": brain.get("researchPlan") or {},
            "researchRun": research_payload,
            "reasoningRefresh": refresh_result,
            "novelHypothesisProposal": proposal_result,
            "decisionEpisodeId": episode.episode_id if episode else "",
            "inferenceGenerationId": relation_context.get("inferenceGenerationId") or "",
            "graphStore": relation_context.get("graphStore") or "",
        }

    def run_research(
        self,
        question: InvestmentQuestion,
        position: Position,
        brain: Dict[str, object],
        account_id: str,
    ):
        if not self.research_orchestrator or not hasattr(self.research_orchestrator, "run"):
            return None
        target = NewsCollectionTarget(
            symbol=position.symbol,
            name=position.name,
            market=position.market,
            currency=position.currency,
            sector=getattr(position, "sector", ""),
        )
        try:
            return self.research_orchestrator.run(question, target, brain, account_id=account_id)
        except Exception as error:  # noqa: BLE001 - existing TypeDB context can still answer with a visible research gap.
            failed = ResearchRun(
                run_id="research-run:error:" + question.question_id,
                question_id=question.question_id,
                account_id=account_id,
                symbol=position.symbol,
                status="error",
                task_ids=[],
                source_types=[],
                provider_statuses=[{"provider": "research-orchestrator", "status": "error", "reason": str(error)[:180]}],
            )
            if self.research_store and hasattr(self.research_store, "save_run"):
                return self.research_store.save_run(failed)
            return failed

    def research_requires_reasoning_refresh(self, run) -> bool:
        return int(getattr(run, "changed_evidence_count", 0) or 0) > 0

    def brain_with_reasoning_generation(
        self,
        brain: Dict[str, object],
        relation_context: Dict[str, object],
    ) -> Dict[str, object]:
        """Give research a stable reference to the exact active TypeDB world."""
        enriched = dict(brain or {})
        context = dict(relation_context or {})
        typedb = context.get("typedbInference") if isinstance(context.get("typedbInference"), dict) else {}
        enriched["reasoningGeneration"] = {
            "inferenceGenerationId": str(
                context.get("inferenceGenerationId")
                or typedb.get("inferenceGenerationId")
                or ""
            ),
            "sourceAboxSnapshotId": str(
                context.get("sourceAboxSnapshotId")
                or typedb.get("sourceAboxSnapshotId")
                or ""
            ),
            "worldId": str(context.get("worldId") or typedb.get("worldId") or ""),
            "generationAligned": bool(
                context.get("generationAligned")
                if "generationAligned" in context
                else typedb.get("generationAligned")
            ),
            "observedAt": str(
                context.get("inferenceGenerationAt")
                or typedb.get("inferenceGenerationAt")
                or ""
            ),
        }
        return enriched

    def completed_reasoning_handoff(self, run, refresh_result: Dict[str, object]):
        handoff = getattr(run, "reasoning_handoff", None)
        if not handoff or not getattr(handoff, "request_id", ""):
            return handoff
        result = dict(refresh_result or {})
        payload = result.get("reasoningGeneration")
        if not isinstance(payload, dict):
            payload = result.get("inferenceBox") if isinstance(result.get("inferenceBox"), dict) else result
        return complete_reasoning_handoff(
            handoff,
            ReasoningGeneration.from_dict(payload),
            str(result.get("reason") or ""),
        )

    def mark_research_reasoning_refreshed(self, run, refreshed: bool, handoff):
        marker = getattr(self.research_orchestrator, "mark_reasoning_refreshed", None)
        if not callable(marker):
            return run
        try:
            return marker(run, refreshed, handoff)
        except TypeError:
            # Compatibility adapters cannot persist the generation audit, but
            # current production orchestration always receives the handoff.
            return marker(run, refreshed)

    def enrich_notification_context(
        self,
        context: Dict[str, object],
        account_id: str = "",
        event_id: str = "",
    ) -> Dict[str, object]:
        enriched = dict(context or {})
        relation_context = enriched.get("ontologyRelationContext")
        if not isinstance(relation_context, dict) or not relation_context:
            return enriched
        subject = relation_context.get("subject") if isinstance(relation_context.get("subject"), dict) else {}
        symbol = str(subject.get("symbol") or enriched.get("rawSymbol") or enriched.get("symbol") or "").upper().strip()
        if not symbol:
            return enriched
        state, position, source = self.resolve_subject(symbol, account_id, symbol)
        if not state or not position:
            state, position, source = subject_from_notification_graph_context(
                relation_context,
                enriched,
                account_id,
            )
        if not state or not position:
            enriched["researchCycle"] = {
                "status": "subject-not-found",
                "symbol": symbol,
                "reason": "최신 계좌 스냅샷과 검증 가능한 TypeDB 알림 컨텍스트에서 대상을 찾지 못했습니다.",
            }
            return enriched
        resolved_account_id = str(state.get("accountId") or account_id or "")
        reference_at = str(
            relation_context.get("inferenceGenerationAt")
            or enriched.get("referenceDate")
            or state.get("generatedAt")
            or ""
        )
        question = InvestmentQuestion.create(
            str(enriched.get("investmentBrainQuestionText") or position.name + "의 현재 알림 판단을 반증 가능한 가설로 다시 검증한다."),
            subject_symbol=position.symbol,
            subject_name=position.name,
            account_id=resolved_account_id,
            asked_at=reference_at,
            source="notification",
        )
        brain = self.brain_with_reasoning_generation(
            hypothesis_set_from_relation_context(relation_context, question),
            relation_context,
        )
        research_run = self.run_research(question, position, brain, resolved_account_id)
        refresh_result: Dict[str, object] = {}
        if research_run and self.research_requires_reasoning_refresh(research_run):
            refresh_result = self.refresh_reasoning(resolved_account_id, position.symbol)
            refreshed_handoff = self.completed_reasoning_handoff(research_run, refresh_result)
            refreshed = bool(refreshed_handoff and refreshed_handoff.applied())
            if str(refresh_result.get("status") or "") != "queued" and self.research_orchestrator and hasattr(self.research_orchestrator, "mark_reasoning_refreshed"):
                research_run = self.mark_research_reasoning_refreshed(
                    research_run,
                    refreshed,
                    refreshed_handoff,
                )
            if refreshed:
                refreshed_position = refresh_result.get("position") if isinstance(refresh_result.get("position"), dict) else {}
                refreshed_state = refresh_result.get("state") if isinstance(refresh_result.get("state"), dict) else {}
                if refreshed_position:
                    position = position_from_payload(refreshed_position, position.symbol, source)
                if refreshed_state:
                    state = {**state, **refreshed_state}
                refreshed_context = self.load_relation_context(state, position, source)
                if refreshed_context:
                    relation_context = refreshed_context
                    brain = self.brain_with_reasoning_generation(
                        hypothesis_set_from_relation_context(relation_context, question),
                        relation_context,
                    )
        research_payload = research_run.to_dict() if research_run and hasattr(research_run, "to_dict") else {}
        research_cycle = {
            **research_payload,
            "notificationEventId": str(event_id or ""),
            "reasoningRefresh": refresh_result,
            "subjectResolutionSource": source,
        } if research_payload else {
            "status": "unavailable",
            "notificationEventId": str(event_id or ""),
            "subjectResolutionSource": source,
            "reason": "가설 조사 오케스트레이터가 구성되지 않아 기존 TypeDB 추론 세대를 사용합니다.",
        }
        if int(research_cycle.get("changedEvidenceCount") or 0) > 0 and not bool(research_cycle.get("reasoningRefreshed")):
            research_cycle["unappliedVerifiedClaims"] = list(research_cycle.get("verifiedClaims") or [])
            research_cycle["verifiedClaims"] = []
            research_cycle["investmentJudgmentEligible"] = False
            research_cycle["reason"] = "새 검증 근거의 TypeDB 재추론이 실패해 마지막 정상 InferenceBox만 판단에 사용합니다."
        else:
            research_cycle["investmentJudgmentEligible"] = True
        relation_context.update({
            "investmentBrain": brain,
            "hypothesisTemplates": brain.get("hypothesisTemplates") or [],
            "hypothesisSet": brain.get("hypothesisSet") or {},
            "researchPlan": brain.get("researchPlan") or {},
            "selfQuestions": brain.get("selfQuestions") or [],
            "epistemicState": brain.get("epistemicState") or {},
            "researchCycle": research_cycle,
        })
        enriched["ontologyRelationContext"] = relation_context
        enriched["investmentBrainQuestion"] = question.to_dict()
        enriched["researchCycle"] = research_cycle
        return enriched

    def enqueue_notification_research_context(
        self,
        context: Dict[str, object],
        account_id: str = "",
        event_id: str = "",
    ) -> Dict[str, object]:
        enriched = dict(context or {})
        relation_context = enriched.get("ontologyRelationContext")
        if not isinstance(relation_context, dict) or not relation_context:
            return enriched
        subject = relation_context.get("subject") if isinstance(relation_context.get("subject"), dict) else {}
        symbol = str(subject.get("symbol") or enriched.get("rawSymbol") or enriched.get("symbol") or "").upper().strip()
        if not symbol:
            return enriched
        state, position, source = self.resolve_subject(symbol, account_id, symbol)
        if not state or not position:
            state, position, source = subject_from_notification_graph_context(relation_context, enriched, account_id)
        if not state or not position:
            enriched["researchCycle"] = {
                "status": "subject-not-found",
                "executionMode": "asynchronous",
                "symbol": symbol,
            }
            return enriched
        resolved_account_id = str(state.get("accountId") or account_id or "")
        reference_at = str(relation_context.get("inferenceGenerationAt") or enriched.get("referenceDate") or state.get("generatedAt") or "")
        question = InvestmentQuestion.create(
            str(enriched.get("investmentBrainQuestionText") or position.name + "의 현재 알림 판단을 반증 가능한 가설로 다시 검증한다."),
            subject_symbol=position.symbol,
            subject_name=position.name,
            account_id=resolved_account_id,
            asked_at=reference_at,
            source="notification",
        )
        brain = self.brain_with_reasoning_generation(
            hypothesis_set_from_relation_context(relation_context, question),
            relation_context,
        )
        research_run = None
        if self.research_orchestrator and hasattr(self.research_orchestrator, "enqueue"):
            research_run = self.research_orchestrator.enqueue(
                question,
                NewsCollectionTarget(
                    symbol=position.symbol,
                    name=position.name,
                    market=position.market,
                    currency=position.currency,
                    sector=getattr(position, "sector", ""),
                ),
                brain,
                account_id=resolved_account_id,
                notification_event_id=event_id,
            )
        research_cycle = research_run.to_dict() if research_run and hasattr(research_run, "to_dict") else {
            "status": "unavailable",
            "reason": "비동기 ResearchRun 저장소를 사용할 수 없습니다.",
        }
        research_cycle.update({
            "executionMode": "asynchronous",
            "notificationEventId": str(event_id or ""),
            "subjectResolutionSource": source,
            "usesActiveInferenceGeneration": True,
            "investmentJudgmentEligible": True,
        })
        relation_context.update({
            "investmentBrain": brain,
            "hypothesisTemplates": brain.get("hypothesisTemplates") or [],
            "hypothesisSet": brain.get("hypothesisSet") or {},
            "researchPlan": brain.get("researchPlan") or {},
            "selfQuestions": brain.get("selfQuestions") or [],
            "epistemicState": brain.get("epistemicState") or {},
            "researchCycle": research_cycle,
        })
        enriched["ontologyRelationContext"] = relation_context
        enriched["investmentBrainQuestion"] = question.to_dict()
        enriched["researchCycle"] = research_cycle
        return enriched

    def refresh_reasoning(self, account_id: str, symbol: str) -> Dict[str, object]:
        if not self.reasoning_refresher:
            return {"status": "queued", "refreshed": False, "reason": "전용 온톨로지 추론 워커가 TypeDB 활성 세대를 갱신합니다."}
        try:
            result = self.reasoning_refresher(account_id, symbol)
        except Exception as error:  # noqa: BLE001 - caller keeps the previous usable inference generation.
            return {"status": "error", "refreshed": False, "reason": str(error)[:180]}
        if isinstance(result, dict):
            return result
        return {"status": "completed" if result else "error", "refreshed": bool(result)}

    def propose_novel_hypotheses(
        self,
        account_id: str,
        symbol: str,
        question: InvestmentQuestion,
        brain: Dict[str, object],
        research_run: Dict[str, object],
        relation_context: Dict[str, object],
    ) -> Dict[str, object]:
        if not self.hypothesis_proposal_service or not self.should_propose_novel_hypothesis(brain, research_run):
            return {"status": "not-required", "proposalCount": 0, "proposals": []}
        return self.hypothesis_proposal_service.propose(
            account_id,
            symbol,
            question.to_dict(),
            brain.get("hypothesisSet") or {},
            research_run,
            relation_context,
        )

    def should_propose_novel_hypothesis(self, brain: Dict[str, object], research_run: Dict[str, object] = None) -> bool:
        hypotheses = ((brain.get("hypothesisSet") or {}).get("hypotheses") if isinstance(brain.get("hypothesisSet"), dict) else []) or []
        approved_graph = [
            item for item in hypotheses
            if isinstance(item, dict)
            and str(item.get("approvalStatus") or "") == "approved-active"
            and item.get("supportingRuleIds")
        ]
        epistemic = brain.get("epistemicState") if isinstance(brain.get("epistemicState"), dict) else {}
        verified_claims = (research_run or {}).get("verifiedClaims") if isinstance(research_run, dict) else []
        return len(approved_graph) < 2 or (
            str(epistemic.get("status") or "") == "contested"
            and bool(verified_claims)
        )

    def episodes(self, account_id: str = "", symbol: str = "", limit: int = 50) -> Dict[str, object]:
        rows = self.decision_episode_store.list(account_id, symbol, limit) if self.decision_episode_store else []
        return {
            "engine": "ontology-investment-brain",
            "count": len(rows),
            "episodes": [item.to_dict() for item in rows],
        }

    def performance(self, account_id: str = "", symbol: str = "", limit: int = 500) -> Dict[str, object]:
        if not self.decision_episode_store or not hasattr(self.decision_episode_store, "performance"):
            return {"status": "unavailable", "engine": "ontology-investment-brain"}
        result = self.decision_episode_store.performance(account_id, symbol, limit)
        return {
            "engine": "ontology-investment-brain",
            "source": "DecisionEpisode+ObservedOutcome",
            "accountId": account_id,
            "symbol": str(symbol or "").upper(),
            **dict(result or {}),
        }

    def learning_proposals(self, status: str = "", limit: int = 50) -> Dict[str, object]:
        rows = self.decision_episode_store.list_learning_proposals(status, limit) if self.decision_episode_store else []
        return {
            "engine": "ontology-investment-brain",
            "governance": "human-review-required-no-automatic-rulebox-deployment",
            "count": len(rows),
            "proposals": rows,
        }

    def review_learning_proposal(self, proposal_id: str, status: str, note: str = "") -> Dict[str, object]:
        proposal = self.decision_episode_store.review_learning_proposal(proposal_id, status, note)
        return {
            "engine": "ontology-investment-brain",
            "governance": "reviewed-not-deployed",
            "proposal": proposal,
        }

    def hypothesis_templates(self) -> Dict[str, object]:
        snapshot = self.ontology_repository.rulebox_snapshot() if self.ontology_repository and hasattr(self.ontology_repository, "rulebox_snapshot") else {}
        rows = hypothesis_templates_from_rulebox_snapshot(snapshot)
        return {
            "engine": "ontology-investment-brain",
            "source": "typedb-active-rulebox",
            "count": len(rows),
            "templates": rows,
        }

    def research_runs(self, account_id: str = "", symbol: str = "", limit: int = 50) -> Dict[str, object]:
        rows = self.research_store.list_runs(account_id, symbol, limit) if self.research_store else []
        return {"count": len(rows), "runs": rows}

    def hypothesis_proposals(self, status: str = "", symbol: str = "", limit: int = 50) -> Dict[str, object]:
        if not self.hypothesis_proposal_service:
            return {"count": 0, "proposals": [], "status": "disabled"}
        return self.hypothesis_proposal_service.list(status, symbol, limit)

    def review_hypothesis_proposal(self, proposal_id: str, status: str, note: str = "") -> Dict[str, object]:
        if not self.hypothesis_proposal_service:
            raise RuntimeError("가설 제안 서비스가 구성되지 않았습니다.")
        return self.hypothesis_proposal_service.review(proposal_id, status, note)

    def resolve_subject(self, message: str, account_id: str = "", symbol: str = "") -> Tuple[Dict[str, object], Optional[Position], str]:
        states = self.monitor_store.load_previous() if hasattr(self.monitor_store, "load_previous") else dict(getattr(self.monitor_store, "previous", {}) or {})
        requested_symbol = str(symbol or "").upper().strip()
        candidates = []
        for state_account_id, state in (states or {}).items():
            if not isinstance(state, dict) or (account_id and str(state_account_id) != str(account_id)):
                continue
            for source_key, source in [("positions", "holding"), ("watchlist", "watchlist")]:
                rows = state.get(source_key) if isinstance(state.get(source_key), dict) else {}
                for item_symbol, payload in rows.items():
                    if not isinstance(payload, dict):
                        continue
                    item = position_from_payload(payload, item_symbol, source)
                    match_kind = subject_match_kind(message, requested_symbol, item)
                    if match_kind != "none":
                        candidates.append((match_kind, state, item, source))
        if not candidates:
            return {}, None, ""
        _, state, item, source = max(candidates, key=lambda row: subject_match_priority(row[0]))
        return state, item, source

    def load_relation_context(self, state: Dict[str, object], position: Position, source: str) -> Dict[str, object]:
        decision_rows = state.get("decisions") if isinstance(state.get("decisions"), dict) else {}
        stored_decision = decision_rows.get(position.symbol) if isinstance(decision_rows.get(position.symbol), dict) else {}
        stored_context = stored_decision.get("relation_rule_context") or stored_decision.get("relationRuleContext")
        world_id = self.portfolio_world_id_for_state(state)
        inferencebox = {}
        if self.ontology_repository and hasattr(self.ontology_repository, "inferencebox_snapshot"):
            try:
                inferencebox = self.ontology_repository.inferencebox_snapshot(
                    symbols=[position.symbol],
                    limit=bounded_int_setting(self.settings, "investmentBrainInferenceBoxLimit", 500, 120, 500),
                    world_id=world_id,
                )
            except TypeError as error:
                # Narrow compatibility for older in-memory test adapters. The
                # production TypeDB adapter always receives the account world.
                if "unexpected keyword" not in str(error) and "world_id" not in str(error):
                    raise
                try:
                    inferencebox = self.ontology_repository.inferencebox_snapshot(
                        symbols=[position.symbol],
                        limit=bounded_int_setting(self.settings, "investmentBrainInferenceBoxLimit", 500, 120, 500),
                    )
                except Exception:
                    inferencebox = {}
            except Exception:  # noqa: BLE001 - stored graph context is the read fallback.
                inferencebox = {}
        if isinstance(inferencebox, dict) and (inferencebox.get("relations") or inferencebox.get("traces")):
            context = relation_context_from_inferencebox(
                position,
                portfolio_from_payload(state.get("portfolio") or {}),
                inferencebox,
                external_signals=state.get("externalSignals") if isinstance(state.get("externalSignals"), dict) else {},
                settings=self.settings,
                source=source,
                prompt_id="investmentBrainQuestion",
            )
            context.setdefault("worldId", world_id)
            return context
        context = dict(stored_context or {}) if isinstance(stored_context, dict) else {}
        if context:
            context.setdefault("worldId", world_id)
        return context

    def portfolio_world_id_for_state(self, state: Dict[str, object]) -> str:
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        account_context = metadata.get("accountContext") if isinstance(metadata.get("accountContext"), dict) else {}
        account_id = str(state.get("accountId") or account_context.get("accountId") or "").strip()
        if not account_id:
            return ""
        tenant_id = str(
            metadata.get("tenantId")
            or account_context.get("tenantId")
            or self.settings.get("ontologyTenantId")
            or self.settings.get("tenantId")
            or ""
        ).strip()
        return portfolio_world_id(account_id, tenant_id)


def position_from_payload(payload: Dict[str, object], symbol: str, source: str) -> Position:
    allowed = {item.name for item in fields(Position)}
    values = {key: value for key, value in payload.items() if key in allowed}
    values["symbol"] = str(values.get("symbol") or symbol).upper()
    values["name"] = str(values.get("name") or values["symbol"])
    values["source"] = source
    return Position(**values)


def portfolio_from_payload(payload: Dict[str, object]) -> PortfolioSummary:
    payload = payload if isinstance(payload, dict) else {}
    return PortfolioSummary(
        total=float(payload.get("total") or 0),
        invested=float(payload.get("invested") or 0),
        cash=float(payload.get("cash") or 0),
        markets=list(payload.get("markets") or []),
        sectors=list(payload.get("sectors") or []),
        concentration=float(payload.get("concentration") or 0),
    )


def subject_from_notification_graph_context(
    relation_context: Dict[str, object],
    notification_context: Dict[str, object],
    account_id: str = "",
) -> Tuple[Dict[str, object], Optional[Position], str]:
    graph_inference = relation_context.get("graphStoreInference") if isinstance(relation_context.get("graphStoreInference"), dict) else {}
    graph_backed = bool(
        relation_context.get("graphStoreUsed")
        or graph_inference.get("relations")
        or graph_inference.get("traces")
    )
    if not graph_backed:
        return {}, None, ""
    subject = relation_context.get("subject") if isinstance(relation_context.get("subject"), dict) else {}
    facts = relation_context.get("facts") if isinstance(relation_context.get("facts"), dict) else {}
    symbol = str(subject.get("symbol") or facts.get("symbol") or notification_context.get("rawSymbol") or "").upper().strip()
    if not symbol:
        return {}, None, ""
    market = str(subject.get("market") or facts.get("market") or "").upper().strip()
    currency = str(subject.get("currency") or facts.get("currency") or ("KRW" if market == "KR" or symbol.isdigit() else ""))
    values = {
        "symbol": symbol,
        "name": str(subject.get("name") or facts.get("name") or notification_context.get("displayTarget") or symbol),
        "market": market,
        "currency": currency,
        "quantity": facts.get("quantity") or 0,
        "sellable_quantity": facts.get("sellableQuantity") or 0,
        "average_price": facts.get("averagePrice") or 0,
        "current_price": facts.get("currentPrice") or 0,
        "change_rate": facts.get("changeRate"),
        "market_value": facts.get("marketValue") or 0,
        "market_value_krw": facts.get("marketValueKrw") or facts.get("marketValueKRW") or 0,
        "profit_loss": facts.get("profitLoss") or 0,
        "profit_loss_krw": facts.get("profitLossKrw") or facts.get("profitLossKRW") or 0,
        "profit_loss_rate": facts.get("profitLossRate") or 0,
        "volume": facts.get("volume") or 0,
        "volume_ratio": facts.get("volumeRatio") or 0,
        "trade_strength": facts.get("tradeStrength") or 0,
        "ma5": facts.get("ma5") or 0,
        "ma20": facts.get("ma20") or 0,
        "ma60": facts.get("ma60") or 0,
        "ma20_distance": facts.get("ma20Distance") or 0,
        "ma60_distance": facts.get("ma60Distance") or 0,
        "sector": str(subject.get("sector") or facts.get("sector") or "기타"),
        "source": str(facts.get("source") or ("holding" if facts.get("isHolding") else "watchlist")),
        "updated_at": str(facts.get("observedAt") or relation_context.get("inferenceGenerationAt") or ""),
        "data_quality": "graph-context",
    }
    try:
        position = Position(**values)
    except (TypeError, ValueError):
        return {}, None, ""
    resolved_account_id = str(notification_context.get("accountId") or account_id or facts.get("accountId") or "")
    portfolio_payload = {
        "total": facts.get("portfolioTotal") or facts.get("accountTotal") or notification_context.get("portfolioTotal") or 0,
        "invested": facts.get("portfolioInvested") or 0,
        "cash": facts.get("portfolioCash") or 0,
        "markets": [],
        "sectors": [],
        "concentration": facts.get("portfolioConcentration") or 0,
    }
    state = {
        "accountId": resolved_account_id,
        "accountLabel": str(notification_context.get("accountLabel") or ""),
        "generatedAt": str(relation_context.get("inferenceGenerationAt") or facts.get("observedAt") or ""),
        "portfolio": portfolio_payload,
        "positions": {symbol: position.to_dict()} if position.source == "holding" else {},
        "watchlist": {symbol: position.to_dict()} if position.source != "holding" else {},
        "decisions": {},
        "externalSignals": {},
    }
    return state, position, "notification-graph-context"


def subject_match_kind(message: str, requested_symbol: str, position: Position) -> str:
    if requested_symbol and requested_symbol == position.symbol.upper():
        return "exact"
    compact = str(message or "").lower().replace(" ", "")
    symbol = position.symbol.lower().replace(" ", "")
    name = position.name.lower().replace(" ", "")
    if symbol and symbol in compact:
        return "symbol"
    if name and name in compact:
        return "name"
    return "none"


def subject_match_priority(match_kind: str) -> int:
    return ("none", "name", "symbol", "exact").index(str(match_kind or "none"))


def answer_text(payload: Dict[str, object]) -> str:
    action_label = str(payload.get("actionLabel") or payload.get("action") or "")
    summary = str(payload.get("summary") or "")
    opinion = str(payload.get("opinion") or "")
    epistemic = str(payload.get("epistemicSummary") or "")
    rows = [item for item in [action_label + (": " if action_label and summary else "") + summary, opinion, epistemic] if item]
    return "\n\n".join(rows)


def bounded_int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))
