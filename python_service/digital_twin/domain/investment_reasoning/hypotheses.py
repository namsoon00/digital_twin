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
    subject = _mapping(relation.get("subject"))
    rows = []
    for item in hypothesis_set.get("hypotheses") or []:
        if not isinstance(item, Mapping) or not str(
            item.get("hypothesisId") or item.get("hypothesis_id") or ""
        ).strip():
            continue
        payload = dict(item)
        payload.setdefault("accountId", relation.get("accountId") or "")
        payload.setdefault("subjectSymbol", subject.get("symbol") or hypothesis_set.get("subjectSymbol") or "")
        payload.setdefault(
            "inferenceGenerationId",
            relation.get("inferenceGenerationId")
            or hypothesis_set.get("inferenceGenerationId")
            or "",
        )
        rows.append(payload)
    return rows


class GraphHypothesisManager:
    """Collect competing hypotheses already materialized from TypeDB context."""

    def from_candidates(
        self,
        candidates: Iterable[object],
        subject_symbols: Iterable[str] = (),
        inference_generation_ids: Iterable[str] = (),
        account_ids: Iterable[str] = (),
    ) -> Tuple[HypothesisRecord, ...]:
        allowed_symbols = {str(value or "").upper().strip() for value in subject_symbols or [] if str(value or "").strip()}
        allowed_generations = {str(value or "").strip() for value in inference_generation_ids or [] if str(value or "").strip()}
        allowed_accounts = {str(value or "").strip() for value in account_ids or [] if str(value or "").strip()}
        indexed: Dict[str, HypothesisRecord] = {}
        for candidate in candidates or []:
            for payload in _hypothesis_payloads(candidate):
                hypothesis = HypothesisRecord.from_dict(payload)
                if allowed_symbols and hypothesis.subject_symbol not in allowed_symbols:
                    continue
                if allowed_generations and hypothesis.inference_generation_id not in allowed_generations:
                    continue
                if allowed_accounts and hypothesis.account_id and hypothesis.account_id not in allowed_accounts:
                    continue
                if hypothesis.hypothesis_id:
                    indexed[hypothesis.hypothesis_id] = hypothesis
        return tuple(indexed[key] for key in sorted(indexed))

    def from_ai_context(self, context: Mapping[str, object]) -> Tuple[HypothesisRecord, ...]:
        return self.from_candidates([{"context": dict(context or {})}])
