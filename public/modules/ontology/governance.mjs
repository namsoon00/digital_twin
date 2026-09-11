import { renderLabStat } from "./graphs.mjs";
import { ontologyAccountOptions } from "./requests.mjs";
import { defaultSettings } from "../settings/defaults.mjs";
import { settingValue } from "../settings/fields.mjs";
import { latestChangedFirst, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { settingsState } from "../state/settings.mjs";

function promptTemplateRows() {
  var rows = [];
  var current = null;
  String(settingValue("aiPromptTemplates") || defaultSettings.aiPromptTemplates || "")
    .split(/\r?\n/)
    .forEach(function (line) {
      var cleaned = line.trim();
      if (!cleaned) return;
      var header = cleaned.match(/^\[([^\]]+)\]$/);
      if (header) {
        current = { id: header[1], label: header[1], purpose: "", version: "" };
        rows.push(current);
        return;
      }
      if (!current || cleaned.indexOf("=") < 0) return;
      var key = cleaned.split("=", 1)[0].trim();
      var value = cleaned.slice(cleaned.indexOf("=") + 1).trim();
      current[key] = value;
      if (key === "label") current.label = value;
      if (key === "purpose") current.purpose = value;
      if (key === "version") current.version = value;
    });
  return rows;
}

function renderTypeDBDiagnosticsPanel() {
  var diagnostics = ontologyState.ontologyDiagnostics || {};
  var tbox = diagnostics.tbox || {};
  var rulebox = diagnostics.rulebox || {};
  var aboxCoverage = diagnostics.aboxCoverage || {};
  var inferenceBox = diagnostics.inferenceBox || {};
  var reasoningBoundary = diagnostics.reasoningBoundary || {};
  var notificationBoundary = diagnostics.notificationBoundary || {};
  var investmentAlertCoverage = diagnostics.investmentAlertCoverage || {};
  var strategyProposalBoundary = diagnostics.strategyProposalBoundary || {};
  var runtimeObservability = diagnostics.runtimeObservability || {};
  var runtimeLatest = runtimeObservability.latest || {};
  var runtimeInference = runtimeLatest.inference || {};
  var runtimeScope = runtimeLatest.scope || {};
  var nativeTiming = runtimeInference.nativeRuleTiming || {};
  var impactDiagnostics = runtimeScope.impactDiagnostics || {};
  var replayValidation = runtimeInference.replayValidation || {};
  var auditHealth = runtimeObservability.auditHealth || {};
  var inferencePlan = inferenceBox.executionPlan || {};
  var runtimeExecution = inferenceBox.runtimeExecution || runtimeInference;
  var inferenceQueryMetrics = inferenceBox.queryMetrics || {};
  var reasoning = ontologyState.ontologyReasoningStatus || {};
  var queueHealth = reasoning.queueHealth || {};
  var queueDispatch = reasoning.queueDispatch || {};
  var globalScopeTypes = (impactDiagnostics.globalScopeTypes || []).map(function (item) {
    if (typeof item === "string") return item;
    return item && (item.label || item.type)
      ? String(item.label || item.type) + (item.count != null ? " " + item.count + "개" : "")
      : "";
  }).filter(Boolean);
  var selectionApplied = Boolean(replayValidation.selectionApplied || runtimeExecution.nativeRuleSelectionApplied);
  var replayStatus = String(replayValidation.status || "unknown");
  var replayCheckStatus = selectionApplied && !replayValidation.verified
    ? "error"
    : (replayValidation.verified ? "ok" : "unknown");
  var auditRecovery = auditHealth.lastRecovery || {};
  var queueStatus = String(queueHealth.status || "unknown");
  var worldId = String(diagnostics.worldId || "");
  var worlds = Array.isArray(diagnostics.worlds) ? diagnostics.worlds : [];
  var rawChecks = Array.isArray(diagnostics.checks) ? diagnostics.checks : (Array.isArray(notificationBoundary.checks) ? notificationBoundary.checks : []);
  var checks = [
    { status: tbox.status || (tbox.configured ? "ok" : ""), title: "TBox", message: tbox.reason || tbox.source || tbox.fingerprint || "" },
    { status: rulebox.status || (rulebox.configured ? "ok" : ""), title: "RuleBox", message: rulebox.reason || rulebox.source || rulebox.ruleboxShortHash || rulebox.engineVersion || "" },
    { status: aboxCoverage.status || "", title: "ABox Coverage", message: [aboxCoverage.primarySymbolCount != null ? aboxCoverage.primarySymbolCount + " primary" : aboxCoverage.symbolCount != null ? aboxCoverage.symbolCount + " symbols" : "", aboxCoverage.primaryCoverageRatio != null ? "primary " + Math.round(Number(aboxCoverage.primaryCoverageRatio || 0) * 100) + "%" : aboxCoverage.coverageRatio != null ? "coverage " + Math.round(Number(aboxCoverage.coverageRatio || 0) * 100) + "%" : "", aboxCoverage.contextSymbolCount != null ? aboxCoverage.contextSymbolCount + " context" : ""].filter(Boolean).join(" · ") },
    { status: inferenceBox.status || inferenceBox.typedbReadStatus || "", title: "InferenceBox", message: inferenceBox.reason || inferenceBox.typedbReadReason || inferenceBox.reasoningMode || inferenceBox.inferenceGenerationId || "" },
    { status: inferenceBox.targetCoverageStatus || "unknown", title: "Inference Coverage", message: [inferenceBox.targetCoverageStatus === "partial" && inferenceBox.notEvaluatedSymbols && inferenceBox.notEvaluatedSymbols.length ? "미실행 " + inferenceBox.notEvaluatedSymbols.join(", ") : "", inferenceBox.requestedSymbols && inferenceBox.requestedSymbols.length ? "요청 " + inferenceBox.requestedSymbols.join(", ") : "", inferenceBox.evaluatedSymbols && inferenceBox.evaluatedSymbols.length ? "계산 " + inferenceBox.evaluatedSymbols.join(", ") : "", inferenceBox.targetCoverageReason || ""].filter(Boolean).join(" · ") },
    { status: inferencePlan.status || runtimeExecution.executionStatus || (inferencePlan.selectedRuleCount != null || runtimeExecution.executedRuleCount != null ? "ok" : "unknown"), title: "Native Execution Plan", message: [inferencePlan.candidateRuleCount != null || runtimeExecution.candidateRuleCount != null ? "후보 " + (inferencePlan.candidateRuleCount != null ? inferencePlan.candidateRuleCount : runtimeExecution.candidateRuleCount) + "개" : "", runtimeExecution.enabledRuleCount != null ? "전체 " + runtimeExecution.enabledRuleCount + "개" : "", runtimeExecution.candidateRuleRatioPct != null ? "후보 비율 " + Math.round(Number(runtimeExecution.candidateRuleRatioPct || 0)) + "%" : "", inferencePlan.selectedRuleCount != null || runtimeExecution.executedRuleCount != null ? "실행 " + (inferencePlan.selectedRuleCount != null ? inferencePlan.selectedRuleCount : runtimeExecution.executedRuleCount) + "개" : "", inferencePlan.preflightPrunedRuleCount ? "사전 제외 " + inferencePlan.preflightPrunedRuleCount + "개" : "", runtimeExecution.deferredRuleCount ? "보류 " + runtimeExecution.deferredRuleCount + "개" : "", runtimeExecution.nativeRuleSelectionEligibilityReason || "", inferenceQueryMetrics.queryCount != null ? "쿼리 " + inferenceQueryMetrics.queryCount + "회 / " + Number(inferenceQueryMetrics.totalDurationMs || 0).toFixed(1) + "ms" : ""].filter(Boolean).join(" · ") },
    { status: impactDiagnostics.classification || (runtimeScope.globalImpact ? "global" : "unknown"), title: "변경 영향 범위", message: [runtimeScope.directChangedScopeCount != null ? "직접 변경 " + runtimeScope.directChangedScopeCount + "개" : "", runtimeScope.affectedScopeCount != null ? "영향 " + runtimeScope.affectedScopeCount + "개" : "", runtimeScope.globalImpact ? "공유 문맥 포함" : "", globalScopeTypes.length ? globalScopeTypes.join(", ") : "", impactDiagnostics.eventScopeAgreement ? "이벤트 범위 " + impactDiagnostics.eventScopeAgreement : "", impactDiagnostics.unexpectedChangedFamilies && impactDiagnostics.unexpectedChangedFamilies.length ? "예상 밖 " + impactDiagnostics.unexpectedChangedFamilies.join(", ") : ""].filter(Boolean).join(" · ") },
    { status: replayCheckStatus, title: "TypeDB 완전성 검증", message: [replayStatus, replayValidation.selectionApplied ? "선택 실행" : "완전 실행", replayValidation.coverageComplete ? "대상 범위 확인" : "대상 범위 미확인", replayValidation.generationAligned ? "세대 정렬" : "세대 불일치", replayValidation.reason || ""].filter(Boolean).join(" · ") },
    { status: Number(auditHealth.windowProjectingCount || 0) > 0 ? "caution" : "ok", title: "투영 감사 복구", message: [auditHealth.staleAfterSeconds != null ? auditHealth.staleAfterSeconds + "초 후 중단 기록 정리" : "", auditHealth.windowProjectingCount ? "진행 중 " + auditHealth.windowProjectingCount + "개" : "", auditHealth.windowAbortedStaleCount ? "중단 복구 기록 " + auditHealth.windowAbortedStaleCount + "개" : "", auditRecovery.abortedCount ? "최근 정리 " + auditRecovery.abortedCount + "개" : ""].filter(Boolean).join(" · ") },
    { status: runtimeObservability.status || "unavailable", title: "Runtime SLO", message: [runtimeObservability.sampleCount != null ? "표본 " + runtimeObservability.sampleCount + "회" : "", runtimeLatest.durationMs != null ? "최근 " + runtimeLatest.durationMs + "ms" : "", nativeTiming.executedRuleCount != null ? "규칙 " + nativeTiming.executedRuleCount + "개" : "", nativeTiming.aggregateQueryDurationMs != null ? "네이티브 쿼리 " + nativeTiming.aggregateQueryDurationMs + "ms" : "", runtimeObservability.interpretation || ""].filter(Boolean).join(" · ") },
    { status: queueStatus, title: "추론 대기열", message: [queueDispatch.mode || "대기열 정보 없음", queueDispatch.selectedWorkClasses && queueDispatch.selectedWorkClasses.length ? "선택 " + queueDispatch.selectedWorkClasses.join(", ") : "", queueDispatch.selectedSymbols && queueDispatch.selectedSymbols.length ? "대상 " + queueDispatch.selectedSymbols.join(", ") : "", queueDispatch.fairnessDrainActive ? "공정성 처리" : "", queueDispatch.backpressureActive ? "백프레셔" : "", queueDispatch.effectiveIntervalSeconds != null ? "다음 간격 " + queueDispatch.effectiveIntervalSeconds + "초" : "", queueHealth.reason || ""].filter(Boolean).join(" · ") },
    { status: reasoningBoundary.status || "", title: "Reasoning Boundary", message: reasoningBoundary.interpretation || reasoningBoundary.ruleboxHashStatus || "" },
    { status: notificationBoundary.status || "", title: "Notification Boundary", message: notificationBoundary.reason || (notificationBoundary.recentJobCount != null ? notificationBoundary.recentJobCount + " recent jobs" : "") },
    { status: investmentAlertCoverage.status || "", title: "투자 알림 커버리지", message: [investmentAlertCoverage.materialEventCount != null ? "중요 사건 " + investmentAlertCoverage.materialEventCount + "건" : "", investmentAlertCoverage.terminalCoveragePct != null ? "종결 " + investmentAlertCoverage.terminalCoveragePct + "%" : "", investmentAlertCoverage.deliveryEligibleCandidateCount != null ? "발송 가치 " + investmentAlertCoverage.deliveryEligibleCandidateCount + "건" : "", investmentAlertCoverage.deliveredEligibleCandidateCount != null ? "전달 " + investmentAlertCoverage.deliveredEligibleCandidateCount + "건" : "", investmentAlertCoverage.overdueEventCount ? "지연 " + investmentAlertCoverage.overdueEventCount + "건" : "", investmentAlertCoverage.failedEventCount ? "실패 " + investmentAlertCoverage.failedEventCount + "건" : "", investmentAlertCoverage.reason || ""].filter(Boolean).join(" · ") },
    { status: strategyProposalBoundary.status || "", title: "Strategy Proposal Boundary", message: strategyProposalBoundary.nextAction || (strategyProposalBoundary.count != null ? strategyProposalBoundary.count + " proposals" : "") }
  ].concat(rawChecks.map(function (check, index) {
    return typeof check === "string"
      ? { status: "check", title: "Boundary check " + (index + 1), message: check }
      : (check || {});
  })).filter(function (check) {
    return check && (check.title || check.name || check.id || check.message || check.detail || check.description || check.status);
  });
  var status = diagnostics.status || reasoningBoundary.status || inferenceBox.status || rulebox.status || aboxCoverage.status || (ontologyState.ontologyDiagnosticsError ? "error" : checks.length ? "ok" : "idle");
  return [
    '<div class="model-section typedb-diagnostics-panel">',
    '<div class="flow-title"><div><strong>TypeDB 진단</strong><span>스키마, RuleBox, 추론 실행 상태를 실제 저장소 기준으로 확인합니다.</span></div><span class="tone-chip ' + escapeHtml(status === "ok" ? "watch" : status === "error" ? "danger" : "hold") + '">' + escapeHtml(status) + '</span></div>',
    ontologyState.ontologyDiagnosticsLoading ? '<p class="lab-message">TypeDB 진단을 읽는 중입니다.</p>' : '',
    ontologyState.ontologyDiagnosticsError ? '<p class="form-error">' + escapeHtml(ontologyState.ontologyDiagnosticsError) + '</p>' : '',
    '<div class="lab-stats-grid model-stats-grid">',
    renderLabStat("엔티티", aboxCoverage.entityCount || inferenceBox.entityCount || 0, "개"),
    renderLabStat("관계", aboxCoverage.relationCount || inferenceBox.relationCount || 0, "개"),
    renderLabStat("규칙", rulebox.ruleCount || rulebox.ruleboxRuleCount || 0, "개"),
    renderLabStat("검사", checks.length, "개"),
    '</div>',
    worldId || worlds.length ? '<div class="rulebox-console-strip"><span><strong>world</strong>' + escapeHtml(worldId || "선택 필요") + '</span><span><strong>active worlds</strong>' + escapeHtml(worlds.length) + '</span></div>' : '',
    '<div class="source-stack rulebox-diagnostics-list">',
    checks.length ? checks.slice(0, 15).map(function (check) {
      return [
        '<div class="source-row">',
        '<span>' + escapeHtml(check.status || check.severity || "check") + '</span>',
        '<strong>' + escapeHtml(check.title || check.name || check.id || "진단 항목") + '</strong>',
        '<em>' + escapeHtml(check.message || check.detail || check.description || "") + '</em>',
        '</div>'
      ].join("");
    }).join("") : '<p class="subtle">진단 결과가 아직 없습니다. 새로고침을 누르면 TypeDB 상태를 확인합니다.</p>',
    '</div>',
    '</div>'
  ].join("");
}

