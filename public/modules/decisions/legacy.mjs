import { buildTradeSignalItems, categoricalModelState, compactStrategyDataSymbols, compactSymbolList, decisionStateMeta, modelFeatureAudit, modelFeatureVariables, modelStatsForItems, strategyDataDiagnostics } from "./signals.mjs";
import { stockDisplayMeta, stockDisplayName } from "../instruments/catalog.mjs";
import { renderAlertDeliveryPanel } from "../notifications/alerts-view.mjs";
import { renderLabStat } from "../ontology/graphs.mjs";
import { defaultSettings } from "../settings/defaults.mjs";
import { renderModelFormulaField, renderModelSettingField, settingValue, settingsSaveButtonClass, settingsSaveButtonLabel, settingsSaveDisabledAttr } from "../settings/fields.mjs";
import { formatPrice, formatSignalNumber, formatSignalRatio, formatSignalVolume, latestChangedFirst, recordChangedAt, renderRecordChangedAt, signedPct, sourceLabel } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs } from "../shell/layout.mjs";
import { shellState } from "../state/shell.mjs";

function renderModelOperatingGuidePanel(snapshot) {
  var items = buildTradeSignalItems(snapshot);
  var stats = modelStatsForItems(items);
  var cards = [
    {
      label: "1. 종목",
      value: "보유·관심",
      description: "계좌 보유 종목과 관심 종목을 같은 기준으로 계산합니다."
    },
    {
      label: "2. 가격",
      value: "적정가",
      description: "EPS, 목표 PER, 안전마진으로 현재가가 비싼지 싼지 봅니다."
    },
    {
      label: "3. 수급",
      value: "방향성 거래량",
      description: "거래량은 매수비중, 가격 변화, 추세와 같은 방향일 때만 강하게 반영합니다."
    },
    {
      label: "4. 행동",
      value: "확인 단계",
      description: "평소 관찰, 변화 관찰, 조건 확인, 대응 준비, 즉시 재확인, 판단 보류로 구분합니다."
    }
  ];
  var steps = [
    ["원시 값 확인", "손익률, 평균 가격, 수급, 거래량, 가치평가가 실제 값인지 먼저 확인합니다."],
    ["조건 확인", "어떤 TypeDB 관계 조건이 새로 성립했는지 확인합니다."],
    ["반대 근거 확인", "위험 근거와 버티는 근거가 함께 있는지 분리해 봅니다."],
    ["자료·검증 확인", "자료가 부족하거나 검증이 보류된 판단은 실행 후보로 올리지 않습니다."]
  ];
  return [
    '<article class="panel model-guide-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Strategy Operations</p>',
    '<h2>전략 운영 기준 관리</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(stats.actionCount) + '</span>',
    '</div>',
    '<div class="model-guide-grid">',
    cards.map(renderModelGuideCard).join(""),
    '</div>',
    '<div class="model-step-list">',
    steps.map(function (step, index) {
      return '<div><b>' + escapeHtml(index + 1) + '</b><span><strong>' + escapeHtml(step[0]) + '</strong><em>' + escapeHtml(step[1]) + '</em></span></div>';
    }).join(""),
    '</div>',
    '<div class="rule-strip"><span>이 화면은 주문 실행이 아니라 조건과 상태 전이를 검증하는 운영 화면입니다.</span><span>실제 값, 성립 조건, 반대 근거, 자료 상태를 그대로 추적합니다.</span></div>',
    '</article>'
  ].join("");
}

function valuationBeginnerText(item) {
  var valuation = item && item.valuation ? item.valuation : {};
  if (!item || !item.currentPrice || !valuation.fairValue) {
    return "가격 예시는 현재가와 적정가가 모두 있을 때 계산됩니다.";
  }
  var gap = Number(valuation.gap || 0);
  var direction = gap >= 0 ? "적정가보다 낮게 거래되어 싸게 보는 쪽" : "적정가보다 높게 거래되어 비싸게 보는 쪽";
  return item.name + " 예시: 현재가 " + formatPrice(item.currentPrice, item.currency)
    + ", 적정가 " + formatPrice(valuation.fairValue, item.currency)
    + "입니다. 적정가와 현재가 차이가 " + signedPct(gap) + "라 " + direction + "입니다.";
}

