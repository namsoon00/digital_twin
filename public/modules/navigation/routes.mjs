import { ontologyWorldLensDefinition } from "../ontology/world.mjs";
import { accountSections, experimentSections, feedSections, marketWorkspaceModes, notificationSections, ontologySections, pageModeEnabledTabs, pageModeSectionMap, settingsSections, strategySections, tabs } from "../shell/catalog.mjs";
import { accountsState } from "../state/accounts.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { experimentsState } from "../state/experiments.mjs";
import { marketState } from "../state/market.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { settingsState } from "../state/settings.mjs";

function investmentCaseDetailRequiresKey(type) {
  return ["investment-case", "investment-flow"].indexOf(String(type || "").trim()) >= 0;
}

function initialTab() {
  var params = new URLSearchParams(window.location.search);
  var detailType = String(params.get("detail") || "").trim();
  var detailKey = String(params.get("detailKey") || "").trim();
  if (!params.get("tab") && investmentCaseDetailRequiresKey(detailType) && !detailKey) {
    return "modeling";
  }
  if (String(params.get("tab") || "").toLowerCase() === "settings"
      && ["operations", "operation", "runtime", "system", "data", "diagnostics"].indexOf(String(params.get("settings") || "").toLowerCase()) >= 0) {
    return "operations";
  }
  return normalizeTabId(params.get("tab"));
}

function normalizePortfolioView(value) {
  var requested = String(value || "").toLowerCase();
  return ["summary", "positions", "rebalance", "activity"].indexOf(requested) >= 0 ? requested : "summary";
}

function initialPortfolioView() {
  var params = new URLSearchParams(window.location.search);
  return normalizePortfolioView(params.get("portfolioView") || params.get("portfolio"));
}

function normalizeOperationsView(value) {
  var requested = String(value || "").toLowerCase();
  return ["health", "data", "reasoning", "delivery", "governance"].indexOf(requested) >= 0 ? requested : "health";
}

function initialOperationsView() {
  var params = new URLSearchParams(window.location.search);
  return normalizeOperationsView(params.get("operationsView") || params.get("operations"));
}

function initialWorkDetailLayer() {
  var params = new URLSearchParams(window.location.search);
  var type = String(params.get("detail") || "").trim();
  if (!type) return null;
  var key = String(params.get("detailKey") || "").trim();
  if (type === "investment-action" && key.indexOf("decision:") === 0) type = "investment-case";
  if (investmentCaseDetailRequiresKey(type) && !key) return null;
  return {
    type: type,
    key: key
  };
}

function initialNotificationSection() {
  var params = new URLSearchParams(window.location.search);
  if (String(params.get("tab") || "").toLowerCase() === "monitoring" && !params.get("notification")) {
    return "candidates";
  }
  return normalizeNotificationSection(params.get("notification"));
}

function initialAccountSection() {
  var params = new URLSearchParams(window.location.search);
  return normalizeAccountSection(params.get("account"));
}

function initialSettingsSection() {
  var params = new URLSearchParams(window.location.search);
  var rawTab = String(params.get("tab") || "").toLowerCase();
  if (rawTab === "accounts" && !params.get("settings")) return "account";
  return normalizeSettingsSection(params.get("settings"));
}

function initialStrategySection() {
  var params = new URLSearchParams(window.location.search);
  var requested = params.get("strategy");
  if (!requested && String(params.get("tab") || "").toLowerCase() === "ontology") {
    requested = params.get("ontology");
  }
  return normalizeStrategySection(requested);
}

function initialStrategyProposalSection() {
  var params = new URLSearchParams(window.location.search);
  return normalizeStrategyProposalSection(params.get("proposalView") || params.get("proposalSection"));
}

function initialInvestmentGraphLayer() {
  var params = new URLSearchParams(window.location.search);
  return normalizeInvestmentGraphLayer(params.get("graphLayer") || params.get("ontologyLayer"));
}

