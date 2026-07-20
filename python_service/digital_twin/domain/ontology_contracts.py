import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .ontology_decision_state import without_aggregate_decision_fields


@dataclass
class OntologyEntity:
    entity_id: str
    label: str
    kind: str
    properties: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.properties = without_aggregate_decision_fields(dict(self.properties or {}))

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["id"] = payload.pop("entity_id")
        return payload


@dataclass
class OntologyRelation:
    source: str
    target: str
    relation_type: str
    weight: float = 1.0
    evidence_ids: List[str] = field(default_factory=list)
    properties: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.weight = 1.0
        self.properties = without_aggregate_decision_fields(dict(self.properties or {}))

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["type"] = payload.pop("relation_type")
        return payload


@dataclass
class OntologyEvidence:
    evidence_id: str
    subject: str
    kind: str
    source: str
    summary: str
    value: Dict[str, object] = field(default_factory=dict)
    evidence_role: str = "context"
    data_state: str = "partial"

    def __post_init__(self) -> None:
        self.value = without_aggregate_decision_fields(dict(self.value or {}))

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["id"] = payload.pop("evidence_id")
        return payload


@dataclass
class OntologyBelief:
    belief_id: str
    subject: str
    label: str
    polarity: str
    evidence_role: str = "context"
    review_level: str = "observe"
    data_state: str = "partial"
    evidence_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["id"] = payload.pop("belief_id")
        return payload


@dataclass
class OntologyOpinion:
    symbol: str
    action: str
    tone: str
    thesis: str
    review_level: str = "check"
    data_state: str = "partial"
    validation_state: str = "conditional"
    supporting_beliefs: List[str] = field(default_factory=list)
    contradictions: List[str] = field(default_factory=list)
    dominant_risks: List[str] = field(default_factory=list)
    opportunities: List[str] = field(default_factory=list)
    legacy_model: Dict[str, object] = field(default_factory=dict)
    evidence_ids: List[str] = field(default_factory=list)
    relation_influences: List[Dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.legacy_model = without_aggregate_decision_fields(dict(self.legacy_model or {}))
        self.relation_influences = without_aggregate_decision_fields(list(self.relation_influences or []))

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class PortfolioOntology:
    portfolio_id: str
    entities: List[OntologyEntity] = field(default_factory=list)
    relations: List[OntologyRelation] = field(default_factory=list)
    evidence: List[OntologyEvidence] = field(default_factory=list)
    beliefs: List[OntologyBelief] = field(default_factory=list)
    opinions: List[OntologyOpinion] = field(default_factory=list)
    reasoning_cards: List[Dict[str, object]] = field(default_factory=list)
    worldview: Dict[str, object] = field(default_factory=dict)
    prompt: str = ""

    def to_dict(self) -> Dict[str, object]:
        from .ontology_prompting import ONTOLOGY_PROMPT_VERSION, build_ai_inference_packet, inferencebox_payload, rulebox_payload
        from .ontology_schema import ontology_abox, ontology_tbox

        inferencebox = inferencebox_payload(self)
        return {
            "portfolioId": self.portfolio_id,
            "tbox": ontology_tbox(),
            "abox": ontology_abox(self),
            "ruleBox": rulebox_payload(self),
            "inferenceBox": inferencebox,
            "derivedRelations": list(inferencebox.get("derivedRelations") or []),
            "inferenceTraces": list(inferencebox.get("traces") or []),
            "entities": [item.to_dict() for item in self.entities],
            "relations": [item.to_dict() for item in self.relations],
            "evidence": [item.to_dict() for item in self.evidence],
            "beliefs": [item.to_dict() for item in self.beliefs],
            "opinions": [item.to_dict() for item in self.opinions],
            "reasoningCards": list(self.reasoning_cards),
            "activeInvestmentOpinions": [
                dict((item.properties or {}).get("activeInvestmentOpinion") or {})
                for item in self.entities
                if item.kind == "active-opinion"
            ],
            "executionPlans": [
                dict((item.properties or {}).get("executionPlan") or {})
                for item in self.entities
                if item.kind == "execution-plan"
            ],
            "aiInferencePacket": build_ai_inference_packet(self),
            "worldview": dict(self.worldview or {}),
            "prompt": self.prompt,
            "promptVersion": ONTOLOGY_PROMPT_VERSION,
        }

    def opinion_for_symbol(self, symbol: str) -> Optional[OntologyOpinion]:
        target = str(symbol or "").upper()
        for opinion in self.opinions:
            if opinion.symbol.upper() == target:
                return opinion
        return None


def entity_id(kind: str, value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9가-힣_.:-]+", "-", str(value or "").strip())
    return kind + ":" + (normalized or "unknown")
