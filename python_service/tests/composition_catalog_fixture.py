"""Replay the frozen pre-composition vocabulary without editing old goldens."""
from contextlib import contextmanager
from unittest.mock import patch
import sys

from digital_twin.modules.model_registry.domain import ontology_tbox as catalog
from digital_twin.modules.reasoning.domain import ontology_schema as schema


@contextmanager
def pre_composition_catalog():
    def clear():
        schema.tbox_fingerprint.cache_clear()
        schema._default_tbox_metadata_tuple.cache_clear()
        fixture = sys.modules.get("static_seed_fixture")
        if fixture is not None:
            fixture._artifact.cache_clear()

    classes, names = list(catalog.CLASS_DEFS), list(catalog.TBOX_CLASSES)
    try:
        catalog.CLASS_DEFS[:] = [row for row in classes if row.name != "RuleEvidenceComposition"]
        catalog.TBOX_CLASSES[:] = [name for name in names if name != "RuleEvidenceComposition"]
        clear()
        with patch.object(schema, "ONTOLOGY_TBOX_VERSION", "investment-tbox-v14-research-knowledge"):
            yield
    finally:
        catalog.CLASS_DEFS[:] = classes
        catalog.TBOX_CLASSES[:] = names
        clear()
