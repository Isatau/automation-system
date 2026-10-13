# RestoCore

Система автоматизации ресторана, кафе, бара и караоке-клуба: касса, заказы, платежи и
кассовые смены, зал и бронирования, кухня/бар (KDS), склад и закупки, финансы, сотрудники
и зарплата, гости и лояльность, аудит, уведомления, документы, отчёты с экспортом,
резервное копирование и слой интеграций. Единый frontend на React + TypeScript.

**Конвенции:** интерфейс и данные — на русском; валюта ₸ (KZT); время — Asia/Almaty.
Деньги хранятся в точных десятичных типах (`NUMERIC(14,2)`), не float. Доступ проверяется
**на сервере** (`require_perm` в роутерах FastAPI) — скрытие кнопок во frontend не считается
защитой. Критические операции выполняются в транзакциях БД, защищены идемпотентностью
(`Idempotency-Key`) и оптимистичной блокировкой заказов по версии.

---

## Структура проекта

```
/workspace
├── app/                     # Backend: FastAPI + SQLAlchemy 2.0
│   ├── main.py              # приложение: ~20 роутеров /api/v1, lifespan (миграции, seed,
│   │                        # фоновые задачи), раздача собранного фронтенда из static/ (SPA-fallback)
│   ├── config.py            # настройки только из ENV (.env)
│   ├── db.py                # engine/session, серверное время Asia/Almaty
│   ├── models.py            # нормализованная схема ~50 таблиц (docs/DATABASE.md)
│   ├── security.py          # bcrypt, JWT + jti-реестр сессий, блокировка при переборе паролей
│   ├── rbac.py              # 10 ролей × матрица разрешений, лимиты скидок, подтверждения руководителем
│   ├── deps.py              # current_user / require_perm (серверная авторизация)
│   ├── seed.py              # демо-данные (отключается SEED_DEMO=false)
│   ├── routers/             # auth, orders, payments, shifts, menu, hall, kitchen, warehouse,
│   │                        # suppliers, purchase_orders, finance, staff, payroll, guests,
│   │                        # auditlog, notifications, documents, reports, dashboard, admin
│   ├── services/            # order_service, payment_service, shift_service, stock_service
│   │                        # (FIFO-партии, списание по техкартам), loyalty_service, pricing,
│   │                        # audit, idempotency, notify, backup, exporters (XLSX/CSV/PDF)
│   ├── integrations/        # адаптеры: fiscal (stub Штрих-KZ), bank terminal, printer, telegram
│   └── static/              # собранный фронтенд (появляется после `npm run build` + копирования)
├── frontend/                # React 18 + TypeScript + Vite (тёмная дизайн-система)
│   ├── src/api/             # client.ts (общий HTTP-клиент), endpoints.ts (только подтверждённые маршруты)
│   ├── src/auth/            # AuthContext (сессия, permissions, выход)
│   ├── src/components/      # ui.tsx, DataTable.tsx — общие компоненты
│   ├── src/layout/          # Layout.tsx — боковая панель + верхняя панель + мобильное меню
│   ├── src/pages/           # LoginPage, DashboardPage (+ заготовки POS/KDS/отчётов по мере подключения)
│   ├── src/styles/tokens.css# дизайн-токены (палитра, отступы, типографика)
│   └── vite.config.ts       # dev-прокси /api → http://127.0.0.1:8000
├── migrations/              # Alembic (первая миграция: 1a3caaebf52d_initial_schema)
├── tests/                   # pytest: критические сценарии (см. раздел «Тесты»)
├── docs/                    # ARCHITECTURE.md, DATABASE.md, API.md, RBAC.md, PROCESSES.md
├── docker-compose.yml       # api + postgres:16, volumes, healthcheck
├── Dockerfile
├── .env.example
└── requirements.txt
```

