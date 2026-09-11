import { normalizePortfolioView } from "../navigation/routes.mjs";
import { portfolioReadModelBusy } from "./requests.mjs";
import { renderConsoleEmpty } from "../shared/console.mjs";
import { portfolioState } from "../state/portfolio.mjs";

function renderPortfolioConsole() {
  var workspace = window.OrbitAlphaConsoleWorkspaces;
  var view = normalizePortfolioView(portfolioState.activePortfolioView);
  var payload = portfolioState.portfolioReadModels[view] || {};
  var body = workspace && typeof workspace.renderPortfolio === "function"
    ? workspace.renderPortfolio(payload, view, {
        loading: portfolioReadModelBusy(view),
        error: portfolioState.portfolioReadModelErrorsByView[view] || ""
      })
    : renderConsoleEmpty("포트폴리오 화면을 준비하지 못했습니다", "웹 자산을 새로고침하세요.");
  return '<div class="managed-page oa-console-page oa-console-page-portfolio" data-console-workspace="portfolio">' + body + '</div>';
}

export { renderPortfolioConsole };
