import { accountIdOf, activeWatchAccount } from "../accounts/watchlist.mjs";
import { activeSectionForPageMode, normalizeStrategySection } from "../navigation/routes.mjs";
import { ontologyStrategyParts } from "./strategy.mjs";
import { ontologyReadableInferenceRows, ontologyReadableRuleRows } from "./world.mjs";
import { render } from "../render/scheduler.mjs";
import { clearReadModelPoll, readModelIsWarming, requestJson, scheduleReadModelPoll } from "../requests/json.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { latestChangedFirst } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { strategySections } from "../shell/catalog.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { tossLensPath } from "../snapshot/requests.mjs";
import { accountsState } from "../state/accounts.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { navigationState } from "../state/navigation.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { proposalsState } from "../state/proposals.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";

function loadOntologyRulebox(force) {
  if (isStaticPreviewHost()) return Promise.resolve(null);
  if (ontologyState.ontologyRuleboxLoading && !force) return Promise.resolve(ontologyState.ontologyRulebox);
  ontologyState.ontologyRuleboxLoading = true;
  ontologyState.ontologyRuleboxError = "";
  return requestJson("/api/ontology/rulebox" + (force ? "?refresh=1" : ""), {
    key: "ontology-rulebox",
    force: Boolean(force),
    cacheTtlMs: 30000,
    timeoutMs: 30000
  })
    .then(function (payload) {
      applyOntologyRuleboxPayload(payload);
      ontologyState.ontologyRuleboxLoaded = true;
      if (readModelIsWarming(payload)) {
        scheduleReadModelPoll("ontology-rulebox", function () { loadOntologyRulebox(true); });
      } else {
        clearReadModelPoll("ontology-rulebox");
      }
      return payload;
    })
    .catch(function (error) {
      ontologyState.ontologyRuleboxError = error.message || "TypeDB RuleBox를 읽지 못했습니다.";
      return null;
    })
    .finally(function () {
      ontologyState.ontologyRuleboxLoading = false;
      if (shellState.snapshot) render();
    });
}

function ontologyCatalogFilter(section) {
  var key = String(section || ontologyState.activeOntologyCatalogTab || "overview");
  if (!ontologyState.ontologyCatalogFilters[key]) {
    ontologyState.ontologyCatalogFilters[key] = {
      query: "",
      boundedContext: "",
      enabled: "",
      ruleKind: "",
      theoryFamily: "",
      validationStatus: "",
      scope: "",
      state: "",
      symbol: ""
    };
  }
  return ontologyState.ontologyCatalogFilters[key];
}

function ontologyCatalogAccountParams() {
  var accountId = activeOntologyAccountId() || "default";
  return accountId ? "&accountId=" + encodeURIComponent(accountId) : "";
}

function loadOntologyCatalogSummary(force) {
  if (isStaticPreviewHost()) return Promise.resolve(null);
  if (ontologyState.ontologyCatalogSummaryLoading && !force) return Promise.resolve(ontologyState.ontologyCatalogSummary);
  if (ontologyState.ontologyCatalogSummaryLoaded && !force) return Promise.resolve(ontologyState.ontologyCatalogSummary);
  ontologyState.ontologyCatalogSummaryLoading = true;
  ontologyState.ontologyCatalogSummaryError = "";
  return requestJson("/api/ontology/catalog/summary?view=compact" + ontologyCatalogAccountParams() + (force ? "&refresh=1" : ""), {
    key: "ontology-catalog-summary",
    force: Boolean(force),
    cacheTtlMs: 30000,
    timeoutMs: 15000
  }).then(function (payload) {
    ontologyState.ontologyCatalogSummary = payload && typeof payload === "object" ? payload : {};
    ontologyState.ontologyCatalogSummaryLoaded = true;
    if (readModelIsWarming(payload)) {
      scheduleReadModelPoll("ontology-catalog-summary", function () { loadOntologyCatalogSummary(true); });
    } else {
      clearReadModelPoll("ontology-catalog-summary");
    }
    return ontologyState.ontologyCatalogSummary;
  }).catch(function (error) {
    ontologyState.ontologyCatalogSummaryError = error.message || "온톨로지 카탈로그 요약을 읽지 못했습니다.";
    ontologyState.ontologyCatalogSummary = { status: "error", reason: ontologyState.ontologyCatalogSummaryError };
    ontologyState.ontologyCatalogSummaryLoaded = true;
    return null;
  }).finally(function () {
    ontologyState.ontologyCatalogSummaryLoading = false;
    if (shellState.snapshot) render();
  });
}

