const childProcess = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const requestedPort = Number(process.env.PORT || 3000);
const requestedProvider = String(process.env.TUNNEL_PROVIDER || "").trim().toLowerCase();
const fixedEntryUrl = normalizeFixedEntryUrl(process.env.SHARE_FIXED_ENTRY_URL || "https://namsoon00.github.io/digital_twin/live/");
const publishTargetEnabled = !["0", "false", "no", "off"].includes(String(process.env.SHARE_PUBLISH_TARGET || "1").trim().toLowerCase());
const npxCommand = process.platform === "win32" ? "npx.cmd" : "npx";
const credentialsPath = path.resolve(process.env.SHARE_CREDENTIALS_PATH || path.join(rootDir, "data", "share-access.json"));
const runtimeStatePath = path.resolve(process.env.SHARE_RUNTIME_STATE_PATH || path.join(rootDir, "data", "share-runtime.json"));
const publisherPath = path.join(rootDir, "scripts", "publish-live-target.js");
const shareCredentials = loadOrCreateShareCredentials();

let tunnelProcess = null;
let printedShareUrl = false;
let shuttingDown = false;
let activeProvider = "";
let runtimeState = {};
let tunnelOutput = "";

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
      if (!status || status.enabled !== true) {
        throw new Error("원본 서버에 공유 인증이 활성화되지 않았습니다.");
      }
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
  runtimeState = Object.assign({}, runtimeState, patch || {}, {
    updatedAt: new Date().toISOString()
  });
  fs.mkdirSync(path.dirname(runtimeStatePath), { recursive: true, mode: 0o700 });
  const temporaryPath = runtimeStatePath + ".tmp-" + process.pid;
  fs.writeFileSync(temporaryPath, JSON.stringify(runtimeState, null, 2) + "\n", { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temporaryPath, runtimeStatePath);
  fs.chmodSync(runtimeStatePath, 0o600);
}

function clearShareRuntimeState() {
  try {
    const state = JSON.parse(fs.readFileSync(runtimeStatePath, "utf8"));
    if (Number(state.ownerPid || 0) === process.pid) fs.unlinkSync(runtimeStatePath);
  } catch (error) {
    if (!error || error.code !== "ENOENT") console.error("공유 URL 런타임 상태를 정리하지 못했습니다: " + (error.message || error));
  }
}

function publishLiveTarget() {
  if (!publishTargetEnabled) {
    writeShareRuntimeState({ targetPublishStatus: "disabled" });
    return;
  }
  writeShareRuntimeState({ targetPublishStatus: "publishing", targetPublishError: "" });
  const publisher = childProcess.spawn(process.execPath, [publisherPath], {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"],
    env: Object.assign({}, process.env, {
      SHARE_RUNTIME_STATE_PATH: runtimeStatePath
    })
  });
  let output = "";
  let errorOutput = "";
  publisher.stdout.on("data", function (chunk) { output += chunk.toString(); });
  publisher.stderr.on("data", function (chunk) { errorOutput += chunk.toString(); });
  publisher.on("exit", function (code) {
    if (shuttingDown) return;
    if (code === 0) {
      writeShareRuntimeState({
        targetPublishStatus: "published",
        targetPublishedAt: new Date().toISOString(),
        targetPublishError: ""
      });
      console.log("Fixed entry target published: " + fixedEntryUrl);
      return;
    }
    const detail = String(errorOutput || output || "publish failed").trim().slice(-500);
    writeShareRuntimeState({ targetPublishStatus: "failed", targetPublishError: detail });
    console.error("고정 진입 주소 갱신 실패: " + detail);
  });
}

function printShareUrl(rawUrl) {
  if (printedShareUrl) return;
  const baseUrl = rawUrl.replace(/[),.]+$/, "").replace(/\/$/, "");
  const viewerUrl = baseUrl + "/?share_token=" + encodeURIComponent(shareCredentials.viewToken);
  const ownerUrl = baseUrl + "/?owner_token=" + encodeURIComponent(shareCredentials.ownerToken);
  runtimeState = {
    version: 2,
    provider: activeProvider,
    baseUrl,
    viewerUrl,
    ownerUrl,
    fixedEntryUrl,
    fixedViewerUrl: fixedAccessUrl("share_token", shareCredentials.viewToken),
    fixedOwnerUrl: fixedAccessUrl("owner_token", shareCredentials.ownerToken),
    ownerPid: process.pid,
    tunnelPid: tunnelProcess && tunnelProcess.pid ? tunnelProcess.pid : 0,
    targetPublishStatus: publishTargetEnabled ? "waiting" : "disabled"
  };
  writeShareRuntimeState();
  printedShareUrl = true;
  console.log("");
  console.log("Cloudflare tunnel: " + baseUrl);
  console.log("Fixed entry: " + fixedEntryUrl);
  console.log("Origin: http://127.0.0.1:" + requestedPort);
  console.log("Access links are available to the local owner in Settings > Operations.");
  console.log("");
  publishLiveTarget();
}

function tunnelArgs(provider, port) {
  if (provider === "cloudflared") {
    return {
      command: "cloudflared",
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

function startTunnel(provider, port) {
  const config = tunnelArgs(provider, port);
  tunnelProcess = childProcess.spawn(config.command, config.args, {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"]
  });

  function read(chunk) {
    const text = chunk.toString();
    process.stdout.write(text);
    tunnelOutput = (tunnelOutput + text).slice(-16000);
    const matches = tunnelOutput.match(config.urlPattern);
    if (matches && matches.length) printShareUrl(matches[matches.length - 1]);
  }

  tunnelProcess.stdout.on("data", read);
  tunnelProcess.stderr.on("data", read);
  tunnelProcess.on("error", function (error) {
    console.error(error.message || error);
    shutdown(1);
  });
  tunnelProcess.on("exit", function (code) {
    if (code !== 0 && code !== null) console.error("터널 프로세스가 종료되었습니다. exit=" + code);
    shutdown(code || 0);
  });
}

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  clearShareRuntimeState();
  if (tunnelProcess) {
    tunnelProcess.removeAllListeners("exit");
    tunnelProcess.kill("SIGTERM");
    tunnelProcess = null;
  }
  process.exit(code);
}

async function main() {
  const provider = providerName();
  activeProvider = provider;
  await ensureProtectedOrigin();
  console.log("Starting " + provider + " tunnel for the managed origin http://127.0.0.1:" + requestedPort);
  startTunnel(provider, requestedPort);
}

process.on("SIGINT", function () { shutdown(0); });
process.on("SIGTERM", function () { shutdown(0); });

main().catch(function (error) {
  console.error(error.message || error);
  shutdown(1);
});
