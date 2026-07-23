"""RuleBox-owned lifecycle policy editing.

This use case updates only the policy attached to an existing TypeDB RuleBox
rule.  It intentionally has no API for changing a hypothesis lifecycle state:
states continue to be written solely from healthy, aligned InferenceBox
generations.
"""

from typing import Dict, Mapping

from ..domain.hypothesis_outcome_contract import SUPPORTED_OBSERVATION_DOMAINS, list_values
from ..domain.ontology_rulebox_contracts import GraphInferenceRule, HypothesisLifecyclePolicy


class HypothesisLifecyclePolicyService:
    def __init__(self, ontology_repository=None):
        self.ontology_repository = ontology_repository

    def prepare_update(self, rule_id: str, policy_payload: Dict[str, object]) -> Dict[str, object]:
        """Build a RuleBox candidate without writing it.

        The governed policy workflow reuses this exact merge path for preview
        and approval.  Keeping the candidate construction here prevents a
        preview from validating a different policy than the one later saved.
        """
        target_rule_id = str(rule_id or "").strip()
        if not target_rule_id:
            raise ValueError("RuleBox 규칙 ID가 필요합니다.")
        if not self.ontology_repository or not hasattr(self.ontology_repository, "rulebox_snapshot"):
            raise RuntimeError("TypeDB RuleBox 저장소가 구성되지 않았습니다.")
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
            self.validate_outcome_contract_payload(
                requested.get("outcomeContract", requested.get("outcome_contract"))
            )
            merged = {
                "formationConditionIds": requested.get("formationConditionIds", requested.get("formation_condition_ids", existing.get("formationConditionIds"))),
                "invalidationConditionIds": requested.get("invalidationConditionIds", requested.get("invalidation_condition_ids", existing.get("invalidationConditionIds"))),
                "validityMinutes": requested.get("validityMinutes", requested.get("validity_minutes", existing.get("validityMinutes"))),
                "requiredFreshnessDomains": requested.get("requiredFreshnessDomains", requested.get("required_freshness_domains", existing.get("requiredFreshnessDomains"))),
                "nextDataRequirements": requested.get("nextDataRequirements", requested.get("next_data_requirements", existing.get("nextDataRequirements"))),
                "invalidationMode": requested.get("invalidationMode", requested.get("invalidation_mode", existing.get("invalidationMode"))),
                "outcomeContract": requested.get("outcomeContract", requested.get("outcome_contract", existing.get("outcomeContract"))),
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
        return {
            "ruleId": target_rule_id,
            "rules": updated_rules,
            "updatedRule": updated_rule,
            "snapshot": snapshot,
        }

    def validate_outcome_contract_payload(self, value: object) -> None:
        """Reject typos at the API boundary instead of silently weakening review."""
        if value in (None, ""):
            return
        if not isinstance(value, Mapping):
            raise ValueError("사후 관측 계약은 객체 형태여야 합니다.")
        raw_domains = value.get("requiredObservationDomains", value.get("required_observation_domains"))
        invalid_domains = [
            str(item or "").strip().lower()
            for item in list_values(raw_domains)
            if str(item or "").strip() and str(item or "").strip().lower() not in SUPPORTED_OBSERVATION_DOMAINS
        ]
        if invalid_domains:
            raise ValueError(
                "지원하지 않는 사후 관측 데이터입니다: " + ", ".join(invalid_domains)
                + ". 사용 가능: " + ", ".join(SUPPORTED_OBSERVATION_DOMAINS)
            )
        raw_horizons = value.get("outcomeHorizonMinutes", value.get("outcome_horizon_minutes"))
        for raw in list_values(raw_horizons):
            try:
                minutes = int(float(str(raw)))
            except (TypeError, ValueError) as error:
                raise ValueError("사후 확인 시점은 분 단위 숫자여야 합니다.") from error
            if minutes <= 0 or minutes > 60 * 24 * 365:
                raise ValueError("사후 확인 시점은 1분 이상 1년 이하로 설정하세요.")

    def update(
        self,
        rule_id: str,
        policy_payload: Dict[str, object],
        change_reason: str = "",
        author: str = "",
        save_status: str = "approved",
    ) -> Dict[str, object]:
        if not self.ontology_repository or not hasattr(self.ontology_repository, "save_rulebox"):
            raise RuntimeError("TypeDB RuleBox 정책을 저장할 수 없습니다.")
        prepared = self.prepare_update(rule_id, policy_payload)
        target_rule_id = str(prepared.get("ruleId") or "")
        reason = str(change_reason or "").strip() or ("웹 가설 수명주기 정책 변경: " + target_rule_id)
        result = self.ontology_repository.save_rulebox({
            "rules": prepared["rules"],
            "changeReason": reason,
            "source": "hypothesis-lifecycle-web-policy",
            "author": str(author or "web-main").strip() or "web-main",
            "status": str(save_status or "approved").strip() or "approved",
        })
        if not bool(result.get("saved")) and str(result.get("status") or "") not in {"ok", "saved"}:
            raise RuntimeError(str(result.get("reason") or "TypeDB RuleBox 정책을 저장하지 못했습니다."))
        return {
            "status": "ok",
            "source": "typedb-rulebox-lifecycle-policy",
            "changeReason": reason,
            "updatedRule": prepared["updatedRule"],
            "rulebox": {
                "status": result.get("status"),
                "saved": result.get("saved"),
                "ruleCount": result.get("ruleCount"),
                "versionCount": result.get("versionCount"),
            },
        }
