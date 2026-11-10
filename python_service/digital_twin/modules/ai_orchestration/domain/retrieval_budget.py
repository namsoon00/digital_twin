"""Reserve only future read decisions; the hard input limits stay authoritative."""
from .retrieval import MAX_ROUNDS

VERSION = "remaining-retrieval-trace-budget-v1"
FIELD = "retrievalBudgetPolicy"
FINAL_BYTES = 2048
STEP_BYTES = 4096


def retry_allowed(previous, packet):
    """A new admission policy may reassess only its own old capacity wait once."""
    prior = previous.get("retrievalAdmission") or {}
    return (packet.get(FIELD) == VERSION and prior.get("status") == "context-budget"
            and prior.get("worldId") == packet.get("worldId") and prior.get("policy") != VERSION)


def reserve(contract, trace, remaining_decisions):
    if type(remaining_decisions) is not int or not 0 <= remaining_decisions <= MAX_ROUNDS:
        raise ValueError("bounded remaining retrieval decisions required")
    return min(contract.reserve(trace), FINAL_BYTES + STEP_BYTES * remaining_decisions)


def failure(error, stage, reserved, limit):
    """Allow only numeric diagnostics authored by the input validator."""
    source = getattr(error, "budget_diagnostic", {}) or {}
    result = {"version": VERSION, "stage": stage,
              "scope": source.get("scope", "routing-prompt" if stage == "routing-input" else "execution-context"),
              "reservedBytes": reserved}
    if result["scope"] not in {"source-packet", "author-prompt", "routing-prompt"}:
        result["scope"] = "execution-context"
    if result["scope"] != "execution-context":
        result["limitBytes"] = limit
    for key in ("usedBytes", "limitBytes", "factCount", "factBytes"):
        if type(source.get(key)) is int and source[key] >= 0:
            result[key] = source[key]
    return result


def blockage(packet):
    status = packet.get("retrieval", {}).get("status")
    reasons = {
        "context-budget": ("retrieval-context-budget", "확보한 자료와 조회 기록을 입력 한도에 함께 넣지 못했습니다. 자료 부재로 확인된 것은 아닙니다."),
        "repeated-read": ("repeated-read", "같은 자료 조회가 반복되어 추가 호출을 중단했습니다."),
        "round-limit": ("retrieval-round-limit", "허용된 조회 회차 안에 근거 선택을 마치지 못했습니다."),
    }
    steps = packet.get("retrieval", {}).get("steps") or [{}]
    if status == "deferred" and steps[-1].get("action") == "finish":
        reasons[status] = ("selected-evidence-insufficient", "조회는 끝났지만 선택한 자료가 분석의 필수 근거 요건을 충족하지 못했습니다.")
    if status not in reasons:
        return {}
    code, reason = reasons[status]
    result = {"reasonCode": code, "stage": "evidence-retrieval", "reason": reason}
    if packet.get("retrieval", {}).get("budgetFailure"):
        result["budgetFailure"] = packet["retrieval"]["budgetFailure"]
    return result
