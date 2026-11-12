#!/usr/bin/env python3
"""Reverse proxy that reveals AudiobookBay posts hidden as base64.

Some listing rows arrive as <div class="post re-ab" style="display:none;">BASE64</div>.
The decoded text is the post's inner HTML (title, size, link), not a replacement for
the outer div. Cardigann matches div.post:has(div.postTitle h2 a), so the outer
post element has to stay. Absolute audiobookbay URLs are rewritten to root-relative
paths so the detail request stays on this proxy and can be decoded the same way.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import os
import re
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

UPSTREAM = os.environ.get("UPSTREAM", "https://audiobookbay.lu").rstrip("/")
_SPLIT = urlsplit(UPSTREAM)
if (
    _SPLIT.scheme not in {"http", "https"}
    or not _SPLIT.hostname
    or _SPLIT.username
    or _SPLIT.password
    or _SPLIT.query
    or _SPLIT.fragment
    or _SPLIT.path not in {"", "/"}
):
    raise SystemExit("UPSTREAM must be an origin like https://audiobookbay.lu")

HOST = _SPLIT.hostname.lower()
if HOST.startswith("www."):
    HOST = HOST[4:]
ALLOWED_HOSTS = {HOST, "www." + HOST}
ORIGIN_RE = re.compile(
    rf"(?i)(?:https?:)?//(?:www\.)?{re.escape(HOST)}(?::\d+)?(?![A-Za-z0-9.-])"
)
RE_AB = re.compile(
    r"(?is)"
    r"(<(?P<tag>[a-zA-Z][\w:-]*)\b[^>]*?\bclass\s*=\s*(?P<q>[\"'])[^\"']*\bre-ab\b[^\"']*(?P=q)[^>]*>)"
    r"(?P<body>[^<]*)"
    r"</(?P=tag)\s*>"
)
HIDDEN_STYLE_RE = re.compile(r"""\sstyle\s*=\s*(["'])display\s*:\s*none\s*;?\1""", re.I)
UA = os.environ.get(
    "UPSTREAM_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)
MAX_BYTES = 3_000_000
PORT = int(os.environ.get("PORT", "8080"))
BIND = os.environ.get("BIND", "0.0.0.0")
HEALTH = "/__abb_reveal_health"


class OffHost(Exception):
    def __init__(self, host: str) -> None:
        self.host = host


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = (urlsplit(newurl).hostname or "").lower()
        if host not in ALLOWED_HOSTS:
            raise OffHost(host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = build_opener(SameHostRedirect)


def try_decode(body: str) -> str | None:
    raw = re.sub(r"[^A-Za-z0-9+/=]", "", body)
    if len(raw) < 32:
        return None
    raw += "=" * (-len(raw) % 4)
    try:
        html = base64.b64decode(raw, validate=True).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return None
    if "<" not in html:
        return None
    return html


def rewrite_origins(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        nxt = html[match.end() : match.end() + 1]
        return "" if nxt == "/" else "/"

    return ORIGIN_RE.sub(repl, html)


def reveal(html: str) -> tuple[str, int]:
    """Decode text-only .re-ab blocks in place. Returns HTML and how many decoded."""
    revealed = 0
    for _ in range(3):
        matches = [m for m in RE_AB.finditer(html) if try_decode(m.group("body"))]
        if not matches:
            break
        for match in reversed(matches):
            decoded = try_decode(match.group("body"))
            if decoded is None:
                continue
            opening = HIDDEN_STYLE_RE.sub("", match.group(1), count=1)
            html = html[: match.start()] + opening + decoded + html[match.end("body") :]
            revealed += 1
    return rewrite_origins(html), revealed


def is_html(ctype: str, data: bytes) -> bool:
    kind = ctype.lower()
    if "text/html" in kind or "application/xhtml" in kind:
        return True
    if kind.startswith("text/") or kind == "":
        head = data[:800].lower()
        return b"<html" in head or b"<!doctype html" in head or b"re-ab" in head
    return False


def safe_target(url_path: str) -> str | None:
    parts = urlsplit(url_path)
    if parts.scheme or parts.netloc or "\\" in url_path or "\x00" in url_path:
        return None
    if not parts.path.startswith("/") or parts.path.startswith("//"):
        return None
    if ".." in parts.path.split("/"):
        return None
    query = f"?{parts.query}" if parts.query else ""
    return f"{UPSTREAM}{parts.path}{query}"


def fetch(url: str) -> tuple[int, str, bytes]:
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with OPENER.open(req, timeout=20) as resp:
            status = getattr(resp, "status", 200)
            ctype = resp.headers.get("Content-Type", "")
            data = resp.read(MAX_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
        data = exc.read(MAX_BYTES + 1) if exc.fp else b""
    if len(data) > MAX_BYTES:
        raise OverflowError("upstream body too large")
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return status, ctype, data


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    def do_GET(self) -> None:
        self._proxy(head=False)

    def do_HEAD(self) -> None:
        self._proxy(head=True)

    def log_message(self, fmt: str, *args) -> None:
        return

    def _proxy(self, head: bool) -> None:
        path_only = urlsplit(self.path).path
        if path_only == HEALTH:
            self._send(200, "text/plain; charset=utf-8", b"ok\n", 0, head)
            return
        target = safe_target(self.path)
        if target is None:
            self._send(400, "text/plain; charset=utf-8", b"bad path\n", 0, head)
            return
        try:
            status, ctype, data = fetch(target)
        except OffHost as exc:
            self._send(502, "text/plain; charset=utf-8", f"off-host redirect: {exc.host}\n".encode(), 0, head)
            print(f"{self.command} {path_only} off-host {exc.host}", flush=True)
            return
        except (URLError, TimeoutError, OSError, OverflowError, gzip.BadGzipFile) as exc:
            self._send(502, "text/plain; charset=utf-8", f"upstream error: {type(exc).__name__}\n".encode(), 0, head)
            print(f"{self.command} {path_only} error {type(exc).__name__}", flush=True)
            return

        revealed = 0
        if is_html(ctype, data):
            text, revealed = reveal(data.decode("utf-8", "replace"))
            data = text.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        elif not ctype or "\n" in ctype or "\r" in ctype:
            ctype = "application/octet-stream"
        self._send(status, ctype, data, revealed, head)
        query = urlsplit(self.path).query[:80]
        print(
            f"{self.command} {path_only} q={query!r} status={status} bytes={len(data)} revealed={revealed}",
            flush=True,
        )

    def _send(self, status: int, ctype: str, body: bytes, revealed: int, head: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Abb-Revealed", str(revealed))
        self.send_header("Connection", "close")
        self.end_headers()
        if not head:
            self.wfile.write(body)


def serve() -> None:
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True

    def stop(signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"abb-reveal listening {BIND}:{PORT} -> {UPSTREAM}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    serve()
