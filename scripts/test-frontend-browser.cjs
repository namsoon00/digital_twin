const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { once } = require("node:events");
const { frontendDependency } = require("./frontend-toolchain.cjs");
const fixtures = require("./frontend-browser-fixtures.cjs");
const root = path.resolve(__dirname, "../public");
const screenshots = process.env.FRONTEND_SCREENSHOTS || "/tmp/orbit-frontend-screenshots";
const mime = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".webmanifest": "application/manifest+json" };
const delays = new Map();
const requests = [];
const results = [];
const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname.startsWith("/api/")) {
    requests.push({ path: url.pathname + url.search, method: request.method });
    const delay = delays.get(url.pathname + "?" + (url.searchParams.get("query") || "")) ?? delays.get(url.pathname) ?? 0;
    const data = fixtures.payload(url, { theme: request.headers["x-fixture-theme"] });
    if (delay) await new Promise(resolve => setTimeout(resolve, delay));
    response.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" });
    response.end(JSON.stringify(data)); return;
  }
  let file = path.resolve(root, "." + (url.pathname === "/" ? "/index.html" : url.pathname));
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404); response.end(); return; }
  let data = fs.readFileSync(file);
  if (url.pathname === "/" && url.searchParams.has("modules")) {
    data = data.toString().replace(/<script src="app\.js[^>]+><\/script>/, '<script>globalThis.__FRONTEND_ASSET_VERSION__="module-test";</script><script type="module" src="modules/bootstrap.mjs"></script>');
  }
  response.writeHead(200, { "content-type": mime[path.extname(file)] || "application/octet-stream", "cache-control": "no-store" }); response.end(data);
});

