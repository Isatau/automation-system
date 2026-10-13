# RestoCore — Система автоматизации ресторана

## 1. Обоснование стека технологий

| Слой | Технология | Обоснование |
|---|---|---|
| Backend API | Python 3.12 + FastAPI | Асинхронность, OpenAPI-документация «из коробки», сильная типизация через Pydantic, зрелая экосистема. |
| ORM / БД | SQLAlchemy 2.0; PostgreSQL (docker-compose) / SQLite (dev, тесты) | Один код для обеих СУБД; `Numeric` даёт точную десятичную арифметику. Для продакшена нескольких касс — PostgreSQL с `SELECT ... FOR UPDATE`. |
| Frontend | Vanilla JS SPA без сборки | Полностью функционален сразу, без npm-инфраструктуры; миграция на React+TS возможна без изменения API. |
| Auth | JWT (HS256) + bcrypt | Stateless-сессии, завершение по TTL; пароли — только bcrypt-хеши. |
| Отчёты | openpyxl (XLSX), csv (stdlib), reportlab (PDF) | Реальный экспорт из данных БД. |
| Фоновые задачи | Планировщик в процессе + таблица jobs | Резервные копии, уведомления, синхронизация; заменяется на Celery/RQ без изменения бизнес-логики. |
| Тесты | pytest + TestClient | Unit + интеграционные API-тесты критических сценариев. |
| Развёртывание | Docker Compose (Postgres + API) либо локальный запуск | Сервер в сети заведения; БД наружу не публикуется. |

**Архитектурные решения:**
- **Модуль-монолит** с разделением доменов (orders, payments, shifts, warehouse, menu, kitchen, finance, hr, loyalty, audit, notifications, reports, backups, integrations). Единая транзакционная граница критична для денег и склада. Рост — вынос сервисов по outbox-событиям.
- **Мультизаведённость**: поле `venue_id` в операционных таблицах + скоуп прав пользователя.
- **Транзакционность**: каждая критическая операция — одна БД-транзакция.
- **Неизменяемый аудит**: append-only журнал; исправления — новые записи.
- **Идемпотентность**: заголовок `Idempotency-Key`, уникальный индекс, повтор возвращает исходный результат.
- **Offline-friendly**: состояние незакрытого заказа хранится на сервере (восстановление после перезапуска клиента); очередь интеграций с retry.

## 2. Структура каталогов

```
/workspace
├── README.md                  # быстрый старт
├── requirements.txt
├── docker-compose.yml         # Postgres + API
├── run.sh                     # локальный запуск
├── docs/  ARCHITECTURE.md DATABASE.md API.md RBAC.md PROCESSES.md
├── app/
│   ├── main.py                # FastAPI, lifespan, планировщик
│   ├── config.py              # настройки из ENV
│   ├── db.py                  # engine/session, серверное время Asia/Almaty
│   ├── models.py              # ~45 таблиц SQLAlchemy
│   ├── security.py            # bcrypt, JWT, защита от перебора
│   ├── rbac.py                # роли → разрешения, серверная проверка
│   ├── seed.py                # начальные данные
│   ├── deps.py                # DB session, current_user, require_perm
│   ├── routers/  auth orders payments shifts hall menu kitchen warehouse
│   │             suppliers finance staff guests audit notifications
│   │             reports documents backups admin
│   ├── services/ order_service payment_service shift_service stock_service
│   │             loyalty_service pricing audit idempotency notify backup exporters
│   ├── integrations/ base fiscal_kz terminal printer telegram
│   └── static/    index.html app.js styles.css
├── migrations/versions/       # SQL-миграции схемы
└── tests/                     # pytest: unit + API-интеграция
```

## 3. Схема базы данных (кратко; полная — docs/DATABASE.md)

Все денежные поля — `Numeric(14,2)`; все таблицы имеют `created_at/updated_at`, UUID-идентификаторы.

