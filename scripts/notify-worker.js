const fs = require("fs");
const https = require("https");
const path = require("path");
const { URLSearchParams } = require("url");

const rootDir = path.resolve(__dirname, "..");
process.chdir(rootDir);

const { flowLensSnapshot, runtimeSettings } = require("../server");

const dataDir = path.join(rootDir, "data");
const notificationStatePath = path.join(dataDir, "notification-state.json");
const kakaoTokenPath = path.join(dataDir, "kakao-token.json");

const severityRank = { INFO: 1, WATCH: 2, ALERT: 3 };
const defaultAlertRules = {
  tossConnection: 1,
  holdingTiming: 1,
  sectorConcentration: 1,
  marketCashLow: 1
};
const defaultAlertThresholds = {
  sectorWeightHigh: 50,
  marketCashLow: 10
};

function hasArg(name) {
  return process.argv.indexOf(name) >= 0;
}

function argValue(name, fallback) {
  const prefix = name + "=";
  const found = process.argv.find(function (arg) {
    return arg.indexOf(prefix) === 0;
  });
  return found ? found.slice(prefix.length) : fallback;
}

function ensureDataDir() {
  if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });
}

function readJson(filePath, fallback) {
  try {
    if (!fs.existsSync(filePath)) return fallback;
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    return fallback;
  }
}

function writePrivateJson(filePath, payload) {
  ensureDataDir();
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2) + "\n", {
    encoding: "utf8",
    mode: 0o600
  });
}

function kstParts(date) {
  return zonedParts(date, "Asia/Seoul");
}

function zonedParts(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    weekday: "short"
  }).formatToParts(date);
  const map = {};
  parts.forEach(function (part) {
    if (part.type !== "literal") map[part.type] = part.value;
  });
  return {
    date: [map.year, map.month, map.day].join("-"),
    minutes: Number(map.hour) * 60 + Number(map.minute),
    time: [map.hour, map.minute].join(":"),
    weekday: map.weekday
  };
}

function notificationKind() {
  const explicit = argValue("--kind", "").trim();
  if (explicit) return explicit;

  const now = kstParts(new Date());
  if (now.minutes >= 8 * 60 + 20 && now.minutes <= 9 * 60 + 20) return "morning";
  if (now.minutes >= 15 * 60 + 50 && now.minutes <= 16 * 60 + 40) return "close";
  if (now.minutes >= 9 * 60 && now.minutes <= 15 * 60 + 30) return "intraday";
  return "status";
}

function formatMoney(value) {
  const amount = Number(value || 0);
  if (!Number.isFinite(amount) || amount <= 0) return "-";
  if (amount >= 100000000) return Math.round(amount / 100000000) + "억";
  if (amount >= 10000) return Math.round(amount / 10000).toLocaleString("ko-KR") + "만";
  return Math.round(amount).toLocaleString("ko-KR");
}

function signedPct(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0%";
  return (number > 0 ? "+" : "") + Math.round(number * 10) / 10 + "%";
}

function notificationIntervalMinutes() {
  return Math.max(1, Number(runtimeSettings().notifyIntervalMinutes || process.env.NOTIFY_INTERVAL_MINUTES || 10));
}

function parseNumberAssignments(value, defaults) {
  const map = Object.assign({}, defaults || {});
  String(value || "")
    .split(/\r?\n/)
    .map(function (line) { return line.trim(); })
    .filter(Boolean)
    .forEach(function (line) {
      const parts = line.split(/[=:,]/);
      const key = String(parts[0] || "").trim();
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) return;
      map[key] = Number(parts.slice(1).join(":")) || 0;
    });
  return map;
}

function notificationAlertRules() {
  return parseNumberAssignments(runtimeSettings().alertRules, defaultAlertRules);
}

function notificationAlertThresholds() {
  return parseNumberAssignments(runtimeSettings().alertThresholds, defaultAlertThresholds);
}

function alertRuleEnabled(rules, key) {
  return !key || Number((rules || {})[key]) !== 0;
}

function realtimeIntervalSeconds() {
  return Math.max(1, Number(process.env.REALTIME_NOTIFY_INTERVAL_SECONDS || 60));
}

