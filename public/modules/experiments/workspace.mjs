import { decisionStateMeta } from "../decisions/signals.mjs";
import { renderOntologyExperimentListPanel, renderOntologyExperimentRecommendation } from "./work-detail.mjs";
import { renderInfoIconButton, renderWorkDetailButton } from "../navigation/detail.mjs";
import { normalizeExperimentSection } from "../navigation/routes.mjs";
import { renderStrategyProposalConsolePanel } from "../proposals/workspace.mjs";
import { formatClock, latestChangedFirst, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml, uniqueTextItems } from "../shared/text.mjs";
import { cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { renderManagedPage } from "../shell/pages.mjs";
import { experimentsState } from "../state/experiments.mjs";
import { hypothesesState } from "../state/hypotheses.mjs";

function renderOntologyExperimentsPage(snapshot) {
  var experiments = ontologyExperimentItems();
  var section = normalizeExperimentSection(experimentsState.activeExperimentSection);
  experimentsState.activeExperimentSection = section;
  syncActiveOntologyExperimentId();
  return renderManagedPage("experiments", snapshot, [
    '<section class="admin-grid ontology-experiments-view">',
    renderOntologyExperimentSectionBar(),
    renderOntologyExperimentSectionContent(section, experiments),
    '</section>'
  ].join(""));
}

function ontologyExperimentTooltipAttrs(text) {
  var value = String(text || "").trim();
  if (!value) return "";
  return ' data-lab-tooltip="' + escapeHtml(value) + '" title="' + escapeHtml(value) + '" aria-description="' + escapeHtml(value) + '"';
}

function ontologyExperimentSectionTooltip(sectionId) {
  var descriptions = {
    overview: "실험 현황, 최근 실행 상태, AI 후보 제안과 활성 실험 실행을 확인합니다.",
    validation: "선택한 실험을 기준으로 백테스트, 리플레이, 후보 비교 결과를 검토합니다.",
    promotion: "검증을 통과한 실험을 운영 RuleBox에 반영할 수 있는지 심사합니다.",
    audit: "실험 실행, 승격 판정, 운영 반영 이력을 시간순으로 확인합니다.",
    proposals: "전략 제안 승인 큐에서 검증된 가설의 조건, 성과, 이력을 검토합니다."
  };
  return descriptions[sectionId] || "전략 검증 화면의 하위 섹션으로 이동합니다.";
}

function ontologyExperimentActionTooltip(action) {
  var descriptions = {
    refresh: "전략 검증 실험 목록과 최근 실행 결과를 다시 불러옵니다.",
    suggest: "최근 판단 실패와 누락 근거를 분석해 새 RuleBox 후보 실험을 만듭니다.",
    runActive: "활성 상태의 실험들을 현재 스냅샷으로 샌드박스 검증합니다.",
    select: "이 실험을 검증, 승격, 상세 패널의 기준 대상으로 선택합니다.",
    detail: "선택한 실험의 가설, 후보 규칙, 승격 체크리스트를 상세 레이어로 엽니다.",
    run: "이 실험만 샌드박스에서 실행해 관계 변화와 승격 조건을 다시 계산합니다.",
    apply: "승격 조건을 통과한 검증 결과를 운영 RuleBox 기준에 반영합니다.",
    applyRecommendation: "AI 보완 제안이 포함된 실험 결과를 운영 기준에 적용합니다.",
    applySelectedRecommendations: "체크한 보완 제안만 묶어서 운영 RuleBox와 TBox 기준에 적용합니다.",
    selectAllRecommendations: "현재 실험에서 자동 적용 가능한 보완 제안을 모두 선택합니다.",
    clearRecommendations: "현재 실험의 보완 제안 선택을 모두 해제합니다.",
    pause: "이 실험을 자동 검증 대상에서 제외해 다음 실행부터 멈춥니다.",
    activate: "이 실험을 활성 상태로 바꿔 다음 자동 검증 대상에 포함합니다."
  };
  return descriptions[action] || "전략 검증 작업을 실행합니다.";
}

function renderOntologyExperimentSectionBar() {
  var activeId = normalizeExperimentSection(experimentsState.activeExperimentSection);
  return [
    '<div class="strategy-section-bar ontology-experiment-section-bar experiment-drilldown-bar">',
    '<div class="strategy-section-tabs ontology-experiment-section-tabs experiment-drilldown-rail" role="toolbar" aria-label="전략 검증 상세 보기">',
    renderWorkDetailButton("experiment-validation-board", "", "검증", "text-button compact", ontologyExperimentTooltipAttrs(ontologyExperimentSectionTooltip("validation"))),
    renderWorkDetailButton("experiment-promotion-board", "", "승격", "text-button compact", ontologyExperimentTooltipAttrs(ontologyExperimentSectionTooltip("promotion"))),
    renderWorkDetailButton("experiment-audit-board", "", "이력", "text-button compact", ontologyExperimentTooltipAttrs(ontologyExperimentSectionTooltip("audit"))),
    renderWorkDetailButton("experiment-proposals-board", "", "전략제안", "text-button compact", ontologyExperimentTooltipAttrs(ontologyExperimentSectionTooltip("proposals"))),
    renderInfoIconButton("experiments", "전략 검증 탭의 단일 화면 운영 방식"),
    '</div>',
    '</div>'
  ].join("");
}

function renderOntologyExperimentSectionContent(section, experiments) {
  section = normalizeExperimentSection(section);
  experiments = experiments || ontologyExperimentItems();
  return [
    '<div class="single-tab-console experiment-unified-console">',
    renderOntologyExperimentOverviewPanel(),
    renderHypothesisDevelopmentPanel(),
    renderOntologyExperimentPipelinePanel(),
    renderOntologyExperimentLatestPanel(),
    !experiments.length ? renderOntologyExperimentStarterPanel() : '',
    renderOntologyExperimentListPanel({ selectable: true, compact: true }),
    '</div>'
  ].join("");
  if (section === "validation") {
    return [
      '<div class="ontology-experiment-workbench ontology-experiment-workbench-validation">',
      '<div class="ontology-experiment-workbench-side">',
      renderOntologyExperimentListPanel({ selectable: true, compact: true }),
      '</div>',
      '<div class="ontology-experiment-workbench-main">',
      renderOntologyExperimentSelectedPanel(),
      renderOntologyExperimentReplayPanel(),
      renderOntologyExperimentComparisonPanel(),
      '</div>',
      '</div>'
    ].join("");
  }
  if (section === "promotion") {
    return [
      '<div class="ontology-experiment-workbench ontology-experiment-workbench-promotion">',
      '<div class="ontology-experiment-workbench-side">',
      renderOntologyExperimentListPanel({ selectable: true, compact: true }),
      '</div>',
      '<div class="ontology-experiment-workbench-main">',
      renderOntologyExperimentSelectedPanel(),
      renderOntologyExperimentPromotionPanel(),
      renderOntologyExperimentLatestPanel(),
      '</div>',
      '</div>'
    ].join("");
  }
  if (section === "audit") {
    return [
      renderOntologyExperimentAuditPanel(),
      renderOntologyExperimentListPanel({ selectable: true })
    ].join("");
  }
  if (section === "proposals") {
    return renderStrategyProposalConsolePanel();
  }
  return [
    renderOntologyExperimentOverviewPanel(),
    renderOntologyExperimentPipelinePanel(),
    renderOntologyExperimentLatestPanel(),
    !experiments.length ? renderOntologyExperimentStarterPanel() : '',
    renderOntologyExperimentListPanel({ selectable: true })
  ].join("");
}

function hypothesisDevelopmentPayload() {
  return hypothesesState.hypothesisDevelopment && typeof hypothesesState.hypothesisDevelopment === "object"
    ? hypothesesState.hypothesisDevelopment
    : { count: 0, summary: { statuses: {} }, cases: [], events: [] };
}

function hypothesisDevelopmentCases() {
  return latestChangedFirst(Array.isArray(hypothesisDevelopmentPayload().cases) ? hypothesisDevelopmentPayload().cases : []);
}

function hypothesisDevelopmentCaseById(caseId) {
  var id = String(caseId || "");
  return hypothesisDevelopmentCases().filter(function (item) { return String(item.caseId || "") === id; })[0] || null;
}

function syncActiveHypothesisDevelopmentCaseId() {
  var cases = hypothesisDevelopmentCases();
  if (!cases.length) {
    hypothesesState.activeHypothesisDevelopmentCaseId = "";
    return null;
  }
  var active = hypothesisDevelopmentCaseById(hypothesesState.activeHypothesisDevelopmentCaseId);
  if (!active) {
    active = cases[0];
    hypothesesState.activeHypothesisDevelopmentCaseId = String(active.caseId || "");
  }
  return active;
}

function hypothesisDevelopmentStatusMeta(status) {
  var value = String(status || "proposed").toLowerCase();
  var labels = {
    proposed: "제안",
    screening: "선별 중",
    "needs-data": "자료 필요",
    rejected: "제외",
    compiled: "규칙 변환",
    validating: "검증 중",
    validated: "검증 완료",
    "approval-required": "승인 필요",
    deployed: "운영 반영",
    observing: "사후 관측",
    strengthened: "강화",
    weakened: "약화",
    invalidated: "반증",
    "needs-revision": "수정 필요",
    blocked: "차단",
    "rolled-back": "자동 복원",
    retired: "종료"
  };
  var tone = ["blocked", "invalidated", "rolled-back"].indexOf(value) >= 0 ? "danger"
    : (["needs-data", "needs-revision", "screening", "validating"].indexOf(value) >= 0 ? "caution"
      : (["approval-required", "validated", "deployed", "observing", "strengthened"].indexOf(value) >= 0 ? "watch" : "hold"));
  return { label: labels[value] || value, tone: tone };
}

function hypothesisDevelopmentGateMeta(status) {
  var value = String(status || "pending").toLowerCase();
  if (value === "passed") return { label: "통과", tone: "watch" };
  if (value === "blocked" || value === "failed" || value === "contradicted") return { label: "차단", tone: "danger" };
  if (value === "needs-data") return { label: "자료 필요", tone: "caution" };
  return { label: "대기", tone: "hold" };
}

function renderHypothesisDevelopmentGate(gate) {
  var meta = hypothesisDevelopmentGateMeta(gate && gate.status);
  return [
    '<div class="hypothesis-development-gate">',
    '<span class="tone-chip ' + escapeHtml(meta.tone) + '">' + escapeHtml(meta.label) + '</span>',
    '<strong>' + escapeHtml(gate && (gate.label || gate.id) || "검증") + '</strong>',
    '<em>' + escapeHtml(gate && gate.detail || "검증 대기") + '</em>',
    '</div>'
  ].join("");
}

function renderHypothesisDevelopmentCaseDetail(item) {
  if (!item) {
    return '<section class="hypothesis-development-detail"><div class="ontology-empty">자동 승격된 가설 개발 케이스가 없습니다.</div></section>';
  }
  var status = hypothesisDevelopmentStatusMeta(item.status);
  var gates = Array.isArray(item.validationGates) ? item.validationGates : [];
  var path = Array.isArray(item.causalPath) ? item.causalPath : [];
  var supporting = Array.isArray(item.supportingEvidenceIds) ? item.supportingEvidenceIds : [];
  var counter = Array.isArray(item.counterEvidenceIds) ? item.counterEvidenceIds : [];
  var impact = item.decisionImpact && typeof item.decisionImpact === "object" ? item.decisionImpact : {};
  var busy = Boolean(hypothesesState.hypothesisDevelopmentAction);
  return [
    '<section class="hypothesis-development-detail">',
    '<div class="hypothesis-development-detail-head">',
    '<div><p class="label">Development Case</p><h3>' + escapeHtml(item.title || item.symbol || "가설") + '</h3><span>' + escapeHtml(item.caseId || "") + '</span></div>',
    '<span class="tone-chip ' + escapeHtml(status.tone) + '">' + escapeHtml(status.label) + '</span>',
    '</div>',
    '<p class="hypothesis-development-claim">' + escapeHtml(item.claim || "가설 주장이 없습니다.") + '</p>',
    '<div class="hypothesis-development-meta">',
    '<span><b>종목</b>' + escapeHtml(item.symbol || "-") + '</span>',
    '<span><b>판단 영향</b>' + escapeHtml(impact.influence || "확인 전") + '</span>',
    '<span><b>후보 규칙</b>' + escapeHtml(item.candidateId || "생성 전") + '</span>',
    '<span><b>실험</b>' + escapeHtml(item.experimentId || "생성 전") + '</span>',
    '</div>',
    path.length ? '<div class="hypothesis-development-path">' + path.map(function (step, index) { return '<span><b>' + escapeHtml(index + 1) + '</b>' + escapeHtml(step) + '</span>'; }).join("") + '</div>' : '',
    '<div class="hypothesis-development-evidence"><span>지지 근거 <strong>' + escapeHtml(supporting.length) + '</strong></span><span>반대 근거 <strong>' + escapeHtml(counter.length) + '</strong></span><span>원본 제안 <strong>' + escapeHtml((item.sourceProposalIds || []).length) + '</strong></span></div>',
    '<div class="hypothesis-development-gates">' + gates.map(renderHypothesisDevelopmentGate).join("") + '</div>',
    item.blockedReason ? '<p class="form-error">' + escapeHtml(item.blockedReason) + '</p>' : '',
    '<div class="ontology-experiment-actions">',
    '<button class="text-button" type="button" data-hypothesis-development-process="' + escapeHtml(item.caseId || "") + '"' + (busy || ["deployed", "observing", "retired"].indexOf(String(item.status || "")) >= 0 ? ' disabled' : '') + '>검증 다시 실행</button>',
    '<button class="text-button primary" type="button" data-hypothesis-development-approve="' + escapeHtml(item.caseId || "") + '"' + (busy || String(item.status || "") !== "approval-required" ? ' disabled' : '') + '>운영 반영 승인</button>',
    '</div>',
    '</section>'
  ].join("");
}

function renderHypothesisDevelopmentPanel() {
  var payload = hypothesisDevelopmentPayload();
  var cases = hypothesisDevelopmentCases();
  var active = syncActiveHypothesisDevelopmentCaseId();
  var statuses = payload.summary && payload.summary.statuses && typeof payload.summary.statuses === "object" ? payload.summary.statuses : {};
  return [
    '<article class="panel hypothesis-development-panel"' + cardTypeAttrs("process-card", cases.length ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div><p class="label">Hypothesis Promotion</p><h2>가설 자동 승격·검증</h2><p class="subtle">AI 제안을 자동 선별하고 후보 규칙과 TypeDB 검증까지 진행합니다. 운영 RuleBox 반영은 승인 후 실행됩니다.</p></div>',
    '<div class="settings-actions"><span class="tone-chip ' + escapeHtml(Number(statuses["approval-required"] || 0) ? "watch" : "hold") + '">승인 필요 ' + escapeHtml(statuses["approval-required"] || 0) + '건</span><button class="text-button" type="button" data-hypothesis-development-refresh' + (hypothesesState.hypothesisDevelopmentLoading ? ' disabled' : '') + '>새로고침</button><button class="text-button" type="button" data-hypothesis-development-process=""' + (hypothesesState.hypothesisDevelopmentAction ? ' disabled' : '') + '>대기 검증</button></div>',
    '</div>',
    '<div class="hypothesis-development-summary">',
    renderOntologyExperimentMetric("전체", payload.count == null ? cases.length : payload.count, "cases"),
    renderOntologyExperimentMetric("자료 필요", statuses["needs-data"] || 0, "needs data"),
    renderOntologyExperimentMetric("검증 완료", statuses["approval-required"] || statuses.validated || 0, "validated"),
    renderOntologyExperimentMetric("운영 반영", Number(statuses.deployed || 0) + Number(statuses.observing || 0), "deployed"),
    '</div>',
    hypothesesState.hypothesisDevelopmentError ? '<p class="form-error">' + escapeHtml(hypothesesState.hypothesisDevelopmentError) + '</p>' : '',
    hypothesesState.hypothesisDevelopmentLoading && !hypothesesState.hypothesisDevelopmentLoaded ? '<div class="rule-strip"><span>가설 개발 계보와 검증 게이트를 읽는 중입니다.</span></div>' : '',
    '<div class="hypothesis-development-layout">',
    '<div class="hypothesis-development-list">',
    cases.length ? cases.slice(0, 20).map(function (item) {
      var meta = hypothesisDevelopmentStatusMeta(item.status);
      var selected = active && String(active.caseId || "") === String(item.caseId || "");
      return '<button type="button" class="hypothesis-development-case' + (selected ? ' active' : '') + '" data-hypothesis-development-select="' + escapeHtml(item.caseId || "") + '"><span class="tone-chip ' + escapeHtml(meta.tone) + '">' + escapeHtml(meta.label) + '</span><strong>' + escapeHtml(item.symbol || "-") + '</strong><em>' + escapeHtml(item.title || item.claim || "가설") + '</em>' + renderRecordChangedAt(item) + '</button>';
    }).join("") : '<div class="ontology-empty">AI 판단 뒤 생성된 신규 가설 제안을 기다리고 있습니다.</div>',
    '</div>',
    renderHypothesisDevelopmentCaseDetail(active),
    '</div>',
    '</article>'
  ].join("");
}

function ontologyExperimentPayload() {
  return experimentsState.ontologyExperiments && typeof experimentsState.ontologyExperiments === "object" ? experimentsState.ontologyExperiments : {};
}

function ontologyExperimentItems() {
  var payload = ontologyExperimentPayload();
  return latestChangedFirst(Array.isArray(payload.experiments) ? payload.experiments : []);
}

function ontologyExperimentById(experimentId) {
  var id = String(experimentId || "");
  return ontologyExperimentItems().filter(function (item) {
    return String((item || {}).id || (item || {}).experimentId || "") === id;
  })[0] || {};
}

function ontologyExperimentIdOf(experiment) {
  return String((experiment || {}).id || (experiment || {}).experimentId || "").trim();
}

function syncActiveOntologyExperimentId() {
  var items = ontologyExperimentItems();
  if (!items.length) {
    if (experimentsState.ontologyExperimentsLoaded) experimentsState.activeOntologyExperimentId = "";
    return experimentsState.activeOntologyExperimentId || "";
  }
  if (experimentsState.activeOntologyExperimentId && ontologyExperimentIdOf(ontologyExperimentById(experimentsState.activeOntologyExperimentId))) {
    return experimentsState.activeOntologyExperimentId;
  }
  var selected = ontologyExperimentPrimaryCandidate() || items[0];
  experimentsState.activeOntologyExperimentId = ontologyExperimentIdOf(selected);
  return experimentsState.activeOntologyExperimentId;
}

function activeOntologyExperiment() {
  return ontologyExperimentIdOf(ontologyExperimentById(experimentsState.activeOntologyExperimentId))
    ? ontologyExperimentById(experimentsState.activeOntologyExperimentId)
    : (ontologyExperimentPrimaryCandidate() || ontologyExperimentItems()[0] || null);
}

function ontologyExperimentLatestRun(experiment) {
  var history = Array.isArray(experiment.runHistory) ? experiment.runHistory : [];
  var lastResult = experiment.lastResult && typeof experiment.lastResult === "object" ? experiment.lastResult : {};
  if (history.length) {
    var latestHistory = history[0] || {};
    if (Array.isArray(latestHistory.recommendations)) return latestHistory;
    var mergedHistory = Object.assign({}, latestHistory);
    if (Array.isArray(lastResult.recommendations)) mergedHistory.recommendations = lastResult.recommendations;
    if (lastResult.proposedOntologyChanges && typeof lastResult.proposedOntologyChanges === "object") {
      mergedHistory.proposedOntologyChanges = lastResult.proposedOntologyChanges;
    }
    return mergedHistory;
  }
  var readiness = lastResult.promotionReadiness && typeof lastResult.promotionReadiness === "object" ? lastResult.promotionReadiness : {};
  var inference = lastResult.inference && typeof lastResult.inference === "object" ? lastResult.inference : {};
  var aggregate = inference.aggregateDelta && typeof inference.aggregateDelta === "object" ? inference.aggregateDelta : {};
  var sandbox = lastResult.sandbox && typeof lastResult.sandbox === "object" ? lastResult.sandbox : {};
  return {
    completedAt: lastResult.completedAt || "",
    promotionStatus: readiness.status || "",
    validationState: readiness.validationState || lastResult.validationState || "conditional",
    dataState: readiness.dataState || lastResult.dataState || "partial",
    qualityState: readiness.qualityState || lastResult.qualityState || "needs-review",
    graphRunCount: sandbox.graphRunCount || 0,
    derivedRelationDelta: aggregate.derivedRelationCount || 0,
    newRelationTypes: aggregate.newRelationTypes || [],
    findings: lastResult.findings || [],
    recommendations: lastResult.recommendations || [],
    proposedOntologyChanges: lastResult.proposedOntologyChanges || {},
    appliedOntologyChanges: lastResult.appliedOntologyChanges || {},
    applyStatus: (lastResult.appliedOntologyChanges || {}).status || "",
    appliedAt: (lastResult.appliedOntologyChanges || {}).appliedAt || ""
  };
}

function ontologyExperimentRecommendations(source) {
  return Array.isArray((source || {}).recommendations) ? source.recommendations : [];
}

function ontologyRecommendationIdOf(item) {
  return String((item || {}).id || "").trim();
}

function ontologyRecommendationCanApply(item) {
  var type = String((item || {}).type || "");
  var applyStatus = String((item || {}).applyStatus || "").toLowerCase();
  if (applyStatus === "applied" || applyStatus === "already-applied") return false;
  return {
    "promote-rule": true,
    "review-rule-promotion": true,
    "reuse-existing-relation-types": true,
    "run-typedb-materialization": true,
    "register-relation-types": true,
    "register-decision-stages": true,
    "register-tbox-classes": true
  }[type] === true;
}

function ontologyExperimentSelectedRecommendationIds(experimentId) {
  var id = String(experimentId || "").trim();
  var selections = experimentsState.ontologyExperimentRecommendationSelections || {};
  return uniqueTextItems(Array.isArray(selections[id]) ? selections[id] : []);
}

function setOntologyExperimentRecommendationSelection(experimentId, ids) {
  var id = String(experimentId || "").trim();
  if (!id) return;
  var next = Object.assign({}, experimentsState.ontologyExperimentRecommendationSelections || {});
  var clean = uniqueTextItems(ids || []);
  if (clean.length) next[id] = clean;
  else delete next[id];
  experimentsState.ontologyExperimentRecommendationSelections = next;
}

function clearOntologyExperimentRecommendationSelection(experimentId, appliedIds) {
  var id = String(experimentId || "").trim();
  if (!id) return;
  if (!Array.isArray(appliedIds) || !appliedIds.length) {
    setOntologyExperimentRecommendationSelection(id, []);
    return;
  }
  var applied = {};
  appliedIds.forEach(function (item) { applied[String(item || "").trim()] = true; });
  setOntologyExperimentRecommendationSelection(id, ontologyExperimentSelectedRecommendationIds(id).filter(function (item) {
    return !applied[item];
  }));
}

function toggleOntologyExperimentRecommendation(experimentId, recommendationId, checked) {
  var id = String(experimentId || "").trim();
  var recommendation = String(recommendationId || "").trim();
  if (!id || !recommendation) return;
  var selected = ontologyExperimentSelectedRecommendationIds(id).filter(function (item) {
    return item !== recommendation;
  });
  if (checked) selected.push(recommendation);
  setOntologyExperimentRecommendationSelection(id, selected);
}

function applicableOntologyExperimentRecommendationIds(experimentId) {
  var experiment = ontologyExperimentById(experimentId);
  var latest = ontologyExperimentLatestRun(experiment);
  return ontologyExperimentRecommendations(latest).filter(ontologyRecommendationCanApply).map(ontologyRecommendationIdOf).filter(Boolean);
}

function ontologyRecommendationTone(priority) {
  var key = String(priority || "").toLowerCase();
  if (key === "high") return "watch";
  if (key === "medium") return "caution";
  return "neutral";
}

function ontologyRecommendationPriorityLabel(priority) {
  return {
    high: "높음",
    medium: "중간",
    low: "낮음"
  }[String(priority || "").toLowerCase()] || "검토";
}

function ontologyExperimentStatusLabel(status) {
  return {
    active: "활성",
    paused: "일시정지",
    completed: "완료",
    draft: "초안"
  }[String(status || "").toLowerCase()] || status || "대기";
}

function ontologyExperimentStatusTone(status) {
  var key = String(status || "").toLowerCase();
  if (key === "active") return "watch";
  if (key === "paused") return "hold";
  if (key === "completed") return "neutral";
  return "caution";
}

function ontologyReadinessTone(status) {
  var key = String(status || "").toLowerCase();
  if (key === "promote-candidate") return "watch";
  if (key === "needs-data") return "hold";
  if (key === "needs-review") return "caution";
  return "neutral";
}

function ontologyReadinessLabel(status) {
  return {
    "promote-candidate": "승격 후보",
    "needs-review": "검토 필요",
    "needs-data": "데이터 필요"
  }[String(status || "").toLowerCase()] || status || "판정 대기";
}

function ontologyApplyStatusLabel(status) {
  return {
    applied: "운영 반영",
    pending: "반영 대기",
    disabled: "저장소 비활성",
    error: "반영 오류",
    "already-applied": "이미 반영"
  }[String(status || "").toLowerCase()] || status || "미반영";
}

function ontologyExperimentBusy(action, id) {
  var current = String(experimentsState.ontologyExperimentAction || "");
  return current === action || current === action + ":" + id;
}

function renderOntologyExperimentOverviewPanel() {
  var payload = ontologyExperimentPayload();
  var latest = payload.latestRun && typeof payload.latestRun === "object" ? payload.latestRun : {};
  return [
    '<article class="panel ontology-experiment-overview-panel"' + cardTypeAttrs("process-card", payload.enabled === false ? "hold" : "watch") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Ontology Lab</p>',
    '<h2>실험 실행 상태</h2>',
    '<p class="subtle">활성 실험은 새 모니터 스냅샷이 들어오면 샌드박스에서 다시 검증됩니다.</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(payload.enabled === false ? "hold" : "watch") + '">' + escapeHtml(payload.enabled === false ? "중지" : "실행") + '</span>',
    '</div>',
    '<div class="ontology-experiment-metrics">',
    renderOntologyExperimentMetric("전체", payload.count || ontologyExperimentItems().length || 0, "experiments"),
    renderOntologyExperimentMetric("활성", payload.activeCount || 0, "active"),
    renderOntologyExperimentMetric("일시정지", payload.pausedCount || 0, "paused"),
    renderOntologyExperimentMetric("배치", payload.batchSize || "-", "batch"),
    '</div>',
    '<div class="ontology-experiment-actions">',
    '<button class="text-button" type="button" data-lab-refresh' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("refresh")) + (experimentsState.ontologyExperimentsLoading ? ' disabled' : '') + '>' + escapeHtml(experimentsState.ontologyExperimentsLoading ? "조회 중" : "새로고침") + '</button>',
    '<button class="text-button" type="button" data-lab-suggest' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("suggest")) + (experimentsState.ontologyExperimentAction ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("suggest") ? "제안 중" : "AI 실험 제안") + '</button>',
    '<button class="text-button primary" type="button" data-lab-run-active' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("runActive")) + (experimentsState.ontologyExperimentAction ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("once") ? "실행 중" : "활성 실험 실행") + '</button>',
    '</div>',
    experimentsState.ontologyExperimentsError ? '<p class="form-error">' + escapeHtml(experimentsState.ontologyExperimentsError) + '</p>' : '',
    '<p class="subtle">AI 자동 제안 ' + escapeHtml(payload.autoSuggestEnabled === false ? "중지" : (payload.autoSuggestConfigured === false ? "미설정" : "실행")) + ' · 주기 ' + escapeHtml(payload.autoSuggestIntervalMinutes || "-") + '분 · 최대 ' + escapeHtml(payload.autoSuggestLimit || "-") + '건</p>',
    latest.completedAt ? '<p class="subtle">최근 실행 ' + escapeHtml(formatClock(latest.completedAt)) + ' · ' + escapeHtml(ontologyReadinessLabel(latest.promotionStatus)) + '</p>' : '',
    '</article>'
  ].join("");
}