function ontologyCatalogSectionPath(section, cursor) {
  var filter = ontologyCatalogFilter(section);
  var params = [
    "limit=40",
    "cursor=" + encodeURIComponent(cursor || "offset:0")
  ];
  ["query", "boundedContext", "enabled", "ruleKind", "theoryFamily", "validationStatus", "scope", "state", "symbol"].forEach(function (key) {
    var value = String(filter[key] || "").trim();
    if (value) params.push(encodeURIComponent(key) + "=" + encodeURIComponent(value));
  });
  return "/api/ontology/catalog/" + encodeURIComponent(section) + "?" + params.join("&") + ontologyCatalogAccountParams();
}

function loadOntologyCatalogSection(section, force, cursor) {
  var key = String(section || ontologyState.activeOntologyCatalogTab || "overview");
  if (key === "overview") return loadOntologyCatalogSummary(force);
  if (isStaticPreviewHost()) return Promise.resolve(null);
  if (ontologyState.ontologyCatalogLoading[key] && !force) return Promise.resolve(ontologyState.ontologyCatalogPages[key] || null);
  if (ontologyState.ontologyCatalogPages[key] && !force && !cursor) return Promise.resolve(ontologyState.ontologyCatalogPages[key]);
  ontologyState.ontologyCatalogLoading[key] = true;
  ontologyState.ontologyCatalogErrors[key] = "";
  return requestJson(ontologyCatalogSectionPath(key, cursor), {
    key: "ontology-catalog-" + key,
    force: Boolean(force),
    cacheTtlMs: 30000,
    timeoutMs: key === "inferences" ? 45000 : 20000
  }).then(function (payload) {
    ontologyState.ontologyCatalogPages[key] = payload && typeof payload === "object" ? payload : {};
    if (readModelIsWarming(payload)) {
      scheduleReadModelPoll("ontology-catalog-" + key, function () {
        loadOntologyCatalogSection(key, true, cursor || "offset:0");
      });
    } else {
      clearReadModelPoll("ontology-catalog-" + key);
    }
    return ontologyState.ontologyCatalogPages[key];
  }).catch(function (error) {
    ontologyState.ontologyCatalogErrors[key] = error.message || "카탈로그 목록을 읽지 못했습니다.";
    ontologyState.ontologyCatalogPages[key] = {
      status: "error",
      reason: ontologyState.ontologyCatalogErrors[key],
      items: [],
      page: { total: 0, offset: 0, limit: 40, hasMore: false, previousCursor: "", nextCursor: "" }
    };
    return null;
  }).finally(function () {
    ontologyState.ontologyCatalogLoading[key] = false;
    if (shellState.snapshot) render();
  });
}

function loadOntologyCatalogLineage(type, id, symbol) {
  var selectedType = String(type || "").trim();
  var selectedId = String(id || "").trim();
  if (!selectedType || !selectedId || isStaticPreviewHost()) return Promise.resolve(null);
  ontologyState.ontologyCatalogSelection = { type: selectedType, id: selectedId, symbol: String(symbol || "") };
  ontologyState.ontologyCatalogLineage = null;
  ontologyState.ontologyCatalogLineageLoading = true;
  ontologyState.ontologyCatalogLineageError = "";
  render();
  var path = "/api/ontology/catalog/lineage?type=" + encodeURIComponent(selectedType)
    + "&id=" + encodeURIComponent(selectedId)
    + (symbol ? "&symbol=" + encodeURIComponent(symbol) : "")
    + ontologyCatalogAccountParams();
  return requestJson(path, {
    key: "ontology-catalog-lineage",
    force: true,
    timeoutMs: 45000
  }).then(function (payload) {
    ontologyState.ontologyCatalogLineage = payload && typeof payload === "object" ? payload : {};
    return ontologyState.ontologyCatalogLineage;
  }).catch(function (error) {
    ontologyState.ontologyCatalogLineageError = error.message || "선택 항목의 추론 계보를 읽지 못했습니다.";
    return null;
  }).finally(function () {
    ontologyState.ontologyCatalogLineageLoading = false;
    if (shellState.snapshot) render();
  });
}

function loadOntologyDiagnostics(force, full) {
  if (isStaticPreviewHost()) return Promise.resolve(null);
  if (ontologyState.ontologyDiagnosticsLoading && !force) return Promise.resolve(ontologyState.ontologyDiagnostics);
  ontologyState.ontologyDiagnosticsLoading = true;
  ontologyState.ontologyDiagnosticsError = "";
  if (shellState.snapshot) render();
  var path = ontologyDiagnosticsPath();
  path += (path.indexOf("?") >= 0 ? "&" : "?") + (full ? "refresh=1" : "quick=1");
  return requestJson(path)
    .then(function (payload) {
      ontologyState.ontologyDiagnostics = payload || {};
      ontologyState.ontologyDiagnosticsError = "";
      if (full && payload && payload.cache && payload.cache.refreshing) {
        scheduleOntologyDiagnosticsPoll();
      } else if (full) {
        stopOntologyDiagnosticsPoll();
      }
      return payload;
    })
    .catch(function (error) {
      ontologyState.ontologyDiagnosticsError = error.message || "TypeDB 진단을 읽지 못했습니다.";
      return null;
    })
    .finally(function () {
      ontologyState.ontologyDiagnosticsLoading = false;
      if (shellState.snapshot) render();
    });
}

