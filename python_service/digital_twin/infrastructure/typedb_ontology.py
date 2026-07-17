import copy
import hashlib
import itertools
import json
import math
import re
import signal
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Dict, Iterable, List, Tuple

from ..domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from ..domain.ontology_inference_materializer import materialize_rule_inference
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from ..domain.ontology_rulebox_governance import (
    normalize_rule_change_candidate,
    rulebox_governance_candidates,
    rulebox_rules_hash,
)
from ..domain.ontology_schema import default_tbox_metadata
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
from .graph_store_payloads import GraphStoreOntologyRowMapperMixin, list_of_strings, number_or_none
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


def typedb_concept_value(concept: object):
    if concept is None:
        return None
    get_value = getattr(concept, "get_value", None)
    if callable(get_value):
        return get_value()
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


TYPEDB_NATIVE_REASONING_PROFILE_VERSION = "typedb-native-rule-profile-v2"
TYPEDB_NATIVE_RULE_ENGINE_VERSION = "typedb-schema-function-rule-engine-v1"
TYPEDB_NATIVE_REASONING_MODE = "typedb-native-rule-materialized"
TYPEDB_NATIVE_BLOCKED_MODE = "typedb-native-rule-materialization-blocked"
TYPEDB_NATIVE_REQUIRED_MODE = "typedb-native-rule-materialization-required"
TYPEDB_NATIVE_MATERIALIZATION_SOURCE = "typedb-abox-native-rule"
TYPEDB_NATIVE_REASONING_LAYER = "typedb-native-rule"
TYPEDB_SCHEMA_FUNCTION_PREFIX = "orbit_rule_"
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
    "investorFlowScore": "ontology-investor-flow-score",
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
    "marginOfSafetyPct": "ontology-margin-of-safety-pct",
    "expensivePremiumPct": "ontology-expensive-premium-pct",
    "minimumMarginOfSafetyPct": "ontology-minimum-margin-of-safety-pct",
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
    "profitLossRateStart": "ontology-profit-loss-rate-start",
    "profitLossRateEnd": "ontology-profit-loss-rate-end",
    "profitLossRateChangePct": "ontology-profit-loss-rate-change-pct",
    "ma20DistanceStart": "ontology-ma20-distance-start",
    "ma20DistanceEnd": "ontology-ma20-distance-end",
    "ma20DistanceChange": "ontology-ma20-distance-change",
    "ma60DistanceStart": "ontology-ma60-distance-start",
    "ma60DistanceEnd": "ontology-ma60-distance-end",
    "volumeRatioEnd": "ontology-volume-ratio-end",
    "tradeStrengthEnd": "ontology-trade-strength-end",
    "bidAskImbalanceEnd": "ontology-bid-ask-imbalance-end",
    "smartMoneyNetLatest": "ontology-smart-money-net-latest",
    "smartMoneyNetChange": "ontology-smart-money-net-change",
    "individualNetLatest": "ontology-individual-net-latest",
    "eventCount": "ontology-event-count",
    "riskEventCount": "ontology-risk-event-count",
    "supportEventCount": "ontology-support-event-count",
    "temporalRiskScore": "ontology-temporal-risk-score",
    "temporalSupportScore": "ontology-temporal-support-score",
}
TYPEDB_PROMOTED_TEXT_ATTRIBUTES = {
    "investmentStrategyProfile": "ontology-investment-strategy-profile",
    "investmentStrategyProfileLabel": "ontology-investment-strategy-profile-label",
    "positionRole": "ontology-position-role",
    "targetPositionRole": "ontology-target-position-role",
    "positionIntent": "ontology-position-intent",
    "instrumentArchetype": "ontology-instrument-archetype",
    "instrumentArchetypes": "ontology-instrument-archetype",
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
    "windowKey": "ontology-window-key",
    "hasSufficientHistory": "ontology-has-sufficient-history",
    "pricePathPattern": "ontology-price-path-pattern",
    "flowPattern": "ontology-flow-pattern",
    "eventClusterType": "ontology-event-cluster-type",
    "trendEpisodeType": "ontology-trend-episode-type",
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
    "minMaterialityScore",
    "minValue",
    "maxValue",
    "tboxClass",
    "tboxClasses",
    "allowAddOnStrength",
    "trimOnTrendBreak",
    "avoidAveragingDown",
    "confidence",
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
    "minMaterialityScore",
    "minRiskImpact",
    "minSupportImpact",
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
    "ontology-confidence",
    "ontology-value-number",
    "ontology-profit-loss-rate",
    "ontology-materiality-score",
    "ontology-risk-impact",
    "ontology-support-impact",
    "ontology-stage-priority",
    "ontology-pe-ratio",
    "ontology-beta",
} | set(TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.values())


class NullTypeDBOntologyGraphRepository:
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
        graph = ontology_seed_graph()
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

    def inferencebox_snapshot(self, symbols: List[str] = None, limit: int = 80) -> Dict[str, object]:
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


