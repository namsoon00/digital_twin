enum MarketRegion { all, korea, unitedStates }

extension MarketRegionLabel on MarketRegion {
  String get label {
    switch (this) {
      case MarketRegion.all:
        return '전체';
      case MarketRegion.korea:
        return '한국';
      case MarketRegion.unitedStates:
        return '미국';
    }
  }

  String get compactLabel {
    switch (this) {
      case MarketRegion.all:
        return 'ALL';
      case MarketRegion.korea:
        return 'KR';
      case MarketRegion.unitedStates:
        return 'US';
    }
  }
}

enum FlowStage { build, breakout, expansion, pullback, riskOff }

enum MarketDataQuality { actual, mock }

extension MarketDataQualityLabel on MarketDataQuality {
  String get label {
    switch (this) {
      case MarketDataQuality.actual:
        return '실제 데이터';
      case MarketDataQuality.mock:
        return 'mock 데이터';
    }
  }
}

extension FlowStageLabel on FlowStage {
  String get label {
    switch (this) {
      case FlowStage.build:
        return '매집';
      case FlowStage.breakout:
        return '돌파';
      case FlowStage.expansion:
        return '확산';
      case FlowStage.pullback:
        return '눌림';
      case FlowStage.riskOff:
        return '경계';
    }
  }
}

enum CapitalFlowAssetClass {
  equityIndex,
  sector,
  crypto,
  commodity,
  bond,
  currency,
  alternative,
}

extension CapitalFlowAssetClassLabel on CapitalFlowAssetClass {
  String get label {
    switch (this) {
      case CapitalFlowAssetClass.equityIndex:
        return '주가지수';
      case CapitalFlowAssetClass.sector:
        return '섹터';
      case CapitalFlowAssetClass.crypto:
        return '코인';
      case CapitalFlowAssetClass.commodity:
        return '원자재';
      case CapitalFlowAssetClass.bond:
        return '채권';
      case CapitalFlowAssetClass.currency:
        return '통화';
      case CapitalFlowAssetClass.alternative:
        return '대체자산';
    }
  }
}

enum ApiIntegrationStatus { live, configurable, needed, vendorNeeded }

extension ApiIntegrationStatusLabel on ApiIntegrationStatus {
  String get label {
    switch (this) {
      case ApiIntegrationStatus.live:
        return '연결됨';
      case ApiIntegrationStatus.configurable:
        return '설정 가능';
      case ApiIntegrationStatus.needed:
        return '추가 필요';
      case ApiIntegrationStatus.vendorNeeded:
        return '벤더 선정';
    }
  }
}

enum EconomicFeedType { macro, liquidity, policy, flow, earnings, risk }

extension EconomicFeedTypeLabel on EconomicFeedType {
  String get label {
    switch (this) {
      case EconomicFeedType.macro:
        return '매크로';
      case EconomicFeedType.liquidity:
        return '유동성';
      case EconomicFeedType.policy:
        return '정책';
      case EconomicFeedType.flow:
        return '자금';
      case EconomicFeedType.earnings:
        return '실적';
      case EconomicFeedType.risk:
        return '리스크';
    }
  }
}

class EconomicFeedChannel {
  const EconomicFeedChannel({
    required this.id,
    required this.name,
    required this.provider,
    required this.query,
    required this.type,
    required this.region,
    required this.tags,
    required this.url,
  });

  final String id;
  final String name;
  final String provider;
  final String query;
  final EconomicFeedType type;
  final MarketRegion region;
  final List<String> tags;
  final String url;
}

class DataApiVendorOption {
  const DataApiVendorOption({
    required this.id,
    required this.name,
    required this.provider,
    required this.docsUrl,
    required this.endpointHint,
  });

  final String id;
  final String name;
  final String provider;
  final String docsUrl;
  final String endpointHint;
}

class DataApiSource {
  const DataApiSource({
    required this.id,
    required this.name,
    required this.provider,
    required this.status,
    required this.coverage,
    required this.usedFor,
    required this.keyName,
    required this.docsUrl,
    required this.priority,
    this.vendorOptions = const [],
  });