function stopOntologyDiagnosticsPoll() {
  if (ontologyState.ontologyDiagnosticsPollTimer) window.clearTimeout(ontologyState.ontologyDiagnosticsPollTimer);
  ontologyState.ontologyDiagnosticsPollTimer = 0;
  ontologyState.ontologyDiagnosticsPollAttempt = 0;
}

function scheduleOntologyDiagnosticsPoll() {
  if (ontologyState.ontologyDiagnosticsPollTimer || ontologyState.ontologyDiagnosticsPollAttempt >= 45) return;
  ontologyState.ontologyDiagnosticsPollTimer = window.setTimeout(function () {
    ontologyState.ontologyDiagnosticsPollTimer = 0;
    ontologyState.ontologyDiagnosticsPollAttempt += 1;
    requestJson(ontologyDiagnosticsPath())
      .then(function (payload) {
        ontologyState.ontologyDiagnostics = payload || {};
        ontologyState.ontologyDiagnosticsError = "";
        if (payload && payload.cache && payload.cache.refreshing) {
          scheduleOntologyDiagnosticsPoll();
        } else {
          stopOntologyDiagnosticsPoll();
        }
        if (shellState.snapshot) render();
      })
      .catch(function (error) {
        ontologyState.ontologyDiagnosticsError = error.message || "TypeDB 진단을 읽지 못했습니다.";
        scheduleOntologyDiagnosticsPoll();
        if (shellState.snapshot) render();
      });
  }, 2000);
}

function loadOntologyReasoningStatus(force) {
  if (isStaticPreviewHost()) return Promise.resolve(null);
  if (ontologyState.ontologyReasoningStatusLoading && !force) return Promise.resolve(ontologyState.ontologyReasoningStatus);
  ontologyState.ontologyReasoningStatusLoading = true;
  ontologyState.ontologyReasoningStatusError = "";
  return requestJson("/api/ontology/reasoning/status")
    .then(function (payload) {
      ontologyState.ontologyReasoningStatus = payload || {};
      ontologyState.ontologyReasoningStatusLoaded = true;
      return ontologyState.ontologyReasoningStatus;
    })
    .catch(function (error) {
      ontologyState.ontologyReasoningStatusError = error.message || "추론 대기열 상태를 읽지 못했습니다.";
      return null;
    })
    .finally(function () {
      ontologyState.ontologyReasoningStatusLoading = false;
      if (shellState.snapshot) render();
    });
}

function seedOntologyGraph() {
  if (ontologyState.ontologySeedRunning) return Promise.resolve(null);
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    ontologyState.ontologyRuleboxError = "로컬 서버에서만 TypeDB 온톨로지를 시드할 수 있습니다.";
    showSnackbar(ontologyState.ontologyRuleboxError, "danger");
    render();
    return Promise.resolve(null);
  }
  if (!window.confirm("TypeDB 온톨로지를 기본 RuleBox로 다시 시드하고 현재 추론 결과를 초기화합니다. 계속할까요?")) return Promise.resolve(null);
  ontologyState.ontologySeedRunning = true;
  ontologyState.ontologyRuleboxError = "";
  render();
  return sendJson("/api/ontology/seed", "POST", {
    replaceRuleBox: true,
    clearInference: true,
    changeReason: "웹 운영 콘솔에서 TypeDB 온톨로지 시드"
  })
    .then(function (payload) {
      showSnackbar(payload && payload.seeded ? "TypeDB 온톨로지를 시드했습니다." : "TypeDB 시드 결과를 확인하세요.", payload && payload.status === "error" ? "danger" : "success");
      ontologyState.ontologyInferenceLedgerLoaded = false;
      ontologyState.ontologyInferenceLedger = null;
      return Promise.all([loadOntologyRulebox(true), loadOntologyDiagnostics(true, true)])
        .then(function () { return payload; });
    })
    .catch(function (error) {
      ontologyState.ontologyRuleboxError = error.message || "TypeDB 온톨로지 시드에 실패했습니다.";
      showSnackbar(ontologyState.ontologyRuleboxError, "danger");
      return null;
    })
    .finally(function () {
      ontologyState.ontologySeedRunning = false;
      render();
    });
}

function snapshotHasFullOntologyDetail(snapshot) {
  var strategy = (((snapshot || {}).tossDecision || {}).ontologyStrategy || {});
  if (!strategy || typeof strategy !== "object") return false;
  if (strategy.detailLevel === "summary") return false;
  return Array.isArray(strategy.aboxEntities) || Array.isArray(strategy.relations) || Array.isArray(strategy.reasoningCards);
}