- Организация: `venues`, `cash_registers`, `halls`, `tables`, `reservations`
- Люди/доступ: `users`, `staff`, `roles`(enum), `sessions`, `login_attempts`
- Меню: `menu_categories`, `items`, `item_prices`, `price_history`, `modifier_groups`, `modifiers`, `stations`, `recipes`, `recipe_lines`
- Заказы: `orders`, `order_items`, `item_modifiers`, `order_splits`, `order_status_history`, `kitchen_tickets`
- Деньги: `shifts`, `payment_methods`, `payments`, `refunds`, `cash_operations`, `financial_accounts`, `fin_transactions`, `debts`
- Склад: `warehouses`, `products`, `uom_conversions`, `stock_balances`, `stock_movements` (append-only), `suppliers`, `purchase_orders`, `goods_receipts`, `receipt_lines`, `write_offs`, `inventories`, `inventory_lines`, `stock_lots`
- Лояльность: `guests`, `bonus_operations`, `deposit_accounts`, `deposit_operations`, `discount_rules`, `promo_codes`, `gift_certificates`, `sale_discounts`
- Персонал: `work_schedules`, `time_entries`, `pay_rules`, `payroll_accruals`
- Контроль: `audit_events`, `notifications`, `notification_rules`, `documents`, `backups`, `idempotency_keys`, `integration_log`, `settings`

## 4. API (обзор; полный список — docs/API.md)

REST, префикс `/api/v1`, OpenAPI на `/docs`, `Authorization: Bearer <JWT>`.
Группы: auth, orders (+items/split/transfer/cancel), payments (+refund), shifts (+open/close/cash-in/out/reopen), hall (+tables/reservations), menu (+recipes/modifiers/prices), kitchen (KDS), warehouse (+receipts/movements/writeoffs/inventory), suppliers (+purchase orders), finance (+accounts/transactions/P&L), staff (+payroll), guests (+loyalty/deposits/promocodes), audit, notifications (+rules), reports (+export xlsx/csv/pdf), documents (Z-отчёты), backups (+restore), admin (+users/settings/integrations).

Мутирующие защищённые операции принимают `Idempotency-Key`.

## 5. Матрица ролей и разрешений

См. docs/RBAC.md. Разрешения `module:action`; проверка на сервере dependency-декоратором в каждом эндпоинте. Роли: owner, manager, shift_admin, cashier, waiter, bartender, cook, storekeeper, accountant, tech_admin.

## 6. Основные бизнес-процессы

См. docs/PROCESSES.md. Ключевые инварианты:

1. **Заказ**: created→open→sent→cooking→ready→awaiting_payment→paid→closed; cancel из незакрытых статусов с обязательной причиной; машина состояний + оптимистичная блокировка `version`.
2. **Оплата**: Σ платежей ≤ итог заказа; частичная/смешанная/раздельная; возврат уменьшает оплаченное без удаления истории; повторная оплата закрытого заказа запрещена; фискализация — отдельная стадия в `integration_log`, успех фиксируется только при подтверждении адаптера.
3. **Смена**: `expected_cash = initial + Σ наличных поступлений − Σ наличных возвратов + внесения − изъятия ± входящие передачи`; `diff = fact − expected`; закрытая смена неизменяема, reopen только с правом `shifts:reopen` и событием аудита.
4. **Продажа→склад**: при оплате позиции списываются по действующей техкарте (FIFO по партиям); отрицательные остатки — только с правом и причиной.
5. **Лояльность**: бонусы/депозиты меняются только записями операций в той же транзакции, что и платёж.

## 7. План разработки и зависимости

Этап 1 архитектура → 2 схема БД → 3 каркас + auth/RBAC → 4 касса/меню/заказы → 5 платежи/смены → 6 панель/отчёты → 7 персонал/склад/закупки → 8 кухня/бар/уведомления → 9 интеграции/бэкапы → 10 тесты/доки.
Зависимости: всё зависит от этапа 3; склад зависит от меню (техкарты); отчёты — от заказов/платежей/склада; уведомления — от аудита; зарплата — от смен сотрудников и продаж.

## 8. Внешние интеграции, требующие отдельной проверки на месте

- Фискальный регистратор / ОНКК (Казахстан: ККМ, ШК/ФД, ОФД) — нужен реальный аппарат или тестовый контур ОФД.
- Банковские POS-терминалы (протокол поставщика, например Ingenico/BBPOS SDK). Секреты терминалов — только в ENV/хранилище секретов, никогда в БД в открытом виде.
- Принтеры чеков/кухни (Epson TM, LAN/USB/CUPS), сканеры штрихкодов (HID), весы (RS-232).
- Telegram Bot API, ОФД, 1С, сервисы доставки — по ключам/API-контрактам поставщиков.

Адаптеры имеют журнал ошибок и retry безопасных операций; заглушки помечаются `stub` и не отмечают операцию выполненной без подтверждения устройства.
