"""Scope clauses for TypeQL, without database execution."""

import re

from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string


def typedb_active_abox_pointer_clause(
    snapshot_variable: str = "$activeAboxSnapshotId",
    pointer_variable: str = "$activeAboxPointer",
    world_id: str = "",
    world_id_variable: str = "",
) -> str:
    """Match the one control record that selects the live ABox generation."""
    world_attribute = typedb_world_id_attribute_variable(pointer_variable, world_id_variable)
    return (
        str(pointer_variable or "$activeAboxPointer")
        + ' isa ontology-node, has ontology-kind "abox-active-pointer", '
        + 'has ontology-box "ABoxControl"'
        + typedb_world_id_constraint(world_id, world_id_variable, world_attribute)
        + ", has ontology-snapshot-id "
        + str(snapshot_variable or "$activeAboxSnapshotId")
        + "; "
        + typedb_world_id_value_match(world_id_variable, world_attribute)
    )


def typedb_world_id_attribute_variable(owner_variable: str, world_id_variable: str) -> str:
    """Return a unique TypeQL attribute variable for a world-id value input."""
    owner = re.sub(r"[^A-Za-z0-9_]", "", str(owner_variable or "item").lstrip("$")) or "item"
    value = re.sub(r"[^A-Za-z0-9_]", "", str(world_id_variable or "world").lstrip("$")) or "world"
    return "$" + owner + value[:1].upper() + value[1:] + "Attribute"


def typedb_world_id_constraint(
    world_id: str = "",
    world_id_variable: str = "",
    world_attribute_variable: str = "",
) -> str:
    """Return a literal or parameterized TypeQL world ownership constraint.

    Explicit portfolio worlds pass their identifier as a typed TypeQL function
    argument.  That keeps the native RuleBox schema at one function per rule,
    rather than creating the same function once per account.  Legacy reads
    still use the literal/no-world form during the rolling migration.
    """
    variable = str(world_id_variable or "").strip()
    if variable:
        attribute = str(world_attribute_variable or "").strip() or typedb_world_id_attribute_variable(
            "item",
            variable,
        )
        return ", has ontology-world-id " + attribute
    value = str(world_id or "").strip()
    return ", has ontology-world-id " + typedb_string(value) if value else ""


def typedb_world_id_value_match(world_id_variable: str = "", world_attribute_variable: str = "") -> str:
    """Match a TypeQL attribute object's primitive value to a function input."""
    variable = str(world_id_variable or "").strip()
    attribute = str(world_attribute_variable or "").strip()
    return attribute + " == " + variable + ";" if variable and attribute else ""


def typedb_active_abox_snapshot_clause(
    variable: str,
    snapshot_variable: str = "$activeAboxSnapshotId",
) -> str:
    return str(variable or "$item") + " has ontology-snapshot-id " + str(snapshot_variable or "$activeAboxSnapshotId") + ";"


def typedb_active_worldview_manifest_clause(
    manifest_variable: str = "$activeManifestPointer",
    manifest_id_variable: str = "$activeManifestId",
    world_id: str = "",
    world_id_variable: str = "",
) -> str:
    """Match the single control record selecting the live scoped ABox world."""
    world_attribute = typedb_world_id_attribute_variable(manifest_variable, world_id_variable)
    return (
        str(manifest_variable or "$activeManifestPointer")
        + ' isa ontology-node, has ontology-kind "worldview-manifest-active-pointer", '
        # Join through the same attribute type as the per-scope pointer. A
        # TypeQL variable cannot bridge values from two differently named
        # string attributes (snapshot id and manifest id), even when both
        # contain the same text.
        + 'has ontology-box "ABoxControl"'
        + typedb_world_id_constraint(world_id, world_id_variable, world_attribute)
        + ", has ontology-manifest-id "
        + str(manifest_id_variable or "$activeManifestId")
        + "; "
        + typedb_world_id_value_match(world_id_variable, world_attribute)
    )


