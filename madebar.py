"""Подключение Made Bar к основному приложению (main.py вызывает setup_madebar)."""
import logging
import os
import re

from flask import Blueprint, Response, abort, jsonify, request, send_from_directory

log = logging.getLogger("korolko.madebar")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
page_bp = Blueprint("madebar_page", __name__)

_state = {"error": None}   # причина, если Made Bar не запустился (видна в админке)
_require_admin = None


def _ensure_bead_color_column():
    """Колонка beads.bead_color: create_all не добавляет колонки в уже существующую таблицу,
    поэтому на рабочей базе добавляем её сами (безопасно повторять)."""
    from sqlalchemy import inspect, text
    from models import Bead, SessionLocal

    if not hasattr(Bead, "bead_color"):
        raise RuntimeError(
            "В models.py у класса Bead нет поля bead_color. Добавьте "
            "bead_color = Column(String(7), nullable=True) и 'bead_color' в Bead.to_dict()"
        )
    engine = SessionLocal().get_bind()
    table = Bead.__table__.name
    if "bead_color" in {c["name"] for c in inspect(engine).get_columns(table)}:
        return
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN bead_color VARCHAR(7)'))
    log.info("Made Bar: в таблицу %s добавлена колонка bead_color", table)


def _short_error(exc) -> str:
    """Краткое описание ошибки без паролей из строки подключения."""
    msg = f"{type(exc).__name__}: {exc}"
    url = os.environ.get("DATABASE_URL", "")
    if url:
        msg = msg.replace(url, "***")
    msg = re.sub(r"://[^@\s/]+@", "://***@", msg)
    return msg[:400]


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


@page_bp.route("/api/admin/madebar-status", methods=["GET"])
def madebar_status():
    _require_admin()
    return jsonify({"ok": _state["error"] is None, "error": _state["error"]})


def setup_madebar(app, bot, admin_chat_ids, require_admin, upload_dir):
    """Конструктор не должен ронять весь магазин: любая ошибка только логируется."""
    global _require_admin
    _require_admin = require_admin

    # Страница, картинки и диагностика не зависят от базы
    app.register_blueprint(page_bp)

    try:
        from models import init_db
        from bead_api import register_bead_api
        from bead_admin import register_bead_admin
        from seed_beads import seed_if_empty
    except Exception as exc:
        _state["error"] = "Не загрузились модули Made Bar: " + _short_error(exc)
        log.exception("Made Bar отключён: не удалось загрузить модули (проверьте requirements.txt)")
        return

    try:
        init_db()
        _ensure_bead_color_column()
        seed_if_empty()
    except Exception as exc:
        _state["error"] = "Ошибка базы данных: " + _short_error(exc)
        log.exception("Made Bar: ошибка базы данных (проверьте DATABASE_URL)")

    register_bead_api(app, bot, admin_chat_ids)
    register_bead_admin(app, require_admin, upload_dir)
    log.info("Made Bar подключён")
