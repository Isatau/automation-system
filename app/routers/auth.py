"""Авторизация: вход с защитой от перебора, выход, me."""
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..deps import get_db, current_user
from ..models import User, SessionRec, LoginAttempt
from ..security import verify_password, make_token
from ..db import now_almaty
from ..config import settings
from ..services.audit import log_event

router = APIRouter(prefix="/auth", tags=["auth"])

class LoginIn(BaseModel):
    login: str
    password: str

@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else ""
    u = db.query(User).filter_by(login=body.login).first()
    blocked = bool(u and u.locked_until and u.locked_until > now_almaty())
    ok_user = u and not blocked and u.active and not u.locked and verify_password(body.password, u.password_hash)
    if not ok_user:
        if u and not blocked:
            u.failed_attempts = (u.failed_attempts or 0) + 1
            if u.failed_attempts >= settings.max_failed_logins:
                u.locked_until = now_almaty() + timedelta(minutes=settings.lockout_minutes)
                u.failed_attempts = 0
        db.add(LoginAttempt(login=body.login, success=False, ip=ip))
        log_event(db, user=u, event_type="auth.login_failed", object_type="user",
                  object_id=u.id if u else None, reason=f"login={body.login} ip={ip}")
        db.commit()
        raise HTTPException(401, "Неверный логин или пароль")
    u.failed_attempts = 0; u.locked_until = None; u.last_login_at = now_almaty()
    token, jti, exp = make_token(u.id, u.role)
    db.add(SessionRec(user_id=u.id, jti=jti, expires_at=exp, ip=ip, workstation=body.login))
    db.add(LoginAttempt(login=body.login, success=True, ip=ip))
    log_event(db, user=u, event_type="auth.login", object_type="user", object_id=u.id)
    db.commit()
    return {"token": token, "user": {"id": u.id, "name": u.name, "role": u.role, "login": u.login}}

@router.post("/logout")
def logout(user: User = Depends(current_user), db: Session = Depends(get_db)):
    jti = getattr(user, "_jwt_payload", {}).get("jti")
    s = db.query(SessionRec).filter_by(jti=jti).first()
    if s: s.revoked_at = now_almaty()
    log_event(db, user=user, event_type="auth.logout", object_type="user", object_id=user.id)
    db.commit()
    return {"ok": True}

@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..rbac import permissions_for
    return {"id": user.id, "name": user.name, "login": user.login, "role": user.role,
            "venue_id": user.venue_id, "discount_limit_pct": float(user.discount_limit_pct or 0),
            "permissions": sorted(permissions_for(user, db))}