def typedb_active_scoped_abox_member_clause(
    variable: str,
    prefix: str,
    manifest_id_variable: str = "",
    world_id: str = "",
    world_id_variable: str = "",
) -> str:
    """Constrain one node or relation to the active Manifest scope pointer."""
    clean_prefix = re.sub(r"[^A-Za-z0-9_]", "", str(prefix or "item")) or "item"
    manifest_pointer = "$" + clean_prefix + "ActiveManifestPointer"
    manifest_id = str(manifest_id_variable or "$" + clean_prefix + "ActiveManifestId")
    return (
        typedb_active_worldview_manifest_clause(
            manifest_pointer,
            manifest_id,
            world_id,
            world_id_variable,
        )
        + " "
        + typedb_scoped_manifest_member_clause(
            variable,
            clean_prefix,
            manifest_id,
            world_id,
            world_id_variable,
        )
    )


def typedb_scoped_manifest_member_clause(
    variable: str,
    prefix: str,
    manifest_id_variable: str,
    world_id: str = "",
    world_id_variable: str = "",
) -> str:
    """Constrain one fact through the active Manifest's scope pointer.

    A scope generation can be reused by many immutable Worldview Manifests.
    Its stored JSON and original `ontology-manifest-id` therefore describe the
    generation's creation provenance, not its current membership. The active
    Manifest pointer gates the world as a whole, while each durable scope
    pointer selects its independently versioned generation. This deliberately
    avoids rewriting every unchanged scope pointer whenever a Manifest id
    changes for one symbol.
    """
    clean_prefix = re.sub(r"[^A-Za-z0-9_]", "", str(prefix or "item")) or "item"
    scope_pointer = "$" + clean_prefix + "ScopePointer"
    scope_id = "$" + clean_prefix + "ScopeId"
    scope_generation = "$" + clean_prefix + "ScopeGenerationId"
    scope_world_attribute = typedb_world_id_attribute_variable(scope_pointer, world_id_variable)
    member_world_attribute = typedb_world_id_attribute_variable(variable, world_id_variable)
    return (
        scope_pointer + ' isa ontology-node, has ontology-kind "abox-scope-active-pointer", '
        + 'has ontology-box "ABoxControl"'
        + typedb_world_id_constraint(world_id, world_id_variable, scope_world_attribute)
        + ", has ontology-scope-id " + scope_id
        + ", has ontology-snapshot-id " + scope_generation + "; "
        + typedb_world_id_value_match(world_id_variable, scope_world_attribute)
        + " "
        + str(variable or "$item") + ' has ontology-box "ABox"'
        + typedb_world_id_constraint(world_id, world_id_variable, member_world_attribute)
        + ", has ontology-scope-id "
        + scope_id + ", has ontology-snapshot-id " + scope_generation + "; "
        + typedb_world_id_value_match(world_id_variable, member_world_attribute)
    )


def typedb_active_abox_member_clause(
    variable: str,
    prefix: str,
    world_id: str = "",
    world_id_variable: str = "",
) -> str:
    """Match one fact from either supported live-ABox activation format.

    Scoped Manifests are the current format.  Keeping the legacy pointer as a
    read-only branch lets a rolling worker update its native TypeDB functions
    before the first scoped ABox projection completes, without treating an
    incomplete Manifest as live data.
    """
    clean_prefix = re.sub(r"[^A-Za-z0-9_]", "", str(prefix or "item")) or "item"
    legacy_pointer = "$" + clean_prefix + "LegacyAboxPointer"
    legacy_snapshot = "$" + clean_prefix + "LegacyAboxSnapshotId"
    scoped = typedb_active_scoped_abox_member_clause(
        variable,
        clean_prefix + "Scoped",
        world_id=world_id,
        world_id_variable=world_id_variable,
    )
    legacy = (
        typedb_active_abox_pointer_clause(
            legacy_snapshot,
            legacy_pointer,
            world_id,
            world_id_variable,
        )
        + " " + str(variable or "$item")
        + ' has ontology-box "ABox"'
        + typedb_world_id_constraint(
            world_id,
            world_id_variable,
            typedb_world_id_attribute_variable(variable, world_id_variable),
        )
        + ", has ontology-snapshot-id " + legacy_snapshot + "; "
        + typedb_world_id_value_match(
            world_id_variable,
            typedb_world_id_attribute_variable(variable, world_id_variable),
        )
    )
    return "{ " + scoped + " } or { " + legacy + " };"
