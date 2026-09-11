"""HTTP contracts exercised without account stores, workers, or external APIs."""

import gzip
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import contextmanager
from dataclasses import replace
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from digital_twin.infrastructure.share_access import (
    SHARE_ROLE_OWNER,
    SHARE_ROLE_VIEWER,
    ShareAccess,
    issue_share_session,
    share_session_cookie,
)
from digital_twin.infrastructure.web import static
from digital_twin.infrastructure.web.composition import WebRoutes, build_api_router
from digital_twin.infrastructure.web.handler import WebRequestHandler, make_handler
from digital_twin.infrastructure.web.responses import MAX_BODY_BYTES
from digital_twin.infrastructure.web.router import ApiRouter, NOT_HANDLED


class RecordedRequest(WebRequestHandler):
    """Use the real access/error/body boundaries and record response values."""

    def __init__(self, router, path, method="GET", body=b"", headers=None, client="198.51.100.10"):
        self.api_router = router
        self.path = path
        self.command = method
        self.client_address = (client, 1234)
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value
        if "Content-Length" not in self.headers:
            self.headers["Content-Length"] = str(len(body))
        self.rfile = io.BytesIO(body)
        self.body_reads = 0
        self.response = None
        self.redirect = None

    def read_json_body(self):
        self.body_reads += 1
        return super().read_json_body()

    def send_payload(self, status, payload, content_type="application/json; charset=utf-8", cors=False, cache_control="no-store"):
        self.response = (status, payload, content_type, cors, cache_control)

    def send_redirect(self, location, cookie=""):
        self.redirect = (location, cookie)


class WebRouterBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "SHARE_TOKEN": "",
            "SHARE_VIEW_TOKEN": "test-viewer",
            "SHARE_OWNER_TOKEN": "test-owner",
            "SHARE_SESSION_SECRET": "test-session-secret",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.routes = WebRoutes()

    def cookie(self, role=SHARE_ROLE_OWNER):
        token = "test-owner" if role == SHARE_ROLE_OWNER else "test-viewer"
        access = ShareAccess(role, int(time.time()) + 3600)
        return share_session_cookie(issue_share_session(access, token)).split(";", 1)[0]

    def request(self, path, method="GET", body=b"", *, routes=None, role=SHARE_ROLE_OWNER, headers=None):
        headers = {**({"Cookie": self.cookie(role)} if role else {}), **(headers or {})}
        request = RecordedRequest(build_api_router(routes or self.routes), path, method, body, headers)
        request.handle_request()
        return request

    def test_only_explicit_route_misses_continue_dispatch(self):
        calls = []
        def miss(*_args):
            calls.append("miss")
            return NOT_HANDLED
        def sent(*_args):
            calls.append("sent")
            return None
        later = Mock(side_effect=AssertionError("already sent"))
        self.assertIsNone(ApiRouter((miss, sent, later)).dispatch(None, "/", {}))
        self.assertEqual(["miss", "sent"], calls)
        later.assert_not_called()

    def test_named_routes_precede_identifier_routes(self):
        status = Mock(return_value={"status": "idle"})
        detail = Mock(side_effect=AssertionError("status is not an identifier"))
        reasoning = replace(self.routes.reasoning, ontology_experiments_status_payload=status, ontology_experiment_payload=detail)
        strategies = replace(self.routes.model_registry, investment_strategy_proposals_status_payload=status, investment_strategy_proposal_payload=detail)
        routes = replace(self.routes, reasoning=reasoning, model_registry=strategies)
        for path in ["/api/ontology/experiments/status", "/api/investment-strategy-proposals/status"]:
            with self.subTest(path=path):
                self.assertEqual((200, {"status": "idle"}), self.request(path, routes=routes).response[:2])
        detail.assert_not_called()
        self.assertEqual(2, status.call_count)

    def test_method_specific_named_route_still_falls_through_to_detail(self):
        detail = Mock(return_value={"experimentId": "once"})
        once = Mock(side_effect=AssertionError("GET must not execute"))
        routes = replace(self.routes, reasoning=replace(self.routes.reasoning, ontology_experiment_payload=detail, run_ontology_experiments_once_payload=once))
        self.assertEqual(200, self.request("/api/ontology/experiments/once", routes=routes).response[0])
        detail.assert_called_once_with("once")
        once.assert_not_called()

    def test_notification_sections_are_not_job_identifiers_and_shared_reads_are_redacted(self):
        detail = Mock(return_value={"jobId": "job-1", "section": "reasoning"})
        routes = replace(self.routes, notifications=replace(self.routes.notifications, notification_job_detail_payload=detail))
        request = self.request("/api/notification-jobs/job-1/reasoning?recipientId=reader", routes=routes, role=SHARE_ROLE_VIEWER)
        self.assertEqual(200, request.response[0])
        detail.assert_called_once_with("job-1", "reader", section="reasoning", include_sensitive=False)

    def test_encoded_watchlist_segment_preserves_double_decoding(self):
        remove = Mock(return_value={"symbols": []})
        account_remove = Mock(side_effect=AssertionError("must not remove the account"))
        routes = replace(self.routes,
            instruments=replace(self.routes.instruments, remove_account_watchlist_payload=remove),
            accounts=replace(self.routes.accounts, remove_account_payload=account_remove),
        )
        result = self.request("/api/service-accounts/account%2520one/watchlist/BRK%252FB", "DELETE", routes=routes)
        self.assertEqual(200, result.response[0])
        remove.assert_called_once_with("account one", "BRK/B")
        account_remove.assert_not_called()

    def test_access_is_checked_before_reads_writes_options_or_route_lookup(self):
        route = Mock(side_effect=AssertionError("unauthorized request reached routing"))
        for path in ["/api/bootstrap", "/api/settings", "/api/missing", "/api/data-api/fred/observations"]:
            for method in ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]:
                with self.subTest(path=path, method=method):
                    request = RecordedRequest(ApiRouter((route,)), path, method, b"{")
                    request.handle_request()
                    self.assertEqual(401, request.response[0])
                    self.assertEqual(0, request.body_reads)
        route.assert_not_called()

    def test_viewer_mutations_are_blocked_before_body_or_dependencies(self):
        route = Mock(side_effect=AssertionError("viewer reached a mutation"))
        for path in ["/api/settings", "/api/service-accounts", "/api/notification-jobs/id/receipt", "/api/investment-brain/hypothesis-proposals/id", "/api/items", "/api/missing"]:
            for method in ["POST", "PUT", "PATCH", "DELETE"]:
                with self.subTest(path=path, method=method):
                    request = RecordedRequest(ApiRouter((route,)), path, method, b"{", {"Cookie": self.cookie(SHARE_ROLE_VIEWER)})
                    request.handle_request()
                    self.assertEqual(403, request.response[0])
                    self.assertEqual(0, request.body_reads)
        route.assert_not_called()

    def test_shared_owner_can_write_but_cannot_invoke_local_ai(self):
        save = Mock(return_value={"accounts": []})
        chat = Mock(side_effect=AssertionError("shared local AI execution"))
        routes = replace(self.routes,
            accounts=replace(self.routes.accounts, save_account_payload=save),
            workspace=replace(self.routes.workspace, chat_payload=chat),
            decisions=replace(self.routes.decisions, investment_brain_question_payload=chat),
        )
        request = self.request("/api/service-accounts", "PUT", b'{"label":"demo"}', routes=routes)
        self.assertEqual(200, request.response[0])
        save.assert_called_once_with({"label": "demo"})
        for path in ["/api/chat", "/api/investment-brain/questions"]:
            request = self.request(path, "POST", b"{", routes=routes)
            self.assertEqual(403, request.response[0])
            self.assertEqual(0, request.body_reads)
        chat.assert_not_called()

    def test_local_direct_owner_is_not_confused_with_forwarded_loopback(self):
        chat = Mock(return_value={"reply": "demo"})
        routes = replace(self.routes, workspace=replace(self.routes.workspace, chat_payload=chat))
        for headers, status in [({}, 200), ({"X-Forwarded-For": "198.51.100.10"}, 401)]:
            request = RecordedRequest(build_api_router(routes), "/api/chat", "POST", b"{}", headers, client="127.0.0.1")
            request.handle_request()
            self.assertEqual(status, request.response[0])
        chat.assert_called_once_with({})

    def test_token_exchange_cleans_tokens_and_sets_secure_cookie(self):
        request = self.request("/api/share/access?owner_token=test-owner&share_token=test-viewer&limit=2&limit=3", role=None, headers={"X-Forwarded-Proto": "https"})
        self.assertIsNone(request.response)
        self.assertEqual("/api/share/access?limit=2&limit=3", request.redirect[0])
        self.assertIn("HttpOnly", request.redirect[1])
        self.assertIn("Secure", request.redirect[1])
        self.assertNotIn("test-owner", request.redirect[1])
        exchanged = self.request(request.redirect[0], role=None, headers={"Cookie": request.redirect[1].split(";", 1)[0]})
        self.assertEqual("owner", exchanged.response[1]["role"])

    def test_wrong_role_tokens_tampered_and_expired_cookies_are_rejected(self):
        self.assertEqual(401, self.request("/api/share/access?owner_token=test-viewer", role=None).response[0])
        expired = share_session_cookie(issue_share_session(ShareAccess(SHARE_ROLE_VIEWER, int(time.time()) - 5), "test-viewer")).split(";", 1)[0]
        for cookie in [self.cookie() + "tampered", expired]:
            self.assertEqual(401, self.request("/api/share/access", role=None, headers={"Cookie": cookie}).response[0])

    def test_websocket_access_is_enforced_before_handshake(self):
        request = RecordedRequest(ApiRouter(()), "/ws", headers={"Upgrade": "websocket"})
        request.do_GET()
        self.assertEqual(401, request.response[0])
        request = RecordedRequest(ApiRouter(()), "/ws", headers={"Upgrade": "websocket", "Cookie": self.cookie()})
        request.do_GET()
        self.assertEqual(400, request.response[0])

    def test_malformed_json_length_encoding_and_body_cap_keep_400_envelope(self):
        save = Mock(side_effect=AssertionError("malformed input reached service"))
        routes = replace(self.routes, accounts=replace(self.routes.accounts, save_account_payload=save))
        cases = [(b"{", {}), (b"\xff", {}), (b"{}", {"Content-Length": "invalid"}), (b"{}", {"Content-Length": str(MAX_BODY_BYTES + 1)})]
        for body, headers in cases:
            with self.subTest(body=body, headers=headers):
                request = self.request("/api/service-accounts", "POST", body, routes=routes, headers={"X-Request-ID": "test-malformed", **headers})
                self.assertEqual(400, request.response[0])
                self.assertEqual("error", request.response[1]["status"])
                self.assertFalse(request.response[1]["retryable"])
                self.assertEqual("test-malformed", request.response[1]["requestId"])
                if "Content-Length" in headers:
                    self.assertEqual(0, request.rfile.tell())
        save.assert_not_called()

    def test_empty_body_and_exact_limit_preserve_accepted_payload(self):
        save = Mock(return_value={"accounts": []})
        routes = replace(self.routes, accounts=replace(self.routes.accounts, save_account_payload=save))
        for body in [b"", b"{}" + b" " * (MAX_BODY_BYTES - 2)]:
            self.assertEqual(200, self.request("/api/service-accounts", "POST", body, routes=routes).response[0])
        self.assertEqual([(({},), {}), (({},), {})], save.call_args_list)

    def test_parameter_errors_keep_their_route_specific_payload(self):
        self.assertEqual((400, {"error": "이름과 비서 이름은 필요합니다."}), self.request("/api/profile", "PUT", b"{}").response[:2])
        self.assertEqual((400, {"error": "기억 내용을 입력하세요."}), self.request("/api/memories", "POST", b"{}").response[:2])
        self.assertEqual((400, {"error": "유형과 제목을 입력하세요."}), self.request("/api/items", "POST", b"{}").response[:2])

    def test_dependency_errors_keep_502_and_500_envelopes(self):
        for error, status, retryable in [(urllib.error.URLError("offline"), 502, True), (RuntimeError("adapter failed"), 500, False)]:
            routes = replace(self.routes, accounts=replace(self.routes.accounts, service_accounts_payload=Mock(side_effect=error)))
            with patch("digital_twin.infrastructure.web.handler.operational_error_reporter"), patch("digital_twin.infrastructure.web.handler.report_runtime_error") as report:
                request = self.request("/api/service-accounts", routes=routes)
            self.assertEqual(status, request.response[0])
            self.assertEqual(retryable, request.response[1]["retryable"])
            report.assert_called_once()

    def test_missing_and_wrong_method_routes_keep_404(self):
        for path, method in [("/api/missing", "GET"), ("/api/bootstrap", "HEAD"), ("/api/bootstrap/", "GET"), ("/api/service-accounts", "PATCH"), ("/api/ontology/experiments/status", "DELETE")]:
            self.assertEqual(404, self.request(path, method).response[0])

    def test_direct_ledger_unavailability_is_503_only_when_requested(self):
        reader = Mock(return_value={"status": "unavailable", "usable": False})
        routes = replace(self.routes, reasoning=replace(self.routes.reasoning, ontology_inference_ledger_api_payload=reader))
        self.assertEqual(200, self.request("/api/ontology/inference-ledger", routes=routes).response[0])
        self.assertEqual(503, self.request("/api/ontology/inference-ledger?direct=1", routes=routes).response[0])


