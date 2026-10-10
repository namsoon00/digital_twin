"""Read/reconsider with one local correction and durable, bounded response audit."""
from copy import deepcopy
import json

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from digital_twin.modules.ai_orchestration.domain.brain_management import required_case_memory
from digital_twin.modules.ai_orchestration.domain.continuity import continuity_memory, merge_recalled
from digital_twin.modules.ai_orchestration.domain.retrieval import (
    MAX_ROUNDS, MAX_CORRECTIONS, MAX_RESULT_BYTES, MAX_TOTAL_RESULT_BYTES, freeze_retrieval_input,
    validate_read_decision, trace_summary, response_audit, ReadRequestError,
)
from .read_tools import ObservationReadTools


class RetrievalLeaseLost(RuntimeError):
    pass


class RetrievalContractFailure(RuntimeError):
    code = "ai-retrieval-invalid-request"

    def __init__(self, trace, stop_reason):
        super().__init__(self.code)
        self.failure = {"kind": "retrieval-contract", "code": self.code, "stopReason": stop_reason,
                        "retrieval": trace_summary(trace, "invalid-request")}


def retrieve_evidence(session, packet, history, research, decide, save_input, prompt_bytes,
                      max_rounds=MAX_ROUNDS, record_round=None):
    baseline = session.business_baseline()
    packet = {**packet, "businessEvidence": baseline}
    rounds = max(0, min(MAX_ROUNDS, int(max_rounds)))
    if not rounds:
        selected = {row["id"] for row in packet["facts"]} | set(baseline["factIds"])
        fallback = {**packet, **session.select(selected)}
        fallback["retrieval"] = trace_summary([], "budget-fallback")
        return fallback, history, research, []
    current = {**packet, **session.select(baseline["factIds"])}
    context = {"catalog": session.catalog(), "memoryCounts": {"analyses": len(history), "memories": len(research)},
        "continuity": continuity_memory(history, research),
        "openQuestions": [{key: row.get(key) for key in ("caseId", "question", "capability", "reviewDue")}
                          for row in research if required_case_memory(row)], "roundLimit": rounds, "trace": []}
    tools = ObservationReadTools(session, history, research)
    trace, seen, selected, recalled = [], set(), {row["id"] for row in current["facts"] if row["kind"] != "stock"}, {"analyses": [], "memories": []}
    used, corrections, status = 0, 0, "round-limit"

    def record(step):
        trace.append(step)
        if record_round is not None and not record_round(deepcopy(step)):
            raise RetrievalLeaseLost()

    for round_index in range(rounds):
        context.update(tools.context(), trace=trace, callsRemainingAfterThis=rounds - round_index - 1,
                       resultBudgetBytesRemaining=MAX_TOTAL_RESULT_BYTES - used)
        try:
            envelope = freeze_retrieval_input(current, context, prompt_bytes)
        except EvidenceContractError:
            if context.get("correction"):
                raise RetrievalContractFailure(trace, "correction-context-budget")
            status = "context-budget"
            break
        input_id = save_input(envelope)
        if not input_id:
            raise RetrievalLeaseLost()
        raw = decide(envelope, input_id)
        step = {"inputId": input_id, "response": response_audit(raw), "reads": [],
                "correction": bool(context.get("correction"))}
        try:
            decision = validate_read_decision(raw)
            # Validate the whole batch before reading anything. A rejected
            # request cannot consume a page or discard previously read facts.
            plans = [tools.resolve(request, index) for index, request in enumerate(decision["requests"])]
        except ReadRequestError as error:
            step.update(status="invalid-request", decision=None, errors=error.issues)
            record(step)
            if corrections >= MAX_CORRECTIONS or round_index + 1 >= rounds:
                raise RetrievalContractFailure(trace, "correction-exhausted" if corrections else "call-budget")
            corrections += 1
            context["correction"] = {"errors": error.issues, "responseHash": step["response"]["hash"]}
            continue
        step.update(status="valid", decision=decision)
        context.pop("correction", None)
        if decision["action"] != "read":
            status = "ready" if decision["action"] == "finish" else "deferred"
            if not selected and any(row["available"] for key, row in context["catalog"]["coverage"].items()
                                    if key not in {"quote", "unclassified"}):
                status = "deferred"
            record(step)
            break
        progress = False
        for request, plan in zip(decision["requests"], plans):
            key = content_hash(plan)
            if key in seen:
                response = {"status": "repeated-read", "facts": []}
            else:
                seen.add(key)
                response = tools.read(plan)
                size = len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode())
                if size > MAX_RESULT_BYTES or used + size > MAX_TOTAL_RESULT_BYTES:
                    # A rejected page must not expose a cursor to unseen data.
                    tools.cursors.pop(response.get("nextCursor"), None)
                    response = {"status": "context-budget", "facts": [], "omittedResultHash": content_hash(response)}
                else:
                    used += size
                    tools.admit(response)
                    facts = response.get("facts", [])
                    selected.update(row["id"] for row in facts)
                    records = response.get("records", [])
                    if records:
                        known = {content_hash(row) for row in recalled[request["category"]]}
                        recalled[request["category"]].extend(row for row in records if content_hash(row) not in known)
                    progress = progress or bool(facts or records)
            step["reads"].append({"request": request, "result": response})
        record(step)
        if not progress and any(row["result"].get("status") for row in step["reads"]):
            status = "context-budget" if any(row["result"].get("status") == "context-budget" for row in step["reads"]) else "repeated-read"
            break
    final = {**packet, **session.select(selected)}
    final["retrieval"] = trace_summary(trace, status)
    required = context["continuity"]
    return (final, merge_recalled(required["previousAnalyses"], recalled["analyses"]),
            merge_recalled(required["researchResults"], recalled["memories"]), trace)
