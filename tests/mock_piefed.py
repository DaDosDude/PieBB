"""Minimal fake PieFed /api/alpha for local testing: python tests/mock_piefed.py"""
from flask import Flask, jsonify, request

app = Flask(__name__)
NOW = "2026-10-25T09:00:00.000000Z"
calls = []


def person(i, name, local=True):
    return {"id": i, "user_name": name, "title": None, "local": local, "bot": False, "banned": False,
            "deleted": False, "published": "2024-03-01T12:00:00Z", "instance_id": 1,
            "actor_id": f"https://{'retrofed.com' if local else 'lemmy.world'}/u/{name}",
            "avatar": None}


def community(i, name, title, local=True):
    return {"id": i, "name": name, "title": title, "local": local, "nsfw": False, "deleted": False,
            "removed": False, "hidden": False, "restricted_to_mods": False, "published": NOW, "instance_id": 1,
            "actor_id": f"https://{'retrofed.com' if local else 'lemmy.world'}/c/{name}",
            "ap_domain": "retrofed.com" if local else "lemmy.world",
            "description": f"All about **{title}**.", "icon": None}


COMMS = [community(1, "retrogaming", "Retro Gaming"), community(2, "dos", "MS-DOS"),
         community(3, "amiga", "Amiga", local=False)]
COMMS[0]["icon"] = "https://img.example/x.png'); } body { display:none } .a { b: url('"


def cview(c):
    return {"community": c, "subscribed": "NotSubscribed", "blocked": False, "banned_from_community": False,
            "activity_alert": False, "counts": {"id": c["id"], "subscriptions_count": 120, "post_count": 45,
                                                "post_reply_count": 300, "published": NOW, "active_daily": 3,
                                                "active_weekly": 10, "active_monthly": 30, "active_6monthly": 60}}


def pview(i, c, sticky=False):
    return {"post": {"id": i, "title": f"Post number {i} <script>x</script>", "body": "Hello *world*\n\n> quoted\n\n<img src=x onerror=alert(1)>",
                     "url": "https://example.com/a.png" if i == 102 else "javascript:alert(1)" if i == 104 else None, "creator_id": 5,
                     "community_id": c["id"], "removed": False, "locked": i == 103, "published": NOW,
                     "deleted": False, "nsfw": False, "ap_id": f"https://retrofed.com/post/{i}", "local": True,
                     "language_id": 0, "sticky": sticky},
            "creator": person(5, "DosDude"), "community": c, "creator_banned_from_community": False,
            "banned_from_community": False, "creator_is_moderator": True, "creator_is_admin": True,
            "counts": {"post_id": i, "comments": 3, "score": 7, "upvotes": 8, "downvotes": 1, "published": NOW,
                       "newest_comment_time": NOW},
            "subscribed": "NotSubscribed", "saved": False, "read": False, "hidden": False, "my_vote": 0,
            "unread_comments": 0}


def comment(i, post, path):
    return {"comment": {"id": i, "creator_id": 6, "post_id": post["post"]["id"], "body": f"Comment {i} `code`",
                        "removed": False, "published": f"2026-10-25T09:0{i % 10}:00Z", "deleted": False,
                        "ap_id": "x", "local": False, "path": path, "distinguished": False, "language_id": 0},
            "creator": person(6, "someone", local=False), "post": post["post"], "community": post["community"],
            "counts": {"comment_id": i, "score": 2, "upvotes": 2, "downvotes": 0, "published": NOW, "child_count": 0},
            "creator_banned_from_community": False, "banned_from_community": False, "creator_is_moderator": False,
            "creator_is_admin": False, "subscribed": "NotSubscribed", "saved": False, "activity_alert": False,
            "creator_blocked": False, "my_vote": None}


import os
import random

CHAOS = float(os.environ.get("CHAOS", "0"))  # 0..1: chance to break each field in a response


