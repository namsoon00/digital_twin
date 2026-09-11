import { scheduleTopbarScrollState } from "./chrome.mjs";
import { layoutLifetime, viewLifetime } from "./lifecycle.mjs";
import { overlayScrollPositionCell } from "./overlay-runtime.mjs";
import { normalizeTabId } from "./routes.mjs";
import { app } from "../shell/root.mjs";
import { navigationState } from "../state/navigation.mjs";

var lastPageScrollActivityAt = 0;

function scrollKeyForTab(tab) {
  return normalizeTabId(tab || navigationState.activeTab);
}

function activeScrollKey() {
  return scrollKeyForTab(navigationState.activeTab);
}

function currentWorkspaceMain() {
  return app && app.querySelector ? app.querySelector(".workspace-main") : null;
}

function currentWorkspaceScroller() {
  var workspace = currentWorkspaceMain();
  if (!workspace) return null;
  var style = window.getComputedStyle ? window.getComputedStyle(workspace) : null;
  var overflowY = style ? String(style.overflowY || style.overflow || "") : "";
  if (overflowY === "visible" || overflowY === "clip") return null;
  if (overflowY === "auto" || overflowY === "scroll") return workspace;
  return workspace.scrollHeight > workspace.clientHeight + 1 ? workspace : null;
}

function scrollTopNumber(value) {
  var number = Number(value || 0);
  return isFinite(number) ? Math.max(0, number) : 0;
}

function windowScrollTop() {
  var doc = document.documentElement || {};
  var body = document.body || {};
  return scrollTopNumber(window.pageYOffset || doc.scrollTop || body.scrollTop || 0);
}

function maxWindowScrollTop() {
  var doc = document.documentElement || {};
  var body = document.body || {};
  var scrollHeight = Math.max(scrollTopNumber(doc.scrollHeight), scrollTopNumber(body.scrollHeight));
  return Math.max(0, scrollHeight - scrollTopNumber(window.innerHeight || doc.clientHeight || 0));
}

function clampScrollTop(value, max) {
  return Math.max(0, Math.min(scrollTopNumber(value), scrollTopNumber(max)));
}

function renderedScrollKey() {
  var workspace = currentWorkspaceMain();
  return workspace ? workspace.getAttribute("data-scroll-key") || "" : "";
}

function rememberRenderedPageScrollPosition() {
  var key = renderedScrollKey();
  if (!key) return null;
  if (
    overlayScrollPositionCell.value
    && overlayScrollPositionCell.value.key === key
    && document.documentElement.classList.contains("oa-overlay-open")
  ) {
    return overlayScrollPositionCell.value;
  }
  var scroller = currentWorkspaceScroller();
  var position = {
    key: key,
    workspaceTop: scroller ? scrollTopNumber(scroller.scrollTop) : 0,
    windowTop: windowScrollTop()
  };
  navigationState.tabScrollPositions[key] = position;
  return position;
}

function restoreRenderedPageScrollPosition(preferredPosition) {
  var key = renderedScrollKey() || activeScrollKey();
  var saved = preferredPosition && preferredPosition.key === key
    ? preferredPosition
    : (navigationState.tabScrollPositions[key] || {});
  var scroller = currentWorkspaceScroller();
  if (scroller) {
    var workspaceTop = scrollTopNumber(saved.workspaceTop) || scrollTopNumber(saved.windowTop);
    scroller.scrollTop = clampScrollTop(workspaceTop, Math.max(0, scroller.scrollHeight - scroller.clientHeight));
    if (window.scrollTo) window.scrollTo(0, 0);
    return;
  }
  if (window.scrollTo) {
    var windowTop = scrollTopNumber(saved.windowTop) || scrollTopNumber(saved.workspaceTop);
    window.scrollTo(0, clampScrollTop(windowTop, maxWindowScrollTop()));
  }
}

function notePageScrollActivity() {
  lastPageScrollActivityAt = Date.now();
}

function pageScrollRecentlyActive() {
  return Date.now() - lastPageScrollActivityAt < 180;
}

