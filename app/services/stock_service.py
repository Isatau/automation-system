"""Складской сервис: неизменяемые движения, FIFO-партии, списание по техкартам."""
from __future__ import annotations
from decimal import Decimal
from fastapi import HTTPException
from ..db import now_almaty, money, qty, SessionLocal
from ..models import (Product, StockBalance, StockMovement, StockLot, Recipe, RecipeLine, MenuItem, OrderItem,
                      ItemModifierRec, Modifier)
from .audit import log_event


def get_balance(db, warehouse_id: str, product_id: str) -> StockBalance:
    bal = db.query(StockBalance).filter_by(warehouse_id=warehouse_id, product_id=product_id).first()
    if not bal:
        bal = StockBalance(warehouse_id=warehouse_id, product_id=product_id, quantity=0)
        db.add(bal); db.flush()
    return bal


def post_movement(db, *, warehouse_id, product_id, qty_signed, doc_type, doc_id=None, reason=None,
                  user=None, unit_cost=None, negative_allowed=False, lot_id=None):
    """Единственная точка изменения остатков. Блокирует баланс, пишет append-only движение."""
    q = qty(qty_signed)
    bal = (db.query(StockBalance)
             .filter_by(warehouse_id=warehouse_id, product_id=product_id)
             .with_for_update()
             .first())
    if bal is None:
        bal = StockBalance(warehouse_id=warehouse_id, product_id=product_id, quantity=0)
        db.add(bal); db.flush()
        bal = db.query(StockBalance).filter_by(warehouse_id=warehouse_id, product_id=product_id).with_for_update().first()
    new_q = qty(Decimal(str(bal.quantity)) + q)
    if new_q < 0 and not negative_allowed:
        p = db.get(Product, product_id)
        raise HTTPException(409, f"Недостаточный остаток «{p.name}»: {qty(bal.quantity)} {p.base_uom}, требуется -{(-q)}")
    mv = StockMovement(warehouse_id=warehouse_id, product_id=product_id, qty_signed=q,
                       doc_type=doc_type, doc_id=doc_id, reason=reason, unit_cost=unit_cost or 0,
                       negative_allowed=negative_allowed and q < 0, user_id=getattr(user, "id", user),
                       at=now_almaty(), lot_id=lot_id)
    db.add(mv)
    bal.quantity = new_q
    # FIFO по партиям при расходе
    if q < 0:
        left = -q
        lots = (db.query(StockLot)
                  .filter(StockLot.warehouse_id == warehouse_id, StockLot.product_id == product_id,
                          StockLot.qty_left > 0)
                  .order_by(StockLot.expiry_date.is_(None), StockLot.expiry_date, StockLot.created_at)
                  .all())
        for lot in lots:
            if left <= 0: break
            take = min(Decimal(str(lot.qty_left)), left)
            lot.qty_left = qty(Decimal(str(lot.qty_left)) - take)
            left -= take
    return mv


def recipe_consumption(db, item_id: str, amount: Decimal, _depth=0) -> list[tuple[str, Decimal]]:
    """[(product_id, qty_base)] с разворачиванием полуфабрикатов. amount — в единицах выхода рецепта."""
    if _depth > 5:
        return []
    res: list[tuple[str, Decimal]] = []
    rec = db.query(Recipe).filter_by(item_id=item_id).first()
    if not rec:
        return res
    out = Decimal(str(rec.output_qty)) or Decimal("1")
    k = amount / out
    for ln in db.query(RecipeLine).filter_by(recipe_id=rec.id).all():
        prod = db.get(Product, ln.product_id)
        if not prod:
            continue
        need = qty(k * Decimal(str(ln.quantity)))
        sub_item = db.query(MenuItem).filter(MenuItem.venue_id == prod.venue_id, MenuItem.archived == False,
                                             MenuItem.name == prod.name).first() if prod else None
        # если ингредиент сам является блюдом-полуфабрикатом с картой — разворачиваем
        semi_rec = db.query(Recipe).join(MenuItem, MenuItem.id == Recipe.item_id).filter(
            MenuItem.article == prod.article, Recipe.is_semi == True).first() if prod else None
        if semi_rec:
            for pid, q2 in recipe_consumption(db, semi_rec.item_id, need, _depth + 1):
                res.append((pid, q2))
        else:
            res.append((ln.product_id, need))
    return res


