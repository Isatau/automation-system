"""Отчёты на реальных данных БД + экспорт XLSX/CSV/PDF.

Согласованные правила расчёта:
- выручка = подтверждённые платежи минус возвраты (по shift/orders того же периода);
- себестоимость = cost_amount закрытых заказов (списание по техкартам на момент продажи);
- средний чек = выручка / число оплаченных заказов;
- все суммы — Decimal, вывод в ₸ с двумя знаками.
"""
from __future__ import annotations
from datetime import datetime, time, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import money, now_almaty
from ..deps import get_db, current_user, require_perm
from ..models import (Order, OrderItem, Payment, Refund, Shift, MenuItem, MenuCategory,
                      StockBalance, Product, Warehouse, TimeEntry, AuditEvent, User,
                      FinTransaction, WriteOff)
from ..services.exporters import to_csv, to_xlsx, to_pdf

router = APIRouter(prefix="/reports", tags=["reports"])

FMT = "%Y-%m-%d"


def _range(date_from: str | None, date_to: str | None):
    d1 = datetime.strptime(date_from, FMT) if date_from else now_almaty().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
    d2 = datetime.strptime(date_to, FMT) if date_to else now_almaty()
    d2 = d2.replace(hour=23, minute=59, second=59)
    if d1 > d2:
        raise HTTPException(422, "Начало периода позже конца")
    return d1, d2


def _paid_orders(db, d1, d2):
    return db.query(Order).filter(Order.created_at >= d1, Order.created_at <= d2,
                                  Order.status.in_(["paid", "closed"])).all()


@router.get("/sales", dependencies=[Depends(require_perm("reports:view"))])
def sales_report(date_from: str | None = None, date_to: str | None = None,
                 fmt: str | None = Query(None, pattern="^(xlsx|csv|pdf)$"),
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    d1, d2 = _range(date_from, date_to)
    orders = _paid_orders(db, d1, d2)
    payments = db.query(Payment).filter(Payment.created_at >= d1, Payment.created_at <= d2,
                                        Payment.status == "confirmed").all()
    refunds = db.query(Refund).filter(Refund.at >= d1, Refund.at <= d2).all()
    revenue = sum(money(p.amount) for p in payments) - sum(money(r.amount) for r in refunds)
    tips = sum(money(p.tip) for p in payments)
    discounts = sum(money(o.discount_total) for o in orders)
    cost = sum(money(o.cost_amount) for o in orders)
    by_method: dict[str, Decimal] = {}
    for p in payments:
        by_method[p.method] = by_method.get(p.method, Decimal(0)) + money(p.amount)
    cols = ["Показатель", "Значение"]
    rows = [["Период", f"{d1:%d.%m.%Y} — {d2:%d.%m.%Y}"],
            ["Заказов оплачено", len(orders)],
            ["Выручка (₸)", float(round(revenue, 2))],
            ["Возвраты (₸)", float(round(sum(money(r.amount) for r in refunds), 2))],
            ["Скидки (₸)", float(round(discounts, 2))],
            ["Чаевые (₸)", float(round(tips, 2))],
            ["Себестоимость проданного (₸)", float(round(cost, 2))],
            ["Валовая прибыль (₸)", float(round(revenue - cost, 2))],
            ["Средний чек (₸)", float(round(revenue / len(orders), 2)) if orders else 0]]
    for m, a in sorted(by_method.items()):
        rows.append([f"Оплата «{m}» (₸)", float(round(a, 2))])
    if fmt:
        blob = {"xlsx": lambda: to_xlsx(cols, rows, "Продажи"),
                "csv": lambda: to_csv(cols, rows),
                "pdf": lambda: to_pdf(cols, rows, "Продажи за период")}[fmt]()
        media = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "csv": "text/csv; charset=utf-8", "pdf": "application/pdf"}[fmt]
        return Response(blob, media_type=media, headers={
            "Content-Disposition": f"attachment; filename=sales.{fmt}"})
    return {"period": [d1.isoformat(), d2.isoformat()], "rows": rows}


