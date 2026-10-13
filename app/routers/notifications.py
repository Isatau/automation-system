"""Уведомления: внутренний inbox, правила рассылки, retry Telegram-доставки."""
from __future__ import annotations
import re
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import now_almaty
from ..deps import get_db, current_user, require_perm
from ..models import Notification, NotificationRule, User, Setting
from ..integrations.telegram import send_telegram, get_setting_value
from ..services.audit import log_event

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", dependencies=[Depends(require_perm("notifications:view"))])
def inbox(unread_only: bool = False, limit: int = Query(50, le=200),
          db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = db.query(Notification).filter((Notification.user_id == user.id) | (Notification.user_id.is_(None)))
    if unread_only:
        q = q.filter(Notification.status == "new")
    rows = q.order_by(Notification.created_at.desc()).limit(limit).all()
    return {"items": [_n(n) for n in rows],
            "unread": db.query(Notification).filter(
                (Notification.user_id == user.id) | (Notification.user_id.is_(None)),
                Notification.status == "new").count()}


def _n(n: Notification) -> dict:
    return {"id": n.id, "type": n.type, "title": n.title, "body": n.body,
            "severity": n.severity, "status": n.status, "channel": n.channel,
            "link": {"type": n.link_object_type, "id": n.link_object_id},
            "at": n.created_at.isoformat() if n.created_at else None}


@router.post("/{nid}/read", dependencies=[Depends(require_perm("notifications:view"))])
def mark_read(nid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n = db.get(Notification, nid)
    if not n:
        raise HTTPException(404, "Уведомление не найдено")
    n.status = "read"
    db.commit()
    return {"ok": True}


@router.post("/{nid}/ack", dependencies=[Depends(require_perm("notifications:view"))])
def ack(nid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n = db.get(Notification, nid)
    if not n:
        raise HTTPException(404, "Уведомление не найдено")
    n.status = "acked"
    log_event(db, user=user, event_type="notification.ack", object_type="notification", object_id=nid)
    db.commit()
    return {"ok": True}


# ---------------- Правила ----------------
from pydantic import BaseModel, Field


class RuleIn(BaseModel):
    event_type: str
    threshold_amount: float | None = None
    recipients_role: str | None = None
    quiet_hours: str | None = None      # "23:00-07:00"
    dedupe_window_min: int = 10
    channels: str = "internal"
    active: bool = True


@router.get("/rules", dependencies=[Depends(require_perm("notifications:view"))])
def list_rules(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.query(NotificationRule).all()
    return [{"id": r.id, "event_type": r.event_type, "threshold_amount": float(r.threshold_amount)
             if r.threshold_amount is not None else None,
             "recipients_role": r.recipients_role, "quiet_hours": r.quiet_hours,
             "dedupe_window_min": r.dedupe_window_min, "channels": r.channels,
             "active": r.active} for r in rows]


@router.post("/rules", dependencies=[Depends(require_perm("notifications:manage"))])
def upsert_rule(body: RuleIn, rule_id: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.quiet_hours and not re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", body.quiet_hours):
        raise HTTPException(422, "Тихие часы в формате ЧЧ:ММ-ЧЧ:ММ")
    if rule_id:
        r = db.get(NotificationRule, rule_id)
        if not r:
            raise HTTPException(404, "Правило не найдено")
        for k, v in body.model_dump().items():
            setattr(r, k, v)
    else:
        r = NotificationRule(**body.model_dump())
        db.add(r)
    db.flush()
    log_event(db, user=user, event_type="settings.change", object_type="notification_rule",
              object_id=r.id, new_values=body.model_dump(mode="json"))
    db.commit()
    return {"id": r.id}


@router.post("/rules/{rid}/toggle", dependencies=[Depends(require_perm("notifications:manage"))])
def toggle_rule(rid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = db.get(NotificationRule, rid)
    if not r:
        raise HTTPException(404, "Правило не найдено")
    r.active = not r.active
    log_event(db, user=user, event_type="settings.change", object_type="notification_rule",
              object_id=rid, new_values={"active": r.active})
    db.commit()
    return {"active": r.active}


# ---------------- Telegram-настройки и retry ----------------
@router.get("/telegram/status", dependencies=[Depends(require_perm("notifications:manage"))])
def tg_status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    token = get_setting_value(db, "telegram_bot_token")
    chat = get_setting_value(db, "telegram_chat_id")
    if not token:
        return {"configured": False, "note": "Токен задаётся только через ENV/Setting без отображения секрета"}
    ok, msg_id, err = send_telegram(token, chat or "", "Тестовое сообщение RestoCore")
    return {"configured": True, "last_test_ok": ok, "error": err}


@router.post("/telegram/retry", dependencies=[Depends(require_perm("notifications:manage"))])
def tg_retry(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Повторная отправка неотправленных telegram-уведомлений (без дублей по tg_message_id)."""
    token = get_setting_value(db, "telegram_bot_token")
    chat = get_setting_value(db, "telegram_chat_id")
    if not token or not chat:
        raise HTTPException(422, "Telegram не настроен (нужны token и chat_id)")
    rows = db.query(Notification).filter(Notification.channel.like("%telegram%"),
                                         Notification.tg_message_id.is_(None)).limit(50).all()
    sent = 0
    for n in rows:
        ok, msg_id, err = send_telegram(token, chat, f"[{n.severity.upper()}] {n.title}\n{n.body}")
        if ok:
            n.tg_message_id = msg_id; sent += 1
        else:
            log_event(db, user=user, event_type="notification.telegram_failed",
                      object_type="notification", object_id=n.id, reason=err)
    db.commit()
    return {"retried": len(rows), "sent": sent}
