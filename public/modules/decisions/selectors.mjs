import { investmentActionKey } from "./actions.mjs";
import { decisionStateMeta, stateValueFromSources } from "./signals.mjs";
import { investmentActionInvalidation, investmentActionUserPresentation, investmentAnalysisModel } from "./strategy.mjs";
import { formatConsoleNarrative, stockDisplayName } from "../instruments/catalog.mjs";
import { consoleQualityMeta } from "../shared/console.mjs";
import { numeric, recordChangedAt, recordChangedAtValue } from "../shared/format.mjs";
import { decisionsState } from "../state/decisions.mjs";

function selectConsoleDecisionRows(snapshot) {
  var analysis = investmentAnalysisModel(snapshot || {});
  var rows = Array.isArray(analysis.actionQueue) ? analysis.actionQueue : [];
  var cases = Array.isArray((decisionsState.investmentFlow || {}).items) ? decisionsState.investmentFlow.items : [];
  if (cases.length) {
    return cases.map(function (item) {
      var decision = item.decision || {};
      var itemSymbol = String(item.symbol || "").toUpperCase();
      var itemName = String(item.name || "").trim();
      var matched = rows.filter(function (row) {
        return String(row.symbol || "").toUpperCase() === itemSymbol;
      })[0] || {};
      var action = decisionActionMeta(decision.state === "blocked" ? "BLOCKED" : decision.action, decision.action);
      var dataState = String(decision.dataState || (item.facts || {}).dataState || "partial");
      var readinessState = String(item.readinessState || "warning");
      var attention = item.attention && typeof item.attention === "object" ? item.attention : {};
      return {
        key: String(item.caseId || item.episodeId || item.symbol),
        caseId: String(item.caseId || ""),
        detailType: String(item.detailType || "investment-case"),
        subjectCaseId: String(item.subjectCaseId || ""),
        subjectDecisionCase: item.subjectDecisionCase && typeof item.subjectDecisionCase === "object" ? item.subjectDecisionCase : {},
        decisionKey: String(matched.decisionKey || ""),
        decisionEpisodeId: String(item.episodeId || ""),
        accountId: String(item.accountId || matched.accountId || "default"),
        accountLabel: String(matched.accountLabel || "기본 계정"),
        symbol: itemSymbol,
        name: itemName && itemName.toUpperCase() !== itemSymbol ? itemName : stockDisplayName(itemSymbol, matched),
        decision: action.label,
        actionCode: action.code,
        actionLabel: action.label,
        tone: readinessState === "blocked" || readinessState === "error" ? "danger" : action.tone,
        reason: formatConsoleNarrative(item.headline || "판단 근거를 확인하세요."),
        invalidation: formatConsoleNarrative(item.nextAction || "무효화 조건과 다음 확인을 살펴보세요."),
        quality: consoleQualityMeta(dataState === "sufficient" ? "actual" : dataState),
        apiSource: item.detailType === "subject-decision-case" ? "SubjectDecisionCase" : "DecisionEpisode",
        isMock: false,
        source: String(matched.portfolioRole || matched.source || "holding"),
        blocked: readinessState === "blocked" || readinessState === "error" || item.caseStatus === "blocked",
        userActionable: Boolean(attention.userActionable),
        userReviewable: Boolean(attention.userReviewable),
        userAttentionRequired: Boolean(attention.userAttentionRequired || attention.userActionable || attention.userReviewable),
        attentionState: String(attention.state || (readinessState === "blocked" ? "blocked" : "review")),
        attentionLabel: String(attention.label || item.readinessLabel || "확인 필요"),
        attentionIssues: Array.isArray(attention.issues) ? attention.issues : [],
        attention: attention,
        reviewLevel: String(decision.reviewLevel || "observe"),
        dataState: dataState,
        changeState: stateValueFromSources([matched, matched.graph], ["changeState", "change_state"], "unchanged"),
        conflictState: stateValueFromSources([matched, matched.graph], ["conflictState", "conflict_state"], "context-only"),
        validationState: String(decision.assuranceState || decision.validationState || "conditional"),
        readinessState: readinessState,
        readinessLabel: String(item.readinessLabel || "확인 필요"),
        phase: String(item.phase || "case"),
        phaseLabel: String(item.phaseLabel || "투자 케이스"),
        nextAction: String(item.nextAction || ""),
        statusDimensions: Array.isArray(item.statusDimensions) ? item.statusDimensions : [],
        explanation: item.explanation && typeof item.explanation === "object" ? item.explanation : {},
        outcome: item.outcome || {},
        updatedAt: item.updatedAt || item.decidedAt || "",
        raw: item
      };
    }).sort(function (a, b) {
    var changedDiff = recordChangedAtValue(b) - recordChangedAtValue(a);
      var priority = { action: 0, blocked: 1, review: 2, system: 3, observe: 4 };
      var priorityDiff = (priority[a.attentionState] == null ? 9 : priority[a.attentionState]) - (priority[b.attentionState] == null ? 9 : priority[b.attentionState]);
      return priorityDiff || changedDiff || String(a.symbol || "").localeCompare(String(b.symbol || ""));
    });
  }
  return rows.map(function (row, index) {
    var graph = row.graph || {};
    var reasons = Array.isArray(row.reasons) ? row.reasons : [];
    var sources = [row, graph, row.ontologyRelationContext, row.stateContract];
    var reviewLevel = stateValueFromSources(sources, ["reviewLevel", "review_level"], graph.blocked ? "blocked" : "observe");
    var dataState = stateValueFromSources(sources, ["dataState", "data_state"], "partial");
    var changeState = stateValueFromSources(sources, ["changeState", "change_state"], "unchanged");
    var conflictState = stateValueFromSources(sources, ["conflictState", "conflict_state"], "context-only");
    var validationState = stateValueFromSources(sources, ["validationState", "validation_state"], dataState === "sufficient" ? "conditional" : "blocked");
    var review = decisionStateMeta("review", reviewLevel, "observe");
    var action = decisionActionMeta(row.actionCode, row.decision || row.action);
    var presentation = investmentActionUserPresentation(row);
    return {
      key: row.decisionKey || investmentActionKey(row, index),
      decisionKey: String(row.decisionKey || ""),
      decisionEpisodeId: String(row.decisionEpisodeId || ""),
      accountId: String(row.accountId || ((analysis.accountFocus || {}).accountId) || "default"),
      accountLabel: String(row.accountLabel || ((analysis.accountFocus || {}).label) || "기본 계정"),
      symbol: String(row.symbol || "").toUpperCase(),
      name: stockDisplayName(row.symbol, row),
      decision: row.decision || row.action || "검토",
      actionCode: presentation.actionCode || action.code,
      actionLabel: presentation.actionLabel || action.label,
      tone: presentation.tone || row.tone || action.tone || review.tone,
      reason: presentation.explanation || formatConsoleNarrative(reasons[0] || graph.reason || "근거 확인 필요"),
      invalidation: formatConsoleNarrative(investmentActionInvalidation(row)),
      quality: consoleQualityMeta(row.dataQuality || row.quality),
      apiSource: String(row.apiSource || row.source || "investment_analysis"),
      isMock: Boolean(row.isMock) || ["mock", "demo"].indexOf(String(row.dataQuality || row.quality || "").toLowerCase()) >= 0,
      source: String(row.portfolioRole || row.source || "holding"),
      blocked: Boolean(graph.blocked) || reviewLevel === "blocked" || validationState === "blocked",
      userActionable: !Boolean(graph.blocked) && ["BUY", "ADD", "SELL", "TRIM", "AVOID"].indexOf(presentation.actionCode || action.code) >= 0,
      userReviewable: !Boolean(graph.blocked) && ["BUY", "ADD", "SELL", "TRIM", "AVOID"].indexOf(presentation.actionCode || action.code) < 0,
      userAttentionRequired: !Boolean(graph.blocked),
      attentionState: Boolean(graph.blocked) || reviewLevel === "blocked" || validationState === "blocked" ? "blocked" : (["BUY", "ADD", "SELL", "TRIM", "AVOID"].indexOf(presentation.actionCode || action.code) >= 0 ? "action" : "review"),
      attentionLabel: Boolean(graph.blocked) || reviewLevel === "blocked" || validationState === "blocked" ? "판단 보류" : "근거 확인",
      attentionIssues: [],
      reviewLevel: reviewLevel,
      dataState: dataState,
      changeState: changeState,
      conflictState: conflictState,
      validationState: validationState,
      statusDimensions: [],
      explanation: {},
      profitLossRate: numeric(row.profitLossRate),
      updatedAt: recordChangedAt(row, recordChangedAt(graph)),
      raw: row
    };
  }).sort(function (a, b) {
    var changedDiff = recordChangedAtValue(b) - recordChangedAtValue(a);
    if (changedDiff) return changedDiff;
    return String(a.symbol || "").localeCompare(String(b.symbol || ""));
  });
}

