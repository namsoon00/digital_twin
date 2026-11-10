"""Observed terminal gaps, bounded completion, immutable compaction and gap receipts."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

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
    def test_remaining_rounds_leave_room_for_an_already_read_financial_source(self):
        from digital_twin.modules.ai_orchestration.application import working_retrieval as engine
        from digital_twin.modules.ai_orchestration.domain import retrieval_budget
        from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence
        from digital_twin.modules.reasoning.contracts import source_packet_bytes
        from test_efficient_retrieval import decision
        key = 'report-2025-12-31'
        value = packet(); value['frozenAuditMetadata'] = 'x' * 60000
        next(f for f in value['facts'] if f['id'] == key)['metricProvenance']['revenue']['sourceNote'] = 'original source note ' * 360
        candidates = deepcopy(value['facts'])
        value['facts'], value['coverage'] = select_evidence([f for f in value['facts'] if f['id'] != key])
        def run(legacy):
            saved = []
            def save(env):
                validate_execution_input(env); saved.append(deepcopy(env)); return 'input-' + str(len(saved))
            def choose(env, _):
                context = env['retrievalContext']; known = {r['factId'] for r in context['knownFacts']}
                if len(saved) == 1:
                    raw = decision('read', [{'tool': 'read_report', 'factId': key, 'metrics': ['revenue']}])
                elif len(saved) == 2:
                    path = next(p for p in context['readableContextPaths'] if p.startswith('/retrieval/trace/') and p.endswith('/metricProvenance'))
                    raw = decision('read', [{'tool': 'read_context', 'path': path}])
                else:
                    raw = decision('finish')
                raw['selectedFactIds'] = list(dict.fromkeys(context['selectedFactIds'] + ([key] if key in known else [])))
                return raw
            source = ObservationEvidenceSession(value, candidates)
            if legacy:
                with patch.object(retrieval_budget, 'reserve', side_effect=lambda contract, trace, remaining: contract.reserve(trace)):
                    result = retrieve_working_evidence(source, value, [], [], choose, save, 262144)
            else:
                result = retrieve_working_evidence(source, value, [], [], choose, save, 262144)
            return result, saved
        (old, _, _, old_trace), old_inputs = run(True)
        self.assertEqual('context-budget', old['retrieval']['status'])
        self.assertEqual(2, len(old_inputs))
        failed = retrieval_budget.blockage(old)
        self.assertEqual('retrieval-context-budget', failed['reasonCode'])
        self.assertGreater(failed['budgetFailure']['usedBytes'], 96000)
        self.assertEqual(failed['budgetFailure'], old_trace[-1]['admissionFailure'])
        (final, history, memories, trace), inputs = run(False)
        self.assertEqual('ready', final['retrieval']['status'])
        self.assertEqual(3, len(inputs))  # Same three-call ceiling; the writer is still separate.
        self.assertNotIn('read', inputs[-1]['outputSchema']['properties']['action']['enum'])
        self.assertLess(source_packet_bytes(final), 96000)
        annual = next(f for f in final['facts'] if f['id'] == key)
        original = next(f for f in candidates if f['id'] == key)
        self.assertEqual(original['reportedValues']['revenue'], annual['reportedValues']['revenue'])
        self.assertEqual(original['periodEnd'], annual['periodEnd'])
        self.assertEqual(original['metricProvenance']['revenue'], annual['metricProvenance']['revenue'])
        self.assertTrue(trace[1]['reads'][0]['result']['content'])
        validate_execution_input(freeze_execution_input(final, history, memories, retrieval_trace=trace))

    def test_budget_policy_retries_only_a_previous_scoped_capacity_wait_once(self):
        from digital_twin.modules.ai_orchestration.domain import retrieval_budget, execution_lanes
        value = packet(); identity = execution_lanes.material_key(value, [])
        value[retrieval_budget.FIELD] = retrieval_budget.VERSION
        self.assertEqual(identity, execution_lanes.material_key(value, []))
        previous = {'retrievalAdmission': {'status': 'context-budget', 'worldId': value['worldId'], 'policy': None}}
        self.assertTrue(retrieval_budget.retry_allowed(previous, value))
        for change in ({'policy': retrieval_budget.VERSION}, {'worldId': 'another-world'}, {'status': 'deferred'}, {'status': 'repeated-read'}):
            self.assertFalse(retrieval_budget.retry_allowed({'retrievalAdmission': {**previous['retrievalAdmission'], **change}}, value))
        self.assertFalse(retrieval_budget.retry_allowed({}, value))
        self.assertEqual(2048, retrieval_budget.reserve(reliable, [], 0))
        self.assertLess(retrieval_budget.reserve(reliable, [], 1), retrieval_budget.reserve(reliable, [], 2))
        for invalid in (-1, 4, True, 1.5):
            with self.assertRaises(ValueError): retrieval_budget.reserve(reliable, [], invalid)
        error = EvidenceContractError('observation evidence exceeds context budget')
        error.budget_diagnostic = {'scope': 'source-packet', 'usedBytes': 98000, 'limitBytes': 96000, 'snapshotId': 'private-source', 'sourceText': 'secret'}
        diagnostic = retrieval_budget.failure(error, 'read-admission', 6144, 96000)
        self.assertNotIn('secret', str(diagnostic)); self.assertNotIn('private', str(diagnostic))
        for status, action, expected in (('context-budget', 'read', 'retrieval-context-budget'),
                ('repeated-read', 'read', 'repeated-read'), ('round-limit', 'read', 'retrieval-round-limit'),
                ('deferred', 'finish', 'selected-evidence-insufficient')):
            self.assertEqual(expected, retrieval_budget.blockage({'retrieval': {'status': status, 'steps': [{'action': action}]}})['reasonCode'])
        self.assertEqual({}, retrieval_budget.blockage({'retrieval': {'status': 'deferred', 'steps': []}}))

    def test_retry_reopens_current_report_and_finishes_without_repeating_the_read_plan(self):
        from digital_twin.modules.ai_orchestration.domain import read_continuation, focused_retrieval
        from digital_twin.modules.reasoning.contracts import HISTORICAL_CONTEXT_BUDGET_POLICY
        from test_efficient_retrieval import decision
        value, saved = packet(), []
        value['historicalContextPolicy'] = HISTORICAL_CONTEXT_BUDGET_POLICY
        source = ObservationEvidenceSession(value, value['facts'])
        request = {'tool': 'read_report', 'factId': 'report-2025-12-31', 'metrics': ['revenue']}
        plan = {'request': request, 'fromInputId': 'prior-input', 'fromAttempt': 1,
                'questionScope': read_continuation.question_scope([]), 'sourceFactHash': 'older-revision'}
        def save(env):
            validate_execution_input(env); saved.append(deepcopy(env)); return 'resume-' + str(len(saved))
        def choose(env, _):
            self.assertEqual(focused_retrieval.CONTINUATION_VERSION, env['promptVersion'])
            read = env['retrievalContext']['resumedSourceReads']['reads'][0]
            self.assertTrue(read['sourceChanged'])
            self.assertNotIn('result', read)
            self.assertIn(request['factId'], {f['id'] for f in env['current']['facts']})
            return {**decision('finish'), 'selectedFactIds': env['retrievalContext']['selectedFactIds'] + [request['factId']]}
        model = Mock(side_effect=choose)
        final, history, memories, trace = retrieve_working_evidence(source, value, [], [], model, save,
            262144, resume_read_plans=[plan])
        self.assertEqual(1, model.call_count)
        self.assertEqual('ready', final['retrieval']['status'])
        self.assertEqual(1, len(trace))
        self.assertFalse(trace[0]['reads'])
        self.assertIn(request['factId'], {f['id'] for f in final['facts']})
        validate_execution_input(freeze_execution_input(final, history, memories, retrieval_trace=trace))

    def test_read_resume_rejects_changed_question_unissued_sources_cursors_and_budget_overflow(self):
        from digital_twin.modules.ai_orchestration.application.read_continuation import reopen
        from digital_twin.modules.ai_orchestration.domain import read_continuation as policy
        source = Mock()
        source.read_document.side_effect = lambda key: {'facts': [{'id': key, 'evidenceView': {'sourceFactHash': 'current'}, 'text': 'x' * 13000}]}
        def plan(key, **extra):
            return {'request': {'tool': 'read_document', 'factId': key}, 'fromInputId': key, 'fromAttempt': 1,
                    'sourceFactHash': 'current', 'questionScope': policy.question_scope([]), **extra}
        a, b = plan('a'), plan('b')
        invalid = plan('cursor'); invalid['request']['cursor'] = 'old-session-cursor'
        work = reopen(source, [plan('changed', questionScope='old-question'), plan('missing'), invalid], [], [], [], set())
        self.assertEqual({'question-contract-changed', 'source-not-issued', 'unsupported-read-plan'}, {r['reason'] for r in work['omitted']})
        source.read_document.assert_not_called()
        work = reopen(source, [a, b], [{'factId': 'a'}, {'factId': 'b'}], [], [], set())
        self.assertEqual(1, len(work['reads']))
        self.assertEqual('context-budget', work['omitted'][0]['reason'])
        self.assertLessEqual(work['bytes'], policy.MAX_BYTES)

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


    def test_focused_history_paths_are_scoped_exact_and_preserved_for_author(self):
        from digital_twin.modules.ai_orchestration.domain import focused_retrieval, focused_retrieval_view
        from digital_twin.modules.reasoning.contracts import HISTORICAL_CONTEXT_BUDGET_POLICY
        from test_efficient_retrieval import decision
        value = packet(); value["historicalContextPolicy"] = HISTORICAL_CONTEXT_BUDGET_POLICY
        original = "이전에 발송한 본문을 그대로 보존합니다. " * 130
        value["lastDeliveredNotification"] = {"auditNote": original}
        inputs = []
        path = "/current/lastDeliveredNotification/auditNote"
        def save(env):
            validate_execution_input(env); inputs.append(deepcopy(env)); return "input-" + str(len(inputs))
        def choose(env, _):
            self.assertEqual(focused_retrieval.VERSION, env["promptVersion"])
            raw = decision("read", [{"tool": "read_context", "path": path}]) if not env["retrievalContext"]["trace"] else decision("finish")
            raw["selectedFactIds"] = env["retrievalContext"]["selectedFactIds"]
            return raw
        final, history, memories, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value["facts"]),
            value, [], [], choose, save, 262144, max_rounds=2)
        self.assertEqual(original, trace[0]["reads"][0]["result"]["content"])
        self.assertEqual(original, final["lastDeliveredNotification"]["auditNote"])
        validate_execution_input(freeze_execution_input(final, history, memories, retrieval_trace=trace))
        with self.assertRaises(EvidenceContractError):
            focused_retrieval_view.read(inputs[0]["current"], inputs[0]["retrievalContext"],
                {"tool": "read_context", "path": "/current/accountId"})
        forged = deepcopy(inputs[1]); forged["retrievalContext"]["trace"][0]["reads"][0]["result"]["content"] = "changed"
        with self.assertRaises(EvidenceContractError): validate_execution_input(forged)

    def test_focused_routing_reduces_repeated_delivery_text_and_preserves_legacy_replay(self):
        from digital_twin.modules.ai_orchestration.domain import focused_retrieval
        from digital_twin.modules.reasoning.contracts import HISTORICAL_CONTEXT_BUDGET_POLICY
        from test_efficient_retrieval import decision
        value = packet(); original = "동일한 과거 본문의 원문입니다. " * 220
        value["deliveredNotificationContext"] = {"entries": [{"jobId": "receipt", "text": original}]}
        # Presentation alone is tested here; notification eligibility remains
        # owned by the separate receipt/quality contracts.
        envelopes = []
        def choose(env, _):
            envelopes.append(deepcopy(env))
            return {**decision("defer"), "missingEvidence": ["같은 기간의 원문이 더 필요합니다."], "blocker": "required-source"}
        retrieve_working_evidence(ObservationEvidenceSession(value, value["facts"]), value, [], [], choose, lambda _: "input", 262144)
        old = envelopes[0]; validate_execution_input(old)
        current = {**old["current"], "historicalContextPolicy": HISTORICAL_CONTEXT_BUDGET_POLICY}
        new = focused_retrieval.freeze(current, old["retrievalContext"], 262144); validate_execution_input(new)
        self.assertLess(len(new["prompt"].encode()), len(old["prompt"].encode()))
        self.assertEqual(old["current"]["facts"], new["current"]["facts"])
        self.assertEqual(original, new["current"]["deliveredNotificationContext"]["entries"][0]["text"])
        validate_execution_input(old)


if __name__ == "__main__": unittest.main()
