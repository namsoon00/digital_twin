"""Observed terminal gaps, bounded completion, immutable compaction and gap receipts."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain import (
    reliable_retrieval as reliable, opinion_retrieval, opinion_memory, question_purpose,
    compact_opinion_prompt as compact, opinion_prompt, memory_reference, gap_read_progress)
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, EvidenceContractError


def packet():
    from test_efficient_retrieval import packet as original
    return {**original(), "opinionMemoryPolicy": opinion_memory.VERSION,
        "questionPurposePolicy": question_purpose.VERSION, "memoryReadPolicy": memory_reference.VERSION,
        "retrievalReliabilityPolicy": reliable.MARKER}


class ReliableOpinionCompletionTests(unittest.TestCase):
    def test_terminal_scoped_gaps_are_preserved_without_a_repair_call(self):
        from test_scoped_knowledge_research import scoped_decision
        scope = {"caseId": "original-question", "status": "waiting", "selectedFactIds": [],
                 "missingEvidence": ["원래 기간의 현금흐름 표와 주석을 확인해야 합니다."]}
        raw = {**scoped_decision("defer", ["quote-1"], [scope]), "purpose": "original-question", "blocker": "required-source",
               "missingEvidence": []}
        before = deepcopy(raw)
        with self.assertRaises(ReadRequestError): opinion_retrieval.validate_decision(raw)
        fixed = reliable.validate_decision(raw)
        self.assertEqual("defer", fixed["action"])
        self.assertEqual(scope["missingEvidence"], fixed["missingEvidence"])
        self.assertEqual(before, raw)
        self.assertEqual(fixed, reliable.validate_decision(fixed))
        raw["missingEvidence"] = ["추가로 비교 기간의 원문이 필요합니다."]
        self.assertEqual(2, len(reliable.validate_decision(raw)["missingEvidence"]))
        raw["questionScopes"] = []
        raw["missingEvidence"] = []
        with self.assertRaises(ReadRequestError): reliable.validate_decision(raw)

    def test_final_round_has_no_new_reads_and_prior_successful_read_reaches_author(self):
        from test_efficient_retrieval import decision
        value, saved = packet(), []
        def save(env):
            validate_execution_input(env); saved.append(deepcopy(env)); return "read-" + str(len(saved))
        def decide(env, _):
            if len(saved) == 1:
                path = next(p for p in env["retrievalContext"]["readableContextPaths"] if p.endswith("/metricProvenance"))
                return decision("read", [{"tool": "read_context", "path": path}])
            self.assertNotIn("read", env["outputSchema"]["properties"]["action"]["enum"])
            self.assertEqual(0, env["outputSchema"]["properties"]["requests"]["maxItems"])
            self.assertTrue(env["retrievalContext"]["trace"][0]["reads"][0]["result"]["content"])
            return decision("finish")
        model = Mock(side_effect=decide)
        final, history, memory, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value["facts"]),
            value, [], [], model, save, 262144, max_rounds=2)
        self.assertEqual(2, model.call_count)
        self.assertEqual("ready", final["retrieval"]["status"])
        validate_execution_input(freeze_execution_input(final, history, memory, retrieval_trace=trace))
        with self.assertRaises(ReadRequestError):
            reliable.validate_scope(decision("read"), saved[-1]["retrievalContext"], {})
        self.assertLess(reliable.reserve(trace), reliable.reserve([]))

    def test_lossless_encoding_preserves_counter_evidence_and_source_key_collisions(self):
        fact = {"reportedValues": {"cash": -42}, "period": "2026-Q2", "counterEvidence": "회수 지연 가능성 " * 80}
        raw = {"facts": [fact], "prior": {"source": deepcopy(fact)}, "originalQuestion": "현금 감소의 원인은 무엇인가?"}
        before = deepcopy(raw)
        encoded, referenced = compact.encode(raw)
        self.assertTrue(referenced)
        self.assertEqual(raw, compact.expand(encoded))
        self.assertEqual(before, raw)
        self.assertLess(len(compact.dumps(encoded)), len(compact.dumps(raw)))
        raw["source"] = {compact.REF: "/facts/0"}
        self.assertEqual((raw, False), compact.encode(raw))
        with self.assertRaises(EvidenceContractError): compact.expand({compact.REF: "/not-issued"})

    def test_author_repair_review_replay_and_old_frozen_contract_are_unchanged(self):
        from test_reviewed_opinion_memory import fixture, approved
        p, memories, trace, _, raw = fixture()
        old = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        before = deepcopy(old)
        p["retrievalReliabilityPolicy"] = reliable.MARKER
        current = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        self.assertEqual(compact.PROMPT_VERSION, current["promptVersion"])
        for env in (current, freeze_repair_input(current, raw, ["근거 연결"], "parent"),
                    freeze_review_input(approved(p, memories, raw)), old):
            validate_execution_input(env)
            broken = deepcopy(env); broken["prompt"] += "changed"
            with self.assertRaises(EvidenceContractError): validate_execution_input(broken)
        self.assertEqual(before, old)
        self.assertEqual(opinion_prompt.PROMPT_VERSION, old["promptVersion"])
        self.assertEqual(old["outputSchema"], current["outputSchema"])

    def test_gap_source_is_linked_to_requirement_and_selection_is_not_answer_completion(self):
        from question_assessment_checks import fixture
        from digital_twin.modules.ai_orchestration.application.opinion_preparation import gap_reads
        p, session, case, document = fixture()
        p.update(opinionMemoryPolicy=opinion_memory.VERSION, retrievalReliabilityPolicy=reliable.MARKER)
        case["questionAssessment"] = {"missingRequirements": ["현금흐름 원문과 비교 기간"], "answerParts": {"gaps": [
            {"requirementIndex": 0, "role": "essential", "cause": "unread-source", "nextAction": "연결된 원문을 확인합니다."}]}}
        docs = session.question_document_index({document["historicalDocumentReference"]["evidenceId"]: [case["caseId"]]}, document_views=True)
        views, work = gap_reads(session, p, [case], docs, [], set())
        p["gapReadWork"] = work
        before = gap_read_progress.coverage(p)
        self.assertEqual("read-not-selected", before["requirements"][0]["status"])
        p["facts"] += list(views.values())
        self.assertEqual("selected-needs-assessment", gap_read_progress.coverage(p)["requirements"][0]["status"])
        assessed = gap_read_progress.reviewed(p, {"caseId": case["caseId"], "answerStatus": "partial",
            "missingRequirements": ["비교 기간의 원문"], "researchReviewInputId": "review"})
        self.assertEqual("partial", assessed["answerStatus"])
        self.assertEqual(["비교 기간의 원문"], assessed["remainingRequirements"])
        self.assertNotIn("resolved", str(assessed))
        self.assertEqual({}, gap_read_progress.reviewed(p, {"caseId": "other"}))

    def test_policy_change_renews_failed_work_once_without_clock_based_retries(self):
        from digital_twin.modules.ai_orchestration.domain.execution_lanes import material_key
        value = packet(); old = deepcopy(value); old.pop("retrievalReliabilityPolicy")
        self.assertNotEqual(material_key(old, []), material_key(value, []))
        value["capturedAt"] = "2099-01-01T00:00:00Z"
        self.assertEqual(material_key(packet(), []), material_key(value, []))

    def test_real_input_limit_waits_before_model_without_manufacturing_a_judgment(self):
        from unittest.mock import patch
        from test_ai_control import AIControlTests, PACKET
        from digital_twin.modules.ai_orchestration.domain.execution_lanes import VERSION
        service, store, planner = AIControlTests().runner(read_planner=Mock(),
            evidence=Mock(return_value=ObservationEvidenceSession(deepcopy(PACKET), deepcopy(PACKET["facts"]))))
        service.settings.update(aiObservationLanePolicy=VERSION)
        store.claim.return_value["capability"] = "market-observe"
        store.last_processing_outcome.return_value = {}
        error = EvidenceContractError("observation evidence exceeds context budget")
        error.required_baseline_budget = True
        error.budget_diagnostic = {"limitBytes": 96000, "usedBytes": 98000, "reservedBytes": 16000}
        with patch("digital_twin.modules.ai_orchestration.application.control.retrieve_working_evidence", side_effect=error):
            self.assertEqual("awaiting-evidence", service.run_once()["status"])
        result = store.complete.call_args.args[1]
        self.assertEqual("required-input-budget", result["processingOutcome"]["reasonCode"])
        self.assertFalse(result["processingOutcome"]["modelCalled"])
        self.assertNotIn("summary", result)
        planner.assert_not_called(); service.read_planner.assert_not_called()
        store.save_execution_input.assert_not_called(); store.fail.assert_not_called()

    def test_trace_space_is_not_double_charged_and_actual_overflow_retains_safe_diagnostics(self):
        from test_efficient_retrieval import decision
        from digital_twin.modules.ai_orchestration.application.working_retrieval import author_packet
        from digital_twin.modules.ai_orchestration.domain.retrieval import response_audit
        from digital_twin.modules.reasoning.contracts import source_packet_bytes
        value = packet(); session = ObservationEvidenceSession(value, value["facts"])
        raw = {**decision("defer"), "reason": "검증" * 250,
            "missingEvidence": [str(i) + "비교 원문 " * 80 for i in range(4)], "blocker": "required-source"}
        trace = [{"inputId": "input", "decision": raw, "response": response_audit(raw), "reads": [], "status": "valid", "correction": False}]
        seen = {row["id"]: row for row in value["facts"]}
        base = author_packet(session, value, set(seen), seen, [], trace, "deferred", contract=reliable)
        value["frozenAuditMetadata"] = "x" * max(0, 96000-source_packet_bytes(base)-reliable.reserve(trace)-256)
        final = author_packet(session, value, set(seen), seen, [], trace, "deferred", reliable.reserve(trace), reliable)
        self.assertLess(source_packet_bytes(final), 96000)
        self.assertEqual({row["id"] for row in base["facts"]}, {row["id"] for row in final["facts"]})
        with self.assertRaises(EvidenceContractError) as raised:
            author_packet(session, value, set(seen), seen, [], trace, "deferred", 16384, reliable)
        diagnostic = raised.exception.budget_diagnostic
        self.assertGreater(diagnostic["usedBytes"], diagnostic["limitBytes"])
        self.assertNotIn("frozenAuditMetadata", str(diagnostic))


if __name__ == "__main__": unittest.main()
