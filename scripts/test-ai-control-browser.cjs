const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { once } = require('node:events');
const { frontendDependency } = require('./frontend-toolchain.cjs');
const root = path.resolve(__dirname, '../public');
const brain = {goals:['미해결 질문을 근거로 재검토합니다.'],cases:[
 {caseId:'question',accountId:'owner',symbol:'TEST',status:'review-needed',question:'오래된 원래 질문을 다시 확인합니다.',researchAttempts:1,
  origin:{capturedAt:'2026-01-01T00:00:00Z',hypothesis:'처음의 설명 <script>bad</script>',evidence:[{id:'old-fact',value:10}]},
  history:[{status:'review-needed',reason:'조사는 끝났지만 답은 아직 검토 중입니다.',details:{executionInputId:'captured-review',evidenceIds:['new-fact']}}]},
 ...['data','experience'].map((category,index)=>({caseId:'feedback-'+index,accountId:'owner',symbol:'TEST',revision:2,kind:'service-feedback',status:'proposed',category,
  problem:'근거가 부족한 항목을 구분할 수 없습니다.',proposal:'자료의 조회 상태를 함께 표시합니다.',verification:'없음과 실패를 구분해 확인합니다.'}))]};
const result = {summary:'현재 화면의 새 분석',hypothesis:'중기 약세 안의 단기 회복일 수 있습니다.',portfolioImpact:'보유 손실의 회복 여부를 구분해 봅니다.',
  development:{requestId:'development-fixture',status:'pending'},developmentQuestions:['가설 개선 질문 <script>bad</script>'],
  quality:{status:'rejected',errors:['가격과 매입가 항목이 다릅니다.'],review:{reason:'<script>bad</script>'}},
  claimEvidence:{summary:[{factId:'fact',field:'currentPrice',period:'current'}]},
  followUpEvaluations:[{description:'평균 가격 회복을 확인합니다.',status:'expired',reason:'기간 내 자료 없음'}],
  publication:{status:'queued',deliveryStatus:'done',reason:'전송 성공',receipt:{body:'실제 전송된 과거 원문 <b>증거</b>',deliveredAt:'2026-10-01T03:00:00Z'}},
  input:{name:'테스트 종목',facts:[{currentPrice:100,currency:'KRW',sourceAsOf:'2026-10-01T03:00:00Z'}],
    retrieval:{status:'ready',corrections:1,steps:[{status:'invalid-request',reason:'조회 요청 규격 오류',errors:[{field:'requests[0].cursor',expected:'서버가 발급한 값 <script>bad</script>'}],reads:[]},{correction:true,reason:'반대 근거 확인 <script>bad</script>',reads:[{request:{tool:'query_facts',category:'company'},factIds:['f1'],status:'ok',nextCursor:'page:fixture',omitted:[]}]}]}}};
