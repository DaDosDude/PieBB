"""End-to-end against a REAL PieFed seeded by pf/seed_content.py (ids from that seed).
Every action goes through PieBB like a browser would; results are checked against PieFed's own API.
Usage: python tests/real_flow.py http://127.0.0.1:8061 http://127.0.0.1:5000"""
import re
import sys
import time

import requests

BB = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8061"
PF = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:5000") + "/api/alpha"
fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def csrf(s, path="/"):
    m = re.search(r'name="csrf" value="([^"]+)"', s.get(BB + path).text)
    return m.group(1) if m else ""


def bb_login(user):
    s = requests.Session()
    r = s.post(BB + "/login", data={"csrf": csrf(s, "/login"), "username": user, "password": "password123"},
               allow_redirects=False)
    check(f"PieBB login as {user}", r.status_code == 302, r.status_code)
    return s


def flashes(r):
    return re.findall(r'class="flash">([^<]*)', r.text)


_pf_tokens = {}


def pf(path, user="dosadmin", **params):
    """Ground truth: ask PieFed directly."""
    if user not in _pf_tokens:
        _pf_tokens[user] = requests.post(PF + "/user/login", json={"username": user, "password": "password123"}).json()["jwt"]
    return requests.get(PF + path, params=params, headers={"Authorization": "Bearer " + _pf_tokens[user]}).json()


admin, mod, user, victim = (bb_login(u) for u in ("dosadmin", "modguy", "plainuser", "victim"))
LONG, POLL, SPAM, SPAMC = 1, 2, 3, 121
RETRO, MODTEST = 1, 5
VICTIM_ID, PLAIN_ID = 4, 3

# ---------------- header / roles
h = admin.get(BB + "/").text
check("admin sees Admin Panel + Moderator CP", "Admin Panel" in h and "Moderator CP" in h)
h = mod.get(BB + "/").text
check("community mod sees Moderator CP but not Admin Panel", "Moderator CP" in h and "Admin Panel" not in h)
h = user.get(BB + "/").text
check("normal user sees neither", "Moderator CP" not in h and "Admin Panel" not in h)

# ---------------- index with real topics
idx = requests.get(BB + "/?view=topics").text
check("real topic titles on index", "Retro Games/Hardware/Emulation" in idx and "Computers" in idx)
check("subtopic communities nested under Emulation", 'row1 nested' in idx and "Handhelds" in idx)
check("forum stats filled in from real PieFed", re.search(r'<td class="row2 center gensmall">\s*\d+\s*</td>', idx) is not None)

# ---------------- long thread paging through PieBB
seen = []
for page in (1, 2, 3):
    seen += re.findall(r'class="postbody">\s*<p>Reply number (\d+)', requests.get(BB + f"/p/{LONG}?page={page}").text)
check("all 120 replies reachable over 3 pages, once each", sorted(map(int, seen)) == list(range(1, 121)), len(seen))
last = requests.get(BB + f"/p/{LONG}?page=last", allow_redirects=False)
check("'last page' link goes to page 3", "page=3" in last.headers.get("Location", ""), last.headers.get("Location"))
p2 = requests.get(BB + f"/p/{LONG}?page=2").text
check("replies to earlier-page posts show a quote", "wrote:" in p2)

# ---------------- normal user: post, reply, quote, edit, delete, vote, subscribe, poll, report, upload
t = csrf(user, "/c/retrogaming")
r = user.post(BB + "/c/retrogaming/new", data={"csrf": t, "title": "PieBB real test topic", "body": "body *one*",
                                              "url": "https://example.com/page"})
