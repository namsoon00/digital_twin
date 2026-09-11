import { renderReasoningCardList } from "../decisions/evidence.mjs";
import { renderInvestmentTodayStatusCell } from "../decisions/today.mjs";
import { renderOntologyExperimentMetric } from "../experiments/workspace.mjs";
import { normalizeStrategyProposalSection } from "../navigation/routes.mjs";
import { strategyProposalActionDisabled } from "./requests.mjs";
import { formatClock, latestChangedFirst, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { proposalsState } from "../state/proposals.mjs";

function strategyProposalPayload() {
  return proposalsState.strategyProposals && typeof proposalsState.strategyProposals === "object"
    ? proposalsState.strategyProposals
    : { proposals: [], count: 0, summary: { statuses: {} } };
}

function strategyProposalItems() {
  var payload = strategyProposalPayload();
  return latestChangedFirst(Array.isArray(payload.proposals) ? payload.proposals : []);
}

function strategyProposalSummary() {
  var payload = strategyProposalPayload();
  return payload.summary && typeof payload.summary === "object" ? payload.summary : {};
}

function strategyProposalById(proposalId) {
  var target = String(proposalId || "");
  return strategyProposalItems().filter(function (item) {
    return String(item.id || "") === target;
  })[0] || null;
}

function activeStrategyProposal() {
  return strategyProposalById(proposalsState.activeStrategyProposalId) || strategyProposalItems()[0] || null;
}

function strategyProposalStatusLabel(status) {
  var value = String(status || "").toLowerCase();
  if (value === "proposed") return "제안";
  if (value === "validated") return "검증됨";
  if (value === "approved") return "승인됨";
  if (value === "deployed") return "운영 반영";
  if (value === "retired") return "폐기";
  return value || "대기";
}

function strategyProposalStatusTone(status) {
  var value = String(status || "").toLowerCase();
  if (value === "approved" || value === "deployed") return "watch";
  if (value === "validated") return "watch";
  if (value === "proposed") return "hold";
  if (value === "retired") return "hold";
  return "caution";
}

function strategyProposalMetricValue(value, suffix) {
  if (value == null || value === "") return "-";
  var number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + (suffix || "");
}

function strategyProposalSignedPercent(value) {
  if (value == null || value === "") return "-";
  var number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return (number > 0 ? "+" : "") + number.toFixed(Math.abs(number) >= 10 ? 0 : 2) + "%";
}

function strategyProposalArray(value) {
  return Array.isArray(value) ? value : [];
}

function strategyProposalValidation(proposal) {
  var validation = proposal && proposal.validation && typeof proposal.validation === "object" ? proposal.validation : {};
  return validation.materialization && typeof validation.materialization === "object" ? validation.materialization : validation;
}

function strategyProposalReviewLog(proposal) {
  var lifecycle = proposal && proposal.lifecycle && typeof proposal.lifecycle === "object" ? proposal.lifecycle : {};
  return Array.isArray(lifecycle.reviewLog) ? lifecycle.reviewLog : [];
}

function strategyProposalPerformanceSummary(proposal) {
  var performance = proposal && proposal.performance && typeof proposal.performance === "object" ? proposal.performance : {};
  return performance.summary && typeof performance.summary === "object" ? performance.summary : {};
}

function strategyProposalBusy(action, proposalId) {
  return proposalsState.strategyProposalAction === action + ":" + String(proposalId || "");
}

function renderStrategyProposalConsolePanel() {
  var payload = strategyProposalPayload();
  var summary = strategyProposalSummary();
  var statuses = summary.statuses && typeof summary.statuses === "object" ? summary.statuses : {};
  var items = strategyProposalItems();
  var active = activeStrategyProposal();
  var waitingCount = Number(statuses.proposed || 0) + Number(statuses.validated || 0);
  return [
    '<article class="panel strategy-proposal-console-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Strategy Proposals</p>',
    '<h2>전략 제안 승인 큐</h2>',
    '<p class="subtle">온톨로지 실험과 AI 후보가 만든 전략 가설을 검증, 승인, 성과 표본으로 추적합니다.</p>',
    '</div>',
    '<div class="settings-actions strategy-proposal-actions">',
    '<span class="tone-chip ' + escapeHtml(waitingCount ? "caution" : "hold") + '">' + escapeHtml(waitingCount ? waitingCount + "건 검토" : "대기 없음") + '</span>',
    '<button class="text-button" type="button" data-action="refresh-strategy-proposals"' + (proposalsState.strategyProposalsLoading ? ' disabled' : '') + '>' + escapeHtml(proposalsState.strategyProposalsLoading ? "조회 중" : "새로고침") + '</button>',
    '</div>',
    '</div>',
    renderStrategyProposalStatusRail(payload, summary, statuses, items),
    proposalsState.strategyProposalsError ? '<p class="form-error">' + escapeHtml(proposalsState.strategyProposalsError) + '</p>' : '',
    proposalsState.strategyProposalsLoading && !proposalsState.strategyProposalsLoaded ? '<div class="rule-strip"><span>전략 제안 목록을 읽는 중입니다.</span></div>' : '',
    '<div class="strategy-proposal-layout">',
    renderStrategyProposalList(items, active),
    renderStrategyProposalDetail(active),
    '</div>',
    '</article>'
  ].join("");
}

function renderStrategyProposalStatusRail(payload, summary, statuses, items) {
  return [
    '<div class="work-detail-metric-row strategy-proposal-summary">',
    renderOntologyExperimentMetric("전체", payload.count == null ? items.length : payload.count, "proposals"),
    renderOntologyExperimentMetric("제안", summary.proposedCount || statuses.proposed || 0, "proposed"),
    renderOntologyExperimentMetric("검증", summary.validatedCount || statuses.validated || 0, "validated"),
    renderOntologyExperimentMetric("승인·운영", Number(summary.approvedCount || statuses.approved || 0) + Number(summary.deployedCount || statuses.deployed || 0), "approved"),
    '</div>'
  ].join("");
}

function renderStrategyProposalList(items, active) {
  if (!items.length) {
    return [
      '<section class="strategy-proposal-list">',
      '<div class="strategy-proposal-empty">',
      '<strong>등록된 전략 제안이 없습니다.</strong>',
      '<span>온톨로지 실험 제안이나 RuleBox 후보가 저장되면 이 큐에 표시됩니다.</span>',
      '</div>',
      '</section>'
    ].join("");
  }
  return [
    '<section class="strategy-proposal-list" aria-label="전략 제안 목록">',
    items.map(function (proposal) {
      var id = proposal.id || "";
      var activeClass = active && active.id === id ? " active" : "";
      var symbols = strategyProposalArray(proposal.symbols);
      var ruleIds = strategyProposalArray(proposal.ruleIds);
      var meta = [
        symbols.slice(0, 4).join(", "),
        ruleIds.length ? ruleIds.length + "개 룰" : ""
      ].filter(Boolean).join(" · ");
      return [
        '<button class="strategy-proposal-card' + activeClass + '" type="button" data-strategy-proposal-select="' + escapeHtml(id) + '">',
        '<span class="tone-chip ' + escapeHtml(strategyProposalStatusTone(proposal.status)) + '">' + escapeHtml(strategyProposalStatusLabel(proposal.status)) + '</span>',
        '<strong>' + escapeHtml(proposal.title || id || "전략 제안") + '</strong>',
        '<em>' + escapeHtml(meta || "세부 정보 대기") + '</em>',
        renderRecordChangedAt(proposal),
        '</button>'
      ].join("");
    }).join(""),
    '</section>'
  ].join("");
}

function renderStrategyProposalDetail(proposal) {
  if (!proposal) {
    return [
      '<section class="strategy-proposal-detail">',
      '<div class="strategy-proposal-empty">',
      '<strong>선택된 전략 제안이 없습니다.</strong>',
      '<span>목록에서 제안을 선택하면 검증 결과와 승인 이력이 표시됩니다.</span>',
      '</div>',
      '</section>'
    ].join("");
  }
  var id = proposal.id || "";
  var validation = strategyProposalValidation(proposal);
  var diff = validation.diff && typeof validation.diff === "object" ? validation.diff : {};
  var reviewLog = strategyProposalReviewLog(proposal);
  var performance = proposal.performance && typeof proposal.performance === "object" ? proposal.performance : {};
  var samples = Array.isArray(performance.samples) ? performance.samples : [];
  var disabled = strategyProposalActionDisabled();
  var approvable = ["proposed", "validated", "approved"].indexOf(String(proposal.status || "")) >= 0;
  var section = normalizeStrategyProposalSection(proposalsState.activeStrategyProposalSection);
  proposalsState.activeStrategyProposalSection = section;
  return [
    '<section class="strategy-proposal-detail">',
    '<div class="strategy-proposal-detail-head">',
    '<div>',
    '<span class="tone-chip ' + escapeHtml(strategyProposalStatusTone(proposal.status)) + '">' + escapeHtml(strategyProposalStatusLabel(proposal.status)) + '</span>',
    '<h3>' + escapeHtml(proposal.title || "전략 제안") + '</h3>',
    '<p>' + escapeHtml(proposal.thesis || "전략 가설 설명이 아직 없습니다.") + '</p>',
    '</div>',
    '<div class="strategy-proposal-detail-actions">',
    '<button class="text-button" type="button" data-strategy-proposal-validate="' + escapeHtml(id) + '"' + (disabled ? ' disabled' : '') + '>' + escapeHtml(strategyProposalBusy("validate", id) ? "검증 중" : "검증 실행") + '</button>',
    '<button class="text-button primary" type="button" data-strategy-proposal-approve="' + escapeHtml(id) + '"' + (disabled || !approvable ? ' disabled' : '') + '>' + escapeHtml(strategyProposalBusy("approve", id) ? "승인 중" : "승인") + '</button>',
    '</div>',
    '</div>',
    '<div class="work-detail-metric-row strategy-proposal-detail-metrics">',
    renderOntologyExperimentMetric("상태", strategyProposalStatusLabel(proposal.status), "status"),
    renderOntologyExperimentMetric("종목", strategyProposalArray(proposal.symbols).length || "-", "symbols"),
    renderOntologyExperimentMetric("룰", strategyProposalArray(proposal.ruleIds).length || "-", "rules"),
    renderOntologyExperimentMetric("갱신", proposal.updatedAt ? formatClock(proposal.updatedAt) : "-", "updated"),
    '</div>',
    renderStrategyProposalSectionTabs(section),
    '<div class="strategy-proposal-section-body">',
    renderStrategyProposalSectionContent(section, proposal, validation, diff, reviewLog, samples, disabled),
    '</div>',
    '</section>'
  ].join("");
}

function strategyProposalSectionItems() {
  return [
    { id: "summary", label: "요약", caption: "가설·상태" },
    { id: "conditions", label: "조건", caption: "진입·청산" },
    { id: "validation", label: "검증", caption: "TypeDB" },
    { id: "performance", label: "성과", caption: "표본 기록" },
    { id: "history", label: "이력", caption: "승인 로그" }
  ];
}

function renderStrategyProposalSectionTabs(activeSection) {
  return [
    '<div class="strategy-proposal-section-tabs" role="tablist" aria-label="전략 제안 상세 섹션">',
    strategyProposalSectionItems().map(function (item) {
      var active = item.id === activeSection;
      return [
        '<button type="button" role="tab" class="' + escapeHtml(active ? "active" : "") + '" data-strategy-proposal-section="' + escapeHtml(item.id) + '"' + (active ? ' aria-selected="true"' : ' aria-selected="false"') + '>',
        '<strong>' + escapeHtml(item.label) + '</strong>',
        '<span>' + escapeHtml(item.caption) + '</span>',
        '</button>'
      ].join("");
    }).join(""),
    '</div>'
  ].join("");
}

function renderStrategyProposalSectionContent(section, proposal, validation, diff, reviewLog, samples, disabled) {
  if (section === "conditions") return renderStrategyProposalConditionGrid(proposal);
  if (section === "validation") return renderStrategyProposalValidationPanel(validation, diff);
  if (section === "performance") return renderStrategyProposalPerformancePanel(proposal, samples) + renderStrategyProposalPerformanceForm(proposal, disabled);
  if (section === "history") return renderStrategyProposalReviewLog(reviewLog);
  return renderStrategyProposalSummaryPanel(proposal, validation, diff, reviewLog, samples);
}

function renderStrategyProposalSummaryPanel(proposal, validation, diff, reviewLog, samples) {
  var symbols = strategyProposalArray(proposal.symbols);
  var ruleIds = strategyProposalArray(proposal.ruleIds);
  var entry = strategyProposalArray(proposal.entryConditions);
  var exit = strategyProposalArray(proposal.exitConditions);
  var summary = strategyProposalPerformanceSummary(proposal);
  return [
    '<section class="strategy-proposal-summary-panel">',
    '<div class="strategy-proposal-summary-copy">',
    '<strong>핵심 가설</strong>',
    '<p>' + escapeHtml(proposal.thesis || "전략 가설 설명이 아직 없습니다.") + '</p>',
    '</div>',
    '<div class="strategy-proposal-focus-grid">',
    renderInvestmentTodayStatusCell("대상 종목", symbols.length || "-", symbols.slice(0, 4).join(", ") || "종목 미지정", symbols.length ? "watch" : "hold"),
    renderInvestmentTodayStatusCell("연결 룰", ruleIds.length || "-", ruleIds.slice(0, 3).join(", ") || "RuleBox 연결 전", ruleIds.length ? "watch" : "caution"),
    renderInvestmentTodayStatusCell("검증 상태", validation.status || "not-run", diff.wroteInferenceBox || validation.wroteInferenceBox ? "InferenceBox 기록" : "기록 전", validation.status === "ok" || validation.status === "completed" ? "watch" : "hold"),
    renderInvestmentTodayStatusCell("성과 표본", summary.sampleCount || samples.length || 0, summary.avgExcessReturnPct == null ? "초과수익 미기록" : strategyProposalSignedPercent(summary.avgExcessReturnPct), summary.avgExcessReturnPct == null ? "hold" : (Number(summary.avgExcessReturnPct) >= 0 ? "watch" : "danger")),
    '</div>',
    '<div class="strategy-proposal-summary-slices">',
    renderReasoningCardList("대표 진입 조건", entry.slice(0, 3)),
    renderReasoningCardList("대표 청산 조건", exit.slice(0, 3)),
    renderReasoningCardList("최근 이력", reviewLog.slice(-3).reverse().map(function (item) {
      return [item.action || "record", item.at ? formatClock(item.at) : "", item.reviewedBy || item.source || ""].filter(Boolean).join(" · ");
    })),
    '</div>',
    '</section>'
  ].join("");
}

function renderStrategyProposalConditionGrid(proposal) {
  var groups = [
    { label: "진입 조건", items: strategyProposalArray(proposal.entryConditions) },
    { label: "청산 조건", items: strategyProposalArray(proposal.exitConditions) },
    { label: "리스크 통제", items: strategyProposalArray(proposal.riskControls) },
    { label: "포지션·리밸런싱", items: strategyProposalArray(proposal.positionSizing).concat(strategyProposalArray(proposal.rebalancePolicy)) }
  ];
  return [
    '<div class="strategy-proposal-condition-grid">',
    groups.map(function (group) {
      return [
        '<section>',
        '<strong>' + escapeHtml(group.label) + '</strong>',
        group.items.length ? '<ul>' + group.items.slice(0, 6).map(function (item) {
          return '<li>' + escapeHtml(item) + '</li>';
        }).join("") + '</ul>' : '<p>조건 데이터 없음</p>',
        '</section>'
      ].join("");
    }).join(""),
    '</div>'
  ].join("");
}

function renderStrategyProposalValidationPanel(validation, diff) {
  var status = String(validation.status || "not-run");
  var reason = validation.reason || "";
  return [
    '<section class="strategy-proposal-subpanel">',
    '<div class="strategy-proposal-subpanel-head">',
    '<strong>TypeDB 물질화 검증</strong>',
    '<span class="tone-chip ' + escapeHtml(status === "ok" || status === "empty" || status === "completed" ? "watch" : (status === "not-run" ? "hold" : "caution")) + '">' + escapeHtml(status === "not-run" ? "미실행" : status) + '</span>',
    '</div>',
    reason ? '<p class="subtle">' + escapeHtml(reason) + '</p>' : '',
    '<div class="work-detail-metric-row strategy-proposal-validation-metrics">',
    renderOntologyExperimentMetric("기준 관계", strategyProposalMetricValue(diff.baselineRelationCount), "baseline"),
    renderOntologyExperimentMetric("후보 매칭", strategyProposalMetricValue(diff.candidateMatchedCount != null ? diff.candidateMatchedCount : validation.matchedCount), "candidate"),
    renderOntologyExperimentMetric("관계 변화", strategyProposalMetricValue(diff.matchedMinusBaselineRelations), "delta"),
    renderOntologyExperimentMetric("InferenceBox", diff.wroteInferenceBox || validation.wroteInferenceBox ? "기록" : "미기록", "write"),
    '</div>',
    '</section>'
  ].join("");
}

function renderStrategyProposalPerformancePanel(proposal, samples) {
  var summary = strategyProposalPerformanceSummary(proposal);
  return [
    '<section class="strategy-proposal-subpanel">',
    '<div class="strategy-proposal-subpanel-head">',
    '<strong>성과 요약</strong>',
    '<span class="tone-chip hold">' + escapeHtml((summary.sampleCount || samples.length || 0) + "개 표본") + '</span>',
    '</div>',
    '<div class="work-detail-metric-row strategy-proposal-performance-metrics">',
    renderOntologyExperimentMetric("평균 수익률", strategyProposalSignedPercent(summary.avgPortfolioReturnPct), "portfolio"),
    renderOntologyExperimentMetric("벤치마크", strategyProposalSignedPercent(summary.avgBenchmarkReturnPct), "benchmark"),
    renderOntologyExperimentMetric("초과 수익", strategyProposalSignedPercent(summary.avgExcessReturnPct), "excess"),
    renderOntologyExperimentMetric("오탐률", summary.falsePositiveRate == null ? "-" : Math.round(Number(summary.falsePositiveRate || 0) * 100) + "%", "false-positive"),
    '</div>',
    '</section>'
  ].join("");
}

function renderStrategyProposalReviewLog(reviewLog) {
  var rows = latestChangedFirst(reviewLog, function (item) { return item && item.at; }).slice(0, 6);
  return [
    '<section class="strategy-proposal-subpanel">',
    '<div class="strategy-proposal-subpanel-head">',
    '<strong>승인·검증 이력</strong>',
    '<span class="tone-chip hold">' + escapeHtml(reviewLog.length + "건") + '</span>',
    '</div>',
    rows.length ? '<div class="strategy-proposal-review-log">' + rows.map(function (item) {
      var meta = [
        item.reviewedBy || item.source || item.trigger || "",
        item.validationStatus || ""
      ].filter(Boolean).join(" · ");
      return [
        '<div>',
        '<strong>' + escapeHtml(item.action || "record") + '</strong>',
        '<span>' + escapeHtml(meta || "이력 데이터") + '</span>',
        renderRecordChangedAt(item.at),
        '</div>'
      ].join("");
    }).join("") + '</div>' : '<p class="subtle">아직 승인 또는 검증 이력이 없습니다.</p>',
    '</section>'
  ].join("");
}

function renderStrategyProposalPerformanceForm(proposal, disabled) {
  var id = proposal && proposal.id ? proposal.id : "";
  return [
    '<form class="strategy-proposal-performance-form" data-strategy-proposal-performance-form="' + escapeHtml(id) + '">',
    '<label class="setting-field"><span>포트폴리오 수익률(%)</span><input name="portfolioReturnPct" type="number" step="0.01" inputmode="decimal" placeholder="0.00"' + (disabled ? ' disabled' : '') + '></label>',
    '<label class="setting-field"><span>벤치마크 수익률(%)</span><input name="benchmarkReturnPct" type="number" step="0.01" inputmode="decimal" placeholder="0.00"' + (disabled ? ' disabled' : '') + '></label>',
    '<label class="setting-field"><span>최대 낙폭(%)</span><input name="maxDrawdownPct" type="number" step="0.01" inputmode="decimal" placeholder="-0.00"' + (disabled ? ' disabled' : '') + '></label>',
    '<label class="setting-field"><span>신호 수</span><input name="signalCount" type="number" min="0" step="1" inputmode="numeric" placeholder="0"' + (disabled ? ' disabled' : '') + '></label>',
    '<label class="setting-field"><span>오탐 수</span><input name="falsePositiveCount" type="number" min="0" step="1" inputmode="numeric" placeholder="0"' + (disabled ? ' disabled' : '') + '></label>',
    '<label class="setting-field wide"><span>메모</span><textarea name="notes" rows="2" placeholder="성과 판단 메모"' + (disabled ? ' disabled' : '') + '></textarea></label>',
    '<button class="text-button primary" type="submit"' + (disabled ? ' disabled' : '') + '>' + escapeHtml(strategyProposalBusy("performance", id) ? "기록 중" : "성과 기록") + '</button>',
    '</form>'
  ].join("");
}



export { renderStrategyProposalConsolePanel, strategyProposalArray, strategyProposalById, strategyProposalItems, strategyProposalPayload, strategyProposalPerformanceSummary, strategyProposalSignedPercent, strategyProposalSummary };
