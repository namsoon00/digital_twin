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
      tasks.push({
        key: "decision:" + (row.id || row.symbol),
        priority: ["SELL", "TRIM", "AVOID"].indexOf(String(row.action || "").toUpperCase()) >= 0 ? 1 : 2,
        kind: "판단",
        target: row.name || row.symbol,
        reason: formatConsoleNarrative(row.headline || row.nextAction || "행동 조건을 확인하세요."),
        state: action.label,
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
        reason: row.reason,
        state: row.decision,
        tone: row.tone,
        action: "판단 상세",
        detailType: "investment-action",
        detailKey: row.key,
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
  var tasks = selectConsoleTodayTasks(shellState.snapshot || {}, { collapseCalendar: false });
  var historicalCount = consoleTodayHistoricalCount();
  var page = consolePageSlice(tasks, "today", 12);
  var body = page.items.length
    ? '<div class="oa-work-list" data-console-keyed-list="today-full">' + page.items.map(renderConsoleTaskRow).join("") + '</div>'
    : renderConsoleEmpty("처리할 작업이 없습니다", "새 판단, 알림, 일정, 데이터 이상이 생기면 이 큐에 표시합니다.");
  return editorWorkDetailPayload(
    "Priority Queue",
    "전체 작업 큐",
    "현재 " + tasks.length + "건" + (historicalCount ? " · 이전 기록 " + historicalCount + "건은 이력에 보관" : ""),
    '<section class="oa-detail-queue">' + renderConsoleLiveRegion("today-full-body", body) + renderConsolePager("today", page) + '</section>'
  );
}

function renderTodayConsole(snapshot) {
  var portfolio = selectConsolePortfolio(snapshot);
  var dashboard = shellState.dashboardSummary || {};
  var dashboardPortfolio = dashboard.portfolio || {};
  var tasks = selectConsoleTodayTasks(snapshot, { collapseCalendar: true });
  var historicalCount = consoleTodayHistoricalCount();
  var urgent = tasks.filter(function (task) { return task.priority <= 2; }).length;
  var upcoming = Array.isArray(dashboard.upcomingEvents) && dashboard.upcomingEvents.length ? dashboard.upcomingEvents : investmentCalendarUpcomingEvents();
  var blockers = Array.isArray(dashboard.blockerGroups) ? dashboard.blockerGroups : [];
  var totalValue = hasNumericValue(dashboardPortfolio.invested) ? numeric(dashboardPortfolio.invested) : portfolio.invested;
  var positionCount = hasNumericValue(dashboardPortfolio.positionCount) ? numeric(dashboardPortfolio.positionCount) : portfolio.holdingCount;
  var valuationBasis = String(dashboardPortfolio.valuationBasis || portfolio.valuationBasis || "legacy-unknown");
  var metrics = [
    { label: portfolioInvestedMetricLabel(valuationBasis), value: formatMoney(totalValue), detail: portfolioValuationBasisLabel(valuationBasis) + " · 현금 제외 · " + positionCount + "개 보유", target: { type: "tab", value: "portfolio" } },
    { label: "현금", value: hasNumericValue(dashboardPortfolio.cash) ? formatMoney(dashboardPortfolio.cash) : formatMoney(portfolio.cash), detail: "포트폴리오 원장", target: { type: "tab", value: "portfolio" } },
    { label: "긴급 작업", value: urgent + "건", detail: "우선순위 1·2", tone: urgent ? "danger" : "watch", target: { type: "detail", value: "today-work-queue" } },
    { label: "예정 일정", value: upcoming.length + "건", detail: upcoming[0] ? formatClock(upcoming[0].startsAt) : "일정 없음", target: upcoming[0] ? { type: "detail", value: "investment-calendar-event", key: upcoming[0].eventId || upcoming[0].id || upcoming[0].title || "" } : { type: "tab", value: "calendar" } },
    { label: "데이터", value: portfolio.freshness.label, detail: portfolio.freshness.detail, tone: portfolio.freshness.tone, target: { type: "detail", value: "feed-source-board" } }
  ];
  var taskBody = tasks.length ? '<div class="oa-work-list" data-console-keyed-list="today-primary">' + tasks.slice(0, 3).map(renderConsoleTaskRow).join("") + '</div>' : renderConsoleEmpty("오늘 처리할 작업이 없습니다", "새 판단이나 전달 실패가 생기면 우선순위에 따라 표시합니다.");
  var contextBody = [
    '<div class="oa-context-list" data-console-keyed-list="today-blockers">',
    blockers.length ? blockers.slice(0, 3).map(function (item) {
      var blockerTone = ["error", "blocked"].indexOf(String(item.state || "")) >= 0 ? "danger" : "caution";
      return '<button type="button" class="oa-context-row" data-console-row-key="' + escapeHtml(item.id || item.label) + '" data-tab="experiments"><span><strong>' + escapeHtml(item.label || "근거 점검") + '</strong><em>' + escapeHtml(formatConsoleNarrative(item.reason || item.effect || "판단 조건을 더 확인해야 합니다.")) + '</em></span><b class="' + blockerTone + '">' + escapeHtml((item.count || 0) + "건") + '</b></button>';
    }).join("") : '<div class="oa-context-row"><span><strong>묶인 차단 원인 없음</strong><em>현재 판단 기록에서 공통 차단 원인이 발견되지 않았습니다.</em></span></div>',
    '</div>',
    upcoming[0] ? '<button type="button" class="oa-next-event" data-work-detail="investment-calendar-event" data-work-detail-key="' + escapeHtml(upcoming[0].eventId || upcoming[0].id || upcoming[0].title || "") + '"><span>다음 일정</span><strong>' + escapeHtml(upcoming[0].title || "투자 이벤트") + '</strong><em>' + escapeHtml(formatClock(upcoming[0].startsAt)) + '</em><b aria-hidden="true">&rarr;</b></button>' : '',
  ].join("");
  return renderConsoleManagedPage("overview", metrics, [
    '<div class="oa-console-grid oa-console-grid-primary">',
    renderConsoleSurface({ kicker: "PRIORITY QUEUE", title: "지금 처리할 일", description: "최근 " + consoleTodayDecisionWindowHours() + "시간의 판단과 아직 조치 가능한 전달 실패만 표시합니다. 이전 기록은 판단·알림 이력에 남습니다.", meta: "현재 " + tasks.length + "건" + (historicalCount ? " · 이전 " + historicalCount + "건" : ""), actions: tasks.length > 3 ? renderWorkDetailButton("today-work-queue", "", "전체 보기", "text-button compact") : "", body: renderConsoleLiveRegion("today-primary-body", taskBody) }),
    renderConsoleSurface({ kicker: "BLOCKER GROUPS", title: "공통 확인 원인", description: "같은 원인으로 막힌 종목을 데이터·추론·AI 단계별로 묶습니다.", body: renderConsoleLiveRegion("today-context-body", contextBody) }),
    '</div>'
  ].join(""));
}

export { renderTodayConsole, todayQueueWorkDetailPayload };
