const childProcess = require("child_process");
const crypto = require("crypto");
const dns = require("dns");
const fs = require("fs");
const http = require("http");
const https = require("https");
const path = require("path");
const {
  healthRotationRequired,
  lifecycleConfig,
  nextRotationAt,
  retryDelayMs,
  rotationDue
} = require("./share-tunnel-lifecycle");

const rootDir = path.resolve(__dirname, "..");
const requestedPort = Number(process.env.PORT || 3000);
const requestedProvider = String(process.env.TUNNEL_PROVIDER || "").trim().toLowerCase();
const fixedEntryUrl = normalizeFixedEntryUrl(process.env.SHARE_FIXED_ENTRY_URL || "https://namsoon00.github.io/digital_twin/live/");
const publishTargetEnabled = !["0", "false", "no", "off"].includes(String(process.env.SHARE_PUBLISH_TARGET || "1").trim().toLowerCase());
const npxCommand = process.platform === "win32" ? "npx.cmd" : "npx";
const credentialsPath = path.resolve(process.env.SHARE_CREDENTIALS_PATH || path.join(rootDir, "data", "share-access.json"));
const runtimeStatePath = path.resolve(process.env.SHARE_RUNTIME_STATE_PATH || path.join(rootDir, "data", "share-runtime.json"));
const rotationRequestPath = path.resolve(process.env.SHARE_ROTATION_REQUEST_PATH || path.join(rootDir, "data", "share-rotation-request.json"));
const publisherPath = path.join(rootDir, "scripts", "publish-live-target.js");
const lifecycle = lifecycleConfig(process.env);
const shareCredentials = loadOrCreateShareCredentials();
const fixedTargetUrl = new URL("../live-target.json", fixedEntryUrl).toString();

let activeProvider = "";
let activeTunnel = null;
let candidateTunnel = null;
let retiredTunnels = [];
let runtimeState = {};
let shuttingDown = false;
let rotationInFlight = false;
let rotationRetryAttempt = 0;
let rotationRetryAt = "";
let consecutiveHealthFailures = 0;
let lastHealthCheckMs = 0;
let maintenanceTimer = null;

function randomToken(bytes) {
  return crypto
    .randomBytes(bytes || 24)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function normalizeFixedEntryUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    if (parsed.protocol !== "https:") throw new Error("https required");
    parsed.hash = "";
    if (!parsed.pathname.endsWith("/")) parsed.pathname += "/";
    return parsed.toString();
  } catch (_error) {
    return "https://namsoon00.github.io/digital_twin/live/";
  }
}

function fixedAccessUrl(tokenName, token) {
  const parsed = new URL(fixedEntryUrl);
  parsed.hash = new URLSearchParams([[tokenName, String(token || "")]]).toString();
  return parsed.toString();
}

function readShareCredentials() {
  try {
    const payload = JSON.parse(fs.readFileSync(credentialsPath, "utf8"));
    return payload && typeof payload === "object" ? payload : {};
  } catch (error) {
    if (error && error.code === "ENOENT") return {};
    throw error;
  }
}

