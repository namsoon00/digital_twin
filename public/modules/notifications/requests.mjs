import { mergeUniqueItems, mobileInfiniteScrollEnabled } from "../navigation/infinite-list.mjs";
import { notificationJobKey } from "./detail.mjs";
import { defaultMarketHoursSessions, defaultNotificationRuleConditionTypes, defaultNotificationRules, defaultNotificationTemplates } from "./policy.mjs";
import { notificationRecipientId } from "./recipient.mjs";
import { render } from "../render/scheduler.mjs";
import { requestJson } from "../requests/json.mjs";
import { createLatestRequestLane } from "../requests/latest.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { shellState } from "../state/shell.mjs";

function applyNotificationTemplates(payload) {
  notificationsState.notificationTemplates = Array.isArray(payload.templates) && payload.templates.length
    ? payload.templates
    : defaultNotificationTemplates();
  notificationsState.notificationTemplateVariables = Array.isArray(payload.variables) && payload.variables.length
    ? payload.variables
    : ["title", "statusHeadline", "titleHeadline", "telegramMessage", "readableMessage", "dataLines", "telegramDataLines", "triggerSummary", "triggerBlock", "criterionBlock", "criterionLines", "lines", "rawLines", "referenceDate", "eventGeneratedAt", "sentAt", "sentTime", "sentLine", "body", "messageType", "symbol", "rawSymbol", "symbolDisplayName", "severity", "metadata", "market", "changePercent", "change24h", "change7d", "price", "volume", "volume24h", "provider"];
  notificationsState.notificationTemplatesLoaded = true;
  notificationsState.notificationTemplatesLoading = false;
  notificationsState.notificationTemplatesError = "";
}

function loadNotificationTemplates() {
  notificationsState.notificationTemplatesLoading = true;
  notificationsState.notificationTemplatesError = "";
  if (isStaticPreviewHost()) {
    applyNotificationTemplates({ templates: defaultNotificationTemplates() });
    notificationsState.notificationTemplatesLoading = false;
    return Promise.resolve();
  }
  return requestJson("/api/notification-templates")
    .then(function (payload) {
      applyNotificationTemplates(payload);
    })
    .catch(function (error) {
      notificationsState.notificationTemplatesError = error.message || "알림 템플릿을 읽지 못했습니다.";
      notificationsState.notificationTemplates = defaultNotificationTemplates();
    })
    .finally(function () {
      notificationsState.notificationTemplatesLoading = false;
      if (shellState.snapshot) render();
    });
}

function applyNotificationRules(payload) {
  notificationsState.notificationRules = Array.isArray(payload.rules) && payload.rules.length
    ? payload.rules
    : defaultNotificationRules();
  notificationsState.notificationRuleConditionTypes = Array.isArray(payload.conditionTypes) && payload.conditionTypes.length
    ? payload.conditionTypes
    : defaultNotificationRuleConditionTypes();
  notificationsState.notificationMarketHoursSessions = Array.isArray(payload.marketHoursSessions) && payload.marketHoursSessions.length
    ? payload.marketHoursSessions
    : defaultMarketHoursSessions();
  notificationsState.notificationRulesLoaded = true;
  notificationsState.notificationRulesLoading = false;
  notificationsState.notificationRulesError = "";
}

function loadNotificationRules() {
  notificationsState.notificationRulesLoading = true;
  notificationsState.notificationRulesError = "";
  if (isStaticPreviewHost()) {
    applyNotificationRules({ rules: defaultNotificationRules(), conditionTypes: defaultNotificationRuleConditionTypes(), marketHoursSessions: defaultMarketHoursSessions() });
    notificationsState.notificationRulesLoading = false;
    return Promise.resolve();
  }
  return requestJson("/api/notification-rules")
    .then(function (payload) {
      applyNotificationRules(payload);
    })
    .catch(function (error) {
      notificationsState.notificationRulesError = error.message || "알림 룰을 읽지 못했습니다.";
      notificationsState.notificationRules = defaultNotificationRules();
      notificationsState.notificationRuleConditionTypes = defaultNotificationRuleConditionTypes();
      notificationsState.notificationMarketHoursSessions = defaultMarketHoursSessions();
    })
    .finally(function () {
      notificationsState.notificationRulesLoading = false;
      if (shellState.snapshot) render();
    });
}

