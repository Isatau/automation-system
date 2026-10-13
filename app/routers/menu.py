"""Меню: категории, позиции, цены (история/планирование), модификаторы, техкарты, станции."""
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional, List
from ..deps import get_db, require_perm
from ..db import now_almaty, money
from ..models import (MenuItem, MenuCategory, ModifierGroup, Modifier, Recipe, RecipeLine, PriceHistory,
                      ScheduledPrice, Station, User, Product)
from ..services.audit import log_event
from ..services.stock_service import dish_cost

router = APIRouter(prefix="/menu", tags=["menu"])


def _item_out(it: MenuItem, db: Session, with_cost=False):
    d = {"id": it.id, "name": it.name, "article": it.article, "category_id": it.category_id,
         "description": it.description, "photo_url": it.photo_url, "price": float(it.price),
         "unit": it.unit, "station_id": it.station_id, "track_stock": it.track_stock,
         "is_set": it.is_set, "set_items": it.set_items or [], "archived": it.archived,
         "unavailable_until": it.unavailable_until.isoformat() if it.unavailable_until else None}
    if with_cost:
        d["cost"] = float(dish_cost(db, it.id))
    return d


class CategoryIn(BaseModel):
    name: str
    venue_id: Optional[str] = None
    parent_id: Optional[str] = None
    sort: int = 0

@router.get("/categories")
def list_categories(user: User = Depends(require_perm("menu:view")), db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "parent_id": c.parent_id, "sort": c.sort}
            for c in db.query(MenuCategory).filter_by(archived=False).order_by(MenuCategory.sort).all()]

@router.post("/categories")
def add_category(body: CategoryIn, user: User = Depends(require_perm("menu:manage")), db: Session = Depends(get_db)):
    c = MenuCategory(name=body.name, venue_id=body.venue_id or user.venue_id, parent_id=body.parent_id, sort=body.sort)
    db.add(c); db.commit(); db.refresh(c)
    log_event(db, user=user, event_type="menu.category_created", object_type="category", object_id=c.id)
    return {"id": c.id, "name": c.name}


class ItemIn(BaseModel):
    name: str
    article: str
    category_id: Optional[str] = None
    price: float = Field(0, ge=0)
    description: Optional[str] = None
    photo_url: Optional[str] = None
    unit: str = "portion"
    station_id: Optional[str] = None
    track_stock: bool = True
    taxable: bool = True
    is_set: bool = False
    set_items: List[dict] = []

@router.get("/items")
def list_items(category_id: Optional[str] = None, q: Optional[str] = None, archived: bool = False,
               user: User = Depends(require_perm("menu:view")), db: Session = Depends(get_db)):
    qs = db.query(MenuItem).filter(MenuItem.archived == archived)
    if category_id: qs = qs.filter(MenuItem.category_id == category_id)
    if q: qs = qs.filter(MenuItem.name.ilike(f"%{q}%") | MenuItem.article.ilike(f"%{q}%"))
    return [_item_out(i, db) for i in qs.order_by(MenuItem.name).limit(500).all()]

@router.post("/items")
def create_item(body: ItemIn, user: User = Depends(require_perm("menu:manage")), db: Session = Depends(get_db)):
    vid = user.venue_id
    if db.query(MenuItem).filter_by(venue_id=vid, article=body.article).first():
        raise HTTPException(409, "Артикул уже занят")
    it = MenuItem(**body.model_dump(), venue_id=vid)
    db.add(it); db.flush()
    db.add(PriceHistory(item_id=it.id, old_price=0, new_price=it.price, valid_from=now_almaty(), changed_by=user.id))
    log_event(db, user=user, event_type="menu.item_created", object_type="item", object_id=it.id,
              new_values={"name": it.name, "price": float(it.price)})
    db.commit(); db.refresh(it)
    return _item_out(it, db)

