"""graph_reads: execution through explicit injected capabilities."""

from typing import Dict, Iterable, List
import time
from .execution_ports import GraphReadsExecutionStore, GraphReadsExecutionRuntime


def read_rows(
    _store: GraphReadsExecutionStore,
    query: str,
    columns: Iterable[str],
    label: str = "typedb.read",
    timeout_seconds: float = None,
) -> List[Dict[str, object]]:
    if not _store.address:
        return []
    imported = _store.driver_imports()
    if imported[0] is None:
        raise RuntimeError(
            "typedb-driver Python package is not installed: " + str(imported[1])[:160]
        )
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    request_timeout = (
        _store.query_timeout_seconds()
        if timeout_seconds is None
        else max(0.5, float(timeout_seconds))
    )

    def operation():
        driver = _store.open_driver(imported, request_timeout_seconds=request_timeout)
        try:
            _store.ensure_database(driver)
            with driver.transaction(
                _store.database,
                TransactionType.READ,
                _store.read_transaction_options(timeout_seconds),
            ) as tx:
                return _store.read_rows_in_transaction(
                    tx,
                    query,
                    columns,
                    label=label,
                    timeout_seconds=timeout_seconds,
                )
        finally:
            _store.close_driver(driver)

    return _store.with_typedb_retries(operation)


def read_rows_in_transaction(
    _store: GraphReadsExecutionStore,
    tx,
    query: str,
    columns: Iterable[str],
    label: str = "typedb.read",
    timeout_seconds: float = None,
    *,
    _bindings: GraphReadsExecutionRuntime
) -> List[Dict[str, object]]:
    started_at = time.perf_counter()
    rows: List[Dict[str, object]] = []
    status = "ok"
    try:
        query_timeout = (
            _store.query_timeout_seconds()
            if timeout_seconds is None
            else max(0.5, float(timeout_seconds))
        )
        with _bindings.typedb_operation_timeout(query_timeout, "TypeDB read query"):
            resolved = tx.query(query).resolve()
            for item in resolved:
                rows.append({name: _bindings.typedb_row_value(item, name) for name in columns})
            return rows
    except Exception:
        status = "error"
        raise
    finally:
        duration_ms = (time.perf_counter() - started_at) * 1000
        _store.record_query_metric(label, query, len(rows), duration_ms, status=status)
