import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/market_models.dart';

class AlphaVantageQuoteService {
  AlphaVantageQuoteService({http.Client? client})
    : _client = client ?? http.Client();

  static const provider = 'Alpha Vantage';
  static const endpoint = 'GLOBAL_QUOTE';
  static const _apiKey = String.fromEnvironment('ALPHA_VANTAGE_API_KEY');
  static const _maxSymbols = int.fromEnvironment(
    'ALPHA_VANTAGE_MAX_SYMBOLS',
    defaultValue: 5,
  );

  final http.Client _client;

  bool get isConfigured => _apiKey.trim().isNotEmpty;

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
    if (!isConfigured) {
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

    final now = DateTime.now();
    final status = quotes.isEmpty
        ? QuoteFetchStatus.failed
        : errors.isEmpty
        ? QuoteFetchStatus.ready
        : QuoteFetchStatus.partial;
    final limitNote = equities.length > selected.length
        ? ' · ${selected.length}/${equities.length}개 조회'
        : '';
    final message = switch (status) {
      QuoteFetchStatus.ready => 'API 가격 반영$limitNote',
      QuoteFetchStatus.partial => '일부 종목만 API 가격 반영$limitNote',
      QuoteFetchStatus.failed => errors.take(2).join(' · '),
      _ => 'API 가격 상태 확인',
    };

    return QuoteFetchResult(
      quotes: quotes,
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
      'apikey': _apiKey,
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
}
