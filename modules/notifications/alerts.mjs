import { buildTradeSignalItems, categoricalModelState, decisionStateMeta } from "../decisions/signals.mjs";
import { defaultSettings } from "../settings/defaults.mjs";
import { settingValue } from "../settings/fields.mjs";
import { parseNumberAssignments } from "../settings/formulas.mjs";
import { alertRuleCatalog } from "../shell/catalog.mjs";

function alertRules() {
  return parseNumberAssignments(settingValue("alertRules"), parseNumberAssignments(defaultSettings.alertRules));
}

function alertThresholds() {
  return parseNumberAssignments(settingValue("alertThresholds"), parseNumberAssignments(defaultSettings.alertThresholds));
}

function relationRuleThresholds() {
  return parseNumberAssignments(settingValue("relationRuleThresholds"), parseNumberAssignments(defaultSettings.relationRuleThresholds));
}

function alertCadenceMinutes() {
  return parseNumberAssignments(settingValue("alertCadenceMinutes"), parseNumberAssignments(defaultSettings.alertCadenceMinutes));
}

function enabledAlertRule(rules, key) {
  return Object.prototype.hasOwnProperty.call(rules || {}, key) && Number((rules || {})[key]) !== 0;
}

function alertSeverityRank(severity) {
  var ranks = { danger: 4, caution: 3, watch: 2, info: 1 };
  return ranks[severity] || 0;
}

function alertSeverityLabel(severity) {
  var labels = { danger: "긴급", caution: "주의", watch: "관찰", info: "정보" };
  return labels[severity] || "정보";
}

function alertRuleLabel(key) {
  var rule = alertRuleCatalog.filter(function (item) { return item.key === key; })[0];
  return rule ? rule.label : key;
}

function addAlert(alerts, rules, alert) {
  if (!alert || !alert.rule || !enabledAlertRule(rules, alert.rule)) return;
  alerts.push(Object.assign({
    id: [alert.rule, alert.symbol || "account", alert.title || ""].join(":"),
    severity: "info",
    value: "",
    threshold: "",
    source: alertRuleLabel(alert.rule)
  }, alert));
}

function addModelAlerts(alerts, rules, thresholds, item) {
  void thresholds;
  var model = categoricalModelState(item);
  var review = decisionStateMeta("review", model.reviewLevel, "observe");
  var validation = decisionStateMeta("validation", model.validationState, "conditional");
  if (item.source === "watchlist" && model.conflictState === "support-only" && ["check", "act", "immediate"].indexOf(model.reviewLevel) >= 0) {
    addAlert(alerts, rules, {
      rule: "modelBuy",
      severity: "watch",
      symbol: item.symbol,
      title: item.name + " 진입 조건 확인",
      message: model.action,
      value: review.label,
      threshold: validation.label,
      source: "상태 판단"
    });
  }
  if (item.source !== "watchlist" && ["act", "immediate"].indexOf(model.reviewLevel) >= 0) {
    addAlert(alerts, rules, {
      rule: "modelSell",
      severity: model.reviewLevel === "immediate" ? "danger" : "caution",
      symbol: item.symbol,
      title: item.name + " 손실·비중 관리 조건 확인",
      message: model.action,
      value: review.label,
      threshold: validation.label,
      source: "상태 판단"
    });
  }
  if (["direction-changed", "worsening", "improving", "new-evidence"].indexOf(model.changeState) >= 0) {
    addAlert(alerts, rules, {
      rule: "monitorDecisionChange",
      severity: model.changeState === "worsening" ? "caution" : "watch",
      symbol: item.symbol,
      title: item.name + " 판단 상태 변경",
      message: decisionStateMeta("change", model.changeState, "unchanged").label,
      value: review.label,
      threshold: decisionStateMeta("conflict", model.conflictState, "context-only").label,
      source: "상태 변화"
    });
  }
}

function snapshotStamp(snapshot) {
  var toss = snapshot.toss || {};
  var raw = snapshot.generatedAt || snapshot.updatedAt || snapshot.asOf || toss.generatedAt || toss.updatedAt || toss.fetchedAt || "";
  var stamp = Date.parse(raw);
  return Number.isFinite(stamp) ? { raw: raw, stamp: stamp } : null;
}

