# RestoCore — система автоматизации ресторана, кафе, бара и караоке-клуба

Единая backend-система: касса, заказы и меню, платежи и кассовые смены, зал и бронирования,
кухня/бар (KDS), склад и закупки, финансы, сотрудники и зарплата, гости и лояльность,
аудит, уведомления, документы, отчёты с экспортом, резервное копирование и слой интеграций.

- Язык интерфейса API-ответов и данных — русский; валюта ₸ (KZT); время — Asia/Almaty.
- Деньги хранятся в точных десятичных типах (`NUMERIC(14,2)`), не float.
- Доступ проверяется **на сервере** (декораторы `require_perm`), а не только скрытием кнопок.
- Критические операции выполняются в транзакциях, защищены идемпотентностью и оптимистичной блокировкой.

## Архитектура

```
/workspace
├── app/
│   ├── main.py            # FastAPI: регистрация 20 роутеров /api/v1, lifespan (миграции, seed, фоновые задачи)
│   ├── config.py          # настройки только из ENV (.env)
│   ├── db.py              # SQLAlchemy engine/session, серверное время Asia/Almaty
│   ├── models.py          # нормализованная схема ~50 таблиц (см. docs/DATABASE.md)
│   ├── security.py        # bcrypt, JWT + jti-реестр сессий, блокировка при переборе паролей
│   ├── rbac.py            # 10 ролей × матрица разрешений, лимиты скидок, подтверждения руководителем
│   ├── deps.py            # current_user / require_perm на стороне сервера
│   ├── seed.py            # демо-данные (отключается SEED_DEMO=false)
│   ├── routers/           # auth, orders, payments, shifts, menu, hall, kitchen, warehouse,
│   │                      # suppliers, purchase_orders, finance, staff, payroll, guests,
│   │                      # auditlog, notifications, documents, reports, dashboard, admin
│   ├── services/          # бизнес-логика: order_service, payment_service, shift_service,
│   │                      # stock_service (FIFO-партии, списание по техкартам), loyalty_service,
│   │                      # pricing, audit, idempotency, notify, backup, exporters (XLSX/CSV/PDF)
│   ├── integrations/      # адаптеры: fiscal (заглушка Штрих-KZ), bank terminal, printer, telegram
│   └── static/            # собранный фронтенд (React build) — опционально
├── migrations/            # Alembic (первая миграция: 1a3caaebf52d_initial_schema)
├── tests/                 # pytest: критические сценарии (см. ниже)
├── docs/                  # ARCHITECTURE.md, DATABASE.md, API.md, RBAC.md, PROCESSES.md
├── docker-compose.yml     # api + postgres:16, volumes, healthcheck
├── Dockerfile
├── .env.example
└── requirements.txt
```

