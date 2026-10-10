"""Read/reconsider with one local correction and durable, bounded response audit."""
from copy import deepcopy
import json

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash, validate_evidence_packet
from digital_twin.modules.ai_orchestration.domain.brain_management import required_case_memory
from digital_twin.modules.ai_orchestration.domain.continuity import continuity_memory, merge_recalled
from digital_twin.modules.ai_orchestration.domain.retrieval import (
    MAX_ROUNDS, MAX_CORRECTIONS, MAX_RESULT_BYTES, MAX_TOTAL_RESULT_BYTES, PAGE_SIZE, freeze_retrieval_input,
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


def fits_author_packet(session, packet, selected, research, trace, remaining_rounds):
    """Check the final evidence contract before a tool page becomes admitted."""
    from ..domain.business_research import evaluate_thesis
    try:
        candidate = {**packet, **session.select(selected), "retrieval": trace_summary(trace, "round-limit")}
        business = []
        for memory in research:
            if memory.get("kind") == "business-thesis":
                value = deepcopy(memory)
                observations = evaluate_thesis(value, candidate)
                value.update(reviewDue=value.get("reviewDue", False) or observations != value.get("observations", []),
                             observations=observations)
                business.append(value)
        candidate["businessThesisMemory"] = business
        # A later finish/defer round adds a bounded reason and trace metadata.
        # Read rounds are checked again with their exact prospective trace.
        candidate["pendingTraceBudget"] = " " * (8192 * remaining_rounds)
        validate_evidence_packet(candidate)
        return True
    except EvidenceContractError as error:
        if error.code != "evidence-contract:observation-evidence-exceeds-context-budget":
            raise
        return False


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
    tools.admit(current)
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
        # When the model requests complementary sources together, reserve one
        # whole fact for each if that minimum fits. A large first page must not
        # consume all space before the counter-source can be read.
        minimums = [{row["id"] for row in tools.preview(plan, 1).get("facts", [])} for plan in plans]
        minimum = selected.union(*minimums)
        reserve_minimum = fits_author_packet(session, packet, minimum, research, trace + [step], rounds - round_index - 1)
        for index, (request, plan) in enumerate(zip(decision["requests"], plans)):
            key = content_hash(plan)
            if key in seen:
                response = {"status": "repeated-read", "facts": []}
            else:
                seen.add(key)
                # Fit a contiguous whole-record page, not an all-or-nothing
                # four-record batch. Rejected trial cursors never escape, and
                # the admitted cursor advances only past the records returned.
                for page_size in range(PAGE_SIZE, 0, -1):
                    response = tools.read(plan, page_size)
                    size = len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode())
                    prospective = selected | {row["id"] for row in response.get("facts", [])}
                    if reserve_minimum:
                        prospective = prospective.union(*minimums[index + 1:])
                    prospective_trace = trace + [{**step, "reads": step["reads"] + [{"request": request, "result": response}]}]
                    packet_fits = fits_author_packet(session, packet, prospective, research, prospective_trace, rounds - round_index - 1)
                    fits = (not response.get("omitted") and size <= MAX_RESULT_BYTES
                            and used + size <= MAX_TOTAL_RESULT_BYTES and packet_fits)
                    if fits:
                        break
                    tools.cursors.pop(response.get("nextCursor"), None)
                    if request["tool"] == "read_fact":
                        break
                if not fits:
                    # A rejected page must not expose a cursor to unseen data.
                    tools.cursors.pop(response.get("nextCursor"), None)
                    response = {"status": "context-budget", "facts": [], "omittedResultHash": content_hash(response),
                                "reason": "author-packet-budget" if not packet_fits else "tool-result-budget"}
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
    if any(read["result"].get("status") == "context-budget" for step in trace for read in step["reads"]):
        status = "context-budget"
    final = {**packet, **session.select(selected)}
    final["retrieval"] = trace_summary(trace, status)
    required = context["continuity"]
    return (final, merge_recalled(required["previousAnalyses"], recalled["analyses"]),
            merge_recalled(required["researchResults"], recalled["memories"]), trace)
