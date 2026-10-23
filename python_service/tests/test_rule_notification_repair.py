"""Native composition predicates and receipt-consistent notification delivery."""
from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import Mock

from digital_twin.infrastructure.typedb_ontology import TypeDBOntologyGraphRepository
from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.modules.reasoning.infrastructure.typeql.profiles import typedb_native_rule_profile
from digital_twin.modules.notifications.domain.rule_delivery_comparison import rule_delivery_comparison
from digital_twin.modules.notifications.domain.rule_notice_delivery_value import has_receipt_correction
from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
from digital_twin.modules.notifications.domain.context_observation_notifications import context_observation_delivery_decision
from digital_twin.modules.notifications.domain.notification_rule_evaluator import apply_state_cooldown_rule
from digital_twin.modules.notifications.domain.notification_rule_models import NotificationRuleConfig, NotificationRuleDecision
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
from digital_twin.modules.decisions.application.investment_insight_dispatch_service import InvestmentInsightDispatchService
from test_rule_notification_intent import counter_context
from test_investment_insight_dispatch_service import (subject_case, context_observation, alert,
    FakeOrchestrator, FakeIngress, FakeNotificationQueue, FakeAIHandoff)


def correction_context():
    context = counter_context()
    original = relation_change_evidence(context, context['relationChangeEvidence']['previous'])
    event = original['customerEvents'][0]
    previous = original['previous']
    previous.update(receiptJobId='original', deliveredAt='2026-10-02T00:50:00Z',
        notificationIntentContinuity={event['intentKey']: {**event, 'rootJobId': 'original',
            'fingerprint': 'original', 'conclusionFingerprint': 'original'}})
    context.update(rule_delivery_comparison(context, previous))
    context.update(notificationDecisionOwner='typedb', notificationAiBypass={'status': 'typedb-direct'},
        inferenceDispatchDecision={'route': 'PUBLISH_TYPEDB'}, contextObservationDeliveryDecision={
            'decision': 'send', 'authorizationSources': ['typedb-relation-transition']})
    return context, previous