function renderOntologyExperimentMetric(label, value, caption) {
  return [
    '<section' + cardTypeAttrs("metric-cell") + '>',
    '<span>' + escapeHtml(caption || "") + '</span>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '<em>' + escapeHtml(label || "") + '</em>',
    '</section>'
  ].join("");
}

function ontologyExperimentPrimaryCandidate() {
  var items = ontologyExperimentItems();
  if (!items.length) return null;
  var promoteCandidate = items.filter(function (experiment) {
    var latest = ontologyExperimentLatestRun(experiment || {});
    var readiness = String(latest.promotionStatus || "").toLowerCase();
    var applyStatus = String(latest.applyStatus || ((latest.appliedOntologyChanges || {}).status) || "").toLowerCase();
    return readiness === "promote-candidate" && applyStatus !== "applied" && applyStatus !== "already-applied";
  })[0];
  if (promoteCandidate) return promoteCandidate;
  return items.filter(function (experiment) {
    return String((experiment || {}).status || "").toLowerCase() === "active";
  })[0] || items[0];
}

function ontologyExperimentRunHistory(experiment) {
  if (!experiment) return [];
  var history = Array.isArray(experiment.runHistory) ? experiment.runHistory.slice() : [];
  var latest = ontologyExperimentLatestRun(experiment);
  if (!history.length && (latest.completedAt || latest.promotionStatus || latest.graphRunCount)) history.push(latest);
  return latestChangedFirst(history);
}

