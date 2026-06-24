const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const url = require("url");
const os = require("os");
const childProcess = require("child_process");

const rootDir = process.cwd();
const publicDir = path.join(rootDir, "public");
const dataDir = path.join(rootDir, "data");
const storePath = path.join(dataDir, "store.json");
const codexPath = process.env.CODEX_BIN || "codex";

loadEnv(".env");
loadEnv(".env.local");

const memoryCategories = ["identity", "preference", "finance", "travel", "asset", "schedule", "work", "other"];
const domainTypes = ["stock", "trip", "asset", "schedule", "task", "note"];

function loadEnv(fileName) {
  const envPath = path.join(rootDir, fileName);
  if (!fs.existsSync(envPath)) return;

  const lines = fs.readFileSync(envPath, "utf8").split(/\r?\n/);
  lines.forEach(function (line) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) return;
    const index = trimmed.indexOf("=");
    if (index < 0) return;
    const key = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1).trim().replace(/^["']|["']$/g, "");
    if (!process.env[key]) process.env[key] = value;
  });
}

function id(prefix) {
  return prefix + "-" + crypto.randomBytes(8).toString("hex");
}

function now() {
  return new Date().toISOString();
}

function defaultStore() {
  const stamped = now();
  return {
    version: 1,
    profile: {
      ownerName: "Namsoon",
      assistantName: "Twin",
      preferredLanguage: "한국어",
      answerStyle: "핵심부터 말하고, 필요한 근거와 실행 단계를 짧게 정리한다.",
      tone: "담백하고 실무적인 말투. 과장하지 않는다.",
      decisionStyle: "선택지를 비교하고 리스크와 다음 행동을 분리해서 판단한다.",
      riskStyle: "투자와 자산 판단은 보수적으로 접근하고, 확신이 낮으면 추가 확인을 요구한다.",
      financePolicy: "주식은 매수/매도 지시가 아니라 관찰 포인트, 리스크, 체크리스트 중심으로 돕는다.",
      travelPolicy: "여행은 예산, 이동 동선, 피로도, 예약 마감일을 함께 본다.",
      schedulePolicy: "일정은 오늘 처리할 것, 미룰 것, 위임할 것을 나눠서 관리한다.",
      assetPolicy: "자산은 계좌번호나 인증 정보 없이 요약 단위로 기록하고, 목표와 현금흐름 중심으로 관리한다.",
      boundaries: "법률, 세무, 투자 판단은 최종 결정을 대신하지 않는다. 민감한 정보는 저장하지 않는다."
    },
    memories: [
      {
        id: "mem-default-1",
        content: "사용자는 한국어로 명확하고 실용적인 답변을 선호한다.",
        category: "preference",
        status: "approved",
        importance: 4,
        source: "초기 설정",
        createdAt: stamped,
        updatedAt: stamped
      },
      {
        id: "mem-default-2",
        content: "비서는 주식, 여행 계획, 자산관리, 스케줄 관리를 우선 도메인으로 다룬다.",
        category: "identity",
        status: "approved",
        importance: 5,
        source: "초기 설정",
        createdAt: stamped,
        updatedAt: stamped
      }
    ],
    items: [
      {
        id: "item-default-1",
        type: "task",
        title: "비서에게 나의 투자 기준 입력",
        status: "open",
        date: "",
        notes: "예: 장기 투자, 단기 매매 회피, 현금 비중 선호, 관심 섹터",
        fields: {},
        createdAt: stamped,
        updatedAt: stamped
      },
      {
        id: "item-default-2",
        type: "schedule",
        title: "이번 주 일정 정리",
        status: "planned",
        date: "",
        notes: "중요한 회의, 마감일, 개인 약속을 입력한다.",
        fields: {},
        createdAt: stamped,
        updatedAt: stamped
      }
    ],
    messages: [
      {
        id: "msg-default-1",
        role: "assistant",
        content:
          "무엇부터 정리할까요? 주식 관심 목록, 여행 계획, 자산 현황, 이번 주 일정 중 하나를 말해주면 바로 기록하고 다음 행동으로 나누겠습니다.",
        createdAt: stamped
      }
    ]
  };
}

function ensureStore() {
  if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });
  if (!fs.existsSync(storePath)) writeStore(defaultStore());
}

function readStore() {
  ensureStore();
  const fallback = defaultStore();
  const parsed = JSON.parse(fs.readFileSync(storePath, "utf8"));
  return Object.assign({}, fallback, parsed, {
    profile: Object.assign({}, fallback.profile, parsed.profile || {}),
    memories: Array.isArray(parsed.memories) ? parsed.memories : [],
    items: Array.isArray(parsed.items) ? parsed.items : [],
    messages: Array.isArray(parsed.messages) ? parsed.messages : []
  });
}

function writeStore(store) {
  if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });
  const tempPath = storePath + "." + process.pid + "." + Date.now() + ".tmp";
  fs.writeFileSync(tempPath, JSON.stringify(store, null, 2) + "\n", "utf8");
  fs.renameSync(tempPath, storePath);
}

function save(mutator) {
  const store = readStore();
  mutator(store);
  writeStore(store);
  return store;
}

function json(res, status, payload) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store"
  });
  res.end(JSON.stringify(payload));
}

function text(res, status, payload, contentType) {
  res.writeHead(status, {
    "Content-Type": contentType + "; charset=utf-8",
    "Cache-Control": "no-store"
  });
  res.end(payload);
}

function corsJson(res, status, payload) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Accept, Authorization, Content-Type, Cache-Control, Pragma, X-Requested-With",
    "Access-Control-Allow-Private-Network": "true",
    "Access-Control-Max-Age": "600",
    "Vary": "Origin, Access-Control-Request-Headers, Access-Control-Request-Private-Network"
  });
  res.end(JSON.stringify(payload));
}

function corsText(res, status, payload, contentType) {
  res.writeHead(status, {
    "Content-Type": contentType + "; charset=utf-8",
    "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Accept, Authorization, Content-Type, Cache-Control, Pragma, X-Requested-With",
    "Access-Control-Allow-Private-Network": "true",
    "Access-Control-Max-Age": "600",
    "Vary": "Origin, Access-Control-Request-Headers, Access-Control-Request-Private-Network"
  });
  res.end(payload);
}

function readBody(req) {
  return new Promise(function (resolve, reject) {
    let body = "";
    req.on("data", function (chunk) {
      body += chunk;
      if (body.length > 1024 * 1024) {
        req.destroy();
        reject(new Error("요청이 너무 큽니다."));
      }
    });
    req.on("end", function () {
      if (!body) return resolve({});
      try {
        resolve(JSON.parse(body));
      } catch (error) {
        reject(new Error("JSON을 해석하지 못했습니다."));
      }
    });
    req.on("error", reject);
  });
}

function safeEqual(actual, expected) {
  const actualBuffer = Buffer.from(String(actual || ""));
  const expectedBuffer = Buffer.from(String(expected || ""));
  if (actualBuffer.length !== expectedBuffer.length) return false;
  return crypto.timingSafeEqual(actualBuffer, expectedBuffer);
}

function parseCookies(cookieHeader) {
  const cookies = {};
  String(cookieHeader || "")
    .split(";")
    .forEach(function (part) {
      const index = part.indexOf("=");
      if (index < 0) return;
      const key = part.slice(0, index).trim();
      const rawValue = part.slice(index + 1).trim();
      if (!key) return;
      try {
        cookies[key] = decodeURIComponent(rawValue);
      } catch (error) {
        cookies[key] = rawValue;
      }
    });
  return cookies;
}

function shareDeniedPage() {
  return [
    "<!doctype html>",
    '<html lang="ko">',
    "<head>",
    '<meta charset="utf-8" />',
    '<meta name="viewport" content="width=device-width, initial-scale=1" />',
    "<title>Digiter Twin 접근 제한</title>",
    "<style>",
    "body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f6f4ee;color:#171717}",
    "main{max-width:520px;padding:32px;line-height:1.6}",
    "h1{font-size:22px;margin:0 0 10px}",
    "p{margin:0;color:#5f5a53}",
    "</style>",
    "</head>",
    "<body>",
    "<main>",
    "<h1>공유 접근 토큰이 필요합니다.</h1>",
    "<p>서버를 공유한 사람이 제공한 전체 URL로 다시 접속하세요.</p>",
    "</main>",
    "</body>",
    "</html>"
  ].join("");
}

function authorizeShare(req, res) {
  const expectedToken = String(process.env.SHARE_TOKEN || "").trim();
  if (!expectedToken) return true;

  const parsed = url.parse(req.url, true);
  const suppliedToken = parsed.query.share_token ? String(parsed.query.share_token) : "";

  if (suppliedToken && safeEqual(suppliedToken, expectedToken)) {
    const params = new URLSearchParams();
    Object.keys(parsed.query).forEach(function (key) {
      if (key === "share_token") return;
      const value = parsed.query[key];
      if (Array.isArray(value)) value.forEach(function (entry) { params.append(key, entry); });
      else if (value !== undefined) params.append(key, value);
    });
    const cleanPath = (parsed.pathname || "/") + (params.toString() ? "?" + params.toString() : "");
    res.writeHead(302, {
      "Set-Cookie": "dt_share_token=" + encodeURIComponent(suppliedToken) + "; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400",
      "Location": cleanPath
    });
    res.end();
    return false;
  }

  const cookies = parseCookies(req.headers.cookie);
  if (safeEqual(cookies.dt_share_token, expectedToken)) return true;

  if ((parsed.pathname || "").indexOf("/api/") === 0) {
    json(res, 401, { error: "공유 접근 토큰이 필요합니다." });
    return false;
  }

  text(res, 401, shareDeniedPage(), "text/html");
  return false;
}

function fetchText(targetUrl) {
  return new Promise(function (resolve, reject) {
    const parsed = url.parse(targetUrl);
    const client = parsed.protocol === "http:" ? http : https;
    const request = client.get(
      {
        hostname: parsed.hostname,
        path: parsed.path,
        headers: {
          "User-Agent": "DigiterTwin/0.1"
        }
      },
      function (response) {
        let raw = "";
        response.on("data", function (chunk) {
          raw += chunk;
        });
        response.on("end", function () {
          if (response.statusCode < 200 || response.statusCode >= 300) {
            return reject(new Error("외부 데이터 요청 실패: " + response.statusCode));
          }
          resolve(raw);
        });
      }
    );
    request.setTimeout(8000, function () {
      request.destroy(new Error("외부 데이터 요청 시간이 초과되었습니다."));
    });
    request.on("error", reject);
  });
}