def _chaos(v, depth=0):
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            r = random.random()
            if r < CHAOS / 3:
                continue                      # drop the key
            if r < CHAOS * 2 / 3:
                out[k] = None                 # null it
            elif r < CHAOS:
                out[k] = random.choice([[], {}, "", 0, -1, "x", True])  # wrong type
            else:
                out[k] = _chaos(x, depth + 1)
        return out
    if isinstance(v, list):
        return [_chaos(x, depth + 1) for x in v]
    return v


@app.after_request
def chaos(resp):
    if CHAOS and resp.is_json and request.path != "/api/alpha/_calls":
        resp.set_data(__import__("json").dumps(_chaos(resp.get_json())))
    return resp


def err(message, status="Bad Request"):
    """PieFed's shared_error_handler: every API error is HTTP 400 with code/message/status."""
    return jsonify({"code": 400, "message": message, "status": status}), 400


ADMIN_JWT, USER_JWT = "TESTJWT", "USERJWT"
REVOKED = set()


def auth():
    """Mirror authorise_api_user: 'admin', 'user', None (no token) or 'bad' (revoked/garbage)."""
    h = request.headers.get("Authorization", "")
    if not h:
        return None
    tok = h[7:] if h.startswith("Bearer ") else None
    if not tok or tok in REVOKED or not tok.startswith((ADMIN_JWT, USER_JWT)):
        return "bad"
    return "admin" if tok.startswith(ADMIN_JWT) else "user"


@app.before_request
def require_valid_token():
    if request.path != "/api/alpha/_calls" and auth() == "bad":
        return err("incorrect_login")


@app.before_request
def log():
    calls.append((request.method, request.path, dict(request.args), request.get_json(silent=True),
                  request.headers.get("Authorization"), request.headers.get("X-Forwarded-For")))


@app.get("/api/alpha/_calls")
def get_calls():
    return jsonify(calls[-20:])


@app.get("/api/alpha/site")
def site():
    out = {"site": {"name": "RetroFed", "description": "Retro computing & gaming", "actor_id": "https://retrofed.com",
                    "user_count": 1234, "enable_downvotes": True, "sidebar": "<h2>Welcome</h2><p>Be nice.</p>", "sidebar_md": "## Welcome\n\nBe nice. **Rules:**\n\n1. No spam\n2. Stay retro"}, "version": "1.8.6", "admins": [{"person": person(5, "DosDude"), "counts": {}, "is_admin": True}]}
    if request.headers.get("Authorization"):
        if auth() == "admin":
            out["my_user"] = {"local_user_view": {"person": person(5, "DosDude"), "local_user": {}, "counts": {}},
                              "moderates": []}
        else:  # a plain user
            out["my_user"] = {"local_user_view": {"person": person(9, "plainuser"), "local_user": {}, "counts": {}},
                              "moderates": []}
    return jsonify(out)


@app.get("/api/alpha/topic/list")
def topics():
    def t(i, slug, title, parent, children=(), comms=()):
        return {"id": i, "name": slug, "title": title, "parent_topic_id": parent, "communities_count": len(comms),
                "show_posts_from_children": False, "children": list(children), "communities": list(comms)}
    return jsonify({"topics": [
        t(10, "gaming", "Gaming & Consoles", None, [
            t(11, "retro", "Retro Gaming", 10, [t(12, "8bit", "8-bit Era", 11), t(13, "16bit", "16-bit Era", 11)],
              comms=[COMMS[0], COMMS[2]])]),
        t(20, "computers", "Computers", None, comms=[COMMS[1]]),
        t(30, "misc", "Miscellaneous", None),
    ]})


@app.get("/api/alpha/community/list")
def comm_list():
    return jsonify({"communities": [cview(c) for c in COMMS]})


@app.get("/api/alpha/community")
def comm():
    name = request.args.get("name", "")
    cid = request.args.get("id", type=int)
    c = next((c for c in COMMS if c["name"] == name.split("@")[0] or c["id"] == cid), None)
    if not c:
        return err("No object found.", "Not found")
    return jsonify({"community_view": cview(c), "moderators": [{"community": c, "moderator": person(5, "DosDude")}]})


