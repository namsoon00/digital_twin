"""Canonical evidence assertions carried from TypeDB traces into decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Dict, Iterable, List, Mapping, Tuple


DECISION_EVIDENCE_ASSERTION_VERSION = "decision-evidence-assertion-v1"


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:max(1, int(limit or 1))]


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _unique(values: Iterable[object], limit: int = 32) -> List[str]:
    result: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= max(1, int(limit or 1)):
            break
    return result


def _stable_relation_id(item: Mapping[str, object]) -> str:
    material = "|".join(str(value or "").strip() for value in (
        item.get("source"), item.get("type"), item.get("target"), item.get("ruleId"),
    ))
    return "relation-evidence:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _compact_value(value: object, depth: int = 0) -> object:
    if depth >= 2:
        return _text(value, 120)
    if isinstance(value, Mapping):
        return {
            str(key): _compact_value(item, depth + 1)
            for key, item in list(value.items())[:12]
            if item not in (None, "", [], {})
        }
    if isinstance(value, (list, tuple, set)):
        return [_compact_value(item, depth + 1) for item in list(value)[:8]]
    if isinstance(value, str):
        return _text(value, 160)
    return value


@dataclass(frozen=True)
class EvidenceAssertion:
    evidence_id: str
    rule_id: str
    label: str
    kind: str
    polarity: str = "context"
    value: object = None
    source: str = ""
    source_as_of: str = ""
    fetched_at: str = ""
    freshness: str = ""
    relation_type: str = ""
    condition_id: str = ""
    evidence_independence_key: str = ""
    related_fact_ids: Tuple[str, ...] = ()
    judgement_eligible: bool = True
    version: str = DECISION_EVIDENCE_ASSERTION_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "version": payload.pop("version"),
            "evidenceId": payload.pop("evidence_id"),
            "ruleId": payload.pop("rule_id"),
            "label": payload.pop("label"),
            "kind": payload.pop("kind"),
            "polarity": payload.pop("polarity"),
            "value": payload.pop("value"),
            "source": payload.pop("source"),
            "sourceAsOf": payload.pop("source_as_of"),
            "fetchedAt": payload.pop("fetched_at"),
            "freshness": payload.pop("freshness"),
            "relationType": payload.pop("relation_type"),
            "conditionId": payload.pop("condition_id"),
            "evidenceIndependenceKey": payload.pop("evidence_independence_key"),
            "relatedFactIds": list(payload.pop("related_fact_ids")),
            "judgementEligible": payload.pop("judgement_eligible"),
        }


def trace_evidence_ids(traces: Iterable[Mapping[str, object]]) -> List[str]:
    """Return the source assertion IDs proven by current TypeDB traces."""

    result: List[str] = []
    for trace in traces or []:
        if not isinstance(trace, Mapping):
            continue
        if trace.get("evidenceUsableForJudgement") is False:
            continue
        conditions = [
            condition
            for condition in trace.get("matchedConditions") or []
            if isinstance(condition, Mapping)
        ]
        unusable_ids = {
            str(condition.get("relationId") or "").strip()
            for condition in conditions
            if condition.get("judgementEvidenceUsable") is False
            and str(condition.get("relationId") or "").strip()
        }
        result.extend(
            evidence_id
            for evidence_id in trace.get("evidenceRelationIds") or []
            if str(evidence_id or "").strip() not in unusable_ids
        )
        result.extend(
            condition.get("relationId")
            for condition in conditions
            if condition.get("judgementEvidenceUsable") is not False
        )
    return _unique(result, 64)


def rebind_hypothesis_evidence_ids(
    hypothesis_set: Mapping[str, object],
    traces: Iterable[Mapping[str, object]],
) -> Dict[str, object]:
    """Replace legacy derived-relation hashes with source assertion IDs."""

    payload = dict(hypothesis_set or {}) if isinstance(hypothesis_set, Mapping) else {}
    trace_rows = [dict(item) for item in traces or [] if isinstance(item, Mapping)]
    evidence_by_rule: Dict[str, List[str]] = {}
    for trace in trace_rows:
        rule_id = _text(trace.get("ruleId") or trace.get("rule_id"), 180)
        if rule_id:
            evidence_by_rule.setdefault(rule_id, []).extend(trace_evidence_ids([trace]))
    proven_evidence_ids = {
        evidence_id
        for evidence_ids in evidence_by_rule.values()
        for evidence_id in evidence_ids
    }

    hypotheses = []
    for item in payload.get("hypotheses") or []:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        for output_key, rule_key in (
            ("supportingEvidenceIds", "supportingRuleIds"),
            ("counterEvidenceIds", "counterRuleIds"),
        ):
            exact_ids = _unique([
                evidence_id
                for rule_id in row.get(rule_key) or []
                for evidence_id in evidence_by_rule.get(str(rule_id or "").strip(), [])
            ], 64)
            retained_ids = [
                evidence_id
                for evidence_id in row.get(output_key) or []
                if str(evidence_id or "").strip() in proven_evidence_ids
            ]
            rebound = _unique([*exact_ids, *retained_ids], 64)
            # Legacy derived hashes have no source value or provenance. If the
            # current trace cannot bind them to an exact assertion, clear the
            # list so downstream eligibility fails closed.
            row[output_key] = rebound
        hypotheses.append(row)
    payload["hypotheses"] = hypotheses
    return payload


def inference_evidence_assertions(
    traces: Iterable[Mapping[str, object]],
    relations: Iterable[Mapping[str, object]] = (),
    facts: Mapping[str, object] = None,
    referenced_evidence_ids: Iterable[object] = (),
) -> List[Dict[str, object]]:
    """Materialize compact, provenance-preserving evidence for prompt/replay."""

    trace_rows = [dict(item) for item in traces or [] if isinstance(item, Mapping)]
    relation_rows = [dict(item) for item in relations or [] if isinstance(item, Mapping)]
    fact_values = _mapping(facts)
    referenced = set(_unique(referenced_evidence_ids, 256))
    assertions: Dict[str, EvidenceAssertion] = {}

    for trace in trace_rows:
        rule_id = _text(trace.get("ruleId") or trace.get("rule_id"), 180)
        claim = _mapping(trace.get("claimContract") or trace.get("claim_contract"))
        basis = _mapping(trace.get("knowledgeBasis") or trace.get("knowledge_basis"))
        expected = _text(claim.get("expectedDirection"), 32).lower()
        polarity = expected if expected in {"support", "risk"} else "context"
        trace_label = _text(trace.get("label") or claim.get("statement") or rule_id, 220)
        trace_evidence = set(_unique(trace.get("evidenceRelationIds") or [], 64))
        related_fact_ids = tuple(_unique([
            "fact:" + str(condition.get("field"))
            for condition in trace.get("matchedConditions") or []
            if isinstance(condition, Mapping)
            and str(condition.get("field") or "").strip() in fact_values
        ], 12))
        for condition in trace.get("matchedConditions") or []:
            if not isinstance(condition, Mapping):
                continue
            evidence_id = _text(condition.get("relationId"), 220)
            if not evidence_id or (trace_evidence and evidence_id not in trace_evidence):
                continue
            if referenced and evidence_id not in referenced:
                continue
            value = condition.get("observedValue")
            target = _mapping(condition.get("matchedTargetProperties"))
            signal_type = _text(target.get("signalType") or _mapping(value).get("signalType"), 100)
            strength = _text(target.get("strengthBand") or _mapping(value).get("strengthBand"), 40)
            label = trace_label
            if signal_type:
                label = signal_type + ((" · " + strength) if strength else "") + " · " + trace_label
            assertions[evidence_id] = EvidenceAssertion(
                evidence_id=evidence_id,
                rule_id=rule_id,
                label=label,
                kind="model-signal" if signal_type else "ontology-assertion",
                polarity=polarity,
                value=_compact_value(value if value not in (None, "", {}, []) else target),
                source=_text(condition.get("source") or "TypeDB", 120),
                source_as_of=_text(condition.get("observedAt"), 100),
                fetched_at=_text(condition.get("sourceFetchedAt"), 100),
                freshness=_text(condition.get("freshnessStatus") or trace.get("freshnessStatus"), 60),
                relation_type=_text(condition.get("relationType"), 100),
                condition_id=_text(condition.get("conditionId"), 180),
                evidence_independence_key=_text(
                    claim.get("evidenceIndependenceKey")
                    or basis.get("evidenceIndependenceKey"),
                    120,
                ),
                related_fact_ids=related_fact_ids,
                judgement_eligible=bool(
                    trace.get("evidenceUsableForJudgement") is not False
                    and condition.get("judgementEvidenceUsable") is not False
                ),
            )

    for relation in relation_rows:
        evidence_id = _text(
            relation.get("id") or relation.get("relationId") or _stable_relation_id(relation),
            220,
        )
        if evidence_id in assertions or (referenced and evidence_id not in referenced):
            continue
        rule_id = _text(relation.get("ruleId") or relation.get("rule_id"), 180)
        assertions[evidence_id] = EvidenceAssertion(
            evidence_id=evidence_id,
            rule_id=rule_id,
            label=_text(
                relation.get("label")
                or relation.get("aiInfluenceLabel")
                or relation.get("targetLabel")
                or evidence_id,
                220,
            ),
            kind="typedb-derived-relation",
            polarity=_text(relation.get("polarity") or relation.get("evidenceRole"), 40).lower() or "context",
            source=_text(relation.get("materializationSource") or "TypeDB", 120),
            source_as_of=_text(relation.get("updatedAt"), 100),
            freshness=_text(relation.get("freshnessStatus"), 60),
            relation_type=_text(relation.get("type") or relation.get("relationType"), 100),
            evidence_independence_key=_text(
                _mapping(relation.get("claimContract")).get("evidenceIndependenceKey"),
                120,
            ),
            judgement_eligible=relation.get("evidenceUsableForJudgement") is not False,
        )

    return [
        assertions[evidence_id].to_dict()
        for evidence_id in sorted(assertions)
        if not referenced or evidence_id in referenced
    ]
