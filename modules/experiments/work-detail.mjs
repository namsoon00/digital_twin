import { ontologyApplyStatusLabel, ontologyExperimentActionTooltip, ontologyExperimentBusy, ontologyExperimentItems, ontologyExperimentLatestRun, ontologyExperimentPromotionChecks, ontologyExperimentPromotionGate, ontologyExperimentRecommendations, ontologyExperimentStatusLabel, ontologyExperimentStatusTone, ontologyExperimentTooltipAttrs, ontologyReadinessLabel, ontologyRecommendationCanApply, ontologyRecommendationIdOf, ontologyRecommendationPriorityLabel, ontologyRecommendationTone, renderOntologyExperimentMetric, renderOntologyExperimentPromotionCheck, renderOntologyExperimentRecommendationList } from "./workspace.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { renderNotificationDetailMetric } from "../notifications/reasoning.mjs";
import { formatClock, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { experimentsState } from "../state/experiments.mjs";

function renderOntologyExperimentRecommendation(item, options) {
  item = item || {};
  options = options || {};
  var id = ontologyRecommendationIdOf(item);
  var selectable = Boolean(options.selectable && id && ontologyRecommendationCanApply(item));
  var selected = selectable && (options.selectedIds || []).indexOf(id) >= 0;
  var appliedAt = item.appliedAt ? formatClock(item.appliedAt) : "";
  var applyStatus = String(item.applyStatus || "");
  return [
    '<section class="ontology-experiment-recommendation' + (selected ? " selected" : "") + (selectable ? " selectable" : "") + '">',
    selectable ? [
      '<label class="ontology-experiment-recommendation-check">',
      '<input type="checkbox" data-lab-recommendation-experiment="' + escapeHtml(options.experimentId || "") + '" data-lab-recommendation-toggle="' + escapeHtml(id) + '"' + (selected ? ' checked' : '') + ' />',
      '<span>선택</span>',
      '</label>'
    ].join("") : '',
    '<div>',
    '<strong>' + escapeHtml(item.title || "보완 제안") + '</strong>',
    item.reason ? '<p>' + escapeHtml(item.reason) + '</p>' : '',
    item.action ? '<em>' + escapeHtml(item.action) + '</em>' : '',
    applyStatus ? '<em>' + escapeHtml(ontologyApplyStatusLabel(applyStatus) + (appliedAt ? " · " + appliedAt : "")) + '</em>' : '',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(ontologyRecommendationTone(item.priority)) + '">' + escapeHtml(ontologyRecommendationPriorityLabel(item.priority)) + '</span>',
    '</section>'
  ].join("");
}

function renderOntologyExperimentListPanel(options) {
  options = options || {};
  var experiments = ontologyExperimentItems();
  return [
    '<article class="panel ontology-experiment-list-panel"' + cardTypeAttrs("relationship-card", experiments.length ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Experiments</p>',
    '<h2>실험 목록</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(experiments.length) + '</span>',
    '</div>',
    experimentsState.ontologyExperimentsLoading && !experiments.length ? '<div class="panel skeleton"></div>' : '',
    (!experimentsState.ontologyExperimentsLoading && !experiments.length) ? renderEmptyState({
      label: "Ontology Lab",
      title: "등록된 실험이 없습니다",
      description: "후보 RuleBox 실험을 만들면 이 탭에서 실행 상태를 볼 수 있습니다.",
      meta: ["AI 제안으로 시작", "활성 실험만 자동 검증"],
      action: '<button class="text-button primary" type="button" data-lab-suggest' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("suggest")) + (experimentsState.ontologyExperimentAction ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("suggest") ? "제안 중" : "AI 실험 제안") + '</button>'
    }) : '',
    experiments.length ? '<div class="ontology-experiment-list">' + experiments.map(function (experiment) { return renderOntologyExperimentCard(experiment, options); }).join("") + '</div>' : '',
    '</article>'
  ].join("");
}

function ontologyExperimentByKey(key) {
  var target = String(key || "");
  return ontologyExperimentItems().filter(function (experiment) {
    return String(experiment.id || experiment.experimentId || "") === target;
  })[0] || null;
}

function ontologyExperimentWorkDetailPayload(key) {
  var experiment = ontologyExperimentByKey(key);
  if (!experiment) return null;
  var latest = ontologyExperimentLatestRun(experiment);
  var candidateRules = Array.isArray(experiment.candidateRules) ? experiment.candidateRules : [];
  var symbols = Array.isArray(experiment.symbols) ? experiment.symbols : [];
  var recommendations = ontologyExperimentRecommendations(latest);
  var status = String(experiment.status || "draft");
  var applyStatus = String(latest.applyStatus || ((latest.appliedOntologyChanges || {}).status) || "");
  var promotionChecks = ontologyExperimentPromotionChecks(experiment, latest);
  return {
    kicker: "Ontology Experiment",
    title: experiment.title || "Ontology experiment",
    meta: [ontologyExperimentStatusLabel(status), experiment.id || experiment.experimentId || "", latest.completedAt ? "최근 " + formatClock(latest.completedAt) : ""].filter(Boolean).join(" · "),
    body: [
      '<section class="work-detail-section primary">',
      '<strong>실험 가설</strong>',
      '<p>' + escapeHtml(experiment.hypothesis || "등록된 가설 설명이 없습니다.") + '</p>',
      '</section>',
      '<div class="work-detail-metric-row">',
      renderNotificationDetailMetric("후보 규칙", candidateRules.length, "watch"),
      renderNotificationDetailMetric("그래프", latest.graphRunCount || 0, "hold"),
      renderNotificationDetailMetric("파생 변화", latest.derivedRelationDelta || 0, "hold"),
      renderNotificationDetailMetric("판정", ontologyReadinessLabel(latest.promotionStatus), ontologyExperimentStatusTone(status)),
      '</div>',
      symbols.length ? '<section class="work-detail-section"><strong>대상 심볼</strong><p>' + escapeHtml(symbols.join(" · ")) + '</p></section>' : '',
      '<section class="work-detail-section"><strong>승격 체크리스트</strong><div class="ontology-experiment-checklist">' + promotionChecks.map(renderOntologyExperimentPromotionCheck).join("") + '</div></section>',
      recommendations.length ? '<section class="work-detail-section"><strong>보완 제안</strong>' + renderOntologyExperimentRecommendationList(recommendations, recommendations.length) + '</section>' : '',
      applyStatus ? '<section class="work-detail-section"><strong>운영 반영</strong><p>' + escapeHtml(ontologyApplyStatusLabel(applyStatus)) + (latest.appliedAt ? ' · ' + escapeHtml(formatClock(latest.appliedAt)) : '') + '</p></section>' : ''
    ].join("")
  };
}

function renderOntologyExperimentCard(experiment, options) {
  options = options || {};
  experiment = experiment || {};
  var id = String(experiment.id || experiment.experimentId || "");
  var latest = ontologyExperimentLatestRun(experiment);
  var candidateRules = Array.isArray(experiment.candidateRules) ? experiment.candidateRules : [];
  var symbols = Array.isArray(experiment.symbols) ? experiment.symbols : [];
  var status = String(experiment.status || "draft");
  var active = status.toLowerCase() === "active";
  var actionBusy = experimentsState.ontologyExperimentAction && String(experimentsState.ontologyExperimentAction).indexOf(":" + id) >= 0;
  var recommendations = ontologyExperimentRecommendations(latest);
  var applyStatus = String(latest.applyStatus || ((latest.appliedOntologyChanges || {}).status) || "");
  var appliedAt = latest.appliedAt || ((latest.appliedOntologyChanges || {}).appliedAt) || "";
  var applied = applyStatus === "applied" || applyStatus === "already-applied";
  var selected = id && id === experimentsState.activeOntologyExperimentId;
  var gate = ontologyExperimentPromotionGate(experiment, latest);
  return [
    '<section class="ontology-experiment-card' + (selected ? " active" : "") + '"' + cardTypeAttrs("relationship-card", selected || active ? "watch" : "hold") + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<div class="ontology-experiment-card-head">',
    '<div>',
    '<strong>' + escapeHtml(experiment.title || "Ontology experiment") + '</strong>',
    '<span>' + escapeHtml(id || "-") + '</span>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(selected ? "watch" : ontologyExperimentStatusTone(status)) + '">' + escapeHtml(selected ? "검토 기준" : ontologyExperimentStatusLabel(status)) + '</span>',
    '</div>',
    experiment.hypothesis ? '<p>' + escapeHtml(experiment.hypothesis) + '</p>' : '',
    '<div class="ontology-experiment-card-metrics">',
    renderOntologyExperimentMetric("후보 규칙", candidateRules.length, "rules"),
    renderOntologyExperimentMetric("그래프", latest.graphRunCount || 0, "graphs"),
    renderOntologyExperimentMetric("파생 변화", latest.derivedRelationDelta || 0, "delta"),
    renderOntologyExperimentMetric("판정", ontologyReadinessLabel(latest.promotionStatus), "readiness"),
    '</div>',
    symbols.length ? '<div class="theme-radar ontology-experiment-tags">' + symbols.slice(0, 12).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
    recommendations.length ? renderOntologyExperimentRecommendationList(recommendations, 2) : '',
    applyStatus ? '<p class="subtle">운영 반영 ' + escapeHtml(ontologyApplyStatusLabel(applyStatus)) + (appliedAt ? ' · ' + escapeHtml(formatClock(appliedAt)) : '') + '</p>' : '',
    gate.reasonLabel ? '<p class="subtle">승격 게이트 ' + escapeHtml(gate.reasonLabel) + '</p>' : '',
    renderRecordChangedAt(experiment, latest.completedAt),
    '<div class="ontology-experiment-card-actions">',
    options.selectable ? '<button class="text-button compact' + (selected ? " primary" : "") + '" type="button" data-lab-select="' + escapeHtml(id) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("select")) + (!id || selected ? ' disabled' : '') + '>' + escapeHtml(selected ? "선택됨" : "검토 기준") + '</button>' : '',
    renderWorkDetailButton("ontology-experiment", id, "상세", "text-button compact", ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("detail"))),
    recommendations.length && latest.completedAt ? '<button class="text-button primary" type="button" data-lab-apply="' + escapeHtml(id) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("applyRecommendation")) + (actionBusy || !id || applied ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("apply", id) ? "반영 중" : (applied ? "반영됨" : "제안 적용")) + '</button>' : '',
    '<button class="text-button" type="button" data-lab-run="' + escapeHtml(id) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("run")) + (actionBusy || !id ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("run", id) ? "실행 중" : "실행") + '</button>',
    active ? '<button class="text-button" type="button" data-lab-pause="' + escapeHtml(id) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("pause")) + (actionBusy || !id ? ' disabled' : '') + '>일시정지</button>' : '<button class="text-button primary" type="button" data-lab-activate="' + escapeHtml(id) + '"' + ontologyExperimentTooltipAttrs(ontologyExperimentActionTooltip("activate")) + (actionBusy || !id ? ' disabled' : '') + '>활성화</button>',
    '</div>',
    '</section>'
  ].join("");
}

export { ontologyExperimentWorkDetailPayload, renderOntologyExperimentListPanel, renderOntologyExperimentRecommendation };