function ontologyExperimentStageCounts() {
  var counts = { draft: 0, validating: 0, promotable: 0, applied: 0, blocked: 0 };
  ontologyExperimentItems().forEach(function (experiment) {
    var latest = ontologyExperimentLatestRun(experiment || {});
    var status = String((experiment || {}).status || "draft").toLowerCase();
    var readiness = String(latest.promotionStatus || "").toLowerCase();
    var applyStatus = String(latest.applyStatus || ((latest.appliedOntologyChanges || {}).status) || "").toLowerCase();
    if (applyStatus === "applied" || applyStatus === "already-applied") {
      counts.applied += 1;
    } else if (readiness === "promote-candidate") {
      counts.promotable += 1;
    } else if (status === "active" || latest.completedAt) {
      counts.validating += 1;
    } else if (status === "paused" || readiness === "needs-data" || readiness === "needs-review") {
      counts.blocked += 1;
    } else {
      counts.draft += 1;
    }
  });
  return counts;
}

function renderOntologyExperimentStageCard(index, title, count, description, tone) {
  return [
    '<section class="ontology-experiment-stage-card"' + cardTypeAttrs("process-card", tone || "hold") + '>',
    '<span>' + escapeHtml(index) + '</span>',
    '<strong>' + escapeHtml(title) + '</strong>',
    '<b>' + escapeHtml(count) + '</b>',
    '<p>' + escapeHtml(description) + '</p>',
    '</section>'
  ].join("");
}

