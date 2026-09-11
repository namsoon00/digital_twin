"""Prepare a storage schema without publishing or executing investment rules."""
import hashlib
import re
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology

from typing import Dict

from .ports import SchemaLifecyclePort, TypeDBRuntime


def ensure_schema(store: SchemaLifecyclePort, driver, imported, *, runtime: TypeDBRuntime) -> None:
    schema = store.schema_query()
    schema_fingerprint = hashlib.sha256(schema.encode("utf-8")).hexdigest()
    if store._base_schema_ready_fingerprint == schema_fingerprint:
        return
    if store.process_base_schema_is_ready(schema_fingerprint):
        store._base_schema_ready_fingerprint = schema_fingerprint
        return
    # A blue-green candidate is created from an empty storage directory.
    # Probing types that cannot exist yet only spends the full driver
    # timeout and can poison the first connection. Bootstrap a database
    # created by this process directly. If the database already existed,
    # a previous bootstrap may have persisted only some batches, so read
    # that partial schema and resume from the first missing definition.
    schema_text = ""
    if store._fresh_candidate_rebuild:
        if not store._database_created_in_process:
            # Fail closed when an existing candidate cannot be inspected.
            # Retrying from a blank schema would redefine completed
            # batches, hide the original connectivity problem, and turn a
            # bounded restart into another long bootstrap loop.
            with runtime.timeout(
                store._fresh_schema_bootstrap_timeout_seconds,
                "TypeDB partial candidate schema inspection",
            ):
                schema_text = store.typedb_schema_text(driver)
            schema_type_names = set(re.findall(
                r"^\s*(?:attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
                schema_text,
                flags=re.MULTILINE,
            ))
            if store.base_schema_type_names().issubset(schema_type_names):
                store._base_schema_ready_fingerprint = schema_fingerprint
                store.mark_process_base_schema_ready(schema_fingerprint)
                return
        if store.http_address:
            store.synchronize_base_schema_batches_http(
                schema_text,
                batch_size=store._fresh_schema_bootstrap_batch_size,
                operation_timeout_seconds=store._fresh_schema_bootstrap_timeout_seconds,
            )
        else:
            store.synchronize_base_schema_batches(
                driver,
                imported,
                schema_text,
                batch_size=store._fresh_schema_bootstrap_batch_size,
                operation_timeout_seconds=store._fresh_schema_bootstrap_timeout_seconds,
            )
        store._base_schema_ready_fingerprint = schema_fingerprint
        store.mark_process_base_schema_ready(schema_fingerprint)
        return
    try:
        schema_text = store.typedb_schema_text(driver)
        schema_type_names = set(re.findall(
            r"^\s*(?:attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
            schema_text,
            flags=re.MULTILINE,
        ))
        # A new database has no ontology types yet. Reading the static
        # manifest first issues a query against those missing types and
        # can invalidate the driver's connection before this schema write.
        if store.base_schema_type_names().issubset(schema_type_names):
            contract_state = store.base_schema_contract_state()
            if str(contract_state.get("status") or "") == "current":
                store._base_schema_ready_fingerprint = schema_fingerprint
                store.mark_process_base_schema_ready(schema_fingerprint)
                return
        # Migrations extend the generic ontology storage types. A blank
        # database has neither type, so its first schema write must be
        # the complete base contract instead of an additive migration.
        if {"ontology-node", "ontology-assertion"}.issubset(schema_type_names):
            if store.ontology_storage_identity_migration_required(schema_text):
                store.migrate_ontology_storage_identity(driver, imported, schema_text)
                schema_text = store.typedb_schema_text(driver)
            if store.ontology_scope_schema_migration_required(schema_text):
                store.migrate_ontology_scope_schema(driver, imported)
                schema_text = store.typedb_schema_text(driver)
            if store.ontology_content_fingerprint_schema_migration_required(
                schema_text
            ):
                store.migrate_ontology_content_fingerprint_schema(
                    driver,
                    imported,
                    schema_text,
                )
                schema_text = store.typedb_schema_text(driver)
            if store.ontology_world_schema_migration_required(schema_text):
                store.migrate_ontology_world_schema(driver, imported)
                schema_text = store.typedb_schema_text(driver)
            if store.promoted_schema_migration_required(schema_text):
                store.migrate_promoted_schema(driver, imported, schema_text)
                schema_text = store.typedb_schema_text(driver)
            if store.ontology_semantic_schema_migration_required(schema_text):
                store.migrate_ontology_semantic_schema(driver, imported, schema_text)
                schema_text = store.typedb_schema_text(driver)
        schema_type_names = set(re.findall(
            r"^\s*(?:attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
            schema_text,
            flags=re.MULTILINE,
        ))
        if store.base_schema_type_names().issubset(schema_type_names):
            store._base_schema_ready_fingerprint = schema_fingerprint
            store.mark_process_base_schema_ready(schema_fingerprint)
            return
    except Exception:
        # On a new database or an older driver without schema inspection,
        # fall through to the idempotent schema definition below.
        pass
    store.synchronize_base_schema_batches(driver, imported, schema_text)
    store._base_schema_ready_fingerprint = schema_fingerprint
    store.mark_process_base_schema_ready(schema_fingerprint)


def sync_base_schema_contract(store: SchemaLifecyclePort, *, runtime: TypeDBRuntime) -> Dict[str, object]:
    """Synchronise the TypeDB storage schema after a contract change.

    This is intentionally separate from the immutable static graph write:
    adding a promoted attribute must not rewrite TBox, RuleBox, or the
    live ABox. The manifest records the successful contract afterwards,
    keeping future restarts on the bounded sentinel path.
    """
    expected = store.base_schema_contract_metadata()
    if not store.address:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
            **expected,
        }
    imported = store.driver_imports()
    if imported[0] is None:
        result = store.driver_missing_result(imported[1], PortfolioOntology("typedb-schema-contract"))
        result.update(expected)
        return result
    started_at = runtime.perf_counter()
    store._last_base_schema_sync = {}
    try:
        def operation():
            driver = store.open_driver(imported)
            try:
                store.ensure_database(driver)
                store.ensure_schema(driver, imported)
            finally:
                store.close_driver(driver)

        store.with_typedb_retries(operation)
    except Exception as error:  # noqa: BLE001 - never execute a RuleBox against a partial schema.
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": runtime.error_code(error),
            "reason": str(error)[:240],
            "durationMs": int((runtime.perf_counter() - started_at) * 1000),
            **expected,
        }
    return {
        "configured": True,
        "saved": True,
        "status": "ok",
        "graphStore": "typedb",
        "durationMs": int((runtime.perf_counter() - started_at) * 1000),
        "schemaSync": dict(store._last_base_schema_sync or {
            "mode": "current-schema-contract",
            "queryCount": 0,
            "definitionCount": 0,
        }),
        **expected,
    }