function shouldLoadOntologyStrategyDetail() {
  var detail = navigationState.workDetailLayer || {};
  return [
    "strategy-evidence-board",
    "strategy-graphs-board",
    "strategy-trace-board",
    "strategy-trace-detail",
    "experiment-validation-board",
    "experiment-promotion-board",
    "experiment-audit-board"
  ].indexOf(String(detail.type || "")) >= 0;
}

function shouldLoadStrategyProposals() {
  if (navigationState.activeTab === "experiments") return false;
  if (navigationState.activeTab !== "modeling") return false;
  var section = activeSectionForPageMode("modeling", strategySections, normalizeStrategySection(decisionsState.activeStrategySection));
  return section === "overview" || section === "graphs" || section === "proposals";
}

function strategyProposalsDesiredDetailLevel() {
  if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "strategy-proposals-board") return "full";
  if (navigationState.activeTab === "modeling") {
    var section = activeSectionForPageMode("modeling", strategySections, normalizeStrategySection(decisionsState.activeStrategySection));
    if (section === "proposals") return "full";
  }
  return "summary";
}

function strategyProposalsNeedLoad() {
  if (!proposalsState.strategyProposalsLoaded) return true;
  return strategyProposalsDesiredDetailLevel() === "full" && proposalsState.strategyProposalsDetailLevel !== "full";
}

function shouldLoadHypothesisWorkspace() {
  if (navigationState.activeTab !== "modeling") return false;
  return activeSectionForPageMode("modeling", strategySections, normalizeStrategySection(decisionsState.activeStrategySection)) === "hypotheses";
}

function loadOntologyStrategyDetail(force) {
  if (isStaticPreviewHost()) return Promise.resolve(shellState.snapshot);
  if (ontologyState.ontologyStrategyDetailLoading && !force) return Promise.resolve(shellState.snapshot);
  if (!force && snapshotHasFullOntologyDetail(shellState.snapshot)) {
    ontologyState.ontologyStrategyDetailLoaded = true;
    return Promise.resolve(shellState.snapshot);
  }
  ontologyState.ontologyStrategyDetailLoading = true;
  ontologyState.ontologyStrategyDetailError = "";
  if (shellState.snapshot) render();
  return requestJson(tossLensPath({ detail: "full" }), { key: "flow-lens-detail", timeoutMs: 30000 })
    .then(function (payload) {
      shellState.snapshot = payload;
      shellState.snapshotFromCache = false;
      ontologyState.ontologyStrategyDetailLoaded = true;
      ontologyState.ontologyStrategyDetailError = "";
      return payload;
    })
    .catch(function (error) {
      ontologyState.ontologyStrategyDetailError = error.message || "온톨로지 상세 데이터를 읽지 못했습니다.";
      return shellState.snapshot;
    })
    .finally(function () {
      ontologyState.ontologyStrategyDetailLoading = false;
      if (shellState.snapshot) render();
    });
}

function ontologyAuditPath() {
  var filters = ontologyState.ontologyAuditFilters || {};
  var params = new URLSearchParams();
  params.set("limit", String(filters.limit || "80"));
  params.set("offset", String(filters.offset || "0"));
  if (String(filters.query || "").trim()) params.set("q", String(filters.query || "").trim());
  if (String(filters.symbol || "").trim()) params.set("symbol", String(filters.symbol || "").trim().toUpperCase());
  appendOntologyAccountQuery(params);
  return "/api/ontology/audit?" + params.toString();
}

function ontologyAuditSectionPath(sectionId) {
  var base = ontologyAuditPath();
  var query = base.indexOf("?") >= 0 ? base.slice(base.indexOf("?")) : "";
  return "/api/ontology/audit/" + encodeURIComponent(String(sectionId || "")) + query;
}

function activeOntologyAccountId() {
  var account = activeWatchAccount();
  if (account && accountIdOf(account)) return accountIdOf(account);
  var snapshot = shellState.snapshot || {};
  return String(snapshot.accountId || ((snapshot.toss || {}).accountId || "")).trim();
}

function appendOntologyAccountQuery(params) {
  var accountId = activeOntologyAccountId();
  if (accountId) params.set("accountId", accountId);
  return params;
}

function ontologyAccountPayload(payload) {
  var accountId = activeOntologyAccountId();
  return accountId ? Object.assign({}, payload || {}, { accountId: accountId }) : Object.assign({}, payload || {});
}

function ontologyDiagnosticsPath() {
  var params = new URLSearchParams();
  appendOntologyAccountQuery(params);
  var query = params.toString();
  return "/api/ontology/diagnostics" + (query ? "?" + query : "");
}

function ontologyAccountOptions() {
  var accounts = accountsState.serviceAccounts || [];
  var activeId = activeOntologyAccountId();
  return accounts.map(function (account) {
    var accountId = accountIdOf(account);
    return '<option value="' + escapeHtml(accountId) + '"' + (accountId === activeId ? " selected" : "") + '>' + escapeHtml(account.label || accountId) + '</option>';
  }).join("");
}

