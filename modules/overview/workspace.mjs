import { investmentCalendarDisplayTitle, investmentCalendarImpactText, investmentCalendarUpcomingEvents } from "../calendar/workspace.mjs";
import { decisionActionMeta, selectConsoleDecisionRows } from "../decisions/selectors.mjs";
import { formatConsoleNarrative, textWithKnownDisplaySymbols } from "../instruments/catalog.mjs";
import { editorWorkDetailPayload, renderWorkDetailButton } from "../navigation/detail.mjs";
import { notificationJobKey, notificationJobResolvedSymbol } from "../notifications/detail.mjs";
import { notificationJobStatusLabel, notificationJobToneClass, notificationJobTypeLabel } from "../notifications/history.mjs";
import { selectConsolePortfolio } from "../portfolio/selectors.mjs";
import { portfolioInvestedMetricLabel, portfolioValuationBasisLabel } from "../portfolio/valuation.mjs";
import { realtimeEventLabel } from "../realtime/labels.mjs";
import { consolePageSlice, renderConsoleEmpty, renderConsoleLiveRegion, renderConsoleManagedPage, renderConsolePager, renderConsoleSurface } from "../shared/console.mjs";
import { formatClock, formatMoney, hasNumericValue, numeric, recordChangedAt, recordChangedAtValue, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { shellState } from "../state/shell.mjs";
import { renderSecondaryDisclosure } from "../shared/disclosure.mjs";
import { groupTodayTasks } from "./task-groups.mjs";
import { investmentReading } from "../decisions/brief.mjs";

function consoleTodayDecisionWindowHours() {
  return Math.max(1, Number((((shellState.dashboardSummary || {}).taskSummary || {}).freshnessWindowHours) || 96));
}

function consoleTodayRecordIsCurrent(record, windowMinutes) {
  var changedAt = recordChangedAtValue(record);
  if (!changedAt) return false;
  return changedAt >= Date.now() - Math.max(1, Number(windowMinutes || 0)) * 60 * 1000;
}

function consoleTodayDecisionIsCurrent(row) {
  return consoleTodayRecordIsCurrent(row, consoleTodayDecisionWindowHours() * 60);
}

function consoleTodayNotificationIsActionable(job) {
  if (!job || job.acknowledgedAt) return false;
  if (typeof job.priorityQueueEligible === "boolean") return job.priorityQueueEligible;
  if (job.recoverableProcessing) return true;
  if (job.status !== "failed") return false;
  return consoleTodayRecordIsCurrent(job, Number(job.priorityQueueWindowMinutes || 60));
}

function consoleTodayHistoricalCount() {
  var taskSummary = ((shellState.dashboardSummary || {}).taskSummary || {});
  var decisionHistory = Math.max(0, Number(taskSummary.historical || 0));
  var notificationHistory = (notificationsState.notificationJobItems || []).filter(function (job) {
    return job.status === "failed" && !consoleTodayNotificationIsActionable(job);
  }).length;
  return decisionHistory + notificationHistory;
}

function selectConsoleTodayTasks(snapshot, options) {
  options = options || {};
  var tasks = [];
  var dashboardTasks = Array.isArray((shellState.dashboardSummary || {}).tasks) ? shellState.dashboardSummary.tasks : [];
  if (dashboardTasks.length || (shellState.dashboardSummary || {}).version) {
    dashboardTasks.filter(consoleTodayDecisionIsCurrent).forEach(function (row) {
      var action = decisionActionMeta(row.action, row.action);
      var reading = row.reading || investmentReading({decision: {action: row.action}, headline: row.headline}, action.label);
      tasks.push({
        key: "decision:" + (row.id || row.symbol),
        priority: ["SELL", "TRIM", "AVOID"].indexOf(String(row.action || "").toUpperCase()) >= 0 ? 1 : 2,
        kind: "판단",
        target: row.name || row.symbol,
        reason: reading.meaning || reading.headline,
        state: reading.status,
        reading: reading,
        tone: action.tone,
        action: "판단 상세",
        detailType: "investment-case",
        detailKey: row.id || "",
        updatedAt: row.updatedAt
      });
    });
  } else {
    selectConsoleDecisionRows(snapshot).filter(consoleTodayDecisionIsCurrent).forEach(function (row) {
      tasks.push({
        key: "decision:" + row.key,
        priority: row.tone === "danger" ? 1 : (row.tone === "caution" ? 2 : 3),
        kind: "판단",
        target: row.name || row.symbol,
        reason: row.reading ? row.reading.meaning || row.reading.headline : row.reason,
        reading: row.reading,
        state: row.reading ? row.reading.status : row.decision,
        tone: row.tone,
        action: "판단 상세",
        detailType: row.subjectCaseId || row.caseId || row.decisionEpisodeId ? "investment-case" : "investment-action",
        detailKey: row.subjectCaseId || row.caseId || row.decisionEpisodeId || row.key,
        updatedAt: row.updatedAt
      });
    });
  }
  (notificationsState.notificationJobItems || []).filter(consoleTodayNotificationIsActionable).forEach(function (job, index) {
    var jobKey = notificationJobKey(job) || String(index);
    tasks.push({
      key: "notification:" + jobKey,
      priority: job.status === "failed" ? 1 : 2,
      kind: "알림",
      target: textWithKnownDisplaySymbols(realtimeEventLabel(job.title || notificationJobTypeLabel(job.messageType, [job])), notificationJobResolvedSymbol(job), job),
      reason: formatConsoleNarrative(job.lastError || job.suppressionSummary || "전달 상태 확인 필요"),
      state: notificationJobStatusLabel(job.status),
      tone: notificationJobToneClass(job.status),
      action: "알림 상세",
      detailType: "notification-job",
      detailKey: jobKey,
      updatedAt: recordChangedAt(job)
    });
  });
  if (!(shellState.dashboardSummary || {}).version) {
    var calendarEvents = investmentCalendarUpcomingEvents();
    if (options.collapseCalendar) {
      var seenCalendarTypes = {};
      calendarEvents = calendarEvents.filter(function (event) {
        var key = String(event.type || event.title || "event").trim().toLowerCase();
        if (seenCalendarTypes[key]) return false;
        seenCalendarTypes[key] = true;
        return true;
      });
    }
    calendarEvents.forEach(function (event, index) {
      var eventKey = event.eventId || event.id || event.title || String(index);
      tasks.push({
        key: "calendar:" + eventKey,
        priority: Number(event.importance || 0) >= 80 ? 2 : 4,
        kind: "일정",
        target: investmentCalendarDisplayTitle(event, "투자 이벤트"),
        reason: formatConsoleNarrative(investmentCalendarImpactText(event)),
        state: formatClock(event.startsAt),
        tone: Number(event.importance || 0) >= 80 ? "caution" : "hold",
        action: "일정 상세",
        detailType: "investment-calendar-event",
        detailKey: eventKey,
        updatedAt: recordChangedAt(event)
      });
    });
  }
  var toss = (snapshot || {}).toss || {};
  if (toss.mode !== "live") {
    tasks.push({
      key: "data:toss-connection",
      priority: 1,
      kind: "데이터",
      target: "Toss 계정 연결",
      reason: toss.status || "실계좌 데이터 연결 상태를 확인해야 합니다.",
      state: toss.mode || "unknown",
      tone: "danger",
      action: "연결 상세",
      detailType: "account-connections-board",
      detailKey: "",
      updatedAt: recordChangedAt(toss, (snapshot || {}).generatedAt)
    });
  }
  return tasks.sort(function (a, b) {
    var changedDiff = recordChangedAtValue(b) - recordChangedAtValue(a);
    if (changedDiff) return changedDiff;
    if (a.priority !== b.priority) return a.priority - b.priority;
    return String(a.target || "").localeCompare(String(b.target || ""));
  });
}

function renderConsoleTaskRow(task) {
  return [
    '<button class="oa-work-row" type="button" data-console-row-key="' + escapeHtml(task.key || [task.kind, task.detailKey].join(":")) + '" data-work-detail="' + escapeHtml(task.detailType || "") + '" data-work-detail-key="' + escapeHtml(task.detailKey || "") + '">',
    '<span class="oa-row-kind">' + escapeHtml(task.kind || "-") + '</span>',
    '<span class="oa-row-main"><strong>' + escapeHtml(task.target || "-") + '</strong><em>' + escapeHtml(task.reason || "") + '</em>' + renderRecordChangedAt(task) + '</span>',
    '<span class="tone-chip ' + escapeHtml(task.tone || "hold") + '">' + escapeHtml(task.state || "-") + '</span>',
    '<span class="oa-row-action">' + escapeHtml(task.action || "상세") + ' &rarr;</span>',
    '</button>'
  ].join("");
}

function todayQueueWorkDetailPayload() {
  var tasks = groupTodayTasks(selectConsoleTodayTasks(shellState.snapshot || {}, { collapseCalendar: false })).investment;
  var historicalCount = consoleTodayHistoricalCount();
  var page = consolePageSlice(tasks, "today", 12);
  var body = page.items.length
    ? '<div class="oa-work-list" data-console-keyed-list="today-full">' + page.items.map(renderConsoleTaskRow).join("") + '</div>'
    : renderConsoleEmpty("현재 확인할 투자 의견이 없습니다", "매수·보유·매도 의견이 없는 상태를 보유 유지 의견으로 해석하지 않습니다.");
  return editorWorkDetailPayload(
    "Investment Views",
    "내 종목 투자 의견",
    "현재 " + tasks.length + "건" + (historicalCount ? " · 이전 기록 " + historicalCount + "건은 이력에 보관" : ""),
    '<section class="oa-detail-queue">' + renderConsoleLiveRegion("today-full-body", body) + renderConsolePager("today", page) + '</section>'
  );
}

function renderTodayConsole(snapshot) {
  var portfolio = selectConsolePortfolio(snapshot);
  var dashboard = shellState.dashboardSummary || {};
  var dashboardPortfolio = dashboard.portfolio || {};
  var groups = groupTodayTasks(selectConsoleTodayTasks(snapshot, { collapseCalendar: true }));
  var tasks = groups.investment;
  var pendingRows = selectConsoleDecisionRows(snapshot).filter(function (row) {
    return row.reading && ["awaiting", "unavailable"].includes(row.reading.kind) && consoleTodayDecisionIsCurrent(row);
  });
  var upcoming = Array.isArray(dashboard.upcomingEvents) && dashboard.upcomingEvents.length ? dashboard.upcomingEvents : investmentCalendarUpcomingEvents();
  var blockers = Array.isArray(dashboard.blockerGroups) ? dashboard.blockerGroups : [];
  var totalValue = hasNumericValue(dashboardPortfolio.invested) ? numeric(dashboardPortfolio.invested) : portfolio.invested;
  var positionCount = hasNumericValue(dashboardPortfolio.positionCount) ? numeric(dashboardPortfolio.positionCount) : portfolio.holdingCount;
  var valuationBasis = String(dashboardPortfolio.valuationBasis || portfolio.valuationBasis || "legacy-unknown");
  var metrics = [
    { label: portfolioInvestedMetricLabel(valuationBasis), value: formatMoney(totalValue), detail: portfolioValuationBasisLabel(valuationBasis) + " · 현금 제외 · " + positionCount + "개 보유", target: { type: "tab", value: "portfolio" } },
    { label: "현금", value: hasNumericValue(dashboardPortfolio.cash) ? formatMoney(dashboardPortfolio.cash) : formatMoney(portfolio.cash), detail: "포트폴리오 원장", target: { type: "tab", value: "portfolio" } },
    { label: "투자 의견", value: tasks.length + "건", detail: "내 종목 분석", target: { type: "detail", value: "today-work-queue" } },
    { label: "다가오는 일정", value: upcoming.length + "건 표시", detail: "미리보기 · 전체 일정은 캘린더", target: { type: "tab", value: "calendar" } },
    { label: "데이터", value: portfolio.freshness.label, detail: portfolio.freshness.detail, tone: portfolio.freshness.tone, target: { type: "detail", value: "feed-source-board" } }
  ];
  var taskBody = tasks.length ? '<div class="oa-work-list" data-console-keyed-list="today-primary">' + tasks.slice(0, 3).map(renderConsoleTaskRow).join("") + '</div>' : renderConsoleEmpty("새로 확인할 투자 의견이 없습니다", pendingRows.length ? pendingRows.length + "개 종목은 아직 투자 의견이 확정되지 않았습니다." : "현재 저장된 분석 중 새로 검토할 투자 의견이 없습니다.");
  var importantBlockers = blockers.filter(function (item) { return ["blocked", "error"].includes(item.state); });
  var contextBody = [
    upcoming[0] ? '<button type="button" class="oa-next-event" data-work-detail="investment-calendar-event" data-work-detail-key="' + escapeHtml(upcoming[0].eventId || upcoming[0].id || upcoming[0].title || "") + '"><span>다음 일정</span><strong>' + escapeHtml(investmentCalendarDisplayTitle(upcoming[0])) + '</strong><em>' + escapeHtml(formatClock(upcoming[0].startsAt)) + '</em><p>' + escapeHtml(investmentCalendarImpactText(upcoming[0])) + '</p><b aria-hidden="true">&rarr;</b></button>' : '',
  ].join("");
  return renderConsoleManagedPage("overview", metrics, [
    '<div class="oa-console-grid oa-console-grid-primary">',
    renderConsoleSurface({ title: "내 종목에서 확인할 투자 의견", meta: tasks.length + "건", actions: tasks.length > 3 ? renderWorkDetailButton("today-work-queue", "", "전체 보기", "text-button compact") : "", body: renderConsoleLiveRegion("today-primary-body", taskBody) }),
    upcoming.length ? renderConsoleSurface({ title: "다가오는 투자 일정", body: renderConsoleLiveRegion("today-context-body", contextBody) }) : '',
    '</div>',
    pendingRows.length ? renderSecondaryDisclosure("today-awaiting", "투자 의견이 아직 없는 종목", '<div class="oa-context-list">' + pendingRows.map(function (row) {
      return '<button type="button" class="oa-context-row" data-work-detail="investment-case" data-work-detail-key="' + escapeHtml(row.subjectCaseId || row.caseId || row.decisionEpisodeId || row.key) + '"><span><strong>' + escapeHtml(row.name) + '</strong><em>' + escapeHtml(row.reading.headline) + '</em></span><b aria-hidden="true">&rarr;</b></button>';
    }).join("") + '</div>', pendingRows.length + "개 종목") : '',
    portfolio.freshness.tone !== "watch" ? '<p class="oa-data-notice caution">자료 상태 · ' + escapeHtml(portfolio.freshness.label + " · " + portfolio.freshness.detail) + '</p>' : '',
    groups.operations.length || importantBlockers.length ? renderSecondaryDisclosure("today-operations", "수집·전달 문제", '<div class="oa-work-list" data-console-keyed-list="today-operations">' + groups.operations.map(renderConsoleTaskRow).join("") + '</div>' + (importantBlockers.length ? '<button class="text-button" type="button" data-tab="experiments">분석 처리 문제 ' + importantBlockers.length + '종류 확인</button>' : ''), groups.operations.length + "건 · 분석 처리 " + importantBlockers.length + "종류") : '',
    '<nav class="oa-related-links"><button class="text-button" type="button" data-tab="modeling">투자 의견 전체</button><button class="text-button" type="button" data-tab="experiments">근거 점검</button></nav>'
  ].join(""), { secondaryMetrics: true });
}

export { renderTodayConsole, todayQueueWorkDetailPayload };
