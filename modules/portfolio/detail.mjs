import { editorWorkDetailPayload } from "../navigation/detail.mjs";
import { formatClock } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { portfolioState } from "../state/portfolio.mjs";

function portfolioInterpretationDetailRows(title, rows, emptyMessage) {
  rows = Array.isArray(rows) ? rows : [];
  return [
    '<section class="work-detail-section">',
    '<strong>' + escapeHtml(title) + '</strong>',
    rows.length ? '<div class="work-detail-list">' + rows.map(function (row) {
      var item = row && typeof row === "object" ? row : { label: String(row || ""), detail: "" };
      return '<div class="work-detail-row portfolio-interpretation-detail-row"><span></span><span><strong>' + escapeHtml(item.label || item.value || item.detail || "확인 항목") + '</strong><em>' + escapeHtml(item.detail || item.value || "") + '</em></span><b>' + escapeHtml(item.value && item.label ? item.value : "") + '</b></div>';
    }).join("") + '</div>' : '<p>' + escapeHtml(emptyMessage || "저장된 항목이 없습니다.") + '</p>',
    '</section>'
  ].join("");
}

function portfolioInterpretationWorkDetailPayload() {
  var item = portfolioState.portfolioInterpretation || ((portfolioState.portfolioReadModels.summary || {}).interpretation || {});
  if (portfolioState.portfolioInterpretationLoading && !item.contract) {
    return editorWorkDetailPayload(
      "Portfolio Interpretation",
      "포트폴리오 해석",
      "저장된 계산·TypeDB·AI 결과",
      '<div class="cws-loading" aria-busy="true"><span></span><strong>최신 해석 리비전을 확인하고 있습니다.</strong></div>'
    );
  }
  if (!item.contract) {
    return editorWorkDetailPayload(
      "Portfolio Interpretation",
      "포트폴리오 해석",
      "해석 데이터 없음",
      '<section class="work-detail-section"><strong>해석을 불러오지 못했습니다.</strong><p>' + escapeHtml(portfolioState.portfolioInterpretationError || "잠시 후 다시 확인하세요.") + '</p></section>'
    );
  }
  var ai = item.ai || {};
  var revision = item.revision || {};
  var sources = (Array.isArray(item.sources) ? item.sources : []).map(function (source) {
    return {
      label: source.label || source.kind || "출처",
      detail: [source.kind, source.id, source.kind === "ai" ? (source.used ? "사용" : "미사용") : ""].filter(Boolean).join(" · ")
    };
  });
  var trace = item.trace || {};
  var traceRows = Object.keys(trace).filter(function (key) { return trace[key]; }).map(function (key) {
    return { label: key, detail: String(trace[key]) };
  });
  var conflictRows = (Array.isArray(item.conflicts) ? item.conflicts : []).map(function (row) {
    return row && typeof row === "object" ? row : { label: "반대 근거", detail: String(row || "") };
  });
  return {
    kicker: "Portfolio Interpretation",
    title: "포트폴리오 해석 근거",
    meta: [item.statusLabel, item.dataState, item.generatedAt ? formatClock(item.generatedAt) : ""].filter(Boolean).join(" · "),
    body: [
      '<section class="work-detail-section primary"><strong>' + escapeHtml(item.headline || "현재 포트폴리오") + '</strong><p>' + escapeHtml(item.rationale || "") + '</p></section>',
      '<section class="work-detail-section"><strong>AI 실행과 리비전</strong><div class="work-detail-grid"><div class="work-detail-card"><span>AI</span><strong>' + escapeHtml(ai.executed ? (ai.current ? "최신 해석" : "이전 해석") : "실행 없음") + '</strong><p>' + escapeHtml(ai.rationale || "중요한 판단 변화가 없어 AI 결과를 생성하지 않았습니다.") + '</p></div><div class="work-detail-card"><span>리비전</span><strong>' + escapeHtml(revision.state || "unknown") + '</strong><p>' + escapeHtml([revision.currentObservedAt ? "원장 " + formatClock(revision.currentObservedAt) : "", revision.interpretedAt ? "해석 " + formatClock(revision.interpretedAt) : ""].filter(Boolean).join(" · ")) + '</p></div></div></section>',
      portfolioInterpretationDetailRows("현재 위험 요인", item.drivers, "현재 정책 이탈이 없습니다."),
      portfolioInterpretationDetailRows("반대 근거와 충돌", conflictRows, "저장된 반대 근거나 충돌이 없습니다."),
      portfolioInterpretationDetailRows("부족한 데이터", (item.missingData || []).map(function (value) { return { label: value, detail: "판단 전 보완 필요" }; }), "필수 데이터 누락이 기록되지 않았습니다."),
      portfolioInterpretationDetailRows("다음 확인", (item.nextChecks || []).map(function (value) { return { label: value, detail: "다음 평가 주기 확인" }; }), "추가 확인 조건이 없습니다."),
      portfolioInterpretationDetailRows("의견 무효화 조건", (item.invalidationConditions || []).map(function (value) { return { label: value, detail: "성립 시 의견 재검토" }; }), "저장된 무효화 조건이 없습니다."),
      portfolioInterpretationDetailRows("사용한 출처", sources, "출처 정보가 없습니다."),
      '<details class="work-detail-section"><summary><strong>감사 추적 ID</strong></summary>' + (traceRows.length ? '<div class="work-detail-list">' + traceRows.map(function (row) { return '<div class="work-detail-row portfolio-interpretation-detail-row"><span></span><span><strong>' + escapeHtml(row.label) + '</strong><em>' + escapeHtml(row.detail) + '</em></span></div>'; }).join("") + '</div>' : '<p>저장된 추적 ID가 없습니다.</p>') + '</details>'
    ].join("")
  };
}

export { portfolioInterpretationWorkDetailPayload };
