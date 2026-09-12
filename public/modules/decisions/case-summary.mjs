import { investmentReasoningValue } from "./case-reasoning.mjs";
import { decisionActionMeta } from "./selectors.mjs";
import { decisionExplanationRows, renderDecisionCauseList } from "./workspace.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { renderDecisionInfoButton } from "../settings/language.mjs";
import { renderConsoleEmpty } from "../shared/console.mjs";
import { formatClock } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { settingsState } from "../state/settings.mjs";
import { briefTexts, renderInvestmentBrief } from "./brief.mjs";
import { evidenceResolutionLabel, evidenceSummary } from "./evidence-summary.mjs";

function investmentCaseOperatorAccess() {
  return !settingsState.serverSettingsLocked && !isStaticPreviewHost();
}

function renderInvestmentCaseDetailTabs(key, active, detail) {
  var tabs = [
    ["summary", "요약"],
    ["current", "판단 당시·현재"],
    ["evidence", "근거·반대"],
    ["reasoning", "추론 과정"],
    ["history", "변화·결과"]
  ];
  if (investmentCaseOperatorAccess()) tabs.push(["trace", "기술 계보"]);
  var available = Array.isArray((detail || {}).availableViews) ? detail.availableViews : [];
  if (available.length) tabs = tabs.filter(function (item) { return available.indexOf(item[0]) >= 0; });
  return '<nav class="oa-case-detail-tabs" role="tablist" aria-label="투자 케이스 상세 보기">' + tabs.map(function (item) {
    var selected = active === item[0];
    return '<button type="button" role="tab" data-investment-case-tab="' + item[0] + '" data-investment-case-key="' + escapeHtml(key) + '" aria-selected="' + (selected ? "true" : "false") + '"' + (selected ? ' class="active"' : '') + '>' + escapeHtml(item[1]) + '</button>';
  }).join("") + '</nav>';
}

function investmentHypothesisQualificationMeta(value) {
  var qualification = value && typeof value === "object" ? value : {};
  var status = String(qualification.status || "shadow").toLowerCase();
  var labels = {
    active: "운영 근거로 사용",
    "limited-active": "제한적으로 사용",
    observed: "성과 검증 중",
    shadow: "표본 수집 중",
    quarantined: "행동 근거에서 제외",
    "active-reference": "참고 근거",
    "active-guardrail": "안전 제한"
  };
  var tones = {
    active: "pass",
    "limited-active": "warning",
    observed: "warning",
    shadow: "pending",
    quarantined: "blocked",
    "active-reference": "hold",
    "active-guardrail": "hold"
  };
  return {
    status: status,
    label: labels[status] || "검증 상태 확인",
    tone: tones[status] || "warning"
  };
}

