import { decisionActionMeta } from "../decisions/selectors.mjs";
import { investmentFlowConsolePayload, investmentFlowStateTone, renderDecisionWorkspaceNavigation, renderInvestmentFlowStateLegend } from "../decisions/workspace.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { consolePageSlice, renderConsoleEmpty, renderConsoleListSkeleton, renderConsoleLiveRegion, renderConsoleManagedPage, renderConsolePager, renderConsoleSurface } from "../shared/console.mjs";
import { renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { decisionsState } from "../state/decisions.mjs";

function validationOperatorDetailType(stageId) {
  var values = {
    source: "feed-quality",
    evidence: "strategy-evidence-board",
    relation: "strategy-graphs-board",
    hypothesis: "hypothesis-governance",
    validation: "experiment-validation-board",
    inference: "strategy-trace-board",
    decision: "decision-action-queue",
    notification: "notification-diagnostics-board"
  };
  return values[String(stageId || "")] || "settings-diagnostics";
}

function renderValidationUserConsole() {
  var payload = investmentFlowConsolePayload();
  var summary = payload.summary || {};
  var readiness = summary.readiness || summary.validation || {};
  var cases = (Array.isArray(payload.items) ? payload.items : []).filter(function (item) {
    return String(((item.attention || {}).state) || "review") !== "observe";
  });
  var items = [];
  cases.forEach(function (item) {
    var attention = item.attention && typeof item.attention === "object" ? item.attention : {};
    var issues = Array.isArray(attention.issues) ? attention.issues : [];
    if (!issues.length) issues = [{ id: item.phase || "case", label: item.phaseLabel || "근거 점검", state: item.readinessState, stateLabel: item.readinessLabel, reason: item.headline, effect: item.nextAction }];
    issues.forEach(function (issue, index) {
      items.push({ caseItem: item, issue: issue, key: String(item.caseId || item.episodeId || item.symbol) + ":" + String(issue.id || index) });
    });
  });
  var page = consolePageSlice(items, "validation", 8);
  var groupLabels = {
    data: ["원천 데이터", "판단 시점 데이터의 부족·지연·적용 가능성을 확인합니다."],
    inference: ["관계 추론", "TypeDB 관계와 규칙 실행 상태를 확인합니다."],
    ai: ["AI 판단", "경쟁 가설 비교와 최종 의견 작성 상태를 확인합니다."],
    decision: ["현재 의견", "판단 보류와 행동 제한 조건을 확인해야 합니다."],
    outcome: ["결과 관측", "판단 이후 성과와 사용자 행동 연결 상태입니다."],
    integrity: ["판단 기록", "당시 스냅샷과 추론 상세의 연결 상태를 확인합니다."]
  };
  var grouped = {};
  page.items.forEach(function (entry) {
    var key = String((entry.issue || {}).id || "decision");
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(entry);
  });
  var body = page.items.length
    ? '<div class="oa-assurance-groups" data-console-keyed-list="validation-subjects">' + Object.keys(grouped).map(function (key) {
      var meta = groupLabels[key] || ["근거 점검", "추가 확인이 필요합니다."];
      return [
        '<section class="oa-assurance-group">',
        '<header><div><strong>' + escapeHtml(meta[0]) + '</strong><p>' + escapeHtml(meta[1]) + '</p></div><span>' + escapeHtml(grouped[key].length) + '건</span></header>',
        '<div>', grouped[key].map(function (entry) {
          var item = entry.caseItem || {};
          var issue = entry.issue || {};
          var decision = item.decision || {};
          var action = decisionActionMeta(decision.action, decision.action);
          var tone = investmentFlowStateTone(issue.state || item.readinessState);
          return [
            '<article class="oa-assurance-row" data-flow-state="' + escapeHtml(issue.state || item.readinessState || "warning") + '">',
            '<div class="oa-assurance-row-main"><span><strong>' + escapeHtml(item.name || item.symbol || "종목") + '</strong><em>' + escapeHtml([item.symbol, action.label].filter(Boolean).join(" · ")) + '</em></span><b class="' + escapeHtml(tone) + '">' + escapeHtml(issue.stateLabel || item.readinessLabel || "확인 필요") + '</b></div>',
            '<p><strong>' + escapeHtml(issue.label || meta[0]) + '</strong> · ' + escapeHtml(issue.reason || item.headline || "판단 근거를 확인하세요.") + '</p>',
            '<div class="oa-assurance-next"><span>판단 영향</span><strong>' + escapeHtml(issue.effect || item.nextAction || "투자 케이스의 부족한 근거를 확인하세요.") + '</strong></div>',
            '<footer>' + renderRecordChangedAt(item) + renderWorkDetailButton("investment-case", item.caseId || item.episodeId || "", "근거 상세", "text-button compact primary") + '</footer>',
            '</article>'
          ].join("");
        }).join(""), '</div></section>'
      ].join("");
    }).join("") + '</div>'
    : (decisionsState.investmentFlowLoading
      ? renderConsoleListSkeleton("oa-case-row", ["종목", "근거", "다음 확인"], 4)
      : renderConsoleEmpty(decisionsState.investmentFlowError ? "근거 점검을 불러오지 못했습니다" : "추가 점검이 필요한 의견이 없습니다", decisionsState.investmentFlowError || "현재 의견의 필수 데이터와 관계가 준비되어 있습니다.", renderWorkDetailButton("investment-model-overview", "", "판단 기준 보기", "text-button compact")));
  var metrics = [
    { label: "점검 종목", value: cases.length + "건", detail: items.length + "개 확인 항목" },
    { label: "근거 충분", value: Number(readiness.pass || 0) + "건", detail: "판단 가능", tone: "watch" },
    { label: "자료 보완", value: Number(readiness.warning || 0) + Number(readiness.pending || 0) + "건", detail: "다음 확인", tone: Number(readiness.warning || readiness.pending) ? "caution" : "neutral" },
    { label: "판단 차단", value: Number(readiness.blocked || 0) + "건", detail: "행동 보류", tone: Number(readiness.blocked || 0) ? "danger" : "neutral" },
    { label: "운영 오류", value: Number(readiness.error || 0) + "건", detail: "설정에서 진단", tone: Number(readiness.error || 0) ? "danger" : "watch", target: { type: "detail", value: "settings-diagnostics" } }
  ];
  return renderConsoleManagedPage("experiments", metrics, [
    '<section class="oa-assurance-context"><span>EVIDENCE ASSURANCE</span><strong>현재 의견을 막는 이유만 확인합니다.</strong><p>모델 실험이나 알림 전달 상태는 이 목록에 포함하지 않습니다.</p></section>',
    '<details class="oa-case-process"><summary><span><strong>상태 색상 기준</strong><em>판단 차단과 운영 오류를 구분합니다.</em></span></summary><section class="oa-flow-detail-section">' + renderInvestmentFlowStateLegend() + '</section></details>',
    renderConsoleSurface({ kicker: "REVIEW QUEUE", title: "근거 점검 대상", description: "부족한 단계별로 묶고 해결할 다음 행동을 함께 표시합니다.", meta: items.length + "건", body: renderConsoleLiveRegion("validation-subject-body", body), footer: renderConsolePager("validation", page) })
  ].join(""), {
    leading: renderDecisionWorkspaceNavigation("experiments"),
    loading: decisionsState.investmentFlowLoading && !decisionsState.investmentFlowLoaded
  });
}

function renderValidationConsole(snapshot) {
  return renderValidationUserConsole(snapshot);
}

export { renderValidationConsole, validationOperatorDetailType };
