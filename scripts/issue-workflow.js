const childProcess = require("child_process");
const fs = require("fs");
const https = require("https");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
loadEnv(".env");
loadEnv(".env.local");

const mode = process.argv[2] || "watch";
const issueNumber = process.argv[3];
const label = process.env.ISSUE_WORK_LABEL || "local-work";
const configuredPollIntervalMs = Number(process.env.ISSUE_WATCH_INTERVAL_MS || 60000);
const pollIntervalMs = isFinite(configuredPollIntervalMs) && configuredPollIntervalMs > 0 ? configuredPollIntervalMs : 60000;
const repoFullName = process.env.ISSUE_REPOSITORY || process.env.GITHUB_REPOSITORY || remoteRepo();
const token = process.env.GITHUB_TOKEN || process.env.GH_TOKEN || "";

function loadEnv(fileName) {
  const envPath = path.join(rootDir, fileName);
  if (!fs.existsSync(envPath)) return;

  fs.readFileSync(envPath, "utf8")
    .split(/\r?\n/)
    .forEach(function (line) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.charAt(0) === "#") return;
      const index = trimmed.indexOf("=");
      if (index < 0) return;
      const key = trimmed.slice(0, index).trim();
      const value = trimmed.slice(index + 1).trim().replace(/^["']|["']$/g, "");
      if (!process.env[key]) process.env[key] = value;
    });
}

function run(command, args, options) {
  const result = childProcess.spawnSync(command, args, Object.assign({ encoding: "utf8" }, options || {}));
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
    throw new Error(output || command + " failed with exit code " + result.status);
  }
  return String(result.stdout || "").trim();
}

function commandExists(command) {
  const result = childProcess.spawnSync(command, ["--version"], { stdio: "ignore" });
  return !result.error && result.status === 0;
}

function remoteRepo() {
  try {
    const remote = run("git", ["remote", "get-url", "origin"]);
    const sshMatch = remote.match(/github\.com[:/]([^/]+\/[^/.]+)(?:\.git)?$/);
    if (sshMatch) return sshMatch[1];
  } catch (error) {
    return "";
  }
  return "";
}