function renderTypeDBRuleboxPanel() {
  var payload = ontologyState.ontologyRulebox || {};
  var rules = latestChangedFirst(Array.isArray(payload.rules) ? payload.rules : []);
  var relationTypes = Array.isArray(payload.relationTypes) ? payload.relationTypes : [];
  var versions = latestChangedFirst(Array.isArray(payload.versions) ? payload.versions : []);
  var candidates = latestChangedFirst(Array.isArray(payload.changeCandidates) ? payload.changeCandidates : []);
  var lastRun = ontologyState.ontologyRuleboxLastRun || {};
  var accountOptions = ontologyAccountOptions();
  var disabled = ontologyState.ontologyRuleboxSaving || ontologyState.ontologyRuleboxRunning || ontologyState.ontologyRuleboxProposing || settingsState.serverSettingsLocked;
  return [
    '<article class="panel model-panel typedb-rulebox-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">TypeDB Native Rules</p>',
    '<h2>네이티브 규칙 실행 콘솔</h2>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(payload.status === "ok" ? "watch" : payload.configured ? "caution" : "hold") + '">' + escapeHtml(ruleboxStatusLabel(payload)) + '</span>',
    '</div>',
    '<div class="model-editor typedb-rulebox-editor">',
    accountOptions ? '<label class="setting-field"><span>추론 계정</span><select data-ontology-account-select>' + accountOptions + '</select></label>' : '',
    '<div class="lab-stats-grid model-stats-grid">',
    renderLabStat("규칙", payload.ruleCount || rules.length || 0, "개"),
    renderLabStat("조건", payload.conditionCount || countRuleboxConditions(rules), "개"),
    renderLabStat("파생 관계", payload.derivationCount || countRuleboxDerivations(rules), "개"),
    renderLabStat("버전", payload.versionCount || versions.length || 0, "개"),
    '</div>',
    '<div class="settings-note model-settings-note">',
    '<strong>TypeDB를 네이티브 규칙 원본으로 사용합니다.</strong>',
    '<p>저장 시 관계 규칙 구조를 TypeDB에 적재하고 버전 해시를 남깁니다. 실행 버튼은 TypeDB ABox와 네이티브 규칙을 읽어 새 InferenceBox 세대를 먼저 만든 뒤 최신 세대로 전환합니다.</p>',
    '</div>',
    '<div class="rulebox-console-strip">',
    '<span><strong>source</strong>' + escapeHtml(payload.source || "-") + '</span>',
    '<span><strong>engine</strong>' + escapeHtml(payload.engineVersion || "-") + '</span>',
    '<span><strong>relations</strong>' + escapeHtml(relationTypes.length ? relationTypes.join(", ") : "-") + '</span>',
    lastRun.status ? '<span><strong>last run</strong>' + escapeHtml(lastRun.status + (lastRun.reason ? " · " + lastRun.reason : "")) + '</span>' : '',
    '</div>',
    ontologyState.ontologyRuleboxLoading ? '<p class="lab-message">TypeDB 네이티브 규칙을 읽는 중입니다.</p>' : '',
    ontologyState.ontologyRuleboxError ? '<p class="form-error">' + escapeHtml(ontologyState.ontologyRuleboxError) + '</p>' : '',
    payload.reason ? '<p class="lab-message caution">' + escapeHtml(payload.reason) + '</p>' : '',
    '<label class="setting-field wide"><span>변경 이유</span><input data-ontology-rulebox-change-reason type="text" autocomplete="off" placeholder="예: 피어 뉴스 후보 검토, 판단 단계 정책 보강" value="' + escapeHtml(ontologyState.ontologyRuleboxChangeReason || "") + '"></label>',
    '<div class="settings-actions rulebox-actions">',
    '<button class="text-button" type="button" data-action="refresh-rulebox"' + (ontologyState.ontologyRuleboxLoading ? ' disabled' : '') + '>새로고침</button>',
    '<button class="text-button" type="button" data-action="refresh-ontology-diagnostics"' + (ontologyState.ontologyDiagnosticsLoading ? ' disabled' : '') + '>TypeDB 진단</button>',
    '<button class="text-button" type="button" data-action="seed-ontology-graph"' + (ontologyState.ontologySeedRunning || settingsState.serverSettingsLocked ? ' disabled' : '') + '>' + escapeHtml(ontologyState.ontologySeedRunning ? "시드 중" : "온톨로지 시드") + '</button>',
    '<button class="text-button" type="button" data-action="seed-rulebox"' + (disabled ? ' disabled' : '') + '>기본값 시드</button>',
    '<button class="text-button primary" type="button" data-action="save-rulebox"' + (disabled ? ' disabled' : '') + '>' + escapeHtml(ontologyState.ontologyRuleboxSaving ? "저장 중" : "네이티브 규칙 저장") + '</button>',
    '<button class="text-button primary" type="button" data-action="run-rulebox"' + (disabled ? ' disabled' : '') + '>' + escapeHtml(ontologyState.ontologyRuleboxRunning ? "실행 중" : "TypeDB 추론 실행") + '</button>',
    '<button class="text-button primary" type="button" data-action="propose-rulebox-candidates"' + (disabled ? ' disabled' : '') + '>' + escapeHtml(ontologyState.ontologyRuleboxProposing ? "생성 중" : "AI 후보 생성") + '</button>',
    '</div>',
    renderTypeDBDiagnosticsPanel(),
    '<div class="settings-grid compact-settings-grid">',
    '<label class="setting-field"><span>AI 후보 사용</span><select data-model-setting="ontologyRuleCandidateAiEnabled"><option value="1"' + ((settingValue("ontologyRuleCandidateAiEnabled") || defaultSettings.ontologyRuleCandidateAiEnabled) !== "0" ? " selected" : "") + '>사용</option><option value="0"' + ((settingValue("ontologyRuleCandidateAiEnabled") || defaultSettings.ontologyRuleCandidateAiEnabled) === "0" ? " selected" : "") + '>끄기</option></select></label>',
    '<label class="setting-field"><span>Codex 사용</span><select data-model-setting="ontologyRuleCandidateAiUseCodex"><option value="1"' + ((settingValue("ontologyRuleCandidateAiUseCodex") || defaultSettings.ontologyRuleCandidateAiUseCodex) !== "0" ? " selected" : "") + '>사용</option><option value="0"' + ((settingValue("ontologyRuleCandidateAiUseCodex") || defaultSettings.ontologyRuleCandidateAiUseCodex) === "0" ? " selected" : "") + '>로컬</option></select></label>',
    '<label class="setting-field"><span>주기(분)</span><input data-model-setting="ontologyRuleCandidateAiIntervalMinutes" type="number" min="5" step="5" value="' + escapeHtml(settingValue("ontologyRuleCandidateAiIntervalMinutes") || defaultSettings.ontologyRuleCandidateAiIntervalMinutes) + '"></label>',
    '<label class="setting-field"><span>최대 후보</span><input data-model-setting="ontologyRuleCandidateAiMaxCandidates" type="number" min="1" max="10" step="1" value="' + escapeHtml(settingValue("ontologyRuleCandidateAiMaxCandidates") || defaultSettings.ontologyRuleCandidateAiMaxCandidates) + '"></label>',
    '<label class="setting-field"><span>자동 반영</span><select data-model-setting="ontologyLabAutoApplyEnabled"><option value="1"' + ((settingValue("ontologyLabAutoApplyEnabled") || defaultSettings.ontologyLabAutoApplyEnabled || "0") !== "0" ? " selected" : "") + '>사용</option><option value="0"' + ((settingValue("ontologyLabAutoApplyEnabled") || defaultSettings.ontologyLabAutoApplyEnabled || "0") === "0" ? " selected" : "") + '>끄기</option></select></label>',
    '<div class="setting-field"><span>자동 반영 조건</span><strong>검증 완료 · 자료 충분</strong></div>',
    '<label class="setting-field"><span>검토 자동승인</span><select data-model-setting="ontologyLabAutoApplyNeedsReviewEnabled"><option value="0"' + ((settingValue("ontologyLabAutoApplyNeedsReviewEnabled") || defaultSettings.ontologyLabAutoApplyNeedsReviewEnabled || "0") === "0" ? " selected" : "") + '>끄기</option><option value="1"' + ((settingValue("ontologyLabAutoApplyNeedsReviewEnabled") || defaultSettings.ontologyLabAutoApplyNeedsReviewEnabled || "0") !== "0" ? " selected" : "") + '>사용</option></select></label>',
    '<label class="setting-field"><span>성장 알림</span><select data-model-setting="ontologyLabNotifyEnabled"><option value="1"' + ((settingValue("ontologyLabNotifyEnabled") || defaultSettings.ontologyLabNotifyEnabled || "1") !== "0" ? " selected" : "") + '>사용</option><option value="0"' + ((settingValue("ontologyLabNotifyEnabled") || defaultSettings.ontologyLabNotifyEnabled || "1") === "0" ? " selected" : "") + '>끄기</option></select></label>',
    '</div>',
    '<div class="model-section">',
    '<div class="flow-title"><div><strong>최근 버전</strong><span>저장된 RuleBox 해시와 변경 이유입니다.</span></div></div>',
    '<div class="source-stack rulebox-version-list">',
    versions.length ? versions.map(renderTypeDBRuleboxVersionRow).join("") : '<p class="subtle">아직 기록된 RuleBox 버전이 없습니다.</p>',
    '</div>',
    '</div>',
    '<div class="model-section">',
    '<div class="flow-title"><div><strong>AI 관계 후보 검토</strong><span>후보는 JSON 초안에만 추가됩니다. enabled=false 상태로 검토 후 활성화하세요.</span></div></div>',
    '<div class="source-stack rulebox-candidate-list">',
    candidates.length ? candidates.map(function (candidate) { return renderTypeDBRuleboxCandidateRow(candidate, disabled); }).join("") : '<p class="subtle">검토할 관계 후보가 없습니다.</p>',
    '</div>',
    '</div>',
    '<div class="model-section">',
    '<div class="flow-title"><div><strong>TypeDB 저장 원본 JSON</strong><span>GraphInferenceRule 배열입니다. 조건과 derivation을 추가하면 다음 실행부터 관계 추론 대상이 됩니다.</span></div></div>',
    '<label class="setting-field wide"><textarea data-ontology-rulebox-json rows="18" autocomplete="off">' + escapeHtml(ontologyState.ontologyRuleboxJson || JSON.stringify(rules, null, 2)) + '</textarea></label>',
    '</div>',
    '<div class="model-section">',
    '<div class="flow-title"><div><strong>활성 규칙 요약</strong><span>TypeDB에 적재된 RuleBox 노드를 사람이 읽기 쉽게 펼친 목록입니다.</span></div></div>',
    '<div class="source-stack rulebox-rule-list">',
    rules.length ? rules.map(renderTypeDBRuleboxRuleRow).join("") : '<p class="subtle">TypeDB RuleBox 규칙이 비어 있습니다.</p>',
    '</div>',
    '</div>',
    '</div>',
    '</article>'
  ].join("");
}

