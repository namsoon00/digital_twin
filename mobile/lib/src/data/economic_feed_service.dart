import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'local_settings_database.dart';
import '../models/market_models.dart';

enum EconomicFeedFetchStatus { idle, loading, ready, cached, partial, failed }

class EconomicFeedFetchSnapshot {
  const EconomicFeedFetchSnapshot({
    required this.provider,
    required this.endpoint,
    required this.status,
    required this.message,
    required this.itemCount,
    required this.updatedAt,
  });

  factory EconomicFeedFetchSnapshot.idle(int itemCount) {
    return EconomicFeedFetchSnapshot(
      provider: MarketNewsEconomicFeedService.provider,
      endpoint: MarketNewsEconomicFeedService.endpoint,
      status: EconomicFeedFetchStatus.idle,
      message: '다중 뉴스 채널 대기 중',
      itemCount: itemCount,
      updatedAt: null,
    );
  }

  factory EconomicFeedFetchSnapshot.loading(int itemCount) {
    return EconomicFeedFetchSnapshot(
      provider: MarketNewsEconomicFeedService.provider,
      endpoint: MarketNewsEconomicFeedService.endpoint,
      status: EconomicFeedFetchStatus.loading,
      message: '다중 뉴스 채널 조회 중',
      itemCount: itemCount,
      updatedAt: DateTime.now(),
    );
  }

  final String provider;
  final String endpoint;
  final EconomicFeedFetchStatus status;
  final String message;
  final int itemCount;
  final DateTime? updatedAt;

  String get statusLabel {
    return switch (status) {
      EconomicFeedFetchStatus.idle => '뉴스 대기',
      EconomicFeedFetchStatus.loading => '뉴스 조회',
      EconomicFeedFetchStatus.ready => '실제 뉴스',
      EconomicFeedFetchStatus.cached => '뉴스 캐시',
      EconomicFeedFetchStatus.partial => '뉴스 일부',
      EconomicFeedFetchStatus.failed => '대체 피드',
    };
  }
}

class EconomicFeedFetchResult {
  const EconomicFeedFetchResult({required this.items, required this.snapshot});

  final List<EconomicFeedItem> items;
  final EconomicFeedFetchSnapshot snapshot;
}

abstract class EconomicFeedService {
  List<EconomicFeedChannel> get feedChannels => const [];

  Future<EconomicFeedFetchResult> fetchFeeds();

  void dispose() {}
}

class StaticEconomicFeedService implements EconomicFeedService {
  const StaticEconomicFeedService(this.items, {this.feedChannels = const []});

  final List<EconomicFeedItem> items;

  @override
  final List<EconomicFeedChannel> feedChannels;

  @override
  Future<EconomicFeedFetchResult> fetchFeeds() async {
    return EconomicFeedFetchResult(
      items: items,
      snapshot: EconomicFeedFetchSnapshot(
        provider: 'Test',
        endpoint: 'static',
        status: EconomicFeedFetchStatus.ready,
        message: '정적 테스트 피드',
        itemCount: items.length,
        updatedAt: DateTime.now(),
      ),
    );
  }

  @override
  void dispose() {}
}

class MarketNewsEconomicFeedService implements EconomicFeedService {
  MarketNewsEconomicFeedService({
    http.Client? client,
    LocalSettingsDatabase? database,
  }) : _client = client ?? http.Client(),
       _database = database ?? const LocalSettingsDatabase();

  static const provider = '멀티 채널 뉴스';
  static const endpoint = 'GDELT + public RSS';
  static const _cacheId = 'multi-channel-news.feeds';
  static const _localProxyBaseUrl = String.fromEnvironment(
    'MARKET_FLOW_FEED_PROXY_BASE_URL',
    defaultValue: 'http://127.0.0.1:3000',
  );
  static const _itemsPerSource = 5;
  static const _maxItems = 30;
  static const _gdeltSearch =
      '"stock market" OR "central bank" OR semiconductor OR Korea OR cryptocurrency';

  static List<_EconomicFeedSource> get _sources => [
    const _EconomicFeedSource(
      id: 'cnbc-markets',
      name: 'CNBC 시장',
      provider: 'CNBC',
      feedUrl: 'https://www.cnbc.com/id/15839135/device/rss/rss.html',
      channelUrl: 'https://www.cnbc.com/markets/',
      query: 'Market movers, earnings, tech, Wall Street',
      format: _EconomicFeedSourceFormat.rss,
      type: EconomicFeedType.earnings,
      region: MarketRegion.unitedStates,
      tags: ['markets', 'earnings', 'AI'],
      baseImpact: 80,
    ),
    const _EconomicFeedSource(
      id: 'yahoo-market-tape',
      name: 'Yahoo 시장 테이프',
      provider: 'Yahoo Finance',
      feedUrl:
          'https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,KRW=X,BTC-USD&region=US&lang=en-US',
      channelUrl: 'https://finance.yahoo.com/markets/',
      query: 'S&P 500, Nasdaq, KRW, Bitcoin headlines',
      format: _EconomicFeedSourceFormat.rss,
      type: EconomicFeedType.liquidity,
      region: MarketRegion.unitedStates,
      tags: ['indices', 'Nasdaq', 'FX'],
      baseImpact: 78,
    ),
    const _EconomicFeedSource(
      id: 'fed-policy',
      name: 'Fed 정책',
      provider: 'Federal Reserve',
      feedUrl: 'https://www.federalreserve.gov/feeds/press_all.xml',
      channelUrl: 'https://www.federalreserve.gov/newsevents/pressreleases.htm',
      query: 'Official Federal Reserve press releases',
      format: _EconomicFeedSourceFormat.rss,
      type: EconomicFeedType.policy,
      region: MarketRegion.unitedStates,
      tags: ['Fed', 'FOMC', 'rates'],
      baseImpact: 76,
    ),
    const _EconomicFeedSource(
      id: 'yonhap-economy',
      name: '연합뉴스 경제',
      provider: '연합뉴스',
      feedUrl: 'https://www.yna.co.kr/rss/economy.xml',
      channelUrl: 'https://www.yna.co.kr/economy/index',
      query: 'Korea economy, securities, industry',
      format: _EconomicFeedSourceFormat.rss,
      type: EconomicFeedType.flow,
      region: MarketRegion.korea,
      tags: ['한국', '증권', '산업'],
      baseImpact: 76,
    ),
    const _EconomicFeedSource(
      id: 'coindesk-markets',
      name: 'CoinDesk 마켓',
      provider: 'CoinDesk',
      feedUrl: 'https://www.coindesk.com/arc/outboundfeeds/rss/',
      channelUrl: 'https://www.coindesk.com/markets/',
      query: 'Crypto markets, tokenization, liquidity',
      format: _EconomicFeedSourceFormat.rss,
      type: EconomicFeedType.macro,
      region: MarketRegion.all,
      tags: ['crypto', 'bitcoin', 'liquidity'],
      baseImpact: 73,
    ),
    _EconomicFeedSource(
      id: 'gdelt-cross-source',
      name: 'GDELT 글로벌 레이더',
      provider: GdeltNewsEconomicFeedService.provider,
      feedUrl: _gdeltArticlesUri().toString(),
      channelUrl: _gdeltSearchUri().toString(),
      query: 'Cross-source global market radar',
      format: _EconomicFeedSourceFormat.gdelt,
      type: EconomicFeedType.macro,
      region: MarketRegion.all,
      tags: const ['global', 'cross-source', 'market'],
      baseImpact: 74,
    ),
  ];

