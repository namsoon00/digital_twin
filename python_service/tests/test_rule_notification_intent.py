"""Receipt-backed customer purpose, evidence roles and registered rule outcomes."""

from copy import deepcopy
from dataclasses import replace
import unittest

from test_investment_insight_dispatch_service import frozen_notification_claim
from test_typedb_independent_observation import readable_fixture
from test_rulebox_decision_brief import fixture as decision_fixture
from digital_twin.modules.model_registry.contracts import rule_notification_intent
from digital_twin.modules.model_registry.domain.rule_claim_contract import RuleClaimContract, rule_claim_contract_violations
from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
from digital_twin.modules.notifications.domain.relation_follow_up import delivery_snapshot
from digital_twin.modules.notifications.domain.context_observation_notifications import context_observation_delivery_decision
from digital_twin.modules.notifications.domain.rule_policy_event import policy_events
from digital_twin.modules.notifications.application.typedb_observation_message import typedb_observation_telegram_message
from digital_twin.modules.read_models.contracts import customer_investment_document_from_dict, customer_investment_document_quality


def counter_context():
    ctx = decision_fixture()
    hypothesis = ctx['ontologyRelationContext']['hypothesisSet']['hypotheses'][0]
    hypothesis['expectedOutcome'] = '반등이 유지되지 못하고 이전 위험 경로로 복귀'
    transition = ctx['ontologyRelationContext']['hypothesisLifecycle']['transitions'][0]
    transition.update(currentState='weakened', previousState='maintained', transitionId='counter-added',
        evidenceDelta={'addedCounterEvidenceKeys': ['relative-strength:counter']},
        evidenceChanges=[{'role': 'counter', 'change': 'added', 'evidence': {'ruleId': 'rule:relative-strength'}}],
        changeBasis={'category': 'market-change', 'ruleComparisons': [{
            'ruleId': 'rule:relative-strength', 'label': '약한 시장 대비 상대 강세',
            'comparison': 'verified', 'previous': {}, 'current': {'score': 1, 'contractMatched': True}}]})
    return ctx


def packet_for(ctx, previous=None):
    packet = relation_change_evidence(ctx, previous)
    ctx['relationChangeEvidence'] = packet
    return packet


def registered_context():
    ctx = readable_fixture()
    case = ctx['investmentSubjectDecisionCase']
    claim = ctx['ontologyRelationContext']['hypothesisSet']['hypotheses'][0]['claimContract']
    contract = deepcopy(claim['outcomeContract'])
    contract['contractFingerprint'] = 'frozen-outcome-contract'
    ctx['ruleObservationTracking'] = [{
        'version': 'rule-outcome-registration-v1', 'episodeId': 'episode:original',
        'accountId': 'main', 'symbol': 'MSTR', 'claimContractId': claim['claimContractId'], 'hypothesisId': 'h1',
        'inferenceGenerationId': case['inferenceGenerationId'], 'sourceAboxSnapshotId': case['sourceAboxSnapshotId'],
        'projectionGenerationId': case['inferenceGenerationId'], 'projectionAboxSnapshotId': case['sourceAboxSnapshotId'],
        'observedFromAt': '2026-10-02T01:10:00Z', 'outcomeContract': contract, 'status': 'scheduled',
    }]
    return ctx


def with_outcome(ctx):
    ctx = deepcopy(ctx)
    ctx['ontologyRelationContext']['hypothesisLifecycle']['transitions'] = []
    ctx['ruleObservationTracking'][0]['results'] = [{
        'outcomeId': 'outcome:one-hour', 'episodeId': 'episode:original', 'observedAt': '2026-10-02T02:10:00Z',
        'priceChangeFromDecisionPct': -0.8, 'selectedHypothesisStatus': 'directionally-corroborated',
        'payload': {'mode': 'contract-criteria', 'contractFingerprint': 'frozen-outcome-contract',
            'calibrationEligibility': 'eligible', 'horizonMinutes': 60,
            'criterionAssessments': [{'role': 'result', 'metric': 'instrumentReturnPct',
                'operator': '<=', 'threshold': -0.5, 'observedValue': -0.8, 'state': 'passed'}]},
    }]
    return ctx


