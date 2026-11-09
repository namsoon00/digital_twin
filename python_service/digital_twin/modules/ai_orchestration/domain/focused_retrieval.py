"""V25 changes routing presentation; all frozen sources and writer facts stay exact."""
from copy import deepcopy
import hashlib
import json
from digital_twin.modules.reasoning.contracts import (
    EvidenceContractError, content_hash, validate_evidence_packet, HISTORICAL_CONTEXT_BUDGET_POLICY)
from . import reliable_retrieval, analyst_prompt, investment_meaning_prompt, evidence_purpose, company_value
from . import focused_retrieval_view as context_view, compact_opinion_prompt

VERSION = "independent-observation-retrieval-v25-focused-context"
INSTRUCTIONS = """현재 조회 계약:
과거 발송 본문과 출처 상세의 readContextPath는 같은 동결 입력의 원문을 읽는 경로다. 생략된 내용은 자료 부재가 아니다.
원래 질문의 요건과 확인한 답, 현재 근거와 반론을 유지한다. 투자 의미에 필요한 기업 원문·주석을 읽을 공간을 우선 사용한다.
같은 자료와 같은 답이라면 이미 확인한 부분의 재작성을 멈추고, 아직 부족한 정확한 문서·항목을 기존 질문에 남긴다.
"""


def enabled(packet):
    return packet.get("historicalContextPolicy") == HISTORICAL_CONTEXT_BUDGET_POLICY and reliable_retrieval.enabled(packet)


def view(packet, contract):
    from . import retrieval_view
    return context_view if enabled(packet) and contract is reliable_retrieval else getattr(contract, "context_view", retrieval_view)


def freeze(current, context, max_bytes):
    from .execution_input import EXECUTION_INPUT_PROTOCOL, prompt_budget
    if not enabled(current):
        raise EvidenceContractError("focused routing policy missing")
    validate_evidence_packet(current)
    evidence_purpose.validate(current)
    if company_value.enabled(current):
        company_value.validate(current)
    reliable_retrieval.validate_trace(context.get("trace", []))
    value = deepcopy(context)
    value["readableContextPaths"] = context_view.paths(current, value)
    # The previous prompt's instructions are immutable. Replace only its final
    # projected JSON payload; the new envelope retains the exact full input.
    base = reliable_retrieval.prompt(current, value)
    start = base.index('{"current":')
    payload, end = json.JSONDecoder().raw_decode(base[start:])
    if start + end != len(base) or set(payload) != {"current", "retrieval"}:
        raise EvidenceContractError("focused routing payload changed")
    packet, routing = context_view.project(current, value)
    payload = {"current": packet, "retrieval": routing}
    encoded, references = compact_opinion_prompt.encode(payload)
    if compact_opinion_prompt.expand(encoded) != payload:
        raise EvidenceContractError("focused routing lost input")
    prefix = INSTRUCTIONS
    if analyst_prompt.enabled(current):
        prefix += analyst_prompt.READ_INSTRUCTIONS
    if investment_meaning_prompt.enabled(current):
        prefix += investment_meaning_prompt.READ_INSTRUCTIONS
    if references:
        prefix += compact_opinion_prompt.INSTRUCTIONS.splitlines()[0] + "\n"
    text = prefix + base[:start] + compact_opinion_prompt.dumps(encoded)
    limit = prompt_budget(max_bytes)
    if len(text.encode()) > limit:
        raise EvidenceContractError("focused retrieval exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": VERSION, "promptBudgetBytes": limit,
        "current": deepcopy(current), "retrievalContext": value, "prompt": text,
        "promptHash": hashlib.sha256(text.encode()).hexdigest(), "outputSchema": reliable_retrieval.schema(value)}


def validate_input(envelope):
    if freeze(envelope["current"], envelope["retrievalContext"], envelope["promptBudgetBytes"]) != envelope:
        raise EvidenceContractError("focused retrieval input changed")
    return content_hash(envelope)
