import { render } from "../render/scheduler.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { shellState } from "../state/shell.mjs";

var snackbarTimer = null;

function showSnackbar(message, tone, action) {
  shellState.snackbar = {
    message: String(message || ""),
    tone: tone || "success",
    action: action && action.label && action.name ? {
      label: String(action.label),
      name: String(action.name)
    } : null
  };
  if (snackbarTimer) clearTimeout(snackbarTimer);
  snackbarTimer = setTimeout(function () {
    shellState.snackbar = null;
    render();
  }, 2600);
  render();
}

function copyTextToClipboard(value) {
  var text = String(value || "");
  if (!text) return Promise.reject(new Error("복사할 링크가 없습니다."));
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise(function (resolve, reject) {
    var input = document.createElement("textarea");
    input.value = text;
    input.setAttribute("readonly", "readonly");
    input.style.position = "fixed";
    input.style.left = "-9999px";
    document.body.appendChild(input);
    input.select();
    try {
      if (!document.execCommand("copy")) throw new Error("copy command failed");
      resolve();
    } catch (error) {
      reject(error);
    } finally {
      document.body.removeChild(input);
    }
  });
}

function renderSnackbar() {
  var snackbar = shellState.snackbar || {};
  var message = String(snackbar.message || "");
  var action = snackbar.action || {};
  var visible = Boolean(message);
  return [
    '<div class="snackbar ' + escapeHtml(snackbar.tone || "success") + (visible ? "" : " is-empty") + '" role="status" aria-hidden="' + (visible ? "false" : "true") + '">',
    '<span>' + escapeHtml(message) + '</span>',
    '<button type="button" data-snackbar-action="' + escapeHtml(action.name || "") + '"' + (action.name && action.label ? '' : ' hidden disabled aria-hidden="true"') + '>' + escapeHtml(action.label || "확인") + '</button>',
    '</div>'
  ].join("");
}

export { copyTextToClipboard, renderSnackbar, showSnackbar };
