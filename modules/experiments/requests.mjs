import { applicableOntologyExperimentRecommendationIds, clearOntologyExperimentRecommendationSelection, ontologyExperimentById, ontologyExperimentLatestRun, ontologyExperimentPromotionGate, ontologyExperimentSelectedRecommendationIds, syncActiveHypothesisDevelopmentCaseId, syncActiveOntologyExperimentId } from "./workspace.mjs";
import { ontologyAccountPayload } from "../ontology/requests.mjs";
import { render } from "../render/scheduler.mjs";
import { clearReadModelPoll, readModelIsWarming, requestJson, scheduleReadModelPoll } from "../requests/json.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { uniqueTextItems } from "../shared/text.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { experimentsState } from "../state/experiments.mjs";
import { hypothesesState } from "../state/hypotheses.mjs";
import { shellState } from "../state/shell.mjs";

function loadOntologyExperiments(force) {
  if (isStaticPreviewHost()) {
    experimentsState.ontologyExperiments = { experiments: [], count: 0, activeCount: 0, latestRun: {} };
    experimentsState.ontologyExperimentsLoaded = true;
    experimentsState.ontologyExperimentsError = "";
    return Promise.resolve(experimentsState.ontologyExperiments);
  }
  if (experimentsState.ontologyExperimentsLoading && !force) return Promise.resolve(experimentsState.ontologyExperiments);
  experimentsState.ontologyExperimentsLoading = true;
  experimentsState.ontologyExperimentsError = "";
  if (shellState.snapshot) render();
  return requestJson("/api/ontology/experiments/status" + (force ? "?refresh=1" : ""), {
    key: "ontology-experiments-status",
    force: Boolean(force),
    cacheTtlMs: 30000,
    timeoutMs: 15000
  })
    .then(function (payload) {
      experimentsState.ontologyExperiments = payload || {};
      experimentsState.ontologyExperimentsLoaded = true;
      if (readModelIsWarming(payload)) {
        scheduleReadModelPoll("ontology-experiments-status", function () { loadOntologyExperiments(true); });
      } else {
        clearReadModelPoll("ontology-experiments-status");
      }
      syncActiveOntologyExperimentId();
      return payload;
    })
    .catch(function (error) {
      experimentsState.ontologyExperimentsError = error.message || "온톨로지 실험 상태를 읽지 못했습니다.";
      return null;
    })
    .finally(function () {
      experimentsState.ontologyExperimentsLoading = false;
      if (shellState.snapshot) render();
    });
}

function loadHypothesisDevelopment(force) {
  if (isStaticPreviewHost()) {
    hypothesesState.hypothesisDevelopment = { count: 0, summary: { statuses: {} }, cases: [], events: [] };
    hypothesesState.hypothesisDevelopmentLoaded = true;
    hypothesesState.hypothesisDevelopmentError = "";
    return Promise.resolve(hypothesesState.hypothesisDevelopment);
  }
  if (hypothesesState.hypothesisDevelopmentLoading && !force) return Promise.resolve(hypothesesState.hypothesisDevelopment);
  hypothesesState.hypothesisDevelopmentLoading = true;
  hypothesesState.hypothesisDevelopmentError = "";
  return requestJson("/api/investment-brain/hypothesis-development?limit=100", {
    key: "hypothesis-development",
    force: Boolean(force),
    timeoutMs: 30000
  }).then(function (payload) {
    hypothesesState.hypothesisDevelopment = payload && typeof payload === "object" ? payload : {};
    hypothesesState.hypothesisDevelopmentLoaded = true;
    syncActiveHypothesisDevelopmentCaseId();
    return hypothesesState.hypothesisDevelopment;
  }).catch(function (error) {
    hypothesesState.hypothesisDevelopmentError = error.message || "가설 자동 검증 상태를 읽지 못했습니다.";
    return null;
  }).finally(function () {
    hypothesesState.hypothesisDevelopmentLoading = false;
    if (shellState.snapshot) render();
  });
}

function processHypothesisDevelopment(caseId) {
  var id = String(caseId || "").trim();
  if (hypothesesState.hypothesisDevelopmentAction || isStaticPreviewHost()) return;
  hypothesesState.hypothesisDevelopmentAction = "process:" + (id || "pending");
  hypothesesState.hypothesisDevelopmentError = "";
  render();
  sendJson("/api/investment-brain/hypothesis-development/process", "POST", id ? { caseId: id } : { limit: 5 })
    .then(function (payload) {
      var processed = Number(payload && payload.processedCount || (payload && payload.case ? 1 : 0));
      showSnackbar(processed ? "가설 자동 검증을 실행했습니다." : "자동 검증할 대기 가설이 없습니다.", processed ? "success" : "caution");
      return Promise.all([loadHypothesisDevelopment(true), loadOntologyExperiments(true)]);
    })
    .catch(function (error) {
      hypothesesState.hypothesisDevelopmentError = error.message || "가설 자동 검증을 실행하지 못했습니다.";
      showSnackbar(hypothesesState.hypothesisDevelopmentError, "danger");
    })
    .finally(function () {
      hypothesesState.hypothesisDevelopmentAction = "";
      render();
    });
}

