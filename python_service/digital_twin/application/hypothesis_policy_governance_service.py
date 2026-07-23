"""Governed RuleBox editing for hypothesis lifecycle and outcome contracts.

The service deliberately separates candidate construction, TypeDB preview,
human approval, and restoration.  It never changes a lifecycle state, creates
an investment action, or promotes a quality-review proposal by itself.
"""

import json
from typing import Dict, Mapping

from .hypothesis_lifecycle_policy_service import HypothesisLifecyclePolicyService


def text(value: object) -> str:
    return str(value or "").strip()


def public_version(value: Mapping[str, object]) -> Dict[str, object]:
    source = dict(value or {})
    return {
        key: source.get(key)
        for key in [
            "id",
            "label",
            "versionLabel",
            "rulesHash",
            "shortHash",
            "ruleCount",
            "conditionCount",
            "derivationCount",
            "status",
            "changeReason",
            "author",
            "engineVersion",
            "createdAt",
        ]
        if key in source
    }


class HypothesisPolicyGovernanceService:
    """Human-gated previews and version operations for a RuleBox policy."""

    def __init__(self, ontology_repository=None, lifecycle_policy_service=None):
        self.ontology_repository = ontology_repository
        self.lifecycle_policy_service = lifecycle_policy_service or HypothesisLifecyclePolicyService(ontology_repository)

    def versions(self, limit: int = 40) -> Dict[str, object]:
        repository = self.ontology_repository
        if not repository or not hasattr(repository, "rulebox_snapshot"):
            return {
                "status": "unavailable",
                "reason": "TypeDB RuleBox 저장소가 구성되지 않았습니다.",
                "versions": [],
                "automaticDeployment": False,
            }
        snapshot = repository.rulebox_snapshot()
        rows = [public_version(item) for item in snapshot.get("versions") or [] if isinstance(item, Mapping)]
        return {
            "status": str(snapshot.get("status") or "unknown"),
            "source": "typedb-rulebox-governance",
            "automaticDeployment": False,
            "versionCount": int(snapshot.get("versionCount") or len(rows)),
            "versions": rows[:max(1, min(100, int(limit or 40)))],
        }

    def record_baseline(self, author: str = "web-main") -> Dict[str, object]:
        repository = self.ontology_repository
        if not repository or not hasattr(repository, "ensure_rulebox_version_baseline"):
            raise RuntimeError("현재 RuleBox 기준선 버전 기록 기능이 구성되지 않았습니다.")
        result = repository.ensure_rulebox_version_baseline(author)
        if not bool(result.get("saved")) and text(result.get("status")) not in {"unchanged", "ok", "saved"}:
            raise RuntimeError(text(result.get("reason")) or "RuleBox 기준선 버전을 기록하지 못했습니다.")
        return {
            "status": "baseline-recorded" if bool(result.get("saved")) else "unchanged",
            "source": "typedb-rulebox-governance",
            "decisionEligibility": "policy-governance-only",
            "automaticDeployment": False,
            "result": result,
        }

    def preview(
        self,
        rule_id: str,
        policy: Dict[str, object],
        change_reason: str = "",
        symbols=None,
        world_id: str = "",
    ) -> Dict[str, object]:
        prepared = self.lifecycle_policy_service.prepare_update(rule_id, policy)
        validation = self.validate_candidate(
            prepared.get("rules") or [],
            symbols=symbols,
            world_id=world_id,
        )
        validation_status = text(validation.get("status"))
        return {
            "status": "ready-for-approval" if validation_status == "ok" else "preview-blocked",
            "source": "typedb-rulebox-policy-preview",
            "decisionEligibility": "policy-review-only",
            "automaticDeployment": False,
            "ruleId": text(prepared.get("ruleId")),
            "changeReason": text(change_reason) or ("가설 정책 미리보기: " + text(prepared.get("ruleId"))),
            "updatedRule": prepared.get("updatedRule") or {},
            "validation": validation,
            "approvalRequired": True,
        }

    def approve(
        self,
        rule_id: str,
        policy: Dict[str, object],
        change_reason: str = "",
        author: str = "web-main",
        symbols=None,
        world_id: str = "",
    ) -> Dict[str, object]:
        preview = self.preview(rule_id, policy, change_reason, symbols=symbols, world_id=world_id)
        if text(preview.get("status")) != "ready-for-approval":
            reason = text((preview.get("validation") or {}).get("reason")) or "TypeDB 후보 규칙 검증을 통과하지 못했습니다."
            raise RuntimeError("정책을 승인하지 않았습니다: " + reason)
        saved = self.lifecycle_policy_service.update(
            rule_id,
            policy,
            change_reason=text(preview.get("changeReason")),
            author=author,
            save_status="approved",
        )
        return {
            "status": "approved",
            "source": "typedb-rulebox-policy-governance",
            "decisionEligibility": "policy-governance-only",
            "automaticDeployment": False,
            "preview": preview,
            "saved": saved,
        }

    def restore(
        self,
        version_id: str,
        change_reason: str = "",
        author: str = "web-main",
        symbols=None,
        world_id: str = "",
    ) -> Dict[str, object]:
        repository = self.ontology_repository
        target = text(version_id)
        if not repository or not target:
            raise ValueError("복원할 RuleBox 버전 ID가 필요합니다.")
        snapshot = repository.rulebox_snapshot() if hasattr(repository, "rulebox_snapshot") else {}
        raw_version = next(
            (dict(item) for item in snapshot.get("versions") or [] if isinstance(item, Mapping) and text(item.get("id")) == target),
            None,
        )
        if not raw_version:
            raise KeyError("복원할 RuleBox 버전을 찾지 못했습니다: " + target)
        try:
            rules = json.loads(text(raw_version.get("rulesJson")) or "[]")
        except json.JSONDecodeError as error:
            raise RuntimeError("저장된 RuleBox 버전의 규칙 형식이 올바르지 않습니다: " + str(error)) from error
        if not isinstance(rules, list) or not rules:
            raise RuntimeError("저장된 RuleBox 버전에 복원 가능한 규칙이 없습니다.")
        current_rules = snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []
        if not self.policy_only_change(current_rules, rules):
            raise RuntimeError(
                "이 버전은 수명주기·사후 관측 계약 외의 RuleBox 구조도 바꿉니다. "
                "이 화면에서는 실행 규칙이 아닌 정책 전용 버전만 안전하게 복원할 수 있습니다."
            )
        validation = self.validate_candidate(rules, symbols=symbols, world_id=world_id, policy_only=True)
        if text(validation.get("status")) != "ok":
            reason = text(validation.get("reason")) or "TypeDB 후보 규칙 검증을 통과하지 못했습니다."
            raise RuntimeError("RuleBox 버전을 복원하지 않았습니다: " + reason)
        reason = text(change_reason) or ("RuleBox 버전 복원: " + target)
        if hasattr(repository, "restore_rulebox_version"):
            result = repository.restore_rulebox_version(target, reason, author)
        elif hasattr(repository, "save_rulebox"):
            result = repository.save_rulebox({
                "rules": rules,
                "changeReason": reason,
                "author": text(author) or "web-main",
                "status": "restored",
                "source": "hypothesis-policy-version-restore",
            })
        else:
            raise RuntimeError("TypeDB RuleBox 버전을 복원할 수 없습니다.")
        if not bool(result.get("saved")) and text(result.get("status")) not in {"ok", "saved"}:
            raise RuntimeError(text(result.get("reason")) or "RuleBox 버전을 저장하지 못했습니다.")
        return {
            "status": "restored",
            "source": "typedb-rulebox-policy-governance",
            "decisionEligibility": "policy-governance-only",
            "automaticDeployment": False,
            "restoredVersion": public_version(raw_version),
            "validation": validation,
            "rulebox": {
                "status": result.get("status"),
                "saved": result.get("saved"),
                "ruleCount": result.get("ruleCount"),
                "versionCount": result.get("versionCount"),
            },
        }

    def validate_candidate(self, rules, symbols=None, world_id: str = "", policy_only: bool = True) -> Dict[str, object]:
        repository = self.ontology_repository
        if not repository or not hasattr(repository, "validate_rulebox_materialization"):
            return {
                "status": "unavailable",
                "reason": "TypeDB 후보 규칙 검증 기능이 구성되지 않았습니다.",
                "validationOnly": True,
                "mutatedOperationalRuleBox": False,
                "wroteInferenceBox": False,
            }
        payload = {"rules": list(rules or [])}
        if policy_only:
            # Lifecycle and outcome contracts do not alter TypeDB predicate
            # bodies.  Keep their preview read-only; generic rule candidates
            # retain the separate materialization preview workflow.
            payload["policyOnly"] = True
        if symbols:
            payload["symbols"] = list(symbols) if isinstance(symbols, (list, tuple, set)) else [symbols]
        if text(world_id):
            payload["worldId"] = text(world_id)
        result = repository.validate_rulebox_materialization(payload)
        validation = dict(result or {}) if isinstance(result, Mapping) else {}
        validation.setdefault("validationOnly", True)
        validation.setdefault("mutatedOperationalRuleBox", False)
        validation.setdefault("wroteInferenceBox", False)
        return validation

    def policy_only_change(self, current_rules, candidate_rules) -> bool:
        """Check that a restore cannot replace an executable predicate body."""
        return self.rule_structure_signature(current_rules) == self.rule_structure_signature(candidate_rules)

    def rule_structure_signature(self, rules) -> str:
        normalized = []
        for raw in rules or []:
            if not isinstance(raw, Mapping):
                continue
            item = dict(raw)
            item.pop("hypothesis_lifecycle", None)
            item.pop("hypothesisLifecycle", None)
            normalized.append(item)
        normalized.sort(key=lambda item: text(item.get("rule_id") or item.get("ruleId")))
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
