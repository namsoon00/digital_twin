import copy
import unittest

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_scopes import apply_scoped_abox_identity
from digital_twin.domain.ontology_worlds import (
    knowledge_world,
    market_world,
    portfolio_world,
    portfolio_world_id,
    shared_premise_world,
    world_scope_suffix,
    world_from_snapshot,
)
from digital_twin.domain.knowledge_world_projection import build_knowledge_world_graph
from digital_twin.domain.market_world_projection import (
    build_market_world_graph,
    merge_market_world_scope_manifest,
    merge_market_world_graph,
)
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position, utc_now_iso
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.infrastructure.graph_store_rulebox import rulebox_rules_to_payload
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
from digital_twin.infrastructure.typedb_ontology import (
    TypeDBOntologyGraphRepository,
    inference_generation_delete_queries,
    ontology_storage_id,
    typedb_active_worldview_manifest_clause,
)
from digital_twin.infrastructure.web_server import run_ontology_rulebox_payload


def sample_graph(symbol="005930", source_observed_at=""):
    graph = PortfolioOntology("account-a")
    source_clock = {"sourceAsOf": source_observed_at} if source_observed_at else {}
    graph.entities.extend([
        OntologyEntity("account:account-a", "Account A", "account", {"ontologyBox": "ABox", "accountId": "account-a"}),
        OntologyEntity("portfolio:account-a", "Portfolio A", "portfolio", {"ontologyBox": "ABox", "accountId": "account-a"}),
        OntologyEntity("position:account-a:" + symbol, "Position", "position", {
            "ontologyBox": "ABox",
            "symbol": symbol,
            "quantity": 10,
            "averagePrice": 70000,
            "marketValue": 710000,
        }),
        OntologyEntity("stock:" + symbol, "Samsung", "stock", {
            "ontologyBox": "ABox",
            "symbol": symbol,
            "currentPrice": 71000,
            "averagePrice": 70000,
            "quantity": 10,
            **source_clock,
        }),
        OntologyEntity("price:" + symbol, "Current price", "price-metric", {
            "ontologyBox": "ABox",
            "symbol": symbol,
            "value": 71000,
            **source_clock,
        }),
    ])
    graph.relations.extend([
        OntologyRelation("portfolio:account-a", "position:account-a:" + symbol, "HAS_POSITION", properties={"ontologyBox": "ABox"}),
        OntologyRelation("position:account-a:" + symbol, "stock:" + symbol, "REPRESENTS_STOCK", properties={"ontologyBox": "ABox"}),
        OntologyRelation("stock:" + symbol, "price:" + symbol, "HAS_PRICE", properties={"ontologyBox": "ABox"}),
    ])
    graph.evidence.append(OntologyEvidence(
        "evidence:" + symbol,
        "stock:" + symbol,
        "quote",
        "KIS",
        "Current quote",
        {"ontologyBox": "ABox", "symbol": symbol, **source_clock},
    ))
    return graph


