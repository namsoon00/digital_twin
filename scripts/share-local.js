const childProcess = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const requestedPort = Number(process.env.PORT || 3000);
const requestedProvider = String(process.env.TUNNEL_PROVIDER || "").trim().toLowerCase();
const npxCommand = process.platform === "win32" ? "npx.cmd" : "npx";
const credentialsPath = path.resolve(process.env.SHARE_CREDENTIALS_PATH || path.join(rootDir, "data", "share-access.json"));
const shareCredentials = loadOrCreateShareCredentials();

let serverProcess = null;
let tunnelProcess = null;
let printedShareUrl = false;
let serverRestartTimer = null;
let shuttingDown = false;

function randomToken(bytes) {
  return crypto
    .randomBytes(bytes || 24)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
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
  const credentials = {
    viewToken: String(process.env.SHARE_VIEW_TOKEN || process.env.SHARE_TOKEN || saved.viewToken || randomToken()),
    ownerToken: String(process.env.SHARE_OWNER_TOKEN || saved.ownerToken || randomToken()),
    sessionSecret: String(process.env.SHARE_SESSION_SECRET || saved.sessionSecret || randomToken(32)),
    sessionDays: Math.max(1, Math.min(90, Number(process.env.SHARE_SESSION_DAYS || saved.sessionDays || 30) || 30)),
    createdAt: String(saved.createdAt || new Date().toISOString()),
    updatedAt: new Date().toISOString()
  };
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

function waitForServer(child) {
  return new Promise(function (resolve, reject) {
    let settled = false;
    let output = "";
    const timer = setTimeout(function () {
      if (settled) return;
      settled = true;
      reject(new Error("서버 시작 시간이 초과되었습니다."));
    }, 10000);

    function finish(port) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(port);
    }

    function read(chunk) {
      const text = chunk.toString();
      process.stdout.write(text);
      output += text;
      const match = output.match(/http:\/\/127\.0\.0\.1:(\d+)/);
      if (match) finish(Number(match[1]));
    }

    child.stdout.on("data", read);
    child.stderr.on("data", function (chunk) {
      process.stderr.write(chunk);
    });
    child.on("error", function (error) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.on("exit", function (code) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error("서버가 시작 전에 종료되었습니다. exit=" + code));
    });
  });
}

function startServer() {
  const child = childProcess.spawn(process.env.PYTHON_BIN || "python3", ["python_service/service.py", "web"], {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"],
    env: Object.assign({}, process.env, {
      HOST: "127.0.0.1",
      PORT: String(requestedPort),
      SHARE_TOKEN: shareCredentials.viewToken,
      SHARE_VIEW_TOKEN: shareCredentials.viewToken,
      SHARE_OWNER_TOKEN: shareCredentials.ownerToken,
      SHARE_SESSION_SECRET: shareCredentials.sessionSecret,
      SHARE_SESSION_DAYS: String(shareCredentials.sessionDays),
      LOCAL_CODEX_ENABLED: "0"
    })
  });
  serverProcess = child;
  return waitForServer(child).then(function (port) {
    child.on("exit", function (code, signal) {
      if (shuttingDown || serverProcess !== child) return;
      serverProcess = null;
      console.error("공유 웹 서버가 종료되었습니다. 재시작합니다. exit=" + code + " signal=" + (signal || "-"));
      serverRestartTimer = setTimeout(function () {
        serverRestartTimer = null;
        startServer().then(function (restartedPort) {
          if (restartedPort !== port) {
            console.error("공유 웹 서버 포트가 변경되어 터널을 유지할 수 없습니다.");
            shutdown(1);
            return;
          }
          console.log("공유 웹 서버가 다시 연결되었습니다: http://127.0.0.1:" + restartedPort);
        }).catch(function (error) {
          console.error(error.message || error);
          shutdown(1);
        });
      }, 1000);
    });
    return port;
  });
}

function printShareUrl(rawUrl) {
  if (printedShareUrl) return;
  const baseUrl = rawUrl.replace(/[),.]+$/, "").replace(/\/$/, "");
  printedShareUrl = true;
  console.log("");
  console.log("External viewer URL:");
  console.log(baseUrl + "/?share_token=" + encodeURIComponent(shareCredentials.viewToken));
  console.log("");
  console.log("External owner URL:");
  console.log(baseUrl + "/?owner_token=" + encodeURIComponent(shareCredentials.ownerToken));
  console.log("");
  console.log("Share credentials: " + credentialsPath);
  console.log("Signed browser session: " + shareCredentials.sessionDays + " days");
  console.log("Local Codex is disabled for this shared session. Press Ctrl+C to stop.");
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
    const matches = text.match(config.urlPattern);
    if (matches && matches.length) printShareUrl(matches[0]);
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
  shuttingDown = true;
  if (serverRestartTimer) {
    clearTimeout(serverRestartTimer);
    serverRestartTimer = null;
  }
  if (tunnelProcess) {
    tunnelProcess.removeAllListeners("exit");
    tunnelProcess.kill("SIGTERM");
    tunnelProcess = null;
  }
  if (serverProcess) {
    serverProcess.kill("SIGTERM");
    serverProcess = null;
  }
  process.exit(code);
}

async function main() {
  const provider = providerName();
  const port = await startServer();
  console.log("Starting " + provider + " tunnel for http://127.0.0.1:" + port);
  startTunnel(provider, port);
}

process.on("SIGINT", function () {
  shutdown(0);
});
process.on("SIGTERM", function () {
  shutdown(0);
});

main().catch(function (error) {
  console.error(error.message || error);
  shutdown(1);
});
