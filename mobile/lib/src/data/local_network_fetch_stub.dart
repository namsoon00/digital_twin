import 'package:http/http.dart' as http;

Future<http.Response> getJsonWithLocalNetworkAccess(
  http.Client client,
  Uri uri,
) {
  return client.get(uri, headers: {'Accept': 'application/json'});
}
