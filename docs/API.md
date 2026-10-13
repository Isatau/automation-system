# API RestoCore (обзор эндпоинтов)

База: `/api/v1`. Auth: `Authorization: Bearer <JWT>`. Мутирующие критические операции принимают заголовок `Idempotency-Key`.
Полная интерактивная документация OpenAPI — `/docs` (запускается вместе с сервером).

## Авторизация и пользователи
- POST `/auth/login` — вход (защита от перебора: блокировка после N неудач, событие аудита)
- POST `/auth/logout`, GET `/auth/me`
- GET/POST/PATCH `/admin/users`, POST `/admin/users/{id}/lock|unlock`, PUT `/admin/users/{id}/permissions`

## Заказы (касса/официант)
- POST `/orders` — создание (зал/стол/официант/заведение; номер генерируется автоматически)
- GET `/orders?status&table&waiter&date_from&date_to&number&q` — поиск; GET `/orders/{id}` — состав+история (восстановление незакрытого заказа)
- POST `/orders/{id}/items` — позиция (модификаторы, добавки, комментарий); PATCH `/orders/items/{id}` — количество/цена(с правом)/скидка; DELETE — удаление позиции (причина + аудит)
- POST `/orders/{id}/send` — на кухню (создаёт kitchen_tickets по станциям)
- POST `/orders/{id}/discount` — скидка по заказу (лимит/подтверждение)
- POST `/orders/{id}/split` — разделение счёта; POST `/orders/{id}/merge` {with_order_id} — объединение
- POST `/orders/{id}/transfer` {table_id} — перенос между столами
- POST `/orders/{id}/prebill` — предзаказ/предварительный счёт (отметка)
- POST `/orders/{id}/cancel` {reason, confirm_login?, confirm_password?} — отмена
- POST `/orders/{id}/close` — закрытие оплаченного заказа

## Платежи
- GET `/payment-methods`
- POST `/orders/{id}/payments` {method, amount, tip, split_id?, bonus_guest_id?, certificate_code?, deposit_guest_id?} — частичная/смешанная/раздельная оплата; защита от превышения и двойного проведения (идемпотентность + проверка статуса/баланса в транзакции)
- POST `/payments/{id}/refund` {amount, reason, confirm...} — возврат
- GET `/orders/{id}/payments`, GET `/payments/reconciliation?date` — сверка платежей и заказов

## Кассовые смены
- POST `/shifts/open` {register_id, initial_cash}
- GET `/shifts/current?register_id`, GET `/shifts/{id}/summary` (по способам оплаты, ожидаемый остаток)
- POST `/shifts/{id}/cash-in|cash-out|transfer` {amount, reason, to_user?}
- POST `/shifts/{id}/close` {actual_cash} → расчёт expected/difference, Z-привязка документов
- POST `/shifts/{id}/reopen` {reason} — только shifts:reopen, событие аудита

## Зал
- GET `/halls?venue_id` (+tables с статусами, суммой открытого заказа, официантом, временем обслуживания)
- CRUD `/halls`, `/tables`; POST `/tables/{id}/merge-group`, `/unmerge`
- CRUD `/reservations`; почасовые зоны/тарифы для бара/караоке (`hourly`, `tariff_id`)

## Меню
- CRUD `/categories`, `/items`, `/modifier-groups`, `/modifiers`, `/stations`
- POST `/items/{id}/availability` — временное снятие с продажи
- PUT `/items/{id}/price` {price, valid_from | scheduled_from} — история цен, планирование; разные цены по venue/zone
- CRUD `/items/{id}/recipe` — техкарта (ингредиенты, нормы, выход, полуфабрикаты)

## Кухня/KDS
- GET `/kitchen/tickets?station&status` — очереди кухни/бара; подсветка просрочки рассчитывается временем sent_at
- POST `/kitchen/tickets/{id}/start|done|cancel`
- POST `/kitchen/tickets/{id}/reprint` — копия с отметкой reprint_no
- События готовности отправляются уведомлениями официанту.

## Склад и закупки
- CRUD `/warehouses`, `/products`, `/suppliers`; GET `/stock?warehouse&product`
- POST `/receipts` (приход, партии/сроки), `/transfers`, `/writeoffs`, `/returns-to-supplier`
- POST `/inventories` + lines → проведение корректировок; GET `/stock/movements` — журнал (append-only)
- GET `/stock/alerts` — минимальные запасы; GET `/stock/consumption?period` — факт/теория
- POST `/purchase-orders` (draft→pending_approval→approved→posted→cancelled), POST `/purchase-orders/{id}/receive` — сверка заказано/получено

## Финансы
- CRUD `/accounts`, `/expense-categories`; POST `/fin-transactions` (доход/расход/перевод/аванс)
- CRUD `/debts`; GET `/finance/pnl?from&to&compare=prev_period` — выручка−себестоимость−расходы, маржа

## Персонал и зарплата
- CRUD `/staff`; POST `/time/clock-in|clock-out` (опоздания), GET `/time/entries`
- CRUD `/schedules`, `/pay-rules`
- POST `/payroll/calculate` {staff_id, period} — сохраняет snapshot формулы; ведомости `/payroll/register`

## Гости и лояльность
- CRUD `/guests`; GET `/guests/{id}/history|bonus-ops|deposits`
- POST `/guests/{id}/deposit` /withdraw; POST `/loyalty/adjust` {delta, reason} (право loyalty:adjust)
- CRUD `/discount-rules`, `/promo-codes`, `/certificates`

## Аудит, уведомления, документы
- GET `/audit?user&type&object&from&to` (только чтение), GET `/audit/export`
- GET `/notifications`, POST `/notifications/{id}/read|ack`; CRUD `/notification-rules` (пороги, получатели, тихие часы); POST `/notifications/test-telegram`
- POST `/documents/upload` (Z-отчёт/расход, sha256-контроль), GET `/documents?shift&kind&period`, POST `/documents/{id}/verify`, OCR-поле суммы (optional stub)

## Отчёты
- GET `/reports/sales|payments-by-method|by-staff|by-category|top-items|cancels-refunds|discounts|shifts|cash-diff|expenses|stock|movements|cost|profit|worktime|events ?from&to&venue&…`
- GET `/reports/{type}/export?format=xlsx|csv|pdf`

## Резервные копии и администрирование
- POST `/backups/run` (manual), GET `/backups` (включая verify_restore_ok), POST `/backups/{id}/restore`
- GET/PUT `/settings/{key}`, GET `/integrations/status`, POST `/integrations/{adapter}/retry-failed`

## Панель управляющего (агрегаты)
- GET `/dashboard?venue_id&date` — выручка дня, чеки, средний чек, открытые заказы, наличные/безналичные, категории, топ позиций, состояние смен/склада, критические события, текущие смены сотрудников, финпоказатели. Каждый показатель раскрывается соответствующим отчётом с исходными операциями.
