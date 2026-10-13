"""Кассовые смены: открытие, внесение/изъятие/передача, закрытие с расчётом остатка, reopen."""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
from ..deps import get_db, require_perm
from ..db import now_almaty, money
from ..models import Shift, CashRegister, User, CashOperation, Order
from ..services.audit import log_event
from ..services.shift_service import expected_cash, shift_summary

router = APIRouter(prefix="/shifts", tags=["shifts"])


def _shift_out(s: Shift):
    return {"id": s.id, "register_id": s.register_id, "user_id": s.user_id, "status": s.status,
            "opened_at": s.opened_at.isoformat(sep=" ", timespec="seconds"),
            "initial_cash": float(s.initial_cash),
            "closed_at": s.closed_at.isoformat(sep=" ", timespec="seconds") if s.closed_at else None,
            "expected_cash": float(s.expected_cash) if s.expected_cash is not None else None,
            "actual_cash": float(s.actual_cash) if s.actual_cash is not None else None,
            "difference": float(s.difference) if s.difference is not None else None,
            "reopen_count": s.reopen_count}


class OpenIn(BaseModel):
    register_id: str
    initial_cash: float = Field(0, ge=0)


@router.post("/open")
def open_shift(body: OpenIn, user: User = Depends(require_perm("shifts:open")), db: Session = Depends(get_db)):
    reg = db.get(CashRegister, body.register_id)
    if not reg: raise HTTPException(404, "Касса не найдена")
    existing = db.query(Shift).filter(Shift.register_id == reg.id, Shift.status == "open").first()
    if existing:
        raise HTTPException(409, f"На кассе «{reg.name}» уже открыта смена этим или иным сотрудником")
    if user.role == "cashier":
        other = db.query(Shift).filter(Shift.user_id == user.id, Shift.status == "open").first()
        if other:
            raise HTTPException(409, "У вас уже есть открытая смена")
    s = Shift(venue_id=reg.venue_id, register_id=reg.id, user_id=user.id,
              opened_at=now_almaty(), initial_cash=money(body.initial_cash), status="open")
    db.add(s); db.flush()
    log_event(db, user=user, event_type="shift.opened", object_type="shift", object_id=s.id,
              venue_id=reg.venue_id, amount=float(s.initial_cash), register_id=reg.id,
              new_values={"initial_cash": float(s.initial_cash)})
    db.commit(); db.refresh(s)
    return _shift_out(s)


@router.get("/current")
def current_shift(register_id: Optional[str] = None, user: User = Depends(require_perm("shifts:view")),
                  db: Session = Depends(get_db)):
    q = db.query(Shift).filter(Shift.status == "open")
    if register_id: q = q.filter(Shift.register_id == register_id)
    s = q.first()
    if not s: return None
    d = _shift_out(s); d["summary"] = shift_summary(db, s)
    return d


@router.get("")
def list_shifts(date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None,
                user: User = Depends(require_perm("shifts:view")), db: Session = Depends(get_db)):
    q = db.query(Shift)
    if date_from: q = q.filter(Shift.opened_at >= date_from)
    if date_to: q = q.filter(Shift.opened_at <= date_to + " 23:59:59")
    if status: q = q.filter(Shift.status == status)
    return [_shift_out(s) for s in q.order_by(Shift.opened_at.desc()).limit(200).all()]


@router.get("/{sid}/summary")
def summary(sid: str, user: User = Depends(require_perm("shifts:view")), db: Session = Depends(get_db)):
    s = db.get(Shift, sid)
    if not s: raise HTTPException(404, "Смена не найдена")
    return shift_summary(db, s)


class CashOpIn(BaseModel):
    amount: float = Field(gt=0)
    reason: str
    to_login: Optional[str] = None  # для передачи другому сотруднику


def _cash_op(sid: str, body: CashOpIn, type_: str, perm_user, db, need_approve=False):
    s = db.query(Shift).filter(Shift.id == sid).with_for_update().first()
    if not s: raise HTTPException(404, "Смена не найдена")
    if s.status != "open":
        raise HTTPException(409, "Операции допустимы только в открытой смене")
    if not body.reason.strip():
        raise HTTPException(400, "Причина обязательна")
    to_user = None
    if type_ in ("transfer_out",):
        if not body.to_login: raise HTTPException(400, "Укажите получателя передачи")
        to_user = db.query(User).filter_by(login=body.to_login).first()
        if not to_user: raise HTTPException(404, "Получатель не найден")
        dst = db.query(Shift).filter(Shift.user_id == to_user.id, Shift.status == "open").first()
        if not dst: raise HTTPException(409, "У получателя нет открытой смены")
        db.add(CashOperation(shift_id=dst.id, type="transfer_in", amount=money(body.amount),
                             reason=body.reason, from_user=s.user_id, to_user=to_user.id))
    db.add(CashOperation(shift_id=s.id, type=type_, amount=money(body.amount), reason=body.reason,
                         from_user=s.user_id, to_user=to_user.id if to_user else None))
    log_event(db, user=perm_user, event_type=f"shift.cash_{type_}", object_type="shift", object_id=s.id,
              venue_id=s.venue_id, amount=float(body.amount), reason=body.reason, register_id=s.register_id)
    db.commit()
    return {"ok": True, "expected_cash": float(expected_cash(db, s))}