function loadOrCreateShareCredentials() {
  const saved = readShareCredentials();
  const now = new Date().toISOString();
  const credentials = {
    viewToken: String(process.env.SHARE_VIEW_TOKEN || process.env.SHARE_TOKEN || saved.viewToken || randomToken()),
    ownerToken: String(process.env.SHARE_OWNER_TOKEN || saved.ownerToken || randomToken()),
    sessionSecret: String(process.env.SHARE_SESSION_SECRET || saved.sessionSecret || randomToken(32)),
    sessionDays: Math.max(1, Math.min(90, Number(process.env.SHARE_SESSION_DAYS || saved.sessionDays || 30) || 30)),
    createdAt: String(saved.createdAt || now),
    updatedAt: String(saved.updatedAt || now)
  };
  const changed = !saved.viewToken
    || ["viewToken", "ownerToken", "sessionSecret", "sessionDays", "createdAt"].some(function (key) {
      return saved[key] !== credentials[key];
    });
  if (!changed) return credentials;
  credentials.updatedAt = now;
  fs.mkdirSync(path.dirname(credentialsPath), { recursive: true, mode: 0o700 });
  const temporaryPath = credentialsPath + ".tmp-" + process.pid;
  fs.writeFileSync(temporaryPath, JSON.stringify(credentials, null, 2) + "\n", { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temporaryPath, credentialsPath);
  fs.chmodSync(credentialsPath, 0o600);
  return credentials;
}

function commandExists(command) {
  const result = childProcess.spawnSync(command, ["--version"], { stdio: "ignore" });
  return !result.error && result.status === 0;
}

function providerName() {
  if (requestedProvider) return requestedProvider;
  return commandExists("cloudflared") ? "cloudflared" : "localtunnel";
}

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

function requestOriginStatus() {
  return new Promise(function (resolve, reject) {
    const request = http.get({
      host: "127.0.0.1",
      port: requestedPort,
      path: "/api/share/status",
      timeout: 2000,
      headers: { Accept: "application/json" }
    }, function (response) {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", function (chunk) { body += chunk; });
      response.on("end", function () {
        if (response.statusCode !== 200) {
          reject(new Error("공유 원본 서버 상태 API가 " + response.statusCode + "로 응답했습니다."));
          return;
        }
        try {
          resolve(JSON.parse(body));
        } catch (_error) {
          reject(new Error("공유 원본 서버 상태 응답을 읽을 수 없습니다."));
        }
      });
    });
    request.on("timeout", function () { request.destroy(new Error("공유 원본 서버 응답 시간이 초과되었습니다.")); });
    request.on("error", reject);
  });
}

async function waitForProtectedOrigin(timeoutMs) {
  const deadline = Date.now() + Math.max(1000, Number(timeoutMs || 30000));
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const status = await requestOriginStatus();
      if (!status || status.enabled !== true) throw new Error("원본 서버에 공유 인증이 활성화되지 않았습니다.");
      return status;
    } catch (error) {
      lastError = error;
      await sleep(500);
    }
  }
  throw new Error(
    "127.0.0.1:" + requestedPort + "의 공유 원본 서버를 확인하지 못했습니다. "
    + "cloudflareShareManagedEnabled를 켠 뒤 서비스를 시작하세요. "
    + String(lastError && lastError.message || "")
  );
}

async function ensureProtectedOrigin() {
  try {
    return await waitForProtectedOrigin(3000);
  } catch (initialError) {
    console.log("Protected origin is not ready. Starting the single local web origin.");
    const restarted = childProcess.spawnSync(process.execPath, [path.join(rootDir, "scripts", "restart-web-service.js")], {
      cwd: rootDir,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      env: Object.assign({}, process.env, {
        PORT: String(requestedPort),
        SHARE_TOKEN: shareCredentials.viewToken,
        SHARE_VIEW_TOKEN: shareCredentials.viewToken,
        SHARE_OWNER_TOKEN: shareCredentials.ownerToken,
        SHARE_SESSION_SECRET: shareCredentials.sessionSecret,
        SHARE_SESSION_DAYS: String(shareCredentials.sessionDays)
      })
    });
    if (restarted.error || restarted.status !== 0) {
      const detail = String(restarted.stderr || restarted.stdout || initialError.message || "").trim();
      throw new Error("공유 원본 서버를 시작하지 못했습니다. " + detail.slice(-500));
    }
    return waitForProtectedOrigin(15000);
  }
}

