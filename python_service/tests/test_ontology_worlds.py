import copy
import unittest

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_scopes import apply_scoped_abox_identity
from digital_twin.domain.ontology_worlds import (
    market_world,
    portfolio_world,
    portfolio_world_id,
    world_from_snapshot,
)
from digital_twin.domain.market_world_projection import (
    build_market_world_graph,
    merge_market_world_graph,
)
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position, utc_now_iso
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


def sample_graph(symbol="005930"):
    graph = PortfolioOntology("account-a")
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
        }),
        OntologyEntity("price:" + symbol, "Current price", "price-metric", {
            "ontologyBox": "ABox",
            "symbol": symbol,
            "value": 71000,
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
        {"ontologyBox": "ABox", "symbol": symbol},
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
            sample_graph("005930"),
            world,
            observed_at="2026-07-01T00:00:00Z",
        )
        fresh = build_market_world_graph(
            sample_graph("000660"),
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
        self.assertIn("($candidate, $ruleWorldId)", call["query"])

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
            self.rulebox_payloads = []
            self.leases = []

        def rulebox_snapshot(self):
            rules = rulebox_rules_to_payload(default_graph_inference_rules())
            return {"configured": True, "status": "ok", "ruleCount": len(rules), "rules": rules}

        def active_abox_metadata(self, world_id=""):
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
            return copy.deepcopy(self.saved_markets.get(world_id, PortfolioOntology(world_id)))

        def save_scoped_abox_graph(self, graph, adopted_write_lease=None):
            self.saved_markets[graph.worldview["worldId"]] = copy.deepcopy(graph)
            return {"saved": True, "status": "ok", "worldId": graph.worldview["worldId"]}

        def activate_scoped_abox_manifest(self, manifest_id, pending_activation=False, world_id=""):
            self.activations.append((world_id, manifest_id, pending_activation))
            return {"status": "ok", "worldId": world_id}

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


if __name__ == "__main__":
    unittest.main()
