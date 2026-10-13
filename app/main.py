"""RestoCore — точка входа FastAPI.

Линейка API: /api/v1/<модуль>. Документация OpenAPI: /docs.
Фоновые задачи (в одном процессе, без внешних зависимостей):
- применение запланированных цен;
- автобэкап по интервалу с проверкой восстановления;
- просрочка сертификатов/снятий с продажи;
- алерты низких остатков и незакрытых смен.
Статика React-фронтенда монтируется из app/static, если собрана.
"""
from __future__ import annotations
import asyncio, os, time
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from .config import settings
from .db import Base, engine, SessionLocal, now_almaty
from . import models  # noqa: F401 — регистрация всех таблиц для create_all/метаданных

from .routers import (auth, orders, payments, shifts, menu, hall, kitchen,
                      warehouse, suppliers, purchase_orders, finance, staff,
                      payroll, guests, auditlog, notifications, documents,
                      reports, dashboard, admin)

BACKGROUND_INTERVAL_SEC = 300
_last_backup_ts = time.time()


def _bg_jobs():
    """Синхронные фоновые операции. Каждая — в своей транзакции; ошибки логируются,
    но не роняют сервис."""
    global _last_backup_ts
    db = SessionLocal()
    try:
        from .routers.menu import apply_scheduled_prices
        apply_scheduled_prices(db)
        db.commit()
    except Exception as e:
        db.rollback(); print(f"[bg] prices: {e}")
    try:
        from .models import GiftCertificate, MenuItem
        now = now_almaty()
        changed = False
        for c in db.query(GiftCertificate).filter(GiftCertificate.status == "active",
                                                  GiftCertificate.valid_to.is_not(None)).all():
            if c.valid_to.replace(tzinfo=now.tzinfo) < now:
                c.status = "expired"; changed = True
        if changed:
            db.commit()
    except Exception as e:
        db.rollback(); print(f"[bg] certs: {e}")
    try:
        from .models import StockBalance, Product, Warehouse
        from .services.notify import notify
        low = []
        for b in db.query(StockBalance).all():
            p = db.get(Product, b.product_id)
            if p and not p.archived and float(b.quantity or 0) < float(p.min_stock or 0):
                low.append(p.name)
        if low:
            notify(db, etype="stock.low", title="Низкие складские остатки",
                   body="Ниже минимума: " + ", ".join(low[:15]), severity="warn")
            db.commit()
    except Exception as e:
        db.rollback(); print(f"[bg] stock alerts: {e}")
    try:
        from .models import Shift
        from .services.notify import notify
        stale = db.query(Shift).filter(Shift.status == "open",
                                       Shift.opened_at < now_almaty().replace(tzinfo=None) - timedelta(hours=16)).count()
        if stale:
            notify(db, etype="shift.stale_open", title="Незакрытые смены старше 16 ч",
                   body=f"Открытых смен: {stale}", severity="warn")
            db.commit()
    except Exception as e:
        db.rollback(); print(f"[bg] shifts: {e}")
    # автобэкап по расписанию
    try:
        interval = max(1, settings.backup_interval_hours) * 3600
        if time.time() - _last_backup_ts >= interval:
            _last_backup_ts = time.time()
            from .services.backup import create_backup
            create_backup(db, created_by="auto")
    except Exception as e:
        print(f"[bg] backup: {e}")
    finally:
        db.close()


async def _bg_loop():
    while True:
        await asyncio.to_thread(_bg_jobs)
        await asyncio.sleep(BACKGROUND_INTERVAL_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Схема: Alembic. Если в БД уже есть таблицы вне alembic_version (например,
    # тесты создали их через create_all), сначала ставим baseline-метку, затем upgrade head.
    try:
        from alembic.config import Config as AConfig
        from alembic import command
        from sqlalchemy import inspect as sa_inspect
        root = os.path.join(os.path.dirname(__file__), "..")
        cfg = AConfig(os.path.join(root, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(root, "migrations"))
        cfg.set_main_option("sqlalchemy.url", settings.database_url)
        insp = sa_inspect(engine)
        tables = set(insp.get_table_names())
        if tables and "alembic_version" not in tables:
            command.stamp(cfg, "head")   # существующая схема = актуальная версия моделей
        else:
            command.upgrade(cfg, "head")
    except Exception as e:
        print(f"[startup] Alembic недоступен ({e}); создаю схему через metadata.create_all")
        Base.metadata.create_all(bind=engine)
    if settings.seed_demo:
        try:
            from .seed import seed_all
            seed_all(force=False)       # идемпотентно: пропускает, если данные уже есть
        except Exception as e:
            print(f"[startup] seed: {e}")
    task = asyncio.create_task(_bg_loop())
    yield
    task.cancel()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in
                    os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

API = "/api/v1"
for r in (auth.router, orders.router, payments.router, shifts.router, menu.router,
          hall.router, kitchen.router, warehouse.router, suppliers.router,
          purchase_orders.router, finance.router, staff.router, payroll.router,
          guests.router, auditlog.router, notifications.router, documents.router,
          reports.router, dashboard.router, admin.router):
    app.include_router(r, prefix=API)


@app.get(API + "/health")
def health():
    return {"status": "ok", "app": settings.app_name, "time": now_almaty().isoformat()}


# --- Фронтенд (React build) ---
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR) and os.path.exists(os.path.join(STATIC_DIR, "index.html")):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/") or full_path in ("docs", "openapi.json", "redoc"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        fp = os.path.join(STATIC_DIR, full_path)
        if full_path and os.path.isfile(fp):
            return FileResponse(fp)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
