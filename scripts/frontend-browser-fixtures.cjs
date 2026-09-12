// Synthetic fixtures only. The test server never opens .env or local stores.
const stamp = "2026-09-12T00:00:00Z";
const items = Array.from({ length: 48 }, (_, index) => ({
  symbol: "TEST" + String(index + 1).padStart(2, "0"), name: "Synthetic Instrument " + (index + 1),
  market: "US", currency: "USD", currentPrice: 100 + index, quantity: 2,
  marketValue: 200 + index * 2, source: "holding", quoteStatus: "mock", updatedAt: stamp,
  isMock: true, dataQuality: "mock", apiSource: "MOCK synthetic fixture"
}));
const jobs = Array.from({ length: 55 }, (_, index) => ({
  id: "job-" + String(index + 1).padStart(3, "0"), jobId: "job-" + String(index + 1).padStart(3, "0"),
  title: "Synthetic alert " + (index + 1), symbol: items[index % items.length].symbol,
  messageType: "investmentInsight", status: index % 3 ? "done" : "failed",
  priorityQueueEligible: index === 0,
  createdAt: new Date(Date.parse(stamp) - index * 60000).toISOString(),
  textPreview: "Synthetic verified evidence", deliveryReasons: ["Synthetic reason"], isMock: true,
  investmentSummary: index % 2 ? {headline: "Synthetic alert " + (index + 1), reason: "검증용 자료: 수요 전망이 바뀌어 매출 가정을 다시 확인합니다."} : undefined,
  accountId: "fixture-a", readAt: "", dataQuality: "actual"
}));
const snapshot = {
  generatedAt: stamp, headline: "Synthetic frontend fixture", regime: "fixture", summary: [], checklist: [],
  accountId: "fixture-a", readModel: { ready: true, status: "ready" },
  toss: { mode: "live", configured: true, accountId: "fixture-a", account: {}, positions: items, watchlist: [] },
  portfolio: { total: 10000, invested: 8000, cash: 2000, concentration: 5, markets: [], sectors: [] },
  tossDecision: { headline: "Fixture", urgentCount: 0, holdingCount: 48, watchCount: 0, items: [], rules: [] }
};

function reading(kind = "opinion") {
  return {version: "investment-reading-v1", kind, status: kind === "opinion" ? "보유 유지" : "투자 의견 미확정",
    headline: kind === "opinion" ? "검증용 자료: 수요 변화의 지속 여부를 확인합니다." : "검증용 자료: 다음 실적을 확인하기 전에는 투자 의견이 없습니다.",
    meaning: kind === "opinion" ? "주문 증가가 다음 분기 매출에 반영되는지 확인할 이유가 있습니다." : "",
    meaningEmpty: "투자 의견을 확정할 자료가 부족합니다.",
    changes: ["검증용 변화: 신규 주문이 늘었습니다."], changeEmpty: "비교 기록 없음",
    reasons: ["매출과 수요 변화의 연결을 확인했습니다."], reasonLabel: "검토한 설명",
    counters: ["검증용 반대 근거: 주문 취소가 늘었습니다."], gaps: [],
    limits: ["검증용 경고: 다음 실적은 아직 발표되지 않았습니다."], nextChecks: ["다음 실적의 수요 변화"],
    facts: [{label: "계좌 내 비중", value: 31.75, unit: "%", asOf: stamp, source: "MOCK"},
      {label: "보유 수익률", value: -0.43884, unit: "%", asOf: stamp, source: "MOCK"}],
    sourceAt: stamp, opinionAt: stamp,
    explanations: [{id: "fixture-model", title: "수요가 매출에 반영되는가", claim: "주문 증가와 다음 분기 매출을 함께 확인합니다.",
      selected: false, qualification: "shadow", reason: "", conditions: ["주문 취소 확대"]}]
  };
}

