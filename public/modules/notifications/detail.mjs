import { relatedDecisionForNotification } from "../decisions/actions.mjs";
import { inferKnownStockSymbolFromText, stockDisplayName, textWithKnownDisplaySymbols } from "../instruments/catalog.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { notificationChangeStateLabel, notificationDeliveryStateLabel, notificationJobDecisionFactors, notificationJobDecisionRoute, notificationJobMarketHoursText, notificationJobQuietHoursText, notificationJobSimilarityText, notificationJobStateCooldownText, notificationJobStatusLabel, notificationJobToneClass, notificationJobTypeKey, notificationJobTypeLabel, notificationReviewLevelLabel, renderNotificationDecisionFactors, renderNotificationDecisionRoute, renderNotificationStateMessage, renderNotificationTriggerLedger } from "./history.mjs";
import { notificationActionFlowActionLabel, notificationJobDetailPayload, notificationPipelineDuration, notificationReverseReasoningTrace, renderNotificationActionFlow, renderNotificationDetailDisclosure, renderNotificationDetailMetric, renderNotificationInferenceStateTransition, renderNotificationJobResearchEvidence, renderNotificationLifecycleTrace, renderNotificationReverseReasoning, renderNotificationUnifiedPipeline } from "./reasoning.mjs";
import { notificationJobDetailSectionKey } from "./requests.mjs";
import { formatClock, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { app } from "../shell/root.mjs";
import { notificationsState } from "../state/notifications.mjs";

function renderNotificationDetailTabs(jobId, active) {
  var tabs = [
    ["summary", "요약"],
    ["reasoning", "규칙"],
    ["ai-review", "AI 검증"],
    ["delivery", "발송"]
  ];
  return '<nav class="notification-detail-tabs" role="tablist" aria-label="알림 상세 보기">' + tabs.map(function (item) {
    var selected = active === item[0];
    return '<button type="button" role="tab" data-notification-detail-tab="' + item[0] + '" data-notification-job-id="' + escapeHtml(jobId) + '" aria-selected="' + (selected ? "true" : "false") + '"' + (selected ? ' class="active"' : '') + '>' + escapeHtml(item[1]) + '</button>';
  }).join("") + '</nav>';
}

function renderNotificationDetailTabIntro(active) {
  var intro = {
    summary: ["Summary", "현재 판단과 이번 변화", "지금 무엇을 해야 하는지와 판단이 달라진 이유를 먼저 확인합니다."],
    reasoning: ["Rule proof", "성립 규칙과 관측값", "판단 당시 저장된 사실, 조건값, 반대 근거를 추적합니다."],
    "ai-review": ["AI review", "AI 실행과 최종 채택 비교", "모델이 만든 설명과 실제 투자 판단에 반영된 범위를 분리합니다."],
    delivery: ["Delivery", "발송 조건과 전달 이력", "발송 가능 여부, 반복 방지 정책, 채널 처리 결과를 확인합니다."]
  }[active] || ["Detail", "상세 정보", "선택한 단계의 저장 기록을 확인합니다."];
  return [
    '<header class="notification-detail-tab-head">',
    '<p class="label">' + escapeHtml(intro[0]) + '</p>',
    '<h4>' + escapeHtml(intro[1]) + '</h4>',
    '<span>' + escapeHtml(intro[2]) + '</span>',
    '</header>'
  ].join("");
}

function renderNotificationAIStatusBar(job) {
  var trace = notificationReverseReasoningTrace(job) || {};
  var execution = trace.aiExecution || {};
  var publication = ((trace.narrative || {}).publication) || execution.claimPublication || {};
  var executed = Boolean(execution.executed);
  var spans = execution.executionSpans && typeof execution.executionSpans === "object" ? execution.executionSpans : {};
  var attempts = Array.isArray(spans.modelAttempts) ? spans.modelAttempts : [];
  var firstAttempt = attempts[0] || {};
  var mode = execution.reviewMode === "context-narrative" ? "참고 서술" : "투자 판단";
  var adoption = execution.adoptionState || publication.status || (executed ? "결과 확인 필요" : "실행 기록 없음");
  return '<section class="notification-ai-status-bar"><span class="tone-chip ' + (executed ? "watch" : "hold") + '">' + escapeHtml(executed ? "AI 실행됨" : "AI 실행 없음") + '</span><div><strong>' + escapeHtml(mode) + '</strong><em>' + escapeHtml([execution.model, adoption].filter(Boolean).join(" · ")) + '</em></div><span>' + escapeHtml(execution.actionAuthority === "typedb" ? "행동 권한 TypeDB" : (execution.actionAuthority ? "행동 권한 " + execution.actionAuthority : "")) + '</span></section>';
}

function notificationDetailSectionState(jobId, section) {
  return notificationsState.notificationJobDetailSections[notificationJobDetailSectionKey(jobId, section)] || null;
}

function renderNotificationDetailSectionState(section) {
  if (!section || section.status === "loading") {
    return renderNotificationStateMessage("muted", "상세 데이터를 불러오는 중", "선택한 단계의 저장 데이터를 읽고 있습니다.");
  }
  if (section.status === "error") {
    return renderNotificationStateMessage("danger", "상세 데이터를 읽지 못했습니다", section.error || "잠시 후 다시 시도하세요.");
  }
  return "";
}

function notificationProofValue(value) {
  if (value === null || value === undefined || value === "") return "기록 없음";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function renderNotificationRuleProofSection(job, jobId) {
  var section = notificationDetailSectionState(jobId, "reasoning");
  var stateMessage = renderNotificationDetailSectionState(section);
  if (stateMessage) return stateMessage;
  var reasoning = section.reasoning || {};
  var evaluations = Array.isArray(reasoning.ruleEvaluations) ? reasoning.ruleEvaluations : [];
  var subjectCase = reasoning.subjectDecisionCase && typeof reasoning.subjectDecisionCase === "object"
    ? reasoning.subjectDecisionCase
    : {};
  var candidateSet = subjectCase.candidateSet && typeof subjectCase.candidateSet === "object"
    ? subjectCase.candidateSet
    : {};
  var publication = subjectCase.publication && typeof subjectCase.publication === "object"
    ? subjectCase.publication
    : {};
  var subjectScope = subjectCase.subjectCaseId ? [
    '<section class="notification-rule-proof-section">',
    '<header><div><strong>종목별 판단 경계</strong><span>이 알림에 사용된 계좌·종목·ABox 세대와 후보 집합을 고정한 기록입니다.</span></div><span class="tone-chip watch">' + escapeHtml(subjectCase.stage || "기록됨") + '</span></header>',
    '<dl class="notification-ai-review-summary">',
    '<div><dt>대상</dt><dd>' + escapeHtml([subjectCase.accountId, subjectCase.symbol].filter(Boolean).join(" · ") || "기록 없음") + '</dd></div>',
    '<div><dt>ABox·세대</dt><dd>' + escapeHtml([subjectCase.sourceAboxSnapshotId, subjectCase.inferenceGenerationId].filter(Boolean).join(" · ") || "기록 없음") + '</dd></div>',
    '<div><dt>후보 가설</dt><dd>' + escapeHtml((candidateSet.eligibleHypothesisIds || []).join(" · ") || "행동 후보 없음") + '</dd></div>',
    '<div><dt>발행 결과</dt><dd>' + escapeHtml([publication.outcomeKind, publication.decisionEpisodeId].filter(Boolean).join(" · ") || "아직 발행되지 않음") + '</dd></div>',
    '</dl>',
    '<div class="notification-reasoning-provenance"><code>' + escapeHtml(subjectCase.subjectCaseId) + '</code>' + (candidateSet.fingerprint ? '<code>' + escapeHtml("candidate " + candidateSet.fingerprint) + '</code>' : '') + '</div>',
    '<details class="notification-ai-prompt-audit"><summary>종목 판단 케이스 전체 데이터</summary><pre>' + escapeHtml(JSON.stringify(subjectCase, null, 2)) + '</pre></details>',
    '</section>'
  ].join("") : "";
  var reasoningJob = Object.assign({}, job, { reasoningTrace: reasoning });
  var executionTrace = renderNotificationReverseReasoning(reasoningJob);
  if (!evaluations.length) {
    return subjectScope + renderNotificationStateMessage("muted", "규칙 증명 기록 없음", "이전 형식의 알림이거나 당시 규칙별 관측값이 저장되지 않았습니다. 현재 그래프로 과거 판단을 재해석하지 않습니다.") + executionTrace;
  }
  return subjectScope + '<section class="notification-rule-proof-section"><header><div><strong>규칙 성립 증명</strong><span>추론 당시 저장된 기대값과 실제 관측값만 표시합니다.</span></div><span class="tone-chip watch">' + escapeHtml(evaluations.length + "개") + '</span></header><div class="notification-rule-proof-list">' + evaluations.map(function (evaluation) {
    var proof = evaluation.proof || {};
    var conditions = Array.isArray(proof.conditions) ? proof.conditions : [];
    var lineage = proof.premise_lineage || proof.premiseLineage || {};
    var proofStatus = String(proof.status || "legacy-unavailable");
    return [
      '<article class="notification-rule-proof-item">',
      '<header><span class="tone-chip ' + (proofStatus === "available" ? "watch" : "hold") + '">' + escapeHtml(proofStatus === "available" ? "증명 있음" : "이전 기록") + '</span><div><strong>' + escapeHtml(evaluation.rule_id || evaluation.ruleId || proof.rule_id || proof.ruleId || "규칙") + '</strong><em>' + escapeHtml(evaluation.selected ? "최종 경로에 채택" : "성립한 참고 경로") + '</em></div></header>',
      conditions.length ? '<dl>' + conditions.map(function (condition) {
        var field = condition.field || condition.relation_type || condition.relationType || condition.condition_id || condition.conditionId || "조건";
        var operator = condition.operator || "=";
        var expected = Object.prototype.hasOwnProperty.call(condition, "expected_value") ? condition.expected_value : condition.expectedValue;
        var observed = Object.prototype.hasOwnProperty.call(condition, "observed_value") ? condition.observed_value : condition.observedValue;
        var source = [condition.source, condition.source_as_of || condition.sourceAsOf, condition.freshness].filter(Boolean).join(" · ");
        return '<div><dt>' + escapeHtml(field) + '</dt><dd><strong>관측 ' + escapeHtml(notificationProofValue(observed)) + '</strong><span>' + escapeHtml(operator + " " + notificationProofValue(expected)) + '</span>' + (source ? '<em>' + escapeHtml(source) + '</em>' : '') + '</dd></div>';
      }).join("") + '</dl>' : '<p>당시 규칙 ID만 저장됐으며 조건별 관측값은 없습니다.</p>',
      (lineage.status === "available" || lineage.shared_generation_id || lineage.sharedGenerationId) ? '<footer><span>공유 전제 계보</span><code>' + escapeHtml(lineage.original_rule_id || lineage.originalRuleId || evaluation.rule_id || evaluation.ruleId || "-") + '</code><em>' + escapeHtml(lineage.shared_generation_id || lineage.sharedGenerationId || "세대 미기록") + '</em></footer>' : '',
      '</article>'
    ].join("");
  }).join("") + '</div></section>' + executionTrace;
}

function renderNotificationAIReviewSection(jobId) {
  var section = notificationDetailSectionState(jobId, "ai-review");
  var stateMessage = renderNotificationDetailSectionState(section);
  if (stateMessage) return stateMessage;
  var execution = section.aiExecution || {};
  var spans = execution.executionSpans && typeof execution.executionSpans === "object" ? execution.executionSpans : {};
  var attempts = Array.isArray(spans.modelAttempts) ? spans.modelAttempts : [];
  var firstAttempt = attempts[0] || {};
  var runtime = section.aiRuntime || {};
  var narrative = section.narrative || {};
  var publication = narrative.publication || execution.claimPublication || {};
  var writer = narrative.writerProvenance || execution.writerProvenance || {};
  var comparison = section.aiComparison || {};
  var finalDecision = section.finalDecision || {};
  var claims = Array.isArray(narrative.claims) ? narrative.claims : [];
  var validations = Array.isArray(narrative.validations) ? narrative.validations : [];
  var validationById = {};
  validations.forEach(function (item) { if (item && item.claimId) validationById[item.claimId] = item; });
  var executed = Boolean(execution.executed);
  return [
    '<section class="notification-ai-review-section">',
    '<header><div><strong>AI 실행과 채택 결과</strong><span>모델 실행 여부와 최종 알림에 사용된 범위를 분리해 표시합니다.</span></div><span class="tone-chip ' + (executed ? "watch" : "hold") + '">' + escapeHtml(executed ? "AI 실행됨" : "AI 실행 없음") + '</span></header>',
    '<dl class="notification-ai-review-summary">',
    '<div><dt>검토 모드</dt><dd>' + escapeHtml(execution.reviewMode === "context-narrative" ? "참고 서술" : "투자 판단") + '</dd></div>',
    '<div><dt>행동 권한</dt><dd>' + escapeHtml(execution.actionAuthority === "typedb" ? "TypeDB · AI는 서술만" : (execution.actionAuthority || "기록 없음")) + '</dd></div>',
    '<div><dt>채택 상태</dt><dd>' + escapeHtml(execution.adoptionState || publication.status || "기록 없음") + '</dd></div>',
    '<div><dt>실효 결과</dt><dd>' + escapeHtml([
      runtime.publicationMode === "ai-authored" ? "AI 판단·설명 채택" : (runtime.publicationMode === "typedb-fallback" ? "TypeDB 폴백" : runtime.publicationMode),
      runtime.publicationContractPassed ? "발행 계약 통과" : "발행 계약 미통과",
      runtime.contractFailureCode || ""
    ].filter(Boolean).join(" · ") || "기록 없음") + '</dd></div>',
    '<div><dt>모델·시간</dt><dd>' + escapeHtml([execution.model, execution.reasoningEffort, notificationPipelineDuration(execution.latencyMs)].filter(Boolean).join(" · ") || "기록 없음") + '</dd></div>',
    '<div><dt>실행 단계</dt><dd>' + escapeHtml([
      spans.completionPolicy === "wait-until-complete" ? "완료까지 대기" : "시간 제한 사용",
      spans.queueWaitMs != null ? "큐 " + notificationPipelineDuration(spans.queueWaitMs) : "",
      firstAttempt.capacityWaitMs != null ? "슬롯 " + notificationPipelineDuration(firstAttempt.capacityWaitMs) : "",
      spans.initialModelMs != null ? "첫 판단 " + notificationPipelineDuration(spans.initialModelMs) : "",
      spans.repairModelMs ? "교정 " + notificationPipelineDuration(spans.repairModelMs) : ""
    ].filter(Boolean).join(" · ") || "기록 없음") + '</dd></div>',
    '</dl>',
    '<div class="notification-ai-publication"><strong>문장 발행 결과</strong><span>' + escapeHtml([writer.label || writer.writerKind, "AI " + Number(publication.aiClaimCount || 0) + "개", "시스템 " + Number(publication.deterministicClaimCount || 0) + "개"].filter(Boolean).join(" · ")) + '</span></div>',
    '<div class="notification-ai-publication"><strong>최종 채택 판단</strong><span>' + escapeHtml([finalDecision.actionLabel || finalDecision.action || "기록 없음", comparison.comparisonStateLabel || comparison.comparisonState, finalDecision.validationLabel || finalDecision.validationState].filter(Boolean).join(" · ")) + '</span></div>',
    claims.length ? '<div class="notification-ai-claim-list">' + claims.map(function (claim) {
      var validation = validationById[claim.claimId] || {};
      return '<div><span class="tone-chip ' + (validation.status === "rejected" ? "caution" : "watch") + '">' + escapeHtml(validation.status === "rejected" ? "제외" : "채택") + '</span><p><strong>' + escapeHtml(claim.section || "문장") + '</strong>' + escapeHtml(claim.text || "") + '</p><em>' + escapeHtml((claim.evidenceIds || []).join(" · ") || "근거 ID 없음") + '</em></div>';
    }).join("") + '</div>' : '<p class="notification-reasoning-empty">발행된 AI 문장 기록이 없습니다.</p>',
    execution.prompt ? '<details class="notification-ai-prompt-audit"><summary>AI 입력 프롬프트</summary><pre>' + escapeHtml(execution.prompt) + '</pre></details>' : '<p class="notification-reasoning-empty">공유 화면에서는 원문 프롬프트를 표시하지 않습니다.</p>',
    '<details class="notification-ai-prompt-audit"><summary>AI 실행·검증 전체 데이터</summary><pre>' + escapeHtml(JSON.stringify({ aiExecution: execution, aiComparison: comparison, finalDecision: finalDecision, narrative: narrative }, null, 2)) + '</pre></details>',
    '</section>'
  ].join("");
}

function renderNotificationDeliverySection(job, jobId) {
  var section = notificationDetailSectionState(jobId, "delivery");
  var stateMessage = renderNotificationDetailSectionState(section);
  if (stateMessage) return stateMessage;
  var deliveryJob = Object.assign({}, job, {
    notificationTrace: {
      pipeline: section.pipeline || {},
      lifecycle: section.lifecycle || [],
      deliveryAttempts: section.deliveryAttempts || [],
      timeline: section.timeline || []
    }
  });
  var gateRows = [
    notificationJobSimilarityText(job),
    notificationJobStateCooldownText(job),
    notificationJobMarketHoursText(job),
    notificationJobQuietHoursText(job),
    job.suppressionSummary || "",
    job.nextEligibleAt ? "다음 발송 가능 " + formatClock(job.nextEligibleAt) : ""
  ].filter(Boolean);
  var payload = notificationJobDetailPayload(job);
  var reasons = Array.isArray(payload.reasons) ? payload.reasons : [];
  var deliveryAudit = [
    '<section class="notification-detail-section"><strong>발송 조건 상세</strong>',
    '<div class="notification-detail-metrics notification-detail-secondary-metrics">',
    renderNotificationDetailMetric("확인 단계", notificationReviewLevelLabel(job.deliveryReviewLevel), "muted"),
    renderNotificationDetailMetric("반복 판단", notificationJobSimilarityText(job), "muted"),
    renderNotificationDetailMetric("발송 가능", job.nextEligibleAt ? formatClock(job.nextEligibleAt) : "조건 충족 시", "muted"),
    '</div>',
    renderNotificationInferenceStateTransition(job),
    renderNotificationTriggerLedger(job),
    gateRows.length ? '<div class="notification-detail-tags">' + gateRows.map(function (row) { return '<span>' + escapeHtml(textWithKnownDisplaySymbols(row, payload.resolvedSymbol, job)) + '</span>'; }).join("") + '</div>' : '',
    reasons.length ? '<div class="notification-detail-reasons">' + reasons.map(function (reason) { return '<p>' + escapeHtml(textWithKnownDisplaySymbols(reason, payload.resolvedSymbol, job)) + '</p>'; }).join("") + '</div>' : '',
    '</section>'
  ].join("");
  return deliveryAudit + renderNotificationUnifiedPipeline(deliveryJob) + renderNotificationLifecycleTrace(deliveryJob);
}

function renderNotificationDeliveryDecisionOverview(job, decisionFactors, compact) {
  var payload = notificationJobDetailPayload(job);
  return [
    '<section class="notification-detail-section notification-delivery-section">',
    '<strong>발송 판단과 근거</strong>',
    renderNotificationDecisionRoute(job),
    decisionFactors.length ? '<div class="notification-detail-tags notification-delivery-tags">' + (compact ? decisionFactors.slice(0, 3) : decisionFactors).map(function (factor) {
      return '<span class="' + escapeHtml(factor.tone || "hold") + '">' + escapeHtml(textWithKnownDisplaySymbols(factor.label, payload.resolvedSymbol, job)) + '</span>';
    }).join("") + '</div>' : '<p>발송 판단 근거가 아직 기록되지 않았습니다.</p>',
    '</section>'
  ].join("");
}

function notificationCustomerDocument(job) {
  return job && job.customerInvestmentDocument && typeof job.customerInvestmentDocument === "object"
    ? job.customerInvestmentDocument
    : {};
}

function renderNotificationCustomerDocument(job, compact) {
  var customerDocument = notificationCustomerDocument(job);
  if (!Object.keys(customerDocument).length) return "";
  var customerSections = Array.isArray(customerDocument.sections) ? customerDocument.sections : [];
  var customerLinks = Array.isArray(customerDocument.links) ? customerDocument.links : [];
  if (compact) {
    var compactKeys = customerDocument.role === "typedb-observation"
      ? ["change", "importance", "tracking", "next-update"]
      : ["change", "action", "reasons", "counter", "tracking", "next-update"];
    customerSections = customerSections.filter(function (section) {
      return compactKeys.indexOf(String((section || {}).key || "")) >= 0;
    }).slice(0, compactKeys.length);
  }
  return [
    '<section class="notification-detail-section primary notification-customer-document">',
    '<div class="notification-reasoning-head"><div><strong>' + escapeHtml(customerDocument.headline || "투자 인사이트") + '</strong><span>' + escapeHtml(customerDocument.roleLabel || "") + '</span></div></div>',
    customerDocument.lead ? '<div class="notification-detail-reasons"><p><b>한눈에 보기</b> ' + escapeHtml(customerDocument.lead) + '</p></div>' : '',
    customerSections.map(function (section) {
      var rows = Array.isArray(section.rows) ? section.rows : [];
      if (!rows.length) return "";
      if (compact) rows = rows.slice(0, 2);
      return '<div class="notification-detail-reasons"><strong>' + escapeHtml(section.title || "상세") + '</strong>'
        + rows.map(function (row) { return '<p>' + escapeHtml(row) + '</p>'; }).join("")
        + '</div>';
    }).join(""),
    customerLinks.length ? '<div class="notification-detail-reasons"><strong>원문</strong>'
      + customerLinks.map(function (link) {
        var url = String((link || {}).url || "");
        if (!/^https?:\/\//i.test(url)) return "";
        return '<p><a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml((link || {}).label || "원문 보기") + '</a></p>';
      }).join("")
      + '</div>' : '',
    '</section>'
  ].join("");
}

function renderNotificationSummaryTab(job, context) {
  context = context || {};
  var customerDocument = notificationCustomerDocument(job);
  var hasCustomerDocument = Boolean(Object.keys(customerDocument).length);
  var customerDocumentBody = renderNotificationCustomerDocument(job, false);
  return [
    hasCustomerDocument ? '' :
    '<div class="notification-detail-metrics notification-summary-metrics">',
    hasCustomerDocument ? '' :
    renderNotificationDetailMetric("지금 행동", context.currentAction, context.actionFlow.status === "ENTRY_ELIGIBLE" ? "watch" : "hold"),
    hasCustomerDocument ? '' :
    renderNotificationDetailMetric("이번 변화", notificationChangeStateLabel(job.deliveryChangeState), "muted"),
    hasCustomerDocument ? '' :
    renderNotificationDetailMetric("발송 판단", notificationDeliveryStateLabel(job.deliveryDecision), notificationJobDecisionRoute(job).tone),
    hasCustomerDocument ? '' :
    renderNotificationDetailMetric("상태", notificationJobStatusLabel(job.status), notificationJobToneClass(job.status)),
    hasCustomerDocument ? '' :
    '</div>',
    customerDocumentBody || '<section class="notification-detail-section primary">',
    customerDocumentBody ? '' :
    '<strong>판단 요약</strong>',
    customerDocumentBody ? '' : '<p>' + escapeHtml((((job.reasoningTrace || {}).finalDecision || {}).summary) || context.payload.fullText || context.payload.preview) + '</p>',
    customerDocumentBody ? '' : '</section>',
    customerDocumentBody ? '' : renderNotificationActionFlow(job),
    context.relatedDecision ? '<section class="notification-detail-section"><strong>관련 현재 판단</strong><p>' + escapeHtml([context.relatedDecision.name || context.relatedDecision.symbol, context.relatedDecision.actionLabel, context.relatedDecision.reason].filter(Boolean).join(" · ")) + '</p>' + renderWorkDetailButton("investment-action", context.relatedDecision.key, "현재 판단 보기", "text-button compact") + '</section>' : '',
    renderNotificationDetailDisclosure("전체 메시지와 식별 정보", "원문 메시지와 중복 판단 키", context.messageDetails, "notification-summary-disclosure", "notification-job:" + context.jobId + ":summary:message"),
    renderNotificationDetailDisclosure("연결된 원문과 출처", "종목에 연결된 최신 뉴스·공시 근거", context.researchDetails, "notification-summary-disclosure", "notification-job:" + context.jobId + ":summary:research")
  ].join("");
}

function renderNotificationDecisionDetail(job, options) {
  if (!job) {
    return renderEmptyState({
      tone: "muted",
      label: "Report",
      title: "선택된 알림이 없습니다",
      description: "왼쪽 목록에서 알림 판단을 선택하면 상세 리포트를 표시합니다."
    });
  }
  options = options || {};
  var compact = Boolean(options.compact);
  var payload = notificationJobDetailPayload(job);
  var jobId = notificationJobKey(job);
  var activeDetailTab = notificationsState.notificationJobDetailTabs[jobId] || "summary";
  var fingerprint = textWithKnownDisplaySymbols(job.deliveryFingerprint || "", payload.resolvedSymbol, job);
  var decisionFactors = notificationJobDecisionFactors(job);
  var detailButton = compact ? '<div class="notification-detail-actions">' + renderWorkDetailButton("notification-job", notificationJobKey(job), "알림·추론 상세", "text-button primary compact") + '</div>' : '';
  var relatedDecision = relatedDecisionForNotification(job);
  var actionFlow = job.actionFlow && typeof job.actionFlow === "object" ? job.actionFlow : {};
  var currentAction = actionFlow.currentActionLabel || notificationActionFlowActionLabel(actionFlow.currentAction);
  var customerDocumentBody = renderNotificationCustomerDocument(job, true);
  var messageDetails = !compact ? [
    payload.fullText && payload.fullText !== payload.preview ? '<section class="notification-detail-section"><strong>전체 메시지</strong><pre class="notification-full-message">' + escapeHtml(payload.fullText) + '</pre></section>' : '',
    fingerprint ? '<section class="notification-detail-section"><strong>중복 판단 키</strong><code class="notification-fingerprint">' + escapeHtml(fingerprint) + '</code></section>' : ''
  ].join("") : "";
  var researchDetails = !compact ? renderNotificationJobResearchEvidence(job) : "";
  var deliveryOverview = renderNotificationDeliveryDecisionOverview(job, decisionFactors, compact);
  var detailTabBody = "";
  if (!compact) {
    if (activeDetailTab === "reasoning") detailTabBody = renderNotificationRuleProofSection(job, jobId);
    else if (activeDetailTab === "ai-review") detailTabBody = renderNotificationAIStatusBar(job) + renderNotificationAIReviewSection(jobId);
    else if (activeDetailTab === "delivery") detailTabBody = deliveryOverview + renderNotificationDeliverySection(job, jobId);
    else detailTabBody = renderNotificationSummaryTab(job, {
      actionFlow: actionFlow,
      currentAction: currentAction,
      jobId: jobId,
      messageDetails: messageDetails,
      payload: payload,
      relatedDecision: relatedDecision,
      researchDetails: researchDetails
    });
    detailTabBody = renderNotificationDetailTabIntro(activeDetailTab) + detailTabBody;
  }
  var feedbackOptions = [
    { usefulness: "helpful", reason: "actionable", label: "도움됨" },
    { usefulness: "not-helpful", reason: "too-vague", label: "모호함" },
    { usefulness: "not-helpful", reason: "not-relevant", label: "관련 없음" }
  ];
  var feedbackActions = '<div class="notification-feedback-actions" aria-label="알림 유용성 평가"><span>알림 평가</span>' + feedbackOptions.map(function (option) {
    var selected = String(job.usefulness || "") === option.usefulness && String(job.feedbackReason || "") === option.reason;
    return '<button class="text-button compact' + (selected ? ' selected' : '') + '" type="button" data-notification-feedback data-notification-job-id="' + escapeHtml(notificationJobKey(job)) + '" data-notification-usefulness="' + escapeHtml(selected ? "" : option.usefulness) + '" data-notification-feedback-reason="' + escapeHtml(selected ? "" : option.reason) + '" aria-pressed="' + (selected ? "true" : "false") + '">' + escapeHtml(option.label) + '</button>';
  }).join("") + '</div>';
  var receiptActions = '<div class="notification-detail-actions"><button class="text-button compact" type="button" data-notification-receipt="important" data-notification-job-id="' + escapeHtml(notificationJobKey(job)) + '" data-notification-receipt-value="' + escapeHtml(job.important ? "false" : "true") + '">' + escapeHtml(job.important ? "중요 해제" : "중요 표시") + '</button><button class="text-button compact" type="button" data-notification-receipt="acknowledged" data-notification-job-id="' + escapeHtml(notificationJobKey(job)) + '" data-notification-receipt-value="' + escapeHtml(job.acknowledgedAt ? "false" : "true") + '">' + escapeHtml(job.acknowledgedAt ? "확인 취소" : "확인 완료") + '</button></div>';
  return [
    '<aside class="notification-decision-detail" data-notification-detail-mode="' + (compact ? "compact" : "full") + '" data-notification-detail-job-id="' + escapeHtml(jobId) + '" data-notification-active-tab="' + escapeHtml(activeDetailTab) + '" aria-label="선택 알림 판단 상세">',
    compact ? '<div class="notification-detail-head"><div><p class="label">Decision Report</p><h3>' + escapeHtml(payload.title || payload.displaySymbol || job.messageTypeLabel || job.messageType || "알림 판단") + '</h3><span>' + escapeHtml([payload.displaySymbol, notificationJobTypeLabel(notificationJobTypeKey(job), [job]), formatClock(job.createdAt)].filter(Boolean).join(" · ")) + '</span></div><span class="tone-chip ' + escapeHtml(notificationJobToneClass(job.status)) + '">' + escapeHtml(notificationJobStatusLabel(job.status)) + '</span></div>' : '',
    compact ? receiptActions : '<div class="notification-detail-toolbar"><span class="tone-chip ' + escapeHtml(notificationJobToneClass(job.status)) + '">' + escapeHtml(notificationJobStatusLabel(job.status)) + '</span>' + receiptActions + '</div>',
    feedbackActions,
    compact ? '' : renderNotificationDetailTabs(jobId, activeDetailTab),
    compact && !customerDocumentBody ? '<div class="notification-detail-metrics">' + renderNotificationDetailMetric("발송 판단", notificationDeliveryStateLabel(job.deliveryDecision), notificationJobDecisionRoute(job).tone) + renderNotificationDetailMetric("지금 행동", currentAction, actionFlow.status === "ENTRY_ELIGIBLE" ? "watch" : "hold") + renderNotificationDetailMetric("이번 변화", notificationChangeStateLabel(job.deliveryChangeState), "muted") + renderNotificationDetailMetric("상태", notificationJobStatusLabel(job.status), notificationJobToneClass(job.status)) + '</div>' : '',
    compact ? (customerDocumentBody || '<section class="notification-detail-section primary"><strong>판단 요약</strong><p>' + escapeHtml((((job.reasoningTrace || {}).finalDecision || {}).summary) || payload.fullText || payload.preview) + '</p></section>') : '',
    compact && !customerDocumentBody ? renderNotificationActionFlow(job) : '',
    compact && relatedDecision ? '<section class="notification-detail-section"><strong>관련 현재 판단</strong><p>' + escapeHtml([relatedDecision.name || relatedDecision.symbol, relatedDecision.actionLabel, relatedDecision.reason].filter(Boolean).join(" · ")) + '</p>' + renderWorkDetailButton("investment-action", relatedDecision.key, "현재 판단 보기", "text-button compact") + '</section>' : '',
    compact ? deliveryOverview : '',
    compact ? '<p class="data-refresh-status">전체 메시지, 전체 근거, 중복 키는 상세 리포트에서 확인합니다.</p>' : '',
    detailButton,
    compact ? '' : '<div class="notification-detail-tab-panel" role="tabpanel" tabindex="-1" data-work-detail-region="notification-detail-content">' + detailTabBody + '</div>',
    '</aside>'
  ].join("");
}

function notificationDetailRoot(jobId) {
  return Array.prototype.slice.call(app.querySelectorAll("[data-notification-detail-job-id]")).filter(function (element) {
    return element.getAttribute("data-notification-detail-job-id") === String(jobId || "")
      && element.getAttribute("data-notification-detail-mode") === "full";
  })[0] || null;
}

function notificationJobKey(job) {
  return String((job && job.jobId) || [job && job.createdAt, job && job.messageType, job && job.title].join(":"));
}

function notificationJobFullText(job, resolvedSymbol) {
  return textWithKnownDisplaySymbols(String((job && (job.fullText || job.text)) || ""), resolvedSymbol, job);
}

function notificationJobExpanded(job) {
  return Boolean((notificationsState.notificationExpandedJobs || {})[notificationJobKey(job)]);
}

function renderNotificationDecisionRow(job, selected) {
  var reasons = Array.isArray(job.deliveryReasons) ? job.deliveryReasons.slice(0, 5) : [];
  var resolvedSymbol = notificationJobResolvedSymbol(job);
  var displaySymbol = resolvedSymbol ? stockDisplayName(resolvedSymbol, job) : "";
  var title = textWithKnownDisplaySymbols(job.title || "", resolvedSymbol, job);
  var target = title || displaySymbol || job.messageType || "-";
  if (displaySymbol && resolvedSymbol && target.toUpperCase() === String(resolvedSymbol || "").toUpperCase()) {
    target = displaySymbol;
  }
  if (displaySymbol && title && title.indexOf(displaySymbol) < 0) {
    target = title + " / " + displaySymbol;
  }
  var preview = textWithKnownDisplaySymbols(job.lastError || job.textPreview || "-", resolvedSymbol, job);
  var suppression = textWithKnownDisplaySymbols(job.suppressionSummary || "", resolvedSymbol, job);
  var nextEligible = job.nextEligibleAt ? "다음 가능 " + formatClock(job.nextEligibleAt) : "";
  var processing = job.recoverableProcessing ? "처리 중 지연 " + String(job.processingAgeMinutes || 0) + "분 · 워커 재시도 가능" : "";
  var rowKey = notificationJobKey(job);
  var rowSignals = [
    notificationJobSimilarityText(job),
    notificationJobStateCooldownText(job),
    notificationJobMarketHoursText(job),
    notificationJobQuietHoursText(job),
    suppression,
    nextEligible,
    processing,
    job.repeatBypassed ? (job.repeatBypassReason ? "반복 보류 해제 " + job.repeatBypassReason : "반복 보류 해제") : ""
  ].filter(Boolean).slice(0, 3);
  return [
    '<div class="notification-decision-row ' + (selected ? "active " : "") + escapeHtml(notificationJobToneClass(job.status)) + '"' + cardTypeAttrs("decision-row", notificationJobToneClass(job.status)) + cardFormatAttrs("decision-ticket", "compact") + ' role="option" tabindex="0" data-notification-job-select="' + escapeHtml(rowKey) + '" aria-selected="' + escapeHtml(selected ? "true" : "false") + '">',
    '<div class="notification-decision-top">',
    '<span class="tone-chip ' + escapeHtml(notificationJobToneClass(job.status)) + '">' + escapeHtml(notificationJobStatusLabel(job.status)) + '</span>',
    '<strong>' + escapeHtml(notificationJobTypeLabel(notificationJobTypeKey(job), [job])) + '</strong>',
    renderRecordChangedAt(job),
    '</div>',
    '<div class="notification-decision-target">' + escapeHtml(target || job.messageType || "-") + '</div>',
    '<div class="notification-decision-state">',
    renderNotificationDecisionRoute(job),
    rowSignals.map(function (signal) {
      return '<span>' + escapeHtml(signal) + '</span>';
    }).join(""),
    '</div>',
    '<p>' + escapeHtml(preview) + '</p>',
    '<div class="notification-decision-actions">',
    '<span class="mini-button ghost">' + escapeHtml(selected ? "리포트 표시 중" : "행 선택") + '</span>',
    '</div>',
    renderNotificationDecisionFactors(job, 4),
    reasons.length ? '<div class="notification-decision-reasons compact">' + reasons.slice(0, 1).map(function (reason) {
      return '<span>' + escapeHtml(textWithKnownDisplaySymbols(reason, resolvedSymbol, job)) + '</span>';
    }).join("") + '</div>' : '',
    '</div>'
  ].join("");
}

function notificationJobResolvedSymbol(job) {
  var explicit = String(job && (job.symbol || job.rawSymbol) || "").trim().toUpperCase();
  if (explicit) return explicit;
  var reasons = Array.isArray(job && job.deliveryReasons) ? job.deliveryReasons.join(" ") : "";
  return inferKnownStockSymbolFromText([
    job && job.title,
    job && job.textPreview,
    job && job.lastError,
    job && job.deliveryFingerprint,
    reasons
  ].join(" "));
}

export { notificationJobFullText, notificationJobKey, notificationJobResolvedSymbol, renderNotificationDecisionDetail, renderNotificationDecisionRow };
