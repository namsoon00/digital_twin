"""Strategy definitions, native phase proofs and durable research boundaries."""
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.modules.model_registry.domain.ontology_rulebox_contracts import GraphInferenceRule
from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import rulebox_semantic_violations, rule_change_candidates_from_text, build_rule_change_candidate_prompt
from digital_twin.modules.model_registry.domain.rule_strategy_contract import strategy_authoring_catalog, strategy_from_design, parse_strategy_contract, PHASES
from digital_twin.modules.model_registry.domain.rule_strategy_compilation import compile_strategy_rules, assemble_strategy_design
from digital_twin.modules.decisions.domain.investment_reasoning.strategy_observations import native_strategy_observations
from stabilization_database import StabilizationDatabaseCase


@lru_cache(maxsize=1)
def catalog():
    return strategy_authoring_catalog({'rules': [r.to_dict() for r in default_graph_inference_rules()]})


def design():
    return {'market': 'KR', 'maximumHoldingSessions': 3, 'signalValiditySeconds': 180, 'phases': {
        'setup': ['graph.temporal.decline_deceleration.defense.v1'],
        'entry': ['graph.price.reclaim.thesis_support.v1', 'graph.temporal.intraday_reversal.defense.v1'],
        'invalidation': ['graph.price.rebound.failure.v1'],
        'exit': ['graph.temporal.intraday_downside_acceleration.risk.v1']}}


def strategy():
    return strategy_from_design(design(), catalog(), '가격 회복과 장중 반전 전략 연구')


def live_phase_snapshot(phases):
    result = {'accountId': 'test', 'symbol': 'T', 'market': 'KR', 'worldId': 'world:test',
              'inferenceGenerationId': 'g', 'sourceAboxSnapshotId': 'a', 'proofIdentityVerified': True,
              'observedAt': '2026-10-23T01:00:00Z', 'rules': []}
    for r in default_graph_inference_rules():
        claim = r.resolved_claim_contract
        if not claim.strategy_contract or claim.strategy_contract['market'] != 'KR' or claim.strategy_phase not in phases:
            continue
        result['rules'].append({'id': r.rule_id, 'nativeProof': True, 'matched': True,
            'claimContract': claim.to_dict(), 'nextChecks': r.derivations[0].next_checks,
            'conditions': [{'conditionId': c.condition_id, 'matchedByTypeDB': True,
                'judgementEvidenceUsable': True, 'freshnessStatus': 'fresh' if c.relation_type == 'HAS_MODEL_SIGNAL' else 'not-applicable',
                'freshnessRequired': c.relation_type == 'HAS_MODEL_SIGNAL', 'observedAt': '2026-10-23T01:00:00Z',
                'maxAgeMinutes': 10} for c in r.conditions]})
    return result


