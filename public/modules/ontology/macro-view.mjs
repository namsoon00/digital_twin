import { ontologyAboxEntities, ontologyAboxRelations, ontologyEndpointLabel, ontologyEntityDisplayLabel, ontologyEntityLabelMap, ontologyTypeOf } from "./graphs.mjs";
import { formatClock, latestChangedFirst, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";

function ontologyMacroSignalData(parts) {
  parts = parts || {};
  var entities = Array.isArray(parts.aboxEntities) ? parts.aboxEntities : ontologyAboxEntities(parts.entities || []);
  var relations = Array.isArray(parts.aboxRelations) ? parts.aboxRelations : ontologyAboxRelations(parts.relations || []);
  var fxSignals = [];
  var rateSignals = [];
  var signalIds = {};
  entities.forEach(function (entity) {
    var kind = String(entity && entity.kind || "");
    if (kind === "fx-rate") {
      fxSignals.push(entity);
      signalIds[entity.id] = true;
    }
    if (kind === "interest-rate" || kind === "yield-curve") {
      rateSignals.push(entity);
      signalIds[entity.id] = true;
    }
  });
  var macroRelations = relations.filter(function (relation) {
    var type = ontologyTypeOf(relation);
    if (type !== "HAS_FX_EXPOSURE" && type !== "HAS_RATE_SENSITIVITY") return false;
    return Boolean(signalIds[relation.source] || signalIds[relation.target]);
  });
  return {
    fxSignals: latestChangedFirst(fxSignals),
    rateSignals: latestChangedFirst(rateSignals),
    fxRelations: latestChangedFirst(macroRelations.filter(function (relation) { return ontologyTypeOf(relation) === "HAS_FX_EXPOSURE"; })),
    rateRelations: latestChangedFirst(macroRelations.filter(function (relation) { return ontologyTypeOf(relation) === "HAS_RATE_SENSITIVITY"; })),
    macroRelations: latestChangedFirst(macroRelations)
  };
}

function ontologyMacroNumber(value) {
  var number = Number(value || 0);
  return isFinite(number) ? number : 0;
}

function ontologyMacroHasValue(value) {
  return value !== undefined && value !== null && value !== "" && isFinite(Number(value));
}

function ontologyMacroValueText(entity) {
  entity = entity || {};
  var properties = entity.properties || {};
  var kind = String(entity.kind || "");
  if (kind === "fx-rate") {
    var base = String(properties.baseCurrency || properties.base || "").toUpperCase();
    var quote = String(properties.quoteCurrency || properties.quote || "").toUpperCase();
    var rate = ontologyMacroNumber(properties.rate || properties.value);
    if (base && quote && rate) return "1 " + base + " = " + rate.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) + " " + quote;
    return rate ? rate.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) : "-";
  }
  var rateValue = ontologyMacroNumber(properties.value);
  var suffix = kind === "yield-curve" ? "%p" : "%";
  return ontologyMacroHasValue(properties.value) ? rateValue.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) + suffix : "-";
}

function ontologyMacroMetaText(entity) {
  entity = entity || {};
  var properties = entity.properties || {};
  var rows = [];
  if (properties.seriesId) rows.push(String(properties.seriesId).toUpperCase());
  if (properties.pair) rows.push(String(properties.pair).toUpperCase());
  if (properties.provider) rows.push(String(properties.provider));
  if (properties.date) rows.push(String(properties.date));
  if (properties.fetchedAt) rows.push(formatClock(properties.fetchedAt));
  return rows.join(" · ") || "온톨로지 현재 데이터";
}

function ontologyMacroRelationCount(entity, relations) {
  var id = String(entity && entity.id || "");
  return (relations || []).filter(function (relation) {
    return relation && (relation.source === id || relation.target === id);
  }).length;
}

function ontologyMacroTone(entity, relations) {
  var id = String(entity && entity.id || "");
  var linked = (relations || []).filter(function (relation) {
    return relation && (relation.source === id || relation.target === id);
  });
  if (linked.some(function (relation) {
    return String((relation.properties || {}).polarity || "") === "risk";
  })) return "danger";
  return String(entity && entity.kind || "") === "fx-rate" ? "watch" : "hold";
}