  final String id;
  final String name;
  final String provider;
  final ApiIntegrationStatus status;
  final String coverage;
  final String usedFor;
  final String keyName;
  final String docsUrl;
  final int priority;
  final List<DataApiVendorOption> vendorOptions;

  bool get requiresVendorSelection {
    return status == ApiIntegrationStatus.vendorNeeded &&
        vendorOptions.isNotEmpty;
  }

  DataApiVendorOption? vendorOptionFor(String vendorId) {
    for (final option in vendorOptions) {
      if (option.id == vendorId) {
        return option;
      }
    }
    return null;
  }
}

class EconomicFeedItem {
  const EconomicFeedItem({
    required this.id,
    required this.type,
    required this.region,
    required this.title,
    required this.summary,
    required this.source,
    required this.timestampLabel,
    required this.impactScore,
    required this.tags,
    this.url = '',
    this.channelId = '',
    this.channelName = '',
    this.publishedAt,
  });

  final String id;
  final EconomicFeedType type;
  final MarketRegion region;
  final String title;
  final String summary;
  final String source;
  final String timestampLabel;
  final int impactScore;
  final List<String> tags;
  final String url;
  final String channelId;
  final String channelName;
  final DateTime? publishedAt;
}

class DataApiKeySettings {
  const DataApiKeySettings({required this.keys, this.vendors = const {}});

  factory DataApiKeySettings.empty() {
    return const DataApiKeySettings(keys: {}, vendors: {});
  }

  final Map<String, String> keys;
  final Map<String, String> vendors;

  String keyFor(String apiId) {
    return keys[apiId] ?? '';
  }

  String vendorFor(String apiId) {
    return vendors[apiId] ?? '';
  }

  bool hasKeyFor(String apiId) {
    return keyFor(apiId).trim().isNotEmpty;
  }

  bool hasVendorFor(String apiId) {
    return vendorFor(apiId).trim().isNotEmpty;
  }

  int configuredCount(Iterable<DataApiSource> sources) {
    return sources.where((source) {
      if (source.requiresVendorSelection) {
        return hasVendorFor(source.id);
      }
      return hasKeyFor(source.id);
    }).length;
  }

  DataApiKeySettings copyWithKey(String apiId, String value) {
    final next = Map<String, String>.of(keys);
    final normalized = value.trim();
    if (normalized.isEmpty) {
      next.remove(apiId);
    } else {
      next[apiId] = normalized;
    }
    return DataApiKeySettings(
      keys: Map.unmodifiable(next),
      vendors: Map.unmodifiable(vendors),
    );
  }

  DataApiKeySettings copyWithVendor(String apiId, String value) {
    final next = Map<String, String>.of(vendors);
    final normalized = value.trim();
    if (normalized.isEmpty) {
      next.remove(apiId);
    } else {
      next[apiId] = normalized;
    }
    return DataApiKeySettings(
      keys: Map.unmodifiable(keys),
      vendors: Map.unmodifiable(next),
    );
  }
}

class InvestmentChecklistItem {
  const InvestmentChecklistItem({
    required this.id,
    required this.label,
    required this.checked,
    this.isCustom = false,
  });

  factory InvestmentChecklistItem.fromJson(Map<String, dynamic> json) {
    return InvestmentChecklistItem(
      id: '${json['id'] ?? ''}',
      label: '${json['label'] ?? ''}',
      checked: json['checked'] == true,
      isCustom: json['isCustom'] == true,
    );
  }

  final String id;
  final String label;
  final bool checked;
  final bool isCustom;

  Map<String, dynamic> toJson() {
    return {'id': id, 'label': label, 'checked': checked, 'isCustom': isCustom};
  }

  InvestmentChecklistItem copyWith({
    String? id,
    String? label,
    bool? checked,
    bool? isCustom,
  }) {
    return InvestmentChecklistItem(
      id: id ?? this.id,
      label: label ?? this.label,
      checked: checked ?? this.checked,
      isCustom: isCustom ?? this.isCustom,
    );
  }
}

class InvestmentChecklistDay {
  const InvestmentChecklistDay({
    required this.dateKey,
    required this.items,
    required this.note,
  });

  factory InvestmentChecklistDay.defaults(String dateKey) {
    return InvestmentChecklistDay(
      dateKey: dateKey,
      items: defaultItems,
      note: '',
    );
  }

