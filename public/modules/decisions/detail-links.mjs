import { renderInvestmentDataLineagePanel, renderInvestmentEvidenceWorkbenchPanel, renderInvestmentMoneyFlowPanel } from "./evidence.mjs";
import { renderStrategyDataPanel } from "./legacy.mjs";
import { renderInvestmentTabWorkspace } from "./navigation.mjs";
import { investmentReasoningCards, renderInvestmentChartControlPanel, renderInvestmentGraphGatePanel, renderInvestmentIntegratedChartPanel } from "./strategy.mjs";
import { editorWorkDetailPayload } from "../navigation/detail.mjs";
import { renderOntologyExecutionPlanPanel } from "../ontology/execution.mjs";
import { renderOntologyMacroSignalPanel } from "../ontology/macro-view.mjs";
import { ontologyStrategyParts } from "../ontology/strategy.mjs";
import { shellState } from "../state/shell.mjs";

function strategyEvidenceWorkDetailPayload() {
  var snapshot = shellState.snapshot || {};
  var parts = ontologyStrategyParts(snapshot);
  return editorWorkDetailPayload(
    "Investment Detail",
    "투자 근거 상세",
    "선택한 판단의 근거 카드, 실행 계획, 데이터 계보를 확인합니다.",
    renderInvestmentTabWorkspace("evidence", [
      { role: "main", html: renderInvestmentEvidenceWorkbenchPanel(snapshot) },
      { role: "side", html: renderOntologyExecutionPlanPanel(investmentReasoningCards(snapshot), parts) + renderInvestmentDataLineagePanel(snapshot) + renderStrategyDataPanel(snapshot) }
    ])
  );
}

function strategyChartsWorkDetailPayload() {
  var snapshot = shellState.snapshot || {};
  var parts = ontologyStrategyParts(snapshot);
  return editorWorkDetailPayload(
    "Investment Detail",
    "통합 차트 상세",
    "가격, 수급, 거시 신호, 그래프 게이트를 같은 화면에서 비교합니다.",
    renderInvestmentTabWorkspace("charts", [
      { role: "summary", html: renderInvestmentChartControlPanel(snapshot, parts) },
      { role: "main", html: renderInvestmentIntegratedChartPanel(snapshot, parts) },
      { role: "side", html: renderInvestmentMoneyFlowPanel(snapshot) + renderOntologyMacroSignalPanel(parts) + renderInvestmentGraphGatePanel(snapshot) + renderInvestmentDataLineagePanel(snapshot) }
    ])
  );
}

export { strategyChartsWorkDetailPayload, strategyEvidenceWorkDetailPayload };
