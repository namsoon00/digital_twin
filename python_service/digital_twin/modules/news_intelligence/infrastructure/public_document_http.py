"""Credential-free, bounded HTTPS reads with public-address pinning per hop."""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

from ..domain.source_exploration import public_url


def public_addresses(host):
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise ValueError("non-public-document-address")
    return sorted(addresses, key=lambda value: ":" in value)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the validated IP, retaining the original TLS/Host identity.
        # A second hostname resolution would reopen the DNS rebinding window.
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def fetch_public_document(url, timeout=20, maximum_bytes=8_000_000):
    deadline = time.monotonic() + max(1, min(25, timeout))
    redirects = []
    for _ in range(4):
        url = public_url(url)
        parsed = urlsplit(url)
        addresses = public_addresses(parsed.hostname)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("document-time-budget-exhausted")
        connection = _PinnedHTTPS(parsed.hostname, addresses[0], min(12, remaining))
        try:
            path = parsed.path + (("?" + parsed.query) if parsed.query else "")
            connection.request("GET", path, headers={"User-Agent": "OrbitAlphaResearch/1.0",
                "Accept": "text/html,application/pdf,text/plain,application/xhtml+xml", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                destination = public_url(urljoin(url, response.getheader("Location") or ""))
                redirects.append({"from": url, "to": destination})
                url = destination
                continue
            if response.status != 200:
                raise ValueError("document-http-" + str(response.status))
            if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise ValueError("unsupported-document-encoding")
            length = response.getheader("Content-Length", "")
            if length.isdigit() and int(length) > maximum_bytes:
                raise ValueError("document-size-budget-exhausted")
            chunks, count = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("document-time-budget-exhausted")
                if connection.sock:
                    connection.sock.settimeout(min(12, remaining))
                chunk = response.read(min(65536, maximum_bytes + 1 - count))
                count += len(chunk)
                if count > maximum_bytes:
                    raise ValueError("document-size-budget-exhausted")
                if not chunk:
                    break
                chunks.append(chunk)
            return {"url": url, "body": b"".join(chunks),
                    "contentType": response.getheader("Content-Type", ""), "redirects": redirects}
        finally:
            connection.close()
    raise ValueError("document-redirect-budget-exhausted")