  factory InvestmentChecklistDay.fromJson(Map<String, dynamic> json) {
    final rawItems = json['items'];
    return InvestmentChecklistDay(
      dateKey: '${json['dateKey'] ?? ''}',
      items: rawItems is List
          ? rawItems
                .whereType<Map<String, dynamic>>()
                .map(InvestmentChecklistItem.fromJson)
                .where((item) => item.id.isNotEmpty && item.label.isNotEmpty)
                .toList(growable: false)
          : const [],
      note: '${json['note'] ?? ''}',
    );
  }

  static const defaultItems = [
    InvestmentChecklistItem(
      id: 'global-flow',
      label: '글로벌 지수와 환율 방향 확인',
      checked: false,
    ),
    InvestmentChecklistItem(
      id: 'capital-flow',
      label: '자금 흐름 탭에서 강한 자산군 확인',
      checked: false,
    ),
    InvestmentChecklistItem(
      id: 'trade-thesis',
      label: '오늘 매매할 종목과 진입 이유 작성',
      checked: false,
    ),
    InvestmentChecklistItem(
      id: 'risk-plan',
      label: '손절선과 목표 구간을 숫자로 확정',
      checked: false,
    ),
    InvestmentChecklistItem(
      id: 'position-size',
      label: '포지션 크기와 하루 최대 손실 한도 확인',
      checked: false,
    ),
    InvestmentChecklistItem(
      id: 'event-calendar',
      label: '실적, 지표, 이벤트 캘린더 확인',
      checked: false,
    ),
    InvestmentChecklistItem(
      id: 'emotion-check',
      label: '감정 상태와 과매매 위험 점검',
      checked: false,
    ),
  ];

  final String dateKey;
  final List<InvestmentChecklistItem> items;
  final String note;

  int get completedCount => items.where((item) => item.checked).length;
  int get totalCount => items.length;
  int get remainingCount => (totalCount - completedCount).clamp(0, totalCount);
  bool get isComplete => totalCount > 0 && completedCount == totalCount;
  bool get hasActivity =>
      completedCount > 0 ||
      note.trim().isNotEmpty ||
      items.any((item) => item.isCustom);
  double get completionRate =>
      totalCount == 0 ? 0 : completedCount / totalCount;

  Map<String, dynamic> toJson() {
    return {
      'dateKey': dateKey,
      'items': items.map((item) => item.toJson()).toList(growable: false),
      'note': note,
    };
  }

  InvestmentChecklistDay copyWith({
    String? dateKey,
    List<InvestmentChecklistItem>? items,
    String? note,
  }) {
    return InvestmentChecklistDay(
      dateKey: dateKey ?? this.dateKey,
      items: items ?? this.items,
      note: note ?? this.note,
    );
  }

  InvestmentChecklistDay toggleItem(String itemId, bool checked) {
    return copyWith(
      items: [
        for (final item in items)
          if (item.id == itemId) item.copyWith(checked: checked) else item,
      ],
    );
  }

  InvestmentChecklistDay addCustomItem(String itemId, String label) {
    final normalized = label.trim();
    if (normalized.isEmpty) {
      return this;
    }
    return copyWith(
      items: [
        ...items,
        InvestmentChecklistItem(
          id: itemId,
          label: normalized,
          checked: false,
          isCustom: true,
        ),
      ],
    );
  }

  InvestmentChecklistDay removeItem(String itemId) {
    return copyWith(
      items: items
          .where((item) => item.id != itemId || !item.isCustom)
          .toList(growable: false),
    );
  }
}

String checklistDateKey(DateTime date) {
  final year = date.year.toString().padLeft(4, '0');
  final month = date.month.toString().padLeft(2, '0');
  final day = date.day.toString().padLeft(2, '0');
  return '$year-$month-$day';
}

DateTime checklistMonthStart(DateTime date) {
  return DateTime(date.year, date.month);
}

class FlowCandle {
  const FlowCandle({
    required this.label,
    required this.open,
    required this.high,
    required this.low,
    required this.close,
    required this.liquidity,
    required this.momentum,
    required this.risk,
    required this.aiFlow,
    required this.cryptoFlow,
    required this.goldFlow,
    required this.koreaFlow,
    this.dataQuality = MarketDataQuality.mock,
    this.dataProvider = 'MarketFlow mock',
  });

