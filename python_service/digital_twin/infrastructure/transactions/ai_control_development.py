"""Observation-to-experiment handoff; no graph writes or release authority."""
import gzip
import json

from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input, PROMPT_VERSION, REPAIR_PROMPT_VERSION
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.modules.model_registry.domain.observation_development import observation_development_request


class AIControlDevelopment:
    def __init__(self, research_store, case_reader):
        self.research, self.case_reader = research_store, case_reader

    def record(self, connection, task, result):
        if not result.get("developmentQuestions"):
            return {"status": "not-requested"}
        if result.get("quality", {}).get("status") not in {"accepted", "observation-only"} or local_quality(result)["errors"]:
            return {"status": "blocked", "reason": "관찰 근거 검증을 통과한 뒤 개선 요청을 등록합니다."}
        row = connection.execute("SELECT i.artifact_gzip FROM ai_control_inputs i "
            "JOIN ai_control_input_calls l ON l.input_id=i.input_id "
            "JOIN ai_control_calls c ON c.call_id=l.call_id AND c.task_id=i.task_id AND c.prompt_hash=i.prompt_hash "
            "WHERE i.input_id=%s AND i.task_id=%s AND i.attempt=%s AND c.status='completed' LIMIT 1",
            (result.get("executionInputId", ""), task["taskId"], task["attempts"])).fetchone()
        if not row:
            raise ValueError("observation development requires completed frozen model input")
        envelope = json.loads(gzip.decompress(row["artifact_gzip"]))
        validate_execution_input(envelope)
        if (envelope["promptVersion"] not in {PROMPT_VERSION, REPAIR_PROMPT_VERSION}
                or envelope["current"] != result["input"]):
            raise ValueError("observation development input mismatch")
        return self.research.enqueue_observation_development_with_connection(connection, observation_development_request(task, result))

    def memory(self, account_id, symbol):
        memories = []
        for row in self.research.observation_development_records(account_id, symbol):
            request, result = row["request"], row["result"]
            if request.get("accountId") != account_id or request.get("symbol") != symbol:
                raise ValueError("development memory subject mismatch")
            cases = []
            for entry in result.get("hypothesisDevelopment", [])[:3]:
                case_id = entry.get("caseId") or entry.get("case", {}).get("caseId")
                case = self.case_reader(case_id) if case_id else None
                if case is not None:
                    if case.account_id != account_id or case.symbol != symbol:
                        raise ValueError("development case subject mismatch")
                    cases.append({"caseId": case.case_id, "status": case.status,
                        "blockedReason": case.blocked_reason, "evolutionState": case.evolution.get("state", ""),
                        "validation": case.to_dict().get("validationSummary", {})})
                elif case_id:
                    cases.append({"caseId": case_id, "status": "unavailable"})
                else:
                    cases.append({"status": entry.get("status", "unavailable")})
            memories.append({"kind": "ontology-development", "requestId": row["requestId"],
                "status": row["status"], "resultStatus": result.get("status", ""),
                "question": request.get("question", {}).get("text", ""),
                "sourceCapturedAt": request["observationContext"]["packet"]["capturedAt"],
                "proposalCount": result.get("proposalCount", 0), "cases": cases,
                "authority": "experiment-status-only"})
        return memories
