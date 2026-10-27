"""Exact measurements, native combination authority and concise delivery."""

from copy import deepcopy
from dataclasses import replace
import unittest

from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import (
    default_graph_inference_rules, governed_graph_inference_rules,
)
from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import rulebox_semantic_violations
from digital_twin.modules.model_registry.domain.statistical_signals.graph_scoring import score_graph_hypothesis_contracts
from digital_twin.modules.model_registry.domain.statistical_signals.measurements import measurement_packet, verified_measurement_packet
from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.modules.reasoning.domain.portfolio_ontology_statistical_concepts import add_position_statistical_signal_concepts
from digital_twin.modules.decisions.domain.investment_reasoning.evidence_composition import native_evidence_composition
from digital_twin.modules.notifications.domain.composed_rule_presentation import exact_measurements, composition_explanation
from digital_twin.modules.reasoning.infrastructure.typeql import typedb_native_match_query
from test_statistical_signals import feature_snapshot
from functools import lru_cache


RATE = "graph.macro.rate.fall.confirmed_support.v1"
RELATIVE = "graph.market_proxy.relative_resilience.support.v1"
AT = "2026-08-10T07:00:00Z"
IDENTITY = dict(accountId="account-1", symbol="NVDA", worldId="world:test",
                inferenceGenerationId="generation:1", sourceAboxSnapshotId="abox:1")


def scored_nodes():
    graph = PortfolioOntology("world:test")
    graph.entities.append(OntologyEntity("stock:NVDA", "Nvidia", "stock",
        dict(symbol="NVDA", source="holding", priceChangeRate=.5, ma20Distance=-1, sourceAsOf=AT)))
    for kind, rel, props in [
        ("factor-sensitivity", "HAS_FACTOR_SENSITIVITY", dict(factor="rate", sensitivityLevel="high")),
        ("interest-rate", "HAS_RATE_SENSITIVITY", dict(rateSeriesId="DGS10", delta5dBp=-21, sourceAsOf="2026-08-07")),
        ("relative-performance-observation", "HAS_RELATIVE_PERFORMANCE",
         dict(dataState="sufficient", proxySymbol="IEF", comparisonWindow="session-change",
              proxyChangeRate=-2.5, stockChangeRate=.5, relativeReturnPct=3, sourceAsOf=AT, proxySourceAsOf=AT)),
    ]:
        entity = OntologyEntity(kind + ":NVDA", kind, kind, props)
        graph.entities.append(entity)
        graph.relations.append(OntologyRelation("stock:NVDA", entity.entity_id, rel))
    rules = [r for r in governed_graph_inference_rules() if r.rule_id in {RATE, RELATIVE}]
    snapshots = score_graph_hypothesis_contracts(graph, feature_snapshot(), rules)
    bundle = next(s.to_dict() for s in snapshots if s.signals)
    projected = PortfolioOntology("world:test")
    add_position_statistical_signal_concepts(projected, "stock:NVDA", "NVDA",
                                             {"statisticalSignalSnapshot": bundle})
    return bundle, [e.properties for e in projected.entities if e.kind == "statistical-model-hypothesis-evidence"]


def relation(nodes):
    return {"matchedRules": [], "graphStoreInference": {"traces": [
        {"ruleId": n["hypothesisContractId"], "matched": True, "matchedConditions": [
            {"conditionId": "signal", "matchedByTypeDB": True, "judgementEvidenceUsable": True,
             "freshnessStatus": "fresh", "matchedTargetProperties": deepcopy(n)}]}
        for n in nodes]}}


def verify(packet):
    return verified_measurement_packet(packet, rule_id=packet["hypothesisContractId"],
        symbol="NVDA", release_id=packet["modelReleaseId"],
        feature_snapshot_id=packet["sourceFeatureSnapshotId"], cutoff=AT)


@lru_cache(maxsize=1)
def migration_artifacts():
    from digital_twin.modules.reasoning.infrastructure.static_seed.artifact import ontology_release_seed_artifact
    from digital_twin.modules.reasoning.infrastructure.static_seed.composition import composition_release_artifact
    from digital_twin.modules.market_data.contracts import payload_fingerprint
    old = ontology_release_seed_artifact([r for r in default_graph_inference_rules() if not r.rule_id.startswith("graph.composition.")])
    new = composition_release_artifact(old, "baseline")
    return [{"valid": True, "artifact": a, "artifactFingerprint": payload_fingerprint(a),
             "ruleboxFingerprint": a["ruleboxFingerprint"]} for a in (old, new)]


