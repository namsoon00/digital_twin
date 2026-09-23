import { renderAccountBalancePanel, renderAccountConnectionsPanel, renderAccountDataHistoryPanel } from "./balance.mjs";
import { renderAdminAccountPanel } from "./editor.mjs";
import { editorWorkDetailPayload } from "../navigation/detail.mjs";
import { shellState } from "../state/shell.mjs";

function accountConnectionsWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Account Detail",
    "증권사 연결과 데이터 출처",
    "기본 계정 콘솔에서는 상태만 보고, API 출처와 품질 원장은 여기서 확인합니다.",
    renderAccountConnectionsPanel(shellState.snapshot || {})
  );
}

function accountBalanceWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Account Detail",
    "자산 검증 상세",
    "평가액, 현금, 환율, 보유 원장 산식을 한 화면에서 대조합니다.",
    renderAccountBalancePanel(shellState.snapshot || {})
  );
}

function accountHistoryWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Account Detail",
    "계정 데이터 이력",
    "스냅샷 생성 시각, 캐시 사용 여부, 데이터 신선도를 확인합니다.",
    renderAccountDataHistoryPanel(shellState.snapshot || {})
  );
}

function accountIdentityWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Account Settings",
    "계정 설정",
    "계정 식별값과 API 자격 정보는 기본 화면에서 숨기고 필요한 때만 편집합니다.",
    renderAdminAccountPanel()
  );
}

export { accountBalanceWorkDetailPayload, accountConnectionsWorkDetailPayload, accountHistoryWorkDetailPayload, accountIdentityWorkDetailPayload };
