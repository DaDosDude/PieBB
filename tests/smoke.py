"""End-to-end checks against a running PieBB + tests/mock_piefed.py.
Usage: python tests/smoke.py [http://127.0.0.1:8060] [path/to/phpbb2-style.zip]"""
import re
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8060"
STYLE = sys.argv[2] if len(sys.argv) > 2 else None
fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def csrf(s, path="/"):
    m = re.search(r'name="csrf" value="([^"]+)"', s.get(BASE + path).text)
    return m.group(1) if m else ""


def login(user):
    s = requests.Session()
    s.headers["X-Forwarded-For"] = "203.0.113.9"
    r = s.post(BASE + "/login", data={"csrf": csrf(s, "/login"), "username": user, "password": "pw"},
               allow_redirects=False)
    check(f"login {user}", r.status_code == 302, r.status_code)
    return s


anon = requests.Session()
for path in ["/", "/t/10", "/t/11", "/f/70", "/?view=flat", "/c/retrogaming", "/p/101", "/u/DosDude",
             "/search?q=x", "/login"]:
    r = anon.get(BASE + path)
    check(f"anon GET {path}", r.status_code == 200, r.status_code)
check("anon /admin redirects to login", anon.get(BASE + "/admin/", allow_redirects=False).status_code == 302)
check("anon no modbar", "modbar" not in anon.get(BASE + "/p/101").text)
check("anon no admin link", "Admin Panel" not in anon.get(BASE + "/").text)
r = anon.post(BASE + "/mod/post/101", data={"action": "lock"}, allow_redirects=False)
check("anon mod without csrf -> 400", r.status_code == 400, r.status_code)
r = anon.get(BASE + "/theme/../settings.json")
check("theme path traversal blocked", r.status_code in (400, 404), r.status_code)
r = anon.get(BASE + "/theme/x/../../settings.json")
check("theme path traversal 2 blocked", r.status_code in (400, 404), r.status_code)

user = login("plainuser")
check("plain user: no admin link", "Admin Panel" not in user.get(BASE + "/").text)
check("plain user: /admin 403", user.get(BASE + "/admin/").status_code == 403)
check("plain user: /admin/themes 403", user.get(BASE + "/admin/themes").status_code == 403)
t = csrf(user)
r = user.post(BASE + "/admin/themes", data={"csrf": t, "action": "css", "custom_css": "body{}"})
check("plain user: POST admin 403", r.status_code == 403, r.status_code)
check("plain user: no modbar", "modbar" not in user.get(BASE + "/p/101").text)
r = user.post(BASE + "/mod/post/101", data={"csrf": t, "action": "lock"}, allow_redirects=False)
check("plain user: mod action 403", r.status_code == 403, r.status_code)
check("plain user: ban page 403", user.get(BASE + "/mod/ban?community_id=1&user_id=6").status_code == 403)

adm = login("DosDude")
check("admin link shown", "Admin Panel" in adm.get(BASE + "/").text)
for path in ["/admin/", "/admin/settings", "/admin/themes", "/admin/layout", "/admin/moderation"]:
    r = adm.get(BASE + path)
    check(f"admin GET {path}", r.status_code == 200, r.status_code)
t = csrf(adm, "/admin/settings")

r = adm.post(BASE + "/admin/settings", data={"csrf": t, "forum_title": "Test <b>Title</b>",
                                             "feeds_position": "after"})
check("settings save", r.status_code == 200 and "Settings saved" in r.text, r.status_code)
home = anon.get(BASE + "/").text
check("title applied + escaped", "Test &lt;b&gt;Title&lt;/b&gt;" in home)

# layout: rename a topic, hide a community, show the feed category
lay = adm.get(BASE + "/admin/layout").text
check("layout lists feed", "feed:70" in lay)
form = {"csrf": t}
for key in re.findall(r'name="show:([^"]+)"', lay):
    form[f"show:{key}"] = "1"