function writeShareRuntimeState(patch) {
  runtimeState = Object.assign({}, runtimeState, patch || {}, { updatedAt: new Date().toISOString() });
  fs.mkdirSync(path.dirname(runtimeStatePath), { recursive: true, mode: 0o700 });
  const temporaryPath = runtimeStatePath + ".tmp-" + process.pid;
  fs.writeFileSync(temporaryPath, JSON.stringify(runtimeState, null, 2) + "\n", { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temporaryPath, runtimeStatePath);
  fs.chmodSync(runtimeStatePath, 0o600);
}

function initializeRuntimeState(provider) {
  runtimeState = {
    version: 3,
    provider,
    baseUrl: "",
    viewerUrl: "",
    ownerUrl: "",
    fixedEntryUrl,
    fixedViewerUrl: fixedAccessUrl("share_token", shareCredentials.viewToken),
    fixedOwnerUrl: fixedAccessUrl("owner_token", shareCredentials.ownerToken),
    ownerPid: process.pid,
    tunnelPid: 0,
    active: false,
    rotationStatus: "starting",
    rotationCount: 0,
    rotationMinutes: lifecycle.rotationMinutes,
    healthCheckSeconds: lifecycle.healthCheckSeconds,
    rotationGraceSeconds: lifecycle.rotationGraceSeconds,
    targetPublishStatus: publishTargetEnabled ? "waiting" : "disabled"
  };
  writeShareRuntimeState();
}

function clearShareRuntimeState() {
  try {
    const state = JSON.parse(fs.readFileSync(runtimeStatePath, "utf8"));
    if (Number(state.ownerPid || 0) === process.pid) fs.unlinkSync(runtimeStatePath);
  } catch (error) {
    if (!error || error.code !== "ENOENT") console.error("공유 URL 런타임 상태를 정리하지 못했습니다: " + (error.message || error));
  }
}

function publishLiveTarget(record) {
  if (!publishTargetEnabled) {
    writeShareRuntimeState({ targetPublishStatus: "disabled" });
    return Promise.resolve({ status: "disabled" });
  }
  writeShareRuntimeState({ targetPublishStatus: "publishing", targetPublishError: "" });
  return new Promise(function (resolve, reject) {
    const publisher = childProcess.spawn(process.execPath, [publisherPath], {
      cwd: rootDir,
      stdio: ["ignore", "pipe", "pipe"],
      env: Object.assign({}, process.env, {
        SHARE_RUNTIME_STATE_PATH: runtimeStatePath,
        SHARE_TARGET_BASE_URL: record.baseUrl,
        SHARE_TARGET_PROVIDER: record.provider
      })
    });
    let output = "";
    let errorOutput = "";
    publisher.stdout.on("data", function (chunk) { output += chunk.toString(); });
    publisher.stderr.on("data", function (chunk) { errorOutput += chunk.toString(); });
    publisher.on("error", reject);
    publisher.on("exit", function (code) {
      if (code === 0) {
        resolve({ status: "published", output: output.trim() });
        return;
      }
      reject(new Error(String(errorOutput || output || "publish failed").trim().slice(-500)));
    });
  });
}

function tunnelArgs(provider, port) {
  if (provider === "cloudflared") {
    return {
      command: process.env.CLOUDFLARED_COMMAND || "cloudflared",
      args: ["tunnel", "--url", "http://127.0.0.1:" + port],
      urlPattern: /https:\/\/[a-zA-Z0-9-]+\.trycloudflare\.com/g
    };
  }
  if (provider === "localtunnel") {
    return {
      command: npxCommand,
      args: ["--yes", "localtunnel", "--port", String(port), "--local-host", "127.0.0.1"],
      urlPattern: /https:\/\/[^\s]+\.loca\.lt/g
    };
  }
  throw new Error("지원하지 않는 터널 제공자입니다: " + provider);
}

function stopTunnel(record) {
  if (!record || !record.process) return;
  record.intentionalStop = true;
  record.process.removeAllListeners("exit");
  try { record.process.kill("SIGTERM"); } catch (_error) {}
}

function handleTunnelExit(record, code) {
  if (shuttingDown || record.intentionalStop) return;
  if (record === activeTunnel) {
    activeTunnel = null;
    writeShareRuntimeState({
      active: false,
      tunnelPid: 0,
      rotationStatus: "recovering",
      lastRotationStatus: "failed",
      lastRotationError: "활성 터널 프로세스가 종료되었습니다. exit=" + String(code == null ? "unknown" : code)
    });
    console.error("활성 터널이 종료되어 새 주소를 발급합니다. exit=" + String(code));
    rotateTunnel("unexpected-exit");
    return;
  }
  if (record === candidateTunnel) {
    console.error("후보 터널이 활성화 전에 종료되었습니다. exit=" + String(code));
  }
}

function startTunnelProcess(provider, port) {
  const config = tunnelArgs(provider, port);
  return new Promise(function (resolve, reject) {
    const record = {
      provider,
      process: childProcess.spawn(config.command, config.args, {
        cwd: rootDir,
        stdio: ["ignore", "pipe", "pipe"]
      }),
      output: "",
      baseUrl: "",
      startedAt: new Date().toISOString(),
      intentionalStop: false,
      ready: false
    };
    let settled = false;
    const timer = setTimeout(function () {
      if (settled) return;
      settled = true;
      stopTunnel(record);
      reject(new Error("새 공유 터널 주소를 제한 시간 안에 발급받지 못했습니다."));
    }, lifecycle.startupTimeoutMs);

    function fail(error) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error instanceof Error ? error : new Error(String(error || "터널 시작 실패")));
    }

    function read(chunk) {
      const text = chunk.toString();
      process.stdout.write(text);
      record.output = (record.output + text).slice(-16000);
      const matches = record.output.match(config.urlPattern);
      if (!matches || !matches.length || settled) return;
      record.baseUrl = matches[matches.length - 1].replace(/[),.]+$/, "").replace(/\/$/, "");
      record.ready = true;
      settled = true;
      clearTimeout(timer);
      resolve(record);
    }

    record.process.stdout.on("data", read);
    record.process.stderr.on("data", read);
    record.process.on("error", fail);
    record.process.on("exit", function (code) {
      if (!record.ready) fail(new Error("터널 프로세스가 주소 발급 전에 종료되었습니다. exit=" + String(code)));
      handleTunnelExit(record, code);
    });
  });
}

