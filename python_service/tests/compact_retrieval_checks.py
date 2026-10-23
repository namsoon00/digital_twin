"""Routing compression must not rewrite evidence, lose a read, or widen scope."""
from copy import deepcopy
import hashlib
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain import compact_retrieval, retrieval_view, evidence_purpose, purpose_retrieval
from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input, freeze_execution_input
from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError, response_audit
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, EvidenceContractError
from test_bounded_evidence_readiness import packet
from test_scoped_knowledge_research import scoped_decision


def decision(action, requests=()):
    return {**scoped_decision(action, ['quote-1', 'report-2025']),
            'requests': list(requests), 'purpose': 'source-dated', 'blocker': 'none'}


def fixture(value=None):
    value = evidence_purpose.annotate(deepcopy(value or packet()))
    value['lastDeliveredNotification'] = {'summary': '이전 설명', 'counterEvidence': '반대 근거를 보존',
        'facts': [{'id': 'historical', 'body': 'historical-source ' * 500, 'sourceAsOf': '2025-01-01'}]}
    return value


def check_compact_routing(test):
    value = fixture(); saved = []
    request = {'tool': 'read_context', 'path': '/current/lastDeliveredNotification/facts'}
    def save(env):
        validate_execution_input(env); saved.append(deepcopy(env)); return 'compact-' + str(len(saved))
    def decide(env, _):
        test.assertEqual(compact_retrieval.VERSION, env['promptVersion'])
        if len(saved) == 1:
            test.assertNotIn('historical-source', env['prompt'])
            test.assertIn('반대 근거를 보존', env['prompt'])
            test.assertEqual(value['lastDeliveredNotification'], env['current']['lastDeliveredNotification'])
            test.assertIn(request['path'], env['retrievalContext']['readableContextPaths'])
            old = purpose_retrieval.freeze(env['current'], env['retrievalContext'], 262144)
            validate_execution_input(old)
            test.assertLess(len(env['prompt'].encode()), len(old['prompt'].encode()))
            return decision('read', [request])
        test.assertIn('historical-source', env['prompt'])
        result = env['retrievalContext']['trace'][0]['reads'][0]['result']
        test.assertEqual(value['lastDeliveredNotification']['facts'], result['content'])
        test.assertEqual('captured-context-only', result['authority'])
        return decision('finish')
    final, history, research, trace = retrieve_working_evidence(
        ObservationEvidenceSession(value, value['facts']), value, [], [], decide, save, 262144)
    test.assertEqual('ready', final['retrieval']['status'])
    test.assertEqual(value['lastDeliveredNotification'], final['lastDeliveredNotification'])
    test.assertEqual(value['facts'], saved[0]['current']['facts'])
    test.assertNotIn('historical', {row['id'] for row in final['facts']})
    validate_execution_input(freeze_execution_input(final, history, research, retrieval_trace=trace))
    projected, _ = retrieval_view.project(saved[0]['current'], saved[0]['retrievalContext'])
    test.assertEqual(saved[0]['current']['facts'], projected['facts'])
    for path in ('/current/accountId', '/other-account/facts', request['path'] + '/0'):
        with test.assertRaises(ReadRequestError):
            retrieval_view.read(value, saved[0]['retrievalContext'], {'tool': 'read_context', 'path': path})
    for key in ('content', 'contentHash'):
        broken = deepcopy(saved[1]); result = broken['retrievalContext']['trace'][0]['reads'][0]['result']
        result[key] = [] if key == 'content' else 'tampered'
        with test.assertRaises(EvidenceContractError): validate_execution_input(broken)
    broken = deepcopy(saved[0]); broken['retrievalContext']['readableContextPaths'].append('/current/accountId')
    broken['prompt'] = compact_retrieval.prompt(broken['current'], broken['retrievalContext'])
    broken['promptHash'] = hashlib.sha256(broken['prompt'].encode()).hexdigest()
    broken['outputSchema'] = compact_retrieval.schema(broken['retrievalContext'])
    with test.assertRaises(EvidenceContractError): validate_execution_input(broken)
    # Two identical reads stop before paying for a third planning pass.
    repeat = Mock(return_value=decision('read', [request])); saved.clear()
    stopped, _, _, repeated = retrieve_working_evidence(ObservationEvidenceSession(value, value['facts']),
        value, [], [], repeat, save, 262144)
    test.assertEqual(2, repeat.call_count)
    test.assertEqual('repeated-read', stopped['retrieval']['status'])
    test.assertEqual('repeated-read', repeated[-1]['reads'][0]['result']['status'])
    compact_retrieval.validate_trace(repeated)
    forged = deepcopy(repeated)
    forged[-1]['reads'][0]['result']['facts'] = [{'id': 'invented-fact'}]
    with test.assertRaises(EvidenceContractError):
        retrieval_view.paths(value, {**saved[0]['retrievalContext'], 'trace': forged})
    check_sections_and_question_contract(test)


def check_sections_and_question_contract(test):
    value = fixture()
    value['lastDeliveredNotification']['facts'] = [{'id': 'past-' + str(i), 'body': 'x' * 16000} for i in range(2)]
    request = {'tool': 'read_context', 'path': '/current/lastDeliveredNotification/facts'}
    # No new data source is read; descendants are issued only after the index.
    result = retrieval_view.read(value, {}, request)
    test.assertEqual('section-index', result['status'])
    raw = decision('read', [request])
    step = {'status': 'valid', 'correction': False, 'decision': raw, 'response': response_audit(raw),
            'reads': [{'request': request, 'result': result}]}
    context = {'trace': [step]}
    child = {'tool': 'read_context', 'path': result['sections'][0]['path']}
    read = retrieval_view.read(value, context, child)
    test.assertEqual(value['lastDeliveredNotification']['facts'][0], read['content'])
    read['content']['body'] = 'not the original'
    test.assertEqual('x' * 16000, retrieval_view.read(value, context, child)['content']['body'])
    injected = {'payload': {'detailNotLoaded': True, 'readContextPath': '/current/accountId'}}
    test.assertEqual(set(), retrieval_view.initial_paths(injected, {}))
    empty_tools = compact_retrieval.schema({'readableContextPaths': [request['path']]})
    branches = empty_tools['properties']['requests']['items']['anyOf']
    test.assertEqual(['read_context'], [branch['properties']['tool']['enum'][0] for branch in branches])
    required = {'kind': 'brain-case', 'questionContract': {'requirement': 'original-period ' * 200},
                'reviewedAssessment': {'missingRequirements': ['missing source ' * 200]},
                'counterEvidence': 'counter premise', 'observations': [{'status': 'unevaluable'}]}
    _, projected = retrieval_view.project({}, {'continuity': {'researchResults': [required]}})
    test.assertEqual(required, projected['continuity']['researchResults'][0])


def check_compact_storage(test, value, save):
    from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
    value = fixture(value)
    responses = [decision('read', [{'tool': 'read_context', 'path': '/current/lastDeliveredNotification/facts'}]), decision('finish')]
    final, _, _, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value['facts']), value, [], [],
        Mock(side_effect=responses), save, 262144,
        record_round=lambda step: test.store.save_retrieval_round(test.job, step))
    with test.store.connect() as connection:
        test.assertEqual(trace, AIControlResearchKnowledge.read_proof(connection, test.job,
            {'input': final, 'retrievalInputIds': [step['inputId'] for step in trace]}))
