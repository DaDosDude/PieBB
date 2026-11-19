"""Routes for the rest of PieFed's API: following users, user notes, hiding topics, blocking domains,
mentions and replies, reply notifications, message actions, scheduled topics, link previews,
accepted answers, extra moderator tools and blocklists."""
import re

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for  # noqa: F401

from . import piefed, render  # noqa: F401
from .piefed import ApiError  # noqa: F401
from .views import back, bp, logged_in, remember_block, require_login  # noqa: F401


# ---- users: post alerts and private notes ---------------------------------------------------------

@bp.route("/u/id/<int:person_id>/alert", methods=["POST"])
def user_alert(person_id):
    if (r := require_login()):
        return r
    on = request.form.get("alert") == "1"
    piefed.post("/user/subscribe", {"person_id": person_id, "subscribe": on}, method="PUT")
    remember_block("useralert", person_id, on)  # PieFed's profile answer lags up to 10 minutes
    piefed.forget_user_cache()
    flash("You'll get a notification when they post." if on else "No more notifications about their posts.")
    return back(url_for("bb.profile_by_id", person_id=person_id))


@bp.route("/u/id/<int:person_id>/note", methods=["POST"])
def user_note(person_id):
    if (r := require_login()):
        return r
    note = request.form.get("note", "").strip()[:500]
    piefed.post("/user/note", {"person_id": person_id, "note": note}, keep=("note",))
    piefed.forget_user_cache()
    flash("Note saved. Only you can see it." if note else "Note removed.")
    return back(url_for("bb.profile_by_id", person_id=person_id))


# ---- hiding topics, blocking link domains ---------------------------------------------------------

@bp.route("/p/<int:post_id>/hide", methods=["POST"])
def hide_topic(post_id):
    if (r := require_login()):
        return r
    hide = request.form.get("hidden") == "1"
    piefed.post("/post/hide", {"post_id": post_id, "hidden": hide})
    remember_block("posthide", post_id, hide)  # PieFed's topic data doesn't say whether it's hidden
    flash("Topic hidden: it won't show in your topic lists any more. You can still open it here." if hide
          else "Topic unhidden: it shows in your topic lists again.")
    return back(url_for("bb.thread", post_id=post_id))


DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")


@bp.route("/domain/block", methods=["POST"])
def block_domain():
    if (r := require_login()):
        return r
    domain = request.form.get("domain", "").strip().lower().removeprefix("www.")
    if not DOMAIN.match(domain):
        flash("That isn't a domain name (like example.com).")
        return back(url_for("bb.blocked"))
    block = request.form.get("block") != "0"
    try:
        piefed.post("/domain/block", {"domain": domain, "block": block})
    except ApiError as e:
        flash(f"PieFed didn't change that: {e}")
        return back(url_for("bb.blocked"))
    remember_domain(domain, block)
    flash(f"Link topics to {domain} are hidden from you now." if block
          else f"Link topics to {domain} show again.")
    return back(url_for("bb.blocked"))


def remember_domain(domain, block):
    """PieFed's API doesn't list blocked domains: keep the ones blocked here, for the Blocked page."""
    lst = [x for x in (session.get("domains") or []) if isinstance(x, str) and x != domain]
    if block:
        lst.append(domain)
    session["domains"] = lst[-50:]


# ---- reply notifications for one post ------------------------------------------------------------

@bp.route("/comment/<int:comment_id>/alert", methods=["POST"])
def comment_alert(comment_id):
    if (r := require_login()):
        return r
    on = request.form.get("alert") == "1"
    piefed.post("/comment/subscribe", {"comment_id": comment_id, "subscribe": on}, method="PUT")
    remember_block("cmtalert", comment_id, on)  # PieFed's reply lists cache this state for a while
    flash("You'll get a notification when someone replies to that post." if on
          else "No more notifications about replies to that post.")
    nxt = request.form.get("next") or request.referrer or ""
    return back(url_for("bb.comment_link", comment_id=comment_id)) if nxt else \
        redirect(url_for("bb.comment_link", comment_id=comment_id))


# ---- private messages: delete, leave, report ------------------------------------------------------

def _pm_back():
    return back(url_for("bb.messages"))