function renderInvestmentCaseLineageChain(detail) {
  var lineage = detail.reasoningLineage && typeof detail.reasoningLineage === "object" ? detail.reasoningLineage : {};
  var identity = lineage.identity && typeof lineage.identity === "object" ? lineage.identity : {};
  var reasoning = detail.reasoning && typeof detail.reasoning === "object" ? detail.reasoning : {};
  var counts = reasoning.counts && typeof reasoning.counts === "object" ? reasoning.counts : {};
  var ai = lineage.ai && typeof lineage.ai === "object" ? lineage.ai : {};
  var modelRelease = detail.traceRefs && detail.traceRefs.modelRelease ? detail.traceRefs.modelRelease : {};
  var scenarios = Array.isArray(detail.scenarios) ? detail.scenarios : [];
  var qualifiedCount = scenarios.filter(function (item) {
    return ["active", "limited-active"].indexOf(String(((item || {}).qualification || {}).status || "").toLowerCase()) >= 0;
  }).length;
  var observedCount = scenarios.filter(function (item) {
    return String(((item || {}).qualification || {}).status || "").toLowerCase() === "observed";
  }).length;
  var aiAuthored = ai.status === "ai-authored" && ai.aiAuthored === true && ai.publicationContractPassed === true;
  var stages = [
    {
      label: "TBox",
      title: "개념·계약",
      detail: modelRelease.tboxReleaseId || identity.tboxReleaseId || "릴리스 연결 필요",
      state: modelRelease.tboxReleaseId || identity.tboxReleaseId ? "pass" : "blocked"
    },
    {
      label: "ABox",
      title: "판단 당시 사실",
      detail: Number(counts.facts || 0) + "개 관측값",
      state: Number(counts.facts || 0) > 0 ? "pass" : "blocked"
    },
    {
      label: "RuleBox",
      title: "성립한 규칙",
      detail: Number(counts.rules || 0) + "개 규칙",
      state: Number(counts.rules || 0) > 0 ? "pass" : "blocked"
    },
    {
      label: "InferenceBox",
      title: "관계 추론",
      detail: Number(counts.relations || 0) + "개 관계 · " + Number(counts.traces || 0) + "개 실행",
      state: Number(counts.traces || 0) > 0 ? "pass" : "blocked"
    },
    {
      label: "가설",
      title: "경쟁 설명 비교",
      detail: scenarios.length + "개 · 운영 " + qualifiedCount + " · 검증 중 " + observedCount,
      state: scenarios.length ? (qualifiedCount ? "pass" : "warning") : "blocked"
    },
    {
      label: "AI",
      title: aiAuthored ? "독립 분석 완료" : "AI 분석 미완료",
      detail: aiAuthored ? [ai.model, ai.reasoningEffort, ai.promptVersion].filter(Boolean).join(" · ") : (ai.contractFailureCode || ai.status || "실행 대기"),
      state: aiAuthored ? "pass" : (ai.status === "not-run" ? "pending" : "warning")
    }
  ];
  return [
    '<section class="oa-case-lineage-chain" data-lineage-state="' + escapeHtml(aiAuthored ? "complete" : "partial") + '">',
    '<header><div><span>REASONING LINEAGE</span><strong>사실에서 투자 해석까지</strong><p>각 단계는 같은 종목, ABox 스냅샷과 추론 세대로 연결됩니다.</p></div><b>' + escapeHtml(aiAuthored ? "전체 연결" : "AI 단계 확인 필요") + '</b></header>',
    '<ol>',
    stages.map(function (stage) {
      return '<li data-flow-state="' + escapeHtml(stage.state) + '"><span>' + escapeHtml(stage.label) + '</span><strong>' + escapeHtml(stage.title) + '</strong><em title="' + escapeHtml(stage.detail) + '">' + escapeHtml(stage.detail) + '</em></li>';
    }).join(""),
    '</ol>',
    '<footer><code>' + escapeHtml(identity.sourceAboxSnapshotId || "ABox 연결 필요") + '</code><code>' + escapeHtml(identity.inferenceGenerationId || "InferenceBox 연결 필요") + '</code></footer>',
    '</section>'
  ].join("");
}

function renderInvestmentCaseSummary(detail, key) {
  var decision = detail.decision || {};
  var action = decisionActionMeta(decision.state === "blocked" ? "BLOCKED" : decision.action, decision.action);
  var modelRelease = ((detail.traceRefs || {}).modelRelease || {});
  return [
    renderInvestmentBrief(detail, key, action.label, formatClock),
    '<section class="oa-case-model-link"><span><strong>사용한 판단 기준</strong><em>' + escapeHtml(modelRelease.deploymentId || modelRelease.reasoningEngineVersion || modelRelease.lineageLabel || "릴리스 계보 확인") + '</em></span>' + renderWorkDetailButton("investment-model-overview", "", "판단 기준", "text-button compact") + '</section>'
  ].join("");
}

function investmentCaseCurrentValue(item) {
  var field = String((item || {}).field || "");
  var value = (item || {}).value;
  if (value === null || value === undefined || value === "") return "기록 없음";
  if (typeof value === "number" && /(Rate|Distance|Slope|Weight|Imbalance|btcChange)/i.test(field)) {
    return value.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + "%";
  }
  return investmentReasoningValue(value);
}

