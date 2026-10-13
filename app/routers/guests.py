"""Гости и программа лояльности.

Правила:
- бонусный баланс меняется ТОЛЬКО через BonusOperation (audit trail);
- депозит — только через DepositOperation; снятие депозита требует payments:create;
- промокоды и сертификаты проверяются по срокам/лимиту использований;
- все ручные корректировки пишутся в журнал аудита с причиной.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import money, now_almaty
from ..deps import get_db, current_user, require_perm
from ..models import (Guest, BonusOperation, DepositAccount, DepositOperation,
                      DiscountRule, PromoCode, GiftCertificate, CertificateOperation, User)
from ..services.audit import log_event
from ..services import loyalty_service as ls

router = APIRouter(prefix="/guests", tags=["guests"])


class GuestIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    phone: str = Field(min_length=6, max_length=30)
    email: str | None = None
    card_number: str | None = None
    tier: str = "base"
    birthday: datetime | None = None
    note: str | None = None


@router.get("", dependencies=[Depends(require_perm("guests:view"))])
def list_guests(q: str = "", limit: int = 50, offset: int = 0,
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    sq = db.query(Guest).filter(Guest.archived == False)  # noqa
    if q:
        like = f"%{q}%"
        sq = sq.filter((Guest.name.ilike(like)) | (Guest.phone.ilike(like)) | (Guest.card_number.ilike(like)))
    total = sq.count()
    rows = sq.order_by(Guest.created_at.desc()).offset(limit).limit(limit).all()
    return {"total": total, "items": [_g(d, db) for d in rows]}


def _g(g: Guest, db) -> dict:
    dep = db.query(DepositAccount).filter_by(guest_id=g.id).first()
    return {"id": g.id, "name": g.name, "phone": g.phone, "email": g.email,
            "card_number": g.card_number, "tier": g.tier,
            "bonuses": float(g.bonuses_balance or 0),
            "deposit": float(dep.balance) if dep else 0.0,
            "birthday": g.birthday.isoformat() if g.birthday else None,
            "note": g.note, "archived": g.archived}


@router.post("", dependencies=[Depends(require_perm("guests:manage"))])
def create_guest(body: GuestIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if db.query(Guest).filter(Guest.phone == body.phone, Guest.archived == False).first():
        raise HTTPException(409, "Гость с таким телефоном уже существует")
    if body.card_number and db.query(Guest).filter_by(card_number=body.card_number).first():
        raise HTTPException(409, "Номер карты занят")
    g = Guest(**body.model_dump())
    db.add(g); db.flush()
    ls.deposit_for(db, g.id)
    log_event(db, user=user, event_type="guest.create", object_type="guest", object_id=g.id)
    db.commit()
    return _g(g, db)


@router.put("/{gid}", dependencies=[Depends(require_perm("guests:manage"))])
def update_guest(gid: str, body: GuestIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = db.get(Guest, gid)
    if not g or g.archived:
        raise HTTPException(404, "Гость не найден")
    old = {"name": g.name, "phone": g.phone, "tier": g.tier}
    for k, v in body.model_dump().items():
        setattr(g, k, v)
    log_event(db, user=user, event_type="guest.update", object_type="guest", object_id=g.id,
              old_values=old, new_values=body.model_dump(mode="json"))
    db.commit()
    return _g(g, db)


@router.post("/{gid}/archive", dependencies=[Depends(require_perm("guests:manage"))])
def archive_guest(gid: str, reason: str = Query(..., min_length=3),
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = db.get(Guest, gid)
    if not g:
        raise HTTPException(404, "Гость не найден")
    dep = db.query(DepositAccount).filter_by(guest_id=gid).first()
    if dep and money(dep.balance) != 0:
        raise HTTPException(409, "Нельзя архивировать гостя с ненулевым депозитом")
    if money(g.bonuses_balance or 0) != 0:
        raise HTTPException(409, "Сначала обнулите бонусы (списанием с причиной)")
    g.archived = True
    log_event(db, user=user, event_type="guest.archive", object_type="guest", object_id=gid, reason=reason)
    db.commit()
    return {"ok": True}


@router.get("/{gid}/history", dependencies=[Depends(require_perm("guests:view"))])
def guest_history(gid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = db.get(Guest, gid)
    if not g:
        raise HTTPException(404, "Гость не найден")
    bonuses = db.query(BonusOperation).filter_by(guest_id=gid).order_by(BonusOperation.created_at.desc()).limit(200).all()
    deps = db.query(DepositOperation).filter_by(guest_id=gid).order_by(DepositOperation.created_at.desc()).limit(200).all()
    from ..models import Order
    orders = db.query(Order).filter_by(guest_id=gid).order_by(Order.created_at.desc()).limit(100).all()
    return {
        "bonuses": [{"at": b.created_at.isoformat(), "delta": float(b.delta),
                     "balance_after": float(b.balance_after), "reason": b.reason,
                     "order_id": b.order_id} for b in bonuses],
        "deposits": [{"at": d.created_at.isoformat(), "type": d.type, "amount": float(d.amount),
                      "balance_after": float(d.balance_after), "reason": d.reason,
                      "order_id": d.order_id} for d in deps],
        "orders": [{"id": o.id, "number": o.number, "status": o.status,
                    "total": float(o.total or 0), "at": o.created_at.isoformat()} for o in orders],
    }


# ---------------- Бонусы ----------------
class BonusIn(BaseModel):
    delta: float = Field(description="+ начисление / - списание")
    reason: str = Field(min_length=3, max_length=200)


@router.post("/{gid}/bonuses", dependencies=[Depends(require_perm("loyalty:adjust"))])
def adjust_bonus(gid: str, body: BonusIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = db.get(Guest, gid)
    if not g or g.archived:
        raise HTTPException(404, "Гость не найден")
    try:
        op = ls.bonus_adjust(db, g, money(body.delta), body.reason, user=user)
    except ValueError as e:
        raise HTTPException(422, str(e))
    log_event(db, user=user, event_type="loyalty.bonus_adjust", object_type="guest", object_id=gid,
              amount=money(body.delta), reason=body.reason)
    db.commit()
    return {"balance": float(g.bonuses_balance), "operation_id": op.id}


# ---------------- Депозиты ----------------
class DepositIn(BaseModel):
    amount: float = Field(gt=0)
    reason: str = Field(min_length=3, max_length=200)


@router.post("/{gid}/deposits", dependencies=[Depends(require_perm("guests:manage"))])
def deposit_in(gid: str, body: DepositIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = db.get(Guest, gid)
    if not g or g.archived:
        raise HTTPException(404, "Гость не найден")
    op = ls.deposit_op(db, g, "in", money(body.amount), body.reason, user=user)
    log_event(db, user=user, event_type="guest.deposit_in", object_type="guest", object_id=gid,
              amount=money(body.amount), reason=body.reason)
    db.commit()
    return {"balance": float(op.balance_after), "operation_id": op.id}


@router.post("/{gid}/deposits/withdraw", dependencies=[Depends(require_perm("payments:create"))])
def deposit_out(gid: str, body: DepositIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Возврат депозита гостю наличными — право кассира+, аудит с причиной."""
    g = db.get(Guest, gid)
    if not g or g.archived:
        raise HTTPException(404, "Гость не найден")
    try:
        op = ls.deposit_op(db, g, "out", money(body.amount), body.reason, user=user)
    except ValueError as e:
        raise HTTPException(422, str(e))
    log_event(db, user=user, event_type="guest.deposit_out", object_type="guest", object_id=gid,
              amount=money(body.amount), reason=body.reason)
    db.commit()
    return {"balance": float(op.balance_after), "operation_id": op.id}


