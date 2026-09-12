import { escapeHtml } from "./text.mjs";

function renderSecondaryDisclosure(key, title, content, detail) {
  return '<details class="oa-secondary-details" id="disclosure-' + escapeHtml(key) + '"><summary><strong>' + escapeHtml(title) + '</strong>' + (detail ? '<span>' + escapeHtml(detail) + '</span>' : '') + '</summary><div class="oa-secondary-content">' + content + '</div></details>';
}

export { renderSecondaryDisclosure };
