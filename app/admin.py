"""Admin panel: PieFed site admins only."""
import zipfile
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import forums, piefed, settings, themes
from .actions import load_moderation
from .views import logged_in, me_info, page_arg

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not logged_in():
            return redirect(url_for("bb.login", next=request.path))
        # /site is cached (and rate limited by PieFed), so confirm the login is still valid with an
        # uncached, unlimited call: a token revoked by logout raises SessionExpired here.
        piefed.get("/user/unread_count", cache=False)
        _, is_admin, _ = me_info()
        if not is_admin:
            abort(403, "Only site administrators can open the admin panel.")
        return fn(*a, **kw)
    return wrapper


@admin_bp.route("/")
@admin_required
def dashboard():
    return render_template("admin/dashboard.html", section="dashboard", settings=settings.load(),
                           themes=themes.available())


@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def general():
    if request.method == "POST":
        settings.update(
            forum_title=request.form.get("forum_title", "").strip(),
            login_required=bool(request.form.get("login_required")),
            feeds_position="before" if request.form.get("feeds_position") == "before" else "after",
            index_forum_limit=min(max(request.form.get("index_forum_limit", 3, type=int) or 0, 0), 100),
        )
        flash("Settings saved.")
        return redirect(url_for("admin.general"))
    return render_template("admin/settings.html", section="settings", settings=settings.load())


@admin_bp.route("/themes", methods=["GET", "POST"])
@admin_required
def theme_admin():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "select":
                slug = request.form.get("theme")
                if slug not in {t["slug"] for t in themes.available()}:
                    raise ValueError("Unknown theme.")
                settings.update(theme=slug)
                flash("Theme changed.")
            elif action == "upload":
                f = request.files.get("zip")
                if not f or not f.filename:
                    raise ValueError("Choose a zip file first.")
                created = themes.import_phpbb2(f.stream, f.filename)
                flash("Imported: " + ", ".join(c["name"] for c in created) + ". Select it below to activate.")
            elif action == "delete":
                slug = request.form.get("theme")
                themes.delete(slug)
                if settings.get("theme") == slug:
                    settings.update(theme="subsilver")
                flash("Theme deleted.")
            elif action == "css":
                settings.update(custom_css=request.form.get("custom_css", ""),
                                css_version=int(settings.get("css_version") or 1) + 1)
                flash("Custom CSS saved.")
        except ValueError as e:
            flash(str(e))
        except (KeyError, OSError, zipfile.BadZipFile, UnicodeDecodeError, StopIteration) as e:
            flash(f"Could not install that style ({e.__class__.__name__}).")
        return redirect(url_for("admin.theme_admin"))
    return render_template("admin/themes.html", section="themes", themes=themes.available(),
                           current=settings.get("theme"), custom_css=settings.get("custom_css"))


@admin_bp.route("/layout", methods=["GET", "POST"])
@admin_required
def layout():
    cats = forums.build_index(include_hidden=True)
    if request.method == "POST":
        new = {}
        nested = [x for c in cats for r in c["rows"] for x in r.get("rows", [])]
        keys = [c["key"] for c in cats] + [r["key"] for c in cats for r in c["rows"]] + [x["key"] for x in nested]
        orig = {c["key"]: c["orig_name"] for c in cats}
        orig.update({r["key"]: r["orig_name"] for c in cats for r in c["rows"]})
        orig.update({x["key"]: x["orig_name"] for x in nested})
        for key in keys:
            entry = {}
            name = request.form.get(f"name:{key}", "").strip()
            if name and name != orig.get(key):
                entry["name"] = name
            order = request.form.get(f"order:{key}", "").strip()
            if order.lstrip("-").isdigit():
                entry["order"] = int(order)
            entry["hidden"] = not request.form.get(f"show:{key}")
            new[key] = entry
        # keep entries for items not currently listed (e.g. communities hidden from the flat list)
        merged = dict(settings.get("layout"))
        merged.update(new)
        settings.update(layout=merged)
        flash("Layout saved.")
        return redirect(url_for("admin.layout"))
    # A community can appear in a topic and in feeds; its settings are shared, so give it inputs only once
    # (walked in display order, so the first one on the page gets the inputs)
    seen = set()
    for item in (x for c in cats for x in [c] + [y for r in c["rows"] for y in [r] + r.get("rows", [])]):
        item["dup"] = item["key"] in seen
        seen.add(item["key"])
    return render_template("admin/layout.html", section="layout", cats=cats)


@admin_bp.route("/layout/reset", methods=["POST"])
@admin_required
def layout_reset():
    settings.update(layout={})
    flash("Layout reset to PieFed's defaults.")
    return redirect(url_for("admin.layout"))


@admin_bp.route("/moderation")
@admin_required
def moderation():
    show = request.args.get("show") if request.args.get("show") in ("reports", "modlog") else "reports"
    page = page_arg()
    unresolved = request.args.get("all") != "1"
    data, error, has_next = load_moderation(page, show, unresolved)
    return render_template("admin/moderation.html", section="moderation", show=show, page=page,
                           unresolved=unresolved, data=data, error=error, has_next=has_next, panel="admin")


@admin_bp.route("/reports/resolve", methods=["POST"])
@admin_required
def resolve_report():
    kind = request.form.get("kind")
    report_id = request.form.get("report_id", type=int)
    if kind not in ("post", "comment") or not report_id:
        abort(400)
    piefed.post(f"/{kind}/report/resolve", {"report_id": report_id,
                                             "resolved": request.form.get("resolved") != "0"}, method="PUT")
    flash("Report updated.")
    return redirect(url_for("admin.moderation", show="reports"))


REGISTRATIONS_PER_PAGE = 25


@admin_bp.route("/registrations")
@admin_required
def registrations():
    """Sign-up applications waiting for approval (PieFed instances with application-based registration)."""
    pending = request.args.get("all") != "1"
    page = page_arg()
    error = None
    try:
        regs = piefed.get("/admin/registration_application/list", cache=False, pending_only=str(pending).lower(),
                          sort="Old" if pending else "New", page=page,
                          limit=REGISTRATIONS_PER_PAGE).get("registrations") or []
    except piefed.SessionExpired:
        raise
    except piefed.ApiError as e:
        regs, error = [], e.message
    return render_template("admin/registrations.html", section="registrations", regs=regs, pending=pending,
                           page=page, error=error, has_next=len(regs) >= REGISTRATIONS_PER_PAGE)


@admin_bp.route("/registrations/decide", methods=["POST"])
@admin_required
def registration_decide():
    user_id = request.form.get("user_id", type=int)
    decision = request.form.get("decision")
    if not user_id or decision not in ("approve", "deny"):
        abort(400)
    name = request.form.get("name", "")[:100] or f"#{user_id}"
    try:
        piefed.post("/admin/registration_application/approve", {"user_id": user_id, "approve": decision == "approve"},
                    method="PUT")
        flash(f"{name} approved." if decision == "approve" else f"{name}'s application was denied.")
    except piefed.SessionExpired:
        raise
    except piefed.ApiError as e:
        flash(e.message)
    return redirect(url_for("admin.registrations"))
