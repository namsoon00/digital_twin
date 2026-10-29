"""Collection -> stored facts -> native evidence -> dispatch clock regressions."""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from digital_twin.infrastructure.external_api.adapters.coingecko import CoinGeckoMarketAdapter
from digital_twin.modules.market_data.application.external_data.read_model_service import ExternalSignalsReadModelService
from digital_twin.modules.market_data.domain.external_signal_quality import attach_external_signal_quality
from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_external_abox import add_external_signal_concepts, external_observation_profile
from digital_twin.modules.reasoning.domain.ontology_inference_materializer import grounded_inference_context, observation_metadata
from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import governed_graph_inference_rules
from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
from digital_twin.modules.notifications.domain.rule_dispatch_validity import rule_dispatch_validity
from test_crypto_context_delivery import crypto_fixture


CAPTURE = '2026-10-28T01:03:13.952856Z'
STORED = '2026-10-28T01:03:13.957485Z'
OBSERVED = '2026-10-28T01:02:47Z'
CLOCK = datetime.fromisoformat('2026-10-28T01:06:53+00:00')


def markets():
    return {'ethereum': {'symbol': 'ETH', 'provider': 'CoinGecko', 'price': 2400,
                         'change24h': -1.3, 'change7d': -4.74,
                         'lastUpdated': OBSERVED, 'fetchedAt': CAPTURE}}


def read_signals(values, now=CLOCK):
    row = {'datasetId': 'coingecko.market', 'subjectKey': 'global', 'providerId': 'coingecko',
           'fetchedAt': STORED, 'updatedAt': STORED, 'payload': {'cryptoMarkets': deepcopy(values)},
           'availability': 'observed', 'freshnessState': 'fresh', 'revisionId': 'capture-revision',
           'payloadHash': 'unchanged-payload', 'quality': {'dataUsable': True}}
    before = deepcopy(row)
    store = SimpleNamespace(list_current=lambda _: [row], provider_statuses=lambda: [])
    result = ExternalSignalsReadModelService(store).signals_for_subjects(['ETH'])
    result = attach_external_signal_quality(result, settings={}, now=now)
    assert row == before, 'reading must not rewrite immutable source facts'
    return result


