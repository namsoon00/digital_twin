"""Evaluate market-scoped hypothesis contracts over one immutable ABox.

The scorer owns empirical market conditions. Account position, mandate and
execution constraints remain in TypeDB, where a compact contract-specific
model evidence node is joined with those private facts.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from ..hypothesis_catalog import hypothesis_family_definition
from ..hypothesis_scoping import condition_scope_profile
from ..ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from ..ontology_rulebox_contracts import GraphInferenceRule, GraphRuleCondition
from ..time_series_storage import TemporalFeatureSnapshot
from .contracts import ModelSignal, ModelSignalSnapshot, SignalEligibility
from .registry import (
    DEFAULT_FLOW_SIGNAL_RELEASE_ID,
    DEFAULT_PRICE_SIGNAL_RELEASE_ID,
    default_statistical_model_registry,
    model_release,
    signal_hypothesis_family,
)
from .rule_contracts import rule_statistical_signal_contract


MODEL_HYPOTHESIS_SCORER_VERSION = "abox-hypothesis-contract-scorer-v2"


def _number(value: object):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _expected(value: object) -> object:
    if isinstance(value, Mapping):
        if value.get("default") not in (None, "", [], {}):
            return value.get("default")
        if value.get("value") not in (None, "", [], {}):
            return value.get("value")
        return ""
    return value


def _equal(actual: object, expected: object) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return str(actual).strip().lower() == str(expected).strip().lower()
    actual_number = _number(actual)
    expected_number = _number(expected)
    if actual_number is not None and expected_number is not None:
        return actual_number == expected_number
    return actual == expected or str(actual) == str(expected)


def _value_matches(actual: object, operator: object, expected: object):
    """Return True, False or None when the value is unavailable."""

    if actual in (None, ""):
        # The scorer receives a complete immutable ABox projection. A missing
        # subject attribute is therefore a proven non-match, which also lets a
        # negated condition correctly establish absence.
        return False
    op = str(operator or "==").strip().lower()
    if op in {"exists", "present"}:
        return True
    expected = _expected(expected)
    if expected in (None, "", [], {}):
        return True
    if isinstance(expected, (list, tuple, set)):
        outcomes = [_value_matches(actual, "==", item) for item in expected]
        if any(item is True for item in outcomes):
            return True
        return False if outcomes and all(item is False for item in outcomes) else None
    if isinstance(actual, (list, tuple, set)):
        outcomes = [_value_matches(item, op, expected) for item in actual]
        if any(item is True for item in outcomes):
            return True
        return False if outcomes and all(item is False for item in outcomes) else None
    if op in {"==", "eq", "in"}:
        return _equal(actual, expected)
    if op in {"!=", "ne"}:
        return not _equal(actual, expected)
    if op in {">", "gt", ">=", "gte", "<", "lt", "<=", "lte"}:
        actual_number = _number(actual)
        expected_number = _number(expected)
        if actual_number is None or expected_number is None:
            return None
        if op in {">", "gt"}:
            return actual_number > expected_number
        if op in {">=", "gte"}:
            return actual_number >= expected_number
        if op in {"<", "lt"}:
            return actual_number < expected_number
        return actual_number <= expected_number
    return None


def _filter_parts(key: object, expected: object) -> Tuple[str, str, object]:
    field = str(key or "")
    operator = "=="
    if field == "minValue":
        field, operator = "value", ">="
    elif field == "maxValue":
        field, operator = "value", "<="
    elif field.startswith("min") and len(field) > 3:
        field, operator = field[3].lower() + field[4:], ">="
    elif field.startswith("max") and len(field) > 3:
        field, operator = field[3].lower() + field[4:], "<="
    if isinstance(expected, Mapping) and expected.get("operator"):
        operator = str(expected.get("operator") or operator)
    return field, operator, expected


def _filters_match(properties: Mapping[str, object], filters: Mapping[str, object]):
    values = dict(properties or {})
    nested = values.get("properties")
    if isinstance(nested, Mapping):
        values.update(dict(nested))
    unknown = False
    for key, expected in dict(filters or {}).items():
        field, operator, raw_expected = _filter_parts(key, expected)
        verdict = _value_matches(values.get(field), operator, raw_expected)
        if verdict is False:
            return False
        if verdict is None:
            unknown = True
    return None if unknown else True


def _relation_matches(
    subject: OntologyEntity,
    condition: GraphRuleCondition,
    entities: Mapping[str, OntologyEntity],
    relation_index: Mapping[Tuple[str, str, str], Sequence[OntologyRelation]],
):
    relation_type = str(condition.relation_type or "").upper().strip()
    if not relation_type:
        return None, []
    direction = str(condition.direction or "out").lower()
    unknown = False
    for relation in relation_index.get((subject.entity_id, direction, relation_type), ()):
        if direction == "in":
            target_id = relation.source
        else:
            target_id = relation.target
        target = entities.get(str(target_id or ""))
        if target is None:
            unknown = True
            continue
        if condition.target_kind and target.kind != condition.target_kind:
            continue
        target_verdict = _filters_match(
            target.properties or {},
            condition.target_property_filters or {},
        )
        relation_properties = dict(relation.properties or {})
        relation_properties.setdefault("weight", relation.weight)
        relation_verdict = _filters_match(
            relation_properties,
            condition.relation_property_filters or {},
        )
        if target_verdict is False or relation_verdict is False:
            continue
        if target_verdict is None or relation_verdict is None:
            unknown = True
            continue
        evidence_id = "|".join((relation.source, relation.relation_type, relation.target))
        return True, [evidence_id]
    return (None if unknown else False), []


def _graph_match_index(graph: PortfolioOntology) -> Dict[str, object]:
    entities = {item.entity_id: item for item in graph.entities}
    relations: Dict[Tuple[str, str, str], List[OntologyRelation]] = defaultdict(list)
    for relation in graph.relations:
        relation_type = str(relation.relation_type or "").upper().strip()
        if not relation_type:
            continue
        relations[(relation.source, "out", relation_type)].append(relation)
        relations[(relation.target, "in", relation_type)].append(relation)
    return {"entities": entities, "relations": relations}


def evaluate_market_hypothesis_contract(
    graph: PortfolioOntology,
    subject: OntologyEntity,
    rule: GraphInferenceRule,
    match_index: Mapping[str, object] = None,
    market_conditions: Sequence[GraphRuleCondition] = None,
) -> Dict[str, object]:
    """Evaluate only the market-owned portion of one predictive rule."""

    index = match_index if match_index is not None else _graph_match_index(graph)
    entities = index.get("entities") or {}
    relation_index = index.get("relations") or {}
    properties = dict(subject.properties or {})
    properties.setdefault("symbol", str(properties.get("symbol") or "").upper())
    if market_conditions is None:
        market_conditions = tuple(
            condition
            for condition_index, condition in enumerate(rule.conditions or [])
            if str(
                condition_scope_profile(
                    condition.to_dict(),
                    condition_index,
                ).get("scope")
                or ""
            )
            == "market"
        )

    matched_ids: List[str] = []
    evidence_ids: List[str] = []
    unknown_ids: List[str] = []
    required_failures: List[str] = []
    any_matches: Dict[str, Tuple[str, List[str]]] = {}

    def verdict(condition: GraphRuleCondition):
        if condition.kind == "subject_property":
            return (
                _value_matches(
                    properties.get(condition.field),
                    condition.operator,
                    condition.value,
                ),
                [subject.entity_id + "#" + str(condition.field or "property")],
            )
        if condition.kind == "relation":
            return _relation_matches(subject, condition, entities, relation_index)
        return None, []

    for condition in market_conditions:
        role = str(condition.role or "required").strip().lower()
        outcome, evidence = verdict(condition)
        condition_id = str(condition.condition_id or "condition")
        if role in {"any", "optional"}:
            if outcome is True:
                group = str(condition.evidence_group_key or condition_id)
                any_matches.setdefault(group, (condition_id, evidence))
            elif outcome is None:
                unknown_ids.append(condition_id)
            continue
        if role in {"not", "negative", "exclude"}:
            if outcome is True:
                required_failures.append("not:" + condition_id)
            elif outcome is None:
                unknown_ids.append(condition_id)
            else:
                matched_ids.append(condition_id)
            continue
        if outcome is True:
            matched_ids.append(condition_id)
            evidence_ids.extend(evidence)
        elif outcome is None:
            unknown_ids.append(condition_id)
        else:
            required_failures.append(condition_id)

    minimum = max(1, int(rule.any_condition_min_count or 1))
    any_conditions = [
        item for item in market_conditions
        if str(item.role or "required").strip().lower() in {"any", "optional"}
    ]
    if any_conditions and len(any_matches) < minimum:
        required_failures.append("any-group:" + rule.rule_id)
    for condition_id, evidence in any_matches.values():
        matched_ids.append(condition_id)
        evidence_ids.extend(evidence)

    known_count = max(0, len(market_conditions) - len(set(unknown_ids)))
    coverage = known_count / max(1.0, float(len(market_conditions)))
    status = (
        "unmatched" if required_failures
        else "incomplete" if unknown_ids
        else "matched"
    )
    return {
        "status": status,
        "ruleId": rule.rule_id,
        "marketConditionCount": len(market_conditions),
        "matchedConditionIds": sorted(set(matched_ids)),
        "unknownConditionIds": sorted(set(unknown_ids)),
        "failedConditionIds": sorted(set(required_failures)),
        "evidenceIds": sorted(set(evidence_ids))[:64],
        "coverageRatio": round(coverage, 8),
        "scorerVersion": MODEL_HYPOTHESIS_SCORER_VERSION,
    }


def _contract_eligibility(
    release,
    rows: Sequence[Mapping[str, object]],
    family_feature_snapshot_available: bool = True,
) -> SignalEligibility:
    coverage = min((float(item.get("coverageRatio") or 0) for item in rows), default=0.0)
    reasons = []
    if not rows:
        reasons.append("no-matched-hypothesis-contract")
    if not family_feature_snapshot_available:
        reasons.append("family-feature-snapshot-unavailable")
    if coverage < float(release.minimum_coverage_ratio or 0):
        reasons.append("minimum-contract-coverage-not-met")
    if release.status != "production":
        reasons.append("model-release-not-production")
    if release.validation_status not in {"calibrated", "validated-deterministic"}:
        reasons.append("model-release-validation-incomplete")
    if release.decision_eligibility not in {"eligible", "conditional"}:
        reasons.append("model-release-not-decision-eligible")
    return SignalEligibility.create(
        "ineligible" if reasons else "conditional",
        reasons,
        data_quality="insufficient" if reasons else "sufficient",
        validation_status=release.validation_status,
        decision_eligibility=release.decision_eligibility,
    )


def _copy_eligibility(eligibility: SignalEligibility) -> SignalEligibility:
    return SignalEligibility.create(
        eligibility.status,
        eligibility.reasons,
        data_quality=eligibility.data_quality,
        validation_status=eligibility.validation_status,
        decision_eligibility=eligibility.decision_eligibility,
    )


def score_graph_hypothesis_contracts(
    graph: PortfolioOntology,
    feature_snapshot: TemporalFeatureSnapshot,
    rules: Iterable[GraphInferenceRule],
    baseline_snapshots: Iterable[ModelSignalSnapshot] = (),
) -> Tuple[ModelSignalSnapshot, ...]:
    """Return six release snapshots with exact matched hypothesis identities."""

    baseline = {
        (item.model_release_id, signal.subject_id, signal.signal_type): signal
        for item in baseline_snapshots or []
        for signal in item.signals
    }
    baseline_release_subjects = {
        (item.model_release_id, signal.subject_id)
        for item in baseline_snapshots or []
        for signal in item.signals
    }
    grouped: Dict[Tuple[str, str, str], List[Dict[str, object]]] = defaultdict(list)
    stock_entities = [item for item in graph.entities if item.kind == "stock"]
    match_index = _graph_match_index(graph)
    predictive_contracts = []
    for rule in rules or []:
        if (
            not rule.enabled
            or rule.resolved_knowledge_basis.rule_kind != "predictive-hypothesis"
        ):
            continue
        contract = rule_statistical_signal_contract(rule)
        signal_types = list(contract.get("signalTypes") or [])
        release_ids = list(contract.get("releaseIds") or [])
        if not signal_types or not release_ids:
            continue
        market_conditions = tuple(
            condition
            for condition_index, condition in enumerate(rule.conditions or [])
            if str(
                condition_scope_profile(
                    condition.to_dict(),
                    condition_index,
                ).get("scope")
                or ""
            )
            == "market"
        )
        predictive_contracts.append((
            rule,
            str(release_ids[0]),
            str(signal_types[0]),
            market_conditions,
        ))
    for rule, release_id, signal_type, market_conditions in predictive_contracts:
        for subject in stock_entities:
            symbol = str((subject.properties or {}).get("symbol") or "").upper().strip()
            if not symbol:
                continue
            result = evaluate_market_hypothesis_contract(
                graph,
                subject,
                rule,
                match_index=match_index,
                market_conditions=market_conditions,
            )
            if result.get("status") == "matched":
                grouped[(release_id, symbol, signal_type)].append(result)

    signals_by_release: Dict[str, List[ModelSignal]] = defaultdict(list)
    for key, rows in sorted(grouped.items()):
        release_id, symbol, signal_type = key
        release = model_release(release_id)
        base = baseline.get(key)
        requires_family_features = release_id in {
            DEFAULT_PRICE_SIGNAL_RELEASE_ID,
            DEFAULT_FLOW_SIGNAL_RELEASE_ID,
        }
        eligibility = (
            _copy_eligibility(base.eligibility)
            if base
            else _contract_eligibility(
                release,
                rows,
                family_feature_snapshot_available=(
                    not requires_family_features
                    or (release_id, symbol) in baseline_release_subjects
                ),
            )
        )
        contract_ids = tuple(sorted({str(item.get("ruleId") or "") for item in rows if item.get("ruleId")}))
        coverage = min((float(item.get("coverageRatio") or 0) for item in rows), default=0.0)
        family_id = signal_hypothesis_family(signal_type)
        family = hypothesis_family_definition(family_id)
        evidence_ids = sorted({
            str(evidence_id)
            for item in rows
            for evidence_id in item.get("evidenceIds") or []
            if str(evidence_id or "")
        })[:64]
        contract_features = {
            "scorerVersion": MODEL_HYPOTHESIS_SCORER_VERSION,
            "contractMatched": True,
            "hypothesisContractCount": len(contract_ids),
            "hypothesisContractIds": list(contract_ids),
            "contractMatches": [
                {
                    "ruleId": item.get("ruleId"),
                    "marketConditionCount": item.get("marketConditionCount"),
                    "matchedConditionIds": list(item.get("matchedConditionIds") or [])[:24],
                    "evidenceIds": list(item.get("evidenceIds") or [])[:24],
                    "coverageRatio": item.get("coverageRatio"),
                }
                for item in rows[:24]
            ],
            "evidenceIds": evidence_ids,
        }
        if base:
            contract_features["familyScore"] = base.score
            contract_features["familyConfidence"] = base.confidence
            contract_features["familyInputFeatures"] = dict(base.input_features or {})
        signal_score = base.score if base else coverage
        signal_confidence = min(
            1.0,
            coverage * (base.confidence if base else 1.0),
        )
        signals_by_release[release_id].append(ModelSignal.create(
            signal_type=signal_type,
            signal_family=release.model_family,
            subject_id=symbol,
            horizon=base.horizon if base else (family.default_horizon if family else "CURRENT"),
            polarity="risk" if signal_type.endswith("risk") else "support",
            # Contract satisfaction and empirical signal strength are separate
            # dimensions. TypeDB receives both and may promote only a strong,
            # eligible match instead of treating every exact match as 1.0.
            score=signal_score,
            confidence=signal_confidence,
            observed_at=base.observed_at if base else feature_snapshot.as_of,
            source_feature_snapshot_id=feature_snapshot.snapshot_id,
            feature_set_version=feature_snapshot.feature_set_version,
            model_release_id=release_id,
            sample_count=base.sample_count if base else max(1, len(evidence_ids)),
            coverage_ratio=min(coverage, base.coverage_ratio) if base else coverage,
            eligibility=eligibility,
            input_features=contract_features,
            contract_matched=True,
            market_session=base.market_session if base else "",
            source_age_seconds=base.source_age_seconds if base else None,
            freshness_compatible=base.freshness_compatible if base else True,
            probability=base.probability if base else None,
            probability_lower=base.probability_lower if base else None,
            probability_upper=base.probability_upper if base else None,
            hypothesis_family_id=family_id,
            hypothesis_contract_ids=contract_ids,
            outcome_metric=family.outcome_metric if family else "",
            knowledge_cutoff_at=feature_snapshot.as_of,
            uncertainty_status=base.uncertainty_status if base else "score-only",
        ))

    # Preserve price/flow diagnostics even when no exact hypothesis contract
    # matched. They remain visible reference signals but cannot satisfy a
    # TypeDB rule because no contract evidence node is projected for them.
    for base in baseline.values():
        key = (base.model_release_id, base.subject_id, base.signal_type)
        if key not in grouped:
            signals_by_release[base.model_release_id].append(base)

    snapshots = []
    subjects = feature_snapshot.symbols or tuple(sorted({
        str((item.properties or {}).get("symbol") or "").upper().strip()
        for item in stock_entities
        if str((item.properties or {}).get("symbol") or "").strip()
    }))
    for release in default_statistical_model_registry():
        snapshots.append(ModelSignalSnapshot.create(
            account_id=feature_snapshot.account_id,
            as_of=feature_snapshot.as_of,
            source_feature_snapshot_id=feature_snapshot.snapshot_id,
            feature_set_version=feature_snapshot.feature_set_version,
            model_release_id=release.release_id,
            signals=signals_by_release.get(release.release_id) or [],
            subjects=subjects,
        ))
    return tuple(snapshots)
