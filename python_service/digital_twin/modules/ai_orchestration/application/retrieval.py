"""Model-directed read/reconsider loop over an immutable, subject-scoped capture."""
from copy import deepcopy
import json

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from digital_twin.modules.ai_orchestration.domain.brain_management import required_case_memory
from digital_twin.modules.ai_orchestration.domain.retrieval import (
    MAX_ROUNDS, MAX_RESULT_BYTES, freeze_retrieval_input, validate_read_decision, trace_summary,
)


class RetrievalLeaseLost(RuntimeError):
    pass


def retrieve_evidence(session, packet, history, research, decide, save_input, prompt_bytes, max_rounds=MAX_ROUNDS):
    rounds = max(0, min(MAX_ROUNDS, int(max_rounds)))
    if not rounds:
        # Preserve the existing bounded observation when its remaining quota
        # can fund author/reviewer only. Never spend their last calls on routing.
        fallback = deepcopy(packet)
        fallback["retrieval"] = trace_summary([], "budget-fallback")
        return fallback, history, research, []
    current = {**packet, **session.select([])}
    context = {"catalog": session.catalog(), "memoryCounts": {"analyses": len(history), "memories": len(research)},
        "openQuestions": [{key: row.get(key) for key in ("caseId", "question", "capability", "reviewDue")}
                          for row in research if required_case_memory(row)], "roundLimit": rounds, "trace": []}
    trace, seen, selected, recalled = [], set(), set(), {"analyses": [], "memories": []}
    used, status = 0, "round-limit"
    for _ in range(rounds):
        context["trace"] = trace
        try:
            envelope = freeze_retrieval_input(current, context, prompt_bytes)
        except EvidenceContractError:
            status = "context-budget"
            break
        input_id = save_input(envelope)
        if not input_id:
            raise RetrievalLeaseLost()
        decision = validate_read_decision(decide(envelope, input_id))
        step = {"inputId": input_id, "decision": decision, "reads": []}
        trace.append(step)
        if decision["action"] != "read":
            status = "ready" if decision["action"] == "finish" else "deferred"
            if not selected and any(row["available"] for key, row in context["catalog"]["coverage"].items()
                                    if key not in {"quote", "unclassified"}):
                status = "deferred"
            break
        progress = False
        for request in decision["requests"]:
            key = content_hash(request)
            if key in seen:
                response = {"status": "repeated-read", "facts": []}
            else:
                seen.add(key)
                if request["tool"] == "recall_memory":
                    source = history if request["category"] == "analyses" else research
                    start, end = request["offset"], request["offset"] + request["limit"]
                    response = {"memoryKind": request["category"], "authority": "historical-context-only",
                        "records": deepcopy(source[start:end]), "available": len(source),
                        "nextOffset": min(end, len(source)) if end < len(source) else None}
                else:
                    try:
                        response = session.read(request["category"], request["kind"], request["offset"],
                                                request["limit"], request["factId"])
                    except EvidenceContractError as error:
                        response = {"status": error.code, "facts": []}
                size = len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode())
                if used + size > MAX_RESULT_BYTES:
                    response = {"status": "context-budget", "facts": [], "omittedResultHash": content_hash(response)}
                else:
                    used += size
                    facts = response.get("facts", [])
                    selected.update(row["id"] for row in facts)
                    records = response.get("records", [])
                    if records:
                        known = {content_hash(row) for row in recalled[request["category"]]}
                        recalled[request["category"]].extend(row for row in records if content_hash(row) not in known)
                    progress = progress or bool(facts or records)
            step["reads"].append({"request": request, "result": response})
        if not progress and any(row["result"].get("status") for row in step["reads"]):
            status = "repeated-read" if all(row["result"].get("status") == "repeated-read" for row in step["reads"]) else "deferred"
            break
    final = {**packet, **session.select(selected)}
    final["retrieval"] = trace_summary(trace, status)
    required = [row for row in research if required_case_memory(row)]
    required_hashes = {content_hash(row) for row in required}
    memories = required + [row for row in recalled["memories"] if content_hash(row) not in required_hashes]
    return final, recalled["analyses"], memories, trace