function renderOntologyExperimentPipelinePanel() {
  var counts = ontologyExperimentStageCounts();
  var total = ontologyExperimentItems().length;
  return [
    '<article class="panel ontology-experiment-pipeline-panel"' + cardTypeAttrs("process-card", total ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Experiment Workbench</p>',
    '<h2>검증 파이프라인</h2>',
    '<p class="subtle">초안 규칙을 샌드박스에서 재생하고, 증거가 쌓인 후보만 운영 RuleBox로 올립니다.</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(total ? "watch" : "hold") + '">' + escapeHtml(total ? total + "개 실험" : "대기") + '</span>',
    '</div>',
    '<div class="ontology-experiment-stage-grid">',
    renderOntologyExperimentStageCard("01", "제안", counts.draft, "AI 또는 수동으로 만든 후보 규칙", "hold"),
    renderOntologyExperimentStageCard("02", "재생 검증", counts.validating, "스냅샷 기반 샌드박스 실행 중", "caution"),
    renderOntologyExperimentStageCard("03", "승격 후보", counts.promotable, "운영 반영 전 심사 대상", "watch"),
    renderOntologyExperimentStageCard("04", "운영 반영", counts.applied, "RuleBox에 적용된 변경", "watch"),
    renderOntologyExperimentStageCard("보류", "데이터 보강", counts.blocked, "근거 부족 또는 검토 필요", "hold"),
    '</div>',
    '</article>'
  ].join("");
}

function ontologyExperimentReplaySource() {
  var candidate = activeOntologyExperiment() || ontologyExperimentPrimaryCandidate();
  if (candidate) {
    return { experiment: candidate, latest: ontologyExperimentLatestRun(candidate), history: ontologyExperimentRunHistory(candidate) };
  }
  var payload = ontologyExperimentPayload();
  var latest = payload.latestRun && typeof payload.latestRun === "object" ? payload.latestRun : {};
  return { experiment: null, latest: latest, history: latest.completedAt ? [latest] : [] };
}

function renderOntologyExperimentSelectedPanel() {
  var experiment = activeOntologyExperiment();
  if (!experiment) {
    return [
      '<article class="panel ontology-experiment-selected-panel"' + cardTypeAttrs("process-card", "hold") + '>',
      renderEmptyState({
        label: "Selected Experiment",
        title: "선택된 실험이 없습니다",
        description: "AI 실험 제안을 만들거나 목록에서 검증할 실험을 선택하세요.",
        action: '<button class="text-button primary" type="button" data-lab-suggest' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("suggest")) + (experimentsState.ontologyExperimentAction ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("suggest") ? "제안 중" : "AI 실험 제안") + '</button>'
      }),
      '</article>'
    ].join("");
  }
  var id = ontologyExperimentIdOf(experiment);
  var latest = ontologyExperimentLatestRun(experiment);
  var candidateRules = Array.isArray(experiment.candidateRules) ? experiment.candidateRules : [];
  var symbols = Array.isArray(experiment.symbols) ? experiment.symbols : [];
  var gate = ontologyExperimentPromotionGate(experiment, latest);
  return [
    '<article class="panel ontology-experiment-selected-panel"' + cardTypeAttrs("process-card", latest.completedAt ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Selected Experiment</p>',
    '<h2>' + escapeHtml(experiment.title || "선택 실험") + '</h2>',
    '<p class="subtle">' + escapeHtml(experiment.hypothesis || "등록된 가설 설명이 없습니다.") + '</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(ontologyPromotionGateTone(gate.status || latest.promotionStatus)) + '">' + escapeHtml(gate.statusLabel || ontologyReadinessLabel(latest.promotionStatus)) + '</span>',
    '</div>',
    '<div class="ontology-experiment-run-grid">',
    renderOntologyExperimentMetric("후보 규칙", candidateRules.length, "rules"),
    renderOntologyExperimentMetric("그래프", latest.graphRunCount || 0, "graphs"),
    renderOntologyExperimentMetric("관계 변화", latest.derivedRelationDelta || 0, "delta"),
    renderOntologyExperimentMetric("운영 게이트", gate.reasonLabel || gate.statusLabel || "-", "gate"),
    '</div>',
    symbols.length ? '<div class="theme-radar ontology-experiment-tags">' + symbols.slice(0, 12).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
    '<div class="ontology-experiment-actions">',
    renderWorkDetailButton("ontology-experiment", id, "상세", "text-button compact", ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("detail"))),
    '<button class="text-button" type="button" data-lab-run="' + escapeHtml(id) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("run")) + (ontologyExperimentBusy("run", id) || !id ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("run", id) ? "실행 중" : "검증 실행") + '</button>',
    gate.canApply ? '<button class="text-button primary" type="button" data-lab-apply="' + escapeHtml(id) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("apply")) + (ontologyExperimentBusy("apply", id) || !id ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("apply", id) ? "반영 중" : "운영 반영") + '</button>' : '',
    '</div>',
    '</article>'
  ].join("");
}

