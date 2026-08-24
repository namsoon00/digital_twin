"""Governed model-signal interpretation contracts.

Predictive market conditions are evaluated by versioned statistical models.
The resulting exact ``HAS_MODEL_SIGNAL`` evidence still needs an ontology
interpretation before it may affect a hypothesis or action envelope.  This
module separates that durable meaning from the repeated per-rule TypeDB
function shape.

The original RuleBox identifier remains the immutable lineage key.  Runtime
execution may share one source-scope bridge function, while the policy keeps
the exact signal contract, remaining account conditions and derivations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Dict, Iterable, List, Mapping, Tuple

from .ontology_rulebox_contracts import GraphInferenceRule


MODEL_SIGNAL_INTERPRETATION_POLICY_VERSION = "model-signal-interpretation-policy-v1"
MODEL_SIGNAL_BRIDGE_VERSION = "typedb-model-signal-bridge-v1"
MODEL_SIGNAL_PRODUCTION_DISPOSITION = "model-signal-production"
MODEL_SIGNAL_RELATION_TYPE = "HAS_MODEL_SIGNAL"
MODEL_SIGNAL_EVIDENCE_KIND = "statistical-model-hypothesis-evidence"

MODEL_SIGNAL_BRIDGE_SOURCE_SCOPES = ("stock", "holding", "watchlist")


def _condition_payload(condition: object) -> Dict[str, object]:
    if isinstance(condition, Mapping):
        return dict(condition)
    if hasattr(condition, "to_dict"):
        return dict(condition.to_dict())
    return {}


def _rule_conditions(rule: object) -> List[object]:
    if isinstance(rule, Mapping):
        return [item for item in rule.get("conditions") or [] if isinstance(item, Mapping)]
    return list(getattr(rule, "conditions", []) or [])


def _rule_id(rule: object) -> str:
    if isinstance(rule, Mapping):
        return str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
    return str(getattr(rule, "rule_id", "") or "").strip()


def _migration_disposition(rule: object) -> str:
    if isinstance(rule, Mapping):
        basis = dict(rule.get("knowledge_basis") or rule.get("knowledgeBasis") or {})
        return str(
            basis.get("migration_disposition")
            or basis.get("migrationDisposition")
            or ""
        ).strip()
    basis = getattr(rule, "resolved_knowledge_basis", None)
    return str(getattr(basis, "migration_disposition", "") or "").strip()


def model_signal_conditions(rule: object) -> Tuple[object, ...]:
    return tuple(
        condition
        for condition in _rule_conditions(rule)
        if str(
            _condition_payload(condition).get("relation_type")
            or _condition_payload(condition).get("relationType")
            or ""
        ).upper().strip()
        == MODEL_SIGNAL_RELATION_TYPE
    )


def is_model_signal_interpretation_rule(rule: object) -> bool:
    """Return whether a production rule is an exact model-signal adapter."""

    if _migration_disposition(rule) != MODEL_SIGNAL_PRODUCTION_DISPOSITION:
        return False
    signal_rows = model_signal_conditions(rule)
    if len(signal_rows) != 1:
        return False
    payload = _condition_payload(signal_rows[0])
    filters = dict(
        payload.get("target_property_filters")
        or payload.get("targetPropertyFilters")
        or {}
    )
    return bool(
        str(filters.get("hypothesisContractId") or "").strip() == _rule_id(rule)
        and str(payload.get("target_kind") or payload.get("targetKind") or "").strip()
        == MODEL_SIGNAL_EVIDENCE_KIND
    )


def model_signal_source_condition(rule: object) -> object:
    """Return the source-role condition shared by a bridge, when present."""

    matches = []
    for condition in _rule_conditions(rule):
        payload = _condition_payload(condition)
        if (
            str(payload.get("kind") or "") == "subject_property"
            and str(payload.get("field") or "") == "source"
            and str(payload.get("operator") or "==") == "=="
            and str(payload.get("value") or "").strip().lower() in {"holding", "watchlist"}
            and str(payload.get("role") or "required").strip().lower() == "required"
        ):
            matches.append(condition)
    return matches[0] if len(matches) == 1 else None


def model_signal_bridge_source_scope(rule: object) -> str:
    condition = model_signal_source_condition(rule)
    if condition is None:
        return "stock"
    return str(_condition_payload(condition).get("value") or "stock").strip().lower()


def model_signal_bridge_conditions(rule: object) -> Tuple[object, ...]:
    condition = model_signal_source_condition(rule)
    return (condition,) if condition is not None else ()


def model_signal_residual_conditions(rule: object) -> Tuple[object, ...]:
    """Return exact signal and account-policy predicates outside the bridge."""

    bridge_condition_ids = {
        str(
            _condition_payload(item).get("condition_id")
            or _condition_payload(item).get("conditionId")
            or ""
        ).strip()
        for item in model_signal_bridge_conditions(rule)
    }
    return tuple(
        condition
        for condition in _rule_conditions(rule)
        if str(
            _condition_payload(condition).get("condition_id")
            or _condition_payload(condition).get("conditionId")
            or ""
        ).strip()
        not in bridge_condition_ids
    )


def is_batchable_model_signal_interpretation_rule(rule: object) -> bool:
    """Return whether one exact signal policy can use a shared bridge read.

    A batchable policy has no account-specific predicate outside the shared
    source scope. TypeDB therefore needs to prove only that the active source
    is linked to one immutable ``HAS_MODEL_SIGNAL`` evidence entity. Runtime
    dispatch maps the evidence's exact hypothesis contract back to the
    governed RuleBox lineage; it does not evaluate a threshold in Python.
    """

    if not is_model_signal_interpretation_rule(rule):
        return False
    residual = model_signal_residual_conditions(rule)
    if len(residual) != 1:
        return False
    payload = _condition_payload(residual[0])
    target_filters = dict(
        payload.get("target_property_filters")
        or payload.get("targetPropertyFilters")
        or {}
    )
    relation_filters = dict(
        payload.get("relation_property_filters")
        or payload.get("relationPropertyFilters")
        or {}
    )
    deterministic_filters = all(
        not isinstance(expected, Mapping)
        or str(expected.get("operator") or "==").strip().lower() in {"==", "eq", "in"}
        for expected in target_filters.values()
    )
    return bool(
        str(payload.get("kind") or "") == "relation"
        and str(
            payload.get("relation_type")
            or payload.get("relationType")
            or ""
        ).upper().strip()
        == MODEL_SIGNAL_RELATION_TYPE
        and str(payload.get("role") or "required").strip().lower() == "required"
        and str(target_filters.get("hypothesisContractId") or "").strip()
        and deterministic_filters
        and not relation_filters
    )


def model_signal_interpretation_contract_id(rule: object) -> str:
    """Return the stable hypothesis contract used to dispatch a bridge row."""

    if not is_model_signal_interpretation_rule(rule):
        return ""
    signal = _condition_payload(model_signal_conditions(rule)[0])
    filters = dict(
        signal.get("target_property_filters")
        or signal.get("targetPropertyFilters")
        or {}
    )
    return str(filters.get("hypothesisContractId") or "").strip()


def model_signal_interpretation_execution_partition(
    rules: Iterable[GraphInferenceRule],
    *,
    enabled_only: bool = True,
) -> Dict[str, object]:
    """Describe the shared-read and constrained policy execution surfaces."""

    model_rules = [
        rule
        for rule in rules or []
        if is_model_signal_interpretation_rule(rule)
        and (not enabled_only or bool(getattr(rule, "enabled", True)))
    ]
    batchable = [
        rule for rule in model_rules
        if is_batchable_model_signal_interpretation_rule(rule)
    ]
    constrained = [
        rule for rule in model_rules
        if not is_batchable_model_signal_interpretation_rule(rule)
    ]
    scopes = tuple(sorted({
        model_signal_bridge_source_scope(rule)
        for rule in batchable
    }))
    return {
        "logicalModelSignalPolicyCount": len(model_rules),
        "batchedSimplePolicyCount": len(batchable),
        "constrainedPolicyCount": len(constrained),
        "modelSignalBridgeReadCount": len(scopes),
        "eliminatedModelSignalPolicyQueryCount": max(0, len(batchable) - len(scopes)),
        "bridgeSourceScopes": list(scopes),
        "batchableRuleIds": sorted(_rule_id(rule) for rule in batchable),
        "constrainedRuleIds": sorted(_rule_id(rule) for rule in constrained),
    }


def _semantic_family(rule: GraphInferenceRule) -> str:
    derivations = list(rule.derivations or [])
    relation_types = {str(item.relation_type or "").upper() for item in derivations}
    roles = {
        str(item.evidence_role or item.polarity or "context").strip().lower()
        for item in derivations
    }
    effects = {str(item.decision_effect or "").strip().lower() for item in derivations}
    if "blocking" in roles or "block" in effects or any("RISK" in item for item in relation_types):
        return "risk"
    if "counter" in roles or "WEAKENS_THESIS" in relation_types:
        return "counter-evidence"
    if relation_types & {
        "HAS_INFERRED_ENTRY_OPPORTUNITY",
        "HAS_VALUATION_OPPORTUNITY",
        "ALLOWS_ACTION",
        "HAS_ACTION_CANDIDATE",
    }:
        return "opportunity"
    if "support" in roles or "support" in effects:
        return "confirmation"
    return "context"


@dataclass(frozen=True)
class ModelSignalInterpretationPolicy:
    policy_id: str
    legacy_rule_id: str
    label: str
    rule_version: str
    policy_version: str
    enabled: bool
    source_scope: str
    bridge_context_key: str
    semantic_family: str
    hypothesis_contract_id: str
    signal_type: str
    hypothesis_family_id: str
    release_id: str
    strength_band: str
    validation_status: str
    decision_eligibility: str
    eligibility_status: str
    residual_conditions: Tuple[Dict[str, object], ...]
    derivations: Tuple[Dict[str, object], ...]
    prompt_hint: str
    hypothesis_family_key: str
    model_input_contract: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "policyId": payload["policy_id"],
            "legacyRuleId": payload["legacy_rule_id"],
            "lineageRuleId": payload["legacy_rule_id"],
            "label": payload["label"],
            "ruleVersion": payload["rule_version"],
            "policyVersion": payload["policy_version"],
            "enabled": payload["enabled"],
            "sourceScope": payload["source_scope"],
            "bridgeContextKey": payload["bridge_context_key"],
            "semanticFamily": payload["semantic_family"],
            "hypothesisContractId": payload["hypothesis_contract_id"],
            "signalType": payload["signal_type"],
            "hypothesisFamilyId": payload["hypothesis_family_id"],
            "releaseId": payload["release_id"],
            "strengthBand": payload["strength_band"],
            "validationStatus": payload["validation_status"],
            "decisionEligibility": payload["decision_eligibility"],
            "eligibilityStatus": payload["eligibility_status"],
            "residualConditions": list(payload["residual_conditions"]),
            "derivations": list(payload["derivations"]),
            "promptHint": payload["prompt_hint"],
            "hypothesisFamilyKey": payload["hypothesis_family_key"],
            "modelInputContract": dict(payload["model_input_contract"]),
            "executionMode": "typedb-shared-model-signal-bridge",
        }


@dataclass(frozen=True)
class ModelSignalBridgeGroup:
    source_scope: str
    bridge_context_key: str
    version: str
    policy_ids: Tuple[str, ...]
    legacy_rule_ids: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "sourceScope": self.source_scope,
            "bridgeContextKey": self.bridge_context_key,
            "version": self.version,
            "policyIds": list(self.policy_ids),
            "legacyRuleIds": list(self.legacy_rule_ids),
            "policyCount": len(self.policy_ids),
        }


def model_signal_interpretation_policy(
    rule: GraphInferenceRule,
) -> ModelSignalInterpretationPolicy:
    if not is_model_signal_interpretation_rule(rule):
        raise ValueError("Rule is not an exact production model-signal interpretation: " + rule.rule_id)
    signal = _condition_payload(model_signal_conditions(rule)[0])
    filters = dict(
        signal.get("target_property_filters")
        or signal.get("targetPropertyFilters")
        or {}
    )
    source_scope = model_signal_bridge_source_scope(rule)
    return ModelSignalInterpretationPolicy(
        policy_id="model-signal-interpretation:" + rule.rule_id,
        legacy_rule_id=rule.rule_id,
        label=rule.label,
        rule_version=rule.version,
        policy_version=MODEL_SIGNAL_INTERPRETATION_POLICY_VERSION,
        enabled=bool(rule.enabled),
        source_scope=source_scope,
        bridge_context_key="model-signal-source:" + source_scope,
        semantic_family=_semantic_family(rule),
        hypothesis_contract_id=str(filters.get("hypothesisContractId") or ""),
        signal_type=str(filters.get("signalType") or ""),
        hypothesis_family_id=str(filters.get("hypothesisFamilyId") or ""),
        release_id=str(filters.get("releaseId") or ""),
        strength_band=str(filters.get("strengthBand") or ""),
        validation_status=str(filters.get("validationStatus") or ""),
        decision_eligibility=str(filters.get("decisionEligibility") or ""),
        eligibility_status=str(filters.get("eligibilityStatus") or ""),
        residual_conditions=tuple(
            _condition_payload(item) for item in model_signal_residual_conditions(rule)
        ),
        derivations=tuple(item.to_dict() for item in rule.derivations or []),
        prompt_hint=str(rule.prompt_hint or ""),
        hypothesis_family_key=str(rule.hypothesis_family_key or ""),
        model_input_contract=dict(rule.model_input_contract or {}),
    )


def model_signal_interpretation_policies(
    rules: Iterable[GraphInferenceRule],
    *,
    enabled_only: bool = False,
) -> Tuple[ModelSignalInterpretationPolicy, ...]:
    policies = [
        model_signal_interpretation_policy(rule)
        for rule in rules or []
        if is_model_signal_interpretation_rule(rule)
        and (not enabled_only or bool(rule.enabled))
    ]
    return tuple(sorted(policies, key=lambda item: item.legacy_rule_id))


def model_signal_bridge_groups(
    rules: Iterable[GraphInferenceRule],
    *,
    enabled_only: bool = False,
) -> Tuple[ModelSignalBridgeGroup, ...]:
    by_scope: Dict[str, List[ModelSignalInterpretationPolicy]] = {}
    for policy in model_signal_interpretation_policies(rules, enabled_only=enabled_only):
        by_scope.setdefault(policy.source_scope, []).append(policy)
    groups = []
    for scope in MODEL_SIGNAL_BRIDGE_SOURCE_SCOPES:
        policies = by_scope.get(scope) or []
        if not policies:
            continue
        groups.append(ModelSignalBridgeGroup(
            source_scope=scope,
            bridge_context_key="model-signal-source:" + scope,
            version=MODEL_SIGNAL_BRIDGE_VERSION,
            policy_ids=tuple(item.policy_id for item in policies),
            legacy_rule_ids=tuple(item.legacy_rule_id for item in policies),
        ))
    return tuple(groups)


def model_signal_bridge_rule_payload(rule: object) -> Dict[str, object]:
    """Return the compact source-scope predicate compiled once by TypeDB."""

    source_scope = model_signal_bridge_source_scope(rule)
    conditions = [_condition_payload(item) for item in model_signal_bridge_conditions(rule)]
    return {
        "rule_id": "bridge.model-signal." + source_scope + ".v1",
        "label": "모델 신호 범용 브리지 · " + source_scope,
        "version": MODEL_SIGNAL_BRIDGE_VERSION,
        "source_kind": "stock",
        "conditions": conditions,
        "derivations": [],
        "any_condition_min_count": 1,
        "enabled": True,
    }


def model_signal_bridge_manifest(rules: Iterable[GraphInferenceRule]) -> Dict[str, object]:
    rules = list(rules or [])
    policies = model_signal_interpretation_policies(rules)
    active = tuple(item for item in policies if item.enabled)
    groups = model_signal_bridge_groups(rules, enabled_only=True)
    violations = []
    expected = [
        rule
        for rule in rules
        if _migration_disposition(rule) == MODEL_SIGNAL_PRODUCTION_DISPOSITION
    ]
    interpreted_ids = {item.legacy_rule_id for item in policies}
    for rule in expected:
        rule_id = _rule_id(rule)
        if rule_id not in interpreted_ids:
            violations.append("unmapped-model-signal-rule:" + rule_id)
    duplicate_contracts = sorted({
        policy.hypothesis_contract_id
        for policy in policies
        if sum(
            1
            for candidate in policies
            if candidate.hypothesis_contract_id == policy.hypothesis_contract_id
        )
        > 1
    })
    violations.extend("duplicate-hypothesis-contract:" + item for item in duplicate_contracts)
    material = {
        "version": MODEL_SIGNAL_BRIDGE_VERSION,
        "policyVersion": MODEL_SIGNAL_INTERPRETATION_POLICY_VERSION,
        "policyIds": [item.policy_id for item in policies],
        "groups": [item.to_dict() for item in groups],
    }
    fingerprint = hashlib.sha256(json.dumps(
        material,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    execution_partition = model_signal_interpretation_execution_partition(
        rules,
        enabled_only=True,
    )
    return {
        **material,
        "status": "ok" if not violations else "invalid",
        "fingerprint": fingerprint,
        "policyCount": len(policies),
        "activePolicyCount": len(active),
        "disabledPolicyCount": len(policies) - len(active),
        "bridgeFunctionCount": len(groups),
        "legacyExecutableFunctionCount": len(active),
        "sharedExecutableFunctionCount": len(groups),
        "eliminatedPerRuleFunctionCount": max(0, len(active) - len(groups)),
        **execution_partition,
        "violations": violations,
    }


def model_signal_bridge_definition_key(rule: object) -> str:
    scope = model_signal_bridge_source_scope(rule)
    seed = MODEL_SIGNAL_BRIDGE_VERSION + "|" + scope
    return scope + "-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