  final String label;
  final double open;
  final double high;
  final double low;
  final double close;
  final double liquidity;
  final double momentum;
  final double risk;
  final double aiFlow;
  final double cryptoFlow;
  final double goldFlow;
  final double koreaFlow;
  final MarketDataQuality dataQuality;
  final String dataProvider;
}

class CapitalFlow {
  const CapitalFlow({
    required this.id,
    required this.name,
    required this.assetClass,
    required this.regionLabel,
    required this.destination,
    required this.flowScore,
    required this.momentum,
    required this.liquidity,
    required this.risk,
    required this.netFlowLabel,
    required this.signal,
    required this.thesis,
    required this.drivers,
    required this.trend,
    required this.updatedLabel,
    this.dataQuality = MarketDataQuality.mock,
    this.dataProvider = 'MarketFlow mock',
  });

  final String id;
  final String name;
  final CapitalFlowAssetClass assetClass;
  final String regionLabel;
  final String destination;
  final int flowScore;
  final int momentum;
  final int liquidity;
  final int risk;
  final String netFlowLabel;
  final String signal;
  final String thesis;
  final List<String> drivers;
  final List<double> trend;
  final String updatedLabel;
  final MarketDataQuality dataQuality;
  final String dataProvider;
}

class EmergingCapitalFlow {
  const EmergingCapitalFlow({
    required this.id,
    required this.title,
    required this.from,
    required this.to,
    required this.probability,
    required this.timeframe,
    required this.trigger,
    required this.watch,
    required this.beneficiaries,
    required this.risks,
  });

  final String id;
  final String title;
  final String from;
  final String to;
  final int probability;
  final String timeframe;
  final String trigger;
  final String watch;
  final List<String> beneficiaries;
  final List<String> risks;
}

class AppUser {
  const AppUser({
    required this.id,
    required this.name,
    required this.role,
    required this.riskProfile,
    required this.watchlistCount,
  });

  final String id;
  final String name;
  final String role;
  final String riskProfile;
  final int watchlistCount;
}

class MarketPulse {
  const MarketPulse({
    required this.region,
    required this.title,
    required this.score,
    required this.change,
    required this.bias,
    required this.netFlow,
    required this.summary,
    required this.heat,
    required this.updatedLabel,
  });

  final MarketRegion region;
  final String title;
  final int score;
  final double change;
  final String bias;
  final String netFlow;
  final String summary;
  final List<double> heat;
  final String updatedLabel;
}

class ThemePulse {
  const ThemePulse({
    required this.id,
    required this.name,
    required this.region,
    required this.stage,
    required this.score,
    required this.diffusion,
    required this.momentum,
    required this.risk,
    required this.leaders,
    required this.narrative,
    required this.trend,
  });

  final String id;
  final String name;
  final MarketRegion region;
  final FlowStage stage;
  final int score;
  final int diffusion;
  final int momentum;
  final int risk;
  final List<String> leaders;
  final String narrative;
  final List<double> trend;
}

class EquityFlow {
  const EquityFlow({
    required this.symbol,
    required this.apiSymbol,
    required this.name,
    required this.region,
    required this.theme,
    required this.priceLabel,
    required this.changePercent,
    required this.flowScore,
    required this.relativeStrength,
    required this.volumeSurge,
    required this.risk,
    required this.stage,
    required this.tags,
    required this.thesis,
    required this.sparkline,
  });

  final String symbol;
  final String apiSymbol;
  final String name;
  final MarketRegion region;
  final String theme;
  final String priceLabel;
  final double changePercent;
  final int flowScore;
  final int relativeStrength;
  final int volumeSurge;
  final int risk;
  final FlowStage stage;
  final List<String> tags;
  final String thesis;
  final List<double> sparkline;
}

enum QuoteFetchStatus {
  idle,
  loading,
  ready,
  cached,
  missingApiKey,
  partial,
  failed,
}

class QuoteApiSnapshot {
  const QuoteApiSnapshot({
    required this.provider,
    required this.endpoint,
    required this.status,
    required this.message,
    required this.apiKeyConfigured,
    required this.requestedSymbols,
    this.updatedAt,
  });

