"""Критические сценарии: заказы, позиции, скидки, идемпотентность и конкурентность платежей."""
import uuid
from decimal import Decimal

from tests.helpers import open_shift, create_order_with_item


def _new_order(client, who, item_part="Стейк", qty=1):
    return create_order_with_item(client, who, item_part, qty)


class TestOrdersFlow:
    def test_create_add_change_delete(self, client, cashier):
        o, mi = _new_order(client, cashier)
        assert o["status"] == "open" and len(o["items"]) == 1
        v = o["version"]
        # изменение количества с корректной версией
        r = client.patch(f"/api/v1/orders/items/{o['items'][0]['id']}",
                         json={"qty": 2, "version": v}, headers=cashier["h"])
        assert r.status_code == 200, r.text
        o2 = r.json()
        assert Decimal(str(o2["total"])) == Decimal(str(mi["price"])) * 2
        # удаление — с обязательной причиной
        r = client.delete(f"/api/v1/orders/items/{o2['items'][0]['id']}", params={"reason": ""}, headers=cashier["h"])
        assert r.status_code == 400
        r = client.delete(f"/api/v1/orders/items/{o2['items'][0]['id']}", params={"reason": "гость передумал"},
                          headers=cashier["h"])
        assert r.status_code == 200 and r.json()["items"] == []

    def test_stale_version_rejected(self, client, cashier):
        """Конкурентное изменение заказа устаревшей версией отклоняется (409)."""
        o, mi = _new_order(client, cashier)
        stale = o["version"] - 1
        r = client.post(f"/api/v1/orders/{o['id']}/items",
                        json={"item_id": mi["id"], "qty": 1, "version": stale}, headers=cashier["h"])
        assert r.status_code == 409
        assert "изменён другим сотрудником" in r.json()["detail"]
        # правильная версия проходит
        r = client.post(f"/api/v1/orders/{o['id']}/items",
                        json={"item_id": mi["id"], "qty": 1, "version": o["version"]}, headers=cashier["h"])
        assert r.status_code == 200

    def test_send_to_kitchen_creates_tickets(self, client, cashier):
        o, mi = _new_order(client, cashier)
        r = client.post(f"/api/v1/orders/{o['id']}/send", json={"all": True}, headers=cashier["h"])
        assert r.status_code == 200 and r.json()["tickets"] >= 1
        tk = client.get("/api/v1/kitchen/tickets", headers=cashier["h"]).json()
        assert any(t["order_id"] == o["id"] for t in tk)
        # позиция отправлена — повторная отправка тех же позиций не создаёт дублей
        r = client.post(f"/api/v1/orders/{o['id']}/send", json={"all": True}, headers=cashier["h"])
        assert r.status_code == 400  # нет новых позиций

    def test_cancel_requires_reason_and_history(self, client, cashier):
        o, mi = _new_order(client, cashier)
        r = client.post(f"/api/v1/orders/{o['id']}/cancel", json={"reason": "   "}, headers=cashier["h"])
        assert r.status_code == 400
        r = client.post(f"/api/v1/orders/{o['id']}/cancel", json={"reason": "гость ушёл"}, headers=cashier["h"])
        assert r.status_code == 200
        got = client.get(f"/api/v1/orders/{o['id']}", headers=cashier["h"]).json()
        assert got["status"] == "cancelled" and got["cancel_reason"] == "гость ушёл"
        # оплата отменённого заказа невозможна
        r = client.post(f"/api/v1/orders/{o['id']}/payments",
                        json={"method": "cash", "amount": 100}, headers=cashier["h"])
        assert r.status_code == 409