@bp.route("/pm/<int:pm_id>/delete", methods=["POST"])
def delete_pm(pm_id):
    if (r := require_login()):
        return r
    piefed.post("/private_message/delete", {"private_message_id": pm_id, "deleted": True})
    flash("Message deleted.")
    return _pm_back()


@bp.route("/conversation/<int:conversation_id>/leave", methods=["POST"])
def leave_conversation(conversation_id):
    if (r := require_login()):
        return r
    if request.form.get("confirm") != "1":
        flash("Tick the box to confirm leaving the conversation.")
        return _pm_back()
    piefed.post("/private_message/conversation/leave", {"conversation_id": conversation_id})
    piefed.forget_badge()
    flash("You left the conversation; it's no longer in your messages.")
    return redirect(url_for("bb.messages"))


@bp.route("/pm/report/<kind>/<int:obj_id>", methods=["POST"])
def report_pm(kind, obj_id):
    if (r := require_login()):
        return r
    if kind not in ("message", "conversation"):
        abort(400)
    reason = request.form.get("reason", "").strip()[:200] or "Other"
    if kind == "message":
        piefed.post("/private_message/report", {"private_message_id": obj_id, "reason": reason})
    else:
        piefed.post("/private_message/conversation/report", {"conversation_id": obj_id, "reason": reason})
    flash("Report sent to the admins.")
    return _pm_back()


# ---- scheduled topics -----------------------------------------------------------------------------

def _scheduled_list(page):
    data = piefed.get("/post/scheduled/list", cache=False, sort="New", page=page, limit=25)
    out = []
    for x in data.get("scheduled_posts") or []:
        if not isinstance(x, dict):
            continue
        sp = x.get("scheduled_post") if isinstance(x.get("scheduled_post"), dict) else {}
        c = x.get("community") if isinstance(x.get("community"), dict) else {}
        if not isinstance(sp.get("id"), int):
            continue
        out.append({"id": sp["id"], "title": str(sp.get("title") or ""), "body": str(sp.get("body") or ""),
                    "url": str(sp.get("url") or ""), "when": str(sp.get("next_scheduled_for") or ""),
                    "repeat": str(sp.get("repeat") or "None"),
                    "community": {"name": str(c.get("name") or ""), "title": str(c.get("title") or ""),
                                  "local": bool(c.get("local")), "ap_domain": str(c.get("ap_domain") or ""),
                                  "actor_id": str(c.get("actor_id") or "")}})
    return out, bool(data.get("next_page"))


@bp.route("/scheduled", methods=["GET", "POST"])
def scheduled():
    if (r := require_login()):
        return r
    from .views import SCHEDULE_REPEATS, page_arg, scheduled_for
    if request.method == "POST":
        pid = request.form.get("post_id", type=int)
        if not pid:
            abort(400)
        if request.form.get("action") == "delete":
            piefed.post("/post/scheduled/delete", {"post_id": pid, "deleted": True})
            flash("Scheduled topic deleted.")
        else:
            later = scheduled_for(request.form.get("sched_at", ""))
            repeat = request.form.get("sched_repeat")
            if not later:
                flash("That posting time isn't a valid date and time.")
            else:
                try:
                    piefed.post("/post/scheduled", {"post_id": pid, "scheduled_for": later,
                                                    "repeat": repeat if repeat in SCHEDULE_REPEATS else "None"},
                                method="PUT")
                    flash("New posting time saved.")
                except ApiError as e:
                    flash(e.message)
        return redirect(url_for("bb.scheduled"))
    page = page_arg()
    posts, has_next = _scheduled_list(page)
    return render_template("scheduled.html", posts=posts, page=page, has_next=has_next)


# ---- accepted answers (question & answer forums) --------------------------------------------------

@bp.route("/comment/<int:comment_id>/answer", methods=["POST"])
def mark_answer(comment_id):
    if (r := require_login()):
        return r
    from .views import me_info
    me, is_admin, mods = me_info()
    cv = piefed.get("/comment", id=comment_id, cache=False).get("comment_view") or {}
    if not (cv.get("comment") or {}).get("id"):
        abort(404)
    pv = piefed.get("/post", id=cv["comment"]["post_id"], cache=False).get("post_view") or {}
    community_id = (pv.get("community") or {}).get("id")
    # PieFed's API doesn't check who marks an answer: only the topic's author and its moderators may here
    if not (me and ((pv.get("creator") or {}).get("id") == me.get("id") or is_admin or community_id in mods)):
        abort(403, "Only the topic's author or a moderator can choose the answer.")
    on = request.form.get("answer") == "1"
    piefed.post("/comment/mark_as_answer", {"comment_reply_id": comment_id, "answer": on})
    flash("Marked as the answer." if on else "No longer marked as the answer.")
    return redirect(url_for("bb.comment_link", comment_id=comment_id))