function initialOntologyWorldLens() {
  var params = new URLSearchParams(window.location.search);
  return normalizeOntologyWorldLens(params.get("ontologyLens") || params.get("worldLens"));
}

function initialOntologyWorldFocusId() {
  var params = new URLSearchParams(window.location.search);
  var requested = String(params.get("ontologyFocus") || params.get("worldFocus") || "").trim();
  if (!requested) return "";
  return requested.indexOf("stock:") === 0 ? requested : "stock:" + requested.toUpperCase();
}

function initialOntologyWorldDepth() {
  var params = new URLSearchParams(window.location.search);
  return normalizeOntologyWorldDepth(params.get("ontologyDepth") || params.get("worldDepth"));
}

function initialOntologySection() {
  var params = new URLSearchParams(window.location.search);
  return normalizeOntologySection(params.get("ontology"));
}

function initialFeedSection() {
  var params = new URLSearchParams(window.location.search);
  if (!params.get("feed") && normalizeTabId(params.get("tab")) === "feed" && normalizePageMode(params.get("mode")) === "settings") {
    return "settings";
  }
  return normalizeFeedSection(params.get("feed"));
}

function normalizeMarketWorkspaceMode(value) {
  var requested = String(value || "").toLowerCase();
  if (["symbols", "all", "catalog", "catalogue"].indexOf(requested) >= 0) return "universe";
  if (["capital", "money-flow", "money", "themes"].indexOf(requested) >= 0) return "flow";
  if (["impact", "feed", "news"].indexOf(requested) >= 0) return "news";
  return marketWorkspaceModes.some(function (mode) { return mode.id === requested; }) ? requested : "mine";
}

function initialMarketWorkspaceMode() {
  var params = new URLSearchParams(window.location.search);
  var rawTab = String(params.get("tab") || "").toLowerCase();
  if (rawTab === "symbols") return "universe";
  if (rawTab === "watchlist") return "mine";
  return normalizeMarketWorkspaceMode(params.get("marketView") || params.get("feed"));
}

function initialExperimentSection() {
  var params = new URLSearchParams(window.location.search);
  return normalizeExperimentSection(params.get("lab") || params.get("experimentSection"));
}

function initialOntologyExperimentId() {
  var params = new URLSearchParams(window.location.search);
  return String(params.get("experimentId") || params.get("ontologyExperimentId") || "").trim();
}

function normalizePageMode(value) {
  return String(value || "").toLowerCase() === "settings" ? "settings" : "results";
}

function pageSupportsMode(pageId) {
  return pageModeEnabledTabs.indexOf(normalizeTabId(pageId)) >= 0;
}

function sectionModeForPage(pageId, sectionId) {
  var config = pageModeSectionMap[normalizeTabId(pageId)];
  var section = String(sectionId || "").toLowerCase();
  if (!config) return "results";
  if ((config.settings || []).indexOf(section) >= 0) return "settings";
  return "results";
}

function initialPageModeForTab(pageId) {
  var params = new URLSearchParams(window.location.search);
  var explicit = params.get("mode");
  var normalized = normalizeTabId(pageId || params.get("tab"));
  if (explicit) return normalizePageMode(explicit);
  if (normalized === "accounts") return sectionModeForPage("accounts", initialAccountSection());
  if (normalized === "notifications") return sectionModeForPage("notifications", initialNotificationSection());
  if (normalized === "modeling") return sectionModeForPage("modeling", initialStrategySection());
  if (normalized === "feed") return sectionModeForPage("feed", initialFeedSection());
  if (normalized === "settings") return "settings";
  return "results";
}

function initialPageViewModes() {
  var mode = initialPageModeForTab();
  var tab = initialTab();
  return {
    overview: "results",
    accounts: tab === "accounts" ? mode : "results",
    watchlist: "results",
    calendar: "results",
    symbols: "results",
    notifications: tab === "notifications" ? mode : "results",
    modeling: tab === "modeling" ? mode : "results",
    experiments: "results",
    feed: tab === "feed" ? mode : "results",
    system: "results",
    settings: "settings"
  };
}

