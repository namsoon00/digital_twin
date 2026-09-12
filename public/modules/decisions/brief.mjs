import { escapeHtml } from "../shared/text.mjs";
import { opinionRecency } from "./recency.mjs";

function briefTexts(values) {
  return [...new Set((values || []).map(function (value) {
    return String(typeof value === "string" ? value : (value || {}).summary || (value || {}).reason || (value || {}).detail || (value || {}).label || "").trim();
  }).filter(Boolean))];
}

function insightText(value) {
  return String(value || "")
    .replace(/TypeDB 추론/g, "종목 관계 분석")
    .replace(/ABox 스냅샷/g, "판단 당시 자료")
    .replace(/RuleBox/g, "판단 규칙")
    .replace(/InferenceBox/g, "관계 분석")
    .replace(/추론 세대/g, "분석 시점");
}

// Presentation only: preserve the stored opinion and its limits, never derive an action.
function investmentBrief(detail) {
  detail = detail || {};
  var decision = detail.decision || {};
  var explanation = detail.explanation || {};
  var primary = explanation.primaryCause || {};
  var ai = (detail.reasoningLineage || {}).ai || {};
  var assessment = ai.insightAssessment || {};
  var useAssessment = assessment.publishable === true && ai.status === "ai-authored" && ai.publicationContractPassed === true;
  var limitations = briefTexts([].concat(
    explanation.constraints || [], explanation.counterCauses || [],
    explanation.dataGaps || [], ((detail.evidence || {}).missingDataItems || []).map(function (item) { return item.detail || item.label; }),
    useAssessment ? assessment.risks || [] : [],
    ((detail.integrity || {}).issues || []).map(function (item) { return item.detail; }),
    (detail.statusDimensions || []).filter(function (item) {
      return ["data", "integrity", "inference", "decision"].includes(item.id) && ["blocked", "error", "warning"].includes(item.state);
    }).map(function (item) { return item.reason || item.stateLabel; })
  ));
  var headline = String(detail.headline || decision.reason || "현재 자료로는 투자 의견을 확인할 수 없습니다.");
  var reasons = briefTexts([useAssessment ? assessment.causalMechanism : "", primary.summary].concat(explanation.supportingCauses || []))
    .filter(function (text) { return text !== headline; });
  var next = briefTexts([].concat(
    useAssessment && assessment.invalidationCondition ? [assessment.invalidationCondition] : [],
    explanation.changeConditions || [], decision.requiredChecks || [],
    detail.nextAction ? [detail.nextAction] : []
  ));
  return {
    headline: insightText(headline),
    reasons: reasons.slice(0, 2).map(insightText),
    limitations: limitations.map(insightText),
    next: next.slice(0, 2).map(insightText),
    sourceAt: (detail.freshness || {}).sourceAsOf || "",
    decisionAt: (detail.freshness || {}).decisionAsOf || detail.decidedAt || detail.updatedAt || "",
    limited: detail.readinessState === "blocked" || detail.readinessState === "error" || decision.state === "blocked"
  };
}

function renderInvestmentBrief(detail, key, actionLabel, formatClock) {
  var brief = investmentBrief(detail);
  var recency = opinionRecency(detail, Date.now());
  function items(values, empty) {
    return values.length ? '<ul>' + values.map(function (value) { return '<li>' + escapeHtml(value) + '</li>'; }).join("") + '</ul>' : '<p class="subtle">' + escapeHtml(empty) + '</p>';
  }
  function link(view, label) {
    return '<button type="button" class="text-button compact" data-investment-case-tab="' + view + '" data-investment-case-key="' + escapeHtml(key) + '">' + escapeHtml(label) + '</button>';
  }
  return [
    '<section class="oa-insight-brief" aria-label="투자 의견 요약">',
    '<header><div><span>현재 의견</span><strong>' + escapeHtml(actionLabel) + '</strong></div><span class="' + (brief.limited ? "caution" : "subtle") + '">' + escapeHtml(detail.readinessLabel || "자료 상태 확인 필요") + '</span></header>',
    '<p class="oa-insight-conclusion">' + escapeHtml(brief.headline) + '</p>',
    recency.state !== "current" ? '<p class="oa-data-notice caution">' + escapeHtml(recency.label) + ' · 아래 내용은 저장 당시의 의견입니다.</p>' : '',
    '<div class="oa-insight-explanation"><section><h3>왜 중요한가</h3>' + items(brief.reasons, "별도로 확인된 핵심 근거가 없습니다.") + '</section>',
    '<section class="oa-insight-caution"><h3>주의할 점</h3>' + items(brief.limitations, "별도로 기록된 반대 근거가 없습니다. 위험이 없다는 뜻은 아닙니다.") + '</section>',
    '<section class="oa-insight-next"><h3>다음에 확인할 것</h3>' + items(brief.next, "다음 확인 조건이 아직 기록되지 않았습니다.") + '</section></div>',
    '<div class="oa-insight-clock"><span>의견 기준 <time>' + escapeHtml(formatClock(brief.decisionAt) || "미기록") + '</time></span><span>자료 기준 <time>' + escapeHtml(formatClock(brief.sourceAt) || "확인 필요") + '</time></span></div>',
    '<footer>' + link("evidence", "근거·반대 확인") + link("current", "수치·출처 확인") + '</footer>',
    '</section>'
  ].join("");
}

export { briefTexts, investmentBrief, renderInvestmentBrief };