  final http.Client _client;
  final LocalSettingsDatabase _database;

  @override
  List<EconomicFeedChannel> get feedChannels => defaultFeedChannels;

  static List<EconomicFeedChannel> get defaultFeedChannels {
    return _sources
        .map(
          (source) => EconomicFeedChannel(
            id: source.id,
            name: source.name,
            provider: source.provider,
            query: source.query,
            type: source.type,
            region: source.region,
            tags: source.tags,
            url: source.channelUrl,
          ),
        )
        .toList(growable: false);
  }

  @override
  void dispose() => _client.close();

  @override
  Future<EconomicFeedFetchResult> fetchFeeds() async {
    final cachedItems = await _readCachedFeeds();
    final results = await Future.wait(
      _sources.map((source) async {
        try {
          return _FeedQueryResult(await _fetchSource(source), null);
        } catch (error) {
          return _FeedQueryResult(const [], '${source.name}: $error');
        }
      }),
    );

    final errors = results
        .map((result) => result.error)
        .whereType<String>()
        .toList(growable: false);
    final items = GoogleNewsEconomicFeedService._dedupe(
      results.expand((result) => result.items).toList(growable: false),
    )..sort(GoogleNewsEconomicFeedService._compareFeedItems);
    final selected = items.take(_maxItems).toList(growable: false);
    if (selected.isNotEmpty) {
      await _writeFeedCache(selected);
    }
    if (selected.isEmpty && cachedItems.isNotEmpty) {
      return _cachedResult(
        cachedItems,
        '다중 채널 조회 실패 · 마지막 저장 피드 사용 · ${errors.take(2).join(' · ')}',
      );
    }

    final status = selected.isEmpty
        ? EconomicFeedFetchStatus.failed
        : errors.isEmpty
        ? EconomicFeedFetchStatus.ready
        : EconomicFeedFetchStatus.partial;
    final activeSources = _sources.length - errors.length;
    final message = switch (status) {
      EconomicFeedFetchStatus.ready =>
        '다중 채널 ${selected.length}건 · ${_sources.length}채널 갱신',
      EconomicFeedFetchStatus.partial =>
        '다중 채널 일부 갱신 ${selected.length}건 · $activeSources/${_sources.length}채널',
      EconomicFeedFetchStatus.failed =>
        '다중 채널 조회 실패 · ${errors.take(2).join(' · ')}',
      _ => '다중 채널 상태 확인',
    };

    return EconomicFeedFetchResult(
      items: selected,
      snapshot: EconomicFeedFetchSnapshot(
        provider: provider,
        endpoint: endpoint,
        status: status,
        message: message,
        itemCount: selected.length,
        updatedAt: DateTime.now(),
      ),
    );
  }

  Future<List<EconomicFeedItem>> _fetchSource(
    _EconomicFeedSource source,
  ) async {
    final uri = Uri.parse(source.feedUrl);
    final body = await _fetchBody(uri, source.format);
    return switch (source.format) {
      _EconomicFeedSourceFormat.gdelt => _parseGdeltArticles(body, source),
      _EconomicFeedSourceFormat.rss => _parseRssItems(body, source),
    };
  }

  Future<String> _fetchBody(Uri uri, _EconomicFeedSourceFormat format) async {
    try {
      return await _fetchUri(uri, format);
    } catch (directError) {
      try {
        return await _fetchUri(_localProxyUri(uri, format), format);
      } catch (proxyError) {
        throw FormatException('$directError · proxy: $proxyError');
      }
    }
  }

  Future<String> _fetchUri(Uri uri, _EconomicFeedSourceFormat format) async {
    final response = await _client
        .get(
          uri,
          headers: {
            'Accept': format == _EconomicFeedSourceFormat.gdelt
                ? 'application/json, text/plain;q=0.9, */*;q=0.8'
                : 'application/rss+xml, application/xml;q=0.9, */*;q=0.8',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'User-Agent': 'MarketFlow/1.0',
          },
        )
        .timeout(const Duration(seconds: 12));
    if (response.statusCode != 200) {
      throw FormatException('HTTP ${response.statusCode}');
    }
    return utf8.decode(response.bodyBytes);
  }

  Uri _localProxyUri(Uri uri, _EconomicFeedSourceFormat format) {
    final base = Uri.parse(_localProxyBaseUrl);
    return base.replace(
      path: format == _EconomicFeedSourceFormat.gdelt
          ? '/api/economic-feed/gdelt'
          : '/api/economic-feed/rss',
      queryParameters: {'url': uri.toString()},
    );
  }

  List<EconomicFeedItem> _parseRssItems(
    String body,
    _EconomicFeedSource source,
  ) {
    final blocks = RegExp(
      r'<item\b[\s\S]*?<\/item>',
      caseSensitive: false,
    ).allMatches(body);
    return blocks
        .take(_itemsPerSource)
        .map((match) => _parseRssItem(match.group(0) ?? '', source))
        .whereType<EconomicFeedItem>()
        .toList(growable: false);
  }

  EconomicFeedItem? _parseRssItem(String block, _EconomicFeedSource source) {
    final title = GoogleNewsEconomicFeedService._readXmlText(block, 'title');
    final link = GoogleNewsEconomicFeedService._readXmlText(block, 'link');
    final pubDate = GoogleNewsEconomicFeedService._readXmlText(
      block,
      'pubDate',
    );
    final description =
        GoogleNewsEconomicFeedService._readXmlText(block, 'description').isEmpty
        ? GoogleNewsEconomicFeedService._readXmlText(block, 'content:encoded')
        : GoogleNewsEconomicFeedService._readXmlText(block, 'description');
    if (title.isEmpty || link.isEmpty) {
      return null;
    }
    final uri = Uri.tryParse(link);
    if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
      return null;
    }