function activePageMode(pageId) {
  var normalized = normalizeTabId(pageId || navigationState.activeTab);
  if (!pageSupportsMode(normalized)) return normalized === "settings" ? "settings" : "results";
  return normalizePageMode((navigationState.pageViewModes || {})[normalized]);
}

function modeSectionsForPage(pageId, sections) {
  var normalized = normalizeTabId(pageId);
  var config = pageModeSectionMap[normalized];
  var mode = activePageMode(normalized);
  if (!config) return sections || [];
  var allowed = config[mode] || [];
  return (sections || []).filter(function (section) {
    return allowed.indexOf(section.id) >= 0;
  });
}

function activeSectionForPageMode(pageId, sections, current) {
  var visible = modeSectionsForPage(pageId, sections);
  var currentId = String(current || "").toLowerCase();
  if (visible.some(function (section) { return section.id === currentId; })) return currentId;
  return visible.length ? visible[0].id : currentId;
}

function firstSectionForPageMode(pageId, mode) {
  var config = pageModeSectionMap[normalizeTabId(pageId)] || {};
  var ids = config[normalizePageMode(mode)] || [];
  return ids[0] || "";
}

function setPageViewMode(pageId, mode) {
  var normalized = normalizeTabId(pageId || navigationState.activeTab);
  if (!navigationState.pageViewModes) navigationState.pageViewModes = {};
  navigationState.pageViewModes[normalized] = normalizePageMode(mode);
  var firstSection = firstSectionForPageMode(normalized, mode);
  if (normalized === "accounts" && firstSection) accountsState.activeAccountSection = firstSection;
  if (normalized === "notifications" && firstSection) {
    notificationsState.activeNotificationSection = firstSection;
    notificationsState.notificationPolicyEditorOpen = false;
    notificationsState.notificationTemplateEditorOpen = false;
  }
  if (normalized === "modeling" && firstSection) decisionsState.activeStrategySection = firstSection;
  if (normalized === "feed" && firstSection) marketState.activeFeedSection = normalizeFeedSection(firstSection);
}

function normalizeTabId(value) {
  var requested = String(value || "").toLowerCase();
  var aliases = {
    more: "overview",
    today: "overview",
    holdings: "portfolio",
    portfolio: "portfolio",
    rebalance: "portfolio",
    market: "feed",
    watchlist: "feed",
    symbols: "feed",
    decision: "modeling",
    ontology: "modeling",
    relations: "modeling",
    alerts: "notifications",
    monitoring: "notifications",
    validation: "experiments",
    lab: "experiments",
    experiment: "experiments",
    system: "operations",
    operations: "operations",
    accounts: "settings"
  };
  requested = aliases[requested] || requested;
  return tabs.some(function (tab) { return tab.id === requested; }) ? requested : "overview";
}

function normalizeNotificationSection(value) {
  var requested = String(value || "").toLowerCase();
  if (requested === "signals" || requested === "signal" || requested === "candidate") return "candidates";
  if (requested === "advanced" || requested === "diagnostic") return "diagnostics";
  return notificationSections.some(function (section) { return section.id === requested; }) ? requested : "status";
}

function normalizeAccountSection(value) {
  var requested = String(value || "").toLowerCase();
  if (requested === "overview" || requested === "summary" || requested === "health") return "status";
  if (requested === "api" || requested === "source" || requested === "sources") return "connections";
  if (requested === "money" || requested === "portfolio" || requested === "audit" || requested === "assets") return "balance";
  if (requested === "settings" || requested === "form" || requested === "accounts" || requested === "management") return "identity";
  if (requested === "freshness" || requested === "logs" || requested === "sync") return "history";
  return accountSections.some(function (section) { return section.id === requested; }) ? requested : "status";
}

function normalizeSettingsSection(value) {
  var requested = String(value || "").toLowerCase();
  if (["accounts", "identity", "connections", "brokerage"].indexOf(requested) >= 0) return "account";
  if (["personal", "environment", "display", "app"].indexOf(requested) >= 0) return "preferences";
  return settingsSections.some(function (section) { return section.id === requested; }) ? requested : "account";
}