class RuleStrategyContractTests(TestCase):
    def test_strategy_preserves_legacy_release_bytes_and_freezes_every_phase_model(self):
        for rule in default_graph_inference_rules():
            if rule.resolved_claim_contract.strategy_contract:
                continue  # Operational V2 is an additive release; legacy rows retain their shape.
            original = rule.to_dict()
            self.assertNotIn('strategyContract', original['claim_contract'])
            self.assertTrue(all('strategy_response' not in d for d in original['derivations']))
            self.assertEqual(rule.resolved_claim_contract.rule_fingerprint,
                             GraphInferenceRule.from_dict(original).resolved_claim_contract.rule_fingerprint)
        from test_rule_notification_intent import frozen_notification_claim
        from digital_twin.modules.model_registry.domain.rule_claim_contract import RuleClaimContract
        legacy = frozen_notification_claim()
        self.assertEqual(legacy, RuleClaimContract.from_dict(legacy).to_dict())
        contract = strategy()
        self.assertEqual(set(PHASES), set(contract['phases']))
        self.assertEqual('research-only', contract['mode'])
        self.assertNotIn('graph.market_proxy.relative_resilience.support.v1', catalog()['models'])
        source = deepcopy(catalog())
        setup_id = design()['phases']['setup'][0]
        source['models'][setup_id]['condition']['target_property_filters']['strengthBand'] = 'weak'
        self.assertNotEqual(contract, strategy_from_design(design(), source, contract['label']))
        changed = deepcopy(contract)
        changed['maximumHoldingSessions'] = 1
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            parse_strategy_contract(changed)
        self.assertEqual('strong', contract['models'][setup_id]['condition']['target_property_filters']['strengthBand'])

    def test_strategy_rejects_missing_phases_unknown_models_and_unearned_authority(self):
        for kind in ['phase', 'unknown', 'duplicate', 'duration', 'entry-exit', 'authority']:
            value = design()
            if kind == 'phase': value['phases'].pop('exit')
            elif kind == 'unknown': value['phases']['entry'] = ['invented-flow-model']
            elif kind == 'duplicate': value['phases']['entry'] *= 2
            elif kind == 'duration': value['maximumHoldingSessions'] = True
            elif kind == 'entry-exit': value['phases']['exit'] = list(reversed(value['phases']['entry']))
            else: value['mode'] = 'live'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                strategy_from_design(value, catalog(), 'fixture')

    def test_strategy_native_rulebox_roundtrip_keeps_contract_and_phase_proof(self):
        from digital_twin.infrastructure import typedb_ontology as api
        from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules, build_rulebox_rules_from_rows
        from digital_twin.modules.reasoning.infrastructure.typeql import typedb_native_match_query
        from digital_twin.modules.reasoning.infrastructure.typeql.profiles import typedb_native_rule_profile
        rules = compile_strategy_rules(strategy())
        self.assertEqual([], rulebox_semantic_violations([replace(r, enabled=True) for r in rules]))
        graph = rulebox_graph_from_rules(rules, include_tbox=False)
        self.assertEqual(1, sum(e.kind == 'strategy-contract' for e in graph.entities))
        with patch.object(api, 'runtime_settings', return_value={}):
            repository = api.TypeDBOntologyGraphRepository('fixture.invalid', retry_count=0)
        rows = {}
        for entity in graph.entities:
            rows.setdefault(entity.kind, []).append(repository.entity_row_from_typeql({
                'id': entity.entity_id, 'kind': entity.kind, 'ruleId': entity.properties.get('ruleId'),
                'json': json.dumps(entity.properties)}, 'RuleBox'))
        restored = build_rulebox_rules_from_rows(rows['rule'], rows['rule-condition'], rows['relation-template'])
        self.assertEqual(4, len(restored))
        for rule in restored:
            self.assertEqual('ready', typedb_native_rule_profile(rule.to_dict())['status'])
            original = next(r for r in rules if r.rule_id == rule.rule_id)
            self.assertEqual(original.resolved_claim_contract.strategy_contract, rule.resolved_claim_contract.strategy_contract)
            self.assertEqual(original.derivations[0].strategy_response, rule.derivations[0].strategy_response)
            query = typedb_native_match_query(rule.to_dict(), ['TEST'], scoped_manifest_only=True, world_id='world:test')['query']
            self.assertIn('ontology-hypothesis-contract-id', query)
            self.assertIn('180', query)
            self.assertIn('regular', query)
            self.assertEqual('TRADED_IN', rule.conditions[0].relation_type)
            self.assertIn('ontology-id "market:KR"', query)
            self.assertTrue(all(c.target_property_filters.get('marketSession') == 'regular'
                                for c in rule.conditions if c.relation_type == 'HAS_MODEL_SIGNAL'))
            self.assertFalse(rule.derivations[0].candidate_action)

    def test_strategy_governance_blocks_orphan_phases_threshold_edits_and_trade_actions(self):
        rules = [replace(r, enabled=True) for r in compile_strategy_rules(strategy())]
        self.assertTrue(any('four-phase' in x for x in rulebox_semantic_violations(rules[:1])))
        changed = list(rules)
        changed[0] = replace(changed[0], conditions=[replace(changed[0].conditions[0], target_property_filters={'entityId':'market:US'}), *changed[0].conditions[1:]])
        self.assertTrue(any('predicates differ' in x for x in rulebox_semantic_violations(changed)))
        changed = list(rules)
        changed[0] = replace(changed[0], derivations=[replace(changed[0].derivations[0], candidate_action='BUY')])
        self.assertTrue(any('cannot grant actions' in x for x in rulebox_semantic_violations(changed)))

    def test_strategy_native_observations_preserve_unknown_conflicts_and_scope(self):
        identity = dict(accountId='account', symbol='TEST', worldId='world:test', inferenceGenerationId='generation', sourceAboxSnapshotId='abox')
        relation = {'graphStoreInference': {**identity, 'traces': []}, 'matchedRules': []}
        rules = compile_strategy_rules(strategy())
        for phase in ['entry', 'invalidation']:
            rule = next(r for r in rules if r.resolved_claim_contract.strategy_phase == phase)
            relation['matchedRules'].append({'ruleId': rule.rule_id, 'matched': True, 'strategyResponse': rule.derivations[0].strategy_response})
            relation['graphStoreInference']['traces'].append({'ruleId': rule.rule_id, 'matched': True, 'matchedConditions': [
                {'conditionId': c.condition_id, 'matchedByTypeDB': True, 'judgementEvidenceUsable': True, 'freshnessStatus': 'fresh'} for c in rule.conditions]})
        result = native_strategy_observations(identity, relation)[0]
        self.assertEqual('conflicting-conditions', result['status'])
        self.assertEqual(['exit', 'setup'], result['unknownPhases'])
        self.assertIsNone(result['selectedMarketAction'])
        relation['graphStoreInference']['traces'][1]['matchedConditions'][0]['freshnessStatus'] = 'stale'
        result = native_strategy_observations(identity, relation)[0]
        self.assertEqual(['entry'], result['confirmedPhases'])
        self.assertIn('invalidation', result['unknownPhases'])
        relation['graphStoreInference']['sourceAboxSnapshotId'] = 'other'
        self.assertEqual([], native_strategy_observations(identity, relation))
        self.assertEqual([], native_strategy_observations({}, {'graphStoreInference': {}}))

    def test_operational_strategy_release_owns_market_clock_schema_and_complete_bundle(self):
        from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import typedb_rule_schema_capability_contract
        from digital_twin.modules.reasoning.domain.projection_facts import graph_for_graph_store_persistence
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology, OntologyEntity, OntologyRelation
        rules = [r for r in default_graph_inference_rules() if r.resolved_claim_contract.strategy_contract]
        self.assertEqual(8, len(rules))
        self.assertTrue(all(r.enabled for r in rules))
        self.assertEqual([], rulebox_semantic_violations(rules))
        self.assertEqual({'KR', 'US'}, {r.resolved_claim_contract.strategy_contract['market'] for r in rules})
        attrs = typedb_rule_schema_capability_contract()['contextAttributes']['observation-data']
        self.assertIn('ontology-model-market-session', attrs)
        self.assertIn('ontology-model-source-age-seconds', attrs)
        graph = PortfolioOntology('test', entities=[OntologyEntity('stock:T', 'T', 'stock', {'symbol': 'T'}),
            OntologyEntity('market:KR', 'KR', 'market', {})],
            relations=[OntologyRelation('stock:T', 'market:KR', 'TRADED_IN')])
        projected = graph_for_graph_store_persistence(graph, {'rules': [r.to_dict() for r in rules],
            'inputRelationTypes': ['TRADED_IN', 'HAS_MODEL_SIGNAL']})
        self.assertEqual(['TRADED_IN'], [r.relation_type for r in projected.relations])
        from digital_twin.modules.model_registry.domain.operational_strategies import operational_strategy_rules
        from digital_twin.infrastructure.graph_store_lifecycle import ontology_release_seed_artifact
        from digital_twin.modules.reasoning.domain.composition_upgrade_review import declared_composition_additions
        original = [r for r in default_graph_inference_rules() if not r.resolved_claim_contract.strategy_contract]
        binding = {'deploymentId': 'old', 'graphStoreBinding': 'old-db'}
        extra = operational_strategy_rules(original, binding)
        before, after = [ontology_release_seed_artifact(rows) for rows in (original, original + extra)]
        declaration = {'version': 'native-strategy-phase-migration-v1', 'sourceDeployment': binding,
            'baselineDeploymentId': 'old', 'tboxUnchanged': True, 'addedRuleIds': [r.rule_id for r in extra]}
        self.assertEqual(set(declaration['addedRuleIds']), declared_composition_additions(before, after, declaration))
        from digital_twin.infrastructure.composition.strategy_release import operational_strategy_release_artifact
        from digital_twin.modules.model_registry.domain.ontology_rule_manifest import validate_rule_domain_manifests
        self.assertTrue(validate_rule_domain_manifests(default_graph_inference_rules())['valid'])
        broken = replace(rules[0], rule_id='graph.strategy.unregistered.setup.v2')
        self.assertIn(broken.rule_id, validate_rule_domain_manifests(original + [broken])['ownership']['missingRuleIds'])
        # Real predecessors retain raw serialization and their older registry
        # metadata, even when today's default catalog has a newer policy/TBox.
        before['rules'][0]['domain_manifest']['outcomeHorizonMinutes'] = 0
        registry = next(n for n in before['graph']['entities'] if n['kind'] == 'threshold-policy')
        registry['properties']['policyVersion'] = 'frozen-predecessor-policy'
        record = {'status': 'ready', 'valid': True, 'deploymentId': 'old', 'artifact': before,
                  'artifactFingerprint': 'old-artifact', 'ruleboxFingerprint': before['ruleboxFingerprint']}
        preserved = operational_strategy_release_artifact(record, binding)
        self.assertEqual(before['rules'], preserved['rules'][:len(original)])
        self.assertEqual(8, len(declared_composition_additions(before, preserved, preserved['compositionUpgradeContract'])))
        bad = deepcopy(after)
        bad['rules'][0]['enabled'] = not bad['rules'][0]['enabled']
        with self.assertRaisesRegex(ValueError, 'existing-rules-changed'):
            declared_composition_additions(before, bad, declaration)

    def test_operational_strategy_notice_proves_every_phase_and_preserves_receipt_novelty(self):
        from digital_twin.modules.notifications.domain.rule_strategy_event import strategy_phase_events, strategy_summary
        from digital_twin.modules.notifications.domain.rule_notification_event import carry_intent_receipts
        from digital_twin.modules.notifications.domain.rule_dispatch_validity import rule_dispatch_validity
        from datetime import datetime, timezone
        current = live_phase_snapshot(['entry', 'invalidation'])
        events = strategy_phase_events(current, {})
        self.assertEqual(1, len(events))
        self.assertTrue(events[0]['conflictingConditions'])
        self.assertEqual(2, len(events[0]['sourceRuleIds']))
        packet = {'current': current, 'customerEvents': events}
        check = rule_dispatch_validity(packet, now=datetime(2026, 10, 23, 1, 5, tzinfo=timezone.utc))
        self.assertTrue(check['allowed'], check)
        check = rule_dispatch_validity(packet, now=datetime(2026, 10, 23, 1, 20, tzinfo=timezone.utc))
        self.assertFalse(check['allowed'])
        summary = strategy_summary(packet, '가격 100원', '장중 가격 99원 → 100원')
        self.assertIn('동시에', summary['lead'])
        self.assertLess(len(json.dumps(summary, ensure_ascii=False)), 2000)
        prior = deepcopy(current)
        carry_intent_receipts(prior, {}, events, 'receipt')
        prior.update(receiptJobId='receipt', deliveredAt='2026-10-23T01:05:00Z')
        self.assertEqual([], strategy_phase_events(current, prior))
        self.assertEqual([], strategy_phase_events(live_phase_snapshot(['entry']), prior))
        new = strategy_phase_events(live_phase_snapshot(['exit']), prior)
        self.assertEqual(['exit'], new[0]['addedStrategyPhases'])
        for key, value in [('nativeProof', False), ('matched', False)]:
            changed = live_phase_snapshot(['entry'])
            changed['rules'][0][key] = value
            self.assertEqual([], strategy_phase_events(changed, {}))
        missing = live_phase_snapshot(['entry'])
        missing['rules'][0]['conditions'].pop()
        self.assertEqual([], strategy_phase_events(missing, {}))
        stale = live_phase_snapshot(['entry'])
        stale['rules'][0]['conditions'][-1]['freshnessStatus'] = 'stale'
        self.assertEqual([], strategy_phase_events(stale, {}))
        wrong = live_phase_snapshot(['entry'])
        wrong['market'] = 'US'
        self.assertEqual([], strategy_phase_events(wrong, {}))

    def test_operational_strategy_context_can_publish_without_ai_or_predictive_hypothesis(self):
        from digital_twin.modules.notifications.domain.rule_context_event import native_context_rule_ids
        current = live_phase_snapshot(['setup'])
        rule = next(r for r in default_graph_inference_rules() if r.rule_id == current['rules'][0]['id'])
        relation = {'subject': {'market': 'KR', 'symbol': 'T'}, 'sourceAboxSnapshotId': 'a',
            'inferenceGenerationId': 'g', 'graphStoreInference': {'sourceAboxSnapshotId': 'a', 'inferenceGenerationId': 'g',
                'traces': [{'ruleId': rule.rule_id, 'claimContract': rule.resolved_claim_contract.to_dict(),
                           'knowledgeBasis': rule.resolved_knowledge_basis.to_dict(), 'matched': True,
                           'matchedConditions': current['rules'][0]['conditions']}]}}
        self.assertEqual([rule.rule_id], native_context_rule_ids(relation))
        relation['graphStoreInference']['sourceAboxSnapshotId'] = 'different'
        self.assertEqual([], native_context_rule_ids(relation))
        from test_typedb_independent_observation import readable_fixture
        from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
        from digital_twin.modules.notifications.application.typedb_observation_message import typedb_observation_telegram_message
        context = readable_fixture()
        live = context['ontologyRelationContext']
        live['hypothesisSet'] = {'hypotheses': []}
        live['hypothesisLifecycle'] = {'transitions': []}
        live.pop('investmentBrain', None)
        proof = {'ruleId': rule.rule_id, 'label': rule.label, 'matched': True,
                 'claimContract': rule.resolved_claim_contract.to_dict(),
                 'knowledgeBasis': rule.resolved_knowledge_basis.to_dict(),
                 'strategyResponse': rule.derivations[0].strategy_response,
                 'nextChecks': rule.derivations[0].next_checks,
                 'matchedConditions': current['rules'][0]['conditions']}
        symbol = live['subject']['symbol']
        evidence_id = 'stock:' + symbol + '|HAS_TEMPORAL_WINDOW|temporal-window:' + symbol + ':3D'
        proof['matchedConditions'][-1]['matchedTargetProperties'] = {
            'contractMatched': True, 'sourceFeatureSnapshotId': 'features', 'modelEvidenceIds': [evidence_id],
            'knowledgeCutoffAt': '2026-10-23T01:00:00Z', 'sourceTemporalWindows': [{
                'evidenceId': evidence_id, 'symbol': symbol, 'sourceFeatureSnapshotId': 'features',
                'knowledgeCutoffAt': '2026-10-23T01:00:00Z', 'windowKey': '3D',
                'hasSufficientHistory': True, 'priceChangePct': -2.34}]}
        live['matchedRules'], live['activeRules'], live['referenceRules'] = [proof], [proof], []
        live['graphStoreInference']['traces'] = [proof]
        live['graphStoreInference']['relations'] = []
        context['relationChangeEvidence'] = relation_change_evidence(context)
        self.assertTrue(context['relationChangeEvidence']['eligible'])
        message = typedb_observation_telegram_message(context)
        self.assertIn('회복 관찰', message)
        self.assertIn('11,000', message)
        self.assertIn('3일 구간 가격 등락 -2.34%', message)
        self.assertNotIn('연구용', message)
        self.assertEqual('passed', context['customerInvestmentDocumentQuality']['status'],
                         context['customerInvestmentDocumentQuality'])

    def test_strategy_ai_design_uses_registered_models_and_keeps_disabled_bundle(self):
        context = {'strategyAuthoringContract': catalog(), 'ruleBox': {}}
        prompt = build_rule_change_candidate_prompt(context)
        self.assertIn('strategyDesign', prompt)
        self.assertIn('market-regime-comparison', prompt)
        candidate = {'title': 'fixture strategy', 'strategyDesign': design()}
        result = rule_change_candidates_from_text(json.dumps({'candidates': [candidate]}), context)[0]
        self.assertFalse(result['proposedRule']['enabled'])
        self.assertEqual('research-only', result['proposedRule']['claim_contract']['strategyContract']['mode'])
        candidate['proposedRule'] = {'enabled': True}
        blocked = rule_change_candidates_from_text(json.dumps({'candidates': [candidate]}), context)[0]
        self.assertIsNone(blocked['proposedRule'])
        self.assertEqual('schema-mismatch', blocked['blockers'][0]['kind'])
        from digital_twin.modules.model_registry.domain.investment_strategy_proposals import without_retired_strategy_score_fields
        from digital_twin.modules.model_registry.domain.rule_strategy_contract import fingerprint
        frozen = strategy()
        frozen['models'][design()['phases']['setup'][0]]['condition']['target_property_filters']['score'] = {'operator': '>=', 'value': 0.7}
        frozen['contractFingerprint'] = fingerprint({k: v for k, v in frozen.items() if k != 'contractFingerprint'})
        self.assertEqual({'strategyContract': frozen}, without_retired_strategy_score_fields({'strategyContract': frozen, 'score': 1}))
        from digital_twin.modules.model_registry.application.ontology_rule_candidate_service import RuleChangeCandidateProposalService
        repository, advisor, proposals = Mock(), Mock(), Mock()
        proposals.design_catalog.return_value = catalog()
        service = RuleChangeCandidateProposalService(repository, advisor, strategy_proposal_service=proposals)
        service.build_context = Mock(return_value={'ruleBox': {'status': 'ok', 'rules': [r.to_dict() for r in default_graph_inference_rules()]},
                                                  'inferenceBox': {'status': 'not-read'}})
        advisor.propose.return_value = [result]
        response = service.propose(trigger='short-term-strategy-design')
        self.assertEqual('ok', response['status'])
        service.build_context.assert_not_called()
        repository.save_rule_change_candidates.assert_not_called()
        proposals.propose_from_rule_candidates.assert_called_once()

    def test_strategy_design_route_requires_write_access_and_reports_stale_catalog(self):
        from digital_twin.infrastructure.composition.model_registry import strategy_delivery_settings
        registry = Mock()
        registry.control.return_value = Mock(delivery_deployment_id='delivery', active_deployment_id='active')
        registry.get.return_value = {'graphStoreBinding': 'delivery-db'}
        settings = {'typedbDatabase': 'stale-default'}
        with patch('digital_twin.infrastructure.operational_store.reasoning_engine_registry_store', return_value=registry):
            bound = strategy_delivery_settings(settings)
            self.assertEqual('delivery-db', bound['typedbDatabase'])
            self.assertEqual('delivery', bound['_strategySourceDeployment']['deploymentId'])
            registry.get.assert_called_with('delivery')
            self.assertEqual('stale-default', settings['typedbDatabase'])
            registry.get.return_value = {}
            with self.assertRaises(RuntimeError): strategy_delivery_settings(settings)
        from digital_twin.infrastructure.web.routes.model_registry import ModelRegistryRoutes
        create = Mock(side_effect=ValueError('RuleBox changed'))
        route = ModelRegistryRoutes(create_strategy_design_payload=create)
        request = Mock(command='POST')
        request.ensure_writable.return_value = False
        route.route_investment_strategy_proposals(request, '/api/investment-strategy-proposals/design', {})
        create.assert_not_called()
        request.ensure_writable.return_value = True
        route.route_investment_strategy_proposals(request, '/api/investment-strategy-proposals/design', {})
        self.assertEqual(400, request.send_payload.call_args.args[0])


