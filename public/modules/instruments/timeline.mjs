import { accountIdOf, activeWatchAccount } from "../accounts/watchlist.mjs";
import { instrumentTimelineChartCell, instrumentTimelineChartFrameCell, instrumentTimelineChartObserverCell } from "./runtime.mjs";
import { openWorkDetailLayer } from "../navigation/detail.mjs";
import { viewLifetime } from "../navigation/lifecycle.mjs";
import { render } from "../render/scheduler.mjs";
import { activeJsonRequest } from "../requests/active.mjs";
import { requestJson } from "../requests/json.mjs";
import { app } from "../shell/root.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { instrumentsState } from "../state/instruments.mjs";

function instrumentWorkspaceTab(symbol) {
  var value = String((instrumentsState.instrumentWorkspaceTabs || {})[String(symbol || "").toUpperCase()] || "summary");
  return ["summary", "valuation", "chart", "decision", "timeline"].indexOf(value) >= 0 ? value : "summary";
}

function instrumentValuationCacheKey(symbol, requestedAccountId) {
  var accountId = requestedAccountId || accountIdOf(activeWatchAccount()) || "default";
  return accountId + ":" + String(symbol || "").toUpperCase().trim();
}

function instrumentValuationViewState(symbol) {
  var key = instrumentValuationCacheKey(symbol);
  return {
    key: key,
    payload: (instrumentsState.instrumentValuations || {})[key] || null,
    loading: Boolean((instrumentsState.instrumentValuationLoading || {})[key]),
    error: String((instrumentsState.instrumentValuationErrors || {})[key] || ""),
    staticPreview: isStaticPreviewHost()
  };
}

function loadInstrumentValuation(symbol, force) {
  var normalized = String(symbol || "").toUpperCase().trim();
  if (!normalized || isStaticPreviewHost()) return Promise.resolve(null);
  var key = instrumentValuationCacheKey(normalized);
  if (!force && instrumentsState.instrumentValuations[key]) return Promise.resolve(instrumentsState.instrumentValuations[key]);
  if (instrumentsState.instrumentValuationLoading[key]) return activeJsonRequest("instrument-valuation:" + key) || Promise.resolve(null);
  instrumentsState.instrumentValuationLoading[key] = true;
  instrumentsState.instrumentValuationErrors[key] = "";
  render();
  var accountId = accountIdOf(activeWatchAccount());
  var params = new URLSearchParams();
  if (accountId) params.set("accountId", accountId);
  var suffix = params.toString() ? "?" + params.toString() : "";
  return requestJson("/api/instruments/" + encodeURIComponent(normalized) + "/valuation" + suffix, {
    key: "instrument-valuation:" + key,
    timeoutMs: 16000,
    force: Boolean(force)
  }).then(function (payload) {
    instrumentsState.instrumentValuations[key] = payload;
    instrumentsState.instrumentValuationErrors[key] = "";
    return payload;
  }).catch(function (error) {
    instrumentsState.instrumentValuationErrors[key] = error.message || "기업가치 자료를 불러오지 못했습니다.";
    return null;
  }).finally(function () {
    instrumentsState.instrumentValuationLoading[key] = false;
    render();
  });
}

function instrumentTimelineRange(symbol) {
  var value = String((instrumentsState.instrumentTimelineRanges || {})[String(symbol || "").toUpperCase()] || "3m");
  return ["1d", "1w", "1m", "3m", "6m", "1y", "3y", "all"].indexOf(value) >= 0 ? value : "3m";
}

function instrumentTimelineCacheKey(symbol, range, requestedAccountId) {
  var accountId = requestedAccountId || accountIdOf(activeWatchAccount()) || "default";
  return encodeURIComponent(accountId) + ":" + String(symbol || "").toUpperCase() + ":" + String(range || "3m");
}

function currentInstrumentTimeline(symbol) {
  return (instrumentsState.instrumentTimelines || {})[instrumentTimelineCacheKey(symbol, instrumentTimelineRange(symbol))] || null;
}

