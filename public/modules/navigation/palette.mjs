import { selectConsoleInstrumentRows } from "../market/selectors.mjs";
import { openWorkDetailLayer } from "./detail.mjs";
import { navigateToTab } from "./router.mjs";
import { normalizeTabId } from "./routes.mjs";
import { selectConsoleAlertRows } from "../notifications/selectors.mjs";
import { render } from "../render/scheduler.mjs";
import { formatClock } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { tabs } from "../shell/catalog.mjs";
import { navigationState } from "../state/navigation.mjs";

function openCommandPalette(mode) {
  navigationState.commandPaletteOpen = true;
  navigationState.commandPaletteQuery = "";
  navigationState.commandPaletteMode = String(mode || "search") === "more" ? "more" : "search";
  render();
}

function closeCommandPalette(options) {
  options = options || {};
  if (!navigationState.commandPaletteOpen) return;
  navigationState.commandPaletteOpen = false;
  navigationState.commandPaletteQuery = "";
  navigationState.commandPaletteMode = "search";
  if (!options.skipRender) render();
}

function commandPaletteEntries(snapshot, mode) {
  var paletteMode = String(mode || "search") === "more" ? "more" : "search";
  var moreTabIds = ["calendar", "operations"];
  var entries = tabs.filter(function (tab) {
    if (tab.hidden) return false;
    return paletteMode !== "more" || moreTabIds.indexOf(tab.id) >= 0;
  }).map(function (tab) {
    return { type: "tab", tab: tab.id, label: tab.label, detail: tab.description || "화면 이동", group: "화면" };
  });
  if (paletteMode !== "more") {
    selectConsoleInstrumentRows(snapshot || {}).slice(0, 16).forEach(function (row) {
      entries.push({ type: "instrument", key: row.symbol, tab: "feed", label: row.name || row.symbol, detail: [row.symbol, row.source === "watchlist" ? "관심" : "보유"].filter(Boolean).join(" · "), group: "종목" });
    });
    selectConsoleAlertRows().slice(0, 12).forEach(function (row) {
      entries.push({ type: "notification", key: row.key, tab: "notifications", label: row.title || row.type, detail: [formatClock(row.time), row.status].filter(Boolean).join(" · "), group: "알림" });
    });
  }
  var detailEntries = [
    { key: "account-connections-board", tab: "settings", label: "계정 연결", detail: "Toss API와 계정 상태" },
    { key: "notification-policy-board", tab: "notifications", label: "알림 정책", detail: "발송·보류 기준" },
    { key: "investment-model-overview", tab: "modeling", label: "판단 기준", detail: "활성 투자모델과 릴리스" },
    { key: "investment-model-management", tab: "modeling", label: "투자모델 관리", detail: "규칙·가설·검증·승격" },
    { key: "settings-runtime", tab: "operations", label: "런타임 설정", detail: "데이터·운영 설정" }
  ];
  detailEntries.filter(function (item) {
    return paletteMode !== "more" || item.key !== "settings-runtime";
  }).forEach(function (item) {
    entries.push({ type: "detail", key: item.key, tab: item.tab, label: item.label, detail: item.detail, group: "관리 도구" });
  });
  return entries;
}

function renderCommandPalette(snapshot) {
  if (!navigationState.commandPaletteOpen) return "";
  var paletteMode = String(navigationState.commandPaletteMode || "search") === "more" ? "more" : "search";
  var query = String(navigationState.commandPaletteQuery || "").trim().toLowerCase();
  var matched = commandPaletteEntries(snapshot, paletteMode).filter(function (entry) {
    if (!query) return true;
    return [entry.label, entry.detail, entry.group, entry.key].filter(Boolean).join(" ").toLowerCase().indexOf(query) >= 0;
  }).slice(0, 12);
  return [
    '<div class="command-palette-backdrop" data-command-palette-close>',
    '<section class="command-palette" role="dialog" aria-modal="true" aria-labelledby="command-palette-title" tabindex="-1" data-command-palette-dialog data-command-palette-mode="' + escapeHtml(paletteMode) + '">',
    '<header><div><p class="label">' + escapeHtml(paletteMode === "more" ? "Navigation" : "Search") + '</p><h2 id="command-palette-title">' + escapeHtml(paletteMode === "more" ? "더보기" : "전체 검색") + '</h2></div><button class="icon-button danger" type="button" data-command-palette-close title="닫기" aria-label="닫기">&times;</button></header>',
    '<label class="command-palette-input"><span class="sr-only">검색</span><input type="search" data-command-palette-input value="' + escapeHtml(navigationState.commandPaletteQuery || "") + '" placeholder="' + escapeHtml(paletteMode === "more" ? "일정, 운영, 관리 도구 검색" : "화면, 종목, 알림, 설정 검색") + '" autocomplete="off" /></label>',
    '<div class="command-palette-results">',
    matched.length ? matched.map(function (entry) {
      return '<button type="button" data-command-palette-result="' + escapeHtml(entry.type) + '" data-command-palette-key="' + escapeHtml(entry.key || entry.tab || "") + '" data-command-palette-tab="' + escapeHtml(entry.tab || "") + '"><span><em>' + escapeHtml(entry.group) + '</em><strong>' + escapeHtml(entry.label) + '</strong><small>' + escapeHtml(entry.detail) + '</small></span><b>&rarr;</b></button>';
    }).join("") : '<div class="command-palette-empty">조건에 맞는 결과가 없습니다.</div>',
    '</div>',
    '</section>',
    '</div>'
  ].join("");
}

function activateCommandPaletteResult(type, key, preferredTab) {
  var resultType = String(type || "");
  var resultKey = String(key || "");
  closeCommandPalette({ skipRender: true });
  if (resultType === "tab") {
    navigateToTab(resultKey);
    return;
  }
  var targetTab = resultType === "instrument"
    ? "feed"
    : (resultType === "notification" ? "notifications" : normalizeTabId(preferredTab || "settings"));
  var detailType = resultType === "instrument" ? "market-instrument" : (resultType === "notification" ? "notification-job" : resultKey);
  if (targetTab !== navigationState.activeTab) {
    navigateToTab(targetTab);
    window.setTimeout(function () { openWorkDetailLayer(detailType, resultType === "detail" ? "" : resultKey); }, 0);
    return;
  }
  openWorkDetailLayer(detailType, resultType === "detail" ? "" : resultKey);
}

export { activateCommandPaletteResult, closeCommandPalette, openCommandPalette, renderCommandPalette };
