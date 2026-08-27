import unittest

from digital_twin.application.statistical_signals import StatisticalSignalPipelineService
from digital_twin.application.statistical_signals import observe_model_signal_outcome
from digital_twin.domain.hypothesis_scoping import condition_scope_profile
from digital_twin.domain.ontology_contracts import (
    OntologyEntity,
    OntologyRelation,
    PortfolioOntology,
)
from digital_twin.domain.ontology_rule_manifest import (
    rule_dependency_reverse_index,
    validate_rule_domain_manifests,
)
from digital_twin.domain.ontology_change_impact import rule_dependency_profile
from digital_twin.domain.ontology_rulebox_catalog import (
    default_graph_inference_rules,
    governed_graph_inference_rules,
)
from digital_twin.domain.ontology_rulebox_governance import rulebox_rules_hash
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.portfolio import PortfolioSummary, Position
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.portfolio_ontology_statistical_concepts import (
    add_position_statistical_signal_concepts,
)
from digital_twin.domain.statistical_signals import (
    DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
    DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
    DEFAULT_EVENT_SIGNAL_RELEASE_ID,
    DEFAULT_FLOW_SIGNAL_RELEASE_ID,
    DEFAULT_PRICE_SIGNAL_RELEASE_ID,
    DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
    model_signal_evaluation_report,
    price_signal_rule_candidates,
    score_flow_feature_snapshot,
    score_temporal_feature_snapshot,
    statistical_rule_candidate_release,
    validate_signal_hypothesis_mapping,
)
from digital_twin.domain.time_series_storage import TemporalFeatureSnapshot, TimeSeriesWatermark
from digital_twin.infrastructure.ontology_projection import (
    rule_catalog_requires_statistical_signal_scoring,
)
from digital_twin.infrastructure.mysql_statistical_signals import MySQLStatisticalModelSignalStore


def rows(prices, start_day=1):
    result = []
    for index, price in enumerate(prices):
        result.append({
            "bucketAt": "2026-08-%02dT07:00:00Z" % (start_day + index),
            "currentPrice": float(price),
            "ma20Distance": ((float(price) / 100.0) - 1.0) * 100.0,
            "ma60Distance": ((float(price) / 98.0) - 1.0) * 100.0,
            "dataQuality": "actual",
        })
    return result


def feature_snapshot(prices=None):
    values = list(prices or [100, 101, 103, 105, 108, 110])
    windows = {
        "NVDA": {
            "1D": rows(values[-2:], 10),
            "3D": rows(values[-3:], 8),
            "5D": rows(values[-5:], 6),
            "20D": rows(values, 1),
        }
    }
    return TemporalFeatureSnapshot.create(
        backend_id="questdb-shadow",
        account_id="account-1",
        as_of="2026-08-10T07:00:00Z",
        windows=windows,
        watermark=TimeSeriesWatermark("questdb-shadow", "2026-08-10T07:00:00Z"),
    )


def flow_feature_snapshot(direction=1):
    values = []
    for index in range(20):
        price = 100 + index
        values.append({
            "bucketAt": "2026-07-%02dT07:00:00Z" % (index + 1),
            "currentPrice": float(price),
            "volume": 1_000_000,
            "volumeRatio": 1.1,
            "foreignNetVolume": direction * (25_000 + index * 1_000),
            "institutionNetVolume": direction * (15_000 + index * 500),
            "tradeStrength": 112 if direction > 0 else 88,
            "bidAskImbalance": 12 if direction > 0 else -12,
            "dataQuality": "actual",
        })
    return TemporalFeatureSnapshot.create(
        backend_id="questdb-shadow",
        account_id="account-1",
        as_of="2026-07-20T07:00:00Z",
        windows={"NVDA": {"20D": values}},
        watermark=TimeSeriesWatermark("questdb-shadow", "2026-07-20T07:00:00Z"),
    )


class MemoryFeatureStore:
    def __init__(self):
        self.ids = set()

    def upsert(self, snapshot):
        inserted = snapshot.snapshot_id not in self.ids
        self.ids.add(snapshot.snapshot_id)
        return inserted