function loadInstrumentTimeline(symbol, force, requestedAccountId) {
  var normalized = String(symbol || "").toUpperCase().trim();
  if (!normalized || isStaticPreviewHost()) return Promise.resolve(null);
  var range = instrumentTimelineRange(normalized);
  var accountId = requestedAccountId || accountIdOf(activeWatchAccount());
  var cacheKey = instrumentTimelineCacheKey(normalized, range, accountId);
  if (!force && instrumentsState.instrumentTimelines[cacheKey]) return Promise.resolve(instrumentsState.instrumentTimelines[cacheKey]);
  if (instrumentsState.instrumentTimelineLoading[cacheKey]) return activeJsonRequest("instrument-timeline:" + cacheKey) || Promise.resolve(null);
  instrumentsState.instrumentTimelineLoading[cacheKey] = true;
  instrumentsState.instrumentTimelineErrors[cacheKey] = "";
  render();
  var params = new URLSearchParams({ range: range });
  if (accountId) params.set("accountId", accountId);
  return requestJson("/api/instruments/" + encodeURIComponent(normalized) + "/timeline?" + params.toString(), {
    key: "instrument-timeline:" + cacheKey,
    timeoutMs: 16000,
    force: Boolean(force)
  }).then(function (payload) {
    instrumentsState.instrumentTimelines[cacheKey] = payload;
    instrumentsState.instrumentTimelineLastKeys[instrumentValuationCacheKey(normalized, accountId)] = cacheKey;
    instrumentsState.instrumentTimelineErrors[cacheKey] = "";
    return payload;
  }).catch(function (error) {
    var message = error.message || "종목 타임라인을 불러오지 못했습니다.";
    instrumentsState.instrumentTimelineErrors[cacheKey] = message.indexOf("API를 찾지 못했습니다") >= 0
      ? "현재 공유 서버가 타임라인 API를 아직 로드하지 못했습니다. 화면을 새로고침한 뒤 다시 조회하세요."
      : message;
    return null;
  }).finally(function () {
    instrumentsState.instrumentTimelineLoading[cacheKey] = false;
    render();
  });
}

var chartGeneration = 0;
var releaseChart = function () {};

function destroyInstrumentTimelineChart() {
  chartGeneration++;
  releaseChart();
  releaseChart = function () {};
  if (instrumentTimelineChartFrameCell.value) {
    if (window.cancelAnimationFrame) window.cancelAnimationFrame(instrumentTimelineChartFrameCell.value);
    else window.clearTimeout(instrumentTimelineChartFrameCell.value);
    instrumentTimelineChartFrameCell.value = 0;
  }
  if (instrumentTimelineChartObserverCell.value) {
    instrumentTimelineChartObserverCell.value.disconnect();
    instrumentTimelineChartObserverCell.value = null;
  }
  if (instrumentTimelineChartCell.value && instrumentTimelineChartCell.value.chart) {
    instrumentTimelineChartCell.value.chart.remove();
  }
  instrumentTimelineChartCell.value = null;
}

function instrumentEventEpochSeconds(value) {
  var raw = String(value || "").trim();
  if (/^\d{8}$/.test(raw)) {
    return Math.floor(Date.UTC(Number(raw.slice(0, 4)), Number(raw.slice(4, 6)) - 1, Number(raw.slice(6, 8))) / 1000);
  }
  var parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : NaN;
}

function nearestInstrumentCandleIndex(times, target) {
  if (!times.length) return -1;
  var low = 0;
  var high = times.length - 1;
  while (low <= high) {
    var middle = Math.floor((low + high) / 2);
    if (times[middle] === target) return middle;
    if (times[middle] < target) low = middle + 1;
    else high = middle - 1;
  }
  if (low <= 0) return 0;
  if (low >= times.length) return times.length - 1;
  return Math.abs(times[low] - target) < Math.abs(times[low - 1] - target) ? low : low - 1;
}

