"""Durable independent research loop, with capabilities supplied by composition."""
from datetime import datetime, timedelta, timezone
from digital_twin.modules.ai_orchestration.domain.continuity import judgment_continuity
from digital_twin.modules.reasoning.contracts import EvidenceContractError, EvidenceReadError, ObservationEvidenceSession
from digital_twin.modules.ai_orchestration.application.retrieval import retrieve_evidence, RetrievalLeaseLost, RetrievalContractFailure
from digital_twin.modules.ai_orchestration.domain.planning import enabled, identity, stamp, validate_plan, observation_fingerprint
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_review_input, freeze_repair_input, PROMPT_VERSION
from digital_twin.modules.ai_orchestration.domain.insight_repair import correction_warranted
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality, accept_review
from digital_twin.modules.ai_orchestration.domain.observation_wording import OBSERVATION_WORDING_VERSION
from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait
from digital_twin.modules.outcomes.contracts import evaluate_observation_conditions


class AIControlService:
    def __init__(self, store, subjects, evidence, planner, researcher, research_memory, settings=None, delivery_memory=None, reviewer=None, development_memory=None, brain_memory=None, brain_waker=None, read_planner=None, evidence_waker=None, read_round_budget=None):
        self.store, self.subjects, self.evidence = store, subjects, evidence
        self.planner, self.researcher, self.research_memory = planner, researcher, research_memory
        self.settings = dict(settings or {})
        self.delivery_memory = delivery_memory or (lambda account, symbol: {})
        self.reviewer = reviewer
        self.development_memory = development_memory or (lambda account, symbol: [])
        self.brain_memory = brain_memory or (lambda account, symbol, world: [])
        self.brain_waker = brain_waker or (lambda subjects: None)
        self.read_planner = read_planner
        self.evidence_waker = evidence_waker or (lambda subjects: None)
        self.read_round_budget = read_round_budget or (lambda: 3)

    def run_once(self):
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
        try:
            current_subject = next((subject for subject in subjects
                if all(subject[key] == job[key] for key in ("accountId", "symbol", "worldId"))), None)
            if current_subject is None:
                self.store.complete(job, {"status": "retired", "reason": "subject-no-longer-observed"}, [])
                return {"status": "retired", "taskId": job["taskId"]}
            with self.store.keep_alive(job):
                if job["capability"] == "observe":
                    capture = self.evidence({**job, "marketWorldId": current_subject.get("marketWorldId", "")})
                    session = capture if isinstance(capture, ObservationEvidenceSession) else None
                    packet = session.packet() if session else capture
                    if not packet.get("facts") or not packet.get("sourceSnapshotId"):
                        raise ValueError("current verified graph facts unavailable")
                    history = self.store.memory(job["accountId"], job["symbol"])
                    history = [row for row in history if not row.get("worldId") or row["worldId"] == job["worldId"]]
                    research = list(self.brain_memory(job["accountId"], job["symbol"], job["worldId"]))
                    research.extend(self.research_memory(job["accountId"], job["symbol"]))
                    research.extend(self.development_memory(job["accountId"], job["symbol"]))
                    packet["taskId"] = job["taskId"]
                    if job.get("evidenceWake"):
                        packet["evidenceWake"] = job["evidenceWake"]
                    packet["questionsToCheck"] = job.get("watchQuestions", [])
                    packet["lastDeliveredNotification"] = self.delivery_memory(job["accountId"], job["symbol"])
                    packet["followUpEvaluations"] = evaluate_observation_conditions(packet, packet["lastDeliveredNotification"],
                        (history[0] if history else {}).get("followUpEvaluations", []))
                    fingerprint = observation_fingerprint(packet, research)
                    previous = history[0] if history else {}
                    from digital_twin.modules.ai_orchestration.domain.brain_management import due_memory
                    if (not due_memory(research) and previous.get("inputFingerprint") == fingerprint and previous.get("observedAt")
                            and previous.get("executionPromptVersion") == PROMPT_VERSION
                            and previous.get("quality", {}).get("status") in {"accepted", "observation-only"}
                            and not any(row.get("transitionVerified") for row in packet["followUpEvaluations"])):
                        age = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["observedAt"].replace("Z", "+00:00"))).total_seconds()
                        if 0 <= age < 21600:
                            due = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
                            child = {**self.subject(job), "capability": "observe", "watchQuestions": job.get("watchQuestions", []), "taskId": identity(job["taskId"], "unchanged"), "availableAt": due}
                            saved = self.store.complete(job, {"status": "unchanged", "reason": "새 근거가 없어 AI 호출을 생략했습니다.",
                                "followUpEvaluations": packet["followUpEvaluations"]}, [child])
                            return {"status": "unchanged" if saved else "lease-lost", "taskId": job["taskId"]}
                    retrieval_trace = []
                    if session and self.read_planner:
                        packet, history, research, retrieval_trace = retrieve_evidence(session, packet, history, research,
                            self.read_planner, lambda value: self.store.save_execution_input(job, value),
                            self.settings.get("aiObservationPromptMaxBytes", 256 * 1024), self.read_round_budget(),
                            record_round=lambda step: self.store.save_retrieval_round(job, step))
                    envelope = freeze_execution_input(packet, history, research,
                        max_prompt_bytes=self.settings.get("aiObservationPromptMaxBytes", 256 * 1024), retrieval_trace=retrieval_trace)
                    input_id = self.store.save_execution_input(job, envelope)
                    if not input_id:
                        return {"status": "lease-lost", "taskId": job["taskId"]}
                    raw = self.planner(envelope, input_id)
                    plan = validate_plan(raw, packet, envelope["researchResults"], require_research=True)
                    result = {**plan, "input": packet, "inputFingerprint": fingerprint, "observedAt": stamp(),
                              "executionInputId": input_id, "executionPromptVersion": PROMPT_VERSION,
                              "wordingVersion": OBSERVATION_WORDING_VERSION,
                              "memoryCoverage": envelope["memoryCoverage"],
                              "judgmentContinuity": judgment_continuity(envelope),
                              "followUpEvaluations": packet["followUpEvaluations"],
                              "comparisonFacts": [fact for previous in envelope["previousAnalyses"] for fact in previous.get("previousFacts", [])[:20]]}
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
                            except AIControlBudgetWait:
                                raise
                            except Exception:
                                result["quality"].update(status="rejected", errors=["독립 검토를 완료하지 못해 발송을 보류했습니다."])
                        if (verification or packet.get("retrieval", {}).get("status") in {"deferred", "repeated-read", "context-budget"}
                                or not correction_warranted(result)):
                            break
                        result["repair"] = {"initialInputId": input_id, "initialErrors": result["quality"]["errors"], "status": "pending"}
                        try:
                            correction = freeze_repair_input(envelope, raw, result["quality"]["errors"], input_id, result["quality"].get("review"))
                            repair_id = self.store.save_execution_input(job, correction)
                            if not repair_id:
                                return {"status": "lease-lost", "taskId": job["taskId"]}
                            result["repair"]["inputId"] = repair_id
                            repaired = validate_plan(self.planner(correction, repair_id), packet, correction["researchResults"], require_research=True)
                            result.update(repaired, executionInputId=repair_id, observedAt=stamp(), memoryCoverage=correction["memoryCoverage"],
                                          judgmentContinuity=judgment_continuity(correction))
                        except AIControlBudgetWait:
                            raise
                        except Exception as error:
                            result["repair"].update(status="failed", errorKind=type(error).__name__)
                            break
                    if result.get("repair", {}).get("status") == "pending":
                        result["repair"]["status"] = result["quality"]["status"]
                    children = []
                    for question in result["researchQuestions"]:
                        request = next((item.get("researchRequest", {}) for item in result["workQuestions"]
                                        if item["question"] == question and item["capability"] == "research"), {})
                        children.append({**self.subject(job), "capability": "research", "priority": 5, "question": question,
                                         "researchRequest": request,
                                         "taskId": identity(job["taskId"], question), "availableAt": stamp()})
                    due = (datetime.now(timezone.utc) + timedelta(minutes=result["nextCheckMinutes"])).isoformat().replace("+00:00", "Z")
                    children.append({**self.subject(job), "capability": "observe", "watchQuestions": result["questions"], "taskId": identity(job["taskId"], "next"), "availableAt": due})
                elif job["capability"] == "research":
                    result = self.researcher(job)
                    children = []
                else:
                    raise ValueError("unsupported AI capability")
                if not self.store.complete(job, result, children):
                    return {"status": "lease-lost", "taskId": job["taskId"]}
            return {"status": "completed", "taskId": job["taskId"], "capability": job["capability"]}
        except RetrievalLeaseLost:
            return {"status": "lease-lost", "taskId": job["taskId"]}
        except RetrievalContractFailure as error:
            saved = self.store.fail(job, error.code, terminal=True, result={"failure": error.failure})
            return {"status": saved["status"], "taskId": job["taskId"], "reason": error.code}
        except AIControlBudgetWait as wait:
            saved = self.store.defer_budget(job, wait)
            return {**wait.result(), "taskId": job["taskId"], **({} if saved else {"status": "lease-lost"})}
        except Exception as error:
            # Persist a safe category, never raw provider/credential-bearing errors.
            reason = error.code if isinstance(error, (EvidenceContractError, EvidenceReadError)) else type(error).__name__
            saved = self.store.fail(job, reason)
            status = saved.get("status") if isinstance(saved, dict) else "pending"
            return {"status": "deferred" if status == "pending" else status, "taskId": job["taskId"], "reason": reason}

    @staticmethod
    def subject(job):
        return {key: job[key] for key in ("accountId", "symbol", "name", "worldId")}
