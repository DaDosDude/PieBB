"""Coerce PieFed API responses into the exact shapes the views and templates use.

Every field the frontend reads is given a guaranteed type here (missing, null or wrong-typed
values become a safe default), so a malformed or partial response can't crash a page.
"""
import math
from datetime import datetime, timezone


def s(v):
    """str or None"""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return None


def i(v, default=None):
    """int (bools and junk become default)"""
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if math.isfinite(v) else default  # JSON may carry NaN/Infinity
    if isinstance(v, str):
        try:
            return int(v.strip(), 10)  # "--5" or "²" pass isdigit() checks but aren't numbers
        except ValueError:
            return default
    return default


def b(v):
    return v is True


def d(v):
    return v if isinstance(v, dict) else {}


def lst(v):
    return v if isinstance(v, list) else []


def person(p):
    p = d(p)
    return {"id": i(p.get("id")), "user_name": s(p.get("user_name")), "title": s(p.get("title")),
            "local": b(p.get("local")), "bot": b(p.get("bot")), "banned": b(p.get("banned")),
            "deleted": b(p.get("deleted")), "published": s(p.get("published")), "actor_id": s(p.get("actor_id")),
            "avatar": s(p.get("avatar")), "about": s(p.get("about"))}


def community(c):
    c = d(c)
    return {"id": i(c.get("id")), "name": s(c.get("name")), "title": s(c.get("title")), "local": b(c.get("local")),
            "nsfw": b(c.get("nsfw")), "deleted": b(c.get("deleted")), "removed": b(c.get("removed")),
            "hidden": b(c.get("hidden")), "restricted_to_mods": b(c.get("restricted_to_mods")),
            "published": s(c.get("published")), "actor_id": s(c.get("actor_id")),
            "ap_domain": s(c.get("ap_domain")), "description": s(c.get("description")), "icon": s(c.get("icon"))}


def post(p):
    p = d(p)
    return {"id": i(p.get("id")), "title": s(p.get("title")) or "", "url": s(p.get("url")), "body": s(p.get("body")),
            "community_id": i(p.get("community_id")), "removed": b(p.get("removed")), "locked": b(p.get("locked")),
            "published": s(p.get("published")), "updated": s(p.get("updated")), "deleted": b(p.get("deleted")),
            "nsfw": b(p.get("nsfw")), "ap_id": s(p.get("ap_id")), "local": b(p.get("local")),
            "sticky": b(p.get("sticky")), "alt_text": s(p.get("alt_text")), "thumbnail_url": s(p.get("thumbnail_url")),
            "post_type": s(p.get("post_type")) or "", "poll": poll(p.get("poll")), "event": event(p.get("event")),
            "gallery": [u for u in (s(x) for x in lst(p.get("gallery"))) if u]}


def poll(v):
    if not isinstance(v, dict):
        return None
    choices = []
    for c in lst(v.get("choices")):
        c = d(c)
        cid = i(c.get("id"))
        if cid is not None:
            choices.append({"id": cid, "text": s(c.get("choice_text")) or "", "votes": i(c.get("num_votes"), 0),
                            "order": i(c.get("sort_order"), 0)})
    choices.sort(key=lambda c: c["order"])
    total = sum(c["votes"] for c in choices)
    for c in choices:
        c["percent"] = round(100 * c["votes"] / total) if total else 0
    end = s(v.get("end_poll"))
    closed = False
    if end:
        try:
            when = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if when.tzinfo is None:  # no timezone given: PieFed's times are UTC
                when = when.replace(tzinfo=timezone.utc)
            closed = when < datetime.now(timezone.utc)
        except ValueError:
            pass
    return {"mode": "multiple" if v.get("mode") == "multiple" else "single", "end": end, "closed": closed,
            "local_only": b(v.get("local_only")), "choices": choices, "total": total,
            "my_votes": [x for x in (i(y) for y in lst(v.get("my_votes"))) if x is not None]}