function instrumentChartEventProjection(events, candles, options) {
  options = options || {};
  var labels = { evidence: "뉴스", calendar: "일정", decision: "판단", hypothesis: "가설", notification: "알림" };
  var typePriority = { notification: 5, decision: 4, calendar: 3, hypothesis: 2, evidence: 1 };
  var tonePriority = { negative: 4, warning: 3, positive: 2, neutral: 1 };
  var typeColors = { evidence: "#2563a6", calendar: "#c6871a", decision: "#0f8f72", hypothesis: "#7c5cbf", notification: "#c9485b", event: "#64748b" };
  var times = (candles || []).map(function (item) {
    return instrumentEventEpochSeconds(item && item.time);
  }).filter(Number.isFinite).sort(function (left, right) { return left - right; });
  if (!times.length) return { markers: [], clusters: [], markerCount: 0, representedEventCount: 0, groupedEventCount: 0, labelCount: 0 };
  var buckets = {};
  (events || []).forEach(function (event) {
    var occurred = instrumentEventEpochSeconds(event && event.occurredAt);
    if (!Number.isFinite(occurred) || occurred < times[0] || occurred > times[times.length - 1]) return;
    var candleIndex = nearestInstrumentCandleIndex(times, occurred);
    if (candleIndex < 0) return;
    var candleTime = times[candleIndex];
    var key = String(candleTime);
    if (!buckets[key]) {
      buckets[key] = { time: candleTime, candleIndex: candleIndex, events: [], typeCounts: {}, toneCounts: {} };
    }
    var type = labels[event.type] ? event.type : "event";
    var tone = tonePriority[event.tone] ? event.tone : "neutral";
    buckets[key].events.push(event);
    buckets[key].typeCounts[type] = (buckets[key].typeCounts[type] || 0) + 1;
    buckets[key].toneCounts[tone] = (buckets[key].toneCounts[tone] || 0) + 1;
  });
  var clusters = Object.keys(buckets).map(function (key) {
    var bucket = buckets[key];
    var types = Object.keys(bucket.typeCounts).sort(function (left, right) {
      return bucket.typeCounts[right] - bucket.typeCounts[left]
        || (typePriority[right] || 0) - (typePriority[left] || 0)
        || left.localeCompare(right);
    });
    var tones = Object.keys(bucket.toneCounts).sort(function (left, right) {
      return (tonePriority[right] || 0) - (tonePriority[left] || 0)
        || bucket.toneCounts[right] - bucket.toneCounts[left];
    });
    bucket.primaryType = types[0] || "event";
    bucket.primaryTone = tones[0] || "neutral";
    bucket.count = bucket.events.length;
    bucket.label = (labels[bucket.primaryType] || "사건") + (bucket.count > 1 ? " " + bucket.count : "");
    bucket.labelScore = bucket.count * 10 + (tonePriority[bucket.primaryTone] || 0) * 4 + (typePriority[bucket.primaryType] || 0);
    return bucket;
  }).sort(function (left, right) { return left.time - right.time; });
  var chartWidth = Math.max(280, Number(options.chartWidth || 720));
  var labelCapacity = Math.max(4, Math.floor(chartWidth / 64));
  var labelGap = Math.max(1, Math.ceil(times.length / labelCapacity));
  var labelWinners = {};
  clusters.forEach(function (cluster) {
    var segment = Math.floor(cluster.candleIndex / labelGap);
    var selected = labelWinners[segment];
    if (!selected || cluster.labelScore > selected.labelScore || (cluster.labelScore === selected.labelScore && cluster.time > selected.time)) {
      labelWinners[segment] = cluster;
    }
  });
  var markers = clusters.map(function (cluster) {
    var shape = cluster.primaryType === "calendar"
      ? "square"
      : cluster.primaryType === "decision"
        ? (cluster.primaryTone === "negative" ? "arrowDown" : "arrowUp")
        : "circle";
    return {
      id: "instrument-event-cluster:" + cluster.time,
      time: cluster.time,
      position: cluster.primaryTone === "negative" ? "belowBar" : "aboveBar",
      color: typeColors[cluster.primaryType] || typeColors.event,
      shape: shape,
      size: cluster.count >= 8 ? 1.6 : cluster.count >= 3 ? 1.3 : 1,
      text: labelWinners[Math.floor(cluster.candleIndex / labelGap)] === cluster ? cluster.label : ""
    };
  });
  var representedEventCount = clusters.reduce(function (total, cluster) { return total + cluster.count; }, 0);
  return {
    markers: markers,
    clusters: clusters,
    markerCount: markers.length,
    representedEventCount: representedEventCount,
    groupedEventCount: Math.max(0, representedEventCount - markers.length),
    labelCount: markers.filter(function (marker) { return Boolean(marker.text); }).length
  };
}

function instrumentChartEventMarkers(events, candles, options) {
  return instrumentChartEventProjection(events, candles, options).markers;
}

function instrumentEventDetailTarget(event) {
  var type = String(event && event.detailType || "").trim();
  var key = String(event && event.detailKey || "").trim();
  return type && key ? { type: type, key: key } : null;
}

function instrumentEventGroupKey(payloadKey, selector) {
  return String(payloadKey || "") + "|" + String(selector || "");
}

