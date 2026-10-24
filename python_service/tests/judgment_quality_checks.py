"""Purpose, budget and source lineage regressions without live model calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock


def check_causality(test):
    from digital_twin.modules.ai_orchestration.domain.insight_contract import unsupported_cause, unsupported_cause_v3
    benign = [
        '선택 범위에는 과거 거래대금 비교값과 독립적인 원인 근거가 없어 거래 증가·순유입·하락 원인은 미확인입니다',
        '제품 구성 변화의 원인은 미확인입니다', '실적 감소의 원인은 아직 불명확합니다',
        '수익성 개선의 원인이 확인되지 않았습니다',
        '과거 근거가 부족하기 때문에, 판단을 보류합니다',
        '현금 전환과 차이 원인은 여전히 미확인입니다',
        '이번 선택 원문으로 확인하는 범위는 현금흐름의 조정 구성에 한정되며, 반복 수요와 현금창출 개선의 원인은 여전히 미확인입니다',
        '실적 변화의 원인은 현재로서는 정확히 확인되지 않았습니다',
    ]
    for sentence in benign:
        test.assertFalse(unsupported_cause(sentence, 'counterEvidence'), sentence)
    test.assertTrue(unsupported_cause_v3(benign[0], 'counterEvidence'))
    for sentence in ('기관 매도 때문에 가격이 하락했습니다',
                     '기관 매도 때문에 가격이 하락했지만 정확한 원인은 미확인입니다',
                     '원인은 미확인입니다만 기관 매도 때문에 가격이 하락했습니다',
                     '원인은 미확인이나 금리 때문입니다',
                     '원가 부담 때문에 이익이 감소했고 일시적이었을 수 있습니다'):
        test.assertTrue(unsupported_cause(sentence, 'hypothesis'), sentence)


def check_paced_budget(test):
    from digital_twin.modules.ai_orchestration.domain.token_budget import POLICY, reserve, settle, usage_view, execution_pool
    from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait
    now = datetime(2026, 10, 24, 0, tzinfo=timezone.utc)
    settings = {'aiDailyTokenBudget': 20_000_000, 'aiTokenBudgetPolicy': POLICY}
    state = {}
    for _ in range(7):
        state = reserve(settings, state, 'independent-observation', now, 'retrieval')
        state = settle(state, 'independent-observation', {'input_tokens': 90_000, 'output_tokens': 10_000}, pool='retrieval')
    with test.assertRaises(AIControlBudgetWait) as wait:
        reserve(settings, state, 'independent-observation', now, 'retrieval')
    test.assertEqual('2026-10-24T01:00:00Z', wait.exception.reset_at)
    for _ in range(3):
        state = reserve(settings, state, 'news-analysis', now, 'preparation')
        state = settle(state, 'news-analysis', {'input_tokens': 100_000, 'output_tokens': 0}, pool='preparation')
    with test.assertRaises(AIControlBudgetWait):
        reserve(settings, state, 'news-analysis', now, 'preparation')
    # A draft and its review can still finish after preparation reaches its cap.
    for _ in range(2):
        state = reserve(settings, state, 'independent-observation', now, 'completion')
        state = settle(state, 'independent-observation', {'input_tokens': 70_000, 'output_tokens': 10_000}, pool='completion')
    test.assertEqual(160_000, usage_view(settings, state, now)['allocation']['pools']['completion'])
    resumed = reserve(settings, state, 'independent-observation', now + timedelta(hours=1), 'retrieval')
    settled = settle(resumed, 'independent-observation', None, not_started=True, pool='retrieval')
    test.assertEqual(usage_view(settings, state, now)['reportedTokens'], usage_view(settings, settled, now)['reportedTokens'])
    test.assertEqual(0, usage_view(settings, settled, now)['reservedTokens'])
    test.assertEqual('completion', execution_pool('independent-observation', 'research-assessment-review-v3-question-impact'))
    test.assertEqual('preparation', execution_pool('independent-observation', 'independent-research-plan-v1'))
    # Unknown usage consumes a reservation in both ledgers; it is never zero cost.
    unknown = settle(resumed, 'independent-observation', None, pool='retrieval')
    test.assertEqual(100_000, usage_view(settings, unknown, now)['unknownTokensAllowance'])


def check_purpose_quality(test):
    from test_bounded_evidence_readiness import packet
    from ai_insight_fixtures import plan, ref, review
    from test_scoped_knowledge_research import scoped_decision
    from digital_twin.modules.ai_orchestration.domain import evidence_purpose, judgment_policy, quality_retrieval
    from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_review_input, freeze_repair_input, validate_execution_input
    from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
    from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality, accept_review, quality_block
    from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
    from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, EvidenceContractError
    from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence
    from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
    value = evidence_purpose.annotate(packet()); value['judgmentQualityPolicy'] = judgment_policy.VERSION
    from digital_twin.modules.ai_orchestration.domain.execution_lanes import material_key
    original = deepcopy(value); original.pop('judgmentQualityPolicy')
    test.assertNotEqual(material_key(original, []), material_key(value, []))
    test.assertEqual(material_key(value, []), material_key(deepcopy(value), []))
    macro = {'id': 'macro-1', 'kind': 'macro-print', 'sourceAsOf': value['capturedAt'], 'value': 4.5,
             'sourceWorldId': value['worldId'], 'sourceSnapshotId': value['sourceSnapshotId']}
    value['facts'], value['coverage'] = select_evidence(value['facts'] + [macro])
    saved = []
    def save(env):
        validate_execution_input(env); saved.append(deepcopy(env)); return 'input-' + str(len(saved))
    decisions = Mock(return_value={**scoped_decision('finish', ['quote-1','report-2025']), 'purpose': 'source-dated', 'blocker': 'none'})
    final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value['facts']),
        value, [], [], decisions, save, 262144)
    test.assertEqual(quality_retrieval.VERSION, saved[0]['promptVersion'])
    test.assertEqual(1, saved[0]['retrievalContext']['catalog']['coverage']['macro']['available'])
    test.assertNotIn('macro-1', {r['id'] for r in final['facts']})
    test.assertEqual(1, final['coverage']['macro']['excluded'])
    session = ObservationEvidenceSession(value, value['facts'])
    test.assertIn('macro-1', {r['id'] for r in session.select(['macro-1'], macro_baseline=False)['facts']})
    # Source contracts and mandatory memory remain present; optional macro is readable.
    env = freeze_execution_input(final, history, research, retrieval_trace=trace)
    test.assertEqual(judgment_policy.PROMPT_VERSION, env['promptVersion']); validate_execution_input(env)
    raw = plan(); raw.update(observations=[], followUpConditions=[], evidenceIds=['quote-1','report-2025'],
        questions=[{'question': '다음 공식 보고서에서 매출과 현금 회수의 연결을 확인합니다.', 'capability': 'observe'}])
    for section in ('summary', 'hypothesis'):
        raw['claimEvidence'][section] = [ref('reportedValues.revenue', fact_id='report-2025')]
        raw[section] = '공식 매출은 확인했으나 현금 회수와의 연결은 추가 자료가 필요합니다.'
    result = {**validate_plan(raw, final), 'input': final, 'observedAt': final['capturedAt'], 'executionInputId':'author'}
    test.assertNotIn('conditionValidation', result)
    test.assertEqual([], local_quality(result)['errors'])
    result['quality'] = accept_review(result, review(), 'review')
    test.assertEqual('', quality_block(result))
    test.assertIn('다음 자료에서 확인할 내용', render_ai_observation(result))
    for frozen in (freeze_review_input(result), freeze_repair_input(env, raw, ['synthetic correction'], 'author')):
        validate_execution_input(frozen)
    altered = deepcopy(result); altered['documentaryReview']['basis'][0]['hash'] = 'forged'
    test.assertTrue(quality_block(altered))
    for mutate in (lambda p: p.update(analysisPurpose='current-state'),
                   lambda p: p.pop('judgmentQualityPolicy'),
                   lambda p: p['facts'][1].update(judgementEvidenceUsable=False)):
        changed = deepcopy(final); mutate(changed)
        rejected = {**validate_plan(raw, changed), 'input': changed}
        test.assertEqual('rejected', local_quality(rejected)['status'])
    broken = deepcopy(env); broken['current'].pop('judgmentQualityPolicy')
    with test.assertRaises(EvidenceContractError): validate_execution_input(broken)
    links = judgment_policy.judgment_links(result)
    test.assertEqual('not-empirically-qualified', links['qualification'])
    test.assertEqual({'quote-1','report-2025'}, {r['factId'] for r in links['evidence']})


def check_source_requirements(test):
    from test_source_exploration import TASK, TARGET, Explorer, tools_for
    from digital_twin.modules.news_intelligence.contracts import source_requirements
    from digital_twin.modules.news_intelligence.domain.source_focus import source_research_focus
    from digital_twin.modules.news_intelligence.domain.research_progress import task_contract, fingerprint
    from digital_twin.modules.news_intelligence.application.source_exploration_service import SourceExplorationService
    contract = {'target': '사업부별 매출과 현금 회수', 'period': '원래 연간 보고 기간', 'answerCriterion': '공식 원문 표의 기간과 연결 범위를 확인'}
    gaps = ['비교 기간의 현금흐름표 단위', '공식 발행일과 연결 범위']
    requirement = source_requirements(contract, gaps, [{'evidenceId':'previous'}])
    task = {**TASK, 'sourceFocus':source_research_focus(gaps), 'sourceRequirements':requirement}
    from types import SimpleNamespace
    from digital_twin.modules.outcomes.domain.hypothesis_research_planning import research_planner_input
    brief = SimpleNamespace(candidate_hypotheses=[], hypothesis_set_id='fixture', evidence_gaps=[],
                            reasoning_generation=SimpleNamespace(to_dict=lambda: {}))
    planned = research_planner_input(brief, {'tasks':[task, TASK]})['baselinePlan']['tasks']
    test.assertEqual(task['sourceFocus'], planned[0]['sourceFocus'])
    test.assertEqual(requirement, planned[0]['sourceRequirements'])
    test.assertNotIn('sourceFocus', planned[1])
    test.assertNotIn('sourceRequirements', planned[1])
    explorer = Explorer()
    _, audit = SourceExplorationService(explorer, tools_for()).explore(TARGET, [task], [], [])
    captured = explorer.contexts[0]['tasks'][0]
    test.assertEqual(requirement, captured['sourceRequirements'])
    test.assertEqual(contract, captured['sourceRequirements']['originalContract'])
    test.assertEqual(gaps, captured['sourceFocus']['missingRequirements'])
    test.assertEqual('unverified', requirement['items'][0]['status'])
    test.assertEqual({'target','period','answerCriterion'}, {r['field'] for r in requirement['contractChecks']})
    changed = deepcopy(task); changed['sourceRequirements']['originalContract']['period'] = '다른 기간'
    test.assertNotEqual(fingerprint(task_contract(task)), fingerprint(task_contract(changed)))
    with test.assertRaisesRegex(ValueError, 'resume-contract-mismatch'):
        SourceExplorationService(explorer, tools_for()).explore(TARGET, [changed], [], [], resume=audit)


def check_quality_storage(test, value, save):
    from digital_twin.modules.ai_orchestration.domain import judgment_policy, quality_retrieval
    from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
    from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_review_input
    from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession
    from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
    from test_scoped_knowledge_research import scoped_decision
    value = deepcopy(value); value['judgmentQualityPolicy'] = judgment_policy.VERSION
    selected, history, memory, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value['facts']),
        value, [], [], Mock(return_value={**scoped_decision('finish', ['quote-1','report-2025']),
        'purpose':'source-dated','blocker':'none'}), save, 262144,
        record_round=lambda step:test.store.save_retrieval_round(test.job, step))
    test.assertEqual(quality_retrieval.VERSION, selected['retrieval']['version'])
    with test.store.connect() as c:
        test.assertEqual(trace, AIControlResearchKnowledge.read_proof(c, test.job,
            {'input':selected,'retrievalInputIds':[step['inputId'] for step in trace]}))
    env = freeze_execution_input(selected, history, memory, retrieval_trace=trace)
    result = {'input':selected, 'summary':'자료 검토 경로와 원문을 보존합니다.', 'executionInputId':save(env)}
    test.assertEqual(test.job['taskId'], test.store.review_proof(save(freeze_review_input(result)))['taskId'])


def check_authoring_preflight(test):
    from types import SimpleNamespace
    from digital_twin.modules.model_registry.application.ontology_rule_candidate_service import RuleChangeCandidateProposalService
    from digital_twin.modules.model_registry.domain.hypothesis_authoring import authoring_readiness
    from digital_twin.modules.model_registry.domain.hypothesis_compilation import compilation_blockers
    repository = SimpleNamespace(rulebox_snapshot=Mock(return_value={'status':'ok','rules':[]}),
        inferencebox_snapshot=Mock(side_effect=AssertionError('no live inference needed')))
    advisor = SimpleNamespace(propose=Mock())
    capture = Mock()
    result = RuleChangeCandidateProposalService(repository, advisor).propose_hypothesis(
        {'caseId':'quality-preflight','symbol':'TEST'}, context_observer=capture)
    test.assertEqual('blocked-capability', result['status'])
    test.assertFalse(result['modelCalled'])
    advisor.propose.assert_not_called()
    repository.inferencebox_snapshot.assert_not_called()
    test.assertEqual(2, len(compilation_blockers(result['candidates'])))
    test.assertEqual('blocked', capture.call_args.args[0]['authoringReadiness']['status'])
    test.assertEqual('ready', authoring_readiness(test.context()['authoringContract'])['status'])


def check_metrics(test):
    from digital_twin.modules.ai_orchestration.domain.judgment_metrics import calls_summary, judgments_summary
    from digital_twin.infrastructure.ai_judgment_audit import clock
    test.assertEqual('2026-10-24T00:00:00.000000Z', clock('2026-10-24T09:00:00+09:00'))
    calls = [dict(workload='independent-observation', status='completed', metrics={'promptVersion':'v24',
        'tokenPool':'completion', 'promptBytes':100, 'usage':{'input_tokens':80,'cached_input_tokens':60,'output_tokens':20}}),
        dict(workload='independent-observation', status='failed', metrics={'promptVersion':'v24', 'tokenPool':'completion'})]
    counted = calls_summary(calls)[0]
    test.assertEqual(100, counted['reportedTokens'])
    test.assertEqual(1, counted['unmeasuredUsageCalls'])
    tasks = [dict(task_id='a',status='completed',result={'summary':'authored','executionPromptVersion':'v24',
        'quality':{'status':'accepted'},'publication':{'status':'queued','jobId':'receipt'}}),
        dict(task_id='b',status='completed',result={'status':'unchanged'})]
    result = judgments_summary(tasks, {'a':calls}, {})[0]
    test.assertEqual(1, result['authored'])
    test.assertEqual(0, result['receiptVerifiedDeliveries'])
    test.assertEqual(0, result['tokensPerFullyMeasuredAuthoredTask']['samples'])
    result = judgments_summary(tasks, {'a':calls[:1]}, {'receipt':True})[0]
    test.assertEqual(100, result['tokensPerFullyMeasuredAuthoredTask']['mean'])
    test.assertEqual(1, result['receiptVerifiedDeliveries'])
    tasks[0]['result']['questionResolutions'] = [{'judgmentImpact':{'effect':'supports'}}]
    test.assertEqual({}, judgments_summary(tasks, {}, {})[0]['reviewedQuestionImpacts'])
    tasks[0]['result']['researchQuality'] = {'status':'accepted'}
    test.assertEqual({'supports':1}, judgments_summary(tasks, {}, {})[0]['reviewedQuestionImpacts'])