def event(v):
    if not isinstance(v, dict):
        return None
    loc = d(v.get("location"))
    loc_text = ", ".join(x for x in (s(loc.get(k)) for k in ("address", "city", "country")) if x)
    return {"start": s(v.get("start")), "end": s(v.get("end")), "timezone": s(v.get("timezone")),
            "online": b(v.get("online")), "online_link": s(v.get("online_link")),
            "participants": i(v.get("participant_count"), 0), "max": i(v.get("max_attendees")),
            "full": b(v.get("full")), "location": loc_text or None,
            "tickets": s(v.get("buy_tickets_link")), "external": s(v.get("external_participation_url"))}


def comment(c):
    c = d(c)
    return {"id": i(c.get("id")), "post_id": i(c.get("post_id")), "body": s(c.get("body")) or "",
            "removed": b(c.get("removed")), "published": s(c.get("published")), "updated": s(c.get("updated")),
            "deleted": b(c.get("deleted")), "ap_id": s(c.get("ap_id")), "local": b(c.get("local")),
            "path": s(c.get("path")) or "", "distinguished": b(c.get("distinguished"))}


def _counts(c, *fields):
    c = d(c)
    return {f: i(c.get(f), 0) for f in fields} | {"published": s(c.get("published")),
                                                  "newest_comment_time": s(c.get("newest_comment_time"))}


def _flags(v):
    return {k: b(v.get(k)) for k in ("creator_banned_from_community", "banned_from_community",
                                     "creator_is_moderator", "creator_is_admin", "saved", "read", "hidden")}


def post_view(v):
    v = d(v)
    return {"post": post(v.get("post")), "creator": person(v.get("creator")), "community": community(v.get("community")),
            "counts": _counts(v.get("counts"), "comments", "score", "upvotes", "downvotes"),
            "subscribed": s(v.get("subscribed")) or "NotSubscribed", "my_vote": i(v.get("my_vote"), 0),
            "unread_comments": i(v.get("unread_comments"), 0)} | _flags(v)


def comment_view(v):
    v = d(v)
    return {"comment": comment(v.get("comment")), "creator": person(v.get("creator")), "post": post(v.get("post")),
            "community": community(v.get("community")),
            "counts": _counts(v.get("counts"), "score", "upvotes", "downvotes", "child_count"),
            "my_vote": i(v.get("my_vote"), 0)} | _flags(v)


def community_view(v):
    v = d(v)
    return {"community": community(v.get("community")), "subscribed": s(v.get("subscribed")) or "NotSubscribed",
            "blocked": b(v.get("blocked")),
            "counts": _counts(v.get("counts"), "subscriptions_count", "post_count", "post_reply_count",
                              "active_daily", "active_weekly", "active_monthly")}


def person_view(v):
    v = d(v)
    return {"person": person(v.get("person")), "is_admin": b(v.get("is_admin")),
            "counts": _counts(v.get("counts"), "post_count", "comment_count")}


def _views(items, fn, key):
    """Normalise a list of views, dropping entries whose main object has no id."""
    out = []
    for x in lst(items):
        nv = fn(x)
        if nv[key]["id"] is not None:
            out.append(nv)
    return out


def moderator_views(items):
    return [{"community": community(d(m).get("community")), "moderator": person(d(m).get("moderator"))}
            for m in lst(items)]


def site(r):
    r = d(r)
    st = d(r.get("site"))
    dv = st.get("enable_downvotes")
    my = d(r.get("my_user"))
    out = {"site": {"name": s(st.get("name")), "description": s(st.get("description")),
                    "sidebar": s(st.get("sidebar_md")) or s(st.get("sidebar")), "icon": s(st.get("icon")),
                    "user_count": i(st.get("user_count")), "enable_downvotes": dv if isinstance(dv, bool) else None},
           "admins": _views(r.get("admins"), person_view, "person"), "my_user": None}
    if my:
        out["my_user"] = {"local_user_view": {"person": person(d(my.get("local_user_view")).get("person"))},
                          "moderates": moderator_views(my.get("moderates"))}
    return out


def _resp(**fields):
    """Normaliser for a response object: each listed key through its own function."""
    def norm(r):
        r = d(r)
        return {key: fn(r.get(key)) for key, fn in fields.items()}
    return norm


def _post_list(v):
    return _views(v, post_view, "post")


