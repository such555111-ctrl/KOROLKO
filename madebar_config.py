"""Настройки конструктора Made Bar. Меняются через переменные окружения на Render."""
import os


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


# Стоимость основы (шнурок + застёжка), ₽. Одно число на весь проект:
# его читает и сервер (проверка суммы), и страница конструктора.
BASE_PRICE = _num("MADEBAR_BASE_PRICE", 700)

# Максимум бусин на одной нити.
MAX_BEADS = int(_num("MADEBAR_MAX_BEADS", 30))

# Не более N заказов с одного IP за окно времени (защита от спама).
RATE_LIMIT_ORDERS = int(_num("MADEBAR_RATE_LIMIT", 6))
RATE_LIMIT_WINDOW_SEC = 600

# Типы украшений и доступные размеры (см). Одно место на весь проект:
# отсюда берут данные и сервер (проверка заказа), и страница конструктора.
JEWELRY_TYPES = {
    "bracelet": {"label": "Браслет", "sizes": [15, 17, 20]},
    "necklace": {"label": "Колье",   "sizes": [35, 41]},
}

# Категории фурнитуры (порядок = порядок вкладок в конструкторе и в админке).
HARDWARE_CATEGORIES = ["Серебро", "Золото"]
