"""Базовый интерфейс адаптеров оборудования/сервисов: журнал ошибок и retry безопасных операций."""
from __future__ import annotations
import hashlib, json
from abc import ABC, abstractmethod
from fastapi import HTTPException
from ..db import now_almaty
from ..models import IntegrationLog

class AdapterResult:
    def __init__(self, ok: bool, ref: str = "", error: str = "", confirmed_by_device: bool = False):
        self.ok, self.ref, self.error, self.confirmed_by_device = ok, ref, error, confirmed_by_device

class BaseAdapter(ABC):
    name = "base"
    safe_retry = True   # операцию можно повторять (печать, telegram); фискал с неопределённым статусом — false

    @abstractmethod
    def execute(self, operation: str, payload: dict) -> AdapterResult: ...

    def run(self, db, operation: str, payload: dict, ref_id: str | None = None):
        ph = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]
        rec = IntegrationLog(adapter=self.name, operation=operation, ref_id=ref_id,
                             payload_hash=ph, state="sent")
        db.add(rec); db.flush()
        res = self.execute(operation, payload)
        rec.device_confirmed = res.confirmed_by_device
        if res.ok and res.confirmed_by_device:
            rec.state = "confirmed"
        elif res.ok:
            rec.state = "sent"      # отправлено, но устройство не подтвердило — НЕ успех
        else:
            rec.state = "failed"; rec.error = res.error
            rec.next_retry_at = now_almaty()
        return rec, res

def retry_failed(db, adapter_obj, limit=20):
    """Повторная обработка только безопасных операций в состоянии failed/sent."""
    out = []
    q = db.query(IntegrationLog).filter(IntegrationLog.state.in_(["failed", "sent"]),
                                        IntegrationLog.retries < 5)
    for rec in q.limit(limit).all():
        if not getattr(adapter_obj, "safe_retry", True):
            continue
        rec.retries += 1
        res = adapter_obj.execute(rec.operation, {"ref": rec.ref_id})
        rec.state = "confirmed" if (res.ok and res.confirmed_by_device) else ("failed" if not res.ok else "sent")
        rec.error = res.error or None
        out.append(rec)
    return out
