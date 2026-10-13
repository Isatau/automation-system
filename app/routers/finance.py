"""Финансовый модуль: статьи, счета, проводки (only-reverse), P&L, ДДС, долги/авансы."""
from datetime import datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional, List
from ..deps import get_db, require_perm
from ..db import now_almaty, money, new_id
from ..models import (FinancialAccount, ExpenseCategory, FinTransaction, DebtRecord, User, Supplier)
from ..services.audit import log_event

router = APIRouter(prefix="/finance", tags=["finance"])

TX_TYPES = {"sale_revenue", "cost", "expense", "advance", "payout", "debt_in", "debt_out",
            "transfer", "correction", "reverse"}


def _tx_out(t: FinTransaction, db: Session):
    cat = db.get(ExpenseCategory, t.category_id) if t.category_id else None
    acc = db.get(FinancialAccount, t.account_id) if t.account_id else None
    return {"id": t.id, "type": t.type, "direction": t.direction, "amount": float(t.amount),
            "date": t.date.isoformat(sep=" ", timespec="seconds") if t.date else None,
            "category": cat.name if cat else None, "category_kind": cat.kind if cat else None,
            "account": acc.name if acc else None, "account_id": t.account_id,
            "doc_ref": t.doc_ref, "counterparty": t.counterparty, "comment": t.comment,
            "status": t.status, "reversed_of_id": t.reversed_of_id,
            "by_user": db.get(User, t.by_user).name if t.by_user and db.get(User, t.by_user) else None}


def _apply_account_delta(db, t: FinTransaction, sign=1):
    """Движение по счёту: in +, out -; transfer между счетами обрабатывается двумя проводками."""
    if not t.account_id:
        return
    acc = db.query(FinancialAccount).filter(FinancialAccount.id == t.account_id).with_for_update().first()
    if not acc:
        return
    delta = Decimal(str(t.amount)) * (1 if t.direction == "in" else -1) * sign
    acc.balance = money(Decimal(str(acc.balance)) + delta)


# ---------- Справочники ----------

class AccountIn(BaseModel):
    name: str
    kind: str = "cash"     # cash|bank|mobile|other
    opening_balance: float = 0


@router.get("/accounts")
def list_accounts(user: User = Depends(require_perm("finance:view")), db: Session = Depends(get_db)):
    return [{"id": a.id, "name": a.name, "kind": a.kind, "balance": float(a.balance), "archived": a.archived}
            for a in db.query(FinancialAccount).filter_by(venue_id=user.venue_id).all()]


@router.post("/accounts")
def create_account(body: AccountIn, user: User = Depends(require_perm("finance:operate")), db: Session = Depends(get_db)):
    a = FinancialAccount(venue_id=user.venue_id, name=body.name, kind=body.kind, balance=money(body.opening_balance))
    db.add(a); db.flush()
    log_event(db, user=user, event_type="finance.account_created", object_type="fin_account", object_id=a.id,
              venue_id=user.venue_id, new_values={"name": a.name, "opening": body.opening_balance})
    db.commit(); db.refresh(a)
    return {"id": a.id, "name": a.name, "balance": float(a.balance)}


class CategoryIn(BaseModel):
    name: str
    kind: str = "opex"     # cogs|opex|payroll|tax|other
    parent_id: Optional[str] = None


@router.get("/categories")
def list_categories(user: User = Depends(require_perm("finance:view")), db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "kind": c.kind, "parent_id": c.parent_id}
            for c in db.query(ExpenseCategory).order_by(ExpenseCategory.kind, ExpenseCategory.name).all()]


@router.post("/categories")
def create_category(body: CategoryIn, user: User = Depends(require_perm("finance:operate")), db: Session = Depends(get_db)):
    if body.kind not in ("cogs", "opex", "payroll", "tax", "other"):
        raise HTTPException(400, "Недопустимый вид статьи")
    c = ExpenseCategory(venue_id=user.venue_id, **body.model_dump())
    db.add(c); db.commit(); db.refresh(c)
    log_event(db, user=user, event_type="finance.category_created", object_type="expense_category", object_id=c.id)
    return {"id": c.id, "name": c.name}


# ---------- Проводки ----------

class TxIn(BaseModel):
    type: str
    direction: str = "out"           # in|out
    amount: float = Field(gt=0)
    date: Optional[datetime] = None
    account_id: Optional[str] = None
    category_id: Optional[str] = None
    counterparty: Optional[str] = None
    comment: Optional[str] = None
    to_account_id: Optional[str] = None  # для transfer — счёт-получатель


