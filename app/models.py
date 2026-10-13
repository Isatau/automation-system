"""Модели базы данных RestoCore (SQLAlchemy 2.0). Денежные поля — Numeric, без float."""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy import (String, Text, Integer, Boolean, ForeignKey, Index, Numeric, DateTime, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base, TimestampMixin, Money, Qty, JSONText, new_id

# ---------------- Организация ----------------

class Venue(Base, TimestampMixin):
    __tablename__ = "venues"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[Optional[str]] = mapped_column(String(300))
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Almaty")
    currency: Mapped[str] = mapped_column(String(8), default="KZT")
    hourly_billing: Mapped[bool] = mapped_column(Boolean, default=False)
    settings_json = mapped_column(JSONText, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class CashRegister(Base, TimestampMixin):
    __tablename__ = "cash_registers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(100))
    number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")


class Hall(Base, TimestampMixin):
    __tablename__ = "halls"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), default="hall")  # hall|bar_room|karaoke
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Table(Base, TimestampMixin):
    __tablename__ = "tables"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    hall_id: Mapped[str] = mapped_column(ForeignKey("halls.id"))
    name: Mapped[str] = mapped_column(String(50))
    seats: Mapped[int] = mapped_column(Integer, default=4)
    status: Mapped[str] = mapped_column(String(20), default="free")  # free|reserved|occupied|servicing|payment|cleaning
    waiter_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    merged_group_id: Mapped[Optional[str]] = mapped_column(String(32))
    x: Mapped[int] = mapped_column(Integer, default=0)
    y: Mapped[int] = mapped_column(Integer, default=0)
    hourly: Mapped[bool] = mapped_column(Boolean, default=False)
    tariff: Mapped[Optional[float]] = mapped_column(Money)  # ₸/час для бара/караоке


class Reservation(Base, TimestampMixin):
    __tablename__ = "reservations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    table_id: Mapped[str] = mapped_column(ForeignKey("tables.id"))
    guest_id: Mapped[Optional[str]] = mapped_column(ForeignKey("guests.id"))
    date_start: Mapped[datetime] = mapped_column(DateTime)
    date_end: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # confirmed|seated|cancelled|no_show
    deposit_amount: Mapped[Optional[float]] = mapped_column(Money)
    comment: Mapped[Optional[str]] = mapped_column(Text)

# ---------------- Доступ ----------------

ROLES = ["owner", "manager", "shift_admin", "cashier", "waiter", "bartender", "cook", "storekeeper", "accountant", "tech_admin"]


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    login: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(150))
    role: Mapped[str] = mapped_column(String(20), default="waiter")
    venue_id: Mapped[Optional[str]] = mapped_column(ForeignKey("venues.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    discount_limit_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class UserPermission(Base, TimestampMixin):
    __tablename__ = "user_permissions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    permission: Mapped[str] = mapped_column(String(60))
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("user_id", "permission"),)


class SessionRec(Base, TimestampMixin):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    jti: Mapped[str] = mapped_column(String(40), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    ip: Mapped[Optional[str]] = mapped_column(String(60))
    workstation: Mapped[Optional[str]] = mapped_column(String(80))


class LoginAttempt(Base, TimestampMixin):
    __tablename__ = "login_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    login: Mapped[str] = mapped_column(String(60), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    ip: Mapped[Optional[str]] = mapped_column(String(60))


class Staff(Base, TimestampMixin):
    __tablename__ = "staff"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    venue_id: Mapped[Optional[str]] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(150))
    position: Mapped[str] = mapped_column(String(80))
    hire_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    pay_type: Mapped[str] = mapped_column(String(20), default="hourly")  # hourly|shift|percent
    rate: Mapped[float] = mapped_column(Money, default=0)      # ₸/час или ₸/смену
    sales_percent: Mapped[float] = mapped_column(Numeric(5,2), default=0)
    phone: Mapped[Optional[str]] = mapped_column(String(30))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkSchedule(Base, TimestampMixin):
    __tablename__ = "work_schedules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff.id"))
    date: Mapped[datetime] = mapped_column(DateTime)
    start_time: Mapped[Optional[str]] = mapped_column(String(5))
    end_time: Mapped[Optional[str]] = mapped_column(String(5))


