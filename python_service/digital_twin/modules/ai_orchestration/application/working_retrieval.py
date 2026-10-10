"""Bounded discovery is independent of the final, explicitly selected evidence."""
from copy import deepcopy
import json
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash, validate_evidence_packet, EVIDENCE_PACKET_MAX_BYTES
from ..domain import working_retrieval as contract
from ..domain.retrieval import MAX_ROUNDS, PAGE_SIZE, MAX_RESULT_BYTES, MAX_TOTAL_RESULT_BYTES, ReadRequestError, issue, response_audit
from ..domain.continuity import continuity_memory, merge_recalled
from ..domain.business_research import evaluate_thesis
from ..domain.execution_input import freeze_execution_input, prompt_budget
from ..domain.question_resolution import resolution_prompt
from .read_tools import ObservationReadTools
from .retrieval import RetrievalLeaseLost, RetrievalContractFailure


def byte_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())


def author_packet(session, packet, ids, seen, research, trace, status, reserve=0):
    views = {key: row for key, row in seen.items() if row.get("evidenceView")}
    protected = {key for key, row in seen.items() if row.get("evidenceCategory") == "quality"}
    result = {**packet, **session.select(set(ids) | protected, views=views), "retrieval": contract.trace_summary(trace, status)}
    result["businessThesisMemory"] = []
    for row in research:
        if row.get("kind") == "business-thesis":
            value = deepcopy(row)
            observations = evaluate_thesis(value, result)
            value.update(reviewDue=value.get("reviewDue", False) or observations != value.get("observations", []), observations=observations)
            result["businessThesisMemory"].append(value)
    result["workingEvidence"] = {"version": contract.POLICY, "selectedFactIds": sorted(ids),
        "requiredQualityFactIds": sorted(protected), "readFactCount": len(seen), "authority": "selected-and-required-facts-only",
        "excludedReadFacts": [{"id": key, "kind": value["kind"], "category": value["evidenceCategory"],
                               "hash": content_hash(value)} for key, value in seen.items()
                              if key not in {fact["id"] for fact in result["facts"]}]}
    if reserve:
        result["pendingTraceBudget"] = " " * reserve
    validate_evidence_packet(result)
    return result


