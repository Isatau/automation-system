"""Фискальные регистраторы Казахстана (ККМ/ОНКК → ОФД).
Реальный адаптер подключается заменой StubFiscalAdapter на драйвер поставщика (Штрих-М/Дримкас/Mercury API)
через ENV FISCAL_ADAPTER. Заглушка НИКОГДА не отмечает фискализацию подтверждённой — это честное состояние pending/failed."""
from .base import BaseAdapter, AdapterResult
from ..config import settings

class StubFiscalAdapter(BaseAdapter):
    name = "fiscal"
    safe_retry = False  # фискальный документ нельзя «повторно пробить» вслепую

    def execute(self, operation, payload):
        # Нет физического ККМ → операция ставится в очередь как неподтверждённая.
        return AdapterResult(ok=False, error="Фискальный регистратор не подключён (stub). Операция поставлена в очередь.",
                             confirmed_by_device=False)

class ShtrihKzAdapter(BaseAdapter):
    """Пример контракта реального адаптера (HTTP-сервер драйвера ККМ локально). Не проверен на оборудовании."""
    name = "fiscal"
    safe_retry = False
    def __init__(self, base_url: str):
        self.base_url = base_url
    def execute(self, operation, payload):
        try:
            import httpx
            r = httpx.post(f"{self.base_url}/{operation}", json=payload, timeout=10)
            data = r.json()
            ok = bool(data.get("success"))
            return AdapterResult(ok=ok, ref=data.get("fd_number", ""), error=data.get("error", ""),
                                 confirmed_by_device=ok)
        except Exception as e:
            return AdapterResult(ok=False, error=str(e))

def get_fiscal_adapter():
    if settings.fiscal_adapter == "shtrih_kz":
        import os
        return ShtrihKzAdapter(os.environ.get("FISCAL_DRIVER_URL", "http://127.0.0.1:8443"))
    return StubFiscalAdapter()