def _comment_list(v):
    return _views(v, comment_view, "comment")


def _community_list(v):
    return _views(v, community_view, "community")


def _person_list(v):
    return _views(v, person_view, "person")


def _report(r, kind):
    r = d(r)
    return {"id": i(r.get("id")), "reason": s(r.get("reason")), "description": s(r.get("description")),
            "resolved": b(r.get("resolved")), "published": s(r.get("published")),
            "original": s(r.get("original_post_name" if kind == "post" else "original_comment_text")),
            "original_body": s(r.get("original_post_body"))}


def post_report_view(v):
    v = d(v)
    return post_view(v) | {"post_report": _report(v.get("post_report"), "post"),
                           "post_creator": person(v.get("post_creator"))}


def comment_report_view(v):
    v = d(v)
    return comment_view(v) | {"comment_report": _report(v.get("comment_report"), "comment"),
                              "comment_creator": person(v.get("comment_creator"))}


def _reports(items, fn, key):
    return [r for r in (fn(x) for x in lst(items)) if r[key]["id"] is not None]


# GetModLogResponse list key -> (record key, label)
MODLOG_KINDS = {
    "removed_posts": ("mod_remove_post", "Post"),
    "locked_posts": ("mod_lock_post", "Lock"),
    "featured_posts": ("mod_feature_post", "Sticky"),
    "removed_comments": ("mod_remove_comment", "Comment"),
    "removed_communities": ("mod_remove_community", "Community"),
    "banned_from_community": ("mod_ban_from_community", "Community ban"),
    "banned": ("mod_ban", "Site ban"),
    "added_to_community": ("mod_add_community", "Community mod"),
    "transferred_to_community": ("mod_transfer_community", "Transfer"),
    "added": ("mod_add", "Site admin"),
    "admin_purged_persons": ("admin_purge_person", "Purge user"),
    "admin_purged_communities": ("admin_purge_community", "Purge community"),
    "admin_purged_posts": ("admin_purge_post", "Purge post"),
    "admin_purged_comments": ("admin_purge_comment", "Purge comment"),
    "hidden_communities": ("mod_hide_community", "Hide community"),
}


def _modlog_action(label, rec):
    """Human verb for a modlog record, e.g. 'Removed post', 'Unlocked post'."""
    if "removed" in rec:
        on = {"Community mod": ("Removed moderator", "Added moderator"),
              "Site admin": ("Removed admin", "Added admin")}.get(label)
        if on:
            return on[0] if rec["removed"] else on[1]
        return ("Removed " if rec["removed"] else "Restored ") + label.lower()
    if "locked" in rec:
        return "Locked post" if rec["locked"] else "Unlocked post"
    if "featured" in rec:
        return "Stickied post" if rec["featured"] else "Unstickied post"
    if "banned" in rec:
        return (("Banned" if rec["banned"] else "Unbanned") +
                (" from community" if label == "Community ban" else " from site"))
    if "hidden" in rec:
        return "Hid community" if rec["hidden"] else "Unhid community"
    return label


def modlog(r):
    """Flatten GetModLogResponse into one list of entries, newest first."""
    r = d(r)
    entries = []
    for list_key, (rec_key, label) in MODLOG_KINDS.items():
        for v in lst(r.get(list_key)):
            v = d(v)
            rec = d(v.get(rec_key))
            flags = {k: b(rec.get(k)) for k in ("removed", "locked", "featured", "banned", "hidden") if k in rec}
            target_person = v.get("banned_person") or v.get("modded_person") or v.get("commenter")
            comment_v = d(v.get("comment"))
            entries.append({
                "when": s(rec.get("when_")) or "",
                "action": _modlog_action(label, flags),
                "reason": s(rec.get("reason")),
                "expires": s(rec.get("expires")),
                "moderator": person(v.get("moderator") or v.get("admin")) if (v.get("moderator") or v.get("admin")) else None,
                "post": post(v.get("post")) if v.get("post") else None,
                "comment": comment(comment_v) if comment_v else None,
                "community": community(v.get("community")) if v.get("community") else None,
                "person": person(target_person) if target_person else None,
            })
    entries.sort(key=lambda e: e["when"], reverse=True)
    return {"entries": entries}


