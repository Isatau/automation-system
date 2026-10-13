"""Расчёт зарплат: черновик → пересчёт/корректировка с причиной → утверждение → выплата (проводка)."""
from datetime import datetime, timedelta, date as datetype
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
from ..deps import get_db, require_perm
from ..db import now_almaty, money
from ..models import Staff, TimeEntry, PayrollAccrual, Order, User, FinTransaction, FinancialAccount
from ..services.audit import log_event

router = APIRouter(prefix="/payroll", tags=["payroll"])


def _month_bounds(month: str):
    try:
        first = datetime.fromisoformat(month + "-01")
    except Exception:
        raise HTTPException(400, "Период в формате YYYY-MM")
    last = (first.replace(day=28) + timedelta(days=7)).replace(day=1)
    return first, last


def _worked_hours(db: Session, staff_id: str, first: datetime, last: datetime) -> float:
    rows = db.query(TimeEntry).filter(TimeEntry.staff_id == staff_id,
                                      TimeEntry.clock_in >= first, TimeEntry.clock_in < last).all()
    return sum(float(t.hours_worked or 0) for t in rows)


def _staff_sales_percent_base(db: Session, staff: Staff, first: datetime, last: datetime) -> Decimal:
    """База процента: выручка закрытых заказов официанта/бармена за период."""
    if not staff.user_id:
        return Decimal("0")
    orders = db.query(Order).filter(Order.waiter_id == staff.user_id, Order.status.in_(["paid", "closed"]),
                                    Order.closed_at >= first, Order.closed_at < last).all()
    total = Decimal("0")
    for o in orders:
        net = Decimal(str(o.total)) - Decimal(str(o.refund_amount))
        if net > 0:
            total += net
    return total


def _acc_out(a: PayrollAccrual, db: Session):
    st = db.get(Staff, a.staff_id)
    return {"id": a.id, "staff_id": a.staff_id, "staff": st.name if st else "",
            "position": st.position if st else "",
            "period": f"{a.period_from.strftime('%Y-%m')}",
            "hours": float(a.formula_snapshot.get("hours", 0)) if a.formula_snapshot else 0,
            "base": float(a.base_amount), "percent": float(a.percent_amount),
            "bonus": float(a.bonus_amount), "advance": float(a.advance_amount),
            "total": float(a.total), "approved": bool(a.approved_by), "paid": a.paid,
            "corrected_of_id": a.corrected_of_id, "reason": a.reason,
            "formula": a.formula_snapshot or {}}


class CalcIn(BaseModel):
    month: str                       # YYYY-MM
    staff_ids: Optional[list[str]] = None


@router.post("/calculate")
def calculate(body: CalcIn, user: User = Depends(require_perm("staff:payroll")), db: Session = Depends(get_db)):
    """Черновик(ы) расчёта. Повторный вызов перезаписывает неутверждённые черновики периода."""
    first, last = _month_bounds(body.month)
    qs = db.query(Staff).filter(Staff.archived == False)  # noqa: E712
    if body.staff_ids:
        qs = qs.filter(Staff.id.in_(body.staff_ids))
    created = []
    for st in qs.all():
        existing = db.query(PayrollAccrual).filter(PayrollAccrual.staff_id == st.id,
                                                   PayrollAccrual.period_from >= first,
                                                   PayrollAccrual.period_to < last,
                                                   PayrollAccrual.approved_by.is_(None)).first()
        hours = _worked_hours(db, st.id, first, last)
        base = Decimal(str(hours)) * Decimal(str(st.rate)) if st.pay_type in ("hourly", "shift") \
            else Decimal(str(st.rate))
        pct_base = _staff_sales_percent_base(db, st, first, last)
        pct_amt = (pct_base * Decimal(str(st.sales_percent)) / 100) if st.pay_type == "percent" else Decimal("0")
        advance = Decimal("0")
        if st.user_id:
            adv_tx = db.query(FinTransaction).filter(FinTransaction.type == "advance",
                                                     FinTransaction.date >= first, FinTransaction.date < last,
                                                     FinTransaction.counterparty == st.name).all()
            advance = sum((Decimal(str(t.amount)) for t in adv_tx), Decimal("0"))
        formula = {"pay_type": st.pay_type, "rate": float(st.rate), "hours": round(hours, 2),
                   "sales_percent": float(st.sales_percent or 0), "percent_base": float(pct_base),
                   "calc_at": now_almaty().isoformat()}
        total = money(base + pct_amt - advance)
        if existing:
            existing.base_amount = money(base); existing.percent_amount = money(pct_amt)
            existing.advance_amount = money(advance); existing.total = total
            existing.formula_snapshot = formula
            rec = existing
        else:
            rec = PayrollAccrual(staff_id=st.id, period_from=first, period_to=last - timedelta(microseconds=1),
                                 base_amount=money(base), percent_amount=money(pct_amt), bonus_amount=0,
                                 advance_amount=money(advance), total=total, formula_snapshot=formula)
            db.add(rec); db.flush()
        created.append(rec.id)
    log_event(db, user=user, event_type="payroll.calculated", object_type="payroll_period", object_id=body.month,
              new_values={"records": len(created)})
    db.commit()
    return {"month": body.month, "records": len(created)}


@router.get("")
def list_accruals(month: Optional[str] = None, user: User = Depends(require_perm("staff:view")),
                  db: Session = Depends(get_db)):
    q = db.query(PayrollAccrual)
    if month:
        first, last = _month_bounds(month)
        q = q.filter(PayrollAccrual.period_from >= first, PayrollAccrual.period_to < last)
    return [_acc_out(a, db) for a in q.order_by(PayrollAccrual.period_from.desc()).all()]


