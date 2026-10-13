"""Заказы: создание, позиции, скидки, разделение, перенос, объединение, отмена, статусы."""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional, List
from ..deps import get_db, require_perm
from ..db import now_almaty, money
from ..models import (Order, OrderItem, MenuItem, ItemModifierRec, Modifier, OrderSplit, OrderStatusHistory,
                      KitchenTicket, Table, Venue, Shift, SaleDiscount, User)
from ..services.audit import log_event
from ..services.pricing import recompute_order
from ..services import order_service
from ..services.idempotency import check_key, save_result

router = APIRouter(prefix="/orders", tags=["orders"])


def _order_out(o: Order, items=None, db=None):
    d = {"id": o.id, "number": o.number, "status": o.status, "table_id": o.table_id,
         "waiter_id": o.waiter_id, "shift_id": o.shift_id, "venue_id": o.venue_id,
         "subtotal": float(o.subtotal), "discount_total": float(o.discount_total),
         "total": float(o.total), "paid_amount": float(o.paid_amount),
         "refund_amount": float(o.refund_amount), "tip_amount": float(o.tip_amount),
         "cost_amount": float(o.cost_amount), "version": o.version,
         "created_at": o.created_at.isoformat(sep=" ", timespec="seconds"),
         "cancel_reason": o.cancel_reason, "comment": o.comment}
    if items is not None:
        d["items"] = [{"id": i.id, "item_id": i.item_id, "name": i.name_snapshot, "qty": float(i.qty),
                       "unit_price": float(i.unit_price), "mods_total": float(i.mods_total),
                       "discount_pct": float(i.discount_pct), "line_total": float(i.line_total),
                       "status": i.status, "deleted": i.deleted, "course": i.course,
                       "comment": i.comment, "split_id": i.split_id,
                       "modifiers": [{"name": m.name_snapshot, "qty": float(m.qty), "price_add": float(m.price_add)}
                                     for m in db.query(ItemModifierRec).filter_by(order_item_id=i.id).all()]}
                      for i in items]
        d["splits"] = [{"id": s.id, "label": s.label, "share_amount": float(s.share_amount),
                        "paid_amount": float(s.paid_amount)}
                       for s in db.query(OrderSplit).filter_by(order_id=o.id).all()]
        d["history"] = [{"old": h.old_status, "new": h.new_status,
                         "at": h.created_at.isoformat(sep=" ", timespec="seconds"), "note": h.note}
                        for h in db.query(OrderStatusHistory).filter_by(order_id=o.id)
                                   .order_by(OrderStatusHistory.created_at).all()]
    return d


def active_items(db, order_id):
    return (db.query(OrderItem).filter(OrderItem.order_id == order_id, OrderItem.deleted == False)
            .order_by(OrderItem.created_at).all())


def full_recalc(db, order):
    """Единая точка пересчёта сумм заказа. Скидка на заказ — сумма записей SaleDiscount уровня заказа."""
    items = [i for i in active_items(db, order.id) if i.status != "cancelled"]
    sd = db.query(SaleDiscount).filter_by(order_id=order.id, order_item_id=None).all()
    od = sum((Decimal(str(x.amount)) for x in sd), Decimal("0"))
    recompute_order(order, items, od)
    db.flush()


class CreateIn(BaseModel):
    table_id: Optional[str] = None
    register_id: Optional[str] = None
    waiter_id: Optional[str] = None
    guest_id: Optional[str] = None
    venue_id: Optional[str] = None
    comment: Optional[str] = None


class ItemIn(BaseModel):
    item_id: str
    qty: float = Field(1, gt=0, le=999)
    modifiers: List[str] = []
    comment: Optional[str] = None
    split_id: Optional[str] = None
    course: int = 1


class ItemPatch(BaseModel):
    qty: Optional[float] = Field(None, gt=0, le=999)
    price: Optional[float] = Field(None, ge=0)
    discount_pct: Optional[float] = Field(None, ge=0, le=100)
    comment: Optional[str] = None
    version: Optional[int] = None


