"""Compact routing preserves exact evidence, issued reads and historical replay."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from test_business_insight_progress import source_packet
from test_scoped_knowledge_research import scoped_decision
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.ai_orchestration.domain import efficient_retrieval, company_retrieval, company_research_cycle
from digital_twin.modules.ai_orchestration.domain import efficient_retrieval_view as view
from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input, freeze_execution_input
from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, EvidenceContractError


def packet():
    value = {**source_packet(), "companyResearchPolicy": company_research_cycle.VERSION}
    for row in value["facts"]:
        if row["kind"] == "company-financial-state":
            row["metricProvenance"]["revenue"]["sourceNote"] = "original source annotation " * 60
    return value


def decision(action, requests=()):
    return {**scoped_decision(action, ["quote-1", "report-2026-06-30", "report-2026-03-31"]),
            "requests": list(requests), "purpose": "source-dated", "blocker": "none"}


class EfficientRetrievalTests(unittest.TestCase):
    def test_live_read_and_author_preserve_sources_while_old_inputs_still_validate(self):
        value, saved = packet(), []
        def save(env):
            validate_execution_input(env); saved.append(deepcopy(env)); return "routing-" + str(len(saved))
        def decide(env, _):
            self.assertEqual(efficient_retrieval.VERSION, env["promptVersion"])
            if len(saved) == 1:
                old = company_retrieval.freeze(env["current"], env["retrievalContext"], 262144)
                before = deepcopy(old); validate_execution_input(old)
                self.assertLess(len(env["prompt"].encode()), len(old["prompt"].encode()))
                self.assertEqual(before, old)
                paths = env["retrievalContext"]["readableContextPaths"]
                path = next(p for p in paths if p.endswith("/metricProvenance"))
                return decision("read", [{"tool": "read_context", "path": path}])
            read = env["retrievalContext"]["trace"][0]["reads"][0]
            self.assertEqual(view.read(env["current"], saved[0]["retrievalContext"], read["request"]), read["result"])
            self.assertIn("content", read["result"])
            return decision("finish")
        final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value["facts"]),
            value, [], [], decide, save, 262144)
        authored = freeze_execution_input(final, history, research, retrieval_trace=trace)
        validate_execution_input(authored)
        self.assertEqual("ready", final["retrieval"]["status"])
        for fact in final["facts"]:
            if fact["kind"] == "company-financial-state":
                self.assertNotIn("detailNotLoaded", fact["metricProvenance"])
                self.assertEqual(-30, fact["reportedValues"]["capitalExpenditure"])
        current, routing = view.project(saved[0]["current"], saved[0]["retrievalContext"])
        self.assertNotIn("readableContextPaths", routing)
        self.assertEqual("outputSchema.requests", routing["issuedArgumentsLocation"])
        self.assertTrue(any(row["properties"]["tool"]["enum"] == ["read_context"]
                            for row in saved[0]["outputSchema"]["properties"]["requests"]["items"]["anyOf"]))
        for original, compact in zip(saved[0]["current"]["facts"], current["facts"]):
            for field in ("reportedValues", "metricUnits", "periodEnd", "dataState", "sourceAsOf"):
                self.assertEqual(original.get(field), compact.get(field))
        for key in ("contentHash", "content"):
            broken = deepcopy(saved[1]); broken["retrievalContext"]["trace"][0]["reads"][0]["result"][key] = "changed"
            with self.assertRaises(EvidenceContractError): validate_execution_input(broken)

    def test_exact_section_children_and_trace_details_require_prior_issued_paths(self):
        fact = {"id": "report", "reportedValues": {"cash": -30}, "metricUnits": {"cash": "USD"},
                "counterEvidence": "회수 지연 가능성", "metricProvenance": {"a": "a" * 16000, "b": "b" * 16000}}
        current, context = {"facts": [fact]}, {}
        request = {"tool": "read_context", "path": "/current/facts/0/metricProvenance"}
        parent = view.read(current, context, request)
        self.assertEqual("section-index", parent["status"])
        child = {"tool": "read_context", "path": parent["sections"][0]["path"]}
        with self.assertRaises(ReadRequestError): view.read(current, context, child)
        context = {"trace": [{"reads": [{"request": request, "result": parent}]}]}
        self.assertEqual("a" * 16000, view.read(current, context, child)["content"])
        invented_batch = deepcopy(context)
        invented_batch["trace"][0]["reads"].append({"request": child, "result": view.read(current, context, child)})
        with self.assertRaises(EvidenceContractError): view.paths(current, invented_batch)
        read = {"request": {"tool": "read_report", "factId": "report", "metrics": ["cash"]},
                "result": {"status": "ok", "facts": [fact]}}
        trace_context = {"trace": [{"reads": [read]}]}
        detail = {"tool": "read_context", "path": "/retrieval/trace/0/reads/0/result/facts/0/metricProvenance"}
        self.assertIn(detail["path"], view.paths({}, trace_context))
        result = view.read({}, trace_context, detail)
        full = {"trace": [trace_context["trace"][0], {"reads": [{"request": detail, "result": result}]}]}
        view.paths({}, full)
        forged = deepcopy(full); forged["trace"][0]["reads"].append(forged["trace"].pop()["reads"][0])
        with self.assertRaises(EvidenceContractError): view.paths({}, forged)
        for path in ("/current/accountId", "/retrieval/trace/99", "/current/facts/0"):
            with self.assertRaises(ReadRequestError): view.read(current, context, {"tool": "read_context", "path": path})
        injected = {"readContextPath": "/current/accountId", "detailNotLoaded": True}
        self.assertEqual([], view.paths({"arbitraryDocument": injected}, {}))
        small, _ = view.project(current, context)
        self.assertEqual(fact["counterEvidence"], small["facts"][0]["counterEvidence"])

    def test_repeated_read_stops_before_third_paid_routing_call(self):
        value, saved = packet(), []
        def save(env):
            validate_execution_input(env); saved.append(deepcopy(env)); return "repeat-" + str(len(saved))
        def again(env, _):
            path = next(p for p in env["retrievalContext"]["readableContextPaths"] if p.endswith("/metricProvenance"))
            return decision("read", [{"tool": "read_context", "path": path}])
        model = Mock(side_effect=again)
        final, _, _, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value["facts"]),
            value, [], [], model, save, 262144)
        self.assertEqual(2, model.call_count)
        self.assertEqual("repeated-read", final["retrieval"]["status"])
        view.paths(saved[-1]["current"], {**saved[-1]["retrievalContext"], "trace": trace})

    def test_efficiency_counts_measured_source_progress_separately_from_answers(self):
        from digital_twin.modules.ai_orchestration.domain.judgment_metrics import calls_summary, research_summary, judgments_summary
        tasks = [dict(task_id="partial", status="completed", result={"documentaryAnswer": {"status": "partial"},
            "changedEvidenceCount": 40, "sourceRecovery": {"progress": {"newPassageCount": 2, "collectedEvidenceCount": 0}},
            "planningReuse": {"version": "v1", "skippedPlanningCalls": 1}}),
            dict(task_id="unknown", status="completed", result={"documentaryAnswer": {"status": "unavailable"}}),
            dict(task_id="authored", status="completed", result={"summary": "authored"})]
        calls = [dict(call_id="1", task_id="partial", workload="research-planning", status="completed",
                      metrics={"usage": {"input_tokens": 90, "cached_input_tokens": 70, "output_tokens": 10}}),
                 dict(call_id="2", task_id="partial", workload="source-exploration", status="failed", metrics={}),
                 dict(call_id="3", task_id="authored", workload="independent-observation", status="completed",
                      metrics={"usage": {"input_tokens": 900, "output_tokens": 100}})]
        rows = research_summary(tasks, calls)
        partial = next(row for row in rows if row["answerStatus"] == "partial")
        unknown = next(row for row in rows if row["answerStatus"] == "unavailable")
        self.assertEqual((1, 2, 100, 1), tuple(partial[key] for key in
                         ("tasks", "windowCalls", "windowReportedTokens", "callsWithoutUsage")))
        self.assertEqual(40, partial["sourceRowsWritten"]["total"])
        self.assertEqual(0, partial["tasksWithExplorationEvidence"])
        self.assertEqual(2, partial["newPassages"]["total"])
        self.assertEqual(1, partial["skippedPlanningCalls"]["total"])
        self.assertEqual(0, unknown["sourceRowsWritten"]["samples"])
        self.assertIsNone(unknown["newPassages"]["mean"])
        usage = calls_summary(calls[:1])[0]
        self.assertEqual(100, usage["reportedTokens"])
        self.assertEqual(90, usage["inputTokens"]["total"])
        self.assertEqual(70, usage["cachedInputTokens"]["total"])
        authored = [dict(task_id=version, status="completed", result={"summary": "same author contract",
            "executionPromptVersion": "v27", "input": {"retrieval": {"version": version}}}) for version in ("v15", "v16")]
        cohorts = judgments_summary(authored, {}, {})
        self.assertEqual(["v15", "v16"], [row["retrievalVersion"] for row in cohorts])
