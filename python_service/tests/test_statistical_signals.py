import unittest

from digital_twin.application.statistical_signals import StatisticalSignalPipelineService
from digital_twin.application.statistical_signals import observe_model_signal_outcome
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rule_manifest import (
    rule_dependency_reverse_index,
    validate_rule_domain_manifests,
)
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_rulebox_governance import rulebox_rules_hash
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.portfolio import PortfolioSummary, Position
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.portfolio_ontology_statistical_concepts import (
    add_position_statistical_signal_concepts,
)
from digital_twin.domain.statistical_signals import (
    model_signal_evaluation_report,
    price_signal_rule_candidates,
    score_flow_feature_snapshot,
    score_temporal_feature_snapshot,
    statistical_rule_candidate_release,
    validate_signal_hypothesis_mapping,
)
from digital_twin.domain.time_series_storage import TemporalFeatureSnapshot, TimeSeriesWatermark
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
    def test_price_signal_is_immutable_reference_only_until_replay(self):
        snapshot = feature_snapshot()
        first = score_temporal_feature_snapshot(snapshot)
        second = score_temporal_feature_snapshot(snapshot)

        self.assertEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(4, len(first.signals))
        self.assertTrue(all(item.probability is None for item in first.signals))
        self.assertTrue(all(item.eligibility.decision_eligibility == "reference-only" for item in first.signals))
        self.assertTrue(all("historical-replay-and-calibration-required" in item.eligibility.reasons for item in first.signals))
        support = next(item for item in first.signals if item.signal_type == "price-trend-continuation-support")
        risk = next(item for item in first.signals if item.signal_type == "price-trend-break-risk")
        self.assertGreater(support.score, risk.score)
        self.assertEqual("trend-continuation", support.hypothesis_family_id)
        self.assertEqual("benchmark-adjusted-return", support.outcome_metric)
        self.assertEqual(support.observed_at, support.knowledge_cutoff_at)
        self.assertEqual("uncalibrated", support.uncertainty_status)
        self.assertIsNone(support.probability_lower)
        self.assertIsNone(support.probability_upper)

    def test_every_registered_model_signal_maps_to_a_predictive_family(self):
        self.assertEqual((), validate_signal_hypothesis_mapping())

    def test_changed_prices_change_signal_material(self):
        first = score_temporal_feature_snapshot(feature_snapshot([100, 101, 103, 105, 108, 110]))
        second = score_temporal_feature_snapshot(feature_snapshot([100, 101, 103, 102, 99, 95]))

        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        first_risk = next(item for item in first.signals if item.signal_type == "price-trend-break-risk")
        second_risk = next(item for item in second.signals if item.signal_type == "price-trend-break-risk")
        self.assertGreater(second_risk.score, first_risk.score)

    def test_flow_signal_uses_independent_daily_samples_and_remains_reference_only(self):
        positive = score_flow_feature_snapshot(flow_feature_snapshot(1))
        negative = score_flow_feature_snapshot(flow_feature_snapshot(-1))

        self.assertEqual(3, len(positive.signals))
        self.assertTrue(all(item.sample_count == 20 for item in positive.signals))
        self.assertTrue(all(item.eligibility.decision_eligibility == "reference-only" for item in positive.signals))
        positive_support = next(item for item in positive.signals if item.signal_type == "flow-accumulation-support")
        positive_risk = next(item for item in positive.signals if item.signal_type == "flow-distribution-risk")
        negative_risk = next(item for item in negative.signals if item.signal_type == "flow-distribution-risk")
        self.assertGreater(positive_support.score, positive_risk.score)
        self.assertGreater(negative_risk.score, positive_risk.score)

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

    def test_pipeline_latest_head_is_unchanged_for_same_material(self):
        feature_store = MemoryFeatureStore()
        signal_store = MemorySignalStore()
        service = StatisticalSignalPipelineService(feature_store, signal_store)
        snapshot = feature_snapshot()

        first = service.run("account-1", "questdb-shadow", snapshot.windows, snapshot.as_of)
        second = service.run("account-1", "questdb-shadow", snapshot.windows, snapshot.as_of)

        self.assertEqual("changed", first["persistence"]["signalSnapshot"]["status"])
        self.assertEqual("unchanged", second["persistence"]["signalSnapshot"]["status"])
        self.assertEqual(first["signalSnapshot"].snapshot_id, second["signalSnapshot"].snapshot_id)

    def test_later_worker_clock_does_not_change_same_observation_signal(self):
        feature_store = MemoryFeatureStore()
        signal_store = MemorySignalStore()
        service = StatisticalSignalPipelineService(feature_store, signal_store)
        snapshot = feature_snapshot()

        first = service.run("account-1", "questdb-shadow", snapshot.windows, "2026-08-10T08:00:00Z")
        second = service.run("account-1", "questdb-shadow", snapshot.windows, "2026-08-10T09:00:00Z")

        self.assertEqual("changed", first["persistence"]["signalSnapshot"]["status"])
        self.assertEqual("unchanged", second["persistence"]["signalSnapshot"]["status"])
        self.assertEqual(first["signalSnapshot"].snapshot_id, second["signalSnapshot"].snapshot_id)

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

    def test_mysql_store_removes_obsolete_latest_signal_head_for_observed_subject(self):
        snapshot = score_temporal_feature_snapshot(feature_snapshot())
        heads = [
            {
                "account_id": snapshot.account_id,
                "subject_id": "NVDA",
                "signal_type": signal.signal_type,
                "model_release_id": snapshot.model_release_id,
                "material_hash": signal.material_hash,
            }
            for signal in snapshot.signals
        ]
        heads.append({
            "account_id": snapshot.account_id,
            "subject_id": "NVDA",
            "signal_type": "obsolete-price-signal",
            "model_release_id": snapshot.model_release_id,
            "material_hash": "obsolete",
        })
        connection = FakeSignalConnection(heads)
        store = object.__new__(MySQLStatisticalModelSignalStore)
        store.transaction_with_deadlock_retry = lambda _label, operation: operation(connection)

        result = store.save(snapshot)

        delete_rows = [item for item in connection.statements if item[0].startswith("DELETE FROM")]
        self.assertEqual(1, result["removedSignalCount"])
        self.assertEqual(1, len(delete_rows))
        self.assertEqual("obsolete-price-signal", delete_rows[0][1][2])

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
        self.assertTrue(all(item.properties.get("decisionEligibility") == "reference-only" for item in signal_entities))
        self.assertTrue(all(item.properties.get("hypothesisFamilyId") for item in signal_entities))
        family_entities = [item for item in graph.entities if item.kind == "hypothesis-family-definition"]
        self.assertEqual(3, len(family_entities))

    def test_shadow_signal_packet_keeps_existing_temporal_rule_inputs(self):
        snapshot = feature_snapshot()
        position = Position(
            symbol="NVDA",
            name="NVIDIA",
            market="US",
            currency="USD",
            quantity=1,
            current_price=110,
            average_price=100,
            updated_at="2026-08-10T07:00:00Z",
            data_quality="actual",
        )
        portfolio = PortfolioSummary(110, 110, 0, [], [], 100)
        base_context = {
            "asOf": snapshot.as_of,
            "settings": {"temporalWindowPeriods": "1D=1d:2\n3D=3d:3\n5D=5d:4\n20D=20d:5"},
            "temporalObservationWindows": snapshot.windows,
        }
        legacy_graph = build_portfolio_ontology(
            [position], portfolio, portfolio_id="account-1", runtime_context=base_context,
        )
        compact_graph = build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="account-1",
            runtime_context={
                **base_context,
                "statisticalSignalSnapshot": score_temporal_feature_snapshot(snapshot).to_dict(),
            },
        )

        legacy_anchors = [item for item in legacy_graph.entities if item.kind == "temporal-observation"]
        shadow_anchors = [item for item in compact_graph.entities if item.kind == "temporal-observation"]
        self.assertGreater(len(legacy_anchors), 0)
        self.assertEqual(len(legacy_anchors), len(shadow_anchors))

    def test_explicit_anchor_disable_supports_post_promotion_compaction(self):
        snapshot = feature_snapshot()
        position = Position(
            symbol="NVDA",
            name="NVIDIA",
            market="US",
            currency="USD",
            quantity=1,
            current_price=110,
            average_price=100,
            updated_at="2026-08-10T07:00:00Z",
            data_quality="actual",
        )
        portfolio = PortfolioSummary(110, 110, 0, [], [], 100)
        graph = build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="account-1",
            runtime_context={
                "asOf": snapshot.as_of,
                "settings": {
                    "temporalWindowPeriods": "1D=1d:2\n3D=3d:3\n5D=5d:4\n20D=20d:5",
                    "ontologyTemporalObservationAnchorProjectionEnabled": "0",
                },
                "temporalObservationWindows": snapshot.windows,
                "statisticalSignalSnapshot": score_temporal_feature_snapshot(snapshot).to_dict(),
            },
        )

        self.assertEqual([], [item for item in graph.entities if item.kind == "temporal-observation"])

    def test_every_predictive_rule_has_governed_signal_migration_contract(self):
        rules = default_graph_inference_rules()
        validation = validate_rule_domain_manifests(rules)
        predictive = [
            item for item in validation["manifests"]
            if item.get("ruleKind") == "predictive-hypothesis"
        ]

        self.assertTrue(validation["valid"])
        self.assertEqual(75, len(predictive))
        self.assertTrue(all((item.get("statisticalSignalContract") or {}).get("signalTypes") for item in predictive))
        reverse_index = rule_dependency_reverse_index(rules)
        migration = reverse_index["statisticalSignals"]["byMigrationState"]
        self.assertEqual(43, len(migration["not-applicable"]))
        self.assertEqual(37, len(migration["shadow-signal-available"]))
        self.assertEqual(38, len(migration["shadow-signal-required"]))
        flow_rule = next(
            item for item in predictive
            if item.get("ruleId") == "graph.flow.sell_pressure.v1"
        )
        flow_contract = flow_rule["statisticalSignalContract"]
        self.assertEqual("implemented", flow_contract["signalAvailability"])
        self.assertIn("point-in-time-replay-and-calibration-required", flow_contract["promotionBlockers"])

    def test_shadow_migration_metadata_does_not_change_executable_rulebox_hash(self):
        rule = default_graph_inference_rules()[0]
        payload = rule.to_dict()
        without_shadow_contract = {
            **payload,
            "domain_manifest": {
                **dict(payload.get("domain_manifest") or {}),
            },
        }
        without_shadow_contract["domain_manifest"].pop("statisticalSignalContract", None)

        self.assertEqual(
            rulebox_rules_hash([payload]),
            rulebox_rules_hash([without_shadow_contract]),
        )

    def test_all_predictive_rule_candidates_are_disabled_and_require_calibrated_signals(self):
        release = statistical_rule_candidate_release(default_graph_inference_rules())

        self.assertEqual("disabled-candidate", release["status"])
        self.assertEqual(75, release["candidateCount"])
        self.assertEqual(27, len(price_signal_rule_candidates(default_graph_inference_rules())))
        self.assertFalse(release["productionEligible"])
        for rule in release["rules"]:
            self.assertFalse(rule["enabled"])
            signal_conditions = [
                item for item in rule["conditions"]
                if item.get("relation_type") == "HAS_MODEL_SIGNAL"
            ]
            self.assertEqual(1, len(signal_conditions))
            filters = signal_conditions[0]["target_property_filters"]
            self.assertEqual("calibrated", filters["validationStatus"])
            self.assertEqual("eligible", filters["decisionEligibility"])

    def test_uncalibrated_outcomes_cannot_pass_promotion_gate(self):
        signal = next(
            item for item in score_temporal_feature_snapshot(feature_snapshot()).signals
            if item.signal_type == "price-trend-continuation-support"
        )
        outcome = observe_model_signal_outcome(signal, rows([112, 115, 117, 116, 120], 12))
        report = model_signal_evaluation_report([outcome], minimum_sample_count=2)

        self.assertIsNotNone(outcome)
        self.assertEqual("blocked", report["status"])
        self.assertIn("probability-calibration-unavailable", report["promotionBlockers"])


if __name__ == "__main__":
    unittest.main()
