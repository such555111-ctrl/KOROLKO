"""Публичный API конструктора Made Bar (то, что вызывает мини-апп)."""
import html
import json
import logging
import time
from collections import Counter, defaultdict, deque

from flask import Blueprint, Response, jsonify, request
from sqlalchemy import select

from madebar_config import (
    BASE_PRICE, HARDWARE_CATEGORIES, JEWELRY_TYPES, MAX_BEADS,
    RATE_LIMIT_ORDERS, RATE_LIMIT_WINDOW_SEC,
)
from models import Bead, CustomOrder, Hardware, SessionLocal

log = logging.getLogger("korolko.beadbar")

bead_bp = Blueprint("bead_api", __name__)

ALLOWED_DELIVERY = {"Доставка", "Самовывоз"}
PRICE_TOLERANCE = 0.01   # допустимое расхождение цены клиента и сервера

_bot = None
_admin_chat_ids: set = set()
_hits = defaultdict(deque)   # ip -> метки времени последних заказов


# --- Вспомогательные функции -------------------------------------------

def _error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() or request.remote_addr or "?")


def _rate_limited() -> bool:
    now = time.monotonic()
    q = _hits[_client_ip()]
    while q and now - q[0] > RATE_LIMIT_WINDOW_SEC:
        q.popleft()
    if len(q) >= RATE_LIMIT_ORDERS:
        return True
    q.append(now)
    return False