def notification(v):
    v = d(v)
    pv = v.get("post")
    cv = v.get("comment_view")
    return {"id": i(v.get("notif_id")), "body": s(v.get("notif_body")) or "", "subtype": s(v.get("notif_subtype")) or "",
            "unread": v.get("status") == "Unread", "author": person(v.get("author")),
            "post_id": i(v.get("post_id")) or (post_view(pv)["post"]["id"] if isinstance(pv, dict) else None),
            "post_title": post_view(pv)["post"]["title"] if isinstance(pv, dict) else None,
            "comment_id": i(v.get("comment_id")) or (comment_view(cv)["comment"]["id"] if isinstance(cv, dict) else None),
            "community": community(v.get("community")) if v.get("community") else None}


def notifications(r):
    r = d(r)
    counts = d(r.get("counts"))
    items = [n for n in (notification(x) for x in lst(r.get("items"))) if n["id"] is not None]
    return {"items": items, "unread": i(counts.get("unread"), 0), "total": i(counts.get("total"), 0),
            "next_page": s(r.get("next_page"))}


def private_message_view(v):
    v = d(v)
    m = d(v.get("private_message"))
    return {"id": i(m.get("id")), "content": s(m.get("content")) or "", "read": b(m.get("read")),
            "deleted": b(m.get("deleted")), "published": s(m.get("published")),
            "creator": person(v.get("creator")), "recipient": person(v.get("recipient")),
            "conversation_id": i(v.get("conversation_id"))}


def _pms(items):
    return [m for m in (private_message_view(x) for x in lst(items)) if m["id"] is not None]


def registration(r):
    r = d(r)
    return {"user_id": i(r.get("user_id")), "user_name": s(r.get("user_name")) or "", "answer": s(r.get("answer")),
            "email": s(r.get("email")), "ip_address": s(r.get("ip_address")), "country": s(r.get("country_code")),
            "throwaway_email": b(r.get("throwaway_email")), "applied_at": s(r.get("applied_at")),
            "status": s(r.get("status")) or "", "approved_by": person(r.get("approved_by")) if r.get("approved_by") else None,
            "approved_at": s(r.get("approved_at"))}


def _registrations(items):
    return [x for x in (registration(r) for r in lst(items)) if x["user_id"] is not None]


NORMALIZERS = {
    "/site": site,
    "/post": _resp(post_view=post_view, community_view=community_view, moderators=moderator_views),
    "/post/list": _resp(posts=_post_list),
    "/comment": _resp(comment_view=comment_view),
    "/comment/list": _resp(comments=_comment_list),
    "/community": _resp(community_view=community_view, moderators=moderator_views),
    "/community/list": _resp(communities=_community_list),
    "/user": _resp(person_view=person_view, posts=_post_list, comments=_comment_list, moderates=moderator_views),
    "/search": _resp(posts=_post_list, comments=_comment_list, communities=_community_list, users=_person_list),
    "/post/report/list": _resp(post_reports=lambda v: _reports(v, post_report_view, "post_report")),
    "/comment/report/list": _resp(comment_reports=lambda v: _reports(v, comment_report_view, "comment_report")),
    "/modlog": modlog,
    "/user/notifications": notifications,
    "/user/unread_count": _resp(replies=lambda v: i(v, 0), mentions=lambda v: i(v, 0),
                                private_messages=lambda v: i(v, 0), other=lambda v: i(v, 0)),
    "/private_message/list": _resp(private_messages=_pms),
    "/private_message/conversation": _resp(private_messages=_pms),
    "/private_message": _resp(private_message_view=private_message_view),
    "/post/poll_vote": _resp(post_view=post_view),
    "/post/like": _resp(post_view=post_view),
    "/comment/like": _resp(comment_view=comment_view),
    "/post/delete": _resp(post_view=post_view),
    "/comment/delete": _resp(comment_view=comment_view),
    "/upload/image": _resp(url=s),
    "/admin/registration_application/list": _resp(registrations=_registrations),
}


def normalize(path, data):
    fn = NORMALIZERS.get(path)
    return fn(data) if fn else data
