"""RuleBox-owned lifecycle policy editing.

This use case updates only the policy attached to an existing TypeDB RuleBox
rule.  It intentionally has no API for changing a hypothesis lifecycle state:
states continue to be written solely from healthy, aligned InferenceBox
generations.
"""

from typing import Dict, Mapping

from ..domain.hypothesis_outcome_contract import (
    SUPPORTED_OBSERVATION_DOMAINS,
    SUPPORTED_OUTCOME_CRITERION_METRICS,
    SUPPORTED_OUTCOME_CRITERION_FAILURE_OUTCOMES,
    SUPPORTED_OUTCOME_CRITERION_OPERATORS,
    SUPPORTED_OUTCOME_CRITERION_ROLES,
    list_values,
)
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
        criteria = value.get("criteria") or []
        if not isinstance(criteria, list):
            raise ValueError("사후 검증 기준은 배열 형태여야 합니다.")
        seen_ids = set()
        benchmark_symbols = set()
        for index, raw in enumerate(criteria):
            if not isinstance(raw, Mapping):
                raise ValueError("사후 검증 기준 " + str(index + 1) + "은 객체 형태여야 합니다.")
            criterion_id = str(raw.get("criterionId") or raw.get("criterion_id") or "").strip()
            if not criterion_id:
                raise ValueError("사후 검증 기준 " + str(index + 1) + "의 criterionId가 필요합니다.")
            if criterion_id in seen_ids:
                raise ValueError("사후 검증 기준 ID가 중복되었습니다: " + criterion_id)
            seen_ids.add(criterion_id)
            metric = str(raw.get("metric") or "").strip()
            role = str(raw.get("role") or "result").strip().lower()
            operator = str(raw.get("operator") or ">=").strip()
            failure_outcome = str(raw.get("failureOutcome") or raw.get("failure_outcome") or "contradicted").strip().lower()
            if metric not in SUPPORTED_OUTCOME_CRITERION_METRICS:
                raise ValueError("지원하지 않는 사후 검증 지표입니다: " + metric)
            if role not in SUPPORTED_OUTCOME_CRITERION_ROLES:
                raise ValueError("지원하지 않는 사후 검증 역할입니다: " + role)
            if operator not in SUPPORTED_OUTCOME_CRITERION_OPERATORS:
                raise ValueError("지원하지 않는 사후 검증 연산자입니다: " + operator)
            if failure_outcome not in SUPPORTED_OUTCOME_CRITERION_FAILURE_OUTCOMES:
                raise ValueError("지원하지 않는 기준 미충족 처리입니다: " + failure_outcome)
            try:
                float(raw.get("threshold"))
            except (TypeError, ValueError) as error:
                raise ValueError("사후 검증 기준의 threshold는 숫자여야 합니다: " + criterion_id) from error
            raw_horizon = raw.get("horizonMinutes", raw.get("horizon_minutes", 0))
            try:
                criterion_horizon = int(float(str(raw_horizon or 0)))
            except (TypeError, ValueError) as error:
                raise ValueError("사후 검증 기준의 horizonMinutes는 숫자여야 합니다: " + criterion_id) from error
            if criterion_horizon < 0 or criterion_horizon > 60 * 24 * 365:
                raise ValueError("사후 검증 기준 시점은 0분 이상 1년 이하로 설정하세요: " + criterion_id)
            benchmark_symbol = str(raw.get("benchmarkSymbol") or raw.get("benchmark_symbol") or "").upper().strip()
            if metric in {"benchmarkReturnPct", "excessReturnPct"} and not benchmark_symbol:
                raise ValueError("벤치마크 수익률 기준에는 benchmarkSymbol이 필요합니다: " + criterion_id)
            if benchmark_symbol:
                benchmark_symbols.add(benchmark_symbol)
            criterion_domains = raw.get("requiredObservationDomains", raw.get("required_observation_domains"))
            invalid_criterion_domains = [
                str(item or "").strip().lower()
                for item in list_values(criterion_domains)
                if str(item or "").strip().lower() not in SUPPORTED_OBSERVATION_DOMAINS
            ]
            if invalid_criterion_domains:
                raise ValueError(
                    "사후 검증 기준의 지원하지 않는 데이터입니다: "
                    + ", ".join(invalid_criterion_domains)
                )
        if len(benchmark_symbols) > 1:
            raise ValueError("한 사후 검증 계약에서는 하나의 benchmarkSymbol만 사용할 수 있습니다.")

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
