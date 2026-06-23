import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'local_settings_database.dart';
import '../models/market_models.dart';

class AlphaVantageQuoteService {
  AlphaVantageQuoteService({
    http.Client? client,
    String apiKey = '',
    LocalSettingsDatabase? database,
  }) : _client = client ?? http.Client(),
       _database = database ?? const LocalSettingsDatabase(),
       _runtimeApiKey = apiKey;

  static const provider = 'Alpha Vantage';
  static const endpoint = 'GLOBAL_QUOTE';
  static const _cacheId = 'alpha-vantage.quotes';
  static const _buildApiKey = String.fromEnvironment('ALPHA_VANTAGE_API_KEY');
  static const _maxSymbols = int.fromEnvironment(
    'ALPHA_VANTAGE_MAX_SYMBOLS',
    defaultValue: 5,
  );

  final http.Client _client;
  final LocalSettingsDatabase _database;
  String _runtimeApiKey;

  String get _effectiveApiKey {
    final runtimeKey = _runtimeApiKey.trim();
    if (runtimeKey.isNotEmpty) {
      return runtimeKey;
    }
    return _buildApiKey.trim();
  }

  bool get isConfigured => _effectiveApiKey.isNotEmpty;

  void updateApiKey(String apiKey) {
    _runtimeApiKey = apiKey;
  }

  void dispose() => _client.close();

  QuoteApiSnapshot get initialSnapshot {
    return QuoteApiSnapshot.initial(apiKeyConfigured: isConfigured);
  }

  QuoteApiSnapshot loadingSnapshot(int requestedSymbols) {
    return QuoteApiSnapshot(
      provider: provider,
      endpoint: endpoint,
      status: QuoteFetchStatus.loading,
      message: '시세 API 조회 중',
      apiKeyConfigured: isConfigured,
      requestedSymbols: requestedSymbols,
      updatedAt: DateTime.now(),
    );
  }

  Future<QuoteFetchResult> fetchQuotes(List<EquityFlow> equities) async {
    final selected = equities.take(_maxSymbols).toList(growable: false);
    final cachedQuotes = await _readCachedQuotes(selected);
    if (!isConfigured) {
      if (cachedQuotes.isNotEmpty) {
        return _cachedResult(
          selected,
          cachedQuotes,
          'API key 미설정 · 마지막 저장 시세 사용',
        );
      }
      return QuoteFetchResult(
        quotes: const {},
        snapshot: QuoteApiSnapshot(
          provider: provider,
          endpoint: endpoint,
          status: QuoteFetchStatus.missingApiKey,
          message: 'ALPHA_VANTAGE_API_KEY가 없어 mock 가격을 표시합니다.',
          apiKeyConfigured: false,
          requestedSymbols: selected.length,
          updatedAt: DateTime.now(),
        ),
      );
    }

    final quotes = <String, LiveQuote>{};
    final errors = <String>[];

    for (final equity in selected) {
      try {
        final quote = await _fetchQuote(equity);
        if (quote == null) {
          errors.add('${equity.symbol}: 응답 없음');
        } else {
          quotes[equity.symbol] = quote;
        }
      } on TimeoutException {
        errors.add('${equity.symbol}: 시간 초과');
      } on FormatException catch (error) {
        errors.add('${equity.symbol}: ${error.message}');
      } catch (error) {
        errors.add('${equity.symbol}: $error');
      }
    }

    if (quotes.isNotEmpty) {
      await _writeQuoteCache({...cachedQuotes, ...quotes});
    }

    if (quotes.isEmpty && cachedQuotes.isNotEmpty) {
      return _cachedResult(selected, cachedQuotes, 'API 조회 실패 · 마지막 저장 시세 사용');
    }

    final mergedQuotes = {...cachedQuotes, ...quotes}
      ..removeWhere((symbol, _) {
        return !selected.any((equity) => equity.symbol == symbol);
      });
    final now = DateTime.now();
    final usedCache = mergedQuotes.length > quotes.length;
    final status = quotes.isEmpty
        ? QuoteFetchStatus.failed
        : errors.isEmpty && !usedCache
        ? QuoteFetchStatus.ready
        : QuoteFetchStatus.partial;
    final limitNote = equities.length > selected.length
        ? ' · ${selected.length}/${equities.length}개 조회'
        : '';
    final message = switch (status) {
      QuoteFetchStatus.ready => 'API 가격 반영$limitNote',
      QuoteFetchStatus.partial =>
        usedCache
            ? '일부 종목은 마지막 저장 시세 사용$limitNote'
            : '일부 종목만 API 가격 반영$limitNote',
      QuoteFetchStatus.failed => errors.take(2).join(' · '),
      _ => 'API 가격 상태 확인',
    };

    return QuoteFetchResult(
      quotes: mergedQuotes,
      snapshot: QuoteApiSnapshot(
        provider: provider,
        endpoint: endpoint,
        status: status,
        message: message,
        apiKeyConfigured: true,
        requestedSymbols: selected.length,
        updatedAt: now,
      ),
    );
  }

  Future<LiveQuote?> _fetchQuote(EquityFlow equity) async {
    final uri = Uri.https('www.alphavantage.co', '/query', {
      'function': endpoint,
      'symbol': equity.apiSymbol,
      'apikey': _effectiveApiKey,
    });

    final response = await _client
        .get(uri)
        .timeout(const Duration(seconds: 12));
    if (response.statusCode != 200) {
      throw FormatException('HTTP ${response.statusCode}');
    }

    final decoded = jsonDecode(response.body);
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('JSON 형식 오류');
    }