  factory QuoteApiSnapshot.initial({required bool apiKeyConfigured}) {
    return QuoteApiSnapshot(
      provider: 'Alpha Vantage',
      endpoint: 'GLOBAL_QUOTE',
      status: QuoteFetchStatus.idle,
      message: apiKeyConfigured ? '대기 중' : 'API key 미설정',
      apiKeyConfigured: apiKeyConfigured,
      requestedSymbols: 0,
    );
  }

  final String provider;
  final String endpoint;
  final QuoteFetchStatus status;
  final String message;
  final bool apiKeyConfigured;
  final int requestedSymbols;
  final DateTime? updatedAt;

  String get statusLabel {
    switch (status) {
      case QuoteFetchStatus.idle:
        return '대기';
      case QuoteFetchStatus.loading:
        return '조회 중';
      case QuoteFetchStatus.ready:
        return '최신 데이터 연결';
      case QuoteFetchStatus.cached:
        return '저장 데이터';
      case QuoteFetchStatus.missingApiKey:
        return 'API key 필요';
      case QuoteFetchStatus.partial:
        return '일부 업데이트';
      case QuoteFetchStatus.failed:
        return '연결 실패';
    }
  }
}

class LiveQuote {
  const LiveQuote({
    required this.symbol,
    required this.apiSymbol,
    required this.price,
    required this.change,
    required this.changePercent,
    required this.volume,
    required this.latestTradingDay,
    required this.fetchedAt,
    required this.provider,
  });

  final String symbol;
  final String apiSymbol;
  final double price;
  final double change;
  final double changePercent;
  final int volume;
  final String latestTradingDay;
  final DateTime fetchedAt;
  final String provider;

  String priceLabel(MarketRegion region) {
    if (region == MarketRegion.korea) {
      return '${price.round()}원';
    }
    return '\$${price.toStringAsFixed(2)}';
  }

  String get changePercentLabel {
    final prefix = changePercent >= 0 ? '+' : '';
    return '$prefix${changePercent.toStringAsFixed(2)}%';
  }
}

class QuoteFetchResult {
  const QuoteFetchResult({required this.quotes, required this.snapshot});

  final Map<String, LiveQuote> quotes;
  final QuoteApiSnapshot snapshot;
}

enum CryptoFetchStatus { idle, loading, ready, cached, partial, failed }

class CryptoMarketSnapshot {
  const CryptoMarketSnapshot({
    required this.provider,
    required this.endpoint,
    required this.status,
    required this.message,
    required this.apiKeyConfigured,
    required this.assetCount,
    required this.updatedAt,
  });

  factory CryptoMarketSnapshot.initial({
    required bool apiKeyConfigured,
    required int assetCount,
  }) {
    return CryptoMarketSnapshot(
      provider: 'CoinGecko',
      endpoint: '/api/v3/coins/markets',
      status: CryptoFetchStatus.idle,
      message: apiKeyConfigured ? '대기 중' : '공개 API 대기 중',
      apiKeyConfigured: apiKeyConfigured,
      assetCount: assetCount,
      updatedAt: null,
    );
  }

  final String provider;
  final String endpoint;
  final CryptoFetchStatus status;
  final String message;
  final bool apiKeyConfigured;
  final int assetCount;
  final DateTime? updatedAt;

  String get statusLabel {
    switch (status) {
      case CryptoFetchStatus.idle:
        return '대기';
      case CryptoFetchStatus.loading:
        return '조회 중';
      case CryptoFetchStatus.ready:
        return '최신 데이터 연결';
      case CryptoFetchStatus.cached:
        return '저장 데이터';
      case CryptoFetchStatus.partial:
        return '일부 업데이트';
      case CryptoFetchStatus.failed:
        return '연결 실패';
    }
  }
}

class CryptoAsset {
  const CryptoAsset({
    required this.id,
    required this.symbol,
    required this.name,
    required this.rank,
    required this.priceUsd,
    required this.marketCapUsd,
    required this.volume24hUsd,
    required this.change1hPercent,
    required this.change24hPercent,
    required this.change7dPercent,
    required this.updatedAt,
    required this.provider,
  });

