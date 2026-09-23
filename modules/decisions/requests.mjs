import { normalizeInvestmentCaseDetailTab, patchInvestmentCaseTabRegion } from "./case-detail.mjs";
import { workDetailUrl } from "../navigation/routes.mjs";
import { activeOntologyAccountId } from "../ontology/requests.mjs";
import { render } from "../render/scheduler.mjs";
import { requestJson } from "../requests/json.mjs";
import { createLatestRequestLane } from "../requests/latest.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { navigationState } from "../state/navigation.mjs";
import { shellState } from "../state/shell.mjs";

function investmentFlowPath() {
  var params = new URLSearchParams();
  var accountId = activeOntologyAccountId();
  if (accountId) params.set("accountId", accountId);
  params.set("limit", "200");
  return "/api/decisions?" + params.toString();
}

const decisionListLane = createLatestRequestLane();

function loadInvestmentFlow(force) {
  if (isStaticPreviewHost()) {
    decisionsState.investmentFlow = { version: "investment-case-v1", status: "preview", count: 0, summary: { total: 0, attentionRequired: 0, validation: {}, readiness: {} }, items: [], operatorView: { stages: [], issues: [] } };
    decisionsState.investmentFlowLoaded = true;
    decisionsState.investmentFlowAccountId = activeOntologyAccountId();
    decisionsState.investmentFlowError = "";
    return Promise.resolve(decisionsState.investmentFlow);
  }
  var accountId = activeOntologyAccountId();
  var active = decisionListLane.active(accountId);
  if (active && !force) return active;
  if (decisionsState.investmentFlowAccountId !== accountId) {
    decisionsState.investmentFlowLoaded = false;
    decisionsState.investmentFlow = null;
    decisionsState.investmentFlowAccountId = accountId;
  }
  if (decisionsState.investmentFlowLoaded && !force) return Promise.resolve(decisionsState.investmentFlow);
  var operation = decisionListLane.begin(accountId);
  decisionsState.investmentFlowLoading = true;
  decisionsState.investmentFlowError = "";
  return operation.track(requestJson(investmentFlowPath(), {
    key: "investment-cases:" + activeOntologyAccountId(),
    force: Boolean(force),
    timeoutMs: 15000
  }).then(function (payload) {
    if (!operation.current() || activeOntologyAccountId() !== accountId) return null;
    decisionsState.investmentFlow = payload && typeof payload === "object" ? payload : {};
    decisionsState.investmentFlowLoaded = true;
    decisionsState.investmentFlowAccountId = accountId;
    return decisionsState.investmentFlow;
  }).catch(function (error) {
    if (!operation.current() || activeOntologyAccountId() !== accountId) return null;
    decisionsState.investmentFlowError = error.message || "투자 케이스를 읽지 못했습니다.";
    return null;
  }).finally(function () {
    if (!operation.current()) return;
    decisionsState.investmentFlowLoading = false;
    operation.finish();
    if (shellState.snapshot) render();
  }));
}

function loadInvestmentModel(force) {
  if (isStaticPreviewHost()) {
    decisionsState.investmentModel = { status: "preview", model: {}, activeRelease: {}, inventory: {}, validation: {}, governance: {} };
    decisionsState.investmentModelLoaded = true;
    decisionsState.investmentModelError = "";
    return Promise.resolve(decisionsState.investmentModel);
  }
  var polling = Boolean(
    decisionsState.investmentModel && (
      decisionsState.investmentModel.status === "warming" ||
      ((decisionsState.investmentModel.cache || {}).refreshing)
    )
  );
  if (decisionsState.investmentModelLoading && !force) return Promise.resolve(decisionsState.investmentModel);
  if (decisionsState.investmentModelLoaded && !force && !polling) return Promise.resolve(decisionsState.investmentModel);
  decisionsState.investmentModelLoading = true;
  decisionsState.investmentModelError = "";
  return requestJson("/api/investment-model" + (force ? "?refresh=1" : ""), {
    key: "investment-model",
    force: Boolean(force || polling),
    timeoutMs: 30000
  }).then(function (payload) {
    decisionsState.investmentModel = payload && typeof payload === "object" ? payload : {};
    decisionsState.investmentModelLoaded = true;
    var refreshPending = decisionsState.investmentModel.status === "warming" || Boolean((decisionsState.investmentModel.cache || {}).refreshing);
    if (refreshPending && decisionsState.investmentModelPollCount < 12) {
      decisionsState.investmentModelPollCount += 1;
      window.setTimeout(function () {
        if (decisionsState.investmentModel && (
          decisionsState.investmentModel.status === "warming" ||
          Boolean((decisionsState.investmentModel.cache || {}).refreshing)
        )) loadInvestmentModel(false);
      }, 1800);
    } else if (!refreshPending) {
      decisionsState.investmentModelPollCount = 0;
    }
    return decisionsState.investmentModel;
  }).catch(function (error) {
    decisionsState.investmentModelError = error.message || "투자모델 상태를 읽지 못했습니다.";
    return null;
  }).finally(function () {
    decisionsState.investmentModelLoading = false;
    if (shellState.snapshot) render();
  });
}