def migration_jobs():
    from test_management_upgrade_review import fixtures, comparison
    left, right, _ = fixtures()
    right["result"]["decision_syntheses"][0].pop("management_decision")
    rid = "graph.composition.opposing_market.v1"
    right["result"]["decision_syntheses"][0]["rulebox_judgment"]["sourceRuleIds"].append(rid)
    right["result"]["projection_results"]["acct"]["ruleboxExecution"]["typedbNativeRuleMatchedRuleIds"].append(rid)
    return left, right, comparison(left, right)


class RuleEvidenceCompositionTests(unittest.TestCase):
    def test_native_grounding_uses_source_clock_across_a_queue_expiry_boundary(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from digital_twin.modules.market_data.domain import data_freshness
        from digital_twin.modules.reasoning.domain.ontology_inference_materializer import grounded_inference_context

        rule = next(r for r in default_graph_inference_rules()
                    if r.rule_id == "graph.instrument_profile.bitcoin_sensitive.crypto_linkage.v1")
        stock = OntologyEntity("stock:MSTR", "Strategy", "stock",
                               {"source": "holding", "freshnessRequired": False})
        archetype = OntologyEntity("archetype:MSTR", "Bitcoin proxy", "investment-archetype",
                                   {"instrumentArchetype": "BitcoinProxy", "freshnessRequired": False})
        crypto = OntologyEntity("crypto:MSTR", "BTC exposure", "crypto-exposure",
                                {"cryptoSymbol": "BTC", "sourceAsOf": "2026-10-20T12:00:00Z",
                                 "maxAgeMinutes": 25})
        graph = PortfolioOntology("fixture", entities=[stock, archetype, crypto], relations=[
            OntologyRelation(stock.entity_id, archetype.entity_id, "HAS_ARCHETYPE",
                             properties={"_relationId": "relation:archetype"}),
            OntologyRelation(stock.entity_id, crypto.entity_id, "HAS_CRYPTO_EXPOSURE",
                             properties={"_relationId": "relation:crypto"}),
        ])
        graph.worldview["evidenceEvaluationAsOf"] = "2026-10-20T12:10:00Z"
        native_proof = {"matchedConditions": [
            {"conditionId": c.condition_id, "matchedByTypeDB": True} for c in rule.conditions]}
        results = []
        for execution_minute in (24, 26):
            with patch.object(data_freshness, "datetime", wraps=datetime) as clock:
                clock.now.return_value = datetime(2026, 10, 20, 12, execution_minute, tzinfo=timezone.utc)
                results.append(grounded_inference_context(graph, rule, stock, native_proof))
                clock.now.assert_not_called()
        self.assertEqual(results[0], results[1])
        self.assertTrue(results[0]["evidenceUsableForJudgement"], results[0])
        measurement = next(c for c in results[0]["matchedConditions"] if c["conditionId"] == "btc-exposure")
        self.assertEqual(10, measurement["freshnessAgeMinutes"])
        self.assertEqual(graph.worldview["evidenceEvaluationAsOf"], measurement["freshnessEvaluatedAt"])
        graph.worldview["evidenceEvaluationAsOf"] = "2026-10-20T12:30:00Z"
        later = grounded_inference_context(graph, rule, stock, native_proof)
        self.assertFalse(later["evidenceUsableForJudgement"])
        self.assertEqual("stale", next(c for c in later["matchedConditions"]
                                      if c["conditionId"] == "btc-exposure")["freshnessStatus"])

    def test_frozen_clock_rejects_future_evidence_and_never_upgrades_explicit_stale_evidence(self):
        from digital_twin.modules.reasoning.domain.ontology_inference_materializer import observation_metadata
        properties = {"sourceAsOf": "2026-10-20T12:00:00Z", "maxAgeMinutes": 25}
        stale = observation_metadata({**properties, "freshnessStatus": "stale"}, 1, "2026-10-20T12:10:00Z")
        self.assertFalse(stale["judgementEvidenceUsable"])
        future = observation_metadata({**properties, "freshnessStatus": "fresh",
                                       "judgementEvidenceUsable": True}, 1, "2026-10-20T11:59:00Z")
        self.assertFalse(future["judgementEvidenceUsable"])
        self.assertIn("분석 기준 시각 이후", future["freshnessGateReason"])
        with self.assertRaises(ValueError):
            observation_metadata(properties, 1, "invalid-clock")

    def test_additive_release_preserves_every_original_rule_and_static_entity(self):
        records = migration_artifacts()
        old, new = (r["artifact"] for r in records)
        self.assertEqual(old["rules"], new["rules"][:len(old["rules"])])
        self.assertEqual(old["tboxMetadata"], new["tboxMetadata"])
        self.assertEqual(old["tboxFingerprint"], new["tboxFingerprint"])
        nodes = {r["id"]: r for r in new["graph"]["entities"]}
        self.assertTrue(all(nodes[r["id"]] == r for r in old["graph"]["entities"]))
        from digital_twin.modules.reasoning.domain.composition_upgrade_review import declared_composition_additions
        self.assertEqual(3, len(declared_composition_additions(old, new, new["compositionUpgradeContract"])))

    def test_review_explains_context_references_but_preserves_raw_comparison(self):
        from digital_twin.modules.reasoning.domain.composition_upgrade_review import review_composition_upgrade, composition_upgrade_review_valid
        from test_management_upgrade_review import summary
        left, right, comparison = migration_jobs()
        original = deepcopy(comparison)
        review = review_composition_upgrade(left, right, comparison, *migration_artifacts())
        self.assertEqual("explained-context-composition", review["status"], review)
        self.assertEqual(original, comparison)
        comparison["compositionUpgradeReview"] = review
        self.assertTrue(composition_upgrade_review_valid(comparison))
        self.assertEqual(1, summary(comparison)["reviewedCompositionSampleCount"])
        self.assertEqual(0, summary(comparison)["unreviewedReasoningParityGapCount"])
        comparison["shadowDeliveryCount"] = 1
        self.assertFalse(composition_upgrade_review_valid(comparison))

    def test_review_cannot_waive_changed_actions_missing_native_match_or_changed_source(self):
        from digital_twin.modules.reasoning.domain.composition_upgrade_review import review_composition_upgrade
        from test_management_upgrade_review import comparison
        for change in ("action", "source", "native"):
            left, right, _ = migration_jobs()
            if change == "action":
                right["result"]["decision_syntheses"][0]["graph_candidate_action"] = "ADD"
            elif change == "source":
                right["sourceSnapshotId"] = "other-source"
            else:
                right["result"]["projection_results"]["acct"]["ruleboxExecution"]["typedbNativeRuleMatchedRuleIds"] = []
            review = review_composition_upgrade(left, right, comparison(left, right), *migration_artifacts())
            self.assertEqual("not-explained", review["status"], (change, review))

    def test_review_rejects_undocumented_rule_and_existing_static_graph_mutation(self):
        from digital_twin.modules.reasoning.domain.composition_upgrade_review import declared_composition_additions
        old, new = [deepcopy(r["artifact"]) for r in migration_artifacts()]
        for change in ("predicate", "tbox", "action"):
            altered = deepcopy(new)
            if change == "predicate":
                altered["rules"][0]["conditions"][0]["value"] = "watchlist"
            elif change == "tbox":
                altered["graph"]["entities"][0]["label"] += " changed"
            else:
                altered["rules"][-1]["derivations"][0]["candidate_action"] = "BUY"
            with self.assertRaises(ValueError):
                declared_composition_additions(old, altered, altered["compositionUpgradeContract"])

    def test_exact_scorer_measurements_survive_abox_without_family_union_attribution(self):
        _, nodes = scored_nodes()
        self.assertEqual({RATE, RELATIVE}, {n["hypothesisContractId"] for n in nodes})
        packets = {n["hypothesisContractId"]: verify(n["sourceMeasurements"]) for n in nodes}
        self.assertTrue(all(packets.values()))
        rate = {r["field"]: r for r in packets[RATE]["measurements"]}
        self.assertEqual((-21, -15), (rate["delta5dBp"]["observedValue"], rate["delta5dBp"]["expectedValue"]))
        self.assertEqual("2026-08-07", rate["delta5dBp"]["provenance"]["sourceAsOf"])
        relative = packets[RELATIVE]["measurements"]
        self.assertNotIn("delta5dBp", [r["field"] for r in relative])
        self.assertTrue(all("HAS_RELATIVE_PERFORMANCE" in r["evidenceId"] for r in relative))
        row = next(r for r in relative if r["field"] == "relativeReturnPct")
        self.assertEqual(("IEF", "session-change", 3, 2), (
            row["context"]["proxySymbol"], row["context"]["comparisonWindow"],
            row["observedValue"], row["expectedValue"]))

    def test_forged_scope_future_partial_and_corrupt_receipts_are_not_current_evidence(self):
        _, nodes = scored_nodes()
        packet = nodes[0]["sourceMeasurements"]
        for key, value in (("symbol", "OTHER"), ("complete", False), ("knowledgeCutoffAt", "2026-08-11T07:00:00Z"),
                           ("measurementFingerprint", "tampered")):
            bad = {**deepcopy(packet), key: value}
            with self.subTest(key=key):
                self.assertFalse(verify(bad))
        future_rows = deepcopy(packet["measurements"])
        future_rows[0]["provenance"]["sourceAsOf"] = "2026-08-11"
        bad = measurement_packet(packet["hypothesisContractId"], "NVDA", packet["modelReleaseId"],
                                 packet["sourceFeatureSnapshotId"], AT, future_rows)
        self.assertFalse(verify(bad))
        self.assertFalse(verified_measurement_packet(packet, rule_id="other-rule", symbol="NVDA",
            release_id=packet["modelReleaseId"], feature_snapshot_id=packet["sourceFeatureSnapshotId"], cutoff=AT))

    def test_shared_observation_and_duplicate_order_do_not_create_independent_confirmation(self):
        _, nodes = scored_nodes()
        rel = relation(nodes)
        result = native_evidence_composition(IDENTITY, rel)
        self.assertEqual(2, len(result["evidenceGroups"]))
        self.assertTrue(result["relationships"][0]["sharedObservation"])
        self.assertFalse(result["relationships"][0]["independenceEstablished"])
        rel["graphStoreInference"]["traces"] = list(reversed(rel["graphStoreInference"]["traces"])) * 3
        self.assertEqual(result, native_evidence_composition(IDENTITY, rel))
        self.assertIsNone(result["selectedMarketAction"])

    def test_opposite_directions_in_different_windows_remain_distinct(self):
        _, nodes = scored_nodes()
        for n, horizon, polarity in zip(nodes, ("3D", "20D"), ("support", "risk")):
            rows = deepcopy(n["sourceMeasurements"]["measurements"])
            for r in rows:
                r["context"] = {"windowKey": horizon}
            n["sourceMeasurements"] = measurement_packet(n["hypothesisContractId"], "NVDA",
                n["releaseId"], n["sourceFeatureSnapshotId"], AT, rows)
            n["polarity"] = polarity
        result = native_evidence_composition(IDENTITY, relation(nodes))
        self.assertTrue(result["relationships"][0]["differentHorizons"])
        self.assertTrue(result["relationships"][0]["opposingDirections"])
        text = composition_explanation({**IDENTITY, "evidenceComposition": result})
        self.assertIn("서로 다른 기간", text)

    def test_native_compositions_are_nonpredictive_and_all_required_predicates_compile(self):
        import json
        from unittest.mock import patch
        from digital_twin.infrastructure import typedb_ontology as api
        from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules, build_rulebox_rules_from_rows
        from digital_twin.infrastructure.graph_store_inferencebox import inferencebox_entity_payload, inferencebox_relation_payload
        from digital_twin.modules.reasoning.domain.ontology_inference_materializer import materialize_rule_inference
        with patch.object(api, "runtime_settings", return_value={}):
            repository = api.TypeDBOntologyGraphRepository("fixture.invalid", retry_count=0)
        rules = default_graph_inference_rules()
        self.assertEqual([], rulebox_semantic_violations(rules))
        combined = [r for r in rules if r.rule_id.startswith("graph.composition.")]
        self.assertEqual(3, len(combined))
        for r in combined:
            self.assertFalse(r.resolved_claim_contract.is_predictive)
            query = typedb_native_match_query(r.to_dict(), ["NVDA"], scoped_manifest_only=True, world_id="world:test")
            self.assertIn("match ", query["query"])
            self.assertIn("ontology-hypothesis-contract-id", query["query"])
            self.assertTrue(all(not d.candidate_action and not d.allowed_actions for d in r.derivations))
            graph = rulebox_graph_from_rules([r], include_tbox=False)
            rows = {}
            for entity in graph.entities:
                rows.setdefault(entity.kind, []).append(repository.entity_row_from_typeql({
                    "id": entity.entity_id, "kind": entity.kind, "ruleId": r.rule_id,
                    "json": json.dumps(entity.properties),
                }, "RuleBox"))
            restored = build_rulebox_rules_from_rows(rows["rule"], rows["rule-condition"], rows["relation-template"])[0]
            response = r.derivations[0].composition_response
            self.assertEqual(response, rows["relation-template"][0]["derivationCompositionResponse"])
            self.assertEqual(response, restored.derivations[0].composition_response)
            stock = OntologyEntity("stock:NVDA", "Nvidia", "stock", {"symbol": "NVDA"})
            output = PortfolioOntology("world:test", entities=[stock])
            materialize_rule_inference(output, restored, stock, {
                "matchedConditions": [{"conditionId": c.condition_id} for c in restored.conditions]})
            materialized = next(edge.properties["compositionResponse"] for edge in output.relations
                                if edge.properties.get("compositionResponse"))
            self.assertEqual(response, materialized)
            for project in (inferencebox_entity_payload, inferencebox_relation_payload):
                self.assertEqual(response, project({"propertiesJson": json.dumps({"compositionResponse": materialized})})["compositionResponse"])
                self.assertNotIn("compositionResponse", project({}))
        bad = replace(combined[0], derivations=[replace(combined[0].derivations[0], allowed_actions=["ADD"])])
        self.assertTrue(any("composition cannot" in x for x in rulebox_semantic_violations([bad])))

    def test_combination_requires_every_native_condition_and_independent_policy_match(self):
        _, nodes = scored_nodes()
        rule = next(r for r in default_graph_inference_rules() if r.rule_id == "graph.composition.position_limit_support.v1")
        response = rule.derivations[0].composition_response
        trace = {"ruleId": rule.rule_id, "matched": True, "matchedConditions": [
            {"conditionId": c.condition_id, "matchedByTypeDB": True,
             "matchedTargetProperties": nodes[0] if c.condition_id == "market-support" else {}}
            for c in rule.conditions]}
        rel = relation(nodes)
        rel["graphStoreInference"]["traces"].append(trace)
        rel["matchedRules"].append({"rule_id": rule.rule_id, "compositionResponse": response})
        self.assertFalse(native_evidence_composition(IDENTITY, rel)["compositions"])
        rel["matchedRules"].append({"rule_id": response["policyRuleId"], "managementResponse": {"code": "RESTORE_POSITION_LIMIT"}})
        self.assertEqual(1, len(native_evidence_composition(IDENTITY, rel)["compositions"]))
        trace["matchedConditions"][0]["matchedByTypeDB"] = False
        self.assertFalse(native_evidence_composition(IDENTITY, rel)["compositions"])

    def test_readable_numbers_include_benchmark_period_threshold_and_exact_previous_value(self):
        _, nodes = scored_nodes()
        current = {**IDENTITY, "evidenceComposition": native_evidence_composition(IDENTITY, relation(nodes))}
        lines = exact_measurements(current, {}, [{"sourceRuleIds": [RELATIVE]}], "USD")
        self.assertTrue(any("IEF" in text and "+3%p" in text and "+2%p" in text for text in lines), lines)
        self.assertEqual(1, sum("비교 자산 대비 등락률 차이" in text for text in lines))
        self.assertTrue(any("미국 10년 국채 금리" in text and "-0.21%p" in text and "-0.15%p" in text for text in lines), lines)
        self.assertNotIn("우호적", " ".join(lines))
        before = deepcopy(current)
        for group in before["evidenceComposition"]["evidenceGroups"]:
            for row in group["sourceMeasurements"]["measurements"]:
                if row["field"] == "delta5dBp":
                    row["observedValue"] = -18
            p = group["sourceMeasurements"]
            group["sourceMeasurements"] = measurement_packet(p["hypothesisContractId"], p["symbol"],
                p["modelReleaseId"], p["sourceFeatureSnapshotId"], p["knowledgeCutoffAt"], p["measurements"])
        changed = exact_measurements(current, before, [{"sourceRuleIds": [RATE]}], "USD")
        self.assertIn("-0.18%p → -0.21%p", changed[0])

    def test_legacy_or_wrong_subject_proof_cannot_fill_missing_measurements(self):
        _, nodes = scored_nodes()
        for n in nodes:
            n.pop("sourceMeasurements", None)
        result = native_evidence_composition(IDENTITY, relation(nodes))
        self.assertEqual([], result["evidenceGroups"])
        self.assertEqual(sorted([RATE, RELATIVE]), result["unavailableRuleIds"])
        self.assertEqual([], exact_measurements({**IDENTITY, "evidenceComposition": result}, {}, [], "USD"))
        _, nodes = scored_nodes()
        result = native_evidence_composition({**IDENTITY, "symbol": "OTHER"}, relation(nodes))
        self.assertEqual([], result["evidenceGroups"])

    def test_policy_and_hypothesis_next_checks_stay_separate(self):
        from digital_twin.modules.notifications.domain.rulebox_notification_summary import compact_rulebox_summary
        _, nodes = scored_nodes()
        composition = native_evidence_composition(IDENTITY, relation(nodes))
        self.assertFalse(composition["compositions"])
        responses = [
            {"ruleId": "graph.position.concentration.guard.v1", "label": "계좌 비중을 설정 상한 이내로 조정",
             "objective": {"field": "positionAccountWeight", "operator": "<="},
             "observedValue": 78.37, "targetValue": 45},
            {"ruleId": "graph.instrument_profile.averaging_down_policy.v1", "label": "손실 구간의 추가매수를 중단",
             "objective": {"field": "profitLossRate", "operator": ">="},
             "observedValue": -5.82, "targetValue": 0},
        ]
        judgment = {**IDENTITY, "authority": "typedb-materialized-synthesis",
                    "evidenceComposition": composition, "managementDecision": {"responses": responses}}
        current = {**IDENTITY, "market": "US", "decisionBrief": {"judgment": judgment}}
        event = {"purpose": "hypothesis-evidence", "sourceRuleIds": [RATE],
                 "nextCheck": {"condition": "금리 변화가 유지되는지 확인"}}
        for events in ([], [event]):
            summary = compact_rulebox_summary({"current": current, "customerEvents": events})
            sections = {key: "\n".join(rows) for key, _, rows in summary["sections"]}
            if not events:
                self.assertIn("45% 이하", sections["next-check-contract"])
                self.assertIn("0%", sections["next-check-contract"])
                self.assertIn("각각 확인", sections["next-check-contract"])
            if events:
                self.assertIn("미국 10년 국채 금리", sections["decision-impact"])
                # The rate rule itself requires relative resilience at +1%p.
                # Keep that input, but exclude the other rule's +2%p threshold.
                self.assertIn("IEF", sections["decision-impact"])
                self.assertIn("기준 +1%p 이상", sections["decision-impact"])
                self.assertNotIn("기준 +2%p 이상", sections["decision-impact"])
            else:
                self.assertNotIn("미국 10년 국채 금리", sections["decision-impact"])
                self.assertNotIn("IEF", sections["decision-impact"])
            if events:
                self.assertIn("금리 변화", sections["next-check-contract"])
                self.assertNotIn("45%", sections["next-check-contract"])
                self.assertNotIn("0%", sections["next-check-contract"])
                self.assertNotIn("계좌 관리:", sections["decision-impact"])
            from digital_twin.modules.read_models.contracts import (
                CustomerInvestmentDocument, CustomerInvestmentSection, normalized_customer_investment_document,
            )
            from digital_twin.modules.notifications.application.customer_investment_message import render_customer_investment_document
            document = normalized_customer_investment_document(CustomerInvestmentDocument(
                role="typedb-observation", headline="관계 변화", lead=summary["lead"], layout="compact-rulebox-v1",
                sections=tuple(CustomerInvestmentSection(key, title, tuple(rows))
                               for key, title, rows in summary["sections"]),
            ))
            rendered = render_customer_investment_document(document)
            numeric_rows = next(s.rows for s in document.sections if s.key == "decision-impact")
            self.assertEqual(2 if events else 1, len(numeric_rows))
            self.assertEqual(bool(events), any("IEF" in line for line in numeric_rows))