function currentRunDate() {
  const explicit = argValue("--at", "").trim();
  if (!explicit) return new Date();
  const parsed = new Date(explicit);
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
}

function intervalBucketKey(parts) {
  const interval = notificationIntervalMinutes();
  const bucket = Math.floor(parts.minutes / interval);
  return [parts.date, interval + "m", bucket].join(":");
}

function event(severity, key, line, rule, meta) {
  return { severity: severity, key: key, line: line, rule: rule || "", meta: meta || {} };
}

function isWeekend(parts) {
  return parts.weekday === "Sat" || parts.weekday === "Sun";
}

function marketProfile(item) {
  const market = String(item.market || "").trim().toUpperCase();
  const currency = String(item.currency || "").trim().toUpperCase();
  const symbol = String(item.symbol || "").trim();
  if (market === "KR" || market === "KOSPI" || market === "KOSDAQ" || currency === "KRW" || /^[0-9]{6}$/.test(symbol)) {
    return {
      id: "KR",
      label: "한국장",
      timeZone: "Asia/Seoul",
      preStart: 7 * 60 + 30,
      openStart: 9 * 60,
      openEnd: 15 * 60 + 30,
      afterEnd: 18 * 60
    };
  }
  return {
    id: "US",
    label: "미국장",
    timeZone: "America/New_York",
    preStart: 4 * 60,
    openStart: 9 * 60 + 30,
    openEnd: 16 * 60,
    afterEnd: 20 * 60
  };
}

function marketSession(item, date) {
  const profile = marketProfile(item);
  const parts = zonedParts(date, profile.timeZone);
  let phase = "closed";
  let label = "장외";
  if (isWeekend(parts)) {
    label = "휴장";
  } else if (parts.minutes >= profile.openStart && parts.minutes < profile.openEnd) {
    phase = "open";
    label = "장중";
  } else if (parts.minutes >= profile.preStart && parts.minutes < profile.openStart) {
    phase = "pre";
    label = "장전";
  } else if (parts.minutes >= profile.openEnd && parts.minutes < profile.afterEnd) {
    phase = "after";
    label = "장후";
  }
  return {
    market: profile.label,
    phase: phase,
    label: profile.label + " " + label,
    localTime: parts.time
  };
}

function sectorRatio(portfolio, sector) {
  const entry = (portfolio.sectors || []).find(function (item) {
    return item.sector === sector;
  });
  return entry ? Number(entry.ratio || 0) : 0;
}

function portfolioCashRatio(portfolio) {
  const total = Number(portfolio.total || 0);
  if (total <= 0) return 0;
  return Math.round((Number(portfolio.cash || 0) / total) * 100);
}

function marketCashExposure(portfolio, item) {
  const profile = marketProfile(item);
  const markets = Array.isArray(portfolio.markets) ? portfolio.markets : [];
  return markets.find(function (entry) {
    return entry.key === profile.id;
  }) || null;
}

function cashRatio(portfolio, item) {
  const exposure = item ? marketCashExposure(portfolio, item) : null;
  if (exposure && Number(exposure.total || 0) > 0) return Number(exposure.cashRatio || 0);
  return portfolioCashRatio(portfolio);
}

function cashLabel(portfolio, item) {
  const exposure = item ? marketCashExposure(portfolio, item) : null;
  if (exposure && Number(exposure.total || 0) > 0) {
    return exposure.label + " 현금 " + Number(exposure.cashRatio || 0) + "%";
  }
  return "현금 " + portfolioCashRatio(portfolio) + "%";
}

function marketCashSummary(portfolio) {
  const markets = Array.isArray(portfolio.markets) ? portfolio.markets : [];
  const lines = markets.filter(function (entry) {
    return Number(entry.total || 0) > 0;
  }).map(function (entry) {
    return entry.label + " 현금 " + Number(entry.cashRatio || 0) + "%";
  });
  return lines.length ? lines.join(" / ") : "현금 " + formatMoney(portfolio.cash);
}

function holdingSeverity(item) {
  if (item.tone === "danger") return "ALERT";
  if (item.tone === "caution" || item.tone === "hold") return "WATCH";
  return "INFO";
}