class TypeDBOntologyGraphRepository(GraphStoreOntologyRowMapperMixin):
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
        self._last_graph = None
        self._last_rules: List[GraphInferenceRule] = []
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
        try:
            def operation():
                with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB graph save"):
                    driver = self.open_driver(imported)
                    try:
                        self.ensure_database(driver)
                        self.ensure_schema(driver, imported)
                        self.write_graph(driver, imported, graph)
                    finally:
                        self.close_driver(driver)
            self.with_typedb_retries(operation)
        except Exception as error:  # noqa: BLE001 - graph-store persistence must not block monitoring.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "reason": str(error)[:240],
                "entityCount": len(graph.entities),
                "relationCount": len(graph.relations),
                "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
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
            "inferenceBoxEntityCount": box_entity_counts.get("InferenceBox", 0),
            "tboxRelationCount": box_relation_counts.get("TBox", 0),
            "aboxRelationCount": box_relation_counts.get("ABox", 0),
            "ruleBoxRelationCount": box_relation_counts.get("RuleBox", 0),
            "inferenceBoxRelationCount": box_relation_counts.get("InferenceBox", 0),
            "evidenceCount": len(graph.evidence),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
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
                request_timeout_millis=max(1000, int(self.timeout_seconds * 1000)),
            ),
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

    def ensure_schema(self, driver, imported) -> None:
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        with typedb_operation_timeout(self.schema_operation_timeout_seconds(), "TypeDB base schema sync"):
            with driver.transaction(self.database, TransactionType.SCHEMA) as tx:
                tx.query(self.schema_query()).resolve()
                tx.commit()

    def read_rows(self, query: str, columns: Iterable[str], label: str = "typedb.read") -> List[Dict[str, object]]:
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
                with driver.transaction(self.database, TransactionType.READ) as tx:
                    return self.read_rows_in_transaction(tx, query, columns, label=label)
            finally:
                self.close_driver(driver)
        return self.with_typedb_retries(operation)

    def read_rows_in_transaction(self, tx, query: str, columns: Iterable[str], label: str = "typedb.read") -> List[Dict[str, object]]:
        started_at = time.perf_counter()
        rows: List[Dict[str, object]] = []
        status = "ok"
        try:
            with typedb_operation_timeout(self.query_timeout_seconds(), "TypeDB read query"):
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
        query = (
            "match $n isa ontology-node, has ontology-box "
            + typedb_string(box)
            + "; limit 1;"
        )
        return bool(self.read_rows(query, []))

    def read_entity_rows(self, boxes: Iterable[str] = None, limit: int = 0) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        safe_limit = int(limit or 0)
        for box in normalized_boxes(boxes):
            query = (
                "match $n isa ontology-node, "
                "has ontology-id $id, "
                "has ontology-label $label, "
                "has ontology-kind $kind, "
                "has ontology-box " + typedb_string(box) + ", "
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
            query = (
                "match $n isa ontology-node, "
                "has ontology-id $id, "
                "has ontology-label $label, "
                "has ontology-kind $kind, "
                "has ontology-box " + typedb_string(box) + ", "
                "has ontology-updated-at $updatedAt, "
                "has ontology-json $json; "
                + id_filter
            )
            rows.extend(self.entity_rows_from_typeql(self.read_rows(
                query,
                ["id", "label", "kind", "updatedAt", "json"],
            ), box))
        return rows

    def read_relation_rows(self, boxes: Iterable[str] = None, limit: int = 0) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        safe_limit = int(limit or 0)
        for box in normalized_boxes(boxes):
            query = (
                "match "
                "$source isa ontology-node, has ontology-id $sourceId, has ontology-label $sourceLabel; "
                "$target isa ontology-node, has ontology-id $targetId, has ontology-label $targetLabel; "
                "$r isa ontology-assertion, links (source: $source, target: $target), "
                "has ontology-id $id, "
                "has ontology-relation-type $type, "
                "has ontology-box " + typedb_string(box) + ", "
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

    def read_inference_generation_records(self) -> List[Dict[str, object]]:
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
        return inference_generation_records(indexed_rows, [])

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
            "conditionMinWeight": float(number_or_none(condition.get("min_weight") if "min_weight" in condition else merged.get("conditionMinWeight")) or 0),
            "derivationIndex": int(number_or_none(merged.get("derivationIndex")) or 0),
            "derivationRelationType": str(derivation.get("relation_type") or merged.get("derivationRelationType") or "").upper(),
            "derivationTargetKind": str(derivation.get("target_kind") or merged.get("derivationTargetKind") or ""),
            "derivationTargetKey": str(derivation.get("target_key") or merged.get("derivationTargetKey") or ""),
            "derivationTargetLabel": str(derivation.get("target_label") or merged.get("derivationTargetLabel") or ""),
            "derivationTboxClass": str(derivation.get("tbox_class") or merged.get("derivationTboxClass") or ""),
            "derivationTboxClasses": list_of_strings(derivation.get("tbox_classes") or merged.get("derivationTboxClasses")),
            "derivationPolarity": str(derivation.get("polarity") or merged.get("derivationPolarity") or ""),
            "derivationRiskImpact": number_or_none(derivation.get("risk_impact") or merged.get("derivationRiskImpact")),
            "derivationSupportImpact": number_or_none(derivation.get("support_impact") or merged.get("derivationSupportImpact")),
            "derivationWeight": number_or_none(derivation.get("weight") or merged.get("derivationWeight")),
            "derivationBeliefLabel": str(derivation.get("belief_label") or merged.get("derivationBeliefLabel") or ""),
            "derivationAiInfluenceLabel": str(derivation.get("ai_influence_label") or merged.get("derivationAiInfluenceLabel") or ""),
            "derivationActionGroup": str(derivation.get("action_group") or merged.get("derivationActionGroup") or ""),
            "derivationActionLevel": str(derivation.get("action_level") or merged.get("derivationActionLevel") or ""),
            "derivationDecisionStage": str(derivation.get("decision_stage") or derivation.get("decisionStage") or merged.get("derivationDecisionStage") or ""),
            "derivationStagePriority": number_or_none(derivation.get("stage_priority") or derivation.get("stagePriority") or merged.get("derivationStagePriority")),
            "derivationTargetRole": str(derivation.get("target_role") or derivation.get("targetRole") or merged.get("derivationTargetRole") or ""),
            "derivationActionPolicy": str(derivation.get("action_policy") or derivation.get("actionPolicy") or merged.get("derivationActionPolicy") or ""),
            "derivationAllowedActions": list_of_strings(derivation.get("allowed_actions") or derivation.get("allowedActions") or merged.get("derivationAllowedActions")),
            "derivationBlockedActions": list_of_strings(derivation.get("blocked_actions") or derivation.get("blockedActions") or merged.get("derivationBlockedActions")),
            "polarity": str(merged.get("polarity") or ""),
            "confidence": number_or_none(merged.get("confidence")),
            "decisionStage": str(merged.get("decisionStage") or ""),
            "stagePriority": number_or_none(merged.get("stagePriority")),
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
            "riskImpact": number_or_none(merged.get("riskImpact")),
            "supportImpact": number_or_none(merged.get("supportImpact")),
            "decisionStage": str(merged.get("decisionStage") or ""),
            "stagePriority": number_or_none(merged.get("stagePriority")),
            "targetRole": str(merged.get("targetRole") or ""),
            "actionPolicy": str(merged.get("actionPolicy") or ""),
            "allowedActions": list_of_strings(merged.get("allowedActions")),
            "blockedActions": list_of_strings(merged.get("blockedActions")),
            "aiInfluenceLabel": str(merged.get("aiInfluenceLabel") or ""),
            "inferenceTraceId": str(merged.get("inferenceTraceId") or ""),
            "nativeTypeDbReasoned": bool(merged.get("nativeTypeDbReasoned")),
        }

    def write_graph(self, driver, imported, graph: PortfolioOntology) -> None:
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        boxes = node_boxes(graph)
        queries = self.delete_queries(boxes) + self.insert_queries(graph)
        if not queries:
            return
        with driver.transaction(self.database, TransactionType.WRITE) as tx:
            for query in queries:
                tx.query(query).resolve()
            tx.commit()

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
attribute ontology-label, value string;
attribute ontology-kind, value string;
attribute ontology-box, value string;
attribute ontology-symbol, value string;
attribute ontology-rule-id, value string;
attribute ontology-account-id, value string;
attribute ontology-snapshot-id, value string;
attribute ontology-tbox-class, value string;
attribute ontology-relation-type, value string;
attribute ontology-updated-at, value string;
attribute ontology-json, value string;
attribute ontology-weight, value double;
attribute ontology-confidence, value double;
attribute ontology-source-value, value string;
attribute ontology-field, value string;
attribute ontology-level-type, value string;
attribute ontology-data-scope, value string;
attribute ontology-domain-scope, value string;
attribute ontology-relation-scope, value string;
attribute ontology-group, value string;
attribute ontology-polarity, value string;
attribute ontology-transition-type, value string;
attribute ontology-signal-group, value string;
attribute ontology-event-type, value string;
attribute ontology-materiality-passed, value string;
attribute ontology-value-number, value double;
attribute ontology-profit-loss-rate, value double;
attribute ontology-materiality-score, value double;
attribute ontology-risk-impact, value double;
attribute ontology-support-impact, value double;
attribute ontology-stage-priority, value double;
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
attribute ontology-investor-flow-score, value double;
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
attribute ontology-margin-of-safety-pct, value double;
attribute ontology-expensive-premium-pct, value double;
attribute ontology-minimum-margin-of-safety-pct, value double;
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
attribute ontology-profit-loss-rate-start, value double;
attribute ontology-profit-loss-rate-end, value double;
attribute ontology-profit-loss-rate-change-pct, value double;
attribute ontology-ma20-distance-start, value double;
attribute ontology-ma20-distance-end, value double;
attribute ontology-ma20-distance-change, value double;
attribute ontology-ma60-distance-start, value double;
attribute ontology-ma60-distance-end, value double;
attribute ontology-volume-ratio-end, value double;
attribute ontology-trade-strength-end, value double;
attribute ontology-bid-ask-imbalance-end, value double;
attribute ontology-smart-money-net-latest, value double;
attribute ontology-smart-money-net-change, value double;
attribute ontology-individual-net-latest, value double;
attribute ontology-event-count, value double;
attribute ontology-risk-event-count, value double;
attribute ontology-support-event-count, value double;
attribute ontology-temporal-risk-score, value double;
attribute ontology-temporal-support-score, value double;
attribute ontology-investment-strategy-profile, value string;
attribute ontology-investment-strategy-profile-label, value string;
attribute ontology-position-role, value string;
attribute ontology-target-position-role, value string;
attribute ontology-position-intent, value string;
attribute ontology-instrument-archetype, value string;
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
attribute ontology-window-key, value string;
attribute ontology-has-sufficient-history, value string;
attribute ontology-price-path-pattern, value string;
attribute ontology-flow-pattern, value string;
attribute ontology-event-cluster-type, value string;
attribute ontology-trend-episode-type, value string;

entity ontology-node @abstract,
    owns ontology-id @key,
    owns ontology-label,
    owns ontology-kind,
    owns ontology-box,
    owns ontology-symbol,
    owns ontology-rule-id,
    owns ontology-account-id,
    owns ontology-snapshot-id,
    owns ontology-tbox-class,
    owns ontology-updated-at,
    owns ontology-json,
    owns ontology-confidence,
    owns ontology-source-value,
    owns ontology-field,
    owns ontology-level-type,
    owns ontology-data-scope,
    owns ontology-domain-scope,
    owns ontology-relation-type,
    owns ontology-relation-scope,
    owns ontology-group,
    owns ontology-polarity,
    owns ontology-event-type,
    owns ontology-materiality-passed,
    owns ontology-value-number,
    owns ontology-profit-loss-rate,
    owns ontology-materiality-score,
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
    owns ontology-investor-flow-score,
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
    owns ontology-margin-of-safety-pct,
    owns ontology-expensive-premium-pct,
    owns ontology-minimum-margin-of-safety-pct,
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
    owns ontology-profit-loss-rate-start,
    owns ontology-profit-loss-rate-end,
    owns ontology-profit-loss-rate-change-pct,
    owns ontology-ma20-distance-start,
    owns ontology-ma20-distance-end,
    owns ontology-ma20-distance-change,
    owns ontology-ma60-distance-start,
    owns ontology-ma60-distance-end,
    owns ontology-volume-ratio-end,
    owns ontology-trade-strength-end,
    owns ontology-bid-ask-imbalance-end,
    owns ontology-smart-money-net-latest,
    owns ontology-smart-money-net-change,
    owns ontology-individual-net-latest,
    owns ontology-event-count,
    owns ontology-risk-event-count,
    owns ontology-support-event-count,
    owns ontology-temporal-risk-score,
    owns ontology-temporal-support-score,
    owns ontology-investment-strategy-profile,
    owns ontology-investment-strategy-profile-label,
    owns ontology-position-role,
    owns ontology-target-position-role,
    owns ontology-position-intent,
    owns ontology-instrument-archetype,
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
    owns ontology-window-key,
    owns ontology-has-sufficient-history,
    owns ontology-price-path-pattern,
    owns ontology-flow-pattern,
    owns ontology-event-cluster-type,
    owns ontology-trend-episode-type,
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
    owns ontology-id @key,
    owns ontology-relation-type,
    owns ontology-box,
    owns ontology-symbol,
    owns ontology-rule-id,
    owns ontology-account-id,
    owns ontology-snapshot-id,
    owns ontology-tbox-class,
    owns ontology-updated-at,
    owns ontology-json,
    owns ontology-weight,
    owns ontology-field,
    owns ontology-polarity,
    owns ontology-transition-type,
    owns ontology-signal-group,
    owns ontology-materiality-passed,
    owns ontology-materiality-score,
    owns ontology-risk-impact,
    owns ontology-support-impact,
    owns ontology-stage-priority;
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
        node_rows = self.node_rows(graph)
        for row in node_rows:
            queries.append(self.node_insert_query(row, updated_at))
        node_ids = {str(row.get("id") or "") for row in node_rows}
        for row in self.rows_for_relations(graph):
            if str(row.get("source") or "") in node_ids and str(row.get("target") or "") in node_ids:
                queries.append(self.relation_insert_query(row, updated_at))
        for row in self.support_relation_rows(graph):
            if str(row.get("source") or "") in node_ids and str(row.get("target") or "") in node_ids:
                queries.append(self.relation_insert_query(row, updated_at))
        return [item for item in queries if item]

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
                "weight": row.get("confidence") or 0.7,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        for row in self.rows_for_beliefs(graph):
            rows.append({
                "source": row.get("subject"),
                "target": row.get("id"),
                "type": "HAS_BELIEF",
                "weight": row.get("confidence") or 0.7,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": row.get("ruleId") or rule_id_from_value(row.get("id")),
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        for row in self.rows_for_opinions(graph):
            rows.append({
                "source": "stock:" + str(row.get("symbol") or "").upper(),
                "target": row.get("id"),
                "type": "HAS_OPINION",
                "weight": row.get("conviction") or 0.0,
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
        return (
            str(variable or "$n") + " isa " + node_type
            + ", has ontology-id " + typedb_string(row.get("id"))
            + typeql_has("ontology-label", row.get("label"))
            + typeql_has("ontology-kind", row.get("kind"))
            + typeql_has("ontology-box", row.get("ontologyBox") or "ABox")
            + typeql_has("ontology-symbol", row.get("symbol"))
            + typeql_has("ontology-rule-id", row.get("ruleId"))
            + typeql_has("ontology-account-id", row.get("accountId"))
            + typeql_has("ontology-snapshot-id", row.get("snapshotId") or row.get("aboxSnapshotId"))
            + typeql_has("ontology-tbox-class", row.get("tboxClass"))
            + typeql_has("ontology-relation-type", row.get("relationTypeName"))
            + typeql_has("ontology-updated-at", updated_at)
            + typeql_has("ontology-json", row.get("propertiesJson"))
            + typeql_has("ontology-confidence", row.get("confidence"), numeric=True)
            + typeql_has("ontology-source-value", row.get("sourceValue"))
            + typeql_has("ontology-field", row.get("field"))
            + typeql_has("ontology-level-type", row.get("levelType"))
            + typeql_has("ontology-data-scope", row.get("dataScope"))
            + typeql_has("ontology-domain-scope", row.get("domainScope"))
            + typeql_has("ontology-relation-scope", row.get("relationScope"))
            + typeql_has("ontology-group", row.get("group"))
            + typeql_has("ontology-polarity", row.get("polarity"))
            + typeql_has("ontology-event-type", row.get("eventType"))
            + typeql_has_bool_string("ontology-materiality-passed", row.get("materialityPassed"))
            + typeql_has("ontology-value-number", row.get("valueNumber"), numeric=True)
            + typeql_has("ontology-profit-loss-rate", row.get("profitLossRate"), numeric=True)
            + typeql_has("ontology-materiality-score", row.get("materialityScore"), numeric=True)
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
        return (
            str(source_variable or "$source") + " isa ontology-node, has ontology-id " + typedb_string(row.get("source")) + "; "
            + str(target_variable or "$target") + " isa ontology-node, has ontology-id " + typedb_string(row.get("target")) + "; "
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
            + typeql_has("ontology-relation-type", row.get("type"))
            + typeql_has("ontology-box", row.get("ontologyBox") or "ABox")
            + typeql_has("ontology-symbol", row.get("symbol"))
            + typeql_has("ontology-rule-id", row.get("ruleId"))
            + typeql_has("ontology-account-id", row.get("accountId"))
            + typeql_has("ontology-snapshot-id", row.get("snapshotId") or row.get("aboxSnapshotId"))
            + typeql_has("ontology-tbox-class", row.get("tboxClass"))
            + typeql_has("ontology-updated-at", updated_at)
            + typeql_has("ontology-json", row.get("propertiesJson"))
            + typeql_has("ontology-weight", row.get("weight"), numeric=True)
            + typeql_has("ontology-field", row.get("field"))
            + typeql_has("ontology-polarity", row.get("polarity"))
            + typeql_has("ontology-transition-type", row.get("transitionType"))
            + typeql_has("ontology-signal-group", row.get("signalGroup"))
            + typeql_has_bool_string("ontology-materiality-passed", row.get("materialityPassed"))
            + typeql_has("ontology-materiality-score", row.get("materialityScore"), numeric=True)
            + typeql_has("ontology-risk-impact", row.get("riskImpact"), numeric=True)
            + typeql_has("ontology-support-impact", row.get("supportImpact"), numeric=True)
            + typeql_has("ontology-stage-priority", row.get("stagePriority"), numeric=True)
        )

    def batched_node_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 40,
    ) -> List[str]:
        items = [row for row in rows or [] if str((row or {}).get("id") or "").strip()]
        queries: List[str] = []
        for offset in range(0, len(items), max(1, int(batch_size or 40))):
            batch = items[offset: offset + max(1, int(batch_size or 40))]
            inserts = [
                self.node_insert_clause(row, updated_at, "$n" + str(index)) + ";"
                for index, row in enumerate(batch)
            ]
            if inserts:
                queries.append("insert " + " ".join(inserts))
        return queries

    def batched_relation_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 25,
    ) -> List[str]:
        items = [
            row for row in rows or []
            if str((row or {}).get("source") or "").strip() and str((row or {}).get("target") or "").strip()
        ]
        queries: List[str] = []
        for offset in range(0, len(items), max(1, int(batch_size or 25))):
            batch = items[offset: offset + max(1, int(batch_size or 25))]
            matches = []
            inserts = []
            for index, row in enumerate(batch):
                source_var = "$source" + str(index)
                target_var = "$target" + str(index)
                relation_var = "$r" + str(index)
                matches.append(self.relation_match_clause(row, source_var, target_var))
                inserts.append(self.relation_insert_clause(row, updated_at, relation_var, source_var, target_var) + ";")
            if matches and inserts:
                queries.append("match " + " ".join(matches) + " insert " + " ".join(inserts))
        return queries

    def inferencebox_insert_queries(
        self,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
        updated_at: str,
    ) -> List[str]:
        settings = runtime_settings()
        node_batch_size = int(number_or_none(settings.get("typedbInferenceBoxNodeBatchSize")) or 40)
        relation_batch_size = int(number_or_none(settings.get("typedbInferenceBoxRelationBatchSize")) or 25)
        return [
            *self.batched_node_insert_queries(node_rows, updated_at, node_batch_size),
            *self.batched_relation_insert_queries(relation_rows, updated_at, relation_batch_size),
        ]

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload or {}
        try:
            rules = rulebox_rules_from_payload(payload) if (payload.get("rules") is not None or payload.get("rulesJson")) else default_graph_inference_rules()
        except ValueError as error:
            return {"configured": True, "saved": False, "seeded": False, "status": "invalid-rulebox", "graphStore": "typedb", "reason": str(error)}
        rules = list(rules)
        rules_payload = rulebox_rules_to_payload(rules)
        self._last_rules = rules
        result = self.save_graph(ontology_seed_graph(rules))
        result.update({
            "configured": True,
            "seeded": bool(result.get("saved")),
            "engineVersion": GRAPH_REASONER_VERSION,
            "ruleCount": len(rules),
            "graphStore": "typedb",
        })
        if typedb_bool(payload.get("replaceRuleBox")) and result.get("saved"):
            expected_rulebox = rulebox_runtime_metadata(rules_payload)
            rulebox_result = self.save_rulebox({"rules": rules_payload})
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
        return result

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
        save_result = self.save_graph(rulebox_graph_from_rules(self._last_rules, include_tbox=False))
        self.clear_rulebox_snapshot_cache()
        snapshot = self.rulebox_snapshot()
        snapshot.update({
            "saved": bool(save_result.get("saved")),
            "status": save_result.get("status") or snapshot.get("status"),
            "reason": save_result.get("reason") or snapshot.get("reason") or "",
            "saveResult": save_result,
        })
        return snapshot

    def match_typedb_native_rules(
        self,
        rules: Iterable[GraphInferenceRule],
        target_symbols: Iterable[str] = None,
    ) -> Dict[str, object]:
        clean_symbols = clean_symbols_from_payload(list(target_symbols or []))
        matches: List[Dict[str, object]] = []
        match_index: Dict[str, Dict[str, object]] = {}
        executed_rules = []
        skipped_rules = []
        read_call_count = 0
        try:
            imported = self.driver_imports()
            if imported[0] is None:
                raise RuntimeError("typedb-driver Python package is not installed: " + str(imported[1])[:160])
            _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]

            def operation():
                nonlocal read_call_count
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    with driver.transaction(self.database, TransactionType.READ) as tx:
                        for rule in rules or []:
                            rule_payload = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {})
                            profile = typedb_native_rule_profile(rule_payload)
                            if profile.get("status") != "ready":
                                skipped_rules.append({
                                    "ruleId": str(rule.rule_id or ""),
                                    "status": profile.get("status"),
                                    "reason": "Rule has JSON-bound or unsupported conditions for TypeDB schema function execution.",
                                })
                                continue
                            function_name = typedb_native_rule_function_name(rule.rule_id)
                            query_plan = typedb_native_function_call_query(rule_payload, clean_symbols)
                            if not query_plan.get("query"):
                                skipped_rules.append({
                                    "ruleId": str(rule.rule_id or ""),
                                    "status": "blocked",
                                    "reason": "TypeDB schema function call could not be built.",
                                })
                                continue
                            rows = self.read_rows_in_transaction(
                                tx,
                                str(query_plan.get("query")),
                                query_plan.get("columns") or ["sourceId"],
                                label="nativeRule:" + str(rule.rule_id or ""),
                            )
                            read_call_count += 1
                            executed_rules.append({
                                "ruleId": rule.rule_id,
                                "nativeRuleId": typedb_native_rule_id(rule.rule_id),
                                "schemaFunctionName": function_name,
                                "rowCount": len(rows),
                            })
                            self.merge_native_match_rows(rule, query_plan, rows, match_index, matches)
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
            return {
                "status": "ok",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "nativeQueryUsed": True,
                "schemaFunctionUsed": True,
                "executedRuleCount": len(executed_rules),
                "skippedRuleCount": len(skipped_rules),
                "matchedCount": len(matches),
                "readTransactionCount": 1 if executed_rules else 0,
                "readQueryCount": read_call_count,
                "conditionDetailQueryCount": 0 if not self.condition_detail_queries_enabled() else None,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
                "matches": matches,
                "executedRules": executed_rules[:40],
                "skippedRules": skipped_rules[:40],
            }
        except Exception as error:  # noqa: BLE001 - run_rulebox reports and can use compatibility fallback.
            return {
                "status": "error",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "nativeQueryUsed": False,
                "schemaFunctionUsed": False,
                "matchedCount": 0,
                "matches": [],
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "executedRules": executed_rules[:40],
                "skippedRules": skipped_rules[:40],
                "readQueryCount": read_call_count,
                "typedbQueryMetrics": self.query_metrics_snapshot(),
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
                "confidence": number_or_none(condition_context.get("confidence")) or typedb_native_match_confidence(rule),
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
                    "weight": condition_payload.get("min_weight") or condition_payload.get("minWeight"),
                })
            matched_conditions.append(payload)
        confidence_conditions = [item for item in matched_conditions if not item.get("absenceSatisfied")]
        confidence = round(min(0.94, 0.62 + len(confidence_conditions) * 0.08), 3)
        return {
            "matchedConditions": matched_conditions,
            "evidenceRelationIds": sorted(set(evidence_relation_ids)),
            "confidence": confidence,
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
        if (
            not force
            and self._schema_function_sync_cache_key == sync_fingerprint
            and str(self._schema_function_sync_cache_result.get("status") or "") == "ok"
        ):
            cached_result = dict(self._schema_function_sync_cache_result)
            cached_result.update({
                "cached": True,
                "schemaFunctionSyncCached": True,
                "syncFingerprint": sync_fingerprint,
            })
            return cached_result
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

        def apply_schema_query(driver, query: str) -> None:
            with typedb_operation_timeout(self.schema_operation_timeout_seconds(), "TypeDB schema function sync"):
                with driver.transaction(self.database, TransactionType.SCHEMA) as tx:
                    tx.query(query).resolve()
                    tx.commit()

        def is_already_existing_schema_function(error: Exception) -> bool:
            error_text = str(error).lower()
            return "already exists" in error_text or "with name" in error_text

        synced: List[Dict[str, object]] = []
        failed: List[Dict[str, object]] = []
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    for definition in definitions:
                        define_query = str(definition.get("define") or "")
                        sync_status = "defined"
                        try:
                            apply_schema_query(driver, define_query)
                        except Exception as define_error:  # noqa: BLE001 - TypeDB reports existing schema functions as exceptions.
                            if not is_already_existing_schema_function(define_error):
                                raise
                            sync_status = "already-exists"
                        synced.append({
                            "ruleId": definition.get("ruleId"),
                            "nativeRuleId": definition.get("nativeRuleId"),
                            "schemaFunctionName": definition.get("functionName"),
                            "rootSchemaFunctionName": definition.get("rootFunctionName") or definition.get("functionName"),
                            "schemaFunctionSyncStatus": sync_status,
                        })
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
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
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
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
        }
        self._schema_function_sync_cache_key = sync_fingerprint
        self._schema_function_sync_cache_result = dict(result)
        return result

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().run_rulebox(payload)
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
        snapshot = self.rulebox_snapshot()
        rules = snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []
        rulebox_metadata = rulebox_runtime_metadata(rules)
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
            function_sync_result = self.sync_typedb_native_rule_functions(parsed_rules, force=force_schema_function_sync)
            runtime_rulebox_metadata = dict(rulebox_metadata)
            runtime_rulebox_metadata.update({
                "typedbSchemaFunctionSyncStatus": str(function_sync_result.get("status") or ""),
                "typedbSchemaFunctionSyncCached": bool(function_sync_result.get("schemaFunctionSyncCached")),
                "typedbSchemaFunctionSyncedCount": int(number_or_none(function_sync_result.get("syncedCount")) or 0),
                "typedbSchemaFunctionSkippedCount": int(number_or_none(function_sync_result.get("skippedCount")) or 0),
                "typedbSchemaFunctionFailedCount": int(number_or_none(function_sync_result.get("failedCount")) or 0),
                "typedbSchemaFunctionUsed": str(function_sync_result.get("status") or "") == "ok",
                "typeDbFunctionReasoningUsed": str(function_sync_result.get("status") or "") == "ok",
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
            native_match_result = self.match_typedb_native_rules(parsed_rules, target_symbols=target_symbols)
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
            graph = self.load_graph_for_native_matches(native_match_result)
            before_entities = len(graph.entities)
            before_relations = len(graph.relations)
            materialize_typedb_native_matches(graph, parsed_rules, native_match_result)
            inference_graph = typedb_inferencebox_graph(
                graph,
                generation_id=generation_id,
                generation_at=generation_at,
                rulebox_metadata=runtime_rulebox_metadata,
            )
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
        inferencebox_payload = self.inferencebox_snapshot_from_graph(inference_graph, target_symbols, 80)
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
                for key in ["status", "reason", "reasonCode", "syncedCount", "skippedCount", "failedCount", "syncedRules", "skippedRules"]
                if key in function_sync_result
            },
            "nativeMatchResult": {
                key: native_match_result.get(key)
                for key in ["status", "reason", "reasonCode", "nativeQueryUsed", "schemaFunctionUsed", "executedRuleCount", "skippedRuleCount", "matchedCount", "executedRules", "skippedRules", "typedbQueryMetrics"]
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
                    for key in ["status", "reason", "reasonCode", "syncedCount", "skippedCount", "failedCount", "syncedRules", "skippedRules"]
                    if key in function_sync_result
                },
                "nativeMatchResult": {
                    key: native_match_result.get(key)
                    for key in ["status", "reason", "reasonCode", "nativeQueryUsed", "schemaFunctionUsed", "executedRuleCount", "skippedRuleCount", "matchedCount", "executedRules", "skippedRules", "typedbQueryMetrics"]
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
        statement_count = len(node_rows) + len([row for row in relation_rows if row.get("source") and row.get("target")])
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    if queries:
                        with driver.transaction(self.database, TransactionType.WRITE) as tx:
                            for query in queries:
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
                "entityCount": len(node_rows),
                "relationCount": len(relation_rows),
                "statementCount": statement_count,
                "batchCount": len(queries),
                "insertMode": "batched",
                "inferenceGenerationId": str((graph.worldview or {}).get("inferenceGenerationId") or ""),
                "inferenceGenerationAt": str((graph.worldview or {}).get("inferenceGenerationAt") or ""),
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
                "inferenceGenerationId": str((graph.worldview or {}).get("inferenceGenerationId") or ""),
            }

    def prune_inferencebox_generations(self, active_generation_id: str, keep_count: int = 2) -> Dict[str, object]:
        active_generation_id = str(active_generation_id or "").strip()
        if not active_generation_id:
            return {"configured": bool(self.address), "status": "skipped", "reason": "active generation id is empty"}
        try:
            records = self.read_inference_generation_records()
        except Exception as error:  # noqa: BLE001 - pruning must not fail materialization.
            return {"configured": True, "status": "error", "reason": str(error)[:180], "activeGenerationId": active_generation_id}
        if not records:
            return {"configured": True, "status": "skipped", "reason": "no generation-scoped InferenceBox rows", "activeGenerationId": active_generation_id}
        keep = {active_generation_id}
        for item in sorted(records, key=lambda row: str(row.get("latestAt") or ""), reverse=True)[: max(1, int(keep_count or 2))]:
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
        return snapshot

    def inferencebox_snapshot(self, symbols: List[str] = None, limit: int = 80) -> Dict[str, object]:
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        safe_limit = max(1, min(500, int(limit or 80)))
        if not self.address:
            return NullTypeDBOntologyGraphRepository().inferencebox_snapshot(clean_symbols, safe_limit)
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

    def load_graph_for_native_matches(self, native_match_result: Dict[str, object]) -> PortfolioOntology:
        matches = [item for item in (native_match_result or {}).get("matches") or [] if isinstance(item, dict)]
        source_ids = sorted(set(str(item.get("sourceId") or "").strip() for item in matches if str(item.get("sourceId") or "").strip()))
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
            "confidence": number_or_none(match.get("confidence")) or typedb_native_match_confidence(rule),
        })