class TimeEntry(Base, TimestampMixin):
    __tablename__ = "time_entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff.id"))
    clock_in: Mapped[datetime] = mapped_column(DateTime)
    clock_out: Mapped[Optional[datetime]] = mapped_column(DateTime)
    planned_start: Mapped[Optional[datetime]] = mapped_column(DateTime)
    late_minutes: Mapped[int] = mapped_column(Integer, default=0)
    hours_worked: Mapped[float] = mapped_column(Numeric(7,2), default=0)


class PayrollAccrual(Base, TimestampMixin):
    __tablename__ = "payroll_accruals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff.id"))
    period_from: Mapped[datetime] = mapped_column(DateTime)
    period_to: Mapped[datetime] = mapped_column(DateTime)
    base_amount: Mapped[float] = mapped_column(Money, default=0)
    percent_amount: Mapped[float] = mapped_column(Money, default=0)
    bonus_amount: Mapped[float] = mapped_column(Money, default=0)
    advance_amount: Mapped[float] = mapped_column(Money, default=0)
    total: Mapped[float] = mapped_column(Money, default=0)
    formula_snapshot = mapped_column(JSONText, default=dict)
    corrected_of_id: Mapped[Optional[str]] = mapped_column(ForeignKey("payroll_accruals.id"))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    paid: Mapped[bool] = mapped_column(Boolean, default=False)

# ---------------- Меню ----------------

class Station(Base, TimestampMixin):
    __tablename__ = "stations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20), default="kitchen")  # kitchen|bar|grill
    printer_config = mapped_column(JSONText, default=dict)
    cook_time_norm_min: Mapped[int] = mapped_column(Integer, default=15)


class MenuCategory(Base, TimestampMixin):
    __tablename__ = "menu_categories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    parent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("menu_categories.id"))
    name: Mapped[str] = mapped_column(String(120))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class MenuItem(Base, TimestampMixin):
    __tablename__ = "items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    category_id: Mapped[Optional[str]] = mapped_column(ForeignKey("menu_categories.id"))
    name: Mapped[str] = mapped_column(String(200))
    article: Mapped[str] = mapped_column(String(40))
    description: Mapped[Optional[str]] = mapped_column(Text)
    photo_url: Mapped[Optional[str]] = mapped_column(String(300))
    unit: Mapped[str] = mapped_column(String(20), default="portion")  # g|ml|pcs|portion
    station_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stations.id"))
    taxable: Mapped[bool] = mapped_column(Boolean, default=True)
    track_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    is_set: Mapped[bool] = mapped_column(Boolean, default=False)   # комплект/комбо
    set_items = mapped_column(JSONText, default=list)              # [{item_id, qty}]
    price: Mapped[float] = mapped_column(Money, default=0)         # текущая цена (кэш; история в price_history)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    unavailable_until: Mapped[Optional[datetime]] = mapped_column(DateTime)  # временное снятие с продажи
    __table_args__ = (UniqueConstraint("venue_id", "article"),)


class PriceHistory(Base, TimestampMixin):
    __tablename__ = "price_history"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), index=True)
    old_price: Mapped[float] = mapped_column(Money)
    new_price: Mapped[float] = mapped_column(Money)
    valid_from: Mapped[datetime] = mapped_column(DateTime)
    changed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class ScheduledPrice(Base, TimestampMixin):
    __tablename__ = "scheduled_prices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"))
    new_price: Mapped[float] = mapped_column(Money)
    scheduled_from: Mapped[datetime] = mapped_column(DateTime)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)


class ModifierGroup(Base, TimestampMixin):
    __tablename__ = "modifier_groups"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(120))
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    min_select: Mapped[int] = mapped_column(Integer, default=0)
    max_select: Mapped[int] = mapped_column(Integer, default=1)


class Modifier(Base, TimestampMixin):
    __tablename__ = "modifiers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("modifier_groups.id"))
    item_id: Mapped[Optional[str]] = mapped_column(ForeignKey("items.id"))  # привязка к блюду (опц.)
    name: Mapped[str] = mapped_column(String(150))
    price_add: Mapped[float] = mapped_column(Money, default=0)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"))  # добавка списывается со склада


