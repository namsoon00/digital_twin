"""Replay the pre-knowledge catalog without rewriting immutable golden files."""
from contextlib import contextmanager
from unittest.mock import patch
import sys
from digital_twin.modules.model_registry.domain import ontology_tbox as catalog
from digital_twin.modules.model_registry.domain.ontology_research_knowledge import RESEARCH_KNOWLEDGE_CLASSES, RESEARCH_KNOWLEDGE_RELATIONS
from digital_twin.modules.reasoning.domain import ontology_schema as schema


@contextmanager
def pre_operational_strategy_schema():
    """Recompile the previous physical capabilities without new phase rules."""
    from digital_twin.modules.reasoning.infrastructure.typeql import storage_schema
    previous = [r for r in storage_schema.default_graph_inference_rules()
                if not r.resolved_claim_contract.strategy_contract]
    storage_schema.typedb_rule_schema_capability_contract.cache_clear()
    try:
        with patch.object(storage_schema, 'default_graph_inference_rules', return_value=previous):
            yield
    finally:
        storage_schema.typedb_rule_schema_capability_contract.cache_clear()


@contextmanager
def pre_research_knowledge_catalog():
    def clear_caches():
        schema.tbox_fingerprint.cache_clear()
        schema._default_tbox_metadata_tuple.cache_clear()
        fixture = sys.modules.get("static_seed_fixture")
        if fixture is not None:
            fixture._artifact.cache_clear()
    classes = {row.name for row in RESEARCH_KNOWLEDGE_CLASSES}
    relations = {row.name for row in RESEARCH_KNOWLEDGE_RELATIONS}
    values = ((catalog.CLASS_DEFS, lambda row: row.name not in classes),
              (catalog.RELATION_DEFS, lambda row: row.name not in relations),
              (catalog.TBOX_CLASSES, lambda name: name not in classes),
              (catalog.TBOX_RELATION_TYPES, lambda name: name not in relations))
    saved = [list(rows) for rows, _ in values]
    try:
        for rows, predicate in values:
            rows[:] = [row for row in rows if predicate(row)]
        clear_caches()
        with patch.object(schema, "ONTOLOGY_TBOX_VERSION", "investment-tbox-v13-official-company-reference"):
            yield
    finally:
        for (rows, _), original in zip(values, saved):
            rows[:] = original
        clear_caches()


@contextmanager
def pre_composition_flag_schema():
    """Reproduce schemas authored before the two model booleans were promoted."""
    from digital_twin.modules.reasoning.infrastructure.typeql import constants, storage_schema
    previous = {k: v for k, v in constants.TYPEDB_PROMOTED_TEXT_ATTRIBUTES.items()
                if k not in {"contractMatched", "freshnessCompatible"}}
    storage_schema.typedb_rule_schema_capability_contract.cache_clear()
    try:
        with patch.dict(constants.TYPEDB_PROMOTED_TEXT_ATTRIBUTES, previous, clear=True):
            yield
    finally:
        storage_schema.typedb_rule_schema_capability_contract.cache_clear()