@router.post("")
def create_order(body: CreateIn, user: User = Depends(require_perm("orders:create")),
                 db: Session = Depends(get_db)):
    venue_id = body.venue_id or user.venue_id
    if not venue_id:
        v = db.query(Venue).first()
        venue_id = v.id if v else None
    if not venue_id:
        raise HTTPException(400, "Не задано заведение")
    last = db.query(Order).with_entities(Order.number).order_by(Order.number.desc()).first()
    number = (last[0] or 1000) + 1
    shift = db.query(Shift).filter(Shift.status == "open").first()
    o = Order(number=number, venue_id=venue_id, status="open",
              table_id=body.table_id, waiter_id=body.waiter_id or user.id,
              guest_id=body.guest_id, register_id=body.register_id,
              shift_id=shift.id if shift else None, comment=body.comment)
    if body.table_id:
        t = db.get(Table, body.table_id)
        if t:
            t.status = "occupied"; t.waiter_id = o.waiter_id
    db.add(o); db.flush()
    db.add(OrderStatusHistory(order_id=o.id, old_status=None, new_status="open", by_user=user.id))
    log_event(db, user=user, event_type="order.created", object_type="order", object_id=o.id, venue_id=venue_id)
    db.commit(); db.refresh(o)
    return _order_out(o, active_items(db, o.id), db)


@router.get("")
def list_orders(status: Optional[str] = None, table_id: Optional[str] = None, waiter_id: Optional[str] = None,
                number: Optional[int] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                limit: int = 100, offset: int = 0,
                user: User = Depends(require_perm("orders:view")), db: Session = Depends(get_db)):
    qs = db.query(Order)
    if status: qs = qs.filter(Order.status == status)
    if table_id: qs = qs.filter(Order.table_id == table_id)
    if waiter_id: qs = qs.filter(Order.waiter_id == waiter_id)
    if number: qs = qs.filter(Order.number == number)
    if date_from: qs = qs.filter(Order.created_at >= date_from)
    if date_to: qs = qs.filter(Order.created_at <= date_to + " 23:59:59")
    rows = qs.order_by(Order.created_at.desc()).offset(offset).limit(limit).all()
    return [_order_out(o) for o in rows]


