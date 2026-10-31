"""Scraper protection that needs no extra services: a robots.txt and a per-IP rate limit for
guests (logged-in members are never limited). Over the limit, a guest gets a tiny 429 page that
costs PieFed nothing."""
import ipaddress
import threading
import time

from flask import Blueprint, Response, current_app, request, session

guard_bp = Blueprint("guard", __name__)

# never limited: assets, the image proxy (a page can hold dozens of images), robots.txt
_FREE_ENDPOINTS = {"static", "theme_file", "custom_css", "img.image", "guard.robots"}

_lock = threading.Lock()
_buckets = {}  # client key -> [tokens, last refill time]


def _client_key(addr):
    """Rate-limit key: the IPv4 address, or the /64 network for IPv6 (one home or server)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return addr
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


@guard_bp.before_app_request
def limit_guests():
    # each gunicorn worker (WEB_CONCURRENCY of them) keeps its own count, so each allows its share
    per_minute = current_app.config["GUEST_RATE_LIMIT"] / current_app.config["WORKERS"]
    if not per_minute or session.get("jwt") or request.endpoint in _FREE_ENDPOINTS:
        return None
    addr = request.remote_addr or ""
    if addr in ("127.0.0.1", "::1"):  # the server itself (health checks, local tests)
        return None
    key, now = _client_key(addr), time.monotonic()
    burst = max(per_minute, 1.0)  # a tiny limit split over workers must still allow one page
    with _lock:
        tokens, last = _buckets.get(key, (burst, now))
        tokens = min(burst, tokens + (now - last) * per_minute / 60)
        if tokens < 1:
            _buckets[key] = [tokens, now]
            wait = int((1 - tokens) * 60 / per_minute) + 1
            return Response(
                "<!doctype html><title>Slow down</title><p>You're loading pages very quickly. "
                f"Please wait {wait} seconds and try again, or log in.</p>",
                status=429, mimetype="text/html", headers={"Retry-After": str(wait)})
        _buckets[key] = [tokens - 1, now]
        if len(_buckets) > 50000:  # forget the idle ones
            for k in [k for k, v in _buckets.items() if now - v[1] > 600]:
                del _buckets[k]
    return None


@guard_bp.route("/robots.txt")
def robots():
    """Polite crawlers: index the forum, skip pages that are endless or only useful to people."""
    lines = ["User-agent: *", "Crawl-delay: 10"]
    lines += [f"Disallow: {p}" for p in (
        "/search", "/login", "/u/", "/img", "/messages", "/notifications", "/unread", "/admin",
        "/modcp", "/report/", "/mod/", "/*/reply", "/*/edit", "/*/new", "/*?page=", "/*&page=")]
    return Response("\n".join(lines) + "\n", mimetype="text/plain",
                    headers={"Cache-Control": "public, max-age=86400"})
