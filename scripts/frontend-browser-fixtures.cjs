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
  createdAt: new Date(Date.parse(stamp) - index * 60000).toISOString(),
  textPreview: "Synthetic verified evidence", deliveryReasons: ["Synthetic reason"], isMock: true,
  accountId: "fixture-a", readAt: "", dataQuality: "actual"
}));
const snapshot = {
  generatedAt: stamp, headline: "Synthetic frontend fixture", regime: "fixture", summary: [], checklist: [],
  accountId: "fixture-a", readModel: { ready: true, status: "ready" },
  toss: { mode: "live", configured: true, accountId: "fixture-a", account: {}, positions: items, watchlist: [] },
  portfolio: { total: 10000, invested: 8000, cash: 2000, concentration: 5, markets: [], sectors: [] },
  tossDecision: { headline: "Fixture", urgentCount: 0, holdingCount: 48, watchCount: 0, items: [], rules: [] }
};

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
  if (pathname === "/api/decisions") return { version: "investment-case-v1", items: [], summary: {}, operatorView: { stages: [], issues: [] } };
  if (pathname.startsWith("/api/decisions/")) return {
    caseId: pathname.split("/")[3], episodeId: "fixture-resolved", resolvedFromLegacyKey: pathname.endsWith("fixture-legacy"),
    symbol: "TEST01", name: "MOCK Synthetic case", accountId: "fixture-a", decision: { action: "HOLD" },
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
  if (pathname.includes("investment-calendar")) return { events: [], candidates: [], summary: {} };
  if (pathname === "/api/investment-brain/hypothesis-development") return {
    count: 2, summary: {statuses: {"needs-revision": 1, "needs-data": 1}}, cases: [
      {caseId: "fixture-development", symbol: "TEST01", title: "검증용 수요 가설", claim: "수요와 매출 관계를 검증하는 테스트 자료입니다.",
        status: "needs-revision", updatedAt: stamp, retry: {state: "development-required", attemptCount: 2, lastAttemptAt: stamp,
          blockers: [{kind: "unsupported-capability", requirement: "분기 수요를 검증할 모델 계약 등록이 필요합니다."}]}},
      {caseId: "fixture-observation", symbol: "TEST02", title: "검증용 관측 대기", claim: "미래 관측을 기다리는 테스트 자료입니다.",
        status: "needs-data", updatedAt: "2026-09-11T23:59:00Z", retry: {state: "waiting-observation", attemptCount: 1,
          nextCheckAt: "2026-09-12T03:00:00Z", blockers: [{kind: "observation-window", requirement: "제안 후 관측 기간을 기다립니다."}]}}
    ], events: []
  };
  return { items: [], summary: {}, status: "ready", jobs: [], terms: [], rules: [], templates: [], schedules: [] };
}
module.exports = { payload, snapshot, items, jobs };
