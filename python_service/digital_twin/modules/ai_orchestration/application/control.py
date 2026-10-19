"""Durable independent research loop, with capabilities supplied by composition."""
from datetime import datetime, timedelta, timezone
from digital_twin.modules.ai_orchestration.domain.continuity import judgment_continuity
from ..domain.memory_reference import historical_facts
from digital_twin.modules.reasoning.contracts import EvidenceContractError, EvidenceReadError, ObservationEvidenceSession
from digital_twin.modules.ai_orchestration.application.retrieval import RetrievalLeaseLost, RetrievalContractFailure
from .working_retrieval import retrieve_working_evidence
from ..domain.observation_wait import retry_key, disposition, unchanged_wait
from ..domain import research_repeat as repeat, execution_lanes as lanes
from digital_twin.modules.ai_orchestration.domain.planning import enabled, identity, stamp, validate_plan, observation_fingerprint
from digital_twin.modules.ai_orchestration.domain.execution_input import LANE_PROMPT_VERSION, freeze_execution_input, freeze_review_input, freeze_repair_input, PROMPT_VERSION
from digital_twin.modules.ai_orchestration.domain.insight_repair import correction_warranted
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality, accept_review
from digital_twin.modules.ai_orchestration.domain.observation_wording import OBSERVATION_WORDING_VERSION
from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait
from digital_twin.modules.ai_orchestration.domain.research_wait import AIResearchWait
from digital_twin.modules.ai_orchestration.domain.execution_resilience import AIExecutionDeferred, AIExecutionError
from digital_twin.modules.outcomes.contracts import evaluate_observation_conditions
from digital_twin.modules.ai_orchestration.domain.observation_recovery import observation_resume
from digital_twin.modules.ai_orchestration.domain.validation_diagnostic import task_failure


