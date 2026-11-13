"""Durable expiry follow-up: no forced analysis, no old-payload replay."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from unittest import TestCase
from unittest.mock import Mock

from stabilization_database import StabilizationDatabaseCase
from test_typedb_independent_observation import readable_fixture, independent_fixture
from digital_twin.modules.notifications.domain.rule_delivery_comparison import rule_delivery_comparison
from digital_twin.modules.notifications.domain.rule_source_recheck import recheck_request, reevaluation
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore


def packet_fixture():
    packet = rule_delivery_comparison(readable_fixture(), {})['relationChangeEvidence']
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    for rule in packet['current']['rules']:
        for condition in rule.get('conditions') or []:
            condition.update(observedAt=old, freshnessRequired=True, source='fixture', freshnessStatus='fresh',
                             judgementEvidenceUsable=True, maxAgeMinutes=10)
    return packet


def advanced(packet):
    current = deepcopy(packet['current'])
    current.update(inferenceGenerationId='generation:new', sourceAboxSnapshotId='abox:new',
                   customerEvents=deepcopy(packet['customerEvents']))
    for rule in current['rules']:
        for condition in rule.get('conditions') or []:
            condition['observedAt'] = datetime.now(timezone.utc).isoformat()
    return current


class RuleSourceRecheckTests(TestCase):
    def test_unexpired_slow_source_need_not_change_when_expired_price_is_refreshed(self):
        packet = packet_fixture()
        rule = next(r for r in packet['current']['rules'] if r.get('conditions'))
        slow = {**deepcopy(rule['conditions'][0]), 'conditionId': 'slow-source', 'maxAgeMinutes': 1440}
        rule['conditions'].append(slow)
        request = recheck_request(packet, 'rule_evidence_expired_at_dispatch', 'old-job')
        self.assertTrue(any(not r['refreshRequired'] for r in request['sourceClocks']))
        current = advanced(packet)
        for row in current['rules']:
            for condition in row.get('conditions') or []:
                if condition['conditionId'] == 'slow-source':
                    condition['observedAt'] = slow['observedAt']
        self.assertEqual('reevaluated-no-notice', reevaluation(request, current, {'subjectCaseId': 'case'})['status'])

    def test_existing_dispatch_worker_records_normal_evaluation_without_an_extra_send(self):
        recorder = Mock()
        result, queue, handoff, case = independent_fixture(qualified=False, source_evaluation_recorder=recorder)
        recorder.assert_called_once()
        context, outcome = recorder.call_args.args
        self.assertEqual(case.subject_case_id, context['investmentSubjectDecisionCaseId'])
        self.assertEqual(case.subject_case_id, outcome['subjectCaseId'])
        self.assertTrue(outcome['notificationJobId'])
        self.assertEqual(1, len(queue.jobs))
        self.assertFalse(handoff.events)
        recorder = Mock(side_effect=RuntimeError('fixture'))
        result, queue, _, _ = independent_fixture(qualified=False, source_evaluation_recorder=recorder)
        self.assertEqual('RuntimeError', result['outcomes'][0]['sourceRecheckTrackingError'])
        self.assertEqual(1, len(queue.jobs), 'tracking failures must not repeat sending')

    def test_requires_new_exact_source_proof_and_never_counts_queue_as_receipt(self):
        packet = packet_fixture()
        request = recheck_request(packet, 'rule_evidence_expired_at_dispatch', 'old-job')
        self.assertTrue(request)
        frozen = deepcopy(packet)
        outcome = {'subjectCaseId': 'new-case', 'queued': True, 'notificationJobId': 'new-job'}
        current = advanced(packet)
        result = reevaluation(request, current, outcome)
        self.assertEqual('awaiting-delivery', result['status'])
        self.assertFalse(result['receiptVerified'])
        self.assertEqual('new-job', result['successorJobId'])
        for field in ['accountId', 'symbol', 'worldId']:
            self.assertEqual({}, reevaluation(request, {**current, field: 'other'}, outcome))
        for field in ['inferenceGenerationId', 'sourceAboxSnapshotId']:
            self.assertEqual({}, reevaluation(request, {**current, field: request[field]}, outcome))
        unchanged = deepcopy(packet['current'])
        unchanged.update(inferenceGenerationId='new-generation', sourceAboxSnapshotId='new-abox')
        self.assertEqual({}, reevaluation(request, unchanged, outcome), 'fetch/generation alone cannot renew a premise')
        self.assertEqual({}, reevaluation(request, {**current, 'rules': []}, outcome))
        self.assertEqual({}, reevaluation(request, {**current, 'proofIdentityVerified': False}, outcome))
        self.assertEqual('reevaluated-no-notice', reevaluation(request, current, {**outcome, 'queued': False})['status'])
        unrelated = {**current, 'customerEvents': []}
        self.assertEqual('', reevaluation(request, unrelated, outcome)['successorJobId'])
        self.assertEqual(frozen, packet, 'original source proof must remain immutable')


class RuleSourceRecheckDatabaseTests(StabilizationDatabaseCase):
    def expired_job(self):
        packet = packet_fixture()
        current = packet['current']
        context = {'symbol': current['symbol'], 'relationChangeEvidence': packet, 'deliverySuppressionReason': 'rule_evidence_expired_at_dispatch'}
        job = NotificationJob.create('fixture', account_id=current['accountId'],
                                     message_type='investmentInsight', context=context)
        self.notifications.mark_suppressed(job, 'fixture expired')
        return job, packet

    def test_restart_idempotence_new_source_no_receipt_and_exact_actual_receipt(self):
        job, packet = self.expired_job()
        original = deepcopy(job.context['relationChangeEvidence'])
        self.notifications.mark_suppressed(job, 'fixture expired again')
        self.assertEqual(1, self.sql('SELECT COUNT(*) AS n FROM rule_notification_rechecks WHERE source_job_id=%s', (job.job_id,))['n'])
        restarted = MySQLNotificationJobStore(self.settings)
        self.assertEqual('pending', restarted.rule_source_recheck(job.job_id)['status'])
        successor = NotificationJob.create('new fixture', account_id=job.account_id, context={'symbol': packet['current']['symbol']}, message_type='investmentInsight')
        successor.status = 'done'
        restarted.upsert_job(successor)
        outcome = {'subjectCaseId': 'fixture-new-case', 'queued': True, 'notificationJobId': successor.job_id}
        self.assertEqual(1, restarted.record_rule_source_snapshot(advanced(packet), outcome))
        self.assertEqual(0, restarted.record_rule_source_snapshot(advanced(packet), outcome))
        restarted.reconcile_rule_source_rechecks()
        self.assertEqual('awaiting-delivery', restarted.rule_source_recheck(job.job_id)['status'], 'done is not a receipt')
        stamp = datetime.now(timezone.utc).isoformat()
        for suffix, audience, verified in [('research', 'aiResearch', True), ('unverified', 'accountNotification', False)]:
            self.sql("INSERT INTO notification_delivery_attempts (attempt_id,job_id,audience,status,reason,metadata_json,started_at,completed_at) "
                     "VALUES (%s,%s,%s,'delivered','',%s,%s,%s)",
                     (successor.job_id+suffix, successor.job_id, audience, json.dumps({'receiptVerified': verified}), stamp, stamp))
        restarted.reconcile_rule_source_rechecks()
        self.assertEqual('awaiting-delivery', restarted.rule_source_recheck(job.job_id)['status'])
        self.sql("INSERT INTO notification_delivery_attempts (attempt_id,job_id,audience,status,reason,metadata_json,started_at,completed_at) "
                 "VALUES (%s,%s,'accountNotification','delivered','',%s,%s,%s)",
                 (successor.job_id+'actual', successor.job_id, json.dumps({'receiptVerified': True}), stamp, stamp))
        restarted.reconcile_rule_source_rechecks()
        self.assertEqual('awaiting-delivery', restarted.rule_source_recheck(job.job_id)['status'], 'an unlinked receipt is insufficient')
        snapshot = advanced(packet)
        for key, bad in [('worldId', 'wrong-world'), ('inferenceGenerationId', 'wrong-generation'), ('customerEvents', [])]:
            wrong = {**snapshot, key: bad}
            self.sql('UPDATE notification_delivery_attempts SET metadata_json=%s WHERE attempt_id=%s',
                     (json.dumps({'receiptVerified': True, 'relationChangeSnapshot': wrong}), successor.job_id+'actual'))
            restarted.reconcile_rule_source_rechecks()
            self.assertEqual('awaiting-delivery', restarted.rule_source_recheck(job.job_id)['status'])
        self.sql('UPDATE notification_delivery_attempts SET metadata_json=%s WHERE attempt_id=%s',
                 (json.dumps({'receiptVerified': True, 'relationChangeSnapshot': snapshot}), successor.job_id+'actual'))
        for field, bad, good in [('is_mock', 1, 0), ('data_quality', 'reference', 'actual'), ('account_id', 'other', job.account_id)]:
            self.sql('UPDATE notification_jobs SET '+field+'=%s WHERE job_id=%s', (bad, successor.job_id))
            restarted.reconcile_rule_source_rechecks()
            self.assertEqual('awaiting-delivery', restarted.rule_source_recheck(job.job_id)['status'])
            self.sql('UPDATE notification_jobs SET '+field+'=%s WHERE job_id=%s', (good, successor.job_id))
        restarted.reconcile_rule_source_rechecks()
        result = restarted.rule_source_recheck(job.job_id)
        self.assertEqual('delivered', result['status'])
        self.assertTrue(result['result']['receiptVerified'])
        restarted.mark_suppressed(job, 'idempotent original audit')
        self.assertEqual('delivered', job.context['freshDataRecheck']['status'])
        self.assertEqual(result['deadline_at'], job.context['freshDataRecheck']['deadlineAt'])
        self.assertEqual(original, restarted.get(job.job_id).context['relationChangeEvidence'])

    def test_suppression_and_ticket_are_atomic_and_deadline_is_not_success(self):
        packet = packet_fixture()
        job = NotificationJob.create('rollback fixture', account_id=packet['current']['accountId'],
            message_type='investmentInsight', context={'symbol': packet['current']['symbol'], 'relationChangeEvidence': packet,
                                                       'deliverySuppressionReason': 'rule_evidence_expired_at_dispatch'})
        with self.assertRaises(RuntimeError):
            with self.notifications.transaction() as connection:
                self.notifications.register_rule_recheck(connection, job)
                raise RuntimeError('rollback')
        self.assertEqual({}, self.notifications.rule_source_recheck(job.job_id))
        self.notifications.mark_suppressed(job, 'expired')
        self.sql("UPDATE rule_notification_rechecks SET deadline_at='2000-01-01T00:00:00Z' WHERE source_job_id=%s", (job.job_id,))
        self.notifications.reconcile_rule_source_rechecks()
        result = self.notifications.rule_source_recheck(job.job_id)
        self.assertEqual('unresolved', result['status'])
        self.assertFalse(result['result']['receiptVerified'])
        self.assertEqual('new-source-proof-not-confirmed', result['result']['reasonCode'])
