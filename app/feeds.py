"""Feeds: the list of feeds, following one, and making and editing your own. A feed from another
site is fetched through the search box (~name@site or a link to it), see views.resolve_link."""
import re

from flask import abort, flash, redirect, render_template, request, url_for

from . import piefed
from .piefed import ApiError
from .views import _upload, back, bp, logged_in, require_login

SLUG = re.compile(r"^[a-z0-9_]{1,50}$")


def _feed_lists():
    """(your own feeds, feeds you follow, all public feeds) from PieFed; for guests only the last."""
    public = piefed.get("/feed/list", cache="user" if logged_in() else True, include_communities="false")
    allf = [f for f in (public.get("feeds") or []) if isinstance(f, dict)]
    flat = []

    def walk(items, depth=0):
        for f in items:
            if isinstance(f, dict) and depth < 20:
                flat.append(f)
                walk(f.get("children") or [], depth + 1)
    walk(allf)
    if not logged_in():
        return [], [], flat
    followed = [f for f in flat if f.get("subscribed")]
    flat_public, flat = flat, []
    try:
        walk(piefed.get("/feed/list", cache=False, mine_only="true", include_communities="false").get("feeds") or [])
    except ApiError:
        pass
    return flat, followed, flat_public


@bp.route("/feeds")
def feeds():
    mine, followed, public = _feed_lists()
    return render_template("feeds.html", mine=mine, followed=followed, public=public)


@bp.route("/f/<int:feed_id>/follow", methods=["POST"])
def follow_feed(feed_id):
    if (r := require_login()):
        return r
    follow = request.form.get("follow") == "1"
    try:
        piefed.post("/feed/follow", {"feed_id": feed_id, "follow": follow})
    except ApiError as e:
        # PieFed can report an error after the change went through (leaving a feed while auto-leave
        # is on and you're not in one of its forums), or do nothing for a few seconds right after
        # the opposite change (it caches feed membership briefly): go by what it says now
        piefed.forget_user_cache()
        info = piefed.feed_info(feed_id)
        if not info or bool(info.get("subscribed")) != follow:
            flash(f"PieFed didn't change that: {e} If you just (un)followed it, try again in a moment.")
            return back(url_for("bb.feed", feed_id=feed_id))
    piefed.forget_user_cache()
    flash("You follow this feed now." if follow else "You no longer follow this feed.")
    return back(url_for("bb.feed", feed_id=feed_id))


def _own_feed(feed_id):
    info = piefed.feed_info(feed_id)
    if not info:
        abort(404)
    if not info.get("owner"):
        abort(403, "Only the feed's owner can change it.")
    return info


def _communities_text(text):
    """The form's forum list as PieFed wants it: one name@site per line, without "!" or blanks."""
    out = []
    for line in (text or "").replace(",", "\n").splitlines():
        line = line.strip().lstrip("!")
        if line and line not in out:
            out.append(line[:255])
    return "\n".join(out[:200])


def _feed_form(info=None):
    """Payload for /feed from the form; None (after a flash) when something's wrong."""
    f = request.form
    title = f.get("title", "").strip()[:255]
    if not title:
        flash("Give the feed a title.")
        return None
    payload = {"title": title, "description": f.get("description", "").strip()[:10000],
               "communities": _communities_text(f.get("communities")),
               "public": bool(f.get("public")), "nsfw": bool(f.get("nsfw")),
               "show_child_posts": bool(f.get("show_child_posts"))}
    parent = f.get("parent_feed_id", "")
    payload["parent_feed_id"] = int(parent) if parent.isdigit() and (not info or int(parent) != info["id"]) else None
    icon = request.files.get("icon")
    if icon and icon.filename:
        url = _upload(icon)
        if not url:
            return None
        payload["icon_url"] = url
    if info is None:
        slug = f.get("name", "").strip().lower()
        if not SLUG.match(slug):
            flash("The address name can only have lowercase letters, digits and _ (at most 50).")
            return None
        payload["name"] = slug
    return payload


def _wanted(text):
    return {line.split("@")[0].lower() for line in text.splitlines() if line}


