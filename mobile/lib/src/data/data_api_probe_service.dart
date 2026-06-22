import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/market_models.dart';

class DataApiProbeClient {
  DataApiProbeClient({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  void dispose() => _client.close();

  static bool supportsSource(String sourceId) {
    return _probeSpecs.containsKey(sourceId);
  }

  static bool requiresKey(String sourceId) {
    return _probeSpecs[sourceId]?.requiresKey ?? true;
  }

  static String linkedDataLabel(DataApiSource source) {
    return _probeSpecs[source.id]?.linkedDataLabel ?? source.usedFor;
  }

  static String testLabel(String sourceId) {
    return supportsSource(sourceId)
        ? (requiresKey(sourceId) ? '연결 테스트' : '공개 API 테스트')
        : '테스트 준비 전';
  }

  Future<DataApiProbeResult> probe(DataApiSource source, String apiKey) async {
    final spec = _probeSpecs[source.id];
    if (spec == null) {
      return DataApiProbeResult.unsupported(
        sourceId: source.id,
        endpoint: source.docsUrl,
        linkedDataLabel: source.usedFor,
      );
    }

    final normalizedKey = apiKey.trim();
    if (spec.requiresKey && normalizedKey.isEmpty) {
      return DataApiProbeResult.failed(
        sourceId: source.id,
        provider: source.name,
        endpoint: spec.endpointLabel,
        linkedDataLabel: spec.linkedDataLabel,
        message: '${source.keyName} 입력 후 테스트할 수 있습니다.',
      );
    }

    try {
      return switch (source.id) {
        'alpha-vantage' => await _probeAlphaVantage(normalizedKey, spec),
        'coingecko' => await _probeCoinGecko(normalizedKey, spec),
        'fred' => await _probeFred(normalizedKey, spec),
        'defillama' => await _probeDefiLlama(spec),
        _ => DataApiProbeResult.unsupported(
          sourceId: source.id,
          endpoint: source.docsUrl,
          linkedDataLabel: source.usedFor,
        ),
      };
    } on TimeoutException {
      return DataApiProbeResult.failed(
        sourceId: source.id,
        provider: source.name,
        endpoint: spec.endpointLabel,
        linkedDataLabel: spec.linkedDataLabel,
        message: '시간 초과',
      );
    } on FormatException catch (error) {
      return DataApiProbeResult.failed(
        sourceId: source.id,
        provider: source.name,
        endpoint: spec.endpointLabel,
        linkedDataLabel: spec.linkedDataLabel,
        message: error.message,
      );
    } on http.ClientException catch (error) {
      return DataApiProbeResult.failed(
        sourceId: source.id,
        provider: source.name,
        endpoint: spec.endpointLabel,
        linkedDataLabel: spec.linkedDataLabel,
        message: error.message,
      );
    } catch (error) {
      return DataApiProbeResult.failed(
        sourceId: source.id,
        provider: source.name,
        endpoint: spec.endpointLabel,
        linkedDataLabel: spec.linkedDataLabel,
        message: '$error',
      );
    }
  }

  Future<DataApiProbeResult> _probeAlphaVantage(
    String apiKey,
    _DataApiProbeSpec spec,
  ) async {
    final uri = Uri.https('www.alphavantage.co', '/query', {
      'function': 'GLOBAL_QUOTE',
      'symbol': 'NVDA',
      'apikey': apiKey,
    });
    final decoded = await _getJsonMap(uri);
    final notice = decoded['Information'] ?? decoded['Note'];
    if (notice is String && notice.isNotEmpty) {
      throw FormatException(notice);
    }
    final quote = decoded['Global Quote'];
    if (quote is! Map || quote.isEmpty) {
      throw const FormatException('GLOBAL_QUOTE 응답이 비어 있습니다.');
    }
    final price = quote['05. price'];
    return DataApiProbeResult.ok(
      sourceId: 'alpha-vantage',
      provider: 'Alpha Vantage',
      endpoint: spec.endpointLabel,
      linkedDataLabel: spec.linkedDataLabel,
      message: 'NVDA 가격 ${price ?? '확인'}',
    );
  }

  Future<DataApiProbeResult> _probeCoinGecko(
    String apiKey,
    _DataApiProbeSpec spec,
  ) async {
    final uri = Uri.https('api.coingecko.com', '/api/v3/coins/markets', {
      'vs_currency': 'usd',
      'ids': 'bitcoin,ethereum',
      'order': 'market_cap_desc',
      'per_page': '2',
      'page': '1',
      'sparkline': 'false',
      'price_change_percentage': '1h,24h,7d',
      'locale': 'en',
    });
    final response = await _client
        .get(
          uri,
          headers: {
            'Accept': 'application/json',
            'User-Agent': 'MarketFlow/1.0',
            if (apiKey.isNotEmpty) 'x-cg-demo-api-key': apiKey,
          },
        )
        .timeout(const Duration(seconds: 12));
    if (response.statusCode != 200) {
      throw FormatException('HTTP ${response.statusCode}');
    }
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (decoded is! List || decoded.isEmpty) {
      throw const FormatException('코인 마켓 응답이 비어 있습니다.');
    }
    return DataApiProbeResult.ok(
      sourceId: 'coingecko',
      provider: 'CoinGecko',
      endpoint: spec.endpointLabel,
      linkedDataLabel: spec.linkedDataLabel,
      message: 'BTC/ETH ${decoded.length}개 마켓 확인',
    );
  }

  Future<DataApiProbeResult> _probeFred(
    String apiKey,
    _DataApiProbeSpec spec,
  ) async {
    final uri = Uri.https('api.stlouisfed.org', '/fred/series/observations', {
      'series_id': 'DGS10',
      'api_key': apiKey,
      'file_type': 'json',
      'limit': '1',
      'sort_order': 'desc',
    });
    final decoded = await _getJsonMap(uri);
    final observations = decoded['observations'];
    if (observations is! List || observations.isEmpty) {
      throw const FormatException('DGS10 관측값 응답이 비어 있습니다.');
    }
    final latest = observations.first;
    final value = latest is Map ? latest['value'] : null;
    return DataApiProbeResult.ok(
      sourceId: 'fred',
      provider: 'FRED',
      endpoint: spec.endpointLabel,
      linkedDataLabel: spec.linkedDataLabel,
      message: '미국 10년물 DGS10 ${value ?? '확인'}',
    );
  }

  Future<DataApiProbeResult> _probeDefiLlama(_DataApiProbeSpec spec) async {
    final uri = Uri.https('api.llama.fi', '/protocols');
    final response = await _client
        .get(uri, headers: {'Accept': 'application/json'})
        .timeout(const Duration(seconds: 12));
    if (response.statusCode != 200) {
      throw FormatException('HTTP ${response.statusCode}');
    }
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (decoded is! List || decoded.isEmpty) {
      throw const FormatException('프로토콜 응답이 비어 있습니다.');
    }
    return DataApiProbeResult.ok(
      sourceId: 'defillama',
      provider: 'DefiLlama',
      endpoint: spec.endpointLabel,
      linkedDataLabel: spec.linkedDataLabel,
      message: 'DeFi 프로토콜 ${decoded.length}개 확인',
    );
  }

  Future<Map<String, dynamic>> _getJsonMap(Uri uri) async {
    final response = await _client
        .get(uri, headers: {'Accept': 'application/json'})
        .timeout(const Duration(seconds: 12));
    if (response.statusCode != 200) {
      throw FormatException('HTTP ${response.statusCode}');
    }
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('JSON 객체 응답이 아닙니다.');
    }
    return decoded;
  }
}

class DataApiProbeResult {
  const DataApiProbeResult({
    required this.sourceId,
    required this.provider,
    required this.endpoint,
    required this.ok,
    required this.message,
    required this.linkedDataLabel,
    required this.checkedAt,
  });

