"""Кассовые смены: открытие, формула ожидаемого остатка, закрытие с расхождением, защита закрытой смены."""
import uuid
from decimal import Decimal

from tests.helpers import open_shift, current_shift, create_order_with_item


def _pay_cash(client, cashier, item_part, qty, amount=None):
    o, mi = create_order_with_item(client, cashier, item_part, qty)
    total = Decimal(str(o["total"]))
    amt = float(amount if amount is not None else total)
    r = client.post(f"/api/v1/orders/{o['id']}/payments", json={"method": "cash", "amount": amt},
                    headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    return o, amt


class TestShiftCycle:
    def test_open_current_and_double_open_blocked(self, client, cashier):
        sh = open_shift(client, cashier, initial=5000)
        cur = current_shift(client, cashier)
        assert cur["status"] == "open" and Decimal(str(cur["initial_cash"])) == Decimal("5000")
        # повторное открытие тем же кассиром / на той же кассе — отказ
        regs = [sh["register_id"]]
        r = client.post("/api/v1/shifts/open", json={"register_id": regs[0], "initial_cash": 1}, headers=cashier["h"])
        assert r.status_code == 409

    def test_expected_cash_formula(self, client, cashier):
        """expected = initial + наличные продажи − возвраты + внесения − изъятия."""
        sh = open_shift(client, cashier, initial=10000)
        sid = sh["id"]
        _, amt1 = _pay_cash(client, cashier, "Вода", 2)          # + наличные
        o3, _ = create_order_with_item(client, cashier, "Вода", 1)
        total3 = Decimal(str(o3["total"]))
        r = client.post(f"/api/v1/orders/{o3['id']}/payments", json={"method": "card", "amount": float(total3)},
                        headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 200                                # карта — не влияет на ящик
        # внесение и изъятие
        client.post(f"/api/v1/shifts/{sid}/cash-in", json={"amount": 2000, "reason": "инкассация выдача размена"},
                    headers=cashier["h"])
        client.post(f"/api/v1/shifts/{sid}/cash-out", json={"amount": 500, "reason": "хознужды"},
                    headers=cashier["h"])
        summ = client.get(f"/api/v1/shifts/{sid}/summary", headers=cashier["h"]).json()
        exp = Decimal(str(summ["expected_cash"]))
        assert exp == Decimal("10000") + Decimal(str(amt1)) + Decimal("2000") - Decimal("500"), summ

    def test_close_with_shortage_and_overage_recorded(self, client, cashier, manager):
        sh = open_shift(client, cashier, initial=1000)
        sid = sh["id"]
        _, amt = _pay_cash(client, cashier, "Вода", 1)
        expected = Decimal("1000") + Decimal(str(amt))
        # закрываем с НАМЕРЕННОЙ недостачей 300
        fact = float(expected - 300)
        r = client.post(f"/api/v1/shifts/{sid}/close", json={"actual_cash": fact}, headers=cashier["h"])
        assert r.status_code == 200, r.text
        s = r.json()["shift"]
        assert Decimal(str(s["difference"])) == Decimal("-300")
        assert s["status"] == "closed"
        # незакрытые заказы других тестов могут мешать — но наш заказ уже paid; проверка идемпотентности закрытия
        r2 = client.post(f"/api/v1/shifts/{sid}/close", json={"actual_cash": fact}, headers=cashier["h"])
        assert r2.status_code == 409, "повторное закрытие запрещено"

    def test_closed_shift_immutable_without_reopen_right(self, client, cashier, waiter):
        sh = open_shift(client, cashier, initial=0)
        sid = sh["id"]
        client.post(f"/api/v1/shifts/{sid}/close", json={"actual_cash": 0}, headers=cashier["h"])
        # попытка внести деньги в закрытую смену — отказ
        r = client.post(f"/api/v1/shifts/{sid}/cash-in", json={"amount": 100, "reason": "проверка"},
                        headers=cashier["h"])
        assert r.status_code in (403, 409), r.text
        # переоткрытие без права shifts:reopen — 403 (официант)
        r = client.post(f"/api/v1/shifts/{sid}/reopen", json={"reason": "тест"}, headers=waiter["h"])
        assert r.status_code == 403

    def test_reopen_with_reason_by_manager(self, client, cashier, manager):
        sh = open_shift(client, cashier, initial=0)
        sid = sh["id"]
        client.post(f"/api/v1/shifts/{sid}/close", json={"actual_cash": 0}, headers=cashier["h"])
        # менеджер может не иметь reopen — тогда проверяем владельца
        closer = manager if client.post("/api/v1/shifts/current", headers=manager["h"]).status_code else cashier
        r = client.post(f"/api/v1/shifts/{sid}/reopen", json={"reason": ""}, headers=closer["h"])
        if r.status_code == 403:
            from tests.conftest import login
            owner = login(client, "owner")
            closer = owner
            r = client.post(f"/api/v1/shifts/{sid}/reopen", json={"reason": ""}, headers=closer["h"])
        assert r.status_code == 400  # пустая причина запрещена
        r = client.post(f"/api/v1/shifts/{sid}/reopen", json={"reason": "уточнение пересчёта"}, headers=closer["h"])
        assert r.status_code == 200, r.text
        cur = client.get(f"/api/v1/shifts/{sid}", headers=closer["h"]) if False else None
        # после reopen смена снова открыта
        lst = client.get("/api/v1/shifts", headers=closer["h"]).json()
        row = next(x for x in (lst if isinstance(lst, list) else lst.get("items", [])) if x["id"] == sid)
        assert row["status"] == "open" and row["reopen_count"] >= 1

    def test_unclosed_orders_block_shift_close(self, client, cashier):
        sh = open_shift(client, cashier, initial=0)
        create_order_with_item(client, cashier, "Стейк", 1)  # открытый неоплаченный заказ
        r = client.post(f"/api/v1/shifts/{sh['id']}/close", json={"actual_cash": 0}, headers=cashier["h"])
        assert r.status_code == 409
        assert "незакрыт" in r.json()["detail"].lower() or "заказ" in r.json()["detail"]
