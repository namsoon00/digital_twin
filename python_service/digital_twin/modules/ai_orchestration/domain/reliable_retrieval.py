"""V22 closes bounded reads and preserves question gaps on every terminal action."""
from copy import deepcopy
import hashlib

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import opinion_retrieval as previous, question_gap_retrieval
from .retrieval import ReadRequestError, issue

VERSION = "independent-observation-retrieval-v22-reliable-completion"
MARKER = "reliable-opinion-completion-v1"
POLICY, AUTHOR_SCOPE_POLICY = previous.POLICY, previous.AUTHOR_SCOPE_POLICY
context_view = previous.context_view
question_index, research_window, research_scope = previous.question_index, previous.research_window, previous.research_scope
price_only_finish = previous.price_only_finish
INSTRUCTIONS = """조회 완료 계약:
마지막 회차(callsRemainingAfterThis=0)에는 새 조회를 시작하지 마세요. 이미 읽은 근거로 finish/assess/research/defer 중 가능한 결과를 선택하세요.
단일 질문은 assess뿐 아니라 defer에서도 questionScopes의 부족 항목을 보존합니다. 아직 읽지 않은 자료·접근 실패·미공개·미래 자료를 구분하세요.
같은 요청이나 같은 빈 결과로 질문을 새로 만들지 마세요. 기존 질문의 충족된 요건, 남은 요건과 필요한 자료를 연결하세요.
"""


def enabled(packet):
    return packet.get("retrievalReliabilityPolicy") == MARKER


def canonical(raw):
    value = deepcopy(raw)
    if isinstance(value, dict) and value.get("action") == "defer":
        # The v20 prompt permits scoped-only gaps, but its legacy validator
        # required global gaps for defer. Copy exact strings, never invent gaps.
        value["action"] = "assess"
        value = question_gap_retrieval.canonical(value)
        value["action"] = "defer"
    return value


def validate_decision(raw):
    return previous.validate_decision(canonical(raw))


def validate_trace(trace):
    value = deepcopy(trace)
    for step in value:
        if step.get("decision"):
            step["decision"] = canonical(step["decision"])
    previous.validate_trace(value)


def validate_scope(decision, context, seen):
    if context.get("callsRemainingAfterThis") == 0 and decision["action"] == "read":
        raise ReadRequestError([issue("action", "마지막 회차에는 이미 읽은 자료로 판단 또는 보류하세요")])
    previous.validate_scope(decision, context, seen)


def schema(context=None):
    value = previous.schema(context)
    if context and context.get("callsRemainingAfterThis") == 0:
        value["properties"]["action"]["enum"] = [x for x in value["properties"]["action"]["enum"] if x != "read"]
        value["properties"]["requests"]["maxItems"] = 0
    return value


def trace_summary(trace, status):
    return {**previous.trace_summary(trace, status), "version": VERSION, "traceHash": content_hash(trace)}


def reserve(trace):
    from digital_twin.modules.reasoning.contracts import source_packet_bytes
    # Reserve the remaining trace, not another full trace after each read.
    return max(2048, 16384 - source_packet_bytes(trace_summary(trace, "round-limit")))


def prompt(current, context):
    return INSTRUCTIONS + previous.prompt(current, context)


def freeze(current, context, max_bytes):
    if not enabled(current):
        raise EvidenceContractError("reliable retrieval policy missing")
    value = previous.freeze(current, context, max_bytes)
    value.update(promptVersion=VERSION, prompt=prompt(current, value["retrievalContext"]), outputSchema=schema(value["retrievalContext"]))
    value["promptHash"] = hashlib.sha256(value["prompt"].encode()).hexdigest()
    if len(value["prompt"].encode()) > value["promptBudgetBytes"]:
        raise EvidenceContractError("reliable retrieval exceeds context budget")
    return value


def validate_input(envelope):
    if freeze(envelope["current"], envelope["retrievalContext"], envelope["promptBudgetBytes"]) != envelope:
        raise EvidenceContractError("reliable retrieval input changed")
    validate_trace(envelope["retrievalContext"].get("trace", []))
    return content_hash(envelope)