    final publishedAt = GoogleNewsEconomicFeedService._parseRssDate(pubDate);
    final summary = _summaryFromDescription(description, title, source);
    final query = _queryForSource(source);
    return EconomicFeedItem(
      id: 'rss-${source.id}-${link.hashCode.abs()}',
      type: query.type,
      region: query.region,
      title: title,
      summary: summary,
      source: source.provider,
      timestampLabel: GoogleNewsEconomicFeedService._formatTimestamp(
        publishedAt,
        pubDate,
      ),
      impactScore: GoogleNewsEconomicFeedService._impactScore(
        query,
        title,
        summary,
        publishedAt,
      ),
      tags: source.tags,
      url: link,
      channelId: source.id,
      channelName: source.name,
      publishedAt: publishedAt,
    );
  }

  List<EconomicFeedItem> _parseGdeltArticles(
    String body,
    _EconomicFeedSource source,
  ) {
    final decoded = jsonDecode(body);
    if (decoded is! Map<String, dynamic>) {
      return const [];
    }
    final articles = decoded['articles'];
    if (articles is! List) {
      return const [];
    }
    return articles
        .whereType<Map<String, dynamic>>()
        .map((article) => _parseGdeltArticle(article, source))
        .whereType<EconomicFeedItem>()
        .take(_itemsPerSource)
        .toList(growable: false);
  }

  EconomicFeedItem? _parseGdeltArticle(
    Map<String, dynamic> article,
    _EconomicFeedSource source,
  ) {
    final title = '${article['title'] ?? ''}'.trim();
    final url = '${article['url'] ?? ''}'.trim();
    if (title.isEmpty || url.isEmpty) {
      return null;
    }
    final uri = Uri.tryParse(url);
    if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
      return null;
    }

    final domain = '${article['domain'] ?? uri.host}'.trim();
    final language = '${article['language'] ?? ''}'.trim();
    final sourceCountry = '${article['sourcecountry'] ?? ''}'.trim();
    final publishedAt = GdeltNewsEconomicFeedService._parseGdeltDate(
      '${article['seendate'] ?? ''}',
    );
    final summary = [
      'GDELT가 수집한 ${domain.isEmpty ? '원문 매체' : domain} 기사입니다.',
      if (language.isNotEmpty) '언어 $language',
      if (sourceCountry.isNotEmpty) '국가 $sourceCountry',
    ].join(' · ');
    final query = _queryForSource(source);

    return EconomicFeedItem(
      id: 'gdelt-${source.id}-${url.hashCode.abs()}',
      type: query.type,
      region: query.region,
      title: title,
      summary: summary,
      source: domain.isEmpty ? source.provider : domain,
      timestampLabel: GoogleNewsEconomicFeedService._formatTimestamp(
        publishedAt,
        '${article['seendate'] ?? ''}',
      ),
      impactScore: GoogleNewsEconomicFeedService._impactScore(
        query,
        title,
        summary,
        publishedAt,
      ),
      tags: source.tags,
      url: url,
      channelId: source.id,
      channelName: source.name,
      publishedAt: publishedAt,
    );
  }

  String _summaryFromDescription(
    String description,
    String title,
    _EconomicFeedSource source,
  ) {
    final normalized = description
        .replaceAll(title, '')
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();
    if (normalized.length >= 36) {
      return normalized.length > 180
          ? '${normalized.substring(0, 177)}...'
          : normalized;
    }
    return '${source.provider} ${source.name}에서 수집한 최신 기사입니다.';
  }

  Future<void> _writeFeedCache(List<EconomicFeedItem> items) async {
    if (items.isEmpty) {
      return;
    }
    final payload = {
      'provider': provider,
      'endpoint': endpoint,
      'cachedAt': DateTime.now().toUtc().toIso8601String(),
      'items': [for (final item in items) _feedToJson(item)],
    };
    await _database.writeString(
      LocalSettingsDatabase.apiCacheStorageKey(_cacheId),
      jsonEncode(payload),
    );
  }

  Future<List<EconomicFeedItem>> _readCachedFeeds() async {
    final raw = await _database.readString(
      LocalSettingsDatabase.apiCacheStorageKey(_cacheId),
    );
    if (raw == null || raw.isEmpty) {
      return const [];
    }
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map<String, dynamic>) {
        return const [];
      }
      final items = decoded['items'];
      if (items is! List) {
        return const [];
      }
      final feeds = items
          .whereType<Map<String, dynamic>>()
          .map(_feedFromJson)
          .whereType<EconomicFeedItem>()
          .toList(growable: false);
      return feeds..sort(GoogleNewsEconomicFeedService._compareFeedItems);
    } catch (_) {
      return const [];
    }
  }

  EconomicFeedFetchResult _cachedResult(
    List<EconomicFeedItem> items,
    String message,
  ) {
    final latest = items
        .map((item) => item.publishedAt)
        .whereType<DateTime>()
        .fold<DateTime?>(null, (latest, value) {
          if (latest == null || value.isAfter(latest)) {
            return value;
          }
          return latest;
        });
    return EconomicFeedFetchResult(
      items: items.take(12).toList(growable: false),
      snapshot: EconomicFeedFetchSnapshot(
        provider: '$provider cache',
        endpoint: endpoint,
        status: EconomicFeedFetchStatus.cached,
        message: message,
        itemCount: items.length,
        updatedAt: latest,
      ),
    );
  }

  Map<String, dynamic> _feedToJson(EconomicFeedItem item) {
    return {
      'id': item.id,
      'type': item.type.name,
      'region': item.region.name,
      'title': item.title,
      'summary': item.summary,
      'source': item.source,
      'timestampLabel': item.timestampLabel,
      'impactScore': item.impactScore,
      'tags': item.tags,
      'url': item.url,
      'channelId': item.channelId,
      'channelName': item.channelName,
      'publishedAt': item.publishedAt?.toUtc().toIso8601String(),
    };
  }

  EconomicFeedItem? _feedFromJson(Map<String, dynamic> json) {
    final id = '${json['id'] ?? ''}';
    final title = '${json['title'] ?? ''}';
    if (id.isEmpty || title.isEmpty) {
      return null;
    }
    return EconomicFeedItem(
      id: id,
      type: GoogleNewsEconomicFeedService._enumByName(
        EconomicFeedType.values,
        '${json['type'] ?? ''}',
        EconomicFeedType.macro,
      ),
      region: GoogleNewsEconomicFeedService._enumByName(
        MarketRegion.values,
        '${json['region'] ?? ''}',
        MarketRegion.all,
      ),
      title: title,
      summary: '${json['summary'] ?? ''}',
      source: '${json['source'] ?? provider} cache',
      timestampLabel: '${json['timestampLabel'] ?? 'cached'}',
      impactScore: int.tryParse('${json['impactScore'] ?? ''}') ?? 0,
      tags: (json['tags'] is List)
          ? (json['tags'] as List).map((tag) => '$tag').toList(growable: false)
          : const [],
      url: '${json['url'] ?? ''}',
      channelId: '${json['channelId'] ?? ''}',
      channelName: '${json['channelName'] ?? ''}',
      publishedAt: DateTime.tryParse('${json['publishedAt'] ?? ''}'),
    );
  }

  _EconomicFeedQuery _queryForSource(_EconomicFeedSource source) {
    return _EconomicFeedQuery(
      id: source.id,
      name: source.name,
      search: source.query,
      type: source.type,
      region: source.region,
      tags: source.tags,
      baseImpact: source.baseImpact,
    );
  }

  static Uri _gdeltArticlesUri() {
    return Uri.https(
      'api.gdeltproject.org',
      GdeltNewsEconomicFeedService.endpoint,
      {
        'query': _gdeltSearch,
        'mode': 'ArtList',
        'format': 'JSON',
        'maxrecords': '40',
        'timespan': '3d',
        'sort': 'DateDesc',
      },
    );
  }

  static Uri _gdeltSearchUri() {
    return Uri.https(
      'api.gdeltproject.org',
      GdeltNewsEconomicFeedService.endpoint,
      {
        'query': _gdeltSearch,
        'mode': 'ArtList',
        'format': 'HTML',
        'timespan': '3d',
        'sort': 'DateDesc',
      },
    );
  }
}