function normalizeStrategySection(value) {
  var requested = String(value || "").toLowerCase();
  if (requested === "data" || requested === "cards" || requested === "card") return "evidence";
  if (requested === "result" || requested === "results" || requested === "actions" || requested === "queue") return "overview";
  if (requested === "chart" || requested === "candle" || requested === "candles" || requested === "flow" || requested === "money-flow") return "charts";
  if (requested === "policy" || requested === "rule" || requested === "rules" || requested === "registry" || requested === "prompts") return "rules";
  if (requested === "structure" || requested === "graph" || requested === "ontology" || requested === "relation-graph") return "graphs";
  if (requested === "proposal" || requested === "proposals" || requested === "strategy-proposals" || requested === "approval" || requested === "approvals") return "proposals";
  if (requested === "hypothesis" || requested === "hypotheses" || requested === "lifecycle" || requested === "lifecycles" || requested === "hypothesis-review") return "hypotheses";
  if (requested === "relations" || requested === "rules-trace" || requested === "review" || requested === "reviews") return "trace";
  return strategySections.some(function (section) { return section.id === requested; }) ? requested : "overview";
}

function normalizeStrategyProposalSection(value) {
  var requested = String(value || "").toLowerCase();
  if (requested === "condition" || requested === "conditions" || requested === "rule" || requested === "rules") return "conditions";
  if (requested === "validate" || requested === "validation" || requested === "typedb" || requested === "materialization") return "validation";
  if (requested === "perform" || requested === "performance" || requested === "result" || requested === "results") return "performance";
  if (requested === "history" || requested === "log" || requested === "review" || requested === "reviews") return "history";
  return "summary";
}

function normalizeInvestmentGraphLayer(value) {
  var requested = String(value || "").toLowerCase();
  if (requested === "rule" || requested === "rules" || requested === "rulebox") return "rulebox";
  if (requested === "infer" || requested === "inference" || requested === "inferencebox") return "inference";
  if (requested === "abox" || requested === "data" || requested === "facts") return "abox";
  return requested === "tbox" || requested === "schema" || requested === "vocabulary" ? "tbox" : "inference";
}

function normalizeOntologyWorldLens(value) {
  var requested = String(value || "").toLowerCase();
  if (requested === "account" || requested === "holding" || requested === "holdings") return "portfolio";
  if (requested === "quality" || requested === "warning" || requested === "anomaly") return "risk";
  if (requested === "reasoning" || requested === "opinion") return "inference";
  if (requested === "source" || requested === "sources" || requested === "provenance") return "evidence";
  return ["reality", "portfolio", "risk", "inference", "evidence"].indexOf(requested) >= 0 ? requested : "reality";
}

function normalizeOntologyWorldDepth(value) {
  return Number(value) === 1 ? 1 : 2;
}

function normalizeOntologySection(value) {
  var requested = String(value || "").toLowerCase();
  if (requested === "graph") return "graphs";
  if (requested === "rules" || requested === "prompts") return "registry";
  if (requested === "relations" || requested === "rules-trace") return "trace";
  return ontologySections.some(function (section) { return section.id === requested; }) ? requested : "overview";
}

function normalizeFeedSection(value) {
  var requested = String(value || "").toLowerCase();
  if (requested === "status" || requested === "operations" || requested === "monitoring" || requested === "pipeline" || requested === "summary") return "overview";
  if (requested === "db" || requested === "research" || requested === "research-evidence" || requested === "evidences" || requested === "evidence" || requested === "inbox" || requested === "news") return "impact";
  if (requested === "theme" || requested === "themes" || requested === "sector" || requested === "sectors" || requested === "asset" || requested === "assets") return "themes";
  if (requested === "portfolio" || requested === "holdings" || requested === "holding" || requested === "watchlist" || requested === "mine" || requested === "symbols") return "portfolio";
  if (requested === "source" || requested === "channel" || requested === "channels" || requested === "provider" || requested === "providers") return "sources";
  if (requested === "config" || requested === "policy" || requested === "policies" || requested === "setting") return "settings";
  return feedSections.some(function (section) { return section.id === requested; }) ? requested : "overview";
}

