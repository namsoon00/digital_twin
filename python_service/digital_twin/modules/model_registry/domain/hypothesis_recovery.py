"""Bounded authoring repair and operator-facing progress; no investment authority."""

from copy import deepcopy

from .hypothesis_compilation import RULE_DESIGN_VERSION, authoring_input_fingerprint, compilation_fingerprint
from .ontology_rulebox_contracts import GraphInferenceRule


REPAIR_FROM_VERSION = "hypothesis-rule-design-v6-observation-contract"
REPAIR_CONTRACT = "registered-design-repair-v1"


def authoring_budget_available(case, maximum):
    if int(case.retry.get("authoringAttempts") or 0) < maximum:
        return True
    repair = case.retry.get("contractRepair") or {}
    return (repair.get("contract") == REPAIR_CONTRACT and repair.get("toVersion") == RULE_DESIGN_VERSION
            and repair.get("inputFingerprint") == authoring_input_fingerprint(case)
            and int(repair.get("attemptsUsed") or 0) < 1)


def repairable_specification(case):
    if (case.status not in {"needs-revision", "blocked"} or case.evolution.get("plan")
            or case.retry.get("contractRepair") or case.compilation_draft.get("designVersion") != REPAIR_FROM_VERSION):
        return False
    if any(row.get("kind") in {"unsupported-capability", "unverified-observation", "unclassified"}
           for row in case.retry.get("blockers") or []):
        return False
    for candidate in case.compilation_draft.get("candidates") or []:
        raw = candidate.get("proposedRule") or candidate.get("proposedRuleDraft")
        if raw:
            try:
                if GraphInferenceRule.from_dict(raw).resolved_claim_contract.is_predictive:
                    return True
            except (ValueError, TypeError):
                continue
    return False


def begin_contract_repair(case, stamp):
    if not repairable_specification(case):
        raise ValueError("No repairable legacy specification")
    draft = deepcopy(case.compilation_draft)
    content = {key: draft.get(key) for key in ("candidates", "contextSummary", "world")}
    if compilation_fingerprint(content) != draft.get("contentFingerprint"):
        raise ValueError("Previous compilation fingerprint mismatch")
    if draft.get("inputFingerprint") != authoring_input_fingerprint(case, design_version=REPAIR_FROM_VERSION):
        raise ValueError("Previous authoring evidence changed")
    case.retry["contractRepair"] = {
        "contract": REPAIR_CONTRACT, "fromVersion": REPAIR_FROM_VERSION, "toVersion": RULE_DESIGN_VERSION,
        "scheduledAt": stamp, "attemptLimit": 1, "attemptsUsed": 0,
        "previousAttempts": int(case.retry.get("authoringAttempts") or 0),
        "previousReason": case.blocked_reason, "previousDraft": draft,
        "previousValidationRequirements": deepcopy(case.validation_requirements),
        "inputFingerprint": authoring_input_fingerprint(case),
    }
    case.retry.update({"state": "authoring-retry", "nextCheckAt": stamp})


def blocks_reauthoring(case):
    return any(row.get("kind") in {"unsupported-capability", "unverified-observation", "unclassified"}
               for row in case.retry.get("blockers") or [])


def development_progress(case):
    kinds = {row.get("kind") for row in case.retry.get("blockers") or []}
    state = case.retry.get("state") or ""
    evolution = case.evolution
    deployed = bool((evolution.get("deployment") or {}).get("deploymentId"))
    observed = int((evolution.get("dataSummary") or {}).get("capturedInputs") or 0) > 0
    experiment_label = ("격리 버전 관측 중" if observed else "격리 버전 등록 · 첫 관측 대기") if deployed else "아직 시작하지 않음"
    terminal_labels = {"retired": "비교 종료 · 미반영", "superseded": "비교 종료 · 기준 변경",
                       "rolled-back": "운영 복원 완료", "strengthened": "운영 반영 후 검증 완료"}
    if evolution.get("plan") and case.status in terminal_labels:
        phase, label = "completed", terminal_labels[case.status]
        experiment_label = label
        action = "종료 이유와 저장된 비교 결과를 확인합니다."
    elif evolution.get("plan") and case.status == "evolution-monitoring":
        phase, label = "monitoring", "운영 반영 후 검증 중"
        experiment_label = label
        action = "반영 후 새 결과에서 성능 악화나 실행 오류가 생기는지 확인합니다."
    elif evolution.get("plan"):
        phase = "experiment"
        label = "비교 실험 관측" if observed else "비교 실험 준비"
        action = "고정된 관측 계획에 따라 다음 결과를 확인합니다."
    elif case.retry.get("reasonCode") == "authoring-capacity-unavailable" and state == "authoring-retry":
        phase, label, action = "capacity-wait", "AI 실행 순서 대기", "투자 판단 작업의 자리를 유지하며 작성 슬롯이 비면 다시 실행합니다."
    elif "unsupported-capability" in kinds:
        phase, label, action = "capability-required", "모델·수집 기능 보완 필요", "부족한 모델이나 관측 계약을 구현해야 합니다. 같은 AI 작성을 반복하지 않습니다."
    elif "dependency-error" in kinds:
        phase, label, action = "runtime-retry", "연결·실행 복구 필요", "이전 오류와 저장 후보를 유지하고 실행 경로를 다시 확인합니다."
    elif state == "waiting-condition":
        phase, label, action = "condition-wait", "조건 성립 대기", "조회는 성공했습니다. 실제 조건이 성립하는지 다음 수집에서 확인합니다."
    elif state in {"waiting-data", "waiting-observation", "waiting-validation"}:
        phase, label, action = "observation-wait", "자료·후속 검증 대기", "부족한 자료와 아직 실행하지 않은 검증을 구분해 확인합니다."
    elif state in {"processing", "authoring-retry", "interrupted", "validation-retry"}:
        phase, label, action = "scheduled", "작성·검증 진행", "예약된 작성 또는 저장 후보 검증을 진행합니다."
    else:
        phase, label, action = "specification-required", "가설 명세 수정 필요", "실패한 명세를 수정해야 합니다. 새 시세를 기다리는 것으로 해결되지 않습니다."
    return {"phase": phase, "label": label, "nextAction": action,
            "reason": evolution.get("reason") or case.blocked_reason,
            "nextCheckAt": case.retry.get("nextCheckAt") or "",
            "authoringAttempts": int(case.retry.get("authoringAttempts") or 0),
            "contractRepairEligible": repairable_specification(case),
            "repairAttemptsUsed": (case.retry.get("contractRepair") or {}).get("attemptsUsed", 0),
            "experimentStarted": deployed, "experimentStateLabel": experiment_label,
            "requirements": list(case.retry.get("requirements") or [])}