class RuleNotificationIntentTests(unittest.TestCase):
    def test_frozen_release_adapter_and_explicit_contract_roundtrip(self):
        claim = frozen_notification_claim()
        self.assertNotIn('notificationIntent', RuleClaimContract.from_dict(claim).to_dict())
        self.assertEqual(claim, RuleClaimContract.from_dict(claim).to_dict())
        original = deepcopy(claim)
        intent = rule_notification_intent(claim)
        self.assertEqual('frozen-rule-claim-adapter-v1', intent['source'])
        self.assertEqual(original, claim, 'reading an old release cannot rewrite it')
        claim['notificationIntent'] = {k: intent[k] for k in (
            'version', 'purpose', 'customerQuestion', 'notifyOn', 'comparisonBasis', 'nextCheckSource')}
        self.assertEqual('authored-rulebox', rule_notification_intent(claim)['source'])
        self.assertEqual(claim, RuleClaimContract.from_dict(claim).to_dict())
        for invalid in ({}, {**claim['notificationIntent'], 'purpose': 'management-policy'},
                        {**claim['notificationIntent'], 'customerQuestion': {'unstructured': True}},
                        {**claim['notificationIntent'], 'notifyOn': [{}]}):
            bad = {**claim, 'notificationIntent': invalid}
            self.assertEqual({}, rule_notification_intent(bad))
            self.assertTrue(any('notificationIntent' in e for e in rule_claim_contract_violations(RuleClaimContract.from_dict(bad))))
        with self.assertRaisesRegex(ValueError, 'notificationIntent'):
            RuleClaimContract.from_dict({**claim, 'notificationIntent': 'unstructured'})

    def test_counter_evidence_reopens_its_own_hypothesis_without_inventing_a_trade(self):
        ctx = counter_context()
        packet = packet_for(ctx, ctx['relationChangeEvidence']['previous'])
        self.assertTrue(packet['eligible'])
        self.assertEqual('counter-added', packet['customerEvents'][0]['changeKind'])
        self.assertEqual('unchanged', packet['customerEvents'][0]['decisionImpact']['status'])
        text = typedb_observation_telegram_message(ctx)
        lead = ctx['customerInvestmentDocument']['lead']
        self.assertIn('반등 실패 가능성', lead)
        self.assertIn('반대되는 근거가 추가', lead)
        self.assertNotIn('상대 강세', lead)
        self.assertIn('관리 제한·대응 후보는 같습니다', text)
        self.assertIn('관리 원칙: 추가매수 제한', text)
        self.assertFalse(packet['current']['decisionBrief']['finalActionSelected'])
        self.assertEqual('passed', ctx['customerInvestmentDocumentQuality']['status'])

    def test_successful_receipt_suppresses_same_meaning_but_preserves_a_new_role_change(self):
        ctx = counter_context()
        first = packet_for(ctx)
        prior = delivery_snapshot(first, 'delivered-original', 'main')
        transition = ctx['ontologyRelationContext']['hypothesisLifecycle']['transitions'][0]
        transition.update(transitionId='poll-again', occurredAt='2026-10-02T02:10:00Z')
        transition['changeBasis']['ruleComparisons'][0]['current']['modelInputWindows'] = {'1D': {'latestObservedAt': '2026-10-02T02:10:00Z'}}
        repeated = packet_for(ctx, prior)
        self.assertFalse(repeated['eligible'])
        self.assertTrue(any(s.get('reasonCode') == 'same-customer-meaning' for s in repeated['suppressedTransitions']))
        self.assertTrue(packet_for(ctx, {})['eligible'], 'an unsent draft is not a delivered baseline')
        transition.update(transitionId='counter-removed', currentState='strengthened',
                          evidenceDelta={'removedCounterEvidenceKeys': ['relative-strength:counter']},
                          evidenceChanges=[{'role': 'counter', 'change': 'removed', 'evidence': {'ruleId': 'rule:relative-strength'}}])
        changed = packet_for(ctx, prior)
        self.assertTrue(changed['eligible'])
        self.assertEqual('counter-removed', changed['customerEvents'][0]['changeKind'])
        self.assertEqual('delivered-original', changed['followUp']['parentJobId'])
        restored = readable_fixture()
        original = delivery_snapshot(packet_for(restored), 'still-open', 'main')
        transition = restored['ontologyRelationContext']['hypothesisLifecycle']['transitions'][0]
        transition.update(transitionId='after-holiday', previousState='expired')
        self.assertFalse(packet_for(restored, original)['eligible'], 'a quiet data interruption does not reopen an unchanged customer case')
        transition.update(transitionId='actual-ending', previousState='observed', currentState='invalidated')
        ended = delivery_snapshot(packet_for(restored, original), 'ending-notice', 'main')
        transition.update(transitionId='actual-reopening', previousState='invalidated', currentState='observed')
        self.assertTrue(packet_for(restored, ended)['eligible'])

    def test_missing_purpose_unrelated_claim_and_scope_mismatch_cannot_authorize_push(self):
        ctx = readable_fixture()
        for mutation in ('missing', 'unrelated', 'internal-only', 'authority', 'account', 'generation', 'world'):
            value = deepcopy(ctx)
            claim = value['ontologyRelationContext']['hypothesisSet']['hypotheses'][0]['claimContract']
            if mutation == 'missing':
                claim.clear()
            elif mutation == 'unrelated':
                claim['ruleId'] = 'another-rule'
            elif mutation == 'internal-only':
                intent = rule_notification_intent(claim)
                claim['notificationIntent'] = {**intent, 'purpose': 'internal-only', 'notifyOn': []}
            elif mutation == 'account':
                value['accountId'] = 'another-account'
            elif mutation == 'authority':
                claim['decisionAuthority'] = 'guardrail-only'
            elif mutation == 'generation':
                value['ontologyRelationContext']['graphStoreInference']['inferenceGenerationId'] = 'wrong'
            else:
                value['ontologyRelationContext']['graphStoreInference']['worldId'] = 'wrong'
            with self.subTest(mutation=mutation):
                self.assertFalse(packet_for(value)['eligible'])

    def test_policy_breach_is_independent_of_predictive_qualification_and_polling_values(self):
        ctx = decision_fixture()
        relation = ctx['ontologyRelationContext']
        relation['hypothesisLifecycle']['transitions'] = []
        relation['matchedRules'][0]['claimContract'] = frozen_notification_claim('rule:policy', 'risk-invariant')
        packet = packet_for(ctx)
        self.assertEqual(['management-policy'], [e['purpose'] for e in packet['customerEvents']])
        self.assertTrue(packet['current']['decisionBrief']['qualificationPending'])
        self.assertEqual('send', context_observation_delivery_decision(ctx)['decision'])
        self.assertNotIn('자료 상태 변화', context_observation_delivery_decision(ctx)['reason'])
        prior = delivery_snapshot(packet, 'policy-original', 'main')
        relation['facts']['profitLossRate'] = -19
        relation['graphStoreInference']['traces'][-1]['matchedConditions'][0]['observedValue'] = -19
        self.assertFalse(packet_for(ctx, prior)['eligible'])
        relation['facts']['strategyLossTolerancePct'] = -16
        updated = packet_for(ctx, prior)
        self.assertEqual('updated', updated['customerEvents'][0]['policyState'])
        text = typedb_observation_telegram_message(ctx)
        self.assertIn('관리 기준 점검', text)
        self.assertEqual('passed', ctx['customerInvestmentDocumentQuality']['status'])

    def test_policy_release_requires_fresh_explicit_negative_proof(self):
        ctx = decision_fixture()
        ctx['ontologyRelationContext']['matchedRules'][0]['claimContract'] = frozen_notification_claim('rule:policy', 'risk-invariant')
        prior = packet_for(ctx)['current']
        current = deepcopy(prior)
        current['decisionBrief']['restrictions'] = []
        rule = next(r for r in current['rules'] if r['id'] == 'rule:policy')
        rule['matched'] = False
        rule['conditions'][0].update(matched=False, judgementEvidenceUsable=True, freshnessStatus='fresh')
        events = policy_events(current, prior)
        self.assertEqual('released', events[0]['policyState'])
        for field, invalid in (('nativeProof', False), ('matched', None)):
            bad = deepcopy(current)
            next(r for r in bad['rules'] if r['id'] == 'rule:policy')[field] = invalid
            self.assertEqual([], policy_events(bad, prior))
        rule['conditions'][0]['freshnessStatus'] = 'stale'
        self.assertEqual([], policy_events(current, prior))

    def test_registered_outcome_replies_to_the_delivered_original_once(self):
        ctx = registered_context()
        first = packet_for(ctx)
        self.assertEqual('registered', first['customerEvents'][0]['nextCheck']['trackingStatus'])
        self.assertIn('자동 결과 확인 등록', typedb_observation_telegram_message(ctx))
        prior = delivery_snapshot(first, 'tracking-original', 'main')
        after = with_outcome(ctx)
        packet = packet_for(after, prior)
        self.assertEqual('outcome-confirmed', packet['customerEvents'][0]['changeKind'])
        self.assertEqual('tracking-original', packet['followUp']['parentJobId'])
        self.assertEqual('send', context_observation_delivery_decision(after)['decision'])
        text = typedb_observation_telegram_message(after)
        for expected in ('가설 확인 결과', '60분 결과', '0.5%', '-0.8%', 'N-TRACKING'):
            self.assertIn(expected, text)
        self.assertNotIn('현재 전체 데이터', text)
        self.assertEqual('passed', after['customerInvestmentDocumentQuality']['status'])
        receipt = delivery_snapshot(packet, 'tracking-result', 'main')
        self.assertFalse(packet_for(after, receipt)['eligible'])
        self.assertFalse(packet_for(after, {})['eligible'], 'outcomes have no standalone notification origin')

    def test_outcome_data_gaps_wrong_contract_and_unrelated_registration_never_become_results(self):
        ctx = registered_context()
        prior = delivery_snapshot(packet_for(ctx), 'tracking-original', 'main')
        for field, value in (('calibrationEligibility', 'excluded-contract-data-gap'),
                             ('horizonMinutes', 999), ('criterionAssessments', []),
                             ('contractFingerprint', 'different-contract'), ('mode', 'legacy-directional-fallback')):
            after = with_outcome(ctx)
            after['ruleObservationTracking'][0]['results'][0]['payload'][field] = value
            self.assertFalse(packet_for(after, prior)['eligible'])
        after = with_outcome(ctx)
        after['ruleObservationTracking'][0]['projectionAboxSnapshotId'] = 'wrong'
        self.assertFalse(packet_for(after, prior)['eligible'])
        ctx['ruleObservationTracking'][0]['inferenceGenerationId'] = 'other-generation'
        self.assertEqual('registration-unconfirmed', packet_for(ctx)['customerEvents'][0]['nextCheck']['trackingStatus'])

    def test_document_quality_requires_purpose_consequence_and_nonempty_rule_name(self):
        ctx = counter_context()
        ctx['ontologyRelationContext']['facts']['volumeRatio'] = .00041
        comparison = ctx['ontologyRelationContext']['hypothesisLifecycle']['transitions'][0]['changeBasis']['ruleComparisons'][0]
        comparison.update(previous={'score': .635414}, current={'status': 'not-supported'})
        packet_for(ctx)
        typedb_observation_telegram_message(ctx)
        doc = customer_investment_document_from_dict(ctx['customerInvestmentDocument'])
        self.assertIn('0.001배 미만', str(doc))
        self.assertIn('약 0.6354', str(doc))
        self.assertEqual('passed', ctx['customerInvestmentDocumentQuality']['status'])
        broken = replace(doc, lead='‘’ 규칙의 근거 변화', sections=tuple(s for s in doc.sections if s.key != 'decision-impact'))
        issues = customer_investment_document_quality(broken, required_sections=('notification-purpose', 'decision-impact'))['issues']
        self.assertIn('empty-rule-or-hypothesis-label', issues)
        self.assertIn('missing-observation-decision-impact', issues)


if __name__ == '__main__':
    unittest.main()