function normalizeExperimentSection(value) {
  var requested = String(value || "").toLowerCase();
  if (requested === "status" || requested === "pipeline" || requested === "dashboard" || requested === "summary") return "overview";
  if (requested === "validate" || requested === "backtest" || requested === "replay" || requested === "comparison" || requested === "compare") return "validation";
  if (requested === "apply" || requested === "approval" || requested === "approve" || requested === "promote") return "promotion";
  if (requested === "history" || requested === "log" || requested === "logs" || requested === "trail") return "audit";
  if (requested === "proposal" || requested === "strategy-proposals" || requested === "strategy") return "proposals";
  return experimentSections.some(function (section) { return section.id === requested; }) ? requested : "overview";
}

function normalizeOntologyGraphId(value) {
  var requested = String(value || "").toLowerCase().replace("-expanded", "");
  if (requested === "chain" || requested === "decision" || requested === "decision-chain") return "decision-chain";
  if (requested === "world" || requested === "reality") return "world";
  return requested === "tbox" || requested === "abox" ? requested : "";
}

function ontologyGraphDisplayMeta(graphId) {
  var normalized = normalizeOntologyGraphId(graphId);
  if (normalized === "world") {
    var lens = ontologyWorldLensDefinition(ontologyState.activeOntologyWorldLens);
    return {
      title: lens.title,
      eyebrow: "Decision Topology",
      description: lens.description,
      fitLabel: "의사결정 지도 맞춤",
      layoutLabel: "의미 계층 배치 초기화"
    };
  }
  if (normalized === "decision-chain") {
    return {
      title: "판단 근거 체인 그래프",
      eyebrow: "Decision Evidence Chain",
      description: "데이터, 관계, RuleBox, InferenceBox, 투자 판단, 알림, 성과가 한 종목 판단으로 이어지는 경로입니다.",
      fitLabel: "판단 근거 체인 맞춤",
      layoutLabel: "판단 근거 체인 자동 배치"
    };
  }
  if (normalized === "abox") {
    return {
      title: "핵심 데이터 관계 그래프",
      eyebrow: "Data Relation Graph",
      description: "실제 데이터 중 AI 판단, 중요 변경, 알림 후보와 연결되는 관계를 큰 화면으로 확인합니다.",
      fitLabel: "데이터 관계 그래프 맞춤",
      layoutLabel: "데이터 관계 자동 배치"
    };
  }
  return {
    title: "전체 규칙 구조 그래프",
    eyebrow: "Rule Structure Graph",
    description: "TBox 분류, 관계 타입, 규칙 연결을 접지 않고 큰 화면으로 확인합니다.",
    fitLabel: "규칙 구조 그래프 맞춤",
    layoutLabel: "규칙 구조 자동 배치"
  };
}

function activeNotificationSectionMeta() {
  return notificationSections.filter(function (section) {
    return section.id === notificationsState.activeNotificationSection;
  })[0] || notificationSections[0];
}

function activeAccountSectionMeta() {
  return accountSections.filter(function (section) {
    return section.id === accountsState.activeAccountSection;
  })[0] || accountSections[0];
}

function activeSettingsSectionMeta() {
  return settingsSections.filter(function (section) {
    return section.id === settingsState.activeSettingsSection;
  })[0] || settingsSections[0];
}

function activeStrategySectionMeta() {
  return strategySections.filter(function (section) {
    return section.id === decisionsState.activeStrategySection;
  })[0] || strategySections[0];
}

function activeFeedSectionMeta() {
  return feedSections.filter(function (section) {
    return section.id === marketState.activeFeedSection;
  })[0] || feedSections[0];
}

