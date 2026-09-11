import { investmentHypothesisQualificationMeta } from "./case-summary.mjs";
import { decisionActionMeta } from "./selectors.mjs";
import { renderDecisionInfoButton } from "../settings/language.mjs";
import { renderConsoleEmpty } from "../shared/console.mjs";
import { escapeHtml } from "../shared/text.mjs";

function investmentReasoningValue(value) {
  if (value === null || value === undefined || value === "") return "기록 없음";
  if (typeof value === "boolean") return value ? "예" : "아니오";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) : "기록 없음";
  if (Array.isArray(value)) return value.map(investmentReasoningValue).join(" · ");
  if (typeof value === "object") {
    try { return JSON.stringify(value); } catch (_error) { return String(value); }
  }
  return String(value);
}

function investmentReasoningRoleLabel(value) {
  return ({ support: "지지", risk: "위험", counter: "반박", constraint: "제한", context: "참고", required: "필수" })[String(value || "").toLowerCase()] || String(value || "근거");
}

function renderInvestmentReasoningConditions(conditions) {
  if (!Array.isArray(conditions) || !conditions.length) return "";
  var verified = conditions.some(function (item) { return item.verified !== false && item.observedValue !== null && item.observedValue !== undefined && item.observedValue !== ""; });
  return '<div class="oa-reasoning-conditions"><strong>' + escapeHtml(verified ? "실제로 확인된 조건" : "과거 기록에 남은 조건 식별자") + '</strong><ul>' + conditions.map(function (item) {
    var observed = investmentReasoningValue(item.observedValue);
    var expected = item.expected ? " · 기준 " + item.expected : "";
    var observation = item.verified === false || item.observedValue === null || item.observedValue === undefined || item.observedValue === "" ? "당시 관측값 미저장" : "관측 " + observed + expected;
    var sourceLink = /^https?:\/\//i.test(String(item.sourceUrl || "")) ? '<a href="' + escapeHtml(item.sourceUrl) + '" target="_blank" rel="noopener noreferrer">원문</a>' : '';
    return '<li><span><b>' + escapeHtml(item.label || item.field || item.id || "성립 조건") + '</b><em>' + escapeHtml(investmentReasoningRoleLabel(item.role)) + '</em></span><p>' + escapeHtml(observation) + '</p>' + ((item.source || item.asOf) ? '<small>' + escapeHtml([item.source, item.asOf].filter(Boolean).join(" · ")) + '</small>' : '') + sourceLink + '</li>';
  }).join("") + '</ul></div>';
}

