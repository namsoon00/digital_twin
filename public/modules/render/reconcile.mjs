import { scrollTopNumber } from "../navigation/scroll.mjs";
import { destroyOntologyCytoscapeGraphs } from "../ontology/graphs.mjs";
import { dashboardRegionReplacementOccurredCell } from "./runtime.mjs";
import { bindActions } from "../shell/actions.mjs";
import { bindAutoGrowingTextareas } from "../shell/forms.mjs";
import { app } from "../shell/root.mjs";

function dashboardDomMismatchPath(currentNode, nextNode, path) {
  path = path || "root";
  if (!currentNode || !nextNode) return path + ":missing";
  if (currentNode.nodeType !== nextNode.nodeType) return path + ":node-type";
  if (currentNode.nodeType === 1 && currentNode.tagName !== nextNode.tagName) return path + ":tag";
  if (currentNode.nodeType !== 1) return "";
  if (currentNode.childNodes.length !== nextNode.childNodes.length) return path + ":children";
  for (var index = 0; index < currentNode.childNodes.length; index += 1) {
    var child = currentNode.childNodes[index];
    var childLabel = child.nodeType === 1 ? String(child.tagName || "node").toLowerCase() : "text";
    var mismatch = dashboardDomMismatchPath(child, nextNode.childNodes[index], path + "/" + childLabel + "[" + index + "]");
    if (mismatch) return mismatch;
  }
  return "";
}

function reconcileKeyedCollection(currentCollection, nextCollection) {
  var currentChildren = Array.prototype.slice.call(currentCollection.children || []);
  var nextChildren = Array.prototype.slice.call(nextCollection.children || []);
  var keyed = {};
  var unkeyed = [];
  currentChildren.forEach(function (child) {
    var key = child.getAttribute("data-console-row-key");
    if (key) {
      if (!keyed[key]) keyed[key] = [];
      keyed[key].push(child);
    } else {
      unkeyed.push(child);
    }
  });
  var desired = nextChildren.map(function (nextChild) {
    var key = nextChild.getAttribute("data-console-row-key");
    var currentChild = key
      ? (keyed[key] && keyed[key].length ? keyed[key].shift() : null)
      : unkeyed.shift();
    if (!currentChild) return nextChild.cloneNode(true);
    if (dashboardDomMismatchPath(currentChild, nextChild, "row")) {
      var replacement = nextChild.cloneNode(true);
      currentChild.replaceWith(replacement);
      return replacement;
    }
    return currentChild;
  });
  desired.forEach(function (child, index) {
    var atIndex = currentCollection.children[index] || null;
    if (atIndex !== child) currentCollection.insertBefore(child, atIndex);
  });
  Array.prototype.slice.call(currentCollection.children || []).forEach(function (child) {
    if (desired.indexOf(child) < 0) child.remove();
  });
}

function reconcileDashboardCollections(current, next) {
  var currentCollections = {};
  Array.prototype.slice.call(current.querySelectorAll("[data-console-keyed-list]")).forEach(function (collection) {
    var key = collection.getAttribute("data-console-keyed-list") || "";
    if (!currentCollections[key]) currentCollections[key] = [];
    currentCollections[key].push(collection);
  });
  Array.prototype.slice.call(next.querySelectorAll("[data-console-keyed-list]")).forEach(function (nextCollection) {
    var key = nextCollection.getAttribute("data-console-keyed-list") || "";
    var currentCollection = currentCollections[key] && currentCollections[key].shift();
    if (currentCollection) reconcileKeyedCollection(currentCollection, nextCollection);
  });
  Array.prototype.slice.call(next.querySelectorAll("[data-console-live-region]")).forEach(function (nextRegion) {
    var key = nextRegion.getAttribute("data-console-live-region") || "";
    var matches = Array.prototype.slice.call(current.querySelectorAll("[data-console-live-region]")).filter(function (region) {
      return region.getAttribute("data-console-live-region") === key;
    });
    var currentRegion = matches[0];
    if (!currentRegion) return;
    if (dashboardDomMismatchPath(currentRegion, nextRegion, "region:" + key)) {
      currentRegion.innerHTML = nextRegion.innerHTML;
    }
  });
}