# ---- autocomplete ---------------------------------------------------------------------------------

@bp.route("/suggest")
def suggest():
    """Name suggestions for the forms (forums, users, feeds), from PieFed: q starts with ! @ or ~."""
    q = request.args.get("q", "").strip()[:100]
    if len(q) < 3 or q[0] not in "!@~":
        return jsonify([])
    try:
        res = piefed.get("/suggest_completion", q=q).get("result") or []
    except ApiError:
        res = []
    return jsonify([x for x in res if isinstance(x, str)][:10])


# ---- moderators: who voted, deleting a forum ------------------------------------------------------

def _person(p):
    from .schema import person
    return person(p if isinstance(p, dict) else {})


@bp.route("/votes/<kind>/<int:obj_id>")
def vote_list(kind, obj_id):
    if (r := require_login()):
        return r
    if kind not in ("post", "comment"):
        abort(404)
    from .views import can_moderate, page_arg
    view = piefed.get(f"/{kind}", id=obj_id, cache=False).get(f"{kind}_view") or {}
    community_id = (view.get("community") or {}).get("id")
    if not community_id:
        abort(404)
    if not can_moderate(community_id):
        abort(403, "Only the forum's moderators can see who voted.")
    page = page_arg()
    data = piefed.get(f"/{kind}/like/list", cache=False, page=page, limit=50, **{f"{kind}_id": obj_id})
    votes = [{"score": x.get("score") if isinstance(x.get("score"), int) else 0, "creator": _person(x.get("creator")),
              "banned": bool(x.get("creator_banned_from_community"))}
             for x in data.get(f"{kind}_likes") or [] if isinstance(x, dict)]
    target = url_for("bb.thread", post_id=obj_id) if kind == "post" else url_for("bb.comment_link", comment_id=obj_id)
    title = (view.get("post") or {}).get("title") or ""
    return render_template("votes.html", kind=kind, votes=votes, target=target, title=title, page=page,
                           has_next=bool(data.get("next_page")))


# ---- site admins: message reports, site-wide bans -------------------------------------------------

def _admin():
    from .views import me_info
    me, is_admin, _ = me_info()
    if not is_admin:
        abort(403, "Only site admins can do that.")
    return me


@bp.route("/modcp/messages")
def message_reports():
    if (r := require_login()):
        return r
    _admin()
    from .views import page_arg
    unresolved = request.args.get("all") != "1"
    page = page_arg()
    flag = "true" if unresolved else "false"
    pms = piefed.get("/private_message/report/list", cache=False, unresolved_only=flag, page=page, limit=20)
    convs = piefed.get("/private_message/conversation/report/list", cache=False, unresolved_only=flag, page=page,
                       limit=20, message_history_limit=10)
    msg_reports = []
    for x in pms.get("private_message_reports") or []:
        rep, pm = x.get("private_message_report") or {}, x.get("private_message") or {}
        if isinstance(rep.get("id"), int):
            msg_reports.append({"id": rep["id"], "reason": str(rep.get("reason") or ""),
                                "resolved": bool(rep.get("resolved")), "published": str(rep.get("published") or ""),
                                "text": str(rep.get("original_pm_text") or pm.get("content") or ""),
                                "reporter": _person(x.get("creator")), "author": _person(x.get("private_message_creator"))})
    conv_reports = []
    for x in convs.get("conversation_reports") or []:
        rep, info = x.get("conversation_report") or {}, x.get("conversation_information") or {}
        if isinstance(rep.get("id"), int):
            history = []
            for m in x.get("message_history") or []:
                if isinstance(m, dict):
                    pm = m.get("private_message") or {}
                    history.append({"creator": _person(m.get("creator")), "text": str(pm.get("content") or ""),
                                    "published": str(pm.get("published") or "")})
            conv_reports.append({"id": rep["id"], "reason": str(rep.get("reason") or ""),
                                 "resolved": bool(rep.get("resolved")), "published": str(rep.get("published") or ""),
                                 "reporter": _person(x.get("creator")),
                                 "members": [_person(p) for p in info.get("members") or []], "history": history})
    return render_template("message_reports.html", msg_reports=msg_reports, conv_reports=conv_reports,
                           unresolved=unresolved, page=page,
                           has_next=bool(pms.get("next_page") or convs.get("next_page")))


