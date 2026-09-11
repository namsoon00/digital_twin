import { formatConsoleNarrative, stockDisplayName, textWithKnownDisplaySymbols } from "../instruments/catalog.mjs";
import { feedEvidenceDataMeta } from "../market/feed.mjs";
import { consoleResearchItems } from "../market/selectors.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { notificationJobFullText, notificationJobKey, notificationJobResolvedSymbol } from "./detail.mjs";
import { notificationDeliveryStateLabel } from "./history.mjs";
import { inferenceLedgerTone } from "../ontology/inference.mjs";
import { realtimeEventLabel } from "../realtime/labels.mjs";
import { researchEvidenceImpactMeta, researchEvidenceKoreanSummary, researchEvidenceTranslationMeta } from "../research/quality.mjs";
import { formatFeedTime } from "../research/requests.mjs";
import { feedEvidenceKey } from "../research/workspace.mjs";
import { formatClock, formatInteger } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { notificationsState } from "../state/notifications.mjs";

function renderNotificationDetailMetric(label, value, tone) {
  return [
    '<span class="notification-detail-metric ' + escapeHtml(tone || "") + '">',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value || "-") + '</strong>',
    '</span>'
  ].join("");
}

function renderNotificationDetailDisclosure(title, description, body, className, disclosureKey) {
  if (!body) return "";
  var stateKey = String(disclosureKey || className || title || "notification-detail");
  var disclosureOpen = Boolean(notificationsState.notificationDetailDisclosureOpen[stateKey]);
  return [
    '<details class="notification-detail-disclosure ' + escapeHtml(className || "") + '" data-notification-detail-disclosure-key="' + escapeHtml(stateKey) + '"' + (disclosureOpen ? ' open' : '') + '>',
    '<summary>',
    '<span><strong>' + escapeHtml(title || "상세 정보") + '</strong><em>' + escapeHtml(description || "전체 기록을 확인합니다.") + '</em></span>',
    '<span class="notification-detail-disclosure-icon" aria-hidden="true">&#9662;</span>',
    '</summary>',
    '<div class="notification-detail-disclosure-body">' + body + '</div>',
    '</details>'
  ].join("");
}

function notificationJobDetailPayload(job) {
  var resolvedSymbol = notificationJobResolvedSymbol(job);
  var displaySymbol = resolvedSymbol ? stockDisplayName(resolvedSymbol, job) : "";
  var title = textWithKnownDisplaySymbols(realtimeEventLabel(String(job.title || "")), resolvedSymbol, job);
  var preview = textWithKnownDisplaySymbols(formatConsoleNarrative(job.lastError || job.textPreview || "-"), resolvedSymbol, job);
  var fullText = notificationJobFullText(job, resolvedSymbol);
  var reasons = Array.isArray(job.deliveryReasons) ? job.deliveryReasons.slice(0, 6) : [];
  return {
    resolvedSymbol: resolvedSymbol,
    displaySymbol: displaySymbol,
    title: title,
    preview: preview,
    fullText: fullText,
    reasons: reasons,
    decisionEpisodeId: String(job.decisionEpisodeId || ((job.context || {}).investmentDecisionEpisodeId) || ""),
    investmentFlow: job.investmentFlow && typeof job.investmentFlow === "object"
      ? job.investmentFlow
      : (((job.context || {}).investmentFlow && typeof (job.context || {}).investmentFlow === "object") ? (job.context || {}).investmentFlow : {})
  };
}

function notificationActionFlowActionLabel(value) {
  var labels = {
    BUY: "소액 진입 검토",
    ADD: "소액 추가매수 검토",
    HOLD: "유지",
    TRIM: "분할축소 검토",
    SELL: "매도 검토",
    AVOID: "신규 진입 회피"
  };
  return labels[String(value || "").toUpperCase()] || "조건 확인";
}

function notificationActionFlowStatusLabel(value) {
  var labels = {
    ENTRY_ELIGIBLE: "소액 진입 조건 성립",
    ENTRY_DEFERRED: "진입 조건 추가 확인",
    ENTRY_OBSERVING: "관심 유지",
    ENTRY_BLOCKED: "진입 판단 보류",
    HOLDING_REVIEW: "보유 판단 재확인",
    JUDGEMENT_BLOCKED: "판단 보류"
  };
  return labels[String(value || "")] || "조건 확인";
}

function renderNotificationActionFlow(job) {
  var flow = job && job.actionFlow && typeof job.actionFlow === "object" ? job.actionFlow : {};
  if (!Object.keys(flow).length) return "";
  var transition = flow.transition && typeof flow.transition === "object" ? flow.transition : {};
  var userTransition = flow.userTransition && typeof flow.userTransition === "object" ? flow.userTransition : {};
  var readiness = flow.dataReadiness && typeof flow.dataReadiness === "object" ? flow.dataReadiness : {};
  var news = flow.newsImpact && typeof flow.newsImpact === "object" ? flow.newsImpact : {};
  var nextChecks = Array.isArray(flow.nextChecks) ? flow.nextChecks : [];
  var invalidation = Array.isArray(flow.invalidationConditions) ? flow.invalidationConditions : [];
  var effects = Array.isArray(flow.effects) ? flow.effects : [];
  var currentAction = flow.currentActionLabel || notificationActionFlowActionLabel(flow.currentAction);
  var status = flow.statusLabel || notificationActionFlowStatusLabel(flow.status);
  var rows = [
    '<div class="notification-detail-metrics">',
    renderNotificationDetailMetric("지금 행동", currentAction, flow.status === "ENTRY_ELIGIBLE" ? "watch" : "hold"),
    renderNotificationDetailMetric("현재 조건", status, flow.status === "JUDGEMENT_BLOCKED" || flow.status === "ENTRY_BLOCKED" ? "caution" : "muted"),
    readiness.dataState ? renderNotificationDetailMetric("자료 상태", readiness.dataState === "sufficient" ? "판단 가능" : (readiness.dataState === "partial" ? "일부 확인" : "판단 보류"), readiness.dataState === "sufficient" ? "watch" : "caution") : "",
    '</div>'
  ].join("");
  var change = userTransition.summary || transition.summary || "이전 알림과 같은 판단 범위입니다.";
  var changeLabel = transition.label ? '<b>[' + escapeHtml(transition.label) + ']</b> ' : '';
  return [
    '<section class="notification-detail-section notification-action-flow-section">',
    '<strong>판단 흐름</strong>',
    rows,
    '<div class="notification-detail-reasons">',
    '<p><b>이번 변화</b> ' + changeLabel + escapeHtml(change) + '</p>',
    nextChecks.length ? '<p><b>다음 행동</b> ' + escapeHtml(nextChecks.slice(0, 2).join(" / ")) + '</p>' : '',
    invalidation.length ? '<p><b>바뀌는 조건</b> ' + escapeHtml(invalidation.slice(0, 2).join(" / ")) + '</p>' : '',
    news.headline ? '<p><b>결정에 반영한 뉴스</b> ' + escapeHtml([news.source, news.headline].filter(Boolean).join(": ")) + '</p>' : '',
    '</div>',
    effects.length ? '<div class="notification-detail-tags">' + effects.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
    '</section>'
  ].join("");
}

