import hashlib
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

from digital_twin import service_manager
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    apply_scoped_abox_identity,
)
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position, utc_now_iso
from digital_twin.domain.repositories import (
    ONTOLOGY_GRAPH_REPOSITORY_CONTRACT,
    ontology_graph_repository_contract_errors,
)
from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder, migrate_typedb_rule_catalog
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules, rulebox_rules_to_payload
from digital_twin.infrastructure.typedb_ontology import (
    NullTypeDBOntologyGraphRepository,
    TypeDBOperationTimeout,
    TypeDBOntologyGraphRepository,
    TYPEDB_NATIVE_REASONING_PROFILE_VERSION,
    TYPEDB_NATIVE_RULE_ENGINE_VERSION,
    TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES,
    TYPEDB_PROMOTED_TEXT_ATTRIBUTES,
    node_boxes,
    ontology_storage_id,
    relation_row_id,
    typedb_repository_from_settings,
    typedb_inferencebox_graph,
    typedb_native_function_definition,
    typedb_native_function_call_query,
    typedb_native_any_group_check_query,
    typedb_native_match_query,
    typedb_native_rule_execution_plan,
    typedb_native_reasoning_profile,
)


class TypeDBOntologyRepositoryTests(unittest.TestCase):
    def test_typedb_schema_defines_nodes_assertions_and_storage_keys(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        schema = repository.schema_query()

        self.assertIn("entity ontology-node @abstract", schema)
        self.assertIn("relation ontology-assertion", schema)
        self.assertIn("attribute ontology-storage-id, value string", schema)
        self.assertIn("attribute ontology-scope-id, value string", schema)
        self.assertIn("attribute ontology-manifest-id, value string", schema)
        self.assertIn("owns ontology-storage-id @unique", schema)
        self.assertNotIn("owns ontology-id @key", schema)
        self.assertIn("plays ontology-assertion:source", schema)
        self.assertIn("plays ontology-assertion:target", schema)
        self.assertIn("attribute ontology-ma5-distance, value double", schema)
        self.assertIn("owns ontology-smart-money-net-volume", schema)
        self.assertIn("attribute ontology-investment-strategy-profile, value string", schema)
        self.assertIn("attribute ontology-instrument-archetype, value string", schema)
        self.assertIn("attribute ontology-factor, value string", schema)
        self.assertIn("attribute ontology-sensitivity-level, value string", schema)
        self.assertIn("attribute ontology-crypto-symbol, value string", schema)
        self.assertIn("owns ontology-crypto-symbol", schema)
        self.assertIn("attribute ontology-adr-ratio, value double", schema)
        self.assertIn("attribute ontology-leverage-factor, value double", schema)
        self.assertIn("attribute ontology-security-line-role, value string", schema)
        self.assertIn("owns ontology-leverage-factor", schema)
        self.assertIn("owns ontology-security-line-role", schema)
        self.assertIn("attribute ontology-window-key, value string", schema)
        self.assertIn("attribute ontology-review-level, value string", schema)
        self.assertIn("attribute ontology-data-state, value string", schema)
        self.assertIn("attribute ontology-event-cluster-type, value string", schema)
        self.assertIn("owns ontology-window-key", schema)
        self.assertNotIn("ontology-temporal-risk-score", schema)
        self.assertNotIn("ontology-temporal-support-score", schema)

    def test_typedb_schema_declares_every_promoted_text_attribute_used_by_nodes(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        schema = repository.schema_query()

        for attribute in TYPEDB_PROMOTED_TEXT_ATTRIBUTES.values():
            self.assertIn("attribute " + attribute + ", value string;", schema)
            self.assertIn("owns " + attribute + ",", schema)

    def test_typedb_schema_declares_every_promoted_numeric_attribute_used_by_nodes(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        schema = repository.schema_query()

        for attribute in TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.values():
            self.assertIn("attribute " + attribute + ", value double;", schema)
            self.assertIn("owns " + attribute + ",", schema)

    def test_typedb_schema_sync_skips_write_when_base_schema_is_already_current(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        class FakeDatabase:
            def __init__(self):
                self.type_schema_calls = 0

            def type_schema(self):
                self.type_schema_calls += 1
                return repository.schema_query()

        class FakeDriver:
            def __init__(self):
                self.database = FakeDatabase()
                self.databases = SimpleNamespace(get=lambda _name: self.database)
                self.transaction_calls = 0

            def transaction(self, *_args, **_kwargs):
                self.transaction_calls += 1
                raise AssertionError("current TypeDB schema must not be redefined for every snapshot")

        driver = FakeDriver()
        imported = (object, object, object, object, SimpleNamespace(SCHEMA="schema"))

        repository.ensure_schema(driver, imported)
        repository.ensure_schema(driver, imported)

        self.assertEqual(0, driver.transaction_calls)
        self.assertEqual(1, driver.database.type_schema_calls)

    def test_typedb_storage_identity_separates_abox_generations_with_same_box(self):
        active = ontology_storage_id({
            "ontologyBox": "ABox",
            "snapshotId": "abox-material:active",
        }, "stock:005930", "node")
        candidate = ontology_storage_id({
            "ontologyBox": "ABox",
            "snapshotId": "abox-material:next",
        }, "stock:005930", "node")

        self.assertNotEqual(active, candidate)
        self.assertEqual(active, ontology_storage_id({
            "ontologyBox": "ABox",
            "snapshotId": "abox-material:active",
        }, "stock:005930", "node"))

    def test_scoped_abox_changes_only_the_affected_symbol_generation(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("stock:005930", "삼성전자", "stock", {
                    "ontologyBox": "ABox", "symbol": "005930",
                }),
                OntologyEntity("price-metric:005930:currentPrice", "삼성전자 현재가", "price-metric", {
                    "ontologyBox": "ABox", "currentPrice": 70000,
                }),
                OntologyEntity("stock:MSTR", "Strategy", "stock", {
                    "ontologyBox": "ABox", "symbol": "MSTR",
                }),
                OntologyEntity("fx-rate:USDKRW", "USD/KRW", "fx-rate", {
                    "ontologyBox": "ABox", "usdKrwRate": 1400,
                }),
            ],
            relations=[
                OntologyRelation("stock:005930", "price-metric:005930:currentPrice", "HAS_PRICE", properties={
                    "ontologyBox": "ABox",
                }),
                OntologyRelation("stock:MSTR", "fx-rate:USDKRW", "EXPOSED_TO_FX", properties={
                    "ontologyBox": "ABox",
                }),
            ],
        )

        first = apply_scoped_abox_identity(graph)
        first_generations = dict(first["scopeGenerationIds"])
        self.assertIn("symbol:005930", first_generations)
        self.assertIn("symbol:MSTR", first_generations)
        self.assertIn("macro:global", first_generations)

        graph.entities[1].properties["currentPrice"] = 71000
        second = apply_scoped_abox_identity(graph)
        second_generations = dict(second["scopeGenerationIds"])

        self.assertNotEqual(first_generations["symbol:005930"], second_generations["symbol:005930"])
        self.assertEqual(first_generations["symbol:MSTR"], second_generations["symbol:MSTR"])
        self.assertEqual(first_generations["macro:global"], second_generations["macro:global"])

    def test_scoped_abox_rolls_forward_only_macro_dependents_and_resolves_cross_scope_endpoints(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("stock:005930", "삼성전자", "stock", {
                    "ontologyBox": "ABox", "symbol": "005930",
                }),
                OntologyEntity("stock:MSTR", "Strategy", "stock", {
                    "ontologyBox": "ABox", "symbol": "MSTR",
                }),
                OntologyEntity("fx-rate:USDKRW", "USD/KRW", "fx-rate", {
                    "ontologyBox": "ABox", "usdKrwRate": 1400,
                }),
            ],
            relations=[
                OntologyRelation("stock:MSTR", "fx-rate:USDKRW", "EXPOSED_TO_FX", properties={
                    "ontologyBox": "ABox",
                }),
            ],
        )
        first = apply_scoped_abox_identity(graph)
        first_generations = dict(first["scopeGenerationIds"])

        graph.entities[2].properties["usdKrwRate"] = 1410
        second = apply_scoped_abox_identity(graph)
        second_generations = dict(second["scopeGenerationIds"])

        self.assertNotEqual(first_generations["macro:global"], second_generations["macro:global"])
        self.assertNotEqual(first_generations["symbol:MSTR"], second_generations["symbol:MSTR"])
        self.assertEqual(first_generations["symbol:005930"], second_generations["symbol:005930"])

        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        nodes, relations = repository.scoped_abox_persistence_rows(graph, ["symbol:MSTR"])
        self.assertEqual(["stock:MSTR"], [row["id"] for row in nodes])
        self.assertEqual(1, len(relations))
        macro = next(item for item in graph.entities if item.entity_id == "fx-rate:USDKRW")
        self.assertEqual(
            ontology_storage_id(macro.properties, macro.entity_id, "node"),
            relations[0]["targetStorageId"],
        )

    def test_scoped_abox_persistence_includes_evidence_and_support_relation_in_the_owner_scope(self):
        graph = PortfolioOntology(
            "main",
            entities=[OntologyEntity("stock:005930", "삼성전자", "stock", {
                "ontologyBox": "ABox", "symbol": "005930",
            })],
            evidence=[OntologyEvidence(
                "evidence:005930:price",
                "stock:005930",
                "price",
                "KIS",
                "현재가",
                {"ontologyBox": "ABox"},
            )],
        )
        apply_scoped_abox_identity(graph)
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        nodes, relations = repository.scoped_abox_persistence_rows(graph, ["symbol:005930"])

        self.assertEqual({"stock:005930", "evidence:005930:price"}, {row["id"] for row in nodes})
        self.assertEqual(["HAS_EVIDENCE"], [row["type"] for row in relations])
        self.assertTrue(all(row["scopeId"] == "symbol:005930" for row in nodes + relations))

    def test_scoped_abox_finalize_runs_reference_aware_manifest_cleanup(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        with patch.object(repository, "active_abox_metadata", return_value={
            "status": "ok",
            "worldviewManifestId": "abox-manifest:next",
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
        }), patch.object(repository, "activate_scoped_abox_manifest", return_value={"status": "ok"}), patch.object(
            repository,
            "prune_inactive_scoped_abox_manifests",
            return_value={"status": "ok", "removedManifestIds": ["abox-manifest:old"]},
        ) as cleanup:
            result = repository.finalize_scoped_abox_manifest("abox-manifest:next", "abox-manifest:old")

        self.assertEqual("ok", result["status"])
        self.assertFalse(result["cleanupDeferred"])
        cleanup.assert_called_once_with()

    def test_scoped_abox_finalize_removes_legacy_complete_generation_after_inference(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        with patch.object(repository, "active_abox_metadata", return_value={
            "status": "ok",
            "worldviewManifestId": "abox-manifest:next",
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
        }), patch.object(repository, "activate_scoped_abox_manifest", return_value={"status": "ok"}), patch.object(
            repository,
            "prune_inactive_scoped_abox_manifests",
            return_value={"status": "ok"},
        ), patch.object(repository, "discard_abox_generation", return_value={
            "status": "ok",
            "deletedBatchCount": 5,
        }) as discard:
            result = repository.finalize_scoped_abox_manifest(
                "abox-manifest:next",
                "abox-material:legacy",
            )

        self.assertEqual("ok", result["status"])
        self.assertFalse(result["cleanupDeferred"])
        self.assertEqual("ok", result["cleanup"]["legacyPredecessorCleanup"]["status"])
        discard.assert_called_once_with("abox-material:legacy")

    def test_scoped_abox_save_defers_when_another_writer_holds_the_lease(self):
        graph = PortfolioOntology(
            "main",
            entities=[OntologyEntity("stock:005930", "삼성전자", "stock", {
                "ontologyBox": "ABox",
                "symbol": "005930",
            })],
        )
        apply_scoped_abox_identity(graph)
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        with patch.object(repository, "acquire_scoped_abox_write_lease", return_value={
            "acquired": False,
            "status": "held",
            "leaseOwner": "other-worker",
            "leaseExpiresAtEpoch": 9999999999,
        }):
            result = repository.save_scoped_abox_graph(graph)

        self.assertFalse(result["saved"])
        self.assertEqual("deferred-scoped-write-lease", result["status"])
        self.assertTrue(result["preservedActiveGeneration"])
        self.assertEqual("other-worker", result["writeLease"]["leaseOwner"])

    def test_orphan_scoped_candidate_inventory_keeps_manifest_referenced_generations(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        with patch.object(repository, "worldview_manifest_marker_rows", return_value=[{
            "worldviewManifestId": "abox-manifest:complete",
            "scopeGenerationIds": {"macro:global": "abox-scope:shared"},
        }]), patch.object(repository, "active_abox_metadata", return_value={
            "worldviewManifestId": "abox-manifest:complete",
            "scopeGenerationIds": {"macro:global": "abox-scope:shared"},
        }), patch.object(repository, "read_rows", side_effect=[
            [
                {"manifestId": "abox-manifest:complete", "snapshotId": "abox-scope:shared"},
                {"manifestId": "abox-manifest:orphan", "snapshotId": "abox-scope:orphan"},
            ],
            [{"manifestId": "abox-manifest:orphan", "snapshotId": "abox-scope:orphan"}],
        ]):
            inventory = repository.scoped_abox_orphan_candidate_inventory()

        self.assertEqual(["abox-manifest:orphan"], inventory["candidateManifestIds"])
        self.assertEqual(["abox-scope:orphan"], inventory["candidateGenerationIds"])
        self.assertIn("abox-scope:shared", inventory["protectedGenerationIds"])

    def test_scoped_abox_write_lease_duration_is_bounded_and_configurable(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        self.assertEqual(900, repository.scoped_abox_write_lease_seconds({}))
        self.assertEqual(120, repository.scoped_abox_write_lease_seconds({
            "typedbScopedABoxLeaseSeconds": "1",
        }))
        self.assertEqual(3600, repository.scoped_abox_write_lease_seconds({
            "typedbScopedABoxLeaseSeconds": "999999",
        }))
        self.assertEqual(4, repository.scoped_abox_orphan_cleanup_max_generations({}))
        self.assertEqual(1, repository.scoped_abox_orphan_cleanup_max_generations({
            "typedbScopedABoxOrphanCleanupMaxGenerations": "0",
        }))
        self.assertEqual(20, repository.scoped_abox_orphan_cleanup_max_generations({
            "typedbScopedABoxOrphanCleanupMaxGenerations": "999999",
        }))

    def test_relation_batch_matches_cross_box_endpoints_without_dropping_the_batch(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        graph = PortfolioOntology(
            "cross-box-test",
            entities=[
                OntologyEntity(
                    "ontology-box:RuleBox",
                    "RuleBox",
                    "ontology-box",
                    {"ontologyBox": "TBox"},
                ),
                OntologyEntity(
                    "rule-registry:test",
                    "Rule registry",
                    "rule-registry",
                    {"ontologyBox": "RuleBox"},
                ),
            ],
            relations=[
                OntologyRelation(
                    "ontology-box:RuleBox",
                    "rule-registry:test",
                    "DEFINES_RULE",
                    properties={"ontologyBox": "RuleBox"},
                ),
            ],
        )

        _nodes, relations = repository.graph_persistence_rows(graph)
        query = repository.batched_relation_insert_queries(relations, "2026-07-21T00:00:00Z")[0]

        self.assertIn(relations[0]["sourceStorageId"], query)
        self.assertIn(relations[0]["targetStorageId"], query)
        self.assertNotIn('has ontology-id "ontology-box:RuleBox";', query)
        self.assertNotIn('has ontology-id "rule-registry:test";', query)

    def test_seed_ontology_repairs_missing_static_relations_before_full_reseed(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        stale = {
            "ready": False,
            "status": "stale",
            "expectedBoxCounts": {
                "TBox": {"entityCount": 2, "relationCount": 3},
                "RuleBox": {"entityCount": 2, "relationCount": 3},
            },
            "actualBoxCounts": {
                "TBox": {"entityCount": 2, "relationCount": 2},
                "RuleBox": {"entityCount": 2, "relationCount": 2},
            },
        }
        current = {**stale, "ready": True, "status": "current"}
        current["actualBoxCounts"] = {
            "TBox": {"entityCount": 2, "relationCount": 3},
            "RuleBox": {"entityCount": 2, "relationCount": 3},
        }

        with patch.object(repository, "seed_graph_preflight", side_effect=[stale, current]), \
                patch.object(repository, "repair_seed_relations", return_value={
                    "saved": True,
                    "status": "ok",
                    "missingRelationCount": 2,
                    "insertedRelationCount": 2,
                }) as repair, \
                patch.object(repository, "sync_typedb_native_rule_functions", return_value={"status": "ok"}), \
                patch.object(repository, "save_graph") as full_seed:
            result = repository.seed_ontology({"replaceRuleBox": True})

        self.assertEqual("repaired", result["status"])
        self.assertTrue(result["saved"])
        self.assertEqual(2, result["staticRelationRepair"]["insertedRelationCount"])
        repair.assert_called_once()
        full_seed.assert_not_called()

    def test_typedb_identity_schema_migration_detects_legacy_key_schema(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        legacy = (
            "entity ontology-node @abstract, owns ontology-id @key;\n"
            "relation ontology-assertion, owns ontology-id @key;"
        )

        self.assertTrue(repository.ontology_storage_identity_migration_required(legacy))
        self.assertFalse(repository.ontology_storage_identity_migration_required(repository.schema_query()))

    def test_typedb_driver_request_timeout_covers_longer_write_operation(self):
        repository = TypeDBOntologyGraphRepository(
            "127.0.0.1:1729",
            timeout_seconds=20,
            query_timeout_seconds=20,
            schema_operation_timeout_seconds=120,
            write_operation_timeout_seconds=180,
        )

        self.assertEqual(180, repository.driver_request_timeout_seconds())

    def test_typedb_write_transaction_options_cover_write_operation_timeout(self):
        repository = TypeDBOntologyGraphRepository(
            "127.0.0.1:1729",
            write_operation_timeout_seconds=180,
        )

        options = repository.write_transaction_options()

        self.assertIsNotNone(options)
        self.assertEqual(180000, options.transaction_timeout_millis)

    def test_typedb_read_transaction_options_cover_query_timeout(self):
        repository = TypeDBOntologyGraphRepository(
            "127.0.0.1:1729",
            query_timeout_seconds=17,
        )

        options = repository.read_transaction_options()

        self.assertIsNotNone(options)
        self.assertEqual(17000, options.transaction_timeout_millis)

    def test_typedb_filtered_schema_function_match_pushes_symbol_filter_into_typeql(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", query_timeout_seconds=17)
        rule = default_graph_inference_rules()[0]
        queries = []
        transaction_options = []

        class FakePromise:
            def resolve(self):
                return []

        class FakeTransaction:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def query(self, query):
                queries.append(query)
                return FakePromise()

        class FakeDriver:
            def transaction(self, _database, _transaction_type, options=None):
                transaction_options.append(options)
                return FakeTransaction()

        driver = FakeDriver()
        imported = (object, object, object, object, SimpleNamespace(READ="read"))

        with patch.object(repository, "driver_imports", return_value=(imported, None)), \
                patch.object(repository, "open_driver", return_value=driver), \
                patch.object(repository, "ensure_database"), \
                patch.object(repository, "close_driver"), \
                patch.object(repository, "active_abox_rule_context", return_value={
                    "status": "ok",
                    "relationTypesBySymbol": {"005930": ["HAS_RISK_BUDGET", "BREAKS_LEVEL"]},
                }):
            result = repository.match_typedb_native_rules([rule], target_symbols=["005930"])

        self.assertEqual("ok", result["status"])
        self.assertEqual("typedb-schema-function-filtered-planned", result["nativeExecutionMode"])
        self.assertTrue(result["schemaFunctionUsed"])
        self.assertEqual(1, result["readQueryCount"])
        self.assertEqual(32000, transaction_options[0].transaction_timeout_millis)
        self.assertIn('has ontology-symbol "005930"', queries[0])
        self.assertIn("let $source in orbit_rule_", queries[0])
        self.assertIn("($candidate)", queries[0])
        self.assertLess(
            queries[0].index('has ontology-symbol "005930"'),
            queries[0].index("let $source in orbit_rule_"),
        )
        self.assertEqual(1, result["executionPlan"]["selectedRuleCount"])

    def test_typedb_any_rule_uses_one_read_snapshot_for_function_and_cardinality_check(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)
        rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.loss_smart_money.add_buy_review.v1"
        )
        queries = []
        transaction_options = []

        class FakeConcept:
            def __init__(self, value):
                self.value = value

            def get_value(self):
                return self.value

        class FakeRow:
            def get(self, name):
                return FakeConcept({
                    "sourceId": "stock:000660",
                    "sourceLabel": "SK하이닉스",
                }.get(name))

        class FakePromise:
            def resolve(self):
                return [FakeRow()]

        class FakeTransaction:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def query(self, query):
                queries.append(query)
                return FakePromise()

        class FakeDriver:
            def transaction(self, _database, _transaction_type, options=None):
                transaction_options.append(options)
                return FakeTransaction()

        driver = FakeDriver()
        imported = (object, object, object, object, SimpleNamespace(READ="read"))
        with patch.object(repository, "driver_imports", return_value=(imported, None)), \
                patch.object(repository, "open_driver", return_value=driver), \
                patch.object(repository, "ensure_database"), \
                patch.object(repository, "close_driver"), \
                patch.object(repository, "active_abox_uses_scoped_manifest", return_value=True):
            result = repository.match_typedb_native_rules([rule])

        self.assertEqual("ok", result["status"])
        self.assertEqual("typedb-schema-function-hybrid-any-verified", result["nativeExecutionMode"])
        self.assertEqual(1, result["readTransactionCount"])
        self.assertEqual(2, result["readQueryCount"])
        self.assertEqual(1, len(transaction_options))
        self.assertEqual(2, len(queries))
        self.assertIn("let $source in orbit_rule_", queries[0])
        self.assertIn("reduce $anyConditionCount = count($anyConditionToken) groupby $source", queries[1])
        self.assertEqual(1, result["matchedCount"])

    def test_typedb_native_rule_execution_plan_prunes_impossible_rules_before_function_calls(self):
        rules = default_graph_inference_rules()
        plan = typedb_native_rule_execution_plan(
            rules,
            ["000660"],
            {
                "000660": [
                    "HAS_RISK_BUDGET",
                    "HAS_TREND_TRANSITION",
                    "BREAKS_LEVEL",
                ],
            },
            query_limit=1,
        )

        selected_ids = [item["ruleId"] for item in plan["selectedEntries"]]
        skipped = {item["ruleId"]: item for item in plan["skippedEntries"]}

        self.assertGreater(len(selected_ids), 1)
        self.assertIn("graph.holding.trend_transition.risk.v1", selected_ids)
        self.assertEqual("not-applicable", skipped["graph.winner_momentum.add_buy_review.v1"]["status"])
        self.assertNotIn("graph.loss_guard.breakdown.v1", skipped)
        self.assertFalse(any(
            item.get("status") == "deferred-by-query-budget"
            for item in plan["skippedEntries"]
        ))
        self.assertEqual(0, plan["queryLimit"])

    def test_typedb_native_rule_execution_plan_never_starves_non_temporal_rules(self):
        rules = default_graph_inference_rules()
        plan = typedb_native_rule_execution_plan(
            rules,
            ["000660"],
            {
                "000660": [
                    "HAS_TEMPORAL_WINDOW",
                    "HAS_COVERAGE_GAP",
                ],
            },
            query_limit=10,
        )

        selected_ids = [item["ruleId"] for item in plan["selectedEntries"]]

        self.assertGreater(len(selected_ids), 10)
        self.assertTrue(any(rule_id.startswith("graph.temporal.") for rule_id in selected_ids))
        self.assertTrue(any(not rule_id.startswith("graph.temporal.") for rule_id in selected_ids))
        self.assertFalse(any(
            item.get("status") == "deferred-by-query-budget"
            for item in plan["skippedEntries"]
        ))
        self.assertIn("graph.temporal.persistent_decline.risk.v1", selected_ids)
        self.assertIn("graph.temporal.stale_observation.block.v1", selected_ids)

    def test_typedb_native_rule_preflight_prunes_only_a_proven_required_relation_mismatch(self):
        rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.data_quality.action_block.v1"
        )
        graph = PortfolioOntology("typedb-preflight")
        graph.entities.extend([
            OntologyEntity("stock:005930", "삼성전자", "stock", {
                "ontologyBox": "ABox",
                "symbol": "005930",
            }),
            OntologyEntity("missing:005930", "수급 결측", "missing-data", {
                "ontologyBox": "ABox",
                "dataScope": "fundamentals",
            }),
        ])
        graph.relations.append(OntologyRelation(
            "stock:005930",
            "missing:005930",
            "HAS_DATA_QUALITY",
            properties={"ontologyBox": "ABox", "evidenceRole": "risk"},
        ))

        mismatch_plan = typedb_native_rule_execution_plan(
            [rule],
            ["005930"],
            {"005930": ["HAS_DATA_QUALITY"]},
            preflight_graph=graph,
        )

        self.assertEqual([], mismatch_plan["selectedEntries"])
        mismatch = mismatch_plan["skippedEntries"][0]
        self.assertEqual("not-applicable-preflight", mismatch["status"])
        self.assertEqual(
            ["005930"],
            list(mismatch["preflightPrunedSymbols"]),
        )
        self.assertEqual(
            ["severe-microstructure-gap"],
            mismatch["preflightPrunedSymbols"]["005930"]["failedConditionIds"],
        )

        graph.entities[1].properties["dataScope"] = "market-microstructure"
        matching_plan = typedb_native_rule_execution_plan(
            [rule],
            ["005930"],
            {"005930": ["HAS_DATA_QUALITY"]},
            preflight_graph=graph,
        )

        self.assertEqual([rule.rule_id], [item["ruleId"] for item in matching_plan["selectedEntries"]])
        self.assertEqual([], matching_plan["skippedEntries"])

    def test_typedb_native_rule_preflight_keeps_incoming_relation_rules_when_endpoint_scan_is_deferred(self):
        rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.news.ai_direct_risk.v1"
        )
        graph = PortfolioOntology(
            "typedb-preflight-incoming",
            entities=[OntologyEntity("stock:005930", "삼성전자", "stock", {
                "ontologyBox": "ABox",
                "symbol": "005930",
                "source": "holding",
            })],
        )

        plan = typedb_native_rule_execution_plan(
            [rule],
            ["005930"],
            {"005930": ["AFFECTS"]},
            preflight_graph=graph,
            preflight_incoming_relations_complete=False,
        )

        self.assertEqual([rule.rule_id], [item["ruleId"] for item in plan["selectedEntries"]])
        self.assertEqual([], plan["skippedEntries"])

    def test_active_abox_rule_context_reads_relation_types_without_evaluating_rule_values(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        with patch.object(
            repository,
            "active_abox_relation_types_by_symbol",
            return_value={
                "status": "ok",
                "symbols": ["000660"],
                "sourceIdsBySymbol": {"000660": ["stock:000660"]},
                "relationTypesBySymbol": {"000660": ["HAS_TREND_TRANSITION"]},
                "relationCount": 1,
            },
        ):
            context = repository.active_abox_rule_context(["000660"])

        self.assertEqual("ok", context["status"])
        self.assertEqual(["stock:000660"], context["sourceIdsBySymbol"]["000660"])
        self.assertEqual(["HAS_TREND_TRANSITION"], context["relationTypesBySymbol"]["000660"])

    def test_active_abox_relation_type_reader_returns_compact_topology_only(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        queries = []

        def read_rows(query, _columns, **_kwargs):
            queries.append(query)
            if "links (source: $stock, target: $other)" in query:
                return [{
                    "sourceId": "stock:000660",
                    "symbol": "000660",
                    "relationId": "relation:trend",
                    "relationType": "HAS_TREND_TRANSITION",
                }]
            return [{
                "sourceId": "stock:000660",
                "symbol": "000660",
                "relationId": "relation:portfolio",
                "relationType": "IN_PORTFOLIO",
            }]

        with patch.object(repository, "active_abox_uses_scoped_manifest", return_value=True), \
                patch.object(repository, "read_rows", side_effect=read_rows):
            topology = repository.active_abox_relation_types_by_symbol(["000660"])

        self.assertEqual("ok", topology["status"])
        self.assertEqual(["stock:000660"], topology["sourceIdsBySymbol"]["000660"])
        self.assertEqual(
            ["HAS_TREND_TRANSITION", "IN_PORTFOLIO"],
            topology["relationTypesBySymbol"]["000660"],
        )
        self.assertEqual(2, topology["relationCount"])
        self.assertEqual(2, len(queries))
        self.assertTrue(all("ontology-json" not in query for query in queries))
        self.assertTrue(all(query.count('worldview-manifest-active-pointer') == 1 for query in queries))
        self.assertTrue(all('has ontology-kind "abox-active-pointer"' not in query for query in queries))

    def test_typedb_native_rule_query_timeout_returns_partial_result(self):
        repository = TypeDBOntologyGraphRepository(
            "127.0.0.1:1729",
            retry_count=0,
            native_rule_query_timeout_seconds=0.5,
            native_rule_execution_budget_seconds=1,
        )
        rule = default_graph_inference_rules()[0]

        class FakePromise:
            def resolve(self):
                raise RuntimeError("TypeDB read query timed out after 0.5s")

        class FakeTransaction:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def query(self, _query):
                return FakePromise()

        class FakeDriver:
            def transaction(self, _database, _transaction_type, options=None):
                return FakeTransaction()

        driver = FakeDriver()
        imported = (object, object, object, object, SimpleNamespace(READ="read"))
        with patch.object(repository, "driver_imports", return_value=(imported, None)), \
                patch.object(repository, "open_driver", return_value=driver), \
                patch.object(repository, "ensure_database"), \
                patch.object(repository, "close_driver"), \
                patch.object(repository, "active_abox_rule_context", return_value={
                    "status": "ok",
                    "relationTypesBySymbol": {"005930": ["HAS_RISK_BUDGET", "BREAKS_LEVEL"]},
                }):
            result = repository.match_typedb_native_rules([rule], target_symbols=["005930"])

        self.assertEqual("partial", result["status"])
        self.assertFalse(result["nativeQueryUsed"])
        self.assertEqual(1, result["readTransactionCount"])
        self.assertEqual("query-timeout", result["skippedRules"][0]["status"])

    def test_typedb_native_rule_profile_gap_blocks_partial_investment_judgement(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)
        rule = default_graph_inference_rules()[0]

        class FakeDriver:
            def transaction(self, *_args, **_kwargs):
                raise AssertionError("A non-native rule profile must not execute a partial query")

        driver = FakeDriver()
        imported = (object, object, object, object, SimpleNamespace(READ="read"))
        with patch.object(repository, "driver_imports", return_value=(imported, None)), \
                patch.object(repository, "open_driver", return_value=driver), \
                patch.object(repository, "ensure_database"), \
                patch.object(repository, "close_driver"), \
                patch.object(repository, "active_abox_rule_context", return_value={
                    "status": "ok",
                    "relationTypesBySymbol": {"005930": ["HAS_RISK_BUDGET", "BREAKS_LEVEL"]},
                }), \
                patch("digital_twin.infrastructure.typedb_ontology.typedb_native_rule_profile", return_value={"status": "partial"}):
            result = repository.match_typedb_native_rules([rule], target_symbols=["005930"])

        self.assertEqual("partial", result["status"])
        self.assertFalse(result["nativeQueryUsed"])
        self.assertEqual(0, result["readQueryCount"])
        self.assertEqual("partial", result["skippedRules"][0]["status"])

    def test_typedb_abox_delete_query_limits_each_transaction(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        query = repository.box_delete_batch_query("ABox", "ontology-assertion", 200)

        self.assertEqual(
            'match $r isa ontology-assertion, has ontology-box "ABox"; limit 200; delete $r;',
            query,
        )

    def test_typedb_abox_delete_batch_size_uses_large_safe_default(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        self.assertEqual(1000, repository.abox_delete_batch_size({}))
        self.assertEqual(100, repository.abox_delete_batch_size({
            "typedbABoxDeleteBatchSize": "1",
        }))
        self.assertEqual(5000, repository.abox_delete_batch_size({
            "typedbABoxDeleteBatchSize": "99999",
        }))

    def test_typedb_incremental_abox_cleanup_uses_small_bounded_defaults(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        self.assertEqual(50, repository.abox_incremental_cleanup_batch_size({}))
        self.assertEqual(10, repository.abox_incremental_cleanup_batch_size({
            "typedbABoxIncrementalCleanupBatchSize": "1",
        }))
        self.assertEqual(500, repository.abox_incremental_cleanup_batch_size({
            "typedbABoxIncrementalCleanupBatchSize": "99999",
        }))
        self.assertEqual(1, repository.abox_incremental_cleanup_max_batches_per_save({}))
        self.assertEqual(4, repository.abox_incremental_cleanup_max_batches_per_save({
            "typedbABoxIncrementalCleanupMaxBatchesPerSave": "999",
        }))

    def test_typedb_abox_relation_batch_size_caps_expensive_endpoint_matches(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        self.assertEqual(4, repository.abox_relation_batch_size({}))
        self.assertEqual(1, repository.abox_relation_batch_size({
            "typedbABoxRelationBatchSize": "1",
        }))
        self.assertEqual(8, repository.abox_relation_batch_size({
            "typedbABoxRelationBatchSize": "1000",
        }))

    def test_typedb_abox_cleanup_preserves_only_the_active_generation(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        driver = object()
        imported = (object(), None)
        with patch.object(repository, "abox_candidate_snapshot_ids", return_value=["active", "failed"]), \
                patch.object(repository, "delete_box_snapshot_rows_in_batches", return_value={
                    "status": "ok", "deletedBatchCount": 2,
                }) as delete_candidate, \
                patch.object(repository, "delete_box_rows_in_batches", return_value={
                    "status": "ok", "deletedBatchCount": 1,
                }) as delete_legacy:
            result = repository.cleanup_inactive_abox_candidates(
                driver,
                imported,
                "active",
            )

        self.assertEqual(["failed"], result["removedCandidateSnapshotIds"])
        self.assertEqual(3, result["deletedBatchCount"])
        delete_candidate.assert_called_once_with(driver, imported, "ABox", "failed")
        delete_legacy.assert_called_once_with(driver, imported, ["ABoxStaging"])

    def test_typedb_incremental_abox_cleanup_limits_historical_deletion_to_one_slice(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        driver = object()
        imported = (object(), None)
        with patch.object(repository, "pending_abox_activation", return_value={"status": "empty"}), \
                patch.object(repository, "abox_candidate_snapshot_ids", return_value=["active", "partial", "old"]), \
                patch.object(repository, "abox_projection_marker_rows", return_value=[{
                    "id": "marker:old",
                    "aboxSnapshotId": "old",
                    "updatedAt": "2026-07-21T00:01:00Z",
                }]), \
                patch.object(repository, "abox_inactive_generation_keep_count", return_value=0), \
                patch.object(repository, "abox_incremental_cleanup_batch_size", return_value=50), \
                patch.object(repository, "abox_incremental_cleanup_max_batches_per_save", return_value=1), \
                patch.object(repository, "delete_box_snapshot_rows_in_batches", return_value={
                    "status": "partial",
                    "aboxSnapshotId": "partial",
                    "deletedBatchCount": 1,
                }) as delete_slice:
            result = repository.drain_inactive_abox_generations_incrementally(
                driver,
                imported,
                "active",
            )

        self.assertEqual("partial", result["status"])
        self.assertEqual(["partial"], result["attemptedSnapshotIds"])
        self.assertEqual(["partial", "old"], result["remainingSnapshotIds"])
        self.assertEqual(1, result["deletedBatchCount"])
        delete_slice.assert_called_once_with(
            driver,
            imported,
            "ABox",
            "partial",
            batch_size=50,
            max_batches=1,
        )

    def test_typedb_save_graph_retains_previous_active_abox_until_inference_finalizes(self):
        """Activation retains the predecessor so failed inference can restore it."""
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)
        graph = PortfolioOntology(
            "typedb-live-abox",
            entities=[OntologyEntity(
                entity_id="stock:000660",
                label="SK하이닉스",
                kind="stock",
                properties={"ontologyBox": "ABox", "aboxSnapshotId": "candidate"},
            )],
            worldview={"aboxSnapshotId": "candidate", "materialFingerprint": "candidate-fingerprint"},
        )
        marker_graph = PortfolioOntology(
            "typedb-live-abox-marker",
            entities=[OntologyEntity(
                entity_id="abox-projection-marker:candidate",
                label="ABox projection marker",
                kind="abox-projection-marker",
                properties={"ontologyBox": "ABox"},
            )],
        )
        with patch.object(repository, "driver_imports", return_value=((object, object, object, object, object), None)), \
                patch.object(repository, "open_driver", return_value=object()), \
                patch.object(repository, "close_driver"), \
                patch.object(repository, "ensure_database"), \
                patch.object(repository, "ensure_schema"), \
                patch.object(repository, "graph_persistence_rows", return_value=([{}], [{}])), \
                patch.object(repository, "abox_projection_marker_graph", return_value=marker_graph), \
                patch.object(repository, "delete_box_snapshot_rows_in_batches", return_value={"status": "ok"}) as clear_retry, \
                patch.object(repository, "write_graph") as write_graph, \
                patch.object(repository, "verify_abox_projection", return_value={"status": "ok"}), \
                patch.object(repository, "active_abox_metadata", side_effect=[
                    {"status": "ok", "aboxSnapshotId": "active"},
                    {"status": "ok", "aboxSnapshotId": "candidate"},
                ]), \
                patch.object(repository, "prune_inactive_abox_generations", return_value={
                    "status": "ok",
                    "activeAboxSnapshotId": "candidate",
                    "removedCandidateSnapshotIds": ["old"],
                    "deletedBatchCount": 2,
                }) as cleanup:
            result = repository.save_graph(graph)

        self.assertTrue(result["saved"])
        activation = result["aboxPersistenceVerification"]["activation"]
        self.assertEqual("activated", activation["status"])
        self.assertEqual("candidate", activation["snapshotId"])
        self.assertEqual("active", activation["previousSnapshotId"])
        self.assertTrue(activation["finalizationRequired"])
        self.assertEqual(1, clear_retry.call_count)
        self.assertEqual("candidate", clear_retry.call_args_list[0].args[3])
        cleanup.assert_not_called()
        self.assertEqual(3, write_graph.call_count)

    def test_typedb_abox_default_retention_keeps_no_inactive_generation(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        with patch("digital_twin.infrastructure.typedb_ontology.runtime_settings", return_value={}):
            self.assertEqual(0, repository.abox_inactive_generation_keep_count())

    def test_typedb_abox_retention_keeps_recent_completed_generation_and_prunes_oldest_first(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        markers = [
            {"id": "marker:active", "aboxSnapshotId": "active", "updatedAt": "2026-07-21T00:04:00Z"},
            {"id": "marker:newest", "aboxSnapshotId": "newest", "updatedAt": "2026-07-21T00:03:00Z"},
            {"id": "marker:middle", "aboxSnapshotId": "middle", "updatedAt": "2026-07-21T00:02:00Z"},
            {"id": "marker:oldest", "aboxSnapshotId": "oldest", "updatedAt": "2026-07-21T00:01:00Z"},
        ]
        with patch.object(repository, "abox_projection_marker_rows", return_value=markers), \
                patch.object(repository, "delete_box_snapshot_rows_in_batches", side_effect=[
                    {"status": "ok", "deletedBatchCount": 2},
                    {"status": "ok", "deletedBatchCount": 3},
                ]) as delete_candidate:
            result = repository.prune_inactive_abox_generations(
                object(),
                (object(), None),
                active_snapshot_id="active",
                keep_inactive_count=1,
                max_generations=2,
            )

        self.assertEqual(["newest"], result["retainedInactiveSnapshotIds"])
        self.assertEqual(["oldest", "middle"], result["removedCandidateSnapshotIds"])
        self.assertEqual(5, result["deletedBatchCount"])
        self.assertEqual(2, delete_candidate.call_count)

    def test_typedb_active_abox_keeps_last_completed_generation_when_newer_marker_is_incomplete(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        markers = [
            {
                "id": "abox-projection-marker:new",
                "aboxSnapshotId": "new",
                "materialFingerprint": "new-fingerprint",
                "expectedAboxEntityCount": 4,
                "expectedAboxRelationCount": 3,
                "updatedAt": "2026-07-21T01:00:00Z",
            },
            {
                "id": "abox-projection-marker:old",
                "aboxSnapshotId": "old",
                "materialFingerprint": "old-fingerprint",
                "expectedAboxEntityCount": 2,
                "expectedAboxRelationCount": 1,
                "updatedAt": "2026-07-21T00:00:00Z",
            },
        ]

        with patch.object(repository, "active_worldview_manifest_pointer_rows", return_value=[], create=True), \
                patch.object(repository, "abox_projection_marker_rows", return_value=markers), \
                patch.object(repository, "active_abox_pointer_rows", return_value=[{
                    "id": "abox-active-pointer",
                    "aboxSnapshotId": "old",
                    "updatedAt": "2026-07-21T00:30:00Z",
                }]), \
                patch.object(repository, "box_snapshot_row_counts", side_effect=lambda _box, snapshot: (
                    {"entityCount": 3, "relationCount": 1}
                    if snapshot == "old"
                    else {"entityCount": 4, "relationCount": 2}
                )):
            metadata = repository.active_abox_metadata()

        self.assertEqual("ok", metadata["status"])
        self.assertEqual("old", metadata["aboxSnapshotId"])

    def test_typedb_abox_candidate_keeps_its_generation_in_the_live_box(self):
        graph = PortfolioOntology("typedb-staging")
        graph.entities.append(OntologyEntity(
            entity_id="stock:005930",
            label="Samsung Electronics",
            kind="stock",
            properties={"ontologyBox": "ABox", "snapshotId": "abox-material:test"},
        ))
        graph.entities.append(OntologyEntity(
            entity_id="signal:005930",
            label="Signal",
            kind="signal",
            properties={"ontologyBox": "ABox", "snapshotId": "abox-material:test"},
        ))
        graph.relations.append(OntologyRelation(
            "stock:005930",
            "signal:005930",
            "HAS_SIGNAL",
            properties={"ontologyBox": "ABox", "snapshotId": "abox-material:test"},
        ))
        graph.evidence.append(OntologyEvidence(
            "evidence:005930",
            "stock:005930",
            "price",
            "test",
            "price evidence",
            {"ontologyBox": "ABox", "snapshotId": "abox-material:test"},
        ))

        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        candidate = repository.abox_candidate_graph(graph)
        pointer = repository.abox_active_pointer_graph(candidate)

        self.assertEqual(["ABox"], node_boxes(candidate))
        self.assertTrue(all(item.properties["ontologyBox"] == "ABox" for item in candidate.entities))
        self.assertTrue(all(item.properties["ontologyBox"] == "ABox" for item in candidate.relations))
        self.assertTrue(all(item.value["ontologyBox"] == "ABox" for item in candidate.evidence))
        self.assertEqual("ABoxControl", pointer.entities[0].properties["ontologyBox"])
        self.assertEqual("abox-material:test", pointer.entities[0].properties["snapshotId"])

    def test_typedb_abox_pointer_persists_pending_native_inference_handoff(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        candidate = PortfolioOntology(
            "typedb-pending-control",
            worldview={
                "aboxSnapshotId": "abox-material:candidate",
                "materialFingerprint": "candidate-fingerprint",
                "inferenceTargetSymbols": ["000660", "005930"],
            },
        )

        pointer = repository.abox_active_pointer_graph(candidate, previous_snapshot_id="abox-material:previous")
        pending = next(item for item in pointer.entities if item.kind == "abox-activation-pending")
        cleared = repository.abox_active_pointer_graph(candidate, pending_activation=False)

        self.assertEqual("abox-material:candidate", pending.properties["candidateAboxSnapshotId"])
        self.assertEqual("abox-material:previous", pending.properties["previousAboxSnapshotId"])
        self.assertEqual(["000660", "005930"], pending.properties["targetSymbols"])
        self.assertEqual(1, len(cleared.entities))
        self.assertEqual("abox-active-pointer", cleared.entities[0].kind)

    def test_scoped_manifest_pending_graph_keeps_the_previous_pointer_out_of_the_candidate_stage(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        candidate = PortfolioOntology(
            "typedb-staged-manifest",
            worldview={
                "worldviewManifestId": "abox-manifest:candidate",
                "aboxSnapshotId": "abox-manifest:candidate",
                "scopeGenerationIds": {"symbol:000660": "abox-scope:candidate"},
                "scopeFingerprints": {"symbol:000660": "fingerprint"},
                "inferenceTargetSymbols": ["000660"],
            },
        )
        scope_plan = [{
            "scopeId": "symbol:000660",
            "scopeType": "symbol",
            "generationId": "abox-scope:candidate",
            "fingerprint": "fingerprint",
        }]

        staged = repository.scoped_manifest_pending_graph(
            candidate,
            scope_plan,
            {"worldviewManifestId": "abox-manifest:previous"},
        )

        self.assertEqual(1, len(staged.entities))
        self.assertEqual("abox-activation-pending", staged.entities[0].kind)
        self.assertEqual("staged-native-inference", staged.entities[0].properties["activationStatus"])
        self.assertEqual("abox-manifest:previous", staged.entities[0].properties["previousAboxSnapshotId"])
        self.assertFalse(any(item.kind == "worldview-manifest-active-pointer" for item in staged.entities))

    def test_typedb_pending_abox_recovery_leaves_unactivated_staged_manifest_intact(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        pending = {
            "status": "pending",
            "activationStatus": "staged-native-inference",
            "candidateAboxSnapshotId": "abox-manifest:candidate",
            "previousAboxSnapshotId": "abox-manifest:previous",
        }
        with patch.object(repository, "pending_abox_activation", return_value=pending), \
                patch.object(repository, "active_abox_metadata", return_value={
                    "status": "ok", "aboxSnapshotId": "abox-manifest:previous",
                }), \
                patch.object(repository, "activate_abox_generation") as activate:
            result = repository.recover_pending_abox_activation()

        self.assertEqual("staged", result["status"])
        self.assertEqual("abox-manifest:previous", result["activeAboxSnapshotId"])
        activate.assert_not_called()

    def test_typedb_prepares_staged_manifest_only_when_the_expected_predecessor_is_active(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        staged = {
            "status": "pending",
            "activationStatus": "staged-native-inference",
            "candidateAboxSnapshotId": "abox-manifest:candidate",
            "previousAboxSnapshotId": "abox-manifest:previous",
            "targetSymbols": ["000660"],
        }
        active_before = {"status": "ok", "worldviewManifestId": "abox-manifest:previous"}
        active_after = {"status": "ok", "worldviewManifestId": "abox-manifest:candidate"}
        pending_after = {**staged, "activationStatus": "pending-native-inference"}
        with patch.object(repository, "pending_abox_activation", side_effect=[staged, pending_after]), \
                patch.object(repository, "active_abox_metadata", side_effect=[active_before, active_after]), \
                patch.object(repository, "activate_scoped_abox_manifest", return_value={"status": "ok"}) as activate:
            result = repository.prepare_pending_abox_activation_for_inference()

        self.assertEqual("activated", result["status"])
        activate.assert_called_once_with(
            "abox-manifest:candidate",
            previous_metadata=active_before,
            pending_activation=True,
        )

    def test_typedb_pending_abox_recovery_restores_previous_generation_on_stale_inference(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        pending = {
            "status": "pending",
            "candidateAboxSnapshotId": "abox-material:candidate",
            "previousAboxSnapshotId": "abox-material:previous",
            "targetSymbols": ["000660"],
        }
        with patch.object(repository, "pending_abox_activation", return_value=pending), \
                patch.object(repository, "active_abox_metadata", return_value={
                    "status": "ok", "aboxSnapshotId": "abox-material:candidate",
                }), \
                patch.object(repository, "inferencebox_snapshot", return_value={
                    "status": "stale-generation",
                    "sourceAboxSnapshotId": "abox-material:previous",
                    "generationAligned": False,
                }), \
                patch.object(repository, "activate_abox_generation", return_value={"status": "ok"}) as restore:
            result = repository.recover_pending_abox_activation()

        self.assertEqual("restored", result["status"])
        restore.assert_called_once_with("abox-material:previous")

    def test_typedb_pending_abox_recovery_finalizes_aligned_inference(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        pending = {
            "status": "pending",
            "candidateAboxSnapshotId": "abox-material:candidate",
            "previousAboxSnapshotId": "abox-material:previous",
            "targetSymbols": ["000660"],
        }
        with patch.object(repository, "pending_abox_activation", return_value=pending), \
                patch.object(repository, "active_abox_metadata", return_value={
                    "status": "ok", "aboxSnapshotId": "abox-material:candidate",
                }), \
                patch.object(repository, "inferencebox_snapshot", return_value={
                    "status": "ok",
                    "nativeTypeDbReasoningUsed": True,
                    "generationAligned": True,
                    "sourceAboxSnapshotId": "abox-material:candidate",
                    "targetSymbols": ["000660"],
                }), \
                patch.object(repository, "finalize_abox_generation", return_value={"status": "ok"}) as finalize:
            result = repository.recover_pending_abox_activation()

        self.assertEqual("finalized", result["status"])
        finalize.assert_called_once_with("abox-material:candidate", "abox-material:previous")

    def test_typedb_abox_finalization_clears_journal_without_blocking_on_predecessor_cleanup(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        with patch.object(repository, "active_abox_metadata", return_value={
            "status": "ok", "aboxSnapshotId": "abox-material:candidate",
        }), \
                patch.object(repository, "activate_abox_generation", return_value={"status": "ok"}) as activate, \
                patch.object(repository, "driver_imports") as driver_imports:
            result = repository.finalize_abox_generation(
                "abox-material:candidate",
                "abox-material:previous",
            )

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["clearedPendingActivation"])
        self.assertTrue(result["cleanupDeferred"])
        self.assertEqual("deferred", result["cleanup"]["status"])
        activate.assert_called_once_with("abox-material:candidate")
        driver_imports.assert_not_called()

    def test_typedb_abox_activation_replaces_only_control_pointer_in_one_transaction(self):
        class FakePromise:
            def resolve(self):
                return []

        class FakeTransaction:
            def __init__(self, calls):
                self.calls = calls

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def query(self, query):
                self.calls.append(query)
                return FakePromise()

            def commit(self):
                self.calls.append("COMMIT")

        class FakeDriver:
            def __init__(self):
                self.calls = []

            def transaction(self, *_args, **_kwargs):
                return FakeTransaction(self.calls)

        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)
        driver = FakeDriver()

        graph = PortfolioOntology(
            "typedb-pointer",
            entities=[OntologyEntity(
                entity_id="abox-active-pointer",
                label="Active ABox generation",
                kind="abox-active-pointer",
                properties={
                    "ontologyBox": "ABoxControl",
                    "snapshotId": "abox-material:test",
                },
            )],
        )
        repository.write_graph(
            driver,
            ((object, object, object, object, SimpleNamespace(WRITE="write")), None),
            graph,
            delete_boxes=["ABoxControl"],
        )

        self.assertEqual(1, driver.calls.count("COMMIT"))
        calls = "\n".join(driver.calls)
        self.assertIn('has ontology-box "ABoxControl"; delete $n;', calls)
        self.assertIn('insert $n0 isa ontology-entity', calls)
        self.assertIn('has ontology-box "ABoxControl"', calls)
        self.assertNotIn('has ontology-box "ABox"; delete', calls)

    def test_typedb_static_replacement_commits_delete_before_insert(self):
        class FakePromise:
            def resolve(self):
                return []

        class FakeTransaction:
            def __init__(self, calls):
                self.calls = calls

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def query(self, query):
                self.calls.append(query)
                return FakePromise()

            def commit(self):
                self.calls.append("COMMIT")

        class FakeDriver:
            def __init__(self):
                self.calls = []

            def transaction(self, *_args, **_kwargs):
                return FakeTransaction(self.calls)

        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)
        driver = FakeDriver()
        graph = PortfolioOntology("rules", entities=[OntologyEntity(
            "rule:test",
            "Test rule",
            "rule",
            {"ontologyBox": "RuleBox"},
        )])

        repository.write_graph(
            driver,
            ((object, object, object, object, SimpleNamespace(WRITE="write")), None),
            graph,
            delete_boxes=["RuleBox"],
        )

        self.assertEqual(2, driver.calls.count("COMMIT"))
        delete_index = next(index for index, item in enumerate(driver.calls) if "delete $n" in item)
        insert_index = next(index for index, item in enumerate(driver.calls) if "insert $n0" in item)
        self.assertIn("COMMIT", driver.calls[delete_index + 1:insert_index])

    def test_typedb_relation_matches_exact_persisted_endpoints(self):
        graph = PortfolioOntology(
            "rules",
            entities=[
                OntologyEntity("rule-registry:test", "Registry", "rule-registry", {"ontologyBox": "RuleBox"}),
                OntologyEntity("rule:test", "Rule", "rule", {"ontologyBox": "RuleBox"}),
            ],
            relations=[OntologyRelation(
                "rule-registry:test",
                "rule:test",
                "DEFINES_RULE",
                properties={"ontologyBox": "RuleBox"},
            )],
        )
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        _nodes, relations = repository.graph_persistence_rows(graph)
        query = repository.relation_insert_query(relations[0], "2026-07-21T00:00:00Z")

        self.assertIn("has ontology-storage-id", query)
        self.assertIn(relations[0]["sourceStorageId"], query)
        self.assertIn(relations[0]["targetStorageId"], query)
        self.assertNotIn('has ontology-id "rule:test"', query)

    def test_typedb_abox_write_graph_commits_query_chunks(self):
        class FakePromise:
            def resolve(self):
                return []

        class FakeTransaction:
            def __init__(self, calls):
                self.calls = calls

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def query(self, query):
                self.calls.append(("query", query))
                return FakePromise()

            def commit(self):
                self.calls.append(("commit", ""))

        class FakeDriver:
            def __init__(self):
                self.calls = []

            def transaction(self, *_args, **_kwargs):
                return FakeTransaction(self.calls)

        graph = PortfolioOntology("typedb-write-chunks")
        graph.entities.append(OntologyEntity(
            entity_id="stock:chunk",
            label="Chunk Stock",
            kind="stock",
            properties={"ontologyBox": "ABox", "tboxClass": "Stock"},
        ))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)
        driver = FakeDriver()

        with patch.object(repository, "graph_insert_queries", return_value=["insert one", "insert two", "insert three"]), \
                patch.object(repository, "abox_write_transaction_query_count", return_value=2):
            repository.write_graph(
                driver,
                ((object, object, object, object, SimpleNamespace(WRITE="write")), None),
                graph,
                delete_boxes=[],
            )

        self.assertEqual(2, len([item for item in driver.calls if item[0] == "commit"]))
        self.assertEqual(
            ["insert one", "insert two", "insert three"],
            [item[1] for item in driver.calls if item[0] == "query"],
        )

    def test_typedb_seed_graph_uses_bounded_write_transactions(self):
        class FakePromise:
            def resolve(self):
                return []

        class FakeTransaction:
            def __init__(self, calls):
                self.calls = calls

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def query(self, query):
                self.calls.append(("query", query))
                return FakePromise()

            def commit(self):
                self.calls.append(("commit", ""))

        class FakeDriver:
            def __init__(self):
                self.calls = []

            def transaction(self, *_args, **_kwargs):
                return FakeTransaction(self.calls)

        graph = PortfolioOntology("typedb-seed-chunks")
        graph.entities.append(OntologyEntity(
            entity_id="tbox:stock",
            label="Stock",
            kind="TBoxClass",
            properties={"ontologyBox": "TBox", "tboxClass": "TBoxClass"},
        ))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)
        driver = FakeDriver()

        with patch.object(repository, "graph_insert_queries", return_value=["seed one", "seed two", "seed three"]), \
                patch.object(repository, "graph_write_transaction_query_count", return_value=2):
            repository.write_graph(
                driver,
                ((object, object, object, object, SimpleNamespace(WRITE="write")), None),
                graph,
                delete_boxes=[],
            )

        self.assertEqual(2, len([item for item in driver.calls if item[0] == "commit"]))
        self.assertEqual(
            ["seed one", "seed two", "seed three"],
            [item[1] for item in driver.calls if item[0] == "query"],
        )

    def test_typedb_inferencebox_write_commits_query_chunks(self):
        class FakePromise:
            def resolve(self):
                return []

        class FakeTransaction:
            def __init__(self, calls):
                self.calls = calls

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def query(self, query):
                self.calls.append(("query", query))
                return FakePromise()

            def commit(self):
                self.calls.append(("commit", ""))

        class FakeDriver:
            def __init__(self):
                self.calls = []

            def transaction(self, *_args, **_kwargs):
                return FakeTransaction(self.calls)

        graph = PortfolioOntology("typedb-inference-write-chunks")
        graph.entities.append(OntologyEntity(
            entity_id="inference:chunk",
            label="Inference Chunk",
            kind="inference",
            properties={"ontologyBox": "InferenceBox"},
        ))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)
        driver = FakeDriver()

        with patch.object(repository, "open_driver", return_value=driver), \
                patch.object(repository, "close_driver"), \
                patch.object(repository, "ensure_database"), \
                patch.object(repository, "inferencebox_insert_queries", return_value=["insert one", "insert two", "insert three"]), \
                patch.object(repository, "inferencebox_write_transaction_query_count", return_value=2):
            result = repository.write_inferencebox_graph(graph)

        self.assertTrue(result["saved"])
        self.assertEqual(2, len([item for item in driver.calls if item[0] == "commit"]))
        self.assertEqual(
            ["insert one", "insert two", "insert three"],
            [item[1] for item in driver.calls if item[0] == "query"],
        )

    def test_typedb_inferencebox_write_transaction_query_count_is_bounded(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        self.assertEqual(8, repository.inferencebox_write_transaction_query_count({}))
        self.assertEqual(1, repository.inferencebox_write_transaction_query_count({
            "typedbInferenceBoxWriteTransactionQueryCount": "0",
        }))
        self.assertEqual(50, repository.inferencebox_write_transaction_query_count({
            "typedbInferenceBoxWriteTransactionQueryCount": "1000",
        }))

    def test_typedb_save_graph_returns_error_when_write_operation_times_out(self):
        graph = PortfolioOntology("typedb-timeout-test")
        repository = TypeDBOntologyGraphRepository(
            "127.0.0.1:1729",
            retry_count=0,
            write_operation_timeout_seconds=1,
        )

        def slow_open_driver(_imported):
            time.sleep(2)
            raise TypeDBOperationTimeout("expected timeout")

        with patch.object(repository, "driver_imports", return_value=((object, object, object, object, object), None)):
            with patch.object(repository, "open_driver", side_effect=slow_open_driver):
                result = repository.save_graph(graph)

        self.assertEqual("error", result["status"])
        self.assertFalse(result["saved"])
        self.assertIn("TypeDB graph save timed out", result["reason"])

    def test_typedb_inferencebox_save_returns_error_when_write_operation_times_out(self):
        graph = PortfolioOntology("typedb-inference-timeout-test")
        graph.entities.append(OntologyEntity(
            entity_id="inference:timeout",
            label="Inference Timeout",
            kind="inference",
            properties={"ontologyBox": "InferenceBox"},
        ))
        repository = TypeDBOntologyGraphRepository(
            "127.0.0.1:1729",
            retry_count=0,
            write_operation_timeout_seconds=1,
        )

        def slow_open_driver(_imported):
            time.sleep(2)
            raise TypeDBOperationTimeout("expected timeout")

        with patch.object(repository, "driver_imports", return_value=((object, object, object, object, object), None)):
            with patch.object(repository, "open_driver", side_effect=slow_open_driver):
                result = repository.write_inferencebox_graph(graph)

        self.assertEqual("error", result["status"])
        self.assertFalse(result["saved"])
        self.assertIn("TypeDB InferenceBox graph save timed out", result["reason"])

    def test_typedb_inferencebox_graph_dedupes_duplicate_native_trace_ids(self):
        graph = PortfolioOntology("typedb-inference-dedupe-test")
        trace = OntologyEntity(
            entity_id="inference-trace:AAPL:graph.watchlist.trend_transition.support.v1",
            label="AAPL trend support",
            kind="inference-trace",
            properties={"ontologyBox": "InferenceBox", "ruleId": "graph.watchlist.trend_transition.support.v1", "matchedConditions": ["a"]},
        )
        duplicate = OntologyEntity(
            entity_id=trace.entity_id,
            label="AAPL trend support",
            kind="inference-trace",
            properties={"ontologyBox": "InferenceBox", "ruleId": "graph.watchlist.trend_transition.support.v1", "matchedConditions": ["b"]},
        )
        graph.entities.extend([trace, duplicate])
        graph.relations.extend([
            OntologyRelation("stock:AAPL", trace.entity_id, "HAS_INFERENCE_TRACE", 0.7, properties={
                "ontologyBox": "InferenceBox",
                "ruleId": "graph.watchlist.trend_transition.support.v1",
            }),
            OntologyRelation("stock:AAPL", trace.entity_id, "HAS_INFERENCE_TRACE", 0.8, evidence_ids=["evidence:1"], properties={
                "ontologyBox": "InferenceBox",
                "ruleId": "graph.watchlist.trend_transition.support.v1",
            }),
        ])

        inference_graph = typedb_inferencebox_graph(graph, generation_id="gen:test", generation_at="2026-07-17T00:00:00Z")

        self.assertEqual(2, len(inference_graph.entities))
        reference = next(item for item in inference_graph.entities if item.entity_id == "stock:AAPL")
        self.assertEqual("inference-context-reference", reference.kind)
        self.assertTrue(reference.properties["referenceOnly"])
        self.assertEqual(1, len(inference_graph.relations))
        self.assertEqual(1.0, inference_graph.relations[0].weight)
        self.assertEqual(["evidence:1"], inference_graph.relations[0].evidence_ids)

    def test_typedb_repository_factory_inherits_ontology_reasoning_native_rule_setting(self):
        direct = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        factory_default = typedb_repository_from_settings({"ontologyTypeDbEnabled": "1", "typedbAddress": "127.0.0.1:1729"})
        factory_reasoning_disabled = typedb_repository_from_settings({
            "ontologyTypeDbEnabled": "1",
            "typedbAddress": "127.0.0.1:1729",
            "ontologyReasoningTypeDbNativeRuleExecutionEnabled": "0",
        })
        factory_enabled = typedb_repository_from_settings({
            "ontologyTypeDbEnabled": "1",
            "typedbAddress": "127.0.0.1:1729",
            "typedbNativeRuleExecutionEnabled": "1",
        })
        factory_disabled = typedb_repository_from_settings({
            "ontologyTypeDbEnabled": "1",
            "typedbAddress": "127.0.0.1:1729",
            "typedbNativeRuleExecutionEnabled": "0",
        })

        self.assertTrue(direct.native_rule_execution_enabled())
        self.assertTrue(factory_default.native_rule_execution_enabled())
        self.assertFalse(factory_reasoning_disabled.native_rule_execution_enabled())
        self.assertTrue(factory_enabled.native_rule_execution_enabled())
        self.assertFalse(factory_disabled.native_rule_execution_enabled())
        self.assertEqual(20.0, factory_default.query_timeout_seconds())
        self.assertEqual(20.0, factory_default.schema_operation_timeout_seconds())
        self.assertEqual(6.0, factory_default.native_rule_query_timeout_seconds())
        self.assertEqual(30.0, factory_default.native_rule_execution_budget_seconds())

    def test_typedb_symbol_filters_keep_numeric_stock_codes_as_strings(self):
        rule = next(item for item in default_graph_inference_rules() if item.rule_id == "graph.loss_guard.breakdown.v1")

        query = typedb_native_match_query(rule.to_dict(), ["000660"])["query"]

        self.assertIn('has ontology-symbol "000660"', query)
        self.assertNotIn("has ontology-symbol 660.0", query)

    def test_typedb_native_function_call_limits_candidates_to_active_manifest(self):
        rule = next(item for item in default_graph_inference_rules() if item.rule_id == "graph.loss_guard.breakdown.v1")

        query = typedb_native_function_call_query(rule.to_dict(), ["000660"])["query"]

        self.assertIn('has ontology-kind "worldview-manifest-active-pointer"', query)
        self.assertIn(
            'has ontology-box "ABox", has ontology-manifest-id $activeManifestId',
            query,
        )
        self.assertNotIn('has ontology-kind "abox-active-pointer"', query)
        self.assertIn('has ontology-symbol "000660"', query)

    def test_typedb_native_any_branches_use_distinct_value_variables(self):
        rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.loss_smart_money.add_buy_review.v1"
        )

        query = typedb_native_match_query(rule.to_dict(), ["000660"])["query"]
        target_value_variables = re.findall(r"\$(targetValue[A-Za-z0-9_]+)", query)
        counts = {
            variable: target_value_variables.count(variable)
            for variable in set(target_value_variables)
        }

        self.assertTrue(any(variable.startswith("targetValueany") for variable in counts))
        self.assertTrue(all(count <= 2 for count in counts.values()))

    def test_typedb_relation_rows_infer_symbol_from_stock_endpoint(self):
        graph = PortfolioOntology("symbol-row-test")
        graph.relations.append(OntologyRelation(
            "stock:000660",
            "risk:000660:loss-guard-breakdown",
            "HAS_INFERRED_RISK",
            weight=0.86,
            properties={"ontologyBox": "InferenceBox", "nativeTypeDbReasoned": True},
        ))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        row = repository.rows_for_relations(graph)[0]

        self.assertEqual("000660", row["symbol"])
        self.assertIn('"symbol": "000660"', row["propertiesJson"])

    def test_typedb_projection_persists_only_native_rule_one_hop_context(self):
        graph = PortfolioOntology("native-context")
        graph.entities.extend([
            OntologyEntity("stock:000660", "SK hynix", "stock", {"ontologyBox": "ABox"}),
            OntologyEntity("portfolio:default", "Portfolio", "portfolio", {"ontologyBox": "ABox"}),
            OntologyEntity("risk-budget:default", "Risk budget", "risk-budget", {"ontologyBox": "ABox"}),
            OntologyEntity("key-level:000660:ma5", "5-day average", "key-level", {"ontologyBox": "ABox", "levelType": "ma5"}),
            OntologyEntity("runtime:irrelevant", "Runtime setting", "runtime-setting", {"ontologyBox": "ABox"}),
            OntologyEntity("orphan:irrelevant", "Orphan", "technical-metric", {"ontologyBox": "ABox"}),
        ])
        graph.relations.extend([
            OntologyRelation("stock:000660", "risk-budget:default", "HAS_RISK_BUDGET", properties={"ontologyBox": "ABox"}),
            OntologyRelation("stock:000660", "key-level:000660:ma5", "BREAKS_LEVEL", properties={"ontologyBox": "ABox"}),
            OntologyRelation("stock:000660", "runtime:irrelevant", "HAS_RUNTIME_SETTING", properties={"ontologyBox": "ABox"}),
            OntologyRelation("orphan:irrelevant", "runtime:irrelevant", "HAS_RUNTIME_SETTING", properties={"ontologyBox": "ABox"}),
        ])
        graph.evidence.extend([
            OntologyEvidence("evidence:stock", "stock:000660", "news", "test", "stock evidence", {"ontologyBox": "ABox"}),
            OntologyEvidence("evidence:orphan", "orphan:irrelevant", "news", "test", "orphan evidence", {"ontologyBox": "ABox"}),
        ])

        persisted = PortfolioOntologyProjectionRecorder(None).graph_for_graph_store_persistence(
            graph,
            {"inputRelationTypes": ["HAS_RISK_BUDGET", "BREAKS_LEVEL"]},
        )

        self.assertEqual(
            {"stock:000660", "portfolio:default", "risk-budget:default", "key-level:000660:ma5"},
            {item.entity_id for item in persisted.entities},
        )
        self.assertEqual(
            ["HAS_RISK_BUDGET", "BREAKS_LEVEL"],
            [item.relation_type for item in persisted.relations],
        )
        self.assertEqual(["evidence:stock"], [item.evidence_id for item in persisted.evidence])
        self.assertEqual(
            "typedb-rule-input-and-semantic-coverage-relations",
            persisted.worldview["runtimeProjectionScope"],
        )

    def test_typedb_projection_keeps_semantic_coverage_relations_outside_rule_inputs(self):
        graph = PortfolioOntology("semantic-context")
        graph.entities.extend([
            OntologyEntity("stock:000660", "SK hynix", "stock", {"ontologyBox": "ABox"}),
            OntologyEntity("price:000660", "Current price", "price", {"ontologyBox": "ABox"}),
            OntologyEntity("liquidity:000660", "Liquidity", "liquidity-profile", {"ontologyBox": "ABox"}),
            OntologyEntity("runtime:irrelevant", "Runtime setting", "runtime-setting", {"ontologyBox": "ABox"}),
        ])
        graph.relations.extend([
            OntologyRelation("stock:000660", "price:000660", "HAS_PRICE", properties={"ontologyBox": "ABox"}),
            OntologyRelation("stock:000660", "liquidity:000660", "HAS_LIQUIDITY_PROFILE", properties={"ontologyBox": "ABox"}),
            OntologyRelation("stock:000660", "runtime:irrelevant", "HAS_RUNTIME_SETTING", properties={"ontologyBox": "ABox"}),
        ])

        persisted = PortfolioOntologyProjectionRecorder(None).graph_for_graph_store_persistence(
            graph,
            {"inputRelationTypes": ["HAS_RISK_BUDGET"]},
        )

        self.assertEqual(
            ["HAS_PRICE", "HAS_LIQUIDITY_PROFILE"],
            [item.relation_type for item in persisted.relations],
        )
        self.assertEqual(1, persisted.worldview["runtimeProjectionRuleInputRelationTypeCount"])
        self.assertGreater(persisted.worldview["runtimeProjectionSemanticRelationTypeCount"], 1)

    def test_typedb_projection_keeps_temporal_observation_structure(self):
        graph = PortfolioOntology("temporal-structure")
        graph.entities.extend([
            OntologyEntity("stock:000660", "SK hynix", "stock", {"ontologyBox": "ABox"}),
            OntologyEntity("window:000660:5D", "5-day window", "temporal-window", {"ontologyBox": "ABox"}),
            OntologyEntity("observation:000660:first", "First observation", "temporal-observation", {"ontologyBox": "ABox"}),
            OntologyEntity("observation:000660:latest", "Latest observation", "temporal-observation", {"ontologyBox": "ABox"}),
        ])
        graph.relations.extend([
            OntologyRelation("stock:000660", "window:000660:5D", "HAS_TEMPORAL_WINDOW", properties={"ontologyBox": "ABox"}),
            OntologyRelation("window:000660:5D", "observation:000660:first", "WINDOW_CONTAINS_OBSERVATION", properties={"ontologyBox": "ABox"}),
            OntologyRelation("window:000660:5D", "observation:000660:latest", "WINDOW_CONTAINS_OBSERVATION", properties={"ontologyBox": "ABox"}),
            OntologyRelation("observation:000660:first", "observation:000660:latest", "PRECEDES", properties={"ontologyBox": "ABox"}),
        ])

        persisted = PortfolioOntologyProjectionRecorder(None).graph_for_graph_store_persistence(
            graph,
            {"inputRelationTypes": ["HAS_TEMPORAL_WINDOW"]},
        )

        self.assertEqual(4, len(persisted.entities))
        self.assertEqual(
            ["HAS_TEMPORAL_WINDOW", "WINDOW_CONTAINS_OBSERVATION", "WINDOW_CONTAINS_OBSERVATION", "PRECEDES"],
            [item.relation_type for item in persisted.relations],
        )

    def test_typedb_rule_catalog_migration_removes_shadow_and_fills_known_policy(self):
        bootstrap = rulebox_rules_to_payload(default_graph_inference_rules())
        stored = [dict(item) for item in bootstrap[:2]]
        stored[0]["derivations"] = [dict(item) for item in stored[0]["derivations"]]
        stored[0]["derivations"][0]["decision_stage"] = ""
        stored.append({
            "rule_id": "shadow.market_psychology.state.v1",
            "derivations": [{"decision_stage": ""}],
        })

        migration = migrate_typedb_rule_catalog(stored, bootstrap)

        self.assertTrue(migration["changed"])
        self.assertEqual(["shadow.market_psychology.state.v1"], migration["removedRuleIds"])
        self.assertEqual("LOSS_REDUCE", migration["rules"][0]["derivations"][0]["decision_stage"])

    def test_typedb_insert_queries_project_same_ontology_graph_shape(self):
        graph = PortfolioOntology("portfolio:test")
        graph.entities.append(OntologyEntity("stock:005930", "삼성전자", "stock", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "tboxClass": "Stock",
        }))
        graph.entities.append(OntologyEntity("signal:005930:risk", "리스크 신호", "risk-signal", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "tboxClass": "RiskSignal",
        }))
        graph.relations.append(OntologyRelation("stock:005930", "signal:005930:risk", "HAS_RISK_SIGNAL", 0.84, properties={
            "ontologyBox": "ABox",
            "ruleId": "risk.test",
        }))
        graph.evidence.append(OntologyEvidence(
            "evidence:005930:risk",
            "stock:005930",
            "market-observation",
            "test",
            "위험 관찰",
            {"ontologyBox": "ABox"},
            0.8,
        ))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        queries = repository.insert_queries(graph)

        self.assertTrue(any("insert $n isa ontology-entity" in query for query in queries))
        self.assertTrue(any("insert $r isa ontology-assertion, links (source: $source, target: $target)" in query for query in queries))
        self.assertTrue(any('has ontology-relation-type "HAS_RISK_SIGNAL"' in query for query in queries))
        self.assertTrue(any('has ontology-relation-type "HAS_EVIDENCE"' in query for query in queries))
        self.assertEqual(relation_row_id(repository.rows_for_relations(graph)[0]), relation_row_id(repository.rows_for_relations(graph)[0]))

    def test_typedb_graph_insert_queries_batch_abox_rows(self):
        graph = PortfolioOntology("portfolio:batched-abox")
        for index in range(31):
            graph.entities.append(OntologyEntity(
                "stock:" + str(index),
                "Stock " + str(index),
                "stock",
                {"ontologyBox": "ABox", "symbol": str(index), "tboxClass": "Stock"},
            ))
        for index in range(30):
            graph.relations.append(OntologyRelation(
                "stock:" + str(index),
                "stock:" + str(index + 1),
                "RELATED_TO",
                properties={"ontologyBox": "ABox"},
            ))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        with patch("digital_twin.infrastructure.typedb_ontology.runtime_settings", return_value={
            "typedbABoxNodeBatchSize": "10",
            "typedbABoxRelationBatchSize": "5",
        }):
            queries = repository.graph_insert_queries(graph)

        self.assertEqual(10, len(queries))
        self.assertTrue(queries[0].startswith("insert $n0 isa ontology-entity"))
        relation_queries = [query for query in queries if query.startswith("match $source0 isa ontology-node")]
        self.assertEqual(6, len(relation_queries))
        self.assertTrue(all(query.count(" isa ontology-assertion") == 5 for query in relation_queries))

    def test_typedb_graph_insert_queries_split_large_payloads_by_byte_size(self):
        graph = PortfolioOntology("portfolio:large-payload-batches")
        for index in range(3):
            graph.entities.append(OntologyEntity(
                "stock:large:" + str(index),
                "Large Stock " + str(index),
                "stock",
                {
                    "ontologyBox": "ABox",
                    "symbol": "LARGE" + str(index),
                    "tboxClass": "Stock",
                    "sourcePayload": "x" * 1600,
                },
            ))
        graph.relations.extend([
            OntologyRelation(
                "stock:large:0",
                "stock:large:1",
                "RELATED_TO",
                properties={"ontologyBox": "ABox", "sourcePayload": "y" * 1600},
            ),
            OntologyRelation(
                "stock:large:1",
                "stock:large:2",
                "RELATED_TO",
                properties={"ontologyBox": "ABox", "sourcePayload": "z" * 1600},
            ),
        ])
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        with patch("digital_twin.infrastructure.typedb_ontology.runtime_settings", return_value={
            "typedbABoxNodeBatchSize": "10",
            "typedbABoxRelationBatchSize": "10",
            "typedbWriteMaxQueryBytes": "4096",
        }):
            queries = repository.graph_insert_queries(graph)

        self.assertGreater(len(queries), 2)
        self.assertTrue(all(repository.query_byte_size(query) <= 4096 for query in queries))
        self.assertEqual(3, sum(query.count(" isa ontology-entity") for query in queries if query.startswith("insert ")))
        self.assertEqual(2, sum(query.count(" isa ontology-assertion") for query in queries if query.startswith("match ")))

    def test_typedb_write_query_max_bytes_is_bounded(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        self.assertEqual(192000, repository.write_query_max_bytes({}))
        self.assertEqual(4096, repository.write_query_max_bytes({
            "typedbWriteMaxQueryBytes": "1",
        }))
        self.assertEqual(256000, repository.write_query_max_bytes({
            "typedbWriteMaxQueryBytes": "999999",
        }))

    def test_typedb_insert_queries_promote_reasoning_fields_to_attributes(self):
        graph = PortfolioOntology("portfolio:typed-fields")
        graph.entities.append(OntologyEntity("stock:005930", "삼성전자", "stock", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "source": "holding",
            "profitLossRate": -12.5,
            "currentPrice": 68000,
            "averagePrice": 72000,
            "marketValue": 680000,
            "quantity": 10,
            "sellableQuantity": 10,
            "positionAccountWeight": 28.4,
            "ma5Distance": 1.2,
            "ma20Distance": -8.5,
            "ma60Distance": 2.1,
            "volumeRatio": 1.6,
            "tradeStrength": 118.4,
            "bidAskImbalance": -22.7,
            "foreignNetVolume": 1400,
            "institutionNetVolume": 2500,
            "individualNetVolume": -3900,
            "smartMoneyNetVolume": 3900,
            "investmentStrategyProfile": "aggressive",
            "tboxClass": "Stock",
        }))
        graph.entities.append(OntologyEntity("level:005930:ma20", "20일선", "key-level", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "levelType": "ma20",
            "field": "movingAverage",
            "value": 70000,
        }))
        graph.entities.append(OntologyEntity("profile:005930", "종목 타입", "instrument-profile", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "allowAddOnStrength": True,
            "trimOnTrendBreak": True,
            "avoidAveragingDown": True,
        }))
        graph.entities.append(OntologyEntity("analysis:005930", "기사 AI", "article-ai-analysis", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "confidence": 0.71,
            "impactPolarity": "risk",
            "needsReview": True,
            "readScope": "title+rss-summary",
        }))
        graph.entities.append(OntologyEntity("valuation:005930", "밸류에이션", "valuation-assumption", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "peRatio": 47.5,
            "beta": 1.8,
        }))
        graph.entities.append(OntologyEntity("security-line:000660:SKHY", "SK hynix ADR", "security-line", {
            "ontologyBox": "ABox",
            "symbol": "000660",
            "securityLineRole": "adr",
            "adrSymbol": "SKHY",
            "adrRatio": 0.1,
            "leverageFactor": 0,
            "listingDate": "2026-07-13",
            "conversionStartDate": "2026-07-29",
        }))
        graph.entities.append(OntologyEntity("temporal-window:005930:5D", "삼성전자 5D 기간 흐름", "temporal-window", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "windowKey": "5D",
            "lookbackDays": 5,
            "sampleCount": 4,
            "requiredSampleCount": 4,
            "coverageRatio": 1.0,
            "priceChangePct": -8.4,
            "peakReturnPct": 1.7,
            "drawdownFromPeakPct": -9.9,
            "recentPriceChangePct": -3.2,
            "priceVelocityChangePct": -1.4,
            "consecutiveDeclineCount": 3,
            "validObservationRatio": 1.0,
            "staleObservationCount": 0,
            "profitLossRateChangePct": -5.2,
            "ma20DistanceChange": -7.5,
            "smartMoneyNetChange": -12000,
            "smartMoneyObservationCount": 4,
            "smartMoneyDistinctObservationCount": 3,
            "riskEventCount": 2,
            "latestObservationQuality": "fresh",
            "reviewLevel": "normal",
            "dataState": "sufficient",
            "evidenceRole": "context",
            "hasSufficientHistory": True,
        }))
        graph.relations.append(OntologyRelation("stock:005930", "level:005930:ma20", "BREAKS_LEVEL", 0.8, properties={
            "ontologyBox": "ABox",
            "polarity": "risk",
            "evidenceRole": "risk",
            "reviewLevel": "act",
            "dataState": "sufficient",
            "field": "ma20Distance",
        }))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        queries = repository.insert_queries(graph)

        self.assertTrue(any('has ontology-source-value "holding"' in query for query in queries))
        self.assertTrue(any("has ontology-profit-loss-rate -12.5" in query for query in queries))
        self.assertTrue(any('has ontology-level-type "ma20"' in query for query in queries))
        self.assertTrue(any('has ontology-evidence-role "risk"' in query for query in queries))
        self.assertTrue(any('has ontology-review-level "act"' in query for query in queries))
        self.assertTrue(any('has ontology-data-state "sufficient"' in query for query in queries))
        self.assertTrue(any('has ontology-allow-add-on-strength "true"' in query for query in queries))
        self.assertTrue(any('has ontology-avoid-averaging-down "true"' in query for query in queries))
        self.assertTrue(any('has ontology-impact-polarity "risk"' in query for query in queries))
        self.assertTrue(any('has ontology-needs-review "true"' in query for query in queries))
        self.assertTrue(any('has ontology-read-scope "title+rss-summary"' in query for query in queries))
        self.assertTrue(any("has ontology-pe-ratio 47.5" in query for query in queries))
        self.assertTrue(any("has ontology-beta 1.8" in query for query in queries))
        self.assertTrue(any("has ontology-current-price 68000.0" in query for query in queries))
        self.assertTrue(any('has ontology-security-line-role "adr"' in query for query in queries))
        self.assertTrue(any('has ontology-adr-symbol "SKHY"' in query for query in queries))
        self.assertTrue(any("has ontology-adr-ratio 0.1" in query for query in queries))
        self.assertTrue(any('has ontology-listing-date "2026-07-13"' in query for query in queries))
        self.assertTrue(any("has ontology-average-price 72000.0" in query for query in queries))
        self.assertTrue(any("has ontology-position-account-weight-pct 28.4" in query for query in queries))
        self.assertTrue(any("has ontology-ma5-distance 1.2" in query for query in queries))
        self.assertTrue(any("has ontology-ma20-distance -8.5" in query for query in queries))
        self.assertTrue(any("has ontology-volume-ratio 1.6" in query for query in queries))
        self.assertTrue(any("has ontology-trade-strength 118.4" in query for query in queries))
        self.assertTrue(any("has ontology-foreign-net-volume 1400.0" in query for query in queries))
        self.assertTrue(any("has ontology-smart-money-net-volume 3900.0" in query for query in queries))
        self.assertTrue(any('has ontology-investment-strategy-profile "aggressive"' in query for query in queries))
        self.assertTrue(any('has ontology-window-key "5D"' in query for query in queries))
        self.assertTrue(any("has ontology-price-change-pct -8.4" in query for query in queries))
        self.assertTrue(any("has ontology-drawdown-from-peak-pct -9.9" in query for query in queries))
        self.assertTrue(any("has ontology-recent-price-change-pct -3.2" in query for query in queries))
        self.assertTrue(any("has ontology-consecutive-decline-count 3.0" in query for query in queries))
        self.assertTrue(any("has ontology-valid-observation-ratio 1.0" in query for query in queries))
        self.assertTrue(any("has ontology-smart-money-distinct-observation-count 3.0" in query for query in queries))
        self.assertTrue(any("has ontology-risk-event-count 2.0" in query for query in queries))
        self.assertTrue(any('has ontology-latest-observation-quality "fresh"' in query for query in queries))
        self.assertFalse(any("ontology-temporal-risk-score" in query for query in queries))

    def test_typedb_read_query_metrics_record_row_count_and_hash(self):
        class FakeConcept:
            def __init__(self, value):
                self._value = value

            def get_value(self):
                return self._value

        class FakeRow:
            def __init__(self, values):
                self.values = values

            def get(self, name):
                return FakeConcept(self.values.get(name))

        class FakeQuery:
            def __init__(self, rows):
                self.rows = rows

            def resolve(self):
                return self.rows

        class FakeTx:
            def __init__(self):
                self.last_query = ""

            def query(self, query):
                self.last_query = query
                return FakeQuery([FakeRow({"id": "stock:005930"})])

        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        repository.reset_query_metrics()

        rows = repository.read_rows_in_transaction(FakeTx(), "match $n isa ontology-node; limit 1;", ["id"], label="unit-test")
        metrics = repository.query_metrics_snapshot()

        self.assertEqual([{"id": "stock:005930"}], rows)
        self.assertEqual(1, metrics["queryCount"])
        self.assertEqual(1, metrics["slowQueries"][0]["rowCount"])
        self.assertEqual("unit-test", metrics["slowQueries"][0]["label"])
        self.assertTrue(metrics["slowQueries"][0]["queryHash"])
        self.assertIn("match $n isa ontology-node", metrics["slowQueries"][0]["queryPreview"])

    def test_typedb_read_helpers_push_limit_and_existence_into_typeql(self):
        class CapturingRepository(TypeDBOntologyGraphRepository):
            def __init__(self):
                super().__init__("127.0.0.1:1729")
                self.queries = []

            def read_rows(self, query, columns):
                self.queries.append(str(query))
                return []

        repository = CapturingRepository()

        self.assertFalse(repository.has_box_rows("ABox"))
        repository.read_entity_rows(["ABox"], limit=1)
        repository.read_relation_rows(["InferenceBox"], limit=2)

        self.assertIn("has ontology-box \"ABoxControl\"", repository.queries[0])
        self.assertIn("has ontology-box \"ABox\", has ontology-scope-id $boxProbeScopedScopeId", repository.queries[0])
        self.assertIn("has ontology-kind \"abox-active-pointer\"", repository.queries[0])
        self.assertIn("limit 1;", repository.queries[1])
        self.assertIn("limit 2;", repository.queries[2])

    def test_typedb_native_rule_matching_skips_condition_detail_queries_by_default(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        rule = default_graph_inference_rules()[0]
        matches = []

        with patch.object(repository, "read_rows", side_effect=AssertionError("condition detail query should not run")), patch.object(repository, "condition_detail_queries_enabled", return_value=False):
            repository.merge_native_match_rows(
                rule,
                {"evidenceColumns": [], "conditionEvidenceColumns": {}},
                [{"sourceId": "stock:005930", "sourceLabel": "삼성전자"}],
                {},
                matches,
            )

        self.assertEqual(1, len(matches))
        self.assertTrue(matches[0]["matchedConditions"])

    def test_typedb_native_match_graph_reads_only_matched_subject_entities(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        requested = []

        def fake_rows(ids, boxes=None):
            requested.extend(list(ids))
            return [
                {
                    "id": "stock:005930",
                    "label": "삼성전자",
                    "kind": "stock",
                    "ontologyBox": "ABox",
                    "symbol": "005930",
                    "propertiesJson": json.dumps({"ontologyBox": "ABox", "symbol": "005930", "source": "holding"}),
                }
            ]

        with patch.object(repository, "read_entity_rows_by_ids", side_effect=fake_rows), patch.object(
            repository,
            "read_relation_rows_by_source_ids",
            return_value=[],
        ):
            graph = repository.load_graph_for_native_matches({
                "matches": [
                    {"sourceId": "stock:005930", "sourceLabel": "삼성전자"},
                    {"sourceId": "stock:MISSING", "sourceLabel": "Fallback"},
                ],
            })

        self.assertEqual(["stock:005930", "stock:MISSING"], requested)
        self.assertEqual(2, len(graph.entities))
        self.assertTrue(any(item.entity_id == "stock:MISSING" and (item.properties or {}).get("queryFallback") for item in graph.entities))

    def test_typedb_rulebox_snapshot_uses_short_cache(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        repository._rulebox_snapshot_cache_at = 9999999999.0
        repository._rulebox_snapshot_cache_result = {"status": "ok", "rules": [], "ruleCount": 0}

        with patch.object(repository, "read_entity_rows", side_effect=AssertionError("cache should avoid TypeDB reads")):
            snapshot = repository.rulebox_snapshot()

        self.assertTrue(snapshot["cached"])
        self.assertTrue(snapshot["ruleBoxSnapshotCached"])

    def test_typedb_inferencebox_scoped_helpers_filter_generation_and_symbol(self):
        class CapturingRepository(TypeDBOntologyGraphRepository):
            def __init__(self):
                super().__init__("127.0.0.1:1729")
                self.queries = []

            def read_rows(self, query, columns):
                self.queries.append(str(query))
                return []

        repository = CapturingRepository()

        repository.read_inference_generation_records()
        repository.read_inferencebox_entity_rows("generation:active", ["AAPL"], 3)
        repository.read_inferencebox_relation_rows("generation:active", ["AAPL"], 4)

        self.assertIn('has ontology-kind "inference-generation"', repository.queries[0])
        self.assertIn('has ontology-box "InferenceBox"', repository.queries[2])
        self.assertIn("has ontology-snapshot-id $snapshotId", repository.queries[2])
        self.assertIn('has ontology-box "InferenceBox"', repository.queries[3])
        self.assertIn("has ontology-snapshot-id $snapshotId", repository.queries[3])
        self.assertIn('has ontology-snapshot-id "generation:active"', repository.queries[4])
        self.assertIn('has ontology-symbol "AAPL"', repository.queries[4])
        self.assertIn("limit 3;", repository.queries[4])
        self.assertIn('has ontology-snapshot-id "generation:active"', repository.queries[5])
        self.assertIn('has ontology-symbol "AAPL"', repository.queries[5])
        self.assertIn("limit 4;", repository.queries[5])

    def test_typedb_active_generation_ignores_unpublished_partial_generation(self):
        class PublishedOnlyRepository(TypeDBOntologyGraphRepository):
            def __init__(self):
                super().__init__("127.0.0.1:1729")

            def read_rows(self, query, columns, label="typedb.read"):
                if 'has ontology-kind "inference-generation"' in query:
                    return [{"snapshotId": "generation:complete", "updatedAt": "2026-07-20T00:00:00Z"}]
                if "$n isa ontology-node" in query:
                    return [
                        {"snapshotId": "generation:complete", "updatedAt": "2026-07-20T00:00:00Z"},
                        {"snapshotId": "generation:partial", "updatedAt": "2026-07-20T00:01:00Z"},
                    ]
                return [
                    {"snapshotId": "generation:complete", "updatedAt": "2026-07-20T00:00:00Z"},
                    {"snapshotId": "generation:partial", "updatedAt": "2026-07-20T00:01:00Z"},
                ]

        records = PublishedOnlyRepository().read_inference_generation_records()

        self.assertEqual(1, len(records))
        self.assertEqual("generation:complete", records[0]["generationId"])
        self.assertEqual("active", records[0]["publicationStatus"])

    def test_typedb_candidate_generation_requires_active_abox_alignment(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        graph = PortfolioOntology("candidate-validation")
        graph.worldview = {
            "inferenceGenerationId": "generation:candidate",
            "sourceAboxSnapshotId": "abox:active",
        }
        graph.entities.append(OntologyEntity(
            "trace:1",
            "trace",
            "inference-trace",
            {"ontologyBox": "InferenceBox"},
        ))
        with patch.object(repository, "read_inferencebox_entity_rows", return_value=[{"kind": "inference-trace"}]), patch.object(repository, "read_inferencebox_relation_rows", return_value=[{"type": "HAS_INFERENCE_TRACE"}]), patch.object(repository, "active_abox_snapshot_id", return_value="abox:active"):
            valid = repository.validate_inference_generation_candidate(graph, "generation:candidate", 1, 1)
        with patch.object(repository, "read_inferencebox_entity_rows", return_value=[{"kind": "inference-trace"}]), patch.object(repository, "read_inferencebox_relation_rows", return_value=[{"type": "HAS_INFERENCE_TRACE"}]), patch.object(repository, "active_abox_snapshot_id", return_value="abox:new"):
            invalid = repository.validate_inference_generation_candidate(graph, "generation:candidate", 1, 1)

        self.assertTrue(valid["valid"])
        self.assertTrue(valid["generationAligned"])
        self.assertFalse(invalid["valid"])
        self.assertIn("candidate-source-abox-not-active", invalid["reason"])

    def test_typedb_inferencebox_insert_queries_batch_rows(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        node_rows = [
            {
                "id": "inference:node:" + str(index),
                "label": "추론 " + str(index),
                "kind": "inference-result",
                "nodeType": "ontology-entity",
                "ontologyBox": "InferenceBox",
            }
            for index in range(3)
        ]
        relation_rows = [
            {
                "source": "inference:node:0",
                "target": "inference:node:1",
                "type": "HAS_INFERENCE_TRACE",
                "ontologyBox": "InferenceBox",
            },
            {
                "source": "inference:node:1",
                "target": "inference:node:2",
                "type": "HAS_INFERRED_RISK",
                "ontologyBox": "InferenceBox",
            },
        ]

        queries = repository.inferencebox_insert_queries(node_rows, relation_rows, "2026-07-16T00:00:00Z")

        self.assertEqual(2, len(queries))
        self.assertIn("$n0 isa ontology-entity", queries[0])
        self.assertIn("$n1 isa ontology-entity", queries[0])
        self.assertIn("$n2 isa ontology-entity", queries[0])
        self.assertIn("match $source0 isa ontology-node", queries[1])
        self.assertIn("$r0 isa ontology-assertion", queries[1])
        self.assertIn("$r1 isa ontology-assertion", queries[1])

    def test_typedb_inferencebox_relation_batch_size_caps_expensive_typeql_match_groups(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        self.assertEqual(4, repository.inferencebox_relation_batch_size({}))
        self.assertEqual(4, repository.inferencebox_relation_batch_size({"typedbInferenceBoxRelationBatchSize": "25"}))
        self.assertEqual(1, repository.inferencebox_relation_batch_size({"typedbInferenceBoxRelationBatchSize": "0"}))

    def test_typedb_inferencebox_snapshot_can_reuse_materialized_graph_without_read(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        graph = PortfolioOntology(
            "portfolio:inference",
            worldview={
                "inferenceGenerationId": "generation:test",
                "inferenceGenerationAt": "2026-07-16T00:00:00Z",
            },
        )
        graph.entities.append(OntologyEntity("inference:stock:AAPL", "Apple risk", "inference-result", {
            "ontologyBox": "InferenceBox",
            "symbol": "AAPL",
            "nativeTypeDbReasoned": True,
            "nativeRuleId": "typedb.native.test",
        }))
        graph.relations.append(OntologyRelation("stock:AAPL", "inference:stock:AAPL", "HAS_INFERRED_RISK", 1.0, properties={
            "ontologyBox": "InferenceBox",
            "symbol": "AAPL",
            "nativeTypeDbReasoned": True,
            "nativeRuleId": "typedb.native.test",
        }))

        snapshot = repository.inferencebox_snapshot_from_graph(graph, ["AAPL"], 80)

        self.assertEqual("ok", snapshot["status"])
        self.assertEqual("typedb-native-rule-result", snapshot["querySource"])
        self.assertEqual("skipped", snapshot["typedbReadStatus"])
        self.assertEqual(1, snapshot["relationCount"])
        self.assertTrue(snapshot["nativeTypeDbReasoningUsed"])

    def test_projection_recorder_reuses_rulebox_inferencebox_payload(self):
        class FakeRepository:
            store_key = "typedb"

            def __init__(self):
                self.snapshot_read_called = False

            def save_graph(self, _graph):
                return {"saved": True, "status": "ok", "graphStore": "typedb"}

            def rulebox_snapshot(self):
                rules = rulebox_rules_to_payload(default_graph_inference_rules())
                return {
                    "configured": True,
                    "status": "ok",
                    "rules": rules,
                    "ruleCount": len(rules),
                }

            def run_rulebox(self, _payload):
                return {
                    "status": "ok",
                    "graphStore": "typedb",
                    "inferenceBox": {
                        "status": "ok",
                        "relationCount": 1,
                        "typedbReadStatus": "skipped",
                    },
                }

            def inferencebox_snapshot(self, *_args, **_kwargs):
                self.snapshot_read_called = True
                raise AssertionError("inferencebox_snapshot should not be called when run_rulebox returned inferenceBox")

        repository = FakeRepository()
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            PortfolioSummary(total=1000, invested=1000, cash=0, markets=[], sectors=[], concentration=0),
            positions=[Position("AAPL", "Apple", market="US", currency="USD", quantity=1, current_price=100, market_value=100, market_value_krw=140000)],
        )

        result = PortfolioOntologyProjectionRecorder(repository).record_snapshot(snapshot)

        self.assertFalse(repository.snapshot_read_called)
        self.assertEqual("ok", result["inferenceBox"]["status"])
        self.assertEqual("skipped", result["inferenceBox"]["typedbReadStatus"])

    def test_projection_recorder_rolls_back_new_abox_when_native_inference_is_not_aligned(self):
        class FakeRepository:
            store_key = "typedb"

            def __init__(self):
                self.restored_snapshot_ids = []
                self.finalized_snapshot_ids = []

            def active_abox_metadata(self):
                return {
                    "status": "ok",
                    "aboxSnapshotId": "abox:previous",
                    "materialFingerprint": "previous-material",
                }

            def save_graph(self, _graph):
                return {
                    "saved": True,
                    "status": "ok",
                    "graphStore": "typedb",
                    "aboxPersistenceVerification": {
                        "activation": {
                            "status": "activated",
                            "snapshotId": "abox:new",
                            "previousSnapshotId": "abox:previous",
                        },
                    },
                }

            def rulebox_snapshot(self):
                rules = rulebox_rules_to_payload(default_graph_inference_rules())
                return {
                    "configured": True,
                    "status": "ok",
                    "rules": rules,
                    "ruleCount": len(rules),
                }

            def run_rulebox(self, _payload):
                return {"status": "error", "reason": "TypeDB native rule query timed out"}

            def inferencebox_snapshot(self, *_args, **_kwargs):
                return {
                    "status": "stale-generation",
                    "nativeTypeDbReasoningUsed": True,
                    "sourceAboxSnapshotId": "abox:previous",
                    "generationAligned": False,
                }

            def activate_abox_generation(self, snapshot_id):
                self.restored_snapshot_ids.append(snapshot_id)
                return {
                    "status": "ok",
                    "activeAbox": {"status": "ok", "aboxSnapshotId": snapshot_id},
                }

            def finalize_abox_generation(self, active_snapshot_id, previous_snapshot_id):
                self.finalized_snapshot_ids.append((active_snapshot_id, previous_snapshot_id))
                return {"status": "ok"}

        repository = FakeRepository()
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            PortfolioSummary(total=1000, invested=1000, cash=0, markets=[], sectors=[], concentration=0),
            positions=[Position("AAPL", "Apple", market="US", currency="USD", quantity=1, current_price=100, market_value=100, market_value_krw=140000)],
        )

        result = PortfolioOntologyProjectionRecorder(repository).record_snapshot(snapshot)

        self.assertFalse(result["saved"])
        self.assertEqual("inference-failed-rolled-back", result["status"])
        self.assertTrue(result["preservedActiveGeneration"])
        self.assertEqual(["abox:previous"], repository.restored_snapshot_ids)
        self.assertEqual([], repository.finalized_snapshot_ids)

    def test_projection_recorder_finalizes_abox_after_aligned_native_inference(self):
        class FakeRepository:
            store_key = "typedb"

            def __init__(self):
                self.finalized_snapshot_ids = []

            def active_abox_metadata(self):
                return {
                    "status": "ok",
                    "aboxSnapshotId": "abox:previous",
                    "materialFingerprint": "previous-material",
                }

            def save_graph(self, _graph):
                return {
                    "saved": True,
                    "status": "ok",
                    "graphStore": "typedb",
                    "aboxPersistenceVerification": {
                        "activation": {
                            "status": "activated",
                            "snapshotId": "abox:new",
                            "previousSnapshotId": "abox:previous",
                        },
                    },
                }

            def rulebox_snapshot(self):
                rules = rulebox_rules_to_payload(default_graph_inference_rules())
                return {
                    "configured": True,
                    "status": "ok",
                    "rules": rules,
                    "ruleCount": len(rules),
                }

            def run_rulebox(self, _payload):
                return {
                    "status": "ok",
                    "inferenceBox": {
                        "status": "ok",
                        "nativeTypeDbReasoningUsed": True,
                        "generationAligned": True,
                        "sourceAboxSnapshotId": "abox:new",
                        "targetSymbols": ["AAPL"],
                    },
                }

            def finalize_abox_generation(self, active_snapshot_id, previous_snapshot_id):
                self.finalized_snapshot_ids.append((active_snapshot_id, previous_snapshot_id))
                return {"status": "ok"}

        repository = FakeRepository()
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            PortfolioSummary(total=1000, invested=1000, cash=0, markets=[], sectors=[], concentration=0),
            positions=[Position("AAPL", "Apple", market="US", currency="USD", quantity=1, current_price=100, market_value=100, market_value_krw=140000)],
        )

        result = PortfolioOntologyProjectionRecorder(repository).record_snapshot(snapshot)

        self.assertTrue(result["saved"])
        self.assertEqual("ok", result["status"])
        self.assertEqual([("abox:new", "abox:previous")], repository.finalized_snapshot_ids)

    def test_projection_recorder_rejects_demo_snapshot_and_defers_regular_typedb_writer(self):
        class FakeRepository:
            store_key = "typedb"

            def __init__(self):
                self.save_calls = 0

            def save_graph(self, _graph):
                self.save_calls += 1
                return {"saved": True}

        repository = FakeRepository()
        demo = AccountSnapshot(
            "main", "메인", "toss", "demo", "credentials missing", utc_now_iso(),
            PortfolioSummary(total=100, invested=100, cash=0, markets=[], sectors=[], concentration=0),
            positions=[Position("AAPL", "Apple", current_price=100)],
        )
        rejected = PortfolioOntologyProjectionRecorder(repository).record_snapshot(demo)
        live = AccountSnapshot(
            "main", "메인", "toss", "live", "ok", utc_now_iso(),
            PortfolioSummary(total=100, invested=100, cash=0, markets=[], sectors=[], concentration=0),
            positions=[Position("AAPL", "Apple", current_price=100)],
        )
        deferred = PortfolioOntologyProjectionRecorder(
            repository,
            settings={"typedbNativeRuleExecutionEnabled": "0"},
        ).record_snapshot(live)

        self.assertEqual("rejected-non-live-snapshot", rejected["status"])
        self.assertEqual("deferred-to-reasoning-worker", deferred["status"])
        self.assertTrue(deferred["singleWriter"])
        self.assertEqual(0, repository.save_calls)

    def test_typedb_schema_function_sync_skips_redefine_when_function_exists(self):
        class FakeQuery:
            def __init__(self, driver, query):
                self.driver = driver
                self.query = str(query or "")

            def resolve(self):
                stripped = self.query.lstrip()
                if stripped.startswith("redefine"):
                    self.driver.redefine_called = True
                    raise AssertionError("redefine should not be called for content-hashed schema functions")
                if stripped.startswith("define\nfun "):
                    self.driver.define_attempts += 1
                    raise RuntimeError("[FUN5] A function with name 'orbit_rule_test' already exists")
                return []

        class FakeTransaction:
            def __init__(self, driver):
                self.driver = driver

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def query(self, query):
                return FakeQuery(self.driver, query)

            def commit(self):
                self.driver.commits += 1

        class FakeDriver:
            def __init__(self):
                self.define_attempts = 0
                self.redefine_called = False
                self.commits = 0

            def transaction(self, *_args, **_kwargs):
                return FakeTransaction(self)

        class FakeRepository(TypeDBOntologyGraphRepository):
            def __init__(self):
                super().__init__("127.0.0.1:1729", retry_count=0)
                self.fake_driver = FakeDriver()

            def driver_imports(self):
                return (object, object, object, object, SimpleNamespace(SCHEMA="schema")), None

            def open_driver(self, _imported):
                return self.fake_driver

            def ensure_database(self, _driver):
                return None

            def ensure_schema(self, _driver, _imported):
                return None

            def close_driver(self, _driver):
                return None

        repository = FakeRepository()

        rule = default_graph_inference_rules()[0]
        with patch.object(repository, "probe_typedb_native_rule_functions", side_effect=[
            {
                "status": "missing",
                "available": False,
                "missingRuleIds": [rule.rule_id],
                "probedCount": 0,
            },
            {
                "status": "ok",
                "available": True,
                "probedCount": 1,
                "verifiedRuleCount": 1,
            },
        ]), patch.object(
            repository,
            "probe_typedb_schema_function_definitions",
            side_effect=lambda definitions: {
                "status": "missing",
                "available": False,
                "missingFunctionNames": [item.get("functionName") for item in definitions],
            },
        ):
            result = repository.sync_typedb_native_rule_functions([rule])

        self.assertEqual("ok", result["status"])
        self.assertGreater(repository.fake_driver.define_attempts, 0)
        self.assertFalse(repository.fake_driver.redefine_called)
        self.assertTrue(all(item["schemaFunctionSyncStatus"] == "already-exists" for item in result["syncedFunctions"]))

    def test_typedb_null_repository_is_explicitly_disabled(self):
        result = NullTypeDBOntologyGraphRepository().save_graph(PortfolioOntology("empty"))

        self.assertFalse(result["saved"])
        self.assertEqual("disabled", result["status"])
        self.assertEqual("typedb", result["graphStore"])

    def test_repository_factory_builds_typedb_graph_store(self):
        repository = ontology_repository_from_settings({
            "ontologyTypeDbEnabled": "1",
            "typedbAddress": "127.0.0.1:1729",
            "typedbUser": "admin",
            "typedbPassword": "password",
            "typedbDatabase": "orbit_alpha_ontology",
            "typedbTlsEnabled": "0",
            "typedbTimeoutSeconds": "20",
        })

        self.assertIsInstance(repository, TypeDBOntologyGraphRepository)
        self.assertEqual("typedb", repository.store_key)

    def test_runtime_composition_uses_generic_graph_store_factory(self):
        root = Path(__file__).resolve().parents[2]
        for relative in [
            "python_service/digital_twin/infrastructure/service_factory.py",
            "python_service/digital_twin/infrastructure/web_server.py",
            "python_service/digital_twin/infrastructure/cli.py",
        ]:
            source = (root / relative).read_text(encoding="utf-8")
            self.assertIn("ontology_graph_store", source)
            self.assertNotIn("from .typedb_ontology import ontology_repository_from_settings", source)
            self.assertNotIn("from ..infrastructure.typedb_ontology import ontology_repository_from_settings", source)

    def test_service_manager_adds_typedb_only_when_graph_store_requests_it(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "ontologyTypeDbEnabled": "0",
        }):
            self.assertNotIn("typedb", service_manager.worker_specs())

        with patch.object(service_manager, "runtime_settings", return_value={
            "ontologyTypeDbEnabled": "1",
            "typedbAddress": "127.0.0.1:1729",
        }), patch.object(service_manager, "typedb_executable", return_value="/tmp/typedb"):
            workers = service_manager.worker_specs()

        self.assertIn("typedb", workers)
        self.assertEqual(["mysql", "typedb"], list(workers.keys())[:2])
        command = workers["typedb"]["command"]
        self.assertIn("server", command)
        self.assertIn("--server.listen-address", command)
        self.assertIn("--storage.data-directory", command)
        self.assertEqual("typedb", workers["typedb"]["role"])
        self.assertEqual("24", workers["typedb"]["retentionHours"])
        self.assertEqual("2048", workers["typedb"]["maxSizeMb"])
        self.assertEqual("0", workers["typedb"]["ageResetEnabled"])
        self.assertEqual("127.0.0.1:1729", workers["typedb"]["healthAddress"])
        self.assertEqual("60", workers["typedb"]["startupWaitSeconds"])
        self.assertEqual("1", workers["typedb"]["seedOnStart"])
        self.assertEqual("1", workers["typedb"]["seedReplaceRuleBox"])
        self.assertEqual("1", workers["typedb"]["seedKeepInference"])
        self.assertEqual("360", workers["typedb"]["seedTimeoutSeconds"])
        self.assertEqual("2", workers["typedb"]["seedRetryCount"])

    def test_service_manager_waits_for_typedb_driver_readiness_after_tcp_opens(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "pid": Path(temp) / "typedb.pid",
                "log": Path(temp) / "typedb.log",
                "needle": "typedb_server_bin",
                "healthAddress": "127.0.0.1:1729",
                "startupWaitSeconds": "2",
            }
            spec["pid"].write_text("123\n", encoding="utf-8")
            with patch.object(service_manager, "pid_exists", return_value=True), \
                    patch.object(service_manager, "tcp_ready", side_effect=[False, True, True]), \
                    patch.object(service_manager, "typedb_driver_ready", side_effect=[False, True]) as driver_ready, \
                    patch.object(service_manager.time, "sleep", return_value=None):
                self.assertTrue(service_manager.wait_for_typedb_ready(spec))
                self.assertEqual(2, driver_ready.call_count)

        self.assertEqual(("127.0.0.1", 1729), service_manager.typedb_host_port("127.0.0.1:1729"))
        self.assertEqual(("127.0.0.1", 1729), service_manager.typedb_host_port("http://127.0.0.1:1729"))

    def test_service_manager_seeds_typedb_rulebox_before_dependents_start(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "log": Path(temp) / "typedb.log",
                "seedOnStart": "1",
                "seedReplaceRuleBox": "1",
                "seedKeepInference": "1",
                "seedTimeoutSeconds": "5",
                "seedRetryCount": "1",
            }
            results = [
                SimpleNamespace(returncode=1, stdout="", stderr="TypeDB is warming up"),
                SimpleNamespace(returncode=0, stdout='{"status":"ok","ruleBoxReplaced":true}', stderr=""),
            ]

            with patch.object(service_manager.subprocess, "run", side_effect=results) as run, \
                    patch.object(service_manager.time, "sleep", return_value=None):
                self.assertTrue(service_manager.ensure_typedb_seeded(spec))

            self.assertEqual(2, run.call_count)
            command = run.call_args[0][0]
            self.assertEqual(command[:5], [
                service_manager.sys.executable,
                "-u",
                "python_service/service.py",
                "ontology",
                "seed",
            ])
            self.assertIn("--replace-rulebox", command)
            self.assertIn("--keep-inference", command)
            log_text = spec["log"].read_text(encoding="utf-8")
            self.assertIn("seed failed attempt=1", log_text)
            self.assertIn("seed ok attempt=2", log_text)

    def test_service_manager_fails_start_when_typedb_rulebox_seed_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "log": Path(temp) / "typedb.log",
                "seedOnStart": "1",
                "seedReplaceRuleBox": "1",
                "seedKeepInference": "1",
                "seedTimeoutSeconds": "5",
                "seedRetryCount": "0",
            }

            with patch.object(service_manager.subprocess, "run", return_value=SimpleNamespace(
                returncode=1,
                stdout='{"status":"rulebox-replace-failed"}',
                stderr="",
            )):
                self.assertFalse(service_manager.ensure_typedb_seeded(spec))

            self.assertIn("seed failed attempt=1 exit=1", spec["log"].read_text(encoding="utf-8"))

    def test_service_manager_does_not_start_dependents_when_typedb_is_not_ready(self):
        calls = []
        specs = {
            "typedb": {"label": "TypeDB ontology graph store", "role": "typedb"},
            "monitor": {"label": "Python realtime monitor"},
        }

        def fake_start_worker(spec):
            calls.append(spec["label"])
            return 1 if spec.get("role") == "typedb" else 0

        with patch.object(service_manager, "worker_specs", return_value=specs), \
                patch.object(service_manager, "start_worker", side_effect=fake_start_worker):
            self.assertEqual(1, service_manager.start())

        self.assertEqual(["TypeDB ontology graph store"], calls)

    def test_service_manager_does_not_reseed_an_already_running_typedb(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "pid": Path(temp) / "typedb.pid",
                "log": Path(temp) / "typedb.log",
                "needle": "typedb_server_bin",
                "command": ["typedb", "server"],
            }
            spec["pid"].write_text("123\n", encoding="utf-8")
            with patch.object(service_manager, "is_running", return_value=True), \
                    patch.object(service_manager, "wait_for_typedb_ready", return_value=True), \
                    patch.object(service_manager, "ensure_typedb_seeded") as ensure_seeded, \
                    patch.object(service_manager, "status_worker", return_value=0):
                self.assertEqual(0, service_manager.start_worker(spec))

        ensure_seeded.assert_not_called()

    def test_service_manager_restart_preserves_typedb_unless_explicitly_requested(self):
        specs = {"typedb": {"role": "typedb", "pid": Path("/tmp/typedb.pid")}}
        with patch.object(service_manager, "worker_specs", return_value=specs), \
                patch.object(service_manager, "read_pid", return_value=123), \
                patch.object(service_manager, "is_running", return_value=True), \
                patch.object(service_manager, "stop", return_value=0) as stop, \
                patch.object(service_manager, "start", return_value=0) as start:
            self.assertEqual(0, service_manager.restart())
            stop.assert_called_once_with(excluded_roles={"typedb"}, include_supervisor=False)
            start.assert_called_once_with(excluded_roles={"typedb"})

        with patch.object(service_manager, "worker_specs", return_value=specs), \
                patch.object(service_manager, "stop", return_value=0) as stop, \
                patch.object(service_manager, "start", return_value=0) as start:
            self.assertEqual(0, service_manager.restart(restart_typedb=True))
            stop.assert_called_once_with(excluded_roles=set(), include_supervisor=False)
            start.assert_called_once_with(excluded_roles=set())

    def test_service_manager_restart_starts_typedb_when_it_is_down(self):
        specs = {"typedb": {"role": "typedb", "pid": Path("/tmp/typedb.pid")}}
        with patch.object(service_manager, "worker_specs", return_value=specs), \
                patch.object(service_manager, "read_pid", return_value=123), \
                patch.object(service_manager, "is_running", return_value=False), \
                patch.object(service_manager, "stop", return_value=0) as stop, \
                patch.object(service_manager, "start", return_value=0) as start:
            self.assertEqual(0, service_manager.restart())
            stop.assert_called_once_with(excluded_roles=set(), include_supervisor=False)
            start.assert_called_once_with(excluded_roles=set())

    def test_service_manager_restart_pauses_active_supervisor(self):
        specs = {}
        with patch.object(service_manager, "worker_specs", return_value=specs), \
                patch.object(service_manager, "supervisor_running", return_value=True), \
                patch.object(service_manager, "begin_supervisor_maintenance", return_value="restart-token") as begin, \
                patch.object(service_manager, "wait_for_supervisor_maintenance_ack", return_value=True) as wait, \
                patch.object(service_manager, "end_supervisor_maintenance") as end, \
                patch.object(service_manager, "stop", return_value=0) as stop, \
                patch.object(service_manager, "start", return_value=0) as start:
            self.assertEqual(0, service_manager.restart())

        begin.assert_called_once_with("restart")
        wait.assert_called_once_with("restart-token")
        stop.assert_called_once_with(excluded_roles=set(), include_supervisor=False)
        start.assert_called_once_with(excluded_roles=set())
        end.assert_called_once_with()

    def test_service_manager_supervisor_acknowledges_maintenance_token(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "python-supervisor-maintenance.json"
            with patch.object(service_manager, "supervisor_maintenance_path", return_value=marker):
                token = service_manager.begin_supervisor_maintenance("restart")
                service_manager.acknowledge_supervisor_maintenance()
                payload = service_manager.read_supervisor_maintenance_payload()

        self.assertEqual(token, payload["token"])
        self.assertEqual(service_manager.os.getpid(), payload["acknowledgedByPid"])
        self.assertTrue(payload["acknowledgedAt"])

    def test_service_manager_stop_detaches_launch_agent_before_supervisor_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            launch_agent = Path(temp) / "com.orbitalpha.services.plist"
            launch_agent.write_text("plist", encoding="utf-8")
            with patch.object(service_manager, "launch_agent_path", return_value=launch_agent), \
                    patch.object(service_manager.shutil, "which", return_value="/bin/launchctl"), \
                    patch.object(service_manager.subprocess, "run") as run, \
                    patch.object(service_manager, "read_pid", return_value=123), \
                    patch.object(service_manager, "command_for_pid", return_value="monitor_service.py supervise"), \
                    patch.object(service_manager.os, "kill") as kill, \
                    patch.object(service_manager, "pid_exists", return_value=False), \
                    patch.object(service_manager, "remove_pid"):
                service_manager.stop_supervisor()

        run.assert_called_once_with(
            ["/bin/launchctl", "bootout", "gui/" + str(service_manager.os.getuid()), str(launch_agent)],
            capture_output=True,
            text=True,
        )
        kill.assert_called_once_with(123, service_manager.signal.SIGTERM)

    def test_service_manager_launch_agent_allows_complete_shutdown(self):
        with tempfile.TemporaryDirectory() as temp:
            launch_agent = Path(temp) / "com.orbitalpha.services.plist"
            supervisor_log = Path(temp) / "python-supervisor.log"
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch.object(service_manager, "launch_agent_path", return_value=launch_agent), \
                    patch.object(service_manager, "supervisor_log_path", return_value=supervisor_log), \
                    patch.object(service_manager.subprocess, "run", return_value=completed) as run:
                self.assertEqual(0, service_manager.install_supervisor())

            with launch_agent.open("rb") as handle:
                payload = service_manager.plistlib.load(handle)

        self.assertEqual(180, payload["ExitTimeOut"])
        self.assertTrue(payload["KeepAlive"])
        self.assertTrue(payload["RunAtLoad"])
        self.assertEqual(
            ["launchctl", "kickstart", "gui/" + str(service_manager.os.getuid()) + "/com.orbitalpha.services"],
            run.call_args_list[-1].args[0],
        )

    def test_typedb_retention_requires_maintenance_when_size_exceeds_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir(parents=True)
            (data_path / "wal").mkdir()
            (data_path / "wal" / "wal-1").write_bytes(b"x" * (2 * 1024 * 1024))
            marker_path = Path(temp) / "typedb-retention.json"
            spec = {
                "role": "typedb",
                "dataPath": data_path,
                "autoResetEnabled": "1",
                "retentionHours": "24",
                "maxSizeMb": "1",
            }

            with patch.object(service_manager, "data_dir", return_value=Path(temp)):
                result = service_manager.run_typedb_data_retention(spec)

            self.assertEqual("maintenance-required", result["status"])
            self.assertTrue(result["destructiveResetBlocked"])
            self.assertTrue(data_path.exists())
            self.assertFalse(marker_path.exists())

    def test_typedb_retention_resets_projection_data_only_when_forced(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir(parents=True)
            (data_path / "wal").mkdir()
            (data_path / "wal" / "wal-1").write_bytes(b"x" * (2 * 1024 * 1024))
            marker_path = Path(temp) / "typedb-retention.json"
            spec = {
                "role": "typedb",
                "dataPath": data_path,
                "autoResetEnabled": "1",
                "retentionHours": "24",
                "maxSizeMb": "1",
            }

            with patch.object(service_manager, "data_dir", return_value=Path(temp)):
                result = service_manager.run_typedb_data_retention(spec, force=True)

            self.assertEqual("reset", result["status"])
            self.assertFalse(data_path.exists())
            self.assertTrue(marker_path.exists())
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(24, marker["retentionHours"])
            self.assertEqual(1, marker["maxSizeMb"])

    def test_typedb_retention_skips_when_under_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir(parents=True)
            (data_path / "small").write_text("ok", encoding="utf-8")
            spec = {
                "role": "typedb",
                "dataPath": data_path,
                "autoResetEnabled": "1",
                "retentionHours": "24",
                "maxSizeMb": "1024",
            }

            with patch.object(service_manager, "data_dir", return_value=Path(temp)):
                result = service_manager.run_typedb_data_retention(spec)

            self.assertEqual("skipped", result["status"])
            self.assertTrue(data_path.exists())

    def test_typedb_retention_does_not_delete_active_store_for_age_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir(parents=True)
            (data_path / "active").write_text("ok", encoding="utf-8")
            spec = {
                "role": "typedb",
                "dataPath": data_path,
                "autoResetEnabled": "1",
                "ageResetEnabled": "0",
                "retentionHours": "1",
                "maxSizeMb": "1024",
            }

            with patch.object(service_manager, "data_dir", return_value=Path(temp)), \
                    patch.object(service_manager, "typedb_data_age_hours", return_value=72.0):
                result = service_manager.run_typedb_data_retention(spec)

            self.assertEqual("skipped", result["status"])
            self.assertFalse(result["ageResetEnabled"])
            self.assertTrue(data_path.exists())

    def test_graph_store_contract_is_shared_by_all_adapters(self):
        repositories = [
            NullTypeDBOntologyGraphRepository(),
            TypeDBOntologyGraphRepository(""),
            TypeDBOntologyGraphRepository(""),
        ]

        for repository in repositories:
            self.assertEqual([], ontology_graph_repository_contract_errors(repository), repository.__class__.__name__)

    def test_graph_store_contract_catches_partial_implementations(self):
        class PartialRepository:
            def save_graph(self, graph):
                return {}

        errors = ontology_graph_repository_contract_errors(PartialRepository())

        self.assertGreaterEqual(len(errors), len(ONTOLOGY_GRAPH_REPOSITORY_CONTRACT) - 1)
        self.assertTrue(any("active_tbox_metadata" in error for error in errors))

    def test_typedb_rulebox_snapshot_reads_persisted_typeql_rows(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        expected_rules = default_graph_inference_rules()[:2]
        graph = rulebox_graph_from_rules(expected_rules)
        entity_rows = [
            {
                "id": row["id"],
                "label": row["label"],
                "kind": row["kind"],
                "updatedAt": "2026-07-10T00:00:00Z",
                "json": row["propertiesJson"],
            }
            for row in repository.rows_for_entities(graph)
            if row["ontologyBox"] == "RuleBox"
        ]
        typedb_entity_rows = repository.entity_rows_from_typeql(entity_rows, "RuleBox")
        relation_rows = repository.rows_for_relations(graph)

        with patch.object(repository, "read_entity_rows", return_value=list(reversed(typedb_entity_rows))), patch.object(repository, "read_relation_rows", return_value=relation_rows):
            snapshot = repository.rulebox_snapshot()

        self.assertEqual("ok", snapshot["status"])
        self.assertEqual("typedb-typeql", snapshot["source"])
        self.assertEqual("typedb", snapshot["graphStore"])
        self.assertGreaterEqual(snapshot["ruleCount"], 1)
        self.assertFalse(snapshot["defaultsFallbackUsed"])
        self.assertEqual("typedb-native-rule-materialization", snapshot["nativeReasoningProfile"]["reasoningModel"])
        expected_payload = [rule.to_dict() for rule in expected_rules]
        self.assertEqual(len(expected_payload), snapshot["ruleCount"])
        self.assertEqual(
            sorted(rule["rule_id"] for rule in expected_payload),
            sorted(rule["rule_id"] for rule in snapshot["rules"]),
        )
        first_rule = next(item for item in snapshot["rules"] if item["rule_id"] == expected_payload[0]["rule_id"])
        self.assertEqual(expected_payload[0]["conditions"][0]["condition_id"], first_rule["conditions"][0]["condition_id"])
        self.assertEqual(expected_payload[0]["derivations"][0]["tbox_class"], first_rule["derivations"][0]["tbox_class"])

    def test_typedb_seed_ontology_replaces_rulebox_when_requested(self):
        class CapturingSeedRepository(TypeDBOntologyGraphRepository):
            def __init__(self):
                super().__init__("127.0.0.1:1729")
                self.saved_graphs = []
                self.inference_clear_count = 0

            def save_graph(self, graph):
                self.saved_graphs.append(graph)
                return {
                    "configured": True,
                    "saved": True,
                    "status": "ok",
                    "graphStore": "typedb",
                    "entityCount": len(graph.entities),
                    "relationCount": len(graph.relations),
                }

            def seed_graph_preflight(self, _graph, _rules_payload):
                return {"ready": False, "status": "stale"}

            def rulebox_snapshot(self):
                rules = [item.to_dict() for item in default_graph_inference_rules()]
                from digital_twin.infrastructure.typedb_ontology import rulebox_runtime_metadata

                metadata = rulebox_runtime_metadata(rules)
                return {
                    "configured": True,
                    "saved": True,
                    "status": "ok",
                    "graphStore": "typedb",
                    "rules": rules,
                    "ruleCount": len(rules),
                    "conditionCount": metadata["ruleboxConditionCount"],
                    "derivationCount": metadata["ruleboxDerivationCount"],
                    "ruleboxRulesHash": metadata["ruleboxRulesHash"],
                    "ruleboxShortHash": metadata["ruleboxShortHash"],
                }

            def clear_inferencebox(self):
                self.inference_clear_count += 1
                return {"configured": True, "status": "ok", "graphStore": "typedb"}

            def sync_typedb_native_rule_functions(self, _rules, force=False):
                return {"status": "ok", "schemaFunctionSyncCached": True}

        repository = CapturingSeedRepository()

        result = repository.seed_ontology({"replaceRuleBox": True, "clearInference": True})

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["saved"])
        self.assertTrue(result["ruleBoxReplaced"])
        self.assertEqual(1, len(repository.saved_graphs))
        self.assertEqual(result["expectedRuleBoxRuleCount"], result["activeRuleBoxRuleCount"])
        self.assertEqual(result["expectedRuleBoxShortHash"], result["activeRuleBoxShortHash"])
        self.assertEqual(1, repository.inference_clear_count)
        saved_boxes = {box for graph in repository.saved_graphs for box in node_boxes(graph)}
        self.assertNotIn("ABox", saved_boxes)
        self.assertNotIn("InferenceBox", saved_boxes)

    def test_typedb_seed_ontology_skips_current_seed_generation(self):
        class CurrentSeedRepository(TypeDBOntologyGraphRepository):
            def __init__(self):
                super().__init__("127.0.0.1:1729")
                self.save_calls = 0

            def seed_graph_preflight(self, _graph, _rules_payload):
                return {"ready": True, "status": "current"}

            def save_graph(self, _graph):
                self.save_calls += 1
                raise AssertionError("current seed generation must not be rewritten")

            def sync_typedb_native_rule_functions(self, _rules, force=False):
                return {"status": "ok", "schemaFunctionSyncCached": True}

        repository = CurrentSeedRepository()

        result = repository.seed_ontology({"replaceRuleBox": True})

        self.assertEqual("unchanged", result["status"])
        self.assertTrue(result["saved"])
        self.assertTrue(result["seeded"])
        self.assertTrue(result["seedSkipped"])
        self.assertTrue(result["ruleBoxAlreadyCurrent"])
        self.assertEqual(0, repository.save_calls)

    def test_seed_schema_function_sync_blocks_realtime_ready_status_when_deployment_fails(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        with patch.object(repository, "sync_typedb_native_rule_functions", return_value={
            "status": "error",
            "reason": "schema connection failed",
        }):
            result = repository.with_seed_schema_function_sync(
                {"configured": True, "saved": True, "seeded": True, "status": "ok"},
                default_graph_inference_rules()[:1],
            )

        self.assertFalse(result["saved"])
        self.assertFalse(result["seeded"])
        self.assertEqual("schema-function-sync-failed", result["status"])
        self.assertIn("schema connection failed", result["reason"])

    def test_typedb_rule_condition_rows_keep_node_kind_separate_from_condition_kind(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        rows = repository.entity_rows_from_typeql([
            {
                "id": "rule-condition:test",
                "label": "보유 종목",
                "kind": "rule-condition",
                "updatedAt": "2026-07-12T00:00:00Z",
                "json": json.dumps({
                    "ontologyBox": "RuleBox",
                    "ruleId": "graph.test",
                    "condition": {
                        "condition_id": "holding-source",
                        "kind": "subject_property",
                        "field": "source",
                        "operator": "==",
                        "value": "holding",
                    },
                }),
            }
        ], "RuleBox")

        self.assertEqual("rule-condition", rows[0]["nodeKind"])
        self.assertEqual("subject_property", rows[0]["kind"])

    def test_typedb_inferencebox_snapshot_reads_persisted_typeql_rows(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        entity_rows = [
            {
                "id": "risk:005930:loss",
                "label": "삼성전자 손실 방어",
                "kind": "risk-signal",
                "ontologyBox": "InferenceBox",
                "symbol": "005930",
                "ruleId": "graph.loss_guard.breakdown.v1",
                "tboxClass": "RiskSignal",
                "reviewLevel": "act",
                "dataState": "sufficient",
                "validationState": "conditional",
                "nativeTypeDbReasoned": True,
                "propertiesJson": json.dumps({
                    "ontologyBox": "InferenceBox",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "reviewLevel": "act",
                    "dataState": "sufficient",
                    "validationState": "conditional",
                    "decisionStage": "LOSS_REDUCE",
                    "nativeTypeDbReasoned": True,
                }),
            },
            {
                "id": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                "label": "삼성전자 · 손실 방어 추론",
                "kind": "inference-trace",
                "ontologyBox": "InferenceBox",
                "symbol": "005930",
                "ruleId": "graph.loss_guard.breakdown.v1",
                "reviewLevel": "act",
                "dataState": "sufficient",
                "validationState": "conditional",
                "nativeTypeDbReasoned": True,
                "propertiesJson": json.dumps({
                    "ontologyBox": "InferenceBox",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "reviewLevel": "act",
                    "dataState": "sufficient",
                    "validationState": "conditional",
                    "matchedConditions": [{"conditionId": "holding-loss"}],
                    "freshnessStatus": "fresh",
                    "nativeTypeDbReasoned": True,
                }),
            },
        ]
        relation_rows = [
            {
                "source": "stock:005930",
                "sourceLabel": "삼성전자",
                "target": "risk:005930:loss",
                "targetLabel": "삼성전자 손실 방어",
                "type": "HAS_INFERRED_RISK",
                "ontologyBox": "InferenceBox",
                "symbol": "005930",
                "ruleId": "graph.loss_guard.breakdown.v1",
                "weight": 0.86,
                "nativeTypeDbReasoned": True,
                "propertiesJson": json.dumps({
                    "ontologyBox": "InferenceBox",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "evidenceRole": "risk",
                    "reviewLevel": "act",
                    "dataState": "sufficient",
                    "decisionStage": "LOSS_REDUCE",
                    "aiInfluenceLabel": "손실 방어 추론",
                    "inferenceTraceId": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                    "nativeTypeDbReasoned": True,
                    "reasoningMode": "typedb-native-rule-materialized",
                    "materializationSource": "typedb-abox-native-rule",
                    "ruleboxRulesHash": "rulebox-hash-1",
                    "ruleboxRuleCount": 23,
                }),
            },
        ]

        with patch.object(repository, "read_inference_generation_records", return_value=[]), patch.object(repository, "read_entity_rows", return_value=entity_rows), patch.object(repository, "read_relation_rows", return_value=relation_rows):
            snapshot = repository.inferencebox_snapshot(symbols=["005930"])

        self.assertEqual("ok", snapshot["status"])
        self.assertEqual("typedbInferenceBox", snapshot["source"])
        self.assertEqual("typedb", snapshot["graphStore"])
        self.assertEqual("typedb-native-rule-materialized", snapshot["reasoningMode"])
        self.assertEqual("typedb-abox-native-rule", snapshot["materializationSource"])
        self.assertEqual("typedb-typeql", snapshot["querySource"])
        self.assertEqual("ok", snapshot["typedbReadStatus"])
        self.assertEqual(2, snapshot["entityCount"])
        self.assertEqual(1, snapshot["relationCount"])
        self.assertTrue(snapshot["nativeTypeDbReasoningUsed"])
        self.assertFalse(snapshot["typedbBootstrapReasoningUsed"])
        self.assertEqual("rulebox-hash-1", snapshot["ruleboxRulesHash"])
        self.assertEqual(23, snapshot["ruleboxRuleCount"])
        self.assertEqual(0, snapshot["ignoredNonNativeRelationCount"])
        self.assertEqual(["holding-loss"], snapshot["traces"][0]["matchedConditionIds"])
        self.assertEqual("act", snapshot["traces"][0]["reviewLevel"])
        self.assertEqual("sufficient", snapshot["traces"][0]["dataState"])
        self.assertEqual("conditional", snapshot["traces"][0]["validationState"])
        self.assertEqual("fresh", snapshot["traces"][0]["freshnessStatus"])

    def test_typedb_inferencebox_snapshot_exposes_typeql_read_errors(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        with patch.object(repository, "read_inference_generation_records", side_effect=RuntimeError("schema unavailable")):
            snapshot = repository.inferencebox_snapshot(symbols=["005930"])

        self.assertEqual("error", snapshot["status"])
        self.assertEqual("typedbInferenceBox", snapshot["source"])
        self.assertEqual("typedb", snapshot["graphStore"])
        self.assertEqual("typedb-typeql-read", snapshot["reasoningMode"])
        self.assertEqual("typedb-typeql", snapshot["querySource"])
        self.assertEqual("error", snapshot["typedbReadStatus"])
        self.assertIn("schema unavailable", snapshot["typedbReadReason"])
        self.assertIn("TypeDB InferenceBox 조회 실패", snapshot["reason"])
        self.assertFalse(snapshot["typedbBootstrapReasoningUsed"])

    def test_typedb_inferencebox_fails_closed_when_abox_generation_changed(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        entity_rows = [{
            "id": "risk:005930:gen:test",
            "kind": "risk-signal",
            "symbol": "005930",
            "ontologyBox": "InferenceBox",
            "nativeTypeDbReasoned": True,
            "propertiesJson": json.dumps({
                "nativeTypeDbReasoned": True,
                "sourceAboxSnapshotId": "abox-snapshot:old",
            }),
        }]
        relation_rows = [{
            "source": "stock:005930",
            "target": "risk:005930:gen:test",
            "type": "HAS_INFERRED_RISK",
            "symbol": "005930",
            "ontologyBox": "InferenceBox",
            "nativeTypeDbReasoned": True,
            "propertiesJson": json.dumps({
                "nativeTypeDbReasoned": True,
                "sourceAboxSnapshotId": "abox-snapshot:old",
            }),
        }]
        with patch.object(repository, "read_inference_generation_records", return_value=[{"generationId": "inference-generation:test", "latestAt": "2026-07-20T00:00:00Z"}]), patch.object(repository, "read_inferencebox_entity_rows", return_value=entity_rows), patch.object(repository, "read_inferencebox_relation_rows", return_value=relation_rows), patch.object(repository, "active_abox_metadata", return_value={"status": "ok", "aboxSnapshotId": "abox-snapshot:new"}):
            snapshot = repository.inferencebox_snapshot(symbols=["005930"])

        self.assertEqual("stale-generation", snapshot["status"])
        self.assertFalse(snapshot["generationAligned"])
        self.assertFalse(snapshot["nativeTypeDbReasoningUsed"])
        self.assertEqual([], snapshot["relations"])

    def test_typedb_rulebox_execution_materializes_inferencebox_from_typedb_abox_rulebox(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        graph = PortfolioOntology("typedb-run-rulebox")
        graph.entities.append(OntologyEntity("stock:005930", "삼성전자", "stock", {
            "ontologyBox": "ABox",
            "aboxSnapshotId": "abox-snapshot:test",
            "symbol": "005930",
            "source": "holding",
            "profitLossRate": -12.5,
        }))
        graph.entities.append(OntologyEntity("level:005930:ma20", "20일선", "key-level", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "levelType": "ma20",
        }))
        graph.entities.append(OntologyEntity("risk-budget:main", "리스크 예산", "risk-budget", {
            "ontologyBox": "ABox",
            "tboxClass": "RiskBudget",
        }))
        graph.relations.append(OntologyRelation("stock:005930", "level:005930:ma20", "BREAKS_LEVEL", 0.8, properties={
            "ontologyBox": "ABox",
        }))
        graph.relations.append(OntologyRelation("stock:005930", "risk-budget:main", "HAS_RISK_BUDGET", 1.0, properties={
            "ontologyBox": "ABox",
        }))
        rule_snapshot = {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "rules": [default_graph_inference_rules()[0].to_dict()],
            "ruleCount": 1,
        }
        native_match = {
            "status": "ok",
            "graphStore": "typedb",
            "nativeQueryUsed": True,
            "schemaFunctionUsed": True,
            "executedRuleCount": 1,
            "skippedRuleCount": 0,
            "matchedCount": 1,
            "matches": [{
                "ruleId": default_graph_inference_rules()[0].rule_id,
                "nativeRuleId": "typedb.native." + default_graph_inference_rules()[0].rule_id,
                "sourceId": "stock:005930",
                "matchedConditions": [{"conditionId": "holding-source"}],
                "evidenceRelationIds": ["ontology-assertion:test"],
                "confidence": 0.86,
            }],
        }

        captured = {}

        def capture_inferencebox(inference_graph):
            captured["graph"] = inference_graph
            return {"configured": True, "saved": True, "status": "ok", "graphStore": "typedb"}

        with patch.object(repository, "has_box_rows", return_value=True), patch.object(repository, "active_abox_metadata", return_value={"status": "ok", "aboxSnapshotId": "abox-snapshot:test"}), patch.object(repository, "rulebox_snapshot", return_value=rule_snapshot), patch.object(repository, "sync_typedb_native_rule_functions", return_value={"status": "ok", "syncedCount": 1, "syncedFunctionCount": 1, "skippedCount": 0, "failedCount": 0}), patch.object(repository, "match_typedb_native_rules", return_value=native_match), patch.object(repository, "clear_inferencebox", return_value={"status": "ok", "graphStore": "typedb"}), patch.object(repository, "load_graph_for_native_matches", return_value=graph), patch.object(repository, "write_inferencebox_graph", side_effect=capture_inferencebox):
            result = repository.run_rulebox({"clearInference": True})

        self.assertEqual("ok", result["status"])
        self.assertEqual("typedb-native-rule-materialized", result["reasoningMode"])
        self.assertGreater(result["statementCount"], 0)
        self.assertFalse(result["typedbBootstrapReasoningUsed"])
        self.assertTrue(result["pythonBootstrapDisabled"])
        self.assertIn("nativeReasoningProfile", result)
        self.assertTrue(result["typedbNativeRuleReasoningUsed"])
        self.assertTrue(result["typedbNativeFunctionReasoningUsed"])
        self.assertTrue(result["typedbNativeRuleQueryUsed"])
        self.assertEqual("ok", result["typedbNativeRuleQueryStatus"])
        self.assertTrue(result["nativeTypeDbReasoningUsed"])
        self.assertIn("HAS_INFERRED_RISK", result["relationTypes"])
        self.assertTrue(result["inferenceGenerationId"].startswith("inference-generation:"))
        self.assertEqual("symbols" if result["targetSymbols"] else "all-symbols", result["incrementalScope"])
        self.assertTrue(captured["graph"].entities)
        self.assertTrue(all((item.properties or {}).get("nativeTypeDbReasoned") for item in captured["graph"].entities))
        self.assertTrue(all((item.properties or {}).get("typedbNativeRuleReasoned") for item in captured["graph"].entities))
        self.assertTrue(all((item.properties or {}).get("nativeRuleId") for item in captured["graph"].entities))
        inferred_classes = {str((item.properties or {}).get("tboxClass") or "") for item in captured["graph"].entities}
        self.assertIn("WhyNow", inferred_classes)
        self.assertIn("SignalConflict", inferred_classes)
        self.assertIn("InferenceTimeline", inferred_classes)
        inferred_relation_types = {item.relation_type for item in captured["graph"].relations}
        self.assertIn("HAS_WHY_NOW", inferred_relation_types)
        self.assertIn("HAS_SIGNAL_CONFLICT", inferred_relation_types)
        self.assertIn("HAS_INFERENCE_TIMELINE", inferred_relation_types)
        self.assertEqual("typedb-native-rule-materialized", captured["graph"].worldview["reasoningMode"])
        self.assertEqual("typedb-abox-native-rule", captured["graph"].worldview["materializationSource"])
        self.assertTrue(all((item.properties or {}).get("snapshotId") == result["inferenceGenerationId"] for item in captured["graph"].entities))
        self.assertTrue(result["ruleboxRulesHash"])
        self.assertEqual(1, result["ruleboxRuleCount"])
        self.assertTrue(all((item.properties or {}).get("ruleboxRulesHash") == result["ruleboxRulesHash"] for item in captured["graph"].entities))
        self.assertEqual("abox-snapshot:test", result["sourceAboxSnapshotId"])
        self.assertTrue(all((item.properties or {}).get("sourceAboxSnapshotId") == "abox-snapshot:test" for item in captured["graph"].entities))
        self.assertEqual("skipped", result["clearResult"]["status"])

    def test_typedb_rulebox_accepts_scoped_source_generations_through_one_worldview_manifest(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        graph = PortfolioOntology("typedb-scoped-run-rulebox")
        graph.entities.extend([
            OntologyEntity("stock:005930", "삼성전자", "stock", {
                "ontologyBox": "ABox",
                "symbol": "005930",
                "scopeId": "symbol:005930",
                "aboxSnapshotId": "abox-scope:005930",
                "worldviewManifestId": "abox-manifest:live",
                "profitLossRate": -12.5,
            }),
            OntologyEntity("level:005930:ma20", "20일선", "key-level", {
                "ontologyBox": "ABox",
                "symbol": "005930",
                "scopeId": "symbol:005930",
                "aboxSnapshotId": "abox-scope:005930",
                "worldviewManifestId": "abox-manifest:live",
            }),
            OntologyEntity("risk-budget:main", "리스크 예산", "risk-budget", {
                "ontologyBox": "ABox",
                "scopeId": "policy:main",
                "aboxSnapshotId": "abox-scope:policy",
                "worldviewManifestId": "abox-manifest:live",
            }),
        ])
        graph.relations.extend([
            OntologyRelation("stock:005930", "level:005930:ma20", "BREAKS_LEVEL", 0.8, properties={
                "ontologyBox": "ABox",
            }),
            OntologyRelation("stock:005930", "risk-budget:main", "HAS_RISK_BUDGET", 1.0, properties={
                "ontologyBox": "ABox",
            }),
        ])
        rule = default_graph_inference_rules()[0]
        rule_snapshot = {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "rules": [rule.to_dict()],
            "ruleCount": 1,
        }
        native_match = {
            "status": "ok",
            "graphStore": "typedb",
            "nativeQueryUsed": True,
            "schemaFunctionUsed": True,
            "executedRuleCount": 1,
            "skippedRuleCount": 0,
            "matchedCount": 1,
            "matches": [{
                "ruleId": rule.rule_id,
                "nativeRuleId": "typedb.native." + rule.rule_id,
                "sourceId": "stock:005930",
                "matchedConditions": [{"conditionId": "holding-source"}],
                "evidenceRelationIds": ["ontology-assertion:test"],
                "confidence": 0.86,
            }],
        }
        captured = {}

        def capture_inferencebox(inference_graph):
            captured["graph"] = inference_graph
            return {"configured": True, "saved": True, "status": "ok", "graphStore": "typedb"}

        with patch.object(repository, "has_box_rows", return_value=True), \
                patch.object(repository, "active_abox_metadata", return_value={
                    "status": "ok",
                    "aboxSnapshotId": "abox-manifest:live",
                    "worldviewManifestId": "abox-manifest:live",
                    "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
                }), \
                patch.object(repository, "rulebox_snapshot", return_value=rule_snapshot), \
                patch.object(repository, "sync_typedb_native_rule_functions", return_value={
                    "status": "ok", "syncedCount": 1, "syncedFunctionCount": 1,
                    "skippedCount": 0, "failedCount": 0,
                }), \
                patch.object(repository, "match_typedb_native_rules", return_value=native_match), \
                patch.object(repository, "load_graph_for_native_matches", return_value=graph), \
                patch.object(repository, "write_inferencebox_graph", side_effect=capture_inferencebox):
            result = repository.run_rulebox({})

        self.assertEqual("ok", result["status"])
        self.assertEqual("worldview-manifest", result["sourceAboxGenerationMode"])
        self.assertEqual("abox-manifest:live", result["sourceAboxSnapshotId"])
        self.assertEqual("abox-manifest:live", result["sourceAboxManifestId"])
        self.assertTrue(all(
            (item.properties or {}).get("sourceAboxSnapshotId") == "abox-manifest:live"
            for item in captured["graph"].entities
        ))

    def test_typedb_rulebox_empty_result_preserves_previous_inference_generation(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        rule_snapshot = {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "rules": [default_graph_inference_rules()[0].to_dict()],
            "ruleCount": 1,
        }
        native_match = {
            "status": "ok",
            "nativeQueryUsed": True,
            "schemaFunctionUsed": True,
            "executedRuleCount": 1,
            "skippedRuleCount": 0,
            "matchedCount": 0,
            "matches": [],
        }
        previous = {
            "status": "ok",
            "inferenceGenerationId": "inference-generation:previous",
            "relations": [{"type": "HAS_INFERRED_RISK"}],
            "traces": [],
        }
        with patch.object(repository, "has_box_rows", return_value=True), patch.object(repository, "active_abox_metadata", return_value={"status": "ok", "aboxSnapshotId": "abox-snapshot:test"}), patch.object(repository, "rulebox_snapshot", return_value=rule_snapshot), patch.object(repository, "sync_typedb_native_rule_functions", return_value={"status": "ok", "syncedCount": 1, "skippedCount": 0, "failedCount": 0}), patch.object(repository, "match_typedb_native_rules", return_value=native_match), patch.object(repository, "load_graph_for_native_matches", return_value=PortfolioOntology("empty")), patch.object(repository, "inferencebox_snapshot", return_value=previous), patch.object(repository, "write_inferencebox_graph") as write_mock, patch.object(repository, "clear_inferencebox") as clear_mock:
            result = repository.run_rulebox({"forceClearInference": True, "allowDestructiveInferenceClear": True})

        write_mock.assert_not_called()
        clear_mock.assert_not_called()
        self.assertEqual("empty", result["status"])
        self.assertTrue(result["preservedPreviousInference"])
        self.assertFalse(result["activatedGeneration"])
        self.assertEqual("inference-generation:previous", result["inferenceBox"]["inferenceGenerationId"])

    def test_typedb_rulebox_execution_preserves_inferencebox_when_preflight_fails(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        with patch.object(repository, "has_box_rows", side_effect=RuntimeError("abox unavailable")), patch.object(repository, "clear_inferencebox") as clear_mock:
            result = repository.run_rulebox({"forceClearInference": True})

        clear_mock.assert_not_called()
        self.assertEqual("error", result["status"])
        self.assertIn("ABox", result["reason"])
        self.assertEqual("skipped", result["clearResult"]["status"])
        self.assertTrue(result["clearResult"]["preservedPreviousInference"])

    def test_typedb_rulebox_blocks_incomplete_abox_before_native_matching(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        with patch.object(repository, "has_box_rows", return_value=True), \
                patch.object(repository, "active_abox_metadata", return_value={
                    "status": "incomplete",
                    "aboxSnapshotId": "abox-snapshot:partial",
                    "reason": "ABox completion marker is missing.",
                }), \
                patch.object(repository, "rulebox_snapshot") as rulebox_snapshot, \
                patch.object(repository, "match_typedb_native_rules") as native_match:
            result = repository.run_rulebox({})

        self.assertEqual("incomplete-abox", result["status"])
        self.assertFalse(result["nativeTypeDbReasoningUsed"])
        self.assertIn("ABox", result["reason"])
        rulebox_snapshot.assert_not_called()
        native_match.assert_not_called()

    def test_typedb_inferencebox_snapshot_blocks_incomplete_active_abox(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        entity_rows = [{
            "id": "risk:005930:gen:test",
            "kind": "risk-signal",
            "symbol": "005930",
            "ontologyBox": "InferenceBox",
            "nativeTypeDbReasoned": True,
            "propertiesJson": json.dumps({
                "nativeTypeDbReasoned": True,
                "sourceAboxSnapshotId": "abox-snapshot:partial",
            }),
        }]
        relation_rows = [{
            "source": "stock:005930",
            "target": "risk:005930:gen:test",
            "type": "HAS_INFERRED_RISK",
            "symbol": "005930",
            "ontologyBox": "InferenceBox",
            "nativeTypeDbReasoned": True,
            "propertiesJson": json.dumps({
                "nativeTypeDbReasoned": True,
                "sourceAboxSnapshotId": "abox-snapshot:partial",
            }),
        }]

        with patch.object(repository, "read_inference_generation_records", return_value=[{"generationId": "inference-generation:test", "latestAt": "2026-07-20T00:00:00Z"}]), patch.object(repository, "read_inferencebox_entity_rows", return_value=entity_rows), patch.object(repository, "read_inferencebox_relation_rows", return_value=relation_rows), patch.object(repository, "active_abox_metadata", return_value={"status": "incomplete", "aboxSnapshotId": "abox-snapshot:partial", "reason": "ABox completion marker is missing."}):
            snapshot = repository.inferencebox_snapshot(symbols=["005930"])

        self.assertEqual("incomplete-abox", snapshot["status"])
        self.assertFalse(snapshot["generationAligned"])
        self.assertFalse(snapshot["nativeTypeDbReasoningUsed"])
        self.assertEqual([], snapshot["relations"])

    def test_typedb_inferencebox_graph_rewrites_ids_by_generation(self):
        graph = PortfolioOntology("typedb-generation")
        graph.entities.append(OntologyEntity("stock:005930", "삼성전자", "stock", {"ontologyBox": "ABox", "symbol": "005930"}))
        graph.entities.append(OntologyEntity("inference-trace:005930:rule", "trace", "inference-trace", {
            "ontologyBox": "InferenceBox",
            "symbol": "005930",
            "ruleId": "rule",
        }))
        graph.entities.append(OntologyEntity("risk:005930", "risk", "risk", {
            "ontologyBox": "InferenceBox",
            "symbol": "005930",
            "ruleId": "rule",
        }))
        graph.evidence.append(OntologyEvidence("evidence:inference:005930:rule", "stock:005930", "inference-trace", "test", "trace", {
            "ontologyBox": "InferenceBox",
            "ruleId": "rule",
        }))
        graph.relations.append(OntologyRelation("stock:005930", "risk:005930", "HAS_INFERRED_RISK", 0.9, ["evidence:inference:005930:rule"], {
            "ontologyBox": "InferenceBox",
            "ruleId": "rule",
        }))
        graph.relations.append(OntologyRelation("risk:005930", "inference-trace:005930:rule", "EXPLAINED_BY_TRACE", 0.9, [], {
            "ontologyBox": "InferenceBox",
            "ruleId": "rule",
        }))

        generated = typedb_inferencebox_graph(
            graph,
            generation_id="inference-generation:test",
            generation_at="2026-07-13T00:00:00Z",
            rulebox_metadata={"ruleboxRulesHash": "hash-1", "ruleboxRuleCount": 2},
        )

        inferred_entities = [item for item in generated.entities if item.kind != "inference-context-reference"]
        reference_entities = [item for item in generated.entities if item.kind == "inference-context-reference"]
        self.assertTrue(all(item.entity_id.endswith(":gen:" + item.entity_id.rsplit(":gen:", 1)[1]) for item in inferred_entities))
        self.assertIn("stock:005930", {item.entity_id for item in reference_entities})
        self.assertTrue(all(item.properties["referenceOnly"] for item in reference_entities))
        self.assertTrue(all((item.properties or {}).get("snapshotId") == "inference-generation:test" for item in generated.entities))
        self.assertTrue(all((item.properties or {}).get("ruleboxRulesHash") == "hash-1" for item in generated.entities))
        self.assertEqual("hash-1", generated.worldview["ruleboxRulesHash"])
        self.assertIn("stock:005930", {item.source for item in generated.relations})
        self.assertTrue(any(item.target.startswith("risk:005930:gen:") for item in generated.relations))
        self.assertTrue(generated.evidence[0].evidence_id.startswith("evidence:inference:005930:rule:gen:"))

    def test_typedb_retry_helper_retries_transient_failures(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=1)
        calls = {"count": 0}

        def operation():
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("transient")
            return "ok"

        self.assertEqual("ok", repository.with_typedb_retries(operation))
        self.assertEqual(2, calls["count"])

    def test_typedb_rulebox_save_failure_does_not_mark_inference_used(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        graph = PortfolioOntology("typedb-run-rulebox-save-failure")
        graph.entities.append(OntologyEntity("stock:005930", "삼성전자", "stock", {
            "ontologyBox": "ABox",
            "aboxSnapshotId": "abox-snapshot:save-failure",
            "symbol": "005930",
            "source": "holding",
            "profitLossRate": -12.5,
        }))
        graph.entities.append(OntologyEntity("level:005930:ma20", "20일선", "key-level", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "levelType": "ma20",
        }))
        graph.entities.append(OntologyEntity("risk-budget:main", "리스크 예산", "risk-budget", {
            "ontologyBox": "ABox",
            "tboxClass": "RiskBudget",
        }))
        graph.relations.append(OntologyRelation("stock:005930", "level:005930:ma20", "BREAKS_LEVEL", 0.8, properties={
            "ontologyBox": "ABox",
        }))
        graph.relations.append(OntologyRelation("stock:005930", "risk-budget:main", "HAS_RISK_BUDGET", 1.0, properties={
            "ontologyBox": "ABox",
        }))
        rule_snapshot = {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "rules": [default_graph_inference_rules()[0].to_dict()],
            "ruleCount": 1,
        }
        native_match = {
            "status": "ok",
            "graphStore": "typedb",
            "nativeQueryUsed": True,
            "schemaFunctionUsed": True,
            "executedRuleCount": 1,
            "skippedRuleCount": 0,
            "matchedCount": 1,
            "matches": [{
                "ruleId": default_graph_inference_rules()[0].rule_id,
                "nativeRuleId": "typedb.native." + default_graph_inference_rules()[0].rule_id,
                "sourceId": "stock:005930",
                "matchedConditions": [{"conditionId": "holding-source"}],
                "evidenceRelationIds": ["ontology-assertion:test"],
                "confidence": 0.86,
            }],
        }

        with patch.object(repository, "has_box_rows", return_value=True), patch.object(repository, "active_abox_metadata", return_value={"status": "ok", "aboxSnapshotId": "abox-snapshot:save-failure"}), patch.object(repository, "rulebox_snapshot", return_value=rule_snapshot), patch.object(repository, "sync_typedb_native_rule_functions", return_value={"status": "ok", "syncedCount": 1, "syncedFunctionCount": 1, "skippedCount": 0, "failedCount": 0}), patch.object(repository, "match_typedb_native_rules", return_value=native_match), patch.object(repository, "clear_inferencebox", return_value={"status": "ok", "graphStore": "typedb"}), patch.object(repository, "load_graph_for_native_matches", return_value=graph), patch.object(repository, "write_inferencebox_graph", return_value={"configured": True, "saved": False, "status": "error", "reason": "write failed"}):
            result = repository.run_rulebox({"clearInference": True})

        self.assertEqual("error", result["status"])
        self.assertEqual("write failed", result["reason"])
        self.assertGreater(result["relationCount"], 0)
        self.assertFalse(result["nativeTypeDbReasoningUsed"])

    def test_typedb_native_reasoning_profile_identifies_function_ready_rules(self):
        profile = typedb_native_reasoning_profile([rule.to_dict() for rule in default_graph_inference_rules()])

        self.assertEqual("typedb-native-rule-materialization", profile["reasoningModel"])
        self.assertEqual(TYPEDB_NATIVE_REASONING_PROFILE_VERSION, profile["version"])
        self.assertEqual(profile["ruleCount"], profile["nativeRuleCount"])
        self.assertTrue(profile["rules"][0]["nativeRuleId"].startswith("typedb.native."))
        self.assertEqual(profile["ruleCount"], profile["readyRuleCount"])
        self.assertEqual(0, profile["partialRuleCount"])
        self.assertTrue(profile["rules"][0]["schemaFunctionName"].startswith("orbit_rule_"))
        self.assertTrue(profile["materializationRequired"])

    def test_typedb_function_definition_keeps_any_groups_out_of_schema_compilation(self):
        rule = next(item for item in default_graph_inference_rules() if item.rule_id == "graph.loss_smart_money.add_buy_review.v1")
        definition = typedb_native_function_definition(rule.to_dict())

        self.assertEqual([], definition["helperFunctions"])
        self.assertEqual(1, len(definition["functionDefinitions"]))
        self.assertNotIn("anyConditionCount", definition["body"])
        self.assertNotIn("let $anyHelperSource", definition["body"])
        self.assertIn("($source: ontology-node)", definition["body"])

    def test_typedb_any_group_check_uses_typeql_distinct_condition_aggregation(self):
        rule = next(item for item in default_graph_inference_rules() if item.rule_id == "graph.loss_smart_money.add_buy_review.v1")

        query = typedb_native_any_group_check_query(
            rule.to_dict(),
            "stock:000660",
            scoped_manifest_only=True,
        )["query"]

        self.assertIn('has ontology-id "stock:000660"', query)
        self.assertIn("reduce $anyConditionCount = count($anyConditionToken) groupby $source", query)
        self.assertIn("$anyConditionCount >= 2", query)
        self.assertIn('has ontology-kind "worldview-manifest-active-pointer"', query)

    def test_typedb_function_definition_binds_active_manifest_without_legacy_branches(self):
        rule = next(item for item in default_graph_inference_rules() if item.rule_id == "graph.loss_guard.breakdown.v1")

        definition = typedb_native_function_definition(rule.to_dict())

        self.assertIn('has ontology-kind "worldview-manifest-active-pointer"', definition["body"])
        self.assertIn('has ontology-manifest-id $activeManifestId', definition["body"])
        self.assertNotIn('has ontology-kind "abox-active-pointer"', definition["body"])
        self.assertNotIn('has ontology-kind "abox-scope-active-pointer"', definition["body"])

    def test_typedb_function_definition_uses_promoted_subject_attributes(self):
        rule = next(
            item
            for item in default_graph_inference_rules()
            if item.rule_id == "graph.aggressive.loss_recovery.add_buy_review.v1"
        )
        definition = typedb_native_function_definition(rule.to_dict())
        body = definition["body"]
        any_group_query = typedb_native_any_group_check_query(
            rule.to_dict(),
            "stock:005930",
            scoped_manifest_only=True,
        )["query"]

        self.assertIn("ontology-investment-strategy-profile", body)
        self.assertIn("ontology-profit-loss-rate", body)
        self.assertIn("ontology-ma5-distance", body)
        self.assertIn("ontology-ma60-distance", body)
        self.assertIn("ontology-position-account-weight-pct", body)
        self.assertIn("ontology-smart-money-net-volume", any_group_query)
        self.assertIn("ontology-bid-ask-imbalance", any_group_query)

    def test_typedb_function_definition_uses_promoted_temporal_attributes(self):
        persistent_rule = next(
            item
            for item in default_graph_inference_rules()
            if item.rule_id == "graph.temporal.persistent_decline.risk.v1"
        )
        event_rule = next(
            item
            for item in default_graph_inference_rules()
            if item.rule_id == "graph.temporal.event_cluster.risk.v1"
        )
        persistent_body = typedb_native_function_definition(persistent_rule.to_dict())["body"]
        event_body = typedb_native_function_definition(event_rule.to_dict())["body"]

        self.assertIn("ontology-price-change-pct", persistent_body)
        self.assertIn("ontology-window-key", persistent_body)
        self.assertIn("ontology-recent-price-change-pct", persistent_body)
        self.assertIn("ontology-consecutive-decline-count", persistent_body)
        self.assertIn("ontology-valid-observation-ratio", persistent_body)
        self.assertNotIn("ontology-trend-episode-type", persistent_body)
        self.assertIn("ontology-risk-event-count", event_body)

    def test_typedb_schema_function_sync_uses_cache_for_same_rule_fingerprint(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        rule = default_graph_inference_rules()[0]
        definition = typedb_native_function_definition(rule.to_dict())
        definitions = []
        for item in list(definition.get("functionDefinitions") or []) or [definition]:
            definitions.append({
                **item,
                "ruleId": definition.get("ruleId") or item.get("ruleId"),
                "nativeRuleId": definition.get("nativeRuleId") or item.get("nativeRuleId"),
                "rootFunctionName": definition.get("functionName"),
            })
        sync_fingerprint = hashlib.sha256(json.dumps({
            "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
            "database": repository.database,
            "functions": [
                {
                    "functionName": item.get("functionName"),
                    "define": item.get("define"),
                    "redefine": item.get("redefine"),
                }
                for item in definitions
            ],
            "skipped": [],
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        repository._schema_function_sync_cache_key = sync_fingerprint
        repository._schema_function_sync_cache_result = {
            "configured": True,
            "status": "ok",
            "graphStore": "typedb",
            "syncedCount": 1,
            "syncedFunctionCount": len(definitions),
            "skippedCount": 0,
            "failedCount": 0,
        }

        with patch.object(repository, "probe_typedb_native_rule_functions", return_value={
            "status": "ok",
            "available": True,
            "probedCount": 1,
        }) as probe, patch.object(repository, "driver_imports") as driver_imports:
            result = repository.sync_typedb_native_rule_functions([rule])

        probe.assert_called_once()
        driver_imports.assert_not_called()
        self.assertTrue(result["cached"])
        self.assertTrue(result["schemaFunctionSyncCached"])
        self.assertEqual(sync_fingerprint, result["syncFingerprint"])

    def test_typedb_schema_function_sync_probes_existing_functions_before_define(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        rule = default_graph_inference_rules()[0]

        with patch.object(repository, "probe_typedb_native_rule_functions", return_value={
            "status": "ok",
            "available": True,
            "probedCount": 1,
        }) as probe, patch.object(repository, "driver_imports") as driver_imports:
            result = repository.sync_typedb_native_rule_functions([rule])

        probe.assert_called_once()
        driver_imports.assert_not_called()
        self.assertEqual("ok", result["status"])
        self.assertTrue(result["schemaFunctionProbeUsed"])
        self.assertTrue(result["schemaFunctionSyncCached"])
        self.assertGreater(result["syncedFunctionCount"], 0)
        self.assertTrue(all(item["schemaFunctionSyncStatus"] == "verified-existing" for item in result["syncedFunctions"]))

    def test_typedb_schema_function_probe_verifies_every_root_function(self):
        rules = default_graph_inference_rules()[:4]
        function_names = [
            str(typedb_native_function_definition(rule.to_dict()).get("functionName") or "")
            for rule in rules
        ]

        class FakeDatabase:
            def schema(self):
                return "define\n" + "\n".join(
                    "fun " + function_name + "($source: ontology-node) -> { ontology-node }:"
                    for function_name in function_names
                )

        class FakeDriver:
            def __init__(self):
                self.databases = SimpleNamespace(get=lambda _name: FakeDatabase())

        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)

        with patch.object(repository, "driver_imports", return_value=((object, object, object, object, SimpleNamespace()), None)), \
                patch.object(repository, "open_driver", return_value=FakeDriver()), \
                patch.object(repository, "ensure_database"), \
                patch.object(repository, "close_driver"):
            result = repository.probe_typedb_native_rule_functions(rules)

        self.assertEqual("ok", result["status"])
        self.assertEqual(result["verifiedRuleCount"], result["probedCount"])
        self.assertEqual("all-root-functions", result["probeMode"])
        self.assertEqual([rule.rule_id for rule in rules], result["verifiedRuleIds"])
        self.assertGreaterEqual(result["verifiedRuleCount"], 1)


if __name__ == "__main__":
    unittest.main()
