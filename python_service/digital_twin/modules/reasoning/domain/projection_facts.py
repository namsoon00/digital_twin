"""Factual ABox shaping only; no queries, model scoring or graph publication."""

from __future__ import annotations

from copy import deepcopy
from digital_twin.modules.market_data.contracts import MARKET_SIGNAL_TRANSITION_RESULTS_KEY
from digital_twin.modules.market_data.contracts import MARKET_SIGNAL_TRANSITION_STATE_KEY
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_external_signals_for_ontology
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from digital_twin.modules.reasoning.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.modules.reasoning.domain.portfolio_ontology_coverage import CATEGORY_RELATIONS
from typing import Dict
from typing import List
from typing import Set


ABOX_STRUCTURAL_RELATION_TYPES = {
    "ASSESSES_HYPOTHESIS_FAMILY",
    "CALIBRATED_BY_OUTCOME",
    "COMPARES_WITH_MARKET_PROXY",
    "HAS_CAPITAL_FLOW_WINDOW",
    "ISSUES",
    "OCCURS_IN_SESSION_PHASE",
    "WINDOW_CONTAINS_OBSERVATION",
    "PRECEDES",
    "REPRESENTS_INSTRUMENT",
    "REPRESENTS_STOCK",
    "RECONCILES_PORTFOLIO",
    "RECORDS_PORTFOLIO_ACTIVITY",
    "INFERRED_FROM_SNAPSHOT_CHANGE",
    "GROUPS_LEDGER_ACTIVITY",
    "HAS_PORTFOLIO_ACTIVITY",
    "HAS_PORTFOLIO_STATE",
    "OBSERVES_ACCOUNT_ACTION",
    "OBSERVED_AFTER_DECISION",
    "OBSERVES_DECISION_CYCLE",
    "EVALUATES_PORTFOLIO_CANDIDATE",
    "HAS_RISK_SNAPSHOT",
    "HAS_POSITION_RISK",
    "HAS_HYPOTHESIS_ASSESSMENT",
    "HAS_HYPOTHESIS_CALIBRATION",
    "HAS_REBALANCE_PROPOSAL",
    "HAS_REBALANCE_SCENARIO",
}


def rule_id_from_payload(rule: Dict[str, object]) -> str:
    return str((rule or {}).get("rule_id") or (rule or {}).get("ruleId") or "").strip()


def rulebox_relation_subject_patterns(rules: List[Dict[str, object]]) -> Set[tuple]:
    """Return the exact subject side each native relation condition reads.

    The runtime ABox must not retain a relation merely because its type is
    used somewhere in RuleBox. For example, a portfolio-to-factor edge is not
    an input to a stock rule that reads ``stock -> HAS_FACTOR_EXPOSURE``.
    Keeping that distinction prevents volatile portfolio aggregates from
    forcing every stock scope into a new generation.
    """
    patterns = set()
    for rule in rules or []:
        if not isinstance(rule, dict) or rule.get("enabled") is False:
            continue
        source_kind = (
            str(rule.get("source_kind") or rule.get("sourceKind") or "stock").strip()
            or "stock"
        )
        for condition in rule.get("conditions") or []:
            if (
                not isinstance(condition, dict)
                or str(condition.get("kind") or "") != "relation"
            ):
                continue
            relation_type = (
                str(
                    condition.get("relation_type")
                    or condition.get("relationType")
                    or ""
                )
                .upper()
                .strip()
            )
            if not relation_type:
                continue
            direction = str(condition.get("direction") or "out").strip().lower()
            patterns.add(
                (source_kind, relation_type, "in" if direction == "in" else "out")
            )
    return patterns


