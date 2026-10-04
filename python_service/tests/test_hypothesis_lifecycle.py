import sys
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.outcomes.application.hypothesis_lifecycle_service import HypothesisLifecycleService
from digital_twin.modules.decisions.application.investment_brain_service import InvestmentBrainService
from digital_twin.modules.model_registry.domain.hypothesis_lifecycle import (
    HypothesisLifecycleSnapshot,
    lifecycle_snapshots_from_relation_context,
    record_for_snapshot,
    record_for_absent_snapshot,
    relation_lifecycle_transition_contract,
    stable_fingerprint,
)
from digital_twin.modules.outcomes.domain.hypothesis_review import episode_matches_lifecycle
from digital_twin.modules.reasoning.domain.ontology_inference_context import (
    relation_context_from_inferencebox,
    relation_contexts_from_snapshot,
)
from digital_twin.modules.model_registry.domain.ontology_rulebox_contracts import (
    GraphInferenceRule,
    GraphRuleCondition,
    GraphRuleDerivation,
)
from digital_twin.modules.portfolio.domain.portfolio import AccountSnapshot, Position
from digital_twin.modules.portfolio.domain.portfolio_calculations import portfolio_summary
from digital_twin.modules.reasoning.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder


class MemoryLifecycleStore:
    def __init__(self):
        self.records = {}
        self.events = []
        self.subject_queries = []

    def current_for_subjects(self, account_id, symbols, lifecycle_key_prefix=""):
        allowed = {str(item or "").upper() for item in symbols or []}
        self.subject_queries.append(set(allowed))
        return {
            key: item
            for key, item in self.records.items()
            if item.symbol in allowed
            and (item.scope == "market" or item.account_id == str(account_id or ""))
            and (not lifecycle_key_prefix or item.lifecycle_key.startswith(lifecycle_key_prefix))
        }

    def list_current(self, account_id="", symbol="", market_id="", scope="", limit=100):
        rows = list(self.records.values())
        if account_id:
            rows = [item for item in rows if item.scope == "market" or item.account_id == account_id]
        if symbol:
            rows = [item for item in rows if item.symbol == str(symbol).upper()]
        if market_id:
            rows = [item for item in rows if item.market_id == market_id]
        if scope:
            rows = [item for item in rows if item.scope == scope]
        return rows[:limit]

    def list_events(self, account_id="", symbol="", lifecycle_key="", market_id="", scope="", limit=100):
        rows = list(self.events)
        if account_id:
            rows = [
                item for item in rows
                if item.record.get("accountId") == account_id or item.scope == "market"
            ]
        if symbol:
            rows = [item for item in rows if item.record.get("symbol") == str(symbol).upper()]
        if lifecycle_key:
            rows = [item for item in rows if item.lifecycle_key == lifecycle_key]
        if market_id:
            rows = [item for item in rows if item.record.get("marketId") == market_id]
        if scope:
            rows = [item for item in rows if item.scope == scope]
        return rows[:limit]

    def save(self, record, transition=None):
        self.records[record.lifecycle_key] = record
        if transition:
            self.events.append(transition)
        return record


class LifecycleProjectionProbe:
    def __init__(self):
        self.calls = []

    def current_summary_for_subjects(self, account_id, symbols, lifecycle_key_prefix=""):
        self.calls.append({
            "accountId": account_id,
            "symbols": set(symbols or []),
            "lifecycleKeyPrefix": lifecycle_key_prefix,
        })
        return {}


