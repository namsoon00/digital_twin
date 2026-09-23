import { loadServiceAccounts } from "../accounts/commands.mjs";
import { loadInvestmentCalendar } from "../calendar/commands.mjs";
import { applySymbolUniverseRefreshStatus, loadSymbolUniverse } from "../instruments/universe.mjs";
import { loadNotificationJobs, loadNotificationRules, loadNotificationSchedules, loadNotificationTemplates } from "../notifications/requests.mjs";
import { loadStrategyProposals } from "../proposals/requests.mjs";
import { renderQueuedDuringSuppressionCell, renderSuppressionDepthCell } from "../render/runtime.mjs";
import { render } from "../render/scheduler.mjs";
import { invalidateJsonResponseCache } from "../requests/json.mjs";
import { loadServerSettings } from "../settings/requests.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { load } from "../snapshot/requests.mjs";
import { shellState } from "../state/shell.mjs";

var realtimeSocket = null;

var realtimeReconnectTimer = null;

var realtimeReloadTimer = null;

var realtimeSeenEventIds = {};

function realtimeWebSocketUrl() {
  var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  var host = window.location.host || window.location.hostname || "127.0.0.1:3000";
  return protocol + "//" + host + "/ws";
}

function markRealtimeState(connected, eventName) {
  shellState.realtime.connected = Boolean(connected);
  shellState.realtime.lastEvent = eventName || shellState.realtime.lastEvent || "";
  shellState.realtime.lastEventAt = new Date().toISOString();
}

function runWithSuppressedRender(taskFactory) {
  renderSuppressionDepthCell.value += 1;
  var task;
  try {
    task = Promise.resolve(taskFactory());
  } finally {
    // Batch synchronous invalidations only; network latency cannot lock navigation.
    renderSuppressionDepthCell.value = Math.max(0, renderSuppressionDepthCell.value - 1);
    if (!renderSuppressionDepthCell.value && (renderQueuedDuringSuppressionCell.value || shellState.snapshot)) {
      renderQueuedDuringSuppressionCell.value = false;
      render();
    }
  }
  return task;
}

function queueRealtimeReload(eventType) {
  if (isStaticPreviewHost()) return;
  if (realtimeReloadTimer) clearTimeout(realtimeReloadTimer);
  realtimeReloadTimer = setTimeout(function () {
    runWithSuppressedRender(function () {
      var tasks = [];
      if (/^settings\./.test(eventType)) {
        tasks.push(loadServerSettings());
        tasks.push(loadNotificationSchedules());
      } else if (/^account\./.test(eventType)) {
        tasks.push(loadServiceAccounts());
        tasks.push(load());
      } else if (/^notification_template\.|^notification_rule\.|^notification\./.test(eventType)) {
        tasks.push(loadNotificationTemplates());
        tasks.push(loadNotificationRules());
        tasks.push(loadNotificationJobs());
        tasks.push(loadNotificationSchedules());
      } else if (/^investment_calendar\./.test(eventType)) {
        tasks.push(loadInvestmentCalendar(true));
        tasks.push(loadNotificationJobs());
        tasks.push(loadNotificationSchedules());
      } else if (/^investment_strategy\./.test(eventType)) {
        tasks.push(loadStrategyProposals(true));
      } else if (/^symbol_universe\.(refreshed|refresh_failed)$/.test(eventType)) {
        tasks.push(loadSymbolUniverse());
      } else if (/^(dashboard\.snapshot_ready|monitoring\.snapshot_collected|app\.|chat\.)/.test(eventType)) {
        tasks.push(load());
      }
      if (!tasks.length) return Promise.resolve();
      return Promise.all(tasks.map(function (task) {
        return task.catch(function () { return null; });
      }));
    });
  }, 900);
}

function normalizeRealtimeEvent(event) {
  if (!event || typeof event !== "object") return null;
  var payload = event.payload && typeof event.payload === "object" ? event.payload : {};
  return {
    name: event.name || event.type || "",
    eventId: event.eventId || event.event_id || "",
    aggregateId: event.aggregateId || event.aggregate_id || "",
    occurredAt: event.occurredAt || event.occurred_at || "",
    payload: payload
  };
}

function notificationJobSummaryText(jobs) {
  jobs = jobs || {};
  var pending = Number(jobs.pending || 0);
  var awaitingAi = Number(jobs.awaiting_ai || 0);
  var processing = Number(jobs.processing || 0);
  var failed = Number(jobs.failed || 0);
  var superseded = Number(jobs.superseded || 0);
  var suppressed = Number(jobs.suppressed || 0);
  if (pending || awaitingAi || processing || failed || superseded || suppressed) {
    return "발송 대기 " + pending + " · AI 판단 " + awaitingAi + " · 처리 " + processing + " · 실패 " + failed + " · 대체 " + superseded + " · 제외 " + suppressed;
  }
  if (Number(jobs.done || 0)) return "완료 " + Number(jobs.done || 0);
  return "-";
}

