"""Mixed-condition regressions for the actual compact delivery boundary."""

from copy import deepcopy


def mixed_policy_packet(recovery, active):
    from digital_twin.modules.model_registry.domain.statistical_signals.measurements import measurement_packet
    packet = deepcopy(recovery)
    current = packet['current']
    brief = current['decisionBrief']
    active_brief = active['current']['decisionBrief']
    brief['managementDecision']['responses'] = deepcopy(active_brief['managementDecision']['responses'])
    brief['restrictions'] = deepcopy(active_brief['restrictions'])
    brief['blockedActions'] = ['ADD']
    judgment = brief['judgment']
    judgment['managementDecision'] = deepcopy(brief['managementDecision'])
    judgment['blockedActions'] = ['ADD']
    source = measurement_packet('market:decline', current['symbol'], 'model:test', 'feature:test',
        '2026-10-02T01:10:00Z', [{'conditionId': 'fall', 'evidenceId': 'stock:MSTR#window:20d',
            'field': 'recentPriceChangePct', 'observedValue': -5.4, 'expectedValue': 0, 'operator': '<',
            'context': {'windowKey': '20d'}, 'provenance': {'sourceAsOf': '2026-10-02T01:10:00Z'}}])
    judgment['evidenceComposition'] = {**{k: current[k] for k in
        ('accountId', 'symbol', 'worldId', 'sourceAboxSnapshotId', 'inferenceGenerationId')},
        'version': 'native-evidence-composition-v1', 'authority': 'typedb-materialized-synthesis',
        'evidenceGroups': [{'ruleId': 'market:decline', 'sourceMeasurements': source}]}
    return packet


def assert_policy_cause_survives_composition(test, recovery, active):
    from digital_twin.modules.notifications.domain.rulebox_notification_summary import compact_rulebox_summary
    from digital_twin.modules.notifications.domain.composed_rule_presentation import exact_measurements
    packet = mixed_policy_packet(recovery, active)
    frozen = deepcopy(packet)
    test.assertTrue(exact_measurements(packet['current']['decisionBrief']['judgment'], {}, packet['customerEvents'], 'USD'))
    summary = compact_rulebox_summary(packet)
    sections = {key: rows for key, _, rows in summary['sections']}
    values = ' '.join(sections['decision-impact'])
    test.assertIn('해제 기준 0% 이상', values)
    test.assertIn('평가 수익률', sections['decision-impact'][0])
    test.assertNotIn('78.45%', values)
    test.assertIn('78.45%', ' '.join(sections['remaining-policy']))
    test.assertNotIn('후반 구간', values)
    test.assertIn('추가매수 제한은 계속 적용', ' '.join(sections['decision-basis']))
    test.assertEqual(frozen, packet)
    assert_final_policy_document(test, packet)
    assert_policy_evidence_variants(test, packet)


def render_at_boundary(packet, *, document=None, text='cached body', extra=None):
    from unittest.mock import patch
    from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
    from digital_twin.modules.notifications.domain.notifications import NotificationJob
    context = {'relationChangeEvidence': deepcopy(packet), **(extra or {})}
    if document is not None:
        context['customerInvestmentDocument'] = deepcopy(document)
    job = NotificationJob.create(text, message_type='investmentInsight', context=context)
    # Exercise the last boundary even when the earlier formatter/cache claims
    # to have done its job. No DB, provider or model is involved.
    with patch.object(NotificationRenderingService, 'apply_investment_presentation_contract'):
        NotificationRenderingService().render(job)
    return job


def assert_final_policy_document(test, packet):
    from digital_twin.modules.read_models.contracts import CustomerInvestmentDocument, CustomerInvestmentSection
    wrong = CustomerInvestmentDocument(role='typedb-observation', headline='🔗 관리 기준 점검 · 예시',
        layout='compact-rulebox-v1', lead='추가매수 제한이 모두 해제됐습니다.',
        sections=(CustomerInvestmentSection('decision-impact', '핵심 수치', ('20일 가격 등락 -5.4%',)),)).to_dict()
    frozen = deepcopy(packet)
    job = render_at_boundary(packet, document=wrong)
    test.assertIn('해제 기준 0% 이상', job.text)
    test.assertIn('추가매수 제한은 계속 적용', job.text)
    test.assertNotIn('모두 해제', job.text)
    test.assertNotIn('20일 가격 등락', job.text)
    test.assertEqual('repaired', job.context['ruleNoticeEvidenceQuality']['status'])
    test.assertEqual('passed', job.context['customerInvestmentDocumentQuality']['status'])
    test.assertEqual(job.text, job.context['telegramMessage'])
    test.assertEqual(frozen, packet)
    test.assertEqual(frozen, job.context['relationChangeEvidence'])
    test.assertEqual('추가매수 제한이 모두 해제됐습니다.', wrong['lead'])
    # A document from the current version is still checked, not trusted by tag.
    good = job.context['customerInvestmentDocument']
    again = render_at_boundary(packet, document=good)
    test.assertEqual('passed', again.context['ruleNoticeEvidenceQuality']['status'])
    for location in ('lead', 'measurement', 'remaining'):
        damaged = deepcopy(good)
        if location == 'lead':
            damaged['lead'] = '추가매수 허용'
        else:
            key = 'decision-impact' if location == 'measurement' else 'decision-basis'
            next(s for s in damaged['sections'] if s['key'] == key)['rows'] = ['기준 0% 미만']
        repaired = render_at_boundary(packet, document=damaged)
        test.assertEqual('repaired', repaired.context['ruleNoticeEvidenceQuality']['status'], location)
        test.assertIn('해제 기준 0% 이상', repaired.text)
    # Successfully sent bytes and an interrupted multi-channel delivery remain immutable.
    for extra in ({'notificationReplayPreserveOriginal': True}, {'transportDelivery': {'message': 'original receipt'}}):
        replay = render_at_boundary(packet, document=wrong, text='original receipt', extra=extra)
        test.assertEqual('original receipt', replay.text)
        test.assertNotIn('ruleNoticeEvidenceQuality', replay.context)