@router.post("/transactions")
def create_tx(body: TxIn, user: User = Depends(require_perm("finance:operate")), db: Session = Depends(get_db)):
    """Ручная проводка (расход/приход/перенос между счетами). Автоматические создаются модулями."""
    if body.type not in TX_TYPES or body.type in ("sale_revenue", "cost", "reverse"):
        raise HTTPException(400, "Этот тип проводки формируется автоматически")
    if body.direction not in ("in", "out"):
        raise HTTPException(400, "direction: in|out")
    if body.type == "transfer":
        if not (body.account_id and body.to_account_id) or body.account_id == body.to_account_id:
            raise HTTPException(400, "Перенос: укажите два разных счёта")
    t = FinTransaction(venue_id=user.venue_id, type=body.type, direction=body.direction,
                       amount=money(body.amount), date=body.date or now_almaty(),
                       account_id=body.account_id, category_id=body.category_id,
                       counterparty=body.counterparty, comment=body.comment, by_user=user.id, status="posted")
    db.add(t); db.flush()
    _apply_account_delta(db, t)
    if body.type == "transfer":
        t2 = FinTransaction(venue_id=user.venue_id, type="transfer", direction="in", amount=t.amount,
                            date=t.date, account_id=body.to_account_id, counterparty=body.counterparty,
                            comment=f"Входящий перенос от {t.comment or '—'}", by_user=user.id, status="posted")
        db.add(t2); db.flush()
        _apply_account_delta(db, t2)
    log_event(db, user=user, event_type="finance.tx_created", object_type="fin_transaction", object_id=t.id,
              venue_id=user.venue_id, amount=float(t.amount),
              new_values={"type": t.type, "direction": t.direction, "counterparty": body.counterparty})
    db.commit(); db.refresh(t)
    return _tx_out(t, db)


@router.get("/transactions")
def list_tx(date_from: Optional[str] = None, date_to: Optional[str] = None, type: Optional[str] = None,
            category_id: Optional[str] = None, account_id: Optional[str] = None, limit: int = 300,
            user: User = Depends(require_perm("finance:view")), db: Session = Depends(get_db)):
    q = db.query(FinTransaction)
    if date_from: q = q.filter(FinTransaction.date >= date_from)
    if date_to: q = q.filter(FinTransaction.date <= date_to + " 23:59:59")
    if type: q = q.filter(FinTransaction.type == type)
    if category_id: q = q.filter(FinTransaction.category_id == category_id)
    if account_id: q = q.filter(FinTransaction.account_id == account_id)
    rows = q.order_by(FinTransaction.date.desc()).limit(limit).all()
    return [_tx_out(t, db) for t in rows]


@router.post("/transactions/{tid}/reverse")
def reverse_tx(tid: str, reason: str = "", user: User = Depends(require_perm("finance:operate")),
               db: Session = Depends(get_db)):
    """Исправление задним числом только через сторно: исходник остаётся, создаётся обратная проводка."""
    if not reason.strip():
        raise HTTPException(400, "Причина сторнирования обязательна")
    t = db.query(FinTransaction).filter(FinTransaction.id == tid).with_for_update().first()
    if not t: raise HTTPException(404, "Проводка не найдена")
    if t.status != "posted":
        raise HTTPException(409, "Сторнировать можно только проведённую проводку")
    if t.reversed_of_id:
        raise HTTPException(409, "Это уже сторно — сторнировать сторно нельзя")
    rev = FinTransaction(venue_id=t.venue_id, account_id=t.account_id, category_id=t.category_id,
                         type="reverse", direction=("out" if t.direction == "in" else "in"), amount=t.amount,
                         date=now_almaty(), doc_ref=t.doc_ref, counterparty=t.counterparty,
                         comment=f"Сторно #{t.id[:8]}: {reason}", by_user=user.id, status="posted",
                         reversed_of_id=t.id)
    db.add(rev); db.flush()
    _apply_account_delta(db, rev)
    t.status = "reversed"
    log_event(db, user=user, event_type="finance.tx_reversed", object_type="fin_transaction", object_id=t.id,
              venue_id=t.venue_id, amount=float(t.amount), reason=reason,
              new_values={"reverse_id": rev.id})
    db.commit(); db.refresh(rev)
    return _tx_out(rev, db)


# ---------- Отчёты ----------

def _period(date_from: Optional[str], date_to: Optional[str]):
    df = datetime.fromisoformat(date_from) if date_from else now_almaty().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    dt = datetime.fromisoformat(date_to + " 23:59:59") if date_to else now_almaty()
    return df, dt


