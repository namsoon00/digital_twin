import { investmentActionByKey, investmentActionFilteredRows, investmentActionKey, investmentActionPageInfo, renderInvestmentActionDetailPanel, renderInvestmentActionPager, renderInvestmentActionRow, renderInvestmentActionToolbar } from "./actions.mjs";
import { renderInvestmentAiPacketPanel, renderInvestmentDataLineagePanel, renderInvestmentEvidenceWorkbenchPanel, renderInvestmentMoneyFlowPanel } from "./evidence.mjs";
import { renderStrategyDataPanel } from "./legacy.mjs";
import { renderInvestmentTabWorkspace } from "./navigation.mjs";
import { decisionActionMeta } from "./selectors.mjs";
import { buildTradeSignalItems, decisionStateMeta, modelStatsForItems } from "./signals.mjs";
import { renderInvestmentDecisionSummaryRail, renderInvestmentTodayActionBoardPanel, renderInvestmentTodayBlockersPanel, renderInvestmentTodaySelectedPanel, renderInvestmentTodayStatusCell, renderInvestmentTodayStatusPanel } from "./today.mjs";
import { renderHypothesisWorkspacePanel } from "../hypotheses/workspace.mjs";
import { stockDisplayMeta, stockDisplayName } from "../instruments/catalog.mjs";
import { marketLabel } from "../instruments/universe-view.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { activePageMode, activeSectionForPageMode, normalizeStrategySection } from "../navigation/routes.mjs";
import { relationRuleThresholds } from "../notifications/alerts.mjs";
import { notificationJobKey } from "../notifications/detail.mjs";
import { renderNotificationDetailMetric } from "../notifications/reasoning.mjs";
import { renderOntologyExecutionPlanPanel } from "../ontology/execution.mjs";
import { promptTemplateRows } from "../ontology/governance.mjs";
import { ontologyEntityDisplayLabel, ontologyOpinionOf } from "../ontology/graphs.mjs";
import { inferenceLedgerRows, inferenceLedgerSummary } from "../ontology/inference.mjs";
import { ontologyMacroMetaText, ontologyMacroRelationCount, ontologyMacroSignalData, ontologyMacroValueText, renderOntologyMacroSignalPanel } from "../ontology/macro-view.mjs";
import { ontologyStrategyParts } from "../ontology/strategy.mjs";
import { ontologyReadableRuleRows, ontologyRuleboxRules, renderInvestmentOntologyWorkspacePanel } from "../ontology/world.mjs";
import { renderStrategyProposalConsolePanel, strategyProposalItems } from "../proposals/workspace.mjs";
import { formatClock, formatMoney, formatPrice, hasNumericValue, recordChangedAt, sourceLabel } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { strategySections } from "../shell/catalog.mjs";
import { cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { ontologyState } from "../state/ontology.mjs";

function investmentDecisionItemMap(snapshot) {
  return ((snapshot || {}).tossDecision || {}).items ? ((snapshot || {}).tossDecision || {}).items.reduce(function (memo, item) {
    var symbol = String(item && item.symbol || "").toUpperCase();
    if (symbol) memo[symbol] = item;
    return memo;
  }, {}) : {};
}

function investmentReasoningCards(snapshot) {
  var parts = ontologyStrategyParts(snapshot);
  var cards = Array.isArray(parts.investmentAnalysis.reasoningCards) ? parts.investmentAnalysis.reasoningCards : [];
  if (!cards.length && Array.isArray(parts.strategy.reasoningCards)) cards = parts.strategy.reasoningCards;
  if (cards.length) return cards;
  var decisionMap = investmentDecisionItemMap(snapshot);
  return buildTradeSignalItems(snapshot).map(function (item) {
    var decisionItem = decisionMap[item.symbol] || {};
    var opinion = ontologyOpinionOf(decisionItem);
    return {
      id: "reasoning-card:" + item.symbol,
      symbol: item.symbol,
      companyName: stockDisplayName(item.symbol, item),
      displayName: stockDisplayName(item.symbol, item),
      source: item.source || "watchlist",
      portfolioRelation: item.source === "watchlist" ? "WATCHES" : "HOLDS",
      status: item.hasData ? "readyForAiReview" : "needsData",
      finalOpinion: {
        action: opinion.action || item.action,
        tone: opinion.tone || item.tone || "hold",
        reviewLevel: item.reviewLevel,
        dataState: item.dataState,
        changeState: item.changeState,
        conflictState: item.conflictState,
        validationState: item.validationState,
        thesis: opinion.thesis || (item.reasons || [])[0] || ""
      },
      strategyEvidence: (item.reasons || []).slice(0, 5).map(function (reason, index) {
        return { id: "client-evidence:" + item.symbol + ":" + index, kind: "strategy", source: "client-model", summary: reason, value: {}, dataState: item.dataState };
      }),
      relationEvidence: (item.relationRules || []).slice(0, 5).map(function (rule, index) {
        return { id: "client-relation:" + item.symbol + ":" + index, type: rule.label || "RELATION_RULE", sourceLabel: stockDisplayName(item.symbol, item), targetLabel: rule.label || "관계 규칙", evidenceRole: rule.evidenceRole || "context", reviewLevel: rule.reviewLevel || "observe" };
      }),
      beliefs: [],
      dataGaps: item.hasData ? [] : ["시장 신호 데이터 부족"],
      graphContext: {
        stockEntityId: "stock:" + item.symbol,
        tboxClasses: ["Stock", "Evidence", "Belief", "Opinion"],
        aboxEntityIds: ["stock:" + item.symbol],
        relationIds: [],
        evidenceIds: [],
        beliefIds: [],
        opinionId: "opinion:" + item.symbol
      },
      aiInference: {
        role: "ontology-first-investment-opinion",
        stateContract: "categorical-decision-state-v1",
        question: "전략 근거와 관계 근거를 함께 읽고 다음 검증 순서를 설명합니다."
      }
    };
  });
}

function investmentAiInferencePacket(snapshot) {
  var parts = ontologyStrategyParts(snapshot);
  return parts.investmentAnalysis.aiInferencePacket || parts.strategy.aiInferencePacket || {};
}

function investmentAnalysisModel(snapshot) {
  var payload = (snapshot || {}).investmentAnalysis || {};
  if (payload && payload.contract) return payload;
  var toss = (snapshot || {}).toss || {};
  var positions = Array.isArray(toss.positions) ? toss.positions.filter(function (item) {
    return item && item.source !== "cash" && String(item.symbol || "").toUpperCase() !== "CASH";
  }) : [];
  var watchlist = Array.isArray(toss.watchlist) ? toss.watchlist : [];
  var decision = (snapshot || {}).tossDecision || {};
  var items = Array.isArray(decision.items) ? decision.items : [];
  var account = toss.account && typeof toss.account === "object" ? toss.account : {};
  var accountId = String(account.accountId || account.id || toss.accountId || "default");
  var accountLabel = String(account.accountLabel || account.name || toss.accountLabel || "기본 계정");
  var sourceBySymbol = {};
  positions.concat(watchlist).forEach(function (item) {
    var symbol = String(item.symbol || "").toUpperCase();
    if (symbol && !sourceBySymbol[symbol]) sourceBySymbol[symbol] = item;
  });
  var actionQueue = items.map(function (item) {
    var symbol = String(item.symbol || "").toUpperCase();
    var source = sourceBySymbol[symbol] || {};
    var quality = String(source.dataQuality || item.dataQuality || (source.currentPrice ? "actual" : "missing"));
    var mode = String(toss.mode || (snapshot || {}).dataMode || "").toLowerCase();
    return Object.assign({}, item, {
      accountId: String(item.accountId || source.accountId || accountId),
      accountLabel: String(item.accountLabel || source.accountLabel || accountLabel),
      portfolioRole: positions.some(function (position) { return String(position.symbol || "").toUpperCase() === symbol; }) ? "holding" : "watchlist",
      dataQuality: quality,
      apiSource: String(source.quoteSource || source.sourceApi || source.source || item.apiSource || "snapshot"),
      isMock: Boolean(source.isMock || item.isMock) || ["mock", "demo"].indexOf(quality.toLowerCase()) >= 0 || ["mock", "demo", "preview"].indexOf(mode) >= 0,
      updatedAt: item.updatedAt || source.updatedAt || (snapshot || {}).generatedAt || ""
    });
  });
  return {
    contract: "investment-analysis-client-fallback-v1",
    generatedAt: (snapshot || {}).generatedAt || "",
    mode: toss.mode || "",
    status: toss.status || "",
    board: {
      title: "오늘의 투자 판단판",
      state: items.length ? "blocked" : "ready",
      tone: items.length ? "caution" : "watch",
      summary: "서버 분석 모델이 없어서 현재 스냅샷으로 기본 판단판을 구성했습니다.",
      metrics: [
        { label: "보유", value: positions.length, caption: "holding" },
        { label: "관심", value: watchlist.length, caption: "watchlist" },
        { label: "액션 후보", value: items.length, caption: "queue" },
        { label: "추론 보류", value: 0, caption: "blocked" }
      ],
      checklist: Array.isArray((snapshot || {}).checklist) ? (snapshot || {}).checklist : []
    },
    accountFocus: {
      accountId: accountId,
      label: accountLabel,
      holdingCount: positions.length,
      watchCount: watchlist.length,
      symbols: positions.concat(watchlist).map(function (item) { return String(item.symbol || "").toUpperCase(); }).filter(Boolean)
    },
    actionQueue: actionQueue,
    dataLineage: {
      actualCount: positions.concat(watchlist).length,
      mockCount: 0,
      items: positions.concat(watchlist).map(function (item) {
        return {
          symbol: item.symbol,
          name: item.name,
          source: item.quoteSource || item.source || "snapshot",
          quality: item.dataQuality || "actual",
          updatedAt: item.updatedAt || "",
          status: item.quoteStatus || ""
        };
      })
    },
    moneyFlow: { buckets: [], emergingFlows: [] },
    graphGate: { status: "blocked", tone: "caution", blockedCount: 0, relationCount: 0, entityCount: 0, requiredSource: "graphStoreInferenceBox", nextChecks: [] }
  };
}

function capitalFlowModel(snapshot) {
  var flow = (snapshot || {}).capitalFlow || {};
  if (flow && String(flow.contract || "").indexOf("capital-flow-") === 0) return flow;
  return {
    contract: "capital-flow-summary-v1",
    status: "empty",
    windowDays: 5,
    markets: [],
    sectors: [],
    subjects: [],
    transitions: [],
    portfolioImpact: {},
    quality: {},
    storageQuality: {}
  };
}

function capitalFlowDirectionMeta(direction) {
  var value = String(direction || "unavailable").toLowerCase();
  if (value === "inflow") return { label: "순유입", tone: "watch", sign: "+" };
  if (value === "outflow") return { label: "순유출", tone: "danger", sign: "" };
  if (value === "neutral") return { label: "중립", tone: "hold", sign: "" };
  return { label: "미수집", tone: "caution", sign: "" };
}

function capitalFlowValueText(item) {
  item = item || {};
  if (hasNumericValue(item.smartMoneyNetAmount)) return formatMoney(item.smartMoneyNetAmount) + "원";
  if (hasNumericValue(item.smartMoneyNetVolume)) return formatMoney(item.smartMoneyNetVolume) + "주";
  return "미수집";
}

function capitalFlowPartyText(item) {
  item = item || {};
  if (hasNumericValue(item.netAmount)) return formatMoney(item.netAmount) + "원";
  if (hasNumericValue(item.netVolume)) return formatMoney(item.netVolume) + "주";
  return "미수집";
}

function investmentChartPeriodOptions() {
  return [
    { id: "1d", label: "일", description: "장중·일간" },
    { id: "1w", label: "주", description: "단기 추세" },
    { id: "1m", label: "월", description: "중기 흐름" },
    { id: "custom", label: "사용자", description: "API 범위" }
  ];
}

function normalizeInvestmentChartPeriod(value) {
  var requested = String(value || "").toLowerCase();
  return investmentChartPeriodOptions().some(function (item) { return item.id === requested; }) ? requested : "1d";
}

function activeInvestmentChartPeriod() {
  decisionsState.activeInvestmentChartPeriod = normalizeInvestmentChartPeriod(decisionsState.activeInvestmentChartPeriod);
  return decisionsState.activeInvestmentChartPeriod;
}

function investmentChartState(direction, fallback) {
  var value = String(direction || fallback || "context").toLowerCase();
  if (["risk", "negative", "sell", "danger", "risk-only", "worsening"].indexOf(value) >= 0) {
    return { key: "risk", label: "위험 근거", tone: "danger" };
  }
  if (["support", "positive", "buy", "watch", "support-only", "improving"].indexOf(value) >= 0) {
    return { key: "support", label: "버티거나 좋아질 근거", tone: "watch" };
  }
  if (["mixed", "caution"].indexOf(value) >= 0) {
    return { key: "mixed", label: "엇갈린 근거", tone: "caution" };
  }
  return { key: "context", label: "참고 근거", tone: "hold" };
}

function investmentChartSourceText(source, quality) {
  var sourceText = String(source || "snapshot");
  var qualityText = String(quality || "").toLowerCase();
  if (qualityText === "mock") return "mock · " + sourceText;
  if (qualityText === "stale") return "실제 데이터 지연 · " + sourceText;
  if (qualityText === "missing" || qualityText === "gap") return "데이터 부족 · " + sourceText;
  return "실제 데이터 · " + sourceText;
}

function investmentIntegratedChartRows(snapshot, parts) {
  var rows = [];
  var analysis = investmentAnalysisModel(snapshot);
  buildTradeSignalItems(snapshot).slice(0, 6).forEach(function (item) {
    var stateMeta = investmentChartState(item.conflictState, item.changeState);
    rows.push({
      group: item.source === "watchlist" ? "관심" : "보유",
      label: stockDisplayName(item.symbol, item),
      detail: stockDisplayMeta(item, [marketLabel(item.market || "-"), item.sector || "-", item.action || "관망"]),
      state: stateMeta.key,
      stateLabel: decisionStateMeta("review", item.reviewLevel, "observe").label + " · " + stateMeta.label,
      tone: stateMeta.tone,
      value: item.currentPrice ? formatPrice(item.currentPrice, item.currency) : "시세 대기",
      source: item.quoteSource || item.source || "portfolio",
      quality: item.hasData ? "actual" : "missing"
    });
  });
  var flow = capitalFlowModel(snapshot);
  (Array.isArray(flow.subjects) ? flow.subjects : []).slice(0, 8).forEach(function (item) {
    var direction = capitalFlowDirectionMeta(item.direction);
    var stateMeta = investmentChartState(item.direction === "inflow" ? "support" : (item.direction === "outflow" ? "risk" : "context"));
    rows.push({
      group: "자금",
      label: stockDisplayName(item.subjectId, item),
      detail: [item.sector || "기타", "외국인 " + capitalFlowPartyText(item.foreign), "기관 " + capitalFlowPartyText(item.institution)].join(" · "),
      state: stateMeta.key,
      stateLabel: direction.label + " · " + (item.windowDays || flow.windowDays || 5) + "일",
      tone: stateMeta.tone,
      value: capitalFlowValueText(item),
      source: flow.source || "capital-flow-observations",
      quality: item.dataState === "sufficient" ? "actual" : "gap"
    });
  });
  var macro = ontologyMacroSignalData(parts || {});
  macro.fxSignals.concat(macro.rateSignals).slice(0, 5).forEach(function (entity) {
    var relationCount = ontologyMacroRelationCount(entity, macro.macroRelations);
    rows.push({
      group: String(entity.kind || "").indexOf("fx") >= 0 ? "환율" : "금리",
      label: ontologyEntityDisplayLabel(entity, entity && entity.id),
      detail: ontologyMacroMetaText(entity),
      state: "context",
      stateLabel: relationCount ? "연결 관계 있음" : "참고 근거",
      tone: "hold",
      value: ontologyMacroValueText(entity),
      source: ((entity.properties || {}).provider) || "ontology ABox",
      quality: (entity.properties || {}).mock ? "mock" : "actual"
    });
  });
  return rows;
}

function renderInvestmentChartPeriodControl(active) {
  return [
    '<div class="investment-chart-periods" role="tablist" aria-label="통합 차트 기간">',
    investmentChartPeriodOptions().map(function (item) {
      var selected = item.id === active;
      return [
        '<button type="button" role="tab" class="' + (selected ? "active" : "") + '" data-investment-chart-period="' + escapeHtml(item.id) + '"' + (selected ? ' aria-selected="true"' : ' aria-selected="false"') + '>',
        '<strong>' + escapeHtml(item.label) + '</strong>',
        '<span>' + escapeHtml(item.description) + '</span>',
        '</button>'
      ].join("");
    }).join(""),
    '</div>'
  ].join("");
}

function renderInvestmentIntegratedChartRow(row) {
  var stateMeta = investmentChartState(row.state, "context");
  var direction = stateMeta.key === "support" ? "positive" : (stateMeta.key === "risk" ? "negative" : "mixed");
  return [
    '<div class="investment-chart-row ' + escapeHtml(direction) + '"' + cardTypeAttrs("signal-card", row.tone || stateMeta.tone) + '>',
    '<div class="investment-chart-label">',
    '<span>' + escapeHtml(row.group || "-") + '</span>',
    '<strong>' + escapeHtml(row.label || "-") + '</strong>',
    '<em>' + escapeHtml(row.detail || "") + '</em>',
    '</div>',
    '<div class="investment-chart-track state" aria-label="' + escapeHtml((row.label || "") + " 상태 " + (row.stateLabel || stateMeta.label)) + '">',
    '<span class="tone-chip ' + escapeHtml(row.tone || stateMeta.tone) + '">' + escapeHtml(row.stateLabel || stateMeta.label) + '</span>',
    '</div>',
    '<div class="investment-chart-value">',
    '<strong>' + escapeHtml(row.value == null ? "-" : row.value) + '</strong>',
    '<span>' + escapeHtml(row.stateLabel || stateMeta.label) + '</span>',
    '<em>' + escapeHtml(investmentChartSourceText(row.source, row.quality)) + '</em>',
    '</div>',
    '</div>'
  ].join("");
}

function renderInvestmentIntegratedChartPanel(snapshot, parts) {
  var rows = investmentIntegratedChartRows(snapshot, parts);
  var active = activeInvestmentChartPeriod();
  var actualCount = rows.filter(function (row) { return String(row.quality || "").toLowerCase() !== "mock" && String(row.quality || "").toLowerCase() !== "missing"; }).length;
  var mockCount = rows.filter(function (row) { return String(row.quality || "").toLowerCase() === "mock"; }).length;
  return [
    '<article class="panel investment-chart-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Integrated Market Chart</p>',
    '<h2>통합 차트</h2>',
    '<p class="subtle">종목, 자금 흐름, 환율·금리 온톨로지 신호를 같은 레인에서 비교합니다. 실제 캔들 API가 붙으면 이 기간 상태를 그대로 사용합니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(rows.length) + '</span>',
    '</div>',
    renderInvestmentChartPeriodControl(active),
    '<div class="investment-chart-summary">',
    '<span>기간 <strong>' + escapeHtml(active.toUpperCase()) + '</strong></span>',
    '<span>실제 데이터 <strong>' + escapeHtml(actualCount) + '</strong></span>',
    '<span>mock <strong>' + escapeHtml(mockCount) + '</strong></span>',
    '</div>',
    '<div class="investment-chart-axis"><span>위험 근거</span><strong>엇갈림·참고</strong><span>버티는 근거</span></div>',
    '<div class="investment-chart-lanes">',
    rows.length ? rows.map(renderInvestmentIntegratedChartRow).join("") : renderEmptyState({
      tone: "muted",
      label: "Chart",
      title: "통합 차트에 표시할 데이터가 없습니다",
      description: "보유·관심 종목, 자금 흐름, 매크로 API가 수집되면 같은 레인에서 비교합니다.",
      meta: ["prices", "flow", "macro"]
    }),
    '</div>',
    '<div class="rule-strip">',
    '<span>각 행은 합산값 없이 위험·버팀·엇갈림·참고 상태를 표시합니다. 최종 판단은 TypeDB의 성립 조건과 실제 값을 함께 봅니다.</span>',
    '<span>각 행 끝에 실제/mock 여부와 API·스냅샷 출처를 표시합니다.</span>',
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentChartControlPanel(snapshot, parts) {
  var rows = investmentIntegratedChartRows(snapshot, parts);
  var active = activeInvestmentChartPeriod();
  var lineageStats = investmentLineageStats((investmentAnalysisModel(snapshot).dataLineage || {}));
  var apiSources = {};
  rows.forEach(function (row) {
    var source = row && row.source ? String(row.source) : "";
    if (source) apiSources[sourceLabel(source)] = true;
  });
  var sources = Object.keys(apiSources);
  return [
    '<article class="panel investment-chart-control-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Chart Control</p>',
    '<h2>차트 기준선</h2>',
    '<p class="subtle">기간, 실제/mock 구분, API 출처를 먼저 고정한 뒤 가격·수급·자금흐름·매크로를 같은 시간축으로 봅니다.</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(lineageStats.mock || lineageStats.missing ? "caution" : "watch") + '">' + escapeHtml(lineageStats.mock || lineageStats.missing ? "출처 확인" : "actual 우선") + '</span>',
    '</div>',
    '<div class="investment-chart-control-grid">',
    renderInvestmentTodayStatusCell("선택 기간", active.toUpperCase(), "일·주·월·사용자", "watch"),
    renderInvestmentTodayStatusCell("차트 레인", rows.length, "가격/흐름/매크로", rows.length ? "watch" : "hold"),
    renderInvestmentTodayStatusCell("실제 데이터", lineageStats.actual, "저장된 최신값", lineageStats.actual ? "watch" : "caution"),
    renderInvestmentTodayStatusCell("mock 데이터", lineageStats.mock, "명시 표시", lineageStats.mock ? "caution" : "hold"),
    renderInvestmentTodayStatusCell("API 출처", sources.length || "-", sources.slice(0, 3).join(", ") || "출처 대기", sources.length ? "watch" : "hold"),
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentDecisionBoardPanel(snapshot) {
  var analysis = investmentAnalysisModel(snapshot);
  var board = analysis.board || {};
  var metrics = Array.isArray(board.metrics) ? board.metrics : [];
  var checklist = Array.isArray(board.checklist) ? board.checklist : [];
  return [
    '<article class="panel investment-board-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Investment Board</p>',
    '<h2>' + escapeHtml(board.title || "오늘의 투자 판단판") + '</h2>',
    '<p class="subtle">' + escapeHtml(board.summary || "데이터, 체크리스트, 그래프 추론 상태를 먼저 확인합니다.") + '</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(board.tone || "hold") + '">' + escapeHtml(board.state === "ready" ? "판단 가능" : "판단 보류") + '</span>',
    '</div>',
    '<div class="investment-board-metrics">',
    metrics.map(function (item) {
      return [
        '<section' + cardTypeAttrs("metric-cell") + '>',
        '<span>' + escapeHtml(item.caption || "") + '</span>',
        '<strong>' + escapeHtml(item.value) + '</strong>',
        '<em>' + escapeHtml(item.label || "") + '</em>',
        '</section>'
      ].join("");
    }).join(""),
    '</div>',
    '<div class="investment-checklist-grid">',
    checklist.length ? checklist.map(function (item) {
      return [
        '<div class="investment-check-row"' + cardTypeAttrs("ledger-row", item.status === "정상" ? "watch" : "hold") + '>',
        '<strong>' + escapeHtml(item.label || item.title || "-") + '</strong>',
        '<span>' + escapeHtml(item.status || "대기") + '</span>',
        '</div>'
      ].join("");
    }).join("") : '<div class="ontology-empty">오늘 체크리스트가 아직 없습니다.</div>',
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentGraphGatePanel(snapshot) {
  var gate = investmentAnalysisModel(snapshot).graphGate || {};
  var checks = Array.isArray(gate.nextChecks) ? gate.nextChecks : [];
  return [
    '<article class="panel investment-gate-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Inference Gate</p>',
    '<h2>추론 가능 상태</h2>',
    '<p class="subtle">' + escapeHtml(gate.reason || "InferenceBox 관계와 데이터 신선도를 확인합니다.") + '</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(gate.tone || "hold") + '">' + escapeHtml(gate.status || "unknown") + '</span>',
    '</div>',
    '<div class="investment-gate-grid">',
    renderInvestmentGateMetric("요구 출처", gate.requiredSource || "graphStoreInferenceBox"),
    renderInvestmentGateMetric("관계", gate.relationCount || 0),
    renderInvestmentGateMetric("엔티티", gate.entityCount || 0),
    renderInvestmentGateMetric("보류", gate.blockedCount || 0),
    '</div>',
    '<div class="rule-strip">',
    checks.slice(0, 3).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") || '<span>추론 상태 확인 대기</span>',
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentGateMetric(label, value) {
  return '<section' + cardTypeAttrs("metric-cell") + '><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong></section>';
}

function renderInvestmentActionQueuePanel(snapshot) {
  var rows = Array.isArray(investmentAnalysisModel(snapshot).actionQueue) ? investmentAnalysisModel(snapshot).actionQueue : [];
  var filteredRows = investmentActionFilteredRows(rows);
  var pageInfo = investmentActionPageInfo(filteredRows);
  var activeRow = investmentActionByKey(decisionsState.expandedInvestmentActionKey);
  return [
    '<article class="panel investment-action-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">종목 판단</p>',
    '<h2>종목별 판단 후보</h2>',
    '<p class="subtle">보유·관심 종목의 현재 결론과 이유, 다음 확인 조건을 한 곳에서 검토합니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(rows.length) + '</span>',
    '</div>',
    renderInvestmentDecisionSummaryRail(rows, filteredRows),
    rows.length ? '<div class="investment-action-workbench ' + escapeHtml(activeRow ? "has-detail" : "summary-only") + '">' + renderInvestmentActionToolbar(rows, filteredRows, pageInfo) + '<div class="investment-action-list">' + (pageInfo.visibleRows.length ? pageInfo.visibleRows.map(renderInvestmentActionRow).join("") : renderEmptyState({
      tone: "muted",
      label: "Filtered",
      title: "조건에 맞는 투자 후보가 없습니다",
      description: "종목명, 코드, 판단, 근거 문장으로 후보를 다시 좁혀보세요.",
      meta: ["검색", "페이지", "상세 팝업"]
    })) + renderInvestmentActionPager(pageInfo) + '</div>' + (activeRow ? renderInvestmentActionDetailPanel(rows) : '') + '</div>' : '<div class="investment-action-list"><div class="ontology-empty">액션 큐가 비어 있습니다.</div></div>',
    '</article>'
  ].join("");
}

function investmentActionDecisionType(row) {
  row = row || {};
  var text = [
    row.decision,
    row.tone,
    Array.isArray(row.reasons) ? row.reasons.join(" ") : "",
    Array.isArray(row.triggers) ? row.triggers.join(" ") : ""
  ].join(" ").toLowerCase();
  if (text.indexOf("매수") >= 0 || text.indexOf("buy") >= 0 || text.indexOf("add") >= 0) return "buy";
  if (text.indexOf("매도") >= 0 || text.indexOf("축소") >= 0 || text.indexOf("sell") >= 0 || text.indexOf("trim") >= 0) return "risk";
  if (text.indexOf("보유") >= 0 || text.indexOf("hold") >= 0) return "hold";
  return "watch";
}

function investmentActionValidation(row) {
  row = row || {};
  var graph = row.graph || {};
  var quality = String(row.dataQuality || "").toLowerCase();
  if (graph.blocked) return { label: "추론 보류", tone: "caution", detail: graph.basis || "InferenceBox 확인" };
  if (["missing", "mock", "demo", "stale"].indexOf(quality) >= 0) return { label: "데이터 확인", tone: "caution", detail: row.dataQuality || "품질 확인" };
  if ((Array.isArray(row.reasons) ? row.reasons.length : 0) >= 2 && (Array.isArray(graph.nextChecks) ? graph.nextChecks.length : 0)) {
    return { label: "검토 가능", tone: "watch", detail: "근거와 다음 확인 있음" };
  }
  return { label: "관찰", tone: "hold", detail: "추가 근거 대기" };
}

function investmentActionInvalidation(row) {
  row = row || {};
  var graph = row.graph || {};
  var checks = Array.isArray(graph.nextChecks) ? graph.nextChecks : [];
  var blockedActions = Array.isArray(graph.blockedActions) ? graph.blockedActions : [];
  var direct = row.invalidationCondition || row.invalidation || graph.invalidationCondition || graph.weakenCondition;
  if (direct) return String(direct);
  for (var i = 0; i < checks.length; i += 1) {
    var check = String(checks[i] || "");
    if (check.indexOf("무효") >= 0 || check.indexOf("약해") >= 0 || check.indexOf("회복") >= 0 || check.indexOf("해소") >= 0) return check;
  }
  if (blockedActions.length) return String(blockedActions[0]);
  var type = investmentActionDecisionType(row);
  if (graph.blocked) return "TypeDB InferenceBox 관계가 복구되기 전까지 판단을 보류합니다.";
  if (type === "risk") return "리스크 관계가 해소되거나 가격 회복 근거가 생기면 축소 판단을 낮춥니다.";
  if (type === "buy") return "진입 근거가 사라지거나 데이터 신선도가 깨지면 매수 후보에서 제외합니다.";
  return "핵심 근거가 사라지거나 새 반대 신호가 생기면 판단을 다시 봅니다.";
}

function investmentActionNextWindow(row) {
  row = row || {};
  var graph = row.graph || {};
  var checks = Array.isArray(graph.nextChecks) ? graph.nextChecks : [];
  if (row.nextReviewAt) return formatClock(row.nextReviewAt);
  if (checks.length) return "다음 데이터 갱신: " + String(checks[0]);
  var market = String(row.market || "").toUpperCase();
  if (market === "US" || market === "USA" || market === "NASDAQ" || market === "NYSE") return "미국장 시작 전";
  if (market === "KR" || market === "KOSPI" || market === "KOSDAQ") return "국내장 시작 전";
  return "장 시작 전 체크";
}

function investmentActionLinkedAlert(row) {
  row = row || {};
  if (row.linkedAlert || row.alertType) return String(row.linkedAlert || row.alertType);
  if ((row.graph || {}).blocked) return "관계 추론 상태 알림";
  var type = investmentActionDecisionType(row);
  if (type === "buy") return "매수 후보 알림 대기";
  if (type === "risk") return "매도·축소 알림 대기";
  if (type === "hold") return "보유 조건 점검 알림";
  return "관찰 알림 대기";
}

function investmentActionPlaybook(row) {
  row = row || {};
  var graph = row.graph || {};
  var text = [
    row.market,
    row.sector,
    row.source,
    row.decision,
    Array.isArray(row.reasons) ? row.reasons.join(" ") : "",
    Array.isArray(row.triggers) ? row.triggers.join(" ") : ""
  ].join(" ").toLowerCase();
  if (graph.blocked) return { id: "gate", label: "추론 게이트 복구", tone: "caution", detail: "관계 추론 정상화 후 판단" };
  if (investmentActionDecisionType(row) === "risk") return { id: "risk", label: "리스크 축소", tone: "danger", detail: "하방 압력과 반대 신호 점검" };
  if (text.indexOf("뉴스") >= 0 || text.indexOf("공시") >= 0 || text.indexOf("소송") >= 0 || text.indexOf("risk") >= 0) return { id: "news", label: "뉴스 리스크 회피", tone: "caution", detail: "새 근거와 가격 반응 연결" };
  if (text.indexOf("usd") >= 0 || text.indexOf("환율") >= 0 || text.indexOf("금리") >= 0 || text.indexOf("미국") >= 0) return { id: "macro", label: "환율·금리 민감", tone: "hold", detail: "거시 관계와 가격 동조 확인" };
  if (investmentActionDecisionType(row) === "buy") return { id: "entry", label: "진입 후보 검증", tone: "watch", detail: "진입 근거와 무효화 조건 확인" };
  return { id: "defense", label: "보유 방어", tone: "hold", detail: "유지 조건과 반대 신호 확인" };
}

function formatInvestmentActionPercent(value) {
  if (!hasNumericValue(value)) return "수집되지 않음";
  var number = Number(String(value).replace(/,/g, "").trim());
  return (number > 0 ? "+" : "") + number.toLocaleString("ko-KR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }) + "%";
}

function formatInvestmentActionNarrative(value) {
  var text = String(value == null ? "" : value).trim();
  text = text.replace(/토스 잔고 기준 수익률이\s*([+-]?\d+(?:\.\d+)?)%입니다\.?/g, function (_match, raw) {
    return "현재 보유 수익률은 " + formatInvestmentActionPercent(raw) + "입니다.";
  });
  text = text.replace(/평가손익은\s*([+-]?[\d,]+(?:\.\d+)?)\s*KRW입니다\.?/g, function (_match, raw) {
    var number = Number(String(raw).replace(/,/g, ""));
    return Number.isFinite(number)
      ? "현재 평가손익은 " + number.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + "원입니다."
      : _match;
  });
  text = text.replace(/매도 가능 수량은\s*([\d,]+(?:\.\d+)?)입니다\.?/g, function (_match, raw) {
    var number = Number(String(raw).replace(/,/g, ""));
    return Number.isFinite(number)
      ? "현재 매도 가능 수량은 " + number.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) + "주입니다."
      : _match;
  });
  text = text.replace(/([+-]?\d+\.\d{3,})\s*%/g, function (_match, raw) {
    return formatInvestmentActionPercent(raw);
  });
  [
    ["Flow Lens에서는 TypeDB InferenceBox 결과가 없으면 매수·매도 판단을 만들지 않습니다.", "종목 관계 분석이 아직 준비되지 않아 매수·매도 판단을 보류합니다."],
    ["TypeDB native rule 저장 상태 확인", "분석 규칙이 정상적으로 준비됐는지 확인"],
    ["InferenceBox 관계 생성 여부 확인", "종목 관계 분석 결과가 생성됐는지 확인"],
    ["온톨로지 자료 상태 확인", "관계 분석에 필요한 자료가 준비됐는지 확인"],
    ["TypeDB InferenceBox 관계가 복구되기 전까지 판단을 보류합니다.", "종목 관계 분석 결과가 준비될 때까지 판단을 보류합니다."],
    ["InferenceBox 없는 매수 판단", "관계 분석 없이 매수 판단하지 않기"],
    ["InferenceBox 없는 매도 판단", "관계 분석 없이 매도 판단하지 않기"],
    ["Python 관계 규칙 fallback", "임시 규칙으로 판단을 대신하지 않기"]
  ].forEach(function (pair) {
    text = text.split(pair[0]).join(pair[1]);
  });
  return text;
}

function investmentActionQualityPresentation(row) {
  row = row || {};
  var raw = String(row.dataQuality || row.quality || "").trim();
  var key = raw.toLowerCase();
  var isMock = Boolean(row.isMock) || ["mock", "demo"].indexOf(key) >= 0;
  if (isMock) return { label: "MOCK 데이터", detail: "검증용 데이터", tone: "hold", raw: raw || "mock" };
  if (["actual", "live", "fresh", "ok"].indexOf(key) >= 0) return { label: "실제 데이터", detail: "API에서 받은 값", tone: "watch", raw: raw || "actual" };
  if (key === "reference") return { label: "참고 데이터", detail: "비교용 기준 정보", tone: "hold", raw: raw };
  if (["cache", "cached"].indexOf(key) >= 0) return { label: "저장 데이터", detail: "마지막 정상 응답", tone: "caution", raw: raw };
  if (key === "stale") return { label: "지연 데이터", detail: "최신 갱신 필요", tone: "caution", raw: raw };
  if (["missing", "gap", "error", "failed"].indexOf(key) >= 0) return { label: "데이터 부족", detail: "수집 상태 확인 필요", tone: "danger", raw: raw };
  return { label: raw || "상태 확인 필요", detail: "데이터 구분 미확인", tone: "hold", raw: raw || "unknown" };
}

function investmentActionUserPresentation(row) {
  row = row || {};
  var graph = row.graph || {};
  var reasons = (Array.isArray(row.reasons) ? row.reasons : []).map(formatInvestmentActionNarrative).filter(Boolean);
  var checks = (Array.isArray(graph.nextChecks) ? graph.nextChecks : []).map(formatInvestmentActionNarrative).filter(Boolean);
  var action = decisionActionMeta(row.actionCode, row.decision || row.action);
  var quality = investmentActionQualityPresentation(row);
  var validation = investmentActionValidation(row);
  var blocked = Boolean(graph.blocked) || action.code === "BLOCKED";
  var copy = {
    BUY: {
      headline: "매수를 검토할 수 있습니다",
      explanation: "진입 후보로 분류됐습니다. 주문 전 가격과 판단이 약해지는 조건을 함께 확인합니다.",
      nextAction: "진입 가격과 약화 조건 확인"
    },
    ADD: {
      headline: "추가 매수를 검토할 수 있습니다",
      explanation: "보유 근거가 유지되는지 확인한 뒤 비중 확대 여부를 검토합니다.",
      nextAction: "보유 근거와 추가 진입 가격 확인"
    },
    HOLD: {
      headline: "현재 보유를 유지하며 관찰합니다",
      explanation: "당장 비중을 바꾸기보다 유지 조건과 반대 신호를 계속 확인합니다.",
      nextAction: "유지 조건과 반대 신호 확인"
    },
    TRIM: {
      headline: "보유 비중 축소를 검토합니다",
      explanation: "위험 신호가 확인됐습니다. 주문 전 회복 가능성과 매도 가능 수량을 함께 봅니다.",
      nextAction: "회복 조건과 매도 가능 수량 확인"
    },
    SELL: {
      headline: "매도를 검토합니다",
      explanation: "보유 근거가 약해졌습니다. 주문 전 반대 근거와 실행 가능 수량을 다시 확인합니다.",
      nextAction: "반대 근거와 실행 가능 수량 확인"
    },
    AVOID: {
      headline: "지금은 신규 진입을 피합니다",
      explanation: "현재 위험이 진입 근거보다 큽니다. 새 근거가 생길 때까지 관찰합니다.",
      nextAction: "새 근거가 생길 때까지 관찰"
    },
    OBSERVE: {
      headline: "지금은 관찰이 우선입니다",
      explanation: "매수나 매도 방향을 정할 근거가 아직 충분하지 않습니다.",
      nextAction: "다음 시세와 근거 갱신 후 재확인"
    }
  }[action.code] || {
    headline: "추가 확인이 필요합니다",
    explanation: "현재 자료만으로 행동 방향을 확정하지 않습니다.",
    nextAction: "다음 데이터 갱신 후 재확인"
  };

  if (blocked) {
    copy = {
      headline: "지금은 매수·매도 판단을 보류합니다",
      explanation: "종목 관계 분석이 완료되지 않아 현재 가격과 손익만으로 매수·매도 방향을 정하지 않습니다.",
      nextAction: "관계 분석 완료 여부를 확인한 뒤 다시 판단"
    };
  }

  var changedAt = recordChangedAt(row, recordChangedAt(graph));
  var profitLoss = formatInvestmentActionPercent(row.profitLossRate);
  var profitNumber = hasNumericValue(row.profitLossRate)
    ? Number(String(row.profitLossRate).replace(/,/g, "").trim())
    : null;
  var dataIssue = ["caution", "danger"].indexOf(quality.tone) >= 0;
  return {
    actionCode: blocked ? "BLOCKED" : action.code,
    actionLabel: blocked ? "판단 보류" : action.label,
    statusLabel: blocked ? "관계 분석 대기" : (dataIssue ? "자료 확인 필요" : validation.label),
    headline: copy.headline,
    explanation: copy.explanation,
    nextAction: copy.nextAction,
    tone: blocked ? "caution" : (action.tone || row.tone || "hold"),
    blocked: blocked,
    evidenceTitle: blocked ? "현재 확인한 계정 정보" : "왜 이런 판단인가요?",
    evidenceDescription: blocked
      ? "분석은 대기 중이지만 아래 계정·시세 정보는 별도로 확인됐습니다."
      : "현재 판단에 사용한 핵심 근거입니다.",
    reasons: reasons,
    checks: checks,
    invalidation: blocked
      ? "종목 관계 분석 결과와 필요한 자료가 준비되면 보류 상태를 해제하고 다시 판단합니다."
      : formatInvestmentActionNarrative(investmentActionInvalidation(row)),
    nextWindow: row.nextReviewAt
      ? formatClock(row.nextReviewAt)
      : (blocked ? "관계 분석이 완료되는 즉시" : formatInvestmentActionNarrative(investmentActionNextWindow(row))),
    linkedAlert: blocked
      ? "관계 분석 상태가 바뀌면 알림 정책에 따라 새 알림 후보를 만듭니다."
      : formatInvestmentActionNarrative(investmentActionLinkedAlert(row)),
    profitLoss: profitLoss,
    profitTone: profitNumber == null ? "muted" : (profitNumber < 0 ? "danger" : (profitNumber > 0 ? "watch" : "hold")),
    quality: quality,
    changedAt: changedAt,
    changedAtText: changedAt ? formatClock(changedAt) : "변경 시각 미확인",
    validation: validation,
    playbook: investmentActionPlaybook(row)
  };
}

function renderInvestmentActionDecisionList(items, emptyText) {
  var rows = Array.isArray(items) ? items : [];
  if (!rows.length) return '<p class="investment-decision-empty">' + escapeHtml(emptyText || "확인된 항목이 없습니다.") + '</p>';
  return '<ol class="investment-decision-list">' + rows.map(function (item) {
    return '<li>' + escapeHtml(item) + '</li>';
  }).join("") + '</ol>';
}

function renderInvestmentActionDecisionDetail(row, relatedNotification, inline) {
  row = row || {};
  var display = investmentActionUserPresentation(row);
  var graph = row.graph || {};
  var apiSource = String(row.apiSource || "출처 미기록");
  var relatedAlertText = relatedNotification
    ? "이 판단에서 만들어진 최신 알림이 있습니다. 발송 여부와 이유를 이어서 확인할 수 있습니다."
    : display.linkedAlert;
  var wrapperClass = "investment-decision-detail" + (inline ? " investment-action-detail inline-detail-surface investment-decision-inline" : "");
  var technicalDisclosureKey = "investment-decision-technical:" + investmentActionKey(row, 0);
  return [
    '<div class="' + escapeHtml(wrapperClass) + '" data-investment-decision-detail>',
    '<section class="investment-decision-conclusion ' + escapeHtml(display.tone) + '" aria-label="현재 결론">',
    '<div class="investment-decision-conclusion-head"><span>현재 결론</span><span class="tone-chip ' + escapeHtml(display.tone) + '">' + escapeHtml(display.statusLabel) + '</span></div>',
    '<h3>' + escapeHtml(display.headline) + '</h3>',
    '<p>' + escapeHtml(display.explanation) + '</p>',
    '<div class="investment-decision-next-action"><span>다음 행동</span><strong>' + escapeHtml(display.nextAction) + '</strong></div>',
    '</section>',
    '<p class="investment-decision-purpose"><strong>이 화면의 역할</strong><span>보유·관심 종목의 최근 수집 자료와 분석 결과를 모아 지금 검토할 행동과 그 이유를 설명합니다. 자동 주문은 실행하지 않습니다.</span></p>',
    '<section class="investment-decision-facts" aria-label="판단 핵심 정보">',
    '<div class="investment-decision-fact"><span>손익률</span><strong class="' + escapeHtml(display.profitTone) + '">' + escapeHtml(display.profitLoss) + '</strong><small>' + escapeHtml(sourceLabel(row.source) + " 종목 기준") + '</small></div>',
    '<div class="investment-decision-fact"><span>데이터 상태</span><strong class="' + escapeHtml(display.quality.tone) + '">' + escapeHtml(display.quality.label) + '</strong><small>' + escapeHtml(display.quality.detail + " · " + apiSource) + '</small></div>',
    '<div class="investment-decision-fact"><span>최종 변경</span><strong>' + escapeHtml(display.changedAtText) + '</strong><small>이 판단이 마지막으로 갱신된 시각</small></div>',
    '</section>',
    '<section class="investment-decision-section">',
    '<header><h3>' + escapeHtml(display.evidenceTitle) + '</h3><p>' + escapeHtml(display.evidenceDescription) + '</p></header>',
    renderInvestmentActionDecisionList(display.reasons, "연결된 계정·시세 근거가 아직 없습니다."),
    '</section>',
    '<section class="investment-decision-section investment-decision-review">',
    '<header><h3>' + escapeHtml(display.blocked ? "판단을 다시 진행하려면" : "지금 확인할 것") + '</h3><p>확인이 끝나기 전에는 현재 결론을 확정 행동으로 보지 않습니다.</p></header>',
    '<div class="investment-decision-review-time"><span>다시 볼 시점</span><strong>' + escapeHtml(display.nextWindow) + '</strong></div>',
    renderInvestmentActionDecisionList(display.checks, "다음 데이터 갱신에서 새 근거를 확인합니다."),
    '</section>',
    '<section class="investment-decision-section investment-decision-change">',
    '<header><h3>' + escapeHtml(display.blocked ? "언제 보류가 풀리나요?" : "언제 판단이 바뀌나요?") + '</h3></header>',
    '<p>' + escapeHtml(display.invalidation) + '</p>',
    '</section>',
    '<section class="investment-decision-section investment-decision-alert">',
    '<header><h3>알림 연결</h3></header>',
    '<p>' + escapeHtml(relatedAlertText) + '</p>',
    relatedNotification ? renderWorkDetailButton("notification-job", notificationJobKey(relatedNotification), "관련 알림 보기", "text-button compact") : '',
    '</section>',
    '<details class="investment-decision-technical" data-disclosure-key="' + escapeHtml(technicalDisclosureKey) + '">',
    '<summary><span>데이터·분석 상세</span><small>사용 API와 원본 분석 상태</small></summary>',
    '<dl>',
    '<div><dt>원본 판단</dt><dd>' + escapeHtml(row.decision || row.action || "판단 대기") + '</dd></div>',
    '<div><dt>검증 상태</dt><dd>' + escapeHtml(display.validation.label + " · " + display.validation.detail) + '</dd></div>',
    '<div><dt>분석 경로</dt><dd>' + escapeHtml(display.playbook.label + " · " + display.playbook.detail) + '</dd></div>',
    '<div><dt>데이터 구분</dt><dd>' + escapeHtml(display.quality.label + " (" + display.quality.raw + ")") + '</dd></div>',
    '<div><dt>사용 API</dt><dd>' + escapeHtml(apiSource) + '</dd></div>',
    graph.blocked ? '<div><dt>차단 코드</dt><dd>' + escapeHtml(graph.basis || "inference-required") + '</dd></div>' : '',
    '</dl>',
    '</details>',
    '</div>'
  ].join("");
}

function investmentDecisionStats(rows, filteredRows) {
  rows = Array.isArray(rows) ? rows : [];
  filteredRows = Array.isArray(filteredRows) ? filteredRows : rows;
  var stats = { total: rows.length, visible: filteredRows.length, buy: 0, risk: 0, hold: 0, watch: 0, blocked: 0, alerts: 0 };
  rows.forEach(function (row) {
    var type = investmentActionDecisionType(row);
    if (stats[type] == null) stats.watch += 1;
    else stats[type] += 1;
    if ((row.graph || {}).blocked) stats.blocked += 1;
    if (investmentActionLinkedAlert(row)) stats.alerts += 1;
  });
  return stats;
}

function investmentChecklistStats(checklist) {
  checklist = Array.isArray(checklist) ? checklist : [];
  var done = checklist.filter(function (item) {
    var status = String(item && (item.status || item.state || item.result) || "").toLowerCase();
    return status === "정상" || status === "완료" || status === "ok" || status === "done" || status === "ready";
  }).length;
  return { total: checklist.length, done: done, pending: Math.max(0, checklist.length - done) };
}

function investmentLineageStats(lineage) {
  lineage = lineage || {};
  var rows = Array.isArray(lineage.items) ? lineage.items : [];
  var stats = {
    total: rows.length,
    actual: Number(lineage.actualCount || 0),
    mock: Number(lineage.mockCount || 0),
    stale: 0,
    missing: 0
  };
  rows.forEach(function (row) {
    var quality = String(row && row.quality || "").toLowerCase();
    if (quality === "mock" || quality === "demo") stats.mock += lineage.mockCount ? 0 : 1;
    if (quality === "stale" || quality === "expired") stats.stale += 1;
    if (quality === "missing" || quality === "gap" || quality === "error") stats.missing += 1;
  });
  return stats;
}

function renderStrategySectionContent(snapshot) {
  var section = activeSectionForPageMode("modeling", strategySections, normalizeStrategySection(decisionsState.activeStrategySection));
  var parts = ontologyStrategyParts(snapshot);
  if (section === "hypotheses") {
    return renderInvestmentTabWorkspace("hypotheses", [
      { role: "full", html: renderHypothesisWorkspacePanel() }
    ]);
  }
  if (activePageMode("modeling") !== "settings") {
    return renderStrategyUnifiedConsole(snapshot, parts);
  }
  if (section === "evidence") {
    return renderInvestmentTabWorkspace("evidence", [
      { role: "main", html: renderInvestmentEvidenceWorkbenchPanel(snapshot) },
      { role: "side", html: renderOntologyExecutionPlanPanel(investmentReasoningCards(snapshot), parts) + renderInvestmentDataLineagePanel(snapshot) + renderStrategyDataPanel(snapshot) }
    ]);
  }
  if (section === "charts") {
    return renderInvestmentTabWorkspace("charts", [
      { role: "summary", html: renderInvestmentChartControlPanel(snapshot, parts) },
      { role: "main", html: renderInvestmentIntegratedChartPanel(snapshot, parts) },
      { role: "side", html: renderInvestmentMoneyFlowPanel(snapshot) + renderOntologyMacroSignalPanel(parts) + renderInvestmentGraphGatePanel(snapshot) + renderInvestmentDataLineagePanel(snapshot) }
    ]);
  }
  if (section === "graphs") {
    return renderInvestmentTabWorkspace("graphs", [
      { role: "full", html: renderInvestmentOntologyWorkspacePanel(snapshot, parts) }
    ]);
  }
  if (section === "proposals") {
    return renderInvestmentTabWorkspace("proposals", [
      { role: "full", html: renderStrategyProposalConsolePanel() }
    ]);
  }
  if (section === "rules") {
    return renderInvestmentTabWorkspace("rules", [
      { role: "main", html: renderStrategyRulesOverviewPanel(snapshot, parts) },
      { role: "side", html: renderInvestmentAiPacketPanel(snapshot) }
    ]);
  }
  if (section === "trace") {
    return renderInvestmentTabWorkspace("trace", [
      { role: "summary", html: renderStrategyReviewStatusPanel(snapshot, parts) },
      { role: "full", html: renderStrategyTraceOverviewPanel(snapshot, parts) }
    ]);
  }
  return renderInvestmentTabWorkspace("overview", [
    { role: "summary", html: renderInvestmentTodayStatusPanel(snapshot, parts) },
    { role: "main", html: renderInvestmentTodayActionBoardPanel(snapshot) },
    { role: "side", html: renderInvestmentTodaySelectedPanel(snapshot) },
    { role: "blockers", html: renderInvestmentTodayBlockersPanel(snapshot) }
  ]);
}

function renderStrategyUnifiedConsole(snapshot, parts) {
  return renderInvestmentTabWorkspace("overview strategy-unified-console", [
    { role: "summary", html: renderInvestmentTodayStatusPanel(snapshot, parts) },
    { role: "main", html: renderInvestmentTodayActionBoardPanel(snapshot) },
    { role: "side", html: renderInvestmentTodaySelectedPanel(snapshot) + renderStrategyDataPanel(snapshot) },
    { role: "blockers", html: renderInvestmentTodayBlockersPanel(snapshot) }
  ]);
}

function renderStrategyOverviewActionCard(item) {
  item = item || {};
  return [
    '<section class="work-detail-card ' + escapeHtml(item.tone || "hold") + '"' + cardTypeAttrs("config-panel", item.tone || "hold") + '>',
    '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.value || "-") + '</span>',
    '<strong>' + escapeHtml(item.title || "-") + '</strong>',
    '<p>' + escapeHtml(item.description || "") + '</p>',
    renderWorkDetailButton(item.type, item.key || "", item.button || "상세 보기", "text-button compact"),
    '</section>'
  ].join("");
}

function renderStrategyRulesOverviewPanel(snapshot, parts) {
  var ruleboxRules = ontologyRuleboxRules();
  var prompts = promptTemplateRows();
  var thresholds = relationRuleThresholds();
  var modelStats = modelStatsForItems(buildTradeSignalItems(snapshot));
  var ruleboxCount = ontologyState.ontologyRuleboxLoaded
    ? (ruleboxRules.length || ((ontologyState.ontologyRulebox || {}).ruleCount || 0))
    : ((ontologyState.ontologyRulebox || {}).ruleCount || 0);
  var items = [
    {
      tone: ruleboxCount ? "watch" : "caution",
      value: ontologyState.ontologyRuleboxLoaded ? ruleboxCount + "개" : "상세",
      title: "TypeDB RuleBox",
      description: "TypeDB 네이티브 규칙 JSON, 후보 생성, 진단, 시드를 한 곳에서 처리합니다.",
      type: "strategy-rulebox-editor",
      button: "RuleBox 열기"
    },
    {
      tone: prompts.length ? "watch" : "hold",
      value: prompts.length + "개",
      title: "AI 프롬프트",
      description: "알림 후보를 투자 의견으로 요약하는 게이트와 템플릿을 조정합니다.",
      type: "strategy-prompt-editor",
      button: "프롬프트 편집"
    },
    {
      tone: modelStats.actionCount ? "watch" : "hold",
      value: modelStats.actionCount + "개",
      title: "모델 기준",
      description: "매수·매도 기준점, 가중치, 계산식처럼 자주 바꾸지 않는 값을 모읍니다.",
      type: "strategy-model-policy-editor",
      button: "모델 기준 편집"
    }
  ];
  return [
    '<article class="panel strategy-rules-overview-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Strategy Controls</p>',
    '<h2>규칙·프롬프트 운영 요약</h2>',
    '<p class="subtle">기본 화면은 현재 상태와 편집 진입점만 보여주고, 긴 입력 화면은 상세 레이어에서 엽니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(parts.relations.length) + '</span>',
    '</div>',
    '<div class="work-detail-metric-row">',
    renderNotificationDetailMetric("RuleBox", ontologyState.ontologyRuleboxLoaded ? ruleboxCount + "개" : "lazy", ruleboxCount ? "watch" : "caution"),
    renderNotificationDetailMetric("프롬프트", prompts.length + "개", prompts.length ? "watch" : "hold"),
    renderNotificationDetailMetric("임계값", Object.keys(thresholds || {}).length + "개", "muted"),
    '</div>',
    '<div class="work-detail-grid strategy-control-grid">',
    items.map(renderStrategyOverviewActionCard).join(""),
    '</div>',
    '<div class="rule-strip"><span>규칙은 결과 해석의 근거입니다. 기본 화면에서는 흐름을 읽고, 실제 입력은 필요한 경우에만 열어 수정합니다.</span><span>변경 후에는 검증·리뷰 탭에서 관계 행과 모델 리뷰가 의도대로 바뀌었는지 확인합니다.</span></div>',
    '</article>'
  ].join("");
}

function renderStrategyTraceOverviewPanel(snapshot, parts) {
  var insights = Array.isArray(parts.insights) && parts.insights.length
    ? parts.insights
    : (parts.aboxEntities || []).filter(function (item) { return String(item && item.kind || "") === "insight"; });
  var qualityNodes = Array.isArray(parts.dataQuality) && parts.dataQuality.length
    ? parts.dataQuality
    : (parts.aboxEntities || []).filter(function (item) {
      return ["data-quality", "data-freshness", "provenance", "source-reliability", "missing-data"].indexOf(String(item && item.kind || "")) >= 0;
    });
  var modelStats = modelStatsForItems(buildTradeSignalItems(snapshot));
  var ledgerSummary = inferenceLedgerSummary();
  var ledgerRows = inferenceLedgerRows();
  var cards = [
    {
      tone: ledgerRows.length ? "watch" : (ontologyState.ontologyInferenceLedgerLoading ? "caution" : "hold"),
      value: ontologyState.ontologyInferenceLedgerLoaded ? (ledgerSummary.ledgerCount || ledgerRows.length || 0) + "건" : "lazy",
      title: "추론 원장",
      description: "TypeDB trace별 조건 통과, 파생 관계, 알림 의도를 한 줄 감사 경로로 봅니다.",
      type: "strategy-trace-detail",
      key: "ledger",
      button: "원장 보기"
    },
    {
      tone: modelStats.actionCount ? "watch" : "hold",
      value: modelStats.actionCount + "개",
      title: "모델 리뷰",
      description: "종목별 행동, 확인 단계, 자료 상태, 근거 역할은 상세에서 확인합니다.",
      type: "strategy-trace-detail",
      key: "model",
      button: "모델 리뷰"
    },
    {
      tone: parts.relations.length ? "watch" : "hold",
      value: parts.relations.length + "행",
      title: "관계 투영",
      description: "근거·믿음·의견이 현재 ABox 행으로 연결되는 흐름입니다.",
      type: "strategy-trace-detail",
      key: "projection",
      button: "투영 보기"
    },
    {
      tone: insights.length ? "watch" : "hold",
      value: insights.length + "개",
      title: "인사이트·품질",
      description: "알림 후보와 데이터 품질·출처 신뢰도만 분리해서 검토합니다.",
      type: "strategy-trace-detail",
      key: "quality",
      button: "품질 보기"
    },
    {
      tone: parts.relationCounts.length ? "watch" : "hold",
      value: parts.relationCounts.length + "종",
      title: "관계 행",
      description: "거시 관계와 저장된 관계 타입별 행을 압축해 확인합니다.",
      type: "strategy-trace-detail",
      key: "relations",
      button: "관계 보기"
    },
    {
      tone: ontologyReadableRuleRows(parts).length ? "watch" : "hold",
      value: ontologyReadableRuleRows(parts).length + "개",
      title: "규칙 추적",
      description: "TBox 규칙이 어떤 입력과 출력으로 연결되는지 봅니다.",
      type: "strategy-trace-detail",
      key: "rules",
      button: "규칙 추적"
    }
  ];
  return [
    '<article class="panel ontology-panel ontology-trace-panel strategy-trace-overview-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Review Trace</p>',
    '<h2>검증·리뷰</h2>',
    '<p class="subtle">검증 화면은 큰 덩어리별 상태를 먼저 보고, 상세 표와 긴 목록은 필요할 때만 엽니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(parts.relations.length) + '</span>',
    '</div>',
    '<div class="work-detail-metric-row">',
    renderNotificationDetailMetric("추론 원장", (ledgerSummary.ledgerCount || ledgerRows.length || 0) + "건", ledgerRows.length ? "watch" : "hold"),
    renderNotificationDetailMetric("모델 액션", modelStats.actionCount + "개", modelStats.actionCount ? "watch" : "hold"),
    renderNotificationDetailMetric("관계 행", parts.relations.length + "개", parts.relations.length ? "watch" : "hold"),
    renderNotificationDetailMetric("인사이트", insights.length + "개", insights.length ? "watch" : "hold"),
    renderNotificationDetailMetric("품질 노드", qualityNodes.length + "개", qualityNodes.length ? "watch" : "muted"),
    '</div>',
    '<div class="work-detail-grid strategy-trace-grid">',
    cards.map(renderStrategyOverviewActionCard).join(""),
    '</div>',
    '<div class="rule-strip"><span>기본 화면에서는 검증 대상의 위치만 파악합니다.</span><span>상세 레이어에서 추론 원장, 모델 리뷰, 관계 투영, 규칙 추적을 하나씩 열어 원인을 확인합니다.</span></div>',
    '</article>'
  ].join("");
}

function renderStrategyReviewStatusPanel(snapshot, parts) {
  var analysis = investmentAnalysisModel(snapshot);
  var lineageStats = investmentLineageStats(analysis.dataLineage || {});
  var ledgerSummary = inferenceLedgerSummary();
  var ledgerRows = inferenceLedgerRows();
  var modelStats = modelStatsForItems(buildTradeSignalItems(snapshot));
  var proposals = strategyProposalItems();
  var approved = proposals.filter(function (proposal) {
    return ["approved", "deployed"].indexOf(String(proposal.status || "")) >= 0;
  }).length;
  return [
    '<article class="panel strategy-review-status-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Review Status</p>',
    '<h2>검증 상태판</h2>',
    '<p class="subtle">데이터 품질, TypeDB 추론, 모델 액션, 전략 성과를 먼저 확인하고 상세 원장은 필요할 때 엽니다.</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(lineageStats.missing || lineageStats.mock ? "caution" : "watch") + '">' + escapeHtml(lineageStats.missing || lineageStats.mock ? "검증 필요" : "검증 가능") + '</span>',
    '</div>',
    '<div class="strategy-review-status-grid">',
    renderInvestmentTodayStatusCell("데이터 품질", lineageStats.actual + "/" + Math.max(1, lineageStats.actual + lineageStats.mock), "mock " + lineageStats.mock + " · missing " + lineageStats.missing, lineageStats.mock || lineageStats.missing ? "caution" : "watch"),
    renderInvestmentTodayStatusCell("추론 원장", ledgerSummary.ledgerCount || ledgerRows.length || 0, ontologyState.ontologyInferenceLedgerLoaded ? "loaded" : "lazy", ledgerRows.length ? "watch" : "hold"),
    renderInvestmentTodayStatusCell("모델 액션", modelStats.actionCount, "buy/sell/hold", modelStats.actionCount ? "watch" : "hold"),
    renderInvestmentTodayStatusCell("전략 성과", approved + "/" + proposals.length, "승인·운영 제안", approved ? "watch" : "hold"),
    renderInvestmentTodayStatusCell("관계 투영", parts.relations.length, "ABox/InferenceBox", parts.relations.length ? "watch" : "hold"),
    '</div>',
    '</article>'
  ].join("");
}

export { capitalFlowDirectionMeta, capitalFlowModel, capitalFlowPartyText, capitalFlowValueText, investmentActionDecisionType, investmentActionInvalidation, investmentActionLinkedAlert, investmentActionNextWindow, investmentActionPlaybook, investmentActionUserPresentation, investmentActionValidation, investmentAiInferencePacket, investmentAnalysisModel, investmentChecklistStats, investmentDecisionStats, investmentLineageStats, investmentReasoningCards, normalizeInvestmentChartPeriod, renderInvestmentActionDecisionDetail, renderInvestmentChartControlPanel, renderInvestmentGraphGatePanel, renderInvestmentIntegratedChartPanel, renderStrategyOverviewActionCard, renderStrategyReviewStatusPanel, renderStrategySectionContent, renderStrategyTraceOverviewPanel };
