import { renderInvestmentCaseReasoning } from "./case-reasoning.mjs";
import { investmentCaseOperatorAccess, renderInvestmentCaseCurrentState, renderInvestmentCaseDetailTabs, renderInvestmentCaseEvidence, renderInvestmentCaseSummary } from "./case-summary.mjs";
import { decisionActionMeta } from "./selectors.mjs";
import { renderInvestmentFlowStages, renderInvestmentFlowStateLegend } from "./workspace.mjs";
import { createPanelScrollMemory } from "../navigation/panel-scroll.mjs";
import { renderConsoleEmpty, renderConsoleListSkeleton } from "../shared/console.mjs";
import { formatClock } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { bindAutoGrowingTextareas } from "../shell/forms.mjs";
import { decorateRenderedBusyControls, syncNetworkActivityDom } from "../shell/network-activity.mjs";
import { app } from "../shell/root.mjs";
import { decisionsState } from "../state/decisions.mjs";

function renderInvestmentCaseHistory(key) {
  var payload = decisionsState.investmentCaseHistories[key] || null;
  var loading = Boolean(decisionsState.investmentCaseHistoryLoading[key]);
  var error = String(decisionsState.investmentCaseHistoryErrors[key] || "");
  if (!payload) {
    if (error) return renderConsoleEmpty("판단 이력을 불러오지 못했습니다", error, '<button class="text-button" type="button" data-investment-case-history-retry="' + escapeHtml(key) + '">다시 시도</button>');
    return renderConsoleListSkeleton("oa-case-history-row", ["시각", "판단", "변화"], loading ? 5 : 3);
  }
  var items = Array.isArray(payload.items) ? payload.items : [];
  var detail = decisionsState.investmentFlowDetails[key] || {};
  var activity = detail.activity && typeof detail.activity === "object" ? detail.activity : {};
  var activitySummary = activity.summary || {};
  var timeline = Array.isArray(activity.timeline) ? activity.timeline : [];
  var activityMarkup = [
    '<section class="oa-case-activity-summary"><header><div><span>DECISION FOLLOW-UP</span><strong>사용자 행동과 사후 결과</strong><p>관측된 수량 변화와 체결은 사실로 표시하되, 이 판단을 따랐다고 자동 단정하지 않습니다.</p></div><b class="' + escapeHtml(activity.status === "observed" ? "watch" : "hold") + '">' + escapeHtml(activity.status === "observed" ? "관측 기록 있음" : "관측 대기") + '</b></header><div>',
    '<span><em>수량 변화</em><strong>' + escapeHtml(Number(activitySummary.actionObservationCount || 0) + "건") + '</strong></span>',
    '<span><em>실제 체결</em><strong>' + escapeHtml(Number(activitySummary.fillCount || 0) + "건") + '</strong></span>',
    '<span><em>성과 관측</em><strong>' + escapeHtml(Number(activitySummary.outcomeCount || 0) + "건") + '</strong></span>',
    '<span><em>사후 검토</em><strong>' + escapeHtml(Number(activitySummary.reviewCount || 0) + "건") + '</strong></span>',
    '</div><p>' + escapeHtml(activity.causalityNote || "행동 인과관계는 사용자 확인 전까지 확정하지 않습니다.") + '</p></section>',
    timeline.length ? '<div class="oa-case-activity-timeline">' + timeline.map(function (entry) {
      var row = entry.payload && typeof entry.payload === "object" ? entry.payload : {};
      var metric = entry.type === "outcome" && row.priceChangeFromDecisionPct !== undefined ? "판단 후 " + (Number(row.priceChangeFromDecisionPct) > 0 ? "+" : "") + Number(row.priceChangeFromDecisionPct).toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + "%" : (entry.detail || "관측 기록");
      return '<article data-activity-type="' + escapeHtml(entry.type || "event") + '"><time>' + escapeHtml(formatClock(entry.at) || "시각 미기록") + '</time><div><strong>' + escapeHtml(entry.label || "추적 기록") + '</strong><p>' + escapeHtml(metric) + '</p></div></article>';
    }).join("") + '</div>' : renderConsoleEmpty("연결된 사용자 행동과 사후 결과가 없습니다", "관측 기간이 지나거나 실제 체결·보유 수량 변화가 확인되면 자동으로 연결합니다.")
  ].join("");
  if (!items.length) return activityMarkup + renderConsoleEmpty("저장된 판단 이력이 없습니다", "새 판단이 저장되면 이전 판단과의 변화가 표시됩니다.");
  return activityMarkup + '<section class="oa-case-history-section"><header><span>DECISION HISTORY</span><strong>판단 변화 이력</strong></header><div class="oa-case-history-list">' + items.map(function (item) {
    var action = decisionActionMeta(item.action, item.action);
    var change = item.change || {};
    var changes = [];
    if (change.actionChanged) changes.push(decisionActionMeta(change.previousAction, change.previousAction).label + " → " + action.label);
    if (change.hypothesisChanged) changes.push("핵심 가설 변경");
    if (change.evidenceChanged) changes.push("근거 변경");
    if (change.readinessChanged || change.validationChanged) changes.push("판단 가능 상태 변경");
    if (change.outcomeChanged) changes.push("결과 추가");
    return '<article class="oa-case-history-row"><time>' + escapeHtml(formatClock(item.decidedAt)) + '</time><div><strong>' + escapeHtml(action.label) + '</strong><p>' + escapeHtml(item.summary || "판단 기록") + '</p></div><span class="' + escapeHtml(changes.length ? "caution" : "hold") + '">' + escapeHtml(change.label || changes.join(" · ") || "이전과 같은 상태") + '</span></article>';
  }).join("") + '</div></section>';
}

