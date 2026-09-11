"""Synthetic schema contracts captured before the runtime extraction."""

from contextlib import nullcontext
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


SCHEMA = """define
attribute ontology-id, value string;
attribute ontology-storage-id, value string;
relation ontology-assertion, relates source, relates target, owns ontology-id;
entity ontology-node @abstract, owns ontology-id, owns ontology-storage-id @unique;
entity child-node, sub parent-node;
entity parent-node, sub ontology-node;
"""
PARTIAL = """define
attribute ontology-id, value string;
entity ontology-node @abstract, owns ontology-id;
"""
IMPORTED = ((None, None, None, None, SimpleNamespace(SCHEMA="schema")), None)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def contract_fingerprints(api):
    contracts = {}
    repository = api.TypeDBOntologyGraphRepository("schema-contract.invalid:1729", database="synthetic")
    for name, schema in [("synthetic", SCHEMA), ("production-contract", repository.schema_query())]:
        with patch.object(repository, "schema_query", return_value=schema):
            for size in [1, 16, 64, 2048]:
                for existing_name, existing in [("empty", ""), ("partial", PARTIAL), ("complete", schema)]:
                    plan = repository.base_schema_bootstrap_plan(existing, batch_size=size)
                    contracts[f"plan/{name}/{size}/{existing_name}"] = fingerprint(plan)

    migrations = [
        ("ontology_storage_identity", ("entity ontology-node, owns ontology-id @key;\nrelation ontology-assertion, owns ontology-id @key;",)),
        ("ontology_scope_schema", ()),
        ("ontology_content_fingerprint_schema", (PARTIAL,)),
        ("ontology_world_schema", ()),
        ("promoted_schema", (SCHEMA,)),
        ("ontology_semantic_schema", (SCHEMA,)),
    ]
    for name, args in migrations:
        driver = MagicMock()
        tx = driver.transaction.return_value.__enter__.return_value
        with patch.object(api, "typedb_operation_timeout", side_effect=lambda *args: nullcontext()):
            getattr(repository, "migrate_" + name)(driver, IMPORTED, *args)
        contracts["migration/" + name] = fingerprint({
            "queries": [call.args[0] for call in tx.query.call_args_list],
            "commits": tx.commit.call_count,
        })

    for fresh, created, http, inspection in [
        (True, True, False, "empty"), (True, True, True, "empty"),
        (True, False, False, "partial"), (True, False, True, "partial"),
        (True, False, False, "complete"), (True, False, False, "failure"),
        (False, False, False, "empty"), (False, False, False, "complete"),
        (False, False, False, "failure"),
    ]:
        repo = api.TypeDBOntologyGraphRepository(
            "schema-contract.invalid:1729", database="synthetic", fresh_candidate_rebuild=fresh,
            http_address="http://schema-contract.invalid" if http else "",
        )
        repo._database_created_in_process = created
        repo.schema_query = lambda: SCHEMA
        events = []

        def inspect(_driver):
            events.append(["inspect"])
            if inspection == "failure":
                raise RuntimeError("inspection unavailable")
            return {"empty": "", "partial": PARTIAL, "complete": SCHEMA}[inspection]

        repo.typedb_schema_text = inspect
        repo.process_base_schema_is_ready = lambda fingerprint: False
        repo.mark_process_base_schema_ready = lambda fingerprint: events.append(["ready", fingerprint])
        repo.base_schema_contract_state = lambda: {"status": "current"}
        repo.synchronize_base_schema_batches = lambda driver, imported, *args, **kwargs: events.append(["grpc", list(args), kwargs])
        repo.synchronize_base_schema_batches_http = lambda *args, **kwargs: events.append(["http", list(args), kwargs])
        with patch.object(api, "typedb_operation_timeout", side_effect=lambda *args: nullcontext()):
            try:
                repo.ensure_schema(object(), IMPORTED)
            except RuntimeError as error:
                events.append(["error", str(error)])
        contracts[f"ensure/{fresh}/{created}/{http}/{inspection}"] = fingerprint(events)
    return contracts