@app.get("/api/alpha/post/list")
def post_list():
    c = COMMS[0]
    return jsonify({"posts": [pview(100, c, sticky=True)] + [pview(i, c) for i in range(101, 105)], "next_page": None})


@app.get("/api/alpha/post")
def post():
    pid = int(request.args["id"])
    return jsonify({"post_view": poll_post(pid) if pid == 105 else pview(pid, COMMS[0]), "community_view": cview(COMMS[0]),
                    "moderators": [], "cross_posts": []})


@app.get("/api/alpha/comment/list")
def comment_list():
    p = pview(int(request.args["post_id"]), COMMS[0])
    return jsonify({"comments": [comment(1, p, "0.1"), comment(3, p, "0.1.3"), comment(2, p, "0.2"),
                                 comment(4, p, "0.1.999"), comment(5, p, "0.50.5")]})


@app.get("/api/alpha/comment")
def get_comment():
    p = pview(101, COMMS[0])
    return jsonify({"comment_view": comment(int(request.args["id"]), p, "0.1")})


@app.get("/api/alpha/user")
def user():
    if request.args.get("person_id") == "6":
        return jsonify({"person_view": {"person": person(6, "someone", local=False),
                                        "counts": {"post_count": 1, "comment_count": 2}, "is_admin": False},
                        "posts": [], "comments": [], "moderates": []})
    return jsonify({"person_view": {"person": person(5, "DosDude"), "counts": {"post_count": 4, "comment_count": 9},
                                    "is_admin": True, "activity_alert": False},
                    "posts": [pview(101, COMMS[0])], "comments": [comment(1, pview(101, COMMS[0]), "0.1")],
                    "moderates": [{"community": COMMS[0], "moderator": person(5, "DosDude")}]})


@app.get("/api/alpha/search")
def search():
    return jsonify({"type_": request.args.get("type_"), "posts": [pview(101, COMMS[0])], "comments": [],
                    "communities": [cview(COMMS[0])], "users": []})


@app.post("/api/alpha/user/login")
def login():
    d = request.get_json()
    if d.get("password") != "pw":
        return err("Bad credentials", "Bad credentials")
    import secrets as _s  # a fresh token per login, like PieFed
    return jsonify({"jwt": (ADMIN_JWT if d.get("username") == "DosDude" else USER_JWT) + "." + _s.token_hex(8)})


@app.get("/api/alpha/feed/list")
def feeds():
    return jsonify({"feeds": [{"id": 70, "name": "retro-mix", "title": "Retro Mix", "description": "A feed",
                               "actor_id": "https://retrofed.com/f/retro-mix", "ap_domain": "retrofed.com",
                               "public": True, "local": True, "nsfw": False, "nsfl": False, "owner": False,
                               "parent_feed_id": None, "children": [], "communities": [COMMS[0], COMMS[2]],
                               "communities_count": 2, "subscribed": False, "subscriptions_count": 3,
                               "is_instance_feed": False, "show_posts_from_children": False, "user_id": 5,
                               "published": NOW, "updated": NOW}]})


@app.put("/api/alpha/community/moderate/unban")
def unban():
    d = request.get_json() or {}
    if not {"community_id", "user_id"} <= set(d):
        return err("Validation failed", str({"json": {"user_id": ["Missing data for required field."]}}))
    return jsonify({"banned_user": person(6, "someone", local=False)})


@app.post("/api/alpha/community/moderate/ban")
def ban():
    d = request.get_json() or {}
    if not {"community_id", "user_id", "reason"} <= set(d):
        return err("Validation failed", str({"json": {"reason": ["Missing data for required field."]}}))
    return jsonify({"expires_at": d.get("expires_at")})


@app.post("/api/alpha/user/logout")
def logout():
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        REVOKED.add(h[7:])
    return jsonify({"success": True})