function ontologyAuditClientRow(row, rowType, box, index) {
  row = row && typeof row === "object" ? row : {};
  var relationType = row.relationType || row.type || "";
  return {
    key: String(box || "row") + "-" + String(rowType || "row") + "-" + String(row.id || row.label || relationType || index),
    rowType: rowType || "entity",
    id: String(row.id || row.key || ""),
    label: String(row.label || row.title || row.id || relationType || "row"),
    kind: String(row.kind || row.nodeKind || relationType || rowType || ""),
    box: String(row.ontologyBox || row.box || box || ""),
    relationType: String(relationType || ""),
    source: String(row.sourceLabel || row.source || ""),
    target: String(row.targetLabel || row.target || ""),
    symbol: String(row.symbol || ""),
    ruleId: String(row.ruleId || row.sourceRuleId || row.semanticRuleId || ""),
    status: String(row.status || ""),
    updatedAt: String(row.updatedAt || row.createdAt || ""),
    weight: row.weight,
    raw: row
  };
}

function ontologyAuditClientSection(id, label, description, rows) {
  rows = latestChangedFirst(Array.isArray(rows) ? rows : []);
  return {
    id: id,
    label: label,
    description: description,
    total: rows.length,
    offset: 0,
    limit: rows.length,
    hasMore: false,
    entityCount: rows.filter(function (row) { return row.rowType === "entity"; }).length,
    relationCount: rows.filter(function (row) { return row.rowType === "relation"; }).length,
    rows: rows
  };
}

function staticOntologyAuditPayload(snapshot) {
  var parts = ontologyStrategyParts(snapshot || shellState.snapshot || {});
  var tboxRows = (Array.isArray(parts.tboxEntities) ? parts.tboxEntities : []).map(function (row, index) {
    return ontologyAuditClientRow(row, "entity", "TBox", index);
  }).concat((Array.isArray(parts.tboxRelations) ? parts.tboxRelations : []).map(function (row, index) {
    return ontologyAuditClientRow(row, "relation", "TBox", index);
  }));
  var aboxRows = (Array.isArray(parts.aboxEntities) ? parts.aboxEntities : []).map(function (row, index) {
    return ontologyAuditClientRow(row, "entity", "ABox", index);
  }).concat((Array.isArray(parts.aboxRelations) ? parts.aboxRelations : []).map(function (row, index) {
    return ontologyAuditClientRow(row, "relation", "ABox", index);
  }));
  var evidenceRows = []
    .concat(Array.isArray(parts.evidence) ? parts.evidence : [])
    .concat(Array.isArray(parts.beliefs) ? parts.beliefs : [])
    .concat(Array.isArray(parts.opinions) ? parts.opinions : [])
    .map(function (row, index) {
      return ontologyAuditClientRow(row, "trace", "ABox", index);
    });
  var relationCounts = parts.relationCounts || {};
  var syncRows = [
    ontologyAuditClientRow({ id: "snapshot.audit", label: "Snapshot fallback", kind: "sync-status", status: "preview", updatedAt: ((shellState.snapshot || {}).generatedAt || "") }, "status", "Runtime", 0)
  ];
  return {
    generatedAt: (shellState.snapshot || {}).generatedAt || new Date().toISOString(),
    status: "preview",
    graphStore: "snapshot",
    storeLabel: "Snapshot",
    configured: false,
    summary: {
      sectionTotals: {
        tbox: tboxRows.length,
        abox: aboxRows.length,
        rulebox: Object.keys(relationCounts).length,
        inferencebox: 0,
        evidence: evidenceRows.length,
        sync: syncRows.length
      },
      graphRowCount: tboxRows.length + aboxRows.length,
      entityCount: (Array.isArray(parts.entities) ? parts.entities.length : 0),
      relationCount: (Array.isArray(parts.relations) ? parts.relations.length : 0),
      ruleCount: Object.keys(relationCounts).length,
      diagnosticsStatus: "preview"
    },
    sections: {
      tbox: ontologyAuditClientSection("tbox", "TBox", "스냅샷 스키마", tboxRows),
      abox: ontologyAuditClientSection("abox", "ABox", "스냅샷 실체 데이터", aboxRows),
      rulebox: ontologyAuditClientSection("rulebox", "RuleBox", "관계 타입 분포", Object.keys(relationCounts).sort().map(function (type, index) {
        return ontologyAuditClientRow({ id: type, label: type, kind: "relation-type", status: String(relationCounts[type] || 0) }, "rule", "RuleBox", index);
      })),
      inferencebox: ontologyAuditClientSection("inferencebox", "InferenceBox", "TypeDB 감사 API 필요", []),
      evidence: ontologyAuditClientSection("evidence", "Evidence Trace", "근거와 의견", evidenceRows),
      sync: ontologyAuditClientSection("sync", "TypeDB Sync", "스냅샷 fallback 상태", syncRows)
    }
  };
}

