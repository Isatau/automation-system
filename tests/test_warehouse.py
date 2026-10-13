"""Склад: приход, списания при недостатке, запрет минуса / разрешение настройкой, инвентаризация,
списание ингредиентов по техкарте при продаже, append-only журнал движений."""
import uuid
from decimal import Decimal

import pytest

from tests.helpers import open_shift, create_order_with_item, close_all_shifts


@pytest.fixture(autouse=True)
def _clean_shifts(client):
    """Перед каждым тестом класса продаж закрываем открытые смены (тестовая инфраструктура),
    чтобы каждый сценарий открывал свою кассовую смену без конфликта 409."""
    close_all_shifts(client, None)
    yield
    close_all_shifts(client, None)


def _wh(client, who):
    ws = client.get("/api/v1/warehouse/warehouses", headers=who["h"]).json()
    return ws[0]["id"]


def _stock(client, who, product_id=None, warehouse_id=None):
    params = {}
    if product_id: params["product_id"] = product_id
    if warehouse_id: params["warehouse_id"] = warehouse_id
    rows = client.get("/api/v1/warehouse/stock", params=params, headers=who["h"]).json()
    return {r["name"]: Decimal(str(r["qty"])) for r in rows}


def _products(client, who):
    return {p["name"]: p for p in client.get("/api/v1/warehouse/products", headers=who["h"]).json()}


class TestWarehouseBasics:
    def test_receipt_post_adds_stock_and_lots(self, client, storekeeper):
        wh = _wh(client, storekeeper)
        prods = _products(client, storekeeper)
        beef = prods["Говядина"]
        sup = client.get("/api/v1/suppliers", headers=storekeeper["h"]).json()[0]
        before = _stock(client, storekeeper)[ "Говядина"]
        r = client.post("/api/v1/warehouse/receipts", json={
            "supplier_id": sup["id"], "warehouse_id": wh,
            "lines": [{"product_id": beef["id"], "qty": 10, "price": 5000}]}, headers=storekeeper["h"])
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        # черновик не меняет остаток
        assert _stock(client, storekeeper)["Говядина"] == before
        r = client.post(f"/api/v1/warehouse/receipts/{rid}/post", headers=storekeeper["h"])
        assert r.status_code == 200, r.text
        assert _stock(client, storekeeper)["Говядина"] == before + Decimal("10") * Decimal(str(beef["coef"]))
        # повторное проведение — отказ (защита от двойного выполнения)
        r = client.post(f"/api/v1/warehouse/receipts/{rid}/post", headers=storekeeper["h"])
        assert r.status_code == 409

    def test_writeoff_insufficient_rejected(self, client, storekeeper):
        wh = _wh(client, storekeeper)
        prods = _products(client, storekeeper)
        potato = prods["Картофель"]
        st = _stock(client, storekeeper)["Картофель"]
        r = client.post("/api/v1/warehouse/writeoffs", json={
            "warehouse_id": wh, "reason": "spoilage",
            "lines": [{"product_id": potato["id"], "qty": float(st) + 1000}]}, headers=storekeeper["h"])
        assert r.status_code == 409
        assert "Недостаточный остаток" in r.json()["detail"]
        # после отказа остаток не изменился (транзакция откачена целиком)
        assert _stock(client, storekeeper)["Картофель"] == st

    def test_writeoff_ok_and_movements_journal(self, client, storekeeper):
        wh = _wh(client, storekeeper)
        prods = _products(client, storekeeper)
        water = prods["Вода 0.5"]
        st = _stock(client, storekeeper)["Вода 0.5"]
        r = client.post("/api/v1/warehouse/writeoffs", json={
            "warehouse_id": wh, "reason": "other", "reason_text": "розлив для staff",
            "lines": [{"product_id": water["id"], "qty": 3}]}, headers=storekeeper["h"])
        assert r.status_code == 200, r.text
        assert _stock(client, storekeeper)["Вода 0.5"] == st - 3
        mv = client.get("/api/v1/warehouse/stock/movements", params={"doc_type": "writeoff"},
                        headers=storekeeper["h"]).json()
        assert any(m["doc_id"] == r.json()["id"] for m in mv), "движение зафиксировано в журнале"
        row = next(m for m in mv if m["doc_id"] == r.json()["id"])
        assert Decimal(str(row["qty_signed"])) == Decimal("-3")

    def test_inventory_diff_posts_movements(self, client, storekeeper):
        wh = _wh(client, storekeeper)
        prods = _products(client, storekeeper)
        potato = prods["Картофель"]
        st = _stock(client, storekeeper)["Картофель"]
        actual = st - 2
        r = client.post("/api/v1/warehouse/inventories", json={
            "warehouse_id": wh, "lines": [{"product_id": potato["id"], "actual_qty": float(actual)}]},
            headers=storekeeper["h"])
        assert r.status_code == 200, r.text
        iid = r.json()["id"]
        r = client.post(f"/api/v1/warehouse/inventories/{iid}/post", headers=storekeeper["h"])
        assert r.status_code == 200, r.text
        assert _stock(client, storekeeper)["Картофель"] == actual
        # повторная проводка инвентаризации запрещена
        r = client.post(f"/api/v1/warehouse/inventories/{iid}/post", headers=storekeeper["h"])
        assert r.status_code == 409


