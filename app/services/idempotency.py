"""Идемпотентность критических операций по заголовку Idempotency-Key."""
import hashlib, json
from fastapi import HTTPException, Header, Depends
from sqlalchemy.exc import IntegrityError
from ..models import IdempotencyKey

def check_key(db, key: str | None, body: dict):
    """Возвращает сохранённый ответ при повторе; иначе регистрирует ключ."""
    if not key:
        return None
    rh = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    rec = db.query(IdempotencyKey).filter_by(key=key).first()
    if rec:
        if rec.request_hash != rh:
            raise HTTPException(422, "Idempotency-Key уже использован с другим телом запроса")
        return {"replayed": True, "response": rec.response_json, "status_code": rec.status_code}
    return {"rec": IdempotencyKey(key=key, request_hash=rh)}

def save_result(db, holder, response: dict, status_code: int = 200):
    if isinstance(holder, dict) and "rec" in holder:
        holder["rec"].response_json = response
        holder["rec"].status_code = status_code
        db.add(holder["rec"])
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