class Recipe(Base, TimestampMixin):
    __tablename__ = "recipes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), unique=True)
    output_qty: Mapped[float] = mapped_column(Qty, default=1)
    output_unit: Mapped[str] = mapped_column(String(20), default="portion")
    is_semi: Mapped[bool] = mapped_column(Boolean, default=False)


class RecipeLine(Base, TimestampMixin):
    __tablename__ = "recipe_lines"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("recipes.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[float] = mapped_column(Qty)          # норма расхода в base_uom товара
    uom: Mapped[str] = mapped_column(String(20), default="base")
    note: Mapped[Optional[str]] = mapped_column(String(200))

# ---------------- Заказы ----------------

ORDER_STATUSES = ["created","open","sent","cooking","ready","awaiting_payment","paid","closed","cancelled"]


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    number: Mapped[int] = mapped_column(Integer, unique=True)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"), index=True)
    register_id: Mapped[Optional[str]] = mapped_column(ForeignKey("cash_registers.id"))
    shift_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shifts.id"))
    table_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tables.id"), index=True)
    waiter_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    guest_id: Mapped[Optional[str]] = mapped_column(ForeignKey("guests.id"))
    status: Mapped[str] = mapped_column(String(20), default="created", index=True)
    subtotal: Mapped[float] = mapped_column(Money, default=0)
    discount_total: Mapped[float] = mapped_column(Money, default=0)
    total: Mapped[float] = mapped_column(Money, default=0)
    paid_amount: Mapped[float] = mapped_column(Money, default=0)
    refund_amount: Mapped[float] = mapped_column(Money, default=0)
    tip_amount: Mapped[float] = mapped_column(Money, default=0)
    cost_amount: Mapped[float] = mapped_column(Money, default=0)   # себестоимость проданного
    version: Mapped[int] = mapped_column(Integer, default=1)
    prebill_printed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text)
    cancelled_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    stock_posted: Mapped[bool] = mapped_column(Boolean, default=False)  # защита от повторного списания
    comment: Mapped[Optional[str]] = mapped_column(Text)


class OrderItem(Base, TimestampMixin):
    __tablename__ = "order_items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"))
    name_snapshot: Mapped[str] = mapped_column(String(200))
    qty: Mapped[float] = mapped_column(Qty, default=1)
    unit_price: Mapped[float] = mapped_column(Money)     # цена на момент добавления
    mods_total: Mapped[float] = mapped_column(Money, default=0)
    discount_pct: Mapped[float] = mapped_column(Numeric(5,2), default=0)
    discount_amt: Mapped[float] = mapped_column(Money, default=0)
    line_total: Mapped[float] = mapped_column(Money, default=0)
    course: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new|sent|cooking|ready|served|cancelled
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text)
    comment: Mapped[Optional[str]] = mapped_column(String(300))
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)   # soft-delete с историей
    locked: Mapped[bool] = mapped_column(Boolean, default=False)    # позиция закреплена за сотрудником (конкурентная правка)
    locked_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    split_id: Mapped[Optional[str]] = mapped_column(ForeignKey("order_splits.id"))


class ItemModifierRec(Base, TimestampMixin):
    __tablename__ = "item_modifiers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    order_item_id: Mapped[str] = mapped_column(ForeignKey("order_items.id"), index=True)
    modifier_id: Mapped[str] = mapped_column(ForeignKey("modifiers.id"))
    name_snapshot: Mapped[str] = mapped_column(String(150), default="")
    qty: Mapped[float] = mapped_column(Qty, default=1)
    price_add: Mapped[float] = mapped_column(Money, default=0)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"))


class OrderSplit(Base, TimestampMixin):
    __tablename__ = "order_splits"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    label: Mapped[str] = mapped_column(String(60))
    share_amount: Mapped[float] = mapped_column(Money, default=0)
    paid_amount: Mapped[float] = mapped_column(Money, default=0)


class OrderStatusHistory(Base, TimestampMixin):
    __tablename__ = "order_status_history"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    old_status: Mapped[Optional[str]] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20))
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    note: Mapped[Optional[str]] = mapped_column(Text)


class KitchenTicket(Base, TimestampMixin):
    __tablename__ = "kitchen_tickets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    order_item_id: Mapped[str] = mapped_column(ForeignKey("order_items.id"))
    station_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stations.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|cooking|done|cancelled
    priority: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: now_almaty_ref())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reprint_no: Mapped[int] = mapped_column(Integer, default=0)
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text)
    copy_flag: Mapped[bool] = mapped_column(Boolean, default=False)

