"""Durable scoped agenda; task completion, work handoff and memory commit together."""
import copy
import gzip
import json

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.modules.ai_orchestration.domain.planning import identity, stamp
from digital_twin.modules.reasoning.contracts import evidence_change_identity
from digital_twin.modules.ai_orchestration.domain.brain_management import (
    ACTIVE_CASE_STATES, GOALS, case_identity, later, new_case, review_transition, source_memory, validate_management,
)
from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input, PROMPT_VERSION, REPAIR_PROMPT_VERSION, LANE_PROMPT_VERSION, LANE_REPAIR_PROMPT_VERSION
from ..domain import evidence_readiness, evidence_purpose
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.modules.ai_orchestration.domain.recovery import retry_delays
from .mysql_brain_subjects import fill_case_names
from ..domain import research_repeat as repeat


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ai_brain_cases (
    case_id VARCHAR(64) PRIMARY KEY, account_id VARCHAR(191) NOT NULL, symbol VARCHAR(64) NOT NULL,
    kind VARCHAR(32) NOT NULL, status VARCHAR(32) NOT NULL, next_check_at VARCHAR(40) NOT NULL,
    payload_json LONGTEXT NOT NULL, created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL,
    INDEX brain_subject(account_id,symbol,kind,status,next_check_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS ai_brain_case_events (
    event_id VARCHAR(64) PRIMARY KEY, case_id VARCHAR(64) NOT NULL, task_id VARCHAR(64) NOT NULL,
    payload_json LONGTEXT NOT NULL, created_at VARCHAR(40) NOT NULL,
    INDEX brain_case_history(case_id,created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class MySQLBrainAgendaStore(MySQLOperationalConnection):
    def __init__(self, settings=None):
        super().__init__(settings)
        with self.connect() as connection:
            for sql in SCHEMA:
                connection.execute(sql)

    @staticmethod
    def read(connection, case_id, account, symbol, lock=False):
        row = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE case_id=%s AND account_id=%s AND symbol=%s"
            + (" FOR UPDATE" if lock else ""), (case_id, account, symbol)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def save(self, connection, case, task_id, stage, details=None):
        return self.save_with_connection(connection, case, task_id, stage, details,
                                         getattr(self, "progress_writer", None))

    @staticmethod
    def save_with_connection(connection, case, task_id, stage, details=None, progress_writer=None):
        fill_case_names(connection, [case], task_id)
        now = stamp()
        case["revision"] += 1
        case["updatedAt"] = now
        event = {"eventId": identity(case["caseId"], task_id, stage, case["revision"]),
            "caseId": case["caseId"], "taskId": task_id, "stage": stage, "at": now,
            "revision": case["revision"], "status": case["status"], "reason": case.get("reason", ""),
            "details": copy.deepcopy(details or {})}
        progress = None
        if progress_writer is not None:
            from ..domain.research_progress import research_progress
            progress = research_progress(case, event)
        connection.execute("INSERT INTO ai_brain_cases (case_id,account_id,symbol,kind,status,next_check_at,payload_json,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE status=VALUES(status),"
            "next_check_at=VALUES(next_check_at),payload_json=VALUES(payload_json),updated_at=VALUES(updated_at)",
            (case["caseId"], case["accountId"], case["symbol"], case.get("kind", "question"), case["status"],
             case.get("nextCheckAt", ""), dumps(case), case["createdAt"], now))
        connection.execute("INSERT INTO ai_brain_case_events (event_id,case_id,task_id,payload_json,created_at) VALUES (%s,%s,%s,%s,%s)",
            (event["eventId"], case["caseId"], task_id, dumps(event), now))
        if progress:
            progress_writer(connection, progress)

    def refresh_research_material(self, case):
        reader = getattr(self, "research_source_reader", None)
        if reader:
            try:
                case["researchSourceState"] = reader(case)
            except Exception:
                # An unavailable read is not a changed source or permission to
                # repeatedly ask the model to diagnose the same outage.
                case["researchSourceState"] = {"status": "unavailable", "fingerprints": []}
        repeat.initialize(case)

    def refresh_research_route(self, case):
        reader = getattr(self, "research_route_reader", None)
        if reader:
            state = case.setdefault("researchSourceState", {})
            state.pop("sourceRouteOpportunity", None)
            try:
                opportunity = reader(case)
            except Exception:
                opportunity = {}
            if opportunity:
                state["sourceRouteOpportunity"] = opportunity

    def research_admission(self, job):
        if not job.get("brainCaseId"):
            return {"allowed": False, "reason": "research-case-required"}
        with self.connect() as connection:
            case = self.read(connection, job["brainCaseId"], job["accountId"], job["symbol"])
        if (not case or case["worldId"] != job["worldId"]
                or case.get("lastResearch", {}).get("taskId") != job["taskId"]
                or case["status"] not in ACTIVE_CASE_STATES):
            return {"allowed": False, "reason": "research-case-changed"}
        self.refresh_research_material(case)
        guard = case["researchRepeat"]
        permitted = guard.get("authorizedTaskId") == job["taskId"]
        initial = case.get("researchAttempts", 0) <= 1 and not case.get("latestSourceAnswer")
        from ..domain.research_work import source_answer_complete
        reason = ("answered" if source_answer_complete(case) else
                  "source-state-unavailable" if case.get("researchSourceState", {}).get("status") == "unavailable" else
                  "" if permitted or initial else "legacy-repeat-not-authorized")
        return {"allowed": not reason, "reason": reason, "version": repeat.VERSION,
            "sourceState": copy.deepcopy(case.get("researchSourceState", {})),
            "knownSourceAnswer": copy.deepcopy(case.get("latestSourceAnswer") or {})}

    @staticmethod
    def proof(connection, job, result):
        row = connection.execute("SELECT i.artifact_gzip FROM ai_control_inputs i JOIN ai_control_input_calls l ON l.input_id=i.input_id "
            "JOIN ai_control_calls c ON c.call_id=l.call_id AND c.task_id=i.task_id AND c.prompt_hash=i.prompt_hash "
            "WHERE i.input_id=%s AND i.task_id=%s AND i.attempt=%s AND c.status='completed' LIMIT 1",
            (result.get("executionInputId", ""), job["taskId"], job["attempts"])).fetchone()
        if not row:
            raise ValueError("brain memory requires completed frozen model input")
        envelope = json.loads(gzip.decompress(row["artifact_gzip"]))
        validate_execution_input(envelope)
        if envelope["promptVersion"] not in {evidence_purpose.author_version(envelope["current"]), evidence_purpose.repair_version(envelope["current"]), evidence_purpose.PROMPT_VERSION, evidence_purpose.REPAIR_VERSION, evidence_readiness.PROMPT_VERSION, evidence_readiness.REPAIR_VERSION, PROMPT_VERSION, REPAIR_PROMPT_VERSION, LANE_PROMPT_VERSION, LANE_REPAIR_PROMPT_VERSION} or envelope["current"] != result["input"]:
            raise ValueError("brain memory input mismatch")
        if any(envelope["current"].get(key) != job[key] for key in ("accountId", "symbol", "worldId", "taskId")):
            raise ValueError("brain memory subject mismatch")
        from ..domain.company_value import enabled as value_enabled, validate as validate_value
        if value_enabled(result["input"]):
            validate_value(result["input"])
            if result.get("companyValue") != result["input"]["companyValueContext"]:
                raise ValueError("company value assessment changed after capture")
        if result.get("businessResearch"):
            from ..domain.business_research import raw_business, validate_business
            if validate_business(raw_business(result["businessResearch"]), result["input"], envelope["researchResults"]) != result["businessResearch"]:
                raise ValueError("business assessment changed after capture")
        raw = {"caseReviews": [{key: value for key, value in item.items() if key != "expectedRevision"}
                               for item in result.get("caseReviews", [])], "serviceFeedback": [
            {key: value for key, value in item.items() if key != "qualification"} for item in result.get("serviceFeedback", [])]}
        validated = validate_management(raw, result["input"], envelope["researchResults"])
        if any(validated[key] != result.get(key, []) for key in validated):
            raise ValueError("brain assessment changed after captured memory validation")
        if "questionResolutions" in result:
            from ..domain.question_resolution import raw_resolutions, validate_resolutions
            if validate_resolutions(raw_resolutions(result["questionResolutions"]), result["input"],
                    envelope["researchResults"], result, required=True) != result["questionResolutions"]:
                raise ValueError("question resolution changed after input capture")
        from ..domain import research_chain
        if "questionContinuations" in result and not research_chain.enabled(result["input"]):
            raise ValueError("legacy assessment cannot originate an answer-led research chain")
        if research_chain.enabled(result["input"]):
            if research_chain.validate(result.get("questionContinuations"), result["input"],
                    envelope["researchResults"], result) != result.get("questionContinuations"):
                raise ValueError("research chain changed after input capture")
        from ..domain import question_purpose, question_source_handoff
        if question_purpose.enabled(result["input"]):
            question_source_handoff.validate(result["input"], envelope["researchResults"])
            if question_purpose.validate(result.get("questionPurposes"), result["input"], result) != result.get("questionPurposes"):
                raise ValueError("question purpose changed after capture")
        from ..domain.scoped_research import validate_plan as validate_scope
        validate_scope(result, result["input"])
        return envelope

    @staticmethod
    def research_proof(connection, job, result, author, section="assessment"):
        from ..domain import research_review
        quality = result.get("researchQuality") or {}
        from ..domain.research_review_parts import approved
        if not approved(result, section):
            return False
        row = connection.execute("SELECT i.artifact_gzip FROM ai_control_inputs i JOIN ai_control_input_calls l ON l.input_id=i.input_id "
            "JOIN ai_control_calls c ON c.call_id=l.call_id AND c.task_id=i.task_id AND c.prompt_hash=i.prompt_hash "
            "WHERE i.input_id=%s AND i.task_id=%s AND i.attempt=%s AND c.status='completed' LIMIT 1",
            (quality.get("reviewInputId", ""), job["taskId"], job["attempts"])).fetchone()
        if not row:
            raise ValueError("research review requires a completed frozen call")
        review = json.loads(gzip.decompress(row["artifact_gzip"]))
        research_review.validate_input(review)
        memories = [row for row in author["researchResults"] if row.get("kind") in {"brain-case", "business-thesis"}]
        if (review["current"] != result["input"] or review["parentInputId"] != result["executionInputId"]
                or review["proposal"] != research_review.proposal(result) or review["researchResults"] != memories
                or research_review.accept(result, quality.get("review"), quality["reviewInputId"]) != quality):
            raise ValueError("research review changed after capture")
        return True

    def record(self, connection, job, result, children):
        if job["capability"] == "research-plan":
            from .mysql_research_work import record_plan
            return record_plan(self, connection, job, result, children)
        if job["capability"] == "research":
            if result.get("status") == "research-wait":
                return self.research_waiting(connection, job, result)
            return self.research_completed(connection, job, result)
        if not result.get("summary"):
            return {"status": "no-assessment"}
        author = self.proof(connection, job, result)
        research_accepted = self.research_proof(connection, job, result, author)
        now = stamp()
        source = source_memory(job, result)
        feedback_ids = self.feedback(connection, job, result, source, now)
        receipt = {"status": "recorded", "caseIds": [], "feedbackIds": feedback_ids, "deferred": []}
        from ..domain.question_evidence import VERSION as evidence_policy
        question_review_required = (result["input"].get("questionEvidencePolicy") == evidence_policy
            and (any(row["action"] in {"answered", "dismissed"} for row in result.get("caseReviews", []))
                 or (result["input"].get("questionWorkPolicy") and result.get("caseReviews"))))
        from ..domain.research_review_parts import VERSION as separate_review_version
        separate_review_rejected = result.get("researchQuality", {}).get("version") == separate_review_version and not research_accepted
        if not research_accepted and (question_review_required or separate_review_rejected
                or result.get("quality", {}).get("status") not in {"accepted", "observation-only"} or local_quality(result)["errors"]):
            children[:] = [child for child in children if child["capability"] != "research"]
            for review in result.get("caseReviews", []):
                case = self.read(connection, review["caseId"], job["accountId"], job["symbol"], lock=True)
                if case and case["worldId"] == job["worldId"] and case["revision"] == review["expectedRevision"] and case["status"] in ACTIVE_CASE_STATES:
                    case.update(nextCheckAt=later(now, 180), reason=("원래 질문의 답변을 독립 검토에서 확인하지 못해 과제 평가를 보류합니다."
                        if question_review_required else "이번 관찰의 근거 검증이 보류되어 과제 평가를 다시 확인합니다."))
                    from .mysql_question_resolution import defer_resolution
                    defer_resolution(case, job, result, now)
                    if review["action"] == "research":
                        # Asking the source owner for missing evidence is not
                        # acceptance of the rejected market explanation.
                        from ..domain.research_request import validate_research_request
                        case["researchRequest"] = validate_research_request(review["research"], True, job["accountId"])
                        self.schedule_research(connection, case, job, result, children, now)
                    case["reviewBlocker"] = {"reason": case["reason"] if question_review_required else "투자 관찰의 품질 검사를 통과하지 못해 가설 판단을 보류했습니다.",
                        "errors": list(dict.fromkeys((["question-independent-review-required"] if question_review_required else [])
                            + (result.get("quality", {}).get("errors") or []) + local_quality(result)["errors"]))[:4]}
                    self.save(connection, case, job["taskId"], "review-deferred", {"executionInputId": result["executionInputId"],
                        "hypothesisResolution": case.get("hypothesisResolution", {}), "authority": "source-verification-only"})
            for review in (result.get("businessResearch") or {}).get("reviews", []):
                case = self.read(connection, review["thesisId"], job["accountId"], job["symbol"], lock=True)
                if case and case["worldId"] == job["worldId"] and case["revision"] == review["expectedRevision"]:
                    case.update(nextCheckAt=later(now, 1440), reason="사업 설명 검토가 보류되어 다음 관찰에서 다시 평가합니다.")
                    self.save(connection, case, job["taskId"], "business-review-deferred", {"executionInputId": result["executionInputId"]})
            self.record_questions(connection, job, result, children, source, now, receipt, research_only=True)
            return {**receipt, "status": "quality-blocked"}
        from .mysql_business_research import record_business
        receipt["business"] = record_business(connection, job, result, self.read, self.save, now)
        for review in result.get("caseReviews", []):
            case = self.read(connection, review["caseId"], job["accountId"], job["symbol"], lock=True)
            if not case or case["worldId"] != job["worldId"]:
                raise ValueError("brain case scope mismatch")
            case = review_transition(case, review, source, now)
            case.pop("reviewBlocker", None)
            if research_accepted:
                case["lastAssessment"]["researchReviewInputId"] = result["researchQuality"]["reviewInputId"]
            from .mysql_question_resolution import link_resolution
            link_resolution(connection, case, job, result, self.read, now)
            if review["action"] == "research":
                self.schedule_research(connection, case, job, result, children, now)
            self.save(connection, case, job["taskId"], "assessment", case["lastAssessment"])
            receipt["caseIds"].append(case["caseId"])
        self.record_questions(connection, job, result, children, source, now, receipt, research_only=research_accepted)
        receipt["caseIds"] = list(dict.fromkeys(receipt["caseIds"]))
        if research_accepted:
            receipt["researchReviewInputId"] = result["researchQuality"]["reviewInputId"]
            from ..domain.execution_lanes import identified, MARKET
            if identified(result["input"]) and result.get("caseReviews"):
                self.wake_observation(connection, job["accountId"], job["symbol"], job["worldId"], later(now, 15), capability=MARKET)
        return receipt

    def record_questions(self, connection, job, result, children, source, now, receipt, research_only=False):
        from ..domain import question_purpose
        from ..domain.research_review_parts import approved
        block_new_research = question_purpose.enabled(result["input"]) and not approved(result)
        if block_new_research:
            new_questions = {row["question"] for row in result.get("workQuestions", []) if row["capability"] == "research"}
            children[:] = [child for child in children if child.get("question") not in new_questions]
        for index, item in enumerate(result.get("workQuestions", [])):
            if block_new_research and item["capability"] == "research":
                continue
            if research_only and item["capability"] != "research":
                continue
            continuation = next((row for row in result.get("questionContinuations", []) if row["questionIndex"] == index), None)
            if continuation:
                from .mysql_research_chain import record_continuation
                record_continuation(self, connection, job, result, item, continuation, children, now, receipt)
                continue
            key = case_identity(job["accountId"], job["symbol"], job["worldId"], item["capability"], item["question"])
            case = self.read(connection, key, job["accountId"], job["symbol"], lock=True)
            matching = [child for child in children if child["capability"] == "research" and child.get("question") == item["question"]]
            children[:] = [child for child in children if child not in matching]
            if case:
                receipt["caseIds"].append(key)
                continue  # Existing questions require a captured caseReview; they cannot reset their history.
            if item["capability"] == "research":
                rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s "
                    "AND kind='question' AND status IN ('open','waiting','review-needed','blocked') "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s ORDER BY next_check_at LIMIT 12",
                    (job["accountId"], job["symbol"], job["worldId"])).fetchall()
                stalled = repeat.stalled_question([json.loads(row["payload_json"]) for row in rows], item.get("researchRequest"))
                if stalled:
                    receipt["deferred"].append({"question": item["question"], "reason": "existing-source-work-stalled", "caseId": stalled})
                    continue
            count = connection.execute("SELECT COUNT(*) AS count FROM ai_brain_cases WHERE account_id=%s AND symbol=%s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s "
                "AND kind='question' AND status IN ('open','waiting','review-needed','blocked')", (job["accountId"], job["symbol"], job["worldId"])).fetchone()["count"]
            if count >= 12:
                receipt["deferred"].append({"question": item["question"], "reason": "active-case-limit"})
                continue
            case = new_case(job, result, item["question"], item["capability"], now)
            if case["capability"] == "research":
                self.schedule_research(connection, case, job, result, children, now)
            if case["capability"] == "develop-hypothesis":
                development = result.get("development", {})
                if development.get("requestedQuestionMatched") is False:
                    case["hypothesisResolution"] = {"disposition": "defer", "state": "deferred-daily-budget",
                        "qualification": "not-requested", "at": now,
                        "reason": "같은 날 먼저 등록된 다른 질문의 실험이 있어 이번 질문의 개발은 보류했습니다."}
                    case["reason"] = case["hypothesisResolution"]["reason"]
                else:
                    case["development"] = copy.deepcopy(development)
                case["status"] = "waiting"
            self.save(connection, case, job["taskId"], "created", {"executionInputId": source["executionInputId"],
                "evidenceIds": [row["id"] for row in source["evidence"]]})
            receipt["caseIds"].append(key)

    def record_evidence_progress(self, connection, job, packet, trace):
        from .mysql_question_evidence import record_question_evidence
        return record_question_evidence(self, connection, job, packet, trace)

    @staticmethod
    def schedule_research(connection, case, job, result, children, now):
        from ..domain.research_work import source_answer_complete
        if source_answer_complete(case):
            case.update(reason="원래 질문의 자료 답변을 확보했습니다. 투자 가설 연결 여부는 별도로 검토합니다.", nextCheckAt=later(now, 1440))
            return
        previous = case.get("lastResearch", {})
        row = connection.execute("SELECT status,payload_json FROM ai_control_tasks WHERE task_id=%s", (previous.get("taskId", ""),)).fetchone()
        if row and row["status"] in {"pending", "processing"}:
            case.update(status="waiting", reason="기존 조사가 진행 중입니다.",
                        nextCheckAt=max(case.get("nextCheckAt", ""), later(now, repeat.WAIT_MINUTES)))
            return
        repeat.initialize(case)
        from ..domain.question_recovery import gap_opportunity
        recovery_key = gap_opportunity(case, "scheduled")
        from ..domain.research_route import opportunity as route_opportunity
        route_key = route_opportunity(case, "scheduled")
        new_material = set(repeat.materials(case)) - set(case["researchRepeat"].get("scheduledMaterials", []))
        ceiling = 6 if result.get("researchBasis") else 3
        repeated = "research-attempt-limit" if case.get("researchAttempts", 0) >= ceiling else repeat.admission(case, "scheduled")
        if repeated:
            case["researchRepeat"]["waitReason"] = repeated
            case.update(status="blocked" if repeated.endswith("-limit") else "waiting",
                        reason=repeat.WAIT_REASON, nextCheckAt=later(now, repeat.WAIT_MINUTES))
            return
        fingerprint = (result.get("researchBasis") or evidence_change_identity(result["input"], []))
        from ..domain.research_request import research_intent_identity
        intent = research_intent_identity(case.get("researchRequest"))
        previous_intent = previous.get("requestFingerprint") or research_intent_identity(
            json.loads(row["payload_json"]).get("researchRequest") if row else case.get("researchRequest"))
        retryable = previous.get("result", {}).get("status") in {"failed", "research-cooldown"}
        attempts = case.get("researchIntentAttempts", {}).get(intent, 0)
        reason = ("이미 시도한 조사 경로입니다. 보완 조사는 다른 출처나 조회 범위를 지정해야 합니다." if recovery_key and not route_key and not new_material and (intent == previous_intent or attempts) else
                  "조사 횟수 한도에 도달해 추가 자료나 기능 검토가 필요합니다." if case["researchAttempts"] >= ceiling or attempts >= 2 else
                  "기존 조사 한도를 사용했습니다. 다른 출처나 구체적으로 달라진 조사 계획이 필요합니다." if result.get("researchBasis") and case["researchAttempts"] >= 3 and previous_intent == intent and not route_key else
                  "같은 근거로 조사를 반복하지 않고 새 자료를 기다립니다." if previous.get("inputFingerprint") == fingerprint and previous_intent == intent and not retryable and not route_key else
                  "조사 재시도 간격을 기다립니다." if previous.get("requestedAt") and later(previous["requestedAt"], 360) > now else "")
        if reason:
            exhausted = case["researchAttempts"] >= ceiling or attempts >= 2
            case.update(status="blocked" if exhausted else "waiting", reason=reason,
                        nextCheckAt=max(case.get("nextCheckAt", ""), later(now, 10080 if exhausted else 360)))
            return
        task_id = identity(case["caseId"], "research", case["researchAttempts"] + 1)
        repeat.remember(case, "scheduled", task_id)
        answer = previous.get("result", {}).get("documentaryAnswer")
        if answer and answer.get("sources"):
            memories = case.setdefault("answerMemory", [])
            if not any(row.get("taskId") == previous.get("taskId") for row in memories):
                memories.append({"taskId": previous["taskId"], "answer": copy.deepcopy(answer)})
                case["answerMemory"] = memories[-3:]
        children.append({**{key: job[key] for key in ("accountId", "symbol", "worldId", "name")},
            "taskId": task_id, "capability": "research", "question": case["question"], "brainCaseId": case["caseId"],
            "researchRequest": copy.deepcopy(case.get("researchRequest", {})),
            **({"previousSourceRecovery": copy.deepcopy(previous["result"]["sourceRecovery"])}
               if previous.get("result", {}).get("sourceRecovery") else {}),
            **({"previousSourceRun": {"runId": previous["result"]["runId"], "taskId": previous["taskId"]}}
               if previous.get("taskId") and previous.get("result", {}).get("runId") else {}),
            **({"questionContract": copy.deepcopy(case["questionContract"])} if case.get("questionContract") else {}),
            **({"questionPurpose": copy.deepcopy(case["questionPurpose"])} if case.get("questionPurpose") else {}),
            **({"evidenceRequirements": copy.deepcopy((case.get("questionAssessment") or case.get("evidenceProgress", {}))["missingRequirements"])[:8]}
               if (recovery_key or route_key) and (case.get("questionAssessment") or case.get("evidenceProgress", {})).get("missingRequirements") else {}),
            **({"sourceRouteOpportunity": copy.deepcopy(case["researchSourceState"]["sourceRouteOpportunity"])} if route_key else {}),
            "priority": 5, "availableAt": now})
        case.update(status="waiting", researchAttempts=case["researchAttempts"] + 1,
            lastResearch={"taskId": task_id, "requestedAt": now, "inputFingerprint": fingerprint, "requestFingerprint": intent},
            reason="원래 질문에 연결된 원문 조사를 예약했습니다.")
        case.setdefault("researchIntentAttempts", {})[intent] = attempts + 1
        case["nextCheckAt"] = later(now, 360)

    @staticmethod
    def wake_observation(connection, account, symbol, world, due, changed=True, capability="observe"):
        return connection.execute("UPDATE ai_control_tasks SET available_at=LEAST(available_at,%s),priority=GREATEST(priority,3) "
            "WHERE account_id=%s AND symbol=%s AND capability=%s AND status='pending' AND attempts=0 "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s "
            "AND (%s OR created_at<=%s)", (due, account, symbol, capability, world, bool(changed), due)).rowcount

    def research_waiting(self, connection, job, result):
        key = job.get("brainCaseId")
        if not key:
            return {"status": "unlinked-research"}
        case = self.read(connection, key, job["accountId"], job["symbol"], lock=True)
        if not case or case["worldId"] != job["worldId"] or case.get("lastResearch", {}).get("taskId") != job["taskId"]:
            raise ValueError("research wait must own its brain case")
        if case["status"] not in ACTIVE_CASE_STATES:
            return {"status": "case-closed", "caseIds": [key]}
        case.update(status="waiting", nextCheckAt=result["nextCheckAt"], reason=result["explanation"])
        case["lastResearch"]["result"] = copy.deepcopy(result)
        self.save(connection, case, job["taskId"], "research-waiting", result)
        return {"status": "waiting", "caseIds": [key]}

    def research_completed(self, connection, job, result):
        key = job.get("brainCaseId")
        if not key:
            return {"status": "unlinked-research"}
        case = self.read(connection, key, job["accountId"], job["symbol"], lock=True)
        if result.get("status") == "research-repeat-suppressed" and (not case or case.get("lastResearch", {}).get("taskId") != job["taskId"]):
            return {"status": "obsolete", "caseIds": []}
        if not case or case["worldId"] != job["worldId"] or case.get("lastResearch", {}).get("taskId") != job["taskId"]:
            raise ValueError("research completion must own its brain case")
        if case["status"] not in ACTIVE_CASE_STATES:
            return {"status": "case-closed", "caseIds": [key]}
        admission = result.get("researchAdmission") or {}
        if admission.get("version") == repeat.VERSION:
            case["researchSourceState"] = copy.deepcopy(admission.get("sourceState", {}))
            repeat.initialize(case)
            repeat.remember(case, "scheduled", job["taskId"])
        if result.get("status") == "research-repeat-suppressed":
            repeat.initialize(case)
            repeat.remember(case, "planned")
            case["researchRepeat"]["waitReason"] = result.get("reason", "unchanged-research-material")
            case.update(status="waiting", nextCheckAt=later(stamp(), repeat.WAIT_MINUTES), reason=repeat.WAIT_REASON)
            # Keep previous source receipts intact. Suppression is an execution
            # audit, not a new unavailable answer or customer milestone.
            self.save(connection, case, job["taskId"], "research-repeat-suppressed", {"reason": result.get("reason")})
            return {"status": "research-repeat-suppressed", "caseIds": [key]}
        if result.get("status") == "failed" and not result.get("documentaryAnswer"):
            result = {**result, "documentaryAnswer": {"version": "documentary-answer-v1",
                "taskId": identity(job["taskId"], "sources"), "authority": "source-review-only",
                "status": "unavailable", "text": "자료 조사 실행에 실패해 답변을 만들지 못했습니다. 재시도 전에 수집 경로를 점검해야 합니다.",
                "sources": [], "missingRequirements": [], "blockers": ["research-execution-failed"]}}
        reason = "조사가 실패해 원래 질문과 자료 경로를 다시 검토합니다." if result.get("status") == "failed" else "조사 처리가 끝났습니다. 원래 질문의 해결 여부는 다음 관찰에서 검토합니다."
        previous_answer = copy.deepcopy(case.get("latestSourceAnswer") or case.get("lastResearch", {}).get("result", {}).get("documentaryAnswer") or {})
        case.update(status="review-needed", nextCheckAt=stamp(), reason=reason)
        case["lastResearch"]["result"] = {key: copy.deepcopy(result[key]) for key in (
            "runId", "status", "stopReason", "changedEvidenceCount", "completedAt", "questionAssessment", "documentaryAnswer", "sourceExploration", "sourceRecovery") if key in result}
        answer = result.get("documentaryAnswer") or {}
        if answer and (answer.get("taskId") != identity(job["taskId"], "sources")
                       or answer.get("authority") != "source-review-only"):
            raise ValueError("documentary answer must belong to the original source task")
        if answer:
            case["reason"] = "원래 질문의 자료 답변을 확보했습니다. 투자 가설 판단은 별도로 진행합니다." if answer.get("status") == "answered" else "확인한 답변을 보존하고 부족한 항목의 추가 조사 계획을 확인합니다."
            case.pop("reviewBlocker", None)
            from ..domain.research_work import answer_resolution
            resolution = answer_resolution(result, job["taskId"])
            if repeat.preserve_answer(case, answer):
                case["answerResolution"] = resolution
        self.save(connection, case, job["taskId"], "research-returned", case["lastResearch"])
        if not answer or repeat.answer_changed(previous_answer, answer):
            self.wake_observation(connection, job["accountId"], job["symbol"], job["worldId"], case["nextCheckAt"])
        return {"status": "review-needed", "caseIds": [case["caseId"]]}

    def failed(self, connection, job, error_kind):
        if job.get("brainCaseId"):
            self.research_completed(connection, job, {"status": "failed", "stopReason": error_kind})
        elif job["capability"] in {"observe", "research-plan"}:
            now = stamp()
            recovery_minutes = retry_delays(job["capability"], error_kind, 3)[1] // 60
            rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s AND kind IN ('question','business-thesis') "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s "
                "AND status IN ('open','waiting','review-needed','blocked','tracking','needs-review','data-needed') AND next_check_at<=%s FOR UPDATE",
                (job["accountId"], job["symbol"], job["worldId"], now)).fetchall()
            for row in rows:
                case = json.loads(row["payload_json"])
                if job["capability"] == "research-plan" and not self.source_work_pending(case):
                    continue
                case.update(nextCheckAt=later(now, recovery_minutes), reason="관찰 처리가 반복 실패해 복구 시점에 과제를 다시 확인합니다.")
                self.save(connection, case, job["taskId"], "review-failed", {"errorKind": error_kind})

    def feedback(self, connection, job, result, source, now):
        ids = []
        for proposal in result.get("serviceFeedback", []):
            # One open proposal per subject/category. Rewording cannot flood the owner or replace its evidence.
            row = connection.execute("SELECT case_id FROM ai_brain_cases WHERE account_id=%s AND symbol=%s "
                "AND kind='service-feedback' AND status IN ('proposed','planned') "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.category'))=%s LIMIT 1 FOR UPDATE",
                (job["accountId"], job["symbol"], proposal["category"])).fetchone()
            if row:
                ids.append(row["case_id"])
                continue
            key = identity("service-feedback-v1", job["accountId"], job["symbol"], proposal["category"], now[:10])
            if self.read(connection, key, job["accountId"], job["symbol"], lock=True):
                ids.append(key)
                continue
            case = {**copy.deepcopy(proposal), "caseId": key, "accountId": job["accountId"], "symbol": job["symbol"],
                "kind": "service-feedback", "status": "proposed", "revision": 0, "createdAt": now,
                "origin": source, "reason": proposal["problem"], "authority": "proposal-only"}
            self.save(connection, case, job["taskId"], "feedback-proposed", {"executionInputId": source["executionInputId"],
                "evidenceIds": proposal["evidenceIds"]})
            ids.append(key)
        return ids

    def market_memory(self, account, symbol, world):
        from .mysql_market_memory import market_memory
        return market_memory(self, account, symbol, world)

    def memory(self, account, symbol, world=None, research_only=False, max_cases=5):
        now = stamp()
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s AND kind='question' "
                "AND (%s IS NULL OR JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s) "
                "AND status IN ('open','waiting','review-needed','blocked') "
                + ("AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.capability'))='research' "
                   "AND (COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.answerResolution.status')),'')<>'answered' "
                   "OR JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.questionAssessment.status'))='partial') " if research_only else "")
                + "ORDER BY (COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.answerResolution.status')),'')='answered') DESC,"
                  "(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.capability')),'')='research') DESC,"
                + ("(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.evidenceProgress.status'))='waiting' "
                   "AND JSON_EXTRACT(payload_json,'$.researchRepeat.gapRecovery.planned') IS NULL) DESC," if research_only else "")
                + "next_check_at,created_at,case_id LIMIT %s", (account, symbol, world, world, max(1, min(12, int(max_cases))))).fetchall()
            feedback = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s AND kind='service-feedback' "
                "ORDER BY updated_at DESC LIMIT 3", (account, symbol)).fetchall()
            from .mysql_business_research import business_memories
            business = business_memories(connection, account, symbol, world, now)
        records = list(business)
        for row in rows:
            case = json.loads(row["payload_json"])
            origin = case["origin"]
            records.append({**{key: case[key] for key in ("caseId", "accountId", "symbol", "worldId", "question", "capability", "status", "revision", "nextCheckAt", "completionCriterion", "reason")},
                "kind": "brain-case", "reviewDue": case["nextCheckAt"] <= now and (research_only or not self.source_work_pending(case)),
                "origin": {key: origin[key] for key in ("taskId", "executionInputId", "capturedAt", "summary", "hypothesis", "counterEvidence", "quality")},
                "lastAssessment": case.get("lastAssessment", {}), "lastResearch": case.get("lastResearch", {}),
                "researchAttempts": case["researchAttempts"], "development": case.get("development", {}),
                "researchRequest": case.get("researchRequest", {}),
                "questionContract": case.get("questionContract", {}), "answerResolution": case.get("answerResolution", {}),
                **{key: copy.deepcopy(case[key]) for key in ("inquiryLineage", "inquiryContinuation", "questionPurpose", "questionSourceReferences") if key in case},
                **({"questionAssessment": case["questionAssessment"]} if case.get("questionAssessment") else {}),
                "researchPlan": case.get("researchPlan", {}),
                **({"evidenceProgress": case["evidenceProgress"]} if case.get("evidenceProgress") else {}),
                **({"researchRepeat": case.get("researchRepeat", {}), "researchSourceState": case.get("researchSourceState", {})}
                   if research_only else {}),
                "answerMemory": case.get("answerMemory", []),
                "latestSourceAnswer": case.get("latestSourceAnswer", {}), "mergedQuestions": case.get("mergedQuestions", []),
                "hypothesisResolution": case.get("hypothesisResolution", {}),
                "developmentProgress": case.get("developmentProgress", {}),
                "authority": "historical-work-status-only"})
        for row in feedback:
            case = json.loads(row["payload_json"])
            records.append({**{key: case[key] for key in ("caseId", "category", "status", "problem", "proposal", "verification")},
                            "kind": "service-feedback", "ownerReview": case.get("ownerReview", {}), "authority": "unverified-proposal"})
        return records

    @staticmethod
    def source_work_pending(case):
        from ..domain.research_work import needs_research_plan
        return needs_research_plan(case)

    def wake_due(self, subjects):
        scopes = {(item["accountId"], item["symbol"], item["worldId"]) for item in subjects}
        if not scopes:
            return
        with self.transaction() as connection:
            from .mysql_research_work import reconcile_and_schedule
            reconcile_and_schedule(self, connection, subjects)
            from ..domain.execution_lanes import enabled, MARKET
            lane = ("CASE WHEN kind='question' AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.capability'))='research' "
                    "THEN 'observe' ELSE '" + MARKET + "' END") if enabled(self.runtime_settings) else "'observe'"
            rows = connection.execute("SELECT account_id,symbol,JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId')) AS world_id,"
                "MIN(next_check_at) AS due," + lane + " AS lane FROM ai_brain_cases WHERE kind IN ('question','business-thesis') "
                "AND status IN ('open','waiting','review-needed','blocked','tracking','needs-review','data-needed') AND next_check_at<=%s "
                "AND NOT (kind='question' AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.capability')),'')='research' "
                "AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.answerResolution.status')),'')<>'answered') "
                "AND (account_id,symbol,JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))) IN ("
                + ",".join(["(%s,%s,%s)"] * len(scopes)) + ") GROUP BY account_id,symbol,world_id,lane",
                (stamp(), *(value for scope in sorted(scopes) for value in scope))).fetchall()
            for row in rows:
                self.wake_observation(connection, row["account_id"], row["symbol"], row["world_id"], row["due"], changed=False, capability=row["lane"])

    def status(self, account=""):
        with self.connect() as connection:
            metrics = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*) AS questions,"
                "SUM(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.answerResolution.status'))='answered') AS sourceAnswered,"
                "SUM(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.answerResolution.status'))='partial') AS sourcePartial,"
                "SUM(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.answerResolution.status'))='unavailable') AS sourceUnavailable,"
                "SUM(status='answered') AS finalAnswered,"
                "SUM(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.questionAssessment.status'))='partial') AS finalPartial,"
                "SUM(JSON_EXTRACT(payload_json,'$.researchPlan') IS NOT NULL) AS planned,"
                "SUM(JSON_EXTRACT(payload_json,'$.mergedInto') IS NOT NULL) AS merged "
                "FROM ai_brain_cases WHERE kind='question' AND (%s='' OR account_id=%s)", (account, account)).fetchone()
            rows = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ payload_json FROM ai_brain_cases WHERE (%s='' OR account_id=%s) "
                "ORDER BY status IN ('open','waiting','review-needed','blocked','proposed','planned','tracking','needs-review','data-needed') DESC,updated_at DESC LIMIT 40", (account, account)).fetchall()
            cases = [json.loads(row["payload_json"]) for row in rows]
            # A completed question must not disappear behind the active backlog
            # at the exact moment its answer/impact becomes available to read.
            completed = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ case_id FROM ai_brain_cases "
                "WHERE kind='question' AND (%s='' OR account_id=%s) "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.questionAssessment.status'))='answered' "
                "ORDER BY updated_at DESC,case_id LIMIT 8", (account, account)).fetchall()
            if completed:
                ids = [row["case_id"] for row in completed]
                recent = connection.execute("SELECT case_id,payload_json FROM ai_brain_cases WHERE case_id IN ("
                    + ",".join(["%s"] * len(ids)) + ")", tuple(ids)).fetchall()
                by_id = {row["case_id"]: json.loads(row["payload_json"]) for row in recent}
                cases = ([by_id[key] for key in ids] + [row for row in cases if row["caseId"] not in by_id])[:40]
            fill_case_names(connection, cases)
            from .mysql_question_progress import attach_question_progress
            attach_question_progress(connection, cases)
            if cases:
                ids = [row["caseId"] for row in cases]
                events = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ case_id,payload_json FROM (SELECT case_id,payload_json,"
                    "ROW_NUMBER() OVER (PARTITION BY case_id ORDER BY created_at DESC,event_id DESC) AS rank_in_case "
                    "FROM ai_brain_case_events WHERE case_id IN (" + ",".join(["%s"] * len(ids)) + ")) ranked "
                    "WHERE rank_in_case<=5 ORDER BY case_id,rank_in_case", tuple(ids)).fetchall()
                for case in cases:
                    case["history"] = [json.loads(row["payload_json"]) for row in events if row["case_id"] == case["caseId"]][:5]
        return {"goals": list(GOALS), "cases": cases, "scope": "최근 최종 답변 최대 8건을 포함한 과제 최대 40건",
                "researchMetrics": {key: int(value or 0) for key, value in metrics.items()},
                "qualification": "AI 답변과 개선 제안은 실증된 성과가 아닙니다."}

    def review_feedback(self, payload):
        required = {"caseId", "accountId", "symbol", "revision", "status", "note"}
        if not isinstance(payload, dict) or set(payload) != required or payload.get("status") not in {"planned", "implemented", "dismissed"}:
            raise ValueError("invalid feedback review")
        if any(not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= limit for key, limit in (("caseId", 64), ("accountId", 191), ("symbol", 64))) or type(payload["revision"]) is not int or payload["revision"] < 1:
            raise ValueError("invalid feedback identity or revision")
        if not isinstance(payload["note"], str) or not 8 <= len(payload["note"].strip()) <= 600:
            raise ValueError("feedback review requires a bounded reason")
        with self.transaction() as connection:
            case = self.read(connection, payload["caseId"], payload["accountId"], payload["symbol"], lock=True)
            if not case or case.get("kind") != "service-feedback" or case["revision"] != payload["revision"]:
                raise ValueError("feedback changed or is unavailable")
            case.update(status=payload["status"], reason=payload["note"].strip(), ownerReview={
                "note": payload["note"].strip(), "at": stamp(), "qualification": "owner-reported-not-empirical"})
            self.save(connection, case, "", "owner-review", case["ownerReview"])
        return {"saved": True, "caseId": case["caseId"], "revision": case["revision"]}
