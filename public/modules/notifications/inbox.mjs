import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { mobileInfiniteScrollEnabled, renderMobileInfiniteScrollFooter } from "../navigation/infinite-list.mjs";
import { filteredNotificationJobs, renderNotificationJobFilterToolbar } from "./history.mjs";
import { selectConsoleAlertRows } from "./selectors.mjs";
import { renderConsoleEmpty, renderConsoleListSkeleton, renderConsoleLiveRegion, renderConsoleManagedPage, renderConsoleSurface } from "../shared/console.mjs";
import { renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { shellState } from "../state/shell.mjs";

function renderAlertConsoleRow(row) {
  return [
    '<article class="oa-alert-card' + (row.readAt ? "" : " unread") + (row.important ? " important" : "") + '" data-console-row-key="' + escapeHtml(row.key) + '">',
    '<header><div><span class="tone-chip ' + escapeHtml(row.movement.tone || "hold") + '">' + escapeHtml(row.movement.label || "변화") + '</span><strong>' + escapeHtml(row.title) + '</strong><em>' + escapeHtml(row.type) + '</em></div><b class="' + escapeHtml(row.tone) + '">' + escapeHtml(row.status) + '</b></header>',
    '<section class="oa-alert-change"><span>무엇이 달라졌나</span><strong>' + escapeHtml(row.movement.change || row.movement.relation || "상태 변화") + '</strong></section>',
    '<section class="oa-alert-reason"><span>알림 이유</span><p>' + escapeHtml(row.reason) + '</p></section>',
    '<div class="oa-alert-delivery"><span><b>' + escapeHtml(row.channel) + '</b><em>' + escapeHtml(row.dataQuality === "actual" && !row.isMock ? "실데이터" : "MOCK") + ' · ' + escapeHtml(row.apiSource) + '</em></span>' + renderRecordChangedAt(row) + '</div>',
    '<footer>',
    row.decisionEpisodeId ? renderWorkDetailButton("investment-case", row.decisionEpisodeId, "연결된 판단", "text-button compact") : '',
    '<span class="oa-alert-actions"><button class="icon-button" type="button" data-notification-receipt="important" data-notification-job-id="' + escapeHtml(row.key) + '" data-notification-receipt-value="' + escapeHtml(row.important ? "false" : "true") + '" aria-label="중요 표시" title="중요 표시">' + (row.important ? '&#9733;' : '&#9734;') + '</button><button class="icon-button" type="button" data-notification-receipt="acknowledged" data-notification-job-id="' + escapeHtml(row.key) + '" data-notification-receipt-value="' + escapeHtml(row.acknowledgedAt ? "false" : "true") + '" aria-label="확인 완료" title="확인 완료">&#10003;</button><button class="icon-button primary" type="button" data-work-detail="notification-job" data-work-detail-key="' + escapeHtml(row.key) + '" aria-label="알림 상세" title="알림 상세">&rarr;</button></span>',
    '</footer>',
    '</article>'
  ].join("");
}

function renderNotificationJobPager() {
  var pageSize = Math.max(1, Number(notificationsState.notificationJobsPageSize || 20));
  var total = Math.max(0, Number(notificationsState.notificationJobsTotal || 0));
  var offset = Math.max(0, Number(notificationsState.notificationJobsOffset || 0));
  var current = Math.floor(offset / pageSize) + 1;
  var pages = Math.max(1, Math.ceil(total / pageSize));
  if (mobileInfiniteScrollEnabled()) {
    var nextCursor = String(notificationsState.notificationJobsNextCursor || "");
    return renderMobileInfiniteScrollFooter({
      loaded: (notificationsState.notificationJobItems || []).length,
      total: total,
      loading: notificationsState.notificationJobsLoading,
      hasNext: Boolean(nextCursor),
      nextAttributes: 'data-notification-job-page="' + escapeHtml(current + 1) + '" data-notification-job-cursor="' + escapeHtml(nextCursor) + '"'
    });
  }
  if (pages <= 1) return "";
  return [
    '<div class="oa-pager" aria-label="알림 원장 페이지">',
    '<span>' + escapeHtml(total ? (offset + 1) + "-" + Math.min(total, offset + pageSize) + " / " + total + "건" : "0건") + '</span>',
    '<div>',
    '<button class="icon-button" type="button" data-notification-job-page="' + escapeHtml(current - 1) + '" aria-label="이전 페이지"' + (current > 1 ? "" : " disabled") + '>&larr;</button>',
    '<span>' + escapeHtml(current + " / " + pages) + '</span>',
    '<button class="icon-button" type="button" data-notification-job-page="' + escapeHtml(current + 1) + '" aria-label="다음 페이지"' + (current < pages ? "" : " disabled") + '>&rarr;</button>',
    '</div></div>'
  ].join("");
}

function renderAlertsConsole() {
  var rows = selectConsoleAlertRows();
  var summary = notificationsState.notificationJobsSummary || shellState.realtime.notificationJobs || {};
  var inbox = notificationsState.notificationInboxSummary || {};
  var initialLoading = !notificationsState.notificationJobsLoaded && !notificationsState.notificationJobsError;
  var metrics = [
    { label: "읽지 않음", value: Number(inbox.unread || 0) + "건", detail: "내 알림", tone: Number(inbox.unread || 0) ? "caution" : "watch", target: { type: "notification", value: "unread" } },
    { label: "중요", value: Number(inbox.important || 0) + "건", detail: "직접 표시", tone: Number(inbox.important || 0) ? "watch" : "neutral", target: { type: "notification", value: "important" } },
    { label: "확인 필요", value: Number(inbox.actionRequired || 0) + "건", detail: "미확인 변화", tone: Number(inbox.actionRequired || 0) ? "danger" : "watch", target: { type: "notification", value: "action" } },
    { label: "전체 알림", value: Number(inbox.total || notificationsState.notificationJobsTotal || 0) + "건", detail: "최신순", target: { type: "notification", value: "all" } },
    { label: "전송 실패", value: Number(summary.failed || 0) + "건", detail: "전달 상태", tone: Number(summary.failed || 0) ? "danger" : "watch", target: { type: "notification", value: "all", scope: "failed" } }
  ];
  var toolbar = renderNotificationJobFilterToolbar(notificationsState.notificationJobItems || [], filteredNotificationJobs(notificationsState.notificationJobItems || []));
  var table = rows.length ? '<div class="oa-alert-card-list" data-console-keyed-list="alerts-ledger">' + rows.map(renderAlertConsoleRow).join("") + '</div>' : (initialLoading ? renderConsoleListSkeleton("oa-alert-card", ["대상", "변화", "이유", "상태"], 5) : renderConsoleEmpty(notificationsState.notificationJobsError ? "변화 알림을 불러오지 못했습니다" : "조건에 맞는 알림이 없습니다", notificationsState.notificationJobsError || "의미 있는 상태 변화가 생기면 판단과 연결해 표시합니다.", renderWorkDetailButton("notification-diagnostics-board", "", "전달 진단", "text-button compact")));
  return renderConsoleManagedPage("notifications", metrics, [
    '<div data-console-monitor-destination="alerts" tabindex="-1">',
    renderConsoleSurface({ kicker: "CHANGE INBOX", title: "변화 알림", description: "달라진 내용과 전달 결과를 구분하고 원래 투자 판단으로 연결합니다.", actions: '<button class="text-button compact" type="button" data-action="mark-all-notifications-read">모두 읽음</button>' + renderWorkDetailButton("notification-policy-board", "", "발송 정책", "text-button compact"), body: toolbar + renderConsoleLiveRegion("alerts-ledger-body", table), footer: renderNotificationJobPager() }),
    '</div>'
  ].join(""), { loading: initialLoading });
}

export { renderAlertsConsole };
