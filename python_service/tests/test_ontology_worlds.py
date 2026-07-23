import copy
import unittest

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_scopes import apply_scoped_abox_identity
from digital_twin.domain.ontology_worlds import (
    market_world,
    portfolio_world,
    portfolio_world_id,
    world_scope_suffix,
    world_from_snapshot,
)
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
    typedb_native_function_call_query,
    typedb_native_function_definition,
    typedb_native_rule_function_name,
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

    def test_full_market_world_setting_keeps_the_market_key(self):
        snapshot = AccountSnapshot(
            "account-a",
            "Account A",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            PortfolioSummary(total=0, invested=0, cash=0, markets=[], sectors=[], concentration=0),
        )
        snapshot.metadata = {"marketWorldId": "market:shared:kr"}

        world = world_from_snapshot(snapshot, {"ontologyTenantId": "tenant-a"})

        self.assertEqual("portfolio:tenant-a:account-a", world.world_id)
        self.assertEqual("kr", world.market_id)

    def test_scoped_abox_identity_isolated_by_world(self):
        first = sample_graph()
        second = copy.deepcopy(first)

        first_identity = apply_scoped_abox_identity(
            first,
            "account-a",
            world_id="portfolio:tenant-a:account-a",
            tenant_id="tenant-a",
            world_type="portfolio",
        )
        second_identity = apply_scoped_abox_identity(
            second,
            "account-b",
            world_id="portfolio:tenant-a:account-b",
            tenant_id="tenant-a",
            world_type="portfolio",
        )

        self.assertNotEqual(first_identity["manifestId"], second_identity["manifestId"])
        self.assertNotEqual(
            first.entities[0].properties["aboxScopeId"],
            second.entities[0].properties["aboxScopeId"],
        )
        self.assertTrue(all(item.properties["worldId"] == "portfolio:tenant-a:account-a" for item in first.entities))
        self.assertTrue(all(item.properties["worldId"] == "portfolio:tenant-a:account-b" for item in second.entities))

    def test_market_world_excludes_account_facts_and_merges_symbol_slices(self):
        world = market_world("kr")
        first = build_market_world_graph(sample_graph("005930"), world)
        second = build_market_world_graph(sample_graph("000660"), world)
        merged = merge_market_world_graph(first, second)

        entity_ids = {item.entity_id for item in merged.entities}
        self.assertIn("stock:005930", entity_ids)
        self.assertIn("stock:000660", entity_ids)
        self.assertFalse(any(item.kind in {"account", "portfolio", "position"} for item in merged.entities))
        samsung = next(item for item in merged.entities if item.entity_id == "stock:005930")
        self.assertNotIn("averagePrice", samsung.properties)
        self.assertNotIn("quantity", samsung.properties)
        self.assertEqual(world.world_id, samsung.properties["worldId"])
        self.assertTrue(all("position:" not in relation.source and "position:" not in relation.target for relation in merged.relations))

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

    def test_market_world_scope_manifest_keeps_prior_scopes_and_retires_tracked_stale_symbols(self):
        world = market_world("kr")
        old = build_market_world_graph(sample_graph("005930"), world, observed_at="2026-07-01T00:00:00Z")
        old_scoped = apply_scoped_abox_identity(
            old,
            world.world_id,
            world_id=world.world_id,
            tenant_id=world.tenant_id,
            world_type=world.world_type,
            world_account_id="",
        )
        fresh = build_market_world_graph(sample_graph("000660"), world, observed_at="2026-07-05T00:00:00Z")
        fresh_scoped = apply_scoped_abox_identity(
            fresh,
            world.world_id,
            world_id=world.world_id,
            tenant_id=world.tenant_id,
            world_type=world.world_type,
            world_account_id="",
        )

        retained = merge_market_world_scope_manifest(
            {
                "scopePlan": old_scoped["scopePlan"],
                "marketScopeObservedAt": {
                    item["scopeId"]: "2026-07-01T00:00:00Z"
                    for item in old_scoped["scopePlan"]
                },
            },
            fresh_scoped["scopePlan"],
            observed_at="2026-07-05T00:00:00Z",
            retention_hours=0,
        )
        retained_scope_ids = {item["scopeId"] for item in retained["scopePlan"]}
        self.assertTrue(any("005930" in item for item in retained_scope_ids))
        self.assertTrue(any("000660" in item for item in retained_scope_ids))

        pruned = merge_market_world_scope_manifest(
            {
                "scopePlan": old_scoped["scopePlan"],
                "marketScopeObservedAt": {
                    item["scopeId"]: "2026-07-01T00:00:00Z"
                    for item in old_scoped["scopePlan"]
                },
            },
            fresh_scoped["scopePlan"],
            observed_at="2026-07-05T00:00:00Z",
            retention_hours=48,
        )
        pruned_scope_ids = {item["scopeId"] for item in pruned["scopePlan"]}
        self.assertFalse(any("005930" in item for item in pruned_scope_ids))
        self.assertTrue(any("000660" in item for item in pruned_scope_ids))
        self.assertTrue(pruned["retiredScopeIds"])

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

    def test_market_world_rebuilds_scope_identity_from_a_portfolio_projection(self):
        source = sample_graph("005930", source_observed_at="2026-07-01T00:00:00Z")
        portfolio = portfolio_world("account-a", "tenant-a", "kr")
        apply_scoped_abox_identity(
            source,
            "account-a",
            world_id=portfolio.world_id,
            tenant_id=portfolio.tenant_id,
            world_type=portfolio.world_type,
        )
        shared = market_world("kr", "tenant-a")
        market = build_market_world_graph(source, shared, observed_at="2026-07-01T00:00:00Z")

        self.assertTrue(all("aboxScopeId" not in item.properties for item in market.entities))
        self.assertTrue(all("scopeGenerationId" not in item.properties for item in market.entities))
        market_scoped = apply_scoped_abox_identity(
            market,
            shared.world_id,
            world_id=shared.world_id,
            tenant_id=shared.tenant_id,
            world_type=shared.world_type,
            world_account_id="",
        )
        expected_suffix = ":world:" + world_scope_suffix(shared.world_id)
        self.assertTrue(all(item["scopeId"].endswith(expected_suffix) for item in market_scoped["scopePlan"]))

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

    def test_typedb_world_identity_isolates_storage_and_reuses_parameterized_rule_namespace(self):
        first_world = "portfolio:tenant-a:account-a"
        second_world = "portfolio:tenant-a:account-b"
        row = {"ontologyBox": "ABox", "snapshotId": "scope:test"}

        self.assertNotEqual(
            ontology_storage_id({**row, "worldId": first_world}, "stock:005930", "node"),
            ontology_storage_id({**row, "worldId": second_world}, "stock:005930", "node"),
        )
        self.assertEqual(
            typedb_native_rule_function_name("graph.holding.loss_guard.v1", first_world),
            typedb_native_rule_function_name("graph.holding.loss_guard.v1", second_world),
        )
        clause = typedb_active_worldview_manifest_clause(world_id=first_world)
        self.assertIn('has ontology-world-id "portfolio:tenant-a:account-a"', clause)
        self.assertIn('has ontology-world-id "portfolio:tenant-a:account-a"', inference_generation_delete_queries("generation:1", first_world)[0])

    def test_parameterized_native_rule_function_binds_the_requested_world_at_call_time(self):
        rule = next(item for item in default_graph_inference_rules() if item.rule_id == "graph.loss_guard.breakdown.v1")
        world = "portfolio:tenant-a:account-a"

        definition = typedb_native_function_definition(rule.to_dict(), world)
        call = typedb_native_function_call_query(rule.to_dict(), ["005930"], world)

        self.assertIn("$ruleWorldId: string", definition["body"])
        self.assertIn("== $ruleWorldId", definition["body"])
        self.assertNotIn(world, definition["body"])
        self.assertIn('let $ruleWorldId = "portfolio:tenant-a:account-a";', call["query"])
        self.assertIn("($candidate, $activeManifestPointer, $ruleWorldId)", call["query"])

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
                    "materialFingerprint": worldview.get("materialFingerprint"),
                    "aboxSnapshotId": worldview.get("aboxSnapshotId"),
                    "worldviewManifestId": worldview.get("worldviewManifestId"),
                }
            return {"status": "empty", "worldId": world_id}

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
            merged = merge_market_world_graph(existing, graph) if existing else copy.deepcopy(graph)
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

    def test_record_snapshot_projects_shared_market_from_native_fact_surface(self):
        repository = self.FakeRepository()
        recorder = PortfolioOntologyProjectionRecorder(
            repository,
            settings={"ontologyTenantId": "tenant-a", "ontologyMarketWorldId": "kr"},
        )
        captured_market_inputs = []
        original_project_market_world = recorder.project_market_world

        def capture_market_input(portfolio_graph, shared_world):
            captured_market_inputs.append(copy.deepcopy(portfolio_graph))
            return original_project_market_world(portfolio_graph, shared_world)

        recorder.project_market_world = capture_market_input
        recorder.record_snapshot(self.snapshot("account-a", "005930", "Samsung"))

        self.assertEqual(1, len(captured_market_inputs))
        market_input = captured_market_inputs[0]
        self.assertEqual(
            "abox-facts-only-typedb-native-rules",
            market_input.worldview["runtimeProjectionMode"],
        )
        self.assertFalse(market_input.beliefs)
        self.assertFalse(market_input.opinions)
        self.assertFalse(market_input.reasoning_cards)

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

    def test_market_world_reuses_scopes_when_only_source_clock_advances(self):
        repository = self.FakeRepository()
        recorder = PortfolioOntologyProjectionRecorder(
            repository,
            settings={"ontologyTenantId": "tenant-a", "ontologyMarketWorldId": "kr"},
        )
        shared_world = market_world("kr", "tenant-a")
        first_graph = sample_graph("005930", source_observed_at="2026-07-01T00:00:00Z")
        first_graph.worldview["asOf"] = "2026-07-01T00:00:00Z"
        second_graph = sample_graph("005930", source_observed_at="2026-07-01T00:10:00Z")
        second_graph.worldview["asOf"] = "2026-07-01T00:10:00Z"

        first = recorder.project_market_world(first_graph, shared_world)
        second = recorder.project_market_world(second_graph, shared_world)

        self.assertEqual("ok", first["status"])
        self.assertEqual("unchanged-material-facts", second["status"])
        self.assertFalse(second["saved"])
        self.assertEqual(1, len(repository.activations))
        self.assertTrue(second["reusedIncomingScopeIds"])
        self.assertEqual([], second["changedIncomingScopeIds"])
        self.assertEqual(1, len(repository.observation_metadata_refreshes))
        refreshed = repository.observation_metadata_refreshes[0]["marketScopeObservedAt"]
        self.assertTrue(refreshed)
        self.assertTrue(all(value == "2026-07-01T00:10:00Z" for value in refreshed.values()))

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
