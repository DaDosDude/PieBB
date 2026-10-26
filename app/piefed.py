"""Thin client for PieFed's /api/alpha."""
import copy
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

import requests
from cachetools import TTLCache
from flask import current_app, request, session

from . import schema


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class SessionExpired(ApiError):
    """PieFed rejected the stored token (expired, revoked or logged out elsewhere)."""


_cache = TTLCache(maxsize=2048, ttl=30)
_cache_lock = threading.Lock()


def _client_ip():
    # ProxyFix has already resolved this from the reverse proxy's X-Forwarded-For
    return request.remote_addr or ""


def _headers():
    cfg = current_app.config
    h = {
        "Accept": "application/json",
        "User-Agent": "PieBB/1.0",
        "X-Forwarded-For": _client_ip(),
        "X-Forwarded-Proto": "https",
    }
    if cfg["PIEFED_HOST_HEADER"]:
        h["Host"] = cfg["PIEFED_HOST_HEADER"]
    jwt = session.get("jwt")
    if isinstance(jwt, str) and jwt:
        h["Authorization"] = f"Bearer {jwt}"
    return h


def _clean(params):
    return {k: v for k, v in (params or {}).items() if v is not None and v != ""}


def _handle(r):
    try:
        data = r.json()
    except ValueError:
        data = None
    if r.status_code >= 400:
        err = data if isinstance(data, dict) else {}
        msg = schema.s(err.get("message")) or schema.s(err.get("error")) or ""
        status = schema.s(err.get("status")) or ""
        if status.lower() == "not found":  # PieFed: {"message": "()", "status": "Not found"}
            raise ApiError(404, "That doesn't exist (anymore): it may have been deleted.")
        if not msg.strip("() "):
            msg = status or f"HTTP {r.status_code}"
        auth_failed = r.status_code == 401 or msg.startswith(("incorrect_login", "incorrect login"))
        if auth_failed and session.get("jwt"):
            session.clear()
            raise SessionExpired(r.status_code, "Your session has expired. Please log in again.")
        elif auth_failed:
            msg = "You need to log in for that."
        elif r.status_code == 429:
            msg = "PieFed is rate-limiting requests. Wait a moment and try again."
        elif msg == "Validation failed":
            msg = "PieFed rejected the request: " + (schema.s(err.get("status")) or "invalid data")
        raise ApiError(r.status_code, msg)
    return data if data is not None else {}


_badge_cache = TTLCache(maxsize=4096, ttl=15)  # header unread badges, per login


def _session_key():
    jwt = session.get("jwt")
    return hashlib.sha256(jwt.encode()).hexdigest() if isinstance(jwt, str) and jwt else None


def unread_count():
    """/user/unread_count for the current login, cached 15 s so the badge costs PieFed little.
    The session remembers when something was last marked read; older cache entries are ignored,
    so the badge is right at once on every worker."""
    key = _session_key()
    seen = session.get("badge_t")
    seen = seen if isinstance(seen, (int, float)) else 0
    if key:
        with _cache_lock:
            hit = _badge_cache.get(key)
        if hit is not None and hit[0] >= seen:
            return dict(hit[1])
    fetched = time.time()
    data = get("/user/unread_count", cache=False)
    if key:
        with _cache_lock:
            _badge_cache[key] = (fetched, dict(data))
    return data


def forget_badge():
    """Call after marking something read, so the header shows the new count at once."""
    session["badge_t"] = time.time()


def forget_session():
    """Drop every cached response that belongs to the current login (called on logout)."""
    h = _session_key()
    if not h:
        return
    with _cache_lock:
        for k in [k for k in list(_cache.keys()) if k[0] == h]:
            _cache.pop(k, None)
        _badge_cache.pop(h, None)


def get(path, cache=True, **params):
    params = _clean(params)
    url = current_app.config["PIEFED_API_URL"] + path
    key = None
    login = _session_key()
    if cache == "user" or (cache and not login):
        # anonymous responses are shared by all guests; "user" entries are per login
        key = (login or "", path, tuple(sorted(params.items())))
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None:
            return copy.deepcopy(hit)
    try:
        r = requests.get(url, params=params, headers=_headers(),
                         timeout=current_app.config["PIEFED_TIMEOUT"])
    except requests.RequestException as e:
        raise ApiError(502, f"Could not reach PieFed: {e.__class__.__name__}")
    data = schema.normalize(path, _handle(r))
    if key:
        with _cache_lock:
            _cache[key] = copy.deepcopy(data)
    return data


