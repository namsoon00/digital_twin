const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { once } = require('node:events');
const { frontendDependency } = require('./frontend-toolchain.cjs');
const root = path.resolve(__dirname, '../public');
const result = {summary:'현재 화면의 새 분석',hypothesis:'중기 약세 안의 단기 회복일 수 있습니다.',portfolioImpact:'보유 손실의 회복 여부를 구분해 봅니다.',
  development:{requestId:'development-fixture',status:'pending'},developmentQuestions:['가설 개선 질문 <script>bad</script>'],
  quality:{status:'rejected',errors:['가격과 매입가 항목이 다릅니다.'],review:{reason:'<script>bad</script>'}},
  claimEvidence:{summary:[{factId:'fact',field:'currentPrice',period:'current'}]},
  followUpEvaluations:[{description:'평균 가격 회복을 확인합니다.',status:'expired',reason:'기간 내 자료 없음'}],
  publication:{status:'queued',deliveryStatus:'done',reason:'전송 성공',receipt:{body:'실제 전송된 과거 원문 <b>증거</b>',deliveredAt:'2026-10-01T03:00:00Z'}},
  input:{name:'테스트 종목',facts:[{currentPrice:100,currency:'KRW',sourceAsOf:'2026-10-01T03:00:00Z'}]}};
const server = http.createServer((req,res) => {
  if (req.url === '/api/ai-control/status') {res.setHeader('content-type','application/json');res.end(JSON.stringify({enabled:true,configuredEnabled:true,tasksStartedToday:2,dailyTaskBudget:48,activeTaskCount:1,dailyCallBudget:24,
    modelCallsUsedToday:24,observationScheduling:{status:'budget-wait',reason:'ai-call-budget-exhausted',nextCheckAt:'2026-10-02T00:00:00Z'},
    qualitySummary:{accepted:0,rejected:1},tasks:[{taskId:'fixture',symbol:'TEST',status:'completed',capability:'observe',result}],callsToday:[]}));return;}
  const file = path.resolve(root,'.'+req.url);
  if (!file.startsWith(root+path.sep) || !fs.existsSync(file)) {res.writeHead(404);res.end();return;}
  res.setHeader('content-type',file.endsWith('.mjs')?'application/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(file));
});
(async () => {
 const { chromium } = frontendDependency('playwright');
 const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || (fs.existsSync(chromium.executablePath()) ? chromium.executablePath() : '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
 let browser;
 try {
  server.listen(0,'127.0.0.1'); await once(server,'listening');
  browser = await chromium.launch({headless:true,executablePath});
  for (const width of [390,1280]) {
   const page = await browser.newPage({viewport:{width,height:1000}});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto('http://127.0.0.1:'+server.address().port+'/ai-control.html');
   await page.locator('article').waitFor();
   await page.getByText('온톨로지 개선 · 현재 진행 기록 없음',{exact:true}).click();
   assert.match(await page.locator('article').innerText(),/가설 개선 질문 <script>bad<\/script>/);
   assert.equal(await page.locator('article script').count(),0);
   assert.match(await page.locator('#overview').innerText(),/사용 한도로 대기/);
   assert.match(await page.locator('#overview').innerText(),/24 \/ 24/);
   assert.match(await page.locator('#overview').innerText(),/한도 갱신/);
   await page.route('**/api/ai-control/settings', async route => {
    assert.equal(route.request().postDataJSON().aiControlBudgetEnabled,'false');
    await route.fulfill({json:{saved:true}});
   });
   await page.locator('#budgetEnabled').uncheck();
   assert(await page.locator('#callBudget').isDisabled());
   assert(await page.locator('#taskBudget').isDisabled());
   await page.route('**/api/ai-control/status', route => route.fulfill({json:{enabled:true,configuredEnabled:true,budgetEnabled:false,
    tasksStartedToday:50,dailyTaskBudget:48,modelCallsUsedToday:30,dailyCallBudget:24,activeTaskCount:1,
    observationScheduling:{status:'ready'},qualitySummary:{accepted:0,rejected:1},
    tasks:[{taskId:'fixture',symbol:'TEST',status:'completed',capability:'observe',result}],callsToday:[]}}));
   await page.locator('#settings button').click();
   await page.getByText('30 / 제한 없음',{exact:true}).waitFor();
   assert.match(await page.locator('#overview').innerText(),/50 \/ 제한 없음/);
   assert.doesNotMatch(await page.locator('#overview').innerText(),/사용 한도로 대기/);
   assert.match(await page.locator('article').innerText(),/품질 검토 보류/);
   await page.getByText(/실제 발송 원문 ·/).click();
   assert.match(await page.locator('article').innerText(),/실제 전송된 과거 원문 <b>증거<\/b>/);
   await page.getByText('이전 설명의 확인 결과',{exact:true}).click();
   assert.match(await page.locator('article').innerText(),/기간 만료 · 평가 불가/);
   await page.getByText('문장별 인용과 검토 기록',{exact:true}).click();
   assert.equal(await page.locator('article script').count(),0);
   const diagnosticResult = {...result, publication:{status:'recorded',reason:'검증 보류',diagnostic:{status:'queued',deliveryStatus:'done',reason:'운영 채널 발송 완료',
    receipt:{body:'검증되지 않은 AI 원문 <script>bad</script>',deliveredAt:'2026-10-01T04:00:00Z'}}}};
   await page.route('**/api/ai-control/status', route => route.fulfill({json:{enabled:true,budgetEnabled:false,
    observationScheduling:{status:'ready'},tasks:[{taskId:'diagnostic',symbol:'TEST',status:'completed',capability:'observe',result:diagnosticResult}],callsToday:[]}}));
   await page.locator('#refresh').click();
   await page.getByText(/검증 미통과 초안 · 운영 알림: 발송 완료/).waitFor();
   await page.getByText(/운영 채널로 보낸 미검증 원문 ·/).click();
   assert.match(await page.locator('article').innerText(),/검증되지 않은 AI 원문 <script>bad<\/script>/);
   assert.equal(await page.locator('article script').count(),0);
   assert.equal(await page.getByText(/실제 발송 원문 ·/).count(),0);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1));
   assert.deepEqual(errors,[]);
   await page.screenshot({path:'/tmp/orbit-ai-control-'+width+'.png',fullPage:true});
   await page.close();
  }
  console.log('AI control browser: desktop/mobile, limit wait/removal, exact receipt, rejected claims, expired checks and escaping passed');
 } finally {if(browser) await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