function renderOntologyExperimentTraceRow(label, title, detail, tone) {
  return [
    '<div class="ontology-experiment-trace-row">',
    '<span class="tone-chip ' + escapeHtml(tone || "hold") + '">' + escapeHtml(label) + '</span>',
    '<div>',
    '<strong>' + escapeHtml(title || "-") + '</strong>',
    '<em>' + escapeHtml(detail || "데이터 대기") + '</em>',
    '</div>',
    '</div>'
  ].join("");
}

function renderOntologyExperimentReplayPanel() {
  var source = ontologyExperimentReplaySource();
  var latest = source.latest || {};
  var experiment = source.experiment || {};
  var relationTypes = Array.isArray(latest.newRelationTypes) ? latest.newRelationTypes : [];
  var findings = Array.isArray(latest.findings) ? latest.findings : [];
  var history = source.history || [];
  var title = experiment.title || "선택된 실험 없음";
  return [
    '<article class="panel ontology-experiment-replay-panel"' + cardTypeAttrs("diagnostic-card", latest.completedAt ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Backtest Replay</p>',
    '<h2>백테스트 / 리플레이</h2>',
    '<p class="subtle">실험 후보가 과거 스냅샷과 TypeDB 물질화 결과에서 어떤 관계 변화를 만들었는지 확인합니다.</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(latest.completedAt ? "watch" : "hold") + '">' + escapeHtml(latest.completedAt ? "최근 재생" : "미실행") + '</span>',
    '</div>',
    '<div class="ontology-experiment-run-grid">',
    renderOntologyExperimentMetric("대상", title, "candidate"),
    renderOntologyExperimentMetric("그래프", latest.graphRunCount || 0, "graphs"),
    renderOntologyExperimentMetric("관계 변화", latest.derivedRelationDelta || 0, "delta"),
    renderOntologyExperimentMetric("이력", history.length || 0, "runs"),
    '</div>',
    latest.completedAt ? '<p class="subtle">최근 실행 ' + escapeHtml(formatClock(latest.completedAt)) + '</p>' : '',
    '<div class="ontology-experiment-trace-list">',
    renderOntologyExperimentTraceRow("입력", "후보 규칙·스냅샷", title, experiment.id ? "watch" : "hold"),
    renderOntologyExperimentTraceRow("검증", "샌드박스 관계 생성", (latest.graphRunCount || 0) + "개 그래프 · 파생 변화 " + (latest.derivedRelationDelta || 0), latest.completedAt ? "watch" : "hold"),
    renderOntologyExperimentTraceRow("출력", "운영 반영 심사", ontologyReadinessLabel(latest.promotionStatus), ontologyReadinessTone(latest.promotionStatus)),
    '</div>',
    relationTypes.length ? '<div class="theme-radar ontology-experiment-tags">' + relationTypes.slice(0, 8).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
    findings.length ? '<div class="ontology-experiment-findings">' + findings.slice(0, 3).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
    '</article>'
  ].join("");
}

function renderOntologyExperimentComparisonRow(label, baseline, candidate, meaning) {
  return [
    '<div class="ontology-experiment-comparison-row">',
    '<strong>' + escapeHtml(label) + '</strong>',
    '<span>' + escapeHtml(baseline || "-") + '</span>',
    '<span>' + escapeHtml(candidate || "-") + '</span>',
    '<em>' + escapeHtml(meaning || "") + '</em>',
    '</div>'
  ].join("");
}

function renderOntologyExperimentComparisonPanel() {
  var source = ontologyExperimentReplaySource();
  var experiment = source.experiment || {};
  var latest = source.latest || {};
  var candidateRules = Array.isArray(experiment.candidateRules) ? experiment.candidateRules : [];
  var relationTypes = Array.isArray(latest.newRelationTypes) ? latest.newRelationTypes : [];
  var recommendations = ontologyExperimentRecommendations(latest);
  return [
    '<article class="panel ontology-experiment-comparison-panel"' + cardTypeAttrs("relationship-card", candidateRules.length || latest.completedAt ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Candidate vs Operation</p>',
    '<h2>후보 비교</h2>',
    '<p class="subtle">운영 기준과 후보 규칙의 차이를 같은 표로 맞춰 보고, 승격 전에 의미를 분리합니다.</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(ontologyReadinessTone(latest.promotionStatus)) + '">' + escapeHtml(ontologyReadinessLabel(latest.promotionStatus)) + '</span>',
    '</div>',
    '<div class="ontology-experiment-comparison-grid">',
    '<div class="ontology-experiment-comparison-head"><strong>항목</strong><span>운영 기준</span><span>실험 후보</span><em>해석</em></div>',
    renderOntologyExperimentComparisonRow("룰", "현재 RuleBox", candidateRules.length ? candidateRules.length + "개 후보" : "후보 없음", "운영 로직에 추가될 조건 수"),
    renderOntologyExperimentComparisonRow("관계", "기존 관계", (latest.derivedRelationDelta || 0) + "개 변화", "새 규칙이 만든 관계 수 변화"),
    renderOntologyExperimentComparisonRow("타입", "기존 타입", relationTypes.length ? relationTypes.slice(0, 3).join(", ") : "신규 타입 없음", "새 관계 타입 확장 여부"),
    renderOntologyExperimentComparisonRow("보완 제안", "운영 유지", recommendations.length ? recommendations.length + "건 검토" : "제안 없음", "승격 전 보완해야 할 작업"),
    '</div>',
    '</article>'
  ].join("");
}

function ontologyPromotionGateTone(status) {
  var key = String(status || "").toLowerCase();
  if (key === "ready" || key === "applied" || key === "already-applied" || key === "promote-candidate") return "watch";
  if (key === "needs-review" || key === "review-required") return "caution";
  if (key === "blocked" || key === "error") return "danger";
  return "hold";
}

function fallbackOntologyPromotionGate(experiment, latest) {
  latest = latest || {};
  var recommendations = ontologyExperimentRecommendations(latest);
  var applyStatus = String(latest.applyStatus || ((latest.appliedOntologyChanges || {}).status) || "").toLowerCase();
  var applied = applyStatus === "applied" || applyStatus === "already-applied";
  var checks = fallbackOntologyPromotionChecks(experiment, latest);
  var blocked = checks.some(function (check) {
    return check.blocking !== false && !check.passed;
  });
  return {
    status: applied ? applyStatus : (blocked ? "blocked" : "ready"),
    statusLabel: applied ? ontologyApplyStatusLabel(applyStatus) : (blocked ? "보류" : "반영 가능"),
    reason: blocked ? "frontend-derived-check-blocked" : "",
    reasonLabel: blocked ? "체크 필요" : "통과",
    canApply: Boolean(!applied && !blocked && ontologyExperimentIdOf(experiment)),
    checks: checks
  };
}

function ontologyExperimentPromotionGate(experiment, latest) {
  var gate = experiment && experiment.promotionGate && typeof experiment.promotionGate === "object" ? experiment.promotionGate : null;
  if (gate) {
    var normalized = Object.assign({}, gate);
    normalized.checks = Array.isArray(gate.checks) ? gate.checks : [];
    normalized.canApply = Boolean(gate.canApply);
    normalized.requiresReviewApproval = Boolean(gate.requiresReviewApproval);
    return normalized;
  }
  return fallbackOntologyPromotionGate(experiment, latest);
}

function ontologyExperimentPromotionChecks(experiment, latest) {
  var gate = experiment && experiment.promotionGate && typeof experiment.promotionGate === "object" ? experiment.promotionGate : null;
  if (gate && Array.isArray(gate.checks) && gate.checks.length) return gate.checks;
  return fallbackOntologyPromotionChecks(experiment, latest);
}

function fallbackOntologyPromotionChecks(experiment, latest) {
  experiment = experiment || {};
  latest = latest || {};
  var candidateRules = Array.isArray(experiment.candidateRules) ? experiment.candidateRules : [];
  var recommendations = ontologyExperimentRecommendations(latest);
  var relationTypes = Array.isArray(latest.newRelationTypes) ? latest.newRelationTypes : [];
  var applyStatus = String(latest.applyStatus || ((latest.appliedOntologyChanges || {}).status) || "").toLowerCase();
  return [
    {
      id: "candidate-rules",
      label: "가설·후보 규칙",
      passed: Boolean(experiment.hypothesis || candidateRules.length),
      detail: candidateRules.length ? candidateRules.length + "개 후보 규칙" : "가설 또는 후보 규칙 필요",
      blocking: true
    },
    {
      id: "replay-run",
      label: "재생 실행",
      passed: Boolean(latest.completedAt),
      detail: latest.completedAt ? formatClock(latest.completedAt) : "실행 이력 없음",
      blocking: true
    },
    {
      id: "graph-evidence",
      label: "그래프 증거",
      passed: Boolean(Number(latest.graphRunCount || 0) > 0 || Number(latest.derivedRelationDelta || 0) !== 0 || relationTypes.length),
      detail: (latest.graphRunCount || 0) + "개 그래프 · 변화 " + (latest.derivedRelationDelta || 0),
      blocking: true
    },
    {
      id: "recommendations",
      label: "AI 보완 검토",
      passed: recommendations.length > 0,
      detail: recommendations.length ? recommendations.length + "건 제안" : "보완 제안 없음",
      blocking: false
    },
    {
      id: "readiness",
      label: "승격 판정",
      passed: String(latest.promotionStatus || "").toLowerCase() === "promote-candidate",
      detail: ontologyReadinessLabel(latest.promotionStatus),
      blocking: true
    },
    {
      id: "apply-state",
      label: "운영 반영",
      passed: applyStatus === "applied" || applyStatus === "already-applied",
      detail: ontologyApplyStatusLabel(applyStatus),
      blocking: false
    }
  ];
}

function renderOntologyExperimentPromotionCheck(check) {
  check = check || {};
  var tone = check.passed ? "watch" : (check.required ? "caution" : "hold");
  return [
    '<section class="ontology-experiment-check-row">',
    '<span class="tone-chip ' + escapeHtml(tone) + '">' + escapeHtml(check.passed ? "통과" : (check.required ? "검토" : "대기")) + '</span>',
    '<div>',
    '<strong>' + escapeHtml(check.label || "체크") + '</strong>',
    '<em>' + escapeHtml(check.detail || "") + '</em>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyExperimentPromotionPanel() {
  var source = ontologyExperimentReplaySource();
  var experiment = source.experiment || {};
  var latest = source.latest || {};
  var id = String(experiment.id || experiment.experimentId || "");
  var recommendations = ontologyExperimentRecommendations(latest);
  var applyStatus = String(latest.applyStatus || ((latest.appliedOntologyChanges || {}).status) || "");
  var applied = applyStatus === "applied" || applyStatus === "already-applied";
  var gate = ontologyExperimentPromotionGate(experiment, latest);
  var canApply = Boolean(id && gate.canApply && !applied);
  var checks = gate.checks && gate.checks.length ? gate.checks : ontologyExperimentPromotionChecks(experiment, latest);
  return [
    '<article class="panel ontology-experiment-promotion-panel"' + cardTypeAttrs("signal-card", canApply ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Promotion Review</p>',
    '<h2>승격 심사</h2>',
    '<p class="subtle">운영 반영은 투자 판단 로직 변경이므로 실행 이력, 관계 증거, 보완 제안을 함께 확인합니다.</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(ontologyPromotionGateTone(gate.status)) + '">' + escapeHtml(gate.statusLabel || (canApply ? "반영 가능" : ontologyApplyStatusLabel(applyStatus))) + '</span>',
    '</div>',
    gate.reasonLabel ? '<p class="subtle ontology-experiment-gate-reason">서버 판정: ' + escapeHtml(gate.reasonLabel) + '</p>' : '',
    '<div class="ontology-experiment-checklist">',
    checks.map(renderOntologyExperimentPromotionCheck).join(""),
    '</div>',
    recommendations.length ? renderOntologyExperimentRecommendationList(recommendations, recommendations.length, { experimentId: id, selectable: canApply }) : '',
    '<div class="ontology-experiment-actions">',
    canApply ? '<button class="text-button primary" type="button" data-lab-apply="' + escapeHtml(id) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("apply")) + (ontologyExperimentBusy("apply", id) ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("apply", id) ? "반영 중" : "전체 제안 운영 반영") + '</button>' : '',
    id ? renderWorkDetailButton("ontology-experiment", id, "상세 심사", "text-button compact", ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("detail"))) : '',
    '</div>',
    '</article>'
  ].join("");
}

function ontologyExperimentAuditEntries() {
  var entries = [];
  ontologyExperimentItems().forEach(function (experiment) {
    var id = String((experiment || {}).id || (experiment || {}).experimentId || "");
    ontologyExperimentRunHistory(experiment).forEach(function (run) {
      entries.push({
        at: run.completedAt || run.startedAt || "",
        title: (experiment || {}).title || id || "실험",
        status: ontologyReadinessLabel(run.promotionStatus),
        detail: "그래프 " + (run.graphRunCount || 0) + " · 파생 변화 " + (run.derivedRelationDelta || 0)
      });
    });
    var latest = ontologyExperimentLatestRun(experiment || {});
    var applyStatus = String(latest.applyStatus || ((latest.appliedOntologyChanges || {}).status) || "");
    if (applyStatus) {
      entries.push({
        at: latest.appliedAt || ((latest.appliedOntologyChanges || {}).appliedAt) || latest.completedAt || "",
        title: (experiment || {}).title || id || "실험",
        status: ontologyApplyStatusLabel(applyStatus),
        detail: "운영 반영 상태"
      });
    }
  });
  return entries.sort(function (a, b) {
    return String(b.at || "").localeCompare(String(a.at || ""));
  });
}

function renderOntologyExperimentAuditPanel() {
  var entries = ontologyExperimentAuditEntries().slice(0, 6);
  return [
    '<article class="panel ontology-experiment-audit-panel"' + cardTypeAttrs("diagnostic-card", entries.length ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Audit Trail</p>',
    '<h2>운영 반영 이력</h2>',
    '<p class="subtle">실험 실행, 승격 판정, 운영 반영 상태를 시간순으로 남겨 되돌림 기준을 확인합니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(entries.length) + '</span>',
    '</div>',
    entries.length ? '<div class="ontology-experiment-audit-list">' + entries.map(function (entry) {
      return [
        '<section class="ontology-experiment-audit-row">',
        '<strong>' + escapeHtml(entry.title) + '</strong>',
        '<span>' + escapeHtml(entry.status) + '</span>',
        '<em>' + escapeHtml(entry.detail || "운영 이력") + '</em>',
        renderRecordChangedAt(entry.at),
        '</section>'
      ].join("");
    }).join("") + '</div>' : renderEmptyState({
      label: "Audit Trail",
      title: "아직 기록된 실험 이력이 없습니다",
      description: "실험을 실행하거나 운영 반영하면 이 위치에 감사 이력이 표시됩니다."
    }),
    '</article>'
  ].join("");
}

function renderOntologyExperimentLatestPanel() {
  var payload = ontologyExperimentPayload();
  var latest = payload.latestRun && typeof payload.latestRun === "object" ? payload.latestRun : {};
  var relationTypes = Array.isArray(latest.newRelationTypes) ? latest.newRelationTypes : [];
  var findings = Array.isArray(latest.findings) ? latest.findings : [];
  var recommendations = ontologyExperimentRecommendations(latest);
  var applyStatus = String(latest.applyStatus || ((latest.appliedOntologyChanges || {}).status) || "");
  var appliedAt = latest.appliedAt || ((latest.appliedOntologyChanges || {}).appliedAt) || "";
  return [
    '<article class="panel ontology-experiment-latest-panel"' + cardTypeAttrs("diagnostic-card", latest.completedAt ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Latest Run</p>',
    '<h2>최근 실행 요약</h2>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(ontologyReadinessTone(latest.promotionStatus)) + '">' + escapeHtml(ontologyReadinessLabel(latest.promotionStatus)) + '</span>',
    '</div>',
    latest.completedAt ? [
      '<div class="ontology-experiment-run-grid">',
      renderOntologyExperimentMetric("그래프", latest.graphRunCount || 0, "graphs"),
      renderOntologyExperimentMetric("파생 변화", latest.derivedRelationDelta || 0, "delta"),
      renderOntologyExperimentMetric("검증", decisionStateMeta("validation", latest.validationState, "conditional").label, "validation"),
      renderOntologyExperimentMetric("자료", decisionStateMeta("data", latest.dataState, "partial").label, "data"),
      '</div>',
      relationTypes.length ? '<div class="theme-radar ontology-experiment-tags">' + relationTypes.slice(0, 8).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
      findings.length ? '<div class="ontology-experiment-findings">' + findings.slice(0, 4).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
      recommendations.length ? renderOntologyExperimentRecommendationList(recommendations, 4) : '',
      applyStatus ? '<p class="subtle">운영 반영 ' + escapeHtml(ontologyApplyStatusLabel(applyStatus)) + (appliedAt ? ' · ' + escapeHtml(formatClock(appliedAt)) : '') + '</p>' : '',
      '<p class="subtle">' + escapeHtml(formatClock(latest.completedAt)) + '</p>'
    ].join("") : renderEmptyState({
      label: "Latest Run",
      title: "아직 실행 이력이 없습니다",
      description: "활성 실험이 실행되면 최근 결과가 이곳에 표시됩니다."
    }),
    '</article>'
  ].join("");
}

function renderOntologyExperimentStarterPanel() {
  return [
    '<article class="panel ontology-experiment-starter-panel"' + cardTypeAttrs("action-queue-card", "hold") + '>',
    '<div class="panel-head">',
    '<div><p class="label">STARTER FLOW</p><h2>전략 검증 시작점</h2><span>빈 화면에서도 다음 작업이 보이도록 실험 생성, 실행, 운영 반영 흐름을 고정합니다.</span></div>',
    '<button class="text-button primary" type="button" data-lab-suggest' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("suggest")) + (experimentsState.ontologyExperimentAction ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("suggest") ? "제안 중" : "AI 실험 제안") + '</button>',
    '</div>',
    '<div class="ontology-experiment-starter-grid">',
    renderExperimentStarterStep("01", "후보 규칙 생성", "최근 판단 실패와 누락 근거에서 RuleBox 후보를 만듭니다.", "process-card"),
    renderExperimentStarterStep("02", "샌드박스 실행", "활성 실험을 스냅샷에 적용해 관계 변화, 자료 상태, 검증 상태를 비교합니다.", "diagnostic-card"),
    renderExperimentStarterStep("03", "운영 반영", "검증 완료 여부와 권고안을 확인한 뒤 실제 온톨로지 기준에 반영합니다.", "relationship-card"),
    '</div>',
    '</article>'
  ].join("");
}

function renderExperimentStarterStep(index, title, description, type) {
  return [
    '<section class="ontology-experiment-starter-step"' + cardTypeAttrs(type || "process-card", "hold") + '>',
    '<b>' + escapeHtml(index) + '</b>',
    '<strong>' + escapeHtml(title) + '</strong>',
    '<p>' + escapeHtml(description) + '</p>',
    '</section>'
  ].join("");
}

function renderOntologyExperimentRecommendationList(recommendations, limit, options) {
  if (limit && typeof limit === "object") {
    options = limit;
    limit = 0;
  }
  options = options || {};
  var allItems = Array.isArray(recommendations) ? recommendations : [];
  var items = allItems.slice(0, limit || 4);
  if (!items.length) return "";
  var experimentId = String(options.experimentId || "").trim();
  var selectable = Boolean(options.selectable && experimentId);
  var selectedIds = selectable ? ontologyExperimentSelectedRecommendationIds(experimentId) : [];
  var applicableIds = selectable ? items.filter(ontologyRecommendationCanApply).map(ontologyRecommendationIdOf).filter(Boolean) : [];
  var selectedApplicableCount = applicableIds.filter(function (id) {
    return selectedIds.indexOf(id) >= 0;
  }).length;
  return [
    '<div class="ontology-experiment-recommendations">',
    '<div class="ontology-experiment-recommendation-title">',
    '<div><strong>온톨로지 보완 제안</strong><span>' + escapeHtml(selectable ? ("선택 " + selectedApplicableCount + "/" + applicableIds.length) : items.length) + '</span></div>',
    selectable ? [
      '<div class="ontology-experiment-recommendation-actions">',
      '<button class="text-button compact" type="button" data-lab-recommendations-select-all="' + escapeHtml(experimentId) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("selectAllRecommendations")) + (!applicableIds.length || ontologyExperimentBusy("apply", experimentId) ? ' disabled' : '') + '>전체 선택</button>',
      '<button class="text-button compact" type="button" data-lab-recommendations-clear="' + escapeHtml(experimentId) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("clearRecommendations")) + (!selectedApplicableCount || ontologyExperimentBusy("apply", experimentId) ? ' disabled' : '') + '>해제</button>',
      '<button class="text-button primary compact" type="button" data-lab-apply-selected="' + escapeHtml(experimentId) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("applySelectedRecommendations")) + (!selectedApplicableCount || ontologyExperimentBusy("apply", experimentId) ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("apply", experimentId) ? "반영 중" : "선택 적용") + '</button>',
      '</div>'
    ].join("") : '',
    '</div>',
    items.map(function (item) {
      return renderOntologyExperimentRecommendation(item, { experimentId: experimentId, selectable: selectable, selectedIds: selectedIds });
    }).join(""),
    '</div>'
  ].join("");
}

