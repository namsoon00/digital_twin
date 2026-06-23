import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/market_models.dart';

enum EconomicFeedFetchStatus { idle, loading, ready, partial, failed }

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
      provider: GoogleNewsEconomicFeedService.provider,
      endpoint: GoogleNewsEconomicFeedService.endpoint,
      status: EconomicFeedFetchStatus.idle,
      message: '실제 RSS 피드 대기 중',
      itemCount: itemCount,
      updatedAt: null,
    );
  }

  factory EconomicFeedFetchSnapshot.loading(int itemCount) {
    return EconomicFeedFetchSnapshot(
      provider: GoogleNewsEconomicFeedService.provider,
      endpoint: GoogleNewsEconomicFeedService.endpoint,
      status: EconomicFeedFetchStatus.loading,
      message: 'Google News RSS 조회 중',
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
      EconomicFeedFetchStatus.idle => 'RSS 대기',
      EconomicFeedFetchStatus.loading => 'RSS 조회',
      EconomicFeedFetchStatus.ready => '실제 RSS',
      EconomicFeedFetchStatus.partial => 'RSS 일부',
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

class GoogleNewsEconomicFeedService implements EconomicFeedService {
  GoogleNewsEconomicFeedService({http.Client? client})
    : _client = client ?? http.Client();

  static const provider = 'Google News RSS';
  static const endpoint = '/rss/search';
  static const _localProxyBaseUrl = String.fromEnvironment(
    'MARKET_FLOW_FEED_PROXY_BASE_URL',
    defaultValue: 'http://127.0.0.1:3000',
  );
  static const _refreshQueryParameter = '_refresh';

  static const _queries = [
    _EconomicFeedQuery(
      id: 'us-liquidity',
      name: '미국 유동성',
      search: '미국 금리 달러 유동성 주식 시장 when:3d',
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
      search: '미국 실적 시즌 마진 가이던스 주식 when:7d',
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

  @override
  List<EconomicFeedChannel> get feedChannels => defaultFeedChannels;

  @override
  void dispose() => _client.close();

  @override
  Future<EconomicFeedFetchResult> fetchFeeds() async {
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
    final selected = items.take(12).toList(growable: false);
    final status = selected.isEmpty
        ? EconomicFeedFetchStatus.failed
        : errors.isEmpty
        ? EconomicFeedFetchStatus.ready
        : EconomicFeedFetchStatus.partial;
    final message = switch (status) {
      EconomicFeedFetchStatus.ready => 'Google News RSS 실데이터 반영',
      EconomicFeedFetchStatus.partial =>
        '일부 RSS 쿼리만 반영 · ${errors.take(2).join(' · ')}',
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
        .take(3)
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
