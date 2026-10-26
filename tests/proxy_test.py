"""Image proxy happy path + content checks. Allows 127.0.0.1 only inside this test process."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.update(PIEFED_URL="https://retrofed.com", SECRET_KEY="test", DATA_DIR="/tmp/piebb-proxytest")
from app import create_app, imgproxy  # noqa: E402

app = create_app()
imgproxy._public_addresses = lambda host, port: ["127.0.0.1"]  # test-only: mock runs on localhost
_open = imgproxy._open


def open_any_port(url):
    # the real proxy only allows ports 80/443; the mock listens on 8099
    parts = imgproxy.urlsplit(url)
    import urllib3
    pool = urllib3.HTTPConnectionPool("127.0.0.1", parts.port, timeout=imgproxy.TIMEOUT, retries=False)
    resp = pool.urlopen("GET", parts.path, redirect=False, preload_content=False)
    resp.piebb_pool = pool
    return resp


imgproxy._open = open_any_port
client = app.test_client()
fails = 0


def fetch(name):
    url = f"http://127.0.0.1:8099/media/{name}"
    with app.test_request_context():
        path = imgproxy.proxied(url)
    return client.get(path)


for name, want in [("ok.png", 200), ("hop.png", 200), ("evil.svg", 415), ("page.html", 415), ("huge.png", 413),
                   ("loop.png", 403), ("missing.png", 404)]:
    r = fetch(name)
    good = r.status_code == want
    fails += not good
    print(("ok   " if good else "FAIL ") + f"{name}: {r.status_code} (want {want})")
r = fetch("ok.png")
for h, v in [("Content-Type", "image/png"), ("X-Content-Type-Options", "nosniff")]:
    good = r.headers.get(h) == v
    fails += not good
    print(("ok   " if good else "FAIL ") + f"header {h}={r.headers.get(h)}")
good = "sandbox" in r.headers.get("Content-Security-Policy", "")
fails += not good
print(("ok   " if good else "FAIL ") + "proxied image served with sandbox CSP")
imgproxy._open = _open
with app.test_request_context():
    p = imgproxy.proxied("http://127.0.0.1:8099/media/ok.png")
r = client.get(p)
good = r.status_code == 403  # real _open refuses port 8099
fails += not good
print(("ok   " if good else "FAIL ") + f"non-80/443 port refused: {r.status_code}")
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
