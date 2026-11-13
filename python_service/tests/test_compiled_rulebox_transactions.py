"""Real isolated MySQL cutover, rollback and concurrent-owner contracts."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from stabilization_database import StabilizationDatabaseCase
from digital_twin.modules.reasoning.domain.compiled_rulebox import COMPILED_RULEBOX_VERSION, compile_rulebox
from digital_twin.modules.reasoning.domain.reasoning_engine_versions import EngineReleaseBundle, ReasoningEngineDescriptor
from digital_twin.modules.reasoning.infrastructure.static_seed.artifact import ontology_release_seed_artifact
from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
from digital_twin.infrastructure.mysql_operational_connection import MySQLConnectionProxy
from digital_twin.modules.reasoning.infrastructure.mysql_ontology_projection_runs import MySQLOntologyProjectionRunStore


class CompiledReleaseTransactionTests(StabilizationDatabaseCase):
    def candidate(self):
        target=self.deployment+'-compiled'
        bundle=EngineReleaseBundle('tbox','rulebox','prompt','features',
                                   source_contract_versions=(COMPILED_RULEBOX_VERSION,))
        self.registry.upsert(ReasoningEngineDescriptor('fixture','v2',target,'candidate',
            'compiled-fixture','test-fixture',bundle))
        artifact=ontology_release_seed_artifact(default_graph_inference_rules(),release_bundle=bundle.to_dict())
        self.registry.save_release_artifact(target,artifact)
        control=self.registry.set_control(self.deployment,self.deployment,target)
        program=compile_rulebox(artifact['rules'],tbox_fingerprint=artifact['tboxFingerprint'])
        receipt={'version':COMPILED_RULEBOX_VERSION,'sourceRuleboxFingerprint':artifact['ruleboxFingerprint'],
            'programFingerprint':program.fingerprint,'validation':{'status':'passed','deliveryCount':0,
                'completeRuleCoverage':True,'programFingerprint':program.fingerprint}}
        return target,receipt,control.version

    def test_atomic_selection_and_duplicate_attempt_cannot_rewrite_ownership(self):
        target,receipt,version=self.candidate()
        selected=self.registry.switch_compiled_release(self.deployment,target,receipt,expected_version=version)
        self.assertEqual(target,selected.active_deployment_id)
        self.assertEqual(target,self.registry.control().delivery_deployment_id)
        self.assertEqual('',self.registry.control().candidate_deployment_id)
        self.assertEqual('retired',self.registry.get(self.deployment)['status'])
        with self.assertRaises(RuntimeError):
            self.registry.switch_compiled_release(self.deployment,target,receipt,expected_version=version)
        self.assertEqual(target,self.registry.control().delivery_deployment_id)

    def test_failure_before_pointer_write_rolls_back_both_deployment_changes(self):
        target,receipt,version=self.candidate()
        original=MySQLConnectionProxy.execute
        def fail(connection,sql,params=None):
            if sql.startswith('UPDATE reasoning_engine_control SET active_deployment_id'):
                raise OSError('fixture interrupted cutover')
            return original(connection,sql,params)
        with patch.object(MySQLConnectionProxy,'execute',fail), self.assertRaises(OSError):
            self.registry.switch_compiled_release(self.deployment,target,receipt,expected_version=version)
        self.assertEqual(self.deployment,self.registry.control().delivery_deployment_id)
        self.assertEqual('active',self.registry.get(self.deployment)['status'])
        self.assertEqual('candidate',self.registry.get(target)['status'])

    def test_validation_is_bound_to_exact_program_and_failed_slot_can_be_reused(self):
        target,receipt,version=self.candidate()
        tampered=deepcopy(receipt)
        tampered['programFingerprint']=tampered['validation']['programFingerprint']='different'
        with self.assertRaises(RuntimeError):
            self.registry.switch_compiled_release(self.deployment,target,tampered,expected_version=version)
        self.assertTrue(self.registry.abandon_compiled_candidate(self.deployment,target,{'reason':'fixture'}))
        self.assertEqual('',self.registry.control().candidate_deployment_id)
        self.assertEqual(self.deployment,self.registry.control().delivery_deployment_id)
        self.assertEqual('retired',self.registry.get(target)['status'])

    def test_failed_validation_cannot_clear_another_sessions_candidate(self):
        target,receipt,version=self.candidate()
        self.registry.upsert(ReasoningEngineDescriptor('fixture','v2','other-session','candidate',
            'other-store','test-fixture',EngineReleaseBundle('tbox','rulebox','prompt','features')))
        self.registry.set_control(self.deployment,self.deployment,'other-session')
        self.assertFalse(self.registry.abandon_compiled_candidate(self.deployment,target,{'reason':'fixture'}))
        self.assertEqual('other-session',self.registry.control().candidate_deployment_id)
        with self.assertRaises(RuntimeError):
            self.registry.switch_compiled_release(self.deployment,target,receipt,expected_version=version)


class CompiledAuditTransactionTests(StabilizationDatabaseCase):
    def setUp(self):
        super().setUp()
        self.audit = MySQLOntologyProjectionRunStore(self.settings)
        with self.audit.transaction() as connection:
            connection.execute('DELETE FROM ontology_projection_trace_outbox')

    def fixture(self, account=None):
        import test_compiled_rulebox_projection as fixtures
        fixtures.CompiledProjectionTests.setUpClass()
        recorder = fixtures.CompiledProjectionTests().recorder(self.audit)
        return recorder, replace(fixtures.source_snapshot(), account_id=account or self.deployment)

    def test_facts_result_and_pending_detail_commit_together_before_delivery(self):
        from digital_twin.modules.reasoning.domain.rule_execution_proof import compiled_rule_execution_completed
        recorder, snapshot = self.fixture()
        result = recorder.record_snapshot(snapshot, ['005930'])
        run_id = result['projectionAudit']['runId']
        rows = self.audit.latest(account_id=self.deployment, limit=1)
        self.assertTrue(compiled_rule_execution_completed(rows[0]['result']['compiledInferenceBox']))
        self.assertTrue(rows[0]['context']['compiledSourceGraph']['entities'])
        with self.audit.connect() as connection:
            count = connection.execute('SELECT COUNT(*) AS n FROM ontology_reasoning_run_stages WHERE run_id=%s', (run_id,)).fetchone()['n']
        self.assertEqual(0, count)
        self.assertEqual({'pending': 1}, self.audit.compiled_trace_summary())
        # A new process can finish the exact receipt without running rules again.
        restarted = MySQLOntologyProjectionRunStore(self.settings)
        completed = restarted.materialize_compiled_trace()
        self.assertEqual('completed', completed['status'])
        self.assertEqual(run_id, completed['runId'])
        self.assertGreater(completed['ruleCount'], 0)
        self.assertEqual('idle', restarted.materialize_compiled_trace()['status'])
        self.assertEqual({}, restarted.compiled_trace_summary())

    def test_outbox_insert_failure_rolls_back_evidence_and_cannot_publish(self):
        recorder, snapshot = self.fixture()
        before = deepcopy(snapshot.metadata)
        original = MySQLConnectionProxy.execute
        def fail(connection, sql, params=None):
            if sql.startswith('INSERT INTO ontology_projection_trace_outbox'):
                raise OSError('fixture interrupted atomic audit')
            return original(connection, sql, params)
        with patch.object(MySQLConnectionProxy, 'execute', fail), self.assertRaises(OSError):
            recorder.record_snapshot(snapshot, ['005930'])
        self.assertEqual(before, snapshot.metadata)
        self.assertEqual([], self.audit.latest(account_id=self.deployment))
        self.assertEqual({}, self.audit.compiled_trace_summary())

    def test_pending_detail_preserves_source_and_retry_does_not_duplicate_delivery(self):
        from digital_twin.infrastructure.mysql_retention import _delete_stale_projection_runs
        recorder, snapshot = self.fixture()
        result = recorder.record_snapshot(snapshot, ['005930'])
        run_id = result['projectionAudit']['runId']
        with self.audit.transaction() as connection:
            _delete_stale_projection_runs(connection, '2099-01-01T00:00:00Z', 100)
        self.assertEqual(run_id, self.audit.latest(account_id=self.deployment)[0]['runId'])
        with patch.object(self.audit, '_replace_execution_trace_with_connection', side_effect=OSError('fixture detail interruption')):
            self.assertEqual('retry', self.audit.materialize_compiled_trace()['status'])
        with self.audit.transaction() as connection:
            connection.execute("UPDATE ontology_projection_trace_outbox SET available_at='2000-01-01T00:00:00Z' WHERE run_id=%s", (run_id,))
        self.assertEqual('completed', self.audit.materialize_compiled_trace()['status'])
        with self.audit.connect() as connection:
            notices = connection.execute('SELECT COUNT(*) AS n FROM notification_jobs WHERE account_id=%s', (self.deployment,)).fetchone()['n']
        self.assertEqual(0, notices)
