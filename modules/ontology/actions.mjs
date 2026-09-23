import { primeActiveTabData } from "../navigation/preload.mjs";
import { normalizeOntologyGraphId, normalizeOntologySection, normalizeOntologyWorldDepth, normalizeOntologyWorldLens, normalizeStrategySection, writeStrategySectionHistory } from "../navigation/routes.mjs";
import { fitOntologyGraph, layoutOntologyGraph, ontologyAboxSymbolFromId, ontologyDecisionChainRows } from "./graphs.mjs";
import { loadOntologyAudit, loadOntologyCatalogLineage, loadOntologyCatalogSection, loadOntologyDiagnostics, loadOntologyInferenceLedger, ontologyCatalogFilter } from "./requests.mjs";
import { ontologyStrategyParts } from "./strategy.mjs";
import { render } from "../render/scheduler.mjs";
import { accountsState } from "../state/accounts.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { navigationState } from "../state/navigation.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { shellState } from "../state/shell.mjs";

function bindOntologyControls(app) {
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-graph-expand]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var graphId = normalizeOntologyGraphId(button.getAttribute("data-ontology-graph-expand"));
      if (!graphId) return;
      ontologyState.expandedOntologyGraphId = graphId;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-graph-fit]")).forEach(function (button) {
    button.addEventListener("click", function () {
      fitOntologyGraph(button.getAttribute("data-ontology-graph-fit"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-graph-layout]")).forEach(function (button) {
    button.addEventListener("click", function () {
      layoutOntologyGraph(button.getAttribute("data-ontology-graph-layout"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-graph-close]")).forEach(function (button) {
    button.addEventListener("click", function (event) {
      if (button.classList && button.classList.contains("ontology-graph-expanded-backdrop") && event.target !== button) return;
      ontologyState.expandedOntologyGraphId = "";
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-chain-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var key = button.getAttribute("data-ontology-chain-select") || "";
      if (!key) return;
      ontologyState.activeOntologyChainKey = key;
      var chain = ontologyDecisionChainRows(ontologyStrategyParts(shellState.snapshot || {}), shellState.snapshot || {}).filter(function (item) { return item.key === key; })[0];
      if (chain && chain.symbol) {
        ontologyState.activeOntologyWorldFocusId = "stock:" + String(chain.symbol).toUpperCase();
        ontologyState.activeOntologyWorldNodeId = ontologyState.activeOntologyWorldFocusId;
      }
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-world-lens]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var nextLens = normalizeOntologyWorldLens(button.getAttribute("data-ontology-world-lens"));
      if (nextLens === ontologyState.activeOntologyWorldLens) return;
      ontologyState.activeOntologyWorldLens = nextLens;
      ontologyState.activeOntologyWorldNodeId = "";
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-world-focus]")).forEach(function (select) {
    select.addEventListener("change", function () {
      var focusId = String(select.value || "");
      if (!focusId || focusId === ontologyState.activeOntologyWorldFocusId) return;
      ontologyState.activeOntologyWorldFocusId = focusId;
      ontologyState.activeOntologyWorldNodeId = focusId;
      var focusSymbol = ontologyAboxSymbolFromId(focusId);
      var focusChain = ontologyDecisionChainRows(ontologyStrategyParts(shellState.snapshot || {}), shellState.snapshot || {}).filter(function (item) {
        return String(item && item.symbol || "").toUpperCase() === focusSymbol;
      })[0];
      if (focusChain) ontologyState.activeOntologyChainKey = focusChain.key;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-world-depth]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var depth = normalizeOntologyWorldDepth(button.getAttribute("data-ontology-world-depth"));
      if (depth === ontologyState.activeOntologyWorldDepth) return;
      ontologyState.activeOntologyWorldDepth = depth;
      ontologyState.activeOntologyWorldNodeId = "";
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-world-chain]")).forEach(function (select) {
    select.addEventListener("change", function () {
      var key = select.value || "";
      if (!key || key === ontologyState.activeOntologyChainKey) return;
      ontologyState.activeOntologyChainKey = key;
      var chain = ontologyDecisionChainRows(ontologyStrategyParts(shellState.snapshot || {}), shellState.snapshot || {}).filter(function (item) { return item.key === key; })[0];
      if (chain && chain.symbol) {
        ontologyState.activeOntologyWorldFocusId = "stock:" + String(chain.symbol).toUpperCase();
        ontologyState.activeOntologyWorldNodeId = ontologyState.activeOntologyWorldFocusId;
      }
      render();
    });
  });
var ontologyAccountSelect = app.querySelector("[data-ontology-account-select]");
if (ontologyAccountSelect) {
    ontologyAccountSelect.addEventListener("change", function () {
      accountsState.activeWatchAccountId = ontologyAccountSelect.value || "";
      ontologyState.ontologyDiagnostics = null;
      ontologyState.ontologyAudit = null;
      ontologyState.ontologyAuditLoaded = false;
      ontologyState.ontologyInferenceLedger = null;
      ontologyState.ontologyInferenceLedgerLoaded = false;
      render();
      Promise.all([
        loadOntologyDiagnostics(true, false),
        loadOntologyInferenceLedger(true),
        loadOntologyAudit(true)
      ]);
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-rulebox-json]")).forEach(function (field) {
    field.addEventListener("input", function () {
      ontologyState.ontologyRuleboxJson = field.value;
      ontologyState.ontologyRuleboxError = "";
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-rulebox-change-reason]")).forEach(function (field) {
    field.addEventListener("input", function () {
      ontologyState.ontologyRuleboxChangeReason = field.value;
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-catalog-tab]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var tabId = button.getAttribute("data-ontology-catalog-tab") || "overview";
      ontologyState.activeOntologyCatalogTab = tabId;
      ontologyState.ontologyCatalogSelection = null;
      ontologyState.ontologyCatalogLineage = null;
      ontologyState.ontologyCatalogLineageError = "";
      render();
      loadOntologyCatalogSection(tabId, false);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-catalog-filter]")).forEach(function (field) {
    var updateCatalogFilter = function () {
      var key = field.getAttribute("data-ontology-catalog-filter") || "";
      if (key) ontologyCatalogFilter(ontologyState.activeOntologyCatalogTab)[key] = field.value;
    };
    field.addEventListener("input", updateCatalogFilter);
    field.addEventListener("change", updateCatalogFilter);
  });
var ontologyCatalogForm = app.querySelector("[data-ontology-catalog-form]");
if (ontologyCatalogForm) {
    ontologyCatalogForm.addEventListener("submit", function (event) {
      event.preventDefault();
      ontologyState.ontologyCatalogSelection = null;
      ontologyState.ontologyCatalogLineage = null;
      loadOntologyCatalogSection(ontologyState.activeOntologyCatalogTab, true, "offset:0");
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-catalog-cursor]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var cursor = button.getAttribute("data-ontology-catalog-cursor") || "";
      if (cursor) loadOntologyCatalogSection(ontologyState.activeOntologyCatalogTab, true, cursor);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-catalog-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      loadOntologyCatalogLineage(
        button.getAttribute("data-ontology-catalog-select"),
        button.getAttribute("data-ontology-catalog-id"),
        button.getAttribute("data-ontology-catalog-symbol")
      );
    });
  });
var ontologyAuditForm = app.querySelector("[data-ontology-audit-form]");
if (ontologyAuditForm) {
    ontologyAuditForm.addEventListener("submit", function (event) {
      event.preventDefault();
      ontologyState.ontologyAuditLoaded = false;
      loadOntologyAudit(true);
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-audit-filter]")).forEach(function (field) {
    var updateOntologyAuditFilter = function () {
      var name = field.getAttribute("data-ontology-audit-filter");
      if (!name) return;
      ontologyState.ontologyAuditFilters[name] = field.value;
    };
    field.addEventListener("input", updateOntologyAuditFilter);
    field.addEventListener("change", updateOntologyAuditFilter);
  });
Array.prototype.slice.call(app.querySelectorAll("[data-ontology-section]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var legacySection = normalizeOntologySection(button.getAttribute("data-ontology-section"));
      var section = normalizeStrategySection(legacySection);
      if (section === decisionsState.activeStrategySection && navigationState.activeTab === "modeling") return;
      navigationState.activeTab = "modeling";
      ontologyState.activeOntologySection = legacySection;
      decisionsState.activeStrategySection = section;
      writeStrategySectionHistory(section);
      primeActiveTabData("modeling");
      render({ transition: "section" });
    });
  });
}

export { bindOntologyControls };