function requestJson(method, apiPath, payload) {
  if (!repoFullName) throw new Error("GitHub repository를 찾지 못했습니다. ISSUE_REPOSITORY=owner/repo를 설정하세요.");

  const body = payload ? JSON.stringify(payload) : "";
  const headers = {
    "Accept": "application/vnd.github+json",
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
    "User-Agent": "digital-twin-issue-workflow",
    "X-GitHub-Api-Version": "2022-11-28"
  };
  if (token) headers.Authorization = "Bearer " + token;

  return new Promise(function (resolve, reject) {
    const req = https.request(
      {
        hostname: "api.github.com",
        method: method,
        path: apiPath,
        headers: headers
      },
      function (res) {
        let raw = "";
        res.setEncoding("utf8");
        res.on("data", function (chunk) {
          raw += chunk;
        });
        res.on("end", function () {
          let parsed = null;
          if (raw) {
            try {
              parsed = JSON.parse(raw);
            } catch (error) {
              return reject(error);
            }
          }

          if (res.statusCode < 200 || res.statusCode >= 300) {
            const message = parsed && parsed.message ? parsed.message : "GitHub API 요청 실패";
            return reject(new Error(message + " (" + res.statusCode + ")"));
          }
          resolve(parsed);
        });
      }
    );

    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function fetchIssue(number) {
  const issue = await requestJson("GET", "/repos/" + repoFullName + "/issues/" + number);
  const comments = await requestJson("GET", "/repos/" + repoFullName + "/issues/" + number + "/comments?per_page=50");
  return {
    number: issue.number,
    title: issue.title,
    body: issue.body,
    url: issue.html_url,
    labels: issue.labels || [],
    comments: comments || [],
    updatedAt: issue.updated_at
  };
}

async function listIssues() {
  const issues = await requestJson(
    "GET",
    "/repos/" + repoFullName + "/issues?state=open&labels=" + encodeURIComponent(label) + "&per_page=30"
  );
  return (issues || [])
    .filter(function (issue) { return !issue.pull_request; })
    .map(function (issue) {
      return {
        number: issue.number,
        title: issue.title,
        updatedAt: issue.updated_at,
        url: issue.html_url,
        labels: issue.labels || []
      };
    });
}

async function commentIssue(number, body) {
  if (token) {
    await requestJson("POST", "/repos/" + repoFullName + "/issues/" + number + "/comments", { body: body });
    return;
  }

  if (commandExists("gh")) {
    run("gh", ["issue", "comment", String(number), "--body", body]);
    return;
  }

  throw new Error("이슈 댓글에는 인증이 필요합니다. .env.local에 GITHUB_TOKEN을 넣거나 `gh auth login`을 실행하세요.");
}

function shortSha() {
  return run("git", ["rev-parse", "--short", "HEAD"]);
}

function labelNames(labels) {
  return (labels || []).map(function (entry) { return entry.name; }).filter(Boolean).join(", ");
}

function printIssue(issue) {
  console.log("#" + issue.number + " " + issue.title);
  console.log(issue.url);
  console.log("labels: " + (labelNames(issue.labels) || "-"));
  console.log("updated: " + issue.updatedAt);
  console.log("");
  console.log(issue.body || "(no body)");

  const comments = issue.comments || [];
  if (comments.length) {
    console.log("");
    console.log("Recent comments:");
    comments.slice(-5).forEach(function (comment) {
      const author = comment.user && comment.user.login ? comment.user.login : "unknown";
      console.log("");
      console.log("[" + comment.created_at + "] " + author);
      console.log(comment.body || "(empty)");
    });
  }
}

async function claimIssue(number) {
  const issue = await fetchIssue(number);
  printIssue(issue);
  await commentIssue(
    number,
    [
      "로컬에서 작업 시작합니다.",
      "",
      "- 기준 브랜치: main",
      "- 검증 예정: npm test",
      "- 완료 후 origin/main 푸시 및 로컬 서버 재시작"
    ].join("\n")
  );
  console.log("");
  console.log("Claim comment posted for issue #" + number + ".");
}

async function doneIssue(number, summaryArgs) {
  const summary = summaryArgs.length ? summaryArgs.join(" ") : "이슈 요구사항을 반영했습니다.";
  const body = [
    "작업 완료했습니다.",
    "",
    "- 커밋: " + shortSha(),
    "- 푸시: origin/main",
    "- 검증: npm test 통과",
    "- 로컬 서버: http://127.0.0.1:3000",
    "",
    "변경 요약:",
    "- " + summary
  ].join("\n");
  await commentIssue(number, body);
  console.log("Done comment posted for issue #" + number + ".");
}

function printChangedIssues(issues, seen, firstRun) {
  issues.forEach(function (issue) {
    const key = String(issue.number);
    const previous = seen[key];
    seen[key] = issue.updatedAt;
    if (!firstRun && previous === issue.updatedAt) return;

    console.log("");
    console.log((firstRun ? "Open issue" : "Updated issue") + ": #" + issue.number + " " + issue.title);
    console.log(issue.url);
    console.log("labels: " + (labelNames(issue.labels) || "-"));
    console.log("updated: " + issue.updatedAt);
  });
}

async function watchIssues(once) {
  const seen = {};

  async function tick(firstRun) {
    const issues = await listIssues();
    if (!issues.length && firstRun) {
      console.log("No open issues with label `" + label + "` in " + repoFullName + ".");
    } else {
      printChangedIssues(issues, seen, firstRun);
    }
    if (once) return;
    console.log("");
    console.log("Watching GitHub issues labeled `" + label + "` every " + Math.round(pollIntervalMs / 1000) + "s. Press Ctrl+C to stop.");
  }

  await tick(true);
  if (once) return;
  setInterval(function () {
    tick(false).catch(function (error) {
      console.error(error.message || error);
    });
  }, pollIntervalMs);
}

function usage() {
  console.log("Usage:");
  console.log("  npm run issue:list");
  console.log("  npm run issue:watch");
  console.log("  npm run issue:claim -- <issue-number>");
  console.log("  npm run issue:done -- <issue-number> \"summary\"");
}

async function main() {
  if (mode === "list") {
    await watchIssues(true);
  } else if (mode === "watch") {
    await watchIssues(false);
  } else if (mode === "claim" && issueNumber) {
    await claimIssue(issueNumber);
  } else if (mode === "done" && issueNumber) {
    await doneIssue(issueNumber, process.argv.slice(4));
  } else {
    usage();
    process.exitCode = 1;
  }
}

main().catch(function (error) {
  console.error(error.message || error);
  process.exitCode = 1;
});