function loadOntologyAudit(force) {
  if (isStaticPreviewHost()) {
    ontologyState.ontologyAudit = staticOntologyAuditPayload(shellState.snapshot || {});
    ontologyState.ontologyAuditSections = {};
    ontologyState.ontologyAuditLoaded = true;
    ontologyState.ontologyAuditError = "";
    if (shellState.snapshot) render();
    return Promise.resolve(ontologyState.ontologyAudit);
  }
  if (ontologyState.ontologyAuditLoading && !force) return Promise.resolve(ontologyState.ontologyAudit);
  if (ontologyState.ontologyAuditLoaded && ontologyState.ontologyAudit && !force) return Promise.resolve(ontologyState.ontologyAudit);
  ontologyState.ontologyAuditLoading = true;
  ontologyState.ontologyAuditError = "";
  if (shellState.snapshot) render();
  return requestJson(ontologyAuditPath())
    .then(function (payload) {
      ontologyState.ontologyAudit = payload || {};
      ontologyState.ontologyAuditSections = {};
      ontologyState.ontologyAuditLoaded = true;
      ontologyState.ontologyAuditError = "";
    })
    .catch(function (error) {
      ontologyState.ontologyAuditError = error.message || "온톨로지 감사 데이터를 읽지 못했습니다.";
    })
    .finally(function () {
      ontologyState.ontologyAuditLoading = false;
      if (shellState.snapshot) render();
    });
}

function loadOntologyAuditSection(sectionId) {
  var key = String(sectionId || "").trim().toLowerCase();
  if (!key || isStaticPreviewHost()) return Promise.resolve(ontologyState.ontologyAudit);
  ontologyState.ontologyAuditLoading = true;
  ontologyState.ontologyAuditError = "";
  if (shellState.snapshot) render();
  return requestJson(ontologyAuditSectionPath(key), {
    key: "ontology-audit:" + key,
    timeoutMs: 30000,
    force: true
  }).then(function (payload) {
    var detailPayload = payload && typeof payload === "object" ? payload : {};
    var section = detailPayload.sections && detailPayload.sections[key];
    if (section && typeof section === "object") ontologyState.ontologyAuditSections[key] = section;
    return detailPayload;
  }).catch(function (error) {
    ontologyState.ontologyAuditError = error.message || "온톨로지 감사 상세를 읽지 못했습니다.";
    return null;
  }).finally(function () {
    ontologyState.ontologyAuditLoading = false;
    if (shellState.snapshot) render();
  });
}

function shouldLoadOntologyInferenceLedger() {
  if (navigationState.activeTab !== "modeling") return false;
  return activeSectionForPageMode("modeling", strategySections, normalizeStrategySection(decisionsState.activeStrategySection)) === "trace";
}

function ontologyInferenceLedgerPath() {
  var params = new URLSearchParams();
  params.set("limit", "120");
  appendOntologyAccountQuery(params);
  return "/api/ontology/inference-ledger?" + params.toString();
}

function staticOntologyInferenceLedgerPayload() {
  var parts = ontologyStrategyParts(shellState.snapshot || {});
  var inferenceRows = ontologyReadableInferenceRows(parts);
  return {
    generatedAt: ((shellState.snapshot || {}).generatedAt || new Date().toISOString()),
    status: inferenceRows.length ? "preview" : "empty",
    graphStore: "snapshot",
    source: "snapshot",
    reason: inferenceRows.length ? "" : "정적 미리보기에는 TypeDB InferenceBox 원장이 포함되지 않습니다.",
    summary: {
      ledgerCount: inferenceRows.length,
      traceCount: inferenceRows.length,
      relationCount: inferenceRows.length,
      entityCount: 0,
      matchedRuleCount: 0,
      activeRuleCount: ontologyReadableRuleRows(parts).length,
      untracedRuleCount: 0,
      conditionCount: 0,
      matchedConditionCount: 0,
      notReturnedConditionCount: 0
    },
    ruleCoverage: { matchedRuleIds: [], untracedRuleIds: [], coverageRatio: 0 },
    rows: inferenceRows.map(function (row, index) {
      return {
        key: "preview-ledger-" + index,
        symbol: row.source || "",
        ruleId: row.detail || row.type || "",
        ruleLabel: row.type || "InferenceBox",
        status: "preview",
        reviewLevel: "observe",
        dataState: "partial",
        validationState: "conditional",
        evidenceRole: "context",
        decisionStage: row.detail || "",
        relationTypes: [row.type].filter(Boolean),
        matchedConditionCount: 0,
        conditionCount: 0,
        derivedRelationCount: 1,
        derivedEntityCount: 0,
        conditions: [],
        derivations: [],
        relations: [{ type: row.type, sourceLabel: row.source, targetLabel: row.target, aiInfluenceLabel: row.detail, evidenceRole: "context" }],
        stages: [
          { id: "source-data", label: "Source facts", status: "preview", detail: "snapshot relation" },
          { id: "rulebox", label: "RuleBox", status: "preview", detail: row.detail || "" },
          { id: "inferencebox", label: "InferenceBox", status: "preview", detail: row.type || "" },
          { id: "derived-output", label: "Derived output", status: "preview", detail: [row.source, row.target].filter(Boolean).join(" → ") }
        ]
      };
    })
  };
}

