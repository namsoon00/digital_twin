"""Driver lifetimes and bounded transport retries, with repository-owned state."""

from typing import Tuple

from .ports import ConnectionPort, TypeDBRuntime


def with_typedb_retries(store: ConnectionPort, operation, retry_if=None, *, runtime: TypeDBRuntime):
    attempts = max(1, store.retry_count + 1)
    last_error = None
    for index in range(attempts):
        try:
            return operation()
        except Exception as error:  # noqa: BLE001 - TypeDB connectivity can be transient.
            last_error = error
            # A driver can retain a broken native transport after a
            # server restart or a cancelled query.  Retrying through the
            # same instance only repeats the stall; drop it before the
            # next bounded attempt.
            store.invalidate_persistent_driver()
            if index >= attempts - 1 or (
                callable(retry_if) and not bool(retry_if(error))
            ):
                break
            runtime.sleep(min(2.0, 0.25 * (index + 1)))
    raise last_error


def driver_imports() -> Tuple[object, object]:
    try:
        from typedb.driver import Credentials, DriverOptions, DriverTlsConfig, TransactionType, TypeDB

        return (TypeDB, Credentials, DriverOptions, DriverTlsConfig, TransactionType), None
    except Exception as error:  # noqa: BLE001 - optional dependency.
        return None, error


def create_driver(store: ConnectionPort, imported, request_timeout_seconds: float=None):
    TypeDB, Credentials, DriverOptions, DriverTlsConfig, _TransactionType = imported[0]
    tls_config = DriverTlsConfig.enabled() if store.tls_enabled else DriverTlsConfig.disabled()
    request_timeout = (
        store.driver_request_timeout_seconds()
        if request_timeout_seconds is None
        else max(1.0, float(request_timeout_seconds))
    )
    return TypeDB.driver(
        store.address,
        Credentials(store.user, store.password),
        DriverOptions(
            tls_config,
            primary_failover_retries=max(0, min(2, store.retry_count)),
            # A write transaction can legitimately outlive an individual read
            # deadline while replacing a large ABox. Do not let the driver's
            # channel deadline invalidate that transaction before its explicit
            # write-operation timeout is reached.
            request_timeout_millis=max(1000, int(request_timeout * 1000)),
        ),
    )


def open_driver(store: ConnectionPort, imported, request_timeout_seconds: float=None):
    if not store._persistent_driver_enabled:
        return store.create_driver(imported, request_timeout_seconds=request_timeout_seconds)
    # Transaction-level options still enforce each read/write deadline.
    # Use the repository's longest declared operation timeout for the
    # shared channel so a short read does not poison a later valid ABox
    # write with a smaller driver-wide deadline.
    with store._persistent_driver_lock:
        if store._persistent_driver is not None:
            return store._persistent_driver
        store._persistent_driver = store.create_driver(
            imported,
            request_timeout_seconds=store.driver_request_timeout_seconds(),
        )
        return store._persistent_driver


def driver_request_timeout_seconds(store: ConnectionPort) -> float:
    return max(
        float(store.timeout_seconds or 0),
        float(store._query_timeout_seconds or 0),
        float(store._schema_operation_timeout_seconds or 0),
        float(store._write_operation_timeout_seconds or 0),
    )


def open_native_rule_read_driver(store: ConnectionPort, imported, request_timeout_seconds: float=None):
    """Open a channel whose deadline matches one bounded native-rule read.

    The process-wide driver intentionally inherits the longest write
    timeout. Reusing it in worker threads made a short rule transaction
    wait on that longer channel after the Python SIGALRM guard became
    unavailable. A dedicated channel keeps the transport and transaction
    deadlines aligned without affecting ABox writes.
    """
    if not store._persistent_driver_enabled or not store.native_rule_dedicated_read_driver_enabled():
        return store.open_driver(imported, request_timeout_seconds=request_timeout_seconds)
    return store.create_driver(
        imported,
        request_timeout_seconds=request_timeout_seconds,
    )


def close_native_rule_read_driver(store: ConnectionPort, driver) -> None:
    if not store._persistent_driver_enabled or not store.native_rule_dedicated_read_driver_enabled():
        store.close_driver(driver)
        return
    close = getattr(driver, "close", None)
    if callable(close):
        close()


def close_driver(store: ConnectionPort, driver) -> None:
    if store._persistent_driver_enabled:
        with store._persistent_driver_lock:
            if driver is store._persistent_driver:
                return
    close = getattr(driver, "close", None)
    if callable(close):
        close()


def invalidate_persistent_driver(store: ConnectionPort) -> None:
    """Drop a cached native channel after an operation failure.

    This is intentionally idempotent because nested repository calls may
    observe the same transport error while unwinding.
    """
    if not store._persistent_driver_enabled:
        return
    with store._persistent_driver_lock:
        driver = store._persistent_driver
        store._persistent_driver = None
    close = getattr(driver, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def ensure_database(store: ConnectionPort, driver) -> None:
    databases = getattr(driver, "databases", None)
    if databases is None:
        store._database_created_in_process = False
        return
    try:
        contains = getattr(databases, "contains", None)
        if callable(contains) and contains(store.database):
            store._database_created_in_process = False
            return
    except Exception:
        pass
    try:
        databases.create(store.database)
        store._database_created_in_process = True
        store.invalidate_process_base_schema_readiness()
    except Exception as error:
        store._database_created_in_process = False
        if "already" not in str(error).lower() and "exist" not in str(error).lower():
            raise
