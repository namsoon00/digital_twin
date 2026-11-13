"""A cached fresh label must never renew frozen rule evidence."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest import TestCase

from digital_twin.modules.reasoning.domain.ontology_inference_materializer import observation_metadata
from digital_twin.modules.notifications.domain.rule_dispatch_validity import rule_dispatch_validity
from digital_twin.modules.notifications.application.notification.admission import NotificationAdmissionPolicy
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.domain.notification_rules import default_notification_rule
from test_typedb_independent_observation import readable_fixture
from digital_twin.modules.notifications.domain.rule_delivery_comparison import rule_delivery_comparison


class RuleSourceFreshnessTests(TestCase):
    def test_source_clock_overrides_cached_label_age_and_fetch_but_preserves_input(self):
        props = dict(sourceAsOf='2026-11-14T01:00:00Z', sourceFetchedAt='2026-11-14T02:00:00Z',
                     freshnessStatus='fresh', freshnessAgeMinutes=0, judgementEvidenceUsable=True,
                     freshnessRequired=True, maxAgeMinutes=10)
        frozen = deepcopy(props)
        value = observation_metadata(props, 10, '2026-11-14T02:00:00Z')
        self.assertEqual(60, value['freshnessAgeMinutes'])
        self.assertEqual('stale', value['freshnessStatus'])
        self.assertFalse(value['judgementEvidenceUsable'])
        self.assertEqual(frozen, props)
        self.assertTrue(observation_metadata(props, 10, '2026-11-14T01:10:00Z')['judgementEvidenceUsable'])

    def test_fractional_budget_invalid_clocks_future_expiry_and_explicit_quality(self):
        props = dict(sourceAsOf='2026-11-14T01:00:00Z', freshnessStatus='fresh', freshnessRequired=True, maxAgeMinutes=.5)
        for change in ({'sourceAsOf': 'invalid'}, {'sourceAsOf': '2026-11-14T01:02:00Z'},
                       {'maxAgeMinutes': -1}, {'maxAgeMinutes': 0}, {'maxAgeMinutes': 'nan'},
                       {'maxAgeMinutes': 'invalid'}, {'validUntil': 'invalid'},
                       {'validUntil': '2026-11-14T01:00:10Z'}, {'freshnessStatus': 'stale'},
                       {'judgementEvidenceUsable': False}):
            with self.subTest(change=change):
                self.assertFalse(observation_metadata({**props, **change}, 10, '2026-11-14T01:00:20Z')['judgementEvidenceUsable'])
        self.assertFalse(observation_metadata(props, 10, '2026-11-14T01:00:31Z')['judgementEvidenceUsable'])
        self.assertTrue(observation_metadata({'freshnessRequired': False}, 10, '2026-11-14T01:00:20Z')['judgementEvidenceUsable'])
        # A historical replay is evaluated at its own immutable cutoff.
        self.assertTrue(observation_metadata(props, 10, '2026-11-14T01:00:20Z')['judgementEvidenceUsable'])

    def test_expired_rule_is_rejected_at_admission_even_with_generic_freshness_disabled(self):
        context = readable_fixture()
        relation = context['ontologyRelationContext']
        for trace in relation['graphStoreInference']['traces']:
            for condition in trace.get('matchedConditions') or []:
                condition.update(freshnessRequired=True, observedAt='2026-01-01T00:00:00Z',
                                 source='fixture', freshnessStatus='fresh', judgementEvidenceUsable=True, maxAgeMinutes=10)
        context.update(rule_delivery_comparison(context, {}))
        self.assertTrue(context['relationChangeEvidence']['eligible'])
        job = NotificationJob.create('fixture', account_id='main', message_type='investmentInsight', context=context)
        policy = NotificationAdmissionPolicy()
        decision = policy.prepare(job, default_notification_rule('investmentInsight'))
        decision.should_send = True
        decision.suppression_reason = ''
        result = policy.apply_result(job, decision, {'dataFreshnessEnabled': '0'})
        self.assertFalse(result.accepted)
        self.assertEqual('rule_evidence_expired_at_dispatch', job.context['deliverySuppressionReason'])
        self.assertEqual('admission', job.context['ruleDispatchValidity']['stage'])
        self.assertFalse(rule_dispatch_validity(context['relationChangeEvidence'], now=datetime.now(timezone.utc))['allowed'])