@router.patch("/items/{iid}")
def patch_item(iid: str, body: dict, user: User = Depends(require_perm("menu:manage")), db: Session = Depends(get_db)):
    it = db.get(MenuItem, iid)
    if not it: raise HTTPException(404, "Позиция не найдена")
    allowed = {"name", "description", "photo_url", "unit", "station_id", "track_stock", "taxable",
               "category_id", "is_set", "set_items"}
    old = {}
    for k, v in body.items():
        if k in allowed:
            old[k] = getattr(it, k) if isinstance(getattr(it, k), (int, float, str, bool)) or getattr(it, k) is None else str(getattr(it, k))
            setattr(it, k, v)
    log_event(db, user=user, event_type="menu.item_updated", object_type="item", object_id=iid,
              old_values={k: str(v) for k, v in old.items()}, new_values={k: str(v) for k, v in body.items() if k in allowed})
    db.commit()
    return _item_out(it, db)

@router.delete("/items/{iid}")
def archive_item(iid: str, user: User = Depends(require_perm("menu:manage")), db: Session = Depends(get_db)):
    """Архивирование вместо физического удаления (справочники не удаляются)."""
    it = db.get(MenuItem, iid)
    if not it: raise HTTPException(404, "Позиция не найдена")
    it.archived = True
    log_event(db, user=user, event_type="menu.item_archived", object_type="item", object_id=iid)
    db.commit()
    return {"ok": True}


class AvailIn(BaseModel):
    unavailable_until: Optional[str] = None   # ISO дата/время; пусто = снова в продаже

@router.post("/items/{iid}/availability")
def availability(iid: str, body: AvailIn, user: User = Depends(require_perm("menu:manage")), db: Session = Depends(get_db)):
    it = db.get(MenuItem, iid)
    if not it: raise HTTPException(404, "Позиция не найдена")
    it.unavailable_until = datetime.fromisoformat(body.unavailable_until) if body.unavailable_until else None
    log_event(db, user=user, event_type="menu.availability", object_type="item", object_id=iid,
              new_values={"unavailable_until": body.unavailable_until})
    db.commit()
    return _item_out(it, db)


class PriceIn(BaseModel):
    price: float = Field(ge=0)
    scheduled_from: Optional[str] = None   # планирование изменения цены

@router.put("/items/{iid}/price")
def set_price(iid: str, body: PriceIn, user: User = Depends(require_perm("menu:price_change")), db: Session = Depends(get_db)):
    it = db.get(MenuItem, iid)
    if not it: raise HTTPException(404, "Позиция не найдена")
    if body.scheduled_from:
        sp = ScheduledPrice(item_id=iid, new_price=money(body.price),
                           scheduled_from=datetime.fromisoformat(body.scheduled_from))
        db.add(sp)
        log_event(db, user=user, event_type="menu.price_scheduled", object_type="item", object_id=iid,
                  new_values={"price": body.price, "from": body.scheduled_from})
        db.commit()
        return {"scheduled": True}
    old = float(it.price)
    it.price = money(body.price)
    db.add(PriceHistory(item_id=iid, old_price=old, new_price=it.price, valid_from=now_almaty(), changed_by=user.id))
    log_event(db, user=user, event_type="menu.price_changed", object_type="item", object_id=iid,
              old_values={"price": old}, new_values={"price": float(it.price)})
    db.commit()
    return _item_out(it, db)

@router.get("/items/{iid}/price-history")
def price_history(iid: str, user: User = Depends(require_perm("menu:view")), db: Session = Depends(get_db)):
    return [{"old": float(h.old_price), "new": float(h.new_price),
             "at": h.valid_from.isoformat(sep=" ", timespec="seconds")}
            for h in db.query(PriceHistory).filter_by(item_id=iid).order_by(PriceHistory.valid_from.desc()).all()]


def apply_scheduled_prices(db):
    """Фоновая задача: наступившие плановые цены применяются с историей."""
    applied = 0
    for sp in db.query(ScheduledPrice).filter(ScheduledPrice.applied == False,
                                              ScheduledPrice.scheduled_from <= now_almaty()).all():
        it = db.get(MenuItem, sp.item_id)
        if it:
            old = float(it.price)
            it.price = sp.new_price
            db.add(PriceHistory(item_id=it.id, old_price=old, new_price=sp.new_price,
                                valid_from=sp.scheduled_from, changed_by=None))
            log_event(db, event_type="menu.price_applied_scheduled", object_type="item", object_id=it.id,
                      old_values={"price": old}, new_values={"price": float(sp.new_price)})
        sp.applied = True; applied += 1
    db.commit()
    return applied


