import { decisionStateMeta } from "../decisions/signals.mjs";
import { stockDisplayName } from "../instruments/catalog.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { normalizeOntologyWorldDepth, normalizeOntologyWorldLens } from "../navigation/routes.mjs";
import { ontologyAboxSymbolFromId, ontologyBoxOf, ontologyBuildWorldGraph, ontologyDecisionChainActiveRow, ontologyDecisionChainRows, ontologyEdgeLabel, ontologyEndpointLabel, ontologyEntityDisplayLabel, ontologyEntityGraphLabel, ontologyTypeOf, renderOntologyAboxPanel, renderOntologyClassPanel } from "./graphs.mjs";
import { snapshotHasFullOntologyDetail } from "./requests.mjs";
import { formatClock, latestChangedFirst, recordChangedAt, recordChangedAtValue, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { shellState } from "../state/shell.mjs";

function ontologyRuleboxRules() {
  var payload = ontologyState.ontologyRulebox || {};
  return Array.isArray(payload.rules) ? payload.rules : [];
}

function ontologyRuleId(rule) {
  return String(rule && (rule.rule_id || rule.ruleId || rule.id) || "");
}

function ontologyRuleConditions(rule) {
  return Array.isArray(rule && rule.conditions) ? rule.conditions : [];
}

function ontologyRuleDerivations(rule) {
  return Array.isArray(rule && rule.derivations) ? rule.derivations : [];
}

function ontologyRuleRelationTypes(rule) {
  var types = [];
  ontologyRuleConditions(rule).forEach(function (condition) {
    var type = String(condition.relation_type || condition.relationType || "").toUpperCase();
    if (type && types.indexOf(type) < 0) types.push(type);
  });
  ontologyRuleDerivations(rule).forEach(function (derivation) {
    var type = String(derivation.relation_type || derivation.relationType || "").toUpperCase();
    if (type && types.indexOf(type) < 0) types.push(type);
  });
  return types;
}

function ontologyReadableRuleRows(parts) {
  var rules = ontologyRuleboxRules();
  if (!rules.length) {
    return latestChangedFirst(((((parts || {}).tbox || {}).reasoningRuleDefinitions || [])).map(function (item, index) {
      return {
        id: "tbox-rule-" + index,
        label: item.text || item.label || "TBox reasoning rule",
        detail: item.bounded_context || item.boundedContext || "reasoning-insight",
        relationTypes: [],
        conditionCount: 0,
        derivationCount: 0,
        updatedAt: recordChangedAt(item)
      };
    })).slice(0, 8);
  }
  var rows = rules.slice().sort(function (a, b) {
    var priority = String(b.action_level || b.actionLevel || "").localeCompare(String(a.action_level || a.actionLevel || ""));
    return priority || ontologyRuleId(a).localeCompare(ontologyRuleId(b));
  }).map(function (rule) {
    return {
      id: ontologyRuleId(rule),
      label: rule.label || ontologyRuleId(rule) || "RuleBox rule",
      detail: [rule.action_group || rule.actionGroup, rule.action_level || rule.actionLevel, rule.prompt_hint || rule.promptHint].filter(Boolean).join(" · "),
      relationTypes: ontologyRuleRelationTypes(rule),
      conditionCount: ontologyRuleConditions(rule).length,
      derivationCount: ontologyRuleDerivations(rule).length,
      updatedAt: recordChangedAt(rule)
    };
  });
  return latestChangedFirst(rows).slice(0, 10);
}

function ontologyRelationPriority(type) {
  var order = [
    "HAS_INFERRED_RISK",
    "HAS_INFERRED_SUPPORT",
    "HAS_INFERRED_ENTRY_OPPORTUNITY",
    "CREATES_NOTIFICATION_INTENT",
    "REQUIRES_NEXT_CHECK",
    "DERIVES_TREND_EPISODE",
    "AFFECTS_DECISION_EPISODE",
    "HAS_TREND_TRANSITION",
    "HAS_TEMPORAL_WINDOW",
    "HAS_PRICE_PATH_PATTERN",
    "HAS_FLOW_PATTERN",
    "HAS_EVENT_CLUSTER",
    "CHANGES_OVER_WINDOW",
    "CONFIRMED_ACROSS_WINDOW",
    "DIVERGES_ACROSS_WINDOW",
    "HAS_EXTERNAL_SIGNAL",
    "HAS_DATA_QUALITY",
    "HAS_TRADE_FLOW",
    "BREAKS_LEVEL",
    "RETESTS_LEVEL",
    "RECLAIMS_LEVEL",
    "HOLDS",
    "WATCHES",
    "HAS_OPINION",
    "HAS_EVIDENCE"
  ];
  var index = order.indexOf(String(type || "").toUpperCase());
  return index < 0 ? 999 : index;
}

function ontologyReadableRelationRows(parts) {
  parts = parts || {};
  var labels = parts.entityLabels || {};
  var groups = {};
  (parts.aboxRelations || []).forEach(function (relation) {
    var type = ontologyTypeOf(relation) || "RELATED_TO";
    if (!groups[type]) groups[type] = { type: type, count: 0, examples: [], updatedAt: "" };
    groups[type].count += 1;
    if (recordChangedAtValue(relation) > recordChangedAtValue(groups[type].updatedAt)) {
      groups[type].updatedAt = recordChangedAt(relation);
    }
    if (groups[type].examples.length < 3) {
      groups[type].examples.push(ontologyEndpointLabel(relation.source, labels) + " → " + ontologyEndpointLabel(relation.target, labels));
    }
  });
  var groupedRows = Object.keys(groups).map(function (key) {
    return groups[key];
  }).sort(function (a, b) {
    var priority = ontologyRelationPriority(a.type) - ontologyRelationPriority(b.type);
    if (priority !== 0) return priority;
    if (b.count !== a.count) return b.count - a.count;
    return a.type.localeCompare(b.type);
  });
  return latestChangedFirst(groupedRows).slice(0, 12);
}

function ontologyInferenceRelationTypes() {
  return {
    HAS_INFERRED_RISK: true,
    HAS_INFERRED_SUPPORT: true,
    HAS_INFERRED_ENTRY_OPPORTUNITY: true,
    HAS_ACTION_CANDIDATE: true,
    CREATES_NOTIFICATION_INTENT: true,
    REQUIRES_NEXT_CHECK: true,
    HAS_INFERENCE_TRACE: true,
    DERIVES_TREND_EPISODE: true,
    AFFECTS_DECISION_EPISODE: true
  };
}

function ontologyReadableInferenceRows(parts) {
  parts = parts || {};
  var labels = parts.entityLabels || {};
  var inferenceTypes = ontologyInferenceRelationTypes();
  var rows = latestChangedFirst((parts.relations || parts.aboxRelations || []).filter(function (relation) {
    var type = ontologyTypeOf(relation);
    return inferenceTypes[type] || ontologyBoxOf(relation) === "INFERENCEBOX";
  })).slice(0, 10).map(function (relation) {
    var props = relation.properties || {};
    return {
      type: ontologyTypeOf(relation),
      source: ontologyEndpointLabel(relation.source, labels),
      target: ontologyEndpointLabel(relation.target, labels),
      detail: [props.aiInfluenceLabel, props.decisionStage, props.ruleId].filter(Boolean).join(" · "),
      stateLabel: decisionStateMeta("evidence", props.evidenceRole || props.evidence_role, "context").label,
      updatedAt: recordChangedAt(relation)
    };
  });
  if (rows.length) return rows;
  return latestChangedFirst(parts.insights || []).slice(0, 6).map(function (item) {
    var props = item.properties || {};
    return {
      type: "INSIGHT",
      source: props.symbol ? stockDisplayName(props.symbol) : "ontology",
      target: ontologyEntityDisplayLabel(item, item && item.id),
      detail: [props.insightType, props.severity].filter(Boolean).join(" · "),
      stateLabel: decisionStateMeta("review", props.reviewLevel || props.review_level, "observe").label,
      updatedAt: recordChangedAt(item)
    };
  });
}

function renderInvestmentRuleRelationTextPanel(parts) {
  var ruleRows = ontologyReadableRuleRows(parts);
  var relationRows = ontologyReadableRelationRows(parts);
  var inferenceRows = ontologyReadableInferenceRows(parts);
  return [
    '<section class="ontology-surface investment-rule-relation-text-panel">',
    '<div class="ontology-surface-head">',
    '<div>',
    '<strong>규칙과 관계 해설</strong>',
    '<span>그래프를 읽기 전에 RuleBox 조건, 현재 관계 행, 추론 출력을 압축해서 확인합니다.</span>',
    '</div>',
    '<span>' + escapeHtml(ruleRows.length) + ' rules · ' + escapeHtml(relationRows.length) + ' relation groups</span>',
    '</div>',
    '<div class="investment-relation-text-grid">',
    renderInvestmentRuleTextColumn("RuleBox 규칙", "조건과 파생 관계", ruleRows, renderInvestmentRuleTextRow),
    renderInvestmentRuleTextColumn("현재 ABox 관계", "실제 데이터 관계 묶음", relationRows, renderInvestmentRelationTextRow),
    renderInvestmentRuleTextColumn("InferenceBox 출력", "AI 판단으로 넘어가는 추론", inferenceRows, renderInvestmentInferenceTextRow),
    '</div>',
    '</section>'
  ].join("");
}

function investmentOntologyLayerItems(parts) {
  parts = parts || {};
  return [
    { id: "tbox", label: "TBox", caption: "규칙 어휘", count: (parts.tboxEntities || []).length || ((parts.tbox || {}).classDefinitions || []).length || 0 },
    { id: "abox", label: "ABox", caption: "현재 사실", count: (parts.aboxEntities || []).length || 0 },
    { id: "inference", label: "InferenceBox", caption: "추론 출력", count: ontologyReadableInferenceRows(parts).length },
    { id: "rulebox", label: "RuleBox", caption: "전략 규칙", count: ontologyReadableRuleRows(parts).length }
  ];
}

function renderInvestmentOntologyWorkspacePanel(snapshot, parts) {
  if (((parts.strategy || {}).detailLevel === "summary" || !snapshotHasFullOntologyDetail(snapshot)) && ontologyState.ontologyStrategyDetailLoading) {
    return renderOntologyWorldLoadingState(snapshot);
  }
  var lensId = normalizeOntologyWorldLens(ontologyState.activeOntologyWorldLens);
  ontologyState.activeOntologyWorldLens = lensId;
  ontologyState.activeOntologyWorldDepth = normalizeOntologyWorldDepth(ontologyState.activeOntologyWorldDepth);
  var focusItems = ontologyWorldFocusItems(parts, snapshot);
  var focusId = ontologyResolveWorldFocusId(parts, snapshot, focusItems);
  ontologyState.activeOntologyWorldFocusId = focusId;
  var lens = ontologyWorldLensDefinition(lensId);
  var graph = ontologyBuildWorldGraph(parts, snapshot, lensId);
  var summary = ontologyWorldSummary(parts, graph);
  return [
    '<article class="investment-ontology-world" data-ontology-world-lens-active="' + escapeHtml(lensId) + '">',
    '<header class="ontology-world-command">',
    '<div>',
    '<p class="label">Reality Intelligence</p>',
    '<h2>실세계 의사결정 지도</h2>',
    '<p>선택 종목의 실데이터가 근거와 규칙을 거쳐 판단으로 이어지는 경로를 추적합니다.</p>',
    '</div>',
    '<span class="ontology-world-asof">기준 ' + escapeHtml(formatClock(snapshot.generatedAt)) + '</span>',
    '</header>',
    renderOntologyWorldMetrics(summary),
    '<nav class="ontology-world-lenses" aria-label="실세계 의사결정 지도 렌즈">',
    ontologyWorldLensItems(parts, snapshot).map(function (item) {
      var active = item.id === lensId;
      return '<button type="button" class="' + (active ? 'active' : '') + '" data-ontology-world-lens="' + escapeHtml(item.id) + '" aria-pressed="' + (active ? 'true' : 'false') + '"><strong>' + escapeHtml(item.label) + '</strong><span>' + escapeHtml(item.count) + '</span></button>';
    }).join(""),
    '</nav>',
    '<section class="ontology-world-stage">',
    '<div class="ontology-world-stage-head">',
    '<div><span>' + escapeHtml(lens.eyebrow) + '</span><strong>' + escapeHtml(lens.title) + '</strong><p>' + escapeHtml(lens.description) + '</p></div>',
    '<div class="ontology-world-stage-controls">',
    '<label class="ontology-world-focus"><span>중심 종목</span><select data-ontology-world-focus aria-label="온톨로지 그래프 중심 종목">',
    focusItems.map(function (item) { return '<option value="' + escapeHtml(item.id) + '"' + (item.id === focusId ? ' selected' : '') + '>' + escapeHtml(item.label) + '</option>'; }).join(""),
    '</select></label>',
    '<div class="ontology-world-depth" role="group" aria-label="관계 탐색 깊이"><span>관계 범위</span><div><button type="button" data-ontology-world-depth="1" class="' + (ontologyState.activeOntologyWorldDepth === 1 ? 'active' : '') + '" aria-pressed="' + (ontologyState.activeOntologyWorldDepth === 1 ? 'true' : 'false') + '">1단계</button><button type="button" data-ontology-world-depth="2" class="' + (ontologyState.activeOntologyWorldDepth === 2 ? 'active' : '') + '" aria-pressed="' + (ontologyState.activeOntologyWorldDepth === 2 ? 'true' : 'false') + '">2단계</button></div></div>',
    '<div class="ontology-world-stage-actions">',
    '<button class="icon-button" type="button" data-ontology-graph-expand="world" title="그래프 전체 화면" aria-label="그래프 전체 화면">⤢</button>',
    '<button class="icon-button" type="button" data-ontology-graph-fit="world" title="그래프 화면 맞춤" aria-label="그래프 화면 맞춤">⌖</button>',
    '<button class="icon-button" type="button" data-ontology-graph-layout="world" title="의미 계층 배치 초기화" aria-label="의미 계층 배치 초기화">↺</button>',
    '</div>',
    '</div>',
    '</div>',
    '<div class="ontology-world-legend" aria-label="그래프 범례"><span data-kind="fact">확인된 사실</span><span data-kind="inference">규칙·추론</span><span data-kind="risk">위험·결측</span><span data-kind="selection">중심 종목</span><em>' + escapeHtml(Object.keys(graph.nodesById || {}).length) + ' nodes · ' + escapeHtml((graph.edges || []).length) + ' relations</em></div>',
    renderOntologyWorldLaneHeaders(graph),
    '<div class="ontology-world-graph-shell"><div class="ontology-world-lane-grid" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div><div class="ontology-cytoscape ontology-world-cytoscape" data-ontology-cytoscape="world"><span>의사결정 토폴로지를 구성하는 중</span></div></div>',
    '</section>',
    renderOntologyWorldNodeInspector(parts, snapshot, graph),
    renderOntologyWorldCausalPath(parts, snapshot),
    '<div class="ontology-world-intelligence-grid">',
    renderOntologyWorldInsightBrief(parts, snapshot),
    renderOntologyWorldChangeLedger(parts),
    '</div>',
    '<div class="ontology-world-ledger-grid">',
    renderOntologyWorldEvidenceLedger(parts),
    renderOntologyWorldRiskLedger(parts, snapshot),
    '</div>',
    renderOntologyWorldToolRail(parts),
    '</article>'
  ].join("");
}

function renderOntologyWorldLoadingState(snapshot) {
  return [
    '<article class="investment-ontology-world ontology-world-loading" aria-busy="true">',
    '<header class="ontology-world-command"><div><p class="label">Reality Intelligence</p><h2>실세계 의사결정 지도</h2><p>온톨로지 상세 데이터를 동기화하고 있습니다.</p></div><span class="ontology-world-asof">기준 ' + escapeHtml(formatClock(snapshot.generatedAt)) + '</span></header>',
    '<div class="ontology-world-loading-metrics" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span></div>',
    '<section class="ontology-world-loading-stage" aria-label="실세계 그래프 로딩 중">',
    '<div><span></span><strong></strong><em></em></div>',
    '<div class="ontology-world-loading-graph"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>',
    '</section>',
    '</article>'
  ].join("");
}

function ontologyWorldLensDefinition(value) {
  var id = normalizeOntologyWorldLens(value);
  var definitions = {
    reality: { id: "reality", label: "전체 흐름", eyebrow: "Decision Topology", title: "실세계 의사결정 토폴로지", description: "실데이터에서 판단과 알림까지 이어지는 현재 종목의 의미 경로를 봅니다." },
    portfolio: { id: "portfolio", label: "포트폴리오", eyebrow: "Portfolio Exposure", title: "보유·관심·노출 관계", description: "계좌에서 종목, 업종, 시장, 환율과 금리로 이어지는 실제 노출만 봅니다." },
    risk: { id: "risk", label: "위험", eyebrow: "Risk Transmission", title: "위험 전이 지도", description: "손실, 추세 이탈, 데이터 결측과 알림 후보가 어디에서 시작되는지 추적합니다." },
    inference: { id: "inference", label: "추론", eyebrow: "Inference Path", title: "TypeDB 추론 지도", description: "근거가 RuleBox와 InferenceBox를 거쳐 투자 의견으로 파생되는 경로입니다." },
    evidence: { id: "evidence", label: "근거", eyebrow: "Evidence Provenance", title: "근거·출처 지도", description: "뉴스, 공시, 시장 데이터와 출처 품질이 어떤 판단에 사용됐는지 확인합니다." }
  };
  return definitions[id];
}

function ontologyWorldLensItems(parts, snapshot) {
  return ["reality", "portfolio", "risk", "inference", "evidence"].map(function (id) {
    var definition = ontologyWorldLensDefinition(id);
    var graph = ontologyBuildWorldGraph(parts, snapshot, id);
    return Object.assign({}, definition, { count: Object.keys(graph.nodesById || {}).length });
  });
}

function ontologyWorldFocusItems(parts, snapshot) {
  parts = parts || {};
  var rows = [];
  var seen = {};
  var activeSymbols = ontologyDecisionChainRows(parts, snapshot || shellState.snapshot || {}).map(function (item) {
    return String(item && item.symbol || "").toUpperCase();
  }).filter(Boolean);
  (parts.aboxEntities || []).forEach(function (entity) {
    if (String(entity && entity.kind || "") !== "stock") return;
    var properties = entity.properties || {};
    var symbol = String(properties.symbol || ontologyAboxSymbolFromId(entity.id) || "").toUpperCase();
    var id = String(entity.id || (symbol ? "stock:" + symbol : ""));
    if (!id || seen[id]) return;
    seen[id] = true;
    rows.push({ id: id, symbol: symbol, label: ontologyEntityGraphLabel(entity) + (symbol ? " · " + symbol : "") });
  });
  (parts.opinions || []).forEach(function (opinion) {
    var symbol = String(opinion && opinion.symbol || "").toUpperCase();
    var id = symbol ? "stock:" + symbol : "";
    if (!id || seen[id]) return;
    seen[id] = true;
    rows.push({ id: id, symbol: symbol, label: stockDisplayName(symbol) + " · " + symbol });
  });
  return rows.sort(function (a, b) {
    var activeA = activeSymbols.indexOf(a.symbol);
    var activeB = activeSymbols.indexOf(b.symbol);
    if (activeA >= 0 || activeB >= 0) {
      if (activeA < 0) return 1;
      if (activeB < 0) return -1;
      if (activeA !== activeB) return activeA - activeB;
    }
    return String(a.label).localeCompare(String(b.label));
  });
}

function ontologyResolveWorldFocusId(parts, snapshot, items) {
  var rows = items || ontologyWorldFocusItems(parts, snapshot);
  var requested = String(ontologyState.activeOntologyWorldFocusId || "");
  if (rows.some(function (item) { return item.id === requested; })) return requested;
  var activeChain = ontologyDecisionChainActiveRow(ontologyDecisionChainRows(parts || {}, snapshot || shellState.snapshot || {}));
  var activeId = activeChain && activeChain.symbol ? "stock:" + String(activeChain.symbol).toUpperCase() : "";
  if (activeId && rows.some(function (item) { return item.id === activeId; })) return activeId;
  return rows.length ? rows[0].id : "";
}

function renderOntologyWorldLaneHeaders(graph) {
  var counts = {};
  Object.keys((graph || {}).nodesById || {}).forEach(function (id) {
    var lane = String(graph.nodesById[id].lane || "reality");
    counts[lane] = Number(counts[lane] || 0) + 1;
  });
  var lanes = [
    ["reality", "01", "실세계", "계좌·시장"],
    ["evidence", "02", "관측·근거", "데이터·출처"],
    ["rule", "03", "규칙·정책", "RuleBox"],
    ["inference", "04", "추론·위험", "InferenceBox"],
    ["decision", "05", "판단·실행", "의견·알림"]
  ];
  return '<div class="ontology-world-lane-heads" aria-label="온톨로지 의미 계층">' + lanes.map(function (lane) {
    return '<div data-lane="' + lane[0] + '"><span>' + lane[1] + '</span><strong>' + lane[2] + '</strong><em>' + lane[3] + ' · ' + Number(counts[lane[0]] || 0) + '</em></div>';
  }).join("") + '</div>';
}

function ontologyWorldQualityNodes(parts) {
  return Array.isArray(parts.dataQuality) && parts.dataQuality.length
    ? parts.dataQuality
    : (parts.aboxEntities || []).filter(function (item) {
      return ["data-quality", "data-freshness", "provenance", "source-reliability", "missing-data", "temporal-coverage-gap"].indexOf(String(item && item.kind || "")) >= 0;
    });
}

function ontologyWorldSummary(parts, graph) {
  return {
    facts: (parts.aboxEntities || []).length,
    relations: (parts.aboxRelations || []).length,
    inference: ontologyReadableInferenceRows(parts).length,
    risk: ontologyWorldQualityNodes(parts).length + ontologyDecisionChainRows(parts, shellState.snapshot || {}).filter(function (row) { return row.inferenceTone === "caution" || row.tone === "danger"; }).length,
    visible: Object.keys((graph || {}).nodesById || {}).length
  };
}

function renderOntologyWorldMetrics(summary) {
  var rows = [
    ["ABox 사실", summary.facts, "현재 데이터"],
    ["관계", summary.relations, "확인·파생"],
    ["InferenceBox", summary.inference, "활성 추론"],
    ["점검 필요", summary.risk, "위험·품질"],
    ["현재 표시", summary.visible, "렌즈 노드"]
  ];
  return '<dl class="ontology-world-metrics">' + rows.map(function (row) {
    return '<div><dt>' + escapeHtml(row[0]) + '</dt><dd>' + escapeHtml(row[1]) + '</dd><span>' + escapeHtml(row[2]) + '</span></div>';
  }).join("") + '</dl>';
}

function ontologyWorldSelectedNode(parts, graph) {
  var nodes = (graph || {}).nodesById || {};
  var selectedId = String(ontologyState.activeOntologyWorldNodeId || "");
  if (!nodes[selectedId]) {
    selectedId = Object.keys(nodes).filter(function (id) { return String(nodes[id].kind || "") === "stock"; })[0] || Object.keys(nodes)[0] || "";
  }
  var entity = (parts.aboxEntities || []).filter(function (item) { return String(item && item.id || "") === selectedId; })[0] || {};
  return { id: selectedId, node: nodes[selectedId] || {}, entity: entity };
}

function ontologyWorldNodeKindLabel(kind) {
  var labels = {
    portfolio: "포트폴리오", stock: "종목", sector: "업종", market: "시장", cash: "현금", risk: "위험",
    evidence: "근거 묶음", belief: "판단 근거", opinion: "투자 의견", rule: "규칙", "missing-data": "결측 데이터",
    "data-quality": "데이터 품질", "source-reliability": "출처 신뢰도", "alert-candidate": "알림 후보", "next-check": "다음 확인"
  };
  return labels[String(kind || "")] || String(kind || "entity").replace(/-/g, " ");
}

function ontologyWorldPropertyRows(entity) {
  var properties = (entity && entity.properties) || {};
  var preferred = ["symbol", "name", "status", "provider", "source", "reviewLevel", "dataState", "changeState", "conflictState", "validationState", "evidenceRole", "value", "rate", "ageMinutes"];
  return preferred.filter(function (key) {
    var value = properties[key];
    return value != null && value !== "" && typeof value !== "object";
  }).slice(0, 4).map(function (key) {
    return { key: key, value: properties[key] };
  });
}

function renderOntologyWorldNodeInspector(parts, snapshot, graph) {
  var selected = ontologyWorldSelectedNode(parts, graph);
  if (!selected.id) return '<section class="ontology-world-inspector" data-ontology-world-inspector><span>그래프에서 대상을 선택하면 연결 정보를 표시합니다.</span></section>';
  var relations = (graph.edges || []).filter(function (edge) { return edge.source === selected.id || edge.target === selected.id; });
  var properties = ontologyWorldPropertyRows(selected.entity);
  var connected = relations.slice(0, 5).map(function (edge) {
    var otherId = edge.source === selected.id ? edge.target : edge.source;
    var other = graph.nodesById[otherId] || {};
    return '<span><b>' + escapeHtml(ontologyEdgeLabel(edge.type)) + '</b>' + escapeHtml(other.label || otherId) + '</span>';
  }).join("");
  return [
    '<section class="ontology-world-inspector" data-ontology-world-inspector>',
    '<div class="ontology-world-inspector-identity"><span>' + escapeHtml(ontologyWorldNodeKindLabel(selected.node.kind)) + '</span><strong>' + escapeHtml(selected.node.label || selected.id) + '</strong><p>' + escapeHtml(selected.node.title || "현재 그래프 개체") + '</p></div>',
    '<div class="ontology-world-inspector-relations"><span>직접 연결 ' + escapeHtml(relations.length) + '</span><div>' + (connected || '<em>직접 연결 없음</em>') + '</div></div>',
    '<dl class="ontology-world-inspector-properties">',
    properties.length ? properties.map(function (item) { return '<div><dt>' + escapeHtml(item.key) + '</dt><dd>' + escapeHtml(item.value) + '</dd></div>'; }).join("") : '<div><dt>graph id</dt><dd>' + escapeHtml(selected.id) + '</dd></div>',
    '</dl>',
    '</section>'
  ].join("");
}

function renderOntologyWorldCausalPath(parts, snapshot) {
  var rows = ontologyDecisionChainRows(parts, snapshot);
  var active = ontologyDecisionChainActiveRow(rows);
  if (!active) return '<section class="ontology-world-causal"><div class="ontology-empty">표시할 판단 경로가 없습니다.</div></section>';
  var stages = [
    ["01", "실데이터", active.dataLabel, active.dataDetail],
    ["02", "관계", active.relationLabel, active.relationDetail],
    ["03", "RuleBox", active.ruleLabel, active.ruleDetail],
    ["04", "InferenceBox", active.inferenceLabel, active.inferenceDetail],
    ["05", "판단", active.actionLabel, active.actionDetail],
    ["06", "알림", active.alertLabel, active.alertDetail],
    ["07", "성과", active.performanceLabel, active.performanceDetail]
  ];
  return [
    '<section class="ontology-world-causal">',
    '<header><div><span>Decision Lineage</span><strong>판단 경로</strong></div>',
    '<label><span>분석 대상</span><select data-ontology-world-chain>',
    rows.map(function (row) { return '<option value="' + escapeHtml(row.key) + '"' + (row.key === active.key ? ' selected' : '') + '>' + escapeHtml(row.displayName || row.symbol || "판단 체인") + '</option>'; }).join(""),
    '</select></label></header>',
    '<ol class="ontology-world-causal-rail">',
    stages.map(function (stage) { return '<li><b>' + escapeHtml(stage[0]) + '</b><span>' + escapeHtml(stage[1]) + '</span><strong>' + escapeHtml(stage[2] || "-") + '</strong><p>' + escapeHtml(stage[3] || "세부 정보 대기") + '</p></li>'; }).join(""),
    '</ol>',
    '</section>'
  ].join("");
}

function renderOntologyWorldInsightBrief(parts, snapshot) {
  var active = ontologyDecisionChainActiveRow(ontologyDecisionChainRows(parts, snapshot));
  if (!active) return '<section class="ontology-intelligence-brief"><div class="ontology-empty">생성된 판단 브리프가 없습니다.</div></section>';
  return [
    '<section class="ontology-intelligence-brief">',
    '<header><span>Insight Brief</span><strong>' + escapeHtml(active.displayName || active.symbol || "투자 판단") + '</strong>',
    active.reasoningKey ? renderWorkDetailButton("investment-reasoning-card", active.reasoningKey, "전체 근거", "text-button compact") : '',
    '</header>',
    '<dl>',
    '<div><dt>확인된 사실</dt><dd>' + escapeHtml([active.dataLabel, active.relationDetail].filter(Boolean).join(" · ") || "사실 확인 중") + '</dd></div>',
    '<div><dt>그래프 추론</dt><dd>' + escapeHtml([active.inferenceLabel, active.inferenceDetail].filter(Boolean).join(" · ") || "추론 대기") + '</dd></div>',
    '<div><dt>현재 판단</dt><dd><strong>' + escapeHtml(active.actionLabel || "판단 대기") + '</strong><span>' + escapeHtml(active.actionDetail || "추가 확인 필요") + '</span></dd></div>',
    '</dl>',
    '</section>'
  ].join("");
}

function renderOntologyWorldChangeLedger(parts) {
  var inferenceRows = ontologyReadableInferenceRows(parts).slice(0, 4);
  var relationRows = ontologyReadableRelationRows(parts).slice(0, Math.max(0, 5 - inferenceRows.length));
  var rows = inferenceRows.map(function (row) {
    var role = decisionStateMeta("evidence", row.evidenceRole || "context", "context");
    return { type: "추론", title: [row.source, row.target].filter(Boolean).join(" → "), detail: [row.type, row.detail].filter(Boolean).join(" · "), value: role.label };
  }).concat(relationRows.map(function (row) {
    return { type: "관계", title: row.type, detail: (row.examples || []).join(" · "), value: row.count + " rows" };
  }));
  return [
    '<section class="ontology-change-ledger">',
    '<header><div><span>Change Ledger</span><strong>현재 변화·파생 관계</strong></div><em>' + escapeHtml(rows.length) + '</em></header>',
    '<div class="ontology-ledger-rows">',
    rows.length ? rows.map(function (row) { return '<div><span>' + escapeHtml(row.type) + '</span><p><strong>' + escapeHtml(row.title || "-") + '</strong><em>' + escapeHtml(row.detail || "세부 정보 없음") + '</em></p><b>' + escapeHtml(row.value) + '</b></div>'; }).join("") : '<div class="ontology-empty">변화 관계가 없습니다.</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyWorldEvidenceLedger(parts) {
  var rows = (parts.evidence || []).slice(0, 5);
  return [
    '<section class="ontology-evidence-ledger">',
    '<header><div><span>Evidence Ledger</span><strong>판단 근거·출처</strong></div>' + renderWorkDetailButton("strategy-evidence-board", "", "근거 전체", "text-button compact") + '</header>',
    '<div class="ontology-ledger-rows">',
    rows.length ? rows.map(function (item) {
      var value = decisionStateMeta("evidence", item.evidenceRole || item.polarity || "context", "context").label;
      return '<div><span>' + escapeHtml(item.kind || "evidence") + '</span><p><strong>' + escapeHtml(item.summary || item.label || item.id || "근거") + '</strong><em>' + escapeHtml([item.source, item.subject].filter(Boolean).join(" · ") || "온톨로지 근거") + '</em></p><b>' + escapeHtml(value) + '</b></div>';
    }).join("") : '<div class="ontology-empty">연결된 근거가 없습니다.</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyWorldRiskLedger(parts, snapshot) {
  var quality = ontologyWorldQualityNodes(parts).slice(0, 4).map(function (item) {
    var props = item.properties || {};
    var data = decisionStateMeta("data", props.dataState || props.data_state, props.status === "error" ? "unavailable" : (props.status === "stale" ? "partial" : "sufficient"));
    return { type: "품질", title: ontologyEntityDisplayLabel(item, item && item.id), detail: [item.kind, props.provider, props.status, props.ageMinutes != null ? Math.round(Number(props.ageMinutes || 0)) + "분" : ""].filter(Boolean).join(" · "), value: data.label };
  });
  var blocked = ontologyDecisionChainRows(parts, snapshot).filter(function (row) { return row.inferenceTone === "caution" || row.tone === "danger"; }).slice(0, Math.max(0, 4 - quality.length)).map(function (row) {
    return { type: "판단", title: row.displayName || row.symbol || "추론 점검", detail: row.inferenceDetail || row.actionDetail, value: row.inferenceLabel || "review" };
  });
  var rows = quality.concat(blocked);
  return [
    '<section class="ontology-risk-ledger">',
    '<header><div><span>Risk &amp; Anomaly</span><strong>위험·결측 점검</strong></div>' + renderWorkDetailButton("strategy-trace-board", "", "검증 전체", "text-button compact") + '</header>',
    '<div class="ontology-ledger-rows">',
    rows.length ? rows.map(function (row) { return '<div><span>' + escapeHtml(row.type) + '</span><p><strong>' + escapeHtml(row.title || "-") + '</strong><em>' + escapeHtml(row.detail || "추가 점검 필요") + '</em></p><b>' + escapeHtml(row.value) + '</b></div>'; }).join("") : '<div class="ontology-empty">현재 표시할 위험 또는 결측이 없습니다.</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyWorldToolRail(parts) {
  var tboxCount = (parts.tboxEntities || []).length || (((parts.tbox || {}).classDefinitions || []).length);
  var ruleCount = ontologyReadableRuleRows(parts).length;
  return [
    '<footer class="ontology-world-tools">',
    '<div><span>Structure Tools</span><strong>구조와 운영 도구는 필요할 때 전체 화면으로 엽니다.</strong></div>',
    '<div>',
    '<button class="text-button compact" type="button" data-ontology-graph-expand="tbox">TBox 구조 <span>' + escapeHtml(tboxCount) + '</span></button>',
    renderWorkDetailButton("strategy-rulebox-editor", "", "RuleBox " + ruleCount, "text-button compact"),
    renderWorkDetailButton("strategy-trace-board", "", "추론 원장", "text-button compact"),
    '</div>',
    '</footer>'
  ].join("");
}

function renderInvestmentOntologyLayerDetail(layer, parts) {
  if (layer === "tbox") return renderOntologyClassPanel(parts.tbox);
  if (layer === "abox") return renderOntologyAboxPanel(parts.abox, parts.aboxEntities, parts.evidence, parts.beliefs, parts.opinions);
  if (layer === "rulebox") return renderInvestmentRuleRelationTextPanel(parts);
  return renderInvestmentInferenceLayerPanel(parts);
}

function renderInvestmentInferenceLayerPanel(parts) {
  var rows = ontologyReadableInferenceRows(parts);
  return [
    '<section class="ontology-surface investment-inference-layer-panel">',
    '<div class="ontology-surface-head">',
    '<div>',
    '<strong>InferenceBox 출력</strong>',
    '<span>AI 의견과 알림 판단으로 넘어가는 그래프 추론 결과입니다.</span>',
    '</div>',
    '<span>' + escapeHtml(rows.length) + ' rows</span>',
    '</div>',
    '<div class="investment-relation-text-list">',
    rows.length ? rows.map(renderInvestmentInferenceTextRow).join("") : '<div class="ontology-empty">InferenceBox 행이 없습니다.</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderInvestmentRuleTextColumn(title, caption, rows, renderer) {
  return [
    '<section class="investment-relation-text-column">',
    '<div class="investment-relation-text-head">',
    '<strong>' + escapeHtml(title) + '</strong>',
    '<span>' + escapeHtml(caption) + '</span>',
    '</div>',
    '<div class="investment-relation-text-list">',
    rows.length ? rows.map(renderer).join("") : '<div class="ontology-empty">표시할 행이 없습니다.</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderInvestmentRuleTextRow(row) {
  return [
    '<div class="investment-relation-text-row rule">',
    '<strong>' + escapeHtml(row.label || row.id || "-") + '</strong>',
    '<span>' + escapeHtml(row.detail || row.id || "-") + '</span>',
    '<em>' + escapeHtml(row.conditionCount + " conditions · " + row.derivationCount + " derives") + '</em>',
    row.relationTypes.length ? '<b>' + escapeHtml(row.relationTypes.slice(0, 4).join(" / ")) + '</b>' : '',
    renderRecordChangedAt(row),
    '</div>'
  ].join("");
}

function renderInvestmentRelationTextRow(row) {
  return [
    '<div class="investment-relation-text-row relation">',
    '<strong>' + escapeHtml(row.type || "-") + '</strong>',
    '<span>' + escapeHtml((row.examples || []).join(" · ") || "-") + '</span>',
    '<em>' + escapeHtml(row.count + " rows") + '</em>',
    renderRecordChangedAt(row),
    '</div>'
  ].join("");
}

function renderInvestmentInferenceTextRow(row) {
  return [
    '<div class="investment-relation-text-row inference">',
    '<strong>' + escapeHtml(row.type || "-") + '</strong>',
    '<span>' + escapeHtml([row.source, row.target].filter(Boolean).join(" → ")) + '</span>',
    '<em>' + escapeHtml([row.detail, row.stateLabel].filter(Boolean).join(" · ") || "-") + '</em>',
    renderRecordChangedAt(row),
    '</div>'
  ].join("");
}

export { ontologyInferenceRelationTypes, ontologyReadableInferenceRows, ontologyReadableRuleRows, ontologyRelationPriority, ontologyResolveWorldFocusId, ontologyRuleboxRules, ontologyWorldFocusItems, ontologyWorldLensDefinition, renderInvestmentOntologyWorkspacePanel, renderOntologyWorldNodeInspector };