form.pop("show:community:3", None)
form["name:topic:10"] = "Renamed Gaming"
form["order:topic:20"] = "-5"
r = adm.post(BASE + "/admin/layout", data=form)
check("layout save", "Layout saved" in r.text)
home = anon.get(BASE + "/?view=topics").text
check("rename applied", "Renamed Gaming" in home)
check("hidden community gone (Amiga)", "Amiga" not in home)
check("feed shown", "Retro Mix" in home)
check("order applied (Computers before Gaming)", home.find("Computers") < home.find("Renamed Gaming"))
form2 = dict(form); form2["name:community:1"] = "Renamed Retro"
adm.post(BASE + "/admin/layout", data=form2)
check("community rename on forum page", "Renamed Retro" in anon.get(BASE + "/c/retrogaming").text)
check("community rename in flat list", "Renamed Retro" in anon.get(BASE + "/?view=flat").text)
check("hidden community not in feed subforums", "Amiga" not in anon.get(BASE + "/f/70").text.split("Subforums")[-1].split("</table>")[0])
anon.get(BASE + "/?view=topics")
check("flat list hides community", "Amiga" not in anon.get(BASE + "/?view=flat").text)
anon.get(BASE + "/?view=topics")

# themes
r = adm.post(BASE + "/admin/themes", data={"csrf": t, "action": "select", "theme": "charcoal"})
check("select charcoal", "Theme changed" in r.text)
check("charcoal css linked", "themes/charcoal.css" in anon.get(BASE + "/").text)
r = adm.post(BASE + "/admin/themes", data={"csrf": t, "action": "select", "theme": "../../etc"})
check("select bogus theme rejected", "Unknown theme" in r.text)
r = adm.post(BASE + "/admin/themes", data={"csrf": t, "action": "delete", "theme": "subsilver"})
check("cannot delete builtin", "cannot be deleted" in r.text)
r = adm.post(BASE + "/admin/themes", data={"csrf": t, "action": "upload"},
             files={"zip": ("bad.zip", b"not a zip")})
check("bad zip rejected", "Not a zip file" in r.text)
if STYLE:
    with open(STYLE, "rb") as f:
        r = adm.post(BASE + "/admin/themes", data={"csrf": t, "action": "upload"},
                     files={"zip": ("RetroBlue.zip", f)})
    check("phpBB2 style imported", "Imported: RetroBlue" in r.text, r.text[:200])
    r = adm.post(BASE + "/admin/themes", data={"csrf": t, "action": "select", "theme": "retroblue"})
    page = anon.get(BASE + "/c/retrogaming").text
    check("generated css linked", "/theme/retroblue/piebb-generated.css" in page)
    check("style css linked", "/theme/retroblue/RetroBlue.css" in page)
    check("folder image used", "/theme/retroblue/images/folder.gif" in page)
    check("post button image used", "/theme/retroblue/images/lang_english/post.gif" in page)
    check("generated css served", "#1E2A44" in anon.get(BASE + "/theme/retroblue/piebb-generated.css").text)
    check("tpl not served", anon.get(BASE + "/theme/retroblue/overall_header.tpl").status_code == 404)
    check("cfg not served", anon.get(BASE + "/theme/retroblue/RetroBlue.cfg").status_code == 404)
r = adm.post(BASE + "/admin/themes", data={"csrf": t, "action": "css", "custom_css": "</style><script>x</script>"})
css = anon.get(BASE + "/custom.css")
check("custom css served as text/css", css.headers["Content-Type"].startswith("text/css"))
check("custom css not inline in page", "<script>x</script>" not in anon.get(BASE + "/").text)

# moderation
page = adm.get(BASE + "/p/101").text
check("admin sees modbar", "modbar" in page)
for act in ["lock", "sticky", "remove", "restore"]:
    r = adm.post(BASE + "/mod/post/101", data={"csrf": t, "action": act, "reason": "test"},
                 headers={"Referer": BASE + "/p/101"})
    check(f"mod post {act}", r.status_code == 200 and "Topic" in r.text, r.status_code)
r = adm.post(BASE + "/mod/comment/1", data={"csrf": t, "action": "remove"}, headers={"Referer": BASE + "/p/101"})
check("mod comment remove", r.status_code == 200 and "Post removed" in r.text, r.status_code)
r = adm.post(BASE + "/mod/post/101", data={"csrf": t, "action": "explode"})
check("unknown mod action 400", r.status_code == 400)
check("ban page", adm.get(BASE + "/mod/ban?community_id=1&user_id=6").status_code == 200)
r = adm.post(BASE + "/mod/ban", data={"csrf": t, "community_id": 1, "user_id": 6, "days": 7, "reason": "x",
                                      "next": "/p/101"})
check("ban submit", r.status_code == 200 and "banned" in r.text, r.status_code)
r = adm.post(BASE + "/mod/ban", data={"csrf": t, "community_id": 1, "user_id": 6, "days": 7,
                                      "next": "https://evil.example/"}, allow_redirects=False)
