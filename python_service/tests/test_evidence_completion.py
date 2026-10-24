"""Observed budget and receipt-provenance failures, with no paid model calls."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import Mock
from ai_insight_fixtures import ref
from test_bounded_evidence_readiness import packet
from test_company_value_observation import valuation_packet
from test_scoped_knowledge_research import scoped_decision
from digital_twin.modules.ai_orchestration.domain import evidence_completion, judgment_policy, evidence_purpose, company_value
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input
from digital_twin.modules.ai_orchestration.domain.insight_schema import evidence_references
from digital_twin.modules.ai_orchestration.domain.insight_memory import receipt_facts, restore_receipt_provenance
from digital_twin.modules.ai_orchestration.domain.research_evidence_use import usable_fact
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, EvidenceContractError, source_packet_bytes, EXACT_QUESTION_DOCUMENT_VERSION
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence


def active(value=None):
    result = evidence_purpose.annotate(deepcopy(value or packet()))
    result.update(judgmentQualityPolicy=judgment_policy.VERSION, evidenceCompletionPolicy=evidence_completion.VERSION)
    return company_value.refresh(result)


def document(value):
    body = '공식 보고 기간의 영업현금흐름에 관한 원문입니다.'
    return {'id': 'source-document', 'kind': 'research-evidence', 'symbol': value['symbol'],
        'sourceWorldId': value['worldId'], 'sourceSnapshotId': value['sourceSnapshotId'],
        'url': 'https://example.com/ir', 'publishedAt': '2025-01-01', 'documentVerified': True,
        'evidenceView': {'version': EXACT_QUESTION_DOCUMENT_VERSION},
        'historicalDocumentReference': {'version': 'research-document-reference-v1', 'purpose': 'dated-issuer-document',
            'symbol': value['symbol'], 'sourceUrl': 'https://example.com/ir', 'text': body,
            'passageTextHash': hashlib.sha256(body.encode()).hexdigest()}}


class EvidenceCompletionTests(unittest.TestCase):
    def run_retrieval(self, value, facts=None, decide=None):
        saved = []
        def save(env):
            validate_execution_input(env); saved.append(deepcopy(env)); return 'input-' + str(len(saved))
        final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(value, facts or value['facts']),
            value, [], [], decide or Mock(return_value={**scoped_decision('finish', ['quote-1', 'report-2025']),
                'purpose': 'source-dated', 'blocker': 'none'}), save, 262144)
        validate_execution_input(freeze_execution_input(final, history, research, retrieval_trace=trace))
        return final, saved

    def test_new_phases_replay_and_old_v24_remains_unchanged(self):
        old = active(); old.pop('evidenceCompletionPolicy')
        frozen = freeze_execution_input(old, [], []); original = deepcopy(frozen)
        value, saved = self.run_retrieval(active())
        self.assertIn('retrieval-v13-', saved[0]['promptVersion'])
        # Use the simple source packet for isolated author/repair/review replay.
        final = freeze_execution_input(active(), [], [])
        for env in (final, freeze_repair_input(final, {}, ['근거 확인'], 'parent'),
                    freeze_review_input({'input': active(), 'summary': '자료 시점의 설명'})):
            validate_execution_input(env)
            broken = deepcopy(env); broken['current'].pop('evidenceCompletionPolicy')
            with self.assertRaises(EvidenceContractError): validate_execution_input(broken)
        validate_execution_input(frozen); self.assertEqual(original, frozen)

    def test_whole_baseline_fits_with_large_required_delivery_memory(self):
        base = packet(); facts = deepcopy(base['facts'])
        facts[1]['exactSourceDescription'] = 'x' * 24500
        base['facts'], base['coverage'] = select_evidence(facts)
        value = active(base)
        value['lastDeliveredNotification'] = {'jobId': 'receipt', 'facts': [{'id': 'prior', 'body': 'x' * 59000}]}
        before = deepcopy(value['lastDeliveredNotification'])
        decide = Mock(side_effect=lambda env, _: {**scoped_decision('defer', ['quote-1'], missing=['관련 보고서 지표를 선택해 확인해야 합니다.']), 'purpose': 'source-dated', 'blocker': 'required-source'})
        result, saved = self.run_retrieval(value, facts, decide)
        self.assertLessEqual(source_packet_bytes(result), 96000)
        self.assertIn('report-2025', result['businessEvidence']['omittedByBudget'])
        self.assertEqual(before, result['lastDeliveredNotification'])
        self.assertEqual(1, decide.call_count)
        self.assertTrue(saved[0]['retrievalContext']['reportIndex'])
        self.check_read_that_adds_required_quality(value, facts)

    def check_read_that_adds_required_quality(self, value, facts):
        # The quality row is absent initially, but mandatory once read.
        quality = {'id': 'quality-large', 'kind': 'data-quality', 'detail': 'q' * 30000,
            'sourceWorldId': value['worldId'], 'sourceSnapshotId': value['sourceSnapshotId']}
        facts = [*facts, quality]
        base = packet(); base['facts'], base['coverage'] = select_evidence(facts)
        value = active(base); value['lastDeliveredNotification'] = {'facts': [{'id': 'past', 'body': 'x' * 52000}]}
        read = {**scoped_decision('read', ['quote-1']), 'purpose': 'source-dated', 'blocker': 'none',
            'requests': [{'tool': 'query_facts', 'category': 'quality', 'kind': 'data-quality', 'cursor': ''}]}
        planner = Mock(return_value=read)
        final, saved = self.run_retrieval(value, facts, planner)
        self.assertEqual(1, planner.call_count)
        self.assertEqual('context-budget', final['retrieval']['status'])
        self.assertNotIn(quality['id'], {f['id'] for f in final['facts']})
        self.assertLessEqual(source_packet_bytes(final), 96000)

    def test_required_memory_overflow_stops_before_any_model_call(self):
        base = packet(); session = ObservationEvidenceSession(base, base['facts']); value = active(base)
        value['lastDeliveredNotification'] = {'facts': [{'id': 'prior', 'body': 'x' * 100000}]}
        decide = Mock(); save = Mock()
        with self.assertRaises(EvidenceContractError):
            retrieve_working_evidence(session, value, [], [], decide, save, 262144)
        decide.assert_not_called(); save.assert_not_called()

    def test_receipt_retains_validation_and_restores_only_exact_original(self):
        value = active(); fact = document(value)
        value.update(taskId='task'); value['facts'].append(fact)
        original = {'input': value, 'evidenceIds': [fact['id']], 'inputFingerprint': 'fingerprint',
            'insightVersion': 'observation-insight-v1', 'publication': {'jobId': 'job'},
            'claimEvidence': {'comparison': [ref('historicalDocumentReference.text', fact_id=fact['id'])]}}
        saved = receipt_facts(original)[0]
        self.assertTrue(usable_fact(saved, 'historicalDocumentReference.text'))
        receipt = {**{k: value[k] for k in ('accountId', 'symbol', 'taskId')}, 'jobId': 'job',
            'inputFingerprint': 'fingerprint', 'insightVersion': original['insightVersion'],
            'deliveredAt': value['capturedAt'], 'facts': [{k: v for k, v in saved.items() if k not in ('evidenceView', 'url')}]}
        receipt['facts'][0]['historicalDocumentReference'] = {'text': fact['historicalDocumentReference']['text']}
        self.assertFalse(usable_fact(receipt['facts'][0]))
        restored = restore_receipt_provenance(receipt, original)
        self.assertTrue(usable_fact(restored['facts'][0]))
        self.assertFalse(usable_fact(receipt['facts'][0]))
        for mutate in (lambda r: r.update(jobId='other'), lambda r: r.update(accountId='other'),
                       lambda r: r.update(deliveredAt='1900-01-01T00:00:00Z'),
                       lambda r: r['facts'][0]['historicalDocumentReference'].update(text='changed')):
            bad = deepcopy(receipt); mutate(bad)
            self.assertEqual(bad, restore_receipt_provenance(bad, original))

    def test_schema_cannot_offer_invalid_baseline_or_launder_empty_usable_set(self):
        value = active(); fact = document(value)
        fact['judgementEvidenceUsable'] = False  # Valid dated-document use survives the current-event gate.
        value['facts'] = [fact]
        value['lastDeliveredNotification'] = {'facts': [{'id': 'bad-baseline', 'kind': 'research-evidence',
            'historicalDocumentReference': {'text': 'lost provenance'}}]}
        schema = evidence_references(value, usable=True)
        self.assertNotIn('bad-baseline', str(schema))
        self.assertIn('historicalDocumentReference.text', str(schema))
        self.assertNotIn('evidenceView.version', str(schema))
        self.assertIn('bad-baseline', str(evidence_references(value)))
        fact['historicalDocumentReference']['passageTextHash'] = 'forged'
        schema = evidence_references(value, usable=True)
        self.assertIn('unavailable-evidence', str(schema)); self.assertNotIn('source-document', str(schema))

    def test_pending_review_is_not_resolved_by_more_sources_or_admitted_as_value(self):
        value = valuation_packet(False)
        assessment = next(f for f in value['facts'] if f['kind'] == 'valuation-assessment')
        assessment['payload'].update(modelApprovalState='ai_applied_pending_review', inputEligibility='sufficient',
            blockedReasons=['lower-priority-earnings-horizon'])
        bundle = next(f for f in value['facts'] if f['kind'] == 'valuation-input-bundle')
        bundle['payload']['exclusions'] = [{'reason': 'lower-priority-earnings-horizon'}]
        value = active(value)
        final, _ = self.run_retrieval(value, decide=Mock(return_value={**scoped_decision('defer', ['quote-1'],
            missing=['모델 검토가 완료되어야 합니다.']), 'purpose': 'source-dated', 'blocker': 'required-source'}))
        state = final['companyValueReadiness']['models'][0]
        self.assertEqual('model-review', state['nextStep'])
        self.assertEqual('calculated', state['calculationStatus'])
        self.assertEqual(['lower-priority-earnings-horizon'], state['excludedCandidateReasons'])
        self.assertFalse(state['valuationAdmitted']); self.assertFalse(state['sourceCollectionResolvesApproval'])
        from digital_twin.modules.notifications.domain.company_value_status import hold_details
        checks = hold_details(final['companyValueContext']['models'][0], final)['checks']
        self.assertEqual(['모델 검토 대기'], [label for label, _ in checks])
        self.assertEqual([], final['companyValueContext']['models'][0]['scenarios'])
        changed = deepcopy(final); changed['companyValueReadiness']['models'][0]['valuationAdmitted'] = True
        with self.assertRaises(EvidenceContractError): company_value.validate(changed)
        # A pending model's bulky audit cannot displace useful business evidence.
        candidate = valuation_packet(False)
        report = deepcopy(packet()['facts'][1])
        facts = [*candidate['facts'], report]
        bundle = next(f for f in facts if f['kind'] == 'valuation-input-bundle')
        bundle['payload']['exclusions'] = [{'reason': 'candidate-not-selected', 'audit': 'x' * 14000}]
        candidate['facts'], candidate['coverage'] = select_evidence(facts)
        candidate = active(candidate)
        candidate['lastDeliveredNotification'] = {'facts': [{'id': 'prior', 'body': 'x' * 62000}]}
        final, _ = self.run_retrieval(candidate, facts)
        self.assertIn(report['id'], {f['id'] for f in final['facts']})
        self.assertFalse(final['companyValueEvidence']['factIds'])
        self.assertTrue(final['companyValueEvidence']['omitted'])
        self.assertEqual('ready', final['retrieval']['status'])