def assert_policy_evidence_variants(test, packet):
    from digital_twin.modules.notifications.domain.rulebox_notification_summary import compact_rulebox_summary
    # Missing source data is never filled from the latest holding or another rule.
    for variant in ('missing-threshold', 'unproven-release', 'stale', 'foreign-scope', 'contradictory-response'):
        altered = deepcopy(packet)
        event = altered['customerEvents'][0]
        condition = next(c for c in event['conditions'] if c.get('field') == 'profitLossRate')
        if variant == 'missing-threshold':
            condition['resolvedThreshold']['value'] = None
        elif variant == 'unproven-release':
            condition['resolutionMatchedByTypeDB'] = False
        elif variant == 'stale':
            condition['freshnessStatus'] = 'stale'
        elif variant == 'foreign-scope':
            event['symbol'] = 'OTHER'
        else:
            altered['current']['decisionBrief']['managementDecision']['resolutions'][0]['observedValue'] = 999
        job = render_at_boundary(altered)
        test.assertEqual('partial', job.context['ruleNoticeEvidenceQuality']['status'], variant)
        test.assertNotIn('해제됐습니다', job.context['customerInvestmentDocument']['lead'])
        test.assertNotIn('해제.', job.context['customerInvestmentDocument']['lead'])
        test.assertEqual('failed', job.context['customerInvestmentDocumentQuality']['status'])
    # Prior observations must share both field and condition, never a numeric coincidence.
    no_prior = deepcopy(packet)
    for c in no_prior['customerEvents'][0]['previousConditions']:
        c['conditionId'] = 'different-condition'
    result = compact_rulebox_summary(no_prior)
    test.assertIn('이전 비교값 없음', str(result['sections']))
    test.assertIsNone(result['evidenceContract']['measurements'][0]['previousValue'])
    target = deepcopy(packet)
    next(c for c in target['customerEvents'][0]['previousConditions'] if c.get('field') == 'profitLossRate')['resolvedThreshold']['value'] = -1
    result = compact_rulebox_summary(target)
    test.assertIn('기준 변경 -1% → 0%', str(result['sections']))
    test.assertIn('시장 개선을 뜻하지 않습니다', str(result['sections']))
    # Tiny signed movements and a literal zero must survive presentation.
    for value in (0, 0.0001):
        changed = deepcopy(packet)
        next(c for c in changed['customerEvents'][0]['conditions'] if c.get('field') == 'profitLossRate')['observedValue'] = value
        changed['current']['decisionBrief']['managementDecision']['resolutions'][0]['observedValue'] = value
        job = render_at_boundary(changed)
        test.assertIn('해제 기준 0% 이상', job.text)
        if value:
            test.assertIn('+0.0001%', job.text)
    # Simultaneously changed market evidence is labelled; unrelated evidence is omitted.
    simultaneous = deepcopy(packet)
    simultaneous['customerEvents'].append({'sourceRuleIds': ['market:decline'], 'purpose': 'hypothesis-evidence'})
    result = compact_rulebox_summary(simultaneous)
    test.assertIn('함께 바뀐 근거:', str(result['sections']))
    test.assertIn('해제 기준 0% 이상', str(result['sections']))
    # All required causes survive the normalizer's general eight-row limit.
    many = deepcopy(packet)
    base_event = deepcopy(many['customerEvents'][0])
    base_rule = next(r for r in many['current']['rules'] if r['id'] in base_event['sourceRuleIds'])
    base_response = many['current']['decisionBrief']['managementDecision']['resolutions'][0]
    for n in range(1, 10):
        rid = 'test:policy:' + str(n)
        event = deepcopy(base_event); event.update(sourceRuleIds=[rid], eventId='event:' + str(n))
        next(c for c in event['conditions'] if c.get('field') == 'profitLossRate')['observedValue'] = n
        many['customerEvents'].append(event)
        many['current']['rules'].append({**deepcopy(base_rule), 'id': rid})
        many['current']['decisionBrief']['managementDecision']['resolutions'].append({**deepcopy(base_response), 'ruleId': rid, 'observedValue': n})
    job = render_at_boundary(many)
    impact = next(s for s in job.context['customerInvestmentDocument']['sections'] if s['key'] == 'decision-impact')
    test.assertEqual(10, len(impact['rows']))
    remaining = next(s for s in job.context['customerInvestmentDocument']['sections'] if s['key'] == 'remaining-policy')
    test.assertEqual(1, len(remaining['rows']))
    test.assertEqual(11, job.context['ruleNoticeEvidenceQuality']['requiredMeasurementCount'])
