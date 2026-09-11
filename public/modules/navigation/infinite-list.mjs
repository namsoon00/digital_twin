import { createInfiniteListObserver } from "./infinite-observer.mjs";
import { mobileInfiniteScrollModeCell, mobileInfiniteScrollObserverCell } from "./infinite-runtime.mjs";
import { viewLifetime } from "./lifecycle.mjs";
import { currentWorkspaceScroller } from "./scroll.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { app } from "../shell/root.mjs";

function mobileInfiniteScrollEnabled() {
  if (typeof window === "undefined") return false;
  if (window.matchMedia) return window.matchMedia("(max-width: 860px)").matches;
  return Number(window.innerWidth || 0) > 0 && Number(window.innerWidth || 0) <= 860;
}

function mobileInfiniteScrollPrefetchDistance() {
  var viewportHeight = Math.max(0, Number((typeof window !== "undefined" && window.innerHeight) || 0));
  return Math.round(Math.max(1400, Math.min(2600, viewportHeight * 2.25)));
}

function mergeUniqueItems(existing, incoming, keyResolver) {
  var rows = [];
  var seen = Object.create(null);
  (Array.isArray(existing) ? existing : []).concat(Array.isArray(incoming) ? incoming : []).forEach(function (item, index) {
    var key = keyResolver ? keyResolver(item, index) : "";
    key = String(key || "item:" + index);
    if (seen[key]) return;
    seen[key] = true;
    rows.push(item);
  });
  return rows;
}

function renderMobileInfiniteScrollFooter(options) {
  options = options || {};
  var loaded = Math.max(0, Number(options.loaded || 0));
  var total = Math.max(loaded, Number(options.total || 0));
  var loading = Boolean(options.loading);
  var hasNext = Boolean(options.hasNext);
  var requestable = hasNext && !loading;
  var status = loading
    ? "다음 데이터를 불러오는 중입니다"
    : (hasNext ? loaded + " / " + total + "개 표시" : (total ? total + "개를 모두 불러왔습니다" : "표시할 데이터가 없습니다"));
  return [
    '<div class="mobile-infinite-scroll' + (loading ? " is-loading" : (hasNext ? "" : " is-complete")) + '" data-mobile-infinite-sentinel data-mobile-infinite-prefetch="next-page" aria-live="polite">',
    '<span>' + escapeHtml(status) + '</span>',
    '<button class="mini-button" type="button" data-mobile-infinite-next ' + (requestable ? String(options.nextAttributes || "") : 'disabled') + (!hasNext ? ' hidden aria-hidden="true"' : '') + '>' + escapeHtml(loading ? "불러오는 중" : "더 불러오기") + '</button>',
    '</div>'
  ].join("");
}

var releaseObserver = function () {};

function disconnectMobileInfiniteScroll() {
  releaseObserver();
  releaseObserver = function () {};
  if (mobileInfiniteScrollObserverCell.value) {
    mobileInfiniteScrollObserverCell.value.disconnect();
    mobileInfiniteScrollObserverCell.value = null;
  }
}

function bindMobileInfiniteScroll() {
  disconnectMobileInfiniteScroll();
  mobileInfiniteScrollModeCell.value = mobileInfiniteScrollEnabled();
  if (!mobileInfiniteScrollModeCell.value || typeof window.IntersectionObserver !== "function") return;
  var sentinels = Array.prototype.slice.call(app.querySelectorAll("[data-mobile-infinite-sentinel]"));
  if (!sentinels.length) return;
  var currentView = viewLifetime.capture();
  mobileInfiniteScrollObserverCell.value = createInfiniteListObserver({
    Observer: window.IntersectionObserver,
    root: currentWorkspaceScroller(),
    prefetchDistance: mobileInfiniteScrollPrefetchDistance(),
    contains: function (sentinel) { return currentView() && app.contains(sentinel); },
    requestNext: function (sentinel, button) {
      if (sentinel.getAttribute("data-mobile-infinite-requested") === "true") return;
      sentinel.setAttribute("data-mobile-infinite-requested", "true");
      button.click();
      sentinel.classList.add("is-prefetching");
      button.disabled = true;
      button.textContent = "다음 페이지 준비 중";
    }
  });
  mobileInfiniteScrollObserverCell.value.observe(sentinels);
  releaseObserver = viewLifetime.own(disconnectMobileInfiniteScroll);
}

export { bindMobileInfiniteScroll, disconnectMobileInfiniteScroll, mergeUniqueItems, mobileInfiniteScrollEnabled, renderMobileInfiniteScrollFooter };
