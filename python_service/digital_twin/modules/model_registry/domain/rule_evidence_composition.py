"""Native RuleBox composition of verified model evidence and account policy.

This is release authoring, not a Python runtime voting or action selector.
The resulting predicates execute in TypeDB alongside the individual rules.
"""

from copy import deepcopy

from digital_twin.modules.model_registry.domain.ontology_rulebox_contracts import (
    GraphInferenceRule, GraphRuleCondition, GraphRuleDerivation,
)


VERSION = "rule-evidence-composition-v1"
PREFIX = "graph.composition."
CODES = {
    "POSITION_LIMIT_WITH_SUPPORT", "LOSS_POLICY_WITH_SUPPORT",
    "OPPOSING_MARKET_EVIDENCE",
}


def composition_response_violations(rule, derivation):
    response = derivation.composition_response
    errors = []
    if (response.get("version") != VERSION or response.get("authority") != "typedb-rulebox"
            or response.get("code") not in CODES):
        errors.append("invalid composition response")
    if rule.resolved_claim_contract.claim_type != "causal-context":
        errors.append("composition requires a non-predictive causal-context claim")
    if (derivation.candidate_action or derivation.primary_action or derivation.allowed_actions
            or derivation.blocked_actions or derivation.management_response):
        errors.append("composition cannot grant or override actions or policies")
    required = {c.condition_id for c in rule.conditions if c.role == "required"}
    bound = response.get("conditionIds") or []
    if len(bound) < 2 or set(bound) != required:
        errors.append("composition must bind every required native predicate")
    for key in ("label", "meaning", "nextCheck", "boundary"):
        if not isinstance(response.get(key), str) or not response[key].strip():
            errors.append("composition requires " + key)
    return errors


def composition_rules(production_rules):
    """Freeze the permitted contract IDs from this exact source release."""
    source = {r.rule_id: r for r in production_rules if r.enabled and not r.rule_id.startswith(PREFIX)}
    bindings = [(r.rule_id, c.target_property_filters) for r in source.values()
                if r.resolved_claim_contract.is_predictive for c in r.conditions
                if c.relation_type == "HAS_MODEL_SIGNAL"]

    def market(key, polarity, family=""):
        selected = [(rid, f) for rid, f in bindings
                    if str(f.get("signalType") or "").endswith("-" + polarity)
                    and (not family or str(f.get("signalType") or "").startswith(family + "-"))]
        if not selected:
            return None
        return GraphRuleCondition(
            key, "relation", ("가격" if family == "price" else "수급" if family == "flow" else "시장")
            + (" 회복·상승" if polarity == "support" else "약화·하락") + " 조건이 확인됐습니다.",
            relation_type="HAS_MODEL_SIGNAL", target_kind="statistical-model-hypothesis-evidence",
            target_property_filters={
                "hypothesisContractId": sorted({rid for rid, _ in selected}),
                "signalType": sorted({f["signalType"] for _, f in selected}),
                "releaseId": sorted({f["releaseId"] for _, f in selected}),
                "strengthBand": "strong", "contractMatched": True,
                "freshnessCompatible": True, "eligibilityStatus": ["eligible", "conditional"],
            },
        )

    support, risk = market("market-support", "support"), market("market-risk", "risk")
    definitions = [
        ("opposing_market", "OPPOSING_MARKET_EVIDENCE", [support, risk], "",
         "회복·상승 조건과 약화·하락 조건이 함께 확인됐습니다.",
         "관측 기간과 반대 근거를 함께 비교해야 합니다. 규칙 개수로 방향을 정하지 않습니다.",
         "각 조건의 관측 기간과 실제 값이 다음 자료에서도 유지되는지 확인합니다."),

    ]
    for short, code, policy_id, label in [
        ("position_limit_support", "POSITION_LIMIT_WITH_SUPPORT",
         "graph.position.concentration.guard.v1",
         "회복·상승 근거가 있어도 계좌 비중 상한에 따른 대응은 유지됩니다."),
        ("loss_policy_support", "LOSS_POLICY_WITH_SUPPORT",
         "graph.instrument_profile.averaging_down_policy.v1",
         "회복·상승 근거가 있어도 추가매수 제한은 유지됩니다."),
    ]:
        policy = source.get(policy_id)
        if policy is None or not any(d.management_response for d in policy.derivations):
            raise ValueError("Composition requires the exact account policy: " + policy_id)
        if any(c.role != "required" for c in policy.conditions):
            raise ValueError("Cannot flatten optional policy predicates into a composition")
        definitions.append((short, code, [*deepcopy(policy.conditions), support], policy_id, label,
                            "시장 전망과 계좌 관리 기준은 별개입니다. 시장 근거가 관리 제한을 해제하지 않습니다.",
                            next(d.management_response["releaseCondition"] for d in policy.derivations if d.management_response)))
    result = []
    for short, code, conditions, policy_id, label, meaning, check in definitions:
        if any(c is None for c in conditions):
            continue  # No approved source model: never manufacture a flow judgment.
        rid = PREFIX + short + ".v1"
        response = {"version": VERSION, "authority": "typedb-rulebox", "code": code,
                    "label": label, "meaning": meaning, "nextCheck": check,
                    "conditionIds": [c.condition_id for c in conditions],
                    "policyRuleId": policy_id, "boundary": "조건 조합의 해석이며 예측 적중이나 주문 승인이 아닙니다."}
        result.append(GraphInferenceRule(
            rule_id=rid, label=label, version="v1", source_kind="stock",
            action_group="context", action_level="review", conditions=conditions,
            derivations=[GraphRuleDerivation(
                relation_type="REQUIRES_NEXT_CHECK", target_kind="next-check",
                target_key="{subjectId}:" + short, target_label="{displayName} " + label,
                tbox_class="NextCheck", tbox_classes=["NextCheck", "RuleEvidenceComposition"],
                polarity="context", evidence_role="context", decision_effect="defer",
                action_group="context", action_level="review", decision_stage="MACRO_REGIME",
                decision_label="여러 조건을 함께 확인", next_checks=[check],
                composition_response=response, notification_category="relationshipChange",
                notification_severity="info",
            )],
            prompt_hint=meaning, execution_stage="native-evidence-composition",
        ))
    # Use the same authoring governance as the source catalog. Explicit
    # ownership makes these context rules, never new predictive hypotheses.
    from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import with_rulebox_v3_governance
    return with_rulebox_v3_governance(result)