@router.get("/pnl")
def pnl(date_from: Optional[str] = None, date_to: Optional[str] = None,
        group: str = "month",  # day|week|month
        user: User = Depends(require_perm("finance:view")), db: Session = Depends(get_db)):
    """ОТ → валовая → операционная → чистая прибыль. Себестоимость — из проводок cost."""
    df, dt = _period(date_from, date_to)
    txs = db.query(FinTransaction).filter(FinTransaction.date >= df, FinTransaction.date <= dt,
                                          FinTransaction.status == "posted").all()

    def bucket(d: datetime):
        if group == "day": return d.strftime("%Y-%m-%d")
        if group == "week": return f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
        return d.strftime("%Y-%m")

    buckets: dict[str, dict] = {}
    totals = {"revenue": 0.0, "cogs": 0.0, "gross": 0.0, "opex": 0.0, "operating": 0.0, "other": 0.0, "net": 0.0}
    for t in txs:
        b = buckets.setdefault(bucket(t.date), {"revenue": 0.0, "cogs": 0.0, "opex": 0.0, "payroll": 0.0,
                                                "tax": 0.0, "other": 0.0})
        cat = db.get(ExpenseCategory, t.category_id) if t.category_id else None
        kind = cat.kind if cat else "other"
        amt = float(t.amount)
        if t.type == "sale_revenue" and t.direction == "in":
            b["revenue"] += amt
        elif t.type == "cost":
            b["cogs"] += amt
        elif t.type in ("expense", "payout", "advance", "debt_out", "correction", "reverse") and t.direction == "out":
            key = kind if kind in b else "other"
            if kind == "payroll":
                b["payroll"] += amt; b["opex"] += amt
            elif kind == "tax":
                b["tax"] += amt; b["opex"] += amt
            else:
                b[key] += amt
                if kind == "cogs":
                    b["cogs"] += amt; b["opex"] -= amt
    rows = []
    for k in sorted(buckets):
        b = buckets[k]
        gross = b["revenue"] - b["cogs"]
        operating = gross - b["opex"]
        net = operating - b["other"] if b["other"] else operating
        rows.append({"period": k, "revenue": round(b["revenue"], 2), "cogs": round(b["cogs"], 2),
                     "gross_profit": round(gross, 2), "opex": round(b["opex"], 2),
                     "payroll": round(b["payroll"], 2), "tax": round(b["tax"], 2),
                     "operating_profit": round(operating, 2), "other": round(b["other"], 2),
                     "net_profit": round(net, 2)})
        for kk in ("revenue", "cogs", "opex", "other"):
            totals[kk] += b[kk]
        totals["payroll"] = b.get("payroll", 0)
    totals["gross"] = round(totals["revenue"] - totals["cogs"], 2)
    totals["operating"] = round(totals["gross"] - totals["opex"], 2)
    totals["net"] = round(totals["operating"] - totals["other"], 2)
    for kk in ("revenue", "cogs", "opex", "other"):
        totals[kk] = round(totals[kk], 2)
    return {"rows": rows, "totals": totals}


@router.get("/cashflow")
def cashflow(date_from: Optional[str] = None, date_to: Optional[str] = None,
             user: User = Depends(require_perm("finance:view")), db: Session = Depends(get_db)):
    """ДДС: разрезание смешанных потоков на операционный/инвестиционный/финансовый."""
    df, dt = _period(date_from, date_to)
    txs = db.query(FinTransaction).filter(FinTransaction.date >= df, FinTransaction.date <= dt,
                                          FinTransaction.status.in_(["posted", "reversed"]),
                                          FinTransaction.account_id.isnot(None)).all()
    op_in = op_out = inv_in = inv_out = fin_in = fin_out = Decimal("0")
    for t in txs:
        if t.status == "reversed" and t.type != "reverse":
            continue
        amt = Decimal(str(t.amount))
        sign = 1 if t.direction == "in" else -1
        if t.type == "reverse":
            sign = -sign
        a = db.get(FinancialAccount, t.account_id)
        cat = db.get(ExpenseCategory, t.category_id) if t.category_id else None
        if t.type in ("sale_revenue", "cost", "expense", "advance", "payout") or (cat and cat.kind in ("cogs", "opex", "payroll", "tax")):
            # авансы сотрудникам и выплаты ЗП — финансовый поток
            if t.type in ("advance", "payout"):
                if sign > 0: fin_in += amt
                else: fin_out += amt
            else:
                if sign > 0: op_in += amt
                else: op_out += amt
        elif t.type == "transfer":
            continue  # внутренний перенос не влияет на ДДС в целом
        elif t.type in ("debt_in", "debt_out"):
            if sign > 0: fin_in += amt
            else: fin_out += amt
        else:
            if sign > 0: inv_in += amt
            else: inv_out += amt
    def f(d): return float(round(d, 2))
    return {
        "operating": {"inflows": f(op_in), "outflows": f(op_out), "net": f(op_in - op_out)},
        "investing": {"inflows": f(inv_in), "outflows": f(inv_out), "net": f(inv_in - inv_out)},
        "financing": {"inflows": f(fin_in), "outflows": f(fin_out), "net": f(fin_in - fin_out)},
        "total_net": f(op_in - op_out + inv_in - inv_out + fin_in - fin_out),
    }


