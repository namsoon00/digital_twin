import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Dict, List

from .market_data import clamp, number
from .ontology_contracts import PortfolioOntology
from .ontology_schema import ontology_abox
from .portfolio import utc_now_iso


@dataclass
class OntologyQualitySample:
    sample_id: str
    portfolio_id: str
    created_at: str
    overall_score: float
    data_coverage_score: float
    context_coverage_score: float
    reasoning_readiness_score: float
    relation_density_score: float
    entity_count: int
    relation_count: int
    evidence_count: int
    belief_count: int
    opinion_count: int
    reasoning_card_count: int
    data_gap_count: int
    bounded_context_count: int
    high_pressure_count: int
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
    data_coverage = clamp((evidence_count / max(1, opinion_count * 4)) * 100 - len(gaps) * 8, 0.0, 100.0)
    context_coverage = clamp((len(contexts) / 6) * 100, 0.0, 100.0)
    reasoning_readiness = clamp((ready_cards / max(1, card_count)) * 100, 0.0, 100.0)
    relation_density = clamp((relation_count / max(1, entity_count)) * 55, 0.0, 100.0)
    overall = clamp(data_coverage * 0.32 + context_coverage * 0.26 + reasoning_readiness * 0.26 + relation_density * 0.16, 0.0, 100.0)
    high_pressure = len([opinion for opinion in graph.opinions or [] if number(opinion.ontology_pressure) >= 55])
    payload = {
        "source": source,
        "portfolioId": graph.portfolio_id,
        "scores": {
            "overall": round(overall, 2),
            "dataCoverage": round(data_coverage, 2),
            "contextCoverage": round(context_coverage, 2),
            "reasoningReadiness": round(reasoning_readiness, 2),
            "relationDensity": round(relation_density, 2),
        },
        "counts": {
            "entity": entity_count,
            "relation": relation_count,
            "evidence": evidence_count,
            "belief": belief_count,
            "opinion": opinion_count,
            "reasoningCard": card_count,
            "highPressure": high_pressure,
        },
        "boundedContexts": sorted(contexts),
        "dataGaps": gaps,
        "highPressureSymbols": [opinion.symbol for opinion in graph.opinions or [] if number(opinion.ontology_pressure) >= 55],
        "promptVersion": graph.to_dict().get("promptVersion"),
    }
    stamp = created_at or utc_now_iso()
    sample_id = "ontology-quality:" + graph.portfolio_id + ":" + sample_hash({"createdAt": stamp, **payload})
    return OntologyQualitySample(
        sample_id=sample_id,
        portfolio_id=graph.portfolio_id,
        created_at=stamp,
        overall_score=round(overall, 2),
        data_coverage_score=round(data_coverage, 2),
        context_coverage_score=round(context_coverage, 2),
        reasoning_readiness_score=round(reasoning_readiness, 2),
        relation_density_score=round(relation_density, 2),
        entity_count=entity_count,
        relation_count=relation_count,
        evidence_count=evidence_count,
        belief_count=belief_count,
        opinion_count=opinion_count,
        reasoning_card_count=card_count,
        data_gap_count=len(gaps),
        bounded_context_count=len(contexts),
        high_pressure_count=high_pressure,
        payload=payload,
    )