function renderInvestmentCaseTrace(key) {
  var payload = decisionsState.investmentCaseTraces[key] || null;
  var loading = Boolean(decisionsState.investmentCaseTraceLoading[key]);
  var error = String(decisionsState.investmentCaseTraceErrors[key] || "");
  if (!payload) {
    if (error) return renderConsoleEmpty("운영 추적을 불러오지 못했습니다", error, '<button class="text-button" type="button" data-investment-case-trace-retry="' + escapeHtml(key) + '">다시 시도</button>');
    return renderConsoleListSkeleton("oa-decision-row", ["단계", "상태", "연결"], loading ? 8 : 4);
  }
  var trace = payload.trace || {};
  var stages = Array.isArray(trace.stages) ? trace.stages : [];
  var gaps = Array.isArray(trace.gaps) ? trace.gaps : [];
  var modelRelease = trace.modelRelease || {};
  return [
    '<section class="oa-flow-detail-section"><header><div><span>OPERATOR TRACE</span><strong>내부 처리 계보</strong></div><em>문제 복구와 감사에만 사용하는 기술 정보입니다.</em></header>',
    renderInvestmentFlowStateLegend(),
    renderInvestmentFlowStages(stages, false),
    '</section>',
    gaps.length ? '<div class="oa-flow-gap-list">' + gaps.map(function (gap) { return '<div data-flow-state="' + escapeHtml(gap.state || "warning") + '"><strong>' + escapeHtml(gap.stageLabel || gap.stage) + '</strong><span>' + escapeHtml(gap.detail || "확인 필요") + '</span></div>'; }).join("") + '</div>' : '<p class="oa-flow-complete">차단된 내부 단계가 없습니다.</p>',
    '<details class="oa-flow-technical" open><summary><span><strong>추적 식별자</strong><em>저장·추론 세대 연결</em></span></summary><dl>',
    '<div><dt>DecisionEpisode</dt><dd>' + escapeHtml(trace.episodeId || "-") + '</dd></div>',
    '<div><dt>ABox 스냅샷</dt><dd>' + escapeHtml(trace.sourceAboxSnapshotId || "연결 필요") + '</dd></div>',
    '<div><dt>InferenceBox 세대</dt><dd>' + escapeHtml(trace.inferenceGenerationId || "연결 필요") + '</dd></div>',
    '<div><dt>추론 배포</dt><dd>' + escapeHtml(modelRelease.deploymentId || modelRelease.reasoningEngineVersion || "연결 필요") + '</dd></div>',
    '<div><dt>TBox 릴리스</dt><dd>' + escapeHtml([modelRelease.tboxReleaseId, modelRelease.tboxFingerprint].filter(Boolean).join(" · ") || "연결 필요") + '</dd></div>',
    '<div><dt>RuleBox 릴리스</dt><dd>' + escapeHtml([modelRelease.ruleboxReleaseId, modelRelease.ruleboxFingerprint].filter(Boolean).join(" · ") || "연결 필요") + '</dd></div>',
    '<div><dt>선택 가설</dt><dd>' + escapeHtml(trace.selectedHypothesisId || "선택 없음") + '</dd></div>',
    '<div><dt>적용 규칙·관계</dt><dd>' + escapeHtml((Array.isArray(trace.ruleIds) ? trace.ruleIds : []).join(" · ") || "연결 필요") + '</dd></div>',
    '</dl></details>'
  ].join("");
}

function normalizeInvestmentCaseDetailTab(active) {
  var normalized = active === "scenarios" ? "reasoning" : String(active || "summary");
  if (normalized === "trace" && !investmentCaseOperatorAccess()) return "summary";
  return ["summary", "current", "evidence", "reasoning", "history", "trace"].indexOf(normalized) >= 0 ? normalized : "summary";
}

function renderInvestmentCaseTabContent(key, active, detail) {
  var normalized = normalizeInvestmentCaseDetailTab(active);
  if (normalized === "current") return renderInvestmentCaseCurrentState(detail);
  if (normalized === "evidence") return renderInvestmentCaseEvidence(detail);
  if (normalized === "reasoning") return renderInvestmentCaseReasoning(detail);
  if (normalized === "history") return renderInvestmentCaseHistory(key);
  if (normalized === "trace") return renderInvestmentCaseTrace(key);
  return renderInvestmentCaseSummary(detail, key);
}