m = re.search(r"/p/(\d+)", r.url)
check("new topic created", m is not None and "PieBB real test topic" in r.text, r.url)
NEW = int(m.group(1)) if m else 0
r = user.post(BB + f"/p/{NEW}/reply", data={"csrf": t, "body": "first reply"})
check("reply posted", "first reply" in r.text)
cid = int(re.search(r'name="c(\d+)"', r.text).group(1)) if re.search(r'name="c(\d+)"', r.text) else 0
q = user.get(BB + f"/p/{NEW}/reply?parent={cid}&quote=1").text
check("quote prefills the reply", "plainuser** wrote:" in q and "&gt; first reply" in q)
r = user.post(BB + f"/p/{NEW}/edit", data={"csrf": t, "title": "PieBB real test topic (edited)", "body": "", "url": ""})
pv = pf("/post", id=NEW)["post_view"]["post"]
check("edit topic: title changed in PieFed", pv["title"] == "PieBB real test topic (edited)", pv["title"])
check("edit topic: link removed in PieFed", not pv.get("url"), pv.get("url"))
check("edit topic: body cleared in PieFed", not pv.get("body"), pv.get("body"))
r = user.post(BB + f"/comment/{cid}/edit", data={"csrf": t, "body": "first reply, edited"})
check("edit reply reached PieFed", pf("/comment", id=cid)["comment_view"]["comment"]["body"] == "first reply, edited")
r = user.post(BB + f"/delete/comment/{cid}", data={"csrf": t, "deleted": "1"}, headers={"Referer": BB + f"/p/{NEW}"})
check("delete own reply reached PieFed", pf("/comment", id=cid)["comment_view"]["comment"]["deleted"] is True)
r = user.post(BB + f"/delete/comment/{cid}", data={"csrf": t, "deleted": "0"}, headers={"Referer": BB + f"/p/{NEW}"})
check("restore own reply reached PieFed", pf("/comment", id=cid)["comment_view"]["comment"]["deleted"] is False)
r = victim.get(BB + f"/p/{NEW}/edit")
check("someone else can't edit it", r.status_code == 403)
tv = csrf(victim, f"/p/{NEW}")
r = victim.post(BB + f"/vote/post/{NEW}", data={"csrf": tv, "score": "1"}, headers={"Referer": BB + f"/p/{NEW}"})
check("upvote reached PieFed", pf("/post", id=NEW)["post_view"]["counts"]["score"] >= 1)
r = victim.post(BB + f"/follow/{MODTEST}", data={"csrf": tv, "follow": "0"}, headers={"Referer": BB + "/c/modtest"})
check("unsubscribe reached PieFed", pf("/community", user="victim", id=MODTEST)["community_view"]["subscribed"] == "NotSubscribed")
victim.post(BB + f"/follow/{MODTEST}", data={"csrf": tv, "follow": "1"}, headers={"Referer": BB + "/c/modtest"})
pp = victim.get(BB + f"/p/{POLL}").text
check("real poll rendered with options", "C64" in pp and "Spectrum" in pp and 'name="choice"' in pp)
choice = re.search(r'name="choice" value="(\d+)"', pp).group(1)
r = victim.post(BB + f"/p/{POLL}/vote_poll", data={"csrf": tv, "choice": choice})
check("poll vote accepted by PieFed", "Vote recorded" in r.text, flashes(r))
pv = pf("/post", user="victim", id=POLL)["post_view"]["post"]["poll"]
check("poll vote counted in PieFed", sum(c.get("num_votes", 0) for c in pv["choices"]) == 1, pv)
r = victim.post(BB + f"/report/post/{NEW}", data={"csrf": tv, "reason": "Spam", "description": "test report"})
check("report accepted by PieFed", "moderators have been notified" in r.text, flashes(r))
import struct, zlib  # noqa: E401
def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
png = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
       + _chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + b"\xc0\x40\x20" * 8 for _ in range(8)))) + _chunk(b"IEND", b""))
r = user.post(BB + f"/p/{NEW}/reply", data={"csrf": t, "body": "pic", "upload": "Upload"},
              files={"image": ("tiny.png", png, "image/png")})
up = re.search(r"!\[image\]\((http[^)]+)\)", r.text)
check("image upload to real PieFed returned a URL", up is not None, flashes(r))
if up:
    img = requests.get(up.group(1).replace("https://", "http://"))
    check("uploaded image is served by PieFed", img.status_code == 200, img.status_code)

# ---------------- notifications + private messages between real users
tu = csrf(user, "/messages")
r = user.post(BB + f"/messages/{VICTIM_ID}", data={"csrf": tu, "content": "hello from PieBB"})
check("PM sent (PieFed has it)", any(m["private_message"]["content"] == "hello from PieBB"
                                    for m in pf("/private_message/list", user="victim")["private_messages"]), flashes(r))
time.sleep(16)  # the header badge is cached for 15 s
hv = victim.get(BB + "/").text
check("recipient sees unread PM count in header", re.search(r"Messages \(\d+\)", hv) is not None)
inbox = victim.get(BB + "/messages").text
check("recipient inbox lists the conversation", "plainuser" in inbox)
conv = victim.get(BB + f"/messages/{PLAIN_ID}").text
check("recipient reads the message", "hello from PieBB" in conv)
check("conversation: reply box above the messages", conv.find("<textarea") < conv.find("hello from PieBB"))
check("PM marked read after opening", "Messages (" not in victim.get(BB + "/").text)
notes = user.get(BB + "/notifications?show=all")
check("notifications page loads from real PieFed", notes.status_code == 200, notes.status_code)

