"""Product-launch qualification for one immutable investment-model release.

Operational promotion proves that an engine can run. Product readiness also
requires decision outcomes, calibrated rule governance, bounded latency, and
explicit operational review. This read model never promotes or edits a rule.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping


INVESTMENT_PRODUCT_READINESS_VERSION = "investment-product-readiness-v1"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _count(value: object) -> int:
    return max(0, int(_number(value)))


def _gate(
    gate_id: str,
    label: str,
    passed: bool,
    detail: str,
    *,
    required_for_closed_beta: bool = True,
    required_for_general_availability: bool = True,
) -> Dict[str, object]:
    return {
        "id": gate_id,
        "label": label,
        "status": "pass" if passed else "blocked",
        "passed": bool(passed),
        "detail": str(detail or ""),
        "requiredForClosedBeta": bool(required_for_closed_beta),
        "requiredForGeneralAvailability": bool(required_for_general_availability),
    }


def _latency_p95_ms(active_health: Mapping[str, object]) -> int:
    health = _mapping(active_health)
    candidates = [
        health.get("p95TotalDurationMs"),
        _mapping(health.get("runPerformance")).get("p95TotalDurationMs"),
        _mapping(health.get("queuePerformance")).get("p95TotalDurationMs"),
        _mapping(health.get("performance")).get("p95TotalDurationMs"),
        _mapping(health.get("queue")).get("endToEndP95Ms"),
        _mapping(health.get("queue")).get("durationP95Ms"),
    ]
    return max((_count(value) for value in candidates), default=0)


def investment_product_readiness(
    *,
    operational_promotion_ready: bool,
    rule_inventory: Mapping[str, object],
    catalog: Mapping[str, object],
    experiments: Mapping[str, object],
    active_health: Mapping[str, object],
    comparison: Mapping[str, object],
    settings: Mapping[str, object],
) -> Dict[str, object]:
    """Evaluate launch gates without confusing runtime health with quality."""

    inventory = _mapping(rule_inventory)
    catalog_payload = _mapping(catalog)
    performance = _mapping(catalog_payload.get("decisionPerformance"))
    performance_summary = _mapping(performance.get("summary"))
    statistical = _mapping(catalog_payload.get("statisticalSignals"))
    migration_counts = _mapping(statistical.get("migrationCounts"))
    comparison_payload = _mapping(comparison)
    settings_payload = _mapping(settings)

    calibration_episodes = _count(performance.get("calibrationEligibleEpisodeCount"))
    outcome_coverage = _number(performance.get("outcomeCoveragePct"))
    minimum_outcomes = max(20, _count(settings_payload.get("investmentLaunchMinimumOutcomeEpisodes") or 50))
    quarantine_rule_ids = [
        str(item or "")
        for item in _mapping(performance.get("governance")).get("quarantineRecommendedRuleIds") or []
        if str(item or "")
    ]
    comparison_samples = _count(comparison_payload.get("sampleCount"))
    minimum_comparisons = max(5, _count(settings_payload.get("investmentLaunchMinimumComparisonSamples") or 20))
    p95_ms = _latency_p95_ms(active_health)
    maximum_p95_ms = max(15000, _count(settings_payload.get("investmentLaunchMaximumP95Ms") or 60000))
    migration_remaining = sum(
        _count(migration_counts.get(key))
        for key in (
            "awaiting-governed-model-scorer",
            "shadow-signal-required",
            "unmapped",
            "missing",
        )
    )
    release_rule_ready = bool(inventory.get("releaseReady"))
    performance_ready = bool(
        str(performance.get("status") or "") == "ok"
        and calibration_episodes >= minimum_outcomes
        and outcome_coverage >= 80.0
    )
    comparison_ready = comparison_samples >= minimum_comparisons
    latency_ready = bool(p95_ms and p95_ms <= maximum_p95_ms)
    compliance_reviewed = str(
        settings_payload.get("investmentProductComplianceReviewed") or ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    soak_passed = str(
        settings_payload.get("investmentProductSoakTestPassed") or ""
    ).strip().lower() in {"1", "true", "yes", "on"}

    gates = [
        _gate(
            "operational-release",
            "운영 릴리스",
            operational_promotion_ready,
            "활성 엔진의 구조·저장소·배포 계약을 통과했습니다."
            if operational_promotion_ready else "활성 엔진의 운영 승격 계약이 완료되지 않았습니다.",
        ),
        _gate(
            "rule-contract",
            "규칙 계약",
            release_rule_ready,
            "실행 규칙의 의존성·출력·무효화 계약이 완전합니다."
            if release_rule_ready else "계약이 불완전한 실행 규칙이 남아 있습니다.",
        ),
        _gate(
            "outcome-calibration",
            "사후 결과 표본",
            performance_ready,
            "교정 가능 독립 판단 " + str(calibration_episodes) + "건 · 결과 연결률 " + str(round(outcome_coverage, 1)) + "%",
        ),
        _gate(
            "rule-performance",
            "규칙 성과 검토",
            not quarantine_rule_ids,
            "격리 검토 규칙 없음" if not quarantine_rule_ids else "격리 검토 필요 " + str(len(quarantine_rule_ids)) + "개",
        ),
        _gate(
            "engine-comparison",
            "릴리스 비교 표본",
            comparison_ready,
            "동일 시점 비교 " + str(comparison_samples) + "/" + str(minimum_comparisons) + "건",
        ),
        _gate(
            "latency-slo",
            "추론 지연",
            latency_ready,
            "p95 " + (str(round(p95_ms / 1000, 1)) + "초" if p95_ms else "측정 없음") + " · 한도 " + str(round(maximum_p95_ms / 1000, 1)) + "초",
        ),
        _gate(
            "statistical-signal-migration",
            "통계 신호 전환",
            migration_remaining == 0,
            "미구현 통계 신호 규칙 " + str(migration_remaining) + "개",
            required_for_closed_beta=False,
        ),
        _gate(
            "operational-soak",
            "연속 운영 검증",
            soak_passed,
            "운영 부하 시험 승인 완료" if soak_passed else "7일 연속 운영 부하 시험 승인이 필요합니다.",
            required_for_closed_beta=False,
        ),
        _gate(
            "compliance-review",
            "출시 정책 검토",
            compliance_reviewed,
            "출시 정책 검토 완료" if compliance_reviewed else "투자정보 표시·보안·개인정보 검토 승인이 필요합니다.",
            required_for_closed_beta=False,
        ),
    ]
    gate_targets = {
        "operational-release": "investment-model-overview",
        "rule-contract": "strategy-rulebox-editor",
        "outcome-calibration": "hypothesis-governance",
        "rule-performance": "experiment-validation-board",
        "engine-comparison": "experiment-validation-board",
        "latency-slo": "strategy-trace-board",
        "statistical-signal-migration": "strategy-rulebox-editor",
        "operational-soak": "experiment-validation-board",
        "compliance-review": "settings-diagnostics",
    }
    for gate in gates:
        gate["detailTarget"] = gate_targets.get(str(gate.get("id") or ""), "investment-model-overview")
    closed_beta_blockers = [
        item for item in gates
        if item["requiredForClosedBeta"] and not item["passed"]
    ]
    ga_blockers = [
        item for item in gates
        if item["requiredForGeneralAvailability"] and not item["passed"]
    ]
    if not ga_blockers:
        stage = "general-availability-candidate"
    elif not closed_beta_blockers:
        stage = "closed-beta-candidate"
    else:
        stage = "internal-validation"
    return {
        "version": INVESTMENT_PRODUCT_READINESS_VERSION,
        "stage": stage,
        "stageLabel": {
            "internal-validation": "내부 검증",
            "closed-beta-candidate": "비공개 베타 후보",
            "general-availability-candidate": "정식 출시 후보",
        }[stage],
        "closedBetaReady": not closed_beta_blockers,
        "generalAvailabilityReady": not ga_blockers,
        "releaseRecommended": not ga_blockers,
        "gates": gates,
        "blockers": [item["id"] for item in ga_blockers],
        "metrics": {
            "calibrationEligibleEpisodeCount": calibration_episodes,
            "outcomeCoveragePct": outcome_coverage,
            "quarantineRecommendedRuleCount": len(quarantine_rule_ids),
            "quarantineRecommendedRuleIds": quarantine_rule_ids[:30],
            "comparisonSampleCount": comparison_samples,
            "p95TotalDurationMs": p95_ms,
            "statisticalSignalMigrationRemaining": migration_remaining,
            "activeExperimentCount": _count(experiments.get("activeCount")),
        },
        "automaticPromotion": False,
    }
