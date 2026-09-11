import { strategyProposalsDesiredDetailLevel } from "../ontology/requests.mjs";
import { strategyProposalById } from "./workspace.mjs";
import { render } from "../render/scheduler.mjs";
import { requestJson } from "../requests/json.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { proposalsState } from "../state/proposals.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";

function loadStrategyProposals(force) {
  var detailLevel = strategyProposalsDesiredDetailLevel();
  if (isStaticPreviewHost()) {
    proposalsState.strategyProposals = { proposals: [], count: 0, summary: { count: 0, statuses: {} } };
    proposalsState.strategyProposalsLoaded = true;
    proposalsState.strategyProposalsDetailLevel = detailLevel;
    proposalsState.strategyProposalsError = "";
    return Promise.resolve(proposalsState.strategyProposals);
  }
  if (proposalsState.strategyProposalsLoading && !force) return Promise.resolve(proposalsState.strategyProposals);
  proposalsState.strategyProposalsLoading = true;
  proposalsState.strategyProposalsError = "";
  var listPath = "/api/investment-strategy-proposals?limit=200"
    + (detailLevel === "summary" ? "&summary=1" : "");
  return Promise.all([
    requestJson(listPath, {
      key: "investment-strategy-proposals:" + detailLevel,
      force: Boolean(force)
    }),
    requestJson("/api/investment-strategy-proposals/status")
  ])
    .then(function (results) {
      var listPayload = results[0] && typeof results[0] === "object" ? results[0] : {};
      var statusPayload = results[1] && typeof results[1] === "object" ? results[1] : {};
      var proposals = Array.isArray(listPayload.proposals) ? listPayload.proposals : [];
      proposalsState.strategyProposals = Object.assign({}, listPayload, {
        proposals: proposals,
        count: listPayload.count == null ? proposals.length : listPayload.count,
        summary: statusPayload
      });
      proposalsState.strategyProposalsLoaded = true;
      proposalsState.strategyProposalsDetailLevel = listPayload.detailLevel || detailLevel;
      if (!strategyProposalById(proposalsState.activeStrategyProposalId) && proposals.length) {
        proposalsState.activeStrategyProposalId = proposals[0].id || "";
      }
      if (!proposals.length) proposalsState.activeStrategyProposalId = "";
      return proposalsState.strategyProposals;
    })
    .catch(function (error) {
      proposalsState.strategyProposalsError = error.message || "전략 제안을 읽지 못했습니다.";
      return null;
    })
    .finally(function () {
      proposalsState.strategyProposalsLoading = false;
      if (shellState.snapshot) render();
    });
}

function strategyProposalActionDisabled() {
  return Boolean(isStaticPreviewHost() || settingsState.serverSettingsLocked || proposalsState.strategyProposalAction);
}

function validateStrategyProposal(proposalId) {
  strategyProposalCommand(proposalId, "validate", "전략 제안의 TypeDB 물질화 검증을 실행했습니다.");
}

function approveStrategyProposal(proposalId) {
  var proposal = strategyProposalById(proposalId);
  if (!proposal) return;
  var status = String(proposal.status || "");
  if (status === "retired" || status === "deployed") {
    showSnackbar("운영 반영 또는 폐기된 제안은 승인 상태로 되돌리지 않습니다.", "caution");
    return;
  }
  if (!window.confirm("전략 제안을 승인 기록으로 남기고 운영 반영 후보 상태로 바꿉니다. 계속할까요?")) return;
  strategyProposalCommand(proposalId, "approve", "전략 제안을 승인했습니다.", {
    reviewedBy: "web-main",
    reviewReason: "웹 전략 제안 화면에서 수동 승인"
  });
}

function recordStrategyProposalPerformance(proposalId, form) {
  var id = String(proposalId || "").trim();
  if (!id || !form) return;
  var payload = {
    source: "web-main",
    portfolioReturnPct: strategyProposalFormNumber(form, "portfolioReturnPct"),
    benchmarkReturnPct: strategyProposalFormNumber(form, "benchmarkReturnPct"),
    maxDrawdownPct: strategyProposalFormNumber(form, "maxDrawdownPct"),
    signalCount: strategyProposalFormInteger(form, "signalCount"),
    falsePositiveCount: strategyProposalFormInteger(form, "falsePositiveCount"),
    notes: strategyProposalFormValue(form, "notes")
  };
  if (
    payload.portfolioReturnPct == null &&
    payload.benchmarkReturnPct == null &&
    payload.maxDrawdownPct == null &&
    payload.signalCount == null &&
    payload.falsePositiveCount == null &&
    !payload.notes
  ) {
    showSnackbar("기록할 성과 값이나 메모를 입력하세요.", "caution");
    return;
  }
  strategyProposalCommand(id, "performance", "전략 제안 성과 표본을 기록했습니다.", payload);
}

function strategyProposalFormValue(form, name) {
  var field = form.querySelector('[name="' + name + '"]');
  return field ? String(field.value || "").trim() : "";
}

function strategyProposalFormNumber(form, name) {
  var value = strategyProposalFormValue(form, name);
  if (!value) return null;
  var number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function strategyProposalFormInteger(form, name) {
  var value = strategyProposalFormNumber(form, name);
  return value == null ? null : Math.round(value);
}

function strategyProposalCommand(proposalId, action, successMessage, payload) {
  var id = String(proposalId || "").trim();
  if (!id || proposalsState.strategyProposalAction) return;
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    proposalsState.strategyProposalsError = "로컬 서버에서만 전략 제안을 변경할 수 있습니다.";
    showSnackbar(proposalsState.strategyProposalsError, "danger");
    render();
    return;
  }
  proposalsState.strategyProposalAction = action + ":" + id;
  proposalsState.strategyProposalsError = "";
  render();
  sendJson("/api/investment-strategy-proposals/" + encodeURIComponent(id) + "/" + action, "POST", payload || {})
    .then(function (result) {
      var failureMessage = strategyProposalCommandFailureMessage(action, result);
      if (failureMessage) throw new Error(failureMessage);
      if (result && result.proposal && result.proposal.id) proposalsState.activeStrategyProposalId = result.proposal.id;
      showSnackbar(successMessage || "전략 제안 상태를 변경했습니다.");
      return loadStrategyProposals(true);
    })
    .catch(function (error) {
      proposalsState.strategyProposalsError = error.message || "전략 제안 요청에 실패했습니다.";
      showSnackbar(proposalsState.strategyProposalsError, "danger");
    })
    .finally(function () {
      proposalsState.strategyProposalAction = "";
      render();
    });
}

function strategyProposalCommandFailureMessage(action, payload) {
  payload = payload && typeof payload === "object" ? payload : {};
  var status = String(payload.status || "");
  var reason = String(payload.reason || "");
  if (status === "not-found") return "전략 제안을 찾지 못했습니다.";
  if (action === "validate" && status === "requires-typedb") return "TypeDB 물질화 미리보기를 사용할 수 없습니다.";
  if (action === "validate" && status === "missing-rules") return "검증할 RuleBox 후보 규칙이 없습니다.";
  if (action === "validate" && status === "error") return "TypeDB 물질화 검증에 실패했습니다.";
  if (action === "approve" && status === "not-ready") return "현재 제안 상태에서는 승인할 수 없습니다." + (reason ? " (" + reason + ")" : "");
  return "";
}

export { approveStrategyProposal, loadStrategyProposals, recordStrategyProposalPerformance, strategyProposalActionDisabled, validateStrategyProposal };