function parseInstrumentEventGroupKey(key) {
  var value = String(key || "");
  var separator = value.lastIndexOf("|");
  if (separator <= 0 || separator >= value.length - 1) return null;
  var payloadKey = value.slice(0, separator);
  var selector = value.slice(separator + 1);
  var cacheSeparator = payloadKey.lastIndexOf(":");
  if (cacheSeparator <= 0 || cacheSeparator >= payloadKey.length - 1) return null;
  var accountSeparator = payloadKey.lastIndexOf(":", cacheSeparator - 1);
  var accountId = "";
  try { accountId = accountSeparator >= 0 ? decodeURIComponent(payloadKey.slice(0, accountSeparator)) : ""; } catch (error) { return null; }
  var symbol = payloadKey.slice(accountSeparator + 1, cacheSeparator);
  var range = payloadKey.slice(cacheSeparator + 1);
  return {
    payloadKey: instrumentTimelineCacheKey(symbol, range, accountId),
    accountId: accountId,
    symbol: symbol,
    range: range,
    selector: selector
  };
}

function ensureInstrumentEventGroupTimeline(key) {
  var parsed = parseInstrumentEventGroupKey(key);
  if (!parsed) return;
  instrumentsState.instrumentTimelineRanges[parsed.symbol] = parsed.range;
  if (!instrumentsState.instrumentTimelines[parsed.payloadKey]
    && !instrumentsState.instrumentTimelineLoading[parsed.payloadKey]
    && !instrumentsState.instrumentTimelineErrors[parsed.payloadKey]) {
    loadInstrumentTimeline(parsed.symbol, false, parsed.accountId);
  }
}

function instrumentEventGroupRows(payloadKey, selector) {
  var parsed = parseInstrumentEventGroupKey(instrumentEventGroupKey(payloadKey, selector));
  if (parsed) payloadKey = parsed.payloadKey;
  var payload = (instrumentsState.instrumentTimelines || {})[String(payloadKey || "")];
  if (!payload) return [];
  var events = Array.isArray(payload.events) ? payload.events : [];
  if (String(selector || "").indexOf("type:") === 0) {
    var type = String(selector).slice(5);
    return events.filter(function (event) { return event.type === type; });
  }
  if (String(selector || "").indexOf("time:") === 0) {
    var target = Number(String(selector).slice(5));
    var candles = payload.series && Array.isArray(payload.series.candles) ? payload.series.candles : [];
    var projection = instrumentChartEventProjection(events, candles, { chartWidth: 720 });
    var cluster = (projection.clusters || []).filter(function (item) { return item.time === target; })[0];
    return cluster ? cluster.events : [];
  }
  return [];
}

function openInstrumentEventGroup(payloadKey, selector) {
  var events = instrumentEventGroupRows(payloadKey, selector);
  if (!events.length) {
    showSnackbar("연결된 사건 상세를 찾지 못했습니다.", "caution");
    return;
  }
  var target = events.length === 1 ? instrumentEventDetailTarget(events[0]) : null;
  if (target) {
    openWorkDetailLayer(target.type, target.key);
    return;
  }
  openWorkDetailLayer("instrument-event-group", instrumentEventGroupKey(payloadKey, selector));
}

function instrumentChartHoveredMarkerId(param) {
  var hovered = param && param.hoveredInfo;
  if (hovered && hovered.objectKind === "series-marker" && hovered.objectId) return String(hovered.objectId);
  return String(param && param.hoveredObjectId || "");
}

