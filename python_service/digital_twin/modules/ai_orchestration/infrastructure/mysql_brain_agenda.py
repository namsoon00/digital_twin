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
from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input, PROMPT_VERSION, REPAIR_PROMPT_VERSION
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.modules.ai_orchestration.domain.recovery import retry_delays


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

    @staticmethod
    def save(connection, case, task_id, stage, details=None):
        now = stamp()
        case["revision"] += 1
        case["updatedAt"] = now
        event = {"eventId": identity(case["caseId"], task_id, stage, case["revision"]),
            "caseId": case["caseId"], "taskId": task_id, "stage": stage, "at": now,
            "revision": case["revision"], "status": case["status"], "reason": case.get("reason", ""),
            "details": copy.deepcopy(details or {})}
        connection.execute("INSERT INTO ai_brain_cases (case_id,account_id,symbol,kind,status,next_check_at,payload_json,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE status=VALUES(status),"
            "next_check_at=VALUES(next_check_at),payload_json=VALUES(payload_json),updated_at=VALUES(updated_at)",
            (case["caseId"], case["accountId"], case["symbol"], case.get("kind", "question"), case["status"],
             case.get("nextCheckAt", ""), dumps(case), case["createdAt"], now))
        connection.execute("INSERT INTO ai_brain_case_events (event_id,case_id,task_id,payload_json,created_at) VALUES (%s,%s,%s,%s,%s)",
            (event["eventId"], case["caseId"], task_id, dumps(event), now))

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
        if envelope["promptVersion"] not in {PROMPT_VERSION, REPAIR_PROMPT_VERSION} or envelope["current"] != result["input"]:
            raise ValueError("brain memory input mismatch")
        if any(envelope["current"].get(key) != job[key] for key in ("accountId", "symbol", "worldId", "taskId")):
            raise ValueError("brain memory subject mismatch")
        raw = {"caseReviews": [{key: value for key, value in item.items() if key != "expectedRevision"}
                               for item in result.get("caseReviews", [])], "serviceFeedback": [
            {key: value for key, value in item.items() if key != "qualification"} for item in result.get("serviceFeedback", [])]}
        validated = validate_management(raw, result["input"], envelope["researchResults"])
        if any(validated[key] != result.get(key, []) for key in validated):
            raise ValueError("brain assessment changed after captured memory validation")

    def record(self, connection, job, result, children):
        if job["capability"] == "research":
            return self.research_completed(connection, job, result)
        if not result.get("summary"):
            return {"status": "no-assessment"}
        self.proof(connection, job, result)
        now = stamp()
        source = source_memory(job, result)
        feedback_ids = self.feedback(connection, job, result, source, now)
        receipt = {"status": "recorded", "caseIds": [], "feedbackIds": feedback_ids, "deferred": []}
        if result.get("quality", {}).get("status") not in {"accepted", "observation-only"} or local_quality(result)["errors"]:
            children[:] = [child for child in children if child["capability"] != "research"]
            for review in result.get("caseReviews", []):
                case = self.read(connection, review["caseId"], job["accountId"], job["symbol"], lock=True)
                if case and case["revision"] == review["expectedRevision"] and case["status"] in ACTIVE_CASE_STATES:
                    case.update(nextCheckAt=later(now, 180), reason="이번 관찰의 근거 검증이 보류되어 과제 평가를 다시 확인합니다.")
                    self.save(connection, case, job["taskId"], "review-deferred", {"executionInputId": result["executionInputId"]})
            return {**receipt, "status": "quality-blocked"}
        for review in result.get("caseReviews", []):
            case = self.read(connection, review["caseId"], job["accountId"], job["symbol"], lock=True)
            if not case or case["worldId"] != job["worldId"]:
                raise ValueError("brain case scope mismatch")
            case = review_transition(case, review, source, now)
            if review["action"] == "research":
                self.schedule_research(connection, case, job, result, children, now)
            self.save(connection, case, job["taskId"], "assessment", case["lastAssessment"])
            receipt["caseIds"].append(case["caseId"])
        for item in result.get("workQuestions", []):
            key = case_identity(job["accountId"], job["symbol"], job["worldId"], item["capability"], item["question"])
            case = self.read(connection, key, job["accountId"], job["symbol"], lock=True)
            matching = [child for child in children if child["capability"] == "research" and child.get("question") == item["question"]]
            children[:] = [child for child in children if child not in matching]
            if case:
                receipt["caseIds"].append(key)
                continue  # Existing questions require a captured caseReview; they cannot reset their history.
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
                case["development"] = copy.deepcopy(result.get("development", {}))
                case["status"] = "waiting"
            self.save(connection, case, job["taskId"], "created", {"executionInputId": source["executionInputId"],
                "evidenceIds": [row["id"] for row in source["evidence"]]})
            receipt["caseIds"].append(key)
        receipt["caseIds"] = list(dict.fromkeys(receipt["caseIds"]))
        return receipt

    @staticmethod
    def schedule_research(connection, case, job, result, children, now):
        fingerprint = evidence_change_identity(result["input"], [])
        previous = case.get("lastResearch", {})
        row = connection.execute("SELECT status FROM ai_control_tasks WHERE task_id=%s", (previous.get("taskId", ""),)).fetchone()
        reason = ("기존 조사가 진행 중입니다." if row and row["status"] in {"pending", "processing"} else
                  "조사 횟수 한도에 도달해 추가 자료나 기능 검토가 필요합니다." if case["researchAttempts"] >= 3 else
                  "같은 근거로 조사를 반복하지 않고 새 자료를 기다립니다." if previous.get("inputFingerprint") == fingerprint else
                  "조사 재시도 간격을 기다립니다." if previous.get("requestedAt") and later(previous["requestedAt"], 360) > now else "")
        if reason:
            case.update(status="blocked" if case["researchAttempts"] >= 3 else "waiting", reason=reason,
                        nextCheckAt=later(now, 1440 if case["researchAttempts"] >= 3 else 360))
            return
        task_id = identity(case["caseId"], "research", case["researchAttempts"] + 1)
        children.append({**{key: job[key] for key in ("accountId", "symbol", "worldId", "name")},
            "taskId": task_id, "capability": "research", "question": case["question"], "brainCaseId": case["caseId"],
            "researchRequest": copy.deepcopy(case.get("researchRequest", {})),
            "priority": 5, "availableAt": now})
        case.update(status="waiting", researchAttempts=case["researchAttempts"] + 1,
            lastResearch={"taskId": task_id, "requestedAt": now, "inputFingerprint": fingerprint},
            reason="원래 질문에 연결된 원문 조사를 예약했습니다.")

    @staticmethod
    def wake_observation(connection, account, symbol, world, due):
        connection.execute("UPDATE ai_control_tasks SET available_at=LEAST(available_at,%s),priority=GREATEST(priority,3) "
            "WHERE account_id=%s AND symbol=%s AND capability='observe' AND status='pending' AND attempts=0 "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s", (due, account, symbol, world))

    def research_completed(self, connection, job, result):
        key = job.get("brainCaseId")
        if not key:
            return {"status": "unlinked-research"}
        case = self.read(connection, key, job["accountId"], job["symbol"], lock=True)
        if not case or case["worldId"] != job["worldId"] or case.get("lastResearch", {}).get("taskId") != job["taskId"]:
            raise ValueError("research completion must own its brain case")
        if case["status"] not in ACTIVE_CASE_STATES:
            return {"status": "case-closed", "caseIds": [key]}
        reason = "조사가 실패해 원래 질문과 자료 경로를 다시 검토합니다." if result.get("status") == "failed" else "조사 처리가 끝났습니다. 원래 질문의 해결 여부는 다음 관찰에서 검토합니다."
        case.update(status="review-needed", nextCheckAt=stamp(), reason=reason)
        case["lastResearch"]["result"] = {key: copy.deepcopy(result[key]) for key in (
            "runId", "status", "stopReason", "changedEvidenceCount", "completedAt", "questionAssessment") if key in result}
        self.save(connection, case, job["taskId"], "research-returned", case["lastResearch"])
        self.wake_observation(connection, job["accountId"], job["symbol"], job["worldId"], case["nextCheckAt"])
        return {"status": "review-needed", "caseIds": [case["caseId"]]}

    def failed(self, connection, job, error_kind):
        if job.get("brainCaseId"):
            self.research_completed(connection, job, {"status": "failed", "stopReason": error_kind})
        elif job["capability"] == "observe":
            now = stamp()
            recovery_minutes = retry_delays(job["capability"], error_kind, 3)[1] // 60
            rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s AND kind='question' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s "
                "AND status IN ('open','waiting','review-needed','blocked') AND next_check_at<=%s FOR UPDATE",
                (job["accountId"], job["symbol"], job["worldId"], now)).fetchall()
            for row in rows:
                case = json.loads(row["payload_json"])
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

    def memory(self, account, symbol, world=None):
        now = stamp()
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s AND kind='question' "
                "AND (%s IS NULL OR JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s) "
                "AND status IN ('open','waiting','review-needed','blocked') ORDER BY next_check_at,created_at,case_id LIMIT 5", (account, symbol, world, world)).fetchall()
            feedback = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s AND kind='service-feedback' "
                "ORDER BY updated_at DESC LIMIT 3", (account, symbol)).fetchall()
        records = []
        for row in rows:
            case = json.loads(row["payload_json"])
            origin = case["origin"]
            records.append({**{key: case[key] for key in ("caseId", "accountId", "symbol", "worldId", "question", "capability", "status", "revision", "nextCheckAt", "completionCriterion", "reason")},
                "kind": "brain-case", "reviewDue": case["nextCheckAt"] <= now,
                "origin": {key: origin[key] for key in ("taskId", "executionInputId", "capturedAt", "summary", "hypothesis", "counterEvidence", "quality")},
                "lastAssessment": case.get("lastAssessment", {}), "lastResearch": case.get("lastResearch", {}),
                "researchAttempts": case["researchAttempts"], "development": case.get("development", {}),
                "researchRequest": case.get("researchRequest", {}),
                "authority": "historical-work-status-only"})
        for row in feedback:
            case = json.loads(row["payload_json"])
            records.append({**{key: case[key] for key in ("caseId", "category", "status", "problem", "proposal", "verification")},
                            "kind": "service-feedback", "ownerReview": case.get("ownerReview", {}), "authority": "unverified-proposal"})
        return records

    def wake_due(self, subjects):
        scopes = {(item["accountId"], item["symbol"], item["worldId"]) for item in subjects}
        if not scopes:
            return
        with self.transaction() as connection:
            rows = connection.execute("SELECT account_id,symbol,JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId')) AS world_id,"
                "MIN(next_check_at) AS due FROM ai_brain_cases WHERE kind='question' "
                "AND status IN ('open','waiting','review-needed','blocked') AND next_check_at<=%s "
                "AND (account_id,symbol,JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))) IN ("
                + ",".join(["(%s,%s,%s)"] * len(scopes)) + ") GROUP BY account_id,symbol,world_id",
                (stamp(), *(value for scope in sorted(scopes) for value in scope))).fetchall()
            for row in rows:
                self.wake_observation(connection, row["account_id"], row["symbol"], row["world_id"], row["due"])

    def status(self, account=""):
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE (%s='' OR account_id=%s) "
                "ORDER BY status IN ('open','waiting','review-needed','blocked','proposed','planned') DESC,updated_at DESC LIMIT 40", (account, account)).fetchall()
            cases = [json.loads(row["payload_json"]) for row in rows]
            if cases:
                ids = [row["caseId"] for row in cases]
                events = connection.execute("SELECT case_id,payload_json FROM (SELECT case_id,payload_json,"
                    "ROW_NUMBER() OVER (PARTITION BY case_id ORDER BY created_at DESC,event_id DESC) AS rank_in_case "
                    "FROM ai_brain_case_events WHERE case_id IN (" + ",".join(["%s"] * len(ids)) + ")) ranked "
                    "WHERE rank_in_case<=5 ORDER BY case_id,rank_in_case", tuple(ids)).fetchall()
                for case in cases:
                    case["history"] = [json.loads(row["payload_json"]) for row in events if row["case_id"] == case["caseId"]][:5]
        return {"goals": list(GOALS), "cases": cases, "scope": "최근 및 진행 중인 과제 최대 40건",
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
