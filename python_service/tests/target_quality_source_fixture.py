"""Complete target-local evidence/quality images for persistence regressions."""

from digital_twin.modules.reasoning.domain.ontology_contracts import (
    OntologyEntity, OntologyRelation, PortfolioOntology,
)
from digital_twin.modules.reasoning.domain.ontology_scopes import apply_scoped_abox_identity


SOURCE_AT = "2026-09-10T01:00:00Z"
FACT_SLOT = {
    "enabled": True, "status": "ready", "targetSymbols": ["AAPL"],
    "requestedFactFamilies": ["evidence"],
    "requestedFactFamiliesBySymbol": {"AAPL": ["evidence"]},
    "slotFamilies": ["evidence"], "slotFamiliesBySymbol": {"AAPL": ["evidence"]},
    "eventBoundaryAuthoritative": True,
}


def quality_source_image(account_id, world_id, *, old):
    entities = [OntologyEntity("stock:AAPL", "Alpha", "stock", {
        "ontologyBox": "ABox", "symbol": "AAPL", "aboxScopeId": "symbol:AAPL:state",
    })]
    relations = []
    for name in (["current", "resolved"] if old else ["current"]):
        entity_id = "article-quality-risk:" + name
        entities.append(OntologyEntity(entity_id, name, "article-quality-risk", {
            "ontologyBox": "ABox", "symbol": "AAPL", "aboxScopeId": "symbol:AAPL:evidence:bucket:19",
        }))
        relations.append(OntologyRelation("stock:AAPL", entity_id, "HAS_DATA_QUALITY", properties={
            "ontologyBox": "ABox", "aboxScopeId": "link:symbol:AAPL:quality:bucket:06",
        }))
    if old:
        entities.append(OntologyEntity("stock:MSFT", "Other", "stock", {
            "ontologyBox": "ABox", "symbol": "MSFT",
        }))
    graph = PortfolioOntology(account_id, entities=entities, relations=relations)
    apply_scoped_abox_identity(graph, account_id=account_id, world_id=world_id)
    graph.worldview.update(targetScopeRetentionMode="incremental-target-patch", sourceObservedAt=SOURCE_AT)
    return graph