class StrategyContractDatabaseTests(StabilizationDatabaseCase):
    def test_strategy_durable_proposal_previews_whole_bundle_without_promoting_trading(self):
        from digital_twin.modules.model_registry.infrastructure.mysql_investment_strategy_proposals import MySQLInvestmentStrategyProposalStore
        from digital_twin.modules.model_registry.application.investment_strategy_proposal_service import InvestmentStrategyProposalService, proposal_rule_payloads
        rules = [r.to_dict() for r in default_graph_inference_rules()]
        repository = Mock()
        repository.rulebox_snapshot.return_value = {'status': 'ok', 'rules': rules}
        repository.validate_rulebox_materialization.return_value = {'status': 'ok', 'matchedCount': 1, 'candidateRuleCount': 4}
        store = MySQLInvestmentStrategyProposalStore(self.settings)
        service = InvestmentStrategyProposalService(store, repository)
        payload = {'title': '회복 전략 연구', 'strategyDesign': design(), 'symbols': ['TEST'], 'sourceRuleboxFingerprint': catalog()['sourceRuleboxFingerprint']}
        with self.assertRaisesRegex(ValueError, 'RuleBox changed'):
            service.create_design({**payload, 'sourceRuleboxFingerprint': 'old'})
        with self.assertRaises(ValueError):
            service.create_design([])
        created = service.create_design(payload)
        self.assertEqual(1, created['createdCount'])
        saved = store.get(created['proposals'][0]['id'])
        frozen = deepcopy(saved.metadata['strategyContract'])
        self.assertEqual(4, len(saved.rule_ids))
        self.assertEqual(4, len(proposal_rule_payloads(saved)))
        self.assertEqual(0, service.create_design(payload)['createdCount'])
        preview = service.validate_materialization(saved.proposal_id)
        self.assertEqual(4, len(repository.validate_rulebox_materialization.call_args.args[0]['rules']))
        self.assertEqual('proposed', preview['proposal']['status'])
        self.assertFalse(preview['validation']['tradeTimingQualified'])
        self.assertEqual('not-evaluated', preview['validation']['strategyPhaseEvaluation']['status'])
        repository.validate_rulebox_materialization.return_value = {
            'status': 'ok', 'nativeMatchResult': {'executedRules': [
                {'ruleId': rid, 'status': 'ok', 'matchedCount': 0} for rid in saved.rule_ids]}}
        self.assertEqual('evaluated', service.validate_materialization(saved.proposal_id)['validation']['strategyPhaseEvaluation']['status'])
        self.assertEqual('not-ready', service.approve(saved.proposal_id, {'forceApproved': True})['status'])
        self.assertEqual(frozen, store.get(saved.proposal_id).metadata['strategyContract'])
        service.strategy_repository_provider = lambda: (repository, {'deploymentId': 'new', 'graphStoreBinding': 'new-db'})
        repository.validate_rulebox_materialization.reset_mock()
        self.assertEqual('source-deployment-changed', service.validate_materialization(saved.proposal_id)['status'])
        repository.validate_rulebox_materialization.assert_not_called()
