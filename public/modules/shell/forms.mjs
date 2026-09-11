import { app } from "./root.mjs";
import { notificationsState } from "../state/notifications.mjs";

function textareaMinimumHeight(field) {
  var rows = Math.max(0, Number(field && field.getAttribute ? field.getAttribute("rows") : 0) || 0);
  if (!rows) return 96;
  var computed = typeof window !== "undefined" && window.getComputedStyle ? window.getComputedStyle(field) : null;
  var lineHeight = computed ? parseFloat(computed.lineHeight) : 0;
  var padding = computed
    ? (parseFloat(computed.paddingTop) || 0)
      + (parseFloat(computed.paddingBottom) || 0)
      + (parseFloat(computed.borderTopWidth) || 0)
      + (parseFloat(computed.borderBottomWidth) || 0)
    : 0;
  return Math.max(96, Math.ceil(rows * (lineHeight || 20) + padding));
}

function resizeTextareaToContent(field) {
  if (!field || field.tagName !== "TEXTAREA") return;
  var minimum = Math.max(field.offsetHeight || 0, textareaMinimumHeight(field));
  field.style.height = "auto";
  field.style.height = Math.max(field.scrollHeight || 0, minimum) + "px";
}

function resizeTextareasIn(root) {
  Array.prototype.slice.call((root || app).querySelectorAll("textarea")).forEach(resizeTextareaToContent);
}

function bindAutoGrowingTextareas(root) {
  var target = root || app;
  Array.prototype.slice.call(target.querySelectorAll("textarea")).forEach(function (field) {
    resizeTextareaToContent(field);
    field.addEventListener("input", function () {
      resizeTextareaToContent(field);
    });
  });
  Array.prototype.slice.call(target.querySelectorAll("details")).forEach(function (details) {
    details.addEventListener("toggle", function () {
      var disclosureKey = details.getAttribute("data-notification-detail-disclosure-key") || "";
      if (disclosureKey) {
        notificationsState.notificationDetailDisclosureOpen[disclosureKey] = Boolean(details.open);
      }
      if (typeof window !== "undefined" && window.requestAnimationFrame) {
        window.requestAnimationFrame(function () { resizeTextareasIn(details); });
      } else {
        resizeTextareasIn(details);
      }
    });
  });
}

export { bindAutoGrowingTextareas };
