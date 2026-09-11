"""Synthetic store state and failure injection, without live credentials."""

import copy
from dataclasses import asdict
import hashlib
import json
from unittest.mock import patch

from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION, SCOPED_ABOX_PERSISTENCE_MODE


WORLD = 'market:backend-fixture'
SCOPE = 'symbol:AAA'
NOW = '2026-01-01T00:00:00+00:00'
SAVE_SCENARIOS = ['ok', 'invalid', 'invalid-patch', 'lease-held', 'invalid-adopted', 'adopted',
                  'journal-unreadable', 'journal-same', 'journal-other', 'driver-missing',
                  'index-incomplete', 'cleanup-error', 'insert-error', 'count-mismatch',
                  'control-error', 'readback-error', 'release-error']


def save_scenario(api, scenario):
    graph = PortfolioOntology('backend-fixture', entities=[OntologyEntity(
        'stock:AAA', 'Fixture', 'stock', properties={'ontologyBox': 'ABox', 'worldId': WORLD,
        'aboxScopeId': SCOPE, 'snapshotId': 'generation:new', 'scopeGenerationId': 'generation:new'})],
        worldview={'worldId': WORLD, 'aboxSnapshotId': 'manifest:new', 'worldviewManifestId': 'manifest:new',
                   'persistenceMode': SCOPED_ABOX_PERSISTENCE_MODE, 'scopedAboxManifestVersion': SCOPED_ABOX_MANIFEST_VERSION,
                   'inferenceTargetSymbols': ['AAA'], 'scopePlan': [{'scopeId': SCOPE, 'generationId': 'generation:new',
                    'fingerprint': 'new', 'entityCount': 1, 'relationCount': 0}]})
    journal = {'status': 'empty'}
    events = []
    if scenario == 'invalid':
        graph.worldview.pop('scopePlan')
    if scenario == 'invalid-patch':
        graph.worldview['targetScopedManifestPatch'] = {'mode': 'incremental-target-scoped-manifest-patch'}
    if scenario.startswith('journal-'):
        journal = {'status': 'pending', 'candidateAboxSnapshotId': 'manifest:new' if scenario == 'journal-same' else 'manifest:other'}

    def pending(world_id=''):
        events.append(['journal', world_id])
        if scenario == 'journal-unreadable':
            raise RuntimeError('journal unavailable')
        return {'status': 'empty'} if scenario == 'readback-error' else copy.deepcopy(journal)

    def release(lease):
        events.append(['release', lease.get('leaseOwner')])
        if scenario == 'release-error':
            raise RuntimeError('release unavailable')
        return {'status': 'released'}

    def operation(name, result=None, error=''):
        def run(*args, **kwargs):
            events.append([name])
            if error and scenario == error:
                raise RuntimeError(name + ' unavailable')
            return copy.deepcopy(result)
        return run

    def write_rows(driver, imported, nodes, relations, **kwargs):
        events.append(['rows', len(nodes), len(relations)])
        if scenario == 'insert-error':
            raise RuntimeError('insert unavailable')
        return {'expectedCountsByScope': {SCOPE: {'entityCount': 1, 'relationCount': 0}},
                'reusedCountsByScope': {}, 'requestedNodeCount': 1, 'requestedRelationCount': 0,
                'insertedNodeCount': 1, 'insertedRelationCount': 0, 'reusedNodeCount': 0, 'reusedRelationCount': 0}

    def write_control(driver, imported, value, **kwargs):
        events.append(['control', [item.kind for item in value.entities]])
        if scenario == 'control-error':
            raise RuntimeError('control unavailable')
        journal.update(status='pending', candidateAboxSnapshotId='manifest:new', targetSymbols=['AAA'])

    with patch.object(api, 'runtime_settings', return_value={}), patch.object(api, 'utc_now', return_value=NOW), \
            patch.object(api.time, 'monotonic', return_value=100):
        repository = api.TypeDBOntologyGraphRepository('fixture.invalid', projection_coordinator_write_enforced=False, retry_count=0)
        repository.acquire_scoped_abox_write_lease = operation('acquire', {'acquired': scenario != 'lease-held', 'leaseOwner': 'fixture-owner'})
        repository.release_scoped_abox_write_lease = release
        repository.scoped_abox_write_lease_status = operation('owner', {'status': 'held', 'leaseOwner': 'wrong' if scenario == 'invalid-adopted' else 'fixture-owner'})
        repository.pending_abox_activation = pending
        repository.active_abox_metadata = operation('active', {'status': 'ok', 'worldId': WORLD, 'aboxSnapshotId': 'manifest:old',
            'scopedAboxManifestVersion': SCOPED_ABOX_MANIFEST_VERSION, 'scopeFingerprints': {SCOPE: 'old'}, 'scopeGenerationIds': {SCOPE: 'generation:old'}})
        repository.fresh_candidate_world_bootstrap_required = lambda world: False
        repository.driver_imports = operation('driver', (None, 'unavailable') if scenario == 'driver-missing' else (('fake-driver',), None))
        repository.open_driver = operation('open', 'fixture-driver')
        repository.close_driver = operation('close')
        repository.ensure_database = operation('database')
        repository.ensure_schema = operation('schema')
        repository.delete_box_manifest_rows_in_batches = operation('cleanup', {'status': 'ok', 'deletedBatchCount': 0}, 'cleanup-error')
        repository.write_persistence_rows = write_rows
        repository.write_graph = write_control
        repository.scoped_abox_scope_row_counts_batch = operation('counts', {SCOPE: {'entityCount': 0 if scenario == 'count-mismatch' else 1, 'relationCount': 0}})
        if scenario == 'index-incomplete':
            repository.prepare_scoped_manifest_native_rule_indexes = operation('index', {'status': 'incomplete'})
        adopted = {'leaseOwner': 'fixture-owner'} if scenario in {'adopted', 'invalid-adopted'} else None
        result = repository.save_scoped_abox_graph(graph, adopted_write_lease=adopted)
    return {'result': result, 'events': events, 'journal': journal, 'graph': asdict(graph)}


