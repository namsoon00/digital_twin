import { compactPlanList, renderAddBuyAssessment } from "../decisions/evidence.mjs";
import { decisionStateMeta } from "../decisions/signals.mjs";
import { stockDisplayName, textWithKnownDisplaySymbols } from "../instruments/catalog.mjs";
import { ontologyEntityDisplayLabel } from "./graphs.mjs";
import { formatClock, latestChangedFirst, recordChangedAt, renderRecordChangedAt } from "../shared/format.mjs";
import { beginnerFriendlyText, escapeHtml } from "../shared/text.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";

function ontologyExecutionRows(cards, parts) {
  var rows = [];
  var seen = {};
  (cards || []).forEach(function (card) {
    var plans = Array.isArray(card.executionPlans) ? card.executionPlans : [];
    plans.forEach(function (plan) {
      if (!plan || typeof plan !== "object") return;
      var symbol = String(plan.symbol || card.symbol || "").toUpperCase();
      var primary = String(plan.primaryActionLabel || plan.primaryAction || "").trim();
      var key = [symbol, primary, plan.decisionStage || ""].join("|");
      if (seen[key]) return;
      seen[key] = true;
      rows.push({
        symbol: symbol,
        displayName: card.companyName || card.displayName || stockDisplayName(symbol),
        relation: card.portfolioRelation || "",
        opinion: card.finalOpinion || {},
        plan: plan
      });
    });
  });
  if (!rows.length && parts && Array.isArray(parts.activeInvestmentOpinions)) {
    parts.activeInvestmentOpinions.forEach(function (opinion) {
      var plan = opinion && typeof opinion.executionPlan === "object" ? opinion.executionPlan : null;
      if (!plan) return;
      var symbol = String(opinion.symbol || plan.symbol || "").toUpperCase();
      var key = [symbol, plan.primaryActionLabel || plan.primaryAction || ""].join("|");
      if (seen[key]) return;
      seen[key] = true;
      rows.push({
        symbol: symbol,
        displayName: stockDisplayName(symbol),
        relation: "",
        opinion: opinion,
        plan: plan
      });
    });
  }
  return latestChangedFirst(rows, function (row) {
    return recordChangedAt(row.plan, recordChangedAt(row.opinion));
  });
}

