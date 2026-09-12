import { escapeHtml } from "../shared/text.mjs";

const reasons = {
  "isolated-release-required": "운영과 분리된 실험 버전을 준비하고 있습니다.",
  "independent-outcomes-required": "같은 시점의 기존 가설과 새 가설을 비교할 독립된 결과가 더 필요합니다.",
  "paired-holdout-and-runtime-checks-passed": "사후 결과 비교와 실행 검증을 통과해 자동 반영했습니다.",
  "runtime-readiness-required": "투자 결과 비교는 통과했으며, 실제 추론 실행과 입력 정합성을 확인 중입니다.",
  "another-candidate-or-rollback-release-in-use": "다른 실험 또는 복원 버전이 사용 중입니다. 운영 알림은 계속 실행됩니다.",
  "evolution-dependency-error": "연결이나 실행 오류가 발생했습니다. 같은 실험 버전으로 다시 확인합니다.",
  "baseline-or-candidate-replaced": "운영 기준 또는 실험 버전이 바뀌어 이 비교를 종료했습니다.",
  "not-better-than-baseline": "사전에 정한 비교 구간에서 기존 가설보다 나아지지 않아 반영하지 않았습니다.",
  "observation-window-expired": "정해진 관측 기간이 끝나 실험을 종료했습니다.",
  "candidate-start-window-expired": "대기 기간 안에 실험을 시작하지 못해 종료했습니다.",
  "adopted-release-under-observation": "운영 반영 이후의 새 결과를 따로 확인하고 있습니다.",
  "post-adoption-cohort-no-regression": "반영 후 검증 구간에서 복원 기준에 해당하는 악화가 확인되지 않았습니다.",
  "forward-regression": "반영 후 성과가 악화되어 이전 버전으로 복원했습니다.",
  "operational-failure": "실행 계약 오류가 확인되어 이전 버전으로 복원했습니다.",
  "shadow-policy-no-deployment": "실험 전용 정책이므로 검증을 통과해도 운영에 반영하지 않습니다.",
  "evolution-disabled-by-operator": "자동 진화가 중지된 상태입니다.",
  "external-validation-required": "자동 비교로 확인할 수 없는 별도 연구 조건이 남아 반영하지 않았습니다.",
  "post-adoption-window-expired": "운영 반영 후 정해진 기간 안에 검증을 마치지 못해 이전 버전으로 복원했습니다.",
};

export function renderOntologyEvolution(evolution = {}, formatClock = value => String(value || "")) {
  const plan = evolution.plan;
  if (!plan) return "";
  const policy = plan.policy || {};
  const assessment = evolution.monitoring || evolution.assessment || {};
  const pendingReview = (plan.validationRequirements || []).some(row => row.check === "review");
  const mode = { automatic: "검증 후 자동 반영", shadow: "실험만 실행", disabled: "중지" }[policy.mode] || "확인 필요";
  return '<section class="hypothesis-development-retry ontology-evolution-status" aria-label="온톨로지 진화 상태">' +
    '<header><strong>독립 결과 검증</strong><span>' + escapeHtml(mode) + '</span></header>' +
    '<p>' + escapeHtml(reasons[evolution.reason] || "실험 결과를 확인하고 있습니다.") + '</p>' +
    '<dl>' +
    '<div><dt>기존 버전</dt><dd>' + escapeHtml(plan.baseline?.deploymentId || "-") + '</dd></div>' +
    '<div><dt>실험 버전</dt><dd>' + escapeHtml(evolution.deployment?.deploymentId || "준비 중") + '</dd></div>' +
    '<div><dt>예측 결과 확인 시점</dt><dd>' + escapeHtml(plan.baseline?.comparisonHorizonMinutes ?? "-") + '분 후</dd></div>' +
    '<div><dt>비교 가능한 독립 관측</dt><dd>' + escapeHtml(assessment.independentPairCount ?? 0) + ' / ' + escapeHtml(policy.minimumIndependentPairs ?? "-") + '건</dd></div>' +
    '<div><dt>관측일</dt><dd>' + escapeHtml(assessment.distinctDayCount ?? 0) + ' / ' + escapeHtml(policy.minimumDistinctDays ?? "-") + '일</dd></div>' +
    '<div><dt>새 가설 우세 / 기존 가설 우세</dt><dd>' + escapeHtml(assessment.pairedGainCount ?? "-") + ' / ' + escapeHtml(assessment.pairedLossCount ?? "-") + '</dd></div>' +
    '<div><dt>비교에서 제외한 자료</dt><dd>' + escapeHtml(assessment.excludedCount ?? 0) + '건</dd></div>' +
    '<div><dt>후보 고정 시각</dt><dd>' + escapeHtml(formatClock(plan.createdAt)) + '</dd></div>' +
    '<div><dt>운영 반영 시각</dt><dd>' + escapeHtml(evolution.adoptedAt ? formatClock(evolution.adoptedAt) : "반영 전") + '</dd></div>' +
    '</dl>' +
    (pendingReview ? '<p class="form-error">자동 결과 비교로 검증할 수 없는 별도 연구 조건이 남아 있습니다.</p>' : '') +
    '<details><summary>검증 기준과 추적</summary><p>정책 ' + escapeHtml(policy.version || "-") +
    ' · 최대 관측 기간 ' + escapeHtml(policy.maximumShadowDays ?? "-") + '일</p>' +
    '<p>비교 기준 ' + escapeHtml(plan.baseline?.comparisonRuleId || "-") + '</p>' +
    '<p>변경 식별자 ' + escapeHtml(plan.fingerprint || "-") + '</p>' +
    (evolution.details?.error ? '<p class="form-error">' + escapeHtml(evolution.details.error) + '</p>' : '') +
    '</details></section>';
}