def retrieve_working_evidence(session, packet, history, research, decide, save_input, prompt_bytes,
                             max_rounds=MAX_ROUNDS, record_round=None):
    rounds = max(0, min(MAX_ROUNDS, int(max_rounds)))
    baseline = session.business_baseline()
    packet = {**packet, "businessEvidence": baseline}
    current = {**packet, **session.select(baseline["factIds"])}
    seen = {x["id"]: x for x in current["facts"]}
    selected = set(seen)
    active = deepcopy(seen)
    tools = ObservationReadTools(session, history, research)
    tools.admit(current)
    continuity = continuity_memory(history, research)
    context = {"catalog": session.catalog(), "continuity": continuity, "reportIndex": session.report_index(),
               "reportIndexLimit": 32, "reportIndexOrdering": "source-time-descending; excluded older reports remain queryable",
               "roundLimit": rounds, "selectionPolicy": contract.POLICY}
    trace, recalled, requests_seen = [], {"analyses": [], "memories": []}, set()
    used, corrections, status = 0, 0, "deferred"
    reports = {row["factId"]: row for row in context["reportIndex"]}

    def record(step):
        trace.append(step)
        if record_round is not None and not record_round(deepcopy(step)):
            raise RetrievalLeaseLost()

    for index in range(rounds):
        reserve = 8192  # Reserve the final decision's reason, selection and audit metadata.
        candidate = author_packet(session, packet, selected, {**seen, **active}, research, trace, "round-limit")
        context.update(tools.context(), trace=trace, callsRemainingAfterThis=rounds-index-1,
            resultBudgetBytesRemaining=MAX_TOTAL_RESULT_BYTES-used,
            authorBudget={"limitBytes": EVIDENCE_PACKET_MAX_BYTES, "usedBytes": byte_size(candidate), "reservedBytes": reserve,
                          "remainingBytes": max(0, EVIDENCE_PACKET_MAX_BYTES-byte_size(candidate)-reserve),
                          "promptLimitBytes": prompt_budget(prompt_bytes),
                          "requiredPromptBytes": len(resolution_prompt(candidate, continuity["previousAnalyses"], continuity["researchResults"]).encode())},
            selectedFactIds=sorted(selected),
            knownFacts=[{"factId": key, "category": row["evidenceCategory"], "kind": row["kind"],
                         "bytes": byte_size(row), "view": row.get("evidenceView", {})} for key, row in seen.items()])
        try:
            envelope = contract.freeze(current, context, prompt_bytes)
        except EvidenceContractError:
            status = "context-budget"
            break
        input_id = save_input(envelope)
        if not input_id:
            raise RetrievalLeaseLost()
        raw = decide(envelope, input_id)
        step = {"inputId": input_id, "response": response_audit(raw), "reads": [], "correction": bool(context.get("correction"))}
        try:
            decision = contract.validate_decision(raw)
            chosen = set(decision["selectedFactIds"])
            if chosen - seen.keys():
                raise ReadRequestError([issue("selectedFactIds", "knownFacts 또는 current.facts에서 읽은 ID만 선택하세요")])
            try:
                proposed = author_packet(session, packet, chosen, seen, research, trace, "round-limit", reserve)
                adjusted = {row["caseId"]: row for row in proposed["businessThesisMemory"]}
                required_research = [adjusted.get(row.get("caseId"), row) if row.get("kind") == "business-thesis" else row for row in research]
                freeze_execution_input(proposed, continuity["previousAnalyses"], required_research,
                    max_prompt_bytes=prompt_bytes, retrieval_trace=trace)
            except EvidenceContractError as error:
                if "exceeds-context-budget" not in error.code:
                    raise
                raise ReadRequestError([issue("selectedFactIds", "최종 입력 한도 초과: 질문과 관계 없는 선택을 해제하거나 보고서 지표를 발췌하세요")])
            plans = []
            for i, request in enumerate(decision["requests"]):
                if request["tool"] == "read_report":
                    report = reports.get(request["factId"])
                    if not report or set(request["metrics"]) - set(report["metrics"]):
                        raise ReadRequestError([issue("requests["+str(i)+"]", "reportIndex의 factId와 metrics만 사용하세요")])
                    plans.append(None)
                else:
                    plans.append(tools.resolve(request, i))
        except ReadRequestError as error:
            step.update(status="invalid-request", decision=None, errors=error.issues)
            record(step)
            if corrections or index+1 >= rounds:
                failure = RetrievalContractFailure([], "correction-exhausted" if corrections else "call-budget")
                failure.failure["retrieval"] = contract.trace_summary(trace, "invalid-request")
                raise failure
            corrections += 1
            context["correction"] = {"errors": error.issues, "responseHash": step["response"]["hash"]}
            continue
        selected = chosen
        active = {key: deepcopy(seen[key]) for key in selected}
        context.pop("correction", None)
        step.update(status="valid", decision=decision)
        if decision["action"] != "read":
            status = "ready" if decision["action"] == "finish" else "deferred"
            if status == "ready" and not any(seen[key]["kind"] != "stock" for key in selected):
                status = "deferred"
            record(step)
            break
        for request, plan in zip(decision["requests"], plans):
            key = content_hash(request if plan is None else plan)
            if key in requests_seen:
                result = {"status": "repeated-read", "facts": []}
            else:
                requests_seen.add(key)
                for page_size in range(PAGE_SIZE, 0, -1):
                    result = (session.read_report(request["factId"], request["metrics"]) if plan is None
                              else tools.read(plan, page_size))
                    size = byte_size(result)
                    if not result.get("omitted") and size <= MAX_RESULT_BYTES and used+size <= MAX_TOTAL_RESULT_BYTES:
                        break
                    tools.cursors.pop(result.get("nextCursor"), None)
                    if plan is None or request["tool"] == "read_fact":
                        break
                else:
                    size = MAX_TOTAL_RESULT_BYTES+1
                if result.get("omitted") or size > MAX_RESULT_BYTES or used+size > MAX_TOTAL_RESULT_BYTES:
                    result = {"status": "context-budget", "facts": [], "reason": "read-audit-budget", "omittedResultHash": content_hash(result)}
                else:
                    used += size
                    tools.admit(result)
                    seen.update({row["id"]: row for row in result.get("facts", [])})
                    if result.get("records"):
                        recalled[request["category"]] = merge_recalled(recalled[request["category"]], result["records"])
            step["reads"].append({"request": request, "result": result})
        record(step)
    final = author_packet(session, packet, selected, {**seen, **active}, research, trace, status)
    return final, merge_recalled(continuity["previousAnalyses"], recalled["analyses"]), merge_recalled(continuity["researchResults"], recalled["memories"]), trace