function requestTunnel(url, headers) {
  return new Promise(function (resolve, reject) {
    const request = https.get(url, {
      timeout: 8000,
      lookup: function (hostname, options, callback) {
        dns.resolve4(hostname, function (error, addresses) {
          if (error) return callback(error);
          const resolved = (addresses || []).filter(Boolean);
          if (!resolved.length) return callback(new Error("IPv4 주소를 확인하지 못했습니다: " + hostname));
          if (options && options.all) {
            callback(null, resolved.map(function (address) { return { address, family: 4 }; }));
            return;
          }
          callback(null, resolved[0], 4);
        });
      },
      headers: Object.assign({ Accept: "application/json", "User-Agent": "orbit-alpha-share-health/1" }, headers || {})
    }, function (response) {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", function (chunk) { body += chunk; });
      response.on("end", function () {
        resolve({ statusCode: Number(response.statusCode || 0), headers: response.headers || {}, body });
      });
    });
    request.on("timeout", function () { request.destroy(new Error("터널 상태 확인 시간이 초과되었습니다.")); });
    request.on("error", reject);
  });
}

function cookieHeader(response) {
  const values = response && response.headers ? response.headers["set-cookie"] : [];
  return (Array.isArray(values) ? values : [values]).filter(Boolean).map(function (value) {
    return String(value).split(";", 1)[0];
  }).join("; ");
}

async function verifyTunnel(record) {
  if (!record || !record.baseUrl) throw new Error("검증할 터널 주소가 없습니다.");
  const authUrl = record.baseUrl + "/?share_token=" + encodeURIComponent(shareCredentials.viewToken);
  const auth = await requestTunnel(authUrl);
  if ([200, 302, 303].indexOf(auth.statusCode) < 0) {
    throw new Error("터널 인증 확인 실패 HTTP " + auth.statusCode);
  }
  const cookie = cookieHeader(auth);
  if (!cookie) throw new Error("터널 인증 세션을 발급받지 못했습니다.");
  const status = await requestTunnel(record.baseUrl + "/api/share/status", { Cookie: cookie });
  if (status.statusCode !== 200) throw new Error("터널 상태 확인 실패 HTTP " + status.statusCode);
  let payload = {};
  try { payload = JSON.parse(status.body); } catch (_error) { throw new Error("터널 상태 응답을 읽지 못했습니다."); }
  if (!payload || payload.enabled !== true) throw new Error("터널 원본의 공유 인증이 활성화되지 않았습니다.");
  return payload;
}

async function waitForTunnelHealth(record) {
  const deadline = Date.now() + lifecycle.startupTimeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      return await verifyTunnel(record);
    } catch (error) {
      lastError = error;
      await sleep(1000);
    }
  }
  throw lastError || new Error("새 터널 검증 시간이 초과되었습니다.");
}

async function waitForPublishedTarget(record) {
  if (!publishTargetEnabled) return;
  const deadline = Date.now() + lifecycle.targetPropagationTimeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const separator = fixedTargetUrl.indexOf("?") >= 0 ? "&" : "?";
      const response = await requestTunnel(fixedTargetUrl + separator + "ts=" + Date.now());
      if (response.statusCode !== 200) throw new Error("고정 주소 대상 확인 실패 HTTP " + response.statusCode);
      const payload = JSON.parse(response.body);
      if (String(payload && payload.baseUrl || "").replace(/\/$/, "") !== record.baseUrl) {
        throw new Error("고정 주소가 아직 이전 터널을 가리키고 있습니다.");
      }
      return;
    } catch (error) {
      lastError = error;
      await sleep(2000);
    }
  }
  throw lastError || new Error("고정 주소 전환 확인 시간이 초과되었습니다.");
}