def factual_runtime_metadata(
    metadata: Dict[str, object] = None,
    target_symbols=None,
    settings: Dict[str, object] = None,
) -> Dict[str, object]:
    """Keep historical market facts while removing derived decision output.

    Trend and change concepts still need the prior positions/watchlist
    snapshots. Their embedded decisions, AI context, and prior ontology
    output are rendered results, however, so carrying them into the next
    ABox would create a self-triggering inference loop.
    """
    source = dict(metadata or {})
    selected_symbols = {
        str(symbol or "").upper().strip()
        for symbol in target_symbols or []
        if str(symbol or "").strip()
    }

    def bounded_transition_rows(key: str, value: object) -> object:
        if key not in {
            MARKET_SIGNAL_TRANSITION_STATE_KEY,
            MARKET_SIGNAL_TRANSITION_RESULTS_KEY,
        }:
            return deepcopy(value)
        if not isinstance(value, dict) or not selected_symbols:
            return deepcopy(value)
        return {
            str(symbol): deepcopy(payload)
            for symbol, payload in value.items()
            if str(symbol or "").upper().strip() in selected_symbols
        }

    values = {}
    for key, value in source.items():
        if key in {
            "ontology",
            "hypothesisLifecycle",
            "reasoningSnapshotReplay",
            "previousMonitorState",
            "previousState",
            "monitorStateHistory",
        }:
            continue
        values[key] = bounded_transition_rows(str(key), value)

    # This marker describes how the worker acquired the snapshot. It is
    # operational replay provenance, not a market fact for the ABox.
    def factual_state(state: object) -> object:
        if not isinstance(state, dict):
            return state
        result = {
            key: deepcopy(value)
            for key, value in state.items()
            if key not in {"decisions", "externalSignals"}
        }
        signals = state.get("externalSignals")
        if isinstance(signals, dict):
            result["externalSignals"] = compact_external_signals_for_ontology(
                signals,
                target_symbols=target_symbols,
                settings=settings,
            )
        nested = result.get("metadata")
        if isinstance(nested, dict):
            nested = {
                key: bounded_transition_rows(str(key), value)
                for key, value in nested.items()
            }
            nested.pop("ontology", None)
            nested.pop("hypothesisLifecycle", None)
            nested.pop("reasoningSnapshotReplay", None)
            nested.pop("previousMonitorState", None)
            nested.pop("previousState", None)
            nested.pop("monitorStateHistory", None)
            result["metadata"] = nested
        return result

    if "previousMonitorState" in source:
        values["previousMonitorState"] = factual_state(
            source.get("previousMonitorState")
        )
    if isinstance(source.get("previousState"), dict):
        values["previousState"] = factual_state(source.get("previousState"))
    if isinstance(source.get("monitorStateHistory"), list):
        values["monitorStateHistory"] = [
            factual_state(item)
            for item in source.get("monitorStateHistory") or []
            if isinstance(item, dict)
        ]
    return values


def data_pipeline_health_context(snapshot: AccountSnapshot = None) -> Dict[str, object]:
    """Return only health that belongs to the snapshot being reasoned.

    The current pipeline read model is operational telemetry, not a market
    fact observed at an older snapshot. Feeding it into a retry made one
    frozen account snapshot alternately gain and lose missing-data facts.
    A future collector can persist ``dataPipelineHealth`` in snapshot
    metadata; until then, per-position source timestamps remain the
    investment freshness contract.
    """
    metadata = dict(getattr(snapshot, "metadata", {}) or {}) if snapshot else {}
    payload = metadata.get("dataPipelineHealth")
    return dict(payload or {}) if isinstance(payload, dict) else {}