function volumeBeginnerText(item, variables) {
  var ratio = Number(variables.volumeRatio || 0);
  if (!ratio) return "거래량은 평소보다 관심이 늘었는지 보는 값입니다. 방향성 거래량은 그 관심이 매수 쪽인지 매도 쪽인지 분리합니다.";
  var direction = variables.flowDirection === "buy" ? "매수 쪽 조건이 더 많음" : (variables.flowDirection === "sell" ? "매도 쪽 조건이 더 많음" : "아직 뚜렷한 방향 없음");
  return item.name + "의 거래량은 평소 대비 " + formatSignalRatio(ratio)
    + "입니다. 체결·호가·가격 조건을 함께 보면 " + direction + "으로 읽습니다.";
}

function decisionBeginnerText(item, model) {
  return item.name + "의 현재 판단은 '" + model.action + "'입니다. "
    + decisionStateMeta("review", model.reviewLevel, "observe").label + " 단계이며, "
    + decisionStateMeta("conflict", model.conflictState, "context-only").label + " 상태입니다.";
}

function beginnerModelRows(item, model) {
  if (!item || !model) {
    return [
      ["가격", "보유 또는 관심 종목 데이터가 들어오면 현재가와 적정가 예시를 표시합니다."],
      ["수급", "거래량, 매수비중, 이동평균을 쉬운 문장으로 풀어 표시합니다."],
      ["판단", "성립 조건과 반대 근거를 비교해 왜 해당 행동이 나왔는지 설명합니다."]
    ];
  }
  var variables = model.variables || item.features || modelFeatureVariables(item, item.signal || {}, item.valuation || {});
  return [
    ["가격", valuationBeginnerText(item)],
    ["거래량", volumeBeginnerText(item, variables)],
    ["종합", decisionBeginnerText(item, model)]
  ];
}

function renderStrategyDataPanel(snapshot) {
  var diagnostics = strategyDataDiagnostics(snapshot).map(function (item) {
    return Object.assign({}, item, { updatedAt: recordChangedAt(item, (snapshot || {}).generatedAt) });
  });
  var issueCount = diagnostics.filter(function (item) {
    return item.tone === "caution" || item.tone === "danger";
  }).length;
  return [
    '<article class="panel strategy-data-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Data Readiness</p>',
    '<h2>전략 데이터 점검</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(issueCount) + '</span>',
    '</div>',
    '<div class="strategy-data-grid">',
    diagnostics.map(renderStrategyDataRow).join(""),
    '</div>',
    '</article>'
  ].join("");
}

function renderStrategyDataRow(item) {
  var fullSymbols = compactSymbolList(item.symbols || []);
  var symbols = compactStrategyDataSymbols(item.symbols || []);
  return [
    '<div class="strategy-data-row"' + cardTypeAttrs("diagnostic-card", item.tone || "hold") + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<div class="strategy-data-main">',
    '<strong>' + escapeHtml(item.label) + '</strong>',
    '<span>' + escapeHtml(item.description) + '</span>',
    '<em>채울 곳: ' + escapeHtml(item.action) + '</em>',
    '</div>',
    '<div class="strategy-data-status">',
    '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.value) + '</span>',
    '<b title="' + escapeHtml(fullSymbols) + '">' + escapeHtml(symbols) + '</b>',
    renderRecordChangedAt(item),
    '</div>',
    '</div>'
  ].join("");
}

function renderModelGuideCard(card) {
  return [
    '<div class="model-guide-step"' + cardTypeAttrs("reference-card") + '>',
    '<em>' + escapeHtml(card.label) + '</em>',
    '<strong>' + escapeHtml(card.value) + '</strong>',
    '<p>' + escapeHtml(card.description) + '</p>',
    '</div>'
  ].join("");
}

