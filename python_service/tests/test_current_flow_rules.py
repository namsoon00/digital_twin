"""Current-source flow proof, missing-data and additive strategy contracts."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch

from digital_twin.modules.portfolio.domain.portfolio import Position, PortfolioSummary
from digital_twin.modules.market_data.domain.verified_flow import verified_flow_measurements
from digital_twin.modules.market_data.domain.time_series_storage import TemporalFeatureSnapshot, TimeSeriesWatermark
from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import governed_graph_inference_rules, default_graph_inference_rules
from digital_twin.modules.model_registry.domain.current_flow_rules import SUFFIX, COHORT
from digital_twin.modules.model_registry.domain.current_flow_strategy import current_flow_strategy_rules
from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import rulebox_semantic_violations
from digital_twin.modules.model_registry.domain.statistical_signals.graph_scoring import score_graph_hypothesis_contracts
from digital_twin.modules.model_registry.domain.statistical_signals.registry import CURRENT_FLOW_SIGNAL_RELEASE_ID, DEFAULT_FLOW_SIGNAL_RELEASE_ID, model_release
from digital_twin.modules.reasoning.domain.portfolio_ontology_builder import build_portfolio_ontology

NOW = "2026-10-23T01:01:00Z"
SOURCE = "2026-10-23T01:00:00Z"


def position():
    from digital_twin.infrastructure.kis_market_signals import stage_coverage
    coverage = {}
    for stage, fields in {"investor": ["foreignNetVolume", "institutionNetVolume"],
                          "ccnl": ["tradeStrength", "buyVolume", "sellVolume"],
                          "orderbook": ["bidAskImbalance", "orderbookBidVolume", "orderbookAskVolume"]}.items():
        normalized = {f: 1 for f in fields}
        coverage[stage] = stage_coverage(stage, normalized, normalized, fields,
            source_as_of=SOURCE, fetched_at="2026-10-23T01:00:10Z",
            source_timestamp_state="provider-scheduled-estimate" if stage == "investor" else "queried-at-fallback",
            measurement_type="intraday-estimate" if stage == "investor" else "current-" + stage,
            session={"regular": True, "key": "regular"}, measurement_metadata={"providerUpdateCurrent": True,
                "providerExpectedUpdateCode": "2", "providerUpdateCode": "2"})
        coverage[stage]["tradeStrengthDecisionUsable"] = True
    return Position(symbol="005930", name="Synthetic", market="KR", currency="KRW", quantity=1,
        current_price=105, average_price=110, market_value=105, market_value_krw=105, change_rate=2, price_change_usable=True,
        ma5=100, ma20=100, ma60=100, ma20_distance=5, ma60_distance=5,
        trade_strength=120, buy_volume=1200, sell_volume=1000, bid_ask_imbalance=20,
        orderbook_bid_volume=1200, orderbook_ask_volume=800, volume=100000, volume_ratio=1.2,
        foreign_net_volume=1000, institution_net_volume=500, individual_net_volume=-1500,
        quote_source="Synthetic", source_as_of=SOURCE, source_fetched_at="2026-10-23T01:00:10Z",
        updated_at=NOW, indicator_as_of=SOURCE, indicator_fetched_at="2026-10-23T01:00:10Z",
        data_quality="actual", quote_status="ok", market_signal_coverage=coverage)


def graph_and_score(pos=None, now=NOW):
    graph = build_portfolio_ontology([pos or position()], PortfolioSummary(1000, 1000, 0, [], [], 1),
        runtime_context={"asOf": now, "settings": {}}, portfolio_id="flow-test")
    feature = TemporalFeatureSnapshot.create(backend_id="test", account_id="test", as_of=now,
        windows={}, watermark=TimeSeriesWatermark("test", now))
    rules = [r for r in governed_graph_inference_rules() if r.rule_id.endswith(SUFFIX)]
    scored = score_graph_hypothesis_contracts(graph, feature, rules)
    return graph, next(s for s in scored if s.model_release_id == CURRENT_FLOW_SIGNAL_RELEASE_ID)


class CurrentFlowRuleTests(TestCase):
    def test_current_proof_does_not_require_or_read_mixed_flow_history(self):
        graph, scored = graph_and_score()
        supported = {a.hypothesis_contract_id for a in scored.assessments if a.status == "supported"}
        self.assertIn("graph.flow.recovery_confirmed_by_smart_money.v1" + SUFFIX, supported)
        self.assertIn("graph.flow.accumulation.entry.v1" + SUFFIX, supported)
        signals = [s for s in scored.signals if s.hypothesis_contract_ids]
        self.assertTrue(signals)
        for s in signals:
            self.assertEqual("conditional", s.eligibility.status)
            self.assertEqual("regular", s.market_session)
            self.assertEqual(60, s.source_age_seconds)
            self.assertIsNone(s.probability)
            self.assertEqual(0, s.input_features["empiricalSampleCount"])
            for match in s.input_features["contractMatches"]:
                numeric = [m for m in match["sourceMeasurements"]["measurements"] if m["field"] == "value"]
                self.assertTrue(numeric)
                self.assertTrue(all(m["provenance"].get("sourceAsOf") == SOURCE for m in numeric))

        from datetime import datetime
        from digital_twin.modules.reasoning.domain.portfolio_ontology_statistical_concepts import add_position_statistical_signal_concepts
        from digital_twin.modules.reasoning.domain.ontology_inference_materializer import observation_metadata
        from digital_twin.modules.notifications.domain.rule_dispatch_validity import rule_dispatch_validity
        add_position_statistical_signal_concepts(graph, "stock:005930", "005930", {"statisticalSignalSnapshot": scored.to_dict()})
        proof = next(e for e in graph.entities if e.kind == "statistical-model-hypothesis-evidence"
                     and e.properties["hypothesisContractId"] == "graph.flow.accumulation.entry.v1" + SUFFIX)
        condition = {"conditionId": "exact-model", **observation_metadata(proof.properties, 1, NOW)}
        self.assertEqual("2026-10-23T01:02:00+00:00", condition["validUntil"])
        packet = {"current": {"rules": [{"id": "native", "nativeProof": True, "conditions": [condition]}]},
                  "customerEvents": [{"eligible": True, "sourceRuleIds": ["native"]}]}
        self.assertTrue(rule_dispatch_validity(packet, now=datetime.fromisoformat(NOW.replace("Z", "+00:00")))["allowed"])
        expired = rule_dispatch_validity(packet, now=datetime.fromisoformat("2026-10-23T01:02:01+00:00"))
        self.assertFalse(expired["allowed"])
        self.assertEqual("rule_evidence_expired_at_dispatch", expired["reasonCode"])

    def test_explicit_zero_is_observed_but_missing_party_is_not_zero(self):
        p = position(); p.institution_net_volume = 0
        rows = {r["field"]: r for r in verified_flow_measurements(p, NOW, "open")}
        self.assertEqual(0, rows["institutionNetVolume"]["value"])
        self.assertTrue(rows["institutionNetVolume"]["judgementEvidenceUsable"])
        p.market_signal_coverage["investor"]["observedFields"].remove("institutionNetVolume")
        rows = {r["field"]: r for r in verified_flow_measurements(p, NOW, "open")}
        self.assertNotIn("smartMoneyNetVolume", rows)
        _, scored = graph_and_score(p)
        assessment = next(a for a in scored.assessments if a.hypothesis_contract_id == "graph.investor_flow.smart_money_outflow_risk.v1" + SUFFIX)
        self.assertEqual("untested", assessment.status)
        p.market_signal_coverage["investor"]["observedFields"] = []
        rows = verified_flow_measurements(p, NOW, "open")
        self.assertFalse(any(r["field"].endswith("NetVolume") for r in rows))

    def test_stale_future_missing_and_late_received_source_cannot_resolve_a_hypothesis(self):
        for changes in ({"sourceAsOf": "2026-10-23T00:00:00Z"}, {"sourceAsOf": "2026-10-23T01:02:00Z"},
                        {"sourceAsOf": ""}, {"fetchedAt": "2026-10-23T01:02:00Z"},
                        {"measurementType": "daily-final"}, {"judgementEvidenceUsable": False},
                        {"freshnessStatus": "stale"}, {"marketSession": "closed"},
                        {"providerUpdateCurrent": False}, {"validUntil": NOW},
                        {"participantStatus": {"foreign": "not-yet-published"}}):
            with self.subTest(changes=changes):
                p = position(); p.market_signal_coverage["investor"].update(changes)
                _, scored = graph_and_score(p)
                a = next(a for a in scored.assessments if a.hypothesis_contract_id == "graph.flow.recovery_confirmed_by_smart_money.v1" + SUFFIX)
                self.assertEqual("untested", a.status)
                self.assertEqual("reference-only", a.decision_eligibility)

    def test_closed_session_and_unconfirmed_trade_samples_are_unusable(self):
        p = position()
        self.assertFalse(any(r["judgementEvidenceUsable"] for r in verified_flow_measurements(p, NOW, "closed")))
        p.market_signal_coverage["ccnl"]["tradeStrengthDecisionUsable"] = False
        _, scored = graph_and_score(p)
        a = next(a for a in scored.assessments if a.hypothesis_contract_id == "graph.flow.accumulation.entry.v1" + SUFFIX)
        self.assertEqual("untested", a.status)
        p = position(); p.market_signal_coverage["orderbook"]["sourceAsOf"] = "2026-10-23T00:57:00Z"
        _, scored = graph_and_score(p)
        a = next(a for a in scored.assessments if a.hypothesis_contract_id == "graph.flow.accumulation.entry.v1" + SUFFIX)
        self.assertEqual("untested", a.status)  # Existing two-minute orderbook budget remains stricter than ten minutes.
        p = position(); p.price_change_usable = False
        _, scored = graph_and_score(p)
        a = next(a for a in scored.assessments if a.hypothesis_contract_id == "graph.flow.price_up_smart_money_outflow.divergence.v1" + SUFFIX)
        self.assertEqual("untested", a.status)
        p.market = "US"; p.currency = "USD"; p.symbol = "AAPL"
        self.assertEqual([], verified_flow_measurements(p, NOW, "open"))

    def test_sign_reversal_changes_exact_hypothesis_instead_of_reusing_broad_family(self):
        p = position(); p.foreign_net_volume = -1000; p.institution_net_volume = -500; p.individual_net_volume = 1500
        _, scored = graph_and_score(p)
        supported = {a.hypothesis_contract_id for a in scored.assessments if a.status == "supported"}
        self.assertIn("graph.investor_flow.smart_money_outflow_risk.v1" + SUFFIX, supported)
        self.assertIn("graph.flow.price_up_smart_money_outflow.divergence.v1" + SUFFIX, supported)
        self.assertNotIn("graph.flow.recovery_confirmed_by_smart_money.v1" + SUFFIX, supported)

    def test_catalog_preserves_legacy_disable_and_claim_ownership(self):
        rules = default_graph_inference_rules()
        self.assertEqual(5, sum(r.enabled and r.rule_id.endswith(SUFFIX) for r in rules))
        personal = {"graph.investor_flow.smart_money_accumulation.v1", "graph.investor_flow.retail_dip_buying_risk.v1"}
        self.assertFalse(any(r.enabled for r in rules if r.rule_id in personal))
        self.assertFalse(any(r.rule_id in {rid + SUFFIX for rid in personal} for r in rules))
        self.assertFalse(any(r.enabled for r in rules if r.rule_id in COHORT))
        self.assertEqual("reference-only", model_release(DEFAULT_FLOW_SIGNAL_RELEASE_ID).decision_eligibility)
        self.assertEqual([], rulebox_semantic_violations(rules))
        from digital_twin.infrastructure.composition.reasoning_release import v2_model_signal_release_contract
        from digital_twin.infrastructure.statistical_signal_factory import build_statistical_signal_pipeline_service
        check = v2_model_signal_release_contract({"rules": [r.to_dict() for r in rules]})
        self.assertEqual("matched", check["status"])
        self.assertIn(CURRENT_FLOW_SIGNAL_RELEASE_ID, check["requiredReleaseIds"])
        with patch("digital_twin.infrastructure.statistical_signal_factory.MySQLTemporalFeatureSnapshotStore"), \
                patch("digital_twin.infrastructure.statistical_signal_factory.MySQLStatisticalModelSignalStore"):
            pipeline = build_statistical_signal_pipeline_service({"test": True})
        self.assertIn(CURRENT_FLOW_SIGNAL_RELEASE_ID, pipeline.model_release_ids)

    def test_flow_strategy_freezes_all_phases_and_explains_actual_flow_changes(self):
        rules = default_graph_inference_rules(); before = [r.to_dict() for r in rules]
        phases = current_flow_strategy_rules(rules)
        self.assertEqual(before, [r.to_dict() for r in rules])
        self.assertEqual({"setup", "entry", "invalidation", "exit"}, {r.resolved_claim_contract.strategy_phase for r in phases})
        for r in phases:
            self.assertTrue(r.enabled)
            self.assertIn("수급", r.label)
            contract = r.resolved_claim_contract.strategy_contract
            self.assertEqual("none", contract["scenarioContract"]["tradingAuthority"])
            for c in r.conditions:
                if c.relation_type == "HAS_MODEL_SIGNAL":
                    self.assertEqual(CURRENT_FLOW_SIGNAL_RELEASE_ID, c.target_property_filters["releaseId"])
                    self.assertEqual("regular", c.target_property_filters["marketSession"])
        self.assertEqual([], rulebox_semantic_violations([*rules, *phases]))
        from digital_twin.infrastructure.graph_store_lifecycle import ontology_release_seed_artifact
        from digital_twin.infrastructure.composition.strategy_release import current_flow_release_artifact
        from digital_twin.modules.model_registry.domain.ontology_rule_manifest import validate_rule_domain_manifests
        self.assertTrue(validate_rule_domain_manifests([*rules, *phases])["valid"])
        baseline = ontology_release_seed_artifact([r for r in rules if not r.rule_id.endswith(SUFFIX)])
        original = deepcopy(baseline)
        record = {"status": "ready", "valid": True, "deploymentId": "baseline", "artifact": baseline,
                  "artifactFingerprint": "test-artifact", "ruleboxFingerprint": baseline["ruleboxFingerprint"]}
        binding = {"deploymentId": "baseline", "graphStoreBinding": "isolated-test"}
        added = current_flow_release_artifact(record, binding)
        self.assertEqual(original, baseline)
        self.assertEqual(baseline["rules"], added["rules"][:-9])
        self.assertEqual(baseline["tboxMetadata"], added["tboxMetadata"])
        original_nodes = {n["id"]: n for n in baseline["graph"]["entities"]}
        for node in added["graph"]["entities"]:
            if node["id"] not in original_nodes:
                continue
            old = original_nodes[node["id"]]
            if old["kind"] != "model-signal-bridge":
                self.assertEqual(old, node)
                continue
            aggregates = {"policyIds", "lineageRuleIds", "policyCount"}
            self.assertEqual({k: v for k, v in old["properties"].items() if k not in aggregates},
                             {k: v for k, v in node["properties"].items() if k not in aggregates})
            self.assertTrue(set(old["properties"]["policyIds"]).issubset(node["properties"]["policyIds"]))
        with self.assertRaisesRegex(ValueError, "already registered"):
            current_flow_release_artifact({**record, "artifact": added}, binding)
        from digital_twin.modules.reasoning.application.reasoning_engine_platform import ReasoningEnginePlatformService
        from digital_twin.modules.reasoning.domain.reasoning_engine_versions import EngineReleaseBundle, ReasoningEngineDescriptor, EngineControlState
        bundle = EngineReleaseBundle("tbox", "rulebox", "prompt", "features", model_signal_release_id="old-model")
        descriptor = ReasoningEngineDescriptor("ontology", "v2", "baseline", "active", "baseline-db", "mysql-primary", bundle)
        registry = Mock(); stored = {"baseline": descriptor.to_dict()}
        registry.control.return_value = EngineControlState("baseline", "baseline", "", 1)
        registry.get.side_effect = lambda rid: stored.get(rid, {})
        registry.upsert.side_effect = lambda row: stored.update({row.deployment_id: row.to_dict()})
        registry.save_release_artifact.return_value = {"status": "saved"}
        registry.set_control.return_value = EngineControlState("baseline", "baseline", "successor", 2)
        platform = ReasoningEnginePlatformService(registry)
        platform.descriptors = lambda: [descriptor]
        platform.deployment_descriptor = lambda rid: descriptor
        platform.synchronize_control_capabilities = lambda control: {}
        registration = platform.register_v2_release("successor", "flow-release", "isolated-flow-db", added, "baseline")
        self.assertEqual("registered", registration["status"])
        self.assertIn(CURRENT_FLOW_SIGNAL_RELEASE_ID, stored["successor"]["releaseBundle"]["model_signal_release_id"].split("+"))
        self.assertEqual("old-model", stored["baseline"]["releaseBundle"]["model_signal_release_id"])

    def test_ambiguous_or_tampered_source_is_unknown_at_the_model_boundary(self):
        from digital_twin.modules.model_registry.domain.statistical_signals.current_flow_evidence import current_flow_source_clock
        from digital_twin.modules.model_registry.domain.statistical_signals.graph_scoring import _graph_match_index
        graph, _ = graph_and_score()
        raw = next(r for r in governed_graph_inference_rules() if r.rule_id == "graph.flow.recovery_confirmed_by_smart_money.v1" + SUFFIX)
        stock = next(e for e in graph.entities if e.kind == "stock")
        target = next(e for e in graph.entities if e.kind == "flow-metric"
                      and e.properties.get("dataScope") == "verified-current-flow-v1"
                      and e.properties.get("field") == "smartMoneyNetVolume")
        target.properties["sourceAsOf"] = "2026-10-23T00:00:00Z"
        target.properties["judgementEvidenceUsable"] = True
        clock = current_flow_source_clock(stock, raw.conditions, _graph_match_index(graph), NOW)
        self.assertTrue(clock["reasons"])

    def test_multiple_strategy_changes_are_visible_without_selecting_a_trade(self):
        from digital_twin.modules.notifications.domain.rule_strategy_event import strategy_summary
        from digital_twin.modules.model_registry.domain.strategy_scenario_contract import flow_scenario_contract, scenario_contract
        events = []
        for name, contract, signal in (("가격 회복 전략", scenario_contract(), "entry"),
                                       ("수급 확인 전략", flow_scenario_contract(), "invalidation")):
            transition = next(t for t in contract["transitions"] if t["from"] == "observing" and t["signal"] == signal)
            events.append({"strategyId": name, "eventId": name, "strategyLabel": name,
                "strategyPhases": [signal], "signalValiditySeconds": 600,
                "scenarioTransition": {**transition, "previousLabel": contract["states"][transition["from"]],
                                       "currentLabel": contract["states"][transition["to"]]},
                "nextCheck": {"condition": name + "의 조건을 다시 확인합니다."}})
        current = {"decisionBrief": {"judgment": {"managementDecision": {"responses": [{"label": "추가매수 제한"}]}}}}
        packet = {"current": current, "customerEvents": events}; before = deepcopy(packet)
        summary = strategy_summary(packet, "현재 105원", "외국인 -1,000주\n기관 -500주")
        self.assertEqual(summary, strategy_summary({**packet, "customerEvents": list(reversed(events))},
                                                  "현재 105원", "외국인 -1,000주\n기관 -500주"))
        self.assertEqual(before, packet)
        text = str(summary)
        for value in ("가격 회복 전략", "수급 확인 전략", "합산 순매도", "추가매수 제한", "-1,000주", "독립된 근거 수로 세지"):
            self.assertIn(value, text)
        self.assertLess(len(text), 1800)
        # The actual scorer receipts must reach strategy summaries; previously
        # the caller discarded the saved composition by passing an empty dict.
        from digital_twin.modules.reasoning.domain.portfolio_ontology_statistical_concepts import add_position_statistical_signal_concepts
        from digital_twin.modules.decisions.domain.investment_reasoning.evidence_composition import native_evidence_composition
        from digital_twin.modules.notifications.domain.rulebox_notification_summary import compact_rulebox_summary
        graph, snapshot = graph_and_score()
        stock = next(e for e in graph.entities if e.kind == "stock")
        add_position_statistical_signal_concepts(graph, stock.entity_id, "005930", {"statisticalSignalSnapshot": snapshot.to_dict()})
        nodes = [e for e in graph.entities if e.kind == "statistical-model-hypothesis-evidence"
                 and e.properties["hypothesisContractId"] in {"graph.flow.accumulation.entry.v1" + SUFFIX,
                                                             "graph.flow.recovery_confirmed_by_smart_money.v1" + SUFFIX}]
        identity = {"accountId": "test", "symbol": "005930", "worldId": "test", "inferenceGenerationId": "test", "sourceAboxSnapshotId": "test"}
        relation = {"graphStoreInference": {"traces": [{"ruleId": "flow-phase", "matched": True,
            "matchedConditions": [{"matchedByTypeDB": True, "freshnessStatus": "fresh", "matchedTargetProperties": node.properties} for node in nodes]}]}}
        composition = native_evidence_composition(identity, relation)
        self.assertEqual(2, len(composition["evidenceGroups"]))
        current = {**identity, "market": "KR", "decisionBrief": {"judgment": {**identity,
            "authority": "typedb-materialized-synthesis", "evidenceComposition": composition}}}
        packet = {"current": current, "customerEvents": [{**e, "sourceRuleIds": ["flow-phase"]} for e in events]}
        text = str(compact_rulebox_summary(packet))
        self.assertIn("체결강도", text); self.assertIn("120", text); self.assertIn("115", text)
        self.assertIn("1,500", text)
        self.assertNotIn("측정값이 보존되지", text)
        current["decisionBrief"]["judgment"]["worldId"] = "other-world"
        self.assertNotIn("체결강도 120", str(compact_rulebox_summary(packet)))