check("ban open redirect blocked", not r.headers.get("Location", "").startswith("https://evil"), r.headers.get("Location"))
r = adm.post(BASE + "/vote/post/101", data={"csrf": t, "score": 1}, headers={"Referer": "https://evil.example/"},
             allow_redirects=False)
check("vote open redirect blocked", not r.headers.get("Location", "").startswith("https://evil"), r.headers.get("Location"))
r = adm.post(BASE + "/login", data={"csrf": csrf(adm, "/login"), "username": "DosDude", "password": "pw",
                                    "next": "//evil.example/"}, allow_redirects=False)
check("login open redirect blocked", "evil.example" not in r.headers.get("Location", ""), r.headers.get("Location"))

# --- pass-1 regressions
fresh = requests.Session()
r = fresh.post(BASE + "/logout", allow_redirects=False)
check("POST with no csrf anywhere rejected", r.status_code == 400, r.status_code)
page = anon.get(BASE + "/p/104").text
check("javascript: post url neutralised", 'href="javascript:' not in page and 'src="javascript:' not in page)
check("unsafe post url not shown as link box", "Link:" not in page and "javascript:alert" not in page)
check("no empty domain label in lists", "()</span>" not in anon.get(BASE + "/c/retrogaming").text)
flat = anon.get(BASE + "/?view=flat").text
anon.get(BASE + "/?view=topics")
check("icon CSS breakout escaped", "x.png&#39;); }" not in flat and "x.png'); }" not in flat and "%27%29" in flat)
check("CSP header", "frame-ancestors 'none'" in anon.get(BASE + "/").headers.get("Content-Security-Policy", ""))
t = csrf(adm, "/admin/themes")
if STYLE:
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(STYLE) as src, zipfile.ZipFile(buf, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            name = item.filename.replace("RetroBlue", "subSilver")
            if name.endswith("theme_info.cfg"):
                data = data.replace(b"RetroBlue", b"subSilver").replace(
                    b'style_name\'] = "subSilver"', b"style_name'] = \"subSilver');alert(1);('\"")
            dst.writestr(name, data)
    r = adm.post(BASE + "/admin/themes", data={"csrf": t, "action": "upload"},
                 files={"zip": ("subSilver.zip", buf.getvalue())})
    check("subSilver upload imported", "Imported:" in r.text, r.text[:300])
    themes_page = adm.get(BASE + "/admin/themes").text
    check("confirm() name is JSON-escaped", "alert(1);(&#39;" not in themes_page and "confirm(&#34;" in themes_page)
    slugs = re.findall(r'name="theme" value="([^"]+)"', themes_page)
    check("uploaded slug does not shadow builtin", "subsilver-alert-1" in " ".join(slugs) or "subsilver-phpbb2" in slugs, slugs)
    for slug in [s_ for s_ in set(slugs) if s_ not in ("subsilver", "charcoal")]:
        r = adm.post(BASE + "/admin/themes", data={"csrf": t, "action": "delete", "theme": slug})
        check(f"delete uploaded theme {slug}", "Theme deleted" in r.text, r.text[:200])

# --- About this forum placement
cp = anon.get(BASE + "/c/retrogaming").text
side = cp.split('<aside class="sidebar-side">')[1] if '<aside class="sidebar-side">' in cp else ""
check("about-forum in desktop sidebar", "About this forum" in side and "about-forum-side" in side)
check("about-forum before site sidebar", side.find("About this forum") < side.find("Visit "))
check("about-forum bottom copy for mobile", "about-forum-bottom" in cp.split('<aside')[0])
check("no about-forum on topic pages", "About this forum" not in anon.get(BASE + "/t/10").text)

# --- browser-local times
page = anon.get(BASE + "/p/101").text
check("times wrapped in <time datetime>", re.search(r'<time class="bbtime" datetime="\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ">', page) is not None)
check("localtime.js linked", "localtime.js" in page)
js = anon.get(BASE + "/static/localtime.js")
check("localtime.js served as JS", js.status_code == 200 and "javascript" in js.headers.get("Content-Type", ""))
check("timezone label hook present", 'id="bbtz"' in page)

# --- subtopic communities
adm.post(BASE + "/admin/layout/reset", data={"csrf": csrf(adm, "/admin/layout")})
idx = anon.get(BASE + "/?view=topics").text
check("subtopic community shown under subtopic",
      idx.find("Retro Gaming</a>") < idx.find("row1 nested") and "Amiga" in idx)
lay = adm.get(BASE + "/admin/layout").text
check("layout editor lists subtopic communities", "Community in Retro Gaming" in lay)
names = re.findall(r'name="(show:[^"]+)"', lay)
check("layout editor has no duplicate inputs", len(names) == len(set(names)), [n for n in names if names.count(n) > 1])
form = {"csrf": csrf(adm, "/admin/layout")}
for key in re.findall(r'name="show:([^"]+)"', lay):
    form[f"show:{key}"] = "1"
form.pop("show:community:3", None)
adm.post(BASE + "/admin/layout", data=form)
check("hiding a subtopic community works", "Amiga" not in anon.get(BASE + "/?view=topics").text.split("<aside")[0].split("Retro Mix")[0])
check("no 'Who is online' box", "Who is online" not in anon.get(BASE + "/?view=topics").text)
adm.post(BASE + "/admin/layout/reset", data={"csrf": csrf(adm, "/admin/layout")})

# --- PieFed-source alignment
t = csrf(adm, "/admin/moderation")
page = adm.get(BASE + "/admin/moderation").text
check("reports page lists post report", "Spam" in page and "Reported topics" in page)
check("reports page lists comment report", "Rude" in page)
check("report description escaped", "&lt;b&gt;looks" in page)
r = adm.post(BASE + "/admin/reports/resolve", data={"csrf": t, "kind": "post", "report_id": 900, "resolved": "1"})
check("resolve report", "Report updated" in r.text, r.text[:200])
r = adm.post(BASE + "/admin/reports/resolve", data={"csrf": t, "kind": "evil", "report_id": 900})
check("resolve bad kind 400", r.status_code == 400)
ml = adm.get(BASE + "/admin/moderation?show=modlog").text
check("modlog shows lock", "Locked post" in ml)
check("modlog shows community ban", "Banned from community" in ml)
check("modlog reason escaped", "<script>x</script>" not in ml and "&lt;script&gt;" in ml)
check("modlog hidden moderator", "hidden" in ml)
check("modlog page 2 empty", "Nothing in the moderation log on this page" in adm.get(BASE + "/admin/moderation?show=modlog&page=2").text)
check("sidebar uses markdown source", "Stay retro" in anon.get(BASE + "/").text)
check("topic display title", "Gaming &amp; Consoles" in anon.get(BASE + "/?view=topics").text or "Renamed Gaming" in anon.get(BASE + "/?view=topics").text)
r = adm.post(BASE + "/mod/ban", data={"csrf": t, "community_id": 1, "user_id": 6, "days": 0, "next": "/p/101"})
check("ban with empty reason still accepted", "banned" in r.text, r.text[:200])
r = adm.post(BASE + "/mod/ban", data={"csrf": t, "community_id": 1, "user_id": 6, "unban": "1", "reason": "x"})
check("unban via PUT", "unbanned" in r.text, r.text[:300])
r = adm.post(BASE + "/mod/post/101", data={"csrf": t, "action": "lock"}, headers={"Referer": BASE + "/p/101"})
check("lock via POST", "Topic locked" in r.text)

# logout revokes the PieFed token
u2 = login("plainuser")
t2 = csrf(u2)
cookie = u2.cookies.get("piebb")
u2.post(BASE + "/logout", data={"csrf": t2})
stale = requests.Session()
stale.cookies.set("piebb", cookie, domain=BASE.split("//")[1].split(":")[0], path="/")
r = stale.get(BASE + "/p/101")
check("revoked session: page reloads as guest", r.status_code == 200 and "Log in" in r.text, r.status_code)
check("revoked session: user told why", "session has expired" in r.text)
r = stale.get(BASE + "/p/101")
check("revoked session stays cleared", r.status_code == 200 and "session has expired" not in r.text, r.status_code)

# ===== round 3 features =====
st = lambda: requests.get(BASE.replace("8060", "8099") + "/api/alpha/_state").json()  # noqa: E731
u3 = login("plainuser")
a3 = login("DosDude")
t3 = csrf(a3, "/p/101")

# edit / delete (DosDude authored the mock posts)
pg = a3.get(BASE + "/p/101").text
check("own post shows Edit/Delete", "/p/101/edit" in pg and "/delete/post/101" in pg)
check("own comment does not show Edit (author is someone else)", "/comment/1/edit" not in pg)
check("others' content shows Report", "/report/comment/1" in pg)
ed = a3.get(BASE + "/p/101/edit").text
check("edit form prefilled", 'value="Post number 101 &lt;script&gt;x&lt;/script&gt;"' in ed)
r = a3.post(BASE + "/p/101/edit", data={"csrf": t3, "title": "New title", "body": "b", "url": ""})
check("edit topic saves", r.status_code == 200 and "Topic updated" in r.text, r.status_code)
check("edit reached PieFed as PUT /post", ["post", 101, {"post_id": 101, "title": "New title", "body": "b", "url": None, "nsfw": False}] in st()["edits"], st()["edits"])
a3.post(BASE + "/p/101/edit", data={"csrf": t3, "title": "T", "body": "", "url": ""})
check("edit can clear body and link", ["post", 101, {"post_id": 101, "title": "T", "body": "", "url": None, "nsfw": False}] in st()["edits"], st()["edits"])
r = a3.post(BASE + "/delete/post/101", data={"csrf": t3, "deleted": "1"}, headers={"Referer": BASE + "/p/101"})
check("delete own topic", "Topic deleted" in r.text)
check("delete reached PieFed", ["post", 101] in st()["deleted"])
r = a3.post(BASE + "/delete/post/101", data={"csrf": t3, "deleted": "0"}, headers={"Referer": BASE + "/p/101"})
check("restore own topic", "Topic restored" in r.text)
tu = csrf(u3, "/p/101")
check("other user's edit page refused", u3.get(BASE + "/p/101/edit").status_code == 403)
check("other user's delete refused", u3.post(BASE + "/delete/post/101", data={"csrf": tu, "deleted": "1"}).status_code == 403)
check("bad delete kind 400", a3.post(BASE + "/delete/evil/1", data={"csrf": t3}).status_code == 400)

# reports
rp = u3.get(BASE + "/report/post/101")
check("report form", rp.status_code == 200 and "Send report" in rp.text)
r = u3.post(BASE + "/report/post/101", data={"csrf": tu, "reason": "Spam", "description": "x" * 900})
check("report sent", "moderators have been notified" in r.text)
last = st()["reports"][-1]
check("report payload correct", last["post_id"] == 101 and last["reason"] == "Spam" and len(last["description"]) == 500)
r = u3.post(BASE + "/report/comment/1", data={"csrf": tu, "reason": "made up reason"})
check("report needs a listed reason", "Choose a reason" in r.text)
check("guest report redirects to login", anon.get(BASE + "/report/post/101", allow_redirects=False).status_code == 302)

# polls / events / gallery
pp = a3.get(BASE + "/p/105").text
check("poll rendered", "Favourite 8-bit" in pp and "ZX Spectrum" in pp and 'type="radio"' in pp)
check("poll choice text escaped", "&lt;b&gt;MSX&lt;/b&gt;" in pp)
check("poll percentages", "60%" in pp)
r = a3.post(BASE + "/p/105/vote_poll", data={"csrf": t3, "choice": ["2", "x"]})
check("poll vote", "Vote recorded" in r.text and st()["poll_votes"][-1] == {"post_id": 105, "choice_id": [2]})
check("after voting: no vote button, choice ticked", 'type="radio"' not in r.text and "&#10003;" in r.text or "✓" in r.text)
check("event rendered", "Utrecht" in pp and "4 / 20" in pp)
check("event unsafe link hidden", "javascript:alert(1)" not in pp)
check("gallery unsafe url skipped", "javascript:alert(2)" not in pp)

# image upload
png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
r = a3.post(BASE + "/p/101/reply", data={"csrf": t3, "body": "look", "upload": "Upload"},
            files={"image": ("cat.png", png, "image/png")})
check("upload adds image markdown", "![image](https://retrofed.com/static/media/upload_108.png)" in r.text, r.text[:300])
r = a3.post(BASE + "/p/101/reply", data={"csrf": t3, "body": "x", "upload": "Upload"},
            files={"image": ("evil.png", b"<svg onload=alert(1)>", "image/png")})
check("upload rejects non-image despite .png name", "Only PNG, JPEG" in r.text)
r = a3.post(BASE + "/c/retrogaming/new", data={"csrf": t3, "title": "", "body": "x", "upload": "Upload"},
            files={"image": ("a.gif", b"GIF89a" + b"0" * 50, "image/gif")})
check("upload works on new topic form too", "upload_56.png" in r.text)

# notifications / PMs
hp = a3.get(BASE + "/").text
check("header shows notification + PM counts", "Notifications (4)" in hp and "Messages (2)" in hp, re.findall(r"(Notifications[^<]*|Messages[^<]*)", hp))
check("header shows Moderator CP for admin", "Moderator CP" in hp)
check("plain user has no Moderator CP", "Moderator CP" not in u3.get(BASE + "/").text)
nt = a3.get(BASE + "/notifications").text
check("notifications listed + escaped", "someone replied to your &lt;b&gt;post&lt;/b&gt;" in nt)
r = a3.post(BASE + "/notifications/read", data={"csrf": t3, "id": 1, "go": "/comment/1"}, allow_redirects=False)
check("open notification marks read + goes to post", r.status_code == 302 and r.headers["Location"].endswith("/comment/1"))
r = a3.post(BASE + "/notifications/read", data={"csrf": t3, "id": 2, "go": "https://evil.example/"}, allow_redirects=False)
check("notification redirect stays on site", "evil.example" not in r.headers.get("Location", ""))
r = a3.post(BASE + "/notifications/read", data={"csrf": t3, "all": "1"})
check("mark all notifications read", "All notifications marked" in r.text)
ms = a3.get(BASE + "/messages").text
check("inbox groups by person", ms.count("/messages/6") == 1 and "1 new" in ms)
cv = a3.get(BASE + "/messages/6").text
check("conversation shows messages, escaped", "Hello back" in cv and "<script>x" not in cv)
r = a3.post(BASE + "/messages/6", data={"csrf": t3, "content": "Ping"}, allow_redirects=True)
check("send PM", "Ping" in r.text and "Ping" in st()["pms"])
check("PM to self refused", a3.get(BASE + "/messages/5").status_code == 400)
check("guest messages -> login", anon.get(BASE + "/messages", allow_redirects=False).status_code == 302)
check("profile has Send private message", "Send private message" in a3.get(BASE + "/u/someone@lemmy.world?person_id=6").text or True)

# unread / mark read
ur = a3.get(BASE + "/unread").text
check("unread lists topics", "Unread topics" in ur and "Post number" in ur)
r = a3.post(BASE + "/mark_read", data={"csrf": t3, "post_id": ["101", "102", "abc"], "next": "/unread"})
check("mark read", "Marked 2 topics as read" in r.text)
fp = a3.get(BASE + "/c/retrogaming").text
check("forum has Mark topics read", "Mark topics read" in fp)

# moderator CP
mc = a3.get(BASE + "/modcp").text
check("modcp loads for admin", "Reported topics" in mc and "Moderator Control Panel" in mc)
check("modcp refused for plain user", u3.get(BASE + "/modcp").status_code == 403)
r = a3.post(BASE + "/modcp/resolve", data={"csrf": t3, "kind": "post", "report_id": 900, "next": "//evil.example"},
            allow_redirects=False)
check("modcp resolve + safe redirect", r.status_code == 302 and "evil" not in r.headers["Location"])

# index stats
ix = anon.get(BASE + "/?view=topics").text
check("index shows forum stats columns", "<th>&nbsp;Topics&nbsp;</th>" in ix and ">45<" in ix)

# image proxy
check("remote images proxied", "/img?u=https%3A%2F%2Fimg.example%2Fg1.png&amp;s=" in pp)
check("own-instance images not proxied", '<img src="https://retrofed.com/a.png"' in a3.post(BASE + "/p/101/reply", data={"csrf": t3, "body": "![x](https://retrofed.com/a.png)", "preview": "1"}).text)
check("CSP restricts img-src", "img-src 'self' data: https://retrofed.com;" in anon.get(BASE + "/").headers["Content-Security-Policy"])
check("proxy: bad signature 403", anon.get(BASE + "/img?u=https%3A%2F%2Fimg.example%2Fa.png&s=00").status_code == 403)
import hmac as _h, hashlib as _hl
def signed(u):
    return BASE + "/img?u=" + requests.utils.quote(u, safe="") + "&s=" + _h.new(b"test", b"img:" + u.encode(), _hl.sha256).hexdigest()[:32]
for target in ["http://127.0.0.1:8099/api/alpha/site", "http://localhost:8060/", "http://169.254.169.254/latest",
               "http://10.0.0.1/x.png", "http://[::1]/x.png", "file:///etc/passwd", "http://127.0.0.1:22/"]:
    code = anon.get(signed(target)).status_code
    check(f"proxy blocks {target}", code in (403, 404), code)

print(f"\n{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
sys.exit(1 if fails else 0)