def lifecycle_snapshot(
    generation="generation-1",
    observed_at="2026-07-23T00:00:00Z",
    support=None,
    counter=None,
    paths=None,
    matched=None,
    policy=None,
    profiles=None,
):
    support = list(support or ["evidence:price"])
    counter = list(counter or [])
    paths = list(paths or ["trace:trend"])
    matched = list(matched or ["trend-below-ma20"])
    policy = dict(policy or {
        "formationConditionIds": ["trend-below-ma20"],
        "invalidationConditionIds": ["trend-recovered"],
        "validityMinutes": 0,
        "requiredFreshnessDomains": ["quote"],
        "nextDataRequirements": ["정규장 거래량"],
    })
    profiles = dict(profiles or {
        "quote": {
            "freshnessStatus": "fresh",
            "freshnessGateReason": "시세 기준시각이 최신입니다.",
        }
    })
    fingerprint = stable_fingerprint({
        "support": support,
        "counter": counter,
        "paths": paths,
        "matched": matched,
        "policy": policy,
    })
    return HypothesisLifecycleSnapshot(
        lifecycle_key="market:market-hypothesis-aapl-trend",
        lifecycle_id="market-hypothesis-aapl-trend",
        scope="market",
        market_world_id="market:US",
        market_id="US",
        symbol="AAPL",
        family_id="family:aapl-trend",
        hypothesis_ids=["hypothesis:aapl-trend"],
        source_rule_ids=["rule.aapl.trend.v1"],
        supporting_evidence_ids=support,
        counter_evidence_ids=counter,
        causal_path_ids=paths,
        formation_condition_ids=["trend-below-ma20"],
        matched_condition_ids=matched,
        policy=policy,
        observation_profiles=profiles,
        trace_freshness_statuses=["fresh"],
        inference_generation_id=generation,
        inference_generation_at=observed_at,
        observed_at=observed_at,
        semantic_fingerprint=fingerprint,
    )


def relation_context(generation="generation-1", support=None, counter=None, matched=None, policy=None):
    support = list(support or ["evidence:price"])
    counter = list(counter or [])
    matched = list(matched or ["trend-below-ma20"])
    policy = dict(policy or {
        "formationConditionIds": ["trend-below-ma20"],
        "invalidationConditionIds": ["trend-recovered"],
        "requiredFreshnessDomains": ["quote"],
        "nextDataRequirements": ["정규장 거래량"],
    })
    return {
        "accountId": "account-1",
        "portfolioWorldId": "portfolio:account-1",
        "marketWorldId": "market:US",
        "subject": {"symbol": "AAPL", "name": "Apple", "market": "US"},
        "inferenceGenerationId": generation,
        "inferenceGenerationAt": "2026-07-23T00:00:00Z",
        "observationProfiles": {
            "quote": {
                "freshnessStatus": "fresh",
                "freshnessGateReason": "시세 기준시각이 최신입니다.",
            }
        },
        "hypothesisSet": {
            "hypotheses": [{
                "hypothesisId": "hypothesis:aapl-trend",
                "marketHypothesisId": "market-hypothesis-aapl-trend",
                "familyId": "family:aapl-trend",
                "templateId": "hypothesis-template:rule.aapl.trend.v1",
                "causalSignature": "typedb-structural:aapl-trend",
                "marketCausalSignature": "typedb-market:aapl-trend",
                "supportingRuleIds": ["rule.aapl.trend.v1"],
                "supportingEvidenceIds": support,
                "counterEvidenceIds": counter,
                "causalPathIds": ["trace:aapl-trend"],
            }],
            "marketHypotheses": [{
                "marketHypothesisId": "market-hypothesis-aapl-trend",
                "marketWorldId": "market:US",
                "marketId": "US",
                "causalSignature": "typedb-market:aapl-trend",
            }],
            "accountOverlays": [],
        },
        "graphStoreInference": {
            "traces": [{
                "id": "trace:aapl-trend",
                "ruleId": "rule.aapl.trend.v1",
                "evidenceRelationIds": support,
                "matchedConditionIds": matched,
                "freshnessStatus": "fresh",
                "hypothesisLifecycle": policy,
            }],
        },
    }


