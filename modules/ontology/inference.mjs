import { renderModelPreviewPanel } from "../decisions/legacy.mjs";
import { decisionStateMeta } from "../decisions/signals.mjs";
import { editorWorkDetailPayload } from "../navigation/detail.mjs";
import { renderNotificationDetailMetric } from "../notifications/reasoning.mjs";
import { renderOntologyDataQualityPanel, renderOntologyInsightPanel } from "./execution.mjs";
import { renderOntologyRelationPanel, renderOntologyRelationalProjectionPanel, renderOntologyRulePanel } from "./graphs.mjs";
import { renderOntologyMacroRelationPanel } from "./macro-view.mjs";
import { ontologyStrategyParts } from "./strategy.mjs";
import { formatInteger, latestChangedFirst, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { shellState } from "../state/shell.mjs";

function inferenceLedgerPayload() {
  return ontologyState.ontologyInferenceLedger && typeof ontologyState.ontologyInferenceLedger === "object" ? ontologyState.ontologyInferenceLedger : {};
}

function inferenceLedgerRows() {
  var payload = inferenceLedgerPayload();
  return latestChangedFirst(Array.isArray(payload.rows) ? payload.rows : []);
}

function inferenceLedgerSummary() {
  var payload = inferenceLedgerPayload();
  return payload.summary && typeof payload.summary === "object" ? payload.summary : {};
}

function inferenceLedgerTone(value) {
  var text = String(value || "").toLowerCase();
  if (["complete", "ok", "matched", "materialized", "linked", "within-budget"].indexOf(text) >= 0) return "watch";
  if (["review", "not-returned", "error", "critical"].indexOf(text) >= 0) return "danger";
  if (["degraded"].indexOf(text) >= 0) return "caution";
  if (["empty", "none", "not-used"].indexOf(text) >= 0) return "hold";
  return "caution";
}

function inferenceLedgerConditionText(condition) {
  condition = condition || {};
  var parts = [
    condition.field,
    condition.operator,
    condition.value !== undefined && condition.value !== null ? condition.value : "",
    condition.relationType ? "relation " + condition.relationType : "",
    condition.evidenceRelationId ? "evidence " + condition.evidenceRelationId : ""
  ].filter(function (item) { return String(item || "").trim(); });
  return parts.join(" · ") || condition.kind || condition.role || "-";
}

function reasoningExecutionStageDetail(stage) {
  stage = stage || {};
  var detail = stage.detail && typeof stage.detail === "object" ? stage.detail : {};
  var labels = [];
  var key = String(stage.stageKey || "");
  if (key === "source-fact-capture") {
    var fieldsBySymbol = detail.changedFieldsBySymbol && typeof detail.changedFieldsBySymbol === "object" ? detail.changedFieldsBySymbol : {};
    Object.keys(fieldsBySymbol).slice(0, 8).forEach(function (symbol) {
      labels.push(symbol + " 변경 필드 " + (Array.isArray(fieldsBySymbol[symbol]) ? fieldsBySymbol[symbol].length : 0) + "개");
    });
    if (Array.isArray(detail.factTypes) && detail.factTypes.length) labels.push("사실 " + detail.factTypes.join(", "));
  } else if (key === "abox-scope-selection") {
    labels.push("선택 " + formatInteger(detail.selectedScopeCount || 0) + " · 유예 " + formatInteger(detail.deferredScopeCount || 0));
    if (Array.isArray(detail.factSlotFamilies) && detail.factSlotFamilies.length) labels.push("슬롯 " + detail.factSlotFamilies.join(", "));
    (Array.isArray(detail.selectedScopes) ? detail.selectedScopes : []).slice(0, 10).forEach(function (scope) {
      labels.push([
        scope.symbol || "shared",
        scope.scopeFamily || scope.scopeId,
        (Array.isArray(scope.reasons) ? scope.reasons : []).join(", ")
      ].filter(Boolean).join(" · "));
    });
  } else if (key === "abox-persistence") {
    var scopes = Array.isArray(detail.scopes) ? detail.scopes : [];
    var totals = scopes.reduce(function (sum, scope) {
      ["requested", "inserted", "reused"].forEach(function (kind) {
        var counts = scope[kind] && typeof scope[kind] === "object" ? scope[kind] : {};
        sum[kind] += Number(counts.entityCount || 0) + Number(counts.relationCount || 0);
      });
      return sum;
    }, { requested: 0, inserted: 0, reused: 0 });
    labels.push("스코프 " + formatInteger(detail.scopeCount || scopes.length) + " · 요청 " + formatInteger(totals.requested) + " · 신규 " + formatInteger(totals.inserted) + " · 재사용 " + formatInteger(totals.reused));
    scopes.slice(0, 10).forEach(function (scope) {
      var requested = scope.requested || {};
      var inserted = scope.inserted || {};
      var reused = scope.reused || {};
      labels.push([
        scope.symbol || "shared",
        scope.scopeFamily || scope.scopeId,
        "요청 " + formatInteger(Number(requested.entityCount || 0) + Number(requested.relationCount || 0)),
        "신규 " + formatInteger(Number(inserted.entityCount || 0) + Number(inserted.relationCount || 0)),
        "재사용 " + formatInteger(Number(reused.entityCount || 0) + Number(reused.relationCount || 0))
      ].join(" · "));
    });
  } else if (key === "rulebox-selection") {
    labels.push("후보 " + formatInteger(detail.candidateRuleCount || 0) + " · 실행 " + formatInteger(detail.executedRuleCount || 0) + " · 유예 " + formatInteger(detail.deferredRuleCount || 0));
  } else if (key.indexOf("runtime:") === 0 && detail.budgetMs != null) {
    labels.push("관측 " + formatInteger(stage.durationMs || 0) + "ms · 예산 " + formatInteger(detail.budgetMs || 0) + "ms · " + Number(detail.ratio || 0).toFixed(2) + "배");
  } else if (key === "performance-contract") {
    labels.push("병목 " + (detail.bottleneckStage || "미확인") + " · 예산 대비 " + Number(detail.bottleneckRatio || 0).toFixed(2) + "배");
    (Array.isArray(detail.violations) ? detail.violations : []).slice(0, 6).forEach(function (item) {
      labels.push((item.stage || "단계") + " " + formatInteger(item.durationMs || 0) + "ms / " + formatInteger(item.budgetMs || 0) + "ms");
    });
  }
  if (!labels.length) return "";
  return '<div class="inference-ledger-relation-strip">' + labels.map(function (label) {
    return '<span class="chip">' + escapeHtml(label) + '</span>';
  }).join("") + '</div>';
}

function renderReasoningExecutionHistory(payload) {
  var history = payload.executionHistory && typeof payload.executionHistory === "object" ? payload.executionHistory : {};
  var runs = latestChangedFirst(Array.isArray(history.runs) ? history.runs : []);
  var runtime = payload.ruleRuntimeSummary && typeof payload.ruleRuntimeSummary === "object" ? payload.ruleRuntimeSummary : {};
  var slowRules = Array.isArray(runtime.rules) ? runtime.rules.slice(0, 12) : [];
  var audit = payload.ruleAudit && typeof payload.ruleAudit === "object" ? payload.ruleAudit : {};
  var auditRules = Array.isArray(audit.rules) ? audit.rules.filter(function (rule) {
    return ["observed", "disabled", "waiting-for-event", "cold-no-sample"].indexOf(String(rule.status || "")) < 0;
  }).slice(0, 24) : [];
  var auditStages = audit.executionStageCounts && typeof audit.executionStageCounts === "object" ? audit.executionStageCounts : {};
  var auditScopes = audit.assessmentScopeCounts && typeof audit.assessmentScopeCounts === "object" ? audit.assessmentScopeCounts : {};
  var auditLifecycles = audit.lifecycleClassCounts && typeof audit.lifecycleClassCounts === "object" ? audit.lifecycleClassCounts : {};
  var auditGrains = audit.evaluationGrainCounts && typeof audit.evaluationGrainCounts === "object" ? audit.evaluationGrainCounts : {};
  if (!runs.length && !slowRules.length && !auditRules.length) {
    return history.status === "error" ? '<p class="form-error">' + escapeHtml(history.reason || "실행 이력을 읽지 못했습니다.") + '</p>' : '';
  }
  return [
    '<div class="inference-ledger-coverage">',
    '<strong>Reasoning execution history</strong>',
    '<span>처리 레인, 단계, 규칙별 실행 시간과 실패를 MySQL 실행 원장에서 읽습니다.</span>',
    audit.ruleCount ? '<div class="inference-ledger-relation-strip">' + [
      "전체 " + formatInteger(audit.ruleCount),
      "critical " + formatInteger(auditStages.critical || 0),
      "core " + formatInteger(auditStages.core || 0),
      "supporting " + formatInteger(auditStages.supporting || 0)
    ].map(function (label) { return '<span class="chip">' + escapeHtml(label) + '</span>'; }).join("") + '</div>' : '',
    audit.ruleCount ? '<div class="inference-ledger-relation-strip">' + [
      "종목 의견 " + formatInteger(auditScopes["investment-opinion"] || 0),
      "근거 품질 " + formatInteger(auditScopes["evidence-quality"] || 0),
      "계좌 적합성 " + formatInteger(auditScopes["portfolio-fit"] || 0),
      "실행 가능성 " + formatInteger(auditScopes["execution-readiness"] || 0),
      "상시 " + formatInteger(auditLifecycles.hot || 0),
      "사건형 " + formatInteger(auditLifecycles["event-driven"] || 0),
      "저빈도 " + formatInteger(auditLifecycles.cold || 0)
    ].map(function (label) { return '<span class="chip">' + escapeHtml(label) + '</span>'; }).join("") + '</div>' : '',
    audit.ruleCount ? '<div class="inference-ledger-relation-strip">' + [
      "종목 단위 " + formatInteger(auditGrains.instrument || 0),
      "계좌 단위 " + formatInteger(auditGrains.account || 0),
      "거시 단위 " + formatInteger(auditGrains.macro || 0),
      "월드 단위 " + formatInteger(auditGrains.world || 0)
    ].map(function (label) { return '<span class="chip">' + escapeHtml(label) + '</span>'; }).join("") + '</div>' : '',
    auditRules.length ? '<div class="inference-ledger-relation-strip">' + auditRules.map(function (rule) {
      var profile = rule.executionProfile && typeof rule.executionProfile === "object" ? rule.executionProfile : {};
      return '<span class="chip">' + escapeHtml([
        rule.ruleId,
        rule.assessmentScope,
        rule.evaluationGrain,
        rule.ownerWorld,
        rule.lifecycleClass,
        profile.executionStage,
        rule.status,
        rule.p95DurationMs ? "p95 " + formatInteger(rule.p95DurationMs) + "ms" : ""
      ].filter(Boolean).join(" · ")) + '</span>';
    }).join("") + '</div>' : '',
    slowRules.length ? '<div class="inference-ledger-relation-strip">' + slowRules.map(function (rule) {
      var detail = [
        rule.ruleId,
        "p95 " + formatInteger(rule.p95DurationMs || 0) + "ms",
        "표본 " + formatInteger(rule.sampleCount || 0),
        rule.failureCount ? "실패 " + formatInteger(rule.failureCount) : ""
      ].filter(Boolean).join(" · ");
      return '<span class="chip">' + escapeHtml(detail) + '</span>';
    }).join("") + '</div>' : '',
    runs.map(function (run) {
      var stages = Array.isArray(run.stages) ? run.stages : [];
      var rules = Array.isArray(run.rules) ? run.rules : [];
      var failedRules = rules.filter(function (rule) {
        return ["error", "blocked", "query-timeout", "query-error"].indexOf(String(rule.status || "").toLowerCase()) >= 0;
      });
      var slow = rules.slice().sort(function (left, right) {
        return Number(right.durationMs || 0) - Number(left.durationMs || 0);
      }).slice(0, 8);
      return [
        '<article class="inference-ledger-row"' + cardTypeAttrs("execution-run", failedRules.length ? "danger" : "watch") + '>',
        '<div class="inference-ledger-row-head"><div>',
        '<span class="tone-chip ' + escapeHtml(failedRules.length ? "danger" : "watch") + '">' + escapeHtml(run.lane || "CORE_REASONING") + '</span>',
        '<strong>' + escapeHtml(run.runId || "Reasoning run") + '</strong>',
        '<em>' + escapeHtml([run.accountId, run.worldId, "규칙 " + rules.length + "개"].filter(Boolean).join(" · ")) + '</em>',
        '</div>' + renderRecordChangedAt(run) + '</div>',
        '<div class="inference-ledger-stage-rail">',
        stages.map(function (stage, index) {
          return '<section class="inference-ledger-stage ' + escapeHtml(inferenceLedgerTone(stage.status)) + '"><b>' + escapeHtml(String(index + 1).padStart(2, "0")) + '</b><div><strong>' + escapeHtml(stage.stageKey || "-") + '</strong><span>' + escapeHtml(stage.status || "-") + '</span><em>' + escapeHtml(formatInteger(stage.durationMs || 0) + "ms") + '</em></div></section>';
        }).join(""),
        '</div>',
        stages.map(reasoningExecutionStageDetail).join(""),
        slow.length ? '<div class="inference-ledger-relation-strip">' + slow.map(function (rule) {
          return '<span class="chip">' + escapeHtml([rule.ruleId, rule.status, formatInteger(rule.durationMs || 0) + "ms", rule.selectedReason].filter(Boolean).join(" · ")) + '</span>';
        }).join("") + '</div>' : '',
        failedRules.length ? '<p class="form-error">' + escapeHtml(failedRules.map(function (rule) { return rule.ruleId + ": " + (rule.failureReason || rule.status); }).join(" · ")) + '</p>' : '',
        '</article>'
      ].join("");
    }).join(""),
    '</div>'
  ].join("");
}

function renderInferenceTraceLedgerPanel() {
  var payload = inferenceLedgerPayload();
  var summary = inferenceLedgerSummary();
  var rows = inferenceLedgerRows();
  var status = payload.status || (ontologyState.ontologyInferenceLedgerLoading ? "loading" : "empty");
  return [
    '<section class="inference-ledger-panel">',
    '<div class="ontology-surface-head">',
    '<div>',
    '<strong>Inference Trace Ledger</strong>',
    '<span>TypeDB InferenceBox가 만든 trace를 입력 데이터, 조건, RuleBox, 파생 관계, 알림 의도까지 감사 원장으로 재구성합니다.</span>',
    '</div>',
    '<button class="mini-button" type="button" data-action="refresh-inference-ledger">' + escapeHtml(ontologyState.ontologyInferenceLedgerLoading ? "읽는 중" : "새로고침") + '</button>',
    '</div>',
    '<div class="work-detail-metric-row">',
    renderNotificationDetailMetric("원장 행", formatInteger(summary.ledgerCount || rows.length || 0), rows.length ? "watch" : "hold"),
    renderNotificationDetailMetric("Trace", formatInteger(summary.traceCount || 0), summary.traceCount ? "watch" : "hold"),
    renderNotificationDetailMetric("조건 매칭", formatInteger(summary.matchedConditionCount || 0) + "/" + formatInteger(summary.conditionCount || 0), summary.notReturnedConditionCount ? "caution" : "watch"),
    renderNotificationDetailMetric("Rule coverage", formatInteger(summary.matchedRuleCount || 0) + "/" + formatInteger(summary.activeRuleCount || 0), summary.matchedRuleCount ? "watch" : "hold"),
    '</div>',
    '<div class="inference-ledger-meta">',
    '<span>상태 ' + escapeHtml(status || "-") + '</span>',
    '<span>세대 ' + escapeHtml(payload.inferenceGenerationId || "-") + '</span>',
    '<span>엔진 ' + escapeHtml(payload.reasoningMode || payload.materializationSource || "-") + '</span>',
    '<span>TypeDB native ' + escapeHtml(payload.nativeTypeDbReasoningUsed ? "사용" : "미확인") + '</span>',
    '</div>',
    ontologyState.ontologyInferenceLedgerError ? '<p class="form-error">' + escapeHtml(ontologyState.ontologyInferenceLedgerError) + '</p>' : '',
    payload.reason && !rows.length ? '<p class="subtle">' + escapeHtml(payload.reason) + '</p>' : '',
    ontologyState.ontologyInferenceLedgerLoading && !rows.length ? '<div class="ontology-empty">Inference Trace Ledger를 읽는 중입니다.</div>' : '',
    rows.length ? '<div class="inference-ledger-list">' + rows.map(renderInferenceLedgerRow).join("") + '</div>' : (!ontologyState.ontologyInferenceLedgerLoading ? '<div class="ontology-empty">표시할 추론 원장이 없습니다. RuleBox 실행과 TypeDB InferenceBox 세대를 확인하세요.</div>' : ''),
    renderReasoningExecutionHistory(payload),
    renderInferenceLedgerCoverage(payload),
    '</section>'
  ].join("");
}

function renderInferenceLedgerCoverage(payload) {
  var coverage = payload.ruleCoverage || {};
  var untraced = Array.isArray(coverage.untracedRuleIds) ? coverage.untracedRuleIds : [];
  if (!untraced.length) return "";
  return [
    '<div class="inference-ledger-coverage">',
    '<strong>이번 세대에서 trace가 없는 RuleBox</strong>',
    '<span>' + escapeHtml(formatInteger(untraced.length) + "개 · coverage " + (coverage.coverageRatio == null ? "-" : coverage.coverageRatio + "%")) + '</span>',
    '<div class="chip-row">' + untraced.slice(0, 16).map(function (ruleId) {
      return '<span class="chip">' + escapeHtml(ruleId) + '</span>';
    }).join("") + '</div>',
    '</div>'
  ].join("");
}

function renderInferenceLedgerRow(row) {
  row = row || {};
  var conditions = Array.isArray(row.conditions) ? row.conditions : [];
  var stages = Array.isArray(row.stages) ? row.stages : [];
  var relations = Array.isArray(row.relations) ? row.relations : [];
  var review = decisionStateMeta("review", row.reviewLevel || row.review_level, "observe");
  var data = decisionStateMeta("data", row.dataState || row.data_state, "partial");
  var validation = decisionStateMeta("validation", row.validationState || row.validation_state, "conditional");
  return [
    '<article class="inference-ledger-row"' + cardTypeAttrs("ledger-row", inferenceLedgerTone(row.status)) + '>',
    '<div class="inference-ledger-row-head">',
    '<div>',
    '<span class="tone-chip ' + escapeHtml(inferenceLedgerTone(row.status)) + '">' + escapeHtml(row.status || "trace") + '</span>',
    '<strong>' + escapeHtml([row.symbol, row.ruleLabel || row.ruleId].filter(Boolean).join(" · ") || "Inference trace") + '</strong>',
    '<em>' + escapeHtml([row.decisionStage, row.actionPolicy, review.label, data.label, validation.label].filter(Boolean).join(" · ")) + '</em>',
    '</div>',
    renderRecordChangedAt(row),
    '</div>',
    '<div class="inference-ledger-stage-rail">',
    stages.map(function (stage, index) {
      return [
        '<section class="inference-ledger-stage ' + escapeHtml(inferenceLedgerTone(stage.status)) + '">',
        '<b>' + escapeHtml(String(index + 1).padStart(2, "0")) + '</b>',
        '<div><strong>' + escapeHtml(stage.label || "-") + '</strong><span>' + escapeHtml(stage.status || "-") + '</span><em>' + escapeHtml(stage.detail || "") + '</em></div>',
        '</section>'
      ].join("");
    }).join(""),
    '</div>',
    '<div class="inference-ledger-condition-grid">',
    conditions.length ? conditions.map(function (condition) {
      return [
        '<div class="inference-ledger-condition ' + escapeHtml(inferenceLedgerTone(condition.status)) + '">',
        '<span>' + escapeHtml(condition.status || "-") + '</span>',
        '<strong>' + escapeHtml(condition.label || condition.id || "-") + '</strong>',
        '<em>' + escapeHtml(inferenceLedgerConditionText(condition)) + '</em>',
        '</div>'
      ].join("");
    }).join("") : '<div class="ontology-empty">조건 상세가 없습니다.</div>',
    '</div>',
    relations.length ? '<div class="inference-ledger-relation-strip">' + relations.slice(0, 10).map(function (relation) {
      return '<span class="chip">' + escapeHtml([relation.type, relation.sourceLabel || relation.source, relation.targetLabel || relation.target].filter(Boolean).join(" · ")) + '</span>';
    }).join("") + '</div>' : '',
    '</article>'
  ].join("");
}

function strategyTraceWorkDetailPayload(key) {
  var snapshot = shellState.snapshot || {};
  var parts = ontologyStrategyParts(snapshot);
  if (key === "ledger") {
    return editorWorkDetailPayload(
      "Review Trace",
      "Inference Trace Ledger",
      "TypeDB 세대별 trace, 조건, 파생 관계, 알림 의도",
      renderInferenceTraceLedgerPanel()
    );
  }
  if (key === "model") {
    return editorWorkDetailPayload("Review Trace", "모델 리뷰 상세", "종목별 확인 단계와 판단 근거", renderModelPreviewPanel(snapshot));
  }
  if (key === "projection") {
    return editorWorkDetailPayload(
      "Review Trace",
      "관계 투영 상세",
      "현재 ABox 행과 근거·믿음·의견 연결",
      renderOntologyRelationalProjectionPanel(parts.entities, parts.relations, parts.evidence, parts.beliefs, parts.opinions, parts)
    );
  }
  if (key === "quality") {
    return editorWorkDetailPayload(
      "Review Trace",
      "인사이트·데이터 품질 상세",
      "알림 후보, 품질, 출처 신뢰도",
      renderOntologyInsightPanel(parts) + renderOntologyDataQualityPanel(parts)
    );
  }
  if (key === "relations") {
    return editorWorkDetailPayload(
      "Review Trace",
      "관계 행 상세",
      "거시 관계와 관계 타입별 저장 행",
      renderOntologyMacroRelationPanel(parts) + renderOntologyRelationPanel(parts.tbox, parts.relations, parts.aboxRelations, parts.relationCounts, parts.entityLabels)
    );
  }
  if (key === "rules") {
    return editorWorkDetailPayload(
      "Review Trace",
      "규칙 추적 상세",
      "TBox 규칙과 현재 행 매칭",
      renderOntologyRulePanel(parts.tbox, parts.relationCounts, parts.evidence, parts.beliefs, parts.opinions)
    );
  }
  return null;
}

export { inferenceLedgerRows, inferenceLedgerSummary, inferenceLedgerTone, strategyTraceWorkDetailPayload };