function loadOntologyInferenceLedger(force) {
  if (isStaticPreviewHost()) {
    ontologyState.ontologyInferenceLedger = staticOntologyInferenceLedgerPayload();
    ontologyState.ontologyInferenceLedgerLoaded = true;
    ontologyState.ontologyInferenceLedgerError = "";
    if (shellState.snapshot) render();
    return Promise.resolve(ontologyState.ontologyInferenceLedger);
  }
  if (ontologyState.ontologyInferenceLedgerLoading && !force) return Promise.resolve(ontologyState.ontologyInferenceLedger);
  if (ontologyState.ontologyInferenceLedgerLoaded && ontologyState.ontologyInferenceLedger && !force) return Promise.resolve(ontologyState.ontologyInferenceLedger);
  ontologyState.ontologyInferenceLedgerLoading = true;
  ontologyState.ontologyInferenceLedgerError = "";
  if (shellState.snapshot) render();
  return requestJson(ontologyInferenceLedgerPath())
    .then(function (payload) {
      ontologyState.ontologyInferenceLedger = payload || {};
      ontologyState.ontologyInferenceLedgerLoaded = true;
      ontologyState.ontologyInferenceLedgerError = "";
      return payload;
    })
    .catch(function (error) {
      ontologyState.ontologyInferenceLedgerError = error.message || "Inference Trace Ledger를 읽지 못했습니다.";
      return null;
    })
    .finally(function () {
      ontologyState.ontologyInferenceLedgerLoading = false;
      if (shellState.snapshot) render();
    });
}

function applyOntologyRuleboxPayload(payload) {
  ontologyState.ontologyRulebox = payload || {};
  ontologyState.ontologyRuleboxJson = JSON.stringify((payload && payload.rules) || [], null, 2);
  ontologyState.ontologyRuleboxError = "";
}

function parseOntologyRuleboxEditor() {
  var raw = String(ontologyState.ontologyRuleboxJson || "[]").trim() || "[]";
  var parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) throw new Error("RuleBox JSON은 규칙 배열이어야 합니다.");
  return parsed;
}

function saveOntologyRulebox(seedDefaults) {
  if (ontologyState.ontologyRuleboxSaving) return;
  var rules = null;
  if (!seedDefaults) {
    try {
      rules = parseOntologyRuleboxEditor();
    } catch (error) {
      ontologyState.ontologyRuleboxError = error.message || "RuleBox JSON 형식을 확인하세요.";
      showSnackbar(ontologyState.ontologyRuleboxError, "danger");
      render();
      return;
    }
  }
  if (!window.confirm(seedDefaults
    ? "기본 RuleBox를 TypeDB에 다시 시드하고 현재 추론 결과를 초기화합니다. 계속할까요?"
    : "RuleBox 규칙을 TypeDB 운영 저장소에 반영하고 현재 추론 결과를 초기화합니다. 계속할까요?")) return;
  ontologyState.ontologyRuleboxSaving = true;
  ontologyState.ontologyRuleboxError = "";
  render();
  sendJson("/api/ontology/rulebox", "PUT", seedDefaults ? {
    clearInference: true,
    useBootstrapDefaults: true,
    changeReason: ontologyState.ontologyRuleboxChangeReason || "기본 RuleBox 시드"
  } : {
    rules: rules,
    clearInference: true,
    changeReason: ontologyState.ontologyRuleboxChangeReason || "RuleBox 관리 화면 저장"
  })
    .then(function (payload) {
      applyOntologyRuleboxPayload(payload);
      ontologyState.ontologyInferenceLedgerLoaded = false;
      ontologyState.ontologyInferenceLedger = null;
      ontologyState.ontologyRuleboxChangeReason = "";
      showSnackbar(seedDefaults ? "기본 RuleBox를 TypeDB에 시드했습니다." : "TypeDB RuleBox를 저장했습니다.");
    })
    .catch(function (error) {
      ontologyState.ontologyRuleboxError = error.message || "TypeDB RuleBox를 저장하지 못했습니다.";
      showSnackbar(ontologyState.ontologyRuleboxError, "danger");
    })
    .finally(function () {
      ontologyState.ontologyRuleboxSaving = false;
      render();
    });
}

