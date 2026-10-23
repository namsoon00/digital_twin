"""Opt-in native strategy smoke; creates/deletes only its own temporary TypeDB.

Run: PYTHONPATH=python_service:python_service/tests python3 python_service/tests/native_strategy_smoke.py
Uses local connection settings, never the operational graph or notification path.
"""
import json
import uuid

from test_rule_strategy_contract import strategy
from test_source_session_observation import source_row, source_signal
from digital_twin.infrastructure.settings import load_local_env
from digital_twin.infrastructure.web.adapters.strategy_proposals import investment_strategy_proposal_read_service
from digital_twin.infrastructure.typedb_ontology import TypeDBOntologyGraphRepository
from digital_twin.modules.model_registry.domain.rule_strategy_compilation import compile_strategy_rules
from digital_twin.modules.reasoning.infrastructure.typeql import typedb_native_match_query
from digital_twin.modules.reasoning.infrastructure.typeql.condition_queries import typedb_entity_match_type
from digital_twin.modules.reasoning.contracts import entity_id
from digital_twin.modules.reasoning.infrastructure.typeql.constants import TYPEDB_NUMERIC_ATTRIBUTES
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_literal_for_attribute, typedb_string
from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import typedb_target_attribute, typedb_relation_storage_type


def run():
    load_local_env()
    operational = investment_strategy_proposal_read_service().ontology_repository
    database = "orbit_strategy_smoke_" + uuid.uuid4().hex[:12]
    repo = TypeDBOntologyGraphRepository(operational.address, user=operational.user,
        password=operational.password, tls_enabled=operational.tls_enabled,
        database=database, retry_count=0)
    imported = repo.driver_imports()
    if not imported[0]:
        raise RuntimeError("TypeDB driver unavailable")
    transaction_type = imported[0][-1]
    from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
    contract = next(r.resolved_claim_contract.strategy_contract for r in default_graph_inference_rules()
                    if r.resolved_claim_contract.strategy_contract and r.resolved_claim_contract.strategy_contract['market'] == 'KR')
    rules = compile_strategy_rules(contract)
    models = {rid: next(c for c in r.conditions if c.relation_type == "HAS_MODEL_SIGNAL"
                       and c.target_property_filters["hypothesisContractId"] == rid)
              for r in rules for rid in r.resolved_claim_contract.strategy_contract["phases"][r.resolved_claim_contract.strategy_phase]}
    attributes = {"ontology-id", "ontology-storage-id", "ontology-label", "ontology-kind",
                  "ontology-symbol", "ontology-relation-type"}
    attributes.update(typedb_target_attribute(k) for c in models.values() for k in c.target_property_filters)
    assert "" not in attributes, "Every filter must compile to a native attribute"
    schema = "define\n" + "\n".join(
        "attribute " + a + ", value " + ("double" if a in TYPEDB_NUMERIC_ATTRIBUTES else "string") + ";"
        for a in sorted(attributes))
    schema += "\nentity ontology-node, " + ", ".join("owns " + a for a in sorted(attributes))
    schema += ", plays ontology-assertion:source, plays ontology-assertion:target;"
    schema += "\nentity ontology-class-stock, sub ontology-node;"
    market_type = typedb_entity_match_type("market")
    if market_type != "ontology-node":
        schema += "\nentity " + market_type + ", sub ontology-node;"
    schema += "\nrelation ontology-assertion, relates source, relates target, owns ontology-id, owns ontology-storage-id, owns ontology-relation-type;"
    schema += "\nrelation ontology-relation-has-model-signal, sub ontology-assertion;"
    market_relation_type = typedb_relation_storage_type('TRADED_IN')
    if market_relation_type != 'ontology-assertion':
        schema += "\nrelation " + market_relation_type + ", sub ontology-assertion;"
    scenarios = {"fresh": [1, 1, 1, 1], "stale": [0, 0, 0, 0], "closed": [0, 0, 0, 0],
                 "ineligible": [0, 0, 0, 0], "wrong-release": [0, 0, 0, 0],
                 "missing-entry-member": [1, 0, 1, 1], "wrong-market": [0, 0, 0, 0],
                 "persisted-source": [1, 1, 1, 1], "persisted-after-close": [0, 0, 0, 0],
                 "persisted-missing-clock": [0, 0, 0, 0]}
    # Session/age used to be injected from the rule itself, hiding a real
    # ingestion defect. Exercise Position -> persisted row -> model signal.
    source_packets = {
        "persisted-source": source_signal(),
        "persisted-after-close": source_signal(source_row(
            "2026-10-23T06:29:59Z", "2026-10-23T06:30:01Z")),
        "persisted-missing-clock": source_signal(source_row(source="")),
    }

    def has(values):
        return ", ".join("has " + a + " " + typedb_literal_for_attribute(a, v) for a, v in values.items())

    driver = repo.create_driver(imported)
    created = False
    results = {}
    try:
        driver.databases.create(database)
        created = True
        with driver.transaction(database, transaction_type.SCHEMA) as tx:
            tx.query(schema).resolve()
            tx.commit()
        for scenario, expected in scenarios.items():
            source_id = scenario + ":source"
            clauses = ["$source isa ontology-class-stock, " + has({"ontology-id": source_id,
                "ontology-storage-id": source_id, "ontology-kind": "stock", "ontology-label": "Synthetic",
                "ontology-symbol": "STRATEGY_TEST"}) + ";"]
            market_relation = scenario + ":market-relation"
            clauses.append("$market isa " + market_type + ", " + has({"ontology-id": entity_id("market", "US" if scenario == "wrong-market" else "KR"), "ontology-kind": "market"}) + ";")
            clauses.append("$marketRelation isa " + market_relation_type + ", links (source: $source, target: $market), "
                           + has({"ontology-id": market_relation, "ontology-storage-id": market_relation, "ontology-relation-type": "TRADED_IN"}) + ";")
            relation_ids = {}
            for index, (rid, condition) in enumerate(models.items()):
                rel_id = scenario + ":relation:" + str(index)
                relation_ids[rid] = rel_id
                if scenario == "missing-entry-member" and rid == contract["phases"]["entry"][0]:
                    continue
                values = {}
                for field, value in condition.target_property_filters.items():
                    if isinstance(value, list): value = value[0]
                    if field == "sourceAgeSeconds": value = contract['signalValiditySeconds'] + 1 if scenario == "stale" else 30
                    if field == "marketSession" and scenario == "closed": value = "closed"
                    if field == "eligibilityStatus" and scenario == "ineligible": value = "ineligible"
                    if field == "releaseId" and scenario == "wrong-release": value = "unregistered-release"
                    if scenario in source_packets and field in {"marketSession", "sourceAgeSeconds"}:
                        signal = source_packets[scenario].signals[0]
                        value = signal.market_session if field == "marketSession" else signal.source_age_seconds
                        if value in (None, ""):
                            continue
                    values[typedb_target_attribute(field)] = value
                values.update({"ontology-id": scenario + ":model:" + str(index),
                               "ontology-kind": "statistical-model-hypothesis-evidence"})
                target = "$target" + str(index)
                clauses.append(target + " isa ontology-node, " + has(values) + ";")
                clauses.append("$r" + str(index) + " isa ontology-relation-has-model-signal, links (source: $source, target: " + target + "), "
                    + has({"ontology-id": rel_id, "ontology-storage-id": rel_id, "ontology-relation-type": "HAS_MODEL_SIGNAL"}) + ";")
            with driver.transaction(database, transaction_type.WRITE) as tx:
                tx.query("insert " + " ".join(clauses)).resolve()
                tx.commit()
            actual = []
            for rule in rules:
                plan = typedb_native_match_query(rule.to_dict(), ["STRATEGY_TEST"],
                    active_source_storage_ids=[source_id], active_relation_storage_ids_by_condition={
                        c.condition_id: ([market_relation] if c.relation_type == "TRADED_IN" else
                                         [relation_ids[c.target_property_filters["hypothesisContractId"]]])
                        for c in rule.conditions})
                with driver.transaction(database, transaction_type.READ) as tx:
                    actual.append(len(list(tx.query(plan["query"]).resolve())))
            assert actual == expected, (scenario, actual, expected)
            results[scenario] = actual
        return {"status": "passed", "cases": results, "nativeQueryCount": len(scenarios) * len(rules),
                "sourceIngestionChecked": True,
                "operationalGraphMutated": False}
    finally:
        if created:
            driver.databases.get(database).delete()
        driver.close()


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