def post(path, payload, method="POST", keep=()):
    """keep: keys sent even when empty/None (e.g. to clear a field when editing)."""
    url = current_app.config["PIEFED_API_URL"] + path
    body = _clean(payload)
    body.update({k: payload[k] for k in keep if k in payload})
    try:
        r = requests.request(method, url, json=body, headers=_headers(),
                             timeout=current_app.config["PIEFED_TIMEOUT"])
    except requests.RequestException as e:
        raise ApiError(502, f"Could not reach PieFed: {e.__class__.__name__}")
    return schema.normalize(path, _handle(r))


# ---- topics -------------------------------------------------------------

_CHILD_KEYS = ("children", "sub_topics", "subtopics", "child_topics", "topics", "child_feeds", "sub_feeds")
_PARENT_KEYS = ("parent_id", "parent_topic_id", "parent_feed_id", "parent")


def _first(d, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return None


def _flatten(items, parent_id, out, depth=0):
    """Collect every topic/feed (nested or flat) into out[id], keeping parent links."""
    if depth > 20:
        return
    for t in schema.lst(items):
        if not isinstance(t, dict):
            continue
        inner = next((t[k] for k in ("topic", "feed") if isinstance(t.get(k), dict)), t)
        tid = schema.i(inner.get("id"))
        if tid is None or tid in out:
            continue
        pid = _first(inner, _PARENT_KEYS)
        if isinstance(pid, dict):
            pid = pid.get("id")
        pid = schema.i(pid)
        comms = schema.lst(t.get("communities")) or schema.lst(inner.get("communities"))
        communities = []
        for c in comms:
            c = schema.community(c.get("community", c) if isinstance(c, dict) else None)
            if c["id"] is not None:
                communities.append(c)
        out[tid] = {
            "id": tid,
            # "name" is the URL slug in PieFed's API; the shown name is "title"
            "name": schema.s(inner.get("title")) or schema.s(inner.get("display_name"))
                    or schema.s(inner.get("name")) or schema.s(inner.get("machine_name")) or f"#{tid}",
            "parent_id": pid if pid is not None else parent_id,
            "description": schema.s(inner.get("description")),
            "communities": communities,
            "children": [],
            # PieFed lists a topic's/feed's posts from its children too when this is set
            "with_children": bool(inner.get("show_posts_from_children") or inner.get("show_posts_in_children")),
        }
        _flatten(_first(t, _CHILD_KEYS) or _first(inner, _CHILD_KEYS), tid, out, depth + 1)


def _tree(path, key):
    data = get(path)
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = schema.lst(data.get(key)) or schema.lst(data.get("items"))
    else:
        raw = []
    by_id = {}
    _flatten(raw, None, by_id)
    roots = []
    for t in by_id.values():
        parent = by_id.get(t["parent_id"])
        (parent["children"] if parent and parent is not t else roots).append(t)
    for t in by_id.values():
        t["children"].sort(key=lambda x: x["name"].lower())
    return roots


def topic_tree():
    """PieFed's topics as a tree: [{id, name, description, communities, children, parent_id}]."""
    return _tree("/topic/list", "topics")


def feed_tree():
    """Public feeds, same shape as topic_tree(). Empty if the instance has none or no API for it."""
    try:
        return _tree("/feed/list", "feeds")
    except SessionExpired:
        raise
    except ApiError:
        return []


def find_topic(tree, topic_id, parent=None):
    for t in tree:
        if t["id"] == topic_id:
            return t, parent
        found = find_topic(t["children"], topic_id, t)
        if found[0]:
            return found
    return None, None


def upload_image(stream, filename, mimetype):
    """POST /upload/image as multipart (field "file"); returns the hosted image URL."""
    url = current_app.config["PIEFED_API_URL"] + "/upload/image"
    headers = _headers()
    try:
        r = requests.post(url, files={"file": (filename, stream, mimetype)}, headers=headers,
                          timeout=max(current_app.config["PIEFED_TIMEOUT"], 60))
    except requests.RequestException as e:
        raise ApiError(502, f"Could not reach PieFed: {e.__class__.__name__}")
    return schema.normalize("/upload/image", _handle(r)).get("url")


# ---- shared community stats (no user session, safe to use from worker threads) ----

_stats_cache = TTLCache(maxsize=4096, ttl=600)  # counts per community
_last_cache = TTLCache(maxsize=4096, ttl=300)  # newest activity per community (index "Last Post")
_stats_lock = threading.Lock()


def _batch(jobs, ids, budget):
    """jobs = [(cache, fetch)]. Returns one {id: value} dict per job, taking cached values and
    fetching the missing ones for all jobs in one parallel pool within `budget` seconds.
    Lookups that don't finish in time are simply left out (a later page load fills them in)."""
    outs, tasks = [], []
    for n, (cache, fetch) in enumerate(jobs):
        out = {}
        with _stats_lock:
            for cid in dict.fromkeys(ids):
                if cid is None:
                    continue
                hit = cache.get(cid)
                if hit is not None:
                    out[cid] = hit
                else:
                    tasks.append((n, cid))
        outs.append(out)
    if not tasks:
        return outs

    def run(n, cid):
        try:
            return n, cid, jobs[n][1](cid)
        except Exception:  # a broken answer for one community must never break the index
            return n, cid, None

    pool = ThreadPoolExecutor(max_workers=6)
    futures = [pool.submit(run, n, cid) for n, cid in tasks]
    done, _ = wait(futures, timeout=budget)
    pool.shutdown(wait=False, cancel_futures=True)
    for f in done:
        n, cid, value = f.result()
        if value is not None:
            outs[n][cid] = value
            with _stats_lock:
                jobs[n][0][cid] = value
    return outs


def _anon_getter(budget):
    """Anonymous API GET for shared statistics (never sends the visitor's login). Config is read
    here, in the request, because the lookups run in worker threads without an app context."""
    base = current_app.config["PIEFED_API_URL"]
    timeout = min(current_app.config["PIEFED_TIMEOUT"], budget)
    headers = {"Accept": "application/json", "User-Agent": "PieBB/1.0"}
    if current_app.config["PIEFED_HOST_HEADER"]:
        headers["Host"] = current_app.config["PIEFED_HOST_HEADER"]

    def anon_get(path, params):
        r = requests.get(base + path, params=params, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return None
        return schema.normalize(path, r.json())
    return anon_get


def _counts_fetch(anon_get):
    def fetch(cid):
        data = anon_get("/community", {"id": cid})
        return ((data or {}).get("community_view") or {}).get("counts")
    return fetch


def _last_fetch(anon_get):
    """Newest activity in a community: the most recently replied-to topic (sort=Active - PieFed leaves
    topics without replies out of that) or the newest topic (sort=New), whichever is later."""
    def newest(cid, sort):
        data = anon_get("/post/list", {"community_id": cid, "sort": sort, "limit": 1})
        if data is None:
            raise LookupError
        for pv in data.get("posts") or []:
            if (pv.get("post") or {}).get("id"):
                when = ((pv.get("counts") or {}).get("newest_comment_time") if sort == "Active" else None) \
                    or pv["post"].get("published") or ""
                return {"id": pv["post"]["id"], "title": pv["post"].get("title") or "", "time": when}
        return None

    def fetch(cid):
        try:
            found = [x for x in (newest(cid, "Active"), newest(cid, "New")) if x]
        except LookupError:
            return None
        # no topics: {} is remembered too, so it isn't looked up again until the cache expires
        return max(found, key=lambda x: x["time"]) if found else {}
    return fetch


def community_counts(community_ids, budget=2.5):
    """{community_id: counts}. Anonymous /community lookups, cached 10 minutes, shared by all visitors."""
    return _batch([(_stats_cache, _counts_fetch(_anon_getter(budget)))], community_ids, budget)[0]


def community_stats(community_ids, budget=2.5):
    """(counts, last posts) for the index in one parallel round. Last post = the community's most
    recently active topic (PieFed's sort=Active, bumped by replies), cached 5 minutes."""
    anon_get = _anon_getter(budget)
    return tuple(_batch([(_stats_cache, _counts_fetch(anon_get)), (_last_cache, _last_fetch(anon_get))],
                        community_ids, budget))
