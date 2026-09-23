import { closeAppNavMenu } from "./chrome.mjs";
import { navigateToTab } from "./router.mjs";
import { pendingScrollableTabRevealCell, scrollableTabRevealFrameCell } from "./tab-runtime.mjs";
import { reducedMotionPreferred } from "../render/scheduler.mjs";
import { app } from "../shell/root.mjs";
import { navigationState } from "../state/navigation.mjs";

function currentTabBar() {
  return app && app.querySelector ? app.querySelector(".tab-bar") : null;
}

function scrollableTabContainerSelector() {
  return [
    "[role='tablist']",
    ".tab-bar",
    ".app-nav-tabs",
    ".market-workspace-tabs",
    ".ontology-catalog-tabs",
    ".cws-tabs"
  ].join(",");
}

function scrollableTabContainers() {
  if (!app || !app.querySelectorAll) return [];
  return Array.prototype.slice.call(app.querySelectorAll(scrollableTabContainerSelector()));
}

function scrollableTabContext(button) {
  if (!button || !button.closest) return null;
  var container = button.closest(scrollableTabContainerSelector());
  if (!container || !app.contains(container)) return null;
  var containers = scrollableTabContainers();
  var buttons = Array.prototype.slice.call(container.querySelectorAll("button"));
  return {
    container: container,
    containerIndex: containers.indexOf(container),
    containerClass: String(container.className || ""),
    containerLabel: String(container.getAttribute("aria-label") || ""),
    buttonIndex: buttons.indexOf(button)
  };
}

function restoredScrollableTabContainer(context) {
  if (!context) return null;
  if (context.container && context.container.isConnected && app.contains(context.container)) return context.container;
  var containers = scrollableTabContainers();
  var exact = containers.filter(function (container) {
    return String(container.className || "") === context.containerClass
      && String(container.getAttribute("aria-label") || "") === context.containerLabel;
  });
  if (exact.length) return exact[0];
  return containers[context.containerIndex] || null;
}

function activeScrollableTabButton(container, fallbackIndex) {
  var buttons = Array.prototype.slice.call(container.querySelectorAll("button")).filter(function (button) {
    return !button.hidden && !button.disabled;
  });
  var active = buttons.filter(function (button) {
    return button.getAttribute("aria-selected") === "true"
      || button.getAttribute("aria-current") === "page"
      || button.classList.contains("active");
  })[0] || buttons[Math.max(0, Number(fallbackIndex || 0))] || null;
  return { buttons: buttons, active: active };
}

function scrollableTabTargetLeft(container, active, next) {
  var clientWidth = Math.max(0, Number(container.clientWidth || 0));
  var maxScroll = Math.max(0, Number(container.scrollWidth || 0) - clientWidth);
  if (!active || !clientWidth || !maxScroll) return Number(container.scrollLeft || 0);
  var containerRect = container.getBoundingClientRect();
  var activeRect = active.getBoundingClientRect();
  var nextRect = next ? next.getBoundingClientRect() : activeRect;
  var currentLeft = Math.max(0, Number(container.scrollLeft || 0));
  var padding = Math.min(12, Math.max(6, clientWidth * 0.03));
  var activeLeft = currentLeft + activeRect.left - containerRect.left;
  var activeRight = currentLeft + activeRect.right - containerRect.left;
  var nextLeft = currentLeft + nextRect.left - containerRect.left;
  var nextRight = currentLeft + nextRect.right - containerRect.left;
  var desiredRight = next
    ? (nextRight - activeLeft <= clientWidth - padding * 2
      ? nextRight
      : Math.min(nextRight, nextLeft + Math.min(44, Math.max(24, nextRect.width))))
    : activeRight;
  var minimumLeft = desiredRight + padding - clientWidth;
  var maximumLeft = activeLeft - padding;
  var targetLeft = Math.min(Math.max(currentLeft, minimumLeft), maximumLeft);
  return Math.max(0, Math.min(targetLeft, maxScroll));
}

function revealPendingScrollableTab() {
  var context = pendingScrollableTabRevealCell.value;
  pendingScrollableTabRevealCell.value = null;
  var container = restoredScrollableTabContainer(context);
  if (!container || container.scrollWidth <= container.clientWidth + 1) return;
  var selection = activeScrollableTabButton(container, context && context.buttonIndex);
  if (!selection.active) return;
  var activeIndex = selection.buttons.indexOf(selection.active);
  var next = activeIndex >= 0 ? selection.buttons[activeIndex + 1] || null : null;
  var targetLeft = scrollableTabTargetLeft(container, selection.active, next);
  if (Math.abs(Number(container.scrollLeft || 0) - targetLeft) < 1) return;
  if (container.scrollTo) {
    try {
      container.scrollTo({ left: targetLeft, behavior: reducedMotionPreferred() ? "auto" : "smooth" });
    } catch (error) {
      container.scrollLeft = targetLeft;
    }
  } else {
    container.scrollLeft = targetLeft;
  }
  if (container.classList.contains("tab-bar")) navigationState.tabBarScrollLeft = targetLeft;
}

