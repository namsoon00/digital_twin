(function () {
  var app = document.getElementById("app");
  var defaultSettings = {
    watchlistSymbols: "NVDA,TSLA,000660",
    tossApiBaseUrl: "https://openapi.tossinvest.com",
    tossClientId: "",
    tossClientSecret: "",
    tossAccountSeq: "",
    notifyProvider: "",
    telegramBotToken: "",
    telegramChatId: "",
    notifyLinkUrl: "http://127.0.0.1:3000",
    notifyIntervalMinutes: "10",
    valuationAssumptions: [
      "AAPL,7.5,28,15",
      "005930,6500,12,20"
    ].join("\n"),
    marketSignalInputs: [
      "005930,118,1.8,620000,480000,18,2.1",
      "AAPL,86,1.4,320000,410000,-12,-1.8",
      "NVDA,132,2.3,780000,520000,22,3.5",
      "TSLA,91,1.2,210000,260000,-8,-0.9",
      "000660,122,1.7,510000,390000,15,2.4"
    ].join("\n"),
    fairValueFormula: "eps * targetPer * growthWeight * qualityWeight * riskWeight",
    buyScoreFormula: "50 + ((tradeStrength - 100) * 0.25 + (volumeRatio - 1) * 12 + (buyShare - 50) * 0.35 + bidAskImbalance * 0.28 + priceChangeRate * 1.1) * flowWeight + undervalueBonus * valuationWeight - expensivePenalty * valuationWeight",
    sellScoreFormula: "50 + ((100 - tradeStrength) * 0.22 + (volumeRatio - 1) * 8 + (50 - buyShare) * 0.42 - bidAskImbalance * 0.28 - priceChangeRate * 1.2) * flowWeight + expensiveBonus * valuationWeight",
    modelName: "나의 매수/매도 모델",
    modelHypothesis: "수급, 가치, 내 점수, 리스크를 함께 봐서 매수 후보와 매도 후보를 분리한다.",
    customBuyModelFormula: "buyScore * 0.35 + thesisScore * thesisWeight + confidenceScore * confidenceWeight + max(0, targetReturn) * 0.15 + undervalueBonus * valuationWeight - riskScore * riskControlWeight",
    customSellModelFormula: "sellScore * 0.35 + riskScore * riskControlWeight + expensivePenalty * valuationWeight + max(0, -targetReturn) * 0.2 - thesisScore * 0.1",
    formulaWeights: [
      "growthWeight=1",
      "qualityWeight=1",
      "riskWeight=1",
      "flowWeight=1",
      "valuationWeight=1",
      "thesisWeight=0.25",
      "confidenceWeight=0.15",
      "riskControlWeight=0.35"
    ].join("\n"),
    decisionThresholds: [
      "buyCandidate=78",
      "chaseCaution=70",
      "strongHold=72",
      "sellTrim=70",
      "riskReduce=66",
      "sellWatch=64"
    ].join("\n"),
    modelDecisionThresholds: [
      "modelBuy=74",
      "modelAdd=70",
      "modelSell=72",
      "modelReduce=64",
      "modelHold=55"
    ].join("\n")
  };
  var tabs = [
    { id: "decision", label: "판단" },
    { id: "lab", label: "실험실" },
    { id: "model", label: "모델" },
    { id: "holdings", label: "보유" },
    { id: "feed", label: "피드" },
    { id: "watchlist", label: "관심" }
  ];
  var feedChannels = [
    {
      id: "cnbc-markets",
      label: "CNBC 시장",
      provider: "CNBC",
      kind: "rss",
      feedUrl: "https://www.cnbc.com/id/15839135/device/rss/rss.html",
      tags: ["미국", "실적", "AI"]
    },
    {
      id: "yahoo-market-tape",
      label: "Yahoo 시장",
      provider: "Yahoo Finance",
      kind: "rss",
      feedUrl: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,KRW=X,BTC-USD&region=US&lang=en-US",
      tags: ["지수", "환율", "유동성"]
    },
    {
      id: "fed-policy",
      label: "Fed 정책",
      provider: "Federal Reserve",
      kind: "rss",
      feedUrl: "https://www.federalreserve.gov/feeds/press_all.xml",
      tags: ["금리", "정책", "달러"]
    },
    {
      id: "yonhap-economy",
      label: "연합뉴스 경제",
      provider: "연합뉴스",
      kind: "rss",
      feedUrl: "https://www.yna.co.kr/rss/economy.xml",
      tags: ["한국", "증권", "산업"]
    },
    {
      id: "coindesk-markets",
      label: "CoinDesk 마켓",
      provider: "CoinDesk",
      kind: "rss",
      feedUrl: "https://www.coindesk.com/arc/outboundfeeds/rss/",
      tags: ["코인", "유동성", "리스크"]
    },
    {
      id: "gdelt-cross-source",
      label: "GDELT 글로벌",
      provider: "GDELT",
      kind: "gdelt",
      query: '"stock market" OR "central bank" OR semiconductor OR Korea OR cryptocurrency',
      tags: ["글로벌", "교차검증", "뉴스"]
    }
  ];
  var settingsMemoryStore = "";
  var labDraftsMemoryStore = "";
  var labRecordsMemoryStore = "";
  var modelVersionsMemoryStore = "";
  var state = {
    loading: true,
    refreshing: false,
    error: "",
    snapshot: null,
    feed: null,
    feedLoading: false,
    feedError: "",
    dataMode: initialDataMode(),
    activeTab: initialTab(),
    settings: loadSettings(),
    settingsOpen: false,
    showSecrets: false,
    settingsSaved: false,
    serverSettingsLoaded: false,
    serverSettingsError: "",
    serverSettingsLocked: false,
    serverConfigured: {},
    labDrafts: loadLabDrafts(),
    labRecords: loadLabRecords(),
    modelVersions: loadModelVersions(),
    labRecordSaved: false,
    labRecordError: "",
    modelSaved: false,
    modelError: "",
    editingWatchSymbol: "",
    watchlistError: ""
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

  function sendJson(path, method, payload) {
    return fetch(path, {
      method: method,
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json"
      },
      cache: "no-store",
      body: JSON.stringify(payload || {})
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) throw new Error(body.error || "요청 실패");
        return body;
      });
    });
  }

  function requestText(path) {
    return fetch(path, {
      headers: { "Accept": "application/rss+xml, application/xml;q=0.9, text/plain;q=0.8, */*;q=0.7" },
      cache: "no-store"
    }).then(function (response) {
      return response.text().then(function (body) {
        if (!response.ok) {
          var message = "요청 실패";
          try {
            message = JSON.parse(body).error || message;
          } catch (error) {
            message = body || message;
          }
          throw new Error(message);
        }
        return body;
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

  function readLocalPayload(key, fallback) {
    try {
      var storage = window.localStorage;
      return storage ? storage.getItem(key) : fallback;
    } catch (error) {
      return fallback;
    }
  }

  function writeLocalPayload(key, payload, memorySetter) {
    memorySetter(payload);
    try {
      var storage = window.localStorage;
      if (storage) storage.setItem(key, payload);
      return true;
    } catch (error) {
      return true;
    }
  }

  function loadLabDrafts() {
    try {
      return JSON.parse(readLocalPayload("exitLensLabDrafts", labDraftsMemoryStore) || "{}") || {};
    } catch (error) {
      return {};
    }
  }

  function persistLabDrafts() {
    return writeLocalPayload("exitLensLabDrafts", JSON.stringify(state.labDrafts || {}), function (payload) {
      labDraftsMemoryStore = payload;
    });
  }

  function loadLabRecords() {
    try {
      var records = JSON.parse(readLocalPayload("exitLensLabRecords", labRecordsMemoryStore) || "[]");
      return Array.isArray(records) ? records : [];
    } catch (error) {
      return [];
    }
  }

  function persistLabRecords() {
    return writeLocalPayload("exitLensLabRecords", JSON.stringify(state.labRecords || []), function (payload) {
      labRecordsMemoryStore = payload;
    });
  }

  function loadModelVersions() {
    try {
      var versions = JSON.parse(readLocalPayload("exitLensModelVersions", modelVersionsMemoryStore) || "[]");
      return Array.isArray(versions) ? versions : [];
    } catch (error) {
      return [];
    }
  }

  function persistModelVersions() {
    return writeLocalPayload("exitLensModelVersions", JSON.stringify(state.modelVersions || []), function (payload) {
      modelVersionsMemoryStore = payload;
    });
  }

  function persistSettings() {
    state.settingsSaved = writeStoredSettings(JSON.stringify(state.settings));
    if (!state.settingsSaved) {
      state.error = "브라우저 저장소에 설정을 저장하지 못했습니다.";
    }
  }

  function applyServerSettings(payload) {
    var nextSettings = payload.settings || {};
    state.settings = Object.assign({}, state.settings, nextSettings);
    state.serverConfigured = payload.configured || {};
    state.serverSettingsLocked = Boolean(payload.locked);
    state.serverSettingsLoaded = true;
    state.serverSettingsError = "";
    state.settingsSaved = true;
    persistSettings();
  }

  function loadServerSettings() {
    if (isStaticPreviewHost()) return Promise.resolve();
    return requestJson("/api/settings")
      .then(function (payload) {
        applyServerSettings(payload);
      })
      .catch(function (error) {
        state.serverSettingsError = error.message || "서버 설정을 읽지 못했습니다.";
      });
  }

  function serverSettingsPayload() {
    return {
      watchlistSymbols: settingValue("watchlistSymbols"),
      tossApiBaseUrl: settingValue("tossApiBaseUrl"),
      tossClientId: settingValue("tossClientId"),
      tossClientSecret: settingValue("tossClientSecret"),
      tossAccountSeq: settingValue("tossAccountSeq"),
      notifyProvider: settingValue("notifyProvider"),
      telegramBotToken: settingValue("telegramBotToken"),
      telegramChatId: settingValue("telegramChatId"),
      notifyLinkUrl: settingValue("notifyLinkUrl"),
      notifyIntervalMinutes: settingValue("notifyIntervalMinutes")
    };
  }

  function saveSettingsToServer() {
    if (isStaticPreviewHost()) {
      persistSettings();
      return Promise.resolve();
    }
    return sendJson("/api/settings", "PUT", { settings: serverSettingsPayload() })
      .then(function (payload) {
        applyServerSettings(payload);
      });
  }

  function clearSettings() {
    state.settings = Object.assign({}, defaultSettings);
    state.showSecrets = false;
    state.settingsSaved = false;
    state.editingWatchSymbol = "";
    state.watchlistError = "";
    if (!removeStoredSettings()) {
      state.error = "브라우저 저장소에서 설정을 삭제하지 못했습니다.";
    }
  }

  function normalizeSymbols(value) {
    return String(value || "")
      .split(/[,\s]+/)
      .map(function (symbol) { return symbol.trim().toUpperCase(); })
      .filter(Boolean)
      .filter(function (symbol, index, list) { return list.indexOf(symbol) === index; })
      .slice(0, 30);
  }

  function watchlistSymbols() {
    return normalizeSymbols(settingValue("watchlistSymbols"));
  }

  function clientKnownStockInfo(symbol) {
    var normalized = String(symbol || "").trim().toUpperCase();
    var map = {
      "005930": { name: "삼성전자", market: "KR", currency: "KRW", sector: "반도체" },
      "000660": { name: "SK하이닉스", market: "KR", currency: "KRW", sector: "반도체" },
      AAPL: { name: "Apple", market: "US", currency: "USD", sector: "AI/플랫폼" },
      MSFT: { name: "Microsoft", market: "US", currency: "USD", sector: "AI/플랫폼" },
      NVDA: { name: "NVIDIA", market: "US", currency: "USD", sector: "반도체" },
      AMD: { name: "AMD", market: "US", currency: "USD", sector: "반도체" },
      TSLA: { name: "Tesla", market: "US", currency: "USD", sector: "모빌리티" },
      GOOGL: { name: "Alphabet", market: "US", currency: "USD", sector: "AI/플랫폼" },
      META: { name: "Meta", market: "US", currency: "USD", sector: "AI/플랫폼" }
    };
    return Object.assign({
      symbol: normalized,
      name: normalized || "관심 종목",
      market: "",
      currency: "",
      sector: ""
    }, map[normalized] || {});
  }

  function saveWatchlistSymbols(symbols) {
    state.settings.watchlistSymbols = normalizeSymbols(symbols.join(",")).join(",");
    state.editingWatchSymbol = "";
    state.watchlistError = "";
    persistSettings();
    state.snapshot = null;
    return load();
  }

  function tossLensPath() {
    var params = new URLSearchParams();
    if (state.dataMode === "mock") params.set("mock", "1");
    var symbols = watchlistSymbols().join(",");
    if (symbols) params.set("watchlistSymbols", symbols);
    var query = params.toString();
    return "/api/flow-lens" + (query ? "?" + query : "");
  }

  function gdeltFeedUrl(channel) {
    var target = new URL("https://api.gdeltproject.org/api/v2/doc/doc");
    target.searchParams.set("query", channel.query || "market stocks");
    target.searchParams.set("mode", "ArtList");
    target.searchParams.set("format", "JSON");
    target.searchParams.set("maxrecords", "24");
    target.searchParams.set("timespan", "3d");
    target.searchParams.set("sort", "DateDesc");
    return target.toString();
  }

  function economicFeedProxyPath(channel) {
    var target = channel.kind === "gdelt" ? gdeltFeedUrl(channel) : channel.feedUrl;
    var route = channel.kind === "gdelt" ? "/api/economic-feed/gdelt" : "/api/economic-feed/rss";
    return route + "?url=" + encodeURIComponent(target);
  }

  function textFromXml(node, selector) {
    var found = node.querySelector(selector);
    return found ? String(found.textContent || "").replace(/\s+/g, " ").trim() : "";
  }

  function cleanSummary(value, fallback) {
    var text = String(value || "")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (text.length > 220) text = text.slice(0, 217) + "...";
    return text || fallback || "요약 대기";
  }

  function feedTimeValue(value) {
    var raw = String(value || "");
    var compact = raw.replace(/\D/g, "");
    if (compact.length >= 14) {
      return Date.UTC(
        Number(compact.slice(0, 4)),
        Number(compact.slice(4, 6)) - 1,
        Number(compact.slice(6, 8)),
        Number(compact.slice(8, 10)),
        Number(compact.slice(10, 12)),
        Number(compact.slice(12, 14))
      );
    }
    var parsed = Date.parse(raw);
    return Number.isNaN(parsed) ? 0 : parsed;
  }

  function formatFeedTime(value) {
    var raw = String(value || "");
    var compact = raw.replace(/\D/g, "");
    if (compact.length >= 14) {
      return compact.slice(4, 6) + "." + compact.slice(6, 8) + " " + compact.slice(8, 10) + ":" + compact.slice(10, 12);
    }
    return formatClock(value);
  }

  function parseRssFeed(raw, channel) {
    var parsed = new DOMParser().parseFromString(raw, "application/xml");
    var items = Array.prototype.slice.call(parsed.querySelectorAll("item"));
    return items.slice(0, 6).map(function (item, index) {
      var title = textFromXml(item, "title");
      var url = textFromXml(item, "link") || textFromXml(item, "guid");
      var publishedAt = textFromXml(item, "pubDate") || textFromXml(item, "updated");
      var summary = cleanSummary(textFromXml(item, "description") || textFromXml(item, "content\\:encoded"), title);
      if (!title || !url) return null;
      return {
        id: channel.id + "-" + index + "-" + title,
        title: title,
        summary: summary,
        url: url,
        source: channel.provider,
        channelId: channel.id,
        channelLabel: channel.label,
        publishedAt: publishedAt,
        publishedLabel: formatFeedTime(publishedAt),
        tags: channel.tags || [],
        sortValue: feedTimeValue(publishedAt)
      };
    }).filter(Boolean);
  }

  function parseGdeltFeed(payload, channel) {
    var articles = Array.isArray(payload.articles) ? payload.articles : [];
    return articles.slice(0, 6).map(function (article, index) {
      var title = String(article.title || "").trim();
      var url = String(article.url || "").trim();
      var publishedAt = String(article.seendate || "");
      var domain = String(article.domain || "GDELT").trim();
      if (!title || !url) return null;
      return {
        id: channel.id + "-" + index + "-" + title,
        title: title,
        summary: "GDELT가 수집한 " + domain + " 기사입니다.",
        url: url,
        source: domain,
        channelId: channel.id,
        channelLabel: channel.label,
        publishedAt: publishedAt,
        publishedLabel: formatFeedTime(publishedAt),
        tags: channel.tags || [],
        sortValue: feedTimeValue(publishedAt)
      };
    }).filter(Boolean);
  }

  function fetchFeedChannel(channel) {
    if (channel.kind === "gdelt") {
      return requestJson(economicFeedProxyPath(channel)).then(function (payload) {
        return parseGdeltFeed(payload, channel);
      });
    }
    return requestText(economicFeedProxyPath(channel)).then(function (raw) {
      return parseRssFeed(raw, channel);
    });
  }

  function staticFeedSnapshot(reason) {
    var stamped = new Date().toISOString();
    var items = [
      {
        title: "AI 인프라 지출과 금리 경로가 성장주 판단을 흔듭니다",
        summary: "정적 미리보기에서는 실제 RSS 대신 예시 피드를 보여줍니다. 로컬 서버에서는 CNBC, Yahoo, Fed, 연합뉴스, CoinDesk, GDELT를 직접 조회합니다.",
        source: "Static Preview",
        url: "",
        channelId: "preview",
        channelLabel: "미리보기",
        publishedAt: stamped,
        publishedLabel: formatFeedTime(stamped),
        tags: ["AI", "금리", "성장주"],
        sortValue: Date.now()
      },
      {
        title: "한국 수급과 코인 유동성은 별도 채널로 분리해 확인합니다",
        summary: reason || "피드 탭은 시장 관점을 여러 원천으로 나눠 비교합니다.",
        source: "Static Preview",
        url: "",
        channelId: "preview",
        channelLabel: "미리보기",
        publishedAt: stamped,
        publishedLabel: formatFeedTime(stamped),
        tags: ["한국", "코인", "유동성"],
        sortValue: Date.now() - 1
      }
    ];
    return {
      generatedAt: stamped,
      mock: true,
      items: items,
      channels: feedChannels.map(function (channel) {
        return { id: channel.id, label: channel.label, provider: channel.provider, count: 0, error: "" };
      }),
      errors: reason ? [reason] : []
    };
  }

  function buildFeedSnapshot(results) {
    var items = [];
    var errors = [];
    var channels = results.map(function (result) {
      if (result.error) errors.push(result.channel.label + ": " + result.error);
      items = items.concat(result.items || []);
      return {
        id: result.channel.id,
        label: result.channel.label,
        provider: result.channel.provider,
        count: (result.items || []).length,
        error: result.error || ""
      };
    });
    items.sort(function (a, b) {
      return (b.sortValue || 0) - (a.sortValue || 0);
    });
    if (!items.length) {
      throw new Error(errors[0] || "피드 결과 없음");
    }
    return {
      generatedAt: new Date().toISOString(),
      mock: false,
      items: items.slice(0, 30),
      channels: channels,
      errors: errors
    };
  }

  function loadFeed(force) {
    if (state.feedLoading) return Promise.resolve();
    if (state.feed && !force) return Promise.resolve(state.feed);
    state.feedLoading = true;
    state.feedError = "";
    render();

    var promise = isStaticPreviewHost()
      ? Promise.resolve(staticFeedSnapshot("정적 미리보기"))
      : Promise.all(feedChannels.map(function (channel) {
        return fetchFeedChannel(channel)
          .then(function (items) {
            return { channel: channel, items: items, error: "" };
          })
          .catch(function (error) {
            return { channel: channel, items: [], error: error.message || String(error) };
          });
      })).then(buildFeedSnapshot);

    return promise
      .then(function (feed) {
        state.feed = feed;
        state.feedError = "";
      })
      .catch(function (error) {
        if (state.dataMode === "mock") {
          state.feed = staticFeedSnapshot(error.message || "피드 조회 실패");
          state.feedError = "";
        } else {
          state.feed = null;
          state.feedError = error.message || "피드를 불러오지 못했습니다.";
        }
      })
      .finally(function () {
        state.feedLoading = false;
        render();
      });
  }

  function staticMockSnapshot() {
    var stamped = new Date().toISOString();
    var positions = [
      {
        symbol: "005930",
        name: "삼성전자",
        source: "holding",
        sector: "반도체",
        market: "KR",
        currency: "KRW",
        quantity: "12",
        sellableQuantity: "12",
        averagePrice: 65000,
        currentPrice: 72000,
        marketValue: 864000,
        profitLoss: 84000,
        profitLossRate: 10.8
      },
      {
        symbol: "AAPL",
        name: "Apple",
        source: "holding",
        sector: "AI/플랫폼",
        market: "US",
        currency: "USD",
        quantity: "2",
        sellableQuantity: "2",
        averagePrice: 210,
        currentPrice: 243.1,
        marketValue: 486.2,
        profitLoss: 66.2,
        profitLossRate: 15.8
      },
      {
        symbol: "CASH",
        name: "대기 현금",
        source: "cash",
        sector: "현금",
        market: "CASH",
        currency: "KRW",
        quantity: "1",
        sellableQuantity: "1",
        averagePrice: 0,
        currentPrice: 0,
        marketValue: 1250000,
        profitLoss: 0,
        profitLossRate: 0
      }
    ];
    var holdingSymbols = positions.map(function (item) { return String(item.symbol || "").toUpperCase(); });
    var watchlist = watchlistSymbols()
      .filter(function (symbol) { return holdingSymbols.indexOf(symbol) < 0; })
      .map(function (symbol) {
        return Object.assign(clientKnownStockInfo(symbol), {
          source: "watchlist",
          quoteStatus: "시세 조회 대기"
        });
      });
    var decisionItems = [
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
        exitPressure: 72,
        decision: "분할 매도 기준 확인",
        tone: "danger",
        reasons: ["토스 잔고 기준 수익률이 +15.8%입니다.", "보유 수량 2주가 모두 매도 가능 수량으로 잡혀 있습니다."],
        triggers: ["분할 매도 비율", "평균단가 대비 목표 수익률", "매도 가능 수량"]
      },
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
        exitPressure: 64,
        decision: "일부 익절 기준 확인",
        tone: "caution",
        reasons: ["토스 잔고 기준 수익률이 +10.8%입니다.", "반도체 보유 평가액이 계좌 내 주요 노출입니다."],
        triggers: ["평가손익", "보유 비중", "매도 가능 수량"]
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
        exitPressure: 36,
        decision: "시세 기준 대기",
        tone: "hold",
        reasons: ["관심 종목은 토스 시세 연결 후 현재가와 기준가를 비교해야 합니다."],
        triggers: ["관심 종목", "현재가", "기준가"]
      }
    ].filter(function (item) {
      if (item.source !== "watchlist") return true;
      return watchlist.some(function (watchItem) {
        return watchItem.symbol === item.symbol;
      });
    });
    return {
      generatedAt: stamped,
      dataMode: "mock",
      mock: true,
      headline: "내 토스 계좌 기준으로 AAPL 분할 매도 기준 점검이 우선입니다.",
      exitScore: 57,
      regime: "토스 조회 전용",
      summary: [
        "보유 종목 2개와 관심 종목 " + watchlist.length + "개를 토스 API 범위 안에서 분리했습니다.",
        "체결강도, 거래량, 매수/매도 체결량은 수급 탭에서 별도 조합합니다.",
        "판단 근거는 수익률, 평가손익, 매도 가능 수량, 보유 비중으로 제한합니다."
      ],
      toss: {
        mode: "mock",
        configured: false,
        status: "GitHub Pages mock 데이터",
        account: { displayNumber: "demo", type: "BROKERAGE", orderableAmount: 1250000, currency: "KRW" },
        positions: positions,
        watchlist: watchlist
      },
      portfolio: {
        total: 2114486.2,
        invested: 864486.2,
        cash: 1250000,
        concentration: 41,
        sectors: [
          { sector: "현금", value: 1250000, ratio: 59 },
          { sector: "반도체", value: 864000, ratio: 41 },
          { sector: "AI/플랫폼", value: 486.2, ratio: 0 }
        ]
      },
      tossDecision: {
        headline: "내 토스 계좌 기준으로 AAPL 분할 매도 기준 점검이 우선입니다.",
        overallPressure: 57,
        urgentCount: 2,
        holdingCount: 2,
        watchCount: watchlist.length,
        items: decisionItems,
        rules: [
          "수익률과 평가손익은 토스 잔고에서 확인 가능한 값만 사용합니다.",
          "관심 종목은 보유가 아니므로 매도 판단 대신 시세 기준 대기 상태로 둡니다.",
          "외부 텍스트 신호는 토스 전용 판단 점수에 반영하지 않습니다."
        ]
      },
      checklist: [
        { label: "토스 잔고의 수익률, 평가손익, 매도 가능 수량 확인", status: "정상" },
        { label: "관심 종목은 토스 시세 연결 후 현재가 기준만 비교", status: "대기" },
        { label: "주문 실행은 읽기 전용 검증 이후 별도 단계에서만 열기", status: "잠금" }
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

  function formatCurrency(value, currency) {
    var suffix = currency ? " " + currency : "";
    return formatMoney(value) + suffix;
  }

  function formatPrice(value, currency) {
    var number = Number(value || 0);
    if (!Number.isFinite(number)) return "-";
    var suffix = currency ? " " + currency : "";
    return number.toLocaleString("ko-KR", {
      maximumFractionDigits: Number.isInteger(number) ? 0 : 2
    }) + suffix;
  }

  function pct(value) {
    return Math.round(Number(value || 0)) + "%";
  }

  function signedPct(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "0%";
    return (number > 0 ? "+" : "") + number.toFixed(Math.abs(number) >= 10 ? 0 : 1) + "%";
  }

  function signedMoney(value, currency) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "0" + (currency ? " " + currency : "");
    return (number > 0 ? "+" : "") + formatCurrency(number, currency);
  }

  function sourceLabel(value) {
    if (value === "holding") return "보유";
    if (value === "watchlist") return "관심";
    if (value === "cash") return "현금";
    return value || "-";
  }

  function numeric(value) {
    var parsed = Number(String(value == null ? "" : value).replace(/,/g, "").trim());
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function formulaSetting(name) {
    return String(settingValue(name) || defaultSettings[name] || "").trim();
  }

  function tokenizeFormula(expression) {
    var input = String(expression || "");
    var tokens = [];
    var index = 0;
    if (input.length > 1200) throw new Error("공식이 너무 깁니다.");
    while (index < input.length) {
      var char = input[index];
      if (/\s/.test(char)) {
        index += 1;
        continue;
      }
      if (/[0-9.]/.test(char)) {
        var start = index;
        index += 1;
        while (index < input.length && /[0-9.]/.test(input[index])) index += 1;
        var number = Number(input.slice(start, index));
        if (!Number.isFinite(number)) throw new Error("숫자 형식 오류");
        tokens.push({ type: "number", value: number });
        continue;
      }
      if (/[A-Za-z_]/.test(char)) {
        var nameStart = index;
        index += 1;
        while (index < input.length && /[A-Za-z0-9_]/.test(input[index])) index += 1;
        tokens.push({ type: "name", value: input.slice(nameStart, index) });
        continue;
      }
      if ("+-*/(),".indexOf(char) >= 0) {
        tokens.push({ type: char, value: char });
        index += 1;
        continue;
      }
      throw new Error("지원하지 않는 문자: " + char);
    }
    return tokens;
  }

  function evaluateFormula(expression, variables) {
    var tokens = tokenizeFormula(expression);
    var index = 0;
    variables = variables || {};

    function peek() {
      return tokens[index] || null;
    }

    function take(type) {
      var token = peek();
      if (token && token.type === type) {
        index += 1;
        return token;
      }
      return null;
    }

    function expect(type) {
      var token = take(type);
      if (!token) throw new Error("'" + type + "'가 필요합니다.");
      return token;
    }

    function safeNumber(value) {
      var number = Number(value);
      return Number.isFinite(number) ? number : 0;
    }

    function applyFormulaFunction(name, args) {
      var lower = String(name || "").toLowerCase();
      if (lower === "min") return Math.min.apply(Math, args);
      if (lower === "max") return Math.max.apply(Math, args);
      if (lower === "abs") return Math.abs(args[0] || 0);
      if (lower === "round") return Math.round(args[0] || 0);
      if (lower === "sqrt") return Math.sqrt(Math.max(0, args[0] || 0));
      if (lower === "pow") return Math.pow(args[0] || 0, args[1] || 0);
      if (lower === "clamp") return clamp(args[0] || 0, args[1] || 0, args[2] == null ? 100 : args[2]);
      throw new Error("지원하지 않는 함수: " + name);
    }

    function parsePrimary() {
      var token = peek();
      if (!token) throw new Error("공식이 끝났습니다.");
      if (take("number")) return token.value;
      if (take("name")) {
        if (take("(")) {
          var args = [];
          if (!take(")")) {
            do {
              args.push(parseExpression());
            } while (take(","));
            expect(")");
          }
          return safeNumber(applyFormulaFunction(token.value, args));
        }
        return safeNumber(variables[token.value]);
      }
      if (take("(")) {
        var value = parseExpression();
        expect(")");
        return value;
      }
      throw new Error("예상하지 못한 토큰: " + token.value);
    }

    function parseUnary() {
      if (take("+")) return parseUnary();
      if (take("-")) return -parseUnary();
      return parsePrimary();
    }

    function parseTerm() {
      var value = parseUnary();
      while (true) {
        if (take("*")) {
          value *= parseUnary();
        } else if (take("/")) {
          var divisor = parseUnary();
          value = divisor ? value / divisor : 0;
        } else {
          break;
        }
      }
      return value;
    }

    function parseExpression() {
      var value = parseTerm();
      while (true) {
        if (take("+")) {
          value += parseTerm();
        } else if (take("-")) {
          value -= parseTerm();
        } else {
          break;
        }
      }
      return value;
    }

    var output = parseExpression();
    if (index < tokens.length) throw new Error("공식 뒤에 해석되지 않은 값이 있습니다.");
    if (!Number.isFinite(output)) throw new Error("공식 결과가 숫자가 아닙니다.");
    return output;
  }

  function evaluateConfiguredFormula(expression, variables, fallback) {
    try {
      var value = evaluateFormula(expression, variables);
      return {
        value: value,
        error: "",
        usedFallback: false
      };
    } catch (error) {
      return {
        value: fallback,
        error: error.message || "공식 오류",
        usedFallback: true
      };
    }
  }

  function parseNumberAssignments(value, defaults) {
    var map = Object.assign({}, defaults || {});
    String(value || "")
      .split(/\r?\n/)
      .map(function (line) { return line.trim(); })
      .filter(Boolean)
      .forEach(function (line) {
        var parts = line.split(/[=:,]/);
        var key = String(parts[0] || "").trim();
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) return;
        map[key] = numeric(parts.slice(1).join(":"));
      });
    return map;
  }

  function formulaWeights() {
    return parseNumberAssignments(settingValue("formulaWeights"), parseNumberAssignments(defaultSettings.formulaWeights));
  }

  function decisionThresholds() {
    return parseNumberAssignments(settingValue("decisionThresholds"), parseNumberAssignments(defaultSettings.decisionThresholds));
  }

  function modelDecisionThresholds() {
    return parseNumberAssignments(settingValue("modelDecisionThresholds"), parseNumberAssignments(defaultSettings.modelDecisionThresholds));
  }

  function assignmentOrder(settingName) {
    return String(defaultSettings[settingName] || "")
      .split(/\r?\n/)
      .map(function (line) { return String(line.split(/[=:,]/)[0] || "").trim(); })
      .filter(Boolean);
  }

  function serializeNumberAssignments(map, order) {
    var seen = {};
    var keys = (order || []).filter(function (key) {
      if (!Object.prototype.hasOwnProperty.call(map, key) || seen[key]) return false;
      seen[key] = true;
      return true;
    });
    Object.keys(map).sort().forEach(function (key) {
      if (!seen[key]) keys.push(key);
    });
    return keys.map(function (key) {
      return key + "=" + Number(map[key] || 0);
    }).join("\n");
  }

  function updateNumberAssignmentSetting(settingName, key, value) {
    if (!Object.prototype.hasOwnProperty.call(defaultSettings, settingName)) return;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(String(key || ""))) return;
    var map = parseNumberAssignments(settingValue(settingName), parseNumberAssignments(defaultSettings[settingName]));
    map[key] = numeric(value);
    state.settings[settingName] = serializeNumberAssignments(map, assignmentOrder(settingName));
    persistSettings();
    state.modelSaved = false;
    state.modelError = "";
    render();
  }

  function formatSignalNumber(value, suffix) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "-";
    return number.toLocaleString("ko-KR", {
      maximumFractionDigits: Math.abs(number) >= 10 ? 0 : 1
    }) + (suffix || "");
  }

  function formatSignalRatio(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "-";
    return number.toFixed(number >= 10 ? 0 : 1) + "x";
  }

  function formatSignalVolume(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "-";
    return formatMoney(number);
  }

  function parseValuationAssumptions() {
    var map = {};
    String(settingValue("valuationAssumptions") || "")
      .split(/\r?\n/)
      .map(function (line) { return line.trim(); })
      .filter(Boolean)
      .forEach(function (line) {
        var parts = line.split(",").map(function (part) { return part.trim(); });
        var symbol = String(parts[0] || "").toUpperCase();
        if (!symbol) return;
        map[symbol] = {
          symbol: symbol,
          eps: numeric(parts[1]),
          targetPer: numeric(parts[2]),
          margin: numeric(parts[3] || 15)
        };
      });
    return map;
  }

  function parseMarketSignals() {
    var map = {};
    String(settingValue("marketSignalInputs") || "")
      .split(/\r?\n/)
      .map(function (line) { return line.trim(); })
      .filter(Boolean)
      .forEach(function (line) {
        var parts = line.split(",").map(function (part) { return part.trim(); });
        var symbol = String(parts[0] || "").toUpperCase();
        if (!symbol) return;
        map[symbol] = {
          symbol: symbol,
          tradeStrength: numeric(parts[1]),
          volumeRatio: numeric(parts[2]),
          buyVolume: numeric(parts[3]),
          sellVolume: numeric(parts[4]),
          bidAskImbalance: numeric(parts[5]),
          priceChangeRate: numeric(parts[6]),
          source: "manual"
        };
      });
    return map;
  }

  function currentPriceOf(item) {
    var currentPrice = numeric(item.currentPrice);
    if (currentPrice) return currentPrice;
    var quantity = numeric(item.quantity);
    var marketValue = numeric(item.marketValue);
    return quantity ? marketValue / quantity : 0;
  }

  function valuationStatus(currentPrice, fairValue, marginPrice) {
    if (!currentPrice || !fairValue) return { label: "입력 필요", tone: "hold", rank: 4 };
    if (currentPrice <= marginPrice) return { label: "싸다", tone: "watch", rank: 1 };
    if (currentPrice <= fairValue) return { label: "적정권", tone: "hold", rank: 2 };
    if (currentPrice <= fairValue * 1.15) return { label: "비싼 편", tone: "caution", rank: 3 };
    return { label: "비싸다", tone: "danger", rank: 3 };
  }

  function buildValuationForItem(item, assumptions, weights, formula) {
    var symbol = String(item.symbol || "").toUpperCase();
    var assumption = assumptions[symbol] || {};
    var currentPrice = currentPriceOf(item);
    var baseFairValue = assumption.eps && assumption.targetPer ? assumption.eps * assumption.targetPer : 0;
    var margin = assumption.margin || 15;
    var variables = Object.assign({}, weights, {
      eps: assumption.eps || 0,
      targetPer: assumption.targetPer || 0,
      margin: margin,
      currentPrice: currentPrice,
      averagePrice: numeric(item.averagePrice),
      quantity: numeric(item.quantity),
      marketValue: numeric(item.marketValue),
      profitLoss: numeric(item.profitLoss),
      profitLossRate: numeric(item.profitLossRate)
    });
    var formulaResult = formula
      ? evaluateConfiguredFormula(formula, variables, baseFairValue)
      : { value: baseFairValue, error: "", usedFallback: false };
    var fairValue = Math.max(0, numeric(formulaResult.value));
    var marginPrice = fairValue ? fairValue * (1 - margin / 100) : 0;
    var gap = currentPrice && fairValue ? ((fairValue / currentPrice) - 1) * 100 : 0;
    var status = valuationStatus(currentPrice, fairValue, marginPrice);
    var reasons = [];
    if (formulaResult.error) {
      reasons.push("적정가 공식 오류로 기본값을 사용했습니다: " + formulaResult.error);
    }
    if (!fairValue) {
      reasons.push("적정가 공식 결과가 0입니다. EPS, 목표 PER, 가중치를 확인하세요.");
    } else if (!currentPrice) {
      reasons.push("현재가가 필요합니다.");
    } else {
      reasons.push("적정가 " + formatPrice(fairValue, item.currency) + " 대비 " + signedPct(gap) + " 괴리입니다.");
      reasons.push("안전마진 " + margin + "% 기준 매수가 상한은 " + formatPrice(marginPrice, item.currency) + "입니다.");
    }
    return {
      symbol: symbol,
      name: item.name || symbol,
      source: item.source || "watchlist",
      sector: item.sector || "-",
      market: item.market || "",
      currency: item.currency || "",
      currentPrice: currentPrice,
      eps: assumption.eps || 0,
      targetPer: assumption.targetPer || 0,
      margin: margin,
      formula: formula,
      formulaError: formulaResult.error,
      fairValue: fairValue,
      marginPrice: marginPrice,
      gap: gap,
      status: status.label,
      tone: status.tone,
      rank: status.rank,
      reasons: reasons
    };
  }

  function buildValuationItems(snapshot) {
    var assumptions = parseValuationAssumptions();
    var weights = formulaWeights();
    var formula = formulaSetting("fairValueFormula");
    return instrumentItems(snapshot)
      .map(function (item) {
        return buildValuationForItem(item, assumptions, weights, formula);
      })
      .sort(function (a, b) {
        if (a.rank !== b.rank) return a.rank - b.rank;
        return a.gap - b.gap;
      });
  }

  function signalValue(raw, keys) {
    var value = 0;
    keys.some(function (key) {
      if (raw && raw[key] != null && raw[key] !== "") {
        value = numeric(raw[key]);
        return true;
      }
      return false;
    });
    return value;
  }

  function marketSignalForItem(item, signalMap) {
    var symbol = String(item.symbol || "").toUpperCase();
    var fromItem = item.marketSignal || item.tradeSignal || item.signal || {};
    var fromSettings = signalMap[symbol] || {};
    var merged = Object.assign({}, fromItem, fromSettings);
    return {
      symbol: symbol,
      tradeStrength: signalValue(merged, ["tradeStrength", "executionStrength"]),
      volumeRatio: signalValue(merged, ["volumeRatio", "relativeVolume", "volumeMultiple"]),
      buyVolume: signalValue(merged, ["buyVolume", "buyTradeVolume", "bidVolume"]),
      sellVolume: signalValue(merged, ["sellVolume", "sellTradeVolume", "askVolume"]),
      bidAskImbalance: signalValue(merged, ["bidAskImbalance", "orderbookImbalance", "imbalance"]),
      priceChangeRate: signalValue(merged, ["priceChangeRate", "changeRate", "changePercent"]),
      source: merged.source || (Object.keys(fromItem).length ? "toss" : "")
    };
  }

  function hasMarketSignal(signal) {
    return [
      "tradeStrength",
      "volumeRatio",
      "buyVolume",
      "sellVolume",
      "bidAskImbalance",
      "priceChangeRate"
    ].some(function (key) {
      return Number(signal[key] || 0) !== 0;
    });
  }

  function buyVolumeShare(signal) {
    var buy = Number(signal.buyVolume || 0);
    var sell = Number(signal.sellVolume || 0);
    var total = buy + sell;
    return total > 0 ? (buy / total) * 100 : 50;
  }

  function marketSignalScores(signal, context) {
    context = context || {};
    var valuation = context.valuation || {};
    var item = context.item || {};
    var weights = formulaWeights();
    var strength = signal.tradeStrength || 100;
    var volumeRatio = signal.volumeRatio || 1;
    var imbalance = signal.bidAskImbalance || 0;
    var priceChange = signal.priceChangeRate || 0;
    var buyShare = buyVolumeShare(signal);
    var valuationGap = Number(valuation.gap || 0);
    var expensivePenalty = valuationGap < 0 ? Math.min(18, Math.abs(valuationGap) / 2) : 0;
    var undervalueBonus = valuationGap > 0 ? Math.min(14, valuationGap / 3) : 0;
    var expensiveBonus = expensivePenalty;
    var variables = Object.assign({}, weights, {
      tradeStrength: strength,
      volumeRatio: volumeRatio,
      buyVolume: signal.buyVolume || 0,
      sellVolume: signal.sellVolume || 0,
      buyShare: buyShare,
      bidAskImbalance: imbalance,
      priceChangeRate: priceChange,
      currentPrice: currentPriceOf(item),
      fairValue: valuation.fairValue || 0,
      fairValueGap: valuationGap,
      valuationRank: valuation.rank || 0,
      expensivePenalty: expensivePenalty,
      expensiveBonus: expensiveBonus,
      undervalueBonus: undervalueBonus,
      profitLossRate: numeric(item.profitLossRate),
      marketValue: numeric(item.marketValue),
      holding: item.source === "watchlist" ? 0 : 1,
      watchlist: item.source === "watchlist" ? 1 : 0
    });
    var fallbackBuyScore = 50
      + (strength - 100) * 0.25
      + (volumeRatio - 1) * 12
      + (buyShare - 50) * 0.35
      + imbalance * 0.28
      + priceChange * 1.1;
    var fallbackSellScore = 50
      + (100 - strength) * 0.22
      + (volumeRatio - 1) * 8
      + (50 - buyShare) * 0.42
      - imbalance * 0.28
      - priceChange * 1.2;
    var buyResult = evaluateConfiguredFormula(formulaSetting("buyScoreFormula"), variables, fallbackBuyScore);
    var sellResult = evaluateConfiguredFormula(formulaSetting("sellScoreFormula"), variables, fallbackSellScore);
    var errors = [];
    if (buyResult.error) errors.push("매수 공식 오류: " + buyResult.error);
    if (sellResult.error) errors.push("매도 공식 오류: " + sellResult.error);
    return {
      buyScore: Math.round(clamp(buyResult.value, 0, 100)),
      sellScore: Math.round(clamp(sellResult.value, 0, 100)),
      buyShare: Math.round(clamp(buyShare, 0, 100)),
      errors: errors
    };
  }

  function instrumentItems(snapshot) {
    var toss = snapshot.toss || { positions: [], watchlist: [] };
    var seen = {};
    var items = [];
    (toss.positions || []).forEach(function (item) {
      if (item.source === "cash" || item.sector === "현금" || String(item.symbol || "").toUpperCase() === "CASH") return;
      var symbol = String(item.symbol || "").toUpperCase();
      if (!symbol || seen[symbol]) return;
      seen[symbol] = true;
      items.push(Object.assign({}, item, { source: item.source || "holding" }));
    });
    (toss.watchlist || []).forEach(function (item) {
      var symbol = String(item.symbol || "").toUpperCase();
      if (!symbol || seen[symbol]) return;
      seen[symbol] = true;
      items.push(Object.assign(clientKnownStockInfo(symbol), item, { source: "watchlist" }));
    });
    return items;
  }

  function tradeSignalDecision(item, scores, valuation, hasData) {
    var thresholds = decisionThresholds();
    if (!hasData) return { label: "수급 입력 필요", tone: "hold", priority: 9 };
    var holding = item.source !== "watchlist";
    var expensive = valuation && (valuation.tone === "danger" || valuation.tone === "caution");
    var cheap = valuation && valuation.tone === "watch";
    if (holding && scores.sellScore >= thresholds.sellTrim && expensive) return { label: "분할매도 검토", tone: "danger", priority: 1 };
    if (holding && scores.sellScore >= thresholds.riskReduce) return { label: "리스크 축소 검토", tone: "caution", priority: 2 };
    if (holding && scores.buyScore >= thresholds.strongHold && !expensive) return { label: "보유 강화 관찰", tone: "watch", priority: 3 };
    if (!holding && scores.buyScore >= thresholds.buyCandidate && (cheap || !expensive)) return { label: "매수 후보", tone: "watch", priority: 2 };
    if (!holding && scores.buyScore >= thresholds.chaseCaution) return { label: "추격 주의", tone: "caution", priority: 4 };
    if (scores.sellScore >= thresholds.sellWatch) return { label: holding ? "매도 기준 확인" : "진입 보류", tone: "caution", priority: 5 };
    return { label: "관망", tone: "hold", priority: 6 };
  }

  function tradeSignalReasons(signal, scores, valuation, hasData) {
    if (!hasData) {
      return ["설정에서 체결강도, 거래량 배율, 매수/매도 체결량을 입력하면 신호를 계산합니다."];
    }
    var reasons = [
      "체결강도: " + formatSignalNumber(signal.tradeStrength, "") + ", 거래량: " + formatSignalRatio(signal.volumeRatio) + "을 함께 봅니다.",
      "매수 체결 비중은 " + scores.buyShare + "%이고 호가 불균형은 " + formatSignalNumber(signal.bidAskImbalance, "%") + "입니다."
    ];
    if (valuation && valuation.status) {
      reasons.push("밸류에이션 분류는 " + valuation.status + "이며 수급 판단에 함께 반영됩니다.");
    } else {
      reasons.push("밸류에이션 가정이 없으면 수급 신호만으로 관찰 라벨을 만듭니다.");
    }
    (scores.errors || []).forEach(function (error) {
      reasons.push(error + " 기본 추천 공식을 대신 사용했습니다.");
    });
    return reasons;
  }

  function buildTradeSignalItems(snapshot) {
    var signalMap = parseMarketSignals();
    var valuationMap = {};
    buildValuationItems(snapshot).forEach(function (item) {
      valuationMap[item.symbol] = item;
    });
    return instrumentItems(snapshot).map(function (item) {
      var symbol = String(item.symbol || "").toUpperCase();
      var signal = marketSignalForItem(item, signalMap);
      var hasData = hasMarketSignal(signal);
      var valuation = valuationMap[symbol] || null;
      var scores = hasData ? marketSignalScores(signal, { item: item, valuation: valuation }) : { buyScore: 0, sellScore: 0, buyShare: 0, errors: [] };
      var decision = tradeSignalDecision(item, scores, valuation, hasData);
      return {
        symbol: symbol,
        name: item.name || symbol,
        source: item.source || "watchlist",
        sector: item.sector || "-",
        market: item.market || "",
        currency: item.currency || "",
        currentPrice: currentPriceOf(item),
        averagePrice: numeric(item.averagePrice),
        quantity: numeric(item.quantity),
        sellableQuantity: numeric(item.sellableQuantity || item.quantity),
        marketValue: numeric(item.marketValue),
        profitLoss: numeric(item.profitLoss),
        profitLossRate: numeric(item.profitLossRate),
        signal: signal,
        hasData: hasData,
        buyScore: scores.buyScore,
        sellScore: scores.sellScore,
        buyShare: scores.buyShare,
        valuation: valuation,
        action: decision.label,
        tone: decision.tone,
        priority: decision.priority,
        reasons: tradeSignalReasons(signal, scores, valuation, hasData),
        triggers: ["체결강도", "거래량", "매수/매도", "호가", "가격변화"]
      };
    }).sort(function (a, b) {
      if (a.priority !== b.priority) return a.priority - b.priority;
      return Math.max(b.buyScore, b.sellScore) - Math.max(a.buyScore, a.sellScore);
    });
  }

  function pressureLabel(score) {
    var value = Number(score || 0);
    if (value >= 72) return "높음";
    if (value >= 55) return "검토";
    if (value >= 38) return "관찰";
    return "낮음";
  }

  function labPriceDiff(value, currentPrice) {
    var price = Number(value || 0);
    var current = Number(currentPrice || 0);
    if (!price || !current) return "-";
    return signedPct(((price / current) - 1) * 100);
  }

  function labActionPrices(item) {
    var valuation = item.valuation || {};
    var current = Number(item.currentPrice || valuation.currentPrice || 0);
    var average = Number(item.averagePrice || 0);
    var reference = average || current;
    var fairValue = Number(valuation.fairValue || 0);
    var marginPrice = Number(valuation.marginPrice || 0);
    var buyLimit = marginPrice || (current ? current * 0.92 : 0);
    var stopPrice = reference ? reference * 0.92 : (buyLimit ? buyLimit * 0.92 : 0);
    var trimOne = reference ? reference * 1.12 : (fairValue ? fairValue * 0.9 : 0);
    var trimTwoBase = reference ? reference * 1.25 : 0;
    var trimTwo = fairValue ? Math.max(fairValue, trimTwoBase) : trimTwoBase;
    if (fairValue && fairValue > reference && trimOne > fairValue) trimOne = fairValue;
    if (fairValue && trimTwo < trimOne) trimTwo = trimOne;
    return [
      { label: "현재가", value: current, tone: "hold" },
      { label: item.source === "watchlist" ? "진입 기준" : "평단", value: reference, tone: "hold" },
      { label: "매수 상한", value: buyLimit, tone: "watch" },
      { label: "손절 기준", value: stopPrice, tone: "danger" },
      { label: "1차 매도", value: trimOne, tone: "caution" },
      { label: "2차 매도", value: trimTwo, tone: "danger" },
      { label: "적정가", value: fairValue, tone: "watch" }
    ];
  }

  function labScenarioNotes(item) {
    var valuation = item.valuation || {};
    var notes = [];
    if (!item.hasData) {
      notes.push("체결강도와 거래량 입력이 없어 수급 판단은 대기 상태입니다.");
    } else if (item.buyScore > item.sellScore + 10) {
      notes.push("매수 압력이 매도 압력보다 뚜렷해 추가 관찰 우선입니다.");
    } else if (item.sellScore > item.buyScore + 10) {
      notes.push("매도 압력이 우세해 분할매도 또는 리스크 축소 기준을 먼저 확인합니다.");
    } else {
      notes.push("매수·매도 압력이 비슷해 가격 기준 도달 여부를 먼저 봅니다.");
    }
    if (valuation.status) {
      notes.push("가치 분류는 " + valuation.status + "이고 적정가 괴리는 " + (valuation.fairValue ? signedPct(valuation.gap) : "-") + "입니다.");
    } else {
      notes.push("EPS와 목표 PER을 입력하면 적정가·안전마진 기준이 계산됩니다.");
    }
    if (item.source !== "watchlist" && item.averagePrice) {
      notes.push("평단 대비 현재 수익률은 " + signedPct(item.profitLossRate) + "입니다.");
    }
    return notes;
  }

  function serializeValuationAssumptions(map) {
    return Object.keys(map)
      .sort()
      .map(function (symbol) {
        var row = map[symbol] || {};
        return [
          symbol,
          Number(row.eps || 0),
          Number(row.targetPer || 0),
          Number(row.margin || 15)
        ].join(",");
      })
      .join("\n");
  }

  function updateValuationAssumption(symbol, field, value) {
    var key = String(symbol || "").toUpperCase();
    if (!key || ["eps", "targetPer", "margin"].indexOf(field) < 0) return;
    var map = parseValuationAssumptions();
    map[key] = Object.assign({ symbol: key, eps: 0, targetPer: 0, margin: 15 }, map[key] || {});
    map[key][field] = numeric(value);
    state.settings.valuationAssumptions = serializeValuationAssumptions(map);
    persistSettings();
    render();
  }

  function labDraftDefaults(item) {
    var valuation = item.valuation || {};
    var current = Number(item.currentPrice || 0);
    var targetReturn = current && valuation.fairValue ? ((valuation.fairValue / current) - 1) * 100 : 15;
    return {
      thesisScore: item.hasData ? item.buyScore : 50,
      riskScore: item.hasData ? item.sellScore : 50,
      confidenceScore: item.hasData ? Math.max(item.buyScore, item.sellScore) : 50,
      targetReturn: Math.round(clamp(targetReturn, -50, 200)),
      stopLoss: 8,
      positionSize: item.source === "watchlist" ? 10 : 100
    };
  }

  function labDraftForItem(item) {
    var symbol = String(item.symbol || "").toUpperCase();
    return Object.assign({}, labDraftDefaults(item), state.labDrafts[symbol] || {});
  }

  function updateLabDraft(symbol, field, value, shouldRender) {
    var key = String(symbol || "").toUpperCase();
    var allowed = ["thesisScore", "riskScore", "confidenceScore", "targetReturn", "stopLoss", "positionSize"];
    if (!key || allowed.indexOf(field) < 0) return;
    state.labDrafts[key] = Object.assign({}, state.labDrafts[key] || {});
    state.labDrafts[key][field] = numeric(value);
    state.labRecordSaved = false;
    state.labRecordError = "";
    persistLabDrafts();
    if (shouldRender !== false) render();
  }

  function labRecordsForSymbol(symbol) {
    var key = String(symbol || "").toUpperCase();
    return (state.labRecords || [])
      .filter(function (record) { return String(record.symbol || "").toUpperCase() === key; })
      .sort(function (a, b) { return String(b.createdAt || "").localeCompare(String(a.createdAt || "")); });
  }

  function latestLabRecordFor(symbol) {
    return labRecordsForSymbol(symbol)[0] || null;
  }

  function labRecordReturn(record, currentPrice) {
    var start = Number(record && record.priceAtRecord || 0);
    var current = Number(currentPrice || 0);
    if (!start || !current) return 0;
    return ((current / start) - 1) * 100;
  }

  function labRecordVersion(symbol) {
    return labRecordsForSymbol(symbol).length + 1;
  }

  function saveLabRecord(symbol) {
    var key = String(symbol || "").toUpperCase();
    if (!key || !state.snapshot) return;
    var item = buildTradeSignalItems(state.snapshot).filter(function (candidate) {
      return candidate.symbol === key;
    })[0];
    if (!item) {
      state.labRecordError = "저장할 종목을 찾지 못했습니다.";
      render();
      return;
    }
    var valuation = item.valuation || {};
    var draft = labDraftForItem(item);
    var model = customModelScores(item);
    var lines = labActionPrices(item);
    var lineMap = {};
    lines.forEach(function (line) {
      lineMap[line.label] = Number(line.value || 0);
    });
    var record = {
      id: key + "-" + Date.now(),
      schemaVersion: 1,
      version: labRecordVersion(key),
      createdAt: new Date().toISOString(),
      symbol: key,
      name: item.name || key,
      source: item.source || "",
      currency: item.currency || "",
      action: item.action || "",
      tone: item.tone || "hold",
      modelAction: model.action,
      modelTone: model.tone,
      priceAtRecord: Number(item.currentPrice || 0),
      averagePrice: Number(item.averagePrice || 0),
      fairValue: Number(valuation.fairValue || 0),
      marginPrice: Number(valuation.marginPrice || 0),
      fairValueGap: Number(valuation.gap || 0),
      buyScore: Number(item.buyScore || 0),
      sellScore: Number(item.sellScore || 0),
      modelBuyScore: Number(model.buyScore || 0),
      modelSellScore: Number(model.sellScore || 0),
      buyShare: Number(item.buyShare || 0),
      inputs: {
        thesisScore: Number(draft.thesisScore || 0),
        riskScore: Number(draft.riskScore || 0),
        confidenceScore: Number(draft.confidenceScore || 0),
        targetReturn: Number(draft.targetReturn || 0),
        stopLoss: Number(draft.stopLoss || 0),
        positionSize: Number(draft.positionSize || 0)
      },
      pricePlan: {
        buyLimit: Number(lineMap["매수 상한"] || 0),
        stopPrice: Number(lineMap["손절 기준"] || 0),
        trimOne: Number(lineMap["1차 매도"] || 0),
        trimTwo: Number(lineMap["2차 매도"] || 0)
      }
    };
    state.labRecords = (state.labRecords || []).concat(record);
    state.labRecordSaved = persistLabRecords();
    state.labRecordError = state.labRecordSaved ? "" : "실험 기록을 저장하지 못했습니다.";
    render();
  }

  function labLatestRecordMap() {
    var map = {};
    (state.labRecords || []).forEach(function (record) {
      var symbol = String(record.symbol || "").toUpperCase();
      if (!symbol) return;
      if (!map[symbol] || String(record.createdAt || "") > String(map[symbol].createdAt || "")) {
        map[symbol] = record;
      }
    });
    return map;
  }

  function labStatsForItems(items) {
    var latestMap = labLatestRecordMap();
    var latestRecords = Object.keys(latestMap).map(function (symbol) { return latestMap[symbol]; });
    var itemMap = {};
    items.forEach(function (item) {
      itemMap[item.symbol] = item;
    });
    var returns = latestRecords
      .map(function (record) {
        var item = itemMap[record.symbol] || {};
        return labRecordReturn(record, item.currentPrice);
      })
      .filter(function (value) { return Number.isFinite(value); });
    var scoreTotal = latestRecords.reduce(function (sum, record) {
      return sum + Number(record.inputs && record.inputs.thesisScore || 0);
    }, 0);
    var riskTotal = latestRecords.reduce(function (sum, record) {
      return sum + Number(record.inputs && record.inputs.riskScore || 0);
    }, 0);
    var returnTotal = returns.reduce(function (sum, value) { return sum + value; }, 0);
    var winners = returns.filter(function (value) { return value > 0; }).length;
    return {
      recordCount: (state.labRecords || []).length,
      symbolCount: latestRecords.length,
      averageReturn: returns.length ? returnTotal / returns.length : 0,
      winRate: returns.length ? (winners / returns.length) * 100 : 0,
      averageScore: latestRecords.length ? scoreTotal / latestRecords.length : 0,
      averageRisk: latestRecords.length ? riskTotal / latestRecords.length : 0
    };
  }

  function modelFormulaVariables(item) {
    var valuation = item.valuation || {};
    var draft = labDraftForItem(item);
    var weights = formulaWeights();
    var valuationGap = Number(valuation.gap || 0);
    var expensivePenalty = valuationGap < 0 ? Math.min(18, Math.abs(valuationGap) / 2) : 0;
    var undervalueBonus = valuationGap > 0 ? Math.min(14, valuationGap / 3) : 0;
    return Object.assign({}, weights, {
      buyScore: Number(item.buyScore || 0),
      sellScore: Number(item.sellScore || 0),
      systemBuyScore: Number(item.buyScore || 0),
      systemSellScore: Number(item.sellScore || 0),
      buyShare: Number(item.buyShare || 0),
      currentPrice: Number(item.currentPrice || 0),
      averagePrice: Number(item.averagePrice || 0),
      fairValue: Number(valuation.fairValue || 0),
      fairValueGap: valuationGap,
      expensivePenalty: expensivePenalty,
      expensiveBonus: expensivePenalty,
      undervalueBonus: undervalueBonus,
      profitLossRate: Number(item.profitLossRate || 0),
      thesisScore: Number(draft.thesisScore || 0),
      riskScore: Number(draft.riskScore || 0),
      confidenceScore: Number(draft.confidenceScore || 0),
      targetReturn: Number(draft.targetReturn || 0),
      stopLoss: Number(draft.stopLoss || 0),
      positionSize: Number(draft.positionSize || 0),
      holding: item.source === "watchlist" ? 0 : 1,
      watchlist: item.source === "watchlist" ? 1 : 0
    });
  }

  function customModelDecision(item, buyScore, sellScore) {
    var thresholds = modelDecisionThresholds();
    var holding = item.source !== "watchlist";
    if (holding && sellScore >= thresholds.modelSell) return { label: "내 모델 분할매도", tone: "danger", rank: 1 };
    if (holding && sellScore >= thresholds.modelReduce) return { label: "내 모델 리스크 축소", tone: "caution", rank: 2 };
    if (buyScore >= thresholds.modelBuy && !holding) return { label: "내 모델 매수 후보", tone: "watch", rank: 2 };
    if (buyScore >= thresholds.modelAdd && holding) return { label: "내 모델 보유 강화", tone: "watch", rank: 3 };
    if (Math.max(buyScore, sellScore) >= thresholds.modelHold) return { label: "내 모델 관찰", tone: "hold", rank: 4 };
    return { label: "내 모델 관망", tone: "hold", rank: 5 };
  }

  function customModelScores(item) {
    var variables = modelFormulaVariables(item);
    var fallbackBuy = variables.buyScore * 0.35
      + variables.thesisScore * Number(variables.thesisWeight || 0.25)
      + variables.confidenceScore * Number(variables.confidenceWeight || 0.15)
      + Math.max(0, variables.targetReturn) * 0.15
      + variables.undervalueBonus * Number(variables.valuationWeight || 1)
      - variables.riskScore * Number(variables.riskControlWeight || 0.35);
    var fallbackSell = variables.sellScore * 0.35
      + variables.riskScore * Number(variables.riskControlWeight || 0.35)
      + variables.expensivePenalty * Number(variables.valuationWeight || 1)
      + Math.max(0, -variables.targetReturn) * 0.2
      - variables.thesisScore * 0.1;
    var buyResult = evaluateConfiguredFormula(formulaSetting("customBuyModelFormula"), variables, fallbackBuy);
    var sellResult = evaluateConfiguredFormula(formulaSetting("customSellModelFormula"), variables, fallbackSell);
    var buy = Math.round(clamp(buyResult.value, 0, 100));
    var sell = Math.round(clamp(sellResult.value, 0, 100));
    var decision = customModelDecision(item, buy, sell);
    var errors = [];
    if (buyResult.error) errors.push("내 모델 매수 공식 오류: " + buyResult.error);
    if (sellResult.error) errors.push("내 모델 매도 공식 오류: " + sellResult.error);
    return {
      buyScore: buy,
      sellScore: sell,
      action: decision.label,
      tone: decision.tone,
      rank: decision.rank,
      errors: errors,
      variables: variables
    };
  }

  function modelStatsForItems(items) {
    var scored = items.map(function (item) {
      return customModelScores(item);
    });
    var buyAverage = scored.length ? scored.reduce(function (sum, score) { return sum + score.buyScore; }, 0) / scored.length : 0;
    var sellAverage = scored.length ? scored.reduce(function (sum, score) { return sum + score.sellScore; }, 0) / scored.length : 0;
    var actionCount = scored.filter(function (score) {
      return score.tone === "danger" || score.tone === "caution" || score.tone === "watch";
    }).length;
    var stats = labStatsForItems(items);
    return {
      buyAverage: buyAverage,
      sellAverage: sellAverage,
      actionCount: actionCount,
      recordCount: stats.recordCount,
      symbolCount: stats.symbolCount,
      averageReturn: stats.averageReturn,
      winRate: stats.winRate
    };
  }

  function currentModelSnapshot(items) {
    return {
      name: settingValue("modelName") || defaultSettings.modelName,
      hypothesis: settingValue("modelHypothesis") || defaultSettings.modelHypothesis,
      fairValueFormula: formulaSetting("fairValueFormula"),
      buyScoreFormula: formulaSetting("buyScoreFormula"),
      sellScoreFormula: formulaSetting("sellScoreFormula"),
      customBuyModelFormula: formulaSetting("customBuyModelFormula"),
      customSellModelFormula: formulaSetting("customSellModelFormula"),
      formulaWeights: formulaWeights(),
      decisionThresholds: decisionThresholds(),
      modelDecisionThresholds: modelDecisionThresholds(),
      stats: modelStatsForItems(items || [])
    };
  }

  function saveModelVersion() {
    if (!state.snapshot) return;
    var items = buildTradeSignalItems(state.snapshot);
    var snapshot = currentModelSnapshot(items);
    var version = {
      id: "model-" + Date.now(),
      schemaVersion: 1,
      version: (state.modelVersions || []).length + 1,
      createdAt: new Date().toISOString(),
      model: snapshot
    };
    state.modelVersions = (state.modelVersions || []).concat(version);
    state.modelSaved = persistModelVersions();
    state.modelError = state.modelSaved ? "" : "모델 버전을 저장하지 못했습니다.";
    render();
  }

  function downloadText(filename, content, mimeType) {
    try {
      var blob = new Blob([content], { type: mimeType || "text/plain;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (error) {
      state.modelError = "파일을 만들지 못했습니다: " + (error.message || "알 수 없는 오류");
      render();
    }
  }

  function csvCell(value) {
    var text = String(value == null ? "" : value);
    return '"' + text.replace(/"/g, '""') + '"';
  }

  function exportLabRecords(format) {
    var records = state.labRecords || [];
    if (format === "csv") {
      var headers = ["version", "createdAt", "symbol", "name", "action", "priceAtRecord", "modelBuyScore", "modelSellScore", "thesisScore", "riskScore", "targetReturn", "stopLoss"];
      var rows = records.map(function (record) {
        var inputs = record.inputs || {};
        return [
          record.version,
          record.createdAt,
          record.symbol,
          record.name,
          record.action,
          record.priceAtRecord,
          record.modelBuyScore,
          record.modelSellScore,
          inputs.thesisScore,
          inputs.riskScore,
          inputs.targetReturn,
          inputs.stopLoss
        ].map(csvCell).join(",");
      });
      downloadText("lab-records.csv", headers.map(csvCell).join(",") + "\n" + rows.join("\n"), "text/csv;charset=utf-8");
      return;
    }
    downloadText("lab-records.json", JSON.stringify(records, null, 2), "application/json;charset=utf-8");
  }

  function exportModelVersions() {
    downloadText("model-versions.json", JSON.stringify(state.modelVersions || [], null, 2), "application/json;charset=utf-8");
  }

  function load() {
    state.loading = !state.snapshot;
    state.refreshing = Boolean(state.snapshot);
    state.error = "";
    render();

    var loadPromise = state.dataMode === "mock" && isStaticPreviewHost()
      ? Promise.resolve(staticMockSnapshot())
      : requestJson(tossLensPath()).catch(function (error) {
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
    if (state.activeTab === "feed" && !state.feed && !state.feedLoading) {
      loadFeed(false);
    }
  }

  function renderLoading() {
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div>',
      '<p class="eyebrow">Toss Lens</p>',
      '<h1>토스 계좌 판단판을 불러오는 중</h1>',
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
      '<p class="eyebrow">Toss Lens</p>',
      '<h1>토스 계좌 판단판을 만들지 못했습니다</h1>',
      '<p class="subtle">' + escapeHtml(state.error || "알 수 없는 오류") + "</p>",
      '</div>',
      '<button class="icon-button" data-action="refresh" title="새로고침">↻</button>',
      '</section>',
      '</main>'
    ].join("");
  }

  function renderDashboard(snapshot) {
    var toss = snapshot.toss || { mode: "demo" };
    var modeLabel = snapshot.mock ? "Mock" : (toss.mode === "live" ? "Toss live" : "Demo");
    var modeClass = snapshot.mock ? "mock" : (toss.mode === "live" ? "live" : "demo");
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div>',
      '<p class="eyebrow">Toss Lens</p>',
      '<h1>토스 계좌 기준 보유/관심 점검</h1>',
      '<p class="subtle">' + escapeHtml(snapshot.headline) + " · " + escapeHtml(formatClock(snapshot.generatedAt)) + "</p>",
      '</div>',
      '<div class="toolbar">',
      '<div class="mode-toggle" role="group" aria-label="데이터 모드">',
      '<button class="' + (state.dataMode === "live" ? "active" : "") + '" data-mode="live">실데이터</button>',
      '<button class="' + (state.dataMode === "mock" ? "active" : "") + '" data-mode="mock">Mock</button>',
      '</div>',
      '<span class="status-pill ' + modeClass + '">' + escapeHtml(modeLabel) + "</span>",
      '<button class="icon-button" data-action="open-settings" title="설정" aria-label="설정">⚙</button>',
      '<button class="icon-button" data-action="refresh" title="새로고침">' + (state.refreshing ? "…" : "↻") + "</button>",
      '</div>',
      '</section>',
      renderTabs(),
      renderActiveTab(snapshot),
      state.settingsOpen ? renderSettingsOverlay() : '',
      '</main>'
    ].join("");
  }

  function renderTabs() {
    return [
      '<nav class="tab-bar" aria-label="앱 탭" style="--tab-count:' + tabs.length + '">',
      tabs.map(function (tab) {
        return '<button class="' + (state.activeTab === tab.id ? "active" : "") + '" data-tab="' + escapeHtml(tab.id) + '">' + escapeHtml(tab.label) + '</button>';
      }).join(""),
      '</nav>'
    ].join("");
  }

  function renderActiveTab(snapshot) {
    if (state.activeTab === "lab") {
      return [
        '<section class="content-grid">',
        renderLabPanel(snapshot, true),
        renderLabMethodPanel(),
        '</section>'
      ].join("");
    }
    if (state.activeTab === "model") {
      return [
        '<section class="content-grid">',
        renderModelStudioPanel(snapshot),
        renderModelVersionPanel(snapshot),
        renderModelPreviewPanel(snapshot),
        '</section>'
      ].join("");
    }
    if (state.activeTab === "holdings") {
      return [
        '<section class="content-grid">',
        renderPortfolioPanel(snapshot),
        renderHoldingsPanel(snapshot),
        '</section>'
      ].join("");
    }
    if (state.activeTab === "feed") {
      return [
        '<section class="content-grid">',
        renderFeedOverviewPanel(),
        renderFeedListPanel(),
        renderFeedChannelPanel(),
        '</section>'
      ].join("");
    }
    if (state.activeTab === "watchlist") {
      return [
        '<section class="content-grid">',
        renderWatchlistPanel(snapshot),
        renderApiScopePanel(),
        '</section>'
      ].join("");
    }
    return [
      '<section class="hero-grid">',
      renderScorePanel(snapshot),
      renderSourcePanel(snapshot),
      '</section>',
      '<section class="content-grid">',
      renderLabPanel(snapshot, false),
      renderDecisionPanel(snapshot),
      renderChecklistPanel(snapshot),
      '</section>'
    ].join("");
  }

  function renderScorePanel(snapshot) {
    var decision = snapshot.tossDecision || {};
    var score = Number(snapshot.exitScore || decision.overallPressure || 0);
    return [
      '<article class="panel score-panel">',
      '<div class="score-wrap">',
      '<div class="score-ring" style="--score:' + score + '">',
      '<span>' + escapeHtml(score) + '</span>',
      '</div>',
      '<div>',
      '<p class="label">Toss Check</p>',
      '<h2>매도 검토 강도 ' + escapeHtml(pressureLabel(score)) + '</h2>',
      '<p class="subtle">토스 조회값으로 설명 가능한 기준만 사용합니다.</p>',
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
    var decision = snapshot.tossDecision || {};
    var account = toss.account || {};
    return [
      '<aside class="panel source-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">데이터 상태</p>',
      '<h2>토스 API 범위</h2>',
      '</div>',
      '</div>',
      '<div class="source-stack">',
      '<div class="source-row"><span>표시 모드</span><strong>' + escapeHtml(snapshot.mock ? "Mock 데이터" : "기본 데이터") + '</strong></div>',
      '<div class="source-row"><span>토스</span><strong>' + escapeHtml(toss.status || "-") + '</strong></div>',
      '<div class="source-row"><span>계좌</span><strong>' + escapeHtml(account.displayNumber || "-") + '</strong></div>',
      '<div class="source-row"><span>주문 가능 금액</span><strong>' + escapeHtml(formatCurrency(account.orderableAmount || 0, account.currency || "KRW")) + '</strong></div>',
      '<div class="source-row"><span>보유/관심</span><strong>' + escapeHtml(decision.holdingCount || 0) + ' / ' + escapeHtml(decision.watchCount || 0) + '</strong></div>',
      '<div class="source-row"><span>수급 신호</span><strong>설정/토스 시장 데이터</strong></div>',
      '<div class="source-row"><span>뉴스·X</span><strong>매매 점수 제외</strong></div>',
      '</div>',
      '</aside>'
    ].join("");
  }

  function renderDecisionPanel(snapshot) {
    var decision = snapshot.tossDecision || { items: [], rules: [] };
    var items = decision.items || [];
    return [
      '<article class="panel exit-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Account Priority</p>',
      '<h2>계좌 데이터 기준 우선 점검 종목</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(items.length) + '</span>',
      '</div>',
      '<div class="exit-list">',
      items.map(renderDecisionRow).join(""),
      '</div>',
      '<div class="rule-strip">',
      (decision.rules || []).map(function (rule) {
        return '<span>' + escapeHtml(rule) + '</span>';
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderDecisionRow(item) {
    return [
      '<div class="exit-row">',
      '<div class="exit-main">',
      '<div class="exit-title">',
      '<div>',
      '<h3>' + escapeHtml(item.name) + '</h3>',
      '<p class="subtle">' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.sector || "-") + '</p>',
      '</div>',
      '<div class="exit-badges">',
      '<span class="source-chip ' + escapeHtml(item.source) + '">' + escapeHtml(sourceLabel(item.source)) + '</span>',
      '<span class="decision-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.decision) + '</span>',
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
      '</div>',
      '<div class="exit-score">',
      '<strong>' + escapeHtml(item.exitPressure || 0) + '</strong>',
      '<span>검토</span>',
      item.source === "holding" ? '<em>' + escapeHtml(signedPct(item.profitLossRate)) + '</em>' : '<em>watch</em>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderLabPanel(snapshot, full) {
    var items = buildTradeSignalItems(snapshot);
    var visible = full ? items : items.slice(0, 3);
    var actionCount = items.filter(function (item) {
      return item.tone === "danger" || item.tone === "caution" || item.tone === "watch";
    }).length;
    return [
      '<article class="panel lab-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Position Lab</p>',
      '<h2>매수·보유·매도 타이밍 실험실</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(actionCount) + '</span>',
      '</div>',
      renderLabStats(items),
      '<div class="lab-list">',
      visible.length ? visible.map(renderLabRow).join("") : '<p class="subtle">보유 또는 관심 종목을 찾지 못했습니다.</p>',
      '</div>',
      '<div class="rule-strip">',
      '<span>주문 실행이 아니라 매매 타이밍을 찾기 위한 읽기 전용 계산판입니다.</span>',
      full ? '<span>EPS, 목표 PER, 안전마진을 바꾸면 적정가와 가격 기준선이 다시 계산됩니다.</span>' : '<span>전체 종목은 실험실 탭에서 봅니다.</span>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderLabStats(items) {
    var stats = labStatsForItems(items);
    return [
      '<div class="lab-stats-grid">',
      renderLabStat("저장 버전", stats.recordCount, "개"),
      renderLabStat("기록 종목", stats.symbolCount, "종목"),
      renderLabStat("평균 성과", signedPct(stats.averageReturn), ""),
      renderLabStat("승률", pct(stats.winRate), ""),
      renderLabStat("평균 내 점수", Math.round(stats.averageScore), "점"),
      renderLabStat("평균 리스크", Math.round(stats.averageRisk), "점"),
      '</div>',
      state.labRecordError ? '<div class="lab-message danger">' + escapeHtml(state.labRecordError) + '</div>' : '',
      state.labRecordSaved ? '<div class="lab-message">실험 기록을 저장했습니다.</div>' : ''
    ].join("");
  }

  function renderLabStat(label, value, suffix) {
    return [
      '<span>',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(value) + escapeHtml(suffix || "") + '</strong>',
      '</span>'
    ].join("");
  }

  function renderLabRow(item) {
    var valuation = item.valuation || {};
    var signal = item.signal || {};
    var lines = labActionPrices(item);
    var notes = labScenarioNotes(item);
    var draft = labDraftForItem(item);
    var latest = latestLabRecordFor(item.symbol);
    var versionCount = labRecordsForSymbol(item.symbol).length;
    var model = customModelScores(item);
    return [
      '<div class="lab-row">',
      '<div class="lab-row-head">',
      '<div>',
      '<strong>' + escapeHtml(item.name) + '</strong>',
      '<span>' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.sector || "-") + ' · ' + escapeHtml(sourceLabel(item.source)) + '</span>',
      '</div>',
      '<div class="exit-badges">',
      '<span class="source-chip ' + escapeHtml(item.source) + '">' + escapeHtml(sourceLabel(item.source)) + '</span>',
      '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.action) + '</span>',
      '</div>',
      '</div>',
      '<div class="lab-status-grid">',
      '<span>현재가 <strong>' + escapeHtml(item.currentPrice ? formatPrice(item.currentPrice, item.currency) : "-") + '</strong></span>',
      '<span>' + escapeHtml(item.source === "watchlist" ? "관심 기준" : "평단") + ' <strong>' + escapeHtml((item.averagePrice || item.currentPrice) ? formatPrice(item.averagePrice || item.currentPrice, item.currency) : "-") + '</strong></span>',
      '<span>수량 <strong>' + escapeHtml(item.source === "watchlist" ? "-" : (item.quantity || "-")) + '</strong></span>',
      '<span>손익률 <strong>' + escapeHtml(item.source === "watchlist" ? "-" : signedPct(item.profitLossRate)) + '</strong></span>',
      '<span>매수 점수 <strong class="buy">' + escapeHtml(item.hasData ? item.buyScore : "-") + '</strong></span>',
      '<span>매도 점수 <strong class="sell">' + escapeHtml(item.hasData ? item.sellScore : "-") + '</strong></span>',
      '</div>',
      '<div class="lab-model-grid">',
      '<span>내 모델 매수 <strong class="buy">' + escapeHtml(model.buyScore) + '</strong></span>',
      '<span>내 모델 매도 <strong class="sell">' + escapeHtml(model.sellScore) + '</strong></span>',
      '<span>내 모델 판단 <strong>' + escapeHtml(model.action) + '</strong></span>',
      '<span>공식 상태 <strong>' + escapeHtml(model.errors.length ? "확인 필요" : "정상") + '</strong></span>',
      '</div>',
      '<div class="lab-body-grid">',
      '<div class="lab-price-ladder">',
      lines.map(function (line) { return renderLabPriceLine(line, item); }).join(""),
      '</div>',
      '<div class="lab-side">',
      '<div class="lab-control-grid">',
      renderLabControl(item.symbol, "eps", "EPS", valuation.eps || 0, "1"),
      renderLabControl(item.symbol, "targetPer", "목표 PER", valuation.targetPer || 0, "0.1"),
      renderLabControl(item.symbol, "margin", "안전마진 %", valuation.margin || 15, "1"),
      '</div>',
      '<div class="lab-draft-grid">',
      renderLabDraftControl(item.symbol, "thesisScore", "내 매수 점수", draft.thesisScore, "1"),
      renderLabDraftControl(item.symbol, "riskScore", "리스크 점수", draft.riskScore, "1"),
      renderLabDraftControl(item.symbol, "confidenceScore", "확신 점수", draft.confidenceScore, "1"),
      renderLabDraftControl(item.symbol, "targetReturn", "목표 수익률 %", draft.targetReturn, "0.1"),
      renderLabDraftControl(item.symbol, "stopLoss", "허용 손절 %", draft.stopLoss, "0.1"),
      renderLabDraftControl(item.symbol, "positionSize", "비중 계획 %", draft.positionSize, "1"),
      '</div>',
      '<div class="signal-metric-grid compact">',
      '<span>체결강도 <strong>' + escapeHtml(formatSignalNumber(signal.tradeStrength, "")) + '</strong></span>',
      '<span>거래량 <strong>' + escapeHtml(formatSignalRatio(signal.volumeRatio)) + '</strong></span>',
      '<span>매수비중 <strong>' + escapeHtml(item.hasData ? item.buyShare + "%" : "-") + '</strong></span>',
      '<span>호가 <strong>' + escapeHtml(formatSignalNumber(signal.bidAskImbalance, "%")) + '</strong></span>',
      '</div>',
      renderLabVersionBar(item, latest, versionCount),
      '<div class="exit-reasons">',
      notes.concat(model.errors).map(function (note) { return '<p>' + escapeHtml(note) + '</p>'; }).join(""),
      '</div>',
      '</div>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderLabPriceLine(line, item) {
    var value = Number(line.value || 0);
    return [
      '<div class="lab-price-line ' + escapeHtml(line.tone || "hold") + '">',
      '<span>' + escapeHtml(line.label) + '</span>',
      '<strong>' + escapeHtml(value ? formatPrice(value, item.currency) : "-") + '</strong>',
      '<em>' + escapeHtml(labPriceDiff(value, item.currentPrice)) + '</em>',
      '</div>'
    ].join("");
  }

  function renderLabControl(symbol, field, label, value, step) {
    return [
      '<label class="lab-control">',
      '<span>' + escapeHtml(label) + '</span>',
      '<input type="number" step="' + escapeHtml(step || "1") + '" value="' + escapeHtml(value || "") + '" data-lab-symbol="' + escapeHtml(symbol) + '" data-lab-assumption="' + escapeHtml(field) + '" />',
      '</label>'
    ].join("");
  }

  function renderLabDraftControl(symbol, field, label, value, step) {
    return [
      '<label class="lab-control lab-draft-control">',
      '<span>' + escapeHtml(label) + '</span>',
      '<input type="number" step="' + escapeHtml(step || "1") + '" value="' + escapeHtml(value) + '" data-lab-symbol="' + escapeHtml(symbol) + '" data-lab-draft="' + escapeHtml(field) + '" />',
      '</label>'
    ].join("");
  }

  function renderLabVersionBar(item, latest, versionCount) {
    var returnText = latest ? signedPct(labRecordReturn(latest, item.currentPrice)) : "-";
    return [
      '<div class="lab-version-bar">',
      '<div>',
      '<strong>' + escapeHtml(versionCount ? "v" + versionCount : "기록 없음") + '</strong>',
      '<span>' + escapeHtml(latest ? "최근 저장 " + formatClock(latest.createdAt) + " · 이후 성과 " + returnText : "점수와 수치를 입력한 뒤 버전을 저장하세요.") + '</span>',
      '</div>',
      '<button class="text-button primary compact" data-lab-save="' + escapeHtml(item.symbol) + '">버전 저장</button>',
      '</div>',
      latest ? renderLabLatestRecord(latest, item) : ''
    ].join("");
  }

  function renderLabLatestRecord(record, item) {
    var inputs = record.inputs || {};
    return [
      '<div class="lab-record-grid">',
      '<span>저장가 <strong>' + escapeHtml(record.priceAtRecord ? formatPrice(record.priceAtRecord, record.currency) : "-") + '</strong></span>',
      '<span>현재 성과 <strong>' + escapeHtml(signedPct(labRecordReturn(record, item.currentPrice))) + '</strong></span>',
      '<span>내 점수 <strong>' + escapeHtml(Math.round(inputs.thesisScore || 0)) + '</strong></span>',
      '<span>리스크 <strong>' + escapeHtml(Math.round(inputs.riskScore || 0)) + '</strong></span>',
      '<span>모델 매수 <strong>' + escapeHtml(record.modelBuyScore == null ? "-" : Math.round(record.modelBuyScore)) + '</strong></span>',
      '<span>모델 매도 <strong>' + escapeHtml(record.modelSellScore == null ? "-" : Math.round(record.modelSellScore)) + '</strong></span>',
      '<span>목표 <strong>' + escapeHtml(signedPct(inputs.targetReturn || 0)) + '</strong></span>',
      '<span>손절 <strong>' + escapeHtml("-" + Math.abs(Number(inputs.stopLoss || 0)).toFixed(1) + "%") + '</strong></span>',
      '</div>'
    ].join("");
  }

  function renderLabMethodPanel() {
    var rows = [
      ["핵심 질문", "지금 추가매수, 보유, 분할매도, 손절 기준 중 어디에 가까운지 계산"],
      ["가격 기준선", "현재가, 평단, 안전마진 매수가, 손절선, 1·2차 매도가를 한 번에 비교"],
      ["수급 판단", "체결강도, 거래량, 매수 비중, 호가 불균형을 매수·매도 점수로 분리"],
      ["가치 판단", "EPS, 목표 PER, 안전마진과 사용자 공식을 이용해 적정가를 계산"],
      ["입력 기록", "내 매수 점수, 리스크, 확신, 목표 수익률, 손절률, 비중 계획을 종목별로 저장"],
      ["성과 분석", "버전 저장 시점의 가격과 현재가를 비교해 평균 성과와 승률을 계산"]
    ];
    var variables = [
      ["eps", "주당순이익"],
      ["targetPer", "목표 PER"],
      ["margin", "안전마진"],
      ["tradeStrength", "체결강도"],
      ["volumeRatio", "거래량 배율"],
      ["buyShare", "매수 체결 비중"],
      ["fairValueGap", "적정가 괴리"],
      ["profitLossRate", "평단 대비 수익률"],
      ["thesisScore", "사용자 매수 점수"],
      ["riskScore", "사용자 리스크 점수"]
    ];
    return [
      '<article class="panel lab-method-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Lab Method</p>',
      '<h2>실험실 계산 기준</h2>',
      '</div>',
      '</div>',
      '<div class="source-stack">',
      rows.map(function (row) {
        return '<div class="source-row"><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>';
      }).join(""),
      '</div>',
      '<div class="formula-stack">',
      renderFormulaBlock("적정가 공식", formulaSetting("fairValueFormula")),
      renderFormulaBlock("매수 점수 공식", formulaSetting("buyScoreFormula")),
      renderFormulaBlock("매도 점수 공식", formulaSetting("sellScoreFormula")),
      '</div>',
      renderVariableGuide(variables),
      '<div class="rule-strip"><span>가격 기준선은 참고용입니다. 실제 주문 API 연결은 별도 승인 단계에서만 다룹니다.</span></div>',
      '</article>'
    ].join("");
  }

  function renderModelStudioPanel(snapshot) {
    var items = buildTradeSignalItems(snapshot);
    var stats = modelStatsForItems(items);
    var weights = formulaWeights();
    var thresholds = modelDecisionThresholds();
    return [
      '<article class="panel model-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Model Studio</p>',
      '<h2>나만의 매수·매도 모델</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(Math.round(stats.buyAverage)) + '</span>',
      '</div>',
      '<div class="lab-stats-grid model-stats-grid">',
      renderLabStat("모델 매수 평균", Math.round(stats.buyAverage), "점"),
      renderLabStat("모델 매도 평균", Math.round(stats.sellAverage), "점"),
      renderLabStat("모델 신호", stats.actionCount, "개"),
      renderLabStat("실험 기록", stats.recordCount, "개"),
      renderLabStat("평균 성과", signedPct(stats.averageReturn), ""),
      renderLabStat("승률", pct(stats.winRate), ""),
      '</div>',
      state.modelError ? '<div class="lab-message danger">' + escapeHtml(state.modelError) + '</div>' : '',
      state.modelSaved ? '<div class="lab-message">모델 버전을 저장했습니다.</div>' : '',
      '<div class="model-editor">',
      '<div class="settings-grid">',
      renderModelSettingField("modelName", "모델 이름", "text", "나의 모델"),
      renderModelFormulaField("modelHypothesis", "모델 가설", "어떤 조건에서 매수/매도할지"),
      renderModelFormulaField("customBuyModelFormula", "내 모델 매수 공식", "buyScore * 0.35 + thesisScore * thesisWeight"),
      renderModelFormulaField("customSellModelFormula", "내 모델 매도 공식", "sellScore * 0.35 + riskScore * riskControlWeight"),
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>가중치</strong><span>공식에서 바로 사용할 수 있는 변수입니다.</span></div></div>',
      renderNumberSettingGrid("formulaWeights", weights, ["growthWeight", "qualityWeight", "riskWeight", "flowWeight", "valuationWeight", "thesisWeight", "confidenceWeight", "riskControlWeight"]),
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>모델 판단 기준</strong><span>내 모델 점수가 이 기준을 넘으면 라벨이 바뀝니다.</span></div></div>',
      renderNumberSettingGrid("modelDecisionThresholds", thresholds, ["modelBuy", "modelAdd", "modelSell", "modelReduce", "modelHold"]),
      '</div>',
      renderVariableGuide(modelVariableGuide()),
      '<div class="rule-strip"><span>공식은 +, -, *, /, 괄호와 min, max, abs, round, sqrt, pow, clamp 함수를 지원합니다.</span></div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderModelVersionPanel(snapshot) {
    var versions = (state.modelVersions || []).slice().sort(function (a, b) {
      return String(b.createdAt || "").localeCompare(String(a.createdAt || ""));
    });
    return [
      '<article class="panel model-version-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Model Versions</p>',
      '<h2>모델 버전과 데이터</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(versions.length) + '</span>',
      '</div>',
      '<div class="settings-body">',
      '<div class="settings-note">',
      '<strong>버전 저장</strong>',
      '<p>현재 모델 이름, 가설, 공식, 가중치, 기준값, 현재 성과 통계를 하나의 버전으로 저장합니다.</p>',
      '</div>',
      '<div class="settings-actions">',
      '<button class="text-button primary" data-action="save-model-version">모델 버전 저장</button>',
      '<button class="text-button" data-export-lab="json">실험 JSON</button>',
      '<button class="text-button" data-export-lab="csv">실험 CSV</button>',
      '<button class="text-button" data-action="export-model-versions">모델 JSON</button>',
      '</div>',
      '<div class="model-version-list">',
      versions.length ? versions.slice(0, 6).map(renderModelVersionRow).join("") : '<p class="subtle">아직 저장한 모델 버전이 없습니다.</p>',
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderModelVersionRow(version) {
    var model = version.model || {};
    var stats = model.stats || {};
    return [
      '<div class="model-version-row">',
      '<div>',
      '<strong>v' + escapeHtml(version.version || "-") + ' · ' + escapeHtml(model.name || "-") + '</strong>',
      '<span>' + escapeHtml(formatClock(version.createdAt)) + ' · 평균 성과 ' + escapeHtml(signedPct(stats.averageReturn || 0)) + ' · 승률 ' + escapeHtml(pct(stats.winRate || 0)) + '</span>',
      '</div>',
      '<span class="tone-chip hold">' + escapeHtml(Math.round(stats.buyAverage || 0)) + ' / ' + escapeHtml(Math.round(stats.sellAverage || 0)) + '</span>',
      '</div>'
    ].join("");
  }

  function renderModelPreviewPanel(snapshot) {
    var items = buildTradeSignalItems(snapshot).map(function (item) {
      return Object.assign({}, item, { model: customModelScores(item) });
    }).sort(function (a, b) {
      if (a.model.rank !== b.model.rank) return a.model.rank - b.model.rank;
      return Math.max(b.model.buyScore, b.model.sellScore) - Math.max(a.model.buyScore, a.model.sellScore);
    });
    return [
      '<article class="panel model-preview-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Model Preview</p>',
      '<h2>현재 종목 적용 결과</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(items.length) + '</span>',
      '</div>',
      '<div class="signal-list">',
      items.length ? items.map(renderModelPreviewRow).join("") : '<p class="subtle">평가할 종목이 없습니다.</p>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderModelPreviewRow(item) {
    var model = item.model || customModelScores(item);
    return [
      '<div class="signal-row model-preview-row">',
      '<div class="signal-main">',
      '<div class="flow-title">',
      '<div>',
      '<strong>' + escapeHtml(item.name) + '</strong>',
      '<span>' + escapeHtml(item.symbol) + ' · ' + escapeHtml(sourceLabel(item.source)) + ' · 현재 ' + escapeHtml(item.currentPrice ? formatPrice(item.currentPrice, item.currency) : "-") + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(model.tone || "hold") + '">' + escapeHtml(model.action) + '</span>',
      '</div>',
      '<div class="lab-model-grid">',
      '<span>내 모델 매수 <strong class="buy">' + escapeHtml(model.buyScore) + '</strong></span>',
      '<span>내 모델 매도 <strong class="sell">' + escapeHtml(model.sellScore) + '</strong></span>',
      '<span>시스템 매수 <strong>' + escapeHtml(item.hasData ? item.buyScore : "-") + '</strong></span>',
      '<span>시스템 매도 <strong>' + escapeHtml(item.hasData ? item.sellScore : "-") + '</strong></span>',
      '</div>',
      model.errors.length ? '<div class="exit-reasons">' + model.errors.map(function (error) { return '<p>' + escapeHtml(error) + '</p>'; }).join("") + '</div>' : '',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderModelSettingField(name, label, type, placeholder) {
    return [
      '<label class="setting-field">',
      '<span>' + escapeHtml(label) + '</span>',
      '<input data-model-setting="' + escapeHtml(name) + '" type="' + escapeHtml(type || "text") + '" value="' + escapeHtml(settingValue(name) || defaultSettings[name] || "") + '" placeholder="' + escapeHtml(placeholder || "") + '" autocomplete="off" />',
      '</label>'
    ].join("");
  }

  function renderModelFormulaField(name, label, placeholder) {
    return [
      '<label class="setting-field wide">',
      '<span>' + escapeHtml(label) + '</span>',
      '<textarea data-model-setting="' + escapeHtml(name) + '" rows="3" autocomplete="off" placeholder="' + escapeHtml(placeholder || "") + '">' + escapeHtml(formulaSetting(name)) + '</textarea>',
      '</label>'
    ].join("");
  }

  function renderNumberSettingGrid(settingName, map, keys) {
    return [
      '<div class="model-number-grid">',
      keys.map(function (key) {
        return renderNumberSettingInput(settingName, key, map[key]);
      }).join(""),
      '</div>'
    ].join("");
  }

  function renderNumberSettingInput(settingName, key, value) {
    return [
      '<label class="lab-control">',
      '<span>' + escapeHtml(key) + '</span>',
      '<input type="number" step="0.01" value="' + escapeHtml(value == null ? 0 : value) + '" data-number-setting="' + escapeHtml(settingName) + '" data-number-key="' + escapeHtml(key) + '" />',
      '</label>'
    ].join("");
  }

  function modelVariableGuide() {
    return [
      ["buyScore", "수급/가치 기반 시스템 매수 점수"],
      ["sellScore", "수급/가치 기반 시스템 매도 점수"],
      ["thesisScore", "실험실에서 입력한 내 매수 점수"],
      ["riskScore", "실험실에서 입력한 리스크 점수"],
      ["confidenceScore", "확신 점수"],
      ["targetReturn", "목표 수익률"],
      ["stopLoss", "허용 손절률"],
      ["positionSize", "비중 계획"],
      ["fairValueGap", "적정가 대비 괴리"],
      ["undervalueBonus", "저평가 보너스"],
      ["expensivePenalty", "고평가/매도 보너스"],
      ["profitLossRate", "보유 수익률"]
    ];
  }

  function renderTradeSignalPanel(snapshot, full) {
    var items = buildTradeSignalItems(snapshot);
    var visible = full ? items : items.slice(0, 3);
    var actionCount = items.filter(function (item) {
      return item.tone === "danger" || item.tone === "caution" || item.tone === "watch";
    }).length;
    return [
      '<article class="panel signal-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Trade Signal</p>',
      '<h2>체결·거래량 매매 신호</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(actionCount) + '</span>',
      '</div>',
      '<div class="signal-list">',
      visible.length ? visible.map(renderTradeSignalRow).join("") : '<p class="subtle">보유 또는 관심 종목을 찾지 못했습니다.</p>',
      '</div>',
      '<div class="rule-strip">',
      '<span>매수/매도 실행 지시가 아니라 수급 데이터 점검 라벨입니다.</span>',
      full ? '<span>값은 설정에서 직접 수정하거나 향후 토스 시장 데이터로 교체합니다.</span>' : '<span>전체 목록은 실험실 탭에서 봅니다.</span>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderTradeSignalRow(item) {
    var signal = item.signal || {};
    var valuationText = item.valuation && item.valuation.status ? item.valuation.status : "가정 대기";
    return [
      '<div class="signal-row">',
      '<div class="signal-main">',
      '<div class="flow-title">',
      '<div>',
      '<strong>' + escapeHtml(item.name) + '</strong>',
      '<span>' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.sector || "-") + ' · ' + escapeHtml(sourceLabel(item.source)) + '</span>',
      '</div>',
      '<div class="exit-badges">',
      '<span class="source-chip ' + escapeHtml(item.source) + '">' + escapeHtml(sourceLabel(item.source)) + '</span>',
      '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.action) + '</span>',
      '</div>',
      '</div>',
      '<div class="signal-score-grid">',
      '<span>매수 점수 <strong class="buy">' + escapeHtml(item.hasData ? item.buyScore : "-") + '</strong></span>',
      '<span>매도 점수 <strong class="sell">' + escapeHtml(item.hasData ? item.sellScore : "-") + '</strong></span>',
      '<span>매수 비중 <strong>' + escapeHtml(item.hasData ? item.buyShare + "%" : "-") + '</strong></span>',
      '<span>가치 판단 <strong>' + escapeHtml(valuationText) + '</strong></span>',
      '</div>',
      '<div class="signal-metric-grid">',
      '<span>체결강도 <strong>' + escapeHtml(formatSignalNumber(signal.tradeStrength, "")) + '</strong></span>',
      '<span>거래량 <strong>' + escapeHtml(formatSignalRatio(signal.volumeRatio)) + '</strong></span>',
      '<span>매수량 <strong>' + escapeHtml(formatSignalVolume(signal.buyVolume)) + '</strong></span>',
      '<span>매도량 <strong>' + escapeHtml(formatSignalVolume(signal.sellVolume)) + '</strong></span>',
      '<span>호가 불균형 <strong>' + escapeHtml(formatSignalNumber(signal.bidAskImbalance, "%")) + '</strong></span>',
      '<span>가격 변화 <strong>' + escapeHtml(formatSignalNumber(signal.priceChangeRate, "%")) + '</strong></span>',
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
      '</div>',
      '</div>'
    ].join("");
  }

  function renderFormulaBlock(label, formula) {
    return [
      '<div class="formula-block">',
      '<span>' + escapeHtml(label) + '</span>',
      '<code>' + escapeHtml(formula || "-") + '</code>',
      '</div>'
    ].join("");
  }

  function renderVariableGuide(items) {
    return [
      '<div class="variable-grid">',
      items.map(function (item) {
        return '<span><strong>' + escapeHtml(item[0]) + '</strong>' + escapeHtml(item[1]) + '</span>';
      }).join(""),
      '</div>'
    ].join("");
  }

  function renderTradeSignalMethodPanel() {
    var rows = [
      ["체결강도", "100 이상이면 매수 체결 우위, 100 미만이면 매도 체결 우위"],
      ["거래량 배율", "평균 대비 거래량이 커질수록 신호 가중치 상승"],
      ["매수/매도량", "실제 체결 방향의 비중으로 매수·매도 압력 분리"],
      ["호가 불균형", "매수잔량 우위는 양수, 매도잔량 우위는 음수로 입력"],
      ["최종 라벨", "매수/매도 점수 + 보유 여부 + 기준값을 조합"]
    ];
    var variables = [
      ["tradeStrength", "체결강도"],
      ["volumeRatio", "거래량 배율"],
      ["buyShare", "매수 체결 비중"],
      ["bidAskImbalance", "호가 불균형"],
      ["priceChangeRate", "가격 변화율"],
      ["fairValueGap", "적정가 대비 괴리율"],
      ["undervalueBonus", "저평가 보너스"],
      ["expensivePenalty", "고평가 감점"],
      ["flowWeight", "수급 가중치"],
      ["valuationWeight", "가치 가중치"]
    ];
    return [
      '<article class="panel signal-method-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Signal Method</p>',
      '<h2>수급 계산 기준</h2>',
      '</div>',
      '</div>',
      '<div class="source-stack">',
      rows.map(function (row) {
        return '<div class="source-row"><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>';
      }).join(""),
      '</div>',
      '<div class="formula-stack">',
      renderFormulaBlock("매수 점수 공식", formulaSetting("buyScoreFormula")),
      renderFormulaBlock("매도 점수 공식", formulaSetting("sellScoreFormula")),
      '</div>',
      renderVariableGuide(variables),
      '<div class="rule-strip"><span>입력 형식: SYMBOL, 체결강도, 거래량배율, 매수량, 매도량, 호가불균형%, 가격변화%</span></div>',
      '</article>'
    ].join("");
  }

  function renderValuationPanel(snapshot, full) {
    var items = buildValuationItems(snapshot);
    var expensive = items.filter(function (item) {
      return item.tone === "danger" || item.tone === "caution";
    }).length;
    return [
      '<article class="panel valuation-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Valuation</p>',
      '<h2>보유 종목 적정가 점검</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(expensive) + '</span>',
      '</div>',
      '<div class="valuation-list">',
      items.length ? items.map(renderValuationRow).join("") : '<p class="subtle">토스 잔고에서 밸류에이션할 보유 종목을 찾지 못했습니다.</p>',
      '</div>',
      full ? '' : '<div class="rule-strip"><span>상세 가정은 실험실 탭과 상단 설정에서 조정합니다.</span></div>',
      '</article>'
    ].join("");
  }

  function renderValuationRow(item) {
    var hasValue = item.currentPrice && item.fairValue;
    return [
      '<div class="valuation-row">',
      '<div class="valuation-main">',
      '<div class="flow-title">',
      '<div>',
      '<strong>' + escapeHtml(item.name) + '</strong>',
      '<span>' + escapeHtml(item.symbol) + ' · 현재 ' + escapeHtml(item.currentPrice ? formatPrice(item.currentPrice, item.currency) : "-") + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.status) + '</span>',
      '</div>',
      '<div class="valuation-grid">',
      '<span>EPS <strong>' + escapeHtml(item.eps || "-") + '</strong></span>',
      '<span>목표 PER <strong>' + escapeHtml(item.targetPer || "-") + '</strong></span>',
      '<span>적정가 <strong>' + escapeHtml(hasValue ? formatPrice(item.fairValue, item.currency) : "-") + '</strong></span>',
      '<span>괴리 <strong>' + escapeHtml(hasValue ? signedPct(item.gap) : "-") + '</strong></span>',
      '</div>',
      '<div class="exit-reasons">',
      item.reasons.map(function (reason) {
        return '<p>' + escapeHtml(reason) + '</p>';
      }).join(""),
      '</div>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderValuationMethodPanel() {
    var rows = [
      ["적정가", "사용자 공식 결과"],
      ["싸다", "현재가가 안전마진 가격 이하"],
      ["적정권", "현재가가 적정가 이하"],
      ["비싸다", "현재가가 적정가를 초과"]
    ];
    var variables = [
      ["eps", "주당순이익"],
      ["targetPer", "목표 PER"],
      ["margin", "안전마진"],
      ["currentPrice", "현재가"],
      ["averagePrice", "평균단가"],
      ["profitLossRate", "수익률"],
      ["growthWeight", "성장 가중치"],
      ["qualityWeight", "품질 가중치"],
      ["riskWeight", "리스크 가중치"]
    ];
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Method</p>',
      '<h2>계산 기준</h2>',
      '</div>',
      '</div>',
      '<div class="source-stack">',
      rows.map(function (row) {
        return '<div class="source-row"><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>';
      }).join(""),
      '</div>',
      '<div class="formula-stack">',
      renderFormulaBlock("적정가 공식", formulaSetting("fairValueFormula")),
      '</div>',
      renderVariableGuide(variables),
      '<div class="rule-strip"><span>현재가는 토스 잔고/시세 값, EPS·목표 PER·공식·가중치는 사용자가 설정합니다.</span></div>',
      '</article>'
    ].join("");
  }

  function renderPortfolioPanel(snapshot) {
    var portfolio = snapshot.portfolio || { sectors: [] };
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Toss Portfolio</p>',
      '<h2>계좌 노출</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(formatMoney(portfolio.total)) + '</span>',
      '</div>',
      '<div class="allocation">',
      '<div class="source-row"><span>투자 평가액</span><strong>' + escapeHtml(formatMoney(portfolio.invested || 0)) + '</strong></div>',
      '<div class="source-row"><span>현금/주문 가능</span><strong>' + escapeHtml(formatMoney(portfolio.cash || 0)) + '</strong></div>',
      (portfolio.sectors || []).map(function (sector) {
        return [
          '<div class="bar-row">',
          '<div class="bar-meta"><span>' + escapeHtml(sector.sector) + '</span><strong>' + escapeHtml(pct(sector.ratio)) + '</strong></div>',
          '<div class="bar-track"><span style="width:' + Math.min(100, Math.max(2, sector.ratio)) + '%"></span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderHoldingsPanel(snapshot) {
    var toss = snapshot.toss || { positions: [] };
    var positions = (toss.positions || []).filter(function (item) {
      return item.source !== "cash" && item.sector !== "현금";
    });
    return [
      '<article class="panel holdings-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Holdings</p>',
      '<h2>보유 종목</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(positions.length) + '</span>',
      '</div>',
      '<div class="position-list">',
      positions.length ? positions.map(renderHoldingRow).join("") : '<p class="subtle">토스 잔고에서 보유 종목을 찾지 못했습니다.</p>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderHoldingRow(item) {
    return [
      '<div class="position-row rich-row">',
      '<div>',
      '<strong>' + escapeHtml(item.name) + '</strong>',
      '<span>' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.market || "-") + ' · 수량 ' + escapeHtml(item.quantity || "-") + '</span>',
      '<span>평균 ' + escapeHtml(formatCurrency(item.averagePrice || 0, item.currency)) + ' · 현재 ' + escapeHtml(formatCurrency(item.currentPrice || 0, item.currency)) + '</span>',
      '</div>',
      '<div class="right">',
      '<strong>' + escapeHtml(formatCurrency(item.marketValue, item.currency)) + '</strong>',
      '<span>' + escapeHtml(signedMoney(item.profitLoss, item.currency)) + ' · ' + escapeHtml(signedPct(item.profitLossRate)) + '</span>',
      '<span>매도 가능 ' + escapeHtml(item.sellableQuantity || item.quantity || "-") + '</span>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderWatchlistPanel(snapshot) {
    var toss = snapshot.toss || {};
    var watchlist = toss.watchlist || [];
    var symbols = watchlistSymbols();
    var lookup = {};
    (watchlist || []).forEach(function (item) {
      lookup[String(item.symbol || "").toUpperCase()] = item;
    });
    ((toss.positions || []) || []).forEach(function (item) {
      var symbol = String(item.symbol || "").toUpperCase();
      if (!symbol) return;
      lookup[symbol] = Object.assign({}, item, {
        source: item.source || "holding",
        quoteStatus: "보유 종목으로 분류됨"
      });
    });
    return [
      '<article class="panel watchlist-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Watchlist</p>',
      '<h2>관심 종목</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(symbols.length) + '</span>',
      '</div>',
      '<div class="watch-editor">',
      '<form class="watch-add-form" data-watch-add-form>',
      '<input name="symbol" placeholder="티커 또는 종목코드 추가" autocomplete="off" />',
      '<button class="text-button primary">추가</button>',
      '</form>',
      '<p class="subtle">토스 앱의 관심 목록은 공개 API에서 직접 읽지 못해, 여기 저장한 관심 종목을 기준으로 점검합니다.</p>',
      state.watchlistError ? '<p class="form-error">' + escapeHtml(state.watchlistError) + '</p>' : '',
      '</div>',
      '<div class="position-list">',
      symbols.length ? symbols.map(function (symbol) {
        return renderEditableWatchRow(symbol, lookup[symbol] || clientKnownStockInfo(symbol));
      }).join("") : '<p class="subtle">관심 종목을 추가하세요.</p>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderEditableWatchRow(symbol, item) {
    var original = String(symbol || "").toUpperCase();
    if (state.editingWatchSymbol === original) {
      return [
        '<form class="watch-edit-row" data-watch-edit-form="' + escapeHtml(original) + '">',
        '<input name="symbol" value="' + escapeHtml(original) + '" autocomplete="off" />',
        '<button class="text-button primary">저장</button>',
        '<button class="text-button" type="button" data-watch-cancel>취소</button>',
        '</form>'
      ].join("");
    }
    return renderWatchRow(Object.assign({}, item, { symbol: original }), true);
  }

  function renderWatchRow(item) {
    var editable = arguments.length > 1 && arguments[1];
    var source = item.source === "holding" ? "보유" : "관심";
    return [
      '<div class="position-row rich-row">',
      '<div>',
      '<strong>' + escapeHtml(item.name || item.symbol) + '</strong>',
      '<span>' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.market || "-") + ' · ' + escapeHtml(item.sector || "-") + ' · ' + escapeHtml(source) + '</span>',
      '</div>',
      '<div class="right">',
      '<strong>' + escapeHtml(item.currentPrice ? formatCurrency(item.currentPrice, item.currency) : "시세 대기") + '</strong>',
      '<span>' + escapeHtml(item.changeRate == null ? item.quoteStatus || "토스 시세 연결 후 표시" : signedPct(item.changeRate)) + '</span>',
      editable ? '<div class="row-actions"><button class="mini-button" data-watch-edit="' + escapeHtml(item.symbol) + '">수정</button><button class="mini-button danger" data-watch-remove="' + escapeHtml(item.symbol) + '">삭제</button></div>' : '',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderApiScopePanel() {
    var rows = [
      ["계좌", "계좌 식별, 주문 가능 금액"],
      ["잔고", "보유 종목, 수량, 평가금액, 손익"],
      ["시세", "관심 종목 현재가, 등락률"],
      ["거래", "주문/정정/취소는 별도 잠금 단계"]
    ];
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Scope</p>',
      '<h2>토스 API로만 쓰는 정보</h2>',
      '</div>',
      '</div>',
      '<div class="source-stack">',
      rows.map(function (row) {
        return '<div class="source-row"><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>';
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function currentFeed() {
    return state.feed || { items: [], channels: [], errors: [] };
  }

  function uniqueCount(items, key) {
    var seen = {};
    (items || []).forEach(function (item) {
      var value = String(item[key] || "").trim();
      if (value) seen[value] = true;
    });
    return Object.keys(seen).length;
  }

  function feedTagCounts(items) {
    var counts = {};
    (items || []).forEach(function (item) {
      (item.tags || []).forEach(function (tag) {
        counts[tag] = (counts[tag] || 0) + 1;
      });
    });
    return Object.keys(counts)
      .map(function (tag) {
        return { tag: tag, count: counts[tag] };
      })
      .sort(function (a, b) {
        return b.count - a.count;
      });
  }

  function renderFeedOverviewPanel() {
    var feed = currentFeed();
    var items = feed.items || [];
    var tags = feedTagCounts(items);
    return [
      '<article class="panel feed-overview-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Market Feed</p>',
      '<h2>여러 채널로 보는 시장 피드</h2>',
      '</div>',
      '<button class="text-button primary" data-action="refresh-feed">' + (state.feedLoading ? "갱신 중" : "피드 갱신") + '</button>',
      '</div>',
      '<div class="feed-stat-grid">',
      '<div class="feed-stat"><span>기사</span><strong>' + escapeHtml(items.length) + '</strong></div>',
      '<div class="feed-stat"><span>소스</span><strong>' + escapeHtml(uniqueCount(items, "source")) + '</strong></div>',
      '<div class="feed-stat"><span>채널</span><strong>' + escapeHtml(feedChannels.length) + '</strong></div>',
      '<div class="feed-stat"><span>갱신</span><strong>' + escapeHtml(feed.generatedAt ? formatFeedTime(feed.generatedAt) : "-") + '</strong></div>',
      '</div>',
      '<div class="theme-radar">',
      tags.length ? tags.slice(0, 8).map(function (entry) {
        return '<span>' + escapeHtml(entry.tag) + ' <strong>' + escapeHtml(entry.count) + '</strong></span>';
      }).join("") : '<span>키워드 대기</span>',
      '</div>',
      feed.errors && feed.errors.length ? '<p class="form-error">' + escapeHtml(feed.errors.slice(0, 2).join(" · ")) + '</p>' : '',
      '</article>'
    ].join("");
  }

  function renderFeedListPanel() {
    var feed = currentFeed();
    var items = feed.items || [];
    return [
      '<article class="panel feed-list-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Articles</p>',
      '<h2>최신 기사</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(items.length) + '</span>',
      '</div>',
      '<div class="news-list">',
      state.feedLoading ? '<div class="panel skeleton"></div>' : '',
      state.feedError ? '<p class="form-error">' + escapeHtml(state.feedError) + '</p>' : '',
      (!state.feedLoading && !state.feedError && !items.length) ? '<p class="subtle">피드 탭을 열면 실제 채널을 조회합니다.</p>' : '',
      (!state.feedLoading && items.length) ? items.map(renderFeedItem).join("") : '',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderFeedItem(item) {
    return [
      '<div class="news-item">',
      '<div>',
      '<div class="news-source">' + escapeHtml(item.channelLabel || item.source) + ' · ' + escapeHtml(item.source || "-") + ' · ' + escapeHtml(item.publishedLabel || "-") + '</div>',
      '<h3>' + escapeHtml(item.title) + '</h3>',
      '<p>' + escapeHtml(item.summary || "요약 대기") + '</p>',
      '<div class="trigger-list">',
      (item.tags || []).map(function (tag) {
        return '<span>' + escapeHtml(tag) + '</span>';
      }).join(""),
      '</div>',
      '</div>',
      item.url ? '<a class="open-link" href="' + escapeHtml(item.url) + '" target="_blank" rel="noreferrer" title="원문 열기">↗</a>' : '<span class="open-link muted">-</span>',
      '</div>'
    ].join("");
  }

  function renderFeedChannelPanel() {
    var feed = currentFeed();
    var channelMap = {};
    (feed.channels || []).forEach(function (channel) {
      channelMap[channel.id] = channel;
    });
    return [
      '<article class="panel feed-channel-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Channels</p>',
      '<h2>피드 채널 상태</h2>',
      '</div>',
      '</div>',
      '<div class="source-stack">',
      feedChannels.map(function (channel) {
        var stateChannel = channelMap[channel.id] || {};
        var count = stateChannel.count || 0;
        var status = stateChannel.error ? "오류" : (count ? count + "건" : "대기");
        return '<div class="source-row"><span>' + escapeHtml(channel.label) + '</span><strong>' + escapeHtml(status) + '</strong></div>';
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function settingValue(name) {
    return state.settings && state.settings[name] != null ? state.settings[name] : "";
  }

  function isConfiguredSetting(name) {
    return Boolean(state.serverConfigured && state.serverConfigured[name]);
  }

  function renderSettingField(name, label, type, placeholder, options) {
    options = options || {};
    var fieldPlaceholder = placeholder || "";
    if (options.preserveConfigured && isConfiguredSetting(name)) {
      fieldPlaceholder = "설정됨 - 새 값 입력 시 교체";
    }
    return [
      '<label class="setting-field">',
      '<span>' + escapeHtml(label) + '</span>',
      '<input data-setting="' + escapeHtml(name) + '" type="' + escapeHtml(type || "text") + '" value="' + escapeHtml(settingValue(name)) + '" placeholder="' + escapeHtml(fieldPlaceholder) + '" autocomplete="off" />',
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
      '<h2>토스 API 설정</h2>',
      '</div>',
      '<span class="tone-chip ' + (state.settingsSaved ? "watch" : "hold") + '">' + (state.settingsSaved ? "DB 저장됨" : "수정 중") + '</span>',
      '</div>',
      '<div class="settings-body">',
      '<div class="settings-note">',
      '<strong>저장 위치</strong>',
      '<p>토스와 알림 설정은 이 PC의 로컬 DB에 저장되고 서버가 직접 사용합니다. secret 원문은 다시 표시하지 않으며, 공유 모드에서는 설정 변경을 막습니다.</p>',
      state.serverSettingsError ? '<p class="form-error">' + escapeHtml(state.serverSettingsError) + '</p>' : '',
      state.serverSettingsLocked ? '<p class="form-error">공유 모드에서는 서버 설정 저장이 잠겨 있습니다.</p>' : '',
      '</div>',
      '<div class="settings-grid">',
      renderSettingField("watchlistSymbols", "관심 종목", "text", "NVDA,TSLA,000660"),
      renderSettingField("tossApiBaseUrl", "Toss API Base URL", "url", "https://openapi.tossinvest.com"),
      renderSettingField("tossClientId", "Toss Client ID", secretType, "client id", { preserveConfigured: true }),
      renderSettingField("tossClientSecret", "Toss Client Secret", secretType, "client secret", { preserveConfigured: true }),
      renderSettingField("tossAccountSeq", "Toss Account Seq", "text", "선택", { preserveConfigured: true }),
      renderSettingField("notifyProvider", "알림 제공자", "text", "telegram"),
      renderSettingField("telegramBotToken", "Telegram Bot Token", secretType, "bot token", { preserveConfigured: true }),
      renderSettingField("telegramChatId", "Telegram Chat ID", "text", "chat id", { preserveConfigured: true }),
      renderSettingField("notifyLinkUrl", "알림 링크 URL", "url", "http://127.0.0.1:3000"),
      renderSettingField("notifyIntervalMinutes", "알림 주기(분)", "number", "10"),
      '<label class="setting-field wide">',
      '<span>밸류에이션 가정</span>',
      '<textarea data-setting="valuationAssumptions" rows="4" autocomplete="off" placeholder="SYMBOL, EPS, 목표PER, 안전마진%">' + escapeHtml(settingValue("valuationAssumptions")) + '</textarea>',
      '</label>',
      '<label class="setting-field wide">',
      '<span>수급 신호 입력</span>',
      '<textarea data-setting="marketSignalInputs" rows="5" autocomplete="off" placeholder="SYMBOL, 체결강도, 거래량배율, 매수량, 매도량, 호가불균형%, 가격변화%">' + escapeHtml(settingValue("marketSignalInputs")) + '</textarea>',
      '</label>',
      '<label class="setting-field wide">',
      '<span>적정가 공식</span>',
      '<textarea data-setting="fairValueFormula" rows="2" autocomplete="off" placeholder="eps * targetPer">' + escapeHtml(formulaSetting("fairValueFormula")) + '</textarea>',
      '</label>',
      '<label class="setting-field wide">',
      '<span>매수 점수 공식</span>',
      '<textarea data-setting="buyScoreFormula" rows="3" autocomplete="off" placeholder="50 + ...">' + escapeHtml(formulaSetting("buyScoreFormula")) + '</textarea>',
      '</label>',
      '<label class="setting-field wide">',
      '<span>매도 점수 공식</span>',
      '<textarea data-setting="sellScoreFormula" rows="3" autocomplete="off" placeholder="50 + ...">' + escapeHtml(formulaSetting("sellScoreFormula")) + '</textarea>',
      '</label>',
      '<label class="setting-field wide">',
      '<span>추가 가중치</span>',
      '<textarea data-setting="formulaWeights" rows="5" autocomplete="off" placeholder="flowWeight=1">' + escapeHtml(settingValue("formulaWeights") || defaultSettings.formulaWeights) + '</textarea>',
      '</label>',
      '<label class="setting-field wide">',
      '<span>판단 기준값</span>',
      '<textarea data-setting="decisionThresholds" rows="5" autocomplete="off" placeholder="buyCandidate=78">' + escapeHtml(settingValue("decisionThresholds") || defaultSettings.decisionThresholds) + '</textarea>',
      '</label>',
      '</div>',
      '<div class="settings-actions">',
      '<button class="text-button primary" data-action="save-settings"' + (state.serverSettingsLocked ? ' disabled' : '') + '>저장</button>',
      '<button class="text-button" data-action="toggle-secrets">' + (state.showSecrets ? "숨기기" : "secret 보기") + '</button>',
      '<button class="text-button danger" data-action="clear-settings">삭제</button>',
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSettingsOverlay() {
    return [
      '<div class="settings-overlay" data-settings-overlay>',
      '<section class="settings-dialog" role="dialog" aria-modal="true" aria-label="설정">',
      '<div class="settings-dialog-head">',
      '<div>',
      '<p class="label">Settings</p>',
      '<h2>설정</h2>',
      '</div>',
      '<button class="icon-button" data-action="close-settings" title="닫기" aria-label="닫기">×</button>',
      '</div>',
      renderSettingsPanel(),
      '</section>',
      '</div>'
    ].join("");
  }

  function renderChecklistPanel(snapshot) {
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Read Only Guard</p>',
      '<h2>토스 전용 체크</h2>',
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

    var openSettings = app.querySelector('[data-action="open-settings"]');
    if (openSettings) {
      openSettings.addEventListener("click", function () {
        state.settingsOpen = true;
        render();
      });
    }

    var closeSettings = app.querySelector('[data-action="close-settings"]');
    if (closeSettings) {
      closeSettings.addEventListener("click", function () {
        state.settingsOpen = false;
        render();
      });
    }

    var settingsOverlay = app.querySelector("[data-settings-overlay]");
    if (settingsOverlay) {
      settingsOverlay.addEventListener("click", function (event) {
        if (event.target !== settingsOverlay) return;
        state.settingsOpen = false;
        render();
      });
    }

    var refreshFeed = app.querySelector('[data-action="refresh-feed"]');
    if (refreshFeed) {
      refreshFeed.addEventListener("click", function () {
        loadFeed(true);
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-lab-assumption]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateValuationAssumption(
          field.getAttribute("data-lab-symbol"),
          field.getAttribute("data-lab-assumption"),
          field.value
        );
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-lab-draft]")).forEach(function (field) {
      field.addEventListener("input", function () {
        updateLabDraft(
          field.getAttribute("data-lab-symbol"),
          field.getAttribute("data-lab-draft"),
          field.value,
          false
        );
      });
      field.addEventListener("change", function () {
        updateLabDraft(
          field.getAttribute("data-lab-symbol"),
          field.getAttribute("data-lab-draft"),
          field.value,
          true
        );
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-lab-save]")).forEach(function (button) {
      button.addEventListener("click", function () {
        saveLabRecord(button.getAttribute("data-lab-save"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-model-setting]")).forEach(function (field) {
      field.addEventListener("input", function () {
        var name = field.getAttribute("data-model-setting");
        if (!name) return;
        state.settings[name] = field.value;
        persistSettings();
        state.modelSaved = false;
        state.modelError = "";
      });
      field.addEventListener("change", function () {
        persistSettings();
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-number-setting]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNumberAssignmentSetting(
          field.getAttribute("data-number-setting"),
          field.getAttribute("data-number-key"),
          field.value
        );
      });
    });

    var saveModelVersionButton = app.querySelector('[data-action="save-model-version"]');
    if (saveModelVersionButton) {
      saveModelVersionButton.addEventListener("click", function () {
        saveModelVersion();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-export-lab]")).forEach(function (button) {
      button.addEventListener("click", function () {
        exportLabRecords(button.getAttribute("data-export-lab"));
      });
    });

    var exportModelVersionsButton = app.querySelector('[data-action="export-model-versions"]');
    if (exportModelVersionsButton) {
      exportModelVersionsButton.addEventListener("click", function () {
        exportModelVersions();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-mode]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var nextMode = button.getAttribute("data-mode") || "live";
        if (nextMode === state.dataMode || state.refreshing) return;
        state.dataMode = nextMode === "mock" ? "mock" : "live";
        state.snapshot = null;
        state.feed = null;
        state.feedError = "";
        persistDataMode(state.dataMode);
        load();
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
        saveSettings.disabled = true;
        saveSettingsToServer()
          .then(function () {
            state.snapshot = null;
            state.feed = null;
            state.settingsOpen = false;
            return load();
          })
          .catch(function (error) {
            state.serverSettingsError = error.message || "설정을 저장하지 못했습니다.";
            state.settingsSaved = false;
            render();
          });
      });
    }

    var watchAddForm = app.querySelector("[data-watch-add-form]");
    if (watchAddForm) {
      watchAddForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var input = watchAddForm.querySelector('input[name="symbol"]');
        var next = normalizeSymbols(input ? input.value : "");
        if (!next.length) {
          state.watchlistError = "추가할 티커나 종목코드를 입력하세요.";
          render();
          return;
        }
        var symbols = watchlistSymbols();
        if (symbols.indexOf(next[0]) >= 0) {
          state.watchlistError = "이미 추가된 관심 종목입니다.";
          render();
          return;
        }
        saveWatchlistSymbols(symbols.concat(next[0]));
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-watch-edit]")).forEach(function (button) {
      button.addEventListener("click", function () {
        state.editingWatchSymbol = String(button.getAttribute("data-watch-edit") || "").toUpperCase();
        state.watchlistError = "";
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-watch-remove]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var removeSymbol = String(button.getAttribute("data-watch-remove") || "").toUpperCase();
        saveWatchlistSymbols(watchlistSymbols().filter(function (symbol) {
          return symbol !== removeSymbol;
        }));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-watch-edit-form]")).forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var original = String(form.getAttribute("data-watch-edit-form") || "").toUpperCase();
        var input = form.querySelector('input[name="symbol"]');
        var next = normalizeSymbols(input ? input.value : "");
        if (!next.length) {
          state.watchlistError = "수정할 티커나 종목코드를 입력하세요.";
          render();
          return;
        }
        var symbols = watchlistSymbols();
        if (next[0] !== original && symbols.indexOf(next[0]) >= 0) {
          state.watchlistError = "이미 추가된 관심 종목입니다.";
          render();
          return;
        }
        saveWatchlistSymbols(symbols.map(function (symbol) {
          return symbol === original ? next[0] : symbol;
        }));
      });
    });

    var watchCancel = app.querySelector("[data-watch-cancel]");
    if (watchCancel) {
      watchCancel.addEventListener("click", function () {
        state.editingWatchSymbol = "";
        state.watchlistError = "";
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

  loadServerSettings().finally(function () {
    load();
  });
}());
