# Схема базы данных RestoCore

Общие правила: PK — UUID (строковый); везде `created_at`, `updated_at`; денежные поля `NUMERIC(14,2)`;
количества/вес `NUMERIC(14,3)`; справочники имеют `archived` (архивирование вместо удаления);
финансовая история и журнал склада/аудита не удаляются и не изменяются (append-only).

## Организация
- **venues**: id, name, address, timezone(default Asia/Almaty), currency(KZT), tax_enabled, hourly_billing(bool), settings(JSON)
- **cash_registers**: id, venue_id, name, number, status
- **halls**: id, venue_id, name, kind(hall|bar_room|karaoke), zone, sort
- **tables**: id, hall_id, name, seats, status(free|reserved|occupied|servicing|payment|cleaning), waiter_id, merged_group_id, x,y (схема), hourly(bool), tariff_id
- **reservations**: id, table_id, guest_id, date_start, date_end, status, deposit_amount

## Доступ
- **users**: id, login UNIQUE, password_hash, role(enum 10 ролей), staff_id, venue_ids, active, locked, failed_attempts, locked_until, discount_limit NUMERIC, last_login_at
- **role_permissions** — задаётся кодом (rbac.py) + переопределения в **user_permissions**(user_id, permission, granted)
- **sessions**: id, user_id, jti, expires_at, revoked_at, ip, workstation
- **login_attempts**: id, login, success, ip, at

## Меню
- **stations**: id, venue_id, name(kitchen|bar|grill…), printer_config
- **menu_categories**: id, venue_id, parent_id, name, sort, archived
- **items**: id, category_id, name, article UNIQUE(venue), description, photo_url, unit(g|ml|pcs|portion), station_id, taxable, track_stock, archived, temp_unavailable(bool до даты)
- **item_prices**: item_id, venue_id, zone, price, valid_from, scheduled_price, scheduled_from (разные цены по заведениям/зонам + планирование)
- **price_history**: item_id, old_price, new_price, changed_by, at
- **modifier_groups**: name, required, min/max, **modifiers**: group_id, item_id?, name, price_add
- **recipes**: item_id, output_qty, output_unit, is_semi
- **recipe_lines**: recipe_id, product_id, qty, uom, conversion_note

## Заказы
- **orders**: id, venue_id, number UNIQUE, register_id, shift_id, table_id, waiter_id, guest_id, status(created|open|sent|cooking|ready|awaiting_payment|paid|closed|cancelled), subtotal, discount_total, total, paid_amount, refund_amount, tip_amount, version(оптимистичная блокировка), cancel_reason, cancelled_by, prebill_printed_at, created_at, closed_at
- **order_items**: id, order_id, item_id, qty, unit_price, mods_total, discount_pct, discount_amt, line_total, course(передача), status(new|sent|cooking|ready|served|cancelled), cancel_reason, split_of
- **item_modifiers**: order_item_id, modifier_id, qty, price_add
- **order_splits**: order_id, label, share_amount, payments via split_id
- **order_status_history**: order_id, old, new, by_user, at, note
- **kitchen_tickets**: order_item(s), station_id, status(pending|cooking|done|cancelled), priority, sent_at, started_at, done_at, reprint_no, copy_flag

## Деньги
- **shifts**: id, venue_id, register_id, user_id, opened_at, initial_cash, status(open|closed), closed_at, expected_cash, actual_cash, difference, closed_by, reopen_count
- **payment_methods**: code(cash|card|mixed|deposit|certificate|bonus|qr|other), name, is_cash, active
- **payments**: id, order_id, shift_id, split_id?, method, amount, tip, auth_code, external_ref, status(pending|confirmed|failed|reversed), idempotency_key UNIQUE?, fiscal_state, created_at
- **refunds**: payment_id, amount, reason, approved_by, shift_id
- **cash_operations**: shift_id, type(in|out|transfer_in|transfer_out|correction), amount, reason, from_user, to_user, approved_by
- **financial_accounts**: venue_id, name(cash desk, bank…), type, balance-cache
- **fin_transactions**: account_id, type(income|expense|transfer|advance|salary|debt), category_id, amount, counterparty, doc_id, date, comment, venue_id
- **expense_categories**, **debts**(direction, amount, due, status)

## Склад
- **warehouses**, **products**(article UNIQUE, name, base_uom, purchase_uom, coef, min_stock, price_history через movements), **uom_conversions**(product_id, from_uom,to_uom,coef)
- **stock_balances**: warehouse_id, product_id, qty (кэш; источник истины — movements)
- **stock_movements** (append-only): id, warehouse_id, product_id, qty_signed, lot_id, doc_type(receipt|sale|writeoff|transfer|inventory|return_to_supplier|correction), doc_id, reason, unit_cost, user_id, at
- **stock_lots**: product_id, qty_left, expiry_date, receipt_id
- **suppliers**: name, contacts, requisites(INN…), rating
- **purchase_orders**(draft|pending_approval|approved|posted|cancelled) + lines
- **goods_receipts** + **receipt_lines**(ordered_qty vs received_qty, price, lot/expiry)
- **write_offs** + lines (reason enum), **inventories** + lines(expected, actual, diff)

## Лояльность
- **guests**: phone UNIQUE(venue), name, card_number, tier, bonuses_balance, archived
- **bonus_operations**: guest_id, delta, order_id?, reason, balance_after
- **deposit_accounts**(guest_id, balance), **deposit_operations**(in/out/order)
- **discount_rules**, **promo_codes**, **gift_certificates**(code, nominal, used_by_order), **sale_discounts**(order, pct/amount, reason, approver)

## Персонал
- **staff**: user-linked, position, hire_date, pay_type(hourly|shift|percent), rate, percent
- **work_schedules**(staff, date, start,end), **time_entries**(clock_in/out, late_minutes), **pay_rules**, **payroll_accruals**(base, hours, sales_pct, bonus, advance, total, formula_snapshot JSON, corrected_by chain)

## Контроль
- **audit_events** (append-only, защита UPDATE/DELETE на уровне приложения): id, at(server), user_id, role, event_type, object_type, object_id, venue_id, old_values JSON, new_values JSON, amount, reason, approved_by, workstation/register_id
- **notifications**: type, payload, link_object, status(new|read|acked), dedupe_key UNIQUE-window, channel(internal|telegram), tg_message_id
- **notification_rules**: event_type, threshold, recipients, quiet_hours
- **documents**: kind(z_report|expense_photo|contract), shift_id, file_path, sha256, amount_ocr, verified_by, date, sum_declared
- **backups**: path, sha256, size, created_by(auto|manual), verify_restore_ok, restored_at
- **idempotency_keys**: key UNIQUE, request_hash, response JSON, created_at
- **integration_log**: adapter, operation, payload_hash, state(sent|confirmed|failed), error, retries, next_retry_at
- **settings**: key, value JSON, venue_id?

## Индексы (основные)
orders(number), orders(venue,status), order_items(order), payments(order,shift,created), stock_movements(product,warehouse,at), audit_events(user,type,at), guests(phone), sessions(jti), documents(shift), notifications(dedupe_key).
