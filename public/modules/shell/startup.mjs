import { loadServiceAccounts } from "../accounts/commands.mjs";
import { closeInvestmentCalendarCandidateConfirmation } from "../calendar/commands.mjs";
import { scheduleAppNavScrollState, scheduleTopbarScrollState } from "../navigation/chrome.mjs";
import { closeWorkDetailLayer, loadInformationWorkDetail, trapWorkDetailFocus } from "../navigation/detail.mjs";
import { bindMobileInfiniteScroll, mobileInfiniteScrollEnabled } from "../navigation/infinite-list.mjs";
import { mobileInfiniteScrollModeCell } from "../navigation/infinite-runtime.mjs";
import { layoutLifetime, viewLifetime } from "../navigation/lifecycle.mjs";
import { closeCommandPalette, openCommandPalette } from "../navigation/palette.mjs";
import { primeActiveTabData, scheduleTabDataPreload, tabDataPreloadPrerequisitesReadyCell } from "../navigation/preload.mjs";
import { syncTabFromLocation } from "../navigation/router.mjs";
import { bindRenderedScrollActivity, notePageScrollActivity, rememberRenderedPageScrollPosition, restoreRenderedPageScrollPosition } from "../navigation/scroll.mjs";
import { bindScrollableTabReveal } from "../navigation/tab-strip.mjs";
import { loadNotificationJobDetail } from "../notifications/requests.mjs";
import { connectRealtime } from "../realtime/events.mjs";
import { render, syncRenderedOverlayPageState } from "../render/scheduler.mjs";
import { applyAppTheme, currentAppTheme } from "../settings/preferences.mjs";
import { loadServerSettings } from "../settings/requests.mjs";
import { bindDelegatedConsoleActions } from "./delegated-actions.mjs";
import { appShellStatus, deferredInstallPromptCell } from "./install-runtime.mjs";
import { registerOrbitAlphaServiceWorker, syncAppDocumentMetadata, syncAppViewportHeight } from "./install.mjs";
import { bindNetworkActivityControls } from "./network-activity.mjs";
import { showSnackbar } from "./snackbar.mjs";
import { ensureFreshSnapshot } from "../snapshot/requests.mjs";
import { accountsState } from "../state/accounts.mjs";
import { calendarState } from "../state/calendar.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { shellState } from "../state/shell.mjs";

function startApplication() {
  window.addEventListener("pagehide", function () {
    viewLifetime.invalidate();
    layoutLifetime.invalidate();
  });
if (window.matchMedia) {
  var systemThemeQuery = window.matchMedia("(prefers-color-scheme: dark)");
  var handleSystemThemeChange = function () {
    if (currentAppTheme() === "system") applyAppTheme();
  };
  if (systemThemeQuery.addEventListener) {
    systemThemeQuery.addEventListener("change", handleSystemThemeChange);
  } else if (systemThemeQuery.addListener) {
    systemThemeQuery.addListener(handleSystemThemeChange);
  }
}
if (window.addEventListener) {
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredInstallPromptCell.value = event;
    appShellStatus.installAvailable = true;
    render();
  });
  window.addEventListener("appinstalled", function () {
    deferredInstallPromptCell.value = null;
    appShellStatus.installAvailable = false;
    syncAppDocumentMetadata();
    showSnackbar("Orbit Alpha가 홈 화면에 설치되었습니다.", "success");
  });
  window.addEventListener("online", function () {
    appShellStatus.online = true;
    showSnackbar("연결이 복구되었습니다. 최신 데이터를 확인합니다.", "success");
    render();
    ensureFreshSnapshot("online", true);
  });
  window.addEventListener("offline", function () {
    appShellStatus.online = false;
    render();
  });
  window.addEventListener("popstate", syncTabFromLocation);
  window.addEventListener("pageshow", function (event) {
    syncRenderedOverlayPageState();
    ensureFreshSnapshot(event && event.persisted ? "page-restore" : "page-entry", Boolean(event && event.persisted));
  });
  window.addEventListener("scroll", function () {
    notePageScrollActivity();
    rememberRenderedPageScrollPosition();
    scheduleAppNavScrollState();
    scheduleTopbarScrollState();
  }, { passive: true });
  window.addEventListener("resize", function () {
    rememberRenderedPageScrollPosition();
    var nextInfiniteScrollMode = mobileInfiniteScrollEnabled();
    if (mobileInfiniteScrollModeCell.value !== null && mobileInfiniteScrollModeCell.value !== nextInfiniteScrollMode) {
      mobileInfiniteScrollModeCell.value = nextInfiniteScrollMode;
      render();
    } else {
      bindMobileInfiniteScroll();
    }
    restoreRenderedPageScrollPosition();
    scheduleAppNavScrollState();
    scheduleTopbarScrollState();
  });
  window.addEventListener("keydown", function (event) {
    if ((event.metaKey || event.ctrlKey) && String(event.key || "").toLowerCase() === "k") {
      event.preventDefault();
      if (!navigationState.commandPaletteOpen) openCommandPalette();
      return;
    }
    if (trapWorkDetailFocus(event)) return;
    if (event.key !== "Escape") return;
    if (accountsState.watchlistAccountPickerSymbol) {
      accountsState.watchlistAccountPickerSymbol = "";
      render();
      return;
    }
    if (navigationState.commandPaletteOpen) {
      closeCommandPalette();
      return;
    }
    if (calendarState.investmentCalendarCandidateConfirmation) {
      closeInvestmentCalendarCandidateConfirmation();
      return;
    }
    if (calendarState.calendarEntryModalOpen) {
      calendarState.calendarEntryModalOpen = false;
      render();
      return;
    }
    if (notificationsState.notificationTemplateEditorOpen) {
      notificationsState.notificationTemplateEditorOpen = false;
      render();
      return;
    }
    if (notificationsState.notificationPolicyEditorOpen) {
      notificationsState.notificationPolicyEditorOpen = false;
      render();
      return;
    }
    if (ontologyState.expandedOntologyGraphId) {
      ontologyState.expandedOntologyGraphId = "";
      render();
      return;
    }
    if (navigationState.workDetailLayer) {
      closeWorkDetailLayer();
      return;
    }
    if (!navigationState.monitoringDetail) return;
    navigationState.monitoringDetail = null;
    render();
  });
}
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") ensureFreshSnapshot("app-resume", false);
  });
}
bindNetworkActivityControls();
bindDelegatedConsoleActions();
bindRenderedScrollActivity();
bindScrollableTabReveal();
applyAppTheme();
syncAppViewportHeight();
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", syncAppViewportHeight, { passive: true });
  window.visualViewport.addEventListener("scroll", syncAppViewportHeight, { passive: true });
}
registerOrbitAlphaServiceWorker();
connectRealtime();
primeActiveTabData(navigationState.activeTab);
render();
loadInformationWorkDetail(navigationState.workDetailLayer);
if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "notification-job" && navigationState.workDetailLayer.key) {
  loadNotificationJobDetail(navigationState.workDetailLayer.key);
}
var snapshotLoadTask = ensureFreshSnapshot("initial-entry", false);
var snapshotPrerequisites = [loadServerSettings(), loadServiceAccounts()];
Promise.all(snapshotPrerequisites.map(function (task) {
  return task.catch(function () {
    return null;
  });
})).finally(function () {
  tabDataPreloadPrerequisitesReadyCell.value = true;
  if (shellState.snapshot) render();
  scheduleTabDataPreload({ reason: "startup-prerequisites" });
});
snapshotLoadTask.catch(function () {
  return null;
});
}

export { startApplication };
