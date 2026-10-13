"""Заказы поставщикам: черновик → на согласование → согласование (с лимитом) → отмена; приёмка по заказу."""
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional, List
from ..deps import get_db, require_perm
from ..db import now_almaty, money, qty, new_id
from ..models import (PurchaseOrder, PurchaseOrderLine, Supplier, Product, GoodsReceipt, User, Venue)
from ..services.audit import log_event
from ..services.notify import notify

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])


def _po_out(po: PurchaseOrder, db: Session, with_lines=True):
    d = {"id": po.id, "number": po.number, "supplier_id": po.supplier_id,
         "supplier": db.get(Supplier, po.supplier_id).name if db.get(Supplier, po.supplier_id) else "",
         "status": po.status, "total": float(po.total),
         "expected_date": po.expected_date.isoformat() if po.expected_date else None,
         "comment": po.comment,
         "created_by": po.created_by, "approved_by": po.approved_by,
         "created_at": po.created_at.isoformat(sep=" ", timespec="seconds")}
    if with_lines:
        lines = []
        for ln in db.query(PurchaseOrderLine).filter_by(po_id=po.id).all():
            p = db.get(Product, ln.product_id)
            lines.append({"id": ln.id, "product_id": ln.product_id, "product": p.name if p else "",
                          "ordered_qty": float(ln.ordered_qty), "received_qty": float(ln.received_qty),
                          "price": float(ln.price), "uom": p.purchase_uom if p else ""})
        d["lines"] = lines
    return d


def _recalc_total(db, po):
    total = Decimal("0")
    for ln in db.query(PurchaseOrderLine).filter_by(po_id=po.id).all():
        total += Decimal(str(ln.ordered_qty)) * Decimal(str(ln.price))
    po.total = money(total)


class POLineIn(BaseModel):
    product_id: str
    qty: float = Field(gt=0)
    price: float = Field(ge=0)


class POIn(BaseModel):
    supplier_id: str
    expected_date: Optional[datetime] = None
    comment: Optional[str] = None
    lines: List[POLineIn]


