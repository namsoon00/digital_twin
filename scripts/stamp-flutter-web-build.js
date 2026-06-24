#!/usr/bin/env node

const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const buildDir = path.resolve(rootDir, process.argv[2] || "mobile/build/web");
const mobileDir = path.join(rootDir, "mobile");
const indexPath = path.join(buildDir, "index.html");
const bootstrapPath = path.join(buildDir, "flutter_bootstrap.js");
const versionPath = path.join(buildDir, "version.json");

function git(args) {
  try {
    return childProcess.execFileSync("git", args, {
      cwd: rootDir,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"]
    }).trim();
  } catch (error) {
    return "";
  }
}

function sanitizeVersion(value) {
  const cleaned = String(value || "")
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 160);
  return cleaned || "local";
}

function currentBuildVersion() {
  const explicit = process.env.WEB_BUILD_VERSION || process.env.BUILD_VERSION;
  if (explicit) return sanitizeVersion(explicit);

  const sha = process.env.GITHUB_SHA || git(["rev-parse", "--short=12", "HEAD"]) || "local";
  const runNumber = process.env.GITHUB_RUN_NUMBER;
  const runAttempt = process.env.GITHUB_RUN_ATTEMPT;
  const timestamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
  return sanitizeVersion(
    [sha.slice(0, 12), runNumber && `run${runNumber}`, runAttempt && `attempt${runAttempt}`, timestamp]
      .filter(Boolean)
      .join("-")
  );
}

function readText(filePath) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`${path.relative(rootDir, filePath)} does not exist. Run flutter build web first.`);
  }
  return fs.readFileSync(filePath, "utf8");
}

function writeText(filePath, content) {
  fs.writeFileSync(filePath, content, "utf8");
}

function withBuildQuery(resource, buildVersion) {
  const hashIndex = resource.indexOf("#");
  const hash = hashIndex >= 0 ? resource.slice(hashIndex) : "";
  const withoutHash = hashIndex >= 0 ? resource.slice(0, hashIndex) : resource;
  const queryIndex = withoutHash.indexOf("?");
  const pathname = queryIndex >= 0 ? withoutHash.slice(0, queryIndex) : withoutHash;
  const query = queryIndex >= 0 ? withoutHash.slice(queryIndex + 1) : "";
  const params = new URLSearchParams(query);
  params.set("v", buildVersion);
  return `${pathname}?${params.toString()}${hash}`;
}

function cacheBustHtmlAssetUrls(html, buildVersion) {
  const cacheBustedAssets = new Set([
    "flutter_bootstrap.js",
    "manifest.json",
    "favicon.png",
    "icons/Icon-192.png"
  ]);

  return html.replace(/\b(src|href)="([^"]+)"/g, function (match, attribute, value) {
    const barePath = value.split(/[?#]/)[0].replace(/^\.\//, "");
    if (!cacheBustedAssets.has(barePath)) return match;
    return `${attribute}="${withBuildQuery(value, buildVersion)}"`;
  });
}

function upsertBuildVersionMeta(html, buildVersion) {
  const tag = `<meta name="build-version" content="${buildVersion}">`;
  if (/<meta\s+name="build-version"\s+content="[^"]*"\s*\/?>/i.test(html)) {
    return html.replace(/<meta\s+name="build-version"\s+content="[^"]*"\s*\/?>/i, tag);
  }
  return html.replace(/(<meta\s+name="description"\s+content="[^"]*"\s*\/?>)/i, `$1\n  ${tag}`);
}

function upsertHttpEquivMeta(html, httpEquiv, content) {
  const tag = `<meta http-equiv="${httpEquiv}" content="${content}">`;
  const pattern = new RegExp(`<meta\\s+http-equiv="${httpEquiv}"\\s+content="[^"]*"\\s*\\/?>`, "i");
  if (pattern.test(html)) return html.replace(pattern, tag);
  return html.replace(/(<meta\s+name="build-version"\s+content="[^"]*"\s*\/?>)/i, `$1\n  ${tag}`);
}

function stampIndexHtml(buildVersion) {
  let html = readText(indexPath);
  html = upsertBuildVersionMeta(html, buildVersion);
  html = upsertHttpEquivMeta(html, "Cache-Control", "no-cache, no-store, must-revalidate");
  html = upsertHttpEquivMeta(html, "Pragma", "no-cache");
  html = upsertHttpEquivMeta(html, "Expires", "0");
  html = cacheBustHtmlAssetUrls(html, buildVersion);
  writeText(indexPath, html);
}

function stampBootstrap(buildVersion) {
  const mainPath = withBuildQuery("main.dart.js", buildVersion);
  let replaced = 0;
  const bootstrap = readText(bootstrapPath).replace(
    /("mainJsPath"\s*:\s*")main\.dart\.js(?:\?[^"]*)?(")/g,
    function (match, prefix, suffix) {
      replaced += 1;
      return `${prefix}${mainPath}${suffix}`;
    }
  );

  if (replaced === 0) {
    throw new Error("Could not find main.dart.js in flutter_bootstrap.js.");
  }

  writeText(bootstrapPath, bootstrap);
}

function readPubspecVersion() {
  const pubspecPath = path.join(mobileDir, "pubspec.yaml");
  const pubspec = fs.existsSync(pubspecPath) ? fs.readFileSync(pubspecPath, "utf8") : "";
  const match = pubspec.match(/^version:\s*([^\s#]+)/m);
  const fullVersion = match ? match[1] : "";
  const parts = fullVersion.split("+");
  return {
    version: parts[0] || "",
    buildNumber: parts[1] || ""
  };
}

function readExistingVersionJson() {
  if (!fs.existsSync(versionPath)) return {};
  try {
    return JSON.parse(fs.readFileSync(versionPath, "utf8"));
  } catch (error) {
    return {};
  }
}

function writeVersionJson(buildVersion) {
  const pubspecVersion = readPubspecVersion();
  const existingVersion = readExistingVersionJson();
  const buildNumber =
    process.env.WEB_BUILD_NUMBER || process.env.GITHUB_RUN_NUMBER || existingVersion.build_number || pubspecVersion.buildNumber;
  const payload = {
    app_name: existingVersion.app_name || "market_flow",
    version: existingVersion.version || pubspecVersion.version,
    build_number: buildNumber,
    package_name: existingVersion.package_name || "market_flow",
    build_version: buildVersion,
    git_sha: process.env.GITHUB_SHA || git(["rev-parse", "HEAD"]),
    built_at: new Date().toISOString()
  };
  writeText(versionPath, `${JSON.stringify(payload, null, 2)}\n`);
}

const buildVersion = currentBuildVersion();
stampIndexHtml(buildVersion);
stampBootstrap(buildVersion);
writeVersionJson(buildVersion);

console.log(`Stamped Flutter web build ${path.relative(rootDir, buildDir)} with ${buildVersion}`);
