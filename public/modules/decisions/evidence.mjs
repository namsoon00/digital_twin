import { decisionStateMeta } from "./signals.mjs";
import { capitalFlowDirectionMeta, capitalFlowModel, capitalFlowPartyText, capitalFlowValueText, investmentActionPlaybook, investmentAiInferencePacket, investmentAnalysisModel, investmentReasoningCards } from "./strategy.mjs";
import { renderInvestmentDecisionCell, renderInvestmentTodayStatusCell } from "./today.mjs";
import { stockDisplayName, textWithKnownDisplaySymbols } from "../instruments/catalog.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { renderNotificationDetailMetric } from "../notifications/reasoning.mjs";
import { ontologyStrategyParts } from "../ontology/strategy.mjs";
import { strategyProposalItems, strategyProposalPayload, strategyProposalPerformanceSummary, strategyProposalSignedPercent, strategyProposalSummary } from "../proposals/workspace.mjs";
import { formatClock, hasNumericValue, sourceLabel } from "../shared/format.mjs";
import { beginnerFriendlyText, escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { proposalsState } from "../state/proposals.mjs";
import { shellState } from "../state/shell.mjs";

function renderInvestmentPlaybookPanel(snapshot) {
  var analysis = investmentAnalysisModel(snapshot);
  var rows = Array.isArray(analysis.actionQueue) ? analysis.actionQueue : [];
  var gate = analysis.graphGate || {};
  var focus = analysis.accountFocus || {};
  var playbookCounts = rows.reduce(function (acc, row) {
    var playbook = investmentActionPlaybook(row);
    acc[playbook.id] = (acc[playbook.id] || 0) + 1;
    return acc;
  }, {});
  var cards = [
    {
      id: "entry",
      title: "진입 후보 검증",
      tone: playbookCounts.entry ? "watch" : "hold",
      count: playbookCounts.entry || 0,
      detail: "매수 후보는 진입 근거, 데이터 신선도, 무효화 조건을 먼저 봅니다."
    },
    {
      id: "risk",
      title: "리스크 축소",
      tone: playbookCounts.risk ? "danger" : "hold",
      count: playbookCounts.risk || 0,
      detail: "매도·축소 후보는 회복 조건과 반대 신호를 함께 확인합니다."
    },
    {
      id: "macro",
      title: "환율·금리 민감",
      tone: playbookCounts.macro ? "caution" : "hold",
      count: playbookCounts.macro || 0,
      detail: "달러, 금리, 시장 흐름이 종목 판단에 미치는 경로를 추적합니다."
    },
    {
      id: "defense",
      title: "보유 방어",
      tone: (playbookCounts.defense || focus.holdingCount) ? "hold" : "muted",
      count: playbookCounts.defense || focus.holdingCount || 0,
      detail: "보유 종목은 유지 조건과 반대 신호를 매일 같은 기준으로 봅니다."
    },
    {
      id: "gate",
      title: "추론 게이트 복구",
      tone: (playbookCounts.gate || gate.blockedCount) ? "caution" : "watch",
      count: playbookCounts.gate || gate.blockedCount || 0,
      detail: "TypeDB InferenceBox, RuleBox, 데이터 신선도 차단 여부를 점검합니다."
    }
  ];
  return [
    '<article class="panel investment-playbook-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Strategy Playbook</p>',
    '<h2>전략 플레이북</h2>',
    '<p class="subtle">오늘의 후보를 어떤 전략 관점으로 볼지 먼저 분류합니다.</p>',
    '</div>',
    '<a class="text-button" href="?tab=modeling&mode=settings&strategy=rules">룰 조정</a>',
    '</div>',
    '<div class="investment-playbook-list">',
    cards.map(function (card) {
      return [
        '<section class="investment-playbook-card ' + escapeHtml(card.tone || "hold") + '"' + cardTypeAttrs("strategy-card", card.tone || "hold") + '>',
        '<span class="tone-chip ' + escapeHtml(card.tone || "hold") + '">' + escapeHtml(card.count + "건") + '</span>',
        '<strong>' + escapeHtml(card.title) + '</strong>',
        '<p>' + escapeHtml(card.detail) + '</p>',
        '</section>'
      ].join("");
    }).join(""),
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentPerformanceFeedbackPanel(snapshot) {
  var payload = strategyProposalPayload();
  var summary = strategyProposalSummary();
  var statuses = summary.statuses && typeof summary.statuses === "object" ? summary.statuses : {};
  var proposals = strategyProposalItems();
  var samples = 0;
  var excessSum = 0;
  var excessCount = 0;
  proposals.forEach(function (proposal) {
    var perf = strategyProposalPerformanceSummary(proposal);
    var count = Number(perf.sampleCount || 0);
    samples += count;
    if (perf.avgExcessReturnPct != null && count) {
      excessSum += Number(perf.avgExcessReturnPct || 0) * count;
      excessCount += count;
    }
  });
  var avgExcess = excessCount ? excessSum / excessCount : null;
  var waiting = Number(statuses.proposed || 0) + Number(statuses.validated || 0);
  var model = investmentAnalysisModel(snapshot);
  var actionCount = Array.isArray(model.actionQueue) ? model.actionQueue.length : 0;
  return [
    '<article class="panel investment-performance-feedback-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Feedback Loop</p>',
    '<h2>성과 피드백</h2>',
    '<p class="subtle">전략 제안 승인 큐와 성과 표본을 오늘의 판단 옆에서 바로 확인합니다.</p>',
    '</div>',
    '<a class="text-button" href="?tab=modeling&strategy=proposals">성과 기록</a>',
    '</div>',
    '<div class="investment-feedback-grid">',
    renderInvestmentDecisionCell("오늘 후보", actionCount + "개", "Decision Inbox", actionCount ? "watch" : "hold"),
    renderInvestmentDecisionCell("검토 대기", waiting + "건", "전략 제안", waiting ? "caution" : "hold"),
    renderInvestmentDecisionCell("성과 표본", samples + "개", proposalsState.strategyProposalsLoaded ? "저장된 표본" : "조회 대기", samples ? "watch" : "hold"),
    renderInvestmentDecisionCell("초과 수익", avgExcess == null ? "-" : strategyProposalSignedPercent(avgExcess), "평균 표본", avgExcess == null ? "hold" : (avgExcess >= 0 ? "watch" : "danger")),
    '</div>',
    proposalsState.strategyProposalsLoading ? '<div class="rule-strip"><span>전략 성과 표본을 읽는 중입니다.</span></div>' : '',
    proposalsState.strategyProposalsError ? '<p class="form-error">' + escapeHtml(proposalsState.strategyProposalsError) + '</p>' : '',
    !proposalsState.strategyProposalsLoaded && !proposalsState.strategyProposalsLoading ? '<div class="rule-strip"><span>성과 피드백은 전략 제안 데이터를 읽은 뒤 갱신됩니다.</span></div>' : '',
    payload.count || proposals.length ? '' : '<div class="rule-strip"><span>아직 승인된 전략 표본이 없으면 후보 판단과 실제 결과를 비교할 수 없습니다.</span></div>',
    '</article>'
  ].join("");
}

function renderInvestmentDataLineagePanel(snapshot) {
  var lineage = investmentAnalysisModel(snapshot).dataLineage || {};
  var rows = Array.isArray(lineage.items) ? lineage.items : [];
  return [
    '<article class="panel investment-lineage-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Data Lineage</p>',
    '<h2>실제·mock·API 출처</h2>',
    '<p class="subtle">화면에 쓰인 기본 데이터가 실제인지 mock인지, 어떤 API에서 왔는지 분리합니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(lineage.actualCount || 0) + '/' + escapeHtml((lineage.actualCount || 0) + (lineage.mockCount || 0)) + '</span>',
    '</div>',
    '<div class="investment-lineage-list">',
    rows.length ? rows.slice(0, 12).map(function (row) {
      return [
        '<div class="investment-lineage-row"' + cardTypeAttrs("source-card") + '>',
        '<div><strong>' + escapeHtml(row.name || stockDisplayName(row.symbol, row)) + '</strong><span>' + escapeHtml(row.symbol || "") + '</span></div>',
        '<em>' + escapeHtml(row.quality || "-") + '</em>',
        '<span>' + escapeHtml(row.source || "-") + '</span>',
        '<b>' + escapeHtml(row.updatedAt ? formatClock(row.updatedAt) : "-") + '</b>',
        '</div>'
      ].join("");
    }).join("") : '<div class="ontology-empty">데이터 출처가 없습니다.</div>',
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentMoneyFlowPanel(snapshot) {
  var flow = capitalFlowModel(snapshot);
  var markets = Array.isArray(flow.markets) ? flow.markets : [];
  var sectors = Array.isArray(flow.sectors) ? flow.sectors : [];
  var subjects = Array.isArray(flow.subjects) ? flow.subjects : [];
  var transitions = Array.isArray(flow.transitions) ? flow.transitions : [];
  var quality = flow.quality || {};
  var portfolio = flow.portfolioImpact || {};
  var storage = flow.storageQuality || {};
  var storageFlow = storage.capitalFlow || storage;
  var windowDays = Number(flow.windowDays || 5);
  var statusLabel = flow.status === "ready" ? "관측 중" : (flow.status === "unavailable" ? "저장소 오류" : "수집 대기");
  var statusTone = flow.status === "ready" ? "watch" : "caution";
  var portfolioAvailable = portfolio.status === "ready";
  var coverageText = Number(quality.subjectCount || 0) > 0
    ? Number(quality.sufficientSubjectCount || 0) + "/" + Number(quality.subjectCount || 0) + " 종목"
    : "미수집";
  var outflowExposure = !portfolioAvailable
    ? "계좌 자료 없음"
    : hasNumericValue(portfolio.outflowExposureRatioPct)
    ? Number(portfolio.outflowExposureRatioPct).toFixed(1) + "%"
    : "해당 없음";
  var outflowHoldingText = portfolioAvailable
    ? Number(portfolio.outflowHoldingCount || 0) + "개 보유 종목"
    : "마지막 계좌 스냅샷 확인 필요";
  var overview = markets.slice(0, 3).concat(sectors.slice(0, 4).map(function (item) {
    return Object.assign({ scopeLabel: "업종" }, item);
  }));
  return [
    '<article class="panel investment-flow-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Capital Flow</p>',
    '<h2>외국인·기관 자금 흐름</h2>',
    '<p class="subtle">관측 범위: ' + escapeHtml(markets.map(function (item) { return item.label || item.key; }).filter(Boolean).join(" · ") || "시장 확인 필요") + ' · ' + escapeHtml(subjects.length + "종목 · " + windowDays + "일") + '. 전체 시장을 대표하는 집계가 아닙니다.</p>',
    '</div>',
    '<div class="capital-flow-head-meta"><span class="tone-chip ' + statusTone + '">' + escapeHtml(statusLabel) + '</span><span class="metric">' + escapeHtml(subjects.length + "종목 · " + windowDays + "일") + '</span></div>',
    '</div>',
    '<div class="capital-flow-quality-strip">',
    '<section><span>판단 가능</span><strong>' + escapeHtml(coverageText) + '</strong><em>필요 관측 충족</em></section>',
    '<section><span>보유 유출 노출</span><strong>' + escapeHtml(outflowExposure) + '</strong><em>' + escapeHtml(outflowHoldingText) + '</em></section>',
    '<section><span>별도 저장 관측</span><strong>' + escapeHtml(Number(storageFlow.observationCount || quality.canonicalObservationCount || 0).toLocaleString("ko-KR") + "건") + '</strong><em>결측→0 변환 ' + escapeHtml(Number(storage.missingConvertedToZeroCount || quality.missingConvertedToZeroCount || 0)) + '건</em></section>',
    '<section><span>자료 기준</span><strong>' + escapeHtml(flow.asOf ? formatClock(flow.asOf) : "미수집") + '</strong><em>' + escapeHtml(Number(storageFlow.dailyFinalCount || 0) + "건 확정") + '</em></section>',
    '</div>',
    '<div class="investment-flow-grid">',
    overview.length ? overview.map(function (item) {
      var direction = capitalFlowDirectionMeta(item.direction);
      return [
        '<section' + cardTypeAttrs("source-card", direction.tone) + '>',
        '<span>' + escapeHtml(item.scopeLabel || "시장") + ' · ' + escapeHtml(direction.label) + '</span>',
        '<strong>' + escapeHtml(item.label || item.key || "-") + '</strong>',
        '<em>' + escapeHtml(capitalFlowValueText(item)) + (hasNumericValue(item.normalizedFlowPct) ? ' · 거래대금 대비 ' + escapeHtml(Number(item.normalizedFlowPct).toFixed(2)) + '%' : '') + '</em>',
        '</section>'
      ].join("");
    }).join("") : '<div class="ontology-empty">외국인·기관 수급 관측이 아직 없습니다.</div>',
    '</div>',
    '<div class="capital-flow-table" role="table" aria-label="종목별 외국인 기관 자금 흐름">',
    '<div class="capital-flow-row capital-flow-table-head" role="row"><span>종목</span><span>합산</span><span>외국인</span><span>기관</span><span>지속성</span><span>기준</span></div>',
    subjects.length ? subjects.slice(0, 12).map(function (item) {
      var direction = capitalFlowDirectionMeta(item.direction);
      var persistence = hasNumericValue(item.persistenceRatio) ? Math.round(Number(item.persistenceRatio) * 100) + "%" : "미수집";
      return '<div class="capital-flow-row" role="row"' + cardTypeAttrs("signal-card", direction.tone) + '><span><strong>' + escapeHtml(stockDisplayName(item.subjectId, item)) + '</strong><em>' + escapeHtml([item.subjectId, item.sector].filter(Boolean).join(" · ")) + '</em></span><span><b class="flow-value ' + escapeHtml(direction.tone) + '">' + escapeHtml(capitalFlowValueText(item)) + '</b><em>' + escapeHtml(direction.label) + '</em></span><span><b>' + escapeHtml(capitalFlowPartyText(item.foreign)) + '</b></span><span><b>' + escapeHtml(capitalFlowPartyText(item.institution)) + '</b></span><span><b>' + escapeHtml(persistence) + '</b><em>' + escapeHtml(Number(item.observationCount || 0) + "/" + Number(item.requiredObservationCount || windowDays) + "일") + '</em></span><span><b>' + escapeHtml(item.throughTradingDate || "-") + '</b><em>' + escapeHtml(item.measurementType === "daily-final" ? "확정" : "추정") + '</em></span></div>';
    }).join("") : '<div class="ontology-empty">종목별 수급 데이터가 수집되면 표시됩니다.</div>',
    '</div>',
    transitions.length ? '<div class="investment-emerging-list capital-flow-transitions"><p class="label">최근 방향 전환</p>' + transitions.slice(0, 6).map(function (item) {
      var direction = capitalFlowDirectionMeta(item.toDirection);
      return '<div' + cardTypeAttrs("signal-card", direction.tone) + '><strong>' + escapeHtml(stockDisplayName(item.subjectId, item)) + '</strong><span>' + escapeHtml(capitalFlowDirectionMeta(item.fromDirection).label + " → " + direction.label + " · " + Number(item.windowDays || windowDays) + "일") + '</span><em>' + escapeHtml(item.throughTradingDate || "-") + '</em></div>';
    }).join("") + '</div>' : '',
    '</article>'
  ].join("");
}

function renderInvestmentBridgePanel(snapshot) {
  var parts = ontologyStrategyParts(snapshot);
  var cards = investmentReasoningCards(snapshot);
  var packet = investmentAiInferencePacket(snapshot);
  var graphInputs = packet.graphInputs || {};
  var steps = [
    ["01", "전략 근거", "가격·수급·추세·관계 조건", cards.length + " cards"],
    ["02", "관계 그래프", "HOLDS/WATCHES와 TBox 규칙", (graphInputs.relationCount || parts.relations.length || 0) + " relations"],
    ["03", "AI 추론 입력", packet.contract || "investment-ontology-ai-inference-v1", (packet.reasoningCardCount || cards.length || 0) + " refs"],
    ["04", "투자 의견", "관계·반대 신호·다음 검증", (graphInputs.opinionCount || parts.opinions.length || 0) + " opinions"]
  ];
  return [
    '<article class="panel investment-bridge-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Investment Analysis</p>',
    '<h2>전략 데이터와 관계 분석을 잇는 추론 구조</h2>',
    '<p class="subtle">TypeDB가 실제 값으로 성립 조건과 근거 역할을 만들고, AI는 TBox/ABox 관계와 reasoning card를 읽어 의견을 설명합니다.</p>',
    '</div>',
    '<span class="tone-chip watch">ontology-first</span>',
    '</div>',
    '<div class="investment-bridge-flow">',
    steps.map(function (step) {
      return [
        '<div class="investment-bridge-step"' + cardTypeAttrs("process-card") + '>',
        '<b>' + escapeHtml(step[0]) + '</b>',
        '<span><strong>' + escapeHtml(step[1]) + '</strong><em>' + escapeHtml(step[2]) + '</em></span>',
        '<i>' + escapeHtml(step[3]) + '</i>',
        '</div>'
      ].join("");
    }).join(""),
    '</div>',
    '<div class="rule-strip">',
    '<span>보유 종목은 HOLDS, 관심 종목은 WATCHES 관계로 구분합니다.</span>',
    '<span>AI 추론 입력은 strategyEvidence, relationEvidence, graphContext ID를 함께 전달합니다.</span>',
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentAiPacketPanel(snapshot) {
  var packet = investmentAiInferencePacket(snapshot);
  var inputOrder = Array.isArray(packet.inputOrder) ? packet.inputOrder : [];
  var guardrails = Array.isArray(packet.guardrails) ? packet.guardrails : [];
  return [
    '<article class="panel investment-ai-packet-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">AI Inference Packet</p>',
    '<h2>AI 추론 입력 계약</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(packet.reasoningCardCount || investmentReasoningCards(snapshot).length || 0) + '</span>',
    '</div>',
    '<div class="investment-packet-grid">',
    '<section><strong>계약</strong><span>' + escapeHtml(packet.contract || "investment-ontology-ai-inference-v1") + '</span><em>' + escapeHtml(packet.promptVersion || "-") + '</em></section>',
    '<section><strong>입력 순서</strong><span>' + escapeHtml(inputOrder.join(" → ") || "tbox → abox → reasoningCards") + '</span><em>상태 계약: 확인 단계 · 자료 상태 · 변화 · 근거 역할</em></section>',
    '<section><strong>가드레일</strong><span>' + escapeHtml(guardrails.slice(0, 2).join(" / ") || "제공된 관계 데이터만 사용") + '</span><em>AI가 없는 값은 추정하지 않음</em></section>',
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentReasoningCardPanel(snapshot, options) {
  options = options || {};
  var cards = investmentReasoningCards(snapshot);
  var visible = options.compact ? cards.slice(0, 3) : cards;
  return [
    '<article class="panel investment-evidence-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Reasoning Cards</p>',
    '<h2>투자 근거 카드</h2>',
    '<p class="subtle">각 카드는 전략 근거, 관계 근거, 그래프 참조, AI 질문을 같은 단위로 묶습니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(cards.length) + '</span>',
    '</div>',
    '<div class="investment-evidence-list">',
    visible.length ? visible.map(renderInvestmentReasoningCard).join("") : renderEmptyState({
      tone: "muted",
      label: "Reasoning",
      title: "연결된 투자 근거 카드가 없습니다",
      description: "계좌 스냅샷, 시세, 뉴스·공시 근거가 수집되면 전략 근거와 관계 근거를 묶어 표시합니다.",
      meta: ["TBox/ABox", "Evidence", "AI opinion"]
    }),
    '</div>',
    '</article>'
  ].join("");
}

function activeInvestmentReasoningCard(cards) {
  cards = Array.isArray(cards) ? cards : [];
  var target = String(decisionsState.activeInvestmentEvidenceKey || "");
  var selected = null;
  cards.forEach(function (card, index) {
    if (selected) return;
    var key = investmentReasoningCardKey(card, index);
    if (target && key === target) selected = { card: card, index: index, key: key };
  });
  if (selected) return selected;
  if (!cards.length) return null;
  return { card: cards[0], index: 0, key: investmentReasoningCardKey(cards[0], 0) };
}

function renderInvestmentEvidenceWorkbenchPanel(snapshot) {
  var cards = investmentReasoningCards(snapshot);
  var active = activeInvestmentReasoningCard(cards);
  var actualCards = cards.filter(function (card) {
    var quality = String(card.dataQuality || card.quality || card.sourceQuality || "").toLowerCase();
    return quality && quality !== "mock" && quality !== "demo" && quality !== "missing";
  }).length;
  var gapCount = cards.reduce(function (count, card) {
    return count + (Array.isArray(card.dataGaps) ? card.dataGaps.length : 0);
  }, 0);
  return [
    '<article class="panel investment-evidence-workbench-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Evidence Workbench</p>',
    '<h2>투자 근거</h2>',
    '<p class="subtle">종목별 근거를 하나씩 선택해 전략 근거, 관계 근거, 데이터 출처를 분리해서 봅니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(cards.length) + '</span>',
    '</div>',
    '<div class="investment-evidence-status-grid">',
    renderInvestmentTodayStatusCell("근거 카드", cards.length, "선택형 검토", cards.length ? "watch" : "hold"),
    renderInvestmentTodayStatusCell("실제 데이터", actualCards || "-", "mock 제외 품질", actualCards ? "watch" : "caution"),
    renderInvestmentTodayStatusCell("데이터 공백", gapCount, "출처·신선도 확인", gapCount ? "caution" : "watch"),
    '</div>',
    '<div class="investment-evidence-workbench">',
    renderInvestmentEvidenceQueue(cards, active ? active.key : ""),
    renderInvestmentEvidenceSelectedPanel(active),
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentEvidenceQueue(cards, activeKey) {
  cards = Array.isArray(cards) ? cards : [];
  return [
    '<section class="investment-evidence-queue" aria-label="투자 근거 카드 목록">',
    cards.length ? cards.map(function (card, index) {
      return renderInvestmentEvidenceQueueCard(card, index, activeKey);
    }).join("") : renderEmptyState({
      tone: "muted",
      label: "Evidence",
      title: "선택할 투자 근거가 없습니다",
      description: "보유·관심 종목의 그래프 근거가 생기면 여기에서 선택해 볼 수 있습니다.",
      meta: ["근거", "관계", "출처"]
    }),
    '</section>'
  ].join("");
}

function renderInvestmentEvidenceQueueCard(card, index, activeKey) {
  card = card || {};
  var finalOpinion = card.finalOpinion || {};
  var gaps = Array.isArray(card.dataGaps) ? card.dataGaps : [];
  var key = investmentReasoningCardKey(card, index);
  var displayName = card.companyName || card.displayName || stockDisplayName(card.symbol);
  var tone = finalOpinion.tone || (gaps.length ? "caution" : "watch");
  return [
    '<button type="button" class="investment-evidence-queue-card ' + escapeHtml(activeKey === key ? "active" : "") + '" data-investment-evidence-select="' + escapeHtml(key) + '">',
    '<span class="tone-chip ' + escapeHtml(tone || "hold") + '">' + escapeHtml(finalOpinion.action || card.status || "대기") + '</span>',
    '<strong>' + escapeHtml(displayName || card.symbol || "투자 근거") + '</strong>',
    '<em>' + escapeHtml([card.symbol, card.portfolioRelation, sourceLabel(card.source || "")].filter(Boolean).join(" · ") || "관계 근거") + '</em>',
    gaps.length ? '<i>' + escapeHtml(gaps.length + "개 데이터 공백") + '</i>' : '',
    '</button>'
  ].join("");
}

function reasoningInfluenceText(item) {
  item = item || {};
  var role = item.evidenceRole || item.evidence_role || item.role || item.polarity || "context";
  var roleMeta = decisionStateMeta("evidence", role, "context");
  return [item.label || item.type, item.scope, roleMeta.label].filter(Boolean).join(" · ");
}

function renderInvestmentEvidenceSelectedPanel(active) {
  if (!active || !active.card) {
    return [
      '<section class="investment-evidence-selected-panel">',
      renderEmptyState({ tone: "muted", label: "Selected", title: "근거 카드를 선택하세요", description: "선택한 종목의 판단 요약과 출처를 압축해서 보여줍니다.", meta: ["요약", "근거", "출처"] }),
      '</section>'
    ].join("");
  }
  var card = active.card || {};
  var finalOpinion = card.finalOpinion || {};
  var relationRows = Array.isArray(card.relationEvidence) ? card.relationEvidence : [];
  var influenceRows = Array.isArray(card.relationInfluences) ? card.relationInfluences : [];
  var evidenceRows = Array.isArray(card.strategyEvidence) ? card.strategyEvidence : [];
  var planRows = Array.isArray(card.executionPlans) ? card.executionPlans : [];
  var gaps = Array.isArray(card.dataGaps) ? card.dataGaps : [];
  var displayName = card.companyName || card.displayName || stockDisplayName(card.symbol);
  var tone = finalOpinion.tone || (gaps.length ? "caution" : "watch");
  var review = decisionStateMeta("review", finalOpinion.reviewLevel || finalOpinion.review_level, gaps.length ? "blocked" : "observe");
  var validation = decisionStateMeta("validation", finalOpinion.validationState || finalOpinion.validation_state, gaps.length ? "blocked" : "conditional");
  var thesis = textWithKnownDisplaySymbols(beginnerFriendlyText(finalOpinion.thesis || finalOpinion.action || ""), card.symbol, { symbol: card.symbol, name: displayName });
  return [
    '<section class="investment-evidence-selected-panel">',
    '<div class="investment-evidence-selected-head">',
    '<div>',
    '<span class="tone-chip ' + escapeHtml(tone || "hold") + '">' + escapeHtml(finalOpinion.action || "관계 의견 대기") + '</span>',
    '<strong>' + escapeHtml(displayName || card.symbol || "투자 근거") + '</strong>',
    '<em>' + escapeHtml([card.symbol, card.portfolioRelation, card.status].filter(Boolean).join(" · ")) + '</em>',
    '</div>',
    renderWorkDetailButton("investment-reasoning-card", active.key, "상세", "mini-button"),
    '</div>',
    '<div class="investment-evidence-focus-grid">',
    renderInvestmentTodayStatusCell("확인 단계", review.label, "InferenceBox", review.tone),
    renderInvestmentTodayStatusCell("AI 검증", validation.label, "검증 상태", validation.tone),
    renderInvestmentTodayStatusCell("전략 근거", evidenceRows.length, "가격·수급·추세", evidenceRows.length ? "watch" : "hold"),
    renderInvestmentTodayStatusCell("관계 근거", relationRows.length, "TBox/ABox 연결", relationRows.length ? "watch" : "hold"),
    '</div>',
    '<div class="investment-evidence-selected-copy">',
    '<strong>판단 요약</strong>',
    '<p>' + escapeHtml(thesis || "판단 문장이 아직 없습니다.") + '</p>',
    gaps.length ? '<p>데이터 공백: ' + escapeHtml(gaps.join(", ")) + '</p>' : '',
    '</div>',
    '<div class="investment-evidence-selected-columns">',
    renderReasoningCardList("전략 근거", evidenceRows.slice(0, 5).map(function (item) { return item.summary || item.kind || item.id; })),
    renderReasoningCardList("관계 근거", relationRows.slice(0, 5).map(function (item) {
      return [item.type, item.sourceLabel, item.targetLabel].filter(Boolean).join(" · ");
    })),
    renderReasoningCardList("의견 영향", influenceRows.slice(0, 5).map(function (item) {
      return reasoningInfluenceText(item);
    })),
    '</div>',
    renderReasoningExecutionPlan(planRows[0]),
    renderReasoningGraphRefs(card),
    '</section>'
  ].join("");
}

function investmentReasoningCardKey(card, index) {
  card = card || {};
  return String(card.id || card.reasoningCardId || card.cardId || [
    "reasoning",
    card.symbol || "",
    card.portfolioRelation || "",
    card.status || "",
    index == null ? "" : index
  ].filter(Boolean).join(":"));
}

function investmentReasoningCardByKey(key) {
  var target = String(key || "");
  var cards = investmentReasoningCards(shellState.snapshot || {});
  for (var i = 0; i < cards.length; i += 1) {
    if (investmentReasoningCardKey(cards[i], i) === target) return cards[i];
  }
  return null;
}

function investmentReasoningCardWorkDetailPayload(key) {
  var card = investmentReasoningCardByKey(key);
  if (!card) return null;
  var finalOpinion = card.finalOpinion || {};
  var relationRows = Array.isArray(card.relationEvidence) ? card.relationEvidence : [];
  var influenceRows = Array.isArray(card.relationInfluences) ? card.relationInfluences : [];
  var evidenceRows = Array.isArray(card.strategyEvidence) ? card.strategyEvidence : [];
  var planRows = Array.isArray(card.executionPlans) ? card.executionPlans : [];
  var gaps = Array.isArray(card.dataGaps) ? card.dataGaps : [];
  var displayName = card.companyName || card.displayName || stockDisplayName(card.symbol);
  var tone = finalOpinion.tone || (gaps.length ? "hold" : "watch");
  var review = decisionStateMeta("review", finalOpinion.reviewLevel || finalOpinion.review_level, gaps.length ? "blocked" : "observe");
  var validation = decisionStateMeta("validation", finalOpinion.validationState || finalOpinion.validation_state, gaps.length ? "blocked" : "conditional");
  var thesis = textWithKnownDisplaySymbols(beginnerFriendlyText(finalOpinion.thesis || finalOpinion.action || ""), card.symbol, { symbol: card.symbol, name: displayName });
  return {
    kicker: "Reasoning Card",
    title: displayName + " 투자 근거",
    meta: [card.portfolioRelation || "", sourceLabel(card.source || ""), card.status || ""].filter(Boolean).join(" · "),
    body: [
      '<section class="work-detail-section primary">',
      '<strong>판단 요약</strong>',
      '<p>' + escapeHtml(thesis || "판단 문장이 아직 없습니다.") + '</p>',
      gaps.length ? '<p>데이터 공백: ' + escapeHtml(gaps.join(", ")) + '</p>' : '',
      '</section>',
      '<div class="work-detail-metric-row">',
      renderNotificationDetailMetric("확인 단계", review.label, review.tone),
      renderNotificationDetailMetric("AI 검증", validation.label, validation.tone),
      renderNotificationDetailMetric("전략 근거", evidenceRows.length, "hold"),
      renderNotificationDetailMetric("관계 근거", relationRows.length, "hold"),
      renderNotificationDetailMetric("관계 영향", influenceRows.length, "hold"),
      renderNotificationDetailMetric("실행 계획", planRows.length, "hold"),
      '</div>',
      '<section class="work-detail-section"><strong>전략 근거</strong>' + renderReasoningCardList("전략 근거", evidenceRows.map(function (item) { return item.summary || item.kind || item.id; })) + '</section>',
      '<section class="work-detail-section"><strong>의견 영향</strong>' + renderReasoningCardList("의견 영향", influenceRows.map(function (item) {
        return reasoningInfluenceText(item);
      })) + '</section>',
      '<section class="work-detail-section"><strong>관계 근거</strong>' + renderReasoningCardList("관계 근거", relationRows.map(function (item) {
        return [item.type, item.sourceLabel, item.targetLabel].filter(Boolean).join(" · ");
      })) + '</section>',
      planRows.length ? '<section class="work-detail-section"><strong>실행 계획</strong>' + planRows.map(renderReasoningExecutionPlan).join("") + '</section>' : '',
      '<section class="work-detail-section"><strong>그래프 참조</strong>' + renderReasoningGraphRefs(card) + '</section>'
    ].join("")
  };
}

function renderInvestmentReasoningCard(card, index) {
  var finalOpinion = card.finalOpinion || {};
  var relationRows = Array.isArray(card.relationEvidence) ? card.relationEvidence : [];
  var influenceRows = Array.isArray(card.relationInfluences) ? card.relationInfluences : [];
  var evidenceRows = Array.isArray(card.strategyEvidence) ? card.strategyEvidence : [];
  var planRows = Array.isArray(card.executionPlans) ? card.executionPlans : [];
  var gaps = Array.isArray(card.dataGaps) ? card.dataGaps : [];
  var displayName = card.companyName || card.displayName || stockDisplayName(card.symbol);
  var tone = finalOpinion.tone || (gaps.length ? "hold" : "watch");
  var review = decisionStateMeta("review", finalOpinion.reviewLevel || finalOpinion.review_level, gaps.length ? "blocked" : "observe");
  var validation = decisionStateMeta("validation", finalOpinion.validationState || finalOpinion.validation_state, gaps.length ? "blocked" : "conditional");
  var thesis = textWithKnownDisplaySymbols(beginnerFriendlyText(finalOpinion.thesis || finalOpinion.action || ""), card.symbol, { symbol: card.symbol, name: displayName });
  var key = investmentReasoningCardKey(card, index);
  return [
    '<div class="investment-evidence-card"' + cardTypeAttrs("evidence-card", tone || "hold") + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<div class="investment-evidence-head">',
    '<div>',
    '<strong>' + escapeHtml(displayName) + '</strong>',
    '<span>' + escapeHtml([card.portfolioRelation || "-", sourceLabel(card.source || ""), card.status || ""].filter(Boolean).join(" · ")) + '</span>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(tone || "hold") + '">' + escapeHtml(finalOpinion.action || "관계 의견 대기") + '</span>',
    '</div>',
    '<div class="investment-evidence-grid">',
    '<span>확인 단계 <strong>' + escapeHtml(review.label) + '</strong></span>',
    '<span>AI 검증 <strong>' + escapeHtml(validation.label) + '</strong></span>',
    '<span>전략 근거 <strong>' + escapeHtml(evidenceRows.length) + '</strong></span>',
    '<span>관계 근거 <strong>' + escapeHtml(relationRows.length) + '</strong></span>',
    '<span>관계 영향 <strong>' + escapeHtml(influenceRows.length) + '</strong></span>',
    '<span>실행 계획 <strong>' + escapeHtml(planRows.length) + '</strong></span>',
    '</div>',
    '<div class="investment-evidence-narrative">',
    thesis ? '<p>' + escapeHtml(thesis) + '</p>' : '',
    gaps.length ? '<p>데이터 공백: ' + escapeHtml(gaps.join(", ")) + '</p>' : '',
    '</div>',
    '<div class="investment-evidence-columns">',
    renderReasoningCardList("전략 근거", evidenceRows.slice(0, 3).map(function (item) { return item.summary || item.kind || item.id; })),
    renderReasoningCardList("의견 영향", influenceRows.slice(0, 3).map(function (item) {
      return reasoningInfluenceText(item);
    })),
    renderReasoningCardList("관계 근거", relationRows.slice(0, 3).map(function (item) {
      return [item.type, item.sourceLabel, item.targetLabel].filter(Boolean).join(" · ");
    })),
    '</div>',
    renderReasoningExecutionPlan(planRows[0]),
    renderReasoningGraphRefs(card),
    '<div class="summary-first-actions">',
    renderWorkDetailButton("investment-reasoning-card", key, "상세", "mini-button"),
    '</div>',
    '</div>'
  ].join("");
}

function compactPlanList(value, limit) {
  if (!Array.isArray(value)) return [];
  return value.map(function (item) { return String(item || "").trim(); }).filter(Boolean).slice(0, limit || 4);
}

function renderReasoningExecutionPlan(plan) {
  if (!plan || typeof plan !== "object") return "";
  var blocked = compactPlanList(plan.blockedActions, 3);
  var strengthen = compactPlanList(plan.strengthenConditions, 3);
  var weaken = compactPlanList(plan.weakenConditions, 3);
  var nextChecks = compactPlanList(plan.nextChecks, 4);
  var addBuy = renderAddBuyAssessment(plan.addBuyAssessment);
  var primary = plan.primaryActionLabel || plan.primaryAction || "실행 판단 대기";
  var meta = [plan.decisionStage, plan.actionGroup, plan.actionLevel].filter(Boolean).join(" · ");
  return [
    '<div class="reasoning-execution-plan">',
    '<div class="reasoning-execution-head">',
    '<strong>' + escapeHtml(primary) + '</strong>',
    '<span>' + escapeHtml(meta || "실행 조건 확인") + '</span>',
    '</div>',
    addBuy,
    '<div class="reasoning-execution-grid">',
    renderReasoningCardList("다음 확인", nextChecks),
    renderReasoningCardList("보류 조건", blocked),
    renderReasoningCardList("강화 조건", strengthen),
    renderReasoningCardList("약화 조건", weaken),
    '</div>',
    '</div>'
  ].join("");
}

function renderAddBuyAssessment(assessment) {
  if (!assessment || typeof assessment !== "object") return "";
  var stage = String(assessment.stage || "").trim();
  if (!stage || stage === "NONE") return "";
  var blockers = compactPlanList(assessment.blockedReasons, 2);
  var opened = compactPlanList(assessment.openedReasons, 2);
  var detail = [
    assessment.statusText || assessment.label || "",
    opened.length ? "확인 " + opened.join(" · ") : "",
    blockers.length ? "보류 " + blockers.join(" · ") : ""
  ].filter(Boolean).join(" / ");
  return [
    '<div class="reasoning-execution-addbuy">',
    '<strong>추가매수 판단</strong>',
    '<span>' + escapeHtml([assessment.label, assessment.investmentProfile].filter(Boolean).join(" · ") || stage) + '</span>',
    '<em>' + escapeHtml(detail) + '</em>',
    '</div>'
  ].join("");
}

function renderReasoningCardList(title, rows) {
  return [
    '<div class="reasoning-card-list">',
    '<strong>' + escapeHtml(title) + '</strong>',
    rows.length ? rows.map(function (row) { return '<span>' + escapeHtml(row) + '</span>'; }).join("") : '<span>연결된 항목 없음</span>',
    '</div>'
  ].join("");
}

function renderReasoningGraphRefs(card) {
  var context = card.graphContext || {};
  var tboxClasses = Array.isArray(context.tboxClasses) ? context.tboxClasses : [];
  return [
    '<div class="reasoning-graph-refs">',
    '<span>Graph ref <strong>' + escapeHtml(context.stockEntityId || ("stock:" + (card.symbol || ""))) + '</strong></span>',
    '<span>TBox <strong>' + escapeHtml(tboxClasses.slice(0, 4).join(", ") || "-") + '</strong></span>',
    '<span>AI 질문 <strong>' + escapeHtml(((card.aiInference || {}).role) || "ontology-first-investment-opinion") + '</strong></span>',
    '</div>'
  ].join("");
}

export { compactPlanList, investmentReasoningCardKey, investmentReasoningCardWorkDetailPayload, renderAddBuyAssessment, renderInvestmentAiPacketPanel, renderInvestmentDataLineagePanel, renderInvestmentEvidenceWorkbenchPanel, renderInvestmentMoneyFlowPanel, renderReasoningCardList };
