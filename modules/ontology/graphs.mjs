import { investmentReasoningCardKey } from "../decisions/evidence.mjs";
import { investmentActionLinkedAlert, investmentActionNextWindow, investmentActionPlaybook, investmentAnalysisModel, investmentReasoningCards } from "../decisions/strategy.mjs";
import { stockDisplayName, textWithKnownDisplaySymbols } from "../instruments/catalog.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { viewLifetime } from "../navigation/lifecycle.mjs";
import { normalizeOntologyGraphId, normalizeOntologyWorldDepth, normalizeOntologyWorldLens, ontologyGraphDisplayMeta } from "../navigation/routes.mjs";
import { ontologyGraphInstancesCell } from "./runtime.mjs";
import { ontologyStrategyParts } from "./strategy.mjs";
import { ontologyInferenceRelationTypes, ontologyReadableRuleRows, ontologyRelationPriority, ontologyResolveWorldFocusId, ontologyWorldFocusItems, renderOntologyWorldNodeInspector } from "./world.mjs";
import { strategyProposalArray, strategyProposalItems, strategyProposalPerformanceSummary, strategyProposalSignedPercent } from "../proposals/workspace.mjs";
import { render } from "../render/scheduler.mjs";
import { sourceLabel } from "../shared/format.mjs";
import { beginnerFriendlyText, escapeHtml } from "../shared/text.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";
import { app } from "../shell/root.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { proposalsState } from "../state/proposals.mjs";
import { shellState } from "../state/shell.mjs";

var cytoscapeLoadPromise = null;

function renderOntologyGraphExpandedOverlay() {
  var graphId = normalizeOntologyGraphId(ontologyState.expandedOntologyGraphId);
  if (!graphId) return "";
  var meta = ontologyGraphDisplayMeta(graphId);
  var expandedGraphId = graphId + "-expanded";
  return [
    '<div class="ontology-graph-expanded-backdrop" data-ontology-graph-close>',
    '<section class="ontology-graph-expanded-dialog" role="dialog" aria-modal="true" aria-label="' + escapeHtml(meta.title) + '">',
    '<header class="ontology-graph-expanded-head">',
    '<div>',
    '<p class="label">' + escapeHtml(meta.eyebrow) + '</p>',
    '<h2>' + escapeHtml(meta.title) + '</h2>',
    '<span>' + escapeHtml(meta.description) + '</span>',
    '</div>',
    '<div class="ontology-graph-expanded-toolbar">',
    '<button class="icon-button" type="button" data-ontology-graph-fit="' + escapeHtml(expandedGraphId) + '" title="' + escapeHtml(meta.fitLabel) + '" aria-label="' + escapeHtml(meta.fitLabel) + '">⌖</button>',
    '<button class="icon-button" type="button" data-ontology-graph-layout="' + escapeHtml(expandedGraphId) + '" title="' + escapeHtml(meta.layoutLabel) + '" aria-label="' + escapeHtml(meta.layoutLabel) + '">↺</button>',
    '<button class="icon-button danger" type="button" data-ontology-graph-close="" title="큰 화면 닫기" aria-label="큰 화면 닫기">×</button>',
    '</div>',
    '</header>',
    '<div class="ontology-cytoscape ontology-cytoscape-expanded" data-ontology-cytoscape="' + escapeHtml(expandedGraphId) + '"><span>그래프 엔진 초기화 중</span></div>',
    '<footer class="ontology-graph-expanded-footer">',
    '<span>표시 기준: 핵심 관계 압축 · 원본과 동일한 그래프 데이터</span>',
    '</footer>',
    '</section>',
    '</div>'
  ].join("");
}

function ontologyOpinionOf(item) {
  return item && item.ontologyOpinion ? item.ontologyOpinion : {};
}

function ontologyTypeOf(relation) {
  return String(relation && (relation.type || relation.relation_type || relation.relationType) || "").toUpperCase();
}

function ontologyBoxOf(item) {
  var properties = item && item.properties ? item.properties : {};
  return String(
    (item && (item.ontologyBox || item.box)) ||
    properties.ontologyBox ||
    properties.box ||
    ""
  ).toUpperCase();
}

function ontologyIsTboxItem(item) {
  var kind = String(item && item.kind || "");
  return ontologyBoxOf(item) === "TBOX" || kind.indexOf("tbox-") === 0;
}

function ontologyEntityLabelMap(entities) {
  return (entities || []).reduce(function (labels, entity) {
    var id = String(entity && entity.id || "");
    if (id) labels[id] = ontologyEntityDisplayLabel(entity, id);
    return labels;
  }, {});
}

function ontologyEndpointLabel(id, labels) {
  var key = String(id || "");
  if (labels && labels[key]) return labels[key];
  var symbol = ontologyAboxSymbolFromId(key);
  if (symbol) return stockDisplayName(symbol);
  return key || "-";
}

function ontologyEntityDisplayLabel(entity, fallbackId) {
  entity = entity || {};
  var properties = entity.properties || {};
  var id = String(entity.id || fallbackId || "");
  var symbol = String(properties.symbol || ontologyAboxSymbolFromId(id) || "").trim().toUpperCase();
  if (symbol) {
    var label = String(entity.label || properties.name || properties.displayName || "").trim();
    var item = Object.assign({}, properties, { symbol: symbol });
    if (label && label.toUpperCase() !== symbol) item.name = label;
    return stockDisplayName(symbol, item);
  }
  return entity.label || properties.name || id || "-";
}

function ontologyRelationCounts(relations) {
  return (relations || []).reduce(function (counts, relation) {
    var type = ontologyTypeOf(relation) || "RELATED_TO";
    counts[type] = (counts[type] || 0) + 1;
    return counts;
  }, {});
}

function ontologyEntityCounts(entities) {
  return (entities || []).reduce(function (counts, entity) {
    var kind = String(entity && entity.kind || "entity");
    counts[kind] = (counts[kind] || 0) + 1;
    return counts;
  }, {});
}

function ontologyTopEntries(counts, limit) {
  return Object.keys(counts || {}).map(function (key) {
    return { key: key, value: counts[key] };
  }).sort(function (a, b) {
    if (b.value !== a.value) return b.value - a.value;
    return a.key.localeCompare(b.key);
  }).slice(0, limit || 8);
}

function ontologyAboxEntities(entities) {
  return (entities || []).filter(function (item) { return !ontologyIsTboxItem(item); });
}

function ontologyAboxRelations(relations) {
  return (relations || []).filter(function (item) { return !ontologyIsTboxItem(item); });
}

function ontologyEvidenceCount(evidence, kind) {
  return (evidence || []).filter(function (item) {
    return String(item && item.kind || "") === kind;
  }).length;
}

function ontologyBeliefCount(beliefs, polarity) {
  return (beliefs || []).filter(function (item) {
    return String(item && item.polarity || "") === polarity;
  }).length;
}

function ontologyContradictionCount(opinions) {
  return (opinions || []).reduce(function (count, opinion) {
    return count + (Array.isArray(opinion && opinion.contradictions) ? opinion.contradictions.length : 0);
  }, 0);
}

function ontologyRuleTrace(rule, index, relationCounts, evidence, beliefs, opinions) {
  var fallback = {
    input: "현재 데이터 행",
    relation: "규칙 구조 조건",
    output: "계산된 판단 근거와 AI 의견 행",
    rows: 0
  };
  if (index === 0) {
    return {
      input: "HOLDS + EXPOSED_TO",
      relation: "계좌와 업종 연결",
      output: "위험 판단 근거 " + ontologyBeliefCount(beliefs, "risk") + "개",
      rows: Number(relationCounts.HOLDS || 0) + Number(relationCounts.EXPOSED_TO || 0)
    };
  }
  if (index === 1) {
    return {
      input: "추세 + 수급 근거",
      relation: "종목별 연결",
      output: "긍정/위험 판단 근거 " + (ontologyBeliefCount(beliefs, "support") + ontologyBeliefCount(beliefs, "risk")) + "개",
      rows: ontologyEvidenceCount(evidence, "trend") + ontologyEvidenceCount(evidence, "flow")
    };
  }
  if (index === 2) {
    return {
      input: "관계 상태 + 시장 근거",
      relation: "USES_EVIDENCE_FROM",
      output: "반대 신호 " + ontologyContradictionCount(opinions) + "개",
      rows: Number(relationCounts.USES_EVIDENCE_FROM || 0) + ontologyEvidenceCount(evidence, "relation-rule")
    };
  }
  if (index === 3) {
    return {
      input: "데이터 품질 근거",
      relation: "신뢰도 확인",
      output: "AI 의견 신뢰도 " + (opinions || []).length + "개",
      rows: ontologyEvidenceCount(evidence, "data-quality")
    };
  }
  if (index === 4) {
    return {
      input: "관계 규칙 근거",
      relation: "최종 상태 근거로 사용",
      output: "AI 의견 " + (opinions || []).length + "개",
      rows: Number(relationCounts.USES_EVIDENCE_FROM || 0)
    };
  }
  fallback.output = rule || fallback.output;
  return fallback;
}

function ontologyActionRowsBySymbol(snapshot) {
  var rows = Array.isArray(investmentAnalysisModel(snapshot || {}).actionQueue) ? investmentAnalysisModel(snapshot || {}).actionQueue : [];
  return rows.reduce(function (memo, row) {
    var symbol = String(row && row.symbol || "").toUpperCase();
    if (symbol && !memo[symbol]) memo[symbol] = row;
    return memo;
  }, {});
}

function ontologyProposalPerformanceForSymbol(symbol) {
  var target = String(symbol || "").toUpperCase();
  if (!target) return { label: "성과 대기", detail: "종목 연결 없음", tone: "hold", sampleCount: 0 };
  var proposals = strategyProposalItems().filter(function (proposal) {
    return strategyProposalArray(proposal.symbols).map(function (item) {
      return String(item || "").toUpperCase();
    }).indexOf(target) >= 0;
  });
  var sampleCount = 0;
  var excessSum = 0;
  var excessWeight = 0;
  proposals.forEach(function (proposal) {
    var summary = strategyProposalPerformanceSummary(proposal);
    var count = Number(summary.sampleCount || 0);
    sampleCount += count;
    if (count && summary.avgExcessReturnPct != null) {
      excessSum += Number(summary.avgExcessReturnPct || 0) * count;
      excessWeight += count;
    }
  });
  if (!proposals.length) return { label: "성과 대기", detail: proposalsState.strategyProposalsLoaded ? "연결된 전략 제안 없음" : "전략 성과 로딩 전", tone: "hold", sampleCount: 0 };
  if (!sampleCount) return { label: proposals.length + "개 전략", detail: "성과 표본 미기록", tone: "caution", sampleCount: 0 };
  var avg = excessWeight ? excessSum / excessWeight : 0;
  return {
    label: strategyProposalSignedPercent(avg),
    detail: sampleCount + "개 표본 · " + proposals.length + "개 전략",
    tone: avg >= 0 ? "watch" : "danger",
    sampleCount: sampleCount
  };
}