class GdeltNewsEconomicFeedService implements EconomicFeedService {
  GdeltNewsEconomicFeedService({http.Client? client})
    : _client = client ?? http.Client();

  static const provider = 'GDELT DOC API';
  static const endpoint = '/api/v2/doc/doc';
  static const _localProxyBaseUrl = String.fromEnvironment(
    'MARKET_FLOW_FEED_PROXY_BASE_URL',
    defaultValue: 'http://127.0.0.1:3000',
  );
  static const _maxItems = 24;
  static const _combinedSearch =
      '"stock market" OR liquidity OR semiconductor OR infrastructure OR '
      '"central bank" OR earnings OR dollar OR cryptocurrency OR electricity OR Korea';

  static const _queries = [
    _EconomicFeedQuery(
      id: 'market-liquidity',
      name: '시장 유동성',
      search: 'liquidity dollar rates stocks treasury',
      type: EconomicFeedType.liquidity,
      region: MarketRegion.all,
      tags: ['liquidity', 'dollar', 'rates', 'treasury'],
      baseImpact: 82,
    ),
    _EconomicFeedQuery(
      id: 'ai-infrastructure',
      name: 'AI 인프라',
      search: 'semiconductor datacenter electricity infrastructure investment',
      type: EconomicFeedType.flow,
      region: MarketRegion.unitedStates,
      tags: ['semiconductor', 'datacenter', 'electricity', 'infrastructure'],
      baseImpact: 86,
    ),
    _EconomicFeedQuery(
      id: 'korea-market',
      name: '한국 시장',
      search: 'Korea KOSPI foreign investors semiconductor defense stocks',
      type: EconomicFeedType.flow,
      region: MarketRegion.korea,
      tags: ['Korea', 'KOSPI', 'foreign', 'semiconductor'],
      baseImpact: 78,
    ),
    _EconomicFeedQuery(
      id: 'central-bank-policy',
      name: '중앙은행 정책',
      search: 'central bank inflation interest rates monetary policy',
      type: EconomicFeedType.policy,
      region: MarketRegion.all,
      tags: ['central bank', 'inflation', 'rates', 'policy'],
      baseImpact: 75,
    ),
    _EconomicFeedQuery(
      id: 'earnings-margin',
      name: '실적/마진',
      search: 'earnings margin guidance revenue stocks',
      type: EconomicFeedType.earnings,
      region: MarketRegion.unitedStates,
      tags: ['earnings', 'margin', 'guidance', 'revenue'],
      baseImpact: 70,
    ),
    _EconomicFeedQuery(
      id: 'risk-assets',
      name: '리스크 자산',
      search: 'volatility gold dollar credit risk assets',
      type: EconomicFeedType.risk,
      region: MarketRegion.all,
      tags: ['volatility', 'gold', 'credit', 'risk'],
      baseImpact: 73,
    ),
    _EconomicFeedQuery(
      id: 'crypto-liquidity',
      name: '코인 유동성',
      search: 'bitcoin cryptocurrency stablecoin liquidity market',
      type: EconomicFeedType.macro,
      region: MarketRegion.all,
      tags: ['bitcoin', 'cryptocurrency', 'stablecoin'],
      baseImpact: 70,
    ),
  ];

  final http.Client _client;

  @override
  List<EconomicFeedChannel> get feedChannels => defaultFeedChannels;

  static List<EconomicFeedChannel> get defaultFeedChannels {
    return _queries
        .map(
          (query) => EconomicFeedChannel(
            id: query.id,
            name: query.name,
            provider: provider,
            query: query.displayQuery,
            type: query.type,
            region: query.region,
            tags: query.tags,
            url: _gdeltSearchUri(query.search).toString(),
          ),
        )
        .toList(growable: false);
  }

  @override
  void dispose() => _client.close();

  @override
  Future<EconomicFeedFetchResult> fetchFeeds() async {
    try {
      final body = await _fetchJsonBody(_gdeltArticlesUri());
      final items = _parseArticles(body)
        ..sort(GoogleNewsEconomicFeedService._compareFeedItems);
      final selected = GoogleNewsEconomicFeedService._dedupe(
        items,
      ).take(_maxItems).toList(growable: false);
      return EconomicFeedFetchResult(
        items: selected,
        snapshot: EconomicFeedFetchSnapshot(
          provider: provider,
          endpoint: endpoint,
          status: selected.isEmpty
              ? EconomicFeedFetchStatus.failed
              : EconomicFeedFetchStatus.ready,
          message: selected.isEmpty
              ? 'GDELT 기사 결과 없음'
              : 'GDELT 직접 기사 ${selected.length}건 갱신',
          itemCount: selected.length,
          updatedAt: DateTime.now(),
        ),
      );
    } catch (error) {
      return EconomicFeedFetchResult(
        items: const [],
        snapshot: EconomicFeedFetchSnapshot(
          provider: provider,
          endpoint: endpoint,
          status: EconomicFeedFetchStatus.failed,
          message: 'GDELT 기사 조회 실패 · $error',
          itemCount: 0,
          updatedAt: DateTime.now(),
        ),
      );
    }
  }

