"""Routes added on top of the core forum views: editing, deleting, reporting, poll votes,
notifications, private messages, unread topics, saved posts, blocking and the moderator control panel."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import abort, current_app, flash, redirect, render_template, request, url_for

from . import piefed
from .piefed import ApiError
from .views import (EMPTY_TOPIC, POLL_DAYS, _upload, assign_flair, attach_upload, back, bp, logged_in, me_info,
                    page_arg, recent_blocks, remember_block, require_login, safe_target, topic_choices, topic_extras,
                    topic_form)
from .schema import PM_SETTINGS

REPORT_REASONS = ["Spam", "Harassment or abuse", "Hate speech", "Misinformation", "NSFW not marked",
                  "Off-topic", "Breaks the forum rules", "Other"]


def _me():
    me, is_admin, mods = me_info()
    if not me:
        abort(403, "You must be logged in to do that.")
    return me, is_admin, mods


def _post_view(post_id):
    pv = piefed.get("/post", id=post_id, cache=False).get("post_view") or {}
    if not (pv.get("post") or {}).get("id"):
        abort(404)
    return pv


def _comment_view(comment_id):
    cv = piefed.get("/comment", id=comment_id, cache=False).get("comment_view") or {}
    if not (cv.get("comment") or {}).get("id"):
        abort(404)
    return cv


def _own(view):
    me, _, _ = _me()
    if (view.get("creator") or {}).get("id") != me.get("id"):
        abort(403, "You can only change your own posts.")


# ---- edit / delete -------------------------------------------------------------

def _local_input(utc, tz):
    """PieFed's UTC time as a datetime-local value in timezone tz ("" if missing or unreadable)."""
    try:
        when = datetime.fromisoformat((utc or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M")


def _edit_form(p, pv):
    """The edit form filled from the topic as it is now, polls and events included."""
    tz = current_app.config["TIMEZONE"]
    form = dict(EMPTY_TOPIC, title=p["title"], body=p.get("body") or "", url=p.get("url") or "", nsfw=p["nsfw"],
                flair=[f["id"] for f in pv.get("flair_list") or []], language_id=p.get("language_id"), tz=tz)
    if p.get("poll"):
        form.update(poll_text="\n".join(c["text"] for c in p["poll"]["choices"]), poll_mode=p["poll"]["mode"])
    if p.get("event"):
        e = p["event"]
        form.update(ev_start=_local_input(e["start"], tz), ev_end=_local_input(e["end"], tz),
                    ev_link=e.get("online_link") or "", ev_max=str(e["max"]) if e.get("max") else "")
    return form


@bp.route("/p/<int:post_id>/edit", methods=["GET", "POST"])
def edit_topic(post_id):
    if (r := require_login()):
        return r
    pv = _post_view(post_id)
    _own(pv)
    p = pv["post"]
    kind = "poll" if p.get("poll") else "event" if p.get("event") else None
    flairs, languages = topic_choices(pv["community"]["id"])
    form = _edit_form(p, pv)

    def show(**extra):
        return render_template("posting.html", mode="edit_topic", pv=pv, form=form, poll_days=POLL_DAYS,
                               flairs=flairs, languages=languages, kind=kind, **extra)
    if request.method == "POST":
        form = topic_form()
        if not kind:
            form["poll_text"] = form["ev_start"] = form["ev_end"] = ""  # a topic can't become a poll or event here
        if request.form.get("upload"):
            form["body"] = attach_upload(form["body"])
            return show()
        if request.form.get("preview"):
            return show(preview=True)
        poll, event, problem = topic_extras(form)
        if not form["title"]:
            flash("You must enter a subject.")
        elif problem:
            flash(problem)
        elif kind == "poll" and not poll:
            flash("A poll needs at least two options.")
        elif kind == "event" and not event:
            flash("Enter when the event starts (and ends).")
        else:
            payload = {"post_id": post_id, "title": form["title"], "body": form["body"], "nsfw": form["nsfw"],
                       "language_id": form["language_id"], "poll": poll, "event": event}
            keep = ["body"]  # so removing the text actually clears it
            if not kind:  # sending "url" makes PieFed turn a poll or event into a plain topic
                payload["url"] = form["url"] or None
                keep.append("url")
            try:
                piefed.post("/post", payload, method="PUT", keep=tuple(keep))
                if pv.get("activity_alert"):  # PieFed may drop the author's follow on an edit: restore it
                    try:
                        piefed.post("/post/subscribe", {"post_id": post_id, "subscribe": True}, method="PUT")
                    except piefed.SessionExpired:
                        raise
                    except ApiError:  # e.g. "already existed": still following, nothing to do
                        pass
                if flairs and sorted(form["flair"]) != sorted(f["id"] for f in pv.get("flair_list") or []):
                    assign_flair(post_id, form["flair"], flairs)
                flash("Topic updated." + (" The poll's votes were reset." if kind == "poll" else ""))
                return redirect(url_for("bb.thread", post_id=post_id))
            except piefed.SessionExpired:
                raise
            except ApiError as e:
                flash(e.message)
    return show()


@bp.route("/comment/<int:comment_id>/edit", methods=["GET", "POST"])
def edit_reply(comment_id):
    if (r := require_login()):
        return r
    cv = _comment_view(comment_id)
    _own(cv)
    pv = _post_view(cv["comment"]["post_id"] or cv["post"]["id"])
    body = cv["comment"]["body"]
    if request.method == "POST":
        body = request.form.get("body", "")
        if request.form.get("upload"):
            return render_template("posting.html", mode="edit_reply", pv=pv, comment_id=comment_id,
                                   form={"body": attach_upload(body)})
        if request.form.get("preview"):
            return render_template("posting.html", mode="edit_reply", pv=pv, comment_id=comment_id,
                                   form={"body": body}, preview=True)
        if not body.strip():
            flash("You must enter a message.")
        else:
            try:
                piefed.post("/comment", {"comment_id": comment_id, "body": body}, method="PUT")
                flash("Post updated.")
                return redirect(url_for("bb.comment_link", comment_id=comment_id))
            except piefed.SessionExpired:
                raise
            except ApiError as e:
                flash(e.message)
    return render_template("posting.html", mode="edit_reply", pv=pv, comment_id=comment_id, form={"body": body})


@bp.route("/delete/<kind>/<int:obj_id>", methods=["POST"])
def delete_own(kind, obj_id):
    if (r := require_login()):
        return r
    if kind not in ("post", "comment"):
        abort(400)
    view = _post_view(obj_id) if kind == "post" else _comment_view(obj_id)
    _own(view)
    deleted = request.form.get("deleted") != "0"
    piefed.post(f"/{kind}/delete", {f"{kind}_id": obj_id, "deleted": deleted})
    flash(("Topic" if kind == "post" else "Post") + (" deleted." if deleted else " restored."))
    return back(url_for("bb.index"))


# ---- reports ---------------------------------------------------------------------

@bp.route("/report/<kind>/<int:obj_id>", methods=["GET", "POST"])
def report(kind, obj_id):
    if (r := require_login()):
        return r
    if kind not in ("post", "comment"):
        abort(400)
    view = _post_view(obj_id) if kind == "post" else _comment_view(obj_id)
    if request.method == "POST":
        reason = request.form.get("reason", "")
        if reason not in REPORT_REASONS:
            flash("Choose a reason.")
        else:
            try:
                piefed.post(f"/{kind}/report", {f"{kind}_id": obj_id, "reason": reason,
                                                "description": request.form.get("description", "").strip()[:500]
                                                or None, "report_remote": True})
                flash("Thank you, the moderators have been notified.")
                target = (url_for("bb.thread", post_id=obj_id) if kind == "post"
                          else url_for("bb.comment_link", comment_id=obj_id))
                return redirect(target)
            except piefed.SessionExpired:
                raise
            except ApiError as e:
                flash(e.message)
    return render_template("report.html", kind=kind, view=view, reasons=REPORT_REASONS)


# ---- polls -----------------------------------------------------------------------

@bp.route("/p/<int:post_id>/vote_poll", methods=["POST"])
def poll_vote(post_id):
    if (r := require_login()):
        return r
    choices = []
    for v in request.form.getlist("choice"):
        if v.isdecimal():
            choices.append(int(v))
    if not choices:
        flash("Pick an option first.")
    else:
        piefed.post("/post/poll_vote", {"post_id": post_id, "choice_id": choices[:10]})
        flash("Vote recorded.")
    return redirect(url_for("bb.thread", post_id=post_id))


# ---- notifications -----------------------------------------------------------------

@bp.route("/notifications")
def notifications():
    if (r := require_login()):
        return r
    show = "All" if request.args.get("show") == "all" else "Unread"
    page = page_arg()
    data = piefed.get("/user/notifications", cache=False, status=show, page=page, limit=25)
    return render_template("notifications.html", data=data, show=show, page=page,
                           has_next=bool(data.get("next_page")))


@bp.route("/notifications/read", methods=["POST"])
def notification_read():
    if (r := require_login()):
        return r
    if request.form.get("all"):
        piefed.post("/user/mark_all_notifications_read", {}, method="PUT")
        piefed.forget_badge()
        flash("All notifications marked as read.")
    else:
        nid = request.form.get("id", type=int)
        if not nid:
            abort(400)
        piefed.post("/user/notification_state", {"notif_id": nid, "read_state": True}, method="PUT")
        piefed.forget_badge()
        go = safe_target(request.form.get("go"), "")
        if go:
            return redirect(go)
    return redirect(url_for("bb.notifications"))


# ---- private messages ------------------------------------------------------------

@bp.route("/messages")
def messages():
    if (r := require_login()):
        return r
    me, _, _ = _me()
    page = page_arg()
    pms = piefed.get("/private_message/list", cache=False, page=page, limit=50).get("private_messages") or []
    # newest message per conversation partner, like a phpBB inbox
    convos = {}
    for m in pms:
        other = m["recipient"] if m["creator"]["id"] == me.get("id") else m["creator"]
        key = other["id"]
        if key is None:
            continue
        c = convos.setdefault(key, {"other": other, "last": m, "unread": 0})
        if (m["published"] or "") > (c["last"]["published"] or ""):
            c["last"] = m
        if not m["read"] and m["creator"]["id"] != me.get("id"):
            c["unread"] += 1
    rows = sorted(convos.values(), key=lambda c: c["last"]["published"] or "", reverse=True)
    return render_template("messages.html", rows=rows, page=page, has_next=len(pms) >= 50)


@bp.route("/messages/<int:person_id>", methods=["GET", "POST"])
def conversation(person_id):
    if (r := require_login()):
        return r
    me, _, _ = _me()
    if person_id == me.get("id"):
        abort(400, "You can't send a message to yourself.")
    other = (piefed.get("/user", person_id=person_id).get("person_view") or {}).get("person") or {}
    if not other.get("id"):
        abort(404)
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if not content:
            flash("You must enter a message.")
        else:
            try:
                piefed.post("/private_message", {"content": content[:10000], "recipient_id": person_id})
                return redirect(url_for("bb.conversation", person_id=person_id))
            except piefed.SessionExpired:
                raise
            except ApiError as e:
                flash(e.message)
    page = page_arg()
    try:
        data = piefed.get("/private_message/conversation", cache=False, person_id=person_id, page=page, limit=50)
    except piefed.SessionExpired:
        raise
    except ApiError:
        data = {}  # no conversation yet
    pms = data.get("private_messages") or []
    pms.sort(key=lambda m: m["published"] or "", reverse=True)  # newest on top, under the reply box
    unread_pms = [m for m in pms if not m["read"] and m["creator"]["id"] == person_id][:20]
    for m in unread_pms:
        try:
            piefed.post("/private_message/mark_as_read", {"private_message_id": m["id"], "read": True})
        except piefed.SessionExpired:
            raise
        except ApiError:
            pass
    if unread_pms:
        piefed.forget_badge()
    return render_template("conversation.html", other=other, pms=pms, draft=request.form.get("content", ""),
                           page=page, has_next=len(pms) >= 50)  # PieFed's response has no next_page here


# ---- your settings -----------------------------------------------------------------

NSFW_CHOICES = ("Hide", "Blur", "Show")


@bp.route("/settings", methods=["GET", "POST"])
def settings_page():
    """Profile and preferences of the logged-in account, saved on PieFed (/user/save_user_settings)."""
    if (r := require_login()):
        return r
    me = piefed.get("/user/me", cache=False)
    if request.method == "POST":
        nsfw = request.form.get("nsfw_visibility")
        pm = request.form.get("accept_private_messages")
        payload = {
            "display_name": request.form.get("display_name", "").strip()[:255] or None,  # empty = use the username
            "bio": request.form.get("bio", "").strip()[:5000],
            "accept_private_messages": pm if pm in PM_SETTINGS else me["settings"]["accept_private_messages"],
            "nsfw_visibility": nsfw if nsfw in NSFW_CHOICES else me["settings"]["nsfw_visibility"],
            "show_read_posts": bool(request.form.get("show_read_posts")),
            "email_unread": bool(request.form.get("email_unread")),
        }
        payload["show_nsfw"] = payload["nsfw_visibility"] != "Hide"
        keep = ["display_name", "bio"]
        f = request.files.get("avatar")
        if f and f.filename:
            url = _upload(f, "/upload/user_image")
            if not url:  # _upload has said why
                return redirect(url_for("bb.settings_page"))
            payload["avatar"] = url
        elif request.form.get("remove_avatar"):
            payload["avatar"] = None
            keep.append("avatar")
        try:
            piefed.post("/user/save_user_settings", payload, method="PUT", keep=keep)
        except piefed.SessionExpired:
            raise
        except ApiError as e:
            flash(e.message)
            return redirect(url_for("bb.settings_page"))
        piefed.forget_session()  # this worker's cached copy of /site holds the old name
        flash("Settings saved.")
        return redirect(url_for("bb.settings_page"))
    return render_template("settings.html", account=me["person"], prefs=me["settings"], nsfw_choices=NSFW_CHOICES)


# ---- unread topics -----------------------------------------------------------------

UNREAD_SCAN_PAGES = 3


@bp.route("/unread")
def unread():
    if (r := require_login()):
        return r
    # unread topics, or topics with new replies, among the newest in the user's subscribed forums
    posts = []
    for page in range(1, UNREAD_SCAN_PAGES + 1):
        batch = piefed.get("/post/list", cache=False, type_="Subscribed", sort="New", page=page,
                           limit=50).get("posts") or []
        posts += [p for p in batch if not p["read"] or p["unread_comments"]]
        if len(batch) < 50:
            break
    return render_template("unread.html", posts=posts, scanned=UNREAD_SCAN_PAGES * 50)


# ---- following topics and crossposting ---------------------------------------------

@bp.route("/p/<int:post_id>/follow", methods=["POST"])
def follow_topic(post_id):
    """Notifications about new replies in a topic (PieFed's post subscription)."""
    if (r := require_login()):
        return r
    following = request.form.get("follow") != "0"
    piefed.post("/post/subscribe", {"post_id": post_id, "subscribe": following}, method="PUT")
    flash("You'll get a notification when someone replies." if following else "You no longer follow this topic.")
    return back(url_for("bb.thread", post_id=post_id))


ALERT_KINDS = {"community": ("/community/subscribe", "community_id"), "topic": ("/topic/subscribe", "topic_id"),
               "feed": ("/feed/subscribe", "feed_id")}


@bp.route("/alerts/<kind>/<int:obj_id>", methods=["POST"])
def new_topic_alerts(kind, obj_id):
    """Notifications about every new topic in a forum, or in a whole category or feed (PieFed 1.8.11+)."""
    if (r := require_login()):
        return r
    if kind not in ALERT_KINDS:
        abort(400)
    path, key = ALERT_KINDS[kind]
    on = request.form.get("alert") != "0"
    piefed.post(path, {key: obj_id, "subscribe": on}, method="PUT")
    flash("You'll get a notification for every new topic here." if on else "No more notifications for new topics here.")
    return back(url_for("bb.index"))


@bp.route("/p/<int:post_id>/crosspost", methods=["GET", "POST"])
def crosspost(post_id):
    """Pick a forum to post this topic in too; the new-topic form there comes filled in."""
    if (r := require_login()):
        return r
    pv = _post_view(post_id)
    if request.method == "POST":
        name = (request.form.get("forum") or request.form.get("other", "")).strip().lstrip("!")
        if not name:
            flash("Choose a forum.")
        else:
            return redirect(url_for("bb.new_topic", name=name, crosspost=post_id))
    forums_ = []
    for n in range(1, 5):
        batch = piefed.get("/community/list", type_="Subscribed", sort="Active", page=n, limit=50).get("communities") or []
        forums_ += [c["community"] for c in batch if c["community"]["id"] != pv["community"]["id"]]
        if len(batch) < 50:
            break
    forums_.sort(key=lambda c: (c.get("title") or c.get("name") or "").lower())
    return render_template("crosspost.html", pv=pv, forums=forums_)


# ---- saved posts -------------------------------------------------------------------

@bp.route("/save/<kind>/<int:obj_id>", methods=["POST"])
def save(kind, obj_id):
    if (r := require_login()):
        return r
    if kind not in ("post", "comment"):
        abort(400)
    saving = request.form.get("save") != "0"
    piefed.post(f"/{kind}/save", {f"{kind}_id": obj_id, "save": saving}, method="PUT")
    flash(("Saved." if saving else "Removed from your saved posts."))
    anchor = ("#p" if kind == "post" else "#c") + str(obj_id)
    return redirect(safe_target(request.form.get("next") or request.referrer, url_for("bb.saved")) + anchor)


SAVED_PER_PAGE = 25


@bp.route("/saved")
def saved():
    if (r := require_login()):
        return r
    show = "posts" if request.args.get("show") == "posts" else "topics"
    page = page_arg()
    if show == "topics":
        items = piefed.get("/post/list", cache=False, saved_only="true", sort="New", page=page,
                           limit=SAVED_PER_PAGE).get("posts") or []
    else:
        items = piefed.get("/comment/list", cache=False, saved_only="true", sort="New", page=page,
                           limit=SAVED_PER_PAGE).get("comments") or []
    return render_template("saved.html", show=show, items=items, page=page, has_next=len(items) >= SAVED_PER_PAGE)


# ---- blocking ----------------------------------------------------------------------

@bp.route("/block/<kind>/<int:obj_id>", methods=["POST"])
def block(kind, obj_id):
    if (r := require_login()):
        return r
    if kind not in ("user", "community"):
        abort(400)
    me, _, _ = _me()
    if kind == "user" and obj_id == me.get("id"):
        abort(400, "You can't block yourself.")
    blocking = request.form.get("block") != "0"
    key = "person_id" if kind == "user" else "community_id"
    piefed.post(f"/{kind}/block", {key: obj_id, "block": blocking})
    remember_block(kind, obj_id, blocking)
    what = "User" if kind == "user" else "Forum"
    flash(f"{what} blocked: you won't see their posts any more." if blocking and kind == "user"
          else f"{what} blocked: its topics are hidden from your lists." if blocking
          else f"{what} unblocked.")
    return back(url_for("bb.blocked"))


@bp.route("/blocked")
def blocked():
    if (r := require_login()):
        return r
    my = piefed.get("/user/me", cache=False)
    people = _with_recent(my.get("person_blocks") or [], "user", "/user", "person_id", "person_view", "person")
    communities = _with_recent(my.get("community_blocks") or [], "community", "/community", "id",
                               "community_view", "community")
    return render_template("blocked.html", people=people, communities=communities)


def _with_recent(items, kind, path, param, view_key, obj_key):
    """PieFed's block list with the visitor's own changes of the last minutes applied (its list lags)."""
    recent = recent_blocks(kind)
    out = [x for x in items if recent.get(x["id"], True)]
    have = {x["id"] for x in out}
    for obj_id, now_blocked in recent.items():
        if now_blocked and obj_id not in have:
            try:
                obj = (piefed.get(path, **{param: obj_id}).get(view_key) or {}).get(obj_key) or {}
            except ApiError:
                continue
            if obj.get("id"):
                out.append(obj)
    return out


@bp.route("/mark_read", methods=["POST"])
def mark_read():
    if (r := require_login()):
        return r
    ids = []
    for v in request.form.getlist("post_id"):
        if v.isdecimal():
            ids.append(int(v))
    ids = list(dict.fromkeys(ids))[:200]
    if ids:
        piefed.post("/post/mark_as_read", {"post_ids": ids, "read": True})
        flash(f"Marked {len(ids)} topic{'s' if len(ids) != 1 else ''} as read.")
    return back(url_for("bb.index"))


# ---- forum settings (moderators) ---------------------------------------------------

COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def _mod_community(name):
    """(community view, moderators) of a forum the visitor may manage, or abort."""
    info = piefed.get("/community", name=name, cache=False)
    cv = info.get("community_view") or {}
    c = cv.get("community") or {}
    if not c.get("id"):
        abort(404)
    _, is_admin, mods = _me()
    if not (is_admin or c["id"] in mods):
        abort(403, "Only this forum's moderators can change its settings.")
    if not c.get("local"):
        abort(400, "This forum lives on another instance; its settings can only be changed there.")
    return cv, info.get("moderators") or []


@bp.route("/c/<path:name>/settings", methods=["GET", "POST"])
def forum_settings(name):
    if (r := require_login()):
        return r
    cv, mods = _mod_community(name)
    cid = cv["community"]["id"]
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "details":
                payload = {"community_id": cid, "title": request.form.get("title", "").strip()[:255] or None,
                           "description": request.form.get("description", "").strip()[:10000],
                           "nsfw": bool(request.form.get("nsfw")),
                           "restricted_to_mods": bool(request.form.get("restricted_to_mods"))}
                keep = ["description"]
                f = request.files.get("icon")
                if f and f.filename:
                    url = _upload(f, "/upload/community_image")
                    if not url:
                        return redirect(url_for("bb.forum_settings", name=name))
                    payload["icon_url"] = url
                elif request.form.get("remove_icon"):
                    payload["icon_url"] = None
                    keep.append("icon_url")
                piefed.post("/community", payload, method="PUT", keep=tuple(keep))
                flash("Forum settings saved.")
            elif action in ("mod_add", "mod_remove"):
                if action == "mod_add":
                    who = request.form.get("username", "").strip().lstrip("@")
                    person = {}
                    if who:
                        try:
                            person = (piefed.get("/user", username=who).get("person_view") or {}).get("person") or {}
                        except piefed.SessionExpired:
                            raise
                        except ApiError:
                            person = {}
                    if not person.get("id"):
                        raise ApiError(404, "There's no user with that name.")
                    person_id = person["id"]
                else:
                    person_id = request.form.get("person_id", type=int)
                    if not person_id:
                        abort(400)
                piefed.post("/community/mod", {"community_id": cid, "person_id": person_id,
                                                "added": action == "mod_add"})
                flash("Moderator added." if action == "mod_add" else "Moderator removed.")
            elif action == "flair_add":
                title = request.form.get("flair_title", "").strip()[:50]
                text_color = request.form.get("text_color", "")
                bg_color = request.form.get("background_color", "")
                if not title:
                    raise ApiError(400, "Give the flair a name.")
                piefed.post("/community/flair", {
                    "community_id": cid, "flair_title": title,
                    "text_color": text_color if COLOR.fullmatch(text_color) else "#000000",
                    "background_color": bg_color if COLOR.fullmatch(bg_color) else "#DEDDDA"})
                flash("Flair added.")
            elif action == "flair_delete":
                flair_id = request.form.get("flair_id", type=int)
                if not flair_id:
                    abort(400)
                piefed.post("/community/flair/delete", {"flair_id": flair_id})
                flash("Flair removed.")
            else:
                abort(400)
        except piefed.SessionExpired:
            raise
        except ApiError as e:
            flash(e.message)
        return redirect(url_for("bb.forum_settings", name=name))
    try:
        bans = piefed.get("/community/moderate/bans", cache=False, community_id=cid, limit=50).get("items") or []
    except piefed.SessionExpired:
        raise
    except ApiError:
        bans = []
    return render_template("forum_settings.html", cv=cv, mods=mods, bans=bans)


# ---- moderator control panel -----------------------------------------------------

REPORTS_PER_PAGE = 20
MODLOG_PER_PAGE = 20


def load_moderation(page, show, unresolved):
    data, error = {}, None
    try:
        if show == "reports":
            flag = "true" if unresolved else "false"
            data["posts"] = piefed.get("/post/report/list", cache=False, page=page, limit=REPORTS_PER_PAGE,
                                       unresolved_only=flag).get("post_reports") or []
            data["comments"] = piefed.get("/comment/report/list", cache=False, page=page, limit=REPORTS_PER_PAGE,
                                          unresolved_only=flag).get("comment_reports") or []
        else:
            data["modlog"] = piefed.get("/modlog", cache=False, page=page, limit=MODLOG_PER_PAGE).get("entries") or []
    except piefed.SessionExpired:
        raise
    except ApiError as e:
        error = e.message
    has_next = (len(data.get("posts", [])) >= REPORTS_PER_PAGE or len(data.get("comments", [])) >= REPORTS_PER_PAGE
                if show == "reports" else len(data.get("modlog", [])) >= MODLOG_PER_PAGE)
    return data, error, has_next


@bp.route("/modcp")
def modcp():
    if (r := require_login()):
        return r
    _, is_admin, mods = _me()
    if not (is_admin or mods):
        abort(403, "Only moderators can open the moderator control panel.")
    show = request.args.get("show") if request.args.get("show") in ("reports", "modlog") else "reports"
    page = page_arg()
    unresolved = request.args.get("all") != "1"
    data, error, has_next = load_moderation(page, show, unresolved)
    return render_template("modcp.html", show=show, page=page, unresolved=unresolved, data=data, error=error,
                           has_next=has_next, panel="modcp")


@bp.route("/modcp/resolve", methods=["POST"])
def modcp_resolve():
    if (r := require_login()):
        return r
    _, is_admin, mods = _me()
    if not (is_admin or mods):
        abort(403, "Only moderators can resolve reports.")
    kind = request.form.get("kind")
    report_id = request.form.get("report_id", type=int)
    if kind not in ("post", "comment") or not report_id:
        abort(400)
    # PieFed itself checks that this moderator may resolve this particular report
    piefed.post(f"/{kind}/report/resolve", {"report_id": report_id,
                                             "resolved": request.form.get("resolved") != "0"}, method="PUT")
    flash("Report updated.")
    return redirect(safe_target(request.form.get("next"), url_for("bb.modcp")))


def unread_counts():
    """Header badges for a logged-in user: (notifications, private messages). Cached per user for 15 s."""
    if not logged_in():
        return 0, 0
    try:
        c = piefed.unread_count()
    except ApiError:  # includes SessionExpired: the page just renders without badges
        return 0, 0
    return c.get("replies", 0) + c.get("mentions", 0) + c.get("other", 0), c.get("private_messages", 0)


@bp.app_context_processor
def inject_unread():
    notes, pms = unread_counts()
    return {"unread_notes": notes, "unread_pms": pms}
