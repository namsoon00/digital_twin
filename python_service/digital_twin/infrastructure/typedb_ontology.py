import copy
import hashlib
import json
import math
import os
import re
import signal
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Dict, Iterable, List, Tuple

from ..domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology, entity_id
from ..domain.investment_ubiquitous_language import investment_language_registry
from ..domain.ontology_inference_materializer import (
    materialize_rule_inference,
    ontology_property_value_matches,
)
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from ..domain.ontology_rulebox_governance import (
    normalize_rule_change_candidate,
    rulebox_governance_candidates,
    rulebox_rules_hash,
)
from ..domain.ontology_change_impact import compact_inference_impact_plan
from ..domain.ontology_schema import default_tbox_metadata
from ..domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION, SCOPED_ABOX_PERSISTENCE_MODE
from .graph_store_inferencebox import (
    inferencebox_entity_payload,
    inferencebox_relation_payload,
    inferencebox_snapshot_from_rows,
    inferencebox_trace_payload,
)
from .graph_store_lifecycle import (
    active_tbox_metadata_from_rows,
    active_tbox_metadata_unavailable,
    graph_box_entity_counts,
    graph_box_relation_counts,
    ontology_seed_graph,
)
from .graph_store_payloads import (
    GraphStoreOntologyRowMapperMixin,
    condition_relation_filter_values,
    list_of_strings,
    number_or_none,
)
from .graph_store_rulebox import (
    rulebox_graph_from_rules,
    rulebox_rules_from_payload,
    rulebox_snapshot_from_rows,
    rulebox_rules_to_payload,
)
from .settings import runtime_settings, utc_now


class TypeDBOperationTimeout(TimeoutError):
    pass


@contextmanager
def typedb_operation_timeout(seconds: float, label: str):
    seconds = float(seconds or 0)
    if seconds <= 0 or threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        yield
        return

    def timeout_handler(_signum, _frame):
        raise TypeDBOperationTimeout(label + " timed out after " + str(round(seconds, 1)) + "s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer and previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def typedb_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def typedb_number(value: object):
    parsed = number_or_none(value)
    if parsed is None:
        return None
    numeric = float(parsed)
    return numeric if math.isfinite(numeric) else None


def typedb_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def typedb_error_code(error: object) -> str:
    text = str(error or "").lower()
    if any(term in text for term in ["unable to connect", "connection refused", "connect failed", "unavailable"]):
        return "typedbConnectionError"
    if any(term in text for term in ["timeout", "timed out", "deadline"]):
        return "typedbTimeout"
    if any(term in text for term in ["schema"]):
        return "typedbSchemaError"
    return "typedbReadError"


def clean_symbols_from_payload(value: object) -> List[str]:
    if isinstance(value, list):
        raw_values = value
    elif value in (None, ""):
        raw_values = []
    else:
        raw_values = [value]
    return sorted(set(str(item or "").upper().strip() for item in raw_values if str(item or "").strip()))


def materialization_preview_diff_payload(
    baseline_inferencebox: Dict[str, object],
    matched_count: int,
    candidate_rule_count: int,
    native_query_used: bool = False,
) -> Dict[str, object]:
    baseline = baseline_inferencebox if isinstance(baseline_inferencebox, dict) else {}
    baseline_relations = int(number_or_none(baseline.get("relationCount")) or 0)
    baseline_traces = int(number_or_none(baseline.get("traceCount")) or 0)
    matched = int(number_or_none(matched_count) or 0)
    return {
        "baselineRelationCount": baseline_relations,
        "baselineTraceCount": baseline_traces,
        "candidateMatchedCount": matched,
        "candidateRuleCount": int(number_or_none(candidate_rule_count) or 0),
        "matchedMinusBaselineRelations": matched - baseline_relations,
        "validationOnly": True,
        "mutatedOperationalRuleBox": False,
        "wroteInferenceBox": False,
        "nativeQueryUsed": bool(native_query_used),
    }


def inference_generation_id() -> str:
    return "inference-generation:" + uuid.uuid4().hex[:16]


def generated_inference_id(original_id: str, generation_id: str) -> str:
    original = str(original_id or "unknown")
    digest = hashlib.sha256((str(generation_id or "") + "|" + original).encode("utf-8")).hexdigest()[:12]
    return original + ":gen:" + digest


def rulebox_runtime_metadata(rules_payload: List[Dict[str, object]]) -> Dict[str, object]:
    rules_payload = [item for item in (rules_payload or []) if isinstance(item, dict)]
    rules_hash = rulebox_rules_hash(rules_payload)
    return {
        "ruleboxRulesHash": rules_hash,
        "ruleboxShortHash": rules_hash[:12],
        "ruleboxRuleCount": len(rules_payload),
        "ruleboxConditionCount": sum(len(item.get("conditions") or []) for item in rules_payload),
        "ruleboxDerivationCount": sum(len(item.get("derivations") or []) for item in rules_payload),
        "ruleboxEngineVersion": GRAPH_REASONER_VERSION,
    }


def rulebox_structural_fingerprint(rules_payload: List[Dict[str, object]]) -> Dict[str, Tuple[int, int]]:
    fingerprint: Dict[str, Tuple[int, int]] = {}
    for rule in rules_payload or []:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
        if not rule_id:
            continue
        fingerprint[rule_id] = (
            len(rule.get("conditions") or []),
            len(rule.get("derivations") or []),
        )
    return fingerprint


def node_boxes(graph: PortfolioOntology) -> List[str]:
    boxes = {
        str((item.properties or {}).get("ontologyBox") or "ABox")
        for item in graph.entities
        if str((item.properties or {}).get("ontologyBox") or "ABox")
    }
    boxes.update(
        str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "ABox"))
        for item in graph.evidence
        if str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "ABox"))
    )
    boxes.update(
        "InferenceBox" if str(item.belief_id or "").startswith("belief:inference:") else "ABox"
        for item in graph.beliefs
    )
    boxes.update(
        str((item.properties or {}).get("ontologyBox") or "ABox")
        for item in graph.relations
        if str((item.properties or {}).get("ontologyBox") or "ABox")
    )
    if graph.opinions or getattr(graph, "reasoning_cards", None):
        boxes.add("ABox")
    return sorted(boxes)


def typeql_has(attribute: str, value: object, numeric: bool = False) -> str:
    if value in (None, "", [], {}):
        return ""
    if numeric:
        parsed = typedb_number(value)
        if parsed is None:
            return ""
        return ", has " + attribute + " " + str(parsed)
    return ", has " + attribute + " " + typedb_string(value)


def typeql_has_bool_string(attribute: str, value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        normalized = "true" if value else "false"
    else:
        normalized = str(value).strip().lower()
        if normalized not in {"true", "false", "1", "0", "yes", "no", "y", "n", "on", "off"}:
            return ""
        normalized = "true" if normalized in {"true", "1", "yes", "y", "on"} else "false"
    return ", has " + attribute + " " + typedb_string(normalized)


def json_object(value: object) -> Dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def list_of_strings(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if value in (None, ""):
        return []
    return [str(value)]


def typeql_limit_clause(limit: int) -> str:
    safe_limit = int(limit or 0)
    return " limit " + str(safe_limit) + ";" if safe_limit > 0 else ""


def typedb_active_abox_pointer_clause(
    snapshot_variable: str = "$activeAboxSnapshotId",
    pointer_variable: str = "$activeAboxPointer",
) -> str:
    """Match the one control record that selects the live ABox generation."""
    return (
        str(pointer_variable or "$activeAboxPointer")
        + ' isa ontology-node, has ontology-kind "abox-active-pointer", '
        + 'has ontology-box "ABoxControl", has ontology-snapshot-id '
        + str(snapshot_variable or "$activeAboxSnapshotId")
        + ";"
    )


def typedb_active_abox_snapshot_clause(
    variable: str,
    snapshot_variable: str = "$activeAboxSnapshotId",
) -> str:
    return str(variable or "$item") + " has ontology-snapshot-id " + str(snapshot_variable or "$activeAboxSnapshotId") + ";"


def typedb_active_worldview_manifest_clause(
    manifest_variable: str = "$activeManifestPointer",
    manifest_id_variable: str = "$activeManifestId",
) -> str:
    """Match the single control record selecting the live scoped ABox world."""
    return (
        str(manifest_variable or "$activeManifestPointer")
        + ' isa ontology-node, has ontology-kind "worldview-manifest-active-pointer", '
        # Join through the same attribute type as the per-scope pointer. A
        # TypeQL variable cannot bridge values from two differently named
        # string attributes (snapshot id and manifest id), even when both
        # contain the same text.
        + 'has ontology-box "ABoxControl", has ontology-manifest-id '
        + str(manifest_id_variable or "$activeManifestId")
        + ";"
    )


def typedb_active_scoped_abox_member_clause(
    variable: str,
    prefix: str,
    manifest_id_variable: str = "",
) -> str:
    """Constrain one node or relation to the active Manifest scope pointer."""
    clean_prefix = re.sub(r"[^A-Za-z0-9_]", "", str(prefix or "item")) or "item"
    manifest_pointer = "$" + clean_prefix + "ActiveManifestPointer"
    manifest_id = str(manifest_id_variable or "$" + clean_prefix + "ActiveManifestId")
    return (
        typedb_active_worldview_manifest_clause(manifest_pointer, manifest_id)
        + " "
        + typedb_scoped_manifest_member_clause(variable, clean_prefix, manifest_id)
    )


def typedb_scoped_manifest_member_clause(
    variable: str,
    prefix: str,
    manifest_id_variable: str,
) -> str:
    """Constrain one fact through the active Manifest's scope pointer.

    A scope generation can be reused by many immutable Worldview Manifests.
    Its stored JSON and original `ontology-manifest-id` therefore describe the
    generation's creation provenance, not its current membership. The scope
    pointer is the durable source of truth for live ABox reads and native
    inference.
    """
    clean_prefix = re.sub(r"[^A-Za-z0-9_]", "", str(prefix or "item")) or "item"
    scope_pointer = "$" + clean_prefix + "ScopePointer"
    scope_id = "$" + clean_prefix + "ScopeId"
    scope_generation = "$" + clean_prefix + "ScopeGenerationId"
    return (
        scope_pointer + ' isa ontology-node, has ontology-kind "abox-scope-active-pointer", '
        + 'has ontology-box "ABoxControl", has ontology-manifest-id '
        + str(manifest_id_variable or "$activeManifestId")
        + ", has ontology-scope-id " + scope_id
        + ", has ontology-snapshot-id " + scope_generation + "; "
        + str(variable or "$item") + ' has ontology-box "ABox", has ontology-scope-id '
        + scope_id + ", has ontology-snapshot-id " + scope_generation + ";"
    )


def typedb_abox_inference_generation_id(metadata: Dict[str, object]) -> str:
    """Return the single ABox identity that an InferenceBox must reference.

    Scoped ABox rows deliberately use a distinct immutable generation ID per
    scope.  Those IDs cannot be compared as though they were one portfolio
    generation.  The Worldview Manifest is the atomic active-world identity;
    legacy ABox storage continues to use its snapshot ID until migrated.
    """
    payload = dict(metadata or {})
    if str(payload.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION:
        return str(payload.get("worldviewManifestId") or payload.get("aboxSnapshotId") or "").strip()
    return str(payload.get("aboxSnapshotId") or payload.get("worldviewManifestId") or "").strip()


def typedb_active_abox_member_clause(variable: str, prefix: str) -> str:
    """Match one fact from either supported live-ABox activation format.

    Scoped Manifests are the current format.  Keeping the legacy pointer as a
    read-only branch lets a rolling worker update its native TypeDB functions
    before the first scoped ABox projection completes, without treating an
    incomplete Manifest as live data.
    """
    clean_prefix = re.sub(r"[^A-Za-z0-9_]", "", str(prefix or "item")) or "item"
    legacy_pointer = "$" + clean_prefix + "LegacyAboxPointer"
    legacy_snapshot = "$" + clean_prefix + "LegacyAboxSnapshotId"
    scoped = typedb_active_scoped_abox_member_clause(variable, clean_prefix + "Scoped")
    legacy = (
        typedb_active_abox_pointer_clause(legacy_snapshot, legacy_pointer)
        + " " + str(variable or "$item")
        + ' has ontology-box "ABox", has ontology-snapshot-id ' + legacy_snapshot + ";"
    )
    return "{ " + scoped + " } or { " + legacy + " };"


def typedb_concept_value(concept: object):
    if concept is None:
        return None
    get_value = getattr(concept, "get_value", None)
    if callable(get_value):
        return get_value()
    # Aggregate TypeQL results are Value concepts. They expose typed getters
    # rather than get_value(), so inspect them before falling back to the type
    # label (for example, "integer" instead of the actual count).
    for getter_name in [
        "get_boolean",
        "get_integer",
        "get_double",
        "get_decimal",
        "get_string",
        "get_datetime_tz",
        "get_datetime",
        "get_date",
        "get_duration",
    ]:
        getter = getattr(concept, getter_name, None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                continue
    value = getattr(concept, "value", None)
    if callable(value):
        return value()
    if value is not None:
        return value
    get_label = getattr(concept, "get_label", None)
    if callable(get_label):
        return str(get_label())
    return concept


def typedb_row_value(row: object, name: str):
    if row is None:
        return None
    getter = getattr(row, "get", None)
    if callable(getter):
        try:
            return typedb_concept_value(getter(name))
        except Exception:
            return None
    if isinstance(row, dict):
        return typedb_concept_value(row.get(name))
    return None


def merge_flat_properties(row: Dict[str, object], props: Dict[str, object]) -> Dict[str, object]:
    merged = dict(props or {})
    nested = merged.get("properties") if isinstance(merged.get("properties"), dict) else {}
    if nested:
        merged.update(nested)
    for key, value in row.items():
        if value not in (None, "", [], {}):
            merged.setdefault(key, value)
    return merged


TYPEDB_NATIVE_REASONING_PROFILE_VERSION = "typedb-native-rule-profile-v5"
TYPEDB_NATIVE_RULE_ENGINE_VERSION = "typedb-schema-function-rule-engine-v5"
TYPEDB_NATIVE_REASONING_MODE = "typedb-native-rule-materialized"
TYPEDB_NATIVE_BLOCKED_MODE = "typedb-native-rule-materialization-blocked"
TYPEDB_NATIVE_REQUIRED_MODE = "typedb-native-rule-materialization-required"
TYPEDB_NATIVE_MATERIALIZATION_SOURCE = "typedb-abox-native-rule"
TYPEDB_NATIVE_REASONING_LAYER = "typedb-native-rule"
# Active ABox generations are selected through a control pointer. A dedicated
# function namespace makes deployed schema functions refresh when that query
# contract changes instead of silently reusing an older unscoped definition.
# Version 5 binds the active Worldview Manifest once per function and resolves
# each fact through its active scope pointer. Scope generations are immutable
# and can be reused by a newer Manifest, so an item's original manifest id is
# provenance, not proof that it belongs to the current world.
TYPEDB_SCHEMA_FUNCTION_PREFIX = "orbit_rule_active_manifest_subject_v5_"
# Scoped ABox writes span many short TypeDB transactions. Keep their lease in
# a separate control box so pointer replacement cannot delete it mid-write.
SCOPED_ABOX_WRITE_LEASE_ID = "scoped-abox-write-lease"
SCOPED_ABOX_WRITE_LEASE_BOX = "ABoxLease"
SCOPED_ABOX_WRITE_LEASE_VERSION = "scoped-abox-write-lease-v1"
# The production RuleBox currently plans up to 57 native functions for one
# material symbol.  Measured local execution completes in roughly 16 seconds,
# while the most complex combination rule can take just over four seconds.
# Keep enough headroom for a complete generation; the reasoning scheduler's
# projection cadence and circuit breaker bound aggregate CPU separately.
DEFAULT_TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS = 6.0
DEFAULT_TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS = 30.0
TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES = {
    "currentPrice": "ontology-current-price",
    "averagePrice": "ontology-average-price",
    "marketValue": "ontology-market-value",
    "quantity": "ontology-quantity",
    "sellableQuantity": "ontology-sellable-quantity",
    "positionWeight": "ontology-position-weight-pct",
    "positionAccountWeight": "ontology-position-account-weight-pct",
    "changeRate": "ontology-change-rate",
    "priceChangeRate": "ontology-price-change-rate",
    "ma5": "ontology-ma5",
    "ma20": "ontology-ma20",
    "ma60": "ontology-ma60",
    "ma5Distance": "ontology-ma5-distance",
    "ma20Distance": "ontology-ma20-distance",
    "ma60Distance": "ontology-ma60-distance",
    "ma20Slope": "ontology-ma20-slope",
    "ma60Slope": "ontology-ma60-slope",
    "trendCurve": "ontology-trend-curve",
    "volume": "ontology-volume",
    "volumeRatio": "ontology-volume-ratio",
    "rawVolumeRatio": "ontology-raw-volume-ratio",
    "timeAdjustedVolumeRatio": "ontology-time-adjusted-volume-ratio",
    "expectedVolumeRatioNow": "ontology-expected-volume-ratio-now",
    "tradeStrength": "ontology-trade-strength",
    "tradingValue": "ontology-trading-value",
    "reportedTradingValue": "ontology-reported-trading-value",
    "estimatedTradingValue": "ontology-estimated-trading-value",
    "tradingValueMismatchPct": "ontology-trading-value-mismatch-pct",
    "bidAskImbalance": "ontology-bid-ask-imbalance",
    "foreignNetVolume": "ontology-foreign-net-volume",
    "foreignNetAmount": "ontology-foreign-net-amount",
    "institutionNetVolume": "ontology-institution-net-volume",
    "institutionNetAmount": "ontology-institution-net-amount",
    "individualNetVolume": "ontology-individual-net-volume",
    "individualNetAmount": "ontology-individual-net-amount",
    "smartMoneyNetVolume": "ontology-smart-money-net-volume",
    "adrRatio": "ontology-adr-ratio",
    "adrPriceUsd": "ontology-adr-price-usd",
    "adrVolume": "ontology-adr-volume",
    "usdKrwRate": "ontology-usd-krw-rate",
    "localPriceKrw": "ontology-local-price-krw",
    "localEquivalentKrw": "ontology-local-equivalent-krw",
    "leverageFactor": "ontology-leverage-factor",
    "price": "ontology-price",
    "fairValue": "ontology-fair-value",
    "fairValuePrice": "ontology-fair-value-price",
    "fairValueLow": "ontology-fair-value-low",
    "fairValueBase": "ontology-fair-value-base",
    "fairValueHigh": "ontology-fair-value-high",
    "marginOfSafetyPct": "ontology-margin-of-safety-pct",
    "conservativeMarginOfSafetyPct": "ontology-conservative-margin-of-safety-pct",
    "optimisticMarginOfSafetyPct": "ontology-optimistic-margin-of-safety-pct",
    "expensivePremiumPct": "ontology-expensive-premium-pct",
    "minimumMarginOfSafetyPct": "ontology-minimum-margin-of-safety-pct",
    "valuationDecisionEligible": "ontology-valuation-decision-eligible",
    "valuationModelCount": "ontology-valuation-model-count",
    "valuationConsensusPrice": "ontology-valuation-consensus-price",
    "valuationDisagreementPct": "ontology-valuation-disagreement-pct",
    "expectedEPS": "ontology-expected-eps",
    "reportedEPS": "ontology-reported-eps",
    "estimatedEPS": "ontology-estimated-eps",
    "targetPER": "ontology-target-per",
    "forwardPE": "ontology-forward-pe",
    "pegRatio": "ontology-peg-ratio",
    "dividendYield": "ontology-dividend-yield",
    "peerPER": "ontology-peer-per",
    "historicalMedianPER": "ontology-historical-median-per",
    "lookbackDays": "ontology-lookback-days",
    "requiredSampleCount": "ontology-required-sample-count",
    "sampleCount": "ontology-sample-count",
    "coverageRatio": "ontology-coverage-ratio",
    "elapsedHours": "ontology-elapsed-hours",
    "startPrice": "ontology-start-price",
    "priceChangePct": "ontology-price-change-pct",
    "peakPrice": "ontology-peak-price",
    "troughPrice": "ontology-trough-price",
    "peakReturnPct": "ontology-peak-return-pct",
    "troughReturnPct": "ontology-trough-return-pct",
    "drawdownFromPeakPct": "ontology-drawdown-from-peak-pct",
    "reboundFromTroughPct": "ontology-rebound-from-trough-pct",
    "priorPriceChangePct": "ontology-prior-price-change-pct",
    "recentPriceChangePct": "ontology-recent-price-change-pct",
    "priceVelocityChangePct": "ontology-price-velocity-change-pct",
    "consecutiveDeclineCount": "ontology-consecutive-decline-count",
    "consecutiveAdvanceCount": "ontology-consecutive-advance-count",
    "directionChangeCount": "ontology-direction-change-count",
    "validObservationCount": "ontology-valid-observation-count",
    "invalidObservationCount": "ontology-invalid-observation-count",
    "staleObservationCount": "ontology-stale-observation-count",
    "validObservationRatio": "ontology-valid-observation-ratio",
    "profitLossRateStart": "ontology-profit-loss-rate-start",
    "profitLossRateEnd": "ontology-profit-loss-rate-end",
    "profitLossRateChangePct": "ontology-profit-loss-rate-change-pct",
    "ma20DistanceStart": "ontology-ma20-distance-start",
    "ma20DistanceEnd": "ontology-ma20-distance-end",
    "ma20DistanceChange": "ontology-ma20-distance-change",
    "ma20DistancePeak": "ontology-ma20-distance-peak",
    "ma20DistanceTrough": "ontology-ma20-distance-trough",
    "ma20ReclaimCount": "ontology-ma20-reclaim-count",
    "ma20BreakCount": "ontology-ma20-break-count",
    "ma20ObservationCount": "ontology-ma20-observation-count",
    "ma60DistanceStart": "ontology-ma60-distance-start",
    "ma60DistanceEnd": "ontology-ma60-distance-end",
    "volumeRatioEnd": "ontology-volume-ratio-end",
    "tradeStrengthEnd": "ontology-trade-strength-end",
    "bidAskImbalanceEnd": "ontology-bid-ask-imbalance-end",
    "smartMoneyNetLatest": "ontology-smart-money-net-latest",
    "smartMoneyNetChange": "ontology-smart-money-net-change",
    "smartMoneyObservationCount": "ontology-smart-money-observation-count",
    "smartMoneyDistinctObservationCount": "ontology-smart-money-distinct-observation-count",
    "individualNetLatest": "ontology-individual-net-latest",
    "eventCount": "ontology-event-count",
    "riskEventCount": "ontology-risk-event-count",
    "supportEventCount": "ontology-support-event-count",
}
TYPEDB_PROMOTED_TEXT_ATTRIBUTES = {
    "investmentStrategyProfile": "ontology-investment-strategy-profile",
    "investmentStrategyProfileLabel": "ontology-investment-strategy-profile-label",
    "positionRole": "ontology-position-role",
    "targetPositionRole": "ontology-target-position-role",
    "positionIntent": "ontology-position-intent",
    "positionIntentLabel": "ontology-position-intent-label",
    "positionIntentDescription": "ontology-position-intent-description",
    "instrumentArchetype": "ontology-instrument-archetype",
    "instrumentArchetypes": "ontology-instrument-archetype",
    "archetypeLabel": "ontology-instrument-archetype-label",
    "archetypeLabels": "ontology-instrument-archetype-label",
    "instrumentArchetypeLabels": "ontology-instrument-archetype-label",
    "factor": "ontology-factor",
    "sensitivityLevel": "ontology-sensitivity-level",
    "cryptoSymbol": "ontology-crypto-symbol",
    "actionPolicy": "ontology-action-policy",
    "tradingValueQuality": "ontology-trading-value-quality",
    "tradingValueBasis": "ontology-trading-value-basis",
    "securityLineRole": "ontology-security-line-role",
    "localSymbol": "ontology-local-symbol",
    "companyName": "ontology-company-name",
    "market": "ontology-market",
    "currency": "ontology-currency",
    "exchange": "ontology-exchange",
    "adrSymbol": "ontology-adr-symbol",
    "etfSymbol": "ontology-etf-symbol",
    "underlyingSymbol": "ontology-underlying-symbol",
    "conversionStartDate": "ontology-conversion-start-date",
    "listingDate": "ontology-listing-date",
    "sourceUrl": "ontology-source-url",
    "valuationMethod": "ontology-valuation-method",
    "formula": "ontology-formula",
    "epsPeriod": "ontology-eps-period",
    "multiplePeriod": "ontology-multiple-period",
    "valuationAsOf": "ontology-valuation-as-of",
    "valuationFreshnessStatus": "ontology-valuation-freshness-status",
    "valuationDataStateLabel": "ontology-valuation-data-state-label",
    "valuationDataState": "ontology-valuation-data-state",
    "valuationInputState": "ontology-valuation-input-state",
    "valuationReliabilityState": "ontology-valuation-reliability-state",
    "valuationSourceType": "ontology-valuation-source-type",
    "valuationCurrency": "ontology-valuation-currency",
    "valuationConsensusStatus": "ontology-valuation-consensus-status",
    "perValuationStatus": "ontology-per-valuation-status",
    "perValuationReason": "ontology-per-valuation-reason",
    "preferredValuationMetric": "ontology-preferred-valuation-metric",
    "fundamentalDataSourcePriority": "ontology-fundamental-data-source-priority",
    "windowKey": "ontology-window-key",
    "hasSufficientHistory": "ontology-has-sufficient-history",
    "latestObservationQuality": "ontology-latest-observation-quality",
    "sequenceRole": "ontology-sequence-role",
    "observationQuality": "ontology-observation-quality",
    "observedAt": "ontology-observed-at",
    "provider": "ontology-provider",
    "pricePathPattern": "ontology-price-path-pattern",
    "flowPattern": "ontology-flow-pattern",
    "eventClusterType": "ontology-event-cluster-type",
    "trendEpisodeType": "ontology-trend-episode-type",
    "registryVersion": "ontology-language-registry-version",
    "termId": "ontology-language-term-id",
    "termCategory": "ontology-language-term-category",
    "termStatus": "ontology-language-term-status",
    "termVersion": "ontology-language-term-version",
    "preferredLabel": "ontology-language-preferred-label",
    "deliveryLevel": "ontology-language-delivery-level",
    "deliveryLevelLabel": "ontology-language-delivery-level-label",
    "renderedLabel": "ontology-language-rendered-label",
    "smartMoneyDirection": "ontology-smart-money-direction",
    "investorFlowPsychology": "ontology-investor-flow-psychology",
    "investorFlowEvidenceRole": "ontology-investor-flow-evidence-role",
    "investorFlowDataState": "ontology-investor-flow-data-state",
    "investorFlowReviewLevel": "ontology-investor-flow-review-level",
    "trendRiskState": "ontology-trend-risk-state",
    "trendReviewLevel": "ontology-trend-review-level",
    "trendEvidenceRole": "ontology-trend-evidence-role",
    "trendDataState": "ontology-trend-data-state",
    "liquidityState": "ontology-liquidity-state",
    "liquidityReviewLevel": "ontology-liquidity-review-level",
    "liquidityDataState": "ontology-liquidity-data-state",
    "sourceDataState": "ontology-source-data-state",
    "externalSignalDataState": "ontology-external-signal-data-state",
    "relevanceState": "ontology-relevance-state",
    "sourceTrustState": "ontology-source-trust-state",
    "materialityState": "ontology-materiality-state",
}
TYPEDB_FUNCTION_SUBJECT_FIELDS = {
    "source",
    "symbol",
    "kind",
    "ontologyBox",
    "tboxClass",
    "profitLossRate",
    "value",
    "valueNumber",
} | set(TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.keys()) | set(TYPEDB_PROMOTED_TEXT_ATTRIBUTES.keys())
TYPEDB_FUNCTION_TARGET_FILTERS = {
    "field",
    "levelType",
    "dataScope",
    "domainScope",
    "relationScope",
    "group",
    "polarity",
    "eventType",
    "materialityPassed",
    "materialityState",
    "relevanceState",
    "sourceTrustState",
    "valuationDataState",
    "minValue",
    "maxValue",
    "tboxClass",
    "tboxClasses",
    "allowAddOnStrength",
    "trimOnTrendBreak",
    "avoidAveragingDown",
    "impactPolarity",
    "needsReview",
    "readScope",
    "peRatio",
    "beta",
} | set(TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.keys()) | set(TYPEDB_PROMOTED_TEXT_ATTRIBUTES.keys())
TYPEDB_FUNCTION_RELATION_FILTERS = {
    "field",
    "signalGroup",
    "polarity",
    "transitionType",
    "materialityPassed",
    "materialityState",
    "relevanceState",
    "sourceTrustState",
    "evidenceRole",
    "reviewLevel",
    "dataState",
    "changeState",
    "conflictState",
}
TYPEDB_FUNCTION_OPERATORS = {"==", "eq", "!=", "ne", "<=", "lte", ">=", "gte", "<", "lt", ">", "gt", "exists", "present"}
TYPEDB_STRING_ATTRIBUTES = {
    "ontology-id",
    "ontology-label",
    "ontology-kind",
    "ontology-box",
    "ontology-symbol",
    "ontology-rule-id",
    "ontology-account-id",
    "ontology-snapshot-id",
    "ontology-scope-id",
    "ontology-scope-type",
    "ontology-manifest-id",
    "ontology-tbox-class",
    "ontology-relation-type",
    "ontology-updated-at",
    "ontology-json",
    "ontology-source-value",
    "ontology-field",
    "ontology-level-type",
    "ontology-data-scope",
    "ontology-domain-scope",
    "ontology-relation-scope",
    "ontology-group",
    "ontology-polarity",
    "ontology-evidence-role",
    "ontology-review-level",
    "ontology-data-state",
    "ontology-change-state",
    "ontology-conflict-state",
    "ontology-validation-state",
    "ontology-transition-type",
    "ontology-signal-group",
    "ontology-event-type",
    "ontology-materiality-passed",
    "ontology-allow-add-on-strength",
    "ontology-trim-on-trend-break",
    "ontology-avoid-averaging-down",
    "ontology-impact-polarity",
    "ontology-needs-review",
    "ontology-read-scope",
} | set(TYPEDB_PROMOTED_TEXT_ATTRIBUTES.values())
TYPEDB_NUMERIC_ATTRIBUTES = {
    "ontology-weight",
    "ontology-value-number",
    "ontology-profit-loss-rate",
    "ontology-pe-ratio",
    "ontology-beta",
} | set(TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.values())


class ScopedABoxManifestMixin:
    """Shared immutable scoped-ABox persistence contract.

    The concrete TypeDB repository supplies TypeQL I/O and row mapping.  The
    disabled repository inherits the conservative fallback methods below, and
    the concrete repository inherits the scoped persistence implementation.
    Keeping the lifecycle in one mixin prevents the two adapters from slowly
    diverging as the ABox contract evolves.
    """
    store_key = "typedb"
    store_label = "TypeDB"

    def active_tbox_metadata(self) -> Dict[str, object]:
        metadata = default_tbox_metadata()
        metadata.update({
            "configured": False,
            "status": "code-fallback",
            "source": "code",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        })
        return metadata

    def active_abox_metadata(self) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "aboxSnapshotId": "",
            "materialFingerprint": "",
        }

    def active_abox_uses_scoped_manifest(self) -> bool:
        """Whether live ABox reads must resolve through scope pointers.

        This deliberately reads the durable manifest marker instead of
        inferring the mode from a transient worker setting.  A failed
        activation therefore continues to read the previous complete world.
        """
        try:
            metadata = self.active_abox_metadata()
        except Exception:  # noqa: BLE001 - retain legacy reads during recovery.
            return False
        return (
            str(metadata.get("status") or "") == "ok"
            and str(metadata.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
        )

    def active_abox_members_clause(self, members: Iterable[Tuple[str, str]]) -> str:
        """Build one active-world constraint for a TypeQL query.

        Runtime reads must not repeat the scoped-or-legacy activation branch
        for every endpoint. Once a scoped Manifest is active, one control
        lookup plus active scope-pointer membership predicates is sufficient.
        The legacy branch remains available only until the first scoped
        migration has completed.
        """
        normalized = [
            (str(variable or "$item"), str(prefix or "item"))
            for variable, prefix in members or []
        ]
        if not normalized:
            return ""
        if self.active_abox_uses_scoped_manifest():
            manifest_id = "$activeManifestId"
            return " ".join([
                typedb_active_worldview_manifest_clause("$activeManifestPointer", manifest_id),
                *[
                    typedb_scoped_manifest_member_clause(variable, prefix, manifest_id)
                    for variable, prefix in normalized
                ],
            ])
        return " ".join(
            typedb_active_abox_member_clause(variable, prefix)
            for variable, prefix in normalized
        )

    def scoped_abox_storage_diagnostics(self) -> Dict[str, object]:
        """Describe active logical scopes separately from physical ABox rows.

        Operators previously saw one large ABox count and could not tell
        whether it was the current investment world or retained immutable
        history.  This compact diagnostic avoids exporting graph payloads and
        makes that distinction explicit.
        """
        try:
            active = self.active_abox_metadata()
        except Exception as error:  # noqa: BLE001 - status endpoints must stay available.
            return {
                "configured": bool(getattr(self, "address", "")),
                "status": "error",
                "graphStore": "typedb",
                "reason": str(error)[:180],
            }
        scoped = str(active.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
        if not scoped:
            return {
                "configured": bool(getattr(self, "address", "")),
                "status": str(active.get("status") or "legacy"),
                "graphStore": "typedb",
                "persistenceMode": "immutable-complete-generation",
                "activeAboxSnapshotId": str(active.get("aboxSnapshotId") or ""),
                "reason": "Active ABox has not yet been migrated to a scoped Worldview Manifest.",
            }
        scope_plan = list(active.get("scopePlan") or [])
        logical_entities = sum(int(number_or_none(item.get("entityCount")) or 0) for item in scope_plan if isinstance(item, dict))
        logical_relations = sum(int(number_or_none(item.get("relationCount")) or 0) for item in scope_plan if isinstance(item, dict))
        scope_type_counts: Dict[str, int] = {}
        scope_family_counts: Dict[str, int] = {}
        for item in scope_plan:
            if not isinstance(item, dict):
                continue
            scope_type = str(item.get("scopeType") or str(item.get("scopeId") or "").split(":", 1)[0] or "reference")
            scope_type_counts[scope_type] = scope_type_counts.get(scope_type, 0) + 1
            scope_family = str(item.get("scopeFamily") or "").strip()
            if not scope_family:
                parts = [part for part in str(item.get("scopeId") or "").split(":") if part]
                scope_family = parts[2] if len(parts) >= 3 and parts[0] == "symbol" else (parts[0] if parts else "reference")
            scope_family_counts[scope_family] = scope_family_counts.get(scope_family, 0) + 1
        result = {
            "configured": bool(getattr(self, "address", "")),
            "status": str(active.get("status") or "ok"),
            "graphStore": "typedb",
            "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
            "worldviewManifestId": str(active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""),
            "activeScopeCount": len(scope_plan),
            "scopeTypeCounts": dict(sorted(scope_type_counts.items())),
            "scopeTopologyVersion": str(active.get("scopeTopologyVersion") or ""),
            "scopeFamilyCounts": dict(sorted(scope_family_counts.items())),
            "logicalActiveEntityCount": logical_entities,
            "logicalActiveRelationCount": logical_relations,
            "scopeIds": [str(item.get("scopeId") or "") for item in scope_plan if isinstance(item, dict)][:120],
            "keepInactiveManifestCount": self.abox_inactive_generation_keep_count(),
            "maxInactiveManifestsPrunedPerRun": self.abox_inactive_generation_max_prune_per_save(),
        }
        try:
            markers = list(self.worldview_manifest_marker_rows())
            manifest_ids = {
                str(item.get("worldviewManifestId") or item.get("aboxSnapshotId") or item.get("snapshotId") or "")
                for item in markers
            }
            manifest_ids.discard("")
            generation_references: Dict[str, int] = {}
            for marker in markers:
                for generation_id in dict(marker.get("scopeGenerationIds") or {}).values():
                    clean_generation_id = str(generation_id or "")
                    if clean_generation_id:
                        generation_references[clean_generation_id] = generation_references.get(clean_generation_id, 0) + 1
            result.update({
                "storedManifestCount": len(manifest_ids),
                "inactiveManifestCount": max(0, len(manifest_ids) - 1),
                "storedScopeGenerationCount": len(generation_references),
                "sharedHistoricalScopeGenerationCount": len([
                    generation_id for generation_id, count in generation_references.items()
                    if count > 1
                ]),
            })
        except Exception as error:  # noqa: BLE001 - physical counts are diagnostic only.
            result["manifestInventoryStatus"] = "error"
            result["manifestInventoryReason"] = str(error)[:180]
        try:
            physical = self.box_row_counts("ABox")
            result.update({
                "physicalAboxEntityCount": int(physical.get("entityCount") or 0),
                "physicalAboxRelationCount": int(physical.get("relationCount") or 0),
            })
        except Exception as error:  # noqa: BLE001 - preserve logical lifecycle status.
            result["physicalCountStatus"] = "error"
            result["physicalCountReason"] = str(error)[:180]
        try:
            result["writeLease"] = self.scoped_abox_write_lease_status()
        except Exception as error:  # noqa: BLE001 - lease visibility must not hide active world state.
            result["writeLease"] = {
                "status": "error",
                "reason": str(error)[:180],
            }
        return result

    def scoped_abox_write_lease_seconds(self, settings: Dict[str, object] = None) -> int:
        """Return a bounded cross-process lease for one scoped ABox writer."""
        raw = (settings or runtime_settings()).get("typedbScopedABoxLeaseSeconds")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 900
        # A first migration writes a full world in bounded batches. The lease
        # must outlast a normal write, while still recovering after a crashed
        # local worker instead of blocking the graph indefinitely.
        return max(120, min(3600, int(parsed)))

    def scoped_abox_orphan_cleanup_max_generations(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbScopedABoxOrphanCleanupMaxGenerations")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 4
        # Inventory is cheap compared with deletion. Keep routine cleanup
        # short so it never monopolizes TypeDB's single writer before a live
        # market update can be projected.
        return max(1, min(20, int(parsed)))

    @staticmethod
    def scoped_abox_write_lease_storage_id() -> str:
        return ontology_storage_id(
            {"ontologyBox": SCOPED_ABOX_WRITE_LEASE_BOX},
            SCOPED_ABOX_WRITE_LEASE_ID,
            "node",
        )

    def scoped_abox_write_lease_rows(self) -> List[Dict[str, object]]:
        """Read the durable lease without treating it as an ontology fact."""
        query = (
            "match $n isa ontology-node, "
            "has ontology-id " + typedb_string(SCOPED_ABOX_WRITE_LEASE_ID) + ", "
            "has ontology-box " + typedb_string(SCOPED_ABOX_WRITE_LEASE_BOX) + ", "
            "has ontology-storage-id " + typedb_string(self.scoped_abox_write_lease_storage_id()) + ", "
            "has ontology-updated-at $updatedAt, has ontology-json $json;"
        )
        return self.read_rows(
            query,
            ["updatedAt", "json"],
            label="typedb.scoped-abox-write-lease",
        )

    def scoped_abox_write_lease_status(self) -> Dict[str, object]:
        rows = list(self.scoped_abox_write_lease_rows() or [])
        if not rows:
            return {
                "status": "empty",
                "leaseId": SCOPED_ABOX_WRITE_LEASE_ID,
                "leaseBox": SCOPED_ABOX_WRITE_LEASE_BOX,
            }
        row = sorted(rows, key=lambda item: str(item.get("updatedAt") or ""), reverse=True)[0]
        payload = json_object(row.get("json"))
        expires_at = float(number_or_none(payload.get("leaseExpiresAtEpoch")) or 0)
        owner = str(payload.get("leaseOwner") or "")
        lease_host = str(payload.get("leaseHost") or "")
        lease_process_id = number_or_none(payload.get("leaseProcessId"))
        status = "held" if expires_at > time.time() else "expired"
        return {
            "status": status,
            "leaseId": SCOPED_ABOX_WRITE_LEASE_ID,
            "leaseBox": SCOPED_ABOX_WRITE_LEASE_BOX,
            "leaseOwner": owner,
            "leaseHost": lease_host,
            "leaseProcessId": int(lease_process_id) if lease_process_id is not None else None,
            "leaseExpiresAtEpoch": expires_at,
            "updatedAt": str(row.get("updatedAt") or ""),
            "propertiesJson": str(row.get("json") or "{}"),
        }

    def scoped_abox_write_lease_graph(
        self,
        owner: str,
        manifest_id: str = "",
        lease_seconds: int = 0,
    ) -> Tuple[PortfolioOntology, Dict[str, object]]:
        acquired_at = time.time()
        lease_settings = (
            {"typedbScopedABoxLeaseSeconds": lease_seconds}
            if int(lease_seconds or 0) > 0
            else None
        )
        expires_at = acquired_at + self.scoped_abox_write_lease_seconds(lease_settings)
        properties = {
            "ontologyBox": SCOPED_ABOX_WRITE_LEASE_BOX,
            "tboxClass": "ScopedABoxWriteLease",
            "leaseVersion": SCOPED_ABOX_WRITE_LEASE_VERSION,
            "leaseOwner": str(owner or ""),
            "leaseManifestId": str(manifest_id or ""),
            # A durable lease can outlive a force-stopped local worker.  These
            # fields let the replacement worker reclaim only a proven-dead
            # local owner; they are not used to steal a live or remote lease.
            "leaseHost": socket.gethostname(),
            "leaseProcessId": os.getpid(),
            "leaseAcquiredAtEpoch": acquired_at,
            "leaseExpiresAtEpoch": expires_at,
        }
        graph = PortfolioOntology(
            "typedb-scoped-abox-lease",
            entities=[OntologyEntity(
                entity_id=SCOPED_ABOX_WRITE_LEASE_ID,
                label="Scoped ABox write lease",
                kind="scoped-abox-write-lease",
                properties=properties,
            )],
        )
        row = self.node_rows(graph)[0]
        return graph, {
            "owner": str(owner or ""),
            "manifestId": str(manifest_id or ""),
            "expiresAtEpoch": expires_at,
            "propertiesJson": str(row.get("propertiesJson") or "{}"),
        }

    def delete_scoped_abox_write_lease(
        self,
        driver,
        imported,
        lease: Dict[str, object],
    ) -> Dict[str, object]:
        """Delete only the exact owner record, never a successor's lease."""
        owner = str((lease or {}).get("owner") or "")
        properties_json = str((lease or {}).get("propertiesJson") or "")
        if not owner or not properties_json:
            return {"status": "skipped", "reason": "Lease ownership payload is incomplete."}
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        query = (
            "match $n isa ontology-node, has ontology-storage-id "
            + typedb_string(self.scoped_abox_write_lease_storage_id())
            + ", has ontology-json " + typedb_string(properties_json)
            + "; delete $n;"
        )

        def operation():
            with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB scoped ABox lease release"):
                with driver.transaction(
                    self.database,
                    TransactionType.WRITE,
                    options=self.write_transaction_options(),
                ) as tx:
                    tx.query(query).resolve()
                    tx.commit()

        self.with_typedb_retries(operation)
        return {"status": "released", "leaseOwner": owner}

    def acquire_scoped_abox_write_lease(self, manifest_id: str = "") -> Dict[str, object]:
        """Serialize multi-transaction scoped writes across local workers.

        A TypeDB write transaction protects only one batch. Without this lease,
        two projections can both clear a shared macro/reference generation and
        leave each other with an incomplete candidate. The lease itself is
        outside ABox/ABoxControl so activation swaps do not affect it.
        """
        owner = "scoped-abox:" + uuid.uuid4().hex
        existing = self.scoped_abox_write_lease_status()
        if str(existing.get("status") or "") == "held":
            return {
                "acquired": False,
                "status": "held",
                "leaseOwner": str(existing.get("leaseOwner") or ""),
                "leaseExpiresAtEpoch": float(number_or_none(existing.get("leaseExpiresAtEpoch")) or 0),
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "acquired": False,
                "status": "driver-missing",
                "reason": str(imported[1])[:180],
            }
        graph, lease = self.scoped_abox_write_lease_graph(owner, manifest_id)

        def operation():
            driver = self.open_driver(imported)
            try:
                self.ensure_database(driver)
                self.ensure_schema(driver, imported)
                if str(existing.get("status") or "") == "expired":
                    self.delete_scoped_abox_write_lease(driver, imported, {
                        "owner": str(existing.get("leaseOwner") or "expired"),
                        "propertiesJson": str(existing.get("propertiesJson") or ""),
                    })
                try:
                    self.write_graph(driver, imported, graph, delete_boxes=[])
                except Exception:
                    current = self.scoped_abox_write_lease_status()
                    if str(current.get("status") or "") in {"held", "expired"}:
                        return {
                            "acquired": False,
                            "status": "held",
                            "leaseOwner": str(current.get("leaseOwner") or ""),
                            "leaseExpiresAtEpoch": float(number_or_none(current.get("leaseExpiresAtEpoch")) or 0),
                        }
                    raise
            finally:
                self.close_driver(driver)
            current = self.scoped_abox_write_lease_status()
            if str(current.get("leaseOwner") or "") != owner:
                return {
                    "acquired": False,
                    "status": "held",
                    "leaseOwner": str(current.get("leaseOwner") or ""),
                    "leaseExpiresAtEpoch": float(number_or_none(current.get("leaseExpiresAtEpoch")) or 0),
                }
            return {
                "acquired": True,
                "status": "acquired",
                "leaseOwner": owner,
                "leaseExpiresAtEpoch": float(lease.get("expiresAtEpoch") or 0),
                "propertiesJson": str(lease.get("propertiesJson") or "{}"),
            }

        return self.with_typedb_retries(operation)

    def release_scoped_abox_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        if not (lease or {}).get("acquired"):
            return {"status": "not-owner"}
        imported = self.driver_imports()
        if imported[0] is None:
            return {"status": "driver-missing", "reason": str(imported[1])[:180]}

        def operation():
            driver = self.open_driver(imported)
            try:
                self.ensure_database(driver)
                return self.delete_scoped_abox_write_lease(driver, imported, {
                    **dict(lease or {}),
                    "owner": str((lease or {}).get("owner") or (lease or {}).get("leaseOwner") or ""),
                })
            finally:
                self.close_driver(driver)

        return self.with_typedb_retries(operation)

    @staticmethod
    def local_process_alive(process_id: object) -> bool:
        """Return whether a locally recorded lease owner still exists."""
        try:
            pid = int(process_id or 0)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # A permission error proves that a process exists, even though the
            # local user cannot signal it.
            return True
        except OSError:
            # Treat an unknown OS state as live: recovery must be conservative.
            return True
        return True

    def recover_dead_local_scoped_abox_write_lease(self) -> Dict[str, object]:
        """Release a held lease only when its local owner process is gone.

        This covers a project worker restart without requiring a TypeDB server
        restart.  Legacy rows without owner host/PID and rows owned by another
        host intentionally remain until normal expiry, so an operator cannot
        accidentally steal an active cross-process writer.
        """
        if not str(getattr(self, "address", "") or "").strip():
            return {
                "configured": False,
                "status": "disabled",
                "graphStore": "typedb",
                "reason": "TypeDB ontology storage is not configured.",
            }
        try:
            existing = self.scoped_abox_write_lease_status()
        except Exception as error:  # noqa: BLE001 - recovery must never block the worker startup.
            return {
                "configured": True,
                "status": "unavailable",
                "graphStore": "typedb",
                "reason": str(error)[:180],
            }
        if str(existing.get("status") or "") != "held":
            return {
                "configured": True,
                "status": "skipped",
                "graphStore": "typedb",
                "reason": "No held scoped ABox write lease requires local recovery.",
            }
        payload = json_object(existing.get("propertiesJson"))
        lease_host = str(existing.get("leaseHost") or payload.get("leaseHost") or "").strip()
        lease_process_id = existing.get("leaseProcessId")
        if lease_process_id in (None, ""):
            lease_process_id = payload.get("leaseProcessId")
        if not lease_host or lease_process_id in (None, ""):
            return {
                "configured": True,
                "status": "legacy-owner-unknown",
                "graphStore": "typedb",
                "leaseOwner": str(existing.get("leaseOwner") or ""),
                "reason": "Held lease has no local owner metadata and will expire normally.",
            }
        try:
            local_process_id = int(lease_process_id)
        except (TypeError, ValueError):
            return {
                "configured": True,
                "status": "invalid-owner",
                "graphStore": "typedb",
                "leaseOwner": str(existing.get("leaseOwner") or ""),
                "reason": "Held lease has an invalid local process identifier and will expire normally.",
            }
        if local_process_id <= 0:
            return {
                "configured": True,
                "status": "invalid-owner",
                "graphStore": "typedb",
                "leaseOwner": str(existing.get("leaseOwner") or ""),
                "reason": "Held lease has no valid local process identifier and will expire normally.",
            }
        if lease_host != socket.gethostname():
            return {
                "configured": True,
                "status": "foreign-owner",
                "graphStore": "typedb",
                "leaseOwner": str(existing.get("leaseOwner") or ""),
                "leaseHost": lease_host,
                "reason": "Held lease belongs to another host and cannot be reclaimed locally.",
            }
        if self.local_process_alive(local_process_id):
            return {
                "configured": True,
                "status": "active-owner",
                "graphStore": "typedb",
                "leaseOwner": str(existing.get("leaseOwner") or ""),
                "leaseHost": lease_host,
                "leaseProcessId": local_process_id,
                "reason": "Held lease owner process is still alive.",
            }
        owner = str(existing.get("leaseOwner") or "")
        properties_json = str(existing.get("propertiesJson") or "")
        if not owner or not properties_json:
            return {
                "configured": True,
                "status": "invalid",
                "graphStore": "typedb",
                "reason": "Dead local lease has no exact ownership payload.",
            }
        try:
            release = self.release_scoped_abox_write_lease({
                "acquired": True,
                "owner": owner,
                "leaseOwner": owner,
                "propertiesJson": properties_json,
            })
        except Exception as error:  # noqa: BLE001 - normal expiry remains the final fallback.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "leaseOwner": owner,
                "reason": str(error)[:180],
            }
        return {
            "configured": True,
            "status": "cleared" if str((release or {}).get("status") or "") == "released" else "error",
            "graphStore": "typedb",
            "previousLeaseOwner": owner,
            "previousLeaseHost": lease_host,
            "previousLeaseProcessId": local_process_id,
            "release": dict(release or {}),
        }

    def recover_scoped_abox_write_lease_after_server_start(self) -> Dict[str, object]:
        """Clear a lease after TypeDB itself has restarted.

        A scoped ABox writer holds a durable lease across bounded TypeDB write
        transactions. A fresh TypeDB server cannot still have a writer from the
        previous server process, so the service manager can reclaim this row
        before any dependent workers start. A normal live seed must never pass
        this recovery path.
        """
        if not str(getattr(self, "address", "") or "").strip():
            return {
                "configured": False,
                "status": "disabled",
                "graphStore": "typedb",
                "reason": "TypeDB ontology storage is not configured.",
            }
        try:
            existing = self.scoped_abox_write_lease_status()
        except Exception as error:  # noqa: BLE001 - a fresh database may not have schema rows yet.
            return {
                "configured": True,
                "status": "unavailable",
                "graphStore": "typedb",
                "reason": str(error)[:180],
            }
        if str(existing.get("status") or "") == "empty":
            return {
                "configured": True,
                "status": "empty",
                "graphStore": "typedb",
            }
        owner = str(existing.get("leaseOwner") or "")
        properties_json = str(existing.get("propertiesJson") or "")
        if not owner or not properties_json:
            return {
                "configured": True,
                "status": "invalid",
                "graphStore": "typedb",
                "reason": "Scoped ABox write lease has no exact ownership payload.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "configured": True,
                "status": "driver-missing",
                "graphStore": "typedb",
                "reason": str(imported[1])[:180],
            }

        def operation():
            driver = self.open_driver(imported)
            try:
                self.ensure_database(driver)
                self.ensure_schema(driver, imported)
                return self.delete_scoped_abox_write_lease(driver, imported, {
                    "owner": owner,
                    "propertiesJson": properties_json,
                })
            finally:
                self.close_driver(driver)

        try:
            deleted = self.with_typedb_retries(operation)
        except Exception as error:  # noqa: BLE001 - seed can continue and surface the recovery state.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "previousLeaseOwner": owner,
                "reason": str(error)[:180],
            }
        return {
            "configured": True,
            "status": "cleared" if str((deleted or {}).get("status") or "") == "released" else str((deleted or {}).get("status") or "error"),
            "graphStore": "typedb",
            "previousLeaseOwner": owner,
            "previousLeaseExpiresAtEpoch": float(number_or_none(existing.get("leaseExpiresAtEpoch")) or 0),
            "release": dict(deleted or {}),
        }

    def recover_scoped_abox_write_lease_after_managed_shutdown(self) -> Dict[str, object]:
        """Recover only a proven-dead local writer after worker restart.

        A project manager restart does not prove that an independently started
        CLI process is absent. Reuse the local owner identity check instead of
        treating a worker restart like a TypeDB server restart.
        """
        return self.recover_dead_local_scoped_abox_write_lease()

    def recover_pending_abox_activation(self) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        }

    def discard_abox_generation(self, snapshot_id: str) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "aboxSnapshotId": str(snapshot_id or ""),
            "reason": "TypeDB ontology storage is not configured.",
        }

    def prune_inactive_abox_generations(
        self,
        _driver=None,
        _imported=None,
        active_snapshot_id: str = "",
        keep_inactive_count: int = 0,
        max_generations: int = 1,
    ) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "activeAboxSnapshotId": str(active_snapshot_id or ""),
            "retainedInactiveSnapshotIds": [],
            "removedCandidateSnapshotIds": [],
            "deletedBatchCount": 0,
        }

    @staticmethod
    def is_scoped_abox_graph(graph: PortfolioOntology) -> bool:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        return (
            str(worldview.get("persistenceMode") or "") == SCOPED_ABOX_PERSISTENCE_MODE
            and str(worldview.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
            and isinstance(worldview.get("scopePlan"), list)
        )

    @staticmethod
    def scoped_abox_plan(graph: PortfolioOntology) -> List[Dict[str, object]]:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        rows = []
        for item in worldview.get("scopePlan") or []:
            if not isinstance(item, dict):
                continue
            scope_id = str(item.get("scopeId") or "").strip()
            generation_id = str(item.get("generationId") or "").strip()
            if not scope_id or not generation_id:
                continue
            rows.append({
                "scopeId": scope_id,
                "scopeType": str(item.get("scopeType") or scope_id.split(":", 1)[0] or "reference"),
                "scopeFamily": str(item.get("scopeFamily") or ""),
                "fingerprint": str(item.get("fingerprint") or ""),
                "baseFingerprint": str(item.get("baseFingerprint") or ""),
                "dependencyScopeIds": [
                    str(value or "")
                    for value in item.get("dependencyScopeIds") or []
                    if str(value or "").strip()
                ],
                "generationId": generation_id,
                "entityCount": int(number_or_none(item.get("entityCount")) or 0),
                "relationCount": int(number_or_none(item.get("relationCount")) or 0),
                "evidenceCount": int(number_or_none(item.get("evidenceCount")) or 0),
            })
        return sorted(rows, key=lambda item: str(item.get("scopeId") or ""))

    def scoped_abox_persistence_rows(
        self,
        graph: PortfolioOntology,
        scope_ids: Iterable[str],
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
        """Build changed scope rows while resolving endpoints from full context."""
        selected = {str(item or "").strip() for item in scope_ids or [] if str(item or "").strip()}
        all_node_rows = [
            row for row in self.node_rows(graph)
            if str(row.get("ontologyBox") or "ABox") == "ABox"
        ]
        nodes_by_id = {
            str(row.get("id") or ""): row
            for row in all_node_rows
            if str(row.get("id") or "")
        }
        changed_nodes = [
            row for row in all_node_rows
            if str(row.get("scopeId") or "") in selected
        ]
        relation_rows: List[Dict[str, object]] = []
        for raw in self.rows_for_relations(graph) + self.support_relation_rows(graph):
            if str(raw.get("ontologyBox") or "ABox") != "ABox":
                continue
            source_id = str(raw.get("source") or "")
            target_id = str(raw.get("target") or "")
            if not str(raw.get("scopeId") or ""):
                source_row = nodes_by_id.get(source_id) or {}
                raw = {
                    **raw,
                    "scopeId": str(source_row.get("scopeId") or ""),
                    "scopeType": str(source_row.get("scopeType") or ""),
                    "manifestId": str(source_row.get("manifestId") or ""),
                    "scopeGenerationId": str(source_row.get("scopeGenerationId") or source_row.get("snapshotId") or ""),
                    "snapshotId": str(raw.get("snapshotId") or source_row.get("snapshotId") or ""),
                    "aboxSnapshotId": str(raw.get("aboxSnapshotId") or source_row.get("aboxSnapshotId") or ""),
                }
            if str(raw.get("scopeId") or "") not in selected:
                continue
            source_row = nodes_by_id.get(source_id)
            target_row = nodes_by_id.get(target_id)
            if not source_row or not target_row:
                continue
            relation_rows.append({
                **raw,
                "sourceStorageId": ontology_storage_id(source_row, source_id, "node"),
                "targetStorageId": ontology_storage_id(target_row, target_id, "node"),
            })
        return changed_nodes, relation_rows

    def scoped_abox_manifest_generation_references(self) -> Dict[str, object]:
        """Return generations protected by a durable Worldview Manifest."""
        manifests = set()
        generations = set()
        for marker in self.worldview_manifest_marker_rows():
            manifest_id = str(
                marker.get("worldviewManifestId")
                or marker.get("aboxSnapshotId")
                or marker.get("snapshotId")
                or ""
            ).strip()
            if manifest_id:
                manifests.add(manifest_id)
                generations.add(manifest_id)
            for generation_id in dict(marker.get("scopeGenerationIds") or {}).values():
                clean_generation_id = str(generation_id or "").strip()
                if clean_generation_id:
                    generations.add(clean_generation_id)
        try:
            active = self.active_abox_metadata()
        except Exception:  # noqa: BLE001 - caller still protects marker-backed generations.
            active = {}
        active_manifest_id = str(
            active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
        ).strip()
        if active_manifest_id:
            manifests.add(active_manifest_id)
        for generation_id in dict(active.get("scopeGenerationIds") or {}).values():
            clean_generation_id = str(generation_id or "").strip()
            if clean_generation_id:
                generations.add(clean_generation_id)
        return {
            "manifestIds": manifests,
            "generationIds": generations,
            "activeManifestId": active_manifest_id,
        }

    def scoped_abox_orphan_candidate_inventory(self) -> Dict[str, object]:
        """Find staged scoped rows not owned by any complete Manifest.

        Interrupted writes cannot have a manifest marker because the marker is
        inserted only after per-scope row verification. They are therefore
        safe to reclaim, except for a generation already referenced by a
        complete active or retained historical Manifest.
        """
        protected = self.scoped_abox_manifest_generation_references()
        protected_manifests = set(protected.get("manifestIds") or set())
        protected_generations = set(protected.get("generationIds") or set())
        candidate_manifests = set()
        candidate_generations = set()
        for type_label in ["ontology-node", "ontology-assertion"]:
            rows = self.read_rows(
                "match $item isa " + type_label
                + ', has ontology-box "ABox", has ontology-manifest-id $manifestId, '
                + "has ontology-snapshot-id $snapshotId;",
                ["manifestId", "snapshotId"],
                label="typedb.scoped-abox-orphan-inventory",
            )
            for row in rows:
                manifest_id = str(row.get("manifestId") or "").strip()
                generation_id = str(row.get("snapshotId") or "").strip()
                if not manifest_id.startswith("abox-manifest:") or not generation_id.startswith("abox-scope:"):
                    continue
                if manifest_id in protected_manifests or generation_id in protected_generations:
                    continue
                candidate_manifests.add(manifest_id)
                candidate_generations.add(generation_id)
        return {
            "candidateManifestIds": sorted(candidate_manifests),
            "candidateGenerationIds": sorted(candidate_generations),
            "protectedManifestIds": sorted(protected_manifests),
            "protectedGenerationIds": sorted(protected_generations),
        }

    def cleanup_orphan_scoped_abox_candidates(
        self,
        driver,
        imported,
        max_generation_count: int = 0,
    ) -> Dict[str, object]:
        """Reclaim incomplete scoped candidates while the scoped write lease is held."""
        inventory = self.scoped_abox_orphan_candidate_inventory()
        deleted_batches = 0
        removed_generation_ids = []
        failures = []
        candidates = list(inventory.get("candidateGenerationIds") or [])
        maximum = (
            self.scoped_abox_orphan_cleanup_max_generations()
            if int(max_generation_count or 0) <= 0
            else max(1, int(max_generation_count))
        )
        selected = candidates[:maximum]
        for generation_id in selected:
            try:
                result = self.delete_box_snapshot_rows_in_batches(
                    driver,
                    imported,
                    "ABox",
                    str(generation_id),
                )
                deleted_batches += int(number_or_none(result.get("deletedBatchCount")) or 0)
                if str(result.get("status") or "") in {"ok", "skipped"}:
                    removed_generation_ids.append(str(generation_id))
                else:
                    failures.append({
                        "generationId": str(generation_id),
                        "status": str(result.get("status") or "error"),
                        "reason": str(result.get("reason") or ""),
                    })
            except Exception as error:  # noqa: BLE001 - keep the candidate invisible and report cleanup state.
                failures.append({
                    "generationId": str(generation_id),
                    "status": "error",
                    "reason": str(error)[:180],
                })
        return {
            "status": "ok" if not failures and len(selected) == len(candidates) else "partial",
            "candidateManifestIds": list(inventory.get("candidateManifestIds") or []),
            "removedGenerationIds": removed_generation_ids,
            "deletedBatchCount": deleted_batches,
            "failures": failures,
            "remainingGenerationIds": candidates[len(selected):],
            "maxGenerationCount": maximum,
        }

    def prune_orphan_scoped_abox_candidates(self) -> Dict[str, object]:
        """Run orphan candidate reclamation as deferred maintenance only."""
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "configured": bool(getattr(self, "address", "")),
                "status": "driver-missing",
                "graphStore": "typedb",
                "reason": str(imported[1])[:180],
            }
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    return self.cleanup_orphan_scoped_abox_candidates(driver, imported)
                finally:
                    self.close_driver(driver)

            result = self.with_typedb_retries(operation)
            return {"configured": True, "graphStore": "typedb", **dict(result or {})}
        except Exception as error:  # noqa: BLE001 - leave invisible candidates for the next idle pass.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }

    def scoped_abox_scope_row_counts(self, scope_id: str, generation_id: str) -> Dict[str, int]:
        clean_scope = str(scope_id or "").strip()
        clean_generation = str(generation_id or "").strip()
        if not clean_scope or not clean_generation:
            return {"entityCount": 0, "relationCount": 0}

        def count(type_label: str) -> int:
            query = (
                "match $item isa " + type_label
                + ', has ontology-box "ABox"'
                + ", has ontology-scope-id " + typedb_string(clean_scope)
                + ", has ontology-snapshot-id " + typedb_string(clean_generation)
                + "; reduce $count = count;"
            )
            rows = self.read_rows(query, ["count"], label="typedb.scoped-abox-count")
            return int(number_or_none((rows[0] if rows else {}).get("count")) or 0)

        return {
            "entityCount": count("ontology-node"),
            "relationCount": count("ontology-assertion"),
        }

    def scoped_abox_scope_row_counts_batch(
        self,
        scope_rows: Iterable[Dict[str, object]],
    ) -> Dict[str, Dict[str, int]]:
        """Read persisted counts for every staged scope with two TypeQL reductions.

        The initial migration from a broad legacy scope layout can change well
        over one hundred scopes.  Issuing two independent TypeQL reads for
        each scope made that correctness check dominate the migration.  The
        scope generation remains part of the grouping key, so this is still
        an exact physical-write verification rather than an in-memory proxy.
        """
        expected_pairs = {
            (
                str(item.get("scopeId") or "").strip(),
                str(item.get("generationId") or "").strip(),
            )
            for item in scope_rows or []
            if isinstance(item, dict)
            and str(item.get("scopeId") or "").strip()
            and str(item.get("generationId") or "").strip()
        }
        counts = {
            scope_id: {"entityCount": 0, "relationCount": 0}
            for scope_id, _generation_id in expected_pairs
        }
        if not expected_pairs:
            return counts

        def collect(type_label: str, count_key: str) -> None:
            query = (
                "match $item isa " + type_label
                + ', has ontology-box "ABox"'
                + ", has ontology-scope-id $scopeId"
                + ", has ontology-snapshot-id $generationId"
                + "; reduce $count = count groupby $scopeId, $generationId;"
            )
            rows = self.read_rows(
                query,
                ["scopeId", "generationId", "count"],
                label="typedb.scoped-abox-count-batch",
            )
            for row in rows or []:
                scope_id = str(row.get("scopeId") or "").strip()
                generation_id = str(row.get("generationId") or "").strip()
                if (scope_id, generation_id) not in expected_pairs:
                    continue
                counts.setdefault(scope_id, {"entityCount": 0, "relationCount": 0})[count_key] = int(
                    number_or_none(row.get("count")) or 0
                )

        collect("ontology-node", "entityCount")
        collect("ontology-assertion", "relationCount")
        return counts

    def write_persistence_rows(
        self,
        driver,
        imported,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
    ) -> None:
        """Write pre-resolved rows without requiring every endpoint in a slice."""
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        settings = runtime_settings()
        queries = [
            *self.batched_node_insert_queries(
                node_rows,
                utc_now(),
                int(number_or_none(settings.get("typedbABoxNodeBatchSize")) or 100),
                self.write_query_max_bytes(settings),
            ),
            *self.batched_relation_insert_queries(
                relation_rows,
                utc_now(),
                self.abox_relation_batch_size(settings),
                self.write_query_max_bytes(settings),
            ),
        ]
        batch_size = self.abox_write_transaction_query_count()
        for offset in range(0, len(queries), batch_size):
            query_batch = queries[offset: offset + batch_size]
            if not query_batch:
                continue

            def write_batch():
                with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB scoped ABox write batch"):
                    with driver.transaction(
                        self.database,
                        TransactionType.WRITE,
                        options=self.write_transaction_options(),
                    ) as tx:
                        for query in query_batch:
                            tx.query(query).resolve()
                        tx.commit()

            self.with_typedb_retries(write_batch)

    def scoped_manifest_marker_graph(
        self,
        graph: PortfolioOntology,
        scope_plan: List[Dict[str, object]],
        changed_scope_ids: Iterable[str],
    ) -> PortfolioOntology:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        manifest_id = str(worldview.get("worldviewManifestId") or worldview.get("aboxSnapshotId") or "").strip()
        if not manifest_id:
            return PortfolioOntology(str(graph.portfolio_id or "typedb-scoped-manifest"))
        marker_scope_id = "manifest:" + manifest_id
        marker = OntologyEntity(
            entity_id="worldview-manifest-marker:" + manifest_id,
            label="Worldview Manifest " + manifest_id,
            kind="worldview-manifest-marker",
            properties={
                "ontologyBox": "ABox",
                "tboxClass": "WorldviewManifest",
                "snapshotId": manifest_id,
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "aboxScopeId": marker_scope_id,
                "aboxScopeType": "manifest",
                "scopeGenerationId": manifest_id,
                "materialFingerprint": str(worldview.get("materialFingerprint") or ""),
                "projectionRunId": str(worldview.get("projectionRunId") or ""),
                "asOf": str(worldview.get("asOf") or worldview.get("generatedAt") or utc_now()),
                "scopePlan": list(scope_plan),
                "scopeGenerationIds": dict(worldview.get("scopeGenerationIds") or {}),
                "scopeFingerprints": dict(worldview.get("scopeFingerprints") or {}),
                "scopeTopologyVersion": str(worldview.get("scopeTopologyVersion") or ""),
                "scopeFamilyCounts": dict(worldview.get("scopeFamilyCounts") or {}),
                "scopeDelta": dict(worldview.get("scopeDelta") or {}),
                "inferenceImpactPlan": dict(worldview.get("inferenceImpactPlan") or {}),
                "changedScopeIds": sorted({str(item or "") for item in changed_scope_ids if str(item or "")}),
                "projectionStatus": "complete",
                "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            },
        )
        return PortfolioOntology(str(graph.portfolio_id or "typedb-scoped-manifest"), entities=[marker])

    def scoped_manifest_pointer_graph(
        self,
        graph: PortfolioOntology,
        scope_plan: List[Dict[str, object]],
        previous_metadata: Dict[str, object] = None,
        pending_activation: bool = True,
    ) -> PortfolioOntology:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        manifest_id = str(worldview.get("worldviewManifestId") or worldview.get("aboxSnapshotId") or "").strip()
        if not manifest_id:
            return PortfolioOntology(str(graph.portfolio_id or "typedb-scoped-control"))
        previous = dict(previous_metadata or {})
        previous_manifest_id = str(previous.get("worldviewManifestId") or previous.get("aboxSnapshotId") or "").strip()
        common = {
            "ontologyBox": "ABoxControl",
            "worldviewManifestId": manifest_id,
            "materialFingerprint": str(worldview.get("materialFingerprint") or ""),
            "projectionRunId": str(worldview.get("projectionRunId") or ""),
            "asOf": str(worldview.get("asOf") or worldview.get("generatedAt") or utc_now()),
            "scopePlan": list(scope_plan),
            "scopeGenerationIds": dict(worldview.get("scopeGenerationIds") or {}),
            "scopeFingerprints": dict(worldview.get("scopeFingerprints") or {}),
            "scopeTopologyVersion": str(worldview.get("scopeTopologyVersion") or ""),
            "scopeFamilyCounts": dict(worldview.get("scopeFamilyCounts") or {}),
            "scopeDelta": dict(worldview.get("scopeDelta") or {}),
            "inferenceImpactPlan": dict(worldview.get("inferenceImpactPlan") or {}),
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
        }
        entities = [OntologyEntity(
            entity_id="worldview-manifest-active-pointer",
            label="Active Worldview Manifest",
            kind="worldview-manifest-active-pointer",
            properties={
                **common,
                "tboxClass": "WorldviewManifestActivePointer",
                "snapshotId": manifest_id,
                "aboxSnapshotId": manifest_id,
            },
        )]
        for item in scope_plan:
            scope_id = str(item.get("scopeId") or "").strip()
            generation_id = str(item.get("generationId") or "").strip()
            if not scope_id or not generation_id:
                continue
            digest = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:16]
            entities.append(OntologyEntity(
                entity_id="abox-scope-active-pointer:" + digest,
                label="Active ABox scope " + scope_id,
                kind="abox-scope-active-pointer",
                properties={
                    "ontologyBox": "ABoxControl",
                    "tboxClass": "ABoxScopeActivePointer",
                    "snapshotId": generation_id,
                    "aboxSnapshotId": generation_id,
                    "worldviewManifestId": manifest_id,
                    "aboxScopeId": scope_id,
                    "aboxScopeType": str(item.get("scopeType") or scope_id.split(":", 1)[0]),
                    "scopeGenerationId": generation_id,
                    "scopeFingerprint": str(item.get("fingerprint") or ""),
                },
            ))
        if pending_activation and previous_manifest_id != manifest_id:
            entities.append(OntologyEntity(
                entity_id="abox-activation-pending",
                label="Worldview Manifest activation pending native inference",
                kind="abox-activation-pending",
                properties={
                    **common,
                    "tboxClass": "ABoxActivationPending",
                    "snapshotId": manifest_id,
                    "aboxSnapshotId": manifest_id,
                    "candidateAboxSnapshotId": manifest_id,
                    "candidateWorldviewManifestId": manifest_id,
                    "previousAboxSnapshotId": previous_manifest_id,
                    "previousWorldviewManifestId": previous_manifest_id,
                    "previousScopeGenerationIds": dict(previous.get("scopeGenerationIds") or {}),
                    "targetSymbols": clean_symbols_from_payload(
                        worldview.get("inferenceTargetSymbols") or worldview.get("targetSymbols") or []
                    ),
                    "activationStatus": "pending-native-inference",
                },
            ))
        return PortfolioOntology(str(graph.portfolio_id or "typedb-scoped-control"), entities=entities)

    def scoped_manifest_pending_graph(
        self,
        graph: PortfolioOntology,
        scope_plan: List[Dict[str, object]],
        previous_metadata: Dict[str, object] = None,
    ) -> PortfolioOntology:
        """Persist a verified candidate journal without moving the live world.

        Candidate scope generations and their complete Manifest marker can be
        safely written before native inference.  The small pending control
        record makes that hand-off durable while leaving the prior active
        Worldview Manifest readable until the reasoning worker explicitly
        prepares the candidate.
        """
        control = self.scoped_manifest_pointer_graph(
            graph,
            scope_plan,
            previous_metadata=previous_metadata,
            pending_activation=True,
        )
        pending_entities = [
            entity
            for entity in control.entities
            if str(entity.kind or "") == "abox-activation-pending"
        ]
        for entity in pending_entities:
            entity.properties["activationStatus"] = "staged-native-inference"
        return PortfolioOntology(
            str(graph.portfolio_id or "typedb-scoped-control"),
            entities=pending_entities,
        )

    def save_scoped_abox_graph(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str] = None,
    ) -> Dict[str, object]:
        """Stage changed scopes before native inference activates a Manifest."""
        scope_plan = self.scoped_abox_plan(graph)
        worldview = dict(getattr(graph, "worldview", {}) or {})
        manifest_id = str(worldview.get("worldviewManifestId") or worldview.get("aboxSnapshotId") or "").strip()
        if not scope_plan or not manifest_id:
            return {
                "configured": True,
                "saved": False,
                "status": "invalid-scoped-abox",
                "graphStore": "typedb",
                "reason": "Scoped ABox graph has no complete scope plan or Manifest id.",
            }
        # The active pointer protects read consistency, but individual scoped
        # writes commit in bounded batches. Take a durable lease before even
        # looking at the active Manifest so two workers cannot delete a shared
        # macro/reference generation while each is staging a successor.
        write_lease = self.acquire_scoped_abox_write_lease(manifest_id)
        if not write_lease.get("acquired"):
            return {
                "configured": True,
                "saved": False,
                "status": "deferred-scoped-write-lease",
                "graphStore": "typedb",
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "preservedActiveGeneration": True,
                "reason": "Another scoped ABox projection is still staging or activating a Worldview Manifest.",
                "writeLease": {
                    key: value
                    for key, value in dict(write_lease or {}).items()
                    if key != "propertiesJson"
                },
            }
        lease_released = False

        def release_write_lease() -> Dict[str, object]:
            nonlocal lease_released
            if lease_released:
                return {"status": "already-released"}
            lease_released = True
            try:
                return self.release_scoped_abox_write_lease(write_lease)
            except Exception as error:  # noqa: BLE001 - expiry protects the next retry if release fails.
                return {"status": "error", "reason": str(error)[:180]}

        try:
            pending_before = self.pending_abox_activation()
        except Exception as error:  # noqa: BLE001 - do not overlap two uncertain generations.
            release = release_write_lease()
            return {
                "configured": True,
                "saved": False,
                "status": "pending-abox-activation-unreadable",
                "graphStore": "typedb",
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "preservedActiveGeneration": True,
                "reason": "Pending ABox activation could not be read: " + str(error)[:180],
                "writeLeaseRelease": release,
            }
        if str(pending_before.get("status") or "") == "pending":
            pending_manifest_id = str(pending_before.get("candidateAboxSnapshotId") or "").strip()
            release = release_write_lease()
            same_manifest = pending_manifest_id == manifest_id
            return {
                "configured": True,
                "saved": False,
                "status": "staged-scoped-manifest" if same_manifest else "deferred-pending-scoped-manifest",
                "graphStore": "typedb",
                "aboxSnapshotId": pending_manifest_id or manifest_id,
                "worldviewManifestId": pending_manifest_id or manifest_id,
                "preservedActiveGeneration": True,
                "pendingAboxActivation": pending_before,
                "reason": (
                    "This Worldview Manifest is already staged and awaits native inference."
                    if same_manifest
                    else "A different staged Worldview Manifest must finish or roll back before another ABox write."
                ),
                "writeLeaseRelease": release,
            }

        try:
            active_before = self.active_abox_metadata()
        except Exception:
            active_before = {}
        active_fingerprints = dict(active_before.get("scopeFingerprints") or {})
        active_generations = dict(active_before.get("scopeGenerationIds") or {})
        scoped_active = str(active_before.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
        changed_scope_ids = [
            str(item.get("scopeId") or "")
            for item in scope_plan
            if not scoped_active
            or str(active_fingerprints.get(str(item.get("scopeId") or "")) or "") != str(item.get("fingerprint") or "")
            or str(active_generations.get(str(item.get("scopeId") or "")) or "") != str(item.get("generationId") or "")
        ]
        changed_scope_ids = [item for item in changed_scope_ids if item]
        previous_manifest_id = str(active_before.get("worldviewManifestId") or active_before.get("aboxSnapshotId") or "").strip()
        if scoped_active and previous_manifest_id == manifest_id and not changed_scope_ids:
            release_write_lease()
            return {
                "configured": True,
                "saved": False,
                "status": "unchanged-scoped-manifest",
                "graphStore": "typedb",
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "changedScopeIds": [],
                "scopePlan": scope_plan,
                "activeAbox": active_before,
            }
        node_rows, relation_rows = self.scoped_abox_persistence_rows(graph, changed_scope_ids)
        scope_rows = {str(item.get("scopeId") or ""): item for item in scope_plan}
        # The logical scope plan counts domain entities/relations.  Physical
        # persistence can additionally contain evidence/support nodes, so the
        # write verification must use the exact rows being written.
        expected_rows_by_scope: Dict[str, Dict[str, int]] = {
            scope_id: {"entityCount": 0, "relationCount": 0}
            for scope_id in changed_scope_ids
        }
        for row in node_rows:
            scope_id = str(row.get("scopeId") or "")
            if scope_id in expected_rows_by_scope:
                expected_rows_by_scope[scope_id]["entityCount"] += 1
        for row in relation_rows:
            scope_id = str(row.get("scopeId") or "")
            if scope_id in expected_rows_by_scope:
                expected_rows_by_scope[scope_id]["relationCount"] += 1
        verification: Dict[str, object] = {}
        timing: Dict[str, object] = {"startedAt": utc_now()}
        save_started_at = time.monotonic()
        imported = self.driver_imports()
        if imported[0] is None:
            release_write_lease()
            return self.driver_missing_result(imported[1], graph)
        orphan_cleanup: Dict[str, object] = {
            "status": "deferred",
            "reason": "Orphan scoped ABox candidates are reclaimed by idle maintenance.",
        }
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    timing["orphanCandidateCleanupMs"] = 0.0
                    timing["orphanCandidateCleanup"] = dict(orphan_cleanup or {})
                    cleanup_started = time.monotonic()
                    active_generation_values = {
                        str(value or "") for value in active_generations.values() if str(value or "")
                    }
                    for scope_id in changed_scope_ids:
                        generation_id = str((scope_rows.get(scope_id) or {}).get("generationId") or "")
                        if generation_id and generation_id not in active_generation_values:
                            self.delete_box_snapshot_rows_in_batches(driver, imported, "ABox", generation_id)
                    # A retry for the exact Manifest must not collide with a
                    # stale marker. The marker is outside every active scope.
                    self.delete_box_snapshot_rows_in_batches(driver, imported, "ABox", manifest_id)
                    timing["candidateCleanupMs"] = round((time.monotonic() - cleanup_started) * 1000, 1)
                    write_started = time.monotonic()
                    self.write_persistence_rows(driver, imported, node_rows, relation_rows)
                    timing["changedScopeWriteMs"] = round((time.monotonic() - write_started) * 1000, 1)
                    verification_started = time.monotonic()
                    actual_counts_by_scope = self.scoped_abox_scope_row_counts_batch([
                        scope_rows.get(scope_id) or {}
                        for scope_id in changed_scope_ids
                    ])
                    for scope_id in changed_scope_ids:
                        scope_plan_row = scope_rows.get(scope_id) or {}
                        expected = expected_rows_by_scope.get(scope_id) or {}
                        generation_id = str(scope_plan_row.get("generationId") or "")
                        actual = actual_counts_by_scope.get(scope_id) or {
                            "entityCount": 0,
                            "relationCount": 0,
                        }
                        valid = (
                            actual.get("entityCount") == int(expected.get("entityCount") or 0)
                            and actual.get("relationCount") == int(expected.get("relationCount") or 0)
                        )
                        verification[scope_id] = {
                            "status": "ok" if valid else "incomplete",
                            "generationId": generation_id,
                            "expectedEntityCount": int(expected.get("entityCount") or 0),
                            "expectedRelationCount": int(expected.get("relationCount") or 0),
                            "actualEntityCount": int(actual.get("entityCount") or 0),
                            "actualRelationCount": int(actual.get("relationCount") or 0),
                        }
                    timing["changedScopeVerificationMs"] = round((time.monotonic() - verification_started) * 1000, 1)
                    failed = [scope_id for scope_id, item in verification.items() if str(item.get("status") or "") != "ok"]
                    if failed:
                        raise RuntimeError("Scoped ABox candidate verification failed for " + ", ".join(failed))
                    marker_started = time.monotonic()
                    marker_graph = self.scoped_manifest_marker_graph(graph, scope_plan, changed_scope_ids)
                    self.write_graph(driver, imported, marker_graph, delete_boxes=[])
                    timing["manifestMarkerWriteMs"] = round((time.monotonic() - marker_started) * 1000, 1)
                    stage_started = time.monotonic()
                    pending_graph = self.scoped_manifest_pending_graph(graph, scope_plan, active_before)
                    if not pending_graph.entities:
                        raise RuntimeError("Scoped ABox candidate has no activation journal.")
                    # Do not replace ABoxControl here. The currently active
                    # Manifest remains the only live read world until the
                    # reasoning worker prepares this verified candidate.
                    self.write_graph(driver, imported, pending_graph, delete_boxes=[])
                    timing["manifestStageMs"] = round((time.monotonic() - stage_started) * 1000, 1)
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
            active_after = self.active_abox_metadata()
            timing["totalMs"] = round((time.monotonic() - save_started_at) * 1000, 1)
            activation_status = "staged" if previous_manifest_id != manifest_id else "unchanged"
            release = release_write_lease()
            return {
                "configured": True,
                "saved": True,
                "status": "ok",
                "graphStore": "typedb",
                "entityCount": len(node_rows),
                "relationCount": len(relation_rows),
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "scopePlan": scope_plan,
                "changedScopeIds": changed_scope_ids,
                "changedScopeEntityCount": len(node_rows),
                "changedScopeRelationCount": len(relation_rows),
                "orphanCandidateCleanup": orphan_cleanup,
                "writeLease": {
                    key: value
                    for key, value in dict(write_lease or {}).items()
                    if key != "propertiesJson"
                },
                "writeLeaseRelease": release,
                "aboxPersistenceVerification": {
                    "status": "ok",
                    "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
                    "scopeVerification": verification,
                    "activePointer": active_after,
                    "activation": {
                        "status": activation_status,
                        "snapshotId": manifest_id,
                        "previousSnapshotId": previous_manifest_id,
                        "atomic": True,
                        "activationRequired": activation_status == "staged",
                        "finalizationRequired": activation_status == "staged",
                    },
                    "timing": timing,
                },
            }
        except Exception as error:  # noqa: BLE001 - preserve the prior Manifest on candidate failure.
            failed_candidate_cleanup: Dict[str, object] = {
                "status": "deferred",
                "reason": "Failed scoped candidate cleanup is deferred to idle maintenance.",
            }
            release = release_write_lease()
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "changedScopeIds": changed_scope_ids,
                "scopePlan": scope_plan,
                "preservedActiveGeneration": bool(previous_manifest_id),
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "scopeVerification": verification,
                "timing": timing,
                "orphanCandidateCleanup": orphan_cleanup,
                "failedCandidateCleanup": failed_candidate_cleanup,
                "writeLease": {
                    key: value
                    for key, value in dict(write_lease or {}).items()
                    if key != "propertiesJson"
                },
                "writeLeaseRelease": release,
            }

    def scoped_manifest_metadata(self, manifest_id: str) -> Dict[str, object]:
        """Load one verified scoped Manifest without consulting the live pointer."""
        clean_manifest_id = str(manifest_id or "").strip()
        if not clean_manifest_id:
            return {}
        try:
            markers = self.worldview_manifest_marker_rows()
        except Exception:  # noqa: BLE001 - callers retain the current Manifest on lookup failure.
            return {}
        candidates = [
            item
            for item in markers
            if str(
                item.get("worldviewManifestId")
                or item.get("aboxSnapshotId")
                or item.get("snapshotId")
                or ""
            ).strip() == clean_manifest_id
        ]
        if not candidates:
            return {}
        marker = sorted(
            candidates,
            key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("id") or "")),
            reverse=True,
        )[0]
        return self.scoped_abox_metadata_from_manifest_marker(marker)

    def scoped_manifest_control_graph(
        self,
        metadata: Dict[str, object],
        previous_metadata: Dict[str, object] = None,
        pending_activation: bool = False,
    ) -> PortfolioOntology:
        """Rebuild the small pointer set for a verified historical Manifest."""
        payload = dict(metadata or {})
        manifest_id = str(payload.get("worldviewManifestId") or payload.get("aboxSnapshotId") or "").strip()
        if not manifest_id:
            return PortfolioOntology("typedb-scoped-control")
        worldview = {
            "worldviewManifestId": manifest_id,
            "aboxSnapshotId": manifest_id,
            "snapshotId": manifest_id,
            "materialFingerprint": str(payload.get("materialFingerprint") or ""),
            "projectionRunId": str(payload.get("projectionRunId") or ""),
            "asOf": str(payload.get("asOf") or utc_now()),
            "scopePlan": list(payload.get("scopePlan") or []),
            "scopeGenerationIds": dict(payload.get("scopeGenerationIds") or {}),
            "scopeFingerprints": dict(payload.get("scopeFingerprints") or {}),
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
        }
        graph = PortfolioOntology("typedb-scoped-control", worldview=worldview)
        return self.scoped_manifest_pointer_graph(
            graph,
            list(worldview["scopePlan"]),
            previous_metadata=previous_metadata,
            pending_activation=pending_activation,
        )

    def activate_scoped_abox_manifest(
        self,
        manifest_id: str,
        previous_metadata: Dict[str, object] = None,
        pending_activation: bool = False,
    ) -> Dict[str, object]:
        """Activate a complete historical Manifest and optionally retain its journal."""
        clean_manifest_id = str(manifest_id or "").strip()
        metadata = self.scoped_manifest_metadata(clean_manifest_id)
        if str(metadata.get("status") or "") != "ok":
            return {
                "configured": bool(getattr(self, "address", "")),
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_manifest_id,
                "worldviewManifestId": clean_manifest_id,
                "reason": "Scoped ABox Manifest is missing or incomplete.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], PortfolioOntology("typedb-scoped-control"))
        previous = dict(previous_metadata or {})
        if not previous:
            try:
                previous = self.active_abox_metadata()
            except Exception:
                previous = {}
        pointer_graph = self.scoped_manifest_control_graph(
            metadata,
            previous_metadata=previous,
            pending_activation=pending_activation,
        )
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    self.write_graph(driver, imported, pointer_graph, delete_boxes=["ABoxControl"])
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
            active = self.active_abox_metadata()
            if (
                str(active.get("status") or "") != "ok"
                or str(active.get("worldviewManifestId") or active.get("aboxSnapshotId") or "") != clean_manifest_id
            ):
                return {
                    "configured": True,
                    "status": "error",
                    "graphStore": "typedb",
                    "aboxSnapshotId": clean_manifest_id,
                    "worldviewManifestId": clean_manifest_id,
                    "reason": "Scoped ABox Manifest pointer verification failed after activation.",
                    "activeAbox": active,
                }
            return {
                "configured": True,
                "status": "ok",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_manifest_id,
                "worldviewManifestId": clean_manifest_id,
                "activeAbox": active,
            }
        except Exception as error:  # noqa: BLE001 - preserve the current pointer on an activation failure.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_manifest_id,
                "worldviewManifestId": clean_manifest_id,
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }

    def prepare_pending_abox_activation_for_inference(self) -> Dict[str, object]:
        """Move one fully staged Manifest into the short native-inference phase.

        This is the only place a staged candidate replaces the active pointer.
        The pending journal is retained in the same ABoxControl transaction, so
        API readers and recovery logic can reject an unaligned InferenceBox
        until finalization succeeds.
        """
        try:
            pending = self.pending_abox_activation()
        except Exception as error:  # noqa: BLE001 - a missing journal must never imply a safe switch.
            return {
                "configured": bool(getattr(self, "address", "")),
                "status": "error",
                "graphStore": "typedb",
                "reason": "Pending ABox activation lookup failed: " + str(error)[:180],
            }
        pending_status = str(pending.get("status") or "")
        if pending_status == "empty":
            return {
                "configured": True,
                "status": "skipped",
                "graphStore": "typedb",
                "reason": "No staged ABox activation exists.",
            }
        if pending_status != "pending":
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "pendingActivation": pending,
                "reason": "Pending ABox activation journal is invalid.",
            }
        candidate_id = str(pending.get("candidateAboxSnapshotId") or "").strip()
        previous_id = str(pending.get("previousAboxSnapshotId") or "").strip()
        activation_status = str(pending.get("activationStatus") or "pending-native-inference")
        try:
            active = self.active_abox_metadata()
        except Exception as error:  # noqa: BLE001 - do not activate when the live pointer cannot be verified.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "reason": "Active ABox lookup failed before activation: " + str(error)[:180],
            }
        active_id = str(active.get("worldviewManifestId") or active.get("aboxSnapshotId") or "").strip()
        if active_id == candidate_id:
            return {
                "configured": True,
                "status": "ready",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "pendingActivation": pending,
                "activeAbox": active,
            }
        if activation_status != "staged-native-inference":
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "pendingActivation": pending,
                "activeAbox": active,
                "reason": "Pending ABox is not in a staged activation phase.",
            }
        if previous_id and active_id != previous_id:
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "activeAbox": active,
                "reason": "Active ABox changed after the candidate was staged.",
            }
        activation = self.activate_scoped_abox_manifest(
            candidate_id,
            previous_metadata=active,
            pending_activation=True,
        )
        if str(activation.get("status") or "") != "ok":
            return {
                **dict(activation or {}),
                "status": "error",
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "pendingActivation": pending,
            }
        try:
            active_after = self.active_abox_metadata()
            pending_after = self.pending_abox_activation()
        except Exception as error:  # noqa: BLE001 - pointer write without journal verification is unsafe.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "reason": "ABox activation verification failed: " + str(error)[:180],
            }
        active_after_id = str(active_after.get("worldviewManifestId") or active_after.get("aboxSnapshotId") or "").strip()
        if (
            str(active_after.get("status") or "") != "ok"
            or active_after_id != candidate_id
            or str(pending_after.get("status") or "") != "pending"
            or str(pending_after.get("candidateAboxSnapshotId") or "") != candidate_id
        ):
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "activeAbox": active_after,
                "pendingActivation": pending_after,
                "reason": "ABox activation pointer or journal verification failed.",
            }
        return {
            "configured": True,
            "status": "activated",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "activeAbox": active_after,
            "pendingActivation": pending_after,
        }

    def finalize_scoped_abox_manifest(
        self,
        active_manifest_id: str,
        previous_manifest_id: str = "",
    ) -> Dict[str, object]:
        """Clear a scoped activation journal only after aligned native inference."""
        active_id = str(active_manifest_id or "").strip()
        previous_id = str(previous_manifest_id or "").strip()
        active = self.active_abox_metadata()
        if (
            str(active.get("status") or "") != "ok"
            or str(active.get("worldviewManifestId") or active.get("aboxSnapshotId") or "") != active_id
        ):
            return {
                "configured": bool(getattr(self, "address", "")),
                "status": "error",
                "graphStore": "typedb",
                "activeAboxSnapshotId": active_id,
                "previousAboxSnapshotId": previous_id,
                "reason": "Active Worldview Manifest changed before finalization.",
            }
        control = self.activate_scoped_abox_manifest(active_id)
        cleared = str(control.get("status") or "") == "ok"
        # Pointer finalization is on the realtime inference path; deleting a
        # retired immutable generation is deliberately not.  Maintenance
        # acquires the same writer lease during an idle window and performs a
        # bounded, reference-aware prune without delaying a valid judgement.
        cleanup_required = bool(cleared and previous_id and previous_id != active_id)
        cleanup = {
            "status": "deferred" if cleanup_required else "not-required" if cleared else "blocked",
            "previousAboxSnapshotId": previous_id,
            "reason": (
                "Inactive scoped ABox cleanup is deferred to an idle maintenance pass."
                if cleanup_required
                else "No prior scoped Manifest requires cleanup."
                if cleared
                else "Activation journal was not cleared."
            ),
            "legacyPredecessorPending": bool(
                cleanup_required and not previous_id.startswith("abox-manifest:")
            ),
        }
        return {
            "configured": True,
            "status": "ok" if cleared else "error",
            "graphStore": "typedb",
            "activeAboxSnapshotId": active_id,
            "previousAboxSnapshotId": previous_id,
            "clearedPendingActivation": cleared,
            "cleanupDeferred": str(cleanup.get("status") or "") == "deferred",
            "cleanup": cleanup,
            "control": control,
            "reason": "" if cleared else str(control.get("reason") or "Scoped ABox activation journal clear failed."),
        }

    def discard_scoped_abox_manifest_in_driver(
        self,
        driver,
        imported,
        manifest_id: str,
        protected_generation_ids: Iterable[str] = None,
    ) -> Dict[str, object]:
        """Delete a non-active Manifest and only generations no other Manifest needs."""
        clean_manifest_id = str(manifest_id or "").strip()
        metadata = self.scoped_manifest_metadata(clean_manifest_id)
        if str(metadata.get("status") or "") != "ok":
            return {
                "status": "skipped",
                "aboxSnapshotId": clean_manifest_id,
                "reason": "Scoped Manifest marker is not available for safe cleanup.",
                "deletedBatchCount": 0,
            }
        active = self.active_abox_metadata()
        active_id = str(active.get("worldviewManifestId") or active.get("aboxSnapshotId") or "").strip()
        if active_id == clean_manifest_id:
            return {
                "status": "protected-active",
                "aboxSnapshotId": clean_manifest_id,
                "deletedBatchCount": 0,
            }
        protected = {
            str(item or "").strip()
            for item in protected_generation_ids or []
            if str(item or "").strip()
        }
        protected.update(
            str(item or "").strip()
            for item in dict(active.get("scopeGenerationIds") or {}).values()
            if str(item or "").strip()
        )
        deleted_batches = 0
        removed_generations = []
        retained_generations = []
        for generation_id in sorted({
            str(item or "").strip()
            for item in dict(metadata.get("scopeGenerationIds") or {}).values()
            if str(item or "").strip()
        }):
            if generation_id in protected:
                retained_generations.append(generation_id)
                continue
            cleanup = self.delete_box_snapshot_rows_in_batches(driver, imported, "ABox", generation_id)
            deleted_batches += int(number_or_none(cleanup.get("deletedBatchCount")) or 0)
            removed_generations.append(generation_id)
        marker_cleanup = self.delete_box_snapshot_rows_in_batches(driver, imported, "ABox", clean_manifest_id)
        deleted_batches += int(number_or_none(marker_cleanup.get("deletedBatchCount")) or 0)
        return {
            "status": "ok",
            "aboxSnapshotId": clean_manifest_id,
            "worldviewManifestId": clean_manifest_id,
            "removedScopeGenerationIds": removed_generations,
            "retainedSharedScopeGenerationIds": retained_generations,
            "deletedBatchCount": deleted_batches,
        }

    def discard_scoped_abox_manifest(self, manifest_id: str) -> Dict[str, object]:
        clean_manifest_id = str(manifest_id or "").strip()
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], PortfolioOntology("typedb-scoped-cleanup"))
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    return self.discard_scoped_abox_manifest_in_driver(
                        driver,
                        imported,
                        clean_manifest_id,
                    )
                finally:
                    self.close_driver(driver)

            result = self.with_typedb_retries(operation)
            return {"configured": True, "graphStore": "typedb", **dict(result or {})}
        except Exception as error:  # noqa: BLE001 - the failed Manifest remains diagnosable.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_manifest_id,
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }

    def prune_inactive_scoped_abox_manifests_in_driver(
        self,
        driver,
        imported,
        active_manifest_id: str = "",
        keep_inactive_count: int = None,
        max_manifests: int = None,
    ) -> Dict[str, object]:
        """Prune immutable Manifests without deleting generations still referenced.

        A scope generation is a shared immutable object: an unchanged macro or
        reference scope can be referenced by many historical Manifests.  The
        protected set therefore includes the active Manifest and all retained
        rollback Manifests before any old physical rows are removed.
        """
        active = self.active_abox_metadata()
        active_id = str(
            active_manifest_id
            or active.get("worldviewManifestId")
            or active.get("aboxSnapshotId")
            or ""
        ).strip()
        pending = self.pending_abox_activation()
        if str(pending.get("status") or "") == "pending":
            return {
                "status": "skipped",
                "reason": "Scoped ABox activation is pending native inference.",
                "activeAboxSnapshotId": active_id,
                "pendingAboxSnapshotId": str(pending.get("candidateAboxSnapshotId") or ""),
                "deletedBatchCount": 0,
            }
        keep_count = (
            self.abox_inactive_generation_keep_count()
            if keep_inactive_count is None
            else max(0, min(5, int(keep_inactive_count or 0)))
        )
        max_count = (
            self.abox_inactive_generation_max_prune_per_save()
            if max_manifests is None
            else max(0, min(10, int(max_manifests or 0)))
        )
        manifests: Dict[str, Dict[str, object]] = {}
        for marker in self.worldview_manifest_marker_rows():
            metadata = self.scoped_abox_metadata_from_manifest_marker(marker)
            manifest_id = str(metadata.get("worldviewManifestId") or metadata.get("aboxSnapshotId") or "").strip()
            if not manifest_id or manifest_id == active_id:
                continue
            previous = manifests.get(manifest_id)
            if previous is None or (
                str(marker.get("updatedAt") or ""), str(marker.get("id") or "")
            ) > (
                str(previous.get("updatedAt") or ""), str(previous.get("id") or "")
            ):
                manifests[manifest_id] = {**metadata, "updatedAt": str(marker.get("updatedAt") or "")}
        ordered = sorted(
            manifests.values(),
            key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("worldviewManifestId") or "")),
            reverse=True,
        )
        retained = ordered[:keep_count]
        removable = list(reversed(ordered[keep_count:]))[:max_count]
        protected_generation_ids = {
            str(item or "").strip()
            for item in dict(active.get("scopeGenerationIds") or {}).values()
            if str(item or "").strip()
        }
        for metadata in retained:
            protected_generation_ids.update(
                str(item or "").strip()
                for item in dict(metadata.get("scopeGenerationIds") or {}).values()
                if str(item or "").strip()
            )
        removed = []
        deleted_batches = 0
        cleanup_rows = []
        for metadata in removable:
            manifest_id = str(metadata.get("worldviewManifestId") or metadata.get("aboxSnapshotId") or "").strip()
            if not manifest_id:
                continue
            cleanup = self.discard_scoped_abox_manifest_in_driver(
                driver,
                imported,
                manifest_id,
                protected_generation_ids=protected_generation_ids,
            )
            cleanup_rows.append(cleanup)
            if str(cleanup.get("status") or "") == "ok":
                removed.append(manifest_id)
                deleted_batches += int(number_or_none(cleanup.get("deletedBatchCount")) or 0)
        return {
            "status": "ok",
            "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
            "activeAboxSnapshotId": active_id,
            "keepInactiveManifestCount": keep_count,
            "maxManifestsPerRun": max_count,
            "completedInactiveManifestCount": len(ordered),
            "retainedInactiveManifestIds": [
                str(item.get("worldviewManifestId") or item.get("aboxSnapshotId") or "")
                for item in retained
            ],
            "removedManifestIds": removed,
            "remainingInactiveManifestCount": max(0, len(ordered) - len(removed)),
            "deletedBatchCount": deleted_batches,
            "cleanup": cleanup_rows,
        }

    def prune_inactive_scoped_abox_manifests(self) -> Dict[str, object]:
        """Run one bounded, reference-aware scoped ABox maintenance pass."""
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "configured": bool(getattr(self, "address", "")),
                "status": "driver-missing",
                "graphStore": "typedb",
                "reason": str(imported[1])[:180],
            }
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    active = self.active_abox_metadata()
                    return self.prune_inactive_scoped_abox_manifests_in_driver(
                        driver,
                        imported,
                        active_manifest_id=str(
                            active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
                        ),
                    )
                finally:
                    self.close_driver(driver)

            result = self.with_typedb_retries(operation)
            return {"configured": True, "graphStore": "typedb", **dict(result or {})}
        except Exception as error:  # noqa: BLE001 - valid inference remains usable if maintenance is delayed.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }

    def run_deferred_maintenance(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        """Prune inactive graph generations only while the reasoning queue is idle.

        This is operational retention, never an investment-rule step. It uses
        the same durable writer lease as ABox activation, so maintenance
        cannot delete a generation that a live native inference still needs.
        """
        if not bool(getattr(self, "address", "")):
            return {
                "configured": False,
                "status": "disabled",
                "graphStore": "typedb",
                "reason": "TypeDB ontology storage is not configured.",
            }
        options = dict(payload or {})
        started_at = time.perf_counter()
        lease = self.acquire_scoped_abox_write_lease("ontology-deferred-maintenance")
        if not lease.get("acquired"):
            return {
                "configured": True,
                "status": "deferred-write-lease",
                "graphStore": "typedb",
                "reason": "A live ABox activation or native inference owns the graph writer lease.",
                "durationMs": int((time.perf_counter() - started_at) * 1000),
            }
        try:
            orphan_result = self.prune_orphan_scoped_abox_candidates()
            abox_result = self.prune_inactive_scoped_abox_manifests()
            legacy_result: Dict[str, object] = {
                "status": "not-required",
                "deletedGenerationIds": [],
            }
            # Scoped manifests reuse several immutable scope generations, so
            # generic ABox pruning must not scan every ABox snapshot. Legacy
            # complete-world snapshots have their own stable prefixes and can
            # be safely reclaimed once a scoped Manifest is active.
            active_abox = self.active_abox_metadata()
            if str(active_abox.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION:
                pending = self.pending_abox_activation()
                active_scope_ids = {
                    str(value or "").strip()
                    for value in dict(active_abox.get("scopeGenerationIds") or {}).values()
                    if str(value or "").strip()
                }
                legacy_candidates = []
                if str(pending.get("status") or "") != "pending":
                    for snapshot_id in self.abox_candidate_snapshot_ids():
                        clean_snapshot_id = str(snapshot_id or "").strip()
                        if (
                            clean_snapshot_id
                            and clean_snapshot_id not in active_scope_ids
                            and clean_snapshot_id.startswith(("abox-material:", "abox-snapshot:"))
                        ):
                            legacy_candidates.append(clean_snapshot_id)
                legacy_slices = [self.discard_abox_generation(snapshot_id) for snapshot_id in legacy_candidates[:2]]
                legacy_result = {
                    "status": "ok" if not legacy_slices or all(str(item.get("status") or "") == "ok" for item in legacy_slices) else "partial",
                    "candidateGenerationIds": legacy_candidates,
                    "deletedGenerationIds": [
                        str(item.get("aboxSnapshotId") or "")
                        for item in legacy_slices
                        if str(item.get("status") or "") == "ok"
                    ],
                    "cleanup": legacy_slices,
                }
            inference_result: Dict[str, object] = {
                "status": "not-required",
                "reason": "No active InferenceBox generation was found.",
            }
            reader = getattr(self, "read_inference_generation_records", None)
            pruner = getattr(self, "prune_inferencebox_generations", None)
            if callable(reader) and callable(pruner):
                records = reader(published_only=True)
                active_generation_id = str((records[0] if records else {}).get("generationId") or "").strip()
                if active_generation_id:
                    inference_result = pruner(
                        active_generation_id,
                        keep_count=max(1, int(number_or_none(options.get("inferenceKeepCount")) or getattr(self, "inference_generation_keep_count", 1))),
                    )
            statuses = {
                str(orphan_result.get("status") or ""),
                str(abox_result.get("status") or ""),
                str(legacy_result.get("status") or ""),
                str(inference_result.get("status") or ""),
            }
            maintenance_partial = bool(statuses.intersection({"error", "partial", "deferred-write-lease"}))
            return {
                "configured": True,
                "status": "partial" if maintenance_partial else "ok",
                "graphStore": "typedb",
                "orphanScopedAbox": orphan_result,
                "abox": abox_result,
                "legacyAbox": legacy_result,
                "inference": inference_result,
                "durationMs": int((time.perf_counter() - started_at) * 1000),
            }
        except Exception as error:  # noqa: BLE001 - a later idle window can retry retention.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "durationMs": int((time.perf_counter() - started_at) * 1000),
            }
        finally:
            try:
                self.release_scoped_abox_write_lease(lease)
            except Exception:
                pass

    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
        }

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        graph = ontology_seed_graph(language_registry=investment_language_registry(runtime_settings()))
        result = self.save_graph(graph)
        result.update({
            "seeded": False,
            "engineVersion": GRAPH_REASONER_VERSION,
            "ruleCount": len(default_graph_inference_rules()),
        })
        return result

    def rulebox_snapshot(self) -> Dict[str, object]:
        rules = rulebox_rules_to_payload(default_graph_inference_rules())
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "source": "typedb-defaults",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "engineVersion": GRAPH_REASONER_VERSION,
            "rules": rules,
            "ruleCount": len(rules),
            "conditionCount": sum(len(item.get("conditions") or []) for item in rules),
            "derivationCount": sum(len(item.get("derivations") or []) for item in rules),
            "relationTypes": sorted({
                str(derivation.get("relation_type") or derivation.get("relationType") or "")
                for rule in rules
                for derivation in (rule.get("derivations") or [])
                if isinstance(derivation, dict)
            }),
            "defaultsFallbackUsed": True,
            "versions": [],
            "versionCount": 0,
            "changeCandidates": rulebox_governance_candidates(rules, []),
            "nativeReasoningProfile": typedb_native_reasoning_profile(rules),
        }

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        snapshot = self.rulebox_snapshot()
        snapshot.update({"saved": False, "status": "disabled"})
        return snapshot

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "statementCount": 0,
        }

    def validate_rulebox_materialization(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": 0,
            "baselineInferenceBox": self.inferencebox_snapshot(),
            "diff": materialization_preview_diff_payload({}, 0, 0, False),
        }

    def inferencebox_snapshot(
        self,
        symbols: List[str] = None,
        limit: int = 80,
        reset_metrics: bool = True,
    ) -> Dict[str, object]:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "reasoningMode": "disabled",
            "reason": "TypeDB ontology storage is not configured.",
            "symbols": list(symbols or []),
            "entities": [],
            "relations": [],
            "traces": [],
            "entityCount": 0,
            "relationCount": 0,
            "traceCount": 0,
            "nativeEntityCount": 0,
            "nativeRelationCount": 0,
            "nativeTraceCount": 0,
            "nativeTypeDbReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
        }

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "candidateCount": len(list(candidates or [])),
            "savedCount": 0,
        }


class NullTypeDBOntologyGraphRepository(ScopedABoxManifestMixin):
    """Disabled TypeDB adapter preserving the graph-store interface."""


class TypeDBOntologyGraphRepository(GraphStoreOntologyRowMapperMixin, ScopedABoxManifestMixin):
    store_key = "typedb"
    store_label = "TypeDB"

    def __init__(
        self,
        address: str,
        user: str = "admin",
        password: str = "password",
        database: str = "orbit_alpha_ontology",
        tls_enabled: bool = False,
        timeout_seconds: int = 20,
        retry_count: int = 2,
        inference_generation_keep_count: int = 2,
        query_timeout_seconds: float = None,
        schema_operation_timeout_seconds: float = None,
        write_operation_timeout_seconds: float = None,
        condition_detail_queries_enabled: bool = False,
        query_metrics_enabled: bool = True,
        rulebox_snapshot_cache_seconds: float = 60.0,
        native_rule_execution_enabled: bool = True,
        native_rule_query_timeout_seconds: float = DEFAULT_TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS,
        native_rule_execution_budget_seconds: float = DEFAULT_TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS,
        inference_write_lease_enabled: bool = False,
    ):
        self.address = str(address or "").strip()
        self.user = str(user or "admin").strip() or "admin"
        self.password = str(password or "password")
        self.database = str(database or "orbit_alpha_ontology").strip() or "orbit_alpha_ontology"
        self.tls_enabled = bool(tls_enabled)
        self.timeout_seconds = max(2, int(timeout_seconds or 20))
        self.retry_count = max(0, int(retry_count or 0))
        self.inference_generation_keep_count = max(1, int(inference_generation_keep_count or 2))
        self._query_timeout_seconds = max(
            1.0,
            float(query_timeout_seconds if query_timeout_seconds is not None else float(self.timeout_seconds or 20)),
        )
        self._schema_operation_timeout_seconds = max(
            1.0,
            float(
                schema_operation_timeout_seconds
                if schema_operation_timeout_seconds is not None
                else float(self.timeout_seconds or 20)
            ),
        )
        self._write_operation_timeout_seconds = max(
            1.0,
            float(
                write_operation_timeout_seconds
                if write_operation_timeout_seconds is not None
                else float(self.timeout_seconds or 20)
            ),
        )
        self._condition_detail_queries_enabled = bool(condition_detail_queries_enabled)
        self._query_metrics_enabled = bool(query_metrics_enabled)
        self._rulebox_snapshot_cache_seconds = max(1.0, float(rulebox_snapshot_cache_seconds or 60.0))
        self._native_rule_execution_enabled = bool(native_rule_execution_enabled)
        self._native_rule_query_timeout_seconds = max(
            0.5,
            float(native_rule_query_timeout_seconds or DEFAULT_TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS),
        )
        self._native_rule_execution_budget_seconds = max(
            1.0,
            float(native_rule_execution_budget_seconds or DEFAULT_TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS),
        )
        # The production composition root enables this durable lease. Bare
        # adapters retain the old unlocked behavior for isolated migrations
        # and deterministic unit tests that do not open a real TypeDB driver.
        self._inference_write_lease_enabled = bool(inference_write_lease_enabled)
        self._last_graph = None
        self._last_rules: List[GraphInferenceRule] = []
        self._base_schema_ready_fingerprint = ""
        self._base_schema_type_names: set = set()
        self._schema_function_sync_cache_key = ""
        self._schema_function_sync_cache_result: Dict[str, object] = {}
        self._rulebox_snapshot_cache_at = 0.0
        self._rulebox_snapshot_cache_result: Dict[str, object] = {}
        self._query_metrics: List[Dict[str, object]] = []

    def with_typedb_retries(self, operation):
        attempts = max(1, self.retry_count + 1)
        last_error = None
        for index in range(attempts):
            try:
                return operation()
            except Exception as error:  # noqa: BLE001 - TypeDB connectivity can be transient.
                last_error = error
                if index >= attempts - 1:
                    break
                time.sleep(min(2.0, 0.25 * (index + 1)))
        raise last_error

    def runtime_timeout_seconds(self, key: str, default_seconds: float) -> float:
        try:
            configured = number_or_none(runtime_settings().get(key))
        except Exception:
            configured = None
        if configured is None:
            configured = default_seconds
        return max(1.0, float(configured or default_seconds))

    def query_timeout_seconds(self) -> float:
        return self._query_timeout_seconds

    def schema_operation_timeout_seconds(self) -> float:
        return self._schema_operation_timeout_seconds

    def write_operation_timeout_seconds(self) -> float:
        return self._write_operation_timeout_seconds

    def condition_detail_queries_enabled(self) -> bool:
        return self._condition_detail_queries_enabled

    def query_metrics_enabled(self) -> bool:
        return self._query_metrics_enabled

    def reset_query_metrics(self) -> None:
        self._query_metrics = []

    def record_query_metric(self, label: str, query: str, row_count: int, duration_ms: float, status: str = "ok") -> None:
        if not self.query_metrics_enabled():
            return
        normalized_query = re.sub(r"\s+", " ", str(query or "")).strip()
        self._query_metrics.append({
            "label": str(label or "typedb.read")[:80],
            "status": str(status or "ok"),
            "rowCount": int(row_count or 0),
            "durationMs": round(float(duration_ms or 0.0), 2),
            "queryHash": hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()[:12] if normalized_query else "",
            "queryPreview": normalized_query[:180],
        })
        if len(self._query_metrics) > 120:
            self._query_metrics = self._query_metrics[-120:]

    def query_metrics_snapshot(self) -> Dict[str, object]:
        rows = list(self._query_metrics or [])
        total_ms = sum(float(item.get("durationMs") or 0) for item in rows)
        slow = sorted(rows, key=lambda item: float(item.get("durationMs") or 0), reverse=True)[:8]
        return {
            "enabled": self.query_metrics_enabled(),
            "queryCount": len(rows),
            "totalDurationMs": round(total_ms, 2),
            "slowQueries": slow,
        }

    def rulebox_snapshot_cache_seconds(self) -> float:
        return self._rulebox_snapshot_cache_seconds

    def native_rule_execution_enabled(self) -> bool:
        return self._native_rule_execution_enabled

    def native_rule_query_timeout_seconds(self) -> float:
        return self._native_rule_query_timeout_seconds

    def native_rule_execution_budget_seconds(self) -> float:
        return self._native_rule_execution_budget_seconds

    def clear_rulebox_snapshot_cache(self) -> None:
        self._rulebox_snapshot_cache_at = 0.0
        self._rulebox_snapshot_cache_result = {}

    def active_tbox_metadata(self) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().active_tbox_metadata()
        try:
            entity_rows = self.read_entity_rows(["TBox"])
            relation_rows = self.read_relation_rows(["TBox"])
        except Exception as error:  # noqa: BLE001 - metadata must be safe for UI/bootstrap.
            metadata = active_tbox_metadata_unavailable("error", str(error)[:180], "typedb")
            metadata.update({"graphStore": "typedb", "storeSource": "typedb-typeql"})
            return metadata
        version = ""
        fingerprint = ""
        updated_at = ""
        for row in entity_rows:
            props = json_object(row.get("propertiesJson"))
            version = version or str(row.get("version") or props.get("version") or props.get("tboxVersion") or "")
            fingerprint = fingerprint or str(row.get("fingerprint") or props.get("fingerprint") or props.get("tboxFingerprint") or "")
            updated_at = max(updated_at, str(row.get("updatedAt") or props.get("updatedAt") or ""))
        metadata = active_tbox_metadata_from_rows(
            {
                "entities": [{
                    "entityCount": len(entity_rows),
                    "version": version,
                    "fingerprint": fingerprint,
                    "updatedAt": updated_at,
                }],
                "relations": [{"relationCount": len(relation_rows)}],
            },
            "typedb-typeql",
        )
        metadata.update({"graphStore": "typedb", "source": "typedb-typeql", "storeSource": "typedb-typeql"})
        return metadata

    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().save_graph(graph)
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], graph)
        boxes = node_boxes(graph)
        if "ABox" in boxes and self.is_scoped_abox_graph(graph):
            return self.save_scoped_abox_graph(graph, boxes)
        abox_projection_verification: Dict[str, object] = {}
        abox_persistence_timing: Dict[str, object] = {}
        try:
            def operation():
                nonlocal abox_projection_verification, abox_persistence_timing
                with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB graph save"):
                    driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    expected_entity_count = 0
                    expected_relation_count = 0
                    if "ABox" in boxes:
                        abox_started_at = time.monotonic()
                        abox_persistence_timing = {"startedAt": utc_now()}
                        node_rows, relation_rows = self.graph_persistence_rows(graph)
                        expected_entity_count = len(node_rows)
                        expected_relation_count = len(relation_rows)
                        candidate_graph = self.abox_candidate_graph(graph)
                        snapshot_id = self.abox_snapshot_id_from_graph(candidate_graph)
                        abox_persistence_timing["candidateAboxSnapshotId"] = snapshot_id
                        if not snapshot_id:
                            abox_projection_verification = {
                                "status": "skipped",
                                "reason": "ABox material identity is unavailable.",
                            }
                        else:
                            active_before = self.active_abox_metadata()
                            active_snapshot_id = str(active_before.get("aboxSnapshotId") or "").strip()
                            if active_snapshot_id != snapshot_id:
                                cleanup_started_at = time.monotonic()
                                try:
                                    incremental_cleanup = self.drain_inactive_abox_generations_incrementally(
                                        driver,
                                        imported,
                                        active_snapshot_id,
                                        excluded_snapshot_ids=[snapshot_id],
                                    )
                                except Exception as error:  # noqa: BLE001 - maintenance cannot block a new live generation.
                                    incremental_cleanup = {
                                        "status": "deferred",
                                        "reason": str(error)[:180],
                                        "activeAboxSnapshotId": active_snapshot_id,
                                    }
                                abox_persistence_timing["incrementalCleanupMs"] = round(
                                    (time.monotonic() - cleanup_started_at) * 1000,
                                    1,
                                )
                                abox_persistence_timing["incrementalCleanup"] = incremental_cleanup
                                # A candidate shares the physical ABox box with the
                                # active generation, but storage IDs include the
                                # snapshot. Clear only an interrupted retry of this
                                # exact candidate; never touch the live generation.
                                clear_started_at = time.monotonic()
                                self.delete_box_snapshot_rows_in_batches(
                                    driver,
                                    imported,
                                    "ABox",
                                    snapshot_id,
                                )
                                abox_persistence_timing["candidateRetryClearMs"] = round(
                                    (time.monotonic() - clear_started_at) * 1000,
                                    1,
                                )
                                candidate_write_started_at = time.monotonic()
                                self.write_graph(driver, imported, candidate_graph, delete_boxes=[])
                                abox_persistence_timing["candidateWriteMs"] = round(
                                    (time.monotonic() - candidate_write_started_at) * 1000,
                                    1,
                                )
                                marker_graph = self.abox_projection_marker_graph(
                                    candidate_graph,
                                    expected_entity_count,
                                    expected_relation_count,
                                )
                                if not marker_graph.entities:
                                    raise RuntimeError("ABox completion marker is unavailable.")
                                marker_write_started_at = time.monotonic()
                                self.write_graph(driver, imported, marker_graph, delete_boxes=[])
                                abox_persistence_timing["markerWriteMs"] = round(
                                    (time.monotonic() - marker_write_started_at) * 1000,
                                    1,
                                )
                            verification_started_at = time.monotonic()
                            candidate_verification = self.verify_abox_projection(
                                candidate_graph,
                                expected_entity_count,
                                expected_relation_count,
                            )
                            abox_persistence_timing["candidateVerificationMs"] = round(
                                (time.monotonic() - verification_started_at) * 1000,
                                1,
                            )
                            if candidate_verification.get("status") != "ok":
                                raise RuntimeError(
                                    "ABox candidate verification failed: "
                                    + json.dumps(candidate_verification, ensure_ascii=False, sort_keys=True)
                                )
                            if active_snapshot_id != snapshot_id:
                                pointer_graph = self.abox_active_pointer_graph(
                                    candidate_graph,
                                    previous_snapshot_id=active_snapshot_id,
                                )
                                pointer_write_started_at = time.monotonic()
                                self.write_graph(
                                    driver,
                                    imported,
                                    pointer_graph,
                                    delete_boxes=["ABoxControl"],
                                )
                                abox_persistence_timing["pointerWriteMs"] = round(
                                    (time.monotonic() - pointer_write_started_at) * 1000,
                                    1,
                                )
                                # Keep the prior active generation until the
                                # new ABox has produced an aligned native
                                # InferenceBox. The projection recorder either
                                # finalizes this retention after success or
                                # restores this pointer after a rule failure.
                            abox_projection_verification = {
                                **self.verify_abox_projection(
                                    candidate_graph,
                                    expected_entity_count,
                                    expected_relation_count,
                                ),
                                "activePointer": self.active_abox_metadata(),
                                "activation": {
                                    "status": "unchanged" if active_snapshot_id == snapshot_id else "activated",
                                    "snapshotId": snapshot_id,
                                    "previousSnapshotId": active_snapshot_id,
                                    "atomic": True,
                                    "finalizationRequired": bool(
                                        active_snapshot_id and active_snapshot_id != snapshot_id
                                    ),
                                },
                            }
                            abox_persistence_timing["totalMs"] = round(
                                (time.monotonic() - abox_started_at) * 1000,
                                1,
                            )
                            abox_projection_verification["timing"] = dict(abox_persistence_timing)
                            if abox_projection_verification.get("status") != "ok":
                                raise RuntimeError(
                                    "ABox activation verification failed: "
                                    + json.dumps(abox_projection_verification, ensure_ascii=False, sort_keys=True)
                                )
                    non_abox_boxes = [box for box in boxes if box != "ABox"]
                    if non_abox_boxes:
                        self.write_graph(
                            driver,
                            imported,
                            self.graph_for_boxes(graph, non_abox_boxes),
                            delete_boxes=non_abox_boxes,
                        )
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
        except Exception as error:  # noqa: BLE001 - graph-store persistence must not block monitoring.
            # Candidate writes never replace the active pointer until their
            # own marker and row counts verify. Preserve both the active ABox
            # and a failed candidate for diagnosis; a retry clears only that
            # candidate snapshot before writing it again.
            cleanup = {
                "status": "preserved-active-generation",
                "activeAboxSnapshotId": str(self.active_abox_metadata().get("aboxSnapshotId") or ""),
            } if "ABox" in boxes else {}
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "reason": str(error)[:240],
                "partialWriteCleanup": cleanup,
                "entityCount": len(graph.entities),
                "relationCount": len(graph.relations),
                "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
                "aboxPersistenceVerification": abox_projection_verification,
                "aboxPersistenceTiming": abox_persistence_timing,
            }
        self._last_graph = copy.deepcopy(graph)
        box_entity_counts = graph_box_entity_counts(graph)
        box_relation_counts = graph_box_relation_counts(graph)
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "schemaPrepared": True,
            "address": self.address,
            "database": self.database,
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "tboxEntityCount": box_entity_counts.get("TBox", 0),
            "aboxEntityCount": box_entity_counts.get("ABox", 0),
            "ruleBoxEntityCount": box_entity_counts.get("RuleBox", 0),
            "languageGovernanceEntityCount": box_entity_counts.get("LanguageGovernance", 0),
            "inferenceBoxEntityCount": box_entity_counts.get("InferenceBox", 0),
            "tboxRelationCount": box_relation_counts.get("TBox", 0),
            "aboxRelationCount": box_relation_counts.get("ABox", 0),
            "ruleBoxRelationCount": box_relation_counts.get("RuleBox", 0),
            "languageGovernanceRelationCount": box_relation_counts.get("LanguageGovernance", 0),
            "inferenceBoxRelationCount": box_relation_counts.get("InferenceBox", 0),
            "evidenceCount": len(graph.evidence),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
            "aboxPersistenceVerification": abox_projection_verification,
            "aboxPersistenceTiming": abox_persistence_timing,
        }

    def driver_missing_result(self, error: Exception, graph: PortfolioOntology) -> Dict[str, object]:
        return {
            "configured": True,
            "saved": False,
            "status": "driver-missing",
            "graphStore": "typedb",
            "reason": "typedb-driver Python package is not installed: " + str(error)[:160],
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
        }

    def driver_imports(self) -> Tuple[object, object]:
        try:
            from typedb.driver import Credentials, DriverOptions, DriverTlsConfig, TransactionType, TypeDB

            return (TypeDB, Credentials, DriverOptions, DriverTlsConfig, TransactionType), None
        except Exception as error:  # noqa: BLE001 - optional dependency.
            return None, error

    def open_driver(self, imported):
        TypeDB, Credentials, DriverOptions, DriverTlsConfig, _TransactionType = imported[0]
        tls_config = DriverTlsConfig.enabled() if self.tls_enabled else DriverTlsConfig.disabled()
        return TypeDB.driver(
            self.address,
            Credentials(self.user, self.password),
            DriverOptions(
                tls_config,
                primary_failover_retries=max(0, min(2, self.retry_count)),
                # A write transaction can legitimately outlive an individual read
                # deadline while replacing a large ABox. Do not let the driver's
                # channel deadline invalidate that transaction before its explicit
                # write-operation timeout is reached.
                request_timeout_millis=max(1000, int(self.driver_request_timeout_seconds() * 1000)),
            ),
        )

    def driver_request_timeout_seconds(self) -> float:
        return max(
            float(self.timeout_seconds or 0),
            float(self._query_timeout_seconds or 0),
            float(self._schema_operation_timeout_seconds or 0),
            float(self._write_operation_timeout_seconds or 0),
        )

    def write_transaction_options(self):
        try:
            from typedb.driver import TransactionOptions
        except Exception:  # noqa: BLE001 - retain compatibility with older optional drivers.
            return None
        return TransactionOptions(
            transaction_timeout_millis=max(1000, int(self.write_operation_timeout_seconds() * 1000)),
        )

    def read_transaction_options(self, timeout_seconds: float = None):
        """Bound a TypeDB read transaction, not only the individual query call.

        A driver-side transaction deadline is required because a schema function
        can spend time planning before ``query(...).resolve()`` becomes
        interruptible in Python.  This keeps one slow rule from blocking the
        realtime reasoning worker indefinitely.
        """
        try:
            from typedb.driver import TransactionOptions
        except Exception:  # noqa: BLE001 - retain compatibility with older optional drivers.
            return None
        timeout = self.query_timeout_seconds() if timeout_seconds is None else max(0.5, float(timeout_seconds))
        return TransactionOptions(
            transaction_timeout_millis=max(1000, int(timeout * 1000)),
        )

    def close_driver(self, driver) -> None:
        close = getattr(driver, "close", None)
        if callable(close):
            close()

    def ensure_database(self, driver) -> None:
        databases = getattr(driver, "databases", None)
        if databases is None:
            return
        try:
            contains = getattr(databases, "contains", None)
            if callable(contains) and contains(self.database):
                return
        except Exception:
            pass
        try:
            databases.create(self.database)
        except Exception as error:
            if "already" not in str(error).lower() and "exist" not in str(error).lower():
                raise

    def base_schema_type_names(self) -> set:
        if self._base_schema_type_names:
            return set(self._base_schema_type_names)
        names = set(re.findall(
            r"^\s*(?:attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
            self.schema_query(),
            flags=re.MULTILINE,
        ))
        self._base_schema_type_names = names
        return set(names)

    def typedb_schema_type_names(self, driver) -> set:
        schema_text = self.typedb_schema_text(driver)
        return set(re.findall(
            r"^\s*(?:attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
            schema_text,
            flags=re.MULTILINE,
        ))

    def typedb_schema_text(self, driver) -> str:
        databases = getattr(driver, "databases", None)
        get_database = getattr(databases, "get", None) if databases is not None else None
        if not callable(get_database):
            raise RuntimeError("TypeDB database schema listing is unavailable.")
        database = get_database(self.database)
        schema_reader = getattr(database, "type_schema", None)
        if not callable(schema_reader):
            schema_reader = getattr(database, "schema", None)
        if not callable(schema_reader):
            raise RuntimeError("TypeDB database schema reader is unavailable.")
        return str(schema_reader() or "")

    @staticmethod
    def ontology_storage_identity_migration_required(schema_text: str) -> bool:
        text = str(schema_text or "")
        if "ontology-node" not in text or "ontology-assertion" not in text:
            return False
        return "ontology-storage-id" not in text or "owns ontology-id @key" in text

    def migrate_ontology_storage_identity(self, driver, imported, schema_text: str) -> None:
        """Separate graph-storage identity from the canonical ontology identifier.

        ABox staging intentionally contains the same real-world facts as the
        active ABox. ``ontology-id`` is therefore a domain identifier, not a
        globally unique database key. Older databases made it a TypeDB key,
        preventing a verified staging generation from coexisting with the
        active generation during an atomic promotion.
        """
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        queries = []
        if "owns ontology-id @key" in str(schema_text or ""):
            queries.append(
                "undefine @key from ontology-node owns ontology-id; "
                "@key from ontology-assertion owns ontology-id;"
            )
        queries.append(
            "define attribute ontology-storage-id, value string; "
            "ontology-node owns ontology-storage-id @unique; "
            "ontology-assertion owns ontology-storage-id @unique;"
        )
        with typedb_operation_timeout(self.schema_operation_timeout_seconds(), "TypeDB storage identity schema migration"):
            with driver.transaction(self.database, TransactionType.SCHEMA) as tx:
                for query in queries:
                    tx.query(query).resolve()
                tx.commit()

    @staticmethod
    def ontology_scope_schema_migration_required(schema_text: str) -> bool:
        text = str(schema_text or "")
        if "ontology-node" not in text or "ontology-assertion" not in text:
            return False
        return any(attribute not in text for attribute in [
            "ontology-scope-id",
            "ontology-scope-type",
            "ontology-manifest-id",
        ])

    def migrate_ontology_scope_schema(self, driver, imported) -> None:
        """Add non-destructive attributes required by scoped ABox manifests."""
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        query = (
            "define "
            "attribute ontology-scope-id, value string; "
            "attribute ontology-scope-type, value string; "
            "attribute ontology-manifest-id, value string; "
            "ontology-node owns ontology-scope-id, owns ontology-scope-type, owns ontology-manifest-id; "
            "ontology-assertion owns ontology-scope-id, owns ontology-scope-type, owns ontology-manifest-id;"
        )
        with typedb_operation_timeout(self.schema_operation_timeout_seconds(), "TypeDB scoped ABox schema migration"):
            with driver.transaction(self.database, TransactionType.SCHEMA) as tx:
                tx.query(query).resolve()
                tx.commit()

    def ensure_schema(self, driver, imported) -> None:
        schema = self.schema_query()
        schema_fingerprint = hashlib.sha256(schema.encode("utf-8")).hexdigest()
        if self._base_schema_ready_fingerprint == schema_fingerprint:
            return
        try:
            schema_text = self.typedb_schema_text(driver)
            if self.ontology_storage_identity_migration_required(schema_text):
                self.migrate_ontology_storage_identity(driver, imported, schema_text)
                schema_text = self.typedb_schema_text(driver)
            if self.ontology_scope_schema_migration_required(schema_text):
                self.migrate_ontology_scope_schema(driver, imported)
                schema_text = self.typedb_schema_text(driver)
            schema_type_names = set(re.findall(
                r"^\s*(?:attribute|entity|relation)\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
                schema_text,
                flags=re.MULTILINE,
            ))
            if self.base_schema_type_names().issubset(schema_type_names):
                self._base_schema_ready_fingerprint = schema_fingerprint
                return
        except Exception:
            # On a new database or an older driver without schema inspection,
            # fall through to the idempotent schema definition below.
            pass
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        with typedb_operation_timeout(self.schema_operation_timeout_seconds(), "TypeDB base schema sync"):
            with driver.transaction(self.database, TransactionType.SCHEMA) as tx:
                tx.query(schema).resolve()
                tx.commit()
        self._base_schema_ready_fingerprint = schema_fingerprint

    def read_rows(
        self,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]:
        if not self.address:
            return []
        imported = self.driver_imports()
        if imported[0] is None:
            raise RuntimeError("typedb-driver Python package is not installed: " + str(imported[1])[:160])
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        def operation():
            driver = self.open_driver(imported)
            try:
                self.ensure_database(driver)
                with driver.transaction(
                    self.database,
                    TransactionType.READ,
                    self.read_transaction_options(timeout_seconds),
                ) as tx:
                    return self.read_rows_in_transaction(
                        tx,
                        query,
                        columns,
                        label=label,
                        timeout_seconds=timeout_seconds,
                    )
            finally:
                self.close_driver(driver)
        return self.with_typedb_retries(operation)

    def read_rows_in_transaction(
        self,
        tx,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]:
        started_at = time.perf_counter()
        rows: List[Dict[str, object]] = []
        status = "ok"
        try:
            query_timeout = (
                self.query_timeout_seconds()
                if timeout_seconds is None
                else max(0.5, float(timeout_seconds))
            )
            with typedb_operation_timeout(query_timeout, "TypeDB read query"):
                resolved = tx.query(query).resolve()
                for item in resolved:
                    rows.append({name: typedb_row_value(item, name) for name in columns})
                return rows
        except Exception:
            status = "error"
            raise
        finally:
            duration_ms = (time.perf_counter() - started_at) * 1000
            self.record_query_metric(label, query, len(rows), duration_ms, status=status)

    def has_box_rows(self, box: str) -> bool:
        clean_box = str(box or "").strip()
        if clean_box == "ABox":
            query = (
                "match " + self.active_abox_members_clause([("$n", "boxProbe")]) + " "
                + "$n isa ontology-node; limit 1;"
            )
        else:
            query = (
                "match $n isa ontology-node, has ontology-box "
                + typedb_string(clean_box)
                + "; limit 1;"
            )
        return bool(self.read_rows(query, []))

    def read_entity_rows(self, boxes: Iterable[str] = None, limit: int = 0) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        safe_limit = int(limit or 0)
        for box in normalized_boxes(boxes):
            active_scope = ""
            active_snapshot = ""
            if box == "ABox":
                active_scope = self.active_abox_members_clause([("$n", "entity")]) + " "
            query = (
                "match " + active_scope + "$n isa ontology-node, "
                "has ontology-id $id, "
                "has ontology-label $label, "
                "has ontology-kind $kind, "
                "has ontology-box " + typedb_string(box) + active_snapshot + ", "
                "has ontology-updated-at $updatedAt, "
                "has ontology-json $json; "
                + typeql_limit_clause(safe_limit)
            )
            rows.extend(self.entity_rows_from_typeql(self.read_rows(
                query,
                ["id", "label", "kind", "updatedAt", "json"],
            ), box))
            if safe_limit > 0 and len(rows) >= safe_limit:
                break
        rows = sorted(rows, key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("id") or "")), reverse=True)
        return rows[:safe_limit] if safe_limit > 0 else rows

    def read_entity_rows_by_ids(self, ids: Iterable[str], boxes: Iterable[str] = None) -> List[Dict[str, object]]:
        clean_ids = sorted(set(str(item or "").strip() for item in ids or [] if str(item or "").strip()))
        if not clean_ids:
            return []
        rows: List[Dict[str, object]] = []
        id_filter = typedb_value_match("$n", "ontology-id", clean_ids, "==", "idFilter")
        for box in normalized_boxes(boxes):
            active_scope = ""
            active_snapshot = ""
            if box == "ABox":
                active_scope = self.active_abox_members_clause([("$n", "entityById")]) + " "
            query = (
                "match " + active_scope + "$n isa ontology-node, "
                "has ontology-id $id, "
                "has ontology-label $label, "
                "has ontology-kind $kind, "
                "has ontology-box " + typedb_string(box) + active_snapshot + ", "
                "has ontology-updated-at $updatedAt, "
                "has ontology-json $json; "
                + id_filter
            )
            rows.extend(self.entity_rows_from_typeql(self.read_rows(
                query,
                ["id", "label", "kind", "updatedAt", "json"],
            ), box))
        return rows

    def read_relation_rows_by_source_ids(
        self,
        source_ids: Iterable[str],
        boxes: Iterable[str] = None,
        relation_types: Iterable[str] = None,
        include_incoming: bool = True,
    ) -> List[Dict[str, object]]:
        clean_ids = sorted(set(str(item or "").strip() for item in source_ids or [] if str(item or "").strip()))
        if not clean_ids:
            return []
        clean_relation_types = sorted(set(
            str(item or "").upper().strip()
            for item in relation_types or []
            if str(item or "").strip()
        ))
        rows: List[Dict[str, object]] = []
        endpoint_filters = [
            typedb_value_match("$source", "ontology-id", clean_ids, "==", "sourceIdFilter"),
        ]
        if include_incoming:
            endpoint_filters.append(
                typedb_value_match("$target", "ontology-id", clean_ids, "==", "targetIdFilter")
            )
        for box in normalized_boxes(boxes):
            active_scope = ""
            active_snapshot = ""
            endpoint_scope = ""
            if box == "ABox":
                active_scope = self.active_abox_members_clause([
                    ("$source", "sourceById"),
                    ("$target", "targetById"),
                    ("$r", "relationById"),
                ]) + " "
            for endpoint_filter in endpoint_filters:
                query = (
                    "match " + active_scope
                    + "$source isa ontology-node, has ontology-id $sourceId" + endpoint_scope + ", has ontology-label $sourceLabel, "
                    "has ontology-kind $sourceKind, has ontology-updated-at $sourceUpdatedAt, has ontology-json $sourceJson; "
                    "$target isa ontology-node, has ontology-id $targetId" + endpoint_scope + ", has ontology-label $targetLabel, "
                    "has ontology-kind $targetKind, has ontology-updated-at $targetUpdatedAt, has ontology-json $targetJson; "
                    "$r isa ontology-assertion, links (source: $source, target: $target), "
                    "has ontology-id $id, "
                    "has ontology-relation-type $type, "
                    "has ontology-box " + typedb_string(box) + active_snapshot + ", "
                    "has ontology-updated-at $updatedAt, "
                    "has ontology-json $json, "
                    "has ontology-weight $weight; "
                    + typedb_value_match("$r", "ontology-relation-type", clean_relation_types, "==", "relationTypeFilter")
                    + endpoint_filter
                )
                raw_rows = self.read_rows(
                    query,
                    [
                        "id", "sourceId", "sourceLabel", "sourceKind", "sourceUpdatedAt", "sourceJson",
                        "targetId", "targetLabel", "targetKind", "targetUpdatedAt", "targetJson",
                        "type", "updatedAt", "json", "weight",
                    ],
                )
                mapped_rows = self.relation_rows_from_typeql(raw_rows, box)
                for mapped, raw in zip(mapped_rows, raw_rows):
                    mapped["sourceNode"] = endpoint_node_row(raw, "source", box)
                    mapped["targetNode"] = endpoint_node_row(raw, "target", box)
                rows.extend(mapped_rows)
        return list({str(row.get("id") or ""): row for row in rows if str(row.get("id") or "").strip()}.values())

    def active_abox_relation_types_by_symbol(
        self,
        symbols: Iterable[str] = None,
        timeout_seconds: float = None,
    ) -> Dict[str, object]:
        """Read a compact active-ABox topology index for stock subjects.

        Native rule planning only needs each stock's available relation types.
        Loading every endpoint's JSON payload for that purpose made the planner
        compete with ABox projection writes and could exceed the realtime read
        deadline. This query keeps the TypeDB-owned topology while returning
        only stock id, symbol, and relation type.
        """
        clean_symbols = clean_symbols_from_payload(list(symbols or []))
        source_ids_by_symbol: Dict[str, set] = {symbol: set() for symbol in clean_symbols}
        relation_types_by_symbol: Dict[str, set] = {symbol: set() for symbol in clean_symbols}
        relation_ids = set()
        symbol_filter = typedb_value_match(
            "$stock",
            "ontology-symbol",
            clean_symbols,
            "==",
            "stockSymbolFilter",
        )
        for role, links_clause in [
            ("source", "links (source: $stock, target: $other)"),
            ("target", "links (source: $other, target: $stock)"),
        ]:
            active_scope = self.active_abox_members_clause([
                ("$stock", "topologyStock" + role.title()),
                ("$other", "topologyOther" + role.title()),
                ("$r", "topologyRelation" + role.title()),
            ]) + " "
            active_snapshot = ""
            query = (
                "match " + active_scope
                + "$stock isa ontology-node, has ontology-id $sourceId, has ontology-kind \"stock\", "
                "has ontology-box \"ABox\"" + active_snapshot + ", "
                "has ontology-symbol $symbol; "
                + "$r isa ontology-assertion, " + links_clause + ", has ontology-id $relationId, "
                "has ontology-box \"ABox\"" + active_snapshot + ", "
                "has ontology-relation-type $relationType; "
                + symbol_filter
            )
            rows = self.read_rows(
                query,
                ["sourceId", "symbol", "relationId", "relationType"],
                label="typedb.active-abox-relation-types:" + role,
                timeout_seconds=timeout_seconds,
            )
            for row in rows:
                symbol = str(row.get("symbol") or "").upper().strip()
                source_id = str(row.get("sourceId") or "").strip()
                relation_type = str(row.get("relationType") or "").upper().strip()
                relation_id = str(row.get("relationId") or "").strip()
                if not symbol:
                    continue
                source_ids_by_symbol.setdefault(symbol, set())
                relation_types_by_symbol.setdefault(symbol, set())
                if source_id:
                    source_ids_by_symbol[symbol].add(source_id)
                if relation_type:
                    relation_types_by_symbol[symbol].add(relation_type)
                if relation_id:
                    relation_ids.add(relation_id)
        symbols_out = clean_symbols or sorted(source_ids_by_symbol)
        return {
            "status": "ok",
            "symbols": symbols_out,
            "sourceIdsBySymbol": {
                symbol: sorted(source_ids_by_symbol.get(symbol, set()))
                for symbol in symbols_out
            },
            "relationTypesBySymbol": {
                symbol: sorted(relation_types_by_symbol.get(symbol, set()))
                for symbol in symbols_out
            },
            "relationCount": len(relation_ids),
        }

    def active_abox_rule_context(self, symbols: Iterable[str]) -> Dict[str, object]:
        """Load only TypeDB facts needed to plan schema-function calls.

        This remains a topology-only execution planner. TypeDB schema functions
        still evaluate every rule condition and decide whether the rule matches.
        """
        clean_symbols = clean_symbols_from_payload(list(symbols or []))
        if not clean_symbols:
            return {
                "status": "empty",
                "symbols": [],
                "sourceIdsBySymbol": {},
                "relationTypesBySymbol": {},
                "relationCount": 0,
            }
        return self.active_abox_relation_types_by_symbol(
            clean_symbols,
            timeout_seconds=self.native_rule_query_timeout_seconds(),
        )

    def active_abox_snapshot_id(self) -> str:
        metadata = self.active_abox_metadata()
        if str(metadata.get("status") or "") != "ok":
            return ""
        return str(metadata.get("aboxSnapshotId") or "")

    def box_snapshot_row_counts(self, box: str, snapshot_id: str) -> Dict[str, int]:
        clean_box = str(box or "").strip()
        clean_snapshot_id = str(snapshot_id or "").strip()
        if not clean_box or not clean_snapshot_id:
            return {"entityCount": 0, "relationCount": 0}

        def count(type_label: str) -> int:
            query = (
                "match $item isa " + type_label
                + ", has ontology-box " + typedb_string(clean_box)
                + ", has ontology-snapshot-id " + typedb_string(clean_snapshot_id)
                + "; reduce $count = count;"
            )
            rows = self.read_rows(query, ["count"], label="typedb.box-snapshot-count")
            return int(number_or_none((rows[0] if rows else {}).get("count")) or 0)

        return {
            "entityCount": count("ontology-node"),
            "relationCount": count("ontology-assertion"),
        }

    def box_row_counts(self, box: str) -> Dict[str, int]:
        """Count one ontology box without loading its full JSON payloads."""
        clean_box = str(box or "").strip()
        if not clean_box:
            return {"entityCount": 0, "relationCount": 0}

        def count(type_label: str) -> int:
            query = (
                "match $item isa " + type_label
                + ", has ontology-box " + typedb_string(clean_box)
                + "; reduce $count = count;"
            )
            rows = self.read_rows(query, ["count"], label="typedb.box-count")
            return int(number_or_none((rows[0] if rows else {}).get("count")) or 0)

        return {
            "entityCount": count("ontology-node"),
            "relationCount": count("ontology-assertion"),
        }

    def abox_projection_marker_rows(self) -> List[Dict[str, object]]:
        query = (
            "match $n isa ontology-node, "
            "has ontology-id $id, "
            "has ontology-label $label, "
            "has ontology-kind \"abox-projection-marker\", "
            "has ontology-box \"ABox\", "
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json;"
        )
        return self.entity_rows_from_typeql(
            self.read_rows(query, ["id", "label", "kind", "updatedAt", "json"], label="typedb.abox-marker"),
            "ABox",
        )

    def active_worldview_manifest_pointer_rows(self) -> List[Dict[str, object]]:
        query = (
            "match $n isa ontology-node, "
            "has ontology-id $id, "
            "has ontology-label $label, "
            "has ontology-kind \"worldview-manifest-active-pointer\", "
            "has ontology-box \"ABoxControl\", "
            "has ontology-snapshot-id $snapshotId, "
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json;"
        )
        return self.entity_rows_from_typeql(
            self.read_rows(
                query,
                ["id", "label", "kind", "snapshotId", "updatedAt", "json"],
                label="typedb.worldview-manifest-active-pointer",
            ),
            "ABoxControl",
        )

    def worldview_manifest_marker_rows(self) -> List[Dict[str, object]]:
        query = (
            "match $n isa ontology-node, "
            "has ontology-id $id, "
            "has ontology-label $label, "
            "has ontology-kind \"worldview-manifest-marker\", "
            "has ontology-box \"ABox\", "
            "has ontology-snapshot-id $snapshotId, "
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json;"
        )
        return self.entity_rows_from_typeql(
            self.read_rows(
                query,
                ["id", "label", "kind", "snapshotId", "updatedAt", "json"],
                label="typedb.worldview-manifest-marker",
            ),
            "ABox",
        )

    @staticmethod
    def scoped_abox_metadata_from_manifest_marker(marker: Dict[str, object]) -> Dict[str, object]:
        payload = dict(marker or {})
        manifest_id = str(
            payload.get("worldviewManifestId")
            or payload.get("aboxSnapshotId")
            or payload.get("snapshotId")
            or ""
        ).strip()
        scope_plan = payload.get("scopePlan") if isinstance(payload.get("scopePlan"), list) else []
        generations = payload.get("scopeGenerationIds") if isinstance(payload.get("scopeGenerationIds"), dict) else {}
        fingerprints = payload.get("scopeFingerprints") if isinstance(payload.get("scopeFingerprints"), dict) else {}
        if not manifest_id or not scope_plan or not generations:
            return {}
        return {
            "configured": True,
            "status": "ok",
            "graphStore": "typedb",
            "aboxSnapshotId": manifest_id,
            "worldviewManifestId": manifest_id,
            "materialFingerprint": str(payload.get("materialFingerprint") or ""),
            "projectionRunId": str(payload.get("projectionRunId") or ""),
            "asOf": str(payload.get("asOf") or ""),
            "scopedAboxManifestVersion": str(payload.get("scopedAboxManifestVersion") or SCOPED_ABOX_MANIFEST_VERSION),
            "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
            "scopePlan": list(scope_plan),
            "scopeGenerationIds": dict(generations),
            "scopeFingerprints": dict(fingerprints),
            "scopeTopologyVersion": str(payload.get("scopeTopologyVersion") or ""),
            "scopeFamilyCounts": dict(payload.get("scopeFamilyCounts") or {}),
            "scopeDelta": dict(payload.get("scopeDelta") or {}),
            "inferenceImpactPlan": dict(payload.get("inferenceImpactPlan") or {}),
            "activeScopeCount": len(generations),
            "manifestMarkerId": str(payload.get("id") or ""),
        }

    def active_abox_pointer_rows(self) -> List[Dict[str, object]]:
        query = (
            "match $n isa ontology-node, "
            "has ontology-id $id, "
            "has ontology-label $label, "
            "has ontology-kind \"abox-active-pointer\", "
            "has ontology-box \"ABoxControl\", "
            "has ontology-snapshot-id $snapshotId, "
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json;"
        )
        return self.entity_rows_from_typeql(
            self.read_rows(
                query,
                ["id", "label", "kind", "snapshotId", "updatedAt", "json"],
                label="typedb.abox-active-pointer",
            ),
            "ABoxControl",
        )

    def abox_metadata_from_marker(self, marker: Dict[str, object]) -> Dict[str, object]:
        snapshot_id = str(marker.get("aboxSnapshotId") or marker.get("snapshotId") or "").strip()
        expected_entities = number_or_none(marker.get("expectedAboxEntityCount"))
        expected_relations = number_or_none(marker.get("expectedAboxRelationCount"))
        if not snapshot_id or expected_entities is None or expected_relations is None:
            return {}
        try:
            actual = self.box_snapshot_row_counts("ABox", snapshot_id)
        except Exception as error:  # noqa: BLE001 - metadata must describe a read verification failure.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": snapshot_id,
                "materialFingerprint": str(marker.get("materialFingerprint") or "").strip(),
                "reason": str(error)[:180],
            }
        expected = {
            "entityCount": int(expected_entities),
            "relationCount": int(expected_relations),
        }
        complete = (
            actual["entityCount"] == expected["entityCount"] + 1
            and actual["relationCount"] == expected["relationCount"]
        )
        return {
            "configured": True,
            "status": "ok" if complete else "incomplete",
            "graphStore": "typedb",
            "aboxSnapshotId": snapshot_id,
            "materialFingerprint": str(marker.get("materialFingerprint") or "").strip(),
            "projectionRunId": str(marker.get("projectionRunId") or "").strip(),
            "asOf": str(marker.get("asOf") or ""),
            "expectedEntityCount": expected["entityCount"],
            "expectedRelationCount": expected["relationCount"],
            "actualEntityCount": actual["entityCount"] - 1 if actual["entityCount"] else 0,
            "actualRelationCount": actual["relationCount"],
            "completionMarkerId": str(marker.get("id") or ""),
        }

    def active_abox_metadata(self) -> Dict[str, object]:
        try:
            manifests = sorted(
                self.active_worldview_manifest_pointer_rows(),
                key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
                reverse=True,
            )
        except Exception:
            # A rolling deployment can have a healthy legacy ABox while the
            # newer Manifest control attributes are not queryable yet.
            manifests = []
        if manifests:
            pointer = manifests[0]
            manifest_id = str(
                pointer.get("worldviewManifestId")
                or pointer.get("aboxSnapshotId")
                or pointer.get("snapshotId")
                or ""
            ).strip()
            try:
                markers = {
                    str(item.get("worldviewManifestId") or item.get("aboxSnapshotId") or item.get("snapshotId") or "").strip(): item
                    for item in self.worldview_manifest_marker_rows()
                }
            except Exception:
                markers = {}
            metadata = self.scoped_abox_metadata_from_manifest_marker(markers.get(manifest_id) or {})
            if metadata:
                metadata["activePointerId"] = str(pointer.get("id") or "")
                return metadata
            return {
                "configured": True,
                "status": "incomplete",
                "graphStore": "typedb",
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "activePointerId": str(pointer.get("id") or ""),
                "reason": "Active Worldview Manifest pointer has no complete manifest marker.",
            }
        markers = sorted(
            self.abox_projection_marker_rows(),
            key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
            reverse=True,
        )
        markers_by_snapshot = {
            str(item.get("aboxSnapshotId") or item.get("snapshotId") or "").strip(): item
            for item in markers
            if str(item.get("aboxSnapshotId") or item.get("snapshotId") or "").strip()
        }
        pointers = sorted(
            self.active_abox_pointer_rows(),
            key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
            reverse=True,
        )
        if pointers:
            pointer = pointers[0]
            snapshot_id = str(pointer.get("aboxSnapshotId") or pointer.get("snapshotId") or "").strip()
            marker = markers_by_snapshot.get(snapshot_id)
            if marker:
                metadata = self.abox_metadata_from_marker(marker)
                if metadata:
                    metadata["activePointerId"] = str(pointer.get("id") or "")
                    return metadata
            return {
                "configured": True,
                "status": "incomplete",
                "graphStore": "typedb",
                "aboxSnapshotId": snapshot_id,
                "materialFingerprint": str(pointer.get("materialFingerprint") or "").strip(),
                "activePointerId": str(pointer.get("id") or ""),
                "reason": "Active ABox pointer has no complete candidate marker.",
            }
        if markers:
            newest = self.abox_metadata_from_marker(markers[0])
            return {
                "configured": True,
                "status": "empty",
                "graphStore": "typedb",
                "aboxSnapshotId": "",
                "materialFingerprint": "",
                "pendingAboxSnapshotId": str(newest.get("aboxSnapshotId") or ""),
                "reason": "ABox active pointer is missing.",
            }
        return {
            "configured": True,
            "status": "empty",
            "graphStore": "typedb",
            "aboxSnapshotId": "",
            "materialFingerprint": "",
        }

    def abox_pending_activation_rows(self) -> List[Dict[str, object]]:
        """Return durable ABox activation hand-offs awaiting native inference.

        The active pointer is intentionally switched only after a candidate
        ABox verifies. Native TypeDB inference follows in a separate operation,
        so the hand-off must survive a worker or server restart. A control row
        makes that otherwise transient state observable and recoverable.
        """
        query = (
            "match $n isa ontology-node, "
            "has ontology-id $id, "
            "has ontology-label $label, "
            "has ontology-kind \"abox-activation-pending\", "
            "has ontology-box \"ABoxControl\", "
            "has ontology-snapshot-id $snapshotId, "
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json;"
        )
        return self.entity_rows_from_typeql(
            self.read_rows(
                query,
                ["id", "label", "kind", "snapshotId", "updatedAt", "json"],
                label="typedb.abox-activation-pending",
            ),
            "ABoxControl",
        )

    def pending_abox_activation(self) -> Dict[str, object]:
        rows = sorted(
            self.abox_pending_activation_rows(),
            key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
            reverse=True,
        )
        if not rows:
            return {
                "configured": True,
                "status": "empty",
                "graphStore": "typedb",
            }
        row = rows[0]
        candidate_snapshot_id = str(
            row.get("candidateAboxSnapshotId") or row.get("aboxSnapshotId") or row.get("snapshotId") or ""
        ).strip()
        return {
            "configured": True,
            "status": "pending" if candidate_snapshot_id else "invalid",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_snapshot_id,
            "previousAboxSnapshotId": str(row.get("previousAboxSnapshotId") or "").strip(),
            "materialFingerprint": str(row.get("materialFingerprint") or "").strip(),
            "projectionRunId": str(row.get("projectionRunId") or "").strip(),
            "asOf": str(row.get("asOf") or ""),
            "targetSymbols": clean_symbols_from_payload(row.get("targetSymbols") or row.get("inferenceTargetSymbols") or []),
            "activationStatus": str(row.get("activationStatus") or "pending-native-inference"),
            "candidateWorldviewManifestId": str(row.get("candidateWorldviewManifestId") or "").strip(),
            "controlId": str(row.get("id") or ""),
            "updatedAt": str(row.get("updatedAt") or ""),
        }

    def read_relation_rows(self, boxes: Iterable[str] = None, limit: int = 0) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        safe_limit = int(limit or 0)
        for box in normalized_boxes(boxes):
            active_scope = ""
            active_snapshot = ""
            endpoint_scope = ""
            if box == "ABox":
                active_scope = self.active_abox_members_clause([
                    ("$source", "relationSource"),
                    ("$target", "relationTarget"),
                    ("$r", "relation"),
                ]) + " "
            query = (
                "match " + active_scope
                + "$source isa ontology-node, has ontology-id $sourceId" + endpoint_scope + ", has ontology-label $sourceLabel; "
                + "$target isa ontology-node, has ontology-id $targetId" + endpoint_scope + ", has ontology-label $targetLabel; "
                "$r isa ontology-assertion, links (source: $source, target: $target), "
                "has ontology-id $id, "
                "has ontology-relation-type $type, "
                "has ontology-box " + typedb_string(box) + active_snapshot + ", "
                "has ontology-updated-at $updatedAt, "
                "has ontology-json $json, "
                "has ontology-weight $weight; "
                + typeql_limit_clause(safe_limit)
            )
            rows.extend(self.relation_rows_from_typeql(self.read_rows(
                query,
                ["id", "sourceId", "sourceLabel", "targetId", "targetLabel", "type", "updatedAt", "json", "weight"],
            ), box))
            if safe_limit > 0 and len(rows) >= safe_limit:
                break
        rows = sorted(rows, key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("source") or ""), str(item.get("target") or "")), reverse=True)
        return rows[:safe_limit] if safe_limit > 0 else rows

    def read_inference_generation_records(self, published_only: bool = True) -> List[Dict[str, object]]:
        published_rows = self.read_rows(
            (
                'match $n isa ontology-node, has ontology-box "InferenceBox", '
                'has ontology-kind "inference-generation", '
                "has ontology-snapshot-id $snapshotId, "
                "has ontology-updated-at $updatedAt, "
                "has ontology-json $json;"
            ),
            ["snapshotId", "updatedAt", "json"],
        )
        candidate_rows = self.read_rows(
            (
                'match $n isa ontology-node, has ontology-box "InferenceBox", '
                'has ontology-kind "inference-generation-candidate", '
                "has ontology-snapshot-id $snapshotId, "
                "has ontology-updated-at $updatedAt;"
            ),
            ["snapshotId", "updatedAt"],
        )
        node_rows = self.read_rows(
            (
                'match $n isa ontology-node, has ontology-box "InferenceBox", '
                "has ontology-snapshot-id $snapshotId, "
                "has ontology-updated-at $updatedAt;"
            ),
            ["snapshotId", "updatedAt"],
        )
        relation_rows = self.read_rows(
            (
                'match $r isa ontology-assertion, has ontology-box "InferenceBox", '
                "has ontology-snapshot-id $snapshotId, "
                "has ontology-updated-at $updatedAt;"
            ),
            ["snapshotId", "updatedAt"],
        )
        indexed_rows = [
            {"snapshotId": row.get("snapshotId"), "updatedAt": row.get("updatedAt")}
            for row in node_rows
        ] + [
            {"snapshotId": row.get("snapshotId"), "updatedAt": row.get("updatedAt"), "relationType": "ontology-assertion"}
            for row in relation_rows
        ]
        records = inference_generation_records(indexed_rows, [])
        candidate_ids = {
            str(row.get("snapshotId") or "")
            for row in candidate_rows
            if str(row.get("snapshotId") or "").strip()
        }
        if not published_rows:
            if published_only:
                return []
            return [
                {**record, "publicationStatus": "candidate" if str(record.get("generationId") or "") in candidate_ids else "staging"}
                for record in records
            ]
        published = {
            str(row.get("snapshotId") or ""): str(row.get("updatedAt") or "")
            for row in published_rows
            if str(row.get("snapshotId") or "").strip()
            and inference_marker_is_active(row.get("json"))
        }
        if not published_only:
            return [
                {
                    **record,
                    "latestAt": published.get(str(record.get("generationId") or ""), record.get("latestAt")),
                    "publicationStatus": (
                        "active"
                        if str(record.get("generationId") or "") in published
                        else ("candidate" if str(record.get("generationId") or "") in candidate_ids else "staging")
                    ),
                }
                for record in records
            ]
        result = []
        for record in records:
            generation_id = str(record.get("generationId") or "")
            if generation_id not in published:
                continue
            result.append({
                **record,
                "latestAt": published[generation_id] or record.get("latestAt"),
                "publicationStatus": "active",
            })
        return sorted(result, key=lambda item: str(item.get("latestAt") or ""), reverse=True)

    def read_inferencebox_entity_rows(
        self,
        generation_id: str = "",
        symbols: Iterable[str] = None,
        limit: int = 0,
    ) -> List[Dict[str, object]]:
        safe_limit = int(limit or 0)
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        query = (
            "match $n isa ontology-node, "
            "has ontology-id $id, "
            "has ontology-label $label, "
            "has ontology-kind $kind, "
            'has ontology-box "InferenceBox", '
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json; "
        )
        if generation_id:
            query += typedb_value_match("$n", "ontology-snapshot-id", generation_id, "==", "generationFilter")
        if clean_symbols:
            query += typedb_value_match("$n", "ontology-symbol", clean_symbols, "==", "symbolFilter")
        query += typeql_limit_clause(safe_limit)
        rows = self.entity_rows_from_typeql(self.read_rows(
            query,
            ["id", "label", "kind", "updatedAt", "json"],
        ), "InferenceBox")
        rows = sorted(rows, key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("id") or "")), reverse=True)
        return rows[:safe_limit] if safe_limit > 0 else rows

    def read_inferencebox_relation_rows(
        self,
        generation_id: str = "",
        symbols: Iterable[str] = None,
        limit: int = 0,
    ) -> List[Dict[str, object]]:
        safe_limit = int(limit or 0)
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        query = (
            "match "
            "$source isa ontology-node, has ontology-id $sourceId, has ontology-label $sourceLabel; "
            "$target isa ontology-node, has ontology-id $targetId, has ontology-label $targetLabel; "
            "$r isa ontology-assertion, links (source: $source, target: $target), "
            "has ontology-id $id, "
            "has ontology-relation-type $type, "
            'has ontology-box "InferenceBox", '
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json, "
            "has ontology-weight $weight; "
        )
        if generation_id:
            query += typedb_value_match("$r", "ontology-snapshot-id", generation_id, "==", "generationFilter")
        if clean_symbols:
            query += typedb_value_match("$r", "ontology-symbol", clean_symbols, "==", "symbolFilter")
        query += typeql_limit_clause(safe_limit)
        rows = self.relation_rows_from_typeql(self.read_rows(
            query,
            ["id", "sourceId", "sourceLabel", "targetId", "targetLabel", "type", "updatedAt", "json", "weight"],
        ), "InferenceBox")
        rows = sorted(rows, key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("source") or ""), str(item.get("target") or "")), reverse=True)
        return rows[:safe_limit] if safe_limit > 0 else rows

    def entity_rows_from_typeql(self, rows: Iterable[Dict[str, object]], box: str) -> List[Dict[str, object]]:
        return [self.entity_row_from_typeql(row, box) for row in rows or [] if str(row.get("id") or "")]

    def entity_row_from_typeql(self, row: Dict[str, object], box: str) -> Dict[str, object]:
        props = json_object(row.get("json"))
        node_kind = str(row.get("kind") or props.get("kind") or "")
        merged = merge_flat_properties({
            "id": row.get("id"),
            "label": row.get("label"),
            "kind": node_kind,
            "ontologyBox": box,
            "symbol": row.get("symbol"),
            "ruleId": row.get("ruleId"),
            "tboxClass": row.get("tboxClass"),
            "updatedAt": row.get("updatedAt"),
        }, props)
        condition = merged.get("condition") if isinstance(merged.get("condition"), dict) else {}
        derivation = merged.get("derivation") if isinstance(merged.get("derivation"), dict) else {}
        proposed = merged.get("proposedRule") if isinstance(merged.get("proposedRule"), dict) else None
        payload = {
            **merged,
            "id": str(row.get("id") or merged.get("id") or ""),
            "label": str(row.get("label") or merged.get("label") or row.get("id") or ""),
            "nodeKind": node_kind,
            "kind": str(condition.get("kind") or merged.get("conditionKind") or node_kind) if node_kind == "rule-condition" else node_kind,
            "ontologyBox": str(box or merged.get("ontologyBox") or "ABox"),
            "symbol": str(row.get("symbol") or merged.get("symbol") or ""),
            "ruleId": str(row.get("ruleId") or merged.get("ruleId") or ""),
            "sourceRuleId": str(merged.get("sourceRuleId") or row.get("ruleId") or merged.get("ruleId") or ""),
            "nativeRuleId": str(merged.get("nativeRuleId") or typedb_native_rule_id(row.get("ruleId") or merged.get("ruleId"))),
            "semanticRuleId": str(merged.get("semanticRuleId") or merged.get("nativeRuleId") or typedb_native_rule_id(row.get("ruleId") or merged.get("ruleId"))),
            "reasoningLayer": str(merged.get("reasoningLayer") or ""),
            "reasoningMode": str(merged.get("reasoningMode") or ""),
            "materializationSource": str(merged.get("materializationSource") or ""),
            "typedbNativeRuleReasoned": bool(merged.get("typedbNativeRuleReasoned")),
            "tboxClass": str(row.get("tboxClass") or merged.get("tboxClass") or ""),
            "updatedAt": str(row.get("updatedAt") or merged.get("updatedAt") or ""),
            "propertiesJson": json.dumps(props, ensure_ascii=False, sort_keys=True),
            "version": str(merged.get("version") or ""),
            "sourceKind": str(merged.get("sourceKind") or ""),
            "actionGroup": str(merged.get("actionGroup") or merged.get("action_group") or ""),
            "actionLevel": str(merged.get("actionLevel") or merged.get("action_level") or ""),
            "promptHint": str(merged.get("promptHint") or ""),
            "anyConditionMinCount": int(number_or_none(merged.get("anyConditionMinCount")) or 1),
            "enabled": bool(merged.get("enabled", True)),
            "conditionId": str(merged.get("conditionId") or condition.get("condition_id") or ""),
            "conditionIndex": int(number_or_none(merged.get("conditionIndex")) or 0),
            "conditionKind": str(condition.get("kind") or merged.get("conditionKind") or ""),
            "conditionField": str(condition.get("field") or merged.get("conditionField") or ""),
            "conditionOperator": str(condition.get("operator") or merged.get("conditionOperator") or ""),
            "conditionRole": str(condition.get("role") or merged.get("conditionRole") or "required"),
            "conditionValueString": str(condition.get("value") or merged.get("conditionValueString") or ""),
            "conditionValueNumber": number_or_none(condition.get("value") if "value" in condition else merged.get("conditionValueNumber")),
            "conditionRelationType": str(condition.get("relation_type") or merged.get("conditionRelationType") or "").upper(),
            "conditionDirection": str(condition.get("direction") or merged.get("conditionDirection") or "out"),
            "conditionTargetKind": str(condition.get("target_kind") or merged.get("conditionTargetKind") or ""),
            "conditionRelationEvidenceRoles": condition_relation_filter_values(condition, "evidenceRole"),
            "derivationIndex": int(number_or_none(merged.get("derivationIndex")) or 0),
            "derivationRelationType": str(derivation.get("relation_type") or merged.get("derivationRelationType") or "").upper(),
            "derivationTargetKind": str(derivation.get("target_kind") or merged.get("derivationTargetKind") or ""),
            "derivationTargetKey": str(derivation.get("target_key") or merged.get("derivationTargetKey") or ""),
            "derivationTargetLabel": str(derivation.get("target_label") or merged.get("derivationTargetLabel") or ""),
            "derivationTboxClass": str(derivation.get("tbox_class") or merged.get("derivationTboxClass") or ""),
            "derivationTboxClasses": list_of_strings(derivation.get("tbox_classes") or merged.get("derivationTboxClasses")),
            "derivationPolarity": str(derivation.get("polarity") or merged.get("derivationPolarity") or ""),
            "derivationEvidenceRole": str(derivation.get("evidence_role") or derivation.get("evidenceRole") or merged.get("derivationEvidenceRole") or derivation.get("polarity") or "context"),
            "derivationBeliefLabel": str(derivation.get("belief_label") or merged.get("derivationBeliefLabel") or ""),
            "derivationAiInfluenceLabel": str(derivation.get("ai_influence_label") or merged.get("derivationAiInfluenceLabel") or ""),
            "derivationActionGroup": str(derivation.get("action_group") or merged.get("derivationActionGroup") or ""),
            "derivationActionLevel": str(derivation.get("action_level") or merged.get("derivationActionLevel") or ""),
            "derivationDecisionStage": str(derivation.get("decision_stage") or derivation.get("decisionStage") or merged.get("derivationDecisionStage") or ""),
            "derivationDecisionLabel": str(derivation.get("decision_label") or derivation.get("decisionLabel") or merged.get("derivationDecisionLabel") or ""),
            "derivationDecisionTone": str(derivation.get("decision_tone") or derivation.get("decisionTone") or merged.get("derivationDecisionTone") or ""),
            "derivationTargetRole": str(derivation.get("target_role") or derivation.get("targetRole") or merged.get("derivationTargetRole") or ""),
            "derivationActionPolicy": str(derivation.get("action_policy") or derivation.get("actionPolicy") or merged.get("derivationActionPolicy") or ""),
            "derivationAllowedActions": list_of_strings(derivation.get("allowed_actions") or derivation.get("allowedActions") or merged.get("derivationAllowedActions")),
            "derivationBlockedActions": list_of_strings(derivation.get("blocked_actions") or derivation.get("blockedActions") or merged.get("derivationBlockedActions")),
            "polarity": str(merged.get("polarity") or ""),
            "evidenceRole": str(merged.get("evidenceRole") or "context"),
            "decisionStage": str(merged.get("decisionStage") or ""),
            "reviewLevel": str(merged.get("reviewLevel") or "observe"),
            "reviewLevelLabel": str(merged.get("reviewLevelLabel") or ""),
            "dataState": str(merged.get("dataState") or "partial"),
            "dataStateLabel": str(merged.get("dataStateLabel") or ""),
            "conflictState": str(merged.get("conflictState") or "context-only"),
            "nativeTypeDbReasoned": bool(merged.get("nativeTypeDbReasoned")),
            "title": str(merged.get("title") or row.get("label") or ""),
            "status": str(merged.get("status") or ""),
            "priority": number_or_none(merged.get("priority")) or 0,
            "source": str(merged.get("source") or ""),
            "rationale": str(merged.get("rationale") or ""),
            "expectedEffect": str(merged.get("expectedEffect") or ""),
            "risk": str(merged.get("risk") or ""),
            "action": str(merged.get("action") or ""),
            "requiresData": list_of_strings(merged.get("requiresData")),
            "proposedRuleJson": json.dumps(proposed, ensure_ascii=False, sort_keys=True) if proposed else str(merged.get("proposedRuleJson") or ""),
            "validationWarnings": list_of_strings(merged.get("validationWarnings")),
            "promptVersion": str(merged.get("promptVersion") or ""),
            "createdAt": str(merged.get("createdAt") or ""),
            "symbols": list_of_strings(merged.get("symbols")),
        }
        return payload

    def relation_rows_from_typeql(self, rows: Iterable[Dict[str, object]], box: str) -> List[Dict[str, object]]:
        return [self.relation_row_from_typeql(row, box) for row in rows or [] if str(row.get("sourceId") or "") and str(row.get("targetId") or "")]

    def relation_row_from_typeql(self, row: Dict[str, object], box: str) -> Dict[str, object]:
        props = json_object(row.get("json"))
        merged = merge_flat_properties({
            "source": row.get("sourceId"),
            "sourceLabel": row.get("sourceLabel"),
            "target": row.get("targetId"),
            "targetLabel": row.get("targetLabel"),
            "type": row.get("type"),
            "ruleId": row.get("ruleId"),
            "ontologyBox": box,
            "updatedAt": row.get("updatedAt"),
            "weight": row.get("weight"),
        }, props)
        return {
            **merged,
            "id": str(row.get("id") or merged.get("id") or ""),
            "source": str(row.get("sourceId") or merged.get("source") or ""),
            "sourceLabel": str(row.get("sourceLabel") or merged.get("sourceLabel") or ""),
            "target": str(row.get("targetId") or merged.get("target") or ""),
            "targetLabel": str(row.get("targetLabel") or merged.get("targetLabel") or ""),
            "type": str(row.get("type") or merged.get("type") or ""),
            "relationType": str(row.get("type") or merged.get("relationType") or merged.get("type") or ""),
            "ontologyBox": str(box or merged.get("ontologyBox") or "ABox"),
            "symbol": str(merged.get("symbol") or ""),
            "ruleId": str(row.get("ruleId") or merged.get("ruleId") or ""),
            "sourceRuleId": str(merged.get("sourceRuleId") or row.get("ruleId") or merged.get("ruleId") or ""),
            "nativeRuleId": str(merged.get("nativeRuleId") or typedb_native_rule_id(row.get("ruleId") or merged.get("ruleId"))),
            "semanticRuleId": str(merged.get("semanticRuleId") or merged.get("nativeRuleId") or typedb_native_rule_id(row.get("ruleId") or merged.get("ruleId"))),
            "reasoningLayer": str(merged.get("reasoningLayer") or ""),
            "reasoningMode": str(merged.get("reasoningMode") or ""),
            "materializationSource": str(merged.get("materializationSource") or ""),
            "typedbNativeRuleReasoned": bool(merged.get("typedbNativeRuleReasoned")),
            "weight": number_or_none(row.get("weight") if row.get("weight") is not None else merged.get("weight")),
            "updatedAt": str(row.get("updatedAt") or merged.get("updatedAt") or ""),
            "propertiesJson": json.dumps(props, ensure_ascii=False, sort_keys=True),
            "polarity": str(merged.get("polarity") or ""),
            "evidenceRole": str(merged.get("evidenceRole") or "context"),
            "decisionStage": str(merged.get("decisionStage") or ""),
            "reviewLevel": str(merged.get("reviewLevel") or "observe"),
            "dataState": str(merged.get("dataState") or "partial"),
            "targetRole": str(merged.get("targetRole") or ""),
            "actionPolicy": str(merged.get("actionPolicy") or ""),
            "allowedActions": list_of_strings(merged.get("allowedActions")),
            "blockedActions": list_of_strings(merged.get("blockedActions")),
            "aiInfluenceLabel": str(merged.get("aiInfluenceLabel") or ""),
            "inferenceTraceId": str(merged.get("inferenceTraceId") or ""),
            "nativeTypeDbReasoned": bool(merged.get("nativeTypeDbReasoned")),
        }

    def abox_delete_batch_size(self, settings: Dict[str, object] = None) -> int:
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbABoxDeleteBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 1000
        # ABox replacement is a bounded operational cleanup, not a per-row
        # workflow. Very small batches turn a few thousand facts into dozens
        # of TypeDB commits and can starve the live reasoning worker before
        # it reaches the first insert batch.
        return max(100, min(5000, int(parsed)))

    def abox_incremental_cleanup_batch_size(self, settings: Dict[str, object] = None) -> int:
        """Keep one live cleanup slice below the TypeDB writer saturation point."""
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbABoxIncrementalCleanupBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 50
        # Full deletion remains available to explicit repair commands. Runtime
        # projection drains only a small slice, so a historic generation can
        # never monopolize the writer before the next market inference runs.
        return max(10, min(500, int(parsed)))

    def abox_incremental_cleanup_max_batches_per_save(self, settings: Dict[str, object] = None) -> int:
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbABoxIncrementalCleanupMaxBatchesPerSave")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 1
        return max(0, min(4, int(parsed)))

    def abox_inactive_generation_keep_count(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbABoxInactiveGenerationKeepCount")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 0
        # MySQL keeps the source snapshot and activation audit. TypeDB retains
        # only active facts, not a rollback or time-series history.
        return max(0, min(5, int(parsed)))

    def abox_inactive_generation_max_prune_per_save(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbABoxInactiveGenerationMaxPrunePerSave")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 2
        # Deletes are deliberately bounded so a live activation cannot spend
        # minutes reclaiming a historic backlog under TypeDB's writer lock.
        return max(0, min(10, int(parsed)))

    def abox_write_transaction_query_count(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbABoxWriteTransactionQueryCount")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 8
        # A single ABox refresh can produce dozens of insert queries. Keeping
        # fifty of them in one transaction made the TypeDB writer hold its lock
        # for several minutes under live market load, which starved the next
        # reasoning and notification cycle. The caller clears a partial ABox on
        # failure, so short committed chunks are the safer operational boundary.
        return max(1, min(50, int(parsed)))

    def abox_relation_batch_size(self, settings: Dict[str, object] = None) -> int:
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbABoxRelationBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 4
        # Every active ABox endpoint is now matched through its generation-
        # scoped, @unique storage identity. Small independent batches therefore
        # have a bounded one-row lookup per endpoint rather than the legacy
        # public-ID cross product. Eight edges keeps one TypeQL statement below
        # the planner cliff while avoiding thousands of single-edge writes.
        return max(1, min(8, int(parsed)))

    def graph_write_transaction_query_count(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbGraphWriteTransactionQueryCount")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 8
        # TBox and TypeDB schema-function rule seeding can also contain
        # thousands of queries. Seed in short commits so startup does not hold
        # the TypeDB writer for minutes before the live ABox worker can run.
        # A subsequent seed deletes and rebuilds those boxes, so retrying a
        # partial seed is deterministic.
        return max(1, min(50, int(parsed)))

    def inferencebox_write_transaction_query_count(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbInferenceBoxWriteTransactionQueryCount")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 8
        # Staging facts are written before the ABox cutover. Bounded batches
        # keep one live refresh from monopolizing TypeDB while the active ABox
        # remains available to the inference worker.
        return max(1, min(50, int(parsed)))

    def inferencebox_relation_batch_size(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbInferenceBoxRelationBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 4
        # InferenceBox relations are more connected than ABox facts: one stock
        # can point to several traces, decisions, and explanation nodes. A
        # large multi-relation TypeQL insert lets the planner expand repeated
        # endpoint matches together. Four relations keeps queries below that
        # cross-product cliff while still avoiding one transaction per edge.
        return max(1, min(4, int(parsed)))

    def box_instance_exists(self, driver, imported, box: str, type_label: str) -> bool:
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        query = (
            "match $item isa " + str(type_label) + ", has ontology-box " + typedb_string(box) + "; limit 1;"
        )
        with driver.transaction(self.database, TransactionType.READ) as tx:
            return bool(self.read_rows_in_transaction(tx, query, [], label="typedb.box-exists"))

    def box_delete_batch_query(self, box: str, type_label: str, batch_size: int) -> str:
        variable = "$r" if str(type_label) == "ontology-assertion" else "$n"
        return (
            "match " + variable + " isa " + str(type_label) + ", has ontology-box " + typedb_string(box)
            + "; limit " + str(max(1, int(batch_size or 1))) + "; delete " + variable + ";"
        )

    def box_snapshot_instance_exists(
        self,
        driver,
        imported,
        box: str,
        snapshot_id: str,
        type_label: str,
    ) -> bool:
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        query = (
            "match $item isa " + str(type_label)
            + ", has ontology-box " + typedb_string(box)
            + ", has ontology-snapshot-id " + typedb_string(snapshot_id)
            + "; limit 1;"
        )
        with driver.transaction(self.database, TransactionType.READ) as tx:
            return bool(self.read_rows_in_transaction(tx, query, [], label="typedb.abox-candidate-exists"))

    def box_snapshot_delete_batch_query(
        self,
        box: str,
        snapshot_id: str,
        type_label: str,
        batch_size: int,
    ) -> str:
        variable = "$r" if str(type_label) == "ontology-assertion" else "$n"
        return (
            "match " + variable + " isa " + str(type_label)
            + ", has ontology-box " + typedb_string(box)
            + ", has ontology-snapshot-id " + typedb_string(snapshot_id)
            + "; limit " + str(max(1, int(batch_size or 1))) + "; delete " + variable + ";"
        )

    def delete_box_snapshot_rows_in_batches(
        self,
        driver,
        imported,
        box: str,
        snapshot_id: str,
        batch_size: int = None,
        max_batches: int = None,
    ) -> Dict[str, object]:
        """Delete one inactive ABox generation in short TypeDB writes.

        ``max_batches`` turns the operation into a bounded maintenance slice.
        The active projection path uses that mode so historical cleanup cannot
        consume an entire realtime reasoning cycle.
        """
        clean_box = str(box or "").strip()
        clean_snapshot_id = str(snapshot_id or "").strip()
        if not clean_box or not clean_snapshot_id:
            return {"status": "skipped", "deletedBatchCount": 0}
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        configured_batch_size = self.abox_delete_batch_size() if batch_size is None else int(batch_size or 0)
        safe_batch_size = max(1, min(5000, configured_batch_size))
        safe_max_batches = None if max_batches is None else max(0, int(max_batches or 0))
        deleted_batches = 0
        remaining_types: List[str] = []
        for type_label in ["ontology-assertion", "ontology-node"]:
            while self.box_snapshot_instance_exists(
                driver,
                imported,
                clean_box,
                clean_snapshot_id,
                type_label,
            ):
                if safe_max_batches is not None and deleted_batches >= safe_max_batches:
                    remaining_types.append(type_label)
                    break
                query = self.box_snapshot_delete_batch_query(
                    clean_box,
                    clean_snapshot_id,
                    type_label,
                    safe_batch_size,
                )

                def delete_batch():
                    with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB ABox candidate delete batch"):
                        with driver.transaction(
                            self.database,
                            TransactionType.WRITE,
                            options=self.write_transaction_options(),
                        ) as tx:
                            tx.query(query).resolve()
                            tx.commit()

                self.with_typedb_retries(delete_batch)
                deleted_batches += 1
            if remaining_types:
                break
        if safe_max_batches is not None and deleted_batches >= safe_max_batches:
            for type_label in ["ontology-assertion", "ontology-node"]:
                if self.box_snapshot_instance_exists(
                    driver,
                    imported,
                    clean_box,
                    clean_snapshot_id,
                    type_label,
                ) and type_label not in remaining_types:
                    remaining_types.append(type_label)
        return {
            "status": "partial" if remaining_types else "ok",
            "ontologyBox": clean_box,
            "aboxSnapshotId": clean_snapshot_id,
            "batchSize": safe_batch_size,
            "maxBatches": safe_max_batches,
            "deletedBatchCount": deleted_batches,
            "remainingRowTypes": remaining_types,
        }

    def discard_abox_generation(self, snapshot_id: str) -> Dict[str, object]:
        """Delete one failed, inactive candidate generation immediately."""
        clean_snapshot_id = str(snapshot_id or "").strip()
        if not clean_snapshot_id:
            return {
                "configured": bool(self.address),
                "status": "skipped",
                "graphStore": "typedb",
                "aboxSnapshotId": "",
                "reason": "ABox snapshot id is empty.",
            }
        if self.scoped_manifest_metadata(clean_snapshot_id):
            return self.discard_scoped_abox_manifest(clean_snapshot_id)
        active = self.active_abox_metadata()
        if str(active.get("aboxSnapshotId") or "").strip() == clean_snapshot_id:
            return {
                "configured": True,
                "status": "protected-active",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_snapshot_id,
                "reason": "The active ABox generation cannot be discarded.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "configured": True,
                "status": "driver-missing",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_snapshot_id,
                "reason": str(imported[1])[:180],
            }
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    return self.delete_box_snapshot_rows_in_batches(
                        driver,
                        imported,
                        "ABox",
                        clean_snapshot_id,
                    )
                finally:
                    self.close_driver(driver)

            result = self.with_typedb_retries(operation)
            return {
                "configured": True,
                "graphStore": "typedb",
                **dict(result or {}),
            }
        except Exception as error:  # noqa: BLE001 - cleanup state remains visible to the circuit breaker.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_snapshot_id,
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }

    def delete_box_rows_in_batches(self, driver, imported, boxes: Iterable[str]) -> Dict[str, object]:
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        batch_size = self.abox_delete_batch_size()
        deleted_batches = 0
        for box in sorted({str(item or "").strip() for item in boxes or [] if str(item or "").strip()}):
            for type_label in ["ontology-assertion", "ontology-node"]:
                while self.box_instance_exists(driver, imported, box, type_label):
                    query = self.box_delete_batch_query(box, type_label, batch_size)

                    def delete_batch():
                        with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB ABox delete batch"):
                            with driver.transaction(
                                self.database,
                                TransactionType.WRITE,
                                options=self.write_transaction_options(),
                            ) as tx:
                                tx.query(query).resolve()
                                tx.commit()

                    self.with_typedb_retries(delete_batch)
                    deleted_batches += 1
        return {
            "status": "ok",
            "boxes": sorted({str(item or "").strip() for item in boxes or [] if str(item or "").strip()}),
            "batchSize": batch_size,
            "deletedBatchCount": deleted_batches,
        }

    def abox_candidate_snapshot_ids(self) -> List[str]:
        rows = self.read_rows(
            'match $n isa ontology-node, has ontology-box "ABox", has ontology-snapshot-id $snapshotId;',
            ["snapshotId"],
            label="typedb.abox-candidate-cleanup-audit",
        )
        return sorted({str(row.get("snapshotId") or "").strip() for row in rows if str(row.get("snapshotId") or "").strip()})

    def cleanup_inactive_abox_candidates(
        self,
        driver,
        imported,
        active_snapshot_id: str = "",
    ) -> Dict[str, object]:
        """Remove incomplete candidate generations without touching the active ABox.

        Candidate writes are committed in bounded batches. If a process exits
        between node and relation batches, those partial generations must not
        accumulate indefinitely or create ambiguous endpoint matches for the
        next candidate. The active pointer is the only generation preserved.
        """
        active = str(active_snapshot_id or "").strip()
        candidates = self.abox_candidate_snapshot_ids()
        stale_candidates = [snapshot_id for snapshot_id in candidates if snapshot_id != active]
        deleted_batches = 0
        if active:
            for snapshot_id in stale_candidates:
                result = self.delete_box_snapshot_rows_in_batches(
                    driver,
                    imported,
                    "ABox",
                    snapshot_id,
                )
                deleted_batches += int(number_or_none(result.get("deletedBatchCount")) or 0)
        elif candidates:
            result = self.delete_box_rows_in_batches(driver, imported, ["ABox"])
            deleted_batches += int(number_or_none(result.get("deletedBatchCount")) or 0)

        # ABoxStaging belongs to the previous two-box rollout. It is never an
        # active generation in the pointer model and can be cleaned safely.
        legacy = self.delete_box_rows_in_batches(driver, imported, ["ABoxStaging"])
        deleted_batches += int(number_or_none(legacy.get("deletedBatchCount")) or 0)
        return {
            "status": "ok",
            "activeAboxSnapshotId": active,
            "candidateSnapshotIds": candidates,
            "removedCandidateSnapshotIds": stale_candidates,
            "deletedBatchCount": deleted_batches,
            "legacyStagingCleanup": legacy,
        }

    def drain_inactive_abox_generations_incrementally(
        self,
        driver,
        imported,
        active_snapshot_id: str = "",
        excluded_snapshot_ids: Iterable[str] = None,
    ) -> Dict[str, object]:
        """Reclaim a bounded slice of inactive ABox generations.

        Native inference and notification delivery must not wait for a full
        historical deletion. The active pointer is already verified before
        this method runs, and pending hand-offs are never cleaned here.
        """
        active = str(active_snapshot_id or "").strip()
        excluded = {
            str(item or "").strip()
            for item in excluded_snapshot_ids or []
            if str(item or "").strip()
        }
        if not active:
            return {
                "status": "skipped",
                "reason": "No active ABox generation is available for safe incremental cleanup.",
                "activeAboxSnapshotId": active,
                "deletedBatchCount": 0,
            }
        pending = self.pending_abox_activation()
        if str(pending.get("status") or "") == "pending":
            return {
                "status": "skipped",
                "reason": "ABox activation is pending native inference.",
                "activeAboxSnapshotId": active,
                "pendingAboxSnapshotId": str(pending.get("candidateAboxSnapshotId") or ""),
                "deletedBatchCount": 0,
            }
        max_batches = self.abox_incremental_cleanup_max_batches_per_save()
        if max_batches <= 0:
            return {
                "status": "skipped",
                "reason": "Incremental ABox cleanup is disabled by runtime setting.",
                "activeAboxSnapshotId": active,
                "deletedBatchCount": 0,
            }
        candidates = [
            snapshot_id
            for snapshot_id in self.abox_candidate_snapshot_ids()
            if snapshot_id != active and snapshot_id not in excluded
        ]
        markers = self.abox_projection_marker_rows()
        marker_by_snapshot: Dict[str, Dict[str, object]] = {}
        for marker in markers:
            snapshot_id = str(marker.get("aboxSnapshotId") or marker.get("snapshotId") or "").strip()
            if snapshot_id and snapshot_id in candidates:
                previous = marker_by_snapshot.get(snapshot_id)
                if previous is None or (
                    str(marker.get("updatedAt") or ""), str(marker.get("id") or "")
                ) > (
                    str(previous.get("updatedAt") or ""), str(previous.get("id") or "")
                ):
                    marker_by_snapshot[snapshot_id] = marker
        incomplete = sorted(snapshot_id for snapshot_id in candidates if snapshot_id not in marker_by_snapshot)
        completed_newest_first = sorted(
            marker_by_snapshot,
            key=lambda snapshot_id: (
                str(marker_by_snapshot[snapshot_id].get("updatedAt") or ""),
                str(marker_by_snapshot[snapshot_id].get("id") or ""),
                snapshot_id,
            ),
            reverse=True,
        )
        keep_count = self.abox_inactive_generation_keep_count()
        retained = completed_newest_first[:keep_count]
        completed_oldest_first = list(reversed(completed_newest_first[keep_count:]))
        targets = incomplete + completed_oldest_first
        deleted_batches = 0
        attempted: List[str] = []
        slices: List[Dict[str, object]] = []
        remaining_budget = max_batches
        for snapshot_id in targets:
            if remaining_budget <= 0:
                break
            cleanup = self.delete_box_snapshot_rows_in_batches(
                driver,
                imported,
                "ABox",
                snapshot_id,
                batch_size=self.abox_incremental_cleanup_batch_size(),
                max_batches=remaining_budget,
            )
            attempted.append(snapshot_id)
            slices.append(cleanup)
            deleted = int(number_or_none(cleanup.get("deletedBatchCount")) or 0)
            deleted_batches += deleted
            remaining_budget = max(0, remaining_budget - deleted)
            if str(cleanup.get("status") or "") == "partial":
                break
        remaining = [snapshot_id for snapshot_id in targets if snapshot_id not in attempted]
        if slices and str(slices[-1].get("status") or "") == "partial":
            remaining = [str(slices[-1].get("aboxSnapshotId") or "")] + remaining
        return {
            "status": "partial" if remaining else "ok",
            "activeAboxSnapshotId": active,
            "excludedSnapshotIds": sorted(excluded),
            "candidateSnapshotIds": candidates,
            "retainedInactiveSnapshotIds": retained,
            "cleanupTargetSnapshotIds": targets,
            "attemptedSnapshotIds": attempted,
            "remainingSnapshotIds": [item for item in remaining if item],
            "batchSize": self.abox_incremental_cleanup_batch_size(),
            "maxBatches": max_batches,
            "deletedBatchCount": deleted_batches,
            "slices": slices,
        }

    def prune_inactive_abox_generations(
        self,
        driver,
        imported,
        active_snapshot_id: str = "",
        keep_inactive_count: int = None,
        max_generations: int = None,
    ) -> Dict[str, object]:
        """Bound retention to completed ABox generations after activation.

        This intentionally operates on completion markers, not every physical
        ABox row. A marker is written only after the candidate rows are present
        and verified; preserving the active pointer plus recent marked
        generations makes deletion safe in the single-writer activation path.
        Unmarked interrupted candidates remain available for retry diagnostics
        and are cleared only when that exact snapshot is retried.
        """
        active = str(active_snapshot_id or "").strip()
        active_metadata: Dict[str, object] = {}
        if active.startswith("abox-manifest:") or not active:
            try:
                active_metadata = self.active_abox_metadata()
            except Exception:
                active_metadata = {}
        if str(active_metadata.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION:
            return self.prune_inactive_scoped_abox_manifests_in_driver(
                driver,
                imported,
                active_manifest_id=active or str(
                    active_metadata.get("worldviewManifestId") or active_metadata.get("aboxSnapshotId") or ""
                ),
                keep_inactive_count=keep_inactive_count,
                max_manifests=max_generations,
            )
        keep_count = (
            self.abox_inactive_generation_keep_count()
            if keep_inactive_count is None
            else max(0, min(5, int(keep_inactive_count or 0)))
        )
        max_count = (
            self.abox_inactive_generation_max_prune_per_save()
            if max_generations is None
            else max(0, min(10, int(max_generations or 0)))
        )
        markers = self.abox_projection_marker_rows()
        marker_by_snapshot: Dict[str, Dict[str, object]] = {}
        for marker in markers:
            snapshot_id = str(marker.get("aboxSnapshotId") or marker.get("snapshotId") or "").strip()
            if not snapshot_id or snapshot_id == active:
                continue
            previous = marker_by_snapshot.get(snapshot_id)
            if previous is None or (
                str(marker.get("updatedAt") or ""), str(marker.get("id") or "")
            ) > (
                str(previous.get("updatedAt") or ""), str(previous.get("id") or "")
            ):
                marker_by_snapshot[snapshot_id] = marker
        ordered_inactive = sorted(
            marker_by_snapshot,
            key=lambda snapshot_id: (
                str(marker_by_snapshot[snapshot_id].get("updatedAt") or ""),
                str(marker_by_snapshot[snapshot_id].get("id") or ""),
                snapshot_id,
            ),
            reverse=True,
        )
        retained = ordered_inactive[:keep_count]
        # Preserve the most recent completed predecessor, then drain the
        # oldest backlog first. This keeps a useful rollback/audit generation
        # while reducing the worst historical amplification immediately.
        removable = list(reversed(ordered_inactive[keep_count:]))[:max_count]
        deleted_batches = 0
        removed = []
        for snapshot_id in removable:
            result = self.delete_box_snapshot_rows_in_batches(driver, imported, "ABox", snapshot_id)
            deleted_batches += int(number_or_none(result.get("deletedBatchCount")) or 0)
            removed.append(snapshot_id)
        return {
            "status": "ok",
            "activeAboxSnapshotId": active,
            "keepInactiveGenerationCount": keep_count,
            "maxGenerationsPerSave": max_count,
            "completedInactiveCandidateCount": len(ordered_inactive),
            "retainedInactiveSnapshotIds": retained,
            "removedCandidateSnapshotIds": removed,
            "remainingInactiveCandidateCount": max(0, len(ordered_inactive) - len(removed)),
            "deletedBatchCount": deleted_batches,
        }

    def clear_boxes_in_batches(self, boxes: Iterable[str]) -> Dict[str, object]:
        clean_boxes = sorted({str(item or "").strip() for item in boxes or [] if str(item or "").strip()})
        if not clean_boxes:
            return {"status": "skipped", "boxes": [], "deletedBatchCount": 0}
        imported = self.driver_imports()
        if imported[0] is None:
            return {"status": "driver-missing", "boxes": clean_boxes, "reason": str(imported[1])[:180]}
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    return self.delete_box_rows_in_batches(driver, imported, clean_boxes)
                finally:
                    self.close_driver(driver)
            return self.with_typedb_retries(operation)
        except Exception as error:  # noqa: BLE001 - preserve the original write failure while reporting cleanup state.
            return {"status": "error", "boxes": clean_boxes, "reason": str(error)[:180]}

    def graph_for_boxes(self, graph: PortfolioOntology, boxes: Iterable[str]) -> PortfolioOntology:
        """Return a persistence-safe graph slice for the requested ontology boxes."""
        allowed = {str(item or "").strip() for item in boxes or [] if str(item or "").strip()}
        if not allowed:
            return PortfolioOntology(str(graph.portfolio_id or "typedb-empty"))
        clone = copy.deepcopy(graph)
        clone.entities = [
            item
            for item in clone.entities
            if str((item.properties or {}).get("ontologyBox") or "ABox") in allowed
        ]
        entity_ids = {str(item.entity_id or "") for item in clone.entities}
        clone.relations = [
            item
            for item in clone.relations
            if str((item.properties or {}).get("ontologyBox") or "ABox") in allowed
            and str(item.source or "") in entity_ids
            and str(item.target or "") in entity_ids
        ]
        clone.evidence = [
            item
            for item in clone.evidence
            if str((item.value or {}).get("ontologyBox") or "ABox") in allowed
        ]
        return clone

    def abox_candidate_graph(self, graph: PortfolioOntology) -> PortfolioOntology:
        """Return one immutable ABox generation ready for pointer activation.

        ABox records stay in their normal box. Their storage identity already
        includes ``snapshotId``, so a verified candidate can coexist with the
        currently active generation without rewriting thousands of records.
        """
        return self.graph_for_boxes(graph, ["ABox"])

    @staticmethod
    def abox_snapshot_id_from_graph(graph: PortfolioOntology) -> str:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        snapshot_id = str(worldview.get("aboxSnapshotId") or worldview.get("snapshotId") or "").strip()
        if snapshot_id:
            return snapshot_id
        for item in list(getattr(graph, "entities", []) or []):
            properties = dict(getattr(item, "properties", {}) or {})
            snapshot_id = str(properties.get("aboxSnapshotId") or properties.get("snapshotId") or "").strip()
            if snapshot_id:
                return snapshot_id
        return ""

    def abox_active_pointer_graph(
        self,
        graph: PortfolioOntology,
        previous_snapshot_id: str = "",
        pending_activation: bool = True,
    ) -> PortfolioOntology:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        snapshot_id = self.abox_snapshot_id_from_graph(graph)
        fingerprint = str(worldview.get("materialFingerprint") or "").strip()
        if not snapshot_id:
            return PortfolioOntology(str(graph.portfolio_id or "typedb-abox-control"))
        as_of = str(worldview.get("asOf") or worldview.get("generatedAt") or utc_now())
        target_symbols = clean_symbols_from_payload(
            worldview.get("inferenceTargetSymbols") or worldview.get("targetSymbols") or []
        )
        pointer = OntologyEntity(
            entity_id="abox-active-pointer",
            label="Active ABox generation",
            kind="abox-active-pointer",
            properties={
                "ontologyBox": "ABoxControl",
                "tboxClass": "ABoxActivePointer",
                "snapshotId": snapshot_id,
                "aboxSnapshotId": snapshot_id,
                "materialFingerprint": fingerprint,
                "projectionRunId": str(worldview.get("projectionRunId") or ""),
                "asOf": as_of,
            },
        )
        entities = [pointer]
        # Store the activation hand-off in the same atomic ABoxControl write as
        # the pointer. This is cleared only after a native InferenceBox is
        # aligned, or after an explicit rollback to the retained predecessor.
        if pending_activation and str(previous_snapshot_id or "").strip() != snapshot_id:
            entities.append(OntologyEntity(
                entity_id="abox-activation-pending",
                label="ABox activation pending native inference",
                kind="abox-activation-pending",
                properties={
                    "ontologyBox": "ABoxControl",
                    "tboxClass": "ABoxActivationPending",
                    "snapshotId": snapshot_id,
                    "aboxSnapshotId": snapshot_id,
                    "candidateAboxSnapshotId": snapshot_id,
                    "previousAboxSnapshotId": str(previous_snapshot_id or "").strip(),
                    "materialFingerprint": fingerprint,
                    "projectionRunId": str(worldview.get("projectionRunId") or ""),
                    "asOf": as_of,
                    "targetSymbols": target_symbols,
                    "activationStatus": "pending-native-inference",
                },
            ))
        return PortfolioOntology(str(graph.portfolio_id or "typedb-abox-control"), entities=entities)

    def activate_abox_generation(self, snapshot_id: str) -> Dict[str, object]:
        """Point the active ABox control record at a verified generation.

        This is used to restore the last aligned ABox when a newly activated
        generation cannot complete TypeDB native inference. It only accepts a
        generation with a complete ABox marker, so it cannot promote a partial
        write left behind by an interrupted worker.
        """
        clean_snapshot_id = str(snapshot_id or "").strip()
        if not clean_snapshot_id:
            return {
                "configured": bool(self.address),
                "status": "skipped",
                "graphStore": "typedb",
                "reason": "ABox snapshot id is empty.",
            }
        if self.scoped_manifest_metadata(clean_snapshot_id):
            return self.activate_scoped_abox_manifest(clean_snapshot_id)
        marker = next((
            item
            for item in self.abox_projection_marker_rows()
            if str(item.get("aboxSnapshotId") or item.get("snapshotId") or "").strip() == clean_snapshot_id
        ), None)
        metadata = self.abox_metadata_from_marker(marker or {}) if marker else {}
        if str(metadata.get("status") or "") != "ok":
            return {
                "configured": bool(self.address),
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_snapshot_id,
                "reason": str(metadata.get("reason") or "ABox generation is not complete."),
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], PortfolioOntology("typedb-abox-control"))
        pointer_graph = self.abox_active_pointer_graph(PortfolioOntology(
            "typedb-abox-control",
            worldview={
                "aboxSnapshotId": clean_snapshot_id,
                "materialFingerprint": str(metadata.get("materialFingerprint") or ""),
                "projectionRunId": str(metadata.get("projectionRunId") or ""),
                "asOf": str(metadata.get("asOf") or utc_now()),
            },
        ), pending_activation=False)
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.write_graph(driver, imported, pointer_graph, delete_boxes=["ABoxControl"])
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
            active = self.active_abox_metadata()
            if str(active.get("status") or "") != "ok" or str(active.get("aboxSnapshotId") or "") != clean_snapshot_id:
                return {
                    "configured": True,
                    "status": "error",
                    "graphStore": "typedb",
                    "aboxSnapshotId": clean_snapshot_id,
                    "reason": "ABox control pointer verification failed after activation.",
                    "activeAbox": active,
                }
            return {
                "configured": True,
                "status": "ok",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_snapshot_id,
                "activeAbox": active,
            }
        except Exception as error:  # noqa: BLE001 - caller preserves the diagnostic failure state.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_snapshot_id,
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }

    def finalize_abox_generation(self, active_snapshot_id: str, previous_snapshot_id: str = "") -> Dict[str, object]:
        """Complete an ABox activation after aligned native inference.

        Clearing the durable activation journal is a correctness boundary;
        deleting the prior generation is storage maintenance. Keeping those
        operations separate prevents one expensive TypeDB delete from making a
        valid realtime inference appear incomplete or retriggering alerts.
        """
        active_id = str(active_snapshot_id or "").strip()
        previous_id = str(previous_snapshot_id or "").strip()
        active_metadata = self.active_abox_metadata()
        if str(active_metadata.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION:
            return self.finalize_scoped_abox_manifest(active_id, previous_id)
        if not active_id:
            return {
                "configured": bool(self.address),
                "status": "error",
                "graphStore": "typedb",
                "activeAboxSnapshotId": active_id,
                "previousAboxSnapshotId": previous_id,
                "reason": "Active ABox snapshot id is empty.",
            }
        active = self.active_abox_metadata()
        if str(active.get("status") or "") != "ok" or str(active.get("aboxSnapshotId") or "") != active_id:
            return {
                "configured": bool(self.address),
                "status": "error",
                "graphStore": "typedb",
                "activeAboxSnapshotId": active_id,
                "previousAboxSnapshotId": previous_id,
                "reason": "Active ABox changed before retained-generation cleanup.",
            }
        control = self.activate_abox_generation(active_id)
        cleared = str(control.get("status") or "") == "ok"
        cleanup_deferred = bool(previous_id and previous_id != active_id)
        return {
            "configured": True,
            "status": "ok" if cleared else "error",
            "graphStore": "typedb",
            "activeAboxSnapshotId": active_id,
            "previousAboxSnapshotId": previous_id,
            "clearedPendingActivation": cleared,
            "cleanupDeferred": cleanup_deferred,
            "cleanup": {
                "status": "deferred" if cleanup_deferred else "not-required",
                "previousAboxSnapshotId": previous_id,
                "reason": (
                    "Inactive ABox cleanup will run in bounded maintenance slices."
                    if cleanup_deferred
                    else "No prior ABox generation requires cleanup."
                ),
            },
            "control": control,
            "reason": "" if cleared else str(control.get("reason") or "ABox activation journal clear failed."),
        }

    def inferencebox_matches_pending_abox_activation(
        self,
        inferencebox: Dict[str, object],
        candidate_snapshot_id: str,
        target_symbols: Iterable[str] = None,
    ) -> bool:
        if str((inferencebox or {}).get("status") or "") != "ok":
            return False
        if not bool((inferencebox or {}).get("nativeTypeDbReasoningUsed")):
            return False
        if (inferencebox or {}).get("generationAligned") is False:
            return False
        if str((inferencebox or {}).get("sourceAboxSnapshotId") or "").strip() != str(candidate_snapshot_id or "").strip():
            return False
        expected = set(clean_symbols_from_payload(target_symbols or []))
        actual = set(clean_symbols_from_payload((inferencebox or {}).get("targetSymbols") or []))
        return not expected or expected.issubset(actual)

    def recover_pending_abox_activation(self) -> Dict[str, object]:
        """Finish or roll back an interrupted ABox-to-InferenceBox hand-off."""
        try:
            pending = self.pending_abox_activation()
        except Exception as error:  # noqa: BLE001 - caller must block a new activation when control state is unreadable.
            return {
                "configured": bool(self.address),
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": "ABox activation journal lookup failed: " + str(error)[:180],
            }
        if str(pending.get("status") or "") == "empty":
            return {
                "configured": True,
                "status": "skipped",
                "graphStore": "typedb",
                "reason": "No pending ABox activation exists.",
            }
        if str(pending.get("status") or "") != "pending":
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "pendingActivation": pending,
                "reason": "ABox activation journal is invalid.",
            }
        candidate_id = str(pending.get("candidateAboxSnapshotId") or "").strip()
        previous_id = str(pending.get("previousAboxSnapshotId") or "").strip()
        active = self.active_abox_metadata()
        active_id = str(active.get("aboxSnapshotId") or "").strip()
        activation_status = str(pending.get("activationStatus") or "pending-native-inference")
        if str(active.get("status") or "") != "ok":
            if activation_status == "staged-native-inference" and not previous_id:
                return {
                    "configured": True,
                    "status": "staged",
                    "graphStore": "typedb",
                    "candidateAboxSnapshotId": candidate_id,
                    "previousAboxSnapshotId": previous_id,
                    "activeAboxSnapshotId": active_id,
                    "pendingActivation": pending,
                    "reason": "Initial ABox candidate is staged and awaits its first native inference activation.",
                }
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "pendingActivation": pending,
                "activeAbox": active,
                "reason": "Pending ABox activation has no complete active generation.",
            }
        if active_id != candidate_id:
            if activation_status == "staged-native-inference":
                return {
                    "configured": True,
                    "status": "staged",
                    "graphStore": "typedb",
                    "candidateAboxSnapshotId": candidate_id,
                    "previousAboxSnapshotId": previous_id,
                    "activeAboxSnapshotId": active_id,
                    "pendingActivation": pending,
                    "reason": "A complete ABox candidate is staged and awaits native inference activation.",
                }
            # The pointer already moved by a successful rollback or a later
            # repair. Rewriting that verified pointer clears a stale journal.
            control = self.activate_abox_generation(active_id)
            return {
                "configured": True,
                "status": "cleared-stale" if str(control.get("status") or "") == "ok" else "error",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "activeAboxSnapshotId": active_id,
                "previousAboxSnapshotId": previous_id,
                "control": control,
                "reason": "" if str(control.get("status") or "") == "ok" else str(control.get("reason") or "ABox control clear failed."),
            }
        try:
            inferencebox = self.inferencebox_snapshot(
                symbols=list(pending.get("targetSymbols") or []),
                limit=80,
            )
        except Exception as error:  # noqa: BLE001 - preserve the old generation when inference state cannot be verified.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "reason": "InferenceBox verification failed during ABox recovery: " + str(error)[:180],
            }
        if self.inferencebox_matches_pending_abox_activation(
            inferencebox,
            candidate_id,
            pending.get("targetSymbols") or [],
        ):
            finalization = self.finalize_abox_generation(candidate_id, previous_id)
            return {
                "configured": True,
                "status": "finalized" if str(finalization.get("status") or "") == "ok" else "error",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "inferenceBox": inferencebox,
                "finalization": finalization,
                "reason": "" if str(finalization.get("status") or "") == "ok" else str(finalization.get("reason") or "ABox finalization failed."),
            }
        if not previous_id:
            # There is no prior verified generation to restore. The candidate
            # remains visible but cannot produce investment judgement until a
            # later same-material cycle retries native inference.
            return {
                "configured": True,
                "status": "retry-required",
                "graphStore": "typedb",
                "candidateAboxSnapshotId": candidate_id,
                "inferenceBox": inferencebox,
                "reason": "Initial ABox activation is awaiting a retry of TypeDB native inference.",
            }
        rollback = self.activate_abox_generation(previous_id)
        return {
            "configured": True,
            "status": "restored" if str(rollback.get("status") or "") == "ok" else "error",
            "graphStore": "typedb",
            "candidateAboxSnapshotId": candidate_id,
            "previousAboxSnapshotId": previous_id,
            "inferenceBox": inferencebox,
            "rollback": rollback,
            "reason": "" if str(rollback.get("status") or "") == "ok" else str(rollback.get("reason") or "ABox rollback failed."),
        }

    def write_graph(
        self,
        driver,
        imported,
        graph: PortfolioOntology,
        delete_boxes: Iterable[str] = None,
    ) -> None:
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        boxes = node_boxes(graph) if delete_boxes is None else list(delete_boxes or [])
        delete_queries = self.delete_queries(boxes)
        insert_queries = self.graph_insert_queries(graph)
        if not delete_queries and not insert_queries:
            return
        transaction_query_count = (
            self.abox_write_transaction_query_count()
            if "ABox" in node_boxes(graph)
            else self.graph_write_transaction_query_count()
        )
        static_replacement_boxes = {"TBox", "RuleBox", "RuleBoxGovernance", "LanguageGovernance"}
        separate_static_replacement = bool(static_replacement_boxes.intersection(boxes))
        # Large static replacements span multiple batches. Commit their deletes
        # first so a later insert batch cannot collide with an old @unique
        # storage ID. Small ABoxControl pointer swaps remain one transaction.
        phases = [delete_queries, insert_queries] if separate_static_replacement else [delete_queries + insert_queries]
        for queries in phases:
            for offset in range(0, len(queries), transaction_query_count):
                query_batch = queries[offset: offset + transaction_query_count]

                def write_batch():
                    with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB graph write batch"):
                        with driver.transaction(
                            self.database,
                            TransactionType.WRITE,
                            options=self.write_transaction_options(),
                        ) as tx:
                            for query in query_batch:
                                tx.query(query).resolve()
                            tx.commit()

                self.with_typedb_retries(write_batch)

    def clear_inferencebox(self) -> Dict[str, object]:
        if not self.address:
            return {
                "configured": False,
                "status": "disabled",
                "graphStore": "typedb",
                "reason": "TypeDB ontology storage is not configured.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "configured": True,
                "status": "driver-missing",
                "graphStore": "typedb",
                "reason": "typedb-driver Python package is not installed: " + str(imported[1])[:160],
            }
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    with driver.transaction(self.database, TransactionType.WRITE) as tx:
                        for query in self.delete_queries(["InferenceBox"]):
                            tx.query(query).resolve()
                        tx.commit()
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
            return {
                "configured": True,
                "status": "ok",
                "graphStore": "typedb",
                "clearedBox": "InferenceBox",
            }
        except Exception as error:  # noqa: BLE001 - caller reports clear failure as inference boundary status.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }

    def schema_query(self) -> str:
        return """
define
attribute ontology-id, value string;
attribute ontology-storage-id, value string;
attribute ontology-label, value string;
attribute ontology-kind, value string;
attribute ontology-box, value string;
attribute ontology-symbol, value string;
attribute ontology-rule-id, value string;
attribute ontology-account-id, value string;
attribute ontology-snapshot-id, value string;
attribute ontology-scope-id, value string;
attribute ontology-scope-type, value string;
attribute ontology-manifest-id, value string;
attribute ontology-tbox-class, value string;
attribute ontology-relation-type, value string;
attribute ontology-updated-at, value string;
attribute ontology-json, value string;
attribute ontology-weight, value double;
attribute ontology-source-value, value string;
attribute ontology-field, value string;
attribute ontology-level-type, value string;
attribute ontology-data-scope, value string;
attribute ontology-domain-scope, value string;
attribute ontology-relation-scope, value string;
attribute ontology-group, value string;
attribute ontology-polarity, value string;
attribute ontology-evidence-role, value string;
attribute ontology-review-level, value string;
attribute ontology-data-state, value string;
attribute ontology-change-state, value string;
attribute ontology-conflict-state, value string;
attribute ontology-validation-state, value string;
attribute ontology-transition-type, value string;
attribute ontology-signal-group, value string;
attribute ontology-event-type, value string;
attribute ontology-materiality-passed, value string;
attribute ontology-materiality-state, value string;
attribute ontology-relevance-state, value string;
attribute ontology-source-trust-state, value string;
attribute ontology-value-number, value double;
attribute ontology-profit-loss-rate, value double;
attribute ontology-allow-add-on-strength, value string;
attribute ontology-trim-on-trend-break, value string;
attribute ontology-avoid-averaging-down, value string;
attribute ontology-impact-polarity, value string;
attribute ontology-needs-review, value string;
attribute ontology-read-scope, value string;
attribute ontology-pe-ratio, value double;
attribute ontology-beta, value double;
attribute ontology-current-price, value double;
attribute ontology-average-price, value double;
attribute ontology-market-value, value double;
attribute ontology-quantity, value double;
attribute ontology-sellable-quantity, value double;
attribute ontology-position-weight-pct, value double;
attribute ontology-position-account-weight-pct, value double;
attribute ontology-change-rate, value double;
attribute ontology-price-change-rate, value double;
attribute ontology-ma5, value double;
attribute ontology-ma20, value double;
attribute ontology-ma60, value double;
attribute ontology-ma5-distance, value double;
attribute ontology-ma20-distance, value double;
attribute ontology-ma60-distance, value double;
attribute ontology-ma20-slope, value double;
attribute ontology-ma60-slope, value double;
attribute ontology-trend-curve, value double;
attribute ontology-volume, value double;
attribute ontology-volume-ratio, value double;
attribute ontology-raw-volume-ratio, value double;
attribute ontology-time-adjusted-volume-ratio, value double;
attribute ontology-expected-volume-ratio-now, value double;
attribute ontology-trade-strength, value double;
attribute ontology-trading-value, value double;
attribute ontology-reported-trading-value, value double;
attribute ontology-estimated-trading-value, value double;
attribute ontology-trading-value-mismatch-pct, value double;
attribute ontology-trading-value-quality, value string;
attribute ontology-trading-value-basis, value string;
attribute ontology-bid-ask-imbalance, value double;
attribute ontology-foreign-net-volume, value double;
attribute ontology-foreign-net-amount, value double;
attribute ontology-institution-net-volume, value double;
attribute ontology-institution-net-amount, value double;
attribute ontology-individual-net-volume, value double;
attribute ontology-individual-net-amount, value double;
attribute ontology-smart-money-net-volume, value double;
attribute ontology-adr-ratio, value double;
attribute ontology-adr-price-usd, value double;
attribute ontology-adr-volume, value double;
attribute ontology-usd-krw-rate, value double;
attribute ontology-local-price-krw, value double;
attribute ontology-local-equivalent-krw, value double;
attribute ontology-leverage-factor, value double;
attribute ontology-price, value double;
attribute ontology-fair-value, value double;
attribute ontology-fair-value-price, value double;
attribute ontology-fair-value-low, value double;
attribute ontology-fair-value-base, value double;
attribute ontology-fair-value-high, value double;
attribute ontology-margin-of-safety-pct, value double;
attribute ontology-conservative-margin-of-safety-pct, value double;
attribute ontology-optimistic-margin-of-safety-pct, value double;
attribute ontology-expensive-premium-pct, value double;
attribute ontology-minimum-margin-of-safety-pct, value double;
attribute ontology-valuation-decision-eligible, value double;
attribute ontology-valuation-model-count, value double;
attribute ontology-valuation-consensus-price, value double;
attribute ontology-valuation-disagreement-pct, value double;
attribute ontology-expected-eps, value double;
attribute ontology-reported-eps, value double;
attribute ontology-estimated-eps, value double;
attribute ontology-target-per, value double;
attribute ontology-forward-pe, value double;
attribute ontology-peg-ratio, value double;
attribute ontology-dividend-yield, value double;
attribute ontology-peer-per, value double;
attribute ontology-historical-median-per, value double;
attribute ontology-lookback-days, value double;
attribute ontology-required-sample-count, value double;
attribute ontology-sample-count, value double;
attribute ontology-coverage-ratio, value double;
attribute ontology-elapsed-hours, value double;
attribute ontology-start-price, value double;
attribute ontology-price-change-pct, value double;
attribute ontology-peak-price, value double;
attribute ontology-trough-price, value double;
attribute ontology-peak-return-pct, value double;
attribute ontology-trough-return-pct, value double;
attribute ontology-drawdown-from-peak-pct, value double;
attribute ontology-rebound-from-trough-pct, value double;
attribute ontology-prior-price-change-pct, value double;
attribute ontology-recent-price-change-pct, value double;
attribute ontology-price-velocity-change-pct, value double;
attribute ontology-consecutive-decline-count, value double;
attribute ontology-consecutive-advance-count, value double;
attribute ontology-direction-change-count, value double;
attribute ontology-valid-observation-count, value double;
attribute ontology-invalid-observation-count, value double;
attribute ontology-stale-observation-count, value double;
attribute ontology-valid-observation-ratio, value double;
attribute ontology-profit-loss-rate-start, value double;
attribute ontology-profit-loss-rate-end, value double;
attribute ontology-profit-loss-rate-change-pct, value double;
attribute ontology-ma20-distance-start, value double;
attribute ontology-ma20-distance-end, value double;
attribute ontology-ma20-distance-change, value double;
attribute ontology-ma20-distance-peak, value double;
attribute ontology-ma20-distance-trough, value double;
attribute ontology-ma20-reclaim-count, value double;
attribute ontology-ma20-break-count, value double;
attribute ontology-ma20-observation-count, value double;
attribute ontology-ma60-distance-start, value double;
attribute ontology-ma60-distance-end, value double;
attribute ontology-volume-ratio-end, value double;
attribute ontology-trade-strength-end, value double;
attribute ontology-bid-ask-imbalance-end, value double;
attribute ontology-smart-money-net-latest, value double;
attribute ontology-smart-money-net-change, value double;
attribute ontology-smart-money-observation-count, value double;
attribute ontology-smart-money-distinct-observation-count, value double;
attribute ontology-individual-net-latest, value double;
attribute ontology-event-count, value double;
attribute ontology-risk-event-count, value double;
attribute ontology-support-event-count, value double;
attribute ontology-investment-strategy-profile, value string;
attribute ontology-investment-strategy-profile-label, value string;
attribute ontology-position-role, value string;
attribute ontology-target-position-role, value string;
attribute ontology-position-intent, value string;
attribute ontology-position-intent-label, value string;
attribute ontology-position-intent-description, value string;
attribute ontology-instrument-archetype, value string;
attribute ontology-instrument-archetype-label, value string;
attribute ontology-factor, value string;
attribute ontology-sensitivity-level, value string;
attribute ontology-crypto-symbol, value string;
attribute ontology-action-policy, value string;
attribute ontology-security-line-role, value string;
attribute ontology-local-symbol, value string;
attribute ontology-company-name, value string;
attribute ontology-market, value string;
attribute ontology-currency, value string;
attribute ontology-exchange, value string;
attribute ontology-adr-symbol, value string;
attribute ontology-etf-symbol, value string;
attribute ontology-underlying-symbol, value string;
attribute ontology-conversion-start-date, value string;
attribute ontology-listing-date, value string;
attribute ontology-source-url, value string;
attribute ontology-valuation-method, value string;
attribute ontology-formula, value string;
attribute ontology-eps-period, value string;
attribute ontology-multiple-period, value string;
attribute ontology-valuation-as-of, value string;
attribute ontology-valuation-freshness-status, value string;
attribute ontology-valuation-data-state-label, value string;
attribute ontology-valuation-source-type, value string;
attribute ontology-valuation-currency, value string;
attribute ontology-valuation-consensus-status, value string;
attribute ontology-per-valuation-status, value string;
attribute ontology-per-valuation-reason, value string;
attribute ontology-preferred-valuation-metric, value string;
attribute ontology-fundamental-data-source-priority, value string;
attribute ontology-window-key, value string;
attribute ontology-has-sufficient-history, value string;
attribute ontology-latest-observation-quality, value string;
attribute ontology-sequence-role, value string;
attribute ontology-observation-quality, value string;
attribute ontology-observed-at, value string;
attribute ontology-provider, value string;
attribute ontology-price-path-pattern, value string;
attribute ontology-flow-pattern, value string;
attribute ontology-event-cluster-type, value string;
attribute ontology-trend-episode-type, value string;
attribute ontology-language-registry-version, value string;
attribute ontology-language-term-id, value string;
attribute ontology-language-term-category, value string;
attribute ontology-language-term-status, value string;
attribute ontology-language-term-version, value string;
attribute ontology-language-preferred-label, value string;
attribute ontology-language-delivery-level, value string;
attribute ontology-language-delivery-level-label, value string;
attribute ontology-language-rendered-label, value string;
attribute ontology-smart-money-direction, value string;
attribute ontology-investor-flow-psychology, value string;
attribute ontology-investor-flow-evidence-role, value string;
attribute ontology-investor-flow-data-state, value string;
attribute ontology-investor-flow-review-level, value string;
attribute ontology-trend-risk-state, value string;
attribute ontology-trend-review-level, value string;
attribute ontology-trend-evidence-role, value string;
attribute ontology-trend-data-state, value string;
attribute ontology-liquidity-state, value string;
attribute ontology-liquidity-review-level, value string;
attribute ontology-liquidity-data-state, value string;
attribute ontology-source-data-state, value string;
attribute ontology-external-signal-data-state, value string;
attribute ontology-valuation-data-state, value string;
attribute ontology-valuation-input-state, value string;
attribute ontology-valuation-reliability-state, value string;

entity ontology-node @abstract,
    owns ontology-id,
    owns ontology-storage-id @unique,
    owns ontology-label,
    owns ontology-kind,
    owns ontology-box,
    owns ontology-symbol,
    owns ontology-rule-id,
    owns ontology-account-id,
    owns ontology-snapshot-id,
    owns ontology-scope-id,
    owns ontology-scope-type,
    owns ontology-manifest-id,
    owns ontology-tbox-class,
    owns ontology-updated-at,
    owns ontology-json,
    owns ontology-source-value,
    owns ontology-field,
    owns ontology-level-type,
    owns ontology-data-scope,
    owns ontology-domain-scope,
    owns ontology-relation-type,
    owns ontology-relation-scope,
    owns ontology-group,
    owns ontology-polarity,
    owns ontology-evidence-role,
    owns ontology-review-level,
    owns ontology-data-state,
    owns ontology-change-state,
    owns ontology-conflict-state,
    owns ontology-validation-state,
    owns ontology-event-type,
    owns ontology-materiality-passed,
    owns ontology-materiality-state,
    owns ontology-relevance-state,
    owns ontology-source-trust-state,
    owns ontology-value-number,
    owns ontology-profit-loss-rate,
    owns ontology-allow-add-on-strength,
    owns ontology-trim-on-trend-break,
    owns ontology-avoid-averaging-down,
    owns ontology-impact-polarity,
    owns ontology-needs-review,
    owns ontology-read-scope,
    owns ontology-pe-ratio,
    owns ontology-beta,
    owns ontology-current-price,
    owns ontology-average-price,
    owns ontology-market-value,
    owns ontology-quantity,
    owns ontology-sellable-quantity,
    owns ontology-position-weight-pct,
    owns ontology-position-account-weight-pct,
    owns ontology-change-rate,
    owns ontology-price-change-rate,
    owns ontology-ma5,
    owns ontology-ma20,
    owns ontology-ma60,
    owns ontology-ma5-distance,
    owns ontology-ma20-distance,
    owns ontology-ma60-distance,
    owns ontology-ma20-slope,
    owns ontology-ma60-slope,
    owns ontology-trend-curve,
    owns ontology-volume,
    owns ontology-volume-ratio,
    owns ontology-raw-volume-ratio,
    owns ontology-time-adjusted-volume-ratio,
    owns ontology-expected-volume-ratio-now,
    owns ontology-trade-strength,
    owns ontology-trading-value,
    owns ontology-reported-trading-value,
    owns ontology-estimated-trading-value,
    owns ontology-trading-value-mismatch-pct,
    owns ontology-trading-value-quality,
    owns ontology-trading-value-basis,
    owns ontology-bid-ask-imbalance,
    owns ontology-foreign-net-volume,
    owns ontology-foreign-net-amount,
    owns ontology-institution-net-volume,
    owns ontology-institution-net-amount,
    owns ontology-individual-net-volume,
    owns ontology-individual-net-amount,
    owns ontology-smart-money-net-volume,
    owns ontology-adr-ratio,
    owns ontology-adr-price-usd,
    owns ontology-adr-volume,
    owns ontology-usd-krw-rate,
    owns ontology-local-price-krw,
    owns ontology-local-equivalent-krw,
    owns ontology-leverage-factor,
    owns ontology-price,
    owns ontology-fair-value,
    owns ontology-fair-value-price,
    owns ontology-fair-value-low,
    owns ontology-fair-value-base,
    owns ontology-fair-value-high,
    owns ontology-margin-of-safety-pct,
    owns ontology-conservative-margin-of-safety-pct,
    owns ontology-optimistic-margin-of-safety-pct,
    owns ontology-expensive-premium-pct,
    owns ontology-minimum-margin-of-safety-pct,
    owns ontology-valuation-decision-eligible,
    owns ontology-valuation-model-count,
    owns ontology-valuation-consensus-price,
    owns ontology-valuation-disagreement-pct,
    owns ontology-expected-eps,
    owns ontology-reported-eps,
    owns ontology-estimated-eps,
    owns ontology-target-per,
    owns ontology-forward-pe,
    owns ontology-peg-ratio,
    owns ontology-dividend-yield,
    owns ontology-peer-per,
    owns ontology-historical-median-per,
    owns ontology-lookback-days,
    owns ontology-required-sample-count,
    owns ontology-sample-count,
    owns ontology-coverage-ratio,
    owns ontology-elapsed-hours,
    owns ontology-start-price,
    owns ontology-price-change-pct,
    owns ontology-peak-price,
    owns ontology-trough-price,
    owns ontology-peak-return-pct,
    owns ontology-trough-return-pct,
    owns ontology-drawdown-from-peak-pct,
    owns ontology-rebound-from-trough-pct,
    owns ontology-prior-price-change-pct,
    owns ontology-recent-price-change-pct,
    owns ontology-price-velocity-change-pct,
    owns ontology-consecutive-decline-count,
    owns ontology-consecutive-advance-count,
    owns ontology-direction-change-count,
    owns ontology-valid-observation-count,
    owns ontology-invalid-observation-count,
    owns ontology-stale-observation-count,
    owns ontology-valid-observation-ratio,
    owns ontology-profit-loss-rate-start,
    owns ontology-profit-loss-rate-end,
    owns ontology-profit-loss-rate-change-pct,
    owns ontology-ma20-distance-start,
    owns ontology-ma20-distance-end,
    owns ontology-ma20-distance-change,
    owns ontology-ma20-distance-peak,
    owns ontology-ma20-distance-trough,
    owns ontology-ma20-reclaim-count,
    owns ontology-ma20-break-count,
    owns ontology-ma20-observation-count,
    owns ontology-ma60-distance-start,
    owns ontology-ma60-distance-end,
    owns ontology-volume-ratio-end,
    owns ontology-trade-strength-end,
    owns ontology-bid-ask-imbalance-end,
    owns ontology-smart-money-net-latest,
    owns ontology-smart-money-net-change,
    owns ontology-smart-money-observation-count,
    owns ontology-smart-money-distinct-observation-count,
    owns ontology-individual-net-latest,
    owns ontology-event-count,
    owns ontology-risk-event-count,
    owns ontology-support-event-count,
    owns ontology-investment-strategy-profile,
    owns ontology-investment-strategy-profile-label,
    owns ontology-position-role,
    owns ontology-target-position-role,
    owns ontology-position-intent,
    owns ontology-position-intent-label,
    owns ontology-position-intent-description,
    owns ontology-instrument-archetype,
    owns ontology-instrument-archetype-label,
    owns ontology-factor,
    owns ontology-sensitivity-level,
    owns ontology-crypto-symbol,
    owns ontology-action-policy,
    owns ontology-security-line-role,
    owns ontology-local-symbol,
    owns ontology-company-name,
    owns ontology-market,
    owns ontology-currency,
    owns ontology-exchange,
    owns ontology-adr-symbol,
    owns ontology-etf-symbol,
    owns ontology-underlying-symbol,
    owns ontology-conversion-start-date,
    owns ontology-listing-date,
    owns ontology-source-url,
    owns ontology-valuation-method,
    owns ontology-formula,
    owns ontology-eps-period,
    owns ontology-multiple-period,
    owns ontology-valuation-as-of,
    owns ontology-valuation-freshness-status,
    owns ontology-valuation-data-state-label,
    owns ontology-valuation-source-type,
    owns ontology-valuation-currency,
    owns ontology-valuation-consensus-status,
    owns ontology-per-valuation-status,
    owns ontology-per-valuation-reason,
    owns ontology-preferred-valuation-metric,
    owns ontology-fundamental-data-source-priority,
    owns ontology-window-key,
    owns ontology-has-sufficient-history,
    owns ontology-latest-observation-quality,
    owns ontology-sequence-role,
    owns ontology-observation-quality,
    owns ontology-observed-at,
    owns ontology-provider,
    owns ontology-price-path-pattern,
    owns ontology-flow-pattern,
    owns ontology-event-cluster-type,
    owns ontology-trend-episode-type,
    owns ontology-language-registry-version,
    owns ontology-language-term-id,
    owns ontology-language-term-category,
    owns ontology-language-term-status,
    owns ontology-language-term-version,
    owns ontology-language-preferred-label,
    owns ontology-language-delivery-level,
    owns ontology-language-delivery-level-label,
    owns ontology-language-rendered-label,
    owns ontology-smart-money-direction,
    owns ontology-investor-flow-psychology,
    owns ontology-investor-flow-evidence-role,
    owns ontology-investor-flow-data-state,
    owns ontology-investor-flow-review-level,
    owns ontology-trend-risk-state,
    owns ontology-trend-review-level,
    owns ontology-trend-evidence-role,
    owns ontology-trend-data-state,
    owns ontology-liquidity-state,
    owns ontology-liquidity-review-level,
    owns ontology-liquidity-data-state,
    owns ontology-source-data-state,
    owns ontology-external-signal-data-state,
    owns ontology-valuation-data-state,
    owns ontology-valuation-input-state,
    owns ontology-valuation-reliability-state,
    plays ontology-assertion:source,
    plays ontology-assertion:target;

entity ontology-entity, sub ontology-node;
entity ontology-evidence, sub ontology-node;
entity ontology-belief, sub ontology-node;
entity ontology-opinion, sub ontology-node;
entity ontology-reasoning-card, sub ontology-node;

relation ontology-assertion,
    relates source,
    relates target,
    owns ontology-id,
    owns ontology-storage-id @unique,
    owns ontology-relation-type,
    owns ontology-box,
    owns ontology-symbol,
    owns ontology-rule-id,
    owns ontology-account-id,
    owns ontology-snapshot-id,
    owns ontology-scope-id,
    owns ontology-scope-type,
    owns ontology-manifest-id,
    owns ontology-tbox-class,
    owns ontology-updated-at,
    owns ontology-json,
    owns ontology-weight,
    owns ontology-field,
    owns ontology-polarity,
    owns ontology-evidence-role,
    owns ontology-review-level,
    owns ontology-data-state,
    owns ontology-change-state,
    owns ontology-conflict-state,
    owns ontology-validation-state,
    owns ontology-transition-type,
    owns ontology-signal-group,
    owns ontology-materiality-passed,
    owns ontology-materiality-state,
    owns ontology-relevance-state,
    owns ontology-source-trust-state;
""".strip()

    def delete_queries(self, boxes: Iterable[str]) -> List[str]:
        queries = []
        for box in sorted(set(str(item or "").strip() for item in boxes if str(item or "").strip())):
            queries.append(
                "match $r isa ontology-assertion, has ontology-box " + typedb_string(box) + "; delete $r;"
            )
            queries.append(
                "match $n isa ontology-node, has ontology-box " + typedb_string(box) + "; delete $n;"
            )
        return queries

    def insert_queries(self, graph: PortfolioOntology) -> List[str]:
        queries: List[str] = []
        updated_at = utc_now()
        node_rows, relation_rows = self.graph_persistence_rows(graph)
        for row in node_rows:
            queries.append(self.node_insert_query(row, updated_at))
        for row in relation_rows:
            queries.append(self.relation_insert_query(row, updated_at))
        return [item for item in queries if item]

    def graph_persistence_rows(self, graph: PortfolioOntology) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
        node_rows = self.node_rows(graph)
        node_rows_by_id = {
            str(row.get("id") or ""): row
            for row in node_rows
            if str(row.get("id") or "")
        }
        node_ids = set(node_rows_by_id)
        relation_rows = [
            {
                **row,
                "sourceStorageId": ontology_storage_id(
                    node_rows_by_id[str(row.get("source") or "")],
                    row.get("source"),
                    "node",
                ),
                "targetStorageId": ontology_storage_id(
                    node_rows_by_id[str(row.get("target") or "")],
                    row.get("target"),
                    "node",
                ),
            }
            for row in self.rows_for_relations(graph) + self.support_relation_rows(graph)
            if str(row.get("source") or "") in node_ids
            and str(row.get("target") or "") in node_ids
        ]
        return node_rows, relation_rows

    def abox_projection_marker_graph(
        self,
        graph: PortfolioOntology,
        expected_entity_count: int,
        expected_relation_count: int,
        box: str = "ABox",
    ) -> PortfolioOntology:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        snapshot_id = str(worldview.get("aboxSnapshotId") or worldview.get("snapshotId") or "").strip()
        fingerprint = str(worldview.get("materialFingerprint") or "").strip()
        if not snapshot_id or not fingerprint:
            return PortfolioOntology(str(graph.portfolio_id or "typedb-abox-marker"))
        as_of = str(worldview.get("asOf") or worldview.get("generatedAt") or utc_now())
        marker = OntologyEntity(
            entity_id="abox-projection-marker:" + snapshot_id,
            label="ABox projection completion",
            kind="abox-projection-marker",
            properties={
                "ontologyBox": str(box or "ABox"),
                "tboxClass": "ABoxProjectionMarker",
                "snapshotId": snapshot_id,
                "aboxSnapshotId": snapshot_id,
                "materialFingerprint": fingerprint,
                "projectionRunId": str(worldview.get("projectionRunId") or ""),
                "asOf": as_of,
                "expectedAboxEntityCount": int(expected_entity_count),
                "expectedAboxRelationCount": int(expected_relation_count),
                "projectionStatus": "complete",
            },
        )
        return PortfolioOntology(str(graph.portfolio_id or "typedb-abox-marker"), entities=[marker])

    def verify_abox_projection(
        self,
        graph: PortfolioOntology,
        expected_entity_count: int,
        expected_relation_count: int,
        box: str = "ABox",
    ) -> Dict[str, object]:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        snapshot_id = str(worldview.get("aboxSnapshotId") or worldview.get("snapshotId") or "").strip()
        if not snapshot_id:
            return {"status": "skipped", "reason": "ABox material identity is unavailable."}
        actual = self.box_snapshot_row_counts(str(box or "ABox"), snapshot_id)
        complete = (
            actual["entityCount"] == int(expected_entity_count) + 1
            and actual["relationCount"] == int(expected_relation_count)
        )
        return {
            "status": "ok" if complete else "incomplete",
            "ontologyBox": str(box or "ABox"),
            "aboxSnapshotId": snapshot_id,
            "expectedEntityCount": int(expected_entity_count),
            "expectedRelationCount": int(expected_relation_count),
            "actualEntityCount": actual["entityCount"] - 1 if actual["entityCount"] else 0,
            "actualRelationCount": actual["relationCount"],
            "completionMarkerCount": 1 if actual["entityCount"] else 0,
        }

    def graph_insert_queries(self, graph: PortfolioOntology) -> List[str]:
        updated_at = utc_now()
        node_rows, relation_rows = self.graph_persistence_rows(graph)
        settings = runtime_settings()
        node_batch_size = int(number_or_none(settings.get("typedbABoxNodeBatchSize")) or 100)
        relation_batch_size = self.abox_relation_batch_size(settings)
        max_query_bytes = self.write_query_max_bytes(settings)
        return [
            *self.batched_node_insert_queries(node_rows, updated_at, node_batch_size, max_query_bytes),
            *self.batched_relation_insert_queries(relation_rows, updated_at, relation_batch_size, max_query_bytes),
        ]

    def write_query_max_bytes(self, settings: Dict[str, object] = None) -> int:
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbWriteMaxQueryBytes")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 192000
        return max(4096, min(256000, int(parsed)))

    @staticmethod
    def query_byte_size(query: str) -> int:
        return len(str(query or "").encode("utf-8"))

    def node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        rows = []
        rows.extend({**row, "nodeType": "ontology-entity"} for row in self.rows_for_entities(graph))
        rows.extend(self.evidence_node_rows(graph))
        rows.extend(self.belief_node_rows(graph))
        rows.extend(self.opinion_node_rows(graph))
        rows.extend(self.reasoning_card_node_rows(graph))
        return [row for row in rows if str(row.get("id") or "")]

    def evidence_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-evidence",
                "label": row.get("summary") or row.get("id"),
                "kind": "evidence:" + str(row.get("kind") or "evidence"),
                "symbol": "",
                "ruleId": "",
                "tboxClass": "Evidence",
                "propertiesJson": row.get("valueJson") or "{}",
            }
            for row in self.rows_for_evidence(graph)
        ]

    def belief_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-belief",
                "label": row.get("label") or row.get("id"),
                "kind": "belief",
                "symbol": symbol_from_subject(row.get("subject")),
                "ruleId": rule_id_from_value(row.get("id")),
                "tboxClass": "Belief",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
            for row in self.rows_for_beliefs(graph)
        ]

    def opinion_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-opinion",
                "label": str(row.get("symbol") or row.get("id")),
                "kind": "opinion",
                "ruleId": "",
                "tboxClass": "InvestmentOpinion",
                "propertiesJson": row.get("payloadJson") or "{}",
            }
            for row in self.rows_for_opinions(graph)
        ]

    def reasoning_card_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-reasoning-card",
                "label": row.get("companyName") or row.get("symbol") or row.get("id"),
                "kind": "reasoning-card",
                "ruleId": "",
                "tboxClass": "ReasoningCard",
                "propertiesJson": row.get("payloadJson") or "{}",
            }
            for row in self.rows_for_reasoning_cards(graph)
        ]

    def support_relation_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for row in self.rows_for_evidence(graph):
            rows.append({
                "source": row.get("subject"),
                "target": row.get("id"),
                "type": "HAS_EVIDENCE",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "scopeId": row.get("scopeId") or "",
                "scopeType": row.get("scopeType") or "",
                "manifestId": row.get("manifestId") or "",
                "scopeGenerationId": row.get("scopeGenerationId") or row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        for row in self.rows_for_beliefs(graph):
            rows.append({
                "source": row.get("subject"),
                "target": row.get("id"),
                "type": "HAS_BELIEF",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "scopeId": row.get("scopeId") or "",
                "scopeType": row.get("scopeType") or "",
                "manifestId": row.get("manifestId") or "",
                "scopeGenerationId": row.get("scopeGenerationId") or row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": row.get("ruleId") or rule_id_from_value(row.get("id")),
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        for row in self.rows_for_opinions(graph):
            rows.append({
                "source": "stock:" + str(row.get("symbol") or "").upper(),
                "target": row.get("id"),
                "type": "HAS_OPINION",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        for row in self.rows_for_reasoning_cards(graph):
            rows.append({
                "source": "stock:" + str(row.get("symbol") or "").upper(),
                "target": row.get("id"),
                "type": "HAS_REASONING_CARD",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        return [row for row in rows if row.get("source") and row.get("target")]

    def node_insert_query(self, row: Dict[str, object], updated_at: str) -> str:
        return "insert " + self.node_insert_clause(row, updated_at, "$n") + ";"

    def node_insert_clause(self, row: Dict[str, object], updated_at: str, variable: str) -> str:
        node_type = str(row.get("nodeType") or "ontology-entity")
        node_id = str(row.get("id") or "")
        return (
            str(variable or "$n") + " isa " + node_type
            + ", has ontology-id " + typedb_string(node_id)
            + ", has ontology-storage-id " + typedb_string(ontology_storage_id(row, node_id, "node"))
            + typeql_has("ontology-label", row.get("label"))
            + typeql_has("ontology-kind", row.get("kind"))
            + typeql_has("ontology-box", row.get("ontologyBox") or "ABox")
            + typeql_has("ontology-symbol", row.get("symbol"))
            + typeql_has("ontology-rule-id", row.get("ruleId"))
            + typeql_has("ontology-account-id", row.get("accountId"))
            + typeql_has("ontology-snapshot-id", row.get("snapshotId") or row.get("aboxSnapshotId"))
            + typeql_has("ontology-scope-id", row.get("scopeId"))
            + typeql_has("ontology-scope-type", row.get("scopeType"))
            + typeql_has("ontology-manifest-id", row.get("manifestId"))
            + typeql_has("ontology-tbox-class", row.get("tboxClass"))
            + typeql_has("ontology-relation-type", row.get("relationTypeName"))
            + typeql_has("ontology-updated-at", updated_at)
            + typeql_has("ontology-json", row.get("propertiesJson"))
            + typeql_has("ontology-source-value", row.get("sourceValue"))
            + typeql_has("ontology-field", row.get("field"))
            + typeql_has("ontology-level-type", row.get("levelType"))
            + typeql_has("ontology-data-scope", row.get("dataScope"))
            + typeql_has("ontology-domain-scope", row.get("domainScope"))
            + typeql_has("ontology-relation-scope", row.get("relationScope"))
            + typeql_has("ontology-group", row.get("group"))
            + typeql_has("ontology-polarity", row.get("polarity"))
            + typeql_has("ontology-evidence-role", row.get("evidenceRole"))
            + typeql_has("ontology-review-level", row.get("reviewLevel"))
            + typeql_has("ontology-data-state", row.get("dataState"))
            + typeql_has("ontology-change-state", row.get("changeState"))
            + typeql_has("ontology-conflict-state", row.get("conflictState"))
            + typeql_has("ontology-validation-state", row.get("validationState"))
            + typeql_has("ontology-event-type", row.get("eventType"))
            + typeql_has_bool_string("ontology-materiality-passed", row.get("materialityPassed"))
            + typeql_has("ontology-value-number", row.get("valueNumber"), numeric=True)
            + typeql_has("ontology-profit-loss-rate", row.get("profitLossRate"), numeric=True)
            + typeql_has_bool_string("ontology-allow-add-on-strength", row.get("allowAddOnStrength"))
            + typeql_has_bool_string("ontology-trim-on-trend-break", row.get("trimOnTrendBreak"))
            + typeql_has_bool_string("ontology-avoid-averaging-down", row.get("avoidAveragingDown"))
            + typeql_has("ontology-impact-polarity", row.get("impactPolarity"))
            + typeql_has_bool_string("ontology-needs-review", row.get("needsReview"))
            + typeql_has("ontology-read-scope", row.get("readScope"))
            + typeql_has("ontology-pe-ratio", row.get("peRatio"), numeric=True)
            + typeql_has("ontology-beta", row.get("beta"), numeric=True)
            + "".join(
                typeql_has(attribute, row.get(field), numeric=True)
                for field, attribute in TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.items()
            )
            + "".join(
                typeql_has(attribute, row.get(field))
                for field, attribute in TYPEDB_PROMOTED_TEXT_ATTRIBUTES.items()
            )
        )

    def relation_insert_query(self, row: Dict[str, object], updated_at: str) -> str:
        return (
            "match "
            + self.relation_match_clause(row, "$source", "$target")
            + "insert "
            + self.relation_insert_clause(row, updated_at, "$r", "$source", "$target")
            + ";"
        )

    def relation_match_clause(self, row: Dict[str, object], source_variable: str, target_variable: str) -> str:
        source_storage_id = str(row.get("sourceStorageId") or "").strip()
        target_storage_id = str(row.get("targetStorageId") or "").strip()
        if source_storage_id and target_storage_id:
            return (
                str(source_variable or "$source") + " isa ontology-node, has ontology-storage-id "
                + typedb_string(source_storage_id) + "; "
                + str(target_variable or "$target") + " isa ontology-node, has ontology-storage-id "
                + typedb_string(target_storage_id) + "; "
            )
        snapshot_id = row.get("snapshotId") or row.get("aboxSnapshotId")
        ontology_box = str(row.get("ontologyBox") or "ABox").strip() or "ABox"
        # A live ABox generation can contain the same public ontology ID as
        # its predecessor while activation is still pending. Match its
        # endpoints by the generation-scoped unique storage identity instead
        # of scanning all nodes with the public ID and snapshot attribute.
        # Static TBox/RuleBox relations may cross boxes, so they retain the
        # public-ID lookup below.
        if ontology_box == "ABox" and str(snapshot_id or "").strip():
            return (
                str(source_variable or "$source") + " isa ontology-node, has ontology-storage-id "
                + typedb_string(ontology_storage_id(row, row.get("source"), "node")) + "; "
                + str(target_variable or "$target") + " isa ontology-node, has ontology-storage-id "
                + typedb_string(ontology_storage_id(row, row.get("target"), "node")) + "; "
            )
        snapshot_match = typeql_has("ontology-snapshot-id", snapshot_id)
        return (
            str(source_variable or "$source") + " isa ontology-node, has ontology-id " + typedb_string(row.get("source"))
            + snapshot_match + "; "
            + str(target_variable or "$target") + " isa ontology-node, has ontology-id " + typedb_string(row.get("target"))
            + snapshot_match + "; "
        )

    def relation_insert_clause(
        self,
        row: Dict[str, object],
        updated_at: str,
        relation_variable: str,
        source_variable: str,
        target_variable: str,
    ) -> str:
        relation_id = relation_row_id(row)
        return (
            str(relation_variable or "$r")
            + " isa ontology-assertion, links (source: "
            + str(source_variable or "$source")
            + ", target: "
            + str(target_variable or "$target")
            + ")"
            + ", has ontology-id " + typedb_string(relation_id)
            + ", has ontology-storage-id " + typedb_string(ontology_storage_id(row, relation_id, "relation"))
            + typeql_has("ontology-relation-type", row.get("type"))
            + typeql_has("ontology-box", row.get("ontologyBox") or "ABox")
            + typeql_has("ontology-symbol", row.get("symbol"))
            + typeql_has("ontology-rule-id", row.get("ruleId"))
            + typeql_has("ontology-account-id", row.get("accountId"))
            + typeql_has("ontology-snapshot-id", row.get("snapshotId") or row.get("aboxSnapshotId"))
            + typeql_has("ontology-scope-id", row.get("scopeId"))
            + typeql_has("ontology-scope-type", row.get("scopeType"))
            + typeql_has("ontology-manifest-id", row.get("manifestId"))
            + typeql_has("ontology-tbox-class", row.get("tboxClass"))
            + typeql_has("ontology-updated-at", updated_at)
            + typeql_has("ontology-json", row.get("propertiesJson"))
            + typeql_has("ontology-weight", row.get("weight"), numeric=True)
            + typeql_has("ontology-field", row.get("field"))
            + typeql_has("ontology-polarity", row.get("polarity"))
            + typeql_has("ontology-evidence-role", row.get("evidenceRole"))
            + typeql_has("ontology-review-level", row.get("reviewLevel"))
            + typeql_has("ontology-data-state", row.get("dataState"))
            + typeql_has("ontology-change-state", row.get("changeState"))
            + typeql_has("ontology-conflict-state", row.get("conflictState"))
            + typeql_has("ontology-validation-state", row.get("validationState"))
            + typeql_has("ontology-transition-type", row.get("transitionType"))
            + typeql_has("ontology-signal-group", row.get("signalGroup"))
            + typeql_has_bool_string("ontology-materiality-passed", row.get("materialityPassed"))
            + typeql_has("ontology-materiality-state", row.get("materialityState"))
            + typeql_has("ontology-relevance-state", row.get("relevanceState"))
            + typeql_has("ontology-source-trust-state", row.get("sourceTrustState"))
        )

    def batched_node_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 40,
        max_query_bytes: int = 0,
    ) -> List[str]:
        items = [row for row in rows or [] if str((row or {}).get("id") or "").strip()]
        maximum_count = max(1, int(batch_size or 40))
        maximum_bytes = max(0, int(max_query_bytes or 0))
        queries: List[str] = []
        clauses: List[str] = []
        query_bytes = self.query_byte_size("insert ")
        for row in items:
            clause = self.node_insert_clause(row, updated_at, "$n" + str(len(clauses))) + ";"
            clause_bytes = self.query_byte_size(clause)
            candidate_bytes = query_bytes + clause_bytes + (1 if clauses else 0)
            if clauses and (len(clauses) >= maximum_count or (maximum_bytes and candidate_bytes > maximum_bytes)):
                queries.append("insert " + " ".join(clauses))
                clauses = []
                query_bytes = self.query_byte_size("insert ")
                clause = self.node_insert_clause(row, updated_at, "$n0") + ";"
                clause_bytes = self.query_byte_size(clause)
            clauses.append(clause)
            query_bytes += clause_bytes + (1 if len(clauses) > 1 else 0)
        if clauses:
            queries.append("insert " + " ".join(clauses))
        return queries

    def node_batch_insert_query(self, rows: Iterable[Dict[str, object]], updated_at: str) -> str:
        inserts = [
            self.node_insert_clause(row, updated_at, "$n" + str(index)) + ";"
            for index, row in enumerate(rows or [])
        ]
        return "insert " + " ".join(inserts)

    def batched_relation_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 25,
        max_query_bytes: int = 0,
    ) -> List[str]:
        items = [
            row for row in rows or []
            if str((row or {}).get("source") or "").strip() and str((row or {}).get("target") or "").strip()
        ]
        maximum_count = max(1, int(batch_size or 25))
        maximum_bytes = max(0, int(max_query_bytes or 0))
        queries: List[str] = []
        matches: List[str] = []
        inserts: List[str] = []
        query_bytes = self.query_byte_size("match ") + self.query_byte_size(" insert ")
        for row in items:
            index = len(matches)
            source_var = "$source" + str(index)
            target_var = "$target" + str(index)
            relation_var = "$r" + str(index)
            match = self.relation_match_clause(row, source_var, target_var)
            insert = self.relation_insert_clause(row, updated_at, relation_var, source_var, target_var) + ";"
            candidate_bytes = query_bytes + self.query_byte_size(match) + self.query_byte_size(insert) + (2 if matches else 0)
            if matches and (len(matches) >= maximum_count or (maximum_bytes and candidate_bytes > maximum_bytes)):
                queries.append("match " + " ".join(matches) + " insert " + " ".join(inserts))
                matches = []
                inserts = []
                query_bytes = self.query_byte_size("match ") + self.query_byte_size(" insert ")
                source_var = "$source0"
                target_var = "$target0"
                relation_var = "$r0"
                match = self.relation_match_clause(row, source_var, target_var)
                insert = self.relation_insert_clause(row, updated_at, relation_var, source_var, target_var) + ";"
            matches.append(match)
            inserts.append(insert)
            query_bytes += self.query_byte_size(match) + self.query_byte_size(insert) + (2 if len(matches) > 1 else 0)
        if matches:
            queries.append("match " + " ".join(matches) + " insert " + " ".join(inserts))
        return queries

    def relation_batch_insert_query(self, rows: Iterable[Dict[str, object]], updated_at: str) -> str:
        matches = []
        inserts = []
        for index, row in enumerate(rows or []):
            source_var = "$source" + str(index)
            target_var = "$target" + str(index)
            relation_var = "$r" + str(index)
            matches.append(self.relation_match_clause(row, source_var, target_var))
            inserts.append(self.relation_insert_clause(row, updated_at, relation_var, source_var, target_var) + ";")
        return "match " + " ".join(matches) + " insert " + " ".join(inserts)

    def inferencebox_insert_queries(
        self,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
        updated_at: str,
    ) -> List[str]:
        settings = runtime_settings()
        node_batch_size = int(number_or_none(settings.get("typedbInferenceBoxNodeBatchSize")) or 25)
        relation_batch_size = self.inferencebox_relation_batch_size(settings)
        max_query_bytes = self.write_query_max_bytes(settings)
        return [
            *self.batched_node_insert_queries(node_rows, updated_at, node_batch_size, max_query_bytes),
            *self.batched_relation_insert_queries(relation_rows, updated_at, relation_batch_size, max_query_bytes),
        ]

    def seed_graph_preflight(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
    ) -> Dict[str, object]:
        """Check a persisted ontology seed before rewriting immutable boxes.

        TBox, RuleBox, and language governance are large enough that replacing
        them on every service restart contends with live ABox projection. The
        check intentionally uses TypeDB count reductions for box completeness
        and reads RuleBox only once to compare its canonical rules hash.
        """
        expected_entities = graph_box_entity_counts(graph)
        expected_relations = graph_box_relation_counts(graph)
        expected_boxes = sorted(set(expected_entities) | set(expected_relations))
        expected_rulebox = rulebox_runtime_metadata(rules_payload)
        expected_tbox = default_tbox_metadata()
        actual_counts: Dict[str, Dict[str, int]] = {}
        try:
            actual_counts = {
                box: self.box_row_counts(box)
                for box in expected_boxes
            }
            counts_match = all(
                actual_counts.get(box, {}).get("entityCount") == int(expected_entities.get(box, 0))
                and actual_counts.get(box, {}).get("relationCount") == int(expected_relations.get(box, 0))
                for box in expected_boxes
            )
            tbox_rows = self.read_entity_rows(["TBox"], limit=1)
            tbox_properties = json_object((tbox_rows[0] if tbox_rows else {}).get("propertiesJson"))
            tbox_matches = bool(tbox_rows) and (
                str(tbox_properties.get("tboxFingerprint") or tbox_properties.get("fingerprint") or "")
                == str(expected_tbox.get("fingerprint") or "")
            ) and (
                str(tbox_properties.get("tboxVersion") or tbox_properties.get("version") or "")
                == str(expected_tbox.get("version") or "")
            )
            rulebox = self.rulebox_snapshot()
            rulebox_matches = (
                str(rulebox.get("status") or "") == "ok"
                and str(rulebox.get("ruleboxRulesHash") or "") == expected_rulebox["ruleboxRulesHash"]
                and int(number_or_none(rulebox.get("ruleCount")) or 0) == expected_rulebox["ruleboxRuleCount"]
                and int(number_or_none(rulebox.get("conditionCount")) or 0) == expected_rulebox["ruleboxConditionCount"]
                and int(number_or_none(rulebox.get("derivationCount")) or 0) == expected_rulebox["ruleboxDerivationCount"]
            )
            language_registry_nodes = [
                item
                for item in graph.entities
                if str(item.kind or "") == "language-registry-version"
            ]
            expected_registry = language_registry_nodes[0] if language_registry_nodes else None
            stored_registry_rows = self.read_entity_rows_by_ids(
                [expected_registry.entity_id] if expected_registry else [],
                ["LanguageGovernance"],
            )
            stored_registry_properties = json_object(
                (stored_registry_rows[0] if stored_registry_rows else {}).get("propertiesJson")
            )
            expected_registry_version = str(
                (expected_registry.properties if expected_registry else {}).get("registryVersion") or ""
            )
            language_registry_matches = (
                expected_registry is None
                or (
                    bool(stored_registry_rows)
                    and str(stored_registry_properties.get("registryVersion") or "") == expected_registry_version
                )
            )
        except Exception as error:  # noqa: BLE001 - a missing/legacy schema must be seeded, not trusted.
            return {
                "ready": False,
                "status": "unavailable",
                "reason": str(error)[:180],
                "expectedBoxCounts": {
                    box: {
                        "entityCount": int(expected_entities.get(box, 0)),
                        "relationCount": int(expected_relations.get(box, 0)),
                    }
                    for box in expected_boxes
                },
            }
        ready = bool(counts_match and tbox_matches and rulebox_matches and language_registry_matches)
        return {
            "ready": ready,
            "status": "current" if ready else "stale",
            "expectedBoxCounts": {
                box: {
                    "entityCount": int(expected_entities.get(box, 0)),
                    "relationCount": int(expected_relations.get(box, 0)),
                }
                for box in expected_boxes
            },
            "actualBoxCounts": actual_counts,
            "tboxMatches": tbox_matches,
            "ruleboxMatches": rulebox_matches,
            "languageRegistryMatches": language_registry_matches,
        }

    def seed_relation_repair_eligible(self, preflight: Dict[str, object]) -> bool:
        """Return whether a stale seed can be repaired without replacing nodes.

        A TypeDB process can be interrupted after static nodes are committed but
        before every relation batch is written. Replacing all boxes in that
        state is slow and competes with live ABox projection. A relation-only
        repair is safe when every expected static node is already present and
        no box has more relations than the immutable seed expects.
        """
        if not isinstance(preflight, dict) or preflight.get("status") != "stale":
            return False
        expected = preflight.get("expectedBoxCounts") if isinstance(preflight.get("expectedBoxCounts"), dict) else {}
        actual = preflight.get("actualBoxCounts") if isinstance(preflight.get("actualBoxCounts"), dict) else {}
        if not expected or not actual:
            return False
        for box, expected_counts in expected.items():
            if not isinstance(expected_counts, dict):
                return False
            actual_counts = actual.get(box) if isinstance(actual.get(box), dict) else {}
            expected_entities = int(number_or_none(expected_counts.get("entityCount")) or 0)
            expected_relations = int(number_or_none(expected_counts.get("relationCount")) or 0)
            if int(number_or_none(actual_counts.get("entityCount")) or 0) != expected_entities:
                return False
            if int(number_or_none(actual_counts.get("relationCount")) or 0) > expected_relations:
                return False
        return True

    def missing_seed_relation_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        """Return immutable static relation rows absent from the graph store."""
        _node_rows, relation_rows = self.graph_persistence_rows(graph)
        expected_boxes = {
            str(row.get("ontologyBox") or "ABox")
            for row in relation_rows
            if str(row.get("ontologyBox") or "ABox") != "ABox"
        }
        stored_ids = set()
        for box in sorted(expected_boxes):
            rows = self.read_rows(
                "match $r isa ontology-assertion, has ontology-box "
                + typedb_string(box)
                + ", has ontology-id $id;",
                ["id"],
                label="typedb.seed-relation-repair-audit",
            )
            stored_ids.update(str(row.get("id") or "") for row in rows)
        return [
            row
            for row in relation_rows
            if str(row.get("ontologyBox") or "ABox") in expected_boxes
            and relation_row_id(row) not in stored_ids
        ]

    def repair_seed_relations(self, graph: PortfolioOntology) -> Dict[str, object]:
        """Insert only missing static relations after an interrupted seed."""
        if not self.address:
            return {"configured": False, "saved": False, "status": "disabled", "missingRelationCount": 0}
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], graph)
        try:
            missing_rows = self.missing_seed_relation_rows(graph)
            if not missing_rows:
                return {
                    "configured": True,
                    "saved": True,
                    "status": "unchanged",
                    "graphStore": "typedb",
                    "missingRelationCount": 0,
                    "insertedRelationCount": 0,
                }
            settings = runtime_settings()
            relation_batch_size = self.abox_relation_batch_size(settings)
            queries = self.batched_relation_insert_queries(
                missing_rows,
                utc_now(),
                relation_batch_size,
                self.write_query_max_bytes(settings),
            )
            _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]

            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    transaction_query_count = self.graph_write_transaction_query_count(settings)
                    for offset in range(0, len(queries), transaction_query_count):
                        query_batch = queries[offset: offset + transaction_query_count]
                        with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB seed relation repair"):
                            with driver.transaction(
                                self.database,
                                TransactionType.WRITE,
                                options=self.write_transaction_options(),
                            ) as tx:
                                for query in query_batch:
                                    tx.query(query).resolve()
                                tx.commit()
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
            return {
                "configured": True,
                "saved": True,
                "status": "ok",
                "graphStore": "typedb",
                "missingRelationCount": len(missing_rows),
                "insertedRelationCount": len(missing_rows),
                "queryCount": len(queries),
            }
        except Exception as error:  # noqa: BLE001 - caller can fall back to a full deterministic seed.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "reason": str(error)[:220],
            }

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload or {}
        try:
            rules = rulebox_rules_from_payload(payload) if (payload.get("rules") is not None or payload.get("rulesJson")) else default_graph_inference_rules()
        except ValueError as error:
            return {"configured": True, "saved": False, "seeded": False, "status": "invalid-rulebox", "graphStore": "typedb", "reason": str(error)}
        rules = list(rules)
        rules_payload = rulebox_rules_to_payload(rules)
        self._last_rules = rules
        scoped_write_lease_recovery = {}
        if typedb_bool(payload.get("recoverScopedABoxWriteLease")):
            scoped_write_lease_recovery = self.recover_scoped_abox_write_lease_after_server_start()

        def complete_seed(result: Dict[str, object]) -> Dict[str, object]:
            completed = dict(result or {})
            if scoped_write_lease_recovery:
                completed["scopedABoxWriteLeaseRecovery"] = dict(scoped_write_lease_recovery)
            return self.with_seed_schema_function_sync(completed, rules)

        seed_graph = ontology_seed_graph(
            rules,
            language_registry=investment_language_registry(runtime_settings()),
        )
        preflight = self.seed_graph_preflight(seed_graph, rules_payload)
        if preflight.get("ready") and not typedb_bool(payload.get("forceReseed")):
            return complete_seed({
                "configured": True,
                "saved": True,
                "seeded": True,
                "status": "unchanged",
                "graphStore": "typedb",
                "engineVersion": GRAPH_REASONER_VERSION,
                "ruleCount": len(rules),
                "seedSkipped": True,
                "seedPreflight": preflight,
                "ruleBoxReplaceRequested": typedb_bool(payload.get("replaceRuleBox")),
                "ruleBoxAlreadyCurrent": True,
                "ruleBoxHashMatched": True,
                "activeRuleBoxRuleCount": len(rules),
                "expectedRuleBoxRuleCount": len(rules),
                "activeRuleBoxShortHash": rulebox_runtime_metadata(rules_payload)["ruleboxShortHash"],
                "expectedRuleBoxShortHash": rulebox_runtime_metadata(rules_payload)["ruleboxShortHash"],
            })
        relation_repair = {}
        if not typedb_bool(payload.get("forceReseed")) and self.seed_relation_repair_eligible(preflight):
            relation_repair = self.repair_seed_relations(seed_graph)
            if relation_repair.get("saved"):
                repaired_preflight = self.seed_graph_preflight(seed_graph, rules_payload)
                if repaired_preflight.get("ready"):
                    return complete_seed({
                        "configured": True,
                        "saved": True,
                        "seeded": True,
                        "status": "repaired",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": False,
                        "seedPreflight": repaired_preflight,
                        "staticRelationRepair": relation_repair,
                        "ruleBoxReplaceRequested": typedb_bool(payload.get("replaceRuleBox")),
                        "ruleBoxAlreadyCurrent": True,
                        "ruleBoxHashMatched": True,
                        "activeRuleBoxRuleCount": len(rules),
                        "expectedRuleBoxRuleCount": len(rules),
                        "activeRuleBoxShortHash": rulebox_runtime_metadata(rules_payload)["ruleboxShortHash"],
                        "expectedRuleBoxShortHash": rulebox_runtime_metadata(rules_payload)["ruleboxShortHash"],
                    })
        result = self.save_graph(seed_graph)
        result.update({
            "configured": True,
            "seeded": bool(result.get("saved")),
            "engineVersion": GRAPH_REASONER_VERSION,
            "ruleCount": len(rules),
            "graphStore": "typedb",
            "seedSkipped": False,
            "seedPreflight": preflight,
        })
        if relation_repair:
            result["staticRelationRepair"] = relation_repair
        if typedb_bool(payload.get("replaceRuleBox")) and result.get("saved"):
            expected_rulebox = rulebox_runtime_metadata(rules_payload)
            # ``seed_graph`` already replaced RuleBox in the same graph write.
            # Read it back for verification instead of performing a second full
            # RuleBox replacement during every service startup.
            self.clear_rulebox_snapshot_cache()
            rulebox_result = self.rulebox_snapshot()
            expected_structure = rulebox_structural_fingerprint(rules_payload)
            active_rules_payload = rulebox_result.get("rules") if isinstance(rulebox_result.get("rules"), list) else []
            active_structure = rulebox_structural_fingerprint(active_rules_payload)
            active_rule_count = int(number_or_none(rulebox_result.get("ruleCount") or rulebox_result.get("ruleboxRuleCount")) or 0)
            active_rule_hash = str(rulebox_result.get("ruleboxRulesHash") or "")
            hash_matched = active_rule_hash == expected_rulebox["ruleboxRulesHash"]
            replace_verified = (
                bool(rulebox_result.get("saved"))
                and str(rulebox_result.get("status") or "") == "ok"
                and active_rule_count == len(rules_payload)
                and active_structure == expected_structure
            )
            result.update({
                "ruleBoxReplaceRequested": True,
                "ruleBoxReplaced": replace_verified,
                "ruleBoxHashMatched": hash_matched,
                "activeRuleBoxRuleCount": active_rule_count,
                "expectedRuleBoxRuleCount": len(rules_payload),
                "activeRuleBoxShortHash": str(rulebox_result.get("ruleboxShortHash") or active_rule_hash[:12]),
                "expectedRuleBoxShortHash": expected_rulebox["ruleboxShortHash"],
                "ruleBoxReplaceResult": {
                    "saved": bool(rulebox_result.get("saved")),
                    "status": rulebox_result.get("status") or "",
                    "reason": rulebox_result.get("reason") or "",
                    "ruleCount": active_rule_count,
                    "conditionCount": int(number_or_none(rulebox_result.get("conditionCount") or rulebox_result.get("ruleboxConditionCount")) or 0),
                    "derivationCount": int(number_or_none(rulebox_result.get("derivationCount") or rulebox_result.get("ruleboxDerivationCount")) or 0),
                    "ruleboxShortHash": str(rulebox_result.get("ruleboxShortHash") or active_rule_hash[:12]),
                },
            })
            if replace_verified and typedb_bool(payload.get("clearInference")):
                result["clearInferenceResult"] = self.clear_inferencebox()
            if not replace_verified:
                result.update({
                    "saved": False,
                    "seeded": False,
                    "status": rulebox_result.get("status") or "rulebox-replace-failed",
                    "reason": (
                        "RuleBox replace requested but active RuleBox did not match the seeded rules. "
                        + str(rulebox_result.get("reason") or "")
                    ).strip(),
                })
        return complete_seed(result)

    def with_seed_schema_function_sync(
        self,
        result: Dict[str, object],
        rules: Iterable[GraphInferenceRule],
    ) -> Dict[str, object]:
        """Make a successful ontology seed usable by realtime rule execution.

        RuleBox rows and TypeDB schema functions are deployed artifacts. Keeping
        the former while silently missing the latter meant a server restart
        could leave every market-open cycle without investment inference.
        """
        completed = dict(result or {})
        if not completed.get("saved"):
            return completed
        try:
            schema_sync = self.sync_typedb_native_rule_functions(rules)
        except Exception as error:  # noqa: BLE001 - surface a failed startup repair explicitly.
            schema_sync = {
                "status": "error",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }
        completed["schemaFunctionSync"] = schema_sync
        if str(schema_sync.get("status") or "") == "ok":
            return completed
        completed.update({
            "saved": False,
            "seeded": False,
            "status": "schema-function-sync-failed",
            "reason": "TypeDB schema function 동기화 실패: "
            + str(schema_sync.get("reason") or schema_sync.get("status") or "")[:180],
        })
        return completed

    def rulebox_snapshot(self) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().rulebox_snapshot()
        cache_age = time.time() - float(self._rulebox_snapshot_cache_at or 0)
        if self._rulebox_snapshot_cache_result and cache_age <= self.rulebox_snapshot_cache_seconds():
            cached = copy.deepcopy(self._rulebox_snapshot_cache_result)
            cached["cached"] = True
            cached["ruleBoxSnapshotCached"] = True
            return cached
        try:
            entities = self.read_entity_rows(["RuleBox", "RuleBoxGovernance"])
            relations = self.read_relation_rows(["RuleBox", "RuleBoxGovernance"])
        except Exception as error:  # noqa: BLE001 - admin read model must fail closed.
            rules = rulebox_rules_to_payload(self._last_rules or default_graph_inference_rules())
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "source": "typedb-typeql",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "engineVersion": GRAPH_REASONER_VERSION,
                "rules": [],
                "ruleCount": 0,
                "conditionCount": 0,
                "derivationCount": 0,
                "relationTypes": [],
                "defaultsFallbackUsed": False,
                "bootstrapAvailable": True,
                "bootstrapRuleCount": len(rules),
                "bootstrapRules": rules,
                "versions": [],
                "versionCount": 0,
                "changeCandidates": rulebox_governance_candidates([], []),
            }
        rowsets = {
            "rules": [row for row in entities if entity_node_kind(row) == "rule" and row.get("ontologyBox") == "RuleBox"],
            "conditions": [row for row in entities if entity_node_kind(row) == "rule-condition" and row.get("ontologyBox") == "RuleBox"],
            "derivations": [row for row in entities if entity_node_kind(row) == "relation-template" and row.get("ontologyBox") == "RuleBox"],
            "relationTypes": relation_type_rows_from_derivations(entities, relations),
            "versions": [row for row in entities if entity_node_kind(row) == "rulebox-version" and row.get("ontologyBox") == "RuleBoxGovernance"],
            "candidates": [row for row in entities if entity_node_kind(row) == "rule-change-candidate" and row.get("ontologyBox") == "RuleBoxGovernance"],
        }
        snapshot = rulebox_snapshot_from_rows(rowsets, "typedb-typeql")
        snapshot.update({"graphStore": "typedb", "source": "typedb-typeql"})
        snapshot.update(rulebox_runtime_metadata(snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []))
        if snapshot.get("status") == "ok":
            try:
                self._last_rules = rulebox_rules_from_payload({"rules": snapshot.get("rules") or []})
            except ValueError:
                pass
        snapshot["nativeReasoningProfile"] = typedb_native_reasoning_profile(snapshot.get("rules") or [])
        snapshot["ruleBoxSnapshotCached"] = False
        self._rulebox_snapshot_cache_at = time.time()
        self._rulebox_snapshot_cache_result = copy.deepcopy(snapshot)
        return snapshot

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        try:
            rules = rulebox_rules_from_payload(payload or {})
        except ValueError as error:
            return {"configured": True, "saved": False, "status": "invalid-rulebox", "graphStore": "typedb", "reason": str(error)}
        self._last_rules = list(rules)
        self.clear_rulebox_snapshot_cache()
        save_result = self.save_graph(rulebox_graph_from_rules(
            self._last_rules,
            include_tbox=False,
            language_registry=investment_language_registry(runtime_settings()),
        ))
        self.clear_rulebox_snapshot_cache()
        snapshot = self.rulebox_snapshot()
        snapshot.update({
            "saved": bool(save_result.get("saved")),
            "status": save_result.get("status") or snapshot.get("status"),
            "reason": save_result.get("reason") or snapshot.get("reason") or "",
            "saveResult": save_result,
        })
        return snapshot

    def verify_typedb_native_any_conditions(
        self,
        driver,
        transaction_type,
        rule: GraphInferenceRule,
        source_id: str,
        timeout_seconds: float,
        scoped_manifest_only: bool,
        tx=None,
    ) -> Dict[str, object]:
        """Verify an N-of-M RuleBox group in one bounded TypeQL query.

        Schema functions retain only required and negative clauses.  Expanding
        `anyConditionMinCount` combinations inside a function made TypeDB
        compile an exponential search plan.  The candidate source is already
        narrowed by that base match, so TypeDB can safely evaluate the whole
        N-of-M group with a distinct RuleBox-condition aggregation.  Python
        receives only the matched/not-matched result; it never counts the
        branches to decide an investment rule.
        """
        conditions = [
            (index, condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {}))
            for index, condition in enumerate(getattr(rule, "conditions", []) or [])
            if normalized_condition_role(
                condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
            ) in {"any", "optional"}
        ]
        if not conditions:
            return {
                "status": "matched",
                "matchedConditionIds": [],
                "requiredConditionCount": 0,
                "readTransactionCount": 0,
                "readQueryCount": 0,
            }
        required_count = max(1, int(number_or_none(getattr(rule, "any_condition_min_count", 1)) or 1))
        if required_count > len(conditions):
            return {
                "status": "invalid",
                "matchedConditionIds": [],
                "requiredConditionCount": required_count,
                "readTransactionCount": 0,
                "readQueryCount": 0,
                "reason": "RuleBox any condition minimum exceeds available conditions.",
            }
        query_plan = typedb_native_any_group_check_query(
            rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {}),
            source_id,
            scoped_manifest_only=scoped_manifest_only,
        )
        if not query_plan.get("query"):
            return {
                "status": "error",
                "matchedConditionIds": [],
                "requiredConditionCount": required_count,
                "readTransactionCount": 0,
                "readQueryCount": 0,
                "reason": str(query_plan.get("reason") or "TypeDB any-condition group query could not be built."),
            }
        owns_transaction = tx is None
        try:
            if owns_transaction:
                with driver.transaction(
                    self.database,
                    transaction_type.READ,
                    self.read_transaction_options(timeout_seconds),
                ) as transaction:
                    rows = self.read_rows_in_transaction(
                        transaction,
                        str(query_plan.get("query")),
                        query_plan.get("columns") or ["sourceId"],
                        label="nativeRuleAnyGroup:" + str(rule.rule_id or ""),
                        timeout_seconds=timeout_seconds,
                    )
            else:
                rows = self.read_rows_in_transaction(
                    tx,
                    str(query_plan.get("query")),
                    query_plan.get("columns") or ["sourceId"],
                    label="nativeRuleAnyGroup:" + str(rule.rule_id or ""),
                    timeout_seconds=timeout_seconds,
                )
        except Exception as error:  # noqa: BLE001 - a partial any check must block the whole inference generation.
            return {
                "status": "query-timeout" if typedb_error_code(error) == "typedbTimeout" else "error",
                "matchedConditionIds": [],
                "requiredConditionCount": required_count,
                "readTransactionCount": 1 if owns_transaction else 0,
                "readQueryCount": 0,
                "reason": str(error)[:220],
            }
        return {
            "status": "matched" if rows else "not-matched",
            # Detailed per-branch evidence is intentionally collected only by
            # the opt-in condition-detail path.  The group cardinality itself
            # is decided by TypeDB's `reduce count` query.
            "matchedConditionIds": [],
            "requiredConditionCount": required_count,
            "readTransactionCount": 1 if owns_transaction else 0,
            "readQueryCount": 1,
            "typeDbCardinalityVerified": bool(rows),
        }

    def match_typedb_native_rules(
        self,
        rules: Iterable[GraphInferenceRule],
        target_symbols: Iterable[str] = None,
        use_schema_functions: bool = True,
    ) -> Dict[str, object]:
        rules = list(rules or [])
        clean_symbols = clean_symbols_from_payload(list(target_symbols or []))
        # The original hot path rebuilt every rule as a raw TypeQL match when a
        # changed symbol was supplied. `any` branches in those queries can grow
        # combinatorially and have previously stalled the TypeDB server. Base
        # clauses stay in persisted TypeDB schema functions; N-of-M clauses
        # are verified as bounded, source-specific TypeQL reads only after a
        # base match exists.
        schema_function_query = bool(use_schema_functions)
        execution_mode = (
            "typedb-schema-function-filtered-planned"
            if schema_function_query and clean_symbols
            else "typedb-schema-function"
            if schema_function_query
            else "typedb-scoped-typeql"
        )
        matches: List[Dict[str, object]] = []
        match_index: Dict[str, Dict[str, object]] = {}
        executed_rules = []
        skipped_rules = []
        read_call_count = 0
        read_transaction_count = 0
        execution_plan: Dict[str, object] = {}
        query_failures = []
        execution_budget_exhausted = False
        execution_incomplete = False
        try:
            relation_types_by_symbol: Dict[str, Iterable[str]] = {}
            rule_context: Dict[str, object] = {}
            preflight_graph = None
            if clean_symbols:
                preflight_sources = []
                try:
                    rule_context = self.active_abox_rule_context(clean_symbols)
                    if str(rule_context.get("status") or "") != "ok":
                        raise RuntimeError("TypeDB active ABox rule context is unavailable.")
                    relation_types_by_symbol = dict(rule_context.get("relationTypesBySymbol") or {})
                    source_ids_by_symbol = dict(rule_context.get("sourceIdsBySymbol") or {})
                    preflight_sources = [
                        {
                            "sourceId": str(source_id or "").strip(),
                            "sourceLabel": str(symbol or "").upper().strip(),
                        }
                        for symbol in clean_symbols
                        for source_id in source_ids_by_symbol.get(symbol, []) or []
                        if str(source_id or "").strip()
                    ]
                except Exception as error:  # noqa: BLE001 - planner topology is an optimization, never a reason to stall all rule functions.
                    rule_context = {
                        "status": "degraded",
                        "symbols": clean_symbols,
                        "reason": str(error)[:220],
                        "relationTypesBySymbol": {},
                        "preflightStatus": "degraded",
                    }
                if preflight_sources:
                    try:
                        # This graph is a read-only execution index. It can
                        # prove that a required RuleBox clause is impossible,
                        # but it never establishes an inference; every
                        # surviving rule is still evaluated by its TypeDB
                        # schema function against the active Manifest.
                        preflight_graph = self.load_graph_for_native_matches(
                            {"matches": preflight_sources},
                            rules,
                            include_all_rule_relation_types=True,
                            # Only two current RuleBox rules need inbound
                            # edges. Keep those candidates for TypeDB rather
                            # than loading a second, expensive endpoint scan
                            # just to preflight them.
                            include_incoming_relations=False,
                        )
                        preflight_manifest_ids = sorted({
                            str((item.properties or {}).get("worldviewManifestId") or "").strip()
                            for item in preflight_graph.entities
                            if str(item.kind or "") == "stock"
                            and str((item.properties or {}).get("worldviewManifestId") or "").strip()
                        })
                        rule_context.update({
                            "preflightStatus": "ok",
                            "preflightSourceCount": len(preflight_sources),
                            "preflightEntityCount": len(preflight_graph.entities),
                            "preflightRelationCount": len(preflight_graph.relations),
                            "preflightWorldviewManifestIds": preflight_manifest_ids,
                        })
                    except Exception as error:  # noqa: BLE001 - preflight is optional; retain the verified topology.
                        rule_context.update({
                            "preflightStatus": "degraded",
                            "preflightReason": str(error)[:220],
                        })
                        preflight_graph = None
                elif str(rule_context.get("preflightStatus") or "") != "degraded":
                    rule_context.update({
                        "preflightStatus": "skipped-no-source",
                        "preflightSourceCount": 0,
                    })
            execution_plan = typedb_native_rule_execution_plan(
                rules,
                clean_symbols,
                relation_types_by_symbol,
                # A selected symbol is one inference unit.  Limiting individual
                # rule calls here meant that a stable priority list could defer
                # the same applicable rules forever and materialize a partial
                # judgement.  Symbol scheduling happens before this method;
                # TypeDB must evaluate every applicable rule for that symbol.
                0,
                preflight_graph=preflight_graph,
                preflight_incoming_relations_complete=False,
            )
            for item in execution_plan.get("skippedEntries") or []:
                skipped_rules.append({
                    "ruleId": str(item.get("ruleId") or ""),
                    "status": str(item.get("status") or "skipped"),
                    "reason": str(item.get("reason") or "")[:220],
                    "requiredRelationTypes": list(item.get("requiredRelationTypes") or []),
                })
            # Reject non-native RuleBox entries before opening TypeDB.  This
            # makes a mixed or incomplete RuleBox fail closed without issuing
            # a partial query against the live investment world.
            selected_entries = []
            for planned in execution_plan.get("selectedEntries") or []:
                rule = planned.get("rule")
                if not rule:
                    continue
                rule_payload = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {})
                profile = typedb_native_rule_profile(rule_payload)
                if profile.get("status") != "ready":
                    execution_incomplete = True
                    skipped_rules.append({
                        "ruleId": str(rule.rule_id or ""),
                        "status": str(profile.get("status") or "partial"),
                        "reason": "Rule has JSON-bound or unsupported conditions for TypeDB schema function execution.",
                    })
                    continue
                selected_entries.append(planned)
            imported = self.driver_imports()
            if imported[0] is None:
                raise RuntimeError("typedb-driver Python package is not installed: " + str(imported[1])[:160])
            _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
            # Schema-function calls always bind the scoped active Manifest in
            # their definition. Only a rule with an N-of-M group uses a direct
            # TypeQL base/group probe, so avoid an extra active-pointer query
            # for the normal function-only fast path.
            requires_direct_any_probe = any(
                any(
                    normalized_condition_role(
                        condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
                    ) in {"any", "optional"}
                    for condition in (getattr(item.get("rule"), "conditions", []) or [])
                )
                for item in selected_entries
                if item.get("rule")
            )
            scoped_manifest_only = False
            if requires_direct_any_probe:
                try:
                    scoped_manifest_only = self.active_abox_uses_scoped_manifest()
                except Exception:
                    scoped_manifest_only = False

            def operation():
                nonlocal read_call_count, read_transaction_count, execution_budget_exhausted, execution_incomplete, execution_mode
                if not selected_entries:
                    return
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    deadline = time.monotonic() + self.native_rule_execution_budget_seconds()
                    # All applicable rules observe one stable ABox read view.
                    # Previously every rule, and then every N-of-M check, opened
                    # an independent transaction. That multiplied driver setup
                    # and query planning work while allowing a live world switch
                    # between rules. Per-query alarm limits still bound an
                    # expensive function; a failed query aborts this shared
                    # transaction and yields a fail-closed partial result.
                    transaction_timeout = max(
                        self.native_rule_query_timeout_seconds(),
                        min(120.0, self.native_rule_execution_budget_seconds() + 2.0),
                    )
                    read_transaction_count += 1
                    with driver.transaction(
                        self.database,
                        TransactionType.READ,
                        self.read_transaction_options(transaction_timeout),
                    ) as tx:
                        for planned in selected_entries:
                            rule = planned.get("rule")
                            if not rule:
                                continue
                            remaining_seconds = deadline - time.monotonic()
                            if remaining_seconds <= 0:
                                execution_budget_exhausted = True
                                execution_incomplete = True
                                skipped_rules.append({
                                    "ruleId": str(rule.rule_id or ""),
                                    "status": "deferred-by-runtime-budget",
                                    "reason": "TypeDB native-rule realtime execution budget is exhausted.",
                                })
                                continue
                            rule_payload = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {})
                            function_name = typedb_native_rule_function_name(rule.rule_id)
                            has_any_conditions = any(
                                normalized_condition_role(
                                    condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
                                ) in {"any", "optional"}
                                for condition in (rule.conditions or [])
                            )
                            if has_any_conditions and schema_function_query:
                                execution_mode = "typedb-schema-function-hybrid-any-verified"
                            candidate_symbols = clean_symbols_from_payload(
                                planned.get("candidateSymbols") or clean_symbols
                            )
                            # Every v4 function compiles only required and
                            # negative clauses. Rules with N-of-M conditions
                            # use that same compiled base and verify cardinality
                            # below in TypeDB, rather than rebuilding a raw
                            # TypeQL base query for every rule.
                            uses_schema_function = schema_function_query
                            query_plan = (
                                typedb_native_function_call_query(rule_payload, candidate_symbols)
                                if uses_schema_function
                                else typedb_native_match_query(
                                    rule_payload,
                                    candidate_symbols,
                                    scoped_manifest_only=scoped_manifest_only,
                                    include_any_conditions=False,
                                )
                            )
                            if not query_plan.get("query"):
                                execution_incomplete = True
                                skipped_rules.append({
                                    "ruleId": str(rule.rule_id or ""),
                                    "status": "blocked",
                                    "reason": "TypeDB schema function call could not be built.",
                                })
                                continue
                            query_timeout = min(self.native_rule_query_timeout_seconds(), remaining_seconds)
                            try:
                                rows = self.read_rows_in_transaction(
                                    tx,
                                    str(query_plan.get("query")),
                                    query_plan.get("columns") or ["sourceId"],
                                    label="nativeRule:" + str(rule.rule_id or ""),
                                    timeout_seconds=query_timeout,
                                )
                                read_call_count += 1
                            except Exception as error:  # noqa: BLE001 - a timed-out shared read cannot safely continue.
                                failure = {
                                    "ruleId": str(rule.rule_id or ""),
                                    "status": "query-timeout" if typedb_error_code(error) == "typedbTimeout" else "query-error",
                                    "reason": str(error)[:220],
                                    "candidateSymbols": candidate_symbols,
                                }
                                skipped_rules.append(failure)
                                query_failures.append(failure)
                                execution_incomplete = True
                                break
                            any_condition_query_count = 0
                            any_condition_failure = False
                            if rows and has_any_conditions:
                                verified_rows = []
                                for row in rows:
                                    remaining_seconds = deadline - time.monotonic()
                                    if remaining_seconds <= 0:
                                        failure = {
                                            "ruleId": str(rule.rule_id or ""),
                                            "status": "deferred-by-runtime-budget",
                                            "reason": "TypeDB native-rule runtime budget was exhausted while verifying any conditions.",
                                            "candidateSymbols": candidate_symbols,
                                        }
                                        skipped_rules.append(failure)
                                        query_failures.append(failure)
                                        execution_budget_exhausted = True
                                        execution_incomplete = True
                                        any_condition_failure = True
                                        break
                                    verification = self.verify_typedb_native_any_conditions(
                                        driver,
                                        TransactionType,
                                        rule,
                                        str(row.get("sourceId") or ""),
                                        min(self.native_rule_query_timeout_seconds(), remaining_seconds),
                                        scoped_manifest_only,
                                        tx=tx,
                                    )
                                    read_transaction_count += int(verification.get("readTransactionCount") or 0)
                                    read_call_count += int(verification.get("readQueryCount") or 0)
                                    any_condition_query_count += int(verification.get("readQueryCount") or 0)
                                    verification_status = str(verification.get("status") or "error")
                                    if verification_status == "matched":
                                        row["_matchedAnyConditionIds"] = list(verification.get("matchedConditionIds") or [])
                                        row["_anyConditionsVerified"] = bool(verification.get("typeDbCardinalityVerified"))
                                        verified_rows.append(row)
                                        continue
                                    if verification_status == "not-matched":
                                        continue
                                    failure = {
                                        "ruleId": str(rule.rule_id or ""),
                                        "status": "any-condition-" + verification_status,
                                        "reason": str(verification.get("reason") or "TypeDB any-condition verification did not complete.")[:220],
                                        "candidateSymbols": candidate_symbols,
                                    }
                                    skipped_rules.append(failure)
                                    query_failures.append(failure)
                                    execution_incomplete = True
                                    any_condition_failure = True
                                    break
                                if any_condition_failure:
                                    # The failed group query can invalidate the
                                    # shared read transaction. Do not evaluate
                                    # the remaining rules against an uncertain
                                    # snapshot.
                                    break
                                rows = verified_rows
                            executed_rules.append({
                                "ruleId": rule.rule_id,
                                "nativeRuleId": typedb_native_rule_id(rule.rule_id),
                                "schemaFunctionName": function_name if uses_schema_function else "",
                                "queryMode": (
                                    execution_mode
                                    if uses_schema_function
                                    else "typedb-scoped-typeql-any-verified"
                                ),
                                "rowCount": len(rows),
                                "candidateSymbols": candidate_symbols,
                                "queryComplexity": int(planned.get("queryComplexity") or 0),
                                "anyConditionQueryCount": any_condition_query_count,
                            })
                            self.merge_native_match_rows(rule, query_plan, rows, match_index, matches)
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
            if query_failures or execution_budget_exhausted or execution_incomplete:
                return {
                    "status": "partial",
                    "graphStore": "typedb",
                    "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                    "nativeQueryUsed": False,
                    "schemaFunctionUsed": schema_function_query,
                    "nativeExecutionMode": execution_mode,
                    "matchedCount": len(matches),
                    "executedRuleCount": len(executed_rules),
                    "skippedRuleCount": len(skipped_rules),
                    "matches": matches,
                    "reasonCode": "typedbNativeRuleExecutionPartial",
                    "reason": "TypeDB native rule execution did not complete for every applicable rule.",
                    "readTransactionCount": read_transaction_count,
                    "readQueryCount": read_call_count,
                    "executedRules": executed_rules[:40],
                    "skippedRules": skipped_rules[:40],
                    "executionPlan": typedb_native_rule_execution_plan_summary(execution_plan),
                    "ruleContext": rule_context,
                    "typedbQueryMetrics": self.query_metrics_snapshot(),
                }
            return {
                "status": "ok",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "nativeQueryUsed": True,
                "schemaFunctionUsed": schema_function_query,
                "nativeExecutionMode": execution_mode,
                "executedRuleCount": len(executed_rules),
                "skippedRuleCount": len(skipped_rules),
                "matchedCount": len(matches),
                "readTransactionCount": read_transaction_count,
                "readQueryCount": read_call_count,
                "readTransactionCount": read_transaction_count,
                "conditionDetailQueryCount": 0 if not self.condition_detail_queries_enabled() else None,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
                "matches": matches,
                "executedRules": executed_rules[:40],
                "skippedRules": skipped_rules[:40],
                "executionPlan": typedb_native_rule_execution_plan_summary(execution_plan),
                "ruleContext": rule_context,
            }
        except Exception as error:  # noqa: BLE001 - run_rulebox reports and can use compatibility fallback.
            return {
                "status": "error",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "nativeQueryUsed": False,
                "schemaFunctionUsed": False,
                "nativeExecutionMode": execution_mode,
                "matchedCount": 0,
                "matches": [],
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "executedRules": executed_rules[:40],
                "skippedRules": skipped_rules[:40],
                "readQueryCount": read_call_count,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
                "executionPlan": typedb_native_rule_execution_plan_summary(execution_plan),
            }

    def merge_native_match_rows(
        self,
        rule: GraphInferenceRule,
        query_plan: Dict[str, object],
        rows: Iterable[Dict[str, object]],
        match_index: Dict[str, Dict[str, object]],
        matches: List[Dict[str, object]],
    ) -> None:
        for row in rows or []:
            source_id = str(row.get("sourceId") or "").strip()
            if not source_id:
                continue
            evidence_relation_ids = [
                str(row.get(column) or "")
                for column in (query_plan.get("evidenceColumns") or [])
                if str(row.get(column) or "").strip()
            ]
            match_key = str(rule.rule_id or "") + "|" + source_id
            existing = match_index.get(match_key)
            if existing:
                existing["evidenceRelationIds"] = sorted(set(list(existing.get("evidenceRelationIds") or []) + evidence_relation_ids))
                existing_conditions = list(existing.get("matchedConditions") or [])
                existing_condition_ids = {
                    str(item.get("conditionId") or "")
                    for item in existing_conditions
                    if isinstance(item, dict)
                }
                for item in typedb_native_matched_conditions(rule, row, query_plan):
                    if str(item.get("conditionId") or "") not in existing_condition_ids:
                        existing_conditions.append(item)
                existing["matchedConditions"] = existing_conditions
                continue
            condition_context = self.typedb_schema_function_condition_context(rule, source_id, query_plan, row)
            evidence_relation_ids = sorted(set(evidence_relation_ids + list(condition_context.get("evidenceRelationIds") or [])))
            match = {
                "ruleId": rule.rule_id,
                "nativeRuleId": typedb_native_rule_id(rule.rule_id),
                "schemaFunctionName": typedb_native_rule_function_name(rule.rule_id),
                "sourceId": source_id,
                "sourceLabel": str(row.get("sourceLabel") or ""),
                "matchedConditions": list(condition_context.get("matchedConditions") or []),
                "evidenceRelationIds": sorted(set(evidence_relation_ids)),
                "conditionDetailSource": str(condition_context.get("conditionDetailSource") or "schema-function-match"),
            }
            match_index[match_key] = match
            matches.append(match)

    def typedb_schema_function_condition_context(
        self,
        rule: GraphInferenceRule,
        source_id: str,
        query_plan: Dict[str, object] = None,
        row: Dict[str, object] = None,
    ) -> Dict[str, object]:
        if not self.condition_detail_queries_enabled():
            return typedb_static_schema_function_condition_context(rule, query_plan or {}, row or {})
        matched_conditions: List[Dict[str, object]] = []
        evidence_relation_ids: List[str] = []
        for index, condition in enumerate(getattr(rule, "conditions", []) or []):
            condition_payload = condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
            condition_id = str(condition_payload.get("condition_id") or condition_payload.get("conditionId") or "condition-" + str(index))
            role = normalized_condition_role(condition_payload)
            query_plan = typedb_native_condition_check_query(condition_payload, source_id, index)
            rows: List[Dict[str, object]] = []
            if query_plan.get("query"):
                rows = self.read_rows(str(query_plan.get("query")), query_plan.get("columns") or [])
            condition_matched = bool(rows)
            if role == "not":
                if not condition_matched:
                    matched_conditions.append({
                        "conditionId": condition_id,
                        "kind": condition_payload.get("kind"),
                        "role": role,
                        "absenceSatisfied": True,
                    })
                continue
            if role in {"any", "optional"} and not condition_matched:
                continue
            if not condition_matched:
                matched_conditions.append({
                    "conditionId": condition_id,
                    "kind": condition_payload.get("kind"),
                    "role": role,
                    "matched": False,
                })
                continue
            payload = {
                "conditionId": condition_id,
                "kind": condition_payload.get("kind"),
                "role": role,
            }
            if condition_payload.get("kind") == "subject_property":
                payload.update({
                    "field": condition_payload.get("field"),
                    "operator": condition_payload.get("operator"),
                    "value": condition_payload.get("value"),
                })
            elif condition_payload.get("kind") == "relation":
                relation_id_column = str(query_plan.get("relationIdColumn") or "")
                relation_id = str((rows[0] if rows else {}).get(relation_id_column) or "")
                if relation_id:
                    payload["relationId"] = relation_id
                    evidence_relation_ids.append(relation_id)
                payload.update({
                    "relationType": condition_payload.get("relation_type") or condition_payload.get("relationType"),
                })
            matched_conditions.append(payload)
        return {
            "matchedConditions": matched_conditions,
            "evidenceRelationIds": sorted(set(evidence_relation_ids)),
            "conditionDetailSource": "schema-function-detail-query",
        }

    def typedb_schema_function_names(self, driver) -> set:
        databases = getattr(driver, "databases", None)
        get_database = getattr(databases, "get", None) if databases is not None else None
        if not callable(get_database):
            raise RuntimeError("TypeDB database schema listing is unavailable.")
        database = get_database(self.database)
        schema_reader = getattr(database, "schema", None)
        if not callable(schema_reader):
            raise RuntimeError("TypeDB database schema reader is unavailable.")
        schema_text = str(schema_reader() or "")
        return set(re.findall(r"\bfun\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", schema_text))

    def probe_typedb_native_rule_functions(self, rules: Iterable[GraphInferenceRule]) -> Dict[str, object]:
        ready_rules = []
        for rule in rules or []:
            rule_payload = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {})
            if typedb_native_rule_profile(rule_payload).get("status") == "ready":
                ready_rules.append((rule, rule_payload))
        if not ready_rules:
            return {"status": "empty", "available": False, "probedCount": 0}
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "status": "driver-missing",
                "available": False,
                "probedCount": 0,
                "reason": str(imported[1])[:180],
            }
        probed_count = 0
        verified_rule_ids: List[str] = []
        missing_rule_ids: List[str] = []
        missing_function_names: List[str] = []
        unresolved_function_names: List[str] = []
        try:
            def operation():
                nonlocal probed_count, verified_rule_ids, missing_rule_ids, missing_function_names, unresolved_function_names
                probed_count = 0
                verified_rule_ids = []
                missing_rule_ids = []
                missing_function_names = []
                unresolved_function_names = []
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    available_function_names = self.typedb_schema_function_names(driver)
                    for rule, rule_payload in ready_rules:
                        rule_id = str(rule.rule_id or "")
                        query_plan = typedb_native_function_call_query(rule_payload, ["__ORBIT_SCHEMA_PROBE__"])
                        function_name = str(query_plan.get("functionName") or "")
                        if function_name not in available_function_names:
                            missing_rule_ids.append(rule_id)
                            missing_function_names.append(function_name)
                            unresolved_function_names.append(function_name)
                            continue
                        probed_count += 1
                        verified_rule_ids.append(rule_id)
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
            if missing_rule_ids:
                return {
                    "status": "missing",
                    "available": False,
                    "probedCount": probed_count,
                    "verifiedRuleCount": len(ready_rules),
                    "verifiedRuleIds": verified_rule_ids,
                    "missingRuleIds": sorted(set(missing_rule_ids)),
                    "missingFunctionNames": sorted(set(missing_function_names)),
                    "unresolvedFunctionNames": sorted(set(unresolved_function_names)),
                    "probeMode": "all-root-functions",
                    "reasonCode": "typedbSchemaFunctionMissing",
                    "reason": "TypeDB schema function is missing.",
                }
            return {
                "status": "ok",
                "available": probed_count == len(ready_rules),
                "probedCount": probed_count,
                "verifiedRuleCount": len(ready_rules),
                "probeMode": "all-root-functions",
                "verifiedRuleIds": verified_rule_ids,
            }
        except Exception as error:  # noqa: BLE001 - schema listing failures must block inference rather than look like missing rules.
            return {
                "status": "error",
                "available": False,
                "probedCount": probed_count,
                "verifiedRuleCount": len(ready_rules),
                "verifiedRuleIds": verified_rule_ids,
                "missingRuleIds": sorted(set(missing_rule_ids)),
                "missingFunctionNames": sorted(set(missing_function_names)),
                "unresolvedFunctionNames": sorted(set(unresolved_function_names)),
                "probeMode": "all-root-functions",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:180],
            }

    def probe_typedb_schema_function_definitions(self, definitions: Iterable[Dict[str, object]]) -> Dict[str, object]:
        function_names = sorted({
            str((item or {}).get("functionName") or "").strip()
            for item in definitions or []
            if str((item or {}).get("functionName") or "").strip()
        })
        if not function_names:
            return {"status": "empty", "available": True, "probedCount": 0, "missingFunctionNames": []}
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "status": "driver-missing",
                "available": False,
                "probedCount": 0,
                "missingFunctionNames": [],
                "reason": str(imported[1])[:180],
            }
        probed_count = 0
        missing_function_names: List[str] = []
        try:
            def operation():
                nonlocal probed_count, missing_function_names
                probed_count = 0
                missing_function_names = []
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    available_function_names = self.typedb_schema_function_names(driver)
                    for function_name in function_names:
                        if function_name not in available_function_names:
                            missing_function_names.append(function_name)
                            continue
                        probed_count += 1
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
            return {
                "status": "ok" if not missing_function_names else "missing",
                "available": not missing_function_names,
                "probedCount": probed_count,
                "verifiedFunctionCount": len(function_names),
                "missingFunctionNames": sorted(set(missing_function_names)),
                "probeMode": "all-generated-functions",
            }
        except Exception as error:  # noqa: BLE001 - do not mask read or connectivity failures as missing functions.
            return {
                "status": "error",
                "available": False,
                "probedCount": probed_count,
                "verifiedFunctionCount": len(function_names),
                "missingFunctionNames": sorted(set(missing_function_names)),
                "probeMode": "all-generated-functions",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:180],
            }

    def sync_typedb_native_rule_functions(self, rules: Iterable[GraphInferenceRule], force: bool = False) -> Dict[str, object]:
        if not self.address:
            return {"status": "disabled", "configured": False, "graphStore": "typedb", "syncedCount": 0}
        rules = list(rules or [])
        definitions: List[Dict[str, object]] = []
        skipped: List[Dict[str, object]] = []
        for rule in rules or []:
            rule_payload = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {})
            profile = typedb_native_rule_profile(rule_payload)
            if profile.get("status") != "ready":
                skipped.append({
                    "ruleId": str(rule.rule_id or ""),
                    "status": profile.get("status") or "blocked",
                    "reason": "Unsupported native-rule profile; schema function was not generated.",
                })
                continue
            definition = typedb_native_function_definition(rule_payload)
            if not definition.get("define"):
                skipped.append({
                    "ruleId": str(rule.rule_id or ""),
                    "status": "blocked",
                    "reason": str(definition.get("reason") or "Function definition was empty."),
                })
                continue
            rule_definitions = list(definition.get("functionDefinitions") or []) or [definition]
            for item in rule_definitions:
                definitions.append({
                    **item,
                    "ruleId": definition.get("ruleId") or item.get("ruleId"),
                    "nativeRuleId": definition.get("nativeRuleId") or item.get("nativeRuleId"),
                    "rootFunctionName": definition.get("functionName"),
                })
        sync_fingerprint = hashlib.sha256(json.dumps({
            "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
            "database": self.database,
            "functions": [
                {
                    "functionName": item.get("functionName"),
                    "define": item.get("define"),
                    "redefine": item.get("redefine"),
                }
                for item in definitions
            ],
            "skipped": skipped,
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        probe_result = self.probe_typedb_native_rule_functions(rules)
        if (
            not force
            and self._schema_function_sync_cache_key == sync_fingerprint
            and str(self._schema_function_sync_cache_result.get("status") or "") == "ok"
            and probe_result.get("available")
        ):
            # A restart can retain this Python object while dropping TypeDB's
            # schema functions. Confirm the deployed functions still exist
            # before trusting the in-process sync cache.
            cached_result = dict(self._schema_function_sync_cache_result)
            cached_result.update({
                "cached": True,
                "schemaFunctionSyncCached": True,
                "syncFingerprint": sync_fingerprint,
                "functionProbe": probe_result,
            })
            return cached_result
        if probe_result.get("available"):
            synced_rule_ids = sorted(set(
                str(item.get("ruleId") or "")
                for item in definitions
                if str(item.get("ruleId") or "")
            ))
            result = {
                "configured": True,
                "status": "ok",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "schemaFunctionSyncUsed": True,
                "schemaFunctionSyncCached": True,
                "schemaFunctionProbeUsed": True,
                "syncFingerprint": sync_fingerprint,
                "syncedCount": len(synced_rule_ids),
                "syncedFunctionCount": len(definitions),
                "skippedCount": len(skipped),
                "failedCount": 0,
                "syncedRules": [{"ruleId": item} for item in synced_rule_ids[:40]],
                "syncedFunctions": [
                    {
                        "ruleId": item.get("ruleId"),
                        "nativeRuleId": item.get("nativeRuleId"),
                        "schemaFunctionName": item.get("functionName"),
                        "rootSchemaFunctionName": item.get("rootFunctionName") or item.get("functionName"),
                        "schemaFunctionSyncStatus": "verified-existing",
                    }
                    for item in definitions[:60]
                ],
                "skippedRules": skipped[:40],
                "functionProbe": probe_result,
            }
            self._schema_function_sync_cache_key = sync_fingerprint
            self._schema_function_sync_cache_result = dict(result)
            return result
        missing_rule_ids = {
            str(item or "").strip()
            for item in (probe_result.get("missingRuleIds") or [])
            if str(item or "").strip()
        }
        if not missing_rule_ids:
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "schemaFunctionSyncUsed": True,
                "schemaFunctionProbeUsed": True,
                "syncedCount": 0,
                "syncedFunctionCount": 0,
                "skippedCount": len(skipped),
                "failedCount": 1,
                "skippedRules": skipped[:40],
                "functionProbe": probe_result,
                "reasonCode": str(probe_result.get("reasonCode") or "typedbSchemaFunctionProbeError"),
                "reason": str(probe_result.get("reason") or "TypeDB schema function probe failed.")[:220],
            }
        definitions_to_sync = [
            item for item in definitions
            if str(item.get("ruleId") or "") in missing_rule_ids
        ]
        if not definitions_to_sync:
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "schemaFunctionSyncUsed": True,
                "schemaFunctionProbeUsed": True,
                "syncedCount": 0,
                "syncedFunctionCount": 0,
                "skippedCount": len(skipped),
                "failedCount": 1,
                "skippedRules": skipped[:40],
                "functionProbe": probe_result,
                "reasonCode": "typedbSchemaFunctionDefinitionMissing",
                "reason": "Missing TypeDB schema function has no generated definition.",
            }
        definition_probe = self.probe_typedb_schema_function_definitions(definitions_to_sync)
        if str(definition_probe.get("status") or "") == "error":
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "schemaFunctionSyncUsed": True,
                "schemaFunctionProbeUsed": True,
                "syncedCount": 0,
                "syncedFunctionCount": 0,
                "skippedCount": len(skipped),
                "failedCount": 1,
                "skippedRules": skipped[:40],
                "functionProbe": probe_result,
                "functionDefinitionProbe": definition_probe,
                "reasonCode": str(definition_probe.get("reasonCode") or "typedbSchemaFunctionProbeError"),
                "reason": str(definition_probe.get("reason") or "TypeDB schema helper function probe failed.")[:220],
            }
        missing_function_names = {
            str(item or "").strip()
            for item in (definition_probe.get("missingFunctionNames") or [])
            if str(item or "").strip()
        }
        definitions_to_sync = [
            item for item in definitions_to_sync
            if str(item.get("functionName") or "") in missing_function_names
        ]
        if not definitions_to_sync:
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "schemaFunctionSyncUsed": True,
                "schemaFunctionProbeUsed": True,
                "syncedCount": 0,
                "syncedFunctionCount": 0,
                "skippedCount": len(skipped),
                "failedCount": 1,
                "skippedRules": skipped[:40],
                "functionProbe": probe_result,
                "functionDefinitionProbe": definition_probe,
                "reasonCode": "typedbSchemaFunctionVerificationMismatch",
                "reason": "TypeDB root function is unavailable although its generated definitions are present.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "configured": True,
                "status": "driver-missing",
                "graphStore": "typedb",
                "syncedCount": 0,
                "reason": "typedb-driver Python package is not installed: " + str(imported[1])[:160],
            }
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]

        def is_already_existing_schema_function(error: Exception) -> bool:
            error_text = str(error).lower()
            return "already exists" in error_text or "with name" in error_text

        synced: List[Dict[str, object]] = []
        failed: List[Dict[str, object]] = []
        try:
            def sync_definitions_batch(definitions_batch: List[Dict[str, object]]) -> List[Dict[str, object]]:
                """Install an entirely new function set in one schema commit.

                A clean TypeDB database has no generated functions, so opening
                one schema transaction per rule repeatedly recompiles a growing
                schema.  The all-missing case is deterministic and has no
                duplicate race to recover from; committing it as one batch
                makes initial provisioning bounded. Mixed/retry cases retain
                the safer one-definition transaction below.
                """
                def operation():
                    driver = self.open_driver(imported)
                    try:
                        self.ensure_database(driver)
                        self.ensure_schema(driver, imported)
                        timeout = max(
                            self.schema_operation_timeout_seconds(),
                            min(180.0, float(len(definitions_batch)) * 2.0),
                        )
                        with typedb_operation_timeout(timeout, "TypeDB initial schema function sync"):
                            with driver.transaction(self.database, TransactionType.SCHEMA) as tx:
                                for definition in definitions_batch:
                                    tx.query(str(definition.get("define") or "")).resolve()
                                tx.commit()
                        return [
                            {
                                "ruleId": definition.get("ruleId"),
                                "nativeRuleId": definition.get("nativeRuleId"),
                                "schemaFunctionName": definition.get("functionName"),
                                "rootSchemaFunctionName": definition.get("rootFunctionName") or definition.get("functionName"),
                                "schemaFunctionSyncStatus": "defined-batch",
                            }
                            for definition in definitions_batch
                        ]
                    finally:
                        self.close_driver(driver)

                return self.with_typedb_retries(operation)

            def sync_definition(definition: Dict[str, object]) -> Dict[str, object]:
                """Define one content-addressed function in its own schema transaction.

                TypeDB invalidates a schema transaction after a duplicate
                definition error.  Retrying a large shared transaction then
                makes every successfully committed function look like a
                failure.  Per-definition transactions keep a restart or an
                interrupted seed idempotent while preserving the generated
                function name as the deployment key.
                """
                def operation():
                    driver = self.open_driver(imported)
                    try:
                        self.ensure_database(driver)
                        self.ensure_schema(driver, imported)
                        try:
                            with typedb_operation_timeout(self.schema_operation_timeout_seconds(), "TypeDB schema function sync"):
                                with driver.transaction(self.database, TransactionType.SCHEMA) as tx:
                                    tx.query(str(definition.get("define") or "")).resolve()
                                    tx.commit()
                            return "defined"
                        except Exception as error:  # noqa: BLE001 - duplicate content-addressed functions are already deployed.
                            if is_already_existing_schema_function(error):
                                return "already-exists"
                            raise
                    finally:
                        self.close_driver(driver)

                return {
                    "ruleId": definition.get("ruleId"),
                    "nativeRuleId": definition.get("nativeRuleId"),
                    "schemaFunctionName": definition.get("functionName"),
                    "rootSchemaFunctionName": definition.get("rootFunctionName") or definition.get("functionName"),
                    "schemaFunctionSyncStatus": self.with_typedb_retries(operation),
                }

            synced.clear()
            if len(definitions_to_sync) > 1 and len(definitions_to_sync) == len(missing_function_names):
                try:
                    synced.extend(sync_definitions_batch(definitions_to_sync))
                except Exception:
                    # A batch can be invalidated by a concurrent schema writer
                    # or one unexpected legacy definition. Re-open independent
                    # transactions so already-installed functions are treated
                    # as idempotent rather than failing the whole RuleBox.
                    synced.clear()
                    for definition in definitions_to_sync:
                        synced.append(sync_definition(definition))
            else:
                for definition in definitions_to_sync:
                    synced.append(sync_definition(definition))
        except Exception as error:  # noqa: BLE001 - caller must block investment inference.
            failed.append({
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            })
            synced_rule_ids = sorted(set(str(item.get("ruleId") or "") for item in synced if str(item.get("ruleId") or "")))
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "schemaFunctionSyncUsed": True,
                "syncedCount": len(synced_rule_ids),
                "syncedFunctionCount": len(synced),
                "skippedCount": len(skipped),
                "failedCount": len(failed),
                "syncedRules": [{"ruleId": item} for item in synced_rule_ids[:40]],
                "syncedFunctions": synced[:60],
                "skippedRules": skipped[:40],
                "failedRules": failed[:10],
                "functionProbe": probe_result,
                "functionDefinitionProbe": definition_probe,
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }
        verification_result = self.probe_typedb_native_rule_functions(rules)
        if not verification_result.get("available"):
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "schemaFunctionSyncUsed": True,
                "schemaFunctionProbeUsed": True,
                "syncedCount": len(sorted(set(str(item.get("ruleId") or "") for item in synced if str(item.get("ruleId") or "")))),
                "syncedFunctionCount": len(synced),
                "skippedCount": len(skipped),
                "failedCount": 1,
                "syncedRules": [{"ruleId": item} for item in sorted(set(str(item.get("ruleId") or "") for item in synced if str(item.get("ruleId") or "")))[:40]],
                "syncedFunctions": synced[:60],
                "skippedRules": skipped[:40],
                "functionProbe": probe_result,
                "functionDefinitionProbe": definition_probe,
                "verificationProbe": verification_result,
                "reasonCode": str(verification_result.get("reasonCode") or "typedbSchemaFunctionVerificationError"),
                "reason": "TypeDB schema function sync did not verify every executable rule.",
            }
        synced_rule_ids = sorted(set(str(item.get("ruleId") or "") for item in synced if str(item.get("ruleId") or "")))
        result = {
            "configured": True,
            "status": "ok",
            "graphStore": "typedb",
            "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
            "schemaFunctionSyncUsed": True,
            "schemaFunctionSyncCached": False,
            "syncFingerprint": sync_fingerprint,
            "syncedCount": len(synced_rule_ids),
            "syncedFunctionCount": len(synced),
            "skippedCount": len(skipped),
            "failedCount": 0,
            "syncedRules": [{"ruleId": item} for item in synced_rule_ids[:40]],
            "syncedFunctions": synced[:60],
            "skippedRules": skipped[:40],
            "functionProbe": probe_result,
            "functionDefinitionProbe": definition_probe,
            "verificationProbe": verification_result,
        }
        self._schema_function_sync_cache_key = sync_fingerprint
        self._schema_function_sync_cache_result = dict(result)
        return result

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        """Run native rules under the same durable writer boundary as ABox swaps.

        A native run writes a generation candidate and atomically replaces the
        active InferenceBox marker.  It must not overlap another ABox
        activation or direct RuleBox invocation, otherwise two otherwise valid
        candidates can prune or publish around each other.
        """
        if not self.address:
            return NullTypeDBOntologyGraphRepository().run_rulebox(payload)
        values = dict(payload or {})
        native_execution_value = values.get("typedbNativeRuleExecutionEnabled")
        if native_execution_value is None:
            native_execution_enabled = self.native_rule_execution_enabled()
        else:
            native_execution_enabled = typedb_bool(native_execution_value)
        if not native_execution_enabled or not self._inference_write_lease_enabled:
            return self._run_rulebox_unlocked(values)

        supplied_owner = str(values.pop("_inferenceWriteLeaseOwner", "") or "").strip()
        supplied_lease = bool(supplied_owner)
        if supplied_lease:
            current = self.scoped_abox_write_lease_status()
            if (
                str(current.get("status") or "") == "held"
                and str(current.get("leaseOwner") or "") == supplied_owner
            ):
                result = self._run_rulebox_unlocked(values)
                if isinstance(result, dict):
                    result["inferenceWriteLease"] = {
                        "status": "adopted",
                        "leaseOwner": supplied_owner,
                        "managedBy": "ontology-projection",
                    }
                return result
            return {
                "configured": True,
                "status": "invalid-inference-write-lease",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reason": "Projection-owned TypeDB inference lease could not be verified.",
                "nativeTypeDbReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "inferenceWriteLease": {
                    "status": str(current.get("status") or "missing"),
                    "leaseOwner": str(current.get("leaseOwner") or ""),
                },
            }

        lease = self.acquire_scoped_abox_write_lease("inferencebox-native-rule")
        if not lease.get("acquired"):
            return {
                "configured": True,
                "status": "deferred-inference-write-lease",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reason": "Another ABox activation or native InferenceBox generation is running.",
                "nativeTypeDbReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "preservedPreviousInference": True,
                "inferenceWriteLease": {
                    key: value
                    for key, value in dict(lease or {}).items()
                    if key != "propertiesJson"
                },
            }
        try:
            result = self._run_rulebox_unlocked(values)
        finally:
            release = self.release_scoped_abox_write_lease(lease)
        if isinstance(result, dict):
            result["inferenceWriteLease"] = {
                key: value
                for key, value in dict(lease or {}).items()
                if key != "propertiesJson"
            }
            result["inferenceWriteLeaseRelease"] = release
        return result

    def _run_rulebox_unlocked(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().run_rulebox(payload)
        # A rule run owns its diagnostic window.  Nested reads made while
        # preserving a previous InferenceBox deliberately pass
        # ``reset_metrics=False`` to their own snapshot method.
        self.reset_query_metrics()
        payload = payload if isinstance(payload, dict) else {}
        if "typedbNativeRuleExecutionEnabled" in payload:
            native_execution_enabled = typedb_bool(payload.get("typedbNativeRuleExecutionEnabled"))
        else:
            native_execution_enabled = self.native_rule_execution_enabled()
        if not native_execution_enabled:
            return {
                "configured": True,
                "status": "skipped",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reason": "TypeDB native rule execution is disabled for this runtime path.",
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbSchemaFunctionUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "pythonCompatibilityReasonerUsed": False,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
            }
        target_symbols = clean_symbols_from_payload(
            payload.get("symbols")
            or payload.get("targetSymbols")
            or payload.get("changedSymbols")
        )
        force_clear_requested = typedb_bool(payload.get("forceClearInference"))
        if "forceClearInference" not in payload:
            force_clear_requested = typedb_bool(payload.get("clearInference"))
        destructive_clear_allowed = typedb_bool(payload.get("allowDestructiveInferenceClear"))
        prune_requested = typedb_bool(payload.get("pruneOldGenerations")) if "pruneOldGenerations" in payload else True
        keep_generation_count = max(1, int(number_or_none(payload.get("keepGenerationCount")) or self.inference_generation_keep_count))
        force_schema_function_sync = (
            typedb_bool(payload.get("forceSchemaFunctionSync"))
            or typedb_bool(payload.get("forceRuleFunctionSync"))
        )
        generation_id = str(payload.get("generationId") or inference_generation_id())
        generation_at = utc_now()
        clear_requested = force_clear_requested and destructive_clear_allowed
        clear_result = {}
        if force_clear_requested and not clear_requested:
            clear_result = {
                "configured": True,
                "status": "skipped",
                "graphStore": "typedb",
                "reason": "InferenceBox is generation-scoped; destructive clear is skipped unless allowDestructiveInferenceClear is true.",
                "preservedPreviousInference": True,
            }
        try:
            abox_available = self.has_box_rows("ABox")
            abox_metadata = self.active_abox_metadata() if abox_available else {}
        except Exception as error:  # noqa: BLE001 - report TypeDB read failures through diagnostics.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reasonCode": typedb_error_code(error),
                "reason": "TypeDB ABox 조회 실패: " + str(error)[:180],
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "clearResult": clear_result,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
            }
        if not abox_available:
            return {
                "configured": True,
                "status": "missing-abox",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reason": "TypeDB에 실행 가능한 ABox 그래프가 없습니다.",
                "statementCount": 0,
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "clearResult": clear_result,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
            }
        if str(abox_metadata.get("status") or "") != "ok":
            return {
                "configured": True,
                "status": "incomplete-abox",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reason": "TypeDB ABox 저장이 아직 완료되지 않아 투자 추론을 보류했습니다. " + str(abox_metadata.get("reason") or "완료 표식 또는 저장 건수를 다시 확인해야 합니다.")[:180],
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "clearResult": clear_result,
                "aboxMetadata": abox_metadata,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
            }
        snapshot = self.rulebox_snapshot()
        rules = snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []
        rulebox_metadata = rulebox_runtime_metadata(rules)
        requested_impact_plan = payload.get("inferenceImpactPlan")
        if isinstance(requested_impact_plan, dict) and requested_impact_plan:
            compact_impact_plan = compact_inference_impact_plan(requested_impact_plan)
            rulebox_metadata.update({
                "inferenceImpactPlan": compact_impact_plan,
                "impactPlanVersion": str(compact_impact_plan.get("version") or ""),
                "ruleExecutionScope": str(compact_impact_plan.get("ruleExecutionScope") or "complete-native-evaluation"),
                "nativeRuleSelectionApplied": bool(compact_impact_plan.get("nativeRuleSelectionApplied")),
            })
        native_profile = typedb_native_reasoning_profile(rules)
        rulebox_metadata.update(typedb_native_profile_metadata(native_profile))
        if str(snapshot.get("status") or "") != "ok" or not rules:
            return {
                "configured": True,
                "status": "rulebox-not-ready",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reason": str(snapshot.get("reason") or "TypeDB RuleBox rules are not available."),
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "clearResult": clear_result,
                "nativeReasoningProfile": native_profile,
                "ruleboxMetadata": rulebox_metadata,
                **rulebox_metadata,
            }
        try:
            parsed_rules = rulebox_rules_from_payload({"rules": rules})
            impact_plan = compact_inference_impact_plan(requested_impact_plan or {})
            selection_requested = typedb_bool(payload.get("typedbNativeRuleSelectionEnabled")) if "typedbNativeRuleSelectionEnabled" in payload else bool(impact_plan.get("nativeRuleSelectionEligible"))
            rule_selection = typedb_native_rule_execution_selection(
                parsed_rules,
                candidate_rule_ids=impact_plan.get("candidateRuleIds") or [],
                prior_matched_rule_ids=payload.get("priorMatchedRuleIds") or [],
                eligible=selection_requested and bool(impact_plan.get("nativeRuleSelectionEligible")),
                prior_inference_reusable=typedb_bool(payload.get("priorInferenceReusable")),
                global_impact=bool(impact_plan.get("globalImpact")),
            )
            execution_rules = list(rule_selection.get("selectedRules") or parsed_rules)
            # TypeDB schema functions are the only runtime investment-rule
            # evaluator. This also repairs a database restarted without its
            # compiled functions before a monitoring cycle can silently lose
            # every changed holding.
            function_sync_result = self.sync_typedb_native_rule_functions(
                parsed_rules,
                force=force_schema_function_sync,
            )
            runtime_rulebox_metadata = dict(rulebox_metadata)
            runtime_rulebox_metadata.update({
                "targetSymbols": target_symbols,
                "incrementalScope": "symbols" if target_symbols else "all-symbols",
                "typedbSchemaFunctionSyncStatus": str(function_sync_result.get("status") or ""),
                "typedbSchemaFunctionSyncCached": bool(function_sync_result.get("schemaFunctionSyncCached")),
                "typedbSchemaFunctionSyncedCount": int(number_or_none(function_sync_result.get("syncedCount")) or 0),
                "typedbSchemaFunctionSkippedCount": int(number_or_none(function_sync_result.get("skippedCount")) or 0),
                "typedbSchemaFunctionFailedCount": int(number_or_none(function_sync_result.get("failedCount")) or 0),
                "typedbSchemaFunctionUsed": str(function_sync_result.get("status") or "") == "ok",
                "typeDbFunctionReasoningUsed": str(function_sync_result.get("status") or "") == "ok",
                "typedbNativeExecutionMode": (
                    "typedb-schema-function-dependency-selected"
                    if bool(rule_selection.get("selectionApplied"))
                    else "typedb-schema-function-filtered"
                    if target_symbols
                    else "typedb-schema-function"
                ),
                "ruleExecutionScope": (
                    "dependency-selected-native-evaluation"
                    if bool(rule_selection.get("selectionApplied"))
                    else "complete-native-evaluation"
                ),
                "nativeRuleSelectionApplied": bool(rule_selection.get("selectionApplied")),
                "nativeRuleSelectionFallbackReason": str(rule_selection.get("fallbackReason") or ""),
                "nativeRuleSelectionCandidateCount": len(rule_selection.get("candidateRuleIds") or []),
                "nativeRuleSelectionPriorMatchedCount": len(rule_selection.get("priorMatchedRuleIds") or []),
                "nativeRuleSelectionExecutedCount": len(rule_selection.get("selectedRuleIds") or []),
                "nativeRuleSelectionDeferredCount": len(rule_selection.get("deferredRuleIds") or []),
                "nativeRuleSelectionExecutedRuleIds": list(rule_selection.get("selectedRuleIds") or [])[:80],
                "nativeRuleSelectionDeferredRuleIds": list(rule_selection.get("deferredRuleIds") or [])[:80],
                "pythonCompatibilityReasonerUsed": False,
            })
            if str(function_sync_result.get("status") or "") != "ok":
                return {
                    "configured": True,
                    "status": "error",
                    "graphStore": "typedb",
                    "source": "typedbNativeRule",
                    "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                    "reasonCode": str(function_sync_result.get("reasonCode") or "typedbSchemaFunctionSyncError"),
                    "reason": "TypeDB schema function 동기화 실패: " + str(function_sync_result.get("reason") or function_sync_result.get("status") or "")[:180],
                    "statementCount": 0,
                    "relationTypes": [],
                    "nativeTypeDbReasoningUsed": False,
                    "typedbNativeFunctionReasoningUsed": False,
                    "typedbSchemaFunctionUsed": False,
                    "typedbBootstrapReasoningUsed": False,
                    "pythonBootstrapDisabled": True,
                    "pythonCompatibilityReasonerUsed": False,
                    "clearResult": clear_result,
                    "nativeReasoningProfile": native_profile,
                    "functionSyncResult": function_sync_result,
                    "ruleboxMetadata": runtime_rulebox_metadata,
                    "typedbQueryMetrics": self.query_metrics_snapshot(),
                    **runtime_rulebox_metadata,
                }
            native_match_result = self.match_typedb_native_rules(
                execution_rules,
                target_symbols=target_symbols,
                use_schema_functions=True,
            )
            native_query_used = str(native_match_result.get("status") or "") == "ok"
            runtime_rulebox_metadata.update({
                "typedbNativeRuleQueryStatus": str(native_match_result.get("status") or ""),
                "typedbNativeRuleQueryUsed": bool(native_match_result.get("nativeQueryUsed")),
                "typedbSchemaFunctionQueryUsed": bool(native_match_result.get("schemaFunctionUsed")),
                "typedbNativeRuleMatchedCount": int(number_or_none(native_match_result.get("matchedCount")) or 0),
                "typedbNativeRuleExecutedCount": int(number_or_none(native_match_result.get("executedRuleCount")) or 0),
                "typedbNativeRuleSkippedCount": int(number_or_none(native_match_result.get("skippedRuleCount")) or 0),
                "pythonCompatibilityReasonerUsed": False,
            })
            if not native_query_used:
                runtime_rulebox_metadata["typedbNativeRuleQueryReason"] = str(native_match_result.get("reason") or "")
                return {
                    "configured": True,
                    "status": "error",
                    "graphStore": "typedb",
                    "source": "typedbNativeRule",
                    "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                    "reasonCode": str(native_match_result.get("reasonCode") or "typedbSchemaFunctionQueryError"),
                    "reason": "TypeDB schema function 실행 실패: " + str(native_match_result.get("reason") or "")[:180],
                    "statementCount": 0,
                    "relationTypes": [],
                    "nativeTypeDbReasoningUsed": False,
                    "typedbNativeFunctionReasoningUsed": False,
                    "typedbSchemaFunctionUsed": False,
                    "typedbBootstrapReasoningUsed": False,
                    "pythonBootstrapDisabled": True,
                    "pythonCompatibilityReasonerUsed": False,
                    "clearResult": clear_result,
                    "nativeReasoningProfile": native_profile,
                    "functionSyncResult": function_sync_result,
                    "nativeMatchResult": native_match_result,
                    "ruleboxMetadata": runtime_rulebox_metadata,
                    "typedbQueryMetrics": self.query_metrics_snapshot(),
                    **runtime_rulebox_metadata,
                }
            graph = self.load_graph_for_native_matches(native_match_result, execution_rules)
            before_entities = len(graph.entities)
            before_relations = len(graph.relations)
            matched_source_ids = {
                str(item.get("sourceId") or "").strip()
                for item in native_match_result.get("matches") or []
                if isinstance(item, dict) and str(item.get("sourceId") or "").strip()
            }
            source_entities = [
                item for item in graph.entities
                if str(item.entity_id or "").strip() in matched_source_ids
            ]
            scoped_active_abox = (
                str(abox_metadata.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
            )
            active_abox_generation_id = typedb_abox_inference_generation_id(abox_metadata)
            if scoped_active_abox:
                # Individual scopes intentionally have different immutable
                # generation IDs and may be reused by a later Manifest. The
                # JSON provenance on a reused fact can therefore contain the
                # Manifest that first wrote it. Native queries already loaded
                # these sources through active scope pointers, so completeness
                # of that active member set, not the historical JSON field,
                # proves the one live Worldview source identity.
                source_entity_ids = {
                    str(item.entity_id or "").strip()
                    for item in source_entities
                    if str(item.entity_id or "").strip()
                }
                stored_source_manifest_ids = sorted({
                    str((item.properties or {}).get("worldviewManifestId") or "").strip()
                    for item in source_entities
                    if str((item.properties or {}).get("worldviewManifestId") or "").strip()
                })
                missing_source_generation = bool(matched_source_ids) and (
                    source_entity_ids != matched_source_ids
                    or any(bool((item.properties or {}).get("queryFallback")) for item in source_entities)
                )
                source_generation_valid = (
                    bool(active_abox_generation_id)
                    and not missing_source_generation
                )
                source_abox_snapshot_ids = [active_abox_generation_id] if source_generation_valid else []
                runtime_rulebox_metadata["sourceAboxManifestId"] = active_abox_generation_id
                runtime_rulebox_metadata["sourceAboxStoredManifestIds"] = stored_source_manifest_ids
                runtime_rulebox_metadata["sourceAboxMembershipValidation"] = "active-scope-pointer"
            else:
                source_abox_snapshot_ids = sorted({
                    str((item.properties or {}).get("aboxSnapshotId") or (item.properties or {}).get("snapshotId") or "").strip()
                    for item in source_entities
                    if str((item.properties or {}).get("aboxSnapshotId") or (item.properties or {}).get("snapshotId") or "").strip()
                })
                missing_source_generation = bool(matched_source_ids) and (
                    len(source_entities) != len(matched_source_ids)
                    or any(
                        not str((item.properties or {}).get("aboxSnapshotId") or (item.properties or {}).get("snapshotId") or "").strip()
                        for item in source_entities
                    )
                )
                source_generation_valid = (
                    len(source_abox_snapshot_ids) == 1
                    and not missing_source_generation
                    and (
                        not active_abox_generation_id
                        or source_abox_snapshot_ids[0] == active_abox_generation_id
                    )
                )
            if source_generation_valid:
                runtime_rulebox_metadata["sourceAboxSnapshotId"] = active_abox_generation_id or source_abox_snapshot_ids[0]
            runtime_rulebox_metadata["sourceAboxSnapshotCount"] = len(source_abox_snapshot_ids)
            runtime_rulebox_metadata["sourceAboxGenerationMode"] = (
                "worldview-manifest" if scoped_active_abox else "snapshot"
            )
            # Keep the proof used by both successful and blocked native
            # materialization paths in the durable execution metadata. Scoped
            # facts may retain the manifest that first created them, so the
            # active scope pointer is the authoritative membership check.
            runtime_rulebox_metadata["sourceAboxGenerationValid"] = source_generation_valid
            runtime_rulebox_metadata["sourceAboxSnapshotIds"] = list(source_abox_snapshot_ids)
            materialize_typedb_native_matches(graph, execution_rules, native_match_result)
            inference_graph = typedb_inferencebox_graph(
                graph,
                generation_id=generation_id,
                generation_at=generation_at,
                rulebox_metadata=runtime_rulebox_metadata,
            )
            inferencebox_limit = max(80, min(500, int(number_or_none(payload.get("inferenceSnapshotLimit")) or 500)))
            invalid_abox_generation = bool(inference_graph.relations) and not source_generation_valid
            if invalid_abox_generation or not inference_graph.relations:
                previous_inferencebox = self.inferencebox_snapshot(
                    symbols=target_symbols,
                    limit=inferencebox_limit,
                    reset_metrics=False,
                )
                return {
                    "configured": True,
                    "status": "invalid-abox-generation" if invalid_abox_generation else "empty",
                    "graphStore": "typedb",
                    "source": "typedbNativeRule",
                    "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                    "reason": (
                        "원본 ABox 세대를 하나로 확인할 수 없어 새 InferenceBox를 활성화하지 않았습니다."
                        if invalid_abox_generation
                        else "TypeDB native rules matched no ABox facts; the previous InferenceBox remains active."
                    ),
                    "statementCount": 0,
                    "entityCount": 0,
                    "relationCount": 0,
                    "traceCount": 0,
                    "relationTypes": [],
                    "nativeTypeDbReasoningUsed": False,
                    "typedbNativeRuleReasoningUsed": False,
                    "typedbNativeFunctionReasoningUsed": False,
                    "typedbBootstrapReasoningUsed": False,
                    "pythonBootstrapDisabled": True,
                    "preservedPreviousInference": True,
                    "activatedGeneration": False,
                    "sourceAboxSnapshotIds": source_abox_snapshot_ids,
                    "sourceAboxGenerationMode": runtime_rulebox_metadata["sourceAboxGenerationMode"],
                    "sourceAboxGenerationValid": source_generation_valid,
                    "inferenceGenerationId": generation_id,
                    "inferenceGenerationAt": generation_at,
                    "targetSymbols": target_symbols,
                    "saveResult": {"saved": False, "status": "skipped-preserve-previous"},
                    "clearResult": clear_result,
                    "inferenceBox": previous_inferencebox,
                    "nativeReasoningProfile": native_profile,
                    "nativeMatchResult": {
                        key: native_match_result.get(key)
                        for key in [
                            "status", "reason", "reasonCode", "nativeQueryUsed", "schemaFunctionUsed",
                            "executedRuleCount", "skippedRuleCount", "matchedCount", "executedRules",
                            "skippedRules", "nativeExecutionMode", "readTransactionCount",
                            "readQueryCount", "executionPlan", "typedbQueryMetrics",
                        ]
                        if key in native_match_result
                    },
                    "ruleboxMetadata": runtime_rulebox_metadata,
                    "typedbQueryMetrics": self.query_metrics_snapshot(),
                    **runtime_rulebox_metadata,
                }
            if clear_requested:
                clear_result = self.clear_inferencebox()
                if str(clear_result.get("status") or "") != "ok":
                    return {
                        "configured": True,
                        "status": "error",
                        "graphStore": "typedb",
                        "source": "typedbNativeRule",
                        "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                        "reasonCode": str(clear_result.get("reasonCode") or "typedbClearError"),
                        "reason": "TypeDB InferenceBox 초기화 실패: " + str(clear_result.get("reason") or clear_result.get("status") or ""),
                        "statementCount": 0,
                        "relationTypes": [],
                        "nativeTypeDbReasoningUsed": False,
                        "typedbNativeFunctionReasoningUsed": False,
                        "typedbBootstrapReasoningUsed": False,
                        "pythonBootstrapDisabled": True,
                        "clearResult": clear_result,
                        "nativeReasoningProfile": native_profile,
                        "functionSyncResult": function_sync_result,
                        "nativeMatchResult": native_match_result,
                        "ruleboxMetadata": runtime_rulebox_metadata,
                        **runtime_rulebox_metadata,
                    }
            save_result = self.write_inferencebox_graph(inference_graph)
        except Exception as error:  # noqa: BLE001 - expose materialization failures to monitoring diagnostics.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_REASONING_MODE,
                "reasonCode": typedb_error_code(error),
                "reason": "TypeDB native rule materialization failed: " + str(error)[:180],
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "clearResult": clear_result,
                "nativeReasoningProfile": native_profile,
                "ruleboxMetadata": rulebox_metadata,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
                **rulebox_metadata,
            }
        relation_types = sorted({
            str(item.relation_type or "")
            for item in inference_graph.relations
            if str(item.relation_type or "").strip()
        })
        materialized_entity_count = len(inference_graph.entities) + len(inference_graph.evidence) + len(inference_graph.beliefs)
        materialized_relation_count = len(inference_graph.relations)
        has_materialized_relations = materialized_relation_count > 0
        saved_ok = bool(save_result.get("saved"))
        prune_result = self.prune_inferencebox_generations(generation_id, keep_count=keep_generation_count) if saved_ok and prune_requested else {}
        inferencebox_payload = self.inferencebox_snapshot_from_graph(inference_graph, target_symbols, inferencebox_limit)
        return {
            "configured": True,
            "status": ("ok" if has_materialized_relations else "empty") if saved_ok else str(save_result.get("status") or "error"),
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_REASONING_MODE,
            "reason": ("" if has_materialized_relations else "TypeDB native rules matched no ABox facts.") if saved_ok else str(save_result.get("reason") or ""),
            "statementCount": materialized_entity_count + materialized_relation_count,
            "entityCount": materialized_entity_count,
            "relationCount": materialized_relation_count,
            "traceCount": len([item for item in inference_graph.entities if item.kind == "inference-trace"]),
            "relationTypes": relation_types,
            "nativeTypeDbReasoningUsed": saved_ok and has_materialized_relations,
            "typedbNativeRuleReasoningUsed": saved_ok and has_materialized_relations,
            "typedbNativeRuleQueryUsed": bool(native_match_result.get("nativeQueryUsed")),
            "typedbSchemaFunctionQueryUsed": bool(native_match_result.get("schemaFunctionUsed")),
            "typedbNativeRuleQueryStatus": str(native_match_result.get("status") or ""),
            "typedbNativeRuleMatchedCount": int(number_or_none(native_match_result.get("matchedCount")) or 0),
            "typedbNativeRuleExecutedCount": int(number_or_none(native_match_result.get("executedRuleCount")) or 0),
            "typedbNativeRuleSkippedCount": int(number_or_none(native_match_result.get("skippedRuleCount")) or 0),
            "typedbSchemaFunctionUsed": bool(function_sync_result.get("status") == "ok"),
            "typedbSchemaFunctionSyncedCount": int(number_or_none(function_sync_result.get("syncedCount")) or 0),
            "pythonCompatibilityReasonerUsed": False,
            "typedbNativeFunctionReasoningUsed": saved_ok and has_materialized_relations and bool(native_match_result.get("schemaFunctionUsed")),
            "typeDbFunctionReasoningUsed": saved_ok and has_materialized_relations and bool(native_match_result.get("schemaFunctionUsed")),
            "typedbNativeReasoningReady": native_profile.get("status") in {"ready", "partial"},
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "materializationSource": TYPEDB_NATIVE_MATERIALIZATION_SOURCE,
            "inferenceGenerationId": generation_id,
            "inferenceGenerationAt": generation_at,
            "targetSymbols": target_symbols,
            "incrementalScope": "symbols" if target_symbols else "all-symbols",
            "readAboxEntityCount": before_entities,
            "readAboxRelationCount": before_relations,
            "clearResult": clear_result,
            "pruneResult": prune_result,
            "saveResult": save_result,
            "functionSyncResult": {
                key: function_sync_result.get(key)
                for key in [
                    "status", "reason", "reasonCode", "syncedCount", "skippedCount", "failedCount",
                    "syncedRules", "skippedRules", "schemaFunctionSyncCached",
                    "schemaFunctionProbeUsed", "functionProbe",
                ]
                if key in function_sync_result
            },
            "nativeMatchResult": {
                key: native_match_result.get(key)
                for key in [
                    "status", "reason", "reasonCode", "nativeQueryUsed", "schemaFunctionUsed",
                    "executedRuleCount", "skippedRuleCount", "matchedCount", "executedRules",
                    "skippedRules", "nativeExecutionMode", "readTransactionCount", "readQueryCount",
                    "executionPlan", "typedbQueryMetrics",
                ]
                if key in native_match_result
            },
            "typedbQueryMetrics": self.query_metrics_snapshot(),
            "inferenceBox": inferencebox_payload,
            "nativeReasoningProfile": native_profile,
            "ruleboxMetadata": runtime_rulebox_metadata,
            **runtime_rulebox_metadata,
        }

    def validate_rulebox_materialization(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload if isinstance(payload, dict) else {}
        if not self.address:
            return NullTypeDBOntologyGraphRepository().validate_rulebox_materialization(payload)
        self.reset_query_metrics()
        target_symbols = clean_symbols_from_payload(
            payload.get("symbols")
            or payload.get("targetSymbols")
            or payload.get("changedSymbols")
        )
        try:
            candidate_rules = rulebox_rules_from_payload({"rules": payload.get("rules") or []})
        except ValueError as error:
            return {
                "configured": True,
                "status": "invalid-rulebox",
                "graphStore": "typedb",
                "reason": str(error),
                "validationOnly": True,
                "mutatedOperationalRuleBox": False,
                "wroteInferenceBox": False,
                "candidateRuleCount": 0,
                "baselineInferenceBox": {},
                "diff": materialization_preview_diff_payload({}, 0, 0, False),
            }
        enabled_rules = [
            GraphInferenceRule.from_dict({**rule.to_dict(), "enabled": True})
            for rule in candidate_rules
        ]
        native_profile = typedb_native_reasoning_profile(enabled_rules)
        try:
            abox_available = self.has_box_rows("ABox")
            abox_metadata = self.active_abox_metadata() if abox_available else {}
        except Exception as error:  # noqa: BLE001 - expose TypeDB read failures to strategy validation.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": "TypeDB ABox 조회 실패: " + str(error)[:180],
                "validationOnly": True,
                "mutatedOperationalRuleBox": False,
                "wroteInferenceBox": False,
                "candidateRuleCount": len(enabled_rules),
                "nativeReasoningProfile": native_profile,
                "baselineInferenceBox": {},
                "diff": materialization_preview_diff_payload({}, 0, len(enabled_rules), False),
                "typedbQueryMetrics": self.query_metrics_snapshot(),
            }
        if not abox_available:
            return {
                "configured": True,
                "status": "missing-abox",
                "graphStore": "typedb",
                "reason": "TypeDB에 실행 가능한 ABox 그래프가 없습니다.",
                "validationOnly": True,
                "mutatedOperationalRuleBox": False,
                "wroteInferenceBox": False,
                "candidateRuleCount": len(enabled_rules),
                "nativeReasoningProfile": native_profile,
                "baselineInferenceBox": {},
                "diff": materialization_preview_diff_payload({}, 0, len(enabled_rules), False),
                "typedbQueryMetrics": self.query_metrics_snapshot(),
            }
        if str(abox_metadata.get("status") or "") != "ok":
            return {
                "configured": True,
                "status": "incomplete-abox",
                "graphStore": "typedb",
                "reasonCode": "typedbIncompleteAbox",
                "reason": "TypeDB ABox 저장이 아직 완료되지 않아 후보 규칙 검증을 보류했습니다. " + str(abox_metadata.get("reason") or "완료 표식 또는 저장 건수를 다시 확인해야 합니다.")[:180],
                "validationOnly": True,
                "mutatedOperationalRuleBox": False,
                "wroteInferenceBox": False,
                "candidateRuleCount": len(enabled_rules),
                "nativeReasoningProfile": native_profile,
                "aboxMetadata": abox_metadata,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
            }
        try:
            baseline_inferencebox = self.inferencebox_snapshot_from_typedb(target_symbols, 80)
        except Exception as error:  # noqa: BLE001 - baseline diff is diagnostic only.
            baseline_inferencebox = {
                "status": "error",
                "graphStore": "typedb",
                "source": "typedbInferenceBox",
                "reasonCode": typedb_error_code(error),
                "reason": "TypeDB InferenceBox 기준선 조회 실패: " + str(error)[:180],
                "relationCount": 0,
                "traceCount": 0,
            }
        try:
            force_sync = typedb_bool(payload.get("forceSchemaFunctionSync")) or typedb_bool(payload.get("forceRuleFunctionSync"))
            function_sync_result = self.sync_typedb_native_rule_functions(enabled_rules, force=force_sync)
            if str(function_sync_result.get("status") or "") != "ok":
                return {
                    "configured": True,
                    "status": "error",
                    "graphStore": "typedb",
                    "reasonCode": str(function_sync_result.get("reasonCode") or "typedbSchemaFunctionSyncError"),
                    "reason": "TypeDB schema function 동기화 실패: " + str(function_sync_result.get("reason") or function_sync_result.get("status") or "")[:180],
                    "validationOnly": True,
                    "mutatedOperationalRuleBox": False,
                    "wroteInferenceBox": False,
                    "candidateRuleCount": len(enabled_rules),
                    "nativeReasoningProfile": native_profile,
                    "baselineInferenceBox": baseline_inferencebox,
                    "diff": materialization_preview_diff_payload(baseline_inferencebox, 0, len(enabled_rules), False),
                    "functionSyncResult": function_sync_result,
                    "typedbQueryMetrics": self.query_metrics_snapshot(),
                }
            native_match_result = self.match_typedb_native_rules(enabled_rules, target_symbols=target_symbols)
            native_query_used = str(native_match_result.get("status") or "") == "ok"
            matched_count = int(number_or_none(native_match_result.get("matchedCount")) or 0)
            return {
                "configured": True,
                "status": "ok" if native_query_used else "error",
                "graphStore": "typedb",
                "source": "typedbCandidateRulePreview",
                "reasoningMode": TYPEDB_NATIVE_REASONING_MODE,
                "reason": "" if native_query_used else "TypeDB schema function preview failed: " + str(native_match_result.get("reason") or "")[:180],
                "validationOnly": True,
                "mutatedOperationalRuleBox": False,
                "wroteInferenceBox": False,
                "candidateRuleCount": len(enabled_rules),
                "candidateRuleIds": [rule.rule_id for rule in enabled_rules],
                "targetSymbols": target_symbols,
                "matchedCount": matched_count,
                "baselineInferenceBox": baseline_inferencebox,
                "diff": materialization_preview_diff_payload(
                    baseline_inferencebox,
                    matched_count,
                    len(enabled_rules),
                    native_query_used,
                ),
                "nativeTypeDbReasoningUsed": native_query_used,
                "typedbNativeFunctionReasoningUsed": native_query_used and bool(native_match_result.get("schemaFunctionUsed")),
                "typedbSchemaFunctionUsed": bool(function_sync_result.get("status") == "ok"),
                "functionSyncResult": {
                    key: function_sync_result.get(key)
                    for key in [
                        "status", "reason", "reasonCode", "syncedCount", "skippedCount", "failedCount",
                        "syncedRules", "skippedRules", "schemaFunctionSyncCached",
                        "schemaFunctionProbeUsed", "functionProbe",
                    ]
                    if key in function_sync_result
                },
                "nativeMatchResult": {
                    key: native_match_result.get(key)
                    for key in [
                        "status", "reason", "reasonCode", "nativeQueryUsed", "schemaFunctionUsed",
                        "executedRuleCount", "skippedRuleCount", "matchedCount", "executedRules",
                        "skippedRules", "nativeExecutionMode", "readTransactionCount", "readQueryCount",
                        "executionPlan", "typedbQueryMetrics",
                    ]
                    if key in native_match_result
                },
                "nativeReasoningProfile": native_profile,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
            }
        except Exception as error:  # noqa: BLE001 - strategy validation is diagnostic, not runtime judgement.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": "TypeDB candidate rule preview failed: " + str(error)[:180],
                "validationOnly": True,
                "mutatedOperationalRuleBox": False,
                "wroteInferenceBox": False,
                "candidateRuleCount": len(enabled_rules),
                "nativeReasoningProfile": native_profile,
                "baselineInferenceBox": baseline_inferencebox if "baseline_inferencebox" in locals() else {},
                "diff": materialization_preview_diff_payload(
                    baseline_inferencebox if "baseline_inferencebox" in locals() else {},
                    0,
                    len(enabled_rules),
                    False,
                ),
                "typedbQueryMetrics": self.query_metrics_snapshot(),
            }

    def write_inferencebox_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        if not self.address:
            return {
                "configured": False,
                "saved": False,
                "status": "disabled",
                "graphStore": "typedb",
                "reason": "TypeDB ontology storage is not configured.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "configured": True,
                "saved": False,
                "status": "driver-missing",
                "graphStore": "typedb",
                "reason": "typedb-driver Python package is not installed: " + str(imported[1])[:160],
            }
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        node_rows = [
            row for row in self.node_rows(graph)
            if str(row.get("ontologyBox") or "") == "InferenceBox"
        ]
        relation_rows = [
            row for row in self.rows_for_relations(graph) + self.support_relation_rows(graph)
            if str(row.get("ontologyBox") or "") == "InferenceBox"
        ]
        updated_at = utc_now()
        queries = self.inferencebox_insert_queries(node_rows, relation_rows, updated_at)
        generation_id = str((graph.worldview or {}).get("inferenceGenerationId") or "").strip()
        marker_query = self.node_insert_query(
            inference_generation_marker_row(graph, node_rows, relation_rows, "candidate"),
            updated_at,
        ) if generation_id else ""
        statement_count = len(node_rows) + len([row for row in relation_rows if row.get("source") and row.get("target")])
        try:
            def operation():
                with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB InferenceBox graph save"):
                    driver = self.open_driver(imported)
                    try:
                        self.ensure_database(driver)
                        transaction_query_count = self.inferencebox_write_transaction_query_count()

                        def write_query_chunks(query_rows: Iterable[str]) -> None:
                            query_list = [str(query) for query in query_rows or [] if str(query or "").strip()]
                            for offset in range(0, len(query_list), transaction_query_count):
                                query_batch = query_list[offset: offset + transaction_query_count]

                                def write_batch():
                                    with driver.transaction(
                                        self.database,
                                        TransactionType.WRITE,
                                        options=self.write_transaction_options(),
                                    ) as tx:
                                        for query in query_batch:
                                            tx.query(query).resolve()
                                        tx.commit()

                                self.with_typedb_retries(write_batch)

                        if generation_id:
                            write_query_chunks(inference_generation_delete_queries(generation_id))
                        write_query_chunks(queries)
                        write_query_chunks([marker_query])
                    finally:
                        self.close_driver(driver)
            self.with_typedb_retries(operation)
            candidate_validation = self.validate_inference_generation_candidate(
                graph,
                generation_id,
                len(node_rows),
                len(relation_rows),
            ) if generation_id else {"status": "legacy", "valid": True}
            if not candidate_validation.get("valid"):
                return {
                    "configured": True,
                    "saved": False,
                    "status": "candidate-validation-failed",
                    "graphStore": "typedb",
                    "reason": str(candidate_validation.get("reason") or "InferenceBox candidate validation failed."),
                    "entityCount": len(node_rows),
                    "relationCount": len(relation_rows),
                    "statementCount": statement_count,
                    "batchCount": len(queries),
                    "insertMode": "batched-candidate",
                    "publicationStatus": "candidate",
                    "preservedPreviousInference": True,
                    "inferenceGenerationId": generation_id,
                    "candidateValidation": candidate_validation,
                }
            activation = self.activate_inference_generation(graph, node_rows, relation_rows) if generation_id else {"status": "legacy", "activated": True}
            if not activation.get("activated"):
                return {
                    "configured": True,
                    "saved": False,
                    "status": "activation-failed",
                    "graphStore": "typedb",
                    "reason": str(activation.get("reason") or "InferenceBox candidate activation failed."),
                    "entityCount": len(node_rows),
                    "relationCount": len(relation_rows),
                    "statementCount": statement_count,
                    "batchCount": len(queries),
                    "insertMode": "batched-candidate",
                    "publicationStatus": "candidate",
                    "preservedPreviousInference": True,
                    "inferenceGenerationId": generation_id,
                    "candidateValidation": candidate_validation,
                    "activation": activation,
                }
            return {
                "configured": True,
                "saved": True,
                "status": "ok",
                "graphStore": "typedb",
                "entityCount": len(node_rows),
                "relationCount": len(relation_rows),
                "statementCount": statement_count,
                "batchCount": len(queries),
                "insertMode": "batched-candidate-activation",
                "publicationStatus": "active" if marker_query else "legacy-unmarked",
                "inferenceGenerationId": generation_id,
                "inferenceGenerationAt": str((graph.worldview or {}).get("inferenceGenerationAt") or ""),
                "candidateValidation": candidate_validation,
                "activation": activation,
            }
        except Exception as error:  # noqa: BLE001 - materialization failure must be visible to diagnostics.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "entityCount": len(node_rows),
                "relationCount": len(relation_rows),
                "statementCount": statement_count,
                "batchCount": len(queries),
                "insertMode": "batched",
                "publicationStatus": "not-published",
                "inferenceGenerationId": generation_id,
            }

    def validate_inference_generation_candidate(
        self,
        graph: PortfolioOntology,
        generation_id: str,
        expected_entity_count: int,
        expected_relation_count: int,
    ) -> Dict[str, object]:
        entity_rows = self.read_inferencebox_entity_rows(generation_id, [], 0)
        relation_rows = self.read_inferencebox_relation_rows(generation_id, [], 0)
        metadata = inference_rulebox_metadata(entity_rows, relation_rows)
        expected_source_abox = str((graph.worldview or {}).get("sourceAboxSnapshotId") or metadata.get("sourceAboxSnapshotId") or "").strip()
        active_abox = self.active_abox_snapshot_id()
        actual_relations = len(relation_rows)
        actual_traces = len([row for row in entity_rows if str(row.get("kind") or "") == "inference-trace"])
        expected_traces = len([item for item in graph.entities if item.kind == "inference-trace"])
        reasons = []
        if expected_relation_count <= 0 or actual_relations < expected_relation_count:
            reasons.append("candidate-relation-count-mismatch")
        if expected_traces > 0 and actual_traces < expected_traces:
            reasons.append("candidate-trace-count-mismatch")
        if not expected_source_abox:
            reasons.append("candidate-source-abox-missing")
        elif not active_abox or expected_source_abox != active_abox:
            reasons.append("candidate-source-abox-not-active")
        return {
            "status": "ok" if not reasons else "invalid",
            "valid": not reasons,
            "reason": ", ".join(reasons),
            "generationId": generation_id,
            "expectedEntityCount": int(expected_entity_count or 0),
            "actualEntityCount": len(entity_rows),
            "expectedRelationCount": int(expected_relation_count or 0),
            "actualRelationCount": actual_relations,
            "expectedTraceCount": expected_traces,
            "actualTraceCount": actual_traces,
            "sourceAboxSnapshotId": expected_source_abox,
            "activeAboxSnapshotId": active_abox,
            "generationAligned": bool(expected_source_abox and expected_source_abox == active_abox),
        }

    def activate_inference_generation(
        self,
        graph: PortfolioOntology,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
    ) -> Dict[str, object]:
        generation_id = str((graph.worldview or {}).get("inferenceGenerationId") or "").strip()
        if not generation_id:
            return {"status": "invalid", "activated": False, "reason": "generation id is empty"}
        imported = self.driver_imports()
        if imported[0] is None:
            return {"status": "driver-missing", "activated": False, "reason": str(imported[1])[:180]}
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        active_marker_query = self.node_insert_query(
            inference_generation_marker_row(graph, node_rows, relation_rows, "active"),
            utc_now(),
        )
        delete_markers = [
            'match $n isa ontology-node, has ontology-box "InferenceBox", has ontology-kind "inference-generation"; delete $n;',
            'match $n isa ontology-node, has ontology-box "InferenceBox", has ontology-kind "inference-generation-candidate"; delete $n;',
        ]
        try:
            def operation():
                with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB InferenceBox generation activation"):
                    driver = self.open_driver(imported)
                    try:
                        self.ensure_database(driver)
                        with driver.transaction(self.database, TransactionType.WRITE) as tx:
                            for query in delete_markers:
                                tx.query(query).resolve()
                            tx.query(active_marker_query).resolve()
                            tx.commit()
                    finally:
                        self.close_driver(driver)
            self.with_typedb_retries(operation)
            return {
                "status": "ok",
                "activated": True,
                "activeGenerationId": generation_id,
                "activationMode": "validated-candidate-pointer-swap",
            }
        except Exception as error:  # noqa: BLE001 - preserve the previous active marker on transaction failure.
            return {
                "status": "error",
                "activated": False,
                "activeGenerationId": generation_id,
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "preservedPreviousInference": True,
            }

    def prune_inferencebox_generations(self, active_generation_id: str, keep_count: int = 2) -> Dict[str, object]:
        active_generation_id = str(active_generation_id or "").strip()
        if not active_generation_id:
            return {"configured": bool(self.address), "status": "skipped", "reason": "active generation id is empty"}
        try:
            records = self.read_inference_generation_records(published_only=False)
        except Exception as error:  # noqa: BLE001 - pruning must not fail materialization.
            return {"configured": True, "status": "error", "reason": str(error)[:180], "activeGenerationId": active_generation_id}
        if not records:
            return {"configured": True, "status": "skipped", "reason": "no generation-scoped InferenceBox rows", "activeGenerationId": active_generation_id}
        keep = {active_generation_id}
        published_records = [item for item in records if str(item.get("publicationStatus") or "active") in {"active", "published"}]
        for item in sorted(published_records, key=lambda row: str(row.get("latestAt") or ""), reverse=True)[: max(1, int(keep_count or 2))]:
            keep.add(str(item.get("generationId") or ""))
        prune_ids = [
            str(item.get("generationId") or "")
            for item in records
            if str(item.get("generationId") or "") and str(item.get("generationId") or "") not in keep
        ]
        if not prune_ids:
            return {
                "configured": True,
                "status": "ok",
                "activeGenerationId": active_generation_id,
                "keptGenerationCount": len(keep),
                "deletedGenerationCount": 0,
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {"configured": True, "status": "driver-missing", "reason": "typedb-driver Python package is not installed: " + str(imported[1])[:160]}
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        queries = []
        for generation_id in prune_ids:
            queries.append(
                "match $r isa ontology-assertion, has ontology-box \"InferenceBox\", has ontology-snapshot-id "
                + typedb_string(generation_id)
                + "; delete $r;"
            )
            queries.append(
                "match $n isa ontology-node, has ontology-box \"InferenceBox\", has ontology-snapshot-id "
                + typedb_string(generation_id)
                + "; delete $n;"
            )
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    with driver.transaction(self.database, TransactionType.WRITE) as tx:
                        for query in queries:
                            tx.query(query).resolve()
                        tx.commit()
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
            return {
                "configured": True,
                "status": "ok",
                "activeGenerationId": active_generation_id,
                "keptGenerationCount": len(keep),
                "deletedGenerationCount": len(prune_ids),
                "deletedGenerationIds": prune_ids[:20],
            }
        except Exception as error:  # noqa: BLE001 - pruning is non-critical but must be visible.
            return {
                "configured": True,
                "status": "error",
                "reason": str(error)[:220],
                "activeGenerationId": active_generation_id,
                "deletedGenerationCount": 0,
            }

    def inferencebox_snapshot_from_graph(
        self,
        graph: PortfolioOntology,
        symbols: List[str] = None,
        limit: int = 80,
    ) -> Dict[str, object]:
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        safe_limit = max(1, min(500, int(limit or 80)))
        all_entity_rows = [
            row for row in self.node_rows(graph)
            if str(row.get("ontologyBox") or "") == "InferenceBox"
        ]
        all_relation_rows = [
            row for row in self.rows_for_relations(graph) + self.support_relation_rows(graph)
            if str(row.get("ontologyBox") or "") == "InferenceBox"
        ]
        entity_rows = [
            row for row in all_entity_rows
            if not clean_symbols or str(row.get("symbol") or "").upper() in clean_symbols
        ]
        relation_rows = [
            row for row in all_relation_rows
            if not clean_symbols
            or any(symbol in str(row.get(key) or "").upper() for symbol in clean_symbols for key in ["source", "target", "symbol"])
        ]
        native_entity_rows = [row for row in entity_rows if bool(row.get("nativeTypeDbReasoned"))]
        native_relation_rows = [row for row in relation_rows if bool(row.get("nativeTypeDbReasoned"))]
        native_trace_rows = [row for row in native_entity_rows if str(row.get("kind") or "") == "inference-trace"]
        generation_rulebox_metadata = inference_rulebox_metadata(all_entity_rows, all_relation_rows)
        rowsets = {
            "entityCounts": [{"entityCount": len(native_entity_rows), "nativeEntityCount": len(native_entity_rows)}],
            "relationCounts": [{"relationCount": len(native_relation_rows), "nativeRelationCount": len(native_relation_rows)}],
            "traceCounts": [{"traceCount": len(native_trace_rows), "nativeTraceCount": len(native_trace_rows)}],
            "entities": native_entity_rows[:safe_limit],
            "relations": native_relation_rows[:safe_limit],
            "traces": [{**row, "matchedConditionIds": matched_condition_ids(row)} for row in native_trace_rows[:safe_limit]],
        }
        snapshot = inferencebox_snapshot_from_rows(rowsets, "typedbNativeRuleResult", clean_symbols)
        has_native_output = bool(native_relation_rows or native_trace_rows)
        generation_id = str((graph.worldview or {}).get("inferenceGenerationId") or "")
        generation_at = str((graph.worldview or {}).get("inferenceGenerationAt") or "")
        snapshot.update({
            "graphStore": "typedb",
            "source": "typedbInferenceBox",
            "status": "ok" if has_native_output else "empty",
            "reasoningMode": str(generation_rulebox_metadata.get("reasoningMode") or TYPEDB_NATIVE_REASONING_MODE),
            "materializationSource": str(generation_rulebox_metadata.get("materializationSource") or TYPEDB_NATIVE_MATERIALIZATION_SOURCE),
            "querySource": "typedb-native-rule-result",
            "typedbReadStatus": "skipped",
            "typedbReadReason": "run_rulebox materialization result reused without opening a second TypeDB read driver.",
            "reason": "" if has_native_output else "TypeDB native rules matched no ABox facts.",
            "nativeTypeDbReasoningUsed": has_native_output,
            "typedbNativeRuleReasoningUsed": has_native_output,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "inferenceGenerationId": generation_id,
            "inferenceGenerationAt": generation_at,
            "generationScoped": bool(generation_id),
            "generationCount": 1 if generation_id else 0,
            "inactiveGenerationEntityCount": 0,
            "inactiveGenerationRelationCount": 0,
            "ignoredNonNativeRelationCount": max(0, len(relation_rows) - len(native_relation_rows)),
            "ignoredNonNativeTraceCount": max(0, len([row for row in entity_rows if str(row.get("kind") or "") == "inference-trace"]) - len(native_trace_rows)),
            **generation_rulebox_metadata,
        })
        source_abox_snapshot_id = str(generation_rulebox_metadata.get("sourceAboxSnapshotId") or "").strip()
        if source_abox_snapshot_id:
            snapshot.update({
                "sourceAboxSnapshotId": source_abox_snapshot_id,
                "activeAboxSnapshotId": source_abox_snapshot_id,
                "generationAligned": True,
            })
        return snapshot

    def inferencebox_snapshot(
        self,
        symbols: List[str] = None,
        limit: int = 80,
        reset_metrics: bool = True,
    ) -> Dict[str, object]:
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        safe_limit = max(1, min(500, int(limit or 80)))
        if not self.address:
            return NullTypeDBOntologyGraphRepository().inferencebox_snapshot(clean_symbols, safe_limit)
        if reset_metrics:
            self.reset_query_metrics()
        try:
            return self.inferencebox_snapshot_from_typedb(clean_symbols, safe_limit)
        except Exception as error:  # noqa: BLE001 - expose TypeDB read failures to monitoring diagnostics.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "reasoningMode": "typedb-typeql-read",
                "querySource": "typedb-typeql",
                "typedbReadStatus": "error",
                "reasonCode": typedb_error_code(error),
                "typedbReadReason": str(error)[:180],
                "reason": "TypeDB InferenceBox 조회 실패: " + str(error)[:180],
                "symbols": clean_symbols,
                "entities": [],
                "relations": [],
                "traces": [],
                "entityCount": 0,
                "relationCount": 0,
                "traceCount": 0,
                "nativeEntityCount": 0,
                "nativeRelationCount": 0,
                "nativeTraceCount": 0,
                "nativeTypeDbReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
            }
    def inferencebox_snapshot_from_typedb(self, clean_symbols: List[str], safe_limit: int) -> Dict[str, object]:
        generation_records = self.read_inference_generation_records()
        active_generation = generation_records[0] if generation_records else {}
        generation_id = str((active_generation or {}).get("generationId") or "")
        generation_scoped = bool(generation_id)
        if generation_scoped:
            entity_rows = self.read_inferencebox_entity_rows(generation_id, clean_symbols, safe_limit)
            relation_rows = self.read_inferencebox_relation_rows(generation_id, clean_symbols, safe_limit)
            metadata_entity_rows = self.read_inferencebox_entity_rows(generation_id, [], min(40, safe_limit))
            metadata_relation_rows = self.read_inferencebox_relation_rows(generation_id, [], min(40, safe_limit))
        else:
            all_entity_rows = self.read_entity_rows(["InferenceBox"])
            all_relation_rows = self.read_relation_rows(["InferenceBox"])
            entity_rows = [
                row for row in all_entity_rows
                if not clean_symbols or str(row.get("symbol") or "").upper() in clean_symbols
            ]
            relation_rows = [
                row for row in all_relation_rows
                if not clean_symbols
                or any(symbol in str(row.get(key) or "").upper() for symbol in clean_symbols for key in ["source", "target", "symbol"])
            ]
            metadata_entity_rows = all_entity_rows
            metadata_relation_rows = all_relation_rows
        native_entity_rows = [row for row in entity_rows if bool(row.get("nativeTypeDbReasoned"))]
        native_relation_rows = [row for row in relation_rows if bool(row.get("nativeTypeDbReasoned"))]
        native_trace_rows = [row for row in native_entity_rows if str(row.get("kind") or "") == "inference-trace"]
        ignored_relation_count = len(relation_rows) - len(native_relation_rows)
        ignored_trace_count = len([row for row in entity_rows if str(row.get("kind") or "") == "inference-trace"]) - len(native_trace_rows)
        generation_rulebox_metadata = inference_rulebox_metadata(metadata_entity_rows, metadata_relation_rows)
        rowsets = {
            "entityCounts": [{"entityCount": len(native_entity_rows), "nativeEntityCount": len(native_entity_rows)}],
            "relationCounts": [{"relationCount": len(native_relation_rows), "nativeRelationCount": len(native_relation_rows)}],
            "traceCounts": [{"traceCount": len(native_trace_rows), "nativeTraceCount": len(native_trace_rows)}],
            "entities": native_entity_rows[:safe_limit],
            "relations": native_relation_rows[:safe_limit],
            "traces": [{**row, "matchedConditionIds": matched_condition_ids(row)} for row in native_trace_rows[:safe_limit]],
        }
        snapshot = inferencebox_snapshot_from_rows(rowsets, "typedb-typeql", clean_symbols)
        has_native_output = bool(native_relation_rows or native_trace_rows)
        reasoning_mode = str(generation_rulebox_metadata.get("reasoningMode") or (TYPEDB_NATIVE_REASONING_MODE if has_native_output else TYPEDB_NATIVE_REQUIRED_MODE))
        materialization_source = str(generation_rulebox_metadata.get("materializationSource") or TYPEDB_NATIVE_MATERIALIZATION_SOURCE)
        snapshot.update({
            "graphStore": "typedb",
            "source": "typedbInferenceBox",
            "status": "ok" if has_native_output else "empty",
            "reasoningMode": reasoning_mode,
            "materializationSource": materialization_source,
            "querySource": "typedb-typeql",
            "typedbReadStatus": "ok",
            "reason": "" if has_native_output else "TypeDB InferenceBox 관계가 아직 없습니다. TypeDB native rule materialization 결과를 확인해야 합니다.",
            "nativeTypeDbReasoningUsed": has_native_output,
            "typedbNativeRuleReasoningUsed": has_native_output,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "inferenceGenerationId": generation_id,
            "inferenceGenerationAt": str((active_generation or {}).get("latestAt") or ""),
            "generationScoped": generation_scoped,
            "generationCount": len(generation_records),
            "inactiveGenerationEntityCount": max(0, sum(int(item.get("entityCount") or 0) for item in generation_records if str(item.get("generationId") or "") != generation_id)) if generation_scoped else 0,
            "inactiveGenerationRelationCount": max(0, sum(int(item.get("relationCount") or 0) for item in generation_records if str(item.get("generationId") or "") != generation_id)) if generation_scoped else 0,
            "ignoredNonNativeRelationCount": ignored_relation_count,
            "ignoredNonNativeTraceCount": ignored_trace_count,
            "typedbQueryMetrics": self.query_metrics_snapshot(),
            **generation_rulebox_metadata,
        })
        source_abox_snapshot_id = str(generation_rulebox_metadata.get("sourceAboxSnapshotId") or "").strip()
        if source_abox_snapshot_id:
            active_abox_metadata = self.active_abox_metadata()
            active_abox_status = str(active_abox_metadata.get("status") or "")
            active_abox_snapshot_id = str(active_abox_metadata.get("aboxSnapshotId") or "").strip() if active_abox_status == "ok" else ""
            generation_aligned = bool(active_abox_snapshot_id and active_abox_snapshot_id == source_abox_snapshot_id)
            snapshot.update({
                "sourceAboxSnapshotId": source_abox_snapshot_id,
                "activeAboxSnapshotId": active_abox_snapshot_id,
                "activeAboxStatus": active_abox_status,
                "generationAligned": generation_aligned,
            })
            if not generation_aligned:
                incomplete_abox = active_abox_status != "ok"
                snapshot.update({
                    "status": "incomplete-abox" if incomplete_abox else "stale-generation",
                    "reason": (
                        "현재 ABox 저장이 완료되지 않아 InferenceBox 결과를 투자 판단에서 제외합니다. "
                        + str(active_abox_metadata.get("reason") or "완료 표식 또는 저장 건수를 확인해야 합니다.")[:180]
                        if incomplete_abox
                        else "현재 ABox와 InferenceBox의 원본 ABox 세대가 달라 투자 판단에서 제외합니다."
                    ),
                    "entities": [],
                    "relations": [],
                    "traces": [],
                    "entityCount": 0,
                    "relationCount": 0,
                    "traceCount": 0,
                    "nativeEntityCount": 0,
                    "nativeRelationCount": 0,
                    "nativeTraceCount": 0,
                    "nativeTypeDbReasoningUsed": False,
                    "typedbNativeRuleReasoningUsed": False,
                })
        return snapshot

    def load_graph_from_typedb(self, boxes: Iterable[str] = None) -> PortfolioOntology:
        graph = PortfolioOntology("typedb-read-model")
        for row in self.read_entity_rows(boxes or ["ABox"]):
            properties = json_object(row.get("propertiesJson"))
            properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
            if row.get("symbol"):
                properties.setdefault("symbol", row.get("symbol"))
            if row.get("tboxClass"):
                properties.setdefault("tboxClass", row.get("tboxClass"))
            graph.entities.append(OntologyEntity(
                str(row.get("id") or ""),
                str(row.get("label") or row.get("id") or ""),
                str(row.get("kind") or ""),
                properties,
            ))
        for row in self.read_relation_rows(boxes or ["ABox"]):
            properties = json_object(row.get("propertiesJson"))
            properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
            if row.get("ruleId"):
                properties.setdefault("ruleId", row.get("ruleId"))
            graph.relations.append(OntologyRelation(
                str(row.get("source") or ""),
                str(row.get("target") or ""),
                str(row.get("type") or ""),
                float(number_or_none(row.get("weight")) or 1.0),
                [],
                properties,
            ))
        return graph

    def load_graph_for_native_matches(
        self,
        native_match_result: Dict[str, object],
        rules: Iterable[GraphInferenceRule] = None,
        include_all_rule_relation_types: bool = False,
        include_incoming_relations: bool = True,
    ) -> PortfolioOntology:
        matches = [item for item in (native_match_result or {}).get("matches") or [] if isinstance(item, dict)]
        source_ids = sorted(set(str(item.get("sourceId") or "").strip() for item in matches if str(item.get("sourceId") or "").strip()))
        matched_rule_ids = {str(item.get("ruleId") or "").strip() for item in matches}
        if include_all_rule_relation_types:
            matched_rule_ids = {
                str(rule.rule_id or "").strip()
                for rule in rules or []
                if str(rule.rule_id or "").strip()
            }
        evidence_relation_types = sorted(set(
            str(condition.relation_type or "").upper().strip()
            for rule in rules or []
            if str(rule.rule_id or "").strip() in matched_rule_ids
            for condition in rule.conditions or []
            if str(condition.kind or "") == "relation" and str(condition.relation_type or "").strip()
        ))
        rows: List[Dict[str, object]] = []
        if source_ids:
            try:
                rows = self.read_entity_rows_by_ids(source_ids, ["ABox"])
            except Exception:
                rows = []
        rows_by_id = {str(row.get("id") or ""): row for row in rows}
        graph = PortfolioOntology("typedb-native-match-model")
        for match in matches:
            source_id = str(match.get("sourceId") or "").strip()
            if not source_id or any(item.entity_id == source_id for item in graph.entities):
                continue
            row = rows_by_id.get(source_id)
            if row:
                properties = json_object(row.get("propertiesJson"))
                properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
                properties.setdefault("symbol", row.get("symbol") or symbol_from_subject(source_id))
                properties.setdefault("tboxClass", row.get("tboxClass") or "")
                graph.entities.append(OntologyEntity(
                    source_id,
                    str(row.get("label") or source_id),
                    str(row.get("kind") or "stock"),
                    properties,
                ))
                continue
            graph.entities.append(OntologyEntity(
                source_id,
                str(match.get("sourceLabel") or symbol_from_subject(source_id) or source_id),
                "stock",
                {
                    "ontologyBox": "ABox",
                    "symbol": symbol_from_subject(source_id),
                    "source": "unknown",
                    "queryFallback": True,
                },
            ))
        relation_rows = self.read_relation_rows_by_source_ids(
            source_ids,
            ["ABox"],
            evidence_relation_types,
            include_incoming=include_incoming_relations,
        ) if source_ids else []
        related_node_rows = {
            str(node.get("id") or ""): node
            for item in relation_rows
            for node in [item.get("sourceNode"), item.get("targetNode")]
            if isinstance(node, dict)
            and str(node.get("id") or "").strip()
            and str(node.get("id") or "").strip() not in source_ids
        }
        existing_entity_ids = {item.entity_id for item in graph.entities}
        for row in related_node_rows.values():
            entity_id_value = str(row.get("id") or "").strip()
            if not entity_id_value or entity_id_value in existing_entity_ids:
                continue
            properties = json_object(row.get("propertiesJson"))
            properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
            properties.setdefault("symbol", row.get("symbol") or symbol_from_subject(entity_id_value))
            properties.setdefault("tboxClass", row.get("tboxClass") or "")
            graph.entities.append(OntologyEntity(
                entity_id_value,
                str(row.get("label") or entity_id_value),
                str(row.get("kind") or "observation"),
                properties,
            ))
            existing_entity_ids.add(entity_id_value)
        for row in relation_rows:
            properties = json_object(row.get("propertiesJson"))
            properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
            properties["_relationId"] = str(row.get("id") or "")
            graph.relations.append(OntologyRelation(
                str(row.get("source") or ""),
                str(row.get("target") or ""),
                str(row.get("type") or ""),
                float(number_or_none(row.get("weight")) or 1.0),
                [],
                properties,
            ))
        return graph

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        if not self._last_rules:
            try:
                snapshot = self.rulebox_snapshot()
                self._last_rules = rulebox_rules_from_payload({"rules": snapshot.get("rules") or []})
            except ValueError:
                self._last_rules = []
        normalized = [
            normalize_rule_change_candidate(candidate, existing_rule_ids=[rule.rule_id for rule in self._last_rules])
            for candidate in (candidates or [])
            if isinstance(candidate, dict)
        ]
        normalized = [item for item in normalized if item]
        if not normalized:
            return {"configured": bool(self.address), "status": "no-candidates", "graphStore": "typedb", "candidateCount": 0, "savedCount": 0}
        graph = PortfolioOntology("typedb-rule-change-candidates")
        for item in normalized:
            from ..domain.ontology_contracts import OntologyEntity

            graph.entities.append(OntologyEntity(
                "rule-change-candidate:" + str(item.get("id") or item.get("title") or len(graph.entities)),
                str(item.get("title") or "Rule change candidate"),
                "rule-change-candidate",
                {
                    "ontologyBox": "RuleBoxGovernance",
                    "boundedContext": "reasoning-insight",
                    "tboxClass": "RuleChangeCandidate",
                    "properties": item,
                },
            ))
        save_result = self.save_graph(graph)
        return {
            "configured": bool(self.address),
            "status": save_result.get("status"),
            "graphStore": "typedb",
            "candidateCount": len(normalized),
            "savedCount": len(normalized) if save_result.get("saved") else 0,
            "saveResult": save_result,
        }


def normalized_boxes(boxes: Iterable[str] = None) -> List[str]:
    values = [str(item or "").strip() for item in (boxes or []) if str(item or "").strip()]
    return sorted(set(values or ["ABox"]))


def endpoint_node_row(row: Dict[str, object], prefix: str, box: str) -> Dict[str, object]:
    properties = json_object(row.get(prefix + "Json"))
    return {
        **properties,
        "id": str(row.get(prefix + "Id") or ""),
        "label": str(row.get(prefix + "Label") or row.get(prefix + "Id") or ""),
        "kind": str(row.get(prefix + "Kind") or properties.get("kind") or "observation"),
        "ontologyBox": str(box or properties.get("ontologyBox") or "ABox"),
        "symbol": str(properties.get("symbol") or ""),
        "tboxClass": str(properties.get("tboxClass") or ""),
        "updatedAt": str(row.get(prefix + "UpdatedAt") or properties.get("updatedAt") or ""),
        "propertiesJson": json.dumps(properties, ensure_ascii=False, sort_keys=True),
    }


def relation_type_rows_from_derivations(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    values = set()
    for row in entity_rows or []:
        if entity_node_kind(row) == "relation-template":
            values.add(str(row.get("derivationRelationType") or row.get("relationType") or "").upper())
    for row in relation_rows or []:
        if row.get("ontologyBox") == "RuleBox":
            values.add(str(row.get("type") or row.get("relationType") or "").upper())
    return [{"relationType": item} for item in sorted(value for value in values if value)]


def typedb_native_rule_id(rule_id: object) -> str:
    value = str(rule_id or "").strip()
    if not value:
        return ""
    if value.startswith("typedb.native."):
        return value
    return "typedb.native." + value


def typedb_native_profile_metadata(native_profile: Dict[str, object]) -> Dict[str, object]:
    profile = dict(native_profile or {})
    return {
        "reasoningMode": TYPEDB_NATIVE_REASONING_MODE,
        "materializationSource": TYPEDB_NATIVE_MATERIALIZATION_SOURCE,
        "reasoningLayer": TYPEDB_NATIVE_REASONING_LAYER,
        "typedbNativeRuleEngineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
        "typedbNativeRuleProfileVersion": str(profile.get("version") or TYPEDB_NATIVE_REASONING_PROFILE_VERSION),
        "typedbNativeRuleProfileStatus": str(profile.get("status") or ""),
        "typedbNativeRuleCount": int(number_or_none(profile.get("ruleCount")) or 0),
        "typedbNativeReadyRuleCount": int(number_or_none(profile.get("readyRuleCount")) or 0),
        "typedbNativePartialRuleCount": int(number_or_none(profile.get("partialRuleCount")) or 0),
        "typedbNativeBlockedRuleCount": int(number_or_none(profile.get("blockedRuleCount")) or 0),
        "typedbNativeRuleMaterializationUsed": True,
        "typedbSchemaFunctionMaterializationUsed": True,
        "typeDbNativeRulesPrimary": True,
        "ruleStore": "TypeDB schema functions",
    }


def materialize_typedb_native_matches(
    graph: PortfolioOntology,
    rules: Iterable[GraphInferenceRule],
    native_matches: Dict[str, object],
) -> None:
    entities_by_id = {item.entity_id: item for item in graph.entities}
    rules_by_id = {str(rule.rule_id or ""): rule for rule in (rules or [])}
    for match in native_matches.get("matches") or []:
        if not isinstance(match, dict):
            continue
        rule = rules_by_id.get(str(match.get("ruleId") or ""))
        subject = entities_by_id.get(str(match.get("sourceId") or ""))
        if not rule or not subject:
            continue
        materialize_rule_inference(graph, rule, subject, {
            "matchedConditions": list(match.get("matchedConditions") or []),
            "evidenceRelationIds": list(match.get("evidenceRelationIds") or []),
            "conditionDetailSource": str(match.get("conditionDetailSource") or "schema-function-match"),
        })


def typedb_native_matched_conditions(
    rule: GraphInferenceRule,
    row: Dict[str, object],
    query_plan: Dict[str, object],
) -> List[Dict[str, object]]:
    result = []
    evidence_by_condition = dict(query_plan.get("conditionEvidenceColumns") or {})
    verified_any_condition_ids = {
        str(item or "")
        for item in (row or {}).get("_matchedAnyConditionIds") or []
        if str(item or "")
    }
    any_group_verified = bool((row or {}).get("_anyConditionsVerified"))
    any_group_added = False
    for condition in getattr(rule, "conditions", []) or []:
        condition_role = condition.role or "required"
        if (
            condition_role in {"any", "optional"}
            and any_group_verified
        ):
            if not any_group_added:
                result.append({
                    "conditionId": "any-group:" + str(rule.rule_id or ""),
                    "kind": "any-condition-group",
                    "role": "any",
                    "minimumCount": max(1, int(number_or_none(getattr(rule, "any_condition_min_count", 1)) or 1)),
                    "matchedByTypeDB": True,
                    "detailDeferred": not bool(verified_any_condition_ids),
                })
                any_group_added = True
            if condition.condition_id not in verified_any_condition_ids:
                continue
        if (
            condition_role in {"any", "optional"}
            and verified_any_condition_ids
            and condition.condition_id not in verified_any_condition_ids
        ):
            continue
        payload = {
            "conditionId": condition.condition_id,
            "kind": condition.kind,
            "role": condition_role,
        }
        evidence_column = evidence_by_condition.get(condition.condition_id)
        if evidence_column:
            payload["relationId"] = str(row.get(evidence_column) or "")
        if condition.kind == "subject_property":
            payload.update({"field": condition.field, "operator": condition.operator, "value": condition.value})
        elif condition.kind == "relation":
            payload.update({"relationType": condition.relation_type})
        result.append(payload)
    return result


def typedb_static_schema_function_condition_context(
    rule: GraphInferenceRule,
    query_plan: Dict[str, object] = None,
    row: Dict[str, object] = None,
) -> Dict[str, object]:
    query_plan = dict(query_plan or {})
    row = dict(row or {})
    matched_conditions = typedb_native_matched_conditions(rule, row, query_plan)
    if not matched_conditions:
        for condition in getattr(rule, "conditions", []) or []:
            role = str(getattr(condition, "role", "") or "required")
            if role in {"optional", "any"}:
                continue
            payload = {
                "conditionId": getattr(condition, "condition_id", ""),
                "kind": getattr(condition, "kind", ""),
                "role": role,
                "matchedBySchemaFunction": True,
            }
            if role == "not":
                payload["absenceSatisfied"] = True
            if getattr(condition, "kind", "") == "subject_property":
                payload.update({
                    "field": getattr(condition, "field", ""),
                    "operator": getattr(condition, "operator", ""),
                    "value": getattr(condition, "value", None),
                })
            elif getattr(condition, "kind", "") == "relation":
                payload.update({
                    "relationType": getattr(condition, "relation_type", ""),
                })
            matched_conditions.append(payload)
    evidence_relation_ids = [
        str(row.get(column) or "")
        for column in (query_plan.get("evidenceColumns") or [])
        if str(row.get(column) or "").strip()
    ]
    return {
        "matchedConditions": matched_conditions,
        "evidenceRelationIds": sorted(set(evidence_relation_ids)),
        "conditionDetailSource": "schema-function-match",
    }


def normalized_condition_role(condition: Dict[str, object]) -> str:
    role = str(condition.get("role") or condition.get("conditionRole") or "required").strip().lower()
    return role if role in {"required", "any", "optional", "not"} else "required"


def typedb_rule_condition_payloads(rule: object) -> List[Dict[str, object]]:
    """Normalize persisted RuleBox conditions for execution planning only."""
    raw_conditions = getattr(rule, "conditions", None)
    if raw_conditions is None and isinstance(rule, dict):
        raw_conditions = rule.get("conditions") or []
    payloads: List[Dict[str, object]] = []
    for condition in raw_conditions or []:
        if hasattr(condition, "to_dict"):
            payload = condition.to_dict()
        elif isinstance(condition, dict):
            payload = dict(condition)
        else:
            continue
        payloads.append(payload)
    return payloads


def typedb_native_rule_required_relation_types(rule: object) -> set:
    """Return required relation dependencies without evaluating their filters.

    A missing required relation type makes a TypeDB rule impossible for a
    subject in the current ABox generation.  Values, operators, and optional
    branches remain exclusively inside the native TypeDB function.
    """
    required_types = set()
    for condition in typedb_rule_condition_payloads(rule):
        if str(condition.get("kind") or "") != "relation":
            continue
        if normalized_condition_role(condition) != "required":
            continue
        relation_type = str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
        if relation_type:
            required_types.add(relation_type)
    return required_types


def typedb_native_rule_any_relation_requirement(rule: object) -> tuple:
    """Return topology-only alternatives when every ``any`` branch is a relation.

    This is only a planner shortcut. Attribute filters and the actual minimum
    match count are still evaluated by the TypeDB schema function.
    """
    any_conditions = [
        condition
        for condition in typedb_rule_condition_payloads(rule)
        if normalized_condition_role(condition) == "any"
    ]
    if not any_conditions or any(str(condition.get("kind") or "") != "relation" for condition in any_conditions):
        return [], 0
    relation_types = [
        str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
        for condition in any_conditions
    ]
    if any(not relation_type for relation_type in relation_types):
        return [], 0
    raw_minimum = getattr(rule, "any_condition_min_count", None)
    if raw_minimum is None and isinstance(rule, dict):
        raw_minimum = rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")
    return relation_types, max(1, int(number_or_none(raw_minimum) or 1))


def typedb_preflight_properties(properties: Dict[str, object]) -> Dict[str, object]:
    """Flatten a persisted ontology JSON object for a conservative planner."""
    payload = dict(properties or {})
    nested = payload.get("properties")
    if isinstance(nested, dict):
        payload.update(nested)
    return payload


def typedb_preflight_scalar_equal(actual: object, expected: object) -> bool:
    """Compare values as TypeDB's promoted string/numeric attributes do."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return str(actual).strip().lower() == str(expected).strip().lower()
    actual_number = number_or_none(actual)
    expected_number = number_or_none(expected)
    if actual_number is not None and expected_number is not None:
        return float(actual_number) == float(expected_number)
    return actual == expected or str(actual) == str(expected)


def typedb_preflight_value_matches(actual: object, operator: object, expected: object):
    """Return ``False`` only when one RuleBox comparison is provably false.

    This is an execution planner, not a second rule engine: unknown/missing
    values return ``None`` and leave the rule for TypeDB to decide.  Values
    and operators are read from the persisted RuleBox condition unchanged.
    """
    if actual in (None, ""):
        return None
    op = str(operator or "==").strip().lower()
    if op in {"exists", "present"}:
        return True
    expected = typedb_expected_value(expected)
    if expected in (None, "", [], {}):
        # TypeQL emits no predicate for an empty expected value.
        return True
    if isinstance(expected, (list, tuple, set)):
        outcomes = [typedb_preflight_value_matches(actual, "==", item) for item in expected]
        if any(item is True for item in outcomes):
            return True
        return False if outcomes and all(item is False for item in outcomes) else None
    if isinstance(actual, (list, tuple, set)):
        outcomes = [typedb_preflight_value_matches(item, op, expected) for item in actual]
        if op in {"!=", "ne"}:
            if any(item is True for item in outcomes):
                return True
            return False if outcomes and all(item is False for item in outcomes) else None
        if any(item is True for item in outcomes):
            return True
        return False if outcomes and all(item is False for item in outcomes) else None
    if op in {"==", "eq", "in"}:
        return typedb_preflight_scalar_equal(actual, expected)
    if op in {"!=", "ne"}:
        return not typedb_preflight_scalar_equal(actual, expected)
    if op in {">", "gt", ">=", "gte", "<", "lt", "<=", "lte"}:
        actual_number = number_or_none(actual)
        expected_number = number_or_none(expected)
        if actual_number is None or expected_number is None:
            return None
        if op in {">", "gt"}:
            return float(actual_number) > float(expected_number)
        if op in {">=", "gte"}:
            return float(actual_number) >= float(expected_number)
        if op in {"<", "lt"}:
            return float(actual_number) < float(expected_number)
        return float(actual_number) <= float(expected_number)
    return None


def typedb_preflight_filter_key_and_operator(filter_key: object, expected: object) -> Tuple[str, str, object]:
    """Mirror the supported target/relation filter form used by TypeQL."""
    key = str(filter_key or "")
    operator = "=="
    if key == "minValue":
        key, operator = "value", ">="
    elif key == "maxValue":
        key, operator = "value", "<="
    elif key.startswith("min") and len(key) > 3:
        key, operator = key[3].lower() + key[4:], ">="
    elif key.startswith("max") and len(key) > 3:
        key, operator = key[3].lower() + key[4:], "<="
    if isinstance(expected, dict) and expected.get("operator"):
        operator = str(expected.get("operator") or operator)
    return key, operator, expected


def typedb_preflight_filters_match(properties: Dict[str, object], filters: Dict[str, object]):
    """Return a conservative result for persisted relation/target filters."""
    values = typedb_preflight_properties(properties)
    unknown = False
    for filter_key, expected in dict(filters or {}).items():
        key, operator, raw_expected = typedb_preflight_filter_key_and_operator(filter_key, expected)
        verdict = typedb_preflight_value_matches(values.get(key), operator, raw_expected)
        if verdict is False:
            return False
        if verdict is None:
            unknown = True
    return None if unknown else True


def typedb_preflight_relation_condition_matches(
    graph: PortfolioOntology,
    subject: OntologyEntity,
    condition: object,
):
    """Check whether a required relation condition is still possible in ABox.

    Returning ``None`` preserves the native TypeDB call when the planner does
    not have enough endpoint data.  A ``False`` result is therefore a strict
    proof that no persisted direct relation can satisfy this required clause.
    """
    relation_type = str(getattr(condition, "relation_type", "") or "").upper().strip()
    if not relation_type:
        return None
    direction = str(getattr(condition, "direction", "") or "out").lower()
    target_kind = str(getattr(condition, "target_kind", "") or "")
    targets = {item.entity_id: item for item in graph.entities}
    unknown = False
    for relation in graph.relations:
        if str(relation.relation_type or "").upper().strip() != relation_type:
            continue
        if direction == "in":
            if str(relation.target or "") != str(subject.entity_id or ""):
                continue
            target_id = str(relation.source or "")
        else:
            if str(relation.source or "") != str(subject.entity_id or ""):
                continue
            target_id = str(relation.target or "")
        target = targets.get(target_id)
        if target is None:
            unknown = True
            continue
        if target_kind and str(target.kind or "") != target_kind:
            continue
        target_verdict = typedb_preflight_filters_match(
            target.properties or {},
            getattr(condition, "target_property_filters", {}) or {},
        )
        relation_verdict = typedb_preflight_filters_match(
            relation.properties or {},
            getattr(condition, "relation_property_filters", {}) or {},
        )
        if target_verdict is False or relation_verdict is False:
            continue
        if target_verdict is None or relation_verdict is None:
            unknown = True
            continue
        return True
    if unknown:
        return None
    # A complete preflight graph can prove absence both when no relation of
    # the required type exists and when every endpoint/filter candidate fails.
    return False


def typedb_native_rule_required_conditions_preflight(
    graph: PortfolioOntology,
    rule: object,
    symbol: str,
    incoming_relations_complete: bool = True,
) -> Dict[str, object]:
    """Prove only impossible required RuleBox clauses for one stock symbol."""
    clean_symbol = str(symbol or "").upper().strip()
    candidates = [
        item for item in graph.entities
        if str(item.kind or "") == "stock"
        and str((item.properties or {}).get("symbol") or "").upper().strip() == clean_symbol
    ]
    if len(candidates) != 1:
        return {"status": "unknown", "reason": "ABox preflight has no unambiguous stock subject.", "failedConditionIds": []}
    subject = candidates[0]
    properties = typedb_preflight_properties(subject.properties or {})
    properties.setdefault("symbol", clean_symbol)
    properties.setdefault("kind", subject.kind)
    properties.setdefault("ontologyBox", (subject.properties or {}).get("ontologyBox") or "ABox")
    failed_condition_ids: List[str] = []
    unknown = False
    for condition in getattr(rule, "conditions", []) or []:
        if normalized_condition_role(condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})) != "required":
            continue
        condition_id = str(getattr(condition, "condition_id", "") or "")
        kind = str(getattr(condition, "kind", "") or "")
        if kind == "subject_property":
            verdict = typedb_preflight_value_matches(
                properties.get(str(getattr(condition, "field", "") or "")),
                getattr(condition, "operator", "=="),
                getattr(condition, "value", None),
            )
        elif kind == "relation":
            direction = str(getattr(condition, "direction", "") or "out").lower()
            verdict = (
                None
                if direction == "in" and not incoming_relations_complete
                else typedb_preflight_relation_condition_matches(graph, subject, condition)
            )
        else:
            verdict = None
        if verdict is False:
            failed_condition_ids.append(condition_id)
        elif verdict is None:
            unknown = True
    if failed_condition_ids:
        return {
            "status": "impossible",
            "reason": "Active ABox cannot satisfy required RuleBox conditions: " + ", ".join(failed_condition_ids[:4]),
            "failedConditionIds": failed_condition_ids,
        }
    return {
        "status": "unknown" if unknown else "possible",
        "reason": "" if not unknown else "Some required ABox values were unavailable to the preflight planner.",
        "failedConditionIds": [],
    }


def typedb_native_rule_query_complexity(rule: object) -> int:
    """Estimate query cost from persisted rule shape, never from market data."""
    conditions = typedb_rule_condition_payloads(rule)
    any_count = sum(
        1
        for condition in conditions
        if normalized_condition_role(condition) in {"any", "optional"}
    )
    raw_minimum = getattr(rule, "any_condition_min_count", None)
    if raw_minimum is None and isinstance(rule, dict):
        raw_minimum = rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")
    any_minimum = max(1, int(number_or_none(raw_minimum) or 1))
    combinations = 0
    if any_count:
        try:
            combinations = math.comb(any_count, min(any_count, any_minimum))
        except ValueError:
            combinations = any_count
    return len(conditions) + min(64, combinations) * 3


def typedb_native_rule_execution_plan(
    rules: Iterable[GraphInferenceRule],
    target_symbols: Iterable[str],
    relation_types_by_symbol: Dict[str, Iterable[str]] = None,
    query_limit: int = 0,
    preflight_graph: PortfolioOntology = None,
    preflight_incoming_relations_complete: bool = True,
) -> Dict[str, object]:
    """Build a complete TypeDB-function plan for selected ABox subjects.

    The planner removes only rule/symbol pairs that cannot satisfy required
    ABox conditions.  When a bounded preflight graph is available it may also
    prove that a required source value or relation filter is impossible; it
    never accepts a rule match. TypeDB functions still evaluate every
    surviving condition, including numeric thresholds, negation, and any
    branches. A query limit is intentionally ignored: an InferenceBox
    generation must be complete for every selected subject, otherwise the
    caller returns a blocked partial result instead of using a biased subset.
    """
    clean_symbols = clean_symbols_from_payload(list(target_symbols or []))
    type_index = {
        str(symbol or "").upper().strip(): {
            str(relation_type or "").upper().strip()
            for relation_type in relation_types or []
            if str(relation_type or "").strip()
        }
        for symbol, relation_types in dict(relation_types_by_symbol or {}).items()
        if str(symbol or "").strip()
    }
    entries: List[Dict[str, object]] = []
    for rule in rules or []:
        rule_id = str(getattr(rule, "rule_id", "") or (rule.get("rule_id") if isinstance(rule, dict) else "") or "")
        required_relation_types = typedb_native_rule_required_relation_types(rule)
        any_relation_types, any_relation_minimum = typedb_native_rule_any_relation_requirement(rule)
        candidate_symbols = list(clean_symbols)
        if clean_symbols and required_relation_types:
            candidate_symbols = [
                symbol
                for symbol in clean_symbols
                if required_relation_types.issubset(type_index.get(symbol, set()))
            ]
        if candidate_symbols and any_relation_types and any_relation_minimum:
            candidate_symbols = [
                symbol
                for symbol in candidate_symbols
                if sum(
                    1
                    for relation_type in any_relation_types
                    if relation_type in type_index.get(symbol, set())
                ) >= any_relation_minimum
            ]
        preflight_pruned_symbols: Dict[str, Dict[str, object]] = {}
        if candidate_symbols and preflight_graph is not None:
            retained_symbols = []
            for symbol in candidate_symbols:
                preflight = typedb_native_rule_required_conditions_preflight(
                    preflight_graph,
                    rule,
                    symbol,
                    incoming_relations_complete=preflight_incoming_relations_complete,
                )
                if str(preflight.get("status") or "") == "impossible":
                    preflight_pruned_symbols[symbol] = preflight
                    continue
                retained_symbols.append(symbol)
            candidate_symbols = retained_symbols
        entry = {
            "rule": rule,
            "ruleId": rule_id,
            "requiredRelationTypes": sorted(required_relation_types),
            "anyRelationTypes": list(any_relation_types),
            "anyRelationMinimum": any_relation_minimum,
            "candidateSymbols": candidate_symbols,
            "queryComplexity": typedb_native_rule_query_complexity(rule),
            "preflightPrunedSymbols": preflight_pruned_symbols,
        }
        if clean_symbols and not candidate_symbols:
            preflight_reasons = [
                str(item.get("reason") or "")
                for item in preflight_pruned_symbols.values()
                if str(item.get("reason") or "")
            ]
            entry.update({
                "selected": False,
                "status": "not-applicable-preflight" if preflight_pruned_symbols else "not-applicable",
                "reason": (
                    preflight_reasons[0]
                    if preflight_reasons
                    else "Active ABox has no candidate symbol with every required relation type."
                ),
            })
        else:
            entry.update({"selected": True, "status": "planned", "reason": ""})
        entries.append(entry)
    selected_entries = [item for item in entries if item.get("selected")]
    selected_entries.sort(
        key=lambda item: (
            int(item.get("queryComplexity") or 0),
            str(item.get("ruleId") or ""),
        )
    )
    skipped_entries = [item for item in entries if not item.get("selected")]
    return {
        "status": "ok",
        "targetSymbols": clean_symbols,
        "queryLimit": 0,
        "candidateRuleCount": len(entries),
        "selectedRuleCount": len(selected_entries),
        "skippedRuleCount": len(skipped_entries),
        "preflightEnabled": preflight_graph is not None,
        "preflightIncomingRelationsComplete": bool(preflight_incoming_relations_complete),
        "preflightPrunedRuleCount": len([
            item for item in skipped_entries
            if str(item.get("status") or "") == "not-applicable-preflight"
        ]),
        "preflightPrunedSymbolCount": sum(
            len(dict(item.get("preflightPrunedSymbols") or {}))
            for item in entries
        ),
        "selectedEntries": selected_entries,
        "skippedEntries": skipped_entries,
        "relationTypesBySymbol": {
            symbol: sorted(values)
            for symbol, values in type_index.items()
        },
    }


def typedb_native_rule_execution_selection(
    rules: Iterable[GraphInferenceRule],
    candidate_rule_ids: Iterable[object] = None,
    prior_matched_rule_ids: Iterable[object] = None,
    eligible: bool = False,
    prior_inference_reusable: bool = False,
    global_impact: bool = False,
) -> Dict[str, object]:
    """Select a complete, reusable native RuleBox slice.

    A RuleBox rule is never evaluated in Python.  For a local immutable scope
    change, an unchanged rule can only remain matched when it was matched in
    the previous aligned InferenceBox; known non-matches remain non-matches.
    We therefore execute changed candidates plus previous matches.  Any
    incomplete proof falls back to every enabled rule rather than emitting a
    partial investment judgement.
    """
    all_rules = [rule for rule in rules or [] if getattr(rule, "enabled", True)]
    all_ids = [str(getattr(rule, "rule_id", "") or "").strip() for rule in all_rules]
    all_ids = [rule_id for rule_id in all_ids if rule_id]
    available = set(all_ids)
    candidates = {
        str(value or "").strip()
        for value in candidate_rule_ids or []
        if str(value or "").strip()
    }
    prior_matches = {
        str(value or "").strip()
        for value in prior_matched_rule_ids or []
        if str(value or "").strip()
    }
    fallback_reason = ""
    if not eligible:
        fallback_reason = "impact-plan-not-eligible"
    elif global_impact:
        fallback_reason = "global-impact-requires-complete-evaluation"
    elif not candidates:
        fallback_reason = "candidate-rules-unavailable"
    elif not prior_inference_reusable:
        fallback_reason = "prior-aligned-inference-unavailable"
    elif (candidates | prior_matches) - available:
        fallback_reason = "rulebox-version-or-prior-match-mismatch"
    if fallback_reason:
        return {
            "selectedRules": all_rules,
            "selectedRuleIds": all_ids,
            "deferredRuleIds": [],
            "candidateRuleIds": sorted(candidates),
            "priorMatchedRuleIds": sorted(prior_matches),
            "selectionApplied": False,
            "fallbackReason": fallback_reason,
            "fullRuleCount": len(all_rules),
        }
    selected_ids = candidates | prior_matches
    selected_rules = [rule for rule in all_rules if str(getattr(rule, "rule_id", "") or "") in selected_ids]
    deferred = [rule_id for rule_id in all_ids if rule_id not in selected_ids]
    return {
        "selectedRules": selected_rules,
        "selectedRuleIds": [str(getattr(rule, "rule_id", "") or "") for rule in selected_rules],
        "deferredRuleIds": deferred,
        "candidateRuleIds": sorted(candidates),
        "priorMatchedRuleIds": sorted(prior_matches),
        "selectionApplied": len(selected_rules) < len(all_rules),
        "fallbackReason": "",
        "fullRuleCount": len(all_rules),
    }


def typedb_native_rule_execution_plan_summary(plan: Dict[str, object]) -> Dict[str, object]:
    """Return bounded operational diagnostics without serialising rule bodies."""
    payload = dict(plan or {})
    selected = [item for item in payload.get("selectedEntries") or [] if isinstance(item, dict)]
    skipped = [item for item in payload.get("skippedEntries") or [] if isinstance(item, dict)]
    status_counts: Dict[str, int] = {}
    for item in skipped:
        status = str(item.get("status") or "skipped")
        status_counts[status] = int(status_counts.get(status, 0) or 0) + 1
    return {
        "status": str(payload.get("status") or ""),
        "targetSymbols": list(payload.get("targetSymbols") or []),
        "queryLimit": int(number_or_none(payload.get("queryLimit")) or 0),
        "candidateRuleCount": int(number_or_none(payload.get("candidateRuleCount")) or 0),
        "selectedRuleCount": len(selected),
        "skippedRuleCount": len(skipped),
        "preflightEnabled": bool(payload.get("preflightEnabled")),
        "preflightIncomingRelationsComplete": bool(payload.get("preflightIncomingRelationsComplete", True)),
        "preflightPrunedRuleCount": int(number_or_none(payload.get("preflightPrunedRuleCount")) or 0),
        "preflightPrunedSymbolCount": int(number_or_none(payload.get("preflightPrunedSymbolCount")) or 0),
        "skippedByStatus": status_counts,
        "selectedRules": [
            {
                "ruleId": str(item.get("ruleId") or ""),
                "candidateSymbols": list(item.get("candidateSymbols") or []),
                "queryComplexity": int(number_or_none(item.get("queryComplexity")) or 0),
            }
            for item in selected[:40]
        ],
        "relationTypesBySymbol": dict(payload.get("relationTypesBySymbol") or {}),
    }


def typedb_filter_operator(filter_key: str, expected: object, default_operator: str = "==") -> str:
    if isinstance(expected, dict) and expected.get("operator"):
        return str(expected.get("operator") or default_operator)
    if str(filter_key or "").startswith("min"):
        return ">="
    if str(filter_key or "").startswith("max"):
        return "<="
    return default_operator


def typedb_condition_pattern(
    condition: Dict[str, object],
    index: int,
    source_var: str = "$source",
    relation_prefix: str = "rel",
    target_prefix: str = "target",
    variable_scope: str = "",
    manifest_id_variable: str = "",
) -> Dict[str, object]:
    condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "condition-" + str(index))
    kind = str(condition.get("kind") or "")
    clauses: List[str] = []
    columns: List[str] = []
    evidence_columns: List[str] = []
    safe_scope = re.sub(r"[^A-Za-z0-9_]", "", str(variable_scope or ""))

    def value_variable(prefix: str, clause_index: int) -> str:
        if safe_scope:
            return prefix + safe_scope + str(clause_index)
        return prefix + str(index) + str(clause_index)

    if kind == "subject_property":
        attr = typedb_subject_attribute(str(condition.get("field") or ""))
        if not attr:
            return {"conditionId": condition_id, "clauses": [], "columns": [], "reason": "unsupported subject field"}
        clause = typedb_value_match(
            source_var,
            attr,
            condition.get("value"),
            str(condition.get("operator") or "=="),
            value_variable("subjectValue", 0),
        )
        if clause:
            clauses.append(clause)
        return {
            "conditionId": condition_id,
            "kind": kind,
            "clauses": clauses,
            "columns": columns,
            "evidenceColumns": evidence_columns,
        }
    if kind == "relation":
        relation_var = "$" + relation_prefix + str(index)
        target_var = "$" + target_prefix + str(index)
        relation_id_var = value_variable("relationId", 0)
        rel_type = str(condition.get("relation_type") or condition.get("relationType") or "").upper()
        direction = str(condition.get("direction") or "out")
        if not rel_type:
            return {"conditionId": condition_id, "clauses": [], "columns": [], "reason": "missing relation type"}
        if direction == "in":
            clauses.append(
                target_var + " isa ontology-node; "
                + relation_var + " isa ontology-assertion, links (source: " + target_var + ", target: " + source_var + "), "
                + "has ontology-id $" + relation_id_var + ", has ontology-relation-type " + typedb_string(rel_type) + ";"
            )
        else:
            clauses.append(
                target_var + " isa ontology-node; "
                + relation_var + " isa ontology-assertion, links (source: " + source_var + ", target: " + target_var + "), "
                + "has ontology-id $" + relation_id_var + ", has ontology-relation-type " + typedb_string(rel_type) + ";"
            )
        if manifest_id_variable:
            clauses.append(typedb_scoped_manifest_member_clause(
                target_var,
                target_prefix + str(index),
                manifest_id_variable,
            ))
            clauses.append(typedb_scoped_manifest_member_clause(
                relation_var,
                relation_prefix + str(index),
                manifest_id_variable,
            ))
        else:
            clauses.append(typedb_active_abox_member_clause(target_var, target_prefix + str(index)))
            clauses.append(typedb_active_abox_member_clause(relation_var, relation_prefix + str(index)))
        target_kind = str(condition.get("target_kind") or condition.get("targetKind") or "")
        if target_kind:
            clauses.append(target_var + " has ontology-kind " + typedb_string(target_kind) + ";")
        for filter_key, expected in dict(condition.get("target_property_filters") or condition.get("targetPropertyFilters") or {}).items():
            attr = typedb_target_attribute(str(filter_key))
            if attr:
                op = typedb_filter_operator(str(filter_key), expected)
                clause = typedb_value_match(target_var, attr, expected, op, value_variable("targetValue", len(clauses)))
                if clause:
                    clauses.append(clause)
        for filter_key, expected in dict(condition.get("relation_property_filters") or condition.get("relationPropertyFilters") or {}).items():
            attr = typedb_relation_attribute(str(filter_key))
            if attr:
                op = typedb_filter_operator(str(filter_key), expected)
                clause = typedb_value_match(relation_var, attr, expected, op, value_variable("relationValue", len(clauses)))
                if clause:
                    clauses.append(clause)
        columns.append(relation_id_var)
        evidence_columns.append(relation_id_var)
        return {
            "conditionId": condition_id,
            "kind": kind,
            "clauses": clauses,
            "columns": columns,
            "evidenceColumns": evidence_columns,
            "relationIdColumn": relation_id_var,
        }
    return {"conditionId": condition_id, "clauses": [], "columns": [], "reason": "unsupported condition kind"}


def typedb_native_match_query(
    rule: Dict[str, object],
    target_symbols: Iterable[str] = None,
    any_helper_names: List[str] = None,
    scoped_manifest_only: bool = False,
    manifest_id_variable: str = "",
    bind_active_manifest: bool = True,
    include_any_conditions: bool = True,
) -> Dict[str, object]:
    """Compile one RuleBox rule into a bounded TypeQL read pipeline.

    ``any`` conditions used to be expanded into every possible combination.
    That made one six-condition, two-of-six rule generate fifteen nested
    function branches and caused TypeDB's planner to hold CPU after the client
    had already timed out.  Each RuleBox condition already has a durable,
    unique ontology entity, so the pipeline now matches one branch at a time
    and counts distinct condition tokens per source.  This preserves the
    exact ``at least N different conditions`` semantics without combinatorial
    function bodies.
    """
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock")
    conditions = [item for item in (rule.get("conditions") or []) if isinstance(item, dict)]
    scoped_manifest_variable = str(manifest_id_variable or "$activeManifestId") if scoped_manifest_only else ""
    if scoped_manifest_only:
        clauses = []
        if bind_active_manifest:
            clauses.append(typedb_active_worldview_manifest_clause("$activeManifestPointer", scoped_manifest_variable))
        clauses.append(typedb_scoped_manifest_member_clause("$source", "source", scoped_manifest_variable))
    else:
        clauses = [typedb_active_abox_member_clause("$source", "source")]
    clauses.append(
        "$source isa ontology-node, has ontology-id $sourceId, has ontology-label $sourceLabel, has ontology-kind "
        + typedb_string(source_kind)
        + ";"
    )
    symbols = clean_symbols_from_payload(list(target_symbols or []))
    if symbols:
        clauses.append(typedb_value_match("$source", "ontology-symbol", symbols, "==", "sourceSymbol"))
    columns = ["sourceId", "sourceLabel"]
    evidence_columns: List[str] = []
    condition_evidence_columns: Dict[str, str] = {}
    any_conditions: List[Tuple[int, Dict[str, object]]] = []
    any_min_count = max(1, int(number_or_none(rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")) or 1))
    for index, condition in enumerate(conditions):
        condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "condition-" + str(index))
        role = normalized_condition_role(condition)
        pattern = typedb_condition_pattern(condition, index, manifest_id_variable=scoped_manifest_variable)
        if pattern.get("reason"):
            return {"ruleId": rule_id, "query": "", "columns": columns, "reason": str(pattern.get("reason") or "")}
        pattern_clauses = [str(item) for item in pattern.get("clauses") or [] if str(item or "").strip()]
        if not pattern_clauses:
            continue
        if role in {"any", "optional"}:
            any_conditions.append((index, condition))
            continue
        if role == "not":
            clauses.append("not { " + " ".join(pattern_clauses) + " };")
            continue
        clauses.extend(pattern_clauses)
        for column in pattern.get("columns") or []:
            columns.append(str(column))
        for column in pattern.get("evidenceColumns") or []:
            evidence_columns.append(str(column))
        if pattern.get("relationIdColumn"):
            condition_evidence_columns[condition_id] = str(pattern.get("relationIdColumn"))
    query = "match " + " ".join(clauses)
    if any_conditions and include_any_conditions:
        if any_min_count > len(any_conditions):
            return {"ruleId": rule_id, "query": "", "columns": columns, "reason": "any condition minimum exceeds available any conditions"}
        branches: List[str] = []
        for branch_index, (_condition_index, condition) in enumerate(any_conditions):
            pattern = typedb_condition_pattern(
                condition,
                branch_index,
                relation_prefix="anyCountRel" + str(branch_index) + "_",
                target_prefix="anyCountTarget" + str(branch_index) + "_",
                variable_scope="anyCount" + str(branch_index) + "_",
                manifest_id_variable=scoped_manifest_variable,
            )
            if pattern.get("reason"):
                return {"ruleId": rule_id, "query": "", "columns": columns, "reason": str(pattern.get("reason") or "")}
            branch_clauses = [str(item) for item in pattern.get("clauses") or [] if str(item or "").strip()]
            if branch_clauses:
                condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "condition-" + str(branch_index))
                token_id = entity_id("rule-condition", rule_id + ":" + condition_id)
                branch_clauses.append(
                    "$anyConditionToken isa ontology-node, has ontology-box \"RuleBox\", has ontology-id "
                    + typedb_string(token_id)
                    + ";"
                )
                branches.append("{ " + " ".join(branch_clauses) + " }")
        if not branches:
            return {"ruleId": rule_id, "query": "", "columns": columns, "reason": "any conditions produced no TypeQL branches"}
        # `count($anyConditionToken)` counts distinct RuleBox condition
        # entities, not relation rows. A source therefore cannot satisfy the
        # N-of-M contract by producing duplicate evidence for one condition.
        query += (
            " match " + " or ".join(branches) + ";"
            + " reduce $anyConditionCount = count($anyConditionToken) groupby $source;"
            + " match $anyConditionCount >= " + str(any_min_count) + ";"
            + " $source has ontology-id $sourceId, has ontology-label $sourceLabel;"
        )
        # The reduce stage intentionally drops relation variables. Detailed
        # evidence is collected only by the opt-in condition-detail path.
        evidence_columns = []
        condition_evidence_columns = {}
    return {
        "ruleId": rule_id,
        "nativeRuleId": typedb_native_rule_id(rule_id),
        "query": query,
        "columns": columns,
        "evidenceColumns": evidence_columns,
        "conditionEvidenceColumns": condition_evidence_columns,
    }


def typedb_native_any_group_check_query(
    rule: Dict[str, object],
    source_id: str,
    scoped_manifest_only: bool = False,
) -> Dict[str, object]:
    """Build a source-bounded TypeDB N-of-M condition check.

    This is deliberately a direct TypeQL query rather than a schema function:
    TypeDB evaluates the cardinality with `reduce count`, while schema compile
    remains small and predictable.  The caller invokes it only after the base
    rule query produced this exact source id.
    """
    clean_source_id = str(source_id or "").strip()
    if not clean_source_id:
        return {
            "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
            "query": "",
            "columns": [],
            "reason": "Native any-condition check needs a source id.",
        }
    plan = typedb_native_match_query(
        rule,
        [],
        scoped_manifest_only=scoped_manifest_only,
        include_any_conditions=True,
    )
    query = str(plan.get("query") or "").strip()
    if not query.startswith("match "):
        return plan
    source_clause = "$source has ontology-id " + typedb_string(clean_source_id) + "; "
    return {
        **plan,
        "query": "match " + source_clause + query[len("match "):],
        "columns": ["sourceId", "sourceLabel"],
    }


def typedb_native_rule_function_name(rule_id: object) -> str:
    raw = str(rule_id or "rule").strip().lower()
    normalized = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    if not normalized:
        normalized = "rule"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return (TYPEDB_SCHEMA_FUNCTION_PREFIX + normalized + "_" + digest)[:120]


def typedb_native_any_helper_definitions(rule: Dict[str, object]) -> List[Dict[str, object]]:
    """Compatibility hook retained for RuleBox management metadata.

    The active v4 compiler emits no helper functions.  N-of-M `any` groups
    are evaluated by a source-bounded TypeQL aggregation after the base
    schema-function match, avoiding both combination explosion and a Python
    fallback decision path.
    """
    del rule
    return []


def typedb_native_function_definition(rule: Dict[str, object]) -> Dict[str, object]:
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    function_name = typedb_native_rule_function_name(rule_id)
    helper_definitions = typedb_native_any_helper_definitions(rule)
    plan = typedb_native_match_query(
        rule,
        [],
        scoped_manifest_only=True,
        # N-of-M `any` clauses are verified as bounded TypeDB reads only when
        # this base function returns a source. Embedding their combinations or
        # an aggregation pipeline in a schema function makes TypeDB compile
        # the rule as one large search space.
        include_any_conditions=False,
    )
    match_query = str(plan.get("query") or "").strip()
    if not match_query:
        return {
            "ruleId": rule_id,
            "nativeRuleId": typedb_native_rule_id(rule_id),
            "functionName": function_name,
            "define": "",
            "redefine": "",
            "reason": str(plan.get("reason") or "match query is empty"),
        }
    body = (
        "fun " + function_name + "($source: ontology-node) -> { ontology-node }:\n"
        + match_query + "\n"
        + "return { $source };"
    )
    return {
        "ruleId": rule_id,
        "nativeRuleId": typedb_native_rule_id(rule_id),
        "functionName": function_name,
        "define": "define\n" + body,
        "redefine": "redefine\n" + body,
        "body": body,
        "helperFunctions": helper_definitions,
        "functionDefinitions": helper_definitions + [{
            "ruleId": rule_id,
            "nativeRuleId": typedb_native_rule_id(rule_id),
            "functionName": function_name,
            "define": "define\n" + body,
            "redefine": "redefine\n" + body,
            "body": body,
        }],
        "matchQuery": match_query,
    }


def typedb_native_function_call_query(
    rule: Dict[str, object],
    target_symbols: Iterable[str] = None,
) -> Dict[str, object]:
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    function_name = typedb_native_rule_function_name(rule_id)
    source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock")
    symbols = clean_symbols_from_payload(list(target_symbols or []))
    clauses = [
        typedb_active_worldview_manifest_clause("$activeManifestPointer", "$activeManifestId"),
        typedb_scoped_manifest_member_clause("$candidate", "candidate", "$activeManifestId"),
        "$candidate isa ontology-node, has ontology-kind " + typedb_string(source_kind) + ";",
    ]
    if symbols:
        clauses.append(typedb_value_match("$candidate", "ontology-symbol", symbols, "==", "sourceSymbol"))
    clauses.extend([
        "let $source in " + function_name + "($candidate);",
        "$source has ontology-id $sourceId;",
        "$source has ontology-label $sourceLabel;",
    ])
    return {
        "ruleId": rule_id,
        "nativeRuleId": typedb_native_rule_id(rule_id),
        "functionName": function_name,
        "query": "match " + " ".join(item for item in clauses if item),
        "columns": ["sourceId", "sourceLabel"],
        "evidenceColumns": [],
        "conditionEvidenceColumns": {},
    }


def typedb_native_condition_check_query(
    condition: Dict[str, object],
    source_id: str,
    index: int,
    scoped_manifest_only: bool = False,
) -> Dict[str, object]:
    """Build one bounded native condition probe for a resolved source.

    The active scoped Manifest is bound once for the probe. This is used only
    after a base schema function has matched, so N-of-M `any` conditions do
    not expand the function compiler or make unrelated sources part of the
    TypeQL search space.
    """
    manifest_id_variable = "$activeManifestId" if scoped_manifest_only else ""
    pattern = typedb_condition_pattern(
        condition,
        index,
        relation_prefix="checkRel",
        target_prefix="checkTarget",
        manifest_id_variable=manifest_id_variable,
    )
    if pattern.get("reason"):
        return {
            "conditionId": str(condition.get("condition_id") or condition.get("conditionId") or ""),
            "query": "",
            "columns": [],
            "reason": str(pattern.get("reason") or ""),
        }
    clauses = (
        [
            typedb_active_worldview_manifest_clause("$activeManifestPointer", manifest_id_variable),
            typedb_scoped_manifest_member_clause("$source", "source", manifest_id_variable),
        ]
        if scoped_manifest_only
        else [typedb_active_abox_member_clause("$source", "source")]
    )
    clauses.extend([
        "$source isa ontology-node, has ontology-id " + typedb_string(source_id) + ";",
        *[str(item) for item in pattern.get("clauses") or [] if str(item or "").strip()],
    ])
    columns = list(pattern.get("columns") or []) or ["sourceId"]
    if "sourceId" in columns:
        clauses.insert(1, "$source has ontology-id $sourceId;")
    return {
        "conditionId": str(condition.get("condition_id") or condition.get("conditionId") or ""),
        "query": "match " + " ".join(clauses),
        "columns": columns,
        "evidenceColumns": list(pattern.get("evidenceColumns") or []),
        "relationIdColumn": str(pattern.get("relationIdColumn") or ""),
    }


def typedb_subject_attribute(field: str) -> str:
    promoted_attribute = TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.get(field) or TYPEDB_PROMOTED_TEXT_ATTRIBUTES.get(field)
    if promoted_attribute:
        return promoted_attribute
    return {
        "source": "ontology-source-value",
        "symbol": "ontology-symbol",
        "kind": "ontology-kind",
        "ontologyBox": "ontology-box",
        "tboxClass": "ontology-tbox-class",
        "profitLossRate": "ontology-profit-loss-rate",
        "value": "ontology-value-number",
        "valueNumber": "ontology-value-number",
    }.get(field, "")


def typedb_target_attribute(field: str) -> str:
    if field in {"minValue", "maxValue"}:
        field = "value"
    promoted_attribute = TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.get(field) or TYPEDB_PROMOTED_TEXT_ATTRIBUTES.get(field)
    if promoted_attribute:
        return promoted_attribute
    return {
        "field": "ontology-field",
        "levelType": "ontology-level-type",
        "dataScope": "ontology-data-scope",
        "domainScope": "ontology-domain-scope",
        "relationScope": "ontology-relation-scope",
        "group": "ontology-group",
        "polarity": "ontology-polarity",
        "eventType": "ontology-event-type",
        "materialityPassed": "ontology-materiality-passed",
        "materialityState": "ontology-materiality-state",
        "relevanceState": "ontology-relevance-state",
        "sourceTrustState": "ontology-source-trust-state",
        "value": "ontology-value-number",
        "tboxClass": "ontology-tbox-class",
        "tboxClasses": "ontology-tbox-class",
        "allowAddOnStrength": "ontology-allow-add-on-strength",
        "trimOnTrendBreak": "ontology-trim-on-trend-break",
        "avoidAveragingDown": "ontology-avoid-averaging-down",
        "impactPolarity": "ontology-impact-polarity",
        "needsReview": "ontology-needs-review",
        "readScope": "ontology-read-scope",
        "peRatio": "ontology-pe-ratio",
        "beta": "ontology-beta",
    }.get(field, "")


def typedb_relation_attribute(field: str) -> str:
    return {
        "field": "ontology-field",
        "signalGroup": "ontology-signal-group",
        "polarity": "ontology-polarity",
        "transitionType": "ontology-transition-type",
        "materialityPassed": "ontology-materiality-passed",
        "materialityState": "ontology-materiality-state",
        "relevanceState": "ontology-relevance-state",
        "sourceTrustState": "ontology-source-trust-state",
        "evidenceRole": "ontology-evidence-role",
        "reviewLevel": "ontology-review-level",
        "dataState": "ontology-data-state",
        "changeState": "ontology-change-state",
        "conflictState": "ontology-conflict-state",
    }.get(field, "")


def typedb_value_match(owner_var: str, attribute: str, expected: object, operator: str, value_var: str) -> str:
    op = str(operator or "==").strip().lower()
    expected = typedb_expected_value(expected)
    if op in {"exists", "present"}:
        return owner_var + " has " + attribute + " $" + value_var + ";"
    if isinstance(expected, list):
        values = [item for item in expected if item not in (None, "", [], {})]
        if not values:
            return ""
        return " or ".join(["{ " + owner_var + " has " + attribute + " " + typedb_literal_for_attribute(attribute, value) + "; }" for value in values]) + ";"
    if expected in (None, "", [], {}):
        return ""
    if op in {"==", "eq", "in"}:
        return owner_var + " has " + attribute + " " + typedb_literal_for_attribute(attribute, expected) + ";"
    if op in {"!=", "ne", "<=", "lte", ">=", "gte", "<", "lt", ">", "gt"}:
        typeql_op = {"ne": "!=", "lte": "<=", "gte": ">=", "lt": "<", "gt": ">"}.get(op, op)
        return owner_var + " has " + attribute + " $" + value_var + "; $" + value_var + " " + typeql_op + " " + typedb_literal_for_attribute(attribute, expected) + ";"
    return owner_var + " has " + attribute + " " + typedb_literal_for_attribute(attribute, expected) + ";"


def typedb_literal_for_attribute(attribute: str, value: object) -> str:
    value = typedb_expected_value(value)
    if attribute in TYPEDB_STRING_ATTRIBUTES:
        return typedb_string("true" if value is True else "false" if value is False else value)
    if attribute in TYPEDB_NUMERIC_ATTRIBUTES:
        numeric = typedb_number(value)
        if numeric is not None and not isinstance(value, bool):
            return str(numeric)
    return typedb_literal(value)


def typedb_literal(value: object) -> str:
    value = typedb_expected_value(value)
    numeric = typedb_number(value)
    if numeric is not None and not isinstance(value, bool):
        return str(numeric)
    return typedb_string("true" if value is True else "false" if value is False else value)


def typedb_expected_value(value: object) -> object:
    if isinstance(value, dict):
        if value.get("default") not in (None, "", [], {}):
            return value.get("default")
        if value.get("value") not in (None, "", [], {}):
            return value.get("value")
        return ""
    return value


def typedb_inferencebox_graph(
    graph: PortfolioOntology,
    generation_id: str = None,
    generation_at: str = None,
    rulebox_metadata: Dict[str, object] = None,
) -> PortfolioOntology:
    generation_id = str(generation_id or inference_generation_id())
    generation_at = str(generation_at or utc_now())
    rulebox_metadata = dict(rulebox_metadata or {})
    reasoning_mode = str(rulebox_metadata.get("reasoningMode") or TYPEDB_NATIVE_REASONING_MODE)
    materialization_source = str(rulebox_metadata.get("materializationSource") or TYPEDB_NATIVE_MATERIALIZATION_SOURCE)
    inference_graph = PortfolioOntology(str(graph.portfolio_id or "typedb-inferencebox"))
    id_map: Dict[str, str] = {}
    for item in graph.entities:
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox":
            id_map[item.entity_id] = generated_inference_id(item.entity_id, generation_id)
    for item in graph.evidence:
        if str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "")) == "InferenceBox":
            id_map[item.evidence_id] = generated_inference_id(item.evidence_id, generation_id)
    inference_graph.entities = [
        OntologyEntity(
            id_map.get(item.entity_id, item.entity_id),
            item.label,
            item.kind,
            typedb_reasoned_properties(item.properties, generation_id, generation_at, item.entity_id, rulebox_metadata),
        )
        for item in graph.entities
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox"
    ]
    inference_graph.relations = [
        OntologyRelation(
            id_map.get(item.source, item.source),
            id_map.get(item.target, item.target),
            item.relation_type,
            item.weight,
            [id_map.get(value, value) for value in list(item.evidence_ids or [])],
            typedb_reasoned_properties(item.properties, generation_id, generation_at, rulebox_metadata=rulebox_metadata),
        )
        for item in graph.relations
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox"
    ]
    inference_graph.evidence = [
        OntologyEvidence(
            id_map.get(item.evidence_id, item.evidence_id),
            id_map.get(item.subject, item.subject),
            item.kind,
            item.source,
            item.summary,
            typedb_reasoned_properties(item.value, generation_id, generation_at, item.evidence_id, rulebox_metadata),
            item.evidence_role,
            item.data_state,
        )
        for item in graph.evidence
        if str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "")) == "InferenceBox"
    ]
    inference_graph.beliefs = []
    inference_graph.worldview = {
        "reasoningMode": reasoning_mode,
        "materializationSource": materialization_source,
        "inferenceGenerationId": generation_id,
        "inferenceGenerationAt": generation_at,
        **rulebox_metadata,
    }
    ensure_inference_reference_entities(inference_graph, graph)
    return dedupe_inferencebox_graph(inference_graph)


def ensure_inference_reference_entities(
    inference_graph: PortfolioOntology,
    source_graph: PortfolioOntology,
) -> None:
    """Materialize external inference endpoints into the same generation.

    Native rule materialization creates paths such as ``stock -> trace`` and
    ``rule -> trace``.  The source stock and rule remain in ABox/RuleBox, but
    a TypeDB assertion in an immutable InferenceBox generation must resolve
    both of its endpoint nodes in that generation.  Persisting a compact
    reference node keeps the inference path self-contained and avoids linking
    a new result to a stale ABox generation.
    """
    worldview = dict(getattr(inference_graph, "worldview", {}) or {})
    generation_id = str(worldview.get("inferenceGenerationId") or "")
    generation_at = str(worldview.get("inferenceGenerationAt") or "")
    source_entities = {
        str(item.entity_id or ""): item
        for item in list(getattr(source_graph, "entities", []) or [])
        if str(item.entity_id or "")
    }
    known_ids = {
        str(item.entity_id or "")
        for item in list(getattr(inference_graph, "entities", []) or [])
        if str(item.entity_id or "")
    }
    known_ids.update(
        str(item.evidence_id or "")
        for item in list(getattr(inference_graph, "evidence", []) or [])
        if str(item.evidence_id or "")
    )
    known_ids.update(
        str(item.belief_id or "")
        for item in list(getattr(inference_graph, "beliefs", []) or [])
        if str(item.belief_id or "")
    )

    endpoints = set()
    native_rule_ids_by_endpoint: Dict[str, List[str]] = {}
    for relation in list(getattr(inference_graph, "relations", []) or []):
        relation_endpoints = [str(relation.source or ""), str(relation.target or "")]
        endpoints.update(relation_endpoints)
        relation_properties = dict(getattr(relation, "properties", {}) or {})
        native_rule_id = str(
            relation_properties.get("nativeRuleId")
            or typedb_native_rule_id(relation_properties.get("ruleId"))
            or ""
        ).strip()
        if native_rule_id:
            for endpoint_id in relation_endpoints:
                if endpoint_id and native_rule_id not in native_rule_ids_by_endpoint.setdefault(endpoint_id, []):
                    native_rule_ids_by_endpoint[endpoint_id].append(native_rule_id)
    for evidence in list(getattr(inference_graph, "evidence", []) or []):
        endpoints.add(str(evidence.subject or ""))
    for belief in list(getattr(inference_graph, "beliefs", []) or []):
        endpoints.add(str(belief.subject or ""))

    for endpoint_id in sorted(value for value in endpoints if value and value not in known_ids):
        source = source_entities.get(endpoint_id)
        source_properties = dict((source.properties if source else {}) or {})
        source_kind = str((source.kind if source else "") or endpoint_id.split(":", 1)[0] or "reference")
        source_box = str(source_properties.get("ontologyBox") or "external")
        symbol = str(source_properties.get("symbol") or symbol_from_subject(endpoint_id) or "").upper()
        label = str((source.label if source else "") or endpoint_id)
        native_rule_ids = sorted(native_rule_ids_by_endpoint.get(endpoint_id, []))
        inference_graph.entities.append(OntologyEntity(
            endpoint_id,
            label,
            "inference-context-reference",
            typedb_reasoned_properties({
                "ontologyBox": "InferenceBox",
                "tboxClass": "InferenceContextReference",
                "tboxClasses": ["InferenceContextReference"],
                "symbol": symbol,
                "referenceEntityId": endpoint_id,
                "referenceEntityKind": source_kind,
                "referenceOntologyBox": source_box,
                "referenceOnly": True,
                "nativeRuleId": native_rule_ids[0] if native_rule_ids else "",
                "nativeRuleIds": native_rule_ids,
            }, generation_id, generation_at, endpoint_id, worldview),
        ))
        known_ids.add(endpoint_id)


def dedupe_inferencebox_graph(graph: PortfolioOntology) -> PortfolioOntology:
    entities_by_id: Dict[str, OntologyEntity] = {}
    for item in graph.entities:
        existing = entities_by_id.get(item.entity_id)
        if existing is None:
            entities_by_id[item.entity_id] = item
            continue
        existing.properties = merge_ontology_properties(existing.properties, item.properties)

    relations_by_id: Dict[str, OntologyRelation] = {}
    for item in graph.relations:
        row_id = relation_row_id({
            "source": item.source,
            "target": item.target,
            "type": item.relation_type,
            "ontologyBox": (item.properties or {}).get("ontologyBox"),
            "snapshotId": (item.properties or {}).get("snapshotId"),
            "aboxSnapshotId": (item.properties or {}).get("aboxSnapshotId"),
            "ruleId": (item.properties or {}).get("ruleId"),
        })
        existing = relations_by_id.get(row_id)
        if existing is None:
            relations_by_id[row_id] = item
            continue
        existing.weight = 1.0
        existing.evidence_ids = list(dict.fromkeys(list(existing.evidence_ids or []) + list(item.evidence_ids or [])))
        existing.properties = merge_ontology_properties(existing.properties, item.properties)

    evidence_by_id: Dict[str, OntologyEvidence] = {}
    for item in graph.evidence:
        existing = evidence_by_id.get(item.evidence_id)
        if existing is None:
            evidence_by_id[item.evidence_id] = item
            continue
        existing.value = merge_ontology_properties(existing.value, item.value)
        if existing.evidence_role == "context" and item.evidence_role != "context":
            existing.evidence_role = item.evidence_role
        if existing.data_state == "sufficient" and item.data_state != "sufficient":
            existing.data_state = item.data_state

    return PortfolioOntology(
        graph.portfolio_id,
        entities=list(entities_by_id.values()),
        relations=list(relations_by_id.values()),
        evidence=list(evidence_by_id.values()),
        beliefs=list(graph.beliefs or []),
        opinions=list(graph.opinions or []),
        reasoning_cards=list(graph.reasoning_cards or []),
        worldview=dict(graph.worldview or {}),
        prompt=graph.prompt,
    )


def merge_ontology_properties(left: Dict[str, object], right: Dict[str, object]) -> Dict[str, object]:
    merged = dict(left or {})
    for key, value in dict(right or {}).items():
        if value in (None, "", [], {}):
            continue
        current = merged.get(key)
        if current in (None, "", [], {}):
            merged[key] = value
        elif isinstance(current, list) and isinstance(value, list):
            merged[key] = list(dict.fromkeys(current + value))
    return merged


def typedb_reasoned_properties(
    properties: Dict[str, object],
    generation_id: str = "",
    generation_at: str = "",
    original_id: str = "",
    rulebox_metadata: Dict[str, object] = None,
) -> Dict[str, object]:
    payload = dict(properties or {})
    for key, value in dict(rulebox_metadata or {}).items():
        if value not in (None, "", [], {}):
            payload.setdefault(key, value)
    source_rule_id = str(payload.get("sourceRuleId") or payload.get("ruleId") or "").strip()
    native_rule_id = str(payload.get("nativeRuleId") or typedb_native_rule_id(source_rule_id)).strip()
    if source_rule_id:
        payload.setdefault("sourceRuleId", source_rule_id)
    if native_rule_id:
        payload.setdefault("nativeRuleId", native_rule_id)
        payload.setdefault("semanticRuleId", native_rule_id)
    payload.setdefault("ontologyBox", "InferenceBox")
    payload.setdefault("box", "InferenceBox")
    payload["nativeTypeDbReasoned"] = True
    payload["typedbNativeRuleReasoned"] = True
    payload["typedbNativeRuleMaterializationUsed"] = True
    payload["typedbSchemaFunctionReasoned"] = True
    payload["typedbSchemaFunctionMaterializationUsed"] = True
    payload["typeDbMaterialized"] = True
    payload["graphInferenceUsed"] = True
    payload["typedbMaterialized"] = True
    payload["reasoningMode"] = str((rulebox_metadata or {}).get("reasoningMode") or TYPEDB_NATIVE_REASONING_MODE)
    payload["materializationSource"] = str((rulebox_metadata or {}).get("materializationSource") or TYPEDB_NATIVE_MATERIALIZATION_SOURCE)
    payload.setdefault("reasoningLayer", str((rulebox_metadata or {}).get("reasoningLayer") or TYPEDB_NATIVE_REASONING_LAYER))
    payload.setdefault("typedbNativeRuleEngineVersion", TYPEDB_NATIVE_RULE_ENGINE_VERSION)
    if generation_id:
        payload["inferenceGenerationId"] = generation_id
        payload["snapshotId"] = generation_id
        payload["aboxSnapshotId"] = generation_id
    if generation_at:
        payload["inferenceGenerationAt"] = generation_at
        payload["asOf"] = generation_at
    if original_id:
        payload["originalId"] = original_id
    return payload


def inference_rulebox_metadata(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    keys = [
        "ruleboxRulesHash",
        "ruleboxShortHash",
        "ruleboxRuleCount",
        "ruleboxConditionCount",
        "ruleboxDerivationCount",
        "ruleboxEngineVersion",
        "reasoningMode",
        "materializationSource",
        "reasoningLayer",
        "typedbNativeRuleEngineVersion",
        "typedbNativeRuleProfileVersion",
        "typedbNativeRuleProfileStatus",
        "typedbNativeRuleCount",
        "typedbNativeReadyRuleCount",
        "typedbNativePartialRuleCount",
        "typedbNativeBlockedRuleCount",
        "typedbNativeRuleQueryStatus",
        "typedbNativeRuleQueryUsed",
        "typedbSchemaFunctionQueryUsed",
        "typedbSchemaFunctionUsed",
        "typedbSchemaFunctionSyncStatus",
        "typedbSchemaFunctionSyncCached",
        "typedbSchemaFunctionSyncedCount",
        "typedbSchemaFunctionSkippedCount",
        "typedbSchemaFunctionFailedCount",
        "typedbNativeRuleMatchedCount",
        "typedbNativeRuleExecutedCount",
        "typedbNativeRuleSkippedCount",
        "pythonCompatibilityReasonerUsed",
        "typeDbNativeRulesPrimary",
        "ruleStore",
        "sourceAboxSnapshotId",
        "sourceAboxSnapshotCount",
        "targetSymbols",
        "incrementalScope",
        "impactPlanVersion",
        "inferenceImpactPlan",
        "ruleExecutionScope",
        "nativeRuleSelectionApplied",
        "nativeRuleSelectionFallbackReason",
        "nativeRuleSelectionCandidateCount",
        "nativeRuleSelectionPriorMatchedCount",
        "nativeRuleSelectionExecutedCount",
        "nativeRuleSelectionDeferredCount",
        "nativeRuleSelectionExecutedRuleIds",
        "nativeRuleSelectionDeferredRuleIds",
    ]
    metadata: Dict[str, object] = {}
    for row in list(entity_rows or []) + list(relation_rows or []):
        if not isinstance(row, dict):
            continue
        props = json_object(row.get("propertiesJson"))
        props.update(json_object(row.get("valueJson")))
        source = {**props, **row}
        for key in keys:
            value = source.get(key)
            if value not in (None, "", [], {}) and key not in metadata:
                metadata[key] = value
        if all(key in metadata for key in keys):
            break
    for key in [
        "ruleboxRuleCount",
        "ruleboxConditionCount",
        "ruleboxDerivationCount",
        "typedbNativeRuleCount",
        "typedbNativeReadyRuleCount",
        "typedbNativePartialRuleCount",
        "typedbNativeBlockedRuleCount",
        "typedbNativeRuleMatchedCount",
        "typedbNativeRuleExecutedCount",
        "typedbNativeRuleSkippedCount",
        "typedbSchemaFunctionSyncedCount",
        "typedbSchemaFunctionSkippedCount",
        "typedbSchemaFunctionFailedCount",
        "sourceAboxSnapshotCount",
        "nativeRuleSelectionCandidateCount",
        "nativeRuleSelectionPriorMatchedCount",
        "nativeRuleSelectionExecutedCount",
        "nativeRuleSelectionDeferredCount",
    ]:
        if key in metadata:
            metadata[key] = int(number_or_none(metadata.get(key)) or 0)
    return metadata


def row_inference_generation_id(row: Dict[str, object]) -> str:
    if not isinstance(row, dict):
        return ""
    direct = str(row.get("inferenceGenerationId") or row.get("snapshotId") or row.get("aboxSnapshotId") or "").strip()
    if direct:
        return direct
    try:
        props = json.loads(str(row.get("propertiesJson") or "{}"))
    except json.JSONDecodeError:
        props = {}
    return str((props or {}).get("inferenceGenerationId") or (props or {}).get("snapshotId") or "").strip()


def row_inference_generation_at(row: Dict[str, object]) -> str:
    if not isinstance(row, dict):
        return ""
    direct = str(row.get("inferenceGenerationAt") or row.get("asOf") or row.get("updatedAt") or "").strip()
    if direct:
        return direct
    try:
        props = json.loads(str(row.get("propertiesJson") or "{}"))
    except json.JSONDecodeError:
        props = {}
    return str((props or {}).get("inferenceGenerationAt") or (props or {}).get("asOf") or "").strip()


def inference_generation_marker_row(
    graph: PortfolioOntology,
    node_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
    publication_status: str = "candidate",
) -> Dict[str, object]:
    worldview = dict(graph.worldview or {})
    generation_id = str(worldview.get("inferenceGenerationId") or "").strip()
    generation_at = str(worldview.get("inferenceGenerationAt") or utc_now()).strip()
    properties = {
        **worldview,
        "ontologyBox": "InferenceBox",
        "tboxClass": "ActiveGeneration" if publication_status == "active" else "CandidateGeneration",
        "tboxClasses": ["InferenceGeneration", "ActiveGeneration" if publication_status == "active" else "CandidateGeneration"],
        "publicationStatus": publication_status,
        "candidateCreatedAt": generation_at,
        "activatedAt": generation_at if publication_status == "active" else "",
        "expectedEntityCount": len(list(node_rows or [])),
        "expectedRelationCount": len(list(relation_rows or [])),
        "nativeTypeDbReasoned": True,
    }
    return {
        "id": "inference-generation" + ("" if publication_status == "active" else "-candidate") + ":" + generation_id,
        "label": ("Active" if publication_status == "active" else "Candidate") + " InferenceBox " + generation_id,
        "kind": "inference-generation" if publication_status == "active" else "inference-generation-candidate",
        "nodeType": "ontology-entity",
        "ontologyBox": "InferenceBox",
        "snapshotId": generation_id,
        "aboxSnapshotId": generation_id,
        "tboxClass": "ActiveGeneration" if publication_status == "active" else "CandidateGeneration",
        "propertiesJson": json.dumps(properties, ensure_ascii=False, sort_keys=True),
    }


def inference_marker_is_active(raw_json: object) -> bool:
    try:
        payload = json.loads(str(raw_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    status = str((payload or {}).get("publicationStatus") or "").strip().lower()
    return status in {"", "active", "published"}


def inference_generation_delete_queries(generation_id: str) -> List[str]:
    generation_literal = typedb_string(str(generation_id or ""))
    return [
        (
            'match $r isa ontology-assertion, has ontology-box "InferenceBox", '
            "has ontology-snapshot-id " + generation_literal + "; delete $r;"
        ),
        (
            'match $n isa ontology-node, has ontology-box "InferenceBox", '
            "has ontology-snapshot-id " + generation_literal + "; delete $n;"
        ),
    ]


def inference_generation_records(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    records: Dict[str, Dict[str, object]] = {}
    for row in list(entity_rows or []) + list(relation_rows or []):
        generation_id = row_inference_generation_id(row)
        if not generation_id:
            continue
        record = records.setdefault(generation_id, {
            "generationId": generation_id,
            "latestAt": "",
            "entityCount": 0,
            "relationCount": 0,
        })
        latest_at = row_inference_generation_at(row)
        if latest_at > str(record.get("latestAt") or ""):
            record["latestAt"] = latest_at
        if "relationType" in row or "type" in row and row.get("source"):
            record["relationCount"] = int(record.get("relationCount") or 0) + 1
        else:
            record["entityCount"] = int(record.get("entityCount") or 0) + 1
    return sorted(records.values(), key=lambda item: str(item.get("latestAt") or ""), reverse=True)


def active_inference_generation(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    records = inference_generation_records(entity_rows, relation_rows)
    return records[0] if records else {}


def entity_node_kind(row: Dict[str, object]) -> str:
    return str(row.get("nodeKind") or row.get("kind") or "")


def typedb_native_reasoning_profile(rules: Iterable[object]) -> Dict[str, object]:
    rule_payloads = [
        item.to_dict() if hasattr(item, "to_dict") else dict(item)
        for item in (rules or [])
        if isinstance(item, dict) or hasattr(item, "to_dict")
    ]
    rule_profiles = [typedb_native_rule_profile(rule) for rule in rule_payloads]
    ready = [item for item in rule_profiles if item.get("status") == "ready"]
    partial = [item for item in rule_profiles if item.get("status") == "partial"]
    blocked = [item for item in rule_profiles if item.get("status") == "blocked"]
    unsupported = sum(int(item.get("unsupportedConditionCount") or 0) for item in rule_profiles)
    supported = sum(int(item.get("supportedConditionCount") or 0) for item in rule_profiles)
    status = "ready" if rule_profiles and len(ready) == len(rule_profiles) else ("partial" if ready or partial else "blocked")
    blockers = [
        blocker
        for item in rule_profiles
        for blocker in (item.get("blockers") or [])
        if isinstance(blocker, dict)
    ]
    return {
        "version": TYPEDB_NATIVE_REASONING_PROFILE_VERSION,
        "graphStore": "typedb",
        "reasoningModel": "typedb-native-rule-materialization",
        "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
        "status": status,
        "ruleCount": len(rule_profiles),
        "nativeRuleCount": len(rule_profiles),
        "readyRuleCount": len(ready),
        "partialRuleCount": len(partial),
        "blockedRuleCount": len(blocked),
        "supportedConditionCount": supported,
        "unsupportedConditionCount": unsupported,
        "materializationRequired": True,
        "materializationTarget": "InferenceBox",
        "materializationStrategy": "typedb-abox-native-rule-to-inferencebox",
        "reason": "TypeDB ABox facts and stored semantic rules are materialized into TypeDB InferenceBox with native-rule metadata.",
        "readyRules": [item.get("ruleId") for item in ready][:24],
        "readyNativeRules": [item.get("nativeRuleId") for item in ready][:24],
        "partialRules": [item.get("ruleId") for item in partial][:24],
        "partialNativeRules": [item.get("nativeRuleId") for item in partial][:24],
        "blockedRules": [item.get("ruleId") for item in blocked][:24],
        "blockedNativeRules": [item.get("nativeRuleId") for item in blocked][:24],
        "blockers": blockers[:24],
        "rules": rule_profiles[:80],
    }


def typedb_native_rule_profile(rule: Dict[str, object]) -> Dict[str, object]:
    conditions = [item for item in (rule.get("conditions") or []) if isinstance(item, dict)]
    derivations = [item for item in (rule.get("derivations") or []) if isinstance(item, dict)]
    condition_profiles = [typedb_native_condition_profile(item) for item in conditions]
    blockers = [
        blocker
        for item in condition_profiles
        for blocker in (item.get("blockers") or [])
        if isinstance(blocker, dict)
    ]
    supported = [item for item in condition_profiles if item.get("status") == "ready"]
    partial = [item for item in condition_profiles if item.get("status") == "partial"]
    if blockers and not supported and not partial:
        status = "blocked"
    elif blockers:
        status = "partial"
    else:
        status = "ready"
    source_rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    native_rule_id = typedb_native_rule_id(source_rule_id)
    function_definition = typedb_native_function_definition(rule) if status in {"ready", "partial"} else {}
    return {
        "ruleId": source_rule_id,
        "sourceRuleId": source_rule_id,
        "nativeRuleId": native_rule_id,
        "schemaFunctionName": typedb_native_rule_function_name(source_rule_id),
        "label": str(rule.get("label") or ""),
        "status": status,
        "conditionCount": len(conditions),
        "derivationCount": len(derivations),
        "supportedConditionCount": len(supported),
        "unsupportedConditionCount": len(blockers),
        "blockers": blockers,
        "reasoningLayer": TYPEDB_NATIVE_REASONING_LAYER,
        "conditions": condition_profiles,
        "typeqlRuleBlueprint": str(function_definition.get("body") or typedb_function_blueprint(rule)) if status in {"ready", "partial"} else "",
        "functionBlueprint": str(function_definition.get("body") or typedb_function_blueprint(rule)) if status in {"ready", "partial"} else "",
    }


def typedb_native_condition_profile(condition: Dict[str, object]) -> Dict[str, object]:
    condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "")
    kind = str(condition.get("kind") or "")
    blockers: List[Dict[str, object]] = []
    operator = str(condition.get("operator") or "==")
    if operator not in TYPEDB_FUNCTION_OPERATORS:
        blockers.append(condition_blocker(condition_id, "unsupported-operator", "TypeDB function profile does not support operator " + operator))
    if kind == "subject_property":
        field = str(condition.get("field") or "")
        if field not in TYPEDB_FUNCTION_SUBJECT_FIELDS:
            blockers.append(condition_blocker(condition_id, "json-bound-subject-field", field + " is still JSON-bound or not promoted to a TypeDB attribute."))
    elif kind == "relation":
        if not str(condition.get("relation_type") or condition.get("relationType") or ""):
            blockers.append(condition_blocker(condition_id, "missing-relation-type", "Relation condition needs an explicit relation_type."))
        blockers.extend(filter_blockers(condition_id, condition.get("target_property_filters") or condition.get("targetPropertyFilters") or {}, TYPEDB_FUNCTION_TARGET_FILTERS, "target"))
        blockers.extend(filter_blockers(condition_id, condition.get("relation_property_filters") or condition.get("relationPropertyFilters") or {}, TYPEDB_FUNCTION_RELATION_FILTERS, "relation"))
    else:
        blockers.append(condition_blocker(condition_id, "unsupported-condition-kind", kind + " is not mapped to a TypeDB function pattern."))
    return {
        "conditionId": condition_id,
        "kind": kind,
        "status": "ready" if not blockers else "partial",
        "blockers": blockers,
    }


def filter_blockers(condition_id: str, filters: Dict[str, object], supported: set, scope: str) -> List[Dict[str, object]]:
    blockers = []
    for key in sorted((filters or {}).keys()):
        if key not in supported:
            blockers.append(condition_blocker(condition_id, "json-bound-" + scope + "-filter", key + " is not promoted to a TypeDB attribute."))
    return blockers


def condition_blocker(condition_id: str, code: str, reason: str) -> Dict[str, object]:
    return {
        "conditionId": condition_id,
        "code": code,
        "reason": reason,
    }


def typedb_function_blueprint(rule: Dict[str, object]) -> str:
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "rule").replace(".", "_").replace("-", "_")
    source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock")
    return (
        "fun orbit_inference_" + rule_id + "() -> { ontology-node, ontology-node }:\n"
        "  match\n"
        "    $source isa ontology-node, has ontology-kind " + typedb_string(source_kind) + ";\n"
        "    # Semantic rule conditions are represented by promoted ontology-* attributes where available.\n"
        "  return { $source, $source };"
    )


def relation_row_id(row: Dict[str, object]) -> str:
    seed = "|".join([
        str(row.get("source") or ""),
        str(row.get("type") or ""),
        str(row.get("target") or ""),
        str(row.get("ontologyBox") or ""),
        str(row.get("snapshotId") or row.get("aboxSnapshotId") or ""),
        str(row.get("ruleId") or ""),
    ])
    return "ontology-assertion:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def ontology_storage_id(row: Dict[str, object], canonical_id: object, owner_kind: str) -> str:
    """Return a TypeDB-unique persistence identity for one graph row.

    The public ontology ID remains stable across ABox generations. The storage
    identity includes the box and generation so a staging generation can hold
    the same fact beside the active one until promotion is verified.
    """
    payload = "|".join([
        str(owner_kind or "row"),
        str((row or {}).get("ontologyBox") or "ABox"),
        str((row or {}).get("snapshotId") or (row or {}).get("aboxSnapshotId") or ""),
        str(canonical_id or ""),
    ])
    return "ontology-storage:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def symbol_from_subject(value: object) -> str:
    raw = str(value or "")
    if raw.startswith("stock:"):
        return raw.split(":", 1)[1].upper()
    return ""


def rule_id_from_value(value: object) -> str:
    raw = str(value or "")
    if ":graph." in raw:
        return raw.split(":", 2)[-1]
    return ""


def matched_condition_ids(row: Dict[str, object]) -> List[str]:
    try:
        properties = json.loads(str(row.get("propertiesJson") or "{}"))
    except json.JSONDecodeError:
        properties = {}
    matches = properties.get("matchedConditions") if isinstance(properties, dict) else []
    return [
        str(item.get("conditionId") or "")
        for item in (matches or [])
        if isinstance(item, dict) and str(item.get("conditionId") or "")
    ]


def typedb_repository_from_settings(settings: Dict[str, str] = None):
    settings = settings or runtime_settings()
    enabled = str(settings.get("ontologyTypeDbEnabled") or "0").strip().lower() not in {"0", "false", "no", "off"}
    address = str(settings.get("typedbAddress") or "").strip()
    if not enabled or not address:
        return NullTypeDBOntologyGraphRepository()
    timeout_seconds = int(settings.get("typedbTimeoutSeconds") or 20)
    query_metrics_value = settings.get("typedbQueryMetricsEnabled")
    native_execution_value = settings.get("ontologyReasoningTypeDbNativeRuleExecutionEnabled")
    if native_execution_value in (None, ""):
        native_execution_value = settings.get("typedbNativeRuleExecutionEnabled")
    return TypeDBOntologyGraphRepository(
        address=address,
        user=str(settings.get("typedbUser") or "admin"),
        password=str(settings.get("typedbPassword") or "password"),
        database=str(settings.get("typedbDatabase") or "orbit_alpha_ontology"),
        tls_enabled=typedb_bool(settings.get("typedbTlsEnabled")),
        timeout_seconds=timeout_seconds,
        retry_count=int(number_or_none(settings.get("typedbRetryCount")) or 2),
        inference_generation_keep_count=int(number_or_none(settings.get("typedbInferenceGenerationKeepCount")) or 1),
        query_timeout_seconds=number_or_none(settings.get("typedbQueryTimeoutSeconds")) or float(timeout_seconds or 20),
        schema_operation_timeout_seconds=number_or_none(settings.get("typedbSchemaOperationTimeoutSeconds")) or float(timeout_seconds or 20),
        write_operation_timeout_seconds=number_or_none(settings.get("typedbWriteOperationTimeoutSeconds")) or float(timeout_seconds or 20),
        condition_detail_queries_enabled=typedb_bool(settings.get("typedbConditionDetailQueriesEnabled")),
        query_metrics_enabled=True if query_metrics_value in (None, "") else typedb_bool(query_metrics_value),
        rulebox_snapshot_cache_seconds=number_or_none(settings.get("typedbRuleBoxSnapshotCacheSeconds")) or 60.0,
        native_rule_execution_enabled=True if native_execution_value in (None, "") else typedb_bool(native_execution_value),
        native_rule_query_timeout_seconds=number_or_none(settings.get("typedbNativeRuleQueryTimeoutSeconds"))
        or DEFAULT_TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS,
        native_rule_execution_budget_seconds=number_or_none(settings.get("typedbNativeRuleExecutionBudgetSeconds"))
        or DEFAULT_TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS,
        inference_write_lease_enabled=True
        if settings.get("typedbInferenceWriteLeaseEnabled") in (None, "")
        else typedb_bool(settings.get("typedbInferenceWriteLeaseEnabled")),
    )