@bp.route("/modcp/messages/resolve", methods=["POST"])
def message_report_resolve():
    if (r := require_login()):
        return r
    _admin()
    kind, report_id = request.form.get("kind"), request.form.get("report_id", type=int)
    if kind not in ("message", "conversation") or not report_id:
        abort(400)
    path = "/private_message/report/resolve" if kind == "message" else "/private_message/conversation/report/resolve"
    piefed.post(path, {"report_id": report_id, "resolved": request.form.get("resolved") != "0"}, method="PUT")
    flash("Report updated.")
    return redirect(url_for("bb.message_reports"))


@bp.route("/admin/ban/<int:person_id>", methods=["GET", "POST"])
def site_ban(person_id):
    if (r := require_login()):
        return r
    me = _admin()
    pv = piefed.get("/user", person_id=person_id, cache=False).get("person_view") or {}
    person = pv.get("person") or {}
    if not person.get("id"):
        abort(404)
    if person["id"] == me.get("id"):
        abort(400, "You can't ban yourself.")
    if request.method == "POST":
        if request.form.get("action") == "unban":
            piefed.post("/user/unban", {"person_id": person_id})
            flash(f"{person.get('user_name')} is no longer banned from this site.")
        else:
            reason = request.form.get("reason", "").strip()[:50]
            piefed.post("/user/ban", {"person_id": person_id, "reason": reason or None,
                                      "ban_ip_address": bool(request.form.get("ban_ip")),
                                      "purge_content": bool(request.form.get("purge"))},
                        keep=("reason", "ban_ip_address", "purge_content"))
            flash(f"{person.get('user_name')} is banned from this site.")
        return redirect(url_for("bb.profile_by_id", person_id=person_id))
    return render_template("site_ban.html", person=person)


# ---- blocklists (PieFed 1.9+): shared lists of accounts to block ---------------------------------

BL_SLUG = re.compile(r"^[a-z0-9_]{1,50}$")
REMOTE_LOOKUP_TIMEOUT = 90  # PieFed looks up accounts from other sites one by one before answering


def _need_blocklists():
    if not piefed.has("blocklists"):
        abort(404, "This PieFed doesn't have blocklists yet (they came with PieFed 1.9).")


def _bl(x):
    """A blocklist from PieFed's answer, with only the fields the pages use."""
    from .schema import person
    if not isinstance(x, dict) or not isinstance(x.get("id"), int):
        return None
    return {"id": x["id"], "name": str(x.get("name") or ""), "title": str(x.get("title") or x.get("name") or ""),
            "description": str(x.get("description") or ""), "ap_domain": str(x.get("ap_domain") or ""),
            "local": bool(x.get("local")), "owner": bool(x.get("owner")), "subscribed": bool(x.get("subscribed")),
            "followers": x.get("subscriptions_count") if isinstance(x.get("subscriptions_count"), int) else 0,
            "count": x.get("blocked_count") if isinstance(x.get("blocked_count"), int) else 0,
            "blocked": [person(p) for p in x.get("blocked") or [] if isinstance(p, dict)],
            "unresolved": [str(u) for u in x.get("unresolved_members") or [] if isinstance(u, str)]}


def _bl_list(**params):
    try:
        data = piefed.get("/blocklist/list", cache=False, limit=50, **params)
    except ApiError:
        return []
    return [b for b in (_bl(x) for x in data.get("blocklists") or []) if b]


@bp.route("/blocklists")
def blocklists():
    _need_blocklists()
    q = request.args.get("name", "").strip().lstrip("%")
    if q:  # open a blocklist by name@site
        try:
            b = _bl(piefed.get("/blocklist", cache=False, name=q if "@" in q else q))
        except ApiError:
            b = None
        if b:
            return redirect(url_for("bb.blocklist", blocklist_id=b["id"]))
        flash("This site doesn't know a blocklist by that name. Use name@site, e.g. spammers@piefed.social.")
    mine = _bl_list(mine_only="true") if logged_in() else []
    followed = _bl_list(subscribed_only="true") if logged_in() else []
    return render_template("blocklists.html", mine=mine, followed=followed, all_lists=_bl_list())