  Future<String> _fetchJsonBody(Uri uri) async {
    try {
      return await _fetchUri(uri);
    } catch (directError) {
      try {
        return await _fetchUri(_localProxyUri(uri));
      } catch (proxyError) {
        throw FormatException('$directError · proxy: $proxyError');
      }
    }
  }

  Future<String> _fetchUri(Uri uri) async {
    final response = await _client
        .get(
          uri,
          headers: const {
            'Accept': 'application/json, text/plain;q=0.9, */*;q=0.8',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'User-Agent': 'MarketFlow/1.0',
          },
        )
        .timeout(const Duration(seconds: 12));
    if (response.statusCode != 200) {
      throw FormatException('HTTP ${response.statusCode}');
    }
    return utf8.decode(response.bodyBytes);
  }

  Uri _localProxyUri(Uri uri) {
    final base = Uri.parse(_localProxyBaseUrl);
    return base.replace(
      path: '/api/economic-feed/gdelt',
      queryParameters: {'url': uri.toString()},
    );
  }

  List<EconomicFeedItem> _parseArticles(String body) {
    final decoded = jsonDecode(body);
    if (decoded is! Map<String, dynamic>) {
      return const [];
    }
    final articles = decoded['articles'];
    if (articles is! List) {
      return const [];
    }
    return articles
        .whereType<Map<String, dynamic>>()
        .map(_parseArticle)
        .whereType<EconomicFeedItem>()
        .toList(growable: false);
  }

  EconomicFeedItem? _parseArticle(Map<String, dynamic> article) {
    final title = '${article['title'] ?? ''}'.trim();
    final url = '${article['url'] ?? ''}'.trim();
    if (title.isEmpty || url.isEmpty) {
      return null;
    }
    final uri = Uri.tryParse(url);
    if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
      return null;
    }

    final domain = '${article['domain'] ?? uri.host}'.trim();
    final language = '${article['language'] ?? ''}'.trim();
    final sourceCountry = '${article['sourcecountry'] ?? ''}'.trim();
    final publishedAt = _parseGdeltDate('${article['seendate'] ?? ''}');
    final query = _classifyArticle(title, domain);
    final summary = [
      'GDELT가 수집한 ${domain.isEmpty ? '원문 매체' : domain} 기사입니다.',
      if (language.isNotEmpty) '언어 $language',
      if (sourceCountry.isNotEmpty) '국가 $sourceCountry',
    ].join(' · ');

    return EconomicFeedItem(
      id: 'gdelt-${query.id}-${url.hashCode.abs()}',
      type: query.type,
      region: query.region,
      title: title,
      summary: summary,
      source: domain.isEmpty ? provider : domain,
      timestampLabel: GoogleNewsEconomicFeedService._formatTimestamp(
        publishedAt,
        '${article['seendate'] ?? ''}',
      ),
      impactScore: GoogleNewsEconomicFeedService._impactScore(
        query,
        title,
        summary,
        publishedAt,
      ),
      tags: query.tags,
      url: url,
      channelId: query.id,
      channelName: query.name,
      publishedAt: publishedAt,
    );
  }

  _EconomicFeedQuery _classifyArticle(String title, String domain) {
    final haystack = '$title $domain'.toLowerCase();
    var bestScore = -1;
    var best = _queries.first;
    for (final query in _queries) {
      var score = 0;
      for (final tag in query.tags) {
        if (haystack.contains(tag.toLowerCase())) {
          score += 3;
        }
      }
      for (final token in query.search.toLowerCase().split(RegExp(r'\s+'))) {
        if (token.length >= 5 && haystack.contains(token)) {
          score += 1;
        }
      }
      if (score > bestScore) {
        bestScore = score;
        best = query;
      }
    }
    return best;
  }

  static Uri _gdeltArticlesUri() {
    return Uri.https('api.gdeltproject.org', endpoint, {
      'query': _combinedSearch,
      'mode': 'ArtList',
      'format': 'JSON',
      'maxrecords': '75',
      'timespan': '3d',
      'sort': 'DateDesc',
    });
  }

  static Uri _gdeltSearchUri(String query) {
    return Uri.https('api.gdeltproject.org', endpoint, {
      'query': query,
      'mode': 'ArtList',
      'format': 'HTML',
      'timespan': '3d',
      'sort': 'DateDesc',
    });
  }

  static DateTime? _parseGdeltDate(String value) {
    final digits = value.replaceAll(RegExp(r'\D'), '');
    if (digits.length < 14) {
      return DateTime.tryParse(value)?.toLocal();
    }
    return DateTime.utc(
      int.parse(digits.substring(0, 4)),
      int.parse(digits.substring(4, 6)),
      int.parse(digits.substring(6, 8)),
      int.parse(digits.substring(8, 10)),
      int.parse(digits.substring(10, 12)),
      int.parse(digits.substring(12, 14)),
    ).toLocal();
  }
}

class GoogleNewsEconomicFeedService implements EconomicFeedService {
  GoogleNewsEconomicFeedService({
    http.Client? client,
    LocalSettingsDatabase? database,
  }) : _client = client ?? http.Client(),
       _database = database ?? const LocalSettingsDatabase();

  static const provider = 'Google News RSS';
  static const endpoint = '/rss/search';
  static const _cacheId = 'google-news-rss.feeds';
  static const _localProxyBaseUrl = String.fromEnvironment(
    'MARKET_FLOW_FEED_PROXY_BASE_URL',
    defaultValue: 'http://127.0.0.1:3000',
  );
  static const _refreshQueryParameter = '_refresh';
  static const _itemsPerQuery = 5;
  static const _maxItems = 24;

