import { renderInvestmentCalendarPage } from "../calendar/workspace.mjs";
import { renderDecisionConsole } from "../decisions/workspace.mjs";
import { renderValidationConsole } from "../experiments/validation.mjs";
import { renderMarketConsole } from "../market/workspace.mjs";
import { activePageMode } from "../navigation/routes.mjs";
import { renderAlertsConsole } from "../notifications/inbox.mjs";
import { renderOperationsHealthConsole } from "../operations/workspace.mjs";
import { renderTodayConsole } from "../overview/workspace.mjs";
import { renderPortfolioConsole } from "../portfolio/workspace.mjs";
import { renderSettingsConsole } from "../settings/workspace.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { pageStructureMeta, webStyleContract } from "./catalog.mjs";
import { renderPageCommandStrip, renderPageRoutinePanel, renderSingleScreenFlowPanel } from "./commands.mjs";
import { navigationState } from "../state/navigation.mjs";

function renderActiveTab(snapshot) {
  if (navigationState.activeTab === "overview") return renderTodayConsole(snapshot);
  if (navigationState.activeTab === "portfolio") return renderPortfolioConsole();
  if (navigationState.activeTab === "calendar") return renderInvestmentCalendarPage(snapshot);
  if (navigationState.activeTab === "feed") return renderMarketConsole(snapshot);
  if (navigationState.activeTab === "modeling") return renderDecisionConsole(snapshot);
  if (navigationState.activeTab === "notifications") return renderAlertsConsole();
  if (navigationState.activeTab === "experiments") return renderValidationConsole(snapshot);
  if (navigationState.activeTab === "settings") return renderSettingsConsole(snapshot);
  if (navigationState.activeTab === "operations") return renderOperationsHealthConsole();
  return renderTodayConsole(snapshot);
}

function renderManagedPage(pageId, snapshot, content) {
  var structure = pageStructureMeta(pageId || "overview");
  var mode = activePageMode(pageId || "overview");
  return [
    '<div class="managed-page managed-page-' + escapeHtml(pageId || "overview") + ' ' + escapeHtml(webStyleContract.pageClass) + ' web-style-screen-' + escapeHtml(pageId || "overview") + '" data-style-contract="' + escapeHtml(webStyleContract.id) + '" data-style-screen="' + escapeHtml(pageId || "overview") + '" data-page-mode="' + escapeHtml(mode) + '" data-structure-group="' + escapeHtml(structure.groupId) + '" data-structure-layer="' + escapeHtml(structure.layer) + '" data-structure-entity="' + escapeHtml(structure.entity) + '">',
    renderPageCommandStrip(pageId, snapshot),
    renderPageRoutinePanel(pageId, snapshot),
    renderSingleScreenFlowPanel(pageId, snapshot),
    content,
    '</div>'
  ].join("");
}

export { renderActiveTab, renderManagedPage };