function sellTimingCore(item, ratio) {
  const pnlRate = Number(item.profitLossRate || 0);
  if (item.tone === "danger") {
    if (pnlRate <= -8) return "손절 기준 즉시 재확인";
    return "분할매도 기준 즉시 확인";
  }
  if (item.tone === "caution") return "일부 익절/비중축소 기준 확인";
  if (pnlRate <= -8) return "손실 허용폭/손절 기준 재확인";
  if (ratio >= 50) return "섹터 쏠림 완화 기준 확인";
  if (pnlRate >= 8) return "급등 시 일부 익절 기준 대기";
  return "보유 조건 이탈 때만 축소 검토";
}

function buyTimingCore(item, accountCashRatio, accountCashLabel) {
  const pnlRate = Number(item.profitLossRate || 0);
  if (accountCashRatio <= 10) return accountCashLabel + "라 추가매수 대기";
  if (item.tone === "danger") return "리스크 해소 전 추가매수 보류";
  if (pnlRate <= -8) return "하락 사유 해소 후만 분할 검토";
  if (pnlRate >= 10) return "추격매수 보류";
  if (item.tone === "hold") return "목표비중 이하에서만 분할 검토";
  return "기준가와 현금 여유 충족 시 분할 검토";
}

function sellTiming(item, ratio, session) {
  const core = sellTimingCore(item, ratio);
  const pnlRate = Number(item.profitLossRate || 0);
  if (session.phase === "open") return core;
  if (session.phase === "pre") {
    if (item.tone === "danger") return "개장 후 " + core.replace("즉시 ", "");
    if (pnlRate <= -8) return "개장 후 손실 허용폭 확인";
    if (item.tone === "caution") return "개장 후 익절/축소 기준 확인";
    return "개장 전 기준가 준비";
  }
  if (session.phase === "after") {
    if (pnlRate <= -8) return "다음 정규장 손절 기준 재점검";
    if (item.tone === "caution") return "다음 정규장 익절/축소 재점검";
    return "장후 거래 유동성 주의, 다음 정규장 재점검";
  }
  if (pnlRate <= -8) return "장외라 다음 장 손절 기준 정리";
  return "장외라 주문보다 기준 정리";
}

function buyTiming(item, accountCashRatio, accountCashLabel, session) {
  const core = buyTimingCore(item, accountCashRatio, accountCashLabel);
  if (session.phase === "open") return core;
  if (session.phase === "pre") return "개장 후 가격 확인";
  if (session.phase === "after") return "장후 추격보다 다음 정규장 대기";
  return "장외라 추가매수 보류";
}

function buildHoldingTimingEvents(snapshot, now, date) {
  const portfolio = snapshot.portfolio || {};
  const decision = snapshot.tossDecision || {};
  const items = (Array.isArray(decision.items) ? decision.items : []).filter(function (item) {
    return item.source === "holding";
  });
  const bucket = intervalBucketKey(now);

  return items.map(function (item) {
    const ratio = sectorRatio(portfolio, item.sector);
    const session = marketSession(item, date);
    const itemCashRatio = cashRatio(portfolio, item);
    const itemCashLabel = cashLabel(portfolio, item);
    const line = [
      item.name + ": " + session.label + " " + session.localTime,
      "상태 " + item.decision,
      "손익 " + signedPct(item.profitLossRate),
      "매도 " + sellTiming(item, ratio, session),
      "매수 " + buyTiming(item, itemCashRatio, itemCashLabel, session)
    ].join(" · ");
    return event(
      holdingSeverity(item),
      [bucket, "holding-timing", item.symbol || item.name, session.phase, item.decision].join(":"),
      line,
      "holdingTiming",
      { splitMessage: true, symbol: item.symbol, name: item.name }
    );
  });
}