function renderAdminModelingPanel(snapshot) {
  var items = buildTradeSignalItems(snapshot);
  var stats = modelStatsForItems(items);
  var reviewStates = ["normal", "observe", "check", "act", "immediate", "blocked"];
  return [
    '<article class="panel admin-modeling-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Operations Policy</p>',
    '<h2>모델·알림 정책 관리</h2>',
    '</div>',
    '<div class="settings-actions">',
    '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
    '</div>',
    '</div>',
    '<div class="lab-stats-grid model-stats-grid">',
    renderLabStat("대응 준비", stats.actionCount, "개"),
    renderLabStat("조건 확인", stats.checkCount, "개"),
    renderLabStat("판단 보류", stats.blockedCount, "개"),
    '</div>',
    '<div class="model-editor">',
    '<div class="settings-note model-settings-note">',
    '<strong>상태 기반 판단</strong>',
    '<p>손익률·가격·수급 같은 실제 값이 TypeDB 조건을 만족하면 확인 단계가 바뀝니다. 서로 다른 조건은 각각 독립적으로 남깁니다.</p>',
    '</div>',
    '<div class="settings-grid">',
    renderModelSettingField("modelName", "운영 정책 이름", "text", "나의 모델"),
    renderModelFormulaField("modelHypothesis", "보조 모델 설명", "예: 수급이 살아 있고 적정가보다 싸며 리스크가 낮을 때만 산다."),
    '</div>',
    '<div class="model-section">',
    '<div class="flow-title"><div><strong>확인 단계</strong><span>낮은 단계부터 높은 단계로 상태를 올리되 자료 부족은 별도로 판단을 막습니다.</span></div></div>',
    '<div class="model-setting-card-grid threshold-grid">',
    reviewStates.map(function (key) {
      var meta = decisionStateMeta("review", key, "normal");
      return '<div class="model-setting-card"><span><strong>' + escapeHtml(meta.label) + '</strong><em>' + escapeHtml(key === "blocked" ? "자료나 검증 문제로 행동 판단을 만들지 않음" : "성립 조건과 근거 역할로 결정") + '</em></span><span class="tone-chip ' + escapeHtml(meta.tone) + '">' + escapeHtml(key) + '</span></div>';
    }).join(""),
    '</div>',
    '</div>',
    '<div class="model-section">',
    '<div class="flow-title"><div><strong>실제 값 기준</strong><span>수치 자체는 유지하되 합산하지 않습니다. 각 조건이 어떤 상태 전이를 만드는지만 관리합니다.</span></div></div>',
    '<label class="setting-field wide"><span>관계 조건 임계값</span><textarea data-model-setting="relationRuleThresholds" rows="12" autocomplete="off">' + escapeHtml(settingValue("relationRuleThresholds") || defaultSettings.relationRuleThresholds || "") + '</textarea></label>',
    '<div class="rule-strip"><span>예: 손실률 -8%, 가격 변화 1%, 거래량 1.5배는 각각 독립된 사실 조건입니다.</span><span>최종 화면에는 확인 단계, 근거 충돌, 자료 상태, 검증 상태가 표시됩니다.</span></div>',
    '</div>',
    '</div>',
    '</article>'
  ].join("");
}

function renderAdminDeliveryPanel() {
  return renderAlertDeliveryPanel();
}

function renderModelPreviewPanel(snapshot) {
  var items = latestChangedFirst(buildTradeSignalItems(snapshot).map(function (item) {
    return Object.assign({}, item, { model: categoricalModelState(item) });
  }), function (item) {
    return recordChangedAt(item, (snapshot || {}).generatedAt);
  }, function (a, b) {
    return Number((a.model || {}).rank || 0) - Number((b.model || {}).rank || 0);
  });
  return [
    '<article class="panel model-preview-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Model Preview</p>',
    '<h2>현재 종목 판단 결과</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(items.length) + '</span>',
    '</div>',
    '<div class="signal-list">',
    items.length ? items.map(renderModelPreviewRow).join("") : '<p class="subtle">보유 종목이나 관심 종목이 있으면 여기서 매수 후보, 보유 강화, 분할매도 판단이 표시됩니다.</p>',
    '</div>',
    '</article>'
  ].join("");
}

