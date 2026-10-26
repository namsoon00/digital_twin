"""Regression checks inside the existing native management test budget."""

from copy import deepcopy
from types import SimpleNamespace


def resolution_fixture(rule_id='graph.position.concentration.guard.v1'):
    from test_rulebox_decision_brief import management_only_fixture
    from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
    from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology, OntologyEntity
    from digital_twin.modules.reasoning.domain.management_resolution import materialize_management_resolution
    from digital_twin.modules.reasoning.infrastructure.native_execution.policy_resolution import append_policy_resolutions
    from digital_twin.infrastructure.graph_store_inferencebox import inferencebox_trace_payload
    ctx = management_only_fixture()
    relation = ctx['ontologyRelationContext']
    facts = relation['facts']
    facts.update(positionAccountWeight=44, source='holding', sourceAsOf='2026-10-02T01:10:00Z',
                 freshnessStatus='fresh', freshnessAgeMinutes=0, judgementEvidenceUsable=True,
                 quoteSource='Fixture feed', maxAgeMinutes=10)
    rule = next(r for r in default_graph_inference_rules() if r.rule_id == rule_id)
    if rule_id == 'graph.instrument_profile.averaging_down_policy.v1':
        facts['profitLossRate'] = 0
    subject = OntologyEntity('stock:MSTR', '스트래티지', 'stock', {**facts, 'symbol': 'MSTR'})
    graph = PortfolioOntology('test-policy-clearance')
    graph.entities = [subject]
    queries = []
    def read(query, columns, **_kwargs):
        queries.append(query)
        return [{'sourceId': 'stock:MSTR', 'sourceLabel': '스트래티지'}]
    native = {'status': 'ok', 'matches': []}
    index = {'status': 'verified', 'index': {'sourceIdsBySymbol': {'MSTR': ['stock:MSTR']},
                                          'sourceStorageIdsBySourceId': {'stock:MSTR': 'exact-abox-source'}}}
    append_policy_resolutions(SimpleNamespace(read_rows=read), native, [rule], ['MSTR'], relation['worldId'], index, True)
    materialize_management_resolution(graph, rule, subject, native['matches'][0], {})
    trace = next(e for e in graph.entities if e.kind == 'inference-trace')
    payload = inferencebox_trace_payload({'id': trace.entity_id, 'symbol': 'MSTR', 'ruleId': rule.rule_id,
        'properties': trace.properties, 'nativeTypeDbReasoned': True})
    relation.update(matchedRules=[], activeRules=[], referenceRules=[], actionEnvelope={
        'blockedActions': [], 'allowedActions': [], 'inferenceEligibilityAssessments': []})
    relation['graphStoreInference'].update(traces=[payload], relations=[])
    return ctx, native, queries


