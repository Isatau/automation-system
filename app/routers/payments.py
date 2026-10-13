"""Платежи: частичная/смешанная/раздельная оплата, возвраты, сверка. Защита от двойного проведения."""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
from ..deps import get_db, require_perm
from ..db import now_almaty, money, SessionLocal
from ..models import (Order, Payment, Refund, PaymentMethod, Shift, OrderSplit, User, Guest,
                      GiftCertificate, CertificateOperation, CashOperation, FinTransaction, FinancialAccount,
                      DiscountRule, DepositAccount, Warehouse)
from ..routers.orders import active_items
from ..services.audit import log_event
from ..services.payment_service import remaining_to_pay, assert_payable, recalc_paid
from ..services.idempotency import check_key, save_result
from ..services.stock_service import post_sale_stock

router = APIRouter(tags=["payments"])


def _pay_out(p: Payment):
    return {"id": p.id, "order_id": p.order_id, "method": p.method, "amount": float(p.amount),
            "tip": float(p.tip or 0), "status": p.status, "refunded": float(p.refunded_amount),
            "auth_code": p.auth_code, "card_mask": p.card_mask, "fiscal_state": p.fiscal_state,
            "created_at": p.created_at.isoformat(sep=" ", timespec="seconds")}


@router.get("/payment-methods")
def list_methods(user: User = Depends(require_perm("orders:view")), db: Session = Depends(get_db)):
    return [{"code": m.code, "name": m.name, "is_cash": m.is_cash}
            for m in db.query(PaymentMethod).filter_by(active=True).all()]


class PayIn(BaseModel):
    method: str
    amount: float = Field(gt=0)
    tip: float = Field(0, ge=0)
    split_id: Optional[str] = None
    guest_id: Optional[str] = None          # для deposit/bonus/certificate
    certificate_code: Optional[str] = None
    promo_code: Optional[str] = None
    card_mask: Optional[str] = None         # только mask PAN из терминала; полные данные карт не принимаются!
    auth_code: Optional[str] = None
    external_ref: Optional[str] = None


