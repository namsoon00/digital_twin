const childProcess = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const runtimeStatePath = path.resolve(process.env.SHARE_RUNTIME_STATE_PATH || path.join(rootDir, "data", "share-runtime.json"));

function command(command, args, options) {
  const result = childProcess.spawnSync(command, args, Object.assign({
    cwd: rootDir,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"]
  }, options || {}));
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = String(result.stderr || result.stdout || "").trim();
    throw new Error(command + " " + args.join(" ") + " failed: " + detail.slice(-800));
  }
  return String(result.stdout || "").trim();
}

function validTunnelBaseUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    const host = String(parsed.hostname || "").toLowerCase();
    return parsed.protocol === "https:"
      && !parsed.username
      && !parsed.password
      && !parsed.search
      && !parsed.hash
      && (host.endsWith(".trycloudflare.com") || host.endsWith(".loca.lt"));
  } catch (_error) {
    return false;
  }
}

function publicTargetPayload(state, revision, now) {
  const baseUrl = String(state && state.baseUrl || "").replace(/\/$/, "");
  if (!validTunnelBaseUrl(baseUrl)) throw new Error("공개 가능한 터널 주소가 런타임 상태에 없습니다.");
  const payload = {
    version: 1,
    status: "available",
    provider: String(state.provider || "cloudflared"),
    baseUrl,
    revision: String(revision || "unknown"),
    updatedAt: String(now || new Date().toISOString())
  };
  if (Object.keys(payload).some(function (key) { return /token|secret|credential/i.test(key); })) {
    throw new Error("공개 대상 파일에 자격 증명 필드가 포함되었습니다.");
  }
  return payload;
}

function readRuntimeState() {
  let payload = {};
  try {
    payload = JSON.parse(fs.readFileSync(runtimeStatePath, "utf8"));
  } catch (error) {
    if (!error || error.code !== "ENOENT") throw error;
  }
  if (!payload || typeof payload !== "object") throw new Error("공유 런타임 상태 형식이 올바르지 않습니다.");
  const targetBaseUrl = String(process.env.SHARE_TARGET_BASE_URL || "").trim();
  if (targetBaseUrl) payload.baseUrl = targetBaseUrl;
  const targetProvider = String(process.env.SHARE_TARGET_PROVIDER || "").trim();
  if (targetProvider) payload.provider = targetProvider;
  return payload;
}

function removeWorktree(worktreePath) {
  try {
    childProcess.spawnSync("git", ["worktree", "remove", "--force", worktreePath], {
      cwd: rootDir,
      stdio: "ignore"
    });
  } catch (_error) {}
  try {
    fs.rmSync(worktreePath, { recursive: true, force: true });
  } catch (_error) {}
}

function publishOnce(payload) {
  command("git", ["fetch", "origin", "gh-pages"]);
  command("git", ["rev-parse", "--verify", "origin/gh-pages"]);
  const worktreePath = fs.mkdtempSync(path.join(os.tmpdir(), "orbit-alpha-live-target-"));
  fs.rmSync(worktreePath, { recursive: true, force: true });
  try {
    command("git", ["worktree", "add", "--detach", worktreePath, "origin/gh-pages"]);
    fs.writeFileSync(
      path.join(worktreePath, "live-target.json"),
      JSON.stringify(payload, null, 2) + "\n",
      "utf8"
    );
    command("git", ["-C", worktreePath, "add", "live-target.json"]);
    const diff = childProcess.spawnSync("git", ["-C", worktreePath, "diff", "--cached", "--quiet"], {
      cwd: rootDir,
      stdio: "ignore"
    });
    if (diff.status === 0) return { status: "unchanged", revision: payload.revision };
    if (diff.status !== 1) throw new Error("live-target.json 변경 상태를 확인하지 못했습니다.");
    command("git", [
      "-C", worktreePath,
      "-c", "user.name=orbit-alpha-runtime",
      "-c", "user.email=orbit-alpha-runtime@users.noreply.github.com",
      "commit", "-m", "Update live preview target"
    ]);
    command("git", ["-C", worktreePath, "push", "origin", "HEAD:gh-pages"]);
    return { status: "published", revision: payload.revision };
  } finally {
    removeWorktree(worktreePath);
  }
}

function publish(payload) {
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      return publishOnce(payload);
    } catch (error) {
      lastError = error;
      if (attempt >= 3) break;
    }
  }
  throw lastError || new Error("고정 진입 주소를 갱신하지 못했습니다.");
}

function main() {
  const state = readRuntimeState();
  const revision = command("git", ["rev-parse", "--short=12", "HEAD"]) || "unknown";
  const payload = publicTargetPayload(state, revision);
  const result = publish(payload);
  process.stdout.write(JSON.stringify(result) + "\n");
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    process.stderr.write(String(error && error.message || error) + "\n");
    process.exitCode = 1;
  }
}

module.exports = {
  publicTargetPayload,
  validTunnelBaseUrl
};
