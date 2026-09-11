"""Pure, resumable TypeQL storage-schema planning without a database driver."""
import re
from digital_twin.domain.ontology_semantics import semantic_class_types, semantic_relation_types

from typing import Dict, Iterable, List, Tuple
from .constants import DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE


def schema_definition_statements(schema_text: str) -> List[str]:
    """Split a TypeQL schema into complete top-level definitions.

    The base schema does not contain functions, but using a small lexical
    scanner here still avoids treating a semicolon inside a future quoted
    annotation as a transaction boundary.
    """
    statements: List[str] = []
    current: List[str] = []
    quote = ""
    escaped = False
    for character in str(schema_text or ""):
        if quote:
            current.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if character in {'"', "'"}:
            quote = character
            current.append(character)
            continue
        if character != ";":
            current.append(character)
            continue
        statement = "".join(current).strip()
        current = []
        statement = re.sub(r"^(?:define|redefine|undefine)\s+", "", statement, count=1)
        if statement:
            statements.append(statement + ";")
    return statements


def schema_definition_identity(statement: str) -> Tuple[str, str]:
    match = re.match(
        r"^\s*(attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
        str(statement or ""),
    )
    return (match.group(1), match.group(2)) if match else ("", "")


def schema_definition_clauses(statement: str) -> List[str]:
    """Return normalized comma-delimited clauses after a type header."""
    text = str(statement or "").strip().rstrip(";").strip()
    clauses: List[str] = []
    current: List[str] = []
    quote = ""
    escaped = False
    for character in text:
        if quote:
            current.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if character in {'"', "'"}:
            quote = character
            current.append(character)
            continue
        if character == ",":
            clauses.append(re.sub(r"\s+", " ", "".join(current)).strip())
            current = []
            continue
        current.append(character)
    if current:
        clauses.append(re.sub(r"\s+", " ", "".join(current)).strip())
    return [clause for clause in clauses[1:] if clause]


def schema_subtype_parent(statement: str) -> str:
    for clause in schema_definition_clauses(statement):
        match = re.match(r"^sub\s+([A-Za-z_][A-Za-z0-9_-]*)$", clause)
        if match:
            return match.group(1)
    return ""


def schema_topological_definitions(statements: Iterable[str]) -> List[str]:
    """Order subtype definitions after their parent without changing them."""
    by_name = {}
    source_order = []
    for statement in statements or []:
        _kind, name = schema_definition_identity(statement)
        if name and name not in by_name:
            by_name[name] = str(statement or "")
            source_order.append(name)
    ordered: List[str] = []
    visiting = set()
    visited = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ValueError("Cyclic TypeDB schema subtype dependency: " + name)
        visiting.add(name)
        parent = schema_subtype_parent(by_name[name])
        if parent in by_name:
            visit(parent)
        visiting.remove(name)
        visited.add(name)
        ordered.append(by_name[name])

    for name in source_order:
        visit(name)
    return ordered


def schema_definition_batches(items: Iterable[str], batch_size: int) -> List[List[str]]:
    rows = [str(item or "").strip() for item in items or [] if str(item or "").strip()]
    size = max(1, int(batch_size or 1))
    return [rows[index:index + size] for index in range(0, len(rows), size)]


def base_schema_bootstrap_plan(expected_schema_text: str, existing_schema_text: str='', batch_size: int=DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE) -> List[Dict[str, object]]:
    """Build a resumable, dependency-ordered base-schema write plan."""
    expected_statements = schema_definition_statements(expected_schema_text)
    existing_statements = schema_definition_statements(existing_schema_text)
    expected_by_identity = {
        schema_definition_identity(statement): statement
        for statement in expected_statements
        if all(schema_definition_identity(statement))
    }
    existing_by_identity = {
        schema_definition_identity(statement): statement
        for statement in existing_statements
        if all(schema_definition_identity(statement))
    }
    existing_names = {name for _kind, name in existing_by_identity}
    batch_size = max(1, min(2048, int(batch_size or DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE)))
    plan: List[Dict[str, object]] = []

    def append_definition_batches(phase: str, definitions: Iterable[str]) -> None:
        for batch in schema_definition_batches(definitions, batch_size):
            plan.append({
                "phase": phase,
                "definitionCount": len(batch),
                "query": "define\n" + "\n".join(batch),
            })

    missing_attributes = [
        statement
        for (kind, name), statement in expected_by_identity.items()
        if kind == "attribute" and name not in existing_names
    ]
    append_definition_batches("attributes", missing_attributes)

    assertion_identity = ("relation", "ontology-assertion")
    node_identity = ("entity", "ontology-node")
    expected_assertion = expected_by_identity.get(assertion_identity, "")
    expected_node = expected_by_identity.get(node_identity, "")
    if assertion_identity not in existing_by_identity:
        plan.append({
            "phase": "core-relation",
            "definitionCount": 1,
            "query": "define\nrelation ontology-assertion, relates source, relates target;",
        })
    if node_identity not in existing_by_identity:
        plan.append({
            "phase": "core-entity",
            "definitionCount": 1,
            "query": "define\nentity ontology-node @abstract;",
        })

    def missing_extension_clauses(identity: Tuple[str, str], expected_statement: str) -> List[str]:
        actual = existing_by_identity.get(identity, "")
        actual_clauses = set(schema_definition_clauses(actual))
        return [
            clause
            for clause in schema_definition_clauses(expected_statement)
            if clause.startswith(("owns ", "plays ")) and clause not in actual_clauses
        ]

    for identity, expected_statement, phase in [
        (assertion_identity, expected_assertion, "core-relation-ownership"),
        (node_identity, expected_node, "core-entity-contract"),
    ]:
        _kind, name = identity
        clauses = missing_extension_clauses(identity, expected_statement)
        for batch in schema_definition_batches(clauses, batch_size):
            plan.append({
                "phase": phase,
                "definitionCount": len(batch),
                "query": "define\n" + name + " " + ", ".join(batch) + ";",
            })

    semantic_entity_names = set(semantic_class_types().values())
    entity_definitions = schema_topological_definitions([
        statement
        for (kind, name), statement in expected_by_identity.items()
        if kind == "entity" and name != "ontology-node" and name not in existing_names
    ])
    append_definition_batches(
        "core-entity-subtypes",
        [
            statement for statement in entity_definitions
            if schema_definition_identity(statement)[1] not in semantic_entity_names
        ],
    )
    append_definition_batches(
        "semantic-entity-subtypes",
        [
            statement for statement in entity_definitions
            if schema_definition_identity(statement)[1] in semantic_entity_names
        ],
    )

    semantic_relation_names = set(semantic_relation_types().values())
    append_definition_batches(
        "semantic-relation-subtypes",
        [
            statement
            for (kind, name), statement in expected_by_identity.items()
            if kind == "relation"
            and name in semantic_relation_names
            and name not in existing_names
        ],
    )
    return plan
