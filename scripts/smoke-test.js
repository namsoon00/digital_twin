const childProcess = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const vm = require("vm");

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
    const body = options && options.body ? options.body : "";
    if (body && !headers["Content-Length"]) headers["Content-Length"] = Buffer.byteLength(body);
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
    if (body) req.write(body);
    req.end();
  });
}

function assertOk(condition, message) {
  if (!condition) throw new Error(message);
}

function readSqliteSetting(dbPath, key) {
  const script = [
    "import sqlite3, sys",
    "connection = sqlite3.connect(sys.argv[1])",
    "row = connection.execute('SELECT value FROM runtime_settings WHERE key = ?', (sys.argv[2],)).fetchone()",
    "print('' if row is None else row[0])"
  ].join("\n");
  return childProcess.execFileSync(process.env.PYTHON_BIN || "python3", ["-c", script, dbPath, key], {
    cwd: rootDir,
    encoding: "utf8"
  }).trim();
}

function checkFrontendAdminRender() {
  const code = fs.readFileSync(path.join(rootDir, "public", "app.js"), "utf8");
  const styles = fs.readFileSync(path.join(rootDir, "public", "styles.css"), "utf8");
  const designSystemDoc = fs.readFileSync(path.join(rootDir, "docs", "design-system.md"), "utf8");
  assertOk(styles.indexOf("--ds-color-bg") >= 0, "전역 디자인 시스템 색상 토큰이 없습니다.");
  assertOk(styles.indexOf("--ds-control-height-md") >= 0, "전역 컨트롤 높이 토큰이 없습니다.");
  assertOk(styles.indexOf(".settings-save-panel") >= 0, "설정 화면 저장 액션 위치 규칙이 없습니다.");
  assertOk(designSystemDoc.indexOf("Button Placement") >= 0, "디자인 시스템 문서에 버튼 위치 정책이 없습니다.");
  assertOk(designSystemDoc.indexOf("aria-current") >= 0, "디자인 시스템 문서에 내비게이션 접근성 기준이 없습니다.");
  assertOk(code.indexOf('appTheme: settingValue("appTheme")') >= 0, "설정 저장 payload에 화면 테마가 포함되지 않았습니다.");
  const payloads = {
    "/api/settings": {
      settings: {
        tossApiBaseUrl: "https://openapi.tossinvest.com",
        notifyProvider: "telegram",
        notifyLinkUrl: "http://127.0.0.1:3000?tab=notifications"
      },
      configured: {
        tossClientId: true,
        tossClientSecret: true,
        tossAccountSeq: true,
        telegramBotToken: true,
        telegramChatId: true
      },
      locked: false
    },
    "/api/service-accounts": {
      accounts: [
        {
          id: "main",
          label: "DB 계정",
          provider: "toss",
          baseUrl: "https://openapi.tossinvest.com",
          accountSeq: "1",
          enabled: true,
          watchlistSymbols: ["NVDA", "005930"],
          notifyProvider: "telegram",
          notifyLinkUrl: "http://127.0.0.1:3000?tab=notifications",
          clientId: true,
          clientSecret: true,
          telegramBotToken: true,
          telegramChatId: true
        }
      ]
    },
    "/api/notification-templates": {
      templates: [
        {
          messageType: "monitorHeartbeat",
          template: "{readableMessage}",
          description: "상태 확인 템플릿",
          enabled: true,
          updatedAt: "2026-07-01T00:00:00.000Z"
        },
        {
          messageType: "modelReview",
          template: "{body}",
          description: "모델 리뷰 템플릿",
          enabled: true,
          updatedAt: "2026-07-01T00:00:00.000Z"
        }
      ],
      variables: ["title", "readableMessage", "dataLines", "triggerSummary", "lines", "rawLines", "body", "messageType"]
    },
    "/api/notification-schedules": {
      generatedAt: "2026-07-01T00:00:00.000Z",
      schedules: [
        {
          messageType: "monitorHeartbeat",
          label: "실시간 상태",
          enabled: true,
          status: "waiting",
          cadenceMinutes: 10,
          cadenceText: "조건이 다시 충족되면 최소 10분 간격으로 보냅니다.",
          triggerSummary: "실시간 모니터링 워커가 정상 작동 중인지 확인할 때 보냅니다.",
          lastSentAt: "2026-07-01T00:00:00.000Z",
          nextEligibleAt: "2026-07-01T00:10:00.000Z",
          eligibleNow: false,
          recentTargets: [
            { accountId: "main", accountLabel: "DB 계정", target: "", sentAt: "2026-07-01T00:00:00.000Z" }
          ]
        }
      ]
    },
    "/api/symbol-universe": {
      items: [
        {
          symbol: "005930",
          name: "삼성전자",
          market: "KOSPI",
          exchange: "KOSPI",
          currency: "KRW",
          sector: "반도체",
          assetType: "STOCK",
          source: "KRX KIND Listed Companies",
          sourceUrl: "https://kind.krx.co.kr/",
          fetchedAt: "2026-07-01T00:00:00.000Z",
          lastSeenAt: "2026-07-01T00:00:00.000Z",
          stale: false
        },
        {
          symbol: "AAPL",
          name: "Apple Inc.",
          market: "NASDAQ",
          exchange: "NASDAQ Global Select",
          currency: "USD",
          sector: "AI/플랫폼",
          assetType: "STOCK",
          source: "Nasdaq Trader Symbol Directory",
          sourceUrl: "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
          fetchedAt: "2026-07-01T00:00:00.000Z",
          lastSeenAt: "2026-07-01T00:00:00.000Z",
          stale: false
        }
      ],
      summary: {
        total: 2,
        maxAgeHours: 24,
        sources: [],
        markets: [
          { market: "KOSPI", count: 1, lastSeenAt: "2026-07-01T00:00:00.000Z", stale: false, source: "KRX KIND Listed Companies", sourceUrl: "https://kind.krx.co.kr/" },
          { market: "KOSDAQ", count: 0, lastSeenAt: "", stale: true, source: "KRX KIND Listed Companies", sourceUrl: "https://kind.krx.co.kr/" },
          { market: "NASDAQ", count: 1, lastSeenAt: "2026-07-01T00:00:00.000Z", stale: false, source: "Nasdaq Trader Symbol Directory", sourceUrl: "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt" }
        ]
      }
    },
    "admin/config.json": {
      mode: "github-pages-readonly-preview",
      localData: {
        generatedAt: "2026-07-01T00:00:00.000Z",
        accountCount: 1,
        enabledAccountCount: 1,
        accounts: [
          {
            id: "pages-main",
            label: "Pages DB 계정",
            provider: "toss",
            baseUrl: "https://openapi.tossinvest.com",
            accountSeq: true,
            enabled: true,
            watchlistSymbols: ["MSFT", "035720"],
            notifyProvider: "telegram",
            notifyLinkUrl: "https://namsoon00.github.io/digital_twin/?tab=notifications",
            clientId: true,
            clientSecret: true,
            telegramBotToken: true,
            telegramChatId: true
          }
        ],
        settings: {
          watchlistSymbols: "MSFT,035720",
          notifyProvider: "telegram",
          notifyLinkUrl: "https://namsoon00.github.io/digital_twin/?tab=notifications",
          alertCadenceMinutes: "monitorDecisionChange=10"
        },
        configured: {
          tossClientId: true,
          tossClientSecret: true,
          tossAccountSeq: true,
          telegramBotToken: true,
          telegramChatId: true
        }
      }
    },
    "/api/flow-lens": {
      generatedAt: "2026-07-01T00:00:00.000Z",
      headline: "테스트 스냅샷",
      exitScore: 0,
      toss: {
        mode: "live",
        status: "ok",
        account: {},
        positions: [
          {
            symbol: "005930",
            name: "삼성전자",
            market: "KR",
            currency: "KRW",
            sector: "반도체",
            quantity: 2,
            sellableQuantity: 2,
            averagePrice: 65000,
            currentPrice: 72000,
            marketValue: 144000,
            profitLoss: 14000,
            profitLossRate: 10.7,
            source: "holding"
          }
        ],
        watchlist: [
          { symbol: "NVDA", name: "NVIDIA", market: "US", currency: "USD", sector: "반도체", currentPrice: 180 }
        ]
      },
      tossDecision: { items: [], rules: [], holdingCount: 0, watchCount: 1, overallPressure: 0 },
      portfolio: { total: 0, invested: 0, cash: 0, markets: [], sectors: [] },
      checklist: [],
      summary: []
    },
    "mock-data/market/recent-one-year.json": {
      schemaVersion: 1,
      dataQuality: "mock-synthetic",
      scenario: { id: "recent-one-year", label: "최근 1년 기준", description: "테스트 시계열" },
      request: { symbols: ["NVDA"], staticFile: true },
      series: {
        NVDA: {
          symbol: "NVDA",
          name: "NVIDIA",
          market: "US",
          currency: "USD",
          sector: "반도체",
          candles: [
            {
              date: "2026-06-28",
              open: 100,
              high: 104,
              low: 98,
              close: 100,
              volume: 100000,
              changePercent: 0,
              relativeVolume: 1,
              tradeStrength: 100,
              buyVolume: 52000,
              sellVolume: 48000,
              bidAskImbalance: 4,
              ma20: 100,
              ma60: 98
            },
            {
              date: "2026-07-01",
              open: 101,
              high: 111,
              low: 100,
              close: 110,
              volume: 180000,
              changePercent: 10,
              relativeVolume: 1.8,
              tradeStrength: 126,
              buyVolume: 120000,
              sellVolume: 60000,
              bidAskImbalance: 18,
              ma20: 103,
              ma60: 99
            }
          ]
        }
      }
    }
  };

  function renderForSearch(search, hostname) {
    let html = "";
    const storage = new Map();
    const app = {
      get innerHTML() {
        return html;
      },
      set innerHTML(value) {
        html = String(value);
      },
      querySelector: function () {
        return null;
      },
      querySelectorAll: function () {
        return [];
      }
    };
    const documentElement = {
      attributes: {},
      setAttribute: function (name, value) {
        this.attributes[name] = String(value);
      }
    };

    vm.runInNewContext(code, {
      console: console,
      setTimeout: setTimeout,
      clearTimeout: clearTimeout,
      URLSearchParams: URLSearchParams,
      document: {
        documentElement: documentElement,
        getElementById: function (id) {
          return id === "app" ? app : null;
        }
      },
      window: {
        location: { protocol: "http:", hostname: hostname || "127.0.0.1", search: search || "" },
        matchMedia: function () {
          return {
            matches: false,
            addEventListener: function () {},
            addListener: function () {}
          };
        },
        localStorage: {
          getItem: function (key) {
            return storage.has(key) ? storage.get(key) : null;
          },
          setItem: function (key, value) {
            storage.set(key, String(value));
          },
          removeItem: function (key) {
            storage.delete(key);
          }
        }
      },
      fetch: function (requestedPath) {
        const key = String(requestedPath).split("?")[0];
        if (!payloads[key]) throw new Error("unexpected frontend fetch: " + requestedPath);
        return Promise.resolve({
          ok: true,
          json: function () {
            return Promise.resolve(payloads[key]);
          },
          text: function () {
            return Promise.resolve(JSON.stringify(payloads[key]));
          }
        });
      }
    }, { filename: "public/app.js" });

    return new Promise(function (resolve) {
      setTimeout(function () {
        resolve(html);
      }, 80);
    });
  }

  return Promise.all([
    renderForSearch(""),
    renderForSearch("?tab=accounts"),
    renderForSearch("?tab=watchlist"),
    renderForSearch("?tab=symbols"),
    renderForSearch("?tab=notifications"),
    renderForSearch("?tab=modeling"),
    renderForSearch("?tab=monitoring"),
    renderForSearch("?tab=settings"),
    renderForSearch("?tab=accounts", "namsoon00.github.io")
  ]).then(function (pages) {
    const overviewHtml = pages[0];
    const accountHtml = pages[1];
    const watchlistHtml = pages[2];
    const symbolUniverseHtml = pages[3];
    const notificationHtml = pages[4];
    const modelingHtml = pages[5];
    const monitoringHtml = pages[6];
    const settingsHtml = pages[7];
    const staticAccountHtml = pages[8];

    assertOk(overviewHtml.indexOf("계정·알림·모델 운영 콘솔") < 0, "이전 고정 운영 콘솔 제목이 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf("<h1>홈</h1>") >= 0, "홈 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("<h1>계정</h1>") >= 0, "계정 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf('aria-current="page"') >= 0, "활성 탭 접근성 상태가 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("<h1>설정</h1>") >= 0, "설정 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-view") >= 0, "설정 화면이 페이지 구조로 렌더링되지 않았습니다.");
    assertOk(code.indexOf("settings-top-button") >= 0, "상단 설정 버튼 전용 스타일이 적용되지 않았습니다.");
    assertOk(code.indexOf("pushState") >= 0 && code.indexOf("popstate") >= 0, "탭 이동이 브라우저 뒤로가기와 동기화되지 않았습니다.");
    assertOk(code.indexOf("settingsSaving") >= 0 && code.indexOf("로컬 SQLite DB") >= 0, "설정 저장 진행 상태가 렌더링되지 않습니다.");
    ["overview", "accounts", "watchlist", "symbols", "monitoring", "notifications", "modeling", "settings"].forEach(function (tab) {
      assertOk(overviewHtml.indexOf('data-tab="' + tab + '"') >= 0, "새 탭이 렌더링되지 않았습니다: " + tab);
    });
    assertOk(overviewHtml.indexOf("data-mode=") < 0, "Mock 데이터 전환 버튼이 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf(">Mock<") < 0, "Mock 데이터 버튼 라벨이 아직 렌더링됩니다.");
    assertOk(overviewHtml.indexOf("Mock 데이터") < 0, "Mock 데이터 표시 문구가 아직 렌더링됩니다.");
    ["decision", "lab", "alerts", "holdings", "feed"].forEach(function (tab) {
      assertOk(overviewHtml.indexOf('data-tab="' + tab + '"') < 0, "기존 탭이 남아 있습니다: " + tab);
    });
    assertOk(overviewHtml.indexOf("admin-monitoring-panel") >= 0, "모니터링 상태 패널이 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("account-directory-panel") >= 0, "홈에 DB 계정 패널이 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("account-watchlist-panel") >= 0, "홈에 계정별 관심 종목 패널이 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("DB 저장 계정") >= 0, "DB 계정 제목이 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("home-command-grid") >= 0, "홈 운영 요약 카드가 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("home-action") >= 0, "홈 빠른 이동 카드가 렌더링되지 않았습니다.");
    assertOk(overviewHtml.indexOf("토스 실데이터 연결됨") >= 0, "홈에 토스 연결 상태가 표시되지 않습니다.");
    assertOk(accountHtml.indexOf("data-account-form") >= 0, "계정 등록 폼이 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("DB 저장 계정") >= 0, "계정 탭에 DB 계정 목록이 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("account-manager-summary") >= 0, "계정 탭 요약 카드가 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("account-credential-pills") >= 0, "계정 API 상태 칩이 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("account-credential-grid") >= 0, "계정 보안 상태 요약이 렌더링되지 않았습니다.");
    assertOk(accountHtml.indexOf("Bot token 설정됨") >= 0, "텔레그램 bot token 설정 상태가 표시되지 않습니다.");
    assertOk(accountHtml.indexOf("Secret 설정됨") >= 0, "토스 secret 설정 상태가 표시되지 않습니다.");
    assertOk(accountHtml.indexOf("저장됨 - 새 값 입력 시 교체") >= 0, "저장된 API 값의 교체 안내가 표시되지 않습니다.");
    assertOk(accountHtml.indexOf('value="DB 계정"') >= 0, "로컬 DB 계정 표시 이름이 폼에 채워지지 않았습니다.");
    assertOk(accountHtml.indexOf('value="NVDA,005930"') >= 0, "로컬 DB 관심 종목이 폼에 채워지지 않았습니다.");
    assertOk(accountHtml.indexOf('value="true"') < 0, "마스킹된 boolean 값이 계정 폼에 그대로 표시됩니다.");
    assertOk(watchlistHtml.indexOf("계정별 관심 종목") >= 0, "관심종목 탭에 계정별 관심 종목이 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("account-watchlist-workbench") >= 0, "관심종목 탭에 계정별 편집 워크벤치가 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("data-watch-account-select") >= 0, "관심종목 탭에 계정 선택 버튼이 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("data-watch-account-id=\"main\"") >= 0, "관심종목 추가 폼이 선택 계정에 연결되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("data-watch-symbol-input") >= 0, "관심종목 검색 입력창이 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("data-watch-suggest-list") >= 0, "관심종목 서제스트 영역이 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("watch-row-meta") >= 0, "관심종목 알림/시세 상태가 렌더링되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("시세 알림") >= 0, "관심종목 시세 알림 상태가 표시되지 않았습니다.");
    assertOk(watchlistHtml.indexOf("symbol-result-list") < 0, "관심종목 탭에 전체 종목 결과 리스트가 남아 있습니다.");
    assertOk(watchlistHtml.indexOf("NVDA") >= 0 && watchlistHtml.indexOf("005930") >= 0, "DB 계정 관심 종목이 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("<h1>전체종목</h1>") >= 0, "전체종목 탭 제목이 상단에 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-result-list") >= 0, "전체종목 탭에 종목 결과 리스트가 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-summary-card") >= 0, "전체종목 탭에 시장 요약 카드가 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("data-symbol-add-account") >= 0, "전체종목 탭에 관심 추가 대상 계정 선택이 없습니다.");
    assertOk(notificationHtml.indexOf("admin-message-row") >= 0, "메시지 타입별 알림 설정이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-template-row") >= 0, "알림 템플릿 편집기가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("admin-message-template") >= 0, "알림 타입 행 안에 템플릿 편집기가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("notification-template-preview") >= 0, "알림 템플릿 미리보기가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("시스템 템플릿") >= 0, "시스템 템플릿 섹션이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("settings-api-grid") >= 0, "설정 API 상태 요약이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("Client ID 설정됨") >= 0, "설정 화면에 토스 Client ID 상태가 표시되지 않습니다.");
    assertOk(notificationHtml.indexOf("Bot token 설정됨") >= 0, "설정 화면에 텔레그램 bot token 상태가 표시되지 않습니다.");
    assertOk(notificationHtml.indexOf("data-template-test-send") >= 0, "실제 데이터 알림 테스트 발송 버튼이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("모니터링 정상 작동") >= 0, "상태 확인 템플릿 미리보기 샘플이 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("매수 점수") >= 0, "타입별 템플릿 미리보기 샘플이 구분되지 않습니다.");
    assertOk(notificationHtml.indexOf("data-notification-template=\"monitorHeartbeat\"") >= 0, "상태 확인 템플릿 textarea가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("{rawLines}") >= 0, "알림 템플릿 변수가 렌더링되지 않았습니다.");
    assertOk(notificationHtml.indexOf("tab=notifications") >= 0, "알림 링크 기본값이 새 알림 탭을 가리키지 않습니다.");
    assertOk(modelingHtml.indexOf("model-guide-panel") >= 0, "모델 운영 가이드가 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("내 매수·매도 기준 운영 순서") >= 0, "모델 운영 순서 제목이 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("admin-modeling-panel") >= 0, "모델링 설정 패널이 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("매매 판단 기준 관리") >= 0, "쉬운 모델 판단 기준 제목이 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("투자자별 수급") >= 0, "투자자별 수급 feature 설명이 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("방향성 거래량") >= 0, "방향성 거래량 feature 설명이 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("feature 기여도") >= 0, "feature 기여도 블록이 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("directionalVolumePressure") >= 0, "방향성 거래량 공식 변수가 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("feature 재현성") >= 0, "feature 재현성 검증 블록이 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("같은 입력 재현됨") >= 0, "같은 입력 재계산 검증 결과가 렌더링되지 않았습니다.");
    assertOk(modelingHtml.indexOf("model-timing-panel") < 0, "Mock 시계열 기반 타이밍 패널이 아직 렌더링됩니다.");
    assertOk(modelingHtml.indexOf("웹에서 운영하는 매수·매도 타이밍 모델") < 0, "타이밍 모델 제목이 아직 렌더링됩니다.");
    assertOk(monitoringHtml.indexOf("monitoring-instrument-panel") >= 0, "모니터링 탭에 보유·관심 통합 패널이 렌더링되지 않았습니다.");
    assertOk(monitoringHtml.indexOf("보유·관심 종목 통합") >= 0, "모니터링 탭 통합 패널 제목이 렌더링되지 않았습니다.");
    assertOk(monitoringHtml.indexOf("삼성전자") >= 0 && monitoringHtml.indexOf("NVIDIA") >= 0, "보유 종목과 관심 종목이 함께 렌더링되지 않았습니다.");
    assertOk(monitoringHtml.indexOf(">보유<") >= 0 && monitoringHtml.indexOf(">관심<") >= 0, "보유/관심 상태 라벨이 함께 렌더링되지 않았습니다.");
    assertOk(monitoringHtml.indexOf("watchlist-panel") < 0, "모니터링 탭에 관심 종목 관리 패널이 따로 남아 있습니다.");
    assertOk(settingsHtml.indexOf("settings-overview-panel") >= 0, "설정 탭 요약 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-environment-panel") >= 0, "설정 탭 앱 환경 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-delivery-panel") >= 0, "설정 탭 알림 전달 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-external-data-panel") >= 0, "설정 탭 외부 데이터 패널이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("settings-save-panel") >= 0, "설정 탭 저장 패널이 렌더링되지 않았습니다.");
    assertOk((settingsHtml.match(/data-action="save-settings"/g) || []).length >= 2, "설정 저장 버튼이 상단과 하단에 모두 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf('data-action="settings-back"') >= 0, "설정 탭 뒤로가기 버튼이 렌더링되지 않았습니다.");
    assertOk(settingsHtml.indexOf("Telegram Bot Token") >= 0, "설정 탭에 알림 전달 설정이 없습니다.");
    assertOk(settingsHtml.indexOf("Alpha Vantage API Key") >= 0, "설정 탭에 외부 데이터 API 설정이 없습니다.");
    assertOk(settingsHtml.indexOf('data-setting="tossClientId"') < 0, "설정 탭에 계정 Client ID 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="tossClientSecret"') < 0, "설정 탭에 계정 Secret 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="tossAccountSeq"') < 0, "설정 탭에 계좌 순번 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="watchlistSymbols"') < 0, "설정 탭에 관심 종목 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf("<span>관심 종목</span>") < 0, "설정 탭 앱 환경에 관심 종목 라벨이 남아 있습니다.");
    assertOk(settingsHtml.indexOf("TSLA,AAPL,NVDA,000660") < 0, "설정 탭에 기본 관심 종목 값이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="modelName"') < 0, "설정 탭에 모델 이름 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="customBuyModelFormula"') < 0, "설정 탭에 모델 공식 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf('data-setting="modelDecisionThresholds"') < 0, "설정 탭에 모델 기준 입력이 남아 있습니다.");
    assertOk(settingsHtml.indexOf("Toss Client") < 0, "설정 탭에 토스 계정 입력 라벨이 남아 있습니다.");
    assertOk(settingsHtml.indexOf("모델 입력과 공식") < 0, "설정 탭에 모델 설정 섹션이 남아 있습니다.");
    assertOk(symbolUniverseHtml.indexOf("symbol-universe-panel") >= 0, "전체 종목 카탈로그 패널이 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("전체 종목 카탈로그") >= 0 || symbolUniverseHtml.indexOf("전체 종목 정보") >= 0, "전체 종목 카탈로그 제목이 렌더링되지 않았습니다.");
    assertOk(symbolUniverseHtml.indexOf("AAPL") >= 0, "종목 유니버스 검색 결과가 렌더링되지 않았습니다.");
    assertOk(staticAccountHtml.indexOf('value="Pages DB 계정"') >= 0, "정적 빌드 DB 계정 표시 이름이 폼에 채워지지 않았습니다.");
    assertOk(staticAccountHtml.indexOf('value="MSFT,035720"') >= 0, "정적 빌드 관심 종목이 폼에 채워지지 않았습니다.");
    assertOk(staticAccountHtml.indexOf('value="true"') < 0, "정적 빌드의 마스킹된 boolean 값이 계정 폼에 그대로 표시됩니다.");
  });
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
            accountType: "BROKERAGE"
          }
        ]
      }));
      return;
    }

    if (req.method === "GET" && req.url.indexOf("/api/v1/buying-power") === 0) {
      if (req.headers.authorization !== "Bearer fake-token" || req.headers["x-tossinvest-account"] !== "1") {
        res.writeHead(401, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "unauthorized" }));
        return;
      }
      const currency = new URL("http://127.0.0.1" + req.url).searchParams.get("currency");
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        result: {
          currency: currency,
          cashBuyingPower: currency === "USD" ? "100" : "250000"
        }
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
              profitLoss: "14000",
              tradeStrength: "118",
              volume: "3521000",
              volumeRatio: "1.8",
              foreignBuyVolume: "420000",
              foreignSellVolume: "275000",
              institutionBuyVolume: "310000",
              institutionSellVolume: "228000"
            }
          ]
        }
      }));
      return;
    }

    if (req.method === "GET" && req.url.indexOf("/api/v1/candles") === 0) {
      if (req.headers.authorization !== "Bearer fake-token") {
        res.writeHead(401, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "unauthorized" }));
        return;
      }
      const candles = [];
      for (let index = 199; index >= 0; index--) {
        const date = new Date(Date.UTC(2026, 0, 1 + index));
        const close = 52000 + index * 100;
        candles.push({
          timestamp: date.toISOString().replace("Z", "+09:00"),
          openPrice: String(close - 100),
          highPrice: String(close + 200),
          lowPrice: String(close - 200),
          closePrice: String(close),
          volume: String(1000000 + index * 1000),
          currency: "KRW"
        });
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ result: { candles: candles, nextBefore: null } }));
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
  const runId = process.pid + "-" + Date.now() + "-" + Math.random();
  const settingsPath = path.join(os.tmpdir(), "digital-twin-smoke-settings-" + runId + ".json");
  const dataDir = path.join(os.tmpdir(), "digital-twin-smoke-data-" + runId);
  const serverProcess = childProcess.spawn(process.env.PYTHON_BIN || "python3", ["python_service/service.py", "web"], {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"],
    env: Object.assign({}, process.env, {
      HOST: "127.0.0.1",
      PORT: String(randomPort()),
      LOCAL_CODEX_ENABLED: "0",
      WATCHLIST_SYMBOLS: "TSLA,AAPL,NVDA,000660",
      SETTINGS_PATH: settingsPath,
      DIGITAL_TWIN_DATA_DIR: dataDir
    }, extraEnv || {})
  });

  try {
    const port = await waitForServer(serverProcess);
    await callback(port, {
      dataDir: dataDir,
      serviceDbPath: path.join(dataDir, "service.db"),
      settingsPath: settingsPath
    });
  } finally {
    serverProcess.kill("SIGTERM");
  }
}

