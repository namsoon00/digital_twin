"""Verified observations are independent of execution eligibility."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock

from digital_twin.modules.notifications.domain.rule_notice_delivery_value import delivery_value
from digital_twin.modules.notifications.domain.rule_delivery_comparison import rule_delivery_comparison
from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
from digital_twin.modules.notifications.domain.rulebox_notification_summary import compact_rulebox_summary
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
from test_typedb_independent_observation import readable_fixture, independent_fixture
from test_rulebox_decision_brief import fixture as policy_fixture
from test_investment_insight_dispatch_service import frozen_notification_claim


def subject(actions=()):
    value = dict(accountId='a', symbol='TEST', worldId='world:a', inferenceGenerationId='g', sourceAboxSnapshotId='abox')
    value['decisionBrief'] = {'judgment': {**value, 'authority': 'typedb-materialized-synthesis',
        'candidateGroups': [{'action': a, 'executionEligible': True, 'supportingRuleIds': ['rule']} for a in actions]}}
    return value


def event(kind='support-added'):
    return {'purpose': 'hypothesis-evidence', 'sourceRuleIds': ['rule'], 'changeKind': kind,
            'parentJobId': 'original', 'intent': {'claimContractId': 'claim', 'claimFingerprint': 'fingerprint'}}


class RuleNoticeDeliveryValueTests(TestCase):
    def test_successful_observation_receipt_consumes_meaning_not_polling_clock(self):
        from digital_twin.modules.notifications.domain.relation_follow_up import delivery_snapshot
        context = readable_fixture()
        packet = rule_delivery_comparison(context, {})['relationChangeEvidence']
        receipt = delivery_snapshot(packet, 'delivered-observation', packet['current']['accountId'])
        receipt.update(receiptJobId='delivered-observation', deliveredAt='2026-11-14T01:00:00Z')
        transition = context['ontologyRelationContext']['hypothesisLifecycle']['transitions'][0]
        transition.update(transitionId='another-poll', occurredAt='2026-11-14T01:05:00Z')
        for stage in ['publication', 'admission', 'dispatch-receipt-lock']:
            repeated = rule_delivery_comparison(context, receipt, stage=stage)['relationChangeEvidence']
            self.assertFalse(repeated['eligible'], stage)
            self.assertFalse(repeated['customerEvents'])

    def test_verified_observations_can_deliver_without_promoting_execution_qualification(self):
        for qualification in ['shadow', 'observed', 'limited-active', 'quarantined', 'unknown', 'active']:
            context = readable_fixture()
            context['ontologyRelationContext']['hypothesisSet']['hypotheses'][0]['qualification']['status'] = qualification
            frozen = deepcopy(context)
            for stage in ['publication', 'admission', 'dispatch-receipt-lock']:
                packet = rule_delivery_comparison(context, {}, stage=stage)['relationChangeEvidence']
                self.assertEqual(qualification not in {'quarantined', 'unknown'}, packet['eligible'], (qualification, stage))
                if packet['eligible']:
                    value = packet['customerEvents'][0]['deliveryValue']
                    self.assertTrue(value['observationOnly'])
                    self.assertFalse(value['executionAuthorized'])
                    self.assertEqual('verified-observation-change', value['reasonCode'])
                else:
                    self.assertTrue(packet['historyOnlyEvents'])
            self.assertEqual(frozen, context, 'policy cannot consume a receipt or rewrite qualification')
        result, queue, handoff, case = independent_fixture(qualified=False)
        self.assertEqual(1, result['typedbPublishedCount'])
        self.assertEqual(0, result['aiQueuedCount'])
        self.assertTrue(queue.jobs)
        self.assertFalse(handoff.events)
        self.assertEqual('PUBLISH_TYPEDB', case.inference_dispatch_decision.route)
        self.assertFalse(case.synthesis.execution_eligible_hypothesis_ids)

    def test_observation_requires_exact_proof_scope_and_does_not_bypass_receipt_novelty(self):
        packet = rule_delivery_comparison(readable_fixture(), {})['relationChangeEvidence']
        original = packet['customerEvents'][0]
        for key in ['accountId', 'symbol', 'worldId', 'inferenceGenerationId', 'sourceAboxSnapshotId']:
            changed = {**original, key: 'wrong'}
            self.assertFalse(delivery_value(changed, packet['current'], {})['allowed'])
        for change in [{'eligible': False}, {'reasonCode': 'same-customer-meaning'}, {'intent': {}},
                       {'changeKind': 'counter-added'}, {'evidenceUse': {'qualification': 'quarantined'}}]:
            self.assertFalse(delivery_value({**original, **change}, packet['current'], {})['allowed'])
        self.assertFalse(delivery_value(original, {**packet['current'], 'proofIdentityVerified': False}, {})['allowed'])
        self.assertFalse(delivery_value(original, {**packet['current'], 'rules': []}, {})['allowed'])

    def test_only_native_new_eligible_actions_authorize_a_positive_hypothesis_notice(self):
        old, new = subject(), subject(['TRIM'])
        self.assertTrue(delivery_value(event(), new, old)['allowed'])
        self.assertTrue(delivery_value(event(), new, new)['allowed'], 'an unsent draft cannot consume first delivery')
        receipt = {**new, 'receiptJobId': 'original', 'deliveredAt': '2026-10-23T01:00:00Z'}
        self.assertFalse(delivery_value(event(), new, receipt)['allowed'])
        other_rule = deepcopy(receipt)
        other_rule['decisionBrief']['judgment']['candidateGroups'][0]['supportingRuleIds'] = ['other-rule']
        self.assertFalse(delivery_value(event(), new, other_rule)['allowed'], 'a new supporting rule alone is not a new action')
        for mutation in ['pending', 'blocked', 'scope', 'authority', 'unrelated']:
            changed = deepcopy(new); judgment = changed['decisionBrief']['judgment']
            if mutation == 'pending': judgment['candidateGroups'][0]['executionEligible'] = False
            elif mutation == 'blocked': judgment['blockedActions'] = ['TRIM']
            elif mutation == 'scope': judgment['symbol'] = 'OTHER'
            elif mutation == 'authority': judgment['authority'] = 'invented'
            else: judgment['candidateGroups'][0]['supportingRuleIds'] = ['unrelated']
            self.assertFalse(delivery_value(event(), changed, old)['allowed'], mutation)

    def test_withdrawal_and_counter_evidence_require_a_scoped_successful_receipt(self):
        current, previous = subject(), subject(['TRIM'])
        self.assertFalse(delivery_value(event(), current, previous)['allowed'])
        previous.update(receiptJobId='original', deliveredAt='2026-10-23T01:00:00Z')
        previous['customerEvents'] = [event()]
        self.assertTrue(delivery_value(event(), current, previous)['allowed'])
        for kind in ['invalidated', 'counter-added', 'support-removed', 'mixed-evidence']:
            notice = event(kind); notice['evidenceChanges'] = [{'kind': 'counter-added'}]
            self.assertTrue(delivery_value(notice, current, previous)['allowed'])
            for key in ['accountId', 'symbol', 'worldId', 'deliveredAt']:
                wrong = {**previous, key: ''}
                self.assertFalse(delivery_value(notice, current, wrong)['allowed'], (kind, key))
        self.assertFalse(delivery_value(event('invalidated'), current, {})['allowed'])

    def test_prior_root_must_belong_to_the_same_claim(self):
        current, previous = subject(), subject()
        previous.update(receiptJobId='latest', deliveredAt='2026-10-23T01:00:00Z', notificationIntentContinuity={
            'intent': {'rootJobId': 'original', 'purpose': 'hypothesis-evidence', 'intent': event()['intent']}})
        self.assertTrue(delivery_value(event('invalidated'), current, previous)['allowed'])
        previous['notificationIntentContinuity']['intent']['intent'] = {'claimContractId': 'other'}
        self.assertFalse(delivery_value(event('invalidated'), current, previous)['allowed'])
        previous['receiptJobId'] = 'original'
        self.assertFalse(delivery_value(event('invalidated'), current, previous)['allowed'])

    def test_policy_strategy_context_and_outcome_keep_their_own_delivery_contracts(self):
        for extra in [{'purpose': 'management-policy'}, {'purpose': 'execution-constraint'},
                      {'purpose': 'context-evidence'}, {'strategyPhases': ['entry']},
                      {'strategyPhases': ['invalidation']}, {'outcome': {'outcomeId': 'result'}}]:
            self.assertTrue(delivery_value({**event(), **extra}, subject(), {})['allowed'])
        context = policy_fixture()
        context['ontologyRelationContext']['matchedRules'][0]['claimContract'] = frozen_notification_claim('rule:policy', 'risk-invariant')
        result = rule_delivery_comparison(context, {})['relationChangeEvidence']
        self.assertTrue(result['eligible'])
        self.assertTrue(result['customerEvents'])
        self.assertTrue(any(e['purpose'] == 'management-policy' for e in result['customerEvents']))

    def test_receipt_locked_transport_rejects_already_queued_observation(self):
        context = readable_fixture()
        context['ontologyRelationContext']['hypothesisSet']['hypotheses'][0]['qualification']['status'] = 'quarantined'
        job = NotificationJob.create('queued before the policy change', message_type='investmentInsight', context=context)
        store = object.__new__(MySQLNotificationJobStore)
        store.connect = Mock(); store.connect.return_value.__enter__ = Mock(return_value=Mock())
        store.connect.return_value.__exit__ = Mock(return_value=False)
        store.relation_delivery_snapshot_with_connection = Mock(return_value={})
        store.rule_for_connection = Mock()
        result = store.recheck_delivery_cadence(job)
        self.assertFalse(result['allowed'])
        self.assertEqual('hypothesis_evidence_history_only', result['suppressionReason'])
        store.rule_for_connection.assert_not_called()
        self.assertTrue(job.context['relationChangeEvidence']['historyOnlyEvents'])

    def test_pretransport_check_does_not_request_new_data_for_a_value_exclusion(self):
        from digital_twin.modules.notifications.application.notification_service import NotificationQueueRunner
        context = readable_fixture()
        context['ontologyRelationContext']['hypothesisSet']['hypotheses'][0]['qualification']['status'] = 'quarantined'
        job = NotificationJob.create('queued', message_type='investmentInsight', context=context)
        runner = NotificationQueueRunner(Mock(), Mock(), Mock())
        runner.request_fresh_data_recheck = Mock()
        self.assertFalse(runner.apply_dispatch_freshness_gate(job, 'dispatch'))
        self.assertEqual('hypothesis_evidence_history_only', job.context['deliverySuppressionReason'])
        runner.request_fresh_data_recheck.assert_not_called()

    def test_summary_keeps_unchanged_management_and_does_not_mix_release_target_into_hypothesis(self):
        packet = relation_change_evidence(readable_fixture())
        current = packet['current']; judgment = {k: current[k] for k in
            ('accountId', 'symbol', 'worldId', 'inferenceGenerationId', 'sourceAboxSnapshotId')}
        response = {'ruleId': 'policy', 'label': '손실 구간의 추가매수를 중단', 'observedValue': -6.37,
                    'targetValue': 0, 'objective': {'field': 'profitLossRate', 'operator': '>='}}
        composition = {**judgment, 'version': 'native-evidence-composition-v1',
                       'authority': 'typedb-materialized-synthesis', 'relationships': [{'sharedObservation': True}]}
        judgment.update(authority='typedb-materialized-synthesis', managementDecision={'responses': [response]},
                        evidenceComposition=composition)
        current['decisionBrief']['judgment'] = judgment
        packet['policyLifecycle'] = {'decisionImpact': {'status': 'maintained', 'remainingResponses': [response]}}
        result = compact_rulebox_summary(packet)
        sections = {key: ' '.join(rows) for key, _, rows in result['sections']}
        self.assertIn('이번 근거 변화로 관리 대응이 바뀌지는 않았습니다', sections['decision-basis'])
        self.assertIn('같은 관측', sections['decision-basis'])
        self.assertNotIn('0%', sections['next-check-contract'])
        self.assertNotIn('해제 기준', sections['decision-impact'])
        self.assertNotIn('계좌 관리:', sections['decision-impact'])