function renderInvestmentCaseCurrentState(detail) {
  var current = detail.currentState || {};
  var groups = Array.isArray(current.groups) ? current.groups : [];
  var comparison = detail.liveComparison && typeof detail.liveComparison === "object" ? detail.liveComparison : {};
  var comparisonRows = Array.isArray(comparison.rows) ? comparison.rows : [];
  var comparisonMarkup = comparisonRows.length ? [
    '<section class="oa-case-live-comparison"><header><div><span>POINT-IN-TIME COMPARISON</span><strong>판단 당시와 최신 관측 비교</strong><p>과거 판단을 현재 값으로 덮어쓰지 않고 두 시점을 나란히 비교합니다.</p></div><time>' + escapeHtml(formatClock(comparison.asOf) || "최신 시각 미기록") + '</time></header>',
    '<div class="oa-case-live-table"><div class="oa-case-live-row head"><span>항목</span><span>판단 당시</span><span>최신 관측</span><span>변화</span></div>',
    comparisonRows.map(function (item) {
      var delta = item.delta === null || item.delta === undefined ? "비교 불가" : ((Number(item.delta) > 0 ? "+" : "") + investmentReasoningValue(item.delta));
      var deltaPct = item.deltaPct === null || item.deltaPct === undefined ? "" : " (" + (Number(item.deltaPct) > 0 ? "+" : "") + Number(item.deltaPct).toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + "%)";
      return '<div class="oa-case-live-row"><strong>' + escapeHtml(item.label || item.field) + '</strong><span>' + escapeHtml(investmentReasoningValue(item.decisionValue)) + '</span><span><b>' + escapeHtml(investmentReasoningValue(item.currentValue)) + '</b><small>' + escapeHtml([item.source, formatClock(item.sourceAsOf), item.freshnessStatus].filter(Boolean).join(" · ")) + '</small></span><em>' + escapeHtml(delta + deltaPct) + '</em></div>';
    }).join(""),
    '</div></section>'
  ].join("") : renderConsoleEmpty("최신 상태와 비교할 수 없습니다", comparison.reason || "최신 모니터 스냅샷에 이 종목의 관측값이 없습니다.");
  if (!groups.length) return comparisonMarkup + renderConsoleEmpty("판단 당시 상태가 없습니다", "과거 복원 기록에는 판단 당시 값의 일부만 남아 있을 수 있습니다.");
  return [
    comparisonMarkup,
    '<details class="oa-case-decision-snapshot"><summary><span><strong>판단 당시 전체 상태</strong><em>' + escapeHtml(groups.reduce(function (total, group) { return total + (Array.isArray(group.items) ? group.items.length : 0); }, 0) + "개 저장값") + '</em></span></summary>',
    '<section class="oa-case-current-head"><div><span>DECISION SNAPSHOT</span><strong>이 판단이 만들어질 때 확인한 상태</strong><p>현재 실시간 값이 아니라 판단 기준 시각에 고정된 값입니다.</p></div><time>' + escapeHtml(formatClock(current.asOf) || "기준 시각 미기록") + '</time></section>',
    '<div class="oa-case-current-groups">',
    groups.map(function (group) {
      var items = Array.isArray(group.items) ? group.items : [];
      return '<section><header><strong>' + escapeHtml(group.label || group.id || "현재 상태") + '</strong><span>' + escapeHtml(items.length + "개") + '</span></header><dl>' + items.map(function (item) {
        var source = [item.source, formatClock(item.asOf)].filter(Boolean).join(" · ");
        var sourceLink = /^https?:\/\//i.test(String(item.sourceUrl || "")) ? '<a href="' + escapeHtml(item.sourceUrl) + '" target="_blank" rel="noopener noreferrer">원문</a>' : '';
        return '<div><dt>' + escapeHtml(item.label || item.field || "관측값") + '</dt><dd><strong>' + escapeHtml(investmentCaseCurrentValue(item)) + '</strong>' + (item.expected ? '<em>성립 기준 ' + escapeHtml(item.expected) + '</em>' : '') + (source ? '<small>' + escapeHtml(source) + '</small>' : '') + sourceLink + '</dd></div>';
      }).join("") + '</dl></section>';
    }).join(""),
    '</div></details>'
  ].join("");
}