function ruleboxStatusLabel(payload) {
  if (!payload || !payload.configured) return "TypeDB 미연결";
  if (payload.status === "ok") return "TypeDB 연결";
  return payload.status || "확인 필요";
}

function countRuleboxConditions(rules) {
  return (rules || []).reduce(function (count, rule) {
    return count + ((rule.conditions || []).length || 0);
  }, 0);
}

function countRuleboxDerivations(rules) {
  return (rules || []).reduce(function (count, rule) {
    return count + ((rule.derivations || []).length || 0);
  }, 0);
}

function renderTypeDBRuleboxRuleRow(rule) {
  var conditions = Array.isArray(rule.conditions) ? rule.conditions : [];
  var derivations = Array.isArray(rule.derivations) ? rule.derivations : [];
  var relationTypes = derivations.map(function (item) {
    return item.relation_type || item.relationType || "";
  }).filter(Boolean);
  var knowledge = rule.knowledge_basis || rule.knowledgeBasis || {};
  return [
    '<div class="source-row rulebox-rule-row">',
    '<span>' + escapeHtml(rule.enabled === false ? "disabled" : (rule.action_group || rule.actionGroup || "enabled")) + '</span>',
    '<strong>' + escapeHtml(rule.label || rule.rule_id || rule.ruleId || "Rule") + '</strong>',
    '<em>' + escapeHtml((rule.rule_id || rule.ruleId || "") + " · " + (knowledge.ruleKind || "역할 미지정") + " · " + (knowledge.theoryFamily || "이론 미지정") + " · " + (knowledge.validationStatus || "검증 미지정") + " · " + conditions.length + " conditions · " + (relationTypes.join(", ") || derivations.length + " derivations")) + '</em>',
    renderRecordChangedAt(rule),
    '</div>'
  ].join("");
}