def graph_for_graph_store_persistence(
    graph: PortfolioOntology,
    rule_catalog: Dict[str, object] = None,
    fallback_rules=None,
) -> PortfolioOntology:
    # TypeDB owns condition evaluation. Projection only retains relation
    # types referenced by the active TypeDB catalog and never evaluates
    # target values, thresholds, or polarity in Python.
    stripped_ids: Set[str] = set()
    abox_entities = []
    for item in graph.entities:
        box = str((item.properties or {}).get("ontologyBox") or "ABox")
        if box != "ABox":
            stripped_ids.add(item.entity_id)
            continue
        abox_entities.append(item)
    abox_relations = [
        item
        for item in graph.relations
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox"
        and item.source not in stripped_ids
        and item.target not in stripped_ids
    ]
    native_relation_types = {
        str(item or "").upper().strip()
        for item in (rule_catalog or {}).get("inputRelationTypes") or []
        if str(item or "").strip()
    }
    active_rules = [
        item
        for item in (rule_catalog or {}).get("rules") or []
        if isinstance(item, dict)
    ]
    if not active_rules:
        # ``ensure_rulebox_ready`` deliberately returns a compact catalog
        # for an immutable V2 release. The executable rule bodies remain
        # in the recorder and are still authoritative for deciding which
        # source kinds belong in the persisted ABox. Falling back to the
        # legacy stock/portfolio surface here removed standalone crypto
        # subjects before TypeDB could evaluate their native rules.
        active_rules = list(fallback_rules or [])
    subject_patterns = rulebox_relation_subject_patterns(active_rules)
    if not subject_patterns:
        # The bootstrap summary may omit full rules. Keep the historic
        # stock/portfolio surface in that narrow compatibility case.
        subject_patterns = {
            (source_kind, relation_type, direction)
            for source_kind in {"stock", "portfolio"}
            for relation_type in native_relation_types
            for direction in {"out", "in"}
        }
    source_kinds = {pattern[0] for pattern in subject_patterns}
    entity_by_id = {item.entity_id: item for item in abox_entities}
    # The active ABox is both TypeDB's native-rule input and the factual
    # investment world shown to diagnostics and AI. Keep the category
    # edges that define that world even when no currently enabled rule
    # consumes one of them. Otherwise a valid Price/Liquidity concept can
    # exist as an orphaned node, producing a misleading coverage gap.
    semantic_relation_types = ABOX_STRUCTURAL_RELATION_TYPES | {
        str(relation_type or "").upper().strip()
        for category_types in CATEGORY_RELATIONS.values()
        for relation_type in category_types
        if str(relation_type or "").strip()
    }
    persisted_relation_types = native_relation_types | semantic_relation_types
    source_ids = {
        item.entity_id for item in abox_entities if str(item.kind or "") in source_kinds
    }

    def matches_native_subject(relation) -> bool:
        relation_type = str(relation.relation_type or "").upper().strip()
        for source_kind, expected_type, direction in subject_patterns:
            if relation_type != expected_type:
                continue
            subject_id = relation.target if direction == "in" else relation.source
            subject = entity_by_id.get(subject_id)
            if subject and str(subject.kind or "") == source_kind:
                return True
        return False

    def should_persist_relation(relation) -> bool:
        if not persisted_relation_types:
            return relation.source in source_ids or relation.target in source_ids
        if matches_native_subject(relation):
            return True
        relation_type = str(relation.relation_type or "").upper().strip()
        return relation_type in (semantic_relation_types - native_relation_types) and (
            relation.source in source_ids or relation.target in source_ids
        )

    relations = [item for item in abox_relations if should_persist_relation(item)]
    # Temporal observations are intentionally structural rather than
    # direct RuleBox predicates. Once a native subject reaches a window,
    # retain the small connected observation chain so time-series
    # reasoning and diagnostics see the same episode.
    persisted_endpoint_ids = {
        endpoint
        for relation in relations
        for endpoint in (relation.source, relation.target)
        if str(endpoint or "").strip()
    } | set(source_ids)
    structural_relations = [
        item
        for item in abox_relations
        if str(item.relation_type or "").upper().strip()
        in ABOX_STRUCTURAL_RELATION_TYPES
    ]

    def equality_key(value):
        if isinstance(value, dict):
            return (
                "dict",
                tuple(
                    sorted(
                        ((key, equality_key(item)) for key, item in value.items()),
                        key=lambda row: repr(row[0]),
                    )
                ),
            )
        if isinstance(value, list):
            return ("list", tuple(equality_key(item) for item in value))
        if isinstance(value, tuple):
            return ("tuple", tuple(equality_key(item) for item in value))
        if isinstance(value, set):
            return ("set", frozenset(equality_key(item) for item in value))
        try:
            hash(value)
        except TypeError:
            return ("object", type(value).__qualname__, repr(value))
        return ("value", value)

    def relation_equality_key(relation):
        # OntologyRelation is a dataclass. Preserve its equality contract
        # while avoiding an O(structural-relations * retained-relations)
        # list scan during full-world recovery and contract migrations.
        return (
            relation.source,
            relation.target,
            relation.relation_type,
            relation.weight,
            equality_key(relation.evidence_ids),
            equality_key(relation.properties),
        )

    retained_relation_keys = {relation_equality_key(item) for item in relations}
    while True:
        additions = [
            item
            for item in structural_relations
            if relation_equality_key(item) not in retained_relation_keys
            and (
                item.source in persisted_endpoint_ids
                or item.target in persisted_endpoint_ids
            )
        ]
        if not additions:
            break
        relations.extend(additions)
        retained_relation_keys.update(relation_equality_key(item) for item in additions)
        persisted_endpoint_ids.update(
            endpoint
            for relation in additions
            for endpoint in (relation.source, relation.target)
            if str(endpoint or "").strip()
        )
    persisted_entity_ids = source_ids | {
        endpoint
        for relation in relations
        for endpoint in [relation.source, relation.target]
        if str(endpoint or "").strip()
    }
    entities = [
        item for item in abox_entities if item.entity_id in persisted_entity_ids
    ]
    evidence = [
        item
        for item in graph.evidence
        if str((item.value or {}).get("ontologyBox") or "ABox") == "ABox"
        and str(item.subject or "") in source_ids
    ]
    # Beliefs are reasoning output, not live observations. Persisting them
    # in the ABox duplicates the InferenceBox and forces unrelated scope
    # generations to be rewritten. Native rules consume the factual
    # entities, relations, and evidence above; derived beliefs remain in
    # their immutable InferenceBox generation.
    beliefs = []
    return PortfolioOntology(
        graph.portfolio_id,
        entities=entities,
        relations=relations,
        evidence=evidence,
        beliefs=beliefs,
        opinions=[],
        reasoning_cards=[],
        worldview={
            **dict(graph.worldview or {}),
            "runtimeProjectionMode": "abox-facts-only-typedb-native-rules",
            "runtimeProjectionScope": "typedb-rule-input-and-semantic-coverage-relations",
            "runtimeProjectionSourceEntityCount": len(source_ids),
            "runtimeProjectionRelationTypeCount": len(persisted_relation_types),
            "runtimeProjectionRelationSubjectPatternCount": len(subject_patterns),
            "runtimeProjectionRuleInputRelationTypeCount": len(native_relation_types),
            "runtimeProjectionSemanticRelationTypeCount": len(semantic_relation_types),
        },
        prompt=graph.prompt,
    )