function renderInvestmentCaseEvidence(detail) {
  var evidence = detail.evidence || {};
  var counts = evidenceSummary(detail);
  function countLabel(value) { return value === null ? "미확인" : value + "건"; }
  var explanation = detail.explanation || {};
  var missing = Array.isArray(evidence.missingData) ? evidence.missingData : [];
  var missingItems = Array.isArray(evidence.missingDataItems) ? evidence.missingDataItems : [];
  var checks = briefTexts([].concat(evidence.requiredChecks || [], (detail.decision || {}).requiredChecks || [], explanation.changeConditions || [], detail.nextAction || []));
  var supportIds = Array.isArray(evidence.supportingIds) ? evidence.supportingIds : [];
  var counterIds = Array.isArray(evidence.counterIds) ? evidence.counterIds : [];
  var records = Array.isArray(evidence.records) ? evidence.records : [];
  var recordMarkup = records.length ? '<div class="oa-case-evidence-records">' + records.map(function (item) {
    var resolved = item.resolutionState === "resolved";
    var title = '<strong>' + escapeHtml(item.title || item.id || "근거") + '</strong>';
    return '<article data-evidence-role="' + escapeHtml(item.role || "context") + '" data-evidence-resolution="' + escapeHtml(item.resolutionState || "identifier-only") + '"><header><span>' + escapeHtml(item.roleLabel || "근거") + '</span><em>' + escapeHtml(item.useStateLabel || "판단에 사용") + '</em></header>' + (/^https?:\/\//i.test(String(item.url || "")) ? '<a href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener noreferrer">' + title + '</a>' : title) + (item.summary ? '<p>' + escapeHtml(item.summary) + '</p>' : '') + '<footer><span>' + escapeHtml([item.sourcePublisher || item.source, item.kind, formatClock(item.sourceAsOf)].filter(Boolean).join(" · ")) + '</span><b class="' + (resolved ? "watch" : "caution") + '">' + escapeHtml(evidenceResolutionLabel(item.resolutionState)) + '</b></footer></article>';
  }).join("") + '</div>' : '<p class="oa-decision-empty-note">저장된 근거 식별자가 없습니다.</p>';
  return [
    '<section class="oa-case-evidence-summary">',
    '<div><span>지지 근거</span><strong>' + escapeHtml(countLabel(counts.support)) + '</strong></div>',
    '<div><span>반박 근거</span><strong>' + escapeHtml(countLabel(counts.counter)) + '</strong></div>',
    '<div><span>자료 확인 항목</span><strong>' + escapeHtml(countLabel(counts.missing)) + '</strong></div>',
    '<div><span>원천 자료 연결</span><strong>' + escapeHtml(counts.originals + " / " + counts.records + "건") + '</strong><small>추론 계보 ' + escapeHtml(counts.lineage) + '건</small></div>',
    '</section>',
    '<section class="oa-case-evidence-section"><header><span>검증 근거</span><strong>판단에 실제 사용한 원천</strong>' + renderDecisionInfoButton("evidence-provenance", "근거의 역할, 출처, 기준 시각과 원문 연결 상태를 확인합니다.") + '</header>' + recordMarkup + '</section>',
    '<div class="oa-case-cause-columns"><section><header><strong>의견을 지지한 근거</strong>' + renderDecisionInfoButton("reasoning-rule", "규칙과 가설을 통해 현재 의견 방향을 지지한 항목입니다.") + '</header>' + renderDecisionCauseList(decisionExplanationRows(explanation, "supportingCauses"), "명시적으로 저장된 지지 근거가 없습니다.") + '</section><section><header><strong>반대 근거</strong>' + renderDecisionInfoButton("competing-hypothesis", "현재 의견과 다른 시나리오를 지지하는 근거입니다.") + '</header>' + renderDecisionCauseList(decisionExplanationRows(explanation, "counterCauses"), "명시적으로 저장된 반대 근거가 없습니다.") + '</section></div>',
    '<section class="oa-case-evidence-section"><header><span>자료 한계</span><strong>빠졌거나 적용할 수 없는 자료</strong>' + renderDecisionInfoButton("data-state", "확인되지 않은 자료는 사실처럼 쓰지 않고 판단 범위만 제한합니다.") + '</header>',
    missingItems.length ? '<ul class="oa-case-gap-list">' + missingItems.map(function (item) { return '<li><strong>' + escapeHtml(item.label || "확인 항목") + '</strong><span>' + escapeHtml(item.detail || item.text || "확인 필요") + '</span><em>' + escapeHtml(item.applicability === "not-applicable" ? "현재 시장에서 적용 안 됨" : item.source || "수집 상태 확인") + '</em></li>'; }).join("") + '</ul>' : '',
    missing.length ? '<ul>' + missing.map(function (item) { return '<li>' + escapeHtml(typeof item === "string" ? item : item.label || item.detail || "자료 확인 필요") + '</li>'; }).join("") + '</ul>' : '',
    !missingItems.length && !missing.length ? '<p class="oa-decision-empty-note">' + (counts.missing === 0 ? '기록된 자료 확인 항목은 0건입니다.' : '자료 공백 여부를 확인할 기록이 부족합니다.') + '</p>' : '',
    checks.length ? '<div class="oa-case-check-list"><strong>다음 확인 조건 전체</strong><ul>' + checks.map(function (item) { return '<li>' + escapeHtml(item) + '</li>'; }).join("") + '</ul></div>' : '',
    '</section>',
    '<details class="oa-flow-technical"><summary><span><strong>근거 식별자</strong><em>감사와 원문 조회에 사용하는 저장 키</em></span></summary><dl>',
    '<div><dt>원천 스냅샷</dt><dd>' + escapeHtml(evidence.sourceSnapshotId || "연결 필요") + '</dd></div>',
    '<div><dt>지지 근거</dt><dd>' + escapeHtml(supportIds.join(" · ") || "없음") + '</dd></div>',
    '<div><dt>반박 근거</dt><dd>' + escapeHtml(counterIds.join(" · ") || "없음") + '</dd></div>',
    '</dl></details>'
  ].join("");
}

export { investmentCaseOperatorAccess, investmentHypothesisQualificationMeta, renderInvestmentCaseCurrentState, renderInvestmentCaseDetailTabs, renderInvestmentCaseEvidence, renderInvestmentCaseLineageChain, renderInvestmentCaseSummary };
