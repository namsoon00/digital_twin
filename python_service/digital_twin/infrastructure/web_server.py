"""Local HTTP server bootstrap; HTTP boundaries live in infrastructure.web."""

import errno
import os
from http.server import ThreadingHTTPServer

from .web.composition import build_api_router
from .web.handler import make_handler


DigitalTwinHandler = make_handler(build_api_router())


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


MAX_PORT_FALLBACK_ATTEMPTS = 20


def port_fallback_enabled(value: str = None) -> bool:
    configured = value if value is not None else os.environ.get("ALLOW_PORT_FALLBACK")
    return str(configured or "").strip().lower() not in {"0", "false", "no", "off"}


def bind_web_server(host: str, port: int, allow_port_fallback: bool = True, server_factory=None):
    factory = server_factory or ReusableThreadingHTTPServer
    requested_port = int(port)
    attempt_count = MAX_PORT_FALLBACK_ATTEMPTS if allow_port_fallback else 1
    last_error = None

    for offset in range(attempt_count):
        candidate_port = requested_port + offset
        if candidate_port > 65535:
            break
        try:
            return factory((host, candidate_port), DigitalTwinHandler), candidate_port
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            last_error = error

    final_port = min(65535, requested_port + attempt_count - 1)
    message = "Address already in use for ports " + str(requested_port) + "-" + str(final_port)
    if last_error is not None:
        raise OSError(errno.EADDRINUSE, message) from last_error
    raise OSError(errno.EADDRINUSE, message)


def serve(host: str = "", port: int = 3000):
    selected_host = host or os.environ.get("HOST") or "127.0.0.1"
    selected_port = int(port or os.environ.get("PORT") or 3000)
    requested_port = selected_port
    server, selected_port = bind_web_server(
        selected_host,
        selected_port,
        allow_port_fallback=port_fallback_enabled(),
    )
    display_host = "127.0.0.1" if selected_host in {"", "0.0.0.0"} else selected_host
    if selected_port != requested_port:
        print(
            "Orbit Alpha requested port " + str(requested_port) + " is occupied; using port " + str(selected_port),
            flush=True,
        )
    print("Orbit Alpha Python server running at http://" + display_host + ":" + str(selected_port), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