function renderNotificationInferenceStateTransition(job) {
  var transition = job && job.investmentNotificationTransition && typeof job.investmentNotificationTransition === "object"
    ? job.investmentNotificationTransition
    : {};
  var current = job && job.investmentNotificationState && typeof job.investmentNotificationState === "object"
    ? job.investmentNotificationState
    : (transition.currentState || {});
  if (!Object.keys(current).length && !Object.keys(transition).length) return "";
  var previous = transition.previousState && typeof transition.previousState === "object" ? transition.previousState : {};
  var labels = Array.isArray(transition.changedFieldLabels) ? transition.changedFieldLabels : [];
  return [
    '<section class="notification-detail-section notification-action-flow-section">',
    '<strong>최종 추론 상태</strong>',
    '<div class="notification-detail-metrics">',
    previous.label ? renderNotificationDetailMetric("이전", previous.label, "muted") : '',
    renderNotificationDetailMetric("현재", current.label || "판단 상태 확인", transition.changed ? "watch" : "hold"),
    renderNotificationDetailMetric("변경 항목", labels.length ? labels.join(" · ") : (transition.historyAvailable ? "변경 없음" : "첫 기준선"), transition.changed ? "watch" : "muted"),
    '</div>',
    '<p>' + escapeHtml(transition.summary || "저장된 최종 AI 판단 상태입니다.") + '</p>',
    '</section>'
  ].join("");
}

function notificationJobResearchEvidence(job) {
  var symbol = notificationJobResolvedSymbol(job);
  if (!symbol) return [];
  return consoleResearchItems().map(function (item, index) {
    return { item: item, key: feedEvidenceKey(item, index) };
  }).filter(function (entry) {
    return String((entry.item || {}).symbol || "").toUpperCase() === symbol;
  }).sort(function (a, b) {
    return Date.parse((b.item || {}).publishedAt || (b.item || {}).observedAt || 0)
      - Date.parse((a.item || {}).publishedAt || (a.item || {}).observedAt || 0);
  }).slice(0, 12);
}

function renderNotificationJobResearchEvidence(job) {
  var entries = notificationJobResearchEvidence(job);
  if (!entries.length) return "";
  return [
    '<section class="notification-detail-section notification-source-section">',
    '<strong>원문/출처</strong>',
    '<p>기사 본문을 읽고 정리한 한글 요약과 주가 영향을 표시합니다.</p>',
    '<div class="work-detail-list">',
    entries.map(function (entry) {
      var item = entry.item || {};
      var impact = researchEvidenceImpactMeta(item);
      var source = feedEvidenceDataMeta(item);
      var translation = researchEvidenceTranslationMeta(item);
      return [
        '<div class="work-detail-row">',
        '<span class="tone-chip ' + escapeHtml(impact.tone || "hold") + '">' + escapeHtml(impact.label || "중립") + '</span>',
        '<div><strong>' + escapeHtml(translation.displayTitle || "제목 없음") + '</strong><span>' + escapeHtml(researchEvidenceKoreanSummary(item)) + '</span><em>' + escapeHtml([source.source, formatFeedTime(item.publishedAt || item.observedAt)].filter(Boolean).join(" · ")) + '</em></div>',
        renderWorkDetailButton("research-evidence", entry.key, "기사 분석", "mini-button"),
        '</div>'
      ].join("");
    }).join(""),
    '</div>',
    '</section>'
  ].join("");
}

function notificationReverseReasoningTrace(job) {
  var trace = job && job.reasoningTrace;
  return trace && typeof trace === "object" ? trace : null;
}

function notificationPipelineStatusMeta(status) {
  var key = String(status || "missing");
  var labels = {
    completed: "완료",
    conditional: "조건부",
    "in-progress": "진행 중",
    blocked: "차단",
    failed: "실패",
    missing: "기록 없음"
  };
  return {
    label: labels[key] || key,
    tone: key === "completed" ? "watch" : (key === "failed" || key === "blocked" ? "caution" : "hold")
  };
}

function notificationPipelineDuration(value) {
  var milliseconds = Number(value || 0);
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return "소요시간 미기록";
  if (milliseconds < 1000) return formatInteger(milliseconds) + "ms";
  if (milliseconds < 60000) return (milliseconds / 1000).toFixed(milliseconds < 10000 ? 2 : 1) + "초";
  return (milliseconds / 60000).toFixed(1) + "분";
}