class RuleNotificationRepairTests(TestCase):
    def test_composition_filters_have_physical_write_and_native_read_contracts(self):
        repository = TypeDBOntologyGraphRepository('127.0.0.1:1729')
        rules = [r for r in default_graph_inference_rules() if r.rule_id.startswith('graph.composition.')]
        self.assertEqual(3, len(rules))
        schema = repository.schema_query()
        for rule in rules:
            profile = typedb_native_rule_profile(rule.to_dict())
            self.assertEqual('ready', profile['status'], profile['blockers'])
            for name in ['contract-matched', 'freshness-compatible']:
                attribute = 'ontology-model-' + name
                self.assertIn('has ' + attribute + ' "true"', profile['typeqlRuleBlueprint'])
                self.assertIn('attribute ' + attribute + ', value string;', schema)
                self.assertIn('owns ' + attribute, schema)
        for value in [True, False, None]:
            properties = {} if value is None else {'contractMatched': value, 'freshnessCompatible': value}
            query = repository.node_insert_query({'id': 'model', 'kind': 'statistical-model-hypothesis-evidence',
                'propertiesJson': json.dumps(properties)}, '2026-10-24T00:00:00Z')
            for name in ['contract-matched', 'freshness-compatible']:
                fragment = 'has ontology-model-' + name
                if value is None:
                    self.assertNotIn(fragment, query, 'missing is not true or false')
                else:
                    self.assertIn(fragment + (' "true"' if value else ' "false"'), query)

    def test_dispatch_loads_receipt_before_publication_and_archives_same_event(self):
        case = subject_case('subject:receipt')
        context = context_observation(case)
        context['accountId'] = case.account_id
        packet = relation_change_evidence(context)
        from digital_twin.modules.notifications.domain.relation_follow_up import delivery_snapshot
        receipt = delivery_snapshot(packet, 'delivered', case.account_id)
        receipt.update(receiptJobId='delivered', deliveredAt='2026-10-01T00:00:00Z')
        reader = Mock(return_value=receipt)
        queue, ai = FakeNotificationQueue(), FakeAIHandoff()
        service = InvestmentInsightDispatchService(FakeIngress(), queue, ai, FakeOrchestrator([case]),
            legacy_ai_handoff_enabled=False, notification_receipt_reader=reader)
        result = service.dispatch([alert(case, context, 'receipt')])
        reader.assert_called_once_with(case.account_id, case.symbol, 'world:fixture')
        self.assertEqual(0, result['queuedCount'])
        self.assertFalse(queue.jobs)
        self.assertFalse(ai.events)
        self.assertEqual('suppress', case.inference_dispatch_decision.details['semanticDeliveryDecision']['decision'])

    def test_loaded_empty_receipt_cannot_fall_back_to_provisional_or_unrelated_history(self):
        context, previous = correction_context()
        context['ruleNotificationReceiptBaseline'] = {}
        decision = context_observation_delivery_decision(context)
        self.assertEqual('none', decision['baselineSource'])
        self.assertEqual('suppress', decision['decision'])
        context['ruleNotificationReceiptBaseline'] = {**previous, 'accountId': 'other'}
        self.assertEqual('suppress', context_observation_delivery_decision(context)['decision'])
        store = object.__new__(MySQLNotificationJobStore)
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {
            'job_id': 'original', 'completed_at': previous['deliveredAt'], 'snapshot': json.dumps(previous)}
        job = NotificationJob.create('scoped', account_id='main', message_type='investmentInsight', context=context)
        baseline = store.relation_delivery_snapshot_with_connection(connection, job)
        self.assertEqual('original', baseline['receiptJobId'])
        query, parameters = connection.execute.call_args.args
        self.assertEqual((job.job_id, 'main', 'MSTR', 'world:fixture'), parameters)
        self.assertNotIn('relationChangeSubjectKey', query, 'dispatch subtype must not choose a different baseline')
        self.assertIn('relationChangeSnapshot.worldId', query)
        connection.execute.return_value.fetchone.return_value['snapshot'] = json.dumps({**previous, 'worldId': 'other'})
        self.assertEqual({}, store.relation_delivery_snapshot_with_connection(connection, job))


    def test_delivered_premise_correction_bypasses_repeat_interval_but_not_receipt_deduplication(self):
        context, previous = correction_context()
        self.assertTrue(has_receipt_correction(context['relationChangeEvidence']))
        config = NotificationRuleConfig(message_type='investmentInsight', state_cooldown_enabled=True,
            material_cooldown_minutes=60, immediate_cooldown_minutes=10)
        decision = NotificationRuleDecision(message_type='investmentInsight', enabled=True, should_send=True, delivery_state='send', gate_state='eligible', gate_reason='', fingerprint='new-counter')
        decision.state_last_sent_age_minutes = 1
        job = NotificationJob.create('correction', message_type='investmentInsight', context=context)
        result = apply_state_cooldown_rule(decision, config, 1, last_sent_age_minutes=1, last_sent_at='2026-10-02T01:09:00Z', job=job)
        self.assertTrue(result.should_send)
        self.assertEqual('correction', result.delivery_cadence_tier)
        store = object.__new__(MySQLNotificationJobStore)
        store.connect = Mock(); store.connect.return_value.__enter__ = Mock(return_value=Mock())
        store.connect.return_value.__exit__ = Mock(return_value=False)
        store.relation_delivery_snapshot_with_connection = Mock(return_value=previous)
        store.rule_for_connection = Mock()
        self.assertTrue(store.recheck_delivery_cadence(job)['allowed'])
        store.rule_for_connection.assert_not_called()
        from digital_twin.modules.notifications.domain.relation_follow_up import delivery_snapshot
        receipt = delivery_snapshot(job.context['relationChangeEvidence'], 'already-delivered', 'main')
        receipt.update(receiptJobId='already-delivered', deliveredAt='2026-10-02T01:10:00Z')
        store.relation_delivery_snapshot_with_connection.return_value = receipt
        job.context['ruleNotificationReceiptBaseline'] = previous
        self.assertFalse(store.recheck_delivery_cadence(job)['allowed'])
        self.assertNotIn('ruleNotificationReceiptBaseline', job.context, 'the locked receipt supersedes publication')
        stale = deepcopy(context['relationChangeEvidence']); stale['eligible'] = False
        self.assertFalse(has_receipt_correction(stale))
        unrelated = deepcopy(context['relationChangeEvidence']); unrelated['previous']['accountId'] = 'other'
        self.assertFalse(has_receipt_correction(unrelated))
        from test_rule_notice_delivery_value import subject, event
        new_action = subject(['TRIM'])
        notice = {**event('counter-added'), 'eligible': True}
        old = {**subject(), 'receiptJobId': 'original', 'deliveredAt': '2026-10-02T00:50:00Z',
               'customerEvents': [notice]}
        self.assertTrue(has_receipt_correction({'eligible': True, 'current': new_action,
            'previous': old, 'customerEvents': [notice]}), 'new action does not downgrade a correction')