function retireTunnel(record) {
  if (!record || !record.process) return;
  const retireAt = new Date(Date.now() + lifecycle.rotationGraceMs).toISOString();
  retiredTunnels.push(record);
  writeShareRuntimeState({ retiringTunnelPid: record.process.pid || 0, retiringTunnelUntil: retireAt });
  setTimeout(function () {
    stopTunnel(record);
    retiredTunnels = retiredTunnels.filter(function (item) { return item !== record; });
    if (!shuttingDown && Number(runtimeState.retiringTunnelPid || 0) === Number(record.process && record.process.pid || 0)) {
      writeShareRuntimeState({ retiringTunnelPid: 0, retiringTunnelUntil: "" });
    }
  }, lifecycle.rotationGraceMs);
}

function activateTunnel(record, previous, reason) {
  const now = new Date().toISOString();
  activeTunnel = record;
  candidateTunnel = null;
  consecutiveHealthFailures = 0;
  lastHealthCheckMs = Date.now();
  rotationRetryAttempt = 0;
  rotationRetryAt = "";
  const baseUrl = record.baseUrl;
  const viewerUrl = baseUrl + "/?share_token=" + encodeURIComponent(shareCredentials.viewToken);
  const ownerUrl = baseUrl + "/?owner_token=" + encodeURIComponent(shareCredentials.ownerToken);
  writeShareRuntimeState({
    version: 3,
    provider: record.provider,
    baseUrl,
    viewerUrl,
    ownerUrl,
    fixedEntryUrl,
    fixedViewerUrl: fixedAccessUrl("share_token", shareCredentials.viewToken),
    fixedOwnerUrl: fixedAccessUrl("owner_token", shareCredentials.ownerToken),
    ownerPid: process.pid,
    tunnelPid: record.process && record.process.pid ? record.process.pid : 0,
    active: true,
    tunnelStartedAt: record.startedAt,
    renewAt: nextRotationAt(record.startedAt, lifecycle.rotationIntervalMs),
    rotationStatus: "active",
    rotationCount: Math.max(0, Number(runtimeState.rotationCount || 0)) + (previous ? 1 : 0),
    rotationMinutes: lifecycle.rotationMinutes,
    healthCheckSeconds: lifecycle.healthCheckSeconds,
    rotationGraceSeconds: lifecycle.rotationGraceSeconds,
    lastRotationAt: now,
    lastRotationReason: reason,
    lastRotationStatus: "ok",
    lastRotationError: "",
    rotationRetryAt: "",
    lastHealthCheckAt: now,
    lastHealthStatus: "healthy",
    consecutiveHealthFailures: 0,
    previousBaseUrl: previous && previous.baseUrl || "",
    targetPublishStatus: publishTargetEnabled ? "published" : "disabled",
    targetPublishedAt: publishTargetEnabled ? now : String(runtimeState.targetPublishedAt || ""),
    targetPublishError: ""
  });
  console.log("");
  console.log("Cloudflare tunnel active: " + baseUrl);
  console.log("Fixed entry: " + fixedEntryUrl);
  console.log("Next proactive renewal: " + runtimeState.renewAt);
  console.log("");
  if (previous && previous !== record) retireTunnel(previous);
}

function scheduleRotationRetry() {
  rotationRetryAttempt += 1;
  rotationRetryAt = new Date(Date.now() + retryDelayMs(rotationRetryAttempt)).toISOString();
  writeShareRuntimeState({ rotationRetryAt, rotationRetryAttempt });
}

