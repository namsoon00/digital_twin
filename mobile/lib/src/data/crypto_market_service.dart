import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/market_models.dart';

abstract class CryptoMarketService {
  CryptoMarketSnapshot initialSnapshot(int assetCount);
  CryptoMarketSnapshot loadingSnapshot(int assetCount);
  void updateApiKey(String apiKey);
  Future<CryptoMarketFetchResult> fetchAssets(List<CryptoAsset> fallbackAssets);
  void dispose() {}
}

class StaticCryptoMarketService implements CryptoMarketService {
  const StaticCryptoMarketService(this.assets);

  final List<CryptoAsset> assets;

  @override
  CryptoMarketSnapshot initialSnapshot(int assetCount) {
    return CryptoMarketSnapshot(
      provider: 'CoinGecko mock',
      endpoint: 'static',
      status: CryptoFetchStatus.ready,
      message: '정적 코인 데이터',
      apiKeyConfigured: false,
      assetCount: assets.length,
      updatedAt: DateTime.now(),
    );
  }

  @override
  CryptoMarketSnapshot loadingSnapshot(int assetCount) {
    return initialSnapshot(assetCount);
  }

  @override
  void updateApiKey(String apiKey) {}

  @override
  Future<CryptoMarketFetchResult> fetchAssets(
    List<CryptoAsset> fallbackAssets,
  ) async {
    return CryptoMarketFetchResult(
      assets: assets,
      snapshot: initialSnapshot(assets.length),
    );
  }

  @override
  void dispose() {}
}

class CoinGeckoCryptoMarketService implements CryptoMarketService {
  CoinGeckoCryptoMarketService({http.Client? client, String apiKey = ''})
    : _client = client ?? http.Client(),
      _runtimeApiKey = apiKey;

  static const provider = 'CoinGecko';
  static const endpoint = '/api/v3/coins/markets';
  static const _buildApiKey = String.fromEnvironment('COINGECKO_API_KEY');

  final http.Client _client;
  String _runtimeApiKey;

  String get _effectiveApiKey {
    final runtimeKey = _runtimeApiKey.trim();
    if (runtimeKey.isNotEmpty) {
      return runtimeKey;
    }
    return _buildApiKey.trim();
  }

  bool get isConfigured => _effectiveApiKey.isNotEmpty;

  @override
  void updateApiKey(String apiKey) {
    _runtimeApiKey = apiKey;
  }

  @override
  CryptoMarketSnapshot initialSnapshot(int assetCount) {
    return CryptoMarketSnapshot.initial(
      apiKeyConfigured: isConfigured,
      assetCount: assetCount,
    );
  }

  @override
  CryptoMarketSnapshot loadingSnapshot(int assetCount) {
    return CryptoMarketSnapshot(
      provider: provider,
      endpoint: endpoint,
      status: CryptoFetchStatus.loading,
      message: 'CoinGecko 코인 데이터 조회 중',
      apiKeyConfigured: isConfigured,
      assetCount: assetCount,
      updatedAt: DateTime.now(),
    );
  }

  @override
  Future<CryptoMarketFetchResult> fetchAssets(
    List<CryptoAsset> fallbackAssets,
  ) async {
    if (fallbackAssets.isEmpty) {
      return CryptoMarketFetchResult(
        assets: const [],
        snapshot: CryptoMarketSnapshot(
          provider: provider,
          endpoint: endpoint,
          status: CryptoFetchStatus.failed,
          message: '조회할 코인 목록이 없습니다.',
          apiKeyConfigured: isConfigured,
          assetCount: 0,
          updatedAt: DateTime.now(),
        ),
      );
    }

    try {
      final assets = await _fetchCoinGeckoAssets(fallbackAssets);
      final merged = _mergeWithFallback(assets, fallbackAssets);
      final status = assets.length >= fallbackAssets.length
          ? CryptoFetchStatus.ready
          : CryptoFetchStatus.partial;
      return CryptoMarketFetchResult(
        assets: merged,
        snapshot: CryptoMarketSnapshot(
          provider: provider,
          endpoint: endpoint,
          status: status,
          message: isConfigured
              ? 'CoinGecko API key로 최신 코인 데이터 반영'
              : 'CoinGecko 공개 API로 최신 코인 데이터 반영',
          apiKeyConfigured: isConfigured,
          assetCount: merged.length,
          updatedAt: DateTime.now(),
        ),
      );
    } on TimeoutException {
      return _fallbackResult(fallbackAssets, 'CoinGecko 시간 초과 · mock 유지');
    } on FormatException catch (error) {
      return _fallbackResult(
        fallbackAssets,
        'CoinGecko 응답 오류 · ${error.message}',
      );
    } catch (error) {
      return _fallbackResult(fallbackAssets, 'CoinGecko 조회 실패 · $error');
    }
  }