function renderNotificationUnifiedPipeline(job) {
  var trace = job && job.notificationTrace && typeof job.notificationTrace === "object"
    ? job.notificationTrace
    : {};
  var pipeline = trace.pipeline && typeof trace.pipeline === "object" ? trace.pipeline : {};
  var stages = Array.isArray(pipeline.stages) ? pipeline.stages : [];
  if (!stages.length) {
    return '<section class="notification-detail-section notification-pipeline-section unavailable"><strong>전체 처리 계보</strong><p>이 알림에는 통합 계보가 저장되지 않았습니다. 기존 추론 근거와 수명주기 기록은 아래에서 각각 확인할 수 있습니다.</p></section>';
  }
  var status = String(pipeline.status || "partial");
  var statusMeta = {
    complete: { label: "전체 단계 연결", tone: "watch" },
    partial: { label: "일부 단계 확인 필요", tone: "hold" },
    blocked: { label: "차단 단계 있음", tone: "caution" }
  }[status] || { label: "계보 확인 필요", tone: "hold" };
  var bottleneck = pipeline.bottleneck && typeof pipeline.bottleneck === "object" ? pipeline.bottleneck : {};
  var links = pipeline.links && typeof pipeline.links === "object" ? pipeline.links : {};
  var body = stages.map(function (stage, index) {
    stage = stage && typeof stage === "object" ? stage : {};
    var stageMeta = notificationPipelineStatusMeta(stage.status);
    var identifiers = stage.identifiers && typeof stage.identifiers === "object" ? stage.identifiers : {};
    var identifierRows = Object.keys(identifiers).filter(function (key) {
      var value = identifiers[key];
      return value !== undefined && value !== null && value !== "" && (!Array.isArray(value) || value.length);
    });
    var timing = [
      stage.startedAt ? "시작 " + formatClock(stage.startedAt) : "시작 시각 미기록",
      stage.completedAt && stage.completedAt !== stage.startedAt ? "완료 " + formatClock(stage.completedAt) : "",
      notificationPipelineDuration(stage.durationMs)
    ].filter(Boolean).join(" · ");
    var disclosureKey = "notification-job:" + notificationJobKey(job) + ":pipeline:" + String(stage.key || index);
    var disclosureOpen = Boolean(notificationsState.notificationDetailDisclosureOpen[disclosureKey]);
    return [
      '<li class="notification-pipeline-stage" data-pipeline-status="' + escapeHtml(stage.status || "missing") + '">',
      '<span class="notification-pipeline-index" aria-hidden="true">' + escapeHtml(String(stage.sequence || index + 1)) + '</span>',
      '<details data-notification-detail-disclosure-key="' + escapeHtml(disclosureKey) + '"' + (disclosureOpen ? ' open' : '') + '>',
      '<summary><span><strong>' + escapeHtml(stage.title || "처리 단계") + '</strong><em>' + escapeHtml(stage.summary || "세부 기록을 확인하세요.") + '</em><i>' + escapeHtml(timing) + '</i></span><b class="tone-chip ' + escapeHtml(stageMeta.tone) + '">' + escapeHtml(stageMeta.label) + '</b></summary>',
      '<div class="notification-pipeline-stage-detail">',
      identifierRows.length ? '<dl class="notification-pipeline-identifiers">' + identifierRows.map(function (key) {
        var value = identifiers[key];
        var rendered = Array.isArray(value) ? value.join(" · ") : String(value);
        return '<div><dt>' + escapeHtml(key) + '</dt><dd>' + escapeHtml(rendered) + '</dd></div>';
      }).join("") + '</dl>' : '<p class="notification-reasoning-empty">이 단계의 연결 식별자가 기록되지 않았습니다.</p>',
      '<details class="notification-ai-prompt-audit"><summary>이 단계의 전체 저장 데이터</summary><pre>' + escapeHtml(JSON.stringify(stage.details || {}, null, 2)) + '</pre></details>',
      '</div>',
      '</details>',
      '</li>'
    ].join("");
  }).join("");
  return [
    '<section class="notification-detail-section notification-pipeline-section">',
    '<div class="notification-reasoning-head"><div><strong>전체 처리 계보</strong><span>원천 이벤트부터 채널 전달까지 실제 저장 시각과 계약을 한 흐름으로 연결했습니다.</span></div><span class="tone-chip ' + escapeHtml(statusMeta.tone) + '">' + escapeHtml(statusMeta.label) + '</span></div>',
    '<div class="notification-pipeline-summary">',
    '<span><em>단계</em><strong>' + escapeHtml(String(stages.length)) + '</strong></span>',
    '<span><em>완료</em><strong>' + escapeHtml(String(stages.filter(function (stage) { return stage.status === "completed"; }).length)) + '</strong></span>',
    '<span><em>최장 구간</em><strong>' + escapeHtml(bottleneck.title ? bottleneck.title + " · " + notificationPipelineDuration(bottleneck.durationMs) : "측정값 없음") + '</strong></span>',
    '</div>',
    '<div class="notification-reasoning-provenance">' + Object.keys(links).filter(function (key) { return links[key]; }).map(function (key) { return '<code>' + escapeHtml(key + " " + links[key]) + '</code>'; }).join("") + '</div>',
    '<ol class="notification-pipeline-flow">' + body + '</ol>',
    '<details class="notification-ai-prompt-audit"><summary>전체 계보 JSON</summary><pre>' + escapeHtml(JSON.stringify(pipeline, null, 2)) + '</pre></details>',
    '</section>'
  ].join("");
}

function renderNotificationLifecycleTrace(job) {
  var trace = job && job.notificationTrace && typeof job.notificationTrace === "object"
    ? job.notificationTrace
    : {};
  var timeline = Array.isArray(trace.timeline) ? trace.timeline : [];
  if (!timeline.length) {
    return '<p class="notification-reasoning-empty">이 알림은 모듈화 이전 기록이거나 처리 수명주기 감사 정보가 없습니다.</p>';
  }
  var stageLabels = {
    received: "요청 수신",
    eligibility_checked: "발송 적격성 확인",
    awaiting_decision: "AI 판단 대기",
    delivery_reason_validated: "사용자 발송 사유 검증",
    ready_to_render: "렌더링 준비",
    rendered: "메시지 렌더링",
    dispatching: "채널 전송 시도",
    delivery_result: "채널 전송 결과",
    delivered: "발송 완료",
    suppressed: "발송 보류",
    superseded: "최신 요청으로 대체",
    expired: "유효시간 만료",
    failed: "처리 실패"
  };
  var body = '<div class="notification-reasoning-trace-list">' + timeline.map(function (item) {
    var metadata = item.metadata && typeof item.metadata === "object" ? item.metadata : {};
    var detail = [
      item.outcome || "",
      metadata.channel ? "채널 " + metadata.channel : "",
      metadata.provider ? "공급자 " + metadata.provider : "",
      item.reason || ""
    ].filter(Boolean).join(" · ");
    return [
      '<div class="notification-reasoning-trace-row">',
      '<span class="tone-chip ' + escapeHtml(item.stage === "failed" ? "caution" : (item.stage === "delivered" ? "watch" : "hold")) + '">' + escapeHtml(String(item.sequence || "-")) + '</span>',
      '<strong>' + escapeHtml(stageLabels[item.stage] || item.stage || "처리 단계") + '</strong>',
      '<em>' + escapeHtml(formatClock(item.at) || item.at || "시각 미기록") + '</em>',
      detail ? '<span>' + escapeHtml(detail) + '</span>' : '',
      '</div>'
    ].join("");
  }).join("") + '</div>';
  body += '<details class="notification-ai-prompt-audit"><summary>알림 처리 전체 감사 데이터</summary><pre>'
    + escapeHtml(JSON.stringify(trace, null, 2))
    + '</pre></details>';
  return body;
}