def native_gate_scenario(api, scenario):
    events = []
    with patch.object(api, 'runtime_settings', return_value={}):
        repository = api.TypeDBOntologyGraphRepository('fixture.invalid', projection_coordinator_write_enforced=False)
        repository._inference_write_lease_enabled = True
        repository._native_rule_execution_enabled = True
        repository.acquire_scoped_abox_write_lease = lambda *a, **k: {'acquired': scenario != 'held', 'leaseOwner': 'native-owner', 'worldId': WORLD}
        repository.scoped_abox_write_lease_status = lambda *a: {'status': 'held', 'leaseOwner': 'other' if scenario == 'invalid-adopted' else 'native-owner'}
        repository.release_scoped_abox_write_lease = lambda lease: events.append(['release', lease['leaseOwner']]) or {'status': 'released'}
        def run(payload):
            events.append(['run', dict(payload)])
            if scenario == 'error':
                raise RuntimeError('native execution failed')
            return {'status': 'fixture-complete'}
        repository._run_rulebox_unlocked = run
        payload = {'worldId': WORLD}
        if 'adopted' in scenario:
            payload['_inferenceWriteLeaseOwner'] = 'native-owner'
        try:
            result = repository.run_rulebox(payload)
        except Exception as error:
            result = {'raised': type(error).__name__, 'reason': str(error)}
    return {'result': result, 'events': events}


def execution_contracts(api):
    values = {'save/' + name: save_scenario(api, name) for name in SAVE_SCENARIOS}
    values.update({'native/' + name: native_gate_scenario(api, name) for name in ['ok', 'held', 'error', 'adopted', 'invalid-adopted']})
    return {key: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode()).hexdigest()
            for key, value in values.items()}