function activeExperimentSectionMeta() {
  return experimentSections.filter(function (section) {
    return section.id === experimentsState.activeExperimentSection;
  })[0] || experimentSections[0];
}

function tabUrl(tab) {
  var normalized = normalizeTabId(tab);
  var params = new URLSearchParams(window.location.search);
  if (normalized !== "accounts") params.delete("account");
  if (normalized !== "notifications") params.delete("notification");
  if (normalized !== "modeling") params.delete("strategy");
  if (normalized !== "ontology") params.delete("ontology");
  if (normalized !== "feed") params.delete("feed");
  if (normalized !== "feed") params.delete("marketView");
  if (normalized !== "settings") params.delete("settings");
  if (normalized !== "portfolio") params.delete("portfolioView");
  if (normalized !== "operations") params.delete("operationsView");
  if (normalized !== "experiments") {
    params.delete("lab");
    params.delete("experimentId");
    params.delete("experimentSection");
    params.delete("ontologyExperimentId");
  }
  params.delete("mode");
  params.delete("detail");
  params.delete("detailKey");
  if (normalized === "overview") {
    params.delete("tab");
  } else {
    params.set("tab", normalized);
  }
  var path = window.location.pathname || "/";
  var query = params.toString();
  var hash = window.location.hash || "";
  return path + (query ? "?" + query : "") + hash;
}

function consoleWorkspaceViewUrl(tab, key, value, defaultValue) {
  var params = new URLSearchParams(window.location.search);
  params.set("tab", tab);
  if (value === defaultValue) params.delete(key);
  else params.set(key, value);
  params.delete("detail");
  params.delete("detailKey");
  var path = window.location.pathname || "/";
  var query = params.toString();
  return path + (query ? "?" + query : "") + (window.location.hash || "");
}

function writeConsoleWorkspaceViewHistory(tab, key, value, defaultValue) {
  if (!window.history || !window.history.replaceState) return;
  window.history.replaceState({ tab: tab, view: value }, "", consoleWorkspaceViewUrl(tab, key, value, defaultValue));
}

function accountSectionUrl(section) {
  var normalized = normalizeAccountSection(section);
  var params = new URLSearchParams(window.location.search);
  params.set("tab", "accounts");
  params.delete("notification");
  params.delete("strategy");
  params.delete("ontology");
  params.delete("feed");
  params.delete("mode");
  if (normalized === "status") {
    params.delete("account");
  } else {
    params.set("account", normalized);
  }
  var path = window.location.pathname || "/";
  var query = params.toString();
  var hash = window.location.hash || "";
  return path + (query ? "?" + query : "") + hash;
}

function notificationSectionUrl(section) {
  var normalized = normalizeNotificationSection(section);
  var params = new URLSearchParams(window.location.search);
  params.set("tab", "notifications");
  params.delete("account");
  params.delete("strategy");
  params.delete("ontology");
  params.delete("feed");
  params.delete("mode");
  if (normalized === "status") {
    params.delete("notification");
  } else {
    params.set("notification", normalized);
  }
  var path = window.location.pathname || "/";
  var query = params.toString();
  var hash = window.location.hash || "";
  return path + (query ? "?" + query : "") + hash;
}

function settingsSectionUrl(section) {
  var normalized = normalizeSettingsSection(section);
  var params = new URLSearchParams(window.location.search);
  params.set("tab", "settings");
  params.delete("account");
  params.delete("notification");
  params.delete("strategy");
  params.delete("ontology");
  params.delete("feed");
  params.delete("mode");
  params.delete("detail");
  params.delete("detailKey");
  if (normalized === "account") params.delete("settings");
  else params.set("settings", normalized);
  var path = window.location.pathname || "/";
  var query = params.toString();
  var hash = window.location.hash || "";
  return path + (query ? "?" + query : "") + hash;
}

