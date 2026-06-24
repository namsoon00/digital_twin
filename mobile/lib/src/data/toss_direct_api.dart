import 'dart:async';
import 'dart:convert';

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
      final tokenResult = await _resolveAccessToken(settings);
      if (!tokenResult.ok || tokenResult.accessToken == null) {
        return TossDirectApiProbeResult(
          endpoint: _buildUri(settings, '/oauth2/token').toString(),
          ok: false,
          statusCode: tokenResult.statusCode,
          message: tokenResult.message,
          checkedAt: DateTime.now(),
        );
      }

      final response = await _client
          .get(uri, headers: _headers(settings, tokenResult.accessToken!))
          .timeout(const Duration(seconds: 12));
      final ok = response.statusCode >= 200 && response.statusCode < 300;
      return TossDirectApiProbeResult(
        endpoint: uri.toString(),
        ok: ok,
        statusCode: response.statusCode,
        message: ok ? 'OAuth 연결 성공' : 'HTTP ${response.statusCode}',
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
      return 'client_id/client_secret 또는 access token이 필요합니다.';
    }
    if (!settings.hasTestPath) {
      return '연결 테스트 경로가 필요합니다.';
    }
    return null;
  }

  Future<_TossAccessTokenResult> _resolveAccessToken(
    TossAccountSettings settings,
  ) async {
    final manualToken = settings.accessToken.trim();
    if (manualToken.isNotEmpty) {
      return _TossAccessTokenResult.ok(_normalizeBearerToken(manualToken));
    }

    final clientId = settings.appKey.trim();
    final clientSecret = settings.appSecret.trim();
    if (clientId.isEmpty || clientSecret.isEmpty) {
      return _TossAccessTokenResult.failed(
        message: 'client_id/client_secret이 필요합니다.',
      );
    }

    final tokenUri = _buildUri(settings, '/oauth2/token');
    try {
      final response = await _client
          .post(
            tokenUri,
            headers: const {
              'Accept': 'application/json',
              'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: {
              'grant_type': 'client_credentials',
              'client_id': clientId,
              'client_secret': clientSecret,
            },
          )
          .timeout(const Duration(seconds: 12));

      if (response.statusCode < 200 || response.statusCode >= 300) {
        return _TossAccessTokenResult.failed(
          message: 'OAuth 토큰 발급 실패',
          statusCode: response.statusCode,
        );
      }

      final decoded = jsonDecode(response.body);
      if (decoded is! Map<String, Object?>) {
        return _TossAccessTokenResult.failed(message: 'OAuth 토큰 응답 형식 오류');
      }

      final accessToken = decoded['access_token'];
      if (accessToken is! String || accessToken.trim().isEmpty) {
        return _TossAccessTokenResult.failed(message: 'OAuth access_token 누락');
      }

      return _TossAccessTokenResult.ok(_normalizeBearerToken(accessToken));
    } on TimeoutException {
      return _TossAccessTokenResult.failed(message: 'OAuth 토큰 발급 시간 초과');
    } on FormatException catch (error) {
      return _TossAccessTokenResult.failed(message: error.message);
    } on http.ClientException catch (error) {
      return _TossAccessTokenResult.failed(message: error.message);
    }
  }

  Uri _buildUri(TossAccountSettings settings, [String? overridePath]) {
    final base = Uri.parse(settings.apiBaseUrl.trim());
    if (!base.hasScheme || !base.hasAuthority) {
      throw const FormatException('Open API 기본 URL 형식이 올바르지 않습니다.');
    }

    final rawPath = overridePath ?? settings.testPath.trim();
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

  Map<String, String> _headers(
    TossAccountSettings settings,
    String accessToken,
  ) {
    final headers = <String, String>{
      'Accept': 'application/json',
      'Authorization': accessToken,
    };

    final accountSeq = settings.accountNumber.trim();
    if (accountSeq.isNotEmpty) {
      headers['X-Tossinvest-Account'] = accountSeq;
    }

    return headers;
  }

  String _normalizeBearerToken(String token) {
    final trimmed = token.trim();
    return trimmed.startsWith('Bearer ') ? trimmed : 'Bearer $trimmed';
  }
}

class _TossAccessTokenResult {
  const _TossAccessTokenResult({
    required this.ok,
    required this.message,
    this.accessToken,
    this.statusCode,
  });

  factory _TossAccessTokenResult.ok(String accessToken) {
    return _TossAccessTokenResult(
      ok: true,
      message: 'OAuth 토큰 준비',
      accessToken: accessToken,
    );
  }

  factory _TossAccessTokenResult.failed({
    required String message,
    int? statusCode,
  }) {
    return _TossAccessTokenResult(
      ok: false,
      message: message,
      statusCode: statusCode,
    );
  }

  final bool ok;
  final String message;
  final String? accessToken;
  final int? statusCode;
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
