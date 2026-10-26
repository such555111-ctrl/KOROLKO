import os
import json
import uuid
import logging
from datetime import datetime, timezone

from flask import Flask, request, send_from_directory, abort, jsonify
import telebot
from telebot import types

import store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("korolko")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN не задана")

# Render автоматически прокидывает RENDER_EXTERNAL_URL для web-сервисов.
# Можно переопределить вручную через WEBHOOK_URL, если нужно.
BASE_URL = (os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")

# Пароль для входа в админ-панель (/admin). Задаётся в переменных окружения.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

# Telegram ID администратора(ов). Только этим пользователям бот присылает
# ссылку на админ-панель по команде /start. Можно указать несколько ID
# через запятую: "111111,222222".
ADMIN_CHAT_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_CHAT_ID", "").split(",")
    if x.strip().lstrip("-").isdigit()
}

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(APP_DIR, "products.json")
ORDERS_FILE = os.path.join(APP_DIR, "orders.json")
QUESTIONS_FILE = os.path.join(APP_DIR, "questions.json")
PROMOS_FILE = os.path.join(APP_DIR, "promos.json")
UPLOAD_DIR = os.path.join(APP_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp", ".gif": "image/gif"}
ALLOWED_BADGES = {"hit", "trend", "sale", "instock"}
ALLOWED_PROMO_TYPES = {"percent", "fixed"}

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 МБ на файл


# --- Вспомогательные функции для товаров и авторизации админа -----------

def require_admin():
    if not ADMIN_PASSWORD:
        abort(403, description="ADMIN_PASSWORD не задан на сервере")
    supplied = request.headers.get("X-Admin-Password", "")
    if supplied != ADMIN_PASSWORD:
        abort(401, description="Неверный пароль")


def order_products(items):
    """Возвращает товары в заданном порядке (по sort_order).

    Товары без sort_order (старые записи) сохраняют своё текущее место и
    остаются перед новыми; при равных значениях порядок стабильный.
    """
    def key(pair):
        idx, p = pair
        try:
            so = int(p.get("sort_order"))
        except (TypeError, ValueError):
            so = None
        return (so is None, so if so is not None else 0, idx)
    return [p for _, p in sorted(enumerate(items), key=key)]


def renumber_products(items):
    """Выставляет sort_order = 1..N по текущему порядку списка."""
    for i, p in enumerate(items, 1):
        p["sort_order"] = i
    return items


def move_product(items, product, position):
    """Ставит товар на позицию position (1..N), остальные сдвигаются."""
    items = [p for p in items if p is not product]
    try:
        pos = int(position)
    except (TypeError, ValueError):
        pos = len(items) + 1
    pos = max(1, min(pos, len(items) + 1))
    items.insert(pos - 1, product)
    return renumber_products(items)


def load_products():
    return order_products(store.load_doc("products"))


def save_products(items):
    store.save_doc("products", items)


def load_orders():
    return store.load_doc("orders")


def save_orders(items):
    store.save_doc("orders", items)


def load_questions():
    return store.load_doc("questions")


def save_questions(items):
    store.save_doc("questions", items)


def load_promos():
    return store.load_doc("promos")


def save_promos(items):
    store.save_doc("promos", items)


# --- Контакты («Связаться с нами») ----------------------------------------

DEFAULT_CONTACTS = {
    "telegram": "https://t.me/ANTONINAKOROLKO",
    "instagram": "https://www.instagram.com/antonina.korolko?rpxt=MW83dG55emcwbWN6&utm_source=qr",
    "tiktok": "https://www.tiktok.com/@korolko?_r=1&_t=ZS-9ApRVVp66Eu",
}


def normalize_contact(kind, raw):
    """Приводит ввод (@ник, t.me/ник, ссылка) к https-ссылке. Пустая строка = скрыть кнопку.
    Бросает ValueError, если ввод не похож на ссылку."""
    import re
    v = (raw or "").strip()
    if not v:
        return ""
    hosts = {
        "telegram": ("t.me", "telegram.me", "telegram.dog"),
        "instagram": ("instagram.com", "instagr.am"),
        "tiktok": ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"),
    }[kind]
    base = {"telegram": "https://t.me/", "instagram": "https://www.instagram.com/",
            "tiktok": "https://www.tiktok.com/@"}[kind]
    if re.fullmatch(r"@?[A-Za-z0-9._]{2,64}", v):
        return base + v.lstrip("@")
    if re.match(r"(?i)^(https?://)?([\w-]+\.)*(" + "|".join(re.escape(h) for h in hosts) + r")(/|$|\?)", v):
        if not re.match(r"(?i)^https?://", v):
            v = "https://" + v
        v = re.sub(r"(?i)^http://", "https://", v)
        if re.search(r"[\s<>\"']", v) or len(v) > 500:
            raise ValueError("Некорректная ссылка")
        return v
    names = {"telegram": "Telegram", "instagram": "Instagram", "tiktok": "TikTok"}
    raise ValueError(f"{names[kind]}: введите @ник или ссылку на {hosts[0]}")


def load_contacts():
    saved = store.load_doc("contacts")
    data = saved[0] if saved and isinstance(saved[0], dict) else {}
    # ключ есть (даже пустой) — берём сохранённое; нет ключа — значение по умолчанию
    return {k: (data[k] if k in data else DEFAULT_CONTACTS[k]) for k in DEFAULT_CONTACTS}


def fmt_price(n):
    return f"{int(n):,}".replace(",", " ") + " ₽"


def sanitize_images(raw):
    if isinstance(raw, list):
        return [str(u).strip() for u in raw if isinstance(u, (str,)) and str(u).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def sanitize_badge(raw):
    b = str(raw or "").strip().lower()
    return b if b in ALLOWED_BADGES else None


def sanitize_old_price(raw):
    if raw in (None, ""):
        return None
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


# --- Промокоды -----------------------------------------------------------

def sanitize_positive_int(raw):
    """Возвращает положительное целое либо None, если значение не задано/некорректно."""
    if raw in (None, ""):
        return None
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def find_promo_by_code(code):
    code_norm = (code or "").strip().upper()
    if not code_norm:
        return None
    promos = load_promos()
    return next((p for p in promos if p.get("code") == code_norm), None)


def validate_promo_for_use(promo, subtotal, customer_chat_id=None):
    """Проверяет, что промокод можно применить к заказу на сумму subtotal.
    Возвращает текст ошибки либо None, если всё в порядке."""
    if not promo:
        return "Промокод не найден"
    if not promo.get("active", True):
        return "Промокод больше не активен"
    max_uses = promo.get("max_uses")
    if max_uses is not None and int(promo.get("used_count") or 0) >= int(max_uses):
        return "Лимит использований промокода исчерпан"
    min_total = promo.get("min_total")
    if min_total is not None and subtotal < int(min_total):
        return f"Промокод действует от суммы заказа {fmt_price(min_total)}"
    if customer_chat_id and has_customer_used_promo(promo.get("code"), customer_chat_id):
        return "Вы уже использовали этот промокод"
    return None


def has_customer_used_promo(promo_code, customer_chat_id):
    """Проверяет, применял ли этот покупатель данный промокод ранее
    (отменённые заказы не считаются использованием)."""
    if not promo_code or not customer_chat_id:
        return False
    orders = load_orders()
    return any(
        o.get("promo_code") == promo_code
        and o.get("customer_chat_id") == customer_chat_id
        and o.get("status") != "cancelled"
        for o in orders
    )


def compute_promo_discount(promo, subtotal):
    if promo.get("type") == "percent":
        discount = subtotal * float(promo.get("value") or 0) / 100.0
    else:
        discount = float(promo.get("value") or 0)
    discount = max(0.0, min(discount, subtotal))
    return int(round(discount))


# --- Telegram handlers -------------------------------------------------

WELCOME_TEXT = (
    "<b>🍇 Добро пожаловать в KOROLKO!</b>\n"
    "\n"
    "Тут вы можете ознакомиться с нашим ассортиментом и оформить заказ💗"
)

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    if not BASE_URL:
        bot.send_message(
            message.chat.id,
            "KOROLKO: мини-апп ещё не сконфигурирован (не задан WEBHOOK_URL)."
        )
        return

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            text="👑 Открыть KOROLKO",
            web_app=types.WebAppInfo(url=BASE_URL + "/")
        )
    )

    keyboard.add(
        types.InlineKeyboardButton(
            text="🧵 Собрать украшение (Made Bar)",
            web_app=types.WebAppInfo(url=BASE_URL + "/madebar")
        )
    )

    is_admin = bool(message.from_user) and message.from_user.id in ADMIN_CHAT_IDS
    if is_admin:
        keyboard.add(
            types.InlineKeyboardButton(
                text="Панель администратора",
                web_app=types.WebAppInfo(url=BASE_URL + "/admin")
            )
        )

    bot.send_message(
        message.chat.id,
        WELCOME_TEXT,
        reply_markup=keyboard,
        parse_mode="HTML",
    )



