"""Economic meaning must survive source reads, review, memory and customer rendering."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.domain import investment_meaning as meaning, investment_meaning_prompt as prompts
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_review_input, freeze_repair_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.domain.insight_contract import narrative_digest
from digital_twin.modules.ai_orchestration.domain.insight_memory import receipt_facts
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession


def chain(question="다음 같은 기준의 분기 보고서에서 영업현금흐름과 투자 지출을 비교합니다."):
    ref = lambda metric: {"factId": "report-2026-06-30", "field": "reportedValues." + metric, "period": "current"}
    cash, capex = ref("operatingCashFlow"), ref("capitalExpenditure")
    return {"status": "analyzed", "question": "투자 후 남는 현금의 감소는 투자 집행과 회수 지연 중 무엇이 설명합니까?",
        "mechanism": "확인한 영업현금과 투자 지출은 자체자금 여유를 검토하는 출발점이며 반복 가능성은 별도로 확인합니다.",
        "evidence": [cash], "explanations": [
            {"explanation": "투자 집행이 잔여 현금에 영향을 주었을 수 있습니다.", "support": [capex], "against": [],
             "assessment": "possible", "reason": "원문 투자 지출은 확인했지만 현금 변화 전체를 설명한 것은 아닙니다."},
            {"explanation": "매출 회수 지연이 영업현금을 제약했을 수 있습니다.", "support": [], "against": [],
             "assessment": "unresolved", "reason": "매출채권과 운전자본의 비교 근거가 없어 아직 구분할 수 없습니다."}],
        "expectation": {"status": "unavailable", "actual": [], "expected": [],
            "meaning": "회사 전망과 시장 기대의 원문이 없어 기대 대비 개선 여부는 판단할 수 없습니다."},
        "nextCheck": {"question": question, "source": "회사의 같은 기간 현금흐름표와 운전자본 주석을 확인합니다.",
            "supports": "현금 회수가 유지되고 투자 증가가 잔여 현금의 감소를 설명하면 투자 집행 설명을 뒷받침합니다.",
            "weakens": "투자 증가 없이 채권 회수가 지연되면 투자 집행만으로 설명하기 어렵습니다."}}


def result():
    from test_business_insight_progress import business_result
    r = business_result()
    r["input"]["investmentMeaningPolicy"] = meaning.VERSION
    r["investmentMeaning"] = chain()
    return r


def authored_company(save=lambda env: "frozen", task_id=""):
    from ai_insight_fixtures import plan, ref
    from test_reliable_opinion_completion import packet
    from test_efficient_retrieval import decision
    from test_analyst_memory import assessment
    from digital_twin.modules.ai_orchestration.domain import analyst_memory, evidence_purpose
    from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, stamp
    from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
    from digital_twin.modules.reasoning.contracts import quote_clock_assessment
    p = packet(); p.update(capturedAt=stamp(), investmentMeaningPolicy=meaning.VERSION, analystMemoryPolicy=analyst_memory.VERSION)
    if task_id:
        p["taskId"] = task_id
    p["facts"][0]["sourceAsOf"] = "unknown"
    p["quoteAssessment"] = quote_clock_assessment(p["facts"], p["capturedAt"])
    p = evidence_purpose.annotate(p)
    def decide(env, _):
        value = decision("finish"); value["selectedFactIds"] = ["report-2026-06-30", "report-2026-03-31"]
        return value
    p, history, memories, trace = retrieve_working_evidence(ObservationEvidenceSession(p, p["facts"]), p, [], [], decide, save, 262144)
    raw = plan(); base = result()
    for key in ("summary", "hypothesis", "questions", "evidenceIds", "observations", "followUpConditions", "judgmentChange"):
        raw[key] = deepcopy(base[key])
    raw["questions"] = [{"question": chain()["nextCheck"]["question"], "capability": "observe"}]
    raw.update(investmentMeaning=chain(), analystAssessment={**assessment(p), "insightKind": "business",
        "meaning": "확인한 영업현금과 투자 지출은 자체자금 여유를 검토할 근거를 더합니다."},
        questionPurposes=[], businessResearch={"theses": [], "reviews": [], "coverageNote": "원문에서 확인한 현금과 투자 지출을 검토하고 원인 판별은 다음 자료에서 확인합니다."})
    cash = ref("reportedValues.operatingCashFlow", fact_id="report-2026-06-30")
    raw.update(evidenceIds=[cash["factId"]], comparison="첫 기업 현금흐름 관찰이며 과거 보고기간의 사실을 검토합니다.",
        portfolioImpact="관심 기업의 자체자금 여유를 확인할 근거이며 현재 매매 시점을 판단한 것은 아닙니다.",
        counterEvidence="회수 지연을 구분할 자료가 없어 투자 집행만으로 현금 변화를 설명할 수는 없습니다.")
    raw["notification"]["reason"] = "영업현금과 투자 지출을 분리해 자체자금 여유를 검토할 근거가 추가됐습니다."
    for key in raw["claimEvidence"]: raw["claimEvidence"][key] = [cash]
    author = freeze_execution_input(p, history, memories, retrieval_trace=trace)
    input_id = save(author)
    r = {**validate_plan(raw, p, memories), "input": p, "executionInputId": input_id,
         "observedAt": p["capturedAt"], "inputFingerprint": "company-meaning-source"}
    return r, author


class InvestmentMeaningTests(unittest.TestCase):
    def test_argument_revision_preserves_sources_legacy_inputs_and_repeat_identity(self):
        from digital_twin.modules.ai_orchestration.domain import argument_quality as argument, execution_lanes
        r, legacy = authored_company()
        before = deepcopy(legacy)
        p = deepcopy(legacy['current'])
        key = execution_lanes.material_key(p, legacy['researchResults'])
        p[argument.FIELD] = argument.VERSION
        self.assertEqual(key, execution_lanes.material_key(p, legacy['researchResults']))
        current = freeze_execution_input(p, legacy['previousAnalyses'], legacy['researchResults'], retrieval_trace=legacy['retrievalTrace'])
        self.assertEqual(argument.VERSIONS['author'], current['promptVersion'])
        self.assertEqual(legacy['current']['facts'], current['current']['facts'])
        self.assertEqual(legacy['outputSchema'], current['outputSchema'])
        repair = freeze_repair_input(current, r, ['문단의 정확한 원천 필드가 필요합니다.'], 'parent')
        review = freeze_review_input({**r, 'input': p})
        self.assertEqual(argument.VERSIONS['repair'], repair['promptVersion'])
        self.assertEqual(argument.VERSIONS['review'], review['promptVersion'])
        for env in (current, repair, review, legacy):
            validate_execution_input(env)
        self.assertEqual(before, legacy)
        for env in (current, repair, review):
            forged = deepcopy(env); forged['current'].pop(argument.FIELD)
            with self.assertRaises(ValueError): validate_execution_input(forged)
        self.assertLess(len(argument.INSTRUCTIONS.encode()) + len(argument.REVIEW_INSTRUCTIONS.encode()), 4096)
        self.assertNotIn('review-feedback', review['prompt'])  # Reviewer remains blind to process memory.

    def test_feedback_audit_separates_inclusion_repeated_checks_and_partial_check_passes(self):
        from digital_twin.modules.ai_orchestration.domain import argument_quality as argument, rejection_feedback
        from digital_twin.modules.ai_orchestration.domain.insight_quality import accept_review
        from ai_insight_fixtures import review
        r, _ = authored_company()
        verdict = review()
        verdict.update(judgmentChange={'supported': True, 'reason': '현재 원문과 의견의 변경 범위를 확인했습니다.'},
            analystAssessment={'supported': True, 'substantive': True, 'memoryIds': [], 'reason': '사업 현금창출의 의미를 원문과 대조했습니다.'},
            investmentMeaning={'supported': True, 'discriminating': False, 'expectationsSupported': True,
                'followUpLinked': True, 'reason': '두 설명은 함께 성립할 수 있어 경쟁 설명을 판별하지 못했습니다.'})
        verdict['sections']['portfolioImpact'] = {'supported': False, 'reason': '이익 비교 문장에 정확한 영업이익 필드가 인용되지 않았습니다.'}
        reviewed = freeze_review_input(r)
        r['quality'] = accept_review(r, verdict, 'old-review')
        memory = rejection_feedback.extract(r, 'old-rejected-task', r['observedAt'], reviewed)
        env = freeze_execution_input(r['input'], [], [memory])
        r['executionInputId'] = 'new-author'
        r['quality'] = accept_review(r, verdict, 'new-review')
        receipt = argument.feedback_use(r, env, [memory])
        self.assertTrue(receipt['feedback'][0]['includedInFinalInput'])
        self.assertEqual({'repeated'}, {i['checkStatus'] for i in receipt['feedback'][0]['issues'] if i['section'] != 'overall'})
        verdict['sections']['portfolioImpact']['supported'] = True
        r['quality'] = accept_review(r, verdict, 'newer-review')
        receipt = argument.feedback_use(r, env, [memory])
        states = {i['section']: i['checkStatus'] for i in receipt['feedback'][0]['issues']}
        self.assertEqual('repeated', states['investmentMeaning'])
        self.assertEqual('check-passed', states['portfolioImpact'])
        self.assertEqual('rejected', receipt['finalQualityStatus'])
        self.assertEqual('execution-audit-only', receipt['authority'])
        self.assertNotIn(memory['issues'][0]['reason'], str(receipt))
        r['hypothesis'] = '검토 이후 새 문장으로 변경된 초안입니다.'
        stale = argument.feedback_use(r, env, [memory])
        self.assertEqual('', stale['reviewInputId'])
        self.assertEqual({'not-reviewed'}, {i['checkStatus'] for i in stale['feedback'][0]['issues']})
        omitted = {**env, 'researchResults': []}
        self.assertEqual({'not-in-final-input'}, {i['checkStatus'] for i in argument.feedback_use(r, omitted, [memory])['feedback'][0]['issues']})
        self.assertIsNone(argument.feedback_use(r, env, [{**memory, 'symbol': 'foreign'}]))

    def test_rejected_feedback_reaches_routing_and_author_without_becoming_evidence(self):
        from digital_twin.modules.ai_orchestration.domain import rejection_feedback as feedback, execution_lanes
        from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
        from test_reliable_opinion_completion import packet
        from test_efficient_retrieval import decision
        r, _ = authored_company()
        r['counterEvidence'] = '기관 매도 때문에 주가가 하락했습니다.'
        r['quality'] = local_quality(r)
        self.assertEqual('rejected', r['quality']['status'])
        memory = feedback.extract(r, 'rejected-task', r['observedAt'])
        self.assertEqual('local-validation', memory['stage'])
        self.assertNotIn(r['counterEvidence'], str(memory))
        p, saved = packet(), []
        identity = execution_lanes.material_key(p, [])
        memories = feedback.admit([], [], [memory, memory])
        self.assertEqual(1, len(memories))
        self.assertEqual(identity, execution_lanes.material_key(p, memories))
        def save(env):
            validate_execution_input(env); saved.append(deepcopy(env)); return 'feedback-read'
        def choose(env, _):
            item = env['retrievalContext']['continuity']['researchResults'][0]
            self.assertEqual(memory['issues'], item['issues'])
            self.assertEqual('rejected-draft-feedback-only', item['authority'])
            return decision('finish')
        final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(p, p['facts']),
            p, [], [], choose, save, 262144, review_feedback=memories)
        author = freeze_execution_input(final, history, research, retrieval_trace=trace)
        validate_execution_input(author)
        self.assertEqual(memory['issues'], author['researchResults'][0]['issues'])
        self.assertEqual(final['facts'], author['current']['facts'])
        self.assertNotIn(feedback.KIND, {f['kind'] for f in final['facts']})
        self.assertLessEqual(sum(len(json.dumps(x, ensure_ascii=False).encode()) for x in memories), feedback.MAX_BYTES)
        from digital_twin.modules.ai_orchestration.domain.continuity import judgment_continuity
        blinded = freeze_review_input({**r, 'input': final, 'judgmentContinuity': judgment_continuity(author)})
        self.assertNotIn(memory['purpose'], blinded['prompt'])
        # A crowded author input loses optional process feedback, not its
        # already admitted financial baselines or the entire analysis turn.
        from digital_twin.modules.ai_orchestration.application import working_retrieval as engine
        from digital_twin.modules.reasoning.contracts import EvidenceContractError
        original = engine.freeze_execution_input
        def crowded(current, history, research, *args, **kwargs):
            if any(row.get('kind') == feedback.KIND for row in research):
                raise EvidenceContractError('observation execution exceeds context budget')
            return original(current, history, research, *args, **kwargs)
        model = Mock(return_value=decision('finish'))
        with patch.object(engine, 'freeze_execution_input', side_effect=crowded):
            fallback, _, fallback_memory, _ = retrieve_working_evidence(ObservationEvidenceSession(p, p['facts']),
                p, [], [], model, save, 262144, review_feedback=memories)
        model.assert_called_once()
        self.assertFalse(any(row.get('kind') == feedback.KIND for row in fallback_memory))
        self.assertEqual(final['facts'], fallback['facts'])
        from digital_twin.modules.ai_orchestration.domain.insight_contract import resolve_ref
        with self.assertRaises(ValueError):
            resolve_ref(final, {'period': 'current', 'factId': memory['taskId'], 'field': 'issues'})
        # Required memory always wins admission; feedback is optional.
        large = {'kind': 'research-context', 'reviewedAssessment': {'answer': 'x' * 40500}}
        self.assertEqual([], feedback.admit([], [large], [memory]))
        r['quality']['errors'] = ['fabricated criticism']
        self.assertIsNone(feedback.extract(r, 'forged', r['observedAt']))

    def test_independent_feedback_requires_the_exact_reviewed_draft_and_never_approves_it(self):
        from digital_twin.modules.ai_orchestration.domain import rejection_feedback as feedback
        from digital_twin.modules.ai_orchestration.domain.insight_quality import accept_review, quality_block
        from ai_insight_fixtures import review
        r, _ = authored_company()
        env = freeze_review_input(r)
        verdict = review()
        verdict['sections']['hypothesis'] = {'supported': False, 'reason': '경쟁 설명을 비교할 별도의 원문 근거가 부족합니다.'}
        verdict['investmentMeaning'] = {'supported': True, 'discriminating': False, 'expectationsSupported': True,
            'followUpLinked': True, 'reason': '경쟁 설명이 서로 판별 가능한 방식으로 비교되지 않았습니다.'}
        r['quality'] = accept_review(r, verdict, 'review-id')
        self.assertIsNone(feedback.extract(r, 'task', r['observedAt']))
        memory = feedback.extract(r, 'task', r['observedAt'], env)
        self.assertEqual('independent-review', memory['stage'])
        self.assertEqual('investmentMeaning', memory['issues'][0]['section'])
        self.assertIn('경쟁 설명', str(memory['issues']))
        self.assertTrue(quality_block(r))
        changed = deepcopy(env); changed['draft']['hypothesis'] = 'another draft'
        self.assertIsNone(feedback.extract(r, 'task', r['observedAt'], changed))
        r['quality']['status'] = 'accepted'
        self.assertIsNone(feedback.extract(r, 'task', r['observedAt'], env))

    def test_new_author_repair_review_and_old_inputs_keep_exact_frozen_contracts(self):
        from test_analyst_memory import assessment
        from test_reviewed_opinion_memory import fixture, approved
        from digital_twin.modules.ai_orchestration.domain import analyst_memory, reliable_retrieval
        p, memories, trace, _, raw = fixture()
        p.update(analystMemoryPolicy=analyst_memory.VERSION, retrievalReliabilityPolicy=reliable_retrieval.MARKER)
        p = analyst_memory.prepare(p, memories)
        raw["analystAssessment"] = assessment(p)
        legacy = freeze_execution_input(p, [], memories, retrieval_trace=trace); before = deepcopy(legacy)
        p["investmentMeaningPolicy"] = meaning.VERSION
        value = chain(); value.update(status="insufficient", evidence=[], explanations=[])
        raw["investmentMeaning"] = value
        authored = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        self.assertEqual(prompts.PROMPT_VERSION, authored["promptVersion"])
        for envelope in (authored, freeze_repair_input(authored, raw, ["경제적 연결 확인"], "parent"),
                         freeze_review_input(approved(p, memories, raw)), legacy):
            validate_execution_input(envelope)
            if envelope is not legacy:
                forged = deepcopy(envelope); forged["current"]["investmentMeaningPolicy"] = "changed"
                with self.assertRaises(ValueError): validate_execution_input(forged)
        self.assertEqual(before, legacy)

    def test_source_content_required_and_metadata_future_or_blocked_facts_rejected(self):
        r = result(); p, value = r["input"], r["investmentMeaning"]
        self.assertEqual(value, meaning.validate(value, p))
        for field in ("sourceAsOf", "periodEnd", "label"):
            bad = deepcopy(value); bad["evidence"][0]["field"] = field
            with self.subTest(field=field), self.assertRaises(ValueError): meaning.validate(bad, p)
        fact = next(f for f in p["facts"] if f["id"] == value["evidence"][0]["factId"])
        fact["judgementEvidenceUsable"] = False
        with self.assertRaises(ValueError) as caught: meaning.validate(value, p)
        from digital_twin.modules.ai_orchestration.domain.validation_diagnostic import task_failure, correction_issue
        self.assertEqual('economic-source-reference', task_failure('plan-validation', caught.exception)[1]['reasonCode'])
        self.assertIsNone(correction_issue(caught.exception))
        fact["judgementEvidenceUsable"] = True; fact["sourceAsOf"] = "2099-01-01T00:00:00Z"
        with self.assertRaises(ValueError): meaning.validate(value, p)

    def test_expectations_cannot_be_made_from_price_or_ai_assumptions(self):
        r = result(); p, value = r["input"], r["investmentMeaning"]
        forecast = {**p["facts"][0], "id": "forecast", "kind": "valuation-assumption", "forecast": 80,
                    "judgementEvidenceUsable": True}
        p["facts"].append(forecast)
        expected = {"factId": "forecast", "field": "forecast", "period": "current"}
        value["expectation"].update(status="compared", actual=value["evidence"], expected=[expected])
        with self.assertRaisesRegex(ValueError, "original source"): meaning.validate(value, p)
        forecast.update(kind="analyst-revision", source="verified-estimate-provider")
        meaning.validate(value, p)
        value["expectation"]["expected"] = value["evidence"]
        with self.assertRaisesRegex(ValueError, "distinct"): meaning.validate(value, p)

    def test_meaning_must_reach_core_claim_and_next_check_must_be_a_real_task(self):
        r = result(); self.assertEqual([], meaning.quality_errors(r))
        r["workQuestions"] = []
        self.assertEqual([meaning.VALUE_ERROR], meaning.quality_errors(r))
        r = result(); r["claimEvidence"]["hypothesis"] = []; r["claimEvidence"]["summary"] = []
        self.assertEqual([meaning.VALUE_ERROR], meaning.quality_errors(r))
        r = result(); r["investmentMeaning"]["explanations"][0]["support"] = []
        self.assertEqual([meaning.VALUE_ERROR], meaning.quality_errors(r))
        r = result(); r["investmentMeaning"]["nextCheck"]["weakens"] = r["investmentMeaning"]["nextCheck"]["supports"]
        self.assertEqual([meaning.VALUE_ERROR], meaning.quality_errors(r))

    def test_independent_review_and_review_digest_cover_the_complete_economic_chain(self):
        r = result()
        verdict = {"investmentMeaning": {"supported": True, "discriminating": True, "expectationsSupported": True,
            "followUpLinked": True, "reason": "원문과 경제적 연결 및 후속 과제를 확인했습니다."}}
        self.assertEqual([], meaning.review_errors(r, verdict))
        for field in ("supported", "discriminating", "expectationsSupported", "followUpLinked"):
            bad = deepcopy(verdict); bad["investmentMeaning"][field] = False
            self.assertTrue(meaning.review_errors(r, bad))
        digest = narrative_digest(r)
        r["investmentMeaning"]["mechanism"] = "검토 후 바뀐 경제적 연결은 다시 독립 검토를 받아야 합니다."
        self.assertNotEqual(digest, narrative_digest(r))

    def test_partial_answer_opens_only_issued_same_subject_originals_without_closing_question(self):
        from test_reviewed_opinion_memory import fixture, approved
        from digital_twin.modules.ai_orchestration.domain import opinion_memory, question_assessment
        p, memories, _, case, raw = fixture()
        reviewed = approved(p, memories, raw)
        case["questionAssessment"] = question_assessment.assessment(case, {"taskId": "reviewed"}, reviewed,
            reviewed["caseReviews"][0], reviewed["questionResolutions"][0], p["capturedAt"])
        case.update({k: p[k] for k in ("accountId", "symbol", "worldId")})
        original = deepcopy(case)
        contexts = [{"kind": "research-context", "body": case}]
        self.assertEqual({}, meaning.document_sources(p, contexts))
        p["investmentMeaningPolicy"] = meaning.VERSION
        sources = meaning.document_sources(p, contexts)
        self.assertTrue(sources)
        session = ObservationEvidenceSession(p, p["facts"])
        documents = session.question_document_index(sources, document_views=True)
        self.assertTrue(documents)
        self.assertTrue(session.read_document(documents[0]["factId"])["facts"])
        self.assertEqual(original, case)
        contexts[0]["body"] = {**case, "accountId": "other-account"}
        with self.assertRaisesRegex(ValueError, "subject scope"): meaning.document_sources(p, contexts)
        contexts[0]["body"] = {**case, "questionAssessment": {}}
        self.assertEqual({}, meaning.document_sources(p, contexts))
        contexts[0]["body"] = deepcopy(case)
        contexts[0]["body"]["questionAssessment"]["at"] = "2099-01-01T00:00:00Z"
        self.assertEqual({}, meaning.document_sources(p, contexts))

    def test_renderer_and_receipt_preserve_alternative_source_and_discriminating_check(self):
        from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
        r = result(); self.assertEqual([], local_quality(r)["errors"])
        body = render_ai_observation(r)
        for phrase in ("이번에 판단한 질문", "사업·가치에 미치는 경로", "경쟁 설명 비교", "기대와 실제의 비교",
                       r["investmentMeaning"]["nextCheck"]["supports"], r["investmentMeaning"]["nextCheck"]["weakens"]):
            self.assertIn(phrase, body)
        receipts = {f["id"]: f for f in receipt_facts(r)}
        self.assertEqual(-30, receipts["report-2026-06-30"]["reportedValues"]["capitalExpenditure"])
        self.assertEqual(body, render_ai_observation(deepcopy(r)))
        from digital_twin.modules.ai_orchestration.domain.memory_reference import brief
        remembered = brief(r, "analyses")
        self.assertEqual(r["investmentMeaning"], remembered["investmentMeaning"])
        self.assertEqual("historical-context-only", remembered["authority"])

    def test_company_observation_reaches_retrieval_without_a_usable_price_clock(self):
        from test_ai_control import AIControlTests
        from test_business_insight_progress import source_packet
        from test_efficient_retrieval import decision
        from digital_twin.modules.ai_orchestration.domain import execution_lanes, evidence_readiness, judgment_policy
        p = source_packet(); p["facts"][0]["sourceAsOf"] = "unknown"
        from digital_twin.modules.reasoning.contracts import quote_clock_assessment
        p["quoteAssessment"] = quote_clock_assessment(p["facts"], p["capturedAt"])
        service, store, planner = AIControlTests().runner()
        from digital_twin.modules.ai_orchestration.domain import rejection_feedback
        prior, _ = authored_company()
        prior['counterEvidence'] = '기관 매도 때문에 주가가 하락했습니다.'
        prior['quality'] = local_quality(prior)
        feedback = rejection_feedback.extract(prior, 'old-rejection', prior['observedAt'])
        class Store:
            feedback_reads = 0
            def __getattr__(self, name): return getattr(store, name)
            def review_feedback(self, *_):
                self.feedback_reads += 1
                return [feedback]
        service.store = wrapped = Store()
        subject = {k: p[k] for k in ("accountId", "symbol", "name", "worldId")}
        store.claim.return_value.update(subject, capability="market-observe")
        service.subjects = lambda: [subject]
        service.evidence = Mock(return_value=ObservationEvidenceSession(p, p["facts"]))
        service.settings.update(aiObservationLanePolicy=execution_lanes.VERSION, aiEvidenceReadPolicy=evidence_readiness.VERSION,
            aiJudgmentQualityPolicy=judgment_policy.VERSION, aiCompanyResearchEnabled="true", aiInvestmentMeaningPolicy=meaning.VERSION)
        captured = []
        def decide(env, _):
            captured.append(env)
            value = decision("defer")
            value.update(missingEvidence=["다음 원문 비교 자료가 더 필요합니다."], purpose="source-dated", blocker="required-source")
            return value
        service.read_planner = Mock(side_effect=decide)
        service.run_once()
        self.assertTrue(captured)
        self.assertEqual(1, wrapped.feedback_reads)
        self.assertEqual(feedback['issues'], captured[0]['retrievalContext']['continuity']['researchResults'][0]['issues'])
        from digital_twin.modules.ai_orchestration.domain.focused_retrieval import VERSION as retrieval_version
        self.assertEqual(retrieval_version, captured[0]["promptVersion"])
        self.assertNotIn("priceInvestigationPolicy", captured[0]["current"])
        self.assertEqual(meaning.VERSION, captured[0]["current"]["investmentMeaningPolicy"])
        validate_execution_input(captured[0])
        # Persisted data-wait identity is unchanged by process feedback. It is
        # checked before another feedback read or paid routing call.
        store.last_processing_outcome.return_value = store.complete.call_args.args[1]['processingOutcome']
        reads = service.read_planner.call_count
        self.assertEqual('unchanged-wait', service.run_once()['status'])
        self.assertEqual(reads, service.read_planner.call_count)
        self.assertEqual(1, wrapped.feedback_reads)
        from digital_twin.modules.ai_orchestration.domain import retrieval_budget
        previous = deepcopy(store.last_processing_outcome.return_value)
        previous['retrievalAdmission'] = {'status': 'context-budget', 'worldId': p['worldId'], 'policy': None}
        store.last_processing_outcome.return_value = previous
        self.assertEqual('awaiting-evidence', service.run_once()['status'])
        self.assertEqual(reads + 1, service.read_planner.call_count)
        previous = deepcopy(store.complete.call_args.args[1]['processingOutcome'])
        previous['retrievalAdmission'] = {'status': 'context-budget', 'worldId': p['worldId'], 'policy': retrieval_budget.VERSION}
        store.last_processing_outcome.return_value = previous
        self.assertEqual('unchanged-wait', service.run_once()['status'])
        self.assertEqual(reads + 1, service.read_planner.call_count)

    def test_company_source_can_be_authored_and_independently_reviewed_without_a_price_anchor(self):
        from ai_insight_fixtures import review
        from digital_twin.modules.ai_orchestration.domain.insight_quality import accept_review
        from digital_twin.modules.ai_orchestration.domain.publication import publication_block
        r, author = authored_company(validate_execution_input)
        self.assertEqual(prompts.PROMPT_VERSION, author["promptVersion"])
        self.assertNotIn("priceExplanation", author["outputSchema"]["properties"])
        verdict = review()
        verdict.update(judgmentChange={"supported": True, "reason": "영업현금 원문과 사업 판단의 연결을 검토했습니다."},
            analystAssessment={"supported": True, "substantive": True, "memoryIds": [], "reason": "기업 현금창출에 더하는 의미를 검토했습니다."},
            investmentMeaning={"supported": True, "discriminating": True, "expectationsSupported": True,
                "followUpLinked": True, "reason": "원문과 경쟁 설명 및 후속 자료 판별 조건을 확인했습니다."})
        validate_execution_input(freeze_review_input(r))
        r["quality"] = accept_review(r, verdict, "independent-review")
        self.assertEqual("accepted", r["quality"]["status"], r["quality"])
        self.assertEqual("", publication_block(r))


if __name__ == "__main__": unittest.main()
