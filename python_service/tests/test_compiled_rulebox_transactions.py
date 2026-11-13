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
