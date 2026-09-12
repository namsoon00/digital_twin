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
  var useAssessment = assessment.publishable === true && ai.status === "ai-authored" && ai.publicationContractPassed === true && ai.aiAuthored === true && ai.currentGeneration === true;
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

function investmentReading(detail, actionLabel) {
  var saved = (detail || {}).reading;
  if (saved && saved.version === "investment-reading-v1") return saved;
  var brief = investmentBrief(detail);
  var hasOpinion = ["BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID"].includes(String(((detail || {}).decision || {}).action || "")) && !brief.limited;
  return {
    kind: hasOpinion ? "opinion" : "awaiting", status: hasOpinion ? actionLabel : "투자 의견 미확정",
    headline: hasOpinion ? brief.headline : "현재 자료의 분석이 완료되지 않아 투자 의견이 확정되지 않았습니다.",
    meaning: "", meaningEmpty: "투자에 미칠 영향이 별도로 설명되지 않았습니다.",
    changes: [], changeEmpty: "이전 분석과 비교해 확인된 변화가 기록되지 않았습니다.",
    reasons: brief.reasons, reasonLabel: "저장된 설명", counters: [], limits: brief.limitations, gaps: [],
    nextChecks: brief.next, facts: [], sourceAt: brief.sourceAt, opinionAt: brief.decisionAt
  };
}

function renderInvestmentBrief(detail, key, actionLabel, formatClock) {
  var reading = investmentReading(detail, actionLabel);
  var recency = opinionRecency(detail, Date.now());
  function items(values, empty, limit) {
    values = Array.isArray(values) ? values : [];
    return values.length ? '<ul>' + values.slice(0, limit || values.length).map(function (value) { return '<li>' + escapeHtml(value) + '</li>'; }).join("") + '</ul>' : (empty ? '<p class="oa-reading-missing">' + escapeHtml(empty) + '</p>' : '');
  }
  function link(view, label) {
    return '<button type="button" class="oa-reading-link" data-investment-case-tab="' + view + '" data-investment-case-key="' + escapeHtml(key) + '">' + escapeHtml(label) + ' <span aria-hidden="true">&rarr;</span></button>';
  }
  var facts = Array.isArray(reading.facts) ? reading.facts : [];
  var gaps = Array.isArray(reading.gaps) ? reading.gaps : [];
  var limits = Array.isArray(reading.limits) ? reading.limits : [];
  var counters = Array.isArray(reading.counters) ? reading.counters : [];
  var factMarkup = facts.length ? '<dl class="oa-reading-facts">' + facts.slice(0, 3).map(function (fact) {
    var value = Number(fact.value);
    var text = Number.isFinite(value) ? value.toLocaleString("ko-KR", {maximumFractionDigits: 2}) + String(fact.unit || "") : "자료 없음";
    return '<div><dt>' + escapeHtml(fact.label) + '</dt><dd><strong>' + escapeHtml(text) + '</strong><small>' + escapeHtml(formatClock(fact.asOf) || "기준 시각 미기록") + '</small></dd></div>';
  }).join("") + '</dl>' : '';
  return [
    '<section class="oa-insight-brief oa-investment-reading" data-reading-kind="' + escapeHtml(reading.kind) + '" aria-label="투자 해석">',
    '<header><div><span>' + escapeHtml(reading.kind === "opinion" ? "저장된 투자 의견" : "분석 결과") + '</span><strong>' + escapeHtml(reading.status) + '</strong></div></header>',
    '<p class="oa-insight-conclusion">' + escapeHtml(reading.headline) + '</p>',
    recency.state !== "current" ? '<p class="oa-data-notice caution">' + escapeHtml(recency.label) + ' · 아래 내용은 저장 당시의 의견입니다.</p>' : '',
    '<div class="oa-reading-questions">',
    '<section data-reading-question="change"><h3>무엇을 확인했나?</h3>' + items(reading.changes, reading.changeEmpty) + factMarkup + link("current", "확인된 수치와 출처") + '</section>',
    '<section data-reading-question="meaning"><h3>내 투자에 어떤 의미인가?</h3>' + items(reading.meaning ? [reading.meaning] : [], reading.meaningEmpty) + link("reasoning", "검토한 설명과 판단 기준") + '</section>',
    '<section data-reading-question="evidence"><h3>왜 그렇게 보나?</h3><span class="oa-reading-role">' + escapeHtml(reading.reasonLabel) + '</span>' + items(reading.reasons, "판단을 뒷받침하는 설명이 아직 기록되지 않았습니다.", 2),
    counters.length ? '<div class="oa-reading-counter"><strong>다르게 볼 근거</strong>' + items(counters, "", 2) + '</div>' : '<p class="oa-reading-missing">기록된 반대 근거가 없습니다. 위험이 없다는 뜻은 아닙니다.</p>',
    limits.length || gaps.length ? '<div class="oa-insight-caution"><strong>해석의 한계' + (gaps.length ? ' · 부족한 자료 ' + gaps.length + '건' : '') + '</strong>' + items(limits, "", 2) + items(gaps, "", 2) + '</div>' : '',
    link("evidence", "근거·반대·자료 한계 전체") + '</section>',
    '<section data-reading-question="next"><h3>무엇을 더 확인해야 하나?</h3>' + items(reading.nextChecks, "다음에 확인할 자료나 조건이 구체적으로 기록되지 않았습니다.", 3) + link("evidence", "확인 조건 전체") + '</section>',
    '</div>',
    '<div class="oa-insight-clock"><span>의견 기준 <time>' + escapeHtml(formatClock(reading.opinionAt) || "미기록") + '</time></span><span>자료 기준 <time>' + escapeHtml(formatClock(reading.sourceAt) || "확인 필요") + '</time></span></div>',
    '</section>'
  ].join("");
}

export { briefTexts, investmentBrief, investmentReading, renderInvestmentBrief };
