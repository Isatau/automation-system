"""Склад: товары, склады, остатки, приход, перемещения, списания, возвраты поставщику, инвентаризация."""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional, List
from ..deps import get_db, require_perm
from ..db import now_almaty, money, qty, new_id
from ..models import (Warehouse, Product, StockBalance, StockMovement, StockLot, Supplier, GoodsReceipt,
                      GoodsReceiptLine, WriteOff, Inventory, InventoryLine, PurchaseOrder, PurchaseOrderLine, User)
from ..services.audit import log_event
from ..services.stock_service import post_movement, get_balance

router = APIRouter(tags=["warehouse"])


class ProductIn(BaseModel):
    name: str
    article: str
    base_uom: str = "kg"
    purchase_uom: str = "pcs"
    coef: float = Field(1, gt=0)
    min_stock: float = 0
    barcode: Optional[str] = None
    warehouse_id: Optional[str] = None

@router.get("/products")
def list_products(q: Optional[str] = None, archived: bool = False, user: User = Depends(require_perm("warehouse:view")),
                  db: Session = Depends(get_db)):
    qs = db.query(Product).filter(Product.archived == archived)
    if q: qs = qs.filter(Product.name.ilike(f"%{q}%") | Product.article.ilike(f"%{q}%"))
    return [{"id": p.id, "name": p.name, "article": p.article, "base_uom": p.base_uom,
             "purchase_uom": p.purchase_uom, "coef": float(p.coef), "min_stock": float(p.min_stock),
             "barcode": p.barcode, "last_purchase_price": float(p.last_purchase_price)} for p in qs.limit(500).all()]

@router.post("/products")
def create_product(body: ProductIn, user: User = Depends(require_perm("warehouse:receipt")), db: Session = Depends(get_db)):
    vid = user.venue_id
    if db.query(Product).filter_by(venue_id=vid, article=body.article).first():
        raise HTTPException(409, "Артикул уже существует")
    wh = body.warehouse_id or (db.query(Warehouse).filter_by(venue_id=vid).first().id
                               if db.query(Warehouse).filter_by(venue_id=vid).count() else None)
    p = Product(**body.model_dump(), venue_id=vid, warehouse_id=wh)
    db.add(p); db.flush()
    if wh: get_balance(db, wh, p.id)
    log_event(db, user=user, event_type="warehouse.product_created", object_type="product", object_id=p.id,
              new_values={"name": p.name})
    db.commit(); db.refresh(p)
    return {"id": p.id}

@router.patch("/products/{pid}")
def patch_product(pid: str, body: dict, user: User = Depends(require_perm("warehouse:receipt")), db: Session = Depends(get_db)):
    p = db.get(Product, pid)
    if not p: raise HTTPException(404, "Товар не найден")
    allowed = {"name", "base_uom", "purchase_uom", "coef", "min_stock", "barcode", "track_lots"}
    old = {k: getattr(p, k) for k in body if k in allowed}
    for k, v in body.items():
        if k in allowed: setattr(p, k, v)
    log_event(db, user=user, event_type="warehouse.product_updated", object_type="product", object_id=pid,
              old_values={k: str(v) for k, v in old.items()}, new_values={k: str(v) for k, v in body.items() if k in allowed})
    db.commit()
    return {"ok": True}

@router.delete("/products/{pid}")
def archive_product(pid: str, user: User = Depends(require_perm("warehouse:receipt")), db: Session = Depends(get_db)):
    """Архивирование вместо удаления — история движений сохраняется."""
    p = db.get(Product, pid)
    if not p: raise HTTPException(404, "Товар не найден")
    p.archived = True
    log_event(db, user=user, event_type="warehouse.product_archived", object_type="product", object_id=pid)
    db.commit()
    return {"ok": True}


