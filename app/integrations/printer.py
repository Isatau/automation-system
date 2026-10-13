"""Чековые и кухонные принтеры (Epson TM, LAN/USB/CUPS). Печать — безопасная для повтора операция."""
import shutil, subprocess
from .base import BaseAdapter, AdapterResult

class PrinterAdapter(BaseAdapter):
    name = "printer"
    safe_retry = True
    def __init__(self, printer_name: str = ""):
        self.printer_name = printer_name
    def execute(self, operation, payload):
        text = payload.get("text", "")
        lp = shutil.which("lp")
        if lp and self.printer_name:
            try:
                r = subprocess.run([lp, "-d", self.printer_name], input=text.encode(), timeout=10, capture_output=True)
                if r.returncode == 0:
                    return AdapterResult(ok=True, ref=self.printer_name, confirmed_by_device=True)
                return AdapterResult(ok=False, error=r.stderr.decode(errors="ignore"))
            except Exception as e:
                return AdapterResult(ok=False, error=str(e))
        return AdapterResult(ok=False, error="Принтер не настроен (printer_name пуст)")