function renderTypeDBRuleboxVersionRow(version) {
  return [
    '<div class="source-row rulebox-version-row">',
    '<span>' + escapeHtml(version.versionLabel || version.shortHash || "-") + '</span>',
    '<strong>' + escapeHtml(version.changeReason || "변경 이유 없음") + '</strong>',
    '<em>' + escapeHtml([(version.ruleCount || 0) + " rules", version.author || ""].filter(Boolean).join(" · ")) + '</em>',
    renderRecordChangedAt(version),
    '</div>'
  ].join("");
}

function renderTypeDBRuleboxCandidateRow(candidate, disabled) {
  var requiresData = Array.isArray(candidate.requiresData) ? candidate.requiresData : [];
  var proposed = candidate.proposedRule && typeof candidate.proposedRule === "object" ? candidate.proposedRule : null;
  var canAppend = proposed && candidate.status !== "covered";
  return [
    '<div class="source-row rulebox-candidate-row">',
    '<span>' + escapeHtml(candidate.status || "candidate") + '</span>',
    '<strong>' + escapeHtml(candidate.title || candidate.id || "관계 후보") + '</strong>',
    '<em>' + escapeHtml(candidate.rationale || "") + (requiresData.length ? '<br><small>' + escapeHtml("필요 데이터: " + requiresData.join(", ")) + '</small>' : '') + '</em>',
    renderRecordChangedAt(candidate),
    '<button class="text-button" type="button" data-action="append-rulebox-candidate" data-candidate-id="' + escapeHtml(candidate.id || "") + '"' + (!canAppend || disabled ? ' disabled' : '') + '>' + escapeHtml(candidate.status === "covered" ? "반영됨" : proposed ? "JSON에 추가" : "데이터 필요") + '</button>',
    '</div>'
  ].join("");
}