function ontologyChainRelationSummary(card) {
  card = card || {};
  var relations = Array.isArray(card.relationEvidence) ? card.relationEvidence : [];
  var influences = Array.isArray(card.relationInfluences) ? card.relationInfluences : [];
  if (relations.length) {
    return {
      label: relations.length + "개 관계",
      detail: relations.slice(0, 2).map(function (item) {
        return [item.type, item.sourceLabel, item.targetLabel].filter(Boolean).join(" ");
      }).filter(Boolean).join(" · ") || "관계 근거",
      count: relations.length
    };
  }
  if (influences.length) {
    return {
      label: influences.length + "개 영향",
      detail: influences.slice(0, 2).map(function (item) { return item.label || item.type || ""; }).filter(Boolean).join(" · ") || "관계 영향",
      count: influences.length
    };
  }
  return { label: "관계 대기", detail: "관계 근거 없음", count: 0 };
}

function ontologyChainRuleSummary(card, parts) {
  var rows = ontologyReadableRuleRows(parts || {});
  var planRows = Array.isArray(card && card.executionPlans) ? card.executionPlans : [];
  var firstPlan = planRows[0] || {};
  var ruleLabel = firstPlan.ruleId || firstPlan.rule_id || (rows[0] && (rows[0].id || rows[0].label)) || "RuleBox";
  return {
    label: ontologyShortText(ruleLabel, 24),
    detail: rows.length ? rows.length + "개 RuleBox 후보 중 관련 규칙" : "TBox/RuleBox 조건 매칭",
    count: rows.length
  };
}

function ontologyChainInferenceSummary(card, actionRow) {
  var plans = Array.isArray(card && card.executionPlans) ? card.executionPlans : [];
  var finalOpinion = (card && card.finalOpinion) || {};
  var graph = (actionRow && actionRow.graph) || {};
  if (graph.blocked) return { label: "추론 보류", detail: graph.basis || "InferenceBox 확인", tone: "caution", count: 0 };
  if (plans.length) {
    var plan = plans[0] || {};
    return {
      label: plan.primaryActionLabel || plan.primaryAction || "InferenceBox",
      detail: [plan.decisionStage, plan.actionGroup, plan.actionLevel].filter(Boolean).join(" · ") || "실행 계획 생성",
      tone: "watch",
      count: plans.length
    };
  }
  return {
    label: finalOpinion.action || "추론 대기",
    detail: finalOpinion.thesis || "InferenceBox 출력 확인",
    tone: finalOpinion.action ? "watch" : "hold",
    count: finalOpinion.action ? 1 : 0
  };
}

function ontologyDecisionChainRows(parts, snapshot) {
  parts = parts || ontologyStrategyParts(snapshot || shellState.snapshot || {});
  snapshot = snapshot || shellState.snapshot || {};
  var actionBySymbol = ontologyActionRowsBySymbol(snapshot);
  var cards = investmentReasoningCards(snapshot);
  var rows = cards.map(function (card, index) {
    var symbol = String(card.symbol || "").toUpperCase();
    var actionRow = actionBySymbol[symbol] || {};
    var finalOpinion = card.finalOpinion || {};
    var relation = ontologyChainRelationSummary(card);
    var rule = ontologyChainRuleSummary(card, parts);
    var inference = ontologyChainInferenceSummary(card, actionRow);
    var performance = ontologyProposalPerformanceForSymbol(symbol);
    var displayName = card.companyName || card.displayName || stockDisplayName(symbol);
    var key = investmentReasoningCardKey(card, index);
    return {
      key: key,
      reasoningKey: key,
      symbol: symbol,
      displayName: displayName,
      dataLabel: [card.portfolioRelation || actionRow.source || "계좌 데이터", sourceLabel(card.source || actionRow.source || "")].filter(Boolean).join(" · "),
      dataDetail: "strategyEvidence " + ((card.strategyEvidence || []).length || 0) + "개 · graphContext " + (((card.graphContext || {}).relationIds || []).length || 0) + "개",
      relationLabel: relation.label,
      relationDetail: relation.detail,
      relationCount: relation.count,
      ruleLabel: rule.label,
      ruleDetail: rule.detail,
      ruleCount: rule.count,
      inferenceLabel: inference.label,
      inferenceDetail: inference.detail,
      inferenceTone: inference.tone,
      actionLabel: finalOpinion.action || actionRow.decision || "판단 대기",
      actionDetail: textWithKnownDisplaySymbols(beginnerFriendlyText(finalOpinion.thesis || (Array.isArray(actionRow.reasons) ? actionRow.reasons[0] : "") || ""), symbol, { symbol: symbol, name: displayName }),
      alertLabel: investmentActionLinkedAlert(actionRow),
      alertDetail: investmentActionNextWindow(actionRow),
      performanceLabel: performance.label,
      performanceDetail: performance.detail,
      performanceTone: performance.tone,
      tone: finalOpinion.tone || inference.tone || actionRow.tone || "hold"
    };
  });
  if (!rows.length) {
    var actionRows = Array.isArray(investmentAnalysisModel(snapshot).actionQueue) ? investmentAnalysisModel(snapshot).actionQueue : [];
    rows = actionRows.slice(0, 8).map(function (row, index) {
      var symbol = String(row.symbol || "").toUpperCase();
      var playbook = investmentActionPlaybook(row);
      var performance = ontologyProposalPerformanceForSymbol(symbol);
      return {
        key: "action-chain:" + symbol + ":" + index,
        reasoningKey: "",
        symbol: symbol,
        displayName: row.name || stockDisplayName(symbol),
        dataLabel: [row.dataQuality || "데이터", row.apiSource || row.source || ""].filter(Boolean).join(" · "),
        dataDetail: "액션 큐 fallback",
        relationLabel: (row.graph || {}).blocked ? "관계 차단" : "관계 확인",
        relationDetail: (row.graph || {}).reason || playbook.detail,
        relationCount: 0,
        ruleLabel: playbook.label,
        ruleDetail: playbook.detail,
        ruleCount: 0,
        inferenceLabel: (row.graph || {}).blocked ? "추론 보류" : "InferenceBox",
        inferenceDetail: (row.graph || {}).basis || "액션 후보 생성",
        inferenceTone: (row.graph || {}).blocked ? "caution" : "watch",
        actionLabel: row.decision || "판단 대기",
        actionDetail: Array.isArray(row.reasons) ? row.reasons[0] || "" : "",
        alertLabel: investmentActionLinkedAlert(row),
        alertDetail: investmentActionNextWindow(row),
        performanceLabel: performance.label,
        performanceDetail: performance.detail,
        performanceTone: performance.tone,
        tone: row.tone || "hold"
      };
    });
  }
  if (!ontologyState.activeOntologyChainKey && rows.length) ontologyState.activeOntologyChainKey = rows[0].key;
  if (rows.length && !rows.some(function (row) { return row.key === ontologyState.activeOntologyChainKey; })) ontologyState.activeOntologyChainKey = rows[0].key;
  return rows;
}

function ontologyDecisionChainActiveRow(rows) {
  rows = Array.isArray(rows) ? rows : [];
  return rows.filter(function (row) { return row.key === ontologyState.activeOntologyChainKey; })[0] || rows[0] || null;
}

function ontologyGraphSafeId(value) {
  return String(value || "").replace(/[^a-zA-Z0-9:_-]+/g, "_");
}

function ontologyBuildDecisionChainGraph(parts, snapshot) {
  var rows = ontologyDecisionChainRows(parts, snapshot).slice(0, 8);
  var active = ontologyDecisionChainActiveRow(rows);
  var nodesById = {};
  var edges = [];
  var stages = [
    { key: "data", label: "실계좌 데이터", kind: "source-data", x: 70 },
    { key: "relation", label: "관계 근거", kind: "relation-evidence", x: 245 },
    { key: "rule", label: "RuleBox", kind: "rulebox", x: 420 },
    { key: "inference", label: "InferenceBox", kind: "inferencebox", x: 595 },
    { key: "opinion", label: "투자 판단", kind: "investment-opinion", x: 770 },
    { key: "alert", label: "알림", kind: "notification-intent", x: 945 },
    { key: "performance", label: "성과", kind: "performance-feedback", x: 1120 }
  ];
  rows.forEach(function (row, rowIndex) {
    var y = 80 + rowIndex * 132;
    stages.forEach(function (stage, stageIndex) {
      var id = "chain:" + ontologyGraphSafeId(row.key) + ":" + stage.key;
      var value = row[stage.key + "Label"] || stage.label;
      if (stage.key === "opinion") value = row.actionLabel;
      nodesById[id] = {
        id: id,
        label: value || stage.label,
        kind: stage.kind,
        title: [row.displayName, stage.label, row[stage.key + "Detail"] || row.actionDetail || ""].filter(Boolean).join(" · "),
        chainKey: row.key,
        active: active && active.key === row.key,
        x: stage.x,
        y: y
      };
      if (stageIndex > 0) {
        edges.push({
          source: "chain:" + ontologyGraphSafeId(row.key) + ":" + stages[stageIndex - 1].key,
          target: id,
          type: stages[stageIndex - 1].label + "→" + stage.label,
          kind: stage.key === "rule" || stage.key === "inference" ? "rule" : "chain",
          chainKey: row.key
        });
      }
    });
  });
  return { nodesById: nodesById, edges: edges, rows: rows, active: active };
}

