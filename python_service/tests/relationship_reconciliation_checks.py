"""Regressions for regrouping, native applicability and frozen comparisons."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch


def assert_lifecycle_regrouping(test):
    from test_hypothesis_lifecycle import MemoryLifecycleStore, relation_context, account_snapshot
    from digital_twin.modules.outcomes.application.hypothesis_lifecycle_service import HypothesisLifecycleService
    from digital_twin.modules.model_registry.contracts import relation_lifecycle_transition_contract
    store = MemoryLifecycleStore()
    service = HypothesisLifecycleService(store)
    first = relation_context('generation-1')
    first['graphStoreInference']['traces'][0]['claimContract'] = {'ruleFingerprint': 'fixture:original-claim'}
    module = 'digital_twin.modules.outcomes.application.hypothesis_lifecycle_service'
    with patch(module + '.relation_contexts_from_snapshot', return_value={'AAPL': first}):
        service.observe_snapshot(account_snapshot('generation-1'))
    old = next(iter(store.records.values()))
    original = deepcopy(old.to_dict())
    from digital_twin.modules.model_registry.contracts import record_for_absent_snapshot, regrouped_lifecycle_predecessors
    retained, transition = record_for_absent_snapshot(old, '2026-07-23T00:10:00Z',
        current_basis={'nativeRuleIds': old.snapshot['sourceRuleIds'], 'nativeGenerationId': 'generation-2'})
    test.assertIsNone(transition, 'a still-materialized native path cannot be closed by a missing read-model row')
    test.assertEqual(old.last_observed_at, retained.last_observed_at)
    test.assertEqual('native-path-still-present', retained.snapshot['pathReconciliation']['status'])
    test.assertEqual('generation-2', retained.snapshot['judgementRefresh']['inferenceGenerationId'])
    test.assertEqual('ready', retained.snapshot['judgementRefresh']['status'])
    held = replace(old, snapshot={**old.snapshot, 'judgementRefresh': {
        'status': 'awaiting-source', 'notificationAvailability': {'state': 'usable'}},
        'dataAvailability': {'state': 'unavailable', 'reasons': ['closed-market source unchanged']}})
    reconciled, notice = record_for_absent_snapshot(held, '2026-07-23T00:10:00Z',
        current_basis={'nativeRuleIds': old.snapshot['sourceRuleIds'], 'nativeGenerationId': 'generation-2'})
    test.assertIsNone(notice)
    test.assertEqual({'state': 'usable'}, reconciled.snapshot['judgementRefresh']['notificationAvailability'],
                     'healthy regrouping must preserve the receipt baseline and not invent outage recovery')
    second = deepcopy(relation_context('generation-2'))
    second['graphStoreInference']['traces'][0]['claimContract'] = {'ruleFingerprint': 'fixture:original-claim'}
    member = deepcopy(second['hypothesisSet']['hypotheses'][0])
    member.update(hypothesisId='hypothesis:new-member', supportingRuleIds=['rule.aapl.new.v1'],
                  causalSignature='different-native-path', marketCausalSignature='different-native-path',
                  supportingEvidenceIds=['evidence:new'])
    second['hypothesisSet']['hypotheses'].append(member)
    trace = deepcopy(second['graphStoreInference']['traces'][0])
    trace.update(ruleId='rule.aapl.new.v1', id='trace:new', evidenceRelationIds=['evidence:new'])
    second['graphStoreInference']['traces'].append(trace)
    with patch(module + '.relation_contexts_from_snapshot', return_value={'AAPL': second}):
        result = service.observe_snapshot(replace(account_snapshot('generation-2'), generated_at='2026-07-23T00:10:00Z'))
    test.assertNotIn('invalidated', [r['currentState'] for r in result['transitions']])
    test.assertEqual('superseded', store.records[old.lifecycle_key].state)
    test.assertEqual(original, old.to_dict(), 'a comparison view cannot mutate the frozen predecessor')
    active = [r for r in store.records.values() if r.state != 'superseded']
    test.assertEqual(1, len(active))
    test.assertEqual(old.first_observed_at, active[0].first_observed_at)
    test.assertEqual([old.lifecycle_key], active[0].snapshot['predecessorLifecycleKeys'])
    from digital_twin.modules.model_registry.domain.hypothesis_lifecycle import HypothesisLifecycleSnapshot
    successor = HypothesisLifecycleSnapshot.from_dict(active[0].snapshot)
    other_account = replace(old, account_id='another-account')
    test.assertEqual({}, regrouped_lifecycle_predecessors([successor], {old.lifecycle_key: other_account}))
    changed_claim = replace(successor, observation_basis={**successor.observation_basis,
        'nativeClaimFingerprints': {old.snapshot['sourceRuleIds'][0]: 'different-claim'}})
    test.assertEqual({}, regrouped_lifecycle_predecessors([changed_claim], {old.lifecycle_key: old}))
    archived = next(t for t in result['transitions'] if t['currentState'] == 'superseded')
    contract = relation_lifecycle_transition_contract({'transitions': [archived]})
    test.assertFalse(contract['material'])
    test.assertFalse(contract['deliverable'])
    # A -> A+B -> A compares the active family, rather than resurrecting an
    # archived alias as a newly observed market claim.
    third = deepcopy(first)
    third.update(inferenceGenerationId='generation-3', inferenceGenerationAt='2026-07-23T00:11:00Z')
    with patch(module + '.relation_contexts_from_snapshot', return_value={'AAPL': third}):
        result = service.observe_snapshot(replace(account_snapshot('generation-3'), generated_at='2026-07-23T00:11:00Z'))
    test.assertEqual('weakened', store.records[old.lifecycle_key].state)
    test.assertEqual(old.first_observed_at, store.records[old.lifecycle_key].first_observed_at)
    test.assertNotIn('invalidated', [r['currentState'] for r in result['transitions']])
    # Subsequent loss of the actual native path still closes the active record.
    with patch(module + '.relation_contexts_from_snapshot', return_value={}):
        result = service.observe_snapshot(replace(account_snapshot('generation-4'), generated_at='2026-07-23T00:12:00Z'))
    test.assertTrue(any(t['currentState'] == 'invalidated' for t in result['transitions']))
    assert_regrouped_delivery_continuity(test)


def assert_regrouped_delivery_continuity(test):
    from test_typedb_independent_observation import readable_fixture
    from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
    from digital_twin.modules.notifications.domain.rule_notification_event import customer_rule_events
    packet = relation_change_evidence(readable_fixture())
    current = packet['current']
    old_event = packet['customerEvents'][0]
    transition = deepcopy(current['transitions'][0])
    original_key = transition['lifecycleKey']
    transition.update(lifecycleKey='regrouped:verified', transitionId='transition:regrouped')
    transition['changeBasis'] = {**transition.get('changeBasis', {}), 'predecessorLifecycleKeys': [original_key]}
    previous = {k: current[k] for k in ('accountId', 'symbol', 'worldId')}
    previous.update(receiptJobId='receipt:original', deliveredAt=current.get('observedAt') or '2026-10-01T00:00:00Z',
        notificationIntentContinuity={old_event['intentKey']: {
            'lifecycleKey': original_key, 'rootJobId': 'receipt:original', 'purpose': old_event['purpose'],
            'intent': old_event['intent']}})
    original = deepcopy(previous)
    events, _ = customer_rule_events(current, previous, [transition])
    test.assertTrue(events)
    test.assertEqual('receipt:original', events[0]['parentJobId'])
    previous['accountId'] = 'other-account'
    events, _ = customer_rule_events(current, previous, [transition])
    test.assertEqual('', events[0]['parentJobId'], 'cross-account receipts cannot become a reply parent')
    previous['accountId'] = original['accountId']
    test.assertEqual(original, previous)


def assert_native_applicability(test):
    from types import SimpleNamespace
    from test_rulebox_decision_brief import fixture
    from digital_twin.modules.reasoning.domain.ontology_inference_materializer import action_policy_properties
    from digital_twin.modules.decisions.contracts import rulebox_decision_brief
    from digital_twin.modules.notifications.domain.rulebox_notification_summary import _conclusion
    context = fixture()
    relation = context['ontologyRelationContext']
    metadata = action_policy_properties(SimpleNamespace(), {'source': 'watchlist'})
    test.assertEqual({'ADD', 'TRIM', 'SELL'}, set(metadata['blockedActions']))
    row = relation['matchedRules'][0]
    row.update(metadata)
    relation['matchedRules'][1].update(candidateAction='BUY')
    case = context['investmentSubjectDecisionCase']
    case.update(allowedActions=['BUY', 'HOLD'], blockedActions=['ADD', 'TRIM', 'SELL'])
    relation['actionEnvelope']['blockedActions'] = ['ADD', 'TRIM', 'SELL']
    original = deepcopy(context)
    brief = rulebox_decision_brief(context)
    test.assertEqual([], brief['restrictions'], 'holding-only inapplicability is not an investment risk restriction')
    plan = brief['judgment']['responsePlan']
    test.assertEqual('qualification-pending', plan['primaryKind'])
    test.assertEqual({'ADD', 'TRIM', 'SELL'}, set(plan['notApplicableActions']))
    test.assertIsNone(plan['selectedMarketAction'])
    test.assertFalse(plan['orderAuthorized'])
    test.assertEqual(['BUY'], [g['action'] for g in plan['pendingCandidates']])
    test.assertNotIn('제한 적용', _conclusion(brief['judgment'], []))
    test.assertEqual(original, context)
    # A genuine holding policy stays an active constraint with the same code.
    row['actionApplicability'] = action_policy_properties(SimpleNamespace(), {'source': 'holding'})['actionApplicability']
    brief = rulebox_decision_brief(context)
    test.assertIn('ADD', brief['blockedActions'])
    test.assertEqual('restriction', brief['judgment']['responsePlan']['primaryKind'])


def assert_frozen_window_comparison(test):
    from digital_twin.modules.notifications.domain.rulebox_notification_summary import _window_measurement
    identity = 'stock:TEST|HAS_TEMPORAL_WINDOW|temporal-window:TEST:20D'
    condition = {'modelEvidenceIds': [identity], 'sourceFeatureSnapshotId': 'new', 'releaseId': 'model:fixed-definition',
                 'knowledgeCutoffAt': '2026-10-19T02:00:00Z'}
    older_condition = dict(condition, sourceFeatureSnapshotId='old', knowledgeCutoffAt='2026-10-19T01:00:00Z')
    current = {**condition, 'evidenceId': identity, 'symbol': 'TEST', 'windowKey': '20D',
               'hasSufficientHistory': True, 'priceChangePct': -22.42}
    old = {**current, **older_condition, 'priceChangePct': -20}
    source = deepcopy((current, old, condition, older_condition))
    line = _window_measurement(current, [old], condition, older_condition, 'TEST')
    test.assertIn('-20% → -22.42%', line)
    test.assertIn('직전 알림', line)
    equal = _window_measurement(current, [dict(old, priceChangePct=current['priceChangePct'])],
                                condition, older_condition, 'TEST')
    test.assertIn('이전 알림과 같음', equal)
    test.assertNotIn('→', equal)
    test.assertIn('수치 없음', _window_measurement(current, [], condition, {}, 'TEST'))
    bad = dict(old, sourceFeatureSnapshotId='wrong')
    test.assertNotIn('→', _window_measurement(current, [bad], condition, older_condition, 'TEST'))
    test.assertNotIn('→', _window_measurement(current, [old], condition,
        dict(older_condition, releaseId='different-calculation'), 'TEST'))
    future = dict(old, knowledgeCutoffAt='2026-10-19T03:00:00Z')
    test.assertNotIn('→', _window_measurement(current, [future], condition,
        dict(older_condition, knowledgeCutoffAt='2026-10-19T03:00:00Z'), 'TEST'))
    test.assertEqual('', _window_measurement(current, [old], condition, older_condition, 'OTHER'))
    test.assertEqual(source, (current, old, condition, older_condition))
