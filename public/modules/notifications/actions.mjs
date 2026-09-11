import { primeActiveTabData } from "../navigation/preload.mjs";
import { normalizeNotificationSection, sectionModeForPage, writeNotificationSectionHistory } from "../navigation/routes.mjs";
import { notificationJobByKey } from "./history.mjs";
import { renderNotificationTemplatePreviewText, updateNotificationRuleBypassCondition, updateNotificationRuleCondition, updateNotificationRuleField, updateNotificationRuleMarket, updateNotificationTemplate } from "./policy.mjs";
import { loadNotificationJobs, resetNotificationJobsPaging, updateNotificationReceipt } from "./requests.mjs";
import { notificationGroupExpanded, notificationRuleByKey, notificationTypeExpanded } from "./workspace.mjs";
import { render } from "../render/scheduler.mjs";
import { updateBooleanAssignmentSetting, updateNumberAssignmentSetting } from "../settings/formulas.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";

function bindNotificationsControls(app) {
Array.prototype.slice.call(app.querySelectorAll("[data-monitor-alert-detail]")).forEach(function (row) {
    var openAlertDetail = function () {
      var index = Number(row.getAttribute("data-monitor-alert-detail"));
      if (!Number.isFinite(index)) return;
      navigationState.monitoringDetail = { type: "alert", index: index };
      render();
    };
    row.addEventListener("click", openAlertDetail);
    row.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openAlertDetail();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-alert-rule]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateBooleanAssignmentSetting("alertRules", field.getAttribute("data-alert-rule"), field.checked);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-section]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var section = normalizeNotificationSection(button.getAttribute("data-notification-section"));
      if (section === notificationsState.activeNotificationSection) return;
      notificationsState.activeNotificationSection = section;
      navigationState.pageViewModes.notifications = sectionModeForPage("notifications", section);
      notificationsState.notificationPolicyEditorOpen = false;
      notificationsState.notificationTemplateEditorOpen = false;
      writeNotificationSectionHistory(section);
      primeActiveTabData("notifications");
      render({ transition: "section" });
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-message-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var messageType = button.getAttribute("data-message-select") || "";
      if (!notificationRuleByKey(messageType)) return;
      notificationsState.activeNotificationMessageType = messageType;
      notificationsState.notificationPolicyEditorOpen = true;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-editor-close]")).forEach(function (button) {
    button.addEventListener("click", function (event) {
      if (button.classList && button.classList.contains("notification-policy-modal-backdrop") && event.target !== button) return;
      notificationsState.notificationPolicyEditorOpen = false;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-template-editor-close]")).forEach(function (button) {
    button.addEventListener("click", function (event) {
      if (button.classList && button.classList.contains("notification-template-modal-backdrop") && event.target !== button) return;
      notificationsState.notificationTemplateEditorOpen = false;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-message-toggle]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var messageType = button.getAttribute("data-message-toggle");
      notificationsState.notificationExpandedTypes[messageType] = !notificationTypeExpanded(messageType);
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-message-group-toggle]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var group = button.getAttribute("data-message-group-toggle") || "";
      if (!group) return;
      notificationsState.notificationExpandedGroups[group] = !notificationGroupExpanded(group);
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-alert-threshold]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNumberAssignmentSetting("alertThresholds", field.getAttribute("data-alert-threshold"), field.value);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-alert-cadence]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNumberAssignmentSetting("alertCadenceMinutes", field.getAttribute("data-alert-cadence"), field.value);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-template]")).forEach(function (field) {
    field.addEventListener("input", function () {
      var messageType = field.getAttribute("data-notification-template");
      updateNotificationTemplate(messageType, field.value);
      var row = field.closest ? field.closest(".notification-template-row") : null;
      var preview = row ? row.querySelector("[data-template-preview]") : null;
      if (preview) {
        preview.textContent = renderNotificationTemplatePreviewText(field.value, messageType);
      }
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-enabled]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleField(field.getAttribute("data-notification-rule-enabled"), "enabled", field.checked);
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-number]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleField(
        field.getAttribute("data-notification-rule-number"),
        field.getAttribute("data-rule-field"),
        field.value
      );
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-similarity-enabled]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleField(field.getAttribute("data-notification-rule-similarity-enabled"), "similarityEnabled", field.checked);
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-state-enabled]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleField(field.getAttribute("data-notification-rule-state-enabled"), "stateCooldownEnabled", field.checked);
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-market-hours-enabled]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleField(field.getAttribute("data-notification-rule-market-hours-enabled"), "marketHoursEnabled", field.checked);
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-market-hours-market]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleMarket(
        field.getAttribute("data-notification-rule-market-hours-market"),
        field.getAttribute("data-market"),
        field.checked
      );
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-off-hours-mode]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleField(field.getAttribute("data-notification-rule-off-hours-mode"), "offHoursDeliveryMode", field.value);
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-fields]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleField(field.getAttribute("data-notification-rule-fields"), "similarityFields", field.value);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-bypass-enabled]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleBypassCondition(
        field.getAttribute("data-notification-rule-bypass-enabled"),
        field.getAttribute("data-condition-id"),
        "enabled",
        field.checked
      );
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-bypass-field]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleBypassCondition(
        field.getAttribute("data-notification-rule-bypass-field"),
        field.getAttribute("data-condition-id"),
        "field",
        field.value
      );
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-bypass-value]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleBypassCondition(
        field.getAttribute("data-notification-rule-bypass-value"),
        field.getAttribute("data-condition-id"),
        "value",
        field.value
      );
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-condition-enabled]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleCondition(
        field.getAttribute("data-notification-rule-condition-enabled"),
        field.getAttribute("data-condition-id"),
        "enabled",
        field.checked
      );
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-condition-field]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleCondition(
        field.getAttribute("data-notification-rule-condition-field"),
        field.getAttribute("data-condition-id"),
        "field",
        field.value
      );
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-condition-value]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNotificationRuleCondition(
        field.getAttribute("data-notification-rule-condition-value"),
        field.getAttribute("data-condition-id"),
        "value",
        field.value
      );
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-job-search]")).forEach(function (field) {
    field.addEventListener("input", function () {
      notificationsState.notificationJobSearch = field.value;
    });
    field.addEventListener("change", function () {
      notificationsState.notificationJobSearch = field.value;
      resetNotificationJobsPaging();
      loadNotificationJobs();
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-search-form]")).forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var field = form.querySelector("[data-notification-job-search]");
      notificationsState.notificationJobSearch = field ? field.value : notificationsState.notificationJobSearch;
      resetNotificationJobsPaging();
      loadNotificationJobs();
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-job-filter]")).forEach(function (field) {
    field.addEventListener("change", function () {
      var filter = field.getAttribute("data-notification-job-filter");
      if (filter === "status") notificationsState.notificationJobStatusFilter = field.value || "all";
      if (filter === "messageType") notificationsState.notificationJobTypeFilter = field.value || "all";
      if (filter === "inbox") notificationsState.notificationInboxFilter = field.value || "all";
      resetNotificationJobsPaging();
      loadNotificationJobs();
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-full-toggle]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var key = button.getAttribute("data-notification-full-toggle") || "";
      if (!key) return;
      notificationsState.notificationExpandedJobs[key] = !notificationsState.notificationExpandedJobs[key];
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-notification-job-select]")).forEach(function (row) {
    var selectNotificationJob = function () {
      var key = row.getAttribute("data-notification-job-select") || "";
      if (!key) return;
      notificationsState.activeNotificationJobKey = key;
      var selectedJob = notificationJobByKey(key);
      if (selectedJob && !selectedJob.readAt) updateNotificationReceipt(key, { read: true });
      render();
    };
    row.addEventListener("click", function (event) {
      var target = event.target;
      while (target && target !== row) {
        if (/^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(target.tagName || "")) return;
        target = target.parentNode;
      }
      selectNotificationJob();
    });
    row.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      selectNotificationJob();
    });
  });
}

export { bindNotificationsControls };