@bp.route("/blocklist/<int:blocklist_id>")
def blocklist(blocklist_id):
    _need_blocklists()
    try:
        b = _bl(piefed.get("/blocklist", cache=False, id=blocklist_id))
    except ApiError:
        b = None
    if not b:
        abort(404)
    return render_template("blocklist.html", b=b)


@bp.route("/blocklist/<int:blocklist_id>/follow", methods=["POST"])
def follow_blocklist(blocklist_id):
    if (r := require_login()):
        return r
    _need_blocklists()
    follow = request.form.get("follow") == "1"
    try:
        piefed.post("/blocklist/follow", {"blocklist_id": blocklist_id, "follow": follow})
    except ApiError as e:
        flash(f"PieFed didn't change that: {e.message}")
        return back(url_for("bb.blocklist", blocklist_id=blocklist_id))
    piefed.forget_user_cache()
    flash("You follow this blocklist: everyone on it is blocked for you, now and as it changes." if follow
          else "You no longer follow this blocklist. Accounts blocked through it are unblocked.")
    return back(url_for("bb.blocklist", blocklist_id=blocklist_id))


def _members_text(text):
    out = []
    for line in (text or "").replace(",", "\n").splitlines():
        line = line.strip().lstrip("@")
        if line and line not in out:
            out.append(line[:255])
    return "\n".join(out[:5000])


@bp.route("/blocklists/new", methods=["GET", "POST"])
@bp.route("/blocklist/<int:blocklist_id>/edit", methods=["GET", "POST"])
def edit_blocklist(blocklist_id=None):
    if (r := require_login()):
        return r
    _need_blocklists()
    b = None
    if blocklist_id is not None:
        try:
            b = _bl(piefed.get("/blocklist", cache=False, id=blocklist_id))
        except ApiError:
            b = None
        if not b:
            abort(404)
        if not b["owner"]:
            abort(403, "Only the blocklist's owner can change it.")
    form = {"title": b["title"], "description": b["description"],
            "members": "\n".join(render.person_ref(p) for p in b["blocked"]) + "".join(
                "\n" + u for u in b["unresolved"])} if b else {}
    if request.method == "POST":
        f = request.form
        if b and f.get("action") == "delete":
            if f.get("confirm") != "1":
                flash("Tick the box to confirm deleting the blocklist.")
                return redirect(url_for("bb.edit_blocklist", blocklist_id=b["id"]))
            piefed.post("/blocklist/delete", {"blocklist_id": b["id"], "deleted": True})
            flash("Blocklist deleted.")
            return redirect(url_for("bb.blocklists"))
        form = f
        title = f.get("title", "").strip()[:255]
        payload = {"title": title, "description": f.get("description", "").strip()[:10000],
                   "members": _members_text(f.get("members"))}
        if not title:
            flash("Give the blocklist a title.")
        elif b:
            try:
                piefed.post("/blocklist", dict(payload, blocklist_id=b["id"]), method="PUT",
                            keep=("description", "members"), timeout=REMOTE_LOOKUP_TIMEOUT)
                flash("Blocklist saved.")
                return redirect(url_for("bb.blocklist", blocklist_id=b["id"]))
            except ApiError as e:
                flash(f"PieFed didn't save it: {e.message}")
        else:
            slug = f.get("name", "").strip().lower()
            if not BL_SLUG.match(slug):
                flash("The address name can only have lowercase letters, digits and _ (at most 50).")
            else:
                try:
                    made = _bl(piefed.post("/blocklist", dict(payload, name=slug), timeout=REMOTE_LOOKUP_TIMEOUT))
                    flash("Blocklist made. Its accounts are blocked for you and for everyone who follows it.")
                    return redirect(url_for("bb.blocklist", blocklist_id=made["id"]) if made else url_for("bb.blocklists"))
                except ApiError as e:
                    flash(f"PieFed didn't make it: {e.message}")
    return render_template("blocklist_form.html", b=b, form=form)
