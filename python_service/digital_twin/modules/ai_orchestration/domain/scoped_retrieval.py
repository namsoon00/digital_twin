"""Question-scoped research admission, with byte-identical V3 replay retained."""
from copy import deepcopy
import hashlib
import json
from . import working_retrieval as legacy
from .retrieval import ReadRequestError, issue
from .insight_schema import obj, array, choice

VERSION = "independent-observation-retrieval-v4-scoped-research"
POLICY = "question-working-set-v2"
SCOPE_VERSION = "question-research-scope-v1"
AUTHOR_SCOPE_POLICY = "provisional-knowledge-only-v1"
INSTRUCTIONS = """질문별 연구 진행 계약:
questionIndex의 원래 질문·기간·완료 기준을 유지하고 질문마다 questionScopes에 caseId,status(ready/waiting),selectedFactIds,missingEvidence를 기록하세요.
ready는 선택한 현재 근거로 해당 질문을 평가할 수 있다는 뜻이며 답변 완료나 가설 검증 승인이 아닙니다. waiting에는 부족한 항목을 남기세요.
한 질문에 근거가 충분하면 finish하고 다른 질문의 부족분은 그 질문의 missingEvidence에만 남기세요. 전체 missingEvidence는 이번 진행 범위를 막는 항목입니다.
질문 전체의 답이 없어도 선택한 공식 보고·원문으로 제한된 설명 가설과 반론을 연구할 수 있으면 action=research를 선택하세요. 요청은 빈 배열, 전체 missingEvidence에는 미확인 조건을 남기세요.
research는 내부 잠정 연구만 허용하며 질문을 answered로 닫거나 고객 알림을 발송하지 않습니다. 가설은 별도의 독립 검토를 거쳐야 저장됩니다.
근거가 없거나 선택 용량이 부족하면 defer하세요. 출처·수치·기간·단위를 만들거나 품질 차단을 무시하면 안 됩니다.
연구 틀의 다른 목적이나 현재 시세의 부족을 과거 공시 비교의 추가 필수 조건으로 만들지 마세요.
"""


def question_index(research):
    return [{key: deepcopy(row[key]) for key in ("caseId", "question", "questionContract") if key in row}
            for row in research if row.get("kind") == "brain-case" and row.get("capability") == "research"][:5]


def research_window(research):
    """Schedule a bounded question cohort without editing any original contract."""
    cases = [row for row in research if row.get("kind") == "brain-case"]
    ordered = sorted(cases, key=lambda row: ((row.get("answerResolution") or {}).get("status") != "answered",
        not row.get("reviewDue", False), row.get("nextCheckAt", ""), row["caseId"]))
    admitted = {row["caseId"] for row in ordered[:3]}
    omitted = [row["caseId"] for row in ordered[3:]]
    return [row for row in research if row.get("kind") != "brain-case" or row["caseId"] in admitted], {
        "version": "question-research-window-v1", "includedCaseIds": sorted(admitted),
        "excludedCaseIds": omitted[:32], "excludedCaseCount": len(omitted),
        "exclusionMeaning": "deferred-to-another-research-window-not-answered-or-dismissed"}


def schema(context=None):
    result = legacy.schema(context)
    result["properties"]["action"] = choice(["read", "finish", "research", "defer"])
    text = {"type": "string", "minLength": 1, "maxLength": 600}
    ids = result["properties"]["selectedFactIds"]
    result["properties"]["questionScopes"] = {**array(obj({
        "caseId": choice([row["caseId"] for row in context.get("questionIndex", [])] or [""]) if context is not None else text,
        "status": choice(["ready", "waiting"]), "selectedFactIds": deepcopy(ids),
        "missingEvidence": {**array(text), "maxItems": 4}})), "maxItems": 5}
    result["required"].append("questionScopes")
    return result


def validate_decision(raw):
    if not isinstance(raw, dict) or set(raw) != set(schema()["required"]):
        raise ReadRequestError([issue("$", schema()["required"])])
    base = {key: deepcopy(value) for key, value in raw.items() if key != "questionScopes"}
    if base["action"] == "research":
        if not base["missingEvidence"] or not base["selectedFactIds"]:
            raise ReadRequestError([issue("research", "잠정 연구는 선택 근거와 미확인 조건이 필요합니다")])
        base["action"] = "defer"
    legacy.validate_decision(base)
    scopes, seen = raw["questionScopes"], set()
    if not isinstance(scopes, list) or len(scopes) > 5:
        raise ReadRequestError([issue("questionScopes", "최대 다섯 개 질문")])
    for row in scopes:
        if (not isinstance(row, dict) or set(row) != {"caseId", "status", "selectedFactIds", "missingEvidence"}
                or not isinstance(row["caseId"], str) or not 1 <= len(row["caseId"]) <= 600 or row["caseId"] in seen
                or row["status"] not in {"ready", "waiting"}):
            raise ReadRequestError([issue("questionScopes", "고유 질문 ID와 진행 상태")])
        seen.add(row["caseId"])
        legacy.validate_decision({"action": "finish" if row["status"] == "ready" else "defer",
            "reason": raw["reason"], "requests": [], "selectedFactIds": row["selectedFactIds"], "missingEvidence": row["missingEvidence"]})
        if not set(row["selectedFactIds"]) <= set(raw["selectedFactIds"]):
            raise ReadRequestError([issue("questionScopes.selectedFactIds", "최종 선택한 근거의 부분집합")])
        if row["status"] == "ready" and not row["selectedFactIds"]:
            raise ReadRequestError([issue("questionScopes.selectedFactIds", "질문 평가에 필요한 선택 근거")])
    return deepcopy(raw)