def now_almaty_ref():
    from .db import now_almaty
    return now_almaty()

# ---------------- Гости и лояльность ----------------

class Guest(Base, TimestampMixin):
    __tablename__ = "guests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[Optional[str]] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str] = mapped_column(String(30), index=True)
    email: Mapped[Optional[str]] = mapped_column(String(120))
    card_number: Mapped[Optional[str]] = mapped_column(String(40), unique=True)
    tier: Mapped[str] = mapped_column(String(20), default="base")
    bonuses_balance: Mapped[float] = mapped_column(Numeric(12,1), default=0)
    birthday: Mapped[Optional[datetime]] = mapped_column(DateTime)
    note: Mapped[Optional[str]] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class BonusOperation(Base, TimestampMixin):
    __tablename__ = "bonus_operations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id"), index=True)
    delta: Mapped[float] = mapped_column(Numeric(12,1))
    balance_after: Mapped[float] = mapped_column(Numeric(12,1))
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"))
    reason: Mapped[str] = mapped_column(String(200))
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class DepositAccount(Base, TimestampMixin):
    __tablename__ = "deposit_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id"), unique=True)
    balance: Mapped[float] = mapped_column(Money, default=0)


class DepositOperation(Base, TimestampMixin):
    __tablename__ = "deposit_operations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id"), index=True)
    type: Mapped[str] = mapped_column(String(10))  # in|out
    amount: Mapped[float] = mapped_column(Money)
    balance_after: Mapped[float] = mapped_column(Money)
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"))
    reason: Mapped[Optional[str]] = mapped_column(String(200))
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class DiscountRule(Base, TimestampMixin):
    __tablename__ = "discount_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[Optional[str]] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="percent")  # percent|amount|bonus_accrual
    value: Mapped[float] = mapped_column(Numeric(10,2), default=0)
    max_share_pct: Mapped[float] = mapped_column(Numeric(5,2), default=100)  # для бонусов — макс. % оплаты бонусами
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PromoCode(Base, TimestampMixin):
    __tablename__ = "promo_codes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    rule_id: Mapped[Optional[str]] = mapped_column(ForeignKey("discount_rules.id"))
    discount_pct: Mapped[float] = mapped_column(Numeric(5,2), default=0)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    max_uses: Mapped[Optional[int]] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class GiftCertificate(Base, TimestampMixin):
    __tablename__ = "gift_certificates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    nominal: Mapped[float] = mapped_column(Money)
    balance: Mapped[float] = mapped_column(Money)
    issued_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|used|expired|blocked
    used_by_order: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"))


class CertificateOperation(Base, TimestampMixin):
    """История использования сертификатов (полный аудит; повторное использование исключено)."""
    __tablename__ = "certificate_operations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    certificate_id: Mapped[str] = mapped_column(ForeignKey("gift_certificates.id"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"))
    amount: Mapped[float] = mapped_column(Money)
    balance_after: Mapped[float] = mapped_column(Money)
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class SaleDiscount(Base, TimestampMixin):
    """Каждое применение скидки — запись с причиной и подтверждением."""
    __tablename__ = "sale_discounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    order_item_id: Mapped[Optional[str]] = mapped_column(ForeignKey("order_items.id"))
    pct: Mapped[float] = mapped_column(Numeric(5,2), default=0)
    amount: Mapped[float] = mapped_column(Money, default=0)
    reason: Mapped[str] = mapped_column(String(200))
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))

# ---------------- Контроль: аудит, уведомления, документы, бэкапы ----------------

class AuditEvent(Base, TimestampMixin):
    """Append-only журнал аудита. Обновление/удаление запрещены приложением; исправления — новые события."""
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(DateTime, default=lambda: now_almaty_ref(), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), index=True)
    user_role: Mapped[Optional[str]] = mapped_column(String(20))
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    object_type: Mapped[Optional[str]] = mapped_column(String(40))
    object_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    venue_id: Mapped[Optional[str]] = mapped_column(ForeignKey("venues.id"))
    old_values = mapped_column(JSONText)
    new_values = mapped_column(JSONText)
    amount: Mapped[Optional[float]] = mapped_column(Money)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    register_id: Mapped[Optional[str]] = mapped_column(ForeignKey("cash_registers.id"))
    workstation: Mapped[Optional[str]] = mapped_column(String(80))


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(String(60), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    link_object_type: Mapped[Optional[str]] = mapped_column(String(40))
    link_object_id: Mapped[Optional[str]] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info|warn|critical
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), index=True)  # null → широковещательное
    status: Mapped[str] = mapped_column(String(10), default="new")     # new|read|acked
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="internal")
    tg_message_id: Mapped[Optional[str]] = mapped_column(String(40))


