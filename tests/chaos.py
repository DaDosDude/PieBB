"""Hit every page many times against `CHAOS=0.15 python tests/mock_piefed.py`; any 500 is a bug."""
import re
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8060"
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 40
PAGES = ["/", "/?view=flat", "/?view=topics", "/t/10", "/t/11", "/t/12", "/f/70", "/c/retrogaming",
         "/c/amiga@lemmy.world", "/p/101", "/p/102", "/p/103", "/p/104", "/p/101?page=2", "/p/101?page=last",
         "/u/DosDude", "/search?q=a", "/search?q=a&type=Comments", "/search?q=a&type=Communities",
         "/search?q=a&type=Users", "/comment/1", "/login"]
LOGGED = ["/admin/", "/admin/settings", "/admin/themes", "/admin/layout", "/admin/moderation", "/admin/moderation?show=modlog", "/admin/moderation?all=1",
          "/admin/moderation?show=modlog&page=2",
          "/c/retrogaming/new", "/p/101/reply", "/p/101/reply?quote=1", "/p/101/reply?parent=1&quote=1",
          "/mod/ban?community_id=1&user_id=6", "/p/105", "/p/101/edit", "/notifications",
          "/notifications?show=all", "/messages", "/messages/6", "/unread", "/modcp", "/modcp?show=modlog",
          "/report/post/101", "/report/comment/1"]
errors = {}


def login():
    for _ in range(20):
        s = requests.Session()
        page = s.get(BASE + "/login").text
        m = re.search(r'name="csrf" value="([^"]+)"', page)
        if not m:
            continue
        r = s.post(BASE + "/login", data={"csrf": m.group(1), "username": "DosDude", "password": "pw"},
                   allow_redirects=False)
        if r.status_code == 302:
            return s
    return None


anon = requests.Session()
adm = login()
for i in range(ROUNDS):
    for path in PAGES:
        code = anon.get(BASE + path, allow_redirects=False).status_code
        if code >= 500 and code not in (501, 502):
            errors.setdefault(path, []).append(code)
    if adm:
        for path in PAGES + LOGGED:
            code = adm.get(BASE + path, allow_redirects=False).status_code
            if code >= 500 and code not in (501, 502):
                errors.setdefault("[admin] " + path, []).append(code)
        m = re.search(r'name="csrf" value="([^"]+)"', adm.get(BASE + "/p/101").text)
        if m:
            t = m.group(1)
            for path, data in [("/mod/post/101", {"action": "lock"}), ("/mod/comment/1", {"action": "remove"}),
                               ("/vote/post/101", {"score": 1}), ("/follow/1", {"follow": "1"}),
                               ("/p/101/reply", {"body": "hi"}), ("/c/retrogaming/new", {"title": "t"}),
                               ("/mod/ban", {"community_id": 1, "user_id": 6, "days": 1}),
                               ("/mod/ban", {"community_id": 1, "user_id": 6, "unban": "1"}),
                               ("/admin/reports/resolve", {"kind": "post", "report_id": 900}),
                               ("/p/101/edit", {"title": "t", "body": "b"}), ("/delete/post/101", {"deleted": "1"}),
                               ("/report/post/101", {"reason": "Spam"}), ("/p/105/vote_poll", {"choice": "1"}),
                               ("/notifications/read", {"id": 1}), ("/notifications/read", {"all": "1"}),
                               ("/messages/6", {"content": "hi"}), ("/mark_read", {"post_id": "101"}),
                               ("/modcp/resolve", {"kind": "post", "report_id": 900}),
                               ("/admin/reports/resolve", {"kind": "comment", "report_id": 901, "resolved": "0"})]:
                code = adm.post(BASE + path, data=dict(data, csrf=t), allow_redirects=False).status_code
                if code >= 500 and code not in (501, 502):
                    errors.setdefault("[POST] " + path, []).append(code)
print("logged in:", bool(adm))
for k, v in errors.items():
    print(f"500s on {k}: {len(v)}")
print("total 500s:", sum(len(v) for v in errors.values()))
sys.exit(1 if errors else 0)