function buildCashEvents(portfolio, now, thresholds) {
  const cashLow = Number(thresholds.marketCashLow || defaultAlertThresholds.marketCashLow);
  let markets = Array.isArray(portfolio.markets) ? portfolio.markets.filter(function (entry) {
    return Number(entry.total || 0) > 0;
  }) : [];
  if (!markets.length && Number(portfolio.total || 0) > 0) {
    markets = [{
      key: "total",
      label: "전체",
      total: portfolio.total,
      cashRatio: portfolioCashRatio(portfolio)
    }];
  }

  const events = [];
  markets.forEach(function (entry) {
    const ratio = Number(entry.cashRatio || 0);
    if (ratio <= cashLow / 2) {
      events.push(event(
        "ALERT",
        [now.date, "cash-low", entry.key, ratio].join(":"),
        entry.label + " 현금 비중이 " + ratio + "%입니다. 신규 매수 전 유동성 확인 필요.",
        "marketCashLow"
      ));
    } else if (ratio <= cashLow) {
      events.push(event(
        "WATCH",
        [now.date, "cash-low", entry.key, ratio].join(":"),
        entry.label + " 현금 비중이 " + ratio + "%입니다.",
        "marketCashLow"
      ));
    }
  });
  return events;
}

function realtimeSellSignal(item, ratio) {
  const pnlRate = Number(item.profitLossRate || 0);
  if (item.tone === "danger") {
    return {
      severity: "ALERT",
      code: pnlRate <= -8 ? "stop" : "sell",
      label: pnlRate <= -8 ? "손절 기준 확인" : "매도 기준 확인",
      reason: sellTimingCore(item, ratio)
    };
  }
  if (item.tone === "caution") {
    return {
      severity: "WATCH",
      code: "trim",
      label: "분할매도/익절 확인",
      reason: sellTimingCore(item, ratio)
    };
  }
  if (pnlRate <= -8) {
    return {
      severity: "ALERT",
      code: "loss",
      label: "손실 기준 확인",
      reason: "손실 허용폭/손절 기준 재확인"
    };
  }
  if (ratio >= 50) {
    return {
      severity: "WATCH",
      code: "sector",
      label: "비중 축소 확인",
      reason: "섹터 쏠림 완화 기준 확인"
    };
  }
  return null;
}

function realtimeBuySignal(item, portfolio) {
  const pnlRate = Number(item.profitLossRate || 0);
  const ratio = cashRatio(portfolio, item);
  if (ratio <= 10) return null;
  if (item.tone === "danger" || item.tone === "caution") return null;
  if (pnlRate <= -8 || pnlRate >= 10) return null;
  if (pnlRate <= 0) {
    return {
      severity: "WATCH",
      code: "pullback",
      label: "분할매수 검토",
      reason: cashLabel(portfolio, item) + " 여유, 하락 구간 기준가 확인"
    };
  }
  return null;
}

function buildRealtimeTimingEvents(snapshot, date) {
  const now = kstParts(date);
  const toss = snapshot.toss || {};
  if (toss.mode !== "live" && !snapshot.mock) return [];

  const portfolio = snapshot.portfolio || {};
  const decision = snapshot.tossDecision || {};
  const items = (Array.isArray(decision.items) ? decision.items : []).filter(function (item) {
    return item.source === "holding";
  });
  const events = [];

  items.forEach(function (item) {
    const session = marketSession(item, date);
    if (session.phase !== "open") return;

    const ratio = sectorRatio(portfolio, item.sector);
    const sell = realtimeSellSignal(item, ratio);
    const buy = sell ? null : realtimeBuySignal(item, portfolio);
    const signal = sell || buy;
    if (!signal) return;

    events.push(event(
      signal.severity,
      [now.date, "realtime", signal.code, item.symbol || item.name].join(":"),
      [
        item.name + ": " + session.label + " " + session.localTime,
        signal.label,
        "손익 " + signedPct(item.profitLossRate),
        signal.reason
      ].join(" · "),
      "holdingTiming",
      { splitMessage: true, symbol: item.symbol, name: item.name }
    ));
  });

  return events;
}

function filterEventsByRules(events, rules) {
  return events.filter(function (item) {
    return alertRuleEnabled(rules, item.rule);
  });
}

