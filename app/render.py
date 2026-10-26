import re
from datetime import datetime, timezone
from html import escape as html_escape, unescape as html_unescape
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import nh3
from flask import has_app_context
from markdown_it import MarkdownIt
from markdown_it.token import Token
from markupsafe import Markup

# linkify: bare URLs become links, as on Lemmy/PieFed
_md = MarkdownIt("commonmark", {"linkify": True, "breaks": True}).enable(["table", "strikethrough", "linkify"])


def _inline_bare_images(state):
    """A bare image URL (auto-linked, not [text](url)) is shown as the image itself, linked to it."""
    for block in state.tokens:
        children = block.children or []
        for n, tok in enumerate(children[:-2]):
            if tok.type == "link_open" and tok.markup == "linkify" and is_image(tok.attrGet("href")) \
                    and children[n + 1].type == "text" and children[n + 2].type == "link_close":
                children[n + 1] = Token("image", "img", 0, attrs={"src": tok.attrGet("href"), "alt": ""},
                                        children=[], content="")


_md.core.ruler.push("inline_bare_images", _inline_bare_images)

_ALLOWED = {
    "p", "br", "a", "em", "strong", "del", "s", "code", "pre", "blockquote", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "img", "table", "thead", "tbody", "tr", "th", "td",
    "sup", "sub", "details", "summary", "span",
}


_SPOILER = re.compile(r"^:::[ \t]*spoiler[ \t]*(.*?)[ \t]*\n(.*?)\n:::[ \t]*$", re.M | re.S)


def _spoilers(text):
    """PieFed's "::: spoiler Title" ... ":::" blocks become <details> (clicked open to read)."""
    return _SPOILER.sub(lambda m: "<details><summary>" + html_escape(m.group(1) or "Spoiler") + "</summary>\n\n"
                        + m.group(2) + "\n\n</details>", text)


def markdown(text):
    if not text:
        return Markup("")
    html = _md.render(_spoilers(text.replace("\r\n", "\n")))
    html = nh3.clean(
        html,
        tags=_ALLOWED,
        attributes={"a": {"href", "title"}, "img": {"src", "alt", "title"}},
        url_schemes={"http", "https", "mailto"},
        link_rel="nofollow noopener ugc",
        attribute_filter=_absolute_urls_only,
    )
    return Markup(html)


def _absolute_urls_only(tag, attr, value):
    """Federated content can't have meaningful relative URLs here; drop them instead of
    letting them point at PieBB's own paths. In-page anchors (#...) are kept for links."""
    if (tag, attr) == ("img", "src"):
        if safe_url(value) == "#":
            return None
        if has_app_context():
            from .imgproxy import proxied
            return proxied(value) or None
        return value
    if (tag, attr) == ("a", "href"):
        if value.startswith("#") or value.lower().startswith("mailto:"):
            return value
        return value if safe_url(value) != "#" else None
    return value


_IMG_SRC = re.compile(r'<img\b[^>]*?\ssrc="([^"]+)"', re.I)


def quote_parts(text, max_images=4):
    """(plain text, image srcs) for quoting a comment: its text without "[image]" markers, plus the
    (already proxied) addresses of its first few images, shown as thumbnails in the quote."""
    if not text:
        return "", []
    srcs = [html_unescape(u) for u in _IMG_SRC.findall(str(markdown(text)))][:max_images]
    return " ".join(plain(text).replace("[image]", " ").split()), srcs


def safe_url(url):
    """http(s) URLs only; anything else (javascript:, data:, relative junk) becomes '#'."""
    if not isinstance(url, str):
        return "#"
    url = url.strip()
    try:
        p = urlparse(url)
    except ValueError:
        return "#"
    return url if p.scheme in ("http", "https") and p.netloc else "#"


def css_escape(url):
    """Percent-encode anything that could end a CSS url('...') value. Expects an absolute http(s)
    URL or one of PieBB's own /img?... proxy paths; anything else becomes empty."""
    if not url or not (url.startswith(("http://", "https://")) or url.startswith("/img?")):
        return ""
    return "".join(ch if ch.isalnum() or ch in "-._~:/?#[]@!$&*+,;=%" else "%{:02X}".format(ord(ch))
                   for ch in url)


_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)


def plain(text):
    """Markdown -> one line of plain text, for descriptions and quotes (images become "[image]")."""
    if not text:
        return ""
    html = _IMG_TAG.sub(" [image] ", _md.render(text.replace("\r\n", "\n")))
    return " ".join(nh3.clean(html, tags=set()).split())


def bbdate(value, tz="Europe/Amsterdam"):
    """phpBB2-style date in the server timezone, wrapped in <time> so static/localtime.js can
    rewrite it into the visitor's own timezone: <time datetime="...Z">Sun Oct 25, 2026 11:05 am</time>"""
    if not value or not isinstance(value, str):
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value  # autoescaped by Jinja as plain text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    shown = dt.astimezone(ZoneInfo(tz)).strftime("%a %b %d, %Y %I:%M %p") \
        .replace(" 0", " ").replace("AM", "am").replace("PM", "pm")
    return Markup('<time class="bbtime" datetime="{}">{}</time>').format(utc, shown)


def domain(url):
    if not isinstance(url, str):
        return ""
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def person_name(p):
    if not p:
        return "Guest"
    return p.get("title") or p.get("user_name") or "?"


def person_ref(p):
    """user or user@remote.host"""
    if not p:
        return ""
    name = p.get("user_name") or ""
    if p.get("local"):
        return name
    return f"{name}@{domain(p.get('actor_id'))}"


def community_ref(c):
    if not c:
        return ""
    if c.get("local"):
        return c.get("name", "")
    return f"{c.get('name')}@{c.get('ap_domain') or domain(c.get('actor_id'))}"


def is_image(url):
    return bool(url) and url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"))
