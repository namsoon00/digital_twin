import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Dict, List

from .ontology_contracts import PortfolioOntology
from .ontology_schema import ontology_abox
from .ontology_validator import validate_ontology
from .portfolio import utc_now_iso


@dataclass
class OntologyQualitySample:
    sample_id: str
    portfolio_id: str
    created_at: str
    overall_state: str
    data_state: str
    context_state: str
    reasoning_state: str
    relation_state: str
    validation_state: str
    entity_count: int
    relation_count: int
    evidence_count: int
    belief_count: int
    opinion_count: int
    reasoning_card_count: int
    data_gap_count: int
    bounded_context_count: int
    action_required_count: int
    payload: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def sample_hash(payload: Dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def reasoning_data_gaps(graph: PortfolioOntology) -> List[str]:
    gaps = []
    for card in graph.reasoning_cards or []:
        if not isinstance(card, dict):
            continue
        gaps.extend(str(item or "") for item in card.get("dataGaps") or [] if str(item or ""))
    return sorted(set(gaps))


def build_ontology_quality_sample(graph: PortfolioOntology, source: str = "runtime", created_at: str = "") -> OntologyQualitySample:
    abox = ontology_abox(graph)
    entity_count = int(abox.get("entityCount") or 0)
    relation_count = int(abox.get("relationCount") or 0)
    evidence_count = len(graph.evidence or [])
    belief_count = len(graph.beliefs or [])
    opinion_count = len(graph.opinions or [])
    card_count = len(graph.reasoning_cards or [])
    contexts = set()
    for entity in graph.entities or []:
        properties = entity.properties or {}
        if properties.get("ontologyBox") == "TBox":
            continue
        context = str(properties.get("boundedContext") or "")
        if context:
            contexts.add(context)
    gaps = reasoning_data_gaps(graph)
    ready_cards = len([card for card in graph.reasoning_cards or [] if isinstance(card, dict) and str(card.get("status") or "") == "readyForAiReview"])
    validation = validate_ontology(graph)
    data_state = "unavailable" if entity_count <= 0 else "insufficient" if evidence_count <= 0 else "partial" if gaps else "sufficient"
    context_state = "sufficient" if len(contexts) >= 6 else "partial" if contexts else "insufficient"
    presentation_deferred = bool((graph.worldview or {}).get("presentationDeferred"))
    reasoning_state = (
        "conditional"
        if presentation_deferred
        else "ready" if card_count > 0 and ready_cards == card_count else "conditional" if ready_cards > 0 else "blocked"
    )
    relation_state = "connected" if relation_count >= entity_count and entity_count > 0 else "sparse" if relation_count > 0 else "empty"
    validation_state = "blocked" if validation.error_count else "conditional" if validation.warning_count else "ready"
    overall_state = "blocked" if "blocked" in {reasoning_state, validation_state} or data_state in {"unavailable", "insufficient"} else "conditional" if data_state == "partial" or context_state != "sufficient" or relation_state != "connected" or validation_state == "conditional" else "ready"
    action_required = [
        opinion for opinion in graph.opinions or []
        if opinion.review_level in {"act", "immediate", "blocked"}
    ]
    payload = {
        "source": source,
        "portfolioId": graph.portfolio_id,
        "states": {
            "overall": overall_state,
            "data": data_state,
            "context": context_state,
            "reasoning": reasoning_state,
            "relation": relation_state,
            "validation": validation_state,
        },
        "counts": {
            "entity": entity_count,
            "relation": relation_count,
            "evidence": evidence_count,
            "belief": belief_count,
            "opinion": opinion_count,
            "reasoningCard": card_count,
            "actionRequired": len(action_required),
        },
        "boundedContexts": sorted(contexts),
        "dataGaps": gaps,
        "validation": validation.to_dict(),
        "actionRequiredSymbols": [opinion.symbol for opinion in action_required],
        "promptVersion": graph.to_dict().get("promptVersion"),
    }
    stamp = created_at or utc_now_iso()
    sample_id = "ontology-quality:" + graph.portfolio_id + ":" + sample_hash({"createdAt": stamp, **payload})
    return OntologyQualitySample(
        sample_id=sample_id,
        portfolio_id=graph.portfolio_id,
        created_at=stamp,
        overall_state=overall_state,
        data_state=data_state,
        context_state=context_state,
        reasoning_state=reasoning_state,
        relation_state=relation_state,
        validation_state=validation_state,
        entity_count=entity_count,
        relation_count=relation_count,
        evidence_count=evidence_count,
        belief_count=belief_count,
        opinion_count=opinion_count,
        reasoning_card_count=card_count,
        data_gap_count=len(gaps),
        bounded_context_count=len(contexts),
        action_required_count=len(action_required),
        payload=payload,
    )