function buildEvents(snapshot, kind) {
  const currentDate = currentRunDate();
  const now = kstParts(currentDate);
  const rules = notificationAlertRules();
  const thresholds = notificationAlertThresholds();
  const toss = snapshot.toss || {};
  const portfolio = snapshot.portfolio || {};
  const decision = snapshot.tossDecision || {};
  const positions = Array.isArray(toss.positions) ? toss.positions : [];
  const items = Array.isArray(decision.items) ? decision.items : [];
  const sectors = Array.isArray(portfolio.sectors) ? portfolio.sectors : [];
  const events = [];

  if (toss.mode !== "live") {
    events.push(event(
      "ALERT",
      [now.date, "connection", toss.status || "not-live"].join(":"),
      "토스 live 연결이 아닙니다: " + (toss.status || "상태 확인 필요"),
      "tossConnection"
    ));
  }

  if (toss.mode === "live" && positions.length === 0) {
    events.push(event(
      "WATCH",
      [now.date, "empty-positions"].join(":"),
      "토스 연결은 성공했지만 보유 종목이 비어 있습니다.",
      "tossConnection"
    ));
  }

  buildHoldingTimingEvents(snapshot, now, currentDate).forEach(function (item) {
    events.push(item);
  });

  const concentration = Number(portfolio.concentration || 0);
  const concentrationHigh = Number(thresholds.sectorWeightHigh || defaultAlertThresholds.sectorWeightHigh);
  const concentrationWatch = Math.max(1, Math.round(concentrationHigh * 0.7));
  if (concentration >= concentrationHigh) {
    events.push(event(
      "ALERT",
      [now.date, "concentration", concentration].join(":"),
      "계좌 최대 섹터 노출이 " + concentration + "%입니다. 비중 기준 확인 필요.",
      "sectorConcentration"
    ));
  } else if (concentration >= concentrationWatch) {
    events.push(event(
      "WATCH",
      [now.date, "concentration", concentration].join(":"),
      "계좌 최대 섹터 노출이 " + concentration + "%입니다.",
      "sectorConcentration"
    ));
  }

  buildCashEvents(portfolio, now, thresholds).forEach(function (item) {
    events.push(item);
  });

  if (kind === "morning" || kind === "close" || kind === "status") {
    const lead = items[0];
    const sector = sectors[0];
    const summaryLine = [
      "보유 " + (decision.holdingCount || 0) + "개 / 관심 " + (decision.watchCount || 0) + "개",
      "평가 " + formatMoney(portfolio.invested),
      marketCashSummary(portfolio),
      sector ? "최대 노출 " + sector.sector + " " + sector.ratio + "%" : "",
      lead ? "우선 점검 " + lead.name + " · " + lead.decision : ""
    ].filter(Boolean).join(" · ");
    events.unshift(event("INFO", [now.date, kind, "summary"].join(":"), summaryLine));
  }

  return filterEventsByRules(events, rules);
}

function loadNotificationState() {
  const state = readJson(notificationStatePath, { sent: {} });
  if (!state.sent || typeof state.sent !== "object") state.sent = {};
  return state;
}

function pruneState(state) {
  const cutoff = Date.now() - 14 * 24 * 60 * 60 * 1000;
  Object.keys(state.sent).forEach(function (key) {
    if (Date.parse(state.sent[key]) < cutoff) delete state.sent[key];
  });
}

function unsentEvents(events, state, force) {
  if (force) return events;
  return events.filter(function (item) {
    return !state.sent[item.key];
  });
}

function markSent(events, state) {
  const stamp = new Date().toISOString();
  events.forEach(function (item) {
    state.sent[item.key] = stamp;
  });
  pruneState(state);
  writePrivateJson(notificationStatePath, state);
}

function maxSeverity(events) {
  return events.reduce(function (current, item) {
    return severityRank[item.severity] > severityRank[current] ? item.severity : current;
  }, "INFO");
}

function notificationTitle(kind) {
  const labels = {
    morning: "장전 점검",
    intraday: "장중 알림",
    close: "마감 요약",
    status: "상태 점검",
    realtime: "실시간 타이밍"
  };
  return labels[kind] || "알림";
}

function composeMessage(events, kind) {
  const severity = maxSeverity(events);
  const lines = [
    "[Twin " + severity + "] " + notificationTitle(kind)
  ].concat(events.map(function (item) {
    return "- " + item.line;
  }));
  const text = lines.join("\n");
  return text.length > 900 ? text.slice(0, 897) + "..." : text;
}

function composeSingleEventMessage(item, kind) {
  const titleName = item.meta && item.meta.name ? " · " + item.meta.name : "";
  const lines = ["[Twin " + item.severity + "] " + notificationTitle(kind) + titleName];
  String(item.line || "").split(" · ").forEach(function (part) {
    if (part) lines.push("- " + part);
  });
  const text = lines.join("\n");
  return text.length > 900 ? text.slice(0, 897) + "..." : text;
}

