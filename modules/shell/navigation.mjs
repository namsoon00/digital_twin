import { accountFreshness } from "../accounts/balance.mjs";
import { symbolUniverseRefreshActive, symbolUniverseRefreshStageMeta, symbolUniverseRefreshTerminal } from "../instruments/universe.mjs";
import { appNavHidden } from "../navigation/chrome.mjs";
import { renderInfoIconButton, renderWorkDetailButton } from "../navigation/detail.mjs";
import { normalizeTabId } from "../navigation/routes.mjs";
import { alertRules, enabledAlertRule } from "../notifications/alerts.mjs";
import { defaultSettings } from "../settings/defaults.mjs";
import { settingValue } from "../settings/fields.mjs";
import { formatMoney } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { activeTabMeta, appBrandName, appBrandSubtitle, bottomTabIds, managementTabIds, navigationGroupForTab, navigationGroups, notificationPolicyCatalog, pageStructureMeta, tabById, tabs, tabsForNavigationGroup } from "./catalog.mjs";
import { pageCommandProfile } from "./commands.mjs";
import { appShellStatus } from "./install-runtime.mjs";
import { canOfferAppInstall } from "./install.mjs";
import { navigationState } from "../state/navigation.mjs";
import { shellState } from "../state/shell.mjs";
import { universeState } from "../state/universe.mjs";

function renderTopbarSyncState(snapshot) {
  if (shellState.refreshing) {
    return [
      '<div class="toolbar topbar-actions">',
      '<span class="status-pill demo">백그라운드 동기화 중</span>',
      '</div>'
    ].join("");
  }
  if (shellState.snapshotFromCache) {
    return [
      '<div class="toolbar topbar-actions">',
      '<span class="status-pill mock">직전 화면 유지</span>',
      '</div>'
    ].join("");
  }
  if (shellState.error && shellState.snapshot) {
    return [
      '<div class="toolbar topbar-actions">',
      '<span class="status-pill demo">갱신 확인 필요</span>',
      '</div>'
    ].join("");
  }
  var freshness = accountFreshness(snapshot || shellState.snapshot || {});
  if (freshness.tone === "warn") {
    return [
      '<div class="toolbar topbar-actions">',
      '<span class="status-pill demo">' + escapeHtml(freshness.label + " · " + freshness.detail) + '</span>',
      '</div>'
    ].join("");
  }
  return "";
}

function renderDeskbar(snapshot, modeLabel, modeClass) {
  var portfolio = snapshot.portfolio || {};
  var toss = snapshot.toss || {};
  var positions = Array.isArray(toss.positions) ? toss.positions.filter(function (item) {
    return item && item.source !== "cash";
  }).length : 0;
  var rules = alertRules();
  var policyRules = notificationPolicyCatalog();
  var enabledRules = policyRules.filter(function (rule) {
    return enabledAlertRule(rules, rule.key);
  }).length;
  var decision = snapshot.tossDecision || {};
  var strategy = decision.ontologyStrategy || {};
  var abox = strategy.abox || {};
  var tbox = strategy.tbox || {};
  var relationCount = Number(abox.relationCount || strategy.relationCount || 0);
  return [
    '<section class="deskbar deskbar-full web-style-deskbar" data-style-region="deskbar" data-style-rail="full" aria-label="운영 상태 요약">',
    renderDeskbarCell("Data", modeLabel, accountFreshness(snapshot).label + " · " + accountFreshness(snapshot).detail, accountFreshness(snapshot).tone === "warn" ? "demo" : modeClass),
    renderDeskbarCell("Portfolio", formatMoney(portfolio.total || 0), positions + " positions", "neutral"),
    renderDeskbarCell("Model", settingValue("modelName") || defaultSettings.modelName, "상태 계약 · 조건 기반", "neutral"),
    renderDeskbarCell("Ontology", (tbox.classes || []).length + " TBox / " + relationCount + " rel", (abox.entityCount || 0) + " ABox entities", "neutral"),
    renderDeskbarCell("Alerts", enabledRules + "/" + policyRules.length, shellState.realtime.connected ? "WebSocket live" : "HTTP polling", shellState.realtime.connected ? "live" : "demo"),
    '</section>'
  ].join("");
}

function renderDeskbarCell(label, value, detail, tone) {
  return [
    '<div class="deskbar-cell ' + escapeHtml(tone || "neutral") + '">',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value || "-") + '</strong>',
    '<span>' + escapeHtml(detail || "") + '</span>',
    '</div>'
  ].join("");
}

