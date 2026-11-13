"""Compiled completion proof, evidence persistence and delivery boundaries."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from digital_twin.infrastructure.compiled_rulebox_projection import FrozenRuleboxRepository, CompiledPortfolioProjectionRecorder
from digital_twin.modules.reasoning.infrastructure.static_seed.artifact import ontology_release_seed_artifact
from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology, OntologyEntity
from digital_twin.modules.reasoning.domain.rule_execution_proof import compiled_rule_execution_completed, rule_notice_execution_proven
from digital_twin.modules.reasoning.application.independent_reasoning_engine import projection_inference_identity
from digital_twin.modules.reasoning.domain.ontology_inference_context import relation_contexts_from_snapshot
from digital_twin.modules.notifications.domain.relation_change import relation_change_snapshot
from test_ontology_projection_audit import source_snapshot


class MemoryAudit:
    def begin(self, run): self.run = run
    def complete_with_execution_trace(self, run, result): self.completed = run; self.result = deepcopy(result)


class CompiledProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifact = ontology_release_seed_artifact(default_graph_inference_rules())

    def recorder(self, store=None):
        repo = FrozenRuleboxRepository(self.artifact)
        recorder = CompiledPortfolioProjectionRecorder(repo, settings={}, projection_run_store=store or MemoryAudit(),
            frozen_rulebox_catalog=repo.rulebox_snapshot(), frozen_tbox_metadata=repo.active_tbox_metadata())
        facts=PortfolioOntology('test', entities=[OntologyEntity('stock:005930','삼성전자','stock',{
            'ontologyBox':'ABox','source':'holding','symbol':'005930','quantity':10,
            'currentPrice':70000,'averagePrice':80000,'profitLossRate':-12.5,
            'positionAccountWeight':0.8,'strategyMaxPositionWeightPct':45,
            'dataQuality':'actual','observedAt':'2026-07-20T00:01:00Z','sourceAsOf':'2026-07-20T00:01:00Z',
            'sourceFetchedAt':'2026-07-20T00:01:00Z','freshnessRequired':True,'maxAgeMinutes':10,
        })])
        recorder.build_graph_assembly=Mock(return_value=(facts,facts,{}))
        return recorder

    def test_source_and_result_are_durable_before_eligibility_without_native_flags(self):
        recorder=self.recorder(); snapshot=source_snapshot()
        result=recorder.record_snapshot(snapshot,['005930'])
        self.assertTrue(projection_inference_identity(result)['verified'])
        inference=result['inferenceBox']
        self.assertTrue(compiled_rule_execution_completed(inference))
        self.assertFalse(inference['nativeTypeDbReasoningCompleted'])
        self.assertFalse(inference['nativeTypeDbReasoningUsed'])
        self.assertEqual(inference,recorder.projection_run_store.completed.result_payload['compiledInferenceBox'])
        self.assertEqual(1,len(recorder.projection_run_store.run.context_payload['compiledSourceGraph']['entities']))
        self.assertGreater(inference['traceCount'],0)
        contexts=relation_contexts_from_snapshot(snapshot)
        self.assertIn('005930',contexts)
        packet=relation_change_snapshot({'ontologyRelationContext':contexts['005930']})
        self.assertTrue(packet['rules'])
        self.assertTrue(all(rule_notice_execution_proven(r) and not r['nativeProof'] for r in packet['rules']))

    def test_failed_durable_commit_cannot_publish_a_complete_projection(self):
        store=MemoryAudit(); store.complete_with_execution_trace=Mock(side_effect=OSError('disk unavailable'))
        recorder=self.recorder(store); snapshot=source_snapshot(); before=deepcopy(snapshot.metadata)
        with self.assertRaises(OSError): recorder.record_snapshot(snapshot,['005930'])
        self.assertEqual(before,snapshot.metadata)

    def test_tampered_coverage_scope_program_or_generation_blocks_delivery(self):
        result=self.recorder().record_snapshot(source_snapshot(),['005930'])
        for key,value in [('durable',False),('enabledRuleIds',[]),('accountId','another'),('worldId','another'),
                          ('generationId','old'),('sourceAboxSnapshotId','old'),('programFingerprint','old'),
                          ('enabledRuleIds',[{}]),('evaluatedRuleIds',[{}])]:
            bad=deepcopy(result);bad['inferenceBox']['compiledExecutionProof'][key]=value
            self.assertFalse(projection_inference_identity(bad)['verified'],key)
        bad=deepcopy(result);bad['inferenceBox']['compiledExecutionProof']['evaluatedRuleIds'].pop()
        self.assertFalse(projection_inference_identity(bad)['verified'])

    def test_account_worlds_and_generations_cannot_be_shared(self):
        a=self.recorder().record_snapshot(source_snapshot(),['005930'])['inferenceBox']
        b=self.recorder().record_snapshot(replace(source_snapshot(),account_id='other'),['005930'])['inferenceBox']
        self.assertNotEqual(a['worldId'],b['worldId'])
        self.assertNotEqual(a['inferenceGenerationId'],b['inferenceGenerationId'])

    def test_artifact_mutation_is_rejected(self):
        artifact=deepcopy(self.artifact);artifact['rules'][0]['label']='tampered'
        with self.assertRaises(ValueError): FrozenRuleboxRepository(artifact)