function decisionActionMeta(value, fallback) {
  var code = String(value || "").toUpperCase();
  var text = String(fallback || "");
  if (!code) {
    if (/추가/.test(text)) code = "ADD";
    else if (/매수|진입/.test(text)) code = "BUY";
    else if (/축소/.test(text)) code = "TRIM";
    else if (/매도|정리/.test(text)) code = "SELL";
    else if (/보류|차단/.test(text)) code = "BLOCKED";
    else if (/유지/.test(text)) code = "HOLD";
    else code = "OBSERVE";
  }
  var values = {
    BUY: { label: "매수 검토", tone: "watch" },
    ADD: { label: "추가매수 검토", tone: "watch" },
    HOLD: { label: "유지", tone: "hold" },
    TRIM: { label: "축소 검토", tone: "caution" },
    SELL: { label: "매도 검토", tone: "danger" },
    AVOID: { label: "진입 회피", tone: "danger" },
    BLOCKED: { label: "판단 보류", tone: "caution" },
    OBSERVE: { label: "관찰", tone: "hold" }
  };
  return Object.assign({ code: code }, values[code] || values.OBSERVE);
}

function filteredConsoleDecisionRows(snapshot) {
  var query = String(decisionsState.consoleDecisionSearch || "").trim().toLowerCase();
  var scope = String(decisionsState.consoleDecisionScope || "all");
  var action = String(decisionsState.consoleDecisionAction || "all");
  var quality = String(decisionsState.consoleDecisionQuality || "all");
  var status = String(decisionsState.consoleDecisionStatus || "all");
  return selectConsoleDecisionRows(snapshot).filter(function (row) {
    var view = String(decisionsState.consoleDecisionView || "attention");
    var actionRequired = Boolean(row.userActionable);
    var reviewRequired = Boolean(row.userReviewable) || row.attentionState === "review";
    var changedAt = recordChangedAtValue(row);
    var recentlyChanged = row.changeState !== "unchanged" || (changedAt && Date.now() - changedAt <= 7 * 24 * 60 * 60 * 1000);
    if (view === "attention" && !actionRequired && !reviewRequired) return false;
    if (view === "action" && !actionRequired) return false;
    if (view === "review" && !reviewRequired) return false;
    if (view === "recent" && !recentlyChanged) return false;
    var isWatch = row.source === "watchlist";
    if (scope === "holding" && isWatch) return false;
    if (scope === "watchlist" && !isWatch) return false;
    if (action === "BUY_REVIEW" && row.actionCode !== "BUY" && row.actionCode !== "ADD") return false;
    if (action === "SELL_REVIEW" && row.actionCode !== "SELL" && row.actionCode !== "TRIM") return false;
    if (action !== "all" && action !== "BUY_REVIEW" && action !== "SELL_REVIEW" && row.actionCode !== action) return false;
    if (quality === "actual" && (row.isMock || row.quality.label !== "실데이터")) return false;
    if (quality === "mock" && !row.isMock) return false;
    if (quality === "issue" && row.quality.tone !== "danger" && row.quality.tone !== "caution") return false;
    if (status !== "all" && row.attentionState !== status) return false;
    if (!query) return true;
    return [row.name, row.symbol, row.accountLabel, row.actionLabel, row.decision, row.reason, row.apiSource].join(" ").toLowerCase().indexOf(query) >= 0;
  });
}

export { decisionActionMeta, filteredConsoleDecisionRows, selectConsoleDecisionRows };
