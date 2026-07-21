const childProcess = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const host = process.env.HOST || "127.0.0.1";
const port = Number(process.env.PORT || 3000);
const pythonBin = process.env.PYTHON_BIN || "python3";
const logPath = path.join(rootDir, "data", "python-web.log");
const restartLockPath = path.join(rootDir, "data", "python-web-restart.lock");

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

function processIsRunning(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (_error) {
    return false;
  }
}

function acquireRestartLock() {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const descriptor = fs.openSync(restartLockPath, "wx");
      fs.writeFileSync(descriptor, JSON.stringify({ pid: process.pid, startedAt: new Date().toISOString() }) + "\n", "utf8");
      return descriptor;
    } catch (error) {
      if (!error || error.code !== "EEXIST") throw error;
      let activePid = 0;
      try {
        const existing = JSON.parse(fs.readFileSync(restartLockPath, "utf8"));
        activePid = Number(existing && existing.pid);
      } catch (_readError) {}
      if (processIsRunning(activePid)) {
        throw new Error("another web restart is already in progress (pid " + activePid + ")");
      }
      fs.unlinkSync(restartLockPath);
    }
  }
  throw new Error("could not acquire the web restart lock");
}

function releaseRestartLock(descriptor) {
  try {
    fs.closeSync(descriptor);
  } catch (_error) {}
  try {
    fs.unlinkSync(restartLockPath);
  } catch (_error) {}
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
  const restartLock = acquireRestartLock();
  try {
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
  } finally {
    releaseRestartLock(restartLock);
  }
}

main().catch((error) => {
  console.error(JSON.stringify({ status: "error", message: String(error && error.message ? error.message : error) }));
  process.exit(1);
});