function workDetailRegionDepth(region, root) {
  var depth = 0;
  var current = region;
  while (current && current !== root) {
    depth += 1;
    current = current.parentElement;
  }
  return depth;
}

function workDetailRegionByKey(root, key) {
  return Array.prototype.slice.call(root.querySelectorAll("[data-work-detail-region]")).filter(function (region) {
    return region.getAttribute("data-work-detail-region") === key;
  })[0] || null;
}

function reconcileWorkDetailRegions(currentBackdrop, nextBackdrop) {
  var nextRegions = Array.prototype.slice.call(nextBackdrop.querySelectorAll("[data-work-detail-region]"));
  var replaced = 0;
  nextRegions.sort(function (left, right) {
    return workDetailRegionDepth(right, nextBackdrop) - workDetailRegionDepth(left, nextBackdrop);
  }).forEach(function (nextRegion) {
    var key = nextRegion.getAttribute("data-work-detail-region") || "";
    var currentRegion = key ? workDetailRegionByKey(currentBackdrop, key) : null;
    if (!currentRegion || !dashboardDomMismatchPath(currentRegion, nextRegion, "work-detail-region:" + key)) return;
    var scrollTop = scrollTopNumber(currentRegion.scrollTop);
    var scrollLeft = scrollTopNumber(currentRegion.scrollLeft);
    if (currentRegion.querySelector("[data-ontology-cytoscape]")) destroyOntologyCytoscapeGraphs();
    currentRegion.innerHTML = nextRegion.innerHTML;
    currentRegion.scrollTop = scrollTop;
    currentRegion.scrollLeft = scrollLeft;
    bindAutoGrowingTextareas(currentRegion);
    bindActions(currentRegion);
    replaced += 1;
  });
  if (replaced) {
    dashboardRegionReplacementOccurredCell.value = true;
    var runtimePerformance = window.OrbitWebRuntime;
    if (runtimePerformance) runtimePerformance.record("render-region", 0, { region: "work-detail", count: replaced });
  }
  return replaced;
}

function reconcileWorkDetailLayer(current, next) {
  var currentBackdrop = current.querySelector("[data-work-detail-backdrop]");
  var nextBackdrop = next.querySelector("[data-work-detail-backdrop]");
  if (!currentBackdrop && !nextBackdrop) return;
  if (currentBackdrop && !nextBackdrop) {
    currentBackdrop.remove();
    dashboardRegionReplacementOccurredCell.value = true;
    return;
  }
  var nextIndex = Array.prototype.indexOf.call(next.children || [], nextBackdrop);
  if (!currentBackdrop && nextBackdrop) {
    var inserted = nextBackdrop.cloneNode(true);
    current.insertBefore(inserted, current.children[nextIndex] || null);
    bindAutoGrowingTextareas(inserted);
    bindActions(inserted);
    dashboardRegionReplacementOccurredCell.value = true;
    return;
  }
  var sameDetail = currentBackdrop.getAttribute("data-work-detail-type") === nextBackdrop.getAttribute("data-work-detail-type")
    && currentBackdrop.getAttribute("data-work-detail-key") === nextBackdrop.getAttribute("data-work-detail-key");
  if (sameDetail) {
    reconcileWorkDetailRegions(currentBackdrop, nextBackdrop);
    if (!dashboardDomMismatchPath(currentBackdrop, nextBackdrop, "work-detail")) {
      syncStableDashboardDom(currentBackdrop, nextBackdrop);
      return;
    }
  }
  var scrollTop = scrollTopNumber(currentBackdrop.scrollTop);
  if (currentBackdrop.querySelector("[data-ontology-cytoscape]")) destroyOntologyCytoscapeGraphs();
  var replacement = nextBackdrop.cloneNode(true);
  currentBackdrop.replaceWith(replacement);
  replacement.scrollTop = scrollTop;
  bindAutoGrowingTextareas(replacement);
  bindActions(replacement);
  dashboardRegionReplacementOccurredCell.value = true;
}