  final String id;
  final String symbol;
  final String name;
  final int rank;
  final double priceUsd;
  final double marketCapUsd;
  final double volume24hUsd;
  final double change1hPercent;
  final double change24hPercent;
  final double change7dPercent;
  final DateTime? updatedAt;
  final String provider;
}

class CryptoMarketFetchResult {
  const CryptoMarketFetchResult({required this.assets, required this.snapshot});

  final List<CryptoAsset> assets;
  final CryptoMarketSnapshot snapshot;
}

class TossAccountSettings {
  const TossAccountSettings({
    required this.enabled,
    required this.accountAlias,
    required this.accountHint,
    required this.apiBaseUrl,
    required this.appKey,
    required this.appSecret,
    required this.accessToken,
    required this.accountNumber,
    required this.testPath,
    required this.readOnly,
    required this.orderLocked,
  });

  factory TossAccountSettings.defaults() {
    return const TossAccountSettings(
      enabled: false,
      accountAlias: '',
      accountHint: '',
      apiBaseUrl: 'https://openapi.tossinvest.com',
      appKey: '',
      appSecret: '',
      accessToken: '',
      accountNumber: '',
      testPath: '/api/v1/accounts',
      readOnly: true,
      orderLocked: true,
    );
  }

  final bool enabled;
  final String accountAlias;
  final String accountHint;
  final String apiBaseUrl;
  final String appKey;
  final String appSecret;
  final String accessToken;
  final String accountNumber;
  final String testPath;
  final bool readOnly;
  final bool orderLocked;

  bool get hasAccount =>
      accountAlias.trim().isNotEmpty ||
      accountHint.trim().isNotEmpty ||
      accountNumber.trim().isNotEmpty;
  bool get hasApiBaseUrl => apiBaseUrl.trim().isNotEmpty;
  bool get hasCredential =>
      accessToken.trim().isNotEmpty ||
      (appKey.trim().isNotEmpty && appSecret.trim().isNotEmpty);
  bool get hasTestPath => testPath.trim().isNotEmpty;
  bool get isReady =>
      enabled &&
      hasApiBaseUrl &&
      hasCredential &&
      hasTestPath &&
      readOnly &&
      orderLocked;

  TossAccountSettings copyWith({
    bool? enabled,
    String? accountAlias,
    String? accountHint,
    String? apiBaseUrl,
    String? appKey,
    String? appSecret,
    String? accessToken,
    String? accountNumber,
    String? testPath,
    bool? readOnly,
    bool? orderLocked,
  }) {
    return TossAccountSettings(
      enabled: enabled ?? this.enabled,
      accountAlias: accountAlias ?? this.accountAlias,
      accountHint: accountHint ?? this.accountHint,
      apiBaseUrl: apiBaseUrl ?? this.apiBaseUrl,
      appKey: appKey ?? this.appKey,
      appSecret: appSecret ?? this.appSecret,
      accessToken: accessToken ?? this.accessToken,
      accountNumber: accountNumber ?? this.accountNumber,
      testPath: testPath ?? this.testPath,
      readOnly: readOnly ?? this.readOnly,
      orderLocked: orderLocked ?? this.orderLocked,
    );
  }
}

class JournalEntry {
  const JournalEntry({
    required this.id,
    required this.userId,
    required this.symbol,
    required this.thesis,
    required this.emotion,
    required this.confidence,
    required this.outcome,
    required this.createdLabel,
  });

  final String id;
  final String userId;
  final String symbol;
  final String thesis;
  final String emotion;
  final int confidence;
  final String outcome;
  final String createdLabel;

  JournalEntry copyWith({
    String? id,
    String? userId,
    String? symbol,
    String? thesis,
    String? emotion,
    int? confidence,
    String? outcome,
    String? createdLabel,
  }) {
    return JournalEntry(
      id: id ?? this.id,
      userId: userId ?? this.userId,
      symbol: symbol ?? this.symbol,
      thesis: thesis ?? this.thesis,
      emotion: emotion ?? this.emotion,
      confidence: confidence ?? this.confidence,
      outcome: outcome ?? this.outcome,
      createdLabel: createdLabel ?? this.createdLabel,
    );
  }
}
