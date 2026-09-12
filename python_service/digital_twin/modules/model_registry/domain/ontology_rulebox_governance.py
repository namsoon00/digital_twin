import hashlib
import json
import re
from typing import Dict, Iterable, List

from digital_twin.modules.reasoning.contracts import DECISION_EFFECTS
from digital_twin.modules.model_registry.domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION, HOLDING_TARGET_ROLE, WATCHLIST_ALLOWED_ACTIONS, WATCHLIST_TARGET_ROLE, GraphInferenceRule, GraphRuleCondition, GraphRuleDerivation
from digital_twin.modules.model_registry.domain.ontology_rule_knowledge import knowledge_basis_violations
from digital_twin.modules.model_registry.domain.rule_claim_contract import rule_claim_contract_violations
from digital_twin.modules.model_registry.domain.hypothesis_compilation import compilation_blockers, rule_design_context, validation_requirements


RULEBOX_EVIDENCE_ROLES = frozenset({"risk", "support", "counter", "context", "blocking"})
RULEBOX_HYPOTHESIS_SCOPES = frozenset({"market", "account", "mixed", "unverified"})
RULEBOX_ACTIONS = frozenset({"BUY", "ADD", "TRIM", "SELL", "HOLD", "AVOID"})


def rulebox_semantic_violations(rules: Iterable[GraphInferenceRule]) -> List[str]:
    """Return static RuleBox contract violations before TypeDB persistence.

    This checks authoring metadata only. TypeDB remains the sole evaluator of
    investment conditions and action evidence.
    """

    violations: List[str] = []
    enabled_rules = [rule for rule in list(rules or []) if bool(getattr(rule, "enabled", True))]
    signatures: Dict[tuple, List[str]] = {}
    for rule in enabled_rules:
        rule_id = str(rule.rule_id or "").strip() or "<missing-rule-id>"
        knowledge_basis = rule.resolved_knowledge_basis
        violations.extend(knowledge_basis_violations(knowledge_basis, rule_id))
        violations.extend(rule_claim_contract_violations(rule.resolved_claim_contract, rule_id))
        violations.extend(rule_model_signal_family_violations(rule))
        if not str(rule.hypothesis_family_key or "").strip():
            violations.append(rule_id + ": hypothesis_family_key is required")
        lifecycle = rule.resolved_hypothesis_lifecycle()
        condition_ids = {str(item.condition_id or "").strip() for item in rule.conditions or []}
        if not lifecycle.formation_condition_ids:
            violations.append(rule_id + ": lifecycle formation_condition_ids is required")
        elif set(lifecycle.formation_condition_ids) - condition_ids:
            violations.append(rule_id + ": lifecycle formation_condition_ids reference unknown conditions")
        if int(lifecycle.validity_minutes or 0) <= 0:
            violations.append(rule_id + ": lifecycle validity_minutes must be positive")
        if not lifecycle.required_freshness_domains:
            violations.append(rule_id + ": lifecycle required_freshness_domains is required")
        if not str(lifecycle.invalidation_mode or "").strip():
            violations.append(rule_id + ": lifecycle invalidation_mode is required")

        condition_signatures: Dict[str, List[str]] = {}
        any_groups = set()
        for condition in rule.conditions or []:
            condition_id = str(condition.condition_id or "").strip() or "<missing-condition-id>"
            scope = str(condition.hypothesis_scope or "").strip().lower()
            if scope not in RULEBOX_HYPOTHESIS_SCOPES:
                violations.append(rule_id + ": " + condition_id + " has no valid hypothesis_scope")
            group = str(condition.evidence_group_key or "").strip()
            if not group:
                violations.append(rule_id + ": " + condition_id + " has no evidence_group_key")
            if str(condition.role or "required").strip().lower() in {"any", "optional"} and group:
                any_groups.add(group)
            condition_signatures.setdefault(rulebox_condition_signature(condition), []).append(condition_id)
        for duplicate_ids in condition_signatures.values():
            if len(duplicate_ids) > 1:
                violations.append(rule_id + ": duplicate condition semantics " + ", ".join(sorted(duplicate_ids)))
        if int(rule.any_condition_min_count or 1) > 1 and int(rule.any_condition_min_count or 1) > len(any_groups):
            violations.append(rule_id + ": any_condition_min_count exceeds independent evidence groups")

        for derivation in rule.derivations or []:
            evidence_role = str(derivation.evidence_role or "").strip().lower()
            if evidence_role not in RULEBOX_EVIDENCE_ROLES:
                violations.append(rule_id + ": derivation has no valid evidence_role")
            effect = str(derivation.decision_effect or "").strip().lower()
            if effect not in DECISION_EFFECTS:
                violations.append(rule_id + ": derivation has no valid decision_effect")
            candidate_action = str(derivation.candidate_action or "").strip().upper()
            if knowledge_basis.rule_kind != "predictive-hypothesis":
                if candidate_action:
                    violations.append(
                        rule_id + ": only predictive-hypothesis rules may author candidate_action"
                    )
                continue
            if candidate_action not in RULEBOX_ACTIONS:
                violations.append(rule_id + ": predictive derivation has no valid candidate_action")
                continue
            target_role = str(derivation.target_role or "").strip().lower()
            if target_role == WATCHLIST_TARGET_ROLE and candidate_action not in WATCHLIST_ALLOWED_ACTIONS:
                violations.append(rule_id + ": watchlist derivation permits holding-only action " + candidate_action)
            if candidate_action == "BUY" and target_role and target_role != WATCHLIST_TARGET_ROLE:
                violations.append(rule_id + ": BUY candidate requires watchlist target_role")
            if candidate_action in {"ADD", "TRIM", "SELL"} and target_role and target_role != HOLDING_TARGET_ROLE:
                violations.append(rule_id + ": " + candidate_action + " candidate requires holding target_role")

        signatures.setdefault(rulebox_rule_condition_set_signature(rule), []).append(rule_id)
    for rule_ids in signatures.values():
        if len(rule_ids) > 1:
            violations.append("duplicate enabled rule conditions: " + ", ".join(sorted(rule_ids)))
    return sorted(set(violations))