function realtimeEventSnackbar(event) {
  var payload = event.payload || {};
  if (event.name === "notification.job_queued") {
    return { message: "알림 작업이 큐에 적재됐습니다: " + (payload.messageType || "notification"), tone: "success" };
  }
  if (event.name === "notification.test_requested") {
    return { message: "테스트 알림 요청을 접수했습니다.", tone: "success" };
  }
  if (event.name === "notification_template.updated") {
    return { message: "알림 템플릿이 갱신됐습니다: " + (payload.messageType || event.aggregateId || "-"), tone: "success" };
  }
  if (event.name === "notification_rule.updated") {
    return { message: "알림 발송 룰이 갱신됐습니다: " + (payload.messageType || event.aggregateId || "-"), tone: "success" };
  }
  if (event.name === "investment_strategy.proposed") {
    return { message: "새 투자 전략 제안이 등록됐습니다.", tone: "success" };
  }
  if (event.name === "investment_strategy.validated") {
    return { message: "투자 전략 제안 검증이 완료됐습니다.", tone: "success" };
  }
  if (event.name === "investment_strategy.approved") {
    return { message: "투자 전략 제안이 승인됐습니다.", tone: "success" };
  }
  if (event.name === "investment_strategy.deployed") {
    return { message: "투자 전략 제안이 운영 온톨로지에 반영됐습니다.", tone: "success" };
  }
  if (event.name === "investment_strategy.performance_recorded") {
    return { message: "투자 전략 성과 표본을 기록했습니다.", tone: "success" };
  }
  if (event.name === "symbol_universe.refresh_requested") {
    return { message: "전체 종목 목록 갱신을 시작했습니다.", tone: "success" };
  }
  if (event.name === "symbol_universe.refreshed") {
    return {
      message: payload.status === "partial" ? "전체 종목 목록을 일부 갱신했습니다." : "전체 종목 목록 갱신을 완료했습니다.",
      tone: payload.status === "partial" ? "caution" : "success",
      action: { label: "결과 보기", name: "open-symbol-universe-refresh" }
    };
  }
  if (event.name === "symbol_universe.refresh_failed") {
    return {
      message: "전체 종목 목록 갱신에 실패했습니다. 마지막 성공 목록을 유지합니다.",
      tone: "danger",
      action: { label: "확인", name: "open-symbol-universe-refresh" }
    };
  }
  return null;
}

function recordRealtimeEvent(event, silent) {
  var normalized = normalizeRealtimeEvent(event);
  if (!normalized || !normalized.name) return;
  shellState.realtime.lastEvent = normalized.name;
  shellState.realtime.lastEventAt = normalized.occurredAt || new Date().toISOString();
  if (normalized.eventId) {
    if (realtimeSeenEventIds[normalized.eventId]) return;
    realtimeSeenEventIds[normalized.eventId] = true;
  }
  if (!silent) {
    var snackbar = realtimeEventSnackbar(normalized);
    if (snackbar) showSnackbar(snackbar.message, snackbar.tone, snackbar.action);
  }
}

function applyRealtimeStatus(payload, silent) {
  payload = payload || {};
  shellState.realtime.eventCounts = payload.events || shellState.realtime.eventCounts || {};
  shellState.realtime.latestEvents = Array.isArray(payload.latestEvents) ? payload.latestEvents : shellState.realtime.latestEvents || [];
  shellState.realtime.monitoring = payload.monitoring || shellState.realtime.monitoring || {};
  shellState.realtime.notificationJobs = payload.notificationJobs || shellState.realtime.notificationJobs || {};
  shellState.realtime.latestEvents.forEach(function (event) {
    recordRealtimeEvent(event, silent);
  });
}

function handleRealtimeMessage(message) {
  var eventType = message.type || (message.payload && message.payload.event && message.payload.event.name) || "";
  if (!eventType || eventType === "realtime.heartbeat") return;
  if (eventType === "realtime.connected" || eventType === "realtime.status" || eventType === "realtime.pong") {
    applyRealtimeStatus(message.payload || {}, eventType === "realtime.connected");
    markRealtimeState(true, eventType);
    render();
    return;
  }
  if (/^symbol_universe\.(refresh_requested|refreshed|refresh_failed)$/.test(eventType)) {
    var refreshEventApplied = applySymbolUniverseRefreshStatus(
      message.payload || {},
      eventType === "symbol_universe.refresh_requested" ? "websocket-request" : "websocket"
    );
    if (refreshEventApplied === false) return;
  }
  recordRealtimeEvent((message.payload && message.payload.event) || {
    name: eventType,
    occurredAt: message.occurredAt,
    payload: message.payload || {}
  }, false);
  invalidateJsonResponseCache();
  markRealtimeState(true, eventType);
  render();
  if (eventType !== "realtime.connected") queueRealtimeReload(eventType);
}

function connectRealtime() {
  if (isStaticPreviewHost() || !shellState.realtime.supported || realtimeSocket) return;
  try {
    realtimeSocket = new window.WebSocket(realtimeWebSocketUrl());
  } catch (error) {
    shellState.realtime.supported = false;
    return;
  }
  realtimeSocket.addEventListener("open", function () {
    shellState.realtime.reconnects = 0;
    markRealtimeState(true, "realtime.connected");
  });
  realtimeSocket.addEventListener("message", function (event) {
    try {
      handleRealtimeMessage(JSON.parse(event.data || "{}"));
    } catch (error) {
      handleRealtimeMessage({ type: "realtime.message" });
    }
  });
  realtimeSocket.addEventListener("close", function () {
    realtimeSocket = null;
    markRealtimeState(false, "realtime.disconnected");
    if (realtimeReconnectTimer) clearTimeout(realtimeReconnectTimer);
    var delay = Math.min(15000, 1000 + shellState.realtime.reconnects * 1500);
    shellState.realtime.reconnects += 1;
    realtimeReconnectTimer = setTimeout(connectRealtime, delay);
  });
  realtimeSocket.addEventListener("error", function () {
    markRealtimeState(false, "realtime.error");
  });
}

export { connectRealtime, normalizeRealtimeEvent, notificationJobSummaryText };
