"""Non-predictive crypto rules survive candidate, publication and receipt gates."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest

from test_investment_insight_dispatch_service import (
    subject_case, context_observation, frozen_notification_claim, FakeOrchestrator,
    FakeIngress, FakeNotificationQueue, FakeAIHandoff,
)
from digital_twin.modules.decisions.contracts import decision_synthesis_from_relation_context
from digital_twin.modules.decisions.application.investment_insight_dispatch_service import InvestmentInsightDispatchService
from digital_twin.modules.notifications.domain.context_observation_notifications import (
    typedb_context_observation_contract, context_observation_delivery_decision,
)
from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
from digital_twin.modules.notifications.domain.relation_follow_up import delivery_snapshot
from digital_twin.modules.notifications.application.typedb_observation_message import typedb_observation_telegram_message
from digital_twin.modules.reasoning.application.investment_reasoning.decision_synthesis import V2GraphDecisionCandidateBuilder


def crypto_fixture(horizon='7d', severity='watch'):
    case = subject_case('subject:crypto-context')
    case.symbol = 'ETH'
    case.synthesis = replace(case.synthesis, symbol='ETH')
    case.candidate_set = replace(case.candidate_set, symbol='ETH')
    context = context_observation(case)
    context['symbol'] = 'ETH'
    relation = context['ontologyRelationContext']
    relation.update(accountId=case.account_id, generationAligned=True)
    relation['subject'] = {'symbol': 'ETH', 'name': '이더리움', 'market': 'CRYPTO'}
    relation['facts'] = {'symbol': 'ETH', 'market': 'CRYPTO', 'currentPrice': 2400, 'currency': 'USD',
                         'priceChangeRate': -1.3, 'quoteSource': 'CoinGecko',
                         'sourceAsOf': '2026-10-20T06:05:47Z', 'sourceFetchedAt': '2026-10-20T06:07:00Z'}
    relation['decision'] = {'basis': 'typedbInferenceBox', 'selectedRuleId': '',
                            'label': '예측 가설 미성립', 'notificationSeverity': ''}
    relation['hypothesisSet'] = {'hypotheses': []}
    relation['hypothesisLifecycle'] = {}
    rule_id = 'graph.crypto.market.' + horizon + '.down.' + severity + '.v1'
    rule = {'ruleId': rule_id, 'label': '이더리움 ' + horizon + ' 하락 변동 재확인', 'symbol': 'ETH',
            'matched': True, 'ruleSourceKind': 'crypto-asset',
            'claimContract': frozen_notification_claim(rule_id, 'causal-context'),
            'knowledgeBasis': {'ruleKind': 'context-observation', 'decisionEligibility': 'reference-only',
                               'requiresHypothesis': False},
            'matchedConditions': [{'conditionId': 'crypto-' + horizon + '-down-' + severity,
                'kind': 'relation', 'role': 'required', 'matchedByTypeDB': True, 'matched': True,
                'observedValue': 'down:' + severity, 'observedAt': '2026-10-20T06:05:47Z',
                'matchedTargetProperties': {'eventType': 'crypto-market-' + horizon + '-down-' + severity,
                    'eventCategory': 'crypto-market-threshold', 'symbol': 'ETH', 'horizon': horizon,
                    'direction': 'down', 'changePct': -4.74, 'thresholdPct': 4},
                'source': 'CoinGecko', 'freshnessRequired': True, 'maxAgeMinutes': 25,
                'freshnessStatus': 'fresh', 'judgementEvidenceUsable': True}],
            'nextChecks': ['다음 수집에서 가격 경로와 연결 자산의 반응을 확인합니다.']}
    rule['claimContract'].update(statement='현재 코인 가격 경로를 설명하는 관측입니다.',
        expectedOutcome='현재 코인 가격 경로를 설명하는 관측입니다.',
        falsificationContract='다음 수집에서 같은 가격 경로가 유지되는지 확인합니다.')
    relation['activeRules'] = []  # Missing investment-stage metadata must not erase a context proof.
    relation['matchedRules'] = [rule]
    relation['referenceRules'] = [rule]
    relation['graphStoreInference'].update(symbol='ETH', relations=[rule], traces=[deepcopy(rule)])
    context['investmentSubjectDecisionCase'] = case.to_dict()
    context['reasoningDeliveryTrigger'] = {'material': True, 'userObservable': True,
        'facts': {'cryptoTransitions': [{'symbol': 'ETH', 'horizon': horizon, 'direction': 'down',
            'changePct': -4.74, 'thresholdPct': 4, 'transition': 'threshold-crossed'}]}}
    return case, context


class CryptoContextDeliveryTests(unittest.TestCase):
    def assert_native_condition_keeps_the_source_measurement(self):
        from datetime import datetime, timezone
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology, OntologyEntity
        from digital_twin.modules.reasoning.domain.ontology_external_abox import add_external_signal_concepts
        from digital_twin.modules.reasoning.domain.ontology_inference_materializer import grounded_inference_context
        from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import governed_graph_inference_rules
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        graph = PortfolioOntology('main', entities=[OntologyEntity('portfolio:main', 'Portfolio', 'portfolio', {})])
        signals = {'cryptoMarkets': {'ethereum': {'symbol': 'ETH', 'price': 2400, 'change24h': -1.3,
            'change7d': -4.74, 'lastUpdated': now, 'fetchedAt': now, 'provider': 'CoinGecko'}},
            'cryptoFetchedAt': now, 'cryptoFreshness': {'status': 'fresh', 'fetchedAt': now, 'maxAgeMinutes': 25}}
        add_external_signal_concepts(graph, 'portfolio:main', signals, {})
        asset = next(e for e in graph.entities if e.kind == 'crypto-asset')
        rule = next(r for r in governed_graph_inference_rules() if r.rule_id == 'graph.crypto.market.7d.down.watch.v1')
        grounded = grounded_inference_context(graph, rule, asset,
            {'matchedConditions': [{'conditionId': rule.conditions[0].condition_id, 'kind': 'relation'}]})
        measured = grounded['matchedConditions'][0]['matchedTargetProperties']
        self.assertEqual('ETH', measured['symbol'])
        self.assertEqual(-4.74, measured['changePct'])
        self.assertEqual(4.0, measured['thresholdPct'])
        self.assertEqual('7d', measured['horizon'])

    def test_native_context_without_selected_investment_rule_builds_an_actionless_candidate(self):
        self.assert_native_condition_keeps_the_source_measurement()
        case, context = crypto_fixture()
        relation = context['ontologyRelationContext']
        # Candidate recognition must precede subject creation/publication.
        synthesis = decision_synthesis_from_relation_context(case.account_id, relation)
        self.assertEqual('CONTEXT_OBSERVATION', synthesis.disposition_code)
        self.assertEqual('NO_ACTION', synthesis.graph_candidate_action)
        self.assertEqual((), synthesis.eligible_hypothesis_ids)
        builder = V2GraphDecisionCandidateBuilder({}, SimpleNamespace(sent={}))
        event = builder._base_event(SimpleNamespace(account_id='main', account_label='기본',
            generated_at='2026-10-20T06:07:00Z'), relation, synthesis)
        self.assertIsNotNone(event)
        self.assertEqual('cryptoOntologySignal', event.rule)
        self.assertEqual('WATCH', event.severity)
        self.assertFalse(event.metadata['requiresAiJudgement'])
        self.assertEqual('NO_ACTION', event.metadata['contextObservationDecision']['action'])

    def test_metadata_mixed_predictions_wrong_subject_and_missing_native_proof_do_not_open_the_path(self):
        _, context = crypto_fixture()
        original = context['ontologyRelationContext']
        for mutation in ('traces', 'generation', 'abox', 'symbol', 'market', 'claim', 'purpose', 'mixed', 'hypothesis'):
            with self.subTest(mutation=mutation):
                relation = deepcopy(original)
                trace = relation['graphStoreInference']['traces'][0]
                if mutation == 'traces': relation['graphStoreInference']['traces'] = []
                elif mutation == 'generation': relation['graphStoreInference']['inferenceGenerationId'] = 'wrong'
                elif mutation == 'abox': relation['graphStoreInference']['sourceAboxSnapshotId'] = 'wrong'
                elif mutation == 'symbol': trace['symbol'] = 'BTC'
                elif mutation == 'market': relation['subject']['market'] = 'US'
                elif mutation == 'claim': trace['claimContract']['ruleId'] = 'unrelated'
                elif mutation == 'purpose': trace['claimContract']['notificationIntent'] = {}
                elif mutation == 'mixed':
                    relation['graphStoreInference']['traces'].append({'claimContract': {'claimType': 'market-hypothesis'}})
                elif mutation == 'hypothesis': relation['hypothesisSet']['hypotheses'] = [{'hypothesisId': 'predictive'}]
                self.assertEqual({}, typedb_context_observation_contract(relation))

    def test_receipts_suppress_same_band_but_keep_an_undelivered_or_escalated_context(self):
        _, context = crypto_fixture()
        first = relation_change_evidence(context)
        self.assertTrue(first['eligible'])
        self.assertEqual('context-evidence', first['customerEvents'][0]['purpose'])
        self.assertEqual({}, first['customerEvents'][0]['hypothesis'])
        prior = delivery_snapshot(first, 'delivered-crypto', 'main')
        prior.update(receiptJobId='delivered-crypto', deliveredAt='2026-10-20T06:08:00Z')
        context['ontologyRelationContext']['facts']['currentPrice'] = 2410
        context['ontologyRelationContext']['graphStoreInference']['traces'][0]['matchedConditions'][0]['matchedTargetProperties']['changePct'] = -4.9
        again = relation_change_evidence(context, prior)
        self.assertFalse(again['eligible'])
        self.assertTrue(relation_change_evidence(context, first['current'])['eligible'], 'a draft is not a receipt')
        _, escalated = crypto_fixture(severity='major')
        self.assertTrue(relation_change_evidence(escalated, prior)['eligible'])
        bad = deepcopy(context)
        bad['investmentSubjectDecisionCase']['symbol'] = 'BTC'
        self.assertFalse(relation_change_evidence(bad)['eligible'])

    def test_observation_publication_queues_directly_without_an_ai_or_trade(self):
        case, context = crypto_fixture()
        from digital_twin.modules.portfolio.contracts import AlertEvent
        event = AlertEvent('main', '기본', 'WATCH', 'investmentInsight', 'crypto:one', '이더리움', [],
                           symbol='ETH', metadata=context)
        queue, ai = FakeNotificationQueue(), FakeAIHandoff()
        service = InvestmentInsightDispatchService(FakeIngress(), queue, ai, FakeOrchestrator([case]),
                                                   legacy_ai_handoff_enabled=False)
        result = service.dispatch([event])
        self.assertEqual(1, result['routeCounts']['PUBLISH_TYPEDB'])
        self.assertEqual(1, len(queue.jobs))
        self.assertEqual([], ai.events)
        queued = next(iter(queue.jobs.values())).context
        from digital_twin.modules.notifications.domain.typedb_publication import independent_typedb_publication
        self.assertTrue(independent_typedb_publication(queued, account_id='main'))
        self.assertEqual('NO_ACTION', typedb_context_observation_contract(queued)['action'])

    def test_customer_message_describes_the_observation_without_a_missing_hypothesis(self):
        _, context = crypto_fixture()
        context['relationChangeEvidence'] = relation_change_evidence(context)
        self.assertEqual('send', context_observation_delivery_decision(context)['decision'])
        text = typedb_observation_telegram_message(context)
        detail = context['customerInvestmentDetailText']
        self.assertIn('투자 행동 판단은 포함하지 않습니다', text)
        self.assertIn('이더리움', text)
        self.assertIn('-4.74%', text)
        self.assertIn('-4%', text)
        self.assertNotIn('가설 설명이 기록되지 않았습니다', detail)
        self.assertIn('다음 수집', detail)


if __name__ == '__main__':
    unittest.main()
