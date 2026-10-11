"""Стартовый каталог Made Bar: заполняется один раз, когда таблица beads пустая.

Цены и размеры здесь примерные: поправьте их в админке (вкладка Made Bar).
Картинки рисуются на лету (bead_art.py), поэтому ничего загружать не нужно.
"""
import logging

from sqlalchemy import func, select

from bead_art import art_url
from models import Bead, SessionLocal

log = logging.getLogger("korolko.seed")

CYRILLIC = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"

# (название, kind, цвет, вариант, цена ₽, диаметр мм)
STONES = [
    ("Аметист",            "gem",   "9a6bd1", "sheen",   45, 8),
    ("Розовый кварц",      "gem",   "f2b3c6", "sheen",   40, 8),
    ("Лунный камень",      "gem",   "dfe7f0", "sheen",   55, 8),
    ("Бирюза",             "gem",   "3fb7b0", "veins",   50, 8),
    ("Лазурит",            "gem",   "2a4fa8", "flecks",  55, 8),
    ("Агат полосатый",     "gem",   "c9885a", "banded",  45, 8),
    ("Малахит",            "gem",   "2f8f5b", "banded",  55, 8),
    ("Тигровый глаз",      "gem",   "b07a2a", "stripes", 50, 8),
    ("Жемчуг барочный",    "pearl", "f4ece1", "baroque", 60, 9),
    ("Жемчуг белый",       "pearl", "faf6f0", "round",   45, 6),
]
SILVER = [
    ("Шарик серебро 4 мм",     "metal", "c9ced6", "shiny", 35, 4),
    ("Шарик серебро 6 мм",     "metal", "c9ced6", "shiny", 55, 6),
    ("Шарик матовый 6 мм",     "metal", "b9bec6", "matte", 55, 6),
    ("Бусина-кольцо",          "ring",  "c9ced6", "",      60, 8),
]
SPACERS = [
    ("Мини-шарик серебро 3 мм", "metal", "c9ced6", "shiny", 8,  3),
    ("Мини-шарик золото 3 мм",  "metal", "e0b45c", "shiny", 10, 3),
    ("Бисер розовый 3 мм",      "gem",   "f08db0", "plain", 5,  3),
    ("Бисер белый 3 мм",        "gem",   "fdf7f2", "plain", 5,  3),
]
CHARMS = [
    ("Подвеска «Сердце»",  "charm", "c9ced6", "heart",  80, 11),
    ("Подвеска «Звезда»",  "charm", "c9ced6", "star",   80, 11),
    ("Подвеска «Луна»",    "charm", "c9ced6", "moon",   80, 11),
    ("Подвеска «Цветок»",  "charm", "c9ced6", "flower", 90, 11),
]


def _rows():
    n = 0
    for category, items in (("Камни", STONES), ("Серебро", SILVER)):
        for name, kind, c, v, price, mm in items:
            n += 1
            yield dict(name=name, category=category, image_url=art_url(kind, c, v),
                       price=price, size_mm=mm, sort_order=n)
    for ch in CYRILLIC:
        n += 1
        yield dict(name=f"Буква {ch}", category="Буквы",
                   image_url=art_url("letter", "fff4f7", "", ch),
                   price=30, size_mm=8, sort_order=n)
    for category, items in (("Разделители", SPACERS), ("Подвески", CHARMS)):
        for name, kind, c, v, price, mm in items:
            n += 1
            yield dict(name=name, category=category, image_url=art_url(kind, c, v),
                       price=price, size_mm=mm, sort_order=n)


def seed_if_empty() -> int:
    session = SessionLocal()
    try:
        if session.scalar(select(func.count()).select_from(Bead)):
            return 0
        rows = list(_rows())
        session.add_all(Bead(is_active=True, **r) for r in rows)
        session.commit()
        log.info("Made Bar: добавлено %d стартовых бусин", len(rows))
        return len(rows)
    except Exception:
        session.rollback()
        raise
    finally:
        SessionLocal.remove()
