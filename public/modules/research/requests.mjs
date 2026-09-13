import { render } from "../render/scheduler.mjs";
import { clearReadModelPoll, readModelIsWarming, requestJson, scheduleReadModelPoll } from "../requests/json.mjs";
import { createLatestRequestLane } from "../requests/latest.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { DEFAULT_RESEARCH_EVIDENCE_LIMIT } from "./constants.mjs";
import { formatClock } from "../shared/format.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { navigationState } from "../state/navigation.mjs";
import { researchState } from "../state/research.mjs";
import { shellState } from "../state/shell.mjs";
import { loadCachedPayload, writeCachedPayload } from "../state/storage.mjs";
import { universeState } from "../state/universe.mjs";

var researchEvidenceMemoryStore = "";

function feedTimeValue(value) {
  var raw = String(value || "");
  var compact = raw.replace(/\D/g, "");
  if (compact.length >= 14) {
    return Date.UTC(
      Number(compact.slice(0, 4)),
      Number(compact.slice(4, 6)) - 1,
      Number(compact.slice(6, 8)),
      Number(compact.slice(8, 10)),
      Number(compact.slice(10, 12)),
      Number(compact.slice(12, 14))
    );
  }
  var parsed = Date.parse(raw);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function formatFeedTime(value) {
  var raw = String(value || "");
  var compact = raw.replace(/\D/g, "");
  if (compact.length >= 14) {
    return compact.slice(4, 6) + "." + compact.slice(6, 8) + " " + compact.slice(8, 10) + ":" + compact.slice(10, 12);
  }
  return formatClock(value);
}

function currentResearchEvidence() {
  return researchState.researchEvidence || {
    items: [],
    summary: { total: 0, latestSeenAt: "", bySymbol: [], byKind: [], bySource: [], byPolarity: [] },
    symbol: "",
    kind: "",
    limit: Number(researchState.researchEvidenceFilters.limit || DEFAULT_RESEARCH_EVIDENCE_LIMIT)
  };
}

function resolveResearchEvidenceSymbol(value) {
  var raw = String(value || "").trim();
  if (!raw) return "";
  var normalized = raw.toUpperCase();
  if (/^[A-Z0-9.\-]+$/.test(normalized)) return normalized;
  var candidates = [];
  var toss = (shellState.snapshot || {}).toss || {};
  (Array.isArray(toss.positions) ? toss.positions : []).forEach(function (item) {
    candidates.push(item);
  });
  (Array.isArray(toss.watchlist) ? toss.watchlist : []).forEach(function (item) {
    candidates.push(item);
  });
  (Array.isArray((universeState.symbolUniverse || {}).items) ? universeState.symbolUniverse.items : []).forEach(function (item) {
    candidates.push(item);
  });
  var exact = candidates.filter(function (item) {
    return String(item && (item.name || item.symbolName || item.displayName) || "").trim() === raw;
  })[0];
  if (exact && exact.symbol) return String(exact.symbol).toUpperCase();
  var partial = candidates.filter(function (item) {
    return String(item && (item.name || item.symbolName || item.displayName) || "").indexOf(raw) >= 0;
  })[0];
  return partial && partial.symbol ? String(partial.symbol).toUpperCase() : normalized;
}

function researchEvidenceQueryString() {
  var filters = researchState.researchEvidenceFilters || {};
  var params = new URLSearchParams();
  var symbol = resolveResearchEvidenceSymbol(filters.symbol);
  var kind = String(filters.kind || "").trim();
  var limit = String(filters.limit || DEFAULT_RESEARCH_EVIDENCE_LIMIT).trim();
  if (symbol) params.set("symbol", symbol);
  if (kind) params.set("kind", kind);
  if (limit) params.set("limit", limit);
  var text = params.toString();
  return text ? "?" + text : "";
}

function staticResearchEvidencePayload(reason) {
  var stamped = new Date().toISOString();
  return {
    items: [
      {
        evidenceId: "preview:005930:news",
        symbol: "005930",
        kind: "news",
        source: "Static Preview",
        title: "반도체 업황 개선 기대",
        summary: reason || "정적 미리보기에서는 저장된 리서치 근거 예시를 보여줍니다.",
        url: "",
        observedAt: stamped,
        publishedAt: stamped,
        polarity: "support",
        materialityState: "notable",
        relevanceState: "direct",
        sourceTrustState: "standard",
        dataState: "partial",
        payload: { name: "삼성전자", materialityState: "notable", relevanceState: "direct", sourceTrustState: "standard" }
      }
    ],
    summary: {
      total: 1,
      latestSeenAt: stamped,
      bySymbol: [{ name: "005930", count: 1, latestSeenAt: stamped }],
      byKind: [{ name: "news", count: 1, latestSeenAt: stamped }],
      bySource: [{ name: "Static Preview", count: 1, latestSeenAt: stamped }],
      byPolarity: [{ name: "support", count: 1, latestSeenAt: stamped }]
    },
    symbol: "005930",
    kind: "",
    limit: Number(DEFAULT_RESEARCH_EVIDENCE_LIMIT),
    preview: true
  };
}

function loadCachedResearchEvidence() {
  return loadCachedPayload("orbitAlphaResearchEvidence", researchEvidenceMemoryStore);
}

function writeCachedResearchEvidence(payload) {
  return writeCachedPayload("orbitAlphaResearchEvidence", payload, function (serialized) {
    researchEvidenceMemoryStore = serialized;
  });
}

const researchLane = createLatestRequestLane();

function loadResearchEvidence(force) {
  var identity = researchEvidenceQueryString();
  var active = researchLane.active(identity);
  if (active && !force) return active;
  if (researchState.researchEvidence && !force) return Promise.resolve(researchState.researchEvidence);
  var operation = researchLane.begin(identity);
  var cached = loadCachedResearchEvidence();
  var requestedLimit = Math.max(1, Math.min(500, Number(researchState.researchEvidenceFilters.limit || DEFAULT_RESEARCH_EVIDENCE_LIMIT)));
  if (cached && !researchState.researchEvidence) {
    researchState.researchEvidence = Object.assign({}, cached, {
      items: Array.isArray(cached.items) ? cached.items.slice(0, requestedLimit) : [],
      limit: requestedLimit
    });
    render();
  }
  researchState.researchEvidenceLoading = true;
  researchState.researchEvidenceError = "";
  render();

  var evidencePath = "/api/market/evidence" + researchEvidenceQueryString();
  if (force) evidencePath += (evidencePath.indexOf("?") >= 0 ? "&" : "?") + "refresh=1";
  var promise = isStaticPreviewHost()
    ? Promise.resolve(staticResearchEvidencePayload("정적 미리보기"))
    : requestJson(evidencePath, {
      key: "research-evidence-list",
      force: Boolean(force),
      cacheTtlMs: 15000,
      timeoutMs: 15000
    });

  return operation.track(promise
    .then(function (payload) {
      if (!operation.current() || researchEvidenceQueryString() !== identity) return;
      var warming = readModelIsWarming(payload);
      researchState.researchEvidence = warming && cached && Array.isArray(cached.items)
        ? cached
        : payload;
      researchState.researchEvidenceError = "";
      if (!warming) writeCachedResearchEvidence(payload);
      if (warming) {
        scheduleReadModelPoll("research-evidence-list", function () { loadResearchEvidence(true); });
      } else {
        clearReadModelPoll("research-evidence-list");
      }
    })
    .catch(function (error) {
      if (!operation.current() || researchEvidenceQueryString() !== identity) return;
      researchState.researchEvidenceError = error.message || "저장된 근거를 불러오지 못했습니다.";
      if (!researchState.researchEvidence && cached) researchState.researchEvidence = cached;
    })
    .finally(function () {
      if (!operation.current()) return;
      researchState.researchEvidenceLoading = false;
      operation.finish();
      render();
    }));
}

function loadResearchEvidenceDetail(evidenceId) {
  var key = String(evidenceId || "").trim();
  var cached = researchState.researchEvidenceDetails[key];
  if (!key || (cached && Date.now() - Number(cached.detailLoadedAt || 0) < 60000) || isStaticPreviewHost()) return Promise.resolve();
  return requestJson("/api/research-evidence/" + encodeURIComponent(key), { key: "research-evidence:" + key, cacheTtlMs: 60000 })
    .then(function (payload) {
      var item = payload && payload.item;
      if (!item) return;
      item.detailLoadedAt = Date.now();
      researchState.researchEvidenceDetails[key] = item;
      if (researchState.researchEvidence && Array.isArray(researchState.researchEvidence.items)) {
        researchState.researchEvidence.items = researchState.researchEvidence.items.map(function (row) {
          return String(row.evidenceId || row.id || "") === key ? Object.assign({}, row, item) : row;
        });
      }
    })
    .catch(function () { return null; })
    .finally(function () {
      if (researchState.expandedResearchEvidenceKey === key || (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "research-evidence" && navigationState.workDetailLayer.key === key)) render();
    });
}

function deleteResearchEvidence(evidenceId) {
  var id = String(evidenceId || "").trim();
  if (!id || researchState.researchEvidenceDeleting) return Promise.resolve();
  if (window.confirm && !window.confirm("선택한 리서치 근거를 삭제할까요?")) return Promise.resolve();
  researchState.researchEvidenceDeleting = id;
  researchState.researchEvidenceError = "";
  render();
  return sendJson("/api/research-evidence/" + encodeURIComponent(id) + researchEvidenceQueryString(), "DELETE", {})
    .then(function (payload) {
      researchState.researchEvidence = payload;
      writeCachedResearchEvidence(payload);
      showSnackbar(payload.deleted ? "리서치 근거를 삭제했습니다." : "삭제할 근거를 찾지 못했습니다.", payload.deleted ? "success" : "danger");
    })
    .catch(function (error) {
      researchState.researchEvidenceError = error.message || "리서치 근거를 삭제하지 못했습니다.";
      showSnackbar(researchState.researchEvidenceError, "danger");
    })
    .finally(function () {
      researchState.researchEvidenceDeleting = "";
      render();
    });
}

function revalidateResearchEvidence() {
  if (researchState.researchEvidenceRevalidating || isStaticPreviewHost()) return Promise.resolve();
  researchState.researchEvidenceRevalidating = true;
  researchState.researchEvidenceError = "";
  render();
  return sendJson("/api/research-evidence/revalidate", "POST", {
    symbol: String((researchState.researchEvidenceFilters || {}).symbol || "").trim(),
    limit: 500
  })
    .then(function (payload) {
      var quality = payload && payload.claimQuality || {};
      showSnackbar("근거 검증 상태를 갱신했습니다. " + Number(quality.ungovernedEvidenceCount || 0) + "건 미검증", quality.alertState === "degraded" ? "danger" : "success");
      researchState.researchEvidence = null;
      return loadResearchEvidence(true);
    })
    .catch(function (error) {
      researchState.researchEvidenceError = error.message || "리서치 근거 검증 상태를 갱신하지 못했습니다.";
      showSnackbar(researchState.researchEvidenceError, "danger");
    })
    .finally(function () {
      researchState.researchEvidenceRevalidating = false;
      render();
    });
}

export { currentResearchEvidence, deleteResearchEvidence, feedTimeValue, formatFeedTime, loadResearchEvidence, loadResearchEvidenceDetail, revalidateResearchEvidence };