@router.get("/warehouses")
def list_warehouses(user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    return [{"id": w.id, "name": w.name} for w in db.query(Warehouse).filter_by(venue_id=user.venue_id, archived=False).all()]


class WhIn(BaseModel):
    name: str

@router.post("/warehouses")
def create_wh(body: WhIn, user: User = Depends(require_perm("warehouse:receipt")), db: Session = Depends(get_db)):
    w = Warehouse(venue_id=user.venue_id, name=body.name)
    db.add(w); db.commit(); db.refresh(w)
    log_event(db, user=user, event_type="warehouse.created", object_type="warehouse", object_id=w.id)
    return {"id": w.id}


@router.get("/stock")
def stock(warehouse_id: Optional[str] = None, product_id: Optional[str] = None,
          user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    q = db.query(StockBalance)
    if warehouse_id: q = q.filter(StockBalance.warehouse_id == warehouse_id)
    if product_id: q = q.filter(StockBalance.product_id == product_id)
    out = []
    for b in q.all():
        p = db.get(Product, b.product_id)
        if not p: continue
        out.append({"product_id": p.id, "name": p.name, "article": p.article, "qty": float(b.quantity),
                    "uom": p.base_uom, "min_stock": float(p.min_stock), "low": Decimal(str(b.quantity)) < Decimal(str(p.min_stock)),
                    "warehouse_id": b.warehouse_id})
    return sorted(out, key=lambda r: r["name"])


@router.get("/stock/alerts")
def stock_alerts(user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    alerts = [r for r in stock(user=user, db=db) if r["low"]]
    from ..services.notify import notify
    for a in alerts:
        notify(db, etype="stock.low", title=f"Низкий остаток: {a['name']}",
               body=f"{a['qty']} {a['uom']} при минимуме {a['min_stock']}", severity="warn",
               link_type="product", link_id=a["product_id"], dedupe_key=f"stock.low:{a['product_id']}")
    db.commit()
    return alerts


@router.get("/stock/movements")
def movements(product_id: Optional[str] = None, warehouse_id: Optional[str] = None, doc_type: Optional[str] = None,
              date_from: Optional[str] = None, date_to: Optional[str] = None, limit: int = 300,
              user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    q = db.query(StockMovement)
    if product_id: q = q.filter(StockMovement.product_id == product_id)
    if warehouse_id: q = q.filter(StockMovement.warehouse_id == warehouse_id)
    if doc_type: q = q.filter(StockMovement.doc_type == doc_type)
    if date_from: q = q.filter(StockMovement.at >= date_from)
    if date_to: q = q.filter(StockMovement.at <= date_to + " 23:59:59")
    rows = q.order_by(StockMovement.at.desc()).limit(limit).all()
    return [{"at": m.at.isoformat(sep=" ", timespec="seconds"), "product": db.get(Product, m.product_id).name,
             "qty": float(m.qty_signed), "doc_type": m.doc_type, "doc_id": m.doc_id, "reason": m.reason,
             "unit_cost": float(m.unit_cost), "negative_allowed": m.negative_allowed,
             "user": (db.get(User, m.user_id).name if m.user_id and db.get(User, m.user_id) else "")}
            for m in rows]


@router.get("/stock/consumption")
def consumption(date_from: Optional[str] = None, date_to: Optional[str] = None,
                user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    """Фактический расход (движения sale) vs теоретический (по проданным блюдам и техкартам)."""
    from sqlalchemy import func
    q = db.query(StockMovement.product_id, func.sum(StockMovement.qty_signed)).filter(StockMovement.doc_type == "sale")
    if date_from: q = q.filter(StockMovement.at >= date_from)
    if date_to: q = q.filter(StockMovement.at <= date_to + " 23:59:59")
    fact = {pid: float(s or 0) for pid, s in q.group_by(StockMovement.product_id).all()}
    # теория: позиции оплаченных заказов × нормы техкарт
    from ..models import Order, OrderItem, MenuItem
    from ..services.stock_service import recipe_consumption
    oq = db.query(Order).filter(Order.status.in_(["paid", "closed"]))
    if date_from: oq = oq.filter(Order.created_at >= date_from)
    if date_to: oq = oq.filter(Order.created_at <= date_to + " 23:59:59")
    theory: dict[str, float] = {}
    for o in oq.all():
        for oi in db.query(OrderItem).filter(OrderItem.order_id == o.id, OrderItem.deleted == False,
                                             OrderItem.status != "cancelled").all():
            mi = db.get(MenuItem, oi.item_id)
            if not mi or not mi.track_stock: continue
            for pid, qn in recipe_consumption(db, mi.id, Decimal(str(oi.qty))):
                theory[pid] = theory.get(pid, 0) + float(qn)
    rows = []
    for pid in set(list(fact) + list(theory)):
        p = db.get(Product, pid)
        f = -fact.get(pid, 0); th = theory.get(pid, 0)
        rows.append({"product": p.name if p else pid, "actual": round(f, 3), "theory": round(th, 3),
                     "diff": round(f - th, 3), "uom": p.base_uom if p else ""})
    return sorted(rows, key=lambda r: abs(r["diff"]), reverse=True)


class ReceiptIn(BaseModel):
    supplier_id: str
    warehouse_id: str
    po_id: Optional[str] = None
    invoice_no: Optional[str] = None
    lines: List[dict]   # [{product_id, qty, price, expiry_date?, lot_note?}] в purchase_uom

@router.post("/receipts")
def create_receipt(body: ReceiptIn, user: User = Depends(require_perm("warehouse:receipt")), db: Session = Depends(get_db)):
    """Приходная накладная: черновик → проведение с движениями и партиями."""
    sup = db.get(Supplier, body.supplier_id)
    if not sup: raise HTTPException(404, "Поставщик не найден")
    wh = db.get(Warehouse, body.warehouse_id)
    if not wh: raise HTTPException(404, "Склад не найден")
    number = f"PR-{now_almaty().strftime('%Y%m')}-{(db.query(GoodsReceipt).count() % 9000) + 1000}"
    r = GoodsReceipt(venue_id=user.venue_id, supplier_id=sup.id, warehouse_id=wh.id, po_id=body.po_id,
                     number=number, status="draft", total=0, posted_by=None, invoice_no=body.invoice_no)
    db.add(r); db.flush()
    total = Decimal("0")
    for ln in body.lines:
        p = db.get(Product, ln["product_id"])
        if not p: raise HTTPException(400, f"Товар {ln['product_id']} не найден")
        rq = qty(ln["qty"]); pr = money(ln["price"])
        if rq <= 0 or pr < 0: raise HTTPException(400, "Некорректные количество/цена")
        db.add(GoodsReceiptLine(receipt_id=r.id, product_id=p.id, ordered_qty=rq, received_qty=rq, price=pr,
                                expiry_date=Decimal and __import__("datetime").datetime.fromisoformat(ln["expiry_date"]) if ln.get("expiry_date") else None,
                                lot_note=ln.get("lot_note")))
        total += rq * pr
        # сверка с заказом поставщика
        if body.po_id:
            pol = db.query(PurchaseOrderLine).filter_by(po_id=body.po_id, product_id=p.id).first()
            if pol: pol.received_qty = qty(Decimal(str(pol.received_qty)) + rq)
    r.total = money(total)
    log_event(db, user=user, event_type="warehouse.receipt_created", object_type="goods_receipt", object_id=r.id,
              amount=float(total), new_values={"number": number})
    db.commit(); db.refresh(r)
    return {"id": r.id, "number": r.number, "total": float(r.total)}


@router.post("/receipts/{rid}/post")
def post_receipt(rid: str, user: User = Depends(require_perm("warehouse:receipt")), db: Session = Depends(get_db)):
    """Проведение прихода: движения + партии + обновление закупочных цен. Повторное проведение невозможно."""
    r = db.query(GoodsReceipt).filter(GoodsReceipt.id == rid).with_for_update().first()
    if not r: raise HTTPException(404, "Накладная не найдена")
    if r.status == "posted": raise HTTPException(409, "Накладная уже проведена")
    if r.status != "draft": raise HTTPException(409, f"Статус {r.status}")
    for ln in db.query(GoodsReceiptLine).filter_by(receipt_id=r.id).all():
        p = db.get(Product, ln.product_id)
        base_qty = qty(Decimal(str(ln.received_qty)) * Decimal(str(p.coef)))
        unit_cost_base = money(Decimal(str(ln.price)) / (Decimal(str(p.coef)) or 1))
        post_movement(db, warehouse_id=r.warehouse_id, product_id=p.id, qty_signed=base_qty, doc_type="receipt",
                      doc_id=r.id, reason=f"Приход {r.number}", user=user, unit_cost=unit_cost_base)
        if p.track_lots or ln.expiry_date:
            db.add(StockLot(product_id=p.id, warehouse_id=r.warehouse_id, qty_left=base_qty,
                            unit_cost=unit_cost_base, expiry_date=ln.expiry_date, receipt_id=r.id))
        p.last_purchase_price = ln.price  # история закупочных цен — через movements.unit_cost
    r.status = "posted"; r.posted_by = user.id
    if r.po_id:
        po = db.get(PurchaseOrder, r.po_id)
        if po and po.status in ("approved", "posted"): po.status = "posted"
    log_event(db, user=user, event_type="warehouse.receipt_posted", object_type="goods_receipt", object_id=r.id,
              amount=float(r.total))
    db.commit()
    return {"ok": True}


@router.get("/receipts")
def list_receipts(date_from: Optional[str] = None, date_to: Optional[str] = None,
                  user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    q = db.query(GoodsReceipt)
    if date_from: q = q.filter(GoodsReceipt.created_at >= date_from)
    if date_to: q = q.filter(GoodsReceipt.created_at <= date_to + " 23:59:59")
    return [{"id": r.id, "number": r.number, "supplier": db.get(Supplier, r.supplier_id).name,
             "date": r.created_at.isoformat(sep=" ", timespec="seconds"), "total": float(r.total),
             "status": r.status} for r in q.order_by(GoodsReceipt.created_at.desc()).limit(200).all()]


class TransferIn(BaseModel):
    from_warehouse_id: str
    to_warehouse_id: str
    lines: List[dict]   # [{product_id, qty}] в base_uom

@router.post("/transfers")
def transfer(body: TransferIn, user: User = Depends(require_perm("warehouse:transfer")), db: Session = Depends(get_db)):
    tid = new_id()
    for ln in body.lines:
        qv = qty(ln["qty"])
        if qv <= 0: raise HTTPException(400, "Количество должно быть положительным")
        post_movement(db, warehouse_id=body.from_warehouse_id, product_id=ln["product_id"], qty_signed=-qv,
                      doc_type="transfer_out", doc_id=tid, reason="Перемещение между складами", user=user)
        post_movement(db, warehouse_id=body.to_warehouse_id, product_id=ln["product_id"], qty_signed=qv,
                      doc_type="transfer_in", doc_id=tid, reason="Перемещение между складами", user=user)
    log_event(db, user=user, event_type="warehouse.transfer", object_type="transfer", object_id=tid,
              new_values={"lines": len(body.lines)})
    db.commit()
    return {"ok": True, "transfer_id": tid}


class WriteOffIn(BaseModel):
    warehouse_id: str
    reason: str          # spoilage|production|inventory_loss|other
    reason_text: Optional[str] = None
    lines: List[dict]    # [{product_id, qty}] base_uom

@router.post("/writeoffs")
def writeoff(body: WriteOffIn, user: User = Depends(require_perm("warehouse:writeoff")), db: Session = Depends(get_db)):
    if body.reason not in ("spoilage", "production", "inventory_loss", "other"):
        raise HTTPException(400, "Недопустимый тип причины")
    if not (body.reason_text or "").strip() and body.reason == "other":
        raise HTTPException(400, "Для причины «другое» нужен текст")
    w = WriteOff(venue_id=user.venue_id, warehouse_id=body.warehouse_id,
                 number=f"WO-{now_almaty().strftime('%Y%m%d-%H%M%S')}", reason=body.reason,
                 reason_text=body.reason_text, status="draft", by_user=user.id, lines=body.lines)
    db.add(w); db.flush()
    total = Decimal("0")
    big = False
    for ln in body.lines:
        p = db.get(Product, ln["product_id"])
        if not p: raise HTTPException(400, "Товар не найден")
        qv = qty(ln["qty"])
        if qv <= 0: raise HTTPException(400, "Количество должно быть положительным")
        cost_per_base = Decimal(str(p.last_purchase_price or 0)) / (Decimal(str(p.coef)) or 1)
        line_cost = money(qv * cost_per_base)
        total += line_cost
        post_movement(db, warehouse_id=body.warehouse_id, product_id=p.id, qty_signed=-qv, doc_type="writeoff",
                      doc_id=w.id, reason=f"{body.reason}: {body.reason_text or ''}".strip(), user=user,
                      unit_cost=line_cost)
    w.total_cost = money(total); w.status = "posted"
    log_event(db, user=user, event_type="warehouse.writeoff", object_type="writeoff", object_id=w.id,
              amount=float(total), reason=f"{body.reason} {body.reason_text or ''}")
    if float(total) >= 50000:
        from ..services.notify import notify
        notify(db, etype="writeoff.big", title=f"Крупное списание {total} ₸",
               body=f"{user.name}: {body.reason} {(body.reason_text or '')[:120]}", severity="warn",
               link_type="writeoff", link_id=w.id, amount=float(total))
    db.commit()
    return {"id": w.id, "total": float(w.total_cost)}


class ReturnIn(BaseModel):
    warehouse_id: str
    lines: List[dict]
    reason: str

@router.post("/returns-to-supplier")
def return_to_supplier(body: ReturnIn, user: User = Depends(require_perm("warehouse:receipt")), db: Session = Depends(get_db)):
    if not body.reason.strip(): raise HTTPException(400, "Укажите причину возврата")
    rid = new_id()
    for ln in body.lines:
        qv = qty(ln["qty"])
        post_movement(db, warehouse_id=body.warehouse_id, product_id=ln["product_id"], qty_signed=-qv,
                      doc_type="return_to_supplier", doc_id=rid, reason=body.reason, user=user)
    log_event(db, user=user, event_type="warehouse.return_to_supplier", object_type="return", object_id=rid,
              reason=body.reason)
    db.commit()
    return {"ok": True, "id": rid}


class InventoryIn(BaseModel):
    warehouse_id: str
    lines: List[dict]   # [{product_id, actual_qty}]

@router.post("/inventories")
def create_inventory(body: InventoryIn, user: User = Depends(require_perm("warehouse:inventory")), db: Session = Depends(get_db)):
    inv = Inventory(venue_id=user.venue_id, warehouse_id=body.warehouse_id,
                    number=f"INV-{now_almaty().strftime('%Y%m%d-%H%M%S')}", status="draft", by_user=user.id)
    db.add(inv); db.flush()
    for ln in body.lines:
        bal = get_balance(db, body.warehouse_id, ln["product_id"])
        exp_q = Decimal(str(bal.quantity)); act_q = qty(ln["actual_qty"])
        p = db.get(Product, ln["product_id"])
        cost_per_base = Decimal(str(p.last_purchase_price or 0)) / (Decimal(str(p.coef)) or 1) if p else Decimal("0")
        db.add(InventoryLine(inventory_id=inv.id, product_id=ln["product_id"], expected_qty=exp_q, actual_qty=act_q,
                             diff_qty=qty(act_q - exp_q), unit_cost=money(cost_per_base)))
    db.commit(); db.refresh(inv)
    return {"id": inv.id, "lines": len(body.lines)}


@router.post("/inventories/{iid}/post")
def post_inventory(iid: str, user: User = Depends(require_perm("warehouse:inventory")), db: Session = Depends(get_db)):
    inv = db.query(Inventory).filter(Inventory.id == iid).with_for_update().first()
    if not inv: raise HTTPException(404, "Инвентаризация не найдена")
    if inv.status == "posted": raise HTTPException(409, "Уже проведена")
    diff_total = Decimal("0")
    for ln in db.query(InventoryLine).filter_by(inventory_id=inv.id).all():
        d = qty(Decimal(str(ln.diff_qty)))
        if d != 0:
            post_movement(db, warehouse_id=inv.warehouse_id, product_id=ln.product_id, qty_signed=d,
                          doc_type="inventory", doc_id=inv.id, reason=f"Инвентаризация {inv.number}", user=user,
                          unit_cost=ln.unit_cost)
            diff_total += money(d * Decimal(str(ln.unit_cost)))
    inv.diff_total = money(diff_total); inv.status = "posted"
    log_event(db, user=user, event_type="warehouse.inventory_posted", object_type="inventory", object_id=inv.id,
              amount=float(diff_total))
    db.commit()
    return {"ok": True, "diff_total": float(diff_total)}


@router.get("/inventories")
def list_inventories(user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    return [{"id": i.id, "number": i.number, "status": i.status, "diff_total": float(i.diff_total),
             "date": i.created_at.isoformat(sep=" ", timespec="seconds")}
            for i in db.query(Inventory).order_by(Inventory.created_at.desc()).limit(100).all()]
