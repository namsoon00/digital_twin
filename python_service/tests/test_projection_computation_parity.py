"""Bound repeated computation without changing factual identity or validation."""

from copy import deepcopy
import hashlib
import json
import re
import unittest
from unittest.mock import MagicMock, patch

from digital_twin.modules.reasoning.domain import ontology_projection_fingerprint as fingerprint
from digital_twin.modules.reasoning.domain import ontology_scopes as scopes
from digital_twin.modules.reasoning.domain import portfolio_ontology_temporal_concepts as temporal
from digital_twin.modules.reasoning.domain.ontology_contracts import (
    OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology,
)
from digital_twin.modules.reasoning.domain.ontology_prompting import ONTOLOGY_PROMPT_VERSION
from digital_twin.modules.reasoning.domain.ontology_quality import build_ontology_quality_sample
from digital_twin.modules.reasoning.domain.ontology_validator import validate_ontology
from digital_twin.modules.reasoning.domain.ontology_projection_payload import (
    ProjectionPayloadBudget, serialize_portfolio_ontology,
)
from digital_twin.modules.reasoning.infrastructure.mysql_ontology_graph_assembly_cache import (
    CACHE_PAYLOAD_VERSION, MySQLOntologyGraphAssemblyCacheStore,
)


def graph_fixture():
    return PortfolioOntology(
        "fixture-account",
        entities=[
            OntologyEntity("stock", "Alpha", "stock", {"tboxClass": "Security"}),
            OntologyEntity("price", "Price", "price", {"tboxClass": "PriceObservation"}),
        ],
        relations=[OntologyRelation("stock", "price", "HAS_PRICE")],
        evidence=[OntologyEvidence("quote", "AAA", "price", "fixture", "price", {"price": 10})],
        worldview={"presentationDeferred": True},
    )