def build_factual_graph(
    snapshot,
    observation_input,
    projection_external_signals,
    runtime_context,
    active_tbox,
    stage_timings,
):
    graph = build_portfolio_ontology(
        observation_input.get("positions") or [],
        snapshot.portfolio,
        # Current decisions are derived from a preceding TypeDB/AI
        # pass. Native rules must start from observed portfolio and
        # market facts, not use their own previous output as evidence.
        legacy_by_symbol={},
        external_signals=projection_external_signals,
        portfolio_id=snapshot.account_id,
        runtime_context=runtime_context,
        # The realtime path persists only ABox facts. Static TBox
        # vocabulary is seeded independently and presentation output is
        # rebuilt later from the active InferenceBox for an alert or UI.
        include_tbox=False,
        include_presentation=False,
        include_derived_decision_items=False,
        reference_positions=observation_input.get("referencePositions") or [],
    )
    # The graph builder's code-default TBox describes the source tree, but
    # an immutable V2 deployment executes the TBox frozen into its release.
    # Persisting the code default here made preflight and projected
    # fingerprints disagree, disabled exact rule-result slot reuse, and
    # forced the complete shared RuleBox to run for every observation.
    graph.worldview["activeTBox"] = deepcopy(active_tbox)
    event_validity_rows = [
        item for item in graph.entities if item.kind == "event-validity-assessment"
    ]
    stage_timings["eventValidityAssessmentCount"] = len(event_validity_rows)
    stage_timings["eventDecisionEligibleCount"] = sum(
        1
        for item in event_validity_rows
        if bool((item.properties or {}).get("eventDecisionEligible"))
    )
    stage_timings["eventExpiredCount"] = sum(
        1
        for item in event_validity_rows
        if str((item.properties or {}).get("eventLifecycleState") or "") == "expired"
    )

    return graph


def verify_calibration_lineage(graph, persistence_graph, stage_timings):
    source_calibration_ids = {
        item.entity_id
        for item in graph.entities
        if str(item.kind or "") == "hypothesis-calibration"
    }
    persisted_calibration_ids = {
        item.entity_id
        for item in persistence_graph.entities
        if str(item.kind or "") == "hypothesis-calibration"
    }
    source_calibration_relations = {
        (item.source, item.target, item.relation_type)
        for item in graph.relations
        if str(item.relation_type or "").upper().strip()
        in {"CALIBRATED_BY_OUTCOME", "HAS_HYPOTHESIS_CALIBRATION"}
        and (
            item.source in source_calibration_ids
            or item.target in source_calibration_ids
        )
    }
    persisted_calibration_relations = {
        (item.source, item.target, item.relation_type)
        for item in persistence_graph.relations
        if str(item.relation_type or "").upper().strip()
        in {"CALIBRATED_BY_OUTCOME", "HAS_HYPOTHESIS_CALIBRATION"}
    }
    missing_calibration_ids = sorted(source_calibration_ids - persisted_calibration_ids)
    missing_calibration_relations = sorted(
        source_calibration_relations - persisted_calibration_relations
    )
    stage_timings["hypothesisCalibrationSourceCount"] = len(source_calibration_ids)
    stage_timings["hypothesisCalibrationPersistedCount"] = len(
        persisted_calibration_ids
    )
    stage_timings["hypothesisCalibrationLineageComplete"] = (
        0 if missing_calibration_ids or missing_calibration_relations else 1
    )
    if missing_calibration_ids or missing_calibration_relations:
        raise RuntimeError(
            "ABox hypothesis calibration lineage was removed at the graph "
            "persistence boundary."
        )
