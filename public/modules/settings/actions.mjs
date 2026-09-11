import { normalizeSettingsSection, writeSettingsSectionHistory } from "../navigation/routes.mjs";
import { rememberRenderedPageScrollPosition } from "../navigation/scroll.mjs";
import { resetNotificationTemplate, saveNotificationTemplate, sendNotificationTemplateTest } from "../notifications/policy.mjs";
import { notificationTemplateItems } from "../notifications/workspace.mjs";
import { render } from "../render/scheduler.mjs";
import { refreshSettingsSaveControls } from "./fields.mjs";
import { updateNumberAssignmentSetting } from "./formulas.mjs";
import { applyAppTheme, currentAppTimezone } from "./preferences.mjs";
import { persistSettings } from "./storage.mjs";
import { calendarState } from "../state/calendar.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { settingsState } from "../state/settings.mjs";

function bindSettingsControls(app) {
var settingsRuntimeToggle = app.querySelector("[data-settings-runtime-toggle]");
if (settingsRuntimeToggle) {
    settingsRuntimeToggle.addEventListener("click", function () {
      settingsState.settingsRuntimeExpanded = !settingsState.settingsRuntimeExpanded;
      render();
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-model-setting]")).forEach(function (field) {
    field.addEventListener("input", function () {
      var name = field.getAttribute("data-model-setting");
      if (!name) return;
      settingsState.settings[name] = field.value;
      persistSettings();
      settingsState.settingsSaved = false;
      refreshSettingsSaveControls();
    });
    field.addEventListener("change", function () {
      persistSettings();
      settingsState.settingsSaved = false;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-number-setting]")).forEach(function (field) {
    field.addEventListener("change", function () {
      updateNumberAssignmentSetting(
        field.getAttribute("data-number-setting"),
        field.getAttribute("data-number-key"),
        field.value
      );
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-settings-section]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var section = normalizeSettingsSection(button.getAttribute("data-settings-section"));
      if (section === settingsState.activeSettingsSection) return;
      rememberRenderedPageScrollPosition();
      settingsState.activeSettingsSection = section;
      writeSettingsSectionHistory(section);
      render({ transition: "section" });
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-template-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var messageType = button.getAttribute("data-template-select") || "";
      if (!notificationTemplateItems().some(function (item) { return item.messageType === messageType; })) return;
      notificationsState.activeNotificationTemplateType = messageType;
      notificationsState.notificationTemplateEditorOpen = true;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-template-save]")).forEach(function (button) {
    button.addEventListener("click", function () {
      saveNotificationTemplate(button.getAttribute("data-template-save"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-template-reset]")).forEach(function (button) {
    button.addEventListener("click", function () {
      resetNotificationTemplate(button.getAttribute("data-template-reset"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-template-test-send]")).forEach(function (button) {
    button.addEventListener("click", function () {
      sendNotificationTemplateTest(button.getAttribute("data-template-test-send"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-setting]")).forEach(function (field) {
    var updateSettingField = function () {
      var name = field.getAttribute("data-setting");
      if (!name) return;
      settingsState.settings[name] = field.value;
      settingsState.settingsSaved = false;
      if (name === "appTheme") applyAppTheme();
      if (name === "appTimezone" && calendarState.investmentCalendarDraft) {
        calendarState.investmentCalendarDraft.timezone = currentAppTimezone();
      }
      refreshSettingsSaveControls();
    };
    field.addEventListener("input", updateSettingField);
    field.addEventListener("change", updateSettingField);
  });
}

export { bindSettingsControls };