@router.get("/by-category", dependencies=[Depends(require_perm("reports:view"))])
def by_category(date_from: str | None = None, date_to: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    d1, d2 = _range(date_from, date_to)
    order_ids = [o.id for o in _paid_orders(db, d1, d2)]
    res = []
    if order_ids:
        rows = (db.query(MenuItem.category_id, func.sum(OrderItem.line_total), func.sum(OrderItem.qty))
                .join(OrderItem, OrderItem.item_id == MenuItem.id)
                .filter(OrderItem.order_id.in_(order_ids), OrderItem.deleted == False)  # noqa
                .group_by(MenuItem.category_id).all())
        cats = {c.id: c.name for c in db.query(MenuCategory).all()}
        for cid, amt, qty in rows:
            res.append({"category": cats.get(cid, "—"), "amount": float(round(money(amt or 0), 2)),
                        "qty": float(round(qty or 0, 3))})
    res.sort(key=lambda r: -r["amount"])
    return {"items": res}


@router.get("/top-items", dependencies=[Depends(require_perm("reports:view"))])
def top_items(date_from: str | None = None, date_to: str | None = None, limit: int = 20,
              db: Session = Depends(get_db), user: User = Depends(current_user)):
    d1, d2 = _range(date_from, date_to)
    order_ids = [o.id for o in _paid_orders(db, d1, d2)]
    if not order_ids:
        return {"items": []}
    rows = (db.query(OrderItem.name_snapshot, func.sum(OrderItem.qty), func.sum(OrderItem.line_total))
            .filter(OrderItem.order_id.in_(order_ids), OrderItem.deleted == False)  # noqa
            .group_by(OrderItem.name_snapshot).order_by(func.sum(OrderItem.line_total).desc())
            .limit(limit).all())
    return {"items": [{"name": n, "qty": float(q or 0), "amount": float(round(money(a or 0), 2))}
                      for n, q, a in rows]}


@router.get("/by-waiter", dependencies=[Depends(require_perm("reports:view"))])
def by_waiter(date_from: str | None = None, date_to: str | None = None,
              db: Session = Depends(get_db), user: User = Depends(current_user)):
    d1, d2 = _range(date_from, date_to)
    rows = (db.query(Order.waiter_id, func.count(Order.id), func.sum(Order.total))
            .filter(Order.created_at >= d1, Order.created_at <= d2,
                    Order.status.in_(["paid", "closed"]))
            .group_by(Order.waiter_id).all())
    users = {u.id: u.name for u in db.query(User).all()}
    out = [{"waiter": users.get(wid, "—"), "orders": cnt,
            "revenue": float(round(money(t or 0), 2))} for wid, cnt, t in rows]
    out.sort(key=lambda r: -r["revenue"])
    return {"items": out}


@router.get("/cancellations-refunds", dependencies=[Depends(require_perm("reports:view"))])
def cancels_refunds(date_from: str | None = None, date_to: str | None = None,
                    db: Session = Depends(get_db), user: User = Depends(current_user)):
    d1, d2 = _range(date_from, date_to)
    co = db.query(Order).filter(Order.created_at >= d1, Order.created_at <= d2,
                                 Order.status == "cancelled").all()
    ci = db.query(OrderItem).join(Order).filter(Order.created_at >= d1, Order.created_at <= d2,
                                                OrderItem.deleted == True,               # noqa
                                                OrderItem.cancel_reason.is_not(None)).all()
    rf = db.query(Refund).filter(Refund.at >= d1, Refund.at <= d2).all()
    return {
        "cancelled_orders": [{"number": o.number, "reason": o.cancel_reason,
                              "total": float(o.total or 0)} for o in co],
        "cancelled_items": [{"name": i.name_snapshot, "reason": i.cancel_reason} for i in ci[:200]],
        "refunds": [{"at": r.at.isoformat(), "amount": float(r.amount), "reason": r.reason} for r in rf],
        "totals": {"orders": len(co), "items": len(ci),
                   "refund_amount": float(round(sum(money(r.amount) for r in rf), 2))},
    }


@router.get("/shifts-cash-diff", dependencies=[Depends(require_perm("reports:view"))])
def shifts_diff(date_from: str | None = None, date_to: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    d1, d2 = _range(date_from, date_to)
    rows = db.query(Shift).filter(Shift.closed_at >= d1, Shift.closed_at <= d2).all()
    regs = {r.id: r.name for r in db.query(__import__("app.models", fromlist=["CashRegister"]).CashRegister).all()}
    users = {u.id: u.name for u in db.query(User).all()}
    out = [{"shift_id": s.id, "register": regs.get(s.register_id, ""),
            "cashier": users.get(s.user_id, ""),
            "opened": s.opened_at.isoformat(), "closed": s.closed_at.isoformat() if s.closed_at else None,
            "expected": float(s.expected_cash or 0), "actual": float(s.actual_cash or 0),
            "diff": float(s.difference or 0), "reopen_count": s.reopen_count} for s in rows]
    total_diff = sum(r["diff"] for r in out)
    return {"items": out, "total_diff": round(total_diff, 2)}


@router.get("/expenses", dependencies=[Depends(require_perm("reports:view"))])
def expenses(date_from: str | None = None, date_to: str | None = None,
            db: Session = Depends(get_db), user: User = Depends(current_user)):
    d1, d2 = _range(date_from, date_to)
    txs = db.query(FinTransaction).filter(FinTransaction.date >= d1, FinTransaction.date <= d2,
                                          FinTransaction.direction == "out",
                                          FinTransaction.status == "posted").all()
    cats = {c.id: c.name for c in db.query(__import__("app.models", fromlist=["ExpenseCategory"]).ExpenseCategory).all()}
    agg: dict[str, Decimal] = {}
    for t in txs:
        name = cats.get(t.category_id, t.type)
        agg[name] = agg.get(name, Decimal(0)) + money(t.amount)
    return {"items": [{"category": k, "amount": float(round(v, 2))}
                      for k, v in sorted(agg.items(), key=lambda kv: -kv[1])],
            "total": float(round(sum(agg.values()), 2)) if agg else 0}


@router.get("/stock", dependencies=[Depends(require_perm("reports:view"))])
def stock_report(low_only: bool = False, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    whs = {w.id: w.name for w in db.query(Warehouse).filter_by(archived=False).all()}
    prods = {p.id: p for p in db.query(Product).filter_by(archived=False).all()}
    out = []
    for b in db.query(StockBalance).all():
        p = prods.get(b.product_id)
        if not p:
            continue
        q = money(b.quantity)
        low = q < money(p.min_stock)
        if low_only and not low:
            continue
        val = q * money(p.last_purchase_price or 0) / (money(p.coef) or 1)
        out.append({"product": p.name, "warehouse": whs.get(b.warehouse_id, ""),
                    "qty": float(q), "uom": p.base_uom, "min": float(p.min_stock or 0),
                    "low": low, "approx_value": float(round(val, 2))})
    out.sort(key=lambda r: (not r["low"], r["product"]))
    return {"items": out}


@router.get("/stock-movement", dependencies=[Depends(require_perm("reports:view"))])
def stock_movement(date_from: str | None = None, date_to: str | None = None,
                   product_id: str | None = None, warehouse_id: str | None = None,
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    d1, d2 = _range(date_from, date_to)
    from ..models import StockMovement
    q = db.query(StockMovement).filter(StockMovement.at >= d1, StockMovement.at <= d2)
    if product_id:
        q = q.filter(StockMovement.product_id == product_id)
    if warehouse_id:
        q = q.filter(StockMovement.warehouse_id == warehouse_id)
    rows = q.order_by(StockMovement.at.desc()).limit(2000).all()
    prods = {p.id: p.name for p in db.query(Product).all()}
    return {"items": [{"at": m.at.isoformat(), "product": prods.get(m.product_id, m.product_id),
                       "doc_type": m.doc_type, "qty_signed": float(m.qty_signed),
                       "unit_cost": float(m.unit_cost or 0), "reason": m.reason} for m in rows]}


@router.get("/cost-analysis", dependencies=[Depends(require_perm("reports:view"))])
def cost_analysis(date_from: str | None = None, date_to: str | None = None,
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Фактический vs теоретический расход ингредиентов по проданным блюдам."""
    d1, d2 = _range(date_from, date_to)
    from ..models import Recipe, RecipeLine
    order_ids = [o.id for o in _paid_orders(db, d1, d2)]
    if not order_ids:
        return {"items": []}
    sold = (db.query(OrderItem.item_id, func.sum(OrderItem.qty))
            .filter(OrderItem.order_id.in_(order_ids), OrderItem.deleted == False)  # noqa
            .group_by(OrderItem.item_id).all())
    theo: dict[str, Decimal] = {}
    recipes = {r.item_id: r for r in db.query(Recipe).all()}
    lines_by_recipe: dict[str, list] = {}
    for rl in db.query(RecipeLine).all():
        lines_by_recipe.setdefault(rl.recipe_id, []).append(rl)
    for item_id, qty in sold:
        r = recipes.get(item_id)
        if not r:
            continue
        for rl in lines_by_recipe.get(r.id, []):
            per = money(rl.quantity) / (money(r.output_qty) or 1)
            theo[rl.product_id] = theo.get(rl.product_id, Decimal(0)) + per * money(qty)
    # фактический расход sale-движениями
    from ..models import StockMovement
    actual: dict[str, Decimal] = {}
    for m in db.query(StockMovement).filter(StockMovement.at >= d1, StockMovement.at <= d2,
                                            StockMovement.doc_type.in_(["sale", "writeoff"])).all():
        actual[m.product_id] = actual.get(m.product_id, Decimal(0)) + abs(money(m.qty_signed))
    prods = {p.id: p for p in db.query(Product).all()}
    out = []
    for pid in set(theo) | set(actual):
        p = prods.get(pid)
        if not p:
            continue
        th, ac = theo.get(pid, Decimal(0)), actual.get(pid, Decimal(0))
        out.append({"product": p.name, "uom": p.base_uom,
                    "theoretical": float(round(th, 3)), "actual": float(round(ac, 3)),
                    "diff": float(round(ac - th, 3)),
                    "diff_pct": float(round((ac - th) / th * 100, 1)) if th else None})
    out.sort(key=lambda r: -(abs(r["diff"])))
    return {"items": out}


@router.get("/worktime", dependencies=[Depends(require_perm("reports:view"))])
def worktime(date_from: str | None = None, date_to: str | None = None,
             db: Session = Depends(get_db), user: User = Depends(current_user)):
    d1, d2 = _range(date_from, date_to)
    rows = db.query(TimeEntry).filter(TimeEntry.clock_in >= d1, TimeEntry.clock_in <= d2).all()
    from ..models import Staff
    staff = {s.id: s for s in db.query(Staff).all()}
    agg: dict[str, float] = {}
    late: dict[str, int] = {}
    for e in rows:
        hours = float(e.hours_worked or 0)
        agg[e.staff_id] = agg.get(e.staff_id, 0) + hours
        late[e.staff_id] = late.get(e.staff_id, 0) + int(e.late_minutes or 0)
    return {"items": [{"staff": getattr(staff.get(k), "name", k), "hours": round(v, 2),
                       "late_minutes": late.get(k, 0)}
                      for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]}


@router.get("/events-summary", dependencies=[Depends(require_perm("reports:view"))])
def events_summary(date_from: str | None = None, date_to: str | None = None,
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    d1, d2 = _range(date_from, date_to)
    rows = (db.query(AuditEvent.event_type, func.count(AuditEvent.id))
            .filter(AuditEvent.at >= d1, AuditEvent.at <= d2)
            .group_by(AuditEvent.event_type).all())
    return {"items": [{"event_type": t, "count": c} for t, c in sorted(rows, key=lambda r: -r[1])]}