function notificationReasoningTraceStatusMeta(status) {
  var key = String(status || "unavailable");
  var labels = {
    ready: "추론 경로 확인됨",
    partial: "일부 추론 기록 있음",
    unavailable: "추론 기록 없음"
  };
  return {
    label: labels[key] || "확인 필요",
    tone: key === "ready" ? "watch" : (key === "partial" ? "caution" : "hold")
  };
}

function notificationReasoningTraceItems(rows, className, renderItem) {
  rows = Array.isArray(rows) ? rows : [];
  if (!rows.length) return "";
  return '<div class="' + escapeHtml(className) + '">' + rows.map(renderItem).join("") + '</div>';
}

function notificationReasoningTraceTags(rows, className) {
  rows = Array.isArray(rows) ? rows.filter(Boolean) : [];
  if (!rows.length) return "";
  return '<div class="' + escapeHtml(className || "notification-reasoning-tags") + '">' + rows.map(function (row) {
    return '<span>' + escapeHtml(String(row)) + '</span>';
  }).join("") + '</div>';
}

function renderNotificationReasoningStep(index, title, summary, detail, body, disclosureKey) {
  var stateKey = String(disclosureKey || "notification-reasoning-step:" + index);
  var disclosureOpen = Boolean(notificationsState.notificationDetailDisclosureOpen[stateKey]);
  return [
    '<li class="notification-reasoning-step">',
    '<span class="notification-reasoning-index" aria-hidden="true">' + escapeHtml(String(index)) + '</span>',
    '<details class="notification-reasoning-step-disclosure" data-notification-detail-disclosure-key="' + escapeHtml(stateKey) + '"' + (disclosureOpen ? ' open' : '') + '>',
    '<summary>',
    '<span class="notification-reasoning-step-copy">',
    '<strong>' + escapeHtml(title) + '</strong>',
    summary ? '<span class="notification-reasoning-step-summary">' + escapeHtml(summary) + '</span>' : '',
    detail ? '<em>' + escapeHtml(detail) + '</em>' : '',
    '</span>',
    '<span class="notification-detail-disclosure-icon" aria-hidden="true">&#9662;</span>',
    '</summary>',
    '<div class="notification-reasoning-step-detail">' + (body || '<p class="notification-reasoning-empty">추가 세부 기록이 없습니다.</p>') + '</div>',
    '</details>',
    '</li>'
  ].join("");
}