class TestSaleStockPosting:
    def test_sale_consumes_recipe_ingredients(self, client, cashier, storekeeper):
        """Оплата блюда со техкартой списывает ингредиенты; себестоимость попадает в заказ."""
        open_shift(client, cashier)
        st_before = _stock(client, storekeeper)["Говядина"]
        o, mi = create_order_with_item(client, cashier, "Стейк", 2)
        total = Decimal(str(o["total"]))
        r = client.post(f"/api/v1/orders/{o['id']}/payments", json={"method": "cash", "amount": float(total)},
                        headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 200, r.text
        st_after = _stock(client, storekeeper)["Говядина"]
        assert st_before - st_after == Decimal("0.7"), "норма 0.35 кг × 2 порции"
        got = client.get(f"/api/v1/orders/{o['id']}", headers=cashier["h"]).json()
        assert Decimal(str(got["cost_amount"])) > 0

    def test_sale_blocked_when_stock_insufficient(self, client, cashier, storekeeper, techadmin):
        """По умолчанию минус запрещён: продажа без остатка блокируется и платёж откатывается."""
        open_shift(client, cashier)
        prods = _products(client, storekeeper)
        wh = _wh(client, storekeeper)
        potato = prods["Картофель"]
        # обнуляем картофель легальным списанием
        st = _stock(client, storekeeper)["Картофель"]
        r = client.post("/api/v1/warehouse/writeoffs", json={
            "warehouse_id": wh, "reason": "other", "reason_text": "тест: обнуление",
            "lines": [{"product_id": potato["id"], "qty": float(st)}]}, headers=storekeeper["h"])
        assert r.status_code == 200, r.text
        o, mi = create_order_with_item(client, cashier, "Картофель фри", 1)
        total = Decimal(str(o["total"]))
        r = client.post(f"/api/v1/orders/{o['id']}/payments", json={"method": "cash", "amount": float(total)},
                        headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 409, f"ожидался отказ из-за нехватки: {r.status_code} {r.text}"
        # платёж НЕ создан (транзакция откатана)
        pays = client.get(f"/api/v1/orders/{o['id']}/payments", headers=cashier["h"]).json()
        assert pays == []
        assert _stock(client, storekeeper)["Картофель"] == 0

    def test_negative_allowed_by_setting(self, client, cashier, storekeeper, owner):
        """Явная настройка negative_stock_policy.allow_on_sales разрешает минус с фиксацией причины."""
        r = client.put("/api/v1/admin/settings", json={"key": "negative_stock_policy",
                        "value": {"allow_on_sales": True}}, headers=owner["h"])
        assert r.status_code == 200, r.text
        try:
            open_shift(client, cashier)
            prods = _products(client, storekeeper)
            wh = _wh(client, storekeeper)
            potato = prods["Картофель"]
            st = _stock(client, storekeeper)["Картофель"]
            if st > 0:
                client.post("/api/v1/warehouse/writeoffs", json={
                    "warehouse_id": wh, "reason": "other", "reason_text": "тест: обнуление",
                    "lines": [{"product_id": potato["id"], "qty": float(st)}]}, headers=storekeeper["h"])
            o, mi = create_order_with_item(client, cashier, "Картофель фри", 1)
            total = Decimal(str(o["total"]))
            r = client.post(f"/api/v1/orders/{o['id']}/payments", json={"method": "cash", "amount": float(total)},
                            headers={**cashier["h"], "Idempotency-Key": str(uuid.uuid4())})
            assert r.status_code == 200, r.text
            assert _stock(client, storekeeper)["Картофель"] < 0
            neg = client.get("/api/v1/warehouse/stock/movements", params={"doc_type": "sale"},
                             headers=storekeeper["h"]).json()
            assert any(Decimal(str(m["qty_signed"])) < 0 and m.get("negative_allowed") for m in neg)
        finally:
            client.put("/api/v1/admin/settings", json={"key": "negative_stock_policy",
                        "value": {"allow_on_sales": False}}, headers=owner["h"])