function fetchJson(targetUrl) {
  return fetchText(targetUrl).then(function (raw) {
    return JSON.parse(raw);
  });
}

function normalizeEconomicFeedRssUrl(rawUrl) {
  let target;
  try {
    target = new URL(String(rawUrl || ""));
  } catch (error) {
    throw new Error("RSS URL 형식이 올바르지 않습니다.");
  }

  if (target.protocol !== "https:") {
    throw new Error("RSS URL은 https만 허용됩니다.");
  }

  const allowed = [
    target.hostname === "news.google.com" && target.pathname === "/rss/search" && target.searchParams.get("q"),
    target.hostname === "www.cnbc.com" && /^\/id\/\d+\/device\/rss\/rss\.html$/.test(target.pathname),
    target.hostname === "feeds.finance.yahoo.com" && target.pathname === "/rss/2.0/headline" && target.searchParams.get("s"),
    target.hostname === "www.coindesk.com" && target.pathname === "/arc/outboundfeeds/rss/",
    target.hostname === "www.federalreserve.gov" && /^\/feeds\/[a-z0-9_-]+\.xml$/i.test(target.pathname),
    target.hostname === "www.yna.co.kr" && /^\/rss\/[a-z0-9_-]+\.xml$/i.test(target.pathname)
  ].some(Boolean);

  if (!allowed) {
    throw new Error("허용된 RSS URL은 등록된 경제 뉴스 공급자만 가능합니다.");
  }
  if (target.hostname === "news.google.com" && !target.searchParams.get("q")) {
    throw new Error("RSS 검색어가 필요합니다.");
  }

  return target.toString();
}

function normalizeEconomicFeedGdeltUrl(rawUrl) {
  let target;
  try {
    target = new URL(String(rawUrl || ""));
  } catch (error) {
    throw new Error("GDELT URL 형식이 올바르지 않습니다.");
  }

  if (target.protocol !== "https:" || target.hostname !== "api.gdeltproject.org" || target.pathname !== "/api/v2/doc/doc") {
    throw new Error("허용된 GDELT URL은 api.gdeltproject.org/api/v2/doc/doc 뿐입니다.");
  }
  if (!target.searchParams.get("query")) {
    throw new Error("GDELT 검색어가 필요합니다.");
  }
  if ((target.searchParams.get("mode") || "").toLowerCase() !== "artlist") {
    throw new Error("GDELT mode=ArtList 요청만 허용됩니다.");
  }
  if ((target.searchParams.get("format") || "").toLowerCase() !== "json") {
    throw new Error("GDELT format=JSON 요청만 허용됩니다.");
  }

  return target.toString();
}

function normalizeFredObservationsUrl(query) {
  const seriesId = String(query.series_id || "").trim().toUpperCase();
  const apiKey = String(query.api_key || "").trim();
  const limit = String(query.limit || "1").trim();
  const sortOrder = String(query.sort_order || "desc").trim().toLowerCase();

  if (!/^[A-Z0-9_.-]{1,40}$/.test(seriesId)) {
    throw new Error("FRED series_id 형식이 올바르지 않습니다.");
  }
  if (!/^[A-Za-z0-9]{16,64}$/.test(apiKey)) {
    throw new Error("FRED_API_KEY 형식이 올바르지 않습니다.");
  }
  if (!/^\d{1,4}$/.test(limit)) {
    throw new Error("FRED limit 형식이 올바르지 않습니다.");
  }
  if (["asc", "desc"].indexOf(sortOrder) < 0) {
    throw new Error("FRED sort_order는 asc 또는 desc만 가능합니다.");
  }

  const target = new URL("https://api.stlouisfed.org/fred/series/observations");
  target.searchParams.set("series_id", seriesId);
  target.searchParams.set("api_key", apiKey);
  target.searchParams.set("file_type", "json");
  target.searchParams.set("limit", limit);
  target.searchParams.set("sort_order", sortOrder);
  return target.toString();
}

function normalizeOpenDartCompanyUrl(query) {
  const apiKey = String(query.crtfc_key || "").trim();
  const corpCode = String(query.corp_code || "00126380").trim();

  if (!/^[A-Za-z0-9]{32,64}$/.test(apiKey)) {
    throw new Error("OpenDART API key 형식이 올바르지 않습니다.");
  }
  if (!/^\d{8}$/.test(corpCode)) {
    throw new Error("OpenDART corp_code 형식이 올바르지 않습니다.");
  }

  const target = new URL("https://opendart.fss.or.kr/api/company.json");
  target.searchParams.set("crtfc_key", apiKey);
  target.searchParams.set("corp_code", corpCode);
  return target.toString();
}

function serveStatic(req, res, pathname) {
  const target = pathname === "/" ? "/index.html" : pathname;
  const filePath = path.normalize(path.join(publicDir, target));
  if (!filePath.startsWith(publicDir)) return text(res, 403, "Forbidden", "text/plain");
  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) return text(res, 404, "Not found", "text/plain");

  const ext = path.extname(filePath);
  const contentTypes = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml"
  };
  text(res, 200, fs.readFileSync(filePath), contentTypes[ext] || "application/octet-stream");
}

function categoryFor(value) {
  if (/주식|투자|종목|포트폴리오|배당|매수|매도/.test(value)) return "finance";
  if (/자산|현금|계좌|예산|지출|저축|대출/.test(value)) return "asset";
  if (/여행|항공|호텔|숙소|동선|예약/.test(value)) return "travel";
  if (/일정|회의|약속|마감|캘린더|할 일/.test(value)) return "schedule";
  if (/좋아|싫어|선호|말투|스타일|방식/.test(value)) return "preference";
  if (/나는|내가|나의|목표|직업|역할/.test(value)) return "identity";
  return "other";
}

function scoreMemory(memory, query) {
  const haystack = (memory.content + " " + memory.category).toLowerCase();
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  let score = memory.importance || 1;
  tokens.forEach(function (token) {
    if (token.length > 1 && haystack.indexOf(token) >= 0) score += 2;
  });
  if (memory.category === "finance" && /주식|투자|종목|포트폴리오/.test(query)) score += 4;
  if (memory.category === "travel" && /여행|항공|호텔|숙소|동선/.test(query)) score += 4;
  if (memory.category === "asset" && /자산|현금|예산|저축|지출/.test(query)) score += 4;
  if (memory.category === "schedule" && /일정|회의|약속|마감|오늘|내일/.test(query)) score += 4;
  return score;
}

function scoreItem(item, query) {
  const haystack = [item.type, item.title, item.status, item.ticker || "", item.location || "", item.notes || ""].join(" ").toLowerCase();
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  let score = 0;
  tokens.forEach(function (token) {
    if (token.length > 1 && haystack.indexOf(token) >= 0) score += 2;
  });
  if (item.type === "stock" && /주식|투자|종목|포트폴리오/.test(query)) score += 4;
  if (item.type === "trip" && /여행|항공|호텔|숙소|동선/.test(query)) score += 4;
  if (item.type === "asset" && /자산|현금|예산|저축|지출/.test(query)) score += 4;
  if ((item.type === "schedule" || item.type === "task") && /일정|회의|약속|마감|오늘|내일/.test(query)) score += 4;
  return score;
}

function relevantMemories(store, query) {
  return store.memories
    .filter(function (memory) {
      return memory.status === "approved";
    })
    .map(function (memory) {
      return { memory: memory, score: scoreMemory(memory, query) };
    })
    .sort(function (a, b) {
      return b.score - a.score;
    })
    .slice(0, 8)
    .map(function (entry) {
      return entry.memory;
    });
}

function relevantItems(store, query) {
  return store.items
    .map(function (item) {
      return { item: item, score: scoreItem(item, query) };
    })
    .sort(function (a, b) {
      return b.score - a.score;
    })
    .slice(0, 10)
    .map(function (entry) {
      return entry.item;
    });
}

function itemSummary(item) {
  return [
    "[" + item.type + "] " + item.title,
    item.status ? "상태: " + item.status : "",
    item.date ? "날짜: " + item.date : "",
    item.ticker ? "티커: " + item.ticker : "",
    item.amount !== undefined && item.amount !== "" && item.amount !== null ? "금액: " + item.amount + (item.currency ? " " + item.currency : "") : "",
    item.location ? "장소: " + item.location : "",
    item.notes ? "메모: " + item.notes : ""
  ]
    .filter(Boolean)
    .join(" | ");
}

function normalizeAmount(value) {
  if (value === undefined || value === null || value === "") return undefined;
  const textValue = String(value).trim();
  if (!textValue) return undefined;
  const numberValue = Number(textValue);
  return Number.isFinite(numberValue) ? numberValue : textValue;
}

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object || {}, key);
}

function normalizeItemFields(fields) {
  if (!fields || typeof fields !== "object" || Array.isArray(fields)) return {};
  const cleaned = {};
  Object.keys(fields).forEach(function (key) {
    const value = fields[key];
    cleaned[key] = value === undefined || value === null ? "" : String(value).trim();
  });
  return cleaned;
}

function patchItem(item, body) {
  const next = Object.assign({}, item);
  if (hasOwn(body, "type") && domainTypes.indexOf(body.type) >= 0) next.type = body.type;
  if (hasOwn(body, "title")) {
    const title = String(body.title || "").trim();
    if (title) next.title = title;
  }
  if (hasOwn(body, "status")) next.status = String(body.status || "open").trim() || "open";
  if (hasOwn(body, "date")) next.date = String(body.date || "").trim();
  if (hasOwn(body, "amount")) next.amount = normalizeAmount(body.amount);
  if (hasOwn(body, "currency")) next.currency = String(body.currency || "").trim();
  if (hasOwn(body, "ticker")) next.ticker = String(body.ticker || "").trim().toUpperCase();
  if (hasOwn(body, "location")) next.location = String(body.location || "").trim();
  if (hasOwn(body, "notes")) next.notes = String(body.notes || "").trim();
  if (hasOwn(body, "fields")) next.fields = Object.assign({}, next.fields || {}, normalizeItemFields(body.fields));
  next.updatedAt = now();
  return next;
}

function parseNumber(value) {
  if (value === undefined || value === null) return null;
  const numberValue = Number(String(value).replace(/,/g, "").replace(/%/g, "").trim());
  return Number.isFinite(numberValue) ? numberValue : null;
}