function composeMessages(events, kind) {
  const bundled = events.filter(function (item) {
    return !(item.meta && item.meta.splitMessage);
  });
  const split = events.filter(function (item) {
    return item.meta && item.meta.splitMessage;
  });
  const messages = bundled.length ? [composeMessage(bundled, kind)] : [];
  split.forEach(function (item) {
    messages.push(composeSingleEventMessage(item, kind));
  });
  return messages;
}

function httpsJson(method, targetUrl, headers, body) {
  return new Promise(function (resolve, reject) {
    const parsed = new URL(targetUrl);
    const request = https.request({
      method: method,
      hostname: parsed.hostname,
      path: parsed.pathname + parsed.search,
      headers: headers,
      timeout: 12000
    }, function (response) {
      let raw = "";
      response.setEncoding("utf8");
      response.on("data", function (chunk) {
        raw += chunk;
      });
      response.on("end", function () {
        let payload = {};
        try {
          payload = raw ? JSON.parse(raw) : {};
        } catch (error) {
          payload = { raw: raw };
        }
        resolve({ statusCode: response.statusCode, payload: payload });
      });
    });
    request.on("timeout", function () {
      request.destroy(new Error("request timeout"));
    });
    request.on("error", reject);
    if (body) request.write(body);
    request.end();
  });
}

async function refreshKakaoAccessToken() {
  const restApiKey = String(process.env.KAKAO_REST_API_KEY || "").trim();
  const tokenState = readJson(kakaoTokenPath, {});
  const refreshToken = String(process.env.KAKAO_REFRESH_TOKEN || tokenState.refreshToken || "").trim();
  if (!restApiKey || !refreshToken) return "";

  const form = new URLSearchParams();
  form.set("grant_type", "refresh_token");
  form.set("client_id", restApiKey);
  form.set("refresh_token", refreshToken);
  const clientSecret = String(process.env.KAKAO_CLIENT_SECRET || "").trim();
  if (clientSecret) form.set("client_secret", clientSecret);

  const response = await httpsJson("POST", "https://kauth.kakao.com/oauth/token", {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    "Content-Length": Buffer.byteLength(form.toString())
  }, form.toString());

  if (response.statusCode < 200 || response.statusCode >= 300 || !response.payload.access_token) {
    throw new Error("카카오 access token 갱신 실패: HTTP " + response.statusCode);
  }

  writePrivateJson(kakaoTokenPath, {
    accessToken: response.payload.access_token,
    refreshToken: response.payload.refresh_token || refreshToken,
    updatedAt: new Date().toISOString(),
    expiresIn: response.payload.expires_in || null
  });
  return response.payload.access_token;
}

async function kakaoAccessToken() {
  const direct = String(process.env.KAKAO_ACCESS_TOKEN || "").trim();
  if (direct) return direct;
  const tokenState = readJson(kakaoTokenPath, {});
  if (tokenState.accessToken) return tokenState.accessToken;
  return refreshKakaoAccessToken();
}

async function sendKakaoMessage(text) {
  let token = await kakaoAccessToken();
  if (!token) return { delivered: false, reason: "카카오 토큰 미설정" };

  async function sendWithToken(accessToken) {
    const settings = runtimeSettings();
    const link = String(settings.notifyLinkUrl || process.env.NOTIFY_LINK_URL || "http://127.0.0.1:3000").trim();
    const template = {
      object_type: "text",
      text: text,
      link: {
        web_url: link,
        mobile_web_url: link
      },
      button_title: "대시보드"
    };
    const form = new URLSearchParams();
    form.set("template_object", JSON.stringify(template));
    return httpsJson("POST", "https://kapi.kakao.com/v2/api/talk/memo/default/send", {
      "Authorization": "Bearer " + accessToken,
      "Accept": "application/json",
      "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
      "Content-Length": Buffer.byteLength(form.toString())
    }, form.toString());
  }

  let response = await sendWithToken(token);
  if (response.statusCode === 401 && !process.env.KAKAO_ACCESS_TOKEN) {
    token = await refreshKakaoAccessToken();
    response = await sendWithToken(token);
  }

  if (response.statusCode < 200 || response.statusCode >= 300) {
    return {
      delivered: false,
      reason: "카카오 발송 실패: HTTP " + response.statusCode
    };
  }
  return { delivered: true };
}