function renderNotificationReverseReasoning(job) {
  var trace = notificationReverseReasoningTrace(job);
  if (!trace) {
    return [
      '<section class="notification-detail-section notification-reasoning-section">',
      '<strong>실제 실행 순서</strong>',
      '<p>알림 상세를 불러오는 중입니다. 생성 시점 기록을 원천 데이터부터 발송까지 시간 순서로 표시합니다.</p>',
      '</section>'
    ].join("");
  }
  var status = notificationReasoningTraceStatusMeta(trace.status);
  var snapshot = trace.snapshot && typeof trace.snapshot === "object" ? trace.snapshot : {};
  var finalDecision = trace.finalDecision && typeof trace.finalDecision === "object" ? trace.finalDecision : {};
  var comparison = trace.aiComparison && typeof trace.aiComparison === "object" ? trace.aiComparison : {};
  var decisionAbstention = comparison.decisionAbstention && typeof comparison.decisionAbstention === "object" ? comparison.decisionAbstention : {};
  var decisionGuardrails = Array.isArray(comparison.decisionGuardrails) ? comparison.decisionGuardrails : [];
  var aiExecution = trace.aiExecution && typeof trace.aiExecution === "object" ? trace.aiExecution : {};
  var narrative = trace.narrative && typeof trace.narrative === "object" ? trace.narrative : {};
  var narrativeWriter = narrative.writerProvenance && typeof narrative.writerProvenance === "object" ? narrative.writerProvenance : {};
  var narrativeClaims = Array.isArray(narrative.claims) ? narrative.claims : [];
  var narrativeValidations = Array.isArray(narrative.validations) ? narrative.validations : [];
  var executionLedger = trace.executionLedger && typeof trace.executionLedger === "object" ? trace.executionLedger : {};
  var assessmentBundle = trace.assessmentBundle && typeof trace.assessmentBundle === "object" ? trace.assessmentBundle : {};
  var investmentLifecycle = trace.investmentLifecycle && typeof trace.investmentLifecycle === "object" ? trace.investmentLifecycle : {};
  var selected = trace.selectedHypothesis && typeof trace.selectedHypothesis === "object" ? trace.selectedHypothesis : {};
  var delivery = trace.delivery && typeof trace.delivery === "object" ? trace.delivery : {};
  var deliveryLabel = delivery.decision ? notificationDeliveryStateLabel(delivery.decision) : (job.status || "발송 판단");
  var rules = Array.isArray(trace.matchedRules) ? trace.matchedRules : [];
  var inferenceTraces = Array.isArray(trace.inferenceTraces) ? trace.inferenceTraces : [];
  var alternatives = Array.isArray(trace.alternativeHypotheses) ? trace.alternativeHypotheses : [];
  var hypotheses = Array.isArray(trace.hypotheses) ? trace.hypotheses : [selected].concat(alternatives).filter(function (item) {
    return Object.keys(item || {}).length;
  });
  var facts = Array.isArray(trace.inputFacts) ? trace.inputFacts : [];
  var sources = Array.isArray(trace.sources) ? trace.sources : [];
  var missing = Array.isArray(trace.missingData) ? trace.missingData : [];
  var audit = Array.isArray(trace.traceability) ? trace.traceability : [];
  var reasoningDisclosurePrefix = "notification-job:" + notificationJobKey(job) + ":reasoning-step:";
  if (trace.status === "unavailable") {
    return [
      '<section class="notification-detail-section notification-reasoning-section unavailable">',
      '<div class="notification-reasoning-head">',
      '<div><strong>실제 실행 순서</strong><span>저장된 생성 시점 기록이 없어 현재 그래프로 재구성하지 않습니다.</span></div>',
      '<span class="tone-chip ' + escapeHtml(status.tone) + '">' + escapeHtml(status.label) + '</span>',
      '</div>',
      '<p>' + escapeHtml(trace.reason || "이 알림에는 생성 시점의 추론 컨텍스트가 없습니다.") + '</p>',
      '</section>'
    ].join("");
  }
  var scope = snapshot.scope && typeof snapshot.scope === "object" ? snapshot.scope : {};
  var provenance = [
    snapshot.inferenceGenerationAt ? "추론 시각 " + formatClock(snapshot.inferenceGenerationAt) : "추론 시각 미기록",
    snapshot.inferenceGenerationId ? "세대 " + snapshot.inferenceGenerationId : "세대 ID 미기록",
    snapshot.ruleSetHash ? "규칙 묶음 " + snapshot.ruleSetHash : "규칙 해시 미기록",
    snapshot.graphStore ? "저장소 " + snapshot.graphStore : ""
  ].filter(Boolean);
  Object.keys(scope).forEach(function (key) {
    if (scope[key]) provenance.push(key + " " + scope[key]);
  });
  var deliveryBody = notificationReasoningTraceTags((delivery.reasons || []).concat(delivery.cooldownReason ? [delivery.cooldownReason] : []).concat(delivery.freshnessReason ? [delivery.freshnessReason] : []));
  var comparisonBody = notificationReasoningTraceTags([
    comparison.precomputedActionLabel ? "계산 후보 " + comparison.precomputedActionLabel : "",
    comparison.selectedActionLabel ? "AI 최종 " + comparison.selectedActionLabel : "",
    comparison.comparisonStateLabel || "",
    comparison.selectionSource ? "선택 방식 " + comparison.selectionSource : ""
  ].filter(Boolean));
  if (comparison.disagreementReason) {
    comparisonBody += '<p class="notification-reasoning-note"><strong>조정 이유</strong>' + escapeHtml(comparison.disagreementReason) + '</p>';
  }
  if (decisionAbstention.abstained) {
    comparisonBody += '<p class="notification-reasoning-note"><strong>판단 유보</strong>' + escapeHtml(decisionAbstention.reason || "가설 비교 계약을 충족하지 못해 선택 가설을 저장하지 않았습니다.") + '</p>';
  }
  decisionGuardrails.forEach(function (guardrail) {
    comparisonBody += '<p class="notification-reasoning-note"><strong>' + escapeHtml(guardrail.label || "판단 안전 제한") + '</strong>' + escapeHtml(guardrail.reason || "추가 검증이 필요합니다.") + '</p>';
  });
  var causalChain = Array.isArray(comparison.causalChain) ? comparison.causalChain : [];
  if (comparison.decisionReadiness) {
    comparisonBody += '<p class="notification-reasoning-note"><strong>판단 준비 상태</strong>' + escapeHtml(comparison.decisionReadiness) + '</p>';
  }
  causalChain.forEach(function (item) {
    var path = [item.driver, item.channel, item.expectedEffect].filter(Boolean).join(" → ");
    var evidenceIds = Array.isArray(item.evidenceIds) ? item.evidenceIds : [];
    comparisonBody += '<p class="notification-reasoning-note"><strong>AI 인과 경로</strong>' + escapeHtml(path || "경로 설명 없음") + (item.status ? ' <em>' + escapeHtml(item.status) + '</em>' : '') + (evidenceIds.length ? '<code>' + escapeHtml(evidenceIds.join(", ")) + '</code>' : '') + '</p>';
  });
  var alternativeAction = comparison.alternativeAction && typeof comparison.alternativeAction === "object" ? comparison.alternativeAction : {};
  if (Object.keys(alternativeAction).length) {
    comparisonBody += '<p class="notification-reasoning-note"><strong>비교 대안</strong>' + escapeHtml([alternativeAction.actionLabel || alternativeAction.action, alternativeAction.whyNotSelected, alternativeAction.switchCondition].filter(Boolean).join(" · ")) + '</p>';
  }
  var hypothesisBody = notificationReasoningTraceTags([
    selected.stanceLabel || "",
    selected.evidenceStateLabel || "",
    selected.verdictLabel || "",
    selected.horizon ? "기간 " + selected.horizon : "",
    selected.verificationStatus || ""
  ].filter(Boolean));
  if (selected.assumptions && selected.assumptions.length) {
    hypothesisBody += '<p class="notification-reasoning-note"><strong>전제</strong>' + escapeHtml(selected.assumptions.join(" · ")) + '</p>';
  }
  if (selected.invalidationConditions && selected.invalidationConditions.length) {
    hypothesisBody += '<p class="notification-reasoning-note"><strong>약화·무효화 조건</strong>' + escapeHtml(selected.invalidationConditions.join(" · ")) + '</p>';
  }
  var ruleBody = notificationReasoningTraceItems(rules, "notification-reasoning-rule-list", function (rule) {
    var meta = [rule.reviewLabel, rule.dataStateLabel, rule.evidenceRole].filter(Boolean).join(" · ");
    return [
      '<div class="notification-reasoning-rule' + (rule.selected ? ' selected' : '') + '">',
      '<span>' + (rule.selected ? "선택 규칙" : "성립 규칙") + '</span>',
      '<strong>' + escapeHtml(rule.label || rule.ruleId || "관계 규칙") + '</strong>',
      meta ? '<em>' + escapeHtml(meta) + '</em>' : '',
      rule.inferenceTraceId ? '<code>' + escapeHtml(rule.inferenceTraceId) + '</code>' : '',
      rule.evidence && rule.evidence.length ? notificationReasoningTraceTags(rule.evidence, "notification-reasoning-tags compact") : '',
      '</div>'
    ].join("");
  });
  var assessmentLabels = {
    evidenceQuality: "근거 품질",
    investmentOpinion: "종목 투자 의견",
    portfolioFit: "계좌 적합성",
    executionReadiness: "실행 가능성"
  };
  var assessmentStatusLabels = {
    supported: "지지 근거 확인",
    constrained: "제약 있음",
    deferred: "추가 확인",
    blocked: "판단 또는 실행 차단",
    observed: "관찰 근거",
    "not-evaluated": "해당 규칙 없음"
  };
  var assessmentBody = Object.keys(assessmentLabels).map(function (key) {
    var item = assessmentBundle[key] && typeof assessmentBundle[key] === "object" ? assessmentBundle[key] : {};
    if (!Object.keys(item).length) return "";
    var details = [
      assessmentStatusLabels[item.status] || item.status,
      item.candidateAction ? notificationActionFlowActionLabel(item.candidateAction) : "",
      Array.isArray(item.ruleIds) && item.ruleIds.length ? "규칙 " + item.ruleIds.length + "개" : ""
    ].filter(Boolean).join(" · ");
    return '<div class="notification-reasoning-rule"><span>' + escapeHtml(assessmentLabels[key]) + '</span><strong>' + escapeHtml(details || "평가 기록 없음") + '</strong>' + (item.selectedRuleId ? '<code>' + escapeHtml(item.selectedRuleId) + '</code>' : '') + '</div>';
  }).join("");
  var recommendedPlan = assessmentBundle.recommendedPlan && typeof assessmentBundle.recommendedPlan === "object" ? assessmentBundle.recommendedPlan : {};
  if (Object.keys(recommendedPlan).length) {
    assessmentBody += '<p class="notification-reasoning-note"><strong>조합 계획</strong>' + escapeHtml([
      recommendedPlan.investmentAction ? notificationActionFlowActionLabel(recommendedPlan.investmentAction) : "투자 의견 없음",
      recommendedPlan.status,
      recommendedPlan.planOption
    ].filter(Boolean).join(" · ")) + '</p>';
  }
  if (assessmentBody) assessmentBody = '<div class="notification-reasoning-rule-list">' + assessmentBody + '</div>';
  var traceBody = notificationReasoningTraceItems(inferenceTraces, "notification-reasoning-trace-list", function (row) {
    var conditions = Array.isArray(row.conditions) ? row.conditions : [];
    return [
      '<div class="notification-reasoning-trace-row' + (row.selected ? ' selected' : '') + '">',
      '<strong>' + escapeHtml(row.label || row.ruleId || "추론 경로") + '</strong>',
      row.traceId ? '<code>' + escapeHtml(row.traceId) + '</code>' : '',
      conditions.length ? '<ul>' + conditions.map(function (condition) {
        return '<li><b>' + escapeHtml(condition.label || "성립 조건") + '</b>' + (condition.value ? '<span>' + escapeHtml(condition.value) + '</span>' : '') + '</li>';
      }).join("") + '</ul>' : '<span>세부 조건 값은 이 알림에 저장되지 않았습니다.</span>',
      '</div>'
    ].join("");
  });
  var factBody = notificationReasoningTraceItems(facts, "notification-reasoning-fact-list", function (fact) {
    return '<span><em>' + escapeHtml(fact.label || fact.key || "사실") + '</em><strong>' + escapeHtml(fact.value || "-") + '</strong></span>';
  });
  var sourceBody = notificationReasoningTraceItems(sources, "notification-reasoning-source-list", function (source) {
    var meta = [source.source, source.publishedAt ? formatClock(source.publishedAt) : "", source.impact].filter(Boolean).join(" · ");
    return '<div><strong>' + escapeHtml(source.title || "원문") + '</strong>' + (meta ? '<span>' + escapeHtml(meta) + '</span>' : '') + (source.url ? '<a href="' + escapeHtml(source.url) + '" target="_blank" rel="noopener noreferrer">원문 열기</a>' : '') + '</div>';
  });
  var hypothesisCandidatesBody = notificationReasoningTraceItems(hypotheses, "notification-reasoning-alternative-list", function (item) {
    var isSelected = item.hypothesisId && item.hypothesisId === comparison.selectedHypothesisId;
    var meta = [item.stanceLabel, item.evidenceStateLabel, item.verdictLabel].filter(Boolean).join(" · ");
    return '<div><strong>' + escapeHtml((isSelected ? "AI 선택 · " : "후보 · ") + (item.label || item.hypothesisId || "가설")) + '</strong><span>' + escapeHtml(item.claim || "설명 없음") + '</span>' + (item.reasoning ? '<span>' + escapeHtml(item.reasoning) + '</span>' : '') + (meta ? '<em>' + escapeHtml(meta) + '</em>' : '') + '</div>';
  });
  var auditBody = notificationReasoningTraceItems(audit, "notification-reasoning-audit-list", function (item) {
    var tone = item.state === "verified" ? "watch" : (item.state === "partial" ? "caution" : "hold");
    return '<div><span class="tone-chip ' + escapeHtml(tone) + '">' + escapeHtml(item.state === "verified" ? "확인" : "제한") + '</span><strong>' + escapeHtml(item.label || "검증") + '</strong><em>' + escapeHtml(item.detail || "") + '</em></div>';
  });
  var aiExecutionMeta = [
    aiExecution.executed ? "실행됨" : "실행 안 함",
    aiExecution.model || "",
    aiExecution.reasoningEffort || "",
    aiExecution.executionProfile && aiExecution.executionProfile.name ? aiExecution.executionProfile.name : "",
    aiExecution.promptVersion || "",
    aiExecution.decisionBriefVersion || "",
    aiExecution.latencyMs ? formatInteger(aiExecution.latencyMs) + "ms" : "",
    aiExecution.promptBytes ? formatInteger(aiExecution.promptBytes) + " bytes" : ""
  ].filter(Boolean);
  var aiExecutionBody = notificationReasoningTraceTags(aiExecutionMeta, "notification-reasoning-tags");
  var executionSpans = aiExecution.executionSpans && typeof aiExecution.executionSpans === "object" ? aiExecution.executionSpans : {};
  var modelAttempts = Array.isArray(executionSpans.modelAttempts) ? executionSpans.modelAttempts : [];
  if (Object.keys(executionSpans).length) {
    aiExecutionBody += notificationReasoningTraceTags([
      executionSpans.completionPolicy === "wait-until-complete" ? "완료까지 대기" : "시간 제한 사용",
      executionSpans.queueWaitMs != null ? "큐 " + formatInteger(executionSpans.queueWaitMs) + "ms" : "",
      executionSpans.promptPreparationMs != null ? "입력 구성 " + formatInteger(executionSpans.promptPreparationMs) + "ms" : "",
      executionSpans.initialModelMs != null ? "첫 판단 " + formatInteger(executionSpans.initialModelMs) + "ms" : "",
      executionSpans.repairModelMs ? "계약 교정 " + formatInteger(executionSpans.repairModelMs) + "ms" : "",
      modelAttempts.length && modelAttempts[0].capacityWaitMs != null ? "AI 슬롯 대기 " + formatInteger(modelAttempts[0].capacityWaitMs) + "ms" : ""
    ].filter(Boolean), "notification-reasoning-tags");
  }
  if (aiExecution.promptHash) {
    aiExecutionBody += '<code>' + escapeHtml(aiExecution.promptHash) + '</code>';
  }
  if (aiExecution.hypothesisComparisonRepair && aiExecution.hypothesisComparisonRepair.attempted) {
    aiExecutionBody += '<p class="notification-reasoning-note"><strong>가설 비교 교정</strong>' + escapeHtml(aiExecution.hypothesisComparisonRepair.succeeded ? "교정 후 비교 계약 통과" : "교정 후에도 판단 유보") + '</p>';
  }
  if (aiExecution.prompt) {
    aiExecutionBody += '<details class="notification-ai-prompt-audit"><summary>실제 AI 입력 프롬프트</summary><pre>' + escapeHtml(aiExecution.prompt) + '</pre></details>';
  }
  if (aiExecution.decisionBrief && Object.keys(aiExecution.decisionBrief).length) {
    aiExecutionBody += '<details class="notification-ai-prompt-audit"><summary>AI Decision Brief</summary><pre>' + escapeHtml(JSON.stringify(aiExecution.decisionBrief, null, 2)) + '</pre></details>';
  }
  if (aiExecution.internalDataAudit && Object.keys(aiExecution.internalDataAudit).length) {
    aiExecutionBody += '<details class="notification-ai-prompt-audit"><summary>내부 데이터 조회 감사</summary><pre>' + escapeHtml(JSON.stringify(aiExecution.internalDataAudit, null, 2)) + '</pre></details>';
  }
  if (aiExecution.researchCycle && Object.keys(aiExecution.researchCycle).length) {
    aiExecutionBody += '<details class="notification-ai-prompt-audit"><summary>AI 조사 사이클</summary><pre>' + escapeHtml(JSON.stringify(aiExecution.researchCycle, null, 2)) + '</pre></details>';
  }
  var narrativeValidationById = {};
  narrativeValidations.forEach(function (item) {
    if (item && item.claimId) narrativeValidationById[item.claimId] = item;
  });
  var narrativeBody = notificationReasoningTraceTags([
    narrativeWriter.label || "작성 주체 미기록",
    narrativeWriter.decisionOwner ? "판단 주체 " + narrativeWriter.decisionOwner : "",
    narrative.intent ? "알림 목적 " + narrative.intent : "",
    narrative.metrics && narrative.metrics.verifiedClaimCount !== undefined ? "검증 문장 " + narrative.metrics.verifiedClaimCount + "개" : "",
    narrative.metrics && narrative.metrics.rejectedClaimCount ? "제외 문장 " + narrative.metrics.rejectedClaimCount + "개" : ""
  ].filter(Boolean), "notification-reasoning-tags");
  narrativeBody += notificationReasoningTraceItems(narrativeClaims, "notification-reasoning-rule-list", function (claim) {
    var validation = narrativeValidationById[claim.claimId] || {};
    var evidenceIds = Array.isArray(claim.evidenceIds) ? claim.evidenceIds : [];
    return [
      '<div class="notification-reasoning-rule">',
      '<span>' + escapeHtml(claim.section || "claim") + '</span>',
      '<strong>' + escapeHtml(claim.text || "문장 없음") + '</strong>',
      '<em>' + escapeHtml(validation.status === "verified" ? "근거 연결 확인" : (validation.status || "검증 상태 미기록")) + '</em>',
      evidenceIds.length ? '<code>' + escapeHtml(evidenceIds.join(", ")) + '</code>' : '',
      '</div>'
    ].join("");
  });
  if (narrative.evidenceLedger && narrative.evidenceLedger.length) {
    narrativeBody += '<details class="notification-ai-prompt-audit"><summary>문장 근거 원장</summary><pre>' + escapeHtml(JSON.stringify(narrative.evidenceLedger, null, 2)) + '</pre></details>';
  }
  var decisionStepTitle = narrativeWriter.decisionOwner === "ai"
    ? "AI 비교·최종 판단"
    : (narrativeWriter.aiAuthored ? "AI 설명·TypeDB 판단 확인" : "TypeDB 판단·근거 요약");
  var executionRuns = Array.isArray(executionLedger.runs) ? executionLedger.runs : [];
  var executionLedgerBody = executionRuns.map(function (run) {
    var stages = Array.isArray(run.stages) ? run.stages : [];
    var ruleRuns = Array.isArray(run.rules) ? run.rules : [];
    return [
      '<div class="notification-reasoning-provenance">',
      '<code>' + escapeHtml(run.runId || "run") + '</code>',
      '<code>' + escapeHtml(run.lane || "CORE_REASONING") + '</code>',
      '<code>stages ' + escapeHtml(stages.length) + '</code>',
      '<code>rules ' + escapeHtml(ruleRuns.length) + '</code>',
      '</div>',
      '<div class="notification-reasoning-audit-list">',
      stages.map(function (stage) {
        var timing = [stage.startedAt ? formatClock(stage.startedAt) : "", stage.completedAt ? formatClock(stage.completedAt) : "", formatInteger(stage.durationMs || 0) + "ms"].filter(Boolean).join(" → ");
        var detail = stage.detail && Object.keys(stage.detail).length ? '<pre class="notification-reasoning-raw">' + escapeHtml(JSON.stringify(stage.detail, null, 2)) + '</pre>' : '';
        return '<div><span class="tone-chip ' + escapeHtml(inferenceLedgerTone(stage.status)) + '">' + escapeHtml(stage.status || "-") + '</span><strong>' + escapeHtml(stage.stageKey || "-") + '</strong><em>' + escapeHtml(timing) + '</em>' + detail + '</div>';
      }).join(""),
      '</div>',
      '<div class="notification-reasoning-rule-list">',
      ruleRuns.map(function (rule) {
        var timing = [rule.status, rule.stageKey, rule.queryMode, "query " + formatInteger(rule.queryCount || 0), formatInteger(rule.durationMs || 0) + "ms", "DB " + formatInteger(rule.queryDurationMs || 0) + "ms"].filter(Boolean).join(" · ");
        var detail = [rule.selectedReason, rule.failureReason, (rule.targetSymbols || []).join(", ")].filter(Boolean).join(" · ");
        return '<div class="notification-reasoning-rule"><span>' + escapeHtml(rule.matched ? "성립" : (rule.reused ? "재사용" : "실행")) + '</span><strong>' + escapeHtml(rule.ruleId || rule.ruleRunKey || "규칙") + '</strong><em>' + escapeHtml(timing) + '</em>' + (detail ? '<em>' + escapeHtml(detail) + '</em>' : '') + '</div>';
      }).join(""),
      '</div>'
    ].join("");
  }).join("");
  var lifecyclePlans = Array.isArray(investmentLifecycle.actionPlans) ? investmentLifecycle.actionPlans : [];
  var lifecycleExecutions = Array.isArray(investmentLifecycle.executionEpisodes) ? investmentLifecycle.executionEpisodes : [];
  var lifecycleFills = Array.isArray(investmentLifecycle.fills) ? investmentLifecycle.fills : [];
  var lifecycleReviews = Array.isArray(investmentLifecycle.decisionReviews) ? investmentLifecycle.decisionReviews : [];
  var lifecycleAttributions = Array.isArray(investmentLifecycle.performanceAttributions) ? investmentLifecycle.performanceAttributions : [];
  var lifecycleBody = notificationReasoningTraceTags([
    investmentLifecycle.decisionEpisodeId ? "판단 " + investmentLifecycle.decisionEpisodeId : "판단 ID 미기록",
    "실행계획 " + lifecyclePlans.length,
    "실행 에피소드 " + lifecycleExecutions.length,
    "체결 " + lifecycleFills.length,
    "성과 귀속 " + lifecycleAttributions.length,
    "결과 리뷰 " + lifecycleReviews.length
  ], "notification-reasoning-tags");
  if (investmentLifecycle.decisionEpisode && Object.keys(investmentLifecycle.decisionEpisode).length) {
    lifecycleBody += '<details class="notification-ai-prompt-audit"><summary>DecisionEpisode 전체 데이터</summary><pre>' + escapeHtml(JSON.stringify(investmentLifecycle.decisionEpisode, null, 2)) + '</pre></details>';
  }
  if (lifecyclePlans.length) {
    lifecycleBody += '<details class="notification-ai-prompt-audit"><summary>ActionPlan 전체 데이터</summary><pre>' + escapeHtml(JSON.stringify(lifecyclePlans, null, 2)) + '</pre></details>';
  }
  if (lifecycleExecutions.length || lifecycleFills.length) {
    lifecycleBody += '<details class="notification-ai-prompt-audit"><summary>주문·체결 전체 데이터</summary><pre>' + escapeHtml(JSON.stringify({ executionEpisodes: lifecycleExecutions, fills: lifecycleFills }, null, 2)) + '</pre></details>';
  }
  if (lifecycleReviews.length) {
    lifecycleBody += '<details class="notification-ai-prompt-audit"><summary>성과·판단 리뷰 전체 데이터</summary><pre>' + escapeHtml(JSON.stringify(lifecycleReviews, null, 2)) + '</pre></details>';
  }
  if (lifecycleAttributions.length) {
    lifecycleBody += '<details class="notification-ai-prompt-audit"><summary>성과 귀속 전체 데이터</summary><pre>' + escapeHtml(JSON.stringify(lifecycleAttributions, null, 2)) + '</pre></details>';
  }
  return [
    '<section class="notification-detail-section notification-reasoning-section">',
    '<div class="notification-reasoning-head">',
    '<div><strong>실제 실행 순서</strong><span>단계별 요약을 먼저 표시합니다. 각 단계를 열면 생성 시점의 전체 데이터를 확인할 수 있습니다.</span></div>',
    '<span class="tone-chip ' + escapeHtml(status.tone) + '">' + escapeHtml(status.label) + '</span>',
    '</div>',
    '<div class="notification-reasoning-provenance">' + provenance.map(function (item) { return '<code>' + escapeHtml(item) + '</code>'; }).join("") + '</div>',
    '<ol class="notification-reasoning-flow">',
    renderNotificationReasoningStep(1, "원천 데이터·ABox 사실", facts.length + "개 사실, " + sources.length + "개 출처", missing.length ? "부족 데이터 " + missing.length + "건도 원본과 함께 표시합니다." : "기록된 부족 데이터 없음", factBody + sourceBody + notificationReasoningTraceTags(missing, "notification-reasoning-tags caution"), reasoningDisclosurePrefix + "1"),
    renderNotificationReasoningStep(2, "TypeDB 규칙 실행", rules.length + "개 규칙, " + inferenceTraces.length + "개 추론 경로 · 영역별 판단 포함", snapshot.inferenceGenerationId || "추론 세대 ID 미기록", executionLedgerBody + assessmentBody + ruleBody + traceBody, reasoningDisclosurePrefix + "2"),
    renderNotificationReasoningStep(3, "경쟁 가설 구성", hypotheses.length + "개 가설을 비교 후보로 구성했습니다.", "선택 표시는 다음 AI 단계의 결과이며, 후보 생성보다 먼저 실행된 것이 아닙니다.", hypothesisCandidatesBody, reasoningDisclosurePrefix + "3"),
    renderNotificationReasoningStep(4, decisionStepTitle, (finalDecision.actionLabel || finalDecision.primaryAction || "판단 기록 없음") + (finalDecision.summary ? " · " + finalDecision.summary : ""), finalDecision.validationLabel || finalDecision.dataStateLabel || "검증 상태 미기록", narrativeBody + aiExecutionBody + comparisonBody + hypothesisBody, reasoningDisclosurePrefix + "4"),
    renderNotificationReasoningStep(5, "판단·실행·성과 수명주기", investmentLifecycle.status === "ready" ? "판단과 실행계획이 연결됐습니다." : "연결된 실행 기록이 아직 없습니다.", investmentLifecycle.decisionEpisodeId || "DecisionEpisode ID 미기록", lifecycleBody, reasoningDisclosurePrefix + "5"),
    renderNotificationReasoningStep(6, "알림 발송", deliveryLabel, delivery.gateReason || "발송 정책과 반복 방지 정책을 통과한 결과입니다.", deliveryBody, reasoningDisclosurePrefix + "6"),
    '</ol>',
    comparison.unresolvedQuestions && comparison.unresolvedQuestions.length ? '<div class="notification-reasoning-appendix"><strong>미해결 질문</strong>' + notificationReasoningTraceTags(comparison.unresolvedQuestions, "notification-reasoning-tags") + '</div>' : '',
    auditBody ? '<div class="notification-reasoning-appendix"><strong>추론 무결성</strong>' + auditBody + '</div>' : '',
    '</section>'
  ].join("");
}

export { notificationActionFlowActionLabel, notificationJobDetailPayload, notificationPipelineDuration, notificationReverseReasoningTrace, renderNotificationActionFlow, renderNotificationDetailDisclosure, renderNotificationDetailMetric, renderNotificationInferenceStateTransition, renderNotificationJobResearchEvidence, renderNotificationLifecycleTrace, renderNotificationReverseReasoning, renderNotificationUnifiedPipeline };
