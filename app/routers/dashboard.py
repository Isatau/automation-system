"""Панель управляющего: главный экран с реальными KPI. Каждый показатель —
ссылка на исходные операции (id объектов возвращаются для drill-down)."""
from __future__ import annotations
from datetime import timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import money, now_almaty
from ..deps import get_db, current_user, require_perm
from ..models import (Order, Payment, Refund, Shift, MenuItem, Table, KitchenTicket,
                      StockBalance, Product, Warehouse, AuditEvent, Notification,
                      TimeEntry, Staff, User)
from ..services.shift_service import expected_cash

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", dependencies=[Depends(require_perm("reports:view"))])
def dashboard(db: Session = Depends(get_db), user: User = Depends(current_user)):
    today = now_almaty().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)

    orders_today = db.query(Order).filter(Order.created_at >= today).all()
    open_orders = [o for o in orders_today if o.status not in ("closed", "cancelled")]
    paid_today = [o for o in orders_today if o.status in ("paid", "closed")]

    pays = db.query(Payment).filter(Payment.created_at >= today,
                                    Payment.status == "confirmed").all()
    refs = db.query(Refund).filter(Refund.at >= today).all()
    revenue = sum(money(p.amount) for p in pays) - sum(money(r.amount) for r in refs)
    cash_amt = sum(money(p.amount) for p in pays if p.method == "cash")
    noncash_amt = revenue and (sum(money(p.amount) for p in pays if p.method != "cash")
                               - sum(money(r.amount) for r in refs)) or Decimal(0)
    checks = len({p.order_id for p in pays})
    avg_check = revenue / checks if checks else Decimal(0)

    # выручка за 7 дней по дням (для графика)
    days = []
    for i in range(7):
        d1 = today - timedelta(days=i)
        d0 = d1 - timedelta(days=1)
        amt = (db.query(func.sum(Payment.amount)).filter(Payment.created_at >= d0,
               Payment.created_at < d1, Payment.status == "confirmed").scalar())
        days.append({"date": d0.strftime("%Y-%m-%d"), "revenue": float(round(money(amt or 0), 2))})
    days.reverse()

    # категории и топ позиций за сегодня
    ids = [o.id for o in paid_today]
    by_cat, top = [], []
    if ids:
        from ..models import OrderItem
        rows = (db.query(MenuItem.category_id, func.sum(OrderItem.line_total))
                .join(OrderItem, OrderItem.item_id == MenuItem.id)
                .filter(OrderItem.order_id.in_(ids), OrderItem.deleted == False)  # noqa
                .group_by(MenuItem.category_id).all())
        cats = {c.id: c.name for c in db.query(__import__("app.models", fromlist=["MenuCategory"]).MenuCategory).all()}
        by_cat = [{"category": cats.get(c, "—"), "amount": float(round(money(a or 0), 2))}
                  for c, a in sorted(rows, key=lambda r: -(r[1] or 0))]
        trows = (db.query(OrderItem.name_snapshot, func.sum(OrderItem.qty), func.sum(OrderItem.line_total))
                 .filter(OrderItem.order_id.in_(ids), OrderItem.deleted == False)  # noqa
                 .group_by(OrderItem.name_snapshot)
                 .order_by(func.sum(OrderItem.line_total).desc()).limit(10).all())
        top = [{"name": n, "qty": float(q or 0), "amount": float(round(money(a or 0), 2))}
               for n, q, a in trows]

    shifts_open = db.query(Shift).filter(Shift.status == "open").all()
    shift_rows = [{"id": s.id, "register_id": s.register_id, "user_id": s.user_id,
                   "opened": s.opened_at.isoformat(), "expected_cash": float(expected_cash(db, s))}
                  for s in shifts_open]

    # столы
    tables = db.query(Table).all()
    open_by_table = {}
    for o in open_orders:
        if o.table_id:
            open_by_table[o.table_id] = open_by_table.get(o.table_id, Decimal(0)) + money(o.total)
    hall = [{"table_id": t.id, "name": t.name, "status": t.status,
             "open_amount": float(open_by_table.get(t.id, Decimal(0))),
             "waiter_id": t.waiter_id} for t in tables]

    # склад: низкие остатки
    whs = {w.id: w.name for w in db.query(Warehouse).filter_by(archived=False).all()}
    low_stock = []
    for b in db.query(StockBalance).all():
        p = db.get(Product, b.product_id)
        if p and not p.archived and money(b.quantity) < money(p.min_stock):
            low_stock.append({"product": p.name, "qty": float(b.quantity),
                              "min": float(p.min_stock), "warehouse": whs.get(b.warehouse_id, "")})

    # кухня: просроченные тикеты (>15 мин в pending/cooking)
    late_tickets = db.query(KitchenTicket).filter(
        KitchenTicket.status.in_(["pending", "cooking"]),
        KitchenTicket.sent_at < now_almaty() - timedelta(minutes=15)).count()

    # критические события и уведомления
    crit_events = db.query(AuditEvent).filter(AuditEvent.event_type.in_(
        ["order.cancel", "payment.refund", "stock.writeoff", "shift.cash_out",
         "shift.closed_with_diff", "discount.over_limit"]
    ), AuditEvent.at >= week_ago).order_by(AuditEvent.at.desc()).limit(10).all()
    unread_crit = db.query(Notification).filter(Notification.severity == "critical",
                                                Notification.status == "new").count()

    # текущие рабочие смены сотрудников
    working = db.query(TimeEntry).filter(TimeEntry.clock_out.is_(None)).all()
    staff_names = {s.id: s.name for s in db.query(Staff).all()}
    on_duty = [{"staff": staff_names.get(e.staff_id, e.staff_id),
                "since": e.clock_in.isoformat()} for e in working]

    # финансы: расходы/выручка за месяц
    month_start = today.replace(day=1)
    from ..models import FinTransaction
    exp_month = money(db.query(func.sum(FinTransaction.amount)).filter(
        FinTransaction.date >= month_start, FinTransaction.direction == "out",
        FinTransaction.status == "posted").scalar() or 0)
    rev_month = money(db.query(func.sum(Payment.amount)).filter(
        Payment.created_at >= month_start, Payment.status == "confirmed").scalar() or 0)
    cost_month = sum(money(o.cost_amount) for o in paid_today)  # упрощённо по сегодня; детальный P&L — /reports/pnl

    return {
        "generated_at": now_almaty().isoformat(),
        "today": {
            "revenue": float(round(revenue, 2)),
            "checks": checks,
            "avg_check": float(round(avg_check, 2)),
            "cash": float(round(cash_amt, 2)),
            "noncash": float(round(noncash_amt, 2)),
            "orders_open": len(open_orders),
            "open_order_ids": [o.id for o in open_orders],
            "refunds_count": len(refs),
            "refunds_amount": float(round(sum(money(r.amount) for r in refs), 2)),
        },
        "week_revenue": days,
        "by_category": by_cat,
        "top_items": top,
        "shifts_open": shift_rows,
        "hall": hall,
        "low_stock": low_stock,
        "late_kitchen_tickets": late_tickets,
        "critical_events": [{"at": e.at.isoformat(), "type": e.event_type,
                             "object": f"{e.object_type}:{e.object_id}",
                             "amount": float(e.amount) if e.amount is not None else None,
                             "reason": e.reason} for e in crit_events],
        "unread_critical_notifications": unread_crit,
        "on_duty": on_duty,
        "finance_month": {"revenue": float(round(rev_month, 2)),
                          "expenses": float(round(exp_month, 2)),
                          "note": "Полный P&L: GET /reports/pnl"},
    }
