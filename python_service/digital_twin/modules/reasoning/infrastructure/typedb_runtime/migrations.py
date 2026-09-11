"""Additive storage-schema migrations; no investment rule execution."""
import re
from digital_twin.modules.reasoning.domain.ontology_semantics import semantic_storage_type_names, semantic_typeql_schema, typedb_context_type
from digital_twin.modules.reasoning.infrastructure.typeql.constants import TYPEDB_NUMERIC_ATTRIBUTES, TYPEDB_STRING_ATTRIBUTES, TYPEDB_COMMON_NODE_ATTRIBUTES
from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import typedb_rule_schema_capability_contract

from .ports import SchemaMigrationPort, TypeDBRuntime


def ontology_storage_identity_migration_required(schema_text: str) -> bool:
    text = str(schema_text or "")
    if "ontology-node" not in text or "ontology-assertion" not in text:
        return False
    return "ontology-storage-id" not in text or "owns ontology-id @key" in text


def migrate_ontology_storage_identity(store: SchemaMigrationPort, driver, imported, schema_text: str, *, runtime: TypeDBRuntime) -> None:
    """Separate graph-storage identity from the canonical ontology identifier.

    ABox staging intentionally contains the same real-world facts as the
    active ABox. ``ontology-id`` is therefore a domain identifier, not a
    globally unique database key. Older databases made it a TypeDB key,
    preventing a verified staging generation from coexisting with the
    active generation during an atomic promotion.
    """
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    queries = []
    if "owns ontology-id @key" in str(schema_text or ""):
        queries.append(
            "undefine @key from ontology-node owns ontology-id; "
            "@key from ontology-assertion owns ontology-id;"
        )
    queries.append(
        "define attribute ontology-storage-id, value string; "
        "ontology-node owns ontology-storage-id @unique; "
        "ontology-assertion owns ontology-storage-id @unique;"
    )
    with runtime.timeout(store.schema_operation_timeout_seconds(), "TypeDB storage identity schema migration"):
        with driver.transaction(store.database, TransactionType.SCHEMA) as tx:
            for query in queries:
                tx.query(query).resolve()
            tx.commit()


def ontology_scope_schema_migration_required(schema_text: str) -> bool:
    text = str(schema_text or "")
    if "ontology-node" not in text or "ontology-assertion" not in text:
        return False
    return any(attribute not in text for attribute in [
        "ontology-scope-id",
        "ontology-scope-type",
        "ontology-manifest-id",
    ])


def migrate_ontology_scope_schema(store: SchemaMigrationPort, driver, imported, *, runtime: TypeDBRuntime) -> None:
    """Add non-destructive attributes required by scoped ABox manifests."""
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    query = (
        "define "
        "attribute ontology-scope-id, value string; "
        "attribute ontology-scope-type, value string; "
        "attribute ontology-manifest-id, value string; "
        "ontology-node owns ontology-scope-id, owns ontology-scope-type, owns ontology-manifest-id; "
        "ontology-assertion owns ontology-scope-id, owns ontology-scope-type, owns ontology-manifest-id;"
    )
    with runtime.timeout(store.schema_operation_timeout_seconds(), "TypeDB scoped ABox schema migration"):
        with driver.transaction(store.database, TransactionType.SCHEMA) as tx:
            tx.query(query).resolve()
            tx.commit()


def ontology_content_fingerprint_schema_migration_required(schema_text: str) -> bool:
    text = str(schema_text or "")
    if "ontology-node" not in text or "ontology-assertion" not in text:
        return False
    return (
        "attribute ontology-content-fingerprint" not in text
        or not re.search(
            r"ontology-node[\s\S]*?owns ontology-content-fingerprint",
            text,
        )
        or not re.search(
            r"ontology-assertion[\s\S]*?owns ontology-content-fingerprint",
            text,
        )
    )


def migrate_ontology_content_fingerprint_schema(store: SchemaMigrationPort, driver, imported, schema_text: str, *, runtime: TypeDBRuntime) -> None:
    """Add the content identity used by current-state delta writes."""

    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    text = str(schema_text or "")
    definitions = []
    if "attribute ontology-content-fingerprint" not in text:
        definitions.append(
            "attribute ontology-content-fingerprint, value string;"
        )
    if not re.search(
        r"ontology-node[\s\S]*?owns ontology-content-fingerprint",
        text,
    ):
        definitions.append(
            "ontology-node owns ontology-content-fingerprint;"
        )
    if not re.search(
        r"ontology-assertion[\s\S]*?owns ontology-content-fingerprint",
        text,
    ):
        definitions.append(
            "ontology-assertion owns ontology-content-fingerprint;"
        )
    if not definitions:
        return
    with runtime.timeout(
        store.schema_operation_timeout_seconds(),
        "TypeDB current-state content fingerprint schema migration",
    ):
        with driver.transaction(store.database, TransactionType.SCHEMA) as tx:
            tx.query("define " + " ".join(definitions)).resolve()
            tx.commit()


def ontology_world_schema_migration_required(schema_text: str) -> bool:
    text = str(schema_text or "")
    if "ontology-node" not in text or "ontology-assertion" not in text:
        return False
    return any(attribute not in text for attribute in [
        "ontology-tenant-id",
        "ontology-world-id",
        "ontology-world-type",
    ])