def typedb_native_match_confidence(rule: GraphInferenceRule) -> float:
    return round(min(0.94, 0.62 + len(list(getattr(rule, "conditions", []) or [])) * 0.08), 3)


def typedb_native_matched_conditions(
    rule: GraphInferenceRule,
    row: Dict[str, object],
    query_plan: Dict[str, object],
) -> List[Dict[str, object]]:
    result = []
    evidence_by_condition = dict(query_plan.get("conditionEvidenceColumns") or {})
    for condition in getattr(rule, "conditions", []) or []:
        payload = {
            "conditionId": condition.condition_id,
            "kind": condition.kind,
            "role": condition.role or "required",
        }
        evidence_column = evidence_by_condition.get(condition.condition_id)
        if evidence_column:
            payload["relationId"] = str(row.get(evidence_column) or "")
        if condition.kind == "subject_property":
            payload.update({"field": condition.field, "operator": condition.operator, "value": condition.value})
        elif condition.kind == "relation":
            payload.update({"relationType": condition.relation_type, "weight": condition.min_weight})
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
                    "weight": getattr(condition, "min_weight", 0),
                })
            matched_conditions.append(payload)
    evidence_relation_ids = [
        str(row.get(column) or "")
        for column in (query_plan.get("evidenceColumns") or [])
        if str(row.get(column) or "").strip()
    ]
    confidence_conditions = [item for item in matched_conditions if not item.get("absenceSatisfied")]
    confidence = round(min(0.94, 0.62 + len(confidence_conditions) * 0.08), 3)
    return {
        "matchedConditions": matched_conditions,
        "evidenceRelationIds": sorted(set(evidence_relation_ids)),
        "confidence": confidence,
        "conditionDetailSource": "schema-function-match",
    }


