#!/usr/bin/env node

const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
process.chdir(rootDir);

const dataDir = path.join(rootDir, "data");
const pidPath = path.join(dataDir, "notify-realtime.pid");
const logPath = path.join(dataDir, "notify-realtime.log");
const command = String(process.argv[2] || "status").toLowerCase();

function ensureDataDir() {
  if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });
}

function readPid() {
  try {
    const value = Number(fs.readFileSync(pidPath, "utf8").trim());
    return Number.isFinite(value) && value > 0 ? value : 0;
  } catch (error) {
    return 0;
  }
}

function commandForPid(pid) {
  try {
    return childProcess.execFileSync("ps", ["-p", String(pid), "-o", "command="], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"]
    }).trim();
  } catch (error) {
    return "";
  }
}

function isWorkerCommand(commandLine) {
  return commandLine.indexOf("notify-worker.js") >= 0 && commandLine.indexOf("--realtime-daemon") >= 0;
}

function isRunning(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
  } catch (error) {
    return false;
  }
  if (process.platform !== "win32") {
    const commandLine = commandForPid(pid);
    return Boolean(commandLine && isWorkerCommand(commandLine));
  }
  return true;
}

function removePidFile() {
  try {
    fs.unlinkSync(pidPath);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

function logStamp(label) {
  ensureDataDir();
  fs.appendFileSync(logPath, "\n[" + new Date().toISOString() + "] manager " + label + "\n", "utf8");
}

function tailLines(filePath, count) {
  try {
    const raw = fs.readFileSync(filePath, "utf8").trim();
    if (!raw) return [];
    const lines = raw.split(/\r?\n/);
    return lines.slice(Math.max(0, lines.length - count));
  } catch (error) {
    return [];
  }
}

function printStatus() {
  const pid = readPid();
  const running = isRunning(pid);
  console.log("Realtime notify worker: " + (running ? "running" : "stopped"));
  if (pid) console.log("PID: " + pid);
  if (running) console.log("Command: " + commandForPid(pid));
  if (fs.existsSync(logPath)) {
    const stat = fs.statSync(logPath);
    console.log("Log: " + logPath);
    console.log("Log updated: " + stat.mtime.toISOString());
    const lines = tailLines(logPath, 8);
    if (lines.length) {
      console.log("Recent log:");
      lines.forEach(function (line) {
        console.log(line);
      });
    }
  } else {
    console.log("Log: " + logPath + " (not created)");
  }
  if (pid && !running) removePidFile();
}

function start() {
  ensureDataDir();
  const existingPid = readPid();
  if (isRunning(existingPid)) {
    console.log("Realtime notify worker already running.");
    printStatus();
    return;
  }
  if (existingPid) removePidFile();

  logStamp("start");
  const out = fs.openSync(logPath, "a");
  const child = childProcess.spawn(process.execPath, [
    "scripts/notify-worker.js",
    "--realtime-daemon",
    "--timestamps"
  ], {
    cwd: rootDir,
    env: process.env,
    detached: true,
    stdio: ["ignore", out, out]
  });
  fs.writeFileSync(pidPath, String(child.pid) + "\n", {
    encoding: "utf8",
    mode: 0o600
  });
  child.unref();
  console.log("Realtime notify worker started. pid=" + child.pid);
  console.log("Log: " + logPath);
}

function delay(ms) {
  return new Promise(function (resolve) {
    setTimeout(resolve, ms);
  });
}

async function stop() {
  const pid = readPid();
  if (!pid) {
    console.log("Realtime notify worker is not running.");
    return;
  }
  if (!isRunning(pid)) {
    removePidFile();
    console.log("Realtime notify worker was not running. Removed stale pid file.");
    return;
  }

  process.kill(pid, "SIGTERM");
  for (let index = 0; index < 25; index += 1) {
    await delay(200);
    if (!isRunning(pid)) {
      removePidFile();
      logStamp("stop");
      console.log("Realtime notify worker stopped. pid=" + pid);
      return;
    }
  }

  process.kill(pid, "SIGKILL");
  removePidFile();
  logStamp("kill");
  console.log("Realtime notify worker killed. pid=" + pid);
}

async function restart() {
  await stop();
  start();
}

async function main() {
  if (command === "start") {
    start();
  } else if (command === "stop") {
    await stop();
  } else if (command === "restart") {
    await restart();
  } else if (command === "status") {
    printStatus();
  } else {
    console.log("Usage: node scripts/notify-realtime-service.js start|stop|restart|status");
    process.exitCode = 1;
  }
}

main().catch(function (error) {
  console.error(error.message || error);
  process.exitCode = 1;
});
