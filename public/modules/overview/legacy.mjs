import { alertCadenceMinutes, alertRules, enabledAlertRule } from "../notifications/alerts.mjs";
import { formatMoney } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { notificationPolicyCatalog } from "../shell/catalog.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { accountsState } from "../state/accounts.mjs";
import { shellState } from "../state/shell.mjs";

function renderAdminOverviewPanel(snapshot) {
  var rules = alertRules();
  var cadences = alertCadenceMinutes();
  var policyRules = notificationPolicyCatalog();
  var enabledRules = policyRules.filter(function (rule) { return enabledAlertRule(rules, rule.key); }).length;
  var realtimeKeys = policyRules.filter(function (rule) {
    return ["투자 알림", "외부 API", "실시간"].indexOf(rule.group) >= 0;
  }).map(function (rule) { return rule.key; });
  var realtimeCadence = realtimeKeys.reduce(function (min, key) {
    var value = Number(cadences[key] || 0);
    return value > 0 ? Math.min(min, value) : min;
  }, 9999);
  var portfolio = snapshot.portfolio || {};
  var accounts = accountsState.serviceAccounts || [];
  var activeAccounts = accounts.filter(function (account) { return account.enabled !== false; }).length;
  var configuredAccounts = accounts.filter(function (account) {
    return account.clientId && account.clientSecret;
  }).length;
  var telegramAccounts = accounts.filter(function (account) {
    return account.telegramBotToken && account.telegramChatId;
  }).length;
  return [
    '<article class="panel admin-overview-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Home</p>',
    '<h2>운영 요약</h2>',
    '</div>',
    '<span class="status-pill ' + (isStaticPreviewHost() ? "demo" : "live") + '">' + (isStaticPreviewHost() ? "Pages preview" : "Local server") + '</span>',
    '</div>',
    '<div class="home-command-grid">',
    '<div class="home-command-main">',
    '<span class="home-kicker">계정 ' + escapeHtml(activeAccounts + "/" + accounts.length) + ' · 알림 ' + escapeHtml(enabledRules + "/" + policyRules.length) + '</span>',
    '<strong>' + escapeHtml(tossModeLabel(snapshot)) + '</strong>',
    '<p>계정 연결, 관심종목, 알림 템플릿, 모델 기준을 한 곳에서 운영합니다.</p>',
    '</div>',
    '<div class="home-action-grid">',
    renderHomeAction("accounts", "계정", configuredAccounts + "개 API 연결", "토스·텔레그램"),
    renderHomeAction("notifications", "알림", enabledRules + "개 활성", "템플릿 테스트"),
    renderHomeAction("modeling", "투자 판단", "전략·관계·AI", "근거 카드"),
    '</div>',
    '</div>',
    '<div class="admin-stat-grid home-stat-grid">',
    renderAdminStat("활성 계정", activeAccounts + "/" + accounts.length, ""),
    renderAdminStat("토스 API", configuredAccounts, "개"),
    renderAdminStat("텔레그램", telegramAccounts, "개"),
    renderAdminStat("최소 알림 주기", (realtimeCadence === 9999 ? "-" : realtimeCadence), realtimeCadence === 9999 ? "" : "분"),
    renderAdminStat("평가 자산", formatMoney(portfolio.total || 0), ""),
    renderAdminStat("데이터", snapshot.preview ? "Preview" : "Live", ""),
    '</div>',
    '<div class="home-signal-strip">',
    renderHomeSignal("토스", configuredAccounts ? "API 정보 저장됨" : "API 정보 필요", configuredAccounts ? "ok" : "warn"),
    renderHomeSignal("알림", telegramAccounts ? "텔레그램 연결됨" : "알림 채널 확인", telegramAccounts ? "ok" : "warn"),
    renderHomeSignal("실시간", shellState.realtime.connected ? "웹소켓 연결됨" : "HTTP 대기", shellState.realtime.connected ? "ok" : "warn"),
    renderHomeSignal("저장소", isStaticPreviewHost() ? "정적 미리보기" : "MySQL 운영 DB", isStaticPreviewHost() ? "warn" : "ok"),
    '</div>',
    '</article>'
  ].join("");
}

function tossModeLabel(snapshot) {
  var toss = snapshot.toss || {};
  if (snapshot.preview) return "정적 미리보기 모드";
  if (toss.mode === "live") return "토스 실데이터 연결됨";
  return "로컬 서버 대기";
}

function renderHomeAction(tab, label, value, caption) {
  return [
    '<button class="home-action" data-tab="' + escapeHtml(tab) + '"' + cardTypeAttrs("action-queue-card") + '>',
    '<span>' + escapeHtml(label) + '</span>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '<em>' + escapeHtml(caption) + '</em>',
    '</button>'
  ].join("");
}

function renderHomeSignal(label, value, tone) {
  return [
    '<span class="home-signal ' + escapeHtml(tone || "ok") + '"' + cardTypeAttrs("health-card", tone || "ok") + '>',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '</span>'
  ].join("");
}

function renderAdminStat(label, value, suffix) {
  return [
    '<span' + cardTypeAttrs("metric-cell") + '>',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value) + escapeHtml(suffix || "") + '</strong>',
    '</span>'
  ].join("");
}