function investmentFlowWorkDetailPayload(key) {
  key = String(key || "").trim();
  if (!key) {
    return {
      kicker: "Investment Case",
      title: "투자 케이스를 선택해 주세요",
      meta: "상세 주소에 판단 식별자가 없습니다.",
      body: renderConsoleEmpty(
        "선택된 투자 케이스가 없습니다",
        "판단 목록에서 종목을 선택하면 사실, 근거, 추론과 결과를 확인할 수 있습니다.",
        '<button class="text-button primary" type="button" data-tab="modeling" data-work-detail-close>판단 목록 보기</button>'
      )
    };
  }
  var detail = decisionsState.investmentFlowDetails[key] && typeof decisionsState.investmentFlowDetails[key] === "object"
    ? decisionsState.investmentFlowDetails[key]
    : null;
  var loading = Boolean(decisionsState.investmentFlowDetailLoading[key]);
  var error = String(decisionsState.investmentFlowDetailErrors[key] || "");
  if (!detail) {
    return {
      kicker: "Investment Case",
      title: loading ? "투자 케이스를 불러오는 중" : "투자 케이스",
      meta: error || "사실부터 결과까지 현재 판단의 전체 맥락을 확인합니다.",
      body: error
        ? renderConsoleEmpty("투자 케이스를 불러오지 못했습니다", error, '<button class="text-button" type="button" data-investment-flow-retry="' + escapeHtml(key) + '">다시 시도</button>')
        : renderConsoleListSkeleton("oa-decision-row", ["현재 판단", "핵심 신호", "다음 확인"], 5)
    };
  }
  var decision = detail.decision || {};
  var action = decisionActionMeta(decision.action, decision.action);
  var active = normalizeInvestmentCaseDetailTab(decisionsState.investmentCaseDetailTabs[key]);
  var availableViews = Array.isArray(detail.availableViews) ? detail.availableViews : [];
  if (availableViews.length && availableViews.indexOf(active) < 0) active = "summary";
  var content = renderInvestmentCaseTabContent(key, active, detail);
  return {
    kicker: "Investment Case",
    title: detail.name || detail.symbol || "투자 케이스 상세",
    meta: [detail.symbol, action.label, detail.accountId, detail.readinessLabel].filter(Boolean).join(" · "),
    body: renderInvestmentCaseDetailTabs(key, active, detail) + '<div class="oa-case-detail-content" role="tabpanel" data-work-detail-region="investment-case-content" data-investment-case-panel-key="' + escapeHtml(key) + '" data-investment-case-panel-tab="' + escapeHtml(active) + '">' + content + '</div>'
  };
}

function investmentCaseDetailRoot(key) {
  return Array.prototype.slice.call(app.querySelectorAll("[data-work-detail-dialog]")).filter(function (dialog) {
    return ["investment-case", "investment-flow"].indexOf(dialog.getAttribute("data-work-detail-type") || "") >= 0
      && dialog.getAttribute("data-work-detail-key") === String(key || "");
  })[0] || null;
}

const casePanelScroll = createPanelScrollMemory();

function patchInvestmentCaseTabRegion(key, active) {
  var normalized = normalizeInvestmentCaseDetailTab(active);
  var detail = decisionsState.investmentFlowDetails[key] && typeof decisionsState.investmentFlowDetails[key] === "object"
    ? decisionsState.investmentFlowDetails[key]
    : null;
  var root = investmentCaseDetailRoot(key);
  var panel = root && root.querySelector("[data-investment-case-panel-key]");
  if (!detail || !root || !panel) return false;
  var scroller = root.closest && root.closest(".work-detail-backdrop");
  var previousTab = panel.getAttribute("data-investment-case-panel-tab") || "summary";
  var startedAt = window.performance && window.performance.now ? window.performance.now() : Date.now();
  Array.prototype.slice.call(root.querySelectorAll("[data-investment-case-tab]")).forEach(function (button) {
    var selected = button.getAttribute("data-investment-case-tab") === normalized;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", selected ? "true" : "false");
  });
  casePanelScroll.replace(scroller, key + ":" + previousTab, key + ":" + normalized, function () {
    panel.setAttribute("data-investment-case-panel-tab", normalized);
    panel.innerHTML = renderInvestmentCaseTabContent(key, normalized, detail);
    bindAutoGrowingTextareas(panel);
  });
  syncNetworkActivityDom();
  decorateRenderedBusyControls();
  var runtimePerformance = window.OrbitWebRuntime;
  if (runtimePerformance) {
    var endedAt = window.performance && window.performance.now ? window.performance.now() : Date.now();
    runtimePerformance.record("render-region", Math.max(0, endedAt - startedAt), { region: "investment-case", tab: normalized });
  }
  return true;
}

export { investmentFlowWorkDetailPayload, normalizeInvestmentCaseDetailTab, patchInvestmentCaseTabRegion };
