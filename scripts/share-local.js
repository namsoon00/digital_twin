const childProcess = require("child_process");
const crypto = require("crypto");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const requestedPort = Number(process.env.PORT || 3000);
const shareToken = String(process.env.SHARE_TOKEN || randomToken());
const requestedProvider = String(process.env.TUNNEL_PROVIDER || "").trim().toLowerCase();
const npxCommand = process.platform === "win32" ? "npx.cmd" : "npx";

let serverProcess = null;
let tunnelProcess = null;
let printedShareUrl = false;

function randomToken() {
  return crypto
    .randomBytes(18)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
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

function printShareUrl(rawUrl) {
  if (printedShareUrl) return;
  const baseUrl = rawUrl.replace(/[),.]+$/, "").replace(/\/$/, "");
  printedShareUrl = true;
  console.log("");
  console.log("External share URL:");
  console.log(baseUrl + "/?share_token=" + encodeURIComponent(shareToken));
  console.log("");
  console.log("Share token: " + shareToken);
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
  serverProcess = childProcess.spawn(process.env.PYTHON_BIN || "python3", ["python_service/service.py", "web"], {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"],
    env: Object.assign({}, process.env, {
      HOST: "127.0.0.1",
      PORT: String(requestedPort),
      SHARE_TOKEN: shareToken,
      LOCAL_CODEX_ENABLED: "0"
    })
  });

  const port = await waitForServer(serverProcess);
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