function applyNotificationJobs(payload) {
  var incomingJobs = Array.isArray(payload.jobs) ? payload.jobs : [];
  var payloadOffset = Math.max(0, Number(payload.offset == null ? notificationsState.notificationJobsOffset : payload.offset));
  var payloadCursor = String(payload.cursor || "");
  notificationsState.notificationJobItems = mobileInfiniteScrollEnabled() && (payloadOffset > 0 || payloadCursor)
    ? mergeUniqueItems(notificationsState.notificationJobItems, incomingJobs, notificationJobKey)
    : incomingJobs;
  var visibleJobs = {};
  notificationsState.notificationJobItems.forEach(function (job) {
    visibleJobs[notificationJobKey(job)] = true;
  });
  Object.keys(notificationsState.notificationExpandedJobs || {}).forEach(function (key) {
    if (!visibleJobs[key]) delete notificationsState.notificationExpandedJobs[key];
  });
  if (notificationsState.activeNotificationJobKey && !visibleJobs[notificationsState.activeNotificationJobKey]) {
    notificationsState.activeNotificationJobKey = "";
  }
  if (!notificationsState.activeNotificationJobKey && notificationsState.notificationJobItems.length) {
    notificationsState.activeNotificationJobKey = notificationJobKey(notificationsState.notificationJobItems[0]);
  }
  notificationsState.notificationJobsSummary = payload.summary && typeof payload.summary === "object" ? payload.summary : {};
  notificationsState.notificationInboxSummary = payload.inboxSummary && typeof payload.inboxSummary === "object" ? payload.inboxSummary : {};
  notificationsState.notificationJobDiagnostics = payload.diagnostics && typeof payload.diagnostics === "object" ? payload.diagnostics : {};
  notificationsState.notificationJobsTotal = Math.max(0, Number(payload.total || notificationsState.notificationJobItems.length));
  notificationsState.notificationJobsOffset = payloadOffset;
  notificationsState.notificationJobsCursor = payloadCursor;
  notificationsState.notificationJobsNextCursor = String(payload.nextCursor || "");
  notificationsState.notificationJobsPageSize = Math.max(1, Number(payload.limit || notificationsState.notificationJobsPageSize || 20));
  notificationsState.notificationJobsLoaded = true;
  notificationsState.notificationJobsLoading = false;
  notificationsState.notificationJobsError = "";
  if (Object.keys(notificationsState.notificationJobsSummary).length) {
    shellState.realtime.notificationJobs = notificationsState.notificationJobsSummary;
  }
}

const inboxLane = createLatestRequestLane();

function notificationListIdentity() {
  return JSON.stringify([notificationsState.notificationJobSearch, notificationsState.notificationJobStatusFilter,
    notificationsState.notificationInboxFilter, notificationsState.notificationJobsCursor,
    notificationsState.notificationJobsOffset, notificationsState.notificationJobsPageSize]);
}

function loadNotificationJobs() {
  var identity = notificationListIdentity();
  var active = inboxLane.active(identity);
  if (active) return active;
  var operation = inboxLane.begin(identity);
  var incrementalLoad = mobileInfiniteScrollEnabled()
    && Boolean((notificationsState.notificationJobItems || []).length)
    && Boolean(Number(notificationsState.notificationJobsOffset || 0) > 0 || notificationsState.notificationJobsCursor);
  notificationsState.notificationJobsLoading = true;
  notificationsState.notificationJobsError = "";
  if (!incrementalLoad && shellState.snapshot) render();
  if (isStaticPreviewHost()) {
    applyNotificationJobs({ jobs: [], summary: {} });
    notificationsState.notificationJobsLoading = false;
    operation.finish();
    return Promise.resolve();
  }
  var params = new URLSearchParams();
  params.set("limit", String(notificationsState.notificationJobsPageSize || 20));
  params.set("offset", String(notificationsState.notificationJobsOffset || 0));
  params.set("scope", "investment");
  params.set("recipientId", notificationRecipientId());
  params.set("inbox", notificationsState.notificationInboxFilter || "all");
  if (mobileInfiniteScrollEnabled() && notificationsState.notificationJobsCursor) params.set("cursor", notificationsState.notificationJobsCursor);
  if (notificationsState.notificationJobStatusFilter && notificationsState.notificationJobStatusFilter !== "all") params.set("status", notificationsState.notificationJobStatusFilter);
  // Presentation kinds filter the loaded page; messageType is a legacy policy key.
  if (notificationsState.notificationJobSearch) params.set("query", notificationsState.notificationJobSearch);
  return operation.track(requestJson("/api/notification-jobs?" + params.toString(), { key: "notification-jobs", force: true })
    .then(function (payload) {
      if (!operation.current() || identity !== notificationListIdentity()) return;
      applyNotificationJobs(payload);
    })
    .catch(function (error) {
      if (!operation.current() || identity !== notificationListIdentity()) return;
      if (String(error.message || "").indexOf("API를 찾지 못했습니다") >= 0) {
        applyNotificationJobs({ jobs: [], summary: {} });
        return;
      }
      notificationsState.notificationJobsError = error.message || "최근 알림 판단을 읽지 못했습니다.";
      // A failed refresh must not erase the last verified ledger.
      if (!notificationsState.notificationJobsLoaded) {
        notificationsState.notificationJobDiagnostics = {};
      }
    })
    .finally(function () {
      if (!operation.current()) return;
      notificationsState.notificationJobsLoading = false;
      operation.finish();
      if (shellState.snapshot) render();
    }));
}