function renderOntologyMacroSignalPanel(parts) {
  var data = ontologyMacroSignalData(parts);
  var total = data.fxSignals.length + data.rateSignals.length;
  return [
    '<article class="panel macro-signal-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Macro Ontology Signals</p>',
    '<h2>환율·금리 관계 신호</h2>',
    '<p class="subtle">환율과 금리는 각각 별도 신호로 보고, 포트폴리오와 종목의 노출 관계를 분리해 표시합니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(total) + '</span>',
    '</div>',
    '<div class="macro-signal-grid">',
    renderOntologyMacroSignalGroup("환율", "FX exposure", data.fxSignals, data.fxRelations, "fx"),
    renderOntologyMacroSignalGroup("금리", "Rate sensitivity", data.rateSignals, data.rateRelations, "rate"),
    '</div>',
    '<div class="rule-strip">',
    '<span>FX는 HAS_FX_EXPOSURE, 금리는 HAS_RATE_SENSITIVITY 관계로 연결됩니다.</span>',
    '<span>값이 없으면 추정하지 않고 현재 온톨로지에 들어온 행만 표시합니다.</span>',
    '</div>',
    '</article>'
  ].join("");
}

function renderOntologyMacroSignalGroup(title, subtitle, signals, relations, tone) {
  return [
    '<section class="macro-signal-group ' + escapeHtml(tone || "") + '">',
    '<div class="macro-signal-group-head">',
    '<strong>' + escapeHtml(title) + '</strong>',
    '<span>' + escapeHtml(subtitle) + ' · ' + escapeHtml(signals.length) + ' signals · ' + escapeHtml(relations.length) + ' relations</span>',
    '</div>',
    '<div class="macro-signal-list">',
    signals.length ? signals.map(function (entity) {
      return renderOntologyMacroSignalRow(entity, relations);
    }).join("") : '<div class="ontology-empty">' + escapeHtml(title) + ' 신호 없음</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyMacroSignalRow(entity, relations) {
  var relationCount = ontologyMacroRelationCount(entity, relations);
  var tone = ontologyMacroTone(entity, relations);
  return [
    '<div class="macro-signal-row ' + escapeHtml(tone) + '"' + cardTypeAttrs("signal-card", tone) + '>',
    '<div>',
    '<strong>' + escapeHtml(ontologyEntityDisplayLabel(entity, entity && entity.id)) + '</strong>',
    '<span>' + escapeHtml(ontologyMacroMetaText(entity)) + '</span>',
    '</div>',
    '<em>' + escapeHtml(ontologyMacroValueText(entity)) + '</em>',
    '<b>' + escapeHtml(relationCount) + ' rel</b>',
    renderRecordChangedAt(entity),
    '</div>'
  ].join("");
}

function renderOntologyMacroRelationPanel(parts) {
  parts = parts || {};
  var data = ontologyMacroSignalData(parts);
  var labels = parts.entityLabels || ontologyEntityLabelMap(parts.entities || []);
  return [
    '<section class="ontology-surface macro-relation-panel">',
    '<div class="ontology-surface-head">',
    '<strong>환율·금리 관계 행</strong>',
    '<span>' + escapeHtml(data.fxRelations.length) + ' FX exposure · ' + escapeHtml(data.rateRelations.length) + ' rate sensitivity</span>',
    '</div>',
    '<div class="macro-relation-list">',
    data.macroRelations.length ? data.macroRelations.slice(0, 28).map(function (relation) {
      return renderOntologyMacroRelationRow(relation, labels);
    }).join("") : '<div class="ontology-empty">환율·금리 관계 행 없음</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyMacroRelationRow(relation, entityLabels) {
  var type = ontologyTypeOf(relation);
  var properties = relation.properties || {};
  var source = ontologyEndpointLabel(relation.source, entityLabels);
  var target = ontologyEndpointLabel(relation.target, entityLabels);
  var label = properties.aiInfluenceLabel || properties.rateSeriesId || properties.source || "";
  var weight = Number(relation.weight || 0);
  return [
    '<div class="macro-relation-row ' + escapeHtml(type === "HAS_FX_EXPOSURE" ? "fx" : "rate") + '"' + cardTypeAttrs("relationship-card") + '>',
    '<strong>' + escapeHtml(type) + '</strong>',
    '<span>' + escapeHtml(source + " → " + target) + '</span>',
    '<em>' + escapeHtml(label || (weight ? "weight " + weight.toFixed(2) : "-")) + '</em>',
    renderRecordChangedAt(relation),
    '</div>'
  ].join("");
}

export { ontologyMacroMetaText, ontologyMacroRelationCount, ontologyMacroSignalData, ontologyMacroValueText, renderOntologyMacroRelationPanel, renderOntologyMacroSignalPanel };