def rule_model_signal_family_violations(rule: GraphInferenceRule) -> List[str]:
    """Reject model evidence that proves a different hypothesis family."""

    basis = rule.resolved_knowledge_basis
    if basis.rule_kind != "predictive-hypothesis":
        return []

    # The model release catalog consumes RuleBox contracts as well, so keep
    # these imports local and avoid a module initialization cycle.
    from digital_twin.modules.model_registry.domain.statistical_signals.registry import signal_hypothesis_family
    from digital_twin.modules.model_registry.domain.statistical_signals.rule_contracts import rule_statistical_signal_contract

    rule_id = str(rule.rule_id or "").strip() or "<missing-rule-id>"
    signal_types = list(rule_statistical_signal_contract(rule).get("signalTypes") or [])
    if not signal_types:
        return [rule_id + ": predictive rule has no governed model signal type"]
    signal_families = sorted({
        signal_hypothesis_family(signal_type)
        for signal_type in signal_types
        if signal_hypothesis_family(signal_type)
    })
    if len(signal_families) != 1 or signal_families[0] != basis.thesis_family:
        return [
            rule_id
            + ": model signal thesis family "
            + (",".join(signal_families) or "<unmapped>")
            + " does not match claim thesis family "
            + (basis.thesis_family or "<missing>")
        ]
    return []


def validate_rulebox_semantics(rules: Iterable[GraphInferenceRule]) -> None:
    violations = rulebox_semantic_violations(rules)
    if violations:
        raise ValueError("RuleBox semantic validation failed: " + " | ".join(violations[:12]))