def validate_scope(decision, context, seen):
    expected = {row["caseId"] for row in context.get("questionIndex", [])}
    actual = {row["caseId"] for row in decision["questionScopes"]}
    if actual - expected or (decision["action"] != "read" and expected != actual):
        raise ReadRequestError([issue("questionScopes", "이번 questionIndex의 질문별 진행 상태를 모두 보존하세요")])
    if decision["action"] == "finish" and expected and not any(row["status"] == "ready" for row in decision["questionScopes"]):
        raise ReadRequestError([issue("questionScopes", "평가할 질문이 없으면 research 또는 defer")])
    if decision["action"] == "research":
        from .research_evidence_use import usable_fact
        if not any(usable_fact(seen[key]) and seen[key].get("evidenceCategory") in {"company", "research", "valuation"}
                   for key in decision["selectedFactIds"] if key in seen):
            raise ReadRequestError([issue("selectedFactIds", "잠정 연구를 뒷받침할 사용 가능한 회사·원문·가치평가 근거")])


def validate_trace(trace):
    normalized = deepcopy(trace)
    for step in normalized:
        if step.get("decision") is not None:
            row = validate_decision(step["decision"])
            row.pop("questionScopes")
            if row["action"] == "research":
                row["action"] = "defer"
            step["decision"] = row
    legacy.validate_trace(normalized)


def trace_summary(trace, status):
    normalized = deepcopy(trace)
    for step in normalized:
        if (step.get("decision") or {}).get("action") == "research":
            step["decision"]["action"] = "defer"
    result = legacy.trace_summary(normalized, status)
    result["version"] = VERSION
    from digital_twin.modules.reasoning.contracts import content_hash
    result["traceHash"] = content_hash(trace)
    for target, step in zip(result["steps"], trace):
        target["action"] = (step.get("decision") or {}).get("action", "invalid")
        target["questionScopes"] = deepcopy((step.get("decision") or {}).get("questionScopes", []))
    return result


def research_scope(trace, status):
    decision = next((row["decision"] for row in reversed(trace) if row.get("decision")), {})
    value = {"version": SCOPE_VERSION, "mode": "research-only" if status == "research-ready" else "question-assessment",
        "questions": deepcopy(decision.get("questionScopes", [])), "missingEvidence": deepcopy(decision.get("missingEvidence", [])),
        "authority": "research-admission-only", "qualification": "not-empirically-qualified"}
    if status == "research-ready" and trace and trace[-1].get("authorScopePolicy") == AUTHOR_SCOPE_POLICY:
        value["assessmentPolicy"] = AUTHOR_SCOPE_POLICY
    return value


def prompt(current, context):
    data = json.dumps({"current": current, "retrieval": context}, ensure_ascii=False, allow_nan=False)
    base = legacy.prompt(current, context)
    # Replace only the authored V3 instruction prefix. Source JSON stays exact,
    # even if a filing happens to quote the old instructions verbatim.
    prefix = base[:-len(data)]
    prefix = prefix.replace("충분하면 finish, 부족하면 defer와 missingEvidence에 필요한 자료·기간 또는 실행 제약을 명시하세요.",
        "선택한 질문을 평가할 수 있으면 finish, 부분 근거로 잠정 연구할 수 있으면 research, 진행 근거가 없으면 defer하세요.")
    prefix = prefix.replace("마지막 호출에는 finish/defer를 선택하세요. finish는 필수 자료가 모두 선택됐을 때만 사용하고 reason에 충분한 이유를 적으세요.",
        "마지막 호출에는 finish/research/defer를 선택하세요. finish의 필수 자료는 ready 질문의 범위에 적용하고 reason에 충분한 이유를 적으세요.")
    return INSTRUCTIONS + prefix + data


def freeze(current, context, max_bytes):
    from .execution_input import EXECUTION_INPUT_PROTOCOL, prompt_budget
    from digital_twin.modules.reasoning.contracts import EvidenceContractError
    value = prompt(current, context)
    limit = prompt_budget(max_bytes)
    if len(value.encode()) > limit:
        raise EvidenceContractError("retrieval input exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": VERSION, "promptBudgetBytes": limit,
        "current": deepcopy(current), "retrievalContext": deepcopy(context), "prompt": value,
        "promptHash": hashlib.sha256(value.encode()).hexdigest(), "outputSchema": schema(context)}
