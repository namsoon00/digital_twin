"""Compatibility exports for ontology lifecycle and immutable seed artifacts."""

from digital_twin.modules.reasoning.infrastructure.graph_reads.tbox_metadata import (
    active_tbox_metadata_from_rows,
    active_tbox_metadata_unavailable,
)
from digital_twin.modules.reasoning.infrastructure.static_seed.artifact import (
    graph_box_entity_counts,
    graph_box_relation_counts,
    ontology_release_seed_artifact,
    ontology_seed_graph,
    ontology_seed_graph_from_artifact,
)