def normalized_condition_role(condition: Dict[str, object]) -> str:
    role = str(condition.get("role") or condition.get("conditionRole") or "required").strip().lower()
    return role if role in {"required", "any", "optional", "not"} else "required"


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
) -> Dict[str, object]:
    condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "condition-" + str(index))
    kind = str(condition.get("kind") or "")
    clauses: List[str] = []
    columns: List[str] = []
    evidence_columns: List[str] = []
    if kind == "subject_property":
        attr = typedb_subject_attribute(str(condition.get("field") or ""))
        if not attr:
            return {"conditionId": condition_id, "clauses": [], "columns": [], "reason": "unsupported subject field"}
        clause = typedb_value_match(
            source_var,
            attr,
            condition.get("value"),
            str(condition.get("operator") or "=="),
            "subjectValue" + str(index),
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
        relation_id_var = "relationId" + str(index)
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
        min_weight = number_or_none(condition.get("min_weight") or condition.get("minWeight"))
        if min_weight:
            weight_var = "relationWeight" + str(index)
            clauses.append(relation_var + " has ontology-weight $" + weight_var + "; $" + weight_var + " >= " + str(float(min_weight)) + ";")
        target_kind = str(condition.get("target_kind") or condition.get("targetKind") or "")
        if target_kind:
            clauses.append(target_var + " has ontology-kind " + typedb_string(target_kind) + ";")
        for filter_key, expected in dict(condition.get("target_property_filters") or condition.get("targetPropertyFilters") or {}).items():
            attr = typedb_target_attribute(str(filter_key))
            if attr:
                op = typedb_filter_operator(str(filter_key), expected)
                clause = typedb_value_match(target_var, attr, expected, op, "targetValue" + str(index) + str(len(clauses)))
                if clause:
                    clauses.append(clause)
        for filter_key, expected in dict(condition.get("relation_property_filters") or condition.get("relationPropertyFilters") or {}).items():
            attr = typedb_relation_attribute(str(filter_key))
            if attr:
                op = typedb_filter_operator(str(filter_key), expected)
                clause = typedb_value_match(relation_var, attr, expected, op, "relationValue" + str(index) + str(len(clauses)))
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
) -> Dict[str, object]:
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock")
    conditions = [item for item in (rule.get("conditions") or []) if isinstance(item, dict)]
    clauses = [
        "$source isa ontology-node, has ontology-id $sourceId, has ontology-label $sourceLabel, has ontology-kind "
        + typedb_string(source_kind)
        + ", has ontology-box \"ABox\";"
    ]
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
        pattern = typedb_condition_pattern(condition, index)
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
    if any_conditions and any_helper_names:
        branches = ["{ let $source in " + name + "(); }" for name in any_helper_names]
        clauses.append(" or ".join(branches) + ";")
    elif any_conditions:
        if any_min_count > len(any_conditions):
            return {"ruleId": rule_id, "query": "", "columns": columns, "reason": "any condition minimum exceeds available any conditions"}
        branches: List[str] = []
        for combo in itertools.combinations(any_conditions, any_min_count):
            branch_clauses: List[str] = []
            for slot_index, (_condition_index, condition) in enumerate(combo):
                pattern = typedb_condition_pattern(
                    condition,
                    slot_index,
                    relation_prefix="anyRel" + str(slot_index) + "_",
                    target_prefix="anyTarget" + str(slot_index) + "_",
                )
                if pattern.get("reason"):
                    return {"ruleId": rule_id, "query": "", "columns": columns, "reason": str(pattern.get("reason") or "")}
                branch_clauses.extend(str(item) for item in pattern.get("clauses") or [] if str(item or "").strip())
            if branch_clauses:
                branches.append("{ " + " ".join(branch_clauses) + " }")
        if branches:
            clauses.append(" or ".join(branches) + ";")
    query = "match " + " ".join(clauses)
    return {
        "ruleId": rule_id,
        "nativeRuleId": typedb_native_rule_id(rule_id),
        "query": query,
        "columns": columns,
        "evidenceColumns": evidence_columns,
        "conditionEvidenceColumns": condition_evidence_columns,
    }


