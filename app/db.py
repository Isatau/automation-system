"""Инфраструктура БД: engine, session, серверное время (Asia/Almaty)."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import create_engine, event, Numeric, DateTime, Text, JSON, TypeDecorator
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column

from .config import settings

ALMATY = timezone(timedelta(hours=5))  # Asia/Almaty (UTC+5, с 2024 единый часовой пояс РК)


def now_almaty() -> datetime:
    """Серверное время для аудита и документов."""
    return datetime.now(ALMATY).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


class JSONText(TypeDecorator):
    """JSON, совместимый с SQLite (через Text)."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        import json
        return json.dumps(value, ensure_ascii=False, default=str)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        import json
        return json.loads(value)


class Base(DeclarativeBase):
    pass


Money = Numeric(14, 2)
Qty = Numeric(14, 3)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_almaty)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_almaty, onupdate=now_almaty)


connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(settings.database_url, connect_args=connect_args, future=True)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def money(v) -> Decimal:
    d = Decimal(str(v or 0))
    return d.quantize(Decimal("0.01"))


def qty(v) -> Decimal:
    d = Decimal(str(v or 0))
    return d.quantize(Decimal("0.001"))
