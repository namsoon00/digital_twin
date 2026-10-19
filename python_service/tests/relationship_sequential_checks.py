"""Native premise closure, queue latency and causal notice regressions."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock


def assert_native_group_proof(test, packet):
    from digital_twin.infrastructure.typedb_ontology import typedb_static_rule_condition_context, typedb_native_matched_conditions
    from digital_twin.modules.model_registry.contracts import GraphInferenceRule, GraphRuleCondition
    from digital_twin.modules.reasoning.infrastructure.native_execution.context import typedb_rule_condition_context
    from digital_twin.modules.reasoning.domain.ontology_inference_materializer import grounded_inference_context
    from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology
    from digital_twin.modules.notifications.domain.rule_dispatch_validity import rule_dispatch_validity

    rule = SimpleNamespace(rule_id="fixture:any", any_condition_min_count=1, conditions=[
        GraphRuleCondition(condition_id="profile", description="Profile", kind="subject_property", field="investmentStrategyProfile", value="growth"),
        GraphRuleCondition(condition_id="price", description="Price", kind="subject_property", field="currentPrice", operator=">", value=100, role="any"),
        GraphRuleCondition(condition_id="other", description="Other", kind="subject_property", field="ma20Distance", operator=">", value=0, role="any")])
    store = SimpleNamespace(condition_detail_queries_enabled=lambda: False,
        read_rows=Mock(side_effect=[[{"sourceId": "stock:TEST"}], []]))
    bindings = SimpleNamespace(typedb_static_rule_condition_context=typedb_static_rule_condition_context,
                               typedb_native_matched_conditions=typedb_native_matched_conditions)
    proof_plan = {'conditionProofIndex': {'status': 'verified', 'index': {
        'sourceStorageIdsBySourceId': {'stock:TEST': 'node:TEST:immutable'},
        'relationStorageIdsBySymbolAndType': {}}}}
    context = typedb_rule_condition_context(store, rule, "stock:TEST", proof_plan, {"_anyConditionsVerified": True},
                                            "portfolio:test", _bindings=bindings)
    test.assertEqual(1, store.read_rows.call_count, "stop reading after the native group has sufficient witnesses")
    test.assertIn('node:TEST:immutable', store.read_rows.call_args.args[0])
    test.assertNotIn('abox-scope-pointer', store.read_rows.call_args.args[0])
    group = next(c for c in context["matchedConditions"] if c.get("kind") == "any-condition-group")
    test.assertEqual(["price"], group["matchedConditionIds"])
    test.assertFalse(group["detailDeferred"])
    independent = SimpleNamespace(rule_id='fixture:two', any_condition_min_count=2, conditions=[
        replace(rule.conditions[1], evidence_group_key='one-source'),
        replace(rule.conditions[1], condition_id='same-source-alias', evidence_group_key='one-source'),
        replace(rule.conditions[2], evidence_group_key='another-source')])
    independent_store = SimpleNamespace(condition_detail_queries_enabled=lambda: False,
        read_rows=Mock(return_value=[{'sourceId': 'stock:TEST'}]))
    independent_context = typedb_rule_condition_context(independent_store, independent, 'stock:TEST', proof_plan,
        {'_anyConditionsVerified': True}, 'portfolio:test', _bindings=bindings)
    test.assertEqual(2, independent_store.read_rows.call_count)
    test.assertEqual(['price', 'other'], next(c for c in independent_context['matchedConditions']
        if c.get('kind') == 'any-condition-group')['matchedConditionIds'], 'aliases do not supply independent witnesses')
    stock = OntologyEntity("stock:TEST", "Fixture", "stock", {"currentPrice": 105,
        "investmentStrategyProfile": "growth", "sourceAsOf": "2026-10-02T01:10:00Z",
        "freshnessStatus": "fresh", "freshnessAgeMinutes": 0, "judgementEvidenceUsable": True, "maxAgeMinutes": 10})
    graph = PortfolioOntology("fixture", entities=[stock])
    context = grounded_inference_context(graph, rule, stock, context)
    unindexed_store = SimpleNamespace(condition_detail_queries_enabled=lambda: False, read_rows=Mock())
    deferred = typedb_rule_condition_context(unindexed_store, rule, 'stock:TEST', {},
        {'_anyConditionsVerified': True}, 'portfolio:test', _bindings=bindings)
    unindexed_store.read_rows.assert_not_called()
    test.assertFalse(grounded_inference_context(graph, rule, stock, deferred)['evidenceUsableForJudgement'])
    profile = next(c for c in context["matchedConditions"] if c.get("conditionId") == "profile")
    test.assertFalse(profile["freshnessRequired"], "policy configuration cannot inherit quote expiry")
    test.assertEqual("not-applicable", profile["freshnessStatus"])
    test.assertEqual('', profile['observedAt'])
    test.assertEqual('account-policy', profile['source'])
    current = deepcopy(packet)
    rid = current["customerEvents"][0]["sourceRuleIds"][0]
    current["current"]["rules"] = [{"id": rid, "nativeProof": True, "conditions": context["matchedConditions"]}]
    clock = datetime(2026, 10, 2, 1, 11, tzinfo=timezone.utc)
    test.assertTrue(rule_dispatch_validity(current, now=clock)["allowed"])
    conflict = deepcopy(current)
    conflict['customerEvents'][0].update(changeKind='invalidated', evidenceChanges=[],
        ruleComparisons=[{'ruleId': rid, 'previous': {}, 'current': {'contractMatched': True}}])
    test.assertEqual('rule_dispatch_transition_conflict', rule_dispatch_validity(conflict, now=clock)['reasonCode'])
    for change in ("deferred", "missing-member", "stale-member", "unusable-member"):
        broken = deepcopy(current)
        rows = broken["current"]["rules"][0]["conditions"]
        marker = next(c for c in rows if c.get("kind") == "any-condition-group")
        witness = next(c for c in rows if c.get("conditionId") == "price")
        if change == "deferred": marker["detailDeferred"] = True
        elif change == "missing-member": rows.remove(witness)
        elif change == "stale-member": witness["freshnessStatus"] = "stale"
        else: witness["judgementEvidenceUsable"] = False
        with test.subTest(group_proof=change):
            test.assertFalse(rule_dispatch_validity(broken, now=clock)["allowed"])


def assert_independent_event_proof(test, flow):
    from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
    from digital_twin.modules.notifications.domain.relation_follow_up import delivery_snapshot
    from test_typedb_independent_observation import readable_fixture
    context = readable_fixture()
    relation = context["ontologyRelationContext"]
    transition = deepcopy(relation["hypothesisLifecycle"]["transitions"][0])
    original_rule = transition["sourceRuleIds"][0]
    transition.update(transitionId="missing-native-premise", lifecycleKey="missing-lifecycle")
    transition["sourceRuleIds"] = [original_rule, "missing-native-rule"]
    relation["hypothesisLifecycle"]["transitions"].append(transition)
    packet = relation_change_evidence(context)
    test.assertTrue(packet["eligible"])
    test.assertEqual(1, len(packet["customerEvents"]))
    test.assertTrue(any(r.get("reasonCode") == "rule_dispatch_proof_missing" for r in packet["suppressedTransitions"]))
    receipt = delivery_snapshot(packet, "verified-original", context["accountId"])
    test.assertTrue(all(v.get("lifecycleKey") != "missing-lifecycle" for v in receipt["notificationIntentContinuity"].values()))
    # A consolidated hypothesis can retain an older sibling claim. That
    # sibling cannot borrow a different rule's transition as its own cause.
    from digital_twin.modules.notifications.domain.rule_notification_event import customer_rule_events
    bad = deepcopy(packet['current'])
    transition = deepcopy(bad['transitions'][0])
    transition['sourceRuleIds'] = ['unrelated-rule']
    observed, _ = customer_rule_events(bad, {}, [transition])
    test.assertEqual([], observed)


def assert_current_state_slots(test):
    from digital_twin.shared_kernel.events import DomainEvent
    from digital_twin.modules.reasoning.domain.event_types import ONTOLOGY_REASONING_REQUESTED
    from digital_twin.modules.reasoning.domain.independent_reasoning import reasoning_queue_slot_key, merge_reasoning_events
    from digital_twin.modules.reasoning.domain.fact_changes import fact_change_contract
    def event(kind, at, account="fixture", symbol="TEST"):
        return DomainEvent(name=ONTOLOGY_REASONING_REQUESTED, aggregate_id=symbol, occurred_at=at, event_id=kind,
            payload={"accountIds": [account], "affectedSymbols": [symbol], "factTypes": [kind],
                "trigger": "verified-monitor-snapshot", "factChangeContract": fact_change_contract([kind], {symbol: [kind]}),
                "verifiedSourceSnapshot": {"accountId": account, "snapshotId": "snapshot:" + at,
                    "generatedAt": at, "sourceFactCoverageComplete": True}})
    price = event("MarketQuote", "2026-10-19T01:00:00Z")
    position = event("PortfolioSnapshot", "2026-10-19T01:02:00Z")
    original = deepcopy((price.to_dict(), position.to_dict()))
    key = reasoning_queue_slot_key(price, "REALTIME")
    test.assertEqual(key, reasoning_queue_slot_key(position, "CONTEXT"))
    merged = merge_reasoning_events([price, position])
    test.assertEqual(["MarketQuote", "PortfolioSnapshot"], merged.payload['factTypes'])
    test.assertEqual(position.payload['verifiedSourceSnapshot'], merged.payload['verifiedSourceSnapshot'])
    test.assertEqual({price.event_id, position.event_id}, set(merged.payload['coalescedReasoningChanges']['sourceEventIds']))
    test.assertEqual(original, (price.to_dict(), position.to_dict()))
    delayed_old_source = replace(price, occurred_at='2026-10-19T01:03:00Z')
    test.assertEqual(position.payload['verifiedSourceSnapshot'],
                     merge_reasoning_events([position, delayed_old_source]).payload['verifiedSourceSnapshot'],
                     'a late old event cannot roll the source boundary backward')
    for variant in (event("ResearchEvidence", position.occurred_at), event("ValuationObservation", position.occurred_at),
                    event("MarketQuote", position.occurred_at, account="other"),
                    event("MarketQuote", position.occurred_at, symbol="OTHER")):
        test.assertNotEqual(key, reasoning_queue_slot_key(variant, "CONTEXT"))
    for field, value in (("candidateValidationReplay", True), ("importanceGate", "non-fungible"),
                         ("workClass", "GOVERNANCE"), ("fullReconciliation", True)):
        variant = deepcopy(position); variant.payload[field] = value
        test.assertNotEqual(key, reasoning_queue_slot_key(variant, "CONTEXT"))
    legacy_research = event('LegacyResearchTransport', position.occurred_at)
    legacy_research.payload['factChangeContract'].update(status='ready', unclassifiedFactTypes=[], scopeFamilies=['evidence'])
    test.assertNotEqual(key, reasoning_queue_slot_key(legacy_research, 'CONTEXT'))


def assert_notice_cause_and_response(test, flow):
    from digital_twin.modules.notifications.domain.rule_policy_lifecycle import policy_lifecycle
    from digital_twin.modules.notifications.domain.rulebox_notification_summary import compact_rulebox_summary
    _, queue, _, _ = flow.engine_run()
    packet = deepcopy(queue.job.context['relationChangeEvidence'])
    packet['previous'] = deepcopy(packet['current'])
    event = {'eventId': 'native-hypothesis-change', 'purpose': 'hypothesis-evidence', 'changeKind': 'invalidated',
             'sourceRuleIds': ['hypothesis:failed-recovery'],
             'hypothesis': {'label': '반등 실패 가능성', 'expectedOutcome': '반등이 유지되지 못하고 이전 위험 경로로 복귀'},
             'nextCheck': {'condition': '회복 가격대와 거래 확인이 다음 관찰에서도 유지되는지'}}
    packet['customerEvents'] = [event]
    packet['policyLifecycle'] = policy_lifecycle(packet['current'], packet['previous'], [event])
    original = deepcopy(packet)
    summary = compact_rulebox_summary(packet)
    test.assertEqual('maintained', summary['decisionImpact']['status'])
    test.assertIn('규칙 조건이 더 이상 성립하지 않습니다', summary['lead'])
    test.assertNotIn('추가매수', summary['lead'])
    rows = {key: lines for key, _, lines in summary['sections']}
    test.assertIn('유지 중인 관리 기준', rows['decision-basis'][0])
    test.assertIn('회복 가격대', rows['next-check-contract'][0])
    test.assertEqual(original, packet, 'presentation cannot mutate receipt or judgment')
    # Without a delivered baseline, report only current policy, never claim
    # that an earlier notice already established it.
    packet['previous'] = {}
    packet['policyLifecycle'] = policy_lifecycle(packet['current'], {}, [event])
    summary = compact_rulebox_summary(packet)
    test.assertEqual('current-only', summary['decisionImpact']['status'])
    test.assertIn('현재 적용 중인 관리 기준', dict((key, lines) for key, _, lines in summary['sections'])['decision-basis'][0])
