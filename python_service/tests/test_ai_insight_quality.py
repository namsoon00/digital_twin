"""Customer-visible regressions: grounding, novelty, conditions and review fencing."""
import copy
from datetime import timedelta
import unittest
from unittest.mock import Mock

from ai_insight_fixtures import observation, packet, plan, ref, review
from digital_twin.modules.ai_orchestration.domain.insight_contract import insight_errors, insight_fingerprint, instant, resolve_ref
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality, accept_review, quality_block
from digital_twin.modules.ai_orchestration.domain.insight_memory import receipt_facts, restore_legacy_receipt
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_review_input, validate_execution_input, freeze_execution_input, freeze_repair_input, LEGACY_PROMPT_VERSION
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.publication import repeat_block
from digital_twin.modules.outcomes.domain.observation_followup import evaluate_observation_conditions, prepare_observation_conditions
from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
import test_ai_control as control_helpers


class InsightGroundingTests(unittest.TestCase):
    def test_repair_rebudgets_memory_but_preserves_required_facts_and_parent(self):
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_review_input, prompt_budget
        self.assertEqual(256 * 1024, prompt_budget('invalid'))
        self.assertEqual(512 * 1024, prompt_budget(9999999))
        envelope = freeze_execution_input(packet(), [{'summary': 'a' * 30000}],
                                          [{'result': 'b' * 23000}], max_prompt_bytes=65536)
        original = copy.deepcopy(envelope)
        rejected = plan(); rejected['summary'] = 'c' * 30000
        repair = freeze_repair_input(envelope, rejected, ['invalid summary'], 'parent')
        self.assertEqual(original, envelope)
        self.assertEqual(envelope['current'], repair['current'])
        self.assertEqual(rejected, repair['repair']['rejectedDraft'])
        self.assertLess(len(repair['researchResults']), len(envelope['researchResults']))
        self.assertLessEqual(len(repair['prompt'].encode()), 65536)
        validate_execution_input(repair)
        review = freeze_review_input(observation(), max_prompt_bytes=65536)
        self.assertEqual(65536, review['promptBudgetBytes'])
        validate_execution_input(review)

    def test_render_is_identical_after_json_object_reordering(self):
        import json
        result = observation()
        for index, section in enumerate(result['claimEvidence']):
            fact_id = 'evidence-' + str(index)
            result['input']['facts'].append({'id': fact_id, 'kind': 'research-evidence',
                'label': fact_id, 'source': 'official', 'value': index})
            result['claimEvidence'][section].append(ref('value', fact_id=fact_id))
        reordered = json.loads(json.dumps(result, sort_keys=True))
        self.assertEqual(result, reordered)
        self.assertEqual(render_ai_observation(result), render_ai_observation(reordered))
        reordered['input']['facts'][-1]['label'] = 'changed evidence'
        # Data equality, not object serialization order, is the prerequisite.
        self.assertNotEqual(result, reordered)

    def test_oversized_memory_is_omitted_with_proof_without_changing_current_facts(self):
        import json
        from digital_twin.modules.ai_orchestration.domain.execution_input import PREVIOUS_PROMPT_VERSION
        p = packet()
        history = [{'summary': '가' * 50000}, {'summary': '최근 분석', 'quality': {'status': 'rejected'}}]
        research = [{'runId': 'too-large', 'claims': 'a' * 150000}, {'runId': 'small', 'status': 'completed'}]
        before = copy.deepcopy((p, history, research))
        envelope = freeze_execution_input(p, history, research, max_prompt_bytes=120000)
        self.assertEqual(before, (p, history, research))
        self.assertEqual(p, envelope['current'])
        self.assertEqual([history[1]], envelope['previousAnalyses'])
        self.assertEqual([research[1]], envelope['researchResults'])
        self.assertEqual('context-budget', envelope['memoryCoverage']['excluded']['analyses'][0]['reason'])
        self.assertLessEqual(len(envelope['prompt'].encode()), 120000)
        validate_execution_input(envelope)
        old = copy.deepcopy(envelope); old['promptVersion'] = PREVIOUS_PROMPT_VERSION
        import hashlib
        from digital_twin.modules.ai_orchestration.domain.planning import bounded_planning_prompt
        from digital_twin.modules.ai_orchestration.domain.insight_schema import bounded_planning_schema
        old['prompt'] = bounded_planning_prompt(old['current'], old['previousAnalyses'], old['researchResults'])
        old['promptHash'] = hashlib.sha256(old['prompt'].encode()).hexdigest()
        old['outputSchema'] = bounded_planning_schema(old['current'])
        validate_execution_input(old)
        p['lastDeliveredNotification'] = {'facts': [{**p['facts'][0], 'sourceDetails': 'x' * 70000}]}
        with self.assertRaisesRegex(ValueError, 'exceeds context budget'):
            freeze_execution_input(p, [], [], max_prompt_bytes=65536)
        enlarged = freeze_execution_input(p, [{'summary': 'a' * 50000}], [])
        self.assertEqual(256 * 1024, enlarged['promptBudgetBytes'])
        self.assertEqual(p, enlarged['current'])
        self.assertGreater(len(enlarged['prompt'].encode()), 120000)
        validate_execution_input(enlarged)

    def assert_readable_forms_keep_causal_guards(self):
        result = observation()
        for section, text in (
            ('summary', '현재 주가는 5·20·60일선과 비교해 관찰합니다.'),
            ('comparison', '현재 자료로 확정적 전환으로 보지는 않습니다.'),
            ('portfolioImpact', '높은 집중도 때문에 가격 변동이 계정 전체에 크게 전달될 수 있다.'),
            ('counterEvidence', '개인 수급 부재와 장중 추정치, 마감 호가 참고값 때문에 수급 정렬의 신뢰도는 제한됩니다.'),
        ):
            checked = copy.deepcopy(result); checked[section] = text
            self.assertFalse(insight_errors(checked, checked['input']), text)
        for text in ('현재 주가는 5·20·60일선 위이고 10% 상승했습니다.',
                     '확정적 전환으로 보지는 않습니다. 하지만 반등은 확정됐습니다.',
                     '자료 부재 때문에 가격이 하락했습니다. 신뢰도는 제한됩니다.',
                     '상승 기울기의 5·20·60일선은 흐름을 지지합니다.'):
            checked = copy.deepcopy(result); checked['counterEvidence'] = text
            self.assertTrue(insight_errors(checked, checked['input']), text)

    def assert_generated_references_preserve_fact_period_and_eligibility(self):
        from itertools import product
        from digital_twin.modules.ai_orchestration.domain.insight_schema import planning_schema
        p = packet()
        baseline = copy.deepcopy(p['facts'][0]); baseline['id'] = 'old-quote'
        del baseline['currency']; del baseline['ma60']
        p['lastDeliveredNotification'] = {'facts': [baseline]}
        p['facts'].append({'id': 'reference-only', 'kind': 'valuation', 'value': 200, 'valuationDecisionEligible': False})
        schema = planning_schema(p)
        references = {}
        for name, definition in schema['$defs'].items():
            references[name] = set()
            for branch in definition.get('anyOf', [definition]):
                fields = branch['properties']
                for fact_id, field, period in product(*(fields[key]['enum'] for key in ('factId', 'field', 'period'))):
                    resolve_ref(p, {'factId': fact_id, 'field': field, 'period': period})
                    references[name].add((fact_id, field, period))
        self.assertNotIn(('old-quote', 'ma60', 'baseline'), references['evidence'])
        self.assertNotIn(('old-quote', 'currentPrice', 'current'), references['evidence'])
        self.assertNotIn(('old-quote', 'currentPrice', 'baseline'), references['numeric'])
        self.assertNotIn(('reference-only', 'value', 'current'), references['evidence'])
        self.assertIn(('reference-only', 'value', 'current'), references['limitation'])

    def assert_legacy_input_and_repair_remain_frozen(self):
        import hashlib
        from digital_twin.modules.ai_orchestration.domain.planning import legacy_planning_prompt
        from digital_twin.modules.ai_orchestration.domain.insight_schema import legacy_planning_schema
        envelope = freeze_execution_input(packet(), [], [])
        old = copy.deepcopy(envelope)
        old.update(promptVersion=LEGACY_PROMPT_VERSION, prompt=legacy_planning_prompt(old['current'], [], []),
                   outputSchema=legacy_planning_schema(old['current']))
        old['promptHash'] = hashlib.sha256(old['prompt'].encode()).hexdigest()
        validate_execution_input(old)
        correction = freeze_repair_input(envelope, plan(), ['비교 항목 확인 필요'], 'original-input')
        validate_execution_input(correction)
        self.assertEqual(envelope['current'], correction['current'])
        correction['current']['facts'][0]['currentPrice'] = 999
        with self.assertRaises(ValueError):
            validate_execution_input(correction)

    def assert_legacy_receipt_uses_original_delivered_source(self):
        original = observation(); original['input']['taskId'] = 'old-task'
        original['publication'] = {'jobId': 'sent-job'}
        source = original['input']['facts'][0]
        receipt = {'jobId': 'sent-job', 'taskId': 'old-task', 'accountId': 'control-test', 'symbol': 'TEST',
                   'inputFingerprint': original['inputFingerprint'], 'deliveredAt': original['observedAt'],
                   'facts': [{key: source[key] for key in ('id', 'currentPrice', 'sourceAsOf')}]}
        restored = restore_legacy_receipt(receipt, original)
        self.assertEqual('KRW', restored['facts'][0]['currency'])
        self.assertEqual(98, restored['facts'][0]['ma5'])
        self.assertEqual('old-task', restored['evidenceRestoration']['taskId'])
        self.assertNotIn('currency', receipt['facts'][0])
        for mutation in ('account', 'task', 'job', 'fingerprint', 'time', 'conflicting-value', 'modern'):
            altered = copy.deepcopy(receipt)
            if mutation == 'account': altered['accountId'] = 'other-account'
            elif mutation == 'task': altered['taskId'] = 'different-task'
            elif mutation == 'job': altered['jobId'] = 'never-delivered'
            elif mutation == 'fingerprint': altered['inputFingerprint'] = 'unrelated-source'
            elif mutation == 'time': altered['deliveredAt'] = '2000-01-01T00:00:00Z'
            elif mutation == 'conflicting-value': altered['facts'][0]['currentPrice'] = 999
            else: altered['insightVersion'] = 'observation-insight-v1'
            self.assertEqual(altered, restore_legacy_receipt(altered, original), mutation)

    def test_wrong_quantities_causes_certainty_and_missing_slope_cannot_publish(self):
        self.assert_readable_forms_keep_causal_guards()
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
        self.assert_generated_references_preserve_fact_period_and_eligibility()
        original = observation()
        for mutation in ('missing-field', 'wrong-period', 'wrong-unit', 'missing-currency', 'future', 'reference-only', 'reversed-time', 'unset-policy'):
            result = copy.deepcopy(original); p = result['input']
            if mutation == 'missing-field':
                result['claimEvidence']['summary'][0]['field'] = 'ma5Slope'
            elif mutation == 'wrong-period':
                result['claimEvidence']['summary'][0]['period'] = 'baseline'
            elif mutation == 'wrong-unit':
                result['observations'][0]['right']['field'] = 'volumeRatio'
            elif mutation == 'missing-currency':
                p['facts'][0].pop('currency')
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
        self.assert_legacy_input_and_repair_remain_frozen()
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
        self.assert_legacy_receipt_uses_original_delivered_source()
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
    def assert_repair_is_once_audited_and_reviewed(self):
        service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(return_value=packet()))
        bad = plan(); bad['observations'][0]['right']['field'] = 'missing'
        planner.side_effect = [bad, plan()]
        store.save_execution_input.side_effect = ['original', 'correction', 'review']
        service.reviewer = Mock(return_value=review())
        self.assertEqual('completed', service.run_once()['status'])
        result = store.complete.call_args.args[1]
        self.assertEqual('accepted', result['quality']['status'])
        self.assertEqual('correction', result['executionInputId'])
        self.assertEqual('original', result['repair']['initialInputId'])
        self.assertTrue(result['repair']['initialErrors'])
        correction = planner.call_args.args[0]
        self.assertEqual('independent-observation-repair-v2-ontology-development', correction['promptVersion'])
        self.assertEqual(bad, correction['repair']['rejectedDraft'])
        self.assertTrue(correction['repair']['comparisons'])
        validate_execution_input(correction)
        self.assertEqual(2, planner.call_count)
        self.assertEqual(3, store.save_execution_input.call_count)
        critique = review(); critique['sections']['summary']['supported'] = False
        for final_review in (review(), critique):
            service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(return_value=packet()))
            planner.return_value = plan(); service.reviewer = Mock(side_effect=[critique, final_review])
            self.assertEqual('completed', service.run_once()['status'])
            self.assertEqual(2, planner.call_count)
            self.assertEqual(2, service.reviewer.call_count)
            self.assertEqual(4, store.save_execution_input.call_count)
            correction = planner.call_args.args[0]
            self.assertEqual(critique, correction['repair']['independentReview'])
            validate_execution_input(correction)
            self.assertEqual('accepted' if final_review['sections']['summary']['supported'] else 'rejected',
                             store.complete.call_args.args[1]['quality']['status'])

    def assert_failed_repair_and_lost_ownership_cannot_publish(self):
        from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait
        bad = plan(); bad['summary'] = '기관 매도 때문에 가격이 하락했습니다.'
        silent = plan(); silent['notification']['send'] = False
        for correction in (bad, silent, TimeoutError(), AIControlBudgetWait('call', '2026-10-02T00:00:00Z')):
            service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(return_value=packet()))
            planner.side_effect = [bad, correction]; service.reviewer = Mock(return_value=review())
            outcome = service.run_once()
            self.assertEqual(2, planner.call_count)
            service.reviewer.assert_not_called()
            if isinstance(correction, AIControlBudgetWait):
                self.assertEqual('budget-wait', outcome['status']); store.complete.assert_not_called()
                store.defer_budget.assert_called_once()
            else:
                self.assertEqual('completed', outcome['status'])
                saved = store.complete.call_args.args[1]
                self.assertNotEqual('accepted', saved['quality']['status'])
        service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(return_value=packet()))
        planner.return_value = bad; store.save_execution_input.side_effect = ['original', '']
        self.assertEqual('lease-lost', service.run_once()['status'])
        planner.assert_called_once(); store.complete.assert_not_called()

    def test_actual_controller_persists_review_before_admission_and_keeps_rejection(self):
        self.assert_repair_is_once_audited_and_reviewed()
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
        self.assert_failed_repair_and_lost_ownership_cannot_publish()
        from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait
        service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(return_value=packet()))
        planner.return_value = plan()
        wait = AIControlBudgetWait('call', '2026-10-02T00:00:00Z')
        service.reviewer = Mock(side_effect=wait)
        self.assertEqual('budget-wait', service.run_once()['status'])
        store.complete.assert_not_called(); store.fail.assert_not_called()
        store.defer_budget.assert_called_once()
        service.reviewer = Mock(side_effect=TimeoutError)
        self.assertEqual('completed', service.run_once()['status'])
        self.assertEqual('rejected', store.complete.call_args.args[1]['quality']['status'])
        legacy = plan(); legacy.pop('insightVersion')
        planner.return_value = legacy; service.reviewer.reset_mock()
        self.assertEqual('completed', service.run_once()['status'])
        service.reviewer.assert_not_called()
        self.assertEqual('rejected', store.complete.call_args.args[1]['quality']['status'])
