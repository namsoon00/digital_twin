import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'local_network_fetch.dart';
import 'local_settings_database.dart';
import '../models/market_models.dart';

class DataApiProbeClient {
  DataApiProbeClient({http.Client? client, LocalSettingsDatabase? database})
    : _client = client ?? http.Client(),
      _database = database ?? const LocalSettingsDatabase();

  static const _localDataProxyBaseUrl = String.fromEnvironment(
    'MARKET_FLOW_DATA_PROXY_BASE_URL',
    defaultValue: 'http://127.0.0.1:3000',
  );

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
    if (sourceId != 'opendart') {
      return null;
    }
    return 'OpenDART는 브라우저 CORS 정책 때문에 GitHub Pages 웹에서 직접 연결 테스트가 실패할 수 있습니다. '
        '로컬 프록시를 쓸 때는 `npm start` 실행 후 Chrome의 로컬 네트워크 접근 권한 요청을 허용하세요. '
        '권한 요청이 뜨지 않거나 계속 실패하면 모바일 앱 또는 127.0.0.1:3000 로컬 웹에서 테스트하세요.';
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
        'alpha-vantage' => await _probeAlphaVantage(normalizedKey, spec),
        'coingecko' => await _probeCoinGecko(normalizedKey, spec),
        'fred' => await _probeFred(normalizedKey, spec),
        'opendart' => await _probeOpenDart(normalizedKey, spec),
        'defillama' => await _probeDefiLlama(spec),
        'fund-flow-vendor' || 'kr-investor-flow' => _probeVendorSelection(
          source,
          normalizedKey,
          vendorId,
          spec,
        ),
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
    try {
      return await _probeFredUri(_fredDirectUri(apiKey), spec);
    } catch (directError) {
      try {
        return await _probeFredUri(_fredProxyUri(apiKey), spec);
      } catch (proxyError) {
        throw FormatException(
          'FRED 직접 호출 실패 후 로컬 프록시도 실패했습니다. '
          '웹에서는 `npm start`로 127.0.0.1:3000 프록시를 켜야 합니다. '
          'direct: ${_safeProbeError(directError)} · '
          'proxy: ${_safeProbeError(proxyError)}',
        );
      }
    }
  }

  Uri _fredDirectUri(String apiKey) {
    return Uri.https('api.stlouisfed.org', '/fred/series/observations', {
      'series_id': 'DGS10',
      'api_key': apiKey,
      'file_type': 'json',
      'limit': '1',
      'sort_order': 'desc',
    });
  }

  Uri _fredProxyUri(String apiKey) {
    final base = Uri.parse(_localDataProxyBaseUrl);
    return base.replace(
      path: '/api/data-api/fred/observations',
      queryParameters: {
        'series_id': 'DGS10',
        'api_key': apiKey,
        'limit': '1',
        'sort_order': 'desc',
      },
    );
  }

  Future<DataApiProbeResult> _probeFredUri(
    Uri uri,
    _DataApiProbeSpec spec,
  ) async {
    final decoded = await _getJsonMap(uri);
    final errorMessage = decoded['error'] ?? decoded['error_message'];
    if (errorMessage is String && errorMessage.isNotEmpty) {
      throw FormatException(errorMessage);
    }
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

  Future<DataApiProbeResult> _probeOpenDart(
    String apiKey,
    _DataApiProbeSpec spec,
  ) async {
    try {
      return await _probeOpenDartUri(_openDartDirectUri(apiKey), spec);
    } catch (directError) {
      try {
        return await _probeOpenDartUri(_openDartProxyUri(apiKey), spec);
      } catch (proxyError) {
        throw FormatException(
          'OpenDART 연결 테스트 실패: 브라우저 웹에서는 OpenDART CORS 차단으로 직접 호출이 막힐 수 있습니다. '
          'GitHub Pages에는 API 서버가 없으므로 모바일 앱에서 테스트하거나 로컬에서 `npm start` 실행 후 '
          '127.0.0.1:3000으로 열어 테스트하세요. '
          'direct: ${_safeProbeError(directError)} · '
          'proxy: ${_safeProbeError(proxyError)}',
        );
      }
    }
  }

  Uri _openDartDirectUri(String apiKey) {
    return Uri.https('opendart.fss.or.kr', '/api/company.json', {
      'crtfc_key': apiKey,
      'corp_code': '00126380',
    });
  }

  Uri _openDartProxyUri(String apiKey) {
    final base = Uri.parse(_localDataProxyBaseUrl);
    return base.replace(
      path: '/api/data-api/opendart/company',
      queryParameters: {'crtfc_key': apiKey, 'corp_code': '00126380'},
    );
  }

  Future<DataApiProbeResult> _probeOpenDartUri(
    Uri uri,
    _DataApiProbeSpec spec,
  ) async {
    final decoded = await _getJsonMap(uri);
    final status = '${decoded['status'] ?? ''}';
    if (status != '000') {
      throw FormatException(
        '${decoded['message'] ?? 'OpenDART 응답 오류'} ($status)',
      );
    }
    final corpName = decoded['corp_name'] ?? '기업개황';
    final stockCode = decoded['stock_code'] ?? '005930';
    return DataApiProbeResult.ok(
      sourceId: 'opendart',
      provider: 'OpenDART',
      endpoint: spec.endpointLabel,
      linkedDataLabel: spec.linkedDataLabel,
      message: '$corpName $stockCode 기업개황 확인',
    );
  }

  DataApiProbeResult _probeVendorSelection(
    DataApiSource source,
    String apiKey,
    String vendorId,
    _DataApiProbeSpec spec,
  ) {
    final vendor = source.vendorOptionFor(vendorId.trim());
    if (vendor == null) {
      return DataApiProbeResult.failed(
        sourceId: source.id,
        provider: source.name,
        endpoint: spec.endpointLabel,
        linkedDataLabel: spec.linkedDataLabel,
        message: '벤더 선택 후 테스트할 수 있습니다.',
      );
    }

    return DataApiProbeResult.ok(
      sourceId: source.id,
      provider: vendor.provider,
      endpoint: vendor.endpointHint,
      linkedDataLabel: spec.linkedDataLabel,
      message: apiKey.isEmpty
          ? '${vendor.name} 벤더 선택 확인 · API key는 아직 입력 전'
          : '${vendor.name} 벤더와 API key 입력 확인',
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
    final response = await getJsonWithLocalNetworkAccess(
      _client,
      uri,
    ).timeout(const Duration(seconds: 12));
    if (response.statusCode != 200) {
      throw FormatException('HTTP ${response.statusCode}');
    }
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('JSON 객체 응답이 아닙니다.');
    }
    return decoded;
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
    this.testLabel,
  });

  final String endpointLabel;
  final String linkedDataLabel;
  final bool requiresKey;
  final String? testLabel;

  String get effectiveTestLabel {
    return testLabel ?? (requiresKey ? '연결 테스트' : '공개 API 테스트');
  }
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
  'opendart': _DataApiProbeSpec(
    endpointLabel: 'company.json 삼성전자',
    linkedDataLabel: '국내 상장사 공시, 기업개황, 사업보고서, 재무제표',
  ),
  'defillama': _DataApiProbeSpec(
    endpointLabel: '/protocols',
    linkedDataLabel: 'DeFi TVL, 스테이블코인, 체인별 온체인 유동성',
    requiresKey: false,
  ),
  'fund-flow-vendor': _DataApiProbeSpec(
    endpointLabel: '벤더별 fund flow endpoint',
    linkedDataLabel: 'ETF/펀드 순유입, 국가/섹터/자산군별 자금 흐름',
    requiresKey: false,
    testLabel: '연결 테스트',
  ),
  'kr-investor-flow': _DataApiProbeSpec(
    endpointLabel: '벤더별 투자자 수급 endpoint',
    linkedDataLabel: '외국인, 기관, 개인의 시장/업종/종목별 순매수',
    requiresKey: false,
    testLabel: '연결 테스트',
  ),
};
