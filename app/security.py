"""Безопасность: bcrypt-хэши паролей, JWT-сессии, защита от перебора."""
from __future__ import annotations
import uuid, hashlib
from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, Header
from .config import settings
from .db import now_almaty, SessionLocal

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(p: str) -> str:
    return pwd_ctx.hash(p)

def verify_password(p: str, h: str) -> bool:
    try:
        return pwd_ctx.verify(p, h)
    except ValueError:
        return False

def make_token(user_id: str, role: str) -> tuple[str, str, datetime]:
    jti = uuid.uuid4().hex
    exp = now_almaty() + timedelta(minutes=settings.access_token_minutes)
    token = jwt.encode({"sub": user_id, "role": role, "jti": jti, "exp": int(exp.timestamp())},
                       settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti, exp

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(401, "Сессия недействительна или истекла")

def request_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()[:32]