@app.get("/api/alpha/post/report/list")
def post_reports():
    if auth() != "admin":
        return err("incorrect_login")
    rv = pview(101, COMMS[0]) | {"post_report": {"id": 900, "creator_id": 9, "post_id": 101, "reason": "Spam",
                                                 "description": "<b>looks</b> like spam", "resolved": False,
                                                 "original_post_name": "x", "original_post_body": "y",
                                                 "published": NOW},
                                 "post_creator": person(5, "DosDude"), "creator_blocked": False}
    return jsonify({"post_reports": [rv], "next_page": None})


@app.get("/api/alpha/comment/report/list")
def comment_reports():
    if auth() != "admin":
        return err("incorrect_login")
    cv = comment(1, pview(101, COMMS[0]), "0.1") | {
        "comment_report": {"id": 901, "creator_id": 9, "comment_id": 1, "reason": "Rude", "resolved": False,
                           "published": NOW, "original_comment_text": "Comment 1 `code`"},
        "comment_creator": person(6, "someone", local=False)}
    return jsonify({"comment_reports": [cv], "next_page": None})


@app.put("/api/alpha/post/report/resolve")
@app.put("/api/alpha/comment/report/resolve", endpoint="resolve_comment_report")
def resolve():
    if auth() != "admin":
        return err("incorrect_login")
    d = request.get_json() or {}
    if not {"report_id", "resolved"} <= set(d):
        return err("Validation failed", "missing fields")
    return jsonify({"success": True})


@app.get("/api/alpha/modlog")
def modlog():
    out = {k: [] for k in ["removed_posts", "locked_posts", "featured_posts", "removed_comments",
                           "removed_communities", "banned_from_community", "banned", "added_to_community",
                           "transferred_to_community", "added", "admin_purged_persons", "admin_purged_communities",
                           "admin_purged_posts", "admin_purged_comments", "hidden_communities"]}
    if request.args.get("page", "1") != "1":
        return jsonify(out)
    p = pview(101, COMMS[0])
    out["locked_posts"] = [{"mod_lock_post": {"id": 1, "mod_person_id": 5, "post_id": 101, "locked": True,
                                              "when_": "2026-10-25T08:00:00.000000Z"},
                            "moderator": person(5, "DosDude"), "post": p["post"], "community": COMMS[0]}]
    out["banned_from_community"] = [{"mod_ban_from_community": {
        "id": 2, "mod_person_id": 5, "other_person_id": 6, "community_id": 1, "reason": "<script>x</script>",
        "banned": True, "expires": "2026-11-01T00:00:00.000000Z", "when_": "2026-10-25T09:00:00.000000Z"},
        "moderator": person(5, "DosDude"), "community": COMMS[0], "banned_person": person(6, "someone", local=False)}]
    out["removed_comments"] = [{"mod_remove_comment": {"id": 3, "mod_person_id": 5, "comment_id": 1, "reason": "rude",
                                                       "removed": True, "when_": "2026-10-24T09:00:00.000000Z"},
                                "moderator": None, "comment": comment(1, p, "0.1")["comment"],
                                "commenter": person(6, "someone", local=False), "post": p["post"],
                                "community": COMMS[0]}]
    return jsonify(out)


@app.post("/api/alpha/post")
def create_post():
    return jsonify({"post_view": pview(555, COMMS[0])})


@app.post("/api/alpha/comment")
def create_comment():
    return jsonify({"comment_view": comment(777, pview(101, COMMS[0]), "0.777")})


# ---- endpoints for edit/delete/report/poll/upload/notifications/PMs (shapes from PieFed's schema.py)

STATE = {"deleted": set(), "edits": {}, "reports": [], "poll_votes": [], "notes_read": set(), "pms": []}


def need(d, *keys):
    missing = [k for k in keys if k not in d]
    return err("Validation failed", str({"json": {k: ["Missing data for required field."] for k in missing}})) \
        if missing else None


def owner_check(view):
    """PieFed only lets the author edit/delete; DosDude (id 5) authored the mock posts."""
    return None if auth() == "admin" else err("You are not the author of this content.")


