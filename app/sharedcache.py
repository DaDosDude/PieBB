"""Small shared cache for data every visitor sees (topic/feed lists, per-community statistics).

- Kept in memory and mirrored to a JSON file in DATA_DIR, so both gunicorn workers share it and a
  restart or redeploy starts warm.
- Entries never expire on their own: callers ask how old an entry is and refresh it in the
  background when it's stale, while visitors keep getting the last known value. So a busy or slow
  PieFed makes the numbers a bit older instead of making pages wait.
"""
import atexit
import json
import os
import tempfile
import threading
import time

_lock = threading.Lock()
_data = {}            # key -> [stored_at, value]
_state = {"path": None, "mtime": None, "checked": 0.0, "dirty": False, "saved": 0.0}
RELOAD_EVERY = 15     # seconds between checks for the other worker's writes
SAVE_EVERY = 10       # seconds between writes to disk
MAX_ENTRIES = 20000


def configure(data_dir):
    with _lock:
        _state["path"] = os.path.join(data_dir, "cache", "shared.json")


def _reload_if_changed():
    """Merge the file into memory when another worker has written it (newer entries win)."""
    path = _state["path"]
    now = time.time()
    if not path or now - _state["checked"] < RELOAD_EVERY:
        return
    _state["checked"] = now
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return
    if mtime == _state["mtime"]:
        return
    try:
        with open(path) as f:
            disk = json.load(f)
    except (OSError, ValueError):
        return
    _state["mtime"] = mtime
    if isinstance(disk, dict):
        for k, v in disk.items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], (int, float)):
                if k not in _data or _data[k][0] < v[0]:
                    _data[k] = v


def get(key):
    """(value, age in seconds) or (None, None)."""
    with _lock:
        _reload_if_changed()
        hit = _data.get(key)
    if hit is None:
        return None, None
    return hit[1], time.time() - hit[0]


def put(key, value):
    with _lock:
        _data[key] = [time.time(), value]
        _state["dirty"] = True
    save()


def save(force=False):
    """Write to disk (at most every SAVE_EVERY seconds), merging what the other worker wrote."""
    with _lock:
        path = _state["path"]
        if not path or not _state["dirty"] or (not force and time.time() - _state["saved"] < SAVE_EVERY):
            return
        _state["checked"] = 0
        _reload_if_changed()
        if len(_data) > MAX_ENTRIES:  # drop the oldest entries
            for k, _ in sorted(_data.items(), key=lambda kv: kv[1][0])[:len(_data) - MAX_ENTRIES]:
                del _data[k]
        snapshot = dict(_data)
        _state["dirty"] = False
        _state["saved"] = time.time()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(snapshot, f, separators=(",", ":"))
        os.replace(tmp, path)
        with _lock:
            _state["mtime"] = os.stat(path).st_mtime_ns
    except OSError:
        with _lock:
            _state["dirty"] = True  # try again next time


atexit.register(save, True)  # keep what was learned when a worker stops


# ---- background refreshes ---------------------------------------------------------------------

_inflight = set()
_pool = None


def refresh_later(key, fn):
    """Run fn() in the background (once per key at a time); store its result unless it's None."""
    global _pool
    with _lock:
        if key in _inflight:
            return
        _inflight.add(key)
        if _pool is None:
            from concurrent.futures import ThreadPoolExecutor
            _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="piebb-refresh")

    def run():
        try:
            try:
                value = fn()
            except Exception:  # a failed refresh just keeps the old value
                value = None
            if value is not None:
                put(key, value)
        finally:
            with _lock:
                _inflight.discard(key)
            save()
    _pool.submit(run)
