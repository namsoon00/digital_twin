import { renderAdminDeliveryPanel } from "../decisions/legacy.mjs";
import { editorWorkDetailPayload } from "../navigation/detail.mjs";
import { renderNotificationAdvancedRulePanel, renderNotificationTemplateManagerPanel, renderNotificationThresholdPanel } from "./editor.mjs";
import { activeNotificationRule, renderAdminMessagePanel, renderNotificationCandidatePanel, renderNotificationDiagnosticsPanel } from "./workspace.mjs";
import { shellState } from "../state/shell.mjs";

function notificationDeliveryWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Delivery",
    "알림 전달 설정",
    "웹 링크, Telegram, 저장 상태",
    renderAdminDeliveryPanel()
  );
}

function notificationRuleDiagnosticsWorkDetailPayload() {
  var rule = activeNotificationRule();
  return editorWorkDetailPayload(
    "Diagnostics Rule",
    "반복·장 상태 상세",
    rule.label + " · 유사 메시지, 장 상태 참고, 조건 변화",
    renderNotificationAdvancedRulePanel()
  );
}

function notificationThresholdWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Advanced",
    "알림 임계값 상세",
    "실시간·모델·외부 데이터 알림 기준",
    renderNotificationThresholdPanel()
  );
}

function notificationCandidatesWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Notification Detail",
    "알림 후보 신호",
    "발송 전 후보, 종목 감지, 포트폴리오 상태를 전체화면에서 검토합니다.",
    renderNotificationCandidatePanel(shellState.snapshot || {})
  );
}

function notificationPolicyWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Notification Settings",
    "알림 정책",
    "메시지 타입별 사용 여부와 발송 게이트는 설정 레이어에서 관리합니다.",
    renderAdminMessagePanel()
  );
}

function notificationTemplatesWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Notification Settings",
    "알림 템플릿",
    "본문 형식과 미리보기는 운영 콘솔에서 분리해 필요할 때만 엽니다.",
    renderNotificationTemplateManagerPanel()
  );
}

function notificationDiagnosticsWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Notification Detail",
    "알림 진단",
    "채널, 반복 차단, 임계값, 발송 실패 원인을 점검합니다.",
    renderNotificationDiagnosticsPanel()
  );
}

export { notificationCandidatesWorkDetailPayload, notificationDeliveryWorkDetailPayload, notificationDiagnosticsWorkDetailPayload, notificationPolicyWorkDetailPayload, notificationRuleDiagnosticsWorkDetailPayload, notificationTemplatesWorkDetailPayload, notificationThresholdWorkDetailPayload };