function loadInvestmentFlowDetail(caseId, force) {
  var key = String(caseId || "").trim();
  if (!key || isStaticPreviewHost()) return Promise.resolve(null);
  if (decisionsState.investmentFlowDetails[key] && !force) return Promise.resolve(decisionsState.investmentFlowDetails[key]);
  if (decisionsState.investmentFlowDetailLoading[key] && !force) return Promise.resolve(null);
  decisionsState.investmentFlowDetailLoading[key] = true;
  decisionsState.investmentFlowDetailErrors[key] = "";
  var request = requestJson("/api/decisions/" + encodeURIComponent(key), {
    key: "investment-case-detail:" + key,
    force: Boolean(force),
    timeoutMs: 15000
  }).catch(function (originalError) {
    var ensureList = decisionsState.investmentFlowLoaded
      ? Promise.resolve(decisionsState.investmentFlow)
      : loadInvestmentFlow(false);
    return ensureList.then(function () {
      var items = Array.isArray((decisionsState.investmentFlow || {}).items) ? decisionsState.investmentFlow.items : [];
      var current = items.filter(function (item) {
        return String(item.caseId || "") === key || String(item.episodeId || "") === key;
      })[0];
      var alternateKey = current
        ? String((String(current.caseId || "") === key ? current.episodeId : current.caseId) || "")
        : "";
      if (!alternateKey || alternateKey === key) throw originalError;
      return requestJson("/api/decisions/" + encodeURIComponent(alternateKey), {
        key: "investment-case-detail-fallback:" + key,
        force: true,
        timeoutMs: 15000
      });
    });
  }).then(function (payload) {
    decisionsState.investmentFlowDetails[key] = payload && typeof payload === "object" ? payload : {};
    var resolvedEpisodeId = String((payload || {}).episodeId || "").trim();
    if ((payload || {}).resolvedFromLegacyKey && resolvedEpisodeId) {
      decisionsState.investmentFlowDetails[resolvedEpisodeId] = decisionsState.investmentFlowDetails[key];
      if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "investment-case" && String(navigationState.workDetailLayer.key || "") === key) {
        navigationState.workDetailLayer.key = resolvedEpisodeId;
        window.history.replaceState(null, "", workDetailUrl("investment-case", resolvedEpisodeId));
      }
    }
    return decisionsState.investmentFlowDetails[key];
  }).catch(function (error) {
    decisionsState.investmentFlowDetailErrors[key] = error.message || "투자 케이스 상세를 읽지 못했습니다. 목록을 새로고침해 주세요.";
    return null;
  }).finally(function () {
    delete decisionsState.investmentFlowDetailLoading[key];
    if (shellState.snapshot) render();
  });
}

function loadInvestmentCaseHistory(caseId, force) {
  var key = String(caseId || "").trim();
  if (!key || isStaticPreviewHost()) return Promise.resolve(null);
  if (decisionsState.investmentCaseHistories[key] && !force) return Promise.resolve(decisionsState.investmentCaseHistories[key]);
  if (decisionsState.investmentCaseHistoryLoading[key] && !force) return Promise.resolve(null);
  decisionsState.investmentCaseHistoryLoading[key] = true;
  decisionsState.investmentCaseHistoryErrors[key] = "";
  return requestJson("/api/investment-cases/" + encodeURIComponent(key) + "/history?limit=40", {
    key: "investment-case-history:" + key,
    force: Boolean(force),
    timeoutMs: 15000
  }).then(function (payload) {
    decisionsState.investmentCaseHistories[key] = payload && typeof payload === "object" ? payload : {};
    return decisionsState.investmentCaseHistories[key];
  }).catch(function (error) {
    decisionsState.investmentCaseHistoryErrors[key] = error.message || "판단 이력을 읽지 못했습니다.";
    return null;
  }).finally(function () {
    delete decisionsState.investmentCaseHistoryLoading[key];
    var active = normalizeInvestmentCaseDetailTab(decisionsState.investmentCaseDetailTabs[key]);
    var visible = navigationState.workDetailLayer
      && ["investment-case", "investment-flow"].indexOf(navigationState.workDetailLayer.type) >= 0
      && String(navigationState.workDetailLayer.key || "") === key
      && active === "history";
    if (visible && !patchInvestmentCaseTabRegion(key, active) && shellState.snapshot) render();
  });
}

function loadInvestmentCaseTrace(caseId, force) {
  var key = String(caseId || "").trim();
  if (!key || isStaticPreviewHost()) return Promise.resolve(null);
  if (decisionsState.investmentCaseTraces[key] && !force) return Promise.resolve(decisionsState.investmentCaseTraces[key]);
  if (decisionsState.investmentCaseTraceLoading[key] && !force) return Promise.resolve(null);
  decisionsState.investmentCaseTraceLoading[key] = true;
  decisionsState.investmentCaseTraceErrors[key] = "";
  return requestJson("/api/investment-cases/" + encodeURIComponent(key) + "/trace", {
    key: "investment-case-trace:" + key,
    force: Boolean(force),
    timeoutMs: 15000
  }).then(function (payload) {
    decisionsState.investmentCaseTraces[key] = payload && typeof payload === "object" ? payload : {};
    return decisionsState.investmentCaseTraces[key];
  }).catch(function (error) {
    decisionsState.investmentCaseTraceErrors[key] = error.message || "운영 추적 정보를 읽지 못했습니다.";
    return null;
  }).finally(function () {
    delete decisionsState.investmentCaseTraceLoading[key];
    var active = normalizeInvestmentCaseDetailTab(decisionsState.investmentCaseDetailTabs[key]);
    var visible = navigationState.workDetailLayer
      && ["investment-case", "investment-flow"].indexOf(navigationState.workDetailLayer.type) >= 0
      && String(navigationState.workDetailLayer.key || "") === key
      && active === "trace";
    if (visible && !patchInvestmentCaseTabRegion(key, active) && shellState.snapshot) render();
  });
}

export { loadInvestmentCaseHistory, loadInvestmentCaseTrace, loadInvestmentFlow, loadInvestmentFlowDetail, loadInvestmentModel };
