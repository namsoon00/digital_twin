import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'local_settings_database.dart';
import '../models/market_models.dart';

class DataApiProbeClient {
  DataApiProbeClient({http.Client? client, LocalSettingsDatabase? database})
    : _client = client ?? http.Client(),
      _database = database ?? const LocalSettingsDatabase();

  final http.Client _client;
  final LocalSettingsDatabase _database;

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
    return _probeSpecs[sourceId]?.effectiveTestLabel ?? '테스트 준비 전';
  }

  static String? sourceNotice(String sourceId) {
    return null;
  }

  Future<DataApiProbeResult> probe(
    DataApiSource source,
    String apiKey, {
    String vendorId = '',
  }) async {
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
      final cached = await _readCachedProbe(source.id, vendorId: vendorId);
      if (cached != null) {
        return cached.asCached(
          message: '${source.keyName} 미입력 · 마지막 성공 테스트 사용',
        );
      }
      return DataApiProbeResult.failed(
        sourceId: source.id,
        provider: source.name,
        endpoint: spec.endpointLabel,
        linkedDataLabel: spec.linkedDataLabel,
        message: '${source.keyName} 입력 후 테스트할 수 있습니다.',
      );
    }

    try {
      final result = switch (source.id) {
        'coingecko' => await _probeCoinGecko(normalizedKey, spec),
        'defillama' => await _probeDefiLlama(spec),
        _ => DataApiProbeResult.unsupported(
          sourceId: source.id,
          endpoint: source.docsUrl,
          linkedDataLabel: source.usedFor,
        ),
      };
      if (result.ok) {
        await _writeCachedProbe(result, vendorId: vendorId);
      }
      return result;
    } catch (error) {
      final cached = await _readCachedProbe(source.id, vendorId: vendorId);
      if (cached != null) {
        return cached.asCached(
          message: '${_safeProbeError(error)} · 마지막 성공 테스트 사용',
        );
      }
      return DataApiProbeResult.failed(
        sourceId: source.id,
        provider: source.name,
        endpoint: spec.endpointLabel,
        linkedDataLabel: spec.linkedDataLabel,
        message: _safeProbeError(error),
      );
    }
  }

  Future<void> _writeCachedProbe(
    DataApiProbeResult result, {
    required String vendorId,
  }) async {
    final payload = jsonEncode(result.toJson());
    await _database.writeString(
      _probeCacheStorageKey(result.sourceId, vendorId),
      payload,
    );
  }

  Future<DataApiProbeResult?> _readCachedProbe(
    String sourceId, {
    required String vendorId,
  }) async {
    final raw = await _database.readString(
      _probeCacheStorageKey(sourceId, vendorId),
    );
    if (raw == null || raw.isEmpty) {
      return null;
    }
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map<String, dynamic>) {
        return null;
      }
      return DataApiProbeResult.fromJson(decoded);
    } catch (_) {
      return null;
    }
  }

  String _probeCacheStorageKey(String sourceId, String vendorId) {
    final vendorSuffix = vendorId.trim().isEmpty ? '' : '.${vendorId.trim()}';
    return LocalSettingsDatabase.apiCacheStorageKey(
      'data-api-probe.$sourceId$vendorSuffix',
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

  String _safeProbeError(Object error) {
    if (error is TimeoutException) {
      return '시간 초과';
    }
    if (error is FormatException) {
      return error.message;
    }
    if (error is http.ClientException) {
      return error.message;
    }
    return error.runtimeType.toString();
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
    this.fromCache = false,
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

  factory DataApiProbeResult.fromJson(Map<String, dynamic> json) {
    return DataApiProbeResult(
      sourceId: '${json['sourceId'] ?? ''}',
      provider: '${json['provider'] ?? ''}',
      endpoint: '${json['endpoint'] ?? ''}',
      ok: json['ok'] == true,
      message: '${json['message'] ?? ''}',
      linkedDataLabel: '${json['linkedDataLabel'] ?? ''}',
      checkedAt:
          DateTime.tryParse('${json['checkedAt'] ?? ''}') ?? DateTime.now(),
      fromCache: json['fromCache'] == true,
    );
  }

  final String sourceId;
  final String provider;
  final String endpoint;
  final bool ok;
  final String message;
  final String linkedDataLabel;
  final DateTime checkedAt;
  final bool fromCache;

  String get statusLabel {
    if (fromCache) {
      return '저장 데이터';
    }
    return ok ? '테스트 성공' : '테스트 실패';
  }

  DataApiProbeResult asCached({required String message}) {
    return DataApiProbeResult(
      sourceId: sourceId,
      provider: '$provider cache',
      endpoint: endpoint,
      ok: ok,
      message: message,
      linkedDataLabel: linkedDataLabel,
      checkedAt: checkedAt,
      fromCache: true,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'sourceId': sourceId,
      'provider': provider,
      'endpoint': endpoint,
      'ok': ok,
      'message': message,
      'linkedDataLabel': linkedDataLabel,
      'checkedAt': checkedAt.toUtc().toIso8601String(),
    };
  }
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

  String get effectiveTestLabel {
    return requiresKey ? '연결 테스트' : '공개 API 테스트';
  }
}

const _probeSpecs = {
  'coingecko': _DataApiProbeSpec(
    endpointLabel: '/api/v3/coins/markets BTC,ETH',
    linkedDataLabel: '자금 탭 코인 가격, 시총, 거래량, 1h/24h/7d 변화율',
    requiresKey: false,
  ),
  'defillama': _DataApiProbeSpec(
    endpointLabel: '/protocols',
    linkedDataLabel: 'DeFi TVL, 스테이블코인, 체인별 온체인 유동성',
    requiresKey: false,
  ),
};
