"""Model-selected reads retain provenance, bounded replay and final publication fences."""
import copy
import hashlib
import json
import unittest
from unittest.mock import Mock

from digital_twin.modules.reasoning.public import ObservationEvidenceReader
from digital_twin.modules.reasoning.contracts import EvidenceContractError, validate_evidence_packet
from digital_twin.modules.ai_orchestration.application.retrieval import retrieve_evidence, RetrievalLeaseLost
from digital_twin.modules.ai_orchestration.domain.retrieval import validate_read_decision
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, validate_execution_input,
    CITABLE_PROMPT_VERSION, CITABLE_REPAIR_PROMPT_VERSION,
)
from test_ai_control import SUBJECT, PLAN
import test_ai_control as control_helpers


def session():
    source = Mock()
    source.metadata.return_value = {"status": "ok", "aboxSnapshotId": "s1", "accountId": SUBJECT["accountId"]}
    source.snapshot_id.return_value = "s1"
    source.candidates.return_value = [
        {"id": "q", "kind": "stock", "symbol": "TEST", "currentPrice": 100},
        {"id": "f1", "kind": "evidence:financial-fact", "symbol": "TEST", "sourceAsOf": "2026-09-01", "profit": 12,
         "body": "verified report " * 780},
        {"id": "f2", "kind": "evidence:financial-fact", "symbol": "TEST", "sourceAsOf": "2026-08-01", "profit": 9},
        {"id": "flow", "kind": "flow-metric", "symbol": "TEST", "flow": -3},
    ]
    return ObservationEvidenceReader(source).capture_session(SUBJECT), source


def request(category="company", tool="query_facts", **values):
    fields = {"factId": ""} if tool == "read_fact" else {"cursor": ""}
    if tool == "query_facts":
        fields["kind"] = ""
    return {"tool": tool, "category": category, **fields, **values}


def read(*requests):
    return {"action": "read", "reason": "가설의 실적 근거와 반대 흐름을 확인합니다.", "requests": list(requests)}


FINISH = {"action": "finish", "reason": "조회한 실적과 반대 근거로 판단할 수 있습니다.", "requests": []}


