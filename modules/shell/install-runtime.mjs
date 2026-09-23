const deferredInstallPromptCell = { value: null };

const appServiceWorkerRegistrationCell = { value: null };

const serviceWorkerReloadPendingCell = { value: false };

var appShellStatus = {
  online: typeof navigator === "undefined" || navigator.onLine !== false,
  installAvailable: false,
  updateAvailable: false
};

export { appServiceWorkerRegistrationCell, appShellStatus, deferredInstallPromptCell, serviceWorkerReloadPendingCell };