function renderOntologyDecisionChainGraph(parts, snapshot) {
  var graph = ontologyBuildDecisionChainGraph(parts, snapshot);
  var rows = graph.rows || [];
  var active = graph.active;
  var activeIndex = active ? rows.indexOf(active) + 1 : 0;
  return [
    '<section class="ontology-graph-panel ontology-decision-chain-graph">',
    '<div class="ontology-surface-head">',
    '<div>',
    '<strong>판단 근거 체인 그래프</strong>',
    '<span>종목을 선택하면 데이터 → 관계 → RuleBox → InferenceBox → 투자 판단 → 알림 → 성과 흐름을 한 줄로 추적합니다.</span>',
    '</div>',
    '<div class="ontology-graph-actions">',
    '<button class="icon-button" type="button" data-ontology-graph-expand="decision-chain" title="판단 근거 체인 큰 화면으로 보기" aria-label="판단 근거 체인 큰 화면으로 보기">⤢</button>',
    '<button class="icon-button" type="button" data-ontology-graph-fit="decision-chain" title="판단 근거 체인 맞춤" aria-label="판단 근거 체인 맞춤">⌖</button>',
    '<button class="icon-button" type="button" data-ontology-graph-layout="decision-chain" title="판단 근거 체인 자동 배치" aria-label="판단 근거 체인 자동 배치">↺</button>',
    '</div>',
    '</div>',
    '<div class="ontology-graph-meta">',
    '<span>표시 ' + escapeHtml(Object.keys(graph.nodesById || {}).length) + ' 노드 · ' + escapeHtml((graph.edges || []).length) + ' 관계</span>',
    '<span>선택 ' + escapeHtml(activeIndex || "-") + ' / ' + escapeHtml(rows.length || 0) + ' 체인</span>',
    '<span>Reasoning Card · RuleBox · InferenceBox · 알림 · 성과</span>',
    '</div>',
    rows.length ? '<div class="ontology-chain-selector" aria-label="판단 근거 체인 선택">' + rows.map(function (row) {
      return [
        '<button class="' + escapeHtml(row.key === (active && active.key) ? "active" : "") + '" type="button" data-ontology-chain-select="' + escapeHtml(row.key) + '">',
        '<strong>' + escapeHtml(row.displayName || row.symbol || "판단 체인") + '</strong>',
        '<span>' + escapeHtml([row.actionLabel, row.inferenceLabel, row.alertLabel].filter(Boolean).join(" · ")) + '</span>',
        '</button>'
      ].join("");
    }).join("") + '</div>' : '',
    '<div class="ontology-cytoscape ontology-decision-chain-cytoscape" data-ontology-cytoscape="decision-chain"><span>그래프 엔진 초기화 중</span></div>',
    renderOntologyDecisionChainDetail(active),
    '<div class="ontology-graph-caption">',
    '<span>노드 또는 종목 버튼을 선택하면 아래 상세 체인이 갱신됩니다.</span>',
    '<span>성과는 전략 제안 표본이 연결된 경우 초과 수익 기준으로 표시합니다.</span>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyDecisionChainDetail(row) {
  if (!row) {
    return '<div class="ontology-chain-detail"><div class="ontology-empty">표시할 판단 근거 체인이 없습니다.</div></div>';
  }
  var stages = [
    ["실계좌 데이터", row.dataLabel, row.dataDetail, "source-data"],
    ["관계 근거", row.relationLabel, row.relationDetail, "relation-evidence"],
    ["RuleBox", row.ruleLabel, row.ruleDetail, "rulebox"],
    ["InferenceBox", row.inferenceLabel, row.inferenceDetail, row.inferenceTone || "inferencebox"],
    ["투자 판단", row.actionLabel, row.actionDetail, row.tone || "investment-opinion"],
    ["연결 알림", row.alertLabel, row.alertDetail, "notification-intent"],
    ["성과 피드백", row.performanceLabel, row.performanceDetail, row.performanceTone || "performance-feedback"]
  ];
  return [
    '<div class="ontology-chain-detail">',
    '<div class="ontology-chain-detail-head">',
    '<div><strong>' + escapeHtml(row.displayName || row.symbol || "판단 체인") + '</strong><span>' + escapeHtml([row.symbol, row.dataLabel].filter(Boolean).join(" · ")) + '</span></div>',
    row.reasoningKey ? renderWorkDetailButton("investment-reasoning-card", row.reasoningKey, "근거 카드", "mini-button") : '',
    '</div>',
    '<div class="ontology-chain-stage-list">',
    stages.map(function (stage, index) {
      return [
        '<section class="ontology-chain-stage ' + escapeHtml(stage[3] || "hold") + '"' + cardTypeAttrs("process-card", stage[3] || "hold") + '>',
        '<b>' + escapeHtml(String(index + 1).padStart(2, "0")) + '</b>',
        '<div><strong>' + escapeHtml(stage[0]) + '</strong><span>' + escapeHtml(stage[1] || "-") + '</span><em>' + escapeHtml(stage[2] || "세부 정보 대기") + '</em></div>',
        '</section>'
      ].join("");
    }).join(""),
    '</div>',
    '</div>'
  ].join("");
}

function renderOntologyRelationshipGraphs(tbox, abox, aboxEntities, aboxRelations, evidence, beliefs, opinions, entityLabels, relationCounts, snapshot, parts) {
  parts = parts || {
    tbox: tbox || {},
    abox: abox || {},
    aboxEntities: aboxEntities || [],
    aboxRelations: aboxRelations || [],
    evidence: evidence || [],
    beliefs: beliefs || [],
    opinions: opinions || [],
    entityLabels: entityLabels || {},
    relationCounts: relationCounts || []
  };
  return [
    '<section class="ontology-relationship-graphs" aria-label="TBox ABox 관계 그래프">',
    renderOntologyDecisionChainGraph(parts, snapshot || shellState.snapshot || {}),
    renderOntologyTboxGraph(tbox, relationCounts),
    renderOntologyAboxGraph(abox, aboxEntities, aboxRelations, evidence, beliefs, opinions, entityLabels),
    '</section>'
  ].join("");
}

function ontologyShortText(value, limit) {
  var text = String(value || "");
  var max = limit || 16;
  return text.length > max ? text.slice(0, Math.max(1, max - 1)) + "…" : text;
}

function ontologyGraphImportantRelationTypes() {
  return {
    HOLDS: true,
    WATCHES: true,
    REPRESENTS_STOCK: true,
    HAS_PRICE: true,
    HAS_OBSERVATION: true,
    HAS_EXTERNAL_SIGNAL: true,
    HAS_DATA_QUALITY: true,
    HAS_TRADE_FLOW: true,
    HAS_TREND_TRANSITION: true,
    HAS_TEMPORAL_WINDOW: true,
    WINDOW_CONTAINS_OBSERVATION: true,
    HAS_PRICE_PATH_PATTERN: true,
    HAS_FLOW_PATTERN: true,
    HAS_EVENT_CLUSTER: true,
    CHANGES_OVER_WINDOW: true,
    CONFIRMED_ACROSS_WINDOW: true,
    DIVERGES_ACROSS_WINDOW: true,
    DERIVES_TREND_EPISODE: true,
    AFFECTS_DECISION_EPISODE: true,
    BREAKS_LEVEL: true,
    RETESTS_LEVEL: true,
    RECLAIMS_LEVEL: true,
    HAS_INFERRED_RISK: true,
    HAS_INFERRED_SUPPORT: true,
    HAS_INFERRED_ENTRY_OPPORTUNITY: true,
    CREATES_NOTIFICATION_INTENT: true,
    REQUIRES_NEXT_CHECK: true,
    HAS_OPINION: true,
    HAS_EVIDENCE: true,
    PASSES_IMPORTANCE_GATE: true,
    TRIGGERS_MATERIALITY_ASSESSMENT: true
  };
}

function ontologyTboxGraphNodes(tbox) {
  var contexts = tbox.boundedContexts || [];
  var classDefs = tbox.classDefinitions || (tbox.classes || []).map(function (name) {
    return { name: name, label: name, bounded_context: "investment-core", parent: "" };
  });
  var ruleDefs = tbox.reasoningRuleDefinitions || (tbox.reasoningRules || []).map(function (text) {
    return { text: text, bounded_context: "reasoning-insight" };
  });
  var nodes = {};
  var contextIndex = {};
  contexts.forEach(function (context, index) {
    var key = String(context.key || context.id || "context-" + index);
    contextIndex[key] = index;
    nodes["ctx:" + key] = {
      id: "ctx:" + key,
      label: context.label || key,
      kind: "context",
      title: (context.label || key) + " · " + (context.description || ""),
      x: 90 + index * 160,
      y: 72
    };
  });
  var contextCounts = {};
  classDefs.forEach(function (item) {
    var name = String(item.name || item.className || "");
    if (!name) return;
    var contextKey = String(item.bounded_context || item.boundedContext || "investment-core");
    var index = contextIndex[contextKey];
    if (index === undefined) index = 0;
    var count = contextCounts[contextKey] || 0;
    contextCounts[contextKey] = count + 1;
    nodes["class:" + name] = {
      id: "class:" + name,
      label: item.label || name,
      kind: "schema",
      title: name + (item.description ? " · " + item.description : ""),
      x: 90 + index * 160,
      y: 150 + count * 42
    };
  });
  ruleDefs.forEach(function (item, index) {
    var contextKey = String(item.bounded_context || item.boundedContext || "reasoning-insight");
    var ctxIndex = contextIndex[contextKey];
    if (ctxIndex === undefined) ctxIndex = Math.min(contexts.length - 1, 4);
    nodes["rule:" + index] = {
      id: "rule:" + index,
      label: "R" + (index + 1),
      kind: "rule",
      title: item.text || item,
      x: 90 + ctxIndex * 160,
      y: 500 + (index % 4) * 48
    };
  });
  return nodes;
}

function ontologyTboxGraphEdges(tbox) {
  var classDefs = tbox.classDefinitions || (tbox.classes || []).map(function (name) {
    return { name: name, bounded_context: "investment-core", parent: "" };
  });
  var relationDefs = tbox.relationDefinitions || [];
  var ruleDefs = tbox.reasoningRuleDefinitions || (tbox.reasoningRules || []).map(function (text) {
    return { text: text, bounded_context: "reasoning-insight" };
  });
  var edges = [];
  classDefs.forEach(function (item) {
    var name = String(item.name || item.className || "");
    var contextKey = String(item.bounded_context || item.boundedContext || "investment-core");
    if (!name) return;
    edges.push({ source: "ctx:" + contextKey, target: "class:" + name, type: "DEFINES_CLASS", kind: "schema" });
    if (item.parent) {
      edges.push({ source: "class:" + name, target: "class:" + item.parent, type: "IS_A", kind: "schema" });
    }
  });
  var relationSeen = {};
  relationDefs.forEach(function (item) {
    var sourceContext = String(item.source_context || item.sourceContext || item.bounded_context || item.boundedContext || "");
    var targetContext = String(item.target_context || item.targetContext || item.bounded_context || item.boundedContext || "");
    var name = String(item.name || item.relationType || "");
    var relationType = name.toUpperCase();
    if (!sourceContext || !targetContext || !name) return;
    var key = sourceContext + "|" + targetContext + "|" + relationType;
    if (relationSeen[key]) return;
    relationSeen[key] = true;
    edges.push({ source: "ctx:" + sourceContext, target: "ctx:" + targetContext, type: relationType, kind: "schema" });
  });
  ruleDefs.forEach(function (item, index) {
    var contextKey = String(item.bounded_context || item.boundedContext || "reasoning-insight");
    edges.push({ source: "rule:" + index, target: "ctx:" + contextKey, type: "CONSTRAINS_ASSERTIONS", kind: "rule" });
  });
  return edges;
}

function renderOntologyTboxGraph(tbox, relationCounts) {
  var graphNodes = ontologyTboxGraphNodes(tbox);
  var graphEdges = ontologyTboxGraphEdges(tbox);
  var graphNodeCount = Object.keys(graphNodes || {}).length;
  return [
    '<section class="ontology-graph-panel ontology-tbox-graph">',
    '<div class="ontology-surface-head">',
    '<div>',
    '<strong>전체 규칙 구조 그래프</strong>',
    '<span>TBox 분류, 관계 타입, 규칙 연결을 접지 않고 모두 표시합니다.</span>',
    '</div>',
    '<div class="ontology-graph-actions">',
    '<button class="icon-button" type="button" data-ontology-graph-expand="tbox" title="규칙 구조 큰 화면으로 보기" aria-label="규칙 구조 그래프 큰 화면으로 보기">⤢</button>',
    '<button class="icon-button" type="button" data-ontology-graph-fit="tbox" title="규칙 구조 그래프 맞춤" aria-label="규칙 구조 그래프 맞춤">⌖</button>',
    '<button class="icon-button" type="button" data-ontology-graph-layout="tbox" title="규칙 구조 자동 배치" aria-label="규칙 구조 자동 배치">↺</button>',
    '</div>',
    '</div>',
    '<div class="ontology-graph-meta">',
    '<span>표시 ' + escapeHtml(graphNodeCount) + ' 노드 · ' + escapeHtml(graphEdges.length) + ' 관계</span>',
    '<span>전체 ' + escapeHtml(((tbox.boundedContexts || []).length || 0)) + ' 컨텍스트 · ' + escapeHtml((tbox.classes || []).length || 0) + ' 분류 · ' + escapeHtml((tbox.relationTypes || []).length || 0) + ' 관계 타입</span>',
    '</div>',
    '<div class="ontology-cytoscape" data-ontology-cytoscape="tbox"><span>그래프 엔진 초기화 중</span></div>',
    '<div class="ontology-graph-caption">',
    '<span>모든 relation type을 각각의 관계 edge로 표시합니다.</span>',
    '<span>점선 rule edge는 TBox 규칙이 어떤 컨텍스트의 판단을 제약하는지 나타냅니다.</span>',
    '</div>',
    '</section>'
  ].join("");
}

function ontologyEntityGraphLabel(entity) {
  return ontologyEntityDisplayLabel(entity, entity && entity.id);
}

function ontologyAddGraphNode(nodesById, id, label, kind, title, symbol) {
  if (!id || nodesById[id]) return;
  nodesById[id] = {
    id: id,
    label: label || id,
    kind: kind || "entity",
    title: title || label || id,
    symbol: symbol || ""
  };
}

function ontologyAboxKind(entity) {
  var kind = String(entity && entity.kind || "entity");
  if (kind === "ai-review") return "review";
  if (kind === "model") return "model";
  return kind;
}

function ontologyAboxSymbolFromId(id) {
  var value = String(id || "");
  return value.indexOf("stock:") === 0 ? value.slice(6).toUpperCase() : "";
}

function ontologyPositionAboxGraphNodes(nodesById) {
  var groups = {};
  Object.keys(nodesById).forEach(function (id) {
    var node = nodesById[id];
    var kind = node.kind || "entity";
    if (!groups[kind]) groups[kind] = [];
    groups[kind].push(node);
  });
  Object.keys(groups).forEach(function (kind) {
    groups[kind].sort(function (a, b) { return String(a.label).localeCompare(String(b.label)); });
  });
  var layout = {
    portfolio: { x: 82, y: 245, step: 70 },
    cash: { x: 82, y: 86, step: 70 },
    stock: { x: 236, y: 148, step: 104 },
    sector: { x: 404, y: 72, step: 66 },
    market: { x: 404, y: 214, step: 66 },
    currency: { x: 404, y: 296, step: 66 },
    "fx-pair": { x: 404, y: 366, step: 62 },
    "fx-rate": { x: 572, y: 350, step: 72 },
    "interest-rate": { x: 720, y: 376, step: 72 },
    "yield-curve": { x: 720, y: 448, step: 72 },
    risk: { x: 572, y: 78, step: 74 },
    opportunity: { x: 572, y: 184, step: 74 },
    model: { x: 572, y: 292, step: 74 },
    review: { x: 572, y: 368, step: 74 },
    evidence: { x: 720, y: 132, step: 104 },
    rule: { x: 720, y: 274, step: 80 },
    belief: { x: 850, y: 132, step: 104 },
    opinion: { x: 850, y: 208, step: 104 },
    "research-evidence": { x: 720, y: 132, step: 96 },
    "news-article": { x: 720, y: 132, step: 96 },
    "disclosure-filing": { x: 720, y: 210, step: 92 },
    "fact-change": { x: 572, y: 224, step: 78 },
    "trend-transition": { x: 572, y: 156, step: 78 },
    "temporal-window": { x: 572, y: 236, step: 76 },
    "price-path-pattern": { x: 720, y: 236, step: 78 },
    "flow-pattern": { x: 720, y: 312, step: 78 },
    "event-cluster": { x: 720, y: 390, step: 78 },
    "trend-episode": { x: 850, y: 390, step: 82 },
    "missing-data": { x: 572, y: 300, step: 78 },
    "temporal-coverage-gap": { x: 572, y: 318, step: 78 },
    "next-check": { x: 850, y: 300, step: 86 },
    "alert-candidate": { x: 850, y: 388, step: 86 },
    "inference-trace": { x: 720, y: 330, step: 78 },
    entity: { x: 404, y: 380, step: 62 }
  };
  Object.keys(groups).forEach(function (kind) {
    var spec = layout[kind] || layout.entity;
    groups[kind].forEach(function (node, index) {
      node.x = spec.x;
      node.y = spec.y + index * spec.step;
    });
  });
  var stockY = {};
  (groups.stock || []).forEach(function (node) {
    var symbol = node.symbol || ontologyAboxSymbolFromId(node.id);
    if (symbol) stockY[symbol] = node.y;
  });
  Object.keys(nodesById).forEach(function (id) {
    var node = nodesById[id];
    if (!node.symbol || !stockY[node.symbol]) return;
    if (node.kind === "evidence") node.y = Math.max(70, stockY[node.symbol] - 34);
    if (node.kind === "research-evidence" || node.kind === "news-article" || node.kind === "disclosure-filing") node.y = Math.max(70, stockY[node.symbol] - 44);
    if (node.kind === "fact-change" || node.kind === "trend-transition" || node.kind === "missing-data") node.y = stockY[node.symbol];
    if (node.kind === "temporal-coverage-gap") node.y = stockY[node.symbol] + 26;
    if (node.kind === "temporal-window") node.y = stockY[node.symbol] + 10;
    if (node.kind === "price-path-pattern" || node.kind === "flow-pattern" || node.kind === "event-cluster") node.y = stockY[node.symbol] + 18;
    if (node.kind === "trend-episode") node.y = stockY[node.symbol] + 52;
    if (node.kind === "belief") node.y = Math.max(70, stockY[node.symbol] - 34);
    if (node.kind === "opinion") node.y = stockY[node.symbol] + 30;
    if (node.kind === "next-check" || node.kind === "alert-candidate") node.y = stockY[node.symbol] + 46;
  });
}

function ontologySelectGraphRelations(relations) {
  var important = ontologyGraphImportantRelationTypes();
  var rows = (relations || []).filter(function (relation) {
    var type = ontologyTypeOf(relation);
    if (important[type]) return true;
    if (ontologyBoxOf(relation) === "INFERENCEBOX") return true;
    var props = relation.properties || {};
    return Boolean(props.aiInfluenceLabel || props.materialityPassed || props.decisionStage);
  });
  rows.sort(function (a, b) {
    var priority = ontologyRelationPriority(ontologyTypeOf(a)) - ontologyRelationPriority(ontologyTypeOf(b));
    if (priority !== 0) return priority;
    return Number(b.weight || 0) - Number(a.weight || 0);
  });
  return rows.slice(0, 120);
}

function ontologyBuildAboxGraph(aboxEntities, aboxRelations, evidence, beliefs, opinions, entityLabels) {
  var nodesById = {};
  var entityById = (aboxEntities || []).reduce(function (memo, entity) {
    if (entity && entity.id) memo[entity.id] = entity;
    return memo;
  }, {});
  var graphRelations = ontologySelectGraphRelations(aboxRelations || []);
  graphRelations.forEach(function (relation) {
    [relation.source, relation.target].forEach(function (id) {
      var entity = entityById[id] || { id: id, label: ontologyEndpointLabel(id, entityLabels), kind: "entity" };
      var label = ontologyEntityGraphLabel(entity);
      var properties = entity.properties || {};
      var symbol = String(properties.symbol || ontologyAboxSymbolFromId(id) || "").toUpperCase();
      ontologyAddGraphNode(nodesById, id, label, ontologyAboxKind(entity), label, symbol);
    });
  });
  var edges = graphRelations.map(function (relation) {
    return { source: relation.source, target: relation.target, type: ontologyTypeOf(relation), kind: "assertion" };
  });
  ontologyAddGraphNode(nodesById, "runtime-rules", "Runtime Rules", "rule", "ABox assertions evaluated by TBox reasoning rules");
  (opinions || []).slice(0, 5).forEach(function (opinion) {
    var symbol = String(opinion && opinion.symbol || "").toUpperCase();
    var stockId = "stock:" + symbol;
    if (!symbol || !nodesById[stockId]) return;
    var displayName = nodesById[stockId].label || stockDisplayName(symbol);
    var stockEvidence = (evidence || []).filter(function (item) { return String(item && item.subject || "") === stockId; });
    var stockBeliefs = (beliefs || []).filter(function (item) { return String(item && item.subject || "") === stockId; });
    var evidenceId = "evidence-set:" + symbol;
    var beliefId = "belief-set:" + symbol;
    var opinionId = "opinion:" + symbol;
    ontologyAddGraphNode(nodesById, evidenceId, "근거 " + stockEvidence.length, "evidence", displayName + " 근거 " + stockEvidence.length + "개", symbol);
    ontologyAddGraphNode(nodesById, beliefId, "판단 근거 " + stockBeliefs.length, "belief", displayName + " 판단 근거 " + stockBeliefs.length + "개", symbol);
    ontologyAddGraphNode(nodesById, opinionId, "의견 " + displayName, "opinion", textWithKnownDisplaySymbols(beginnerFriendlyText(opinion.thesis || opinion.action || displayName), symbol, { symbol: symbol, name: displayName }), symbol);
    edges.push({ source: stockId, target: evidenceId, type: "HAS_EVIDENCE", kind: "derived" });
    edges.push({ source: evidenceId, target: "runtime-rules", type: "EVALUATED_BY", kind: "rule" });
    edges.push({ source: "runtime-rules", target: beliefId, type: "DERIVES", kind: "rule" });
    edges.push({ source: beliefId, target: opinionId, type: "HAS_OPINION", kind: "derived" });
  });
  ontologyPositionAboxGraphNodes(nodesById);
  return { nodesById: nodesById, edges: edges };
}

function ontologyWorldLensNodeKinds(lensId) {
  var map = {
    portfolio: ["portfolio", "account", "position", "stock", "company", "security", "sector", "industry", "market", "cash", "currency", "fx-pair", "fx-rate", "interest-rate", "yield-curve", "factor", "instrument-profile", "market-proxy-instrument"],
    risk: ["risk", "missing-data", "temporal-coverage-gap", "coverage-gap", "data-quality", "data-freshness", "data-latency", "source-reliability", "fact-change", "trend-transition", "signal-transition", "relation-state", "competing-hypothesis", "materiality-assessment", "alert-candidate", "next-check"],
    inference: ["evidence", "belief", "opinion", "active-opinion", "insight", "decision-driver", "competing-hypothesis", "rule", "review", "model", "strategy-signal", "inference-trace", "reasoning-cycle", "alert-candidate", "next-check", "execution-plan"],
    evidence: ["evidence", "research-evidence", "news-article", "disclosure-filing", "financial-fact", "fundamental-event", "market-observation", "price-metric", "price-bar", "volume-profile", "source-reliability", "data-quality", "provenance", "fact-change", "trend-transition"]
  };
  return (map[normalizeOntologyWorldLens(lensId)] || []).reduce(function (memo, kind) { memo[kind] = true; return memo; }, {});
}

function ontologyWorldLensRelationMatches(lensId, relationType) {
  var type = String(relationType || "").toUpperCase();
  if (lensId === "portfolio") return /HOLDS|WATCHES|EXPOSURE|BELONGS|MARKET|CURRENCY|FX|RATE|PRICE/.test(type);
  if (lensId === "risk") return /RISK|QUALITY|MISSING|ALERT|NEXT_CHECK|BREAK|DIVERG|CHANGE|TREND|LOSS/.test(type);
  if (lensId === "inference") return Boolean(ontologyInferenceRelationTypes()[type]) || /EVIDENCE|OPINION|DERIVE|EVALUATED|RULE/.test(type);
  if (lensId === "evidence") return /EVIDENCE|SOURCE|PROVENANCE|RESEARCH|NEWS|DISCLOSURE|QUALITY/.test(type);
  return true;
}

function ontologyWorldLaneForKind(kind) {
  var value = String(kind || "entity");
  var lanes = {
    reality: ["portfolio", "account", "position", "stock", "company", "security", "cash", "currency", "sector", "industry", "market", "fx-pair", "fx-rate", "interest-rate", "yield-curve", "instrument-profile", "investment-archetype", "factor-sensitivity", "market-proxy-instrument", "market-proxy-theme", "crypto-exposure"],
    evidence: ["evidence", "relation-evidence", "research-evidence", "news-article", "disclosure-filing", "financial-fact", "fundamental-event", "market-observation", "price-bar", "price-metric", "volume-profile", "key-level", "liquidity-profile", "source-reliability", "data-quality", "data-freshness", "data-latency", "missing-data", "temporal-coverage-gap", "coverage-gap", "provenance", "fact-change", "trend-transition", "signal-transition", "relation-state", "temporal-window", "price-path", "price-path-pattern", "flow-pattern", "event-cluster", "trend-episode", "observed-outcome"],
    rule: ["rule", "rule-condition", "relation-rule", "relation-rule-condition", "relation-rule-template", "relation-template", "threshold-policy", "strategy", "investment-thesis", "instrument-policy", "runtime-setting", "valuation-assumption", "data-pipeline", "collection-schedule", "model", "review"],
    inference: ["belief", "insight", "inference-trace", "reasoning-cycle", "strategy-signal", "trend-scenario", "macro-regime", "risk", "opportunity", "decision-driver", "competing-hypothesis", "materiality-assessment", "margin-of-safety", "factor"],
    decision: ["opinion", "active-opinion", "execution-plan", "next-check", "alert-candidate", "notification-intent", "notification-dispatch", "decision-episode", "investment-opinion", "performance-feedback"]
  };
  var lane = Object.keys(lanes).filter(function (key) { return lanes[key].indexOf(value) >= 0; })[0];
  return lane || "evidence";
}

function ontologyWorldNodeDegree(edges) {
  var degree = {};
  (edges || []).forEach(function (edge) {
    degree[edge.source] = (degree[edge.source] || 0) + 1;
    degree[edge.target] = (degree[edge.target] || 0) + 1;
  });
  return degree;
}

function ontologyWorldNeighborhood(graph, focusId, depth) {
  var nodes = graph.nodesById || {};
  var edges = graph.edges || [];
  if (!focusId || !nodes[focusId]) return graph;
  var focusSymbol = String((nodes[focusId] || {}).symbol || ontologyAboxSymbolFromId(focusId) || "").toUpperCase();
  var keep = {};
  keep[focusId] = true;
  Object.keys(nodes).forEach(function (id) {
    if (focusSymbol && String(nodes[id].symbol || "").toUpperCase() === focusSymbol) keep[id] = true;
  });
  for (var step = 0; step < normalizeOntologyWorldDepth(depth); step += 1) {
    var next = Object.assign({}, keep);
    edges.forEach(function (edge) {
      if (keep[edge.source]) next[edge.target] = true;
      if (keep[edge.target]) next[edge.source] = true;
    });
    keep = next;
  }
  var nodesById = {};
  Object.keys(keep).forEach(function (id) { if (nodes[id]) nodesById[id] = nodes[id]; });
  return {
    nodesById: nodesById,
    edges: edges.filter(function (edge) { return keep[edge.source] && keep[edge.target]; })
  };
}

function ontologyWorldCompactTopology(graph, focusId) {
  var nodes = graph.nodesById || {};
  var edges = graph.edges || [];
  var degree = ontologyWorldNodeDegree(edges);
  var focusSymbol = String(((nodes[focusId] || {}).symbol) || ontologyAboxSymbolFromId(focusId) || "").toUpperCase();
  var groups = { reality: [], evidence: [], rule: [], inference: [], decision: [] };
  Object.keys(nodes).forEach(function (id) {
    var node = nodes[id] || {};
    var lane = ontologyWorldLaneForKind(node.kind);
    node.lane = lane;
    groups[lane].push(id);
  });
  function nodePriority(id) {
    var node = nodes[id] || {};
    var priority = Number(degree[id] || 0) * 20;
    if (id === focusId) priority += 10000;
    if (focusSymbol && String(node.symbol || "").toUpperCase() === focusSymbol) priority += 4000;
    if (id === "runtime-rules") priority += 2500;
    if (["risk", "alert-candidate", "opinion", "active-opinion", "next-check", "inference-trace"].indexOf(String(node.kind || "")) >= 0) priority += 900;
    if (String(node.kind || "") === "portfolio") priority += 700;
    return priority;
  }
  var keep = {};
  Object.keys(groups).forEach(function (lane) {
    groups[lane].sort(function (a, b) {
      var priority = nodePriority(b) - nodePriority(a);
      return priority || String((nodes[a] || {}).label || a).localeCompare(String((nodes[b] || {}).label || b));
    }).slice(0, 6).forEach(function (id) { keep[id] = true; });
  });
  if (nodes[focusId]) keep[focusId] = true;
  var nodesById = {};
  Object.keys(keep).forEach(function (id) { if (nodes[id]) nodesById[id] = nodes[id]; });
  var relationLabels = {};
  var labelCount = 0;
  var keptEdges = edges.filter(function (edge) { return keep[edge.source] && keep[edge.target]; }).sort(function (a, b) {
    var directA = a.source === focusId || a.target === focusId ? 0 : 1;
    var directB = b.source === focusId || b.target === focusId ? 0 : 1;
    if (directA !== directB) return directA - directB;
    var derivedA = a.kind === "derived" || a.kind === "rule" ? 0 : 1;
    var derivedB = b.kind === "derived" || b.kind === "rule" ? 0 : 1;
    if (derivedA !== derivedB) return derivedA - derivedB;
    return ontologyRelationPriority(a.type) - ontologyRelationPriority(b.type);
  }).slice(0, 38).map(function (edge) {
    var type = String(edge.type || "");
    var direct = edge.source === focusId || edge.target === focusId;
    var semantic = edge.kind === "derived" || edge.kind === "rule" || ontologyRelationPriority(type) < 7;
    var noisyObservation = /EXTERNAL_SIGNAL|DATA_QUALITY|HAS_PRICE|HAS_OBSERVATION/.test(type.toUpperCase());
    var crossLane = String((nodes[edge.source] || {}).lane || "") !== String((nodes[edge.target] || {}).lane || "");
    var showLabel = labelCount < 10 && crossLane && (semantic || (direct && !noisyObservation)) && Number(relationLabels[type] || 0) < 2;
    if (showLabel) {
      labelCount += 1;
      relationLabels[type] = Number(relationLabels[type] || 0) + 1;
    }
    return Object.assign({}, edge, { showLabel: showLabel });
  });
  var laneX = { reality: 120, evidence: 380, rule: 640, inference: 900, decision: 1160 };
  Object.keys(groups).forEach(function (lane) {
    var ids = groups[lane].filter(function (id) { return Boolean(keep[id]); });
    var spacing = ids.length > 5 ? 76 : 84;
    var firstY = 284 - ((ids.length - 1) * spacing / 2);
    ids.forEach(function (id, index) {
      var node = nodesById[id];
      node.x = laneX[lane];
      node.y = firstY + index * spacing;
      node.focus = id === focusId;
      node.context = Boolean(focusSymbol && String(node.symbol || "").toUpperCase() === focusSymbol);
    });
  });
  return { nodesById: nodesById, edges: keptEdges, focusId: focusId };
}

function ontologyAddWorldDecisionCorridor(graph, parts, snapshot, focusId) {
  var symbol = ontologyAboxSymbolFromId(focusId);
  if (!symbol) return graph;
  var row = ontologyDecisionChainRows(parts || {}, snapshot || shellState.snapshot || {}).filter(function (item) {
    return String(item && item.symbol || "").toUpperCase() === symbol;
  })[0];
  if (!row) return graph;
  var nodes = graph.nodesById || {};
  var edges = graph.edges || [];
  var ids = {
    relation: "world-relation:" + symbol,
    rule: "world-rule:" + symbol,
    inference: "world-inference:" + symbol,
    decision: "world-decision:" + symbol,
    alert: "world-alert:" + symbol,
    performance: "world-performance:" + symbol
  };
  ontologyAddGraphNode(nodes, ids.relation, row.relationLabel || "관계 근거", "relation-evidence", row.relationDetail || "관계 근거", symbol);
  ontologyAddGraphNode(nodes, ids.rule, row.ruleLabel || "RuleBox", "rule", row.ruleDetail || "RuleBox 조건", symbol);
  ontologyAddGraphNode(nodes, ids.inference, row.inferenceLabel || "InferenceBox", "inference-trace", row.inferenceDetail || "InferenceBox 출력", symbol);
  ontologyAddGraphNode(nodes, ids.decision, row.actionLabel || "판단 대기", "opinion", row.actionDetail || "현재 투자 판단", symbol);
  ontologyAddGraphNode(nodes, ids.alert, row.alertLabel || "알림 대기", "notification-intent", row.alertDetail || "알림 생성 상태", symbol);
  ontologyAddGraphNode(nodes, ids.performance, row.performanceLabel || "성과 대기", "performance-feedback", row.performanceDetail || "성과 표본 상태", symbol);
  [
    [focusId, ids.relation, "HAS_DECISION_CONTEXT", "derived"],
    [ids.relation, ids.rule, "EVALUATED_BY", "rule"],
    [ids.rule, ids.inference, "DERIVES", "rule"],
    [ids.inference, ids.decision, "HAS_OPINION", "derived"],
    [ids.decision, ids.alert, "CREATES_NOTIFICATION_INTENT", "derived"],
    [ids.decision, ids.performance, "EVALUATED_BY_OUTCOME", "derived"]
  ].forEach(function (item) {
    edges.push({ source: item[0], target: item[1], type: item[2], kind: item[3] });
  });
  return { nodesById: nodes, edges: edges };
}

function ontologyBuildWorldGraph(parts, snapshot, lensValue) {
  parts = parts || ontologyStrategyParts(snapshot || shellState.snapshot || {});
  var lensId = normalizeOntologyWorldLens(lensValue);
  var base = ontologyBuildAboxGraph(parts.aboxEntities, parts.aboxRelations, parts.evidence, parts.beliefs, parts.opinions, parts.entityLabels);
  var focusId = ontologyResolveWorldFocusId(parts, snapshot || shellState.snapshot || {}, ontologyWorldFocusItems(parts, snapshot || shellState.snapshot || {}));
  if (focusId && !base.nodesById[focusId]) {
    var focusItem = (parts.aboxEntities || []).filter(function (item) { return String(item && item.id || "") === focusId; })[0];
    if (focusItem) ontologyAddGraphNode(base.nodesById, focusId, ontologyEntityGraphLabel(focusItem), "stock", ontologyEntityGraphLabel(focusItem), ontologyAboxSymbolFromId(focusId));
  }
  base = ontologyAddWorldDecisionCorridor(base, parts, snapshot || shellState.snapshot || {}, focusId);
  var candidate = base;
  if (lensId !== "reality") {
    var kinds = ontologyWorldLensNodeKinds(lensId);
    var selected = {};
    Object.keys(base.nodesById || {}).forEach(function (id) {
      if (kinds[String((base.nodesById[id] || {}).kind || "")]) selected[id] = true;
    });
    var edges = (base.edges || []).filter(function (edge) {
      return ontologyWorldLensRelationMatches(lensId, edge.type) || (selected[edge.source] && selected[edge.target]);
    });
    edges.forEach(function (edge) {
      selected[edge.source] = true;
      selected[edge.target] = true;
    });
    (base.edges || []).forEach(function (edge) {
      if (!selected[edge.source] && !selected[edge.target]) return;
      var sourceKind = String(((base.nodesById || {})[edge.source] || {}).kind || "");
      var targetKind = String(((base.nodesById || {})[edge.target] || {}).kind || "");
      if (["portfolio", "stock"].indexOf(sourceKind) < 0 && ["portfolio", "stock"].indexOf(targetKind) < 0) return;
      if (!/HOLDS|WATCHES|BELONGS|HAS_OPINION|HAS_EVIDENCE|HAS_INFERRED/.test(String(edge.type || "").toUpperCase())) return;
      if (edges.indexOf(edge) < 0) edges.push(edge);
      selected[edge.source] = true;
      selected[edge.target] = true;
    });
    var nodesById = {};
    Object.keys(selected).forEach(function (id) {
      if (base.nodesById[id]) nodesById[id] = base.nodesById[id];
    });
    if (focusId && base.nodesById[focusId]) nodesById[focusId] = base.nodesById[focusId];
    candidate = Object.keys(nodesById).length ? { nodesById: nodesById, edges: edges.slice(0, 120) } : base;
  }
  var neighborhood = ontologyWorldNeighborhood(candidate, focusId, ontologyState.activeOntologyWorldDepth);
  if (Object.keys(neighborhood.nodesById || {}).length < 2) neighborhood = candidate;
  return ontologyWorldCompactTopology(neighborhood, focusId);
}

function renderOntologyAboxGraph(abox, aboxEntities, aboxRelations, evidence, beliefs, opinions, entityLabels) {
  var portfolioId = String(abox && abox.portfolioId || "flow-lens");
  var graph = ontologyBuildAboxGraph(aboxEntities, aboxRelations, evidence, beliefs, opinions, entityLabels);
  var graphNodeCount = Object.keys(graph.nodesById || {}).length;
  var graphEdgeCount = (graph.edges || []).length;
  return [
    '<section class="ontology-graph-panel ontology-abox-graph">',
    '<div class="ontology-surface-head">',
    '<div>',
    '<strong>핵심 데이터 관계 그래프</strong>',
    '<span>실제 데이터 중 AI 판단, 중요 변경, 알림 후보와 연결되는 관계만 압축 표시합니다.</span>',
    '</div>',
    '<div class="ontology-graph-actions">',
    '<button class="icon-button" type="button" data-ontology-graph-expand="abox" title="데이터 관계 큰 화면으로 보기" aria-label="데이터 관계 그래프 큰 화면으로 보기">⤢</button>',
    '<button class="icon-button" type="button" data-ontology-graph-fit="abox" title="현재 데이터 그래프 맞춤" aria-label="현재 데이터 그래프 맞춤">⌖</button>',
    '<button class="icon-button" type="button" data-ontology-graph-layout="abox" title="현재 데이터 자동 배치" aria-label="현재 데이터 자동 배치">↺</button>',
    '</div>',
    '</div>',
    '<div class="ontology-graph-meta">',
    '<span>표시 ' + escapeHtml(graphNodeCount) + ' 노드 · ' + escapeHtml(graphEdgeCount) + ' 관계</span>',
    '<span>전체 ' + escapeHtml(aboxEntities.length) + ' 데이터 행 · ' + escapeHtml(aboxRelations.length) + ' 관계 행 · ' + escapeHtml((beliefs || []).length) + ' 판단 근거</span>',
    '<span>현재 실행 데이터 · 계좌 ' + escapeHtml(portfolioId) + '</span>',
    '</div>',
    '<div class="ontology-cytoscape" data-ontology-cytoscape="abox"><span>그래프 엔진 초기화 중</span></div>',
    '<div class="ontology-graph-caption">',
    '<span>실선은 현재 데이터에서 확인된 핵심 관계입니다.</span>',
    '<span>점선은 근거가 규칙을 거쳐 판단 근거, 다음 확인, 알림 후보로 이어지는 관계입니다.</span>',
    '</div>',
    '</section>'
  ].join("");
}

function ontologyEdgeLabel(type) {
  var label = String(type || "");
  return label.replace("USES_EVIDENCE_FROM", "USES_EVIDENCE").replace("REQUESTS_OPINION_FROM", "REQUESTS_OPINION");
}

function ontologyGraphClass(value) {
  return String(value || "entity").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
}

function ontologyCyColor(name, fallback) {
  var styles = window.getComputedStyle ? window.getComputedStyle(document.documentElement) : null;
  var value = styles ? String(styles.getPropertyValue(name) || "").trim() : "";
  return value || fallback;
}

function ontologyCyElements(nodesById, edges) {
  var nodes = Object.keys(nodesById || {}).map(function (id) {
    var node = nodesById[id] || {};
    var nodeClasses = ["node-" + ontologyGraphClass(node.kind), "lane-" + ontologyGraphClass(node.lane || "")];
    if (node.active) nodeClasses.push("node-active-chain");
    if (node.context) nodeClasses.push("node-world-context");
    if (node.focus) nodeClasses.push("node-world-focus");
    return {
      group: "nodes",
      data: {
        id: id,
        label: ontologyShortText(node.label || id, node.kind === "rule" ? 10 : 18),
        fullLabel: node.label || id,
        kind: node.kind || "entity",
        lane: node.lane || "",
        title: node.title || node.label || id,
        chainKey: node.chainKey || ""
      },
      position: {
        x: Number(node.x || 0),
        y: Number(node.y || 0)
      },
      classes: nodeClasses.join(" ")
    };
  });
  var seen = {};
  var edgeItems = (edges || []).filter(function (edge) {
    return edge && nodesById[edge.source] && nodesById[edge.target];
  }).map(function (edge, index) {
    var key = [edge.source, edge.type, edge.target, edge.kind, index].join("|");
    var id = "edge:" + key;
    if (seen[id]) id += ":" + index;
    seen[id] = true;
    return {
      group: "edges",
      data: {
        id: id,
        source: edge.source,
        target: edge.target,
        label: edge.showLabel === false ? "" : ontologyShortText(ontologyEdgeLabel(edge.type), 18),
        fullLabel: edge.type || "",
        kind: edge.kind || "assertion",
        chainKey: edge.chainKey || ""
      },
      classes: "edge-" + ontologyGraphClass(edge.kind)
    };
  });
  return nodes.concat(edgeItems);
}

function ontologyCurrentGraphData() {
  var parts = ontologyStrategyParts(shellState.snapshot || {});
  var tboxNodes = ontologyTboxGraphNodes(parts.tbox);
  var tboxEdges = ontologyTboxGraphEdges(parts.tbox);
  var aboxGraph = ontologyBuildAboxGraph(parts.aboxEntities, parts.aboxRelations, parts.evidence, parts.beliefs, parts.opinions, parts.entityLabels);
  var worldGraph = ontologyBuildWorldGraph(parts, shellState.snapshot || {}, ontologyState.activeOntologyWorldLens);
  var decisionChainGraph = ontologyBuildDecisionChainGraph(parts, shellState.snapshot || {});
  return {
    tbox: { elements: ontologyCyElements(tboxNodes, tboxEdges) },
    abox: { elements: ontologyCyElements(aboxGraph.nodesById, aboxGraph.edges) },
    world: { elements: ontologyCyElements(worldGraph.nodesById, worldGraph.edges) },
    "decision-chain": { elements: ontologyCyElements(decisionChainGraph.nodesById, decisionChainGraph.edges) }
  };
}

function ontologyCytoscapeStyle() {
  var ink = ontologyCyColor("--ink", "#172033");
  var muted = ontologyCyColor("--muted", "#64748b");
  var panel = ontologyCyColor("--panel", "#ffffff");
  var blue = ontologyCyColor("--blue", "#2563eb");
  var green = ontologyCyColor("--green", "#059669");
  var red = ontologyCyColor("--red", "#dc2626");
  var amber = ontologyCyColor("--amber", "#d97706");
  var violet = ontologyCyColor("--violet", "#7c3aed");
  var line = ontologyCyColor("--line", "#d8e0ea");
  return [
    {
      selector: "node",
      style: {
        "shape": "round-rectangle",
        "width": 132,
        "height": 44,
        "background-color": panel,
        "border-width": 1.4,
        "border-color": line,
        "label": "data(label)",
        "color": ink,
        "font-size": 10,
        "font-weight": 800,
        "text-valign": "center",
        "text-halign": "center",
        "text-wrap": "wrap",
        "text-max-width": 112,
        "overlay-opacity": 0
      }
    },
    { selector: ".node-context", style: { "width": 150, "height": 48, "background-color": "#eef2ff", "border-color": violet, "color": ink, "font-weight": 900 } },
    { selector: ".node-rule", style: { "width": 74, "height": 36, "background-color": "#fff7ed", "border-color": amber, "color": amber, "font-size": 9 } },
    { selector: ".node-schema, .node-portfolio, .node-stock", style: { "background-color": "#eff6ff", "border-color": blue } },
    { selector: ".node-sector, .node-market, .node-currency, .node-cash", style: { "background-color": "#ecfdf5", "border-color": green } },
    { selector: ".node-fx-rate, .node-fx-pair", style: { "background-color": "#ecfeff", "border-color": green } },
    { selector: ".node-interest-rate, .node-yield-curve", style: { "background-color": "#fff7ed", "border-color": amber } },
    { selector: ".node-risk", style: { "background-color": "#fef2f2", "border-color": red } },
    { selector: ".node-evidence, .node-belief, .node-opinion, .node-review, .node-model, .node-research-evidence, .node-news-article, .node-disclosure-filing", style: { "background-color": "#f5f3ff", "border-color": violet } },
    { selector: ".node-fact-change, .node-trend-transition", style: { "background-color": "#eff6ff", "border-color": blue } },
    { selector: ".node-temporal-window, .node-price-path-pattern, .node-flow-pattern, .node-event-cluster, .node-trend-episode", style: { "background-color": "#ecfeff", "border-color": blue } },
    { selector: ".node-missing-data, .node-temporal-coverage-gap, .node-data-quality, .node-source-reliability", style: { "background-color": "#fff7ed", "border-color": amber } },
    { selector: ".node-next-check, .node-alert-candidate, .node-inference-trace", style: { "background-color": "#f8fafc", "border-color": ink } },
    { selector: ".node-source-data", style: { "background-color": "#eff6ff", "border-color": blue } },
    { selector: ".node-relation-evidence", style: { "background-color": "#ecfdf5", "border-color": green } },
    { selector: ".node-rulebox", style: { "background-color": "#fff7ed", "border-color": amber, "color": amber } },
    { selector: ".node-inferencebox", style: { "background-color": "#f5f3ff", "border-color": violet } },
    { selector: ".node-investment-opinion", style: { "background-color": "#eff6ff", "border-color": blue, "font-weight": 900 } },
    { selector: ".node-notification-intent", style: { "background-color": "#f8fafc", "border-color": ink } },
    { selector: ".node-performance-feedback", style: { "background-color": "#ecfdf5", "border-color": green } },
    { selector: ".lane-reality", style: { "shape": "round-rectangle", "background-color": panel, "border-color": blue, "border-width": 1.6 } },
    { selector: ".lane-evidence", style: { "shape": "round-rectangle", "background-color": panel, "border-color": muted, "border-width": 1.3 } },
    { selector: ".lane-rule", style: { "shape": "diamond", "width": 88, "height": 54, "background-color": "#fffaf0", "border-color": amber, "color": ink, "font-size": 9 } },
    { selector: ".lane-inference", style: { "shape": "hexagon", "width": 126, "height": 52, "background-color": "#f8f7ff", "border-color": violet, "color": ink } },
    { selector: ".lane-decision", style: { "shape": "round-rectangle", "background-color": ink, "border-color": ink, "color": panel, "font-weight": 900 } },
    { selector: ".node-world-context", style: { "border-width": 2.2 } },
    { selector: ".node-world-focus", style: { "shape": "round-rectangle", "width": 146, "height": 50, "background-color": ink, "border-width": 3, "border-color": blue, "color": panel, "font-size": 11, "font-weight": 900 } },
    { selector: ".node-active-chain", style: { "border-width": 3, "border-color": ink } },
    {
      selector: "edge",
      style: {
        "curve-style": "taxi",
        "taxi-direction": "rightward",
        "taxi-turn": 32,
        "taxi-turn-min-distance": 12,
        "target-arrow-shape": "triangle",
        "target-arrow-color": blue,
        "line-color": blue,
        "width": 1.25,
        "opacity": 0.82,
        "label": "data(label)",
        "font-size": 7,
        "font-weight": 700,
        "color": muted,
        "text-background-color": panel,
        "text-background-opacity": 0.82,
        "text-background-padding": 2,
        "text-rotation": "none",
        "text-margin-y": -6
      }
    },
    { selector: ".edge-schema", style: { "line-color": blue, "target-arrow-color": blue, "width": 1.8 } },
    { selector: ".edge-assertion", style: { "line-color": green, "target-arrow-color": green } },
    { selector: ".edge-derived", style: { "line-color": violet, "target-arrow-color": violet, "line-style": "dashed" } },
    { selector: ".edge-rule", style: { "line-color": amber, "target-arrow-color": amber, "line-style": "dashed" } },
    { selector: ".edge-chain", style: { "line-color": blue, "target-arrow-color": blue, "width": 1.7 } },
    { selector: ":selected", style: { "border-width": 3, "border-color": ink, "line-color": ink, "target-arrow-color": ink, "opacity": 1 } }
  ];
}

var graphGeneration = 0;
var releaseGraphs = function () {};

function destroyOntologyCytoscapeGraphs() {
  graphGeneration++;
  releaseGraphs();
  releaseGraphs = function () {};
  Object.keys(ontologyGraphInstancesCell.value || {}).forEach(function (key) {
    var instance = ontologyGraphInstancesCell.value[key];
    if (instance && typeof instance.destroy === "function") instance.destroy();
  });
  ontologyGraphInstancesCell.value = {};
}

function ensureCytoscape() {
  if (window.cytoscape) return Promise.resolve(window.cytoscape);
  if (cytoscapeLoadPromise) return cytoscapeLoadPromise;
  var runtime = window.OrbitWebRuntime;
  if (runtime && typeof runtime.loadScriptOnce === "function") {
    cytoscapeLoadPromise = runtime.loadScriptOnce("vendor/cytoscape.min.js?v=20260707-ontology-graph", "cytoscape");
    return cytoscapeLoadPromise;
  }
  cytoscapeLoadPromise = new Promise(function (resolve, reject) {
    var script = document.createElement("script");
    script.src = "vendor/cytoscape.min.js?v=20260707-ontology-graph";
    script.async = true;
    script.onload = function () { resolve(window.cytoscape); };
    script.onerror = function () { reject(new Error("그래프 엔진을 불러오지 못했습니다.")); };
    document.head.appendChild(script);
  });
  return cytoscapeLoadPromise;
}

function initOntologyCytoscapeGraphs() {
  var containers = Array.prototype.slice.call(app.querySelectorAll("[data-ontology-cytoscape]"));
  if (!containers.length) { destroyOntologyCytoscapeGraphs(); return; }
  var generation = graphGeneration;
  var currentView = viewLifetime.capture();
  if (!window.cytoscape) {
    containers.forEach(function (container) {
      container.innerHTML = '<span>그래프 엔진을 준비하는 중입니다.</span>';
    });
    ensureCytoscape().then(function () {
      if (currentView() && generation === graphGeneration && containers.some(function (container) { return container.isConnected; })) initOntologyCytoscapeGraphs();
    }).catch(function () {
      if (!currentView() || generation !== graphGeneration) return;
      containers.forEach(function (container) {
        if (container.isConnected) container.innerHTML = '<span>그래프 엔진을 불러오지 못했습니다.</span>';
      });
    });
    return;
  }
  releaseGraphs();
  releaseGraphs = viewLifetime.own(destroyOntologyCytoscapeGraphs);
  Object.keys(ontologyGraphInstancesCell.value).forEach(function (key) {
    var instance = ontologyGraphInstancesCell.value[key];
    if (instance.container && !instance.container().isConnected) {
      instance.destroy();
      delete ontologyGraphInstancesCell.value[key];
    }
  });
  var graphs = ontologyCurrentGraphData();
  containers.forEach(function (container) {
    var graphId = container.getAttribute("data-ontology-cytoscape") || "";
    var existing = ontologyGraphInstancesCell.value[graphId];
    if (existing && existing.container && existing.container() === container) return;
    if (existing) existing.destroy();
    var sourceGraphId = normalizeOntologyGraphId(graphId);
    var graph = graphs[sourceGraphId];
    if (!graph || !graph.elements.length) {
      container.innerHTML = '<span>표시할 그래프 관계가 없습니다.</span>';
      return;
    }
    container.innerHTML = "";
    ontologyGraphInstancesCell.value[graphId] = window.cytoscape({
      container: container,
      elements: graph.elements,
      style: ontologyCytoscapeStyle(),
      layout: { name: "preset", fit: true, padding: sourceGraphId === "world" ? 34 : 28 },
      minZoom: 0.25,
      maxZoom: 2.4,
      boxSelectionEnabled: false,
      autoungrabify: sourceGraphId === "world"
    });
    ontologyGraphInstancesCell.value[graphId].ready(function () {
      this.fit(undefined, 30);
    });
    if (sourceGraphId === "decision-chain") {
      ontologyGraphInstancesCell.value[graphId].on("tap", "node", function (event) {
        var key = event && event.target && event.target.data ? event.target.data("chainKey") : "";
        if (!key) return;
        ontologyState.activeOntologyChainKey = key;
        render();
      });
    }
    if (sourceGraphId === "world") {
      ontologyGraphInstancesCell.value[graphId].on("tap", "node", function (event) {
        var nodeId = event && event.target && event.target.data ? event.target.data("id") : "";
        if (!nodeId) return;
        ontologyState.activeOntologyWorldNodeId = nodeId;
        var parts = ontologyStrategyParts(shellState.snapshot || {});
        var worldGraph = ontologyBuildWorldGraph(parts, shellState.snapshot || {}, ontologyState.activeOntologyWorldLens);
        var inspector = app.querySelector("[data-ontology-world-inspector]");
        if (inspector) inspector.outerHTML = renderOntologyWorldNodeInspector(parts, shellState.snapshot || {}, worldGraph);
      });
    }
  });
}

function fitOntologyGraph(graphId) {
  var instance = ontologyGraphInstancesCell.value[graphId];
  if (!instance) return;
  instance.fit(undefined, 30);
  instance.center();
}

function layoutOntologyGraph(graphId) {
  var instance = ontologyGraphInstancesCell.value[graphId];
  if (!instance) return;
  if (normalizeOntologyGraphId(graphId) === "world") {
    render();
    return;
  }
  instance.layout({
    name: "breadthfirst",
    directed: true,
    padding: 36,
    spacingFactor: normalizeOntologyGraphId(graphId) === "abox" || normalizeOntologyGraphId(graphId) === "world" ? 1.25 : normalizeOntologyGraphId(graphId) === "decision-chain" ? 1.05 : 1.1,
    avoidOverlap: true,
    animate: true,
    animationDuration: 260,
    fit: true
  }).run();
}

function renderOntologyClassPanel(tbox) {
  var classes = tbox.classes || [];
  var relationTypes = tbox.relationTypes || [];
  var contexts = tbox.boundedContexts || [];
  var classDefs = tbox.classDefinitions || [];
  var grouped = {};
  classDefs.forEach(function (item) {
    var key = String(item.bounded_context || item.boundedContext || "schema");
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(item);
  });
  return [
    '<section class="ontology-surface ontology-tbox-surface">',
    '<div class="ontology-surface-head">',
    '<strong>규칙 구조</strong>',
    '<span>' + escapeHtml(contexts.length || 0) + ' 컨텍스트 · ' + escapeHtml(classes.length) + ' 분류 · ' + escapeHtml(relationTypes.length) + ' 관계 종류</span>',
    '</div>',
    contexts.length && classDefs.length ? '<div class="ontology-context-class-grid">' + contexts.map(function (context) {
      var key = String(context.key || "");
      var rows = grouped[key] || [];
      return [
        '<div class="ontology-context-class-group">',
        '<strong>' + escapeHtml(context.label || key) + '</strong>',
        '<span>' + escapeHtml(context.description || "") + '</span>',
        '<div class="ontology-class-grid">',
        rows.slice(0, 18).map(function (item) {
          return '<em>' + escapeHtml(item.label || item.name) + '</em>';
        }).join("") || '<em>분류 없음</em>',
        rows.length > 18 ? '<em>+' + escapeHtml(rows.length - 18) + '</em>' : '',
        '</div>',
        '</div>'
      ].join("");
    }).join("") + '</div>' : '<div class="ontology-class-grid">' + (classes.length ? classes.map(function (item) {
      return '<span>' + escapeHtml(item) + '</span>';
    }).join("") : '<span>등록된 규칙 분류 없음</span>') + '</div>',
    '</section>'
  ].join("");
}

function renderOntologyAboxPanel(abox, aboxEntities, evidence, beliefs, opinions) {
  var counts = ontologyEntityCounts(aboxEntities);
  return [
    '<section class="ontology-surface ontology-abox-surface">',
    '<div class="ontology-surface-head">',
    '<strong>현재 데이터</strong>',
    '<span>' + escapeHtml(abox.entityCount || aboxEntities.length || 0) + ' 데이터 · ' + escapeHtml(abox.beliefCount || beliefs.length || 0) + ' 판단 근거</span>',
    '</div>',
    renderOntologyDistribution(counts, "데이터 구성"),
    '<div class="ontology-abox-metrics">',
    renderOntologyMiniMetric("근거", evidence.length),
    renderOntologyMiniMetric("판단 근거", beliefs.length),
    renderOntologyMiniMetric("AI 의견", opinions.length),
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyRelationalProjectionPanel(entities, relations, evidence, beliefs, opinions, parts) {
  parts = parts || {};
  var rows = [
    { name: "데이터 행", count: entities.length, key: "id", fk: "규칙 구조/현재 데이터 구분" },
    { name: "현재 데이터 행", count: (parts.aboxEntities || []).length || ontologyAboxEntities(entities).length, key: "ABox id", fk: "실제 계정·종목·관측값" },
    { name: "관계 행", count: relations.length, key: "source + type + target", fk: "출발점,도착점 -> 데이터 id" },
    { name: "현재 관계 행", count: (parts.aboxRelations || []).length || ontologyAboxRelations(relations).length, key: "ABox relation", fk: "현재 데이터 그래프 edge" },
    { name: "근거 행", count: evidence.length, key: "id", fk: "대상 -> 데이터 id" },
    { name: "판단 근거 행", count: beliefs.length, key: "id", fk: "대상 -> 데이터 id" },
    { name: "AI 의견 행", count: opinions.length, key: "회사 표시명", fk: "회사명 -> 종목 데이터" },
    { name: "실행 계획 행", count: (parts.executionPlans || []).length, key: "symbol + action", fk: "AI 의견 -> 실행 계획" },
    { name: "인사이트 행", count: (parts.insights || []).length, key: "subject + type", fk: "관계 변화 -> 알림 후보" }
  ];
  return [
    '<section class="ontology-surface ontology-projection-surface">',
    '<div class="ontology-surface-head">',
    '<strong>테이블 저장 구조</strong>',
    '<span>운영 DB 관점 · 규칙 구조와 현재 데이터를 행 단위로 표시</span>',
    '</div>',
    '<div class="ontology-projection-grid">',
    rows.map(renderOntologyProjectionRow).join(""),
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyProjectionRow(row) {
  return [
    '<div class="ontology-projection-row"' + cardTypeAttrs("source-card") + '>',
    '<strong>' + escapeHtml(row.name) + '</strong>',
    '<span>PK ' + escapeHtml(row.key) + '</span>',
    '<span>' + escapeHtml(row.fk) + '</span>',
    '<em>' + escapeHtml(row.count) + '</em>',
    '</div>'
  ].join("");
}

function renderOntologyMiniMetric(label, value) {
  return '<span' + cardTypeAttrs("metric-cell") + '><em>' + escapeHtml(label) + '</em><strong>' + escapeHtml(value) + '</strong></span>';
}

function renderOntologyDistribution(counts, label) {
  var entries = ontologyTopEntries(counts, 8);
  if (!entries.length) return '<div class="ontology-empty">' + escapeHtml(label) + ' 없음</div>';
  var max = entries.reduce(function (current, item) {
    return Math.max(current, Number(item.value || 0));
  }, 1);
  return [
    '<div class="ontology-distribution" aria-label="' + escapeHtml(label) + '">',
    entries.map(function (item) {
      var width = Math.max(8, Math.round((Number(item.value || 0) / max) * 100));
      return [
        '<div class="ontology-distribution-row"' + cardTypeAttrs("diagnostic-card") + '>',
        '<span>' + escapeHtml(item.key) + '</span>',
        '<b><i style="width:' + escapeHtml(width) + '%"></i></b>',
        '<em>' + escapeHtml(item.value) + '</em>',
        '</div>'
      ].join("");
    }).join(""),
    '</div>'
  ].join("");
}

function renderOntologyRelationPanel(tbox, relations, aboxRelations, relationCounts, entityLabels) {
  var relationTypes = (tbox.relationTypes || []).slice();
  ontologyTopEntries(relationCounts, 20).forEach(function (item) {
    if (relationTypes.indexOf(item.key) < 0) relationTypes.push(item.key);
  });
  return [
    '<section class="ontology-surface ontology-relation-surface">',
    '<div class="ontology-surface-head">',
    '<strong>TBox Relation Constraints</strong>',
    '<span>' + escapeHtml(aboxRelations.length) + ' ABox relation rows · ' + escapeHtml(relations.length) + ' total rows</span>',
    '</div>',
    '<div class="ontology-relation-table">',
    relationTypes.slice(0, 18).map(function (type) {
      return renderOntologyRelationRow(type, relationCounts[type] || 0, aboxRelations, entityLabels);
    }).join(""),
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyRelationRow(type, count, relations, entityLabels) {
  var sample = (relations || []).filter(function (item) {
    return ontologyTypeOf(item) === type;
  })[0] || {};
  var source = ontologyEndpointLabel(sample.source, entityLabels);
  var target = ontologyEndpointLabel(sample.target, entityLabels);
  var example = sample.source ? source + ' → ' + target : "TBox declared only";
  return [
    '<div class="ontology-relation-row ' + (count ? "active" : "empty") + '"' + cardTypeAttrs("relationship-card", count ? "watch" : "hold") + '>',
    '<strong>' + escapeHtml(type) + '</strong>',
    '<span>' + escapeHtml(example) + '</span>',
    '<em>' + escapeHtml(count) + '</em>',
    '</div>'
  ].join("");
}

function renderOntologyRulePanel(tbox, relationCounts, evidence, beliefs, opinions) {
  var rules = tbox.reasoningRules || [];
  return [
    '<section class="ontology-surface ontology-rule-surface ontology-rule-trace-surface">',
    '<div class="ontology-surface-head">',
    '<strong>규칙 추적</strong>',
    '<span>' + escapeHtml(rules.length) + ' 규칙 -> 현재 데이터에서 계산된 행</span>',
    '</div>',
    '<div class="ontology-rule-list ontology-rule-trace-list">',
    rules.length ? rules.map(function (rule, index) {
      var trace = ontologyRuleTrace(rule, index, relationCounts, evidence, beliefs, opinions);
      return [
        '<div class="ontology-rule-row ontology-rule-trace-row"' + cardTypeAttrs("relationship-card") + '>',
        '<b>' + escapeHtml(index + 1) + '</b>',
        '<span class="ontology-rule-body">',
        '<strong>' + escapeHtml(rule) + '</strong>',
        '<em>input: ' + escapeHtml(trace.input) + '</em>',
        '<em>constraint: ' + escapeHtml(trace.relation) + '</em>',
        '<em>output: ' + escapeHtml(trace.output) + '</em>',
        '</span>',
        '<i>' + escapeHtml(trace.rows) + '</i>',
        '</div>'
      ].join("");
    }).join("") : '<div class="ontology-empty">reasoning rule 없음</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderLabStat(label, value, suffix) {
  return [
    '<span>',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value) + escapeHtml(suffix || "") + '</strong>',
    '</span>'
  ].join("");
}

export { destroyOntologyCytoscapeGraphs, fitOntologyGraph, initOntologyCytoscapeGraphs, layoutOntologyGraph, ontologyAboxEntities, ontologyAboxRelations, ontologyAboxSymbolFromId, ontologyBoxOf, ontologyBuildWorldGraph, ontologyDecisionChainActiveRow, ontologyDecisionChainRows, ontologyEdgeLabel, ontologyEndpointLabel, ontologyEntityDisplayLabel, ontologyEntityGraphLabel, ontologyEntityLabelMap, ontologyIsTboxItem, ontologyOpinionOf, ontologyRelationCounts, ontologyTypeOf, renderLabStat, renderOntologyAboxPanel, renderOntologyClassPanel, renderOntologyGraphExpandedOverlay, renderOntologyRelationPanel, renderOntologyRelationalProjectionPanel, renderOntologyRulePanel };
