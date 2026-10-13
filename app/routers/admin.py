"""Администрирование: пользователи/права, настройки, резервные копии и
восстановление, журнал интеграций. Секреты не возвращаются в API."""
from __future__ import annotations
import os
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import now_almaty
from ..deps import get_db, current_user, require_perm
from ..models import User, Backup, Setting, IntegrationLog, SessionRec, LoginAttempt, UserPermission
from ..security import hash_password
from ..rbac import ALL as ALL_PERMS, ROLE_PERMS, permissions_for
from ..services.audit import log_event
from ..services.backup import create_backup, restore_backup
from ..integrations.base import retry_failed
from ..integrations.fiscal_kz import get_fiscal_adapter
from ..config import settings

router = APIRouter(prefix="/admin", tags=["admin"])

ROLES = list(ROLE_PERMS.keys())


# ---------------- Пользователи ----------------
class UserIn(BaseModel):
    login: str = Field(min_length=3, max_length=40, pattern=r"^[a-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=150)
    role: str
    password: str | None = Field(None, min_length=8)
    discount_limit_pct: float = Field(0, ge=0, le=100)
    venue_id: str | None = None
    active: bool = True


@router.get("/users", dependencies=[Depends(require_perm("admin:users"))])
def list_users(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.query(User).all()
    return [{"id": u.id, "login": u.login, "name": u.name, "role": u.role,
             "active": u.active, "locked_until": u.locked_until.isoformat() if u.locked_until else None,
             "discount_limit_pct": float(u.discount_limit_pct or 0)} for u in rows]


@router.post("/users", dependencies=[Depends(require_perm("admin:users"))])
def create_user(body: UserIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.role not in ROLES:
        raise HTTPException(422, f"Недопустимая роль. Доступны: {', '.join(ROLES)}")
    if not body.password:
        raise HTTPException(422, "Пароль обязателен (минимум 8 символов)")
    if db.query(User).filter_by(login=body.login).first():
        raise HTTPException(409, "Логин занят")
    u = User(login=body.login, name=body.name, role=body.role,
             password_hash=hash_password(body.password),
             discount_limit_pct=body.discount_limit_pct, venue_id=body.venue_id or user.venue_id,
             active=body.active)
    db.add(u); db.flush()
    log_event(db, user=user, event_type="admin.user_create", object_type="user", object_id=u.id,
              new_values={"login": u.login, "role": u.role})
    db.commit()
    return {"id": u.id}


@router.put("/users/{uid}", dependencies=[Depends(require_perm("admin:users"))])
def update_user(uid: str, body: UserIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    if body.role not in ROLES:
        raise HTTPException(422, "Недопустимая роль")
    old = {"role": u.role, "active": u.active, "discount_limit_pct": float(u.discount_limit_pct or 0)}
    u.name, u.role, u.active = body.name, body.role, body.active
    u.discount_limit_pct = body.discount_limit_pct
    if body.password:
        u.password_hash = hash_password(body.password)
        # завершаем все сессии при смене пароля
        db.query(SessionRec).filter(SessionRec.user_id == uid).delete()
    log_event(db, user=user, event_type="admin.user_update", object_type="user", object_id=uid,
              old_values=old, new_values={"role": u.role, "active": u.active,
                                          "password_changed": bool(body.password)})
    db.commit()
    return {"ok": True}


@router.post("/users/{uid}/block", dependencies=[Depends(require_perm("admin:users"))])
def block_user(uid: str, minutes: int = Query(60, ge=1, le=10080), reason: str = Query(..., min_length=3),
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    from datetime import timedelta
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    u.locked_until = now_almaty() + timedelta(minutes=minutes)
    db.query(SessionRec).filter(SessionRec.user_id == uid).delete()
    log_event(db, user=user, event_type="admin.user_block", object_type="user", object_id=uid,
              reason=reason, new_values={"until": u.locked_until.isoformat()})
    db.commit()
    return {"locked_until": u.locked_until.isoformat()}


@router.post("/users/{uid}/unlock", dependencies=[Depends(require_perm("admin:users"))])
def unlock_user(uid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    u.locked_until = None
    u.failed_attempts = 0; u.locked = False
    log_event(db, user=user, event_type="admin.user_unlock", object_type="user", object_id=uid)
    db.commit()
    return {"ok": True}


class PermIn(BaseModel):
    grants: dict[str, bool] = Field(description="Дополнительные индивидуальные разрешения: perm -> true/false")


@router.get("/users/{uid}/permissions", dependencies=[Depends(require_perm("admin:permissions"))])
def get_user_perms(uid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    extra = db.query(UserPermission).filter_by(user_id=uid).all()
    return {"role": u.role, "base": sorted(ROLE_PERMS.get(u.role, set())),
            "effective": sorted(permissions_for(u, db)),
            "extra": {p.permission: p.granted for p in extra}}


@router.put("/users/{uid}/permissions", dependencies=[Depends(require_perm("admin:permissions"))])
def set_user_perms(uid: str, body: PermIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    unknown = [k for k in body.grants if k not in ALL_PERMS]
    if unknown:
        raise HTTPException(422, f"Неизвестные разрешения: {', '.join(unknown)}")
    old = {p.permission: p.granted for p in db.query(UserPermission).filter_by(user_id=uid).all()}
    for perm, granted in body.grants.items():
        rec = db.query(UserPermission).filter_by(user_id=uid, permission=perm).first()
        if rec:
            rec.granted = granted
        else:
            db.add(UserPermission(user_id=uid, permission=perm, granted=granted))
    log_event(db, user=user, event_type="admin.permissions_change", object_type="user",
              object_id=uid, old_values=old, new_values=body.grants)
    db.commit()
    return {"effective": sorted(permissions_for(u, db))}


@router.get("/roles", dependencies=[Depends(require_perm("admin:permissions"))])
def roles_matrix(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"permissions": sorted(ALL_PERMS),
            "roles": {r: sorted(p) for r, p in ROLE_PERMS.items()}}


# ---------------- Настройки ----------------
class SettingIn(BaseModel):
    key: str
    value: dict


SECRET_KEYS = ("telegram_bot_token",)


@router.get("/settings", dependencies=[Depends(require_perm("admin:settings"))])
def list_settings(db: Session = Depends(get_db), user: User = Depends(current_user)):
    out = {}
    for s in db.query(Setting).all():
        v = s.value if isinstance(s.value, dict) else {"v": s.value}
        if s.key in SECRET_KEYS and v.get("v"):
            v = {"v": "***"}   # секрет не отдаём наружу
        out[s.key] = v
    return out


@router.put("/settings", dependencies=[Depends(require_perm("admin:settings"))])
def set_setting(body: SettingIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    allowed = {"telegram_chat_id", "notification_thresholds", "purchase_daily_limit",
               "hourly_services_enabled", "negative_stock_policy", "telegram_bot_token"}
    if body.key not in allowed:
        raise HTTPException(422, f"Разрешённые ключи: {', '.join(sorted(allowed))}")
    old = db.query(Setting).filter_by(key=body.key).first()
    old_v = old.value if old else None
    if old:
        old.value = body.value
    else:
        db.add(Setting(key=body.key, value=body.value))
    shown = {k: ("***" if k in SECRET_KEYS else v) for k, v in body.value.items()}
    log_event(db, user=user, event_type="settings.change", object_type="setting",
              object_id=body.key, old_values=old_v, new_values=shown)
    db.commit()
    return {"ok": True}


# ---------------- Сессии и попытки входа ----------------
@router.get("/sessions", dependencies=[Depends(require_perm("admin:users"))])
def sessions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.query(SessionRec).order_by(SessionRec.created_at.desc()).limit(200).all()
    names = {u.id: u.name for u in db.query(User).all()}
    return [{"user": names.get(s.user_id, s.user_id), "started": s.created_at.isoformat(),
             "expires": s.expires_at.isoformat() if s.expires_at else None,
             "workstation": getattr(s, "workstation", None)} for s in rows]


@router.get("/login-attempts", dependencies=[Depends(require_perm("audit:view"))])
def login_attempts(failed_only: bool = False, limit: int = 200,
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = db.query(LoginAttempt)
    if failed_only:
        q = q.filter(LoginAttempt.success == False)  # noqa
    rows = q.order_by(LoginAttempt.at.desc()).limit(limit).all()
    return [{"at": a.at.isoformat(), "login": a.login, "success": a.success,
             "ip": a.ip} for a in rows]


# ---------------- Резервные копии ----------------
@router.get("/backups", dependencies=[Depends(require_perm("backups:run"))])
def list_backups(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.query(Backup).order_by(Backup.created_at.desc()).limit(100).all()
    return [{"id": b.id, "at": b.created_at.isoformat(), "by": b.created_by,
             "status": b.status, "verified_restore_ok": b.verify_restore_ok,
             "size": b.size_bytes, "sha256": b.sha256[:16], "error": b.error,
             "table_counts": b.table_counts} for b in rows]


@router.post("/backups/run", dependencies=[Depends(require_perm("backups:run"))])
def run_backup(db: Session = Depends(get_db), user: User = Depends(current_user)):
    b = create_backup(db, created_by=f"user:{user.id}")
    log_event(db, user=user, event_type="backup.manual", object_type="backup",
              object_id=b.id, new_values={"status": b.status})
    db.commit()
    if b.status == "failed":
        raise HTTPException(500, f"Резервное копирование не удалось: {b.error}")
    return {"id": b.id, "status": b.status, "verify_restore_ok": b.verify_restore_ok}


@router.post("/backups/{bid}/restore", dependencies=[Depends(require_perm("backups:restore"))])
def restore(bid: str, confirm: str = Query(..., description="строка RESTORE для подтверждения"),
           db: Session = Depends(get_db), user: User = Depends(current_user)):
    if confirm != "RESTORE":
        raise HTTPException(422, "Требуется подтверждение: confirm=RESTORE")
    b = db.get(Backup, bid)
    if not b:
        raise HTTPException(404, "Копия не найдена")
    if not b.verify_restore_ok:
        raise HTTPException(422, "Копия не прошла проверку восстановления — восстановление запрещено. "
                                 "Сначала создайте новую копию и проверьте её.")
    log_event(db, user=user, event_type="backup.restore_start", object_type="backup",
              object_id=bid, reason="Восстановление БД из копии")
    db.commit()
    try:
        restore_backup(db, bid)
    except NotImplementedError:
        raise HTTPException(501, "Для PostgreSQL восстановление выполняется pg_restore из командной строки "
                                 "(см. README раздел «Восстановление»).")
    except Exception as e:
        raise HTTPException(500, f"Ошибка восстановления: {e}")
    return {"ok": True, "note": "Файл БД заменён; предыдущее состояние сохранено как .prerestore, "
                                "аудит — как audit_pre_restore_*.json. Перезапустите сервис."}


# ---------------- Интеграции ----------------
@router.get("/integrations/log", dependencies=[Depends(require_perm("admin:settings"))])
def integration_log(adapter: str | None = None, state: str | None = None,
                    limit: int = 200, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    q = db.query(IntegrationLog)
    if adapter:
        q = q.filter(IntegrationLog.adapter == adapter)
    if state:
        q = q.filter(IntegrationLog.state == state)
    rows = q.order_by(IntegrationLog.created_at.desc()).limit(limit).all()
    return [{"at": r.created_at.isoformat(), "adapter": r.adapter, "op": r.operation,
             "state": r.state, "device_confirmed": r.device_confirmed,
             "retries": r.retries, "error": r.error, "ref_id": r.ref_id} for r in rows]


@router.post("/integrations/retry", dependencies=[Depends(require_perm("admin:settings"))])
def retry_integrations(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Повторно обрабатываем только БЕЗОПАСНЫЕ операции (pending с идемпотентным ref)."""
    fiscal = get_fiscal_adapter()
    n = retry_failed(db, fiscal)
    log_event(db, user=user, event_type="integration.retry", object_type="integration",
              new_values={"adapter": fiscal.name, "processed": n})
    db.commit()
    return {"processed": n, "adapter": fiscal.name}


@router.get("/system/status", dependencies=[Depends(require_perm("admin:settings"))])
def system_status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    last_backup = db.query(Backup).filter(Backup.status == "verified")\
        .order_by(Backup.created_at.desc()).first()
    stale = None
    if last_backup:
        age_h = (now_almaty().replace(tzinfo=None) - last_backup.created_at).total_seconds() / 3600
        stale = round(age_h, 1)
    return {"app": settings.app_name, "db": settings.database_url.split("://")[0],
            "fiscal_adapter": settings.fiscal_adapter, "terminal_adapter": settings.terminal_adapter,
            "telegram_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
            "last_verified_backup_age_hours": stale,
            "pending_integration_ops": db.query(IntegrationLog).filter_by(state="pending").count()}
