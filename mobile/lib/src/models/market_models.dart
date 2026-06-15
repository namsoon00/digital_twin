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

enum QuoteFetchStatus { idle, loading, ready, missingApiKey, partial, failed }

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
      apiBaseUrl: '',
      appKey: '',
      appSecret: '',
      accessToken: '',
      accountNumber: '',
      testPath: '',
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
      hasAccount &&
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