# ---------- Долги и авансы ----------

class DebtIn(BaseModel):
    party: str
    kind: str = "payable"   # receivable|payable|advance
    amount: float = Field(gt=0)
    due_date: Optional[datetime] = None
    comment: Optional[str] = None


@router.get("/debts")
def list_debts(status: Optional[str] = None, kind: Optional[str] = None,
               user: User = Depends(require_perm("finance:view")), db: Session = Depends(get_db)):
    q = db.query(DebtRecord)
    if status: q = q.filter(DebtRecord.status == status)
    if kind: q = q.filter(DebtRecord.kind == kind)
    return [{"id": d.id, "party": d.party, "kind": d.kind, "amount": float(d.amount),
             "closed_amount": float(d.closed_amount), "remaining": float(Decimal(str(d.amount)) - Decimal(str(d.closed_amount))),
             "due_date": d.due_date.isoformat() if d.due_date else None, "status": d.status,
             "overdue": bool(d.due_date and d.due_date < now_almaty() and d.status != "closed"),
             "comment": d.comment}
            for d in q.order_by(DebtRecord.due_date.is_(None), DebtRecord.due_date).all()]


@router.post("/debts")
def create_debt(body: DebtIn, user: User = Depends(require_perm("finance:operate")), db: Session = Depends(get_db)):
    if body.kind not in ("receivable", "payable", "advance"):
        raise HTTPException(400, "kind: receivable|payable|advance")
    d = DebtRecord(venue_id=user.venue_id, party=body.party, kind=body.kind, amount=money(body.amount),
                   due_date=body.due_date, comment=body.comment, by_user=user.id)
    db.add(d); db.flush()
    log_event(db, user=user, event_type="finance.debt_created", object_type="debt", object_id=d.id,
              venue_id=user.venue_id, amount=float(d.amount), new_values={"party": d.party, "kind": d.kind})
    db.commit(); db.refresh(d)
    return {"id": d.id, "status": d.status}


class CloseIn(BaseModel):
    amount: float = Field(gt=0)
    account_id: Optional[str] = None
    comment: Optional[str] = None


@router.post("/debts/{did}/close-payment")
def close_debt(did: str, body: CloseIn, user: User = Depends(require_perm("finance:operate")), db: Session = Depends(get_db)):
    """Закрытие долга/аванса фактическим расчётом — создаёт проводку и гасит запись."""
    d = db.query(DebtRecord).filter(DebtRecord.id == did).with_for_update().first()
    if not d: raise HTTPException(404, "Запись не найдена")
    if d.status == "closed": raise HTTPException(409, "Уже закрыто")
    remaining = Decimal(str(d.amount)) - Decimal(str(d.closed_amount))
    amt = money(body.amount)
    if amt > remaining:
        raise HTTPException(409, f"Сумма больше остатка долга ({remaining} ₸)")
    d.closed_amount = money(Decimal(str(d.closed_amount)) + amt)
    d.status = "closed" if Decimal(str(d.closed_amount)) >= Decimal(str(d.amount)) else "partial"
    tx_type = "debt_out" if d.kind in ("payable", "advance") else "debt_in"
    direction = "out" if tx_type == "debt_out" else "in"
    t = FinTransaction(venue_id=user.venue_id, account_id=body.account_id, type=tx_type, direction=direction,
                       amount=amt, date=now_almaty(), doc_ref=d.id, counterparty=d.party,
                       comment=body.comment or f"Расчёт по записи: {d.comment or d.party}", by_user=user.id)
    db.add(t); db.flush()
    _apply_account_delta(db, t)
    log_event(db, user=user, event_type="finance.debt_closed", object_type="debt", object_id=d.id,
              venue_id=user.venue_id, amount=float(amt), new_values={"status": d.status})
    db.commit()
    return {"id": d.id, "status": d.status, "closed_amount": float(d.closed_amount)}