function renderInvestmentReasoningItem(layer, item) {
  item = item && typeof item === "object" ? item : {};
  var title = item.label || item.title || item.claim || item.id || "상세 정보";
  var primary = "";
  var meta = [];
  var knowledge = item.knowledgeBasis && typeof item.knowledgeBasis === "object" ? item.knowledgeBasis : {};
  var knowledgeDetail = "";
  if (layer === "fact") {
    primary = '<p><b>관측값</b> ' + escapeHtml(investmentReasoningValue(item.observedValue)) + (item.expected ? ' <span>· 성립 기준 ' + escapeHtml(item.expected) + '</span>' : '') + '</p>';
    if (item.source) meta.push("출처 " + item.source);
    if (item.asOf) meta.push("기준 " + item.asOf);
    if (item.dataState) meta.push("자료 " + item.dataState);
  } else if (layer === "relation") {
    var source = item.sourceLabel || item.source || "출발 객체";
    var target = item.targetLabel || item.target || "도착 객체";
    primary = '<p><b>' + escapeHtml(source) + '</b><span class="oa-reasoning-arrow">→</span><b>' + escapeHtml(target) + '</b></p>';
    if (item.type) meta.push("관계 " + item.type);
    if (item.polarity) meta.push("역할 " + investmentReasoningRoleLabel(item.polarity));
    if (item.referenceOnly) meta.push("판단에는 참고만 사용");
    if (item.evidenceUsable === false) meta.push("행동 근거 사용 불가");
  } else if (layer === "rule") {
    primary = (item.description || knowledge.plainLanguageBasis) ? '<p>' + escapeHtml(item.description || knowledge.plainLanguageBasis) + '</p>' : '<p>이 규칙의 조건이 판단 시점 데이터에서 성립했습니다.</p>';
    if (item.evidenceRole) meta.push("역할 " + investmentReasoningRoleLabel(item.evidenceRole));
    if (item.candidateAction) meta.push("후보 행동 " + decisionActionMeta(item.candidateAction, item.candidateAction).label);
    if (item.reviewLevel) meta.push("검토 단계 " + item.reviewLevel);
    if (item.referenceOnly) meta.push("참고 규칙");
    if (knowledge.ruleKind) meta.push("규칙 유형 " + knowledge.ruleKind);
    if (knowledge.validationStatus) meta.push("검증 " + knowledge.validationStatus);
    if (knowledge.thresholdOrigin) meta.push("임계값 출처 " + knowledge.thresholdOrigin);
    var references = Array.isArray(knowledge.references) ? knowledge.references : [];
    if (references.length) knowledgeDetail = '<div class="oa-reasoning-knowledge"><strong>규칙 지식 근거</strong><ul>' + references.map(function (reference) { var body = '<b>' + escapeHtml(reference.title || reference.referenceId || "근거 자료") + '</b>' + (reference.claim ? '<span>' + escapeHtml(reference.claim) + '</span>' : '') + (reference.applicability ? '<small>' + escapeHtml(reference.applicability) + '</small>' : ''); return '<li>' + (/^https?:\/\//i.test(String(reference.url || "")) ? '<a href="' + escapeHtml(reference.url) + '" target="_blank" rel="noopener noreferrer">' + body + '</a>' : body) + '</li>'; }).join("") + '</ul></div>';
  } else if (layer === "hypothesis") {
    primary = '<p>' + escapeHtml(item.claim || "이 규칙 묶음이 설명하는 투자 가설입니다.") + '</p>';
    if (item.stateLabel || item.state) meta.push("근거 상태 " + (item.stateLabel || item.state));
    if (item.horizon) meta.push("관측 기간 " + item.horizon);
    if (item.selected) meta.push("AI 선택 가설");
  } else if (layer === "trace") {
    primary = '<p>TypeDB에서 규칙 조건과 관계가 맞았던 실행 기록입니다.</p>';
    if (item.dataState) meta.push("자료 " + item.dataState);
    if (item.freshnessStatus) meta.push("최신성 " + item.freshnessStatus);
  } else {
    primary = '<p>' + escapeHtml(item.reason || item.claim || "최종 판단 단계입니다.") + '</p>';
    if (item.abstained) meta.push("AI 판단 유보");
  }
  var technical = [item.id ? "ID " + item.id : "", item.ruleId ? "규칙 " + item.ruleId : "", item.selectedHypothesisId ? "선택 가설 " + item.selectedHypothesisId : ""].filter(Boolean);
  return '<article class="oa-reasoning-item" data-reasoning-layer="' + escapeHtml(layer || "decision") + '"><header><strong>' + escapeHtml(title) + '</strong><span>' + escapeHtml(investmentReasoningRoleLabel(item.evidenceRole || item.role || item.polarity)) + '</span></header>' + primary + (meta.length ? '<small>' + escapeHtml(meta.join(" · ")) + '</small>' : '') + renderInvestmentReasoningConditions(item.conditions) + knowledgeDetail + (technical.length ? '<details class="oa-reasoning-technical"><summary>감사 식별자</summary><code>' + escapeHtml(technical.join(" · ")) + '</code></details>' : '') + '</article>';
}

function renderInvestmentReasoningNode(node) {
  var items = Array.isArray(node.items) ? node.items : [];
  var traces = Array.isArray(node.traces) ? node.traces : [];
  var refs = Array.isArray(node.refIds) ? node.refIds : [];
  var count = items.length || refs.length;
  return '<li><span>' + escapeHtml(node.label || node.layer || "단계") + '</span><em>' + escapeHtml(count ? count + "개 근거" : "설명 단계") + '</em>' + ((items.length || traces.length) ? '<details class="oa-reasoning-node-detail"><summary>상세 보기</summary><div>' + items.map(function (item) { return renderInvestmentReasoningItem(node.layer, item); }).join("") + (traces.length ? '<div class="oa-reasoning-trace-inline"><strong>규칙 실행 기록</strong>' + traces.map(function (item) { return renderInvestmentReasoningItem("trace", item); }).join("") + '</div>' : '') + '</div></details>' : '') + '</li>';
}

function renderInvestmentReasoningInventory(reasoning) {
  var groups = [
    ["facts", "확인된 사실", "fact"],
    ["relations", "성립한 관계", "relation"],
    ["rules", "적용된 규칙", "rule"],
    ["traces", "규칙 실행 기록", "trace"],
    ["hypotheses", "비교한 가설", "hypothesis"]
  ];
  return '<div class="oa-reasoning-inventory">' + groups.map(function (group) {
    var items = Array.isArray(reasoning[group[0]]) ? reasoning[group[0]] : [];
    return '<details class="oa-reasoning-group"><summary><span><strong>' + escapeHtml(group[1]) + '</strong><em>' + escapeHtml(items.length + "개") + '</em></span></summary>' + (items.length ? '<div>' + items.map(function (item) { return renderInvestmentReasoningItem(group[2], item); }).join("") + '</div>' : '<p>이 판단에 저장된 항목이 없습니다.</p>') + '</details>';
  }).join("") + '</div>';
}

function renderInvestmentCaseReasoning(detail) {
  var explanation = detail.explanation || {};
  var comparison = explanation.comparison || {};
  var paths = Array.isArray(explanation.causalPaths) ? explanation.causalPaths : [];
  var reasoning = detail.reasoning && typeof detail.reasoning === "object" ? detail.reasoning : {};
  var counts = reasoning.counts && typeof reasoning.counts === "object" ? reasoning.counts : {};
  var typeDbActions = Array.isArray(comparison.typeDbCandidateActions) ? comparison.typeDbCandidateActions : [];
  var detailDecision = detail.decision || {};
  var finalAction = decisionActionMeta(detailDecision.state === "blocked" ? "BLOCKED" : (comparison.aiFinalAction || detailDecision.action), comparison.aiFinalAction).label;
  var comparisonState = String(comparison.state || "unavailable");
  var finalLabel = comparisonState === "typedb-only" ? "저장된 TypeDB 관찰" : (comparisonState === "ai-only" ? "AI 의견" : "최종 투자 의견");
  var limitations = Array.isArray(reasoning.limitations) ? reasoning.limitations : [];
  var comparisonBody = comparison.comparable
    ? '<div><span><em>TypeDB 행동 후보</em><strong>' + escapeHtml(typeDbActions.map(function (value) { return decisionActionMeta(value, value).label; }).join(" · ")) + '</strong></span><b aria-hidden="true">→</b><span><em>' + escapeHtml(finalLabel) + '</em><strong>' + escapeHtml(finalAction) + '</strong></span></div>'
    : '<div class="not-comparable"><span><em>TypeDB 행동 후보</em><strong>' + escapeHtml(comparisonState === "typedb-only" ? (typeDbActions.length ? typeDbActions.map(function (value) { return decisionActionMeta(value, value).label; }).join(" · ") : "관찰 관계만 저장") : "후보 미저장") + '</strong></span><span><em>' + escapeHtml(finalLabel) + '</em><strong>' + escapeHtml(finalAction) + '</strong></span></div>';
  return [
    '<section class="oa-case-reasoning-compare" data-comparison-state="' + escapeHtml(comparisonState) + '"><header><strong>' + escapeHtml(comparison.label || "TypeDB와 AI 비교 상태") + '</strong>' + renderDecisionInfoButton("type-db-action-candidate", "TypeDB 행동 후보가 저장된 경우에만 AI 최종 의견과 직접 비교합니다.") + '</header>' + comparisonBody + '<p>' + escapeHtml(comparison.reason || "현재 추론 세대의 판단 참여 상태입니다.") + '</p></section>',
    '<section class="oa-reasoning-snapshot" data-snapshot-state="' + escapeHtml(reasoning.snapshotState || "unknown") + '"><div><strong>' + escapeHtml(reasoning.snapshotStateLabel || "추론 상세 연결 상태") + '</strong><p>' + escapeHtml(reasoning.snapshotReason || "이 판단에 연결된 사실·관계·규칙을 확인합니다.") + '</p>' + (limitations.length ? '<ul>' + limitations.map(function (item) { return '<li>' + escapeHtml(item) + '</li>'; }).join("") + '</ul>' : '') + '</div><dl><div><dt>사실</dt><dd>' + escapeHtml(counts.facts || 0) + '</dd></div><div><dt>관계</dt><dd>' + escapeHtml(counts.relations || 0) + '</dd></div><div><dt>규칙</dt><dd>' + escapeHtml(counts.rules || 0) + '</dd></div><div><dt>실행 기록</dt><dd>' + escapeHtml(counts.traces || 0) + '</dd></div></dl></section>',
    '<section class="oa-case-overview-section"><header><strong>사실에서 의견까지 연결</strong>' + renderDecisionInfoButton("reasoning-rule", "사실과 관계가 규칙을 통과해 가설과 최종 의견으로 이어지는 경로입니다.") + '</header>',
    paths.length ? '<div class="oa-case-causal-paths">' + paths.map(function (path) { return '<article><header><strong>' + escapeHtml(path.title || "추론 경로") + '</strong><span>' + escapeHtml(path.selected ? "최종 선택 경로" : path.researchLead ? "AI 연구 선두" : "비교 경로") + '</span></header><ol>' + (Array.isArray(path.nodes) ? path.nodes : []).map(renderInvestmentReasoningNode).join("") + '</ol></article>'; }).join("") + '</div>' : '<p class="oa-decision-empty-note">사용자에게 설명할 수 있는 추론 경로가 아직 저장되지 않았습니다.</p>',
    '</section>',
    '<section class="oa-case-overview-section"><header><strong>전체 추론 상세</strong>' + renderDecisionInfoButton("reasoning-detail", "판단 당시 사용한 사실값, TypeDB 관계, 성립 규칙과 조건, 가설을 종류별로 확인합니다.") + '</header>' + renderInvestmentReasoningInventory(reasoning) + '</section>',
    '<section class="oa-case-overview-section"><header><strong>비교한 가설</strong>' + renderDecisionInfoButton("competing-hypothesis", "한 방향의 설명만 선택하지 않고 지지와 반박을 함께 비교합니다.") + '</header>' + renderInvestmentCaseScenarios(detail) + '</section>'
  ].join("");
}

function renderInvestmentCaseScenarios(detail) {
  var scenarios = Array.isArray(detail.scenarios) ? detail.scenarios : [];
  if (!scenarios.length) return renderConsoleEmpty("비교할 시나리오가 없습니다", "다음 추론 세대에서 경쟁 가설이 만들어지면 여기에 표시합니다.");
  var verdictLabels = { supported: "지지", weakened: "약화", rejected: "기각", unresolved: "미해결", unreviewed: "미검토" };
  return '<div class="oa-case-scenario-list">' + scenarios.map(function (item) {
    var assumptions = Array.isArray(item.assumptions) ? item.assumptions : [];
    var invalidations = Array.isArray(item.invalidationConditions) ? item.invalidationConditions : [];
    var qualification = item.qualification && typeof item.qualification === "object" ? item.qualification : {};
    var aiReview = item.aiReview && typeof item.aiReview === "object" ? item.aiReview : {};
    var qualificationMeta = investmentHypothesisQualificationMeta(qualification);
    var decisiveCount = Number(qualification.decisiveOutcomeCount || 0);
    var hitRate = qualification.directionalHitRate === undefined || qualification.directionalHitRate === null
      ? "기록 없음"
      : (Number(qualification.directionalHitRate) * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 }) + "%";
    var adjustedReturn = qualification.actionReturnAvailable === true
      ? ((Number(qualification.averageActionAdjustedReturnPct || 0) > 0 ? "+" : "") + Number(qualification.averageActionAdjustedReturnPct || 0).toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + "%")
      : "표본 없음";
    var observationCount = Number(((item.observationState || {}).sampleCount) || 0);
    return [
      '<article class="oa-case-scenario' + (item.selected || item.researchLead ? " selected" : "") + '">',
      '<header><div><span>' + escapeHtml(item.selected ? "최종 선택 가설" : item.researchLead ? "AI 연구 선두" : "비교 가설") + '</span><strong>' + escapeHtml(item.title || "투자 시나리오") + '</strong></div><b data-flow-state="' + escapeHtml(qualificationMeta.tone) + '">' + escapeHtml(qualificationMeta.label) + '</b></header>',
      '<p>' + escapeHtml(item.claim || "설명 문장이 없습니다.") + '</p>',
      aiReview.reasoning ? '<p class="oa-case-scenario-qualification"><strong>AI 평가 · ' + escapeHtml(verdictLabels[aiReview.verdict] || aiReview.verdict || "미평가") + '</strong> ' + escapeHtml(aiReview.reasoning) + '</p>' : '',
      '<div class="oa-case-scenario-metrics"><span>지지 <strong>' + escapeHtml(item.supportCount || 0) + '</strong></span><span>반박 <strong>' + escapeHtml(item.counterCount || 0) + '</strong></span><span>독립 결과 <strong>' + escapeHtml(decisiveCount + "건") + '</strong></span><span>방향 적중 <strong>' + escapeHtml(hitRate) + '</strong></span><span>행동조정 수익 <strong>' + escapeHtml(adjustedReturn) + '</strong></span><span>추적 기록 <strong>' + escapeHtml(observationCount + "건") + '</strong></span></div>',
      qualification.reason ? '<p class="oa-case-scenario-qualification">' + escapeHtml(qualification.reason) + '</p>' : '',
      (assumptions.length || invalidations.length) ? '<details><summary>전제와 무효화 조건</summary>' + (assumptions.length ? '<strong>전제</strong><ul>' + assumptions.map(function (value) { return '<li>' + escapeHtml(value) + '</li>'; }).join("") + '</ul>' : '') + (invalidations.length ? '<strong>무효화 조건</strong><ul>' + invalidations.map(function (value) { return '<li>' + escapeHtml(value) + '</li>'; }).join("") + '</ul>' : '') + '</details>' : '',
      '</article>'
    ].join("");
  }).join("") + '</div>';
}

export { investmentReasoningValue, renderInvestmentCaseReasoning };