class CorrectIn(BaseModel):
    bonus_amount: Optional[float] = Field(None, ge=0)
    advance_amount: Optional[float] = Field(None, ge=0)
    reason: str


@router.patch("/{aid}")
def correct_accrual(aid: str, body: CorrectIn, user: User = Depends(require_perm("staff:payroll")),
                    db: Session = Depends(get_db)):
    """Корректировка до утверждения — с обязательной причиной и аудитом. После утверждения — только новая запись-коррекция."""
    if not body.reason.strip():
        raise HTTPException(400, "Причина корректировки обязательна")
    a = db.get(PayrollAccrual, aid)
    if not a: raise HTTPException(404, "Расчёт не найден")
    if a.approved_by:
        raise HTTPException(409, "Утверждённый расчёт менять нельзя — создайте корректировку /payroll/{id}/correction")
    old = {"bonus": float(a.bonus_amount), "advance": float(a.advance_amount), "total": float(a.total)}
    if body.bonus_amount is not None: a.bonus_amount = money(body.bonus_amount)
    if body.advance_amount is not None: a.advance_amount = money(body.advance_amount)
    a.total = money(Decimal(str(a.base_amount)) + Decimal(str(a.percent_amount)) + Decimal(str(a.bonus_amount))
                    - Decimal(str(a.advance_amount)))
    a.reason = body.reason
    log_event(db, user=user, event_type="payroll.corrected", object_type="payroll", object_id=a.id,
              old_values=old, new_values={"bonus": float(a.bonus_amount), "advance": float(a.advance_amount),
                                          "total": float(a.total)}, reason=body.reason)
    db.commit(); db.refresh(a)
    return _acc_out(a, db)


class CorrectionIn(BaseModel):
    delta: float          # +/- к итогу утверждённого расчёта
    reason: str


@router.post("/{aid}/correction")
def add_correction(aid: str, body: CorrectionIn, user: User = Depends(require_perm("staff:payroll")),
                   db: Session = Depends(get_db)):
    """Корректировка задним числом — отдельная запись со ссылкой на оригинал (без изменения исходного расчёта)."""
    if not body.reason.strip(): raise HTTPException(400, "Причина обязательна")
    if body.delta == 0: raise HTTPException(400, "Delta не может быть 0")
    a = db.get(PayrollAccrual, aid)
    if not a: raise HTTPException(404, "Расчёт не найден")
    corr = PayrollAccrual(staff_id=a.staff_id, period_from=a.period_from, period_to=a.period_to,
                          base_amount=0, percent_amount=0,
                          bonus_amount=money(max(body.delta, 0)), advance_amount=money(max(-body.delta, 0)),
                          total=money(body.delta), corrected_of_id=a.id, reason=body.reason,
                          formula_snapshot={"correction_of": a.id, "delta": body.delta})
    db.add(corr); db.flush()
    log_event(db, user=user, event_type="payroll.correction_added", object_type="payroll", object_id=corr.id,
              amount=body.delta, reason=body.reason, new_values={"original": a.id})
    db.commit(); db.refresh(corr)
    return _acc_out(corr, db)


@router.post("/{aid}/approve")
def approve_accrual(aid: str, user: User = Depends(require_perm("staff:payroll")), db: Session = Depends(get_db)):
    a = db.query(PayrollAccrual).filter(PayrollAccrual.id == aid).with_for_update().first()
    if not a: raise HTTPException(404, "Расчёт не найден")
    if a.approved_by: raise HTTPException(409, "Уже утверждён")
    a.approved_by = user.id
    log_event(db, user=user, event_type="payroll.approved", object_type="payroll", object_id=a.id,
              amount=float(a.total), approved_by=user.id)
    db.commit()
    return _acc_out(a, db)


class PayOut(BaseModel):
    account_id: Optional[str] = None


@router.post("/{aid}/pay")
def pay_accrual(aid: str, body: PayOut, user: User = Depends(require_perm("staff:payroll")), db: Session = Depends(get_db)):
    """Выплата: только утверждённого расчёта, один раз; сумма <= остатка к выплате (учитывая авансы)."""
    a = db.query(PayrollAccrual).filter(PayrollAccrual.id == aid).with_for_update().first()
    if not a: raise HTTPException(404, "Расчёт не найден")
    if not a.approved_by: raise HTTPException(409, "Выплата возможна только после утверждения")
    if a.paid: raise HTTPException(409, "Расчёт уже выплачен — повторная выплата заблокирована")
    st = db.get(Staff, a.staff_id)
    payout = Decimal(str(a.total))
    if payout > 0:
        acc = db.get(FinancialAccount, body.account_id) if body.account_id else \
            db.query(FinancialAccount).filter_by(venue_id=user.venue_id, kind="cash").first()
        t = FinTransaction(venue_id=user.venue_id, account_id=acc.id if acc else None, type="payout",
                           direction="out", amount=money(payout), date=now_almaty(), doc_ref=a.id,
                           counterparty=st.name if st else a.staff_id,
                           comment=f"Зарплата {a.period_from.strftime('%m.%Y')}", by_user=user.id)
        db.add(t); db.flush()
        if acc:
            acc.balance = money(Decimal(str(acc.balance)) - payout)
    a.paid = True
    log_event(db, user=user, event_type="payroll.paid", object_type="payroll", object_id=a.id, amount=float(a.total))
    db.commit()
    return _acc_out(a, db)
