"""Customer-visible regressions: grounding, novelty, conditions and review fencing."""
import copy
from datetime import timedelta
import unittest
from unittest.mock import Mock

from ai_insight_fixtures import observation, packet, plan, ref, review
from digital_twin.modules.ai_orchestration.domain.insight_contract import insight_errors, insight_fingerprint, instant
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality, accept_review, quality_block
from digital_twin.modules.ai_orchestration.domain.insight_memory import receipt_facts
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_review_input, validate_execution_input
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.publication import repeat_block
from digital_twin.modules.outcomes.domain.observation_followup import evaluate_observation_conditions, prepare_observation_conditions
from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
import test_ai_control as control_helpers


class InsightGroundingTests(unittest.TestCase):
    def test_wrong_quantities_causes_certainty_and_missing_slope_cannot_publish(self):
        original = observation()
        for text in ("현재가는 110원입니다.", "이 종목의 포트폴리오 비중은 9.09%입니다.",
                     "현재 하락은 기관의 대규모 매도 때문에 발생했습니다.", "추세 전환으로 반등이 확정됐습니다.",
                     "기관 매도 때문에 하락했습니다. 원인은 확인할 수 없습니다.",
                     "5일선의 기울기가 음수라 하락 압력을 보여 줍니다."):
            result = copy.deepcopy(original); result['summary'] = text
            self.assertTrue(insight_errors(result, result['input']), text)
            self.assertTrue(quality_block(result))
        original['counterEvidence'] = '현재 자료가 부분 상태라 원인은 판단할 수 없습니다.'
        self.assertFalse(insight_errors(original, original['input']))
        for text in ('약세 원인은 확정되지 않는다.', '방향은 확정적이지 않으며 반등을 보장할 수 없습니다.'):
            original['counterEvidence'] = text
            self.assertFalse(insight_errors(original, original['input']), text)
        original['hypothesis'] = '상승 기울기의 20일선과 60일선은 흐름 유지를 시사하며, 5일선 위 위치는 단기 반등일 수 있습니다.'
        original['input']['facts'][0]['ma20Slope'] = 0.2
        original['claimEvidence']['hypothesis'] += [ref('ma20Slope'), ref('ma60Slope')]
        self.assertFalse(insight_errors(original, original['input']))

    def test_exact_field_period_units_and_source_clock_are_checked(self):
        original = observation()
        for mutation in ('missing-field', 'wrong-period', 'wrong-unit', 'future', 'reference-only', 'reversed-time', 'unset-policy'):
            result = copy.deepcopy(original); p = result['input']
            if mutation == 'missing-field':
                result['claimEvidence']['summary'][0]['field'] = 'ma5Slope'
            elif mutation == 'wrong-period':
                result['claimEvidence']['summary'][0]['period'] = 'baseline'
            elif mutation == 'wrong-unit':
                result['observations'][0]['right']['field'] = 'volumeRatio'
            elif mutation == 'future':
                p['facts'][0]['sourceAsOf'] = (instant(p['capturedAt']) + timedelta(hours=1)).isoformat()
            elif mutation == 'reference-only':
                p['facts'][0]['judgementEvidenceUsable'] = False
            elif mutation == 'unset-policy':
                p['facts'][0]['policyLimitRatio'] = 0
                result['observations'][0]['left']['field'] = 'positionWeight'
                result['observations'][0]['right']['field'] = 'policyLimitRatio'
            else:
                p['lastDeliveredNotification'] = {'facts': copy.deepcopy(p['facts'])}
                p['facts'][0]['sourceAsOf'] = (instant(p['capturedAt']) - timedelta(hours=1)).isoformat()
                result['observations'][0]['right']['period'] = 'baseline'
            self.assertTrue(insight_errors(result, p), mutation)

    def test_review_must_cover_every_section_and_new_meaning(self):
        result = observation()
        for patch in ({'usefulness': 'generic'}, {'novelty': 'repetition'}, {'sections': {}}, {'version': 'old'}):
            rejected = accept_review(result, {**review(), **patch}, 'review-id')
            self.assertEqual('rejected', rejected['status'])
        tampered = copy.deepcopy(result); tampered['input']['facts'][0]['currentPrice'] = 99
        self.assertTrue(quality_block(tampered))
        self.assertEqual('', quality_block(result))
        silent = copy.deepcopy(result); silent['notification']['send'] = False
        self.assertEqual('observation-only', local_quality(silent)['status'])
        now = instant(result['input']['capturedAt'])
        prior = {'accountId': 'control-test', 'symbol': 'TEST', 'inputFingerprint': 'old', 'deliveredAt': (now - timedelta(minutes=61)).isoformat()}
        self.assertTrue(repeat_block(result, [prior], 'control-test', 'TEST', now))
        result['input']['followUpEvaluations'] = [{'conditionId': 'observed', 'transitionVerified': True, 'effect': 'invalidates'}]
        self.assertFalse(repeat_block(result, [prior], 'control-test', 'TEST', now))
        prior['deliveredAt'] = (now - timedelta(minutes=59)).isoformat()
        self.assertTrue(repeat_block(result, [prior], 'control-test', 'TEST', now))

    def test_price_jitter_does_not_create_new_insight_and_review_is_frozen(self):
        original = observation()
        changed = copy.deepcopy(original)
        changed['input']['facts'][0]['currentPrice'] = 100.1
        self.assertEqual(insight_fingerprint(original, original['input']), insight_fingerprint(changed, changed['input']))
        changed['input']['lastDeliveredNotification'] = {'insightFingerprint': original['quality']['insightFingerprint']}
        self.assertEqual('rejected', local_quality(changed)['status'])
        envelope = freeze_review_input(original)
        validate_execution_input(envelope)
        envelope['draft']['summary'] = '검토 이후 다른 설명으로 바꾼 내용입니다.'
        with self.assertRaises(ValueError):
            validate_execution_input(envelope)

    def test_renderer_shows_named_values_provider_and_registered_conditions(self):
        result = observation(); text = render_ai_observation(result)
        self.assertIn('현재가: 100원', text)
        self.assertIn('평균 매입가: 110원', text)
        self.assertIn('계정 내 비중: 10%', text)
        self.assertIn('0.01배 미만', text)
        self.assertNotIn('거래량 대비: 0배', text)
        self.assertIn('출처 Example exchange', text)
        self.assertNotIn('출처 holding', text)
        self.assertIn('현재가가 20일 평균 가격 초과로 전환', text)
        self.assertNotIn('확인할 가치가 있는가', text)
        self.assertEqual(result['input']['facts'][0]['positionWeight'], receipt_facts(result)[0]['positionWeight'])
        previous = copy.deepcopy(result['input']['facts'][0]); previous['currentPrice'] = 102
        result['input']['lastDeliveredNotification'] = {'facts': [previous]}
        result['observations'].append({'left': ref('currentPrice'), 'operator': 'lt', 'right': {**ref('currentPrice'), 'period': 'baseline'}})
        result['input']['facts'][0]['ma20Slope'] = -0.2
        result['claimEvidence']['counterEvidence'].append(ref('ma20Slope'))
        text = render_ai_observation(result)
        self.assertIn('현재가: 102원 → 100원', text)
        self.assertIn('20일 평균 가격 기울기: -0.2%', text)

    def test_legacy_result_cannot_use_new_publication_gate(self):
        result = observation(); result.pop('insightVersion'); result.pop('quality')
        self.assertTrue(quality_block(result))