  static const _queries = [
    _EconomicFeedQuery(
      id: 'us-liquidity',
      name: '미국 유동성',
      search: '미국 금리 달러 유동성 주식 시장 when:2d',
      type: EconomicFeedType.liquidity,
      region: MarketRegion.unitedStates,
      tags: ['금리', '달러', '유동성'],
      baseImpact: 82,
    ),
    _EconomicFeedQuery(
      id: 'ai-capex',
      name: 'AI CAPEX',
      search: 'AI capex 반도체 전력 인프라 데이터센터 주식 when:7d',
      type: EconomicFeedType.flow,
      region: MarketRegion.unitedStates,
      tags: ['AI', '전력', '인프라'],
      baseImpact: 86,
    ),
    _EconomicFeedQuery(
      id: 'kr-foreign-flow',
      name: '한국 수급',
      search: '코스피 외국인 순매수 반도체 방산 수급 when:3d',
      type: EconomicFeedType.flow,
      region: MarketRegion.korea,
      tags: ['KOSPI', '외국인', '수급'],
      baseImpact: 78,
    ),
    _EconomicFeedQuery(
      id: 'central-bank-policy',
      name: '중앙은행 정책',
      search: '연준 한국은행 금리 인하 물가 중앙은행 when:7d',
      type: EconomicFeedType.policy,
      region: MarketRegion.all,
      tags: ['중앙은행', '물가', '정책'],
      baseImpact: 75,
    ),
    _EconomicFeedQuery(
      id: 'earnings-margin',
      name: '실적/마진',
      search: '미국 실적 시즌 마진 가이던스 주식 when:3d',
      type: EconomicFeedType.earnings,
      region: MarketRegion.unitedStates,
      tags: ['실적', '마진', '가이던스'],
      baseImpact: 68,
    ),
    _EconomicFeedQuery(
      id: 'risk-assets',
      name: '위험자산',
      search: '금 달러 변동성 위험자산 주식 시장 when:3d',
      type: EconomicFeedType.risk,
      region: MarketRegion.all,
      tags: ['금', '달러', '변동성'],
      baseImpact: 72,
    ),
    _EconomicFeedQuery(
      id: 'crypto-liquidity',
      name: '코인 유동성',
      search: '비트코인 스테이블코인 유동성 crypto market when:7d',
      type: EconomicFeedType.macro,
      region: MarketRegion.all,
      tags: ['BTC', '스테이블코인', '코인'],
      baseImpact: 70,
    ),
    _EconomicFeedQuery(
      id: 'global-etf-flow',
      name: 'ETF 자금',
      search: 'ETF fund flow 주식 채권 자금 유입 유출 when:3d',
      type: EconomicFeedType.flow,
      region: MarketRegion.all,
      tags: ['ETF', '자금', '채권'],
      baseImpact: 76,
    ),
    _EconomicFeedQuery(
      id: 'fx-carry',
      name: '환율/캐리',
      search: '달러 엔화 원화 환율 carry trade 증시 when:3d',
      type: EconomicFeedType.risk,
      region: MarketRegion.all,
      tags: ['환율', '엔화', '캐리'],
      baseImpact: 74,
    ),
    _EconomicFeedQuery(
      id: 'energy-grid',
      name: '전력/에너지',
      search: '전력망 전력기기 구리 에너지 인프라 AI 데이터센터 when:7d',
      type: EconomicFeedType.flow,
      region: MarketRegion.all,
      tags: ['전력', '구리', '에너지'],
      baseImpact: 81,
    ),
    _EconomicFeedQuery(
      id: 'korea-valueup',
      name: '밸류업/주주환원',
      search: '코리아 밸류업 자사주 배당 주주환원 외국인 when:7d',
      type: EconomicFeedType.policy,
      region: MarketRegion.korea,
      tags: ['밸류업', '배당', '주주환원'],
      baseImpact: 77,
    ),
    _EconomicFeedQuery(
      id: 'credit-stress',
      name: '크레딧/부동산',
      search: '크레딧 스프레드 회사채 상업용 부동산 은행 리스크 when:7d',
      type: EconomicFeedType.risk,
      region: MarketRegion.all,
      tags: ['크레딧', '부동산', '은행'],
      baseImpact: 73,
    ),
  ];

  static List<EconomicFeedChannel> get defaultFeedChannels {
    return _queries
        .map(
          (query) => EconomicFeedChannel(
            id: query.id,
            name: query.name,
            provider: provider,
            query: query.displayQuery,
            type: query.type,
            region: query.region,
            tags: query.tags,
            url: _googleNewsSearchUri(query).toString(),
          ),
        )
        .toList(growable: false);
  }

  final http.Client _client;
  final LocalSettingsDatabase _database;

  @override
  List<EconomicFeedChannel> get feedChannels => defaultFeedChannels;

  @override
  void dispose() => _client.close();

  @override
  Future<EconomicFeedFetchResult> fetchFeeds() async {
    final cachedItems = await _readCachedFeeds();
    final refreshToken = DateTime.now().millisecondsSinceEpoch.toString();
    final results = await Future.wait(
      _queries.map((query) async {
        try {
          return _FeedQueryResult(await _fetchQuery(query, refreshToken), null);
        } catch (error) {
          return _FeedQueryResult(const [], '${query.id}: $error');
        }
      }),
    );

    final errors = results
        .map((result) => result.error)
        .whereType<String>()
        .toList(growable: false);
    final items = _dedupe(
      results.expand((result) => result.items).toList(growable: false),
    )..sort(_compareFeedItems);
    final selected = items.take(_maxItems).toList(growable: false);
    if (selected.isNotEmpty) {
      await _writeFeedCache(selected);
    }
    if (selected.isEmpty && cachedItems.isNotEmpty) {
      return _cachedResult(
        cachedItems,
        'RSS 조회 실패 · 마지막 저장 피드 사용 · ${errors.take(2).join(' · ')}',
      );
    }
    final status = selected.isEmpty
        ? EconomicFeedFetchStatus.failed
        : errors.isEmpty
        ? EconomicFeedFetchStatus.ready
        : EconomicFeedFetchStatus.partial;
    final message = switch (status) {
      EconomicFeedFetchStatus.ready =>
        'Google News RSS ${selected.length}건 · ${_queries.length}채널 갱신',
      EconomicFeedFetchStatus.partial =>
        'RSS 일부 갱신 ${selected.length}건 · ${errors.take(2).join(' · ')}',
      EconomicFeedFetchStatus.failed =>
        'RSS 조회 실패 · ${errors.take(2).join(' · ')}',
      _ => 'RSS 상태 확인',
    };

    return EconomicFeedFetchResult(
      items: selected,
      snapshot: EconomicFeedFetchSnapshot(
        provider: provider,
        endpoint: endpoint,
        status: status,
        message: message,
        itemCount: selected.length,
        updatedAt: DateTime.now(),
      ),
    );
  }

  Future<void> _writeFeedCache(List<EconomicFeedItem> items) async {
    if (items.isEmpty) {
      return;
    }
    final payload = {
      'provider': provider,
      'endpoint': endpoint,
      'cachedAt': DateTime.now().toUtc().toIso8601String(),
      'items': [for (final item in items) _feedToJson(item)],
    };
    await _database.writeString(
      LocalSettingsDatabase.apiCacheStorageKey(_cacheId),
      jsonEncode(payload),
    );
  }