function renderAppRuntimeBanner() {
  if (!appShellStatus.online) {
    return [
      '<aside class="app-runtime-banner offline" role="status" aria-live="polite">',
      '<span class="app-runtime-indicator" aria-hidden="true"></span>',
      '<div><strong>오프라인 화면</strong><p>저장된 화면을 유지합니다. 연결되면 최신 데이터를 다시 확인합니다.</p></div>',
      '<button class="text-button compact" type="button" data-action="retry-connectivity">재연결</button>',
      '</aside>'
    ].join("");
  }
  if (appShellStatus.updateAvailable) {
    return [
      '<aside class="app-runtime-banner update" role="status" aria-live="polite">',
      '<span class="app-runtime-indicator" aria-hidden="true"></span>',
      '<div><strong>새 버전 준비됨</strong><p>현재 위치를 유지한 채 최신 웹 앱으로 전환할 수 있습니다.</p></div>',
      '<button class="text-button primary compact" type="button" data-action="apply-app-update">업데이트</button>',
      '</aside>'
    ].join("");
  }
  return "";
}

function navTabButton(tab, className) {
  var active = navigationState.activeTab === tab.id;
  var structure = pageStructureMeta(tab.id);
  return [
    '<button type="button" class="' + escapeHtml(className) + (active ? " active" : "") + '" data-tab="' + escapeHtml(tab.id) + '" data-nav-group="' + escapeHtml(structure.groupId) + '"' + (active ? ' aria-current="page"' : "") + '>',
    '<span class="nav-tab-label">' + escapeHtml(tab.label) + '</span>',
    '<span class="nav-tab-description">' + escapeHtml(tab.description || "") + '</span>',
    '</button>'
  ].join("");
}

function renderAppNavigation(activeTab, modeLabel, modeClass, snapshot) {
  var managementTabs = tabs.filter(function (tab) {
    return managementTabIds.indexOf(tab.id) >= 0;
  });
  var managementActive = managementTabs.some(function (tab) {
    return tab.id === navigationState.activeTab;
  });
  function renderAppNavGroup(group, index) {
    var groupTabs = tabsForNavigationGroup(group);
    var active = groupTabs.some(function (tab) { return tab.id === navigationState.activeTab; });
    if (!groupTabs.length) return "";
    return [
      index ? '<span class="app-nav-divider" aria-hidden="true"></span>' : '',
      '<section class="app-nav-group' + (active ? " active" : "") + '" data-nav-group="' + escapeHtml(group.id) + '">',
      '<span class="app-nav-section-label"><strong>' + escapeHtml(group.label) + '</strong><em>' + escapeHtml(group.description || "") + '</em></span>',
      groupTabs.map(function (tab) {
        return navTabButton(tab, "app-nav-tab " + group.id);
      }).join(""),
      '</section>'
    ].join("");
  }
  return [
    '<nav class="app-nav web-style-nav' + (appNavHidden ? " is-hidden" : "") + '" data-style-region="navigation" aria-label="앱 네비게이션">',
    '<div class="app-nav-brand">',
    '<span class="app-brand-mark" aria-hidden="true"><span></span></span>',
    '<div class="app-brand-copy">',
    '<strong>' + escapeHtml(appBrandName) + '</strong>',
    '<span class="app-brand-subtitle">' + escapeHtml(navigationGroupForTab(activeTab.id).label + " · " + (activeTab.description || appBrandSubtitle)) + '</span>',
    '</div>',
    '</div>',
    '<div class="app-nav-tabs" aria-label="업무 구조 탭">',
    navigationGroups.map(renderAppNavGroup).join(""),
    '</div>',
    managementTabs.length ? [
      '<details class="app-nav-menu">',
      '<summary title="검증 도구" aria-label="검증 도구"><span class="app-tool-icon experiments" aria-hidden="true"></span><strong>검증</strong><span>' + escapeHtml(managementActive ? activeTab.label : "관리 탭") + '</span></summary>',
      '<div class="app-nav-menu-list">',
      managementTabs.map(function (tab) {
        return navTabButton(tab, "app-nav-menu-item");
      }).join(""),
      '</div>',
    '</details>'
    ].join("") : '',
    '<div class="app-nav-tools">',
    renderSymbolUniverseNavTask(),
    '<span class="status-pill ' + modeClass + '">' + escapeHtml(modeLabel) + "</span>",
    '<button class="icon-button app-install-button" type="button" data-action="install-app" title="앱으로 설치" aria-label="Orbit Alpha 앱으로 설치"' + (canOfferAppInstall() ? '' : ' hidden aria-hidden="true"') + '><span class="app-tool-icon install" aria-hidden="true"></span></button>',
    '<button class="icon-button app-settings-button' + (navigationState.activeTab === "settings" ? " active" : "") + '" type="button" data-tab="settings" title="설정" aria-label="설정 열기"' + (navigationState.activeTab === "settings" ? ' aria-current="page"' : '') + '><span class="app-tool-icon settings" aria-hidden="true"></span></button>',
    '<button class="icon-button" type="button" data-action="command-palette" data-command-palette-mode="search" title="전체 검색" aria-label="전체 검색"><span class="app-tool-icon search" aria-hidden="true"></span></button>',
    '<button class="icon-button refresh-button' + (shellState.refreshing ? " is-loading" : "") + '" type="button" data-action="refresh" title="' + (shellState.refreshing ? "데이터 갱신 중" : "새로고침") + '" aria-label="' + (shellState.refreshing ? "데이터 갱신 중" : "새로고침") + '"' + (shellState.refreshing ? " disabled aria-busy=\"true\"" : "") + '><span class="app-tool-icon refresh" aria-hidden="true"></span></button>',
    '</div>',
    renderAppNavCommand(activeTab.id, snapshot),
    '</nav>'
  ].join("");
}

