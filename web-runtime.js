(function (global) {
  "use strict";

  var SAMPLE_LIMIT = 120;
  var samples = [];
  var active = {};
  var resourcePromises = {};
  var sequence = 0;

  function clock() {
    if (global.performance && typeof global.performance.now === "function") {
      return global.performance.now();
    }
    return Date.now();
  }

  function finiteNumber(value) {
    var number = Number(value || 0);
    return Number.isFinite(number) ? number : 0;
  }

  function round(value) {
    return Math.round(finiteNumber(value) * 100) / 100;
  }

  function publish(sample) {
    samples.push(sample);
    if (samples.length > SAMPLE_LIMIT) samples.splice(0, samples.length - SAMPLE_LIMIT);
    if (!global.document || !global.document.documentElement) return sample;
    var root = global.document.documentElement;
    root.setAttribute("data-last-web-operation", sample.name);
    root.setAttribute("data-last-web-duration-ms", String(sample.durationMs));
    if (sample.name === "render") root.setAttribute("data-last-render-ms", String(sample.durationMs));
    if (sample.name === "api-request") {
      root.setAttribute("data-last-api-duration-ms", String(sample.durationMs));
      root.setAttribute("data-last-api-path", String((sample.metadata || {}).path || ""));
    }
    return sample;
  }

  function loadScriptOnce(src, globalName) {
    var key = String(src || "").trim();
    if (!key) return Promise.reject(new Error("불러올 스크립트 주소가 없습니다."));
    if (globalName && global[globalName]) return Promise.resolve(global[globalName]);
    if (resourcePromises[key]) return resourcePromises[key];
    resourcePromises[key] = new Promise(function (resolve, reject) {
      if (!global.document || !global.document.head) {
        reject(new Error("스크립트를 불러올 문서가 없습니다."));
        return;
      }
      var existing = global.document.querySelector('script[data-orbit-resource="' + key.replace(/"/g, "&quot;") + '"]');
      if (existing) {
        existing.addEventListener("load", function () { resolve(globalName ? global[globalName] : true); }, { once: true });
        existing.addEventListener("error", function () { reject(new Error("화면 리소스를 불러오지 못했습니다.")); }, { once: true });
        return;
      }
      var script = global.document.createElement("script");
      script.src = key;
      script.async = true;
      script.setAttribute("data-orbit-resource", key);
      script.onload = function () { resolve(globalName ? global[globalName] : true); };
      script.onerror = function () {
        delete resourcePromises[key];
        reject(new Error("화면 리소스를 불러오지 못했습니다."));
      };
      global.document.head.appendChild(script);
    });
    return resourcePromises[key];
  }

  function begin(name, metadata) {
    sequence += 1;
    var id = String(name || "operation") + ":" + sequence;
    active[id] = {
      id: id,
      name: String(name || "operation"),
      startedAt: clock(),
      metadata: metadata && typeof metadata === "object" ? metadata : {}
    };
    return id;
  }

  function end(id, metadata) {
    var item = active[id];
    if (!item) return null;
    delete active[id];
    return publish({
      name: item.name,
      durationMs: round(clock() - item.startedAt),
      recordedAt: new Date().toISOString(),
      metadata: Object.assign({}, item.metadata, metadata && typeof metadata === "object" ? metadata : {})
    });
  }

  function record(name, durationMs, metadata) {
    return publish({
      name: String(name || "operation"),
      durationMs: round(durationMs),
      recordedAt: new Date().toISOString(),
      metadata: metadata && typeof metadata === "object" ? metadata : {}
    });
  }

  function percentile(values, value) {
    var ordered = values.slice().sort(function (left, right) { return left - right; });
    if (!ordered.length) return 0;
    var index = Math.max(0, Math.min(ordered.length - 1, Math.ceil((value / 100) * ordered.length) - 1));
    return round(ordered[index]);
  }

  function snapshot() {
    var groups = {};
    samples.forEach(function (sample) {
      if (!groups[sample.name]) groups[sample.name] = [];
      groups[sample.name].push(sample.durationMs);
    });
    return {
      version: "web-runtime-performance-v1",
      samples: samples.slice(),
      summary: Object.keys(groups).sort().map(function (name) {
        var values = groups[name];
        var total = values.reduce(function (sum, item) { return sum + item; }, 0);
        return {
          name: name,
          sampleCount: values.length,
          averageMs: round(total / Math.max(1, values.length)),
          p50Ms: percentile(values, 50),
          p95Ms: percentile(values, 95),
          maxMs: round(Math.max.apply(Math, values))
        };
      })
    };
  }

  global.OrbitWebRuntime = Object.freeze({
    begin: begin,
    end: end,
    now: clock,
    record: record,
    snapshot: snapshot,
    loadScriptOnce: loadScriptOnce
  });
}(window));