  Future<List<CryptoAsset>> _fetchCoinGeckoAssets(
    List<CryptoAsset> fallbackAssets,
  ) async {
    final uri = Uri.https('api.coingecko.com', endpoint, {
      'vs_currency': 'usd',
      'ids': fallbackAssets.map((asset) => asset.id).join(','),
      'order': 'market_cap_desc',
      'per_page': '${fallbackAssets.length}',
      'page': '1',
      'sparkline': 'false',
      'price_change_percentage': '1h,24h,7d',
      'locale': 'en',
    });
    final headers = <String, String>{
      'Accept': 'application/json',
      'User-Agent': 'MarketFlow/1.0',
      if (isConfigured) 'x-cg-demo-api-key': _effectiveApiKey,
    };
    final response = await _client
        .get(uri, headers: headers)
        .timeout(const Duration(seconds: 12));
    if (response.statusCode != 200) {
      throw FormatException('HTTP ${response.statusCode}');
    }

    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (decoded is! List) {
      throw const FormatException('JSON 형식 오류');
    }

    return decoded
        .whereType<Map<String, dynamic>>()
        .map(_assetFromPayload)
        .whereType<CryptoAsset>()
        .toList(growable: false);
  }

  CryptoAsset? _assetFromPayload(Map<String, dynamic> payload) {
    final id = '${payload['id'] ?? ''}';
    final symbol = '${payload['symbol'] ?? ''}'.toUpperCase();
    final name = '${payload['name'] ?? ''}';
    if (id.isEmpty || symbol.isEmpty || name.isEmpty) {
      return null;
    }

    return CryptoAsset(
      id: id,
      symbol: symbol,
      name: name,
      rank: _readInt(payload['market_cap_rank']),
      priceUsd: _readDouble(payload['current_price']),
      marketCapUsd: _readDouble(payload['market_cap']),
      volume24hUsd: _readDouble(payload['total_volume']),
      change1hPercent: _readDouble(
        payload['price_change_percentage_1h_in_currency'],
      ),
      change24hPercent: _readDouble(
        payload['price_change_percentage_24h_in_currency'],
      ),
      change7dPercent: _readDouble(
        payload['price_change_percentage_7d_in_currency'],
      ),
      updatedAt: DateTime.tryParse('${payload['last_updated'] ?? ''}'),
      provider: provider,
    );
  }

  List<CryptoAsset> _mergeWithFallback(
    List<CryptoAsset> assets,
    List<CryptoAsset> fallbackAssets,
  ) {
    final byId = {for (final asset in assets) asset.id: asset};
    final merged = [
      for (final fallback in fallbackAssets) byId[fallback.id] ?? fallback,
    ];
    return merged..sort((a, b) => a.rank.compareTo(b.rank));
  }

  CryptoMarketFetchResult _fallbackResult(
    List<CryptoAsset> fallbackAssets,
    String message,
  ) {
    return CryptoMarketFetchResult(
      assets: fallbackAssets,
      snapshot: CryptoMarketSnapshot(
        provider: provider,
        endpoint: endpoint,
        status: CryptoFetchStatus.failed,
        message: message,
        apiKeyConfigured: isConfigured,
        assetCount: fallbackAssets.length,
        updatedAt: DateTime.now(),
      ),
    );
  }

  double _readDouble(Object? value) {
    if (value == null) {
      return 0;
    }
    return double.tryParse('$value') ?? 0;
  }

  int _readInt(Object? value) {
    if (value == null) {
      return 0;
    }
    return int.tryParse('$value') ?? 0;
  }

  @override
  void dispose() => _client.close();
}
