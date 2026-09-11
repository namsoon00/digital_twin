import { DEFAULT_SYMBOL_UNIVERSE_LIMIT } from "./constants.mjs";
import { loadSymbolUniverse } from "./universe.mjs";
import { render } from "../render/scheduler.mjs";
import { universeState } from "../state/universe.mjs";

function bindInstrumentsControls(app) {
var symbolSearchForm = app.querySelector("[data-symbol-search-form]");
if (symbolSearchForm) {
    symbolSearchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var query = symbolSearchForm.querySelector("[data-symbol-query]");
      var market = symbolSearchForm.querySelector("[data-symbol-market]");
      var limit = symbolSearchForm.querySelector("[data-symbol-limit]");
      universeState.symbolUniverseQuery = query ? query.value.trim() : "";
      universeState.symbolUniverseMarket = market ? market.value : "";
      universeState.symbolUniverseLimit = limit ? Number(limit.value || DEFAULT_SYMBOL_UNIVERSE_LIMIT) : universeState.symbolUniverseLimit;
      universeState.symbolUniverseOffset = 0;
      universeState.activeSymbolUniverseKey = "";
      universeState.symbolUniverseChangedKeys = {};
      loadSymbolUniverse();
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-symbol-select]")).forEach(function (button) {
    var selectSymbolRow = function (event) {
      if (event && event.preventDefault) event.preventDefault();
      universeState.activeSymbolUniverseKey = button.getAttribute("data-symbol-select") || "";
      render();
    };
    button.addEventListener("click", selectSymbolRow);
    if (String(button.tagName || "").toLowerCase() !== "button") {
      button.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        selectSymbolRow(event);
      });
    }
  });
Array.prototype.slice.call(app.querySelectorAll("[data-symbol-page]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var direction = button.getAttribute("data-symbol-page");
      var limit = Number(universeState.symbolUniverseLimit || DEFAULT_SYMBOL_UNIVERSE_LIMIT);
      if (direction === "prev") {
        universeState.symbolUniverseOffset = Math.max(0, Number(universeState.symbolUniverseOffset || 0) - limit);
      } else if (direction === "next") {
        universeState.symbolUniverseOffset = Number(universeState.symbolUniverseOffset || 0) + limit;
      }
      universeState.activeSymbolUniverseKey = "";
      loadSymbolUniverse();
    });
  });
}

export { bindInstrumentsControls };
