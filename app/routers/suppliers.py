"""Поставщики: справочник, условия, оборот закупки."""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional
from ..deps import get_db, require_perm
from ..models import Supplier, GoodsReceipt, PurchaseOrder, User
from ..services.audit import log_event

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


def _out(s: Supplier):
    return {"id": s.id, "name": s.name, "contacts": s.contacts, "requisites": s.requisites,
            "terms": s.terms, "archived": s.archived}


class SupplierIn(BaseModel):
    name: str
    contacts: Optional[str] = None
    requisites: Optional[str] = None
    terms: Optional[str] = None


@router.get("")
def list_suppliers(q: Optional[str] = None, archived: bool = False,
                  user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    qs = db.query(Supplier).filter(Supplier.venue_id == user.venue_id, Supplier.archived == archived)
    if q:
        qs = qs.filter(Supplier.name.ilike(f"%{q}%"))
    out = []
    for s in qs.order_by(Supplier.name).all():
        turnover = (db.query(func.coalesce(func.sum(GoodsReceipt.total), 0))
                      .filter(GoodsReceipt.supplier_id == s.id, GoodsReceipt.status == "posted").scalar())
        open_po = db.query(PurchaseOrder).filter(PurchaseOrder.supplier_id == s.id,
                                                 PurchaseOrder.status.in_(["draft", "pending_approval", "approved"])).count()
        d = _out(s); d["posted_turnover"] = float(turnover); d["open_orders"] = open_po
        out.append(d)
    return out


@router.post("")
def create_supplier(body: SupplierIn, user: User = Depends(require_perm("suppliers:manage")),
                    db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(400, "Название обязательно")
    s = Supplier(venue_id=user.venue_id, **body.model_dump())
    db.add(s); db.flush()
    log_event(db, user=user, event_type="supplier.created", object_type="supplier", object_id=s.id,
              venue_id=user.venue_id, new_values={"name": s.name})
    db.commit(); db.refresh(s)
    return _out(s)


@router.patch("/{sid}")
def patch_supplier(sid: str, body: dict, user: User = Depends(require_perm("suppliers:manage")),
                   db: Session = Depends(get_db)):
    s = db.get(Supplier, sid)
    if not s: raise HTTPException(404, "Поставщик не найден")
    allowed = {"name", "contacts", "requisites", "terms", "archived"}
    old = {k: getattr(s, k) for k in body if k in allowed}
    for k, v in body.items():
        if k in allowed:
            setattr(s, k, v)
    log_event(db, user=user, event_type="supplier.updated", object_type="supplier", object_id=sid,
              old_values={k: str(v) for k, v in old.items()},
              new_values={k: str(v) for k, v in body.items() if k in allowed})
    db.commit()
    return _out(s)


@router.delete("/{sid}")
def archive_supplier(sid: str, user: User = Depends(require_perm("suppliers:manage")), db: Session = Depends(get_db)):
    """Архивирование вместо удаления — история накладных сохраняется."""
    s = db.get(Supplier, sid)
    if not s: raise HTTPException(404, "Поставщик не найден")
    open_po = db.query(PurchaseOrder).filter(PurchaseOrder.supplier_id == sid,
                                             PurchaseOrder.status.in_(["draft", "pending_approval", "approved"])).count()
    if open_po:
        raise HTTPException(409, f"Есть {open_po} незавершённых заказов поставщику — закройте или отмените их")
    s.archived = True
    log_event(db, user=user, event_type="supplier.archived", object_type="supplier", object_id=sid)
    db.commit()
    return {"ok": True}


@router.get("/{sid}/purchases")
def supplier_purchases(sid: str, user: User = Depends(require_perm("warehouse:view")), db: Session = Depends(get_db)):
    rows = (db.query(GoodsReceipt).filter_by(supplier_id=sid, status="posted")
              .order_by(GoodsReceipt.created_at.desc()).limit(200).all())
    return [{"id": r.id, "number": r.number, "total": float(r.total),
             "date": r.created_at.isoformat(sep=" ", timespec="seconds")} for r in rows]
