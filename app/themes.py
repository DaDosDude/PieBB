"""Themes: built-ins plus phpBB2 style packages (zip) uploaded by an admin.

A phpBB2 style is a templates/<Name>/ directory holding:
  theme_info.cfg  colours/fonts per style  ($Name[0]['body_bgcolor'] = "E5E5E5";)
  <Name>.cfg      image map               ($images['folder_new'] = "$current_template_images/folder_new.gif";)
  <Name>.css      optional stylesheet
  images/         folder icons, buttons, header backgrounds
PieBB's markup uses the subSilver class names, so the stylesheet and colours apply as-is;
the .tpl templates are ignored.
"""
import io
import json
import os
import re
import shutil
import zipfile

from flask import current_app, request, url_for

from . import settings

BUILTIN = {
    "subsilver": {"name": "subSilver", "css": []},
    "charcoal": {"name": "Charcoal (dark)", "css": ["themes/charcoal.css"]},
}
ALLOWED_EXT = {".css", ".gif", ".png", ".jpg", ".jpeg", ".webp", ".cfg", ".txt", ".bmp", ".ico"}
MAX_ZIP = 25 * 1024 * 1024
IMAGE_KEYS = ("folder", "folder_new", "folder_hot", "folder_hot_new", "folder_locked", "folder_locked_new",
              "folder_sticky", "folder_sticky_new", "folder_announce", "forum", "forum_new", "forum_locked",
              "post_new", "reply_new", "reply_locked", "icon_quote", "icon_latest_reply",
              "icon_minipost", "icon_minipost_new")
_SLUG = re.compile(r"[^a-z0-9_-]+")


def themes_dir():
    return os.path.join(current_app.config["DATA_DIR"], "themes")


