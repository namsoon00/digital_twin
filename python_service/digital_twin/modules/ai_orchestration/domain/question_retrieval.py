"""V6 admits partial question assessment and exact source-document reads."""
from copy import deepcopy
import hashlib
from . import indexed_retrieval as previous, scoped_retrieval, question_assessment
from .insight_schema import obj, choice
from .retrieval import ReadRequestError, issue
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash

VERSION = "independent-observation-retrieval-v6-question-assessment"
POLICY = "question-working-set-v4-assessment"
AUTHOR_SCOPE_POLICY = previous.AUTHOR_SCOPE_POLICY
question_index = previous.question_index


def research_window(research):
    parked = [row for row in research if row.get("questionWorkUnchanged")]
    active, window = previous.research_window([row for row in research if not row.get("questionWorkUnchanged")])
    window.update(version="question-research-window-v3", unchangedCaseIds=[row["caseId"] for row in parked],
        resumeOn=["changed-source-evidence", "changed-question-contract"])
    return active, window


def schema(context=None):
    result = previous.schema(context)
    result["properties"]["action"] = choice(["read", "finish", "assess", "research", "defer"])
    result["properties"]["requests"]["items"]["anyOf"].append(obj({"tool": choice(["read_document"]),
        "factId": choice([row["factId"] for row in context.get("questionDocumentIndex", [])]
                         or [""]) if context is not None else {"type": "string", "minLength": 1, "maxLength": 600}}))
    return result


def normalized(raw):
    result = deepcopy(raw)
    if isinstance(result, dict):
        if result.get("action") == "assess":
            result["action"] = "defer"
        requests = result.get("requests")
        for row in requests if isinstance(requests, list) else []:
            if isinstance(row, dict) and row.get("tool") == "read_document":
                if (set(row) != {"tool", "factId"} or not isinstance(row["factId"], str)
                        or not 1 <= len(row["factId"]) <= 600):
                    raise ReadRequestError([issue("read_document", "questionDocumentIndex의 원문 factId")])
                row.update(tool="read_fact", category="research")
    return result


def validate_decision(raw):
    previous.validate_decision(normalized(raw))
    if raw["action"] == "assess" and (not raw["selectedFactIds"] or not raw["questionScopes"]
            or not any(row["selectedFactIds"] for row in raw["questionScopes"])):
        raise ReadRequestError([issue("assess", "원래 질문에 연결된 실제 선택 근거")])
    return deepcopy(raw)


def validate_scope(decision, context, seen):
    if decision["action"] != "assess":
        return previous.validate_scope(decision, context, seen)
    if any(row["status"] == "ready" for row in decision["questionScopes"]):
        raise ReadRequestError([issue("action", "답할 질문이 있으면 finish")])
    previous.validate_scope(normalized(decision), context, seen)
    from .research_evidence_use import usable_fact
    for row in decision["questionScopes"]:
        if row["selectedFactIds"] and not any(key in seen and usable_fact(seen[key])
                and seen[key].get("evidenceCategory") in {"company", "research", "valuation"} for key in row["selectedFactIds"]):
            raise ReadRequestError([issue("questionScopes", "질문 부분 평가에는 사용 가능한 회사·원문·가치평가 근거")])


def validate_trace(trace):
    value = deepcopy(trace)
    for step in value:
        if step.get("decision") is not None:
            validate_decision(step["decision"])
            step["decision"] = normalized(step["decision"])
    previous.validate_trace(value)


def trace_summary(trace, status):
    value = deepcopy(trace)
    for step in value:
        if step.get("decision") is not None:
            step["decision"] = normalized(step["decision"])
    result = previous.trace_summary(value, status)
    result.update(version=VERSION, traceHash=content_hash(trace))
    for target, step in zip(result["steps"], trace):
        target["action"] = (step.get("decision") or {}).get("action", "invalid")
    return result


def research_scope(trace, status):
    result = scoped_retrieval.research_scope(trace, status)
    if status == "assessment-ready" or (status == "ready" and result["questions"]):
        result.update(assessmentOnly=True, assessmentPolicy=question_assessment.VERSION)
    return result


def prompt(current, context):
    from .question_answer import read_instructions
    return read_instructions(current) + question_assessment.INSTRUCTIONS + ("read_document(factId)는 questionDocumentIndex의 원문과 출처·검증 정보를 손실 없이 읽고 중복 분석 필드를 제외합니다. "
        "structured-report는 출처에 기록된 보고 수치와 단위·기간·계보이며 공시 전문·주석·정정 이력이 아닙니다. "
        "부분 근거가 있으면 assess로 원래 질문의 부분 답변과 부족분을 기록하고, 같은 자료의 반복 연구를 멈추세요.\n") + previous.prompt(current, context)


def freeze(current, context, max_bytes):
    from .execution_input import EXECUTION_INPUT_PROTOCOL, prompt_budget
    value = prompt(current, context)
    limit = prompt_budget(max_bytes)
    if len(value.encode()) > limit:
        raise EvidenceContractError("retrieval input exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": VERSION, "promptBudgetBytes": limit,
        "current": deepcopy(current), "retrievalContext": deepcopy(context), "prompt": value,
        "promptHash": hashlib.sha256(value.encode()).hexdigest(), "outputSchema": schema(context)}