function csvLine(line) {
  const values = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      values.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  values.push(current);
  return values;
}

function decodeXml(value) {
  return String(value || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/<[^>]+>/g, "")
    .trim();
}

function stockInputToNaverCode(symbol) {
  const cleaned = String(symbol || "").trim().toUpperCase();
  const match = cleaned.match(/^(\d{6})(?:\.(KS|KQ|KR))?$/);
  return match ? match[1] : "";
}

function stockInputToStooqSymbol(symbol) {
  const cleaned = String(symbol || "").trim().toUpperCase();
  if (!cleaned) return "";
  if (stockInputToNaverCode(cleaned)) return "";
  if (cleaned.indexOf(".") >= 0) return cleaned;
  return cleaned + ".US";
}

function formatStockTimestamp(date, time) {
  return [date, time].filter(Boolean).join(" ").trim();
}

async function fetchNaverQuote(symbol) {
  const code = stockInputToNaverCode(symbol);
  const payload = await fetchJson("https://m.stock.naver.com/api/stock/" + code + "/basic");
  const price = parseNumber(payload.closePrice);
  if (price === null) throw new Error("국내 종목 가격을 찾지 못했습니다.");
  const change = parseNumber(payload.compareToPreviousClosePrice);
  const changePercent = parseNumber(payload.fluctuationsRatio);
  return {
    inputSymbol: symbol,
    symbol: code,
    displaySymbol: code,
    name: payload.stockName || code,
    exchange: payload.stockExchangeName || "KR",
    currency: "KRW",
    price: price,
    previousClose: null,
    change: change,
    changePercent: changePercent,
    open: null,
    high: null,
    low: null,
    volume: parseNumber(payload.accumulatedTradingVolume),
    marketStatus: payload.marketStatus || "",
    asOf: payload.localTradedAt || "",
    source: "Naver Finance"
  };
}

async function fetchStooqQuote(symbol) {
  const stooqSymbol = stockInputToStooqSymbol(symbol);
  const raw = await fetchText(
    "https://stooq.com/q/l/?s=" + encodeURIComponent(stooqSymbol.toLowerCase()) + "&f=sd2t2ohlcvpn&h&e=csv"
  );
  const rows = raw.trim().split(/\r?\n/);
  if (rows.length < 2) throw new Error("해외 종목 가격을 찾지 못했습니다.");
  const header = csvLine(rows[0]);
  const values = csvLine(rows[1]);
  const row = {};
  header.forEach(function (key, index) {
    row[key] = values[index];
  });
  const close = parseNumber(row.Close);
  if (close === null) throw new Error("해외 종목 가격을 찾지 못했습니다. 미국 종목은 AAPL, TSLA처럼 입력하거나 거래소 접미사를 붙여 주세요.");
  const previousClose = parseNumber(row.Prev);
  const change = previousClose === null ? null : close - previousClose;
  const changePercent = previousClose === null || previousClose === 0 ? null : (change / previousClose) * 100;
  return {
    inputSymbol: symbol,
    symbol: row.Symbol || stooqSymbol,
    displaySymbol: String(row.Symbol || stooqSymbol).replace(/\.US$/i, ""),
    name: row.Name || String(symbol).toUpperCase(),
    exchange: String(row.Symbol || stooqSymbol).split(".")[1] || "US",
    currency: "USD",
    price: close,
    previousClose: previousClose,
    change: change,
    changePercent: changePercent,
    open: parseNumber(row.Open),
    high: parseNumber(row.High),
    low: parseNumber(row.Low),
    volume: parseNumber(row.Volume),
    marketStatus: "DELAYED",
    asOf: formatStockTimestamp(row.Date, row.Time),
    source: "Stooq"
  };
}

async function fetchQuote(symbol) {
  if (stockInputToNaverCode(symbol)) return fetchNaverQuote(symbol);
  return fetchStooqQuote(symbol);
}

async function fetchStockNews(symbol, companyName) {
  const query = encodeURIComponent((companyName || symbol) + " " + symbol + " 주가 stock when:14d");
  const raw = await fetchText("https://news.google.com/rss/search?q=" + query + "&hl=ko&gl=KR&ceid=KR:ko");
  const items = [];
  const blocks = raw.match(/<item>[\s\S]*?<\/item>/g) || [];
  blocks.slice(0, 6).forEach(function (block) {
    const title = decodeXml((block.match(/<title>([\s\S]*?)<\/title>/) || [])[1]);
    const link = decodeXml((block.match(/<link>([\s\S]*?)<\/link>/) || [])[1]);
    const pubDate = decodeXml((block.match(/<pubDate>([\s\S]*?)<\/pubDate>/) || [])[1]);
    const source = decodeXml((block.match(/<source[^>]*>([\s\S]*?)<\/source>/) || [])[1]);
    if (title && link) {
      items.push({
        title: title,
        url: link,
        source: source || "Google News",
        publishedAt: pubDate
      });
    }
  });
  return items;
}

async function stockSnapshot(symbol) {
  const cleanSymbol = String(symbol || "").trim();
  try {
    const quote = await fetchQuote(cleanSymbol);
    const news = await fetchStockNews(quote.displaySymbol || cleanSymbol, quote.name);
    return {
      inputSymbol: cleanSymbol,
      quote: quote,
      news: news,
      error: ""
    };
  } catch (error) {
    let news = [];
    try {
      news = await fetchStockNews(cleanSymbol, cleanSymbol);
    } catch (ignored) {
      news = [];
    }
    return {
      inputSymbol: cleanSymbol,
      quote: null,
      news: news,
      error: error.message || "종목 정보를 가져오지 못했습니다."
    };
  }
}

function buildContext(store, query) {
  const memories = relevantMemories(store, query);
  const items = relevantItems(store, query);
  const profile = store.profile;
  const lines = [
    "사용자 프로필",
    "- 이름: " + profile.ownerName,
    "- 비서 이름: " + profile.assistantName,
    "- 언어: " + profile.preferredLanguage,
    "- 답변 방식: " + profile.answerStyle,
    "- 말투: " + profile.tone,
    "- 의사결정 방식: " + profile.decisionStyle,
    "- 투자 기준: " + profile.financePolicy,
    "- 여행 기준: " + profile.travelPolicy,
    "- 일정 기준: " + profile.schedulePolicy,
    "- 자산 기준: " + profile.assetPolicy,
    "- 경계: " + profile.boundaries,
    "",
    "관련 기억"
  ];
  if (memories.length) memories.forEach(function (memory) { lines.push("- " + memory.content); });
  else lines.push("- 없음");
  lines.push("", "관련 기록");
  if (items.length) items.forEach(function (item) { lines.push("- " + itemSummary(item)); });
  else lines.push("- 없음");
  lines.push("", "최근 대화");
  store.messages.slice(-8).forEach(function (message) {
    lines.push("- " + message.role + ": " + message.content);
  });
  return { memories: memories, items: items, text: lines.join("\n") };
}

function fallbackReply(store, query) {
  const context = buildContext(store, query);
  const memoryLine = context.memories.length ? "참고한 기억은 " + context.memories.length + "개입니다." : "아직 참고할 승인된 기억이 거의 없습니다.";
  const itemLine = context.items.length ? "관련 기록 " + context.items.length + "개를 함께 보겠습니다." : "관련 기록은 아직 부족합니다.";
  return [
    store.profile.assistantName + "입니다. " + memoryLine + " " + itemLine,
    "",
    "지금 바로 처리하려면 아래 중 하나로 말해 주세요.",
    "- 주식: 관심 종목, 보유 수량, 판단 기준",
    "- 여행: 목적지, 날짜, 예산, 동행자, 선호 동선",
    "- 자산: 현금/투자/부채 요약, 월 저축액, 목표",
    "- 일정: 오늘 할 일, 마감일, 고정 약속",
    "",
    "현재 요청: " + query
  ].join("\n");
}

const ragExtensions = {
  ".md": true,
  ".txt": true,
  ".js": true,
  ".css": true,
  ".html": true,
  ".json": true
};

const ragExcludedDirs = {
  ".git": true,
  ".next": true,
  "node_modules": true,
  "data": true,
  "dist": true,
  "build": true
};

function walkTextFiles(dir, results) {
  let entries = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (error) {
    return results;
  }

  entries.forEach(function (entry) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!ragExcludedDirs[entry.name]) walkTextFiles(fullPath, results);
      return;
    }

    if (!entry.isFile()) return;
    if (entry.name.indexOf(".env") === 0 || entry.name === "package-lock.json") return;
    if (!ragExtensions[path.extname(entry.name)]) return;

    results.push(fullPath);
  });

  return results;
}

function queryTerms(query) {
  return String(query || "")
    .toLowerCase()
    .replace(/[^\w가-힣.\-]+/g, " ")
    .split(/\s+/)
    .filter(function (term) {
      return term.length >= 2;
    });
}

function scoreText(filePath, content, terms) {
  const lowerPath = filePath.toLowerCase();
  const lowerContent = content.toLowerCase();
  let score = 0;
  terms.forEach(function (term) {
    const pathHit = lowerPath.indexOf(term) >= 0;
    const contentHit = lowerContent.indexOf(term) >= 0;
    if (pathHit) score += 8;
    if (contentHit) score += 2;
  });
  if (/주식|stock|시세|뉴스|증권/.test(lowerContent)) score += /주식|stock|시세|뉴스|증권/.test(String(terms.join(" "))) ? 4 : 0;
  return score;
}

