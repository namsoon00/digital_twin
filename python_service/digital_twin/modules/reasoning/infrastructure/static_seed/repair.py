"""TypeDB static-seed repair owner; no facade or runtime construction."""

from .repair_ports import RepairStore, RepairBindings
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from typing import Dict


def repair_seed_relations(
    _store: RepairStore, graph: PortfolioOntology, *, _bindings: RepairBindings
) -> Dict[str, object]:
    """Insert only missing static relations after an interrupted seed."""
    if not _store.address:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "missingRelationCount": 0,
        }
    imported = _store.driver_imports()
    if imported[0] is None:
        return _store.driver_missing_result(imported[1], graph)
    try:
        missing_rows = _store.missing_seed_relation_rows(graph)
        if not missing_rows:
            return {
                "configured": True,
                "saved": True,
                "status": "unchanged",
                "graphStore": "typedb",
                "missingRelationCount": 0,
                "insertedRelationCount": 0,
            }
        settings = _bindings.runtime_settings()
        relation_batch_size = _store.abox_relation_batch_size(settings)
        queries = _store.batched_relation_insert_queries(
            missing_rows,
            _bindings.utc_now(),
            relation_batch_size,
            _store.write_query_max_bytes(settings),
        )
        (_TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType) = (
            imported[0]
        )

        def operation():
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                _store.ensure_schema(driver, imported)
                transaction_query_count = _store.graph_write_transaction_query_count(
                    settings
                )
                for offset in range(0, len(queries), transaction_query_count):
                    query_batch = queries[offset : offset + transaction_query_count]
                    with _bindings.typedb_operation_timeout(
                        _store.write_operation_timeout_seconds(),
                        "TypeDB seed relation repair",
                    ):
                        with driver.transaction(
                            _store.database,
                            TransactionType.WRITE,
                            options=_store.write_transaction_options(),
                        ) as tx:
                            for query in query_batch:
                                tx.query(query).resolve()
                            tx.commit()
            finally:
                _store.close_driver(driver)

        _store.with_typedb_retries(operation)
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "missingRelationCount": len(missing_rows),
            "insertedRelationCount": len(missing_rows),
            "queryCount": len(queries),
        }
    except Exception as error:
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "reason": str(error)[:220],
        }
