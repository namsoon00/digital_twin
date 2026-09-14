import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.external_api.mysql_stores import MySQLExternalDataStore
from digital_twin.modules.market_data.application.information_followup_service import stamp
from digital_twin.modules.market_data.infrastructure.mysql_information_followups import MySQLInformationFollowups
from digital_twin.modules.market_data.public import DatasetDescriptor
from digital_twin.modules.market_data.application.external_data.contracts import CollectionJob, ExternalSubject, FollowupCollectionRequest, SourceObservation
from digital_twin.shared_kernel.events import DomainEvent


@unittest.skipUnless(os.environ.get('MYSQL_DATABASE') == 'orbit_alpha_test', 'requires isolated test database')
class InformationStorageTests(unittest.TestCase):
    def test_document_revision_reader_restores_legacy_recovery_origin_without_using_current_body(self):
        store = MySQLExternalDataStore(runtime_settings())
        symbol = 'test-document-' + uuid.uuid4().hex
        now = stamp(datetime.now(timezone.utc))
        descriptor = DatasetDescriptor(dataset_id='sec.document', provider_id='sec-edgar', capability='official-document-body',
                                       cadence_seconds=86400, freshness_seconds=86400, completion_mode='once', revision_mode='immutable')
        try:
            for index, origin in ((1, 'document-recovery'), (2, 'sec-submissions')):
                accession = '0000000001-26-00000' + str(index)
                subject = ExternalSubject(symbol, symbol=symbol, source=origin)
                partition = symbol + ':' + accession + ':body-v1'
                request = FollowupCollectionRequest(descriptor.dataset_id, partition, subject, {'accessionNumber': accession})
                store.enqueue_followups([(descriptor, request)])
                job = CollectionJob(descriptor.dataset_id, partition, descriptor.provider_id, 50, subject)
                observation = SourceObservation(descriptor.dataset_id, descriptor.provider_id, symbol, accession + ':hash', now, now,
                                                {'document': index}, quality={'dataUsable': True})
                store.complete_observation(job, descriptor, observation, now)
            older = store.official_document_fact('sec.document', symbol, '0000000001-26-000001:hash')
            newer = store.official_document_fact('sec.document', symbol, '0000000001-26-000002:hash')
            self.assertEqual({'document': 1}, older['payload'])
            self.assertEqual('document-recovery', older['quality']['collectionSource'])
            self.assertEqual('sec-submissions', newer['quality']['collectionSource'])
            self.assertEqual({}, store.official_document_fact('sec.document', symbol, 'missing-revision'))
            self.assertNotIn('collectionSource', store.current_fact('sec.document', symbol)['quality'], 'legacy provenance read must not rewrite immutable facts')
        finally:
            with store.transaction() as c:
                c.execute('DELETE FROM external_fact_revision WHERE dataset_id=%s AND subject_key=%s', ('sec.document', symbol))
                c.execute('DELETE FROM external_fact_current WHERE dataset_id=%s AND subject_key=%s', ('sec.document', symbol))
                c.execute('DELETE FROM external_dataset_state WHERE dataset_id=%s AND partition_key LIKE %s', ('sec.document', symbol + ':%'))

    def test_provider_reservations_are_shared_across_store_instances(self):
        settings = runtime_settings()
        first, second = MySQLExternalDataStore(settings), MySQLExternalDataStore(settings)
        identity = 'test-information-' + uuid.uuid4().hex
        descriptor = DatasetDescriptor(dataset_id=identity, provider_id=identity, capability='test',
            cadence_seconds=60, freshness_seconds=60, rate_limit_seconds=6, daily_request_budget=1)
        now = datetime.now(timezone.utc)
        try:
            self.assertTrue(first.reserve_provider_call(descriptor, now)['allowed'])
            self.assertEqual(second.reserve_provider_call(descriptor, now)['reason'], 'rate-limited')
            self.assertEqual(second.reserve_provider_call(descriptor, now + timedelta(seconds=7))['reason'], 'daily-budget')
            with first.connect() as connection:
                row = connection.execute('SELECT request_count FROM external_provider_state WHERE provider_id=%s AND bucket_id=%s', (identity, identity)).fetchone()
                self.assertEqual(row['request_count'], 1)
        finally:
            with first.transaction() as connection:
                connection.execute('DELETE FROM external_provider_state WHERE provider_id=%s', (identity,))

    def test_followup_outbox_rollback_and_duplicate_completion(self):
        settings = runtime_settings()
        store = MySQLInformationFollowups(settings)
        identity = 'test-information-' + uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        row = {'trackingId': identity, 'sourceKind': 'test', 'sourceId': identity, 'sourceHash': 'v1',
            'status': 'active', 'nextCheckAt': stamp(now), 'createdAt': stamp(now), 'updatedAt': stamp(now)}
        event = DomainEvent('information.observation.updated', identity, payload={'trackingId': identity, 'decisionAuthority': False})
        try:
            self.assertTrue(store.register(row))
            self.assertFalse(store.register(row))
            changed = store.get(identity)
            changed.update(status='completed', updatedAt=stamp(now + timedelta(seconds=1)))
            def failure(*args):
                raise RuntimeError('outbox unavailable')
            store.notification_writer = failure
            with self.assertRaisesRegex(RuntimeError, 'outbox unavailable'):
                store.complete(changed, event, ['60'])
            self.assertEqual(store.get(identity)['status'], 'active')
            with store.connect() as connection:
                self.assertIsNone(connection.execute('SELECT event_id FROM domain_events WHERE event_id=%s', (event.event_id,)).fetchone())
            store.notification_writer = lambda *args: [{'phase': '60', 'state': 'queued'}]
            self.assertTrue(store.complete(changed, event, ['60']))
            self.assertFalse(store.complete(changed, event, ['60']))
            self.assertEqual(store.get(identity)['status'], 'completed')
        finally:
            with store.transaction() as connection:
                connection.execute('DELETE FROM information_followups WHERE tracking_id=%s', (identity,))
                connection.execute('DELETE FROM domain_events WHERE event_id=%s', (event.event_id,))


if __name__ == '__main__':
    unittest.main()