    final information = decoded['Information'] ?? decoded['Note'];
    if (information is String && information.isNotEmpty) {
      throw FormatException(information);
    }

    final payload = decoded['Global Quote'];
    if (payload is! Map<String, dynamic> || payload.isEmpty) {
      return null;
    }

    return LiveQuote(
      symbol: equity.symbol,
      apiSymbol: equity.apiSymbol,
      price: _readDouble(payload, '05. price'),
      change: _readDouble(payload, '09. change'),
      changePercent: _readPercent(payload, '10. change percent'),
      volume: _readInt(payload, '06. volume'),
      latestTradingDay: '${payload['07. latest trading day'] ?? 'unknown'}',
      fetchedAt: DateTime.now(),
      provider: provider,
    );
  }

  Future<void> _writeQuoteCache(Map<String, LiveQuote> quotes) async {
    if (quotes.isEmpty) {
      return;
    }
    final now = DateTime.now().toUtc().toIso8601String();
    final payload = {
      'provider': provider,
      'endpoint': endpoint,
      'cachedAt': now,
      'quotes': [for (final quote in quotes.values) _quoteToJson(quote)],
    };
    await _database.writeString(
      LocalSettingsDatabase.apiCacheStorageKey(_cacheId),
      jsonEncode(payload),
    );
  }

  Future<Map<String, LiveQuote>> _readCachedQuotes(
    List<EquityFlow> equities,
  ) async {
    final raw = await _database.readString(
      LocalSettingsDatabase.apiCacheStorageKey(_cacheId),
    );
    if (raw == null || raw.isEmpty) {
      return const {};
    }
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map<String, dynamic>) {
        return const {};
      }
      final cachedAt = DateTime.tryParse('${decoded['cachedAt'] ?? ''}');
      final items = decoded['quotes'];
      if (items is! List) {
        return const {};
      }
      final allowedSymbols = equities.map((equity) => equity.symbol).toSet();
      final quotes = <String, LiveQuote>{};
      for (final item in items.whereType<Map<String, dynamic>>()) {
        final quote = _quoteFromJson(item, cachedAt: cachedAt);
        if (quote != null && allowedSymbols.contains(quote.symbol)) {
          quotes[quote.symbol] = quote;
        }
      }
      return quotes;
    } catch (_) {
      return const {};
    }
  }

  QuoteFetchResult _cachedResult(
    List<EquityFlow> selected,
    Map<String, LiveQuote> quotes,
    String reason,
  ) {
    final values = quotes.values.toList(growable: false);
    final latest = values.isEmpty
        ? null
        : values
              .map((quote) => quote.fetchedAt)
              .reduce((a, b) => a.isAfter(b) ? a : b);
    return QuoteFetchResult(
      quotes: quotes,
      snapshot: QuoteApiSnapshot(
        provider: '$provider cache',
        endpoint: endpoint,
        status: QuoteFetchStatus.cached,
        message: '$reason · ${quotes.length}/${selected.length}개',
        apiKeyConfigured: isConfigured,
        requestedSymbols: selected.length,
        updatedAt: latest,
      ),
    );
  }

  Map<String, dynamic> _quoteToJson(LiveQuote quote) {
    return {
      'symbol': quote.symbol,
      'apiSymbol': quote.apiSymbol,
      'price': quote.price,
      'change': quote.change,
      'changePercent': quote.changePercent,
      'volume': quote.volume,
      'latestTradingDay': quote.latestTradingDay,
      'fetchedAt': quote.fetchedAt.toUtc().toIso8601String(),
    };
  }

  LiveQuote? _quoteFromJson(Map<String, dynamic> json, {DateTime? cachedAt}) {
    final symbol = '${json['symbol'] ?? ''}';
    final apiSymbol = '${json['apiSymbol'] ?? symbol}';
    if (symbol.isEmpty || apiSymbol.isEmpty) {
      return null;
    }
    return LiveQuote(
      symbol: symbol,
      apiSymbol: apiSymbol,
      price: _readDoubleValue(json['price']),
      change: _readDoubleValue(json['change']),
      changePercent: _readDoubleValue(json['changePercent']),
      volume: _readIntValue(json['volume']),
      latestTradingDay: '${json['latestTradingDay'] ?? 'cached'}',
      fetchedAt:
          DateTime.tryParse('${json['fetchedAt'] ?? ''}') ??
          cachedAt ??
          DateTime.now(),
      provider: '$provider cache',
    );
  }

  double _readDouble(Map<String, dynamic> payload, String key) {
    final value = payload[key];
    if (value == null) {
      throw FormatException('$key 누락');
    }
    return double.parse('$value'.replaceAll(',', ''));
  }

  double _readPercent(Map<String, dynamic> payload, String key) {
    final value = payload[key];
    if (value == null) {
      throw FormatException('$key 누락');
    }
    return double.parse('$value'.replaceAll('%', '').replaceAll(',', ''));
  }

  int _readInt(Map<String, dynamic> payload, String key) {
    final value = payload[key];
    if (value == null) {
      return 0;
    }
    return int.tryParse('$value'.replaceAll(',', '')) ?? 0;
  }

  double _readDoubleValue(Object? value) {
    if (value == null) {
      return 0;
    }
    return double.tryParse('$value'.replaceAll(',', '')) ?? 0;
  }

  int _readIntValue(Object? value) {
    if (value == null) {
      return 0;
    }
    return int.tryParse('$value'.replaceAll(',', '')) ?? 0;
  }
}
