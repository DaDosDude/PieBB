"""Admin-editable settings, stored as JSON in DATA_DIR (shared by all gunicorn workers)."""
import json
import os
import tempfile
import threading

from flask import current_app

_lock = threading.Lock()
_cache = {"mtime": None, "data": {}}

DEFAULTS = {
    "forum_title": "",
    "index_mode": "topics",
    "login_required": False,
    "theme": "subsilver",
    "custom_css": "",
    "css_version": 1,
    # "topic:10" / "feed:3" / "community:7" / "group:loose" -> {"name", "order", "hidden"}
    "layout": {},
    "feeds_position": "after",
    # communities shown per category on the index (most active first); 0 = all
    "index_forum_limit": 3,
}


def _path():
    return os.path.join(current_app.config["DATA_DIR"], "settings.json")


def _env_defaults():
    cfg = current_app.config
    d = dict(DEFAULTS)
    d["forum_title"] = cfg["FORUM_TITLE"]
    d["index_mode"] = cfg["INDEX_MODE"]
    d["login_required"] = cfg["LOGIN_REQUIRED"]
    return d


def load():
    path = _path()
    try:
        st = os.stat(path)
        mtime = (st.st_ino, st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return _env_defaults()
    if mtime != _cache["mtime"]:
        with _lock:
            try:
                with open(path) as f:
                    _cache["data"] = _validated(json.load(f))
                _cache["mtime"] = mtime
            except (OSError, ValueError):
                current_app.logger.exception("settings.json unreadable, using defaults")
                return _env_defaults()
    merged = _env_defaults()
    merged.update(_cache["data"])
    return merged


def _validated(data):
    """Drop anything in settings.json whose type doesn't match the defaults (e.g. a hand edit gone wrong)."""
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        default = DEFAULTS.get(key)
        if key not in DEFAULTS:
            continue
        if isinstance(default, bool):
            if isinstance(value, bool):
                out[key] = value
        elif isinstance(default, int):
            if isinstance(value, int) and not isinstance(value, bool):
                out[key] = value
        elif isinstance(default, str):
            if isinstance(value, str):
                out[key] = value
        elif key == "layout" and isinstance(value, dict):
            layout = {}
            for k, v in value.items():
                if isinstance(k, str) and isinstance(v, dict):
                    entry = {}
                    if isinstance(v.get("name"), str):
                        entry["name"] = v["name"]
                    if isinstance(v.get("order"), int) and not isinstance(v.get("order"), bool):
                        entry["order"] = v["order"]
                    if isinstance(v.get("hidden"), bool):
                        entry["hidden"] = v["hidden"]
                    layout[k] = entry
            out[key] = layout
    if out.get("index_mode") not in (None, "topics", "flat"):
        del out["index_mode"]
    return out


def get(key):
    return load().get(key, DEFAULTS.get(key))


def update(**changes):
    with _lock:
        data = {}
        try:
            with open(_path()) as f:
                data = _validated(json.load(f))
        except (OSError, ValueError):
            pass
        data.update(changes)
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_path()), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp, _path())
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        _cache["mtime"] = None


def layout_for(key):
    return load().get("layout", {}).get(key, {})
