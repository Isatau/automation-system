"""Зал: схема, столы, статусы, объединение столов, бронирования."""
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional, List
from ..deps import get_db, require_perm
from ..db import now_almaty, money
from ..models import Hall, Table, Reservation, Order, User, Guest
from ..services.audit import log_event

router = APIRouter(tags=["hall"])


@router.get("/halls")
def list_halls(user: User = Depends(require_perm("hall:view")), db: Session = Depends(get_db)):
    out = []
    for h in db.query(Hall).filter_by(venue_id=user.venue_id).order_by(Hall.sort).all():
        tables = []
        for t in db.query(Table).filter_by(hall_id=h.id).all():
            o = db.query(Order).filter(Order.table_id == t.id,
                                       Order.status.notin_(["closed", "cancelled"])).first()
            open_ms = None
            if o:
                open_ms = int((now_almaty() - o.created_at).total_seconds())
            tables.append({"id": t.id, "name": t.name, "seats": t.seats, "status": t.status,
                           "x": t.x, "y": t.y, "hourly": t.hourly, "tariff": float(t.tariff or 0),
                           "waiter_id": t.waiter_id, "merged_group_id": t.merged_group_id,
                           "open_order_id": o.id if o else None,
                           "open_order_number": o.number if o else None,
                           "open_order_total": float(o.total) if o else 0,
                           "open_minutes": open_ms // 60 if o else None})
        out.append({"id": h.id, "name": h.name, "kind": h.kind, "tables": tables})
    return out


class HallIn(BaseModel):
    name: str
    kind: str = "hall"
    sort: int = 0

@router.post("/halls")
def create_hall(body: HallIn, user: User = Depends(require_perm("hall:manage")), db: Session = Depends(get_db)):
    h = Hall(venue_id=user.venue_id, **body.model_dump())
    db.add(h); db.commit(); db.refresh(h)
    log_event(db, user=user, event_type="hall.created", object_type="hall", object_id=h.id)
    return {"id": h.id}


class TableIn(BaseModel):
    hall_id: str
    name: str
    seats: int = Field(4, ge=1, le=50)
    x: int = 0
    y: int = 0
    hourly: bool = False
    tariff: Optional[float] = None

@router.post("/tables")
def create_table(body: TableIn, user: User = Depends(require_perm("hall:manage")), db: Session = Depends(get_db)):
    t = Table(**body.model_dump(), tariff=money(body.tariff or 0))
    db.add(t); db.commit(); db.refresh(t)
    log_event(db, user=user, event_type="table.created", object_type="table", object_id=t.id)
    return {"id": t.id}

@router.patch("/tables/{tid}")
def patch_table(tid: str, body: dict, user: User = Depends(require_perm("hall:manage")), db: Session = Depends(get_db)):
    t = db.get(Table, tid)
    if not t: raise HTTPException(404, "Стол не найден")
    allowed = {"name", "seats", "status", "x", "y", "hourly", "tariff", "waiter_id"}
    old = {k: getattr(t, k) for k in body if k in allowed}
    for k, v in body.items():
        if k in allowed: setattr(t, k, v)
    log_event(db, user=user, event_type="table.updated", object_type="table", object_id=tid,
              old_values={k: str(v) for k, v in old.items()}, new_values={k: str(v) for k, v in body.items() if k in allowed})
    db.commit()
    return {"ok": True}


class MergeGroupIn(BaseModel):
    table_ids: List[str]

@router.post("/tables/merge-group")
def merge_tables(body: MergeGroupIn, user: User = Depends(require_perm("hall:manage")), db: Session = Depends(get_db)):
    """Объединение столов в группу (для компании за соседними столами)."""
    if len(body.table_ids) < 2: raise HTTPException(400, "Нужно минимум 2 стола")
    import uuid
    gid = uuid.uuid4().hex[:12]
    for tid in body.table_ids:
        t = db.get(Table, tid)
        if not t: raise HTTPException(404, f"Стол {tid} не найден")
        t.merged_group_id = gid
    log_event(db, user=user, event_type="tables.merged", object_type="table", object_id=gid,
              new_values={"tables": body.table_ids})
    db.commit()
    return {"group_id": gid}

@router.post("/tables/unmerge")
def unmerge(group_id: str, user: User = Depends(require_perm("hall:manage")), db: Session = Depends(get_db)):
    n = 0
    for t in db.query(Table).filter_by(merged_group_id=group_id).all():
        t.merged_group_id = None; n += 1
    log_event(db, user=user, event_type="tables.unmerged", object_type="table", object_id=group_id)
    db.commit()
    return {"ok": n}


class ResIn(BaseModel):
    table_id: str
    date_start: str
    date_end: str
    guest_name: Optional[str] = None
    guest_phone: Optional[str] = None
    deposit_amount: Optional[float] = None
    comment: Optional[str] = None

@router.get("/reservations")
def list_reservations(date: Optional[str] = None, user: User = Depends(require_perm("hall:view")),
                      db: Session = Depends(get_db)):
    q = db.query(Reservation)
    if date: q = q.filter(Reservation.date_start >= date)
    return [{"id": r.id, "table_id": r.table_id, "guest_id": r.guest_id,
             "date_start": r.date_start.isoformat(sep=" ", timespec="minutes"),
             "date_end": r.date_end.isoformat(sep=" ", timespec="minutes"),
             "status": r.status, "deposit_amount": float(r.deposit_amount or 0)}
            for r in q.order_by(Reservation.date_start).limit(200).all()]

@router.post("/reservations")
def create_reservation(body: ResIn, user: User = Depends(require_perm("hall:view")), db: Session = Depends(get_db)):
    ds = datetime.fromisoformat(body.date_start); de = datetime.fromisoformat(body.date_end)
    if de <= ds: raise HTTPException(400, "Дата окончания должна быть позже начала")
    t = db.get(Table, body.table_id)
    if not t: raise HTTPException(404, "Стол не найден")
    clash = db.query(Reservation).filter(Reservation.table_id == t.id, Reservation.status.in_(["confirmed", "seated"]),
                                         Reservation.date_start < de, Reservation.date_end > ds).first()
    if clash: raise HTTPException(409, "На это время у стола уже есть бронь")
    guest_id = None
    if body.guest_phone:
        g = db.query(Guest).filter_by(phone=body.guest_phone).first()
        if not g:
            g = Guest(name=body.guest_name or "Гость", phone=body.guest_phone, venue_id=user.venue_id)
            db.add(g); db.flush()
        guest_id = g.id
    r = Reservation(table_id=t.id, guest_id=guest_id, date_start=ds, date_end=de,
                    deposit_amount=money(body.deposit_amount or 0), comment=body.comment)
    db.add(r)
    if t.status == "free": t.status = "reserved"
    log_event(db, user=user, event_type="reservation.created", object_type="reservation", object_id=r.id,
              amount=float(r.deposit_amount or 0))
    db.commit(); db.refresh(r)
    return {"id": r.id}

@router.post("/reservations/{rid}/cancel")
def cancel_reservation(rid: str, user: User = Depends(require_perm("hall:view")), db: Session = Depends(get_db)):
    r = db.get(Reservation, rid)
    if not r: raise HTTPException(404, "Бронь не найдена")
    r.status = "cancelled"
    t = db.get(Table, r.table_id)
    if t and t.status == "reserved": t.status = "free"
    log_event(db, user=user, event_type="reservation.cancelled", object_type="reservation", object_id=rid)
    db.commit()
    return {"ok": True}