@router.post("/{sid}/cash-in")
def cash_in(sid: str, body: CashOpIn, user: User = Depends(require_perm("shifts:cashops")), db: Session = Depends(get_db)):
    return _cash_op(sid, body, "in", user, db)


@router.post("/{sid}/cash-out")
def cash_out(sid: str, body: CashOpIn, user: User = Depends(require_perm("shifts:cashops")), db: Session = Depends(get_db)):
    s = db.get(Shift, sid)
    exp = expected_cash(db, s)
    if Decimal(str(body.amount)) > exp and user.role not in ("owner", "manager", "shift_admin"):
        raise HTTPException(409, f"Изъятие больше ожидаемого остатка наличных ({exp} ₸) — требуется старший смены")
    return _cash_op(sid, body, "out", user, db)


@router.post("/{sid}/transfer")
def transfer(sid: str, body: CashOpIn, user: User = Depends(require_perm("shifts:cashops")), db: Session = Depends(get_db)):
    return _cash_op(sid, body, "transfer_out", user, db)


class CloseIn(BaseModel):
    actual_cash: float = Field(ge=0)


@router.post("/{sid}/close")
def close_shift(sid: str, body: CloseIn, user: User = Depends(require_perm("shifts:close")), db: Session = Depends(get_db)):
    s = db.query(Shift).filter(Shift.id == sid).with_for_update().first()
    if not s: raise HTTPException(404, "Смена не найдена")
    if s.status != "open":
        raise HTTPException(409, "Смена уже закрыта")
    unclosed = db.query(Order).filter(Order.shift_id == s.id,
                                      Order.status.notin_(["closed", "cancelled", "paid"])).count()
    if unclosed:
        raise HTTPException(409, f"В смене {unclosed} незакрытых неоплаченных заказов — закройте их или отмените")
    exp = expected_cash(db, s)
    fact = money(body.actual_cash)
    diff = money(fact - exp)
    s.status = "closed"; s.closed_at = now_almaty()
    s.expected_cash = exp; s.actual_cash = fact; s.difference = diff
    s.closed_by = user.id
    summ = shift_summary(db, s)
    log_event(db, user=user, event_type="shift.closed", object_type="shift", object_id=s.id, venue_id=s.venue_id,
              amount=float(diff), register_id=s.register_id,
              new_values={"expected": float(exp), "actual": float(fact), "diff": float(diff)})
    if abs(diff) > 0:
        from ..services.notify import notify
        notify(db, etype="shift.cash_diff", title=f"Расхождение кассы: {diff} ₸",
               body=f"Смена на кассе {s.register_id}: ожидалось {exp}, факт {fact}.", severity="warn",
               link_type="shift", link_id=s.id, amount=abs(float(diff)))
    db.commit()
    return {"shift": _shift_out(s), "summary": summ}


class ReopenIn(BaseModel):
    reason: str


@router.post("/{sid}/reopen")
def reopen_shift(sid: str, body: ReopenIn, user: User = Depends(require_perm("shifts:reopen")),
                 db: Session = Depends(get_db)):
    s = db.query(Shift).filter(Shift.id == sid).with_for_update().first()
    if not s: raise HTTPException(404, "Смена не найдена")
    if s.status != "closed":
        raise HTTPException(409, "Смена не закрыта")
    if not body.reason.strip():
        raise HTTPException(400, "Причина переоткрытия обязательна")
    old = {"expected": float(s.expected_cash or 0), "actual": float(s.actual_cash or 0)}
    s.status = "open"; s.closed_at = None; s.expected_cash = None; s.actual_cash = None
    s.difference = None; s.reopen_count += 1
    log_event(db, user=user, event_type="shift.reopened", object_type="shift", object_id=s.id, venue_id=s.venue_id,
              reason=body.reason, old_values=old, register_id=s.register_id)
    db.commit()
    return _shift_out(s)