function addDataAlerts(alerts, rules, thresholds, snapshot) {
  var toss = snapshot.toss || {};
  if (toss.mode !== "live") {
    addAlert(alerts, rules, {
      rule: "tossConnection",
      severity: "caution",
      title: "토스 live 연결 확인",
      message: toss.status || "토스 live 연결 상태를 확인해야 합니다.",
      value: toss.mode || "unknown",
      threshold: "live",
      source: "데이터"
    });
  }
  if (toss.mode === "live" && Array.isArray(toss.positions) && toss.positions.length === 0) {
    addAlert(alerts, rules, {
      rule: "tossConnection",
      severity: "caution",
      title: "보유 종목 없음",
      message: "토스 연결은 성공했지만 보유 종목 배열이 비어 있습니다.",
      value: "0개",
      threshold: "1개 이상",
      source: "데이터"
    });
  }
  var stamp = snapshotStamp(snapshot);
  if (stamp) {
    var minutes = (Date.now() - stamp.stamp) / 60000;
    if (minutes >= Number(thresholds.staleMinutes || 0)) {
      addAlert(alerts, rules, {
        rule: "dataFreshness",
        severity: minutes >= Number(thresholds.staleMinutes || 0) * 2 ? "caution" : "info",
        title: "데이터 갱신 지연",
        message: "마지막 데이터 생성 시각이 설정값보다 오래되었습니다.",
        value: Math.round(minutes) + "분",
        threshold: Math.round(thresholds.staleMinutes || 0) + "분",
        source: "데이터"
      });
    }
  }
}

function orderCandidates(snapshot) {
  var toss = snapshot.toss || {};
  return []
    .concat(Array.isArray(snapshot.orders) ? snapshot.orders : [])
    .concat(Array.isArray(toss.orders) ? toss.orders : [])
    .concat(Array.isArray(toss.orderStatus) ? toss.orderStatus : []);
}

function addOrderAlerts(alerts, rules, thresholds, snapshot) {
  orderCandidates(snapshot).forEach(function (order) {
    var status = String(order.status || order.orderStatus || order.state || "").toLowerCase();
    var symbol = String(order.symbol || order.ticker || order.stockCode || "").toUpperCase();
    var name = order.name || order.stockName || symbol || "주문";
    var createdAt = Date.parse(order.createdAt || order.orderTime || order.orderedAt || "");
    var ageMinutes = Number.isFinite(createdAt) ? (Date.now() - createdAt) / 60000 : 0;
    if (/pending|open|wait|partial|미체결|접수|부분/.test(status) && ageMinutes >= Number(thresholds.pendingOrderMinutes || 0)) {
      addAlert(alerts, rules, {
        rule: "orderPending",
        severity: "caution",
        symbol: symbol,
        title: name + " 미체결 주문",
        message: "미체결 주문이 설정 시간보다 오래 남아 있습니다.",
        value: Math.round(ageMinutes) + "분",
        threshold: Math.round(thresholds.pendingOrderMinutes || 0) + "분",
        source: "주문"
      });
    }
    if (/reject|fail|error|거부|실패/.test(status)) {
      addAlert(alerts, rules, {
        rule: "orderReject",
        severity: "danger",
        symbol: symbol,
        title: name + " 주문 실패",
        message: "주문 상태가 거부 또는 실패로 표시되었습니다.",
        value: order.status || order.orderStatus || "-",
        threshold: "정상",
        source: "주문"
      });
    }
  });
}

function buildAlertItems(snapshot) {
  if (!snapshot) return [];
  var rules = alertRules();
  var thresholds = alertThresholds();
  var alerts = [];
  var items = buildTradeSignalItems(snapshot);
  items.forEach(function (item) {
    addModelAlerts(alerts, rules, thresholds, item);
  });
  addDataAlerts(alerts, rules, thresholds, snapshot);
  addOrderAlerts(alerts, rules, thresholds, snapshot);
  return alerts.sort(function (a, b) {
    var severityDiff = alertSeverityRank(b.severity) - alertSeverityRank(a.severity);
    if (severityDiff) return severityDiff;
    return String(a.title || "").localeCompare(String(b.title || ""), "ko");
  });
}

function alertStats(alerts) {
  return alerts.reduce(function (stats, alert) {
    stats.total += 1;
    stats[alert.severity] = (stats[alert.severity] || 0) + 1;
    return stats;
  }, { total: 0, danger: 0, caution: 0, watch: 0, info: 0 });
}

export { alertCadenceMinutes, alertRuleLabel, alertRules, alertSeverityLabel, alertStats, alertThresholds, buildAlertItems, enabledAlertRule, relationRuleThresholds };