async function rotateTunnel(reason) {
  if (shuttingDown || rotationInFlight) return false;
  rotationInFlight = true;
  const previous = activeTunnel;
  writeShareRuntimeState({
    rotationStatus: previous ? "preparing" : "recovering",
    lastRotationReason: reason,
    lastRotationError: ""
  });
  try {
    candidateTunnel = await startTunnelProcess(activeProvider, requestedPort);
    writeShareRuntimeState({
      candidateTunnelPid: candidateTunnel.process && candidateTunnel.process.pid || 0,
      candidateBaseUrl: candidateTunnel.baseUrl,
      rotationStatus: "verifying"
    });
    await waitForTunnelHealth(candidateTunnel);
    writeShareRuntimeState({ rotationStatus: "publishing" });
    await publishLiveTarget(candidateTunnel);
    writeShareRuntimeState({ rotationStatus: "confirming" });
    await waitForPublishedTarget(candidateTunnel);
    activateTunnel(candidateTunnel, previous, reason);
    writeShareRuntimeState({ candidateTunnelPid: 0, candidateBaseUrl: "" });
    return true;
  } catch (error) {
    const detail = String(error && error.message || error || "터널 갱신 실패").slice(-500);
    if (candidateTunnel && candidateTunnel !== activeTunnel) stopTunnel(candidateTunnel);
    candidateTunnel = null;
    writeShareRuntimeState({
      candidateTunnelPid: 0,
      candidateBaseUrl: "",
      rotationStatus: previous ? "degraded" : "recovering",
      lastRotationStatus: "failed",
      lastRotationError: detail,
      targetPublishStatus: previous ? String(runtimeState.targetPublishStatus || "published") : "failed",
      targetPublishError: previous ? "" : detail
    });
    console.error("공유 터널 선제 갱신 실패: " + detail);
    scheduleRotationRetry();
    return false;
  } finally {
    rotationInFlight = false;
  }
}

function consumeRotationRequest() {
  try {
    const payload = JSON.parse(fs.readFileSync(rotationRequestPath, "utf8"));
    fs.unlinkSync(rotationRequestPath);
    return payload && typeof payload === "object" ? payload : { reason: "manual" };
  } catch (error) {
    if (!error || error.code !== "ENOENT") console.error("터널 갱신 요청을 읽지 못했습니다: " + String(error.message || error));
    return null;
  }
}

async function checkActiveTunnelHealth() {
  if (!activeTunnel || rotationInFlight) return;
  lastHealthCheckMs = Date.now();
  const now = new Date().toISOString();
  try {
    await verifyTunnel(activeTunnel);
    consecutiveHealthFailures = 0;
    writeShareRuntimeState({
      lastHealthCheckAt: now,
      lastHealthStatus: "healthy",
      lastHealthError: "",
      consecutiveHealthFailures: 0
    });
  } catch (error) {
    consecutiveHealthFailures += 1;
    const detail = String(error && error.message || error || "터널 상태 확인 실패").slice(-500);
    writeShareRuntimeState({
      lastHealthCheckAt: now,
      lastHealthStatus: "degraded",
      lastHealthError: detail,
      consecutiveHealthFailures
    });
    if (healthRotationRequired(consecutiveHealthFailures, lifecycle.healthFailureThreshold)) {
      await rotateTunnel("health-check-failed");
    }
  }
}

async function maintenanceTick() {
  if (shuttingDown || rotationInFlight) return;
  const request = consumeRotationRequest();
  if (request) {
    await rotateTunnel(String(request.reason || "manual"));
    return;
  }
  if (rotationRetryAt && Date.now() >= new Date(rotationRetryAt).getTime()) {
    await rotateTunnel("retry-after-failure");
    return;
  }
  if (activeTunnel && rotationDue(Date.now(), runtimeState.renewAt)) {
    await rotateTunnel("proactive-renewal");
    return;
  }
  if (!activeTunnel) {
    await rotateTunnel("recover-missing-tunnel");
    return;
  }
  if (Date.now() - lastHealthCheckMs >= lifecycle.healthCheckIntervalMs) {
    await checkActiveTunnelHealth();
  }
}

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  if (maintenanceTimer) clearInterval(maintenanceTimer);
  clearShareRuntimeState();
  [candidateTunnel, activeTunnel].concat(retiredTunnels).forEach(stopTunnel);
  candidateTunnel = null;
  activeTunnel = null;
  retiredTunnels = [];
  process.exit(code);
}

async function main() {
  activeProvider = providerName();
  await ensureProtectedOrigin();
  initializeRuntimeState(activeProvider);
  console.log("Starting managed " + activeProvider + " tunnel lifecycle for http://127.0.0.1:" + requestedPort);
  console.log("Proactive renewal interval: " + lifecycle.rotationMinutes + " minutes");
  await rotateTunnel("startup");
  const maintenanceIntervalMs = Math.min(15000, lifecycle.healthCheckIntervalMs);
  maintenanceTimer = setInterval(function () { maintenanceTick(); }, maintenanceIntervalMs);
}

process.on("SIGINT", function () { shutdown(0); });
process.on("SIGTERM", function () { shutdown(0); });

main().catch(function (error) {
  console.error(error.message || error);
  shutdown(1);
});
