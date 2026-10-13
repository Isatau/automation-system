"""Зависимости FastAPI: сессия БД, текущий пользователь, проверка прав."""
from fastapi import Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session
from .db import SessionLocal
from .security import decode_token
from .rbac import has_perm, permissions_for
from .models import User, SessionRec

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def current_user(request: Request = None, authorization: str = Header(None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Требуется авторизация")
    payload = decode_token(authorization.split(" ", 1)[1])
    sess = db.query(SessionRec).filter(SessionRec.jti == payload["jti"]).first()
    if not sess or sess.revoked_at:
        raise HTTPException(401, "Сессия завершена")
    user = db.get(User, payload["sub"])
    if not user or not user.active or user.locked:
        raise HTTPException(403, "Пользователь заблокирован или удалён")
    user._jwt_payload = payload
    return user

def require_perm(perm: str):
    def checker(user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not has_perm(user, db, perm):
            raise HTTPException(403, f"Недостаточно прав: {perm}")
        return user
    return checker