function scheduleScrollableTabReveal() {
  if (scrollableTabRevealFrameCell.value) return;
  var frame = window.requestAnimationFrame || function (callback) { return window.setTimeout(callback, 16); };
  scrollableTabRevealFrameCell.value = frame(function () {
    scrollableTabRevealFrameCell.value = frame(function () {
      scrollableTabRevealFrameCell.value = 0;
      revealPendingScrollableTab();
    });
  });
}

function queueScrollableTabReveal(button) {
  var context = scrollableTabContext(button);
  if (!context) return;
  pendingScrollableTabRevealCell.value = context;
  scheduleScrollableTabReveal();
}

function bindScrollableTabReveal() {
  if (!app || !app.addEventListener) return;
  var pointerStart = null;
  var touchStart = null;
  var targetButton = function (event) {
    var button = event.target && event.target.closest && event.target.closest("button");
    return button && app.contains(button) ? button : null;
  };
  app.addEventListener("pointerdown", function (event) {
    pointerStart = activePointerPoint(event);
  }, { passive: true, capture: true });
  app.addEventListener("pointerup", function (event) {
    if (!isTapMovement(pointerStart, event)) return;
    queueScrollableTabReveal(targetButton(event));
  }, { passive: true, capture: true });
  app.addEventListener("touchstart", function (event) {
    touchStart = activePointerPoint(event);
  }, { passive: true, capture: true });
  app.addEventListener("touchend", function (event) {
    if (!isTapMovement(touchStart, event)) return;
    queueScrollableTabReveal(targetButton(event));
  }, { passive: true, capture: true });
  app.addEventListener("click", function (event) {
    queueScrollableTabReveal(targetButton(event));
  }, { capture: true });
}

function rememberTabBarPosition() {
  var tabBar = currentTabBar();
  if (!tabBar) return;
  navigationState.tabBarScrollLeft = Number(tabBar.scrollLeft || 0);
}

function restoreTabBarPosition() {
  var tabBar = currentTabBar();
  if (!tabBar || tabBar.scrollWidth <= tabBar.clientWidth) return;
  var maxScroll = Math.max(0, tabBar.scrollWidth - tabBar.clientWidth);
  var targetLeft = Math.max(0, Math.min(Number(navigationState.tabBarScrollLeft || 0), maxScroll));
  var active = tabBar.querySelector("[aria-current='page']") || tabBar.querySelector(".active");
  tabBar.scrollLeft = targetLeft;
  if (active) {
    var padding = 8;
    var activeLeft = active.offsetLeft;
    var activeRight = activeLeft + active.offsetWidth;
    var visibleLeft = tabBar.scrollLeft;
    var visibleRight = visibleLeft + tabBar.clientWidth;
    if (activeLeft < visibleLeft + padding) {
      targetLeft = Math.max(0, activeLeft - padding);
    } else if (activeRight > visibleRight - padding) {
      targetLeft = Math.min(maxScroll, activeRight - tabBar.clientWidth + padding);
    }
    tabBar.scrollLeft = targetLeft;
  }
  navigationState.tabBarScrollLeft = Number(tabBar.scrollLeft || 0);
}

function activePointerPoint(event) {
  var touch = event && event.changedTouches && event.changedTouches[0];
  var source = touch || event || {};
  return {
    x: Number(source.clientX || 0),
    y: Number(source.clientY || 0)
  };
}

function isTapMovement(start, event) {
  if (!start) return true;
  var point = activePointerPoint(event);
  return Math.abs(point.x - start.x) <= 12 && Math.abs(point.y - start.y) <= 12;
}

function activateTabButton(button, event) {
  var nextTab = button.getAttribute("data-tab") || "overview";
  if (nextTab === navigationState.activeTab) return false;
  if (event && event.preventDefault) event.preventDefault();
  if (event && event.stopPropagation) event.stopPropagation();
  closeAppNavMenu();
  navigateToTab(nextTab);
  return true;
}

function bindTabNavigation(button) {
  var pointerStart = null;
  var touchStart = null;
  var handledAt = 0;
  var markHandled = function () {
    handledAt = Date.now();
  };

  button.addEventListener("pointerdown", function (event) {
    pointerStart = activePointerPoint(event);
  }, { passive: true });
  button.addEventListener("pointercancel", function () {
    pointerStart = null;
  });
  button.addEventListener("pointerup", function (event) {
    if (!isTapMovement(pointerStart, event)) return;
    markHandled();
    activateTabButton(button, event);
  });
  button.addEventListener("touchstart", function (event) {
    touchStart = activePointerPoint(event);
  }, { passive: true });
  button.addEventListener("touchcancel", function () {
    touchStart = null;
  });
  button.addEventListener("touchend", function (event) {
    if (Date.now() - handledAt < 500) return;
    if (!isTapMovement(touchStart, event)) return;
    markHandled();
    activateTabButton(button, event);
  }, { passive: false });
  button.addEventListener("click", function (event) {
    if (Date.now() - handledAt < 700) {
      event.preventDefault();
      return;
    }
    activateTabButton(button, event);
  });
}

export { bindScrollableTabReveal, bindTabNavigation, rememberTabBarPosition, restoreTabBarPosition };