function renderModelPreviewRow(item) {
  var model = item.model || categoricalModelState(item);
  var displayName = stockDisplayName(item.symbol, item);
  var review = decisionStateMeta("review", model.reviewLevel, "observe");
  var data = decisionStateMeta("data", model.dataState, "partial");
  var change = decisionStateMeta("change", model.changeState, "unchanged");
  var conflict = decisionStateMeta("conflict", model.conflictState, "context-only");
  var validation = decisionStateMeta("validation", model.validationState, "conditional");
  return [
    '<div class="signal-row model-preview-row"' + cardTypeAttrs("signal-card", model.tone || "hold") + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<div class="signal-main">',
    '<div class="flow-title">',
    '<div>',
    '<strong>' + escapeHtml(displayName) + '</strong>',
    '<span>' + escapeHtml(stockDisplayMeta(item, [sourceLabel(item.source), "현재 " + (item.currentPrice ? formatPrice(item.currentPrice, item.currency) : "-")])) + '</span>',
    renderRecordChangedAt(item, (shellState.snapshot || {}).generatedAt),
    '</div>',
    '<span class="tone-chip ' + escapeHtml(model.tone || "hold") + '">' + escapeHtml(model.action) + '</span>',
    '</div>',
    renderModelRelationRuleSummary(item),
    '<div class="lab-model-grid">',
    '<span>확인 단계 <strong>' + escapeHtml(review.label) + '</strong></span>',
    '<span>자료 상태 <strong>' + escapeHtml(data.label) + '</strong></span>',
    '<span>변화 <strong>' + escapeHtml(change.label) + '</strong></span>',
    '<span>근거 관계 <strong>' + escapeHtml(conflict.label) + '</strong></span>',
    '<span>AI 검증 <strong>' + escapeHtml(validation.label) + '</strong></span>',
    '<span>성립 조건 <strong>' + escapeHtml((item.relationRules || []).length) + '</strong></span>',
    '</div>',
    renderModelPlainLanguageExplanation(item, model),
    renderModelFeatureAudit(item, model),
    '</div>',
    '</div>'
  ].join("");
}

function renderModelRelationRuleSummary(item) {
  var rules = item.relationRules || [];
  var first = rules[0] || {};
  var firstRole = decisionStateMeta("evidence", first.evidenceRole, "context");
  return [
    '<div class="model-feature-audit model-relation-summary"' + cardTypeAttrs("relationship-card", first.tone || firstRole.tone) + '>',
    '<div class="feature-audit-head">',
    '<strong>관계 규칙</strong>',
    '<span class="tone-chip ' + escapeHtml(first.tone || firstRole.tone) + '">' + escapeHtml(rules.length ? firstRole.label : "성립 조건 없음") + '</span>',
    '</div>',
    '<div class="feature-audit-grid">',
    rules.length ? rules.slice(0, 4).map(function (rule) {
      var role = decisionStateMeta("evidence", rule.evidenceRole, "context");
      var review = decisionStateMeta("review", rule.reviewLevel, "observe");
      return '<span>' + escapeHtml(rule.label) + ' <strong>' + escapeHtml(role.label + " · " + review.label) + '</strong></span>';
    }).join("") : '<span>성립한 관계 규칙이 없습니다.</span>',
    '</div>',
    '</div>'
  ].join("");
}

