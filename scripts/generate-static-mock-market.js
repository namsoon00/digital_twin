const childProcess = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const outputDir = path.join(rootDir, "public", "mock-data", "market");
const scenarios = [
  "recent-one-year",
  "covid-crash",
  "financial-crisis",
  "semiconductor-boom",
  "rate-shock"
];
const symbols = ["NVDA", "AAPL", "005930", "000660", "TSLA"];
const staticAsOf = "2026-07-01";
const staticGeneratedAt = "2026-07-01T00:00:00.000Z";

function randomPort() {
  return 44000 + (crypto.randomBytes(2).readUInt16BE(0) % 1000);
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
      output += chunk.toString();
      const match = output.match(/http:\/\/127\.0\.0\.1:(\d+)/);
      if (match) finish(Number(match[1]));
    }

    child.stdout.on("data", read);
    child.stderr.on("data", function (chunk) {
      output += chunk.toString();
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
      reject(new Error("서버가 시작 전에 종료되었습니다. exit=" + code + "\n" + output.trim()));
    });
  });
}

function requestJson(port, pathname) {
  return new Promise(function (resolve, reject) {
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port: port,
        path: pathname,
        method: "GET",
        headers: { Accept: "application/json" },
        timeout: 10000
      },
      function (res) {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", function (chunk) {
          body += chunk;
        });
        res.on("end", function () {
          if (res.statusCode < 200 || res.statusCode >= 300) {
            return reject(new Error(pathname + " 응답 코드가 " + res.statusCode + "입니다.\n" + body));
          }
          try {
            resolve(JSON.parse(body));
          } catch (error) {
            reject(new Error(pathname + " JSON을 해석하지 못했습니다: " + error.message));
          }
        });
      }
    );

    req.on("timeout", function () {
      req.destroy(new Error("요청 시간이 초과되었습니다: " + pathname));
    });
    req.on("error", reject);
    req.end();
  });
}

function writeJson(fileName, payload) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(path.join(outputDir, fileName), JSON.stringify(payload, null, 2) + "\n", "utf8");
}

async function withServer(callback) {
  const serverProcess = childProcess.spawn(process.execPath, ["server.js"], {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"],
    env: Object.assign({}, process.env, {
      HOST: "127.0.0.1",
      PORT: String(randomPort()),
      LOCAL_CODEX_ENABLED: "0"
    })
  });

  try {
    const port = await waitForServer(serverProcess);
    await callback(port);
  } finally {
    serverProcess.kill("SIGTERM");
  }
}

async function main() {
  await withServer(async function (port) {
    const scenarioPayload = await requestJson(port, "/api/mock-market/scenarios");
    scenarioPayload.generatedAt = staticGeneratedAt;
    writeJson("scenarios.json", scenarioPayload);

    const index = {
      schemaVersion: 1,
      generatedAt: staticGeneratedAt,
      asOf: staticAsOf,
      symbols: symbols,
      files: {}
    };

    for (const scenario of scenarios) {
      const pathname = "/api/mock-market/candles?scenario=" + encodeURIComponent(scenario)
        + "&symbols=" + encodeURIComponent(symbols.join(","))
        + "&seed=static-v1"
        + "&asOf=" + encodeURIComponent(staticAsOf);
      const payload = await requestJson(port, pathname);
      payload.generatedAt = staticGeneratedAt;
      payload.request.staticFile = true;
      payload.request.staticAsOf = staticAsOf;
      const fileName = scenario + ".json";
      writeJson(fileName, payload);
      index.files[scenario] = "mock-data/market/" + fileName;
    }

    writeJson("index.json", index);
  });
  console.log("Generated static mock market JSON in " + path.relative(rootDir, outputDir));
}

main()
  .catch(function (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  });
