"""Платёжный сервис: баланс платежей, защита от двойного проведения, статусы оплаты."""
from decimal import Decimal
from fastapi import HTTPException
from ..db import money
from ..models import Payment

def remaining_to_pay(db, order) -> Decimal:
    paid = db.query(Payment).filter(Payment.order_id == order.id, Payment.status == "confirmed").all()
    confirmed = sum((Decimal(str(p.amount) + str(p.tip or 0)) for p in paid), Decimal("0"))
    need = Decimal(str(order.total)) - Decimal(str(order.refund_amount))
    return money(max(need - confirmed, Decimal("0")))

def assert_payable(order):
    if order.status in ("closed", "cancelled"):
        raise HTTPException(409, f"Заказ №{order.number} уже {('закрыт' if order.status=='closed' else 'отменён')} — оплата невозможна")
    if order.status == "paid":
        raise HTTPException(409, f"Заказ №{order.number} уже полностью оплачен — повторная оплата запрещена")

def recalc_paid(db, order):
    """Обновляет paid_amount; при полном покрытии переводит в paid (переход разрешён из awaiting_payment/open/sent/cooking/ready)."""
    from .audit import log_event
    paid = db.query(Payment).filter(Payment.order_id == order.id, Payment.status == "confirmed").all()
    total_paid = sum((Decimal(str(p.amount)) for p in paid), Decimal("0"))
    tips = sum((Decimal(str(p.tip or 0)) for p in paid), Decimal("0"))
    order.paid_amount = money(total_paid)
    order.tip_amount = money(tips)
    if order.status not in ("closed", "cancelled", "paid") and total_paid >= Decimal(str(order.total)):
        old = order.status
        order.status = "paid"
        log_event(db, event_type="order.status.auto", object_type="order", object_id=order.id,
                  venue_id=order.venue_id, old_values={"status": old}, new_values={"status": "paid"})
    elif order.status == "paid" and total_paid < Decimal(str(order.total)):
        # после возврата заказ снова не оплачен полностью
        order.status = "awaiting_payment"
