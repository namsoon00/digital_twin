import { renderAccountControlMetric } from "../accounts/balance.mjs";
import { activeOntologyAccountId } from "../ontology/requests.mjs";
import { loadPortfolioInterpretation, loadPortfolioReadModel } from "./requests.mjs";
import { render } from "../render/scheduler.mjs";
import { requestJson } from "../requests/json.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { formatClock, formatMoney } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { portfolioState } from "../state/portfolio.mjs";
import { shellState } from "../state/shell.mjs";

function portfolioLifecycleAccountId() {
  var snapshot = shellState.snapshot || {};
  return String(snapshot.accountId || ((snapshot.toss || {}).accountId || "") || activeOntologyAccountId() || "default").trim();
}

function loadPortfolioLifecycle(force) {
  if (isStaticPreviewHost()) return Promise.resolve(portfolioState.portfolioLifecycle);
  if (portfolioState.portfolioLifecycleLoading && !force) return Promise.resolve(portfolioState.portfolioLifecycle);
  portfolioState.portfolioLifecycleLoading = true;
  portfolioState.portfolioLifecycleError = "";
  var params = new URLSearchParams();
  params.set("accountId", portfolioLifecycleAccountId());
  return requestJson("/api/portfolio-lifecycle?" + params.toString(), { key: "portfolio-lifecycle", force: Boolean(force) })
    .then(function (payload) {
      portfolioState.portfolioLifecycle = payload || {};
      return portfolioState.portfolioLifecycle;
    })
    .catch(function (error) {
      portfolioState.portfolioLifecycleError = error.message || "계좌 의사결정 주기를 읽지 못했습니다.";
      return null;
    })
    .finally(function () {
      portfolioState.portfolioLifecycleLoading = false;
      if (shellState.snapshot) render();
    });
}

function reviewPortfolioActionPlan(planId, decision) {
  if (!planId || portfolioState.portfolioLifecycleSaving) return Promise.resolve(null);
  portfolioState.portfolioLifecycleSaving = true;
  render();
  return sendJson("/api/action-plans/" + encodeURIComponent(planId) + "/" + decision, "POST", {
    reviewer: "web-owner",
    reason: decision === "approve" ? "웹 계좌 라이프사이클 검토 승인" : "웹 계좌 라이프사이클 검토 거절"
  }).then(function (payload) {
    var errors = Array.isArray(payload.validationErrors) ? payload.validationErrors : [];
    showSnackbar(errors.length ? "현재 계좌 검증에서 계획이 거절되었습니다." : "실행 계획 검토 결과를 저장했습니다.", errors.length ? "danger" : "success");
    portfolioState.portfolioReadModels = {};
    portfolioState.portfolioInterpretation = null;
    return Promise.all([
      loadPortfolioLifecycle(true),
      loadPortfolioReadModel(portfolioState.activePortfolioView, true),
      loadPortfolioInterpretation(true)
    ]);
  }).catch(function (error) {
    portfolioState.portfolioLifecycleError = error.message || "실행 계획 검토에 실패했습니다.";
    showSnackbar(portfolioState.portfolioLifecycleError, "danger");
    return null;
  }).finally(function () {
    portfolioState.portfolioLifecycleSaving = false;
    render();
  });
}

function lifecyclePercent(value) {
  var parsed = Number(value || 0);
  return (parsed >= 0 ? "+" : "") + parsed.toFixed(1) + "%";
}

function lifecycleActionLabel(value) {
  return ({
    NO_ACTION: "현재 상태 유지",
    REDUCE_POSITION_EXPOSURE: "종목 비중 축소 범위 검토",
    RESTORE_CASH_FLOOR: "현금 하한 회복 검토",
    INCREASE_UNDERWEIGHT_ALLOCATION: "목표 배분 하단 복원 검토",
    REDUCE_PORTFOLIO_RISK: "포트폴리오 위험 축소 검토"
  })[String(value || "").toUpperCase()] || String(value || "후보");
}

function lifecycleActivityLabel(activity) {
  return ({
    "new-position": "신규 보유 감지",
    "position-increase": "보유 수량 증가",
    "position-decrease": "보유 수량 감소",
    "position-exit": "보유 종료",
    "possible-corporate-action": "기업행동 가능성",
    "unclassified-cash-balance-change": "현금 잔액 변화"
  })[String((activity || {}).classification || "")] || "계좌 잔고 변화";
}

