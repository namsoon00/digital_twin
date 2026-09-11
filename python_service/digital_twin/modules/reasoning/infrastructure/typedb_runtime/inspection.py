"""Storage schema inspection and persisted contract comparison."""
import re

from typing import Dict

from .ports import SchemaInspectionPort


def base_schema_type_names(store: SchemaInspectionPort) -> set:
    if store._base_schema_type_names:
        return set(store._base_schema_type_names)
    names = set(re.findall(
        r"^\s*(?:attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
        store.schema_query(),
        flags=re.MULTILINE,
    ))
    store._base_schema_type_names = names
    return set(names)


def typedb_schema_type_names(store: SchemaInspectionPort, driver) -> set:
    schema_text = store.typedb_schema_text(driver)
    return set(re.findall(
        r"^\s*(?:attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
        schema_text,
        flags=re.MULTILINE,
    ))


def typedb_schema_text(store: SchemaInspectionPort, driver) -> str:
    databases = getattr(driver, "databases", None)
    get_database = getattr(databases, "get", None) if databases is not None else None
    if not callable(get_database):
        raise RuntimeError("TypeDB database schema listing is unavailable.")
    database = get_database(store.database)
    schema_reader = getattr(database, "type_schema", None)
    if not callable(schema_reader):
        schema_reader = getattr(database, "schema", None)
    if not callable(schema_reader):
        raise RuntimeError("TypeDB database schema reader is unavailable.")
    return str(schema_reader() or "")


def base_schema_contract_state(store: SchemaInspectionPort) -> Dict[str, object]:
    """Return the persisted base-schema contract without listing TypeDB schema.

    TypeDB's schema catalogue includes every generated native RuleBox
    function.  Serialising that catalogue on a large live ABox is far more
    expensive than the keyed static-manifest read that already protects
    immutable TBox/RuleBox generations.  The manifest is published only
    after the matching schema sync succeeds, so it is a safe readiness
    marker for normal runtime requests.
    """
    expected = store.base_schema_contract_metadata()
    manifest = store.read_seed_static_manifest()
    metadata = dict(manifest.get("metadata") or {})
    status = str(manifest.get("status") or "")
    if status != "ok":
        return {
            "status": "unavailable" if status == "error" else "missing",
            "manifestStatus": status or "missing",
            "metadata": metadata,
            **expected,
        }
    stored_version = str(metadata.get("schemaContractVersion") or "")
    stored_fingerprint = str(metadata.get("schemaContractFingerprint") or "")
    matches = (
        stored_version == str(expected.get("schemaContractVersion") or "")
        and stored_fingerprint == str(expected.get("schemaContractFingerprint") or "")
    )
    return {
        "status": "current" if matches else "stale",
        "manifestStatus": status,
        "metadata": metadata,
        "storedSchemaContractVersion": stored_version,
        "storedSchemaContractFingerprint": stored_fingerprint,
        **expected,
    }
