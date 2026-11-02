"""Opt-in temporal quality regression against a disposable TypeDB database.

Run with PYTHONPATH=python_service:python_service/tests python3
python_service/tests/native_temporal_quality_smoke.py. Never delivers alerts.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import uuid

from digital_twin.infrastructure.settings import load_local_env, runtime_settings
from digital_twin.infrastructure.typedb_ontology import typedb_repository_from_settings
from digital_twin.modules.model_registry.contracts import governed_graph_inference_rules
from digital_twin.modules.reasoning.domain.portfolio_ontology_temporal_concepts import TemporalWindowDefinition, temporal_window_values
from digital_twin.modules.reasoning.infrastructure.typeql import typedb_native_match_query
from digital_twin.modules.reasoning.infrastructure.typeql.condition_queries import typedb_entity_match_type
from digital_twin.modules.reasoning.infrastructure.typeql.constants import TYPEDB_NUMERIC_ATTRIBUTES
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_literal_for_attribute
from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import typedb_target_attribute, typedb_relation_storage_type


def run(settings=None):
    load_local_env()
    configured = dict(settings or runtime_settings(fast_operational_read=True))
    database = "orbit_temporal_quality_smoke_" + uuid.uuid4().hex[:12]
    repo = typedb_repository_from_settings({**configured, "typedbDatabase": database})
    imported = repo.driver_imports()
    transaction_type = imported[0][-1]
    driver = repo.create_driver(imported)
    rule_id = "graph.temporal.stale_observation.block.v1"
    rules = [next(r for r in governed_graph_inference_rules(filtered_temporal_paths=mode)
                  if r.rule_id == rule_id) for mode in (False, True)]
    fields = {key for rule in rules for c in rule.conditions for key in c.target_property_filters}
    assert all(typedb_target_attribute(key) for key in fields), "Unmapped native condition"
    attributes = {"ontology-id", "ontology-storage-id", "ontology-label", "ontology-kind",
                  "ontology-symbol", "ontology-relation-type"} | {typedb_target_attribute(key) for key in fields}
    target_type = typedb_entity_match_type("temporal-window")
    relation_type = typedb_relation_storage_type("HAS_TEMPORAL_WINDOW")
    schema = "define " + " ".join("attribute " + a + ", value "
        + ("double" if a in TYPEDB_NUMERIC_ATTRIBUTES else "string") + ";" for a in sorted(attributes))
    schema += " entity ontology-node, " + ", ".join("owns " + a for a in sorted(attributes))
    schema += ", plays ontology-assertion:source, plays ontology-assertion:target;"
    schema += " entity ontology-class-stock, sub ontology-node;"
    if target_type != "ontology-node":
        schema += " entity " + target_type + ", sub ontology-node;"
    schema += " relation ontology-assertion, relates source, relates target, owns ontology-id, owns ontology-storage-id, owns ontology-relation-type;"
    if relation_type != "ontology-assertion":
        schema += " relation " + relation_type + ", sub ontology-assertion;"
    start = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
    rows = [{"sourceAsOf": (start + timedelta(days=i // 2, minutes=i % 2)).isoformat(),
             "currentPrice": 100 + i, "dataQuality": "fresh"} for i in range(40)]
    cases = {"fresh": ([], [False, False]), "one-old-middle": ([13], [True, False]),
             "latest-invalid": ([39], [True, True]), "low-valid-ratio": (list(range(10)), [True, True])}
    def has(values):
        return ", ".join("has " + key + " " + typedb_literal_for_attribute(key, value)
                         for key, value in values.items())
    created = False
    results = {}
    try:
        driver.databases.create(database)
        created = True
        with driver.transaction(database, transaction_type.SCHEMA) as tx:
            tx.query(schema).resolve()
            tx.commit()
        for name, (bad_indexes, expected) in cases.items():
            data = deepcopy(rows)
            for index in bad_indexes:
                data[index].update(dataQuality="stale", currentPrice=10000)
            values = temporal_window_values(data, TemporalWindowDefinition("20D", 20, 5))
            props = {typedb_target_attribute(key): values[key] for key in fields}
            source_id, rel_id = name + ":source", name + ":window-link"
            clauses = ["$s isa ontology-class-stock, " + has({"ontology-id": source_id,
                "ontology-storage-id": source_id, "ontology-kind": "stock", "ontology-symbol": name.upper(),
                "ontology-label": "Synthetic"}) + ";",
                "$t isa " + target_type + ", " + has({**props, "ontology-id": name + ":window",
                    "ontology-kind": "temporal-window"}) + ";",
                "$r isa " + relation_type + ", links (source: $s, target: $t), " + has({
                    "ontology-id": rel_id, "ontology-storage-id": rel_id,
                    "ontology-relation-type": "HAS_TEMPORAL_WINDOW"}) + ";"]
            with driver.transaction(database, transaction_type.WRITE) as tx:
                tx.query("insert " + " ".join(clauses)).resolve()
                tx.commit()
            actual = []
            for rule in rules:
                plan = typedb_native_match_query(rule.to_dict(), [name], active_source_storage_ids=[source_id],
                    active_relation_storage_ids_by_type={"HAS_TEMPORAL_WINDOW": [rel_id]})
                with driver.transaction(database, transaction_type.READ) as tx:
                    actual.append(bool(list(tx.query(plan["query"]).resolve())))
            assert actual == expected, (name, actual, expected)
            results[name] = {"legacyConstrained": actual[0], "successorConstrained": actual[1],
                             "used": values["sampleCount"], "excluded": values["excludedPriceObservationCount"]}
        return {"status": "passed", "cases": results, "nativeQueryCount": len(cases) * len(rules),
                "deliveryCount": 0, "operationalGraphMutated": False}
    finally:
        if created:
            driver.databases.get(database).delete()
        driver.close()


if __name__ == "__main__":
    print(json.dumps(run()))