Документация: [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [DATABASE.md](docs/DATABASE.md) ·
[API.md](docs/API.md) · [RBAC.md](docs/RBAC.md) · [PROCESSES.md](docs/PROCESSES.md).
Интерактивная документация OpenAPI доступна на работающем сервере: `GET /docs` (Swagger) и `/redoc`.

## Быстрый старт (локально, SQLite)

```bash
cd /workspace
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # как минимум замените JWT_SECRET!
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

При первом запуске приложение автоматически:
1. применяет миграции Alembic (`alembic upgrade head`, идемпотентно);
2. при `SEED_DEMO=true` заполняет демо-данные (заведение, залы, столы, меню с техкартами, склады, пользователи).

Откройте `http://localhost:8000/docs` — весь API доступен прямо из браузера.

### Демо-пользователи (пароль задаётся ENV `RESTOCORE_ADMIN_PASSWORD`, по умолчанию `admin123`)

| Логин | Роль | Логин | Роль |
|---|---|---|---|
| `owner` | Владелец | `cook` | Повар |
| `manager` | Управляющий | `storekeeper` | Кладовщик |
| `shiftadmin` | Администратор смены | `accountant` | Бухгалтер |
| `cashier` | Кассир | `techadmin` | Технический администратор |
| `waiter` | Официант | `bartender` | Бармен |

Вход: `POST /api/v1/auth/login {"login":"cashier","password":"..."}` → Bearer JWT.
Защита учётных записей: блокировка после `MAX_FAILED_LOGINS` (5) неудачных попыток на
`LOCKOUT_MINUTES` (15); все входы и неудачи пишутся в журнал аудита.

## Запуск через Docker Compose (PostgreSQL)

```bash
cp .env.example .env
# в .env задайте: JWT_SECRET (обязательно), POSTGRES_PASSWORD, RESTOCORE_ADMIN_PASSWORD
docker compose up -d --build
docker compose ps                # оба сервиса должны быть healthy
curl http://localhost:8000/api/v1/health
```

Что настроено:
- **db**: PostgreSQL 16, данные в volume `pgdata`, порт проброшен только на `127.0.0.1`
  (БД не публикуется в интернет), healthcheck `pg_isready`;
- **api**: стартует только после готовности БД (`depends_on: service_healthy`),
  миграции применяются при startup;
- volumes `backups` и `uploads` — резервные копии и загруженные документы переживают пересоздание контейнеров;
- healthcheck API каждые 15 с через `/api/v1/health`.

Остановка: `docker compose down` (данные сохраняются). Полный сброс с удалением данных: `docker compose down -v`.

## Тесты

```bash
pytest -q                                  # вся сюита
pytest -q tests/test_warehouse.py          # отдельный модуль
```

Покрытые критические сценарии (раздел 24 ТЗ):

| Файл | Сценарии |
|---|---|
| `tests/test_orders_payments.py` | создание заказа и позиций; **повторная/двойная оплата одного заказа** (идемпотентность + параллельные запросы — второй отказывает); частичная и смешанная оплата; изменение заказа устаревшей версией (оптимистичная блокировка); возврат; скидка сверх лимита требует подтверждения руководителя |
| `tests/test_shifts.py` | открытие смены с начальным остатком; внесение/изъятие/передача денег; **закрытие смены, расчёт ожидаемого остатка наличных и расхождения** (недостача/излишек); закрытая смена защищена от правок, reopen — только с правом и записью в аудит |
| `tests/test_warehouse.py` | проведение накладной и приход партий; **списание ингредиентов по техкарте при продаже**; **отказ списания при недостатке остатков** («в минус» — только с правом и причиной); инвентаризация; неизменяемый журнал движений |
| RBAC | тесты выполняют запросы под разными ролями: отсутствие разрешения → HTTP 403 на сервере |
| Резервные копии | создание бэкапа, проверка контрольных счётчиков, **восстановление из копии** |

Фикстуры (`tests/conftest.py`) поднимают чистую базу и тестируют через TestClient реальные
HTTP-роутеры — т.е. вместе с бизнес-логикой проверяется и серверная авторизация.

> Примечание про окружение разработки: если в системе установлен плагин `libtmux`
> (конфликтует с фикстурами pytest), запускайте `pytest -p no:libtmux -q`.
> В docker-образе лишних плагинов нет.

## Миграции базы данных

```bash
alembic upgrade head          # применить все миграции (идемпотентно на уже обновлённой БД)
alembic downgrade -1          # откат одной миграции
alembic revision --autogenerate -m "описание"   # новая миграция после изменения models.py
```

Проверено: развёртывание «с нуля» создаёт все таблицы; повторный `upgrade head` безопасен.
SQLite — для разработки/тестов; PostgreSQL — целевая продакшен-СУБД (compose выше).

## Восстановление из резервной копии

Копии создаются автоматически по расписанию (`BACKUP_INTERVAL_HOURS`) и вручную.
Копия помечается надёжной **только после проверки восстановления** (сверка контрольных
счётчиков таблиц); при ошибке формируется уведомление ответственным ролям.

**Вариант 1 — через API (права `backups:run` / `backups:restore`):**
```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"login":"techadmin","password":"..."}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s localhost:8000/api/v1/admin/backups -H "Authorization: Bearer $TOKEN"               # список копий и статус проверок
curl -s -X POST localhost:8000/api/v1/admin/backups/run -H "Authorization: Bearer $TOKEN"   # ручная копия
curl -s -X POST localhost:8000/api/v1/admin/backups/<backup_id>/restore -H "Authorization: Bearer $TOKEN"
```
После восстановления перезапустите сервис; операция фиксируется в журнале аудита.

**Вариант 2 — напрямую в PostgreSQL (аварийная процедура, остановив api):**
```bash
docker compose stop api
gunzip -c backups/restocore_<stamp>.sql.gz | docker compose exec -T db \
  psql -U restocore -d restocore
docker compose start api
```
После аварийного восстановления сверьте остатки кассовых смен и склада с последним Z-отчётом.

## Конфигурация (ENV)

Все настройки — в `.env` (комментарии в `.env.example`). Секреты (`JWT_SECRET`,
`TELEGRAM_BOT_TOKEN`, пароли) никогда не хранятся в коде и БД в открытом виде. Данные
банковских карт не сохраняются — эквайринг подключается через адаптер (`TERMINAL_ADAPTER`),
в БД остаётся только mask PAN.

Интеграции по умолчанию работают в режиме `stub`: они **честно** возвращают pending/failed,
если оборудование не подтвердило операцию (фискализация, эквайринг), пишут журнал интеграций
и позволяют повторить безопасные операции (`POST /api/v1/admin/integrations/retry`). Реальные
адаптеры онлайн-касс РК (например, Штрих-KZ) подключаются реализацией интерфейса `app/integrations/fiscal.py`.

## Статус проекта

Реализовано и покрывается тестами: backend полностью — 20 модулей API, бизнес-логика,
RBAC, аудит, склад, финансы, зарплата, лояльность, бэкапы, экспорт XLSX/CSV/PDF.
Frontend (React + TypeScript: касса, карта зала, KDS, панель управляющего) — следующий этап;
после сборки помещается в `app/static/` и раздаётся тем же сервером (SPA-fallback настроен).

## Принципы разработки

- Каждая критическая операция: транзакция БД + проверка `require_perm` + запись в append-only
  журнал аудита + заголовок `Idempotency-Key` там, где возможны повторы (платежи, проводки, restore).
- Незаметное редактирование проведённых документов запрещено: исправления оформляются
  сторнированием или новой записью с указанием причины.
- Конкурентные изменения заказа решаются оптимистичной блокировкой по версии;
  дубли платежей отсекаются уникальным idempotency-ключом и `SELECT ... FOR UPDATE`.
