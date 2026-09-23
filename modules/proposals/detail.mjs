import { editorWorkDetailPayload } from "../navigation/detail.mjs";
import { renderStrategyProposalConsolePanel } from "./workspace.mjs";

function strategyProposalsWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Investment Detail",
    "전략 제안",
    "승인 후보와 성과 기록은 판단 콘솔에서 분리해 상세로 검토합니다.",
    renderStrategyProposalConsolePanel()
  );
}

export { strategyProposalsWorkDetailPayload };