const server = http.createServer((req,res) => {
  if (req.url === '/api/ai-control/status') {res.setHeader('content-type','application/json');res.end(JSON.stringify({enabled:true,configuredEnabled:true,tasksStartedToday:2,dailyTaskBudget:48,activeTaskCount:1,dailyCallBudget:24,
    modelCallsUsedToday:24,observationScheduling:{status:'budget-wait',reason:'ai-call-budget-exhausted',nextCheckAt:'2026-10-02T00:00:00Z'},
    brain,qualitySummary:{accepted:0,rejected:1},tasks:[{taskId:'fixture',symbol:'TEST',status:'completed',capability:'observe',result}],callsToday:[]}));return;}
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
   await page.getByText('AI가 조회한 과정 · 조회 완료',{exact:true}).click();
   assert.match(await page.locator('.retrieval').innerText(),/기업·재무 · 사실 1개/);
   assert.match(await page.locator('.retrieval').innerText(),/다음 페이지 있음/);
   assert.match(await page.locator('.retrieval').innerText(),/요청 보정/);
   assert.match(await page.locator('.retrieval').innerText(),/서버가 발급한 값 <script>bad<\/script>/);
   assert.match(await page.locator('.retrieval').innerText(),/반대 근거 확인 <script>bad<\/script>/);
   assert.equal(await page.locator('.retrieval script').count(),0);
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
    brain,observationScheduling:{status:'ready'},qualitySummary:{accepted:0,rejected:1},
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
   assert.match(await page.locator('#brain').innerText(),/오래된 원래 질문/);
   await page.getByText(/처음 생긴 이유와 근거 ·/).first().click();
   assert.match(await page.locator('#brain').innerText(),/처음의 설명 <script>bad<\/script>/);
   await page.getByText('진행과 평가 이력',{exact:true}).first().click();
   assert.match(await page.locator('#brain').innerText(),/captured-review/);
   assert.equal(await page.locator('#brain script').count(),0);
   const forms=page.locator('.feedback-review');
   await forms.nth(0).locator('textarea').fill('자료 상태를 화면에 표시하도록 개선할 계획입니다.');
   await forms.nth(1).locator('textarea').fill('다른 개선 제안에 작성 중인 내용을 보존합니다.');
   await page.locator('#refresh').click();
   assert.equal(await forms.nth(0).locator('textarea').inputValue(),'자료 상태를 화면에 표시하도록 개선할 계획입니다.');
   let reviewed=false;
   await page.route('**/api/ai-control/feedback', async route=>{
    assert.equal(route.request().method(),'PUT');
    assert.deepEqual(route.request().postDataJSON(),{caseId:'feedback-0',accountId:'owner',symbol:'TEST',revision:2,status:'planned',note:'자료 상태를 화면에 표시하도록 개선할 계획입니다.'});
    reviewed=true;await route.fulfill({json:{saved:true,caseId:'feedback-0',revision:3}});
   });
   await forms.nth(0).locator('[type="submit"]').click();
   await page.getByText('검토 내용을 기록했습니다.',{exact:true}).waitFor();
   assert(reviewed);
   assert.equal(await forms.nth(1).locator('textarea').inputValue(),'다른 개선 제안에 작성 중인 내용을 보존합니다.');
   await forms.nth(1).locator('[type="reset"]').click();
   await forms.nth(1).locator('textarea').filter({visible:true}).waitFor();
   assert.equal(await forms.nth(1).locator('textarea').inputValue(),'');
   const diagnosticResult = {...result, publication:{status:'recorded',reason:'검증 보류',diagnostic:{status:'queued',deliveryStatus:'done',reason:'운영 채널 발송 완료',
    receipt:{body:'검증되지 않은 AI 원문 <script>bad</script>',deliveredAt:'2026-10-01T04:00:00Z'}}}};
   await page.route('**/api/ai-control/status', route => route.fulfill({json:{enabled:true,budgetEnabled:false,brain,
    observationScheduling:{status:'ready'},tasks:[{taskId:'diagnostic',symbol:'TEST',status:'completed',capability:'observe',result:diagnosticResult}],callsToday:[]}}));
   await page.locator('#refresh').click();
   await page.getByText(/검증 미통과 초안 · 운영 알림: 발송 완료/).waitFor();
   await page.getByText(/운영 채널로 보낸 미검증 원문 ·/).click();
   assert.match(await page.locator('article').innerText(),/검증되지 않은 AI 원문 <script>bad<\/script>/);
   assert.equal(await page.locator('article script').count(),0);
   assert.equal(await page.getByText(/실제 발송 원문 ·/).count(),0);
   const failed = {failure:{kind:'retrieval-contract',retrieval:{...result.input.retrieval,status:'invalid-request'}},rawResponse:'private raw response sentinel'};
   await page.route('**/api/ai-control/status', route => route.fulfill({json:{enabled:true,budgetEnabled:false,brain,
    observationScheduling:{status:'ready'},tasks:[{taskId:'failure',symbol:'TEST',status:'failed',capability:'observe',result:failed}],callsToday:[]}}));
   await page.locator('#refresh').click();
   await page.getByText('예약·조사·처리 기록 1개',{exact:true}).click();
   await page.getByText('AI가 조회한 과정 · 조회 요청 오류',{exact:true}).click();
   assert.match(await page.locator('article').innerText(),/판단 작성 전 중단/);
   assert.match(await page.locator('.retrieval').innerText(),/requests\[0\].cursor/);
   assert.doesNotMatch(await page.locator('article').innerText(),/자료 부족|private raw response sentinel/);
   assert.equal(await page.locator('article script').count(),0);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1));
   assert.deepEqual(errors,[]);
   await page.screenshot({path:'/tmp/orbit-ai-control-'+width+'.png',fullPage:true});
   await page.close();
  }
  console.log('AI control browser: desktop/mobile, agenda origins, proposal review, draft preservation, limits, receipts, rejections and escaping passed');
 } finally {if(browser) await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