Документация: [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [DATABASE.md](docs/DATABASE.md) ·
[API.md](docs/API.md) · [RBAC.md](docs/RBAC.md) · [PROCESSES.md](docs/PROCESSES.md).
Интерактивная OpenAPI-документация на работающем сервере: `GET /docs` (Swagger) и `/redoc`.

---

## Запуск для разработки (backend + frontend отдельно)

### 1. Backend (FastAPI, SQLite)

```bash
cd /workspace
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env         # как минимум замените JWT_SECRET!
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

При первом запуске приложение автоматически применяет миграции Alembic
(`alembic upgrade head`, повторный запуск безопасен) и при `SEED_DEMO=true`
заполняет демо-данные (заведение, залы, столы, меню с техкартами, склады, пользователи).

Проверка: `curl http://127.0.0.1:8000/api/v1/health` и `http://127.0.0.1:8000/docs`.

### 2. Frontend (Vite dev-server)

```bash
cd frontend
npm install
npm run dev                  # http://localhost:5173
```

В режиме разработки Vite проксирует `/api/*` на `http://127.0.0.1:8000`
(настроен в `frontend/vite.config.ts`), поэтому CORS не требуется. При необходимости
указать другой адрес backend задайте переменную окружения:

```bash
# frontend/.env.local
VITE_API_BASE_URL=http://127.0.0.1:8000
```

По умолчанию `VITE_API_BASE_URL` пустой — запросы идут на same-origin (через прокси или
на тот же сервер, что раздаёт статику). Секретов во frontend нет и быть не должно.

### 3. Продакшен-сборка фронтенда внутри backend

```bash
cd frontend && npm ci && npm run build           # результат в frontend/dist
cp -r dist ../app/static                          # FastAPI раздаёт SPA из app/static (SPA-fallback)
```

После этого весь интерфейс доступен с того же адреса, что и API: `http://<server>:8000/`.

---

## Демо-пользователи

Пароль для всех учётных записей задаётся ENV `RESTOCORE_ADMIN_PASSWORD` (по умолчанию `admin123`).
Логин — роль. **Не используйте демо-учётные данные в эксплуатации**, отключите сид
`SEED_DEMO=false` и создайте реальных сотрудников через API.

| Логин | Роль | Логин | Роль |
|---|---|---|---|
| `owner` | Владелец | `cook` | Повар |
| `manager` | Управляющий | `storekeeper` | Кладовщик |
| `shiftadmin` | Администратор смены | `accountant` | Бухгалтер |
| `cashier` | Кассир | `techadmin` | Технический администратор |
| `waiter` | Официант | `bartender` | Бармен |

Вход: `POST /api/v1/auth/login {"login":"cashier","password":"..."}` → ответ
`{"token": "<JWT>", "user": {id, name, role, login}}`; далее Bearer-заголовок
`Authorization: Bearer <token>`. Разрешения текущей сессии — `GET /api/v1/auth/me`
(`permissions`, `discount_limit_pct`, `venue_id`). Защита учётных записей: блокировка
после `MAX_FAILED_LOGINS` (5) неудачных попыток на `LOCKOUT_MINUTES` (15); все входы и
неудачи пишутся в журнал аудита. Выход завершает сессию на сервере (jti-реестр).

Frontend показывает пункты меню согласно реальному списку `permissions` из API, но это
только удобство — сервер проверяет права на каждом запросе (403 без обхода интерфейса).

---

## Запуск через Docker Compose (PostgreSQL)

```bash
cp .env.example .env
# в .env задайте: JWT_SECRET (обязательно), POSTGRES_PASSWORD, RESTOCORE_ADMIN_PASSWORD
docker compose up -d --build
docker compose ps                # оба сервиса должны быть healthy
curl http://localhost:8000/api/v1/health
```

Что настроено:

- **db**: PostgreSQL 16, данные в volume `pgdata`; порт проброшен только на `127.0.0.1` —
  база не публикуется в сеть; healthcheck `pg_isready`;
- **api**: стартует после готовности БД (`depends_on: service_healthy`), миграции применяются
  при startup; healthcheck каждые 15 с через `/api/v1/health`; таймзона контейнера — Asia/Almaty;
- volumes `backups` и `uploads` — резервные копии и загруженные документы переживают
  пересоздание контейнеров.

Остановка: `docker compose down` (данные сохраняются). Полный сброс с удалением данных:
`docker compose down -v`.

---

## Тесты

```bash
pytest -q                                  # вся сюита
pytest -q tests/test_warehouse.py          # отдельный модуль
```

Если в системе установлен конфликтующий плагин `libtmux`, запускайте
`pytest -p no:libtmux -q` (в docker-образе лишних плагинов нет).

Фикстуры `tests/conftest.py` поднимают чистую БД и тестируют через `TestClient` реальные
HTTP-роутеры — вместе с бизнес-логикой проверяется серверная авторизация.

| Файл | Критические сценарии |
|---|---|
| `tests/test_orders_payments.py` | создание заказа и позиций; **повторная/двойная оплата** одного заказа (идемпотентность + параллельные запросы — второй отказывает); частичная и смешанная оплата; изменение заказа устаревшей версией (оптимистичная блокировка); возврат; скидка сверх лимита требует подтверждения руководителя |
| `tests/test_shifts.py` | открытие смены с начальным остатком; внесение/изъятие/передача денег; **закрытие смены, расчёт ожидаемого остатка наличных и расхождения** (недостача/излишек); закрытая смена защищена от правок, reopen — только с правом и записью в аудит |
| `tests/test_warehouse.py` | проведение накладной и приход партий; **списание ингредиентов по техкарте при продаже**; **отказ списания при недостатке остатков** («в минус» — только с правом и причиной); инвентаризация; неизменяемый журнал движений |
| RBAC | запросы под разными ролями: отсутствие разрешения → HTTP 403 на сервере |
| Резервные копии | создание бэкапа, проверка контрольных счётчиков, восстановление из копии |

---

## Миграции базы данных

```bash
alembic upgrade head                            # применить все миграции (идемпотентно)
alembic downgrade -1                            # откат одной миграции
alembic revision --autogenerate -m "описание"   # новая миграция после изменения models.py
```

Проверено: развёртывание «с нуля» создаёт все таблицы; повторный `upgrade head` безопасен.
SQLite — для разработки/тестов; PostgreSQL — целевая продакшен-СУБД (compose выше).
Изменение схемы SQLite/PostgreSQL без отдельного согласования запрещено.

---

## Резервное копирование и восстановление

Копии создаются автоматически по расписанию (`BACKUP_INTERVAL_HOURS`) и вручную.
Копия помечается надёжной **только после проверки восстановления** (сверка контрольных
счётчиков таблиц); при ошибке формируется уведомление ответственным ролям.

**Вариант 1 — через API** (права `backups:run` / `backups:restore`):

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"login":"techadmin","password":"..."}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -s localhost:8000/api/v1/admin/backups -H "Authorization: Bearer $TOKEN"               # список копий и статус проверок
curl -s -X POST localhost:8000/api/v1/admin/backups/run -H "Authorization: Bearer $TOKEN"   # ручная копия
curl -s -X POST localhost:8000/api/v1/admin/backups/<backup_id>/restore -H "Authorization: Bearer $TOKEN"
```

После восстановления перезапустите сервис; операция фиксируется в журнале аудита.

**Вариант 2 — напрямую в PostgreSQL** (аварийная процедура, остановив api):

```bash
docker compose stop api
gunzip -c backups/restocore_<stamp>.sql.gz | docker compose exec -T db \
  psql -U restocore -d restocore
