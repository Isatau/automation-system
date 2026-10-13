"""Хранилище документов: Z-отчёты, фото расходов, договоры.

Оригиналы — в защищённой папке (не отдаются без права), БД хранит ссылки,
метаданные и sha256 для контроля целостности. OCR — подключаемый адаптер
(по умолчанию выключен, честно возвращает none; интеграция с реальным OCR —
отдельный внешний сервис, см. docs/ARCHITECTURE.md).
"""
from __future__ import annotations
import hashlib, os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import money, now_almaty
from ..deps import get_db, current_user, require_perm
from ..models import Document, User, Shift
from ..services.audit import log_event
from ..config import settings

router = APIRouter(prefix="/documents", tags=["documents"])

KINDS = ("z_report", "expense", "contract", "other")


def _safe_join(base: str, name: str) -> str:
    """Защита от path traversal при скачивании."""
    p = os.path.realpath(os.path.join(base, name))
    if not p.startswith(os.path.realpath(base) + os.sep):
        raise HTTPException(400, "Некорректный путь файла")
    return p


@router.post("", dependencies=[Depends(require_perm("documents:upload"))])
async def upload(file: UploadFile = File(...), kind: str = Form(..., alias="kind"),
                 shift_id: str | None = Form(None), order_id: str | None = Form(None),
                 doc_date: datetime | None = Form(None),
                 amount_declared: float | None = Form(None),
                 comment: str | None = Form(None),
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    if kind not in KINDS:
        raise HTTPException(422, f"Тип документа должен быть одним из: {', '.join(KINDS)}")
    if kind == "z_report":
        if not shift_id:
            raise HTTPException(422, "Z-отчёт должен быть привязан к кассовой смене")
        if not db.get(Shift, shift_id):
            raise HTTPException(404, "Смена не найдена")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".pdf", ".webp"):
        raise HTTPException(422, "Допустимые форматы: jpg, png, webp, pdf")
    blob = await file.read()
    if len(blob) > 15 * 1024 * 1024:
        raise HTTPException(422, "Файл больше 15 МБ")
    sha = hashlib.sha256(blob).hexdigest()
    fname = f"{sha[:16]}{ext}"
    updir = os.path.abspath(settings.upload_dir)
    os.makedirs(updir, exist_ok=True)
    path = os.path.join(updir, fname)
    with open(path, "wb") as f:
        f.write(blob)
    d = Document(kind=kind, shift_id=shift_id, order_id=order_id,
                 file_path=fname, file_name=file.filename or fname, sha256=sha,
                 size_bytes=len(blob), doc_date=doc_date or now_almaty(),
                 amount_declared=money(amount_declared) if amount_declared is not None else None,
                 comment=comment, uploaded_by=user.id, venue_id=user.venue_id)
    db.add(d)
    log_event(db, user=user, event_type="document.upload", object_type="document",
              object_id=d.id, amount=money(amount_declared) if amount_declared is not None else None,
              new_values={"kind": kind, "shift_id": shift_id, "sha256": sha})
    db.commit()
    return {"id": d.id, "sha256": sha, "size": d.size_bytes}