function strategySectionUrl(section) {
  var normalized = normalizeStrategySection(section);
  var params = new URLSearchParams(window.location.search);
  params.set("tab", "modeling");
  params.delete("account");
  params.delete("notification");
  params.delete("ontology");
  params.delete("feed");
  params.delete("mode");
  if (normalized === "overview") {
    params.delete("strategy");
  } else {
    params.set("strategy", normalized);
  }
  var path = window.location.pathname || "/";
  var query = params.toString();
  var hash = window.location.hash || "";
  return path + (query ? "?" + query : "") + hash;
}

function feedSectionUrl(section) {
  var normalized = normalizeFeedSection(section);
  var params = new URLSearchParams(window.location.search);
  params.set("tab", "feed");
  params.delete("account");
  params.delete("notification");
  params.delete("strategy");
  params.delete("ontology");
  params.delete("mode");
  if (normalized === "settings") {
    params.set("mode", "settings");
  }
  if (normalized === "overview") {
    params.delete("feed");
  } else {
    params.set("feed", normalized);
  }
  var path = window.location.pathname || "/";
  var query = params.toString();
  var hash = window.location.hash || "";
  return path + (query ? "?" + query : "") + hash;
}

function marketWorkspaceUrl(mode) {
  var normalized = normalizeMarketWorkspaceMode(mode);
  var params = new URLSearchParams(window.location.search);
  params.set("tab", "feed");
  params.delete("feed");
  params.delete("mode");
  if (normalized === "mine") params.delete("marketView");
  else params.set("marketView", normalized);
  var path = window.location.pathname || "/";
  var query = params.toString();
  var hash = window.location.hash || "";
  return path + (query ? "?" + query : "") + hash;
}

function experimentSectionUrl(section, experimentId) {
  var normalized = normalizeExperimentSection(section);
  var params = new URLSearchParams(window.location.search);
  params.set("tab", "experiments");
  params.delete("account");
  params.delete("notification");
  params.delete("strategy");
  params.delete("ontology");
  params.delete("feed");
  params.delete("mode");
  params.delete("experimentSection");
  params.delete("ontologyExperimentId");
  if (normalized === "overview") {
    params.delete("lab");
  } else {
    params.set("lab", normalized);
  }
  var selectedId = String(experimentId == null ? experimentsState.activeOntologyExperimentId : experimentId).trim();
  if (selectedId) {
    params.set("experimentId", selectedId);
  } else {
    params.delete("experimentId");
  }
  var path = window.location.pathname || "/";
  var query = params.toString();
  var hash = window.location.hash || "";
  return path + (query ? "?" + query : "") + hash;
}

function writeTabHistory(tab, replace) {
  if (!window.history) return;
  var method = replace ? "replaceState" : "pushState";
  if (!window.history[method]) return;
  var normalized = normalizeTabId(tab);
  window.history[method]({ tab: normalized }, "", tabUrl(normalized));
}

function workDetailUrl(type, key) {
  var params = new URLSearchParams(window.location.search);
  if (type) {
    params.set("detail", String(type));
  } else {
    params.delete("detail");
  }
  if (type && key) {
    params.set("detailKey", String(key));
  } else {
    params.delete("detailKey");
  }
  var path = window.location.pathname || "/";
  var query = params.toString();
  var hash = window.location.hash || "";
  return path + (query ? "?" + query : "") + hash;
}

function writeWorkDetailHistory(type, key) {
  if (!window.history || !window.history.pushState) return;
  var historyState = Object.assign({}, window.history.state || {}, {
    tab: navigationState.activeTab,
    workDetail: true,
    detail: String(type || ""),
    detailKey: String(key || "")
  });
  window.history.pushState(historyState, "", workDetailUrl(type, key));
}

function writeNotificationSectionHistory(section) {
  if (!window.history || !window.history.replaceState) return;
  var normalized = normalizeNotificationSection(section);
  window.history.replaceState({ tab: "notifications", notification: normalized }, "", notificationSectionUrl(normalized));
}

function writeAccountSectionHistory(section) {
  if (!window.history || !window.history.replaceState) return;
  var normalized = normalizeAccountSection(section);
  window.history.replaceState({ tab: "accounts", account: normalized }, "", accountSectionUrl(normalized));
}