@router.get("/{oid}")
def get_order(oid: str, user: User = Depends(require_perm("orders:view")), db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    if not o: raise HTTPException(404, "Заказ не найден")
    return _order_out(o, active_items(db, o.id), db)


@router.post("/{oid}/items")
def add_item(oid: str, body: ItemIn, user: User = Depends(require_perm("orders:create")),
             db: Session = Depends(get_db), idem_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    holder = check_key(db, idem_key, {"o": oid, **body.model_dump()})
    if holder and holder.get("replayed"):
        return holder["response"]
    o = db.get(Order, oid)
    if not o: raise HTTPException(404, "Заказ не найден")
    if o.status in ("closed", "cancelled", "paid"):
        raise HTTPException(409, f"Нельзя добавлять позиции в заказ со статусом {o.status}")
    mi = db.get(MenuItem, body.item_id)
    if not mi or mi.archived: raise HTTPException(400, "Позиция меню не найдена")
    if mi.unavailable_until and mi.unavailable_until > now_almaty():
        raise HTTPException(409, f"«{mi.name}» временно снят с продажи")
    oi = OrderItem(order_id=o.id, item_id=mi.id, name_snapshot=mi.name, qty=body.qty,
                   unit_price=mi.price, comment=body.comment, course=body.course, split_id=body.split_id)
    mods_total = Decimal("0")
    db.add(oi); db.flush()
    for mid in body.modifiers:
        mod = db.get(Modifier, mid)
        if not mod: continue
        db.add(ItemModifierRec(order_item_id=oi.id, modifier_id=mod.id, name_snapshot=mod.name,
                               qty=1, price_add=mod.price_add, product_id=mod.product_id))
        mods_total += Decimal(str(mod.price_add))
    oi.mods_total = money(mods_total)
    if o.status == "created": o.status = "open"
    full_recalc(db, o)
    log_event(db, user=user, event_type="order.item_added", object_type="order", object_id=o.id,
              venue_id=o.venue_id, new_values={"item": mi.name, "qty": body.qty}, amount=float(oi.line_total))
    db.commit()
    res = _order_out(o, active_items(db, o.id), db)
    save_result(db, holder, res); db.commit()
    return res


@router.patch("/items/{item_id}")
def patch_item(item_id: str, body: ItemPatch, user: User = Depends(require_perm("orders:edit")),
               db: Session = Depends(get_db)):
    oi = db.get(OrderItem, item_id)
    if not oi: raise HTTPException(404, "Позиция не найдена")
    o = db.get(Order, oi.order_id)
    if o.status in ("closed", "cancelled", "paid"):
        raise HTTPException(409, "Заказ закрыт/оплачен — изменение невозможно")
    if body.version is not None and body.version != o.version:
        raise HTTPException(409, "Заказ изменён другим сотрудником — обновите данные")
    old = {"qty": float(oi.qty), "price": float(oi.unit_price), "discount_pct": float(oi.discount_pct)}
    if body.qty is not None:
        oi.qty = body.qty
        log_event(db, user=user, event_type="order.qty_changed", object_type="order_item", object_id=oi.id,
                  venue_id=o.venue_id, old_values=old, new_values={"qty": body.qty})
    if body.price is not None:
        from ..rbac import has_perm
        if not has_perm(user, db, "orders:price_override"):
            raise HTTPException(403, "Изменение цены требует права orders:price_override")
        oi.unit_price = money(body.price)
        log_event(db, user=user, event_type="order.price_changed", object_type="order_item", object_id=oi.id,
                  venue_id=o.venue_id, old_values=old, new_values={"price": float(oi.unit_price)},
                  reason="ручная замена цены")
    if body.discount_pct is not None:
        if body.discount_pct > float(user.discount_limit_pct or 0) and user.role not in ("owner", "manager", "shift_admin"):
            raise HTTPException(403, f"Скидка {body.discount_pct}% превышает ваш лимит {float(user.discount_limit_pct or 0)}%")
        oi.discount_pct = body.discount_pct
        db.add(SaleDiscount(order_id=o.id, order_item_id=oi.id, pct=body.discount_pct,
                            amount=0, reason="скидка на позицию", by_user=user.id))
        log_event(db, user=user, event_type="order.discount", object_type="order_item", object_id=oi.id,
                  venue_id=o.venue_id, new_values={"pct": body.discount_pct})
    if body.comment is not None:
        oi.comment = body.comment
    full_recalc(db, o)
    o.version += 1
    db.commit()
    return _order_out(o, active_items(db, o.id), db)


@router.delete("/items/{item_id}")
def delete_item(item_id: str, reason: str = "", user: User = Depends(require_perm("orders:edit")),
                db: Session = Depends(get_db)):
    if not reason.strip():
        raise HTTPException(400, "Укажите причину удаления позиции")
    oi = db.get(OrderItem, item_id)
    if not oi: raise HTTPException(404, "Позиция не найдена")
    o = db.get(Order, oi.order_id)
    if o.status in ("closed", "cancelled", "paid"):
        raise HTTPException(409, "Заказ закрыт/оплачен — удаление невозможно")
    if oi.status in ("sent", "cooking", "ready"):
        # отмена после передачи на кухню — с сохранением истории и событием для KDS
        oi.status = "cancelled"; oi.cancel_reason = reason
        for kt in db.query(KitchenTicket).filter(KitchenTicket.order_item_id == oi.id,
                                                 KitchenTicket.status != "cancelled").all():
            kt.status = "cancelled"; kt.cancel_reason = reason
        log_event(db, user=user, event_type="order.item_cancelled_after_send", object_type="order_item",
                  object_id=oi.id, venue_id=o.venue_id, reason=reason, amount=float(oi.line_total))
    else:
        oi.deleted = True
        log_event(db, user=user, event_type="order.item_deleted", object_type="order_item", object_id=oi.id,
                  venue_id=o.venue_id, old_values={"item": oi.name_snapshot, "qty": float(oi.qty)}, reason=reason)
    full_recalc(db, o)
    o.version += 1
    db.commit()
    return _order_out(o, active_items(db, o.id), db)


class DiscountIn(BaseModel):
    pct: Optional[float] = Field(None, ge=0, le=100)
    amount: Optional[float] = Field(None, ge=0)
    reason: str
    confirm_login: Optional[str] = None
    confirm_password: Optional[str] = None


@router.post("/{oid}/discount")
def order_discount(oid: str, body: DiscountIn, user: User = Depends(require_perm("orders:discount")),
                   db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    if o.status in ("closed", "cancelled", "paid"):
        raise HTTPException(409, "Скидка недоступна для этого статуса заказа")
    if not body.reason.strip(): raise HTTPException(400, "Причина скидки обязательна")
    if body.pct is not None:
        amt = money(Decimal(str(o.subtotal)) * Decimal(str(body.pct)) / 100)
    elif body.amount is not None:
        amt = money(body.amount)
    else:
        raise HTTPException(400, "Укажите pct или amount")
    pct = float(amt / Decimal(str(o.subtotal)) * 100) if o.subtotal else 0
    approved_by = None
    over_limit = pct > float(user.discount_limit_pct or 0) and user.role not in ("owner", "manager")
    if over_limit:
        if not (body.confirm_login and body.confirm_password):
            raise HTTPException(403, "Скидка выше лимита — требуется подтверждение руководителя (логин+пароль)")
        sup = db.query(User).filter_by(login=body.confirm_login).first()
        from ..security import verify_password
        from ..rbac import has_perm
        if not sup or not verify_password(body.confirm_password, sup.password_hash) or not has_perm(sup, db, "orders:discount"):
            raise HTTPException(403, "Подтверждение не пройдено")
        approved_by = sup.id
    sd = SaleDiscount(order_id=o.id, pct=pct, amount=amt, reason=body.reason, by_user=user.id, approved_by=approved_by)
    db.add(sd)
    full_recalc(db, o)
    o.version += 1
    log_event(db, user=user, event_type="order.discount", object_type="order", object_id=o.id, venue_id=o.venue_id,
              amount=float(amt), reason=body.reason, approved_by=approved_by, new_values={"pct": round(pct, 2)})
    if over_limit:
        from ..services.notify import notify
        notify(db, etype="discount.over_limit", title=f"Скидка сверх лимита в заказе №{o.number}",
               body=f"{user.name}: {round(pct, 1)}% ({amt} ₸). Причина: {body.reason}", severity="warn",
               link_type="order", link_id=o.id, amount=float(amt))
    db.commit()
    return _order_out(o, active_items(db, o.id), db)


class SplitIn(BaseModel):
    labels: List[str]


@router.post("/{oid}/split")
def split_bill(oid: str, body: SplitIn, user: User = Depends(require_perm("orders:split")),
               db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    if len(body.labels) < 2: raise HTTPException(400, "Нужно минимум 2 части")
    for s in db.query(OrderSplit).filter_by(order_id=o.id).all():
        db.delete(s)
    db.flush()
    share = money(Decimal(str(o.total)) / len(body.labels))
    splits = []
    for lab in body.labels:
        sp = OrderSplit(order_id=o.id, label=lab, share_amount=share)
        db.add(sp); splits.append(sp)
    db.flush()
    diff = Decimal(str(o.total)) - sum(Decimal(str(s.share_amount)) for s in splits)
    splits[-1].share_amount = money(Decimal(str(splits[-1].share_amount)) + diff)
    log_event(db, user=user, event_type="order.split", object_type="order", object_id=o.id,
              new_values={"parts": body.labels})
    db.commit()
    return _order_out(o, active_items(db, o.id), db)


class TransferIn(BaseModel):
    to_table_id: str


@router.post("/{oid}/transfer")
def transfer_table(oid: str, body: TransferIn, user: User = Depends(require_perm("orders:transfer")),
                   db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    if o.status in ("closed", "cancelled", "paid"):
        raise HTTPException(409, "Перенос недоступен для этого статуса")
    old_table = o.table_id
    nt = db.get(Table, body.to_table_id)
    if not nt: raise HTTPException(404, "Столик не найден")
    conflict = db.query(Order).filter(Order.table_id == nt.id,
                                      Order.status.notin_(["closed", "cancelled"]),
                                      Order.id != o.id).first()
    if conflict:
        raise HTTPException(409, f"На столе «{nt.name}» уже есть открытый заказ №{conflict.number} — используйте объединение")
    o.table_id = nt.id; nt.status = "occupied"; nt.waiter_id = o.waiter_id
    if old_table:
        ot = db.get(Table, old_table)
        if ot and not db.query(Order).filter(Order.table_id == ot.id,
                                             Order.status.notin_(["closed", "cancelled"])).first():
            ot.status = "cleaning"
    log_event(db, user=user, event_type="order.transferred", object_type="order", object_id=o.id,
              old_values={"table": old_table}, new_values={"table": nt.id})
    db.commit()
    return _order_out(o, active_items(db, o.id), db)


class MergeIn(BaseModel):
    with_order_id: str


@router.post("/{oid}/merge")
def merge_orders(oid: str, body: MergeIn, user: User = Depends(require_perm("orders:transfer")),
                 db: Session = Depends(get_db)):
    a = db.get(Order, oid); b = db.get(Order, body.with_order_id)
    if not a or not b: raise HTTPException(404, "Заказ не найден")
    for o in (a, b):
        if o.status in ("closed", "cancelled", "paid"):
            raise HTTPException(409, "Объединение недоступно для закрытых/оплаченных заказов")
    for it in active_items(db, b.id):
        it.order_id = a.id
    full_recalc(db, a)
    old_b = b.status
    b.status = "cancelled"; b.cancel_reason = f"Объединён с заказом №{a.number}"
    db.add(OrderStatusHistory(order_id=b.id, old_status=old_b, new_status="cancelled", by_user=user.id,
                              note=f"merge → №{a.number}"))
    if b.table_id:
        t = db.get(Table, b.table_id)
        if t: t.status = "cleaning"
    log_event(db, user=user, event_type="order.merged", object_type="order", object_id=a.id,
              new_values={"merged_from": b.number})
    db.commit()
    return _order_out(a, active_items(db, a.id), db)


@router.post("/{oid}/prebill")
def prebill(oid: str, user: User = Depends(require_perm("orders:view")), db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    if o.status in ("closed", "cancelled", "paid"):
        raise HTTPException(409, "Предварительный счёт недоступен для этого статуса")
    o.prebill_printed_at = now_almaty()
    if o.status in ("sent", "cooking", "ready"):
        order_service.check_transition(o, "awaiting_payment")
        old = o.status; o.status = "awaiting_payment"
        db.add(OrderStatusHistory(order_id=o.id, old_status=old, new_status="awaiting_payment", by_user=user.id))
    log_event(db, user=user, event_type="order.prebill", object_type="order", object_id=o.id)
    db.commit()
    return {"ok": True, "printed_at": o.prebill_printed_at.isoformat()}


class SendIn(BaseModel):
    all: bool = True
    item_ids: List[str] = []


@router.post("/{oid}/send")
def send_to_kitchen(oid: str, body: SendIn, user: User = Depends(require_perm("orders:edit")),
                    db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    if o.status not in ("created", "open", "sent", "cooking", "ready"):
        raise HTTPException(409, f"Отправка невозможна из статуса {o.status}")
    items = [i for i in active_items(db, o.id) if i.status == "new" and (body.all or i.id in body.item_ids)]
    if not items: raise HTTPException(400, "Нет новых позиций для отправки")
    for oi in items:
        oi.status = "sent"
        mi = db.get(MenuItem, oi.item_id)
        db.add(KitchenTicket(order_id=o.id, order_item_id=oi.id, station_id=mi.station_id if mi else None))
    old = o.status
    if o.status in ("created", "open"):
        o.status = "sent"
        db.add(OrderStatusHistory(order_id=o.id, old_status=old, new_status="sent", by_user=user.id))
    log_event(db, user=user, event_type="order.sent_to_kitchen", object_type="order", object_id=o.id,
              new_values={"positions": len(items)})
    db.commit()
    return {"tickets": len(items)}


class CancelIn(BaseModel):
    reason: str
    confirm_login: Optional[str] = None
    confirm_password: Optional[str] = None


@router.post("/{oid}/cancel")
def cancel_order(oid: str, body: CancelIn, user: User = Depends(require_perm("orders:cancel")),
                 db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    if o.status == "closed":
        raise HTTPException(409, "Закрытый заказ нельзя отменить — оформите возврат")
    if o.status == "cancelled":
        raise HTTPException(409, "Заказ уже отменён")
    if not body.reason.strip():
        raise HTTPException(400, "Причина отмены обязательна")
    approved_by = None
    if user.role in ("cashier", "waiter", "bartender"):
        if not (body.confirm_login and body.confirm_password):
            raise HTTPException(403, "Отмена требует подтверждения старшего смены")
        from ..security import verify_password
        from ..rbac import has_perm
        sup = db.query(User).filter_by(login=body.confirm_login).first()
        if not sup or not verify_password(body.confirm_password, sup.password_hash) or not has_perm(sup, db, "orders:cancel"):
            raise HTTPException(403, "Подтверждение не пройдено")
        approved_by = sup.id
    old = o.status
    o.status = "cancelled"; o.cancel_reason = body.reason; o.cancelled_by = user.id
    db.add(OrderStatusHistory(order_id=o.id, old_status=old, new_status="cancelled", by_user=user.id, note=body.reason))
    for kt in db.query(KitchenTicket).filter(KitchenTicket.order_id == o.id,
                                             KitchenTicket.status.in_(["pending", "cooking"])).all():
        kt.status = "cancelled"; kt.cancel_reason = "Отмена заказа"
    if o.table_id:
        t = db.get(Table, o.table_id)
        if t: t.status = "cleaning"
    log_event(db, user=user, event_type="order.cancelled", object_type="order", object_id=o.id, venue_id=o.venue_id,
              amount=float(o.total), reason=body.reason, approved_by=approved_by, old_values={"status": old})
    from ..services.notify import notify
    notify(db, etype="order.cancel", title=f"Отменён заказ №{o.number}",
           body=f"{user.name}. Сумма {float(o.total)} ₸. Причина: {body.reason}", severity="warn",
           link_type="order", link_id=o.id, amount=float(o.total))
    db.commit()
    return _order_out(o, active_items(db, o.id), db)


@router.post("/{oid}/close")
def close_order(oid: str, user: User = Depends(require_perm("payments:create")), db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    if o.status != "paid":
        raise HTTPException(409, "Закрывать можно только полностью оплаченный заказ")
    old = o.status
    o.status = "closed"; o.closed_at = now_almaty()
    db.add(OrderStatusHistory(order_id=o.id, old_status=old, new_status="closed", by_user=user.id))
    if o.table_id:
        t = db.get(Table, o.table_id)
        if t: t.status = "cleaning"
    log_event(db, user=user, event_type="order.closed", object_type="order", object_id=o.id, amount=float(o.total))
    db.commit()
    return _order_out(o, active_items(db, o.id), db)