# ---------------- unread / mark read
ur = victim.get(BB + "/unread").text
ids = re.findall(r'name="post_id" value="(\d+)"', ur)
check("unread page lists topics from subscriptions", len(ids) > 0, len(ids))
r = victim.post(BB + "/mark_read", data={"csrf": tv, "post_id": ids, "next": "/unread"})
check("mark all read", "Marked" in r.text, flashes(r))
check("unread list empty afterwards", "all caught up" in victim.get(BB + "/unread").text)

# ---------------- moderator (modguy moderates 'modtest' only)
tm = csrf(mod, "/modcp")
mc = mod.get(BB + "/modcp").text
check("modcp shows report from own community", "Spammy post" in mc, re.findall(r"Reported \w+", mc))
check("modcp hides report from other community", "PieBB real test topic" not in mc)
r = mod.post(BB + f"/mod/post/{SPAM}", data={"csrf": tm, "action": "lock"}, headers={"Referer": BB + f"/p/{SPAM}"})
check("mod lock reached PieFed", pf("/post", id=SPAM)["post_view"]["post"]["locked"] is True, flashes(r))
mod.post(BB + f"/mod/post/{SPAM}", data={"csrf": tm, "action": "unlock"}, headers={"Referer": BB + f"/p/{SPAM}"})
check("mod unlock reached PieFed", pf("/post", id=SPAM)["post_view"]["post"]["locked"] is False)
r = mod.post(BB + f"/mod/post/{SPAM}", data={"csrf": tm, "action": "sticky"}, headers={"Referer": BB + f"/p/{SPAM}"})
check("mod sticky reached PieFed", pf("/post", id=SPAM)["post_view"]["post"]["sticky"] is True, flashes(r))
mod.post(BB + f"/mod/post/{SPAM}", data={"csrf": tm, "action": "unsticky"}, headers={"Referer": BB + f"/p/{SPAM}"})
r = mod.post(BB + f"/mod/comment/{SPAMC}", data={"csrf": tm, "action": "remove", "reason": "spam"},
             headers={"Referer": BB + f"/p/{SPAM}"})
check("mod remove comment reached PieFed", pf("/comment", id=SPAMC)["comment_view"]["comment"]["removed"] is True, flashes(r))
mod.post(BB + f"/mod/comment/{SPAMC}", data={"csrf": tm, "action": "restore"}, headers={"Referer": BB + f"/p/{SPAM}"})
check("mod restore comment reached PieFed", pf("/comment", id=SPAMC)["comment_view"]["comment"]["removed"] is False)
r = mod.post(BB + f"/mod/post/{NEW}", data={"csrf": tm, "action": "lock"})
check("mod refused outside own community", r.status_code == 403, r.status_code)
r = mod.post(BB + "/mod/ban", data={"csrf": tm, "community_id": MODTEST, "user_id": VICTIM_ID, "days": 1,
                                     "reason": "PieBB test ban", "next": f"/p/{SPAM}"})
bans = pf("/community/moderate/bans", community_id=MODTEST)
check("mod ban reached PieFed", "banned" in r.text and "PieBB test ban" in str(bans), flashes(r))
r = mod.post(BB + "/mod/ban", data={"csrf": tm, "community_id": MODTEST, "user_id": VICTIM_ID, "unban": "1"})
check("mod unban accepted", "unbanned" in r.text, flashes(r))
mc = mod.get(BB + "/modcp").text
rid = re.search(r'name="report_id" value="(\d+)"', mc)
if rid:
    r = mod.post(BB + "/modcp/resolve", data={"csrf": tm, "kind": "post", "report_id": rid.group(1)})
    check("mod resolves report", "Report updated" in r.text, flashes(r))
check("modcp mod log page loads", mod.get(BB + "/modcp?show=modlog").status_code == 200)
ml = admin.get(BB + "/admin/moderation?show=modlog").text  # PieFed hides non-public entries from non-admins
# (PieFed's API ban/unban does not write a mod log entry, so only check the post/comment actions)
check("mod log shows the actions", all(a in ml for a in ("Locked post", "Stickied post", "Removed comment")),
      re.findall(r"<b>([A-Z][a-z]+ [a-z ]+)</b>", ml)[:8])

