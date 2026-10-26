import secrets
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, session, url_for)

from werkzeug.exceptions import HTTPException

from . import forums, piefed, render, settings, themes
from .piefed import ApiError

bp = Blueprint("bb", __name__)

PER_PAGE = 25
COMMENTS_PER_PAGE = 50
MAX_PAGE = 10000
TOPIC_SORT = "New"  # phpBB-style: topic lists always show the newest topics first
LISTINGS = ("Subscribed", "Local", "All")
SEARCH_TYPES = ("Posts", "Comments", "Communities", "Users")


# ---- helpers --------------------------------------------------------------

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


@bp.before_app_request
def check_csrf():
    if request.method == "POST":
        sent, expected = request.form.get("csrf") or "", session.get("csrf") or ""
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400, "Form expired, go back and try again.")
    if settings.get("login_required") and not session.get("jwt") \
            and request.endpoint not in ("bb.login", "static", "theme_file", "custom_css"):
        return redirect(url_for("bb.login", next=request.full_path))


def logged_in():
    return bool(session.get("jwt"))


def require_login():
    if not logged_in():
        flash("You must be logged in to do that.")
        return redirect(url_for("bb.login", next=request.referrer or request.path))
    return None


def page_count(total, per_page):
    try:
        total = int(total or 0)
    except (TypeError, ValueError):
        total = 0
    return max(1, -(-total // per_page))


def page_arg():
    try:
        return min(max(1, int(request.args.get("page", 1))), MAX_PAGE)
    except ValueError:
        return 1


def safe_target(target, default="/"):
    """Only allow same-site redirects: a local path, or a full URL on this host."""
    if not target:
        return default
    if target.startswith(request.host_url):
        target = "/" + target[len(request.host_url):]
    target = target.replace("\\", "/")
    if not target.startswith("/") or target.startswith("//") or any(ord(ch) < 32 for ch in target):
        return default
    return target


def back(default):
    return redirect(safe_target(request.form.get("next") or request.referrer, default))


def site():
    try:
        return piefed.get("/site", cache="user")
    except ApiError:
        return {}


def split_sticky(posts, page):
    posts = [p for p in posts if isinstance(p, dict) and isinstance(p.get("post"), dict)]
    if page != 1:
        return [], posts
    sticky = [p for p in posts if p["post"].get("sticky")]
    return sticky, [p for p in posts if not p["post"].get("sticky")]


def comment_parent(path):
    parts = (path or "").split(".")
    # path is "0.<root>.<child>...": parent is the second-to-last id
    if len(parts) >= 3:
        try:
            return int(parts[-2])
        except ValueError:
            return None
    return None


def me_info():
    """(person, is_admin, moderated community ids) for the logged-in user."""
    if not logged_in():
        return None, False, set()
    s = site()
    my = s.get("my_user") or {}
    me = (my.get("local_user_view") or {}).get("person")
    if not me:
        return None, False, set()
    admin_ids = {(a.get("person") or {}).get("id") for a in (s.get("admins") or []) if isinstance(a, dict)}
    mods = {(m.get("community") or {}).get("id") for m in (my.get("moderates") or []) if isinstance(m, dict)}
    mods.discard(None)
    return me, me.get("id") in admin_ids, mods


def can_moderate(community_id):
    _, is_admin, mods = me_info()
    return is_admin or community_id in mods


@bp.app_context_processor
def inject():
    s = site()
    me, is_admin, mods = me_info()
    return {"site": s.get("site") or {}, "site_admins": (s.get("admins") or []), "me": me, "is_admin": is_admin,
            "is_mod": is_admin or bool(mods),
            "can_mod": lambda cid: is_admin or cid in mods,
            "csrf_token": csrf_token, "logged_in": logged_in(), "theme": themes.current(),
            "forum_title": settings.get("forum_title") or (s.get("site") or {}).get("name") or "PieFed",
            "piefed_url": current_app.config["PIEFED_PUBLIC_URL"], "comments_per_page": COMMENTS_PER_PAGE}


@bp.app_errorhandler(HTTPException)
def http_error(e):
    return render_template("message.html", title=e.name, message=e.description), e.code


@bp.app_errorhandler(piefed.SessionExpired)
def session_expired(e):
    # the session is already cleared: reload the page as a guest instead of showing an error
    flash(e.message)
    if request.method == "GET":
        return redirect(safe_target(request.full_path))
    return redirect(url_for("bb.login", next=safe_target(request.referrer)))


@bp.app_errorhandler(ApiError)
def api_error(e):
    return render_template("message.html", title="Error", message=e.message), \
        e.status if 400 <= e.status < 600 else 500


# ---- index ----------------------------------------------------------------

@bp.route("/")
def index():
    view = request.args.get("view")
    if view in ("topics", "flat"):
        session["index_view"] = view
    view = session.get("index_view") or settings.get("index_mode")

    if view == "topics":
        cats = forums.build_index()
        if cats:
            return render_template("index_topics.html", cats=cats, view=view)
        view = "flat"  # instance has no topics or visible feeds

    listing = request.args.get("type")
    if listing not in LISTINGS or (listing == "Subscribed" and not logged_in()):
        listing = "Subscribed" if logged_in() else "Local"
    sort = request.args.get("sort") if request.args.get("sort") in ("Active", "New", "Hot") else "Active"
    page = page_arg()
    data = piefed.get("/community/list", type_=listing, sort=sort, page=page, limit=50)
    hidden = forums.hidden_community_ids()
    communities = [dict(c, community=_renamed_community(c["community"]))
                   for c in (data.get("communities") or [])
                   if (c.get("community") or {}).get("id") and c["community"]["id"] not in hidden]
    return render_template("index_flat.html", communities=communities, listing=listing,
                           sort=sort, page=page, view=view)


# ---- forums ---------------------------------------------------------------

@bp.route("/t/<int:topic_id>")
def topic(topic_id):
    t, parent = piefed.find_topic(piefed.topic_tree(), topic_id)
    if not t:
        abort(404)
    return _node_listing("topic", t, parent, topic_id=topic_id)


@bp.route("/f/<int:feed_id>")
def feed(feed_id):
    f, parent = piefed.find_topic(piefed.feed_tree(), feed_id)
    if not f:
        abort(404)
    return _node_listing("feed", f, parent, feed_id=feed_id)


def _node_listing(kind, node, parent, **query):
    """Topic list of a PieFed topic or feed (all their communities' posts)."""
    page = page_arg()
    data = piefed.get("/post/list", sort=TOPIC_SORT, page=page, limit=PER_PAGE, **query)
    pages = listing_pages(node)
    if page > 1 and not data.get("posts"):  # past the end: show the last page instead
        return redirect(url_for(f"bb.{kind}", page=min(page - 1, pages or 1), **query))
    sticky, posts = split_sticky(data.get("posts") or [], page)
    return render_template("forum.html", kind=kind, topic=_named(node, kind), parent=_named(parent, kind),
                           subforums=forums.subforum_rows(node, kind),
                           sticky=sticky, posts=posts, page=page, pages=pages,
                           has_next=len(data.get("posts") or []) >= PER_PAGE)


def listing_pages(node):
    """Total pages of a topic/feed listing: the summed topic counts of the communities PieFed
    lists posts from (cached index statistics). None when some count isn't known yet."""
    ids, stack, seen = [], [node], set()
    while stack:
        n = stack.pop()
        if id(n) in seen:
            continue
        seen.add(id(n))
        ids += [c["id"] for c in n["communities"] if c.get("id") is not None]
        if node.get("with_children"):
            stack += n["children"]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return 1
    counts = piefed.community_counts(ids)
    if any(cid not in counts for cid in ids):
        return None
    return page_count(sum(int((counts[cid] or {}).get("post_count") or 0) for cid in ids), PER_PAGE)


def _named(node, kind):
    """Copy of a topic/feed node with the admin's display name, if any."""
    if not node:
        return None
    return dict(node, name=settings.layout_for(f"{kind}:{node['id']}").get("name") or node["name"])


def _renamed_community(c):
    """Community dict with the admin's display name (if any) as its title."""
    name = settings.layout_for(f"community:{c.get('id')}").get("name")
    return dict(c, title=name) if name else c


@bp.route("/c/<path:name>")
def community(name):
    info = piefed.get("/community", name=name)
    cv = info.get("community_view") or {}
    if not (cv.get("community") or {}).get("id"):
        abort(404)
    cv["community"] = _renamed_community(cv["community"])
    page = page_arg()
    data = piefed.get("/post/list", community_id=cv["community"]["id"], sort=TOPIC_SORT,
                      page=page, limit=PER_PAGE)
    pages = page_count((cv.get("counts") or {}).get("post_count"), PER_PAGE)
    if page > 1 and not data.get("posts"):  # past the end: show the last page instead
        return redirect(url_for("bb.community", name=name, page=min(page - 1, pages)))
    sticky, posts = split_sticky(data.get("posts") or [], page)
    return render_template("forum.html", kind="community", cv=cv, mods=(info.get("moderators") or []),
                           sticky=sticky, posts=posts, page=page, pages=pages,
                           has_next=len(data.get("posts") or []) >= PER_PAGE)


# ---- threads --------------------------------------------------------------

@bp.route("/p/<int:post_id>")
def thread(post_id):
    data = piefed.get("/post", id=post_id)
    pv = data.get("post_view")
    if not pv:
        abort(404)
    pages = page_count((pv.get("counts") or {}).get("comments"), COMMENTS_PER_PAGE)
    if request.args.get("page") == "last":
        return redirect(url_for("bb.thread", post_id=post_id, page=pages) + "#bottom")
    page = page_arg()
    if page > pages + 1:  # past the end (old link, or comments were removed): go to the last page
        return redirect(url_for("bb.thread", post_id=post_id, page=pages))
    comments = _comment_page(post_id, page)
    by_id = {c["comment"]["id"]: c for c in comments}
    for c in comments:
        pid = comment_parent(c["comment"].get("path"))
        c["parent_id"] = pid
        c["parent"] = by_id.get(pid)
        c["parent_here"] = pid in by_id
    # Parents on an earlier page: fetch them so the reply can still quote them
    fetched = {}
    for c in comments:
        pid = c["parent_id"]
        if pid and not c["parent"] and len(fetched) < 15:
            if pid not in fetched:
                try:
                    fetched[pid] = piefed.get("/comment", id=pid).get("comment_view")
                except ApiError:
                    fetched[pid] = None
            c["parent"] = fetched[pid]
    if logged_in() and not pv.get("read"):
        try:
            piefed.post("/post/mark_as_read", {"post_id": post_id, "read": True})
        except ApiError:
            pass
    return render_template("thread.html", pv=pv, comments=comments, page=page, pages=pages,
                           offset=(page - 1) * COMMENTS_PER_PAGE,
                           has_next=len(comments) >= COMMENTS_PER_PAGE)


def _comment_page(post_id, page):
    """One page of a thread's replies in posting order (PieFed may hand back a tree-ordered page)."""
    comments = piefed.get("/comment/list", post_id=post_id, sort="Old", page=page,
                          limit=COMMENTS_PER_PAGE, max_depth=100).get("comments") or []
    comments = [c for c in comments if c["comment"]["id"]]
    comments.sort(key=lambda c: c["comment"]["published"] or "")
    return comments


def _page_of(post_id, published, pages):
    """Thread page holding a reply posted at `published`: a binary search over the pages' time
    ranges (a handful of requests even for very long threads)."""
    lo, hi = 1, pages
    for _ in range(12):
        if lo >= hi:
            break
        mid = (lo + hi) // 2
        times = [c["comment"]["published"] for c in _comment_page(post_id, mid) if c["comment"]["published"]]
        if not times:
            hi = max(lo, mid - 1)
        elif published > max(times):
            lo = mid + 1
        else:
            hi = mid
    return lo


@bp.route("/comment/<int:comment_id>")
def comment_link(comment_id):
    c = piefed.get("/comment", id=comment_id).get("comment_view") or {}
    post_id = (c.get("post") or {}).get("id")
    if not post_id:
        abort(404)
    pv = piefed.get("/post", id=post_id).get("post_view") or {}
    pages = page_count((pv.get("counts") or {}).get("comments"), COMMENTS_PER_PAGE)
    published = (c.get("comment") or {}).get("published")
    page = _page_of(post_id, published, pages) if pages > 1 and published else 1
    return redirect(url_for("bb.thread", post_id=post_id, page=page if page > 1 else None) + f"#c{comment_id}")


# ---- writing --------------------------------------------------------------

UPLOAD_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
UPLOAD_MAX = 10 * 1024 * 1024


def attach_upload(body):
    """Upload the form's image to PieFed and append it to the message as markdown."""
    f = request.files.get("image")
    if not f or not f.filename:
        flash("Choose an image to upload first.")
        return body
    url = _upload(f)
    if not url:
        return body
    flash("Image uploaded and added to your message.")
    return (body.rstrip() + "\n\n" if body.strip() else "") + f"![image]({url})\n"


def _upload(f):
    """Upload a file to PieFed; returns its URL, or None after flashing why it failed."""
    head = f.stream.read(16)
    f.stream.seek(0)
    kind = _sniff_image(head)
    if kind not in UPLOAD_TYPES:
        flash("Only PNG, JPEG, GIF or WebP images can be uploaded.")
        return None
    f.stream.seek(0, 2)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > UPLOAD_MAX:
        flash("That image is larger than 10 MB.")
        return None
    try:
        url = piefed.upload_image(f.stream, f"upload.{UPLOAD_TYPES[kind]}", kind)
    except piefed.SessionExpired:
        raise
    except ApiError as e:
        flash(f"Upload failed: {e.message}")
        return None
    if not url or render.safe_url(url) == "#":
        flash("Upload failed: PieFed returned no image address.")
        return None
    return url


def _sniff_image(head):
    """Content type from the file's magic bytes (the browser-supplied type is not trusted)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


@bp.route("/c/<path:name>/new", methods=["GET", "POST"])
def new_topic(name):
    if (r := require_login()):
        return r
    cv = piefed.get("/community", name=name, cache=False).get("community_view") or {}
    if not (cv.get("community") or {}).get("id"):
        abort(404)
    form = {"title": "", "body": "", "url": "", "nsfw": False}
    if request.method == "POST":
        form = {"title": request.form.get("title", "").strip(), "body": request.form.get("body", ""),
                "url": request.form.get("url", "").strip(), "nsfw": bool(request.form.get("nsfw"))}
        if request.form.get("upload"):
            form["body"] = attach_upload(form["body"])
            return render_template("posting.html", mode="topic", cv=cv, form=form)
        if request.form.get("preview"):
            return render_template("posting.html", mode="topic", cv=cv, form=form, preview=True)
        if not form["title"]:
            flash("You must enter a subject.")
        else:
            try:
                res = piefed.post("/post", {"title": form["title"], "community_id": cv["community"]["id"],
                                            "body": form["body"] or None, "url": form["url"] or None,
                                            "nsfw": form["nsfw"]})
                new_id = ((res.get("post_view") or {}).get("post") or {}).get("id")
                if not new_id:  # created, but PieFed answered in an unexpected shape
                    flash("Topic posted.")
                    return redirect(url_for("bb.community", name=name))
                return redirect(url_for("bb.thread", post_id=new_id))
            except ApiError as e:
                flash(e.message)
    return render_template("posting.html", mode="topic", cv=cv, form=form)


@bp.route("/p/<int:post_id>/reply", methods=["GET", "POST"])
def reply(post_id):
    if (r := require_login()):
        return r
    pv = piefed.get("/post", id=post_id, cache=False).get("post_view") or {}
    if not (pv.get("post") or {}).get("id"):
        abort(404)
    parent_id = request.values.get("parent", type=int)
    body = request.form.get("body", "")
    if request.method == "GET" and request.args.get("quote"):
        if parent_id:
            src = piefed.get("/comment", id=parent_id, cache=False).get("comment_view") or {}
            who, text = render.person_name(src.get("creator")), (src.get("comment") or {}).get("body")
        else:
            who, text = render.person_name(pv.get("creator")), pv["post"].get("body") or pv["post"].get("title")
        body = f"**{who}** wrote:\n" + "\n".join("> " + line for line in (text or "").splitlines()) + "\n\n"
    if request.method == "POST":
        if request.form.get("upload"):
            return render_template("posting.html", mode="reply", pv=pv, parent_id=parent_id,
                                   form={"body": attach_upload(body)})
        if request.form.get("preview"):
            return render_template("posting.html", mode="reply", pv=pv, parent_id=parent_id,
                                   form={"body": body}, preview=True)
        if not body.strip():
            flash("You must enter a message.")
        else:
            try:
                res = piefed.post("/comment", {"body": body, "post_id": post_id, "parent_id": parent_id})
                cid = ((res.get("comment_view") or {}).get("comment") or {}).get("id")
                if not cid:  # created, but PieFed answered in an unexpected shape
                    flash("Reply posted.")
                    return redirect(url_for("bb.thread", post_id=post_id, page="last"))
                return redirect(url_for("bb.comment_link", comment_id=cid))
            except ApiError as e:
                flash(e.message)
    return render_template("posting.html", mode="reply", pv=pv, parent_id=parent_id, form={"body": body})


@bp.route("/vote/<kind>/<int:obj_id>", methods=["POST"])
def vote(kind, obj_id):
    if (r := require_login()):
        return r
    score = request.form.get("score", type=int, default=0)
    if score not in (-1, 0, 1) or kind not in ("post", "comment"):
        abort(400)
    piefed.post(f"/{kind}/like", {f"{kind}_id": obj_id, "score": score})
    return back(url_for("bb.index"))


@bp.route("/follow/<int:community_id>", methods=["POST"])
def follow(community_id):
    if (r := require_login()):
        return r
    piefed.post("/community/follow", {"community_id": community_id,
                                      "follow": request.form.get("follow") == "1"})
    return back(url_for("bb.index"))


# ---- moderation -----------------------------------------------------------

POST_ACTIONS = {
    "lock": ("/post/lock", lambda i, r: {"post_id": i, "locked": True}, "Topic locked."),
    "unlock": ("/post/lock", lambda i, r: {"post_id": i, "locked": False}, "Topic unlocked."),
    "sticky": ("/post/feature", lambda i, r: {"post_id": i, "featured": True, "feature_type": "Community"},
               "Topic made sticky."),
    "unsticky": ("/post/feature", lambda i, r: {"post_id": i, "featured": False, "feature_type": "Community"},
                 "Topic is no longer sticky."),
    "remove": ("/post/remove", lambda i, r: {"post_id": i, "removed": True, "reason": r}, "Topic removed."),
    "restore": ("/post/remove", lambda i, r: {"post_id": i, "removed": False, "reason": r}, "Topic restored."),
}
COMMENT_ACTIONS = {
    "remove": ("/comment/remove", lambda i, r: {"comment_id": i, "removed": True, "reason": r}, "Post removed."),
    "restore": ("/comment/remove", lambda i, r: {"comment_id": i, "removed": False, "reason": r}, "Post restored."),
}


@bp.route("/mod/<kind>/<int:obj_id>", methods=["POST"])
def moderate(kind, obj_id):
    if (r := require_login()):
        return r
    actions = POST_ACTIONS if kind == "post" else COMMENT_ACTIONS if kind == "comment" else None
    act = request.form.get("action")
    if not actions or act not in actions:
        abort(400)
    view = piefed.get(f"/{kind}", id=obj_id, cache=False).get(f"{kind}_view") or {}
    community_id = (view.get("community") or {}).get("id")
    if not community_id:
        abort(404)
    if not can_moderate(community_id):
        abort(403, "You are not a moderator of this forum.")
    path, payload, done = actions[act]
    piefed.post(path, payload(obj_id, request.form.get("reason", "").strip() or None))
    flash(done)
    return back(url_for("bb.index"))


@bp.route("/mod/ban", methods=["GET", "POST"])
def ban():
    if (r := require_login()):
        return r
    community_id = request.values.get("community_id", type=int)
    user_id = request.values.get("user_id", type=int)
    if not community_id or not user_id:
        abort(400)
    if not can_moderate(community_id):
        abort(403, "You are not a moderator of this forum.")
    cv = piefed.get("/community", id=community_id, cache=False).get("community_view") or {}
    person = (piefed.get("/user", person_id=user_id, cache=False).get("person_view") or {}).get("person") or {}
    if not (cv.get("community") or {}).get("id") or not person.get("id"):
        abort(404)
    if request.method == "POST":
        days = request.form.get("days", type=int, default=0)
        if request.form.get("unban"):
            piefed.post("/community/moderate/unban", {"community_id": community_id, "user_id": user_id},
                        method="PUT")
            flash(f"{render.person_name(person)} unbanned.")
        else:
            payload = {"community_id": community_id, "user_id": user_id,
                       "reason": request.form.get("reason", "").strip()[:250] or "No reason given"}
            if days > 0:
                payload["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=days)) \
                    .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            else:
                payload["permanent"] = True
            piefed.post("/community/moderate/ban", payload)
            flash(f"{render.person_name(person)} banned from {cv.get('community', {}).get('title')}.")
        return redirect(safe_target(request.form.get("next"), url_for("bb.index")))
    return render_template("ban.html", cv=cv, person=person, next=request.referrer or "")


# ---- users / auth ---------------------------------------------------------

@bp.route("/u/<path:username>")
def profile(username):
    data = piefed.get("/user", username=username, sort="New")
    return render_template("profile.html", pv=data.get("person_view") or {}, posts=(data.get("posts") or []),
                           comments=(data.get("comments") or []), moderates=(data.get("moderates") or []))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            res = piefed.post("/user/login", {"username": request.form.get("username", "").strip(),
                                             "password": request.form.get("password", "")})
            jwt = res.get("jwt")
            if not isinstance(jwt, str) or not jwt:
                raise ApiError(401, "Login failed.")
            nxt = request.form.get("next") or "/"
            session.clear()
            session.permanent = bool(request.form.get("autologin"))
            session["jwt"] = jwt
            csrf_token()
            return redirect(safe_target(nxt))
        except ApiError as e:
            flash("Incorrect username or password." if e.status == 400 else e.message)
    return render_template("login.html", next=request.args.get("next", ""))


@bp.route("/logout", methods=["POST"])
def logout():
    if logged_in():
        try:  # revoke the token on PieFed's side too
            piefed.post("/user/logout", {})
        except ApiError:
            pass
        piefed.forget_session()
    session.clear()
    return redirect(url_for("bb.index"))


@bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    stype = request.args.get("type") if request.args.get("type") in SEARCH_TYPES else "Posts"
    page = page_arg()
    res = piefed.get("/search", q=q, type_=stype, page=page, limit=PER_PAGE) if q else {}
    return render_template("search.html", q=q, stype=stype, res=res, page=page)