def assert_policy_resolution_flow(test, checks):
    from digital_twin.modules.notifications.domain.relation_follow_up import delivery_snapshot
    from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
    from digital_twin.modules.decisions.contracts import decision_synthesis_from_relation_context
    from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
    from digital_twin.modules.notifications.domain.rule_dispatch_validity import rule_dispatch_validity
    from datetime import datetime, timezone
    _, original, _, _ = checks.engine_run()
    baseline = delivery_snapshot(original.job.context['relationChangeEvidence'], 'original-policy', original.job.account_id)
    baseline.update(receiptJobId='original-policy', deliveredAt='2026-10-02T01:10:01Z')
    ctx, native, queries = resolution_fixture()
    test.assertEqual(1, len(queries))
    test.assertIn('exact-abox-source', queries[0])
    test.assertIn('<=', queries[0])
    # Missing cost/holdings values represented as zero do not prove recovery.
    from digital_twin.modules.reasoning.domain.management_resolution import materialize_management_resolution
    from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology, OntologyEntity
    from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
    authored = next(r for r in default_graph_inference_rules() if r.rule_id == 'graph.position.concentration.guard.v1')
    for field in ('quantity', 'currentPrice', 'positionAccountWeight'):
        graph = PortfolioOntology('missing-source')
        subject = OntologyEntity('stock:MSTR', '스트래티지', 'stock', {**ctx['ontologyRelationContext']['facts'], field: 0})
        materialize_management_resolution(graph, authored, subject, native['matches'][0], {})
        test.assertEqual([], graph.entities, field)
    test.assertTrue(native['matches'][0]['managementResolution']['matched'])
    synthesis = decision_synthesis_from_relation_context(ctx['accountId'], ctx['ontologyRelationContext'])
    test.assertEqual('POLICY_RESOLUTION_VERIFIED', synthesis.management_decision['status'])
    test.assertEqual([], synthesis.management_decision['responses'])
    test.assertFalse(synthesis.management_decision['orderAuthorized'])
    # A trace-only native generation must survive the real relation assembler.
    from digital_twin.modules.reasoning.domain.ontology_inference_context import relation_context_from_inferencebox
    from digital_twin.modules.portfolio.contracts import Position, PortfolioSummary
    rel = ctx['ontologyRelationContext']
    box = {**rel['graphStoreInference'], 'status': 'ok', 'graphStore': 'typedb',
           'nativeTypeDbReasoningUsed': True, 'generationAligned': True, 'worldId': rel['worldId']}
    position = Position('MSTR', '스트래티지', market='US', currency='USD', quantity=44,
                        current_price=100, market_value_krw=4400, source_as_of='2026-10-02T01:10:00Z')
    assembled = relation_context_from_inferencebox(position, PortfolioSummary(10000,4400,5600,[],[],44),
        box, source='holding', account_id=ctx['accountId'], account_context={'investmentStrategy': {'profile': 'aggressive'}})
    test.assertTrue(decision_synthesis_from_relation_context(ctx['accountId'], assembled).management_decision.get('resolutions'))
    result, queue, ai, _ = checks.engine_run(context=ctx, baseline=baseline)
    test.assertEqual(1, len(result.candidate_events))
    test.assertTrue(queue.admission.accepted, queue.job.last_error)
    packet = queue.job.context['relationChangeEvidence']
    test.assertEqual('released', packet['customerEvents'][0]['policyState'])
    test.assertEqual('objective-met', packet['policyLifecycle']['transitions'][0]['cause'])
    test.assertEqual('original-policy', packet['policyLifecycle']['baselineJobId'])
    test.assertEqual('original-policy', packet['followUp']['parentJobId'])
    test.assertTrue(rule_dispatch_validity(packet, now=datetime(2026,10,2,1,11,tzinfo=timezone.utc))['allowed'])
    body = NotificationRenderingService().render(queue.job)
    test.assertIn('비중 조정 요구 해제', body)
    test.assertIn('78.45%', body)
    test.assertIn('44%', body)
    test.assertIn('해제 기준 45% 이하', body)
    test.assertIn('비중 조정 요구 해제', queue.job.context['customerInvestmentDetailDocument']['lead'])
    test.assertLessEqual(len([line for line in body.splitlines() if line.strip()]), 9)
    ai.enqueue.assert_not_called()
    checks.assert_policy_only_candidate_survives_worker_gates_and_records_transport_receipt(context=ctx, baseline=baseline)
    delivered = delivery_snapshot(packet, 'release-policy', queue.job.account_id)
    delivered.update(receiptJobId='release-policy', deliveredAt='2026-10-02T01:11:00Z')
    test.assertEqual(0, len(checks.engine_run(context=ctx, baseline=delivered)[0].candidate_events))
    # Reactivation is a real transition even though its policy goal is unchanged.
    _, reapplied, _, _ = checks.engine_run(baseline=delivered)
    test.assertTrue(reapplied.admission.accepted)
    test.assertEqual('applied', reapplied.job.context['relationChangeEvidence']['customerEvents'][0]['policyState'])
    test.assertEqual('policy-reapplied', reapplied.job.context['relationChangeEvidence']['policyLifecycle']['transitions'][0]['cause'])
    # No receipt, foreign scope, stale evidence, missing or forged inverse proof.
    test.assertEqual(0, len(checks.engine_run(context=ctx)[0].candidate_events))
    for variant in ('stale', 'native', 'inverse', 'world'):
        broken = deepcopy(ctx)
        trace = broken['ontologyRelationContext']['graphStoreInference']['traces'][0]
        if variant == 'stale':
            trace['freshnessStatus'] = 'stale'
        elif variant == 'native':
            trace['nativeTypeDbReasoned'] = False
        elif variant == 'inverse':
            trace['matchedConditions'][1]['resolutionMatchedByTypeDB'] = False
        else:
            broken['ontologyRelationContext']['graphStoreInference']['worldId'] = 'foreign'
        test.assertEqual(0, len(checks.engine_run(context=broken, baseline=baseline)[0].candidate_events), variant)
    # A missing rule is not proof of release.
    missing = deepcopy(ctx)
    missing['ontologyRelationContext']['graphStoreInference']['traces'] = []
    test.assertEqual(0, len(checks.engine_run(context=missing, baseline=baseline)[0].candidate_events))
    # A clearance alongside another native restriction must not say all clear.
    from test_rulebox_decision_brief import management_only_fixture
    from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
    def add_loss_policy(context):
        rel = context['ontologyRelationContext']
        rule = next(r for r in default_graph_inference_rules() if r.rule_id == 'graph.instrument_profile.averaging_down_policy.v1')
        response = deepcopy(rule.derivations[0].management_response)
        rel['facts']['profitLossRate'] = -3.2
        response['boundFacts'] = {'profitLossRate': -3.2}
        row = {'rule_id': rule.rule_id, 'ruleId': rule.rule_id, 'matched': True,
               'claimContract': rule.resolved_claim_contract.to_dict(),
               'knowledgeBasis': rule.resolved_knowledge_basis.to_dict(),
               'blockedActions': ['ADD'], 'managementResponse': response,
               'matchedConditions': [{'conditionId': 'holding-loss', 'field': 'profitLossRate', 'operator': '<',
                   'value': 0, 'observedValue': -3.2, 'matched': True, 'matchedByTypeDB': True,
                   'judgementEvidenceUsable': True, 'freshnessStatus': 'fresh', 'observedAt': '2026-10-02T01:10:00Z'}]}
        rel['matchedRules'].append(row)
        rel['graphStoreInference']['traces'].append(deepcopy(row))
        rel['actionEnvelope']['blockedActions'] = ['ADD']
        rel['actionEnvelope']['inferenceEligibilityAssessments'].append({'ruleId': rule.rule_id,
            'status': 'eligible', 'freshnessStatus': 'fresh', 'evidenceUsableForJudgement': True})
    combined = management_only_fixture()
    add_loss_policy(combined)
    _, both, _, _ = checks.engine_run(context=combined)
    both_baseline = delivery_snapshot(both.job.context['relationChangeEvidence'], 'both-policies', both.job.account_id)
    both_baseline.update(receiptJobId='both-policies', deliveredAt='2026-10-02T01:10:01Z')
    partial, _, _ = resolution_fixture()
    add_loss_policy(partial)
    _, left, _, _ = checks.engine_run(context=partial, baseline=both_baseline)
    test.assertTrue(left.admission.accepted)
    body = NotificationRenderingService().render(left.job)
    test.assertIn('비중 조정 요구 해제', body)
    test.assertIn('손실 구간 추가매수 제한은 계속 적용', body)
    test.assertEqual(1, len(left.job.context['relationChangeEvidence']['policyLifecycle']['remainingRestrictions']))
    test.assertEqual(['ADD'], left.job.context['relationChangeEvidence']['current']['decisionBrief']['blockedActions'])
    _, uncertain, _, _ = checks.engine_run(context=ctx, baseline=both_baseline)
    test.assertIn('다른 기존 제한의 해제는 아직 확인되지', NotificationRenderingService().render(uncertain.job))
    # A later receipt with absent/stale evidence must retain the original policy lineage.
    later = deepcopy(both_baseline)
    later['decisionBrief']['restrictions'] = []
    later['rules'] = []
    test.assertTrue(any(e.get('policyState') == 'released' for e in relation_change_evidence(
        left.job.context, later)['customerEvents']))
    loss_active = management_only_fixture()
    lr = loss_active['ontologyRelationContext']
    lr.update(matchedRules=[], activeRules=[])
    lr['graphStoreInference'].update(traces=[], relations=[])
    lr['actionEnvelope']['inferenceEligibilityAssessments'] = []
    add_loss_policy(loss_active)
    _, loss_queue, _, _ = checks.engine_run(context=loss_active)
    loss_baseline = delivery_snapshot(loss_queue.job.context['relationChangeEvidence'], 'loss-policy', loss_queue.job.account_id)
    loss_baseline.update(receiptJobId='loss-policy', deliveredAt='2026-10-02T01:10:01Z')
    recovery, recovery_native, _ = resolution_fixture('graph.instrument_profile.averaging_down_policy.v1')
    _, recovered, _, _ = checks.engine_run(context=recovery, baseline=loss_baseline)
    test.assertTrue(recovered.admission.accepted)
    text = NotificationRenderingService().render(recovered.job)
    test.assertIn('손실 구간 추가매수 제한 해제', text)
    test.assertIn('해제 기준 0% 이상', text)
    from rule_notice_evidence_checks import assert_policy_cause_survives_composition
    assert_policy_cause_survives_composition(test, recovered.job.context['relationChangeEvidence'],
                                           original.job.context['relationChangeEvidence'])
    graph = PortfolioOntology('missing-cost')
    subject = OntologyEntity('stock:MSTR', '스트래티지', 'stock', {**recovery['ontologyRelationContext']['facts'], 'averagePrice': 0})
    rule = next(r for r in default_graph_inference_rules() if r.rule_id == 'graph.instrument_profile.averaging_down_policy.v1')
    materialize_management_resolution(graph, rule, subject, recovery_native['matches'][0], {})
    test.assertEqual([], graph.entities)