  Future<List<EconomicFeedItem>> _readCachedFeeds() async {
    final raw = await _database.readString(
      LocalSettingsDatabase.apiCacheStorageKey(_cacheId),
    );
    if (raw == null || raw.isEmpty) {
      return const [];
    }
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map<String, dynamic>) {
        return const [];
      }
      final items = decoded['items'];
      if (items is! List) {
        return const [];
      }
      final feeds = items
          .whereType<Map<String, dynamic>>()
          .map(_feedFromJson)
          .whereType<EconomicFeedItem>()
          .toList(growable: false);
      return feeds..sort(_compareFeedItems);
    } catch (_) {
      return const [];
    }
  }

  EconomicFeedFetchResult _cachedResult(
    List<EconomicFeedItem> items,
    String message,
  ) {
    final latest = items
        .map((item) => item.publishedAt)
        .whereType<DateTime>()
        .fold<DateTime?>(null, (latest, value) {
          if (latest == null || value.isAfter(latest)) {
            return value;
          }
          return latest;
        });
    return EconomicFeedFetchResult(
      items: items.take(12).toList(growable: false),
      snapshot: EconomicFeedFetchSnapshot(
        provider: '$provider cache',
        endpoint: endpoint,
        status: EconomicFeedFetchStatus.cached,
        message: message,
        itemCount: items.length,
        updatedAt: latest,
      ),
    );
  }

  Map<String, dynamic> _feedToJson(EconomicFeedItem item) {
    return {
      'id': item.id,
      'type': item.type.name,
      'region': item.region.name,
      'title': item.title,
      'summary': item.summary,
      'source': item.source,
      'timestampLabel': item.timestampLabel,
      'impactScore': item.impactScore,
      'tags': item.tags,
      'url': item.url,
      'channelId': item.channelId,
      'channelName': item.channelName,
      'publishedAt': item.publishedAt?.toUtc().toIso8601String(),
    };
  }

  EconomicFeedItem? _feedFromJson(Map<String, dynamic> json) {
    final id = '${json['id'] ?? ''}';
    final title = '${json['title'] ?? ''}';
    if (id.isEmpty || title.isEmpty) {
      return null;
    }
    return EconomicFeedItem(
      id: id,
      type: _enumByName(
        EconomicFeedType.values,
        '${json['type'] ?? ''}',
        EconomicFeedType.macro,
      ),
      region: _enumByName(
        MarketRegion.values,
        '${json['region'] ?? ''}',
        MarketRegion.all,
      ),
      title: title,
      summary: '${json['summary'] ?? ''}',
      source: '${json['source'] ?? provider} cache',
      timestampLabel: '${json['timestampLabel'] ?? 'cached'}',
      impactScore: int.tryParse('${json['impactScore'] ?? ''}') ?? 0,
      tags: (json['tags'] is List)
          ? (json['tags'] as List).map((tag) => '$tag').toList(growable: false)
          : const [],
      url: '${json['url'] ?? ''}',
      channelId: '${json['channelId'] ?? ''}',
      channelName: '${json['channelName'] ?? ''}',
      publishedAt: DateTime.tryParse('${json['publishedAt'] ?? ''}'),
    );
  }

  Future<List<EconomicFeedItem>> _fetchQuery(
    _EconomicFeedQuery query,
    String refreshToken,
  ) async {
    final uri = _googleNewsRssUri(query, refreshToken: refreshToken);
    final body = await _fetchRssBody(uri);
    final blocks = RegExp(
      r'<item\b[\s\S]*?<\/item>',
      caseSensitive: false,
    ).allMatches(body);
    return blocks
        .take(_itemsPerQuery)
        .map((match) => _parseItem(match.group(0) ?? '', query))
        .whereType<EconomicFeedItem>()
        .toList(growable: false);
  }

  Future<String> _fetchRssBody(Uri uri) async {
    try {
      return await _fetchRssUri(uri);
    } catch (directError) {
      try {
        return await _fetchRssUri(_localProxyUri(uri));
      } catch (proxyError) {
        throw FormatException('$directError · proxy: $proxyError');
      }
    }
  }

  Future<String> _fetchRssUri(Uri uri) async {
    final response = await _client
        .get(
          uri,
          headers: const {
            'Accept': 'application/rss+xml, application/xml;q=0.9, */*;q=0.8',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'User-Agent': 'MarketFlow/1.0',
          },
        )
        .timeout(const Duration(seconds: 10));
    if (response.statusCode != 200) {
      throw FormatException('HTTP ${response.statusCode}');
    }
    return utf8.decode(response.bodyBytes);
  }

  Uri _localProxyUri(Uri uri) {
    final base = Uri.parse(_localProxyBaseUrl);
    return base.replace(
      path: '/api/economic-feed/rss',
      queryParameters: {'url': uri.toString()},
    );
  }

  EconomicFeedItem? _parseItem(String block, _EconomicFeedQuery query) {
    final title = _readXmlText(block, 'title');
    final link = _readXmlText(block, 'link');
    final source = _readXmlText(block, 'source');
    final pubDate = _readXmlText(block, 'pubDate');
    final description = _readXmlText(block, 'description');
    if (title.isEmpty || link.isEmpty) {
      return null;
    }

    final publishedAt = _parseRssDate(pubDate);
    final summary = _summaryFromDescription(description, title);
    return EconomicFeedItem(
      id: 'rss-${query.id}-${title.hashCode.abs()}',
      type: query.type,
      region: query.region,
      title: title,
      summary: summary,
      source: source.isEmpty ? provider : source,
      timestampLabel: _formatTimestamp(publishedAt, pubDate),
      impactScore: _impactScore(query, title, summary, publishedAt),
      tags: query.tags,
      url: link,
      channelId: query.id,
      channelName: query.name,
      publishedAt: publishedAt,
    );
  }

  static Uri _googleNewsRssUri(
    _EconomicFeedQuery query, {
    String? refreshToken,
  }) {
    final parameters = {
      'q': query.search,
      'hl': 'ko',
      'gl': 'KR',
      'ceid': 'KR:ko',
      if (refreshToken != null && refreshToken.isNotEmpty)
        _refreshQueryParameter: refreshToken,
    };
    return Uri.https('news.google.com', endpoint, parameters);
  }

  static Uri _googleNewsSearchUri(_EconomicFeedQuery query) {
    return Uri.https('news.google.com', '/search', {
      'q': query.displayQuery,
      'hl': 'ko',
      'gl': 'KR',
      'ceid': 'KR:ko',
    });
  }

  static List<EconomicFeedItem> _dedupe(List<EconomicFeedItem> items) {
    final seen = <String>{};
    final deduped = <EconomicFeedItem>[];
    for (final item in items) {
      final key = item.title.toLowerCase().replaceAll(RegExp(r'\s+'), ' ');
      if (seen.add(key)) {
        deduped.add(item);
      }
    }
    return deduped;
  }

  static int _compareFeedItems(EconomicFeedItem a, EconomicFeedItem b) {
    final aDate = a.publishedAt;
    final bDate = b.publishedAt;
    if (aDate != null && bDate != null) {
      final dateCompare = bDate.compareTo(aDate);
      if (dateCompare != 0) {
        return dateCompare;
      }
    }
    if (aDate != null) {
      return -1;
    }
    if (bDate != null) {
      return 1;
    }
    final impactCompare = b.impactScore.compareTo(a.impactScore);
    if (impactCompare != 0) {
      return impactCompare;
    }
    return a.title.compareTo(b.title);
  }

  static String _readXmlText(String block, String tagName) {
    final match = RegExp(
      '<$tagName(?:\\s[^>]*)?>([\\s\\S]*?)<\\/$tagName>',
      caseSensitive: false,
    ).firstMatch(block);
    return _decodeXml(match?.group(1) ?? '');
  }

  static String _summaryFromDescription(String description, String title) {
    final normalized = description
        .replaceAll(title, '')
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();
    if (normalized.length >= 36) {
      return normalized.length > 180
          ? '${normalized.substring(0, 177)}...'
          : normalized;
    }
    return 'Google News RSS에서 수집한 최신 시장 기사입니다.';
  }

  static String _decodeXml(String value) {
    return value
        .replaceAllMapped(
          RegExp(r'<!\[CDATA\[([\s\S]*?)\]\]>'),
          (match) => match.group(1) ?? '',
        )
        .replaceAllMapped(RegExp(r'&#x([0-9a-fA-F]+);'), (match) {
          final code = int.tryParse(match.group(1) ?? '', radix: 16);
          return code == null ? '' : String.fromCharCode(code);
        })
        .replaceAllMapped(RegExp(r'&#(\d+);'), (match) {
          final code = int.tryParse(match.group(1) ?? '');
          return code == null ? '' : String.fromCharCode(code);
        })
        .replaceAll('&amp;', '&')
        .replaceAll('&lt;', '<')
        .replaceAll('&gt;', '>')
        .replaceAll('&quot;', '"')
        .replaceAll('&#39;', "'")
        .replaceAll('&nbsp;', ' ')
        .replaceAll(RegExp(r'<[^>]+>'), ' ')
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();
  }

  static DateTime? _parseRssDate(String value) {
    final match = RegExp(
      r'^(?:[A-Za-z]{3},\s*)?(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})',
    ).firstMatch(value.trim());
    if (match == null) {
      return null;
    }

    final month = const {
      'Jan': 1,
      'Feb': 2,
      'Mar': 3,
      'Apr': 4,
      'May': 5,
      'Jun': 6,
      'Jul': 7,
      'Aug': 8,
      'Sep': 9,
      'Oct': 10,
      'Nov': 11,
      'Dec': 12,
    }[match.group(2)];
    if (month == null) {
      return null;
    }

    return DateTime.utc(
      int.parse(match.group(3)!),
      month,
      int.parse(match.group(1)!),
      int.parse(match.group(4)!),
      int.parse(match.group(5)!),
      int.parse(match.group(6)!),
    ).toLocal();
  }

  static String _formatTimestamp(DateTime? publishedAt, String fallback) {
    if (publishedAt == null) {
      return fallback.isEmpty ? '시간 미상' : fallback;
    }

    final diff = DateTime.now().difference(publishedAt);
    if (diff.inMinutes < 1) {
      return '방금 전';
    }
    if (diff.inHours < 1) {
      return '${diff.inMinutes}분 전';
    }
    if (diff.inDays < 1) {
      return '${diff.inHours}시간 전';
    }
    if (diff.inDays < 7) {
      return '${diff.inDays}일 전';
    }
    return '${publishedAt.year}.${_twoDigits(publishedAt.month)}.${_twoDigits(publishedAt.day)}';
  }

  static String _twoDigits(int value) {
    return value.toString().padLeft(2, '0');
  }

  static int _impactScore(
    _EconomicFeedQuery query,
    String title,
    String summary,
    DateTime? publishedAt,
  ) {
    var score = query.baseImpact;
    final haystack = '$title $summary'.toLowerCase();
    for (final tag in query.tags) {
      if (haystack.contains(tag.toLowerCase())) {
        score += 2;
      }
    }
    if (publishedAt != null) {
      final age = DateTime.now().difference(publishedAt);
      if (age.inHours <= 6) {
        score += 6;
      } else if (age.inHours <= 24) {
        score += 3;
      }
    }
    return score.clamp(50, 99).toInt();
  }

  static T _enumByName<T extends Enum>(
    List<T> values,
    String name,
    T fallback,
  ) {
    for (final value in values) {
      if (value.name == name) {
        return value;
      }
    }
    return fallback;
  }
}

