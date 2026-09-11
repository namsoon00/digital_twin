import { renderOntologyExperimentListPanel } from "./work-detail.mjs";
import { renderOntologyExperimentAuditPanel, renderOntologyExperimentComparisonPanel, renderOntologyExperimentLatestPanel, renderOntologyExperimentPromotionPanel, renderOntologyExperimentReplayPanel, renderOntologyExperimentSelectedPanel } from "./workspace.mjs";
import { editorWorkDetailPayload } from "../navigation/detail.mjs";
import { renderStrategyProposalConsolePanel } from "../proposals/workspace.mjs";

function experimentValidationWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Experiment Detail",
    "전략 검증 리플레이",
    "선택한 실험의 샌드박스 실행과 후보 비교 결과를 검토합니다.",
    '<div class="ontology-experiment-workbench ontology-experiment-workbench-validation"><div class="ontology-experiment-workbench-side">' + renderOntologyExperimentListPanel({ selectable: true, compact: true }) + '</div><div class="ontology-experiment-workbench-main">' + renderOntologyExperimentSelectedPanel() + renderOntologyExperimentReplayPanel() + renderOntologyExperimentComparisonPanel() + '</div></div>'
  );
}

function experimentPromotionWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Experiment Detail",
    "승격 심사",
    "운영 RuleBox에 반영 가능한지 체크리스트와 최근 실행 결과를 봅니다.",
    '<div class="ontology-experiment-workbench ontology-experiment-workbench-promotion"><div class="ontology-experiment-workbench-side">' + renderOntologyExperimentListPanel({ selectable: true, compact: true }) + '</div><div class="ontology-experiment-workbench-main">' + renderOntologyExperimentSelectedPanel() + renderOntologyExperimentPromotionPanel() + renderOntologyExperimentLatestPanel() + '</div></div>'
  );
}

function experimentAuditWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Experiment Detail",
    "실험 이력",
    "실행, 승격 판정, 운영 반영 이력을 확인합니다.",
    renderOntologyExperimentAuditPanel() + renderOntologyExperimentListPanel({ selectable: true })
  );
}

function experimentProposalsWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Experiment Detail",
    "전략 제안 검토",
    "검증된 가설의 조건, 성과, 승인 상태를 확인합니다.",
    renderStrategyProposalConsolePanel()
  );
}

export { experimentAuditWorkDetailPayload, experimentPromotionWorkDetailPayload, experimentProposalsWorkDetailPayload, experimentValidationWorkDetailPayload };