function bestSnippet(content, terms) {
  const lower = content.toLowerCase();
  let index = -1;
  for (let termIndex = 0; termIndex < terms.length; termIndex += 1) {
    index = lower.indexOf(terms[termIndex]);
    if (index >= 0) break;
  }
  if (index < 0) index = 0;
  const start = Math.max(0, index - 260);
  const end = Math.min(content.length, index + 620);
  return content
    .slice(start, end)
    .replace(/\s+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function buildRagSnippets(query) {
  const terms = queryTerms(query);
  const files = walkTextFiles(rootDir, []);
  const scored = [];

  files.forEach(function (filePath) {
    let stat;
    try {
      stat = fs.statSync(filePath);
    } catch (error) {
      return;
    }
    if (stat.size > 350 * 1024) return;

    let content = "";
    try {
      content = fs.readFileSync(filePath, "utf8");
    } catch (error) {
      return;
    }

    const score = terms.length ? scoreText(path.relative(rootDir, filePath), content, terms) : 1;
    if (score <= 0 && scored.length > 0) return;
    scored.push({
      filePath: path.relative(rootDir, filePath),
      score: score,
      snippet: bestSnippet(content, terms)
    });
  });

  return scored
    .sort(function (a, b) {
      return b.score - a.score;
    })
    .slice(0, 8);
}

function buildCodexPrompt(store, query) {
  const context = buildContext(store, query);
  const snippets = buildRagSnippets(query);
  const ragText = snippets.length
    ? snippets
        .map(function (entry, index) {
          return "[" + (index + 1) + "] " + entry.filePath + "\n" + entry.snippet;
        })
        .join("\n\n---\n\n")
    : "검색된 로컬 문서가 없습니다.";

  return [
    "너는 Digiter Twin 웹앱의 로컬 Codex 비서 백엔드다.",
    "사용자 질문에 한국어로 답하라. 사용자의 기억, 프로필, 관심 종목, 로컬 RAG 문서를 우선 근거로 사용한다.",
    "파일을 수정하지 말고, 코드 변경 제안이 필요하면 설명만 하라.",
    "투자 관련 답변은 매수/매도 단정 대신 확인할 데이터, 리스크, 다음 행동으로 나눠라.",
    "모르는 최신 정보는 확정하지 말고 확인 필요라고 말하라.",
    "답변은 사용자에게 바로 보여줄 최종 답변만 작성하라.",
    "",
    "사용자 질문:",
    query,
    "",
    "개인 비서 컨텍스트:",
    context.text,
    "",
    "로컬 RAG 문서:",
    ragText
  ].join("\n");
}

function runLocalCodex(prompt) {
  return new Promise(function (resolve, reject) {
    const outputPath = path.join(os.tmpdir(), "digiter-twin-codex-" + process.pid + "-" + Date.now() + ".txt");
    const args = [
      "-a",
      "never",
      "--sandbox",
      "read-only",
      "--cd",
      rootDir,
      "exec",
      "--skip-git-repo-check",
      "--ephemeral",
      "--output-last-message",
      outputPath,
      "-"
    ];

    const child = childProcess.spawn(codexPath, args, {
      cwd: rootDir,
      stdio: ["pipe", "pipe", "pipe"],
      env: Object.assign({}, process.env, {
        NO_COLOR: "1"
      })
    });

    let stderr = "";
    const timer = setTimeout(function () {
      child.kill("SIGTERM");
      reject(new Error("로컬 Codex 응답 시간이 초과되었습니다."));
    }, Number(process.env.CODEX_TIMEOUT_MS || 90000));

    child.stdout.on("data", function () {
      // Codex writes progress to stdout; the final response is read from outputPath.
    });

    child.stderr.on("data", function (chunk) {
      stderr += chunk.toString();
    });

    child.on("error", function (error) {
      clearTimeout(timer);
      reject(error);
    });

    child.on("close", function (code) {
      clearTimeout(timer);
      if (code !== 0) {
        return reject(new Error(stderr.trim() || "로컬 Codex 실행 실패"));
      }

      try {
        const output = fs.readFileSync(outputPath, "utf8").trim();
        fs.unlinkSync(outputPath);
        resolve(output);
      } catch (error) {
        reject(error);
      }
    });

    child.stdin.write(prompt);
    child.stdin.end();
  });
}

async function askLocalCodex(store, message) {
  if (process.env.LOCAL_CODEX_ENABLED === "0") return null;
  const prompt = buildCodexPrompt(store, message);
  const reply = await runLocalCodex(prompt);
  return reply && reply.trim() ? reply.trim() : null;
}

function localMemoryCandidate(message) {
  const trimmed = String(message || "").trim();
  if (trimmed.length < 12) return [];
  const signals = [
    "나는",
    "내가",
    "나의",
    "선호",
    "좋아",
    "싫어",
    "원해",
    "중요",
    "성향",
    "스타일",
    "방식",
    "투자",
    "여행",
    "일정",
    "자산",
    "목표"
  ];
  const matched = signals.some(function (signal) {
    return trimmed.indexOf(signal) >= 0;
  });
  if (!matched) return [];

  if (/기억에 저장|기억해줘|저장해줘/.test(trimmed) && trimmed.length < 40) return [];

  const preferenceCategory = /투자|주식|종목|포트폴리오|리스크|장기|단기/.test(trimmed)
    ? "finance"
    : /여행|숙소|호텔|항공|동선|일정이 빡빡|무리/.test(trimmed)
      ? "travel"
      : /자산|현금|저축|지출|예산|부채/.test(trimmed)
        ? "asset"
        : /일정|스케줄|회의|약속|마감|할 일/.test(trimmed)
          ? "schedule"
          : categoryFor(trimmed);

  const normalized = trimmed
    .replace(/^나는\s*/, "")
    .replace(/^내가\s*/, "")
    .replace(/^나의\s*/, "")
    .trim();

  return [
    {
      content: ("사용자는 " + normalized).slice(0, 180),
      category: preferenceCategory,
      importance: /선호|싫어|좋아|중요|원해|성향|방식|스타일/.test(trimmed) ? 4 : 3
    }
  ];
}

function memoryFingerprint(content) {
  return String(content || "")
    .toLowerCase()
    .replace(/\s+/g, "")
    .replace(/[.,!?'"`~:;()[\]{}<>]/g, "")
    .replace(/^사용자는/, "");
}

function isDuplicateMemory(memories, content, category) {
  const next = memoryFingerprint(content);
  if (!next) return true;
  return memories.some(function (memory) {
    if (memory.status === "archived") return false;
    if (category && memory.category !== category) return false;
    const existing = memoryFingerprint(memory.content);
    return existing === next || existing.indexOf(next) >= 0 || next.indexOf(existing) >= 0;
  });
}

function persistCandidates(candidates, options) {
  const saveStatus = options && options.status ? options.status : "approved";
  const source = options && options.source ? options.source : "conversation";
  const saved = [];
  if (!Array.isArray(candidates) || !candidates.length) return saved;
  save(function (draft) {
    candidates.slice(0, 3).forEach(function (candidate) {
      const content = String(candidate.content || "").trim();
      if (!content || content.length < 5) return;
      const stamped = now();
      const category = memoryCategories.indexOf(candidate.category) >= 0 ? candidate.category : categoryFor(content);
      if (isDuplicateMemory(draft.memories, content, category)) return;
      const memory = {
        id: id("mem"),
        content: content,
        category: category,
        status: saveStatus,
        importance: Math.min(Math.max(Number(candidate.importance || 3), 1), 5),
        source: source,
        createdAt: stamped,
        updatedAt: stamped
      };
      draft.memories.unshift(memory);
      saved.push(memory);
    });
  });
  return saved;
}

function appendMessage(role, content) {
  save(function (store) {
    store.messages.push({
      id: id("msg"),
      role: role,
      content: content,
      createdAt: now()
    });
    store.messages = store.messages.slice(-80);
  });
}

function systemPrompt(contextText) {
  return [
    "너는 사용자의 개인 비서다. 사용자의 말투와 기준을 닮되, 사용자인 척하지 않는다.",
    "주요 업무는 주식 관심 목록 정리, 여행 계획, 자산관리 메모, 스케줄 관리다.",
    "투자 조언은 단정하지 말고 리스크, 확인할 데이터, 선택지, 다음 행동으로 나눠라.",
    "여행 계획은 날짜, 예산, 이동 동선, 예약 필요 항목, 피로도를 함께 고려하라.",
    "자산관리는 민감정보를 요구하지 말고 요약, 목표, 현금흐름, 리스크 중심으로 돕는다.",
    "일정관리는 오늘 할 일, 마감일, 의존성, 위임/보류 항목을 분리한다.",
    "모르는 최신 정보나 시세는 안다고 꾸미지 말고 확인 필요라고 말한다.",
    "응답은 한국어로 한다.",
    "반드시 JSON만 반환한다. 스키마: {\"reply\":\"사용자에게 보여줄 답변\",\"memoryCandidates\":[{\"content\":\"기억 후보\",\"category\":\"identity|preference|finance|travel|asset|schedule|work|other\",\"importance\":1-5}]}",
    "기억 후보는 사용자의 장기 선호, 기준, 목표, 반복될 계획만 만든다. 일회성 잡담은 제외한다.",
    "",
    contextText
  ].join("\n");
}

function postOpenAI(payload) {
  const apiKey = process.env.OPENAI_API_KEY;
  return new Promise(function (resolve, reject) {
    const body = JSON.stringify(payload);
    const req = https.request(
      {
        hostname: "api.openai.com",
        path: "/v1/responses",
        method: "POST",
        headers: {
          Authorization: "Bearer " + apiKey,
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body)
        }
      },
      function (res) {
        let raw = "";
        res.on("data", function (chunk) {
          raw += chunk;
        });
        res.on("end", function () {
          let parsed;
          try {
            parsed = JSON.parse(raw);
          } catch (error) {
            return reject(new Error("OpenAI 응답을 해석하지 못했습니다."));
          }
          if (res.statusCode < 200 || res.statusCode >= 300) {
            return reject(new Error(parsed.error && parsed.error.message ? parsed.error.message : "OpenAI 요청 실패"));
          }
          resolve(parsed);
        });
      }
    );
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

function extractOutputText(response) {
  if (response.output_text) return response.output_text;
  const output = Array.isArray(response.output) ? response.output : [];
  const parts = [];
  output.forEach(function (item) {
    const content = Array.isArray(item.content) ? item.content : [];
    content.forEach(function (part) {
      if (part.text) parts.push(part.text);
    });
  });
  return parts.join("\n");
}

function parseAssistantJson(textValue) {
  const raw = String(textValue || "").trim();
  const fenced = raw.match(/```json\s*([\s\S]*?)```/i);
  const candidate = fenced ? fenced[1] : raw;
  try {
    return JSON.parse(candidate);
  } catch (error) {
    const first = candidate.indexOf("{");
    const last = candidate.lastIndexOf("}");
    if (first >= 0 && last > first) {
      try {
        return JSON.parse(candidate.slice(first, last + 1));
      } catch (inner) {
        return null;
      }
    }
  }
  return null;
}

async function askAssistant(message) {
  const store = readStore();
  const trimmed = String(message || "").trim();
  appendMessage("user", trimmed);
  let codexError = "";

  try {
    const codexReply = await askLocalCodex(store, trimmed);
    if (codexReply) {
      const memoryCandidates = persistCandidates(localMemoryCandidate(trimmed), {
        status: "approved",
        source: "conversation"
      });
      appendMessage("assistant", codexReply);
      return { reply: codexReply, memoryCandidates: memoryCandidates, usedFallback: false, engine: "codex" };
    }
  } catch (error) {
    codexError = error.message || "로컬 Codex 실행 실패";
  }

  if (!process.env.OPENAI_API_KEY) {
    const reply = codexError ? fallbackReply(store, trimmed) + "\n\n로컬 Codex 오류: " + codexError : fallbackReply(store, trimmed);
    const memoryCandidates = persistCandidates(localMemoryCandidate(trimmed), {
      status: "approved",
      source: "conversation"
    });
    appendMessage("assistant", reply);
    return { reply: reply, memoryCandidates: memoryCandidates, usedFallback: true };
  }

  try {
    const context = buildContext(store, trimmed);
    const response = await postOpenAI({
      model: process.env.OPENAI_MODEL || "gpt-5.5",
      instructions: systemPrompt(context.text),
      input: trimmed
    });
    const outputText = extractOutputText(response);
    const parsed = parseAssistantJson(outputText);
    const reply = parsed && parsed.reply ? String(parsed.reply).trim() : outputText.trim();
    const candidates = parsed && Array.isArray(parsed.memoryCandidates) ? parsed.memoryCandidates : localMemoryCandidate(trimmed);
    const memoryCandidates = persistCandidates(candidates, {
      status: "approved",
      source: "conversation"
    });
    const finalReply = reply || fallbackReply(store, trimmed);
    appendMessage("assistant", finalReply);
    return { reply: finalReply, memoryCandidates: memoryCandidates, usedFallback: false };
  } catch (error) {
    const reply = [
      "OpenAI 응답을 받지 못했습니다.",
      "",
      fallbackReply(store, trimmed),
      "",
      "오류: " + error.message
    ].join("\n");
    const memoryCandidates = persistCandidates(localMemoryCandidate(trimmed), {
      status: "approved",
      source: "conversation"
    });
    appendMessage("assistant", reply);
    return { reply: reply, memoryCandidates: memoryCandidates, usedFallback: true };
  }
}

function snapshot() {
  const store = readStore();
  return {
    profile: store.profile,
    memories: store.memories,
    items: store.items,
    messages: store.messages
  };
}

function requestExternalJson(method, targetUrl, options) {
  options = options || {};
  return new Promise(function (resolve, reject) {
    const parsed = url.parse(targetUrl);
    const client = parsed.protocol === "http:" ? http : https;
    const body = options.body || "";
    const headers = Object.assign(
      {
        "Accept": "application/json",
        "User-Agent": "DigiterTwin-FlowLens/0.1"
      },
      options.headers || {}
    );
    if (body) headers["Content-Length"] = Buffer.byteLength(body);

    const request = client.request(
      {
        method: method,
        hostname: parsed.hostname,
        path: parsed.path,
        headers: headers,
        timeout: options.timeout || 7000
      },
      function (response) {
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
            return reject(new Error("외부 JSON 응답을 해석하지 못했습니다."));
          }
          if (response.statusCode < 200 || response.statusCode >= 300) {
            return reject(new Error("외부 API 응답 오류: HTTP " + response.statusCode));
          }
          resolve({ statusCode: response.statusCode, headers: response.headers, payload: payload });
        });
      }
    );
    request.setTimeout(options.timeout || 7000, function () {
      request.destroy(new Error("외부 API 요청 시간이 초과되었습니다."));
    });
    request.on("timeout", function () {
      request.destroy(new Error("외부 API 요청 시간이 초과되었습니다."));
    });
    request.on("error", reject);
    if (body) request.write(body);
    request.end();
  });
}

function formBody(values) {
  const params = new URLSearchParams();
  Object.keys(values).forEach(function (key) {
    params.set(key, values[key]);
  });
  return params.toString();
}

function decimalNumber(value) {
  if (value == null) return 0;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.replace(/,/g, ""));
    return Number.isFinite(parsed) ? parsed : 0;
  }
  if (typeof value === "object") {
    return decimalNumber(value.amount || value.value || value.krw || value.usd);
  }
  return 0;
}

function demoTossPortfolio(reason) {
  return {
    mode: "demo",
    configured: false,
    status: reason || "토스 credentials 미설정",
    account: {
      displayNumber: "demo",
      type: "BROKERAGE"
    },
    positions: [
      {
        symbol: "005930",
        name: "삼성전자",
        market: "KR",
        currency: "KRW",
        quantity: "12",
        marketValue: 864000,
        profitLoss: 84000,
        sector: "반도체"
      },
      {
        symbol: "AAPL",
        name: "Apple",
        market: "US",
        currency: "USD",
        quantity: "2",
        marketValue: 486.2,
        profitLoss: 66.2,
        sector: "AI 디바이스"
      },
      {
        symbol: "CASH",
        name: "대기 현금",
        market: "CASH",
        currency: "KRW",
        quantity: "1",
        marketValue: 1250000,
        profitLoss: 0,
        sector: "현금"
      }
    ]
  };
}

function normalizeTossAccounts(payload) {
  const data = payload && (payload.data || payload.result || payload);
  const accounts = data && (data.accounts || data.items || data);
  return Array.isArray(accounts) ? accounts : [];
}

function normalizeTossHoldings(payload) {
  const data = payload && (payload.data || payload.result || payload);
  const overview = data && (data.overview || data.holdings || data);
  const items = overview && (overview.items || overview.holdings || overview.positions || overview);
  return Array.isArray(items) ? items : [];
}

function normalizeTossPosition(item) {
  const marketValue = decimalNumber(item.marketValue) || decimalNumber(item.evaluationAmount);
  const profitLoss = decimalNumber(item.profitLoss) || decimalNumber(item.unrealizedProfitLoss);
  return {
    symbol: String(item.symbol || item.stockCode || item.code || ""),
    name: String(item.name || item.stockName || item.symbol || "보유 종목"),
    market: String(item.marketCountry || item.market || ""),
    currency: String(item.currency || ""),
    quantity: String(item.quantity || item.qty || ""),
    marketValue: marketValue,
    profitLoss: profitLoss,
    sector: sectorFromSymbol(String(item.symbol || item.stockCode || item.name || ""))
  };
}

function sectorFromSymbol(value) {
  const normalized = value.toUpperCase();
  if (/005930|000660|NVDA|AMD|TSM|반도체|CHIP|SEMICONDUCTOR/.test(normalized)) return "반도체";
  if (/AAPL|MSFT|GOOGL|META|AMZN|AI|SOFTWARE/.test(normalized)) return "AI/플랫폼";
  if (/^(TSLA|RIVN|GM|F)$/.test(normalized) || /EV|BATTERY|AUTO|전기차/.test(normalized)) return "모빌리티";
  if (/CASH|USD|KRW|현금/.test(normalized)) return "현금";
  if (/BTC|ETH|COIN|CRYPTO/.test(normalized)) return "디지털자산";
  return "기타";
}

function knownStockInfo(symbol) {
  const normalized = String(symbol || "").trim().toUpperCase();
  const map = {
    "005930": { name: "삼성전자", market: "KR", currency: "KRW", sector: "반도체" },
    "000660": { name: "SK하이닉스", market: "KR", currency: "KRW", sector: "반도체" },
    AAPL: { name: "Apple", market: "US", currency: "USD", sector: "AI/플랫폼" },
    MSFT: { name: "Microsoft", market: "US", currency: "USD", sector: "AI/플랫폼" },
    NVDA: { name: "NVIDIA", market: "US", currency: "USD", sector: "반도체" },
    AMD: { name: "AMD", market: "US", currency: "USD", sector: "반도체" },
    TSLA: { name: "Tesla", market: "US", currency: "USD", sector: "모빌리티" },
    GOOGL: { name: "Alphabet", market: "US", currency: "USD", sector: "AI/플랫폼" },
    META: { name: "Meta", market: "US", currency: "USD", sector: "AI/플랫폼" }
  };
  return Object.assign(
    {
      symbol: normalized,
      name: normalized || "관심 종목",
      market: "",
      currency: "",
      sector: sectorFromSymbol(normalized)
    },
    map[normalized] || {}
  );
}

async function fetchTossPortfolio() {
  const baseUrl = String(process.env.TOSS_API_BASE_URL || "https://openapi.tossinvest.com").replace(/\/+$/, "");
  const clientId = String(process.env.TOSS_CLIENT_ID || "").trim();
  const clientSecret = String(process.env.TOSS_CLIENT_SECRET || "").trim();
  const forcedAccountSeq = String(process.env.TOSS_ACCOUNT_SEQ || "").trim();

  if (!clientId || !clientSecret) {
    return demoTossPortfolio();
  }

  try {
    const tokenResponse = await requestExternalJson("POST", baseUrl + "/oauth2/token", {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formBody({
        grant_type: "client_credentials",
        client_id: clientId,
        client_secret: clientSecret
      })
    });
    const token = tokenResponse.payload && tokenResponse.payload.access_token;
    if (!token) throw new Error("토스 access_token이 없습니다.");

    const accountsResponse = await requestExternalJson("GET", baseUrl + "/api/v1/accounts", {
      headers: { "Authorization": "Bearer " + token }
    });
    const accounts = normalizeTossAccounts(accountsResponse.payload);
    const account = accounts[0] || {};
    const accountSeq = forcedAccountSeq || String(account.accountSeq || account.id || "");
    if (!accountSeq) {
      return {
        mode: "live",
        configured: true,
        status: "계좌 식별값 없음",
        account: { displayNumber: maskAccount(account.accountNo || ""), type: account.accountType || "" },
        positions: []
      };
    }

    const holdingsResponse = await requestExternalJson("GET", baseUrl + "/api/v1/holdings", {
      headers: {
        "Authorization": "Bearer " + token,
        "X-Tossinvest-Account": accountSeq
      }
    });
    const positions = normalizeTossHoldings(holdingsResponse.payload).map(normalizeTossPosition);
    return {
      mode: "live",
      configured: true,
      status: "토스 계좌 동기화",
      account: {
        displayNumber: maskAccount(account.accountNo || accountSeq),
        type: account.accountType || "BROKERAGE"
      },
      positions: positions
    };
  } catch (error) {
    return demoTossPortfolio("토스 조회 실패 · " + error.message);
  }
}

function maskAccount(value) {
  const textValue = String(value || "");
  if (!textValue) return "연결 계좌";
  return "****" + textValue.slice(-4);
}

function demoNewsItems(reason) {
  const stamped = now();
  return [
    {
      title: "AI 반도체 투자와 전력 인프라 지출이 성장주 흐름을 좌우",
      source: "demo",
      url: "",
      publishedAt: stamped,
      summary: "AI CAPEX와 전력망 증설 이슈가 반도체와 데이터센터 밸류체인을 동시에 움직입니다."
    },
    {
      title: "달러와 금리가 재상승하면 위험자산 포지션 크기 조절 필요",
      source: "demo",
      url: "",
      publishedAt: stamped,
      summary: "환율과 금리 변동은 해외 주식 평가액과 신규 매수 여력을 동시에 흔듭니다."
    },
    {
      title: "한국 증시는 반도체 수급과 외국인 매수 지속 여부가 핵심",
      source: "demo",
      url: "",
      publishedAt: stamped,
      summary: "국내 포트폴리오는 반도체 집중도가 높을수록 외국인 수급 뉴스 민감도가 커집니다."
    }
  ].map(function (item) {
    item.reason = reason || "뉴스 fallback";
    return item;
  });
}

function demoSocialPosts(reason) {
  const stamped = now();
  return [
    {
      id: "social-demo-ai",
      author: "market_signal",
      source: "demo",
      text: "AI capex commentary is still driving chip names, but watch whether power-grid bottlenecks cap the next leg.",
      url: "",
      createdAt: stamped,
      metrics: { reposts: 18, replies: 7, likes: 96, quotes: 4 },
      reason: reason || "social fallback"
    },
    {
      id: "social-demo-rates",
      author: "macro_watch",
      source: "demo",
      text: "Dollar strength and front-end yields are the two tells for whether risk appetite can hold into the US session.",
      url: "",
      createdAt: stamped,
      metrics: { reposts: 9, replies: 3, likes: 41, quotes: 2 },
      reason: reason || "social fallback"
    },
    {
      id: "social-demo-korea",
      author: "seoul_flow",
      source: "demo",
      text: "KOSPI flow still looks tied to foreign buying in semis. If that pauses, cash buffer matters more than beta.",
      url: "",
      createdAt: stamped,
      metrics: { reposts: 12, replies: 5, likes: 54, quotes: 3 },
      reason: reason || "social fallback"
    }
  ];
}

async function fetchFlowNews() {
  const target = new URL("https://api.gdeltproject.org/api/v2/doc/doc");
  target.searchParams.set("query", "(market OR stocks OR semiconductor OR \"Federal Reserve\" OR Korea OR dollar OR bonds) sourcelang:english");
  target.searchParams.set("mode", "ArtList");
  target.searchParams.set("format", "JSON");
  target.searchParams.set("maxrecords", "12");
  target.searchParams.set("sort", "DateDesc");

  try {
    const response = await requestExternalJson("GET", target.toString(), {
      timeout: 2500
    });
    const payload = response.payload;
    const articles = Array.isArray(payload.articles) ? payload.articles : [];
    const items = articles.slice(0, 12).map(function (article) {
      return {
        title: String(article.title || "Untitled"),
        source: String(article.domain || "GDELT"),
        url: String(article.url || ""),
        publishedAt: String(article.seendate || ""),
        summary: String(article.title || "")
      };
    }).filter(function (item) {
      return item.title && item.url;
    });
    if (!items.length) return demoNewsItems("GDELT 기사 없음");
    return items;
  } catch (error) {
    return demoNewsItems("뉴스 조회 실패 · " + error.message);
  }
}

function normalizeXPosts(payload) {
  const tweets = Array.isArray(payload.data) ? payload.data : [];
  const users = {};
  const includedUsers = payload.includes && Array.isArray(payload.includes.users)
    ? payload.includes.users
    : [];
  includedUsers.forEach(function (user) {
    users[String(user.id)] = user;
  });

  return tweets.map(function (post) {
    const user = users[String(post.author_id)] || {};
    const username = user.username || post.author_id || "x";
    const metrics = post.public_metrics || {};
    return {
      id: String(post.id || ""),
      author: String(username),
      source: "X",
      text: String(post.text || ""),
      url: username && post.id ? "https://x.com/" + encodeURIComponent(username) + "/status/" + encodeURIComponent(post.id) : "",
      createdAt: String(post.created_at || ""),
      metrics: {
        reposts: Number(metrics.retweet_count || 0),
        replies: Number(metrics.reply_count || 0),
        likes: Number(metrics.like_count || 0),
        quotes: Number(metrics.quote_count || 0)
      }
    };
  }).filter(function (post) {
    return post.id && post.text;
  });
}

async function fetchSocialPosts() {
  const bearerToken = String(process.env.X_BEARER_TOKEN || "").trim();
  const query = String(process.env.X_SEARCH_QUERY || "(market OR stocks OR semiconductor OR Fed OR KOSPI OR dollar OR AI) -is:retweet lang:en").trim();

  if (!bearerToken) {
    return demoSocialPosts("X_BEARER_TOKEN 미설정");
  }

  try {
    const target = new URL("https://api.x.com/2/tweets/search/recent");
    target.searchParams.set("query", query);
    target.searchParams.set("max_results", "10");
    target.searchParams.set("tweet.fields", "created_at,public_metrics,lang,author_id");
    target.searchParams.set("expansions", "author_id");
    target.searchParams.set("user.fields", "username,name");

    const response = await requestExternalJson("GET", target.toString(), {
      timeout: 2500,
      headers: {
        "Authorization": "Bearer " + bearerToken
      }
    });
    const posts = normalizeXPosts(response.payload);
    if (!posts.length) return demoSocialPosts("X 검색 결과 없음");
    return posts;
  } catch (error) {
    return demoSocialPosts("X 조회 실패 · " + error.message);
  }
}

function socialPostsAsSignals(posts) {
  return (posts || []).map(function (post) {
    return {
      title: post.text,
      summary: post.text,
      source: post.source + " @" + post.author,
      url: post.url,
      publishedAt: post.createdAt,
      signalType: "social"
    };
  });
}

function analyzeThemes(newsItems, socialPosts) {
  const signals = newsItems.concat(socialPostsAsSignals(socialPosts));
  const themeDefs = [
    { id: "ai", label: "AI/반도체", color: "green", keywords: ["ai", "chip", "semiconductor", "nvidia", "data center", "반도체", "삼성", "hynix"] },
    { id: "rates", label: "금리/달러", color: "blue", keywords: ["fed", "rate", "yield", "bond", "dollar", "inflation", "금리", "달러"] },
    { id: "korea", label: "한국/수급", color: "amber", keywords: ["korea", "kospi", "krw", "seoul", "한국", "코스피", "외국인"] },
    { id: "risk", label: "리스크", color: "red", keywords: ["war", "tariff", "risk", "selloff", "volatility", "oil", "위험", "관세"] },
    { id: "crypto", label: "코인/유동성", color: "violet", keywords: ["bitcoin", "crypto", "stablecoin", "token", "ethereum", "코인", "비트코인"] }
  ];

  return themeDefs.map(function (theme) {
    const matches = signals.filter(function (item) {
      const haystack = (item.title + " " + item.summary + " " + item.source).toLowerCase();
      return theme.keywords.some(function (keyword) {
        return haystack.indexOf(keyword.toLowerCase()) >= 0;
      });
    });
    return {
      id: theme.id,
      label: theme.label,
      color: theme.color,
      count: matches.length,
      socialCount: matches.filter(function (item) { return item.signalType === "social"; }).length,
      headline: matches[0] ? matches[0].title : "관련 헤드라인 대기",
      weight: Math.min(100, matches.length * 28)
    };
  }).sort(function (a, b) {
    return b.count - a.count;
  });
}

function analyzePortfolio(positions) {
  const total = positions.reduce(function (sum, item) {
    return sum + Math.max(0, decimalNumber(item.marketValue));
  }, 0);
  const sectorMap = {};
  positions.forEach(function (item) {
    const sector = item.sector || sectorFromSymbol(item.symbol || item.name);
    sectorMap[sector] = (sectorMap[sector] || 0) + Math.max(0, decimalNumber(item.marketValue));
  });
  const sectors = Object.keys(sectorMap)
    .map(function (sector) {
      return {
        sector: sector,
        value: sectorMap[sector],
        ratio: total ? Math.round((sectorMap[sector] / total) * 100) : 0
      };
    })
    .sort(function (a, b) {
      return b.value - a.value;
    });
  const concentration = sectors[0] ? sectors[0].ratio : 0;
  return { total: total, sectors: sectors, concentration: concentration };
}

function parseWatchlist() {
  const raw = String(process.env.WATCHLIST_SYMBOLS || "").trim();
  const symbols = raw
    ? raw.split(",").map(function (item) { return item.trim(); }).filter(Boolean)
    : ["NVDA", "TSLA", "000660"];
  return symbols.map(function (symbol) {
    const info = knownStockInfo(symbol);
    return {
      symbol: info.symbol,
      name: info.name,
      market: info.market,
      currency: info.currency,
      sector: info.sector,
      source: "watchlist",
      configured: Boolean(raw)
    };
  });
}

function stockSignalKeywords(item) {
  const sector = item.sector || sectorFromSymbol(item.symbol || item.name);
  const base = [item.symbol, item.name, sector].filter(Boolean);
  const sectorKeywords = {
    "반도체": ["ai", "chip", "semiconductor", "nvidia", "hynix", "samsung", "data center", "반도체", "삼성", "하이닉스"],
    "AI/플랫폼": ["ai", "platform", "software", "apple", "microsoft", "google", "meta", "cloud", "앱", "플랫폼"],
    "모빌리티": ["tesla", "ev", "battery", "delivery", "vehicle", "auto", "전기차", "배터리"],
    "디지털자산": ["bitcoin", "crypto", "coin", "token", "ethereum", "비트코인", "코인"],
    "기타": ["market", "stocks", "earnings", "guidance", "growth", "equity"]
  };
  return base.concat(sectorKeywords[sector] || sectorKeywords["기타"]).map(function (value) {
    return String(value || "").toLowerCase();
  }).filter(Boolean);
}

function matchedSignalsForStock(item, newsItems, socialPosts) {
  const keywords = stockSignalKeywords(item);
  const signals = newsItems.concat(socialPostsAsSignals(socialPosts));
  return signals.filter(function (signal) {
    const haystack = (signal.title + " " + signal.summary + " " + signal.source).toLowerCase();
    return keywords.some(function (keyword) {
      return keyword.length > 1 && haystack.indexOf(keyword) >= 0;
    });
  }).slice(0, 4).map(function (signal) {
    return {
      title: signal.title,
      source: signal.source,
      url: signal.url || "",
      type: signal.signalType === "social" ? "post" : "news"
    };
  });
}

function sectorThemePressure(item, themes) {
  const sector = item.sector || sectorFromSymbol(item.symbol || item.name);
  const themeById = {};
  themes.forEach(function (theme) {
    themeById[theme.id] = theme;
  });
  if (sector === "반도체") return themeById.ai || { count: 0 };
  if (sector === "AI/플랫폼") return themeById.ai || { count: 0 };
  if (sector === "디지털자산") return themeById.crypto || { count: 0 };
  if (sector === "모빌리티") return themeById.risk || { count: 0 };
  return { count: 0 };
}

function profitLossRate(item) {
  const marketValue = decimalNumber(item.marketValue);
  const profitLoss = decimalNumber(item.profitLoss);
  const costBasis = marketValue - profitLoss;
  if (!costBasis) return 0;
  return Math.round((profitLoss / costBasis) * 1000) / 10;
}

function clampScore(value) {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function exitDecision(source, pressure, pnlRate) {
  if (source === "watchlist") {
    if (pressure >= 62) return { label: "진입 보류", tone: "danger", priority: 1 };
    if (pressure >= 44) return { label: "기준가 대기", tone: "caution", priority: 2 };
    return { label: "관심 유지", tone: "watch", priority: 3 };
  }
  if (pressure >= 72) {
    return {
      label: pnlRate <= -8 ? "손절 검토" : "매도 검토",
      tone: "danger",
      priority: 1
    };
  }
  if (pressure >= 55) return { label: "부분 매도 검토", tone: "caution", priority: 2 };
  if (pressure >= 38) return { label: "조건부 보유", tone: "hold", priority: 3 };
  return { label: "보유 유지", tone: "watch", priority: 4 };
}

function exitReasons(item, pressure, pnlRate, context) {
  const reasons = [];
  if (item.source === "holding") {
    if (pnlRate >= 15) reasons.push("수익 구간이어서 일부 이익 확정 기준을 점검할 때입니다.");
    if (pnlRate <= -8) reasons.push("손실 구간에서 리스크 신호가 겹치면 손절 기준을 다시 확인해야 합니다.");
    if (context.sectorRatio >= 50) reasons.push("계좌 안에서 " + item.sector + " 비중이 높아 한 종목 판단이 전체 성과에 크게 반영됩니다.");
  } else {
    reasons.push("보유 전 관심 종목은 먼저 무효화 조건과 목표 보유 기간을 정해야 합니다.");
  }
  if (context.riskCount) reasons.push("리스크 뉴스가 반복되어 포지션 크기를 줄이는 판단에 가중치를 줬습니다.");
  if (context.ratesCount) reasons.push("금리/달러 신호가 있어 해외주식과 성장주의 할인율 부담을 반영했습니다.");
  if (context.sectorThemeCount && pnlRate >= 8) reasons.push(item.sector + " 테마가 강하지만 이미 수익이 난 구간이라 추격보다 분할 매도 기준이 우선입니다.");
  if (!reasons.length) reasons.push("강한 매도 압력보다 보유 조건 유지 신호가 더 큽니다.");
  return reasons.slice(0, 3);
}

function exitTriggers(item, pnlRate, context) {
  const triggers = [];
  if (item.source === "holding") {
    triggers.push(pnlRate >= 10 ? "수익률 " + pnlRate + "% 구간: 분할 익절 비율 확정" : "평균단가 대비 손실 허용폭 재확인");
    if (context.riskCount || context.ratesCount) triggers.push("금리/달러/리스크 뉴스가 다음 장까지 이어지는지 확인");
    if (context.sectorRatio >= 50) triggers.push(item.sector + " 비중 50% 초과 시 같은 테마 종목 동시 축소 검토");
  } else {
    triggers.push("진입 전 목표가, 손절가, 매도 사유를 한 줄로 고정");
    triggers.push("뉴스와 포스팅 신호가 같은 방향으로 2회 이상 반복될 때만 반응");
  }
  if (context.matchedCount) triggers.push("관련 기사/포스팅 " + context.matchedCount + "건의 방향성 확인");
  return triggers.slice(0, 3);
}

function buildExitCandidate(item, source, portfolio, themes, newsItems, socialPosts) {
  const normalized = Object.assign({}, item, {
    source: source,
    sector: item.sector || sectorFromSymbol(item.symbol || item.name)
  });
  const themeById = {};
  themes.forEach(function (theme) { themeById[theme.id] = theme; });
  const riskCount = (themeById.risk && themeById.risk.count) || 0;
  const ratesCount = (themeById.rates && themeById.rates.count) || 0;
  const sectorTheme = sectorThemePressure(normalized, themes);
  const sectorEntry = (portfolio.sectors || []).find(function (entry) {
    return entry.sector === normalized.sector;
  }) || { ratio: 0 };
  const matches = matchedSignalsForStock(normalized, newsItems, socialPosts);
  const pnlRate = source === "holding" ? profitLossRate(normalized) : 0;
  const context = {
    riskCount: riskCount,
    ratesCount: ratesCount,
    sectorThemeCount: sectorTheme.count || 0,
    sectorRatio: sectorEntry.ratio || 0,
    matchedCount: matches.length
  };

  let pressure = source === "holding" ? 26 : 22;
  pressure += Math.min(28, riskCount * 9 + ratesCount * 5);
  pressure += Math.min(18, matches.length * 5);
  pressure += Math.min(16, (sectorTheme.count || 0) * 4);
  if (source === "holding") {
    if (pnlRate >= 20) pressure += 22;
    else if (pnlRate >= 10) pressure += 13;
    else if (pnlRate <= -15) pressure += 24;
    else if (pnlRate <= -8) pressure += 15;
    if (sectorEntry.ratio >= 55) pressure += 12;
    else if (sectorEntry.ratio >= 40) pressure += 6;
    if ((sectorTheme.count || 0) >= 2 && pnlRate >= 8) pressure += 8;
  } else {
    if (riskCount || ratesCount) pressure += 10;
    if ((sectorTheme.count || 0) >= 2 && !riskCount) pressure -= 7;
  }

  const exitPressure = clampScore(pressure);
  const signalScore = clampScore(matches.length * 18 + (sectorTheme.count || 0) * 10 - riskCount * 4);
  const decision = exitDecision(source, exitPressure, pnlRate);
  return {
    symbol: normalized.symbol,
    name: normalized.name,
    source: source,
    sector: normalized.sector,
    market: normalized.market || "",
    currency: normalized.currency || "",
    marketValue: decimalNumber(normalized.marketValue),
    profitLoss: decimalNumber(normalized.profitLoss),
    profitLossRate: pnlRate,
    signalScore: signalScore,
    exitPressure: exitPressure,
    decision: decision.label,
    tone: decision.tone,
    priority: decision.priority,
    reasons: exitReasons(normalized, exitPressure, pnlRate, context),
    triggers: exitTriggers(normalized, pnlRate, context),
    matchedSignals: matches
  };
}

function buildExitLens(toss, portfolio, themes, newsItems, socialPosts) {
  const positions = (toss.positions || []).filter(function (item) {
    const sector = item.sector || sectorFromSymbol(item.symbol || item.name);
    return sector !== "현금" && decimalNumber(item.marketValue) > 0;
  });
  const holdingSymbols = new Set(positions.map(function (item) {
    return String(item.symbol || "").toUpperCase();
  }));
  const watchlist = parseWatchlist().filter(function (item) {
    return !holdingSymbols.has(String(item.symbol || "").toUpperCase());
  });
  const holdingItems = positions.map(function (item) {
    return buildExitCandidate(item, "holding", portfolio, themes, newsItems, socialPosts);
  });
  const watchItems = watchlist.map(function (item) {
    return buildExitCandidate(item, "watchlist", portfolio, themes, newsItems, socialPosts);
  });
  const items = holdingItems.concat(watchItems).sort(function (a, b) {
    if (a.priority !== b.priority) return a.priority - b.priority;
    return b.exitPressure - a.exitPressure;
  });
  const urgentCount = items.filter(function (item) {
    return item.tone === "danger" || item.tone === "caution";
  }).length;
  const topItems = items.slice(0, 3);
  const overallPressure = topItems.length
    ? Math.round(topItems.reduce(function (sum, item) { return sum + item.exitPressure; }, 0) / topItems.length)
    : 0;
  const headline = items[0]
    ? items[0].name + "의 " + items[0].decision + " 우선순위가 가장 높습니다."
    : "매도 판단을 만들 보유/관심 종목이 아직 없습니다.";
  return {
    headline: headline,
    overallPressure: overallPressure,
    urgentCount: urgentCount,
    holdingCount: holdingItems.length,
    watchCount: watchItems.length,
    items: items,
    rules: [
      "수익 구간에서 리스크 신호가 커지면 전량 매도보다 분할 매도 기준부터 확인합니다.",
      "손실 구간에서 같은 악재가 반복되면 손절 기준을 숫자로 고정합니다.",
      "관심 종목은 매수 전 목표가, 손절가, 매도 사유를 먼저 정합니다."
    ]
  };
}

function buildFlowLensSnapshot(toss, newsItems, socialPosts) {
  const themes = analyzeThemes(newsItems, socialPosts);
  const portfolio = analyzePortfolio(toss.positions || []);
  const riskTheme = themes.find(function (theme) { return theme.id === "risk"; }) || { count: 0 };
  const aiTheme = themes.find(function (theme) { return theme.id === "ai"; }) || { count: 0 };
  const ratesTheme = themes.find(function (theme) { return theme.id === "rates"; }) || { count: 0 };
  const exitLens = buildExitLens(toss, portfolio, themes, newsItems, socialPosts);
  const flowScore = Math.max(
    0,
    Math.min(100, 52 + aiTheme.count * 6 - riskTheme.count * 7 - ratesTheme.count * 3 - Math.max(0, portfolio.concentration - 55) / 3)
  );
  const regime = flowScore >= 65 ? "위험자산 우위" : flowScore <= 40 ? "방어 우위" : "혼조 관찰";
  const leadTheme = themes[0] || { label: "대기", headline: "뉴스 대기" };
  return {
    generatedAt: now(),
    headline: exitLens.headline,
    exitScore: exitLens.overallPressure,
    flowScore: Math.round(flowScore),
    regime: regime,
    summary: [
      exitLens.urgentCount ? "매도 또는 축소를 검토할 종목이 " + exitLens.urgentCount + "개 잡혔습니다." : "즉시 매도보다 조건 확인이 우선입니다.",
      portfolio.sectors[0] ? "계좌는 " + portfolio.sectors[0].sector + " 비중이 가장 큽니다." : "계좌 보유 종목은 아직 비어 있습니다.",
      leadTheme.label + " 신호가 뉴스와 포스팅에서 가장 많이 잡혔습니다."
    ],
    toss: toss,
    portfolio: portfolio,
    exitLens: exitLens,
    themes: themes,
    news: newsItems,
    social: socialPosts,
    checklist: [
      { label: "보유 종목마다 전량/부분 매도 기준과 손절 기준을 숫자로 남기기", status: exitLens.urgentCount ? "주의" : "정상" },
      { label: "관심 종목은 진입 전에 무효화 조건과 매도 사유부터 정하기", status: exitLens.watchCount ? "정상" : "대기" },
      { label: "X 포스팅은 기사보다 소음이 크므로 반복 등장하는 테마만 반영", status: socialPosts.length ? "정상" : "대기" },
      { label: "금리/달러 뉴스가 강하면 해외주식과 성장주 비중 축소 기준 확인", status: ratesTheme.count > 1 ? "주의" : "정상" },
      { label: "주문 기능은 읽기 전용 점검 이후 별도 단계에서만 열기", status: "잠금" }
    ]
  };
}

async function flowLensSnapshot() {
  const newsPromise = Promise.race([
    fetchFlowNews(),
    new Promise(function (resolve) {
      setTimeout(function () {
        resolve(demoNewsItems("뉴스 빠른 fallback"));
      }, 1800);
    })
  ]);
  const socialPromise = Promise.race([
    fetchSocialPosts(),
    new Promise(function (resolve) {
      setTimeout(function () {
        resolve(demoSocialPosts("포스팅 빠른 fallback"));
      }, 1800);
    })
  ]);
  const results = await Promise.all([fetchTossPortfolio(), newsPromise, socialPromise]);
  return buildFlowLensSnapshot(results[0], results[1], results[2]);
}

async function api(req, res, pathname) {
  if (pathname === "/api/economic-feed/rss") {
    if (req.method === "OPTIONS") return corsText(res, 204, "", "text/plain");
    if (req.method === "GET") {
      try {
        const parsedQuery = url.parse(req.url, true).query;
        const targetUrl = normalizeEconomicFeedRssUrl(parsedQuery.url);
        const raw = await fetchText(targetUrl);
        return corsText(res, 200, raw, "application/rss+xml");
      } catch (error) {
        return corsJson(res, 400, { error: error.message || "RSS 피드를 가져오지 못했습니다." });
      }
    }
  }

  if (pathname === "/api/economic-feed/gdelt") {
    if (req.method === "OPTIONS") return corsText(res, 204, "", "text/plain");
    if (req.method === "GET") {
      try {
        const parsedQuery = url.parse(req.url, true).query;
        const targetUrl = normalizeEconomicFeedGdeltUrl(parsedQuery.url);
        const raw = await fetchText(targetUrl);
        return corsText(res, 200, raw, "application/json");
      } catch (error) {
        return corsJson(res, 400, { error: error.message || "GDELT 피드를 가져오지 못했습니다." });
      }
    }
  }

  if (pathname === "/api/data-api/fred/observations") {
    if (req.method === "OPTIONS") return corsJson(res, 204, {});
    if (req.method === "GET") {
      try {
        const parsedQuery = url.parse(req.url, true).query;
        const targetUrl = normalizeFredObservationsUrl(parsedQuery);
        const payload = await fetchJson(targetUrl);
        return corsJson(res, 200, payload);
      } catch (error) {
        return corsJson(res, 400, {
          error: error.message || "FRED 데이터를 가져오지 못했습니다."
        });
      }
    }
  }

  if (pathname === "/api/data-api/opendart/company") {
    if (req.method === "OPTIONS") return corsJson(res, 204, {});
    if (req.method === "GET") {
      try {
        const parsedQuery = url.parse(req.url, true).query;
        const targetUrl = normalizeOpenDartCompanyUrl(parsedQuery);
        const payload = await fetchJson(targetUrl);
        return corsJson(res, 200, payload);
      } catch (error) {
        return corsJson(res, 400, {
          error: error.message || "OpenDART 데이터를 가져오지 못했습니다."
        });
      }
    }
  }

  if (req.method === "GET" && pathname === "/api/flow-lens") {
    const payload = await flowLensSnapshot();
    return json(res, 200, payload);
  }

  if (req.method === "GET" && pathname === "/api/bootstrap") return json(res, 200, snapshot());

  if (req.method === "PUT" && pathname === "/api/profile") {
    const body = await readBody(req);
    if (!body.ownerName || !body.assistantName) return json(res, 400, { error: "이름과 비서 이름은 필요합니다." });
    const store = save(function (draft) {
      draft.profile = Object.assign({}, draft.profile, body);
    });
    return json(res, 200, { profile: store.profile });
  }

  if (req.method === "POST" && pathname === "/api/chat") {
    const body = await readBody(req);
    if (!body.message || !String(body.message).trim()) return json(res, 400, { error: "메시지를 입력하세요." });
    return json(res, 200, await askAssistant(body.message));
  }

  if (req.method === "GET" && pathname === "/api/memories") return json(res, 200, { memories: readStore().memories });

  if (req.method === "GET" && pathname === "/api/stocks") {
    const parsedQuery = url.parse(req.url, true).query;
    const symbols = String(parsedQuery.symbols || "")
      .split(",")
      .map(function (symbol) {
        return symbol.trim();
      })
      .filter(Boolean)
      .filter(function (symbol, index, list) {
        return list.indexOf(symbol) === index;
      })
      .slice(0, 12);
    const stocks = await Promise.all(
      symbols.map(function (symbol) {
        return stockSnapshot(symbol);
      })
    );
    return json(res, 200, {
      stocks: stocks,
      source: "Quotes: Stooq/Naver Finance, News: multi-channel RSS/GDELT",
      fetchedAt: now()
    });
  }

  if (req.method === "POST" && pathname === "/api/memories") {
    const body = await readBody(req);
    const content = String(body.content || "").trim();
    if (!content) return json(res, 400, { error: "기억 내용을 입력하세요." });
    const stamped = now();
    const memory = {
      id: id("mem"),
      content: content,
      category: memoryCategories.indexOf(body.category) >= 0 ? body.category : "other",
      status: body.status === "candidate" ? "candidate" : "approved",
      importance: Math.min(Math.max(Number(body.importance || 3), 1), 5),
      source: "manual",
      createdAt: stamped,
      updatedAt: stamped
    };
    const store = save(function (draft) {
      draft.memories.unshift(memory);
    });
    return json(res, 200, { memory: memory, memories: store.memories });
  }

  const memoryMatch = pathname.match(/^\/api\/memories\/([^/]+)$/);
  if (memoryMatch && req.method === "PATCH") {
    const body = await readBody(req);
    const store = save(function (draft) {
      draft.memories = draft.memories.map(function (memory) {
        if (memory.id !== memoryMatch[1]) return memory;
        return Object.assign({}, memory, body, {
          content: body.content ? String(body.content).trim() : memory.content,
          updatedAt: now()
        });
      });
    });
    return json(res, 200, { memories: store.memories });
  }
  if (memoryMatch && req.method === "DELETE") {
    const store = save(function (draft) {
      draft.memories = draft.memories.filter(function (memory) {
        return memory.id !== memoryMatch[1];
      });
    });
    return json(res, 200, { memories: store.memories });
  }

  if (req.method === "GET" && pathname === "/api/items") return json(res, 200, { items: readStore().items });

  if (req.method === "POST" && pathname === "/api/items") {
    const body = await readBody(req);
    const title = String(body.title || "").trim();
    if (domainTypes.indexOf(body.type) < 0 || !title) return json(res, 400, { error: "유형과 제목을 입력하세요." });
    const stamped = now();
    const item = {
      id: id("item"),
      type: body.type,
      title: title,
      status: String(body.status || "open").trim(),
      date: String(body.date || "").trim(),
      amount: normalizeAmount(body.amount),
      currency: String(body.currency || "").trim(),
      ticker: String(body.ticker || "").trim().toUpperCase(),
      location: String(body.location || "").trim(),
      notes: String(body.notes || "").trim(),
      fields: normalizeItemFields(body.fields),
      createdAt: stamped,
      updatedAt: stamped
    };
    const store = save(function (draft) {
      draft.items.unshift(item);
    });
    return json(res, 200, { item: item, items: store.items });
  }

  const itemMatch = pathname.match(/^\/api\/items\/([^/]+)$/);
  if (itemMatch && req.method === "PATCH") {
    const body = await readBody(req);
    const store = save(function (draft) {
      draft.items = draft.items.map(function (item) {
        if (item.id !== itemMatch[1]) return item;
        return patchItem(item, body);
      });
    });
    return json(res, 200, { items: store.items });
  }
  if (itemMatch && req.method === "DELETE") {
    const store = save(function (draft) {
      draft.items = draft.items.filter(function (item) {
        return item.id !== itemMatch[1];
      });
    });
    return json(res, 200, { items: store.items });
  }

  return json(res, 404, { error: "API를 찾지 못했습니다." });
}

const server = http.createServer(function (req, res) {
  const parsed = url.parse(req.url);
  const pathname = decodeURIComponent(parsed.pathname || "/");

  if (!authorizeShare(req, res)) return;

  if (pathname.indexOf("/api/") === 0) {
    api(req, res, pathname).catch(function (error) {
      json(res, 500, { error: error.message || "서버 오류" });
    });
    return;
  }

  serveStatic(req, res, pathname);
});

function listen(port) {
  const host = process.env.HOST || "127.0.0.1";
  server.once("error", function (error) {
    if (error.code === "EADDRINUSE") {
      listen(port + 1);
    } else {
      console.error(error);
      process.exit(1);
    }
  });
  server.listen(port, host, function () {
    console.log("Digiter Twin is running at http://" + host + ":" + port);
  });
}

listen(Number(process.env.PORT || 3000));
