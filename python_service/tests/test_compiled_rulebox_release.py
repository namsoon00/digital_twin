"""Release replacement keeps active decisions isolated until validation passes."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
from digital_twin.modules.reasoning.application.compiled_rulebox_release import CompiledRuleboxReleaseService
from digital_twin.modules.reasoning.domain.compiled_rulebox import compile_rulebox, COMPILED_RULEBOX_VERSION
from test_compiled_rulebox import rule


class CompiledReleaseTests(TestCase):
    def setup_service(self, validation=None):
        self.artifact={'rules':[rule()], 'ruleboxFingerprint':'new-rules', 'tboxFingerprint':'tbox'}
        self.program=compile_rulebox(self.artifact['rules'],tbox_fingerprint='tbox')
        self.control=SimpleNamespace(active_deployment_id='old',delivery_deployment_id='old',candidate_deployment_id='',version=7)
        self.platform=Mock()
        self.platform.registry.control.side_effect=lambda:self.control
        self.platform.registry.release_artifact.return_value={'valid':True,'artifact':{
            'ruleboxFingerprint':'old-rules','tboxFingerprint':'tbox',
            'releaseBundle':{'source_contract_versions':[COMPILED_RULEBOX_VERSION]}}}
        def register(target,*args,**kwargs):
            self.control=SimpleNamespace(active_deployment_id='old',delivery_deployment_id='old',candidate_deployment_id=target,version=8)
            return {'status':'registered'}
        self.platform.register_v2_release.side_effect=register
        selected=SimpleNamespace(to_dict=lambda:{'active_deployment_id':'new','delivery_deployment_id':'new'})
        self.platform.registry.switch_compiled_release.return_value=selected
        self.validator=Mock(return_value=validation or {'status':'passed','deliveryCount':0,'completeRuleCoverage':True,'programFingerprint':self.program.fingerprint})
        self.event=Mock()
        return CompiledRuleboxReleaseService(self.platform,self.validator,self.event)

    def test_verified_artifact_switches_once_after_zero_delivery_validation(self):
        service=self.setup_service()
        result=service.apply(self.artifact,expected_baseline='old',deployment_id='new')
        self.assertEqual('applied',result['status'])
        self.validator.assert_called_once()
        self.event.assert_called_once()
        self.platform.registry.switch_compiled_release.assert_called_once()
        args=self.platform.registry.switch_compiled_release.call_args
        self.assertEqual(('old','new'),args.args[:2])
        self.assertEqual(8,args.kwargs['expected_version'])

    def test_incomplete_or_mismatched_validation_keeps_old_delivery(self):
        for changes in ({'completeRuleCoverage':False},{'deliveryCount':1},{'programFingerprint':'another'}):
            service=self.setup_service();self.validator.return_value.update(changes)
            result=service.apply(self.artifact,expected_baseline='old',deployment_id='new')
            self.assertEqual('not-applied',result['status'])
            self.platform.registry.switch_compiled_release.assert_not_called()
            self.assertEqual('old',self.control.delivery_deployment_id)

    def test_concurrent_release_cannot_be_overwritten(self):
        service=self.setup_service();self.control.candidate_deployment_id='another-chat'
        result=service.apply(self.artifact,expected_baseline='old')
        self.assertEqual('blocked',result['status'])
        self.platform.register_v2_release.assert_not_called()

    def test_lost_commit_response_uses_authoritative_owner_without_undoing_release(self):
        service=self.setup_service()
        def switched_then_disconnected(*args,**kwargs):
            self.control=SimpleNamespace(active_deployment_id='new',delivery_deployment_id='new',
                candidate_deployment_id='',version=9,to_dict=lambda:{'active_deployment_id':'new'})
            raise OSError('connection lost after commit')
        self.platform.registry.switch_compiled_release.side_effect=switched_then_disconnected
        self.platform.supersede_inactive_pending_work.side_effect=OSError('cleanup unavailable')
        result=service.apply(self.artifact,expected_baseline='old',deployment_id='new')
        self.assertEqual('applied',result['status'])
        self.assertEqual('pending',result['queueCleanup']['status'])
        self.platform.registry.abandon_compiled_candidate.assert_not_called()

    def test_source_rule_deletion_is_new_program_and_repeated_save_is_noop(self):
        service=self.setup_service()
        baseline=self.platform.registry.release_artifact.return_value['artifact']
        baseline['ruleboxFingerprint']='new-rules'
        result=service.apply(self.artifact,expected_baseline='old')
        self.assertEqual('unchanged',result['status'])
        self.validator.assert_not_called()
        changed=deepcopy(self.artifact);changed['rules'][0]['enabled']=False
        changed['ruleboxFingerprint']='disabled-rules'
        other=compile_rulebox(changed['rules'],tbox_fingerprint='tbox')
        self.assertNotEqual(other.fingerprint,self.program.fingerprint)
        self.assertFalse(other.rule_order)

    def test_native_to_compiled_needs_explicit_initial_cutover(self):
        service=self.setup_service()
        self.platform.registry.release_artifact.return_value['artifact']['releaseBundle']['source_contract_versions']=['typedb-native']
        self.assertEqual('blocked',service.apply(self.artifact,expected_baseline='old')['status'])
        self.platform.register_v2_release.assert_not_called()

    def test_governed_save_validates_before_write_and_reports_unapplied_runtime(self):
        from digital_twin.modules.reasoning.infrastructure.graph_writes.rulebox_commands import save_rulebox
        from digital_twin.modules.reasoning.infrastructure.graph_writes.rulebox_commands_ports import SaveRuleboxBindings
        from static_seed_fixture import artifact
        store=SimpleNamespace(_last_rules=[],clear_rulebox_snapshot_cache=Mock(),
            rulebox_snapshot=Mock(return_value={'rules':[],'versions':[]}),
            append_rulebox_version=Mock(return_value={'saved':True}),
            seed_ontology=Mock(return_value={'saved':True,'status':'ok'}))
        store.rulebox_release_change_handler=Mock(side_effect=ValueError('unsupported condition'))
        payload={'rules':artifact()['rules']}
        bindings=SaveRuleboxBindings(utc_now=lambda:'2026-11-13T00:00:00Z')
        result=save_rulebox(store,payload,_bindings=bindings)
        self.assertFalse(result['saved']);store.seed_ontology.assert_not_called()
        self.assertEqual('invalid-runtime-rulebox',result['status'])
        store.rulebox_release_change_handler=Mock(side_effect=[{'status':'ready'},
            {'status':'not-applied','reason':'validation failed'}])
        result=save_rulebox(store,payload,_bindings=bindings)
        self.assertTrue(result['saved'])
        self.assertEqual('saved-runtime-not-applied',result['status'])
        self.assertEqual('validation failed',result['runtimeRelease']['reason'])
        self.assertEqual(['preflight','saved'],[c.args[0] for c in store.rulebox_release_change_handler.call_args_list])

    def test_read_status_detects_saved_rules_that_differ_from_active_release(self):
        from unittest.mock import patch
        from digital_twin.infrastructure.composition.compiled_rulebox_release import compiled_rulebox_runtime_status
        registry=Mock()
        registry.control.return_value=SimpleNamespace(active_deployment_id='active',candidate_deployment_id='')
        registry.get.return_value={'releaseBundle':{'source_contract_versions':[COMPILED_RULEBOX_VERSION]}}
        registry.release_artifact.return_value={'valid':True,'artifact':{'rules':[rule()]}}
        with patch('digital_twin.infrastructure.operational_store.reasoning_engine_registry_store',return_value=registry):
            self.assertEqual('current',compiled_rulebox_runtime_status({}, {'rules':[rule()]})['status'])
            changed=rule();changed['enabled']=False
            self.assertEqual('not-applied',compiled_rulebox_runtime_status({}, {'rules':[changed]})['status'])
            registry.release_artifact.return_value={'valid':False}
            self.assertEqual('unavailable',compiled_rulebox_runtime_status({}, {'rules':[rule()]})['status'])