@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(message: types.Message):
    cmd_start(message)


# --- Flask routes --------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.route("/products.json", methods=["GET"])
def products():
    resp = jsonify(load_products())
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/contacts", methods=["GET"])
def public_contacts():
    resp = jsonify(load_contacts())
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/order", methods=["POST"])
def create_order():
    data = request.get_json(force=True, silent=True) or {}

    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    comment = (data.get("comment") or "").strip()
    items = data.get("items") or []

    if not name or not phone:
        abort(400, description="Укажите имя и телефон")
    if not isinstance(items, list) or not items:
        abort(400, description="Корзина пуста")

    subtotal = sum(int(i.get("price") or 0) * int(i.get("qty") or 1) for i in items)

    tg_user = data.get("tg_user") or {}
    try:
        customer_chat_id = int(tg_user.get("id")) if tg_user.get("id") else None
    except (TypeError, ValueError):
        customer_chat_id = None
    customer_username = (tg_user.get("username") or "").strip() or None

    promo_code_raw = (data.get("promo_code") or "").strip()
    promo = None
    discount = 0
    if promo_code_raw:
        promo = find_promo_by_code(promo_code_raw)
        promo_err = validate_promo_for_use(promo, subtotal, customer_chat_id)
        if promo_err:
            abort(400, description=promo_err)
        discount = compute_promo_discount(promo, subtotal)

    total = subtotal - discount

    orders = load_orders()
    new_id = (max([o.get("id", 0) for o in orders], default=0) + 1)
    order = {
        "id": new_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "phone": phone,
        "comment": comment,
        "items": items,
        "subtotal": subtotal,
        "discount": discount,
        "promo_code": promo.get("code") if promo else None,
        "total": total,
        "customer_chat_id": customer_chat_id,
        "customer_username": customer_username,
        "status": "new",
        "messages": [],
    }
    orders.append(order)
    save_orders(orders)

    if promo:
        promos = load_promos()
        for p in promos:
            if p.get("id") == promo.get("id"):
                p["used_count"] = int(p.get("used_count") or 0) + 1
        save_promos(promos)

    items_lines = "\n".join(
        f"· {i.get('name','—')} (размер {i.get('size') or '—'}) × {i.get('qty', 1)}"
        for i in items
    )

    discount_line = ""
    if discount > 0:
        discount_line = (
            f"Сумма товаров: {fmt_price(subtotal)}\n"
            f"Промокод: {promo.get('code')} (−{fmt_price(discount)})\n"
        )

    if customer_chat_id:
        try:
            bot.send_message(
                customer_chat_id,
                "✅ Заказ оформлен!\n\n"
                f"Номер заказа: KR-{new_id}\n"
                f"{items_lines}\n\n"
                f"{discount_line}"
                f"Итого: {fmt_price(total)}\n\n"
                "Мы свяжемся с вами в этом чате, чтобы подтвердить заказ и "
                "обсудить доставку."
            )
        except Exception as e:
            log.warning("Не удалось отправить подтверждение покупателю: %s", e)

    admin_text = (
        f"🛒 Новый заказ KR-{new_id}\n\n"
        f"Клиент: {name}\n"
        f"Телефон: {phone}\n"
        + (f"Telegram: @{customer_username}\n" if customer_username else "")
        + f"\n{items_lines}\n\n"
        f"{discount_line}"
        f"Итого: {fmt_price(total)}\n"
        + (f"\nКомментарий: {comment}\n" if comment else "")
        + "\nОткройте панель администратора (вкладка «Заказы»), чтобы "
          "написать клиенту и обсудить доставку."
    )
    for admin_id in ADMIN_CHAT_IDS:
        try:
            bot.send_message(admin_id, admin_text)
        except Exception as e:
            log.warning("Не удалось уведомить администратора %s: %s", admin_id, e)

    return jsonify({"ok": True, "order_id": new_id}), 201


