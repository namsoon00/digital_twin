"""Replay the pre-knowledge catalog without rewriting immutable golden files."""
from contextlib import contextmanager
from unittest.mock import patch
import sys
from digital_twin.modules.model_registry.domain import ontology_tbox as catalog
from digital_twin.modules.model_registry.domain.ontology_research_knowledge import RESEARCH_KNOWLEDGE_CLASSES, RESEARCH_KNOWLEDGE_RELATIONS
from digital_twin.modules.reasoning.domain import ontology_schema as schema


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