def rulebox_condition_signature(condition: GraphRuleCondition) -> str:
    payload = condition.to_dict()
    for field in ("condition_id", "description", "hypothesis_scope", "evidence_group_key"):
        payload.pop(field, None)
    return json.dumps(canonical_json_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rulebox_rule_condition_set_signature(rule: GraphInferenceRule) -> tuple:
    return tuple(sorted(rulebox_condition_signature(condition) for condition in rule.conditions or []))


def rulebox_rules_payload(rules: Iterable[GraphInferenceRule]) -> List[Dict[str, object]]:
    return [rule.to_dict() for rule in rules]


def rulebox_rules_hash(rules_payload: List[Dict[str, object]]) -> str:
    canonical_rules = sorted(
        [canonical_rulebox_rule(item) for item in (rules_payload or []) if isinstance(item, dict)],
        key=lambda item: str(item.get("rule_id") or item.get("ruleId") or ""),
    )
    encoded = json.dumps(canonical_rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_rulebox_rule(rule: Dict[str, object]) -> Dict[str, object]:
    result = canonical_json_value(rule)
    if not isinstance(result, dict):
        return {}
    manifest_key = "domain_manifest" if isinstance(result.get("domain_manifest"), dict) else "domainManifest"
    if isinstance(result.get(manifest_key), dict):
        # Model migration plans are catalog metadata. They do not alter a
        # TypeDB condition, derivation, routing dependency, or action envelope.
        # Keeping them out of the executable hash avoids freezing a new
        # RuleBox release for a shadow-only analysis contract.
        result[manifest_key].pop("statisticalSignalContract", None)
    if isinstance(result.get("conditions"), list):
        result["conditions"] = sorted(
            [canonical_json_value(item) for item in result.get("conditions") if isinstance(item, dict)],
            key=lambda item: str(item.get("condition_id") or item.get("conditionId") or ""),
        )
    if isinstance(result.get("derivations"), list):
        result["derivations"] = sorted(
            [canonical_rulebox_derivation(item, result) for item in result.get("derivations") if isinstance(item, dict)],
            key=lambda item: "|".join([
                str(item.get("relation_type") or item.get("relationType") or ""),
                str(item.get("target_key") or item.get("targetKey") or ""),
                str(item.get("target_kind") or item.get("targetKind") or ""),
                str(item.get("action_group") or item.get("actionGroup") or ""),
                str(item.get("action_level") or item.get("actionLevel") or ""),
            ]),
        )
    return result


def canonical_rulebox_derivation(derivation: Dict[str, object], rule: Dict[str, object] = None) -> Dict[str, object]:
    result = canonical_json_value(derivation)
    if not isinstance(result, dict):
        return {}
    rule = rule if isinstance(rule, dict) else {}
    action_group = str(result.get("action_group") or result.get("actionGroup") or rule.get("action_group") or rule.get("actionGroup") or "")
    action_level = str(result.get("action_level") or result.get("actionLevel") or rule.get("action_level") or rule.get("actionLevel") or "")
    if action_group:
        if "action_group" in result or "actionGroup" not in result:
            result["action_group"] = action_group
        else:
            result["actionGroup"] = action_group
    if action_level:
        if "action_level" in result or "actionLevel" not in result:
            result["action_level"] = action_level
        else:
            result["actionLevel"] = action_level
    decision_stage = str(result.get("decision_stage") or result.get("decisionStage") or "").strip()
    if decision_stage:
        if "decision_stage" in result or "decisionStage" not in result:
            result["decision_stage"] = decision_stage
        else:
            result["decisionStage"] = decision_stage
    evidence_role = str(result.get("evidence_role") or result.get("evidenceRole") or result.get("polarity") or "context").strip().lower()
    result["evidence_role"] = evidence_role if evidence_role in {"risk", "support", "counter", "context", "blocking"} else "context"
    for legacy_key in (
        "stage_priority", "stagePriority", "risk_impact", "riskImpact",
        "support_impact", "supportImpact", "weight", "min_weight", "minWeight",
    ):
        result.pop(legacy_key, None)
    return result


def canonical_json_value(value: object) -> object:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return round(value, 10)
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return [canonical_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): canonical_json_value(value[key]) for key in sorted(value.keys(), key=str)}
    return value


def rulebox_version_payload(
    rules: Iterable[GraphInferenceRule],
    created_at: str,
    change_reason: str = "",
    author: str = "",
    status: str = "saved",
) -> Dict[str, object]:
    rules_payload = rulebox_rules_payload(rules)
    rules_hash = rulebox_rules_hash(rules_payload)
    rule_count = len(rules_payload)
    condition_count = sum(len(item.get("conditions") or []) for item in rules_payload)
    derivation_count = sum(len(item.get("derivations") or []) for item in rules_payload)
    short_hash = rules_hash[:12]
    time_token = "".join(ch for ch in str(created_at or "") if ch.isalnum())[:24] or short_hash
    return {
        "id": "rulebox-version:" + short_hash + ":" + time_token,
        "label": "RuleBox " + short_hash,
        "versionLabel": short_hash,
        "rulesHash": rules_hash,
        "shortHash": short_hash,
        "ruleCount": rule_count,
        "conditionCount": condition_count,
        "derivationCount": derivation_count,
        "createdAt": created_at,
        "changeReason": str(change_reason or "").strip(),
        "author": str(author or "local-admin").strip() or "local-admin",
        "status": str(status or "saved"),
        "engineVersion": GRAPH_REASONER_VERSION,
        "rulesJson": json.dumps(rules_payload, ensure_ascii=False, sort_keys=True),
    }


def rulebox_governance_candidates(
    rules_payload: List[Dict[str, object]],
    versions: List[Dict[str, object]] = None,
    persisted_candidates: List[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    rules_payload = list(rules_payload or [])
    versions = list(versions or [])
    persisted_candidates = list(persisted_candidates or [])
    rule_ids = {
        str(item.get("rule_id") or item.get("ruleId") or "").strip()
        for item in rules_payload
        if isinstance(item, dict)
    }
    candidates: List[Dict[str, object]] = []

    for candidate in persisted_candidates:
        normalized = normalize_rule_change_candidate(candidate, existing_rule_ids=rule_ids)
        if normalized:
            candidates.append(normalized)

    if not versions:
        candidates.append({
            "id": "governance.baseline-version.v1",
            "title": "현재 RuleBox 기준선 버전 기록",
            "status": "review",
            "priority": 92,
            "source": "rulebox-governance",
            "rationale": "규칙을 운영 자산으로 다루려면 저장 시점, 해시, 변경 이유가 남아야 합니다.",
            "action": "save-current-version",
            "requiresData": [],
            "proposedRule": None,
        })

    if missing_decision_policy_count(rules_payload):
        candidates.append({
            "id": "governance.decision-policy-complete.v1",
            "title": "파생 관계 판단 단계 정책 보강",
            "status": "review",
            "priority": 88,
            "source": "rulebox-governance",
            "rationale": "decisionStage와 evidenceRole이 없는 파생 관계는 AI가 확인 순서와 근거 방향을 구분하기 어렵습니다.",
            "action": "complete-decision-policy",
            "requiresData": [],
            "proposedRule": None,
            "affectedDerivationCount": missing_decision_policy_count(rules_payload),
        })

    for candidate in curated_rule_candidates():
        proposed = candidate.get("proposedRule") if isinstance(candidate.get("proposedRule"), dict) else {}
        rule_id = str(proposed.get("rule_id") or proposed.get("ruleId") or "").strip()
        if rule_id and rule_id in rule_ids:
            candidate = dict(candidate)
            candidate["status"] = "covered"
            candidate["rationale"] = "동일 rule_id가 이미 RuleBox에 있습니다."
        candidates.append(candidate)

    return sorted(deduplicate_candidates(candidates), key=lambda item: (int(item.get("priority") or 0), str(item.get("id") or "")), reverse=True)


def deduplicate_candidates(candidates: List[Dict[str, object]]) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    seen = set()
    for candidate in candidates or []:
        candidate_id = str(candidate.get("id") or "").strip()
        proposed = candidate.get("proposedRule") if isinstance(candidate.get("proposedRule"), dict) else {}
        proposed_id = str(proposed.get("rule_id") or proposed.get("ruleId") or "").strip()
        key = candidate_id or proposed_id or str(candidate.get("title") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(candidate)
    return result


def build_rule_change_candidate_prompt(context: Dict[str, object]) -> str:
    payload = compact_candidate_context(context or {})
    contract = {
        "candidates": [
            {
                "title": "string",
                "rationale": "why this ontology relation is useful",
                "expectedEffect": "how this changes AI opinions or alert quality",
                "risk": "false positive or data risk",
                "requiresData": [],
                "blockers": [],
                "validationRequirements": [
                    {"check": "current-match", "requirement": "Verify all candidate predicates in the current scoped TypeDB ABox", "dependencyKey": "current-replay"},
                    {"check": "review", "requirement": "Specific empirical or causal checks not proven by a current match", "dependencyKey": "outcome-validation"},
                ],
                "priority": 0,
                "proposedRule": {
                    "rule_id": "graph.example.context.v1",
                    "label": "Korean label",
                    "version": "ai-candidate-v1",
                    "source_kind": "stock",
                    "enabled": False,
                    "action_group": "alertReview",
                    "action_level": "watch",
                    "prompt_hint": "Korean prompt hint",
                    "conditions": [],
                    "derivations": [],
                },
            }
        ]
    }
    return "\n".join([
        "너는 자동매매 시스템이 아니라 투자 온톨로지 RuleBox 설계 리뷰어다.",
        "목표: 현재 TypeDB RuleBox, InferenceBox, 최근 데이터 변경, 알림 근거를 보고 새로운 RuleChangeCandidate만 제안한다.",
        "제약:",
        "- 매수/매도 지시를 만들지 말고 관계 후보만 제안한다.",
        "- proposedRule.enabled는 반드시 false다.",
        "- blockers/requiresData에는 후보 명세 자체를 작성할 수 없는 원인만 적는다. 작성 가능한 후보는 proposedRule을 반환하고 blockers와 requiresData는 빈 배열로 둔다.",
        "- validationRequirements는 작성 후 검증 조건이다. 현재 TypeDB 실행 확인은 typedb-execution, 모든 후보 조건의 현재 일치 확인은 current-match, 독립 사건/미래 관측/연구 교차검증은 review로 구분한다.",
        "- 미래 관측이나 현재 후보 일치 여부가 미확인이라는 이유만으로 작성 가능한 proposedRule을 비우지 않는다. validationRequirements에 기록하고 검증 단계로 넘긴다.",
        "- review 검증은 후보가 일치하거나 가격 스냅샷이 많다는 이유로 통과되지 않는다. AI는 검증 결과나 통과 여부를 작성하지 않는다.",
        "- ruleDesign.observationState가 not-queried이면 현재 ABox를 조회하지 않은 명세 작성 단계다. 빈 inferenceBox.relations를 결측 증거로 사용하지 않는다.",
        "- 명세와 모델 계약이 확인되면 현재 사실의 존재를 단정하지 않고 후보를 작성한다. 현재 일치 여부는 후속 TypeDB preview가 검증한다.",
        "- 현재 사실 확인 없이는 작성할 수 없다면 unverified-observation으로 표시한다. 이를 missing-observation이나 관측 기간 부족으로 단정하지 않는다.",
        "- 작성 차단 항목마다 blockers에 원인을 구분한다. 규칙 명세 불일치는 schema-mismatch, 미구현 모델/공급자는 unsupported-capability다. 단순 후속 검증 요구를 blockers로 옮기지 않는다.",
        "- ruleDesign의 필드 명세와 실제 조건/파생 예시를 사용한다. 명세에 있는 필드를 알 수 없다는 이유로 데이터 수집을 요구하지 않는다.",
        "- evidence ID 보존은 가설의 출처 계보로 처리하며 PRESERVES_RULE_LINEAGE 관측이 있어야 규칙을 작성할 수 있다고 요구하지 않는다.",
        "- 새 예측 모델 등록이 필요하면 unsupported-capability로 명시한다. 기존 모델을 다른 인과 가설의 증거로 바꾸지 않는다.",
        "- ruleDesign.capabilityIndex에서 관련 등록 규칙과 정확한 모델 계약을 확인한다. 전체 예시가 3개라는 이유로 나머지 기능이 없다고 단정하지 않는다.",
        "- 이미 있는 가설보다 강한 인과 주장을 추가하려면 그 차이를 구체적으로 설명한다. 비슷한 등록 모델이 있다는 사실만으로 새 주장이 검증됐다고 판단하지 않는다.",
        "- modelAssessmentContext는 명시된 계정·종목·시각의 저장된 모델 평가다. not-supported/failedConditionIds는 모델 미등록이나 필수 원천 자료 결측을 뜻하지 않는다.",
        "- 모델 평가가 현재 TypeDB 세대와 같다고 추측하거나 과거 제안 시점의 증거로 소급하지 않는다. 현재 성립 여부는 후보 preview에서 확인한다.",
        "- relation_type, condition field, target filters는 제공된 RuleBox/InferenceBox/TBox에서 확인 가능한 형태를 우선 사용한다.",
        "- hypothesisProposal이 있으면 그 주장 하나만 실행 가능한 후보 규칙으로 변환하고 다른 가설을 추가하지 않는다.",
        "- hypothesisProposal의 evidence ID는 출처 계보이며 조건 field나 relation_type으로 직접 사용하지 않는다.",
        "- derivations에는 decision_stage, evidence_role, decision_effect을 포함한다.",
        "- 예측 가설 후보만 candidate_action=HOLD, decision_effect=defer 또는 constrain으로 제한한다. 참고용 관계에는 candidate_action을 넣지 않는다.",
        "- knowledge_basis, claim_contract, model_input_contract, hypothesis_family_key, hypothesis_lifecycle을 제공 예시에 맞춰 명시한다. 참고용 관계는 원래 인과 가설의 검증 계약을 대신할 수 없다.",
        "- 중복 rule_id를 만들지 않는다.",
        "- 응답은 설명 없이 JSON 하나만 반환한다.",
        "JSON 계약:",
        json.dumps(contract, ensure_ascii=False, indent=2),
        "입력 컨텍스트:",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    ])


def compact_candidate_context(context: Dict[str, object]) -> Dict[str, object]:
    rulebox = context.get("ruleBox") if isinstance(context.get("ruleBox"), dict) else {}
    inferencebox = context.get("inferenceBox") if isinstance(context.get("inferenceBox"), dict) else {}
    proposal = context.get("hypothesisProposal") if isinstance(context.get("hypothesisProposal"), dict) else {}
    return {
        "trigger": context.get("trigger") or "manual",
        "symbols": list(context.get("symbols") or [])[:30],
        "ruleBox": {
            "status": rulebox.get("status"),
            "ruleCount": rulebox.get("ruleCount"),
            "relationTypes": list(rulebox.get("relationTypes") or [])[:40],
        },
        "inferenceBox": {
            "status": inferencebox.get("status"),
            "reason": inferencebox.get("reason"),
            "decisionEligibility": inferencebox.get("decisionEligibility"),
            "relationCount": inferencebox.get("relationCount"),
            "relations": [
                {
                    "type": item.get("type"),
                    "ruleId": item.get("ruleId"),
                    "polarity": item.get("polarity"),
                    "decisionStage": item.get("decisionStage"),
                    "evidenceRole": item.get("evidenceRole") or item.get("polarity") or "context",
                    "label": item.get("label") or item.get("aiInfluenceLabel"),
                }
                for item in list(inferencebox.get("relations") or [])[:40]
                if isinstance(item, dict)
            ],
        },
        "recentEvents": list(context.get("recentEvents") or [])[:20],
        "alerts": list(context.get("alerts") or [])[:20],
        "materialityAssessments": list(context.get("materialityAssessments") or [])[:20],
        "hypothesisProposal": {
            "caseId": proposal.get("caseId"),
            "proposalIds": list(proposal.get("sourceProposalIds") or proposal.get("proposalIds") or [])[:20],
            "inferenceGenerationIds": list(proposal.get("inferenceGenerationIds") or [])[:20],
            "symbol": proposal.get("symbol"),
            "claim": proposal.get("claim"),
            "causalPath": list(proposal.get("causalPath") or [])[:12],
            "supportingEvidenceIds": list(proposal.get("supportingEvidenceIds") or [])[:20],
            "counterEvidenceIds": list(proposal.get("counterEvidenceIds") or [])[:20],
            "requiredEvidenceTypes": list(proposal.get("requiredEvidenceTypes") or [])[:12],
            "invalidationConditions": list(proposal.get("invalidationConditions") or [])[:12],
        } if proposal else {},
        "ruleDesign": rule_design_context(context),
        "modelAssessmentContext": context.get("modelAssessmentContext") or {"status": "not-queried", "snapshots": []},
        "existingCandidates": [
            {"id": item.get("id"), "status": item.get("status"), "title": item.get("title")}
            for item in list(rulebox.get("changeCandidates") or [])[:20]
            if isinstance(item, dict)
        ],
    }


def rule_change_candidates_from_text(text: str, context: Dict[str, object] = None) -> List[Dict[str, object]]:
    payload = json_object_from_text(text)
    raw_candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    rulebox = (context or {}).get("ruleBox") if isinstance((context or {}).get("ruleBox"), dict) else {}
    existing_rule_ids = {
        str(item.get("rule_id") or item.get("ruleId") or "").strip()
        for item in (rulebox.get("rules") or [])
        if isinstance(item, dict)
    }
    candidates = [
        normalize_rule_change_candidate(item, existing_rule_ids=existing_rule_ids, source="ai-rule-candidate")
        for item in raw_candidates
        if isinstance(item, dict)
    ]
    unqueried = ((context or {}).get("inferenceBox") or {}).get("status") == "deferred-validation"
    if unqueried:
        for candidate in candidates:
            for blocker in candidate.get("blockers") or []:
                if blocker.get("kind") in {"missing-observation", "stale-observation"}:
                    blocker.update({
                        "kind": "unverified-observation", "owner": "development",
                        "requirement": "현재 ABox 미조회로 확인되지 않은 AI 요구사항: " + blocker["requirement"],
                    })
    return [item for item in candidates if item]


def json_object_from_text(text: str) -> Dict[str, object]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            decoded = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return {}
    return decoded if isinstance(decoded, dict) else {}


def normalize_rule_change_candidate(
    candidate: Dict[str, object],
    existing_rule_ids: Iterable[str] = None,
    source: str = "",
) -> Dict[str, object]:
    candidate = dict(candidate or {})
    existing = {str(item or "").strip() for item in (existing_rule_ids or []) if str(item or "").strip()}
    proposed = candidate.get("proposedRule") if isinstance(candidate.get("proposedRule"), dict) else None
    warnings: List[str] = [str(item) for item in (candidate.get("validationWarnings") or []) if str(item or "").strip()]
    normalized_rule = None
    proposed_rule_id = ""
    if proposed:
        proposed = dict(proposed)
        proposed["enabled"] = False
        proposed.setdefault("version", "ai-candidate-v1")
        proposed.setdefault("source_kind", "ai")
        proposed_rule_id = str(proposed.get("rule_id") or proposed.get("ruleId") or "").strip()
        try:
            normalized_rule = GraphInferenceRule.from_dict(proposed).to_dict()
            normalized_rule["enabled"] = False
        except ValueError as error:
            warnings.append("proposedRule invalid: " + str(error))
            normalized_rule = None
    status = str(candidate.get("status") or "").strip() or ("candidate" if normalized_rule else "data-required")
    if proposed_rule_id and proposed_rule_id in existing:
        status = "covered"
        warnings.append("same rule_id already exists in RuleBox")
    payload = {
        "id": str(candidate.get("id") or "") or candidate_id(candidate, normalized_rule),
        "title": str(candidate.get("title") or (normalized_rule or {}).get("label") or "AI 관계 후보"),
        "status": status,
        "priority": int(float(candidate.get("priority") or (74 if normalized_rule else 58))),
        "source": str(source or candidate.get("source") or "ai-rule-candidate"),
        "rationale": str(candidate.get("rationale") or ""),
        "expectedEffect": str(candidate.get("expectedEffect") or ""),
        "risk": str(candidate.get("risk") or ""),
        "action": str(candidate.get("action") or ("append-disabled-rule" if normalized_rule else "data-required")),
        "requiresData": [str(item) for item in (candidate.get("requiresData") or []) if str(item or "").strip()],
        "blockers": compilation_blockers([candidate]),
        "validationRequirements": validation_requirements(candidate),
        "proposedRule": normalized_rule,
        "proposedRuleDraft": proposed if proposed and normalized_rule is None else None,
        "validationWarnings": dedupe_strings(warnings),
    }
    if not payload["id"].startswith(("candidate.", "governance.", "ai-candidate:")):
        payload["id"] = "ai-candidate:" + payload["id"]
    return payload


def candidate_id(candidate: Dict[str, object], proposed_rule: Dict[str, object] = None) -> str:
    basis = {
        "title": candidate.get("title"),
        "rationale": candidate.get("rationale"),
        "ruleId": (proposed_rule or {}).get("rule_id") or (proposed_rule or {}).get("ruleId"),
    }
    encoded = json.dumps(basis, ensure_ascii=False, sort_keys=True)
    return "ai-candidate:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def dedupe_strings(values: Iterable[str]) -> List[str]:
    result = []
    seen = set()
    for value in values or []:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def missing_decision_policy_count(rules_payload: List[Dict[str, object]]) -> int:
    count = 0
    for rule in rules_payload or []:
        if not isinstance(rule, dict):
            continue
        for derivation in rule.get("derivations") or []:
            if not isinstance(derivation, dict):
                continue
            if not (derivation.get("decision_stage") or derivation.get("decisionStage")):
                count += 1
            elif not (derivation.get("evidence_role") or derivation.get("evidenceRole") or derivation.get("polarity")):
                count += 1
            elif str(derivation.get("decision_effect") or derivation.get("decisionEffect") or "").strip().lower() not in DECISION_EFFECTS:
                count += 1
    return count


def curated_rule_candidates() -> List[Dict[str, object]]:
    return [
        {
            "id": "candidate.factor-concentration-context.v1",
            "title": "팩터 집중 노출 컨텍스트",
            "status": "candidate",
            "priority": 76,
            "source": "ai-relation-candidate",
            "rationale": "보유 종목이 특정 팩터에 크게 묶이면 가격 신호 하나보다 포트폴리오 노출과 함께 봐야 합니다.",
            "action": "append-disabled-rule",
            "requiresData": ["HAS_FACTOR_EXPOSURE", "positionWeight"],
            "proposedRule": factor_concentration_candidate_rule().to_dict(),
        },
        {
            "id": "candidate.peer-sector-news-context.v1",
            "title": "피어·섹터 뉴스 컨텍스트",
            "status": "candidate",
            "priority": 72,
            "source": "ai-relation-candidate",
            "rationale": "직접 종목 뉴스가 아니어도 피어·섹터 이벤트가 투자 논리의 확인 항목이 될 수 있습니다.",
            "action": "append-disabled-rule",
            "requiresData": ["HAS_EXTERNAL_SIGNAL", "relationScope=peer|sector", "materialityState=material|notable"],
            "proposedRule": peer_sector_news_candidate_rule().to_dict(),
        },
        {
            "id": "candidate.data-quality-gate.v1",
            "title": "데이터 품질 게이트 고도화",
            "status": "data-required",
            "priority": 64,
            "source": "ai-relation-candidate",
            "rationale": "자료 상태가 부족하거나 사용할 수 없을 때 투자 판단을 막는 상태 규칙이 필요합니다.",
            "action": "extend-abox-schema",
            "requiresData": ["data-quality.dataState=insufficient|unavailable", "validationState=blocked"],
            "proposedRule": None,
        },
    ]


def factor_concentration_candidate_rule() -> GraphInferenceRule:
    return GraphInferenceRule(
        rule_id="graph.factor.position_crowding.v1",
        label="보유 종목 + 높은 팩터 노출 -> 팩터 집중 점검",
        version="candidate-v1",
        source_kind="stock",
        action_group="factorRisk",
        action_level="review",
        enabled=False,
        prompt_hint="팩터 노출은 단독 매도 신호가 아니라 같은 방향으로 움직일 수 있는 포트폴리오 리스크로 설명합니다.",
        conditions=[
            GraphRuleCondition(
                "holding-source",
                "subject_property",
                "보유 종목입니다.",
                field="source",
                operator="==",
                value="holding",
            ),
            GraphRuleCondition(
                "factor-exposure",
                "relation",
                "팩터 노출 관계가 포트폴리오 관점에서 의미 있습니다.",
                relation_type="HAS_FACTOR_EXPOSURE",
                target_kind="factor",
            ),
        ],
        derivations=[
            GraphRuleDerivation(
                relation_type="REQUIRES_NEXT_CHECK",
                target_kind="next-check",
                target_key="{symbol}:factor-concentration-review",
                target_label="{displayName} 팩터 집중 점검",
                tbox_class="NextCheck",
                tbox_classes=["NextCheck", "ExposureAssessment", "FactorExposure"],
                polarity="context",
                belief_label="팩터 노출이 커서 포트폴리오 리스크를 함께 확인해야 합니다.",
                ai_influence_label="팩터 집중 점검",
                action_group="factorRisk",
                action_level="review",
                decision_stage="FACTOR_CROWDING",
            )
        ],
    )


def peer_sector_news_candidate_rule() -> GraphInferenceRule:
    return GraphInferenceRule(
        rule_id="graph.news.peer_sector.material_context.v1",
        label="피어·섹터 중요 뉴스 -> 컨텍스트 재확인",
        version="candidate-v1",
        source_kind="stock",
        action_group="alertReview",
        action_level="watch",
        enabled=False,
        prompt_hint="피어·섹터 뉴스는 직접 매수·매도 판단보다 내 종목 투자 논리와 연결되는지 먼저 설명합니다.",
        conditions=[
            GraphRuleCondition(
                "peer-sector-material-event",
                "relation",
                "피어 또는 섹터 범위의 중요 외부 신호입니다.",
                relation_type="HAS_EXTERNAL_SIGNAL",
                target_kind="research-evidence",
                target_property_filters={
                    "relationScope": ["peer", "sector"],
                    "materialityPassed": True,
                    "materialityState": ["material", "notable"],
                },
            )
        ],
        derivations=[
            GraphRuleDerivation(
                relation_type="REQUIRES_NEXT_CHECK",
                target_kind="next-check",
                target_key="{symbol}:peer-sector-news-review",
                target_label="{displayName} 피어·섹터 뉴스 영향 점검",
                tbox_class="NextCheck",
                tbox_classes=["NextCheck", "NewsEvent", "PeerContext"],
                polarity="context",
                belief_label="피어·섹터 뉴스가 투자 논리에 영향을 줄 수 있어 연결성을 확인합니다.",
                ai_influence_label="피어·섹터 뉴스 컨텍스트",
                action_group="alertReview",
                action_level="watch",
                decision_stage="SECTOR_NEWS",
            )
        ],
    )