class ObservationConditionTests(unittest.TestCase):
    def advance(self, source, minutes, price):
        value = copy.deepcopy(source)
        clock = (instant(source['capturedAt']) + timedelta(minutes=minutes)).isoformat()
        value['capturedAt'] = clock
        value['facts'][0].update(currentPrice=price, sourceAsOf=clock)
        return value

    def test_false_to_true_is_verified_once_and_already_true_requires_rearming(self):
        result = observation(); source = result['input']
        baseline = {'jobId': 'delivered', 'followUpConditions': result['followUpConditions']}
        hit = evaluate_observation_conditions(self.advance(source, 60, 109), baseline)
        self.assertTrue(hit[0]['transitionVerified'])
        again = evaluate_observation_conditions(self.advance(source, 120, 110), baseline, hit)
        self.assertFalse(again[0]['transitionVerified'])
        self.assertEqual('triggered', again[0]['status'])
        source['facts'][0]['currentPrice'] = 109
        baseline['followUpConditions'] = prepare_observation_conditions(plan()['followUpConditions'], source)
        same = evaluate_observation_conditions(self.advance(source, 60, 110), baseline)
        self.assertFalse(same[0]['transitionVerified'])
        armed = evaluate_observation_conditions(self.advance(source, 120, 107), baseline, same)
        triggered = evaluate_observation_conditions(self.advance(source, 180, 110), baseline, armed)
        self.assertTrue(triggered[0]['transitionVerified'])

    def test_expiry_missing_old_and_cross_account_observations_are_not_successes(self):
        result = observation(); source = result['input']
        baseline = {'jobId': 'delivered', 'followUpConditions': result['followUpConditions']}
        with self.assertRaises(ValueError):
            evaluate_observation_conditions(source, {**baseline, 'accountId': 'someone-else'})
        expired = evaluate_observation_conditions(self.advance(source, 1500, 200), baseline)
        self.assertEqual('expired', expired[0]['status'])
        self.assertFalse(expired[0]['transitionVerified'])
        old = copy.deepcopy(source); old['facts'][0]['currentPrice'] = 200
        self.assertFalse(evaluate_observation_conditions(old, baseline)[0]['transitionVerified'])
        missing = self.advance(source, 60, 110); del missing['facts'][0]['ma20']
        self.assertEqual('unavailable', evaluate_observation_conditions(missing, baseline)[0]['status'])
        for mutation in ({'currency': 'USD'}, {'judgementEvidenceUsable': False}):
            unusable = self.advance(source, 60, 110)
            unusable['facts'][0].update(mutation)
            self.assertEqual('unavailable', evaluate_observation_conditions(unusable, baseline)[0]['status'])
        state = evaluate_observation_conditions(self.advance(source, 60, 109), baseline)
        state[0]['baselineJobId'] = 'someone-else'
        self.assertTrue(evaluate_observation_conditions(self.advance(source, 120, 109), baseline, state)[0]['transitionVerified'])


