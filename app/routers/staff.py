"""Сотрудники: карточки, график смен, учёт отработанного времени (часы/смены)."""
from datetime import datetime, timedelta, date as datetype
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional, List
from ..deps import get_db, require_perm
from ..db import now_almaty, money, new_id
from ..models import Staff, WorkSchedule, TimeEntry, User, Shift
from ..services.audit import log_event

router = APIRouter(prefix="/staff", tags=["staff"])


def _parse_day(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        raise HTTPException(400, "Дата в формате YYYY-MM-DD")


def _staff_out(s: Staff, db: Session):
    u = db.get(User, s.user_id) if s.user_id else None
    return {"id": s.id, "user_id": s.user_id, "name": s.name, "position": s.position, "phone": s.phone,
            "hire_date": s.hire_date.isoformat(sep=" ", timespec="seconds") if s.hire_date else None,
            "archived": s.archived, "pay_type": s.pay_type, "rate": float(s.rate),
            "sales_percent": float(s.sales_percent or 0), "login": u.login if u else None}


class StaffIn(BaseModel):
    name: str
    position: str
    phone: Optional[str] = None
    user_id: Optional[str] = None
    pay_type: str = "hourly"     # hourly|shift|percent
    rate: float = Field(0, ge=0)
    sales_percent: float = Field(0, ge=0, le=100)
    hire_date: Optional[datetype] = None


@router.get("")
def list_staff(active: Optional[bool] = None, q: Optional[str] = None,
               user: User = Depends(require_perm("staff:view")), db: Session = Depends(get_db)):
    qs = db.query(Staff).filter(Staff.venue_id == user.venue_id)
    if active is not None: qs = qs.filter(Staff.archived == (not active))
    if q: qs = qs.filter(Staff.name.ilike(f"%{q}%") | Staff.position.ilike(f"%{q}%"))
    return [_staff_out(s, db) for s in qs.order_by(Staff.name).all()]


@router.get("/{sid}")
def get_staff(sid: str, user: User = Depends(require_perm("staff:view")), db: Session = Depends(get_db)):
    s = db.get(Staff, sid)
    if not s: raise HTTPException(404, "Сотрудник не найден")
    return _staff_out(s, db)


@router.post("")
def create_staff(body: StaffIn, user: User = Depends(require_perm("staff:manage")), db: Session = Depends(get_db)):
    if body.pay_type not in ("hourly", "shift", "percent"):
        raise HTTPException(400, "pay_type: hourly|shift|percent")
    if body.user_id and db.query(Staff).filter_by(user_id=body.user_id).first():
        raise HTTPException(409, "Для этого пользователя уже есть карточка")
    s = Staff(venue_id=user.venue_id, **body.model_dump())
    if s.hire_date:
        s.hire_date = datetime.combine(s.hire_date, datetime.min.time())
    db.add(s); db.flush()
    log_event(db, user=user, event_type="staff.created", object_type="staff", object_id=s.id,
              venue_id=user.venue_id, new_values={"name": s.name, "position": s.position})
    db.commit(); db.refresh(s)
    return _staff_out(s, db)


@router.patch("/{sid}")
def patch_staff(sid: str, body: dict, user: User = Depends(require_perm("staff:manage")), db: Session = Depends(get_db)):
    s = db.get(Staff, sid)
    if not s: raise HTTPException(404, "Сотрудник не найден")
    allowed = {"name", "position", "phone", "pay_type", "rate", "sales_percent", "archived", "hire_date", "user_id"}
    if body.get("rate") is not None or body.get("sales_percent") is not None:
        from ..rbac import has_perm
        if not has_perm(user, db, "staff:payroll"):
            raise HTTPException(403, "Изменение ставки/процента требует права staff:payroll")
    old = {k: getattr(s, k) for k in body if k in allowed}
    for k, v in body.items():
        if k in allowed:
            setattr(s, k, v)
    if isinstance(s.hire_date, datetype) and not isinstance(s.hire_date, datetime):
        s.hire_date = datetime.combine(s.hire_date, datetime.min.time())
    log_event(db, user=user, event_type="staff.updated", object_type="staff", object_id=sid,
              old_values={k: str(v) for k, v in old.items()},
              new_values={k: str(v) for k, v in body.items() if k in allowed})
    db.commit()
    return _staff_out(s, db)


# ---------- График ----------

class ScheduleIn(BaseModel):
    staff_id: str
    date: datetype
    start_time: str      # "HH:MM"
    end_time: str
    note: Optional[str] = None


def _sch_out(w: WorkSchedule, db: Session):
    st = db.get(Staff, w.staff_id)
    return {"id": w.id, "staff_id": w.staff_id, "staff": st.name if st else "",
            "date": w.date.strftime("%Y-%m-%d"), "start_time": w.start_time, "end_time": w.end_time}


@router.get("/schedule")
def schedule_list(date_from: str, date_to: str, staff_id: Optional[str] = None,
                  user: User = Depends(require_perm("staff:view")), db: Session = Depends(get_db)):
    q = db.query(WorkSchedule).filter(WorkSchedule.date >= _parse_day(date_from),
                                      WorkSchedule.date <= _parse_day(date_to) + timedelta(days=1))
    if staff_id: q = q.filter(WorkSchedule.staff_id == staff_id)
    return [_sch_out(w, db) for w in q.order_by(WorkSchedule.date).all()]


@router.post("/schedule")
def schedule_add(body: ScheduleIn, user: User = Depends(require_perm("staff:schedule")), db: Session = Depends(get_db)):
    st = db.get(Staff, body.staff_id)
    if not st or st.archived: raise HTTPException(404, "Активный сотрудник не найден")
    try:
        sh, sm = map(int, body.start_time.split(":")); eh, em = map(int, body.end_time.split(":"))
        assert 0 <= sh <= 23 and 0 <= eh <= 23 and min(sm, em) >= 0 and max(sm, em) <= 59
    except Exception:
        raise HTTPException(400, "Время в формате HH:MM")
    if (sh, sm) >= (eh, em):
        raise HTTPException(400, "Конец смены должен быть позже начала (ночные смены — двумя записями)")
    day = _parse_day(body.date.isoformat())
    conflict = db.query(WorkSchedule).filter(WorkSchedule.staff_id == st.id, WorkSchedule.date == day,
                                             WorkSchedule.start_time < body.end_time,
                                             WorkSchedule.end_time > body.start_time).first()
    if conflict:
        raise HTTPException(409, "Пересечение с уже поставленной сменой на этот день")
    w = WorkSchedule(staff_id=st.id, date=day, start_time=body.start_time, end_time=body.end_time)
    db.add(w); db.flush()
    log_event(db, user=user, event_type="staff.schedule_added", object_type="schedule", object_id=w.id,
              venue_id=user.venue_id, new_values={"staff": st.name, "date": body.date.isoformat(),
                                                  "time": f"{body.start_time}-{body.end_time}"})
    db.commit(); db.refresh(w)
    return _sch_out(w, db)


@router.delete("/schedule/{wid}")
def schedule_del(wid: str, reason: str = "", user: User = Depends(require_perm("staff:schedule")),
                 db: Session = Depends(get_db)):
    if not reason.strip(): raise HTTPException(400, "Укажите причину снятия смены")
    w = db.get(WorkSchedule, wid)
    if not w: raise HTTPException(404, "Смена графика не найдена")
    db.delete(w)
    log_event(db, user=user, event_type="staff.schedule_removed", object_type="schedule", object_id=wid, reason=reason)
    db.commit()
    return {"ok": True}


# ---------- Учёт времени ----------

class TimeIn(BaseModel):
    staff_id: str
    date: datetype
    hours_worked: float = Field(ge=0, le=24)
    note: Optional[str] = None


@router.get("/time")
def time_list(month: Optional[str] = None, staff_id: Optional[str] = None,
              user: User = Depends(require_perm("staff:view")), db: Session = Depends(get_db)):
    q = db.query(TimeEntry)
    if month:
        first = _parse_day(month + "-01")
        last = (first.replace(day=28) + timedelta(days=7)).replace(day=1) - timedelta(days=1)
        q = q.filter(TimeEntry.clock_in >= first, TimeEntry.clock_in <= last + timedelta(days=1))
    if staff_id: q = q.filter(TimeEntry.staff_id == staff_id)
    rows = []
    for t in q.order_by(TimeEntry.clock_in).all():
        st = db.get(Staff, t.staff_id)
        rows.append({"id": t.id, "staff_id": t.staff_id, "staff": st.name if st else "",
                     "date": t.clock_in.strftime("%Y-%m-%d"), "hours_worked": float(t.hours_worked),
                     "late_minutes": t.late_minutes,
                     "planned_start": t.planned_start.strftime("%H:%M") if t.planned_start else None})
    return rows


@router.post("/time")
def time_upsert(body: TimeIn, user: User = Depends(require_perm("staff:schedule")), db: Session = Depends(get_db)):
    """Подтверждённое время за день: повторная запись перезаписывает (с аудитом old/new)."""
    st = db.get(Staff, body.staff_id)
    if not st: raise HTTPException(404, "Сотрудник не найден")
    day = _parse_day(body.date.isoformat())
    t = db.query(TimeEntry).filter(TimeEntry.staff_id == body.staff_id,
                                   TimeEntry.clock_in >= day, TimeEntry.clock_in < day + timedelta(days=1)).first()
    if t:
        old_h = float(t.hours_worked)
        t.hours_worked = body.hours_worked
        t.clock_out = day + timedelta(hours=min(23.99, body.hours_worked))
        log_event(db, user=user, event_type="staff.time_updated", object_type="time_entry", object_id=t.id,
                  old_values={"hours": old_h}, new_values={"hours": body.hours_worked}, reason=body.note)
    else:
        t = TimeEntry(staff_id=st.id, clock_in=day, clock_out=day + timedelta(hours=min(23.99, body.hours_worked)),
                      hours_worked=body.hours_worked)
        db.add(t); db.flush()
        log_event(db, user=user, event_type="staff.time_created", object_type="time_entry", object_id=t.id,
                  new_values={"hours": body.hours_worked}, reason=body.note)
    db.commit(); db.refresh(t)
    return {"id": t.id, "hours_worked": float(t.hours_worked)}


@router.get("/time/actual-shifts")
def actual_shift_hours(month: str, user: User = Depends(require_perm("staff:view")), db: Session = Depends(get_db)):
    """Факт по закрытым кассовым сменам — ориентир для сверки с графиком."""
    first = _parse_day(month + "-01")
    last = (first.replace(day=28) + timedelta(days=7)).replace(day=1)
    shifts = db.query(Shift).filter(Shift.status == "closed", Shift.opened_at >= first,
                                    Shift.closed_at.isnot(None), Shift.closed_at < last).all()
    out = {}
    for s in shifts:
        h = round((s.closed_at - s.opened_at).total_seconds() / 3600, 2)
        st = db.query(Staff).filter_by(user_id=s.user_id).first()
        key = st.name if st else s.user_id
        out[key] = round(out.get(key, 0) + h, 2)
    return [{"staff": k, "shift_hours": v} for k, v in sorted(out.items())]
