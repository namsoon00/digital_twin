"""Bounded discovery is independent of the final, explicitly selected evidence."""
from copy import deepcopy
import json
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash, validate_evidence_packet, EVIDENCE_PACKET_MAX_BYTES, source_packet_bytes
from ..domain import working_retrieval as legacy_contract, scoped_retrieval, indexed_retrieval, memory_reference, question_retrieval, question_assessment, lane_retrieval, execution_lanes
from ..domain.retrieval import MAX_ROUNDS, PAGE_SIZE, MAX_RESULT_BYTES, MAX_TOTAL_RESULT_BYTES, ReadRequestError, issue, response_audit
from ..domain.continuity import continuity_memory, merge_recalled
from ..domain.business_research import evaluate_thesis
from ..domain.execution_input import freeze_execution_input, prompt_budget
from ..domain.knowledge_research import knowledge_prompt
from ..domain import bounded_retrieval, evidence_readiness
from .read_tools import ObservationReadTools
from .retrieval import RetrievalLeaseLost, RetrievalContractFailure


def byte_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())


def author_packet(session, packet, ids, seen, research, trace, status, reserve=0, contract=legacy_contract):
    views = {key: row for key, row in seen.items() if row.get("evidenceView")}
    protected = {key for key, row in seen.items() if row.get("evidenceCategory") == "quality"}
    result = {**packet, **session.select(set(ids) | protected, views=views), "retrieval": contract.trace_summary(trace, status)}
    if contract in {scoped_retrieval, indexed_retrieval, question_retrieval, lane_retrieval, bounded_retrieval}:
        result["researchScope"] = contract.research_scope(trace, status)
    result["businessThesisMemory"] = []
    for row in research:
        if row.get("kind") == "business-thesis":
            value = memory_reference.brief(row, "memories") if contract in {indexed_retrieval, question_retrieval, lane_retrieval, bounded_retrieval} else deepcopy(row)
            observations = evaluate_thesis(row, result)
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
    indexed = packet.get("memoryReadPolicy") == memory_reference.VERSION
    question_work = question_assessment.enabled(packet)
    contract = bounded_retrieval if evidence_readiness.enabled(packet) else lane_retrieval if execution_lanes.identified(packet) else question_retrieval if question_work else indexed_retrieval if indexed else scoped_retrieval if packet.get("researchKnowledge") else legacy_contract
    scoped = contract in {scoped_retrieval, indexed_retrieval, question_retrieval, lane_retrieval, bounded_retrieval}
    if question_work:
        research = question_assessment.prepare(research, session.question_work_inventory())
    if scoped:
        from ..domain.question_evidence import VERSION as evidence_policy, captured_index
        research, window = contract.research_window(research)
        question_index = contract.question_index(research)
        packet = {**packet, "researchWindow": window, "questionEvidencePolicy": evidence_policy,
                  "questionEvidenceIndex": captured_index(question_index)}
    rounds = max(0, min(MAX_ROUNDS, int(max_rounds)))
    sources = {}
    for case in research:
        if case.get("kind") == "brain-case":
            for source in (case.get("latestSourceAnswer") or {}).get("sources", []):
                if source.get("evidenceId"):
                    sources.setdefault(source["evidenceId"], []).append(case["caseId"])
    documents = session.question_document_index(sources, document_views=True) if question_work or contract in {lane_retrieval, bounded_retrieval} else session.question_document_index(sources)
    baseline = session.business_baseline(documents, document_views=True) if question_work or contract in {lane_retrieval, bounded_retrieval} else session.business_baseline(documents)
    packet = {**packet, "businessEvidence": baseline}
    from ..domain.research_evidence_use import answered_research_scope
    scope = answered_research_scope(research)
    if scope and contract is legacy_contract:
        packet["researchAssessmentScope"] = scope
    elif scoped:
        # V4 owns question-level admission, including provisional research.
        # The V3 answered-only gate remains solely in historical/disabled inputs.
        packet.pop("researchAssessmentScope", None)
    baseline_views = {key: session.read_document(key)["facts"][0] for key in baseline.get("viewFactIds", [])}
    current = {**packet, **session.select(baseline["factIds"], views=baseline_views)}
    seen = {x["id"]: x for x in current["facts"]}
    selected = set(seen)
    active = deepcopy(seen)
    tools = ObservationReadTools(session, history, research, indexed=indexed)
    tools.admit(current)
    # The frozen index grants read access, not evidence selection. Unread IDs
    # remain outside seen and must be read before the author can use them.
    tools.known.update({row["factId"]: row["category"] for row in documents})
    continuity = (memory_reference.continuity(history, research) if indexed else
                  continuity_memory(history, research, compact_cases=scoped))
    context = {**({"executionLane": packet["executionLane"]} if execution_lanes.identified(packet) else {}), "catalog": session.catalog(), "continuity": continuity, "reportIndex": session.report_index(),
               "questionDocumentIndex": documents,
               "reportIndexLimit": 32, "reportIndexOrdering": "source-time-descending; excluded older reports remain queryable",
               "roundLimit": rounds, "selectionPolicy": contract.POLICY}
    if indexed:
        context["memoryIndex"] = tools.memory_index
    if scoped:
        context["questionIndex"] = question_index
        context["questionEvidencePolicy"] = packet["questionEvidencePolicy"]
        context["authorScopePolicy"] = contract.AUTHOR_SCOPE_POLICY
    trace, recalled, requests_seen = [], {"analyses": [], "memories": []}, set()
    used, corrections, status = 0, 0, "deferred"
    reports = {row["factId"]: row for row in context["reportIndex"]}
    document_ids = {row["factId"] for row in documents}

    def record(step):
        trace.append(step)
        if record_round is not None and not record_round(deepcopy(step)):
            raise RetrievalLeaseLost()

    for index in range(rounds):
        reserve = 8192  # Reserve the final decision's reason, selection and audit metadata.
        candidate = author_packet(session, packet, selected, {**seen, **active}, research, trace, "round-limit", contract=contract)
        source_bytes = source_packet_bytes(candidate)
        context.update(tools.context(), trace=trace, callsRemainingAfterThis=rounds-index-1,
            resultBudgetBytesRemaining=MAX_TOTAL_RESULT_BYTES-used,
            authorBudget={"limitBytes": EVIDENCE_PACKET_MAX_BYTES, "usedBytes": source_bytes, "reservedBytes": reserve,
                          "remainingBytes": max(0, EVIDENCE_PACKET_MAX_BYTES-source_bytes-reserve),
                          "knowledgeBytes": byte_size(candidate.get("researchKnowledge", {})),
                          "promptLimitBytes": prompt_budget(prompt_bytes),
                          "requiredPromptBytes": len(knowledge_prompt(candidate,
                              merge_recalled(continuity["previousAnalyses"], recalled["analyses"]),
                              merge_recalled(continuity["researchResults"], recalled["memories"])).encode())},
            selectedFactIds=sorted(selected),
            knownFacts=[{"factId": key, "category": row["evidenceCategory"], "kind": row["kind"],
                         "bytes": byte_size(row), "view": row.get("evidenceView", {})} for key, row in seen.items()])
        if contract is bounded_retrieval:
            context.update(tools.issued_context())
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
        if scoped:
            step["authorScopePolicy"] = contract.AUTHOR_SCOPE_POLICY
        try:
            decision = contract.validate_decision(raw)
            chosen = set(decision["selectedFactIds"])
            if chosen - seen.keys():
                raise ReadRequestError([issue("selectedFactIds", "knownFacts 또는 current.facts에서 읽은 ID만 선택하세요")])
            if scoped:
                contract.validate_scope(decision, context, seen)
            try:
                proposed = author_packet(session, packet, chosen, seen, research, trace, "round-limit", reserve, contract)
                adjusted = {row["caseId"]: row for row in proposed["businessThesisMemory"]}
                required_research = [adjusted.get(row.get("caseId"), row) if row.get("kind") == "business-thesis" else row
                                    for row in (continuity["researchResults"] if scoped else research)]
                freeze_execution_input(proposed, merge_recalled(continuity["previousAnalyses"], recalled["analyses"]),
                    merge_recalled(required_research, recalled["memories"]),
                    max_prompt_bytes=prompt_bytes, retrieval_trace=trace)
            except EvidenceContractError as error:
                if "exceeds-context-budget" not in error.code:
                    raise
                if contract in {lane_retrieval, bounded_retrieval}:
                    # This is an admission limit, not malformed model output.
                    # Save the valid choice and stop without a paid correction.
                    step.update(status="valid", decision=decision)
                    record(step)
                    status = "context-budget"
                    break
                raise ReadRequestError([issue("selectedFactIds", "최종 입력 한도 초과: 질문과 관계 없는 선택을 해제하거나 보고서 지표를 발췌하세요")])
            plans = []
            for i, request in enumerate(decision["requests"]):
                if request["tool"] == "read_document":
                    if request["factId"] not in document_ids:
                        raise ReadRequestError([issue("read_document", "이번 questionDocumentIndex에 있는 원문 ID")])
                    plans.append(None)
                elif request["tool"] == "read_report":
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
            status = "ready" if decision["action"] == "finish" else "assessment-ready" if decision["action"] == "assess" else "research-ready" if decision["action"] == "research" else "deferred"
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
                    result = (session.read_document(request["factId"]) if request["tool"] == "read_document" else
                              session.read_report(request["factId"], request["metrics"]) if plan is None
                              else tools.read(plan, page_size))
                    size = byte_size(result)
                    if not result.get("omitted") and size <= MAX_RESULT_BYTES and used+size <= MAX_TOTAL_RESULT_BYTES:
                        break
                    tools.cursors.pop(result.get("nextCursor"), None)
                    if plan is None or request["tool"] in {"read_fact", "read_memory"}:
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
    final = author_packet(session, packet, selected, {**seen, **active}, research, trace, status, contract=contract)
    memories = merge_recalled(continuity["researchResults"], recalled["memories"])
    if final.get("researchScope", {}).get("assessmentPolicy") == scoped_retrieval.AUTHOR_SCOPE_POLICY:
        final["researchDeferredQuestions"] = [{key: deepcopy(row[key]) for key in
            ("caseId", "question", "questionContract", "capability", "reviewDue", "revision") if key in row}
            for row in research if row.get("kind") == "brain-case"]
        final["researchWindow"]["authorDeferredCaseIds"] = [row["caseId"] for row in final["researchDeferredQuestions"]]
        memories = [row for row in memories if row.get("kind") != "brain-case"]
        validate_evidence_packet(final)
    return final, merge_recalled(continuity["previousAnalyses"], recalled["analyses"]), memories, trace
