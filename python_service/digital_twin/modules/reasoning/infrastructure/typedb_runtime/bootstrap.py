"""Bounded schema commits over native and HTTP transports."""

from typing import Dict

from .ports import SchemaBootstrapPort, TypeDBRuntime
from .constants import DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE


def synchronize_base_schema_batches(store: SchemaBootstrapPort, driver, imported, schema_text: str='', batch_size: int=DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE, operation_timeout_seconds: float=None, *, runtime: TypeDBRuntime) -> Dict[str, object]:
    """Apply a cold or partially written schema through bounded commits."""
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    bounded_batch_size = max(1, min(2048, int(batch_size or DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE)))
    plan = store.base_schema_bootstrap_plan(schema_text, batch_size=bounded_batch_size)
    phase_counts: Dict[str, int] = {}
    phase_durations_ms: Dict[str, int] = {}
    batch_metrics = []
    started_at = runtime.perf_counter()
    timeout_seconds = max(
        1.0,
        float(operation_timeout_seconds or store.schema_operation_timeout_seconds()),
    )
    for index, item in enumerate(plan, start=1):
        phase = str(item.get("phase") or "schema")
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
        query = str(item.get("query") or "")
        batch_started_at = runtime.perf_counter()
        with runtime.timeout(
            timeout_seconds,
            "TypeDB base schema batch " + phase,
        ):
            with store.schema_transaction(
                driver,
                TransactionType.SCHEMA,
                timeout_seconds=timeout_seconds,
            ) as tx:
                tx.query(query).resolve()
                tx.commit()
        duration_ms = int((runtime.perf_counter() - batch_started_at) * 1000)
        phase_durations_ms[phase] = phase_durations_ms.get(phase, 0) + duration_ms
        batch_metrics.append({
            "batch": index,
            "phase": phase,
            "definitionCount": int(item.get("definitionCount") or 0),
            "queryBytes": len(query.encode("utf-8")),
            "durationMs": duration_ms,
        })
    result = {
        "mode": "bounded-schema-batches",
        "batchSize": bounded_batch_size,
        "operationTimeoutSeconds": timeout_seconds,
        "queryCount": len(plan),
        "definitionCount": sum(int(item.get("definitionCount") or 0) for item in plan),
        "phaseQueryCounts": phase_counts,
        "phaseDurationsMs": phase_durations_ms,
        "batches": batch_metrics,
        "durationMs": int((runtime.perf_counter() - started_at) * 1000),
        "resumed": bool(str(schema_text or "").strip() not in {"", "define"}),
    }
    store._last_base_schema_sync = dict(result)
    return result


def synchronize_base_schema_batches_http(store: SchemaBootstrapPort, schema_text: str='', batch_size: int=DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE, operation_timeout_seconds: float=None, *, runtime: TypeDBRuntime) -> Dict[str, object]:
    """Bootstrap a fresh schema over HTTP when a long gRPC commit loses keep-alive."""

    bounded_batch_size = max(1, min(2048, int(batch_size or DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE)))
    timeout_seconds = max(
        1.0,
        float(operation_timeout_seconds or store.schema_operation_timeout_seconds()),
    )
    signin = store.typedb_http_json_request(
        "/v1/signin",
        {"username": store.user, "password": store.password},
        min(timeout_seconds, 30.0),
    )
    token = str(signin.get("token") or "").strip()
    if not token:
        raise RuntimeError("TypeDB HTTP sign-in did not return an access token.")
    plan = store.base_schema_bootstrap_plan(schema_text, batch_size=bounded_batch_size)
    phase_counts: Dict[str, int] = {}
    phase_durations_ms: Dict[str, int] = {}
    batch_metrics = []
    started_at = runtime.perf_counter()
    for index, item in enumerate(plan, start=1):
        phase = str(item.get("phase") or "schema")
        query = str(item.get("query") or "")
        batch_started_at = runtime.perf_counter()
        try:
            store.typedb_http_json_request(
                "/v1/query",
                {
                    "databaseName": store.database,
                    "transactionType": "schema",
                    "query": query,
                    "commit": True,
                    "transactionOptions": {
                        "transactionTimeoutMillis": int(timeout_seconds * 1000),
                        "schemaLockAcquireTimeoutMillis": int(timeout_seconds * 1000),
                    },
                },
                timeout_seconds,
                token=token,
            )
        except Exception as error:
            raise RuntimeError(
                "TypeDB HTTP base schema batch failed at "
                + str(index) + "/" + str(len(plan)) + " (" + phase + "): " + str(error)
            ) from error
        duration_ms = int((runtime.perf_counter() - batch_started_at) * 1000)
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
        phase_durations_ms[phase] = phase_durations_ms.get(phase, 0) + duration_ms
        batch_metrics.append({
            "batch": index,
            "phase": phase,
            "definitionCount": int(item.get("definitionCount") or 0),
            "queryBytes": len(query.encode("utf-8")),
            "durationMs": duration_ms,
        })
    result = {
        "mode": "http-bounded-schema-batches",
        "batchSize": bounded_batch_size,
        "operationTimeoutSeconds": timeout_seconds,
        "queryCount": len(plan),
        "definitionCount": sum(int(item.get("definitionCount") or 0) for item in plan),
        "phaseQueryCounts": phase_counts,
        "phaseDurationsMs": phase_durations_ms,
        "batches": batch_metrics,
        "durationMs": int((runtime.perf_counter() - started_at) * 1000),
        "resumed": bool(str(schema_text or "").strip() not in {"", "define"}),
    }
    store._last_base_schema_sync = dict(result)
    return result
