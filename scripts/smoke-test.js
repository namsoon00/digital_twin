const childProcess = require("child_process");
const crypto = require("crypto");
const http = require("http");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");

function randomPort() {
  return 43000 + (crypto.randomBytes(2).readUInt16BE(0) % 1000);
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

function request(port, pathname, options) {
  return new Promise(function (resolve, reject) {
    const method = options && options.method ? options.method : "GET";
    const headers = options && options.method ? options.headers || {} : options || {};
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port: port,
        path: pathname,
        method: method,
        headers: headers || {},
        timeout: 5000
      },
      function (res) {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", function (chunk) {
          body += chunk;
        });
        res.on("end", function () {
          resolve({ statusCode: res.statusCode, headers: res.headers, body: body });
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

function assertOk(condition, message) {
  if (!condition) throw new Error(message);
}

function withFakeTossApi(callback) {
  const server = http.createServer(function (req, res) {
    if (req.method === "POST" && req.url === "/oauth2/token") {
      req.resume();
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        token_type: "Bearer",
        access_token: "fake-token",
        expires_in: 3600
      }));
      return;
    }

    if (req.method === "GET" && req.url === "/api/v1/accounts") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        result: [
          {
            accountSeq: "1",
            accountNo: "1234567890",
            accountType: "BROKERAGE",
            orderableAmount: "250000",
            currency: "KRW"
          }
        ]
      }));
      return;
    }

    if (req.method === "GET" && req.url === "/api/v1/holdings") {
      if (req.headers.authorization !== "Bearer fake-token" || req.headers["x-tossinvest-account"] !== "1") {
        res.writeHead(401, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "unauthorized" }));
        return;
      }

      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        result: {
          totalPurchaseAmount: { krw: "130000", usd: "0" },
          marketValue: { amount: { krw: "144000", usd: "0" } },
          profitLoss: { amount: { krw: "14000", usd: "0" }, rate: "10.77" },
          items: [
            {
              symbol: "005930",
              name: "삼성전자",
              marketCountry: "KR",
              currency: "KRW",
              quantity: "2",
              lastPrice: "72000",
              averagePurchasePrice: "65000",
              marketValue: "144000",
              profitLoss: "14000"
            }
          ]
        }
      }));
      return;
    }

    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "not_found" }));
  });

  return new Promise(function (resolve, reject) {
    server.on("error", reject);
    server.listen(0, "127.0.0.1", async function () {
      const port = server.address().port;
      try {
        await callback("http://127.0.0.1:" + port);
        server.close(function () {
          resolve();
        });
      } catch (error) {
        server.close(function () {
          reject(error);
        });
      }
    });
  });
}

async function withServer(extraEnv, callback) {
  const serverProcess = childProcess.spawn(process.execPath, ["server.js"], {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"],
    env: Object.assign({}, process.env, {
      HOST: "127.0.0.1",
      PORT: String(randomPort()),
      LOCAL_CODEX_ENABLED: "0"
    }, extraEnv || {})
  });

  try {
    const port = await waitForServer(serverProcess);
    await callback(port);
  } finally {
    serverProcess.kill("SIGTERM");
  }
}

