"""Observe native account-policy responses without requiring a forecast."""

from collections.abc import Mapping

from digital_twin.modules.model_registry.contracts import rule_notification_intent


VERSION = "typedb-management-observation-v2"


def mapping(value):
    return dict(value) if isinstance(value, Mapping) else {}


def management_only_observation_requested(context):
    """Identify the requested contract, never authorize its delivery."""
    payload = mapping(context)
    case = mapping(payload.get("investmentSubjectDecisionCase"))
    sources = [case, mapping(case.get("candidateSet")), mapping(case.get("synthesis")),
               mapping(payload.get("v2DecisionSynthesis"))]
    return bool(any(s.get("managementDecision") or s.get("management_decision") for s in sources)
                and not any(s.get("eligibleHypothesisIds") or s.get("eligible_hypothesis_ids")
                            or (s.get("graph_candidate_action") or s.get("graphCandidateAction") or "NO_ACTION") != "NO_ACTION"
                            for s in sources))


def management_observation_assessment(context):
    """Validate the frozen native policy proof without losing its claim.

    A compact match can omit a claim that remains on the native trace. Such
    a projection must not overwrite the original. Explicit invalid/conflicting
    copies still fail closed, independently of delivery novelty.
    """
    payload = mapping(context)
    # Hypothesis-backed subjects retain their existing review/publication path.
    if not management_only_observation_requested(payload):
        return {}
    def invalid(code, reason, rule_id=""):
        return {"version": VERSION, "status": "invalid", "reasonCode": code, "reason": reason,
                **({"ruleId": rule_id} if rule_id else {})}
    # The decisions package assembles syntheses using notification contracts.
    # Resolve this port after contract loading, not during module discovery.
    from digital_twin.modules.decisions.contracts import verified_management_decision
    management = verified_management_decision(payload)
    if not management:
        return invalid("management-notification-proof-invalid", "관리 판단과 TypeDB 원본 근거의 연결이 일치하지 않습니다.")
    relation = mapping(payload.get("ontologyRelationContext")) or mapping(
        mapping(payload.get("metadata")).get("ontologyRelationContext"))
    graph = mapping(relation.get("graphStoreInference"))
    def rule_id(row):
        return row.get("ruleId") or row.get("rule_id") or row.get("sourceRuleId")
    native = [r for r in [*(graph.get("traces") or []), *(graph.get("relations") or [])] if isinstance(r, Mapping)]
    projected = [r for r in relation.get("matchedRules") or [] if isinstance(r, Mapping)]
    ids = sorted({r["ruleId"] for r in [*management["responses"], *management.get('resolutions', [])]})
    for identity in ids:
        native_rows = [r for r in native if rule_id(r) == identity]
        if not native_rows:
            return invalid("management-notification-native-proof-missing", "관리 규칙의 TypeDB 원본 추론을 찾을 수 없습니다.", identity)
        intents, native_claim = [], False
        proof_rows = [(row, True) for row in native_rows] + [(row, False) for row in projected if rule_id(row) == identity]
        for row, is_native in proof_rows:
            if any(row.get(key) and row[key] != management[key] for key in (
                "accountId", "symbol", "worldId", "sourceAboxSnapshotId", "inferenceGenerationId")):
                return invalid("management-notification-proof-scope-mismatch", "관리 규칙의 계좌·종목·추론 세대가 원본과 다릅니다.", identity)
            claim = row.get("claimContract", row.get("claim_contract"))
            if claim in (None, {}):
                continue  # InferenceBox read projections can represent absence as {}.
            intent = rule_notification_intent(claim)
            if (not intent or intent["ruleId"] != identity or intent["purpose"] != "management-policy"
                    or "policy-changed" not in intent["notifyOn"]):
                return invalid("management-notification-purpose-invalid", "관리 판단은 생성됐지만 해당 규칙의 알림 목적이 유효하지 않습니다.", identity)
            intents.append(intent)
            native_claim = native_claim or is_native
        if not native_claim:
            return invalid("management-notification-purpose-missing", "관리 판단은 생성됐지만 해당 규칙의 원본 알림 목적이 보존되지 않았습니다.", identity)
        if len({intent["claimFingerprint"] for intent in intents}) != 1:
            return invalid("management-notification-purpose-conflict", "같은 관리 규칙의 원본과 전달 정보에서 알림 목적이 서로 다릅니다.", identity)
    contract = {"managementContractVersion": VERSION, "messageClass": "typedb-management-policy",
            "decisionEligibility": "management-policy", "ruleKind": "policy-constraint",
            "requiresHypothesis": False, "requiresAiJudgement": False, "requiresAiNarrative": False,
            "action": "NO_ACTION", "validationState": "typedb-policy-verified",
            **{key: management[key] for key in ("accountId", "symbol", "worldId", "sourceAboxSnapshotId", "inferenceGenerationId")},
            "managementDecisionId": management["decisionId"], "relationIds": ids,
            "selectedRuleLabel": "관리 기준 점검", "graphSource": relation.get("source"),
            "graphStore": relation.get("graphStore") or graph.get("graphStore"),
            "market": (relation.get("subject") or {}).get("market") or (relation.get("facts") or {}).get("market") or ""}
    return {"version": VERSION, "status": "eligible", "contract": contract}


def management_observation_contract(context):
    """Recognize policy-only candidates; successful receipts own novelty."""
    return management_observation_assessment(context).get("contract") or {}
