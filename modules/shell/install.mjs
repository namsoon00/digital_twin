import { render } from "../render/scheduler.mjs";
import { activeTabMeta, appBrandName } from "./catalog.mjs";
import { appServiceWorkerRegistrationCell, appShellStatus, deferredInstallPromptCell, serviceWorkerReloadPendingCell } from "./install-runtime.mjs";
import { showSnackbar } from "./snackbar.mjs";

function isStandaloneApp() {
  return Boolean(
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches)
    || (typeof navigator !== "undefined" && navigator.standalone === true)
  );
}

function isIosBrowser() {
  if (typeof navigator === "undefined") return false;
  return /iPad|iPhone|iPod/.test(String(navigator.userAgent || "")) && !window.MSStream;
}

function canOfferAppInstall() {
  return !isStandaloneApp() && Boolean(deferredInstallPromptCell.value || isIosBrowser());
}

function syncAppViewportHeight() {
  var viewport = window.visualViewport;
  var height = Math.max(0, Number((viewport && viewport.height) || window.innerHeight || 0));
  if (height) document.documentElement.style.setProperty("--oa-visual-viewport-height", Math.round(height) + "px");
}

function syncAppDocumentMetadata() {
  var tab = activeTabMeta();
  document.title = (tab && tab.label ? tab.label + " · " : "") + appBrandName;
  document.documentElement.classList.toggle("oa-standalone", isStandaloneApp());
  document.documentElement.classList.toggle("oa-offline", !appShellStatus.online);
}

function installOrbitAlpha() {
  if (isStandaloneApp()) {
    showSnackbar("이미 앱으로 실행 중입니다.", "success");
    return;
  }
  if (!deferredInstallPromptCell.value) {
    showSnackbar(isIosBrowser() ? "공유 버튼을 누른 뒤 ‘홈 화면에 추가’를 선택하세요." : "브라우저 메뉴에서 앱 설치를 선택하세요.", "success");
    return;
  }
  var prompt = deferredInstallPromptCell.value;
  prompt.prompt();
  prompt.userChoice.then(function (choice) {
    deferredInstallPromptCell.value = null;
    appShellStatus.installAvailable = false;
    if (choice && choice.outcome === "accepted") showSnackbar("Orbit Alpha를 앱으로 설치했습니다.", "success");
    render();
  }).catch(function () {
    deferredInstallPromptCell.value = null;
    appShellStatus.installAvailable = false;
    render();
  });
}

function applyServiceWorkerUpdate() {
  var waiting = appServiceWorkerRegistrationCell.value && appServiceWorkerRegistrationCell.value.waiting;
  if (!waiting) {
    window.location.reload();
    return;
  }
  serviceWorkerReloadPendingCell.value = true;
  waiting.postMessage({ type: "SKIP_WAITING" });
}

function registerOrbitAlphaServiceWorker() {
  if (window.location.protocol === "file:" || typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("service-worker.js?v=" + __FRONTEND_ASSET_VERSION__, { updateViaCache: "none" }).then(function (registration) {
    appServiceWorkerRegistrationCell.value = registration;
    if (registration.waiting && navigator.serviceWorker.controller) {
      appShellStatus.updateAvailable = true;
      render();
    }
    registration.addEventListener("updatefound", function () {
      var worker = registration.installing;
      if (!worker) return;
      worker.addEventListener("statechange", function () {
        if (worker.state === "installed" && navigator.serviceWorker.controller) {
          appShellStatus.updateAvailable = true;
          render();
        }
      });
    });
  }).catch(function () {
    appServiceWorkerRegistrationCell.value = null;
  });
  navigator.serviceWorker.addEventListener("controllerchange", function () {
    if (!serviceWorkerReloadPendingCell.value) return;
    serviceWorkerReloadPendingCell.value = false;
    window.location.reload();
  });
}

export { applyServiceWorkerUpdate, canOfferAppInstall, installOrbitAlpha, registerOrbitAlphaServiceWorker, syncAppDocumentMetadata, syncAppViewportHeight };
