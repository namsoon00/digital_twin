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

    def progress(self, request_id, account_id, symbol, world):
        record = self.research.observation_development_record(request_id, account_id, symbol, world)
        if record is None:
            return {"requestId": request_id, "status": "unavailable", "authority": "experiment-status-only"}
        result = record["result"]
        cases = []
        for entry in result.get("hypothesisDevelopment", [])[:3]:
            case_id = entry.get("caseId") or entry.get("case", {}).get("caseId")
            case = self.case_reader(case_id) if case_id else None
            if case is not None:
                if case.account_id != account_id or case.symbol != symbol:
                    raise ValueError("development progress subject mismatch")
                cases.append({"caseId": case.case_id, "status": case.status,
                    "blockedReason": str(case.blocked_reason)[:1200], "stage": case.stage,
                    "experimentId": case.experiment_id, "candidateRuleId": case.candidate_rule.get("id", ""),
                    "candidateId": case.candidate_id,
                    "evolutionState": case.evolution.get("state", ""),
                    "validation": case.to_dict().get("validationSummary", {}),
                    "dataShapeErrors": ["causal-path-character-array"] if len(getattr(case, "causal_path", [])) >= 4
                        and all(len(str(item).strip()) == 1 for item in case.causal_path) else []})
            else:
                cases.append({"caseId": case_id or "", "status": "unavailable" if case_id else entry.get("status", "unavailable"),
                    "reason": str(entry.get("reason", ""))[:500], "proposalId": entry.get("proposalId", "")})
        return {"requestId": request_id, "status": record["status"], "resultStatus": result.get("status", ""),
                "proposalCount": result.get("proposalCount"), "cases": cases,
                "authority": "experiment-status-only"}

    def restore_question(self, agenda, request_id, account, symbol, world):
        """Explicit maintenance: restore missing lineage, never replay old work."""
        from digital_twin.modules.model_registry.contracts import validate_observation_development_context
        from digital_twin.modules.ai_orchestration.domain.brain_management import case_identity
        from digital_twin.modules.ai_orchestration.domain.planning import stamp
        record = self.research.observation_development_record(request_id, account, symbol, world)
        if record is None:
            raise ValueError("development request unavailable")
        context = record["request"]["observationContext"]
        validate_observation_development_context(context, account, symbol)
        question = record["request"]["question"]["text"]
        key = case_identity(account, symbol, world, "develop-hypothesis", question)
        now = stamp()
        progress = self.progress(request_id, account, symbol, world)
        packet = context["packet"]
        case = {"caseId": key, "accountId": account, "symbol": symbol, "worldId": world,
            "kind": "question", "capability": "develop-hypothesis", "question": question,
            "status": "review-needed", "revision": 0, "createdAt": now, "nextCheckAt": now,
            "completionCriterion": "원래 질문과 가설 개발의 차단 이유를 현재 근거로 다시 판단합니다.",
            "reason": "과거 가설 개발 요청의 질문 연결을 복구했습니다. 과거 관측을 재실행한 결과가 아닙니다.",
            "researchAttempts": 0, "development": {"requestId": request_id}, "developmentProgress": progress,
            "hypothesisResolution": {"disposition": "experiment", "requestId": request_id,
                "state": "restored-reference", "at": now, "qualification": "proposal-only"},
            "origin": {"taskId": context["taskId"], "executionInputId": context["executionInputId"],
                "capturedAt": packet["capturedAt"], "sourceSnapshots": packet["sourceSnapshots"],
                "summary": question, "hypothesis": context["analysis"].get("hypothesis", ""),
                "counterEvidence": context["analysis"].get("counterEvidence", ""), "quality": "historical-development-request",
                "evidence": [row for row in packet["facts"] if row["id"] in context["evidenceIds"]]},
            "restoration": {"sourceRequestId": request_id, "sourceFingerprint": context["fingerprint"],
                            "historicalWorkReplayed": False}, "authority": "research-only"}
        with agenda.transaction() as connection:
            existing = agenda.read(connection, key, account, symbol, lock=True)
            if existing:
                return {"status": "existing", "caseId": key}
            agenda.save(connection, case, request_id, "development-link-restored", case["restoration"])
            agenda.wake_observation(connection, account, symbol, world, now)
        return {"status": "restored", "caseId": key}

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
                "worldId": request["observationContext"]["packet"]["worldId"],
                "status": row["status"], "resultStatus": result.get("status", ""),
                "question": request.get("question", {}).get("text", ""),
                "sourceCapturedAt": request["observationContext"]["packet"]["capturedAt"],
                "proposalCount": result.get("proposalCount", 0), "cases": cases,
                "authority": "experiment-status-only"})
        return memories