class MemorySignalStore:
    def __init__(self):
        self.material = {}

    def save(self, snapshot):
        changed = 0
        for signal in snapshot.signals:
            key = (snapshot.account_id, signal.subject_id, signal.signal_type, signal.model_release_id)
            if self.material.get(key) != signal.material_hash:
                changed += 1
                self.material[key] = signal.material_hash
        return {
            "status": "changed" if changed else "unchanged",
            "changedSignalCount": changed,
            "unchangedSignalCount": len(snapshot.signals) - changed,
        }


class FailingSignalStore:
    def save(self, _snapshot):
        raise RuntimeError("signal persistence unavailable")


class FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self.rows = list(rows or [])
        self.rowcount = rowcount

    def fetchall(self):
        return list(self.rows)


class FakeSignalConnection:
    def __init__(self, heads):
        self.heads = list(heads or [])
        self.statements = []

    def execute(self, query, params=None):
        self.statements.append((" ".join(str(query).split()), tuple(params or ())))
        if "FROM statistical_model_signal_heads WHERE" in query and query.lstrip().startswith("SELECT"):
            return FakeCursor(self.heads)
        return FakeCursor(rowcount=1)

    def executemany(self, query, rows):
        for params in rows or []:
            self.statements.append((" ".join(str(query).split()), tuple(params or ())))
        return FakeCursor(rowcount=len(list(rows or [])))


