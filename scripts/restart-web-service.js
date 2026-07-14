const childProcess = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const host = process.env.HOST || "127.0.0.1";
const port = Number(process.env.PORT || 3000);
const pythonBin = process.env.PYTHON_BIN || "python3";
const logPath = path.join(rootDir, "data", "python-web.log");

function commandOutput(command, args, options) {
  try {
    return childProcess.execFileSync(command, args, Object.assign({ encoding: "utf8" }, options || {}));
  } catch (_error) {
    return "";
  }
}

function cwdForPid(pid) {
  const output = commandOutput("lsof", ["-a", "-p", String(pid), "-d", "cwd"]);
  const lines = output.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) return "";
  const columns = lines[lines.length - 1].trim().split(/\s+/);
  return columns[columns.length - 1] || "";
}

function webPids() {
  const output = commandOutput("pgrep", ["-f", "python_service/service.py web"]);
  return output
    .split(/\s+/)
    .map((value) => Number(value))
    .filter((pid) => Number.isFinite(pid) && pid > 0)
    .filter((pid) => cwdForPid(pid) === rootDir);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function stopWebProcesses() {
  const pids = webPids();
  pids.forEach((pid) => {
    try {
      process.kill(pid, "SIGTERM");
    } catch (_error) {}
  });
  await sleep(1200);
  webPids().forEach((pid) => {
    try {
      process.kill(pid, "SIGKILL");
    } catch (_error) {}
  });
  return pids;
}

function requestBootstrap() {
  return new Promise((resolve, reject) => {
    const request = http.get({ host, port, path: "/api/bootstrap", timeout: 2000 }, (response) => {
      response.resume();
      response.on("end", () => resolve(response.statusCode || 0));
    });
    request.on("timeout", () => request.destroy(new Error("web bootstrap timeout")));
    request.on("error", reject);
  });
}

async function waitForWeb() {
  const deadline = Date.now() + 10000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const statusCode = await requestBootstrap();
      if (statusCode >= 200 && statusCode < 500) return statusCode;
    } catch (error) {
      lastError = error;
    }
    await sleep(250);
  }
  throw lastError || new Error("web server did not become ready");
}

async function main() {
  fs.mkdirSync(path.dirname(logPath), { recursive: true });
  const stopped = await stopWebProcesses();
  const log = fs.openSync(logPath, "a");
  const child = childProcess.spawn(pythonBin, ["-u", "python_service/service.py", "web", "--host", host, "--port", String(port)], {
    cwd: rootDir,
    detached: true,
    stdio: ["ignore", log, log],
    env: Object.assign({}, process.env, {
      HOST: host,
      PORT: String(port),
      ALLOW_PORT_FALLBACK: "0",
    }),
  });
  child.unref();
  fs.closeSync(log);
  const statusCode = await waitForWeb();
  console.log(JSON.stringify({
    status: "ok",
    stoppedPids: stopped,
    pid: child.pid,
    url: "http://" + host + ":" + port,
    bootstrapStatusCode: statusCode,
    logPath,
  }));
}

main().catch((error) => {
  console.error(JSON.stringify({ status: "error", message: String(error && error.message ? error.message : error) }));
  process.exit(1);
});