@router.post("/orders/{oid}/payments")
def pay_order(oid: str, body: PayIn, user: User = Depends(require_perm("payments:create")),
              db: Session = Depends(get_db), idem_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    holder = check_key(db, idem_key, {"o": oid, **body.model_dump()})
    if holder and holder.get("replayed"):
        return holder["response"]
    o = db.get(Order, oid)
    if not o: raise HTTPException(404, "Заказ не найден")
    # блокировка строки заказа — защита от параллельной оплаты с двух касс
    o = db.query(Order).filter(Order.id == o.id).with_for_update().first()
    assert_payable(o)
    pm = db.query(PaymentMethod).filter_by(code=body.method, active=True).first()
    if not pm: raise HTTPException(400, f"Способ оплаты «{body.method}» недоступен")
    amt = money(body.amount); tip = money(body.tip)
    rem = remaining_to_pay(db, o)
    if amt > rem:
        raise HTTPException(409, f"Сумма оплаты {amt} ₸ превышает остаток к оплате {rem} ₸")
    shift = db.get(Shift, o.shift_id) if o.shift_id else db.query(Shift).filter_by(status="open").first()
    if not shift:
        raise HTTPException(409, "Нет открытой кассовой смены — откройте смену")
    guest = None
    # спец-методы: депозит / бонусы / сертификат
    if body.method in ("deposit", "bonus"):
        if not body.guest_id: raise HTTPException(400, "Не указан гость")
        guest = db.get(Guest, body.guest_id)
        if not guest: raise HTTPException(404, "Гость не найден")
        from ..services.loyalty_service import bonus_adjust, deposit_op
        if body.method == "bonus":
            # лимит правила лояльности: max_share_pct от итога
            rule = db.query(DiscountRule).filter_by(kind="bonus_accrual").first()
            if rule and rule.max_share_pct:
                maxb = money(Decimal(str(o.total)) * Decimal(str(rule.max_share_pct)) / 100)
                if amt > maxb:
                    raise HTTPException(409, f"Бонусами можно оплатить не более {maxb} ₸ ({rule.max_share_pct}%)")
            if Decimal(str(guest.bonuses_balance)) < Decimal(str(amt)):
                raise HTTPException(409, "Недостаточно бонусов на счету гостя")
            bonus_adjust(db, guest, -Decimal(str(amt)), f"Оплата бонусами, заказ №{o.number}", user, o.id)
        else:
            dep = db.query(DepositAccount).filter_by(guest_id=guest.id).first()
            if not dep or Decimal(str(dep.balance)) < amt:
                raise HTTPException(409, "Недостаточно средств на депозите гостя")
            deposit_op(db, guest, "out", amt, f"Оплата депозитом, заказ №{o.number}", user, o.id)
    elif body.method == "certificate":
        cert = db.query(GiftCertificate).filter_by(code=body.certificate_code or "").first()
        if not cert or cert.status != "active":
            raise HTTPException(400, "Сертификат не найден или не активен")
        if cert.valid_to and cert.valid_to < now_almaty():
            cert.status = "expired"; raise HTTPException(409, "Срок действия сертификата истёк")
        use = min(amt, Decimal(str(cert.balance)))
        if use <= 0: raise HTTPException(409, "На сертификате нет средств")
        cert.balance = money(Decimal(str(cert.balance)) - use)
        db.add(CertificateOperation(certificate_id=cert.id, order_id=o.id, amount=use,
                                   balance_after=cert.balance, by_user=user.id))
        if cert.balance == 0:
            cert.status = "used"; cert.used_by_order = o.id
        amt = money(use)
    # смешанная: сам метод mixed не несёт сумму — клиент шлёт несколько платежей; mixed остаётся справочным
    p = Payment(order_id=o.id, shift_id=shift.id, split_id=body.split_id, method=body.method,
                amount=amt, tip=tip, status="confirmed", guest_id=guest.id if guest else body.guest_id,
                by_user=user.id, card_mask=(body.card_mask[:12] if body.card_mask else None),
                auth_code=body.auth_code, external_ref=body.external_ref,
                idempotency_key=idem_key)
    db.add(p); db.flush()
    if body.split_id:
        sp = db.get(OrderSplit, body.split_id)
        if sp: sp.paid_amount = money(Decimal(str(sp.paid_amount)) + amt + tip)
    # фискализация — отдельный слой; успех только при подтверждении устройства
    if body.method in ("cash", "card", "qr"):
        from ..integrations.fiscal_kz import get_fiscal_adapter
        adapter = get_fiscal_adapter()
        rec, res = adapter.run(db, "receipt", {"order": o.number, "amount": float(amt)}, ref_id=p.id)
        p.fiscal_state = "done" if res.confirmed_by_device else "pending"
        if res.confirmed_by_device:
            p.fiscal_receipt_no = res.ref
        else:
            from ..services.notify import notify
            notify(db, etype="device.error", title="Фискальный чек не пробит",
                   body=f"Заказ №{o.number}, платёж {amt} ₸: {res.error}. Позиция в очереди повторной обработки.",
                   severity="critical", link_type="payment", link_id=p.id)
    old_status = o.status
    recalc_paid(db, o)
    if o.status == "paid" and old_status != "paid":
        # списание ингредиентов по техкартам (идемпотентно)
        wh = db.query(Warehouse).filter_by(venue_id=o.venue_id).first()
        total_cost = post_sale_stock(db, o, active_items(db, o.id), wh.id if wh else None, user)
        # выручка и себестоимость — в финансовый учёт (отдельно от заказов)
        acc = db.query(FinancialAccount).filter_by(venue_id=o.venue_id, kind="cash").first()
        db.add(FinTransaction(venue_id=o.venue_id, account_id=acc.id if acc else None, type="sale_revenue",
                              amount=Decimal(str(o.total)), date=now_almaty(), doc_ref=o.id,
                              comment=f"Выручка по заказу №{o.number}", by_user=user.id))
        if total_cost:
            db.add(FinTransaction(venue_id=o.venue_id, type="cost", amount=total_cost, date=now_almaty(),
                                  doc_ref=o.id, comment=f"Себестоимость заказа №{o.number}", by_user=user.id))
        # начисление бонусов гостю заказа
        if o.guest_id:
            g = db.get(Guest, o.guest_id)
            if g:
                from ..models import Setting
                s = db.query(Setting).filter_by(key="loyalty_accrual_pct").first()
                pct = Decimal(str((s.value or {}).get("value", 5))) if s else Decimal("5")
                accr = (Decimal(str(o.total)) * pct / 100).quantize(Decimal("0.1"))
                if accr > 0:
                    from ..services.loyalty_service import bonus_adjust
                    bonus_adjust(db, g, accr, f"Начисление {pct}% за заказ №{o.number}", user, o.id)
    log_event(db, user=user, event_type="payment.created", object_type="payment", object_id=p.id,
              venue_id=o.venue_id, amount=float(amt), register_id=shift.register_id,
              new_values={"method": body.method, "order": o.number, "tip": float(tip)})
    db.commit(); db.refresh(p)
    res = {"payment": _pay_out(p), "order_status": o.status,
           "paid_amount": float(o.paid_amount), "remaining": float(remaining_to_pay(db, o))}
    save_result(db, holder, res); db.commit()
    return res


@router.get("/orders/{oid}/payments")
def order_payments(oid: str, user: User = Depends(require_perm("payments:view")), db: Session = Depends(get_db)):
    return [_pay_out(p) for p in db.query(Payment).filter_by(order_id=oid).order_by(Payment.created_at).all()]


class RefundIn(BaseModel):
    amount: float = Field(gt=0)
    reason: str
    confirm_login: Optional[str] = None
    confirm_password: Optional[str] = None


@router.post("/payments/{pid}/refund")
def refund_payment(pid: str, body: RefundIn, user: User = Depends(require_perm("payments:refund")),
                   db: Session = Depends(get_db), idem_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    holder = check_key(db, idem_key, {"p": pid, **body.model_dump()})
    if holder and holder.get("replayed"):
        return holder["response"]
    p = db.query(Payment).filter(Payment.id == pid).with_for_update().first()
    if not p: raise HTTPException(404, "Платёж не найден")
    if p.status != "confirmed":
        raise HTTPException(409, "Возврат возможен только по подтверждённому платежу")
    if not body.reason.strip():
        raise HTTPException(400, "Причина возврата обязательна")
    o = db.get(Order, p.order_id)
    amt = money(body.amount)
    available = Decimal(str(p.amount)) - Decimal(str(p.refunded_amount))
    if amt > available:
        raise HTTPException(409, f"Сумма возврата больше невозвращённой части платежа ({available} ₸)")
    approved_by = None
    if user.role == "cashier":
        if not (body.confirm_login and body.confirm_password):
            raise HTTPException(403, "Возврат требует подтверждения старшего смены")
        from ..security import verify_password
        from ..rbac import has_perm
        sup = db.query(User).filter_by(login=body.confirm_login).first()
        if not sup or not verify_password(body.confirm_password, sup.password_hash) or not has_perm(sup, db, "payments:refund"):
            raise HTTPException(403, "Подтверждение не пройдено")
        approved_by = sup.id
    r = Refund(payment_id=p.id, order_id=o.id, shift_id=p.shift_id, amount=amt,
               reason=body.reason, approved_by=approved_by, by_user=user.id)
    db.add(r)
    p.refunded_amount = money(Decimal(str(p.refunded_amount)) + amt)
    if p.refunded_amount >= Decimal(str(p.amount)):
        p.status = "reversed"
    o.refund_amount = money(Decimal(str(o.refund_amount)) + amt)
    # наличный возврат уменьшает ожидаемый остаток через учёт Refund в формуле смены
    recalc_paid(db, o)
    # возврат бонусов/депозита назад гостю
    if p.method == "bonus" and o.guest_id:
        from ..services.loyalty_service import bonus_adjust
        g = db.get(Guest, o.guest_id)
        if g: bonus_adjust(db, g, Decimal(str(amt)), f"Возврат бонусов, заказ №{o.number}", user, o.id)
    if p.method == "deposit" and o.guest_id:
        from ..services.loyalty_service import deposit_op
        g = db.get(Guest, o.guest_id)
        if g: deposit_op(db, g, "in", amt, f"Возврат на депозит, заказ №{o.number}", user, o.id)
    log_event(db, user=user, event_type="payment.refund", object_type="payment", object_id=p.id,
              venue_id=o.venue_id, amount=float(amt), reason=body.reason, approved_by=approved_by)
    from ..services.notify import notify
    notify(db, etype="refund", title=f"Возврат {amt} ₸ по заказу №{o.number}",
           body=f"{user.name}. Причина: {body.reason}", severity="warn", link_type="order", link_id=o.id,
           amount=float(amt))
    db.commit()
    res = {"refund_id": r.id, "order_status": o.status, "paid_amount": float(o.paid_amount),
           "refund_amount": float(o.refund_amount)}
    save_result(db, holder, res); db.commit()
    return res


@router.get("/payments/reconciliation")
def reconciliation(date: Optional[str] = None, user: User = Depends(require_perm("payments:view")),
                   db: Session = Depends(get_db)):
    """Сверка: платежи vs итоги заказов за день."""
    q = db.query(Order)
    if date:
        q = q.filter(Order.created_at >= date, Order.created_at <= date + " 23:59:59")
    orders = q.all()
    out = []
    for o in orders:
        pays = db.query(Payment).filter_by(order_id=o.id, status="confirmed").all()
        paid = sum(float(p.amount) for p in pays)
        if abs(paid - float(o.paid_amount)) > 0.009 or (o.status in ("paid", "closed") and paid < float(o.total)):
            out.append({"order": o.number, "total": float(o.total), "payments_sum": round(paid, 2),
                        "paid_amount_field": float(o.paid_amount), "status": o.status})
    return {"mismatches": out, "checked_orders": len(orders)}