  factory DataApiProbeResult.ok({
    required String sourceId,
    required String provider,
    required String endpoint,
    required String message,
    required String linkedDataLabel,
  }) {
    return DataApiProbeResult(
      sourceId: sourceId,
      provider: provider,
      endpoint: endpoint,
      ok: true,
      message: message,
      linkedDataLabel: linkedDataLabel,
      checkedAt: DateTime.now(),
    );
  }

  factory DataApiProbeResult.failed({
    required String sourceId,
    required String provider,
    required String endpoint,
    required String message,
    required String linkedDataLabel,
  }) {
    return DataApiProbeResult(
      sourceId: sourceId,
      provider: provider,
      endpoint: endpoint,
      ok: false,
      message: message,
      linkedDataLabel: linkedDataLabel,
      checkedAt: DateTime.now(),
    );
  }

  factory DataApiProbeResult.unsupported({
    required String sourceId,
    required String endpoint,
    required String linkedDataLabel,
  }) {
    return DataApiProbeResult(
      sourceId: sourceId,
      provider: '미지원',
      endpoint: endpoint,
      ok: false,
      message: '벤더/endpoint 선정 후 테스트할 수 있습니다.',
      linkedDataLabel: linkedDataLabel,
      checkedAt: DateTime.now(),
    );
  }

  final String sourceId;
  final String provider;
  final String endpoint;
  final bool ok;
  final String message;
  final String linkedDataLabel;
  final DateTime checkedAt;

  String get statusLabel => ok ? '테스트 성공' : '테스트 실패';
}

class _DataApiProbeSpec {
  const _DataApiProbeSpec({
    required this.endpointLabel,
    required this.linkedDataLabel,
    this.requiresKey = true,
  });

  final String endpointLabel;
  final String linkedDataLabel;
  final bool requiresKey;
}

const _probeSpecs = {
  'alpha-vantage': _DataApiProbeSpec(
    endpointLabel: 'GLOBAL_QUOTE NVDA',
    linkedDataLabel: '대시보드 관심 종목 가격, 등락률, 거래량',
  ),
  'coingecko': _DataApiProbeSpec(
    endpointLabel: '/api/v3/coins/markets BTC,ETH',
    linkedDataLabel: '자금 탭 코인 가격, 시총, 거래량, 1h/24h/7d 변화율',
    requiresKey: false,
  ),
  'fred': _DataApiProbeSpec(
    endpointLabel: 'series/observations DGS10',
    linkedDataLabel: '금리, 물가, 고용, 유동성 매크로 시계열',
  ),
  'defillama': _DataApiProbeSpec(
    endpointLabel: '/protocols',
    linkedDataLabel: 'DeFi TVL, 스테이블코인, 체인별 온체인 유동성',
    requiresKey: false,
  ),
};