class RecipeIn(BaseModel):
    output_qty: float = Field(1, gt=0)
    output_unit: str = "portion"
    is_semi: bool = False
    lines: List[dict]  # [{product_id, quantity}]

@router.get("/items/{iid}/recipe")
def get_recipe(iid: str, user: User = Depends(require_perm("menu:view")), db: Session = Depends(get_db)):
    rec = db.query(Recipe).filter_by(item_id=iid).first()
    if not rec: return None
    lines = [{"product_id": l.product_id, "quantity": float(l.quantity),
              "product_name": (db.get(Product, l.product_id).name if db.get(Product, l.product_id) else "")}
             for l in db.query(RecipeLine).filter_by(recipe_id=rec.id).all()]
    return {"output_qty": float(rec.output_qty), "output_unit": rec.output_unit,
            "is_semi": rec.is_semi, "lines": lines, "cost": float(dish_cost(db, iid))}

@router.put("/items/{iid}/recipe")
def put_recipe(iid: str, body: RecipeIn, user: User = Depends(require_perm("menu:manage")), db: Session = Depends(get_db)):
    it = db.get(MenuItem, iid)
    if not it: raise HTTPException(404, "Позиция не найдена")
    rec = db.query(Recipe).filter_by(item_id=iid).first()
    if rec:
        for l in db.query(RecipeLine).filter_by(recipe_id=rec.id).all(): db.delete(l)
        rec.output_qty = body.output_qty; rec.output_unit = body.output_unit; rec.is_semi = body.is_semi
    else:
        rec = Recipe(item_id=iid, output_qty=body.output_qty, output_unit=body.output_unit, is_semi=body.is_semi)
        db.add(rec)
    db.flush()
    for ln in body.lines:
        p = db.get(Product, ln["product_id"])
        if not p: raise HTTPException(400, f"Товар {ln['product_id']} не найден")
        db.add(RecipeLine(recipe_id=rec.id, product_id=p.id, quantity=Decimal(str(ln["quantity"]))))
    log_event(db, user=user, event_type="menu.recipe_updated", object_type="item", object_id=iid,
              new_values={"lines": len(body.lines)})
    db.commit()
    return {"ok": True, "cost": float(dish_cost(db, iid))}


class ModGroupIn(BaseModel):
    name: str
    required: bool = False
    min_select: int = 0
    max_select: int = 1
    modifiers: List[dict] = []   # [{name, price_add, item_id?, product_id?}]

@router.get("/modifier-groups")
def list_modgroups(user: User = Depends(require_perm("menu:view")), db: Session = Depends(get_db)):
    out = []
    for g in db.query(ModifierGroup).filter_by(venue_id=user.venue_id).all():
        mods = db.query(Modifier).filter_by(group_id=g.id).all()
        out.append({"id": g.id, "name": g.name, "required": g.required, "min_select": g.min_select,
                    "max_select": g.max_select,
                    "modifiers": [{"id": m.id, "name": m.name, "price_add": float(m.price_add),
                                   "item_id": m.item_id} for m in mods]})
    return out

@router.post("/modifier-groups")
def create_modgroup(body: ModGroupIn, user: User = Depends(require_perm("menu:manage")), db: Session = Depends(get_db)):
    g = ModifierGroup(venue_id=user.venue_id, name=body.name, required=body.required,
                      min_select=body.min_select, max_select=body.max_select)
    db.add(g); db.flush()
    for m in body.modifiers:
        db.add(Modifier(group_id=g.id, name=m["name"], price_add=m.get("price_add", 0),
                        item_id=m.get("item_id"), product_id=m.get("product_id")))
    log_event(db, user=user, event_type="menu.modgroup_created", object_type="modifier_group", object_id=g.id)
    db.commit(); db.refresh(g)
    return {"id": g.id}


@router.get("/stations")
def list_stations(user: User = Depends(require_perm("menu:view")), db: Session = Depends(get_db)):
    return [{"id": s.id, "name": s.name, "kind": s.kind, "cook_time_norm_min": s.cook_time_norm_min}
            for s in db.query(Station).filter_by(venue_id=user.venue_id).all()]
