import hashlib
import json
import re
import tempfile
import time
import unittest
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, current_thread
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, call, patch

from digital_twin import service_manager
from digital_twin.domain.ontology_rulebox_catalog import (
    default_graph_inference_rules,
    governed_graph_inference_rules,
)
from digital_twin.domain.ontology_rulebox_contracts import (
    GraphInferenceRule,
    GraphRuleCondition,
    GraphRuleDerivation,
)
from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_current_state import (
    CURRENT_STATE_ABOX_PERSISTENCE_MODE,
    LEGACY_CURRENT_STATE_ABOX_PERSISTENCE_MODE,
    copy_on_write_generation_id,
    current_state_slot_id,
)
from digital_twin.domain.ontology_fact_slots import build_fact_slot_projection_plan
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
    apply_scoped_abox_repair_epochs,
    apply_scoped_abox_identity,
    bounded_fact_scope_id,
    merge_target_scoped_abox_manifest,
    scope_requires_v8_bounded_slot,
    select_target_scoped_manifest_patch,
)
from digital_twin.domain.ontology_native_rule_planning import (
    merge_native_rule_planner_topology,
    native_rule_planner_topology,
)
from digital_twin.domain.ontology_schema import default_tbox_metadata
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position, utc_now_iso
from digital_twin.domain.ontology_worlds import market_world
from digital_twin.domain.repositories import (
    ONTOLOGY_GRAPH_REPOSITORY_CONTRACT,
    ontology_graph_repository_contract_errors,
)
from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
from digital_twin.infrastructure.ontology_projection import (
    PortfolioOntologyProjectionRecorder,
    SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE,
    SharedMarketWorldProjectionCoordinator,
    migrate_typedb_rule_catalog,
    rulebox_catalog_requires_bootstrap_repair,
)
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules, rulebox_rules_to_payload
from digital_twin.infrastructure.graph_store_lifecycle import ontology_seed_graph
from digital_twin.infrastructure.typedb_ontology import (
    NullTypeDBOntologyGraphRepository,
    TypeDBOperationTimeout,
    TypeDBOntologyGraphRepository,
    TYPEDB_NATIVE_REASONING_PROFILE_VERSION,
    TYPEDB_NATIVE_RULE_ENGINE_VERSION,
    TYPEDB_PROJECTION_COORDINATOR_WORLD_ID,
    TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES,
    TYPEDB_PROMOTED_TEXT_ATTRIBUTES,
    node_boxes,
    native_rule_evidence_read_index_from_rows,
    native_rule_manifest_index_required,
    merge_native_rule_evidence_read_index,
    normalize_native_rule_evidence_read_index,
    ontology_storage_id,
    ontology_row_content_fingerprint,
    relation_row_id,
    typedb_literal,
    typedb_literal_for_attribute,
    typeql_has,
    typedb_repository_from_settings,
    typedb_inferencebox_graph,
    typedb_native_any_group_check_query,
    typedb_native_indexed_evidence_match_query,
    typedb_native_match_query,
    typedb_native_rule_runtime_query_plan,
    typedb_native_rule_execution_plan,
    typedb_native_rule_target_work_plan,
    typedb_native_rule_adaptive_target_parallelism_by_rule_id,
    typedb_native_rule_evidence_read_index_for_execution,
    typedb_native_rule_evidence_read_allows_active_membership_recovery,
    typedb_native_rule_planner_topology_for_execution,
    materialize_typedb_native_matches,
    typedb_projection_preflight_graph_for_execution,
    coordinated_typedb_projection_write,
    typedb_native_rule_profile,
    typedb_native_reasoning_profile,
    typedb_scoped_manifest_member_clause,
    typedb_error_code,
    inference_generation_marker_row,
)


def executable_catalog_rule(rule_id: str) -> GraphInferenceRule:
    """Opt a catalog rule into execution for an isolated planner test."""

    rule = next(item for item in default_graph_inference_rules() if item.rule_id == rule_id)
    return GraphInferenceRule.from_dict({**rule.to_dict(), "enabled": True})


def governed_catalog_rule(rule_id: str) -> GraphInferenceRule:
    """Return the governed model-input contract for compiler-focused tests."""

    rule = next(item for item in governed_graph_inference_rules() if item.rule_id == rule_id)
    return GraphInferenceRule.from_dict({**rule.to_dict(), "enabled": True})