def _meta(slug):
    try:
        with open(os.path.join(themes_dir(), slug, "piebb-theme.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def available():
    out = [{"slug": k, "name": v["name"], "builtin": True} for k, v in BUILTIN.items()]
    if os.path.isdir(themes_dir()):
        for slug in sorted(os.listdir(themes_dir())):
            meta = _meta(slug)
            if meta:
                out.append({"slug": slug, "name": meta["name"], "builtin": False,
                            "source": meta.get("source", "")})
    return out


COOKIE = "piebb_theme"


def chosen():
    """The visitor's own theme (cookie), if allowed and still installed; otherwise None."""
    slug = request.cookies.get(COOKIE)
    if not slug or not settings.get("visitor_themes"):
        return None
    return slug if slug in {t["slug"] for t in available()} else None


def current():
    """Context for templates: stylesheet URLs and image lookup for the active theme."""
    site_default = settings.get("theme") or "subsilver"
    picked = chosen()
    slug = picked or site_default
    css, images = [], {}
    if slug in BUILTIN:
        css = [url_for("static", filename=p) for p in BUILTIN[slug]["css"]]
    else:
        meta = _meta(slug)
        if meta:
            css = [url_for("theme_file", slug=slug, path=p) for p in (meta.get("css") or [])]
            images = {k: url_for("theme_file", slug=slug, path=v) for k, v in (meta.get("images") or {}).items()}
    if settings.get("custom_css"):
        css.append(url_for("custom_css", v=settings.get("css_version")))
    return {"slug": slug, "css": css, "img": images.get, "default": site_default, "picked": picked,
            "choices": available() if settings.get("visitor_themes") else []}


# ---- phpBB2 import --------------------------------------------------------

_INFO_RE = re.compile(r"\$\w+\[(\d+)\]\['(\w+)'\]\s*=\s*['\"]([^'\"]*)['\"]")
_IMG_RE = re.compile(r"\$images\['(\w+)'\]\s*=\s*['\"]([^'\"]*)['\"]")


def _safe_members(zf):
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or name.startswith("/") or ".." in name.split("/") or name.startswith("__MACOSX"):
            continue
        if os.path.splitext(name)[1].lower() not in ALLOWED_EXT:
            continue
        yield info, name


def import_phpbb2(fileobj, filename):
    data = fileobj.read(MAX_ZIP + 1)
    if len(data) > MAX_ZIP:
        raise ValueError("Zip is larger than 25 MB.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Not a zip file.")
    members = list(_safe_members(zf))
    if sum(i.file_size for i, _ in members) > 4 * MAX_ZIP:
        raise ValueError("Zip expands too large.")

    # The style root is the directory holding theme_info.cfg
    roots = [n.rsplit("/", 1)[0] if "/" in n else "" for _, n in members
             if n.lower().rsplit("/", 1)[-1] == "theme_info.cfg"]
    if not roots:
        raise ValueError("No theme_info.cfg found: this does not look like a phpBB2 style.")
    root = roots[0]
    prefix = root + "/" if root else ""
    files = {n[len(prefix):]: i for i, n in members if n.startswith(prefix)}

    info_name = next(f for f in files if f.lower() == "theme_info.cfg")
    info_text = zf.read(files[info_name]).decode("latin-1")
    styles = {}
    for idx, key, val in _INFO_RE.findall(info_text):
        styles.setdefault(int(idx), {})[key] = val
    if not styles:
        raise ValueError("theme_info.cfg contains no style definitions.")

    tpl_name = root.rsplit("/", 1)[-1] if root else os.path.splitext(filename)[0]
    cfg_name = next((f for f in files if f.lower() == f"{tpl_name.lower()}.cfg"), None) \
        or next((f for f in files if f.lower().endswith(".cfg") and "/" not in f and f != info_name), None)
    images = {}
    if cfg_name:
        lang_dirs = sorted({f.split("/")[1] for f in files if f.startswith("images/lang_") and f.count("/") >= 2})
        lang = "lang_english" if "lang_english" in lang_dirs else (lang_dirs[0] if lang_dirs else "lang_english")
        for key, path in _IMG_RE.findall(zf.read(files[cfg_name]).decode("latin-1")):
            rel = path.replace("$current_template_images", "images").replace("{LANG}", lang).lstrip("/")
            if key in IMAGE_KEYS and rel in files:
                images[key] = rel

    created = []
    for idx in sorted(styles):
        st = styles[idx]
        name = st.get("style_name") or tpl_name
        slug = _SLUG.sub("-", name.lower()).strip("-") or "style"
        if slug in BUILTIN:
            slug += "-phpbb2"
        dest = os.path.join(themes_dir(), slug)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)
        for rel, info in files.items():
            target = os.path.normpath(os.path.join(dest, rel))
            if not target.startswith(dest + os.sep):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
        css = []
        with open(os.path.join(dest, "piebb-generated.css"), "w") as f:
            f.write(generate_css(st))
        css.append("piebb-generated.css")
        sheet = st.get("head_stylesheet")
        if sheet and sheet in files:
            css.append(sheet)
        meta = {"name": name, "source": f"phpBB2 style from {filename}", "css": css, "images": images}
        with open(os.path.join(dest, "piebb-theme.json"), "w") as f:
            json.dump(meta, f, indent=2)
        created.append({"slug": slug, "name": name})
    return created


def delete(slug):
    slug = slug if isinstance(slug, str) else ""
    if slug in BUILTIN or not re.fullmatch(r"[a-z0-9_-]+", slug):
        raise ValueError("Built-in themes cannot be deleted.")
    shutil.rmtree(os.path.join(themes_dir(), slug), ignore_errors=True)


def _col(v, default):
    v = (v or "").strip().lstrip("#")
    return f"#{v}" if re.fullmatch(r"[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}", v) else default


def _px(v, default):
    return f"{int(v)}px" if (v or "").strip().isdecimal() else default


def _img(v):
    v = (v or "").strip()
    if re.search(r"\.(gif|png|jpe?g|webp)$", v, re.I) and not re.search(r"[\"'()\\]", v):
        return f"url('images/{v}')" if "/" not in v else f"url('{v}')"
    return None


def generate_css(t):
    """Rebuild the stylesheet phpBB2's overall_header.tpl generates from theme_info.cfg values."""
    def c(key, default):
        return _col(t.get(key), default)
    font1 = re.sub(r"[;{}<>/*\\\n\r]", "", t.get("fontface1") or "Verdana, Arial, Helvetica, sans-serif")
    font2 = re.sub(r"[;{}<>/*\\\n\r]", "", t.get("fontface2") or "Trebuchet MS")
    font3 = re.sub(r"[;{}<>/*\\\n\r]", "", t.get("fontface3") or "Courier, 'Courier New', sans-serif")
    fs1, fs2, fs3 = _px(t.get("fontsize1"), "10px"), _px(t.get("fontsize2"), "11px"), _px(t.get("fontsize3"), "12px")
    th1, th2, th3 = _img(t.get("th_class1")), _img(t.get("th_class2")), _img(t.get("th_class3"))
    body_bg = _img(t.get("body_background"))
    return f"""/* generated by PieBB from theme_info.cfg */
body {{ background: {c('body_bgcolor', '#E5E5E5')}{(' ' + body_bg) if body_bg else ''}; color: {c('body_text', '#000000')};
  font-family: {font1}; font-size: {fs2}; }}
a, a:link, a:visited {{ color: {c('body_link', '#006699')}; }}
a:hover {{ color: {c('body_hlink', '#DD6900')}; }}
.wrap, .bodyline {{ background: {c('td_color2', '#FFFFFF')}; border-color: {c('th_color1', '#98AAB1')}; }}
table.forumline {{ background: {c('th_color2', '#006699')}; border: 1px solid {c('th_color2', '#006699')}; }}
td.row1 {{ background: {c('tr_color1', '#EFEFEF')}; }}
td.row2 {{ background: {c('tr_color2', '#DEE3E7')}; }}
td.row3, td.spaceRow {{ background: {c('tr_color3', '#D1D7DC')}; }}
table.thread td.spaceRow {{ background: {c('th_color2', '#006699')}; }}
th {{ color: {c('fontcolor3', '#FFA34F')}; background: {c('th_color3', '#006699')}{(' ' + th2) if th2 else ''}; background-repeat: repeat-x; }}
td.catHead, td.catLeft, td.catBottom, .sidebar-top summary {{ background: {c('tr_color3', '#D1D7DC')}{(' ' + th1) if th1 else ''}; background-repeat: repeat-x; border-color: {c('td_color2', '#FFFFFF')}; }}
{('td.catBottom { background-image: ' + th3 + '; }') if th3 else ''}
.cattitle, .forumlink, .sidebar-top summary {{ color: {c('body_link', '#006699')}; }}
.maintitle {{ font-family: {font2}; color: {c('body_text', '#000000')}; }}
.gensmall, .postdetails, .rank {{ font-size: {fs1}; color: {c('body_text', '#000000')}; }}
.gen, .postbody {{ font-size: {fs3}; color: {c('body_text', '#000000')}; }}
.genmed, .topictitle, .nav, .name {{ font-size: {fs2}; }}
.postbody blockquote, .quote {{ background: {c('td_color1', '#FAFAFA')}; border-color: {c('tr_color3', '#D1D7DC')}; color: {c('fontcolor2', '#444444')}; }}
.postbody pre, .postbody code {{ font-family: {font3}; color: {c('fontcolor2', '#006600')}; background: {c('td_color1', '#FAFAFA')}; }}
hr {{ border-top-color: {c('tr_color3', '#D1D7DC')}; }}
input, textarea, select {{ font-family: {font1}; color: {c('fontcolor1', '#000000')}; background: {c('td_color1', '#FAFAFA')}; border-color: {c('th_color1', '#98AAB1')}; }}
.btn, input[type=submit], button {{ background: {c('td_color1', '#FAFAFA')}; color: {c('fontcolor1', '#000000')}; border-color: {c('th_color1', '#98AAB1')}; }}
"""
