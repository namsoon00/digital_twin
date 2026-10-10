import { recordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { notificationClock } from "./clock.mjs";

function renderNotificationChangedAt(record, fallback, className) {
  var changedAt = recordChangedAt(record, fallback);
  return '<span class="record-changed-at ' + escapeHtml(className || "") + '">'
    + escapeHtml(changedAt ? "최종 변경 " + notificationClock(changedAt) : "변경일 미확인") + '</span>';
}

export { renderNotificationChangedAt };
