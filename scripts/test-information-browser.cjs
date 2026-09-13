const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { once } = require("node:events");
const { frontendDependency } = require("./frontend-toolchain.cjs");
const fixtures = require("./frontend-browser-fixtures.cjs");
const root = path.resolve(__dirname, "../public");
const screenshots = "/tmp/orbit-information-screenshots";
const stamp = new Date().toISOString();
const evidence = {
  evidenceId: "fixture-information", kind: "news", symbol: "TEST01", source: "검증용 공식 출처",
  title: "Example Company publishes its quarterly results", translatedTitleKo: "검증용 기업이 분기 실적을 발표했습니다", publishedAt: stamp,
  promptEvidenceAdmission: {freshnessState: "fresh"},
  informationBrief: {summary: "검증용 기업의 분기 매출이 전년 동기보다 증가했습니다.", facts: [{label: "기사에 기재", text: "The company reported quarterly revenue of $100 million.", sourceUrl: "https://example.org/report"}],
    interpretation: "매출 증가가 이익 개선으로 이어졌는지는 비용과 이익률 자료를 함께 봐야 합니다.", followUps: [{text: "다음 분기 영업이익률을 확인합니다.", statusLabel: "자동 관찰 미등록"}],
    sourceUrl: "https://example.org/report", publishedAt: stamp, collectedAt: stamp, independentSourceCount: 1, sourceHash: "a".repeat(64), sourceAgeHours: 0.1},
  storyTimeline: [{title: "검증용 최초 기사", source: "검증 출처", publishedAt: stamp, state: "active"}, {title: "검증용 후속 기사", source: "검증 출처", publishedAt: stamp, state: "active"}]
};
const calendar = {
  eventId: "fixture-result", title: "검증용 소비자물가지수 발표", startsAt: stamp, eventType: "macro", status: "active", markets: ["US"], symbols: [], source: "BLS", sourceUrl: "https://www.bls.gov", reminderOffsetsMinutes: [],
  payload: {timeState: "operationalDefault"},
  releaseInformation: {status: "released", statusLabel: "공식 발표 결과 확보", collection: {enabled: true, state: "scheduled", nextAttemptAt: stamp}, comparison: {reason: "시장 예상치는 수집하지 않았습니다."},
    release: {referencePeriod: "검증용 기간", releasedAt: stamp, releasedDate: stamp.slice(0,10), firstCollectedAt: stamp, lastCollectedAt: stamp, sourceUrl: "https://www.bls.gov", source: "BLS",
      metrics: [{label: "소비자물가 전월 대비", actual: 0, previous: 0.1, unit: "%", excerpt: "Synthetic official source excerpt."}]}}
};
evidence.informationBrief.marketReaction = {version:"information-price-observation-v1",label:"공개 전후 가격 관측",note:"시간상 전후 비교이며 사건의 인과관계를 의미하지 않습니다.",observations:[{symbol:"TEST01",horizonMinutes:60,status:"observed",priceChangePercent:2,baseline:{price:100,sourceAsOf:stamp,provider:"fixture"},outcome:{price:102,sourceAsOf:stamp,provider:"fixture",currency:"USD"}}]};
calendar.releaseInformation.latestStatistics = {label:"최근 공표 통계 · 보관된 조회본",referencePeriod:"2026-08",source:"BLS Public Data API",sourceUrl:"https://api.bls.gov/",fetchedAt:stamp,ageHours:24,freshnessState:"stale",note:"최초 발표값이나 발표 전 예상치가 아닙니다.",metrics:[{label:"소비자물가 전월 대비",actual:0.4,unit:"%",formula:"(current / previous - 1) * 100",inputs:[{seriesId:"CUSR0000SA0",period:"2026-08",value:100.4},{seriesId:"CUSR0000SA0",period:"2026-07",value:100}]}]};
const server = http.createServer((request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname.startsWith("/api/")) {
    let data;
    if (url.pathname === "/api/research-evidence/fixture-information") data = {item: evidence};
    else if (url.pathname === "/api/investment-calendar/events/fixture-result") data = {event: calendar};
    else if (["/api/market/evidence", "/api/research-evidence"].includes(url.pathname)) data = {items: [evidence], total: 1, summary: {}};
    else if (url.pathname.includes("investment-calendar")) data = {events: [calendar], candidates: [], summary: {total: 1}};
    else data = fixtures.payload(url);
    response.writeHead(200, {"content-type": "application/json", "cache-control": "no-store"});
    response.end(JSON.stringify(data)); return;
  }
  const file = path.resolve(root, "." + (url.pathname === "/" ? "/index.html" : url.pathname));
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {response.writeHead(404); response.end(); return;}
  const mime = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".webmanifest": "application/manifest+json"};
  response.writeHead(200, {"content-type": mime[path.extname(file)] || "application/octet-stream"}); response.end(fs.readFileSync(file));
});

async function run() {
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  const origin = "http://127.0.0.1:" + server.address().port;
  const { chromium } = frontendDependency("playwright");
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || (fs.existsSync(chromium.executablePath()) ? chromium.executablePath() : "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
  const browser = await chromium.launch({headless: true, executablePath});
  fs.mkdirSync(screenshots, {recursive: true});
  try {
    for (const width of [1440, 390, 360]) {
      const context = await browser.newContext({viewport: {width, height: 900}, reducedMotion: "reduce", serviceWorkers: "block"});
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      await page.goto(origin + "/?tab=feed&detail=research-evidence&detailKey=fixture-information&token=fixture-readonly");
      const brief = page.locator('[data-work-detail-dialog] .information-brief');
      await brief.waitFor();
      assert.match(await brief.textContent(), /요약 · 분석.*원문과 대조한 내용.*의미 · 분석 의견.*자동 관찰 미등록/s);
      assert.match(await brief.textContent(), /같은 사건의 보도 이력/);
      assert.match(await brief.textContent(), /공개 전후 가격 관측.*\+2.00%/s);
      assert.equal(await brief.locator('a[href^="https://"]').count(), 2);
      let bounds = await brief.evaluate(node => ({width: node.clientWidth, content: node.scrollWidth}));
      assert(bounds.content <= bounds.width + 1, "News overflow " + width);
      await page.screenshot({path: path.join(screenshots, "news-" + width + ".png")});
      await page.goto(origin + "/?tab=calendar&detail=investment-calendar-event&detailKey=fixture-result&token=fixture-readonly");
      const result = page.locator('[data-work-detail-dialog] .calendar-release-information');
      await result.waitFor();
      assert.match(await result.textContent(), /공식 발표 결과 확보.*0%.*직전 기간 0.1%/s);
      assert.match(await result.textContent(), /최근 공표 통계.*최초 발표값이나 발표 전 예상치가 아닙니다/s);
      assert.match(await result.textContent(), /수집 후 24시간 경과.*재확인이 지연/s);
      await result.locator("details summary").first().click();
      assert(await result.getByText("Synthetic official source excerpt.").isVisible());
      await result.getByText("통계 원값과 계산식", {exact:true}).click();
      assert(await result.getByText(/CUSR0000SA0/).isVisible());
      bounds = await result.evaluate(node => ({width: node.clientWidth, content: node.scrollWidth}));
      assert(bounds.content <= bounds.width + 1, "Calendar overflow " + width);
      await page.screenshot({path: path.join(screenshots, "calendar-" + width + ".png")});
      assert.deepEqual(errors, []);
      await context.close();
      console.log("Information views: " + width + "px, source links, disclosure controls and no overflow passed");
    }
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
run().catch(error => {console.error(error); process.exitCode = 1; if (server.listening) server.close();});
