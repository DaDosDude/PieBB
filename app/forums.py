"""Builds the category index from PieFed topics + feeds, with the admin's layout overrides applied."""
from flask import url_for

from . import piefed, render, settings


def _row(key, kind, name, url, description=None, icon=None, subforums=(), default_hidden=False, pos=0):
    ov = settings.layout_for(key)
    return {
        "key": key, "kind": kind, "orig_name": name, "name": ov.get("name") or name, "url": url,
        "description": description, "icon": icon, "subforums": list(subforums),
        "order": ov.get("order", pos * 10), "hidden": ov.get("hidden", default_hidden),
    }


def _community_row(c, pos, default_hidden=False):
    row = _row(f"community:{c.get('id')}", "community", c.get("title") or c.get("name"),
               url_for("bb.community", name=render.community_ref(c)), c.get("description"), c.get("icon"),
               default_hidden=default_hidden, pos=pos)
    row["community_id"] = c.get("id")
    return row


def _node_rows(node, kind, default_hidden):
    endpoint = "bb.topic" if kind == "topic" else "bb.feed"
    rows = []
    for i, ch in enumerate(node["children"]):
        subs = [(settings.layout_for(f"{kind}:{g['id']}").get("name") or g["name"],
                 url_for(endpoint, **{f"{kind}_id": g["id"]})) for g in ch["children"]]
        row = _row(f"{kind}:{ch['id']}", kind, ch["name"], url_for(endpoint, **{f"{kind}_id": ch["id"]}),
                   ch["description"], subforums=subs, default_hidden=default_hidden, pos=i)
        row["rows"] = [_community_row(c, j, default_hidden) for j, c in enumerate(ch["communities"])]
        rows.append(row)
    for i, c in enumerate(node["communities"]):
        rows.append(_community_row(c, len(node["children"]) + i, default_hidden))
    return rows


def _categories(tree, kind, default_hidden, loose_key, loose_name, offset):
    endpoint = "bb.topic" if kind == "topic" else "bb.feed"
    cats, loose = [], []
    for i, node in enumerate(tree):
        url = url_for(endpoint, **{f"{kind}_id": node["id"]})
        if node["children"] or node["communities"]:
            cat = _row(f"{kind}:{node['id']}", kind, node["name"], url, node["description"],
                       default_hidden=default_hidden, pos=offset + i)
            cat["rows"] = _node_rows(node, kind, False)
            cats.append(cat)
        else:
            loose.append(_row(f"{kind}:{node['id']}", kind, node["name"], url, node["description"], pos=i))
    if loose:
        group = _row(loose_key, "group", loose_name, None, default_hidden=default_hidden, pos=offset + len(tree))
        group["rows"] = loose
        cats.append(group)
    return cats


def build_index(include_hidden=False):
    topic_cats = _categories(piefed.topic_tree(), "topic", False, "group:topics", "Topics", 0)
    feed_offset = -1000 if settings.get("feeds_position") == "before" else 1000
    feed_cats = _categories(piefed.feed_tree(), "feed", True, "group:feeds", "Feeds", feed_offset)
    cats = topic_cats + feed_cats
    for cat in cats:
        _arrange(cat, include_hidden)
    cats.sort(key=lambda c: c["order"])
    if not include_hidden:
        cats = [c for c in cats if not c["hidden"] and c["rows"]]
        _attach_stats(cats)
    return cats


def _arrange(group, include_hidden):
    """Admin order for a group's rows and their nested rows; hidden ones dropped unless asked for."""
    group["rows"].sort(key=lambda r: r["order"])
    if not include_hidden:
        group["rows"] = [r for r in group["rows"] if not r["hidden"]]
    for r in group["rows"]:
        sub = r.setdefault("rows", [])
        sub.sort(key=lambda x: x["order"])
        if not include_hidden:
            r["rows"] = [x for x in sub if not x["hidden"]]


def subforum_rows(node, kind):
    """The Subforums table of a topic/feed page: the same rows (counts, last post, activity order,
    "more" links under subtopics) as that topic's category on the index, with all its own communities."""
    group = {"rows": _node_rows(node, kind, False), "url": None}
    _arrange(group, False)
    _attach_stats([group])
    return group["rows"]


def _last_time(r):
    return ((r.get("last") or {}).get("time")) or ""


def _by_activity(rows):
    """Topics/feeds keep the admin's order; communities follow, most recently active first
    (communities whose activity isn't known yet keep their place at the end)."""
    fixed = [r for r in rows if r["kind"] != "community"]
    comms = [r for r in rows if r["kind"] == "community"]
    comms.sort(key=_last_time, reverse=True)  # stable: unknown ("") stay in their original order
    return fixed + comms


def _attach_stats(cats):
    """Topics / posts / members and the last active topic per community row (PieFed's topic list
    doesn't include them), then order each category's communities by that activity."""
    rows = [x for c in cats for r in c["rows"] for x in [r] + r.get("rows", []) if x.get("community_id")]
    counts, last = piefed.community_stats([r["community_id"] for r in rows])
    for r in rows:
        r["counts"] = counts.get(r["community_id"])
        r["last"] = last.get(r["community_id"]) or None
    for c in cats:
        for r in c["rows"]:
            if r.get("rows"):
                r["rows"] = _by_activity(r["rows"])
                if r["kind"] != "community":  # a subtopic shows its newest community activity
                    r["last"] = max((x["last"] for x in r["rows"] if x.get("last")), key=lambda x: x["time"] or "",
                                    default=None)
        c["rows"] = _by_activity(c["rows"])
    limit = settings.get("index_forum_limit")
    if isinstance(limit, int) and limit > 0:
        for c in cats:
            if c.get("url"):  # groups without a page of their own are always listed in full
                _limit(c, limit)
            for r in c["rows"]:
                if r.get("rows") and r.get("url"):
                    _limit(r, limit)


def _limit(row, limit):
    """Keep `limit` communities (the most active, they're sorted already) and count the rest for a
    "more" link to the category's own page. Subcategories always stay: they are the admin's layout."""
    comms = [x for x in row["rows"] if x["kind"] == "community"]
    if len(comms) <= limit + 1:  # never hide just one: "1 more" takes the same room as the row itself
        return
    keep = {id(x) for x in comms[:limit]}
    row["rows"] = [x for x in row["rows"] if x["kind"] != "community" or id(x) in keep]
    row["more"] = len(comms) - limit


def hidden_community_ids():
    return {int(k.split(":", 1)[1]) for k, v in settings.get("layout").items()
            if k.startswith("community:") and k.split(":", 1)[1].isdigit()
            and isinstance(v, dict) and v.get("hidden")}
