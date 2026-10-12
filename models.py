import logging
import os
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Float, Integer, LargeBinary, String, Text,
    create_engine, inspect, text,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, scoped_session, sessionmaker,
)

log = logging.getLogger("korolko.db")

# --- Подключение --------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///beadbar.db")

# Render отдаёт "postgres://", а SQLAlchemy 2.x требует "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL.startswith("sqlite") and os.environ.get("RENDER"):
    log.warning(
        "DATABASE_URL не задан: заказы Made Bar хранятся в SQLite и ПРОПАДУТ "
        "при следующем деплое. Подключите PostgreSQL (см. README)."
    )

# Neon усыпляет неактивную базу и закрывает старые соединения:
# pool_pre_ping проверяет соединение перед использованием, pool_recycle обновляет его.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300, future=True)
SessionLocal = scoped_session(
    sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt):
    """ISO-строка с явной UTC-зоной (SQLite теряет tzinfo при чтении)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class Base(DeclarativeBase):
    pass


# --- Модели -------------------------------------------------------------
class Bead(Base):
    """Бусина в каталоге конструктора."""
    __tablename__ = "beads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Диаметр в миллиметрах: от него зависит размер бусины на нити
    size_mm: Mapped[float] = mapped_column(Float, nullable=False, default=8.0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "image_url": self.image_url,
            "price": self.price,
            "is_active": self.is_active,
            "sort_order": self.sort_order,
            "size_mm": self.size_mm,
        }


class CustomOrder(Base):
    """Заказ собранного украшения."""
    __tablename__ = "custom_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_type: Mapped[str] = mapped_column(String(20), nullable=False)  # «Доставка» / «Самовывоз»
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Кто заказал в Telegram: по этому ID заказ показывается в профиле покупателя
    customer_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    customer_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Массив ID бусин по порядку слева направо, напр. [3, 7, 7, 12]
    beads_sequence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    total_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")  # new / done / cancelled
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "customer_name": self.customer_name,
            "customer_phone": self.customer_phone,
            "delivery_type": self.delivery_type,
            "address": self.address,
            "comment": self.comment,
            "customer_chat_id": self.customer_chat_id,
            "customer_username": self.customer_username,
            "beads_sequence": self.beads_sequence,
            "total_price": self.total_price,
            "status": self.status,
            "created_at": _iso(self.created_at),
        }


class StoreDoc(Base):
    """Документы магазина: товары, заказы, промокоды, вопросы (раньше — JSON-файлы)."""
    __tablename__ = "store_docs"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    data: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class UploadedFile(Base):
    """Загруженные из админки фото: лежат в базе, а не на диске контейнера."""
    __tablename__ = "uploaded_files"

    filename: Mapped[str] = mapped_column(String(120), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(60), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


# --- Создание таблиц ----------------------------------------------------
# Колонки, добавленные позже: create_all их не дописывает в уже
# существующие таблицы, поэтому дописываем сами (безопасно, идемпотентно).
_LATE_COLUMNS = {
    "beads": {"size_mm": "FLOAT DEFAULT 8.0"},
    "custom_orders": {
        "comment": "TEXT",
        "status": "VARCHAR(20) DEFAULT 'new'",
        "customer_chat_id": "BIGINT",
        "customer_username": "VARCHAR(64)",
    },
}


def _ensure_columns() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in _LATE_COLUMNS.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    log.info("Добавлена колонка %s.%s", table, name)


def init_db() -> None:
    """Создаёт таблицы, если их ещё нет (безопасно вызывать при каждом старте)."""
    Base.metadata.create_all(bind=engine)
    _ensure_columns()


if __name__ == "__main__":
    init_db()
    print("Таблицы готовы.")