function appendRuleboxCandidate(candidateId) {
  var payload = ontologyState.ontologyRulebox || {};
  var candidates = Array.isArray(payload.changeCandidates) ? payload.changeCandidates : [];
  var candidate = candidates.filter(function (item) {
    return String(item.id || "") === String(candidateId || "");
  })[0];
  var proposedRule = candidate && candidate.proposedRule && typeof candidate.proposedRule === "object" ? candidate.proposedRule : null;
  if (!proposedRule) {
    showSnackbar("이 후보는 먼저 데이터나 스키마 보강이 필요합니다.", "caution");
    return;
  }
  try {
    var rules = parseOntologyRuleboxEditor();
    var proposedId = proposedRule.rule_id || proposedRule.ruleId || "";
    if (rules.some(function (rule) { return String(rule.rule_id || rule.ruleId || "") === String(proposedId); })) {
      showSnackbar("이미 같은 rule_id가 RuleBox JSON에 있습니다.", "caution");
      return;
    }
    rules.push(proposedRule);
    ontologyState.ontologyRuleboxJson = JSON.stringify(rules, null, 2);
    ontologyState.ontologyRuleboxChangeReason = ontologyState.ontologyRuleboxChangeReason || ("AI 후보 추가 검토: " + (candidate.title || proposedId));
    ontologyState.ontologyRuleboxError = "";
    showSnackbar("후보 규칙 초안을 JSON에 추가했습니다. 검토 후 저장하세요.");
    render();
  } catch (error) {
    ontologyState.ontologyRuleboxError = error.message || "RuleBox JSON 형식을 확인하세요.";
    showSnackbar(ontologyState.ontologyRuleboxError, "danger");
    render();
  }
}

function runOntologyRulebox() {
  if (ontologyState.ontologyRuleboxRunning) return;
  ontologyState.ontologyRuleboxRunning = true;
  ontologyState.ontologyRuleboxError = "";
  render();
  sendJson("/api/ontology/rulebox/run", "POST", ontologyAccountPayload({ clearInference: true }))
    .then(function (payload) {
      ontologyState.ontologyRuleboxLastRun = payload;
      showSnackbar(payload.status === "ok" ? "TypeDB 네이티브 규칙 추론을 실행했습니다." : "네이티브 규칙 실행 결과: " + (payload.status || "확인 필요"), payload.status === "ok" ? "success" : "caution");
      ontologyState.ontologyInferenceLedgerLoaded = false;
      return Promise.all([loadOntologyRulebox(true), loadOntologyInferenceLedger(true)]);
    })
    .catch(function (error) {
      ontologyState.ontologyRuleboxError = error.message || "TypeDB 네이티브 규칙 추론 실행에 실패했습니다.";
      showSnackbar(ontologyState.ontologyRuleboxError, "danger");
    })
    .finally(function () {
      ontologyState.ontologyRuleboxRunning = false;
      render();
    });
}

function proposeOntologyRuleCandidates() {
  if (ontologyState.ontologyRuleboxProposing) return;
  ontologyState.ontologyRuleboxProposing = true;
  ontologyState.ontologyRuleboxError = "";
  render();
  sendJson("/api/ontology/rulebox/candidates", "POST", ontologyAccountPayload({ trigger: "manual" }))
    .then(function (payload) {
      ontologyState.ontologyRuleboxCandidateResult = payload;
      if (payload && payload.rulebox) applyOntologyRuleboxPayload(payload.rulebox);
      showSnackbar(
        payload && payload.savedCount ? "AI 관계 후보를 TypeDB RuleBox에 저장했습니다." : "AI 관계 후보 생성 결과가 없습니다.",
        payload && payload.savedCount ? "success" : "caution"
      );
    })
    .catch(function (error) {
      ontologyState.ontologyRuleboxError = error.message || "AI 관계 후보 생성에 실패했습니다.";
      showSnackbar(ontologyState.ontologyRuleboxError, "danger");
    })
    .finally(function () {
      ontologyState.ontologyRuleboxProposing = false;
      render();
    });
}

export { activeOntologyAccountId, appendRuleboxCandidate, loadOntologyAudit, loadOntologyAuditSection, loadOntologyCatalogLineage, loadOntologyCatalogSection, loadOntologyCatalogSummary, loadOntologyDiagnostics, loadOntologyInferenceLedger, loadOntologyReasoningStatus, loadOntologyRulebox, loadOntologyStrategyDetail, ontologyAccountOptions, ontologyAccountPayload, ontologyAuditClientSection, ontologyCatalogFilter, proposeOntologyRuleCandidates, runOntologyRulebox, saveOntologyRulebox, seedOntologyGraph, shouldLoadHypothesisWorkspace, shouldLoadOntologyInferenceLedger, shouldLoadOntologyStrategyDetail, shouldLoadStrategyProposals, snapshotHasFullOntologyDetail, staticOntologyAuditPayload, strategyProposalsDesiredDetailLevel, strategyProposalsNeedLoad };