function approveHypothesisDevelopment(caseId) {
  var id = String(caseId || "").trim();
  if (!id || hypothesesState.hypothesisDevelopmentAction || isStaticPreviewHost()) return;
  if (window.confirm && !window.confirm("자동 검증을 통과한 가설 규칙을 운영 RuleBox에 반영합니다. 새 TypeDB 추론이 실패하면 이전 버전으로 자동 복원됩니다. 계속할까요?")) return;
  hypothesesState.hypothesisDevelopmentAction = "approve:" + id;
  hypothesesState.hypothesisDevelopmentError = "";
  render();
  sendJson("/api/investment-brain/hypothesis-development/" + encodeURIComponent(id) + "/approve", "POST", ontologyAccountPayload({
    reviewedBy: "web-main",
    reviewReason: "검증 탭에서 자동 검증 완료 가설의 운영 배포 승인"
  })).then(function (payload) {
    var status = String(payload && payload.status || "");
    if (["deployed", "observing"].indexOf(status) < 0) throw new Error("가설 규칙 운영 반영 결과: " + (status || "확인 필요"));
    showSnackbar("검증된 가설 규칙을 운영 RuleBox에 반영했습니다.");
    return Promise.all([loadHypothesisDevelopment(true), loadOntologyExperiments(true)]);
  }).catch(function (error) {
    hypothesesState.hypothesisDevelopmentError = error.message || "검증된 가설 규칙을 운영 반영하지 못했습니다.";
    showSnackbar(hypothesesState.hypothesisDevelopmentError, "danger");
  }).finally(function () {
    hypothesesState.hypothesisDevelopmentAction = "";
    render();
  });
}

function runOntologyExperimentsOnce() {
  if (experimentsState.ontologyExperimentAction) return;
  if (isStaticPreviewHost()) {
    experimentsState.ontologyExperimentsError = "로컬 서버에서 실행할 수 있습니다.";
    showSnackbar(experimentsState.ontologyExperimentsError, "danger");
    render();
    return;
  }
  experimentsState.ontologyExperimentAction = "once";
  experimentsState.ontologyExperimentsError = "";
  render();
  sendJson("/api/ontology/experiments/once", "POST", { force: false })
    .then(function (payload) {
      showSnackbar("활성 실험 실행: " + (payload.runCount || 0) + "건, 건너뜀 " + (payload.skippedCount || 0) + "건");
      return loadOntologyExperiments(true);
    })
    .catch(function (error) {
      experimentsState.ontologyExperimentsError = error.message || "온톨로지 실험을 실행하지 못했습니다.";
      showSnackbar(experimentsState.ontologyExperimentsError, "danger");
    })
    .finally(function () {
      experimentsState.ontologyExperimentAction = "";
      render();
    });
}

function suggestOntologyExperiments() {
  if (experimentsState.ontologyExperimentAction) return;
  if (isStaticPreviewHost()) {
    experimentsState.ontologyExperimentsError = "로컬 서버에서 실행할 수 있습니다.";
    showSnackbar(experimentsState.ontologyExperimentsError, "danger");
    render();
    return;
  }
  experimentsState.ontologyExperimentAction = "suggest";
  experimentsState.ontologyExperimentsError = "";
  render();
  sendJson("/api/ontology/experiments/suggest", "POST", ontologyAccountPayload({ trigger: "ontology-lab-manual-suggest", activate: true, run: true }))
    .then(function (payload) {
      showSnackbar(
        payload && payload.createdCount ? "AI 실험 제안 " + payload.createdCount + "건을 등록하고 실행했습니다." : "새로 등록할 AI 실험 제안이 없습니다.",
        payload && payload.createdCount ? "success" : "caution"
      );
      return loadOntologyExperiments(true);
    })
    .catch(function (error) {
      experimentsState.ontologyExperimentsError = error.message || "AI 실험 제안을 생성하지 못했습니다.";
      showSnackbar(experimentsState.ontologyExperimentsError, "danger");
    })
    .finally(function () {
      experimentsState.ontologyExperimentAction = "";
      render();
    });
}

function runOntologyExperiment(experimentId) {
  ontologyExperimentCommand(experimentId, "run", "실험을 실행했습니다.");
}

function activateOntologyExperiment(experimentId) {
  ontologyExperimentCommand(experimentId, "activate", "실험을 활성화했습니다.");
}

function pauseOntologyExperiment(experimentId) {
  ontologyExperimentCommand(experimentId, "pause", "실험을 일시정지했습니다.");
}