class DirectedRetrievalTests(unittest.TestCase):
    def run_reads(self, responses, history=None, research=None):
        captured, source = session()
        packet = {**captured.packet(), "taskId": "task-1"}
        envelopes = []
        def save(envelope):
            validate_execution_input(envelope)
            envelopes.append(copy.deepcopy(envelope))
            return "input-" + str(len(envelopes))
        model = Mock(side_effect=responses)
        result = retrieve_evidence(captured, packet, history or [], research or [], model, save, 256 * 1024)
        return result, envelopes, model, source

    def test_captured_inventory_can_read_a_whole_fact_excluded_by_old_category_budget(self):
        captured, source = session()
        self.assertNotIn(SUBJECT["worldId"] + ":f1", {fact["id"] for fact in captured.packet()["facts"]})
        first = captured.read("company", limit=1)
        self.assertEqual(12, first["facts"][0]["profit"])
        self.assertEqual(1, first["nextOffset"])
        source.candidates.return_value[1]["profit"] = 999
        first["facts"][0]["profit"] = 999
        final = captured.select([SUBJECT["worldId"] + ":f1"])
        self.assertEqual(12, next(fact["profit"] for fact in final["facts"] if "profit" in fact))
        self.assertEqual(1, final["coverage"]["company"]["included"])
        self.assertEqual({"not-retrieved": 1}, final["coverage"]["company"]["exclusionReasons"])
        validate_evidence_packet(final)
        with self.assertRaises(EvidenceContractError):
            captured.read("company", fact_id="another-account:secret")
        source.snapshot_id.assert_called()  # Capture fenced before and after reads.
        self.assertEqual(1, source.candidates.call_count)  # Later reads never query a newer world.

    def test_second_decision_sees_first_result_and_final_prompt_has_only_read_evidence(self):
        self.assert_multiple_source_pages_fit_bounded_retrieval()
        self.assert_adaptive_pages_preserve_every_record_and_cursor()
        (packet, history, memories, trace), envelopes, model, _ = self.run_reads([
            read(request()), read(request("flow"), request("analyses", "recall_memory")), FINISH],
            history=[{"summary": "previous explanation", "previousFacts": [{"currentPrice": 80}]}])
        self.assertNotIn("verified report", envelopes[0]["prompt"])
        self.assertIn("verified report", envelopes[1]["prompt"])
        self.assertEqual(3, model.call_count)
        self.assertEqual("ready", packet["retrieval"]["status"])
        self.assertEqual({"stock", "evidence:financial-fact", "flow-metric"}, {row["kind"] for row in packet["facts"]})
        self.assertEqual("previous explanation", history[0]["summary"])
        self.assertEqual([], memories)
        envelope = freeze_execution_input(packet, history, memories, retrieval_trace=trace)
        validate_execution_input(envelope)
        self.assertEqual(trace, envelope["retrievalTrace"])
        broken = copy.deepcopy(envelope)
        broken["retrievalTrace"][0]["reads"][0]["result"]["facts"][0]["profit"] = 99
        with self.assertRaises(EvidenceContractError):
            validate_execution_input(broken)
        self.assertEqual(0, packet["coverage"]["valuation"]["included"])

    def assert_multiple_source_pages_fit_bounded_retrieval(self):
        captured, source = session()
        source.candidates.return_value[1]["body"] = "report-source " * 2100
        source.candidates.return_value[3]["body"] = "counter-source " * 1900
        captured = ObservationEvidenceReader(source).capture_session(SUBJECT)
        saved = []
        def save(envelope):
            validate_execution_input(envelope)
            saved.append(envelope)
            return "bounded-input-" + str(len(saved))
        selected, history, memories, trace = retrieve_evidence(captured, captured.packet(), [], [],
            Mock(side_effect=[read(request(), request("flow")), FINISH]), save, 256 * 1024)
        self.assertEqual("ready", selected["retrieval"]["status"])
        self.assertEqual({"ok"}, {row["result"].get("status", "ok") for row in trace[0]["reads"]})
        self.assertGreater(sum(len(json.dumps(row["result"]).encode()) for row in trace[0]["reads"]), 40 * 1024)
        self.assertLess(saved[1]["retrievalContext"]["resultBudgetBytesRemaining"], 56 * 1024)
        validate_execution_input(freeze_execution_input(selected, history, memories, retrieval_trace=trace))
        # A legal tool page may still overflow the separate final packet once
        # prior-delivery context and business memory are included.
        large_baseline = {**captured.packet(), "priorDeliveryContext": "x" * 28000}
        bounded, history, memories, trace = retrieve_evidence(captured, large_baseline, [], [],
            Mock(side_effect=[read(request(), request("flow")), FINISH]), save, 256 * 1024)
        self.assertEqual("context-budget", bounded["retrieval"]["status"])
        self.assertTrue(any(row["result"].get("reason") == "author-packet-budget" for row in trace[0]["reads"]))
        validate_evidence_packet(bounded)
        validate_execution_input(freeze_execution_input(bounded, history, memories, retrieval_trace=trace))

    def assert_adaptive_pages_preserve_every_record_and_cursor(self):
        for body_bytes, context_bytes in ((6000, 56000), (10000, 40000)):
            _, source = session()
            originals = [{"id": "flow-" + str(i), "kind": "flow-metric", "symbol": "TEST",
                          "body": str(i) * body_bytes} for i in range(4)]
            source.candidates.return_value = [source.candidates.return_value[0], *originals]
            captured = ObservationEvidenceReader(source).capture_session(SUBJECT)
            packet = {**captured.packet(), "priorDeliveryContext": "x" * context_bytes}
            seen = []
            def decide(envelope, _):
                context = envelope["retrievalContext"]
                if not context["trace"]:
                    return read(request("flow"))
                if len(context["trace"]) == 1:
                    result = context["trace"][0]["reads"][0]["result"]
                    self.assertGreater(len(result["facts"]), 0)
                    self.assertLess(len(result["facts"]), 4)
                    self.assertEqual([], result["omitted"])
                    # Trial cursors for larger rejected pages are not exposed.
                    self.assertEqual([result["nextCursor"]], [x["cursor"] for x in context["availableCursors"]])
                    return read(request("flow", cursor=result["nextCursor"]))
                return FINISH
            def save(envelope):
                validate_execution_input(envelope)
                seen.append(envelope)
                return "adaptive-" + str(len(seen))
            selected, history, memories, trace = retrieve_evidence(captured, packet, [], [], decide, save, 262144)
            self.assertEqual("ready", selected["retrieval"]["status"])
            facts = [f for step in trace for page in step["reads"] for f in page["result"]["facts"]]
            self.assertEqual(4, len(facts))
            self.assertEqual(4, len({f["id"] for f in facts}))
            self.assertEqual({x["body"] for x in originals}, {f["body"] for f in facts})
            validate_execution_input(freeze_execution_input(selected, history, memories, retrieval_trace=trace))
        # A single oversized leading fact may not be skipped to a later small
        # fact while the page cursor pretends that the requested range was read.
        source.candidates.return_value[1]["body"] = "large" * 9000
        captured = ObservationEvidenceReader(source).capture_session(SUBJECT)
        selected, _, _, trace = retrieve_evidence(captured, captured.packet(), [], [],
            Mock(return_value=read(request("flow"))), lambda _: "oversized", 262144)
        self.assertEqual("context-budget", selected["retrieval"]["status"])
        self.assertEqual([], trace[0]["reads"][0]["result"]["facts"])
        self.assertFalse(trace[0]["reads"][0]["result"].get("nextCursor"))
        # Share remaining packet space across both requested source families.
        captured, source = session()
        source.candidates.return_value = [source.candidates.return_value[0],
            *[{"id": "company-" + str(i), "kind": "evidence:financial-fact", "symbol": "TEST",
               "body": "x" * 9500} for i in range(4)],
            {"id": "counter", "kind": "flow-metric", "symbol": "TEST", "body": "y" * 6000}]
        captured = ObservationEvidenceReader(source).capture_session(SUBJECT)
        selected, history, memories, trace = retrieve_evidence(captured,
            {**captured.packet(), "priorDeliveryContext": "z" * 45000}, [], [],
            Mock(side_effect=[read(request(), request("flow")), FINISH]), lambda _: "balanced", 262144)
        self.assertEqual("ready", selected["retrieval"]["status"])
        self.assertTrue(all(page["result"].get("facts") for page in trace[0]["reads"]))
        validate_execution_input(freeze_execution_input(selected, history, memories, retrieval_trace=trace))

    def test_repetition_unknown_scope_and_lost_lease_cannot_extend_authority(self):
        (packet, _, _, _), _, model, _ = self.run_reads([read(request()), read(request())])
        self.assertEqual("repeated-read", packet["retrieval"]["status"])
        self.assertEqual(2, model.call_count)
        for bad in ({**request(), "accountId": "other"}, request(tool="execute_sql"), request(offset=-1), request(limit=True)):
            with self.subTest(bad=bad), self.assertRaises(EvidenceContractError):
                validate_read_decision(read(bad))
        captured, _ = session()
        model = Mock()
        with self.assertRaises(RetrievalLeaseLost):
            retrieve_evidence(captured, captured.packet(), [], [], model, lambda _: "", 65536)
        model.assert_not_called()

    def test_whole_oversized_memory_is_omitted_and_due_questions_are_preserved(self):
        required = {"kind": "brain-case", "caseId": "due", "reviewDue": True, "capability": "observe"}
        (packet, history, memories, trace), _, _, _ = self.run_reads(
            [read(request("analyses", "recall_memory"))], history=[{"summary": "x" * 80000}], research=[required])
        self.assertEqual(1, len(history))
        self.assertEqual(["summary"], history[0]["truncatedFields"])
        self.assertEqual("required-continuity", history[0]["memoryRole"])
        self.assertEqual([required], memories)
        self.assertEqual("context-budget", packet["retrieval"]["status"])
        self.assertEqual("context-budget", trace[0]["reads"][0]["result"]["status"])
        from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
        from ai_insight_fixtures import observation
        result = observation()
        result["input"]["retrieval"] = packet["retrieval"]
        self.assertEqual("rejected", local_quality(result)["status"])

    def test_controller_freezes_tool_rounds_before_final_generation_and_keeps_audit(self):
        captured, _ = session()
        final_plan = {**PLAN, "evidenceIds": [SUBJECT["worldId"] + ":q"]}
        service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(return_value=captured),
            read_planner=Mock(side_effect=[read(request("flow")), FINISH]))
        planner.return_value = final_plan
        self.assertEqual("completed", service.run_once()["status"])
        self.assertEqual(3, store.save_execution_input.call_count)
        self.assertEqual(2, service.read_planner.call_count)
        self.assertEqual(1, planner.call_count)
        envelope = planner.call_args.args[0]
        validate_execution_input(envelope)
        result = store.complete.call_args.args[1]
        self.assertEqual("ready", result["input"]["retrieval"]["status"])
        self.assertEqual(2, len(envelope["retrievalTrace"]))

    def test_previous_citable_prompts_and_repairs_still_replay_exactly(self):
        from digital_twin.modules.ai_orchestration.domain.observation_clock import citable_management_prompt, citable_management_schema
        from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
        captured, _ = session()
        author = freeze_execution_input(captured.packet(), [], [])
        repair = freeze_repair_input(author, PLAN, ["recheck"], "parent")
        for envelope, version in ((author, CITABLE_PROMPT_VERSION), (repair, CITABLE_REPAIR_PROMPT_VERSION)):
            envelope["promptVersion"] = version
            prompt = citable_management_prompt(envelope["current"], [], [])
            if version == CITABLE_REPAIR_PROMPT_VERSION:
                prompt = repair_prompt(prompt, envelope["repair"])
            envelope.update(prompt=prompt, promptHash=hashlib.sha256(prompt.encode()).hexdigest())
            envelope["outputSchema"] = citable_management_schema(envelope["current"], envelope["researchResults"])
            validate_execution_input(envelope)
        import gzip
        import json
        from pathlib import Path
        import runpy
        from unittest.mock import patch
        replay = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/replay-ai-observation.py"))["replay_input"]
        (selected, history, memory, trace), _, _, _ = self.run_reads([read(request("flow")), FINISH])
        original = freeze_execution_input(selected, history, memory, retrieval_trace=trace)
        with patch("digital_twin.modules.ai_orchestration.infrastructure.mysql_control.MySQLAIControlStore") as store:
            connection = store.return_value.connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchone.return_value = {"artifact_gzip": gzip.compress(json.dumps(original).encode())}
            self.assertEqual(original, replay(selected, {}, "frozen-id"))
            with self.assertRaises(ValueError):
                replay({**selected, "symbol": "OTHER"}, {}, "frozen-id")
            connection.execute.return_value.fetchone.return_value = None
            with self.assertRaises(ValueError):
                replay(selected, {}, "missing-id")

    def test_small_call_budget_preserves_author_review_capacity_without_routing_deadlock(self):
        captured, _ = session()
        model, save = Mock(), Mock()
        packet = captured.packet()
        result, history, _, trace = retrieve_evidence(captured, packet, [{"summary": "prior"}], [], model, save, 65536, 0)
        self.assertEqual("budget-fallback", result["retrieval"]["status"])
        self.assertEqual(packet["facts"], result["facts"])
        self.assertEqual([{"summary": "prior"}], history)
        self.assertEqual([], trace)
        model.assert_not_called(); save.assert_not_called()
        validate_execution_input(freeze_execution_input(result, history, [], retrieval_trace=trace))