function restoreRenderedPageScrollPositionAfterLayout(position) {
  restoreRenderedPageScrollPosition(position);
  if (!position || pageScrollRecentlyActive() || typeof window === "undefined" || !window.requestAnimationFrame) return;
  var currentView = viewLifetime.capture();
  var currentLayout = layoutLifetime.capture();
  var activityAt = lastPageScrollActivityAt;
  window.requestAnimationFrame(function () {
    if (!currentView() || !currentLayout() || activityAt !== lastPageScrollActivityAt) return;
    restoreRenderedPageScrollPosition(position);
  });
}

function renderedElementPath(element) {
  if (!element || !app || element === app) return [];
  var path = [];
  var current = element;
  while (current && current !== app) {
    var parent = current.parentElement;
    if (!parent) return null;
    var index = Array.prototype.indexOf.call(parent.children || [], current);
    if (index < 0) return null;
    path.unshift(index);
    current = parent;
  }
  return current === app ? path : null;
}

function renderedElementAtPath(path) {
  if (!Array.isArray(path)) return null;
  var current = app;
  for (var index = 0; current && index < path.length; index += 1) {
    current = current.children && current.children[path[index]];
  }
  return current || null;
}

function renderedElementIdentity(element) {
  if (!element || !element.attributes) return [];
  return Array.prototype.slice.call(element.attributes).filter(function (attribute) {
    var name = String(attribute.name || "");
    return name === "id" || name === "name" || name.indexOf("data-") === 0;
  }).filter(function (attribute) {
    return [
      "data-render-mode",
      "data-dashboard-render-mode",
      "data-network-busy",
      "data-network-busy-icon-only",
      "data-mobile-infinite-requested"
    ].indexOf(attribute.name) < 0;
  }).map(function (attribute) {
    return [attribute.name, attribute.value];
  });
}

function renderedElementMatches(snapshot, element) {
  if (!snapshot || !element || String(element.tagName || "") !== snapshot.tagName) return false;
  return (snapshot.identity || []).every(function (attribute) {
    return element.getAttribute(attribute[0]) === attribute[1];
  });
}

function renderedScrollableElements() {
  if (!app || !app.querySelectorAll) return [];
  var selectors = [
    "[data-scroll-key]",
    "[data-preserve-scroll]",
    "[role='tablist']",
    ".tab-bar",
    ".app-nav-tabs",
    ".app-nav-menu-list",
    ".oa-data-table",
    ".work-detail-backdrop",
    ".work-detail-body",
    ".command-palette-results",
    ".ontology-graph-expanded-dialog",
    ".investment-calendar-month-shell",
    ".market-workspace-tabs",
    ".instrument-workspace-tabs",
    ".instrument-range-control",
    ".ontology-catalog-tabs",
    ".cws-tabs"
  ];
  var elements = Array.prototype.slice.call(app.querySelectorAll(selectors.join(",")));
  var active = document.activeElement;
  while (active && active !== app && app.contains(active)) {
    elements.push(active);
    active = active.parentElement;
  }
  return elements.filter(function (element, index) {
    return elements.indexOf(element) === index && (scrollTopNumber(element.scrollTop) || scrollTopNumber(element.scrollLeft));
  });
}

function rememberRenderedInteractiveState() {
  if (!app || !app.querySelectorAll) return null;
  var scrollPositions = renderedScrollableElements().map(function (element) {
    var top = scrollTopNumber(element.scrollTop);
    var left = scrollTopNumber(element.scrollLeft);
    var path = renderedElementPath(element);
    if (!path) return null;
    return {
      path: path,
      tagName: String(element.tagName || ""),
      identity: renderedElementIdentity(element),
      top: top,
      left: left
    };
  }).filter(Boolean);
  var activeElement = document.activeElement;
  var focus = activeElement && activeElement !== document.body && app.contains(activeElement) ? {
    path: renderedElementPath(activeElement),
    tagName: String(activeElement.tagName || ""),
    identity: renderedElementIdentity(activeElement),
    selectionStart: typeof activeElement.selectionStart === "number" ? activeElement.selectionStart : null,
    selectionEnd: typeof activeElement.selectionEnd === "number" ? activeElement.selectionEnd : null
  } : null;
  var disclosures = Array.prototype.slice.call(app.querySelectorAll("details:not(.app-nav-menu)")).map(function (element) {
    return {
      path: renderedElementPath(element),
      tagName: String(element.tagName || ""),
      identity: renderedElementIdentity(element),
      open: Boolean(element.open)
    };
  }).filter(function (item) { return item.path; });
  return { scrollPositions: scrollPositions, focus: focus, disclosures: disclosures };
}

