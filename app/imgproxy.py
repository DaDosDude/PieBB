"""Image proxy: remote avatars, icons and post images are fetched by PieBB instead of the visitor's
browser, so other instances don't see your visitors' IP addresses.

Safety rules (this endpoint makes outbound requests on a visitor's behalf):
- only URLs PieBB itself put in a page are fetched: each carries an HMAC made with SECRET_KEY;
- http/https on ports 80/443 only, and the host must resolve to public addresses only; the
  connection is made to the address that was checked, so a DNS change between check and fetch
  can't redirect it to an internal service;
- redirects are followed manually (max 3), each hop checked the same way;
- only raster image types are passed through (no SVG: it can carry script), max 10 MB, 20 s in total.
"""
import hashlib
import hmac
import ipaddress
import socket
import time
from urllib.parse import quote, urljoin, urlsplit

import urllib3
from flask import Blueprint, Response, current_app, request

from .render import safe_url

proxy_bp = Blueprint("img", __name__)

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp",
                 "image/x-icon", "image/vnd.microsoft.icon"}
MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 3
TOTAL_SECONDS = 20  # whole download, so a server trickling bytes can't hold a worker
TIMEOUT = urllib3.Timeout(connect=5, read=10)


def _sign(url):
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, b"img:" + url.encode(), hashlib.sha256).hexdigest()[:32]


def proxied(url):
    """URL to use in <img src>: the proxy path for remote images, the original for local ones."""
    url = safe_url(url)
    if url == "#":
        return ""
    if not current_app.config.get("IMAGE_PROXY"):
        return url
    host = (urlsplit(url).hostname or "").lower()
    if host in current_app.config["IMAGE_PROXY_SKIP_HOSTS"]:
        return url  # served by our own PieFed: no third party to hide from
    return f"/img?u={quote(url, safe='')}&s={_sign(url)}"


class Blocked(Exception):
    pass


def _public_addresses(host, port):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise Blocked("host does not resolve")
    addrs = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            raise Blocked("unparseable address")
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise Blocked(f"{host} resolves to a non-public address")
        addrs.append(str(ip))
    if not addrs:
        raise Blocked("host does not resolve")
    return addrs


def _open(url):
    """GET url, connecting only to an address that passed the public-address check."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Blocked("bad url")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in (80, 443):
        raise Blocked("port not allowed")
    host = parts.hostname
    ip = _public_addresses(host, port)[0]
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    headers = {"Host": parts.netloc.rsplit("@", 1)[-1], "User-Agent": "PieBB image proxy",
               "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,image/*;q=0.8"}
    if parts.scheme == "https":
        pool = urllib3.HTTPSConnectionPool(ip, port, server_hostname=host, assert_hostname=host,
                                           cert_reqs="CERT_REQUIRED", timeout=TIMEOUT, retries=False)
    else:
        pool = urllib3.HTTPConnectionPool(ip, port, timeout=TIMEOUT, retries=False)
    resp = pool.urlopen("GET", path, headers=headers, redirect=False, preload_content=False)
    resp.piebb_pool = pool  # closed by the caller once the body has been read
    return resp


def _done(resp):
    resp.release_conn()
    resp.piebb_pool.close()


def _fetch(url):
    """(content type, bytes) of the image at url; raises Blocked(status) when it can't be served."""
    for _ in range(MAX_REDIRECTS + 1):
        resp = _open(url)
        if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            url = urljoin(url, resp.headers["Location"])
            _done(resp)
            continue
        break
    else:
        raise Blocked(403)
    try:
        if resp.status != 200:
            raise Blocked(404)
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype not in ALLOWED_TYPES:
            raise Blocked(415)
        length = resp.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > MAX_BYTES:
            raise Blocked(413)
        body = bytearray()
        deadline = time.monotonic() + TOTAL_SECONDS
        for chunk in resp.stream(64 * 1024):
            body += chunk
            if len(body) > MAX_BYTES:
                raise Blocked(413)
            if time.monotonic() > deadline:
                raise Blocked(504)
        return ctype, bytes(body)
    finally:
        _done(resp)


@proxy_bp.route("/img")
def image():
    url = request.args.get("u", "")
    sig = request.args.get("s", "")
    if not url or not hmac.compare_digest(sig.encode("utf-8", "replace"), _sign(url).encode()):
        return _fail(403)
    try:
        ctype, body = _fetch(url)
    except Blocked as e:
        return _fail(e.args[0] if e.args and isinstance(e.args[0], int) else 403)
    except (urllib3.exceptions.HTTPError, OSError):
        return _fail(502)
    return Response(body, mimetype=ctype, headers={
        "Cache-Control": "public, max-age=86400, immutable",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
    })


def _fail(status):
    """A bare error for a broken image: no HTML page (that would cost PieFed API calls per image)."""
    return Response(b"", status=status, headers={"Cache-Control": "public, max-age=600"})