@contextmanager
def running_server(router):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(router))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise AssertionError("test-owned HTTP server did not stop")


class WebRouterWireTests(unittest.TestCase):
    def request(self, port, method, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    def test_gzip_request_id_retry_headers_and_head_body(self):
        payload = {"status": "unavailable", "retryAfterSeconds": 17, "detail": "x" * 1500}
        router = ApiRouter((lambda request, _path, _query: request.send_payload(503, payload),))
        with running_server(router) as port:
            status, pairs, body = self.request(port, "GET", "/api/test", {"Accept-Encoding": "gzip", "X-Request-ID": "wire-1"})
            headers = dict(pairs)
            self.assertEqual(503, status)
            self.assertEqual(payload, json.loads(gzip.decompress(body)))
            self.assertEqual("gzip", headers["Content-Encoding"])
            self.assertEqual("17", headers["Retry-After"])
            self.assertEqual("wire-1", headers["X-Request-ID"])
            self.assertEqual("no-store", headers["Cache-Control"])
            self.assertEqual(len(body), int(headers["X-Response-Wire-Bytes"]))
            self.assertEqual(len(gzip.decompress(body)), int(headers["X-Response-Raw-Bytes"]))
            self.assertIn("app;dur=", headers["Server-Timing"])
            status, pairs, body = self.request(port, "HEAD", "/api/test")
            self.assertEqual(503, status)
            self.assertEqual(b"", body)
            self.assertEqual("0", dict(pairs)["Content-Length"])

    def test_proxy_options_remain_cors_204_without_dependency_calls(self):
        routes = WebRoutes()
        fetch = Mock(side_effect=AssertionError("OPTIONS fetched external data"))
        routes = replace(routes, market_data=replace(routes.market_data, fetch_json_url=fetch))
        with running_server(build_api_router(routes)) as port:
            status, pairs, body = self.request(port, "OPTIONS", "/api/data-api/fred/observations")
        self.assertEqual(204, status)
        self.assertEqual(b"", body)
        self.assertEqual("*", dict(pairs)["Access-Control-Allow-Origin"])
        self.assertEqual("GET, OPTIONS", dict(pairs)["Access-Control-Allow-Methods"])
        fetch.assert_not_called()

    def test_static_traversal_etag_head_and_asset_cache_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("<h1>test</h1>", encoding="utf-8")
            (root / "asset.css").write_text("body {color: black;}", encoding="utf-8")
            (root / "folder").mkdir()
            (root / "folder" / "index.html").write_text("directory", encoding="utf-8")
            with patch.object(static, "PUBLIC_DIR", root), running_server(ApiRouter(())) as port:
                status, pairs, body = self.request(port, "GET", "/")
                self.assertEqual((200, b"<h1>test</h1>"), (status, body))
                self.assertEqual("no-cache", dict(pairs)["Cache-Control"])
                status, _, body = self.request(port, "GET", "/", {"If-None-Match": dict(pairs)["ETag"]})
                self.assertEqual((304, b""), (status, body))
                status, pairs, body = self.request(port, "HEAD", "/asset.css")
                self.assertEqual((200, b""), (status, body))
                self.assertIn("immutable", dict(pairs)["Cache-Control"])
                self.assertGreater(int(dict(pairs)["Content-Length"]), 0)
                self.assertEqual(403, self.request(port, "GET", "/%2e%2e/private")[0])
                status, pairs, _ = self.request(port, "GET", "/folder")
                self.assertEqual(302, status)
                self.assertEqual("/folder/", dict(pairs)["Location"])


if __name__ == "__main__":
    unittest.main()