@app.route("/api/promo/check", methods=["POST"])
def check_promo():
    """Проверка промокода из корзины (до оформления заказа)."""
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip()
    try:
        subtotal = int(float(data.get("subtotal") or 0))
    except (TypeError, ValueError):
        subtotal = 0
    tg_user = data.get("tg_user") or {}
    try:
        customer_chat_id = int(tg_user.get("id")) if tg_user.get("id") else None
    except (TypeError, ValueError):
        customer_chat_id = None
    if not code:
        abort(400, description="Введите промокод")

    promo = find_promo_by_code(code)
    err = validate_promo_for_use(promo, subtotal, customer_chat_id)
    if err:
        abort(400, description=err)

    discount = compute_promo_discount(promo, subtotal)
    return jsonify({
        "ok": True,
        "code": promo.get("code"),
        "type": promo.get("type"),
        "value": promo.get("value"),
        "discount": discount,
        "new_total": subtotal - discount,
    })


@app.route("/api/orders", methods=["GET"])
def customer_orders():
    """История заказов покупателя (по его Telegram ID)."""
    try:
        tg_id = int(request.args.get("tg_id", ""))
    except (TypeError, ValueError):
        abort(400, description="Некорректный tg_id")
    orders = load_orders()
    mine = [o for o in orders if o.get("customer_chat_id") == tg_id]
    # Заказы конструктора Made Bar лежат в таблице custom_orders: добавляем их в историю
    try:
        from bead_api import customer_madebar_orders
        mine += customer_madebar_orders(tg_id)
    except Exception:
        log.exception("Не удалось загрузить заказы Made Bar для профиля")
    mine.sort(key=lambda o: o.get("created_at") or "", reverse=True)
    return jsonify(mine)