async function checkNormalMode(port) {
  const home = await request(port, "/");
  assertOk(home.statusCode === 200, "홈 화면 응답 코드가 200이 아닙니다: " + home.statusCode);
  assertOk(home.body.indexOf('id="app"') >= 0, "홈 화면에 앱 루트가 없습니다.");

  const bootstrap = await request(port, "/api/bootstrap");
  assertOk(bootstrap.statusCode === 200, "부트스트랩 API 응답 코드가 200이 아닙니다: " + bootstrap.statusCode);
  const payload = JSON.parse(bootstrap.body);
  assertOk(payload.profile && payload.profile.assistantName, "부트스트랩 API에 프로필 정보가 없습니다.");
  assertOk(Array.isArray(payload.items), "부트스트랩 API items가 배열이 아닙니다.");
  assertOk(Array.isArray(payload.messages), "부트스트랩 API messages가 배열이 아닙니다.");

  const tossLens = await request(port, "/api/flow-lens?mock=1");
  assertOk(tossLens.statusCode === 200, "토스 판단 API 응답 코드가 200이 아닙니다: " + tossLens.statusCode);
  const tossPayload = JSON.parse(tossLens.body);
  assertOk(tossPayload.toss && Array.isArray(tossPayload.toss.positions), "토스 판단 API에 보유 종목 배열이 없습니다.");
  assertOk(tossPayload.tossDecision && Array.isArray(tossPayload.tossDecision.items), "토스 판단 API에 판단 항목이 없습니다.");
  assertOk(!Array.isArray(tossPayload.news), "토스 전용 판단 API가 뉴스 배열을 내려주고 있습니다.");
  assertOk(!Array.isArray(tossPayload.social), "토스 전용 판단 API가 소셜 배열을 내려주고 있습니다.");

  const scenarios = await request(port, "/api/mock-market/scenarios");
  assertOk(scenarios.statusCode === 200, "mock market 시나리오 API 응답 코드가 200이 아닙니다: " + scenarios.statusCode);
  const scenarioPayload = JSON.parse(scenarios.body);
  assertOk(Array.isArray(scenarioPayload.scenarios), "mock market 시나리오 목록이 배열이 아닙니다.");
  assertOk(scenarioPayload.scenarios.some(function (scenario) { return scenario.id === "semiconductor-boom"; }), "반도체 호황 시나리오가 없습니다.");

  const mockMarket = await request(port, "/api/mock-market/candles?scenario=semiconductor-boom&symbols=NVDA,005930&seed=ci");
  assertOk(mockMarket.statusCode === 200, "mock market candles API 응답 코드가 200이 아닙니다: " + mockMarket.statusCode);
  const mockMarketPayload = JSON.parse(mockMarket.body);
  assertOk(mockMarketPayload.scenario && mockMarketPayload.scenario.id === "semiconductor-boom", "mock market 시나리오 id가 맞지 않습니다.");
  assertOk(mockMarketPayload.series && Array.isArray(mockMarketPayload.series.NVDA.candles), "NVDA mock candle 배열이 없습니다.");
  assertOk(mockMarketPayload.series.NVDA.candles.length >= 200, "NVDA mock candle 수가 부족합니다.");
  assertOk(Array.isArray(mockMarketPayload.signals) && mockMarketPayload.signals.length === 2, "mock market signal 개수가 맞지 않습니다.");

  const staticMockMarket = await request(port, "/mock-data/market/semiconductor-boom.json");
  assertOk(staticMockMarket.statusCode === 200, "정적 mock market JSON 응답 코드가 200이 아닙니다: " + staticMockMarket.statusCode);
  const staticMockMarketPayload = JSON.parse(staticMockMarket.body);
  assertOk(staticMockMarketPayload.request && staticMockMarketPayload.request.staticFile === true, "정적 mock market JSON 표시가 없습니다.");
  assertOk(staticMockMarketPayload.series && Array.isArray(staticMockMarketPayload.series.NVDA.candles), "정적 NVDA mock candle 배열이 없습니다.");

  const preflight = await request(port, "/api/data-api/opendart/company", {
    method: "OPTIONS",
    headers: {
      Origin: "https://namsoon00.github.io",
      "Access-Control-Request-Method": "GET",
      "Access-Control-Request-Headers": "accept",
      "Access-Control-Request-Private-Network": "true"
    }
  });
  assertOk(preflight.statusCode === 204, "데이터 API preflight 응답 코드가 204가 아닙니다: " + preflight.statusCode);
  assertOk(preflight.headers["access-control-allow-origin"] === "*", "데이터 API CORS origin 헤더가 없습니다.");
  assertOk(String(preflight.headers["access-control-allow-methods"] || "").indexOf("GET") >= 0, "데이터 API CORS method 헤더에 GET이 없습니다.");
  assertOk(String(preflight.headers["access-control-allow-headers"] || "").toLowerCase().indexOf("accept") >= 0, "데이터 API CORS headers에 Accept가 없습니다.");
  assertOk(preflight.headers["access-control-allow-private-network"] === "true", "데이터 API private network preflight 허용 헤더가 없습니다.");
}

async function checkShareMode(port) {
  const blockedHome = await request(port, "/");
  assertOk(blockedHome.statusCode === 401, "공유 토큰 없는 홈 접근이 차단되지 않았습니다.");

  const blockedApi = await request(port, "/api/bootstrap");
  assertOk(blockedApi.statusCode === 401, "공유 토큰 없는 API 접근이 차단되지 않았습니다.");

  const tokenRedirect = await request(port, "/?share_token=ci-token");
  assertOk(tokenRedirect.statusCode === 302, "공유 토큰 URL이 쿠키 리다이렉트를 만들지 않았습니다.");
  assertOk(String(tokenRedirect.headers["set-cookie"] || "").indexOf("dt_share_token=") >= 0, "공유 토큰 쿠키가 설정되지 않았습니다.");

  const bootstrap = await request(port, "/api/bootstrap", { Cookie: "dt_share_token=ci-token" });
  assertOk(bootstrap.statusCode === 200, "공유 토큰 쿠키로 API 접근이 허용되지 않았습니다.");
}

async function checkLiveTossMode(port) {
  const tossLens = await request(port, "/api/flow-lens");
  assertOk(tossLens.statusCode === 200, "live 토스 판단 API 응답 코드가 200이 아닙니다: " + tossLens.statusCode);
  const payload = JSON.parse(tossLens.body);
  assertOk(payload.toss && payload.toss.mode === "live", "토스 live 모드가 아닙니다.");
  assertOk(Array.isArray(payload.toss.positions), "토스 live 보유 종목 배열이 없습니다.");
  assertOk(payload.toss.positions.length === 1, "토스 live 보유 종목 수가 맞지 않습니다.");
  const position = payload.toss.positions[0];
  assertOk(position.symbol === "005930", "토스 live 보유 종목 코드가 맞지 않습니다.");
  assertOk(position.currentPrice === 72000, "토스 live 현재가 매핑이 맞지 않습니다.");
  assertOk(position.averagePrice === 65000, "토스 live 평균단가 매핑이 맞지 않습니다.");
}

async function main() {
  await withServer({}, checkNormalMode);
  await withFakeTossApi(async function (baseUrl) {
    await withServer({
      TOSS_API_BASE_URL: baseUrl,
      TOSS_CLIENT_ID: "fake-client-id",
      TOSS_CLIENT_SECRET: "fake-client-secret"
    }, checkLiveTossMode);
  });
  await withServer({ SHARE_TOKEN: "ci-token" }, checkShareMode);
  console.log("Smoke test passed");
}

main()
  .catch(function (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  });