class NotificationRule(Base, TimestampMixin):
    __tablename__ = "notification_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    threshold_amount: Mapped[Optional[float]] = mapped_column(Money)
    recipients_role: Mapped[Optional[str]] = mapped_column(String(20))  # например manager
    quiet_hours: Mapped[Optional[str]] = mapped_column(String(20))      # "23:00-07:00"
    dedupe_window_min: Mapped[int] = mapped_column(Integer, default=10)
    channels: Mapped[str] = mapped_column(String(40), default="internal")  # internal,telegram
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[Optional[str]] = mapped_column(ForeignKey("venues.id"))
    kind: Mapped[str] = mapped_column(String(20), index=True)  # z_report|expense|contract|other
    shift_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shifts.id"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"))
    file_path: Mapped[str] = mapped_column(String(400))
    file_name: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    doc_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    amount_declared: Mapped[Optional[float]] = mapped_column(Money)
    amount_ocr: Mapped[Optional[float]] = mapped_column(Money)   # результат OCR (опционально)
    ocr_status: Mapped[str] = mapped_column(String(15), default="none")
    verified_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class Backup(Base, TimestampMixin):
    __tablename__ = "backups"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    path: Mapped[str] = mapped_column(String(400))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(10), default="auto")  # auto|manual|user:<id>
    status: Mapped[str] = mapped_column(String(15), default="created")   # created|verified|failed
    verify_restore_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    table_counts = mapped_column(JSONText, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)


class IdempotencyKey(Base, TimestampMixin):
    __tablename__ = "idempotency_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_json = mapped_column(JSONText)
    status_code: Mapped[int] = mapped_column(Integer, default=200)


class IntegrationLog(Base, TimestampMixin):
    __tablename__ = "integration_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    adapter: Mapped[str] = mapped_column(String(40), index=True)   # fiscal|terminal|printer|telegram|ofd
    operation: Mapped[str] = mapped_column(String(60))
    ref_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(15), default="pending", index=True)  # pending|sent|confirmed|failed
    device_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[Optional[str]] = mapped_column(Text)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Setting(Base, TimestampMixin):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    venue_id: Mapped[Optional[str]] = mapped_column(ForeignKey("venues.id"))
    value = mapped_column(JSONText, default=dict)


# ---------------- Платежи, возвраты, смены ----------------

PAYMENT_STATUSES = ["confirmed", "reversed", "pending"]


class PaymentMethod(Base, TimestampMixin):
    __tablename__ = "payment_methods"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(30), unique=True)   # cash|card|qr|deposit|certificate|bonus|mixed|other
    name: Mapped[str] = mapped_column(String(100))
    is_cash: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Shift(Base, TimestampMixin):
    __tablename__ = "shifts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"), index=True)
    register_id: Mapped[str] = mapped_column(ForeignKey("cash_registers.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(15), default="open", index=True)  # open|closed
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: now_almaty_ref())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    initial_cash: Mapped[float] = mapped_column(Money, default=0)
    expected_cash: Mapped[Optional[float]] = mapped_column(Money)
    actual_cash: Mapped[Optional[float]] = mapped_column(Money)
    difference: Mapped[Optional[float]] = mapped_column(Money)
    closed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    shift_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shifts.id"), index=True)
    split_id: Mapped[Optional[str]] = mapped_column(ForeignKey("order_splits.id"))
    method: Mapped[str] = mapped_column(String(30), index=True)
    amount: Mapped[float] = mapped_column(Money)
    tip: Mapped[float] = mapped_column(Money, default=0)
    status: Mapped[str] = mapped_column(String(15), default="confirmed", index=True)  # confirmed|reversed|pending
    refunded_amount: Mapped[float] = mapped_column(Money, default=0)
    guest_id: Mapped[Optional[str]] = mapped_column(ForeignKey("guests.id"))
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    card_mask: Mapped[Optional[str]] = mapped_column(String(19))   # только mask PAN (****1234); полные данные не хранятся
    auth_code: Mapped[Optional[str]] = mapped_column(String(40))
    external_ref: Mapped[Optional[str]] = mapped_column(String(80))
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), unique=True)
    fiscal_state: Mapped[str] = mapped_column(String(15), default="none")  # none|pending|done|failed
    fiscal_receipt_no: Mapped[Optional[str]] = mapped_column(String(60))


