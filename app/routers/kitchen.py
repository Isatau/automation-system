"""Кухня/бар: KDS-очереди по станциям, статусы приготовления, перепечатка, приоритеты."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List
from ..deps import get_db, require_perm
from ..db import now_almaty
from ..models import KitchenTicket, OrderItem, Order, Station, User, SaleDiscount
from ..services.audit import log_event

router = APIRouter(prefix="/kitchen", tags=["kitchen"])


def _ticket_out(kt: KitchenTicket, db):
    oi = db.get(OrderItem, kt.order_item_id)
    o = db.get(Order, kt.order_id)
    mods = []
    if oi:
        from ..models import ItemModifierRec
        mods = [m.name_snapshot for m in db.query(ItemModifierRec).filter_by(order_item_id=oi.id).all()]
    st = db.get(Station, kt.station_id) if kt.station_id else None
    age_min = int((now_almaty() - kt.sent_at).total_seconds() // 60)
    overdue = age_min > (st.cook_time_norm_min if st else 15) and kt.status in ("pending", "cooking")
    return {"id": kt.id, "order_number": o.number if o else None, "table_id": o.table_id if o else None,
            "item_name": oi.name_snapshot if oi else "", "qty": float(oi.qty) if oi else 0,
            "comment": oi.comment if oi else None, "modifiers": mods, "course": oi.course if oi else 1,
            "status": kt.status, "priority": kt.priority, "station_id": kt.station_id,
            "sent_at": kt.sent_at.isoformat(sep=" ", timespec="minutes"),
            "started_at": kt.started_at.isoformat(sep=" ", timespec="minutes") if kt.started_at else None,
            "done_at": kt.done_at.isoformat(sep=" ", timespec="minutes") if kt.done_at else None,
            "age_min": age_min, "overdue": overdue, "reprint_no": kt.reprint_no,
            "cancel_reason": kt.cancel_reason}


@router.get("/tickets")
def queue(station_id: Optional[str] = None, status: Optional[str] = None,
          user: User = Depends(require_perm("kitchen:view")), db: Session = Depends(get_db)):
    q = db.query(KitchenTicket)
    if station_id: q = q.filter(KitchenTicket.station_id == station_id)
    if status: q = q.filter(KitchenTicket.status == status)
    else: q = q.filter(KitchenTicket.status.in_(["pending", "cooking"]))
    tks = q.order_by(KitchenTicket.priority.desc(), KitchenTicket.sent_at).limit(300).all()
    return [_ticket_out(t, db) for t in tks]


class StatusIn(BaseModel):
    reason: Optional[str] = None

@router.post("/tickets/{tid}/start")
def start(tid: str, user: User = Depends(require_perm("kitchen:status")), db: Session = Depends(get_db)):
    kt = db.get(KitchenTicket, tid)
    if not kt: raise HTTPException(404, "Тикет не найден")
    if kt.status != "pending": raise HTTPException(409, f"Статус {kt.status}: старт невозможен")
    kt.status = "cooking"; kt.started_at = now_almaty()
    oi = db.get(OrderItem, kt.order_item_id)
    if oi: oi.status = "cooking"
    o = db.get(Order, kt.order_id)
    if o and o.status == "sent": o.status = "cooking"
    log_event(db, user=user, event_type="kitchen.started", object_type="kitchen_ticket", object_id=tid)
    db.commit()
    return _ticket_out(kt, db)

@router.post("/tickets/{tid}/done")
def done(tid: str, user: User = Depends(require_perm("kitchen:status")), db: Session = Depends(get_db)):
    kt = db.get(KitchenTicket, tid)
    if not kt: raise HTTPException(404, "Тикет не найден")
    if kt.status not in ("pending", "cooking"): raise HTTPException(409, f"Статус {kt.status}")
    kt.status = "done"; kt.done_at = now_almaty()
    oi = db.get(OrderItem, kt.order_item_id)
    if oi: oi.status = "ready"
    # уведомление официанту о готовности + смена статуса заказа, если всё готово
    o = db.get(Order, kt.order_id)
    if o:
        pending = db.query(KitchenTicket).filter(KitchenTicket.order_id == o.id,
                                                 KitchenTicket.status.in_(["pending", "cooking"])).count()
        if pending == 0 and o.status in ("sent", "cooking"):
            o.status = "ready"
        from ..services.notify import notify
        notify(db, etype="kitchen.ready", title=f"Готово: заказ №{o.number}",
               body=f"«{oi.name_snapshot if oi else ''}» — позиция готова", severity="info",
               link_type="order", link_id=o.id, user_id=o.waiter_id)
    log_event(db, user=user, event_type="kitchen.done", object_type="kitchen_ticket", object_id=tid)
    db.commit()
    return _ticket_out(kt, db)

@router.post("/tickets/{tid}/cancel")
def cancel(tid: str, body: StatusIn, user: User = Depends(require_perm("kitchen:status")), db: Session = Depends(get_db)):
    kt = db.get(KitchenTicket, tid)
    if not kt: raise HTTPException(404, "Тикет не найден")
    if kt.status in ("done", "cancelled"): raise HTTPException(409, f"Статус {kt.status}")
    if not (body.reason or "").strip():
        raise HTTPException(400, "Причина отмены обязательна")
    was_cooking = kt.status == "cooking"
    kt.status = "cancelled"; kt.cancel_reason = body.reason
    oi = db.get(OrderItem, kt.order_item_id)
    if oi:
        oi.status = "cancelled"; oi.cancel_reason = body.reason
        o = db.get(Order, kt.order_id)
        from ..services.pricing import recompute_order
        from ..routers.orders import active_items
        items = [i for i in active_items(db, o.id) if i.status != "cancelled"]
        sd_amts = db.query(SaleDiscount).filter_by(order_id=o.id, order_item_id=None).all()
        from decimal import Decimal
        recompute_order(o, items, sum((Decimal(str(x.amount)) for x in sd_amts), Decimal("0")))
        o.version += 1
    log_event(db, user=user, event_type="kitchen.ticket_cancelled", object_type="kitchen_ticket", object_id=tid,
              reason=body.reason, new_values={"was_cooking": was_cooking},
              amount=float(oi.line_total) if oi else None)
    db.commit()
    return _ticket_out(kt, db)


class PriorityIn(BaseModel):
    priority: int

@router.post("/tickets/{tid}/priority")
def set_priority(tid: str, body: PriorityIn, user: User = Depends(require_perm("kitchen:status")), db: Session = Depends(get_db)):
    kt = db.get(KitchenTicket, tid)
    if not kt: raise HTTPException(404, "Тикет не найден")
    kt.priority = max(0, min(body.priority, 9))
    log_event(db, user=user, event_type="kitchen.priority", object_type="kitchen_ticket", object_id=tid,
              new_values={"priority": kt.priority})
    db.commit()
    return _ticket_out(kt, db)


@router.post("/tickets/{tid}/reprint")
def reprint(tid: str, user: User = Depends(require_perm("kitchen:view")), db: Session = Depends(get_db)):
    """Повторная печать с отметкой «КОПИЯ №n». Успех — только при подтверждении принтера."""
    kt = db.get(KitchenTicket, tid)
    if not kt: raise HTTPException(404, "Тикет не найден")
    kt.reprint_no += 1; kt.copy_flag = True
    oi = db.get(OrderItem, kt.order_item_id)
    text = f"=== КОПИЯ №{kt.reprint_no} ===\nЗаказ №{db.get(Order, kt.order_id).number}\n{oi.name_snapshot if oi else ''} x{float(oi.qty) if oi else 1}"
    from ..integrations.printer import PrinterAdapter
    st = db.get(Station, kt.station_id) if kt.station_id else None
    printer_name = (st.printer_config or {}).get("printer", "") if st else ""
    adapter = PrinterAdapter(printer_name)
    rec, res = adapter.run(db, "print_kitchen", {"text": text}, ref_id=kt.id)
    log_event(db, user=user, event_type="kitchen.reprint", object_type="kitchen_ticket", object_id=tid,
              new_values={"reprint_no": kt.reprint_no, "printed": res.confirmed_by_device})
    db.commit()
    return {"reprint_no": kt.reprint_no, "printed": res.confirmed_by_device,
            "error": None if res.ok or res.confirmed_by_device else res.error}