# ---------------- Правила лояльности / промокоды ----------------
class RuleIn(BaseModel):
    name: str
    kind: str = "percent"          # percent|amount|bonus_accrual
    value: float = 0
    max_share_pct: float = 100
    active: bool = True


@router.get("/loyalty/rules", dependencies=[Depends(require_perm("guests:view"))])
def list_rules(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.query(DiscountRule).all()
    return [{"id": r.id, "name": r.name, "kind": r.kind, "value": float(r.value),
             "max_share_pct": float(r.max_share_pct), "active": r.active} for r in rows]


@router.post("/loyalty/rules", dependencies=[Depends(require_perm("admin:settings"))])
def upsert_rule(body: RuleIn, rule_id: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    if rule_id:
        r = db.get(DiscountRule, rule_id)
        if not r:
            raise HTTPException(404, "Правило не найдено")
        for k, v in body.model_dump(exclude={"rule_id"}).items():
            setattr(r, k, v)
    else:
        r = DiscountRule(**body.model_dump())
        db.add(r)
    db.flush()
    log_event(db, user=user, event_type="loyalty.rule_change", object_type="discount_rule",
              object_id=r.id, new_values=body.model_dump(mode="json"))
    db.commit()
    return {"id": r.id}


class PromoIn(BaseModel):
    code: str = Field(min_length=3, max_length=40)
    discount_pct: float = Field(ge=0, le=100)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    max_uses: int | None = None


@router.post("/promocodes", dependencies=[Depends(require_perm("admin:settings"))])
def create_promo(body: PromoIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if db.query(PromoCode).filter_by(code=body.code.upper()).first():
        raise HTTPException(409, "Промокод уже существует")
    p = PromoCode(**body.model_dump()); p.code = body.code.upper()
    db.add(p)
    log_event(db, user=user, event_type="loyalty.promo_create", object_type="promo_code",
              object_id=p.id, new_values=body.model_dump(mode="json"))
    db.commit()
    return {"id": p.id, "code": p.code}


@router.get("/promocodes/validate/{code}", dependencies=[Depends(require_perm("orders:create"))])
def validate_promo(code: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = db.query(PromoCode).filter_by(code=code.upper(), active=True).first()
    if not p:
        raise HTTPException(404, "Промокод не найден или неактивен")
    now = now_almaty()
    if p.valid_from and now < p.valid_from.replace(tzinfo=now.tzinfo):
        raise HTTPException(422, "Промокод ещё не действует")
    if p.valid_to and now > p.valid_to.replace(tzinfo=now.tzinfo):
        raise HTTPException(422, "Срок действия промокода истёк")
    if p.max_uses is not None and p.used_count >= p.max_uses:
        raise HTTPException(422, "Лимит использований промокода исчерпан")
    return {"code": p.code, "discount_pct": float(p.discount_pct)}


@router.post("/promocodes/{pid}/use", dependencies=[Depends(require_perm("orders:create"))])
def use_promo(pid: str, order_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = db.get(PromoCode, pid)
    if not p or not p.active:
        raise HTTPException(404, "Промокод не найден")
    if p.max_uses is not None and p.used_count >= p.max_uses:
        raise HTTPException(422, "Лимит использований исчерпан")
    p.used_count += 1
    log_event(db, user=user, event_type="loyalty.promo_used", object_type="promo_code",
              object_id=p.id, new_values={"order_id": order_id})
    db.commit()
    return {"ok": True}


# ---------------- Подарочные сертификаты ----------------
class CertIn(BaseModel):
    code: str = Field(min_length=4, max_length=40)
    nominal: float = Field(gt=0)
    valid_days: int = Field(default=365, ge=1, le=3650)


@router.post("/certificates", dependencies=[Depends(require_perm("finance:operate"))])
def issue_cert(body: CertIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if db.query(GiftCertificate).filter_by(code=body.code.upper()).first():
        raise HTTPException(409, "Сертификат с таким кодом уже выпущен")
    c = GiftCertificate(code=body.code.upper(), nominal=money(body.nominal),
                        balance=money(body.nominal), issued_date=now_almaty(),
                        valid_to=now_almaty() + timedelta(days=body.valid_days))
    db.add(c)
    log_event(db, user=user, event_type="loyalty.cert_issue", object_type="certificate",
              object_id=c.id, amount=money(body.nominal))
    db.commit()
    return {"id": c.id, "code": c.code, "nominal": float(c.nominal)}


@router.get("/certificates/{code}", dependencies=[Depends(require_perm("guests:view"))])
def cert_status(code: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.query(GiftCertificate).filter_by(code=code.upper()).first()
    if not c:
        raise HTTPException(404, "Сертификат не найден")
    ops = db.query(CertificateOperation).filter_by(certificate_id=c.id)\
        .order_by(CertificateOperation.created_at.desc()).limit(50).all()
    return {"code": c.code, "status": c.status, "nominal": float(c.nominal),
            "balance": float(c.balance), "valid_to": c.valid_to.isoformat() if c.valid_to else None,
            "operations": [{"at": o.created_at.isoformat(), "amount": float(o.amount),
                            "balance_after": float(o.balance_after), "order_id": o.order_id} for o in ops]}


@router.post("/certificates/{code}/block", dependencies=[Depends(require_perm("loyalty:adjust"))])
def block_cert(code: str, reason: str = Query(..., min_length=3),
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.query(GiftCertificate).filter_by(code=code.upper()).first()
    if not c:
        raise HTTPException(404, "Сертификат не найден")
    c.status = "blocked"
    log_event(db, user=user, event_type="loyalty.cert_block", object_type="certificate",
              object_id=c.id, reason=reason)
    db.commit()
    return {"ok": True}


# ---------------- Отчёт по скидкам ----------------
@router.get("/discount-usage", dependencies=[Depends(require_perm("reports:view"))])
def discount_usage(date_from: datetime | None = None, date_to: datetime | None = None,
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    from ..models import SaleDiscount
    q = db.query(SaleDiscount)
    if date_from:
        q = q.filter(SaleDiscount.created_at >= date_from)
    if date_to:
        q = q.filter(SaleDiscount.created_at <= date_to)
    rows = q.all()
    total_amt = sum(money(d.amount or 0) for d in rows)
    by_reason: dict[str, float] = {}
    for d in rows:
        by_reason[d.reason[:60]] = by_reason.get(d.reason[:60], 0) + float(d.amount or 0)
    return {"count": len(rows), "total_discount": float(total_amt),
            "by_reason": by_reason,
            "items": [{"at": d.created_at.isoformat(), "order_id": d.order_id, "pct": float(d.pct),
                       "amount": float(d.amount or 0), "reason": d.reason,
                       "approved_by": d.approved_by} for d in rows[-200:]]}
