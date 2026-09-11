"""Web static boundary."""

from digital_twin.infrastructure.settings import ROOT_DIR
import gzip
import hashlib
import mimetypes


PUBLIC_DIR = ROOT_DIR / "public"


def serve_static(self, path: str):
    target = "/index.html" if path == "/" else path
    file_path = (PUBLIC_DIR / target.lstrip("/")).resolve()
    try:
        file_path.relative_to(PUBLIC_DIR.resolve())
    except ValueError:
        return self.send_payload(403, "Forbidden", "text/plain; charset=utf-8")
    if file_path.exists() and file_path.is_dir():
        if not path.endswith("/"):
            return self.send_redirect(path + "/")
        file_path = file_path / "index.html"
    if not file_path.exists() or file_path.is_dir():
        return self.send_payload(404, "Not found", "text/plain; charset=utf-8")
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    data = file_path.read_bytes()
    etag = '"' + hashlib.sha256(data).hexdigest()[:20] + '"'
    mutable_app_assets = {
        "index.html",
        "service-worker.js",
        "manifest.webmanifest",
        "app.js",
        "app-default-settings.js",
        "web-runtime.js",
        "styles.css",
        "live-target.json",
    }
    cache_control = "no-cache" if file_path.name in mutable_app_assets else "public, max-age=31536000, immutable"
    if self.headers.get("If-None-Match") == etag:
        self.send_response(304)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        return
    compressed = False
    if len(data) >= 1024 and self.accepts_gzip() and content_type.startswith(("text/", "application/javascript", "application/json")):
        data = gzip.compress(data, compresslevel=6)
        compressed = True
    self.send_response(200)
    self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith(("text/", "application/javascript", "application/json")) else ""))
    self.send_header("Cache-Control", cache_control)
    self.send_header("ETag", etag)
    self.send_header("Vary", "Accept-Encoding")
    if compressed:
        self.send_header("Content-Encoding", "gzip")
    self.send_header("Content-Length", str(len(data)))
    self.end_headers()
    if self.command != "HEAD":
        self.wfile.write(data)