class AIControlService:
    def __init__(self, store, subjects, evidence, planner, researcher, research_memory, settings=None, delivery_memory=None, reviewer=None, development_memory=None, brain_memory=None, brain_waker=None, read_planner=None, evidence_waker=None, read_round_budget=None, company_memory=None, research_plan_memory=None, research_gate=None, knowledge_context=None, knowledge_refresh=None, rulebox_feedback=None, market_brain_memory=None):
        self.store, self.subjects, self.evidence = store, subjects, evidence
        self.planner, self.researcher, self.research_memory = planner, researcher, research_memory
        self.settings = dict(settings or {})
        self.delivery_memory = delivery_memory or (lambda account, symbol: {})
        self.reviewer = reviewer
        self.development_memory = development_memory or (lambda account, symbol: [])
        self.brain_memory = brain_memory or (lambda account, symbol, world: [])
        self.market_brain_memory = market_brain_memory
        self.brain_waker = brain_waker or (lambda subjects: None)
        self.read_planner = read_planner
        self.evidence_waker = evidence_waker or (lambda subjects: None)
        self.read_round_budget = read_round_budget or (lambda: 3)
        self.company_memory = company_memory or (lambda account, symbol, cutoff: {})
        self.research_plan_memory = research_plan_memory or self.brain_memory
        self.research_gate = research_gate or (lambda job: {"allowed": True})
        self.knowledge_context = knowledge_context
        self.knowledge_refresh = knowledge_refresh or (lambda context, packet: context)
        self.rulebox_feedback = rulebox_feedback or (lambda packet: [])

    def run_once(self):
        self.store.record_runtime_pulse()
        if not enabled(self.settings):
            return {"status": "paused"}
        subjects = list(self.subjects())
        for subject in subjects:
            self.store.seed(subject)
        self.brain_waker(subjects)
        self.evidence_waker(subjects)
        try:
            job = self.store.claim()
        except AIControlBudgetWait as wait:
            return wait.result()
        if not job:
            return {"status": "idle"}
        stage = "subject-check"
        try:
            current_subject = next((subject for subject in subjects
                if all(subject[key] == job[key] for key in ("accountId", "symbol", "worldId"))), None)
            if current_subject is None:
                self.store.complete(job, {"status": "retired", "reason": "subject-no-longer-observed"}, [])
                return {"status": "retired", "taskId": job["taskId"]}
            with self.store.keep_alive(job):
                if job["capability"] in lanes.OBSERVATIONS:
                    stage = "evidence-capture"
                    self.store.execution_wait()
                    capture = self.evidence({**job, "marketWorldId": current_subject.get("marketWorldId", "")})
                    session = capture if isinstance(capture, ObservationEvidenceSession) else None
                    packet = session.packet() if session else capture
                    if not packet.get("facts") or not packet.get("sourceSnapshotId"):
                        raise ValueError("current verified graph facts unavailable")
                    if lanes.enabled(self.settings):
                        packet = lanes.annotate(packet, job)
                    history = (self.store.memory(job["accountId"], job["symbol"], lanes.MARKET) if lanes.market(job)
                               else self.store.memory(job["accountId"], job["symbol"]))
                    stage = "memory-and-followup"
                    history = [row for row in history if not row.get("worldId") or row["worldId"] == job["worldId"]]
                    brain_reader = (self.market_brain_memory or self.brain_memory) if lanes.market(packet) else self.brain_memory
                    research = list(brain_reader(job["accountId"], job["symbol"], job["worldId"]))
                    research.extend(self.research_memory(job["accountId"], job["symbol"]))
                    company = self.company_memory(job["accountId"], job["symbol"], packet["capturedAt"])
                    if company:
                        research.append(company)
                    research.extend(row for row in self.development_memory(job["accountId"], job["symbol"])
                                    if not row.get("worldId") or row["worldId"] == job["worldId"])
                    research.extend(self.rulebox_feedback(packet))
                    if lanes.market(packet):
                        research = lanes.market_memories(research)
                    elif lanes.identified(packet):
                        research = lanes.scoped_memories(research, False)
                    packet["taskId"] = job["taskId"]
                    if job.get("evidenceWake"):
                        packet["evidenceWake"] = job["evidenceWake"]
                    packet["questionsToCheck"] = job.get("watchQuestions", [])
                    packet["lastDeliveredNotification"] = self.delivery_memory(job["accountId"], job["symbol"])
                    packet["followUpEvaluations"] = evaluate_observation_conditions(packet, packet["lastDeliveredNotification"],
                        (history[0] if history else {}).get("followUpEvaluations", []))
                    knowledge = self.knowledge_context(packet) if self.knowledge_context else None
                    if knowledge:
                        from ..domain.scoped_retrieval import AUTHOR_SCOPE_POLICY
                        packet["researchKnowledge"] = lanes.compact_knowledge(knowledge) if lanes.identified(packet) else knowledge
                        packet["researchExecutionPolicy"] = AUTHOR_SCOPE_POLICY
                    if session and self.read_planner and (knowledge or lanes.identified(packet)):
                        from ..domain.memory_reference import VERSION as indexed_policy
                        packet["memoryReadPolicy"] = indexed_policy
                    if (knowledge or lanes.identified(packet)) and session and self.read_planner and not lanes.market(packet):
                        from ..domain.memory_reference import VERSION as memory_policy
                        packet["memoryReadPolicy"] = memory_policy
                        from ..domain.question_assessment import VERSION as question_policy
                        packet["questionWorkPolicy"] = question_policy
                        from ..domain.question_assessment import prepare
                        research = prepare(research, session.question_work_inventory())
                        packet["questionWorkPending"] = sorted(row["questionWorkKey"] for row in research
                            if row.get("questionWorkKey") and not row.get("questionWorkUnchanged"))
                    fingerprint = lanes.material_key(packet, research) if lanes.market(packet) else observation_fingerprint(packet, research)
                    repeat_key = fingerprint if lanes.market(packet) else repeat.observation_key(packet, research)
                    retry_identity = retry_key(packet, research)
                    outcome_reader = getattr(self.store, "last_processing_outcome", lambda *_: {})
                    previous_outcome = outcome_reader(job["accountId"], job["symbol"], lanes.MARKET) if lanes.market(job) else outcome_reader(job["accountId"], job["symbol"])
                    if (packet.get("questionWorkPolicy") and not packet.get("questionWorkPending")
                            and previous_outcome.get("questionWorkPending")):
                        # Parking the last cohort is our bookkeeping, not new
                        # evidence for an otherwise empty paid research pass.
                        parked_key = retry_key({**packet, "questionWorkPending": previous_outcome["questionWorkPending"]}, research)
                        if unchanged_wait(previous_outcome, parked_key):
                            retry_identity = parked_key
                    if unchanged_wait(previous_outcome, retry_identity) and not any(row.get("transitionVerified") for row in packet["followUpEvaluations"]):
                        return self.complete_wait(job, packet, previous_outcome, [], "unchanged-wait")
                    if (lanes.identified(packet) and not lanes.market(packet) and packet.get("questionWorkPolicy")
                            and not packet.get("questionWorkPending")):
                        state = disposition(packet, retry_identity, "data-wait", ["새 원문 또는 아직 평가하지 않은 원래 연구 질문을 기다립니다."])
                        return self.complete_wait(job, packet, state, [], "unchanged-wait")
                    previous = history[0] if history else {}
                    from digital_twin.modules.ai_orchestration.domain.brain_management import due_memory
                    if (not due_memory(research) and not any(row.get("reviewDue") for row in research if row.get("kind") == "business-thesis")
                            and (previous.get("inputFingerprint") == fingerprint or previous.get("observationRepeatKey") == repeat_key) and previous.get("observedAt")
                            and previous.get("executionPromptVersion") == (LANE_PROMPT_VERSION if lanes.identified(packet) else PROMPT_VERSION)
                            and previous.get("quality", {}).get("status") in {"accepted", "observation-only"}
                            and datetime.fromisoformat(previous["observedAt"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)
                            and not any(row.get("transitionVerified") for row in packet["followUpEvaluations"])):
                        due = lanes.wait_until(packet) if lanes.market(packet) else (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
                        child = {**self.subject(job), "capability": job["capability"], "watchQuestions": job.get("watchQuestions", []), "taskId": identity(job["taskId"], "unchanged"), "availableAt": due}
                        saved = self.store.complete(job, {"status": "unchanged", "reason": "새 근거가 없어 AI 호출을 생략했습니다.",
                            "resumption": observation_resume(job, packet),
                            "followUpEvaluations": packet["followUpEvaluations"]}, [child])
                        return {"status": "unchanged" if saved else "lease-lost", "taskId": job["taskId"]}
                    retrieval_trace = []
                    stage = "evidence-retrieval"
                    if session and self.read_planner:
                        packet, history, research, retrieval_trace = retrieve_working_evidence(session, packet, history, research,
                            self.read_planner, lambda value: self.store.save_execution_input(job, value),
                            self.settings.get("aiObservationPromptMaxBytes", 256 * 1024), self.read_round_budget(),
                            record_round=lambda step: self.store.save_retrieval_round(job, step))
                        if packet["retrieval"]["status"] not in {"ready", "research-ready", "assessment-ready"}:
                            missing = [item for step in retrieval_trace for item in (step.get("decision") or {}).get("missingEvidence", [])]
                            blocked = (not missing or packet["retrieval"]["status"] == "context-budget" or any(
                                read["result"].get("status") in {"context-budget", "repeated-read"} for step in retrieval_trace for read in step["reads"]))
                            state = disposition(packet, retry_identity, "retrieval-blocked" if blocked else "data-wait",
                                missing or ["조회 범위 또는 호출 한도 안에서 필요한 근거를 선택하지 못했습니다."])
                            return self.complete_wait(job, packet, state, retrieval_trace)
                    from ..domain.business_research import evaluate_thesis
                    from copy import deepcopy
                    business_memory = []
                    for memory in research:
                        if memory.get("kind") == "business-thesis":
                            observations = evaluate_thesis(memory, packet)
                            memory["reviewDue"] = memory.get("reviewDue", False) or observations != memory.get("observations", [])
                            memory["observations"] = observations
                            business_memory.append(deepcopy(memory))
                    packet["businessThesisMemory"] = business_memory
                    if knowledge:
                        refreshed = self.knowledge_refresh(knowledge, packet)
                        packet["researchKnowledge"] = lanes.compact_knowledge(refreshed) if lanes.identified(packet) else refreshed
                    stage = "input-freeze"
                    envelope = freeze_execution_input(packet, history, research,
                        max_prompt_bytes=self.settings.get("aiObservationPromptMaxBytes", 256 * 1024), retrieval_trace=retrieval_trace)
                    input_id = self.store.save_execution_input(job, envelope)
                    if not input_id:
                        return {"status": "lease-lost", "taskId": job["taskId"]}
                    stage = "author-execution"
                    raw = self.planner(envelope, input_id)
                    stage = "plan-validation"
                    from .author_validation import validate_author
                    plan, raw, envelope, input_id, validation_repair = validate_author(raw, envelope, input_id,
                        self.planner, lambda value: self.store.save_execution_input(job, value))
                    result = {**plan, "input": packet, "inputFingerprint": fingerprint, "observationRepeatKey": repeat_key, "observedAt": stamp(),
                              "executionInputId": input_id, "executionPromptVersion": LANE_PROMPT_VERSION if lanes.identified(packet) else PROMPT_VERSION,
                              "wordingVersion": OBSERVATION_WORDING_VERSION,
                              "memoryCoverage": envelope["memoryCoverage"],
                              "judgmentContinuity": judgment_continuity(envelope),
                              "followUpEvaluations": packet["followUpEvaluations"],
                              "resumption": observation_resume(job, packet),
                              "comparisonFacts": historical_facts(envelope["previousAnalyses"])}
                    if validation_repair:
                        result["validationRepair"] = validation_repair
                    for verification in range(2):
                        result["quality"] = local_quality(result)
                        if result["notification"]["send"] and result["quality"]["status"] == "awaiting-review" and self.reviewer:
                            review_input = freeze_review_input(result,
                                max_prompt_bytes=envelope["promptBudgetBytes"])
                            review_id = self.store.save_execution_input(job, review_input)
                            if not review_id:
                                return {"status": "lease-lost", "taskId": job["taskId"]}
                            try:
                                result["quality"] = accept_review(result, self.reviewer(review_input, review_id), review_id)
                            except (AIControlBudgetWait, AIExecutionDeferred):
                                raise
                            except Exception as error:
                                result["quality"].update(status="rejected", errors=["독립 검토를 완료하지 못해 발송을 보류했습니다."])
                                result["quality"]["failure"] = task_failure("independent-review", error)[1]
                        if (verification or validation_repair or packet.get("researchScope", {}).get("mode") == "research-only"
                                or packet.get("retrieval", {}).get("status") in {"deferred", "repeated-read", "context-budget"}
                                or not correction_warranted(result)):
                            break
                        result["repair"] = {"initialInputId": input_id, "initialErrors": result["quality"]["errors"], "status": "pending"}
                        try:
                            correction = freeze_repair_input(envelope, raw, result["quality"]["errors"], input_id, result["quality"].get("review"))
                            repair_id = self.store.save_execution_input(job, correction)
                            if not repair_id:
                                return {"status": "lease-lost", "taskId": job["taskId"]}
                            result["repair"]["inputId"] = repair_id
                            repaired = validate_plan(self.planner(correction, repair_id), packet, correction["researchResults"], require_research=True, require_resolution=True)
                            result.pop("conditionValidation", None)
                            result.update(repaired, executionInputId=repair_id, observedAt=stamp(), memoryCoverage=correction["memoryCoverage"],
                                          judgmentContinuity=judgment_continuity(correction),
                                          comparisonFacts=historical_facts(correction["previousAnalyses"]))
                            envelope = correction
                        except (AIControlBudgetWait, AIExecutionDeferred):
                            raise
                        except Exception as error:
                            result["repair"].update(status="failed", errorKind=type(error).__name__)
                            result["repair"]["failure"] = task_failure("repair", error)[1]
                            break
                    if result.get("repair", {}).get("status") == "pending":
                        result["repair"]["status"] = result["quality"]["status"]
                    from ..domain import research_review
                    if research_review.needed(result) and (result["quality"]["status"] not in {"accepted", "observation-only"}
                            or (result.get("knowledgeResearch") or {}).get("claims")
                            or (packet.get("questionEvidencePolicy") and any(row["action"] in {"answered", "dismissed"}
                                for row in result.get("caseReviews", [])))
                            or (packet.get("questionWorkPolicy") and result.get("caseReviews"))):
                        stage = "research-assessment-review"
                        result["researchQuality"] = {"version": research_review.VERSION, "status": "rejected"}
                        try:
                            review_input = research_review.freeze(result, envelope["researchResults"], envelope["promptBudgetBytes"])
                            review_id = self.store.save_execution_input(job, review_input)
                            if not review_id:
                                return {"status": "lease-lost", "taskId": job["taskId"]}
                            result["researchQuality"] = research_review.accept(result,
                                (self.reviewer or self.planner)(review_input, review_id), review_id)
                        except (AIControlBudgetWait, AIExecutionDeferred):
                            raise
                        except Exception as error:
                            result["researchQuality"]["failure"] = task_failure(stage, error)[1]
                    if result.get("conditionValidation", {}).get("reasonCode") == "condition-baseline-unusable":
                        result["dataRecovery"] = {"status": "awaiting-next-observation", "reasonCode": "condition-baseline-unusable",
                                                  "sameSnapshotRepairSkipped": True}
                    from ..domain.scoped_research import research_disposition
                    result["processingOutcome"] = disposition(packet, retry_identity,
                        research_disposition(result) or ("data-wait" if result.get("dataRecovery") else "output-invalid" if result["quality"]["status"] == "rejected" else "ready"),
                        result["quality"].get("errors", []))
                    children = []
                    for question in result["researchQuestions"]:
                        request = next((item.get("researchRequest", {}) for item in result["workQuestions"]
                                        if item["question"] == question and item["capability"] == "research"), {})
                        children.append({**self.subject(job), "capability": "research", "priority": 5, "question": question,
                                         "researchRequest": request,
                                         "taskId": identity(job["taskId"], question), "availableAt": stamp()})
                    due = (datetime.now(timezone.utc) + timedelta(minutes=result["nextCheckMinutes"])).isoformat().replace("+00:00", "Z")
                    children.append({**self.subject(job), "capability": job["capability"], "watchQuestions": result["questions"], "taskId": identity(job["taskId"], "next"), "availableAt": due})
                elif job["capability"] == "research-plan":
                    # Operational source planning has no dependency on graph
                    # readiness, narrative novelty or notification acceptance.
                    stage = "research-planning"
                    from ..domain.research_work import freeze, validate_plan as validate_research_plan, needs_research_plan
                    memories = list(self.research_plan_memory(job["accountId"], job["symbol"], job["worldId"]))
                    cases = [row for row in memories if row.get("kind") == "brain-case" and needs_research_plan(row)]
                    for row in cases:
                        repeat.initialize(row)
                        row["planDue"] = repeat.planning_due(row, stamp())
                    cases = [repeat.planning_memory(row) for row in cases]
                    envelope = freeze(job, cases, job.get("researchGaps", []),
                                      self.settings.get("aiObservationPromptMaxBytes", 256 * 1024))
                    if not any(row.get("planDue") for row in cases) and (cases or not envelope["gaps"]):
                        result, children = {"status": "research-plan-obsolete"}, []
                    else:
                        self.store.execution_wait()
                        input_id = self.store.save_execution_input(job, envelope)
                        if not input_id:
                            return {"status": "lease-lost", "taskId": job["taskId"]}
                        plan = validate_research_plan(self.planner(envelope, input_id), envelope)
                        result = {"status": "research-planned", "researchPlan": plan, "executionInputId": input_id}
                        children = []
                elif job["capability"] == "research":
                    stage = "research"
                    admission = self.research_gate(job)
                    result = (self.researcher({**job, "knownSourceAnswer": admission.get("knownSourceAnswer", {})})
                              if admission["allowed"] else {"status": "research-repeat-suppressed", "reason": admission["reason"]})
                    if admission.get("version"):
                        result["researchAdmission"] = {key: value for key, value in admission.items() if key != "knownSourceAnswer"}
                    children = []
                else:
                    raise ValueError("unsupported AI capability")
                stage = "completion"
                if not self.store.complete(job, result, children):
                    return {"status": "lease-lost", "taskId": job["taskId"]}
            return {"status": "completed", "taskId": job["taskId"], "capability": job["capability"]}
        except RetrievalLeaseLost:
            return {"status": "lease-lost", "taskId": job["taskId"]}
        except RetrievalContractFailure as error:
            failure_result = {"failure": error.failure}
            if "packet" in locals() and lanes.identified(packet):
                failure_result.update(input={"executionLane": packet["executionLane"]},
                    processingOutcome=disposition(packet, retry_identity, "output-invalid", [error.code]))
            saved = self.store.fail(job, error.code, terminal=True, result=failure_result)
            return {"status": saved["status"], "taskId": job["taskId"], "reason": error.code}
        except AIResearchWait as wait:
            saved = self.store.defer_research(job, wait)
            return {**wait.result(), "taskId": job["taskId"], **({} if saved else {"status": "lease-lost"})}
        except AIControlBudgetWait as wait:
            saved = self.store.defer_budget(job, wait)
            return {**wait.result(), "taskId": job["taskId"], **({} if saved else {"status": "lease-lost"})}
        except AIExecutionDeferred as wait:
            saved = self.store.defer_execution(job, wait)
            return {**wait.result(), "taskId": job["taskId"], **({} if saved else {"status": "lease-lost"})}
        except Exception as error:
            # Persist a safe category, never raw provider/credential-bearing errors.
            reason, diagnostic = task_failure(stage, error)
            if isinstance(error, (EvidenceContractError, EvidenceReadError, AIExecutionError)):
                reason = error.code
            if isinstance(error, (EvidenceContractError, EvidenceReadError)):
                diagnostic.update(category="evidence-read" if isinstance(error, EvidenceReadError) else "evidence-contract",
                                  reasonCode=error.code)
            failure_result = {"failure": diagnostic}
            stable_failure = ("packet" in locals() and lanes.identified(packet) and "retry_identity" in locals()
                              and (diagnostic.get("category") == "validation" or isinstance(error, EvidenceContractError)))
            if stable_failure:
                failure_result.update(input={"executionLane": packet["executionLane"]},
                    processingOutcome=disposition(packet, retry_identity, "output-invalid", [reason]))
            saved = self.store.fail(job, reason, **({"terminal": True} if stable_failure else {}), result=failure_result)
            status = saved.get("status") if isinstance(saved, dict) else "pending"
            return {"status": "deferred" if status == "pending" else status, "taskId": job["taskId"], "reason": reason}

    @staticmethod
    def subject(job):
        return {key: job[key] for key in ("accountId", "symbol", "name", "worldId")}

    def complete_wait(self, job, packet, outcome, trace, status="awaiting-evidence"):
        # No authored judgment, case resolution, hypothesis or notification is
        # manufactured from an incomplete read. Only the leased task advances.
        if lanes.market(packet):
            outcome = {**outcome, "retryAt": lanes.wait_until(packet), "repeatPolicy": "market-material-change-only-v1"}
        elif status == "unchanged-wait":
            outcome = {**outcome, "retryAt": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
                       "repeatPolicy": "source-change-only-v1"}
        result = {"status": status, "processingOutcome": outcome, "input": packet,
                  "retrievalInputIds": [row["inputId"] for row in trace],
                  "followUpEvaluations": packet.get("followUpEvaluations", []),
                  "resumption": observation_resume(job, packet)}
        child = {**self.subject(job), "capability": job["capability"], "watchQuestions": job.get("watchQuestions", []),
                 "taskId": identity(job["taskId"], "wait"), "availableAt": outcome["retryAt"]}
        children = [child]
        if status != "unchanged-wait" and outcome.get("status") == "data-wait" and outcome.get("requirements"):
            from ..domain.research_work import PRIORITY
            children.append({**self.subject(job), "capability": "research-plan", "priority": PRIORITY,
                "taskId": identity("research-gap-v1", job["accountId"], job["symbol"], job["worldId"], outcome["retryKey"]),
                "researchGaps": list(outcome["requirements"])[:4], "availableAt": stamp()})
        saved = self.store.complete(job, result, children)
        return {"status": status if saved else "lease-lost", "taskId": job["taskId"]}
