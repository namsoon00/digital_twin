import { primeActiveTabData } from "../navigation/preload.mjs";
import { normalizeFeedSection, sectionModeForPage, writeFeedSectionHistory } from "../navigation/routes.mjs";
import { render } from "../render/scheduler.mjs";
import { deleteResearchEvidence, loadResearchEvidence, loadResearchEvidenceDetail } from "./requests.mjs";
import { marketState } from "../state/market.mjs";
import { navigationState } from "../state/navigation.mjs";
import { researchState } from "../state/research.mjs";

function bindResearchControls(app) {
Array.prototype.slice.call(app.querySelectorAll("[data-feed-detail-toggle]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var key = button.getAttribute("data-feed-detail-toggle") || "";
      researchState.expandedFeedDetail = researchState.expandedFeedDetail === key ? "" : key;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-research-evidence-toggle]")).forEach(function (button) {
    var selectResearchEvidence = function (event) {
      if (event && event.preventDefault) event.preventDefault();
      if (event && event.stopPropagation) event.stopPropagation();
      var key = button.getAttribute("data-research-evidence-toggle") || "";
      researchState.expandedResearchEvidenceKey = key;
      render();
      loadResearchEvidenceDetail(key);
    };
    button.addEventListener("click", selectResearchEvidence);
    if (String(button.tagName || "").toLowerCase() !== "button") {
      button.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        selectResearchEvidence(event);
      });
    }
  });
var researchEvidenceForm = app.querySelector("[data-research-evidence-form]");
if (researchEvidenceForm) {
    researchEvidenceForm.addEventListener("submit", function (event) {
      event.preventDefault();
      loadResearchEvidence(true);
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-research-filter]")).forEach(function (field) {
    var updateResearchFilter = function () {
      var name = field.getAttribute("data-research-filter");
      if (!name) return;
      researchState.researchEvidenceFilters[name] = field.value;
    };
    field.addEventListener("input", updateResearchFilter);
    field.addEventListener("change", updateResearchFilter);
  });
Array.prototype.slice.call(app.querySelectorAll("[data-research-delete]")).forEach(function (button) {
    button.addEventListener("click", function () {
      deleteResearchEvidence(button.getAttribute("data-research-delete"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-feed-section]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var section = normalizeFeedSection(button.getAttribute("data-feed-section"));
      if (section === marketState.activeFeedSection) return;
      marketState.activeFeedSection = section;
      navigationState.pageViewModes.feed = sectionModeForPage("feed", section);
      writeFeedSectionHistory(section);
      primeActiveTabData("feed");
      render({ transition: "section" });
    });
  });
}

export { bindResearchControls };