@app.route("/api/orders/<int:oid>/cancel", methods=["POST"])
def customer_cancel_order(oid):
    """Покупатель отменяет свой заказ, если он ещё не взят в обработку."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        tg_id = int(data.get("tg_id"))
    except (TypeError, ValueError):
        abort(400, description="Некорректный tg_id")

    orders = load_orders()
    order = next((o for o in orders if o.get("id") == oid), None)
    if not order:
        abort(404, description="Заказ не найден")
    if order.get("customer_chat_id") != tg_id:
        abort(403, description="Это не ваш заказ")
    if order.get("status") != "new":
        abort(400, description="Заказ уже в обработке — отмена недоступна")

    order["status"] = "cancelled"
    save_orders(orders)

    for admin_id in ADMIN_CHAT_IDS:
        try:
            bot.send_message(admin_id, f"❌ Клиент отменил заказ KR-{oid}")
        except Exception as e:
            log.warning("Не удалось уведомить администратора %s: %s", admin_id, e)

    return jsonify(order)


@app.route("/api/question", methods=["POST"])
def create_question():
    data = request.get_json(force=True, silent=True) or {}

    question = (data.get("question") or "").strip()
    product_name = (data.get("product_name") or "").strip()
    try:
        product_id = int(data.get("product_id"))
    except (TypeError, ValueError):
        product_id = None

    if not question:
        abort(400, description="Введите вопрос")

    tg_user = data.get("tg_user") or {}
    try:
        customer_chat_id = int(tg_user.get("id")) if tg_user.get("id") else None
    except (TypeError, ValueError):
        customer_chat_id = None
    customer_username = (tg_user.get("username") or "").strip() or None

    questions = load_questions()
    new_id = (max([q.get("id", 0) for q in questions], default=0) + 1)
    entry = {
        "id": new_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "product_id": product_id,
        "product_name": product_name,
        "question": question,
        "customer_chat_id": customer_chat_id,
        "customer_username": customer_username,
        "status": "new",
        "messages": [],
    }
    questions.append(entry)
    save_questions(questions)

    if customer_chat_id:
        try:
            bot.send_message(
                customer_chat_id,
                "✅ Ваш вопрос отправлен администратору.\n\n"
                f"Товар: {product_name}\n"
                f"Вопрос: {question}\n\n"
                "Мы ответим вам в этом чате."
            )
        except Exception as e:
            log.warning("Не удалось отправить подтверждение по вопросу: %s", e)

    admin_text = (
        f"❓ Новый вопрос по товару «{product_name}»\n\n"
        + (f"От: @{customer_username}\n\n" if customer_username else "\n")
        + f"{question}\n\n"
        "Откройте панель администратора (вкладка «Вопросы»), чтобы ответить клиенту."
    )
    for admin_id in ADMIN_CHAT_IDS:
        try:
            bot.send_message(admin_id, admin_text)
        except Exception as e:
            log.warning("Не удалось уведомить администратора %s: %s", admin_id, e)

    return jsonify({"ok": True, "question_id": new_id}), 201


@app.route("/uploads/<path:filename>", methods=["GET"])
def uploaded_file(filename):
    # Фото хранятся в базе (переживают деплой); диск — запасной вариант
    found = store.get_upload(filename)
    if found:
        data, content_type = found
        resp = app.response_class(data, mimetype=content_type)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/admin", methods=["GET"])
def admin_page():
    return send_from_directory(APP_DIR, "admin.html")


@app.route("/healthz", methods=["GET"])
def healthz():
    return {"status": "ok"}


# --- Admin API -------------------------------------------------------

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    require_admin()
    return jsonify({"ok": True})


@app.route("/api/admin/products", methods=["GET"])
def admin_get_products():
    require_admin()
    return jsonify(load_products())


@app.route("/api/admin/products", methods=["POST"])
def admin_create_product():
    require_admin()
    data = request.get_json(force=True, silent=True) or {}
    items = load_products()
    new_id = (max([p.get("id", 0) for p in items], default=0) + 1)
    sizes_raw = data.get("sizes", "")
    sizes = [s.strip() for s in sizes_raw.split(",") if s.strip()] if isinstance(sizes_raw, str) else (sizes_raw or [])
    try:
        price = int(float(data.get("price") or 0))
    except (TypeError, ValueError):
        price = 0
    images = sanitize_images(data.get("images") if "images" in data else data.get("image"))
    product = {
        "id": new_id,
        "name": (data.get("name") or "").strip(),
        "category": (data.get("category") or "").strip(),
        "price": price,
        "old_price": sanitize_old_price(data.get("old_price")),
        "badge": sanitize_badge(data.get("badge")),
        "sizes": sizes or ["One size"],
        "swatch": int(data.get("swatch") or 0) % 6,
        "desc": (data.get("desc") or "").strip(),
        "images": images,
        "image": images[0] if images else None,
    }
    # Новый товар по умолчанию встаёт в конец — текущий порядок не ломается.
    # Если админ указал позицию, вставляем товар на неё.
    items.append(product)
    raw_pos = data.get("sort_order")
    if raw_pos in (None, ""):
        raw_pos = len(items)
    items = move_product(items, product, raw_pos)
    save_products(items)
    return jsonify(product), 201


@app.route("/api/admin/products/<int:pid>", methods=["PUT"])
def admin_update_product(pid):
    require_admin()
    data = request.get_json(force=True, silent=True) or {}
    items = load_products()
    for p in items:
        if p.get("id") == pid:
            if "name" in data:
                p["name"] = (data.get("name") or "").strip()
            if "category" in data:
                p["category"] = (data.get("category") or "").strip()
            if "price" in data:
                try:
                    p["price"] = int(float(data.get("price") or 0))
                except (TypeError, ValueError):
                    pass
            if "old_price" in data:
                p["old_price"] = sanitize_old_price(data.get("old_price"))
            if "badge" in data:
                p["badge"] = sanitize_badge(data.get("badge"))
            if "sizes" in data:
                sizes_raw = data.get("sizes", "")
                p["sizes"] = [s.strip() for s in sizes_raw.split(",") if s.strip()] if isinstance(sizes_raw, str) else (sizes_raw or p["sizes"])
            if "desc" in data:
                p["desc"] = (data.get("desc") or "").strip()
            if "swatch" in data:
                p["swatch"] = int(data.get("swatch") or 0) % 6
            if "images" in data:
                images = sanitize_images(data.get("images"))
                p["images"] = images
                p["image"] = images[0] if images else None
            elif "image" in data:
                images = sanitize_images(data.get("image"))
                p["images"] = images
                p["image"] = images[0] if images else None
            if data.get("sort_order") not in (None, ""):
                items = move_product(items, p, data.get("sort_order"))
            else:
                items = renumber_products(items)
            save_products(items)
            return jsonify(p)
    abort(404, description="Товар не найден")


@app.route("/api/admin/products/<int:pid>/move", methods=["POST"])
def admin_move_product(pid):
    """Кнопки «выше / ниже» (up / down), а также top / bottom."""
    require_admin()
    data = request.get_json(force=True, silent=True) or {}
    direction = data.get("direction")
    if direction not in ("up", "down", "top", "bottom"):
        abort(400, description="Неверное направление")
    items = load_products()
    idx = next((i for i, p in enumerate(items) if p.get("id") == pid), None)
    if idx is None:
        abort(404, description="Товар не найден")
    target = {
        "up": max(0, idx - 1),
        "down": min(len(items) - 1, idx + 1),
        "top": 0,
        "bottom": len(items) - 1,
    }[direction]
    items.insert(target, items.pop(idx))
    renumber_products(items)
    save_products(items)
    return jsonify(items)


@app.route("/api/admin/products/<int:pid>", methods=["DELETE"])
def admin_delete_product(pid):
    require_admin()
    items = load_products()
    new_items = [p for p in items if p.get("id") != pid]
    if len(new_items) == len(items):
        abort(404, description="Товар не найден")
    save_products(renumber_products(new_items))
    return jsonify({"ok": True})


@app.route("/api/admin/upload", methods=["POST"])
def admin_upload():
    require_admin()
    file = request.files.get("file")
    if not file or not file.filename:
        abort(400, description="Файл не передан")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        abort(400, description="Недопустимый формат файла")
    filename = f"{uuid.uuid4().hex}{ext}"
    data = file.read()
    with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
        f.write(data)
    store.save_upload(filename, data, IMAGE_MIME.get(ext, "application/octet-stream"))
    return jsonify({"url": f"/uploads/{filename}"}), 201


@app.route("/api/admin/contacts", methods=["GET"])
def admin_get_contacts():
    require_admin()
    return jsonify(load_contacts())


@app.route("/api/admin/contacts", methods=["PUT"])
def admin_put_contacts():
    require_admin()
    body = request.get_json(silent=True) or {}
    result = {}
    for kind in DEFAULT_CONTACTS:
        try:
            result[kind] = normalize_contact(kind, body.get(kind, ""))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    store.save_doc("contacts", [result])
    return jsonify(result)


@app.route("/api/admin/promos", methods=["GET"])
def admin_get_promos():
    require_admin()
    promos = load_promos()
    promos.sort(key=lambda p: p.get("id", 0), reverse=True)
    return jsonify(promos)


@app.route("/api/admin/promos", methods=["POST"])
def admin_create_promo():
    require_admin()
    data = request.get_json(force=True, silent=True) or {}

    code = (data.get("code") or "").strip().upper()
    if not code:
        abort(400, description="Укажите код промокода")

    promo_type = (data.get("type") or "").strip().lower()
    if promo_type not in ALLOWED_PROMO_TYPES:
        abort(400, description="Некорректный тип скидки")

    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        abort(400, description="Укажите значение скидки")
    if value <= 0:
        abort(400, description="Значение скидки должно быть больше нуля")
    if promo_type == "percent" and value > 100:
        abort(400, description="Скидка в процентах не может быть больше 100")

    promos = load_promos()
    if any(p.get("code") == code for p in promos):
        abort(400, description="Такой промокод уже существует")

    new_id = (max([p.get("id", 0) for p in promos], default=0) + 1)
    promo = {
        "id": new_id,
        "code": code,
        "type": promo_type,
        "value": value,
        "active": bool(data.get("active", True)),
        "max_uses": sanitize_positive_int(data.get("max_uses")),
        "used_count": 0,
        "min_total": sanitize_positive_int(data.get("min_total")),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    promos.append(promo)
    save_promos(promos)
    return jsonify(promo), 201


@app.route("/api/admin/promos/<int:pid>", methods=["PUT"])
def admin_update_promo(pid):
    require_admin()
    data = request.get_json(force=True, silent=True) or {}
    promos = load_promos()
    for p in promos:
        if p.get("id") == pid:
            if "code" in data:
                code = (data.get("code") or "").strip().upper()
                if not code:
                    abort(400, description="Укажите код промокода")
                if any(o.get("code") == code and o.get("id") != pid for o in promos):
                    abort(400, description="Такой промокод уже существует")
                p["code"] = code
            if "type" in data:
                promo_type = (data.get("type") or "").strip().lower()
                if promo_type not in ALLOWED_PROMO_TYPES:
                    abort(400, description="Некорректный тип скидки")
                p["type"] = promo_type
            if "value" in data:
                try:
                    value = float(data.get("value"))
                except (TypeError, ValueError):
                    abort(400, description="Укажите значение скидки")
                if value <= 0:
                    abort(400, description="Значение скидки должно быть больше нуля")
                if p.get("type") == "percent" and value > 100:
                    abort(400, description="Скидка в процентах не может быть больше 100")
                p["value"] = value
            if "active" in data:
                p["active"] = bool(data.get("active"))
            if "max_uses" in data:
                p["max_uses"] = sanitize_positive_int(data.get("max_uses"))
            if "min_total" in data:
                p["min_total"] = sanitize_positive_int(data.get("min_total"))
            save_promos(promos)
            return jsonify(p)
    abort(404, description="Промокод не найден")


@app.route("/api/admin/promos/<int:pid>", methods=["DELETE"])
def admin_delete_promo(pid):
    require_admin()
    promos = load_promos()
    new_promos = [p for p in promos if p.get("id") != pid]
    if len(new_promos) == len(promos):
        abort(404, description="Промокод не найден")
    save_promos(new_promos)
    return jsonify({"ok": True})


@app.route("/api/admin/orders", methods=["GET"])
def admin_get_orders():
    require_admin()
    orders = load_orders()
    orders.sort(key=lambda o: o.get("id", 0), reverse=True)
    return jsonify(orders)


@app.route("/api/admin/orders/<int:oid>/status", methods=["PUT"])
def admin_update_order_status(oid):
    require_admin()
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get("status") or "").strip()
    if status not in {"new", "contacted", "done", "cancelled"}:
        abort(400, description="Некорректный статус")
    orders = load_orders()
    for o in orders:
        if o.get("id") == oid:
            o["status"] = status
            save_orders(orders)
            return jsonify(o)
    abort(404, description="Заказ не найден")


@app.route("/api/admin/orders/<int:oid>/message", methods=["POST"])
def admin_message_customer(oid):
    require_admin()
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        abort(400, description="Введите текст сообщения")

    orders = load_orders()
    order = next((o for o in orders if o.get("id") == oid), None)
    if not order:
        abort(404, description="Заказ не найден")

    chat_id = order.get("customer_chat_id")
    if not chat_id:
        abort(400, description="У этого заказа нет чата с покупателем в Telegram")

    try:
        bot.send_message(
            chat_id,
            f"💬 Сообщение от KOROLKO по заказу KR-{oid}:\n\n{text}"
        )
    except Exception as e:
        abort(400, description=f"Не удалось отправить сообщение: {e}")

    order.setdefault("messages", []).append({
        "text": text,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    if order.get("status") == "new":
        order["status"] = "contacted"
    save_orders(orders)
    return jsonify(order)


@app.route("/api/admin/questions", methods=["GET"])
def admin_get_questions():
    require_admin()
    questions = load_questions()
    questions.sort(key=lambda q: q.get("id", 0), reverse=True)
    return jsonify(questions)


@app.route("/api/admin/questions/<int:qid>/message", methods=["POST"])
def admin_message_question(qid):
    require_admin()
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        abort(400, description="Введите текст ответа")

    questions = load_questions()
    q = next((x for x in questions if x.get("id") == qid), None)
    if not q:
        abort(404, description="Вопрос не найден")

    chat_id = q.get("customer_chat_id")
    if not chat_id:
        abort(400, description="У этого вопроса нет чата с покупателем в Telegram")

    try:
        bot.send_message(
            chat_id,
            f"💬 Ответ от KOROLKO по товару «{q.get('product_name','')}»:\n\n{text}"
        )
    except Exception as e:
        abort(400, description=f"Не удалось отправить ответ: {e}")

    q.setdefault("messages", []).append({
        "text": text,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    q["status"] = "answered"
    save_questions(questions)
    return jsonify(q)


@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def handle_api_errors(err):
    if request.path.startswith("/api/"):
        return jsonify({"error": getattr(err, "description", str(err))}), err.code
    return err


def setup_webhook():
    if not BASE_URL:
        log.warning("WEBHOOK_URL / RENDER_EXTERNAL_URL не заданы — вебхук не установлен.")
        return
    url = f"{BASE_URL}/webhook/{BOT_TOKEN}"
    bot.remove_webhook()
    bot.set_webhook(url=url)
    log.info("Webhook установлен: %s", url)


# --- Made Bar (конструктор украшений) ------------------------------------
from madebar import setup_madebar  # noqa: E402
setup_madebar(app, bot, ADMIN_CHAT_IDS, require_admin, UPLOAD_DIR)

setup_webhook()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
