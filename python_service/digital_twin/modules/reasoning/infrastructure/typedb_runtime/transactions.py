"""Operation-specific TypeDB transaction deadlines and driver compatibility."""

from .ports import TransactionPort


def write_transaction_options(store: TransactionPort):
    try:
        from typedb.driver import TransactionOptions
    except Exception:  # noqa: BLE001 - retain compatibility with older optional drivers.
        return None
    return TransactionOptions(
        transaction_timeout_millis=max(1000, int(store.write_operation_timeout_seconds() * 1000)),
    )


def read_transaction_options(store: TransactionPort, timeout_seconds: float=None):
    """Bound a TypeDB read transaction, not only the individual query call.

    A driver-side transaction deadline keeps one slow direct TypeQL rule
    from blocking the realtime reasoning worker indefinitely.
    """
    try:
        from typedb.driver import TransactionOptions
    except Exception:  # noqa: BLE001 - retain compatibility with older optional drivers.
        return None
    timeout = store.query_timeout_seconds() if timeout_seconds is None else max(0.5, float(timeout_seconds))
    return TransactionOptions(
        transaction_timeout_millis=max(1000, int(timeout * 1000)),
    )


def schema_transaction_options(store: TransactionPort, timeout_seconds: float=None):
    """Give TypeDB schema maintenance its configured operation deadline."""
    try:
        from typedb.driver import TransactionOptions
    except Exception:  # noqa: BLE001 - retain compatibility with older optional drivers.
        return None
    timeout = (
        store.schema_operation_timeout_seconds()
        if timeout_seconds is None
        else max(1.0, float(timeout_seconds))
    )
    timeout_millis = max(1000, int(timeout * 1000))
    return TransactionOptions(
        transaction_timeout_millis=timeout_millis,
        schema_lock_acquire_timeout_millis=timeout_millis,
    )


def schema_transaction(store: TransactionPort, driver, transaction_type, timeout_seconds: float=None):
    """Open a schema transaction while retaining older driver compatibility."""
    options = store.schema_transaction_options(timeout_seconds)
    if options is None:
        return driver.transaction(store.database, transaction_type)
    try:
        return driver.transaction(store.database, transaction_type, options=options)
    except TypeError as error:
        if "unexpected keyword" not in str(error).lower():
            raise
        return driver.transaction(store.database, transaction_type)