def migrate_ontology_world_schema(store: SchemaMigrationPort, driver, imported, *, runtime: TypeDBRuntime) -> None:
    """Add explicit tenant/world ownership without rewriting existing ABox rows."""
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    query = (
        "define "
        "attribute ontology-tenant-id, value string; "
        "attribute ontology-world-id, value string; "
        "attribute ontology-world-type, value string; "
        "ontology-node owns ontology-tenant-id, owns ontology-world-id, owns ontology-world-type; "
        "ontology-assertion owns ontology-tenant-id, owns ontology-world-id, owns ontology-world-type;"
    )
    with runtime.timeout(store.schema_operation_timeout_seconds(), "TypeDB ontology world schema migration"):
        with driver.transaction(store.database, TransactionType.SCHEMA) as tx:
            tx.query(query).resolve()
            tx.commit()


def promoted_schema_migration_required(schema_text: str) -> bool:
    text = str(schema_text or "")
    if "ontology-node" not in text:
        return False
    promoted = set(
        typedb_rule_schema_capability_contract().get("directQueryAttributes") or []
    )
    return any(
        attribute not in text or not re.search(r"\bowns\s+" + re.escape(attribute) + r"\b", text)
        for attribute in promoted
    )


def migrate_promoted_schema(store: SchemaMigrationPort, driver, imported, schema_text: str, *, runtime: TypeDBRuntime) -> None:
    """Add only new RuleBox-queryable attributes to an existing schema."""

    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    text = str(schema_text or "")
    numeric = set(TYPEDB_NUMERIC_ATTRIBUTES)
    string = set(TYPEDB_STRING_ATTRIBUTES)
    contract = typedb_rule_schema_capability_contract()
    direct = set(contract.get("directQueryAttributes") or [])
    missing_types = sorted(attribute for attribute in direct if attribute not in text)
    definitions = [
        "attribute " + attribute + ", value " + ("double" if attribute in numeric else "string") + ";"
        for attribute in missing_types
    ]
    common_missing = sorted(
        attribute for attribute in TYPEDB_COMMON_NODE_ATTRIBUTES
        if attribute in direct
        and not re.search(r"ontology-node[\s\S]*?\bowns\s+" + re.escape(attribute) + r"\b", text)
    )
    if common_missing:
        definitions.append("ontology-node " + ", ".join("owns " + item for item in common_missing) + ";")
    for context, attributes in sorted(dict(contract.get("contextAttributes") or {}).items()):
        context_type = typedb_context_type(context)
        missing = sorted(
            attribute for attribute in attributes
            if not re.search(
                re.escape(context_type) + r"[^;]*\bowns\s+" + re.escape(attribute) + r"\b",
                text,
            )
        )
        if missing and context_type in text:
            definitions.append(context_type + " " + ", ".join("owns " + item for item in missing) + ";")
    if not definitions:
        return
    query = "define\n" + "\n".join(definitions)
    with runtime.timeout(store.schema_operation_timeout_seconds(), "TypeDB promoted attribute schema migration"):
        with driver.transaction(store.database, TransactionType.SCHEMA) as tx:
            tx.query(query).resolve()
            tx.commit()


def ontology_semantic_schema_migration_required(schema_text: str) -> bool:
    """Whether the physical TypeDB hierarchy still flattens logical types.

    The logical TBox is versioned independently from the TypeDB schema.
    This check keeps the migration additive: existing generic instances
    remain readable while every new write receives a class/relation
    subtype derived from the active TBox.
    """
    type_names = set(re.findall(
        r"^\s*(?:attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
        str(schema_text or ""),
        flags=re.MULTILINE,
    ))
    contract = typedb_rule_schema_capability_contract()
    desired = semantic_storage_type_names(
        contract.get("physicalClassNames") or [],
        contract.get("physicalRelationNames") or [],
    )
    return not desired.issubset(type_names)


def migrate_ontology_semantic_schema(store: SchemaMigrationPort, driver, imported, schema_text: str, *, runtime: TypeDBRuntime) -> None:
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    existing = set(re.findall(
        r"^\s*(?:attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
        str(schema_text or ""),
        flags=re.MULTILINE,
    ))
    contract = typedb_rule_schema_capability_contract()
    missing = semantic_storage_type_names(
        contract.get("physicalClassNames") or [],
        contract.get("physicalRelationNames") or [],
    ) - existing
    definitions = []
    if "ontology-semantic-type" in missing:
        definitions.extend([
            "attribute ontology-semantic-type, value string;",
            "ontology-node owns ontology-semantic-type;",
            "ontology-assertion owns ontology-semantic-type;",
        ])
        missing.discard("ontology-semantic-type")
    semantic_definitions = semantic_typeql_schema(
        missing,
        context_attribute_ownership=contract.get("contextAttributes") or {},
        physical_class_names=contract.get("physicalClassNames") or [],
        physical_relation_names=contract.get("physicalRelationNames") or [],
    )
    if semantic_definitions:
        definitions.append(semantic_definitions.replace("define\n", "", 1))
    if not definitions:
        return
    query = "define\n" + "\n".join(definitions)
    with runtime.timeout(store.schema_operation_timeout_seconds(), "TypeDB semantic schema migration"):
        with driver.transaction(store.database, TransactionType.SCHEMA) as tx:
            tx.query(query).resolve()
            tx.commit()