# ---------------- admin
ta = csrf(admin, "/admin/")
for path in ["/admin/", "/admin/settings", "/admin/themes", "/admin/layout", "/admin/moderation",
             "/admin/moderation?show=modlog"]:
    r = admin.get(BB + path)
    check(f"admin page {path}", r.status_code == 200, r.status_code)
am = admin.get(BB + "/admin/moderation?all=1").text
check("admin sees reports from all communities", "PieBB real test topic" in am)
r = admin.post(BB + f"/mod/post/{NEW}", data={"csrf": ta, "action": "remove", "reason": "admin test"},
               headers={"Referer": BB + f"/p/{NEW}"})
check("admin remove post reached PieFed", pf("/post", id=NEW)["post_view"]["post"]["removed"] is True, flashes(r))
admin.post(BB + f"/mod/post/{NEW}", data={"csrf": ta, "action": "restore"}, headers={"Referer": BB + f"/p/{NEW}"})
check("admin restore post reached PieFed", pf("/post", id=NEW)["post_view"]["post"]["removed"] is False)
r = admin.post(BB + "/admin/settings", data={"csrf": ta, "forum_title": "Real Test Forum",
                                            "feeds_position": "before"})
check("admin settings saved", "Real Test Forum" in requests.get(BB + "/").text)
lay = admin.get(BB + "/admin/layout").text
form = {"csrf": ta}
for key in re.findall(r'name="show:([^"]+)"', lay):
    form[f"show:{key}"] = "1"
form["name:topic:2"] = "Home Computers"
admin.post(BB + "/admin/layout", data=form)
idx = requests.get(BB + "/?view=topics").text
check("layout: rename + feed shown", "Home Computers" in idx and "Retro Mix" in idx)
check("layout: feeds placed before topics", idx.find("Retro Mix") < idx.find("Retro Games"))
admin.post(BB + "/admin/layout/reset", data={"csrf": ta})
r = admin.post(BB + "/admin/themes", data={"csrf": ta, "action": "select", "theme": "charcoal"})
check("theme switch", "charcoal.css" in requests.get(BB + "/").text)
admin.post(BB + "/admin/themes", data={"csrf": ta, "action": "select", "theme": "subsilver"})

# ---------------- phpBB-style paging
t1 = requests.get(BB + "/p/1").text
check("thread pager: Page 1 of 3 with Last", "Page <b>1</b> of <b>3</b>" in t1 and "Last&nbsp;&raquo;" in t1)
r = requests.get(BB + "/p/1?page=99", allow_redirects=False)
check("thread past the end goes to last page", r.headers.get("Location", "").endswith("page=3"), r.headers.get("Location"))
cl = requests.get(BB + "/c/retrogaming").text
check("topic list mini pager", "Goto page:" in cl and "page=3" in cl)
r = requests.get(BB + "/c/retrogaming?page=9", allow_redirects=False)
check("community past the end redirects", r.status_code == 302, r.status_code)

# ---------------- index: communities ordered by latest activity
ix = requests.get(BB + "/?view=topics").text
times = [m for m in re.findall(r'lastpost">\s*<time class="bbtime" datetime="([^"]+)"', ix)]
check("index has Last Post column", "Last Post" in ix and len(times) >= 2, len(times))
comp = ix[ix.find("Computers"):ix.find("Retro Games")]
ct = re.findall(r'lastpost">\s*<time class="bbtime" datetime="([^"]+)"', comp)
check("communities in a category: newest activity first", ct and ct == sorted(ct, reverse=True)
      and (comp.find("No posts") == -1 or comp.find("No posts") > comp.find(ct[-1])), ct)

# ---------------- links to a reply land on the page that holds it
cl = pf("/comment/list", post_id=1, sort="Old", limit=50, page=3)["comments"]
last_c = max(cl, key=lambda c: c["comment"]["published"])["comment"]["id"]
first_c = min(pf("/comment/list", post_id=1, sort="Old", limit=50, page=1)["comments"],
              key=lambda c: c["comment"]["published"])["comment"]["id"]
r = requests.get(BB + f"/comment/{last_c}", allow_redirects=False)
check("comment link: late reply goes to its page", "page=3" in r.headers.get("Location", "")
      and r.headers["Location"].endswith(f"#c{last_c}"), r.headers.get("Location"))
r = requests.get(BB + f"/comment/{first_c}", allow_redirects=False)
check("comment link: first reply stays on page 1", "page=" not in r.headers.get("Location", "x=page="),
      r.headers.get("Location"))