def dish_cost(db, item_id: str) -> Decimal:
    """Себестоимость блюда по техкарте и последним закупочным ценам."""
    total = Decimal("0")
    for pid, qn in recipe_consumption(db, item_id, Decimal("1")):
        p = db.get(Product, pid)
        price_per_base = Decimal(str(p.last_purchase_price or 0)) / (Decimal(str(p.coef)) or 1) if p and p.coef else Decimal(str(p.last_purchase_price or 0)) if p else Decimal("0")
        total += money(qn * price_per_base)
    return money(total)


def post_sale_stock(db, order, items: list[OrderItem], warehouse_id: str, user=None):
    """Списание ингредиентов по действующим техкартам при оплате. Идемпотентно по order.stock_posted."""
    if order.stock_posted:
        return Decimal("0")
    total_cost = Decimal("0")
    for oi in items:
        if oi.deleted or oi.status == "cancelled":
            continue
        mi = db.get(MenuItem, oi.item_id)
        if not mi:
            continue
        amounts: list[tuple[str, Decimal]] = []
        if mi.track_stock:
            amounts += recipe_consumption(db, mi.id, Decimal(str(oi.qty)))
        if mi.is_set and mi.set_items:
            for si in mi.set_items:
                sub = db.get(MenuItem, si["item_id"])
                if sub and sub.track_stock:
                    amounts += recipe_consumption(db, sub.id, Decimal(str(si.get("qty", 1))) * Decimal(str(oi.qty)))
        for im in db.query(ItemModifierRec).filter_by(order_item_id=oi.id).all():
            mod = db.get(Modifier, im.modifier_id)
            if mod and mod.product_id:
                amounts.append((mod.product_id, qty(Decimal(str(im.qty)) * Decimal(str(mod.price_add and 1 or 1)))))
        line_cost = Decimal("0")
        for pid, qn in amounts:
            p = db.get(Product, pid)
            wh = warehouse_id or (p.warehouse_id if p else None)
            cost_per_base = Decimal(str(p.last_purchase_price or 0)) / (Decimal(str(p.coef)) or Decimal("1")) if p else Decimal("0")
            line_cost += money(qn * cost_per_base)
            try:
                post_movement(db, warehouse_id=wh, product_id=pid, qty_signed=-qn, doc_type="sale",
                              doc_id=order.id, reason=f"Продажа, заказ №{order.number}", user=user)
            except HTTPException as e:
                # фиксируем нехватку, но не блокируем оплату: списываем с разрешением минуса и причиной
                allow_neg = _allow_negative_sales(db)
                if not allow_neg:
                    raise
                post_movement(db, warehouse_id=wh, product_id=pid, qty_signed=-qn, doc_type="sale",
                              doc_id=order.id, reason=f"Продажа (нехватка, минус разрешён настройкой): {e.detail}",
                              user=user, negative_allowed=True)
                log_event(db, user=user, event_type="stock.negative", object_type="product", object_id=pid,
                          venue_id=order.venue_id, amount=float(line_cost), reason=str(e.detail))
        total_cost += line_cost
    order.cost_amount = money(total_cost)
    order.stock_posted = True
    return money(total_cost)

def _allow_negative_sales(db) -> bool:
    """Разрешение уходить в минус при списании продаж — только явной настройкой (по умолчанию запрещено)."""
    from ..models import Setting

    def truthy(v):
        return bool(v) if isinstance(v, bool) else str(v).lower() in ("1", "true")

    st = db.get(Setting, "stock.allow_negative_sales")
    if st is not None:
        val = st.value
        if isinstance(val, dict):
            return truthy(val.get("value", False))
        return truthy(val)
    # совместимо с настройкой админ-панели negative_stock_policy {"allow_on_sales": true}
    st2 = db.get(Setting, "negative_stock_policy")
    if st2 is not None and isinstance(st2.value, dict):
        return truthy(st2.value.get("allow_on_sales", False))
    return False