@router.get("")
def list_pos(status: Optional[str] = None, supplier_id: Optional[str] = None,
             user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    q = db.query(PurchaseOrder)
    if status: q = q.filter(PurchaseOrder.status == status)
    if supplier_id: q = q.filter(PurchaseOrder.supplier_id == supplier_id)
    return [_po_out(po, db, with_lines=False) for po in q.order_by(PurchaseOrder.created_at.desc()).limit(200).all()]


@router.get("/{poid}")
def get_po(poid: str, user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, poid)
    if not po: raise HTTPException(404, "Заказ не найден")
    return _po_out(po, db)


@router.post("")
def create_po(body: POIn, user: User = Depends(require_perm("suppliers:manage")), db: Session = Depends(get_db)):
    sup = db.get(Supplier, body.supplier_id)
    if not sup or sup.archived: raise HTTPException(404, "Поставщик не найден")
    if not body.lines: raise HTTPException(400, "Добавьте позиции в заказ")
    number = f"PO-{now_almaty().strftime('%Y%m')}-{(db.query(PurchaseOrder).count() % 9000) + 1000}"
    po = PurchaseOrder(venue_id=user.venue_id, supplier_id=sup.id, number=number, status="draft",
                       expected_date=body.expected_date, comment=body.comment, created_by=user.id)
    db.add(po); db.flush()
    for ln in body.lines:
        p = db.get(Product, ln.product_id)
        if not p: raise HTTPException(400, f"Товар {ln.product_id} не найден")
        db.add(PurchaseOrderLine(po_id=po.id, product_id=p.id, ordered_qty=qty(ln.qty), price=money(ln.price)))
    _recalc_total(db, po)
    log_event(db, user=user, event_type="purchase_order.created", object_type="purchase_order", object_id=po.id,
              venue_id=user.venue_id, amount=float(po.total), new_values={"number": number, "supplier": sup.name})
    db.commit(); db.refresh(po)
    return _po_out(po, db)


class POLinePatch(BaseModel):
    id: Optional[str] = None
    product_id: Optional[str] = None
    qty: Optional[float] = Field(None, gt=0)
    price: Optional[float] = Field(None, ge=0)


@router.patch("/{poid}")
def patch_po(poid: str, body: dict, user: User = Depends(require_perm("suppliers:manage")), db: Session = Depends(get_db)):
    """Изменение только черновика/на согласовании. Позиции: replace через lines=[{id?,product_id,qty,price}]."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == poid).with_for_update().first()
    if not po: raise HTTPException(404, "Заказ не найден")
    if po.status not in ("draft", "pending_approval"):
        raise HTTPException(409, f"Заказ в статусе «{po.status}» изменять нельзя — отмените и создайте новый")
    if "lines" in body:
        keep = set()
        for ln in body["lines"]:
            if not ln.get("product_id") or not ln.get("qty"):
                raise HTTPException(400, "Каждая строка: product_id, qty, price")
            pol = db.get(PurchaseOrderLine, ln["id"]) if ln.get("id") else None
            if pol and pol.po_id != po.id:
                raise HTTPException(400, "Строка заказа не найдена")
            if pol:
                pol.ordered_qty = qty(ln["qty"]); pol.price = money(ln.get("price", 0))
                keep.add(pol.id)
            else:
                p = db.get(Product, ln["product_id"])
                if not p: raise HTTPException(400, "Товар не найден")
                new_ln = PurchaseOrderLine(po_id=po.id, product_id=p.id, ordered_qty=qty(ln["qty"]),
                                           price=money(ln.get("price", 0)))
                db.add(new_ln); db.flush(); keep.add(new_ln.id)
        for old_ln in db.query(PurchaseOrderLine).filter_by(po_id=po.id).all():
            if old_ln.id not in keep:
                if Decimal(str(old_ln.received_qty)) > 0:
                    raise HTTPException(409, "Нельзя удалить частично принятую строку")
                db.delete(old_ln)
        db.flush()
    for k in ("expected_date", "comment"):
        if k in body: setattr(po, k, body[k])
    _recalc_total(db, po)
    log_event(db, user=user, event_type="purchase_order.updated", object_type="purchase_order", object_id=po.id,
              amount=float(po.total))
    db.commit()
    return _po_out(po, db)


@router.post("/{poid}/submit")
def submit_po(poid: str, user: User = Depends(require_perm("suppliers:manage")), db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == poid).with_for_update().first()
    if not po: raise HTTPException(404, "Заказ не найден")
    if po.status != "draft": raise HTTPException(409, f"Статус «{po.status}» — отправка невозможна")
    if not db.query(PurchaseOrderLine).filter_by(po_id=po.id).count():
        raise HTTPException(400, "В заказе нет позиций")
    po.status = "pending_approval"
    log_event(db, user=user, event_type="purchase_order.submitted", object_type="purchase_order", object_id=po.id,
              amount=float(po.total))
    db.commit()
    return _po_out(po, db, with_lines=False)


class ApproveIn(BaseModel):
    approve: bool = True
    reason: Optional[str] = None


@router.post("/{poid}/approve")
def approve_po(poid: str, body: ApproveIn, user: User = Depends(require_perm("purchase:approve")),
               db: Session = Depends(get_db)):
    """Согласование с контролем лимита закупки за день (настройка purchase_daily_limit)."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == poid).with_for_update().first()
    if not po: raise HTTPException(404, "Заказ не найден")
    if po.status != "pending_approval":
        raise HTTPException(409, f"Ожидается статус «на согласовании», сейчас «{po.status}»")
    if not body.approve:
        if not (body.reason or "").strip():
            raise HTTPException(400, "Причина отказа обязательна")
        po.status = "cancelled"
        log_event(db, user=user, event_type="purchase_order.rejected", object_type="purchase_order",
                  object_id=po.id, amount=float(po.total), reason=body.reason)
        db.commit()
        return _po_out(po, db, with_lines=False)
    from ..models import Setting
    s = db.query(Setting).filter_by(key="purchase_daily_limit").first()
    limit = Decimal(str((s.value or {}).get("value", 0))) if s else Decimal("0")
    if limit > 0:
        today = now_almaty().replace(hour=0, minute=0, second=0, microsecond=0)
        approved_today = sum((Decimal(str(x)) for (x,) in
            db.query(PurchaseOrder.total).filter(PurchaseOrder.status.in_(["approved", "posted"]),
                                                 PurchaseOrder.created_at >= today).all()), Decimal("0"))
        if approved_today + Decimal(str(po.total)) > limit:
            notify(db, etype="purchase.over_limit", title=f"Заказ {po.number} превышает дневной лимит закупок",
                   body=f"Сумма {po.total} ₸; согласовано сегодня {approved_today} ₸; лимит {limit} ₸.",
                   severity="warn", link_type="purchase_order", link_id=po.id, amount=float(po.total))
            raise HTTPException(409, f"Превышен дневной лимит закупок ({limit} ₸). Согласование заблокировано.")
    po.status = "approved"; po.approved_by = user.id
    log_event(db, user=user, event_type="purchase_order.approved", object_type="purchase_order", object_id=po.id,
              amount=float(po.total), approved_by=user.id)
    db.commit()
    return _po_out(po, db, with_lines=False)


@router.post("/{poid}/cancel")
def cancel_po(poid: str, reason: str = "", user: User = Depends(require_perm("suppliers:manage")),
              db: Session = Depends(get_db)):
    if not reason.strip(): raise HTTPException(400, "Укажите причину отмены")
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == poid).with_for_update().first()
    if not po: raise HTTPException(404, "Заказ не найден")
    if po.status == "posted":
        raise HTTPException(409, "Принятый складом заказ нельзя отменить — оформите возврат поставщику")
    if po.status == "cancelled": raise HTTPException(409, "Уже отменён")
    old = po.status; po.status = "cancelled"
    log_event(db, user=user, event_type="purchase_order.cancelled", object_type="purchase_order", object_id=po.id,
              old_values={"status": old}, reason=reason)
    db.commit()
    return _po_out(po, db, with_lines=False)


@router.get("/{poid}/receipts")
def po_receipts(poid: str, user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    rows = db.query(GoodsReceipt).filter_by(po_id=poid).order_by(GoodsReceipt.created_at).all()
    return [{"id": r.id, "number": r.number, "status": r.status, "total": float(r.total),
             "date": r.created_at.isoformat(sep=" ", timespec="seconds")} for r in rows]