def _save_feed(payload, feed_id=None):
    """Make (feed_id None) or change a feed; returns its id, or None after a flash.

    PieFed 1.8/1.9 fail with an internal error when the API adds a forum to a feed that the user
    doesn't follow (it then checks the web login's auto-follow setting, which API calls don't have).
    The forum is added before that error and the rest of the list isn't, so: save again until
    every forum is in or nothing changes any more."""
    want = _wanted(payload.get("communities") or "")
    keep = ("parent_feed_id", "description")
    try:
        if feed_id is None:
            made = piefed.post("/feed", payload, timeout=90)
            feed_id = made.get("id") if isinstance(made, dict) else None
        else:
            piefed.post("/feed", dict(payload, feed_id=feed_id), method="PUT", keep=keep, timeout=90)
        return feed_id
    except ApiError as e:
        first_error = e
    if feed_id is None:  # the feed itself may exist: find it by its address name
        try:
            own = piefed.get("/feed/list", cache=False, mine_only="true", include_communities="false")
        except ApiError:
            own = {}
        feed_id = next((f.get("id") for f in own.get("feeds") or [] if isinstance(f, dict)
                        and (f.get("name") or "").split("/")[0] == payload.get("name")), None)
        if feed_id is None:
            flash(f"PieFed didn't make the feed: {first_error}")
            return None
    have = None
    for _ in range(len(want) + 1):
        piefed.forget_user_cache()
        info = piefed.feed_info(feed_id) or {}
        now = {c["name"].lower() for c in info.get("communities") or []}
        if want <= now or now == have:
            break
        have = now
        try:
            piefed.post("/feed", {"feed_id": feed_id, "communities": payload["communities"]}, method="PUT", timeout=90)
        except ApiError:
            pass
    missing = want - {c["name"].lower() for c in (piefed.feed_info(feed_id) or {}).get("communities") or []}
    if missing:
        flash("PieFed couldn't add these forums: " + ", ".join(sorted(missing))
              + ". Check the names (name@site), or try again.")
    return feed_id


@bp.route("/feeds/new", methods=["GET", "POST"])
def new_feed():
    if (r := require_login()):
        return r
    mine = _feed_lists()[0]
    form = {"public": True, "communities": request.args.get("communities", "")}
    if request.method == "POST":
        form = request.form
        payload = _feed_form()
        if payload:
            fid = _save_feed(payload)
            if fid:
                piefed.forget_user_cache()
                piefed.refresh_feed_tree()
                flash("Feed made.")
                return redirect(url_for("bb.feed", feed_id=fid))
    return render_template("feed_form.html", info=None, form=form, mine=mine)


@bp.route("/f/<int:feed_id>/edit", methods=["GET", "POST"])
def edit_feed(feed_id):
    if (r := require_login()):
        return r
    info = _own_feed(feed_id)
    mine = [f for f in _feed_lists()[0] if f.get("id") != feed_id]
    form = {"title": info["name"], "description": info.get("description_md") or "",
            "communities": "\n".join(f"{c['name']}@{c.get('ap_domain') or ''}".rstrip("@")
                                     for c in info["communities"]),
            "public": info.get("public"), "nsfw": info.get("nsfw"),
            "show_child_posts": info.get("show_posts_from_children"),
            "parent_feed_id": info.get("parent_feed_id")}
    if request.method == "POST":
        if request.form.get("action") == "delete":
            if request.form.get("confirm") != "1":
                flash("Tick the box to confirm deleting the feed.")
                return redirect(url_for("bb.edit_feed", feed_id=feed_id))
            piefed.post("/feed/delete", {"feed_id": feed_id, "deleted": True})
            piefed.forget_user_cache()
            piefed.refresh_feed_tree()
            flash("Feed deleted.")
            return redirect(url_for("bb.feeds"))
        form = request.form
        payload = _feed_form(info)
        if payload and _save_feed(payload, feed_id):
            piefed.forget_user_cache()
            piefed.refresh_feed_tree()
            flash("Feed saved.")
            return redirect(url_for("bb.feed", feed_id=feed_id))
    return render_template("feed_form.html", info=info, form=form, mine=mine)
