"""Opt-in TypeDB witness-order regression; owns and deletes a temporary DB."""

import json
import uuid

from test_native_evidence_witness import fixture
from digital_twin.infrastructure.settings import load_local_env, runtime_settings
from digital_twin.infrastructure.typedb_ontology import typedb_repository_from_settings
from digital_twin.modules.reasoning.domain.ontology_inference_materializer import grounded_inference_context
from digital_twin.modules.reasoning.infrastructure.typeql import typedb_native_match_query
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string


def run():
    load_local_env()
    database = "orbit_witness_smoke_" + uuid.uuid4().hex[:12]
    settings = runtime_settings(fast_operational_read=True)
    repo = typedb_repository_from_settings({**settings, "typedbDatabase": database})
    imported = repo.driver_imports()
    transaction_type = imported[0][-1]
    driver = repo.create_driver(imported)
    created = False
    results = []
    attributes = ["ontology-id", "ontology-storage-id", "ontology-label", "ontology-kind",
                  "ontology-symbol", "ontology-relation-type", "ontology-data-scope"]
    schema = "define " + " ".join("attribute " + a + ", value string;" for a in attributes)
    schema += " entity ontology-node, " + ", ".join("owns " + a for a in attributes)
    schema += ", plays ontology-assertion:source, plays ontology-assertion:target;"
    schema += " entity ontology-class-stock, sub ontology-node; entity ontology-class-coverage-gap, sub ontology-node;"
    schema += " relation ontology-assertion, relates source, relates target, owns ontology-id, owns ontology-storage-id, owns ontology-relation-type;"
    schema += " relation ontology-relation-has-coverage-gap, sub ontology-assertion;"

    def has(values):
        return ", ".join("has " + key + " " + typedb_string(value) for key, value in values.items())

    try:
        driver.databases.create(database)
        created = True
        with driver.transaction(database, transaction_type.SCHEMA) as tx:
            tx.query(schema).resolve()
            tx.commit()
        for generation in ("baseline", "candidate"):
            graph, rule, stock, context = fixture()
            source_storage = generation + ":source"
            clauses = ["$source isa ontology-class-stock, " + has({"ontology-id": stock.entity_id,
                "ontology-storage-id": source_storage, "ontology-label": "AAA", "ontology-kind": "stock", "ontology-symbol": "AAA"}) + ";"]
            for i, relation in enumerate(graph.relations):
                target = graph.entities[i + 1]
                rid = generation + ":" + str(i if generation == "baseline" else 3 - i)
                relation.properties["_relationId"] = rid
                clauses.append("$target" + str(i) + " isa ontology-class-coverage-gap, " + has({
                    "ontology-id": target.entity_id, "ontology-kind": target.kind,
                    "ontology-data-scope": target.properties["dataScope"]}) + ";")
                clauses.append("$r" + str(i) + " isa ontology-relation-has-coverage-gap, links (source: $source, target: $target" + str(i) + "), " + has({
                    "ontology-id": rid, "ontology-storage-id": rid, "ontology-relation-type": relation.relation_type}) + ";")
            with driver.transaction(database, transaction_type.WRITE) as tx:
                tx.query("insert " + " ".join(clauses)).resolve()
                tx.commit()
            plan = typedb_native_match_query(rule.to_dict(), ["AAA"], active_source_storage_ids=[source_storage],
                active_relation_storage_ids_by_type={"HAS_COVERAGE_GAP": [r.properties["_relationId"] for r in graph.relations]})
            rows = repo.read_rows(plan["query"], plan["columns"])
            assert len(rows) == 2, "TypeDB must return both exact cross-listed witnesses"
            context["evidenceRelationIds"] = [r[plan["evidenceColumns"][0]] for r in rows]
            proof = grounded_inference_context(graph, rule, stock, context)
            assert proof["evidenceUsableForJudgement"]
            condition = proof["matchedConditions"][0]
            assert condition["targetId"] == "gap:etf"
            results.append({"generation": generation, "nativeWitnessCount": len(rows),
                            "targetId": condition["targetId"], "sourceAsOf": condition["observedAt"]})
        return {"status": "passed", "cases": results, "operationalGraphMutated": False, "deliveryCount": 0}
    finally:
        if created:
            driver.databases.get(database).delete()
        driver.close()


if __name__ == "__main__":
    print(json.dumps(run()))