class InsightControlTests(unittest.TestCase):
    def test_actual_controller_persists_review_before_admission_and_keeps_rejection(self):
        service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(return_value=packet()))
        planner.return_value = plan()
        service.reviewer = Mock(return_value=review())
        self.assertEqual('completed', service.run_once()['status'])
        self.assertEqual(2, store.save_execution_input.call_count)
        saved = store.complete.call_args.args[1]
        self.assertEqual('accepted', saved['quality']['status'])
        self.assertTrue(saved['followUpConditions'])
        self.assertEqual('independent-observation-review-v1', service.reviewer.call_args.args[0]['promptVersion'])
        service.reviewer.return_value = {**review(), 'usefulness': 'generic'}
        self.assertEqual('completed', service.run_once()['status'])
        self.assertEqual('rejected', store.complete.call_args.args[1]['quality']['status'])

    def test_review_failure_and_legacy_model_output_never_approve_delivery(self):
        service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(return_value=packet()))
        planner.return_value = plan()
        service.reviewer = Mock(side_effect=TimeoutError)
        self.assertEqual('completed', service.run_once()['status'])
        self.assertEqual('rejected', store.complete.call_args.args[1]['quality']['status'])
        legacy = plan(); legacy.pop('insightVersion')
        planner.return_value = legacy; service.reviewer.reset_mock()
        self.assertEqual('completed', service.run_once()['status'])
        service.reviewer.assert_not_called()
        self.assertEqual('rejected', store.complete.call_args.args[1]['quality']['status'])
