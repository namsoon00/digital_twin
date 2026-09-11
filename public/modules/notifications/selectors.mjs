import { formatConsoleNarrative, stockDisplayName, textWithKnownDisplaySymbols } from "../instruments/catalog.mjs";
import { notificationJobKey, notificationJobResolvedSymbol } from "./detail.mjs";
import { filteredNotificationJobs, notificationJobDecisionFactors, notificationJobDecisionRoute, notificationJobStatusLabel, notificationJobToneClass, notificationJobTypeKey, notificationJobTypeLabel } from "./history.mjs";
import { realtimeEventLabel } from "../realtime/labels.mjs";
import { recordChangedAt, recordChangedAtValue } from "../shared/format.mjs";
import { notificationsState } from "../state/notifications.mjs";

function selectConsoleAlertRows() {
  return filteredNotificationJobs(notificationsState.notificationJobItems || []).map(function (job) {
    var symbol = notificationJobResolvedSymbol(job);
    var factors = notificationJobDecisionFactors(job);
    var movement = notificationJobDecisionRoute(job);
    var eventTitle = realtimeEventLabel(String(job.title || ""));
    var title = textWithKnownDisplaySymbols(eventTitle, symbol, job) || (symbol ? stockDisplayName(symbol, job) : notificationJobTypeLabel(notificationJobTypeKey(job), [job]));
    return {
      key: notificationJobKey(job),
      time: recordChangedAt(job),
      updatedAt: recordChangedAt(job),
      symbol: symbol,
      title: title,
      type: notificationJobTypeLabel(notificationJobTypeKey(job), [job]),
      movement: movement,
      reason: formatConsoleNarrative((factors[0] || {}).label || (Array.isArray(job.deliveryReasons) ? job.deliveryReasons[0] : "") || job.suppressionSummary || job.textPreview || "발송 판단 기록"),
      status: notificationJobStatusLabel(job.status),
      tone: notificationJobToneClass(job.status),
      channel: job.channel || job.deliveryChannel || "Telegram",
      accountId: String(job.accountId || "default"),
      decisionEpisodeId: String(job.decisionEpisodeId || ((job.context || {}).investmentDecisionEpisodeId) || ""),
      decisionKey: String(job.decisionKey || ((job.context || {}).decisionKey) || ""),
      readAt: String(job.readAt || ""),
      acknowledgedAt: String(job.acknowledgedAt || ""),
      important: Boolean(job.important),
      apiSource: String(job.apiSource || "notification_jobs"),
      dataQuality: String(job.dataQuality || "actual"),
      isMock: Boolean(job.isMock),
      raw: job
    };
  }).sort(function (a, b) {
    return recordChangedAtValue(b) - recordChangedAtValue(a);
  });
}

export { selectConsoleAlertRows };
