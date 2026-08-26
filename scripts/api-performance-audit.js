const http = require("http");
const https = require("https");
const zlib = require("zlib");

const baseUrl = String(process.env.API_PERFORMANCE_BASE_URL || "http://127.0.0.1:3000").replace(/\/$/, "");
const timeoutMs = Math.max(1000, Number(process.env.API_PERFORMANCE_TIMEOUT_MS || 20000));
const concurrency = Math.max(1, Math.min(8, Number(process.env.API_PERFORMANCE_CONCURRENCY || 3)));
const strict = process.env.API_PERFORMANCE_STRICT === "1";
const token = String(process.env.API_PERFORMANCE_SHARE_TOKEN || "").trim();
const defaultPaths = [
  "/api/version",
  "/api/dashboard/summary?accountId=default",
  "/api/portfolio/positions?accountId=default",
  "/api/market/instruments",
  "/api/market/evidence?limit=8",
  "/api/decisions?accountId=default&limit=40",
  "/api/operations/health",
  "/api/external-data/status",
  "/api/investment-model",
  "/api/ontology/catalog/summary",
  "/api/ontology/catalog/rules?limit=20",
  "/api/ontology/experiments/status",
  "/api/investment-brain/hypotheses?view=summary&limit=20",
  "/api/investment-brain/episodes?limit=20",
  "/api/investment-brain/research-runs?limit=20"
];
const requestedPaths = process.argv.slice(2);
const paths = requestedPaths.length ? requestedPaths : defaultPaths;

function percentile(values, p) {
  const sorted = values.slice().sort((a, b) => a - b);
  if (!sorted.length) return 0;
  return sorted[Math.max(0, Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1))];
}

function request(pathname) {
  return new Promise((resolve) => {
    const url = new URL(pathname, baseUrl);
    if (token && !url.searchParams.has("share_token")) url.searchParams.set("share_token", token);
    const client = url.protocol === "https:" ? https : http;
    const startedAt = process.hrtime.bigint();
    const request = client.get(url, {
      headers: { Accept: "application/json", "Accept-Encoding": "gzip", Connection: "close" },
      timeout: timeoutMs
    }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        const wire = Buffer.concat(chunks);
        let raw = wire;
        try {
          if (String(response.headers["content-encoding"] || "").includes("gzip")) raw = zlib.gunzipSync(wire);
        } catch (_error) {}
        const elapsedMs = Number(process.hrtime.bigint() - startedAt) / 1e6;
        resolve({
          path: pathname,
          status: Number(response.statusCode || 0),
          durationMs: Math.round(elapsedMs),
          rawBytes: raw.length,
          wireBytes: wire.length,
          serverTiming: String(response.headers["server-timing"] || ""),
          error: ""
        });
      });
    });
    request.on("timeout", () => request.destroy(new Error("timeout")));
    request.on("error", (error) => resolve({ path: pathname, status: 0, durationMs: timeoutMs, rawBytes: 0, wireBytes: 0, serverTiming: "", error: error.message }));
  });
}

async function mapLimit(items, limit, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  async function run() {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await worker(items[index]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, run));
  return results;
}

async function main() {
  const results = await mapLimit(paths, concurrency, request);
  console.table(results.map((row) => ({
    status: row.status || row.error,
    ms: row.durationMs,
    rawKB: Math.round(row.rawBytes / 1024),
    wireKB: Math.round(row.wireBytes / 1024),
    path: row.path
  })));
  const durations = results.filter((row) => row.status > 0).map((row) => row.durationMs);
  const failures = results.filter((row) => row.status < 200 || row.status >= 400);
  const oversized = results.filter((row) => row.rawBytes > 100000);
  const slow = results.filter((row) => row.durationMs > 1000);
  console.log(JSON.stringify({
    baseUrl,
    endpointCount: results.length,
    failureCount: failures.length,
    overOneSecondCount: slow.length,
    over100KbCount: oversized.length,
    p50Ms: percentile(durations, 50),
    p95Ms: percentile(durations, 95),
    totalRawBytes: results.reduce((sum, row) => sum + row.rawBytes, 0),
    totalWireBytes: results.reduce((sum, row) => sum + row.wireBytes, 0)
  }, null, 2));
  if (strict && (failures.length || oversized.length)) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});