function telegramCredentials() {
  const settings = runtimeSettings();
  return {
    botToken: String(settings.telegramBotToken || process.env.TELEGRAM_BOT_TOKEN || "").trim(),
    chatId: String(settings.telegramChatId || process.env.TELEGRAM_CHAT_ID || "").trim()
  };
}

function hasTelegramCredentials() {
  const credentials = telegramCredentials();
  return Boolean(credentials.botToken && credentials.chatId);
}

function hasKakaoCredentials() {
  if (String(process.env.KAKAO_ACCESS_TOKEN || "").trim()) return true;
  if (String(process.env.KAKAO_REST_API_KEY || "").trim() && String(process.env.KAKAO_REFRESH_TOKEN || "").trim()) return true;
  const tokenState = readJson(kakaoTokenPath, {});
  return Boolean(tokenState.accessToken || (String(process.env.KAKAO_REST_API_KEY || "").trim() && tokenState.refreshToken));
}

async function sendTelegramMessage(text) {
  const credentials = telegramCredentials();
  if (!credentials.botToken || !credentials.chatId) {
    return { delivered: false, reason: "텔레그램 토큰 또는 chat id 미설정" };
  }

  const body = JSON.stringify({
    chat_id: credentials.chatId,
    text: text,
    disable_web_page_preview: true
  });
  const response = await httpsJson("POST", "https://api.telegram.org/bot" + credentials.botToken + "/sendMessage", {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body)
  }, body);

  if (response.statusCode < 200 || response.statusCode >= 300 || response.payload.ok === false) {
    const description = response.payload && response.payload.description ? " (" + response.payload.description + ")" : "";
    return {
      delivered: false,
      reason: "텔레그램 발송 실패: HTTP " + response.statusCode + description
    };
  }
  return { delivered: true };
}

function selectedProvider() {
  const settings = runtimeSettings();
  const explicit = String(settings.notifyProvider || process.env.NOTIFY_PROVIDER || "").trim().toLowerCase();
  if (explicit) return explicit;
  if (hasTelegramCredentials()) return "telegram";
  if (hasKakaoCredentials()) return "kakao";
  return "console";
}

async function sendNotification(text) {
  const provider = selectedProvider();
  if (provider === "telegram") {
    const result = await sendTelegramMessage(text);
    return Object.assign({ provider: "telegram", label: "Telegram" }, result);
  }
  if (provider === "kakao") {
    const result = await sendKakaoMessage(text);
    return Object.assign({ provider: "kakao", label: "Kakao" }, result);
  }
  if (provider === "console") {
    return { provider: "console", label: "Console", delivered: false, reason: "콘솔 전용 모드" };
  }
  return {
    provider: provider,
    label: provider,
    delivered: false,
    reason: "지원하지 않는 NOTIFY_PROVIDER: " + provider
  };
}

async function sendMessages(messages) {
  let lastResult = null;
  for (let index = 0; index < messages.length; index += 1) {
    lastResult = await sendNotification(messages[index]);
    if (!lastResult.delivered) return lastResult;
  }
  return Object.assign({ delivered: true, label: "Notification" }, lastResult || {});
}

function telegramChatFromUpdate(update) {
  const candidates = [
    update.message,
    update.edited_message,
    update.channel_post,
    update.edited_channel_post,
    update.my_chat_member,
    update.chat_member
  ];
  for (let index = 0; index < candidates.length; index += 1) {
    const item = candidates[index];
    if (item && item.chat) return item.chat;
  }
  return null;
}

function telegramChatLabel(chat) {
  const name = [chat.first_name, chat.last_name].filter(Boolean).join(" ");
  const handle = chat.username ? "@" + chat.username : "";
  return [chat.type, handle || name || chat.title || ""].filter(Boolean).join(" ");
}