function initInstrumentTimelineChart() {
  var container = app.querySelector("[data-instrument-candle-chart]");
  if (!container) {
    destroyInstrumentTimelineChart();
    return;
  }
  if (!window.LightweightCharts) {
    var runtime = window.OrbitWebRuntime;
    if (runtime && typeof runtime.loadScriptOnce === "function") {
      container.setAttribute("aria-busy", "true");
      runtime.loadScriptOnce("vendor/lightweight-charts.standalone.production.js?v=5.2.1", "LightweightCharts")
        .then(function () {
          if (container.isConnected) {
            container.removeAttribute("aria-busy");
            initInstrumentTimelineChart();
          }
        })
        .catch(function () {
          if (container.isConnected) {
            container.removeAttribute("aria-busy");
            container.innerHTML = '<span class="empty-copy">차트 엔진을 불러오지 못했습니다.</span>';
          }
        });
    }
    return;
  }
  var payloadKey = container.getAttribute("data-instrument-candle-chart") || "";
  var payload = (instrumentsState.instrumentTimelines || {})[payloadKey];
  var candles = payload && payload.series && Array.isArray(payload.series.candles) ? payload.series.candles : [];
  if (!candles.length) {
    destroyInstrumentTimelineChart();
    return;
  }
  var existing = instrumentTimelineChartCell.value;
  var theme = document.documentElement.getAttribute("data-theme");
  if (existing && existing.container === container && existing.payload === payload && existing.theme === theme) return;
  destroyInstrumentTimelineChart();
  var generation = chartGeneration;
  var currentView = viewLifetime.capture();
  releaseChart = viewLifetime.own(destroyInstrumentTimelineChart);
  var create = function () {
    instrumentTimelineChartFrameCell.value = 0;
    if (!container.isConnected || !currentView() || generation !== chartGeneration) return;
    var styles = window.getComputedStyle(document.documentElement);
    var chart = window.LightweightCharts.createChart(container, {
      width: Math.max(280, container.clientWidth),
      height: Math.max(320, container.clientHeight || 380),
      layout: {
        background: { type: "solid", color: styles.getPropertyValue("--panel").trim() || "#ffffff" },
        textColor: styles.getPropertyValue("--muted").trim() || "#667085",
        fontFamily: "Inter, Pretendard, -apple-system, BlinkMacSystemFont, sans-serif"
      },
      grid: {
        vertLines: { color: styles.getPropertyValue("--line").trim() || "#e4e7ec" },
        horzLines: { color: styles.getPropertyValue("--line").trim() || "#e4e7ec" }
      },
      rightPriceScale: { borderColor: styles.getPropertyValue("--line").trim() || "#e4e7ec" },
      timeScale: {
        borderColor: styles.getPropertyValue("--line").trim() || "#e4e7ec",
        timeVisible: String((payload.query || {}).interval || "1d") !== "1d",
        secondsVisible: false,
        rightOffset: 3
      },
      crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal }
    });
    var candleSeries = chart.addSeries(window.LightweightCharts.CandlestickSeries, {
      upColor: "#0f8f72",
      downColor: "#c9485b",
      wickUpColor: "#0f8f72",
      wickDownColor: "#c9485b",
      borderVisible: false
    });
    var candleData = candles.map(function (item) {
      return {
        time: Math.floor(Date.parse(item.time || "") / 1000),
        open: Number(item.open || item.close || 0),
        high: Number(item.high || item.close || 0),
        low: Number(item.low || item.close || 0),
        close: Number(item.close || 0)
      };
    }).filter(function (item) { return Number.isFinite(item.time) && item.close > 0; });
    candleSeries.setData(candleData);
    var volumeSeries = chart.addSeries(window.LightweightCharts.HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      lastValueVisible: false,
      priceLineVisible: false
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volumeSeries.setData(candles.map(function (item) {
      return {
        time: Math.floor(Date.parse(item.time || "") / 1000),
        value: Number(item.volume || 0),
        color: Number(item.close || 0) >= Number(item.open || item.close || 0) ? "rgba(15,143,114,.38)" : "rgba(201,72,91,.34)"
      };
    }).filter(function (item) { return Number.isFinite(item.time); }));
    var markers = instrumentChartEventMarkers(payload.events || [], candles, { chartWidth: container.clientWidth });
    if (markers.length && window.LightweightCharts.createSeriesMarkers) {
      window.LightweightCharts.createSeriesMarkers(candleSeries, markers);
    }
    chart.subscribeCrosshairMove(function (param) {
      var markerId = instrumentChartHoveredMarkerId(param);
      container.classList.toggle("has-event-target", markerId.indexOf("instrument-event-cluster:") === 0);
    });
    chart.subscribeClick(function (param) {
      var markerId = instrumentChartHoveredMarkerId(param);
      if (markerId.indexOf("instrument-event-cluster:") !== 0) return;
      openInstrumentEventGroup(payloadKey, "time:" + markerId.slice("instrument-event-cluster:".length));
    });
    chart.timeScale().fitContent();
    instrumentTimelineChartCell.value = { chart: chart, container: container, payloadKey: payloadKey, payload: payload, theme: theme };
    if (window.ResizeObserver) {
      instrumentTimelineChartObserverCell.value = new window.ResizeObserver(function () {
        if (container.isConnected && generation === chartGeneration) chart.applyOptions({ width: Math.max(280, container.clientWidth) });
      });
      instrumentTimelineChartObserverCell.value.observe(container);
    }
  };
  instrumentTimelineChartFrameCell.value = window.requestAnimationFrame ? window.requestAnimationFrame(create) : window.setTimeout(create, 0);
}

export { destroyInstrumentTimelineChart, ensureInstrumentEventGroupTimeline, initInstrumentTimelineChart, instrumentChartEventProjection, instrumentEventDetailTarget, instrumentEventGroupRows, instrumentTimelineCacheKey, instrumentTimelineRange, instrumentValuationCacheKey, instrumentValuationViewState, instrumentWorkspaceTab, loadInstrumentTimeline, loadInstrumentValuation, openInstrumentEventGroup, parseInstrumentEventGroupKey };