class Refund(Base, TimestampMixin):
    __tablename__ = "refunds"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    shift_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shifts.id"), index=True)
    amount: Mapped[float] = mapped_column(Money)
    reason: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(DateTime, default=lambda: now_almaty_ref())


class CashOperation(Base, TimestampMixin):
    __tablename__ = "cash_operations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    shift_id: Mapped[str] = mapped_column(ForeignKey("shifts.id"), index=True)
    type: Mapped[str] = mapped_column(String(15))  # in|out|transfer_in|transfer_out|correction
    amount: Mapped[float] = mapped_column(Money)
    reason: Mapped[str] = mapped_column(String(200))
    from_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    to_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(DateTime, default=lambda: now_almaty_ref())


# ---------------- Финансовый учёт ----------------

class FinancialAccount(Base, TimestampMixin):
    __tablename__ = "financial_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(15), default="cash")  # cash|bank|mobile|other
    balance: Mapped[float] = mapped_column(Money, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class ExpenseCategory(Base, TimestampMixin):
    __tablename__ = "expense_categories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[Optional[str]] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(15), default="opex")  # cogs|opex|payroll|tax|other
    parent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("expense_categories.id"))


class FinTransaction(Base, TimestampMixin):
    """Проводки управленческого учёта. Проведённые документы редактируются только через reverse+новая запись."""
    __tablename__ = "fin_transactions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"), index=True)
    account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("financial_accounts.id"))
    category_id: Mapped[Optional[str]] = mapped_column(ForeignKey("expense_categories.id"))
    type: Mapped[str] = mapped_column(String(25), index=True)  # sale_revenue|cost|expense|advance|payout|debt_in|debt_out|transfer|correction|reverse
    direction: Mapped[str] = mapped_column(String(4), default="in")  # in|out
    amount: Mapped[float] = mapped_column(Money)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    doc_ref: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    counterparty: Mapped[Optional[str]] = mapped_column(String(150))
    comment: Mapped[Optional[str]] = mapped_column(Text)
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(12), default="posted")  # draft|posted|reversed
    reversed_of_id: Mapped[Optional[str]] = mapped_column(ForeignKey("fin_transactions.id"))


class DebtRecord(Base, TimestampMixin):
    __tablename__ = "debt_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    party: Mapped[str] = mapped_column(String(150))     # контрагент или сотрудник
    kind: Mapped[str] = mapped_column(String(10), default="receivable")  # receivable|payable|advance
    amount: Mapped[float] = mapped_column(Money)
    closed_amount: Mapped[float] = mapped_column(Money, default=0)
    date: Mapped[datetime] = mapped_column(DateTime, default=lambda: now_almaty_ref())
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|partial|closed
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


# ---------------- Склад и закупки ----------------

class Warehouse(Base, TimestampMixin):
    __tablename__ = "warehouses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(120))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"), index=True)
    warehouse_id: Mapped[Optional[str]] = mapped_column(ForeignKey("warehouses.id"))
    article: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    base_uom: Mapped[str] = mapped_column(String(20), default="kg")       # базовая единица хранения
    purchase_uom: Mapped[str] = mapped_column(String(20), default="pcs")  # единица закупки
    coef: Mapped[float] = mapped_column(Qty, default=1)                   # 1 purchase_uom = coef base_uom
    min_stock: Mapped[float] = mapped_column(Qty, default=0)
    barcode: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    last_purchase_price: Mapped[float] = mapped_column(Money, default=0)  # цена за purchase_uom
    track_lots: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("venue_id", "article"),)


