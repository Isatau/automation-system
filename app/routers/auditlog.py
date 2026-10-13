"""Журнал аудита: просмотр (append-only) + выгрузка. Изменение истории запрещено."""
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..deps import get_db, current_user, require_perm
from ..models import AuditEvent, User
from ..services.exporters import to_csv, to_xlsx

router = APIRouter(prefix="/audit", tags=["audit"])


def _q(db, date_from, date_to, event_type, user_id, object_type, object_id, q):
    sq = db.query(AuditEvent)
    if date_from:
        sq = sq.filter(AuditEvent.at >= date_from)
    if date_to:
        sq = sq.filter(AuditEvent.at <= date_to)
    if event_type:
        sq = sq.filter(AuditEvent.event_type.like(event_type + "%"))
    if user_id:
        sq = sq.filter(AuditEvent.user_id == user_id)
    if object_type:
        sq = sq.filter(AuditEvent.object_type == object_type)
    if object_id:
        sq = sq.filter(AuditEvent.object_id == object_id)
    if q:
        sq = sq.filter(AuditEvent.reason.ilike(f"%{q}%"))
    return sq


@router.get("", dependencies=[Depends(require_perm("audit:view"))])
def list_events(date_from: datetime | None = None, date_to: datetime | None = None,
                event_type: str | None = None, user_id: str | None = None,
                object_type: str | None = None, object_id: str | None = None,
                q: str = "", limit: int = Query(100, le=500), offset: int = 0,
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    sq = _q(db, date_from, date_to, event_type, user_id, object_type, object_id, q)
    total = sq.count()
    rows = sq.order_by(AuditEvent.at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [_e(e) for e in rows]}


def _e(e: AuditEvent) -> dict:
    return {"id": e.id, "at": e.at.isoformat() if e.at else None,
            "user_id": e.user_id, "user_role": e.user_role, "event_type": e.event_type,
            "object_type": e.object_type, "object_id": e.object_id,
            "old_values": e.old_values, "new_values": e.new_values,
            "amount": float(e.amount) if e.amount is not None else None,
            "reason": e.reason, "approved_by": e.approved_by,
            "register_id": e.register_id, "workstation": e.workstation}


@router.get("/export", dependencies=[Depends(require_perm("audit:view"))])
def export(date_from: datetime | None = None, date_to: datetime | None = None,
           event_type: str | None = None, fmt: str = Query("xlsx", pattern="^(csv|xlsx)$"),
           db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = _q(db, date_from, date_to, event_type, None, None, None, "").\
        order_by(AuditEvent.at.desc()).limit(20000).all()
    cols = ["Дата (Asia/Almaty)", "Сотрудник", "Роль", "Событие", "Объект", "ID объекта",
            "Сумма", "Причина", "Подтвердил"]
    data = [[e.at.strftime("%d.%m.%Y %H:%M:%S") if e.at else "", e.user_id or "", e.user_role or "",
             e.event_type, f"{e.object_type or ''}:{e.object_id or ''}",
             float(e.amount) if e.amount is not None else "", e.reason or "", e.approved_by or ""]
            for e in rows]
    blob = to_xlsx(cols, data, "Журнал событий") if fmt == "xlsx" else to_csv(cols, data)
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
             if fmt == "xlsx" else "text/csv; charset=utf-8")
    return Response(blob, media_type=media, headers={
        "Content-Disposition": f"attachment; filename=audit.{fmt}"})


@router.get("/object/{otype}/{oid}", dependencies=[Depends(require_perm("audit:view"))])
def object_history(otype: str, oid: str, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    rows = db.query(AuditEvent).filter_by(object_type=otype, object_id=oid)\
        .order_by(AuditEvent.at.desc()).limit(500).all()
    return {"items": [_e(e) for e in rows]}
