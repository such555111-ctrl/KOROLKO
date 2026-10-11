"""Подключение Made Bar к основному приложению (main.py вызывает setup_madebar)."""
import logging
import os

from flask import Blueprint, Response, abort, request, send_from_directory

log = logging.getLogger("korolko.madebar")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
page_bp = Blueprint("madebar_page", __name__)


@page_bp.route("/madebar", methods=["GET"])
def madebar_page():
    resp = send_from_directory(APP_DIR, "madebar.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@page_bp.route("/beadart/<kind>.svg", methods=["GET"])
def bead_art_image(kind):
    import bead_art
    try:
        svg = bead_art.render(
            kind,
            request.args.get("c", "")[:7],
            request.args.get("v", "")[:12],
            request.args.get("t", "")[:2],
        )
    except ValueError:
        abort(404)
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


def setup_madebar(app, bot, admin_chat_ids, require_admin, upload_dir):
    """Конструктор не должен ронять весь магазин: любая ошибка только логируется."""
    # Страница и картинки не зависят от базы: конструктор открывается всегда,
    # а при проблемах с БД покажет понятную ошибку вместо 404.
    app.register_blueprint(page_bp)

    try:
        from models import init_db
        from bead_api import register_bead_api
        from bead_admin import register_bead_admin
        from seed_beads import seed_if_empty
    except Exception:
        log.exception("Made Bar отключён: не удалось загрузить модули (проверьте requirements.txt)")
        return

    try:
        init_db()
        seed_if_empty()
    except Exception:
        log.exception("Made Bar: ошибка базы данных (проверьте DATABASE_URL)")

    register_bead_api(app, bot, admin_chat_ids)
    register_bead_admin(app, require_admin, upload_dir)
    log.info("Made Bar подключён")
