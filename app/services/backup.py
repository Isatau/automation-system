"""Резервное копирование с ОБЯЗАТЕЛЬНОЙ проверкой восстановлением."""
from __future__ import annotations
import hashlib, os, shutil, subprocess
from sqlalchemy import create_engine, text as sqltext
from ..config import settings
from ..db import now_almaty
from ..models import Backup

CRITICAL_TABLES = ["orders", "payments", "shifts", "stock_movements", "audit_events", "users"]

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def _table_counts(dburl: str) -> dict:
    eng = create_engine(dburl, connect_args={"check_same_thread": False} if dburl.startswith("sqlite") else {})
    out = {}
    with eng.connect() as c:
        for t in CRITICAL_TABLES:
            try:
                out[t] = int(c.execute(sqltext(f"SELECT COUNT(*) FROM {t}")).scalar() or 0)
            except Exception:
                out[t] = -1
    eng.dispose()
    return out

def create_backup(db, created_by: str = "auto") -> Backup:
    os.makedirs(settings.backup_dir, exist_ok=True)
    ts = now_almaty().strftime("%Y%m%d_%H%M%S")
    rec = Backup(path="", sha256="", size_bytes=0, created_by=created_by)
    try:
        url = settings.database_url
        if url.startswith("sqlite"):
            src = url.split("///", 1)[1]
            # WAL checkpoint, затем копия файла
            with db.bind.connect() as c:
                c.execute(sqltext("PRAGMA wal_checkpoint(TRUNCATE)")); c.commit()
            dst = os.path.join(settings.backup_dir, f"restocore_{ts}.sqlite")
            shutil.copy2(src, dst)
        else:
            dst = os.path.join(settings.backup_dir, f"restocore_{ts}.dump.sql")
            env = dict(os.environ)
            # PGURL вида postgresql+psycopg2://user:pass@host/db
            u = url.replace("postgresql+psycopg2://", "postgresql://")
            env["PGURL"] = u
            r = subprocess.run(["pg_dump", u], capture_output=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode(errors="ignore")[:500])
            with open(dst, "wb") as f:
                f.write(r.stdout)
        rec.path = dst
        rec.sha256 = _sha256(dst)
        rec.size_bytes = os.path.getsize(dst)
        rec.table_counts = _table_counts(url)
        # Проверка восстановления: разворачиваем копию во временную БД и сверяем счётчики
        if url.startswith("sqlite"):
            tmp = dst + ".verify.sqlite"
            shutil.copy2(dst, tmp)
            counts2 = _table_counts(f"sqlite:///{tmp}")
            os.remove(tmp)
            ok = all(counts2.get(t, -1) == rec.table_counts.get(t) and rec.table_counts.get(t, -1) >= 0
                     for t in CRITICAL_TABLES)
            rec.verify_restore_ok = bool(ok)
            rec.status = "verified" if ok else "failed"
            if not ok:
                rec.error = "Контрольные счётчики таблиц после восстановления не совпали"
        else:
            # Для Postgres проверка — восстановление в изолированной БД (реализуется при эксплуатации); честно помечаем unverifed
            rec.verify_restore_ok = False
            rec.status = "created"
            rec.error = "Требуется ручная проверка восстановления в изолированной БД"
    except Exception as e:
        rec.status = "failed"
        rec.error = str(e)[:1000]
        from .notify import notify
        notify(db, etype="backup.failed", title="Ошибка резервного копирования", body=str(e)[:300], severity="critical")
    db.add(rec); db.commit(); db.refresh(rec)
    if rec.status == "failed" and created_by == "auto":
        from .notify import notify
        notify(db, etype="backup.failed", title="Резервная копия не создана", body=rec.error or "", severity="critical")
    return rec

def restore_backup(db, backup_id: str) -> bool:
    """Восстановление SQLite-копии. Сохраняет текущий файл как .prerestore и журнал аудита отдельно."""
    b = db.get(Backup, backup_id)
    if not b or b.status == "failed":
        raise ValueError("Копия отсутствует или помечена как неудачная")
    url = settings.database_url
    if not url.startswith("sqlite"):
        raise NotImplementedError("Для Postgres восстановление выполняется утилитой pg_restore вне API (см. README)")
    src_file = url.split("///", 1)[1]
    # сохранить аудит текущего состояния
    audit_copy = os.path.join(settings.backup_dir, f"audit_pre_restore_{now_almaty().strftime('%Y%m%d%H%M%S')}.json")
    rows = db.execute(sqltext("SELECT * FROM audit_events")).fetchall()
    import json
    with open(audit_copy, "w", encoding="utf") as f:
        json.dump([{k: str(v) for k, v in r._mapping.items()} for r in rows], f, ensure_ascii=False)
    if _sha256(b.path) != b.sha256:
        raise ValueError("Контрольная сумма копии не совпала — файл повреждён")
    db.close()
    shutil.move(src_file, src_file + ".prerestore")
    shutil.copy2(b.path, src_file)
    return True
