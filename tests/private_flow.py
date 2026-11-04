"""PieBB against a private PieFed instance (PieFed 1.8.11+ answers nothing without a login).
Run with the instance switched to private; see the comments in real_flow.py for setup."""
import re
import sys

import requests

BB = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8061"
fails = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  [{detail}]"))
    if not ok:
        fails.append(name)


g = requests.Session()
for path in ("/", "/c/retrogaming", "/t/1", "/search?q=x", "/u/plainuser"):
    r = g.get(BB + path, allow_redirects=False)
    check(f"guest {path} goes to the login page", r.status_code == 302 and "/login" in r.headers.get("Location", ""),
          (r.status_code, r.headers.get("Location")))
r = g.get(BB + "/")
check("login page says the forum is private", "This forum is private" in r.text)
check("guest sees no topic or forum names", 'class="forumlink"' not in r.text and 'class="topictitle"' not in r.text)
t = re.search(r'name="csrf" value="([^"]+)"', g.get(BB + "/login").text).group(1)
r = g.post(BB + "/login", data={"csrf": t, "username": "plainuser", "password": "password123", "next": "/"})
check("logged in, the front page shows the forums", 'class="forumlink"' in r.text, r.status_code)
check("logged in, a forum lists its topics", 'class="topictitle"' in g.get(BB + "/c/retrogaming").text)
check("logged in, the Last Post column is filled", "lastpost" in r.text and "/p/" in r.text)
g.post(BB + "/logout", data={"csrf": re.search(r'name="csrf" value="([^"]+)"', g.get(BB + "/").text).group(1)})
r = requests.get(BB + "/", allow_redirects=False)
check("after logout, cached pages stay hidden from guests", r.status_code == 302, r.status_code)
print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