mid_c = sorted(pf("/comment/list", post_id=1, sort="Old", limit=50, page=2)["comments"],
               key=lambda c: c["comment"]["published"])[10]["comment"]["id"]
loc = requests.get(BB + f"/comment/{mid_c}", allow_redirects=False).headers.get("Location", "")
check("comment link: middle reply goes to page 2 and is on it", "page=2" in loc
      and f'name="c{mid_c}"' in requests.get(BB + loc.split("#")[0]).text, loc)

# ---------------- notifications link to what they are about (also on phones)
nt = user.get(BB + "/notifications?show=all").text
check("notification text links to the reply", re.search(r'name="go" value="/comment/\d+">\s*<button class="btnlink notiflink">', nt)
      is not None or re.search(r'<a href="/comment/\d+">', nt) is not None)
check("notification link column not hidden on mobile", '<th class="hide-sm"></th>' not in nt)

# ---------------- formatting toolbar + spoilers
tq = csrf(user, "/p/1/reply")
r = user.post(BB + "/p/1/reply", data={"csrf": tq, "body": "a\r\n::: spoiler Ending\r\nthe **butler**\r\n:::\r\n",
                                        "preview": "1"})
check("spoiler block previews as <details>", "<details><summary>Ending</summary>" in r.text
      and "<strong>butler</strong>" in r.text)
check("formatting toolbar on the reply form", 'class="mdbar"' in r.text and 'data-md="bold"' in r.text)

# ---------------- bare links: clickable, bare image links shown as the image
tq = csrf(user, "/p/1/reply")
r = user.post(BB + "/p/1/reply", data={"csrf": tq, "preview": "1",
              "body": "see https://lemmy.ca/pictrs/image/abc.png\r\n\r\nhttps://editor.p5js.org/x/full/y"})
check("bare link becomes clickable", '<a href="https://editor.p5js.org/x/full/y"' in r.text)
check("bare image link shows the (proxied) image", re.search(r'<a href="https://lemmy.ca/pictrs/image/abc.png"[^>]*><img src="/img\?u=', r.text)
      is not None)
r = user.post(BB + "/p/1/reply", data={"csrf": tq, "preview": "1", "body": "javascript:alert(1) data:text/html,x"})
check("non-http text is not linkified", "href=\"javascript" not in r.text and "href=\"data" not in r.text)

# ---------------- registration applications (admin)
rg = admin.get(BB + "/admin/registrations").text
check("registrations: pending applications listed", "newbie1" in rg and "newbie2" in rg and "&lt;b&gt;spam" in rg)
ids = {n: i for i, n in re.findall(r'name="user_id" value="(\d+)"><input type="hidden" name="name" value="(\w+)"', rg)}
tr = csrf(admin, "/admin/registrations")
if "newbie1" in ids and "newbie2" in ids:
    admin.post(BB + "/admin/registrations/decide", data={"csrf": tr, "user_id": ids["newbie1"], "decision": "approve"})
    admin.post(BB + "/admin/registrations/decide", data={"csrf": tr, "user_id": ids["newbie2"], "decision": "deny"})
    check("registrations: approved user can log in",
          "jwt" in requests.post(PF + "/user/login", json={"username": "newbie1", "password": "password123"}).text)
    check("registrations: denied application removed", "newbie2" not in admin.get(BB + "/admin/registrations?all=1").text)
tu = csrf(user, "/")
check("registrations: non-admins refused", user.get(BB + "/admin/registrations").status_code == 403
      and user.post(BB + "/admin/registrations/decide", data={"csrf": tu, "user_id": 1, "decision": "approve"}).status_code == 403)

# ---------------- front page is always the categories
g = requests.Session()
g.get(BB + "/?view=flat")
check("front page stays categories after visiting the forum list", 'class="catLeft"' in g.get(BB + "/").text)

# ---------------- logout revokes the PieFed token
tok_before = admin.cookies.get("piebb")
admin.post(BB + "/logout", data={"csrf": csrf(admin)})
r = requests.get(BB + "/admin/", cookies={"piebb": tok_before}, allow_redirects=False)
r2 = requests.get(BB + "/admin/", cookies=r.cookies, allow_redirects=False) if r.status_code == 302 else r
check("old session cookie no longer works after logout", r2.status_code in (302, 403)
      and (r2.status_code == 403 or "/login" in r2.headers.get("Location", "")),
      (r.status_code, r2.status_code, r2.headers.get("Location")))

print(f"\n{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
sys.exit(1 if fails else 0)