class TypeDBOntologyRepositoryTests(unittest.TestCase):
    def _assert_verified_projection_graph_replaces_duplicate_matched_evidence_read(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        snapshot_id = "abox-current:scope-market:a"
        common = {
            "ontologyBox": "ABox",
            "worldId": "portfolio:local:main",
            "scopeId": "scope:market:005930",
            "scopeType": "market",
            "snapshotId": snapshot_id,
            "aboxSnapshotId": snapshot_id,
        }
        graph = PortfolioOntology(
            "projection-evidence-reuse",
            entities=[
                OntologyEntity(
                    "stock:005930",
                    "삼성전자",
                    "stock",
                    {**common, "symbol": "005930", "tboxClass": "Stock"},
                ),
                OntologyEntity(
                    "price:005930",
                    "삼성전자 가격",
                    "price-observation",
                    {**common, "symbol": "005930", "tboxClass": "PriceObservation"},
                ),
            ],
            relations=[
                OntologyRelation(
                    "stock:005930",
                    "price:005930",
                    "HAS_PRICE",
                    properties={**common, "symbol": "005930"},
                ),
            ],
        )
        node_rows, relation_rows = repository.graph_persistence_rows(graph)
        evidence_index = native_rule_evidence_read_index_from_rows(
            node_rows,
            relation_rows,
        )
        rule = SimpleNamespace(
            rule_id="graph.test.price.v1",
            conditions=[SimpleNamespace(
                kind="relation",
                relation_type="HAS_PRICE",
            )],
        )
        native_match = {
            "matches": [{
                "sourceId": "stock:005930",
                "sourceLabel": "삼성전자",
                "ruleId": rule.rule_id,
            }],
        }

        reused = repository.projection_graph_for_native_matches(
            graph,
            native_match,
            [rule],
            evidence_read_index={
                "status": "verified",
                "source": "active-manifest",
                "index": evidence_index,
            },
        )

        self.assertEqual("ok", reused["status"])
        reused_graph = reused["graph"]
        self.assertEqual(
            "projection-verified-in-memory",
            reused_graph.worldview["nativeEvidenceRead"]["mode"],
        )
        self.assertTrue(
            str(reused_graph.relations[0].properties.get("_relationId") or "")
        )

        broken_index = deepcopy(evidence_index)
        broken_index["relationStorageIdsBySymbolAndType"]["005930"][
            "HAS_PRICE"
        ] = ["ontology-storage:missing"]
        rejected = repository.projection_graph_for_native_matches(
            graph,
            native_match,
            [rule],
            evidence_read_index={
                "status": "verified",
                "source": "active-manifest",
                "index": broken_index,
            },
        )
        self.assertEqual("incomplete", rejected["status"])
        self.assertIn(
            "ontology-storage:missing",
            rejected["missingRelationStorageIds"],
        )

    def test_seed_write_does_not_claim_projection_coordinator_when_schema_bootstrap_fails(self):
        class SeedRepository:
            address = "127.0.0.1:1729"

            def projection_coordinator_write_enforced(self):
                return True

            def sync_base_schema_contract(self):
                return {"configured": True, "saved": False, "reason": "schema unavailable"}

            @contextmanager
            def projection_coordinator_write_scope(self, _owner, _world_id):
                raise AssertionError("coordinator lease must not be claimed before schema bootstrap")
                yield {}

            @coordinated_typedb_projection_write(
                "ontology-seed",
                bootstrap_schema=True,
            )
            def seed(self):
                raise AssertionError("seed body must not run when schema bootstrap fails")

        result = SeedRepository().seed()

        self.assertEqual("schema-bootstrap-failed", result["status"])
        self.assertEqual("schema unavailable", result["reason"])

    def test_target_work_plan_preemptively_shards_only_timeout_prone_rules(self):
        slow_rule_id = "graph.timeout-prone.v1"
        fast_rule_id = "graph.normal.v1"
        adaptive_profile = {
            "status": "active",
            "enabled": True,
            "rules": [{
                "ruleId": slow_rule_id,
                "preemptiveTargetSharding": True,
                "targetParallelism": 2,
            }],
        }
        work_plan = typedb_native_rule_target_work_plan(
            [
                {"ruleId": slow_rule_id, "candidateSymbols": ["005930", "000660", "035420", "035720"]},
                {"ruleId": fast_rule_id, "candidateSymbols": ["005930", "000660", "035420", "035720"]},
            ],
            target_parallelism=1,
            adaptive_target_parallelism_by_rule_id=(
                typedb_native_rule_adaptive_target_parallelism_by_rule_id(adaptive_profile)
            ),
        )

        slow_work = [
            item for item in work_plan["workItems"]
            if item["ruleId"] == slow_rule_id
        ]
        fast_work = [
            item for item in work_plan["workItems"]
            if item["ruleId"] == fast_rule_id
        ]
        self.assertTrue(work_plan["targetWorkAdaptiveShardingUsed"])
        self.assertEqual([slow_rule_id], work_plan["targetWorkAdaptiveShardedRuleIds"])
        self.assertEqual(2, len(slow_work))
        self.assertTrue(all(item["targetWorkAdaptiveShardingUsed"] for item in slow_work))
        self.assertEqual(1, len(fast_work))
        self.assertFalse(fast_work[0]["targetWorkAdaptiveShardingUsed"])
        self.assertEqual(
            ["000660", "005930", "035420", "035720"],
            sorted({symbol for item in slow_work for symbol in item["candidateSymbols"]}),
        )

    def test_promoted_schema_migration_adds_only_missing_company_attributes(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        queries = []

        class FakeTransaction:
            def query(self, query):
                queries.append(query)
                return SimpleNamespace(resolve=lambda: None)

            def commit(self):
                queries.append("commit")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class FakeDriver:
            def transaction(self, *_args, **_kwargs):
                return FakeTransaction()

        existing = (
            "define\n"
            "attribute ontology-current-price, value double;\n"
            "entity ontology-node @abstract, owns ontology-current-price;\n"
        )
        imported = ((object, object, object, object, SimpleNamespace(SCHEMA="schema")), None)

        repository.migrate_promoted_schema(FakeDriver(), imported, existing)

        self.assertIn("attribute ontology-company-revenue, value double;", queries[0])
        self.assertIn("owns ontology-company-revenue", queries[0])
        self.assertNotIn("attribute ontology-current-price, value double;", queries[0])
        self.assertEqual("commit", queries[1])

    def test_fresh_candidate_bootstraps_without_schema_inspection(self):
        repository = TypeDBOntologyGraphRepository(
            "typedb-fresh-candidate.test:1729",
            database="fresh_candidate_bootstrap_test",
            fresh_candidate_rebuild=True,
        )
        repository.invalidate_process_base_schema_readiness()
        repository._database_created_in_process = True
        imported = (object, object, object, object, SimpleNamespace(SCHEMA="schema"))

        with patch.object(
            repository,
            "typedb_schema_text",
            side_effect=AssertionError("fresh candidate must not inspect schema"),
        ) as inspect_schema, patch.object(
            repository,
            "synchronize_base_schema_batches",
            return_value={"queryCount": 1},
        ) as synchronize, patch.object(
            repository,
            "mark_process_base_schema_ready",
        ) as mark_ready:
            repository.ensure_schema(object(), imported)

        inspect_schema.assert_not_called()
        synchronize.assert_called_once()
        self.assertEqual("", synchronize.call_args.args[2])
        self.assertEqual(16, synchronize.call_args.kwargs["batch_size"])
        self.assertEqual(60.0, synchronize.call_args.kwargs["operation_timeout_seconds"])
        mark_ready.assert_called_once()
        repository.invalidate_process_base_schema_readiness()

        resumed_repository = TypeDBOntologyGraphRepository(
            "typedb-fresh-candidate.test:1729",
            database="partial_candidate_bootstrap_test",
            fresh_candidate_rebuild=True,
        )
        resumed_repository.invalidate_process_base_schema_readiness()
        resumed_repository._database_created_in_process = False
        partial_schema = "define\nattribute already-persisted, value string;\n"

        with patch.object(
            resumed_repository,
            "typedb_schema_text",
            return_value=partial_schema,
        ) as inspect_partial_schema, patch.object(
            resumed_repository,
            "synchronize_base_schema_batches",
            return_value={"queryCount": 1},
        ) as resume_synchronize, patch.object(
            resumed_repository,
            "mark_process_base_schema_ready",
        ) as mark_resumed_ready:
            resumed_repository.ensure_schema(object(), imported)

        inspect_partial_schema.assert_called_once()
        resume_synchronize.assert_called_once()
        self.assertEqual(partial_schema, resume_synchronize.call_args.args[2])
        self.assertEqual(16, resume_synchronize.call_args.kwargs["batch_size"])
        self.assertEqual(
            60.0,
            resume_synchronize.call_args.kwargs["operation_timeout_seconds"],
        )
        mark_resumed_ready.assert_called_once()
        resumed_repository.invalidate_process_base_schema_readiness()

        manifest = resumed_repository.seed_static_manifest_metadata(
            PortfolioOntology("release-preflight-test"),
            [],
            default_tbox_metadata(),
        )
        self.assertEqual(
            default_tbox_metadata()["fingerprint"],
            manifest["tboxFingerprint"],
        )

    def test_fresh_schema_batches_use_the_configured_server_transaction_deadline(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        transaction = MagicMock()
        transaction.__enter__.return_value = transaction
        transaction.__exit__.return_value = False
        driver = MagicMock()
        driver.transaction.return_value = transaction
        imported = ((None, None, None, None, SimpleNamespace(SCHEMA="schema")), None)

        with patch.object(repository, "base_schema_bootstrap_plan", return_value=[{
            "phase": "attributes",
            "query": "define attribute test-value, value string;",
            "definitionCount": 1,
        }]), patch.object(repository, "schema_transaction", wraps=repository.schema_transaction) as schema_transaction:
            result = repository.synchronize_base_schema_batches(
                driver,
                imported,
                batch_size=512,
                operation_timeout_seconds=900,
            )

        schema_transaction.assert_called_once_with(
            driver,
            "schema",
            timeout_seconds=900.0,
        )
        options = driver.transaction.call_args.kwargs["options"]
        self.assertEqual(900000, options.transaction_timeout_millis)
        self.assertEqual(900000, options.schema_lock_acquire_timeout_millis)
        self.assertEqual(1, result["queryCount"])

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
        self.assertIn("symbol:005930:state", first_generations)
        self.assertIn("symbol:005930:market", first_generations)
        self.assertIn("symbol:MSTR:state", first_generations)
        self.assertIn("macro:fx", first_generations)

        graph.entities[1].properties["currentPrice"] = 71000
        second = apply_scoped_abox_identity(graph)
        second_generations = dict(second["scopeGenerationIds"])

        self.assertNotEqual(first_generations["symbol:005930:market"], second_generations["symbol:005930:market"])
        self.assertEqual(first_generations["symbol:005930:state"], second_generations["symbol:005930:state"])
        self.assertEqual(first_generations["symbol:MSTR:state"], second_generations["symbol:MSTR:state"])
        self.assertEqual(first_generations["macro:fx"], second_generations["macro:fx"])

        repository = TypeDBOntologyGraphRepository("")
        world_id = "portfolio:local:main"
        market_scope = "symbol:005930:market"
        flow_scope = "symbol:005930:flow"
        active_market_slot = current_state_slot_id(world_id, market_scope, "a")
        active_flow_slot = current_state_slot_id(world_id, flow_scope, "b")
        physical_plan = repository.current_state_physical_scope_plan(
            [
                {
                    "scopeId": market_scope,
                    "generationId": "logical-market-2",
                    "fingerprint": "market-2",
                },
                {
                    "scopeId": flow_scope,
                    "generationId": "logical-flow-1",
                    "fingerprint": "flow-1",
                },
            ],
            {
                "scopeGenerationIds": {
                    market_scope: active_market_slot,
                    flow_scope: active_flow_slot,
                },
            },
            [market_scope],
            world_id,
            persistence_mode=LEGACY_CURRENT_STATE_ABOX_PERSISTENCE_MODE,
        )
        by_scope = {item["scopeId"]: item for item in physical_plan}
        self.assertEqual(
            current_state_slot_id(world_id, market_scope, "b"),
            by_scope[market_scope]["generationId"],
        )
        self.assertEqual(active_flow_slot, by_scope[flow_scope]["generationId"])
        self.assertEqual(
            "logical-market-2",
            by_scope[market_scope]["logicalGenerationId"],
        )
        legacy_physical_graph = repository.current_state_physical_graph(
            graph,
            physical_plan,
        )
        self.assertEqual(
            LEGACY_CURRENT_STATE_ABOX_PERSISTENCE_MODE,
            legacy_physical_graph.worldview["physicalStateMode"],
        )
        self.assertTrue(repository.is_scoped_abox_graph(legacy_physical_graph))
        copy_on_write_plan = repository.current_state_physical_scope_plan(
            [
                {
                    "scopeId": market_scope,
                    "generationId": "logical-market-2",
                    "fingerprint": "market-2",
                },
                {
                    "scopeId": flow_scope,
                    "generationId": "logical-flow-1",
                    "fingerprint": "flow-1",
                },
            ],
            {
                "scopeGenerationIds": {
                    market_scope: active_market_slot,
                    flow_scope: active_flow_slot,
                },
            },
            [market_scope],
            world_id,
            persistence_mode=CURRENT_STATE_ABOX_PERSISTENCE_MODE,
            transition_id="projection-run:123",
        )
        copy_by_scope = {
            item["scopeId"]: item for item in copy_on_write_plan
        }
        self.assertEqual(
            copy_on_write_generation_id(
                world_id,
                market_scope,
                "logical-market-2",
                "projection-run:123",
            ),
            copy_by_scope[market_scope]["generationId"],
        )
        self.assertTrue(
            copy_by_scope[market_scope]["generationId"].startswith(
                "abox-current-cow:"
            )
        )
        self.assertEqual(
            active_flow_slot,
            copy_by_scope[flow_scope]["generationId"],
        )
        self.assertNotEqual(
            copy_by_scope[market_scope]["generationId"],
            copy_on_write_generation_id(
                world_id,
                market_scope,
                "logical-market-2",
                "projection-run:124",
            ),
        )
        legacy_active = {
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "scopeFingerprints": {
                market_scope: "market-1",
                flow_scope: "flow-1",
            },
            "scopeGenerationIds": {
                market_scope: "legacy-market-generation",
                flow_scope: "legacy-flow-generation",
            },
        }
        logical_plan = [
            {"scopeId": market_scope, "generationId": "logical-market-2", "fingerprint": "market-2"},
            {"scopeId": flow_scope, "generationId": "logical-flow-1", "fingerprint": "flow-1"},
        ]
        self.assertEqual(
            [market_scope],
            repository.scoped_abox_changed_scope_ids(
                logical_plan,
                legacy_active,
                current_state_mode=True,
                migration_mode="progressive",
            ),
        )
        self.assertEqual(
            [market_scope, flow_scope],
            repository.scoped_abox_changed_scope_ids(
                logical_plan,
                legacy_active,
                current_state_mode=True,
                migration_mode="full",
            ),
        )

        snapshot_id = "abox-current:scope-a:a"
        unchanged = {
            "id": "stock:005930",
            "kind": "stock",
            "ontologyBox": "ABox",
            "scopeId": market_scope,
            "snapshotId": snapshot_id,
            "propertiesJson": json.dumps({"currentPrice": 100}),
        }
        changed = {
            "id": "price:005930",
            "kind": "price-observation",
            "ontologyBox": "ABox",
            "scopeId": market_scope,
            "snapshotId": snapshot_id,
            "propertiesJson": json.dumps({"currentPrice": 101}),
        }
        relation = {
            "source": "stock:005930",
            "target": "price:005930",
            "type": "HAS_PRICE",
            "ontologyBox": "ABox",
            "scopeId": market_scope,
            "snapshotId": snapshot_id,
            "propertiesJson": "{}",
        }
        unchanged_id = ontology_storage_id(unchanged, unchanged["id"], "node")
        changed_id = ontology_storage_id(changed, changed["id"], "node")
        relation_id = ontology_storage_id(
            relation,
            relation_row_id(relation),
            "relation",
        )
        delta = repository.current_state_delta_plan(
            [unchanged, changed],
            [relation],
            {
                "nodes": {
                    unchanged_id: {
                        "storageId": unchanged_id,
                        "scopeId": market_scope,
                        "contentFingerprint": ontology_row_content_fingerprint(
                            unchanged,
                            "node",
                        ),
                    },
                    changed_id: {
                        "storageId": changed_id,
                        "scopeId": market_scope,
                        "contentFingerprint": "old-price",
                    },
                },
                "relations": {
                    relation_id: {
                        "storageId": relation_id,
                        "scopeId": market_scope,
                        "contentFingerprint": ontology_row_content_fingerprint(
                            relation,
                            "relation",
                        ),
                    },
                },
            },
        )
        self.assertEqual(1, len(delta["nodeRowsToInsert"]))
        self.assertEqual("price:005930", delta["nodeRowsToInsert"][0]["id"])
        self.assertEqual(1, len(delta["reusedNodeRows"]))
        self.assertEqual(1, len(delta["relationRowsToInsert"]))
        self.assertIn(relation_id, delta["relationStorageIdsToDelete"])

        schema = repository.schema_query()
        self.assertIn(
            "attribute ontology-content-fingerprint, value string;",
            schema,
        )
        self.assertGreaterEqual(
            schema.count("owns ontology-content-fingerprint"),
            2,
        )

    def test_scoped_abox_manifest_verification_rejects_unexpected_scope_generation(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        with patch.object(repository, "read_rows", return_value=[{
            "scopeId": "symbol:005930:market",
            "generationId": "abox-scope:unexpected",
            "count": 1,
        }]):
            with self.assertRaisesRegex(RuntimeError, "unexpected scope generation"):
                repository.scoped_abox_scope_row_counts_batch([
                    {"scopeId": "symbol:005930:market", "generationId": "abox-scope:market"},
                ], manifest_id="abox-manifest:next", world_id="portfolio:local:default")

    def test_fresh_candidate_mode_remains_for_world_without_manifest(self):
        repository = TypeDBOntologyGraphRepository(
            "typedb-fresh-candidate.test:1729",
            database="fresh_candidate_empty_world_test",
            fresh_candidate_rebuild=True,
        )
        repository.active_abox_metadata = MagicMock(return_value={"status": "not-found"})

        self.assertTrue(
            repository.fresh_candidate_world_bootstrap_required(
                "portfolio:local:default"
            )
        )

    def test_deferred_maintenance_prunes_after_the_realtime_activation_boundary(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        with patch.object(repository, "acquire_scoped_abox_write_lease", return_value={"acquired": True}), patch.object(
            repository,
            "release_scoped_abox_write_lease",
            return_value={"status": "ok"},
        ), patch.object(
            repository,
            "prune_orphan_scoped_abox_candidates",
            return_value={"status": "ok", "removedGenerationIds": []},
        ) as prune_orphans, patch.object(
            repository,
            "prune_inactive_scoped_abox_manifests",
            return_value={"status": "ok", "removedManifestIds": ["abox-manifest:old"]},
        ) as prune_abox, patch.object(repository, "list_ontology_worlds", return_value=[]), patch.object(
            repository,
            "active_abox_metadata",
            return_value={},
        ), patch.object(
            repository,
            "read_inference_generation_records",
            return_value=[{"generationId": "inference:active"}],
        ), patch.object(
            repository,
            "prune_inferencebox_generations",
            return_value={"status": "ok", "deletedGenerationCount": 2},
        ) as prune_inference:
            result = repository.run_deferred_maintenance({
                "aboxDeleteBatchSize": 25,
                "maxInactiveManifests": 20,
                "maxAboxDeleteBatches": 2,
            })

        self.assertEqual("ok", result["status"])
        self.assertEqual("not-requested", result["orphanScopedAbox"]["status"])
        self.assertEqual("ok", result["abox"]["status"])
        self.assertEqual("ok", result["inference"]["status"])
        prune_orphans.assert_not_called()
        self.assertEqual(2, prune_abox.call_args.kwargs["max_delete_batches"])
        self.assertEqual(25, prune_abox.call_args.kwargs["delete_batch_size"])
        self.assertEqual(20, prune_abox.call_args.kwargs["max_manifests"])
        prune_inference.assert_called_once_with("inference:active", keep_count=2)

    def test_scoped_manifest_integrity_audit_reports_only_mismatched_slice(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        scope_plan = [
            {
                "scopeId": "symbol:005930:market",
                "scopeFamily": "market",
                "generationId": "scope:market",
                "entityCount": 2,
                "relationCount": 1,
            },
            {
                "scopeId": "symbol:005930:flow",
                "scopeFamily": "flow",
                "generationId": "scope:flow",
                "entityCount": 3,
                "relationCount": 2,
            },
        ]
        with patch.object(repository, "active_abox_metadata", return_value={
            "status": "ok",
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "worldviewManifestId": "abox-manifest:active",
            "worldId": "portfolio:local:main",
            "worldType": "portfolio",
            "accountId": "main",
            "scopePlan": scope_plan,
        }), patch.object(repository, "scoped_abox_scope_row_counts_batch", return_value={
            "symbol:005930:flow": {"entityCount": 3, "relationCount": 1},
            "symbol:005930:market": {"entityCount": 2, "relationCount": 1},
        }) as counts:
            result = repository.scoped_abox_integrity_audit(
                "portfolio:local:main",
                cursor=0,
                limit=20,
            )

        self.assertEqual("repair-required", result["status"])
        self.assertEqual(2, result["checkedScopeCount"])
        self.assertEqual(1, result["mismatchCount"])
        self.assertEqual("symbol:005930:flow", result["mismatches"][0]["scopeId"])
        self.assertEqual("005930", result["mismatches"][0]["symbol"])
        self.assertTrue(result["readOnly"])
        self.assertFalse(result["automaticFullProjectionUsed"])
        counts.assert_called_once()
        audited_plan = counts.call_args.args[0]
        self.assertEqual(
            {"symbol:005930:flow", "symbol:005930:market"},
            {item["scopeId"] for item in audited_plan},
        )
        self.assertEqual("portfolio:local:main", counts.call_args.kwargs["world_id"])

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

    def test_scoped_abox_write_lease_recovers_only_a_dead_local_owner(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        held = {
            "status": "held",
            "leaseOwner": "scoped-abox:dead",
            "leaseHost": "unit-host",
            "leaseProcessId": 4242,
            "propertiesJson": '{"leaseHost":"unit-host","leaseProcessId":4242}',
        }
        with patch.object(repository, "scoped_abox_write_lease_status", return_value=held), \
                patch("digital_twin.infrastructure.typedb_ontology.socket.gethostname", return_value="unit-host"), \
                patch.object(repository, "local_process_alive", return_value=False), \
                patch.object(repository, "release_scoped_abox_write_lease", return_value={"status": "released"}) as release:
            result = repository.recover_dead_local_scoped_abox_write_lease()

        self.assertEqual("cleared", result["status"])
        self.assertEqual("scoped-abox:dead", result["previousLeaseOwner"])
        release.assert_called_once()

    def test_scoped_abox_write_lease_recovers_a_dead_account_world_owner_by_exact_storage_id(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        world_id = "portfolio:local:default"
        held = {
            "status": "held",
            "worldId": world_id,
            "leaseOwner": "scoped-abox:dead",
            "leaseHost": "unit-host",
            "leaseProcessId": 4242,
            "propertiesJson": '{"leaseHost":"unit-host","leaseProcessId":4242}',
        }
        with patch.object(repository, "scoped_abox_write_lease_status", return_value=held) as status, \
                patch("digital_twin.infrastructure.typedb_ontology.socket.gethostname", return_value="unit-host"), \
                patch.object(repository, "local_process_alive", return_value=False), \
                patch.object(repository, "release_scoped_abox_write_lease", return_value={"status": "released"}) as release:
            result = repository.recover_dead_local_scoped_abox_write_lease(world_id)

        self.assertEqual("cleared", result["status"])
        self.assertEqual(world_id, result["worldId"])
        status.assert_called_once_with(world_id)
        self.assertEqual(world_id, release.call_args.args[0]["worldId"])
        self.assertEqual(
            repository.scoped_abox_write_lease_storage_id(world_id),
            release.call_args.args[0]["storageId"],
        )

    def test_projection_coordinator_recovery_checks_only_the_global_writer_world(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        with patch.object(
            repository,
            "recover_dead_local_scoped_abox_write_lease",
            return_value={"status": "cleared", "worldId": TYPEDB_PROJECTION_COORDINATOR_WORLD_ID},
        ) as recover:
            result = repository.recover_dead_projection_coordinator_lease()

        self.assertEqual("cleared", result["status"])
        recover.assert_called_once_with(
            TYPEDB_PROJECTION_COORDINATOR_WORLD_ID,
            recover_untracked_current_process=True,
        )

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

    def test_typedb_write_transaction_options_cover_write_operation_timeout(self):
        repository = TypeDBOntologyGraphRepository(
            "127.0.0.1:1729",
            write_operation_timeout_seconds=180,
        )

        options = repository.write_transaction_options()

        self.assertIsNotNone(options)
        self.assertEqual(180000, options.transaction_timeout_millis)

    def test_missing_target_index_uses_only_the_explicit_active_membership_recovery(self):
        self._assert_verified_projection_graph_replaces_duplicate_matched_evidence_read()
        allowed = {
            "status": "fallback",
            "source": "typedb-active-abox-membership-recovery",
        }

        self.assertTrue(typedb_native_rule_evidence_read_allows_active_membership_recovery(allowed))
        self.assertTrue(typedb_native_rule_evidence_read_allows_active_membership_recovery({
            "status": "verified",
            "source": "active-manifest",
        }))
        self.assertFalse(typedb_native_rule_evidence_read_allows_active_membership_recovery({
            "status": "fallback",
            "source": "typedb-active-abox-manifest",
        }))

    def test_materiality_any_one_rule_uses_exists_without_rulebox_count(self):
        rule = next(
            item
            for item in default_graph_inference_rules()
            if item.rule_id == "graph.materiality.alert_candidate.v1"
        )
        evidence_index = {
            "status": "verified",
            "index": {
                "sourceIdsBySymbol": {"005930": ["stock:005930"]},
                "sourceStorageIdsBySourceId": {
                    "stock:005930": "ontology-storage:stock-005930",
                },
                "relationStorageIdsBySymbolAndType": {
                    "005930": {
                        "HAS_OBSERVATION": ["ontology-storage:observation-005930"],
                    },
                },
            },
        }

        plan = typedb_native_indexed_evidence_match_query(
            rule.to_dict(),
            ["005930"],
            evidence_index,
            "portfolio:local:default",
        )

        self.assertEqual("ok", plan["status"])
        self.assertTrue(plan["anyConditionsVerified"])
        self.assertIn("ontology-storage:observation-005930", plan["query"])
        self.assertNotIn("$anyConditionToken", plan["query"])
        self.assertNotIn("reduce $anyConditionCount", plan["query"])

    def test_pending_abox_recovery_blocks_empty_targets_when_a_predecessor_exists(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        pending = {
            "status": "pending",
            "candidateAboxSnapshotId": "abox-manifest:candidate",
            "previousAboxSnapshotId": "abox-manifest:previous",
            "targetSymbols": [],
        }
        with patch.object(repository, "pending_abox_activation", return_value=pending), \
                patch.object(repository, "active_abox_metadata", return_value={
                    "status": "ok",
                    "aboxSnapshotId": "abox-manifest:candidate",
                }), \
                patch.object(repository, "inferencebox_snapshot") as inference_snapshot, \
                patch.object(repository, "activate_abox_generation") as restore:
            result = repository.recover_pending_abox_activation("portfolio:local:default")

        self.assertEqual("invalid-empty-target", result["status"])
        self.assertIn("lost its target symbols", result["reason"])
        inference_snapshot.assert_not_called()
        restore.assert_not_called()

    def test_typedb_native_rule_preflight_prunes_only_a_proven_required_relation_mismatch(self):
        rule = executable_catalog_rule("graph.data_quality.action_block.v1")
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

    def test_support_only_native_rule_timeout_preserves_completed_core_with_gap(self):
        repository = TypeDBOntologyGraphRepository(
            "127.0.0.1:1729",
            retry_count=0,
            native_rule_query_timeout_seconds=0.5,
            native_rule_execution_budget_seconds=1,
        )
        rule = next(
            item
            for item in default_graph_inference_rules()
            if item.rule_id == "graph.loss_rebound.trim_moderation.v1"
        )

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
                patch.object(repository, "close_driver"):
            result = repository.match_typedb_native_rules([rule])

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["nativeQueryUsed"])
        self.assertFalse(result["nativeInferenceEvaluationComplete"])
        self.assertTrue(result["coreNativeInferenceEvaluationComplete"])
        self.assertEqual("core-complete-supporting-partial", result["nativeCoverageStatus"])
        self.assertEqual(1, result["supportingRuleFailureCount"])
        self.assertEqual("preserve-core-with-gap", result["skippedRules"][0]["failurePolicy"])

    def test_timed_out_native_rule_recovers_only_after_every_target_shard_succeeds(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)
        rule = default_graph_inference_rules()[0]
        symbols = ["005930", "000660", "035420", "035720"]
        planned = {
            "rule": rule,
            "candidateSymbols": symbols,
            "queryComplexity": 5,
        }
        primary = {
            "status": "partial",
            "readTransactionCount": 1,
            "readQueryCount": 1,
            "failure": {
                "ruleId": rule.rule_id,
                "status": "query-timeout",
                "reason": "TypeDB read query timed out after 10s",
                "candidateSymbols": symbols,
                "elapsedMs": 10000,
                "queryDurationMs": 10000,
            },
        }

        def recovered_entry(shard, *_args):
            shard_symbols = list(shard["candidateSymbols"])
            return {
                "status": "ok",
                "rule": rule,
                "queryPlan": {"queryMode": "typedb-scoped-typeql"},
                "rows": [{"sourceId": "stock:" + symbol} for symbol in shard_symbols],
                "readTransactionCount": 1,
                "readQueryCount": 1,
                "executed": {
                    "ruleId": rule.rule_id,
                    "nativeRuleId": "native:" + rule.rule_id,
                    "indexedEvidenceQueryUsed": False,
                    "rowCount": len(shard_symbols),
                    "candidateSymbols": shard_symbols,
                    "queryCount": 1,
                    "anyConditionQueryCount": 0,
                    "queryDurationMs": 120,
                    "elapsedMs": 120,
                },
            }

        with patch.object(repository, "execute_typedb_native_rule_entry", side_effect=recovered_entry) as execute:
            result = repository.recover_timed_out_native_rule_entry(
                primary,
                planned,
                symbols,
                "",
                False,
                None,
                None,
                time.monotonic() + 5,
                "typedb-scoped-typeql-parallel",
            )

        self.assertEqual("ok", result["status"])
        self.assertEqual(2, execute.call_count)
        self.assertEqual(
            [["000660", "035420"], ["005930", "035720"]],
            [call.args[0]["candidateSymbols"] for call in execute.call_args_list],
        )
        self.assertEqual(set(symbols), set(result["executed"]["candidateSymbols"]))
        self.assertTrue(result["executed"]["timeoutFallbackUsed"])
        self.assertEqual(2, result["executed"]["timeoutFallbackShardCount"])
        self.assertEqual(3, result["readTransactionCount"])
        self.assertEqual(3, result["readQueryCount"])
        self.assertEqual(
            {"stock:" + symbol for symbol in symbols},
            {row["sourceId"] for row in result["rows"]},
        )

    def test_parallel_native_rule_match_uses_serial_timeout_recovery_before_merging(self):
        repository = TypeDBOntologyGraphRepository(
            "127.0.0.1:1729",
            retry_count=0,
            native_rule_parallelism=2,
        )
        symbols = ["005930", "000660"]

        def rule(rule_id):
            return GraphInferenceRule(
                rule_id=rule_id,
                label=rule_id,
                version="v1",
                source_kind="stock",
                conditions=[
                    GraphRuleCondition(
                        "holding-source",
                        "subject_property",
                        "보유 종목입니다.",
                        field="source",
                        value="holding",
                    ),
                ],
                derivations=[
                    GraphRuleDerivation(
                        relation_type="REQUIRES_NEXT_CHECK",
                        target_kind="next-check",
                        target_key="{symbol}:check",
                        target_label="다음 확인",
                        tbox_class="NextCheck",
                    ),
                ],
                action_group="watch",
                action_level="review",
                prompt_hint="시간 초과 복구 연결 검증",
            )

        slow_rule = rule("graph.timeout.recovery.slow.v1")
        fast_rule = rule("graph.timeout.recovery.fast.v1")

        def successful_entry(planned, timeout_fallback=False):
            candidate_symbols = list(planned["candidateSymbols"])
            return {
                "status": "ok",
                "rule": planned["rule"],
                "queryPlan": {},
                "rows": [],
                "readTransactionCount": 1,
                "readQueryCount": 1,
                "executed": {
                    "ruleId": planned["rule"].rule_id,
                    "nativeRuleId": "native:" + planned["rule"].rule_id,
                    "indexedEvidenceQueryUsed": False,
                    "rowCount": 0,
                    "candidateSymbols": candidate_symbols,
                    "queryCount": 1,
                    "anyConditionQueryCount": 0,
                    "queryDurationMs": 10,
                    "elapsedMs": 10,
                    "timeoutFallbackUsed": timeout_fallback,
                    "timeoutFallbackShardCount": 2 if timeout_fallback else 0,
                },
            }

        def execute_entry(planned, *_args):
            if planned["rule"].rule_id == slow_rule.rule_id:
                return {
                    "status": "partial",
                    "readTransactionCount": 1,
                    "readQueryCount": 1,
                    "failure": {
                        "ruleId": slow_rule.rule_id,
                        "status": "query-timeout",
                        "reason": "TypeDB read query timed out after 10s",
                        "candidateSymbols": list(planned["candidateSymbols"]),
                    },
                }
            return successful_entry(planned)

        def recover_entry(_primary, planned, *_args):
            self.assertEqual(slow_rule.rule_id, planned["rule"].rule_id)
            return successful_entry(planned, timeout_fallback=True)

        imported = (object, object, object, object, SimpleNamespace(READ="read"))
        with patch.object(repository, "driver_imports", return_value=(imported, None)), \
                patch.object(repository, "active_abox_rule_context", return_value={
                    "status": "ok",
                    "relationTypesBySymbol": {symbol: [] for symbol in symbols},
                    "sourceIdsBySymbol": {
                        symbol: ["stock:" + symbol]
                        for symbol in symbols
                    },
                }), \
                patch.object(repository, "execute_typedb_native_rule_entry", side_effect=execute_entry), \
                patch.object(repository, "recover_timed_out_native_rule_entry", side_effect=recover_entry) as recover:
            result = repository.match_typedb_native_rules(
                [slow_rule, fast_rule],
                target_symbols=symbols,
                native_rule_parallelism=2,
                stable_abox_write_lease_held=True,
            )

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["parallelRuleExecution"])
        recover.assert_called_once()
        self.assertTrue(result["timeoutFallbackUsed"])
        self.assertEqual(1, result["timeoutFallbackRuleCount"])
        self.assertEqual(2, result["timeoutFallbackShardCount"])

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

        with patch.object(repository, "active_worldview_manifest_pointer_identity_rows", return_value=[]), \
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

    def test_market_scope_observation_refresh_preserves_manifest_generations(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        scope_id = "symbol:005930:market:world:test"
        scope_plan = [{
            "scopeId": scope_id,
            "scopeFamily": "market",
            "generationId": "abox-scope:005930",
            "fingerprint": "fingerprint-005930",
            "observedAt": "2026-07-23T00:10:00Z",
        }]
        active = {
            "status": "ok",
            "worldId": "market:shared:kr",
            "worldType": "market",
            "tenantId": "tenant-a",
            "worldviewManifestId": "abox-manifest:active",
            "aboxSnapshotId": "abox-manifest:active",
            "materialFingerprint": "market-fingerprint",
            "scopeGenerationIds": {scope_id: "abox-scope:005930"},
            "scopeFingerprints": {scope_id: "fingerprint-005930"},
            "scopeFamilyCounts": {"market": 1},
        }
        marker = {
            "id": "worldview-manifest-marker:active",
            "label": "Worldview Manifest abox-manifest:active",
            "worldviewManifestId": "abox-manifest:active",
            "propertiesJson": json.dumps({
                "ontologyBox": "ABox",
                "worldId": "market:shared:kr",
                "tboxClass": "WorldviewManifest",
                "snapshotId": "abox-manifest:active",
                "aboxSnapshotId": "abox-manifest:active",
                "worldviewManifestId": "abox-manifest:active",
                "aboxScopeId": "manifest:abox-manifest:active",
                "aboxScopeType": "manifest",
                "scopeGenerationId": "abox-manifest:active",
                "nativeRuleEvidenceReadIndex": {"status": "ok", "fingerprint": "native-index"},
            }),
        }
        refreshed_active = {
            **active,
            "marketScopeObservedAt": {scope_id: "2026-07-23T00:10:00Z"},
        }

        with patch.object(repository, "acquire_scoped_abox_write_lease", return_value={"acquired": True, "leaseOwner": "refresh"}), \
                patch.object(repository, "release_scoped_abox_write_lease", return_value={"status": "released"}), \
                patch.object(repository, "active_abox_metadata", side_effect=[active, refreshed_active]), \
                patch.object(repository, "worldview_manifest_marker_rows", return_value=[marker]), \
                patch.object(repository, "replace_scoped_manifest_marker_graph", return_value={"saved": True, "status": "ok"}) as replace:
            result = repository.refresh_market_world_observation_metadata(
                "abox-manifest:active",
                scope_plan,
                {scope_id: "2026-07-23T00:10:00Z"},
                world_id="market:shared:kr",
            )

        self.assertEqual("ok", result["status"])
        replacement_graph = replace.call_args.args[0]
        properties = replacement_graph.entities[0].properties
        self.assertEqual({scope_id: "abox-scope:005930"}, properties["scopeGenerationIds"])
        self.assertEqual({scope_id: "2026-07-23T00:10:00Z"}, properties["marketScopeObservedAt"])
        self.assertEqual("native-index", properties["nativeRuleEvidenceReadIndex"]["fingerprint"])

    def test_staged_abox_rule_run_finalizes_only_an_aligned_native_generation(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        lifecycle = []
        inferencebox = {
            "status": "ok",
            "nativeTypeDbReasoningUsed": True,
            "nativeTypeDbReasoningCompleted": True,
            "generationAligned": True,
            "sourceAboxSnapshotId": "abox-manifest:candidate",
            "targetSymbols": ["000660"],
        }

        def prepare(_world_id):
            lifecycle.append("prepare")
            return {
                "status": "activated",
                "candidateAboxSnapshotId": "abox-manifest:candidate",
                "previousAboxSnapshotId": "abox-manifest:previous",
            }

        def run(_payload):
            lifecycle.append("infer")
            return {"status": "ok", "inferenceBox": inferencebox}

        def finalize(active_id, previous_id, world_id):
            lifecycle.append("finalize")
            self.assertEqual("abox-manifest:candidate", active_id)
            self.assertEqual("abox-manifest:previous", previous_id)
            self.assertEqual("premise:shared:global", world_id)
            return {"status": "ok"}

        with patch.object(repository, "acquire_scoped_abox_write_lease", return_value={
                    "acquired": True, "leaseOwner": "test-owner",
                }), \
                patch.object(repository, "release_scoped_abox_write_lease", return_value={"status": "released"}), \
                patch.object(repository, "prepare_pending_abox_activation_for_inference", side_effect=prepare), \
                patch.object(repository, "active_abox_metadata", return_value={
                    "status": "ok", "worldviewManifestId": "abox-manifest:candidate",
                }), \
                patch.object(repository, "_run_rulebox_unlocked", side_effect=run), \
                patch.object(repository, "finalize_abox_generation", side_effect=finalize), \
                patch.object(repository, "activate_abox_generation") as rollback:
            result = repository.run_rulebox_for_staged_abox({
                "worldId": "premise:shared:global",
                "symbols": ["000660"],
                "expectedAboxSnapshotId": "abox-manifest:candidate",
            })

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["stagedAboxInferenceAlignment"]["verified"])
        self.assertEqual(["prepare", "infer", "finalize"], lifecycle)
        rollback.assert_not_called()

    def test_typedb_pending_abox_recovery_resumes_complete_active_generation_on_stale_inference(self):
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
                patch.object(repository, "inferencebox_recovery_metadata", return_value={
                    "status": "ok",
                    "sourceAboxSnapshotId": "abox-material:previous",
                    "targetSymbols": ["000660"],
                    "nativeTypeDbReasoningCompleted": True,
                    "nativeInferenceOutcome": "matched",
                }), \
                patch.object(repository, "inferencebox_snapshot") as full_snapshot, \
                patch.object(repository, "activate_abox_generation") as restore:
            result = repository.recover_pending_abox_activation()

        self.assertEqual("retry-required", result["status"])
        self.assertEqual("resume-active-candidate", result["recoveryMode"])
        self.assertEqual(["000660"], result["targetSymbols"])
        full_snapshot.assert_not_called()
        restore.assert_not_called()

    def test_typedb_pending_abox_recovery_keeps_initial_retry_targets_for_resume(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        pending = {
            "status": "pending",
            "activationStatus": "pending-native-inference",
            "candidateAboxSnapshotId": "abox-material:first",
            "previousAboxSnapshotId": "",
            "targetSymbols": ["000660"],
        }
        with patch.object(repository, "pending_abox_activation", return_value=pending), \
                patch.object(repository, "active_abox_metadata", return_value={
                    "status": "ok", "aboxSnapshotId": "abox-material:first",
                }), \
                patch.object(repository, "inferencebox_recovery_metadata", return_value={
                    "status": "missing",
                    "sourceAboxSnapshotId": "",
                    "targetSymbols": [],
                    "nativeTypeDbReasoningCompleted": False,
                }):
            result = repository.recover_pending_abox_activation()

        self.assertEqual("retry-required", result["status"])
        self.assertEqual("abox-material:first", result["candidateAboxSnapshotId"])
        self.assertEqual(["000660"], result["targetSymbols"])
        self.assertEqual(pending, result["pendingActivation"])

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

    def test_typedb_fresh_inference_generation_skips_predelete(self):
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

            def query(self, query, given_rows=None):
                self.calls.append(("query", query, given_rows))
                return FakePromise()

            def commit(self):
                self.calls.append(("commit", "", None))

        class FakeDriver:
            def __init__(self):
                self.calls = []

            def transaction(self, *_args, **_kwargs):
                return FakeTransaction(self.calls)

        graph = PortfolioOntology("typedb-fresh-inference")
        graph.worldview = {
            "inferenceGenerationId": "inference-generation:fresh",
            "freshInferenceGeneration": True,
            "sourceAboxSnapshotId": "abox:active",
        }
        graph.entities.append(OntologyEntity(
            "inference:fresh",
            "Fresh inference",
            "inference",
            {"ontologyBox": "InferenceBox"},
        ))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729", retry_count=0)
        driver = FakeDriver()

        with patch.object(repository, "open_driver", return_value=driver), \
                patch.object(repository, "close_driver"), \
                patch.object(repository, "ensure_database"), \
                patch.object(repository, "batched_node_insert_queries", return_value=["insert fresh node"]), \
                patch.object(repository, "inferencebox_given_relation_insert_plans", return_value=[]), \
                patch.object(repository, "validate_inference_generation_candidate", return_value={"valid": True}), \
                patch.object(repository, "activate_inference_generation", return_value={"activated": True}):
            result = repository.write_inferencebox_graph(graph)

        queries = [item[1] for item in driver.calls if item[0] == "query"]
        self.assertTrue(result["saved"])
        self.assertTrue(result["writeTiming"]["candidateDeleteSkipped"])
        self.assertFalse(any("delete" in query.lower() for query in queries))

    def test_native_materialization_preserves_rulebox_decision_effect(self):
        graph = PortfolioOntology("typedb-decision-effect")
        stock = OntologyEntity(
            "stock:NVDA",
            "NVIDIA",
            "stock",
            {"ontologyBox": "ABox", "symbol": "NVDA", "source": "watchlist"},
        )
        graph.entities.append(stock)
        rule = GraphInferenceRule(
            rule_id="graph.test.entry.support.v1",
            label="진입 지지",
            version="v1",
            source_kind="watchlist",
            conditions=[],
            derivations=[GraphRuleDerivation(
                relation_type="HAS_INFERRED_SUPPORT",
                target_kind="entry-support",
                target_key="{symbol}:entry-support",
                target_label="{displayName} 진입 지지",
                tbox_class="EntrySupport",
                polarity="support",
                evidence_role="support",
                decision_effect="support",
                decision_stage="ENTRY_REVIEW",
                target_role="watchlist",
                action_policy="ENTRY_ONLY",
                allowed_actions=["BUY", "HOLD", "AVOID"],
                blocked_actions=["ADD", "TRIM", "SELL"],
                candidate_action="BUY",
            )],
            action_group="entry",
            action_level="check",
            prompt_hint="진입 조건을 확인합니다.",
        )

        materialize_typedb_native_matches(
            graph,
            [rule],
            {"matches": [{"ruleId": rule.rule_id, "sourceId": stock.entity_id}]},
        )
        generated = typedb_inferencebox_graph(
            graph,
            generation_id="inference-generation:decision-effect",
            generation_at="2026-07-27T00:00:00Z",
        )

        inferred = next(
            item for item in generated.relations
            if item.relation_type == "HAS_INFERRED_SUPPORT"
        )
        self.assertEqual("support", inferred.properties["decisionEffect"])
        self.assertEqual("BUY", inferred.properties["candidateAction"])

        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        persisted = next(
            item
            for item in repository.rows_for_relations(generated)
            if item["type"] == "HAS_INFERRED_SUPPORT"
        )
        restored = repository.relation_row_from_typeql({
            "id": "relation:decision-effect",
            "sourceId": persisted["source"],
            "targetId": persisted["target"],
            "type": persisted["type"],
            "ruleId": persisted["ruleId"],
            "updatedAt": "2026-07-27T00:00:00Z",
            "weight": persisted["weight"],
            "json": persisted["propertiesJson"],
        }, "InferenceBox")

        self.assertEqual("support", persisted["decisionEffect"])
        self.assertEqual("support", restored["decisionEffect"])

        snapshot = repository.inferencebox_snapshot_from_graph(generated, ["NVDA"])
        visible = next(
            item for item in snapshot["relations"]
            if item["type"] == "HAS_INFERRED_SUPPORT"
        )
        self.assertEqual("support", visible["decisionEffect"])

    def test_primary_quote_failure_remains_block_while_recovery_check_constrains(self):
        bootstrap = rulebox_rules_to_payload(default_graph_inference_rules())
        self.assertFalse(rulebox_catalog_requires_bootstrap_repair(bootstrap))
        stored = next(
            deepcopy(item) for item in bootstrap
            if item["rule_id"] == "graph.data_quality.market_snapshot_failure_block.v1"
        )
        stored["version"] = "v1"
        for derivation in stored["derivations"]:
            derivation["decision_effect"] = "block"

        migration = migrate_typedb_rule_catalog([stored], bootstrap)
        migrated = migration["rules"][0]

        self.assertEqual("v2", migrated["version"])
        self.assertEqual(
            ["block", "constrain"],
            [item["decision_effect"] for item in migrated["derivations"]],
        )

    def test_inferencebox_recovery_metadata_reads_only_the_active_generation_marker(self):
        class MarkerRepository(TypeDBOntologyGraphRepository):
            def __init__(self):
                super().__init__("127.0.0.1:1729")
                self.queries = []

            def read_rows(self, query, columns, **_kwargs):
                self.queries.append(str(query))
                return [{
                    "id": "inference-generation:active",
                    "label": "Active InferenceBox",
                    "kind": "inference-generation",
                    "snapshotId": "inference-generation:active",
                    "updatedAt": "2026-07-24T00:00:00Z",
                    "json": json.dumps({
                        "inferenceGenerationId": "inference-generation:active",
                        "sourceAboxSnapshotId": "abox-manifest:active",
                        "targetSymbols": ["005930"],
                        "nativeInferenceEvaluationComplete": True,
                        "nativeInferenceOutcome": "no-match",
                        "nativeRuleSelectionApplied": True,
                        "nativeRuleSelectionCandidateCount": 1,
                        "nativeRuleSelectionExecutedCount": 2,
                        "nativeRuleSelectionDeferredCount": 3,
                        "nativeRuleSelectionFullRuleCount": 5,
                        "nativeRuleSelectionExecutedRuleIds": ["graph.changed.v1", "graph.prior.v1"],
                        "nativeRuleSelectionDeferredRuleIds": [
                            "graph.deferred-a.v1",
                            "graph.deferred-b.v1",
                            "graph.deferred-c.v1",
                        ],
                        "typedbNativeRuleExecutedCount": 2,
                        "typedbNativeRuleMatchedCount": 1,
                        "typedbNativeRuleMatchedRuleIds": ["graph.changed.v1"],
                        "typedbNativeRuleTimingProfile": {
                            "executedRuleCount": 2,
                            "slowestRules": [{"ruleId": "graph.changed.v1", "elapsedMs": 700}],
                        },
                        "typedbNativeStageTimings": {"nativeRuleQueryMs": 900},
                    }),
                }]

        repository = MarkerRepository()

        metadata = repository.inferencebox_recovery_metadata("portfolio:local:default")

        self.assertEqual("ok", metadata["status"])
        self.assertEqual("inference-generation:active", metadata["inferenceGenerationId"])
        self.assertEqual("abox-manifest:active", metadata["sourceAboxSnapshotId"])
        self.assertEqual(["005930"], metadata["targetSymbols"])
        self.assertTrue(metadata["nativeTypeDbReasoningCompleted"])
        self.assertTrue(metadata["nativeRuleSelectionApplied"])
        self.assertEqual(1, metadata["nativeRuleSelectionCandidateCount"])
        self.assertEqual(2, metadata["nativeRuleSelectionExecutedCount"])
        self.assertEqual(3, metadata["nativeRuleSelectionDeferredCount"])
        self.assertEqual(5, metadata["nativeRuleSelectionFullRuleCount"])
        self.assertEqual(["graph.changed.v1", "graph.prior.v1"], metadata["nativeRuleSelectionExecutedRuleIds"])
        self.assertEqual(["graph.changed.v1"], metadata["typedbNativeRuleMatchedRuleIds"])
        self.assertEqual(2, metadata["typedbNativeRuleTimingProfile"]["executedRuleCount"])
        self.assertEqual({"nativeRuleQueryMs": 900}, metadata["typedbNativeStageTimings"])
        self.assertEqual(1, len(repository.queries))
        self.assertIn('has ontology-kind "inference-generation"', repository.queries[0])
        self.assertIn('has ontology-world-id "portfolio:local:default"', repository.queries[0])

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
        candidate_summary = {
            "status": "ok",
            "entityCount": 2,
            "relationCount": 1,
            "traceCount": 1,
            "candidateMarkerPresent": True,
            "metadata": {"sourceAboxSnapshotId": "abox:active"},
            "readTransactionCount": 1,
            "readQueryCount": 4,
        }
        with patch.object(repository, "inference_generation_candidate_summary", return_value=candidate_summary), patch.object(repository, "active_abox_snapshot_id", return_value="abox:active"):
            valid = repository.validate_inference_generation_candidate(graph, "generation:candidate", 1, 1)
        with patch.object(repository, "inference_generation_candidate_summary", return_value=candidate_summary), patch.object(repository, "active_abox_snapshot_id", return_value="abox:new"):
            invalid = repository.validate_inference_generation_candidate(graph, "generation:candidate", 1, 1)

        self.assertTrue(valid["valid"])
        self.assertTrue(valid["generationAligned"])
        self.assertFalse(invalid["valid"])
        self.assertIn("candidate-source-abox-not-active", invalid["reason"])

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
            "sourceAboxSnapshotId": "abox:test",
        }))
        graph.entities.append(OntologyEntity("hypothesis-calibration:AAPL:risk", "Apple risk calibration", "hypothesis-calibration", {
            "ontologyBox": "ABox",
            "symbol": "AAPL",
            "tboxClass": "HypothesisCalibration",
            "templateId": "hypothesis-template:risk-rule",
            "templateLabel": "Risk rule",
            "calibrationStatus": "usable",
            "outcomeState": "more-contradicted",
            "latestObservedAt": "2026-07-15T00:00:00Z",
            "aboxSnapshotId": "abox:test",
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
        self.assertEqual("ok", snapshot["hypothesisCalibration"]["status"])
        self.assertEqual("hypothesis-template:risk-rule", snapshot["hypothesisCalibration"]["calibrations"][0]["templateId"])

    def test_projection_recorder_defers_before_preparing_abox_when_inference_lease_is_held(self):
        class FakeRepository:
            store_key = "typedb"

            def acquire_scoped_abox_write_lease(self, _manifest_id):
                return {"acquired": False, "status": "held", "leaseOwner": "other-worker"}

            def prepare_pending_abox_activation_for_inference(self):
                raise AssertionError("ABox pointer must not move while another native inference owns the lease")

            def run_rulebox(self, _payload):
                raise AssertionError("Native inference must not start while another writer owns the lease")

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
        result = {
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "aboxSnapshotId": "abox-manifest:candidate",
        }

        PortfolioOntologyProjectionRecorder(FakeRepository()).attach_graph_store_inference_result(result, snapshot)

        self.assertFalse(result["saved"])
        self.assertTrue(result["aboxStaged"])
        self.assertEqual("deferred-inference-write-lease", result["status"])
        self.assertEqual("deferred-inference-write-lease", result["inferenceBox"]["status"])

    def test_projection_recorder_defers_when_pending_activation_recovery_waits_for_coordinator(self):
        class FakeRepository:
            store_key = "typedb"

            def pending_abox_activation(self, **_kwargs):
                return {
                    "status": "pending",
                    "candidateAboxSnapshotId": "abox-manifest:pending",
                    "targetSymbols": ["AAPL"],
                }

            def recover_pending_abox_activation(self, **_kwargs):
                return {
                    "status": "deferred-projection-coordinator",
                    "retryable": True,
                    "recommendedRetryAfterSeconds": 13,
                    "reason": "another TypeDB projection owns the writer boundary",
                    "projectionCoordinator": {"status": "held", "leaseRemainingSeconds": 13},
                }

        snapshot = AccountSnapshot(
            "main", "메인", "toss", "live", "ok", utc_now_iso(),
            PortfolioSummary(total=1000, invested=1000, cash=0, markets=[], sectors=[], concentration=0),
            positions=[Position("AAPL", "Apple", market="US", currency="USD", quantity=1, current_price=100, market_value=100, market_value_krw=140000)],
        )

        result = PortfolioOntologyProjectionRecorder(FakeRepository()).record_snapshot(snapshot)

        self.assertEqual("deferred-projection-coordinator", result["status"])
        self.assertTrue(result["retryable"])
        self.assertEqual(13, result["recommendedRetryAfterSeconds"])
        self.assertEqual("held", result["projectionCoordinator"]["status"])
        self.assertEqual(
            "deferred-projection-coordinator",
            result["pendingAboxActivationRecovery"]["status"],
        )

    def test_projection_recorder_preserves_native_timeout_without_stale_inference_readback(self):
        class FakeRepository:
            store_key = "typedb"

            def __init__(self):
                self.restored_snapshot_ids = []
                self.snapshot_read_called = False

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
                    "status": "error",
                    "reason": "TypeDB native rule execution did not complete for every applicable rule.",
                    "nativeMatchResult": {
                        "status": "partial",
                        "reasonCode": "typedbNativeRuleQueryTimeout",
                        "reason": (
                            "TypeDB native rule execution did not complete for every applicable rule. "
                            "Blocking rule: graph.price.reclaim.thesis_support.v1 / query-timeout / [TSV13]."
                        ),
                        "nativeExecutionMode": "typedb-manifest-evidence-index",
                        "blockingRule": {
                            "ruleId": "graph.price.reclaim.thesis_support.v1",
                            "status": "query-timeout",
                            "candidateSymbols": ["AAPL"],
                        },
                    },
                }

            def inferencebox_snapshot(self, *_args, **_kwargs):
                self.snapshot_read_called = True
                raise AssertionError("a failed native rule must not read stale inference rows")

            def activate_abox_generation(self, snapshot_id):
                self.restored_snapshot_ids.append(snapshot_id)
                return {
                    "status": "ok",
                    "activeAbox": {"status": "ok", "aboxSnapshotId": snapshot_id},
                }

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
        repository = FakeRepository()

        result = PortfolioOntologyProjectionRecorder(repository).record_snapshot(snapshot)

        self.assertFalse(repository.snapshot_read_called)
        self.assertFalse(result["saved"])
        self.assertEqual("inference-failed-rolled-back", result["status"])
        self.assertTrue(result["preservedActiveGeneration"])
        self.assertEqual(["abox:previous"], repository.restored_snapshot_ids)
        self.assertEqual("native-rule-failed", result["inferenceBox"]["status"])
        self.assertEqual("query-timeout", result["nativeRuleFailure"]["status"])
        self.assertEqual("graph.price.reclaim.thesis_support.v1", result["nativeRuleFailure"]["ruleId"])
        self.assertEqual(["AAPL"], result["nativeRuleFailure"]["targetSymbols"])
        self.assertEqual(30, result["recommendedRetryAfterSeconds"])
        self.assertIn("graph.price.reclaim.thesis_support.v1", result["reason"])

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

    def test_active_typedb_serves_preserved_generation_after_seed_repair_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "log": Path(temp) / "typedb.log",
                "_typedbSeedFailure": {
                    "status": "schema-bootstrap-failed",
                    "reason": "schema repair timed out",
                    "preservedActiveGeneration": True,
                    "retryable": True,
                },
            }
            with patch.object(
                service_manager, "ensure_typedb_seeded", return_value=False,
            ), patch.object(
                service_manager, "typedb_driver_ready", return_value=True,
            ):
                self.assertTrue(
                    service_manager.ensure_typedb_startup_seed_contract(spec)
                )

            self.assertTrue(spec["_typedbServingPreservedGeneration"])
            self.assertIn(
                "serving preserved active generation",
                spec["log"].read_text(encoding="utf-8"),
            )

    def test_service_manager_defers_type_db_lease_inventory_without_subprocess(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "log": Path(temp) / "typedb.log",
            }
            with patch.object(service_manager.subprocess, "run") as run:
                self.assertTrue(service_manager.recover_typedb_scoped_write_lease_after_worker_restart(spec))

        run.assert_not_called()

    def test_seed_refresh_selects_only_rulebox_when_tbox_and_language_are_current(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        expected = {
            "TBox": {"entityCount": 2, "relationCount": 3},
            "RuleBox": {"entityCount": 4, "relationCount": 5},
            "LanguageGovernance": {"entityCount": 6, "relationCount": 7},
        }
        preflight = {
            "ready": False,
            "status": "stale",
            "expectedBoxCounts": expected,
            "actualBoxCounts": {
                "TBox": {"entityCount": 2, "relationCount": 3},
                "RuleBox": {"entityCount": 4, "relationCount": 4},
                "LanguageGovernance": {"entityCount": 6, "relationCount": 7},
            },
            "tboxMatches": True,
            "ruleboxMatches": False,
            "languageRegistryMatches": True,
        }

        self.assertEqual(["RuleBox"], repository.seed_static_boxes_requiring_refresh(preflight))

    def test_server_start_lease_recovery_skips_full_schema_read_after_exact_lease_probe(self):
        class LeaseRecoveryRepository(TypeDBOntologyGraphRepository):
            def __init__(self):
                super().__init__("127.0.0.1:1729", retry_count=0)

            def driver_imports(self):
                return ((object, object, object, object, object), None)

            def open_driver(self, _imported, request_timeout_seconds=None):
                del request_timeout_seconds
                return object()

            def ensure_database(self, _driver):
                return None

            def ensure_schema(self, _driver, _imported):
                raise AssertionError("exact lease recovery must not read the complete schema")

            def close_driver(self, _driver):
                return None

            def scoped_abox_write_lease_status(self, _world_id):
                return {
                    "status": "held",
                    "leaseOwner": "dead-worker",
                    "propertiesJson": "{}",
                    "leaseExpiresAtEpoch": 0,
                }

            def delete_scoped_abox_write_lease(self, _driver, _imported, payload):
                self.deleted = payload
                return {"status": "released"}

        repository = LeaseRecoveryRepository()
        result = repository.recover_scoped_abox_write_lease_after_server_start_for_world(
            "portfolio:local:default"
        )

        self.assertEqual("cleared", result["status"])
        self.assertEqual("portfolio:local:default", repository.deleted["worldId"])

    def test_rulebox_snapshot_reads_only_manifest_selected_generation(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        rules = default_graph_inference_rules()[:1]
        graph = rulebox_graph_from_rules(rules)
        entity_rows = repository.entity_rows_from_typeql([
            {
                "id": row["id"],
                "label": row["label"],
                "kind": row["kind"],
                "updatedAt": "2026-07-10T00:00:00Z",
                "json": row["propertiesJson"],
            }
            for row in repository.rows_for_entities(graph)
            if row["ontologyBox"] == "RuleBox"
        ], "RuleBox")
        relation_rows = repository.rows_for_relations(graph)
        generation = "static-rulebox:active"

        with patch.object(repository, "read_seed_static_manifest", return_value={
            "status": "ok",
            "metadata": {"ruleboxSnapshotId": generation},
        }), patch.object(repository, "read_entity_rows", side_effect=[entity_rows, []]) as entities, \
                patch.object(repository, "read_relation_rows", side_effect=[relation_rows, []]) as relations:
            snapshot = repository.rulebox_snapshot()

        self.assertEqual("ok", snapshot["status"])
        self.assertEqual(generation, snapshot["ruleboxSnapshotId"])
        self.assertEqual(["RuleBox"], entities.call_args_list[0].args[0])
        self.assertEqual(generation, entities.call_args_list[0].kwargs["snapshot_id"])
        self.assertEqual(["RuleBoxGovernance"], entities.call_args_list[1].args[0])
        self.assertEqual(["RuleBox"], relations.call_args_list[0].args[0])
        self.assertEqual(generation, relations.call_args_list[0].kwargs["snapshot_id"])
        self.assertEqual(["RuleBoxGovernance"], relations.call_args_list[1].args[0])

    def test_typedb_inferencebox_recovers_one_aligned_generation_when_marker_is_missing(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        def entity(generation_id, source_abox, updated_at):
            return {
                "id": "inference-trace:CPNG:" + generation_id,
                "kind": "inference-trace",
                "symbol": "CPNG",
                "ontologyBox": "InferenceBox",
                "nativeTypeDbReasoned": True,
                "updatedAt": updated_at,
                "propertiesJson": json.dumps({
                    "ontologyBox": "InferenceBox",
                    "symbol": "CPNG",
                    "nativeTypeDbReasoned": True,
                    "inferenceGenerationId": generation_id,
                    "inferenceGenerationAt": updated_at,
                    "sourceAboxSnapshotId": source_abox,
                }),
            }

        def relation(generation_id, source_abox, updated_at):
            return {
                "source": "stock:CPNG",
                "target": "inference-trace:CPNG:" + generation_id,
                "type": "HAS_INFERENCE_TRACE",
                "symbol": "CPNG",
                "ontologyBox": "InferenceBox",
                "nativeTypeDbReasoned": True,
                "updatedAt": updated_at,
                "propertiesJson": json.dumps({
                    "ontologyBox": "InferenceBox",
                    "symbol": "CPNG",
                    "nativeTypeDbReasoned": True,
                    "inferenceGenerationId": generation_id,
                    "inferenceGenerationAt": updated_at,
                    "sourceAboxSnapshotId": source_abox,
                }),
            }

        old_entity = entity("inference-generation:old", "abox-manifest:old", "2026-07-23T00:00:00Z")
        old_relation = relation("inference-generation:old", "abox-manifest:old", "2026-07-23T00:00:00Z")
        active_entity = entity("inference-generation:active", "abox-manifest:active", "2026-07-23T00:01:00Z")
        active_relation = relation("inference-generation:active", "abox-manifest:active", "2026-07-23T00:01:00Z")

        with patch.object(repository, "read_inference_generation_records", return_value=[]), patch.object(repository, "read_entity_rows", return_value=[old_entity, active_entity]), patch.object(repository, "read_relation_rows", return_value=[old_relation, active_relation]), patch.object(repository, "active_abox_metadata", return_value={"status": "ok", "aboxSnapshotId": "abox-manifest:active"}), patch.object(repository, "hypothesis_calibration_snapshot", return_value={"status": "empty", "calibrations": [], "calibrationCount": 0}):
            snapshot = repository.inferencebox_snapshot(symbols=["CPNG"])

        self.assertEqual("ok", snapshot["status"])
        self.assertEqual("inference-generation:active", snapshot["inferenceGenerationId"])
        self.assertEqual("materialized-row-provenance", snapshot["inferenceGenerationIdentitySource"])
        self.assertEqual("abox-manifest:active", snapshot["sourceAboxSnapshotId"])
        self.assertTrue(snapshot["generationAligned"])
        self.assertEqual(1, snapshot["entityCount"])
        self.assertEqual(1, snapshot["relationCount"])
        self.assertEqual(2, snapshot["generationCount"])
        self.assertEqual(["inference-trace:CPNG:inference-generation:active"], [item["id"] for item in snapshot["entities"]])

    def test_typedb_rulebox_defers_when_native_inference_writer_lease_is_held(self):
        repository = TypeDBOntologyGraphRepository(
            "127.0.0.1:1729",
            inference_write_lease_enabled=True,
        )
        with patch.object(repository, "acquire_scoped_abox_write_lease", return_value={
            "acquired": False,
            "status": "held",
            "leaseOwner": "other-worker",
        }), patch.object(repository, "_run_rulebox_unlocked") as run_unlocked:
            result = repository.run_rulebox({"typedbNativeRuleExecutionEnabled": True})

        run_unlocked.assert_not_called()
        self.assertEqual("deferred-inference-write-lease", result["status"])
        self.assertTrue(result["preservedPreviousInference"])
        self.assertEqual("other-worker", result["inferenceWriteLease"]["leaseOwner"])

    def test_typedb_rulebox_empty_result_materializes_aligned_no_match_generation(self):
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
            "executedRuleCount": 1,
            "skippedRuleCount": 0,
            "matchedCount": 0,
            "matches": [],
        }
        captured = {}

        def save_empty_generation(graph):
            captured["graph"] = graph
            return {"configured": True, "saved": True, "status": "ok", "graphStore": "typedb"}

        with patch.object(repository, "has_box_rows", return_value=True), patch.object(repository, "active_abox_metadata", return_value={"status": "ok", "aboxSnapshotId": "abox-snapshot:test"}), patch.object(repository, "rulebox_snapshot", return_value=rule_snapshot), patch.object(repository, "match_typedb_native_rules", return_value=native_match), patch.object(repository, "load_graph_for_native_matches", return_value=PortfolioOntology("empty")), patch.object(repository, "write_inferencebox_graph", side_effect=save_empty_generation) as write_mock, patch.object(repository, "clear_inferencebox") as clear_mock:
            result = repository.run_rulebox({"forceClearInference": True, "allowDestructiveInferenceClear": True})

        write_mock.assert_called_once()
        clear_mock.assert_not_called()
        self.assertEqual("empty", result["status"])
        self.assertTrue(result["nativeTypeDbReasoningCompleted"])
        self.assertEqual("no-match", result["nativeInferenceOutcome"])
        self.assertEqual("abox-snapshot:test", result["sourceAboxSnapshotId"])
        self.assertEqual("no-match", captured["graph"].worldview["nativeInferenceOutcome"])
        self.assertEqual("skipped", result["clearResult"]["status"])

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