function resetNotificationJobsPaging() {
  inboxLane.invalidate();
  notificationsState.notificationJobsLoading = false;
  notificationsState.notificationJobsOffset = 0;
  notificationsState.notificationJobsCursor = "";
  notificationsState.notificationJobsNextCursor = "";
}

function loadNotificationJobDetail(jobId) {
  var key = String(jobId || "").trim();
  if (!key || notificationsState.notificationJobDetails[key] || isStaticPreviewHost()) return Promise.resolve();
  return requestJson("/api/notification-jobs/" + encodeURIComponent(key) + "?recipientId=" + encodeURIComponent(notificationRecipientId()), {
    key: "notification-job:" + key,
    timeoutMs: 30000
  })
    .then(function (payload) {
      var detail = payload && payload.job;
      if (!detail) return;
      notificationsState.notificationJobDetails[key] = detail;
      notificationsState.notificationJobItems = (notificationsState.notificationJobItems || []).map(function (item) {
        return notificationJobKey(item) === key ? Object.assign({}, item, detail) : item;
      });
    })
    .catch(function () {
      return null;
    })
    .finally(function () {
      if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "notification-job" && navigationState.workDetailLayer.key === key) render();
    });
}

function notificationJobDetailSectionKey(jobId, section) {
  return String(jobId || "") + ":" + String(section || "summary");
}

function loadNotificationJobDetailSection(jobId, section, force) {
  var key = String(jobId || "").trim();
  var normalized = ["reasoning", "ai-review", "delivery"].indexOf(String(section || "")) >= 0 ? String(section) : "summary";
  if (!key || normalized === "summary" || isStaticPreviewHost()) return Promise.resolve();
  var cacheKey = notificationJobDetailSectionKey(key, normalized);
  var existing = notificationsState.notificationJobDetailSections[cacheKey];
  if (!force && existing && existing.status !== "error") return Promise.resolve(existing);
  notificationsState.notificationJobDetailSections[cacheKey] = { status: "loading" };
  render({ transition: "section" });
  return requestJson("/api/notification-jobs/" + encodeURIComponent(key) + "/" + normalized + "?recipientId=" + encodeURIComponent(notificationRecipientId()), {
    key: "notification-job-section:" + cacheKey,
    timeoutMs: 30000
  }).then(function (payload) {
    notificationsState.notificationJobDetailSections[cacheKey] = Object.assign({ status: "ready" }, payload || {});
    return payload;
  }).catch(function (error) {
    notificationsState.notificationJobDetailSections[cacheKey] = { status: "error", error: String((error && error.message) || error || "상세 조회 실패") };
    return null;
  }).finally(function () {
    if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "notification-job" && navigationState.workDetailLayer.key === key) render({ transition: "section" });
  });
}