class OntologyWorldContractTests(unittest.TestCase):
    def test_portfolio_worlds_are_deterministic_and_distinct(self):
        self.assertEqual("portfolio:tenant-a:account-a", portfolio_world_id("Account A", "Tenant A"))
        self.assertNotEqual(
            portfolio_world_id("account-a", "tenant-a"),
            portfolio_world_id("account-b", "tenant-a"),
        )

    def test_shared_worlds_strip_account_strategy_and_ai_working_properties(self):
        graph = sample_graph("005930")
        stock = next(item for item in graph.entities if item.entity_id == "stock:005930")
        stock.properties.update({
            "strategyMaxPositionWeightPct": 45,
            "strategyLossTolerancePct": -8,
            "targetPositionRole": "core",
            "aiPrompt": "private prompt",
            "rawPayload": {"account": "private"},
        })
        graph.entities.extend([
            OntologyEntity("company:Samsung", "Samsung", "company", {"ontologyBox": "ABox", "tboxClass": "Company"}),
            OntologyEntity("security:005930", "Samsung common", "security", {"ontologyBox": "ABox", "tboxClass": "Security"}),
        ])
        graph.relations.extend([
            OntologyRelation("company:Samsung", "security:005930", "ISSUES", properties={"ontologyBox": "ABox"}),
            OntologyRelation("security:005930", "stock:005930", "REPRESENTS_INSTRUMENT", properties={"ontologyBox": "ABox"}),
        ])

        market_stock = next(
            item for item in build_market_world_graph(graph, market_world("kr")).entities
            if item.entity_id == "stock:005930"
        )
        knowledge_stock = next(
            item for item in build_knowledge_world_graph(graph, knowledge_world("kr")).entities
            if item.entity_id == "stock:005930"
        )

        for properties in [market_stock.properties, knowledge_stock.properties]:
            self.assertNotIn("strategyMaxPositionWeightPct", properties)
            self.assertNotIn("strategyLossTolerancePct", properties)
            self.assertNotIn("targetPositionRole", properties)
            self.assertNotIn("aiPrompt", properties)
            self.assertNotIn("rawPayload", properties)
            self.assertEqual("005930", properties["symbol"])
        self.assertEqual(71000, market_stock.properties["currentPrice"])
        self.assertNotIn("currentPrice", knowledge_stock.properties)

    def test_market_world_prunes_stale_observations_without_erasing_fresh_account_slice(self):
        world = market_world("kr")
        old = build_market_world_graph(
            sample_graph("005930", source_observed_at="2026-07-01T00:00:00Z"),
            world,
            observed_at="2026-07-01T00:00:00Z",
        )
        fresh = build_market_world_graph(
            sample_graph("000660", source_observed_at="2026-07-05T00:00:00Z"),
            world,
            observed_at="2026-07-05T00:00:00Z",
        )

        merged = merge_market_world_graph(
            old,
            fresh,
            retention_hours=48,
            max_symbols=100,
            observed_at="2026-07-05T00:00:00Z",
        )

        entity_ids = {item.entity_id for item in merged.entities}
        self.assertNotIn("stock:005930", entity_ids)
        self.assertIn("stock:000660", entity_ids)
        retention = merged.worldview["marketWorldRetention"]
        self.assertGreater(retention["removedStaleEntityCount"], 0)
        self.assertEqual(48.0, retention["retentionHours"])

    def test_market_world_scope_generation_ignores_projection_clock(self):
        world = market_world("kr")
        source_graph = sample_graph("005930", source_observed_at="2026-07-01T00:00:00Z")
        first = build_market_world_graph(
            source_graph,
            world,
            observed_at="2026-07-01T00:00:00Z",
        )
        second = build_market_world_graph(
            source_graph,
            world,
            observed_at="2026-07-01T00:10:00Z",
        )
        first_scoped = apply_scoped_abox_identity(
            first,
            world.world_id,
            world_id=world.world_id,
            tenant_id=world.tenant_id,
            world_type=world.world_type,
            world_account_id="",
        )
        second_scoped = apply_scoped_abox_identity(
            second,
            world.world_id,
            world_id=world.world_id,
            tenant_id=world.tenant_id,
            world_type=world.world_type,
            world_account_id="",
        )

        self.assertEqual(
            {item["scopeId"]: item["fingerprint"] for item in first_scoped["scopePlan"]},
            {item["scopeId"]: item["fingerprint"] for item in second_scoped["scopePlan"]},
        )
        stock = next(item for item in first.entities if item.entity_id == "stock:005930")
        self.assertEqual("2026-07-01T00:00:00Z", stock.properties["marketObservedAt"])

    def test_market_world_manifest_refreshes_source_clock_without_new_generation(self):
        scope_id = "symbol:005930:market"
        first = merge_market_world_scope_manifest(
            {},
            [{
                "scopeId": scope_id,
                "scopeFamily": "market",
                "fingerprint": "same-facts",
                "generationId": "same-generation",
                "observedAt": "2026-07-01T00:00:00Z",
            }],
            observed_at="2026-07-01T00:00:00Z",
        )
        second = merge_market_world_scope_manifest(
            first,
            [{
                "scopeId": scope_id,
                "scopeFamily": "market",
                "fingerprint": "same-facts",
                "generationId": "same-generation",
                "observedAt": "2026-07-01T00:10:00Z",
            }],
            observed_at="2026-07-01T00:10:00Z",
        )

        self.assertEqual(first["materialFingerprint"], second["materialFingerprint"])
        self.assertEqual([], second["changedIncomingScopeIds"])
        self.assertEqual([scope_id], second["reusedIncomingScopeIds"])
        self.assertEqual([scope_id], second["observationRefreshedScopeIds"])
        self.assertEqual("2026-07-01T00:10:00Z", second["marketScopeObservedAt"][scope_id])

    def test_native_rulebox_api_requires_a_portfolio_world(self):
        missing = run_ontology_rulebox_payload({"clearInference": True})
        market = run_ontology_rulebox_payload({"worldId": "market:shared:kr"})

        self.assertEqual("world-required", missing["status"])
        self.assertEqual("portfolio-world-required", market["status"])


