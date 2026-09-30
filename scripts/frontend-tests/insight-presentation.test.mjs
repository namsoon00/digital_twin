import test from "node:test";
import assert from "node:assert/strict";
import { investmentBrief, investmentReading, renderInvestmentBrief } from "../../public/modules/decisions/brief.mjs";
import { groupTodayTasks, todayTaskNeedsAction } from "../../public/modules/overview/task-groups.mjs";
import { calendarEventCanBeDeleted, mergeCalendarEvents } from "../../public/modules/calendar/presentation.mjs";
import { opinionRecency, decisionInView } from "../../public/modules/decisions/recency.mjs";
import { evidenceSummary, evidenceResolutionLabel } from "../../public/modules/decisions/evidence-summary.mjs";
import { notificationEventSummary } from "../../public/modules/notifications/summary.mjs";
import { renderSecondaryDisclosure } from "../../public/modules/shared/disclosure.mjs";
import { renderNotificationCustomerDocument } from "../../public/modules/notifications/customer-document.mjs";
import { renderCompanyChangeReport, renderInstrumentInvestmentAnalysis } from "../../public/modules/instruments/valuation-presentation.mjs";

test("company reports show captured financial periods and sources while reference prices stay collapsed", () => {
  const report = {
    contractVersion: "company-change-report-v2", reportKind: "expanded", headline: "기업 <테스트>",
    symbol: "000660", currency: "KRW", summary: "자료 보강", sourceCutoffDisplay: "2026-09-28 07:19 KST",
    sections: [
      {key: "annualFinancials", title: "연간 실적", financialReports: [{period: "2025-12-31", metrics: [
        {label: "매출액", value: 97146675000000, currency: "KRW", basisLabel: "2025-12-31 · 연간 · 연결",
          provider: "공공데이터포털", sourceLabel: "공식 API 집계", sourceReferences: [{datasetId: "public-data.financials", revisionId: "r1"}], sourceUrl: "javascript:alert(1)"}
      ]}]},
      {key: "documents", title: "회사 발표", documents: [{title: "발표 <script>", bodyVerified: true, excerpt: "<script>alert(1)</script>", url: "https://example.test/ir?a=1&b=2", publishedAt: "2026-08-14"}]},
      {key: "valuation", title: "가치평가", rows: ["모델 검토 대기"], models: [{label: "반도체 이익 방식", fairValue: 1793020, currency: "KRW", stateLabel: "참고 계산"}], calculation: {earningsScenario: {base: 100}, multipleBand: {base: 10}, fairValue: {base: 1793020}}}
    ]
  };
  const html = renderCompanyChangeReport(report);
  assert.match(html, /기업 &lt;테스트&gt;/);
  assert.match(html, /97\.15 조원/);
  assert.match(html, /2025-12-31 · 연간 · 연결/);
  assert.match(html, /공식 API 집계/);
  assert.match(html, /public-data.financials · r1/);
  assert.match(html, /2026-09-28 07:19 KST/);
  assert.match(html, /https:\/\/example.test\/ir\?a=1&amp;b=2/);
  assert.doesNotMatch(html, /<script>|href="javascript:/);
  assert.doesNotMatch(html.split('<details class="company-report-calculation">')[0], /1,793,020/);
  assert.match(html, /<details class="company-report-calculation"><summary>참고 계산과 가정 펼치기/);
  assert.match(html, /1,793,020 KRW/);
  assert.doesNotMatch(html, /이번에 달라진 점/);
});

test("both notification lanes show financial dates, comparisons and sources in compact web details", () => {
  for (const role of ["typedb-observation", "ai-research-insight"]) {
    const html = renderNotificationCustomerDocument({customerInvestmentDocument: {role, sections: [
      {key: "financial-evidence", title: "재무 비교", rows: ["2026-06-30 · 영업이익 100 → 130 · yfinance"]}
    ], links: [{label: "공식 공시", url: "https://dart.fss.or.kr/test"}]}}, true);
    assert.match(html, /2026-06-30/);
    assert.match(html, /100 → 130/);
    assert.match(html, /yfinance/);
    assert.match(html, /https:\/\/dart.fss.or.kr\/test/);
  }
});

test("company report v3 explains recent facts and conditional value with traceable escaped evidence", () => {
  const report = {contractVersion: "company-change-report-v3", reportKind: "change", currency: "KRW", symbol: "TEST",
    headline: "기업 보고서", summary: "실적 해석", sections: [
      {key: "businessMeaning", title: "실적이 의미하는 것", readingCards: [{
        kind: "accounting-observation", title: "투자 후 현금", fact: "2026-06-30 · 누적 · -20 KRW",
        meaning: "투자 후 현금이 음수입니다. <script>bad</script>", limitations: ["차입·배당은 계산에서 제외"],
        evidence: [{key: "cash", label: "영업현금흐름", value: 30, currency: "KRW", basisLabel: "2026-06-30 · 누적",
          provider: "OpenDART", sourceUrl: "javascript:alert(1)", sourceReferences: [{datasetId: "filing", revisionId: "r1"}]}]
      }]},
      {key: "valuationMeaning", title: "가격의 조건", readingCards: [{kind: "conditional-model", title: "가격을 설명하는 사업 조건",
        fact: "조건부 영업이익률 17.50%", meaning: "관측된 시장 기대가 아닙니다.", asOf: "2026-09-29",
        fixedAssumptions: {waccPct: 12, terminalGrowthPct: 2.5}}]},
      {key: "judgmentConditions", title: "해석을 다시 확인할 조건", rows: ["다음 분기 원문 확인"]},
      {key: "annualFinancials", title: "연간 실적", financialReports: []}
    ]};
  const html = renderCompanyChangeReport(report);
  assert.ok(html.indexOf("실적이 의미하는 것") < html.indexOf("연간 실적"));
  assert.match(html, /수치로 확인한 의미/);
  assert.match(html, /이 설명의 근거와 가정/);
  assert.match(html, /filing · r1/);
  assert.match(html, /2026-06-30 · 누적/);
  assert.match(html, /차입·배당/);
  assert.match(html, /조건부 계산/);
  assert.match(html, /할인율 \(%\): 12/);
  assert.match(html, /관측된 시장 기대가 아닙니다/);
  assert.match(html, /다음 분기 원문 확인/);
  assert.doesNotMatch(html, /<script>|href="javascript:/);
});

test("instrument valuation separates verified changes, price-cause limits and reference models", () => {
  const html = renderInstrumentInvestmentAnalysis({
      currentVerifiedFacts: [{label: "영업이익률", value: 24, unit: "percent", period: "2025-12-31"}],
      newlyConfirmedFacts: [],
      changeFromPrevious: {state: "unchanged", materialChange: false},
      priceExplanation: {claimStrength: "supported-mechanism", blockingReasons: ["precise-event-clock-missing"]},
      valuationModels: [{modelId: "growth-quality-earnings", fairValue: 40, currency: "USD", decisionEligible: false}],
      modelAgreement: {status: "conflict", spreadPct: 47.1, blockingReasons: ["valuation-models-materially-disagree"]},
      dataReadiness: {
        status: "limited", quote: {status: "available", asOf: "2026-09-27"},
        officialFinancials: {officialMetricCount: 9, requiredMetricCount: 12, period: "2025-12-31"},
        consensus: {status: "blocked", currency: "USD"}
      },
      nextChecks: ["company-currency-exposure-missing"]
  }, "USD");
  assert.match(html, /회사 상태와 이전 판단/);
  assert.match(html, /현재 검증 계약과 source revision/);
  assert.match(html, /영업이익률/);
  assert.match(html, /기업가치 영향 경로만 확인/);
  assert.match(html, /사건이 공개된 정확한 시각/);
  assert.match(html, /참고용/);
  assert.match(html, /데이터 준비도/);
  assert.match(html, /9\/12개/);
  assert.match(html, /적정가 모델 충돌/);
  assert.match(html, /평균내지 않고/);
  assert.match(html, /기업의 매출·비용 통화 노출/);
  assert.doesNotMatch(html, /causal-hypothesis|company-currency-exposure-missing/);

  const pendingReview = renderInstrumentInvestmentAnalysis({
    priceExplanation: {claimStrength: "unresolved"},
    valuationModels: [{
      modelId: "growth-quality-earnings", fairValue: 40, currency: "USD",
      decisionEligible: false, evidenceBacked: true, inputState: "sufficient",
      reliabilityState: "sufficient", reviewStatus: "ai_applied_pending_review"
    }]
  }, "USD");
  assert.match(pendingReview, /모델 검토 대기/);
  assert.doesNotMatch(pendingReview, /<em>참고용<\/em>/);

  const shadowDcf = renderInstrumentInvestmentAnalysis({
    priceExplanation: {claimStrength: "unresolved"},
    valuationModels: [{
      modelId: "driver-fcff-dcf", fairValue: 125.78, currency: "USD",
      decisionEligible: false, sourceBacked: true, assumptionReviewState: "required",
      financialEvidence: {sourceClass: "secondary-aggregator", officialDecisionReady: false, officialMetricCount: 0, requiredMetricCount: 12},
      exposureReadiness: {currency: {status: "unresolved"}, debtRate: {status: "unresolved"}},
      assumptionReview: {state: "required", pendingCount: 2},
      modelRelease: {status: "released", releaseMode: "reference", releaseId: "driver-dcf-reference-r1"},
      releaseAudit: {status: "passed-with-limitations", limitations: ["forecast-growth-exceeds-100pct", "unapproved-assumptions-present"]},
      assumptions: [
        {id: "fy1-revenue-consensus", value: 100, unit: "USD", status: "observed"},
        {id: "wacc", value: 16.2, unit: "percent", status: "candidate"},
        {id: "terminal-growth", value: 2.5, unit: "percent", status: "candidate"}
      ],
      sensitivity: {
        status: "calculated", valueRange: {low: 108.2, high: 149.4, currency: "USD"},
        rows: [{isBase: true, terminalValueSharePct: 66.4}]
      }
    }],
    dcfReadiness: {
      status: "ready-for-shadow", assumptionReviewState: "required",
      financialEvidence: {sourceClass: "secondary-aggregator", officialDecisionReady: false, officialMetricCount: 0, requiredMetricCount: 12},
      exposureReadiness: {currency: {status: "unresolved"}, debtRate: {status: "unresolved"}},
      assumptionReview: {state: "required", pendingCount: 2}
      ,modelRelease: {status: "released", releaseMode: "reference", releaseId: "driver-dcf-reference-r1"}
      ,releaseAudit: {status: "passed-with-limitations", limitations: ["forecast-growth-exceeds-100pct", "unapproved-assumptions-present"]}
    },
    impliedExpectations: {
      status: "solved", impliedRevenueGrowthPct: 62.5, assumptionReviewState: "required",
      fixedAssumptions: {waccPct: 16.2, terminalGrowthPct: 2.5},
      interpretation: "고정 가정 아래의 조건부 역산값입니다."
    }
  }, "USD");
  assert.match(shadowDcf, /사업 변수 현금흐름 방식/);
  assert.match(shadowDcf, /DCF 신뢰도 점검/);
  assert.match(shadowDcf, /집계 재무/);
  assert.match(shadowDcf, /0\/12개 필수 항목 공식 확인/);
  assert.match(shadowDcf, /기업별 노출 자료 없음/);
  assert.match(shadowDcf, /현재 입력 bundle에만 유효/);
  assert.match(shadowDcf, /가정 검토 필요/);
  assert.match(shadowDcf, /참고용 릴리즈/);
  assert.match(shadowDcf, /driver-dcf-reference-r1/);
  assert.match(shadowDcf, /전망 매출 성장률이 100%를 초과함/);
  assert.match(shadowDcf, /매수·매도 판단과 자동 주문에는 사용하지 않습니다/);
  assert.match(shadowDcf, /5년 일정 매출 성장률/);
  assert.match(shadowDcf, /62.5%/);
  assert.match(shadowDcf, /시장 기대를 관측한 값이 아님/);
  assert.match(shadowDcf, /DCF 가정 민감도/);
  assert.match(shadowDcf, /\$108.2 ~ \$149.4/);
  assert.match(shadowDcf, /terminal 비중/);
  assert.match(shadowDcf, /주식 위험 프리미엄|가중평균자본비용/);
  assert.doesNotMatch(shadowDcf, /fy1-revenue-consensus/);

  const activeDiagnostic = renderInstrumentInvestmentAnalysis({
    priceExplanation: {claimStrength: "unresolved"},
    valuationModels: [{
      modelId: "driver-fcff-dcf", fairValue: null, currency: "USD",
      decisionEligible: false, sourceBacked: true, assumptionReviewState: "complete",
      modelRelease: {status: "active", releaseMode: "active", releaseId: "driver-dcf-active-cpng-r1"},
      releaseAudit: {diagnosticOnly: true, limitations: ["non-positive-equity-value-under-current-economics"]}
    }],
    dcfReadiness: {
      status: "active-diagnostic", assumptionReviewState: "complete",
      assumptionReview: {state: "complete", pendingCount: 0},
      modelRelease: {status: "active", releaseMode: "active", releaseId: "driver-dcf-active-cpng-r1"},
      releaseAudit: {diagnosticOnly: true, limitations: ["non-positive-equity-value-under-current-economics"]}
    }
  }, "USD");
  assert.match(activeDiagnostic, /운영 승격 · 진단용/);
  assert.match(activeDiagnostic, /현재 수익성으로 양\(\+\) 가치 미산출/);
  assert.match(activeDiagnostic, /운영 분석 승격/);
  assert.match(activeDiagnostic, /0개 대기/);
  assert.match(activeDiagnostic, /운영 분석에서 계속 추적합니다/);
});

test("compact financial reviews keep all source-labelled facts separate from the new market change", () => {
  const html = renderNotificationCustomerDocument({customerInvestmentDocument: {
    role: "ai-research-insight", sections: [
      {key: "financial-evidence", title: "기존 판단 근거 · 재무", rows: [
        "2026-06-30 보고 기간 · 직전 알림과 같은 자료",
        "영업이익 +30% · OpenDART 공시", "매출 +20% · OpenDART 공시", "잉여현금흐름 +10% · yfinance 집계"
      ]},
      {key: "change", title: "이번에 달라진 점", rows: ["매수·매도 대기 물량 차이가 줄었습니다."]},
      {key: "reasons", title: "판단 연결", rows: ["기존 실적과 이번 거래 흐름을 함께 비교했습니다."]}
    ]
  }}, true);
  assert.match(html, /잉여현금흐름 \+10% · yfinance 집계/);
  assert.ok(html.indexOf("기존 판단 근거") < html.indexOf("이번에 달라진 점"));
  assert.ok(html.indexOf("이번에 달라진 점") < html.indexOf("판단 연결"));
});

test("old opinions remain in history and recheck, never current tasks or recent changes forever", () => {
  const item = {updatedAt: "2026-08-18T10:00:00Z", changeState: "changed", userReviewable: true};
  const now = Date.parse("2026-09-12T00:00:00Z");
  const row = {...item, recency: opinionRecency(item, now)};
  assert.equal(row.recency.state, "review");
  assert.equal(decisionInView(row, "attention", now), false);
  assert.equal(decisionInView(row, "recent", now), false);
  assert.equal(decisionInView(row, "review", now), true);
  assert.equal(decisionInView(row, "all", now), true);
  assert.equal(item.updatedAt, "2026-08-18T10:00:00Z");
  assert.equal(opinionRecency({}, now).state, "unknown");
  assert.equal(opinionRecency({...item, lastVerifiedAt: "2026-09-12T00:00:00Z"}, now).state, "current");
});

test("brief preserves important limitations and does not create an investment action", () => {
  const detail = {headline: "자료 확인 중", decision: {state: "blocked"},
    explanation: {primaryCause: {summary: "자료 확인 중"}, constraints: [{summary: "매출 자료 부족"}]},
    statusDimensions: [{id: "data", state: "warning", reason: "매출 자료 부족"}]};
  const brief = investmentBrief(detail);
  assert.deepEqual(brief.limitations, ["매출 자료 부족"]);
  assert.deepEqual(brief.reasons, []);
  assert.deepEqual(brief.next, []);
  assert.equal(brief.limited, true);
  assert.equal(brief.action, undefined);
});

test("current review and recheck views are disjoint and blocked opinions are never actionable", () => {
  const current = {recency: {state: "current"}, userReviewable: true};
  assert.equal(decisionInView(current, "attention", Date.now()), true);
  assert.equal(decisionInView(current, "review", Date.now()), false);
  const blocked = {...current, blocked: true, userActionable: true};
  assert.equal(decisionInView(blocked, "attention", Date.now()), false);
  assert.equal(decisionInView(blocked, "action", Date.now()), false);
  assert.equal(decisionInView(blocked, "review", Date.now()), true);
});

test("unvalidated AI assessment is excluded from the brief", () => {
  const brief = investmentBrief({reasoningLineage: {ai: {status: "contract-failed", insightAssessment: {
    publishable: true, causalMechanism: "must not publish", invalidationCondition: "must not publish"
  }}}});
  assert.doesNotMatch(JSON.stringify(brief), /must not publish/);
  const old = investmentBrief({reasoningLineage: {ai: {status: "ai-authored", publicationContractPassed: true,
    aiAuthored: true, currentGeneration: false, insightAssessment: {
      publishable: true, causalMechanism: "must not publish", invalidationCondition: "must not publish"
    }}}});
  assert.doesNotMatch(JSON.stringify(old), /must not publish/);
});

test("brief translates internal storage terms without rewriting the stored reasoning", () => {
  const detail = {headline: "TypeDB 추론 완료", explanation: {dataGaps: [{detail: "ABox 스냅샷 확인 필요"}]}};
  const brief = investmentBrief(detail);
  assert.equal(brief.headline, "종목 관계 분석 완료");
  assert.deepEqual(brief.limitations, ["판단 당시 자료 확인 필요"]);
  assert.equal(detail.headline, "TypeDB 추론 완료");
});

test("brief escapes external text and preserves dates and source warnings", () => {
  const html = renderInvestmentBrief({headline: '<script>alert(1)</script>', decision: {action: "HOLD"}, updatedAt: "2026-09-12T00:00:00Z"}, 'x" onclick="bad', "의견 없음", value => value);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>| onclick="bad/);
  assert.match(html, /2026-09-12T00:00:00Z/);
  assert.match(html, /자료 기준.*확인 필요/);
  assert.match(html, /data-investment-case-tab="evidence"/);
});

test("unknown evidence counts are not zero and lineage is not original source resolution", () => {
  assert.deepEqual(evidenceSummary({}), {support: null, counter: null, missing: null, originals: 0, lineage: 0, records: 0});
  const summary = evidenceSummary({evidence: {resolvedCount: 99, records: [{resolutionState: "lineage-linked"}, {resolutionState: "resolved"}]}, explanation: {supportingCauses: [{summary: "basis"}], counterCauses: []}});
  assert.equal(summary.support, 1);
  assert.equal(summary.counter, 0);
  assert.equal(summary.originals, 1);
  assert.equal(summary.lineage, 1);
  assert.equal(evidenceResolutionLabel("lineage-linked"), "추론 계보 연결");
  assert.equal(evidenceSummary({evidence: {missingCount: null}}).missing, null);
  assert.equal(evidenceSummary({evidence: {missingCount: 0}}).missing, 0);
});

test("notification explanation never uses quiet hours or transport failure as investment reasoning", () => {
  const job = {suppressionSummary: "22:00 quiet hours", deliveryReasons: ["transport failed"],
    customerInvestmentDocument: {headline: "검증용 변화", sections: [
      {key: "change", rows: ["수요 전망 변경"]}, {key: "importance", rows: ["매출 가정 재검토"]}
    ]}};
  assert.deepEqual(notificationEventSummary(job), {title: "검증용 변화", change: "수요 전망 변경", reason: "매출 가정 재검토", reasonLabel: "이유"});
  delete job.customerInvestmentDocument;
  assert.doesNotMatch(notificationEventSummary(job).reason, /quiet hours|transport failed/);
});

test("lightweight alert summaries and legacy previews remain visible and distinct", () => {
  const projected = notificationEventSummary({investmentSummary: {headline: "수요 변경", reason: "매출 확인 필요"}});
  assert.equal(projected.reason, "매출 확인 필요");
  assert.equal(projected.reasonLabel, "이유");
  const legacy = notificationEventSummary({textPreview: "저장된 과거 알림", suppressionSummary: "quiet hours"});
  assert.equal(legacy.reason, "저장된 과거 알림");
  assert.equal(legacy.reasonLabel, "본문 미리보기");
});

test("secondary disclosure has a stable identity and defaults closed", () => {
  const html = renderSecondaryDisclosure("test-metrics", "현황", "<p>detail</p>");
  assert.match(html, /id="disclosure-test-metrics"/);
  assert.doesNotMatch(html, / open[ >]/);
  assert.match(html, /<summary>/);
});

test("operational failures never occupy the investor queue and input order is preserved", () => {
  const tasks = [{kind: "알림", key: "failure"}, {kind: "판단", key: "new"},
    {kind: "판단", key: "pending", reading: {kind: "awaiting"}}, {kind: "데이터", key: "connection"},
    {kind: "일정", key: "earnings"}, {kind: "판단", key: "older"}];
  const before = JSON.stringify(tasks);
  const groups = groupTodayTasks(tasks);
  assert.deepEqual(groups.investment.map(x => x.key), ["new", "older"]);
  assert.deepEqual(groups.operations.map(x => x.key), ["failure", "connection"]);
  assert.deepEqual(groups.pending.map(x => x.key), ["pending"]);
  assert.deepEqual(groups.calendar.map(x => x.key), ["earnings"]);
  assert.equal(JSON.stringify(tasks), before);
});

test("today separates actionable opinions from observation-only interpretations", () => {
  const buy = {kind: "판단", key: "buy", actionCode: "BUY", reading: {kind: "opinion"}};
  const hold = {kind: "판단", key: "hold", actionCode: "HOLD", reading: {kind: "opinion"}};
  const interpretation = {kind: "판단", key: "interpretation", actionCode: "NO_ACTION", reading: {kind: "interpretation"}};
  const groups = groupTodayTasks([interpretation, hold, buy]);
  assert.equal(todayTaskNeedsAction(buy), true);
  assert.equal(todayTaskNeedsAction(hold), false);
  assert.equal(todayTaskNeedsAction(interpretation), false);
  assert.deepEqual(groups.actionable.map(item => item.key), ["buy"]);
  assert.deepEqual(groups.observation.map(item => item.key), ["interpretation", "hold"]);
  assert.deepEqual(groups.investment.map(item => item.key), ["interpretation", "hold", "buy"]);
});

test("calendar combines registered and detected schedules without duplicate or delete ambiguity", () => {
  const registered = [{eventId: "gdp", title: "GDP", startsAt: "2026-09-30T12:30:00Z", status: "active", notes: "stored"}];
  const detected = [
    {eventId: "gdp", title: "GDP", startsAt: "2026-09-30T12:30:00Z", notes: "detected duplicate"},
    {eventId: "pce", title: "PCE", startsAt: "2026-09-30T12:30:00Z"}
  ];
  const merged = mergeCalendarEvents(registered, detected);
  assert.equal(merged.length, 2);
  assert.equal(merged[0].notes, "stored");
  assert.equal(merged[0].calendarOrigin, "registered");
  assert.equal(calendarEventCanBeDeleted(merged[0]), true);
  assert.equal(merged[1].calendarOrigin, "detected");
  assert.equal(calendarEventCanBeDeleted(merged[1]), false);
});

test("an unfinished current analysis belongs to preparation, not investment opinions", () => {
  const row = {recency: {state: "current"}, reading: {kind: "awaiting"}, userReviewable: true, userActionable: true};
  assert.equal(decisionInView(row, "attention", Date.now()), false);
  assert.equal(decisionInView(row, "action", Date.now()), false);
  assert.equal(decisionInView(row, "review", Date.now()), true);
  assert.equal(decisionInView({...row, reading: {kind: "interpretation"}}, "attention", Date.now()), true);
});

test("legacy NO_ACTION and blocked HOLD never become a holding recommendation", () => {
  for (const decision of [{action: "NO_ACTION"}, {action: "HOLD", state: "blocked"}]) {
    const reading = investmentReading({decision, headline: "TypeDB 추론 완료"}, "관찰");
    assert.equal(reading.kind, "awaiting");
    assert.equal(reading.status, "투자 의견 미확정");
    assert.doesNotMatch(reading.headline, /TypeDB/);
  }
});

test("reading links each question to evidence and separates missing data from counterarguments", () => {
  const reading = {version: "investment-reading-v1", kind: "interpretation", status: "참고 해석 · 매매 의견 없음",
    headline: "검증용 해석", meaning: "검증용 영향", changes: ["검증용 변화"], reasons: ["주문 증가"],
    reasonLabel: "검토한 설명", counters: ["주문 취소 증가"], gaps: ["분기 실적 미발표"], limits: [],
    nextChecks: ["다음 실적"], facts: [{label: "계좌 내 비중", value: 31.75222, unit: "%", asOf: "2026-09-13T00:00:00Z"}]};
  const html = renderInvestmentBrief({reading}, "case-test", "NO_ACTION", value => value);
  assert.match(html, /무엇을 확인했나.*내 투자에 어떤 의미인가.*왜 그렇게 보나.*무엇을 더 확인해야 하나/s);
  assert.match(html, /다르게 볼 근거.*주문 취소 증가/s);
  assert.match(html, /부족한 자료 1건.*분기 실적 미발표/s);
  assert.match(html, /31.75%/);
  assert.doesNotMatch(html, /자동.*추적|자료 상태 확인 필요|31.75222/);
  for (const tab of ["current", "reasoning", "evidence"]) assert.match(html, new RegExp('data-investment-case-tab="' + tab + '"'));
});
