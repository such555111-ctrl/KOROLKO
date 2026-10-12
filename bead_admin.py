"""Админ-API Made Bar: каталог бусин и заказы собранных украшений."""
import logging
import math
import os
import uuid
from collections import Counter

from flask import Blueprint, abort, jsonify, request
from sqlalchemy import func, select

import store
from models import Bead, CustomOrder, SessionLocal

log = logging.getLogger("korolko.beadadmin")

bead_admin_bp = Blueprint("bead_admin", __name__)

MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_URL_PREFIXES = ("/uploads/", "/beadart/", "https://")
ORDER_STATUSES = {"new", "done", "cancelled"}

_require_admin = None
_upload_dir = None


# --- Вспомогательные функции -------------------------------------------

def _guard():
    _require_admin()   # abort(401/403) при неверном пароле


def _sniff_image(head: bytes):
    """Определяем формат по сигнатуре, а не по имени файла."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def _cleanup_file(session, url: str):
    """Удаляет локальный файл бусины, если на него больше никто не ссылается."""
    if not url or not url.startswith("/uploads/"):
        return
    if session.scalar(select(func.count()).select_from(Bead).where(Bead.image_url == url)):
        return
    path = os.path.join(_upload_dir, os.path.basename(url))   # basename: защита от ../
    try:
        store.delete_upload(os.path.basename(url))
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        log.exception("Не удалось удалить файл %s", path)


def _number(raw, label, lo, hi):
    try:
        val = float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        abort(400, description=f"{label}: некорректное число")
    if not math.isfinite(val) or not lo <= val <= hi:
        abort(400, description=f"{label}: от {lo:g} до {hi:g}")
    return round(val, 2)


def _clean_fields(data: dict, partial: bool) -> dict:
    out = {}

    if not partial or "name" in data:
        name = str(data.get("name") or "").strip()
        if not 1 <= len(name) <= 120:
            abort(400, description="Название: от 1 до 120 символов")
        out["name"] = name

    if not partial or "category" in data:
        cat = str(data.get("category") or "").strip()
        if not 1 <= len(cat) <= 60:
            abort(400, description="Категория: от 1 до 60 символов")
        out["category"] = cat

    if not partial or "price" in data:
        out["price"] = _number(data.get("price"), "Цена", 0, 100000)

    if "size_mm" in data or not partial:
        raw = data.get("size_mm")
        out["size_mm"] = 8.0 if raw in (None, "") and not partial else _number(raw, "Размер, мм", 1, 30)

    if not partial or "image_url" in data:
        url = str(data.get("image_url") or "").strip()
        if not url or len(url) > 500 or not url.startswith(ALLOWED_URL_PREFIXES):
            abort(400, description="Нужна картинка: загрузите PNG или укажите ссылку https://…")
        out["image_url"] = url

    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            abort(400, description="is_active должен быть true/false")
        out["is_active"] = data["is_active"]

    if data.get("sort_order") not in (None, ""):
        try:
            out["sort_order"] = int(data["sort_order"])
        except (TypeError, ValueError):
            abort(400, description="Порядок должен быть целым числом")

    return out


def _json_body() -> dict:
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        abort(400, description="Ожидался JSON-объект")
    return data


# --- Бусины -------------------------------------------------------------

@bead_admin_bp.route("/api/admin/beads", methods=["GET"])
def admin_list_beads():
    _guard()
    session = SessionLocal()

    beads = session.scalars(
        select(Bead).order_by(Bead.sort_order, Bead.id)
    ).all()

    # Сколько раз бусина встречается в заказах (для предупреждения при удалении)
    used = Counter()
    for (seq,) in session.execute(select(CustomOrder.beads_sequence)):
        used.update(set(seq or []))

    return jsonify({
        "beads": [{**b.to_dict(), "used_in_orders": used.get(b.id, 0)} for b in beads],
        "categories": list(dict.fromkeys(b.category for b in beads)),
    })


@bead_admin_bp.route("/api/admin/beads", methods=["POST"])
def admin_create_bead():
    _guard()
    fields = _clean_fields(_json_body(), partial=False)
    session = SessionLocal()

    if "sort_order" not in fields:   # новая бусина встаёт в конец
        fields["sort_order"] = (session.scalar(select(func.max(Bead.sort_order))) or 0) + 1

    bead = Bead(**fields)
    try:
        session.add(bead)
        session.commit()
    except Exception:
        session.rollback()
        log.exception("Ошибка создания бусины")
        return jsonify({"error": "Не удалось сохранить бусину"}), 500
    return jsonify(bead.to_dict()), 201


@bead_admin_bp.route("/api/admin/beads/<int:bead_id>", methods=["PUT"])
def admin_update_bead(bead_id):
    _guard()
    fields = _clean_fields(_json_body(), partial=True)   # меняем только присланные поля
    session = SessionLocal()

    bead = session.get(Bead, bead_id)
    if not bead:
        abort(404, description="Бусина не найдена")

    old_url = bead.image_url
    for key, value in fields.items():
        setattr(bead, key, value)
    try:
        session.commit()
    except Exception:
        session.rollback()
        log.exception("Ошибка обновления бусины %s", bead_id)
        return jsonify({"error": "Не удалось сохранить изменения"}), 500

    if "image_url" in fields and fields["image_url"] != old_url:
        _cleanup_file(session, old_url)
    return jsonify(bead.to_dict())


@bead_admin_bp.route("/api/admin/beads/<int:bead_id>", methods=["DELETE"])
def admin_delete_bead(bead_id):
    _guard()
    session = SessionLocal()

    bead = session.get(Bead, bead_id)
    if not bead:
        abort(404, description="Бусина не найдена")

    url = bead.image_url
    try:
        session.delete(bead)
        session.commit()
    except Exception:
        session.rollback()
        log.exception("Ошибка удаления бусины %s", bead_id)
        return jsonify({"error": "Не удалось удалить бусину"}), 500

    _cleanup_file(session, url)
    return jsonify({"ok": True})


@bead_admin_bp.route("/api/admin/beads/upload", methods=["POST"])
def admin_upload_bead_image():
    _guard()
    file = request.files.get("file")
    if not file or not file.filename:
        abort(400, description="Файл не передан")

    head = file.stream.read(12)
    ext = _sniff_image(head)
    if not ext:
        abort(400, description="Нужен PNG (или WebP) с прозрачным фоном")

    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > MAX_IMAGE_BYTES:
        abort(400, description="Файл больше 2 МБ")

    filename = f"bead_{uuid.uuid4().hex}{ext}"
    payload = file.read()
    with open(os.path.join(_upload_dir, filename), "wb") as out:
        out.write(payload)
    store.save_upload(filename, payload, "image/png" if ext == ".png" else "image/webp")
    return jsonify({"url": f"/uploads/{filename}"}), 201


# --- Заказы Made Bar ----------------------------------------------------

@bead_admin_bp.route("/api/admin/custom-orders", methods=["GET"])
def admin_list_custom_orders():
    _guard()
    session = SessionLocal()

    orders = session.scalars(
        select(CustomOrder).order_by(CustomOrder.id.desc()).limit(200)
    ).all()

    all_ids = {i for o in orders for i in (o.beads_sequence or [])}
    beads = {}
    if all_ids:
        beads = {b.id: b for b in session.scalars(select(Bead).where(Bead.id.in_(all_ids)))}

    result = []
    for o in orders:
        items = []
        for bid in (o.beads_sequence or []):
            b = beads.get(bid)
            items.append(
                {"id": bid, "name": b.name, "category": b.category, "size_mm": b.size_mm,
                 "price": b.price, "image_url": b.image_url, "deleted": False}
                if b else
                {"id": bid, "name": f"Бусина №{bid} (удалена)", "category": "", "size_mm": 0,
                 "price": 0, "image_url": "", "deleted": True}
            )
        d = o.to_dict()
        d["items"] = items
        del d["beads_sequence"]
        result.append(d)
    return jsonify(result)


@bead_admin_bp.route("/api/admin/custom-orders/<int:order_id>/status", methods=["PUT"])
def admin_set_custom_order_status(order_id):
    _guard()
    status = str(_json_body().get("status") or "")
    if status not in ORDER_STATUSES:
        abort(400, description="Недопустимый статус")

    session = SessionLocal()
    order = session.get(CustomOrder, order_id)
    if not order:
        abort(404, description="Заказ не найден")
    order.status = status
    try:
        session.commit()
    except Exception:
        session.rollback()
        log.exception("Ошибка смены статуса заказа %s", order_id)
        return jsonify({"error": "Не удалось сохранить статус"}), 500
    return jsonify({"ok": True, "status": status})


# --- Подключение --------------------------------------------------------

def register_bead_admin(app, require_admin, upload_dir):
    global _require_admin, _upload_dir
    _require_admin = require_admin
    _upload_dir = upload_dir
    app.register_blueprint(bead_admin_bp)
