import { stockDisplayName, textWithKnownDisplaySymbols } from "../instruments/catalog.mjs";
import { renderInstrumentWorkspaceLink } from "../instruments/workspace.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { notificationJobKey, notificationJobResolvedSymbol, renderNotificationDecisionDetail, renderNotificationDecisionRow } from "./detail.mjs";
import { notificationTemplateLabel } from "./editor.mjs";
import { notificationActionFlowActionLabel, notificationJobDetailPayload } from "./reasoning.mjs";
import { formatClock, latestChangedFirst } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { labelWithNotificationIcon } from "../shell/catalog.mjs";
import { cardFormatAttrs, cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { shellState } from "../state/shell.mjs";

function notificationJobStatusLabel(status) {
  var labels = {
    pending: "대기",
    awaiting_ai: "AI 판단 대기",
    processing: "처리 중",
    done: "발송",
    failed: "실패",
    superseded: "최신 판단으로 대체",
    suppressed: "보류"
  };
  return labels[status] || status || "-";
}

function notificationJobToneClass(status) {
  if (status === "done" || status === "pending" || status === "awaiting_ai" || status === "processing") return "watch";
  if (status === "suppressed" || status === "superseded") return "muted";
  if (status === "failed") return "danger";
  return "muted";
}

function notificationDeliveryStateLabel(value) {
  var labels = {
    send: "발송 가능",
    suppressed: "발송 보류",
    bypass: "필터 우회"
  };
  return labels[String(value || "")] || "확인 중";
}

function notificationReviewLevelLabel(value) {
  var labels = {
    normal: "평소 관찰",
    observe: "변화 관찰",
    check: "조건 확인",
    act: "대응 준비",
    immediate: "즉시 재확인",
    blocked: "판단 보류"
  };
  return labels[String(value || "")] || "확인 단계 없음";
}

function notificationChangeStateLabel(value) {
  var labels = {
    unchanged: "이전과 같은 상태",
    "new-condition": "새 조건 성립",
    improving: "이전보다 개선",
    worsening: "이전보다 악화",
    "direction-changed": "판단 방향 변경",
    "new-evidence": "새 뉴스·공시·근거"
  };
  return labels[String(value || "")] || "변화 정보 없음";
}

function notificationJobDecisionRoute(job) {
  var decision = String((job && job.deliveryDecision) || "");
  var gateState = String((job && job.deliveryGateState) || "");
  var inferenceTransition = job && job.investmentNotificationTransition && typeof job.investmentNotificationTransition === "object"
    ? job.investmentNotificationTransition
    : {};
  var previousState = inferenceTransition.previousState && typeof inferenceTransition.previousState === "object" ? inferenceTransition.previousState : {};
  var currentState = inferenceTransition.currentState && typeof inferenceTransition.currentState === "object" ? inferenceTransition.currentState : {};
  if (inferenceTransition.changed) {
    return {
      label: "추론 상태 변경",
      relation: previousState.label || "이전 판단",
      change: currentState.label || "현재 판단",
      tone: "watch"
    };
  }
  var tone = decision === "suppressed" || gateState === "blocked" ? "danger" : (decision === "send" ? "watch" : "hold");
  return {
    label: notificationDeliveryStateLabel(decision),
    relation: notificationReviewLevelLabel(job && job.deliveryReviewLevel),
    change: notificationChangeStateLabel(job && job.deliveryChangeState),
    tone: tone
  };
}

function notificationDecisionFactorTone(text) {
  var value = String(text || "");
  if (/닫힘|보류|반복|실패|억제|낮아|부족|금지|차단/.test(value)) return "danger";
  if (/상승|증가|충족|예외|통과|확인|회복|새 /.test(value)) return "watch";
  return "hold";
}

function notificationJobDecisionFactors(job) {
  if (!job) return [];
  var rows = [];
  var explanation = job.customerDeliveryExplanation && typeof job.customerDeliveryExplanation === "object"
    ? job.customerDeliveryExplanation
    : {};
  var explanationValidation = explanation.validation && typeof explanation.validation === "object"
    ? explanation.validation
    : {};
  var primaryCause = explanation.primaryCause && typeof explanation.primaryCause === "object"
    ? explanation.primaryCause
    : {};
  if (explanationValidation.state === "valid" && primaryCause.summary) {
    rows.push({ label: String(primaryCause.summary), tone: "watch" });
  } else if (explanationValidation.state === "invalid") {
    rows.push({ label: "사용자 발송 사유 계약 오류", tone: "danger" });
  }
  var inferenceTransition = job.investmentNotificationTransition && typeof job.investmentNotificationTransition === "object"
    ? job.investmentNotificationTransition
    : {};
  if (inferenceTransition.changed && inferenceTransition.summary) {
    rows.push({ label: "추론 상태 변경: " + String(inferenceTransition.summary), tone: "watch" });
  }
  var freshRecheck = job.freshDataRecheck && typeof job.freshDataRecheck === "object" ? job.freshDataRecheck : {};
  if (freshRecheck.requested) {
    rows.push({ label: "오래된 판단 대신 최신 데이터 재수집을 예약했습니다.", tone: "watch" });
  }
  if (!primaryCause.summary && job.deliveryGateReason) {
    rows.push({ label: String(job.deliveryGateReason), tone: notificationDecisionFactorTone(job.deliveryGateReason) });
  }
  if (!primaryCause.summary) {
    (Array.isArray(job.deliveryReasons) ? job.deliveryReasons : []).slice(0, 5).forEach(function (reason) {
      rows.push({ label: String(reason || ""), tone: notificationDecisionFactorTone(reason) });
    });
  }
  if (Number(job.repeatRecentCount || 0) > 0) {
    rows.push({
      label: "같은 내용 " + String(job.repeatWindowMinutes || 0) + "분 내 " + String(job.repeatRecentCount || 0) + "회 확인",
      tone: job.repeatBypassed ? "watch" : "danger"
    });
  }
  if (job.marketHoursReason) rows.push({ label: job.marketHoursReason, tone: job.marketHoursDecision === "suppressed" ? "danger" : "hold" });
  if (job.quietHoursReason) rows.push({ label: job.quietHoursReason, tone: "danger" });
  if (job.repeatBypassed) rows.push({ label: job.repeatBypassReason || "새 변화로 반복 보류 해제", tone: "watch" });
  return rows.slice(0, 7);
}

function renderNotificationDecisionRoute(job) {
  var route = notificationJobDecisionRoute(job);
  return [
    '<div class="notification-delivery-route ' + escapeHtml(route.tone || "hold") + '">',
    '<span>발송 판단</span>',
    '<strong>' + escapeHtml(route.label) + '</strong>',
    '<em>' + escapeHtml(route.relation + " · " + route.change) + '</em>',
    '</div>'
  ].join("");
}

function renderNotificationDecisionFactors(job, limit) {
  var factors = notificationJobDecisionFactors(job);
  if (Number.isFinite(Number(limit))) factors = factors.slice(0, Number(limit));
  if (!factors.length) return "";
  return [
    '<div class="notification-delivery-factors">',
    factors.map(function (factor) {
      return '<span class="' + escapeHtml(factor.tone || "hold") + '">' + escapeHtml(textWithKnownDisplaySymbols(factor.label, notificationJobResolvedSymbol(job), job)) + '</span>';
    }).join(""),
    '</div>'
  ].join("");
}

function renderNotificationTriggerLedger(job) {
  var allRows = Array.isArray(job && job.deliveryTriggerLedger) ? job.deliveryTriggerLedger : [];
  var explanation = job && job.customerDeliveryExplanation && typeof job.customerDeliveryExplanation === "object"
    ? job.customerDeliveryExplanation
    : {};
  var validation = explanation.validation && typeof explanation.validation === "object" ? explanation.validation : {};
  var primaryCause = explanation.primaryCause && typeof explanation.primaryCause === "object" ? explanation.primaryCause : {};
  var supportingCauses = Array.isArray(explanation.supportingCauses) ? explanation.supportingCauses : [];
  var customerRows = Array.isArray(job && job.customerDeliveryTriggers)
    ? job.customerDeliveryTriggers
    : allRows.filter(function (item) { return item && item.customerVisible === true; });
  var internalRows = Array.isArray(job && job.internalDeliveryChecks)
    ? job.internalDeliveryChecks
    : allRows.filter(function (item) { return !item || item.customerVisible !== true; });
  function renderRows(rows) {
    return rows.map(function (item) {
      var label = String(item.label || item.kind || "발송 조건");
      var reason = String(item.reason || "기록된 설명 없음");
      var values = [
        item.previousValue !== undefined && item.previousValue !== "" ? "이전 " + String(item.previousValue) : "",
        item.currentValue !== undefined && item.currentValue !== "" ? "현재 " + (Array.isArray(item.currentValue) ? item.currentValue.join(", ") : String(item.currentValue)) : "",
        item.threshold !== undefined && item.threshold !== "" ? "기준 " + String(item.threshold) : "",
        item.sourceTitle ? "원문 " + String(item.sourceTitle) : "",
        item.sourceProvider ? "출처 " + String(item.sourceProvider) : "",
        Array.isArray(item.sourceReferences) && item.sourceReferences.length ? "참조 " + item.sourceReferences.join(", ") : "",
        Array.isArray(item.ruleIds) && item.ruleIds.length ? "규칙 " + item.ruleIds.join(", ") : "",
        Array.isArray(item.evidenceIds) && item.evidenceIds.length ? "근거 " + item.evidenceIds.join(", ") : ""
      ].filter(Boolean).join(" · ");
      return '<p><b>' + escapeHtml(label) + '</b> ' + escapeHtml(reason) + (values ? '<br><span>' + escapeHtml(values) + '</span>' : '') + '</p>';
    }).join("");
  }
  var sections = [];
  if (primaryCause.summary) {
    sections.push(
      '<section class="notification-detail-section"><strong>사용자에게 표시된 발송 사유</strong>' +
      '<div class="notification-detail-reasons">' + renderRows([primaryCause].concat(supportingCauses)) + '</div>' +
      '<p class="notification-detail-note">계약 ' + escapeHtml(explanation.version || "-") +
      ' · 검증 ' + escapeHtml(validation.state || "확인 필요") + '</p></section>'
    );
  } else if (validation.state === "invalid") {
    sections.push(
      '<section class="notification-detail-section"><strong>사용자 발송 사유 계약 오류</strong>' +
      '<div class="notification-detail-reasons"><p><b>검증 실패</b> ' +
      escapeHtml((Array.isArray(validation.errors) ? validation.errors : []).join(", ") || "원인 기록 없음") +
      '</p></div></section>'
    );
  }
  if (customerRows.length) {
    sections.push('<details class="notification-detail-section"><summary><strong>발송 계기 원장</strong></summary><div class="notification-detail-reasons">' + renderRows(customerRows) + '</div></details>');
  }
  if (internalRows.length) {
    sections.push('<details class="notification-detail-section"><summary><strong>내부 발송 검사</strong></summary><div class="notification-detail-reasons">' + renderRows(internalRows) + '</div></details>');
  }
  return sections.join("");
}

function notificationJobSimilarityText(job) {
  var count = Number(job.repeatRecentCount || 0);
  var windowMinutes = Number(job.repeatWindowMinutes || 0);
  if (!count) return "같은 내용 없음";
  if (job.repeatBypassed) return windowMinutes + "분 내 " + count + "회 · 새 변화로 재발송";
  return windowMinutes + "분 내 " + count + "회 · 반복 보류";
}

function notificationJobMarketHoursText(job) {
  if (!job.marketHoursEnabled) return "";
  if (job.marketHoursReason) return job.marketHoursReason;
  if (job.marketHoursStatus === "open") return "장 시간 열림";
  if (job.marketHoursStatus === "closed") return "장 시간 외";
  return "";
}

function notificationJobQuietHoursText(job) {
  if (!job.quietHoursSuppressed) return "";
  return job.quietHoursReason || "계정 알림 금지 시간";
}

function notificationJobStateCooldownText(job) {
  if (!job.cooldownEnabled && !job.cooldownReason) return "";
  if (job.cooldownReason) return job.cooldownReason;
  if (job.cooldownDecision === "new-condition") return "처음 확인된 상태";
  if (job.cooldownDecision === "meaningful-change") return "의미 있는 변화";
  if (job.cooldownDecision === "scheduled-summary") return "지속 상태 요약";
  if (job.cooldownDecision === "cooldown") return "같은 상태 지속";
  return "";
}

function notificationJobStatusKey(job) {
  return String((job && job.status) || "unknown");
}

function notificationJobTypeKey(job) {
  return String((job && (job.notificationKind || job.messageType)) || "notification");
}

function notificationJobFilterOptions(jobs, keyFn) {
  var options = ["all"];
  var seen = { all: true };
  (Array.isArray(jobs) ? jobs : []).forEach(function (job) {
    var key = keyFn(job);
    if (!key || seen[key]) return;
    seen[key] = true;
    options.push(key);
  });
  return options;
}

function notificationJobTypeLabel(type, jobs) {
  if (type === "all") return "전체 타입";
  var found = (Array.isArray(jobs) ? jobs : []).filter(function (job) {
    return notificationJobTypeKey(job) === type;
  })[0];
  if (found && found.notificationKind) {
    return [found.notificationKindIcon, found.notificationKindLabel].filter(Boolean).join(" ");
  }
  return labelWithNotificationIcon(type, (found && found.messageTypeLabel) || notificationTemplateLabel(type));
}

function notificationJobSearchText(job) {
  job = job || {};
  var resolvedSymbol = notificationJobResolvedSymbol(job);
  var displaySymbol = resolvedSymbol ? stockDisplayName(resolvedSymbol, job) : "";
  var reasons = Array.isArray(job.deliveryReasons) ? job.deliveryReasons : [];
  var decisionFactors = notificationJobDecisionFactors(job).map(function (factor) {
    return factor.label;
  });
  return [
    notificationJobStatusLabel(job.status),
    notificationJobStatusKey(job),
    notificationJobTypeLabel(notificationJobTypeKey(job), [job]),
    job.messageType,
    job.messageTypeLabel,
    job.accountId,
    job.accountLabel,
    job.sourceEventName,
    job.title,
    job.symbol,
    job.rawSymbol,
    resolvedSymbol,
    displaySymbol,
    job.textPreview,
    job.lastError,
    job.suppressionSummary,
    notificationJobSimilarityText(job),
    notificationJobStateCooldownText(job),
    notificationJobMarketHoursText(job),
    notificationJobQuietHoursText(job),
    job.deliveryFingerprint,
    reasons.join(" "),
    decisionFactors.join(" "),
    formatClock(job.createdAt)
  ].filter(Boolean).join(" ").toLowerCase();
}

function filteredNotificationJobs(jobs) {
  jobs = Array.isArray(jobs) ? jobs : [];
  var query = String(notificationsState.notificationJobSearch || "").trim().toLowerCase();
  var status = String(notificationsState.notificationJobStatusFilter || "all");
  var type = String(notificationsState.notificationJobTypeFilter || "all");
  return latestChangedFirst(jobs.filter(function (job) {
    if (status !== "all" && notificationJobStatusKey(job) !== status) return false;
    if (type !== "all" && notificationJobTypeKey(job) !== type) return false;
    if (query && notificationJobSearchText(job).indexOf(query) < 0) return false;
    return true;
  }));
}

function renderNotificationJobFilterOptions(options, currentValue, labelFn) {
  return options.map(function (option) {
    return '<option value="' + escapeHtml(option) + '"' + (option === currentValue ? " selected" : "") + '>' + escapeHtml(labelFn(option)) + '</option>';
  }).join("");
}

function renderNotificationJobFilterToolbar(jobs, filteredJobs) {
  var query = String(notificationsState.notificationJobSearch || "");
  var status = String(notificationsState.notificationJobStatusFilter || "all");
  var type = String(notificationsState.notificationJobTypeFilter || "all");
  var inbox = String(notificationsState.notificationInboxFilter || "all");
  var hasFilter = Boolean(query.trim() || status !== "all" || type !== "all" || inbox !== "all");
  return [
    '<form class="notification-search-panel"' + cardFormatAttrs("control-strip", "compact") + ' data-notification-search-form>',
    '<label class="notification-search-field primary">',
    '<span>검색</span>',
    '<input data-notification-job-search type="search" value="' + escapeHtml(query) + '" placeholder="종목, 알림 타입, 상태, 본문 검색" autocomplete="off" />',
    '</label>',
    '<label class="notification-search-field">',
    '<span>알림함</span>',
    '<select data-notification-job-filter="inbox"><option value="all"' + (inbox === "all" ? " selected" : "") + '>전체 알림</option><option value="unread"' + (inbox === "unread" ? " selected" : "") + '>읽지 않음</option><option value="important"' + (inbox === "important" ? " selected" : "") + '>중요</option><option value="action"' + (inbox === "action" ? " selected" : "") + '>확인 필요</option></select>',
    '</label>',
    '<label class="notification-search-field">',
    '<span>상태</span>',
    '<select data-notification-job-filter="status">',
    renderNotificationJobFilterOptions(notificationJobFilterOptions(jobs, notificationJobStatusKey), status, function (option) {
      return option === "all" ? "전체 상태" : notificationJobStatusLabel(option);
    }),
    '</select>',
    '</label>',
    '<label class="notification-search-field">',
    '<span>현재 목록 종류</span>',
    '<select data-notification-job-filter="messageType">',
    renderNotificationJobFilterOptions(notificationJobFilterOptions(jobs, notificationJobTypeKey), type, function (option) {
      return notificationJobTypeLabel(option, jobs);
    }),
    '</select>',
    '</label>',
    '<div class="notification-search-actions">',
    '<span class="notification-filter-count"><strong>' + escapeHtml(filteredJobs.length) + '</strong><em>/ 전체 ' + escapeHtml(jobs.length) + '건</em></span>',
    '<button class="mini-button primary" type="submit">검색</button>',
    '<button class="mini-button" type="button" data-action="reset-notification-job-filters"' + (hasFilter ? "" : " disabled") + '>초기화</button>',
    '</div>',
    '</form>'
  ].join("");
}

function renderNotificationDecisionPanel() {
  var jobs = notificationsState.notificationJobItems || [];
  var filteredJobs = filteredNotificationJobs(jobs);
  var summary = notificationsState.notificationJobsSummary || shellState.realtime.notificationJobs || {};
  var diagnostics = notificationsState.notificationJobDiagnostics || {};
  var hasError = Boolean(notificationsState.notificationJobsError);
  var summaryItems = ["pending", "awaiting_ai", "done", "superseded", "suppressed", "failed"].map(function (key) {
    return '<span class="chip">' + escapeHtml(notificationJobStatusLabel(key)) + ' ' + escapeHtml(Number(summary[key] || 0)) + '</span>';
  }).join("");
  var activeJob = activeNotificationDecisionJob(filteredJobs);
  var stateMessage = hasError
    ? renderNotificationStateMessage("hold", "최근 판단 API 연결 확인", notificationsState.notificationJobsError)
    : renderNotificationDecisionEmptyConsole();
  var detailHtml = filteredJobs.length ? renderNotificationDecisionDetail(activeJob, { compact: true }) : renderEmptyState({
    tone: "muted",
    label: "Detail",
    title: "상세 보기 대상이 없습니다",
    description: "검색어나 필터를 조정하면 선택한 알림의 판단 근거와 발송 조건을 여기서 확인할 수 있습니다.",
    meta: ["검색 결과", "상세 보기"]
  });
  return [
    '<article class="panel notification-decision-panel"' + cardTypeAttrs("process-card", jobs.length ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Decisions</p>',
    '<h2>최근 알림 판단</h2>',
    '</div>',
    '<div class="notification-decision-summary">',
    summaryItems,
    '</div>',
    '</div>',
    renderNotificationDecisionDiagnostics(diagnostics),
    '<div class="notification-decision-body notification-structured-body' + (jobs.length ? " has-detail" : "") + '">',
    '<div class="notification-decision-master">',
    jobs.length ? renderNotificationJobFilterToolbar(jobs, filteredJobs) : '',
    '<div class="notification-decision-status">',
    '<span class="tone-chip ' + escapeHtml(hasError ? "hold" : "watch") + '">' + escapeHtml(hasError ? "확인 필요" : "현황") + '</span>',
    '<span>' + escapeHtml(jobs.length ? "전체 " + jobs.length + "건 · 검색 결과 " + filteredJobs.length + "건 · 선택 행을 상세 보기로 확인" : (hasError ? "연결 상태 확인" : "판단 이력 없음")) + '</span>',
    '<em>' + escapeHtml(notificationsState.notificationJobsLoading ? "백그라운드 갱신 중" : "마지막 결과 유지") + '</em>',
    '</div>',
    jobs.length ? '<div class="notification-workbench"><section class="notification-list-pane" aria-label="알림 전체 리스트"><div class="notification-list-head"><div><p class="label">All Notifications</p><h3>전체 리스트</h3><span>발송·보류·실패 판단을 시간순으로 모두 확인합니다.</span></div><strong>' + escapeHtml(filteredJobs.length) + '건</strong></div>' + (filteredJobs.length ? '<div class="notification-decision-list" role="listbox" aria-label="최근 알림 판단 목록">' + filteredJobs.map(function (job) {
      return renderNotificationDecisionRow(job, notificationJobKey(job) === notificationJobKey(activeJob));
    }).join("") + '</div>' : renderEmptyState({
      tone: "muted",
      label: "Search",
      title: "검색 결과가 없습니다",
      description: "종목명, 알림 타입, 발송 상태, 본문 문구를 바꿔 다시 검색하세요.",
      meta: ["전체 리스트", "검색"]
    })) + '</section><section class="notification-detail-pane" aria-label="알림 상세 보기"><div class="notification-detail-pane-head"><div><p class="label">Selected Report</p><h3>상세 보기</h3><span>선택한 알림의 발송 판단, 보류 조건, 상세 리포트를 확인합니다.</span></div></div>' + detailHtml + '</section></div>' : stateMessage,
    '</div>',
    '</div>',
    '</article>'
  ].join("");
}

function renderNotificationDecisionEmptyConsole() {
  return [
    '<div class="notification-empty-console">',
    '<section' + cardTypeAttrs("signal-card", "hold") + '>',
    '<span>01 후보</span>',
    '<strong>후보 신호 확인</strong>',
    '<p>관심종목의 가격·뉴스·모델 신호가 알림 후보로 올라오는지 먼저 봅니다.</p>',
    '<button class="text-button primary" type="button" data-notification-section="candidates">후보 신호 보기</button>',
    '</section>',
    '<section' + cardTypeAttrs("decision-row", "hold") + '>',
    '<span>02 판단</span>',
    '<strong>발송·보류 이력 대기</strong>',
    '<p>워커가 상태 변화와 발송 사유를 남기면 이 영역에 시간순 판단 행으로 표시됩니다.</p>',
    '</section>',
    '<section' + cardTypeAttrs("config-panel", "hold") + '>',
    '<span>03 설정</span>',
    '<strong>정책 기준 점검</strong>',
    '<p>후보가 없거나 보류가 많으면 정책, 템플릿, 진단 섹션에서 기준을 확인합니다.</p>',
    '<button class="text-button" type="button" data-notification-section="diagnostics">진단 보기</button>',
    '</section>',
    '</div>'
  ].join("");
}

function renderNotificationDecisionDiagnostics(diagnostics) {
  diagnostics = diagnostics || {};
  var reasons = Array.isArray(diagnostics.suppressionReasons) ? diagnostics.suppressionReasons : [];
  var chips = [];
  var staleCount = Number(diagnostics.staleProcessingCount || 0);
  if (staleCount) {
    chips.push("처리 재시도 가능 " + staleCount + "건");
  }
  reasons.slice(0, 3).forEach(function (item) {
    chips.push(String(item.reason || "보류") + " " + Number(item.count || 0) + "건");
  });
  if (!chips.length) return "";
  return '<div class="notification-decision-diagnostics">' + chips.map(function (chip) {
    return '<span>' + escapeHtml(chip) + '</span>';
  }).join("") + '</div>';
}

function renderNotificationStateMessage(tone, title, description) {
  return [
    '<div class="notification-state-message ' + escapeHtml(tone || "muted") + '">',
    '<strong>' + escapeHtml(title) + '</strong>',
    '<span>' + escapeHtml(description || "") + '</span>',
    '</div>'
  ].join("");
}

function activeNotificationDecisionJob(jobs) {
  jobs = Array.isArray(jobs) ? jobs : [];
  if (!jobs.length) return null;
  var selectedKey = notificationsState.activeNotificationJobKey || "";
  var selected = jobs.filter(function (job) {
    return notificationJobKey(job) === selectedKey;
  })[0];
  return selected || jobs[0];
}

function notificationJobByKey(key) {
  var jobs = notificationsState.notificationJobItems || [];
  return jobs.filter(function (job) {
    return notificationJobKey(job) === key;
  })[0] || null;
}

function notificationWorkDetailPayload(key) {
  var job = notificationsState.notificationJobDetails[key] || notificationJobByKey(key) || (!key ? activeNotificationDecisionJob(notificationsState.notificationJobItems || []) : null);
  if (!job) return null;
  var payload = notificationJobDetailPayload(job);
  return {
    kicker: "Change Notification",
    title: payload.title || payload.displaySymbol || job.messageTypeLabel || job.messageType || "변화 알림",
    meta: [payload.displaySymbol, notificationJobTypeLabel(notificationJobTypeKey(job), [job]), formatClock(job.createdAt)].filter(Boolean).join(" · "),
    body: renderInstrumentWorkspaceLink(payload.resolvedSymbol, "종목 전체 흐름")
      + renderNotificationInvestmentFlowTransition(payload.investmentFlow)
      + (payload.decisionEpisodeId ? '<div class="oa-linked-detail-action">' + renderWorkDetailButton("investment-case", payload.decisionEpisodeId, "연결된 투자 판단", "text-button primary") + '</div>' : '')
      + renderNotificationDecisionDetail(job)
  };
}

function renderNotificationInvestmentFlowTransition(flow) {
  flow = flow && typeof flow === "object" ? flow : {};
  if (!flow.decisionChanged && !flow.validationChanged) return "";
  var rows = [];
  if (flow.decisionChanged) {
    rows.push('<span><em>투자 행동</em><strong>' + escapeHtml(notificationActionFlowActionLabel(flow.previousAction)) + ' → ' + escapeHtml(notificationActionFlowActionLabel(flow.currentAction)) + '</strong></span>');
  }
  if (flow.validationChanged) {
    rows.push('<span><em>근거 점검</em><strong>' + escapeHtml(flow.previousValidationState || "이전 상태 없음") + ' → ' + escapeHtml(flow.currentValidationState || "확인 필요") + '</strong></span>');
  }
  return '<section class="oa-notification-flow-change"><header><span>STATE CHANGE</span><strong>투자 의견 또는 근거 상태가 바뀌었습니다</strong></header><div>' + rows.join("") + '</div></section>';
}

































export { activeNotificationDecisionJob, filteredNotificationJobs, notificationChangeStateLabel, notificationDeliveryStateLabel, notificationJobByKey, notificationJobDecisionFactors, notificationJobDecisionRoute, notificationJobMarketHoursText, notificationJobQuietHoursText, notificationJobSimilarityText, notificationJobStateCooldownText, notificationJobStatusLabel, notificationJobToneClass, notificationJobTypeKey, notificationJobTypeLabel, notificationReviewLevelLabel, notificationWorkDetailPayload, renderNotificationDecisionFactors, renderNotificationDecisionPanel, renderNotificationDecisionRoute, renderNotificationJobFilterToolbar, renderNotificationStateMessage, renderNotificationTriggerLedger };