function payload(url, options = {}) {
  const pathname = url.pathname;
  if (pathname === "/api/flow-lens") return snapshot;
  if (pathname === "/api/settings") return { settings: { watchlistSymbols: "TEST01,TEST02", appTheme: options.theme || "light" }, configured: {}, locked: true,
    shareAccess: { role: "viewer", writable: false, capabilities: ["read"] } };
  if (pathname === "/api/accounts" || pathname === "/api/service-accounts") return {
    accounts: [{ id: "fixture-a", label: "Synthetic Account A", enabled: true, watchlistSymbols: ["TEST01", "TEST02"] },
      { id: "fixture-b", label: "Synthetic Account B", enabled: true, watchlistSymbols: ["TEST03"] }]
  };
  if (pathname === "/api/market/instruments") return { items };
  if (pathname === "/api/market/evidence" || pathname === "/api/research-evidence") return { items: [], total: 0, summary: {} };
  if (pathname.startsWith("/api/portfolio/")) return { version: "console-read-model-v1", summary: { total: 10000, cash: 2000 }, positions: items,
    ledgerEntries: [], activityEpisodes: [], candidates: [], policyBreaches: [], risk: {}, proposal: {} };
  if (pathname === "/api/dashboard/summary") return { summary: {}, tasks: [], blockers: [], calendar: [] };
  if (pathname === "/api/operations/health") return { version: "console-read-model-v1", summary: {}, components: [] };
  if (pathname === "/api/notification-jobs") {
    const query = url.searchParams.get("query") || "";
    const selected = jobs.filter(job => (!query || job.title.includes(query)) && (!url.searchParams.get("status") || job.status === url.searchParams.get("status")));
    const offset = Number(url.searchParams.get("cursor") || url.searchParams.get("offset") || 0);
    const limit = Number(url.searchParams.get("limit") || 20);
    return { jobs: selected.slice(offset, offset + limit), total: selected.length, offset, limit, cursor: url.searchParams.get("cursor") || "",
      nextCursor: offset + limit < selected.length ? String(offset + limit) : "", summary: {}, inboxSummary: { total: selected.length } };
  }
  if (/^\/api\/notification-jobs\/[^/]+$/.test(pathname)) return { job: jobs.find(job => job.id === pathname.split("/").at(-1)) || jobs[0] };
  if (pathname.endsWith("/ai-review")) return { aiExecution: { executionSpans: { completionPolicy: "wait-until-complete", queueWaitMs: 1200, modelAttempts: [{ capacityWaitMs: 50 }] } } };
  if (pathname.endsWith("/timeline")) return {
    symbol: pathname.split("/")[3], query: { range: url.searchParams.get("range"), interval: "1d", accountId: url.searchParams.get("accountId") },
    series: { pointCount: 41, latestAt: stamp, candles: Array.from({ length: 41 }, (_, index) => ({
      time: new Date(Date.parse(stamp) - (40 - index) * 86400000).toISOString(), open: 100 + index, high: 103 + index, low: 98 + index, close: 101 + index, volume: 200 + index
    })) }, events: [], summary: { candleCount: 41, eventCount: 0 },
    sources: [{ dataset: "MOCK synthetic candles", store: "in-memory fixture", providers: ["MOCK"], count: 41 }]
  };
  if (pathname === "/api/symbol-universe") {
    const query = url.searchParams.get("query") || "";
    const selected = items.filter(item => !query || item.symbol.includes(query));
    const offset = Number(url.searchParams.get("offset") || 0); const limit = Number(url.searchParams.get("limit") || 8);
    return { items: selected.slice(offset, offset + limit), offset, limit, resultTotal: selected.length, hasMore: offset + limit < selected.length, summary: { total: selected.length, markets: [], sources: [] } };
  }
  if (pathname === "/api/symbol-universe/refresh") return { status: "idle", running: false, history: [] };
  if (pathname === "/api/decisions") return { version: "investment-case-v1", items: items.slice(0, 5).map((item, i) => ({
    caseId: i ? "fixture-case-" + i : "fixture-case", accountId: "fixture-a", symbol: item.symbol,
    name: i ? "검증용 기업 " + (i + 1) : "검증용 반도체 기업", updatedAt: stamp, lastVerifiedAt: new Date().toISOString(),
    headline: "검증용 자료: 수요 변화의 지속 여부를 확인하고 있습니다.", readinessState: "warning", readinessLabel: "일부 자료 확인 필요",
    attention: {state: "review", userReviewable: true}, decision: {action: i === 4 ? "NO_ACTION" : "HOLD", dataState: "partial"},
    explanation: {constraints: [{summary: "실적 자료 확인 필요"}]},
    reading: reading(i === 4 ? "awaiting" : "opinion")
  })), summary: {}, operatorView: { stages: [], issues: [] } };
  if (pathname.startsWith("/api/decisions/")) return {
    caseId: pathname.split("/")[3], episodeId: "fixture-resolved", resolvedFromLegacyKey: pathname.endsWith("fixture-legacy"),
    symbol: "TEST01", name: "MOCK Synthetic case", accountId: "fixture-a", decision: { action: "HOLD" },
    headline: "검증용 자료: 수요 변화의 지속 여부를 확인합니다.", updatedAt: stamp,
    reading: reading(),
    freshness: {decisionAsOf: stamp, sourceAsOf: stamp},
    explanation: {primaryCause: {summary: "매출과 수요 변화의 연결을 확인했습니다."},
      constraints: [{summary: "검증용 경고: 다음 실적은 아직 발표되지 않았습니다."}], changeConditions: ["다음 실적의 수요 변화"]},
    decisionReview: {state: "data-gap", previousSummary: "검증용 가설: 수요 증가가 다음 분기 매출에 반영되는지 확인합니다.",
      capturedAt: stamp, packetId: "synthetic-review", interpretation: "자료 부족은 가설 실패가 아니며, 관측 수익률은 실제 매매 수익을 뜻하지 않습니다.",
      verifiedChanges: [{label: "20일 평균 가격 회복", status: "satisfied"}], nextChecks: ["다음 분기 매출 발표"],
      outcomes: [{state: "data-gap", explanation: "비교 지수 자료가 부족해 성공·실패 판정을 보류했습니다.",
        horizonMinutes: 1440, observedAt: stamp, priceChangeFromDecisionPct: 1.2, benchmarkReturnPct: null}]},
    availableViews: ["summary", "current", "evidence", "reasoning", "history"],
    currentState: { items: [] }, stages: [], summary: {},
    evidence: { supportCount: 30, resolvedCount: 30, records: Array.from({length: 30}, (_,i)=>({
      id: "fixture-evidence-"+i, title: "MOCK evidence "+(i+1), summary: "Synthetic source record for scroll testing.",
      source: "MOCK fixture", sourceAsOf: stamp, role: "support", resolutionState: "resolved"
    })) }
  };
  if (/^\/api\/investment-cases\/[^/]+\/history$/.test(pathname)) return { items: Array.from({length: 40}, (_,i)=>({
    action: "HOLD", decidedAt: new Date(Date.parse(stamp)-i*3600000).toISOString(),
    summary: "MOCK history record "+(i+1), change: { evidenceChanged: true }
  })) };
  if (pathname.includes("investment-calendar")) return { events: [{eventId: "fixture-calendar-1", title: "검증용 반도체 기업 실적 발표",
    startsAt: new Date(Date.now() + 86400000).toISOString(), eventType: "earnings", status: "tentative", symbols: ["TEST01"],
    importance: 80, description: "실적과 수요 전망을 확인하는 검증용 일정입니다.", source: "MOCK fixture", updatedAt: stamp,
    reminderOffsetsMinutes: []}], candidates: [], summary: {total: 1, upcoming: 1} };
  if (pathname === "/api/investment-brain/hypothesis-development") return {
    count: 3, summary: {statuses: {"needs-revision": 1, "needs-data": 1, "shadow-observing": 1}}, cases: [
      {caseId: "fixture-development", symbol: "TEST01", title: "검증용 수요 가설", claim: "수요와 매출 관계를 검증하는 테스트 자료입니다.",
        status: "needs-revision", updatedAt: stamp, retry: {state: "development-required", attemptCount: 2, lastAttemptAt: stamp,
          blockers: [{kind: "unsupported-capability", requirement: "분기 수요를 검증할 모델 계약 등록이 필요합니다."},
            {kind: "unverified-observation", requirement: "현재 자료는 조회 전이며 결측 여부는 확인되지 않았습니다."}],
          compilationContext: {modelAssessmentContext: {snapshots: [{asOf: stamp, assessments: [{status: "not-supported",
            ruleLabel: "위험 이벤트 이후 가격 방어", failedConditionIds: ["fixture-condition"], unknownConditionIds: []}]}]}}}},
      {caseId: "fixture-observation", symbol: "TEST02", title: "검증용 관측 대기", claim: "미래 관측을 기다리는 테스트 자료입니다.",
        status: "needs-data", updatedAt: "2026-09-11T23:59:00Z", retry: {state: "waiting-observation", attemptCount: 1,
          nextCheckAt: "2026-09-12T03:00:00Z", blockers: [{kind: "observation-window", requirement: "제안 후 관측 기간을 기다립니다."}]}},
      {caseId: "fixture-evolution", symbol: "TEST01", title: "검증용 독립 실험", status: "shadow-observing", updatedAt: stamp,
        evolution: {state: "shadow-observing", reason: "independent-outcomes-required",
          plan: {createdAt: stamp, fingerprint: "0123456789abcdef".repeat(4), baseline: {deploymentId: "baseline-fixture", comparisonRuleId: "graph.fixture.recovery.v1", comparisonHorizonMinutes: 60},
            policy: {mode: "automatic", version: "fixture-policy", minimumIndependentPairs: 20, minimumDistinctDays: 5, maximumShadowDays: 30}},
          deployment: {deploymentId: "candidate-fixture"}, assessment: {independentPairCount: 0, distinctDayCount: 0, excludedCount: 2}}}
    ], events: []
  };
  return { items: [], summary: {}, status: "ready", jobs: [], terms: [], rules: [], templates: [], schedules: [] };
}
module.exports = { payload, snapshot, items, jobs };