function renderAiPromptRegistryPanel(snapshot) {
  var prompts = promptTemplateRows();
  var release = notificationsState.notificationAiPromptRelease || {};
  var releaseInstructions = Array.isArray(release.instructions) ? release.instructions : [];
  var releaseSchema = release.responseSchema && typeof release.responseSchema === "object" ? release.responseSchema : {};
  return [
    '<article class="panel model-panel prompt-registry-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Prompt Registry</p>',
    '<h2>AI 분석 프롬프트 관리</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(prompts.length) + '</span>',
    '</div>',
    '<div class="settings-body">',
    '<div class="settings-note">',
    '<strong>최종 투자 판단은 활성 Prompt Release로 실행됩니다.</strong>',
    '<p>TypeDB가 연결한 판단 근거만 Decision Core로 선별하고, 아래 운영 정책과 활성 릴리스를 조합한 실제 프롬프트를 AI에 전달합니다. 전체 원본은 알림 추적에 별도로 보존됩니다.</p>',
    '</div>',
    '<div class="model-section">',
    '<div class="flow-title"><div><strong>활성 최종 판단 릴리스</strong><span>실제 investmentInsight AI 요청에 사용되는 버전과 출력 계약입니다.</span></div></div>',
    '<div class="rulebox-console-strip">',
    '<span><strong>version</strong>' + escapeHtml(release.version || "확인 필요") + '</span>',
    '<span><strong>contract</strong>' + escapeHtml(release.contractVersion || "-") + '</span>',
    '<span><strong>fingerprint</strong>' + escapeHtml(String(release.fingerprint || "-").slice(0, 16)) + '</span>',
    '<span><strong>status</strong>' + escapeHtml(release.status || "unknown") + '</span>',
    '</div>',
    '<label class="setting-field wide"><span>실제 최종 판단 기본 지시문</span><textarea rows="10" readonly>' + escapeHtml(releaseInstructions.join("\n")) + '</textarea></label>',
    '<label class="setting-field wide"><span>실제 구조화 응답 계약</span><textarea rows="10" readonly>' + escapeHtml(JSON.stringify(releaseSchema, null, 2)) + '</textarea></label>',
    '</div>',
    '<div class="model-section">',
    '<div class="flow-title"><div><strong>실시간 알림 AI 검증</strong><span>알림 워커가 AI 답변을 기다린 뒤 검증된 실행 메시지만 보낼지 정합니다.</span></div></div>',
    '<div class="settings-grid compact-settings-grid">',
    '<label class="setting-field"><span>AI 투자판단</span><select data-model-setting="notificationAiGateEnabled"><option value="1"' + ((settingValue("notificationAiGateEnabled") || defaultSettings.notificationAiGateEnabled) !== "0" ? " selected" : "") + '>사용</option><option value="0"' + ((settingValue("notificationAiGateEnabled") || defaultSettings.notificationAiGateEnabled) === "0" ? " selected" : "") + '>끄기</option></select></label>',
    '<label class="setting-field"><span>병렬 AI 워커</span><select data-model-setting="notificationAiQueueWorkerCount"><option value="0"' + ((settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount) === "0" ? " selected" : "") + '>중지</option><option value="1"' + ((settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount) === "1" ? " selected" : "") + '>1개</option><option value="2"' + ((settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount) === "2" ? " selected" : "") + '>2개 (권장)</option><option value="3"' + ((settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount) === "3" ? " selected" : "") + '>3개</option><option value="4"' + ((settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount) === "4" ? " selected" : "") + '>4개</option></select></label>',
    '<label class="setting-field"><span>Codex 사용</span><select data-model-setting="notificationAiUseCodex"><option value="1"' + ((settingValue("notificationAiUseCodex") || defaultSettings.notificationAiUseCodex) !== "0" ? " selected" : "") + '>사용</option><option value="0"' + ((settingValue("notificationAiUseCodex") || defaultSettings.notificationAiUseCodex) === "0" ? " selected" : "") + '>로컬 검증만</option></select></label>',
    '<label class="setting-field"><span>AI 완료 정책</span><select data-model-setting="notificationAiTimeoutSeconds"><option value="0"' + ((settingValue("notificationAiTimeoutSeconds") || defaultSettings.notificationAiTimeoutSeconds) === "0" ? " selected" : "") + '>완료까지 대기</option><option value="300"' + ((settingValue("notificationAiTimeoutSeconds") || defaultSettings.notificationAiTimeoutSeconds) === "300" ? " selected" : "") + '>비상 제한 300초</option><option value="600"' + ((settingValue("notificationAiTimeoutSeconds") || defaultSettings.notificationAiTimeoutSeconds) === "600" ? " selected" : "") + '>비상 제한 600초</option></select></label>',
    '</div>',
    '<label class="setting-field wide"><span>적용 알림 타입</span><textarea data-model-setting="notificationAiGateMessageTypes" rows="3" autocomplete="off">' + escapeHtml(settingValue("notificationAiGateMessageTypes") || defaultSettings.notificationAiGateMessageTypes) + '</textarea></label>',
    '</div>',
    '<div class="model-section">',
    '<div class="flow-title"><div><strong>최종 판단 운영 정책</strong><span>저장하면 활성 릴리스 지문에 반영되고 다음 AI 요청부터 적용됩니다.</span></div></div>',
    '<label class="setting-field wide"><textarea data-model-setting="aiPromptPolicy" rows="6" autocomplete="off">' + escapeHtml(settingValue("aiPromptPolicy") || defaultSettings.aiPromptPolicy) + '</textarea></label>',
    '</div>',
    '<div class="model-section">',
    '<div class="flow-title"><div><strong>보조·호환 프롬프트 템플릿</strong><span>운영·뉴스·레거시 promptContext용이며 최종 investmentInsight 판단 릴리스와 구분됩니다.</span></div></div>',
    '<label class="setting-field wide"><textarea data-model-setting="aiPromptTemplates" rows="14" autocomplete="off">' + escapeHtml(settingValue("aiPromptTemplates") || defaultSettings.aiPromptTemplates) + '</textarea></label>',
    '</div>',
    '<div class="source-stack prompt-registry-list">',
    prompts.map(function (prompt) {
      return [
        '<div class="source-row prompt-registry-row">',
        '<span>' + escapeHtml(prompt.id) + '</span>',
        '<strong>' + escapeHtml(prompt.label || prompt.id) + '</strong>',
        '<em>' + escapeHtml([prompt.version, prompt.purpose].filter(Boolean).join(" · ")) + '</em>',
        renderRecordChangedAt(prompt),
        '</div>'
      ].join("");
    }).join("") || '<p class="subtle">등록된 프롬프트가 없습니다.</p>',
    '</div>',
    '</div>',
    '</article>'
  ].join("");
}

export { promptTemplateRows, renderAiPromptRegistryPanel, renderTypeDBRuleboxPanel };