class ProjectionComputationParityTests(unittest.TestCase):
    def test_semantic_group_digest_preserves_persisted_json_contract(self):
        rows = [
            {"value": "한글", "nested": {"b": 2, "a": 1}},
            {"value": [None, True, 1, -0.0, "a, b"]},
            {"value": "a"}, {"value": "a ", "other": "z"},
        ]
        # The persisted contract sorts spaced JSON, then hashes compact JSON.
        # Keep this independent reference when the encoder is optimized.
        def persisted_digest(items):
            payload = json.dumps(sorted(items, key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True,
            )), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()

        groups = {"forward": rows, "reverse": list(reversed(rows)),
                  "distinct": deepcopy(rows), "duplicate": rows + [rows[0]],
                  "single": [rows[0]], "empty": [], "": rows,
                  "generator": (row for row in rows), "empty-generator": iter([])}
        before = deepcopy(rows)
        result = scopes._fingerprint_semantic_groups(groups)
        self.assertEqual({
            "forward": persisted_digest(rows), "reverse": persisted_digest(rows),
            "distinct": persisted_digest(rows), "duplicate": persisted_digest(rows + [rows[0]]),
            "single": persisted_digest([rows[0]]), "generator": persisted_digest(rows),
            "empty-generator": persisted_digest([]),
        }, result)
        self.assertEqual(before, rows)

    def test_semantic_group_reuse_is_local_and_never_reuses_changed_facts(self):
        row = {"field": "price", "value": 10}
        groups = {"field:price": [row], "kind:price:field:price": [row]}
        with patch.object(scopes.json, "dumps", wraps=json.dumps) as encode:
            original = scopes._fingerprint_semantic_groups(groups)
        self.assertEqual(1, encode.call_count)
        row["value"] = 11
        updated = scopes._fingerprint_semantic_groups(groups)
        self.assertNotEqual(original, updated)
        self.assertEqual(updated["field:price"], updated["kind:price:field:price"])

    def test_overlapping_groups_encode_each_assertion_at_most_twice(self):
        rows = [{"field": "가격", "value": index, "nested": [None, -0.0, "a, b"]}
                for index in range(30)]
        groups = {str(index): rows[index:] + [rows[-1]] for index in range(20)}
        expected = {key: hashlib.sha256(json.dumps(
            sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True)),
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest() for key, items in groups.items()}
        with patch.object(scopes.json, "dumps", wraps=json.dumps) as encode:
            actual = scopes._fingerprint_semantic_groups(groups)
        self.assertEqual(expected, actual)
        self.assertLessEqual(encode.call_count, 2 * len(rows))

    def test_field_family_reuse_preserves_scope_hashes_and_rechecks_policy(self):
        graph = graph_fixture()
        graph.entities += [OntologyEntity("stock2", "Beta", "stock", {})]
        for entity in graph.entities:
            entity.properties.update(aboxScopeId="fixture", currentPrice=10)
        for relation in graph.relations:
            relation.properties["aboxScopeId"] = "fixture"
        for evidence in graph.evidence:
            evidence.value["aboxScopeId"] = "fixture"
        for family in ["market", "flow"]:
            with patch.object(scopes, "_semantic_property_family", return_value=family) as classify:
                payloads, semantic, _ = scopes._scope_fragment_payloads_with_semantic_fingerprints_and_dependencies(
                    graph, ["fixture"],
                )
                calls = classify.call_args_list
                self.assertEqual(len(calls), len({call.args for call in calls}))
                self.assertEqual(scopes._scope_fragment_payload(graph, "fixture"), payloads["fixture"])
                self.assertEqual(scopes._scope_semantic_fingerprints(graph, "fixture"), semantic["fixture"])

    def test_optional_serialization_budget_preserves_complete_payload_and_copy_isolation(self):
        graph = graph_fixture()
        graph.entities[0].properties["nested"] = {"values": ["한글", None, True]}
        expected = serialize_portfolio_ontology(graph)
        budget = ProjectionPayloadBudget(1024 * 1024)
        actual = serialize_portfolio_ontology(graph, budget=budget)
        self.assertEqual(expected, actual)
        self.assertGreater(budget.minimum_bytes, 0)
        self.assertLessEqual(budget.minimum_bytes, len(json.dumps(actual, ensure_ascii=False, sort_keys=True).encode()))
        actual["entities"][0]["properties"]["nested"]["values"].append("changed")
        self.assertEqual(expected, serialize_portfolio_ontology(graph))

    def test_oversized_cache_stops_before_remaining_rows_or_database_writes(self):
        graph = graph_fixture()
        # UTF-8 bytes, not character count, exceed the minimum 64 KiB budget.
        graph.entities[0].properties["large"] = "한" * 24000
        before = deepcopy(graph)
        store = object.__new__(MySQLOntologyGraphAssemblyCacheStore)
        with patch.object(graph.entities[1], "to_dict", side_effect=AssertionError("Unneeded row conversion")), \
                patch.object(store, "transaction", side_effect=AssertionError("Oversized cache wrote to MySQL")):
            result = store.put("key", graph, graph, 120, 4, 64 * 1024)
        self.assertEqual("skipped-oversized", result["status"])
        self.assertGreater(result["payloadBytesLowerBound"], result["maxPayloadBytes"])
        self.assertEqual(before, graph)

    def test_small_cache_keeps_exact_wire_payload_and_roundtrip(self):
        graph = graph_fixture()
        packet = {"frozen": "자료 시각", "values": [1, 2]}
        expected = {"version": CACHE_PAYLOAD_VERSION, "graph": serialize_portfolio_ontology(graph),
                    "persistenceGraph": serialize_portfolio_ontology(graph), "runtimeContextPacket": packet}
        wire = json.dumps(expected, ensure_ascii=False, sort_keys=True)
        store = object.__new__(MySQLOntologyGraphAssemblyCacheStore)
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"count": 1}
        with patch.object(store, "transaction") as transaction:
            transaction.return_value.__enter__.return_value = connection
            saved = store.put("key", graph, graph, 120, 4, 64 * 1024, packet)
        self.assertEqual("stored", saved["status"])
        insert = next(call for call in connection.execute.call_args_list if "INSERT INTO" in call.args[0])
        self.assertEqual(wire, insert.args[1][1])
        self.assertEqual(len(wire.encode()), saved["payloadBytes"])
        connection.execute.return_value.fetchone.return_value = {
            "payload_json": wire, "created_at": "", "payload_bytes": len(wire.encode()),
        }
        with patch.object(store, "connect") as connect:
            connect.return_value.__enter__.return_value = connection
            restored = store.get("key", 120)
        self.assertEqual("hit", restored["status"])
        self.assertEqual(graph, restored["graph"])
        self.assertEqual(graph, restored["persistenceGraph"])
        self.assertEqual(packet, restored["runtimeContextPacket"])

    def test_cache_retains_final_exact_size_check_for_envelope_fields(self):
        graph = graph_fixture()
        graph.prompt = "x" * (64 * 1024)
        store = object.__new__(MySQLOntologyGraphAssemblyCacheStore)
        with patch.object(store, "transaction", side_effect=AssertionError("Oversized cache wrote to MySQL")):
            result = store.put("key", graph, graph, 120, 4, 64 * 1024)
        self.assertEqual("skipped-oversized", result["status"])
        self.assertGreater(result["payloadBytes"], result["maxPayloadBytes"])

    def test_cache_budget_is_shared_by_both_graphs_and_frozen_packet(self):
        graph = graph_fixture()
        graph.entities[0].properties["large"] = "x" * 40000
        store = object.__new__(MySQLOntologyGraphAssemblyCacheStore)
        with patch.object(store, "transaction", side_effect=AssertionError("Oversized cache wrote to MySQL")):
            combined = store.put("key", graph, graph, 120, 4, 64 * 1024)
            with patch.object(graph.entities[0], "to_dict", side_effect=AssertionError("Oversized packet already rejected")):
                packet = store.put("key", graph, graph, 120, 4, 64 * 1024, {"data": "x" * 70000})
        for result in [combined, packet]:
            self.assertEqual("skipped-oversized", result["status"])
            self.assertGreater(result["payloadBytesLowerBound"], result["maxPayloadBytes"])

    def test_vocabulary_caches_preserve_unicode_and_live_policy_membership(self):
        keys = [" Source_As-Of ", "sourceAsOf", "currentPrice", "체결강도", "ÄBC_1", "a.b/c", ""]
        for key in keys:
            self.assertEqual(re.sub(r"[^a-z0-9]", "", key.lower()), scopes._normalized_semantic_field(key))
            self.assertEqual("".join(c for c in key.lower() if c.isalnum()), fingerprint._normalized_lifecycle_key(key))
        custom = "fixture-policy-key"
        self.assertFalse(fingerprint.is_volatile_lifecycle_key(custom))
        with patch.object(fingerprint, "_NORMALIZED_VOLATILE_LIFECYCLE_KEYS", {"fixturepolicykey"}):
            self.assertTrue(fingerprint.is_volatile_lifecycle_key(custom))
        self.assertFalse(fingerprint.is_volatile_lifecycle_key(custom))
        for index in range(4200):
            scopes._normalized_semantic_field(str(index))
            fingerprint._normalized_lifecycle_key(str(index))
        self.assertLessEqual(scopes._normalized_semantic_field.cache_info().currsize, 4096)
        self.assertLessEqual(fingerprint._normalized_lifecycle_key.cache_info().currsize, 4096)
        self.assertEqual({"currentPrice": 11, "freshnessState": "stale"}, fingerprint.stable_value({
            "currentPrice": 11, "sourceAsOf": "2026-10-24T00:00:00Z", "freshnessState": "stale",
        }))

    def test_quality_record_keeps_validation_without_rendering_ai_presentation(self):
        graph = graph_fixture()
        validation = validate_ontology(graph).to_dict()
        with patch.object(graph, "to_dict", side_effect=AssertionError("Quality must not render the graph")):
            sample = build_ontology_quality_sample(graph, created_at="2026-10-24T00:00:00Z")
        self.assertEqual(ONTOLOGY_PROMPT_VERSION, sample.payload["promptVersion"])
        self.assertEqual(validation, sample.payload["validation"])
        self.assertEqual((2, 1, 1), (sample.entity_count, sample.relation_count, sample.evidence_count))
        self.assertEqual("conditional", sample.reasoning_state)

    def test_endpoint_validation_retains_first_duplicate_and_missing_endpoint_errors(self):
        class CountedEntities(list):
            scans = 0

            def __iter__(self):
                self.scans += 1
                return super().__iter__()

        graph = graph_fixture()
        graph.entities = CountedEntities(graph.entities + [
            OntologyEntity("stock", "Wrong duplicate", "price", {"tboxClass": "PriceObservation"}),
        ])
        graph.relations = graph.relations * 100 + [
            OntologyRelation("absent", "price", "HAS_PRICE"),
            OntologyRelation("stock", "absent", "HAS_PRICE"),
            OntologyRelation("price", "stock", "HAS_PRICE"),
            OntologyRelation("stock", "price", "UNKNOWN_RELATION"),
        ]
        report = validate_ontology(graph)
        self.assertEqual([
            "missing_relation_source", "missing_relation_target",
            "relation_endpoint_contract_violation", "unknown_relation_type",
        ], [issue.code for issue in report.issues])
        self.assertLessEqual(graph.entities.scans, 4)

    def test_overlapping_temporal_windows_parse_each_source_once_without_cross_build_cache(self):
        first = {"events": [{"evidenceId": "same", "polarity": "support"}]}
        second = {"events": [{"evidenceId": "same", "polarity": "risk"},
                             {"evidenceId": "other", "polarity": "contradiction"}]}
        rows = [{"externalSignals": first}, {"externalSignals": second}]

        def parse(symbol, state):
            return deepcopy((state.get("externalSignals") or {}).get("events", []))

        with patch.object(temporal, "research_events_for_state", side_effect=parse) as parser:
            cache = {}
            recent = temporal.event_counts("AAA", rows[1:], event_cache=cache)
            complete = temporal.event_counts("AAA", rows, event_cache=cache)
            self.assertEqual(2, parser.call_count)
            self.assertEqual({"eventCount": 2, "riskEventCount": 2, "supportEventCount": 0}, complete)
            self.assertEqual(recent, complete)
            temporal.event_counts("BBB", rows, event_cache=cache)
            self.assertEqual(4, parser.call_count)
            first["events"][0]["polarity"] = "risk"
            fresh = temporal.event_counts("AAA", rows[:1], event_cache={})
            self.assertEqual(1, fresh["riskEventCount"])
            self.assertEqual(0, temporal.event_counts("AAA", [{}], event_cache={})["eventCount"])


if __name__ == "__main__":
    unittest.main()