class _EconomicFeedQuery {
  const _EconomicFeedQuery({
    required this.id,
    required this.name,
    required this.search,
    required this.type,
    required this.region,
    required this.tags,
    required this.baseImpact,
  });

  final String id;
  final String name;
  final String search;
  final EconomicFeedType type;
  final MarketRegion region;
  final List<String> tags;
  final int baseImpact;

  String get displayQuery {
    return search.replaceAll(RegExp(r'\s*when:\d+d'), '').trim();
  }
}

class _FeedQueryResult {
  const _FeedQueryResult(this.items, this.error);

  final List<EconomicFeedItem> items;
  final String? error;
}

enum _EconomicFeedSourceFormat { rss, gdelt }

class _EconomicFeedSource {
  const _EconomicFeedSource({
    required this.id,
    required this.name,
    required this.provider,
    required this.feedUrl,
    required this.channelUrl,
    required this.query,
    required this.format,
    required this.type,
    required this.region,
    required this.tags,
    required this.baseImpact,
  });

  final String id;
  final String name;
  final String provider;
  final String feedUrl;
  final String channelUrl;
  final String query;
  final _EconomicFeedSourceFormat format;
  final EconomicFeedType type;
  final MarketRegion region;
  final List<String> tags;
  final int baseImpact;
}
