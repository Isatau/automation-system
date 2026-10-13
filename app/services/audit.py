"""Запись событий аудита (append-only). Все критические операции вызывают log_event."""
from __future__ import annotations
from typing import Any, Optional
from ..db import now_almaty
from ..models import AuditEvent

def log_event(db, *, user=None, event_type: str, object_type: str = None, object_id: str = None,
              venue_id: str = None, old_values: dict = None, new_values: dict = None,
              amount=None, reason: str = None, approved_by=None, register_id: str = None,
              workstation: str = None):
    ev = AuditEvent(
        at=now_almaty(),
        user_id=getattr(user, "id", user),
        user_role=getattr(user, "role", None),
        event_type=event_type, object_type=object_type, object_id=object_id,
        venue_id=venue_id, old_values=old_values, new_values=new_values,
        amount=amount, reason=reason,
        approved_by=getattr(approved_by, "id", approved_by),
        register_id=register_id, workstation=workstation,
    )
    db.add(ev)
    return ev