function writeSettingsSectionHistory(section) {
  if (!window.history || !window.history.replaceState) return;
  var normalized = normalizeSettingsSection(section);
  window.history.replaceState({ tab: "settings", settings: normalized }, "", settingsSectionUrl(normalized));
}

function writeStrategySectionHistory(section) {
  if (!window.history || !window.history.replaceState) return;
  var normalized = normalizeStrategySection(section);
  window.history.replaceState({ tab: "modeling", strategy: normalized }, "", strategySectionUrl(normalized));
}

function writeFeedSectionHistory(section) {
  if (!window.history || !window.history.replaceState) return;
  var normalized = normalizeFeedSection(section);
  window.history.replaceState({ tab: "feed", feed: normalized }, "", feedSectionUrl(normalized));
}

function writeMarketWorkspaceHistory(mode) {
  if (!window.history || !window.history.replaceState) return;
  var normalized = normalizeMarketWorkspaceMode(mode);
  window.history.replaceState({ tab: "feed", marketView: normalized }, "", marketWorkspaceUrl(normalized));
}

function writeExperimentSectionHistory(section, experimentId) {
  if (!window.history || !window.history.replaceState) return;
  var normalized = normalizeExperimentSection(section);
  var selectedId = String(experimentId == null ? experimentsState.activeOntologyExperimentId : experimentId).trim();
  window.history.replaceState({ tab: "experiments", lab: normalized, experimentId: selectedId }, "", experimentSectionUrl(normalized, selectedId));
}

function pageModeUrl(pageId, mode) {
  var params = new URLSearchParams(window.location.search);
  var normalized = normalizeTabId(pageId);
  params.set("tab", normalized);
  if (normalizePageMode(mode) === "settings") {
    params.set("mode", "settings");
  } else {
    params.delete("mode");
  }
  var path = window.location.pathname || "/";
  var query = params.toString();
  var hash = window.location.hash || "";
  return path + (query ? "?" + query : "") + hash;
}

function writePageModeHistory(pageId, mode) {
  if (!window.history || !window.history.replaceState) return;
  var normalized = normalizeTabId(pageId || navigationState.activeTab);
  window.history.replaceState({ tab: normalized, mode: normalizePageMode(mode) }, "", pageModeUrl(normalized, mode));
}

export { activeNotificationSectionMeta, activePageMode, activeSectionForPageMode, activeSettingsSectionMeta, initialAccountSection, initialExperimentSection, initialFeedSection, initialInvestmentGraphLayer, initialMarketWorkspaceMode, initialNotificationSection, initialOntologyExperimentId, initialOntologySection, initialOntologyWorldDepth, initialOntologyWorldFocusId, initialOntologyWorldLens, initialOperationsView, initialPageModeForTab, initialPageViewModes, initialPortfolioView, initialSettingsSection, initialStrategyProposalSection, initialStrategySection, initialTab, initialWorkDetailLayer, investmentCaseDetailRequiresKey, modeSectionsForPage, normalizeAccountSection, normalizeExperimentSection, normalizeFeedSection, normalizeInvestmentGraphLayer, normalizeMarketWorkspaceMode, normalizeNotificationSection, normalizeOntologyGraphId, normalizeOntologySection, normalizeOntologyWorldDepth, normalizeOntologyWorldLens, normalizeOperationsView, normalizePageMode, normalizePortfolioView, normalizeSettingsSection, normalizeStrategyProposalSection, normalizeStrategySection, normalizeTabId, ontologyGraphDisplayMeta, pageSupportsMode, sectionModeForPage, setPageViewMode, workDetailUrl, writeAccountSectionHistory, writeConsoleWorkspaceViewHistory, writeExperimentSectionHistory, writeFeedSectionHistory, writeMarketWorkspaceHistory, writeNotificationSectionHistory, writePageModeHistory, writeSettingsSectionHistory, writeStrategySectionHistory, writeTabHistory, writeWorkDetailHistory };
