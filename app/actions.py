"""Routes added on top of the core forum views: editing, deleting, reporting, polls,
notifications, private messages, unread topics and the moderator control panel."""
from flask import abort, flash, redirect, render_template, request, url_for

from . import piefed
from .piefed import ApiError
from .views import (attach_upload, back, bp, logged_in, me_info, page_arg, require_login, safe_target)

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

@bp.route("/p/<int:post_id>/edit", methods=["GET", "POST"])
def edit_topic(post_id):
    if (r := require_login()):
        return r
    pv = _post_view(post_id)
    _own(pv)
    p = pv["post"]
    form = {"title": p["title"], "body": p.get("body") or "", "url": p.get("url") or "", "nsfw": p["nsfw"]}
    if request.method == "POST":
        form = {"title": request.form.get("title", "").strip(), "body": request.form.get("body", ""),
                "url": request.form.get("url", "").strip(), "nsfw": bool(request.form.get("nsfw"))}
        if request.form.get("upload"):
            form["body"] = attach_upload(form["body"])
            return render_template("posting.html", mode="edit_topic", pv=pv, form=form)
        if request.form.get("preview"):
            return render_template("posting.html", mode="edit_topic", pv=pv, form=form, preview=True)
        if not form["title"]:
            flash("You must enter a subject.")
        else:
            try:
                piefed.post("/post", {"post_id": post_id, "title": form["title"], "body": form["body"],
                                      "url": form["url"] or None, "nsfw": form["nsfw"]}, method="PUT",
                            keep=("body", "url"))  # so removing the link or text actually clears it
                flash("Topic updated.")
                return redirect(url_for("bb.thread", post_id=post_id))
            except piefed.SessionExpired:
                raise
            except ApiError as e:
                flash(e.message)
    return render_template("posting.html", mode="edit_topic", pv=pv, form=form)


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


# ---- unread topics -----------------------------------------------------------------

UNREAD_SCAN_PAGES = 3


@bp.route("/unread")
def unread():
    if (r := require_login()):
        return r
    posts = []
    for page in range(1, UNREAD_SCAN_PAGES + 1):
        batch = piefed.get("/post/list", cache=False, type_="Subscribed", sort="New", page=page,
                           limit=50).get("posts") or []
        posts += [p for p in batch if not p["read"] or p["unread_comments"]]
        if len(batch) < 50:
            break
    return render_template("unread.html", posts=posts, scanned=UNREAD_SCAN_PAGES * 50)


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
