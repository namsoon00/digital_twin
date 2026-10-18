"""Adversarial memory growth and exact read/replay checks, without model calls."""
import copy
import hashlib
import json
from unittest.mock import Mock


def check_indexed_memory(test):
    from test_business_research import business_packet
    from test_research_knowledge import context
    from test_question_evidence_flow import question
    from test_scoped_knowledge_research import research_session, scoped_decision
    from digital_twin.modules.ai_orchestration.domain import memory_reference as memory, indexed_retrieval
    from digital_twin.modules.ai_orchestration.application.read_tools import ObservationReadTools
    from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
    from digital_twin.modules.ai_orchestration.domain.execution_input import (
        freeze_execution_input, freeze_repair_input, validate_execution_input,
        KNOWLEDGE_PROMPT_VERSION, KNOWLEDGE_REPAIR_PROMPT_VERSION)
    from digital_twin.modules.ai_orchestration.domain.knowledge_research import knowledge_prompt
    from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
    from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
    p = business_packet(); p["researchKnowledge"] = context(p); p["memoryReadPolicy"] = memory.VERSION
    original = question(p)
    original.update(reviewDue=True, lastResearch={"taskId": "original-source-task", "result": {
        "status": "partial", "audit": "UNREQUESTED_PROVIDER_AUDIT" * 10000}},
        latestSourceAnswer={"text": "원문에서 확인한 부분 답변입니다.", "status": "partial", "sources": []})
    business = {"kind": "business-thesis", **{key: p[key] for key in ("accountId", "symbol", "worldId")},
        "caseId": "business", "revision": 1, "status": "data-needed", "reviewDue": False,
        "question": "현금 회수의 개선을 확인할 수 있는가?", "contract": {"checkpoints": []},
        "createdAt": "2026-01-01T00:00:00Z", "expiresAt": "2027-01-01T00:00:00Z", "observations": [],
        "origin": {"evidence": ["UNREQUESTED_ORIGIN" * 10000]}}
    rows = [original, business, {"kind": "rulebox-feedback", "notifications": ["UNREQUESTED_NOTICES" * 10000]},
            {"kind": "company-research-record", "originalQuestions": ["처음의 질문"],
             "previousInterpretation": "이전 해석", "observations": ["UNREQUESTED_COMPANY" * 10000]}]
    past = {"summary": "이전 판단", "hypothesis": "이전의 잠정 설명", "quality": {"status": "observation-only"},
            "previousFacts": ["UNREQUESTED_OLD_FACT" * 10000]}
    untouched = copy.deepcopy(rows)
    saved = []
    def save(value):
        validate_execution_input(value); saved.append(value); return "indexed-" + str(len(saved))
    def decide(envelope, _):
        ctx = envelope["retrievalContext"]
        test.assertEqual(indexed_retrieval.VERSION, envelope["promptVersion"])
        test.assertNotIn("UNREQUESTED", envelope["prompt"])
        test.assertEqual(original["questionContract"], ctx["continuity"]["researchResults"][0]["questionContract"])
        source = next(row for row in ctx["memoryIndex"] if row.get("caseId") == original["caseId"])
        ready = [{"caseId": original["caseId"], "status": "ready", "selectedFactIds": ["report-2025"], "missingEvidence": []}]
        result = scoped_decision("finish", ["report-2025"], ready)
        if not ctx["trace"]:
            result.update(action="read", requests=[{"tool": "read_memory", "category": "memories", "memoryId": source["memoryId"],
                "path": path} for path in ("/lastResearch", "/latestSourceAnswer")])
        return result
    packet, previous, research, trace = retrieve_working_evidence(research_session(p), p, [past], rows, decide, save, 262144)
    test.assertEqual("section-index", trace[0]["reads"][0]["result"]["status"])
    detail = trace[0]["reads"][1]["result"]["records"][0]
    test.assertEqual(original["latestSourceAnswer"], detail["content"])
    test.assertEqual(hashlib.sha256(json.dumps(detail["content"], sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":")).encode()).hexdigest(), detail["contentHash"])
    frozen = freeze_execution_input(packet, previous, research, retrieval_trace=trace)
    validate_execution_input(frozen)
    test.assertEqual(1, frozen["memoryCoverage"]["detailSectionsIncluded"])
    test.assertLess(len(frozen["prompt"].encode()), 90000)
    test.assertNotIn("UNREQUESTED", frozen["prompt"])
    test.assertIn(original["latestSourceAnswer"]["text"], frozen["prompt"])
    test.assertEqual(untouched, rows)
    from digital_twin.modules.ai_orchestration.domain.business_research import evaluate_thesis
    test.assertEqual([], evaluate_thesis(next(row for row in research if row.get("kind") == "business-thesis"), packet))
    tools = ObservationReadTools(research_session(p), [past], rows, indexed=True)
    request = trace[0]["reads"][0]["request"]
    with test.assertRaises(ReadRequestError):
        tools.resolve({**request, "path": "/lastResearch/result"})
    result = tools.read(tools.resolve(request)); tools.admit(result)
    tools.resolve({**request, "path": "/lastResearch/result"})
    for bad in ({**request, "memoryId": "foreign-memory"}, {**request, "category": "analyses"}):
        with test.assertRaises(ReadRequestError):
            tools.resolve(bad)
    with test.assertRaises(ReadRequestError):
        indexed_retrieval.validate_decision({**scoped_decision("read", []), "requests": [{**request, "category": []}]})
    # Old V17/V13 envelopes reconstruct exactly with their original builders.
    old_packet = business_packet(); old_packet["researchKnowledge"] = context(old_packet)
    old = freeze_execution_input(old_packet, [], [])
    old["promptVersion"] = KNOWLEDGE_PROMPT_VERSION
    old["prompt"] = knowledge_prompt(old_packet, old["previousAnalyses"], old["researchResults"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    validate_execution_input(old)
    repaired = freeze_repair_input(old, {"summary": "수정 대상"}, ["출력 규격 확인"], "legacy-parent")
    repaired["promptVersion"] = KNOWLEDGE_REPAIR_PROMPT_VERSION
    repaired["prompt"] = repair_prompt(knowledge_prompt(old_packet, repaired["previousAnalyses"], repaired["researchResults"]), repaired["repair"])
    repaired["promptHash"] = hashlib.sha256(repaired["prompt"].encode()).hexdigest()
    validate_execution_input(repaired)


def check_exclusion_growth(test):
    from test_research_knowledge import registered, methods
    from test_business_research import business_packet
    from digital_twin.modules.research_knowledge.domain.context import build_context, validate_context
    p = {**business_packet(), "capturedAt": "2026-03-03T00:00:00Z"}
    original = registered()
    rows = [{**original, "claimId": str(i) * 64, "reviewReceipt": {"reason": "과거 독립 검토 " * 1400}} for i in range(24)]
    for limit in (32000, 26000):
        result = build_context(p, methods(), rows, max_bytes=limit)
        test.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), limit)
        test.assertEqual(24, len(result["claims"]) + result["exclusionSummary"]["count"])
        test.assertLessEqual(len(result["excluded"]), 4)
        test.assertEqual(result["exclusionSummary"]["count"], sum(result["exclusionSummary"]["reasonCounts"].values()))
        validate_context(result, p)
