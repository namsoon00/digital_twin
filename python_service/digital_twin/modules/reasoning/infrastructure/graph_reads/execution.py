"""graph_reads: execution through explicit injected capabilities."""

from typing import Dict, Iterable, List
from contextlib import contextmanager
from contextvars import ContextVar
import time
from .execution_ports import GraphReadsExecutionStore, GraphReadsExecutionRuntime


_READ_SNAPSHOT = ContextVar("typedb_read_snapshot", default=())


def current_snapshot(store):
    return next((item for item in reversed(_READ_SNAPSHOT.get()) if item[0] is store), None)


@contextmanager
def read_snapshot_scope(_store: GraphReadsExecutionStore, timeout_seconds=None):
    """Bind all reads of this repository to one bounded native READ snapshot."""
    existing = current_snapshot(_store)
    if existing is not None:
        yield
        return
    imported = _store.driver_imports()
    if imported[0] is None:
        raise RuntimeError("typedb-driver Python package is not installed")
    transaction_type = imported[0][-1]
    # Several bounded queries form one capture. Its total budget is separate
    # from each query's existing deadline, and never exceeds one minute.
    budget = max(0.5, min(60.0, float(
        max(30.0, 2 * _store.query_timeout_seconds()) if timeout_seconds is None else timeout_seconds)))
    deadline = time.monotonic() + budget
    # A capture has its own bounded channel; the shared writer's longer
    # deadline and failures must not keep an observation read alive.
    driver = _store.create_driver(imported, request_timeout_seconds=budget)
    try:
        remaining = deadline - time.monotonic()
        if remaining < 0.5:
            raise TimeoutError("TypeDB observation snapshot connection deadline exhausted")
        # Opening a READ must fail on a missing deployment, never create one.
        with driver.transaction(_store.database, transaction_type.READ,
                                _store.read_transaction_options(remaining)) as tx:
            token = _READ_SNAPSHOT.set((*_READ_SNAPSHOT.get(), (_store, tx, deadline)))
            try:
                yield
            finally:
                _READ_SNAPSHOT.reset(token)
    finally:
        _store.close_driver(driver)


def read_rows(
    _store: GraphReadsExecutionStore,
    query: str,
    columns: Iterable[str],
    label: str = "typedb.read",
    timeout_seconds: float = None,
) -> List[Dict[str, object]]:
    snapshot = current_snapshot(_store)
    if snapshot is not None:
        remaining = snapshot[2] - time.monotonic()
        if remaining < 0.5:
            raise TimeoutError("TypeDB observation snapshot deadline exhausted")
        requested = _store.query_timeout_seconds() if timeout_seconds is None else float(timeout_seconds)
        return _store.read_rows_in_transaction(snapshot[1], query, columns,
            label=label, timeout_seconds=min(requested, remaining))
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

    return _store.with_typedb_retries(operation, retry_if=retryable_read_error)


def retryable_read_error(error: Exception) -> bool:
    # Re-running the same expensive read immediately multiplies its load.
    # The owning worker supplies backoff; transport failures remain retryable.
    text = str(error).lower()
    return not (
        isinstance(error, TimeoutError)
        or "timed out" in text
        or "transaction timeout" in text
        or "[tsv17]" in text
        or ("[tsv13]" in text and "execution interrupted" in text)
    )


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