export { applicableOntologyExperimentRecommendationIds, clearOntologyExperimentRecommendationSelection, ontologyApplyStatusLabel, ontologyExperimentActionTooltip, ontologyExperimentBusy, ontologyExperimentById, ontologyExperimentItems, ontologyExperimentLatestRun, ontologyExperimentPayload, ontologyExperimentPromotionChecks, ontologyExperimentPromotionGate, ontologyExperimentRecommendations, ontologyExperimentSelectedRecommendationIds, ontologyExperimentStatusLabel, ontologyExperimentStatusTone, ontologyExperimentTooltipAttrs, ontologyReadinessLabel, ontologyRecommendationCanApply, ontologyRecommendationIdOf, ontologyRecommendationPriorityLabel, ontologyRecommendationTone, renderOntologyExperimentAuditPanel, renderOntologyExperimentComparisonPanel, renderOntologyExperimentLatestPanel, renderOntologyExperimentMetric, renderOntologyExperimentPromotionCheck, renderOntologyExperimentPromotionPanel, renderOntologyExperimentRecommendationList, renderOntologyExperimentReplayPanel, renderOntologyExperimentSelectedPanel, setOntologyExperimentRecommendationSelection, syncActiveHypothesisDevelopmentCaseId, syncActiveOntologyExperimentId, toggleOntologyExperimentRecommendation };