@app.put("/api/alpha/post")
def edit_post():
    d = request.get_json() or {}
    if (e := need(d, "post_id")) or (e := owner_check(d)):
        return e
    STATE["edits"][("post", d["post_id"])] = d
    return jsonify({"post_view": pview(d["post_id"], COMMS[0])})


@app.post("/api/alpha/post/delete")
def delete_post():
    d = request.get_json() or {}
    if (e := need(d, "post_id", "deleted")) or (e := owner_check(d)):
        return e
    (STATE["deleted"].add if d["deleted"] else STATE["deleted"].discard)(("post", d["post_id"]))
    return jsonify({"post_view": pview(d["post_id"], COMMS[0])})


@app.put("/api/alpha/comment")
def edit_comment():
    d = request.get_json() or {}
    if (e := need(d, "comment_id", "body")):
        return e
    STATE["edits"][("comment", d["comment_id"])] = d
    return jsonify({"comment_view": comment(d["comment_id"], pview(101, COMMS[0]), "0.1")})


@app.post("/api/alpha/comment/delete")
def delete_comment():
    d = request.get_json() or {}
    if (e := need(d, "comment_id", "deleted")):
        return e
    return jsonify({"comment_view": comment(d["comment_id"], pview(101, COMMS[0]), "0.1")})


@app.post("/api/alpha/post/report")
@app.post("/api/alpha/comment/report", endpoint="report_comment")
def report_content():
    d = request.get_json() or {}
    key = "post_id" if request.path.endswith("post/report") else "comment_id"
    if (e := need(d, key, "reason")):
        return e
    STATE["reports"].append(d)
    return jsonify({"success": True})


@app.post("/api/alpha/post/poll_vote")
def poll_vote():
    d = request.get_json() or {}
    if (e := need(d, "post_id", "choice_id")):
        return e
    if not isinstance(d["choice_id"], list):
        return err("Validation failed", "choice_id must be a list")
    STATE["poll_votes"].append(d)
    return jsonify({"post_view": poll_post(105)})


def poll_post(i):
    pv = pview(i, COMMS[0])
    pv["post"].update({"post_type": "Poll", "title": "Favourite 8-bit computer?",
                       "poll": {"mode": "single", "end_poll": "2099-01-01T00:00:00.000000Z",
                                "choices": [{"id": 1, "choice_text": "C64", "sort_order": 0, "num_votes": 6},
                                            {"id": 2, "choice_text": "ZX Spectrum", "sort_order": 1, "num_votes": 3},
                                            {"id": 3, "choice_text": "<b>MSX</b>", "sort_order": 2, "num_votes": 1}],
                                "my_votes": [c for v in STATE["poll_votes"] for c in v["choice_id"]]},
                       "event": {"start": "2026-11-01T18:00:00.000000Z", "end": "2026-11-01T22:00:00.000000Z",
                                 "online": True, "online_link": "javascript:alert(1)", "participant_count": 4,
                                 "max_attendees": 20, "location": {"city": "Utrecht", "country": "NL"}},
                       "gallery": ["https://img.example/g1.png", "javascript:alert(2)"]})
    return pv


@app.post("/api/alpha/upload/image")
def upload():
    f = request.files.get("file")
    if not f:
        return err("Validation failed", "file missing")
    data = f.read()
    return jsonify({"url": f"https://retrofed.com/static/media/upload_{len(data)}.png"})


@app.get("/api/alpha/user/unread_count")
def unread_count():
    if auth() not in ("admin", "user"):
        return err("incorrect_login")
    return jsonify({"mentions": 1, "private_messages": 2, "replies": 3 - len(STATE["notes_read"]), "other": 0})


def note(i, unread):
    return {"notif_id": i, "notif_body": f"someone replied to your <b>post</b> #{i}", "notif_subtype": "post_reply",
            "notif_type": 1, "status": "Unread" if unread else "Read", "author": person(6, "someone", local=False),
            "post_id": 101, "comment_id": i, "post": pview(101, COMMS[0])}


