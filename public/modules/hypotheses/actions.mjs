import { approveHypothesisDevelopment, loadHypothesisDevelopment, processHypothesisDevelopment } from "../experiments/requests.mjs";
import { approveHypothesisLifecyclePolicy, previewHypothesisLifecyclePolicy, restoreHypothesisPolicyVersion } from "./workspace.mjs";
import { render } from "../render/scheduler.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { hypothesesState } from "../state/hypotheses.mjs";

function bindHypothesesControls(app) {
var refreshHypothesisDevelopmentButton = app.querySelector("[data-hypothesis-development-refresh]");
if (refreshHypothesisDevelopmentButton) {
    refreshHypothesisDevelopmentButton.addEventListener("click", function () {
      loadHypothesisDevelopment(true).then(function () {
        if (!hypothesesState.hypothesisDevelopmentError) showSnackbar("가설 자동 검증 상태를 다시 읽었습니다.");
      });
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-hypothesis-development-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var id = String(button.getAttribute("data-hypothesis-development-select") || "");
      if (!id || id === hypothesesState.activeHypothesisDevelopmentCaseId) return;
      hypothesesState.activeHypothesisDevelopmentCaseId = id;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-hypothesis-development-process]")).forEach(function (button) {
    button.addEventListener("click", function () {
      processHypothesisDevelopment(button.getAttribute("data-hypothesis-development-process"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-hypothesis-development-approve]")).forEach(function (button) {
    button.addEventListener("click", function () {
      approveHypothesisDevelopment(button.getAttribute("data-hypothesis-development-approve"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-hypothesis-lifecycle-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var key = String(button.getAttribute("data-hypothesis-lifecycle-select") || "");
      if (!key || key === hypothesesState.activeHypothesisLifecycleKey) return;
      hypothesesState.activeHypothesisLifecycleKey = key;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-hypothesis-policy-form]")).forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      previewHypothesisLifecyclePolicy(form);
    });
    form.addEventListener("input", function () {
      var ruleId = String(form.getAttribute("data-hypothesis-policy-form") || "").trim();
      if (!ruleId || !hypothesesState.hypothesisPolicyPreview || !hypothesesState.hypothesisPolicyPreview[ruleId]) return;
      delete hypothesesState.hypothesisPolicyPreview[ruleId];
      var approveButton = form.querySelector("[data-hypothesis-policy-approve]");
      if (approveButton) approveButton.disabled = true;
      var previewNotice = form.querySelector(".hypothesis-policy-preview");
      if (previewNotice) previewNotice.remove();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-hypothesis-policy-approve]")).forEach(function (button) {
    button.addEventListener("click", function () {
      approveHypothesisLifecyclePolicy(button.getAttribute("data-hypothesis-policy-approve"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-hypothesis-policy-restore]")).forEach(function (button) {
    button.addEventListener("click", function () {
      restoreHypothesisPolicyVersion(button.getAttribute("data-hypothesis-policy-restore"));
    });
  });
}

export { bindHypothesesControls };
