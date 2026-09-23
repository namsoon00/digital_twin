import { currentWorkspaceScroller, scrollTopNumber, windowScrollTop } from "./scroll.mjs";
import { app } from "../shell/root.mjs";

var appNavLastScrollY = 0;

var appNavHidden = false;

var appNavScrollTicking = false;

var appNavScrollDirection = 0;

var appNavScrollDistance = 0;

var topbarCollapsed = false;

var topbarScrollTicking = false;

function currentShell() {
  return app && app.querySelector ? app.querySelector(".shell") : null;
}

function setTopbarCollapsed(collapsed) {
  topbarCollapsed = Boolean(collapsed);
  var shell = currentShell();
  if (!shell) return;
  shell.classList.toggle("topbar-collapsed", topbarCollapsed);
}

function syncTopbarScrollState() {
  var scroller = currentWorkspaceScroller();
  var scrollTop = scroller ? scrollTopNumber(scroller.scrollTop) : windowScrollTop();
  setTopbarCollapsed(scrollTop > 32);
}

function scheduleTopbarScrollState() {
  if (topbarScrollTicking) return;
  topbarScrollTicking = true;
  var frame = window.requestAnimationFrame || function (callback) {
    return window.setTimeout(callback, 16);
  };
  frame(function () {
    topbarScrollTicking = false;
    syncTopbarScrollState();
  });
}

function currentAppNav() {
  return app && app.querySelector ? app.querySelector(".app-nav") : null;
}

function closeAppNavMenu() {
  var menu = app && app.querySelector ? app.querySelector(".app-nav-menu") : null;
  if (menu) menu.open = false;
}

function setAppNavHidden(hidden) {
  var nextHidden = Boolean(hidden);
  if (appNavHidden === nextHidden) return;
  appNavHidden = nextHidden;
  var nav = currentAppNav();
  if (!nav) return;
  nav.classList.toggle("is-hidden", appNavHidden);
  if (appNavHidden) closeAppNavMenu();
}

function syncAppNavScrollState() {
  var scrollY = Math.max(0, Number(window.pageYOffset || document.documentElement.scrollTop || 0));
  var mobile = window.matchMedia ? window.matchMedia("(max-width: 860px)").matches : false;
  if (!mobile) {
    setAppNavHidden(false);
    appNavLastScrollY = scrollY;
    appNavScrollDirection = 0;
    appNavScrollDistance = 0;
    return;
  }
  var delta = scrollY - appNavLastScrollY;
  var direction = delta > 1 ? 1 : (delta < -1 ? -1 : 0);
  if (direction) {
    if (direction !== appNavScrollDirection) appNavScrollDistance = 0;
    appNavScrollDirection = direction;
    appNavScrollDistance += Math.abs(delta);
  }
  if (Math.abs(delta) > 3) closeAppNavMenu();
  if (scrollY < 48) {
    setAppNavHidden(false);
    appNavScrollDistance = 0;
  } else if (direction > 0 && scrollY > 140 && appNavScrollDistance >= 44) {
    setAppNavHidden(true);
    appNavScrollDistance = 0;
  } else if (direction < 0 && appNavScrollDistance >= 56) {
    setAppNavHidden(false);
    appNavScrollDistance = 0;
  }
  appNavLastScrollY = scrollY;
}

function scheduleAppNavScrollState() {
  if (appNavScrollTicking) return;
  appNavScrollTicking = true;
  var frame = window.requestAnimationFrame || function (callback) {
    return window.setTimeout(callback, 16);
  };
  frame(function () {
    appNavScrollTicking = false;
    syncAppNavScrollState();
  });
}

export { appNavHidden, closeAppNavMenu, scheduleAppNavScrollState, scheduleTopbarScrollState, setAppNavHidden, syncAppNavScrollState, syncTopbarScrollState, topbarCollapsed };