function applyOntologyExperiment(experimentId, options) {
  options = options || {};
  var experiment = ontologyExperimentById(experimentId);
  var latest = ontologyExperimentLatestRun(experiment);
  var readiness = String(((latest.promotionReadiness || {}).status) || latest.promotionStatus || "");
  var gate = ontologyExperimentPromotionGate(experiment, latest);
  var payload = {};
  var recommendationIds = Array.isArray(options.recommendationIds) ? uniqueTextItems(options.recommendationIds) : [];
  if (options.requireRecommendationSelection && !recommendationIds.length) {
    showSnackbar("적용할 제안을 먼저 선택하세요.", "caution");
    return;
  }
  if (recommendationIds.length) payload.recommendationIds = recommendationIds;
  if (readiness === "needs-review" || gate.requiresReviewApproval) {
    if (window.confirm && !window.confirm("이 실험은 needs-review 상태입니다. 검토 승인 기록을 남기고 운영 온톨로지에 반영할까요?")) return;
    payload = Object.assign({}, payload, {
      reviewApproved: true,
      reviewedBy: "web-main",
      reviewReason: "웹 실험 탭에서 needs-review 결과를 수동 승인"
    });
  }
  ontologyExperimentCommand(
    experimentId,
    "apply",
    recommendationIds.length ? recommendationIds.length + "개 제안을 운영 반영했습니다." : "온톨로지 제안을 운영 반영했습니다.",
    payload
  );
}

function applySelectedOntologyExperimentRecommendations(experimentId) {
  var id = String(experimentId || "").trim();
  var selectedIds = ontologyExperimentSelectedRecommendationIds(id).filter(function (recommendationId) {
    return applicableOntologyExperimentRecommendationIds(id).indexOf(recommendationId) >= 0;
  });
  applyOntologyExperiment(id, { recommendationIds: selectedIds, requireRecommendationSelection: true });
}

function ontologyExperimentCommand(experimentId, action, successMessage, payload) {
  var id = String(experimentId || "").trim();
  if (!id || experimentsState.ontologyExperimentAction) return;
  if (isStaticPreviewHost()) {
    experimentsState.ontologyExperimentsError = "로컬 서버에서 실행할 수 있습니다.";
    showSnackbar(experimentsState.ontologyExperimentsError, "danger");
    render();
    return;
  }
  experimentsState.ontologyExperimentAction = action + ":" + id;
  experimentsState.ontologyExperimentsError = "";
  var requestPayload = action === "apply" ? ontologyAccountPayload(payload || {}) : (payload || {});
  render();
  sendJson("/api/ontology/experiments/" + encodeURIComponent(id) + "/" + action, "POST", requestPayload)
    .then(function (responsePayload) {
      var failureMessage = ontologyExperimentCommandFailureMessage(action, responsePayload);
      if (failureMessage) throw new Error(failureMessage);
      if (action === "apply") clearOntologyExperimentRecommendationSelection(id, requestPayload.recommendationIds);
      showSnackbar(successMessage || "실험 상태를 변경했습니다.");
      return loadOntologyExperiments(true);
    })
    .catch(function (error) {
      experimentsState.ontologyExperimentsError = error.message || "온톨로지 실험 요청에 실패했습니다.";
      showSnackbar(experimentsState.ontologyExperimentsError, "danger");
    })
    .finally(function () {
      experimentsState.ontologyExperimentAction = "";
      render();
    });
}

function ontologyExperimentCommandFailureMessage(action, payload) {
  payload = payload && typeof payload === "object" ? payload : {};
  var status = String(payload.status || "");
  var reason = String(payload.reason || "");
  if (status === "not-found") return "실험을 찾지 못했습니다.";
  if (status === "no-result") return "아직 적용할 실험 결과가 없습니다.";
  if (action === "apply" && reason === "experiment-needs-review-approval") return "needs-review 실험은 검토 승인 후 적용할 수 있습니다.";
  if (action === "apply" && reason === "experiment-recommendations-not-found") return "선택한 제안 중 현재 결과에 없는 항목이 있습니다.";
  if (action === "apply" && reason === "experiment-selected-recommendations-not-applicable") return "선택한 제안은 자동 적용 대상이 아닙니다.";
  if (action === "apply" && status === "not-ready") return "완료된 샌드박스 실행 결과가 있어야 적용할 수 있습니다." + (reason ? " (" + reason + ")" : "");
  if (action === "apply" && status === "disabled") return "온톨로지 저장소가 비활성화되어 적용할 수 없습니다.";
  if (action === "apply" && status === "pending") return "온톨로지 제안 적용이 완료되지 않았습니다.";
  if (action === "apply" && status === "error") return "온톨로지 제안을 운영 반영하지 못했습니다.";
  return "";
}

export { activateOntologyExperiment, applyOntologyExperiment, applySelectedOntologyExperimentRecommendations, approveHypothesisDevelopment, loadHypothesisDevelopment, loadOntologyExperiments, pauseOntologyExperiment, processHypothesisDevelopment, runOntologyExperiment, runOntologyExperimentsOnce, suggestOntologyExperiments };