@router.get("", dependencies=[Depends(require_perm("documents:upload"))])
def list_docs(kind: str | None = None, shift_id: str | None = None,
              date_from: datetime | None = None, date_to: datetime | None = None,
              verified: str | None = Query(None, pattern="^(yes|no)$"),
              limit: int = 50, offset: int = 0,
              db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = db.query(Document)
    if kind:
        q = q.filter(Document.kind == kind)
    if shift_id:
        q = q.filter(Document.shift_id == shift_id)
    if date_from:
        q = q.filter(Document.doc_date >= date_from)
    if date_to:
        q = q.filter(Document.doc_date <= date_to)
    if verified == "yes":
        q = q.filter(Document.verified_by.is_not(None))
    elif verified == "no":
        q = q.filter(Document.verified_by.is_(None))
    total = q.count()
    rows = q.order_by(Document.doc_date.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [_d(d) for d in rows]}


def _d(d: Document) -> dict:
    return {"id": d.id, "kind": d.kind, "shift_id": d.shift_id, "order_id": d.order_id,
            "file_name": d.file_name, "sha256": d.sha256, "size": d.size_bytes,
            "doc_date": d.doc_date.isoformat() if d.doc_date else None,
            "amount_declared": float(d.amount_declared) if d.amount_declared is not None else None,
            "amount_ocr": float(d.amount_ocr) if d.amount_ocr is not None else None,
            "ocr_status": d.ocr_status,
            "verified_by": d.verified_by,
            "verified_at": d.verified_at.isoformat() if d.verified_at else None,
            "comment": d.comment}


@router.get("/{did}/integrity", dependencies=[Depends(require_perm("documents:verify"))])
def check_integrity(did: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404, "Документ не найден")
    path = _safe_join(os.path.abspath(settings.upload_dir), d.file_path)
    if not os.path.exists(path):
        return {"ok": False, "error": "Файл отсутствует в хранилище"}
    actual = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return {"ok": actual == d.sha256, "expected": d.sha256, "actual": actual}


@router.get("/{did}/file", dependencies=[Depends(require_perm("documents:upload"))])
def download(did: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404, "Документ не найден")
    path = _safe_join(os.path.abspath(settings.upload_dir), d.file_path)
    if not os.path.exists(path):
        raise HTTPException(410, "Файл утрачен — проверьте целостность хранилища")
    return FileResponse(path, filename=d.file_name)


@router.post("/{did}/verify", dependencies=[Depends(require_perm("documents:verify"))])
def verify(did: str, comment: str = Form(""), compare_shift: bool = Form(True),
           db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Отметка о проверке управляющим + сравнение заявленной суммы с данными продаж смены."""
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404, "Документ не найден")
    d.verified_by = user.id
    d.verified_at = now_almaty()
    result = {"diff": None}
    if compare_shift and d.kind == "z_report" and d.shift_id and d.amount_declared is not None:
        from ..models import Payment
        s = db.get(Shift, d.shift_id)
        paid = db.query(Payment).filter(Payment.shift_id == d.shift_id,
                                        Payment.status == "confirmed").all()
        sales = sum(money(p.amount) for p in paid) - sum(
            money(r.amount) for r in db.query(__import__("app.models", fromlist=["Refund"]).Refund)
            .filter_by(shift_id=d.shift_id).all())
        result["shift_sales"] = float(sales)
        result["declared"] = float(d.amount_declared)
        result["diff"] = float(d.amount_declared - sales)
    log_event(db, user=user, event_type="document.verify", object_type="document",
              object_id=did, reason=comment, new_values=result)
    db.commit()
    return {"ok": True, "comparison": result}


@router.post("/{did}/ocr", dependencies=[Depends(require_perm("documents:verify"))])
def ocr(did: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """OCR — дополнительная функция. Реальный движок подключается как внешний
    адаптер (см. app/integrations). Без настроенного сервиса честно возвращаем none."""
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404, "Документ не найден")
    adapter_url = os.environ.get("OCR_SERVICE_URL", "")
    if not adapter_url:
        d.ocr_status = "unavailable"
        db.commit()
        raise HTTPException(503, "OCR-сервис не настроен (OCR_SERVICE_URL). Распознавание недоступно.")
    import httpx
    try:
        with httpx.Client(timeout=30) as c:
            files = {"file": open(_safe_join(os.path.abspath(settings.upload_dir), d.file_path), "rb")}
            r = c.post(adapter_url.rstrip("/") + "/recognize", files=files)
            r.raise_for_status()
            amount = float(r.json().get("amount", 0))
        d.amount_ocr = money(amount)
        d.ocr_status = "done"
    except Exception as e:
        d.ocr_status = "failed"
        log_event(db, user=user, event_type="document.ocr_failed", object_type="document",
                  object_id=did, reason=str(e)[:200])
    db.commit()
    return {"ocr_status": d.ocr_status, "amount_ocr": float(d.amount_ocr) if d.amount_ocr else None}
