"""Хранилище магазина: товары, заказы, промокоды, вопросы и загруженные фото.

Если задан DATABASE_URL (Neon / любой PostgreSQL) — всё лежит в базе и переживает
деплои. Если не задан (локальный запуск) — работает по-старому, через JSON-файлы.
"""
import json
import logging
import os
import threading

log = logging.getLogger("korolko.store")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
USE_DB = bool(os.environ.get("DATABASE_URL"))

_init_lock = threading.Lock()
_ready = False


def _db():
    """Ленивый импорт: без DATABASE_URL базу вообще не трогаем."""
    global _ready
    import models
    if not _ready:
        with _init_lock:
            if not _ready:
                models.init_db()
                _ready = True
    return models


# --- JSON-документы -----------------------------------------------------

def _file_path(name: str) -> str:
    return os.path.join(APP_DIR, f"{name}.json")


def _read_file(name: str):
    path = _file_path(name)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_doc(name: str):
    """Читает документ (список). Первый раз заполняется из одноимённого JSON-файла
    в репозитории (так в пустую базу попадают стартовые товары)."""
    if not USE_DB:
        return _read_file(name)

    models = _db()
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    with Session(models.engine) as s:
        row = s.get(models.StoreDoc, name)
        if row is not None:
            return row.data
        seed = _read_file(name)
        try:
            s.add(models.StoreDoc(name=name, data=seed))
            s.commit()
        except IntegrityError:   # параллельный запрос успел раньше
            s.rollback()
            row = s.get(models.StoreDoc, name)
            return row.data if row else seed
        return seed


def save_doc(name: str, items) -> None:
    if not USE_DB:
        with open(_file_path(name), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        return

    models = _db()
    from sqlalchemy.orm import Session

    with Session(models.engine) as s:
        row = s.get(models.StoreDoc, name)
        if row is None:
            s.add(models.StoreDoc(name=name, data=items))
        else:
            row.data = items
        s.commit()


# --- Загруженные фото ---------------------------------------------------

def save_upload(filename: str, data: bytes, content_type: str) -> None:
    if not USE_DB:
        return
    models = _db()
    from sqlalchemy.orm import Session

    with Session(models.engine) as s:
        row = s.get(models.UploadedFile, filename)
        if row is None:
            s.add(models.UploadedFile(filename=filename, content_type=content_type, data=data))
        else:
            row.content_type, row.data = content_type, data
        s.commit()


def get_upload(filename: str):
    """Возвращает (bytes, content_type) или None."""
    if not USE_DB:
        return None
    models = _db()
    from sqlalchemy.orm import Session

    with Session(models.engine) as s:
        row = s.get(models.UploadedFile, filename)
        return (row.data, row.content_type) if row else None


def delete_upload(filename: str) -> None:
    if not USE_DB:
        return
    models = _db()
    from sqlalchemy.orm import Session

    with Session(models.engine) as s:
        row = s.get(models.UploadedFile, filename)
        if row is not None:
            s.delete(row)
            s.commit()
