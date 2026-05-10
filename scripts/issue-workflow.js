const childProcess = require("child_process");

const mode = process.argv[2] || "watch";
const issueNumber = process.argv[3];
const label = process.env.ISSUE_WORK_LABEL || "local-work";
const configuredPollIntervalMs = Number(process.env.ISSUE_WATCH_INTERVAL_MS || 60000);
const pollIntervalMs = isFinite(configuredPollIntervalMs) && configuredPollIntervalMs > 0 ? configuredPollIntervalMs : 60000;

function run(command, args, options) {
  const result = childProcess.spawnSync(command, args, Object.assign({ encoding: "utf8" }, options || {}));
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
    throw new Error(output || command + " failed with exit code " + result.status);
  }
  return String(result.stdout || "").trim();
}

function ensureGh() {
  try {
    run("gh", ["--version"], { stdio: "pipe" });
  } catch (error) {
    throw new Error("GitHub CLI가 필요합니다. `gh auth login`으로 인증한 뒤 다시 실행하세요.");
  }
}

function fetchIssue(number) {
  const raw = run("gh", [
    "issue",
    "view",
    String(number),
    "--json",
    "number,title,body,url,labels,comments,updatedAt"
  ]);
  return JSON.parse(raw);
}

function listIssues() {
  const raw = run("gh", [
    "issue",
    "list",
    "--state",
    "open",
    "--label",
    label,
    "--json",
    "number,title,updatedAt,url,labels"
  ]);
  return JSON.parse(raw);
}

function commentIssue(number, body) {
  run("gh", ["issue", "comment", String(number), "--body", body]);
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
      const author = comment.author && comment.author.login ? comment.author.login : "unknown";
      console.log("");
      console.log("[" + comment.createdAt + "] " + author);
      console.log(comment.body || "(empty)");
    });
  }
}

function claimIssue(number) {
  const issue = fetchIssue(number);
  printIssue(issue);
  commentIssue(
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

function doneIssue(number, summaryArgs) {
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
  commentIssue(number, body);
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

function watchIssues(once) {
  const seen = {};

  function tick(firstRun) {
    const issues = listIssues();
    if (!issues.length && firstRun) {
      console.log("No open issues with label `" + label + "`.");
    } else {
      printChangedIssues(issues, seen, firstRun);
    }
    if (once) return;
    console.log("");
    console.log("Watching GitHub issues labeled `" + label + "` every " + Math.round(pollIntervalMs / 1000) + "s. Press Ctrl+C to stop.");
  }

  tick(true);
  if (once) return;
  setInterval(function () {
    try {
      tick(false);
    } catch (error) {
      console.error(error.message || error);
    }
  }, pollIntervalMs);
}

function usage() {
  console.log("Usage:");
  console.log("  npm run issue:list");
  console.log("  npm run issue:watch");
  console.log("  npm run issue:claim -- <issue-number>");
  console.log("  npm run issue:done -- <issue-number> \"summary\"");
}

try {
  ensureGh();

  if (mode === "list") {
    watchIssues(true);
  } else if (mode === "watch") {
    watchIssues(false);
  } else if (mode === "claim" && issueNumber) {
    claimIssue(issueNumber);
  } else if (mode === "done" && issueNumber) {
    doneIssue(issueNumber, process.argv.slice(4));
  } else {
    usage();
    process.exitCode = 1;
  }
} catch (error) {
  console.error(error.message || error);
  process.exitCode = 1;
}
