"""Банковский POS-терминал. Данные карт НЕ хранятся; только mask PAN и auth-код из ответа терминала.
Интеграция — по документированному интерфейсу поставщика (пример контракта ниже). Без железа — stub."""
from .base import BaseAdapter, AdapterResult

class StubTerminalAdapter(BaseAdapter):
    name = "terminal"
    safe_retry = False   # платёж нельзя повторять вслепую
    def execute(self, operation, payload):
        return AdapterResult(ok=False, error="POS-терминал не подключён (stub)", confirmed_by_device=False)

class PinPadAdapter(BaseAdapter):
    """Контракт реального адаптера (например BBPOS/Ingenico через локальный сервис). Требует проверки на устройстве."""
    name = "terminal"
    safe_retry = False
    def __init__(self, url): self.url = url
    def execute(self, operation, payload):
        try:
            import httpx
            r = httpx.post(f"{self.url}/{operation}", json=payload, timeout=60)
            d = r.json()
            ok = d.get("approved") is True
            return AdapterResult(ok=ok, ref=d.get("rrn", ""), error=d.get("error", ""),
                                 confirmed_by_device=ok)
        except Exception as e:
            return AdapterResult(ok=False, error=str(e))