class TestPaymentsCritical:
    def test_double_payment_same_key_is_idempotent(self, client, cashier):
        """Повтор запроса с тем же Idempotency-Key НЕ создаёт второй платёж."""
        open_shift(client, cashier)
        o, mi = _new_order(client, cashier, "Вода", 2)
        total = Decimal(str(o["total"]))
        key = str(uuid.uuid4())
        body = {"method": "cash", "amount": float(total)}
        r1 = client.post(f"/api/v1/orders/{o['id']}/payments", json=body,
                         headers={**cashier["h"], "Idempotency-Key": key})
        assert r1.status_code == 200, r1.text
        r2 = client.post(f"/api/v1/orders/{o['id']}/payments", json=body,
                         headers={**cashier["h"], "Idempotency-Key": key})
        assert r2.status_code == 200, r2.text
        pays = client.get(f"/api/v1/orders/{o['id']}/payments", headers=cashier["h"]).json()
        assert len(pays) == 1, f"идемпотентность нарушена: {len(pays)} платежей"
        assert Decimal(str(r2.json()["paid_amount"])) == total

    def test_overpayment_blocked_second_full_payment(self, client, cashier):
        """Второй полный платёж (другой ключ) блокируется: сумма больше остатка / заказ paid."""
        open_shift(client, cashier)
        o, mi = _new_order(client, cashier, "Вода", 1)
        total = float(Decimal(str(o["total"])))
        r = client.post(f"/api/v1/orders/{o['id']}/payments", json={"method": "cash", "amount": total},
                        headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 200 and r.json()["order_status"] == "paid"
        r2 = client.post(f"/api/v1/orders/{o['id']}/payments", json={"method": "cash", "amount": total},
                         headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
        assert r2.status_code == 409  # assert_payable: уже оплачен

    def test_partial_then_rest(self, client, cashier):
        open_shift(client, cashier)
        o, mi = _new_order(client, cashier, "Стейк", 2)
        total = Decimal(str(o["total"]))
        half = float((total / 2).quantize(Decimal("0.01")))
        r = client.post(f"/api/v1/orders/{o['id']}/payments", json={"method": "cash", "amount": half},
                        headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 200 and r.json()["order_status"] != "paid"
        rem = Decimal(str(r.json()["remaining"]))
        r = client.post(f"/api/v1/orders/{o['id']}/payments", json={"method": "card", "amount": float(rem)},
                        headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
        assert r.json()["order_status"] == "paid"
        # закрытие только полностью оплаченного
        r = client.post(f"/api/v1/orders/{o['id']}/close", headers=cashier["h"])
        assert r.status_code == 200 and r.json()["status"] == "closed"
        # закрытый заказ повторно оплатить нельзя
        r = client.post(f"/api/v1/orders/{o['id']}/payments", json={"method": "cash", "amount": 1},
                        headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 409

    def test_concurrent_payments_no_double_charge(self, client, cashier):
        """Два параллельных платежа на весь итог: суммарно списано не больше total."""
        from concurrent.futures import ThreadPoolExecutor
        open_shift(client, cashier)
        o, mi = _new_order(client, cashier, "Стейк", 1)
        total = float(Decimal(str(o["total"])))
        # общий клиент не потокобезопасен по session-state FastAPI TestClient — используем отдельные прогоны
        def pay(i):
            return client.post(f"/api/v1/orders/{o['id']}/payments",
                               json={"method": "cash", "amount": total},
                               headers={**cashier["h"], "Idempotency-Key": f"conc-{uuid.uuid4()}-{i}"})
        with ThreadPoolExecutor(max_workers=2) as ex:
            results = [f.result() for f in [ex.submit(pay, 0), ex.submit(pay, 1)]]
        codes = sorted(r.status_code for r in results)
        assert codes[0] == 200 and codes[1] == 409, f"ожидался один успешный + один отказ: {codes}"
        pays = client.get(f"/api/v1/orders/{o['id']}/payments", headers=cashier["h"]).json()
        assert sum(Decimal(str(p["amount"])) for p in pays) == Decimal(str(total))

    def test_refund_returns_order_to_awaiting(self, client, cashier):
        open_shift(client, cashier)
        o, mi = _new_order(client, cashier, "Вода", 1)
        total = float(Decimal(str(o["total"])))
        r = client.post(f"/api/v1/orders/{o['id']}/payments", json={"method": "cash", "amount": total},
                        headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
        pid = r.json()["payment"]["id"]
        r = client.post(f"/api/v1/payments/{pid}/refund", json={"amount": total, "reason": "возврат гостю"},
                        headers=cashier["h"])
        assert r.status_code == 200, r.text
        got = client.get(f"/api/v1/orders/{o['id']}", headers=cashier["h"]).json()
        assert Decimal(str(got["paid_amount"])) < Decimal(str(got["total"])) or got["status"] == "awaiting_payment"
