"""Normalize only graph-derived hypotheses; never invent a Python claim catalog."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Tuple

from .contracts import HypothesisRecord


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _relation_context(value: object) -> Dict[str, object]:
    if isinstance(value, Mapping):
        metadata = _mapping(value.get("metadata"))
        context = _mapping(value.get("context"))
    else:
        metadata = _mapping(getattr(value, "metadata", {}))
        context = {}
    return (
        _mapping(context.get("ontologyRelationContext"))
        or _mapping(metadata.get("ontologyRelationContext"))
        or _mapping(value.get("ontologyRelationContext") if isinstance(value, Mapping) else {})
    )


def _hypothesis_payloads(value: object) -> List[Dict[str, object]]:
    relation = _relation_context(value)
    brain = _mapping(relation.get("investmentBrain"))
    hypothesis_set = _mapping(relation.get("hypothesisSet")) or _mapping(brain.get("hypothesisSet"))
    return [
        dict(item)
        for item in hypothesis_set.get("hypotheses") or []
        if isinstance(item, Mapping) and str(item.get("hypothesisId") or item.get("hypothesis_id") or "").strip()
    ]


class GraphHypothesisManager:
    """Collect competing hypotheses already materialized from TypeDB context."""

    def from_candidates(self, candidates: Iterable[object]) -> Tuple[HypothesisRecord, ...]:
        indexed: Dict[str, HypothesisRecord] = {}
        for candidate in candidates or []:
            for payload in _hypothesis_payloads(candidate):
                hypothesis = HypothesisRecord.from_dict(payload)
                if hypothesis.hypothesis_id:
                    indexed[hypothesis.hypothesis_id] = hypothesis
        return tuple(indexed[key] for key in sorted(indexed))

    def from_ai_context(self, context: Mapping[str, object]) -> Tuple[HypothesisRecord, ...]:
        return self.from_candidates([{"context": dict(context or {})}])
