import { renderInfoIconButton, renderWorkDetailButton } from "../navigation/detail.mjs";
import { activePageMode, activeSectionForPageMode, modeSectionsForPage } from "../navigation/routes.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { strategySections } from "../shell/catalog.mjs";
import { decisionsState } from "../state/decisions.mjs";

function renderStrategySectionBar() {
  var visibleSections = modeSectionsForPage("modeling", strategySections);
  var activeId = activeSectionForPageMode("modeling", strategySections, decisionsState.activeStrategySection);
  if (activePageMode("modeling") !== "settings") {
    return [
      '<div class="strategy-section-bar strategy-drilldown-bar" data-section-mode="results">',
      '<div class="strategy-section-tabs strategy-drilldown-rail" role="toolbar" aria-label="투자 판단 상세 보기">',
      renderWorkDetailButton("strategy-evidence-board", "", "투자 근거", "text-button compact"),
      renderWorkDetailButton("strategy-charts-board", "", "통합 차트", "text-button compact"),
      renderWorkDetailButton("strategy-graphs-board", "", "온톨로지", "text-button compact"),
      renderWorkDetailButton("strategy-proposals-board", "", "전략 제안", "text-button compact"),
      '<button class="text-button compact" type="button" data-strategy-section="hypotheses">가설 검증</button>',
      renderWorkDetailButton("strategy-trace-board", "", "검증·리뷰", "text-button compact"),
      renderInfoIconButton("modeling", "투자 판단 탭의 단일 화면 운영 방식"),
      '</div>',
      '</div>'
    ].join("");
  }
  return [
    '<div class="strategy-section-bar" data-section-mode="' + escapeHtml(activePageMode("modeling")) + '">',
    '<div class="strategy-section-tabs" role="tablist" aria-label="투자 판단 섹션">',
    visibleSections.map(function (item) {
      var active = activeId === item.id;
      return [
        '<button type="button" role="tab" class="' + (active ? "active" : "") + '" data-strategy-section="' + escapeHtml(item.id) + '"' + (active ? ' aria-selected="true"' : ' aria-selected="false"') + '>',
        '<strong>' + escapeHtml(item.label) + '</strong>',
        '<span>' + escapeHtml(item.description) + '</span>',
        '</button>'
      ].join("");
    }).join(""),
    '</div>',
    '</div>'
  ].join("");
}

function renderInvestmentTabWorkspace(kind, columns) {
  return [
    '<section class="investment-tab-workspace investment-tab-workspace-' + escapeHtml(kind || "overview") + '">',
    (columns || []).map(function (column) {
      return [
        '<div class="investment-tab-stack investment-tab-stack-' + escapeHtml(column.role || "main") + '">',
        column.html || "",
        '</div>'
      ].join("");
    }).join(""),
    '</section>'
  ].join("");
}

export { renderInvestmentTabWorkspace, renderStrategySectionBar };
