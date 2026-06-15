import 'dart:async';

import 'package:http/http.dart' as http;

import '../models/market_models.dart';

class TossDirectApiClient {
  TossDirectApiClient({http.Client? client})
    : _client = client ?? http.Client();

  static const provider = 'Toss Securities Open API';

  final http.Client _client;

  void dispose() => _client.close();

  Future<TossDirectApiProbeResult> probe(TossAccountSettings settings) async {
    final validationMessage = _validate(settings);
    if (validationMessage != null) {
      return TossDirectApiProbeResult.failed(
        endpoint: settings.testPath,
        message: validationMessage,
      );
    }

    final uri = _buildUri(settings);
    try {
      final response = await _client
          .get(uri, headers: _headers(settings))
          .timeout(const Duration(seconds: 12));
      final ok = response.statusCode >= 200 && response.statusCode < 300;
      return TossDirectApiProbeResult(
        endpoint: uri.toString(),
        ok: ok,
        statusCode: response.statusCode,
        message: ok ? '직접 호출 성공' : 'HTTP ${response.statusCode}',
        checkedAt: DateTime.now(),
      );
    } on TimeoutException {
      return TossDirectApiProbeResult.failed(
        endpoint: uri.toString(),
        message: '시간 초과',
      );
    } on FormatException catch (error) {
      return TossDirectApiProbeResult.failed(
        endpoint: settings.testPath,
        message: error.message,
      );
    } on http.ClientException catch (error) {
      return TossDirectApiProbeResult.failed(
        endpoint: uri.toString(),
        message: error.message,
      );
    } catch (error) {
      return TossDirectApiProbeResult.failed(
        endpoint: uri.toString(),
        message: '$error',
      );
    }
  }

  String? _validate(TossAccountSettings settings) {
    if (!settings.enabled) {
      return '토스증권 연결을 먼저 켜야 합니다.';
    }
    if (!settings.hasApiBaseUrl) {
      return 'Open API 기본 URL이 필요합니다.';
    }
    if (!settings.hasCredential) {
      return '앱 키/시크릿 또는 액세스 토큰이 필요합니다.';
    }
    if (!settings.hasTestPath) {
      return '연결 테스트 경로가 필요합니다.';
    }
    return null;
  }

  Uri _buildUri(TossAccountSettings settings) {
    final base = Uri.parse(settings.apiBaseUrl.trim());
    if (!base.hasScheme || !base.hasAuthority) {
      throw const FormatException('Open API 기본 URL 형식이 올바르지 않습니다.');
    }

    final rawPath = settings.testPath.trim();
    final endpoint = Uri.parse(rawPath);
    if (endpoint.hasScheme) {
      return endpoint;
    }

    final basePath = base.path.endsWith('/')
        ? base.path.substring(0, base.path.length - 1)
        : base.path;
    final endpointPath = endpoint.path.startsWith('/')
        ? endpoint.path
        : '/${endpoint.path}';

    return base.replace(path: '$basePath$endpointPath', query: endpoint.query);
  }

  Map<String, String> _headers(TossAccountSettings settings) {
    final headers = <String, String>{'Accept': 'application/json'};

    final token = settings.accessToken.trim();
    if (token.isNotEmpty) {
      headers['Authorization'] = token.startsWith('Bearer ')
          ? token
          : 'Bearer $token';
    }

    final appKey = settings.appKey.trim();
    final appSecret = settings.appSecret.trim();
    if (appKey.isNotEmpty) {
      headers['appkey'] = appKey;
    }
    if (appSecret.isNotEmpty) {
      headers['appsecret'] = appSecret;
    }

    return headers;
  }
}

class TossDirectApiProbeResult {
  const TossDirectApiProbeResult({
    required this.endpoint,
    required this.ok,
    required this.statusCode,
    required this.message,
    required this.checkedAt,
  });

  factory TossDirectApiProbeResult.failed({
    required String endpoint,
    required String message,
  }) {
    return TossDirectApiProbeResult(
      endpoint: endpoint,
      ok: false,
      statusCode: null,
      message: message,
      checkedAt: DateTime.now(),
    );
  }

  final String endpoint;
  final bool ok;
  final int? statusCode;
  final String message;
  final DateTime checkedAt;

  String get statusLabel {
    final code = statusCode == null ? '' : ' · HTTP $statusCode';
    return '$message$code';
  }
}
