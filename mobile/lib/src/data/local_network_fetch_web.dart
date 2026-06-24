import 'dart:js_interop';

import 'package:http/http.dart' as http;
import 'package:web/web.dart' show HeadersInit, RequestInit, window;

Future<http.Response> getJsonWithLocalNetworkAccess(
  http.Client client,
  Uri uri,
) async {
  if (!_isLoopbackHttpUri(uri)) {
    return client.get(uri, headers: {'Accept': 'application/json'});
  }
  if (!_isPublicPageOrigin()) {
    return client.get(uri, headers: {'Accept': 'application/json'});
  }

  try {
    final response = await window
        .fetch(
          uri.toString().toJS,
          RequestInit(
            method: 'GET',
            headers: {'Accept': 'application/json'}.jsify()! as HeadersInit,
            cache: 'no-store',
            credentials: 'omit',
            targetAddressSpace: 'local',
          ),
        )
        .toDart;
    final body = (await response.text().toDart).toDart;
    final headers = <String, String>{};
    final contentType = response.headers.get('content-type');
    if (contentType != null && contentType.isNotEmpty) {
      headers['content-type'] = contentType;
    }

    return http.Response(
      body,
      response.status,
      headers: headers,
      reasonPhrase: response.statusText,
      request: http.Request('GET', uri),
    );
  } catch (error) {
    throw http.ClientException(
      '브라우저가 로컬 프록시 접근을 차단했습니다. Chrome 사이트 설정에서 로컬 네트워크 접근을 허용하세요. ($error)',
      uri,
    );
  }
}

bool _isLoopbackHttpUri(Uri uri) {
  if (uri.scheme != 'http') {
    return false;
  }
  final host = uri.host.toLowerCase();
  return host == 'localhost' || host == '::1' || host.startsWith('127.');
}

bool _isPublicPageOrigin() {
  final host = window.location.hostname.toLowerCase();
  return host != 'localhost' && host != '::1' && !host.startsWith('127.');
}