class StatisticalSignalTests(unittest.TestCase):
    def test_price_signal_is_immutable_conditional_score_only(self):
        snapshot = feature_snapshot()
        first = score_temporal_feature_snapshot(snapshot)
        second = score_temporal_feature_snapshot(snapshot)

        self.assertEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(4, len(first.signals))
        self.assertTrue(all(item.probability is None for item in first.signals))
        self.assertTrue(all(item.eligibility.decision_eligibility == "conditional" for item in first.signals))
        self.assertTrue(all(item.eligibility.status == "conditional" for item in first.signals))
        support = next(item for item in first.signals if item.signal_type == "price-trend-continuation-support")
        risk = next(item for item in first.signals if item.signal_type == "price-trend-break-risk")
        self.assertGreater(support.score, risk.score)
        self.assertEqual("trend-continuation", support.hypothesis_family_id)
        self.assertEqual("benchmark-adjusted-return", support.outcome_metric)
        self.assertLessEqual(support.observed_at, support.knowledge_cutoff_at)
        self.assertEqual(snapshot.as_of, support.knowledge_cutoff_at)
        self.assertEqual("score-only", support.uncertainty_status)
        self.assertFalse(support.contract_matched)
        self.assertTrue(support.freshness_compatible)
        self.assertIsNotNone(support.source_age_seconds)
        self.assertIsNone(support.probability_lower)
        self.assertIsNone(support.probability_upper)

    def test_changed_prices_change_signal_material(self):
        first = score_temporal_feature_snapshot(feature_snapshot([100, 101, 103, 105, 108, 110]))
        second = score_temporal_feature_snapshot(feature_snapshot([100, 101, 103, 102, 99, 95]))

        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        first_risk = next(item for item in first.signals if item.signal_type == "price-trend-break-risk")
        second_risk = next(item for item in second.signals if item.signal_type == "price-trend-break-risk")
        self.assertGreater(second_risk.score, first_risk.score)

    def test_pipeline_builds_one_bundle_and_persists_each_model_release(self):
        signal_store = MemorySignalStore()
        service = StatisticalSignalPipelineService(MemoryFeatureStore(), signal_store)
        snapshot = flow_feature_snapshot(1)

        result = service.run("account-1", "questdb-shadow", snapshot.windows, snapshot.as_of)
        bundle = result["signalBundle"]

        self.assertEqual(2, len(bundle.model_release_ids))
        self.assertEqual(7, len(bundle.signals))
        self.assertEqual(2, len(result["persistence"]["signalSnapshots"]))
        self.assertTrue(all(
            item["status"] == "changed"
            for item in result["persistence"]["signalSnapshots"].values()
        ))

    def test_distinct_knowledge_cutoff_changes_signal_contract(self):
        feature_store = MemoryFeatureStore()
        signal_store = MemorySignalStore()
        service = StatisticalSignalPipelineService(feature_store, signal_store)
        snapshot = feature_snapshot()

        first = service.run("account-1", "questdb-shadow", snapshot.windows, "2026-08-10T08:00:00Z")
        second = service.run("account-1", "questdb-shadow", snapshot.windows, "2026-08-10T09:00:00Z")

        self.assertEqual("changed", first["persistence"]["signalSnapshot"]["status"])
        self.assertEqual("changed", second["persistence"]["signalSnapshot"]["status"])
        self.assertNotEqual(first["signalSnapshot"].snapshot_id, second["signalSnapshot"].snapshot_id)

    def test_snapshot_identity_is_account_scoped_but_reuses_shared_market_material(self):
        first = score_temporal_feature_snapshot(feature_snapshot())
        second_features = TemporalFeatureSnapshot.create(
            backend_id="questdb-shadow",
            account_id="account-2",
            as_of="2026-08-10T07:00:00Z",
            windows=feature_snapshot().windows,
            watermark=TimeSeriesWatermark("questdb-shadow", "2026-08-10T07:00:00Z"),
        )
        second = score_temporal_feature_snapshot(second_features)

        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(first.shared_material_hash, second.shared_material_hash)
        self.assertEqual(("NVDA",), first.subjects)

    def test_abox_projects_signal_release_feature_and_eligibility(self):
        signal_snapshot = score_temporal_feature_snapshot(feature_snapshot()).to_dict()
        graph = PortfolioOntology("portfolio:account-1")
        stock_id = add_entity(graph, "stock", "NVDA", "NVIDIA", {
            "tboxClass": "Stock",
            "tboxClasses": ["Instrument", "Stock"],
            "symbol": "NVDA",
        })

        add_position_statistical_signal_concepts(
            graph,
            stock_id,
            "NVDA",
            {"statisticalSignalSnapshot": signal_snapshot},
        )

        relation_types = [item.relation_type for item in graph.relations]
        self.assertEqual(4, relation_types.count("HAS_MODEL_SIGNAL"))
        self.assertEqual(4, relation_types.count("GENERATED_BY_MODEL_RELEASE"))
        self.assertEqual(4, relation_types.count("BASED_ON_FEATURE_SNAPSHOT"))
        self.assertEqual(4, relation_types.count("HAS_SIGNAL_ELIGIBILITY"))
        self.assertEqual(4, relation_types.count("SUPPORTS_HYPOTHESIS_FAMILY"))
        signal_entities = [item for item in graph.entities if item.kind == "statistical-model-signal"]
        self.assertEqual(4, len(signal_entities))
        self.assertTrue(all(item.properties.get("decisionEligibility") == "conditional" for item in signal_entities))
        self.assertTrue(all(item.properties.get("hypothesisFamilyId") for item in signal_entities))
        family_entities = [item for item in graph.entities if item.kind == "hypothesis-family-definition"]
        self.assertEqual(3, len(family_entities))

    def test_every_predictive_rule_has_governed_signal_migration_contract(self):
        rules = default_graph_inference_rules()
        validation = validate_rule_domain_manifests(rules)
        predictive = [
            item for item in validation["manifests"]
            if item.get("ruleKind") == "predictive-hypothesis"
        ]

        self.assertTrue(validation["valid"])
        self.assertEqual(74, len(predictive))
        self.assertTrue(all((item.get("statisticalSignalContract") or {}).get("signalTypes") for item in predictive))
        reverse_index = rule_dependency_reverse_index(rules)
        migration = reverse_index["statisticalSignals"]["byMigrationState"]
        self.assertEqual(44, len(migration["not-applicable"]))
        self.assertEqual(74, len(migration["model-signal-production"]))
        self.assertEqual([], migration.get("shadow-signal-required") or [])
        flow_rule = next(
            item for item in predictive
            if item.get("ruleId") == "graph.flow.sell_pressure.v1"
        )
        flow_contract = flow_rule["statisticalSignalContract"]
        self.assertEqual("implemented", flow_contract["signalAvailability"])
        self.assertTrue(flow_contract["productionEligible"])
        self.assertEqual("typedb-model-signal-rule", flow_contract["currentDecisionAuthority"])

    def test_high_consequence_recovery_rules_require_exact_hypothesis_contract(self):
        rules = {rule.rule_id: rule for rule in default_graph_inference_rules()}

        for rule_id in {
            "graph.loss_rebound.trim_moderation.v1",
            "graph.aggressive.loss_recovery.add_buy_review.v1",
            "graph.winner_momentum.add_buy_review.v1",
        }:
            rule = rules[rule_id]
            signal_conditions = [
                condition
                for condition in rule.conditions
                if condition.relation_type == "HAS_MODEL_SIGNAL"
            ]
            self.assertEqual(1, len(signal_conditions))
            self.assertEqual(
                rule_id,
                signal_conditions[0].target_property_filters.get("hypothesisContractId"),
            )
            self.assertEqual(
                "statistical-model-hypothesis-evidence",
                signal_conditions[0].target_kind,
            )
            self.assertIn("원시 임계치", rule.prompt_hint)

    def test_every_converted_rule_preserves_its_original_change_routing_contract(self):
        governed = {
            rule.rule_id: rule
            for rule in governed_graph_inference_rules()
            if rule.resolved_knowledge_basis.rule_kind == "predictive-hypothesis"
        }
        production = {
            rule.rule_id: rule
            for rule in default_graph_inference_rules()
            if rule.resolved_knowledge_basis.rule_kind == "predictive-hypothesis"
        }

        self.assertEqual(set(governed), set(production))
        for rule_id, source_rule in governed.items():
            converted_rule = production[rule_id]
            source_dependency = rule_dependency_profile(source_rule)
            converted_dependency = rule_dependency_profile(converted_rule)
            self.assertEqual(
                source_dependency["scopeFamilies"],
                converted_dependency["scopeFamilies"],
                rule_id,
            )
            self.assertEqual(
                source_dependency["dependencyKeys"],
                converted_dependency["dependencyKeys"],
                rule_id,
            )
            self.assertEqual(
                "predictive-model-input-routing-v1",
                converted_rule.model_input_contract.get("version"),
                rule_id,
            )

    def test_all_six_model_families_emit_exact_contract_evidence(self):
        snapshot = flow_feature_snapshot(1)
        graph = PortfolioOntology("portfolio:account-1")
        graph.entities.append(OntologyEntity(
            "stock:NVDA",
            "NVIDIA",
            "stock",
            {"symbol": "NVDA", "source": "holding", "profitLossRate": -5},
        ))

        evidence = [
            (
                "temporal:risk",
                "temporal-window",
                "HAS_TEMPORAL_WINDOW",
                {
                    "windowKey": "5D",
                    "hasSufficientHistory": True,
                    "coverageRatio": 1,
                    "validObservationRatio": 1,
                    "staleObservationCount": 0,
                    "priceChangePct": -4,
                    "recentPriceChangePct": -3,
                    "priceVelocityChangePct": -2,
                    "consecutiveDeclineCount": 3,
                },
            ),
            (
                "leveraged:flow",
                "leveraged-flow-signal",
                "HAS_LEVERAGED_FLOW_SIGNAL",
                {"field": "leverageFactor", "value": 2.5},
            ),
            (
                "quality:judgement",
                "data-availability-assessment",
                "HAS_DATA_QUALITY",
                {"field": "judgementEvidence", "dataState": "sufficient"},
            ),
            (
                "fx:usdkrw",
                "fx-rate",
                "HAS_FX_EXPOSURE",
                {"pair": "USDKRW", "value": 1500},
            ),
            (
                "earnings:risk",
                "earnings-calendar-event",
                "HAS_EXTERNAL_SIGNAL",
                {"surprisePercentage": -8, "eventDecisionEligible": True},
            ),
            (
                "valuation:risk",
                "margin-of-safety",
                "HAS_MARGIN_OF_SAFETY",
                {
                    "marginOfSafetyPct": -15,
                    "valuationDataState": "sufficient",
                    "valuationDecisionEligible": 1,
                },
            ),
        ]
        for entity_id, kind, relation_type, properties in evidence:
            graph.entities.append(OntologyEntity(entity_id, entity_id, kind, properties))
            graph.relations.append(OntologyRelation("stock:NVDA", entity_id, relation_type))

        release_ids = (
            DEFAULT_PRICE_SIGNAL_RELEASE_ID,
            DEFAULT_FLOW_SIGNAL_RELEASE_ID,
            DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
            DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
            DEFAULT_EVENT_SIGNAL_RELEASE_ID,
            DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
        )
        result = StatisticalSignalPipelineService(
            model_release_ids=release_ids,
        ).run(
            "account-1",
            "test-time-series",
            snapshot.windows,
            snapshot.as_of,
            graph=graph,
            rules=governed_graph_inference_rules(),
        )

        snapshots = result["signalSnapshots"]
        self.assertEqual(set(release_ids), {item.model_release_id for item in snapshots})
        self.assertTrue(all(
            any(signal.hypothesis_contract_ids for signal in item.signals)
            for item in snapshots
        ))
        exact_contract_ids = {
            contract_id
            for signal in result["signalBundle"].signals
            for contract_id in signal.hypothesis_contract_ids
        }
        self.assertTrue({
            "graph.temporal.persistent_decline.risk.v1",
            "graph.security_line.leveraged_flow_amplification.v1",
            "graph.averaging_down.risk_guard.v1",
            "graph.fx.usdkrw.exposure.regime.v1",
            "graph.earnings.surprise.risk.v1",
            "graph.valuation.negative_margin.risk.v1",
        }.issubset(exact_contract_ids))
        matched_signals = [
            signal
            for signal in result["signalBundle"].signals
            if signal.hypothesis_contract_ids
        ]
        self.assertTrue(all(signal.contract_matched for signal in matched_signals))
        self.assertTrue(any(signal.score < 1.0 for signal in matched_signals))
        for signal in matched_signals:
            family_score = signal.input_features.get("familyScore")
            if family_score is not None:
                self.assertEqual(float(family_score), signal.score)

    def test_price_contract_is_ineligible_without_family_feature_snapshot(self):
        graph = PortfolioOntology(
            "portfolio:account-1",
            entities=[
                OntologyEntity("stock:NVDA", "NVIDIA", "stock", {"symbol": "NVDA"}),
                OntologyEntity("level:ma20", "MA20", "key-level", {
                    "levelType": "ma20",
                    "value": 5,
                }),
                OntologyEntity("quality:market", "Market quality", "data-quality-status", {
                    "dataScope": "market-microstructure",
                    "dataState": "available",
                }),
            ],
            relations=[
                OntologyRelation("stock:NVDA", "level:ma20", "HAS_TECHNICAL_INDICATOR"),
                OntologyRelation("stock:NVDA", "quality:market", "HAS_DATA_QUALITY"),
            ],
        )
        result = StatisticalSignalPipelineService().run(
            "account-1",
            "test-time-series",
            {},
            "2026-08-10T07:00:00Z",
            graph=graph,
            rules=governed_graph_inference_rules(),
        )
        price_contract_signals = [
            signal for signal in result["signalBundle"].signals
            if signal.model_release_id == DEFAULT_PRICE_SIGNAL_RELEASE_ID
            and signal.hypothesis_contract_ids
        ]

        self.assertTrue(price_contract_signals)
        self.assertTrue(all(signal.eligibility.status == "ineligible" for signal in price_contract_signals))
        self.assertTrue(all(
            "family-feature-snapshot-unavailable" in signal.eligibility.reasons
            for signal in price_contract_signals
        ))

    def test_production_model_evidence_is_ineligible_when_snapshot_persistence_fails(self):
        snapshot = flow_feature_snapshot(1)
        graph = PortfolioOntology(
            "portfolio:account-1",
            entities=[
                OntologyEntity("stock:NVDA", "NVIDIA", "stock", {"symbol": "NVDA"}),
                OntologyEntity("temporal:risk", "Risk path", "temporal-window", {
                    "windowKey": "5D",
                    "hasSufficientHistory": True,
                    "coverageRatio": 1,
                    "validObservationRatio": 1,
                    "staleObservationCount": 0,
                    "priceChangePct": -4,
                    "recentPriceChangePct": -3,
                    "priceVelocityChangePct": -2,
                    "consecutiveDeclineCount": 3,
                }),
            ],
            relations=[
                OntologyRelation("stock:NVDA", "temporal:risk", "HAS_TEMPORAL_WINDOW"),
            ],
        )
        result = StatisticalSignalPipelineService(
            MemoryFeatureStore(),
            FailingSignalStore(),
            model_release_ids=(DEFAULT_PRICE_SIGNAL_RELEASE_ID,),
        ).run(
            "account-1",
            "test-time-series",
            snapshot.windows,
            snapshot.as_of,
            graph=graph,
            rules=governed_graph_inference_rules(),
        )

        self.assertTrue(result["signalBundle"].signals)
        self.assertEqual("evidence-not-durable", result["status"])
        self.assertFalse(result["decisionEligible"])
        self.assertIn("model-signal-persistence-failed", result["decisionBlockers"])

if __name__ == "__main__":
    unittest.main()
