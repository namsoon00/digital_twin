"""V9 captures the chosen purpose and a typed blocker without widening reads."""
from copy import deepcopy
import hashlib
from . import bounded_retrieval as previous, evidence_purpose, execution_lanes, company_value
from .retrieval import ReadRequestError, issue
from digital_twin.modules.reasoning.contracts import content_hash

VERSION = "independent-observation-retrieval-v9-purpose-scoped"
SUPPORTED_VERSIONS = {VERSION, company_value.RETRIEVAL_VERSION}


def version_for(packet):
    return company_value.RETRIEVAL_VERSION if company_value.enabled(packet) else VERSION


POLICY = previous.POLICY
AUTHOR_SCOPE_POLICY = previous.AUTHOR_SCOPE_POLICY
question_index = previous.question_index
research_window = previous.research_window
research_scope = previous.research_scope
price_only_finish = previous.price_only_finish
PURPOSES = ["source-dated", "current-state", "original-question"]
BLOCKERS = ["none", "current-quote", "required-source"]


def schema(context=None):
    value = previous.schema(context)
    purposes = PURPOSES[:2] if context and context.get("executionLane") == execution_lanes.MARKET else ["original-question"]
    value["properties"].update(purpose={"type": "string", "enum": purposes},
                               blocker={"type": "string", "enum": BLOCKERS})
    value["required"] += ["purpose", "blocker"]
    return value


def legacy(decision):
    return {key: value for key, value in decision.items() if key not in {"purpose", "blocker"}}


def validate_decision(raw):
    if not isinstance(raw, dict) or raw.get("purpose") not in PURPOSES or raw.get("blocker") not in BLOCKERS:
        raise ReadRequestError([issue("purpose/blocker", "스키마의 분석 목적과 필수 자료 부족 원인을 선택하세요")])
    value = previous.validate_decision(legacy(raw))
    blocker = raw["blocker"]
    if raw["action"] == "finish" and value["action"] == "defer" and value["missingEvidence"] and blocker == "none":
        blocker = "required-source"  # Keep V8's unpaid, conservative canonicalization.
    if (blocker == "current-quote" and (raw["purpose"] != "current-state" or value["action"] != "defer")
            or value["action"] in {"read", "finish"} and blocker != "none"
            or value["missingEvidence"] and blocker == "none"):
        raise ReadRequestError([issue("blocker", "현재 시세 대기는 current-state/defer, 필수 부족 항목은 required-source")])
    return {**value, "purpose": raw["purpose"], "blocker": blocker}


def validate_scope(decision, context, seen):
    market = context.get("executionLane") == execution_lanes.MARKET
    if (decision["purpose"] == "original-question") == market:
        raise ReadRequestError([issue("purpose", "시장 관찰과 원래 질문의 분석 목적을 구분하세요")])
    if decision["purpose"] == "current-state" and decision["action"] == "finish":
        eligible = {row["evidenceId"] for row in context.get("evidenceUses", {}).get("quotes", []) if row["currentStateEligible"]}
        if not eligible.intersection(decision["selectedFactIds"]):
            raise ReadRequestError([issue("purpose", "최신 시세 없는 현재 상태 결론은 불가: source-dated 또는 defer")])
    previous.validate_scope(legacy(decision), context, seen)


def validate_trace(trace):
    stripped = deepcopy(trace)
    for row in stripped:
        if row.get("decision"):
            validate_decision(row["decision"])
            row["decision"] = legacy(row["decision"])
    previous.validate_trace(stripped)


def trace_summary(trace, status):
    stripped = deepcopy(trace)
    for row in stripped:
        if row.get("decision"):
            row["decision"] = legacy(row["decision"])
    value = previous.trace_summary(stripped, status)
    value.update(version=VERSION, traceHash=content_hash(trace))
    for target, row in zip(value["steps"], trace):
        target.update({key: (row.get("decision") or {}).get(key, "") for key in ("purpose", "blocker")})
    return value


def prompt(current, context):
    return ((company_value.INSTRUCTIONS if company_value.enabled(current) else "") + evidence_purpose.INSTRUCTIONS + "조회 응답에 purpose와 blocker를 기록하세요. 더 읽는 동안 blocker=none입니다. "
            "현재 시세만 필수로 부족하면 current-state/defer/current-quote, 다른 필수 근거가 부족하면 required-source입니다. "
            "source-dated 설명이 가능한지 먼저 확인하고, 아무 새 의미가 없으면 작성을 강제하지 마세요.\n"
            + previous.prompt(current, context))


def freeze(current, context, max_bytes):
    from .execution_input import prompt_budget
    from digital_twin.modules.reasoning.contracts import EvidenceContractError
    evidence_purpose.validate(current)
    value = previous.freeze(current, context, max_bytes)
    text = prompt(current, context)
    if len(text.encode()) > prompt_budget(max_bytes):
        raise EvidenceContractError("retrieval input exceeds context budget")
    value.update(promptVersion=version_for(current), prompt=text, promptHash=hashlib.sha256(text.encode()).hexdigest(), outputSchema=schema(context))
    return value