class RelationSourceValidityTests(unittest.TestCase):
    def test_capture_and_legacy_fact_reach_dispatch_without_borrowing_storage_time(self):
        raw = markets()
        fake = SimpleNamespace(add_coingecko=lambda signals: signals.update(
            cryptoMarkets=deepcopy(raw), cryptoFetchedAt=CAPTURE))
        with patch('digital_twin.infrastructure.external_api.adapters.coingecko.legacy_provider', return_value=fake), \
             patch('digital_twin.infrastructure.external_api.adapters.base.utc_now_iso', return_value=STORED):
            captured = CoinGeckoMarketAdapter().fetch(None, {})
        self.assertEqual(CAPTURE, captured.fetched_at)
        self.assertEqual(OBSERVED, captured.source_as_of)
        self.assertEqual(raw, captured.payload['cryptoMarkets'])

        signals = read_signals(raw)
        self.assertEqual(CAPTURE, signals['cryptoFreshness']['fetchedAt'])
        self.assertEqual(STORED, signals['externalDataLineage']['coingecko.market:GLOBAL']['fetchedAt'])
        graph = PortfolioOntology('main', entities=[OntologyEntity('portfolio:main', 'Portfolio', 'portfolio', {})])
        graph.worldview['evidenceEvaluationAsOf'] = CLOCK.isoformat()
        add_external_signal_concepts(graph, 'portfolio:main', signals, {})
        asset = next(e for e in graph.entities if e.kind == 'crypto-asset')
        rule = next(r for r in governed_graph_inference_rules() if r.rule_id == 'graph.crypto.market.7d.down.watch.v1')
        grounded = grounded_inference_context(graph, rule, asset,
            {'matchedConditions': [{'conditionId': rule.conditions[0].condition_id, 'kind': 'relation'}]})
        condition = grounded['matchedConditions'][0]
        self.assertTrue(condition['judgementEvidenceUsable'])
        self.assertEqual(OBSERVED, condition['observedAt'])
        self.assertEqual(CAPTURE, condition['sourceFetchedAt'])
        _, context = crypto_fixture()
        for key in ('referenceRules', 'matchedRules'):
            context['ontologyRelationContext'][key][0]['matchedConditions'] = [condition]
        for key in ('relations', 'traces'):
            context['ontologyRelationContext']['graphStoreInference'][key][0]['matchedConditions'] = [condition]
        packet = relation_change_evidence(context)
        self.assertTrue(packet['eligible'])
        self.assertTrue(rule_dispatch_validity(packet, now=CLOCK)['allowed'])
        expired = rule_dispatch_validity(packet, now=CLOCK + timedelta(minutes=22))
        self.assertFalse(expired['allowed'])
        self.assertEqual('source-age-exceeded', expired['checks'][0]['failureKind'])
        self.assertIn('25.0분', expired['reason'])

    def test_invalid_or_mixed_source_rows_never_inherit_newer_collection_quality(self):
        for mutation in ('missing-clock', 'bad-clock', 'naive-clock', 'mixed-captures', 'provider',
                         'missing-source', 'future-source', 'old-source', 'partial', 'disabled'):
            with self.subTest(mutation=mutation):
                raw = markets()
                item = raw['ethereum']
                if mutation == 'missing-clock': item.pop('fetchedAt')
                elif mutation == 'bad-clock': item['fetchedAt'] = 'invalid'
                elif mutation == 'naive-clock': item['fetchedAt'] = '2026-10-28T01:03:13'
                elif mutation == 'mixed-captures':
                    raw['bitcoin'] = {**item, 'symbol': 'BTC', 'fetchedAt': '2026-10-28T01:03:14Z'}
                elif mutation == 'provider': item['provider'] = 'Another provider'
                elif mutation == 'missing-source': item.pop('lastUpdated')
                elif mutation == 'future-source': item['lastUpdated'] = '2026-10-28T02:00:00Z'
                elif mutation == 'old-source': item['lastUpdated'] = '2026-10-28T00:00:00Z'
                elif mutation == 'partial': item['freshnessStatus'] = 'partial'
                elif mutation == 'disabled': item['judgementEvidenceUsable'] = False
                signals = read_signals(raw)
                profile = external_observation_profile(signals, item, 'crypto', 25)
                measured = observation_metadata(profile, 'down:watch', CLOCK.isoformat())
                self.assertFalse(measured['judgementEvidenceUsable'])
        signals = read_signals(markets(), now=CLOCK + timedelta(hours=1))
        self.assertNotEqual('fresh', signals['cryptoFreshness']['status'])

    def test_rejection_reason_distinguishes_quality_age_clocks_and_proof(self):
        _, context = crypto_fixture()
        packet = relation_change_evidence(context)
        packet['current']['rules'][0]['conditions'][0].update(observedAt=OBSERVED, maxAgeMinutes=25)
        cases = [({'judgementEvidenceUsable': False, 'freshnessGateReason': '수집 자료의 품질 확인 필요'},
                  'quality-rejected', '품질 확인 필요'),
                 ({'observedAt': '2026-10-28T00:00:00Z'}, 'source-age-exceeded', '허용 시간을 초과'),
                 ({'observedAt': ''}, 'missing-source-clock', '원천 기준 시각'),
                 ({'observedAt': '2026-10-28T02:00:00Z'}, 'future-source-clock', '확인 시각보다 뒤'),
                 ({'maxAgeMinutes': 'nan'}, 'invalid-validity-contract', '올바르게 기록되지'),
                 ({'validUntil': '2026-10-28T01:05:00Z'}, 'validity-ended', '유효 종료 시각'),
                 ({'freshnessStatus': 'partial'}, 'freshness-unconfirmed', '신선도 상태')]
        for update, kind, text in cases:
            with self.subTest(kind=kind):
                changed = deepcopy(packet)
                changed['current']['rules'][0]['conditions'][0].update(update)
                result = rule_dispatch_validity(changed, now=CLOCK)
                self.assertFalse(result['allowed'])
                self.assertEqual(kind, result['checks'][0]['failureKind'])
                self.assertIn(text, result['reason'])
                if kind == 'quality-rejected': self.assertNotIn('유효기간', result['reason'])
        missing = deepcopy(packet)
        missing['current']['rules'][0]['nativeProof'] = False
        self.assertIn('원본 근거', rule_dispatch_validity(missing, now=CLOCK)['reason'])
        self.assertNotIn('거래 세션', observation_metadata(
            {'sourceAsOf': OBSERVED, 'freshnessStatus': 'fresh', 'judgementEvidenceUsable': False},
            'down:watch', CLOCK.isoformat())['freshnessGateReason'])


if __name__ == '__main__':
    unittest.main()