async function settle(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function routeTo(page, tab, detail, key, pushedDetail = false) {
  await page.evaluate(({tab, detail, key, pushedDetail}) => {
    const url = new URL(location.href);
    url.searchParams.set("tab", tab);
    if (detail) { url.searchParams.set("detail", detail); url.searchParams.set("detailKey", key); }
    else { url.searchParams.delete("detail"); url.searchParams.delete("detailKey"); }
    history.pushState(pushedDetail ? {tab, workDetail: true, detail, detailKey: key} : null, "", url);
    dispatchEvent(new PopStateEvent("popstate"));
  }, {tab, detail, key, pushedDetail});
  await page.waitForSelector('.workspace-main[data-scroll-key="' + tab + '"]');
}

async function caseInteractions(page, label) {
  await routeTo(page, "feed", "investment-case", "fixture-case");
  await page.waitForSelector('[data-investment-case-tab="history"]');
  await page.locator('[role="tab"][data-investment-case-tab="summary"]').click();
  await page.waitForSelector('.oa-insight-brief');
  assert.match(await page.locator('.oa-insight-brief').textContent(), /무엇을 확인했나.*내 투자에 어떤 의미인가.*왜 그렇게 보나.*무엇을 더 확인해야 하나/s);
  assert.match(await page.locator('.oa-insight-brief').textContent(), /다음 실적은 아직 발표되지 않았습니다/);
  assert.equal(await page.locator('.oa-case-detail-content .oa-case-lineage-chain').count(), 0, label + ' technical lineage must not precede the summary');
  await page.screenshot({path: path.join(screenshots, label + '-insight-summary.png')});
  await page.locator('[data-investment-case-tab="history"]').click();
  await page.waitForSelector('.oa-case-history-row');
  await settle(page);
  const review = page.locator('.oa-decision-review');
  await review.waitFor();
  await review.scrollIntoViewIfNeeded();
  assert.equal(await review.getAttribute('data-review-state'), 'data-gap');
  assert.match(await review.textContent(), /성공·실패 판정을 보류/);
  assert.match(await review.textContent(), /비교 지수 자료 없음/);
  const reviewBounds = await review.evaluate(node => ({width: node.clientWidth, content: node.scrollWidth}));
  assert(reviewBounds.content <= reviewBounds.width + 1, label + ' decision review overflows');
  await review.screenshot({path: path.join(screenshots, label + '-decision-review.png')});
  await page.locator('[data-investment-case-tab="history"]').click();
  await page.waitForSelector('.oa-case-history-row');
  await settle(page);
  const before = await page.locator('.work-detail-backdrop').evaluate(node => {
    node.scrollTop = 900; window.__caseScroller = node; return node.scrollTop;
  });
  assert(before > 300, label + " fixture must actually scroll");
  // Real delegated click handlers, same-task A -> short panel -> B -> A.
  // DOM click avoids Playwright scrolling the sticky tab into view for us.
  await page.evaluate(() => {
    for (const tab of ["current", "evidence", "history", "summary", "history"]) {
      document.querySelector('[data-investment-case-tab="' + tab + '"]').click();
    }
  });
  await settle(page);
  const after = await page.locator('.work-detail-backdrop').evaluate(node => {
    if (node !== window.__caseScroller) throw Error("Case tab replaced its scroller");
    return node.scrollTop;
  });
  assert(Math.abs(after - before) < 3, `${label} case scroll ${before} -> ${after}`);
  assert.equal(await page.locator('[data-investment-case-panel-key]').getAttribute("data-investment-case-panel-tab"), "history");
  await page.screenshot({path: path.join(screenshots, label + "-case-scroll.png")});
  await page.locator('button[data-work-detail-close]').first().click();
  await page.waitForSelector('[data-work-detail-dialog]', {state: "detached"});

  // A slow legacy-key resolution must not reopen the closed detail or rewrite
  // its successor's route. Closing exercises real history.back return behavior.
  delays.set("/api/decisions/fixture-legacy", 900);
  const response = page.waitForResponse(response => new URL(response.url()).pathname === "/api/decisions/fixture-legacy");
  await routeTo(page, "feed", "investment-case", "fixture-legacy", true);
  await page.waitForSelector('[data-work-detail-key="fixture-legacy"]');
  await page.locator('button[data-work-detail-close]').first().click();
  await page.waitForSelector('[data-work-detail-dialog]', {state: "detached"});
  await routeTo(page, "portfolio");
  await response;
  await settle(page);
  assert.equal(await page.locator('[data-work-detail-dialog]').count(), 0, label + " delayed response reopened detail");
  assert.equal(new URL(page.url()).searchParams.get("tab"), "portfolio");
  assert.equal(new URL(page.url()).searchParams.get("detailKey"), null);
  delays.delete("/api/decisions/fixture-legacy");
  results.push({test: label + " case tabs / delayed close", before, after});
}

async function insightFirstScreens(page, label) {
  await routeTo(page, "notifications");
  const alertFilters = page.locator('#disclosure-alerts-filters');
  await alertFilters.waitFor();
  const reset = alertFilters.locator('[data-action="reset-notification-job-filters"]');
  if (await reset.isEnabled()) {
    if (await alertFilters.getAttribute('open') === null) await alertFilters.locator(':scope > summary').click();
    await reset.click();
    if (await alertFilters.getAttribute('open') !== null) await alertFilters.locator(':scope > summary').click();
  }
  await page.locator('[data-console-row-key="job-001"]').waitFor();
  const delivery = page.locator('#disclosure-alert-delivery-job-001');
  assert.equal(await delivery.getAttribute('open'), null, label + ' delivery state not secondary');
  await delivery.locator(':scope > summary').click();
  assert(await delivery.locator('.oa-secondary-content').isVisible(), label + ' delivery state inaccessible');
  await delivery.locator(':scope > summary').click();
  await routeTo(page, "overview");
  await page.locator('#disclosure-today-awaiting').waitFor();
  const today = page.locator('[data-console-keyed-list="today-primary"]');
  assert.doesNotMatch(await today.textContent(), /Synthetic alert|검증용 기업 5|미확정|전달 상태/);
  await page.locator('#disclosure-today-awaiting > summary').click();
  assert.match(await page.locator('#disclosure-today-awaiting').textContent(), /검증용 기업 5/);
  await page.locator('#disclosure-today-awaiting > summary').click();
  await page.locator('#disclosure-today-operations > summary').click();
  assert.match(await page.locator('[data-console-keyed-list="today-operations"]').textContent(), /Synthetic alert 1/);
  await page.locator('#disclosure-today-operations > summary').click();
  await page.evaluate(() => { window.scrollTo(0, 0); document.querySelector('.workspace-main').scrollTop = 0; });
  await settle(page);
  await page.screenshot({path: path.join(screenshots, label + '-today-reading.png')});
  await routeTo(page, "modeling");
  const first = page.locator('.oa-case-row').first();
  await first.waitFor();
  await page.evaluate(() => { window.scrollTo(0, 0); document.querySelector('.workspace-main').scrollTop = 0; });
  await settle(page);
  const bounds = await first.evaluate(node => ({top: node.getBoundingClientRect().top, viewport: innerHeight, width: node.clientWidth, content: node.scrollWidth}));
  assert(bounds.top < bounds.viewport - 140, label + ' first opinion is below the first viewport: ' + JSON.stringify(bounds));
  assert(bounds.content <= bounds.width + 1, label + ' opinion text overflows');
  await page.screenshot({path: path.join(screenshots, label + '-opinions-first.png')});
  assert.equal(await page.locator('.oa-case-row').count(), 4, label + ' pending record mixed into opinions');
  await page.locator('[data-decision-view="review"]').first().click();
  await page.waitForFunction(() => document.querySelectorAll('.oa-case-row').length === 1);
  assert.match(await page.locator('.oa-case-row').textContent(), /검증용 기업 5/);
  assert.match(await page.locator('.oa-case-row').textContent(), /투자 의견 미확정/);
  await page.locator('[data-decision-view="attention"]').first().click();
  await page.waitForFunction(() => document.querySelectorAll('.oa-case-row').length === 4);
  await page.locator('.oa-decision-filter-sheet > summary').click();
  assert(await page.locator('[data-console-decision-filter="scope"]').isVisible(), label + ' scope filter is unreachable');
  await page.locator('.oa-decision-filter-sheet > summary').click();
  await first.click();
  await page.locator('[role="tab"][data-investment-case-tab="summary"]').click();
  await page.locator('.oa-insight-brief').waitFor();
  if (page.viewportSize().width >= 1200) assert(await page.locator('.oa-case-siblings').isVisible(), label + ' desktop record navigation missing');
  const quickLink = await page.locator('.oa-insight-brief [data-investment-case-tab="evidence"]').first().boundingBox();
  assert(quickLink.height >= 44, label + ' quick link touch target is too small');
  await page.screenshot({path: path.join(screenshots, label + '-brief.png')});
  const questions = await page.locator('.oa-reading-questions').evaluate(node => ({width: node.clientWidth, content: node.scrollWidth}));
  assert(questions.content <= questions.width + 1, label + ' investment explanation overflows');
  await page.locator('.oa-insight-brief [data-investment-case-tab="reasoning"]').click();
  await page.locator('.oa-reading-models').waitFor();
  assert.match(await page.locator('.oa-reading-models').textContent(), /수요가 매출에 반영되는가.*주문 증가/s);
  const modelRecords = page.locator('.oa-secondary-details').filter({has: page.locator('summary strong', {hasText: '모델·규칙과 성립 조건 전체'})});
  assert.equal(await modelRecords.getAttribute('open'), null, label + ' technical model detail is not secondary');
  await modelRecords.locator(':scope > summary').click();
  assert(await page.locator('.oa-reasoning-inventory').isVisible(), label + ' full model records inaccessible');
  await modelRecords.locator(':scope > summary').click();
  await page.screenshot({path: path.join(screenshots, label + '-model-reading.png')});
  await page.locator('[role="tab"][data-investment-case-tab="summary"]').click();
  await page.locator('.oa-insight-brief [data-investment-case-tab="evidence"]').first().click();
  await page.waitForSelector('[data-investment-case-panel-tab="evidence"]');
  await page.locator('button[data-work-detail-close]').first().click();
  await page.waitForSelector('[data-work-detail-dialog]', {state: 'detached'});
  await page.locator('[data-action="command-palette"][data-command-palette-mode="search"]').first().click();
  await page.locator('[data-command-palette-key="calendar"][data-command-palette-result="tab"] strong').click();
  await page.waitForSelector('.workspace-main[data-scroll-key="calendar"]');
  assert.equal(await page.locator('[data-command-palette-dialog]').count(), 0, label + ' palette did not navigate');
  await page.screenshot({path: path.join(screenshots, label + '-calendar-first.png')});
  results.push({test: label + ' insight-first layout and real palette navigation', ...bounds});
}

async function hypothesisScheduling(page, label) {
  await routeTo(page, "modeling", "investment-model-management", "fixture-model");
  await page.locator('[data-investment-model-management-tab="validation"]').click();
  await page.waitForSelector('[data-hypothesis-development-select="fixture-development"]');
  await page.locator('[data-hypothesis-development-select="fixture-development"]').click();
  const retry = page.locator('.hypothesis-development-retry').first();
  await retry.scrollIntoViewIfNeeded();
  assert.match(await retry.textContent(), /개발·명세 수정 필요/);
  assert.match(await retry.textContent(), /기능 보완.*모델 계약 등록/s);
  assert.match(await retry.textContent(), /미조회 자료 확인 필요.*결측 여부는 확인되지 않았습니다/s);
  assert.match(await retry.textContent(), /예약 없음/);
  assert.equal(await page.locator('[data-hypothesis-development-approve="fixture-development"]').isDisabled(), true);
  const modelAssessments = page.locator('.hypothesis-model-assessments');
  assert.match(await modelAssessments.textContent(), /조건 미충족.*위험 이벤트 이후 가격 방어.*미충족 1개.*확인 불가 0개/s);
  const bounds = await retry.evaluate(node => ({width: node.clientWidth, content: node.scrollWidth}));
  assert(bounds.content <= bounds.width + 1, label + ' hypothesis retry overflows');
  await retry.screenshot({path: path.join(screenshots, label + '-hypothesis-retry.png')});
  await modelAssessments.scrollIntoViewIfNeeded();
  const modelBounds = await modelAssessments.evaluate(node => ({width: node.clientWidth, content: node.scrollWidth}));
  assert(modelBounds.content <= modelBounds.width + 1, label + ' model assessments overflow');
  await modelAssessments.screenshot({path: path.join(screenshots, label + '-hypothesis-model-assessments.png')});
  await page.locator('[data-hypothesis-development-select="fixture-observation"]').click();
  await page.waitForFunction(() => document.querySelector('.hypothesis-development-retry')?.textContent.includes('관측 기간 대기'));
  assert.match(await page.locator('.hypothesis-development-retry').textContent(), /관측 기간 대기/);
  await page.locator('button[data-work-detail-close]').first().click();
  await page.waitForSelector('[data-work-detail-dialog]', {state: 'detached'});
  results.push({test: label + ' hypothesis blockers and approval gate', ...bounds});
}

async function instrumentChart(page, label) {
  await routeTo(page, "feed");
  await page.addScriptTag({url: "/vendor/lightweight-charts.standalone.production.js?v=5.2.1"});
  await page.evaluate(() => {
    const engine = window.LightweightCharts;
    window.__chartMetrics = {created: 0, removed: 0, candleCount: 0};
    window.LightweightCharts = Object.assign({}, engine, {createChart(...args) {
      const chart = engine.createChart(...args);
      window.__chartMetrics.created++;
      const remove = chart.remove.bind(chart);
      chart.remove = () => { window.__chartMetrics.removed++; return remove(); };
      const add = chart.addSeries.bind(chart);
      chart.addSeries = (type, ...options) => {
        const series = add(type, ...options); const setData = series.setData.bind(series);
        series.setData = data => { if (type === engine.CandlestickSeries) window.__chartMetrics.candleCount = data.length; return setData(data); };
        return series;
      };
      return chart;
    }});
  });
  await page.locator('[data-work-detail="market-instrument"]').first().click();
  await page.waitForSelector('[data-work-detail-dialog]');
  assert.equal(new URL(page.url()).searchParams.get("token"), "fixture-readonly", "Deep-link auth query was lost");
  await page.locator('[data-instrument-workspace-tab="chart"]').click();
  await page.waitForSelector('[data-instrument-candle-chart] canvas', {timeout: 15000});
  // Keep the measured pixels from the successful frame, not a later redraw.
  const paintedFrame = await page.waitForFunction(() => {
    const maximum = Math.max(...[...document.querySelectorAll('[data-instrument-candle-chart] canvas')].map(canvas => {
      const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
      const colors = new Set();
      for (let i = 0; i < data.length; i += 4) if (data[i+3]) colors.add(`${data[i]},${data[i+1]},${data[i+2]}`);
      return colors.size;
    }));
    return maximum > 10 ? maximum : false;
  });
  const pixels = await paintedFrame.jsonValue();
  await paintedFrame.dispose();
  assert(pixels > 10, "Chart canvas is blank or uniform");
  assert.match(await page.locator('.instrument-chart-meta').textContent(), /41개/);
  assert.match(await page.locator('.instrument-source-strip').textContent(), /MOCK synthetic candles.*41건/);
  assert.equal(await page.evaluate(() => window.__chartMetrics.candleCount), 41);
  await page.screenshot({path: path.join(screenshots, label + "-chart.png")});
  await page.locator('button[data-work-detail-close]').first().click();
  await page.waitForSelector('[data-work-detail-dialog]', {state: "detached"});
  await settle(page);
  const metrics = await page.evaluate(() => window.__chartMetrics);
  assert.equal(metrics.created, metrics.removed, "Chart instances not disposed");
  assert.equal(await page.locator('[data-instrument-candle-chart] canvas').count(), 0);
  results.push({test: label + " chart", colors: pixels, ...metrics});
}

async function run() {
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  const origin = "http://127.0.0.1:" + server.address().port;
  const { chromium } = frontendDependency("playwright");
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || (fs.existsSync(chromium.executablePath()) ? chromium.executablePath()
    : fs.existsSync("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined);
  let browser;
  try {
    browser = await chromium.launch({ headless: true, executablePath });
    fs.mkdirSync(screenshots, { recursive: true });
    for (const mode of ["bundle", "modules"]) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: "block", reducedMotion: "reduce" });
      await context.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await context.addInitScript(() => { window.WebSocket = undefined; });
      const page = await context.newPage(); const errors = [];
      page.on("pageerror", error => { errors.push(error.stack || error.message); console.error(mode + ": " + error.message); });
      const prefix = mode === "modules" ? "modules=1&" : "";
      await page.goto(origin + "/?" + prefix + "tab=feed&token=fixture-readonly");
      await page.waitForSelector('.workspace-main[data-scroll-key="feed"]', { timeout: 15000 });
      await page.waitForSelector('[data-work-detail="market-instrument"]');
      assert.deepEqual(errors, [], mode + " initial render");
      for (const tab of ["portfolio", "calendar", "modeling", "notifications", "experiments", "settings", "operations", "overview", "feed"]) {
        await page.evaluate(tab => { history.pushState(null, "", "?tab=" + tab + "&token=fixture-readonly"); dispatchEvent(new PopStateEvent("popstate")); }, tab);
        await page.waitForSelector('.workspace-main[data-scroll-key="' + tab + '"]');
      }
      await page.screenshot({ path: path.join(screenshots, mode + "-desktop.png") });
      assert.deepEqual(errors, [], mode + " all workspaces");

      // All changes happen in the same task: only the final tab/detail may win.
      await page.evaluate(() => {
        for (const tab of ["notifications", "portfolio", "feed", "notifications", "feed"]) {
          history.pushState(null, "", "?tab=" + tab + "&token=fixture-readonly"); dispatchEvent(new PopStateEvent("popstate"));
        }
      });
      await page.waitForSelector('.workspace-main[data-scroll-key="feed"]');
      await instrumentChart(page, mode);
      await insightFirstScreens(page, mode + "-desktop");
      await caseInteractions(page, mode + "-desktop");
      await hypothesisScheduling(page, mode + "-desktop");
      if (mode === "modules") {
        const accountScope = await page.evaluate(async () => {
          const { decisionsState } = await import('/modules/state/decisions.mjs');
          const { marketState } = await import('/modules/state/market.mjs');
          const { selectConsoleDecisionRows } = await import('/modules/decisions/selectors.mjs');
          const { selectConsoleInstrumentRows } = await import('/modules/market/selectors.mjs');
          const priorFlow = decisionsState.investmentFlow, priorMarket = marketState.marketReadModel;
          try {
            decisionsState.investmentFlow = {items: [
              {caseId: 'a-case', subjectCaseId: 'a-subject', detailType: 'subject-decision-case', accountId: 'a', symbol: 'SAME', updatedAt: new Date().toISOString(), decision: {action: 'HOLD'}},
              {caseId: 'b-case', accountId: 'b', symbol: 'SAME', updatedAt: new Date().toISOString(), decision: {action: 'SELL'}}
            ]};
            marketState.marketReadModel = {items: []};
            const snapshot = {toss: {accountId: 'a', positions: [{symbol: 'SAME', source: 'toss', currentPrice: 1}], watchlist: []}, investmentAnalysis: {contract: 'fixture', actionQueue: []}};
            return {roles: selectConsoleDecisionRows(snapshot).map(row => ({accountId: row.accountId, source: row.source})), link: selectConsoleInstrumentRows(snapshot)[0].decision};
          } finally { decisionsState.investmentFlow = priorFlow; marketState.marketReadModel = priorMarket; }
        });
        assert.deepEqual(accountScope.roles.sort((a, b) => a.accountId.localeCompare(b.accountId)), [{accountId: 'a', source: 'holding'}, {accountId: 'b', source: 'unknown'}]);
        assert.equal(accountScope.link.consoleKey, 'a-subject');
        assert.equal(accountScope.link.consoleDetailType, 'investment-case');
        results.push({test: 'account-scoped membership and canonical market decision links'});
        const result = await page.evaluate(async () => {
          const { openWorkDetailLayer, closeWorkDetailLayer } = await import("/modules/navigation/detail.mjs");
          const { notificationsState } = await import("/modules/state/notifications.mjs");
          const { loadNotificationJobDetailSection } = await import("/modules/notifications/requests.mjs");
          openWorkDetailLayer("notification-job", "job-001");
          openWorkDetailLayer("notification-job", "job-002");
          await loadNotificationJobDetailSection("job-002", "ai-review", true);
          notificationsState.notificationJobDetailTabs["job-002"] = "ai-review";
          const { render } = await import("/modules/render/scheduler.mjs");
          render();
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          return document.querySelector("[data-work-detail-dialog]").getAttribute("data-work-detail-key");
        });
        assert.equal(result, "job-002", "Rapid detail navigation rendered the older record");
        await page.locator('button[data-work-detail-close]').first().click();
        await page.waitForFunction(() => !document.querySelector('[data-work-detail-key="job-002"]'));
      }
      assert.deepEqual(errors, [], mode + " chart/detail navigation");
      await context.setExtraHTTPHeaders({"x-fixture-theme": "dark"});
      await page.goto(origin + "/?" + prefix + "tab=feed&token=fixture-readonly");
      await page.waitForSelector('html[data-theme="dark"] .workspace-main[data-scroll-key="feed"]');
      await page.waitForSelector('[data-work-detail="market-instrument"]');
      await page.screenshot({path: path.join(screenshots, mode + "-desktop-dark.png")});
      await instrumentChart(page, mode + "-dark");
      await insightFirstScreens(page, mode + "-desktop-dark");
      assert.deepEqual(errors, [], mode + " dark theme");
      await context.close();
      console.log(mode + ": nine workspaces, rapid navigation, auth query, chart pixels and detail cleanup passed");
    }

    for (const mode of ["bundle", "modules"]) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, serviceWorkers: "block", reducedMotion: "reduce" });
    await context.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await context.addInitScript(() => { window.WebSocket = undefined; });
    const page = await context.newPage(); const errors = [];
    page.on("pageerror", error => errors.push(error.stack || error.message));
    delays.set("/api/notification-jobs", 1000);
    await page.goto(origin + "/?" + (mode === "modules" ? "modules=1&" : "") + "tab=notifications&token=fixture-readonly");
    await page.waitForSelector('[data-console-row-key="job-001"]');
    assert.match(await page.locator('[data-console-row-key="job-001"] .oa-alert-reason').textContent(), /본문 미리보기.*Synthetic verified evidence/s);
    assert.match(await page.locator('[data-console-row-key="job-002"] .oa-alert-reason').textContent(), /이유.*수요 전망/s);
    await page.locator('[data-console-row-key="job-001"]').evaluate(node => { window.__retainedFixtureRow = node; });
    await page.evaluate(() => {
      const scroller = document.querySelector(".workspace-main");
      scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight - 500);
      window.scrollTo(0, document.documentElement.scrollHeight - innerHeight - 500);
    });
    await settle(page);
    const appendBefore = await page.evaluate(() => ({window: scrollY, workspace: document.querySelector(".workspace-main").scrollTop}));
    assert(appendBefore.window + appendBefore.workspace > 300, mode + " append must test a scrolled viewport");
    await page.waitForSelector('[data-console-row-key="job-021"]', { timeout: 15000 });
    await settle(page);
    const appendAfter = await page.evaluate(() => ({window: scrollY, workspace: document.querySelector(".workspace-main").scrollTop}));
    for (const key of ["window", "workspace"]) assert(Math.abs(appendBefore[key] - appendAfter[key]) < 12, `${mode} append ${key}: ${appendBefore[key]} -> ${appendAfter[key]}`);
    assert(await page.locator('[data-console-row-key="job-001"]').evaluate(node => node === window.__retainedFixtureRow), "Infinite append replaced an existing row");
    const count = await page.locator(".oa-alert-card").count();
    const unique = await page.locator(".oa-alert-card").evaluateAll(nodes => new Set(nodes.map(node => node.getAttribute("data-console-row-key"))).size);
    assert.equal(count, unique, "Infinite append duplicated rows");

    await page.evaluate(() => { window.scrollTo(0, 1400); document.querySelector(".workspace-main").scrollTop = 1400; });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const scrollBefore = await page.evaluate(() => window.scrollY || document.querySelector(".workspace-main").scrollTop);
    await page.locator('.tab-bar [data-tab="feed"]').click();
    await page.waitForSelector('.workspace-main[data-scroll-key="feed"]');
    await page.locator('.tab-bar [data-tab="notifications"]').click();
    await page.waitForSelector('.workspace-main[data-scroll-key="notifications"]');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const scrollAfter = await page.evaluate(() => window.scrollY || document.querySelector(".workspace-main").scrollTop);
    assert(Math.abs(scrollAfter - scrollBefore) < 12, `Tab scroll retention: ${scrollBefore} -> ${scrollAfter}`);
    await page.screenshot({ path: path.join(screenshots, mode + "-mobile-retained-scroll.png") });
    results.push({test: mode + " mobile append/tab retention", appendBefore, appendAfter, tabBefore: scrollBefore, tabAfter: scrollAfter, count, unique});

    delays.set("/api/notification-jobs?alert 1", 800);
    delays.set("/api/notification-jobs?alert 2", 20);
    if (mode === "modules") await page.evaluate(async () => {
      const { notificationsState } = await import("/modules/state/notifications.mjs");
      const { loadNotificationJobs, resetNotificationJobsPaging } = await import("/modules/notifications/requests.mjs");
      resetNotificationJobsPaging(); notificationsState.notificationJobSearch = "alert 1";
      const stale = loadNotificationJobs();
      resetNotificationJobsPaging(); notificationsState.notificationJobSearch = "alert 2";
      await Promise.all([stale, loadNotificationJobs()]);
      if (!notificationsState.notificationJobItems.every(job => job.title.includes("alert 2"))) throw new Error("Stale list replaced latest query");
    });
    await page.screenshot({ path: path.join(screenshots, mode + "-mobile-inbox.png") });
    await page.evaluate(() => { window.scrollTo(0, 0); document.querySelector(".workspace-main").scrollTop = 0; });
    await page.screenshot({ path: path.join(screenshots, mode + "-mobile-inbox-top.png") });
    await caseInteractions(page, mode + "-mobile");
    await insightFirstScreens(page, mode + "-mobile");
    await hypothesisScheduling(page, mode + "-mobile");
    await context.setExtraHTTPHeaders({"x-fixture-theme": "dark"});
    for (const width of [360, 430]) {
      await page.setViewportSize({width, height: 844});
      await page.goto(origin + "/?" + (mode === "modules" ? "modules=1&" : "") + "tab=modeling&token=fixture-readonly");
      await page.waitForSelector('html[data-theme="dark"] .oa-case-row');
      await insightFirstScreens(page, mode + "-mobile-dark-" + width);
    }
    assert.deepEqual(errors, [], "Mobile append and stale response");
    await context.close();
    console.log(mode + " mobile: measured append/tab/case scroll, retained row identity, append deduplication and delayed close passed");
    }
    fs.writeFileSync(path.join(screenshots, "browser-validation.json"), JSON.stringify({fixture: "synthetic, isolated HTTP server; no live data", results, requests}, null, 2));
    console.log("Screenshots: " + screenshots);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; if (server.listening) server.close(); });
