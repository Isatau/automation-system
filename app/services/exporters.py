"""Экспорт отчётов: XLSX (openpyxl), CSV (stdlib), PDF (reportlab)."""
import csv, io
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def to_xlsx(columns: list[str], rows: list[list], title: str = "") -> bytes:
    wb = Workbook(); ws = wb.active; ws.title = (title or "Отчёт")[:30]
    ws.append(columns)
    for r in rows: ws.append([("" if v is None else v) for v in r])
    b = io.BytesIO(); wb.save(b); return b.getvalue()

def to_csv(columns: list[str], rows: list[list]) -> bytes:
    s = io.StringIO(); w = csv.writer(s, delimiter=";", lineterminator="\r\n")
    w.writerow(columns)
    for r in rows: w.writerow(r)
    return s.getvalue().encode("utf-8-sig")

# кириллица в PDF: DejaVuSans если доступен, иначе Helvetica (транслитерация не нужна — теряются только спецсимволы)
_FONT = "Helvetica"
for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
          "/System/Library/Fonts/Supplemental/DejaVuSans.ttf"):
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", p)); _FONT = "DejaVu"; break
    except Exception:
        pass

def to_pdf(columns: list[str], rows: list[list], title: str = "Отчёт") -> bytes:
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=landscape(A4))
    y = 800
    c.setFont(_FONT, 13); c.drawString(30, y, title); y -= 24
    c.setFont(_FONT, 8)
    colw = [(780 // max(len(columns), 1))] * len(columns)
    def header():
        nonlocal y
        c.setFont(_FONT, 8); yy = y
        for i, col in enumerate(columns):
            c.drawString(30 + sum(colw[:i]), yy, str(col)[:18])
        y -= 14
    header()
    for r in rows:
        if y < 40:
            c.showPage(); y = 800; header()
        c.setFont(_FONT, 8)
        for i, v in enumerate(r):
            c.drawString(30 + sum(colw[:i]), y, str(v if v is not None else "")[:18])
        y -= 12
    c.save()
    return buf.getvalue()