class StockBalance(Base, TimestampMixin):
    __tablename__ = "stock_balances"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[float] = mapped_column(Qty, default=0)
    __table_args__ = (UniqueConstraint("warehouse_id", "product_id"),)


class StockMovement(Base, TimestampMixin):
    """Неизменяемый журнал складских операций (append-only)."""
    __tablename__ = "stock_movements"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    qty_signed: Mapped[float] = mapped_column(Qty)
    doc_type: Mapped[str] = mapped_column(String(20), index=True)  # receipt|sale|writeoff|transfer_in|transfer_out|inventory|return_to_supplier|production
    doc_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    unit_cost: Mapped[float] = mapped_column(Money, default=0)
    negative_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    lot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stock_lots.id"))
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(DateTime, default=lambda: now_almaty_ref(), index=True)


class StockLot(Base, TimestampMixin):
    __tablename__ = "stock_lots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    qty_left: Mapped[float] = mapped_column(Qty, default=0)
    unit_cost: Mapped[float] = mapped_column(Money, default=0)
    expiry_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    receipt_id: Mapped[Optional[str]] = mapped_column(ForeignKey("goods_receipts.id"))


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(200))
    contacts: Mapped[Optional[str]] = mapped_column(String(300))
    requisites: Mapped[Optional[str]] = mapped_column(String(300))  # БИН и т.п.
    terms: Mapped[Optional[str]] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


DOC_STATUSES = ["draft", "pending_approval", "approved", "posted", "cancelled"]


class PurchaseOrder(Base, TimestampMixin):
    __tablename__ = "purchase_orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    number: Mapped[str] = mapped_column(String(30), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    total: Mapped[float] = mapped_column(Money, default=0)
    expected_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class PurchaseOrderLine(Base, TimestampMixin):
    __tablename__ = "purchase_order_lines"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    ordered_qty: Mapped[float] = mapped_column(Qty)      # в purchase_uom
    received_qty: Mapped[float] = mapped_column(Qty, default=0)
    price: Mapped[float] = mapped_column(Money, default=0)


class GoodsReceipt(Base, TimestampMixin):
    __tablename__ = "goods_receipts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    po_id: Mapped[Optional[str]] = mapped_column(ForeignKey("purchase_orders.id"))
    number: Mapped[str] = mapped_column(String(30), unique=True)
    invoice_no: Mapped[Optional[str]] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(15), default="draft", index=True)  # draft|posted|cancelled
    total: Mapped[float] = mapped_column(Money, default=0)
    posted_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class GoodsReceiptLine(Base, TimestampMixin):
    __tablename__ = "goods_receipt_lines"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    receipt_id: Mapped[str] = mapped_column(ForeignKey("goods_receipts.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    ordered_qty: Mapped[float] = mapped_column(Qty, default=0)   # если есть PO — заказано (purchase_uom)
    received_qty: Mapped[float] = mapped_column(Qty)             # фактически принято (purchase_uom)
    price: Mapped[float] = mapped_column(Money)                  # за purchase_uom
    expiry_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    lot_note: Mapped[Optional[str]] = mapped_column(String(120))


class WriteOff(Base, TimestampMixin):
    __tablename__ = "writeoffs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    number: Mapped[str] = mapped_column(String(40), unique=True)
    reason: Mapped[str] = mapped_column(String(30))              # spoilage|production|inventory_loss|other
    reason_text: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(15), default="draft")
    total_cost: Mapped[float] = mapped_column(Money, default=0)
    lines = mapped_column(JSONText, default=list)                # [{product_id, qty}]
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class Inventory(Base, TimestampMixin):
    __tablename__ = "inventories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    number: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(15), default="draft")
    diff_total: Mapped[float] = mapped_column(Money, default=0)
    by_user: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class InventoryLine(Base, TimestampMixin):
    __tablename__ = "inventory_lines"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    inventory_id: Mapped[str] = mapped_column(ForeignKey("inventories.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    expected_qty: Mapped[float] = mapped_column(Qty)
    actual_qty: Mapped[float] = mapped_column(Qty)
    diff_qty: Mapped[float] = mapped_column(Qty)
    unit_cost: Mapped[float] = mapped_column(Money, default=0)


Index("ix_mov_prod_wh", StockMovement.product_id, StockMovement.warehouse_id)
Index("ix_pay_shift_method", Payment.shift_id, Payment.method)
