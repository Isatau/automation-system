"""Движок уведомлений: пороги, получатели, антидупликация, каналы internal/telegram."""
from __future__ import annotations
from datetime import timedelta
from ..db import now_almaty
from ..models import Notification, NotificationRule, User
from ..integrations.telegram import send_telegram, get_setting_value

def notify(db, *, etype: str, title: str, body: str, severity: str = "info",
           link_type=None, link_id=None, amount=None, user_id=None, dedupe_key=None):
    rules = db.query(NotificationRule).filter_by(event_type=etype, active=True).all()
    if not rules:
        # без правила — базовая внутренняя доставка менеджеру/владельцу для critical
        targets = [None]
        channels = "internal"
        window = 10
    else:
        targets, channels, window = [], set(), 10
        for r in rules:
            if r.threshold_amount is not None and amount is not None and float(amount) < float(r.threshold_amount):
                continue
            if r.recipients_role:
                for u in db.query(User).filter_by(role=r.recipients_role, active=True).all():
                    targets.append(u.id)
            else:
                targets.append(None)
            channels.update(c.strip() for c in (r.channels or "internal").split(","))
            window = max(window, r.dedupe_window_min or 0)
        channels = ",".join(sorted(channels))
    dk = dedupe_key or f"{etype}:{link_id or 'general'}"
    since = now_almaty() - timedelta(minutes=window)
    if db.query(Notification).filter(Notification.dedupe_key == dk, Notification.created_at > since).first():
        return None  # защита от повторной отправки одинаковых уведомлений
    n = Notification(type=etype, title=title, body=body, severity=severity,
                     link_object_type=link_type, link_object_id=link_id,
                     user_id=user_id or (targets[0] if targets and targets[0] else None),
                     dedupe_key=dk, channel="internal")
    db.add(n); db.flush()
    if "telegram" in channels:
        token = get_setting_value(db, "telegram_bot_token")
        chat_id = get_setting_value(db, "telegram_chat_id")
        if token and chat_id:
            ok, msg_id, err = send_telegram(token, chat_id, f"[{severity.upper()}] {title}\n{body}")
            n.tg_message_id = msg_id
            if not ok:
                from .audit import log_event
                log_event(db, event_type="notification.telegram_failed", object_type="notification",
                          object_id=n.id, reason=err)
    return n