function restoreRenderedDisclosureState(snapshot) {
  if (!snapshot) return;
  (snapshot.disclosures || []).forEach(function (saved) {
    var element = renderedElementAtPath(saved.path);
    if (!renderedElementMatches(saved, element)) {
      element = Array.prototype.slice.call(app.querySelectorAll(String(saved.tagName || "details").toLowerCase())).filter(function (candidate) {
        return renderedElementMatches(saved, candidate);
      })[0] || null;
    }
    if (!element || String(element.tagName || "") !== "DETAILS") return;
    element.open = Boolean(saved.open);
  });
}

function restoreRenderedElementScrollPositions(snapshot) {
  if (!snapshot) return;
  (snapshot.scrollPositions || []).forEach(function (saved) {
    var element = renderedElementAtPath(saved.path);
    if (!renderedElementMatches(saved, element)) return;
    element.scrollTop = saved.top;
    element.scrollLeft = saved.left;
  });
}

function focusElementWithoutScroll(element) {
  if (!element || !element.focus) return false;
  try {
    element.focus({ preventScroll: true });
  } catch (error) {
    element.focus();
  }
  return document.activeElement === element;
}

function restoreRenderedInteractiveStateAfterLayout(snapshot) {
  if (!snapshot) return;
  restoreRenderedElementScrollPositions(snapshot);
  var focus = snapshot.focus;
  var element = focus ? renderedElementAtPath(focus.path) : null;
  if (focus && renderedElementMatches(focus, element) && focusElementWithoutScroll(element)) {
    if (focus.selectionStart != null && focus.selectionEnd != null && element.setSelectionRange) {
      try {
        element.setSelectionRange(focus.selectionStart, focus.selectionEnd);
      } catch (error) {
        // Some input types expose selectionStart but do not accept setSelectionRange.
      }
    }
  }
  restoreRenderedElementScrollPositions(snapshot);
  if (pageScrollRecentlyActive() || typeof window === "undefined" || !window.requestAnimationFrame) return;
  var currentView = viewLifetime.capture();
  var currentLayout = layoutLifetime.capture();
  var activityAt = lastPageScrollActivityAt;
  window.requestAnimationFrame(function () {
    if (!currentView() || !currentLayout() || activityAt !== lastPageScrollActivityAt) return;
    restoreRenderedElementScrollPositions(snapshot);
  });
}

function bindPageScrollMemory() {
  var scroller = currentWorkspaceScroller();
  if (!scroller) return;
  scroller.addEventListener("scroll", function () {
    notePageScrollActivity();
    rememberRenderedPageScrollPosition();
    scheduleTopbarScrollState();
  }, { passive: true });
}

function bindRenderedScrollActivity() {
  if (!app || !app.addEventListener) return;
  app.addEventListener("scroll", notePageScrollActivity, { passive: true, capture: true });
}

export { activeScrollKey, bindPageScrollMemory, bindRenderedScrollActivity, currentWorkspaceScroller, focusElementWithoutScroll, notePageScrollActivity, rememberRenderedInteractiveState, rememberRenderedPageScrollPosition, restoreRenderedDisclosureState, restoreRenderedInteractiveStateAfterLayout, restoreRenderedPageScrollPosition, restoreRenderedPageScrollPositionAfterLayout, scrollTopNumber, windowScrollTop };
