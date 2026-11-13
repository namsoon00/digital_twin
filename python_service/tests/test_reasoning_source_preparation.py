"""Preparation reuse keeps exact source bytes, authority and transaction boundaries."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from sys import getsizeof
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from digital_twin.infrastructure import reasoning_snapshot_source
from digital_twin.infrastructure.transactions import monitoring
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.infrastructure.snapshot_payload_codec import snapshot_json_dumps
from digital_twin.modules.market_data.infrastructure import transaction_writes as market_writes
from digital_twin.modules.reasoning.infrastructure import transaction_writes as reasoning_writes
from digital_twin.modules.reasoning.infrastructure.source_packet_cache import SourcePacketCache
from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_monitor_state_for_ontology
from digital_twin.modules.reasoning.domain.reasoning_source_snapshot import build_reasoning_source_snapshot
from digital_twin.modules.portfolio.domain.portfolio import account_snapshot_from_monitor_state
from test_reasoning_snapshot_replay import monitor_state
from stabilization_database import StabilizationDatabaseCase, fixture_snapshot


class ReadRepository:
    """The real repository method with a deterministic, mutable durable row."""
    def __init__(self):
        self.rows, self.queries = {}, []
        self.fail = False
        self.store = object.__new__(monitoring.MySQLMonitorStore)
        self.store._source_packet_cache = SourcePacketCache()
        self.store.connect = self.connection

    @contextmanager
    def connection(self):
        yield self

    def execute(self, sql, params):
        self.queries.append(sql)
        if self.fail:
            raise RuntimeError('fixture unavailable')
        row = self.rows.get(params[0]) or {}
        if 'payload_json' not in sql:
            row = {'fingerprint': row['fingerprint']} if row else {}
        return SimpleNamespace(fetchone=lambda: deepcopy(row))

    def save(self, identity, state, fingerprint='first'):
        self.rows[identity] = {'fingerprint': fingerprint, 'payload_json': json_dumps(state)}


def context(identity, account='acct', symbol='AAPL'):
    return {'targetSymbols': [symbol], 'verifiedSourceSnapshot': {'accountId': account, 'snapshotId': identity}}


class ReasoningSourcePreparationTests(TestCase):
    def test_json_memo_is_bounded_exact_mutation_safe_and_preserves_non_json_types(self):
        from decimal import Decimal
        from digital_twin.shared_kernel.json_memo import json_memoized
        calls = []
        @json_memoized(max_bytes=80, max_entries=2)
        def normalize(value):
            calls.append(deepcopy(value))
            return deepcopy(value)
        value = {"scope": "A", "at": "original", "value": [0, False, None]}
        result = normalize(value); result["value"][0] = 100
        self.assertEqual(value, normalize(value))
        self.assertEqual(1, len(calls))
        for mutation in ({"scope": "B"}, {"at": "new"}, {"value": [0.0, False, None]}):
            changed = {**value, **mutation}
            self.assertEqual(changed, normalize(changed))
        before = len(calls)
        self.assertEqual(value, normalize(value))  # Old entry was evicted.
        self.assertEqual(before + 1, len(calls))
        for unsupported in ({"value": Decimal("1.2")}, {"value": (1, 2)}):
            before = len(calls)
            self.assertEqual(unsupported, normalize(unsupported))
            self.assertEqual(unsupported, normalize(unsupported))
            self.assertEqual(before + 2, len(calls))
        shared_calls = []
        @json_memoized(max_bytes=80, max_entries=4)
        def same_result(key):
            shared_calls.append(key)
            return {"value": "x" * 60}
        for key in range(4):
            same_result(key)
        for key in range(4):
            same_result(key)
        self.assertEqual(list(range(4)), shared_calls)
        same_result(4)  # Removing one key must retain its shared payload.
        same_result(1)
        self.assertEqual([0, 1, 2, 3, 4], shared_calls)
        same_result(0)
        self.assertEqual([0, 1, 2, 3, 4, 0], shared_calls)

    def test_symbol_normalization_reuse_preserves_scope_source_changes_and_uncached_result(self):
        from digital_twin.modules.news_intelligence.domain import company_knowledge as company
        build, merge = company.build_company_knowledge, company.merge_company_knowledge_rows
        for prepare in (build, merge):
            prepare.cache_clear(); self.addCleanup(prepare.cache_clear)
        source = {"name": "Apple", "peRatio": 30, "provider": "fixture", "fetchedAt": "2026-11-12T00:00:00Z"}
        with patch.object(company, "enrich_financial_periods", wraps=company.enrich_financial_periods) as reader:
            result = build("AAPL", overview=source)
            expected = deepcopy(result); result["valuation"].clear()
            count = reader.call_count
            self.assertEqual(expected, build("AAPL", overview=source))
            self.assertEqual(count, reader.call_count)
            self.assertEqual(expected, build.__wrapped__("AAPL", overview=source))
            self.assertEqual("MSFT", build("MSFT", overview=source)["symbol"])
            for field, value in (("peRatio", 31), ("fetchedAt", "2026-11-13T00:00:00Z")):
                source[field] = value
                count = reader.call_count
                changed = build("AAPL", overview=source)
                self.assertGreater(reader.call_count, count)
                self.assertNotEqual(expected, changed)
                self.assertEqual(build.__wrapped__("AAPL", overview=source), changed)
                expected = deepcopy(changed)
        with patch.object(company, "_normalize_company_knowledge_row", wraps=company._normalize_company_knowledge_row) as reader:
            merged = merge(expected, expected)
            expected_merge = deepcopy(merged); merged["valuation"].clear()
            count = reader.call_count
            self.assertEqual(expected_merge, merge(expected, expected))
            self.assertEqual(count, reader.call_count)
            self.assertEqual(expected_merge, merge.__wrapped__(expected, expected))
            changed = deepcopy(expected); changed["valuation"]["peRatio"] = 99
            self.assertEqual(merge.__wrapped__(expected, changed), merge(expected, changed))
            self.assertNotEqual(expected_merge, merge(expected, changed))

    def test_preparation_preserves_all_sql_bytes_and_canonical_delta_inputs(self):
        current = monitor_state()
        current['externalSignals'] = {'corporateActions': {'AAPL': {
            str(i): {'eventId': str(i), 'eventType': 'dividend', 'recordDate': '20260730'}
            for i in reversed(range(24))
        }}}
        current['metadata']['previousMonitorState'] = {'generatedAt': 'obsolete'}
        before = deepcopy(current)
        store = object.__new__(monitoring.MySQLMonitorStore)
        store.runtime_settings = {}
        for previous in [None, {}, monitor_state('2026-07-29T00:01:00Z'), monitor_state('2026-07-29T00:03:00Z')]:
            with self.subTest(previous_stamp=(previous or {}).get('generatedAt')):
                actual, legacy = Mock(), Mock()
                stamp = current['generatedAt']
                # Retain the legacy V1 SQL contract. V2 cache retirement and
                # encoded large evidence are covered by real storage tests.
                with patch.object(monitoring, 'snapshot_json_dumps', wraps=snapshot_json_dumps) as encode, \
                     patch.object(monitoring, 'active_reasoning_engine_with_connection', return_value={'engineVersion': 'v1'}):
                    serialized = store.upsert_snapshot_state_with_connection(actual, 'acct', current, stamp, previous)
                    prepared = monitoring.reasoning_snapshot_for_persisted_boundary(
                        account_snapshot_from_monitor_state(current), current, payload_json=serialized,
                    )
                    self.assertEqual(1, encode.call_count)
                market_writes.upsert_monitor_snapshot(legacy, 'acct', stamp, current, stamp)
                reasoning_writes.upsert_reasoning_snapshot_inputs(
                    legacy, 'acct', current, stamp, stamp, previous, _runtime_settings={},
                )
                market_writes.upsert_monitor_snapshot_history(
                    legacy, 'acct', stamp, current, compact_monitor_state_for_ontology(current), stamp,
                )
                reasoning_writes.insert_verified_source_snapshot(
                    legacy, build_reasoning_source_snapshot('acct', current, previous_state=previous), stamp,
                )
                self.assertEqual(legacy.execute.call_args_list, actual.execute.call_args_list)
                old = monitoring.reasoning_snapshot_for_persisted_boundary(account_snapshot_from_monitor_state(current), current)
                self.assertEqual(old.to_monitor_state(), prepared.to_monitor_state())
        self.assertEqual(before, current)

    def test_durable_monitor_state_bounds_provider_archives_without_mutating_source(self):
        current = monitor_state()
        current['externalSignals'] = {
            'macro': {'series': {'DGS10': {'value': 4.5, 'observationDate': '2026-11-10'}}},
            'companyOverviews': {'AAPL': {
                'symbol': 'AAPL', 'name': 'Apple', 'peRatio': 30,
                'sourceArchive': {'document': 'overview archive ' * 10000},
            }},
            'researchEvidence': {'AAPL': [{
                'evidenceId': 'evidence:one', 'title': 'Material event',
                'payload': {'relationScope': 'direct', 'eventType': 'earnings',
                            'rawArticleBody': 'article body ' * 10000},
            }]},
        }
        before = deepcopy(current)

        persisted = monitoring.bounded_monitor_snapshot_state(current, {})

        self.assertEqual(before, current)
        self.assertEqual(4.5, persisted['externalSignals']['macro']['series']['DGS10']['value'])
        self.assertEqual('evidence:one', persisted['externalSignals']['researchEvidence']['AAPL'][0]['evidenceId'])
        self.assertNotIn('sourceArchive', persisted['externalSignals']['companyOverviews']['AAPL'])
        self.assertNotIn('rawArticleBody', persisted['externalSignals']['researchEvidence']['AAPL'][0]['payload'])
        self.assertLess(len(json_dumps(persisted)), len(json_dumps(current)) // 10)
        self.assertEqual(
            'monitor-snapshot-storage-v2-bounded-signals',
            persisted['metadata']['monitorSnapshotStorage']['contractVersion'],
        )

    def test_monitor_transaction_timeout_is_isolated_and_never_shorter(self):
        self.assertEqual(
            '90',
            monitoring.monitoring_transaction_settings({
                'mysqlOperationTimeoutSeconds': '30',
                'monitoringMysqlOperationTimeoutSeconds': '90',
            })['mysqlOperationTimeoutSeconds'],
        )
        self.assertEqual(
            '120',
            monitoring.monitoring_transaction_settings({
                'mysqlOperationTimeoutSeconds': '120',
                'monitoringMysqlOperationTimeoutSeconds': '60',
            })['mysqlOperationTimeoutSeconds'],
        )

    def test_repeated_subjects_decode_independently_and_share_only_exact_source(self):
        repository = ReadRepository()
        repository.save('source:one', monitor_state())
        other = monitor_state(); other['accountId'] = 'another'
        repository.save('source:two', other)
        source = reasoning_snapshot_source.LatestMonitorSnapshotReasoningSource(repository.store)
        first = source.current_state('acct', context('source:one'))
        first['positions']['AAPL']['current_price'] = 999
        second = source.current_state('acct', context('source:one', symbol='MSFT'))
        self.assertEqual(100, second['positions']['AAPL']['current_price'])
        self.assertEqual(1, sum('payload_json' in sql for sql in repository.queries))
        self.assertEqual(2, len(repository.queries))
        source.now_provider = lambda: datetime(2026, 7, 29, 0, 2, tzinfo=timezone.utc)
        account = SimpleNamespace(account_id='acct')
        snapshot = source(account, context('source:one'))
        self.assertTrue(snapshot.has_live_account_data())
        snapshot.metadata['reasoningSnapshotReplay']['status'] = 'modified'
        snapshot.positions[0].current_price = 999
        next_snapshot = source(account, context('source:one', symbol='MSFT'))
        self.assertEqual('ready', next_snapshot.metadata['reasoningSnapshotReplay']['status'])
        self.assertEqual(100, next_snapshot.positions[0].current_price)
        self.assertEqual('another', source.current_state('another', context('source:two', 'another'))['accountId'])
        separate = ReadRepository(); separate.save('source:one', other)
        self.assertEqual('another', separate.store.reasoning_source_snapshot_state('source:one')['accountId'])

    def test_cache_rechecks_corrections_deletion_and_database_failure(self):
        repository = ReadRepository(); repository.save('source', monitor_state())
        source = reasoning_snapshot_source.LatestMonitorSnapshotReasoningSource(repository.store)
        self.assertEqual('acct', source.current_state('acct', context('source'))['accountId'])
        changed = monitor_state('2026-07-29T00:02:30Z')
        repository.save('source', changed, 'corrected')
        self.assertEqual(changed, source.current_state('acct', context('source')))
        self.assertEqual(2, sum('payload_json' in sql for sql in repository.queries))
        repository.fail = True
        self.assertEqual({}, source.current_state('acct', context('source')))
        repository.fail = False
        repository.rows.pop('source')
        self.assertEqual({}, source.current_state('acct', context('source')))
        self.assertIsNone(repository.store._source_packet_cache.get('source'))
        repository.save('source', monitor_state(), 'restored')
        self.assertEqual(monitor_state(), source.current_state('acct', context('source')))

    def test_missing_invalid_or_unfingerprinted_sources_cannot_gain_cached_authority(self):
        repository = ReadRepository()
        repository.store.payload = {'previous': {'acct': monitor_state()}}
        source = reasoning_snapshot_source.LatestMonitorSnapshotReasoningSource(repository.store)
        self.assertEqual({}, source.current_state('acct', context('missing')))
        for raw in ['not json', '[]', 'null']:
            repository.rows['source'] = {'payload_json': raw, 'fingerprint': raw}
            self.assertEqual({}, source.current_state('acct', context('source')))
        repository.save('source', monitor_state(), '')
        self.assertEqual(monitor_state(), source.current_state('acct', context('source')))
        self.assertIsNone(repository.store._source_packet_cache.get('source'))
        self.assertEqual(monitor_state(), source.current_state('acct', context('source')))
        self.assertIn('payload_json', repository.queries[-1])

    def test_cache_respects_lru_byte_budgets_unicode_and_oversized_replacement(self):
        cache = SourcePacketCache(max_entries=2, max_bytes=2048)
        cache.put('one', 'f', '처음'); cache.put('two', 'f', '두번째')
        self.assertEqual(('f', '처음'), cache.get('one'))
        cache.put('three', 'f', '세번째')
        self.assertIsNone(cache.get('two'))
        cache.put('one', 'g', '큰자료' * 2000)
        self.assertIsNone(cache.get('one'))
        self.assertIsNotNone(cache.get('three'))
        size = sum(getsizeof(value) for value in ('one', 'f', '한글'))
        bounded = SourcePacketCache(max_bytes=size)
        bounded.put('one', 'f', '한글')
        self.assertIsNotNone(bounded.get('one'))
        bounded.put('two', 'f', '한글')
        self.assertIsNone(bounded.get('one'))
        bounded.discard('two')
        self.assertEqual(0, bounded._bytes)
        disabled = SourcePacketCache(max_entries=0)
        disabled.put('one', 'f', '{}')
        self.assertIsNone(disabled.get('one'))


class ReasoningSourcePreparationStorageTests(StabilizationDatabaseCase):
    def test_real_source_cache_observes_commits_and_preserves_rollback(self):
        store = monitoring.MySQLMonitorStore(self.settings)
        state = fixture_snapshot(account='source-cache').to_monitor_state()
        packet = build_reasoning_source_snapshot('source-cache', state)
        with store.transaction() as connection:
            store.upsert_snapshot_state_with_connection(connection, 'source-cache', state)
        # This is the separate read-only adapter actually wired into live V2.
        # Its constructor intentionally skips the normal monitor initializer.
        reader = monitoring.MySQLOntologyReasoningMonitorStore(self.settings)
        self.assertEqual({}, reader.previous)
        self.assertEqual(packet['payload'], reader.reasoning_source_snapshot_state(packet['snapshotId']))
        self.assertEqual(packet['payload'], reader.reasoning_source_snapshot_state(packet['snapshotId']))
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with store.transaction() as connection:
                connection.execute('DELETE FROM verified_reasoning_source_snapshots WHERE snapshot_id=%s', (packet['snapshotId'],))
                # Separate reader still sees the previous committed authority.
                self.assertEqual(packet['payload'], reader.reasoning_source_snapshot_state(packet['snapshotId']))
                raise RuntimeError('rollback')
        self.assertEqual(packet['payload'], reader.reasoning_source_snapshot_state(packet['snapshotId']))
        corrected = deepcopy(packet['payload']); corrected['metadata']['correction'] = True
        with store.transaction() as connection:
            connection.execute('UPDATE verified_reasoning_source_snapshots SET payload_json=%s,fingerprint=%s WHERE snapshot_id=%s',
                               (json_dumps(corrected), 'corrected-fingerprint', packet['snapshotId']))
        self.assertEqual(corrected, reader.reasoning_source_snapshot_state(packet['snapshotId']))
        with store.transaction() as connection:
            connection.execute('DELETE FROM verified_reasoning_source_snapshots WHERE snapshot_id=%s', (packet['snapshotId'],))
        self.assertEqual({}, reader.reasoning_source_snapshot_state(packet['snapshotId']))

    def test_monitor_cycle_reuses_committed_payload_and_rolls_back_source_with_ingress(self):
        store = monitoring.MySQLMonitorStore(self.settings)
        recorder = monitoring.MySQLMonitoringCycleRecorder(self.settings, monitor_store=store)
        snapshot = fixture_snapshot(account='prepared-cycle')
        original = monitoring.reasoning_snapshot_for_persisted_boundary
        with patch.object(monitoring, 'reasoning_snapshot_for_persisted_boundary', wraps=original) as normalize:
            recorder.record_cycle([snapshot.account_id], [snapshot], [])
        serialized = normalize.call_args.kwargs['payload_json']
        self.assertIsInstance(serialized, str)
        with store.connect() as connection:
            current = connection.execute('SELECT payload_json FROM monitor_snapshots WHERE account_id=%s', (snapshot.account_id,)).fetchone()
            history = connection.execute('SELECT payload_json FROM monitor_snapshot_history WHERE account_id=%s AND generated_at=%s', (snapshot.account_id, snapshot.generated_at)).fetchone()
        self.assertEqual(serialized, current['payload_json'])
        self.assertEqual(serialized, history['payload_json'])
        next_snapshot = fixture_snapshot(account=snapshot.account_id, stamp='2026-09-10T01:02:00Z')
        next_snapshot.positions[0].current_price = 130
        packet = build_reasoning_source_snapshot(snapshot.account_id, next_snapshot.to_monitor_state())
        with patch.object(monitoring, 'ingress_reasoning_event_with_connection', side_effect=RuntimeError('ingress failed')):
            with self.assertRaisesRegex(RuntimeError, 'ingress failed'):
                recorder.record_cycle([snapshot.account_id], [next_snapshot], [])
        self.assertEqual({}, store.reasoning_source_snapshot_state(packet['snapshotId']))
        with store.connect() as connection:
            after = connection.execute('SELECT payload_json FROM monitor_snapshots WHERE account_id=%s', (snapshot.account_id,)).fetchone()
        self.assertEqual(serialized, after['payload_json'])
