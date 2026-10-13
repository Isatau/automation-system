"""Финикинг сумм заказа: subtotal, скидки позиций и заказа, итог."""
from decimal import Decimal, ROUND_HALF_UP
from ..db import money

def line_total(oi) -> Decimal:
    base = money(Decimal(str(oi.unit_price)) * Decimal(str(oi.qty))) + money(oi.mods_total)
    disc = Decimal("0")
    if oi.discount_pct:
        disc = base * Decimal(str(oi.discount_pct)) / Decimal("100")
    if oi.discount_amt:
        disc += Decimal(str(oi.discount_amt))
    disc = money(disc)
    if disc > base:
        disc = base
    return money(base - disc), disc

def recompute_order(order, items, order_disc_amt: Decimal | None = None) -> None:
    """items — активные (не deleted, не cancelled) OrderItem.
    order_disc_amt — скидка на весь заказ (если не передана, берётся из order.discount_total как уже посчитанной позиции)."""
    subtotal = Decimal("0"); item_disc = Decimal("0")
    for oi in items:
        lt, d = line_total(oi)
        oi.line_total = lt
        subtotal += money(Decimal(str(oi.unit_price)) * Decimal(str(oi.qty)) + Decimal(str(oi.mods_total)))
        item_disc += d
    od = order_disc_amt if order_disc_amt is not None else getattr(order, "_order_disc", Decimal("0"))
    order.subtotal = money(subtotal)
    order.discount_total = money(item_disc + Decimal(str(od)))
    total = order.subtotal - order.discount_total
    if total < 0: total = Decimal("0")
    order.total = money(total)
    order._order_disc = od
