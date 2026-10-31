import importlib
import os
import re
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, Response, abort, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from . import guard, imgproxy, render, sharedcache


def _bool(v):
    return str(v).lower() in ("1", "true", "yes", "on")


def _num(value, default, cast=int, minimum=0):
    """A number from the environment; a typo falls back to the default instead of crashing."""
    try:
        return max(minimum, cast(value))
    except (TypeError, ValueError):
        return default


def _timezone(name):
    """A valid IANA zone name, or UTC (an unknown TIMEZONE would otherwise break every page)."""
    try:
        ZoneInfo(name)
        return name
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"


def create_app():
    app = Flask(__name__)
    env = os.environ
    public = env.get("PIEFED_URL", "").rstrip("/")
    if not public:
        raise RuntimeError("PIEFED_URL is required (e.g. https://retrofed.com)")
    api_base = env.get("PIEFED_API_BASE", public).rstrip("/")
    secret = env.get("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY is required")

    app.config.update(
        SECRET_KEY=secret,
        PIEFED_PUBLIC_URL=public,
        PIEFED_API_URL=api_base + "/api/alpha",
        PIEFED_HOST_HEADER=env.get("PIEFED_HOST_HEADER", ""),
        PIEFED_TIMEOUT=_num(env.get("PIEFED_TIMEOUT"), 15.0, float, 1.0),
        FORUM_TITLE=env.get("FORUM_TITLE", ""),
        LOGIN_REQUIRED=_bool(env.get("LOGIN_REQUIRED", "0")),
        GUEST_RATE_LIMIT=_num(env.get("GUEST_RATE_LIMIT"), 60),
        WORKERS=_num(env.get("WEB_CONCURRENCY"), 1, minimum=1),
        TIMEZONE=_timezone(env.get("TIMEZONE", "Europe/Amsterdam")),
        DATA_DIR=env.get("DATA_DIR", "/data"),
        IMAGE_PROXY=_bool(env.get("IMAGE_PROXY", "1")),
        # images from these origins are loaded straight from them: our own PieFed, plus any
        # IMAGE_PROXY_SKIP_HOSTS (over https); the CSP allows exactly these
        IMAGE_DIRECT_ORIGINS={o for o in [imgproxy.origin(public)]
                              + [f"https://{h.strip().lower()}" for h in env.get("IMAGE_PROXY_SKIP_HOSTS", "").split(",")
                                 if h.strip()] if o},
        MAX_CONTENT_LENGTH=30 * 1024 * 1024,
        SESSION_COOKIE_NAME="piebb",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=_bool(env.get("SECURE_COOKIES", "1")),
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    sharedcache.configure(app.config["DATA_DIR"])
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.jinja_env.filters.update(
        md=render.markdown,
        url=render.safe_url,
        img=imgproxy.proxied,
        cssurl=lambda u: render.css_escape(imgproxy.proxied(u)),
        plain=render.plain,
        quote_parts=render.quote_parts,
        bbdate=lambda v: render.bbdate(v, app.config["TIMEZONE"]),
        domain=render.domain,
        pname=render.person_name,
        pref=render.person_ref,
        cref=render.community_ref,
    )
    app.jinja_env.tests["image"] = render.is_image

    from .views import bp
    importlib.import_module(".actions", __name__)  # adds its routes to bp before registration
    from .admin import admin_bp
    app.register_blueprint(bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(imgproxy.proxy_bp)
    app.register_blueprint(guard.guard_bp)

    from . import settings, themes

    @app.route("/theme/<slug>/<path:path>")
    def theme_file(slug, path):
        if not re.fullmatch(r"[a-z0-9_-]+", slug) or os.path.splitext(path)[1].lower() not in themes.ALLOWED_EXT - {".cfg", ".txt"}:
            abort(404)
        resp = send_from_directory(os.path.join(themes.themes_dir(), slug), path, max_age=86400)
        if path.endswith(".css"):
            resp.headers["Content-Type"] = "text/css; charset=utf-8"
        return resp

    @app.route("/custom.css")
    def custom_css():
        return Response(settings.get("custom_css") or "", mimetype="text/css",
                        headers={"Cache-Control": "public, max-age=86400"})

    def link_fallback(error, endpoint, values):
        # A profile/community/thread link whose name or id is empty in PieFed's data: render a dead
        # link instead of failing the whole page.
        app.logger.warning("could not build link to %s with %s", endpoint, values)
        return "#"
    app.url_build_error_handlers.append(link_fallback)

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        # with the image proxy on, browsers may only load images from us and our own PieFed,
        # so a remote image that slipped past the proxy can't leak a visitor's IP either
        img_src = ("'self' data: " + " ".join(sorted(app.config["IMAGE_DIRECT_ORIGINS"]))
                   if app.config["IMAGE_PROXY"] else "* data:")
        resp.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; img-src {img_src}; media-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'")
        return resp

    return app
