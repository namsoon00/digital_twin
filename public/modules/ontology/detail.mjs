import { renderAdminModelingPanel } from "../decisions/legacy.mjs";
import { renderInvestmentTabWorkspace } from "../decisions/navigation.mjs";
import { renderStrategyReviewStatusPanel, renderStrategyTraceOverviewPanel } from "../decisions/strategy.mjs";
import { editorWorkDetailPayload } from "../navigation/detail.mjs";
import { ontologyAuditRowByKey, ontologyAuditRowMeta, ontologyAuditRowTitle, ontologyAuditSection, ontologyAuditSectionLabel, renderOntologyAuditRowDetail, renderSystemOntologyAuditDetail } from "./audit.mjs";
import { renderOntologyCatalogPanel } from "./catalog.mjs";
import { renderAiPromptRegistryPanel, renderTypeDBRuleboxPanel } from "./governance.mjs";
import { ontologyStrategyParts } from "./strategy.mjs";
import { renderInvestmentOntologyWorkspacePanel } from "./world.mjs";
import { formatInteger } from "../shared/format.mjs";
import { shellState } from "../state/shell.mjs";

function ontologyAuditSectionWorkDetailPayload(key) {
  var sectionId = String(key || "tbox");
  var section = ontologyAuditSection(sectionId);
  return editorWorkDetailPayload(
    "Ontology Audit",
    (section.label || ontologyAuditSectionLabel(sectionId)) + " 상세",
    "감사 API 조회 범위 " + formatInteger((section.rows || []).length) + " / 전체 " + formatInteger(section.total || 0),
    renderSystemOntologyAuditDetail(sectionId)
  );
}

function ontologyAuditRowWorkDetailPayload(key) {
  var detail = ontologyAuditRowByKey(key);
  if (!detail.row) return null;
  return editorWorkDetailPayload(
    "Ontology Row",
    ontologyAuditRowTitle(detail.row),
    ontologyAuditSectionLabel(detail.sectionId) + " · " + ontologyAuditRowMeta(detail.row),
    renderOntologyAuditRowDetail(detail.row)
  );
}

function strategyRuleboxWorkDetailPayload() {
  return editorWorkDetailPayload(
    "RuleBox",
    "TypeDB RuleBox 편집",
    "RuleBox JSON, AI 후보, 버전 관리",
    renderTypeDBRuleboxPanel(shellState.snapshot || {})
  );
}

function strategyPromptWorkDetailPayload() {
  return editorWorkDetailPayload(
    "AI Prompts",
    "AI 프롬프트·게이트 편집",
    "알림 후보를 설명 가능한 투자 의견으로 바꾸는 프롬프트",
    renderAiPromptRegistryPanel(shellState.snapshot || {})
  );
}

function strategyModelPolicyWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Model Policy",
    "모델 기준·가중치 편집",
    "매수·매도 판단 기준, 가중치, 계산식",
    renderAdminModelingPanel(shellState.snapshot || {})
  );
}

function strategyGraphsWorkDetailPayload() {
  var snapshot = shellState.snapshot || {};
  var parts = ontologyStrategyParts(snapshot);
  return editorWorkDetailPayload(
    "Investment Detail",
    "온톨로지 그래프 상세",
    "TBox, ABox, 근거 관계, 추론 결과를 전체화면으로 확인합니다.",
    renderInvestmentTabWorkspace("graphs", [
      { role: "full", html: renderOntologyCatalogPanel() },
      { role: "full", html: renderInvestmentOntologyWorkspacePanel(snapshot, parts) }
    ])
  );
}

function strategyTraceBoardWorkDetailPayload() {
  var snapshot = shellState.snapshot || {};
  var parts = ontologyStrategyParts(snapshot);
  return editorWorkDetailPayload(
    "Investment Detail",
    "검증·리뷰 상세",
    "모델 리뷰, 관계 trace, 품질 점검을 전체화면에서 확인합니다.",
    renderInvestmentTabWorkspace("trace", [
      { role: "summary", html: renderStrategyReviewStatusPanel(snapshot, parts) },
      { role: "full", html: renderStrategyTraceOverviewPanel(snapshot, parts) }
    ])
  );
}

export { ontologyAuditRowWorkDetailPayload, ontologyAuditSectionWorkDetailPayload, strategyGraphsWorkDetailPayload, strategyModelPolicyWorkDetailPayload, strategyPromptWorkDetailPayload, strategyRuleboxWorkDetailPayload, strategyTraceBoardWorkDetailPayload };
