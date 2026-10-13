"""Rejected drafts have receipt-backed, scoped, recoverable operations replies."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
import uuid
from unittest.mock import Mock, patch

from stabilization_database import StabilizationDatabaseCase
from ai_insight_fixtures import observation, persist_review
from test_notification_delivery_reliability import receipt
from digital_twin.modules.ai_orchestration.domain.planning import identity, stamp
from digital_twin.modules.ai_orchestration.domain.observation_diagnostic import observation_diagnostic
from digital_twin.modules.ai_orchestration.domain.diagnostic_progress import progress_state, progress_fingerprint
from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
from digital_twin.infrastructure.transactions.ai_control_publication import AIControlPublication
from digital_twin.modules.notifications.application.ai_observation_diagnostic import render_ai_observation_diagnostic
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed
from digital_twin.modules.notifications.infrastructure.notification.transport import TelegramNotifier


class DiagnosticProgressPolicyTests(unittest.TestCase):
    def test_format_repair_is_visible_without_rewriting_legacy_drafts(self):
        result = observation()
        result['validationRepair'] = {'status': 'validated', 'diagnostic': {'field': 'questionResolutions'}}
        original = render_ai_observation_diagnostic(observation_diagnostic(result, 'task', '문장 검증 보류'))
        self.assertIn('수정 없이 검토를 보류', original)
        frozen = deepcopy(result)
        result['diagnosticVersion'] = 'ai-observation-diagnostic-v2'
        revised = render_ai_observation_diagnostic(observation_diagnostic(result, 'task', '문장 검증 보류'))
        self.assertIn('출력 형식·필수 항목을 한 차례 수정', revised)
        self.assertIn('형식 검증은 통과했지만 최종 발송 검증은 통과하지 못', revised)
        self.assertEqual(original, render_ai_observation_diagnostic(observation_diagnostic(frozen, 'task', '문장 검증 보류')))

    def test_clocks_do_not_create_news_and_validation_is_not_a_receipt(self):
        task = {'taskId': 'next', 'status': 'completed', 'updatedAt': stamp(), 'result': {
            'processingOutcome': {'status': 'data-wait', 'requirements': ['공식 보고서', '최신 시세']}}}
        waiting = progress_state(task)
        task.update(updatedAt='later', nextCheckAt='later')
        task['result']['processingOutcome']['requirements'].reverse()
        self.assertEqual(progress_fingerprint(waiting), progress_fingerprint(progress_state(task)))
        task['result'] = {'publication': {'status': 'queued', 'jobId': 'normal'}}
        self.assertEqual('validated', progress_state(task)['stage'])
        self.assertNotIn('terminal', progress_state(task))
        self.assertEqual('delivery-blocked', progress_state(task, {'status': 'suppressed'})['stage'])
        self.assertTrue(progress_state(task, receipt={'jobId': 'normal', 'deliveredAt': stamp()})['terminal'])
        task['result'] = {'notification': {'send': True}, 'quality': {'status': 'accepted'},
                          'publication': {'status': 'recorded', 'reason': '시세가 오래되었습니다.'}}
        self.assertEqual(['시세가 오래되었습니다.'], progress_state(task)['reasons'])
        self.assertEqual('rejected', progress_state(task)['stage'])


class DiagnosticProgressStorageTests(StabilizationDatabaseCase):
    def setUp(self):
        self.account = 'draft-' + uuid.uuid4().hex[:12]
        self.subject = {'accountId': self.account, 'symbol': 'TEST', 'name': '검증 종목', 'worldId': 'draft-world'}
        self.control = MySQLAIControlStore({**self.settings, 'aiControlEnabled': 'true'})
        self.publication = AIControlPublication(self.settings, self.control, self.notifications, lambda: [self.subject])
        self.control.outbox_writer = self.publication.publish
        self.progress = self.publication.diagnostic_progress
        self.notifier = TelegramNotifier('123:fake', '456')
        self.notifier.post_message = Mock(side_effect=lambda _payload: receipt(str(100 + self.notifier.post_message.call_count)))
        self.dispatcher = NotificationDispatchService(self.notifications, lambda _: self.notifier,
            lambda _: self.notifier, delivery_guard=self.publication.delivery_guard)

    def tearDown(self):
        with self.control.transaction() as c:
            c.execute('DELETE FROM notification_delivery_attempts WHERE job_id IN (SELECT job_id FROM notification_jobs WHERE account_id=%s)', (self.account,))
            c.execute('DELETE FROM notification_jobs WHERE account_id=%s', (self.account,))
            c.execute('DELETE FROM ai_observation_diagnostic_events WHERE root_job_id IN (SELECT root_job_id FROM ai_observation_diagnostic_followups WHERE account_id=%s)', (self.account,))
            c.execute('DELETE FROM ai_observation_diagnostic_followups WHERE account_id=%s', (self.account,))
            c.execute('DELETE FROM ai_control_tasks WHERE account_id=%s', (self.account,))

    def task(self, task_id=None):
        value = {**self.subject, 'capability': 'observe', 'taskId': task_id or uuid.uuid4().hex,
                 'availableAt': stamp(), 'leaseToken': uuid.uuid4().hex, 'attempts': 1}
        with self.control.transaction() as c:
            self.control.insert(c, value)
            c.execute("UPDATE ai_control_tasks SET status='processing',attempts=1,lease_token=%s,lease_until=%s WHERE task_id=%s",
                (value['leaseToken'], (datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(), value['taskId']))
        return value

    def result(self, rejected=True):
        result = observation()
        result['input'].update(self.subject)
        result['input']['sourceSnapshots'] = {self.subject['worldId']: result['input']['sourceSnapshotId']}
        for fact in result['input']['facts']:
            fact['sourceWorldId'] = self.subject['worldId']
        if rejected:
            result['quality'].update(status='rejected', errors=['확인 가능한 조건이 없습니다.'])
        return result

    def job(self, job_id):
        with self.notifications.connect() as c:
            row=c.execute('SELECT text,payload_json FROM notification_jobs WHERE job_id=%s',(job_id,)).fetchone()
        return self.notifications.job_from_row(row)

    def send(self, job):
        body=NotificationRenderingService().render(job)
        with patch('digital_twin.infrastructure.settings.runtime_settings', return_value={**self.settings,'aiControlEnabled':'true'}):
            self.dispatcher.deliver(job, {self.account: object()}, body)
        self.notifications.mark_done(job)
        return body

    def root(self, send=True):
        task=self.task(); result=self.result()
        self.control.complete(task,result,[])
        job=self.job(result['publication']['diagnostic']['jobId'])
        if send: self.send(job)
        return task, job

    def progress_events(self, root):
        with self.control.connect() as c:
            rows=c.execute('SELECT payload_json FROM ai_observation_diagnostic_events WHERE root_job_id=%s ORDER BY created_at,event_id',(root.job_id,)).fetchall()
        return [json.loads(row['payload_json']) for row in rows]

    def waiting(self, task, reasons=None):
        result={'status':'awaiting-evidence', 'processingOutcome':{'status':'data-wait','requirements': reasons or ['공식 보고서의 비교 수치']}}
        self.control.complete(task,result,[])

    def test_real_lineage_replies_once_and_survives_retained_parent_receipt(self):
        parent, root=self.root()
        child=self.task(identity(parent['taskId'],'next'))
        self.assertEqual(1,self.progress.run_once()['queued'])
        first=self.progress_events(root)[0]
        self.assertEqual('reviewing',first['stage'])
        self.send(self.job(first['eventId']))
        self.assertEqual(101,self.notifier.post_message.call_args.args[0]['reply_parameters']['message_id'])
        self.waiting(child)
        self.assertEqual(1,self.progress.run_once()['queued'])
        waiting=self.progress_events(root)[-1]
        self.assertEqual('waiting',waiting['stage'])
        next_task=self.task(identity(child['taskId'],'wait')); self.waiting(next_task)
        self.assertEqual(0,self.progress.run_once()['queued'],'same wait on a new task is not another message')
        with self.notifications.transaction() as c:
            c.execute('DELETE FROM notification_jobs WHERE job_id=%s',(root.job_id,))
        body=self.send(self.job(waiting['eventId']))
        self.assertIn('공식',body)
        self.assertEqual(101,self.notifier.post_message.call_args.args[0]['reply_parameters']['message_id'])
        self.assertEqual([],self.publication.receipts(self.account))
        self.assertEqual(0,self.progress.run_once()['queued'])

    def test_acceptance_closes_only_after_real_normal_transport_receipt(self):
        parent,root=self.root(); child=self.task(identity(parent['taskId'],'next'))
        result=self.result(False); persist_review(self.control,child,result)
        self.control.complete(child,result,[])
        self.progress.run_once()
        self.assertEqual('validated',self.progress_events(root)[-1]['stage'])
        normal=self.job(result['publication']['jobId'])
        with self.notifications.transaction() as c:
            c.execute("UPDATE notification_jobs SET status='failed' WHERE job_id=%s", (normal.job_id,))
        self.progress.run_once()
        self.assertEqual('delivery-blocked', self.progress_events(root)[-1]['stage'])
        later=self.task(identity(child['taskId'],'next')); self.waiting(later)
        self.progress.run_once()
        self.assertEqual('waiting', self.progress_events(root)[-1]['stage'], 'an old delivery failure must not hide a later data wait')
        self.send(normal)
        self.progress.run_once()
        event=self.progress_events(root)[-1]
        self.assertEqual('delivered',event['stage'])
        self.assertEqual(normal.job_id,event['deliveryJobId'])
        self.assertEqual(child['taskId'],event['sourceTaskId'])
        body=self.send(self.job(event['eventId']))
        self.assertIn('원본의 미검증 문장을 승인한 것은 아닙니다',body)
        self.assertEqual(1,len(self.publication.receipts(self.account)))
        self.assertEqual(0,self.progress.run_once()['queued'])

    def test_unknown_parent_destination_tampering_and_unrelated_work_never_fall_back(self):
        parent,root=self.root(send=False)
        unrelated=self.task(); self.waiting(unrelated)
        self.assertEqual(0,self.progress.run_once()['queued'])
        child=self.task(identity(parent['taskId'],'next')); self.waiting(child)
        self.assertEqual(0,self.progress.run_once()['queued'],'a queued original is not an anchor')
        self.send(root); self.progress.run_once()
        event=self.progress_events(root)[-1]; job=self.job(event['eventId'])
        body=NotificationRenderingService().render(job)
        other=TelegramNotifier('123:fake','999'); other.post_message=Mock()
        dispatch=NotificationDispatchService(self.notifications,lambda _:other,lambda _:other,delivery_guard=self.publication.delivery_guard)
        with self.assertRaises(NotificationDeliverySuppressed): dispatch.deliver(job,{},body)
        other.post_message.assert_not_called()
        job.context['transportDelivery']={'checkpoint':{'messageIds':['partial'],
            'destinationFingerprint':self.notifier.destination_fingerprint,'replyToMessageId':'999'}}
        before=self.notifier.post_message.call_count
        with self.assertRaises(NotificationDeliverySuppressed): self.dispatcher.deliver(job,{},body)
        self.assertEqual(before, self.notifier.post_message.call_count)
        job.context.pop('transportDelivery')
        with self.assertRaises(NotificationDeliverySuppressed),self.publication.delivery_guard(job,body+'changed'): pass
        job.context['aiDiagnosticProgress']['worldId']='another'
        with self.assertRaises(NotificationDeliverySuppressed),self.publication.delivery_guard(job,body): pass

    def test_outbox_rollback_retry_and_rejected_successor_do_not_create_new_original(self):
        parent,root=self.root(); child=self.task(identity(parent['taskId'],'next'))
        rejected=self.result(); self.control.complete(child,rejected,[])
        self.assertEqual('follow-up',rejected['publication']['diagnostic']['status'])
        with patch.object(self.notifications,'enqueue_with_connection',side_effect=RuntimeError('test rollback')):
            with self.assertRaises(RuntimeError): self.progress.run_once()
        self.assertEqual([],self.progress_events(root))
        self.assertEqual(1,self.progress.run_once()['queued'])
        event=self.progress_events(root)[0]
        self.assertEqual('rejected',event['stage'])
        self.send(self.job(event['eventId']))
        with self.assertRaises(NotificationDeliverySuppressed): self.send(self.job(event['eventId']))

    def test_recent_original_adoption_requires_exact_legacy_successor_and_scope(self):
        parent,root=self.root()
        with self.control.transaction() as c:
            c.execute('DELETE FROM ai_observation_diagnostic_followups WHERE root_job_id=%s',(root.job_id,))
        wrong={**self.subject,'worldId':'other-world'}
        task=self.task(identity(parent['taskId'],'next'))
        with self.control.transaction() as c:
            c.execute("UPDATE ai_control_tasks SET payload_json=JSON_SET(payload_json,'$.worldId',%s) WHERE task_id=%s",(wrong['worldId'],task['taskId']))
        self.assertEqual(0,self.progress.run_once()['queued'])
        with self.control.transaction() as c:
            c.execute("UPDATE ai_control_tasks SET payload_json=JSON_SET(payload_json,'$.worldId',%s) WHERE task_id=%s",(self.subject['worldId'],task['taskId']))
        self.assertEqual(1,self.progress.run_once()['queued'])
        self.control.fail(task, 'TimeoutError')
        self.assertEqual(1, self.progress.run_once()['queued'])
        self.assertEqual('retry', self.progress_events(root)[-1]['stage'])
        replacement=self.task(); self.waiting(replacement)
        with self.control.transaction() as c:
            c.execute("UPDATE ai_control_tasks SET status='completed',result_json=%s WHERE task_id=%s",
                (json.dumps({'status':'superseded','replacementTaskId':replacement['taskId']}),task['taskId']))
        self.assertEqual(1, self.progress.run_once()['queued'])
        self.assertEqual('waiting', self.progress_events(root)[-1]['stage'])
        self.assertEqual(replacement['taskId'], self.progress_events(root)[-1]['sourceTaskId'])
