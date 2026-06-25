(function () {
  var app = document.getElementById("app");
  var defaultSettings = {
    watchlistSymbols: "NVDA,TSLA,000660",
    tossApiBaseUrl: "https://openapi.tossinvest.com",
    tossClientId: "",
    tossClientSecret: "",
    tossAccountSeq: "",
    xBearerToken: "",
    xSearchQuery: "(market OR stocks OR semiconductor OR Fed OR KOSPI OR dollar OR AI) -is:retweet lang:en"
  };
  var tabs = [
    { id: "decision", label: "판단" },
    { id: "flow", label: "심리·흐름" },
    { id: "feed", label: "피드" },
    { id: "settings", label: "설정" }
  ];
  var settingsMemoryStore = "";
  var state = {
    loading: true,
    refreshing: false,
    error: "",
    snapshot: null,
    selectedTheme: "all",
    dataMode: initialDataMode(),
    activeTab: initialTab(),
    settings: loadSettings(),
    showSecrets: false,
    settingsSaved: false
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function requestJson(path) {
    return fetch(path, {
      headers: { "Accept": "application/json" },
      cache: "no-store"
    }).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok) throw new Error(payload.error || "요청 실패");
        return payload;
      });
    });
  }

  function isStaticPreviewHost() {
    return window.location.protocol === "file:" || /\.github\.io$/i.test(window.location.hostname);
  }

  function initialTab() {
    var params = new URLSearchParams(window.location.search);
    var requested = String(params.get("tab") || "").toLowerCase();
    return tabs.some(function (tab) { return tab.id === requested; }) ? requested : "decision";
  }

  function initialDataMode() {
    var params = new URLSearchParams(window.location.search);
    var queryMode = String(params.get("mock") || params.get("mode") || "").toLowerCase();
    if (queryMode === "1" || queryMode === "true" || queryMode === "mock") return "mock";
    if (isStaticPreviewHost()) return "mock";
    try {
      return window.localStorage.getItem("exitLensDataMode") === "mock" ? "mock" : "live";
    } catch (error) {
      return "live";
    }
  }

  function persistDataMode(value) {
    try {
      window.localStorage.setItem("exitLensDataMode", value);
    } catch (error) {
      // Storage can be unavailable in private contexts; the in-memory state is enough.
    }
  }

  function loadSettings() {
    try {
      var raw = readStoredSettings();
      return Object.assign({}, defaultSettings, raw ? JSON.parse(raw) : {});
    } catch (error) {
      return Object.assign({}, defaultSettings);
    }
  }

  function readStoredSettings() {
    try {
      var storage = window.localStorage;
      return storage ? storage.getItem("exitLensSettings") : settingsMemoryStore;
    } catch (error) {
      return settingsMemoryStore;
    }
  }

  function writeStoredSettings(payload) {
    settingsMemoryStore = payload;
    try {
      var storage = window.localStorage;
      if (storage) storage.setItem("exitLensSettings", payload);
      return true;
    } catch (error) {
      return true;
    }
  }

  function removeStoredSettings() {
    settingsMemoryStore = "";
    try {
      var storage = window.localStorage;
      if (storage) storage.removeItem("exitLensSettings");
      return true;
    } catch (error) {
      return true;
    }
  }

  function persistSettings() {
    state.settingsSaved = writeStoredSettings(JSON.stringify(state.settings));
    if (!state.settingsSaved) {
      state.error = "브라우저 저장소에 설정을 저장하지 못했습니다.";
    }
  }

  function clearSettings() {
    state.settings = Object.assign({}, defaultSettings);
    state.showSecrets = false;
    state.settingsSaved = false;
    if (!removeStoredSettings()) {
      state.error = "브라우저 저장소에서 설정을 삭제하지 못했습니다.";
    }
  }

  function flowLensPath() {
    return state.dataMode === "mock" ? "/api/flow-lens?mock=1" : "/api/flow-lens";
  }

  function staticMockSnapshot() {
    var stamped = new Date().toISOString();
    var news = [
      {
        title: "AI 반도체 투자와 전력 인프라 지출이 성장주 흐름을 좌우",
        source: "mock",
        url: "",
        publishedAt: stamped,
        summary: "AI CAPEX와 전력망 증설 이슈가 투자자 낙관과 추격 심리를 키웁니다."
      },
      {
        title: "달러와 금리가 재상승하면 위험자산 포지션 크기 조절 필요",
        source: "mock",
        url: "",
        publishedAt: stamped,
        summary: "환율과 금리 변동은 공포 매도와 관망 심리를 자극합니다."
      },
      {
        title: "한국 증시는 반도체 수급과 외국인 매수 지속 여부가 핵심",
        source: "mock",
        url: "",
        publishedAt: stamped,
        summary: "반도체 집중도가 높을수록 외국인 매수, 수급 유입, 차익 실현 뉴스 민감도가 커집니다."
      }
    ];
    var social = [
      {
        id: "static-social-ai",
        author: "market_signal",
        source: "mock",
        text: "AI capex is still driving chip names and traders are chasing winners, but bottlenecks can cap the next leg.",
        url: "",
        createdAt: stamped,
        metrics: { reposts: 18, replies: 7, likes: 96, quotes: 4 }
      },
      {
        id: "static-social-rates",
        author: "macro_watch",
        source: "mock",
        text: "Dollar strength and yields decide whether risk appetite holds or fear selling returns.",
        url: "",
        createdAt: stamped,
        metrics: { reposts: 9, replies: 3, likes: 41, quotes: 2 }
      },
      {
        id: "static-social-korea",
        author: "seoul_flow",
        source: "mock",
        text: "KOSPI flow is tied to foreign buying in semis. If that pauses, profit taking matters more.",
        url: "",
        createdAt: stamped,
        metrics: { reposts: 12, replies: 5, likes: 54, quotes: 3 }
      }
    ];
    var items = [
      {
        symbol: "005930",
        name: "삼성전자",
        source: "holding",
        sector: "반도체",
        market: "KR",
        currency: "KRW",
        marketValue: 864000,
        profitLoss: 84000,
        profitLossRate: 10.8,
        signalScore: 64,
        exitPressure: 92,
        decision: "매도 검토",
        tone: "danger",
        priority: 1,
        reasons: ["리스크 뉴스가 반복되어 포지션 크기를 줄이는 판단에 가중치를 줬습니다.", "반도체 테마가 강하지만 이미 수익이 난 구간이라 분할 매도 기준이 우선입니다."],
        triggers: ["수익률 10.8% 구간: 분할 익절 비율 확정", "관련 기사/포스팅 4건의 방향성 확인"],
        matchedSignals: [{ title: news[0].title, source: "mock", type: "news", url: "" }]
      },
      {
        symbol: "NVDA",
        name: "NVIDIA",
        source: "watchlist",
        sector: "반도체",
        market: "US",
        currency: "USD",
        marketValue: 0,
        profitLoss: 0,
        profitLossRate: 0,
        signalScore: 70,
        exitPressure: 84,
        decision: "진입 보류",
        tone: "danger",
        priority: 1,
        reasons: ["보유 전 관심 종목은 먼저 무효화 조건과 목표 보유 기간을 정해야 합니다.", "추격 심리가 강해 기준가 없는 진입은 위험합니다."],
        triggers: ["진입 전 목표가, 손절가, 매도 사유를 한 줄로 고정", "뉴스와 포스팅 신호가 같은 방향으로 2회 이상 반복될 때만 반응"],
        matchedSignals: [{ title: social[0].text, source: "mock @market_signal", type: "post", url: "" }]
      },
      {
        symbol: "AAPL",
        name: "Apple",
        source: "holding",
        sector: "AI/플랫폼",
        market: "US",
        currency: "USD",
        marketValue: 486.2,
        profitLoss: 66.2,
        profitLossRate: 15.8,
        signalScore: 46,
        exitPressure: 68,
        decision: "부분 매도 검토",
        tone: "caution",
        priority: 2,
        reasons: ["수익 구간이어서 일부 이익 확정 기준을 점검할 때입니다.", "금리/달러 신호가 있어 성장주의 할인율 부담을 반영했습니다."],
        triggers: ["수익률 15.8% 구간: 분할 익절 비율 확정", "금리/달러/리스크 뉴스가 다음 장까지 이어지는지 확인"],
        matchedSignals: [{ title: news[1].title, source: "mock", type: "news", url: "" }]
      },
      {
        symbol: "TSLA",
        name: "Tesla",
        source: "watchlist",
        sector: "모빌리티",
        market: "US",
        currency: "USD",
        marketValue: 0,
        profitLoss: 0,
        profitLossRate: 0,
        signalScore: 35,
        exitPressure: 58,
        decision: "기준가 대기",
        tone: "caution",
        priority: 2,
        reasons: ["보유 전 관심 종목은 먼저 무효화 조건과 목표 보유 기간을 정해야 합니다."],
        triggers: ["진입 전 목표가, 손절가, 매도 사유를 한 줄로 고정"],
        matchedSignals: []
      }
    ];
    return {
      generatedAt: stamped,
      dataMode: "mock",
      mock: true,
      headline: "삼성전자의 매도 검토 우선순위가 가장 높습니다.",
      exitScore: 81,
      flowScore: 54,
      regime: "혼조 관찰",
      summary: [
        "매도 또는 축소를 검토할 종목이 4개 잡혔습니다.",
        "시장 심리는 낙관 우위이고 핵심 감정은 추격입니다.",
        "계좌는 반도체 비중이 가장 큽니다.",
        "AI/반도체 신호가 뉴스와 포스팅에서 가장 많이 잡혔습니다."
      ],
      toss: {
        mode: "mock",
        configured: false,
        status: "GitHub Pages mock 데이터",
        account: { displayNumber: "demo", type: "BROKERAGE" },
        positions: [
          { symbol: "005930", name: "삼성전자", market: "KR", currency: "KRW", quantity: "12", marketValue: 864000, profitLoss: 84000, sector: "반도체" },
          { symbol: "AAPL", name: "Apple", market: "US", currency: "USD", quantity: "2", marketValue: 486.2, profitLoss: 66.2, sector: "AI/플랫폼" },
          { symbol: "CASH", name: "대기 현금", market: "CASH", currency: "KRW", quantity: "1", marketValue: 1250000, profitLoss: 0, sector: "현금" }
        ]
      },
      portfolio: {
        total: 2114486.2,
        concentration: 59,
        sectors: [
          { sector: "현금", value: 1250000, ratio: 59 },
          { sector: "반도체", value: 864000, ratio: 41 },
          { sector: "AI/플랫폼", value: 486.2, ratio: 0 }
        ]
      },
      exitLens: {
        headline: "삼성전자의 매도 검토 우선순위가 가장 높습니다.",
        overallPressure: 81,
        urgentCount: 4,
        holdingCount: 2,
        watchCount: 2,
        items: items,
        rules: [
          "수익 구간에서 리스크 신호가 커지면 전량 매도보다 분할 매도 기준부터 확인합니다.",
          "손실 구간에서 같은 악재가 반복되면 손절 기준을 숫자로 고정합니다.",
          "관심 종목은 매수 전 목표가, 손절가, 매도 사유를 먼저 정합니다."
        ]
      },
      psychology: {
        moodScore: 62,
        moodLabel: "낙관 우위",
        tone: "watch",
        dominantEmotion: "추격",
        disagreement: 56,
        socialEngagement: 241,
        contrarianAlert: "심리가 한쪽으로 치우치지 않아 가격과 뉴스의 다음 반복을 기다립니다.",
        gauges: [
          { label: "낙관", value: 66, tone: "watch" },
          { label: "공포", value: 54, tone: "danger" },
          { label: "추격", value: 56, tone: "caution" },
          { label: "의견 충돌", value: 56, tone: "hold" }
        ],
        notes: [
          "심리 중심축은 추격입니다.",
          "심리가 한쪽으로 치우치지 않아 가격과 뉴스의 다음 반복을 기다립니다.",
          "포스팅 반응 합산 점수는 241입니다."
        ]
      },
      stockFlows: {
        headline: "삼성전자 흐름은 혼조로 읽힙니다.",
        lanes: [
          { label: "유입", value: 1, tone: "watch" },
          { label: "혼조", value: 2, tone: "hold" },
          { label: "이탈", value: 1, tone: "danger" }
        ],
        items: [
          { symbol: "005930", name: "삼성전자", sector: "반도체", source: "holding", flowScore: 60, direction: "혼조", tone: "hold", crowdTilt: "의견 충돌", read: "긍정과 경계 신호가 섞여 있어 가격 확인 전 결론을 미룹니다.", evidenceCount: 4 },
          { symbol: "NVDA", name: "NVIDIA", sector: "반도체", source: "watchlist", flowScore: 66, direction: "유입 우세", tone: "watch", crowdTilt: "추격 심리", read: "관심 유입은 강하지만 군중 추격이 겹쳐 매도 기준을 앞에 둡니다.", evidenceCount: 3 },
          { symbol: "AAPL", name: "Apple", sector: "AI/플랫폼", source: "holding", flowScore: 52, direction: "혼조", tone: "hold", crowdTilt: "의견 충돌", read: "긍정과 경계 신호가 섞여 있어 가격 확인 전 결론을 미룹니다.", evidenceCount: 2 },
          { symbol: "TSLA", name: "Tesla", sector: "모빌리티", source: "watchlist", flowScore: 42, direction: "약한 이탈", tone: "caution", crowdTilt: "관심 대기", read: "흐름이 약해지고 있어 다음 뉴스 반복 여부를 확인합니다.", evidenceCount: 0 }
        ]
      },
      themes: [
        { id: "ai", label: "AI/반도체", color: "green", count: 4, socialCount: 2, headline: news[0].title, weight: 100 },
        { id: "rates", label: "금리/달러", color: "blue", count: 2, socialCount: 1, headline: news[1].title, weight: 56 },
        { id: "korea", label: "한국/수급", color: "amber", count: 2, socialCount: 1, headline: news[2].title, weight: 56 },
        { id: "risk", label: "리스크", color: "red", count: 2, socialCount: 1, headline: news[1].title, weight: 56 },
        { id: "crypto", label: "코인/유동성", color: "violet", count: 0, socialCount: 0, headline: "관련 헤드라인 대기", weight: 0 }
      ],
      news: news,
      social: social,
      checklist: [
        { label: "보유 종목마다 전량/부분 매도 기준과 손절 기준을 숫자로 남기기", status: "주의" },
        { label: "군중 심리가 과열이면 좋은 뉴스보다 분할 매도 기준 먼저 확인", status: "정상" },
        { label: "관심 종목은 진입 전에 무효화 조건과 매도 사유부터 정하기", status: "정상" },
        { label: "X 포스팅은 기사보다 소음이 크므로 반복 등장하는 테마만 반영", status: "정상" },
        { label: "주문 기능은 읽기 전용 점검 이후 별도 단계에서만 열기", status: "잠금" }
      ]
    };
  }

  function formatClock(value) {
    if (!value) return "-";
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  }

  function formatMoney(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number)) return "-";
    if (Math.abs(number) >= 100000000) return (number / 100000000).toFixed(1) + "억";
    if (Math.abs(number) >= 10000) return Math.round(number / 10000).toLocaleString("ko-KR") + "만";
    return number.toLocaleString("ko-KR");
  }

  function pct(value) {
    return Math.round(Number(value || 0)) + "%";
  }

  function signedPct(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "0%";
    return (number > 0 ? "+" : "") + number.toFixed(Math.abs(number) >= 10 ? 0 : 1) + "%";
  }

  function sourceLabel(value) {
    if (value === "holding") return "보유";
    if (value === "watchlist") return "관심";
    return value || "-";
  }

  function pressureLabel(score) {
    var value = Number(score || 0);
    if (value >= 72) return "높음";
    if (value >= 55) return "검토";
    if (value >= 38) return "관찰";
    return "낮음";
  }

  function filteredNews(snapshot) {
    if (!snapshot) return [];
    if (state.selectedTheme === "all") return snapshot.news || [];
    var theme = (snapshot.themes || []).filter(function (entry) {
      return entry.id === state.selectedTheme;
    })[0];
    if (!theme) return snapshot.news || [];
    var label = theme.label.toLowerCase();
    return (snapshot.news || []).filter(function (item) {
      var text = (item.title + " " + item.summary + " " + item.source).toLowerCase();
      if (theme.id === "ai") return /ai|chip|semiconductor|nvidia|data center|반도체|삼성|hynix/.test(text);
      if (theme.id === "rates") return /fed|rate|yield|bond|dollar|inflation|금리|달러/.test(text);
      if (theme.id === "korea") return /korea|kospi|krw|seoul|한국|코스피|외국인/.test(text);
      if (theme.id === "risk") return /war|tariff|risk|selloff|volatility|oil|위험|관세/.test(text);
      if (theme.id === "crypto") return /bitcoin|crypto|stablecoin|token|ethereum|코인|비트코인/.test(text);
      return text.indexOf(label) >= 0;
    });
  }

  function load() {
    state.loading = !state.snapshot;
    state.refreshing = Boolean(state.snapshot);
    state.error = "";
    render();

    var loadPromise = state.dataMode === "mock" && isStaticPreviewHost()
      ? Promise.resolve(staticMockSnapshot())
      : requestJson(flowLensPath()).catch(function (error) {
        if (state.dataMode === "mock") return staticMockSnapshot();
        throw error;
      });

    return loadPromise
      .then(function (snapshot) {
        state.snapshot = snapshot;
        state.error = "";
      })
      .catch(function (error) {
        state.error = error.message;
      })
      .finally(function () {
        state.loading = false;
        state.refreshing = false;
        render();
      });
  }

  function render() {
    if (state.loading && !state.snapshot) {
      app.innerHTML = renderLoading();
      return;
    }
    if (!state.snapshot) {
      app.innerHTML = renderError();
      bindActions();
      return;
    }
    app.innerHTML = renderDashboard(state.snapshot);
    bindActions();
  }

  function renderLoading() {
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div>',
      '<p class="eyebrow">Exit Lens</p>',
      '<h1>매도 타이밍을 불러오는 중</h1>',
      '</div>',
      '</section>',
      '<section class="grid">',
      '<div class="panel skeleton tall"></div>',
      '<div class="panel skeleton"></div>',
      '<div class="panel skeleton"></div>',
      '</section>',
      '</main>'
    ].join("");
  }

  function renderError() {
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div>',
      '<p class="eyebrow">Exit Lens</p>',
      '<h1>매도 판단 스냅샷을 만들지 못했습니다</h1>',
      '<p class="subtle">' + escapeHtml(state.error || "알 수 없는 오류") + "</p>",
      '</div>',
      '<button class="icon-button" data-action="refresh" title="새로고침">↻</button>',
      '</section>',
      '</main>'
    ].join("");
  }

  function renderDashboard(snapshot) {
    var news = filteredNews(snapshot);
    var toss = snapshot.toss || { mode: "demo" };
    var modeLabel = snapshot.mock ? "Mock" : (toss.mode === "live" ? "Toss live" : "Demo");
    var modeClass = snapshot.mock ? "mock" : (toss.mode === "live" ? "live" : "demo");
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div>',
      '<p class="eyebrow">Exit Lens</p>',
      '<h1>보유/관심 종목 매도 타이밍</h1>',
      '<p class="subtle">' + escapeHtml(snapshot.headline) + " · " + escapeHtml(formatClock(snapshot.generatedAt)) + "</p>",
      '</div>',
      '<div class="toolbar">',
      '<div class="mode-toggle" role="group" aria-label="데이터 모드">',
      '<button class="' + (state.dataMode === "live" ? "active" : "") + '" data-mode="live">실데이터</button>',
      '<button class="' + (state.dataMode === "mock" ? "active" : "") + '" data-mode="mock">Mock</button>',
      '</div>',
      '<span class="status-pill ' + modeClass + '">' + escapeHtml(modeLabel) + "</span>",
      '<button class="icon-button" data-action="refresh" title="새로고침">' + (state.refreshing ? "…" : "↻") + "</button>",
      '</div>',
      '</section>',
      renderTabs(),
      renderActiveTab(snapshot, news),
      '</main>'
    ].join("");
  }

  function renderTabs() {
    return [
      '<nav class="tab-bar" aria-label="앱 탭">',
      tabs.map(function (tab) {
        return '<button class="' + (state.activeTab === tab.id ? "active" : "") + '" data-tab="' + escapeHtml(tab.id) + '">' + escapeHtml(tab.label) + '</button>';
      }).join(""),
      '</nav>'
    ].join("");
  }

  function renderActiveTab(snapshot, news) {
    if (state.activeTab === "flow") {
      return [
        '<section class="content-grid">',
        renderPsychologyPanel(snapshot),
        renderStockFlowPanel(snapshot),
        renderThemePanel(snapshot),
        '</section>'
      ].join("");
    }
    if (state.activeTab === "feed") {
      return [
        '<section class="content-grid">',
        renderNewsPanel(snapshot, news),
        renderSocialPanel(snapshot),
        renderChecklistPanel(snapshot),
        '</section>'
      ].join("");
    }
    if (state.activeTab === "settings") {
      return [
        '<section class="content-grid">',
        renderSettingsPanel(),
        '</section>'
      ].join("");
    }
    return [
      '<section class="hero-grid">',
      renderScorePanel(snapshot),
      renderSourcePanel(snapshot),
      '</section>',
      '<section class="content-grid">',
      renderExitPanel(snapshot),
      renderPortfolioPanel(snapshot),
      renderChecklistPanel(snapshot),
      '</section>'
    ].join("");
  }

  function renderScorePanel(snapshot) {
    var exitLens = snapshot.exitLens || {};
    var score = Number(snapshot.exitScore || exitLens.overallPressure || 0);
    return [
      '<article class="panel score-panel">',
      '<div class="score-wrap">',
      '<div class="score-ring" style="--score:' + score + '">',
      '<span>' + escapeHtml(score) + '</span>',
      '</div>',
      '<div>',
      '<p class="label">Exit Pressure</p>',
      '<h2>매도 압력 ' + escapeHtml(pressureLabel(score)) + '</h2>',
      '<p class="subtle">' + escapeHtml(exitLens.urgentCount || 0) + '개 종목은 매도/축소 기준 확인이 필요합니다.</p>',
      '</div>',
      '</div>',
      '<div class="summary-list">',
      (snapshot.summary || []).map(function (item) {
        return '<p>' + escapeHtml(item) + '</p>';
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSourcePanel(snapshot) {
    var toss = snapshot.toss || { account: {} };
    var exitLens = snapshot.exitLens || {};
    return [
      '<aside class="panel source-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">데이터 상태</p>',
      '<h2>연결 맥락</h2>',
      '</div>',
      '</div>',
      '<div class="source-stack">',
      '<div class="source-row"><span>표시 모드</span><strong>' + escapeHtml(snapshot.mock ? "Mock 데이터" : "기본 데이터") + '</strong></div>',
      '<div class="source-row"><span>토스</span><strong>' + escapeHtml(toss.status || "-") + '</strong></div>',
      '<div class="source-row"><span>계좌</span><strong>' + escapeHtml(toss.account && toss.account.displayNumber || "-") + '</strong></div>',
      '<div class="source-row"><span>보유/관심</span><strong>' + escapeHtml(exitLens.holdingCount || 0) + ' / ' + escapeHtml(exitLens.watchCount || 0) + '</strong></div>',
      '<div class="source-row"><span>매도 검토</span><strong>' + escapeHtml(exitLens.urgentCount || 0) + '개</strong></div>',
      '<div class="source-row"><span>뉴스</span><strong>' + escapeHtml((snapshot.news || []).length) + '건</strong></div>',
      '<div class="source-row"><span>포스팅</span><strong>' + escapeHtml((snapshot.social || []).length) + '건</strong></div>',
      '</div>',
      '</aside>'
    ].join("");
  }

  function renderExitPanel(snapshot) {
    var exitLens = snapshot.exitLens || { items: [], rules: [] };
    var items = exitLens.items || [];
    return [
      '<article class="panel exit-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Sell Timing</p>',
      '<h2>오늘 먼저 볼 종목</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(items.length) + '</span>',
      '</div>',
      '<div class="exit-list">',
      items.map(function (item) {
        var signals = item.matchedSignals || [];
        return [
          '<div class="exit-row">',
          '<div class="exit-main">',
          '<div class="exit-title">',
          '<div>',
          '<h3>' + escapeHtml(item.name) + '</h3>',
          '<p class="subtle">' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.sector) + '</p>',
          '</div>',
          '<div class="exit-badges">',
          '<span class="source-chip ' + escapeHtml(item.source) + '">' + escapeHtml(sourceLabel(item.source)) + '</span>',
          '<span class="decision-chip ' + escapeHtml(item.tone) + '">' + escapeHtml(item.decision) + '</span>',
          '</div>',
          '</div>',
          '<div class="exit-reasons">',
          (item.reasons || []).map(function (reason) {
            return '<p>' + escapeHtml(reason) + '</p>';
          }).join(""),
          '</div>',
          '<div class="trigger-list">',
          (item.triggers || []).map(function (trigger) {
            return '<span>' + escapeHtml(trigger) + '</span>';
          }).join(""),
          '</div>',
          signals.length ? '<div class="evidence-list">' + signals.map(function (signal) {
            return '<span>' + escapeHtml(signal.type === "post" ? "X" : "뉴스") + ' · ' + escapeHtml(signal.source) + '</span>';
          }).join("") + '</div>' : '',
          '</div>',
          '<div class="exit-score">',
          '<strong>' + escapeHtml(item.exitPressure) + '</strong>',
          '<span>압력</span>',
          item.source === "holding" ? '<em>' + escapeHtml(signedPct(item.profitLossRate)) + '</em>' : '<em>watch</em>',
          '</div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="rule-strip">',
      (exitLens.rules || []).map(function (rule) {
        return '<span>' + escapeHtml(rule) + '</span>';
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderPsychologyPanel(snapshot) {
    var psychology = snapshot.psychology || { gauges: [], notes: [] };
    return [
      '<article class="panel psychology-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Market Psychology</p>',
      '<h2>사람들의 심리</h2>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(psychology.tone || "hold") + '">' + escapeHtml(psychology.moodLabel || "대기") + '</span>',
      '</div>',
      '<div class="psychology-body">',
      '<div class="mood-meter">',
      '<div>',
      '<strong>' + escapeHtml(psychology.moodScore || 0) + '</strong>',
      '<span>' + escapeHtml(psychology.dominantEmotion || "관망") + '</span>',
      '</div>',
      '<div class="mood-track"><span style="width:' + Math.min(100, Math.max(0, Number(psychology.moodScore || 0))) + '%"></span></div>',
      '</div>',
      '<div class="gauge-grid">',
      (psychology.gauges || []).map(function (gauge) {
        return [
          '<div class="gauge-card ' + escapeHtml(gauge.tone || "hold") + '">',
          '<div><strong>' + escapeHtml(gauge.label) + '</strong><span>' + escapeHtml(gauge.value || 0) + '</span></div>',
          '<div class="bar-track"><span style="width:' + Math.min(100, Math.max(2, Number(gauge.value || 0))) + '%"></span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="psychology-notes">',
      (psychology.notes || []).map(function (note) {
        return '<p>' + escapeHtml(note) + '</p>';
      }).join(""),
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderStockFlowPanel(snapshot) {
    var stockFlows = snapshot.stockFlows || { items: [], lanes: [] };
    return [
      '<article class="panel flow-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Stock Flow</p>',
      '<h2>종목 흐름 읽기</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml((stockFlows.items || []).length) + '</span>',
      '</div>',
      '<div class="flow-lanes">',
      (stockFlows.lanes || []).map(function (lane) {
        return '<div class="flow-lane ' + escapeHtml(lane.tone || "hold") + '"><strong>' + escapeHtml(lane.value || 0) + '</strong><span>' + escapeHtml(lane.label) + '</span></div>';
      }).join(""),
      '</div>',
      '<div class="flow-list">',
      (stockFlows.items || []).map(function (item) {
        return [
          '<div class="flow-row">',
          '<div class="flow-main">',
          '<div class="flow-title">',
          '<div><strong>' + escapeHtml(item.name) + '</strong><span>' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.crowdTilt) + '</span></div>',
          '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.direction) + '</span>',
          '</div>',
          '<p>' + escapeHtml(item.read) + '</p>',
          '</div>',
          '<div class="flow-score"><strong>' + escapeHtml(item.flowScore) + '</strong><span>흐름</span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function settingValue(name) {
    return state.settings && state.settings[name] != null ? state.settings[name] : "";
  }

  function renderSettingField(name, label, type, placeholder) {
    return [
      '<label class="setting-field">',
      '<span>' + escapeHtml(label) + '</span>',
      '<input data-setting="' + escapeHtml(name) + '" type="' + escapeHtml(type || "text") + '" value="' + escapeHtml(settingValue(name)) + '" placeholder="' + escapeHtml(placeholder || "") + '" autocomplete="off" />',
      '</label>'
    ].join("");
  }

  function renderSettingsPanel() {
    var secretType = state.showSecrets ? "text" : "password";
    return [
      '<article class="panel settings-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">App Settings</p>',
      '<h2>앱 설정과 secret</h2>',
      '</div>',
      '<span class="tone-chip ' + (state.settingsSaved ? "watch" : "hold") + '">' + (state.settingsSaved ? "저장됨" : "로컬") + '</span>',
      '</div>',
      '<div class="settings-body">',
      '<div class="settings-note">',
      '<strong>저장 위치</strong>',
      '<p>입력값은 이 브라우저의 localStorage에만 저장됩니다. GitHub Pages에서는 서버로 secret을 전송하지 않습니다.</p>',
      '</div>',
      '<div class="settings-grid">',
      renderSettingField("watchlistSymbols", "관심 종목", "text", "NVDA,TSLA,000660"),
      renderSettingField("tossApiBaseUrl", "Toss API Base URL", "url", "https://openapi.tossinvest.com"),
      renderSettingField("tossClientId", "Toss Client ID", "text", "client id"),
      renderSettingField("tossClientSecret", "Toss Client Secret", secretType, "client secret"),
      renderSettingField("tossAccountSeq", "Toss Account Seq", "text", "선택"),
      renderSettingField("xBearerToken", "X Bearer Token", secretType, "bearer token"),
      '<label class="setting-field wide">',
      '<span>X Search Query</span>',
      '<textarea data-setting="xSearchQuery" rows="3" autocomplete="off">' + escapeHtml(settingValue("xSearchQuery")) + '</textarea>',
      '</label>',
      '</div>',
      '<div class="settings-actions">',
      '<button class="text-button primary" data-action="save-settings">저장</button>',
      '<button class="text-button" data-action="toggle-secrets">' + (state.showSecrets ? "숨기기" : "secret 보기") + '</button>',
      '<button class="text-button danger" data-action="clear-settings">삭제</button>',
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderPortfolioPanel(snapshot) {
    var portfolio = snapshot.portfolio || { sectors: [] };
    var toss = snapshot.toss || { positions: [] };
    var positions = toss.positions || [];
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Toss Portfolio</p>',
      '<h2>보유 노출</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(formatMoney(portfolio.total)) + '</span>',
      '</div>',
      '<div class="allocation">',
      portfolio.sectors.map(function (sector) {
        return [
          '<div class="bar-row">',
          '<div class="bar-meta"><span>' + escapeHtml(sector.sector) + '</span><strong>' + escapeHtml(pct(sector.ratio)) + '</strong></div>',
          '<div class="bar-track"><span style="width:' + Math.min(100, Math.max(2, sector.ratio)) + '%"></span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="position-list">',
      positions.slice(0, 5).map(function (item) {
        return [
          '<div class="position-row">',
          '<div><strong>' + escapeHtml(item.name) + '</strong><span>' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.sector) + '</span></div>',
          '<div class="right"><strong>' + escapeHtml(formatMoney(item.marketValue)) + '</strong><span>' + escapeHtml(item.currency || "") + '</span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderThemePanel(snapshot) {
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Signal Themes</p>',
      '<h2>매도 판단에 영향을 주는 신호</h2>',
      '</div>',
      '</div>',
      '<div class="theme-tabs">',
      '<button class="' + (state.selectedTheme === "all" ? "active" : "") + '" data-theme="all">전체</button>',
      (snapshot.themes || []).map(function (theme) {
        return '<button class="' + (state.selectedTheme === theme.id ? "active" : "") + '" data-theme="' + escapeHtml(theme.id) + '">' + escapeHtml(theme.label) + '</button>';
      }).join(""),
      '</div>',
      '<div class="theme-list">',
      (snapshot.themes || []).map(function (theme) {
        return [
          '<div class="theme-row ' + escapeHtml(theme.color) + '">',
          '<div><strong>' + escapeHtml(theme.label) + '</strong><span>' + escapeHtml(theme.headline) + '</span></div>',
          '<div class="theme-count"><strong>' + escapeHtml(theme.count) + '</strong><span>X ' + escapeHtml(theme.socialCount || 0) + '</span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderNewsPanel(snapshot, news) {
    return [
      '<article class="panel news-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">News Feed</p>',
      '<h2>매도 근거 기사</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(news.length) + '</span>',
      '</div>',
      '<div class="news-list">',
      news.map(function (item) {
        var content = [
          '<div class="news-item">',
          '<div>',
          '<p class="news-source">' + escapeHtml(item.source) + '</p>',
          '<h3>' + escapeHtml(item.title) + '</h3>',
          item.summary && item.summary !== item.title ? '<p class="subtle">' + escapeHtml(item.summary) + '</p>' : '',
          '</div>',
          item.url ? '<a class="open-link" href="' + escapeHtml(item.url) + '" target="_blank" rel="noreferrer" title="원문 열기">↗</a>' : '',
          '</div>'
        ];
        return content.join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSocialPanel(snapshot) {
    var posts = snapshot.social || [];
    return [
      '<article class="panel social-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Social Pulse</p>',
      '<h2>X 포스팅 신호</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(posts.length) + '</span>',
      '</div>',
      '<div class="social-list">',
      posts.map(function (post) {
        var metrics = post.metrics || {};
        return [
          '<div class="social-item">',
          '<div>',
          '<p class="post-meta">' + escapeHtml(post.source || "post") + ' · @' + escapeHtml(post.author || "unknown") + '</p>',
          '<p>' + escapeHtml(post.text) + '</p>',
          '<div class="engagement">',
          '<span>↻ ' + escapeHtml(metrics.reposts || 0) + '</span>',
          '<span>♡ ' + escapeHtml(metrics.likes || 0) + '</span>',
          '<span>↩ ' + escapeHtml(metrics.replies || 0) + '</span>',
          '</div>',
          '</div>',
          post.url ? '<a class="open-link" href="' + escapeHtml(post.url) + '" target="_blank" rel="noreferrer" title="포스팅 열기">↗</a>' : '',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderChecklistPanel(snapshot) {
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Trade Check</p>',
      '<h2>매도 전 확인할 것</h2>',
      '</div>',
      '</div>',
      '<div class="check-list">',
      (snapshot.checklist || []).map(function (item) {
        return [
          '<div class="check-row">',
          '<span class="check-state ' + escapeHtml(item.status) + '">' + escapeHtml(item.status) + '</span>',
          '<p>' + escapeHtml(item.label) + '</p>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function bindActions() {
    var refresh = app.querySelector('[data-action="refresh"]');
    if (refresh) {
      refresh.addEventListener("click", function () {
        if (!state.refreshing) load();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-tab]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var nextTab = button.getAttribute("data-tab") || "decision";
        if (nextTab === state.activeTab) return;
        state.activeTab = nextTab;
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-mode]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var nextMode = button.getAttribute("data-mode") || "live";
        if (nextMode === state.dataMode || state.refreshing) return;
        state.dataMode = nextMode === "mock" ? "mock" : "live";
        state.selectedTheme = "all";
        state.snapshot = null;
        persistDataMode(state.dataMode);
        load();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-theme]")).forEach(function (button) {
      button.addEventListener("click", function () {
        state.selectedTheme = button.getAttribute("data-theme") || "all";
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-setting]")).forEach(function (field) {
      field.addEventListener("input", function () {
        var name = field.getAttribute("data-setting");
        if (!name) return;
        state.settings[name] = field.value;
        state.settingsSaved = false;
      });
    });

    var saveSettings = app.querySelector('[data-action="save-settings"]');
    if (saveSettings) {
      saveSettings.addEventListener("click", function () {
        persistSettings();
        render();
      });
    }

    var toggleSecrets = app.querySelector('[data-action="toggle-secrets"]');
    if (toggleSecrets) {
      toggleSecrets.addEventListener("click", function () {
        state.showSecrets = !state.showSecrets;
        render();
      });
    }

    var clearSettingsButton = app.querySelector('[data-action="clear-settings"]');
    if (clearSettingsButton) {
      clearSettingsButton.addEventListener("click", function () {
        clearSettings();
        render();
      });
    }
  }

  load();
})();
