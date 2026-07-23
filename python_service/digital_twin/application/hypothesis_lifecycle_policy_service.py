"""RuleBox-owned lifecycle policy editing.

This use case updates only the policy attached to an existing TypeDB RuleBox
rule.  It intentionally has no API for changing a hypothesis lifecycle state:
states continue to be written solely from healthy, aligned InferenceBox
generations.
"""

from typing import Dict, Mapping

from ..domain.ontology_rulebox_contracts import GraphInferenceRule, HypothesisLifecyclePolicy


class HypothesisLifecyclePolicyService:
    def __init__(self, ontology_repository=None):
        self.ontology_repository = ontology_repository

    def update(self, rule_id: str, policy_payload: Dict[str, object], change_reason: str = "") -> Dict[str, object]:
        target_rule_id = str(rule_id or "").strip()
        if not target_rule_id:
            raise ValueError("RuleBox 규칙 ID가 필요합니다.")
        if not self.ontology_repository or not hasattr(self.ontology_repository, "rulebox_snapshot"):
            raise RuntimeError("TypeDB RuleBox 저장소가 구성되지 않았습니다.")
        if not hasattr(self.ontology_repository, "save_rulebox"):
            raise RuntimeError("TypeDB RuleBox 정책을 저장할 수 없습니다.")
        snapshot = self.ontology_repository.rulebox_snapshot()
        rules = [dict(item) for item in snapshot.get("rules") or [] if isinstance(item, Mapping)]
        if not rules:
            raise RuntimeError("현재 TypeDB RuleBox 규칙을 읽지 못했습니다.")
        updated_rules = []
        updated_rule = None
        for raw in rules:
            if str(raw.get("rule_id") or raw.get("ruleId") or "").strip() != target_rule_id:
                updated_rules.append(raw)
                continue
            rule = GraphInferenceRule.from_dict(raw)
            existing = rule.resolved_hypothesis_lifecycle().to_dict()
            requested = dict(policy_payload or {}) if isinstance(policy_payload, Mapping) else {}
            merged = {
                "formationConditionIds": requested.get("formationConditionIds", requested.get("formation_condition_ids", existing.get("formationConditionIds"))),
                "invalidationConditionIds": requested.get("invalidationConditionIds", requested.get("invalidation_condition_ids", existing.get("invalidationConditionIds"))),
                "validityMinutes": requested.get("validityMinutes", requested.get("validity_minutes", existing.get("validityMinutes"))),
                "requiredFreshnessDomains": requested.get("requiredFreshnessDomains", requested.get("required_freshness_domains", existing.get("requiredFreshnessDomains"))),
                "nextDataRequirements": requested.get("nextDataRequirements", requested.get("next_data_requirements", existing.get("nextDataRequirements"))),
                "invalidationMode": requested.get("invalidationMode", requested.get("invalidation_mode", existing.get("invalidationMode"))),
            }
            policy = HypothesisLifecyclePolicy.from_dict(
                merged,
                default_formation_condition_ids=existing.get("formationConditionIds") or [],
            )
            candidate = rule.to_dict()
            candidate["hypothesis_lifecycle"] = policy.to_dict()
            candidate.pop("hypothesisLifecycle", None)
            updated_rules.append(candidate)
            updated_rule = {
                "ruleId": rule.rule_id,
                "label": rule.label,
                "policy": policy.to_dict(),
            }
        if not updated_rule:
            raise KeyError("가설 수명주기 정책을 가진 RuleBox 규칙을 찾지 못했습니다: " + target_rule_id)
        reason = str(change_reason or "").strip() or ("웹 가설 수명주기 정책 변경: " + target_rule_id)
        result = self.ontology_repository.save_rulebox({
            "rules": updated_rules,
            "changeReason": reason,
            "source": "hypothesis-lifecycle-web-policy",
        })
        if not bool(result.get("saved")) and str(result.get("status") or "") not in {"ok", "saved"}:
            raise RuntimeError(str(result.get("reason") or "TypeDB RuleBox 정책을 저장하지 못했습니다."))
        return {
            "status": "ok",
            "source": "typedb-rulebox-lifecycle-policy",
            "changeReason": reason,
            "updatedRule": updated_rule,
            "rulebox": {
                "status": result.get("status"),
                "saved": result.get("saved"),
                "ruleCount": result.get("ruleCount"),
                "versionCount": result.get("versionCount"),
            },
        }