function replaceDashboardRegion(currentRegion, nextRegion) {
  var scrollTop = scrollTopNumber(currentRegion.scrollTop);
  var scrollLeft = scrollTopNumber(currentRegion.scrollLeft);
  if (currentRegion.querySelector && currentRegion.querySelector("[data-ontology-cytoscape]")) {
    destroyOntologyCytoscapeGraphs();
  }
  var replacement = nextRegion.cloneNode(true);
  currentRegion.replaceWith(replacement);
  replacement.scrollTop = scrollTop;
  replacement.scrollLeft = scrollLeft;
  bindAutoGrowingTextareas(replacement);
  bindActions(replacement);
  dashboardRegionReplacementOccurredCell.value = true;
  return replacement;
}

function reconcileDashboardMainRegion(current, next) {
  var currentMain = current.querySelector('[data-style-region="main"]');
  var nextMain = next.querySelector('[data-style-region="main"]');
  if (!currentMain || !nextMain) return;
  if (!dashboardDomMismatchPath(currentMain, nextMain, "main")) return;
  replaceDashboardRegion(currentMain, nextMain);
}

function reconcileOptionalDashboardRegion(current, next, selector) {
  var currentRegion = current.querySelector(selector);
  var nextRegion = next.querySelector(selector);
  if (!currentRegion && !nextRegion) return;
  if (currentRegion && !nextRegion) {
    currentRegion.remove();
    dashboardRegionReplacementOccurredCell.value = true;
    return;
  }
  if (!currentRegion && nextRegion) {
    var nextIndex = Array.prototype.indexOf.call(next.children || [], nextRegion);
    var inserted = nextRegion.cloneNode(true);
    current.insertBefore(inserted, current.children[nextIndex] || null);
    bindAutoGrowingTextareas(inserted);
    bindActions(inserted);
    dashboardRegionReplacementOccurredCell.value = true;
    return;
  }
  if (dashboardDomMismatchPath(currentRegion, nextRegion, "optional-region")) {
    replaceDashboardRegion(currentRegion, nextRegion);
  }
}

function syncStableDashboardDom(currentNode, nextNode) {
  if (currentNode.nodeType === 3 || currentNode.nodeType === 8) {
    if (currentNode.nodeValue !== nextNode.nodeValue) currentNode.nodeValue = nextNode.nodeValue;
    return;
  }
  if (currentNode.nodeType !== 1) return;
  Array.prototype.slice.call(currentNode.attributes || []).forEach(function (attribute) {
    if (!nextNode.hasAttribute(attribute.name)) currentNode.removeAttribute(attribute.name);
  });
  Array.prototype.slice.call(nextNode.attributes || []).forEach(function (attribute) {
    if (currentNode.getAttribute(attribute.name) !== attribute.value) currentNode.setAttribute(attribute.name, attribute.value);
  });
  for (var index = 0; index < currentNode.childNodes.length; index += 1) {
    syncStableDashboardDom(currentNode.childNodes[index], nextNode.childNodes[index]);
  }
}

function patchStableDashboardMarkup(markup) {
  var current = app.firstElementChild;
  if (!current || !current.classList || !current.classList.contains("console-shell")) return false;
  var template = document.createElement("template");
  template.innerHTML = String(markup || "").trim();
  var next = template.content.firstElementChild;
  if (!next || !next.classList || !next.classList.contains("console-shell")) return false;
  dashboardRegionReplacementOccurredCell.value = false;
  reconcileDashboardCollections(current, next);
  reconcileWorkDetailLayer(current, next);
  reconcileOptionalDashboardRegion(current, next, '[data-style-region="deskbar"]');
  reconcileDashboardMainRegion(current, next);
  var mismatch = dashboardDomMismatchPath(current, next);
  if (mismatch) {
    document.documentElement.setAttribute("data-dashboard-render-mode", "full:" + (mismatch || "structure"));
    return false;
  }
  syncStableDashboardDom(current, next);
  current.setAttribute("data-render-mode", "stable-patch");
  document.documentElement.setAttribute("data-dashboard-render-mode", "stable-patch");
  return true;
}

export { patchStableDashboardMarkup };