def _clean_phone(raw: str):
    """Оставляет цифры и ведущий '+', проверяет длину 10-15 цифр."""
    raw = (raw or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not 10 <= len(digits) <= 15:
        return None
    return ("+" if raw.startswith("+") else "") + digits


def _thread_color(b):
    """Цвет круга на нити. Если админ его не задал, берём цвет нарисованной бусины
    (/beadart/gem.svg?c=9a6bd1); для загруженного фото без цвета остаётся None (нейтральный круг)."""
    if b.bead_color:
        return b.bead_color
    url = b.image_url or ""
    if not url.startswith("/beadart/"):
        return None
    try:
        import bead_art
        from urllib.parse import parse_qs, urlparse
        u = urlparse(url)
        kind = u.path.rsplit("/", 1)[-1].replace(".svg", "")
        c = (parse_qs(u.query).get("c") or [""])[0].lstrip("#").lower()
        if bead_art.HEX_RE.match(c):
            return "#" + c
        return "#" + bead_art.KINDS[kind][0]
    except Exception:
        return None


def _fmt_num(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _validate_order(data: dict):
    """Возвращает (clean_dict, error_message)."""
    name = str(data.get("customer_name") or "").strip()
    if not 2 <= len(name) <= 120:
        return None, "Укажите имя (от 2 до 120 символов)"

    phone = _clean_phone(str(data.get("customer_phone") or ""))
    if not phone:
        return None, "Некорректный номер телефона"

    delivery = str(data.get("delivery_type") or "").strip()
    if delivery not in ALLOWED_DELIVERY:
        return None, "delivery_type должен быть «Доставка» или «Самовывоз»"

    address = str(data.get("address") or "").strip() or None
    if delivery == "Доставка":
        if not address or len(address) < 5:
            return None, "Для доставки нужно указать адрес"
    else:
        address = None
    if address and len(address) > 500:
        return None, "Адрес слишком длинный"

    comment = str(data.get("comment") or "").strip() or None
    if comment and len(comment) > 500:
        return None, "Комментарий слишком длинный"

    # Telegram-аккаунт покупателя (из мини-аппа): нужен, чтобы заказ был виден в профиле
    tg_user = data.get("tg_user") if isinstance(data.get("tg_user"), dict) else {}
    try:
        chat_id = int(tg_user.get("id")) if tg_user.get("id") else None
    except (TypeError, ValueError):
        chat_id = None
    username = (str(tg_user.get("username") or "").strip()[:64]) or None

    ids = data.get("beads_ids")
    if not isinstance(ids, list) or not ids:
        return None, "Украшение пустое: добавьте хотя бы одну бусину"
    if len(ids) > MAX_BEADS:
        return None, f"Слишком много бусин (максимум {MAX_BEADS})"
    # bool является подклассом int, его отсекаем отдельно
    if not all(isinstance(i, int) and not isinstance(i, bool) and i > 0 for i in ids):
        return None, "beads_ids должен быть массивом положительных целых чисел"

    # Тип и размер: обязательны (клиент должен выбрать размер до оформления)
    jtype = str(data.get("jewelry_type") or "").strip()
    if jtype not in JEWELRY_TYPES:
        return None, "Выберите тип украшения: браслет или колье"
    try:
        size_cm = float(data.get("size_cm"))
    except (TypeError, ValueError):
        return None, "Выберите размер украшения"
    if size_cm not in [float(x) for x in JEWELRY_TYPES[jtype]["sizes"]]:
        return None, "Выберите размер из доступных для этого типа украшения"

    # Фурнитура необязательна: либо null, либо id активной позиции
    hw_id = data.get("hardware_id")
    if hw_id in (None, ""):
        hw_id = None
    elif not isinstance(hw_id, int) or isinstance(hw_id, bool) or hw_id <= 0:
        return None, "Некорректная фурнитура"

    try:
        client_total = float(data.get("total_price"))
    except (TypeError, ValueError):
        return None, "Некорректная total_price"

    return {
        "customer_name": name, "customer_phone": phone, "delivery_type": delivery,
        "address": address, "comment": comment, "beads_ids": ids,
        "client_total": client_total,
        "jewelry_type": jtype, "size_cm": size_cm, "hardware_id": hw_id,
        "customer_chat_id": chat_id, "customer_username": username,
    }, None


def _size_text(order) -> str:
    """«Браслет, 17 см» (пусто у старых заказов без размера)."""
    t = JEWELRY_TYPES.get(order.jewelry_type or "")
    if not t or not order.size_cm:
        return ""
    return f"{t['label']}, {_fmt_num(order.size_cm)} см"


def _build_recipe(order: CustomOrder, beads_by_id: dict) -> str:
    """Текстовый рецепт сборки для админа (HTML для Telegram)."""
    esc = html.escape
    seq = order.beads_sequence
    total_mm = sum(beads_by_id[i].size_mm for i in seq)

    lines = [
        f"🧵 <b>Новый заказ Made Bar #{order.id}</b>",
        "",
        f"👤 {esc(order.customer_name)}",
        f"📞 {esc(order.customer_phone)}",
        f"🚚 {esc(order.delivery_type)}",
    ]
    if order.address:
        lines.append(f"📍 {esc(order.address)}")
    if order.comment:
        lines.append(f"💬 {esc(order.comment)}")
    if order.customer_username:
        lines.append(f"✈️ @{esc(order.customer_username)}")

    size_text = _size_text(order)
    if size_text:
        lines.append(f"📏 <b>{esc(size_text)}</b>")
    if order.hardware_name:
        lines.append(
            f"🔩 Фурнитура: <b>{esc(order.hardware_name)}</b> "
            f"({esc(order.hardware_category or '')}, {_fmt_num(order.hardware_price or 0)} ₽)"
        )

    lines += ["", f"<b>Рецепт сборки</b> (слева направо, {len(seq)} шт., бусины ≈ {total_mm / 10:.1f} см):"]
    for pos, bead_id in enumerate(seq, start=1):
        b = beads_by_id[bead_id]
        lines.append(f"{pos}. {esc(b.name)} <i>({_fmt_num(b.size_mm)} мм, {esc(b.category)})</i>")

    lines += ["", "<b>Закупка по позициям:</b>"]
    for bead_id, qty in Counter(seq).items():
        b = beads_by_id[bead_id]
        lines.append(f"• {esc(b.name)} × {qty} = {_fmt_num(b.price * qty)} ₽")
    lines.append(f"• Основа (шнурок + застёжка) = {_fmt_num(BASE_PRICE)} ₽")
    if order.hardware_name:
        lines.append(f"• Фурнитура: {esc(order.hardware_name)} = {_fmt_num(order.hardware_price or 0)} ₽")

    lines += ["", f"💰 <b>Итого: {_fmt_num(order.total_price)} ₽</b>"]
    return "\n".join(lines)


def _notify_admins(text: str) -> None:
    """Отправка в Telegram. Ошибки не должны ломать оформление заказа."""
    if _bot is None or not _admin_chat_ids:
        log.warning("ADMIN_CHAT_ID не задан, уведомление не отправлено")
        return
    for chat_id in _admin_chat_ids:
        try:
            _bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:
            log.exception("Не удалось отправить уведомление админу %s", chat_id)


# --- Эндпоинты ----------------------------------------------------------

@bead_bp.route("/api/madebar/config", methods=["GET"])
def get_config():
    resp = jsonify({
        "base_price": BASE_PRICE, "max_beads": MAX_BEADS,
        "types": [{"id": k, "label": v["label"], "sizes": v["sizes"]} for k, v in JEWELRY_TYPES.items()],
    })
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bead_bp.route("/api/beads", methods=["GET"])
def get_beads():
    session = SessionLocal()
    rows = session.scalars(
        select(Bead).where(Bead.is_active.is_(True)).order_by(Bead.sort_order, Bead.id)
    ).all()

    grouped: dict = {}
    for b in rows:
        grouped.setdefault(b.category, []).append({
            "id": b.id, "name": b.name, "category": b.category,
            "image_url": b.image_url, "price": b.price, "size_mm": b.size_mm,
            "bead_color": _thread_color(b),
        })
    # Не через jsonify: он сортирует ключи по алфавиту, а нам нужен порядок категорий
    # по sort_order (иначе табы в конструкторе встанут в алфавитном порядке).
    resp = Response(json.dumps(grouped, ensure_ascii=False), mimetype="application/json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bead_bp.route("/api/hardware", methods=["GET"])
def get_hardware():
    """Активная фурнитура по категориям (Серебро, Золото); пустые категории не отдаём."""
    session = SessionLocal()
    rows = session.scalars(
        select(Hardware).where(Hardware.is_active.is_(True)).order_by(Hardware.id)
    ).all()
    grouped = {cat: [] for cat in HARDWARE_CATEGORIES}
    for h in rows:
        if h.category in grouped:
            grouped[h.category].append({
                "id": h.id, "name": h.name, "category": h.category,
                "image_url": h.image_url, "price": h.price,
            })
    grouped = {cat: items for cat, items in grouped.items() if items}
    # Не через jsonify: нужен порядок категорий (Серебро, потом Золото)
    resp = Response(json.dumps(grouped, ensure_ascii=False), mimetype="application/json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bead_bp.route("/api/orders/custom", methods=["POST"])
def create_custom_order():
    if _rate_limited():
        return _error("Слишком много заказов с вашего адреса. Попробуйте чуть позже.", 429)

    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return _error("Ожидался JSON-объект")

    clean, err = _validate_order(data)
    if err:
        return _error(err)

    session = SessionLocal()
    ids = clean["beads_ids"]

    beads = session.scalars(
        select(Bead).where(Bead.id.in_(set(ids)), Bead.is_active.is_(True))
    ).all()
    beads_by_id = {b.id: b for b in beads}

    missing = sorted(set(ids) - set(beads_by_id))
    if missing:
        return _error("Некоторые бусины закончились или были убраны. Обновите конструктор.")

    # Фурнитура (если выбрана) должна быть активной
    hardware = None
    if clean["hardware_id"] is not None:
        hardware = session.scalars(
            select(Hardware).where(Hardware.id == clean["hardware_id"], Hardware.is_active.is_(True))
        ).first()
        if hardware is None:
            return _error("Выбранная фурнитура недоступна. Обновите конструктор и выберите другую.")

    # Цену считаем на сервере: клиенту доверять нельзя
    server_total = round(
        BASE_PRICE + sum(beads_by_id[i].price for i in ids) + (hardware.price if hardware else 0), 2
    )
    if abs(server_total - clean["client_total"]) > PRICE_TOLERANCE:
        log.warning("Расхождение цены: клиент=%s, сервер=%s", clean["client_total"], server_total)
        return _error(
            f"Цены обновились, актуальная сумма: {_fmt_num(server_total)} ₽. "
            "Проверьте заказ и отправьте ещё раз.",
            status=409,
        )

    order = CustomOrder(
        customer_name=clean["customer_name"], customer_phone=clean["customer_phone"],
        delivery_type=clean["delivery_type"], address=clean["address"],
        comment=clean["comment"], beads_sequence=ids, total_price=server_total,
        customer_chat_id=clean["customer_chat_id"],
        customer_username=clean["customer_username"],
        jewelry_type=clean["jewelry_type"], size_cm=clean["size_cm"],
        hardware_id=hardware.id if hardware else None,
        hardware_name=hardware.name if hardware else None,
        hardware_category=hardware.category if hardware else None,
        hardware_price=hardware.price if hardware else None,
        status="new",
    )
    try:
        session.add(order)
        session.commit()
    except Exception:
        session.rollback()
        log.exception("Ошибка сохранения заказа")
        return _error("Не удалось сохранить заказ", 500)

    _notify_admins(_build_recipe(order, beads_by_id))
    return jsonify({"ok": True, "order_id": order.id, "total_price": server_total}), 201


# --- История заказов в профиле -----------------------------------------

def customer_madebar_orders(tg_id: int) -> list:
    """Заказы Made Bar покупателя в том же формате, что и обычные заказы магазина."""
    session = SessionLocal()
    orders = session.scalars(
        select(CustomOrder).where(CustomOrder.customer_chat_id == tg_id)
        .order_by(CustomOrder.id.desc())
    ).all()
    if not orders:
        return []

    all_ids = {i for o in orders for i in (o.beads_sequence or [])}
    beads = {b.id: b for b in session.scalars(select(Bead).where(Bead.id.in_(all_ids)))} if all_ids else {}

    result = []
    for o in orders:
        items = [{"name": "Основа (шнурок + застёжка)", "qty": 1}]
        for bead_id, qty in Counter(o.beads_sequence or []).items():
            b = beads.get(bead_id)
            items.append({"name": b.name if b else f"Бусина №{bead_id}", "qty": qty})
        if o.hardware_name:
            items.append({"name": f"Фурнитура: {o.hardware_name} ({o.hardware_category or ''})", "qty": 1})
        if _size_text(o):
            items.append({"name": f"Размер: {_size_text(o)}", "qty": 1})
        result.append({
            "id": o.id, "kind": "madebar", "number": f"MB-{o.id}",
            "created_at": o.to_dict()["created_at"],
            "status": o.status, "items": items,
            "discount": 0, "total": o.total_price,
        })
    return result


@bead_bp.route("/api/orders/custom/<int:order_id>/cancel", methods=["POST"])
def cancel_custom_order(order_id):
    """Покупатель отменяет свой заказ Made Bar, пока он в статусе «Новый»."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        tg_id = int(data.get("tg_id"))
    except (TypeError, ValueError):
        return _error("Некорректный tg_id")

    session = SessionLocal()
    order = session.get(CustomOrder, order_id)
    if not order:
        return _error("Заказ не найден", 404)
    if order.customer_chat_id != tg_id:
        return _error("Это не ваш заказ", 403)
    if order.status != "new":
        return _error("Заказ уже в обработке — отмена недоступна")

    order.status = "cancelled"
    try:
        session.commit()
    except Exception:
        session.rollback()
        log.exception("Ошибка отмены заказа Made Bar %s", order_id)
        return _error("Не удалось отменить заказ", 500)

    _notify_admins(f"❌ Клиент отменил заказ Made Bar #{order_id}")
    return jsonify({"id": order.id, "kind": "madebar", "status": order.status})


# --- Подключение --------------------------------------------------------

def register_bead_api(app, bot, admin_chat_ids):
    global _bot, _admin_chat_ids
    _bot = bot
    _admin_chat_ids = set(admin_chat_ids)
    app.register_blueprint(bead_bp)

    @app.teardown_appcontext
    def _shutdown_session(exc=None):
        SessionLocal.remove()