async function printTelegramChatIds() {
  const credentials = telegramCredentials();
  if (!credentials.botToken) {
    console.log("TELEGRAM_BOT_TOKEN is required.");
    return;
  }

  const response = await httpsJson("GET", "https://api.telegram.org/bot" + credentials.botToken + "/getUpdates", {
    "Accept": "application/json"
  });
  if (response.statusCode < 200 || response.statusCode >= 300 || response.payload.ok === false) {
    const description = response.payload && response.payload.description ? " (" + response.payload.description + ")" : "";
    throw new Error("텔레그램 업데이트 조회 실패: HTTP " + response.statusCode + description);
  }

  const chats = {};
  (Array.isArray(response.payload.result) ? response.payload.result : []).forEach(function (update) {
    const chat = telegramChatFromUpdate(update);
    if (chat && chat.id !== undefined) chats[String(chat.id)] = telegramChatLabel(chat);
  });

  const ids = Object.keys(chats);
  if (ids.length === 0) {
    console.log("No Telegram chat ids found. Open the bot, send /start, then retry.");
    return;
  }

  console.log("Telegram chat ids:");
  ids.forEach(function (id) {
    console.log("- " + id + (chats[id] ? " (" + chats[id] + ")" : ""));
  });
}

async function runOnce() {
  const kind = notificationKind();
  const force = hasArg("--force");
  const dryRun = hasArg("--dry-run");
  const snapshot = await flowLensSnapshot({
    mock: hasArg("--mock"),
    watchlistSymbols: runtimeSettings().watchlistSymbols
  });
  const state = loadNotificationState();
  const events = unsentEvents(buildEvents(snapshot, kind), state, force);

  if (events.length === 0) {
    console.log("No new notification events.");
    return;
  }

  const messages = composeMessages(events, kind);
  if (dryRun) {
    console.log(messages.join("\n\n"));
    return;
  }

  const result = await sendMessages(messages);
  if (!result.delivered) {
    console.log(messages.join("\n\n"));
    console.log("Delivery: console only (" + result.reason + ")");
    return;
  }

  markSent(events, state);
  console.log(result.label + " notification sent: " + events.length + " event(s), " + messages.length + " message(s).");
}

async function runRealtimeOnce() {
  const force = hasArg("--force");
  const dryRun = hasArg("--dry-run");
  const snapshot = await flowLensSnapshot({
    mock: hasArg("--mock"),
    watchlistSymbols: runtimeSettings().watchlistSymbols
  });
  const state = loadNotificationState();
  const events = unsentEvents(
    filterEventsByRules(buildRealtimeTimingEvents(snapshot, currentRunDate()), notificationAlertRules()),
    state,
    force
  );

  if (events.length === 0) {
    console.log("No realtime timing events.");
    return;
  }

  const messages = composeMessages(events, "realtime");
  if (dryRun) {
    console.log(messages.join("\n\n"));
    return;
  }

  const result = await sendMessages(messages);
  if (!result.delivered) {
    console.log(messages.join("\n\n"));
    console.log("Delivery: console only (" + result.reason + ")");
    return;
  }

  markSent(events, state);
  console.log(result.label + " realtime notification sent: " + events.length + " event(s), " + messages.length + " message(s).");
}

async function runDaemon() {
  const intervalMinutes = notificationIntervalMinutes();
  console.log("Notify worker started. interval=" + intervalMinutes + "m");
  await runOnce();
  setInterval(function () {
    runOnce().catch(function (error) {
      console.error(error.message || error);
    });
  }, intervalMinutes * 60 * 1000);
}

async function runRealtimeDaemon() {
  const intervalSeconds = realtimeIntervalSeconds();
  let running = false;
  console.log("Realtime notify worker started. interval=" + intervalSeconds + "s");

  async function tick() {
    if (running) return;
    running = true;
    try {
      await runRealtimeOnce();
    } finally {
      running = false;
    }
  }

  await tick();
  setInterval(function () {
    tick().catch(function (error) {
      console.error(error.message || error);
    });
  }, intervalSeconds * 1000);
}

if (hasArg("--telegram-chat-ids")) {
  printTelegramChatIds().catch(function (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  });
} else if (hasArg("--realtime-daemon")) {
  runRealtimeDaemon().catch(function (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  });
} else if (hasArg("--realtime")) {
  runRealtimeOnce().catch(function (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  });
} else if (hasArg("--daemon")) {
  runDaemon().catch(function (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  });
} else {
  runOnce().catch(function (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}