def account_snapshot(generation="generation-1", targets=None, aligned=True, symbols=None):
    positions = [
        Position(
            symbol=symbol,
            name="Apple" if symbol == "AAPL" else symbol,
            market="US",
            currency="USD",
            source="watchlist",
            current_price=200,
            source_as_of="2026-07-23T00:00:00Z",
            source_fetched_at="2026-07-23T00:00:00Z",
        )
        for symbol in (symbols or ["AAPL"])
    ]
    return AccountSnapshot(
        "account-1",
        "테스트 계좌",
        "test",
        "live",
        "ok",
        "2026-07-23T00:00:00Z",
        portfolio_summary([], fx_rates={"USD": 1400}),
        watchlist=positions,
        metadata={
            "ontology": {
                "activeGraphStore": "typedb",
                "typedb": {
                    "inferenceBox": {
                        "status": "ok",
                        "nativeTypeDbReasoningUsed": True,
                        "generationAligned": aligned,
                        "inferenceGenerationId": generation,
                        "inferenceGenerationAt": "2026-07-23T00:00:00Z",
                        "targetSymbols": list(targets if targets is not None else ["AAPL"]),
                    },
                },
            },
        },
    )


class HypothesisLifecycleTests(unittest.TestCase):
    def test_unknown_evidence_count_changes_never_strengthen_or_weaken(self):
        previous = None
        for index, count in enumerate([1, 4, 1, 4, 1]):
            context = relation_context("generation-" + str(index), support=["unknown:%s:%s" % (index, n) for n in range(count)])
            snapshot = lifecycle_snapshots_from_relation_context(context)[0]
            previous, transition = record_for_snapshot(previous, snapshot)
            self.assertNotIn(previous.state, {"strengthened", "weakened"})
            if index:
                self.assertFalse(previous.material_change)
        # Previously queued v4 count-only transitions are rechecked as well.
        contract = relation_lifecycle_transition_contract({"transitions": [{
            "transitionId": "legacy:count-change", "currentState": "strengthened", "materialChange": True,
            "evidenceDelta": {"addedSupportingEvidenceKeys": ["support:unresolved-slot:rule:2"]},
        }]})
        self.assertFalse(contract["material"])

    def test_source_assertions_resolve_across_freshness_and_id_rotation(self):
        previous = None
        support_keys = None
        for index, stale in enumerate([False, True, False, True]):
            identity = "assertion:" + str(index)
            context = relation_context("generation-" + str(index), support=[identity])
            trace = context["graphStoreInference"]["traces"][0]
            trace.update(evidenceUsableForJudgement=not stale, freshnessStatus="stale" if stale else "fresh")
            trace["matchedConditions"] = [{"conditionId": "trend-below-ma20", "relationId": identity,
                "relationType": "HAS_MODEL_SIGNAL", "observedValue": {"strengthBand": "strong"},
                "judgementEvidenceUsable": not stale}]
            if stale:
                context["hypothesisSet"]["hypotheses"][0]["supportingEvidenceIds"] = ["relation-evidence:" + str(n) for n in range(3)]
                context["graphStoreInference"]["traces"].append({
                    "ruleId": "unrelated-account-policy", "evidenceRelationIds": [identity],
                    "matchedConditions": [{"conditionId": "unrelated-condition", "relationId": identity}],
                })
            snapshot = lifecycle_snapshots_from_relation_context(context)[0]
            support_keys = support_keys or snapshot.supporting_evidence_keys
            self.assertEqual(support_keys, snapshot.supporting_evidence_keys)
            self.assertFalse(any("unresolved-slot" in key for key in support_keys))
            previous, transition = record_for_snapshot(previous, snapshot)
            if index:
                self.assertEqual("maintained", previous.state)
                self.assertFalse(previous.material_change)
                contract = relation_lifecycle_transition_contract({"transitions": [transition.to_dict()]})
                self.assertEqual("data-availability", contract["changeCategory"])
                self.assertTrue(contract["deliverable"])
                self.assertFalse(contract["material"])
        # Upgrading a legacy record without availability metadata still exposes
        # its first data issue once, without fabricating market weakening.
        legacy = replace(previous, snapshot={**previous.snapshot, "dataAvailability": {}})
        migrated, transition = record_for_snapshot(legacy, replace(snapshot, inference_generation_id="migration"))
        self.assertFalse(migrated.material_change)
        self.assertEqual("data-unavailable", transition.data_availability_change["kind"])
        _, duplicate = record_for_snapshot(migrated, replace(snapshot, inference_generation_id="after-migration"))
        self.assertIsNone(duplicate)

    def test_new_verified_support_and_counter_evidence_remain_material(self):
        first = relation_context()
        first["graphStoreInference"]["relations"] = [{"id": "evidence:price", "semanticKey": "price-path"}]
        previous, _ = record_for_snapshot(None, lifecycle_snapshots_from_relation_context(first)[0])
        second = deepcopy(first)
        second["inferenceGenerationId"] = "generation-2"
        second["hypothesisSet"]["hypotheses"][0]["supportingEvidenceIds"].append("evidence:filing")
        second["graphStoreInference"]["relations"].append({"id": "evidence:filing", "semanticKey": "verified-document-revision-1"})
        strengthened, _ = record_for_snapshot(previous, lifecycle_snapshots_from_relation_context(second)[0])
        self.assertEqual("strengthened", strengthened.state)
        third = deepcopy(second)
        third["inferenceGenerationId"] = "generation-3"
        third["hypothesisSet"]["hypotheses"][0]["counterEvidenceIds"] = ["evidence:counter"]
        third["graphStoreInference"]["relations"].append({"id": "evidence:counter", "semanticKey": "verified-counter-document"})
        weakened, transition = record_for_snapshot(strengthened, lifecycle_snapshots_from_relation_context(third)[0])
        self.assertEqual("weakened", weakened.state)
        self.assertTrue(transition.material_change)
        # Counter assertions are resolved through their own opposing rule,
        # which is distinct from the hypothesis's supporting rule set.
        native_counter = deepcopy(second)
        native_counter["inferenceGenerationId"] = "native-counter-generation"
        native_counter["hypothesisSet"]["hypotheses"][0].update(
            counterEvidenceIds=["assertion:counter"], counterRuleIds=["opposing-rule"])
        native_counter["graphStoreInference"]["traces"].append({
            "ruleId": "opposing-rule", "evidenceRelationIds": ["assertion:counter"],
            "matchedConditions": [{"conditionId": "verified-counter", "relationId": "assertion:counter", "relationType": "HAS_MODEL_SIGNAL"}],
        })
        weakened, transition = record_for_snapshot(strengthened, lifecycle_snapshots_from_relation_context(native_counter)[0])
        self.assertEqual("weakened", weakened.state)
        self.assertTrue(transition.material_change)
        self.assertEqual("opposing-rule", transition.evidence_changes[0]["evidence"]["ruleId"])

    def test_unavailable_data_preserves_hypothesis_and_real_disappearance_invalidates(self):
        snapshot = lifecycle_snapshots_from_relation_context(relation_context())[0]
        previous, _ = record_for_snapshot(None, snapshot)
        unavailable, transition = record_for_absent_snapshot(previous, "2026-07-23T00:01:00Z", "시세 원천 자료 미확인")
        self.assertEqual("maintained", unavailable.state)
        contract = relation_lifecycle_transition_contract({"transitions": [transition.to_dict()]})
        self.assertEqual("data-unavailable", contract["changeKind"])
        self.assertFalse(contract["material"])
        same, duplicate = record_for_absent_snapshot(unavailable, "2026-07-23T00:02:00Z", "시세 원천 자료 미확인")
        self.assertIsNone(duplicate)
        restored, transition = record_for_snapshot(same, replace(snapshot, inference_generation_id="generation-2"))
        self.assertEqual("maintained", restored.state)
        self.assertEqual("data-restored", relation_lifecycle_transition_contract({"transitions": [transition.to_dict()]})["changeKind"])
        invalidated, transition = record_for_absent_snapshot(restored, "2026-07-23T00:03:00Z")
        self.assertEqual("invalidated", invalidated.state)
        self.assertTrue(relation_lifecycle_transition_contract({"transitions": [transition.to_dict()]})["material"])

    def test_rulebox_resolves_formation_policy_for_bootstrap_rules(self):
        rule = GraphInferenceRule(
            rule_id="rule.lifecycle.test.v1",
            label="수명주기 테스트",
            version="test",
            source_kind="stock",
            conditions=[
                GraphRuleCondition("required-condition", "subject_property", "필수 조건", field="source", value="holding"),
                GraphRuleCondition("optional-condition", "subject_property", "선택 조건", field="sector", value="IT", role="optional"),
            ],
            derivations=[
                GraphRuleDerivation(
                    relation_type="REQUIRES_NEXT_CHECK",
                    target_kind="next-check",
                    target_key="{symbol}:check",
                    target_label="다음 확인",
                    tbox_class="NextCheck",
                    decision_stage="CHECK",
                )
            ],
            action_group="watch",
            action_level="review",
            prompt_hint="테스트",
        )

        payload = rule.to_dict()
        self.assertEqual(["required-condition"], payload["hypothesis_lifecycle"]["formationConditionIds"])
        outcome_contract = payload["hypothesis_lifecycle"]["outcomeContract"]
        self.assertEqual([60, 1440, 10080], outcome_contract["outcomeHorizonMinutes"])
        self.assertEqual(3, outcome_contract["minimumIndependentEpisodes"])
        graph = rulebox_graph_from_rules([rule], include_tbox=False)
        rule_node = next(item for item in graph.entities if item.kind == "rule")
        self.assertEqual(
            ["required-condition"],
            rule_node.properties["hypothesisLifecycle"]["formationConditionIds"],
        )

    def test_market_hypothesis_is_not_duplicated_as_private_lifecycle(self):
        snapshots = lifecycle_snapshots_from_relation_context(relation_context())

        self.assertEqual(1, len(snapshots))
        self.assertEqual("market", snapshots[0].scope)
        self.assertEqual("", snapshots[0].account_id)
        self.assertEqual(["trend-below-ma20"], snapshots[0].policy["formationConditionIds"])
        self.assertEqual(["정규장 거래량"], snapshots[0].policy["nextDataRequirements"])

    def test_stable_lifecycle_key_ignores_generation_local_hypothesis_ids(self):
        first = lifecycle_snapshots_from_relation_context(relation_context("generation-1"))[0]
        changed = deepcopy(relation_context("generation-2"))
        changed["hypothesisSet"]["hypotheses"][0]["hypothesisId"] = "hypothesis:next-generation"
        changed["hypothesisSet"]["hypotheses"][0]["marketHypothesisId"] = "market-hypothesis-next-generation"
        changed["hypothesisSet"]["marketHypotheses"][0]["marketHypothesisId"] = "market-hypothesis-next-generation"
        second = lifecycle_snapshots_from_relation_context(changed)[0]

        self.assertTrue(first.lifecycle_key.startswith("v2:market:"))
        self.assertEqual(first.lifecycle_key, second.lifecycle_key)
        self.assertEqual(first.semantic_fingerprint, second.semantic_fingerprint)
        observed, _ = record_for_snapshot(None, first, first.observed_at)
        maintained, transition = record_for_snapshot(observed, second, second.observed_at)
        self.assertEqual("maintained", maintained.state)
        self.assertEqual("observed", transition.previous_state)

    def test_relation_and_trace_id_rotation_preserves_semantic_evidence_keys(self):
        first_context = relation_context("generation-1")
        first = lifecycle_snapshots_from_relation_context(first_context)[0]
        observed, _ = record_for_snapshot(None, first, first.observed_at)
        maintained, _ = record_for_snapshot(
            observed,
            replace(first, inference_generation_id="generation-2", observed_at="2026-07-23T00:01:00Z"),
        )
        changed = deepcopy(relation_context("generation-3"))
        hypothesis = changed["hypothesisSet"]["hypotheses"][0]
        hypothesis["supportingEvidenceIds"] = ["evidence:price:generation-3"]
        hypothesis["causalPathIds"] = ["trace:aapl-trend:generation-3"]
        trace = changed["graphStoreInference"]["traces"][0]
        trace["id"] = "trace:aapl-trend:generation-3"
        trace["evidenceRelationIds"] = ["evidence:price:generation-3"]
        second = lifecycle_snapshots_from_relation_context(changed)[0]

        rotated, transition = record_for_snapshot(maintained, second, second.observed_at)

        self.assertEqual(first.supporting_evidence_keys, second.supporting_evidence_keys)
        self.assertEqual(first.causal_path_keys, second.causal_path_keys)
        self.assertEqual("maintained", rotated.state)
        self.assertFalse(rotated.material_change)
        self.assertIsNone(transition)

    def test_outcome_review_matches_a_stable_key_after_source_ids_change(self):
        first = lifecycle_snapshots_from_relation_context(relation_context("generation-1"))[0]
        record, _ = record_for_snapshot(None, first, first.observed_at)
        changed = deepcopy(relation_context("generation-2"))
        changed["hypothesisSet"]["hypotheses"][0]["hypothesisId"] = "hypothesis:next-generation"
        changed["hypothesisSet"]["hypotheses"][0]["marketHypothesisId"] = "market-hypothesis-next-generation"
        changed["hypothesisSet"]["marketHypotheses"][0]["marketHypothesisId"] = "market-hypothesis-next-generation"
        episode = {
            "accountId": "account-1",
            "symbol": "AAPL",
            "marketId": "US",
            "selectedHypothesisId": "hypothesis:next-generation",
            "hypothesisSet": changed["hypothesisSet"],
        }

        self.assertTrue(episode_matches_lifecycle(episode, record.to_dict()))

    def test_generation_local_id_rotation_is_not_a_material_weakening(self):
        observed, _ = record_for_snapshot(None, lifecycle_snapshot())
        maintained, _ = record_for_snapshot(
            observed,
            lifecycle_snapshot(generation="generation-2", observed_at="2026-07-23T00:01:00Z"),
        )
        rotated_snapshot = lifecycle_snapshot(
            generation="generation-3",
            observed_at="2026-07-23T00:02:00Z",
            support=["evidence:new-generation-price"],
            paths=["trace:new-generation-trend"],
        )

        rotated, transition = record_for_snapshot(maintained, rotated_snapshot)

        self.assertEqual("maintained", rotated.state)
        self.assertFalse(rotated.material_change)
        self.assertIsNone(transition)
        self.assertEqual(
            ["evidence:new-generation-price"],
            rotated.evidence_delta["rotatedAddedSupportingEvidenceIds"],
        )

        legacy_observed, _ = record_for_snapshot(
            None,
            lifecycle_snapshot(
                counter=["counter:legacy:1", "counter:legacy:2", "counter:legacy:3"],
            ),
        )
        legacy_maintained, _ = record_for_snapshot(
            legacy_observed,
            lifecycle_snapshot(
                generation="generation-2",
                observed_at="2026-07-23T00:01:00Z",
                counter=["counter:legacy:1", "counter:legacy:2", "counter:legacy:3"],
            ),
        )
        migrated, migration_transition = record_for_snapshot(
            legacy_maintained,
            lifecycle_snapshot(
                generation="generation-3",
                observed_at="2026-07-23T00:02:00Z",
                counter=[],
            ),
        )

        self.assertEqual("maintained", migrated.state)
        self.assertFalse(migrated.material_change)
        self.assertIsNone(migration_transition)
        self.assertTrue(
            all(
                ":migration-slot:" in item
                for item in migrated.evidence_delta["removedCounterEvidenceKeys"]
            )
        )
        migration_contract = relation_lifecycle_transition_contract({
            "hypothesisLifecycle": {
                "transitions": [{
                    "transitionId": "transition:migration-only",
                    "previousState": "weakened",
                    "currentState": "strengthened",
                    "materialChange": True,
                    "evidenceDelta": migrated.evidence_delta,
                }],
            },
        })
        self.assertFalse(migration_contract["material"])
        self.assertEqual(0, migration_contract["materialTransitionCount"])

    def test_lifecycle_expires_when_required_freshness_or_validity_fails(self):
        first = lifecycle_snapshot(policy={
            "formationConditionIds": ["trend-below-ma20"],
            "validityMinutes": 5,
            "requiredFreshnessDomains": ["quote"],
        })
        observed, _ = record_for_snapshot(None, first)
        expired_snapshot = replace(first, inference_generation_id="generation-2")
        expired, _ = record_for_snapshot(observed, expired_snapshot, "2026-07-23T00:06:00Z")
        self.assertEqual("expired", expired.state)
        self.assertIn("유효기간", expired.transition_reason)

        stale = lifecycle_snapshot(profiles={
            "quote": {
                "freshnessStatus": "stale",
                "freshnessGateReason": "시세가 오래되었습니다.",
            }
        })
        stale_record, _ = record_for_snapshot(None, stale)
        self.assertEqual("expired", stale_record.state)
        self.assertIn("시세가 오래되었습니다", stale_record.transition_reason)

    def test_service_invalidates_only_with_explicit_healthy_target_coverage(self):
        store = MemoryLifecycleStore()
        service = HypothesisLifecycleService(store)
        first_snapshot = account_snapshot("generation-1")
        with mock.patch(
            "digital_twin.modules.outcomes.application.hypothesis_lifecycle_service.relation_contexts_from_snapshot",
            return_value={"AAPL": relation_context("generation-1")},
        ):
            first_result = service.observe_snapshot(first_snapshot)
        self.assertEqual("ok", first_result["status"])
        self.assertEqual("observed", next(iter(store.records.values())).state)

        missing_targets_snapshot = account_snapshot("generation-2", targets=[])
        with mock.patch(
            "digital_twin.modules.outcomes.application.hypothesis_lifecycle_service.relation_contexts_from_snapshot",
            return_value={},
        ):
            missing_targets_result = service.observe_snapshot(missing_targets_snapshot)
        self.assertEqual("ok", missing_targets_result["status"])
        self.assertEqual("observed", next(iter(store.records.values())).state)

        covered_snapshot = account_snapshot("generation-3", targets=["AAPL"])
        with mock.patch(
            "digital_twin.modules.outcomes.application.hypothesis_lifecycle_service.relation_contexts_from_snapshot",
            return_value={},
        ):
            covered_result = service.observe_snapshot(covered_snapshot)
        self.assertEqual(1, covered_result["transitionCount"])
        self.assertEqual(1, len(covered_result["transitions"]))
        self.assertEqual(
            "invalidated",
            covered_result["bySymbol"]["AAPL"]["transitions"][0]["currentState"],
        )
        self.assertEqual("invalidated", next(iter(store.records.values())).state)

        resolved_context = relation_contexts_from_snapshot(covered_snapshot)["AAPL"]
        lifecycle_transition = relation_lifecycle_transition_contract(resolved_context)
        self.assertTrue(resolved_context["relationLifecycleOnly"])
        self.assertEqual("resolved", lifecycle_transition["changeKind"])
        self.assertEqual("NO_ACTION", resolved_context["decision"]["candidateAction"])
        self.assertEqual([], resolved_context["activeRules"])

        recovered_snapshot = account_snapshot("generation-4", targets=["AAPL"])
        with mock.patch(
            "digital_twin.modules.outcomes.application.hypothesis_lifecycle_service.relation_contexts_from_snapshot",
            return_value={"AAPL": relation_context("generation-4")},
        ):
            recovered_result = service.observe_snapshot(recovered_snapshot)
        recovered_transition = recovered_result["bySymbol"]["AAPL"]["transitions"][0]
        self.assertEqual("invalidated", recovered_transition["previousState"])
        self.assertEqual("observed", recovered_transition["currentState"])
        self.assertEqual("observed", next(iter(store.records.values())).state)

        unaligned_snapshot = account_snapshot("generation-5", aligned="false")
        result = service.observe_snapshot(unaligned_snapshot)
        self.assertEqual("skipped-unhealthy-inference", result["status"])
        self.assertEqual("observed", next(iter(store.records.values())).state)

    def test_service_reconciles_only_explicit_target_symbols(self):
        store = MemoryLifecycleStore()
        service = HypothesisLifecycleService(store)
        first_snapshot = account_snapshot("generation-1")
        with mock.patch(
            "digital_twin.modules.outcomes.application.hypothesis_lifecycle_service.relation_contexts_from_snapshot",
            return_value={"AAPL": relation_context("generation-1")},
        ):
            service.observe_snapshot(first_snapshot)
        aapl_key = next(iter(store.records))
        msft_snapshot = replace(
            lifecycle_snapshot(),
            lifecycle_key="v2:market:msft-audit",
            lifecycle_id="market-hypothesis-msft",
            symbol="MSFT",
            family_id="family:msft-trend",
        )
        msft_record, _ = record_for_snapshot(None, msft_snapshot)
        store.save(msft_record)

        target_snapshot = account_snapshot(
            "generation-2",
            targets=["AAPL"],
            symbols=["AAPL", "MSFT"],
        )
        with mock.patch(
            "digital_twin.modules.outcomes.application.hypothesis_lifecycle_service.relation_contexts_from_snapshot",
            return_value={},
        ):
            result = service.observe_snapshot(target_snapshot)

        self.assertEqual(["AAPL"], result["reconciledSymbols"])
        self.assertEqual({"AAPL"}, store.subject_queries[-1])
        self.assertEqual("invalidated", store.records[aapl_key].state)
        self.assertEqual("observed", store.records[msft_record.lifecycle_key].state)
        self.assertNotIn("MSFT", result["bySymbol"])

    def test_lifecycle_reaches_ai_context_and_web_read_model(self):
        position = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            current_price=200,
            ma20=190,
            source="watchlist",
        )
        inferencebox = {
            "status": "ok",
            "graphStore": "typedb",
            "nativeTypeDbReasoningUsed": True,
            "generationAligned": True,
            "inferenceGenerationId": "generation-1",
            "inferenceGenerationAt": "2026-07-23T00:00:00Z",
            "relations": [{
                "id": "relation:aapl-trend",
                "source": "stock:AAPL",
                "target": "risk:aapl-trend",
                "type": "HAS_INFERRED_RISK",
                "symbol": "AAPL",
                "ruleId": "rule.aapl.trend.v1",
                "polarity": "risk",
                "evidenceRole": "risk",
                "reviewLevel": "check",
                "dataState": "sufficient",
                "decisionStage": "HOLD_REVIEW",
                "nativeTypeDbReasoned": True,
            }],
            "traces": [{
                "id": "trace:aapl-trend",
                "symbol": "AAPL",
                "ruleId": "rule.aapl.trend.v1",
                "matchedConditionIds": ["trend-below-ma20"],
                "matchedConditions": [{
                    "conditionId": "trend-below-ma20",
                    "kind": "subject_property",
                    "field": "ma20Distance",
                    "role": "required",
                }],
                "nativeTypeDbReasoned": True,
            }],
        }
        lifecycle = {
            "records": [{
                "lifecycleKey": "market:market-hypothesis-aapl-trend",
                "state": "strengthened",
                "transitionReason": "새 거래량 근거가 추가되었습니다.",
            }],
            "activeCount": 1,
        }
        context = relation_context_from_inferencebox(
            position,
            portfolio_summary([], fx_rates={"USD": 1400}),
            inferencebox,
            hypothesis_lifecycle=lifecycle,
        )
        self.assertEqual(lifecycle, context["hypothesisLifecycle"])
        self.assertEqual(lifecycle, context["promptContext"]["hypothesisLifecycle"])

        store = MemoryLifecycleStore()
        record, transition = record_for_snapshot(None, lifecycle_snapshot())
        store.save(record, transition)
        brain = InvestmentBrainService(None, None, None, None, hypothesis_lifecycle_store=store)
        payload = brain.hypothesis_lifecycles(account_id="account-1", symbol="AAPL")
        self.assertEqual("ok", payload["status"])
        self.assertEqual(1, payload["count"])
        self.assertEqual(1, payload["eventCount"])


if __name__ == "__main__":
    unittest.main()
