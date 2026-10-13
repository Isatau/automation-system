"""Начальные данные. Пароль demo-пользователей задаётся ENV RESTOCORE_ADMIN_PASSWORD (по умолчанию admin123 — сменить!)."""
import os
from .db import SessionLocal, now_almaty
from .security import hash_password
from .models import (Venue, CashRegister, Hall, Table, User, PaymentMethod, Station, MenuCategory, MenuItem,
                    Product, Warehouse, StockBalance, Recipe, RecipeLine, Supplier, NotificationRule, Setting, Staff)

def seed_all(force=False):
    db = SessionLocal()
    try:
        if db.query(Venue).count() and not force:
            return
        pwd = os.environ.get("RESTOCORE_ADMIN_PASSWORD", "admin123")
        v = Venue(name="Ресторан «Демотест»", address="Алматы, пр. Абая 1", currency="KZT")
        db.add(v); db.flush()
        reg = CashRegister(venue_id=v.id, name="Касса 1", number=1); db.add(reg)
        h = Hall(venue_id=v.id, name="Основной зал"); db.add(h)
        hb = Hall(venue_id=v.id, name="Барная зона", kind="bar_room"); db.add(hb)
        hk = Hall(venue_id=v.id, name="Караоке-комнаты", kind="karaoke"); db.add(hk)
        db.flush()
        tables = []
        for i in range(1, 13):
            t = Table(hall_id=h.id, name=f"Стол {i}", seats=4, x=(i % 4) * 120 + 20, y=(i // 4) * 100 + 20)
            db.add(t); tables.append(t)
        for i in range(1, 5):
            db.add(Table(hall_id=hb.id, name=f"Бар {i}", seats=2, x=i * 80, y=20))
        for i in range(1, 4):
            db.add(Table(hall_id=hk.id, name=f"Комната {i}", seats=8, hourly=True, tariff=5000, x=i * 120, y=20))
        users = [
            ("owner", "Владелец", "owner"), ("manager", "Управляющий", "manager"),
            ("cashier", "Кассир", "cashier"), ("waiter", "Официант", "waiter"),
            ("bartender", "Бармен", "bartender"), ("cook", "Повар", "cook"),
            ("storekeeper", "Кладовщик", "storekeeper"), ("accountant", "Бухгалтер", "accountant"),
            ("techadmin", "Тех. администратор", "tech_admin"), ("shiftadmin", "Админ смены", "shift_admin"),
        ]
        created_users = {}
        for login, name, role in users:
            u = User(login=login, name=name, role=role, venue_id=v.id,
                     password_hash=hash_password(pwd), discount_limit_pct=10 if role in ("cashier","shift_admin") else 0)
            db.add(u); db.flush(); created_users[role] = u
            db.add(Staff(user_id=u.id, venue_id=v.id, name=name, position=role, pay_type="hourly", rate=1500))
        for code, name, is_cash in [("cash","Наличные",True),("card","Банковская карта",False),
                                    ("qr","QR (Kaspi/Halyk)",False),("deposit","Депозит гостя",False),
                                    ("certificate","Подарочный сертификат",False),("bonus","Бонусы лояльности",False),
                                    ("mixed","Смешанная",False),("other","Прочее",False)]:
            db.add(PaymentMethod(code=code, name=name, is_cash=is_cash))
        st_k = Station(venue_id=v.id, name="Горячий цех", kind="kitchen", cook_time_norm_min=20); db.add(st_k)
        st_b = Station(venue_id=v.id, name="Бар", kind="bar", cook_time_norm_min=5); db.add(st_b)
        db.flush()
        c_hot = MenuCategory(venue_id=v.id, name="Горячие блюда"); db.add(c_hot)
        c_dr = MenuCategory(venue_id=v.id, name="Напитки"); db.add(c_dr)
        db.flush()
        wh = Warehouse(venue_id=v.id, name="Основной склад"); db.add(wh); db.flush()
        p_beef = Product(venue_id=v.id, warehouse_id=wh.id, article="МЯСО-ГВ", name="Говядина", base_uom="kg",
                         purchase_uom="kg", coef=1, min_stock=5, last_purchase_price=3200)
        p_pot  = Product(venue_id=v.id, warehouse_id=wh.id, article="ОВОЩ-КТ", name="Картофель", base_uom="kg",
                         purchase_uom="kg", coef=1, min_stock=20, last_purchase_price=350)
        p_wat  = Product(venue_id=v.id, warehouse_id=wh.id, article="НАПИТ-ВОДА", name="Вода 0.5", base_uom="pcs",
                         purchase_uom="pcs", coef=1, min_stock=48, last_purchase_price=180)
        for p in (p_beef, p_pot, p_wat):
            db.add(p); db.flush()
            db.add(StockBalance(warehouse_id=wh.id, product_id=p.id, quantity=100))
        db.flush()
        i_steak = MenuItem(venue_id=v.id, category_id=c_hot.id, name="Стейк рибай", article="HB-001",
                           price=8900, station_id=st_k.id, unit="portion", description="Говяжий стейк 300г")
        i_fries = MenuItem(venue_id=v.id, category_id=c_hot.id, name="Картофель фри", article="HB-002",
                           price=1500, station_id=st_k.id, unit="portion")
        i_water = MenuItem(venue_id=v.id, category_id=c_dr.id, name="Вода 0.5", article="NP-001",
                           price=400, station_id=st_b.id, unit="pcs")
        for it in (i_steak, i_fries, i_water):
            db.add(it); db.flush()
        r1 = Recipe(item_id=i_steak.id, output_qty=1, output_unit="portion"); db.add(r1); db.flush()
        db.add(RecipeLine(recipe_id=r1.id, product_id=p_beef.id, quantity=0.35))
        r2 = Recipe(item_id=i_fries.id, output_qty=1, output_unit="portion"); db.add(r2); db.flush()
        db.add(RecipeLine(recipe_id=r2.id, product_id=p_pot.id, quantity=0.25))
        db.add(Supplier(venue_id=v.id, name="ТОО «Мясной двор»", contacts="+7 701 000 11 22", requisites="БИН 000000000000"))
        db.add(Supplier(venue_id=v.id, name="Osu Retail", contacts="+7 727 355 55 55", requisites="БИН 111111111111"))
        # правила уведомлений
        for et, th, role in [("order.cancel", None, "manager"), ("refund", 5000, "manager"),
                              ("discount.over_limit", None, "manager"), ("writeoff.big", 50000, "manager"),
                              ("shift.cash_diff", 1000, "manager"), ("stock.low", None, "storekeeper"),
                              ("backup.failed", None, "tech_admin"), ("device.error", None, "tech_admin")]:
            db.add(NotificationRule(event_type=et, threshold_amount=th, recipients_role=role, channels="internal"))
        db.add(Setting(key="loyalty_accrual_pct", value={"value": 5}))
        db.commit()
    finally:
        db.close()