function renderOntologyExecutionPlanPanel(cards, parts) {
  var rows = ontologyExecutionRows(cards || [], parts || {});
  return [
    '<section class="ontology-surface ontology-execution-surface">',
    '<div class="ontology-surface-head">',
    '<strong>실행 계획과 다음 확인</strong>',
    '<span>' + escapeHtml(rows.length) + ' plans · 보유/관심 판단 이후 확인할 조건</span>',
    '</div>',
    '<div class="ontology-execution-list">',
    rows.length ? rows.slice(0, 8).map(renderOntologyExecutionPlanRow).join("") : '<div class="ontology-empty">실행 계획 데이터가 아직 없습니다.</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyExecutionPlanRow(row) {
  var plan = row.plan || {};
  var nextChecks = compactPlanList(plan.nextChecks, 3);
  var blocked = compactPlanList(plan.blockedActions, 2);
  var strengthen = compactPlanList(plan.strengthenConditions, 2);
  var weaken = compactPlanList(plan.weakenConditions, 2);
  var primary = plan.primaryActionLabel || plan.primaryAction || "실행 판단 대기";
  var addBuy = renderAddBuyAssessment(plan.addBuyAssessment);
  var tone = (row.opinion || {}).tone || (plan.actionLevel === "action" ? "caution" : "hold");
  return [
    '<div class="ontology-execution-row"' + cardTypeAttrs("action-queue-card", tone || "hold") + '>',
    '<div class="ontology-execution-title">',
    '<strong>' + escapeHtml(row.displayName || row.symbol || "-") + '</strong>',
    '<span>' + escapeHtml([row.relation, plan.decisionStage, plan.actionGroup, plan.actionLevel].filter(Boolean).join(" · ") || "관계 조건 확인") + '</span>',
    renderRecordChangedAt(plan, recordChangedAt(row.opinion)),
    '</div>',
    '<span class="tone-chip ' + escapeHtml(tone || "hold") + '">' + escapeHtml(primary) + '</span>',
    addBuy,
    '<div class="ontology-execution-detail">',
    renderOntologyPlanMiniList("다음 확인", nextChecks),
    renderOntologyPlanMiniList("보류", blocked),
    renderOntologyPlanMiniList("강화", strengthen),
    renderOntologyPlanMiniList("약화", weaken),
    '</div>',
    '</div>'
  ].join("");
}

function renderOntologyPlanMiniList(label, rows) {
  return [
    '<div class="ontology-plan-mini-list">',
    '<strong>' + escapeHtml(label) + '</strong>',
    rows.length ? rows.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") : '<span>-</span>',
    '</div>'
  ].join("");
}

function renderOntologyOperationalPanel(parts) {
  var operational = (parts || {}).operationalOntology || {};
  var pipelines = latestChangedFirst(Array.isArray(operational.pipelines) ? operational.pipelines : [], function (pipeline) {
    return recordChangedAt(pipeline, recordChangedAt(operational));
  });
  return [
    '<section class="ontology-surface ontology-operational-surface">',
    '<div class="ontology-surface-head">',
    '<strong>운영 온톨로지</strong>',
    '<span>' + escapeHtml(operational.dispatchMode || "insight-driven-only") + '</span>',
    '</div>',
    '<div class="ontology-operational-grid">',
    renderOntologyOperationalMetric("수집 파이프라인", operational.collectionPipelineCount || pipelines.length || 0, "DataPipeline"),
    renderOntologyOperationalMetric("인사이트", operational.insightCount || ((parts || {}).insights || []).length || 0, "Insight"),
    renderOntologyOperationalMetric("디스패치", operational.dispatchMode || "-", "NotificationDispatch"),
    '</div>',
    '<div class="ontology-operational-list">',
    pipelines.length ? pipelines.map(function (pipeline) {
      return [
        '<div class="ontology-operational-row"' + cardTypeAttrs("source-card") + '>',
        '<strong>' + escapeHtml(pipeline.key || "-") + '</strong>',
        '<span>target ' + escapeHtml(pipeline.targetMinutes || "-") + '분</span>',
        '<em>configured ' + escapeHtml(pipeline.configuredMinutes || "-") + '분</em>',
        renderRecordChangedAt(pipeline, recordChangedAt(operational)),
        '</div>'
      ].join("");
    }).join("") : '<div class="ontology-empty">수집 파이프라인 정보가 없습니다.</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyOperationalMetric(label, value, caption) {
  return [
    '<div class="ontology-operational-metric"' + cardTypeAttrs("metric-cell") + '>',
    '<span>' + escapeHtml(caption || "") + '</span>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '<em>' + escapeHtml(label) + '</em>',
    '</div>'
  ].join("");
}

function renderOntologyInsightPanel(parts) {
  parts = parts || {};
  var insights = Array.isArray(parts.insights) && parts.insights.length
    ? parts.insights
    : (parts.aboxEntities || []).filter(function (item) { return String(item && item.kind || "") === "insight"; });
  insights = latestChangedFirst(insights);
  return [
    '<section class="ontology-surface ontology-insight-surface">',
    '<div class="ontology-surface-head">',
    '<strong>인사이트·알림 디스패치</strong>',
    '<span>' + escapeHtml(insights.length) + ' insights · ' + escapeHtml(((parts.operationalOntology || {}).dispatchMode) || "insight-driven-only") + '</span>',
    '</div>',
    '<div class="ontology-insight-list">',
    insights.length ? insights.slice(0, 8).map(renderOntologyInsightRow).join("") : '<div class="ontology-empty">생성된 인사이트가 없습니다.</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyInsightRow(item) {
  var properties = (item && item.properties) || {};
  var review = decisionStateMeta("review", properties.reviewLevel || properties.review_level, "observe");
  var validation = decisionStateMeta("validation", properties.validationState || properties.validation_state, "conditional");
  var tone = review.tone;
  return [
    '<div class="ontology-insight-row"' + cardTypeAttrs("signal-card", tone || "hold") + '>',
    '<div>',
    '<strong>' + escapeHtml(ontologyEntityDisplayLabel(item, item && item.id)) + '</strong>',
    '<span>' + escapeHtml([properties.symbol, properties.insightType, properties.dispatchCandidate ? "dispatch candidate" : "reference"].filter(Boolean).join(" · ")) + '</span>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(tone || "hold") + '">' + escapeHtml(review.label) + '</span>',
    '<em>' + escapeHtml(validation.label) + '</em>',
    renderRecordChangedAt(item),
    properties.thesis ? '<p>' + escapeHtml(textWithKnownDisplaySymbols(beginnerFriendlyText(properties.thesis), properties.symbol, { symbol: properties.symbol })) + '</p>' : '',
    '</div>'
  ].join("");
}

function renderOntologyDataQualityPanel(parts) {
  parts = parts || {};
  var nodes = Array.isArray(parts.dataQuality) && parts.dataQuality.length
    ? parts.dataQuality
    : (parts.aboxEntities || []).filter(function (item) {
      return ["data-quality", "data-freshness", "provenance", "source-reliability", "missing-data"].indexOf(String(item && item.kind || "")) >= 0;
    });
  nodes = latestChangedFirst(nodes);
  return [
    '<section class="ontology-surface ontology-data-quality-surface">',
    '<div class="ontology-surface-head">',
    '<strong>데이터 품질·출처</strong>',
    '<span>' + escapeHtml(nodes.length) + ' nodes · freshness/provenance</span>',
    '</div>',
    '<div class="ontology-data-quality-list">',
    nodes.length ? nodes.slice(0, 10).map(renderOntologyDataQualityRow).join("") : '<div class="ontology-empty">데이터 품질 노드가 없습니다.</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyDataQualityRow(item) {
  var properties = (item && item.properties) || {};
  var data = decisionStateMeta("data", properties.dataState || properties.data_state, properties.status === "error" ? "unavailable" : (properties.status === "stale" ? "partial" : "sufficient"));
  var value = data.label;
  var meta = [
    item && item.kind,
    properties.fetchedAt ? formatClock(properties.fetchedAt) : "",
    properties.ageMinutes != null ? Math.round(Number(properties.ageMinutes || 0)) + "분" : "",
    Array.isArray(properties.sources) ? properties.sources.slice(0, 3).join(", ") : ""
  ].filter(Boolean).join(" · ");
  return [
    '<div class="ontology-data-quality-row"' + cardTypeAttrs("diagnostic-card") + '>',
    '<div>',
    '<strong>' + escapeHtml(ontologyEntityDisplayLabel(item, item && item.id)) + '</strong>',
    '<span>' + escapeHtml(meta || "품질 정보") + '</span>',
    '</div>',
    '<em>' + escapeHtml(value) + '</em>',
    renderRecordChangedAt(item),
    '</div>'
  ].join("");
}

export { renderOntologyDataQualityPanel, renderOntologyExecutionPlanPanel, renderOntologyInsightPanel };
