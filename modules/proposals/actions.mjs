import { normalizeStrategyProposalSection } from "../navigation/routes.mjs";
import { approveStrategyProposal, recordStrategyProposalPerformance, validateStrategyProposal } from "./requests.mjs";
import { render } from "../render/scheduler.mjs";
import { proposalsState } from "../state/proposals.mjs";

function bindProposalsControls(app) {
Array.prototype.slice.call(app.querySelectorAll("[data-strategy-proposal-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var id = button.getAttribute("data-strategy-proposal-select") || "";
      if (!id || id === proposalsState.activeStrategyProposalId) return;
      proposalsState.activeStrategyProposalId = id;
      proposalsState.activeStrategyProposalSection = "summary";
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-strategy-proposal-section]")).forEach(function (button) {
    button.addEventListener("click", function () {
      proposalsState.activeStrategyProposalSection = normalizeStrategyProposalSection(button.getAttribute("data-strategy-proposal-section"));
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-strategy-proposal-validate]")).forEach(function (button) {
    button.addEventListener("click", function () {
      validateStrategyProposal(button.getAttribute("data-strategy-proposal-validate"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-strategy-proposal-approve]")).forEach(function (button) {
    button.addEventListener("click", function () {
      approveStrategyProposal(button.getAttribute("data-strategy-proposal-approve"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-strategy-proposal-performance-form]")).forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      recordStrategyProposalPerformance(form.getAttribute("data-strategy-proposal-performance-form"), form);
    });
  });
}

export { bindProposalsControls };