function renderPortfolioLifecyclePanel(snapshot) {
  var lifecycle = portfolioState.portfolioLifecycle || {};
  var reconciliation = lifecycle.reconciliation || {};
  var exposure = lifecycle.exposureSnapshot || {};
  var risk = lifecycle.portfolioRiskSnapshot || {};
  var rebalance = lifecycle.rebalanceProposal || {};
  var cycle = lifecycle.portfolioDecisionCycle || {};
  var checkpoint = lifecycle.snapshotCheckpoint || {};
  var activities = Array.isArray(lifecycle.recentInferredActivities) ? lifecycle.recentInferredActivities : [];
  var activityEpisodes = Array.isArray(lifecycle.recentActivityEpisodes) ? lifecycle.recentActivityEpisodes : [];
  var portfolioState = lifecycle.portfolioState || {};
  var positionStates = Array.isArray(portfolioState.positions) ? portfolioState.positions : [];
  var actionObservations = Array.isArray(lifecycle.decisionActionObservations) ? lifecycle.decisionActionObservations : [];
  var snapshotQuarantines = Array.isArray(lifecycle.recentSnapshotQuarantines) ? lifecycle.recentSnapshotQuarantines : [];
  var candidates = Array.isArray(cycle.candidates) ? cycle.candidates : [];
  var plans = Array.isArray(lifecycle.actionPlans) ? lifecycle.actionPlans : [];
  var scenarios = Array.isArray(rebalance.scenarios) ? rebalance.scenarios : [];
  var executions = Array.isArray(lifecycle.executionEpisodes) ? lifecycle.executionEpisodes : [];
  var fills = Array.isArray(lifecycle.fills) ? lifecycle.fills : [];
  var attributions = Array.isArray(lifecycle.performanceAttributions) ? lifecycle.performanceAttributions : [];
  var quality = lifecycle.decisionQualitySummary || {};
  var metrics = Array.isArray(exposure.metrics) ? exposure.metrics : [];
  return [
    '<article class="panel portfolio-lifecycle-panel">',
    '<div class="panel-head"><div><p class="label">Portfolio Lifecycle</p><h2>계좌 의사결정 주기</h2><p class="subtle">원장 사실부터 정책 후보, 승인 계획, 실제 결과까지 같은 계좌 기준으로 추적합니다.</p></div>',
    '<button class="text-button" type="button" data-portfolio-lifecycle-refresh>새로고침</button></div>',
    portfolioState.portfolioLifecycleLoading ? '<div class="rule-strip"><span>계좌 라이프사이클을 읽는 중입니다.</span></div>' : '',
    portfolioState.portfolioLifecycleError ? '<p class="form-error">' + escapeHtml(portfolioState.portfolioLifecycleError) + '</p>' : '',
    '<div class="portfolio-lifecycle-metrics">',
    renderAccountControlMetric("원장 대사", reconciliation.status || "대기", (reconciliation.differences || []).length + "개 차이", reconciliation.status === "matched" ? "ok" : "warn"),
    renderAccountControlMetric("최근 변화 묶음", activityEpisodes.length + "건", "스냅샷별 원장 활동", activityEpisodes.length ? "ok" : "neutral"),
    renderAccountControlMetric("정책 후보", candidates.length + "개", cycle.dataState || "자료 대기", candidates.length ? "neutral" : "warn"),
    renderAccountControlMetric("연환산 변동성", Number(risk.sampleCount || 0) ? lifecyclePercent(risk.annualizedVolatilityPct) : "자료 대기", "표본 " + Number(risk.sampleCount || 0) + "개", Number(risk.volatilityPolicyDeltaPct || 0) > 0 ? "warn" : "neutral"),
    renderAccountControlMetric("최대 낙폭", Number(risk.sampleCount || 0) ? lifecyclePercent(risk.maximumDrawdownPct) : "자료 대기", "상관 최대 " + Number(risk.maximumPairwiseCorrelation || 0).toFixed(2), Number(risk.drawdownPolicyDeltaPct || 0) > 0 ? "warn" : "neutral"),
    renderAccountControlMetric("실행 계획", plans.length + "개", "자동 주문 없음", plans.length ? "neutral" : "warn"),
    renderAccountControlMetric("체크포인트", "v" + Number(checkpoint.checkpointVersion || 0), snapshotQuarantines.length ? "최근 검역 " + snapshotQuarantines.length + "건" : (formatClock(checkpoint.observedAt) || "기준선 대기"), snapshotQuarantines.length ? "warn" : "neutral"),
    renderAccountControlMetric("성과 표본", attributions.length + "건", "1h · 1d · 5d · 20d", attributions.length ? "ok" : "neutral"),
    '</div>',
    '<div class="portfolio-lifecycle-columns">',
    '<section class="portfolio-lifecycle-section"><div class="account-board-title"><strong>정책 노출과 후보</strong><span>후보는 산술 범위이며 최종 매수·매도 판단이 아닙니다.</span></div>',
    '<div class="portfolio-lifecycle-list">',
    metrics.length ? metrics.slice(0, 12).map(function (metric) {
      var ratio = metric.ratio_pct == null ? metric.ratioPct : metric.ratio_pct;
      var overPolicy = Number(metric.policyDeltaPct || 0) > 0 && Number(metric.policy_limit_pct || metric.policyLimitPct || 0) > 0;
      return '<div><strong>' + escapeHtml(metric.label || metric.key || metric.exposure_type || metric.exposureType || "노출") + '</strong><span>' + escapeHtml(lifecyclePercent(ratio)) + '</span><em>' + escapeHtml(overPolicy ? "정책 범위 초과" : "정책 범위 안") + '</em></div>';
    }).join("") : '<p class="subtle">노출 스냅샷이 아직 없습니다.</p>',
    candidates.map(function (candidate) {
      return '<div><strong>' + escapeHtml(lifecycleActionLabel(candidate.candidate_type || candidate.candidateType)) + '</strong><span>' + escapeHtml(candidate.affected_symbol || candidate.affectedSymbol || "계좌 전체") + '</span><em>최대 ' + escapeHtml(formatMoney(candidate.maximum_notional || candidate.maximumNotional || 0)) + '</em></div>';
    }).join(""),
    '</div></section>',
    '<section class="portfolio-lifecycle-section"><div class="account-board-title"><strong>검토할 실행 계획</strong><span>승인 시 현재 시세·현금·정책을 다시 검사합니다.</span></div>',
    '<div class="portfolio-action-plan-list">',
    plans.length ? plans.map(function (plan) {
      var envelope = plan.envelope || {};
      var status = String(plan.status || "review-required");
      return [
        '<div class="portfolio-action-plan">',
        '<div><strong>' + escapeHtml([envelope.symbol, plan.action].filter(Boolean).join(" · ") || "실행 계획") + '</strong><span class="status-pill ' + escapeHtml(status === "approved" ? "ok" : status === "rejected" || status === "blocked" ? "warn" : "neutral") + '">' + escapeHtml(status) + '</span></div>',
        '<p>분할 ' + escapeHtml((plan.slices || []).length) + '회 · 만료 ' + escapeHtml(formatClock(plan.expires_at || plan.expiresAt)) + '</p>',
        status === "review-required" ? '<div class="portfolio-plan-actions"><button type="button" class="mini-button primary" data-action-plan-review="approve" data-action-plan-id="' + escapeHtml(plan.plan_id || plan.planId) + '">승인</button><button type="button" class="mini-button" data-action-plan-review="reject" data-action-plan-id="' + escapeHtml(plan.plan_id || plan.planId) + '">거절</button></div>' : '',
        '</div>'
      ].join("");
    }).join("") : '<p class="subtle">검토할 실행 계획이 없습니다.</p>',
    '</div></section>',
    '</div>',
    '<div class="portfolio-lifecycle-columns">',
    '<section class="portfolio-lifecycle-section"><div class="account-board-title"><strong>리밸런싱 시나리오</strong><span>거래 비용과 회전율을 포함한 비교 후보</span></div>',
    '<div class="portfolio-lifecycle-list">',
    scenarios.length ? scenarios.map(function (scenario) {
      return '<div><strong>' + escapeHtml(scenario.label || scenario.scenario_type || scenario.scenarioType || "시나리오") + '</strong><span>회전율 ' + escapeHtml(lifecyclePercent(scenario.turnover_pct == null ? scenario.turnoverPct : scenario.turnover_pct)) + '</span><em>예상 비용 ' + escapeHtml(formatMoney(Number(scenario.estimated_cost == null ? scenario.estimatedCost : scenario.estimated_cost))) + ' · ' + escapeHtml(scenario.data_state || scenario.dataState || "partial") + '</em></div>';
    }).join("") : '<p class="subtle">정책 이탈이나 위험 초과가 없어 비교 시나리오가 없습니다.</p>',
    '</div></section>',
    '<section class="portfolio-lifecycle-section"><div class="account-board-title"><strong>실제 실행과 체결</strong><span>승인된 계획에 연결된 공급자 체결만 표시</span></div>',
    '<div class="portfolio-lifecycle-list">',
    executions.length ? executions.map(function (execution) {
      var episodeFills = Array.isArray(execution.fills) ? execution.fills : [];
      return '<div><strong>' + escapeHtml(execution.status || "실행") + '</strong><span>체결 ' + episodeFills.length + '건</span><em>' + escapeHtml(formatClock(execution.completedAt || execution.completed_at || execution.startedAt || execution.started_at)) + '</em></div>';
    }).join("") : '<p class="subtle">실제 주문 제출은 꺼져 있으며 기록된 체결이 없습니다.</p>',
    fills.slice(0, 10).map(function (fill) {
      return '<div><strong>' + escapeHtml((fill.symbol || "종목") + " · " + (fill.side || "")) + '</strong><span>' + escapeHtml(String(fill.quantity || 0) + "주 @ " + formatMoney(Number(fill.price || 0))) + '</span><em>수수료 ' + escapeHtml(formatMoney(Number(fill.fee || 0))) + '</em></div>';
    }).join(""),
    '</div></section>',
    '</div>',
    '<section class="portfolio-lifecycle-section"><div class="account-board-title"><strong>판단 품질</strong><span>완전한 사후 표본만 초과수익 통계에 사용</span></div>',
    '<div class="portfolio-lifecycle-list">',
    '<div><strong>표본 상태</strong><span>전체 ' + Number(quality.sampleCount || 0) + '건 · 완전 ' + Number(quality.completeSampleCount || 0) + '건</span><em>' + escapeHtml(quality.dataState || "partial") + '</em></div>',
    '<div><strong>벤치마크 대비</strong><span>평균 ' + escapeHtml(lifecyclePercent(quality.meanActiveReturnPct)) + '</span><em>양의 초과수익 ' + escapeHtml(lifecyclePercent(quality.positiveActiveRatePct)) + '</em></div>',
    '<div><strong>수명주기 준수</strong><span>실행 ' + escapeHtml(lifecyclePercent(quality.executionComplianceRatePct)) + '</span><em>근거 유효 ' + escapeHtml(lifecyclePercent(quality.evidenceValidityRatePct)) + '</em></div>',
    '</div></section>',
    '<section class="portfolio-lifecycle-section portfolio-activity-history"><div class="account-board-title"><strong>최근 보유 변화</strong><span>실계좌 전체 잔고의 전후 차이</span></div>',
    '<div class="portfolio-lifecycle-list">',
    activities.length ? activities.map(function (activity) {
      var symbol = activity.symbol || (activity.currency ? activity.currency + " 현금" : "계좌");
      var quantity = activity.cashDelta
        ? "증감 " + formatMoney(Number(activity.cashDelta || 0))
        : String(activity.previousQuantity || "0") + " → " + String(activity.observedQuantity || "0");
      var confidence = activity.confidence === "low" ? "확인 필요" : "잔고 기준";
      return '<div><strong>' + escapeHtml(symbol + " · " + lifecycleActivityLabel(activity)) + '</strong><span>' + escapeHtml(quantity) + '</span><em>' + escapeHtml(confidence + " · " + formatClock(activity.currentSnapshotAt || activity.occurredAt)) + '</em></div>';
    }).join("") : '<p class="subtle">기준선 이후 감지된 보유 변화가 없습니다.</p>',
    '</div></section>',
    '<div class="portfolio-lifecycle-columns">',
    '<section class="portfolio-lifecycle-section"><div class="account-board-title"><strong>활동 에피소드</strong><span>같은 스냅샷에서 확인된 종목·현금 변화를 한 묶음으로 표시</span></div>',
    '<div class="portfolio-lifecycle-list">',
    activityEpisodes.length ? activityEpisodes.map(function (episode) {
      var labels = {
        "probable-buy": "보유 증가·현금 감소 동시 관측",
        "probable-sell": "보유 감소·현금 증가 동시 관측",
        "position-balance-change": "보유 수량 변화",
        "cash-balance-change": "현금 잔액 변화",
        "possible-corporate-action": "기업행동 가능성",
        "mixed-portfolio-change": "복합 계좌 변화"
      };
      var target = (episode.symbols || []).join(", ") || "현금";
      return '<div><strong>' + escapeHtml(target + " · " + (labels[episode.classification] || episode.classification || "계좌 변화")) + '</strong><span>현금 증감 ' + escapeHtml(formatMoney(Number(episode.cashDelta || 0))) + '</span><em>' + escapeHtml((episode.confidence || "low") + " · " + formatClock(episode.observedAt)) + '</em></div>';
    }).join("") : '<p class="subtle">기준선 이후 묶을 계좌 변화가 없습니다.</p>',
    '</div></section>',
    '<section class="portfolio-lifecycle-section"><div class="account-board-title"><strong>원장 기반 보유 상태</strong><span>보유 기간과 최근 증감 이력</span></div>',
    '<div class="portfolio-lifecycle-list">',
    positionStates.length ? positionStates.map(function (item) {
      var activity = "20일 증가 " + Number(item.increaseCount20d || 0) + "회 · 감소 " + Number(item.decreaseCount20d || 0) + "회";
      return '<div><strong>' + escapeHtml((item.name || item.symbol || "종목") + (item.reentered ? " · 재진입" : "")) + '</strong><span>' + escapeHtml("보유 " + Number(item.holdingDays || 0) + "일 · 비중 " + Number(item.currentWeightPct || 0).toFixed(1) + "%") + '</span><em>' + escapeHtml(activity) + '</em></div>';
    }).join("") : '<p class="subtle">원장 기반 보유 상태가 아직 없습니다.</p>',
    '</div></section>',
    '</div>',
    '<section class="portfolio-lifecycle-section"><div class="account-board-title"><strong>판단 이후 계좌 변화</strong><span>이전 AI 판단과 이후 실계좌 방향 비교 · 인과관계는 주장하지 않음</span></div>',
    '<div class="portfolio-lifecycle-list">',
    actionObservations.length ? actionObservations.map(function (item) {
      var relationLabel = item.correspondence === "aligned" ? "같은 방향" : item.correspondence === "contrary" ? "반대 방향" : "직접 비교 불가";
      return '<div><strong>' + escapeHtml((item.symbol || "종목") + " · " + relationLabel) + '</strong><span>' + escapeHtml((item.priorAction || "이전 판단 없음") + " → 계좌 " + (item.observedDirection || "변화")) + '</span><em>' + escapeHtml(Number(item.elapsedMinutes || 0) + "분 후 관측 · " + (item.priorDecisionEpisodeId || "판단 기록 없음")) + '</em></div>';
    }).join("") : '<p class="subtle">이전 AI 판단과 연결할 계좌 변화가 없습니다.</p>',
    '</div></section>',
    '<section class="portfolio-lifecycle-section"><div class="account-board-title"><strong>스냅샷 검역</strong><span>기준선을 덮어쓰지 않은 비정상 계좌 응답</span></div>',
    '<div class="portfolio-lifecycle-list">',
    snapshotQuarantines.length ? snapshotQuarantines.map(function (item) {
      return '<div><strong>' + escapeHtml(item.reason || item.quarantineReason || "스냅샷 검역") + '</strong><span>' + escapeHtml("보유 " + Number(item.positionCount || 0) + "개 · 현금 " + formatMoney(Number(item.cashBalance || 0))) + '</span><em>' + escapeHtml(formatClock(item.observedAt)) + '</em></div>';
    }).join("") : '<p class="subtle">검역된 계좌 스냅샷이 없습니다.</p>',
    '</div></section>',
    '</article>'
  ].join("");
}

export { loadPortfolioLifecycle, renderPortfolioLifecyclePanel, reviewPortfolioActionPlan };