async function checkNormalMode(port, context) {
  const home = await request(port, "/");
  assertOk(home.statusCode === 200, "홈 화면 응답 코드가 200이 아닙니다: " + home.statusCode);
  assertOk(home.body.indexOf('id="app"') >= 0, "홈 화면에 앱 루트가 없습니다.");

  const bootstrap = await request(port, "/api/bootstrap");
  assertOk(bootstrap.statusCode === 200, "부트스트랩 API 응답 코드가 200이 아닙니다: " + bootstrap.statusCode);
  const payload = JSON.parse(bootstrap.body);
  assertOk(payload.profile && payload.profile.assistantName, "부트스트랩 API에 프로필 정보가 없습니다.");
  assertOk(Array.isArray(payload.items), "부트스트랩 API items가 배열이 아닙니다.");
  assertOk(Array.isArray(payload.messages), "부트스트랩 API messages가 배열이 아닙니다.");

  const settings = await request(port, "/api/settings");
  assertOk(settings.statusCode === 200, "설정 API 응답 코드가 200이 아닙니다: " + settings.statusCode);
  const settingsPayload = JSON.parse(settings.body);
  assertOk(settingsPayload.settings && settingsPayload.configured, "설정 API 응답 형식이 맞지 않습니다.");
  assertOk(settingsPayload.settings.tossClientSecret === "", "설정 API가 secret 원문을 내려주고 있습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "alertRules"), "설정 API에 알림 규칙 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "modelDecisionThresholds"), "설정 API에 모델 판단 기준 필드가 없습니다.");
  assertOk(Object.prototype.hasOwnProperty.call(settingsPayload.settings, "appTheme"), "설정 API에 화면 테마 필드가 없습니다.");
  assertOk(settingsPayload.settings.watchlistSymbols.indexOf("TSLA") >= 0, "기본 관심 종목에 TSLA가 없습니다.");
  assertOk(settingsPayload.settings.watchlistSymbols.indexOf("AAPL") >= 0, "기본 관심 종목에 AAPL이 없습니다.");

  const universe = await request(port, "/api/symbol-universe?query=AAPL");
  assertOk(universe.statusCode === 200, "종목 유니버스 API 응답 코드가 200이 아닙니다: " + universe.statusCode);
  const universePayload = JSON.parse(universe.body);
  assertOk(Array.isArray(universePayload.items), "종목 유니버스 items가 배열이 아닙니다.");
  assertOk(universePayload.items.some(function (item) { return item.symbol === "AAPL"; }), "종목 유니버스에 AAPL seed가 없습니다.");
  assertOk(universePayload.summary && Array.isArray(universePayload.summary.markets), "종목 유니버스 시장별 신선도 요약이 없습니다.");

  const savedSettings = await request(port, "/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      settings: {
        watchlistSymbols: "TSLA,AAPL,NVDA",
        tossApiBaseUrl: "http://127.0.0.1:1",
        tossClientId: "fake-client",
        tossClientSecret: "fake-secret",
        appTheme: "dark",
        notifyProvider: "telegram",
        telegramBotToken: "fake-telegram-token",
        telegramChatId: "1234",
        alertRules: "priceStop=1\nmodelSell=1",
        modelDecisionThresholds: "modelBuy=75\nmodelSell=70"
      }
    })
  });
  assertOk(savedSettings.statusCode === 200, "설정 저장 API 응답 코드가 200이 아닙니다: " + savedSettings.statusCode);
  const savedSettingsPayload = JSON.parse(savedSettings.body);
  assertOk(savedSettingsPayload.configured.tossClientSecret === true, "저장된 토스 secret 설정 상태가 true가 아닙니다.");
  assertOk(savedSettingsPayload.settings.tossClientSecret === "", "저장 응답이 토스 secret을 내려주고 있습니다.");
  assertOk(savedSettingsPayload.settings.watchlistSymbols === "TSLA,AAPL,NVDA", "저장된 관심 종목 값이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.alertRules.indexOf("priceStop=1") >= 0, "저장된 알림 규칙이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.modelDecisionThresholds.indexOf("modelBuy=75") >= 0, "저장된 모델 기준값이 응답에 없습니다.");
  assertOk(savedSettingsPayload.settings.appTheme === "dark", "저장된 화면 테마 값이 응답에 없습니다.");
  assertOk(readSqliteSetting(context.serviceDbPath, "appTheme") === "dark", "화면 테마 설정이 SQLite DB에 저장되지 않았습니다.");
  assertOk(readSqliteSetting(context.serviceDbPath, "notifyProvider") === "telegram", "알림 제공자 설정이 SQLite DB에 저장되지 않았습니다.");
  assertOk(readSqliteSetting(context.serviceDbPath, "telegramChatId") === "1234", "Telegram Chat ID 설정이 SQLite DB에 저장되지 않았습니다.");
  assertOk(readSqliteSetting(context.serviceDbPath, "tossClientSecret") === "fake-secret", "Toss secret 설정이 SQLite DB에 저장되지 않았습니다.");

  const templates = await request(port, "/api/notification-templates");
  assertOk(templates.statusCode === 200, "알림 템플릿 API 응답 코드가 200이 아닙니다: " + templates.statusCode);
  const templatesPayload = JSON.parse(templates.body);
  assertOk(Array.isArray(templatesPayload.templates), "알림 템플릿 API templates가 배열이 아닙니다.");
  assertOk(templatesPayload.templates.some(function (item) { return item.messageType === "monitorHeartbeat"; }), "상태 확인 템플릿이 없습니다.");
  assertOk(templatesPayload.templates.some(function (item) { return item.messageType === "watchlistQuote"; }), "관심종목 시세 템플릿이 없습니다.");
  assertOk(Array.isArray(templatesPayload.variables) && templatesPayload.variables.indexOf("body") >= 0, "알림 템플릿 변수 목록이 없습니다.");

  const savedTemplate = await request(port, "/api/notification-templates", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messageType: "monitorHeartbeat",
      template: "[{messageType}] {title}\n{rawLines}",
      description: "상태 확인 템플릿"
    })
  });
  assertOk(savedTemplate.statusCode === 200, "알림 템플릿 저장 API 응답 코드가 200이 아닙니다: " + savedTemplate.statusCode);
  const savedTemplatePayload = JSON.parse(savedTemplate.body);
  assertOk(savedTemplatePayload.template.template.indexOf("{rawLines}") >= 0, "저장된 알림 템플릿 응답이 맞지 않습니다.");

  const emptyAccounts = await request(port, "/api/service-accounts");
  assertOk(emptyAccounts.statusCode === 200, "계정 DB API 응답 코드가 200이 아닙니다: " + emptyAccounts.statusCode);
  const emptyAccountsPayload = JSON.parse(emptyAccounts.body);
  assertOk(Array.isArray(emptyAccountsPayload.accounts), "계정 DB API accounts가 배열이 아닙니다.");
  assertOk(emptyAccountsPayload.accounts[0].clientSecret !== "fake-secret", "계정 DB API가 secret 원문을 내려주고 있습니다.");

  const savedAccount = await request(port, "/api/service-accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      account: {
        id: "db-test",
        label: "DB 테스트",
        provider: "toss",
        baseUrl: "http://127.0.0.1:1",
        clientId: "db-client",
        clientSecret: "db-secret",
        accountSeq: "7",
        watchlistSymbols: "TSLA,AAPL,NVDA",
        notifyProvider: "telegram",
        telegramBotToken: "telegram-secret",
        telegramChatId: "9876",
        notifyLinkUrl: "http://127.0.0.1:3000"
      }
    })
  });
  assertOk(savedAccount.statusCode === 200, "계정 DB 저장 API 응답 코드가 200이 아닙니다: " + savedAccount.statusCode);
  const savedAccountPayload = JSON.parse(savedAccount.body);
  assertOk(savedAccountPayload.account && savedAccountPayload.account.clientSecret === true, "계정 DB 저장 응답이 토스 secret 설정 상태를 내려주지 않습니다.");
  assertOk(savedAccountPayload.account.telegramBotToken === true, "계정 DB 저장 응답이 텔레그램 토큰 설정 상태를 내려주지 않습니다.");

  const accountList = await request(port, "/api/service-accounts");
  const accountListPayload = JSON.parse(accountList.body);
  assertOk(accountListPayload.accounts.some(function (account) { return account.id === "db-test"; }), "계정 DB 목록에 저장한 계정이 없습니다.");

  const removedAccount = await request(port, "/api/service-accounts/db-test", { method: "DELETE" });
  assertOk(removedAccount.statusCode === 200, "계정 DB 삭제 API 응답 코드가 200이 아닙니다: " + removedAccount.statusCode);
  assertOk(JSON.parse(removedAccount.body).removed === true, "계정 DB 삭제 응답이 removed=true가 아닙니다.");

  const tossLens = await request(port, "/api/flow-lens?mock=1");
  assertOk(tossLens.statusCode === 200, "토스 판단 API 응답 코드가 200이 아닙니다: " + tossLens.statusCode);
  const tossPayload = JSON.parse(tossLens.body);
  assertOk(tossPayload.toss && Array.isArray(tossPayload.toss.positions), "토스 판단 API에 보유 종목 배열이 없습니다.");
  assertOk(tossPayload.tossDecision && Array.isArray(tossPayload.tossDecision.items), "토스 판단 API에 판단 항목이 없습니다.");
  assertOk(tossPayload.tossDecision.items.some(function (item) { return item.symbol === "AAPL"; }), "토스 판단 항목에 AAPL이 없습니다.");
  assertOk(tossPayload.tossDecision.items.some(function (item) { return item.symbol === "TSLA"; }), "토스 판단 항목에 TSLA 관심 종목이 없습니다.");
  assertOk(tossPayload.portfolio && Array.isArray(tossPayload.portfolio.markets), "토스 판단 API에 시장별 현금비중 배열이 없습니다.");
  assertOk(tossPayload.portfolio.markets.some(function (market) { return market.key === "KR"; }), "시장별 현금비중에 한국장 항목이 없습니다.");
  assertOk(tossPayload.portfolio.markets.some(function (market) { return market.key === "US"; }), "시장별 현금비중에 미국장 항목이 없습니다.");
  assertOk(tossPayload.portfolio.total > 2700000, "미국장 USD 평가액이 KRW 기준 총 평가액에 환산되지 않았습니다.");
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

  const adminRedirect = await request(port, "/admin");
  assertOk(adminRedirect.statusCode === 302 && adminRedirect.headers.location === "/admin/", "Python admin preview 디렉터리 리다이렉트가 없습니다.");

  const adminPreview = await request(port, "/admin/");
  assertOk(adminPreview.statusCode === 200, "Python admin preview 응답 코드가 200이 아닙니다: " + adminPreview.statusCode);
  assertOk(adminPreview.body.indexOf("Exit Lens Python Admin") >= 0, "Python admin preview 제목이 없습니다.");

  const adminConfig = await request(port, "/admin/config.json");
  assertOk(adminConfig.statusCode === 200, "Python admin config 응답 코드가 200이 아닙니다: " + adminConfig.statusCode);
  const adminConfigPayload = JSON.parse(adminConfig.body);
  assertOk(adminConfigPayload.mode === "github-pages-readonly-preview", "Python admin config 모드가 정적 미리보기가 아닙니다.");
  assertOk(Array.isArray(adminConfigPayload.pages) && adminConfigPayload.pages.some(function (page) { return page.id === "model-review"; }), "Python admin config에 모델 리뷰 구성이 없습니다.");
  assertOk(adminConfig.body.indexOf("fake-secret") < 0, "Python admin config가 테스트 secret을 포함했습니다.");

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
  assertOk(position.tradeStrength === 118, "토스 live 체결강도 매핑이 맞지 않습니다.");
  assertOk(position.volume === 3521000, "토스 live 거래량 매핑이 맞지 않습니다.");
  assertOk(position.foreignBuyVolume === 420000, "토스 live 외국인 매수량 매핑이 맞지 않습니다.");
  assertOk(position.institutionSellVolume === 228000, "토스 live 기관 매도량 매핑이 맞지 않습니다.");
  assertOk(position.ma20 > 0, "토스 live 캔들 기반 20일 이동평균이 없습니다.");
  assertOk(position.ma60 > 0, "토스 live 캔들 기반 60일 이동평균이 없습니다.");
  assertOk(position.ma20Distance !== 0, "토스 live 이동평균 괴리율이 계산되지 않았습니다.");
  assertOk(payload.toss.account.orderableAmount === 390000, "토스 live 매수 가능 금액이 buying-power API로 계산되지 않았습니다.");
  assertOk(payload.portfolio.cash === 390000, "토스 live 포트폴리오 현금이 buying-power API 값을 반영하지 않았습니다.");
}

async function main() {
  await checkFrontendAdminRender();
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
