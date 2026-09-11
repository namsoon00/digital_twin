import { investmentActionKey } from "./actions.mjs";
import { investmentActionDecisionType, investmentActionInvalidation, investmentActionNextWindow, investmentActionPlaybook, investmentActionValidation, investmentAnalysisModel, investmentChecklistStats, investmentDecisionStats, investmentLineageStats } from "./strategy.mjs";
import { stockDisplayName } from "../instruments/catalog.mjs";
import { sourceLabel } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { decisionsState } from "../state/decisions.mjs";

function investmentTodayMarketState(stats, gate, lineageStats) {
  stats = stats || {};
  gate = gate || {};
  lineageStats = lineageStats || {};
  if (Number(gate.blockedCount || 0) || stats.blocked || lineageStats.missing) {
    return { label: "판단 보류", tone: "caution", detail: "차단 요인 먼저 확인" };
  }
  if (stats.risk > stats.buy) return { label: "위험 점검", tone: "danger", detail: stats.risk + "개 축소 후보" };
  if (stats.buy > stats.risk && stats.buy) return { label: "기회 탐색", tone: "watch", detail: stats.buy + "개 매수 후보" };
  return { label: "중립", tone: "hold", detail: "우선순위 확인" };
}

function renderInvestmentTodayStatusPanel(snapshot, parts) {
  var analysis = investmentAnalysisModel(snapshot);
  var board = analysis.board || {};
  var rows = Array.isArray(analysis.actionQueue) ? analysis.actionQueue : [];
  var stats = investmentDecisionStats(rows, rows);
  var gate = analysis.graphGate || {};
  var checklist = investmentChecklistStats(Array.isArray(board.checklist) ? board.checklist : []);
  var lineageStats = investmentLineageStats(analysis.dataLineage || {});
  var market = investmentTodayMarketState(stats, gate, lineageStats);
  var freshnessTone = lineageStats.missing || lineageStats.stale || lineageStats.mock ? "caution" : "watch";
  var checklistText = checklist.total ? checklist.done + "/" + checklist.total : "-";
  return [
    '<article class="panel investment-today-status-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Daily Decision Board</p>',
    '<h2>오늘의 판단</h2>',
    '<p class="subtle">결론, 우선순위, 막힌 이유만 먼저 확인합니다. 근거·차트·룰·온톨로지는 하위 탭에서 봅니다.</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(market.tone) + '">' + escapeHtml(market.label) + '</span>',
    '</div>',
    '<div class="investment-today-status-grid">',
    renderInvestmentTodayStatusCell("시장 상태", market.label, market.detail, market.tone),
    renderInvestmentTodayStatusCell("데이터 신선도", lineageStats.missing ? "부족" : (lineageStats.stale || lineageStats.mock ? "확인" : "정상"), "실제 " + lineageStats.actual + " · mock " + lineageStats.mock, freshnessTone),
    renderInvestmentTodayStatusCell("체크리스트", checklistText, checklist.pending ? checklist.pending + "개 남음" : "완료 기준 확인", checklist.pending ? "caution" : "watch"),
    renderInvestmentTodayStatusCell("액션 필요", stats.total + "개", "매수 " + stats.buy + " · 축소 " + stats.risk, stats.total ? "watch" : "hold"),
    renderInvestmentTodayStatusCell("주요 리스크", (stats.risk + stats.blocked) + "개", "추론 보류 " + stats.blocked, (stats.risk + stats.blocked) ? "danger" : "hold"),
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentTodayStatusCell(label, value, detail, tone) {
  return [
    '<section class="investment-today-status-cell ' + escapeHtml(tone || "hold") + '"' + cardTypeAttrs("metric-cell", tone || "hold") + '>',
    '<span>' + escapeHtml(label) + '</span>',
    '<strong>' + escapeHtml(value == null ? "-" : value) + '</strong>',
    '<em>' + escapeHtml(detail || "") + '</em>',
    '</section>'
  ].join("");
}

function investmentTodayActionGroup(row) {
  row = row || {};
  var graph = row.graph || {};
  var quality = String(row.dataQuality || "").toLowerCase();
  if (graph.blocked || ["missing", "mock", "demo", "stale", "gap", "error"].indexOf(quality) >= 0) return "data";
  if (String(row.tone || "").toLowerCase() === "danger" || String(row.tone || "").toLowerCase() === "caution") return "immediate";
  var type = investmentActionDecisionType(row);
  if (type === "buy") return "buy";
  if (type === "risk") return "risk";
  return "hold";
}

function investmentTodayActionGroups(rows) {
  var groups = [
    { id: "immediate", label: "즉시 확인", tone: "danger", rows: [] },
    { id: "buy", label: "매수 후보", tone: "watch", rows: [] },
    { id: "risk", label: "매도·축소 후보", tone: "danger", rows: [] },
    { id: "hold", label: "관망", tone: "hold", rows: [] },
    { id: "data", label: "데이터 부족", tone: "caution", rows: [] }
  ];
  var byId = groups.reduce(function (memo, group) {
    memo[group.id] = group;
    return memo;
  }, {});
  (rows || []).forEach(function (row, index) {
    var group = byId[investmentTodayActionGroup(row)] || byId.hold;
    group.rows.push({ row: row, index: index });
  });
  return groups;
}

function investmentTodaySelectedAction(rows) {
  rows = Array.isArray(rows) ? rows : [];
  var selectedKey = String(decisionsState.expandedInvestmentActionKey || "");
  var selected = null;
  rows.forEach(function (row, index) {
    if (selected) return;
    var key = investmentActionKey(row, index);
    if (selectedKey && key === selectedKey) selected = { row: row, index: index, key: key };
  });
  if (selected) return selected;
  if (!rows.length) return null;
  return { row: rows[0], index: 0, key: investmentActionKey(rows[0], 0) };
}

function renderInvestmentTodayActionBoardPanel(snapshot) {
  var analysis = investmentAnalysisModel(snapshot);
  var rows = Array.isArray(analysis.actionQueue) ? analysis.actionQueue : [];
  var selected = investmentTodaySelectedAction(rows);
  var groups = investmentTodayActionGroups(rows);
  return [
    '<article class="panel investment-today-action-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Action Queue</p>',
    '<h2>액션 큐</h2>',
    '<p class="subtle">그룹별 최대 4개만 표시합니다. 상세 근거는 선택 요약이나 하위 탭에서 확인합니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(rows.length) + '</span>',
    '</div>',
    '<div class="investment-today-action-groups">',
    groups.map(function (group) {
      return renderInvestmentTodayActionGroup(group, selected ? selected.key : "");
    }).join(""),
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentTodayActionGroup(group, selectedKey) {
  var visible = group.rows.slice(0, 4);
  var hiddenCount = Math.max(0, group.rows.length - visible.length);
  return [
    '<section class="investment-today-action-group ' + escapeHtml(group.tone || "hold") + '"' + cardTypeAttrs("action-group", group.tone || "hold") + '>',
    '<div class="investment-today-action-group-head">',
    '<strong>' + escapeHtml(group.label) + '</strong>',
    '<span class="tone-chip ' + escapeHtml(group.tone || "hold") + '">' + escapeHtml(group.rows.length + "개") + '</span>',
    '</div>',
    '<div class="investment-today-action-list">',
    visible.length ? visible.map(function (entry) {
      return renderInvestmentTodayActionTicket(entry.row, entry.index, selectedKey);
    }).join("") : '<div class="ontology-empty">표시할 항목 없음</div>',
    hiddenCount ? '<span class="investment-today-more">+' + escapeHtml(hiddenCount) + '개는 투자 근거/검증 탭에서 확인</span>' : '',
    '</div>',
    '</section>'
  ].join("");
}

function renderInvestmentTodayActionTicket(row, index, selectedKey) {
  row = row || {};
  var graph = row.graph || {};
  var key = investmentActionKey(row, index);
  var active = selectedKey && selectedKey === key;
  var reason = (Array.isArray(row.reasons) ? row.reasons[0] : "") || graph.reason || investmentActionInvalidation(row);
  var displayName = row.name || stockDisplayName(row.symbol, row);
  return [
    '<button type="button" class="investment-today-ticket ' + escapeHtml(active ? "active" : "") + '" data-investment-action-toggle="' + escapeHtml(key) + '">',
    '<span><strong>' + escapeHtml(displayName || row.symbol || "-") + '</strong><em>' + escapeHtml([row.symbol, sourceLabel(row.source), row.market].filter(Boolean).join(" · ")) + '</em></span>',
    '<b class="tone-chip ' + escapeHtml(row.tone || "hold") + '">' + escapeHtml(row.decision || "대기") + '</b>',
    '<i>' + escapeHtml(reason || "다음 확인 조건 대기") + '</i>',
    '</button>'
  ].join("");
}

function renderInvestmentTodaySelectedPanel(snapshot) {
  var analysis = investmentAnalysisModel(snapshot);
  var rows = Array.isArray(analysis.actionQueue) ? analysis.actionQueue : [];
  var selected = investmentTodaySelectedAction(rows);
  if (!selected) {
    return [
      '<article class="panel investment-today-selected-panel">',
      '<div class="panel-head"><div><p class="label">Selected Instrument</p><h2>선택 종목 요약</h2></div></div>',
      renderEmptyState({ tone: "muted", label: "Selection", title: "선택할 액션 후보가 없습니다", description: "보유·관심 종목의 추론 결과가 생기면 여기서 핵심 판단만 보여줍니다.", meta: ["액션 큐", "요약"] }),
      '</article>'
    ].join("");
  }
  var row = selected.row || {};
  var graph = row.graph || {};
  var reasons = Array.isArray(row.reasons) ? row.reasons.slice(0, 3) : [];
  var checks = Array.isArray(graph.nextChecks) ? graph.nextChecks.slice(0, 3) : [];
  var validation = investmentActionValidation(row);
  var playbook = investmentActionPlaybook(row);
  var displayName = row.name || stockDisplayName(row.symbol, row);
  return [
    '<article class="panel investment-today-selected-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Selected Instrument</p>',
    '<h2>선택 종목 요약</h2>',
    '<p class="subtle">액션 큐에서 고른 한 종목의 판단, 핵심 근거, 무효화 조건만 압축해 봅니다.</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(row.tone || "hold") + '">' + escapeHtml(row.decision || "판단 대기") + '</span>',
    '</div>',
    '<div class="investment-today-selected-head">',
    '<strong>' + escapeHtml(displayName || row.symbol || "-") + '</strong>',
    '<span>' + escapeHtml([row.symbol, sourceLabel(row.source), row.market, row.sector].filter(Boolean).join(" · ")) + '</span>',
    '</div>',
    '<div class="investment-today-selected-metrics">',
    renderInvestmentTodayStatusCell("검증 상태", validation.label, validation.detail, validation.tone),
    renderInvestmentTodayStatusCell("전략", playbook.label, playbook.detail, playbook.tone),
    renderInvestmentTodayStatusCell("데이터", row.dataQuality || "-", row.apiSource || "출처 확인", row.dataQuality === "actual" ? "watch" : "caution"),
    '</div>',
    '<div class="investment-today-selected-block">',
    '<strong>핵심 근거</strong>',
    reasons.length ? reasons.map(function (reason) { return '<span>' + escapeHtml(reason) + '</span>'; }).join("") : '<span>연결된 근거가 아직 없습니다.</span>',
    '</div>',
    '<div class="investment-today-selected-block caution">',
    '<strong>무효화 조건</strong>',
    '<span>' + escapeHtml(investmentActionInvalidation(row)) + '</span>',
    '</div>',
    '<div class="investment-today-selected-block">',
    '<strong>다음 확인 액션</strong>',
    checks.length ? checks.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") : '<span>' + escapeHtml(investmentActionNextWindow(row)) + '</span>',
    '</div>',
    '<div class="investment-today-selected-actions">',
    '<a class="text-button compact" href="?tab=modeling&strategy=evidence">투자 근거</a>',
    '<a class="text-button compact" href="?tab=modeling&strategy=charts">통합 차트</a>',
    '<a class="text-button compact" href="?tab=modeling&strategy=graphs">온톨로지 추적</a>',
    '</div>',
    '</article>'
  ].join("");
}

function investmentTodayBlockers(snapshot) {
  var analysis = investmentAnalysisModel(snapshot);
  var board = analysis.board || {};
  var gate = analysis.graphGate || {};
  var lineage = analysis.dataLineage || {};
  var lineageRows = Array.isArray(lineage.items) ? lineage.items : [];
  var rows = Array.isArray(analysis.actionQueue) ? analysis.actionQueue : [];
  var blockers = [];
  if (Number(gate.blockedCount || 0) || String(gate.status || "").toLowerCase().indexOf("block") >= 0) {
    blockers.push({ label: "온톨로지 추론", value: gate.status || "blocked", detail: gate.reason || gate.requiredSource || "InferenceBox 확인", tone: "caution" });
  }
  var checklist = investmentChecklistStats(Array.isArray(board.checklist) ? board.checklist : []);
  if (checklist.pending) blockers.push({ label: "체크리스트", value: checklist.pending + "개 미완료", detail: "투자 전 확인 항목", tone: "caution" });
  var weakData = lineageRows.filter(function (item) {
    var quality = String(item && item.quality || "").toLowerCase();
    return ["mock", "demo", "stale", "missing", "gap", "error"].indexOf(quality) >= 0;
  });
  if (weakData.length) blockers.push({ label: "데이터 품질", value: weakData.length + "개 확인", detail: weakData.slice(0, 2).map(function (item) { return item.name || item.symbol || item.source || "-"; }).join(", "), tone: "caution" });
  var blockedRows = rows.filter(function (row) { return row && row.graph && row.graph.blocked; });
  if (blockedRows.length) blockers.push({ label: "종목 판단", value: blockedRows.length + "개 보류", detail: blockedRows.slice(0, 2).map(function (row) { return row.name || stockDisplayName(row.symbol, row); }).join(", "), tone: "danger" });
  if (!rows.length) blockers.push({ label: "액션 큐", value: "비어 있음", detail: "보유·관심 종목과 데이터 수집 상태 확인", tone: "hold" });
  return blockers;
}

function renderInvestmentTodayBlockersPanel(snapshot) {
  var blockers = investmentTodayBlockers(snapshot);
  return [
    '<article class="panel investment-today-blockers-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Decision Blockers</p>',
    '<h2>오늘의 차단 요인</h2>',
    '<p class="subtle">투자 판단 전에 해결하거나 감안해야 하는 조건만 분리합니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(blockers.length) + '</span>',
    '</div>',
    '<div class="investment-today-blocker-list">',
    blockers.length ? blockers.slice(0, 5).map(function (item) {
      return [
        '<section class="investment-today-blocker ' + escapeHtml(item.tone || "hold") + '"' + cardTypeAttrs("diagnostic-card", item.tone || "hold") + '>',
        '<span>' + escapeHtml(item.label) + '</span>',
        '<strong>' + escapeHtml(item.value || "-") + '</strong>',
        '<em>' + escapeHtml(item.detail || "") + '</em>',
        '</section>'
      ].join("");
    }).join("") : '<div class="ontology-empty">현재 차단 요인이 없습니다.</div>',
    blockers.length > 5 ? '<span class="investment-today-more">+' + escapeHtml(blockers.length - 5) + '개는 검증·리뷰에서 확인</span>' : '',
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentDecisionSummaryRail(rows, filteredRows) {
  var stats = investmentDecisionStats(rows, filteredRows);
  return [
    '<div class="investment-decision-summary-rail" aria-label="오늘의 판단 요약">',
    renderInvestmentDecisionSummaryCell("전체 후보", stats.total, stats.visible + "개 표시", "hold"),
    renderInvestmentDecisionSummaryCell("매수 후보", stats.buy, "진입 검토", stats.buy ? "watch" : "hold"),
    renderInvestmentDecisionSummaryCell("매도·축소", stats.risk, "리스크 점검", stats.risk ? "danger" : "hold"),
    renderInvestmentDecisionSummaryCell("추론 보류", stats.blocked, "게이트 확인", stats.blocked ? "caution" : "watch"),
    renderInvestmentDecisionSummaryCell("연결 알림", stats.alerts, "발송 정책 확인", stats.alerts ? "watch" : "hold"),
    '</div>'
  ].join("");
}

function renderInvestmentDecisionSummaryCell(label, value, detail, tone) {
  return [
    '<section class="investment-decision-summary-cell ' + escapeHtml(tone || "hold") + '"' + cardTypeAttrs("metric-cell", tone || "hold") + '>',
    '<span>' + escapeHtml(label) + '</span>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '<em>' + escapeHtml(detail || "") + '</em>',
    '</section>'
  ].join("");
}

function renderInvestmentDecisionCell(label, value, detail, tone) {
  return [
    '<section class="investment-decision-cell ' + escapeHtml(tone || "hold") + '"' + cardTypeAttrs("signal-card", tone || "hold") + '>',
    '<span>' + escapeHtml(label) + '</span>',
    '<strong>' + escapeHtml(value || "-") + '</strong>',
    detail ? '<em>' + escapeHtml(detail) + '</em>' : '',
    '</section>'
  ].join("");
}

export { renderInvestmentDecisionCell, renderInvestmentDecisionSummaryRail, renderInvestmentTodayActionBoardPanel, renderInvestmentTodayBlockersPanel, renderInvestmentTodaySelectedPanel, renderInvestmentTodayStatusCell, renderInvestmentTodayStatusPanel };