docker compose start api
```

После аварийного восстановления сверьте остатки кассовых смен и склада с последним Z-отчётом.

---

## Конфигурация (ENV)

Все настройки — в `.env` (полный список с комментариями в `.env.example`). Секреты
(`JWT_SECRET`, `TELEGRAM_BOT_TOKEN`, пароли) никогда не хранятся в коде, в исходниках
frontend или в БД в открытом виде. Данные банковских карт не сохраняются — эквайринг
подключается через адаптер (`TERMINAL_ADAPTER`), в БД остаётся только mask PAN.

Интеграции по умолчанию работают в режиме `stub`: они **честно** возвращают pending/failed,
если оборудование не подтвердило операцию (фискализация, эквайринг), пишут журнал интеграций
и позволяют повторить безопасные операции (`POST /api/v1/admin/integrations/retry`). Реальные
адаптеры онлайн-касс РК (например, Штрих-KZ) подключаются реализацией интерфейса
`app/integrations/fiscal.py`.

---

## Статус проекта

- **Backend:** реализован полностью — ~20 модулей API, бизнес-логика, RBAC, аудит, склад,
  финансы, зарплата, лояльность, бэкапы, экспорт XLSX/CSV/PDF; покрыт тестами (см. выше).
- **Frontend:** каркас готов — авторизация через `POST /api/v1/auth/login` + `GET /api/v1/auth/me`,
  общий HTTP-клиент с обработкой 401/403/сети, тёмная дизайн-система (`styles/tokens.css`),
  Layout с роль-ориентированной навигацией, страница входа и главная панель на реальных
  данных `GET /api/v1/dashboard`. Разделы POS/касса, карта зала, KDS и отчёты подключаются
  поэтапно строго по подтверждённым маршрутам из `frontend/src/api/endpoints.ts`; функции
  без подтверждённого API помечаются как «ожидает интеграции» и не имитируются.

**Важно про доступ к главной панели:** `GET /api/v1/dashboard` требует серверного права
`reports:view` — оно есть у владельца, управляющего, администратора смены, бухгалтера и
техадминистратора. Кассир, официант, бармен, повар и кладовщик получают `403` (это корректное
поведение RBAC, а не ошибка frontend): интерфейс обязан показывать сообщение об отсутствии
доступа, а не пустые нули. Для этих ролей стартовой страницей служит их рабочий модуль
(POS/KDS/склад) по мере его подключения.

## Принципы разработки

- Каждая критическая операция: транзакция БД + проверка `require_perm` + запись в append-only
  журнал аудита + `Idempotency-Key` там, где возможны повторы (платежи, проводки, restore).
- Незаметное редактирование проведённых документов запрещено: исправления оформляются
  сторнированием или новой записью с указанием причины.
- Конкурентные изменения заказа решаются оптимистичной блокировкой по версии;
  дубли платежей отсекаются уникальным idempotency-ключом и `SELECT ... FOR UPDATE`.
- Frontend не выдумывает эндпоинты и поля: только схемы из OpenAPI/backend-кода;
  состояния загрузки, пустого результата и ошибки обрабатываются на каждой странице.