function renderModelPlainLanguageExplanation(item, model) {
  var rows = beginnerModelRows(item, model);
  return [
    '<div class="model-feature-audit model-plain-explain"' + cardTypeAttrs("reference-card") + '>',
    '<div class="feature-audit-head">',
    '<strong>쉬운 해석</strong>',
    '<span class="tone-chip hold">실제 데이터 예시</span>',
    '</div>',
    '<div class="variable-grid">',
    rows.map(function (row) {
      return '<span><strong>' + escapeHtml(row[0]) + '</strong>' + escapeHtml(row[1]) + '</span>';
    }).join(""),
    '</div>',
    '</div>'
  ].join("");
}

function renderModelFeatureAudit(item) {
  if (!item.hasData) {
    return [
      '<div class="model-feature-audit"' + cardTypeAttrs("diagnostic-card", "hold") + '>',
      '<div class="feature-audit-head">',
      '<strong>재계산 확인</strong>',
      '<span class="tone-chip hold">데이터 부족</span>',
      '</div>',
      '<div class="feature-audit-grid"><span>현재가, 이동평균, 거래량, 투자자별 수급이 채워지면 TypeDB 성립 조건과 자료 상태를 다시 확인합니다.</span></div>',
      '</div>'
    ].join("");
  }
  var audit = modelFeatureAudit(item);
  var variables = audit.variables || {};
  var signal = item.signal || {};
  var flowLabel = variables.flowDirection === "buy" ? "매수 쪽 조건 우세" : (variables.flowDirection === "sell" ? "매도 쪽 조건 우세" : "엇갈림");
  var featureRows = [
    ["거래량", formatSignalRatio(variables.volumeRatio)],
    ["체결강도", formatSignalNumber(variables.tradeStrength, "")],
    ["흐름 방향", flowLabel],
    ["20일선 차이", formatSignalNumber(variables.trendDistance20, "%")],
    ["60일선 차이", formatSignalNumber(variables.trendDistance60, "%")],
    ["외국인", formatSignalVolume(signal.foreignNet)],
    ["기관", formatSignalVolume(signal.institutionNet)],
    ["개인", formatSignalVolume(signal.individualNet)],
    ["외국인+기관", formatSignalVolume(variables.smartMoneyNet)]
  ];
  return [
    '<div class="model-feature-audit"' + cardTypeAttrs("diagnostic-card", audit.stable ? "watch" : "caution") + '>',
    '<div class="feature-audit-head">',
    '<strong>재계산 확인</strong>',
    '<span class="tone-chip ' + (audit.stable ? "watch" : "caution") + '">' + (audit.stable ? "같은 입력 재현됨" : "재계산 확인 필요") + '</span>',
    '</div>',
    '<div class="feature-audit-grid">',
    featureRows.map(function (row) {
      return '<span>' + escapeHtml(row[0]) + ' <strong>' + escapeHtml(row[1]) + '</strong></span>';
    }).join(""),
    '</div>',
    '<div class="feature-delta-grid">',
    audit.groups.map(function (group) {
      var role = decisionStateMeta("evidence", group.key, "context");
      return '<span class="' + escapeHtml(role.tone) + '"><b>' + escapeHtml(group.label) + '</b><strong>' + escapeHtml(group.count + "개") + '</strong><em>' + escapeHtml(role.label) + '</em></span>';
    }).join(""),
    '</div>',
    '<div class="feature-contribution-grid">',
    '<strong>성립한 조건</strong>',
    (audit.conditions || []).slice(0, 6).map(function (condition) {
      var role = decisionStateMeta("evidence", condition.evidenceRole, "context");
      return '<span class="' + escapeHtml(role.tone) + '"><b>' + escapeHtml(condition.label || "조건") + '</b><strong>' + escapeHtml(role.label) + '</strong><em>' + escapeHtml(decisionStateMeta("review", condition.reviewLevel, "observe").label) + '</em></span>';
    }).join("") || '<span><b>성립 조건 없음</b><em>현재 상태 유지</em></span>',
    '</div>',
    '</div>'
  ].join("");
}

export { renderAdminDeliveryPanel, renderAdminModelingPanel, renderModelPreviewPanel, renderStrategyDataPanel };
