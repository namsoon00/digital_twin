const fs = require("fs");
const https = require("https");
const path = require("path");
const { URLSearchParams } = require("url");

const rootDir = path.resolve(__dirname, "..");
process.chdir(rootDir);

const { flowLensSnapshot } = require("../server");

const dataDir = path.join(rootDir, "data");
const notificationStatePath = path.join(dataDir, "notification-state.json");
const kakaoTokenPath = path.join(dataDir, "kakao-token.json");

const severityRank = { INFO: 1, WATCH: 2, ALERT: 3 };

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
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul",
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

function event(severity, key, line) {
  return { severity: severity, key: key, line: line };
}

function buildEvents(snapshot, kind) {
  const now = kstParts(new Date());
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
      "토스 live 연결이 아닙니다: " + (toss.status || "상태 확인 필요")
    ));
  }

  if (toss.mode === "live" && positions.length === 0) {
    events.push(event(
      "WATCH",
      [now.date, "empty-positions"].join(":"),
      "토스 연결은 성공했지만 보유 종목이 비어 있습니다."
    ));
  }

  items
    .filter(function (item) {
      return item.source === "holding" && (item.tone === "danger" || item.tone === "caution");
    })
    .slice(0, 5)
    .forEach(function (item) {
      const severity = item.tone === "danger" ? "ALERT" : "WATCH";
      events.push(event(
        severity,
        [now.date, "risk", item.symbol, item.decision].join(":"),
        item.name + " " + item.decision + " · 손익률 " + signedPct(item.profitLossRate)
      ));
    });

  const concentration = Number(portfolio.concentration || 0);
  if (concentration >= 50) {
    events.push(event(
      "ALERT",
      [now.date, "concentration", concentration].join(":"),
      "계좌 최대 섹터 노출이 " + concentration + "%입니다. 비중 기준 확인 필요."
    ));
  } else if (concentration >= 35) {
    events.push(event(
      "WATCH",
      [now.date, "concentration", concentration].join(":"),
      "계좌 최대 섹터 노출이 " + concentration + "%입니다."
    ));
  }

  const total = Number(portfolio.total || 0);
  const cash = Number(portfolio.cash || 0);
  const cashRatio = total > 0 ? Math.round((cash / total) * 100) : 0;
  if (total > 0 && cashRatio <= 5) {
    events.push(event(
      "ALERT",
      [now.date, "cash-low", cashRatio].join(":"),
      "현금 비중이 " + cashRatio + "%입니다. 신규 매수 전 유동성 확인 필요."
    ));
  } else if (total > 0 && cashRatio <= 10) {
    events.push(event(
      "WATCH",
      [now.date, "cash-low", cashRatio].join(":"),
      "현금 비중이 " + cashRatio + "%입니다."
    ));
  }

  if (kind === "morning" || kind === "close" || kind === "status") {
    const lead = items[0];
    const sector = sectors[0];
    const summaryLine = [
      "보유 " + (decision.holdingCount || 0) + "개 / 관심 " + (decision.watchCount || 0) + "개",
      "평가 " + formatMoney(portfolio.invested),
      "현금 " + formatMoney(portfolio.cash),
      sector ? "최대 노출 " + sector.sector + " " + sector.ratio + "%" : "",
      lead ? "우선 점검 " + lead.name + " · " + lead.decision : ""
    ].filter(Boolean).join(" · ");
    events.unshift(event("INFO", [now.date, kind, "summary"].join(":"), summaryLine));
  }

  return events;
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

function composeMessage(events, kind) {
  const severity = maxSeverity(events);
  const labels = {
    morning: "장전 점검",
    intraday: "장중 알림",
    close: "마감 요약",
    status: "상태 점검"
  };
  const lines = [
    "[Twin " + severity + "] " + (labels[kind] || "알림")
  ].concat(events.map(function (item) {
    return "- " + item.line;
  }));
  const text = lines.join("\n");
  return text.length > 900 ? text.slice(0, 897) + "..." : text;
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
    const link = String(process.env.NOTIFY_LINK_URL || "http://127.0.0.1:3000").trim();
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
  return {
    botToken: String(process.env.TELEGRAM_BOT_TOKEN || "").trim(),
    chatId: String(process.env.TELEGRAM_CHAT_ID || "").trim()
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
  const explicit = String(process.env.NOTIFY_PROVIDER || "").trim().toLowerCase();
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
    watchlistSymbols: process.env.WATCHLIST_SYMBOLS
  });
  const state = loadNotificationState();
  const events = unsentEvents(buildEvents(snapshot, kind), state, force);

  if (events.length === 0) {
    console.log("No new notification events.");
    return;
  }

  const message = composeMessage(events, kind);
  if (dryRun) {
    console.log(message);
    return;
  }

  const result = await sendNotification(message);
  if (!result.delivered) {
    console.log(message);
    console.log("Delivery: console only (" + result.reason + ")");
    return;
  }

  markSent(events, state);
  console.log(result.label + " notification sent: " + events.length + " event(s).");
}

async function runDaemon() {
  const intervalMinutes = Math.max(1, Number(process.env.NOTIFY_INTERVAL_MINUTES || 10));
  console.log("Notify worker started. interval=" + intervalMinutes + "m");
  await runOnce();
  setInterval(function () {
    runOnce().catch(function (error) {
      console.error(error.message || error);
    });
  }, intervalMinutes * 60 * 1000);
}

if (hasArg("--telegram-chat-ids")) {
  printTelegramChatIds().catch(function (error) {
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
