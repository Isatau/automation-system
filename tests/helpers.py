"""Хелперы для сквозных сценариев."""
from decimal import Decimal


def open_shift(client, who, initial=10000):
    regs = client.get("/api/v1/shifts", headers=who["h"])  # не все роли; берём кассу из сида напрямую
    import app.db as dbmod
    from app.models import CashRegister
    s = dbmod.SessionLocal()
    try:
        reg = s.query(CashRegister).first()
        rid = reg.id
    finally:
        s.close()
    r = client.post("/api/v1/shifts/open", json={"register_id": rid, "initial_cash": initial}, headers=who["h"])
    assert r.status_code == 200, r.text
    return r.json()["shift"] if "shift" in r.json() else r.json()


def current_shift(client, who):
    r = client.get("/api/v1/shifts/current", headers=who["h"])
    assert r.status_code == 200, r.text
    return r.json()


def create_order_with_item(client, who, item_name_part="Стейк", qty=1):
    items = client.get("/api/v1/menu/items", headers=who["h"]).json()
    mi = next(i for i in items if item_name_part.lower() in i["name"].lower())
    o = client.post("/api/v1/orders", json={}, headers=who["h"])
    assert o.status_code == 200, o.text
    oid = o.json()["id"]
    r = client.post(f"/api/v1/orders/{oid}/items", json={"item_id": mi["id"], "qty": qty}, headers=who["h"])
    assert r.status_code == 200, r.text
    return r.json(), mi


def close_all_shifts(client, manager_h):
    """Закрыть открытые смены между тестами (тестовая инфраструктура).

    Суммы не проверяются: это не бизнес-операция, а подготовка чистого состояния.
    Реальные сценарии закрытия с расхождением тестируются через API в test_shifts.py.
    """
    import app.db as dbmod
    from app.models import Shift, Order
    s = dbmod.SessionLocal()
    try:
        for sh in s.query(Shift).filter_by(status="open").all():
            # незакрытые заказы этой смены отменяем, чтобы не мешать следующим тестам
            for o in s.query(Order).filter(Order.shift_id == sh.id,
                                           Order.status.notin_(["closed", "cancelled", "paid"])).all():
                o.status = "cancelled"
            sh.status = "closed"
        s.commit()
    finally:
        s.close()