function renderSymbolUniverseNavTask() {
  var refresh = universeState.symbolUniverseRefresh || {};
  var active = symbolUniverseRefreshActive(refresh);
  var terminal = refresh.jobId && symbolUniverseRefreshTerminal(refresh);
  var unacknowledged = terminal && String(universeState.symbolUniverseRefreshAcknowledgedJobId || "") !== String(refresh.jobId || "");
  var visible = active || unacknowledged;
  var stage = symbolUniverseRefreshStageMeta(refresh);
  var tone = active ? "active" : (refresh.status === "completed" ? "completed" : (visible ? "failed" : "idle"));
  var label = active
    ? stage.label
    : (refresh.status === "completed" ? "종목 갱신 완료" : refresh.status === "partial" ? "일부 갱신 완료" : visible ? "종목 갱신 확인" : "종목 갱신 대기");
  return [
    '<button class="symbol-refresh-nav-task ' + escapeHtml(tone) + '" type="button" data-action="open-symbol-universe-refresh" aria-label="' + escapeHtml(label + (visible ? " 결과 보기" : "")) + '"' + (visible ? '' : ' hidden disabled aria-hidden="true"') + '>',
    '<span aria-hidden="true">' + (active ? "↻" : refresh.status === "completed" ? "✓" : "!") + '</span>',
    '<strong>' + escapeHtml(label) + '</strong>',
    '</button>'
  ].join("");
}

function renderAppNavCommand(pageId, snapshot) {
  var normalized = normalizeTabId(pageId || navigationState.activeTab);
  var tab = tabById(normalized) || activeTabMeta();
  var activeSnapshot = snapshot || shellState.snapshot || {};
  var freshness = accountFreshness(activeSnapshot);
  var profile = pageCommandProfile(normalized, activeSnapshot);
  var action = normalized === "overview" ? ["screen-info", "overview", "오늘의 데이터 기준"] : null;
  return [
    '<div class="app-nav-command oa-console-command no-mode" data-style-layer="unified-command" data-command-group="' + escapeHtml(profile.groupId) + '" aria-label="현재 화면 작업 요약">',
    '<div class="app-nav-current">',
    '<span class="app-nav-command-kicker">' + escapeHtml(profile.layer) + '</span>',
    '<strong>' + escapeHtml(tab.label || profile.entity) + '</strong>',
    '</div>',
    '<p class="oa-console-command-objective">' + escapeHtml(profile.objective || tab.description || "") + '</p>',
    '<div class="oa-console-command-status">',
    '<span class="status-pill ' + escapeHtml(shellState.snapshotFromCache ? "mock" : (freshness.tone === "warn" ? "demo" : "live")) + '">' + escapeHtml(shellState.refreshing ? "갱신 중" : (shellState.snapshotFromCache ? "직전 화면" : freshness.label)) + '</span>',
    '<em>' + escapeHtml(shellState.refreshing ? "데이터를 확인하고 있습니다" : freshness.detail) + '</em>',
    '</div>',
    '<div class="oa-console-command-actions">',
    renderInfoIconButton(normalized, "이 화면의 데이터 기준"),
    action ? renderWorkDetailButton(action[0], action[1], action[2], "text-button primary compact") : '',
    '</div>',
    '</div>'
  ].join("");
}

function renderTabs() {
  var bottomTabs = bottomTabIds.map(tabById).filter(Boolean);
  var moreActive = ["calendar", "operations"].indexOf(navigationState.activeTab) >= 0;
  return [
    '<nav class="tab-bar" aria-label="주요 탭" style="--tab-count:' + (bottomTabs.length + 1) + '">',
    bottomTabs.map(function (tab) {
      var active = navigationState.activeTab === tab.id || (tab.id === "modeling" && navigationState.activeTab === "experiments");
      return '<button type="button" class="' + (active ? "active" : "") + '" data-tab="' + escapeHtml(tab.id) + '"' + (active ? ' aria-current="page"' : "") + '><span class="tab-icon" data-tab-icon="' + escapeHtml(tab.id) + '" aria-hidden="true"></span><span class="tab-label">' + escapeHtml(tab.label) + '</span><span class="tab-description">' + escapeHtml(tab.description || "") + '</span></button>';
    }).join(""),
    '<button type="button" class="' + (moreActive ? "active" : "") + '" data-action="command-palette" data-command-palette-mode="more"' + (moreActive ? ' aria-current="page"' : '') + '><span class="tab-icon" data-tab-icon="more" aria-hidden="true"></span><span class="tab-label">더보기</span><span class="tab-description">일정·운영·도구</span></button>',
    '</nav>'
  ].join("");
}

export { renderAppNavigation, renderAppRuntimeBanner, renderDeskbar, renderTabs, renderTopbarSyncState };