@app.get("/api/alpha/user/notifications")
def notifications():
    if auth() not in ("admin", "user"):
        return err("incorrect_login")
    status = request.args.get("status")
    if status not in ("All", "Unread", "Read", "New"):
        return err("Validation failed", "status")
    items = [note(i, i not in STATE["notes_read"]) for i in (1, 2, 3)]
    if status == "Unread":
        items = [n for n in items if n["status"] == "Unread"]
    unread = sum(1 for n in items if n["status"] == "Unread")
    return jsonify({"counts": {"unread": unread, "read": 3 - unread, "total": 3}, "items": items,
                    "status": status, "username": "DosDude", "next_page": None})


@app.put("/api/alpha/user/notification_state")
def notification_state():
    d = request.get_json() or {}
    if (e := need(d, "notif_id", "read_state")):
        return e
    STATE["notes_read"].add(d["notif_id"])
    return jsonify(note(d["notif_id"], False))


@app.put("/api/alpha/user/mark_all_notifications_read")
def mark_all_notes():
    STATE["notes_read"].update({1, 2, 3})
    return jsonify({"result": "success"})


def pm(i, frm, to, text, read=False):
    return {"private_message": {"id": i, "creator_id": frm["id"], "recipient_id": to["id"], "content": text,
                                "deleted": False, "read": read, "published": f"2026-10-25T0{i % 10}:00:00.000000Z",
                                "ap_id": f"https://retrofed.com/pm/{i}", "local": True},
            "creator": frm, "recipient": to, "conversation_id": 1}


def all_pms():
    me, other = person(5, "DosDude"), person(6, "someone", local=False)
    base = [pm(1, other, me, "Hi <script>x</script> there"), pm(2, me, other, "Hello back", True)]
    return base + [pm(10 + n, me, other, t, True) for n, t in enumerate(STATE["pms"])]


@app.get("/api/alpha/private_message/list")
def pm_list():
    if auth() not in ("admin", "user"):
        return err("incorrect_login")
    return jsonify({"private_messages": all_pms()})


@app.get("/api/alpha/private_message/conversation")
def pm_conversation():
    if auth() not in ("admin", "user"):
        return err("incorrect_login")
    if "person_id" not in request.args and "conversation_id" not in request.args:
        return err("Validation failed", "person_id or conversation_id")
    return jsonify({"private_messages": all_pms()})


@app.post("/api/alpha/private_message")
def pm_create():
    d = request.get_json() or {}
    if (e := need(d, "content", "recipient_id")):
        return e
    STATE["pms"].append(d["content"])
    return jsonify({"private_message_view": all_pms()[-1]})


@app.post("/api/alpha/private_message/mark_as_read")
def pm_read():
    d = request.get_json() or {}
    if (e := need(d, "private_message_id", "read")):
        return e
    return jsonify({"private_message_view": all_pms()[0]})


@app.get("/media/<name>")
def media(name):
    """Fake remote image host for image-proxy tests."""
    from flask import Response, redirect
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    if name == "ok.png":
        return Response(png, mimetype="image/png")
    if name == "evil.svg":
        return Response(b"<svg onload=alert(1)>", mimetype="image/svg+xml")
    if name == "page.html":
        return Response(b"<script>alert(1)</script>", mimetype="text/html")
    if name == "huge.png":
        return Response(png + b"\x00" * (11 * 1024 * 1024), mimetype="image/png")
    if name == "hop.png":
        return redirect("/media/ok.png")
    if name == "loop.png":
        return redirect("/media/loop.png")
    return Response(status=404)


@app.get("/api/alpha/_state")
def get_state():
    return jsonify({k: (list(v) if isinstance(v, set) else v) for k, v in STATE.items()
                    if k != "edits"} | {"edits": [list(k) + [v] for k, v in STATE["edits"].items()]})


@app.post("/api/alpha/<path:p>")
def generic(p):
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(port=8099)
