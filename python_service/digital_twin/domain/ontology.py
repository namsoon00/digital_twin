"""Compatibility facade for the investment ontology domain.

New production code should import concrete contracts, schema helpers, prompt
helpers, or the portfolio ontology builder directly. This module remains only
for older tests and integrations that still import ``digital_twin.domain.ontology``.
"""

from .ontology_contracts import (
    OntologyBelief,
    OntologyEntity,
    OntologyEvidence,
    OntologyOpinion,
    OntologyRelation,
    PortfolioOntology,
    entity_id,
)
from .ontology_prompting import ONTOLOGY_PROMPT_VERSION
from .ontology_schema import (
    abox_properties,
    abox_relation_properties,
    add_entity,
    add_relation,
    ontology_abox,
    ontology_tbox,
    tbox_entities,
    tbox_relations,
)
from .portfolio_ontology_builder import *  # noqa: F401,F403 - legacy import surface

__all__ = [name for name in globals() if not name.startswith("_")]
