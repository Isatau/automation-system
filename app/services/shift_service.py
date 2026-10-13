"""Кассовые смены: формула ожидаемого остатка наличных и сводка."""
from decimal import Decimal
from sqlalchemy import func
from ..db import money
from ..models import Shift, Payment, CashOperation, Refund, Order

def cash_methods(db) -> set[str]:
    from ..models import PaymentMethod
    return {m.code for m in db.query(PaymentMethod).filter_by(is_cash=True).all()} or {"cash"}

def expected_cash(db, shift: Shift) -> Decimal:
    cm = cash_methods(db)
    e = Decimal(str(shift.initial_cash))
    # наличные платежи (сумма, без чаевых — чаевые учитываются отдельно как tip)
    q = (db.query(func.coalesce(func.sum(Payment.amount), 0))
           .filter(Payment.shift_id == shift.id, Payment.method.in_(cm), Payment.status == "confirmed").scalar())
    e += Decimal(str(q))
    r = (db.query(func.coalesce(func.sum(Refund.amount), 0))
           .join(Payment, Payment.id == Refund.payment_id)
           .filter(Refund.shift_id == shift.id, Payment.method.in_(cm)).scalar())
    e -= Decimal(str(r))
    ops = db.query(CashOperation).filter_by(shift_id=shift.id).all()
    for o in ops:
        a = Decimal(str(o.amount))
        if o.type == "in": e += a
        elif o.type == "out": e -= a
        elif o.type == "transfer_in": e += a
        elif o.type == "transfer_out": e -= a
        elif o.type == "correction": e += a  # сумма может быть отрицательной
    return money(e)

def shift_summary(db, shift: Shift) -> dict:
    rows = (db.query(Payment.method, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0),
                    func.coalesce(func.sum(Payment.tip), 0))
              .filter(Payment.shift_id == shift.id, Payment.status == "confirmed")
              .group_by(Payment.method).all())
    by_method = [{"method": m, "count": c, "amount": float(a), "tip": float(t)} for m, c, a, t in rows]
    refunds = (db.query(func.coalesce(func.sum(Refund.amount), 0))
                 .filter(Refund.shift_id == shift.id).scalar())
    orders_count = (db.query(func.count(Order.id)).filter(Order.shift_id == shift.id,
                    Order.status.in_(["paid", "closed"])).scalar())
    cancelled_count = (db.query(func.count(Order.id)).filter(Order.shift_id == shift.id,
                       Order.status == "cancelled").scalar())
    exp = expected_cash(db, shift)
    return {
        "shift_id": shift.id, "status": shift.status,
        "opened_at": shift.opened_at.isoformat(sep=" ", timespec="seconds"),
        "initial_cash": float(shift.initial_cash),
        "by_method": by_method,
        "refunds_total": float(refunds),
        "paid_orders": int(orders_count), "cancelled_orders": int(cancelled_count),
        "expected_cash": float(exp),
    }
