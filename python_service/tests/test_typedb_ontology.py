import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin import service_manager
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position, utc_now_iso
from digital_twin.domain.repositories import (
    ONTOLOGY_GRAPH_REPOSITORY_CONTRACT,
    ontology_graph_repository_contract_errors,
)
from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.infrastructure.typedb_ontology import (
    NullTypeDBOntologyGraphRepository,
    TypeDBOperationTimeout,
    TypeDBOntologyGraphRepository,
    relation_row_id,
    typedb_repository_from_settings,
    typedb_inferencebox_graph,
    typedb_native_function_definition,
    typedb_native_match_query,
    typedb_native_reasoning_profile,
)


class TypeDBOntologyRepositoryTests(unittest.TestCase):
    def test_typedb_schema_defines_nodes_assertions_and_keys(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        schema = repository.schema_query()

        self.assertIn("entity ontology-node @abstract", schema)
        self.assertIn("relation ontology-assertion", schema)
        self.assertIn("owns ontology-id @key", schema)
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
        self.assertIn("attribute ontology-temporal-risk-score, value double", schema)
        self.assertIn("attribute ontology-event-cluster-type, value string", schema)
        self.assertIn("owns ontology-window-key", schema)
        self.assertIn("owns ontology-temporal-risk-score", schema)

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

    def test_typedb_repository_factory_disables_native_rule_execution_by_default(self):
        direct = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        factory_default = typedb_repository_from_settings({"ontologyTypeDbEnabled": "1", "typedbAddress": "127.0.0.1:1729"})
        factory_enabled = typedb_repository_from_settings({
            "ontologyTypeDbEnabled": "1",
            "typedbAddress": "127.0.0.1:1729",
            "typedbNativeRuleExecutionEnabled": "1",
        })

        self.assertTrue(direct.native_rule_execution_enabled())
        self.assertFalse(factory_default.native_rule_execution_enabled())
        self.assertTrue(factory_enabled.native_rule_execution_enabled())

    def test_typedb_symbol_filters_keep_numeric_stock_codes_as_strings(self):
        rule = next(item for item in default_graph_inference_rules() if item.rule_id == "graph.loss_guard.breakdown.v1")

        query = typedb_native_match_query(rule.to_dict(), ["000660"])["query"]

        self.assertIn('has ontology-symbol "000660"', query)
        self.assertNotIn("has ontology-symbol 660.0", query)

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
            "profitLossRateChangePct": -5.2,
            "ma20DistanceChange": -7.5,
            "smartMoneyNetChange": -12000,
            "riskEventCount": 2,
            "eventClusterType": "EventDrivenRiskCluster",
            "pricePathPattern": "PersistentDecline",
            "flowPattern": "SmartMoneyOutflow",
            "trendEpisodeType": "PersistentDecline",
            "temporalRiskScore": 84,
            "temporalSupportScore": 0,
            "hasSufficientHistory": True,
        }))
        graph.relations.append(OntologyRelation("stock:005930", "level:005930:ma20", "BREAKS_LEVEL", 0.8, properties={
            "ontologyBox": "ABox",
            "riskImpact": 3.2,
            "polarity": "risk",
            "field": "ma20Distance",
        }))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        queries = repository.insert_queries(graph)

        self.assertTrue(any('has ontology-source-value "holding"' in query for query in queries))
        self.assertTrue(any("has ontology-profit-loss-rate -12.5" in query for query in queries))
        self.assertTrue(any('has ontology-level-type "ma20"' in query for query in queries))
        self.assertTrue(any("has ontology-risk-impact 3.2" in query for query in queries))
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
        self.assertTrue(any("has ontology-risk-event-count 2.0" in query for query in queries))
        self.assertTrue(any('has ontology-event-cluster-type "EventDrivenRiskCluster"' in query for query in queries))
        self.assertTrue(any('has ontology-trend-episode-type "PersistentDecline"' in query for query in queries))
        self.assertTrue(any("has ontology-temporal-risk-score 84.0" in query for query in queries))

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

        self.assertIn("has ontology-box \"ABox\"; limit 1;", repository.queries[0])
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

        with patch.object(repository, "read_entity_rows_by_ids", side_effect=fake_rows):
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

        self.assertIn('has ontology-box "InferenceBox"', repository.queries[0])
        self.assertIn("has ontology-snapshot-id $snapshotId", repository.queries[0])
        self.assertIn('has ontology-box "InferenceBox"', repository.queries[1])
        self.assertIn("has ontology-snapshot-id $snapshotId", repository.queries[1])
        self.assertIn('has ontology-snapshot-id "generation:active"', repository.queries[2])
        self.assertIn('has ontology-symbol "AAPL"', repository.queries[2])
        self.assertIn("limit 3;", repository.queries[2])
        self.assertIn('has ontology-snapshot-id "generation:active"', repository.queries[3])
        self.assertIn('has ontology-symbol "AAPL"', repository.queries[3])
        self.assertIn("limit 4;", repository.queries[3])

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

        result = repository.sync_typedb_native_rule_functions(default_graph_inference_rules()[:1])

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
        command = workers["typedb"]["command"]
        self.assertIn("server", command)
        self.assertIn("--server.listen-address", command)
        self.assertIn("--storage.data-directory", command)
        self.assertEqual("typedb", workers["typedb"]["role"])
        self.assertEqual("24", workers["typedb"]["retentionHours"])
        self.assertEqual("2048", workers["typedb"]["maxSizeMb"])

    def test_typedb_retention_resets_projection_data_when_size_exceeds_limit(self):
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
                self.rulebox_payloads = []
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

            def save_rulebox(self, payload=None):
                self.rulebox_payloads.append(dict(payload or {}))
                rules = payload.get("rules") if isinstance(payload, dict) else []
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

        repository = CapturingSeedRepository()

        result = repository.seed_ontology({"replaceRuleBox": True, "clearInference": True})

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["saved"])
        self.assertTrue(result["ruleBoxReplaced"])
        self.assertEqual(1, len(repository.rulebox_payloads))
        self.assertEqual(len(default_graph_inference_rules()), len(repository.rulebox_payloads[0]["rules"]))
        self.assertEqual(result["expectedRuleBoxRuleCount"], result["activeRuleBoxRuleCount"])
        self.assertEqual(result["expectedRuleBoxShortHash"], result["activeRuleBoxShortHash"])
        self.assertEqual(1, repository.inference_clear_count)

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
                "confidence": 0.86,
                "nativeTypeDbReasoned": True,
                "propertiesJson": json.dumps({
                    "ontologyBox": "InferenceBox",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "confidence": 0.86,
                    "decisionStage": "LOSS_REDUCE",
                    "stagePriority": 90,
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
                "confidence": 0.86,
                "nativeTypeDbReasoned": True,
                "propertiesJson": json.dumps({
                    "ontologyBox": "InferenceBox",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "confidence": 0.86,
                    "matchedConditions": [{"conditionId": "holding-loss"}],
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
                    "riskImpact": 13,
                    "decisionStage": "LOSS_REDUCE",
                    "stagePriority": 90,
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

    def test_typedb_rulebox_execution_materializes_inferencebox_from_typedb_abox_rulebox(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        graph = PortfolioOntology("typedb-run-rulebox")
        graph.entities.append(OntologyEntity("stock:005930", "삼성전자", "stock", {
            "ontologyBox": "ABox",
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

        with patch.object(repository, "has_box_rows", return_value=True), patch.object(repository, "rulebox_snapshot", return_value=rule_snapshot), patch.object(repository, "sync_typedb_native_rule_functions", return_value={"status": "ok", "syncedCount": 1, "syncedFunctionCount": 1, "skippedCount": 0, "failedCount": 0}), patch.object(repository, "match_typedb_native_rules", return_value=native_match), patch.object(repository, "clear_inferencebox", return_value={"status": "ok", "graphStore": "typedb"}), patch.object(repository, "load_graph_for_native_matches", return_value=graph), patch.object(repository, "write_inferencebox_graph", side_effect=capture_inferencebox):
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
        self.assertEqual("typedb-native-rule-materialized", captured["graph"].worldview["reasoningMode"])
        self.assertEqual("typedb-abox-native-rule", captured["graph"].worldview["materializationSource"])
        self.assertTrue(all((item.properties or {}).get("snapshotId") == result["inferenceGenerationId"] for item in captured["graph"].entities))
        self.assertTrue(result["ruleboxRulesHash"])
        self.assertEqual(1, result["ruleboxRuleCount"])
        self.assertTrue(all((item.properties or {}).get("ruleboxRulesHash") == result["ruleboxRulesHash"] for item in captured["graph"].entities))
        self.assertEqual("skipped", result["clearResult"]["status"])

    def test_typedb_rulebox_execution_preserves_inferencebox_when_preflight_fails(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        with patch.object(repository, "has_box_rows", side_effect=RuntimeError("abox unavailable")), patch.object(repository, "clear_inferencebox") as clear_mock:
            result = repository.run_rulebox({"forceClearInference": True})

        clear_mock.assert_not_called()
        self.assertEqual("error", result["status"])
        self.assertIn("ABox", result["reason"])
        self.assertEqual("skipped", result["clearResult"]["status"])
        self.assertTrue(result["clearResult"]["preservedPreviousInference"])

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

        self.assertTrue(all(item.entity_id.endswith(":gen:" + item.entity_id.rsplit(":gen:", 1)[1]) for item in generated.entities))
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

        with patch.object(repository, "has_box_rows", return_value=True), patch.object(repository, "rulebox_snapshot", return_value=rule_snapshot), patch.object(repository, "sync_typedb_native_rule_functions", return_value={"status": "ok", "syncedCount": 1, "syncedFunctionCount": 1, "skippedCount": 0, "failedCount": 0}), patch.object(repository, "match_typedb_native_rules", return_value=native_match), patch.object(repository, "clear_inferencebox", return_value={"status": "ok", "graphStore": "typedb"}), patch.object(repository, "load_graph_for_native_matches", return_value=graph), patch.object(repository, "write_inferencebox_graph", return_value={"configured": True, "saved": False, "status": "error", "reason": "write failed"}):
            result = repository.run_rulebox({"clearInference": True})

        self.assertEqual("error", result["status"])
        self.assertEqual("write failed", result["reason"])
        self.assertGreater(result["relationCount"], 0)
        self.assertFalse(result["nativeTypeDbReasoningUsed"])

    def test_typedb_native_reasoning_profile_identifies_function_ready_rules(self):
        profile = typedb_native_reasoning_profile([rule.to_dict() for rule in default_graph_inference_rules()])

        self.assertEqual("typedb-native-rule-materialization", profile["reasoningModel"])
        self.assertEqual("typedb-native-rule-profile-v2", profile["version"])
        self.assertEqual(profile["ruleCount"], profile["nativeRuleCount"])
        self.assertTrue(profile["rules"][0]["nativeRuleId"].startswith("typedb.native."))
        self.assertEqual(profile["ruleCount"], profile["readyRuleCount"])
        self.assertEqual(0, profile["partialRuleCount"])
        self.assertTrue(profile["rules"][0]["schemaFunctionName"].startswith("orbit_rule_"))
        self.assertTrue(profile["materializationRequired"])

    def test_typedb_function_definition_uses_helper_functions_for_any_condition_groups(self):
        rule = next(item for item in default_graph_inference_rules() if item.rule_id == "graph.loss_smart_money.add_buy_review.v1")
        definition = typedb_native_function_definition(rule.to_dict())

        self.assertEqual(15, len(definition["helperFunctions"]))
        self.assertEqual(16, len(definition["functionDefinitions"]))
        self.assertIn("let $source in", definition["body"])

    def test_typedb_function_definition_uses_promoted_subject_attributes(self):
        rule = next(
            item
            for item in default_graph_inference_rules()
            if item.rule_id == "graph.aggressive.loss_recovery.add_buy_review.v1"
        )
        definition = typedb_native_function_definition(rule.to_dict())
        body = definition["body"] + "\n" + "\n".join(
            str(item.get("body") or "")
            for item in definition.get("helperFunctions") or []
        )

        self.assertIn("ontology-investment-strategy-profile", body)
        self.assertIn("ontology-profit-loss-rate", body)
        self.assertIn("ontology-ma5-distance", body)
        self.assertIn("ontology-ma60-distance", body)
        self.assertIn("ontology-position-account-weight-pct", body)
        self.assertIn("ontology-smart-money-net-volume", body)
        self.assertIn("ontology-bid-ask-imbalance", body)

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

        self.assertIn("ontology-trend-episode-type", persistent_body)
        self.assertIn("ontology-window-key", persistent_body)
        self.assertIn("ontology-value-number", persistent_body)
        self.assertIn("ontology-event-cluster-type", event_body)
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
            "engineVersion": "typedb-schema-function-rule-engine-v1",
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

        with patch.object(repository, "driver_imports") as driver_imports:
            result = repository.sync_typedb_native_rule_functions([rule])

        driver_imports.assert_not_called()
        self.assertTrue(result["cached"])
        self.assertTrue(result["schemaFunctionSyncCached"])
        self.assertEqual(sync_fingerprint, result["syncFingerprint"])


if __name__ == "__main__":
    unittest.main()
