import { renderStrategySectionBar } from "./navigation.mjs";
import { decisionActionMeta, filteredConsoleDecisionRows, selectConsoleDecisionRows } from "./selectors.mjs";
import { renderStrategySectionContent } from "./strategy.mjs";
import { validationOperatorDetailType } from "../experiments/validation.mjs";
import { editorWorkDetailPayload } from "../navigation/detail.mjs";
import { renderDecisionInfoButton } from "../settings/language.mjs";
import { consolePageSlice, renderConsoleEmpty, renderConsoleLiveRegion, renderConsoleManagedPage, renderConsolePager, renderConsoleSurface } from "../shared/console.mjs";
import { recordChangedAtValue, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { renderManagedPage } from "../shell/pages.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { shellState } from "../state/shell.mjs";
import { decisionInView } from "./recency.mjs";

function renderDecisionConsoleRow(row) {
  var detailType = row.detailType === "subject-decision-case"
    ? "investment-case"
    : (row.caseId || row.decisionEpisodeId ? "investment-case" : "investment-action");
  var detailKey = row.subjectCaseId || row.caseId || row.decisionEpisodeId || row.key;
  var readinessTone = row.attentionState === "action" ? "watch" : investmentFlowStateTone(row.readinessState || (row.blocked ? "blocked" : "warning"));
  var primary = (row.explanation || {}).primaryCause || {};
  var attention = row.attention && typeof row.attention === "object" ? row.attention : {};
  var primaryIssue = attention.primaryIssue && typeof attention.primaryIssue === "object"
    ? attention.primaryIssue
    : ((Array.isArray(row.attentionIssues) ? row.attentionIssues : [])[0] || {});
  var readinessBlocked = ["blocked", "error"].indexOf(String(row.readinessState || "")) >= 0;
  var causeLabel = readinessBlocked ? "사용 제한 이유" : "핵심 원인";
  var causeText = readinessBlocked
    ? (primaryIssue.reason || primary.summary || row.reason || "현재 의견을 사용할 수 없는 이유를 확인하세요.")
    : (primary.summary || row.reason || "판단 근거를 확인하세요.");
  return [
    '<button class="oa-case-row" type="button" data-decision-tone="' + escapeHtml(row.tone || "hold") + '" data-flow-state="' + escapeHtml(row.readinessState || "warning") + '" data-console-row-key="' + escapeHtml(row.key) + '" data-work-detail="' + escapeHtml(detailType) + '" data-work-detail-key="' + escapeHtml(detailKey) + '">',
    '<header><span class="oa-case-identity"><strong>' + escapeHtml(row.name || row.symbol) + '</strong><em>' + escapeHtml([row.symbol, row.source === "watchlist" ? "관심" : (row.source === "holding" ? "보유" : "보유·관심 상태 미확인"), row.accountLabel].filter(Boolean).join(" · ")) + '</em></span><span class="oa-case-state"><b class="' + escapeHtml(row.tone || "hold") + '">' + escapeHtml(row.actionLabel || "관찰") + '</b><em class="' + escapeHtml(readinessTone) + '">' + escapeHtml(row.attentionLabel || row.readinessLabel || "확인 필요") + '</em></span></header>',
    '<div class="oa-case-reason"><span>' + escapeHtml(causeLabel) + '</span><strong>' + escapeHtml(causeText) + '</strong></div>',
    row.recency && row.recency.state !== "current" ? '<p class="oa-case-recency caution">' + escapeHtml(row.recency.label) + '</p>' : '',
    '<footer><span>' + renderRecordChangedAt(row) + '<em>' + escapeHtml(row.quality.label || "자료 확인") + '</em></span><b aria-hidden="true">근거 보기 →</b></footer>',
    '</button>'
  ].join("");
}

function decisionDimensionTermId(dimensionId) {
  return {
    decision: "decision-readiness",
    data: "data-state",
    inference: "inference-state",
    ai: "ai-validation-state",
    outcome: "decision-action"
  }[String(dimensionId || "")] || "decision-readiness";
}

function renderDecisionStatusDimensions(dimensions, compact) {
  var rows = Array.isArray(dimensions) ? dimensions : [];
  if (!rows.length) return '<p class="oa-decision-empty-note">세부 상태를 불러오는 중입니다.</p>';
  return '<div class="oa-decision-dimensions' + (compact ? " compact" : "") + '">' + rows.map(function (item) {
    var tone = investmentFlowStateTone(item.state || "warning");
    return [
      '<article data-flow-state="' + escapeHtml(item.state || "warning") + '">',
      '<header><span>' + escapeHtml(item.label || "상태") + '</span>' + renderDecisionInfoButton(decisionDimensionTermId(item.id), item.effect || "현재 판단에서 이 상태를 별도로 확인합니다.") + '</header>',
      '<strong class="' + escapeHtml(tone) + '">' + escapeHtml(item.stateLabel || "확인 필요") + '</strong>',
      '<p>' + escapeHtml(item.reason || "상태 원인을 확인하고 있습니다.") + '</p>',
      '</article>'
    ].join("");
  }).join("") + '</div>';
}

function decisionExplanationRows(explanation, key) {
  var rows = Array.isArray(explanation[key]) ? explanation[key] : [];
  if (key === "supportingCauses" && !rows.length && Array.isArray(explanation.topCauses)) rows = explanation.topCauses;
  return rows;
}

function renderDecisionCauseList(rows, emptyText) {
  rows = Array.isArray(rows) ? rows : [];
  if (!rows.length) return '<p class="oa-decision-empty-note">' + escapeHtml(emptyText || "확인된 항목이 없습니다.") + '</p>';
  return '<ul class="oa-decision-cause-list">' + rows.map(function (item) {
    return '<li><strong>' + escapeHtml(item.title || "판단 근거") + '</strong><span>' + escapeHtml(item.summary || item.effect || "세부 설명 확인 중") + '</span>' + (item.effect && item.summary ? '<em>' + escapeHtml(item.effect) + '</em>' : '') + '</li>';
  }).join("") + '</ul>';
}

function investmentCaseStage(detail, stageId) {
  return (Array.isArray((detail || {}).stages) ? detail.stages : []).filter(function (stage) {
    return String(stage.id || "") === String(stageId || "");
  })[0] || {};
}

function renderInvestmentDecisionRationale(detail, compact) {
  detail = detail && typeof detail === "object" ? detail : {};
  var decision = detail.decision && typeof detail.decision === "object" ? detail.decision : {};
  var explanation = detail.explanation && typeof detail.explanation === "object" ? detail.explanation : {};
  var primary = explanation.primaryCause && typeof explanation.primaryCause === "object" ? explanation.primaryCause : {};
  var stateValue = String(detail.readinessState || decision.state || "warning");
  var blocked = stateValue === "blocked" || stateValue === "error";
  var decisionBlocked = decision.state === "blocked" || decision.state === "error";
  var availabilityLimited = blocked && !decisionBlocked;
  var attention = detail.attention && typeof detail.attention === "object" ? detail.attention : {};
  var primaryIssue = attention.primaryIssue && typeof attention.primaryIssue === "object" ? attention.primaryIssue : {};
  var title = availabilityLimited
    ? (primaryIssue.stateLabel || primaryIssue.label || "현재 의견 사용 제한")
    : (primary.title || (blocked ? "판단 조건을 충족하지 못했습니다" : "현재 의견의 핵심 근거"));
  var summary = availabilityLimited
    ? (primaryIssue.reason || "현재 의견의 근거 기록을 완전하게 검증할 수 없습니다.")
    : (primary.summary || decision.reason || detail.headline || "판단 이유를 확인하고 있습니다.");
  var effect = availabilityLimited
    ? (primaryIssue.effect || "근거가 복구되기 전에는 현재 의견을 투자 행동에 사용하지 않습니다.")
    : (primary.effect || (blocked ? "매수·매도 행동을 확정하지 않습니다." : "현재 투자 의견의 방향과 강도에 반영했습니다."));
  var signal = investmentCaseStage(detail, "signal");
  var investmentCase = investmentCaseStage(detail, "case");
  var dimensions = Array.isArray(detail.statusDimensions) ? detail.statusDimensions : [];
  var dataDimension = dimensions.filter(function (item) { return item.id === "data"; })[0] || {};
  var facts = [
    availabilityLimited && detail.headline ? { label: "저장된 의견 근거", value: detail.headline } : null,
    signal.detail ? { label: "핵심 신호", value: signal.detail } : null,
    investmentCase.detail ? { label: "비교 가설", value: investmentCase.detail } : null,
    dataDimension.stateLabel ? { label: "판단 자료", value: dataDimension.stateLabel } : null
  ].filter(Boolean);
  return [
    '<section class="oa-decision-rationale' + (compact ? " compact" : "") + '" data-flow-state="' + escapeHtml(stateValue) + '">',
    '<header><div><span>' + escapeHtml(availabilityLimited ? "DECISION LIMITED" : (blocked ? "DECISION HOLD" : "DECISION BASIS")) + '</span><strong>' + escapeHtml(availabilityLimited ? "왜 현재 의견을 사용할 수 없나" : (blocked ? "왜 판단을 유보했나" : "왜 이런 판단인가")) + '</strong></div><b>' + escapeHtml(detail.readinessLabel || decision.stateLabel || (blocked ? "판단 유보" : "판단 가능")) + '</b></header>',
    '<div class="oa-decision-rationale-body"><strong>' + escapeHtml(title) + '</strong><p>' + escapeHtml(summary) + '</p></div>',
    facts.length ? '<dl>' + facts.map(function (item) { return '<div><dt>' + escapeHtml(item.label) + '</dt><dd>' + escapeHtml(item.value) + '</dd></div>'; }).join("") + '</dl>' : '',
    '<footer><span>판단에 미친 영향</span><strong>' + escapeHtml(effect) + '</strong></footer>',
    '</section>'
  ].join("");
}

function renderDecisionFilterToolbar() {
  return [
    '<form class="oa-decision-tools" data-console-decision-form>',
    '<label class="oa-decision-search"><span>의견 검색</span><input type="search" data-console-decision-search value="' + escapeHtml(decisionsState.consoleDecisionSearch || "") + '" placeholder="회사명, 코드, 근거 검색" /></label>',
    '<details class="oa-decision-filter-sheet"><summary>필터</summary><div>',
    '<label><span>범위</span><select data-console-decision-filter="scope"><option value="all"' + (decisionsState.consoleDecisionScope === "all" ? " selected" : "") + '>전체</option><option value="holding"' + (decisionsState.consoleDecisionScope === "holding" ? " selected" : "") + '>보유</option><option value="watchlist"' + (decisionsState.consoleDecisionScope === "watchlist" ? " selected" : "") + '>관심</option></select></label>',
    '<label><span>행동</span><select data-console-decision-filter="action"><option value="all"' + (decisionsState.consoleDecisionAction === "all" ? " selected" : "") + '>전체 행동</option><option value="BUY_REVIEW"' + (decisionsState.consoleDecisionAction === "BUY_REVIEW" ? " selected" : "") + '>매수 검토 전체</option><option value="SELL_REVIEW"' + (decisionsState.consoleDecisionAction === "SELL_REVIEW" ? " selected" : "") + '>매도 검토 전체</option><option value="BUY"' + (decisionsState.consoleDecisionAction === "BUY" ? " selected" : "") + '>매수</option><option value="ADD"' + (decisionsState.consoleDecisionAction === "ADD" ? " selected" : "") + '>추가매수</option><option value="HOLD"' + (decisionsState.consoleDecisionAction === "HOLD" ? " selected" : "") + '>유지</option><option value="TRIM"' + (decisionsState.consoleDecisionAction === "TRIM" ? " selected" : "") + '>축소</option><option value="SELL"' + (decisionsState.consoleDecisionAction === "SELL" ? " selected" : "") + '>매도</option><option value="BLOCKED"' + (decisionsState.consoleDecisionAction === "BLOCKED" ? " selected" : "") + '>보류</option></select></label>',
    '<label><span>데이터</span><select data-console-decision-filter="quality"><option value="all"' + (decisionsState.consoleDecisionQuality === "all" ? " selected" : "") + '>전체 품질</option><option value="actual"' + (decisionsState.consoleDecisionQuality === "actual" ? " selected" : "") + '>실데이터</option><option value="mock"' + (decisionsState.consoleDecisionQuality === "mock" ? " selected" : "") + '>Mock</option><option value="issue"' + (decisionsState.consoleDecisionQuality === "issue" ? " selected" : "") + '>지연·부족</option></select></label>',
    '<label><span>판단 상태</span><select data-console-decision-filter="status"><option value="all"' + (decisionsState.consoleDecisionStatus === "all" ? " selected" : "") + '>전체 상태</option><option value="action"' + (decisionsState.consoleDecisionStatus === "action" ? " selected" : "") + '>행동 검토</option><option value="review"' + (decisionsState.consoleDecisionStatus === "review" ? " selected" : "") + '>근거 확인</option><option value="blocked"' + (decisionsState.consoleDecisionStatus === "blocked" ? " selected" : "") + '>판단 보류</option><option value="system"' + (decisionsState.consoleDecisionStatus === "system" ? " selected" : "") + '>운영 점검</option><option value="observe"' + (decisionsState.consoleDecisionStatus === "observe" ? " selected" : "") + '>관찰 유지</option></select></label>',
    '</div></details>',
    '</form>'
  ].join("");
}

function renderDecisionViewSwitch(rows) {
  rows = Array.isArray(rows) ? rows : [];
  var counts = {
    attention: rows.filter(function (row) { return decisionInView(row, "attention", Date.now()); }).length,
    action: rows.filter(function (row) { return decisionInView(row, "action", Date.now()); }).length,
    review: rows.filter(function (row) { return decisionInView(row, "review", Date.now()); }).length,
    recent: rows.filter(function (row) { return decisionInView(row, "recent", Date.now()); }).length,
    all: rows.length
  };
  var items = [["attention", "지금 확인"], ["review", "재확인"], ["all", "전체 기록"]];
  if (decisionsState.consoleDecisionView === "action") items.splice(1, 0, ["action", "주문 검토"]);
  if (decisionsState.consoleDecisionView === "recent") items.splice(1, 0, ["recent", "최근 변화"]);
  return '<nav class="oa-decision-view-switch" aria-label="투자 의견 범위">' + items.map(function (item) {
    var active = decisionsState.consoleDecisionView === item[0];
    return '<button type="button" data-decision-view="' + item[0] + '"' + (active ? ' class="active" aria-current="page"' : '') + '><strong>' + item[1] + '</strong><span>' + escapeHtml(counts[item[0]]) + '</span></button>';
  }).join("") + '</nav>';
}

function decisionQueueWorkDetailPayload() {
  var rows = filteredConsoleDecisionRows(shellState.snapshot || {});
  var page = consolePageSlice(rows, "decision", 12);
  var body = page.items.length
    ? '<div class="oa-case-list" data-console-keyed-list="decision-full">' + page.items.map(renderDecisionConsoleRow).join("") + '</div>'
    : renderConsoleEmpty("현재 판단 후보가 없습니다", "TypeDB 추론과 투자 분석 데이터가 생성되면 표시합니다.");
  return editorWorkDetailPayload(
    "Action Queue",
    "전체 투자 행동 후보",
    "canonical 판단 큐 " + rows.length + "건",
    '<section class="oa-detail-queue">' + renderConsoleLiveRegion("decision-full-body", body) + renderConsolePager("decision", page) + '</section>'
  );
}

function renderInvestmentInsightAssessmentCard(value) {
  var assessment = value && typeof value === "object" ? value : {};
  if (assessment.publishable !== true) return "";
  var direction = String(assessment.direction || "balanced");
  var tone = direction === "positive" ? "pass" : direction === "negative" ? "warning" : "pending";
  var catalysts = Array.isArray(assessment.catalysts) ? assessment.catalysts : [];
  var risks = Array.isArray(assessment.risks) ? assessment.risks : [];
  var meta = [assessment.directionLabel, assessment.horizonLabel, assessment.convictionLabel].filter(Boolean).join(" · ");
  return [
    '<section class="oa-decision-rationale compact" data-flow-state="' + escapeHtml(tone) + '">',
    '<header><div><span>INVESTMENT INSIGHT</span><strong>' + escapeHtml(assessment.directionLabel || "투자 관점") + '</strong></div><b>' + escapeHtml(assessment.horizonLabel || "기간 확인") + '</b></header>',
    '<div class="oa-decision-rationale-body"><p><strong>핵심 판단</strong> · ' + escapeHtml(assessment.dominantThesis || "") + '</p><p><strong>작동 경로</strong> · ' + escapeHtml(assessment.causalMechanism || "") + '</p><p><strong>투자 의미</strong> · ' + escapeHtml(assessment.investmentImplication || "") + '</p></div>',
    '<footer><span>근거 수준</span><strong>' + escapeHtml(meta || "검증된 근거 기반") + '</strong></footer>',
    '</section>',
    catalysts.length || risks.length ? '<div class="oa-assurance-groups">' + (catalysts.length ? '<section class="oa-assurance-group"><header><div><strong>관점을 강화할 촉매</strong><p>현재 설명을 더 강하게 만들 다음 사건입니다.</p></div><span>' + escapeHtml(catalysts.length) + '개</span></header><ul class="oa-decision-cause-list">' + catalysts.slice(0, 3).map(function (item) { return '<li><span>' + escapeHtml(item) + '</span></li>'; }).join("") + '</ul></section>' : '') + (risks.length ? '<section class="oa-assurance-group"><header><div><strong>반대 시나리오</strong><p>현재 관점을 약화하거나 뒤집을 수 있는 근거입니다.</p></div><span>' + escapeHtml(risks.length) + '개</span></header><ul class="oa-decision-cause-list">' + risks.slice(0, 3).map(function (item) { return '<li><span>' + escapeHtml(item) + '</span></li>'; }).join("") + '</ul></section>' : '') + '</div>' : '',
    assessment.invalidationCondition ? '<section class="oa-assurance-context"><span>VIEW INVALIDATION</span><strong>판단이 바뀌는 조건</strong><p>' + escapeHtml(assessment.invalidationCondition) + '</p></section>' : ''
  ].join("");
}

function renderSubjectDecisionAIInsight(detail) {
  var ai = detail && detail.aiInsight && typeof detail.aiInsight === "object"
    ? detail.aiInsight
    : {};
  var status = String(ai.status || "not-run");
  var statusMeta = {
    completed: { label: "현재 세대 해석 완료", tone: "watch" },
    pending: { label: "AI 처리 중", tone: "hold" },
    fallback: { label: "AI 실패 · TypeDB 대체", tone: "caution" },
    "contract-failed": { label: "AI 결과 미채택", tone: "caution" },
    "previous-generation": { label: "이전 세대 해석", tone: "caution" },
    "not-run": { label: "현재 세대 미실행", tone: "hold" }
  }[status] || { label: status, tone: "hold" };
  if (!ai.episodeId && status !== "completed") {
    return [
      '<section class="oa-assurance-context" data-flow-state="' + escapeHtml(status === "pending" ? "pending" : "pass") + '">',
      '<span>AI INTERPRETATION</span>',
      '<strong>' + escapeHtml(statusMeta.label) + '</strong>',
      '<p>' + escapeHtml(ai.reason || "현재 TypeDB 세대에는 연결된 AI 해석이 없습니다.") + '</p>',
      '</section>'
    ].join("");
  }
  var validationLabels = {
    ready: "검증 완료",
    verified: "검증 완료",
    conditional: "조건부 사용",
    blocked: "판단 사용 제한"
  };
  var notificationLabel = ai.notificationDeliveryLabel || (ai.notificationDecision === "send" ? "발송 요청" : "웹 기록만");
  var evidence = Array.isArray(ai.evidence) ? ai.evidence : [];
  var counterEvidence = Array.isArray(ai.counterEvidence) ? ai.counterEvidence : [];
  var nextChecks = Array.isArray(ai.nextChecks) ? ai.nextChecks : [];
  var hypotheses = Array.isArray(ai.hypotheses) ? ai.hypotheses : [];
  var insightAssessment = ai.insightAssessment && typeof ai.insightAssessment === "object" ? ai.insightAssessment : {};
  var assessmentBody = renderInvestmentInsightAssessmentCard(insightAssessment);
  var researchLeadId = String(ai.researchLeadHypothesisId || "");
  var verdictLabels = {
    supported: "지지",
    weakened: "약화",
    rejected: "기각",
    unresolved: "미해결",
    unreviewed: "미검토"
  };
  var interpretation = insightAssessment.dominantThesis || ai.summary || ai.investmentView || "AI 해석 요약이 저장되지 않았습니다.";
  var evidenceBody = evidence.length || counterEvidence.length ? [
    '<div class="oa-assurance-groups">',
    evidence.length ? '<section class="oa-assurance-group"><header><div><strong>AI가 사용한 근거</strong><p>TypeDB 후보 안에서 비교한 근거입니다.</p></div><span>' + escapeHtml(evidence.length) + '개</span></header><ul class="oa-decision-cause-list">' + evidence.map(function (item) { return '<li><span>' + escapeHtml(item) + '</span></li>'; }).join("") + '</ul></section>' : '',
    counterEvidence.length ? '<section class="oa-assurance-group"><header><div><strong>반대 근거</strong><p>현재 설명을 약화할 수 있는 근거입니다.</p></div><span>' + escapeHtml(counterEvidence.length) + '개</span></header><ul class="oa-decision-cause-list">' + counterEvidence.map(function (item) { return '<li><span>' + escapeHtml(item) + '</span></li>'; }).join("") + '</ul></section>' : '',
    '</div>'
  ].join("") : '';
  var hypothesisBody = hypotheses.length ? [
    '<section class="oa-assurance-group"><header><div><strong>AI 가설 비교</strong><p>같은 TypeDB 추론 세대의 가설만 비교했습니다.</p></div><span>' + escapeHtml(hypotheses.length) + '개</span></header><div>',
    hypotheses.map(function (item) {
      var hypothesisId = String(item.hypothesisId || "");
      var lead = Boolean(researchLeadId && hypothesisId === researchLeadId);
      return [
        '<article class="oa-assurance-row" data-flow-state="' + escapeHtml(item.verdict === "supported" ? "pass" : item.verdict === "rejected" ? "warning" : "pending") + '">',
        '<div class="oa-assurance-row-main"><span>' + escapeHtml(lead ? "연구 선두 가설" : "비교 가설") + '</span><strong>' + escapeHtml(item.claim || item.templateLabel || "투자 가설") + '</strong><p>' + escapeHtml(item.reasoning || "가설별 분석 이유가 저장되지 않았습니다.") + '</p></div>',
        '<div class="oa-assurance-row-side"><strong>' + escapeHtml(verdictLabels[item.verdict] || item.verdict || "미평가") + '</strong><span>' + escapeHtml(item.decisionEligible ? "판단 후보" : "연구용") + '</span></div>',
        '</article>'
      ].join("");
    }).join(""),
    '</div></section>'
  ].join("") : '';
  return [
    '<section class="oa-assurance-context" data-flow-state="' + escapeHtml(ai.currentGeneration ? "pass" : "warning") + '">',
    '<span>AI INTERPRETATION</span>',
    '<strong>' + escapeHtml(statusMeta.label) + '</strong>',
    '<p>' + escapeHtml(ai.reason || "TypeDB 결과에 연결된 AI 해석입니다.") + '</p>',
    '</section>',
    assessmentBody,
    '<div class="oa-console-metrics">',
    '<article><span>AI 실행</span><strong>' + escapeHtml(ai.model || "모델 미기록") + '</strong><em>' + escapeHtml(ai.reasoningEffort ? "추론 " + ai.reasoningEffort : "추론 강도 미기록") + '</em></article>',
    '<article><span>검증 상태</span><strong>' + escapeHtml(validationLabels[ai.validationState] || ai.validationState || "미기록") + '</strong><em>행동 범위는 TypeDB가 제한</em></article>',
    '<article><span>AI 역할</span><strong>투자 관점·인과 해석</strong><em>주문 실행 권한과 분리</em></article>',
    '<article><span>알림 결과</span><strong>' + escapeHtml(notificationLabel) + '</strong><em>' + escapeHtml(ai.deliveryReason || "발송 정책 결과") + '</em></article>',
    '</div>',
    '<section class="oa-decision-rationale compact" data-flow-state="' + escapeHtml(ai.validationState === "blocked" ? "warning" : "pass") + '">',
    '<header><div><span>AI READING</span><strong>AI가 해석한 의미</strong></div><b>' + escapeHtml(ai.actionLabel || "매매 판단 없음") + '</b></header>',
    '<div class="oa-decision-rationale-body"><p>' + escapeHtml(interpretation) + '</p></div>',
    ai.currentActionPlan ? '<footer><span>현재 대응 설명</span><strong>' + escapeHtml(ai.currentActionPlan) + '</strong></footer>' : '',
    '</section>',
    evidenceBody,
    hypothesisBody,
    ai.epistemicSummary ? '<section class="oa-assurance-context"><span>KNOWN / UNKNOWN</span><strong>확인된 점과 남은 불확실성</strong><p>' + escapeHtml(ai.epistemicSummary) + '</p></section>' : '',
    ai.nextActionPlan || nextChecks.length ? '<section class="oa-assurance-context"><span>NEXT CHECK</span><strong>AI가 제안한 다음 확인</strong><p>' + escapeHtml(ai.nextActionPlan || nextChecks.join(" · ")) + '</p></section>' : '',
    ai.invalidationCondition ? '<section class="oa-assurance-context"><span>INVALIDATION</span><strong>이 해석이 무효가 되는 조건</strong><p>' + escapeHtml(ai.invalidationCondition) + '</p></section>' : '',
    '<section class="oa-assurance-context"><span>AI TRACE</span><strong>분리 저장된 AI 인사이트</strong><p>' + escapeHtml([ai.episodeId, ai.inferenceGenerationId, ai.promptVersion, ai.createdAt].filter(Boolean).join(" · ")) + '</p></section>'
  ].join("");
}

function subjectDecisionCaseWorkDetailPayload(key) {
  var rows = selectConsoleDecisionRows(shellState.snapshot || {});
  var row = rows.filter(function (item) {
    return String(item.subjectCaseId || "") === String(key || "");
  })[0];
  if (!row) {
    return editorWorkDetailPayload(
      "TypeDB Inference",
      "최신 추론을 찾을 수 없습니다",
      String(key || ""),
      renderConsoleEmpty("추론 기록이 갱신되었습니다", "판단 목록을 새로고침해 최신 세대를 확인하세요.")
    );
  }
  var detail = row.subjectDecisionCase || {};
  var hypotheses = Array.isArray(detail.hypotheses) ? detail.hypotheses : [];
  var candidate = decisionActionMeta(detail.candidateAction, detail.candidateAction);
  var dispatch = detail.dispatch || {};
  var dispatchLabel = dispatch.label || "연결 경로 미기록";
  var dispatchReason = dispatch.reason || dispatch.deliveryReason || "과거 추론 기록에는 후속 처리 경로가 저장되지 않았습니다.";
  var hypothesisBody = hypotheses.length ? '<div class="oa-assurance-groups"><section class="oa-assurance-group"><header><div><strong>경쟁 가설</strong><p>같은 TypeDB 세대에서 성립한 대안을 비교합니다.</p></div><span>' + escapeHtml(hypotheses.length) + '개</span></header><div>' + hypotheses.map(function (item) {
    var rules = Array.isArray(item.supportingRuleIds) ? item.supportingRuleIds : [];
    var evidence = Array.isArray(item.supportingEvidenceIds) ? item.supportingEvidenceIds : [];
    var invalidation = Array.isArray(item.invalidationConditions) ? item.invalidationConditions : [];
    return [
      '<article class="oa-assurance-row" data-flow-state="pass">',
      '<div class="oa-assurance-row-main"><span><strong>' + escapeHtml(item.label || item.hypothesisId || "가설") + '</strong><em>' + escapeHtml(item.hypothesisId || "") + '</em></span><b class="watch">' + escapeHtml(decisionActionMeta(item.candidateAction, item.candidateAction).label) + '</b></div>',
      '<p><strong>성립 규칙</strong> · ' + escapeHtml(rules.join(", ") || "규칙 식별자 없음") + '</p>',
      '<p><strong>근거 연결</strong> · ' + escapeHtml(evidence.length + "건") + '</p>',
      invalidation[0] ? '<div class="oa-assurance-next"><span>무효화 조건</span><strong>' + escapeHtml(invalidation[0]) + '</strong></div>' : '',
      '</article>'
    ].join("");
  }).join("") + '</div></section></div>' : renderConsoleEmpty("성립한 예측 가설이 없습니다", "관계 추론은 완료됐지만 현재 기준시각에 행동 후보로 승격할 예측 가설은 없습니다.");
  var checks = Array.isArray(detail.nextChecks) ? detail.nextChecks : [];
  var gaps = Array.isArray(detail.missingData) ? detail.missingData : [];
  var body = [
    '<section class="oa-assurance-context"><span>TYPE DB SUBJECT CASE</span><strong>' + escapeHtml(row.name || row.symbol) + ' · ' + escapeHtml(candidate.label) + '</strong><p>' + escapeHtml(row.reason || "TypeDB 관계와 가설 후보를 확인합니다.") + '</p></section>',
    '<div class="oa-console-metrics"><article><span>현재 단계</span><strong>' + escapeHtml(detail.stage || "-") + '</strong><em>AI·발송과 분리된 추론 상태</em></article><article><span>연결 경로</span><strong>' + escapeHtml(dispatchLabel) + '</strong><em>' + escapeHtml(dispatch.deliveryState || "not-requested") + '</em></article><article><span>가설</span><strong>' + escapeHtml(hypotheses.length + "개") + '</strong><em>현재 세대 후보</em></article><article><span>허용 행동</span><strong>' + escapeHtml((detail.allowedActions || []).length + "개") + '</strong><em>' + escapeHtml((detail.allowedActions || []).join(", ") || "없음") + '</em></article><article><span>자료 공백</span><strong>' + escapeHtml(gaps.length + "개") + '</strong><em>행동 확정 제약</em></article></div>',
    '<section class="oa-assurance-context"><span>TYPE DB OUTPUT ROUTE</span><strong>' + escapeHtml(dispatchLabel) + '</strong><p>' + escapeHtml(dispatchReason) + '</p></section>',
    hypothesisBody,
    renderSubjectDecisionAIInsight(detail),
    '<section class="oa-assurance-context"><span>NEXT VALIDATION</span><strong>다음 판단에서 확인할 조건</strong><p>' + escapeHtml(checks.join(" · ") || "다음 사실 변경에서 동일 가설과 반대 근거를 다시 비교합니다.") + '</p></section>',
    '<section class="oa-assurance-context"><span>TRACE IDENTITY</span><strong>재현 가능한 세대 식별자</strong><p>' + escapeHtml([detail.sourceAboxSnapshotId, detail.inferenceGenerationId, detail.candidateFingerprint].filter(Boolean).join(" · ") || "식별자 없음") + '</p></section>'
  ].join("");
  return editorWorkDetailPayload(
    "TypeDB Inference",
    (row.name || row.symbol) + " 최신 추론",
    "최종 주문 행동이 아닌 현재 세대의 후보와 근거",
    body
  );
}

function decisionOutcomeBoardWorkDetailPayload() {
  var payload = investmentFlowConsolePayload();
  var items = Array.isArray(payload.items) ? payload.items.slice() : [];
  items.sort(function (a, b) {
    var outcomeDiff = Number(((b.outcome || {}).count) || 0) - Number(((a.outcome || {}).count) || 0);
    return outcomeDiff || recordChangedAtValue(b) - recordChangedAtValue(a);
  });
  var recordedCases = items.filter(function (item) { return Number(((item.outcome || {}).count) || 0) > 0; });
  var totalSamples = items.reduce(function (sum, item) { return sum + Number(((item.outcome || {}).count) || 0); }, 0);
  var page = consolePageSlice(items, "decision-outcome", 10);
  var body = page.items.length ? '<div class="oa-assurance-groups"><section class="oa-assurance-group"><header><div><strong>판단 이후 기록</strong><p>판단 당시 의견과 이후 사용자 행동·실행·성과를 분리해서 확인합니다.</p></div><span>' + escapeHtml(items.length) + '건</span></header><div>' + page.items.map(function (item) {
    var outcome = item.outcome || {};
    var decision = item.decision || {};
    var attention = item.attention || {};
    var action = decisionActionMeta(decision.action, decision.action);
    var sampleCount = Number(outcome.count || 0);
    var outcomeLabel = sampleCount ? "성과 기록 " + sampleCount + "건" : "성과 관측 대기";
    return [
      '<article class="oa-assurance-row" data-flow-state="' + escapeHtml(sampleCount ? "pass" : "pending") + '">',
      '<div class="oa-assurance-row-main"><span><strong>' + escapeHtml(item.name || item.symbol || "종목") + '</strong><em>' + escapeHtml([item.symbol, action.label].filter(Boolean).join(" · ")) + '</em></span><b class="' + escapeHtml(sampleCount ? "watch" : "hold") + '">' + escapeHtml(outcomeLabel) + '</b></div>',
      '<p><strong>현재 추적 상태</strong> · ' + escapeHtml(attention.label || item.readinessLabel || "판단 기록 확인") + '</p>',
      '<div class="oa-assurance-next"><span>해석 원칙</span><strong>' + escapeHtml(sampleCount ? "기록된 성과는 당시 판단과 함께 비교하며 인과관계로 단정하지 않습니다." : "다음 관측과 사용자 행동이 기록되면 이 판단과 연결해 비교합니다.") + '</strong></div>',
      '<footer>' + renderRecordChangedAt(item) + '<button class="text-button compact primary" type="button" data-investment-case-tab="history" data-investment-case-key="' + escapeHtml(item.caseId || item.episodeId || "") + '">변화·성과 보기</button></footer>',
      '</article>'
    ].join("");
  }).join("") + '</div></section></div>' : renderConsoleEmpty("추적할 투자 판단이 없습니다", "투자 판단이 생성되면 이후 행동과 성과를 같은 기록에서 추적합니다.");
  return editorWorkDetailPayload(
    "Outcome Tracking",
    "판단 이후 성과 추적",
    "판단 " + items.length + "건 · 성과 표본 " + totalSamples + "건",
    [
      '<section class="oa-detail-queue">',
      '<div class="oa-console-metrics"><article><span>추적 판단</span><strong>' + escapeHtml(items.length) + '건</strong><em>현재 투자 케이스</em></article><article><span>성과 연결</span><strong>' + escapeHtml(recordedCases.length) + '건</strong><em>표본이 연결된 판단</em></article><article><span>성과 표본</span><strong>' + escapeHtml(totalSamples) + '건</strong><em>중복 포함 관측 기록</em></article><article><span>관측 대기</span><strong>' + escapeHtml(items.length - recordedCases.length) + '건</strong><em>향후 자동 비교</em></article></div>',
      '<section class="oa-assurance-context"><span>ATTRIBUTION CONTRACT</span><strong>같이 일어난 일과 원인을 구분합니다.</strong><p>가격 변화, 사용자 주문, 체결, 결과 표본을 시간순으로 연결하지만 해당 판단이 수익을 만들었다고 자동 단정하지 않습니다.</p></section>',
      renderConsoleLiveRegion("decision-outcome-body", body),
      renderConsolePager("decision-outcome", page),
      '</section>'
    ].join("")
  );
}

function renderDecisionConsole(snapshot) {
  var allRows = selectConsoleDecisionRows(snapshot);
  var rows = filteredConsoleDecisionRows(snapshot);
  var page = consolePageSlice(rows, "decision", 10);
  var blocked = allRows.filter(function (row) { return row.blocked; }).length;
  var buy = allRows.filter(function (row) { return row.actionCode === "BUY" || row.actionCode === "ADD"; }).length;
  var sell = allRows.filter(function (row) { return row.actionCode === "SELL" || row.actionCode === "TRIM"; }).length;
  var actionRequired = allRows.filter(function (row) { return decisionInView(row, "action", Date.now()); }).length;
  var reviewRequired = allRows.filter(function (row) { return decisionInView(row, "review", Date.now()); }).length;
  var awaitingOutcome = allRows.filter(function (row) { return String((row.outcome || {}).state || "pending") === "pending"; }).length;
  var metrics = [
    { label: "행동 검토", value: actionRequired + "건", detail: "최근 검토 의견", tone: actionRequired ? "caution" : "neutral", target: { type: "decision", value: "all", key: "action", quality: "all", status: "action" } },
    { label: "매수 검토", value: buy + "건", detail: "조건 확인", tone: buy ? "watch" : "neutral", target: { type: "decision", value: "BUY_REVIEW", key: "all", quality: "all" } },
    { label: "매도 검토", value: sell + "건", detail: "위험 관리", tone: sell ? "danger" : "neutral", target: { type: "decision", value: "SELL_REVIEW", key: "all", quality: "all" } },
    { label: "재확인", value: reviewRequired + "건", detail: "이전 의견·사용 제한", tone: reviewRequired ? "caution" : "neutral", target: { type: "decision", value: "all", key: "review", quality: "all", status: "all" } },
    { label: "판단 보류", value: blocked + "건", detail: "행동 아님", tone: blocked ? "danger" : "watch", target: { type: "tab", value: "experiments" } },
    { label: "결과 대기", value: awaitingOutcome + "건", detail: "성과 관측", target: { type: "decision", value: "all", key: "all", quality: "all" } }
  ];
  var viewLabels = { attention: "지금 확인할 투자 의견", action: "주문 전 검토 의견", review: "근거를 더 확인할 의견", recent: "최근 달라진 의견", all: "전체 투자 의견" };
  var emptyDetail = decisionsState.consoleDecisionView === "action"
    ? "현재 주문을 검토할 의견은 없습니다. 근거 검토에는 행동을 바꿀 수 있는 확인 항목이 표시됩니다."
    : decisionsState.consoleDecisionView === "attention"
    ? "현재 사용자 확인이 필요한 판단 변화가 없습니다."
    : "검색어나 필터를 조정하세요.";
  var list = page.items.length ? '<div class="oa-case-list" data-console-keyed-list="decision-primary">' + page.items.map(renderDecisionConsoleRow).join("") + '</div>' : renderConsoleEmpty("조건에 맞는 투자 의견이 없습니다", emptyDetail, '<button class="text-button primary" type="button" data-decision-view="all">전체 의견 보기</button>');
  return renderConsoleManagedPage("modeling", metrics, [
    renderDecisionViewSwitch(allRows),
    renderDecisionFilterToolbar(),
    '<div data-console-monitor-destination="decisions" tabindex="-1">',
    renderConsoleSurface({ title: viewLabels[decisionsState.consoleDecisionView] || viewLabels.attention, meta: page.items.length + " / " + rows.length + "건", className: "decision-list-surface", body: renderConsoleLiveRegion("decision-primary-body", list), footer: renderConsolePager("decision", page) }),
    '</div>',
    renderDecisionWorkspaceNavigation("modeling")
  ].join(""), { secondaryMetrics: true });
}

function investmentFlowConsolePayload() {
  return decisionsState.investmentFlow && typeof decisionsState.investmentFlow === "object"
    ? decisionsState.investmentFlow
    : { summary: {}, items: [], operatorView: { stages: [], issues: [] } };
}

function investmentCaseForSymbol(symbol) {
  var target = String(symbol || "").toUpperCase();
  if (!target) return null;
  var payload = investmentFlowConsolePayload();
  return (Array.isArray(payload.items) ? payload.items : []).filter(function (item) {
    return String(item.symbol || "").toUpperCase() === target;
  })[0] || null;
}

function renderDecisionWorkspaceNavigation(activeTab) {
  var active = activeTab === "experiments" ? "experiments" : "modeling";
  return [
    '<nav class="oa-decision-workspace-nav" aria-label="투자 판단 작업공간">',
    '<button type="button" data-tab="modeling"' + (active === "modeling" ? ' class="active" aria-current="page"' : '') + '><strong>현재 의견</strong><span>무엇을·왜</span></button>',
    '<button type="button" data-tab="experiments"' + (active === "experiments" ? ' class="active" aria-current="page"' : '') + '><strong>근거 점검</strong><span>부족·차단</span></button>',
    '<button type="button" data-work-detail="investment-model-overview" data-work-detail-key=""><strong>판단 기준</strong><span>모델·규칙</span></button>',
    '<button type="button" data-work-detail="decision-outcome-board" data-work-detail-key=""><strong>성과 추적</strong><span>행동·결과</span></button>',
    '</nav>'
  ].join("");
}

function investmentFlowStateTone(value) {
  var stateValue = String(value || "warning");
  if (stateValue === "pass") return "watch";
  if (stateValue === "error" || stateValue === "blocked") return "danger";
  if (stateValue === "pending") return "hold";
  return "caution";
}

function renderInvestmentFlowStateLegend() {
  var items = [
    { state: "pass", label: "준비됨", detail: "해당 단계 결과 사용 가능" },
    { state: "warning", label: "확인 필요", detail: "일부 조건을 더 확인" },
    { state: "blocked", label: "판단 차단", detail: "행동 의견에 사용 불가" },
    { state: "error", label: "운영 오류", detail: "API·저장·작업 실패" },
    { state: "pending", label: "처리 대기", detail: "아직 실행·관측 전" }
  ];
  return [
    '<aside class="oa-flow-state-legend" aria-label="처리 단계 색상 범례">',
    '<div>', items.map(function (item) {
      return '<span data-flow-state="' + item.state + '"><i aria-hidden="true"></i><b>' + escapeHtml(item.label) + '</b><em>' + escapeHtml(item.detail) + '</em></span>';
    }).join(""), '</div>',
    '<p>빨간 ×는 판단 차단, 빨간 !는 운영 오류입니다. 색상은 주가 방향이나 투자 위험도를 의미하지 않습니다.</p>',
    '</aside>'
  ].join("");
}

function renderInvestmentFlowStages(stages, compact, caseId) {
  var rows = Array.isArray(stages) ? stages : [];
  var userCaseStages = rows.some(function (stage) { return stage.id === "fact" || stage.id === "signal" || stage.id === "case" || stage.id === "outcome"; });
  return '<div class="oa-flow-stage-strip' + (compact ? " compact" : "") + (userCaseStages ? " case-stages" : "") + '" aria-label="판단 생성 단계">' + rows.map(function (stage) {
    var stageLabel = stage.label || stage.id || "단계";
    var stateLabel = stage.stateLabel || "확인 필요";
    var stageAriaLabel = [stageLabel, stateLabel, stage.detail, "상세 열기"].filter(Boolean).join(" · ");
    if (userCaseStages && caseId) {
      var caseTab = stage.id === "case" ? "reasoning" : (stage.id === "outcome" ? "history" : (stage.id === "fact" || stage.id === "signal" ? "evidence" : "summary"));
      return '<button type="button" class="oa-flow-stage" data-flow-state="' + escapeHtml(stage.state || "warning") + '" data-investment-case-tab="' + escapeHtml(caseTab) + '" data-investment-case-key="' + escapeHtml(caseId) + '" title="' + escapeHtml((stage.detail || "") + " · 상세 열기") + '" aria-label="' + escapeHtml(stageAriaLabel) + '"><i aria-hidden="true"></i><strong>' + escapeHtml(stageLabel) + '</strong><em>' + escapeHtml(stateLabel) + '</em></button>';
    }
    return '<button type="button" class="oa-flow-stage" data-flow-state="' + escapeHtml(stage.state || "warning") + '" data-work-detail="' + escapeHtml(validationOperatorDetailType(stage.id)) + '" title="' + escapeHtml((stage.detail || "") + " · 해당 정보 열기") + '" aria-label="' + escapeHtml(stageAriaLabel) + '"><i aria-hidden="true"></i><strong>' + escapeHtml(stageLabel) + '</strong><em>' + escapeHtml(stateLabel) + '</em></button>';
  }).join("") + '</div>';
}

function renderStrategyModelingPage(snapshot) {
  return renderManagedPage("modeling", snapshot, [
    '<section class="admin-grid strategy-view investment-analysis-view">',
    renderStrategySectionBar(),
    renderStrategySectionContent(snapshot),
    '</section>'
  ].join(""));
}

export { decisionExplanationRows, decisionOutcomeBoardWorkDetailPayload, decisionQueueWorkDetailPayload, investmentFlowConsolePayload, investmentFlowStateTone, renderDecisionCauseList, renderDecisionConsole, renderDecisionStatusDimensions, renderDecisionWorkspaceNavigation, renderInvestmentDecisionRationale, renderInvestmentFlowStages, renderInvestmentFlowStateLegend, renderInvestmentInsightAssessmentCard, renderStrategyModelingPage, subjectDecisionCaseWorkDetailPayload };