class MultiAccountProjectionTests(unittest.TestCase):
    class FakeRepository:
        store_key = "typedb"

        def __init__(self):
            self.saved_portfolios = []
            self.saved_markets = {}
            self.activations = []
            self.observation_metadata_refreshes = []
            self.rulebox_payloads = []
            self.leases = []
            self.market_load_calls = 0
            self.pending_abox_activation_payload = {"status": "empty"}
            self.pending_abox_recovery_result = {"status": "skipped"}
            self.pending_abox_recovery_calls = []

        def rulebox_snapshot(self):
            rules = rulebox_rules_to_payload(default_graph_inference_rules())
            return {"configured": True, "status": "ok", "ruleCount": len(rules), "rules": rules}

        def active_abox_metadata(self, world_id=""):
            graph = self.saved_markets.get(world_id)
            if graph:
                worldview = dict(graph.worldview or {})
                return {
                    "status": "ok",
                    "worldId": world_id,
                    "scopedAboxManifestVersion": worldview.get("scopedAboxManifestVersion"),
                    "scopePlan": list(worldview.get("scopePlan") or []),
                    "scopeGenerationIds": dict(worldview.get("scopeGenerationIds") or {}),
                    "scopeFingerprints": dict(worldview.get("scopeFingerprints") or {}),
                    "scopeTopologyVersion": worldview.get("scopeTopologyVersion"),
                    "marketScopeObservedAt": dict(worldview.get("marketScopeObservedAt") or {}),
                    "marketScopeObservedAtVersion": worldview.get("marketScopeObservedAtVersion"),
                    "sharedWorldProjectionContractVersion": worldview.get("sharedWorldProjectionContractVersion"),
                    "nativeRulePlannerTopology": dict(worldview.get("nativeRulePlannerTopology") or {}),
                    "materialFingerprint": worldview.get("materialFingerprint"),
                    "aboxSnapshotId": worldview.get("aboxSnapshotId"),
                    "worldviewManifestId": worldview.get("worldviewManifestId"),
                }
            return {"status": "empty", "worldId": world_id}

        def pending_abox_activation(self, world_id=""):
            return dict(self.pending_abox_activation_payload or {})

        def recover_pending_abox_activation(
            self,
            world_id="",
            max_staged_target_symbols=0,
        ):
            self.pending_abox_recovery_calls.append({
                "worldId": world_id,
                "maxStagedTargetSymbols": max_staged_target_symbols,
            })
            return dict(self.pending_abox_recovery_result or {})

        def acquire_scoped_abox_write_lease(self, owner, world_id=""):
            lease = {
                "acquired": True,
                "leaseOwner": owner,
                "worldId": world_id,
            }
            self.leases.append(lease)
            return lease

        def release_scoped_abox_write_lease(self, _lease):
            return {"status": "released"}

        def load_graph_from_typedb(self, _boxes=None, world_id=""):
            self.market_load_calls += 1
            return copy.deepcopy(self.saved_markets.get(world_id, PortfolioOntology(world_id)))

        def save_scoped_abox_graph(self, graph, adopted_write_lease=None):
            world_id = graph.worldview["worldId"]
            existing = self.saved_markets.get(world_id)
            # A shared-world projection contract bump intentionally replaces
            # every legacy scope.  This mirrors TypeDB's new Manifest pointer
            # rather than retaining a privacy-unsafe prior generation.
            merged = (
                merge_market_world_graph(existing, graph)
                if existing and not graph.worldview.get("sharedWorldFullRebuild")
                else copy.deepcopy(graph)
            )
            merged.worldview.update(dict(graph.worldview or {}))
            self.saved_markets[world_id] = merged
            return {"saved": True, "status": "ok", "worldId": graph.worldview["worldId"]}

        def activate_scoped_abox_manifest(self, manifest_id, pending_activation=False, world_id=""):
            self.activations.append((world_id, manifest_id, pending_activation))
            return {"status": "ok", "worldId": world_id}

        def refresh_market_world_observation_metadata(
            self,
            manifest_id,
            scope_plan,
            market_scope_observed_at,
            adopted_write_lease=None,
            world_id="",
        ):
            graph = self.saved_markets.get(world_id)
            if not graph or graph.worldview.get("worldviewManifestId") != manifest_id:
                return {"saved": False, "status": "stale-manifest", "worldId": world_id}
            graph.worldview.update({
                "scopePlan": list(scope_plan),
                "marketScopeObservedAt": dict(market_scope_observed_at),
                "marketScopeObservedAtVersion": "source-item-v1",
            })
            self.observation_metadata_refreshes.append({
                "worldId": world_id,
                "manifestId": manifest_id,
                "scopePlan": list(scope_plan),
                "marketScopeObservedAt": dict(market_scope_observed_at),
            })
            return {"saved": True, "status": "ok", "worldId": world_id}

        def save_graph(self, graph):
            self.saved_portfolios.append(copy.deepcopy(graph))
            return {
                "saved": True,
                "status": "ok",
                "graphStore": "typedb",
                "worldId": graph.worldview.get("worldId"),
            }

        def run_rulebox(self, payload):
            self.rulebox_payloads.append(dict(payload))
            return {"status": "ok", "graphStore": "typedb"}

        def inferencebox_snapshot(self, _symbols=None, limit=80, world_id=""):
            return {
                "status": "ok",
                "graphStore": "typedb",
                "nativeTypeDbReasoningUsed": True,
                "generationAligned": True,
                "sourceAboxSnapshotId": "",
                "worldId": world_id,
                "relations": [],
                "traces": [],
            }

    @staticmethod
    def snapshot(account_id, symbol, name):
        return AccountSnapshot(
            account_id,
            name,
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            PortfolioSummary(
                total=1000000,
                invested=700000,
                cash=300000,
                markets=[],
                sectors=[],
                concentration=0,
            ),
            positions=[Position(
                symbol,
                name,
                market="KR",
                currency="KRW",
                quantity=10,
                sellable_quantity=10,
                current_price=70000,
                average_price=68000,
                market_value=700000,
                market_value_krw=700000,
            )],
        )

    def test_two_accounts_get_isolated_portfolio_worlds_and_one_merged_market_world(self):
        repository = self.FakeRepository()
        recorder = PortfolioOntologyProjectionRecorder(
            repository,
            settings={"ontologyTenantId": "tenant-a", "ontologyMarketWorldId": "kr"},
        )

        first = recorder.record_snapshot(self.snapshot("account-a", "005930", "Samsung"))
        second = recorder.record_snapshot(self.snapshot("account-b", "000660", "SK Hynix"))

        self.assertEqual("portfolio:tenant-a:account-a", first["ontologyWorld"]["worldId"])
        self.assertEqual("portfolio:tenant-a:account-b", second["ontologyWorld"]["worldId"])
        self.assertEqual(
            ["portfolio:tenant-a:account-a", "portfolio:tenant-a:account-b"],
            [item["worldId"] for item in repository.rulebox_payloads],
        )
        self.assertEqual(2, len(repository.saved_portfolios))
        self.assertNotEqual(
            repository.saved_portfolios[0].worldview["worldviewManifestId"],
            repository.saved_portfolios[1].worldview["worldviewManifestId"],
        )
        shared = repository.saved_markets["market:shared:kr"]
        shared_ids = {item.entity_id for item in shared.entities}
        self.assertIn("stock:005930", shared_ids)
        self.assertIn("stock:000660", shared_ids)
        self.assertFalse(any(item.kind in {"account", "portfolio", "position"} for item in shared.entities))
        self.assertTrue(all(world_id == "market:shared:kr" for world_id, _manifest, _pending in repository.activations))
        self.assertEqual(0, repository.market_load_calls)

    def test_market_world_reuses_an_identical_material_generation(self):
        repository = self.FakeRepository()
        recorder = PortfolioOntologyProjectionRecorder(
            repository,
            settings={"ontologyTenantId": "tenant-a", "ontologyMarketWorldId": "kr"},
        )
        snapshot = self.snapshot("account-a", "005930", "Samsung")
        graph = build_portfolio_ontology(
            snapshot.positions,
            snapshot.portfolio,
            portfolio_id=snapshot.account_id,
            include_tbox=False,
            include_presentation=False,
        )
        shared_world = market_world("kr", "tenant-a")

        first = recorder.project_market_world(graph, shared_world)
        second = recorder.project_market_world(graph, shared_world)

        self.assertEqual("ok", first["status"])
        self.assertEqual("unchanged-material-facts", second["status"])
        self.assertFalse(second["saved"])
        self.assertEqual(1, len(repository.activations))

    def test_shared_world_releases_coordinator_when_world_lease_lookup_raises(self):
        class LeaseFailureRepository(self.FakeRepository):
            def __init__(self):
                super().__init__()
                self.coordinator_releases = []

            def acquire_projection_coordinator_lease(self, owner, world_id=""):
                return {
                    "acquired": True,
                    "status": "acquired",
                    "leaseOwner": owner,
                    "leaseToken": "coordinator-token",
                    "worldId": world_id,
                }

            def release_projection_coordinator_lease(self, lease):
                self.coordinator_releases.append(dict(lease))
                return {"status": "released"}

            def acquire_scoped_abox_write_lease(self, owner, world_id=""):
                raise RuntimeError("lease lookup failed")

        repository = LeaseFailureRepository()
        recorder = PortfolioOntologyProjectionRecorder(repository)
        world = shared_premise_world("kr", "tenant-a")

        result = recorder.project_shared_world_update(sample_graph("005930"), world, "premise")

        self.assertEqual("deferred-premise-world-write-lease", result["status"])
        self.assertEqual("released", result["projectionCoordinatorRelease"]["status"])
        self.assertEqual(1, len(repository.coordinator_releases))

    def test_market_world_target_patch_keeps_untargeted_symbol_generation(self):
        repository = self.FakeRepository()
        recorder = PortfolioOntologyProjectionRecorder(
            repository,
            settings={"ontologyTenantId": "tenant-a", "ontologyMarketWorldId": "kr"},
        )
        shared_world = market_world("kr", "tenant-a")

        def graph_with_symbols():
            graph = sample_graph("005930", source_observed_at="2026-07-01T00:00:00Z")
            other = sample_graph("MSTR", source_observed_at="2026-07-01T00:00:00Z")
            graph.entities.extend([
                item for item in other.entities
                if item.entity_id not in {"account:account-a", "portfolio:account-a"}
            ])
            graph.relations.extend(other.relations)
            graph.evidence.extend(other.evidence)
            graph.worldview["asOf"] = "2026-07-01T00:00:00Z"
            return graph

        first_graph = graph_with_symbols()
        first = recorder.project_market_world(first_graph, shared_world)
        self.assertEqual("ok", first["status"])
        first_generations = dict(
            repository.saved_markets[shared_world.world_id].worldview["scopeGenerationIds"]
        )
        samsung_scope = next(
            scope_id for scope_id in first_generations
            if scope_id.startswith("symbol:005930:market:")
        )
        mstr_scope = next(
            scope_id for scope_id in first_generations
            if scope_id.startswith("symbol:MSTR:market:")
        )

        second_graph = graph_with_symbols()
        quote = next(item for item in second_graph.entities if item.entity_id == "price:005930")
        quote.properties["value"] = 72000
        second_graph.worldview["targetScopedManifestPatch"] = {
            "status": "applied",
            "targetSymbols": ["005930"],
        }
        second = recorder.project_market_world(second_graph, shared_world)
        second_generations = dict(
            repository.saved_markets[shared_world.world_id].worldview["scopeGenerationIds"]
        )

        self.assertEqual("ok", second["status"])
        self.assertEqual("applied", second["targetScopedManifestPatch"]["status"])
        self.assertNotEqual(first_generations[samsung_scope], second_generations[samsung_scope])
        self.assertEqual(first_generations[mstr_scope], second_generations[mstr_scope])
        self.assertNotIn(mstr_scope, second["changedIncomingScopeIds"])


if __name__ == "__main__":
    unittest.main()