def typedb_native_rule_function_name(rule_id: object) -> str:
    raw = str(rule_id or "rule").strip().lower()
    normalized = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    if not normalized:
        normalized = "rule"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return (TYPEDB_SCHEMA_FUNCTION_PREFIX + normalized + "_" + digest)[:120]


def typedb_native_any_helper_function_name(rule_id: object, index: int) -> str:
    base = typedb_native_rule_function_name(rule_id)
    suffix = "_any_" + str(index)
    return (base[: max(1, 120 - len(suffix))] + suffix).strip("_")


def typedb_native_any_helper_definitions(rule: Dict[str, object]) -> List[Dict[str, object]]:
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock")
    conditions = [item for item in (rule.get("conditions") or []) if isinstance(item, dict)]
    any_conditions = [
        (index, condition)
        for index, condition in enumerate(conditions)
        if normalized_condition_role(condition) in {"any", "optional"}
    ]
    if not any_conditions:
        return []
    any_min_count = max(1, int(number_or_none(rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")) or 1))
    if any_min_count > len(any_conditions):
        return []
    definitions: List[Dict[str, object]] = []
    for combo_index, combo in enumerate(itertools.combinations(any_conditions, any_min_count)):
        clauses = [
            "$source isa ontology-node, has ontology-kind "
            + typedb_string(source_kind)
            + ", has ontology-box \"ABox\";"
        ]
        for slot_index, (_condition_index, condition) in enumerate(combo):
            pattern = typedb_condition_pattern(
                condition,
                slot_index,
                relation_prefix="helperRel" + str(slot_index) + "_",
                target_prefix="helperTarget" + str(slot_index) + "_",
            )
            if pattern.get("reason"):
                return []
            clauses.extend(str(item) for item in pattern.get("clauses") or [] if str(item or "").strip())
        function_name = typedb_native_any_helper_function_name(rule_id, combo_index)
        body = (
            "fun " + function_name + "() -> { ontology-node }:\n"
            + "match " + " ".join(clauses) + "\n"
            + "return { $source };"
        )
        definitions.append({
            "ruleId": rule_id,
            "functionName": function_name,
            "body": body,
            "define": "define\n" + body,
            "redefine": "redefine\n" + body,
            "comboConditionIds": [
                str(condition.get("condition_id") or condition.get("conditionId") or "")
                for _condition_index, condition in combo
            ],
        })
    return definitions


def typedb_native_function_definition(rule: Dict[str, object]) -> Dict[str, object]:
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    function_name = typedb_native_rule_function_name(rule_id)
    helper_definitions = typedb_native_any_helper_definitions(rule)
    plan = typedb_native_match_query(rule, [], [str(item.get("functionName") or "") for item in helper_definitions])
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
        "fun " + function_name + "() -> { ontology-node }:\n"
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


def typedb_native_function_call_query(rule: Dict[str, object], target_symbols: Iterable[str] = None) -> Dict[str, object]:
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    function_name = typedb_native_rule_function_name(rule_id)
    symbols = clean_symbols_from_payload(list(target_symbols or []))
    clauses = [
        "let $source in " + function_name + "();",
        "$source has ontology-id $sourceId;",
        "$source has ontology-label $sourceLabel;",
    ]
    if symbols:
        clauses.append(typedb_value_match("$source", "ontology-symbol", symbols, "==", "sourceSymbol"))
    return {
        "ruleId": rule_id,
        "nativeRuleId": typedb_native_rule_id(rule_id),
        "functionName": function_name,
        "query": "match " + " ".join(item for item in clauses if item),
        "columns": ["sourceId", "sourceLabel"],
        "evidenceColumns": [],
        "conditionEvidenceColumns": {},
    }


def typedb_native_condition_check_query(condition: Dict[str, object], source_id: str, index: int) -> Dict[str, object]:
    pattern = typedb_condition_pattern(condition, index, relation_prefix="checkRel", target_prefix="checkTarget")
    if pattern.get("reason"):
        return {
            "conditionId": str(condition.get("condition_id") or condition.get("conditionId") or ""),
            "query": "",
            "columns": [],
            "reason": str(pattern.get("reason") or ""),
        }
    clauses = [
        "$source isa ontology-node, has ontology-id " + typedb_string(source_id) + ";",
        *[str(item) for item in pattern.get("clauses") or [] if str(item or "").strip()],
    ]
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
    if field in {"minMaterialityScore", "minValue", "maxValue"}:
        field = "materialityScore" if field == "minMaterialityScore" else "value"
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
        "materialityScore": "ontology-materiality-score",
        "value": "ontology-value-number",
        "tboxClass": "ontology-tbox-class",
        "tboxClasses": "ontology-tbox-class",
        "allowAddOnStrength": "ontology-allow-add-on-strength",
        "trimOnTrendBreak": "ontology-trim-on-trend-break",
        "avoidAveragingDown": "ontology-avoid-averaging-down",
        "confidence": "ontology-confidence",
        "impactPolarity": "ontology-impact-polarity",
        "needsReview": "ontology-needs-review",
        "readScope": "ontology-read-scope",
        "peRatio": "ontology-pe-ratio",
        "beta": "ontology-beta",
    }.get(field, "")


def typedb_relation_attribute(field: str) -> str:
    if field in {"minRiskImpact", "minSupportImpact", "minMaterialityScore"}:
        field = field.replace("min", "", 1)
        field = field[:1].lower() + field[1:]
    return {
        "field": "ontology-field",
        "signalGroup": "ontology-signal-group",
        "polarity": "ontology-polarity",
        "transitionType": "ontology-transition-type",
        "materialityPassed": "ontology-materiality-passed",
        "materialityScore": "ontology-materiality-score",
        "riskImpact": "ontology-risk-impact",
        "supportImpact": "ontology-support-impact",
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
            item.confidence,
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
    return inference_graph


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
    )
