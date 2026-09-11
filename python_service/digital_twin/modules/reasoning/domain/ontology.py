"""Compatibility facade for the investment ontology domain.

New production code should import concrete contracts, schema helpers, prompt
helpers, or the portfolio ontology builder directly. This module remains only
for older tests and integrations that still import ``digital_twin.modules.reasoning.domain.ontology``.
"""

from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyBelief, OntologyEntity, OntologyEvidence, OntologyOpinion, OntologyRelation, PortfolioOntology, entity_id
from digital_twin.modules.reasoning.domain.ontology_prompting import ONTOLOGY_PROMPT_VERSION
from digital_twin.modules.reasoning.domain.ontology_schema import abox_properties, abox_relation_properties, add_entity, add_relation, ontology_abox, ontology_tbox, tbox_entities, tbox_relations
from digital_twin.modules.reasoning.domain.portfolio_ontology_builder import *  # noqa: F401,F403 - legacy import surface

__all__ = [name for name in globals() if not name.startswith("_")]
