"""Лояльность: бонусы и депозиты изменяются ТОЛЬКО записями операций в транзакции."""
from decimal import Decimal
from fastapi import HTTPException
from ..db import now_almaty, money
from ..models import Guest, BonusOperation, DepositAccount, DepositOperation

def bonus_adjust(db, guest: Guest, delta, reason: str, user=None, order_id=None):
    d = Decimal(str(delta)).quantize(Decimal("0.1"))
    bal = Decimal(str(guest.bonuses_balance)) + d
    if bal < 0:
        raise HTTPException(409, f"Недостаточно бонусов: баланс {guest.bonuses_balance}, списывается {-d}")
    guest.bonuses_balance = bal
    op = BonusOperation(guest_id=guest.id, delta=d, balance_after=bal, order_id=order_id,
                        reason=reason, by_user=getattr(user, "id", user))
    db.add(op)
    return op

def deposit_for(db, guest_id: str) -> DepositAccount:
    acc = db.query(DepositAccount).filter_by(guest_id=guest_id).first()
    if not acc:
        acc = DepositAccount(guest_id=guest_id, balance=0); db.add(acc); db.flush()
    return acc

def deposit_op(db, guest: Guest, type_: str, amount, reason: str, user=None, order_id=None):
    acc = deposit_for(db, guest.id)
    a = money(amount)
    bal = Decimal(str(acc.balance)) + (a if type_ == "in" else -a)
    if bal < 0:
        raise HTTPException(409, f"Недостаточно средств на депозите: {acc.balance}")
    acc.balance = bal
    op = DepositOperation(guest_id=guest.id, type=type_, amount=a, balance_after=bal,
                          order_id=order_id, reason=reason, by_user=getattr(user, "id", user))
    db.add(op)
    return op