function updateNotificationReceipt(jobId, changes) {
  var key = String(jobId || "").trim();
  if (!key) return Promise.resolve();
  var existing = notificationsState.notificationJobItems.filter(function (item) { return notificationJobKey(item) === key; })[0] || {};
  var next = Object.assign({}, existing, changes || {});
  if (Object.prototype.hasOwnProperty.call(changes || {}, "read")) next.readAt = changes.read ? new Date().toISOString() : "";
  if (Object.prototype.hasOwnProperty.call(changes || {}, "acknowledged")) {
    next.acknowledgedAt = changes.acknowledged ? new Date().toISOString() : "";
    if (changes.acknowledged) next.readAt = next.readAt || new Date().toISOString();
  }
  if (Object.prototype.hasOwnProperty.call(changes || {}, "important")) next.important = Boolean(changes.important);
  if (Object.prototype.hasOwnProperty.call(changes || {}, "usefulness")) {
    next.usefulness = String(changes.usefulness || "");
    next.feedbackReason = String(changes.feedbackReason || "");
    next.feedbackAt = next.usefulness ? new Date().toISOString() : "";
  }
  notificationsState.notificationJobItems = notificationsState.notificationJobItems.map(function (item) {
    return notificationJobKey(item) === key ? next : item;
  });
  if (notificationsState.notificationJobDetails[key]) notificationsState.notificationJobDetails[key] = Object.assign({}, notificationsState.notificationJobDetails[key], next);
  render();
  if (isStaticPreviewHost()) return Promise.resolve();
  return sendJson("/api/notification-jobs/" + encodeURIComponent(key) + "/receipt", "PATCH", Object.assign({
    recipientId: notificationRecipientId()
  }, changes || {})).then(function (payload) {
    var receipt = payload.receipt || {};
    notificationsState.notificationJobItems = notificationsState.notificationJobItems.map(function (item) {
      return notificationJobKey(item) === key ? Object.assign({}, item, receipt) : item;
    });
    if (notificationsState.notificationJobDetails[key]) notificationsState.notificationJobDetails[key] = Object.assign({}, notificationsState.notificationJobDetails[key], receipt);
    notificationsState.notificationInboxSummary = payload.inboxSummary || notificationsState.notificationInboxSummary;
    render();
  }).catch(function (error) {
    notificationsState.notificationJobsError = error.message || "알림 확인 상태를 저장하지 못했습니다.";
    return loadNotificationJobs();
  });
}

function markAllNotificationsRead() {
  notificationsState.notificationJobItems = notificationsState.notificationJobItems.map(function (item) {
    return Object.assign({}, item, { readAt: item.readAt || new Date().toISOString() });
  });
  Object.keys(notificationsState.notificationJobDetails || {}).forEach(function (key) {
    notificationsState.notificationJobDetails[key] = Object.assign({}, notificationsState.notificationJobDetails[key], {
      readAt: notificationsState.notificationJobDetails[key].readAt || new Date().toISOString()
    });
  });
  notificationsState.notificationInboxSummary = Object.assign({}, notificationsState.notificationInboxSummary, { unread: 0 });
  render();
  if (isStaticPreviewHost()) return Promise.resolve();
  return sendJson("/api/notification-jobs/read-all", "POST", {
    recipientId: notificationRecipientId(),
    scope: "investment"
  }).then(function (payload) {
    notificationsState.notificationInboxSummary = payload.inboxSummary || notificationsState.notificationInboxSummary;
    render();
  }).catch(function (error) {
    notificationsState.notificationJobsError = error.message || "모두 읽음 상태를 저장하지 못했습니다.";
    return loadNotificationJobs();
  });
}

function applyNotificationSchedules(payload) {
  notificationsState.messageSchedules = Array.isArray(payload.schedules) ? payload.schedules : [];
  notificationsState.messageSchedulesLoaded = true;
  notificationsState.messageSchedulesLoading = false;
  notificationsState.messageSchedulesError = "";
}

function loadNotificationSchedules() {
  notificationsState.messageSchedulesLoading = true;
  notificationsState.messageSchedulesError = "";
  if (isStaticPreviewHost()) {
    applyNotificationSchedules({ schedules: [] });
    notificationsState.messageSchedulesLoading = false;
    return Promise.resolve();
  }
  return requestJson("/api/notification-schedules")
    .then(function (payload) {
      applyNotificationSchedules(payload);
    })
    .catch(function (error) {
      notificationsState.messageSchedulesError = error.message || "메시지 스케줄을 읽지 못했습니다.";
      notificationsState.messageSchedules = [];
    })
    .finally(function () {
      notificationsState.messageSchedulesLoading = false;
      if (shellState.snapshot) render();
    });
}

export { loadNotificationJobDetail, loadNotificationJobDetailSection, loadNotificationJobs, loadNotificationRules, loadNotificationSchedules, loadNotificationTemplates, markAllNotificationsRead, notificationJobDetailSectionKey, resetNotificationJobsPaging, updateNotificationReceipt };
